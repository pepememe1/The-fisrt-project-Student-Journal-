<script setup>
// AdminSchedule — редактор расписания 2.0 (overlay поверх портала ВСГУТУ).
//
// Что нового против первой версии:
//  • сетка «слоты × дни» — каждая ячейка является зоной для drag-drop;
//  • перенос пары ПЕРЕТАСКИВАНИЕМ (long-press ~0.5 c, работает мышью и пальцем);
//  • при переносе время подстраивается под НОМЕР слота (1-я пара → 09:00, 3-я → 13:00);
//  • сразу после переноса — сверка аудитории/преподавателя у ДРУГИХ групп, накладка
//    подсвечивается красным;
//  • правки НЕ пишутся сразу — копятся в ЧЕРНОВИКЕ, «Сохранить» шлёт их пачкой;
//  • уход со вкладки или смена группы с несохранённым черновиком — переспрос;
//  • «Взять с ВСГУТУ» (группа/все) — форс-обновление портала; «Сброс» (группа/все) —
//    снять правки и вернуться к порталу.
//
// Почему сетка, а не карточки: перетаскивать пару в конкретный слот (день+номер) можно,
// только когда каждый слот — видимая зона сброса, включая пустые.
import { ref, computed, reactive, onMounted, onBeforeUnmount } from 'vue'
import StickyXScroll from '@/components/ui/StickyXScroll.vue'
import { onBeforeRouteLeave } from 'vue-router'
import { RotateCw, User, Users } from '@lucide/vue'
import { adminApi, scheduleApi } from '@/api/endpoints'
import AppButton from '@/components/ui/AppButton.vue'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocaleStore } from '@/stores/locale'

const toast = useToast()
const { confirm } = useConfirm()
const locale = useLocaleStore()

const DAYS = ['Пнд', 'Втр', 'Срд', 'Чтв', 'Птн', 'Сбт']
// Коды дней — те же строки, что отдаёт парсер портала (schedule/parser.py), их менять
// нельзя: они служат КЛЮЧАМИ в base.value.weeks. Отображаемая подпись переводится
// отдельно, см. dayLabel() ниже.
const DAY_LABEL_KEYS = {
  Пнд: ['adminSchedule.dayMon', 'Пнд'],
  Втр: ['adminSchedule.dayTue', 'Втр'],
  Срд: ['adminSchedule.dayWed', 'Срд'],
  Чтв: ['adminSchedule.dayThu', 'Чтв'],
  Птн: ['adminSchedule.dayFri', 'Птн'],
  Сбт: ['adminSchedule.daySat', 'Сбт'],
}
function dayLabel(day) {
  const e = DAY_LABEL_KEYS[day]
  return e ? locale.t(e[0], e[1]) : day
}
function weekLabel(w) {
  return w === 2 ? locale.t('adminSchedule.week2', 'II неделя') : locale.t('adminSchedule.week1', 'I неделя')
}
// Стандартная сетка звонков ВСГУТУ — запасная, если портал не отдал своё расписание пар.
const DEFAULT_TIMES = ['09:00-10:35', '10:45-12:20', '13:00-14:35',
                       '14:45-16:20', '16:25-18:00', '18:05-19:40']
// Сколько держать пару, прежде чем начнётся перетаскивание. Короткое удержание отличает
// намеренный перенос от обычного тапа/прокрутки. Значение подбираемое.
const LONG_PRESS_MS = 500
const MOVE_CANCEL_PX = 8      // сдвиг до старта перетаскивания = это прокрутка, не drag

const groups = ref([])
const group = ref('')
const week = ref(1)
const base = ref(null)        // слитое расписание с сервера {weeks, pair_times}
const savedKeys = ref(new Set())   // ячейки с УЖЕ сохранёнными правками (бейдж «сохранено»)
const loading = ref(false)
// §ролей: предмет — из СПИСКА предметов группы (не свободный текст), преподаватель —
// автоматом из назначения (webdata.teacher_assignments), а не ручной ввод ФИО.
const teacherBySubject = ref({})   // {предмет: {id, name}} для ТЕКУЩЕЙ группы/термина
const groupSubjects = computed(() => {
  const g = groups.value.find((x) => (x.name || x) === group.value)
  return g?.subjects || []
})
//§3.5.5: без категории сервер искал ЛЮБУЮ группу в индексе колледжа — бакалавриат/
//заочные группы (даже с верно импортированным расписанием) отвечали «недоступно».
//Правки (drag-drop/«Взять с ВСГУТУ»/«Сброс») намеренно остаются college-only — это
//решение уже заложено на сервере (_group_schedule накладывает ScheduleOverride
//только для колледжа), здесь чиним именно ПРОСМОТР для остальных категорий.
const groupCategory = computed(() => {
  const g = groups.value.find((x) => (x.name || x) === group.value)
  return g?.category || ''
})

// ── Режим (преподаватели/группы) + категория портала — как в общем SchedulePage.vue.
// §3.5.5, живой отзыв: раньше страница ВСЕГДА была редактором группы, категории и
// выбора «препод/группа» не было вовсе. mode==='' — экран выбора (как заходишь на
// вкладку в первый раз); дальше — та же логика, что на общей странице: категория
// видна ТОЛЬКО после выбора режима, режим переживает смену категории.
const mode = ref('')                 // '' | 'teacher' | 'group'
const categories = ref([])
const category = ref('')
const DEFAULT_CATEGORY = 'college'
const isCollege = computed(() => !category.value || category.value === DEFAULT_CATEGORY)
async function loadCategories() {
  try {
    const r = (await scheduleApi.categories()).data
    categories.value = r.categories || []
    category.value = r.default || DEFAULT_CATEGORY
  } catch { categories.value = []; category.value = DEFAULT_CATEGORY }
}
const groupsForCategory = computed(() =>
  groups.value.filter((g) => (g.category || DEFAULT_CATEGORY) === category.value))

