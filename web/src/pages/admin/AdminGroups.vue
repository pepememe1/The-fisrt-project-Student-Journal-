<script setup>
// AdminGroups — группы + CRUD (Phase B). Создание/правка (список предметов группы) /
// удаление; кнопка «🏫 Из расписания» добавляет спарсенные группы колледжа (как в
// десктопе). Пишется в те же таблицы (id=grp:name) → синкается в десктоп.
import { ref, computed, watch, onMounted } from 'vue'
import { adminApi, scheduleApi, termsApi } from '@/api/endpoints'
import AppButton from '@/components/ui/AppButton.vue'
import TeacherSuggestionsDialog from '@/components/admin/TeacherSuggestionsDialog.vue'
import InviteDialog from '@/components/admin/InviteDialog.vue'
import Badge from '@/components/ui/Badge.vue'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocaleStore } from '@/stores/locale'

const toast = useToast()
const { confirm } = useConfirm()
const locale = useLocaleStore()
const rows = ref([])
const loading = ref(true)
const allSubjects = ref([])
const parsedGroups = ref([])
const allTeachers = ref([])   // §ролей: [{id, name, subjects}] — для выбора препода на предмет

// ── Категории расписания (schedule/parser.py::CATEGORIES) — та же идея, что в
// «Расписании»: кнопки-фильтр сверху таблицы + выбор при создании группы. «Все
// категории» по умолчанию — 100% прежнее поведение (видно всё).
// Выпадающий список кнопки «+». Держим ЗДЕСЬ, а не в компоненте-обёртке: пунктов
// четыре и они разные по природе, общий «дропдаун на всё» пришлось бы параметризовать
// сильнее, чем стоит эта экономия.
const addMenu = ref(false)
function runAddAction(fn) {
  // Закрываем ДО действия: часть пунктов открывает модалку, и меню осталось бы висеть
  // поверх неё — на телефоне это перекрывает половину диалога.
  addMenu.value = false
  fn()
}

const categories = ref([])
const categoryFilter = ref('')
// Основная категория — та, где живут группы с журналом. Остальные заводятся как
// каталожные записи для просмотра расписания (см. Group.category в моделях).
const DEFAULT_CATEGORY = 'college'
// ── Курс (3.5.5) — сужает список ВНУТРИ выбранной категории. Курс НЕ хранится на
// Group: это живой, разведанный с портала признак (столбец таблицы индекса, см.
// schedule_web.groups_by_course), сверяем группы АДМИНА с этим списком по имени —
// группа, которой на портале нет (заведена вручную), просто не попадёт ни в один курс.
const courseFilter = ref('')
const byCourse = ref({})   // {курс: [имена с портала]} — для ТЕКУЩЕЙ categoryFilter
async function loadByCourse() {
  courseFilter.value = ''
  // 🔥 Пустая категория — это НЕ «курсы не нужны» (жалоба Влада 03.09.2026: «у админа
  // нет сортировки по курсу»). Список курсов грузился только ПОСЛЕ выбора категории, а
  // по умолчанию она не выбрана — то есть сортировки не было вовсе, пока человек не
  // догадается нажать «Колледж». Без категории берём основную: в ней и живут группы с
  // журналом, остальные заводятся как каталожные записи.
  const cat = categoryFilter.value || DEFAULT_CATEGORY
  try { byCourse.value = (await scheduleApi.groups(cat)).data.by_course || {} }
  catch { byCourse.value = {} }
}
watch(categoryFilter, loadByCourse)
const courseKeys = computed(() => Object.keys(byCourse.value).map(Number).sort((a, b) => a - b))
const filteredRows = computed(() => {
  let list = rows.value
  if (categoryFilter.value) list = list.filter((g) => (g.category || 'college') === categoryFilter.value)
  if (courseFilter.value) {
    const names = new Set(byCourse.value[courseFilter.value] || [])
    list = list.filter((g) => names.has(g.name))
  }
  return list
})
const nonCollegeCategories = computed(() => categories.value.filter((c) => c.key !== 'college'))
async function loadCategories() {
  try { categories.value = (await scheduleApi.categories()).data.categories || [] }
  catch { categories.value = [] }
}
function categoryLabel(key) {
  return categories.value.find((c) => c.key === key)?.label || key
}

// ── Учебный период (только чтение) ────────────────────────────────────────────
// ⚠️ Живой баг (3.6.1): ручной «⏭ Перевод на курс» ЗДЕСЬ и породил всю пачку
// расхождений «курс группы по расписанию vs по плану» — им один раз воспользовались
// раньше времени, термин в конфиге разошёлся с реальной датой, и это не было видно
// нигде в интерфейсе. Термин ТЕПЕРЬ считается ИСКЛЮЧИТЕЛЬНО по дате сервера
// (server/app/db.py::default_term, граница — 1 сентября), кнопка ручного перевода
// убрана из интерфейса совсем — здесь остаётся только пояснительная надпись, какой
// период сейчас действует, без возможности его сдвинуть отсюда.
const currentTerm = ref(null)
// Сезон/период — общая помощь для всех мест, где семестр показывается человеку
// (бейдж периода, окно часов, сводка импорта ВСГУТУ) — раньше форма собиралась
// инлайн-тернарником в каждом месте по отдельности.
function seasonLabel(semester) {
  return semester === 1 ? locale.t('adminGroups.seasonFall', 'осенний') : locale.t('adminGroups.seasonSpring', 'весенний')
}
function termLabel(t) { return t ? locale.t('adminGroups.termLabel', { year: t.year, semester: seasonLabel(t.semester) }) : '' }
async function loadTerm() {
  try { currentTerm.value = (await termsApi.list()).data.current } catch { /* */ }
}

