// rejectedWritesVisible.test.mjs — у отвергнутых сервером правок обязан быть ПОТРЕБИТЕЛЬ.
//
// 🔥 Дефект, ради которого заведён (07.09.2026). Очередь офлайн-правок различала «ещё в
// пути» и «сервер отказал по существу», складывала вторые в `rejected` — и не показывала
// их НИКОМУ: потребителя у этой коллекции не было ни одного, а ConnectionBadge считает
// только `pending`. Со стороны хуже, чем ошибка: счётчик «не отправлено: 3» ИСЧЕЗАЛ, то
// есть сообщал «всё уехало» ровно тогда, когда работа пропала.
//
// Наш обычный класс «обещание без вызывающего» — и ловится он проверкой ВЫЗОВА, а не
// поведения (правило CLAUDE.md: «у новой величины проверяй ВЫЗОВ»).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const NL = String.fromCharCode(10)
const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

function walk(dir) {
  const out = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...walk(p))
    else if (/\.(vue|js)$/.test(e.name)) out.push(p)
  }
  return out
}

const files = walk(SRC)
const read = (p) => readFileSync(p, 'utf8')

test('очередь отказов имеет хотя бы одного потребителя вне самой очереди', () => {
  const consumers = files.filter((p) => !p.endsWith(join('api', 'outbox.js')))
    .filter((p) => /from ['"][^'"]*api\/outbox['"]/.test(read(p)))
    .filter((p) => /\brejected\b/.test(read(p)))
  assert.ok(consumers.length > 0,
    'никто не импортирует `rejected` из api/outbox — отвергнутые правки снова невидимы')
})

test('потребитель показан человеку, а не просто существует', () => {
  // Компонент обязан быть СМОНТИРОВАН кем-то: файл, лежащий в src и никем не открытый, —
  // ровно тот дефект, от которого этот сторож защищает, только на уровень выше.
  const badge = files.find((p) => p.endsWith('RejectedWritesBadge.vue'))
  assert.ok(badge, 'компонент показа отказов отсутствует')
  const mounts = files.filter((p) => p !== badge && /<RejectedWritesBadge\b/.test(read(p)))
  assert.ok(mounts.length > 0, 'RejectedWritesBadge не смонтирован ни на одном экране')
})

test('значок отказов НЕ прячется при сворачивании панели', () => {
  // Соседний SyncIssuesBadge стоит под `v-if="!compact"` — и именно это однажды навсегда
  // отключило индикатор проблем синхронизации. Здесь такой ошибки быть не должно:
  // свёрнутая панель не имеет права спрятать сообщение о потерянной оценке.
  const sidebar = files.find((p) => p.endsWith(join('components', 'Sidebar.vue')))
  const line = read(sidebar).split('\n').find((l) => l.includes('<RejectedWritesBadge'))
  assert.ok(line, 'RejectedWritesBadge не найден в сайдбаре')
  assert.ok(!/v-if\s*=\s*"!compact"/.test(line),
    'значок отказов не должен исчезать в компактном режиме')
})

test('кнопки «повторить» у отказов нет — сервер отказал по существу', () => {
  const badge = files.find((p) => p.endsWith('RejectedWritesBadge.vue'))
  // ⚠️ Комментарии вырезаем ДО проверки. Первая версия этого сторожа краснела на самом
  // ПОЯСНЕНИИ «кнопки повторить здесь нет, см. outbox.flushOutbox» — то есть ловила
  // объяснение, а не код. Ровно та грабля, что уже записана в CLAUDE.md («сторож
  // потребителя искал подстроку, встречавшуюся в комментариях»).
  const src = read(badge)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split(NL).filter((l) => !/^\s*(\/\/|<!--)/.test(l)).join(NL)
  assert.ok(!/flushOutbox|enqueue[A-Z]/.test(src),
    'повтор заведомо получит тот же отказ; кнопка, которая не помогает, хуже её отсутствия')
})

test('все ключи перевода отказов есть во ВСЕХ трёх локалях', () => {
  const dict = read(join(SRC, 'i18n', 'dictionaries.js'))
  const badge = read(files.find((p) => p.endsWith('RejectedWritesBadge.vue')))
  const keys = [...badge.matchAll(/locale\.t\(\s*'([^']+)'/g)].map((m) => m[1])
    .filter((k) => k.startsWith('rejected.'))
  assert.ok(keys.length >= 5, 'ожидались ключи rejected.*')
  for (const k of new Set(keys)) {
    const n = dict.split(`'${k}':`).length - 1
    assert.equal(n, 3, `ключ ${k} должен быть ровно в трёх локалях, найдено ${n}`)
  }
})
