<script setup>
// TeacherJournal — журнал преподавателя, порт десктопного teacher_dashboard._update_table:
//  • компактная таблица, выровнена ВЛЕВО (не расползается при 1–2 занятиях);
//  • значения ячеек — селекты по типу (Лекция ✓/Н/Б/О; Практика 2–5/Н; Экзамен 2–5/Н с
//    «(Зачтено)/(Не зачтено)» и назначением пересдачи, как на ПК);
//  • заголовок занятия читаемый: тип №, дата, тема (2 строки + полная в тултипе);
//  • ПКМ по заголовку — меню «Изменить / Пересдача / Удалить»; двойной клик — правка
//    (в модалке видна полная тема); ✎/✕ прямо в шапке;
//  • зебра, цветные оценки/средний, строка «средний по группе»;
//  • дата нового занятия — автоматически сегодняшняя.
// Всё пишется в те же таблицы, что синк десктопа → изменения доезжают до ПК pull'ом.
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import StickyXScroll from '@/components/ui/StickyXScroll.vue'
// Положение ПКМ-меню считает общий модуль — тот же, что у меню сообщения в мессенджере.
// Своя копия правила разошлась бы с ним на первой же правке (и уже разошлась: здесь
// клампилась только горизонталь).
import { menuMaxHeight, placeMenu } from '@/utils/menuPlacement'
import { useRoute } from 'vue-router'
import { teacherApi, termsApi } from '@/api/endpoints'
import {
  enqueueGrade, enqueueLessonCreate, enqueueLessonDelete, enqueueLessonUpdate,
  enqueueTermGrade, isTempId, pendingGrade, pendingLessons,
} from '@/api/outbox'
import EmptyState from '@/components/ui/EmptyState.vue'
import AppButton from '@/components/ui/AppButton.vue'
import { attemptKey, needsRetake as needsRetakeShared } from '@/utils/grades'   //контракт с Python
import { practiceAverage, scaleValues, toFivePoint } from '@/utils/grading'   //кастомная шкала препода (§ролей, 3.3.1) + офлайн-пересчёт среднего
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import MicButton from '@/components/vector/MicButton.vue'
import VoiceCommandDialog from '@/components/vector/VoiceCommandDialog.vue'
import { useLocaleStore } from '@/stores/locale'
import haptics from '@/utils/haptics'

const toast = useToast()
const { confirm, prompt } = useConfirm()
const locale = useLocaleStore()
const route = useRoute()

const groups = ref([])
const subjects = ref([])
const group = ref('')
const subject = ref('')
const data = ref(null)
const loading = ref(false)
const saving = ref(false)

// ── Учебный период: преподаватель работает ИСКЛЮЧИТЕЛЬНО с ТЕКУЩИМ термином ──────
// Ручного переключателя семестра здесь больше нет (убран по живому отзыву — путал
// «какой семестр сейчас редактируется» с архивным просмотром, а термин и так
// считается сервером исключительно по дате, см. CLAUDE.md). currentTerm остаётся
// только ради подписи в аттестации.
const currentTerm = ref(null)
function termLabel(t) {
  if (!t) return ''
  const sem = t.semester === 1
    ? locale.t('teacherJournal.termAutumn', 'осенний')
    : locale.t('teacherJournal.termSpring', 'весенний')
  return locale.t('teacherJournal.termLabelFormat', { year: t.year, semester: sem })
}
async function loadTerm() {
  try { currentTerm.value = (await termsApi.list()).data.current } catch { /* */ }
}

onMounted(async () => {
  try {
    const o = (await teacherApi.overview()).data
    groups.value = o.groups || []
    subjects.value = o.subjects || []
    // Deep-link из статистики (§ИИ Помощник → диаграммы → «Открыть журнал»): группа/
    // предмет пришли в query. Подставляем, ТОЛЬКО если они реально есть в списках
    // этого преподавателя — иначе чужой/опечатанный параметр молча выбрал бы то,
    // что сервер потом всё равно откажется отдавать.
    const qGroup = String(route.query.group || '')
    const qSubject = String(route.query.subject || '')
    group.value = (qGroup && groups.value.includes(qGroup)) ? qGroup : (groups.value[0] || '')
    subject.value = (qSubject && subjects.value.includes(qSubject)) ? qSubject : (subjects.value[0] || '')
  } catch { /* */ }
  await loadTerm()
  document.addEventListener('click', closeCtx)
})
onBeforeUnmount(() => document.removeEventListener('click', closeCtx))

async function load() {
  if (!group.value || !subject.value) { data.value = null; return }
  loading.value = true
  try {
    data.value = (await teacherApi.journal(group.value, subject.value)).data
  } catch {
    // ⚠️ НЕ обнуляем то, что уже показано. Прежде здесь стояло `data.value = null`, и
    // один неудачный запрос стирал журнал целиком — а без сети он неудачен постоянно.
    // Пустой экран вместо последних известных данных это ровно то, чего офлайн-режим
    // должен не допускать. Нет ни ответа, ни прежних данных — тогда честно пусто.
    if (!data.value) data.value = null
  } finally { loading.value = false }
}

/**
 * Занятия журнала: пришедшие с сервера ПЛЮС созданные без сети и ещё не уехавшие.
 *
 * 🔥 Это ВЫЧИСЛЯЕМОЕ значение, а не дописывание в `data.value`, и разница
 * принципиальная. Первая версия дописывала локальные занятия внутрь загруженного
 * ответа прямо в `load()` — то есть результат зависел от порядка выполнения, и любое
 * следующее присваивание `data.value` его затирало. На телефоне это выглядело так:
 * создаёшь занятие без сети — и СТАРЫЕ занятия пропадают, остаётся одно новое (живой
 * отзыв). Вычисляемый союз потерять нельзя в принципе: он пересобирается из двух
 * источников на каждое обращение.
 *
 * Без локальных занятий в списке созданная офлайн домашка не появлялась бы в таблице
 * вовсе (сервер о ней пока не знает), и преподаватель решил бы, что кнопка не сработала.
 * Ставить по такой колонке оценки можно сразу: очередь перепривяжет их к настоящему id,
 * как только занятие уедет.
 */
const allLessons = computed(() => {
  const fromServer = data.value?.lessons || []
  const known = new Set(fromServer.map((l) => l.id))
  const local = pendingLessons(group.value, subject.value)
    .filter((l) => !known.has(l.id))
    .map((l) => ({ number: 0, topic: '', date: '', hour: '', extra: {}, ...l, pending: true }))
  return local.length ? [...fromServer, ...local] : fromServer
})
watch([group, subject], load)

// ── Раздельное обучение (§ролей, 3.6.1) ──────────────────────────────────────────
// owned_subgroups: [1] или [2] — препод ведёт только свою (кнопок нет, всё неявно под
// неё); [1,2] — ведёт ОБЕ (маленький класс либо единственный назначенный) — тогда
// показываем 1ПГ/2ПГ/Совместно, и переключение фильтрует И ростер, И колонки занятий —
// иначе таблица выбранной подгруппы всё равно показывала бы чужих студентов сплошными
// прочерками (живой отзыв: «должны показываться студенты ЭТИХ подгрупп»).
const splitOwned = computed(() => data.value?.split?.owned_subgroups || [])
const hasSubgroupButtons = computed(() => splitOwned.value.length === 2)
// Подгруппа нового занятия/текущего вида: если кнопок нет, но предмет разделён —
// единственная своя; если кнопок нет и предмет не разделён — 0 (обычное занятие,
// сервер её всё равно проигнорирует, но не подставлять произвольное число чище).
const activeSubgroup = ref(0)
// 🔥 ВЫБОР ЧЕЛОВЕКА ПЕРЕЖИВАЕТ ПЕРЕЗАГРУЗКУ ЖУРНАЛА. `splitOwned` — computed поверх
// `data`, и он отдаёт НОВЫЙ массив при каждой загрузке, даже когда состав подгрупп не
// менялся. Vue сравнивает ссылки, поэтому watch срабатывал после КАЖДОГО обновления
// журнала — а простановка оценки как раз перезагружает его. Со стороны: выбрал 1ПГ,
// поставил оценку, и тебя выбросило в «Совместно», и так каждый раз.
// Сбрасываем только тогда, когда прежний выбор стал НЕВОЗМОЖЕН.
watch(splitOwned, (owned) => {
  if (!owned.length) { activeSubgroup.value = 0; return }         //предмет не разделён
  if (owned.length === 1) { activeSubgroup.value = owned[0]; return } //своя единственная
  if (activeSubgroup.value && !owned.includes(activeSubgroup.value)) activeSubgroup.value = 0
}, { immediate: true })
// Сколько студентов группы куратор ещё не расписал по подгруппам этого предмета.
// Молчать об этом нельзя: такой студент не попадает ни в одну подгруппу, и без явного
// предупреждения преподаватель не узнает, что человека в журнале не хватает.
const unassignedCount = computed(() => data.value?.split?.unassigned || 0)

