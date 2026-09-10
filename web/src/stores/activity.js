/**
 * stores/activity.js — текущая активность беседы (docs/done/PLAN-ACTIVITIES.md).
 *
 * Состояние живёт в Pinia, а не в компоненте, по одной причине: свёрнутая активность
 * превращается в плавающее окно, которое следует за человеком по вкладкам. Держи его в
 * компоненте страницы — и переход в «Расписание» убил бы идущую викторину.
 *
 * Кадры состояния приходят по WebSocket мессенджера (отдельного канала нет), приём от
 * участника идёт по HTTP.
 */
import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { activitiesApi } from '@/api/endpoints'
import { acceptsFrame, mergeFrame } from '@/utils/frameOrder'


// 🔑 КАК ОТКРЫВАЕТСЯ АКТИВНОСТЬ ПОСЛЕ ЗАПУСКА — по категориям, а не одинаково.
// Раньше `start()` всегда ставил 'full', и это давало сразу три жалобы:
//   • опрос открывался отдельной страницей, хотя он ЕСТЬ сообщение в чате с кнопками;
//   • таймер закрывал собой весь экран, и полоски в шапке никто не видел;
//   • полноэкранное окно перекрывало переписку там, где смотреть было не на что.
// Правило простое: на весь экран открывается только то, ВНУТРИ чего работают.
const MODE_ON_START = {
  poll: 'hidden',     //голосуют в ленте
  timer: 'hidden',    //живёт полоской под названием беседы
  pulse: 'full',      //плашка обязана всплыть у всех, иначе её не заметят
  board: 'full',
  quiz: 'full',
  contest: 'full',
}

