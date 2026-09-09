// buildStamp.test.mjs — метка сборки реально доезжает и до бандла, и до /version.json.
//
// Правила «пора ли перезагружаться» проверены отдельно (buildFreshness.test.mjs). Здесь
// проверяется ПРОВОДКА: правило бесполезно, если сравнивать нечего. Отказ тут тихий —
// метка не подставилась, `BUILD_STAMP` пуст, `watchForNewBuild` молча выходит первой
// строкой, и жалоба «после деплоя показывается старая версия» возвращается целиком.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import buildStamp from '../build/buildStamp.js'

const vite = readFileSync(new URL('../vite.config.js', import.meta.url), 'utf8')
const sw = readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8')
const main = readFileSync(new URL('../src/main.js', import.meta.url), 'utf8')

test('плагин подключён к сборке', () => {
  assert.ok(/import buildStamp from '\.\/build\/buildStamp\.js'/.test(vite), 'плагин не импортирован')
  assert.ok(/plugins:\s*\[[^\]]*buildStamp\(\)/.test(vite), 'плагин не в списке plugins')
})

test('🔥 метка в бандле и в version.json — ОДНА И ТА ЖЕ', () => {
  // Два независимых значения давали бы вечное «версия сменилась», то есть бесконечную
  // перезагрузку вкладки. Проверяем на самом плагине, а не на его описании.
  const p = buildStamp('12345')
  const defined = JSON.parse(p.config().define.__BUILD_STAMP__)
  const emitted = []
  p.generateBundle.call({ emitFile: (f) => emitted.push(f) })
  const version = emitted.find((f) => f.fileName === 'version.json')
  assert.ok(version, 'version.json не выкладывается — сравнивать будет не с чем')
  assert.equal(JSON.parse(version.source).stamp, defined,
    'метка в бандле и в файле разошлись — вкладка перезагружалась бы бесконечно')
})

test('у каждой сборки метка своя', () => {
  assert.notEqual(
    JSON.parse(buildStamp('1').config().define.__BUILD_STAMP__),
    JSON.parse(buildStamp('2').config().define.__BUILD_STAMP__))
})

test('🔒 service worker НЕ кэширует version.json', () => {
  // Отданная из кэша метка всегда равна себе самой — проверка обновления замолчала бы
  // ровно после деплоя, то есть тогда, когда она единственно и нужна.
  assert.ok(/url\.pathname === '\/version\.json'\) return/.test(sw),
    'version.json проходит через кэш — обновление перестанет замечаться')
})

test('оба конца лечения подключены при старте', () => {
  assert.ok(/reloadOnChunkError\(router\)/.test(main), 'отказ загрузки чанка никто не ловит')
  assert.ok(/watchForNewBuild\(\)/.test(main), 'новую сборку никто не замечает')
})