function subgroupLabel(n) {
  return n === 1 ? locale.t('teacherJournal.subgroup1', '1ПГ')
       : n === 2 ? locale.t('teacherJournal.subgroup2', '2ПГ') : ''
}
async function pickSubgroup(n) {
  if (n === 0) {
    const ok = await confirm({
      title: locale.t('teacherJournal.combinedConfirmTitle', 'Совместное занятие'),
      message: locale.t('teacherJournal.combinedConfirmMessage',
        'Занятие будет создано СРАЗУ для обеих подгрупп (общая лекция/задание). Продолжить?'),
      okText: locale.t('teacherJournal.combinedConfirmOk', 'Да, совместное'),
    })
    if (!ok) return
  }
  activeSubgroup.value = n
}
// Ячейка НЕ применима к студенту, если занятие — чужой подгруппы (студент её не посещал).
// Актуально только для «Совместно» (activeSubgroup=0, видно всех сразу) — при выбранной
// конкретной подгруппе и ростер, и колонки уже сужены, чужих строк/столбцов там нет.
function cellApplies(s, col) {
  return !col.l.subgroup || !s.subgroup || col.l.subgroup === s.subgroup
}
// Ростер: при выбранной конкретной подгруппе — только её студенты; «Совместно» (0)
// или предмет не разделён — все, как раньше.
const visibleStudents = computed(() => {
  const all = data.value?.students || []
  if (!hasSubgroupButtons.value || activeSubgroup.value === 0) return all
  return all.filter((s) => s.subgroup === activeSubgroup.value)
})
// Занятия: та же логика, что у ростера — конкретная подгруппа сужает список до своих
// занятий + «Совместно» (subgroup=0 видно всем); «Совместно» выбрано или предмет не
// разделён — вообще всё, без сужения.
const visibleLessons = computed(() => {
  const all = allLessons.value        //с сервера + созданные без сети, см. allLessons
  const onlySubgroup = hasSubgroupButtons.value && activeSubgroup.value !== 0 ? activeSubgroup.value : 0
  if (!onlySubgroup) return all
  return all.filter((l) => !l.subgroup || l.subgroup === onlySubgroup)
})

// ── Колонки: занятие + его пересдачи (как col_defs в десктопе) ──────────────────
function retakeKey(l, ri) { return attemptKey(l.id, ri) }   //единый формат ключа (контракт)
function retakeDate(l, ri) { return ri === 1 ? l.retake_date : (l.extra || {})[`retake_date_${ri}`] || '' }
const colDefs = computed(() => {
  const out = []
  for (const l of visibleLessons.value) {
    out.push({ l, ri: 0, key: l.id })
    if (l.type === 'Экзамен') {
      for (let ri = 1; ri <= 5; ri++) {
        if (retakeDate(l, ri)) out.push({ l, ri, key: retakeKey(l, ri) })
      }
    }
  }
  return out
})

// ── Значения ячеек (селекты, как комбобоксы десктопа) ───────────────────────────
// Практика/ДЗ — варианты берутся из ШКАЛЫ преподавателя (data.scale_values, §ролей
// 3.3.1): «5» даёт как раньше 2-5, другая шкала — свои значения (100-балльная/буквенная/
// зачёт-незачёт). Экзамен намеренно вне кастомных шкал (см. grading.py) — там литеральная
// 2-5, как и было. «Н» у практики — не часть шкалы, отдельный маркер непредоставления.
const PRACTICE_TYPES = ['Практика', 'ДЗ']
const OPTIONS = {
  'Лекция': ['', '✓', 'Н', 'Б', 'О'],
  'default': ['', '2', '3', '4', '5', 'Н'],
}
// ⚠️ «Б»/«О» есть у ПРАКТИКИ, но не у ДЗ, и это не мелочь (01.09.2026).
// Практика — аудиторное занятие: студент может на ней болеть и опаздывать ровно так же,
// как на лекции. До этой правки поставить ему «Б» рукой было НЕЛЬЗЯ — вариантов в списке
// не было, — при том что сервер обе метки принимает (`grading.is_allowed_value`), а
// голосовая команда («Иванов болел», `voice_command._ABSENCE_B`) ставит их на любом типе
// занятия. То есть одно и то же действие было доступно голосом и недоступно рукой, а
// поставленные голосом отметки до 31.08.2026 ещё и молча терялись в подсчёте пропусков.
// ДЗ намеренно оставлено без них: домашняя работа делается ВНЕ аудитории, «Н» там значит
// «не сдал», а не «не был» — по той же причине ДЗ не идёт в посещаемость (`webdata.absences`).
const ATTENDANCE_MARKS = ['Н', 'Б', 'О']
function cellOptions(l) {
  if (l.type === 'Лекция') return OPTIONS['Лекция']
  if (l.type === 'Практика') return ['', ...scaleValues(data.value?.scale), ...ATTENDANCE_MARKS]
  if (PRACTICE_TYPES.includes(l.type)) return ['', ...scaleValues(data.value?.scale), 'Н']
  return OPTIONS.default
}
function rawValue(v) { return (v || '').split(' ')[0] }   // «5 (Зачтено)» → «5» в селекте
function needsRetake(s, col) { return needsRetakeShared(s.grades, col.l.id, col.ri) }

// Короткое ФИО для мобилки: «Иванчиков Егор Андреевич» → «Иванчиков Е.А.». Полная
// колонка ФИО с whitespace-nowrap на телефоне съедала почти всю ширину, и занятия не
// влезали. На десктопе (sm:) по-прежнему показываем полное ФИО.
function shortName(s) {
  const initials = (s.name || '').trim().split(/\s+/).filter(Boolean)
    .map((w) => w[0].toUpperCase() + '.').join('')
  return `${s.surname || ''} ${initials}`.trim()
}

function gradeClass(v, l) {
  const raw = rawValue(v)
  if (raw === '✓') return 'text-accent font-bold'
  if (raw === 'Б' || raw === 'О') return 'text-text3 font-semibold'
  if (raw === 'Н') return 'text-red font-bold'
  if (!raw) return 'text-text2'
  // Практика/ДЗ на кастомной шкале — цвет по КОНВЕРТИРОВАННОМУ 5-балльному значению
  // (100-балльная «85» тоже должна подсвечиваться как «хорошо»). Экзамен — как раньше,
  // литеральная 2-5 (шкалы туда намеренно не распространяются, см. grading.py).
  if (l && PRACTICE_TYPES.includes(l.type)) {
    const n = toFivePoint(raw, data.value?.scale)
    if (n === null) return 'text-text2'
    if (n >= 5) return 'text-accent font-bold'
    if (n >= 4) return 'text-blue font-bold'
    if (n >= 3) return 'text-orange font-bold'
    return 'text-red font-bold'
  }
  if (raw === '5') return 'text-accent font-bold'
  if (raw === '4') return 'text-blue font-bold'
  if (raw === '3') return 'text-orange font-bold'
  if (raw === '2') return 'text-red font-bold'
  return 'text-text2'
}
function avgClass(a) {
  const n = Number(a) || 0
  if (n >= 4.5) return 'text-accent'
  if (n >= 3.5) return 'text-blue'
  if (n >= 3) return 'text-orange'
  if (n > 0) return 'text-red'
  return 'text-text3'
}
const groupAverage = computed(() => {
  const vals = visibleStudents.value.map((s) => Number(averageOf(s)) || 0).filter((v) => v > 0)
  return vals.length ? (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2) : '—'
})