export const useActivityStore = defineStore('activity', () => {
  const activity = ref(null)        // {id, kind, host_id, is_host, status, params, ...}
  const state = ref({})             // payload последнего принятого кадра
  const unseen = ref(false)         // в беседе началась активность, её ещё не открывали
  const journalFor = ref('')        // открыт журнал активностей этой беседы
  //ВСЕ идущие активности беседы. Их теперь может быть несколько разных категорий
  //(таймер рядом с доской — обычная работа), а `activity` — та, что открыта на экране.
  const running = ref([])
  //Истёкший тайм-бокс, по которому ещё не нажали «ОК». Живёт в сторе, а не в компоненте
  //мессенджера: окно с сигналом обязано всплыть в ЛЮБОЙ вкладке — таймер ставят и идут
  //работать в журнал или расписание.
  const expiredTimer = ref(null)
  const seq = ref(-1)               // номер последнего принятого кадра
  const mode = ref('hidden')        // hidden | full | mini
  const launcherFor = ref('')       // id беседы, для которой открыт выбор категории
  const loading = ref(false)
  const error = ref('')

  const isRunning = computed(() => activity.value?.status === 'running')
  const isHost = computed(() => !!activity.value?.is_host)
  const kind = computed(() => activity.value?.kind || '')

  /**
   * Принять кадр состояния.
   *
   * Само правило — в `utils/frameOrder.js`: чистой функцией без импортов, чтобы его
   * можно было проверить тестом. Стор тянет алиас `@/api/endpoints`, который `node
   * --test` не разрешает, и проверка правила «по месту» была бы копией — то есть
   * зелёной при сломанном сторе.
   */
  function applyFrame(frame) {
    if (!frame || !activity.value) return false
    if (frame.activity_id && frame.activity_id !== activity.value.id) return false
    if (!acceptsFrame(seq.value, frame.seq)) return false
    seq.value = Number(frame.seq)
    const add = frame.payload?.strokes_added
    const cleared = frame.payload?.cleared
    state.value = mergeFrame(state.value, frame.payload)
    //🔥 Доска: кадр приносит ТОЛЬКО добавленное, а полную доску держал у себя открытый
    //холст. Кто открывал доску позже, получал `state.strokes` с момента последнего
    //load() — то есть чужие штрихи ему не приезжали вовсе («не видно доску у других»).
    //Копим в сторе: тогда и поздно открывший, и свёрнутое окно видят одно и то же.
    if (kind.value === 'board') {
      const base = cleared ? [] : (state.value.strokes || [])
      state.value = { ...state.value, strokes: Array.isArray(add) ? [...base, ...add] : base }
    }
    return true
  }

  /** Событие «в беседе запустили активность» — для тех, кто её ещё не видит. */
  async function onStarted(ev, conversationId) {
    if (!ev || ev.conversation_id !== conversationId) return
    // Отметка «появилась новая» — для значка у кнопки активностей в шапке чата. Гасим её
    // не по факту получения события, а по факту ОТКРЫТИЯ: иначе значок исчезал бы сам, и
    // человек, отошедший от экрана на минуту, не узнал бы, что активность вообще была.
    unseen.value = true
    await refreshRunning(conversationId)
    await load(conversationId)
    //Срез понимания обязан ВСПЛЫТЬ: он живёт минуту, и свёрнутое окошко в углу человек
    //просто не заметит — ради этого его и запускают посреди пары.
    if (kind.value === 'pulse') { mode.value = 'full'; return }
    //Опрос и таймер экрана не занимают: первый голосуется в ленте, второй виден
    //полоской в шапке. Открывать их поверх переписки не за чем.
    if (kind.value === 'poll' || kind.value === 'timer') { mode.value = 'hidden'; return }
    if (mode.value === 'hidden') mode.value = 'mini'
  }

  /** Убрать окно истёкшего таймера (кнопка «ОК»). */
  function clearExpiredTimer() { expiredTimer.value = null }

  function onFinished(ev) {
    //Таймер, у которого ВЫШЛО время (а не тот, что завершили руками), поднимает окно с
    //сигналом. Признак ставит сервер: только он знает, дошёл отсчёт до нуля или человек
    //нажал «завершить» — у клиента часы свои, и доверять им тут нельзя.
    if (ev?.summary?.expired) {
      const was = (running.value || []).find((x) => x.id === ev.activity_id)
      expiredTimer.value = {
        id: ev.activity_id,
        duration_s: Number(ev.summary.duration_s || 0),
        title: was?.title || '',
      }
    }
    //Из списка идущих убираем всегда: полоска таймера обязана исчезнуть у всех сразу,
    //даже если человек в этот момент смотрел другую активность.
    running.value = running.value.filter((x) => x.id !== ev?.activity_id)
    if (!activity.value || ev?.activity_id !== activity.value.id) return
    activity.value = { ...activity.value, status: 'finished' }
    if (ev.summary) state.value = { ...state.value, summary: ev.summary }
    //🔥 Свёрнутое окно закрываем САМИ: ведущий завершил — значит смотреть больше не на
    //что, а окошко висело у всех до тех пор, пока каждый не закроет его руками.
    //Полный экран не трогаем: там человек читает свой результат, и выдёргивать его
    //с экрана итогов нельзя (кнопка «Завершить» там своя).
    if (mode.value === 'mini') hide()
  }

  async function load(conversationId) {
    if (!conversationId) return
    loading.value = true
    try {
      const { data } = await activitiesApi.current(conversationId)
      _adopt(data.activity)
    } catch { /* беседа без активности — не ошибка */ }
    finally { loading.value = false }
  }

  function _adopt(a) {
    if (!a) { reset(); return }
    activity.value = a
    state.value = a.state?.payload || {}
    seq.value = Number(a.state?.seq ?? -1)
  }

  async function start(conversationId, kindName, params = {}, title = '') {
    error.value = ''
    loading.value = true
    try {
      const { data } = await activitiesApi.start(conversationId, kindName, params, title)
      _adopt(data)
      mode.value = MODE_ON_START[data?.kind] || 'full'
      launcherFor.value = ''
      return true
    } catch (e) {
      error.value = e?.response?.data?.detail || 'Не удалось запустить активность'
      return false
    } finally { loading.value = false }
  }

  async function finish(save = false) {
    if (!activity.value) return
    try {
      const { data } = await activitiesApi.finish(activity.value.id, save)
      activity.value = { ...activity.value, status: 'finished' }
      state.value = { ...state.value, summary: data.summary || {} }
    } catch (e) {
      error.value = e?.response?.data?.detail || 'Не удалось завершить активность'
    }
  }

  /** Открыть уже идущую активность по нажатию карточки в ленте. */
  async function open(activityId) {
    loading.value = true
    try {
      const { data } = await activitiesApi.get(activityId)
      _adopt(data)
      mode.value = 'full'
    } catch (e) {
      error.value = e?.response?.data?.detail || 'Активность недоступна'
    } finally { loading.value = false }
  }

  function openLauncher(conversationId) { unseen.value = false; launcherFor.value = conversationId }
  /** Подхватить активность, которая уже идёт в этой беседе (вход в чат, перезагрузка).
   *  Режим выбираем тот же, что при запуске: таймер и опрос экрана не занимают. */
  /** Обновить список идущих активностей беседы (полоска таймера, значок «новая»). */
  async function refreshRunning(conversationId) {
    if (!conversationId) { running.value = []; return }
    try {
      const { data } = await activitiesApi.running(conversationId)
      running.value = data.activities || []
    } catch { running.value = [] }
  }

  async function adoptCurrent(conversationId) {
    await refreshRunning(conversationId)
    try {
      const { data } = await activitiesApi.current(conversationId)
      //Активности нет — гасим свою, но НЕ трогаем открытую на весь экран: человек мог
      //как раз в ней работать, а ответ прийти позже переключения беседы.
      if (!data?.activity) { if (mode.value !== 'full') reset(); return }
      _adopt(data.activity)
      if (mode.value === 'hidden' || !activity.value) {
        mode.value = MODE_ON_START[kind.value] === 'hidden' ? 'hidden' : 'mini'
      }
    } catch { /* активности нет — обычное дело */ }
  }

  function openJournal(conversationId) { journalFor.value = conversationId }
  function closeJournal() { journalFor.value = '' }
  function closeLauncher() { launcherFor.value = '' }
  function minimize() { mode.value = 'mini' }
  function expand() { mode.value = 'full' }
  function hide() { mode.value = 'hidden' }

  /**
   * ⚠️ Обязателен при выходе из аккаунта. Активность живёт ВНЕ страницы и переживает
   * смену пользователя в той же вкладке (SPA не перезагружается) — без сброса следующий
   * вошедший увидел бы чужую викторину поверх своего кабинета. Ровно та же граница, по
   * которой уже сбрасываются сторы мессенджера, Вектора и профиля.
   */
  function reset() {
    activity.value = null
    state.value = {}
    seq.value = -1
    mode.value = 'hidden'
    launcherFor.value = ''
    error.value = ''
  }

  return {
    activity, state, seq, mode, launcherFor, loading, error,
    isRunning, isHost, kind,
    applyFrame, onStarted, onFinished, load, start, finish, open,
    openLauncher, closeLauncher, minimize, expand, hide, reset, unseen,
    journalFor, openJournal, closeJournal, adoptCurrent, running, refreshRunning,
    expiredTimer, clearExpiredTimer,
  }
})
