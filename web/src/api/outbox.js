/**
 * outbox.js — всё, что преподаватель сделал БЕЗ СЕТИ, и выгрузка этого на сервер.
 *
 * Идея взята у десктопного синка (`sync/sync_runner.py`), но НЕ его реализация, и
 * различие принципиальное. Десктоп держит полноценную копию базы и сливает её
 * построчно, потому что умеет считать средние, часы и долги сам. Телефон этого не
 * умеет и не должен: вся методика живёт в одном месте — на сервере (`grading.py` и
 * далее). Повторить её в JavaScript значило бы завести ВТОРУЮ формулу среднего балла,
 * то есть ровно то, что запрещено инвариантом продукта. Поэтому здесь очередь
 * НАМЕРЕНИЙ («поставить оценку», «создать домашку»), а не копия базы.
 *
 * ━━ ЧТО УМЕЕТ ━━
 *   grade          оценка И ПОСЕЩАЕМОСТЬ (Н/Б/О) — на сервере это один эндпоинт;
 *   lesson.create  новое занятие любого типа, включая ДЗ;
 *   lesson.update  правка занятия (тема, дата, номер);
 *   lesson.delete  удаление занятия;
 *   term           итоговая оценка за семестр (аттестация).
 * То есть тот же набор, что преподаватель делает в журнале на десктопе.
 *
 * ━━ ПОЧЕМУ ЭТО БЕЗОПАСНО ━━
 * • Записи уходят ТЕМИ ЖЕ эндпоинтами, что и с сайта. Отдельного «мобильного» пути
 *   записи нет, значит нет и второй проверки прав: сервер как проверял назначение
 *   преподавателя на предмет и группу, так и проверяет.
 * • Метку времени для LWW ставит СЕРВЕР в момент приёма (инвариант §4.3). Часам
 *   телефона мы не верим вовсе — их владелец волен перевести, и правка «из будущего»
 *   выиграла бы у чужой свежей.
 * • Ключ оценки на сервере детерминирован (`grade_id(student_id, lesson_id)`), запись
 *   идёт upsert'ом: повтор БЕЗВРЕДЕН. Связь оборвалась после отправки, но до ответа —
 *   отправим ещё раз и получим то же самое, а не дубль.
 * • Очередь привязана к ЛОГИНУ автора и выгружается, только когда вошёл он же. На
 *   общем телефоне следующий вошедший не отправит чужие записи под своим именем.
 *
 * ━━ ВРЕМЕННЫЕ id ━━
 * Занятие получает id на СЕРВЕРЕ. Создавая домашку офлайн, мы не знаем его заранее, но
 * оценки по ней преподаватель ставит тут же — им нужно на что-то ссылаться. Поэтому
 * занятие получает временный `tmp:…`, а после успешного создания мы переписываем ВСЕ
 * ждущие записи, которые на него ссылались, на настоящий id. Переписываем сразу и
 * сохраняем: если связь оборвётся следующим же запросом, занятие на сервере уже есть,
 * и оценки обязаны знать его настоящий id — иначе они вечно бились бы о 404.
 *
 * ━━ ЧТО С КОНФЛИКТАМИ ━━
 * Пока преподаватель был офлайн, ту же клетку мог поправить кто-то ещё. Разрешает это
 * сервер по LWW, и наша запись приходит позже — значит побеждает она. Это осознанно:
 * человек видел клетку своими глазами, а «свежесть» чужой правки ничего не говорит о
 * её правоте. А вот отказ по СУЩЕСТВУ (предмет больше не ваш, занятие удалено) повторять
 * бессмысленно — такие записи уходят в список `rejected` и показываются человеку. Молча
 * выбрасывать их нельзя: для преподавателя это потерянная работа, о которой он не узнает.
 */
import { computed, ref, watch } from 'vue'

// Расширения «.js» здесь обязательны: очередь проверяется голым `node --test`, мимо
// сборщика, а он путь без расширения не разрешает. Тот же случай уже был с grading.js.
import { online } from './offlineSession.js'

const LS_PREFIX = 'gb.outbox.'          // НЕ 'gb.cache.' — переживает выход из аккаунта
const LS_REJECTED = 'gb.outbox.rejected.'
const MAX_TRIES = 5
const TMP = 'tmp:'