// ── Голосовые команды преподавателя («Иванову пять», «всей группе четыре») ─────────
// Разбор делает СЕРВЕР (общий модуль `voice_command.py`, тот же, что на десктопе), запись —
// обычный `setGrade` ниже. Отдельного «голосового» пути записи НЕТ намеренно: он означал
// бы вторую проверку прав и второй формат ключа оценки.
//
// ⚠️ Между «расслышал» и «записал» ВСЕГДА стоит подтверждение человеком. Распознавание
// ошибается тихо, и неверная оценка в журнале может остаться незамеченной надолго.
const voiceResult = ref(null)      // разобранная команда (открывает диалог)
const voiceWriting = ref(false)

async function onVoiceText(text) {
  if (!group.value || !subject.value) {
    toast.error(locale.t('teacherJournal.selectGroupSubjectFirst', 'Сначала выберите группу и предмет'))
    return
  }
  try {
    const { data } = await teacherApi.voiceCommand(text, group.value, subject.value)
    // Вопрос — не команда журнала: отправляем человека к «Вектору», а не пишем оценки.
    if (data.kind === 'question') {
      toast.info(locale.t('teacherJournal.voiceQuestionHint', 'Это вопрос — задайте его «Вектору» на вкладке помощника'))
      return
    }
    // Создание занятия голосом на сайте пока не поддержано: занятие заводится своей
    // формой (там тема, дата, номер и тип), и дублировать её в диалоге подтверждения
    // значило бы делать вторую форму создания. Показываем, что расслышали, честно.
    if (data.kind === 'lesson') {
      toast.info(locale.t('teacherJournal.voiceLessonHint', 'Создание занятия голосом пока только в программе — заведите его кнопкой «+»'))
      return
    }
    voiceResult.value = data
  } catch (e) {
    toast.error(e?.response?.data?.detail || locale.t('teacherJournal.voiceParseFailed', 'Не удалось разобрать команду'))
  }
}

async function writeVoiceItems(items) {
  const lessonId = voiceResult.value?.lesson_id
  if (!lessonId) return
  voiceWriting.value = true
  let ok = 0
  const failed = []
  try {
    // По одной записи, тем же эндпоинтом, что и ручная простановка. Последовательно, а не
    // пачкой: при сбое на середине важно знать, что именно записалось.
    for (const it of items) {
      try {
        await teacherApi.setGrade(it.surname, it.name, lessonId, it.grade)
        ok += 1
      } catch { failed.push(it.who) }
    }
    await load()
    if (failed.length) toast.error(locale.t('teacherJournal.voiceWritePartial', { ok, failed: failed.join(', ') }))
    else toast.success(locale.t('teacherJournal.voiceWriteSuccess', { ok }))
  } finally {
    voiceWriting.value = false
    voiceResult.value = null
  }
}

/** Стоит ли в этой клетке значение, которое ещё НЕ доехало до сервера. */
function isPending(s, col) {
  return pendingGrade(col.key, s.surname, s.name) !== undefined
}

/**
 * Средний балл студента с учётом оценок, которые ещё ждут отправки.
 *
 * Живой отзыв: «оценки ставит препод и нифига не видит, что как». Средний считает
 * сервер, и без сети он остаётся тем же, каким был до ввода, — преподаватель выставляет
 * восемь оценок, а колонка не шевелится. Пересчитываем на месте.
 *
 * ⚠️ Считаем ТЕМ ЖЕ `practiceAverage`, что и сервер: это построчный порт `grading.py`,
 * закреплённый общим контрактом (docs/contracts/grade-cases.json). Своей формулы здесь
 * нет и быть не может — у продукта нетривиальные пересдачи, шкалы преподавателей и
 * подгруппы, и вторая реализация разошлась бы с первой на первом же нестандартном
 * журнале. Пока сеть есть, показываем ровно то, что прислал сервер: он видит и то,
 * чего нет на экране (архив, другие подгруппы).
 */
function averageOf(s) {
  const overrides = {}
  for (const l of allLessons.value) {
    const p = pendingGrade(l.id, s.surname, s.name)
    if (p !== undefined) overrides[l.id] = p
  }
  if (!Object.keys(overrides).length) return s.average
  const items = allLessons.value.map((l) => [l.id, l.type])
  const records = { ...s.grades, ...overrides }
  return practiceAverage(items, records, data.value?.methodology_cfg || null,
                         data.value?.scale || '5')
}

// ── Запись оценки (селект → сервер → перезагрузка: средний считает сервер) ──────
async function setGrade(s, key, value) {
  const prev = s.grades[key] || ''
  if (value === rawValue(prev) && value !== '') return
  s.grades[key] = value
  saving.value = true
  try {
    await teacherApi.setGrade(s.surname, s.name, key, value)
    //Оценка ЗАПИСАНА. Второй случай из haptics.js — «действие значимо»: преподаватель в
    //этот момент смотрит на студента и на журнал, а не на всплывающие подтверждения, и
    //«получилось» должен узнать рукой. Отдача ПОСЛЕ ответа сервера, а не по нажатию:
    //иначе она подтверждала бы намерение, а не результат.
    haptics.success()
    await load()
  } catch (e) {
    // ⚠️ Различаем «сервер отказал» и «сервер не ответил» — это противоположные случаи.
    // Отказ (есть response) означает, что запись неверна: предмет не ваш, занятие
    // удалено — оценку надо вернуть как была, иначе экран покажет то, чего в базе нет.
    // Отсутствие ответа означает лишь, что нет сети. Откатывать здесь нельзя: работа
    // преподавателя пропала бы ровно в тот момент, когда он физически не может её
    // повторить. Такую оценку кладём в очередь — она уедет сама (см. api/outbox.js).
    if (!e?.response) {
      enqueueGrade({ surname: s.surname, name: s.name, lesson_id: key, grade: value })
      //Оценка не потеряна, но и не уехала — это НЕ успех и не отказ. Отдельного узора
      //заводить не стали: важнее, что ощущение отличается от «записано», а подробность
      //человек прочитает в подсказке.
      haptics.tap()
      toast.info(locale.t('teacherJournal.queuedOffline',
        'Нет сети — оценка сохранена и уйдёт, когда появится связь'))
      return
    }
    s.grades[key] = prev
    //Отказ сервера: оценка ОТКАТИЛАСЬ, и это надо почувствовать иначе, чем успех, —
    //две коротких с паузой против одной (см. haptics.js).
    haptics.error()
    toast.error(locale.t('teacherJournal.saveFailedPrefix', 'Не удалось сохранить: ') + (e?.response?.data?.detail || e.message))
  } finally { saving.value = false }
}

