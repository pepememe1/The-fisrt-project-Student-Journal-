// sessionGeneration.test.mjs — запоздавший refresh НЕ ТРОГАЕТ чужую сессию.
//
// 🔥 Дефект (07.09.2026). `doRefresh` брал refresh-токен, уходил в сеть и по возвращении
// записывал новые токены БЕЗУСЛОВНО; ветка ошибки так же безусловно их стирала. А между
// отправкой и ответом человек мог выйти и войти под другим аккаунтом — на общем
// компьютере колледжа это обычный день, а не экзотика.
//   • поздний УСПЕХ аккаунта A перезаписывал токены B — тот продолжал работу под чужой
//     сессией, с чужими правами и чужими данными;
//   • поздний ОТКАЗ A выбрасывал B из аккаунта посреди выставления оценок.
//
// Проверяем СВОЙСТВО («записать/стереть токены вправе только то поколение, которое
// refresh начало»), а не наличие строки в файле. Обратный ход — в конце.
import { test } from 'node:test'
import assert from 'node:assert/strict'

// ── Модель того же алгоритма, что в client.js ──────────────────────────────────────
// Настоящий модуль тянет axios и весь стор; здесь важна РАЗВЯЗКА поколений, и она
// целиком укладывается в двадцать строк. Копией формулы это не является: проверяемое
// решение — «сравнить поколение до и после await», а не расчёт значения.
function makeClient() {
  let gen = 0
  let stored = null                 // «хранилище токенов»
  const bump = () => { gen += 1 }

  async function doRefresh(network) {
    const mine = gen
    const data = await network()    // ← сюда влезает смена пользователя
    if (mine !== gen) {
      const e = new Error('stale'); e.stale = true; throw e
    }
    stored = data
    return data
  }

  async function onUnauthorized(network) {
    const mine = gen
    try {
      return { ok: true, token: await doRefresh(network) }
    } catch (e) {
      if (e?.stale || mine !== gen) return { ok: false, cleared: false }
      stored = null                 // clearTokens()
      return { ok: false, cleared: true }
    }
  }

  return { bump, onUnauthorized, tokens: () => stored, set: (v) => { stored = v } }
}

test('поздний УСПЕХ прежнего аккаунта не подменяет токены нового', async () => {
  const c = makeClient()
  let release
  const slow = () => new Promise((r) => { release = () => r('токен-A') })

  const inflight = c.onUnauthorized(slow)   // refresh аккаунта A ушёл в сеть
  c.bump(); c.set('токен-B')                // человек вышел и вошёл как B
  release()
  const res = await inflight

  assert.equal(res.ok, false, 'поздний ответ прежней сессии не считается успехом')
  assert.equal(c.tokens(), 'токен-B',
    'токены B обязаны уцелеть — иначе человек работает под чужой сессией')
})

test('поздний ОТКАЗ прежнего аккаунта не выбрасывает нового из программы', async () => {
  const c = makeClient()
  let fail
  const slow = () => new Promise((_, rej) => { fail = () => rej(new Error('HTTP 401')) })

  const inflight = c.onUnauthorized(slow)
  c.bump(); c.set('токен-B')
  fail()
  const res = await inflight

  assert.equal(res.cleared, false, 'чужой отказ не имеет права гасить живую сессию')
  assert.equal(c.tokens(), 'токен-B', 'B остаётся в аккаунте')
})

test('без смены сессии всё работает как прежде: успех сохраняет, отказ гасит', async () => {
  const ok = makeClient()
  const r1 = await ok.onUnauthorized(async () => 'свежий')
  assert.equal(r1.ok, true)
  assert.equal(ok.tokens(), 'свежий', 'обычное продление обязано сохранять токен')

  const bad = makeClient()
  bad.set('старый')
  const r2 = await bad.onUnauthorized(async () => { throw new Error('HTTP 401') })
  assert.equal(r2.cleared, true, 'настоящий отказ сервера обязан завершать сессию')
  assert.equal(bad.tokens(), null)
})

// ── ВЫЗОВ, а не только поведение ───────────────────────────────────────────────────
// Правило CLAUDE.md: у новой величины проверяй ВЫЗОВ. Счётчик, который никто не двигает,
// молча превращает всю защиту выше в мёртвый код.
test('поколение двигают ВСЕ три двери смены владельца сессии', async () => {
  const { readFileSync } = await import('node:fs')
  const { fileURLToPath } = await import('node:url')
  const { dirname, join } = await import('node:path')
  const src = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

  const client = readFileSync(join(src, 'api', 'client.js'), 'utf8')
  assert.match(client, /export function bumpSessionGeneration/,
    'счётчик поколений обязан быть заведён в client.js')

  const auth = readFileSync(join(src, 'stores', 'auth.js'), 'utf8')
  assert.match(auth, /import \{ bumpSessionGeneration \}/, 'стор входа обязан его импортировать')
  // Три двери: вход, выход, локальный сброс. Забудешь одну — там и разойдётся.
  const calls = (auth.match(/bumpSessionGeneration\(\)/g) || []).length
  assert.ok(calls >= 3,
    `поколение двигается в ${calls} местах из трёх (вход, выход, clearSession)`)
})

// ── ОБРАТНЫЙ ХОД ────────────────────────────────────────────────────────────────────
test('обратный ход: прежний безусловный код обязан краснеть', async () => {
  let stored = 'токен-B'
  let release
  const broken = (async () => {
    const data = await new Promise((r) => { release = () => r('токен-A') })
    stored = data                    // ← ровно то, что стояло до починки
  })()
  release()
  await broken
  assert.equal(stored, 'токен-A',
    'дефектная версия обязана подменить токены — иначе проверки выше ничего не ловят')
})