// Расписание ПРЕПОДАВАТЕЛЯ — только просмотр (правки ScheduleOverride привязаны к
// ГРУППЕ+ячейке, у препода группы как единицы редактирования нет). Тот же эндпоинт,
// что у общей страницы (SchedulePage.vue) и у самого препода/студента.
const teachers = ref([])
const teacherName = ref('')
const teacherData = ref(null)
const teacherLoading = ref(false)
const teacherBuilding = ref(false)
async function loadTeacherList(name) {
  teacherLoading.value = true
  try {
    const r = (await scheduleApi.teacher(name, category.value)).data
    teacherBuilding.value = !!r.building
    teachers.value = r.teachers || []
    week.value = r.week || 1
    teacherData.value = r.available ? r : null
    if (r.available) teacherName.value = r.teacher
  } catch { teacherData.value = null } finally { teacherLoading.value = false }
}
async function chooseMode(next) {
  mode.value = next
  await loadCategories()
  if (next === 'teacher') { teacherName.value = ''; await loadTeacherList('') }
  else if (groups.value.length) { await onCategoryChange(category.value, true) }
}
async function onCategoryChange(key, force) {
  if (key === category.value && !force) return
  category.value = key
  if (mode.value === 'teacher') { teacherName.value = ''; await loadTeacherList(''); return }
  //group-режим: переключаемся на первую группу НОВОЙ категории (текущая может не
  //принадлежать ей вовсе — список ниже был бы пуст, а редактор показывал бы старое).
  const list = groupsForCategory.value
  if (list.length && !list.some((g) => (g.name || g) === group.value)) {
    group.value = list[0].name || list[0]
  }
  if (list.length) await load()
  else { base.value = null }
}

// Общий рендер ДЛЯ ЧТЕНИЯ (день → пары) — используется и режимом «преподаватель»
// (всегда только просмотр), и режимом «группа» ВНЕ колледжа (там ScheduleOverride
// не накладывается — редактор был бы косметикой без эффекта, см. groupCategory выше).
const readOnlyWeeks = computed(() =>
  (mode.value === 'teacher' ? teacherData.value?.schedule?.weeks : base.value?.weeks) || {})
function readOnlyDayLessons(day) { return (readOnlyWeeks.value[String(week.value)] || {})[day] || [] }

// Черновик: ключ «неделя|день|слот» → {action:'set'|'remove', ...поля}. Пусто = нет правок.
const pending = reactive({})
// Ключи ячеек, где сверка нашла накладку (для подсветки).
const conflicts = reactive(new Set())

const dirty = computed(() => Object.keys(pending).length > 0)
const pairTimes = computed(() => base.value?.pair_times?.length ? base.value.pair_times : DEFAULT_TIMES)

// 🔥 ПОДГРУППА ВХОДИТ В КЛЮЧ ЧЕРНОВИКА (жалоба Влада 03.09.2026: «если выставить тот
// номер пары, который уже есть, новая пара заменяет полностью старую»). Ключ без неё
// означал ровно одну пару в ячейке, и вторая затирала первую молча — здесь, в
// черновике, так же как и на сервере.
// ⚠️ У «Совместно» (0) хвоста НЕТ — иначе ключи всех уже существующих правок сменились
// бы, и сохранённые пары перестали бы узнаваться (`savedKeys`, `conflicts`).
function key(day, slot, subgroup = 0) {
  return `${week.value}|${day}|${slot}` + (Number(subgroup) ? `|${Number(subgroup)}` : '')
}
function timeForSlot(slot) { return pairTimes.value[slot - 1] || '' }

// Сколько строк-слотов рисовать: минимум 6, но если где-то есть пара с бо́льшим номером —
// показываем и её (портал изредка даёт 7-ю пару).
const slotCount = computed(() => {
  let max = 6
  for (const day of DAYS) {
    for (const p of basePairs(day)) max = Math.max(max, p.pair_no || 0)
  }
  for (const k of Object.keys(pending)) {
    const [w, , slot] = k.split('|')
    if (Number(w) === week.value) max = Math.max(max, Number(slot))
  }
  return max
})
const slots = computed(() => Array.from({ length: slotCount.value }, (_, i) => i + 1))

function basePairs(day) {
  return (base.value?.weeks?.[String(week.value)] || {})[day] || []
}

// Что показать в ячейке с учётом черновика: null — пусто.
function cell(day, slot) {
  const k = key(day, slot)
  if (k in pending) {
    const op = pending[k]
    if (op.action === 'remove') return null
    return { pair_no: slot, ...op, _pending: true }
  }
  const found = basePairs(day).find((p) => Number(p.pair_no) === slot)
  return found ? { ...found, _pending: false } : null
}

/** Вторая пара слота — та, что у другой подгруппы. null, если её нет.
 *
 * ⚠️ Без этого сетка админки показывала бы ОДНУ пару из двух: данные сохранены верно и
 * студенту с преподавателем видны обе, а администратор, который их и завёл, видит одну
 * — то есть выглядит как потеря, хотя ничего не потеряно. Ровно та жалоба, с которой
 * всё началось, только в другом месте.
 */
function cellSecond(day, slot) {
  const first = cell(day, slot)
  if (!first) return null
  const mine = Number(first.subgroup || 0)
  for (const sub of [1, 2]) {
    if (sub === mine) continue
    const k = key(day, slot, sub)
    if (k in pending) {
      const op = pending[k]
      if (op.action !== 'remove') return { pair_no: slot, ...op, _pending: true }
      continue
    }
    const found = basePairs(day).find(
      (p) => Number(p.pair_no) === slot && Number(p.subgroup || 0) === sub)
    if (found && found !== first) return { ...found, _pending: false }
  }
  return null
}

