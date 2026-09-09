/**
 * widgetRefreshBackoff.test.mjs — ПОПЫТКА И УСПЕХ ЭТО РАЗНЫЕ ВЕЛИЧИНЫ (B2, 07.09.2026).
 *
 * ━━ ЧТО БЫЛО ━━
 * Метка времени одна, клалась ДО запроса и обновлялась в ЛЮБОМ случае. Открыли
 * приложение без сети (пары идут, вайфай в аудитории не отвечает) → метка обновилась
 * ровно как при успехе → связь вернулась → расписание в виджете не обновляется ещё
 * шесть часов. Молча: ни ошибки, ни следа, виджет просто показывает вчерашний день.
 *
 * ━━ ПОЧЕМУ ЭТО ПРОВЕРЯЕТСЯ ЗДЕСЬ, А НЕ В СЕРВИСЕ ━━
 * `services/scheduleWidget.js` тянет `@/api/server` — без сборщика его не импортировать.
 * Правило вынесено в `utils/widgetRefresh.js` именно затем, чтобы его можно было
 * исполнить. Проверка, которую дорого выполнить, не выполняется никогда.
 *
 * Обратный ход: вернуть одну метку (считать длинный интервал от ПОПЫТКИ) — краснеет
 * «осечка не покупает полдня молчания».
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { shouldRefresh, STALE_MS, RETRY_MS } from '../src/utils/widgetRefresh.js'

const WEB = dirname(dirname(fileURLToPath(import.meta.url)))
const NOW = 1_800_000_000_000

test('никогда не обновлялись — идём в сеть', () => {
  assert.equal(shouldRefresh({ now: NOW, okAt: 0, tryAt: 0 }), true)
})

test('данные свежие — в сеть не ходим', () => {
  // Основной ограничитель: приложение открывают по десять раз в день.
  assert.equal(shouldRefresh({ now: NOW, okAt: NOW - 60_000, tryAt: NOW - 60_000 }), false)
})

test('🔥 ОСЕЧКА НЕ ПОКУПАЕТ ПОЛДНЯ МОЛЧАНИЯ', () => {
  // Дословный сценарий дефекта: успеха не было давно, попытка была только что и
  // ПРОВАЛИЛАСЬ. Через шесть минут связь вернулась — обязаны пойти.
  const okAt = NOW - 10 * 60 * 60 * 1000        // успех был десять часов назад
  const tryAt = NOW - 6 * 60 * 1000             // неудачная попытка шесть минут назад
  assert.equal(shouldRefresh({ now: NOW, okAt, tryAt }), true,
    'после осечки ждём длинный интервал — это и был дефект')
})

test('но и не долбим сеть: сразу после осечки молчим', () => {
  const okAt = NOW - 10 * 60 * 60 * 1000
  assert.equal(shouldRefresh({ now: NOW, okAt, tryAt: NOW - 1000 }), false,
    'при лежащем интернете пошли бы в сеть на каждый запуск — довод прежнего кода верен')
})

test('пауза повтора заметно короче интервала свежести', () => {
  // Если бы они совпали, разведение величин не значило бы ничего.
  assert.ok(RETRY_MS * 10 <= STALE_MS,
    `пауза повтора ${RETRY_MS} мс слишком близка к интервалу свежести ${STALE_MS} мс`)
  assert.ok(RETRY_MS >= 60_000, 'пауза короче минуты — это и есть «долбим сеть»')
})

test('обратный ход: одна метка на обе цели ломает один из двух случаев', () => {
  // Прежний код: длинный интервал считался от ЛЮБОЙ попытки.
  const oneMark = ({ now, tryAt }) => now - tryAt >= STALE_MS
  const okAt = NOW - 10 * 60 * 60 * 1000
  const tryAt = NOW - 6 * 60 * 1000
  assert.equal(oneMark({ now: NOW, tryAt }), false,
    'прежняя формула вдруг перестала воспроизводить дефект — проверка бессмысленна')
  assert.notEqual(shouldRefresh({ now: NOW, okAt, tryAt }), oneMark({ now: NOW, tryAt }),
    'новое правило ведёт себя как старое — починки нет')
})

// ── связь с продуктом: правило без вызывающего ничего не значит ─────────────────
test('сервис виджета пользуется этим правилом, а не своей копией', () => {
  const src = readFileSync(join(WEB, 'src/services/scheduleWidget.js'), 'utf8')
  assert.match(src, /import \{ shouldRefresh \} from '@\/utils\/widgetRefresh'/,
    'сервис не подключил правило')
  assert.match(src, /shouldRefresh\(/, 'правило импортировано, но не вызвано')
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  assert.ok(!/const\s+STALE_MS\s*=/.test(code) && !/const\s+RETRY_MS\s*=/.test(code),
    'в сервисе снова свои константы — две копии разъедутся молча')
})

test('обе метки времени снимаются при выходе из аккаунта', () => {
  // Смена владельца телефона: новый вошедший наследовал чужое «обновлено только что» —
  // снимок стёрт, а веб-путь считает данные свежими и не идёт за ними шесть часов.
  const src = readFileSync(join(WEB, 'src/services/scheduleWidget.js'), 'utf8')
  const clear = src.slice(src.indexOf('export function clear()'))
  assert.match(clear, /removeItem\('gb\.widget\.lastRefresh'\)/, 'метка успеха переживает выход')
  assert.match(clear, /removeItem\('gb\.widget\.lastTry'\)/, 'метка попытки переживает выход')
  const nativeAt = clear.indexOf('native()')
  const marksAt = clear.indexOf('lastRefresh')
  assert.ok(marksAt < nativeAt,
    'метки снимаются ПОСЛЕ выхода по отсутствию нативного моста — то есть в браузере не снимаются вовсе')
})
