<script setup>
// SchedulePage — расписание ВСГУТУ (порт ui/schedule_view.py). Недельная сетка:
// дни-колонки, карточки пар, переключатель I/II недели.
//
// Категории (портал читается в 4 разделах — schedule/parser.py::CATEGORIES):
// «Магистратура, колледж, ДОУ» (DEFAULT_CATEGORY) — единственная с реальными
// аккаунтами/журналом, остальные («Бакалавриат, специалитет», «Заочное 1/2») —
// только просмотр расписания. Кнопки-категории видны всем ролям; смена категории
// на неколледжевую переводит ЛЮБУЮ роль в режим выбора группы из списка — «моя
// группа»/«мои пары» имеют смысл только внутри колледжа, вне его для роли просто
// нет данных, которые можно было бы авто-подставить.
//
// Роли для КАТЕГОРИИ ПО УМОЛЧАНИЮ (колледж) — как и раньше:
//  • студент — сразу расписание СВОЕЙ группы (без выбора);
//  • преподаватель — сервер матчит его ФИО со спарсенными преподавателями портала:
//    совпало → сразу ЕГО пары; не совпало → две НЕвыбранные кнопки
//    «Расписание преподавателя» / «Расписание группы» (пункт 2);
//  • админ — выбор любой группы.
// Полный снимок для преподавателей сервер строит лениво в фоне (~минута) — пока
// готовится, показываем статус и кнопку «Проверить снова».
//
// Сессионные категории (Заочное 1/2, dated=true) — заголовки дней это конкретные
// календарные даты («Пнд, 06 апреля»), а не Пн-Сб, и число «недель» (сессионных
// блоков) не всегда 2 — см. dayColumns/weekButtons ниже.
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { RotateCw, User, Users, Download } from '@lucide/vue'
import { scheduleApi } from '@/api/endpoints'
import { useAuthStore } from '@/stores/auth'
import { useToast } from '@/composables/useToast'
import { useLocaleStore } from '@/stores/locale'
import * as widget from '@/services/scheduleWidget'
import DataFreshness from '@/components/ui/DataFreshness.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import AppButton from '@/components/ui/AppButton.vue'
import Badge from '@/components/ui/Badge.vue'
import { dayLabels, kindMap } from '@/config/scheduleView'

const locale = useLocaleStore()
// ⚠️ Подписи дней и типов занятий переехали в `config/scheduleView.js` — их рисует ещё и
// расписание БЕЗ входа (`PublicSchedule.vue`), а два словаря на одни и те же данные
// разошлись бы молча: одна и та же пара звалась бы по-разному на двух экранах.
const DAYS = computed(() => dayLabels(locale.t))
const KIND = computed(() => kindMap(locale.t))
const DEFAULT_CATEGORY = 'college'

const auth = useAuthStore()
const toast = useToast()
const isStudent = computed(() => auth.role === 'student')
const isTeacher = computed(() => auth.role === 'teacher')

const categories = ref([])
const category = ref(DEFAULT_CATEGORY)
const isDefaultCategory = computed(() => category.value === DEFAULT_CATEGORY)
const categoryDated = computed(() =>
  categories.value.find((c) => c.key === category.value)?.dated || false)

const groups = ref([])
const group = ref('')
const week = ref(1)
const data = ref(null)
const loading = ref(true)

// Режим преподавателя: '' — ещё не выбран (две кнопки), 'me' — свои пары (авто-матч),
// 'teacher' — конкретный преподаватель из списка, 'group' — расписание группы.
const mode = ref('')
const teachers = ref([])
const teacherName = ref('')
const building = ref(false)

// ── Выгрузка расписания файлом ──────────────────────────────────────────────────
// Сервер выгружает расписание ГРУППЫ, поэтому в преподавательском виде («мои пары»,
// где группы как таковой нет) кнопки не показываем: они молча выгрузили бы не то,
// что человек видит на экране.
const exporting = ref(false)
const exportGroup = computed(() =>
  (isTeacher.value && isDefaultCategory.value && mode.value !== 'group') ? '' : group.value)

