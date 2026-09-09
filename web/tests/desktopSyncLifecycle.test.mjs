// desktopSyncLifecycle.test.mjs — значок «правки не уехали на сервер» обязан ПЕРЕЖИВАТЬ
// сворачивание боковой панели.
//
// 🔥 Дефект, ради которого заведён (07.09.2026). Значок живёт в сайдбаре под
// `v-if="!compact"`, то есть сворачивание панели РАЗМОНТИРУЕТ его. Размонтаж звал
// `stop()`, тот ставил общий флаг `stopped = true`, а `start()` при таком флаге выходил
// первой же строкой. Итог: одно сворачивание — и единственный индикатор потерянной
// оценки молчал до перезапуска программы. Ни ошибки, ни следа в консоли.
//
// ⚠️ Проверяем СВОЙСТВО жизненного цикла, а не текст модуля: «после stop() следующий
// start() снова опрашивает». Обратный ход — в конце файла: возвращаем прежнее поведение
// подделкой и убеждаемся, что проверка краснеет.
import { test } from 'node:test'
import assert from 'node:assert/strict'

// Минимальные заглушки окружения браузера — модуль зовёт fetch, document и setInterval.
// ⚠️ `createElement` обязателен, хотя наш код его не трогает: `vue` тянет runtime-dom,
// а тот на ИМПОРТЕ создаёт <template>. Без заглушки падает не тест, а загрузка модуля.
const listeners = {}
globalThis.document = {
  hidden: false,
  createElement: () => ({ innerHTML: '', content: {}, firstChild: null }),
  addEventListener: (k, f) => { (listeners[k] ||= []).push(f) },
  removeEventListener: (k, f) => { listeners[k] = (listeners[k] || []).filter((x) => x !== f) },
}
globalThis.localStorage = {
  getItem: () => null, setItem: () => {}, removeItem: () => {},
}

let calls = 0
let nextStatus = 200
let nextBody = { available: true, conflicts: 2, rejected: {}, auth_error: '' }
globalThis.fetch = async () => {
  calls += 1
  return {
    ok: nextStatus >= 200 && nextStatus < 300,
    status: nextStatus,
    json: async () => nextBody,
  }
}

const sync = await import('../src/api/desktopSync.js')

// Дать модулю доработать свои await внутри poll().
const settle = () => new Promise((r) => setTimeout(r, 0))

function reset() {
  sync._resetForTests()
  calls = 0
  nextStatus = 200
}

test('свернули панель и развернули обратно — опрос продолжается', async () => {
  reset()
  sync.start()
  await settle()
  assert.equal(calls, 1, 'первый монтаж обязан спросить состояние')
  assert.equal(sync.syncIssues().length, 1, 'конфликт с сервера должен быть виден')

  sync.stop()                       // ← свернули сайдбар: значок размонтирован
  sync.start()                      // ← развернули обратно
  await settle()
  assert.equal(calls, 2, 'после разворачивания панели опрос обязан возобновиться')
  assert.equal(sync.syncIssues().length, 1, 'проблемы синка снова видны')
})

test('два одновременных значка: размонтаж одного не гасит опрос', async () => {
  reset()
  sync.start()                      // десктопная панель
  sync.start()                      // мобильная шторка поверх неё
  await settle()
  sync.stop()                       // шторку закрыли
  const before = calls
  sync.start()                      // снова открыли
  await settle()
  assert.ok(calls > before, 'пока хоть один значок на экране, опрос обязан жить')
})

test('404 — это сайт, замолкаем НАВСЕГДА', async () => {
  reset()
  nextStatus = 404
  sync.start()
  await settle()
  const after404 = calls
  sync.stop()
  sync.start()
  await settle()
  assert.equal(calls, after404, 'на сайте маршрута нет и не будет — повторять нечего')
})

test('500 своего же локального сервера — временная беда, не приговор', async () => {
  reset()
  nextStatus = 500
  sync.start()
  await settle()
  const afterFail = calls
  nextStatus = 200
  sync.stop()
  sync.start()
  await settle()
  assert.ok(calls > afterFail,
    'одна пятисотка на старте программы не имеет права отключить значок до конца сеанса')
  assert.equal(sync.syncIssues().length, 1, 'состояние обязано восстановиться')
})

test('уборка: гасим таймер, иначе node --test не завершится', () => {
  //Не косметика: setInterval держит цикл событий Node живым, и прогон висел бы вечно.
  sync._resetForTests()
  assert.equal(sync.syncIssues().length, 0)
})

// ── ОБРАТНЫЙ ХОД ────────────────────────────────────────────────────────────────────
// Без него сторож неотличим от исправного кода: он был бы зелёным и при дефекте.
test('обратный ход: прежнее поведение (общий флаг stopped) обязано краснеть', async () => {
  // Модель ровно того кода, что стоял до починки.
  let stopped = false
  let timer = null
  let polls = 0
  const brokenStart = () => { if (timer || stopped) return; polls += 1; timer = 1 }
  const brokenStop = () => { stopped = true; timer = null }

  brokenStart()
  assert.equal(polls, 1)
  brokenStop()                      // свернули панель
  brokenStart()                     // развернули
  assert.equal(polls, 1,
    'дефектная версия обязана НЕ возобновить опрос — иначе проверка выше ничего не ловит')
})
