/**
 * messengerHiddenApp.test.mjs — СВЁРНУТОЕ ПРИЛОЖЕНИЕ НЕ ХОДИТ В СЕТЬ (B4, 07.09.2026).
 *
 * ━━ ЧТО БЫЛО ━━
 * Правило «в скрытой вкладке не ходим в сеть» жило ТОЛЬКО в `_tick`. Сторож
 * `pollingRespectsVisibility.test.mjs` проверял именно таймеры — и был зелёным, — а
 * мимо него шли две цепочки:
 *   • обработчик сообщений сокета звал `pollOnce()`/`loadChats()`/`loadConvInfo()`
 *     вообще без проверки: сервер прислал кадр — телефон в кармане пошёл в сеть;
 *   • переподключение сокета планировалось так же безусловно, то есть упавшая ночью
 *     связь давала цепочку попыток у свёрнутого приложения до утра.
 *
 * **Зелёный тест рядом с дефектом — это «случай не покрыт», а не «исправно».**
 *
 * ━━ ЧЕСТНАЯ ГРАНИЦА ━━
 * Проверка эвристическая: она читает ТЕКСТ обработчика и требует, чтобы рядом с каждым
 * сетевым вызовом стояло условие по `_hidden()`. Точный разбор потребовал бы AST и всё
 * равно ошибался бы, а цена ложного срабатывания здесь — одна строка условия. Зато
 * обратный ход проверяется на ДОСЛОВНОМ тексте обработчика до починки (см. последний
 * тест): правило, которое не ловит настоящий дефект, не защищает ни от чего.
 *
 * ⚠️ Вызовы ищутся ПОДСТРОКОЙ `имя(`, а не регулярным выражением: первая версия этого
 * файла собирала регулярку в шаблонной строке, двойные слэши в ней схлопнулись, и она
 * искала «pollOnces(» — то есть не находила НИЧЕГО и была зелёной впустую. Ровно наш
 * записанный класс «сторож, который не может покраснеть», и поймал его только обратный
 * ход, который отказался краснеть на дословном дефекте.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const WEB = dirname(dirname(fileURLToPath(import.meta.url)))
const store = readFileSync(join(WEB, 'src/stores/messenger.js'), 'utf8')

/** Вызовы, каждый из которых уходит в сеть. */
const NETWORK = ['pollOnce', 'loadChats', 'loadConvInfo', 'markReadActive']
/** Насколько близко перед вызовом обязано стоять условие видимости. */
const REACH = 220

function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*/gm, '')
}

/** Тело функции/обработчика от `marker` до парной закрывающей фигурной скобки. */
function bodyAfter(src, marker) {
  const at = src.indexOf(marker)
  if (at < 0) return ''
  const open = src.indexOf('{', at)
  if (open < 0) return ''
  let depth = 0
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1
    else if (src[i] === '}') {
      depth -= 1
      if (!depth) return src.slice(open, i + 1)
    }
  }
  return src.slice(open)
}

/** Сетевые вызовы, рядом с которыми НЕТ проверки видимости. */
function unguardedCalls(body) {
  const bad = []
  for (const fn of NETWORK) {
    let from = 0
    for (;;) {
      const at = body.indexOf(fn + '(', from)
      if (at < 0) break
      from = at + 1
      const before = body.slice(Math.max(0, at - REACH), at)
      if (!/_hidden\s*\(/.test(before)) bad.push(fn + ' @' + at)
    }
  }
  return bad
}

test('сам разбор работает: находит вызов и видит условие рядом', () => {
  // 🔒 Проверка НАД проверкой. Без неё сломанный разбор (а он уже ломался — см. шапку)
  // делает все остальные тесты этого файла зелёными и бессмысленными.
  assert.deepEqual(unguardedCalls('{ pollOnce() }'), ['pollOnce @2'],
    'разбор не находит вызов вовсе — весь файл зелен впустую')
  assert.deepEqual(unguardedCalls('{ if (!_hidden()) pollOnce() }'), [],
    'разбор не видит условие видимости рядом с вызовом — будут ложные срабатывания')
})

test('признак «экрана не видно» существует и смотрит на document.hidden', () => {
  const fn = bodyAfter(store, 'function _hidden(')
  assert.ok(fn, 'функции _hidden больше нет — правило снова размазано по файлу')
  assert.match(fn, /document\.hidden/,
    'признак перестал спрашивать браузер — он будет врать')
})

test('обработчик кадров сокета не ходит в сеть у свёрнутого приложения', () => {
  const body = bodyAfter(stripComments(store), 'ws.onmessage')
  assert.ok(body.length > 200, 'не нашёл тело обработчика — разбор сломался')
  assert.deepEqual(unguardedCalls(body), [],
    'сетевой вызов в обработчике сокета без проверки видимости')
})

test('переподключение сокета в фоне не планируется вовсе', () => {
  const body = bodyAfter(stripComments(store), 'function _scheduleReconnect(')
  assert.ok(body, 'функция переподключения пропала')
  assert.match(body, /if\s*\(\s*_hidden\(\)\s*\)\s*return/,
    'в фоне снова планируется цепочка попыток — радиомодуль будят всю ночь')
})

test('возвращение к окну чинит связь немедленно, а не «когда-нибудь»', () => {
  // Отказ от фоновых попыток имеет цену: связь надо восстановить при возврате. Без
  // этого починка B4 превратилась бы в «мессенджер молчит после сворачивания».
  const body = bodyAfter(stripComments(store), 'function _onVisibleAgain(')
  assert.ok(body, 'обработчика возвращения нет — сокет не восстановится')
  assert.match(body, /_connectWS\s*\(|_ensureWS\s*\(/, 'при возврате сокет не поднимают')
  assert.match(body, /pollOnce\s*\(/, 'при возврате не догоняем ленту')
  assert.match(store, /addEventListener\('visibilitychange',\s*_onVisibleAgain\)/,
    'обработчик написан, но не подписан — обещание без вызывающего')
  assert.match(store, /removeEventListener\('visibilitychange',\s*_onVisibleAgain\)/,
    'обработчик не снимается — накопится по одному на каждый вход в раздел (урок B6)')
})

test('ОБРАТНЫЙ ХОД: правило ловит дословный обработчик до починки', () => {
  // Текст взят из `git show 4028d18^:web/src/stores/messenger.js`. Если проверка его
  // не краснит — она бесполезна, и это ровно тот случай, который у нас уже ловили
  // четыре раза подряд на `pollingRespectsVisibility`.
  const OLD = `ws.onmessage = (e) => {
        let ev
        try { ev = JSON.parse(e.data) } catch { return }
        if (ev.type === 'changed') {
          if (ev.conversation_id === activeId.value) pollOnce()
          else loadChats()
        } else if (ev.type && ev.type.startsWith('activity.')) {
          if (ev.conversation_id === activeId.value) pollOnce()
        }
      }`
  assert.notDeepEqual(unguardedCalls(bodyAfter(OLD, 'ws.onmessage')), [],
    'проверка НЕ видит дефект в прежнем коде — она ничего не защищает')

  const OLD_RECONNECT = `function _scheduleReconnect() {
    if (!pollTimer) return
    clearTimeout(wsRetryTimer)
    const delay = reconnectDelay(wsRetry++)
    wsRetryTimer = setTimeout(() => { if (pollTimer) _connectWS() }, delay)
  }`
  assert.doesNotMatch(bodyAfter(OLD_RECONNECT, 'function _scheduleReconnect('),
    /if\s*\(\s*_hidden\(\)\s*\)\s*return/,
    'прежнее переподключение вдруг проходит проверку — она не та')
})