async function reload() {
  loading.value = true
  try { rows.value = (await adminApi.groups()).data.groups || [] } catch { rows.value = [] } finally { loading.value = false }
}
onMounted(async () => {
  await reload()
  await loadTerm()
  await loadCategories()
  // Курсы грузим СРАЗУ, не дожидаясь выбора категории: иначе сортировка появляется
  // только у того, кто наугад нажал «Колледж», — а для остальных её как бы нет.
  await loadByCourse()
  try { allSubjects.value = (await adminApi.subjects()).data.subjects?.map((s) => s.name) || [] } catch { /* */ }
  try { parsedGroups.value = (await scheduleApi.groups()).data.groups || [] } catch { /* */ }
  try { allTeachers.value = (await adminApi.teachers()).data.teachers || [] } catch { /* */ }
})
// Преподаватели, у которых ЕСТЬ данный предмет (Влад: «нажали математику — все преподы,
// у которых указана математика в предметах») — фильтр на клиенте, без нового запроса.
function teachersFor(subject) {
  return allTeachers.value.filter((t) => (t.subjects || []).includes(subject))
}

const showForm = ref(false)
const editing = ref(null)
// 🎓 КУРС И ГОД ПОСТУПЛЕНИЯ — ОДНО ЧИСЛО В ДВУХ ВИДАХ (06.09.2026, требование Влада).
// Администратор знает КУРС, системе нужен ГОД ПОСТУПЛЕНИЯ: от него считаются курс,
// семестр, ЗЕТ и ключи часов. Поэтому поля два, но они связаны — правишь одно, второе
// пересчитывается. Формула на сервере (`study_hours.enrollment_year_for_course`); здесь
// та же арифметика ради мгновенного отклика, а решает всё равно сервер.
const form = ref({ name: '', subjects: [], category: 'college', course: 1, enrollment_year: null, specialty_code: '' })
// Учебный год берём из УЖЕ загружаемого термина (`loadTerm` выше): год поступления
// считается от него, а не от календарной даты браузера — границу 1 сентября продукт
// двигал уже трижды, и вторая её копия здесь разошлась бы молча.
const termYear = computed(() => currentTerm.value?.year || '')
const termStartYear = computed(() => Number(String(termYear.value).split('/')[0]) || new Date().getFullYear())
function yearFromCourse(c) { return termStartYear.value - (Number(c) - 1) }
function courseFromYear(y) { return termStartYear.value - Number(y) + 1 }
function onCourseInput(v) {
  form.value.course = Number(v) || 1
  form.value.enrollment_year = yearFromCourse(form.value.course)
}
function onYearInput(v) {
  form.value.enrollment_year = Number(v) || null
  if (form.value.enrollment_year) form.value.course = courseFromYear(form.value.enrollment_year)
}
const saving = ref(false)
const formError = ref('')
const importing = ref(false)

function openCreate() {
  editing.value = null
  form.value = { name: '', subjects: [], category: 'college', course: 1,
                 enrollment_year: yearFromCourse(1), specialty_code: '' }
  formError.value = ''; showForm.value = true
}
function openEdit(g) {
  editing.value = g.name
  form.value = { name: g.name, subjects: [...(g.subjects || [])], category: g.category || 'college',
                 enrollment_year: g.enrollment_year || null,
                 course: g.enrollment_year ? courseFromYear(g.enrollment_year) : 1,
                 specialty_code: g.specialty_code || '' }
  formError.value = ''; showForm.value = true
}
function toggleSubject(s) {
  const i = form.value.subjects.indexOf(s)
  if (i >= 0) form.value.subjects.splice(i, 1)
  else form.value.subjects.push(s)
}
function clearSubjects() { form.value.subjects = [] }
async function save() {
  const f = form.value
  if (!f.name.trim()) { formError.value = locale.t('adminGroups.enterName', 'Введите название группы'); return }
  saving.value = true; formError.value = ''
  try {
    if (editing.value) await adminApi.updateGroup(editing.value, { subjects: f.subjects, category: f.category })
    else {
      const { data } = await adminApi.createGroup({
        name: f.name.trim(), subjects: f.subjects, category: f.category,
        course: f.course, enrollment_year: f.enrollment_year,
        specialty_code: (f.specialty_code || '').trim(),
      })
      //⚠️ Отказ учебного плана ПОКАЗЫВАЕМ, но группу не теряем: она уже создана, и
      //молчание оставило бы админа с пустым списком предметов без объяснения причины.
      if (data?.plan_error) toast.error(data.plan_error)
      else if (data?.subjects_added) {
        toast.success(locale.t('adminGroups.planPulled', { n: data.subjects_added }))
      }
    }
    showForm.value = false; await reload()
  } catch (e) { formError.value = e?.response?.data?.detail || locale.t('adminGroups.saveFailed', 'Не удалось сохранить') }
  finally { saving.value = false }
}
async function del(g) {
  if (!(await confirm({ title: locale.t('adminGroups.confirmDeleteTitle', { name: g.name }), okText: locale.t('common.delete'), danger: true }))) return
  try { await adminApi.deleteGroup(g.name); await reload() }
  catch (e) { toast.error(e?.response?.data?.detail || locale.t('adminGroups.deleteFailed', 'Не удалось удалить')) }
}
// ── Учебные часы группы («пройдено X из Y») ─────────────────────────────────────
// Часы задаются на СЕМЕСТР и по КАЖДОМУ предмету, поэтому это отдельное окно, а не поле
// в форме группы. Сохраняем пачкой одним запросом: одно нажатие «Сохранить» не должно
// превращаться в полтора десятка запросов с половинчатым результатом при обрыве связи.
const showHours = ref(false)
const hoursGroup = ref('')
const hoursRows = ref([])          // [{subject, hours_total, hours_done}]
const hoursTerm = ref(null)
const hoursLoading = ref(false)
const hoursSaving = ref(false)

//Приглашения в группу: ссылка вместо «заведи тридцать человек руками».
const inviteGroup = ref('')

async function openHours(g) {
  hoursGroup.value = g.name
  hoursRows.value = []
  showHours.value = true
  hoursLoading.value = true
  try {
    const r = (await adminApi.groupHours(g.name)).data
    hoursRows.value = r.subjects || []
    hoursTerm.value = r.term || null
  } catch (e) {
    toast.error(e?.response?.data?.detail || locale.t('adminGroups.hoursLoadFailed', 'Не удалось загрузить часы'))
    showHours.value = false
  } finally { hoursLoading.value = false }
}

