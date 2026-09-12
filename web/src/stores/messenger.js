/**
 * messenger.js — состояние мессенджера (Фаза 2: личные чаты).
 *
 * Отдельная онлайн-подсистема (см. docs/done/MESSENGER-PLAN.md): истина на сервере, здесь —
 * кэш активной переписки + список чатов + каталог людей. Транспорт Фазы 2 — ОПРОС
 * (poll ?after=<id> раз в несколько секунд); WebSocket добавим отдельной фазой, интерфейс
 * стора менять не придётся. `mine` (своё ли сообщение) считает сервер — клиент своего id
 * не знает (в JWT/сторе только логин+роль).
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { messengerApi } from '@/api/endpoints'
import { useActivityStore } from '@/stores/activity'
import { getAccess } from '@/api/tokens'
import { getApiBase } from '@/api/server'
import { playMentionPing } from '@/utils/pingSound'
import { draftFor, saveDraft, clearDraft } from '@/utils/drafts'
import { pollInterval, reconnectDelay } from '@/utils/livePolling'
import { resolveIncoming } from '@/utils/messageMerge'

// Частота тика и паузы переподключения живут в utils/livePolling.js — там же
// объяснено, почему интервала два и почему опрос не выключен совсем. Здесь только
// вызовы: правило, лежащее внутри стора, нечем проверить тестом без браузера.

// Параметры WebSocket-канала: та же база, что у REST, но схема ws/wss. Токен передаём
// сабпротоколом (['bearer', <jwt>]) — так он НЕ попадает в URL и, значит, в access-логи
// прокси. Сервер эхом вернёт 'bearer'. Пусто, если нет токена.
function _wsConn() {
  const token = getAccess()
  if (!token) return null
  const base = (getApiBase() || window.location.origin).replace(/^http/, 'ws')
  return { url: `${base}/web/messenger/ws`, protocols: ['bearer', token] }
}

export const useMessengerStore = defineStore('messenger', () => {
  const chats = ref([])                 // список бесед (см. /chats)
  //Заявки: студент позвал преподавателя в свою группу. Пока не принята, участника НЕТ,
  //поэтому такая беседа НЕ приходит в /chats — её видно только здесь.
  const invites = ref([])
  const activeId = ref('')              // id активной беседы
  const activePeer = ref(null)          // карточка собеседника активной беседы
  const messages = ref([])             // сообщения активной беседы (хронология)
  const loadingChats = ref(false)
  const loadingMessages = ref(false)
  const loadingOlder = ref(false)       // идёт подгрузка истории вверх
  const hasOlder = ref(true)            // есть ли что грузить выше (false — дошли до начала)
  const sending = ref(false)
  const replyTo = ref(null)             // сообщение, на которое отвечаем (или null)
  // «Ответить с цитатой» (05.09.2026): ВЫДЕЛЕННЫЙ кусок исходного сообщения. Пусто —
  // обычный ответ, и в пузыре цитируется начало оригинала, как было.
  // ⚠️ Живёт РЯДОМ с `replyTo`, а не внутри него: `replyTo` — это само сообщение из ленты,
  // и дописывать в чужой объект своё поле значит однажды отправить его обратно на сервер.
  const replyQuote = ref('')
  const pinned = ref([])                // закреплённые сообщения активной беседы
  const selectionMode = ref(false)      // режим множественного выбора («Выделить»)
  const selectedIds = ref([])           // id выбранных сообщений
  const isModeration = ref(false)       // открыт чат с модерацией (кнопка ⚙)?
  const activeInfo = ref(null)          // инфо о группе/канале (участники, роли, моя роль)
  const channels = ref([])              // каталог публичных каналов

  const peerTyping = ref(false)         // собеседник печатает (по WS)
  const notice = ref('')                // сервисное уведомление (анти-флуд/мьют) — плашка в UI
  const totalUnread = computed(() => chats.value.reduce((s, c) => s + (c.unread || 0), 0))
  // Элемент активной беседы в списке чатов — источник её состояния (в т.ч. muted).
  const activeChat = computed(() => chats.value.find(c => c.conversation_id === activeId.value) || null)
  // Тип беседы (direct | group | channel | saved | moderation), известный СРАЗУ — не
  // дожидаясь ответа /chats/{id}. Раньше и лента, и боковая панель читали тип только из
  // activeInfo, а пока он не приехал, подставляли 'direct': «Избранное» на долю секунды
  // показывалось как переписка с человеком («был(а) недавно»), группа — как личный чат.
  // Тип берём по порядку надёжности: подробности беседы → строка списка чатов →
  // детерминированный префикс id («saved:<user>», см. _saved_conv_id на сервере).
  const activeKind = computed(() =>
    activeInfo.value?.kind || activeChat.value?.kind
    || (activeId.value.startsWith('saved:') ? 'saved' : '')
    || (activeId.value.startsWith('mod:') ? 'moderation' : '')
    || 'direct')

  let noticeTimer = null
  function setNotice(text) {
    notice.value = text || ''
    clearTimeout(noticeTimer)
    if (text) noticeTimer = setTimeout(() => { notice.value = '' }, 4000)
  }

  // Каталог/поиск людей.
  const dir = ref({ role: 'student', q: '', users: [], loading: false })

  let pollTimer = null
  let pollEvery = 0            // текущий интервал тика (см. utils/livePolling.js)
  let ws = null
  let wsRetry = 0              // сколько подряд не удалось подключиться
  let wsRetryTimer = null
  let typingTimer = null
  let lastTypingSent = 0

  /**
   * Граница дельты для опроса (`?after=<id>`).
   * 🔥 ТОЛЬКО ПОДТВЕРЖДЁННЫЕ СООБЩЕНИЯ. У оптимистичного черновика id ОТРИЦАТЕЛЬНЫЙ
   * (см. _optimisticMessage), и стоит ему оказаться последним в ленте, как опрос уходит
   * с `after=-1756…`, а сервер честно отвечает «всё, что новее» — то есть отдаёт первые
   * 50 сообщений беседы, и они дописываются В КОНЕЦ, под свежими. Дублей при этом нет,
   * поэтому проверка «дублей 1 шт» такое не ловит — ломается ПОРЯДОК. Гарантированно
   * воспроизводится там, где ответ на POST идёт долго: `/vector`, `/отчет`.
   */
  function _lastId() {
    for (let i = messages.value.length - 1; i >= 0; i--) {
      const m0 = messages.value[i]
      if (m0 && !m0.pending && m0.id > 0) return m0.id
    }
    return 0
  }

  // Громкие отметки (`/@!Фамилия`), по которым уже звонили. Держим id САМИХ СООБЩЕНИЙ, а
  // не бесед: иначе вторая отметка в том же чате прошла бы молча. Сбрасывается вместе со
  // стором при выходе (reset) — новому аккаунту чужие «уже звонили» не нужны.
  const _pinged = new Set()

  async function loadChats() {
    loadingChats.value = true
    try {
      const { data } = await messengerApi.chats()
      chats.value = data.chats || []
      _refreshActivePeer()
      _ringLoudMentions()
    } catch { /* сервер ещё не поднят / оффлайн — пустой список */ }
    finally { loadingChats.value = false }
    loadInvites()
  }

  // ── Заявки в беседу ────────────────────────────────────────────────────────────────
  // ⚠️ Едут ОТДЕЛЬНЫМ запросом, а не полем в /chats: непринятая заявка — это как раз НЕ
  // беседа человека, и класть её в общий список значило бы показывать её всем выборкам,
  // которые с этим списком работают (счётчик непрочитанного, поиск, последнее сообщение).
  // Сбой запроса гасим молча: заявки — дополнение к списку чатов, а не он сам.
  async function loadInvites() {
    try { invites.value = (await messengerApi.invites()).data.invites || [] }
    catch { invites.value = [] }
  }

  async function answerInvite(convId, accept) {
    try {
      if (accept) await messengerApi.acceptInvite(convId)
      else await messengerApi.declineInvite(convId)
    } catch { return false }
    //Список чатов перечитываем в любом случае: принял — беседа появилась, отклонил —
    //заявка ушла, и оба состояния должны быть видны сразу, а не после ручного обновления.
    await loadChats()
    if (accept) await selectChat(convId)
    return true
  }

  // Карточка собеседника бралась ОДИН раз при входе в чат и дальше не обновлялась: смена
  // статуса («не беспокоить», «отошёл») или уход в оффлайн доезжали только после
  // повторного открытия переписки — со стороны это и выглядело как «статус не работает».
  // Свежие данные и так приходят в списке чатов на каждом тике — берём их оттуда, без
  // отдельного запроса.
  function _refreshActivePeer() {
    if (!activeId.value || !activePeer.value) return
    const fresh = chats.value.find(c => c.conversation_id === activeId.value)?.peer
    if (fresh) activePeer.value = fresh
  }

  // Звук громкой отметки. Живёт в СТОРЕ, а не в компоненте чата: список чатов обновляется
  // на всех страницах (AppShell держит фоновый опрос), и «вас позвали» должно быть слышно
  // из журнала или расписания — иначе смысл громкого пинга теряется.
  // Замьюченные беседы сервер сюда не присылает как loud (см. _notify_loud_mentions).
  function _ringLoudMentions() {
    for (const c of chats.value) {
      if (!c.mention_loud || !c.mention_message_id) continue
      if (_pinged.has(c.mention_message_id)) continue
      //Отметку считаем показанной ВСЕГДА, даже когда звук приглушён: иначе, сняв
      //«не беспокоить», человек получил бы очередь накопившихся сигналов разом.
      _pinged.add(c.mention_message_id)
      //🔕 «Не беспокоить» глушит именно ЗВУК. Само письмо и значок остаются: статус
      //отключает то, что дёргает, а не факт события — тот же принцип, что у категорий
      //уведомлений на сервере (rustore_push.notify_login).
      if (myStatus.value.kind === 'dnd') continue
      playMentionPing()
    }
  }

  const PAGE = 50                       // столько отдаёт сервер за один запрос истории

  async function loadMessages(convId) {
    loadingMessages.value = true
    hasOlder.value = true
    try {
      const { data } = await messengerApi.messages(convId)
      messages.value = data.messages || []
      // Пришло меньше страницы — значит вся переписка уже здесь, тянуть выше нечего.
      if (messages.value.length < PAGE) hasOlder.value = false
    } catch { messages.value = []; hasOlder.value = false }
    finally { loadingMessages.value = false }
  }

  /**
   * 🔥 ИСТОРИЯ ВВЕРХ. Сервер умел это с самого начала (`GET …/messages?before=<id>`), и
   * даже докстринг в endpoints.js обещал «history (before=<id>)» — а вызова НЕ БЫЛО НИ
   * ОДНОГО: в вебе переписку глубже последних 50 сообщений нельзя было прочитать вовсе.
   * Ровно наш обычный класс дефекта — обещание без вызывающего, зелёное со всех сторон.
   * @returns {number} сколько сообщений добавлено сверху (нужно для сохранения прокрутки)
   */
  async function loadOlder() {
    if (loadingOlder.value || !hasOlder.value || !activeId.value) return 0
    // Опираемся на ПЕРВОЕ настоящее сообщение: у неподтверждённых черновиков id
    // отрицательные, и они бы увели границу в бессмыслицу.
    const первое = messages.value.find(x => x && x.id > 0)
    if (!первое) return 0
    loadingOlder.value = true
    const convId = activeId.value
    try {
      const { data } = await messengerApi.messages(convId, { before: первое.id })
      const старые = data.messages || []
      if (старые.length < PAGE) hasOlder.value = false
      // Пока ходили на сервер, человек мог уйти в другую беседу — тогда это чужая история.
      if (!старые.length || activeId.value !== convId) return 0
      const есть = new Set(messages.value.map(x => x.id))
      const свежие = старые.filter(x => !есть.has(x.id))
      messages.value = [...свежие, ...messages.value]
      return свежие.length
    } catch { return 0 }
    finally { loadingOlder.value = false }
  }

  async function _enterChat(convId, peer) {
    // 🔥 ЛЕНТА ГАСИТСЯ СРАЗУ, до похода на сервер. Шапка беседы меняется в этот же тик
    // (имя приходит из уже загруженного списка чатов), а сообщения ехали сетевым кругом —
    // и всё это время под НОВЫМ именем висели сообщения ПРЕДЫДУЩЕЙ беседы. Со стороны
    // это и читается как «ник появился раньше, а чат позже»: экран собирается по частям,
    // причём часть из них — чужая. Скелет ленты при этом не показывался вовсе: его
    // условие `!messages.length` было ложным, потому что старые сообщения ещё лежали.
    // ⚠️ Только при СМЕНЕ беседы: повторный вход в ту же (клик по уже открытому чату)
    // не должен мигать пустотой.
    if (activeId.value !== convId) {
      messages.value = []
      pinned.value = []
      activeThread.value = null
      searchResults.value = null
    }
    activeId.value = convId
    activePeer.value = peer
    isModeration.value = false
    peerTyping.value = false
    replyTo.value = null
    clearSelection()
    // 🔥 ОДНОЙ ВОЛНОЙ, А НЕ ЛЕСЕНКОЙ. Здесь стояли семь `await` подряд, и каждый — свой
    // круг до сервера: замер 24.08.2026 показал ровно 7 последовательных волн на одно
    // открытие чата. На локальном стенде это незаметно (ответ за 15 мс), а на боевом VPS
    // круг стоит 50–100 мс — то есть до полусекунды, в течение которой экран дорисовывался
    // КУСКАМИ: ник уже есть, ленты ещё нет, потом появляются закреплённые, потом панель
    // участников. Именно это читается как «дёшево и несобранно», и никакая анимация этого
    // не лечит — лечит одновременность. Запросы независимы, поэтому идут параллельно.
    const дела = [loadMessages(convId), loadPinned(), loadConvInfo()]
    //🔥 Подхватываем ИДУЩУЮ активность беседы. Без этого таймер, запущенный до ухода на
    //другую вкладку, исчезал вместе со стором: полоски нет, завершить нечем, а сервер
    //не даёт запустить второй — «одна активность на беседу». Выглядело как тупик.
    дела.push(useActivityStore().adoptCurrent(convId).catch(() => {}))
    await Promise.all(дела)
    // Модерационную беседу распознаём по ТИПУ, а не только по кнопке ⚙: иначе при открытии
    // из списка/после перезагрузки она рендерится как обычный личный чат с ролью-заглушкой
    // «Студент» (см. ProfilePanel). Тип приходит из convInfo (kind='moderation').
    if (activeInfo.value?.kind === 'moderation') isModeration.value = true
    // Хвост — В ФОНЕ. Отметка о прочтении и счётчик в списке чатов не влияют на то, что
    // человек уже видит в открытой беседе; держать ради них экран в состоянии «грузится»
    // значит платить двумя лишними кругами за чужую строчку в сайдбаре.
    // ⚠️ Порядок между ними сохраняем: loadChats, запущенный параллельно с markRead,
    // вернул бы ещё старый счётчик непрочитанного и нарисовал бы бейдж, которого нет.
    markReadActive().then(loadChats).catch(() => {})
  }

  // reset=true — вход в ДРУГУЮ беседу (старую карточку показывать нельзя, чистим сразу).
  // reset=false — обновление на тике опроса: карточку НЕ гасим.
  // Раньше гасили всегда, и на каждом опросе (раз в 3.5 с) панель на время сетевого
  // запроса оставалась без activeInfo — группа/канал «моргали» и подменялись карточкой
  // собеседника. Пустое значение показываем только когда правда не знаем, что за беседа.
  async function loadConvInfo(reset = true) {
    if (!activeId.value) { activeInfo.value = null; return }
    if (reset) activeInfo.value = null
    const convId = activeId.value
    try {
      const { data } = await messengerApi.convInfo(convId)
      //Ответ мог прийти уже после перехода в другой чат — тогда он не про эту беседу.
      if (activeId.value === convId) activeInfo.value = data
    } catch { /* личный чат может не отдавать расширенное инфо — не критично */ }
  }

  // ── Ссылка на сообщение: открыть беседу по id и перемотать к строке ─────────────
  // К какому сообщению перемотаться после открытия беседы. Ноль — не надо.
  // ⚠️ Живёт В СТОРЕ, а не в параметрах `_enterChat`: перемотку делает ChatThread, когда
  // лента уже отрисована, а `_enterChat` к этому моменту давно вернул управление.
  const pendingJump = ref(0)

  async function openById(convId, jumpToId = 0) {
    if (!convId) return false
    pendingJump.value = Number(jumpToId) || 0
    //Имя беседы возьмётся из convInfo; если чат уже в списке — берём его заголовок сразу,
    //чтобы шапка не мигнула пустотой.
    const known = chats.value.find(c => c.conversation_id === convId)
    await _enterChat(convId, known?.peer || { full_name: known?.title || '' })
    return true
  }

  // Открыть беседу из СПИСКА чатов (peer уже в элементе списка).
  async function selectChat(chat) {
    await _enterChat(chat.conversation_id, chat.peer || { full_name: chat.title || '' })
  }

  // Открыть/создать личный чат с пользователем (из каталога/поиска).
  async function openWith(user) {
    try {
      const { data } = await messengerApi.openDirect(user.id)
      await _enterChat(data.conversation_id, data.peer || user)
    } catch { /* нет доступа/сервера — тихо */ }
  }

  async function loadPinned() {
    if (!activeId.value) { pinned.value = []; return }
    try {
      const { data } = await messengerApi.pinned(activeId.value)
      pinned.value = data.pinned || []
    } catch { pinned.value = [] }
  }

  // Открыть чат с модерацией (кнопка ⚙). Слева покажем правила вместо карточки собеседника.
  async function openModeration() {
    try {
      const { data } = await messengerApi.moderation()
      await _enterChat(data.conversation_id, { full_name: 'Модерация', role: 'moderation' })
      isModeration.value = true
    } catch { /* сервер не готов — тихо */ }
  }

  // Возвращает true при успехе; false — если сервер отклонил (анти-флуд 429 / мьют 403 и пр.),
  // чтобы вызывающий вернул текст в поле ввода. Причину показываем плашкой (notice).
  // §D10: UUID для идемпотентности — ретрай с тем же nonce (обрыв сети/двойной клик по
  // «Отправить») не создаст на сервере дубль сообщения.
  function _nonce() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`
  }

  // §D2: «маскот-замедление» — сервер шлёт 429 с {mascot:true, cooldown_seconds}. Вместо
  // холодной плашки-ошибки показываем Вектора с фразой и на cooldown.seconds блокируем
  // композер (обратный отсчёт — remaining).
  const mascotCooldown = ref({ active: false, seconds: 0, remaining: 0 })
  let cooldownTimer = null
  function _startCooldown(seconds) {
    clearInterval(cooldownTimer)
    mascotCooldown.value = { active: true, seconds, remaining: seconds }
    cooldownTimer = setInterval(() => {
      mascotCooldown.value.remaining -= 1
      if (mascotCooldown.value.remaining <= 0) {
        clearInterval(cooldownTimer)
        mascotCooldown.value = { active: false, seconds: 0, remaining: 0 }
      }
    }, 1000)
  }

  // Добавить сообщение в ленту, если его там ещё нет. Дедуп обязателен: сервер шлёт
  // WS-сигнал «changed» ДО того, как вернёт ответ на POST /send, и тик опроса успевает
  // притащить наше же сообщение раньше, чем resolve'нется этот await. Ярче всего это
  // видно на «/vector»: ответ ИИ считается ПОСЛЕ отправки, POST висит секунды, и
  // сообщение гарантированно приезжает опросом первым — в ленте появлялся дубль.
  // Правило слияния вынесено в utils/messageMerge.js — там же объяснено, почему оно
  // важнее, чем выглядит (два пути доставки своего же сообщения). Здесь только применение.
  function _appendUnique(msg) {
    const { action, index } = resolveIncoming(messages.value, msg)
    if (action === 'replace') messages.value[index] = msg
    else if (action === 'append') messages.value.push(msg)
  }

  // Общий хвост и для текста, и для GIF (см. sendGif ниже) — анти-флуд/мьют отвечают
  // ОДИНАКОВО независимо от вида сообщения, дублировать разбор ошибки незачем.
  function _handleSendError(e) {
    const st = e?.response?.status
    const detail = e?.response?.data?.detail
    if (st === 429 && detail && typeof detail === 'object' && detail.mascot) {
      _startCooldown(detail.cooldown_seconds || 8)
    } else if (st === 403 && detail && typeof detail === 'object' && detail.muted) {
      //🔥 ОГРАНИЧЕНИЕ МОДЕРАЦИИ ПРИХОДИТ СЛОВАРЁМ (срок, причина), а не строкой, и общая
      //ветка ниже показала бы вместо него «Сообщение не отправлено». Человек, которого
      //ограничили ПОСЛЕ открытия чата, не узнал бы ни срока, ни причины — то есть ровно
      //то состояние, ради выхода из которого срок и заводился.
      restriction.value = {
        muted: true,
        muted_until: detail.muted_until || '',
        reason: detail.reason || '',
      }
      setNotice(detail.message || 'Переписка ограничена модерацией.')
    } else if ([400, 403, 429].includes(st)) {
      //400 сюда попал не для полноты: так отвечает разбор команд («/отчет чужая-группа»),
      //и без плашки человек видел ровно ничего — сообщение исчезало без объяснений.
      setNotice((typeof detail === 'string' && detail) || 'Сообщение не отправлено.')
    }
  }

  /**
   * Черновик собственного сообщения — то, что человек видит МГНОВЕННО по нажатию.
   * Отрицательный id гарантированно не столкнётся с серверным (те растут от 1), а
   * `pending` отличает его в ленте: он приглушён, пока сервер не подтвердил.
   */
  function _optimisticMessage(body, nonce, reply) {
    return {
      id: -Date.now(), client_nonce: nonce, pending: true,
      conversation_id: activeId.value, sender_id: '', sender_name: '', mine: true,
      kind: 'text', body, body_format: 'markdown',
      created_at: new Date().toISOString(), edited_at: '', deleted: false,
      reply_to_id: reply?.id || null, reply_quote: replyQuote.value || '',
      pinned: false, forwarded_from: null,
      mentions: [], reactions: [], reply_count: 0, report: null,
    }
  }

  async function send(text) {
    const body = (text || '').trim()
    if (!body || !activeId.value || sending.value) return false
    sending.value = true
    // 🔥 РИСУЕМ СРАЗУ, НЕ ДОЖИДАЯСЬ СЕРВЕРА. Раньше сообщение появлялось только после
    // ответа: на боевой сети это 100–200 мс пустоты после нажатия, и всё это время
    // композер заблокирован. Дело не в экономии миллисекунд, а в том, что действие
    // человека обязано иметь НЕМЕДЛЕННОЕ следствие — иначе интерфейс кажется вязким,
    // даже когда сервер отвечает быстро. Безопасно ровно потому, что отправка
    // идемпотентна по nonce (§D10): повтор с той же меткой не создаст второе сообщение.
    const nonce = _nonce()
    const черновик = _optimisticMessage(body, nonce, replyTo.value)
    messages.value.push(черновик)
    const убратьЧерновик = () => {
      const i = messages.value.findIndex(x => x.pending && x.client_nonce === nonce)
      if (i >= 0) messages.value.splice(i, 1)
    }
    try {
      const { data } = await messengerApi.send(
        activeId.value, body, replyTo.value?.id || 0, nonce,
        //Цитату проверяет СЕРВЕР (кусок обязан реально быть в оригинале) — здесь мы её
        //только передаём. Пустую не шлём вовсе: лишнее поле в теле каждого сообщения.
        replyQuote.value ? { reply_quote: replyQuote.value } : {})
      // ℹ️ Ветка `open_activity_launcher` убрана 17.08.2026 вместе с командой
      // `/активность` (решение Влада: активности открывает кнопка в шапке беседы, и она
      // зовёт лаунчер напрямую, не спрашивая сервер). Сервер такой ответ больше не шлёт —
      // держать разбор «на всякий случай» значило бы оставить ветку, которую никто не
      // выполняет и никто не проверяет.
      // Ответ может не нести метку (старый сервер) — тогда просто снимаем черновик,
      // иначе он остался бы висеть приглушённым рядом с настоящим сообщением.
      if (!data || !data.client_nonce) убратьЧерновик()
      _appendUnique(data)
      replyTo.value = null
      replyQuote.value = ''
      setNotice('')
      // Список чатов слева — в ФОН: он про соседнюю панель, а не про отправленное
      // сообщение, и держать ради него композер занятым незачем.
      loadChats().catch(() => {})
      return true
    } catch (e) {
      убратьЧерновик()          // не доехало — строка не должна оставаться в ленте
      _handleSendError(e)
      return false
    } finally { sending.value = false }
  }

  // GIF-пикер (Klipy) — отправляется СРАЗУ по клику на превью (как в Discord), а не
  // вставляется в поле ввода: тело сообщения — прямая ссылка, редактировать её незачем.
  async function sendGif(item) {
    if (!item?.url || !activeId.value || sending.value) return false
    sending.value = true
    try {
      const { data } = await messengerApi.send(
        activeId.value, item.url, replyTo.value?.id || 0, _nonce(),
        { kind: 'gif', gif_slug: item.slug || '' })
      _appendUnique(data)
      replyTo.value = null
      replyQuote.value = ''
      setNotice('')
      await loadChats()
      return true
    } catch (e) {
      _handleSendError(e)
      return false
    } finally { sending.value = false }
  }

  /**
   * Отправить файл. Три шага, и ни на одном файл не проходит через наш сервер.
   *
   *   1. просим подпись → сервер проверяет участие, размер, тип и отдаёт ссылку;
   *   2. кладём файл ПРЯМО в хранилище (PUT по подписанной ссылке);
   *   3. подтверждаем загрузку и отправляем обычное сообщение с `attachment_id`.
   *
   * ⚠️ Шаг 3 разделён на два запроса не от лени: без подтверждения в базе копились бы
   * записи о файлах, которых в хранилище нет, и человек видел бы в списке файлы,
   * которые не открываются.
   *
   * ⚠️ `fetch`, а не наш axios-клиент: к хранилищу нельзя приложить наш заголовок
   * авторизации — подпись уже в ссылке, а лишний заголовок ломает проверку подписи.
   *
   * @param {File} file
   * @param {string} caption — подпись к файлу (необязательна: файл сам по себе сообщение)
   * @param {(pct:number)=>void} onProgress
   */
  async function sendFile(file, caption = '', onProgress = null) {
    if (!file || !activeId.value || sending.value) return false
    sending.value = true
    try {
      const { data: sign } = await messengerApi.signUpload(activeId.value, {
        name: file.name, size: file.size, mime: file.type || 'application/octet-stream',
      })
      onProgress?.(5)

      // ⚠️ ДВА СПОСОБА, ОДИН КОД. Куда класть файл, решает сервер: в объектное
      // хранилище — сырым PUT (иначе подпись не сойдётся), к нам на диск — обычной
      // формой (чтобы обработчик остался синхронным и не встал поперёк цикла событий).
      // Клиент про это знать не должен: он делает то, что сказано в ответе на подпись.
      // Благодаря этому переезд на большую машину не требует правок в браузере.
      let body = file
      if (sign.form_field) {
        body = new FormData()
        body.append(sign.form_field, file, file.name)
      }
      const put = await fetch(sign.url, {
        method: sign.method || 'PUT',
        //У формы заголовок ставит браузер сам (там граница multipart) — свой сломал бы.
        headers: sign.form_field ? undefined : (sign.headers || {}),
        body,
      })
      if (!put.ok) throw new Error(`хранилище отказало: ${put.status}`)
      onProgress?.(85)

      await messengerApi.confirmUpload(sign.attachment_id)
      const { data } = await messengerApi.send(
        activeId.value, caption.trim(), replyTo.value?.id || 0, _nonce(),
        { attachment_id: sign.attachment_id })
      _appendUnique(data)
      replyTo.value = null
      replyQuote.value = ''
      setNotice('')
      onProgress?.(100)
      await loadChats()
      return true
    } catch (e) {
      //Отдельная ветка для «хранилище не настроено»: это не сбой сети, и человеку надо
      //сказать правду, а не «попробуйте ещё раз».
      if (e?.response?.status === 503) setNotice('Хранилище файлов пока не настроено')
      else if (e?.response?.status === 413) setNotice('Файл слишком большой')
      else if (e?.response?.status === 415) setNotice('Такой тип файла не поддерживается')
      else _handleSendError(e)
      return false
    } finally { sending.value = false }
  }

  // Замьютить/размьютить активную беседу у себя (без пушей по ней).
  async function muteConversation(muted) {
    if (!activeId.value) return
    try { await messengerApi.muteChat(activeId.value, muted); await loadChats() } catch { /* noop */ }
  }

  // Удалить переписку У СЕБЯ (clearOnly — только очистить историю, чат остаётся в списке).
  // У собеседника переписка сохраняется — это личное действие.
  async function deleteConversation(clearOnly = false) {
    if (!activeId.value) return false
    try {
      await messengerApi.deleteChat(activeId.value, clearOnly)
      if (clearOnly) { messages.value = []; pinned.value = [] }
      else clearActive()
      await loadChats()
      return true
    } catch { return false }
  }

  // ── Моё ограничение переписки (мьют модерацией) ──────────────────────────────────
  //
  // Живёт ЗДЕСЬ, а не в компоненте: читают его два места — плашка в композере и разбор
  // отказа при отправке. Две копии разошлись бы молча, и человек видел бы «ограничение
  // снято» при работающем запрете (или наоборот).
  //
  // ⚠️ Спрашиваем ОДИН РАЗ при открытии мессенджера, а не опросом: мьют не появляется
  // сам — его выдаёт человек, и раз в несколько секунд спрашивать про состояние, которое
  // меняется раз в месяц, значит вернуть тот опрос, который мы прореживали (§PERF).
  // Выданный при открытом чате мьют доедет отказом на первой же отправке: сервер
  // возвращает срок и причину тем же телом (см. `_mute_refusal`).
  const restriction = ref({ muted: false, muted_until: '', reason: '' })

  async function loadRestriction() {
    try {
      const { data } = await messengerApi.myRestriction()
      restriction.value = data || { muted: false }
    } catch { /* не критично: барьер всё равно на сервере */ }
  }

  // Кого просили добавить в новую группу (из трёх точек карточки профиля). Список чатов
  // следит за этим полем и открывает пикер участников с уже отмеченным человеком.
  // ⚠️ Поле, а не событие: карточка и список чатов — соседние ветки дерева, между ними
  // нет ни одного пропса, а протаскивать событие через четыре компонента значит завести
  // четыре места, где его однажды забудут передать.
  const groupDraftPeer = ref(null)
  function askAddToGroup(peer) { groupDraftPeer.value = peer || null }
  function clearGroupDraft() { groupDraftPeer.value = null }

  // ── Действия над ЛЮБОЙ беседой из списка (не над открытой) ───────────────────────
  //
  // 🔥 ПОЧЕМУ НЕ «ОТКРЫТЬ, ПОТОМ СДЕЛАТЬ». Соблазн переиспользовать `deleteConversation`,
  // открыв нужный чат, велик — и он ломает ровно то, ради чего человек лезет в меню:
  // открытие беседы ПОМЕЧАЕТ ЕЁ ПРОЧИТАННОЙ и тянет ленту с сервера. То есть «очистить
  // историю» у непрочитанного чата попутно снимало бы с него значок, а «пометить
  // непрочитанным» вообще не имело бы смысла. Побочный эффект тут дороже переиспользования.
  async function clearChatById(convId) {
    try {
      await messengerApi.deleteChat(convId, true)
      if (activeId.value === convId) { messages.value = []; pinned.value = [] }
      await loadChats()
      return true
    } catch { return false }
  }

  async function deleteChatById(convId) {
    try {
      await messengerApi.deleteChat(convId, false)
      if (activeId.value === convId) clearActive()
      await loadChats()
      return true
    } catch { return false }
  }

  async function muteChatById(convId, muted) {
    try { await messengerApi.muteChat(convId, muted); await loadChats(); return true }
    catch { return false }
  }

  async function markChatUnread(convId) {
    try { await messengerApi.markUnread(convId); await loadChats(); return true }
    catch { return false }
  }

  async function markReadActive() {
    if (!activeId.value) return
    try { await messengerApi.read(activeId.value, _lastId()) } catch { /* noop */ }
  }

  /**
   * Пометить карточку активности завершённой — и в открытой ленте, и в списке чатов.
   *
   * Нужна потому, что обычный путь обновления сюда не дотягивается: `pollOnce` спрашивает
   * только сообщения новее последнего id, а карточка активности к моменту завершения уже
   * старая. Перечитывать всю ленту ради одного поля дороже и заметно морганием.
   */
  function _patchActivityCard(activityId, finishedAt) {
    if (!activityId) return
    const patch = (m) => {
      if (m && m.kind === 'activity' && m.activity && m.activity.id === activityId) {
        m.activity = { ...m.activity, status: 'finished', finished_at: finishedAt }
      }
    }
    messages.value.forEach(patch)
    chats.value.forEach((c) => patch(c.last_message))
  }

  /** Обновить счётчик голосов у опроса прямо в ленте (см. про pollOnce выше). */
  function _patchPollCard(activityId, votedCount, tally) {
    if (!activityId) return
    const patch = (m) => {
      if (m && m.kind === 'poll' && m.activity && m.activity.id === activityId) {
        m.activity = { ...m.activity, voted_count: votedCount,
                       ...(tally ? { tally } : {}) }
      }
    }
    messages.value.forEach(patch)
    chats.value.forEach((c) => patch(c.last_message))
  }

  // Опрос новых сообщений активной беседы + обновление списка чатов.
  async function pollOnce() {
    if (activeId.value) {
      try {
        const { data } = await messengerApi.messages(activeId.value, { after: _lastId() })
        const fresh = data.messages || []
        if (fresh.length) {
          for (const msg of fresh) _appendUnique(msg)
          await markReadActive()
        }
      } catch { /* noop */ }
      // Галочки «прочитано» в ЛС читают last_read_at собеседника из activeInfo — держим
      // его свежим на каждый тик опроса/WS-сигнала, иначе галочка сменится только при
      // повторном входе в чат (см. ChatThread.vue::peerLastReadAt). Без сброса (reset=false):
      // иначе панель справа моргает на каждом тике.
      await loadConvInfo(false)
    }
    await loadChats()
  }

  // ── WebSocket: живые события (Фаза 7) — опрос остаётся страховкой ───────────────────
  function _connectWS() {
    const conn = _wsConn()
    if (!conn || ws) return
    try {
      ws = new WebSocket(conn.url, conn.protocols)
      ws.onmessage = (e) => {
        let ev
        try { ev = JSON.parse(e.data) } catch { return }
        if (ev.type === 'changed') {
          //Свёрнуто — только помечаем. Ленту и список всё равно никто не видит, а вот
          //запросы и перерисовку они вызывают настоящие.
          if (ev.conversation_id === activeId.value) {
            if (!_hidden()) pollOnce()
          } else if (_hidden()) missedChats = true
          else loadChats()
        } else if (ev.type && ev.type.startsWith('activity.')) {
          // Активности (docs/done/PLAN-ACTIVITIES.md §7) — своего канала у них нет, кадры
          // идут этим же сокетом. Разбор — в сторе активности: тут только маршрутизация.
          const act = useActivityStore()
          if (ev.type === 'activity.started') act.onStarted(ev, activeId.value)
          else if (ev.type === 'activity.state') {
            act.applyFrame(ev)
            // Опрос живёт СООБЩЕНИЕМ в ленте, а не окном: его счётчик обязан расти у
            // всех сразу. Обычный опрос ленты сюда не дотянется — карточка старая, а
            // `pollOnce` тянет только то, что новее последнего id.
            const vc = ev.payload?.voted_count
            if (vc !== undefined) _patchPollCard(ev.activity_id, vc, ev.payload?.tally)
          }
          else if (ev.type === 'activity.finished') {
            act.onFinished(ev)
            // 🔥 Карточку в ленте надо ПОЧИНИТЬ ЗДЕСЬ, а не ждать опроса. `pollOnce`
            // тянет только сообщения НОВЕЕ последнего id, а карточка активности —
            // сообщение старое: её статус менялся на сервере, но до клиента не доезжал
            // никогда, и таймер «идёт · 38:49» продолжал тикать у завершённой
            // активности до перезахода в чат. Тот же класс, что уже ловили с `/clear`.
            _patchActivityCard(ev.activity_id, ev.finished_at || new Date().toISOString())
          }
          //Кадры активности приходят часто; в свёрнутом приложении их некому показывать.
          if (ev.conversation_id === activeId.value && !_hidden()) {
            pollOnce()                                             // карточка в ленте
          }
        } else if (ev.type === 'typing' && ev.conversation_id === activeId.value) {
          peerTyping.value = true
          clearTimeout(typingTimer)
          typingTimer = setTimeout(() => { peerTyping.value = false }, 4000)
        }
      }
      // 🔥 БЕЗ ПЕРЕПОДКЛЮЧЕНИЯ СОКЕТ УМИРАЛ НАВСЕГДА. Раньше здесь стояло только
      // `ws = null`: один разрыв — уснул ноутбук, перескочил Wi-Fi, моргнул прокси — и
      // до перезахода в раздел живость держалась ИСКЛЮЧИТЕЛЬНО опросом. Это же объясняет,
      // почему опрос нельзя было проредить раньше: он молча стал основным транспортом.
      ws.onopen = () => {
        wsRetry = 0
        _applyPollInterval()
        // Пока сокета не было, события терялись — догоняем пропущенное сразу, а не
        // через тик. Именно здесь, а не в _connectWS: соединение может и не открыться.
        pollOnce()
      }
      ws.onclose = () => { ws = null; _applyPollInterval(); _scheduleReconnect() }
      ws.onerror = () => { try { ws.close() } catch { /* noop */ } }
    } catch { ws = null; _scheduleReconnect() }
  }

  // Переподключение с нарастающей паузой: 1, 2, 4 … до 30 секунд. Без потолка первый же
  // упавший сервер получил бы шквал попыток со всех вкладок колледжа сразу.
  function _scheduleReconnect() {
    if (!pollTimer) return                    // раздел закрыт — не воскрешаем
    //⚠️ Свёрнутое приложение связь не чинит. Показывать некому, а цепочка попыток на
    //телефоне — это пробуждения радиомодуля всю ночь. Вернулись к окну — `_onVisibleAgain`
    //подключается НЕМЕДЛЕННО и без ожидания паузы (там же сбрасывается счётчик попыток).
    if (_hidden()) return
    clearTimeout(wsRetryTimer)
    const delay = reconnectDelay(wsRetry++)
    wsRetryTimer = setTimeout(() => { if (pollTimer || !_hidden()) _connectWS() }, delay)
  }

  function _disconnectWS() {
    clearTimeout(wsRetryTimer)
    wsRetry = 0
    if (ws) {
      // Снимаем onclose ДО закрытия: иначе наш же обработчик назначит переподключение
      // к разделу, который человек только что закрыл.
      ws.onclose = null
      try { ws.close() } catch { /* noop */ }
      ws = null
    }
  }
  // Сообщить собеседнику, что печатаю (не чаще раза в 2 c).
  function sendTyping() {
    if (!ws || ws.readyState !== 1 || !activeId.value) return
    const now = Date.now()
    if (now - lastTypingSent < 2000) return
    lastTypingSent = now
    try { ws.send(JSON.stringify({ type: 'typing', conversation_id: activeId.value })) } catch { /* noop */ }
  }

  /** Жив ли сокет ПРЯМО СЕЙЧАС (readyState 1 = OPEN). */
  function _wsAlive() { return !!ws && ws.readyState === 1 }

  /**
   * 🔥 СКРЫТО ЛИ ПРИЛОЖЕНИЕ — ОДИН ВОПРОС НА ВЕСЬ ТРАНСПОРТ (07.09.2026).
   *
   * Дефект, ради которого заведено. Правило «в скрытой вкладке не ходим в сеть» жило
   * ТОЛЬКО в `_tick` — то есть в интервальном опросе. Существующий тест это проверял и
   * был зелёным, а мимо него шли две другие цепочки:
   *   • обработчик сообщений сокета звал `pollOnce()`/`loadChats()`/`loadConvInfo()` без
   *     единой проверки. Свёрнутое на телефоне приложение продолжало выполнять запросы,
   *     пока Android не приостановит процесс — а когда он это сделает, не знает никто;
   *   • переподключение сокета планировалось так же безусловно, и упавшая ночью связь
   *     давала цепочку попыток у свёрнутого приложения.
   * Зелёный тест рядом с дефектом — это «случай не покрыт», а не «исправно».
   *
   * ⚠️ Пропущенное не теряется: ставим флаг и догоняем ОДНИМ согласованным запросом при
   * возврате (`_onVisibleAgain`). Доставка уведомлений в фоне — дело нативных пушей, а
   * не нашего опроса; это разные механизмы, и подменять один другим значит платить
   * батареей за то, что уже сделано системой.
   */
  function _hidden() { return typeof document !== 'undefined' && document.hidden }

  //Что накопилось, пока приложение было свёрнуто. Разбираем при возвращении.
  //⚠️ Флаг ОДИН, и только для списка бесед. Отдельного «менялась активная беседа» здесь
  //нет намеренно: ленту при возвращении мы перечитываем ВСЕГДА (сокет мог пропустить
  //события и без нашей помощи), значит такой флаг ни на что не влиял бы — то есть был бы
  //мёртвой величиной, которую следующий читатель принял бы за работающую защиту.
  let missedChats = false

  /** Частота тика зависит от того, есть ли сокет; переустанавливаем при смене режима. */
  function _applyPollInterval() {
    if (!pollTimer) return                    // раздел закрыт — таймера быть не должно
    const want = pollInterval({ wsAlive: _wsAlive(), hidden: false })
    if (want === pollEvery) return
    pollEvery = want
    clearInterval(pollTimer)
    pollTimer = setInterval(_tick, pollEvery)
  }

  // ⚠️ В СКРЫТОЙ ВКЛАДКЕ НЕ ОПРАШИВАЕМ ВОВСЕ. Человек держит журнал открытым весь день
  // в соседней вкладке; сокет доставит всё сам, а при его смерти догоняем при возврате
  // (см. _onVisibility). Смысл не только в трафике: разбор трёх ответов и переприсваивание
  // массивов будят перерисовку в невидимой вкладке, отбирая такт у той, где человек работает.
  function _tick() {
    const hidden = typeof document !== 'undefined' && document.hidden
    if (pollInterval({ wsAlive: _wsAlive(), hidden }) === 0) return
    pollOnce()
  }

  //Не путать с _onVisibility ниже: тот про статус «отошёл» (idle watch), этот про
  //транспорт. Слушателей на одном событии два, и это осознанно — иначе транспорт и
  //присутствие оказались бы связаны одной функцией без всякой на то причины.
  function _onVisibleAgain() {
    if (typeof document === 'undefined' || document.hidden) return
    if (!pollTimer) return
    if (!_wsAlive()) { wsRetry = 0; _connectWS() }   // вернулись — чиним связь немедленно
    //Догоняем ОДНИМ согласованным заходом, а не по запросу на каждое пропущенное событие:
    //за ночь их набирается сотня, и повторять их по одному значило бы устроить при
    //разблокировке ровно тот шквал, ради избежания которого мы молчали.
    if (missedChats) { missedChats = false; loadChats() }
    pollOnce()                                       // и догоняем ленту
  }

  function startPolling() {
    stopPolling()
    pollEvery = pollInterval({ wsAlive: false, hidden: false })
    pollTimer = setInterval(_tick, pollEvery)
    _connectWS()
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', _onVisibleAgain)
  }
  function stopPolling() {
    //Накопленное относится к разделу, который закрывают: следующий вход соберёт свежее
    //сам, а «догнать» по флагу от прошлой сессии — это запрос за чужими данными.
    missedChats = false
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
    pollEvery = 0
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', _onVisibleAgain)
    _disconnectWS()
  }

  async function searchUsers(role, q) {
    dir.value.role = role
    dir.value.q = q
    dir.value.loading = true
    try {
      const { data } = await messengerApi.users(role, q)
      dir.value.users = data.users || []
    } catch { dir.value.users = [] }
    finally { dir.value.loading = false }
  }

  // quote — выделенный кусок (необязателен). Гасим его ЯВНО при обычном ответе: иначе
  // цитата от прошлого ответа уехала бы со следующим сообщением, к которому не относится.
  function setReply(msg, quote = '') { replyTo.value = msg; replyQuote.value = quote || '' }
  function clearReply() { replyTo.value = null; replyQuote.value = '' }
  function clearActive() {
    activeId.value = ''; activePeer.value = null; messages.value = []
    replyTo.value = null; replyQuote.value = ''; pinned.value = []; activeInfo.value = null
    isModeration.value = false; clearSelection(); closeThread(); clearSearch()
    setNotice('')
  }

  // Полный сброс стора — при выходе/смене аккаунта. Иначе список чатов, активная беседа и
  // её сообщения остаются от прошлого юзера (чужой канал «как будто создал ты», чужое
  // сообщение с mine=true «как будто написал ты»). Пара к clearCache() в auth.logout().
  function reset() {
    stopPolling()
    stopIdleWatch()          //иначе авто-«отошёл» продолжил бы жить от прошлого аккаунта
    clearActive()
    chats.value = []
    invites.value = []       //на общем телефоне выход — это смена владельца (§5.4)
    channels.value = []
    peerTyping.value = false
    _pinged.clear()          //чужие «уже звонили» новому аккаунту не наследуем
    dir.value = { role: 'student', q: '', users: [], loading: false }
  }

  // ── Группы и каналы (Фазы 5–6) ──────────────────────────────────────────────────────
  async function createGroup(title, memberIds = [], about = '', classGroups = []) {
    try {
      const { data } = await messengerApi.createGroup(title, memberIds, about, classGroups)
      await loadChats()
      await _enterChat(data.conversation_id, { full_name: data.title, role: 'group' })
      return true
    } catch { return false }
  }
  async function createChannel(title, writerIds = [], isPublic = true, about = '') {
    try {
      const { data } = await messengerApi.createChannel(title, writerIds, isPublic, about)
      await loadChats()
      await _enterChat(data.conversation_id, { full_name: data.title, role: 'channel' })
      return true
    } catch { return false }
  }
  async function loadChannels(q = '') {
    try { const { data } = await messengerApi.channels(q); channels.value = data.channels || [] }
    catch { channels.value = [] }
  }
  async function joinChannel(convId) {
    try {
      const { data } = await messengerApi.join(convId)
      await loadChats()
      await loadChannels(dir.value.q)
      await _enterChat(data.conversation_id, { full_name: '', role: 'channel' })
    } catch { /* noop */ }
  }
  // §D12: открыть/создать канал «Объявления · Группа» (teacher/admin) и перейти в него —
  // дальше публикация обычным send(), отдельного «отправить объявление» не нужно.
  async function openAnnouncementsChannel(groupName) {
    const g = (groupName || '').trim()
    if (!g) return 'Выберите группу'
    try {
      const { data } = await messengerApi.ensureAnnouncementsChannel(g)
      await loadChats()
      await _enterChat(data.conversation_id, { full_name: data.title, role: 'channel' })
      return ''
    } catch (e) { return _errText(e, 'Не удалось открыть канал объявлений') }
  }
  async function openPracticeChannel(groupName) {
    const g = (groupName || '').trim()
    if (!g) return 'Выберите группу'
    try {
      const { data } = await messengerApi.ensurePracticeChannel(g)
      await loadChats()
      await _enterChat(data.conversation_id, { full_name: data.title, role: 'channel' })
      return ''
    } catch (e) { return _errText(e, 'Не удалось открыть канал объявлений') }
  }
  // §12: найти/создать канал «Отчёты · Группа» и вернуть его id (только куратор этой
  // группы и только если у группы есть активный родитель — сервер проверяет обе границы).
  // Нужен диалогу отчёта: канал — один из возможных адресатов, наравне с личными чатами.
  async function ensureReportsChannel(groupName) {
    const g = (groupName || '').trim()
    if (!g) return { id: '', error: 'Выберите группу' }
    try {
      const { data } = await messengerApi.ensureCuratorReportsChannel(g)
      return { id: data.conversation_id, error: '' }
    } catch (e) { return { id: '', error: _errText(e, 'Не удалось открыть канал отчётов') } }
  }
  // §12: создать отчёт по группе и отправить его сообщением (в ЛС родителям и/или в
  // выбранные беседы; без адресатов — себе в «Избранное»). Возвращает текст ошибки или ''.
  // Ошибку ОБЯЗАТЕЛЬНО показываем: молчание в ответ на «Создать» и читалось как «не работает».
  async function createReport(group, userIds = [], convIds = []) {
    try {
      const { data } = await messengerApi.createReport(group, userIds, convIds)
      await loadChats()
      await _enterChat(data.conversation_id, { full_name: '', role: '' })
      return ''
    } catch (e) { return _errText(e, 'Не удалось создать отчёт') }
  }
  //Текст ошибки от сервера (detail) — он написан для человека, а не «Request failed 400».
  function _errText(e, fallback) {
    const d = e?.response?.data?.detail
    return (typeof d === 'string' && d) ? d : fallback
  }

  async function leaveActive() {
    if (!activeId.value) return
    try { await messengerApi.leave(activeId.value); clearActive(); await loadChats() } catch { /* noop */ }
  }
  // §D6: переименовать активную группу/канал (owner/admin) — сервер сам допишет системное
  // сообщение "title_changed:…" в ленту.
  async function renameActive(title, about) {
    if (!activeId.value) return false
    try {
      await messengerApi.renameChat(activeId.value, title, about)
      await loadConvInfo()
      await loadChats()
      return true
    } catch { return false }
  }

  /**
   * Аватарка активной группы/канала (02.09.2026, просьба Влада).
   *
   * ⚠️ Перечитываем И карточку беседы, И список чатов: картинка видна в обоих местах, и
   * обновив одно, мы получили бы старую аватарку в списке рядом с новой в карточке —
   * человек решил бы, что смена не сохранилась, и нажал ещё раз.
   */
  async function setActiveAvatar(dataUrl) {
    if (!activeId.value) return false
    try {
      await messengerApi.setChatAvatar(activeId.value, dataUrl || '')
      await loadConvInfo()
      await loadChats()
      return true
    } catch { return false }
  }

  // §ролей: выгнать/выдать роль/игнор — действуют на АКТИВНУЮ беседу, перегружают
  // activeInfo (там же лежат my_permissions/participants[].custom_role_id).
  /**
   * Добавить людей в активную беседу.
   *
   * ⚠️ Возвращает ЧИСЛО добавленных, а не `true`: сервер молча пропускает тех, кто уже
   * состоит в беседе или удалён, и «успех» при нуле добавленных ввёл бы человека в
   * заблуждение — он бы решил, что участники в чате, и не проверил.
   */
  async function addMembers(userIds, classGroups) {
    if (!activeId.value) return 0
    if (!userIds?.length && !classGroups?.length) return 0
    try {
      const { data } = await messengerApi.addMembers(activeId.value, userIds || [], classGroups || [])
      await loadConvInfo()
      return Number(data?.added || 0)
    } catch { return 0 }
  }
  async function kickMember(userId) {
    if (!activeId.value) return false
    try { await messengerApi.removeMember(activeId.value, userId); await loadConvInfo(); return true }
    catch { return false }
  }
  async function setMemberRole(userId, opts) {
    if (!activeId.value) return false
    try { await messengerApi.setMemberRole(activeId.value, userId, opts); await loadConvInfo(); return true }
    catch { return false }
  }
  async function toggleIgnore(userId, ignored) {
    if (!activeId.value) return false
    try {
      if (ignored) await messengerApi.unignoreMember(activeId.value, userId)
      else await messengerApi.ignoreMember(activeId.value, userId)
      await loadConvInfo()
      return true
    } catch { return false }
  }

  // §D7: мой статус (поверх presence) — dnd/studying/away + текст (только у преподавателя).
  const myStatus = ref({ kind: '', custom_text: '' })
  async function loadMyStatus() {
    try { const { data } = await messengerApi.getStatus(); myStatus.value = data } catch { /* noop */ }
  }
  // ── Авто-«отошёл» по бездействию (как в Discord) ─────────────────────────────────
  // Человек оставил вкладку открытой и ушёл — собеседник не должен думать, что ему
  // просто не отвечают. Ставим «отошёл» САМИ и снимаем при первом же действии.
  //
  // ⚠️ Возвращаем ровно тот статус, что был ДО отлучки, а не «обычный»: иначе выбранное
  // человеком «не беспокоить» тихо слетало бы после каждой отлучки, и он получал бы
  // звуки, от которых как раз отписался. И по той же причине авто-режим НЕ трогает
  // статусы, выставленные вручную сейчас, — только запоминает их на время отсутствия.
  const AWAY_AFTER_MS = 10 * 60 * 1000     //как в Discord — десять минут тишины
  let _idleTimer = null
  let _statusBeforeAway = null             //не null → «отошёл» поставили мы, а не человек

  async function _goAway() {
    if (_statusBeforeAway !== null || myStatus.value.kind === 'away') return
    _statusBeforeAway = { ...myStatus.value }
    await setMyStatus('away', myStatus.value.custom_text || '', true)
  }

  async function _comeBack() {
    if (_statusBeforeAway === null) return
    const prev = _statusBeforeAway
    _statusBeforeAway = null
    await setMyStatus(prev.kind, prev.custom_text || '', true)
  }

  function _resetIdle() {
    if (_idleTimer) clearTimeout(_idleTimer)
    _idleTimer = setTimeout(_goAway, AWAY_AFTER_MS)
    _comeBack()
  }

  const _IDLE_EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart']
  function startIdleWatch() {
    if (_idleTimer) return                 //уже следим — второй раз не подписываемся
    for (const e of _IDLE_EVENTS) window.addEventListener(e, _resetIdle, { passive: true })
    //Сворачивание вкладки — тоже отсутствие, и узнаём мы о нём сразу, не дожидаясь таймера.
    document.addEventListener('visibilitychange', _onVisibility)
    _resetIdle()
  }
  function stopIdleWatch() {
    for (const e of _IDLE_EVENTS) window.removeEventListener(e, _resetIdle)
    document.removeEventListener('visibilitychange', _onVisibility)
    if (_idleTimer) { clearTimeout(_idleTimer); _idleTimer = null }
    _statusBeforeAway = null
  }
  function _onVisibility() {
    if (document.visibilityState === 'hidden') _goAway()
    else _resetIdle()
  }

  //auto=true — статус меняем МЫ (авто-отошёл), а не человек: такой вызов не должен
  //затирать память о прежнем статусе, иначе возвращаться будет некуда.
  async function setMyStatus(kind, customText = '', auto = false) {
    if (!auto) _statusBeforeAway = null
    try {
      const { data } = await messengerApi.setStatus(kind, customText)
      myStatus.value = { kind: data.kind, custom_text: data.custom_text }
      return true
    } catch { return false }
  }

  // ── Действия над сообщением (Фаза 3) ────────────────────────────────────────────────
  function _replaceMsg(updated) {
    const i = messages.value.findIndex(x => x.id === updated.id)
    if (i >= 0) messages.value[i] = updated
  }

  async function editMessage(id, body) {
    const t = (body || '').trim()
    if (!t) return
    try { const { data } = await messengerApi.edit(id, t); _replaceMsg(data) } catch { /* noop */ }
  }

  async function setPinned(id, on) {
    try {
      if (on) { const { data } = await messengerApi.pin(id); _replaceMsg(data) }
      else { await messengerApi.unpin(id); const m = messages.value.find(x => x.id === id); if (m) m.pinned = false }
      await loadPinned()
    } catch { /* noop */ }
  }

  async function removeMessage(id, scope = 'self') {
    try {
      await messengerApi.deleteMessage(id, scope)
      if (scope === 'all') {
        const m = messages.value.find(x => x.id === id)
        if (m) { m.deleted = true; m.body = ''; m.pinned = false }
      } else {
        messages.value = messages.value.filter(x => x.id !== id)
      }
      await loadPinned()
      await loadChats()
    } catch { /* noop */ }
  }

  async function forwardMessages(ids, toConvIds) {
    if (!ids.length || !toConvIds.length) return
    try { await messengerApi.forward(ids, toConvIds); await loadChats() } catch { /* noop */ }
  }

  async function reportMessage(id, reasonCode, description = '') {
    try { await messengerApi.report(id, reasonCode, description); return true } catch { return false }
  }

  // §D3: поставить/снять реакцию-эмодзи. Обновляем локально ОПТИМИСТИЧНО (сервер отвечает
  // просто {ok:true}, без свежего списка реакций) — лишний перезапрос истории не нужен.
  async function toggleReaction(mid, emoji) {
    const msg = messages.value.find(x => x.id === mid)
    if (!msg) return
    const list = msg.reactions || []
    const cell = list.find(r => r.emoji === emoji)
    const wasMine = !!cell?.mine
    try {
      if (wasMine) await messengerApi.removeReaction(mid, emoji)
      else await messengerApi.addReaction(mid, emoji)
    } catch { return }
    if (wasMine) {
      cell.count -= 1
      msg.reactions = list.filter(r => r.count > 0)
    } else if (cell) {
      cell.count += 1; cell.mine = true
    } else {
      msg.reactions = [...list, { emoji, count: 1, mine: true }]
    }
  }

  // §D11: история редактирования сообщения (для попапа «(ред.)» → версии).
  async function messageHistory(mid) {
    try { const { data } = await messengerApi.messageHistory(mid); return data.versions || [] }
    catch { return [] }
  }

  // ── Организация списка чатов: закреп/архив/избранное (docs/MESSENGER-ADDON-PLAN-GPT*.md) ─
  async function togglePinChat(convId, on) {
    try {
      if (on) await messengerApi.pinChat(convId); else await messengerApi.unpinChat(convId)
      await loadChats()
      return true
    } catch { return false }
  }
  async function toggleArchiveChat(convId, on) {
    try {
      if (on) await messengerApi.archiveChat(convId); else await messengerApi.unarchiveChat(convId)
      if (on && activeId.value === convId) clearActive()   //ушли в архив — закрыть открытый тред
      await loadChats()
      return true
    } catch { return false }
  }
  // «Избранное» (Saved Messages) — личный чат с самим собой: заметки/ссылки/код себе, без
  // отдельной сущности заметок (переиспользуем всю инфраструктуру сообщений). Ленивое
  // создание на сервере при первом входе, закреплён по умолчанию (см. openSaved на сервере).
  async function openSaved() {
    try {
      const { data } = await messengerApi.openSaved()
      await loadChats()
      await _enterChat(data.conversation_id, { full_name: 'Избранное', role: 'saved' })
      return true
    } catch { return false }
  }

  // ── Черновики (клиент-только, docs/MESSENGER-ADDON-PLAN-GPT.md «Черновики») ────────────
  // Мессенджер и так не синкует состояние между устройствами (см. §5.4 CLAUDE.md) —
  // серверное хранилище черновика было бы лишней сущностью ради того же эффекта.
  // ⚠️ Сама механика хранения переехала в utils/drafts.js: карта ключуется ЛОГИНОМ и
  // стирается при выходе. Прежняя версия жила здесь, ключевалась только id беседы и
  // выход переживала — на общем телефоне следующий вошедший видел чужой недописанный
  // текст в ОБЩЕМ канале. Разбор — в шапке utils/drafts.js. Здесь оставлена тонкая
  // обёртка: вызывающие (ChatThread.vue) не менялись.

  // ── Шаблоны быстрых ответов преподавателя ───────────────────────────────────────────
  const templates = ref([])
  async function loadTemplates() {
    try { const { data } = await messengerApi.templates(); templates.value = data.templates || [] }
    catch { templates.value = [] }
  }
  async function addTemplate(body) {
    const t = (body || '').trim()
    if (!t) return false
    try { const { data } = await messengerApi.createTemplate(t); templates.value.push(data); return true }
    catch { return false }
  }
  async function removeTemplate(id) {
    try { await messengerApi.deleteTemplate(id); templates.value = templates.value.filter(x => x.id !== id) }
    catch { /* noop */ }
  }

  // ── Треды: просмотр ответов на сообщение (переиспользует reply_to_id, без нового
  // «тредового» состояния на сервере — см. docs/MESSENGER-ADDON-PLAN-GPT-SMART.md §3.3) ────
  const activeThread = ref(null)   // { parentId, messages: [] } | null
  async function openThread(messageId) {
    if (!activeId.value) return
    try {
      const { data } = await messengerApi.thread(activeId.value, messageId)
      activeThread.value = { parentId: messageId, messages: data.messages || [] }
    } catch { activeThread.value = { parentId: messageId, messages: [] } }
  }
  function closeThread() { activeThread.value = null }

  // ── Поиск внутри активного чата ──────────────────────────────────────────────────────
  const searchResults = ref(null)   // null — поиск закрыт; [] — открыт, но пусто
  const searching = ref(false)
  // §17: слова, которыми модель расширила запрос («домашка» → дз, задание). Показываем их
  // пользователю — иначе непонятно, почему нашлось сообщение без искомого слова.
  const searchExpanded = ref([])
  async function searchInActive(q) {
    if (!activeId.value || !(q || '').trim()) { searchResults.value = null; searchExpanded.value = []; return }
    searching.value = true
    try {
      // Умный поиск: сервер спрашивает у модели словоформы и синонимы к ЗАПРОСУ (саму
      // переписку она не видит) и ищет по всем вариантам сразу. Модель недоступна —
      // сервер вернёт то же, что обычный поиск, и expanded придёт пустым.
      const { data } = await messengerApi.aiSearchInChat(activeId.value, q)
      searchResults.value = data.messages || []
      searchExpanded.value = data.expanded || []
    } catch {
      // Умный поиск не ответил вовсе — откатываемся на обычный, а не показываем пустоту.
      searchExpanded.value = []
      try { const { data } = await messengerApi.searchInChat(activeId.value, q); searchResults.value = data.messages || [] }
      catch { searchResults.value = [] }
    } finally { searching.value = false }
  }
  function clearSearch() { searchResults.value = null; searchExpanded.value = [] }

  // Кто прочитал сообщение — по запросу (попап под сообщением), в общее состояние не кладём.
  async function readBy(mid) {
    try { const { data } = await messengerApi.readBy(mid); return data.users || [] }
    catch { return [] }
  }

  // ── Множественный выбор («Выделить») ────────────────────────────────────────────────
  function enterSelection(firstId = 0) {
    selectionMode.value = true
    selectedIds.value = firstId ? [firstId] : []
  }
  function toggleSelect(id) {
    const i = selectedIds.value.indexOf(id)
    if (i >= 0) selectedIds.value.splice(i, 1)
    else selectedIds.value.push(id)
  }
  function clearSelection() { selectionMode.value = false; selectedIds.value = [] }
  // «Выбрать всё / снять всё» — по видимым сообщениям беседы.
  function selectAll() { selectedIds.value = messages.value.map(x => x.id) }
  function selectNone() { selectedIds.value = [] }

  return {
    chats, invites, activeId, activePeer, messages, loadingChats, loadingMessages, loadingOlder, hasOlder, sending,
    replyTo, replyQuote, pinned, selectionMode, selectedIds, isModeration, activeInfo, activeKind,
    channels, dir,
    peerTyping, totalUnread, notice, activeChat, mascotCooldown,
    loadChats, loadInvites, answerInvite, loadMessages, loadOlder, selectChat, openWith, send, sendGif, markReadActive, loadPinned, setNotice,
    openModeration, pollOnce, startPolling, stopPolling, searchUsers, sendTyping,
    setReply, clearReply, clearActive, reset, loadConvInfo, muteConversation,
    clearChatById, deleteChatById, muteChatById, markChatUnread,
    groupDraftPeer, askAddToGroup, clearGroupDraft,
    restriction, loadRestriction,
    pendingJump, openById,
    deleteConversation, selectAll, selectNone,
    editMessage, setPinned, removeMessage, forwardMessages, reportMessage,
    toggleReaction, messageHistory,
    enterSelection, toggleSelect, clearSelection,
    createGroup, createChannel, loadChannels, joinChannel, leaveActive, renameActive,
    setActiveAvatar,
    sendFile, addMembers, kickMember, setMemberRole, toggleIgnore,
    openAnnouncementsChannel, openPracticeChannel, ensureReportsChannel, createReport,
    myStatus, loadMyStatus, setMyStatus, startIdleWatch, stopIdleWatch,
    togglePinChat, toggleArchiveChat, openSaved,
    draftFor, saveDraft, clearDraft,
    templates, loadTemplates, addTemplate, removeTemplate,
    activeThread, openThread, closeThread,
    searchResults, searching, searchExpanded, searchInActive, clearSearch,
    readBy,
  }
})
