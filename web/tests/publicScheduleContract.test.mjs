// publicScheduleContract.test.mjs — страница расписания БЕЗ входа читает то, что сервер
// на самом деле отдаёт.
//
// 🔥 ЗАЧЕМ (12.09.2026). Страница не показывала расписание НИКОГДА — и не из-за вёрстки:
// она читала `resp.days`, а сервер такого ключа не кладёт вовсе (расписание лежит в
// `resp.schedule.weeks`). Список дней выходил пустым всегда, и экран честно писал «На этой
// неделе пар нет» — то есть отказ выглядел как ответ. Вторым ключом была неделя:
// `/public/week` отдаёт `{date, week}`, а читалось `week.parity`.
//
// ⚠️ ЭТОГО НЕ ВИДЯТ НИ СБОРКА, НИ ЛИНТЕР, НИ ТЕСТЫ ПОВЕДЕНИЯ: обращение к несуществующему
// полю в JS законно и молча даёт `undefined`. Поймать можно только сверкой ДВУХ сторон —
// чем этот сторож и занимается: ключи он берёт из ЖИВОГО кода обеих половин, а не из
// списка у себя (список разошёлся бы с продуктом на первой же правке).
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const root = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const PY = readFileSync(root + 'server/app/routers/publicschedule.py', 'utf8')
const PAGE = readFileSync(root + 'web/src/pages/PublicSchedule.vue', 'utf8')
const ENDPOINTS = readFileSync(root + 'web/src/api/endpoints.js', 'utf8')

/**
 * Убрать комментарии.
 *
 * ⚠️ БЕЗ ЭТОГО СТОРОЖ ЛОВИТ САМ СЕБЯ. Шапка страницы ОБЯЗАНА объяснять, что читалось
 * `resp.days` и почему это было неверно, — иначе следующий читатель вернёт дефект. Разбор,
 * считающий пояснение кодом, краснеет на правильном файле и заставляет вычеркнуть урок:
 * тот же приём и та же причина, что у `test_translate_never_reaches_the_network`, где
 * докстринг вырезается перед поиском сетевых вызовов.
 */
function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, ' ')        // блочные /* */ и шапка страницы
    .replace(/<!--[\s\S]*?-->/g, ' ')         // пояснения в разметке
    .replace(/(^|[^:])\/\/[^\n]*/gm, '$1 ')   // строчные //, но не «https://»
}

const CODE = stripComments(PAGE)

/** Ключи словаря, который РЕАЛЬНО возвращает функция сервера. */
function serverReturnKeys(fnName) {
  const start = PY.indexOf('def ' + fnName + '(')
  assert.ok(start > 0, `на сервере нет функции ${fnName}`)
  const nextDef = PY.indexOf('\ndef ', start + 5)
  const body = PY.slice(start, nextDef > 0 ? nextDef : PY.length)
  const keys = new Set()
  for (const m of body.matchAll(/return\s*\{([\s\S]*?)\}/g)) {
    for (const k of m[1].matchAll(/"(\w+)"\s*:/g)) keys.add(k[1])
  }
  assert.ok(keys.size > 0, `не удалось разобрать ответ ${fnName}`)
  return keys
}

/** Поля ответа, которые ЧИТАЕТ страница. */
function fieldsReadBy(source) {
  const out = new Set()
  // `resp` — ответ ручки расписания в load(); `data` — он же, положенный в ref.
  for (const m of source.matchAll(/\bresp\??\.(\w+)/g)) out.add(m[1])
  for (const m of source.matchAll(/\bdata\.value\??\.(\w+)/g)) out.add(m[1])
  // В разметке ref разворачивается сам: `data.group`.
  for (const m of source.matchAll(/\bdata\.(\w+)/g)) if (m[1] !== 'value') out.add(m[1])
  return out
}

test('страница читает только те поля, которые сервер кладёт в ответ', () => {
  const server = serverReturnKeys('public_schedule')
  const foreign = [...fieldsReadBy(CODE)].filter((k) => !server.has(k))
  assert.deepEqual(foreign, [],
    `страница читает поля, которых сервер не отдаёт: ${foreign.join(', ')}. ` +
    `Сервер кладёт: ${[...server].join(', ')}`)
})

test('расписание берётся из schedule.weeks, а не из выдуманного days', () => {
  // Дословная форма дефекта: именно так и было написано, и именно поэтому экран молчал.
  assert.ok(!/resp\??\.days|data\.value\??\.days/.test(CODE),
    'страница снова читает resp.days — такого ключа сервер не отдаёт')
  assert.ok(CODE.includes('schedule?.weeks'),
    'страница обязана брать пары из schedule.weeks')
})

test('номер недели читается ключом week, а не parity', () => {
  const week = serverReturnKeys('public_week')
  assert.ok(week.has('week'), '/public/week обязан отдавать ключ week')
  assert.ok(!week.has('parity'), 'ключа parity у /public/week нет и не было')
  assert.ok(!/\.parity\b/.test(CODE), 'страница снова читает .parity — такого ключа нет')
  assert.ok(/publicScheduleApi\.week\(\)\)\.data\?\.week/.test(CODE),
    'неделя обязана читаться как .data?.week')
})