async function saveHours() {
  hoursSaving.value = true
  try {
    const hours = {}
    const teachers = {}
    const teachers2 = {}
    const zet = {}
    hoursRows.value.forEach((r) => {
      hours[r.subject] = Number(r.hours_total) || 0
      teachers[r.subject] = r.teacher_id || ''
      //Второй преподаватель (раздельное обучение, §ролей 3.6.1) — шлём, ТОЛЬКО если
      //куратор уже поставил галочку на предмете (иначе сервер отклонит: 400).
      if (r.split) teachers2[r.subject] = r.teacher_id_2 || ''
      //ЗЕТ: пусто/не число — снять (null), НЕ подставлять zet_hint автоматически
      //(docs/done/PLAN-ZET.md §10 — подсказка, а не источник правды).
      const zv = r.zet
      zet[r.subject] = (zv === '' || zv === null || zv === undefined || Number.isNaN(Number(zv)))
        ? null : Number(zv)
    })
    await adminApi.saveGroupHours(hoursGroup.value, hours, teachers, teachers2, zet)
    toast.success(locale.t('adminGroups.hoursSaved', 'Часы сохранены'))
    showHours.value = false
  } catch (e) { toast.error(e?.response?.data?.detail || locale.t('adminGroups.hoursSaveFailed', 'Не удалось сохранить')) }
  finally { hoursSaving.value = false }
}

// ── Импорт специальности/учебного плана ВСГУТУ (parsers/esstu_parser.py) ────────
// Отдельно от «Из расписания» выше: тот берёт ТОЛЬКО названия предметов с портала
// расписания, а это — целиком специальность+год поступления → часы/ЗЕТ по каждому
// предмету ИМЕННО текущего курса/семестра (курс считается от года поступления,
// см. серверный study_hours.course_and_semester — счётчик от даты, а не хранимое
// число, чтобы через год не protaskaть группу вручную).
const showImport = ref(false)
const importGroup = ref('')
const importSpecialties = ref([])
const importLoadingList = ref(false)
const importForm = ref({ specialty_code: '', enrollment_year: '' })
const importYears = ref([])          // реальные годы набора с опубликованным планом
const importLoadingYears = ref(false)
const importSaving = ref(false)
const importError = ref('')
const importResult = ref(null)   // {course, semester, imported, unmapped, saved_hours}

// Год поступления — ВЫБОР из реально опубликованных на сайте планов, а не
// ручной ввод числа (админ почти всегда промахивался мимо доступных лет —
// на сайте обычно только 3-5 последних, не с основания колледжа). Список
// зависит от специальности — перегружаем при каждой её смене.
watch(() => importForm.value.specialty_code, async (code) => {
  importYears.value = []
  importForm.value.enrollment_year = ''
  if (!code) return
  importLoadingYears.value = true
  try {
    const r = (await adminApi.esstuPlanYears(code)).data
    importYears.value = r.years || []
    if (importYears.value.length) importForm.value.enrollment_year = importYears.value[0]
  } catch { /* остаётся пустым — форма покажет «нет доступных годов» */ }
  finally { importLoadingYears.value = false }
})

async function openImport(g) {
  importGroup.value = g.name
  importResult.value = null
  importError.value = ''
  importYears.value = []
  importForm.value = { specialty_code: '', enrollment_year: '' }
  showImport.value = true
  importLoadingList.value = true
  try {
    const r = (await adminApi.esstuSpecialties(g.name)).data
    importSpecialties.value = r.specialties || []
    if (r.suggested_code) importForm.value.specialty_code = r.suggested_code   // будит watch выше
  } catch (e) {
    importError.value = e?.response?.data?.detail || locale.t('adminGroups.specialtiesLoadFailed', 'Не удалось получить список специальностей')
  } finally { importLoadingList.value = false }
}

async function runImport() {
  const f = importForm.value
  if (!f.specialty_code || !f.enrollment_year) {
    importError.value = locale.t('adminGroups.selectSpecialtyAndYear', 'Выберите специальность и год поступления')
    return
  }
  importSaving.value = true; importError.value = ''; importResult.value = null
  try {
    const r = (await adminApi.importEsstu(importGroup.value, f.specialty_code, Number(f.enrollment_year))).data
    importResult.value = r
    await reload()
  } catch (e) { importError.value = e?.response?.data?.detail || locale.t('adminGroups.importPlanFailed', 'Не удалось импортировать план') }
  finally { importSaving.value = false }
}

// ── Импорт группы по категории расписания (Бакалавриат/Заочное 1/2) ─────────────
// Отдельно от ESSTU-импорта выше (тот — предметы+часы+ЗЕТ для УЖЕ существующей
// колледж-группы) и от «Из расписания» ниже (тот — тоже только колледж). Здесь —
// ЗАВОДИМ саму группу как каталожную запись, связанную с расписанием, без
// предметов/часов/журнала (у небюджетных категорий их нет и не будет).
const showScheduleImport = ref(false)
const showTeacherSuggest = ref(false)
const scheduleImportCategory = ref('')
const scheduleImportGroups = ref([])
const scheduleImportGroupName = ref('')
const scheduleImportLoadingGroups = ref(false)
const scheduleImportSaving = ref(false)
const scheduleImportError = ref('')

watch(scheduleImportCategory, async (key) => {
  scheduleImportGroups.value = []
  scheduleImportGroupName.value = ''
  if (!key) return
  scheduleImportLoadingGroups.value = true
  try {
    const groups = (await scheduleApi.groups(key)).data.groups || []
    //Категорию могли переключить ЕЩЁ РАЗ, пока этот запрос летел (клик по нескольким
    //кнопкам подряд) — тогда ответ устарел и не про ТЕКУЩИЙ выбор. Без проверки более
    //медленный (например, Заочное 1 с меньшим списком) ответ мог прилететь ПОСЛЕ более
    //быстрого и молча подменить список группами не той категории (реальный баг, живьём
    //выглядел как «импорт в Заочное 2 берёт группу из Заочное 1»).
    if (key !== scheduleImportCategory.value) return
    scheduleImportGroups.value = groups
  } catch {
    if (key === scheduleImportCategory.value) scheduleImportGroups.value = []
  } finally {
    if (key === scheduleImportCategory.value) scheduleImportLoadingGroups.value = false
  }
})