/** Очередь текущего автора (реактивная копия того, что лежит в localStorage). */
export const pending = ref([])
/** Записи, которые сервер отверг по существу — их надо показать человеку. */
export const rejected = ref([])
/** Идёт ли выгрузка прямо сейчас (чтобы не запустить две разом). */
export const flushing = ref(false)

/**
 * 🔥 ЗАПИСЬ, КОТОРАЯ ПРЯМО СЕЙЧАС ЛЕТИТ НА СЕРВЕР (07.09.2026).
 *
 * Дефект, ради которого заведено. Проверка `if (!pending.value.includes(entry)) continue`
 * закрывала только один случай — запись убрали ДО её очереди. А главная дыра была
 * ВНУТРИ `await send(entry)`: запрос уже ушёл со снимком полей, и всё, что человек делал
 * следующие полсекунды, пропадало молча.
 *   • правка: отправка началась → преподаватель исправил тему занятия → сервер сохранил
 *     ПРЕЖНЮЮ, а очередь опустела. Правки больше нет нигде;
 *   • удаление: удалённое временное занятие всё равно создавалось на сервере — и
 *     оставалось там навсегда, потому что удалять было уже нечего.
 *
 * Лечится ВЕРСИЕЙ операции (`rev`) плюс состоянием «в полёте». Изменение во время
 * полёта не пытается догнать уже ушедший запрос — оно превращается в СЛЕДУЮЩУЮ
 * операцию, которая уйдёт, когда станет известен настоящий id занятия.
 */
let inFlight = null            // { key, rev } — что именно сейчас в сети
//Временные занятия, которые человек удалил, пока их создание летело на сервер.
//Настоящего id ещё нет, поэтому удаление ждёт его здесь.
const deleteAfterCreate = new Set()

/** Только для тестов: что сейчас в полёте. */
export function _inFlight() { return inFlight }

export const pendingCount = computed(() => pending.value.length)

/** Похоже ли это на занятие, которого на сервере ещё нет. */
export function isTempId(id) {
  return typeof id === 'string' && id.startsWith(TMP)
}