function isPending(day, slot) { return key(day, slot) in pending }
function isSaved(day, slot) { return !isPending(day, slot) && savedKeys.value.has(key(day, slot)) }
function isConflict(day, slot) { return conflicts.has(key(day, slot)) }

// ── Загрузка ──────────────────────────────────────────────────────────────────────
onMounted(async () => {
  try { groups.value = (await adminApi.groups()).data.groups || [] } catch { groups.value = [] }
})

async function load() {
  if (!group.value) return
  loading.value = true
  try {
    const r = (await adminApi.schedule(group.value, groupCategory.value)).data
    base.value = r.schedule
    savedKeys.value = new Set((r.overrides || []).map((o) => `${o.week}|${o.day}|${o.pair_no}`))
    clearDraft()
  } catch { toast.error(locale.t('adminSchedule.loadFailed', 'Не удалось загрузить расписание')) } finally { loading.value = false }
  //Назначения препод↔предмет для автозаполнения графы «Преподаватель» — тот же
  //источник, что и в редакторе часов («Группы» → 🕐), не блокирует основную загрузку.
  try {
    const hr = (await adminApi.groupHours(group.value)).data
    const map = {}
    for (const s of (hr.subjects || [])) {
      if (s.teacher_id) map[s.subject] = { id: s.teacher_id, name: s.teacher_name }
    }
    teacherBySubject.value = map
  } catch { teacherBySubject.value = {} }
}

function clearDraft() {
  for (const k of Object.keys(pending)) delete pending[k]
  conflicts.clear()
}

// ── Сохранение / отмена черновика ──────────────────────────────────────────────────
const saving = ref(false)
async function saveDraft() {
  if (!dirty.value) return
  saving.value = true
  try {
    const overrides = Object.entries(pending).map(([k, op]) => {
      // Хвост ключа — подгруппа; у «Совместно» его нет (см. `key`). Берём из операции,
      // а к ключу обращаемся лишь как к запасному источнику: одна правда, второй
      // разбор строки разошёлся бы с ней при первой же правке формы.
      const [w, day, slot, sub] = k.split('|')
      return { group: group.value, week: Number(w), day, pair_no: Number(slot),
               subgroup: Number(op.subgroup ?? sub ?? 0), ...op }
    })
    await adminApi.saveScheduleOverrides(overrides)
    toast.success(locale.t('adminSchedule.saved', 'Расписание сохранено'))
    await load()
  } catch (e) { toast.error(e?.response?.data?.detail || locale.t('adminSchedule.saveFailed', 'Не удалось сохранить')) }
  finally { saving.value = false }
}

async function discardDraft() {
  if (!dirty.value) return
  if (!(await confirm({ title: locale.t('adminSchedule.confirmDiscardTitle', 'Отменить несохранённые правки?'), okText: locale.t('adminSchedule.confirmDiscardOk', 'Отменить правки'), danger: true }))) return
  clearDraft()
}

// ── Перенос: собственно правка черновика ───────────────────────────────────────────
async function moveCell(from, to) {
  if (from.day === to.day && from.slot === to.slot) return
  const src = cell(from.day, from.slot)
  if (!src) return
  if (cell(to.day, to.slot)) {
    toast.error(locale.t('adminSchedule.slotOccupied', 'Слот занят — сначала освободите его'))
    return
  }
  // Перенос = скрыть исходную ячейку + задать целевую (время под номер целевого слота).
  pending[key(from.day, from.slot)] = { action: 'remove' }
  conflicts.delete(key(from.day, from.slot))
  const op = {
    action: 'set',
    subject: src.subject || src.raw || '',
    room: src.room || '', teacher: src.teacher || '', kind: src.kind || '',
    time: timeForSlot(to.slot),
  }
  pending[key(to.day, to.slot)] = op
  await checkSlot(to.day, to.slot, op)
}

// Сверка целевого слота против других групп (аудитория/преподаватель).
async function checkSlot(day, slot, op) {
  try {
    const r = (await adminApi.slotConflicts({
      group: group.value, week: week.value, day, pair_no: slot,
      room: op.room, teacher: op.teacher, subject: op.subject,
    })).data
    const clash = (r.room?.length || 0) + (r.teacher?.length || 0)
    if (clash) {
      conflicts.add(key(day, slot))
      const who = r.room?.length
        ? locale.t('adminSchedule.conflictRoom', { room: op.room })
        : locale.t('adminSchedule.conflictTeacher', { teacher: op.teacher })
      toast.error(locale.t('adminSchedule.conflictMessage', { who }))
    } else {
      conflicts.delete(key(day, slot))
      if (r.building) toast.info(locale.t('adminSchedule.checkIncomplete', 'Проверка неполна: расписание портала ещё собирается'))
    }
  } catch { /* сверка не критична — правку не блокируем */ }
}

// ── Форма пары (добавить / изменить / скрыть) ──────────────────────────────────────
const showForm = ref(false)
// `subgroup`: 0 — пара всей группе, 1/2 — только своей половине. `time` правится вручную,
// но по умолчанию подставляется по номеру пары.
const form = reactive({ day: 'Пнд', slot: 1, subject: '', room: '', teacher: '',
                        origin: null, subgroup: 0, time: '' })

// Предмет выбран/сменился → преподаватель подставляется автоматом из назначения.
// Не найдено назначения — поле остаётся пустым (не блокирует сохранение: расписание
// можно вести и до кадрового назначения).
function onFormSubjectChange() {
  form.teacher = teacherBySubject.value[form.subject]?.name || ''
}
function openAdd(day, slot) {
  // Номер и время продолжают предыдущую пару дня (просьба Влада: «время и номер
  // становятся по порядку от предыдущей, но можно выставить вручную»). Клик по
  // конкретной ячейке остаётся главнее подсказки — человек указал слот пальцем.
  const proposed = slot || nextFreeSlot(day)
  Object.assign(form, {
    day, slot: proposed, subject: '', room: '', teacher: '', origin: null,
    subgroup: 0, time: timeForSlot(proposed),
  })
  showForm.value = true
}