test('каждая ручка, которую зовёт клиент, существует на сервере', () => {
  // «Обещание без вызывающего» наоборот: клиент зовёт адрес, которого нет, и получает
  // заглушку SPA либо 404 — молча, потому что ответ обрабатывается в catch.
  const from = ENDPOINTS.indexOf('export const publicScheduleApi')
  assert.ok(from > 0, 'в endpoints.js нет publicScheduleApi')
  // ⚠️ Границу берём по ЗАКРЫВАЮЩЕЙ СКОБКЕ объекта, а не «первые N символов». С окном в
  // 900 знаков запас был одна строка: дописанный в блок комментарий вытолкнул бы
  // последний вызов наружу, и сторож остался бы ЗЕЛЁНЫМ, молча перестав сверять эту
  // ручку с сервером. Тот же класс, что «допуск шире поломки».
  const end = ENDPOINTS.indexOf('\n}', from)
  assert.ok(end > from, 'не нашёл конец объекта publicScheduleApi')
  const block = ENDPOINTS.slice(from, end)
  const called = [...block.matchAll(/rawApi\.get\('(\/public\/[\w/]+)'/g)].map((m) => m[1])
  // Сверяем с числом ВЫЗОВОВ в блоке, а не с константой: разбор, потерявший вызов,
  // обязан краснеть, а не «проходить с запасом».
  const rawCalls = (block.match(/rawApi\.get\(/g) || []).length
  assert.equal(called.length, rawCalls,
    `разобрано ${called.length} адресов из ${rawCalls} вызовов — разбор теряет вызовы`)
  assert.ok(called.length >= 4, `ожидали минимум четыре публичных вызова, нашли ${called.length}`)
  for (const path of called) {
    const tail = path.replace('/public', '')          // router объявлен с prefix="/public"
    assert.ok(PY.includes(`@router.get("${tail}")`),
      `клиент зовёт ${path}, а на сервере такого маршрута нет`)
  }
})

test('на публичной странице нет ни одного поля из журнала', () => {
  // Граница названа и на сервере, и в шапке страницы. Сторож нужен потому, что соблазн
  // «показать тут же средний балл» появляется у того, кто шапку не читал.
  for (const forbidden of ['grade', 'average', 'attendance', 'absence', 'debt', 'zet']) {
    assert.ok(!new RegExp('\\b' + forbidden, 'i').test(CODE),
      `на странице без входа появилось «${forbidden}» — это утечка, а не удобство`)
  }
})

test('обратный ход: сторож ловит дословный дефект', () => {
  // Проверка, зелёная и без починки, неотличима от исправного кода. Натравливаем разбор
  // на ровно тот текст, который жил в продукте.
  const broken = [
    'const src = data.value?.days',
    'week.value = (await publicScheduleApi.week()).data',
    '<span v-if="week?.parity">',
  ].join('\n')
  const server = serverReturnKeys('public_schedule')
  const foreign = [...fieldsReadBy(stripComments(broken))].filter((k) => !server.has(k))
  assert.deepEqual(foreign, ['days'], 'разбор обязан заметить чтение несуществующего days')
  assert.ok(/\.parity\b/.test(broken), 'и обязан видеть .parity в исходнике')
})

test('обратный ход: вырезание комментариев не глотает настоящий код', () => {
  // Обратная опасность: слишком жадный «стриппер» вырезал бы половину файла, и сторож
  // молча перестал бы что-либо проверять — то есть стал бы зелёным навсегда.
  assert.ok(CODE.includes('publicScheduleApi.group('), 'вызов ручки расписания пропал при разборе')
  assert.ok(CODE.includes('filterGroups('), 'вызов поиска пропал при разборе')
  assert.ok(CODE.length > PAGE.length / 3, 'от файла осталось подозрительно мало')
})

test('обрыв связи не выбрасывает из уже открытого расписания', () => {
  // 🔥 Первая редакция показывала выбор группы по условию `!data || error`. При сбое
  // обновления `error` ставится, а `data` остаётся — и человека уводило от расписания,
  // которое он читает, обратно к списку групп. «Нет связи» не повод убирать с экрана уже
  // показанное: то же правило записано у расписания в кабинете («показанную копию НЕ
  // стираем»). Ошибка «группа не найдена» к выбору по-прежнему возвращает — там `data`
  // гасится явно, в самой ветке отказа.
  assert.ok(/<div v-if="!data" class=/.test(PAGE),
    'выбор группы обязан показываться РОВНО по !data, без «|| error»')
  assert.ok(!/v-if="!data \|\| error"/.test(PAGE),
    'вернулось условие, из-за которого сбой обновления прятал расписание')
  // И отказ обязан быть ВИДЕН в самом расписании, иначе он просто исчезнет.
  const tail = PAGE.slice(PAGE.indexOf('backToPicker'))
  assert.ok(/v-if="error"/.test(tail), 'в ветке расписания нет показа ошибки')
})

test('группа из адреса ищется БЕЗ навязанной категории', () => {
  // Сервер резолвит категорию из базы, только когда параметр пуст. Присланная ссылка
  // «вот расписание К74/1» вместе с запомненной чужой категорией иначе давала бы
  // «группа не найдена» при живом расписании.
  const code = stripComments(PAGE)
  assert.ok(/if \(group\.value\) load\(group\.value, ''\)/.test(code),
    'первая загрузка обязана передавать пустую категорию')
  assert.ok(/async function load\(name, cat\)/.test(code),
    'категория обязана быть отдельным аргументом, а не читаться из состояния')
})