function openScheduleImport() {
  scheduleImportError.value = ''
  scheduleImportSaving.value = false
  showScheduleImport.value = true
  const key = nonCollegeCategories.value[0]?.key || ''
  if (key === scheduleImportCategory.value) scheduleImportCategory.value = ''   // форсируем watch
  scheduleImportCategory.value = key
}

//Сентинел для пункта «Все» в списке групп — не настоящее имя группы, отдельная ветка
//в runScheduleImport ниже. Список групп категории — 90-220 штук, кликать по одной
//неразумно (Влад: «нужен импорт всего»).
const SCHEDULE_IMPORT_ALL = '__all__'

async function runScheduleImport() {
  if (!scheduleImportCategory.value || !scheduleImportGroupName.value) {
    scheduleImportError.value = locale.t('adminGroups.selectCategoryAndGroup', 'Выберите категорию и группу')
    return
  }
  if (scheduleImportGroupName.value === SCHEDULE_IMPORT_ALL) {
    await runScheduleImportAll()
    return
  }
  scheduleImportSaving.value = true; scheduleImportError.value = ''
  try {
    await adminApi.importScheduleCategory(scheduleImportCategory.value, scheduleImportGroupName.value)
    toast.success(locale.t('adminGroups.groupAdded', { name: scheduleImportGroupName.value }))
    showScheduleImport.value = false
    await reload()
  } catch (e) { scheduleImportError.value = e?.response?.data?.detail || locale.t('adminGroups.scheduleImportFailed', 'Не удалось импортировать') }
  finally { scheduleImportSaving.value = false }
}

//Полный снимок категории строится на сервере ЛЕНИВО и В ФОНЕ (десятки-сотни страниц
//портала, ~минута на первый запрос — тот же приём, что у расписания преподавателя):
//пока не готов, сервер отвечает building=true, и мы САМИ повторяем запрос через 5с,
//вместо того чтобы заставлять админа жать кнопку вручную несколько раз подряд.
//scheduleImportSaving держим true ВСЁ ЭТО ВРЕМЯ (до готового результата/ошибки) —
//поэтому сбрасываем его явно в КАЖДОЙ конечной ветке, а не одним finally для всех.
async function runScheduleImportAll() {
  const category = scheduleImportCategory.value
  scheduleImportSaving.value = true; scheduleImportError.value = ''
  let result
  try {
    result = (await adminApi.importScheduleCategoryAll(category)).data
  } catch (e) {
    scheduleImportError.value = e?.response?.data?.detail || locale.t('adminGroups.scheduleImportFailed', 'Не удалось импортировать')
    scheduleImportSaving.value = false
    return
  }
  if (result.building) {
    toast.info(locale.t('adminGroups.buildingCategorySchedule', 'Собираю расписание категории на сервере (~минута)…'))
    //Категорию могли сменить ИЛИ диалог закрыть (Отмена), пока снимок собирался —
    //тогда повторный опрос уже не нужен и не ожидается пользователем.
    setTimeout(() => {
      if (showScheduleImport.value && scheduleImportCategory.value === category) runScheduleImportAll()
    }, 5000)
    return   //scheduleImportSaving остаётся true — кнопка «Импортировать» ждёт результата
  }
  scheduleImportSaving.value = false
  toast.success(locale.t('adminGroups.scheduleImportAllDone', { imported: result.imported, skipped: result.skipped, total: result.total }))
  showScheduleImport.value = false
  await reload()
}