/** Первый свободный номер пары в дне — «по порядку от предыдущей». */
function nextFreeSlot(day) {
  let last = 0
  for (const s of slots.value) if (cell(day, s)) last = s
  return Math.min(last + 1, slots.value.length ? slots.value[slots.value.length - 1] + 1 : 1)
}
function openEdit(day, slot) {
  const c = cell(day, slot)
  if (!c) return openAdd(day, slot)
  const subject = c.subject || c.raw || ''
  Object.assign(form, {
    day, slot, subject, room: c.room || '',
    teacher: teacherBySubject.value[subject]?.name || c.teacher || '', origin: { day, slot },
  })
  showForm.value = true
}
async function submitForm() {
  if (!form.subject.trim()) { toast.error(locale.t('adminSchedule.enterSubject', 'Укажите предмет')); return }
  // Сменили день/номер в форме — это тоже перенос: старую ячейку убрать.
  if (form.origin && (form.origin.day !== form.day || form.origin.slot !== form.slot)) {
    pending[key(form.origin.day, form.origin.slot)] = { action: 'remove' }
    conflicts.delete(key(form.origin.day, form.origin.slot))
  }
  // ⚠️ СЛОТ ЗАНЯТ — СПРАШИВАЕМ, А НЕ ЗАТИРАЕМ. Раньше вторая пара с тем же номером
  // молча заменяла первую, и человек узнавал об этом, уже потеряв её. Предлагаем ровно
  // тот исход, ради которого так делают: две подгруппы в одной паре.
  let subgroup = Number(form.subgroup || 0)
  const occupied = cell(form.day, form.slot)
  if (occupied && !subgroup && !form.origin) {
    const both = await confirm({
      title: locale.t('adminSchedule.slotTakenTitle', 'В этой паре уже есть занятие'),
      text: locale.t('adminSchedule.slotTakenText',
        'Поставить оба — по одному на подгруппу? «Отмена» оставит как было.'),
      okText: locale.t('adminSchedule.slotTakenOk', 'Две подгруппы'),
    })
    if (!both) return
    // Занятое остаётся первой подгруппой, новое становится второй. Иначе пришлось бы
    // переписывать чужую строку, а её автор об этом не просил.
    subgroup = 2
    const prev = pending[key(form.day, form.slot)] || { ...occupied, action: 'set' }
    delete pending[key(form.day, form.slot)]
    pending[key(form.day, form.slot, 1)] = { ...prev, subgroup: 1 }
  }

  const op = {
    action: 'set', subject: form.subject.trim(), room: form.room.trim(),
    //Преподаватель — ставится автоматом из назначения (не ручной ввод, см. onFormSubjectChange).
    teacher: (teacherBySubject.value[form.subject]?.name || '').trim(), kind: '',
    //Время берётся из формы: оно подставляется по номеру пары, но правится вручную.
    time: (form.time || '').trim() || timeForSlot(form.slot),
    subgroup,
  }
  pending[key(form.day, form.slot, subgroup)] = op
  showForm.value = false
  await checkSlot(form.day, form.slot, op)
}
function hideFromForm() {
  pending[key(form.day, form.slot)] = { action: 'remove' }
  conflicts.delete(key(form.day, form.slot))
  showForm.value = false
}

// ── Взять с ВСГУТУ / Сброс ─────────────────────────────────────────────────────────
async function refreshGroup() {
  if (dirty.value && !(await confirm({ title: locale.t('adminSchedule.confirmRefreshGroupTitle', 'Обновить с ВСГУТУ? Несохранённые правки пропадут.'), okText: locale.t('common.refresh'), danger: true }))) return
  loading.value = true
  try { await adminApi.refreshSchedule(group.value); await load(); toast.success(locale.t('adminSchedule.refreshedFromPortal', 'Расписание обновлено с портала')) }
  catch { toast.error(locale.t('adminSchedule.refreshFailed', 'Не удалось обновить')); loading.value = false }
}
async function refreshAll() {
  if (!(await confirm({ title: locale.t('adminSchedule.confirmRefreshAllTitle', 'Обновить расписание ВСЕХ групп с ВСГУТУ?'), message: locale.t('adminSchedule.confirmRefreshAllMessage', 'Сборка идёт в фоне (~минуту).'), okText: locale.t('adminSchedule.confirmRefreshAllOk', 'Обновить все') }))) return
  try { await adminApi.refreshSchedule('', true); toast.success(locale.t('adminSchedule.refreshAllStarted', 'Обновление всех групп запущено (в фоне)')) }
  catch { toast.error(locale.t('adminSchedule.refreshAllFailed', 'Не удалось запустить обновление')) }
}
async function resetGroup() {
  if (!(await confirm({ title: locale.t('adminSchedule.confirmResetGroupTitle', { group: group.value }), message: locale.t('adminSchedule.confirmResetGroupMessage', 'Расписание снова будет браться с портала.'), okText: locale.t('adminSchedule.confirmResetGroupOk', 'Сбросить'), danger: true }))) return
  try { await adminApi.resetSchedule(group.value); await load(); toast.success(locale.t('adminSchedule.resetDone', 'Правки сброшены')) }
  catch { toast.error(locale.t('adminSchedule.resetFailed', 'Не удалось сбросить')) }
}
async function resetAll() {
  if (!(await confirm({ title: locale.t('adminSchedule.confirmResetAllTitle', 'Сбросить правки ВСЕХ групп?'), message: locale.t('adminSchedule.confirmResetAllMessage', 'Все ручные правки колледжа будут удалены безвозвратно.'), okText: locale.t('adminSchedule.confirmResetAllOk', 'Далее'), danger: true }))) return
  if (!(await confirm({ title: locale.t('adminSchedule.confirmResetAllConfirmTitle', 'Точно удалить правки всех групп?'), message: locale.t('adminSchedule.confirmResetAllConfirmMessage', 'Это действие необратимо.'), okText: locale.t('adminSchedule.confirmResetAllConfirmOk', 'Удалить всё'), danger: true }))) return
  try { const r = await adminApi.resetSchedule('', true); await load(); toast.success(locale.t('adminSchedule.resetAllDone', { n: r.data.reset })) }
  catch { toast.error(locale.t('adminSchedule.resetFailed', 'Не удалось сбросить')) }
}