async function downloadSchedule(fmt) {
  if (!exportGroup.value) return
  exporting.value = true
  try {
    const { data: blob } = await scheduleApi.exportFile(exportGroup.value, fmt, category.value)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${locale.t('schedulePage.fileNamePrefix', 'Расписание')}_${exportGroup.value}.${fmt === 'docx' ? 'docx' : 'xlsx'}`
      .replaceAll('/', '-')
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    toast.error(locale.t('schedulePage.exportFailed', 'Не удалось выгрузить расписание'))
  } finally { exporting.value = false }
}

onMounted(async () => {
  try {
    const r = (await scheduleApi.categories()).data
    categories.value = r.categories || []
    category.value = r.default || DEFAULT_CATEGORY
  } catch { categories.value = []; category.value = DEFAULT_CATEGORY }
  await enterCategory()
})

// Вход в категорию (при старте страницы и при каждой смене кнопки). Колледж —
// прежнее ролевое поведение (студент/препод авто, админ выбирает); любая другая
// категория — режим «выбор группы из списка» для ВСЕХ ролей, там нет ни своей
// группы, ни своего ФИО преподавателя — их просто неоткуда авто-подставить.
async function enterCategory(fromUser = false) {
  data.value = null
  week.value = 1
  stopPoll()
  //Живой баг 3.5.5, вернулся в новом виде: суб-режим «расписание преподавателей»
  //раньше сбрасывался на «группа» при ЛЮБОМ переключении категории — сперва чинили
  //только для роли «преподаватель» (isTeacher-гейт), и тот же баг остался для
  //студента: он тоже умеет открыть «Расписание преподавателя» (см. chooseTeacherMode
  //ниже), но при смене категории вылетал в группу как ни в чём не бывало. Проверка
  //теперь на ЗНАЧЕНИЕ mode, а не на роль — как уже сделано в AdminSchedule.vue,
  //где ТА ЖЕ логика (`if (mode.value === 'teacher') {...}`) ни от какой роли не
  //зависит и сразу работала верно.
  //Если мы уже были в 'teacher' — держим это же значение (не сбрасываем в '' до
  //ответа сервера): loadTeacher('') подтвердит 'me' при авто-совпадении ФИО
  //(только у реальных преподавателей) или оставит 'teacher' как есть, вместо
  //падения в общий экран выбора.
  //Авто-матч «мои пары» (mode==='me') НЕ переносим специально: это чистый побочный
  //эффект удачного совпадения ФИО на конкретной категории, а не осознанный выбор —
  //переносить его в категорию, где он не пробовался, значило бы врать про «моё».
  const wasTeacherMode = mode.value === 'teacher'
  if (wasTeacherMode || (isTeacher.value && isDefaultCategory.value && mode.value === '')) {
    mode.value = wasTeacherMode ? 'teacher' : ''
    teacherName.value = ''
    await loadTeacher('')
    return
  }
  mode.value = 'group'
  group.value = ''
  await loadGroupsList()
  //⚠️ 3.6: студент при ВХОДЕ на страницу всегда попадает на СВОЮ группу — независимо от
  //категории. Раньше условие требовало ещё и колледжа (isDefaultCategory), поэтому
  //студент бакалавриата/заочного открывал вкладку и упирался в «выберите группу», хотя
  //его собственная известна серверу. Свою категорию сервер сообщает в ответе (см.
  //schedule_get) — кнопки категорий переключатся сами.
  //А вот при РУЧНОЙ смене категории (`fromUser`) своя группа не подставляется: человек
  //осознанно пошёл смотреть чужую категорию, и там его группы нет.
  if (isStudent.value && !fromUser) {
    await load()
    //§f (3.6.1, по аналогии с преподавателем): группа НЕ резолвилась (нет своей группы/
    //данные разошлись) — тот же двухкнопочный выбор «Расписание группы / Расписание
    //преподавателя», что у непойманного по ФИО преподавателя, вместо тихого пустого
    //списка. Резолвится (обычный случай) — mode остаётся 'group', экран не показывается,
    //ни одного лишнего клика не появляется.
    if (!group.value) mode.value = ''
  } else {
    loading.value = false // ждём выбора группы из списка
  }
}

async function onCategoryChange(key) {
  if (key === category.value) return
  category.value = key
  await enterCategory(true)
}

// Курс (3.5.5) — сужает список групп ВНУТРИ категории. Разведано с портала (столбец
// таблицы индекса, schedule_web.groups_by_course) — число курсов НЕ фиксировано на 4.
// ⚠️ Актуальность ответа сверяем ТОКЕНОМ запроса, а не значениями category/group.
// Так было раньше (3.5.4) и это дало живой баг: load() ДЛЯ СТУДЕНТА вызывается с пустой
// group (сервер сам подставляет свою группу студента), а потом САМА ЖЕ присваивает
// group.value = r.group — то есть к моменту finally запомненное forGroup ('') уже не
// равно текущему ('К74/1'), сброс loading не срабатывал, и расписание «грузилось»
// вечно, пока человек не нажмёт «Обновить» (на втором заходе группа уже известна, и
// сравнение случайно сходилось). Счётчик от этого свободен по построению: он не зависит
// от данных, которые сама функция и меняет.
let reqSeq = 0
const nextReq = () => ++reqSeq

// ━━━ КОПИЯ НА ЭКРАН СРАЗУ, СВЕЖЕЕ — МОЛЧА ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//
// 🔥 Жалоба Влада (07.09.2026): «при открытии расписания оно грузится заново каждый раз».
// Копия прошлого ответа лежала на диске всё это время (api/offlineCache.js наполняется на
// КАЖДОМ успешном GET), но читал её только обработчик ошибки — то есть при живой сети её
// не показывали ни разу. На телефоне это самый заметный экран: заходят в него чаще
// прочих, а мобильная сеть даёт те самые полсекунды пустоты.
//
// ⚠️ Копия НЕ отменяет запрос. Расписание правят на портале и в админке, и показать
// вчерашнее вместо сегодняшнего значило бы поменять один дефект на худший — человек
// придёт не на ту пару. Поэтому запрос уходит всегда, а копия лишь закрывает ожидание.
//
// ⚠️ СРОК ГОДНОСТИ ОБЯЗАТЕЛЕН. Расписание недельное: копия недельной давности — правда,
// месячной — уже нет. Слишком старую не показываем вовсе, честный спиннер лучше
// уверенной неправды.
const CACHE_SHOW_MS = 7 * 24 * 60 * 60 * 1000

/** Стоит ли показывать копию, снятую в момент `at` (мс). */
function worthShowing(at) {
  return !!at && Date.now() - at < CACHE_SHOW_MS
}

/**
 * Положить копию на экран. Только то, что нужно для ОТРИСОВКИ.
 *
 * ⚠️ Побочных действий здесь нет намеренно: переключение категории, догрузка списка
 * групп и снимок для виджета остаются за настоящим ответом. Копия — это картинка, а не
 * источник решений; иначе устаревшее поле `category` увело бы человека в чужую
 * категорию ещё до того, как сервер успел ответить.
 */
function paintCached(hit) {
  if (!hit || !worthShowing(hit.at) || !hit.data?.schedule) return false
  data.value = hit.data
  const wk = Object.keys(hit.data.schedule?.weeks || {}).map(Number).sort((a, b) => a - b)
  week.value = categoryDated.value ? (wk[0] || 1) : (hit.data.week || 1)
  loading.value = false
  return true
}

const byCourse = ref({})
const courseFilter = ref('')
const courseKeys = computed(() => Object.keys(byCourse.value).map(Number).sort((a, b) => a - b))
const groupsForCourse = computed(() => {
  if (!courseFilter.value) return groups.value
  const names = new Set(byCourse.value[courseFilter.value] || [])
  return groups.value.filter((g) => names.has(g))
})

async function loadGroupsList() {
  //Категория, ДЛЯ которой запрошен список — не читаем category.value ПОСЛЕ await:
  //быстрое переключение кнопок-категорий (клик-клик-клик) запускает несколько таких
  //вызовов одновременно, и более медленный ответ (например, у Заочного 1 список
  //короче — придёт раньше) мог прилететь ПОСЛЕ более быстрого и молча подменить
  //список группами не той категории, хотя кнопка уже показывает другую — реальный
  //баг, со стороны выглядел как «всё перемешано».
  const my = nextReq()
  const forCategory = category.value
  courseFilter.value = ''
  try {
    const r = (await scheduleApi.groups(forCategory)).data
    if (my !== reqSeq) return
    groups.value = r.groups || []
    byCourse.value = r.by_course || {}
  } catch {
    if (my === reqSeq) { groups.value = []; byCourse.value = {} }
  }
}

async function load() {
  const my = nextReq()
  const forCategory = category.value
  const forGroup = group.value
  //Спиннер поднимаем ТОЛЬКО если показать нечего: иначе экран моргнул бы «загрузка» и
  //тут же вернул то же самое, что на нём и было, — это раздражает сильнее ожидания.
  if (!paintCached(scheduleApi.cachedGet(forGroup || undefined, forCategory))) {
    loading.value = true
  }
  try {
    const r = (await scheduleApi.get(forGroup || undefined, forCategory)).data
    //Устаревший ответ отбрасываем: пока шёл запрос, могли сменить категорию или группу
    //(клик-клик по кнопкам), и более медленный ответ подменил бы уже выбранное.
    if (my !== reqSeq) return
    data.value = r
    group.value = r.group || group.value
    //Категорию СВОЕЙ группы знает только сервер (Group.category). Пришла другая, чем
    //подсвечена, — переключаем кнопки и догружаем её список групп, иначе человек видит
    //своё расписание с отмеченной чужой категорией и пустым выпадающим списком.
    if (r.category && r.category !== category.value) {
      category.value = r.category
      loadGroupsList()
    }
    const wk = Object.keys(r.schedule?.weeks || {}).map(Number).sort((a, b) => a - b)
    //Сессионные категории — своя «текущая» неделя не существует (это не календарная
    //чётность, а порядковый номер сессии) — берём первый реально найденный блок.
    week.value = categoryDated.value ? (wk[0] || 1) : (r.week || 1)
    //Запрос БЕЗ группы делает только тот, у кого она своя (студент): группу подставил
    //сервер по токену, значит это и есть «своя». Запоминаем, чтобы и после ручного
    //ухода на чужую группу и возврата обратно виджет снова обновлялся.
    if (isStudent.value && !forGroup && r.group) ownGroup.value = r.group
    if (isStudent.value && r.group && r.group === ownGroup.value) pushWidgetSnapshot('group', r)
  } catch {
    //⚠️ Показанную копию НЕ стираем: «нет связи» — не повод убрать с экрана расписание,
    //которое человек уже читает. Пусто было и остаётся пусто только если копии не было.
    if (my === reqSeq && !data.value) data.value = null
  } finally {
    //Спиннер снимает ТОЛЬКО последний запрос — и снимает его всегда, независимо от того,
    //что успело поменяться в category/group за время ожидания (см. комментарий у reqSeq).
    if (my === reqSeq) loading.value = false
  }
}

// ── Снимок для нативного виджета на рабочем столе Android ───────────────────────
// Виджет наполняет ТОЛЬКО приложение (у него нет своего доступа к серверу: токен
// живёт жёстко 5 часов, см. services/scheduleWidget.js). Здесь единственное место,
// где расписание уже загружено и точно принадлежит текущему аккаунту.
//
// ⚠️ Сохраняем СТРОГО СВОЁ расписание: студент и админ могут открыть любую группу,
// преподаватель — любого коллегу, и без этой проверки виджет показывал бы чужие
// пары. Признак «своё» разный у ролей: у студента — группа, которую сервер подставил
// САМ (запрос без group), у преподавателя — авто-совпадение по ФИО (matched_self).
//
// Сессионные категории (Заочное 1/2) сюда не попадают намеренно: днями там служат
// календарные даты без года, восстановить ISO-дату не из чего — см. fromDatedSchedule.
const ownGroup = ref('')

function pushWidgetSnapshot(kind, resp) {
  if (!widget.isAvailable() || !widget.isWanted()) return
  if (categoryDated.value) return
  try {
    const snap = kind === 'teacher'
      ? widget.fromTeacherSchedule(resp, resp?.teacher || auth.user?.name || '')
      : widget.fromGroupSchedule(resp, resp?.group || '')
    if (snap) widget.save(snap)
  } catch (e) {
    //Виджет — дополнение: его сбой не имеет права ломать саму страницу расписания.
    console.warn('[widget] снимок не собран:', e)
  }
}

let pollTimer = null
function stopPoll() { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null } }
onBeforeUnmount(stopPoll)

async function loadTeacher(name) {
  const my = nextReq()
  if (!paintCached(scheduleApi.cachedTeacher(name, category.value))) {
    loading.value = true
  }
  try {
    const r = (await scheduleApi.teacher(name, category.value)).data
    //Тот же токен, что у load()/loadGroupsList: переключение категории во время
    //ожидания не должно подменять список преподавателей уже другой категорией.
    if (my !== reqSeq) return
    building.value = !!r.building
    teachers.value = r.teachers || []
    week.value = r.week || 1
    if (r.available) {
      data.value = r
      teacherName.value = r.teacher
      if (r.matched_self) mode.value = 'me'
      else if (name) mode.value = 'teacher'
      //Только СВОИ пары (авто-совпадение по ФИО). Расписание коллеги, открытое из
      //любопытства, в виджет попадать не должно.
      if (r.matched_self) pushWidgetSnapshot('teacher', r)
    } else {
      data.value = null
      if (name) mode.value = 'teacher'   // выбрал вручную, но пар нет — остаёмся в режиме
    }
    // Снимок ещё строится на сервере — АВТО-повтор через 5 с (без ручного «Обновить»),
    // пока не будет готов. Прогрев при старте сервера делает ожидание редким.
    stopPoll()
    if (building.value) pollTimer = setTimeout(() => loadTeacher(name), 5000)
  } catch {
    if (my === reqSeq) data.value = null
  } finally {
    if (my === reqSeq) loading.value = false
  }
}

async function chooseTeacherMode() {
  mode.value = 'teacher'
  if (!teachers.value.length) await loadTeacher('')
}
async function chooseGroupMode() {
  mode.value = 'group'
  data.value = null
  if (!groups.value.length) await loadGroupsList()
  if (group.value) await load()
  else loading.value = false
}
function refresh() {
  if (isDefaultCategory.value && isTeacher.value && mode.value !== 'group') {
    loadTeacher(mode.value === 'teacher' ? teacherName.value : '')
  } else load()
}

const weeks = computed(() => data.value?.schedule?.weeks || {})
const weekKeys = computed(() => Object.keys(weeks.value).map(Number).sort((a, b) => a - b))
// Кнопки недели/сессии: обычные категории — ВСЕГДА [1, 2] (как и раньше — кнопка
// нужна и тогда, когда у группы на эту чётность нет пар, просто покажет «нет
// занятий»); сессионные — реальное число блоков (не фиксировано, у разных групп
// заочки может отличаться).
const weekButtons = computed(() => {
  if (!categoryDated.value) return [1, 2]
  return weekKeys.value.length ? weekKeys.value : [1]
})
function weekButtonLabel(wk) {
  if (!categoryDated.value) {
    const label = wk === 2 ? locale.t('schedulePage.week2', 'II неделя') : locale.t('schedulePage.week1', 'I неделя')
    return label + (data.value?.week === wk ? locale.t('schedulePage.nowSuffix', '  · сейчас') : '')
  }
  const days = Object.keys(weeks.value[String(wk)] || {})
  if (!days.length) return locale.t('schedulePage.sessionN', { n: wk })
  const first = days[0].split(', ').pop()
  const last = days[days.length - 1].split(', ').pop()
  return first === last
    ? locale.t('schedulePage.sessionNSingle', { n: wk, date: first })
    : locale.t('schedulePage.sessionNRange', { n: wk, from: first, to: last })
}
function dayLessons(dayKey) { return (weeks.value[String(week.value)] || {})[dayKey] || [] }
const hasAny = computed(() => data.value?.available && Object.keys(weeks.value).length)
// Дни-колонки: обычные категории — фиксированные 6 (даже пустые, как и раньше);
// сессионные — реальные даты ТЕКУЩЕЙ недели/сессии, в порядке разбора (уже
// хронологический — см. schedule/parser.py::parse_group_page_dated).
const dayColumns = computed(() => {
  if (!categoryDated.value) return DAYS.value
  return Object.keys(weeks.value[String(week.value)] || {}).map((d) => [d, d])
})
// Преподаватель ещё не выбрал режим и авто-матч не сработал → экран с двумя кнопками.
// ⚠️ (живой отзыв Влада) Раньше здесь ЕЩЁ И проверялась isDefaultCategory — верно
// для АВТО-матча при входе (тот проверяет её отдельно, прямо в enterCategory(), этот
// computed там не участвует), но неверно для РУЧНОГО сброса кнопкой «← к выбору»:
// снаружи колледжа mode становился '', а экран выбора всё равно не показывался
// (teacherChoice оставался false из-за категории) — кнопка молча откатывала на
// список групп и переставала на вид что-либо делать при повторных нажатиях. Экран
// выбора не завязан на авто-матч ФИО — это ручное переключение вида, оно уместно в
// любой категории, как и у studentChoice ниже (у него такого гейта не было изначально).
const teacherChoice = computed(() => isTeacher.value && mode.value === '')
// Студент (§f, 3.6.1): та же пара кнопок — но только КОГДА автоподстановка своей группы
// не сработала (см. enterCategory) — обычно экран вообще не появляется, группа известна
// сразу. В отличие от teacherChoice, не привязан к категории: авто-детект своей группы
// (3.6) работает в любой, значит и откат на выбор уместен в любой же.
const studentChoice = computed(() => isStudent.value && mode.value === '')
const modeChoice = computed(() => teacherChoice.value || studentChoice.value)
</script>

<template>
  <div class="space-y-4">
    <!-- Расписание особенно опасно показывать без пометки: без сети оно выглядит точно
         так же, как свежее, а пары за это время могли переставить. Подпись отвечает на
         вопрос «на какой момент это верно». Ищем последний сохранённый ответ ЛЮБОГО
         запроса расписания: параметры (группа, категория) у него разные, а человеку
         нужна просто дата снимка. -->
    <DataFreshness url="/web/schedule" />
    <!-- Категории портала (как на сайте) — видны, КОГДА уже выбран режим (расписание
         преподавателя/группы). §3.5.5, живой отзыв: до выбора режима категории путали —
         выглядело так, будто они выбирают что-то ещё до основного выбора; пока препод не
         решил, что смотреть, эти кнопки нечего показывать. -->
    <!-- «← к выбору» — явная кнопка сброса режима (расписание группы/преподавателя),
         тот же приём, что уже есть в AdminSchedule.vue: живёт в ЭТОЙ строке, а не
         спрятана в тексте фильтров, поэтому видна независимо от того, сколько
         категорий портала вообще есть. -->
    <div v-if="!modeChoice" class="flex flex-wrap items-center justify-between gap-2">
      <div v-if="categories.length > 1" class="flex flex-wrap gap-2">
        <button v-for="c in categories" :key="c.key"
                class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
                :class="category === c.key
                  ? 'border-accent bg-accent text-white'
                  : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
                @click="onCategoryChange(c.key)">
          {{ c.label }}
        </button>
      </div>
      <button type="button" class="text-xs text-text3 underline-offset-2 hover:text-accent hover:underline"
              @click="mode = ''">
        {{ locale.t('schedulePage.backToChoice', '← к выбору') }}
      </button>
    </div>

    <!-- Преподаватель без авто-совпадения ФИО ИЛИ студент без резолвнутой группы (§f,
         3.6.1) — две НЕвыбранные кнопки. У студента появляется практически никогда:
         обычный случай (своя группа известна) резолвится в enterCategory() ДО первого
         показа этого экрана, см. studentChoice. -->
    <div v-if="modeChoice" class="flex min-h-[50vh] flex-col items-center justify-center gap-4">
      <p v-if="loading" class="text-sm text-text3">{{ locale.t('common.loading', 'Загрузка…') }}</p>
      <template v-else>
        <p class="text-sm text-text2">
          {{ building ? locale.t('schedulePage.buildingTeachers', 'Индекс преподавателей ещё готовится на сервере (~минута).') :
             studentChoice ? locale.t('schedulePage.studentNotMatched', 'Не удалось определить вашу группу — выберите, что показать:') :
             locale.t('schedulePage.notMatched', 'Ваше ФИО не найдено в спарсенном расписании — выберите, что показать:') }}
        </p>
        <div class="flex flex-wrap justify-center gap-3">
          <AppButton variant="ghost" :disabled="building" @click="chooseTeacherMode">
            <User class="size-4" /> {{ locale.t('schedulePage.teacherSchedule', 'Расписание преподавателя') }}
          </AppButton>
          <AppButton variant="ghost" @click="chooseGroupMode">
            <Users class="size-4" /> {{ locale.t('schedulePage.groupSchedule', 'Расписание группы') }}
          </AppButton>
        </div>
        <AppButton v-if="building" variant="green" size="sm" @click="loadTeacher('')">
          <RotateCw class="size-3.5" /> {{ locale.t('schedulePage.checkAgain', 'Проверить снова') }}
        </AppButton>
      </template>
    </div>

    <template v-else>
      <div class="flex flex-wrap items-center gap-3">
        <!-- Студент в категории по умолчанию: своя группа (ярлык). Преподаватель в
             ней же: свои пары / выбор коллеги / выбор группы. Все остальные случаи
             (админ, либо любая роль вне колледжа) — выбор группы из списка. -->
        <!-- ⚠️ 3.6: у СТУДЕНТА здесь стоял неинтерактивный ярлык с его группой, а не
             выпадающий список — отсюда живой отзыв «кнопка с выбором группы не работает,
             смотреть можно везде кроме колледжа»: вне колледжа он попадал в общую ветку
             со списком, а в своей категории выбора не было вовсе. Теперь список у всех:
             своя группа подставлена сервером (её же он и открывает при входе), но при
             желании можно посмотреть чужую. -->
        <template v-if="mode === 'teacher' || mode === 'me'">
          <div v-if="mode === 'me'" class="flex h-10 items-center gap-2 rounded-sm border border-border2 bg-card2 px-3 text-sm">
            <User class="size-4 text-accent" />
            <span class="font-medium text-text">{{ teacherName }}</span>
            <span class="text-xs text-text3">· {{ locale.t('schedulePage.myLessons', 'мои пары') }}</span>
          </div>
          <select v-else v-model="teacherName" class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent sm:w-auto sm:max-w-64"
                  @change="loadTeacher(teacherName)">
            <option value="" disabled>{{ locale.t('schedulePage.teacherPlaceholder', 'Преподаватель…') }}</option>
            <option v-for="t in teachers" :key="t" :value="t">{{ t }}</option>
          </select>
          <!-- §3.5.5: живой баг — индекс преподавателей греется ФОНОМ и на первый заход
               в неколледжевую категорию может ещё не быть готов (~минута на живых
               данных бакалавриата); подсказка раньше показывалась ТОЛЬКО для колледжа
               (внутри teacherChoice), и пустой список вне колледжа выглядел так, будто
               преподавателей там нет вовсе, хотя на портале они есть. -->
          <template v-if="building && !teachers.length">
            <span class="text-xs text-text3">{{ locale.t('schedulePage.buildingTeachers', 'Индекс преподавателей ещё готовится на сервере (~минута).') }}</span>
            <AppButton variant="green" size="sm" @click="loadTeacher(teacherName)">
              <RotateCw class="size-3.5" /> {{ locale.t('schedulePage.checkAgain', 'Проверить снова') }}
            </AppButton>
          </template>
          <!-- ⚠️ (живой отзыв Влада) Раньше здесь и у соседней кнопки (ниже) был общий
               подписанный «к выбору»/«смотреть по группе» текст, который на деле МЕНЯЛ
               СМЫСЛ в зависимости от роли и текущего режима — с двух разных мест разными
               словами читалось одно и то же действие. Название теперь ВСЕГДА по
               НАЗНАЧЕНИЮ (куда ведёт клик), а не по роли/состоянию. -->
          <button class="text-xs text-text3 underline-offset-2 hover:text-accent hover:underline" @click="chooseGroupMode">
            {{ locale.t('schedulePage.groupSchedule', 'Расписание группы') }}
          </button>
        </template>

        <template v-else>
          <!-- Курс — сужает выпадающий список ниже (3.5.5), не фиксирован на 4. -->
          <select v-if="courseKeys.length > 1" v-model="courseFilter" class="h-10 rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent">
            <option value="">{{ locale.t('adminGroups.allCourses', 'Все курсы') }}</option>
            <option v-for="c in courseKeys" :key="c" :value="c">{{ locale.t('adminGroups.courseN', { n: c }) }}</option>
          </select>
          <select v-model="group" class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent sm:w-auto" @change="load">
            <option v-if="!groupsForCourse.length" :value="group">{{ group || locale.t('schedulePage.groupPlaceholder', 'Группа') }}</option>
            <option v-for="g in groupsForCourse" :key="g" :value="g">{{ g }}</option>
          </select>
          <!-- Студенту (как и преподавателю) даём посмотреть расписание преподавателя:
               «у кого сегодня физика» — обычный вопрос, а не привилегия сотрудника.
               ⚠️ Раньше у преподавателя здесь стояло «← к выбору» — то же самое слово,
               что и у ВЕРХНЕЙ кнопки сброса режима (см. выше), хотя ведут они в разные
               места: та сбрасывает выбор целиком, эта просто переключает вид. Название —
               по назначению, для всех ролей одинаково. -->
          <button class="text-xs text-text3 underline-offset-2 hover:text-accent hover:underline" @click="chooseTeacherMode">
            {{ locale.t('schedulePage.teacherSchedule', 'Расписание преподавателя') }}
          </button>
        </template>

        <div class="flex flex-wrap overflow-hidden rounded-sm border border-border2">
          <button v-for="wk in weekButtons" :key="wk" class="px-3 py-2 text-sm"
                  :class="week === wk ? 'bg-accent text-white' : 'bg-card2 text-text3'"
                  @click="week = wk">{{ weekButtonLabel(wk) }}</button>
        </div>
        <AppButton variant="ghost" size="sm" @click="refresh"><RotateCw class="size-3.5" /> {{ locale.t('schedulePage.refresh', 'Обновить') }}</AppButton>

        <!-- Выгрузка расписания файлом. Показываем только для расписания ГРУППЫ:
             у преподавательского вида группы нет, а сервер выгружает именно её. -->
        <template v-if="exportGroup && hasAny">
          <AppButton variant="ghost" size="sm" :disabled="exporting"
                     @click="downloadSchedule('xlsx')">
            <Download class="size-3.5" /> {{ locale.t('schedulePage.exportExcel', 'Excel') }}
          </AppButton>
          <AppButton variant="ghost" size="sm" :disabled="exporting"
                     @click="downloadSchedule('docx')">
            <Download class="size-3.5" /> {{ locale.t('schedulePage.exportWord', 'Word') }}
          </AppButton>
        </template>

        <span v-if="!categoryDated && data?.week" class="text-xs text-text3">{{ locale.t('schedulePage.currentWeek', { roman: data.week === 2 ? 'II' : 'I' }) }}</span>
      </div>

      <p v-if="loading" class="text-sm text-text3">{{ locale.t('schedulePage.loadingSchedule', 'Загрузка расписания…') }}</p>
      <EmptyState v-else-if="!hasAny" :title="locale.t('schedulePage.unavailableTitle', 'Расписание недоступно')"
                  :message="building ? locale.t('schedulePage.buildingHint', 'Индекс расписания готовится на сервере (~минута) — нажмите «Обновить».')
                            : locale.t('schedulePage.noSnapshotHint', 'Не удалось получить снимок с портала ВСГУТУ (нет связи или расписание не найдено).')" />

      <!-- Десктоп показывает дни ОДНИМ горизонтальным рядом; на узких — переносим.
           ⚠️ Шесть колонок включаются только с 2xl (1536px). На xl (1280px) за вычетом
           бокового меню на день оставалось ~170px: время рвалось на две строки, а
           названия предметов — на два-три слова, и ряд выглядел рваным (поймано
           скриншотом на 1280). Для сессионных категорий число колонок не фиксировано —
           сетка просто перетекает.
           h-full на карточке — чтобы дни в одном ряду были РАВНОЙ высоты: без него
           карточка тянется по своему содержимому, и ряд «лесенкой» выглядит как сбой
           вёрстки, хотя данные верные. -->
      <div v-else class="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-6">
        <div v-for="[key, full] in dayColumns" :key="key" class="flex h-full flex-col rounded-lg border border-border bg-card p-3 shadow-card">
          <p class="mb-2 font-title text-base font-bold text-text">{{ full }}</p>
          <p v-if="!dayLessons(key).length" class="py-4 text-center text-xs text-text2">{{ locale.t('schedulePage.noLessons', 'Занятий нет') }}</p>
          <ul v-else class="space-y-2">
            <li v-for="(l, i) in dayLessons(key)" :key="i" class="rounded-md border border-border bg-card2 p-2.5">
              <!-- ⚠️ `flex-wrap`: в узкой колонке дня бейджи обязаны переехать на СЛЕДУЮЩУЮ
                   строку целиком, а не ужиматься до столбика символов (жалоба 06.09.2026).
                   Сам перенос внутри бейджа запрещён в самом компоненте. -->
              <div class="mb-1 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                <!-- Время не переносим: «10:45–12:20» на двух строках читается как две пары. -->
                <span class="whitespace-nowrap text-xs font-semibold text-text3">{{ l.pair_no }}. {{ l.time }}</span>
                <span class="flex shrink-0 items-center gap-1.5">
                  <!-- 🔥 ПОДГРУППА (01.09.2026). Портал кладёт в одну клетку ДВА разных
                       занятия — своё для каждой подгруппы, — и до этой правки второе
                       пропадало бесследно: половина группы не видела своей пары.
                       Теперь показаны обе, и метка обязательна: без неё в расписании
                       просто две пары в одно время, и понять, которая твоя, нельзя. -->
                  <Badge v-if="l.subgroup" variant="muted">{{ locale.t('schedulePage.subgroup', { n: l.subgroup }) }}</Badge>
                  <Badge v-if="l.kind" :variant="(KIND[l.kind] || ['', 'muted'])[1]">{{ (KIND[l.kind] || [l.kind])[0] }}</Badge>
                </span>
              </div>
              <p class="text-sm font-medium text-text">{{ l.subject || l.raw }}</p>
              <!-- У пары преподавателя показываем ГРУППУ (у кого ведёт), у групповой — преподавателя. -->
              <p v-if="l.group || l.teacher || l.room" class="mt-0.5 text-xs text-text3">
                {{ l.group ? locale.t('schedulePage.groupLabel', { group: l.group }) : l.teacher }}<span v-if="l.room"> · {{ locale.t('schedulePage.roomLabel', { room: l.room }) }}</span>
              </p>
            </li>
          </ul>
        </div>
      </div>
    </template>
  </div>
</template>