// «Из расписания» — привязывает к каждой группе предметы ИЗ её расписания (портал
// ВСГУТУ) и пополняет каталог. Снимок строится на сервере лениво (~минута): если он
// ещё готовится — просим нажать позже.
async function importParsed() {
  importing.value = true
  try {
    const r = (await adminApi.bindSubjects()).data
    if (!r.ok && r.building) {
      toast.info(locale.t('adminGroups.scheduleIndexBuilding', 'Индекс расписания готовится на сервере (~минута). Нажми «🏫 Из расписания» ещё раз чуть позже.'))
    } else {
      const suffix = r.building ? locale.t('adminGroups.bindSubjectsBuildingSuffix', ' (индекс ещё дообновляется — можно повторить для полноты)') : ''
      toast.success(locale.t('adminGroups.bindSubjectsDone', { bound: r.bound, subjects: r.subjects }) + suffix)
      // Группы, которых расписание больше не знает (выпустились, переименовались).
      // Сервер их НЕ удаляет намеренно: за ними живые студенты и оценки, а пропасть
      // группа может и от сбоя портала. Но копиться молча они тоже не должны — иначе
      // выпущенные курсы годами висят в каждом выпадающем списке.
      const stale = r.stale_groups || []
      if (stale.length) {
        const head = stale.slice(0, 6)
          .map((g) => (g.students ? `${g.name} (${g.students})` : g.name)).join(', ')
        const tail = stale.length > head.split(', ').length ? '…' : ''
        toast.info(locale.t('adminGroups.staleGroups', { n: stale.length, list: head + tail }))
      }
      await reload()
      try { allSubjects.value = (await adminApi.subjects()).data.subjects?.map((s) => s.name) || [] } catch { /* */ }
    }
  } catch (e) { toast.error(e?.response?.data?.detail || locale.t('adminGroups.actionFailed', 'Не удалось выполнить')) }
  finally { importing.value = false }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-3">
      <div v-if="currentTerm" class="mr-auto flex items-center gap-2 text-sm">
        <span class="text-text3">{{ locale.t('adminGroups.currentTermLabel', 'Учебный период:') }}</span>
        <Badge variant="green">{{ termLabel(currentTerm) }}</Badge>
      </div>
      <!-- ОДНА кнопка «+» вместо четырёх в ряд (просьба Влада 03.09.2026). Панель из
           четырёх подписей читается как четыре равнозначных действия, хотя обычное из
           них ровно одно — завести группу. Остальные три редкие и связаны с порталом.
           ⚠️ Список закрывается по клику мимо и по Esc: без этого на телефоне он
           остаётся висеть, потому что «мимо» там некуда нажать пальцем случайно. -->
      <div class="relative">
        <AppButton variant="green" size="sm" @click="addMenu = !addMenu">
          {{ locale.t('adminGroups.addBtn', '+ Добавить') }}
        </AppButton>
        <div v-if="addMenu"
             class="absolute right-0 z-20 mt-1 w-64 overflow-hidden rounded-lg border shadow-lg"
             style="background: var(--gb-surface); border-color: var(--gb-border)">
          <button class="w-full px-3 py-2 text-left text-sm hover:opacity-80"
                  style="color: var(--gb-text)" @click="runAddAction(openCreate)">
            {{ locale.t('adminGroups.addOne', 'Новая группа') }}
          </button>
          <button class="w-full px-3 py-2 text-left text-sm hover:opacity-80"
                  style="color: var(--gb-text)" :disabled="importing"
                  @click="runAddAction(importParsed)">
            {{ importing ? locale.t('adminGroups.updatingBtn', 'Обновление…')
                         : locale.t('adminGroups.updateGroupsBtn', 'Обновить группы') }}
          </button>
          <button class="w-full px-3 py-2 text-left text-sm hover:opacity-80"
                  style="color: var(--gb-text)" @click="runAddAction(openScheduleImport)">
            {{ locale.t('adminGroups.importByCategoryBtn', '🌐 Импорт по категории') }}
          </button>
          <!-- Расписание знает, КТО ведёт пару, а у нас эта связь велась руками: смена
               расписания меняла предметы группы и оставляла преподавателя без журнала. -->
          <button class="w-full px-3 py-2 text-left text-sm hover:opacity-80"
                  style="color: var(--gb-text)"
                  @click="runAddAction(() => { showTeacherSuggest = true })">
            {{ locale.t('adminGroups.teacherSuggestBtn', '👤 Кто ведёт предметы') }}
          </button>
        </div>
      </div>
    </div>
    <!-- Заслонка клика мимо. Прозрачная и БЕЗ затемнения: это меню, а не модалка. -->
    <div v-if="addMenu" class="fixed inset-0 z-10" @click="addMenu = false"></div>

    <!-- Кнопки-категории (та же идея, что в «Расписании») — фильтр таблицы ниже. -->
    <div v-if="categories.length > 1" class="flex flex-wrap gap-2">
      <button class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
              :class="!categoryFilter ? 'border-accent bg-accent text-white' : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
              @click="categoryFilter = ''">{{ locale.t('adminGroups.allCategories', 'Все категории') }}</button>
      <button v-for="c in categories" :key="c.key"
              class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
              :class="categoryFilter === c.key ? 'border-accent bg-accent text-white' : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
              @click="categoryFilter = c.key">{{ c.label }}</button>
    </div>

    <!-- Кнопки-курсы — ВНУТРИ выбранной категории (3.5.5). Число курсов не
         фиксировано (на живых данных бакалавриата/заочного бывает 5-6). -->
    <div v-if="courseKeys.length > 1" class="flex flex-wrap gap-2">
      <button class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
              :class="!courseFilter ? 'border-accent bg-accent text-white' : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
              @click="courseFilter = ''">{{ locale.t('adminGroups.allCourses', 'Все курсы') }}</button>
      <button v-for="c in courseKeys" :key="c"
              class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
              :class="courseFilter === c ? 'border-accent bg-accent text-white' : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
              @click="courseFilter = c">{{ locale.t('adminGroups.courseN', { n: c }) }}</button>
    </div>

    <div class="overflow-x-auto rounded-lg border border-border bg-card shadow-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border2 bg-bg2 text-left text-tiny uppercase tracking-wide text-text2">
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminGroups.colGroup', 'Группа') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminGroups.colCategory', 'Категория') }}</th>
            <th class="px-4 py-2.5 text-right font-semibold">{{ locale.t('adminGroups.colStudents', 'Студентов') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminGroups.colSubjects', 'Предметы') }}</th>
            <th class="px-4 py-2.5 text-right font-semibold">{{ locale.t('adminGroups.colActions', 'Действия') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="5" class="px-4 py-6 text-center text-text3">{{ locale.t('common.loading') }}</td></tr>
          <tr v-else-if="!filteredRows.length"><td colspan="5" class="px-4 py-6 text-center text-text3">{{ locale.t('adminGroups.noGroups', 'Групп нет') }}</td></tr>
          <tr v-for="(g, i) in filteredRows" :key="i" class="border-b border-border last:border-0 hover:bg-bg2/60">
            <td class="px-4 py-2.5 font-semibold text-text">{{ g.name }}</td>
            <td class="px-4 py-2.5"><Badge variant="muted">{{ categoryLabel(g.category || 'college') }}</Badge></td>
            <td class="px-4 py-2.5 text-right text-text2">{{ g.students }}</td>
            <td class="px-4 py-2.5">
              <div class="flex flex-wrap gap-1.5">
                <Badge v-for="s in (g.subjects || []).slice(0, 4)" :key="s" variant="muted">{{ s }}</Badge>
                <span v-if="(g.subjects?.length || 0) > 4" class="text-xs text-text3">+{{ g.subjects.length - 4 }}</span>
                <span v-if="!g.subjects?.length" class="text-text3">—</span>
              </div>
            </td>
            <td class="whitespace-nowrap px-4 py-2.5 text-right">
              <button class="mr-3 text-text3 hover:text-accent" :title="locale.t('adminGroups.importEsstuTitle', 'Импорт специальности/плана с сайта ВСГУТУ')" @click="openImport(g)">🎓</button>
              <button class="mr-3 text-text3 hover:text-accent" :title="locale.t('adminGroups.hoursTitle', 'Учебные часы по предметам')" @click="openHours(g)">🕐</button>
              <button class="mr-3 text-text3 hover:text-accent" :title="locale.t('adminGroups.inviteTitle', 'Пригласить студентов ссылкой')" @click="inviteGroup = g.name">✉</button>
              <button class="mr-3 text-text3 hover:text-accent" :title="locale.t('adminGroups.editBtnTitle', 'Изменить')" @click="openEdit(g)">✎</button>
              <button class="text-text3 hover:text-red" :title="locale.t('common.delete')" @click="del(g)">✕</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- ── Учебные часы группы ─────────────────────────────────────────────────── -->
    <div v-if="showHours" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="showHours = false">
      <div class="w-full max-w-lg rounded-lg border border-border bg-card p-5 shadow-card">
        <h3 class="font-title text-lg font-bold text-text">{{ locale.t('adminGroups.hoursModalTitle', { group: hoursGroup }) }}</h3>
        <p class="mb-4 mt-1 text-xs text-text3">
          {{ locale.t('adminGroups.hoursExplain', 'Часы на семестр по каждому предмету. Пройденное считается по лекциям и практикам (одно занятие — 2 академических часа); домашние задания в часы не входят.') }}
          <span v-if="hoursTerm"> {{ locale.t('adminGroups.hoursPeriodLabel', { year: hoursTerm.year, season: seasonLabel(hoursTerm.semester) }) }}</span>
        </p>

        <p v-if="hoursLoading" class="py-6 text-center text-sm text-text3">{{ locale.t('common.loading') }}</p>
        <p v-else-if="!hoursRows.length" class="py-6 text-center text-sm text-text3">
          {{ locale.t('adminGroups.hoursNoSubjects', 'У группы нет предметов — сначала задайте их кнопкой ✎.') }}
        </p>
        <div v-else class="max-h-80 space-y-2 overflow-y-auto">
          <div v-for="r in hoursRows" :key="r.subject"
               class="rounded-sm border border-border2 px-2 py-1.5 hover:bg-bg2">
            <div class="flex items-center gap-3">
              <span class="min-w-0 flex-1 truncate text-sm text-text" :title="r.subject">{{ r.subject }}</span>
              <span class="shrink-0 text-xs text-text3">{{ locale.t('adminGroups.hoursDoneLabel', { n: r.hours_done }) }}</span>
              <input v-model.number="r.hours_total" type="number" min="0" step="2"
                     class="h-9 w-24 shrink-0 rounded-sm border border-border2 bg-card2 px-2 text-right text-sm text-text outline-none focus:border-accent" />
            </div>
            <!-- ЗЕТ (docs/done/PLAN-ZET.md): подсказка zet_hint — СЕРАЯ, только placeholder,
                 автоматом никогда не сохраняется, пока администратор явно не впишет число. -->
            <div class="mt-1.5 flex items-center gap-2">
              <span class="shrink-0 text-xs text-text3">{{ locale.t('adminGroups.zetLabel', 'ЗЕТ') }}</span>
              <input v-model="r.zet" type="number" min="0" step="0.1"
                     :placeholder="r.zet_hint ? String(r.zet_hint) : ''"
                     class="h-8 w-20 shrink-0 rounded-sm border border-border2 bg-card2 px-2 text-right text-xs text-text outline-none placeholder:text-text3 focus:border-accent" />
              <span v-if="r.zet_hint" class="text-[11px] text-text3">{{ locale.t('adminGroups.zetHintFormula', { hint: r.zet_hint }) }}</span>
            </div>
            <!-- §ролей: препод, ведущий эту группу по этому предмету — единственный
                 источник правды «какие группы видит препод» (webdata.teacher_assignments). -->
            <select v-model="r.teacher_id"
                    class="mt-1.5 h-8 w-full rounded-sm border border-border2 bg-card2 px-2 text-xs text-text2 outline-none focus:border-accent">
              <option value="">{{ locale.t('adminGroups.teacherNotAssigned', '— преподаватель не назначен —') }}</option>
              <option v-for="t in teachersFor(r.subject)" :key="t.id" :value="t.id">{{ t.name }}</option>
            </select>
            <p v-if="!teachersFor(r.subject).length" class="mt-1 text-[11px] text-text3">
              {{ locale.t('adminGroups.noTeachersForSubject', { subject: r.subject }) }}
            </p>
            <!-- Раздельное обучение (§ролей, 3.6.1) — флаг ставит куратор во вкладке
                 «Курирование», здесь только читаем его и, если он стоит, даём занять
                 ВТОРОГО преподавателя (подгруппа 2). Пусто — обе подгруппы у teacher_id. -->
            <template v-if="r.split">
              <div class="mt-1.5 flex items-center gap-1.5 text-[11px] text-accent">
                <span>👥</span><span>{{ locale.t('adminGroups.splitBadge', 'раздельное обучение') }}</span>
              </div>
              <select v-model="r.teacher_id_2"
                      class="mt-1 h-8 w-full rounded-sm border border-border2 bg-card2 px-2 text-xs text-text2 outline-none focus:border-accent">
                <option value="">{{ locale.t('adminGroups.subgroup2NotAssigned', '— подгруппа 2: тот же преподаватель —') }}</option>
                <option v-for="t in teachersFor(r.subject)" :key="t.id" :value="t.id">{{ t.name }}</option>
              </select>
            </template>
          </div>
        </div>

        <div class="mt-5 flex justify-end gap-2">
          <AppButton variant="ghost" size="sm" @click="showHours = false">{{ locale.t('common.cancel') }}</AppButton>
          <AppButton variant="green" size="sm" :disabled="hoursSaving || hoursLoading || !hoursRows.length" @click="saveHours">
            {{ hoursSaving ? locale.t('adminGroups.savingBtn', 'Сохранение…') : locale.t('common.save') }}
          </AppButton>
        </div>
      </div>
    </div>

    <!-- ── Импорт специальности/учебного плана ВСГУТУ ─────────────────────────────── -->
    <div v-if="showImport" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="showImport = false">
      <div class="w-full max-w-md rounded-lg border border-border bg-card p-5 shadow-card">
        <h3 class="font-title text-lg font-bold text-text">{{ locale.t('adminGroups.importEsstuModalTitle', { group: importGroup }) }}</h3>
        <p class="mb-4 mt-1 text-xs text-text3">
          {{ locale.t('adminGroups.importEsstuExplain', 'Подтягивает учебный план специальности и заносит в группу предметы+часы+ЗЕТ ИМЕННО текущего курса/семестра (считается от года поступления). Список предметов группы будет ЗАМЕНЁН — как при обновлении «Из расписания».') }}
        </p>

        <template v-if="!importResult">
          <p v-if="importLoadingList" class="py-6 text-center text-sm text-text3">{{ locale.t('adminGroups.loadingSpecialties', 'Загрузка специальностей…') }}</p>
          <div v-else class="space-y-3">
            <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminGroups.specialtyLabel', 'Специальность') }}</span>
              <select v-model="importForm.specialty_code"
                      class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent">
                <option value="">{{ locale.t('adminGroups.selectPlaceholder', '— выберите —') }}</option>
                <option v-for="s in importSpecialties" :key="s.code" :value="s.code">{{ s.code }} — {{ s.name }}</option>
              </select>
              <p v-if="!importSpecialties.length" class="mt-1 text-xs text-red">
                {{ locale.t('adminGroups.specialtiesUnavailable', 'Список специальностей недоступен (сайт ВСГУТУ не ответил) — попробуйте позже.') }}
              </p>
            </label>
            <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminGroups.enrollmentYearLabel', 'Год поступления') }}</span>
              <select v-model.number="importForm.enrollment_year" :disabled="!importForm.specialty_code || importLoadingYears"
                      class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent disabled:opacity-50">
                <option value="">{{ importLoadingYears ? locale.t('adminGroups.loadingPlaceholder', '— загрузка… —') : locale.t('adminGroups.selectPlaceholder', '— выберите —') }}</option>
                <option v-for="y in importYears" :key="y" :value="y">{{ y }}</option>
              </select>
              <p v-if="importForm.specialty_code && !importLoadingYears && !importYears.length"
                 class="mt-1 text-xs text-red">
                {{ locale.t('adminGroups.noPlansForSpecialty', 'Для этой специальности на сайте нет опубликованных планов.') }}
              </p>
            </label>
            <p v-if="importError" class="text-sm text-red">{{ importError }}</p>
          </div>
        </template>

        <!-- Сводка после успешного импорта — что именно подтянулось. -->
        <div v-else class="space-y-2 text-sm">
          <p class="text-text">{{ locale.t('adminGroups.importResultCourseText', 'Курс') }} <b>{{ importResult.course }}</b>,
            {{ locale.t('adminGroups.importResultSemesterText', 'семестр с начала обучения') }}
            <b>{{ importResult.semester }}</b> {{ locale.t('adminGroups.currentPeriodParenthetical', { year: importResult.term.year, season: seasonLabel(importResult.term.semester) }) }}.</p>
          <p class="text-text2">{{ locale.t('adminGroups.importResultSubjectsCountText', 'Предметов с часами этого семестра:') }} <b>{{ importResult.imported.length }}</b></p>
          <div v-if="importResult.imported.length" class="flex flex-wrap gap-1.5">
            <Badge v-for="s in importResult.imported" :key="s" variant="green">{{ s }}</Badge>
          </div>
          <template v-if="importResult.unmapped.length">
            <p class="pt-1 text-text2">{{ locale.t('adminGroups.importResultUnmappedText', 'Добавлены без часов (не удалось определить семестр в плане — задайте часы вручную кнопкой 🕐):') }}</p>
            <div class="flex flex-wrap gap-1.5">
              <Badge v-for="s in importResult.unmapped" :key="s" variant="muted">{{ s }}</Badge>
            </div>
          </template>
        </div>

        <div class="mt-5 flex justify-end gap-2">
          <AppButton variant="ghost" size="sm" @click="showImport = false">{{ importResult ? locale.t('common.close') : locale.t('common.cancel') }}</AppButton>
          <AppButton v-if="!importResult" variant="green" size="sm"
                    :disabled="importSaving || importLoadingList || !importSpecialties.length" @click="runImport">
            {{ importSaving ? locale.t('adminGroups.importingBtn', 'Импорт…') : locale.t('adminGroups.importBtn', 'Импортировать') }}
          </AppButton>
        </div>
      </div>
    </div>

    <!-- ── Импорт группы по категории расписания ──────────────────────────────────── -->
    <div v-if="showScheduleImport" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="showScheduleImport = false">
      <div class="w-full max-w-md rounded-lg border border-border bg-card p-5 shadow-card">
        <h3 class="font-title text-lg font-bold text-text">{{ locale.t('adminGroups.scheduleImportModalTitle', 'Импорт группы по категории') }}</h3>
        <p class="mb-4 mt-1 text-xs text-text3">
          {{ locale.t('adminGroups.scheduleImportExplain', 'Заводит группу как каталожную запись, связанную с расписанием портала. Предметы подставятся из её расписания; часов/учебного плана/журнала для этой категории нет (для колледжа их даёт «Добавить группу» / «Обновить группы»).') }}
        </p>
        <div class="space-y-3">
          <!-- 🎓 Курс и год поступления — одно число в двух видах: правишь одно, второе
               пересчитывается. Хранится ГОД (курс растёт сам по календарю), но вводить
               удобнее КУРС — его администратор знает про группу сразу. -->
          <div class="grid grid-cols-2 gap-2">
            <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminGroups.courseLabel', 'Курс') }}</span>
              <input type="number" min="1" max="6" :value="form.course" :disabled="!!editing"
                     @input="onCourseInput($event.target.value)"
                     class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent disabled:opacity-60" />
            </label>
            <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminGroups.enrollmentYearLabel', 'Год поступления') }}</span>
              <input type="number" min="2000" max="2100" :value="form.enrollment_year" :disabled="!!editing"
                     @input="onYearInput($event.target.value)"
                     class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent disabled:opacity-60" />
            </label>
          </div>
          <!-- 📚 Программа обучения подтягивается САМА по коду специальности из учебного
               плана ВСГУТУ. Поле необязательное: без него группа заводится как раньше, с
               ручным списком предметов ниже. -->
          <label v-if="!editing" class="block">
            <span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminGroups.specialtyLabel', 'Код специальности') }}</span>
            <input v-model="form.specialty_code" placeholder="09.02.07"
                   class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" />
            <span class="mt-1 block text-tiny text-text3">{{ locale.t('adminGroups.specialtyHint', 'Заполните — предметы и часы подтянутся из учебного плана') }}</span>
          </label>
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminGroups.categoryLabel', 'Категория') }}</span>
            <select v-model="scheduleImportCategory"
                    class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent">
              <option v-for="c in nonCollegeCategories" :key="c.key" :value="c.key">{{ c.label }}</option>
            </select>
          </label>
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminGroups.groupFromPortalLabel', 'Группа (с портала)') }}</span>
            <select v-model="scheduleImportGroupName" :disabled="scheduleImportLoadingGroups || !scheduleImportGroups.length"
                    class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent disabled:opacity-50">
              <option value="">{{ scheduleImportLoadingGroups ? locale.t('adminGroups.loadingPlaceholder', '— загрузка… —') : locale.t('adminGroups.selectPlaceholder', '— выберите —') }}</option>
              <option v-if="scheduleImportGroups.length" :value="SCHEDULE_IMPORT_ALL">
                {{ locale.t('adminGroups.allGroupsOption', { n: scheduleImportGroups.length }) }}
              </option>
              <option v-for="n in scheduleImportGroups" :key="n" :value="n">{{ n }}</option>
            </select>
            <p v-if="!scheduleImportLoadingGroups && scheduleImportCategory && !scheduleImportGroups.length"
               class="mt-1 text-xs text-red">
              {{ locale.t('adminGroups.noPortalDataForCategory', 'Нет данных с портала для этой категории (сайт недоступен либо снимок ещё не собран).') }}
            </p>
            <p v-if="scheduleImportGroupName === SCHEDULE_IMPORT_ALL" class="mt-1 text-xs text-text3">
              {{ locale.t('adminGroups.scheduleImportAllHint', 'Заведёт каталожные записи для ВСЕХ групп категории разом (может занять минуту на первый снимок — уже существующие группы не трогает).') }}
            </p>
          </label>
          <p v-if="scheduleImportError" class="text-sm text-red">{{ scheduleImportError }}</p>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <AppButton variant="ghost" size="sm" @click="showScheduleImport = false">{{ locale.t('common.cancel') }}</AppButton>
          <AppButton variant="green" size="sm"
                    :disabled="scheduleImportSaving || !scheduleImportGroupName" @click="runScheduleImport">
            {{ scheduleImportSaving ? locale.t('adminGroups.importingBtn', 'Импорт…') : locale.t('adminGroups.importBtn', 'Импортировать') }}
          </AppButton>
        </div>
      </div>
    </div>

    <div v-if="showForm" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="showForm = false">
      <div class="w-full max-w-md rounded-lg border border-border bg-card p-5 shadow-card">
        <h3 class="mb-4 font-title text-lg font-bold text-text">{{ editing ? locale.t('adminGroups.editGroupTitle', 'Изменить группу') : locale.t('adminGroups.addGroupTitle', 'Добавить группу') }}</h3>
        <div class="space-y-3">
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminGroups.nameLabel', 'Название') }}</span>
            <input v-model="form.name" :disabled="!!editing" list="grp-parsed" :placeholder="locale.t('adminGroups.namePlaceholder', 'Выберите или введите')"
                   class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent disabled:opacity-60" />
            <datalist id="grp-parsed"><option v-for="n in parsedGroups" :key="n" :value="n" /></datalist>
          </label>
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminGroups.categoryLabel', 'Категория') }}</span>
            <select v-model="form.category"
                    class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent">
              <option v-for="c in categories" :key="c.key" :value="c.key">{{ c.label }}</option>
            </select>
          </label>
          <div>
            <div class="mb-1 flex items-center justify-between">
              <span class="block text-tiny uppercase text-text3">{{ locale.t('adminGroups.subjectsLabel', 'Предметы группы') }}</span>
              <button v-if="form.subjects.length" type="button" class="text-tiny text-text3 hover:text-red hover:underline" @click="clearSubjects">
                {{ locale.t('adminGroups.clearSubjects', 'убрать все') }}
              </button>
            </div>
            <div class="max-h-48 overflow-y-auto rounded-sm border border-border2 bg-card2 p-2">
              <label v-for="s in allSubjects" :key="s" class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm text-text hover:bg-bg2">
                <input type="checkbox" :checked="form.subjects.includes(s)" @change="toggleSubject(s)" />
                {{ s }}
              </label>
              <p v-if="!allSubjects.length" class="px-1 py-2 text-xs text-text3">{{ locale.t('adminGroups.noSubjectsCatalogHint', 'Сначала заведите предметы во вкладке «Предметы».') }}</p>
            </div>
          </div>
          <p v-if="formError" class="text-sm text-red">{{ formError }}</p>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <AppButton variant="ghost" size="sm" @click="showForm = false">{{ locale.t('common.cancel') }}</AppButton>
          <AppButton variant="green" size="sm" :disabled="saving" @click="save">{{ saving ? locale.t('adminGroups.savingBtn', 'Сохранение…') : (editing ? locale.t('common.save') : locale.t('adminGroups.addAction2', 'Добавить')) }}</AppButton>
        </div>
      </div>
    </div>
  </div>

  <TeacherSuggestionsDialog v-if="showTeacherSuggest"
                            @close="showTeacherSuggest = false" @applied="reload" />

  <InviteDialog v-if="inviteGroup" :group="inviteGroup" @close="inviteGroup = ''" />
</template>