// ── Смена группы/недели с защитой от потери черновика ───────────────────────────────
async function onGroupChange(e) {
  const next = e.target.value
  if (dirty.value && !(await confirm({ title: locale.t('adminSchedule.confirmLeaveTitle', 'Продолжить без сохранения?'), message: locale.t('adminSchedule.confirmLeaveMessage', 'В расписании есть несохранённые правки.'), okText: locale.t('adminSchedule.confirmLeaveOkContinue', 'Продолжить'), danger: true }))) {
    e.target.value = group.value      // вернуть прежний выбор
    return
  }
  group.value = next
  await load()
}

onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  return await confirm({ title: locale.t('adminSchedule.confirmLeaveTitle', 'Продолжить без сохранения?'), message: locale.t('adminSchedule.confirmLeaveMessage', 'В расписании есть несохранённые правки.'), okText: locale.t('adminSchedule.confirmLeaveOkLeave', 'Уйти без сохранения'), danger: true })
})

// Уход со страницы через закрытие вкладки/обновление браузера.
function beforeUnload(e) { if (dirty.value) { e.preventDefault(); e.returnValue = '' } }
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

// ── Drag-and-drop (long-press, мышь и тач) ─────────────────────────────────────────
const dragging = ref(null)        // {day, slot, text} — что тащим
const hover = ref(null)           // {day, slot} — над какой ячейкой
const ghost = reactive({ show: false, x: 0, y: 0, text: '' })
let press = null                  // фаза удержания до старта перетаскивания

function onPairPointerDown(e, day, slot) {
  if (e.button != null && e.button !== 0) return   // только основная кнопка мыши
  const c = cell(day, slot)
  if (!c) return
  press = { day, slot, x: e.clientX, y: e.clientY, text: c.subject || c.raw || '', moved: false }
  press.timer = setTimeout(beginDrag, LONG_PRESS_MS)
  window.addEventListener('pointermove', onPressMove)
  window.addEventListener('pointerup', onPressUp)
}
function onPressMove(e) {
  if (!press) return
  if (Math.hypot(e.clientX - press.x, e.clientY - press.y) > MOVE_CANCEL_PX) {
    press.moved = true
    endPress()          // сдвинулись до срабатывания удержания — это прокрутка, не перенос
  }
}
function onPressUp() {
  if (!press) return
  const p = press
  endPress()
  if (!p.moved) openEdit(p.day, p.slot)   // короткий тап = открыть форму (правка/скрытие)
}
function endPress() {
  if (press?.timer) clearTimeout(press.timer)
  window.removeEventListener('pointermove', onPressMove)
  window.removeEventListener('pointerup', onPressUp)
  press = null
}
function beginDrag() {
  if (!press) return
  const p = press
  window.removeEventListener('pointermove', onPressMove)
  window.removeEventListener('pointerup', onPressUp)
  if (press?.timer) clearTimeout(press.timer)
  press = null
  dragging.value = { day: p.day, slot: p.slot, text: p.text }
  Object.assign(ghost, { show: true, x: p.x, y: p.y, text: p.text })
  window.addEventListener('pointermove', onDragMove, { passive: false })
  window.addEventListener('pointerup', onDragUp)
}
function cellUnderPoint(e) {
  const el = document.elementFromPoint(e.clientX, e.clientY)
  const td = el?.closest('[data-day]')
  if (!td) return null
  return { day: td.dataset.day, slot: Number(td.dataset.slot) }
}
function onDragMove(e) {
  e.preventDefault()      // не даём странице прокручиваться во время перетаскивания
  ghost.x = e.clientX; ghost.y = e.clientY
  hover.value = cellUnderPoint(e)
}
async function onDragUp(e) {
  const from = dragging.value
  const to = cellUnderPoint(e)
  endDrag()
  if (from && to) await moveCell(from, to)
}
function endDrag() {
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragUp)
  dragging.value = null
  hover.value = null
  ghost.show = false
}
onBeforeUnmount(() => { endPress(); endDrag() })

function isDragSource(day, slot) {
  return dragging.value && dragging.value.day === day && dragging.value.slot === slot
}
function isHover(day, slot) {
  return hover.value && hover.value.day === day && hover.value.slot === slot
}
</script>

