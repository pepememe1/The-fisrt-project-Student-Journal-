/**
 * sharedClock.test.mjs — ОДНИ ЧАСЫ НА ВСЕ КАРТОЧКИ ЛЕНТЫ (B7 разбора 07.09.2026).
 *
 * ━━ ЧТО БЫЛО ━━
 * `ActivityCard.vue` и `PollMessage.vue` заводили `setInterval(..., 1000)` БЕЗУСЛОВНО —
 * в том числе завершённой активности и закрытому опросу, где значение уже никогда не
 * изменится. Число таймеров росло вместе с числом карточек в ленте.
 *
 * ━━ ЧТО ПРОВЕРЯЕТСЯ ━━
 * Два свойства, и второе важнее первого:
 *   1. таймер В ПРОЦЕССЕ ОДИН, сколько бы подписчиков ни было;
 *   2. неактивная карточка НЕ ПОДПИСАНА, то есть её значение не меняется и перерисовки
 *      нет. Без второго правила таймер стал бы один, а работы осталось бы столько же —
 *      починка была бы видна в коде и отсутствовала на экране.
 *
 * Обратный ход проверен: возвращаю в компоненты безусловный `setInterval` — краснеет
 * «в карточках не осталось собственных таймеров».
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { effectScope, nextTick, ref } from 'vue'

const WEB = dirname(dirname(fileURLToPath(import.meta.url)))

// ── управляемое время и управляемая видимость ───────────────────────────────────
// Подменяем ДО первого вызова: модуль обращается к `setInterval`/`document` в момент
// подписки, а не при загрузке, поэтому подмены достаточно здесь.
let intervalCalls = 0
let fired = null
const listeners = new Map()
globalThis.setInterval = (fn) => { intervalCalls += 1; fired = fn; return { id: intervalCalls } }
globalThis.clearInterval = () => { fired = null }
globalThis.document = {
  hidden: false,
  addEventListener: (name, fn) => listeners.set(name, fn),
  removeEventListener: (name) => listeners.delete(name),
}
function tick() { if (fired) fired() }
function setHidden(v) {
  globalThis.document.hidden = v
  const fn = listeners.get('visibilitychange')
  if (fn) fn()
}

const { subscribe, useSharedNow, _state, TICK_MS } = await import('../src/utils/sharedClock.js')

test('период тика — секунда: меньше подписи не показывают', () => {
  assert.equal(TICK_MS, 1000)
})

test('таймер ОДИН на сколько угодно подписчиков', () => {
  intervalCalls = 0
  const a = subscribe(() => {})
  const b = subscribe(() => {})
  const c = subscribe(() => {})
  assert.equal(_state().subscribers, 3)
  assert.equal(intervalCalls, 1, 'на каждого подписчика завели свой таймер — это и был дефект')
  a(); b(); c()
})

test('последний отписавшийся снимает таймер, а не оставляет его крутиться', () => {
  const a = subscribe(() => {})
  const b = subscribe(() => {})
  a()
  assert.equal(_state().running, true, 'таймер сняли, пока подписчик ещё есть')
  b()
  assert.equal(_state().running, false, 'таймер остался жить без единого подписчика')
  assert.equal(_state().visibilityBound, false, 'обработчик видимости не снят — тот же класс, что B6')
})

test('отписка идемпотентна: второй вызов не роняет чужие часы', () => {
  const a = subscribe(() => {})
  const b = subscribe(() => {})
  a(); a(); a()
  assert.equal(_state().subscribers, 1, 'повторная отписка убрала чужого подписчика')
  b()
})

test('подписчик, отписавшийся ВНУТРИ тика, не роняет соседей', () => {
  // Ровно это и делает карточка опроса, когда срок вышел прямо на тике.
  const seen = []
  let off1 = null
  off1 = subscribe(() => { seen.push(1); off1() })
  const off2 = subscribe(() => { seen.push(2) })
  tick()
  assert.deepEqual(seen.sort(), [1, 2], 'сосед потерян из-за правки набора во время обхода')
  off2()
})

test('в скрытой вкладке часы СТОЯТ, при возврате тикают немедленно', () => {
  let last = 0
  const off = subscribe((t) => { last = t })
  assert.equal(_state().running, true)
  setHidden(true)
  assert.equal(_state().running, false, 'в фоне часы продолжали идти — батарея телефона')
  last = 0
  setHidden(false)
  assert.ok(last > 0, 'при возврате не догнали время — подпись осталась с момента сворачивания')
  assert.equal(_state().running, true)
  off()
})

test('пока карточка неактивна — она НЕ подписана и её время не меняется', async () => {
  const scope = effectScope()
  let now
  const active = ref(false)
  scope.run(() => { now = useSharedNow(active) })
  const frozen = now.value
  assert.equal(_state().subscribers, 0, 'неактивная карточка всё-таки подписалась')
  tick()
  assert.equal(now.value, frozen, 'время неактивной карточки изменилось — будет лишняя перерисовка')

  active.value = true
  await nextTick()
  assert.equal(_state().subscribers, 1, 'ставшая активной карточка не подписалась')
  scope.stop()
})

test('уход карточки с экрана снимает подписку', async () => {
  const scope = effectScope()
  const active = ref(true)
  scope.run(() => { useSharedNow(active) })
  await nextTick()
  assert.equal(_state().subscribers, 1)
  scope.stop()
  assert.equal(_state().subscribers, 0, 'размонтированная карточка осталась подписанной — утечка')
})

test('карточка, переставшая быть активной, отписывается сама', async () => {
  const scope = effectScope()
  const active = ref(true)
  scope.run(() => { useSharedNow(active) })
  await nextTick()
  assert.equal(_state().subscribers, 1)
  active.value = false
  await nextTick()
  assert.equal(_state().subscribers, 0,
    'завершившаяся активность продолжает тикать — ровно дефект B7')
  scope.stop()
})

// ── связь с продуктом ───────────────────────────────────────────────────────────
// Проверка выше описывает механизм; здесь — что им ПОЛЬЗУЮТСЯ. Механизм без вызывающего
// это наш самый частый класс дефекта (§«обещание без вызывающего»).
const CARDS = [
  'src/components/activity/ActivityCard.vue',
  'src/components/activity/poll/PollMessage.vue',
]

test('в карточках ленты не осталось собственных таймеров', () => {
  for (const rel of CARDS) {
    const src = readFileSync(join(WEB, rel), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    assert.ok(!/setInterval\s*\(/.test(src), `${rel}: снова свой таймер на карточку`)
    assert.match(src, /useSharedNow\s*\(/, `${rel}: общие часы не подключены`)
  }
})

test('часы подключены УСЛОВНО, а не «всегда включены»', () => {
  // `useSharedNow(true)` вернуло бы прежнее поведение с одним таймером на всех — то есть
  // половину починки. Условие обязано быть величиной, а не литералом.
  for (const rel of CARDS) {
    const src = readFileSync(join(WEB, rel), 'utf8')
    const arg = (src.match(/useSharedNow\s*\(\s*([^)]*)\)/) || [])[1] || ''
    assert.ok(arg.trim(), `${rel}: у часов нет условия вовсе`)
    assert.ok(!/^(true|1)$/.test(arg.trim()),
      `${rel}: часы включены безусловно (${arg}) — завершённая карточка снова тикает`)
  }
})