function plusDays(days) {
  const d = new Date(Date.now() + days * 86400000)
  return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()}`
}

// Экзамен — та же логика, что _set_exam_val на ПК.
async function setExamGrade(s, col, value) {
  if (value === '') { await setGrade(s, col.key, ''); return }
  let full = value
  let scheduleRetake = false
  if (value === '4' || value === '5') full = `${value} (Зачтено)`
  else if (value === '3') {
    const ok = await confirm({
      title: locale.t('teacherJournal.grade3Title', { surname: s.surname }),
      message: locale.t('teacherJournal.grade3Message', 'Засчитать или отправить на пересдачу?'),
      okText: locale.t('teacherJournal.credit', 'Зачесть'), cancelText: locale.t('teacherJournal.sendToRetake', 'На пересдачу'),
    })
    if (ok) full = '3 (Зачтено)'
    else { full = '3 (Не зачтено)'; scheduleRetake = true }
  } else if (value === '2' || value === 'Н') { full = `${value} (Не зачтено)`; scheduleRetake = true }
  await setGrade(s, col.key, full)
  if (scheduleRetake) {
    const nextRi = col.ri + 1
    if (nextRi <= 5 && !retakeDate(col.l, nextRi)) {
      const rd = await prompt({ title: locale.t('teacherJournal.retakeDateTitle', 'Дата пересдачи'), placeholder: locale.t('teacherJournal.datePlaceholder', 'дд.мм.гггг'), defaultValue: plusDays(7) })
      if (rd) {
        const payload = nextRi === 1 ? { retake_date: rd } : { [`retake_date_${nextRi}`]: rd }
        try { await teacherApi.updateLesson(col.l.id, payload); await load() }
        catch { toast.error(locale.t('teacherJournal.retakeAssignFailed', 'Не удалось назначить пересдачу')) }
      }
    }
  }
}
function onCell(s, col, value) {
  if (col.l.type === 'Экзамен' || col.ri > 0) setExamGrade(s, col, value)
  else setGrade(s, col.key, value)
}

// ── ПКМ-меню на заголовке занятия (изменить / пересдача / удалить) ──────────────
// 🔥 ТОТ ЖЕ ДЕФЕКТ, ЧТО В МЕССЕНДЖЕРЕ (найден при разборе жалобы 05.09.2026: «меню
// уезжает за край окна»). Здесь клампилась только ГОРИЗОНТАЛЬ, причём числом 200, а
// вертикаль бралась как есть — у занятия в нижней части журнала меню уходило под
// нижнюю кромку вместе с пунктом «Удалить занятие». Теперь положение считает общий
// `utils/menuPlacement.js` по ИЗМЕРЕННОМУ размеру: своя копия правила разошлась бы с
// мессенджерской на первой же правке.
const ctx = ref({ show: false, x: 0, y: 0, lesson: null })
const ctxBox = ref(null)
const ctxPos = ref(null)          // null — ещё не измерили
function placeCtx() {
  const el = ctxBox.value
  if (!el) return
  const r = el.getBoundingClientRect()
  ctxPos.value = placeMenu({
    x: ctx.value.x, y: ctx.value.y, w: r.width, h: r.height,
    vw: window.innerWidth, vh: window.innerHeight,
  })
}
function openCtx(e, l) {
  ctxPos.value = null              //первый кадр невидим: пока размер неизвестен, меню прыгнуло бы
  ctx.value = { show: true, x: e.clientX, y: e.clientY, lesson: l }
  nextTick(placeCtx)
}
function closeCtx() { if (ctx.value.show) ctx.value.show = false }
function ctxEdit() { openEditLesson(ctx.value.lesson); closeCtx() }
function ctxDelete() { const l = ctx.value.lesson; closeCtx(); delLesson(l) }
async function ctxRetake() {
  const l = ctx.value.lesson; closeCtx()
  const rd = await prompt({ title: locale.t('teacherJournal.retakeDateTitle', 'Дата пересдачи'), placeholder: locale.t('teacherJournal.datePlaceholder', 'дд.мм.гггг'), defaultValue: plusDays(7) })
  if (rd) { try { await teacherApi.updateLesson(l.id, { retake_date: rd }); await load() } catch { toast.error(locale.t('teacherJournal.retakeAssignFailed', 'Не удалось назначить пересдачу')) } }
}

// ── Занятия: создание и правка (дата — сегодня по умолчанию) ────────────────────
const LESSON_TYPES = ['Практика', 'Лекция', 'ДЗ', 'Экзамен', 'Семинар', 'Лабораторная', 'Зачёт']
// У ДЗ «тема» — это текст самого задания, он же уходит студенту в уведомление.
const topicLabel = computed(() => (lessonForm.value.type === 'ДЗ' ? locale.t('teacherJournal.topicHomework', 'Домашнее задание') : locale.t('teacherJournal.topicFull', 'Тема (полностью)')))
const topicPlaceholder = computed(() => (lessonForm.value.type === 'ДЗ' ? locale.t('teacherJournal.topicPlaceholderHomework', 'создать базу данных') : locale.t('teacherJournal.topicPlaceholderDefault', 'Тема занятия')))

// Следующий номер — МАКСИМУМ+1 внутри типа, как считают сервер и десктоп.
// Не количество строк: лекция лежит в базе двумя строками (по академическому часу),
// и счётчик выдавал бы каждой следующей лекции номер вдвое больше настоящего.
// При раздельном обучении — ОТДЕЛЬНО в пределах ТЕКУЩЕЙ подгруппы (activeSubgroup),
// иначе у двух преподавателей одной группы независимые «Практика №1» сливались бы в
// одну серию и сбивали счёт друг другу (тот же принцип, что и на сервере).
function nextNumber(t) {
  const nums = allLessons.value
    .filter((l) => l.type === t && (l.subgroup || 0) === activeSubgroup.value)
    .map((l) => l.number || 0)
  return nums.length ? Math.max(...nums) + 1 : 1
}
const showLesson = ref(false)
const editingLesson = ref(null)   // null — создание, иначе id занятия
const lessonForm = ref({ type: 'Практика', number: 1, topic: '', date: '', hour: 0, retake_date: '' })
const savingLesson = ref(false)
const lessonError = ref('')

function openLesson() {
  editingLesson.value = null
  lessonForm.value = { type: 'Практика', number: nextNumber('Практика'), topic: '', date: plusDays(0), hour: 0, retake_date: '' }
  lessonError.value = ''
  showLesson.value = true
}
function openEditLesson(l) {
  editingLesson.value = l.id
  lessonForm.value = { type: l.type, number: l.number, topic: l.topic || '', date: l.date || '', hour: l.hour || 0, retake_date: l.retake_date || '' }
  lessonError.value = ''
  showLesson.value = true
}
watch(() => lessonForm.value.type, (t) => {
  if (editingLesson.value) return
  lessonForm.value.number = nextNumber(t)
})
async function saveLesson() {
  if (!group.value || !subject.value) { lessonError.value = locale.t('teacherJournal.selectGroupSubject', 'Выберите группу и предмет'); return }
  savingLesson.value = true
  lessonError.value = ''
  try {
    if (editingLesson.value) {
      const f = lessonForm.value
      await teacherApi.updateLesson(editingLesson.value, {
        topic: f.topic, date: f.date, number: f.number, hour: f.hour, retake_date: f.retake_date,
      })
    } else {
      await teacherApi.createLesson({
        group: group.value, subject: subject.value, subgroup: activeSubgroup.value, ...lessonForm.value,
      })
    }
    showLesson.value = false
    await load()
  } catch (e) {
    // Нет ответа — нет сети. Занятие (в том числе домашку) кладём в очередь: она
    // выдаст ему временный id, журнал сразу покажет колонку, и по ней уже можно
    // ставить оценки — очередь перепривяжет их к настоящему id после отправки.
    if (!e?.response) {
      const f = lessonForm.value
      if (editingLesson.value) {
        enqueueLessonUpdate(editingLesson.value, {
          topic: f.topic, date: f.date, number: f.number, hour: f.hour, retake_date: f.retake_date,
        })
      } else {
        enqueueLessonCreate({
          group: group.value, subject: subject.value, subgroup: activeSubgroup.value, ...f,
        })
      }
      showLesson.value = false
      toast.info(locale.t('teacherJournal.queuedOffline',
        'Нет сети — сохранено и уйдёт, когда появится связь'))
      await load()
      return
    }
    lessonError.value = e?.response?.data?.detail || locale.t('teacherJournal.saveLessonFailed', 'Не удалось сохранить занятие')
  } finally { savingLesson.value = false }
}
async function delLesson(l) {
  const ok = await confirm({
    title: locale.t('teacherJournal.deleteLessonTitle', { type: l.type, number: l.number }),
    message: locale.t('teacherJournal.deleteLessonMessage', 'Оценки этой пары перестанут учитываться.'),
    okText: locale.t('common.delete'), danger: true,
  })
  if (!ok) return
  // Занятие, которое само ещё не уехало на сервер, удаляется прямо из очереди —
  // создавать его на сервере, чтобы тут же удалить, незачем (а создание ДЗ ещё и
  // рассылает письма студентам).
  if (isTempId(l.id)) { enqueueLessonDelete(l.id); await load(); return }
  try { await teacherApi.deleteLesson(l.id); await load() }
  catch (e) {
    if (!e?.response) {
      enqueueLessonDelete(l.id)
      toast.info(locale.t('teacherJournal.queuedOffline',
        'Нет сети — сохранено и уйдёт, когда появится связь'))
      await load()
      return
    }
    toast.error(e?.response?.data?.detail || locale.t('teacherJournal.deleteLessonFailed', 'Не удалось удалить'))
  }
}

// ── Экспорт журнала/ведомости с выбором формата (Excel / Word) ──────────────────
const exporting = ref(false)
const fmtMenu = ref({ show: false, kind: '' })   // kind: 'journal' | 'vedomost'
function openFmt(kind) { fmtMenu.value = { show: true, kind } }
async function pickFmt(fmt) {
  const kind = fmtMenu.value.kind
  fmtMenu.value.show = false
  if (kind === 'journal') await downloadJournal(fmt)
  else await downloadVedomost(fmt)
}
function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name.replaceAll('/', '-')
  a.click()
  URL.revokeObjectURL(url)
}
async function downloadJournal(fmt) {
  exporting.value = true
  try {
    const { data: blob } = await teacherApi.journalExport(group.value, subject.value, { fmt })
    saveBlob(blob, `Журнал_${group.value}_${subject.value}.${fmt === 'docx' ? 'docx' : 'xlsx'}`)
  } catch (e) {
    let detail = ''
    try { detail = JSON.parse(await e?.response?.data?.text())?.detail || '' } catch { /* */ }
    toast.error(locale.t('teacherJournal.exportJournalFailed', 'Не удалось выгрузить журнал') + (detail ? `: ${detail}` : ''))
  } finally { exporting.value = false }
}

// ── Аттестация: итоговые оценки за семестр + ведомость ──────────────────────────
const showAtt = ref(false)
const attRows = ref([])
const attForm = ref('экзамен')
const attSaving = ref(false)
const ATT_FORMS = ['экзамен', 'зачёт', 'диффзачёт']
const ATT_GRADES = ['', '5', '4', '3', '2', 'Зачтено', 'Не зачтено']

// ⚠️ ВТОРОГО ИСТОЧНИКА ДАННЫХ ЗДЕСЬ НЕТ. Средний балл и оценки берём из УЖЕ
// загруженного журнала (`data.value`), а не отдельной ручкой: два расчёта среднего
// разошлись бы, и преподаватель увидел бы в журнале одно число, а в аттестации другое —
// причём заметил бы это в тот момент, когда решает судьбу семестра.
const examLessons = computed(() =>
  (data.value?.lessons || []).filter((l) => l.type === 'Экзамен'))
// Предмет БЕЗ экзамена аттестуется как раньше (зачёт/диффзачёт): требовать экзаменационную
// оценку там, где экзамена нет, значило бы закрыть аттестацию таким предметам совсем.
const subjectHasExam = computed(() => examLessons.value.length > 0)

// Все оценки студента по этому предмету — для колонки «оценки» в аттестации: по ним
// человек и решает, дотягивает ли средний. Пропуски (Н/Б/О) сюда не идут — это не баллы.
function studentMarks(s) {
  const out = []
  for (const l of (data.value?.lessons || [])) {
    const v = (s.grades || {})[l.id]
    if (v && !['Н', 'Б', 'О'].includes(v)) out.push({ v, type: l.type, date: l.date })
  }
  return out
}
// Экзаменационная оценка — включая пересдачи (ключи <id>_retake[_N], как в десктопе):
// решает ПОСЛЕДНЯЯ, иначе допуск считался бы по заваленной первой попытке.
function examMark(s) {
  let last = ''
  for (const l of examLessons.value) {
    for (const key of [l.id, `${l.id}_retake`, `${l.id}_retake_2`, `${l.id}_retake_3`,
                       `${l.id}_retake_4`, `${l.id}_retake_5`]) {
      const v = (s.grades || {})[key]
      if (v && !['Н', 'Б', 'О'].includes(v)) last = v
    }
  }
  return last
}

async function openAtt() {
  try {
    const tg = (await teacherApi.termGrades(group.value, subject.value)).data.grades || {}
    attRows.value = (data.value?.students || []).map((s) => ({
      surname: s.surname, name: s.name,
      grade: tg[`${s.surname}|${s.name}`]?.grade || '',
      //Снимок исходного значения: по нему видно, у кого семестр УЖЕ закрыт (и, значит,
      //текущие оценки заперты), а кому итоговую только предстоит выставить.
      was: tg[`${s.surname}|${s.name}`]?.grade || '',
      average: s.average,
      marks: studentMarks(s),
      exam: examMark(s),
    }))
    showAtt.value = true
  } catch { toast.error(locale.t('teacherJournal.loadFinalGradesFailed', 'Не удалось загрузить итоговые оценки')) }
}
async function saveAtt() {
  attSaving.value = true
  try {
    for (const r of attRows.value) {
      const row = {
        group: group.value, subject: subject.value,
        surname: r.surname, name: r.name, grade: r.grade, form: attForm.value,
      }
      try {
        await teacherApi.setTermGrade(row)
      } catch (e) {
        // Связи нет — дальше по списку идти незачем, она не появится к следующему
        // студенту. Кладём В ОЧЕРЕДЬ ВСЮ ведомость, включая ещё не пройденных: иначе
        // половина группы уехала бы, а половина потерялась, и разобраться, где
        // остановились, было бы нельзя.
        if (!e?.response) {
          attRows.value.slice(attRows.value.indexOf(r)).forEach((rest) => enqueueTermGrade({
            group: group.value, subject: subject.value,
            surname: rest.surname, name: rest.name, grade: rest.grade, form: attForm.value,
          }))
          showAtt.value = false
          toast.info(locale.t('teacherJournal.queuedOffline',
            'Нет сети — сохранено и уйдёт, когда появится связь'))
          return
        }
        throw e
      }
    }
    showAtt.value = false
    //Журнал перечитываем: у кого итоговая теперь стоит, тому ячейки заперты (сервер
    //ответит 409), и человек должен видеть актуальное состояние, а не то, что было до
    //сохранения. Без этого он попробует поставить балл и получит отказ «из ниоткуда».
    await load()
    toast.success(locale.t('teacherJournal.finalGradesSaved', 'Итоговые оценки сохранены'))
  } catch (e) { toast.error(locale.t('teacherJournal.saveFailedPrefix', 'Не удалось сохранить: ') + (e?.response?.data?.detail || e.message)) }
  finally { attSaving.value = false }
}
async function downloadVedomost(fmt) {
  try {
    const { data: blob } = await teacherApi.vedomost(group.value, subject.value, { fmt, form: attForm.value })
    saveBlob(blob, `Ведомость_${group.value}_${subject.value}.${fmt === 'docx' ? 'docx' : 'xlsx'}`)
  } catch { toast.error(locale.t('teacherJournal.exportVedomostFailed', 'Не удалось выгрузить ведомость')) }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-2 sm:gap-3">
      <select v-model="group" class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent sm:w-auto">
        <option v-for="g in groups" :key="g" :value="g">{{ g }}</option>
      </select>
      <select v-model="subject" :title="subject"
              class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent sm:w-auto sm:min-w-52 sm:max-w-md">
        <option v-for="s in subjects" :key="s" :value="s">{{ s }}</option>
      </select>
      <!-- Голосом можно ставить оценки и отмечать пропуски. Кнопка здесь, а не у
           «Вектора»: команде нужны группа и предмет, а они выбраны именно на этой
           странице. Тумблер и выбор микрофона — в настройках. -->
      <MicButton @text="onVoiceText" @error="(m) => toast.error(m)" />
      <span v-if="saving" class="text-xs font-medium text-accent sm:self-center">{{ locale.t('teacherJournal.saving', 'Сохранение…') }}</span>
      <span v-else-if="data" class="text-xs text-text3 sm:self-center">
        {{ locale.t('teacherJournal.countsLine', { students: visibleStudents.length, lessons: visibleLessons.length }) }}
        <!-- План часов задаёт админ. Не задан (total=0) — строку не показываем вовсе -->
        <span v-if="data.hours?.total" class="text-accent">
          · {{ locale.t('teacherJournal.hoursProgress', { done: data.hours.done, total: data.hours.total }) }}
        </span>
      </span>
      <!-- ⚠️ НА ТЕЛЕФОНЕ 2x2, а не одна строка. Четыре кнопки в ряд делят ~312 px (360
           минус поля и три промежутка) — по 78 px на «🎓 Аттестация», и последняя не
           влезала (живой отзыв). Уменьшать текст бессмысленно: он и так короткий, а
           подписи нужны — иконки без слов на редких действиях не читаются. С `sm`
           места хватает, и кнопки возвращаются в одну строку, как были. -->
      <div v-if="group && subject" class="grid w-full grid-cols-2 gap-2 sm:ml-auto sm:flex sm:w-auto">
        <AppButton variant="ghost" size="sm" class="sm:flex-none" :disabled="!data?.students?.length" @click="openAtt">🎓 {{ locale.t('teacherJournal.attButton', 'Аттестация') }}</AppButton>
        <AppButton variant="ghost" size="sm" class="sm:flex-none" :disabled="!data?.students?.length" @click="openFmt('vedomost')">📄 {{ locale.t('teacherJournal.vedomostButton', 'Ведомость') }}</AppButton>
        <AppButton variant="ghost" size="sm" class="sm:flex-none" :disabled="exporting || !data?.students?.length" @click="openFmt('journal')">
          {{ exporting ? locale.t('teacherJournal.exportingButton', 'Выгрузка…') : `📘 ${locale.t('teacherJournal.journalButton', 'Журнал')}` }}
        </AppButton>
        <AppButton variant="green" size="sm" class="sm:flex-none" @click="openLesson">+ {{ locale.t('teacherJournal.addLessonButton', 'Занятие') }}</AppButton>
      </div>
    </div>

    <!-- Раздельное обучение (§ролей, 3.6.1): показываются, только если ЭТОТ преподаватель
         ведёт ОБЕ подгруппы — выбор определяет, для кого создастся следующее занятие. -->
    <div v-if="hasSubgroupButtons" class="flex items-center gap-1.5">
      <span class="text-xs text-text3">{{ locale.t('teacherJournal.subgroupPickerLabel', 'Занятие для:') }}</span>
      <div class="flex overflow-hidden rounded-sm border border-border2">
        <button type="button" class="px-3 py-1.5 text-xs font-medium transition-colors"
                :class="activeSubgroup === 1 ? 'bg-accent text-white' : 'bg-card2 text-text2 hover:bg-bg2'"
                @click="pickSubgroup(1)">{{ locale.t('teacherJournal.subgroup1', '1ПГ') }}</button>
        <button type="button" class="border-l border-border2 px-3 py-1.5 text-xs font-medium transition-colors"
                :class="activeSubgroup === 2 ? 'bg-accent text-white' : 'bg-card2 text-text2 hover:bg-bg2'"
                @click="pickSubgroup(2)">{{ locale.t('teacherJournal.subgroup2', '2ПГ') }}</button>
        <button type="button" class="border-l border-border2 px-3 py-1.5 text-xs font-medium transition-colors"
                :class="activeSubgroup === 0 ? 'bg-accent text-white' : 'bg-card2 text-text2 hover:bg-bg2'"
                @click="pickSubgroup(0)">{{ locale.t('teacherJournal.subgroupCombined', 'Совместно') }}</button>
      </div>
    </div>

    <!-- Нераспределённые студенты. Раньше такой человек просто не появлялся в журнале —
         ни строки, ни причины: добавили студента в группу с раздельным обучением, и он
         исчез. Тихая пропажа человека из журнала хуже любой неудобной надписи, поэтому
         говорим о ней прямо и называем, кто это чинит. -->
    <div v-if="unassignedCount" class="flex items-start gap-2 rounded-sm border border-yellow-500/40 bg-yellow-500/10 px-3 py-2 text-xs text-text2">
      <span aria-hidden="true">⚠️</span>
      <span>{{ locale.t('teacherJournal.unassignedWarning', { n: unassignedCount }) }}</span>
    </div>

    <EmptyState v-if="!groups.length || !subjects.length" :title="locale.t('teacherJournal.noWorkloadTitle', 'Нет нагрузки')"
                :message="locale.t('teacherJournal.noWorkloadMessage', 'За вами не закреплены группы или предметы.')" />
    <p v-else-if="loading" class="text-sm text-text3">{{ locale.t('common.loading') }}</p>
    <EmptyState v-else-if="!data?.students?.length" :title="locale.t('teacherJournal.noStudentsTitle', 'Нет студентов')" :message="locale.t('teacherJournal.noStudentsMessage', { group })" />
    <EmptyState v-else-if="!visibleStudents.length" :title="locale.t('teacherJournal.emptySubgroupTitle', 'Подгруппа пуста')"
                :message="locale.t('teacherJournal.emptySubgroupMessage', 'Куратор ещё не распределил сюда студентов.')" />
    <EmptyState v-else-if="!colDefs.length" :title="locale.t('teacherJournal.emptyJournalTitle', 'Журнал пуст')"
                :message="locale.t('teacherJournal.emptyJournalMessage', 'Добавьте первое занятие кнопкой «+ Занятие» — колонки появятся здесь.')" />

    <!-- Таблица компактная и выровнена влево: обёртка не растягивает table (w-max). -->
    <StickyXScroll v-else class="rounded-lg border border-border bg-card shadow-card">
      <!-- w-max (без min-w-full): таблица ровно по содержимому и прижата ВЛЕВО, не
           расползается к центру при 1–2 занятиях. -->
      <table class="w-max text-sm">
        <thead>
          <tr class="border-b-2 border-accent bg-bg2 text-text2">
            <th class="sticky left-0 z-10 bg-bg2 px-2 py-3 text-left text-tiny font-semibold uppercase tracking-wide sm:px-4">{{ locale.t('teacherJournal.colStudent', 'Студент') }}</th>
            <th v-for="col in colDefs" :key="col.key"
                class="w-24 border-l border-border align-top px-2 py-2 sm:w-32"
                :class="col.ri === 0 ? 'cursor-context-menu' : ''"
                :title="col.ri === 0 ? locale.t('teacherJournal.editHint', 'ПКМ или двойной клик — изменить занятие') : ''"
                @contextmenu.prevent="col.ri === 0 && openCtx($event, col.l)"
                @dblclick="col.ri === 0 && openEditLesson(col.l)">
              <template v-if="col.ri > 0">
                <div class="text-xs font-bold text-orange">{{ locale.t('teacherJournal.retakeNumber', { n: col.ri }) }}</div>
                <div class="text-[11px] font-normal normal-case text-text3">{{ retakeDate(col.l, col.ri) }}</div>
              </template>
              <template v-else>
                <div class="text-xs font-bold text-text">
                  {{ col.l.type }} №{{ col.l.number }}<span v-if="col.l.type === 'Лекция' && col.l.hour" class="font-normal text-text3"> ({{ col.l.hour }}ч)</span>
                  <span v-if="col.l.subgroup" class="ml-1 rounded bg-accent/15 px-1 py-0.5 text-[10px] font-bold normal-case text-accent">{{ subgroupLabel(col.l.subgroup) }}</span>
                </div>
                <div class="text-[11px] font-normal normal-case text-text3">{{ col.l.date || '—' }}</div>
                <div v-if="col.l.topic" class="mt-0.5 line-clamp-2 text-[11px] font-normal normal-case leading-snug text-text2" :title="col.l.topic">
                  {{ col.l.topic }}
                </div>
                <div class="mt-1 flex items-center justify-center gap-3 text-text3">
                  <button class="hover:text-accent" :title="locale.t('teacherJournal.edit', 'Изменить')" @click.stop="openEditLesson(col.l)">✎</button>
                  <button class="hover:text-red" :title="locale.t('common.delete')" @click.stop="delLesson(col.l)">✕</button>
                </div>
              </template>
            </th>
            <th class="border-l-2 border-accent/40 px-4 py-3 text-right text-tiny font-semibold uppercase tracking-wide">{{ locale.t('teacherJournal.colAverage', 'Средн.') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(s, i) in visibleStudents" :key="i"
              class="border-b border-border last:border-0 hover:bg-accent-glow/40" :class="i % 2 ? 'bg-bg2/50' : ''">
            <td class="sticky left-0 z-10 max-w-[7.5rem] truncate px-2 py-2 text-left font-medium text-text sm:max-w-none sm:whitespace-nowrap sm:px-4"
                :class="i % 2 ? 'bg-bg2' : 'bg-card'" :title="`${s.surname} ${s.name}`">
              <!-- Мобилка: «Фамилия И.О.» (полное — в подсказке). Десктоп: полное ФИО. -->
              <span class="sm:hidden">{{ shortName(s) }}</span>
              <span class="hidden sm:inline">{{ s.surname }} {{ s.name }}</span>
            </td>
            <td v-for="col in colDefs" :key="col.key" class="border-l border-border px-1.5 py-1.5 text-center">
              <span v-if="!cellApplies(s, col)" class="text-text3" :title="locale.t('teacherJournal.otherSubgroupHint', 'Другая подгруппа')">·</span>
              <span v-else-if="col.ri > 0 && !needsRetake(s, col)" class="text-text3">—</span>
              <!-- Пунктирная жёлтая рамка = оценка ещё НЕ на сервере (нет сети). Без
                   пометки офлайн-клетка выглядит точно так же, как сохранённая, и
                   преподаватель закрыл бы журнал в уверенности, что всё уехало. -->
              <select v-else :value="rawValue(s.grades[col.key])"
                      :class="[gradeClass(s.grades[col.key], col.l),
                               isPending(s, col) ? 'border-dashed border-yellow' : 'border-border2']"
                      :title="isPending(s, col)
                        ? locale.t('teacherJournal.cellPending', 'Ещё не отправлено на сервер')
                        : (s.grades[col.key] || '')"
                      class="h-9 w-14 cursor-pointer rounded-sm border bg-card2 text-center text-sm outline-none transition-colors hover:border-accent focus:border-accent disabled:cursor-default disabled:opacity-70"
                      @change="onCell(s, col, $event.target.value)">
                <option v-for="o in cellOptions(col.l)" :key="o" :value="o">{{ o || '·' }}</option>
              </select>
            </td>
            <td class="border-l-2 border-accent/20 px-4 py-2 text-right font-title text-base font-bold" :class="avgClass(averageOf(s))">
              {{ averageOf(s) || '—' }}
            </td>
          </tr>
          <tr class="border-t-2 border-accent/40 bg-bg2/70">
            <td class="sticky left-0 z-10 max-w-[7.5rem] truncate bg-bg2 px-2 py-2.5 text-right text-xs font-semibold uppercase text-text3 sm:max-w-none sm:px-4">{{ locale.t('teacherJournal.groupAverageRow', 'Средний по группе') }}</td>
            <td :colspan="colDefs.length" class="px-2 py-2.5"></td>
            <td class="border-l-2 border-accent/40 px-4 py-2.5 text-right font-title text-base font-extrabold" :class="avgClass(groupAverage)">{{ groupAverage }}</td>
          </tr>
        </tbody>
      </table>
    </StickyXScroll>

    <!-- ЛЕГЕНДА МЕТОК. Заведена в 3.7.6 вместе с переназначением «О» (было «уважительная
         причина», стало «опоздал»). Раньше смысл букв не был написан НИГДЕ в журнале —
         преподаватель выбирал их из списка по догадке, и именно так «О» и разъехалось
         с тем, что считал сервер. Подпись стоит там, где метку ставят. -->
    <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-tiny text-text3">
      <span><b class="text-accent">✓</b> {{ locale.t('teacherJournal.legendPresent', 'был на занятии') }}</span>
      <span><b class="text-red">Н</b> {{ locale.t('teacherJournal.legendN', 'неявка — идёт в пропуски') }}</span>
      <span><b>Б</b> {{ locale.t('teacherJournal.legendB', 'болезнь — идёт в пропуски') }}</span>
      <span><b>О</b> {{ locale.t('teacherJournal.legendO', 'опоздал — в пропуски НЕ идёт') }}</span>
    </div>

    <!-- Контекстное меню (ПКМ) -->
    <div v-if="ctx.show" ref="ctxBox"
         class="fixed z-50 min-w-48 overflow-y-auto rounded-lg border border-border2 bg-card py-1 shadow-card transition-opacity"
         :class="ctxPos ? 'opacity-100' : 'opacity-0'"
         :style="{ left: (ctxPos ? ctxPos.left : ctx.x) + 'px', top: (ctxPos ? ctxPos.top : ctx.y) + 'px',
                   maxHeight: menuMaxHeight(typeof window === 'undefined' ? 800 : window.innerHeight) + 'px' }"
         @click.stop>
      <button class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2" @click="ctxEdit">✎ {{ locale.t('teacherJournal.ctxEditTopic', 'Изменить тему / дату') }}</button>
      <button v-if="ctx.lesson?.type === 'Экзамен'" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2" @click="ctxRetake">📅 {{ locale.t('teacherJournal.ctxAssignRetake', 'Назначить пересдачу') }}</button>
      <div class="my-1 border-t border-border" />
      <button class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red hover:bg-red/10" @click="ctxDelete">🗑 {{ locale.t('teacherJournal.ctxDeleteLesson', 'Удалить занятие') }}</button>
    </div>

    <!-- Модалка занятия: создание и правка (дата — сегодня по умолчанию) -->
    <div v-if="showLesson" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="showLesson = false">
      <div class="w-full max-w-sm rounded-lg border border-border bg-card p-5 shadow-card">
        <h3 class="mb-4 font-title text-lg font-bold text-text">
          {{ editingLesson ? locale.t('teacherJournal.editLessonTitle', 'Изменить занятие') : locale.t('teacherJournal.newLessonTitle', 'Новое занятие') }} · {{ group }}
        </h3>
        <div class="space-y-3">
          <div class="grid grid-cols-2 gap-3">
            <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('teacherJournal.typeLabel', 'Тип') }}</span>
              <select v-model="lessonForm.type" :disabled="!!editingLesson"
                      class="h-10 w-full rounded-sm border border-border2 bg-card2 px-2 text-sm text-text outline-none focus:border-accent disabled:opacity-60">
                <option v-for="t in LESSON_TYPES" :key="t" :value="t">{{ t }}</option>
              </select></label>
            <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('teacherJournal.numberLabel', '№') }}</span>
              <input v-model.number="lessonForm.number" type="number" min="1"
                     class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" /></label>
          </div>
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ topicLabel }}</span>
            <textarea v-model="lessonForm.topic" rows="2" :placeholder="topicPlaceholder"
                      class="w-full resize-none rounded-sm border border-border2 bg-card2 px-3 py-2 text-sm text-text outline-none focus:border-accent"></textarea></label>
          <div class="grid grid-cols-2 gap-3">
            <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('teacherJournal.dateLabel', 'Дата') }}</span>
              <input v-model="lessonForm.date" :placeholder="locale.t('teacherJournal.datePlaceholder', 'дд.мм.гггг')"
                     class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" /></label>
            <label v-if="lessonForm.type === 'Лекция'" class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('teacherJournal.hoursLabel', 'Часы') }}</span>
              <input v-model.number="lessonForm.hour" type="number" min="0"
                     class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" /></label>
            <label v-if="lessonForm.type === 'Экзамен'" class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('teacherJournal.retakeLabel', 'Пересдача') }}</span>
              <input v-model="lessonForm.retake_date" :placeholder="locale.t('teacherJournal.datePlaceholder', 'дд.мм.гггг')"
                     class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" /></label>
          </div>
          <p v-if="lessonError" class="text-sm text-red">{{ lessonError }}</p>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <AppButton variant="ghost" size="sm" @click="showLesson = false">{{ locale.t('common.cancel') }}</AppButton>
          <AppButton variant="green" size="sm" :disabled="savingLesson" @click="saveLesson">
            {{ savingLesson ? locale.t('teacherJournal.saving', 'Сохранение…') : (editingLesson ? locale.t('common.save') : locale.t('teacherJournal.add', 'Добавить')) }}
          </AppButton>
        </div>
      </div>
    </div>

    <!-- Модалка аттестации: итоговые оценки за семестр + форма контроля + ведомость -->
    <div v-if="showAtt" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="showAtt = false">
      <div class="flex max-h-[85vh] w-full max-w-lg flex-col rounded-lg border border-border bg-card p-5 shadow-card">
        <h3 class="mb-1 font-title text-lg font-bold text-text">🎓 {{ locale.t('teacherJournal.attTitle', 'Итоговые оценки за семестр') }}</h3>
        <p class="mb-3 text-xs text-text3">{{ group }} · {{ subject }} · {{ termLabel(currentTerm) }}</p>
        <label class="mb-3 block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('teacherJournal.controlFormLabel', 'Форма контроля') }}</span>
          <select v-model="attForm" class="h-9 w-full rounded-sm border border-border2 bg-card2 px-2 text-sm text-text outline-none focus:border-accent">
            <option v-for="f in ATT_FORMS" :key="f" :value="f">{{ f }}</option>
          </select>
        </label>
        <!-- 🔒 Замок зачётки: выставленная итоговая закрывает семестр по предмету, и
             текущие оценки этому студенту больше не пишутся (сервер отвечает 409).
             Говорим об этом ДО сохранения, а не после отказа в журнале. -->
        <p class="mb-2 rounded-sm border border-border2 bg-bg2 px-2.5 py-2 text-[11px] leading-snug text-text3">
          {{ locale.t('teacherJournal.attLockHint', 'Выставленная итоговая закрывает семестр: новые текущие оценки и посещаемость по этому предмету записать будет нельзя. Чтобы снова открыть — снимите итоговую («—»).') }}
        </p>

        <div class="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
          <div v-for="(r, i) in attRows" :key="i"
               class="rounded-sm border border-border2/60 px-2 py-1.5"
               :class="r.was ? 'bg-bg2/60' : ''">
            <div class="flex items-center gap-2">
              <span class="min-w-0 flex-1 truncate text-sm text-text">{{ r.surname }} {{ r.name }}</span>
              <!-- Средний балл рядом с полем — ровно то, ради чего диалог и открывают:
                   «дотягивает ли до пятёрки». Держать его на другом экране значит
                   заставить человека помнить число, пока он выбирает оценку. -->
              <span class="shrink-0 text-xs font-semibold"
                    :class="r.average >= 4.5 ? 'text-green' : r.average >= 3.5 ? 'text-accent' : 'text-text3'">
                {{ locale.t('teacherJournal.attAvg', 'ср.') }} {{ Number(r.average || 0).toFixed(2) }}
              </span>
              <select v-model="r.grade"
                      :disabled="subjectHasExam && !r.exam && !r.was"
                      :title="subjectHasExam && !r.exam && !r.was ? locale.t('teacherJournal.attNeedsExam', 'Сначала выставьте оценку за экзамен') : ''"
                      class="h-9 w-28 shrink-0 rounded-sm border border-border2 bg-card2 px-2 text-sm text-text outline-none focus:border-accent disabled:opacity-40">
                <option v-for="g in ATT_GRADES" :key="g" :value="g">{{ g || '—' }}</option>
              </select>
            </div>
            <div class="mt-1 flex flex-wrap items-center gap-1 pl-0.5">
              <span v-if="r.exam" class="rounded-sm bg-accent-glow px-1.5 py-0.5 text-[11px] font-semibold text-accent">
                {{ locale.t('teacherJournal.attExam', 'экзамен') }}: {{ r.exam }}
              </span>
              <span v-else-if="subjectHasExam" class="rounded-sm bg-bg2 px-1.5 py-0.5 text-[11px] text-text3">
                {{ locale.t('teacherJournal.attNoExam', 'экзамен не сдан') }}
              </span>
              <!-- Сами оценки: по ним видно, из чего сложился средний, — одна «5» при
                   среднем 4.4 читается иначе, чем ровный ряд четвёрок. -->
              <span v-for="(mk, j) in r.marks" :key="j"
                    class="rounded-sm bg-bg2 px-1.5 py-0.5 text-[11px] text-text2" :title="`${mk.type} · ${mk.date}`">{{ mk.v }}</span>
              <span v-if="!r.marks.length" class="text-[11px] text-text3">{{ locale.t('teacherJournal.attNoMarks', 'оценок нет') }}</span>
            </div>
          </div>
        </div>
        <div class="mt-4 flex flex-wrap justify-end gap-2">
          <AppButton variant="ghost" size="sm" @click="showAtt = false">{{ locale.t('common.close') }}</AppButton>
          <AppButton variant="ghost" size="sm" @click="openFmt('vedomost')">📄 {{ locale.t('teacherJournal.vedomostButton', 'Ведомость') }}</AppButton>
          <AppButton variant="green" size="sm" :disabled="attSaving" @click="saveAtt">{{ attSaving ? locale.t('teacherJournal.saving', 'Сохранение…') : locale.t('common.save') }}</AppButton>
        </div>
      </div>
    </div>

    <!-- Выбор формата экспорта: Excel или Word -->
    <div v-if="fmtMenu.show" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4" @click.self="fmtMenu.show = false">
      <div class="w-full max-w-xs rounded-lg border border-border bg-card p-5 shadow-card">
        <h3 class="mb-1 font-title text-base font-bold text-text">{{ locale.t('teacherJournal.formatTitle', 'В каком формате?') }}</h3>
        <p class="mb-4 text-xs text-text3">{{ fmtMenu.kind === 'journal' ? locale.t('teacherJournal.journalWord', 'Журнал успеваемости') : locale.t('teacherJournal.vedomostButton', 'Ведомость') }} · {{ group }} · {{ subject }}</p>
        <div class="flex flex-col gap-2">
          <AppButton variant="ghost" @click="pickFmt('xlsx')">📊 {{ locale.t('teacherJournal.excelOption', 'Excel (.xlsx)') }}</AppButton>
          <AppButton variant="ghost" @click="pickFmt('docx')">📄 {{ locale.t('teacherJournal.wordOption', 'Word (.docx)') }}</AppButton>
        </div>
      </div>
    </div>

    <!-- Подтверждение голосовой команды: между «расслышал» и «записал». -->
    <VoiceCommandDialog v-if="voiceResult" :result="voiceResult" :busy="voiceWriting"
                        @confirm="writeVoiceItems" @cancel="voiceResult = null" />
  </div>
</template>