<template>
  <div class="space-y-4" style="touch-action: pan-y">
    <!-- §3.5.5: заход на вкладку — выбор режима (как в общем SchedulePage.vue), а не
         сразу редактор группы. Живёт, пока не выйдешь со вкладки. -->
    <div v-if="mode === ''" class="flex min-h-[40vh] flex-col items-center justify-center gap-4">
      <p class="text-sm text-text2">{{ locale.t('adminSchedule.chooseMode', 'Что показать?') }}</p>
      <div class="flex flex-wrap justify-center gap-3">
        <AppButton variant="ghost" @click="chooseMode('teacher')">
          <User class="size-4" /> {{ locale.t('schedulePage.teacherSchedule', 'Расписание преподавателя') }}
        </AppButton>
        <AppButton variant="ghost" @click="chooseMode('group')">
          <Users class="size-4" /> {{ locale.t('schedulePage.groupSchedule', 'Расписание группы') }}
        </AppButton>
      </div>
    </div>

    <template v-else>
      <div class="flex flex-wrap items-center justify-between gap-2">
        <!-- Категории портала — БЕЗ пояснений «преподы/группы» на кнопках, режим уже
             выбран отдельно выше. -->
        <div v-if="categories.length > 1" class="flex flex-wrap gap-2">
          <button v-for="c in categories" :key="c.key"
                  class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
                  :class="category === c.key ? 'border-accent bg-accent text-white' : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
                  @click="onCategoryChange(c.key)">{{ c.label }}</button>
        </div>
        <button class="text-xs text-text3 underline-offset-2 hover:text-accent hover:underline" @click="mode = ''">
          {{ locale.t('schedulePage.backToChoice', '← к выбору') }}
        </button>
      </div>

      <!-- ═══ Режим «преподаватель» — ТОЛЬКО просмотр, правки привязаны к группе ═══ -->
      <template v-if="mode === 'teacher'">
        <div class="flex flex-wrap items-center gap-2">
          <select v-model="teacherName" @change="loadTeacherList(teacherName)"
                  class="h-9 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent sm:w-auto sm:max-w-64">
            <option value="" disabled>{{ locale.t('schedulePage.teacherPlaceholder', 'Преподаватель…') }}</option>
            <option v-for="t in teachers" :key="t" :value="t">{{ t }}</option>
          </select>
          <div class="inline-flex overflow-hidden rounded-sm border border-border2">
            <button class="px-3 py-1.5 text-sm" :class="week === 1 ? 'bg-accent text-white' : 'bg-card2 text-text3'" @click="week = 1">{{ locale.t('adminSchedule.week1', 'I неделя') }}</button>
            <button class="px-3 py-1.5 text-sm" :class="week === 2 ? 'bg-accent text-white' : 'bg-card2 text-text3'" @click="week = 2">{{ locale.t('adminSchedule.week2', 'II неделя') }}</button>
          </div>
          <template v-if="teacherBuilding && !teachers.length">
            <span class="text-xs text-text3">{{ locale.t('schedulePage.buildingTeachers', 'Индекс преподавателей ещё готовится на сервере (~минута).') }}</span>
            <AppButton variant="green" size="sm" @click="loadTeacherList(teacherName)"><RotateCw class="size-3.5" /> {{ locale.t('schedulePage.checkAgain', 'Проверить снова') }}</AppButton>
          </template>
        </div>
        <p v-if="teacherLoading" class="text-sm text-text3">{{ locale.t('common.loading') }}</p>
        <div v-else-if="!teacherData" class="text-sm text-text3">{{ locale.t('schedulePage.unavailableTitle', 'Расписание недоступно') }}</div>
        <div v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          <div v-for="day in DAYS" :key="day" class="rounded-lg border border-border bg-card p-3 shadow-card">
            <p class="mb-2 font-title text-base font-bold text-text">{{ dayLabel(day) }}</p>
            <p v-if="!readOnlyDayLessons(day).length" class="py-4 text-center text-xs text-text2">{{ locale.t('schedulePage.noLessons', 'Занятий нет') }}</p>
            <ul v-else class="space-y-2">
              <li v-for="(l, i) in readOnlyDayLessons(day)" :key="i" class="rounded-md border border-border bg-card2 p-2.5">
                <p class="text-xs font-semibold text-text3">{{ l.pair_no }}. {{ l.time }}</p>
                <p class="text-sm font-medium text-text">{{ l.subject || l.raw }}</p>
                <p v-if="l.group || l.room" class="mt-0.5 text-xs text-text3">
                  {{ l.group ? locale.t('schedulePage.groupLabel', { group: l.group }) : '' }}<span v-if="l.room"> · {{ locale.t('schedulePage.roomLabel', { room: l.room }) }}</span>
                </p>
              </li>
            </ul>
          </div>
        </div>
      </template>

      <!-- ═══ Режим «группа» — редактор ТОЛЬКО для колледжа (там живут ScheduleOverride);
           вне колледжа — тот же просмотр, что и у препода/студента. ═══ -->
      <template v-else>
        <div class="flex flex-wrap items-center gap-2">
          <select :value="group" @change="onGroupChange"
                  class="h-9 rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent">
            <option v-for="g in groupsForCategory" :key="g.name || g" :value="g.name || g">{{ g.name || g }}</option>
          </select>
          <div class="inline-flex overflow-hidden rounded-sm border border-border2">
            <button class="px-3 py-1.5 text-sm" :class="week === 1 ? 'bg-accent text-white' : 'bg-card2 text-text3'" @click="week = 1">{{ locale.t('adminSchedule.week1', 'I неделя') }}</button>
            <button class="px-3 py-1.5 text-sm" :class="week === 2 ? 'bg-accent text-white' : 'bg-card2 text-text3'" @click="week = 2">{{ locale.t('adminSchedule.week2', 'II неделя') }}</button>
          </div>

          <div v-if="isCollege" class="ml-auto flex flex-wrap items-center gap-2">
            <AppButton variant="ghost" size="sm" @click="refreshGroup">{{ locale.t('adminSchedule.pullFromPortal', '↻ Взять с ВСГУТУ') }}</AppButton>
            <AppButton variant="ghost" size="sm" @click="refreshAll">{{ locale.t('adminSchedule.pullAllGroups', '↻ Все группы') }}</AppButton>
            <AppButton variant="ghost" size="sm" @click="resetGroup">{{ locale.t('adminSchedule.resetGroupBtn', 'Сброс группы') }}</AppButton>
            <AppButton variant="ghost" size="sm" @click="resetAll">{{ locale.t('adminSchedule.resetAllBtn', 'Сброс всех') }}</AppButton>
          </div>
          <AppButton v-else variant="ghost" size="sm" class="ml-auto" @click="load"><RotateCw class="size-3.5" /> {{ locale.t('schedulePage.refresh', 'Обновить') }}</AppButton>
        </div>

        <!-- Черновик: сохранить / отменить — только колледж (только там правки вообще
             применяются, см. isCollege выше). -->
        <div v-if="isCollege && dirty" class="flex flex-wrap items-center gap-3 rounded-lg border border-accent/40 bg-accent/5 px-4 py-2.5">
          <span class="text-sm font-medium text-accent">{{ locale.t('adminSchedule.unsavedChanges', 'Есть несохранённые правки') }}</span>
          <div class="ml-auto flex gap-2">
            <AppButton variant="ghost" size="sm" @click="discardDraft">{{ locale.t('adminSchedule.discardBtn', 'Отменить') }}</AppButton>
            <AppButton variant="green" size="sm" :disabled="saving" @click="saveDraft">{{ saving ? locale.t('adminSchedule.savingBtn', 'Сохранение…') : locale.t('common.save') }}</AppButton>
          </div>
        </div>

        <p v-if="isCollege" class="text-xs text-text3">
          {{ locale.t('adminSchedule.dragHint', 'Удерживайте пару ~полсекунды и перетащите на другой слот или день. Время подстроится под номер пары. Тап по паре — правка, тап по пустой ячейке — добавить.') }}
        </p>

        <p v-if="loading" class="text-sm text-text3">{{ locale.t('common.loading') }}</p>

        <!-- Интерактивная сетка «слоты × дни» — ТОЛЬКО колледж. -->
        <StickyXScroll v-else-if="isCollege">
          <table class="w-full min-w-[820px] border-collapse select-none">
            <thead>
              <tr>
                <th class="w-28 border border-border2 bg-card2 p-2 text-xs font-semibold text-text3">{{ locale.t('adminSchedule.colPair', 'Пара') }}</th>
                <th v-for="day in DAYS" :key="day" class="border border-border2 bg-card2 p-2 text-sm font-semibold text-text">{{ dayLabel(day) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="slot in slots" :key="slot">
                <td class="border border-border2 bg-card2 p-2 text-center align-top">
                  <div class="text-sm font-bold text-text">{{ slot }}</div>
                  <div class="text-tiny text-text3">{{ timeForSlot(slot) }}</div>
                </td>
                <td v-for="day in DAYS" :key="day" :data-day="day" :data-slot="slot"
                    class="h-20 border border-border2 p-1 align-top transition-colors"
                    :class="[
                      isHover(day, slot) ? 'bg-accent/15 ring-2 ring-inset ring-accent' : '',
                      !cell(day, slot) && dragging ? 'bg-card2/50' : '',
                    ]"
                    @click="!cell(day, slot) && !dragging && openAdd(day, slot)">
                  <div v-if="cell(day, slot)"
                       class="group relative h-full cursor-grab rounded-md border p-1.5 text-left"
                       :class="[
                         isConflict(day, slot) ? 'border-red bg-red/10'
                           : isPending(day, slot) ? 'border-accent bg-accent/10'
                           : 'border-border bg-card',
                         isDragSource(day, slot) ? 'opacity-40' : '',
                       ]"
                       @pointerdown="onPairPointerDown($event, day, slot)">
                    <p class="truncate text-xs font-semibold text-text">{{ cell(day, slot).subject || cell(day, slot).raw }}</p>
                    <p v-if="cell(day, slot).room || cell(day, slot).teacher" class="truncate text-tiny text-text3">
                      {{ [cell(day, slot).teacher, cell(day, slot).room ? locale.t('adminSchedule.roomLabel', { room: cell(day, slot).room }) : ''].filter(Boolean).join(' · ') }}
                    </p>
                    <!-- Вторая подгруппа того же слота. Отделена чертой: без неё две
                         строки читаются как одно занятие с длинным названием. -->
                    <template v-if="cellSecond(day, slot)">
                      <p class="mt-1 truncate border-t border-border2 pt-1 text-xs font-semibold text-text">
                        {{ cellSecond(day, slot).subject || cellSecond(day, slot).raw }}
                      </p>
                      <p class="truncate text-tiny text-text3">
                        {{ locale.t('adminSchedule.subgroupShort', { n: cellSecond(day, slot).subgroup }) }}
                        <template v-if="cellSecond(day, slot).teacher"> · {{ cellSecond(day, slot).teacher }}</template>
                      </p>
                    </template>
                    <span v-if="isConflict(day, slot)" class="absolute right-1 top-1 text-tiny font-bold text-red" :title="locale.t('adminSchedule.conflictTitle', 'Накладка')">⚠</span>
                    <span v-else-if="isPending(day, slot)" class="absolute right-1 top-1 text-tiny text-accent" :title="locale.t('adminSchedule.unsavedTitle', 'Не сохранено')">●</span>
                    <span v-else-if="isSaved(day, slot)" class="absolute right-1 top-1 text-tiny text-text3" :title="locale.t('adminSchedule.savedEditTitle', 'Сохранённая правка')">✎</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </StickyXScroll>

        <!-- Вне колледжа — тот же просмотр, что у препода/студента, без редактирования
             (ScheduleOverride там не накладывается, см. _group_schedule на сервере). -->
        <div v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          <div v-for="day in DAYS" :key="day" class="rounded-lg border border-border bg-card p-3 shadow-card">
            <p class="mb-2 font-title text-base font-bold text-text">{{ dayLabel(day) }}</p>
            <p v-if="!readOnlyDayLessons(day).length" class="py-4 text-center text-xs text-text2">{{ locale.t('schedulePage.noLessons', 'Занятий нет') }}</p>
            <ul v-else class="space-y-2">
              <li v-for="(l, i) in readOnlyDayLessons(day)" :key="i" class="rounded-md border border-border bg-card2 p-2.5">
                <p class="text-xs font-semibold text-text3">{{ l.pair_no }}. {{ l.time }}</p>
                <p class="text-sm font-medium text-text">{{ l.subject || l.raw }}</p>
                <p v-if="l.teacher || l.room" class="mt-0.5 text-xs text-text3">
                  {{ l.teacher }}<span v-if="l.room"> · {{ locale.t('schedulePage.roomLabel', { room: l.room }) }}</span>
                </p>
              </li>
            </ul>
          </div>
        </div>
      </template>
    </template>

    <!-- Плавающий «призрак» перетаскивания -->
    <div v-if="ghost.show" class="pointer-events-none fixed z-[60] -translate-x-1/2 -translate-y-1/2 rounded-md border border-accent bg-card px-2 py-1 text-xs font-semibold text-text shadow-card"
         :style="{ left: ghost.x + 'px', top: ghost.y + 'px' }">
      {{ ghost.text }}
    </div>

    <!-- Форма пары -->
    <div v-if="showForm" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="showForm = false">
      <div class="w-full max-w-md rounded-lg border border-border bg-card p-5 shadow-card">
        <h3 class="mb-4 font-title text-lg font-bold text-text">{{ locale.t('adminSchedule.pairFormTitle', { group, week: weekLabel(week) }) }}</h3>
        <div class="grid grid-cols-2 gap-3">
          <label class="text-sm">{{ locale.t('adminSchedule.formDay', 'День') }}
            <select v-model="form.day" class="mt-1 h-9 w-full rounded-sm border border-border2 bg-card2 px-2 text-sm text-text outline-none focus:border-accent">
              <option v-for="d in DAYS" :key="d" :value="d">{{ dayLabel(d) }}</option>
            </select>
          </label>
          <label class="text-sm">{{ locale.t('adminSchedule.formSlotNumber', '№ пары') }}
            <select v-model.number="form.slot" class="mt-1 h-9 w-full rounded-sm border border-border2 bg-card2 px-2 text-sm text-text outline-none focus:border-accent">
              <option v-for="s in slots" :key="s" :value="s">{{ s }} ({{ timeForSlot(s) }})</option>
            </select>
          </label>
          <label class="col-span-2 text-sm">{{ locale.t('adminSchedule.formSubject', 'Предмет') }}
            <select v-model="form.subject" @change="onFormSubjectChange"
                    class="mt-1 h-9 w-full rounded-sm border border-border2 bg-card2 px-2 text-sm text-text outline-none focus:border-accent">
              <option value="" disabled>{{ locale.t('adminSchedule.selectSubject', 'Выберите предмет') }}</option>
              <option v-for="s in groupSubjects" :key="s" :value="s">{{ s }}</option>
            </select>
            <p v-if="!groupSubjects.length" class="mt-1 text-tiny text-text3">
              {{ locale.t('adminSchedule.noSubjectsHint', 'У группы нет предметов — задайте их во вкладке «Группы».') }}
            </p>
          </label>
          <!-- Время подставляется по номеру пары, но правится руками: у замен и
               консультаций оно регулярно не совпадает с сеткой звонков. -->
          <label class="text-sm">{{ locale.t('adminSchedule.formTime', 'Время') }}
            <input v-model="form.time" :placeholder="timeForSlot(form.slot)"
                   class="mt-1 h-9 w-full rounded-sm border border-border2 bg-card2 px-2 text-sm text-text outline-none focus:border-accent" />
          </label>
          <!-- Подгруппа: та же нумерация, что у занятий журнала (0 — вся группа). -->
          <label class="text-sm">{{ locale.t('adminSchedule.formSubgroup', 'Подгруппа') }}
            <select v-model.number="form.subgroup"
                    class="mt-1 h-9 w-full rounded-sm border border-border2 bg-card2 px-2 text-sm text-text outline-none focus:border-accent">
              <option :value="0">{{ locale.t('adminSchedule.subgroupJoint', 'Вся группа') }}</option>
              <option :value="1">{{ locale.t('adminSchedule.subgroup1', '1 подгруппа') }}</option>
              <option :value="2">{{ locale.t('adminSchedule.subgroup2', '2 подгруппа') }}</option>
            </select>
          </label>
          <label class="text-sm">{{ locale.t('adminSchedule.formRoom', 'Аудитория') }}
            <input v-model="form.room" placeholder="101" class="mt-1 h-9 w-full rounded-sm border border-border2 bg-card2 px-2 text-sm text-text outline-none focus:border-accent" />
          </label>
          <!-- §ролей: преподаватель — ставится АВТОМАТОМ из назначения (не ручной ввод),
               см. onFormSubjectChange / webdata.teacher_assignments. -->
          <label class="text-sm">{{ locale.t('adminSchedule.formTeacher', 'Преподаватель') }}
            <input :value="form.teacher || locale.t('adminSchedule.notAssigned', '— не назначен —')" disabled
                   class="mt-1 h-9 w-full rounded-sm border border-border2 bg-card2/60 px-2 text-sm text-text3 outline-none" />
          </label>
        </div>
        <div class="mt-5 flex items-center justify-between">
          <button v-if="form.origin" class="text-sm text-red hover:underline" @click="hideFromForm">{{ locale.t('adminSchedule.hidePair', 'Скрыть пару') }}</button>
          <div class="ml-auto flex gap-2">
            <AppButton variant="ghost" size="sm" @click="showForm = false">{{ locale.t('common.cancel') }}</AppButton>
            <AppButton variant="green" size="sm" @click="submitForm">{{ locale.t('adminSchedule.addToDraft', 'В черновик') }}</AppButton>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
