// outboxInFlightRace.test.mjs — правка и удаление ВО ВРЕМЯ отправки не пропадают.
//
// 🔥 Дефект (07.09.2026). Очередь проверяла `if (!pending.includes(entry)) continue` —
// но только ПЕРЕД отправкой. Дыра была внутри самого `await send(entry)`: запрос уже
// ушёл со снимком полей, и всё, что человек делал следующие полсекунды, исчезало молча.
//   • правка: отправка началась → преподаватель исправил тему занятия → сервер сохранил
//     ПРЕЖНЮЮ, а очередь опустела. Правки не осталось нигде;
//   • удаление: удалённое временное занятие всё равно создавалось на сервере — и жило
//     там навсегда, потому что удалять было уже нечего.
//
// Окно узкое, поэтому ни один прогон его случайно не поймает: воспроизводим НАРОЧНО —
// задерживаем ответ сервера и правим очередь ровно в этот момент.
import { test } from 'node:test'
import assert from 'node:assert/strict'

function memoryStorage() {
  const m = new Map()
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    clear: () => m.clear(),
    key: (i) => [...m.keys()][i] ?? null,
    get length() { return m.size },
  }
}
globalThis.localStorage = memoryStorage()
globalThis.window = { localStorage: globalThis.localStorage }

const outbox = await import('../src/api/outbox.js')

function loginAs(login) {
  localStorage.setItem('gb.user', JSON.stringify({ login, role: 'teacher' }))
  outbox.reloadOutbox()
}

/** Отправитель, который ЗАВИСАЕТ на первом запросе, пока мы не отпустим. */
function pausingSender(realId = 'les-99') {
  const sent = []
  let release
  const held = new Promise((r) => { release = r })
  let first = true
  outbox.setSender(async (entry) => {
    sent.push({ kind: entry.kind, payload: { ...entry.payload } })
    if (first) { first = false; await held }
    return { data: { id: entry.kind === 'lesson.create' ? realId : undefined } }
  })
  return { sent, release: () => release() }
}

test('правка занятия во время отправки доезжает отдельной операцией', async () => {
  loginAs('teach1')
  outbox.clearOutbox()
  const tmp = outbox.enqueueLessonCreate({ group: 'К-24', subject: 'Физика', topic: 'Старая' })

  const s = pausingSender()
  const flush = outbox.flushOutbox()
  await new Promise((r) => setTimeout(r, 0))          // дать запросу уйти в «сеть»

  // ← ровно тот момент: создание в полёте, человек правит тему
  outbox.enqueueLessonUpdate(tmp, { topic: 'Исправленная' })
  s.release()
  await flush

  const themes = s.sent.map((x) => x.payload.topic)
  assert.deepEqual(themes, ['Старая'], 'первый запрос ушёл со старым снимком — это норма')

  // А вот что НЕ норма: чтобы исправление на этом и кончилось.
  const left = outbox.pending.value
  assert.equal(left.length, 1, 'исправление обязано остаться в очереди')
  assert.equal(left[0].kind, 'lesson.update')
  assert.equal(left[0].payload.id, 'les-99', 'правка адресуется НАСТОЯЩИМ id занятия')
  assert.equal(left[0].payload.topic, 'Исправленная')
  assert.ok(!('__tempId' in left[0].payload), 'временный id наружу не уезжает')
})

test('удаление занятия во время его создания доводится до конца', async () => {
  loginAs('teach1')
  outbox.clearOutbox()
  const tmp = outbox.enqueueLessonCreate({ group: 'К-24', subject: 'Физика', topic: 'ДЗ' })

  const s = pausingSender()
  const flush = outbox.flushOutbox()
  await new Promise((r) => setTimeout(r, 0))

  outbox.enqueueLessonDelete(tmp)      // ← передумал, пока создание летело
  s.release()
  await flush

  const left = outbox.pending.value
  assert.equal(left.length, 1,
    'занятие создано на сервере — удаление обязано его догнать, а не потеряться')
  assert.equal(left[0].kind, 'lesson.delete')
  assert.equal(left[0].payload.id, 'les-99')
})

test('ничего не трогали — лишних операций не появляется', async () => {
  loginAs('teach1')
  outbox.clearOutbox()
  outbox.enqueueLessonCreate({ group: 'К-24', subject: 'Физика', topic: 'Тема' })

  const s = pausingSender()
  const flush = outbox.flushOutbox()
  await new Promise((r) => setTimeout(r, 0))
  s.release()
  await flush

  assert.equal(outbox.pending.value.length, 0,
    'без правок очередь обязана опустеть — иначе починка гонки завела вечный цикл отправки')
})

test('правка оценки во время отправки уцелевала и раньше — проверяем, что не сломали', async () => {
  loginAs('teach1')
  outbox.clearOutbox()
  outbox.enqueueGrade({ surname: 'Иванов', name: 'Пётр', lesson_id: 'les-1', grade: '3' })

  const s = pausingSender()
  const flush = outbox.flushOutbox()
  await new Promise((r) => setTimeout(r, 0))
  outbox.enqueueGrade({ surname: 'Иванов', name: 'Пётр', lesson_id: 'les-1', grade: '5' })
  s.release()
  await flush

  const left = outbox.pending.value
  assert.equal(left.length, 1, 'свежая оценка обязана остаться в очереди')
  assert.equal(left[0].payload.grade, '5')
  // ⚠️ И ровно ОДНА: обычный enqueue кладёт новый объект вместо прежнего, поэтому
  // дополнительная «страховочная» ветка отправила бы оценку дважды.
  assert.equal(left.filter((e) => e.kind === 'grade').length, 1, 'дубля быть не должно')
})

test('версия операции растёт при каждой правке — на ней всё и держится', () => {
  loginAs('teach1')
  outbox.clearOutbox()
  const tmp = outbox.enqueueLessonCreate({ group: 'К-24', subject: 'Физика', topic: 'A' })
  const e = outbox.pending.value[0]
  const v0 = e.rev
  outbox.enqueueLessonUpdate(tmp, { topic: 'B' })
  assert.ok(e.rev > v0,
    'слияние правки правит payload НА МЕСТЕ — без счётчика отличить его нечем')
})