function newTempId() {
  const rnd = (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`)
  return `${TMP}${rnd}`
}

function currentLogin() {
  try {
    return JSON.parse(localStorage.getItem('gb.user') || 'null')?.login || ''
  } catch {
    return ''
  }
}

function load(key, target) {
  const login = currentLogin()
  if (!login) { target.value = []; return }
  try {
    target.value = JSON.parse(localStorage.getItem(key + login) || '[]')
  } catch {
    target.value = []
  }
}

function save(key, source) {
  const login = currentLogin()
  if (!login) return
  try {
    localStorage.setItem(key + login, JSON.stringify(source.value))
  } catch { /* квота переполнена — очередь короткая, терять нечего */ }
}

/** Перечитать очередь текущего пользователя (после входа/смены аккаунта). */
export function reloadOutbox() {
  load(LS_PREFIX, pending)
  load(LS_REJECTED, rejected)
}
reloadOutbox()

function persist() {
  pending.value = [...pending.value].sort((a, b) => a.seq - b.seq)
  save(LS_PREFIX, pending)
}

/**
 * Положить операцию в очередь, схлопнув повтор по тому же ключу.
 *
 * ⚠️ Схлопывание обязательно. Преподаватель, поправивший одну и ту же оценку трижды,
 * должен отправить последнее значение, а не три подряд: сервер применил бы их по
 * очереди, а при обрыве посередине оставил бы не то, что человек видел на экране.
 * Хранить надо намерение, а не историю нажатий.
 */
function enqueue(kind, key, payload) {
  const prev = pending.value.find((e) => e.key === key)
  const rest = pending.value.filter((e) => e.key !== key)
  rest.push({
    kind,
    key,
    payload,
    seq: prev?.seq ?? Date.now() + rest.length,   // порядок постановки переживает правку
    //Версия НАМЕРЕНИЯ. Схлопывание повторов (см. выше) переписывает payload на месте, и
    //без счётчика отличить «то же самое» от «человек успел поправить» нечем: объект тот
    //же, поля другие. Именно на этом и терялась правка, сделанная во время отправки.
    rev: (prev?.rev ?? 0) + 1,
    queuedAt: Date.now(),
    tries: 0,
    lastError: '',
  })
  pending.value = rest
  persist()
  return key
}

// ───────────────────────── постановка операций ─────────────────────────

/** Оценка ИЛИ отметка посещаемости (Н/Б/О) — на сервере это одно и то же. */
export function enqueueGrade({ surname, name, lesson_id, grade }) {
  return enqueue('grade', `grade|${lesson_id}|${surname}|${name}`,
    { surname, name, lesson_id, grade: grade ?? '' })
}

/**
 * Новое занятие (в том числе ДЗ). Возвращает ВРЕМЕННЫЙ id — на него уже можно
 * ставить оценки, очередь сама перепривяжет их к настоящему после отправки.
 */
export function enqueueLessonCreate(payload) {
  const tempId = newTempId()
  enqueue('lesson.create', `lesson.create|${tempId}`, { ...payload, __tempId: tempId })
  return tempId
}

/**
 * Правка занятия. Если оно ещё само не уехало на сервер, правка ВЛИВАЕТСЯ в его
 * создание: отправлять «создай, а теперь поправь» бессмысленно, а на сервере такого
 * занятия для правки пока и нет.
 */
export function enqueueLessonUpdate(id, payload) {
  if (isTempId(id)) {
    const create = pending.value.find((e) => e.key === `lesson.create|${id}`)
    if (create) {
      create.payload = { ...create.payload, ...payload }
      //⚠️ Версию двигаем ВСЕГДА, а не только когда запись в полёте. Проверять «летит ли
      //прямо сейчас» пришлось бы в каждом месте правки — то есть завести ещё одно
      //место, где однажды забудут. Счётчик дешевле любой проверки.
      create.rev = (create.rev ?? 0) + 1
      persist()
      return create.key
    }
  }
  return enqueue('lesson.update', `lesson.update|${id}`, { id, ...payload })
}

/**
 * Удаление занятия. Занятие, которое ещё не уехало, просто ИСЧЕЗАЕТ из очереди вместе
 * со всеми оценками по нему: создавать его на сервере, чтобы тут же удалить, — лишний
 * круг и лишний шум в чужих уведомлениях (создание ДЗ рассылает письма студентам).
 */
export function enqueueLessonDelete(id) {
  if (isTempId(id)) {
    //🔥 Занятие уже летит на сервер — «просто убрать из очереди» здесь НЕПРАВДА: оно
    //там появится, а удалять будет нечем. Запоминаем намерение и исполним его, как
    //только сервер вернёт настоящий id.
    if (inFlight && inFlight.key === `lesson.create|${id}`) {
      deleteAfterCreate.add(id)
    }
    pending.value = pending.value.filter((e) => !referencesLesson(e, id))
    persist()
    return ''
  }
  return enqueue('lesson.delete', `lesson.delete|${id}`, { id })
}

/** Итоговая оценка за семестр (аттестация). */
export function enqueueTermGrade(payload) {
  const { group, subject, surname, name } = payload
  return enqueue('term', `term|${group}|${subject}|${surname}|${name}`, payload)
}

// ───────────────────────── чтение очереди интерфейсом ─────────────────────────

/** Значение, ожидающее отправки для этой клетки, или undefined. */
export function pendingGrade(lesson_id, surname, name) {
  return pending.value.find((e) => e.key === `grade|${lesson_id}|${surname}|${name}`)
    ?.payload.grade
}

/**
 * Занятия, созданные без сети и ещё не уехавшие. Журналу они нужны, чтобы показать
 * колонку: иначе преподаватель создал бы домашку офлайн и не увидел, куда ставить
 * оценки, — то есть фича существовала бы только на бумаге.
 */
export function pendingLessons(group, subject) {
  return pending.value
    .filter((e) => e.kind === 'lesson.create'
      && (!group || e.payload.group === group)
      && (!subject || e.payload.subject === subject))
    .map((e) => ({ ...e.payload, id: e.payload.__tempId, pending: true }))
}

/** Ссылается ли запись на это занятие (само занятие или оценка по нему). */
function referencesLesson(entry, lessonId) {
  if (entry.kind === 'lesson.create') return entry.payload.__tempId === lessonId
  if (entry.kind === 'grade') return entry.payload.lesson_id === lessonId
  if (entry.kind === 'lesson.update' || entry.kind === 'lesson.delete') {
    return entry.payload.id === lessonId
  }
  return false
}

/** Убрать запись из отвергнутых (человек её посмотрел). */
export function dismissRejected(key) {
  rejected.value = rejected.value.filter((e) => e.key !== key)
  save(LS_REJECTED, rejected)
}

/** Полностью очистить очередь текущего автора. Только по явной команде человека. */
export function clearOutbox() {
  pending.value = []
  rejected.value = []
  deleteAfterCreate.clear()
  save(LS_PREFIX, pending)
  save(LS_REJECTED, rejected)
}

function reject(entry, reason) {
  rejected.value = [...rejected.value, { ...entry, reason, rejectedAt: Date.now() }]
  save(LS_REJECTED, rejected)
}

// ───────────────────────── отправка ─────────────────────────

/**
 * Кто именно отправляет запись. Обычно — тот же axios-клиент, что и весь сайт; в
 * тестах подменяется. Импорт ленивый НАМЕРЕННО: очередь не должна тянуть за собой
 * HTTP-клиент, который при загрузке модуля лезет в localStorage и в адрес сервера, —
 * иначе её нельзя проверить иначе как в живом браузере, а сторож, который негде
 * запустить, не пишется вовсе.
 */
let sender = null
export function setSender(fn) { sender = fn }

async function send(entry) {
  if (sender) return sender(entry)
  const { api } = await import('./client.js')
  const p = entry.payload
  switch (entry.kind) {
    case 'grade':
      return api.post('/web/teacher/grade', p)
    case 'lesson.create': {
      const body = { ...p }
      delete body.__tempId
      return api.post('/web/teacher/lesson', body)
    }
    case 'lesson.update': {
      const body = { ...p }
      delete body.id
      return api.put(`/web/teacher/lesson/${encodeURIComponent(p.id)}`, body)
    }
    case 'lesson.delete':
      return api.delete(`/web/teacher/lesson/${encodeURIComponent(p.id)}`)
    case 'term':
      return api.post('/web/teacher/term-grade', p)
    default:
      throw new Error(`неизвестный тип записи: ${entry.kind}`)
  }
}

/**
 * Занятие уехало и получило настоящий id — переписываем всех, кто на него ссылался.
 * Сразу и с сохранением: оборвись связь на следующем запросе, оценки уже знают, куда
 * им идти, и не будут вечно биться о несуществующий `tmp:…`.
 */
function remapTempId(tempId, realId) {
  for (const e of pending.value) {
    if (e.kind === 'grade' && e.payload.lesson_id === tempId) {
      e.payload.lesson_id = realId
      e.key = `grade|${realId}|${e.payload.surname}|${e.payload.name}`
    } else if ((e.kind === 'lesson.update' || e.kind === 'lesson.delete')
               && e.payload.id === tempId) {
      e.payload.id = realId
      e.key = `${e.kind}|${realId}`
    }
  }
  persist()
}

/**
 * Выгрузить очередь. Строго последовательно и в порядке постановки: занятие обязано
 * уехать раньше оценок по нему, а параллельные запросы с телефона на одноядерный
 * сервер ничего не ускорят, зато усложнят разбор, если что-то пойдёт не так.
 *
 * Возвращает {sent, failed, rejected}.
 */
export async function flushOutbox() {
  if (flushing.value) return { sent: 0, failed: 0, rejected: 0 }
  reloadOutbox()
  if (!pending.value.length) return { sent: 0, failed: 0, rejected: 0 }

  flushing.value = true
  const stats = { sent: 0, failed: 0, rejected: 0 }
  try {
    for (const entry of [...pending.value].sort((a, b) => a.seq - b.seq)) {
      // Запись могли переписать или удалить, пока шёл предыдущий запрос.
      if (!pending.value.includes(entry)) continue
      const sentRev = entry.rev ?? 0
      inFlight = { key: entry.key, rev: sentRev }
      try {
        const resp = await send(entry)
        //🔑 СНАЧАЛА разбираемся, что произошло с записью ПОКА мы ждали ответ, и только
        //потом убираем её из очереди. Прежний порядок («убрали, потом разобрались»)
        //и терял правку: она была применена к объекту, которого уже нет в очереди.
        const changed = (entry.rev ?? 0) !== sentRev
        pending.value = pending.value.filter((e) => e !== entry)
        if (entry.kind === 'lesson.create') {
          const realId = resp?.data?.id
          if (realId) {
            remapTempId(entry.payload.__tempId, realId)
            //Занятие удалили, пока оно летело: теперь id известен — удаляем по-настоящему.
            if (deleteAfterCreate.delete(entry.payload.__tempId)) {
              enqueue('lesson.delete', `lesson.delete|${realId}`, { id: realId })
            } else if (changed) {
              //Человек поправил занятие, пока летело создание. Сервер сохранил прежние
              //поля — досылаем правку отдельной операцией, уже по настоящему id.
              const body = { ...entry.payload }
              delete body.__tempId
              enqueue('lesson.update', `lesson.update|${realId}`, { id: realId, ...body })
            }
          } else {
            // Сервер принял, но id не вернул — редкость, однако тогда оценки по этому
            // занятию отправить некуда. Честно признаём это, а не шлём их в никуда.
            dropDependents(entry.payload.__tempId,
              'занятие создано, но сервер не вернул его номер')
            deleteAfterCreate.delete(entry.payload.__tempId)
          }
        }
        //⚠️ ВЕТКИ «обычная запись изменилась в полёте» здесь НЕТ, и это не забывчивость.
        //Обычный `enqueue` не правит запись на месте: он выбрасывает прежнюю по ключу и
        //кладёт НОВЫЙ объект. Значит правка оценки во время отправки уже уцелела сама —
        //старый объект уходит из очереди, свежий остаётся и уедет следующим кругом.
        //Мутируется на месте РОВНО ОДИН случай — слияние правки с ещё не уехавшим
        //созданием занятия (`enqueueLessonUpdate` по временному id), и именно он выше.
        //Дописать сюда «на всякий случай» ещё одну ветку значило бы отправить оценку
        //дважды и завести код, который невозможно ни проверить, ни отладить.
        persist()
        stats.sent += 1
      } catch (err) {
        const status = err?.response?.status
        if (!status) {
          // Ответа нет — связь опять пропала. Прекращаем: остальное ждёт следующего
          // раза. Ошибкой записи это не считается, она не отвергнута.
          stats.failed += 1
          break
        }
        if (status === 401 || (status === 403 && !entry.tries)) {
          // 401 — сессия кончилась, выгружать нечем; 403 на ПЕРВОЙ попытке чаще всего
          // тоже про сессию или устройство, а не про предмет. Останавливаемся и ждём,
          // пока человек войдёт заново.
          entry.tries += 1
          entry.lastError = `HTTP ${status}`
          persist()
          break
        }
        if (status >= 400 && status < 500) {
          // Отказ по существу: занятие удалено, предмет больше не ваш, студента нет в
          // группе. Повторять нечего — сколько ни шли, ответ будет тот же.
          pending.value = pending.value.filter((e) => e !== entry)
          reject(entry, err?.response?.data?.detail || `HTTP ${status}`)
          stats.rejected += 1
          if (entry.kind === 'lesson.create') {
            stats.rejected += dropDependents(entry.payload.__tempId,
              'занятие не создано, оценка по нему не имеет смысла')
          }
          persist()
          continue
        }
        // 5xx — сервер жив, но ему сейчас плохо. Пробуем ещё, но не бесконечно: запись,
        // которую сервер стабильно не принимает, обязана стать видимой.
        entry.tries += 1
        entry.lastError = `HTTP ${status}`
        if (entry.tries >= MAX_TRIES) {
          pending.value = pending.value.filter((e) => e !== entry)
          reject(entry, `сервер не принял после ${MAX_TRIES} попыток (HTTP ${status})`)
          stats.rejected += 1
        } else {
          stats.failed += 1
        }
        persist()
        break
      }
    }
  } finally {
    inFlight = null
    flushing.value = false
  }
  return stats
}

/** Снять с очереди всё, что ссылалось на несостоявшееся занятие. */
function dropDependents(tempId, reason) {
  const doomed = pending.value.filter((e) => referencesLesson(e, tempId))
  if (!doomed.length) return 0
  pending.value = pending.value.filter((e) => !doomed.includes(e))
  doomed.forEach((e) => reject(e, reason))
  persist()
  return doomed.length
}

/**
 * Выгружать, как только вернулась связь.
 *
 * Именно по ПЕРЕХОДУ офлайн -> онлайн, а не по таймеру: пока сети нет, попытки только
 * жгут батарею и ничего не меняют.
 */
export function startOutboxWatch() {
  watch(online, (isOnline, was) => {
    if (isOnline && !was) flushOutbox().catch(() => {})
  })
}
