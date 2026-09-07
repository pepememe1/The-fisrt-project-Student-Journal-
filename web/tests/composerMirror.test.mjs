// composerMirror.test.mjs — набранный текст обязан быть ВИДЕН.
//
// 🔥 РЕГРЕССИЯ, ДОЕХАВШАЯ ДО БОЯ (06.09.2026, жалоба Влада: «когда набираем текст, его
// вообще не видно»). Приглушение символов разметки сделано зеркалом: текст поля
// прозрачный, рисует его div под полем. Но у поля остался НЕПРОЗРАЧНЫЙ фон (`bg-card2`),
// и оно закрывало зеркало собой. Свой текст прозрачный, чужой перекрыт — видно ничего.
//
// ⚠️ Урок общий: делая элемент прозрачным, проверь, что под ним ВИДНО. Фон соседа — такая
// же преграда, как непрозрачный текст, и ни сборка, ни линтер, ни тесты этого не
// показывают. Здесь проверяется ровно связка, на которой сломалось.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/components/messenger/ChatThread.vue', import.meta.url), 'utf8')
const composerTag = src.match(/<textarea ref="composer"[\s\S]*?\/>/)?.[0] || ''
const styles = src.split('<style')[1] || ''

test('поле композера в разметке найдено', () => {
  assert.ok(composerTag, 'не нашли <textarea ref="composer"> — тест ослеп, чинить его')
})

test('у поля с ПРОЗРАЧНЫМ текстом не может быть непрозрачного фона', () => {
  // Ровно то, что сломалось: `color: transparent` + `bg-card2` = не видно ничего.
  assert.ok(/\.gb-composer-input[\s\S]*?color:\s*transparent/.test(styles),
    'текст поля больше не прозрачный — тогда зеркало рисует ВТОРУЮ копию текста поверх')
  const opaqueBg = composerTag.match(/\bbg-(card2?|bg2?|white|surface)\b/)
  assert.equal(opaqueBg, null,
    `у поля непрозрачный фон (${opaqueBg?.[0]}) — он закроет зеркало, и текста не будет видно`)
})

test('фон и рамку рисует ОБЁРТКА, а не поле', () => {
  // Иначе их некому нарисовать: поле обязано быть прозрачным, а зеркало лежит под ним.
  assert.ok(/gb-composer-wrap[^"]*"[^>]*?\bbg-card2\b/.test(src)
    || /class="gb-composer-wrap[^"]*bg-/.test(src),
    'обёртка композера потеряла фон — поле станет прозрачным окном в страницу')
  assert.ok(/\.gb-composer-wrap:focus-within/.test(styles),
    'без focus-within подсветка фокуса пропадёт: рамку рисует уже не поле')
})

test('зеркало и поле делят ОДИН класс раскладки', () => {
  // Разъедутся метрики — приглушённые символы съедут с настоящих, и это будет рябь.
  assert.ok(/ref="mirror"[\s\S]{0,400}gb-composer-box/.test(src), 'у зеркала нет общего класса')
  assert.ok(/gb-composer-box/.test(composerTag), 'у поля нет общего класса')
})
