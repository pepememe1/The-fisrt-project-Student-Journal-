// scheduleInstant.test.mjs — РАСПИСАНИЕ ПОКАЗЫВАЕТСЯ СРАЗУ, А НЕ ПОСЛЕ КРУГА ДО СЕРВЕРА.
//
// 🔥 ЖАЛОБА (Влад, 07.09.2026): «при открытии расписания оно грузится заново каждый раз».
//
// Копия прошлого ответа лежала на диске всё это время — `api/offlineCache.js` наполняется
// на КАЖДОМ успешном GET. Но заглядывал в неё только обработчик ОШИБКИ (`client.js`), то
// есть при живой сети её не показывали ни разу. Расписание на телефоне открывают чаще
// всего, и мобильная сеть даёт те самые полсекунды пустого экрана.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const page = readFileSync(new URL('../src/pages/SchedulePage.vue', import.meta.url), 'utf8')
const endpoints = readFileSync(new URL('../src/api/endpoints.js', import.meta.url), 'utf8')
const cache = readFileSync(new URL('../src/api/offlineCache.js', import.meta.url), 'utf8')

test('копия читается ДО сети, а не только при ошибке', () => {
  assert.ok(/export function peekCached/.test(cache), 'нет двери «покажи копию не ходя в сеть»')
  assert.ok(/paintCached\(scheduleApi\.cachedGet\(/.test(page),
    'расписание группы снова ждёт сервер, прежде чем хоть что-то показать')
  assert.ok(/paintCached\(scheduleApi\.cachedTeacher\(/.test(page),
    'расписание преподавателя снова ждёт сервер')
})

test('🔥 путь запроса и путь копии — ОДНА И ТА ЖЕ строка', () => {
  // Путь намеренно остаётся литералом в каждом вызове: мост HTTP-контракта
  // (tools/graph_api_bridge.py) ищет `api.get('…')` с литералом, и вынесенная константа
  // делала живой роут «невостребованным» на карте — по такой карте его однажды удалят.
  // Цена литерала — возможность разойтись; её и закрывает эта проверка.
  const pairs = [
    [/get:[\s\S]{0,80}?api\.get\('([^']+)'/, /cachedGet:[\s\S]{0,80}?peekCached\('([^']+)'/],
    [/teacher:[\s\S]{0,120}?api\.get\('([^']+)'/, /cachedTeacher:[\s\S]{0,80}?peekCached\('([^']+)'/],
  ]
  for (const [reqRe, cacheRe] of pairs) {
    const a = endpoints.match(reqRe)?.[1]
    const b = endpoints.match(cacheRe)?.[1]
    assert.ok(a && b, 'не нашли пару «запрос ↔ копия» — тест ослеп')
    assert.equal(b, a, `путь копии (${b}) разошёлся с путём запроса (${a}) — копия перестанет находиться`)
  }
})

test('🔥 параметры запроса и копии собираются ОДИН раз', () => {
  // Разойдутся — копия просто перестанет находиться: экран вернётся к спиннеру, и
  // никакой ошибки при этом не будет. Отказ тихий, поэтому связка под сторожем.
  assert.ok(/const scheduleGetParams = /.test(endpoints) && /const scheduleTeacherParams = /.test(endpoints),
    'параметры расписания больше не собираются общей функцией')
  for (const fn of ['scheduleGetParams', 'scheduleTeacherParams']) {
    const uses = endpoints.split(fn).length - 1
    assert.ok(uses >= 3,
      `${fn} используется ${uses - 1} раз(а) — запрос и его копия обязаны брать параметры отсюда оба`)
  }
})

test('копия НЕ отменяет запрос', () => {
  // Расписание правят на портале и в админке. Показать вчерашнее ВМЕСТО сегодняшнего —
  // это поменять «медленно» на «человек пришёл не на ту пару», то есть на худшее.
  const load = page.match(/async function load\(\)[\s\S]*?\n\}/)?.[0] || ''
  assert.ok(load, 'не нашли load() — тест ослеп')
  assert.ok(/await scheduleApi\.get\(/.test(load),
    'после показа копии запрос к серверу пропал — расписание перестанет обновляться')
  assert.ok(!/return/.test(load.split('paintCached')[1]?.split('\n')[0] || ''),
    'показ копии прерывает загрузку')
})

test('🔒 у копии есть срок годности', () => {
  // Недельная копия — правда, месячная уже нет. Без срока сторож бы «работал», а человек
  // однажды увидел бы расписание прошлого семестра и поверил ему.
  assert.ok(/CACHE_SHOW_MS/.test(page), 'срок годности копии не задан')
  const days = Number(page.match(/CACHE_SHOW_MS = (\d+) \* 24 \* 60 \* 60 \* 1000/)?.[1])
  assert.ok(days > 0 && days <= 14,
    `срок показа копии ${days} дн. — расписание недельное, столько ей верить нельзя`)
})

test('копия не принимает решений за сервер', () => {
  const paint = page.match(/function paintCached\([\s\S]*?\n\}/)?.[0] || ''
  assert.ok(paint, 'не нашли paintCached — тест ослеп')
  // Устаревшее поле `category` увело бы человека в чужую категорию ещё до ответа сервера,
  // а снимок для виджета положил бы на рабочий стол вчерашние пары.
  assert.ok(!/category\.value\s*=/.test(paint), 'копия переключает категорию — это дело ответа сервера')
  assert.ok(!/pushWidgetSnapshot/.test(paint), 'копия уезжает в виджет на рабочий стол')
  assert.ok(!/loadGroupsList/.test(paint), 'копия дёргает загрузку списка групп')
})

test('🔒 «нет связи» не стирает уже показанное расписание', () => {
  assert.ok(/if \(my === reqSeq && !data\.value\) data\.value = null/.test(page),
    'при ошибке показанная копия снова стирается — экран пустеет у человека на глазах')
})
