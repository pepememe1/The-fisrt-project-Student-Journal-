/**
 * easterAssets.test.mjs — файл, на который ссылается пасхалка, обязан существовать.
 *
 * 🔥 Почему это нужен ОТДЕЛЬНЫЙ сторож. Пасхалка срабатывает раз в сотню входов и по
 * случайности — битую ссылку на звук никто не заметит ни на сборке (Vite файлы из
 * `public/` не проверяет), ни на линтере, ни глазами: сцена просто отыграет молча, и
 * выглядит это как «задумано так». Поймать можно только сверкой с диском.
 *
 * Повод завести: 28.08.2026 звук пасхалок пережали (4.2 МБ → 1.7 МБ), и расширения
 * поменялись с `.mp3`/`.mp4` на `.m4a`. Одна ссылка собиралась конкатенацией и по
 * этой причине не попала под замену — её нашёл grep, а мог и не найти.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, existsSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const SRC = fileURLToPath(new URL('../src', import.meta.url))
const PUBLIC = fileURLToPath(new URL('../public', import.meta.url))

function walk(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.(vue|js)$/.test(name)) out.push(p)
  }
  return out
}

const files = walk(SRC)
const code = files.map((f) => readFileSync(f, 'utf8')).join('\n')

//Полные ссылки: '/easter/snd/vaas.m4a'. Составные («…narrator-' + key + …») сюда не
//попадают по построению — у них после префикса идёт кавычка, а не расширение.
const FULL = [...code.matchAll(/['"`](\/easter\/[a-zA-Z0-9/_-]+\.[a-z0-9]{2,5})['"`]/g)]
  .map((m) => m[1])

test('каждая ссылка на файл пасхалки существует на диске', () => {
  assert.ok(FULL.length > 5, `ссылок нашлось всего ${FULL.length} — тест выродился`)
  const missing = [...new Set(FULL)].filter((u) => !existsSync(join(PUBLIC, u)))
  assert.deepEqual(missing, [], `нет файлов: ${missing.join(', ')}`)
})

test('составные ссылки тоже находят свои файлы', () => {
  //`StanleyNarrator.vue` собирает имя из частей: '/easter/snd/narrator-' + key + '-' + n
  //+ '.m4a'. Полная строка в коде не встречается, поэтому проверяем префикс и расширение
  //отдельно — иначе переименование расширения прошло бы мимо теста именно здесь.
  const partial = [...code.matchAll(/['"`](\/easter\/[a-zA-Z0-9/_-]*[a-z-])['"`]\s*\+/g)]
    .map((m) => m[1])
  for (const prefix of new Set(partial)) {
    const dir = join(PUBLIC, prefix.slice(0, prefix.lastIndexOf('/')))
    const base = prefix.slice(prefix.lastIndexOf('/') + 1)
    const hits = readdirSync(dir).filter((f) => f.startsWith(base))
    assert.ok(hits.length > 0, `под префикс «${prefix}» нет ни одного файла`)
  }
})

// 🔥 `.ogg` УБРАН ИЗ РАЗРЕШЁННЫХ (07.09.2026). Список сам себе противоречил: в
// комментарии сказано «в Safari на iOS в <audio> не играют, а сайт открывают и с
// айфонов» — и это верно НЕ ТОЛЬКО для Opus/WebM. Vorbis в контейнере Ogg Safari не
// поддерживает тоже, ни на iOS, ни на macOS. То есть формат, ради которого написано
// предупреждение, стоял в белом списке.
//
// ⚠️ ИСКЛЮЧЕНИЕ С ПРИЧИНОЙ И СРОКОМ, а не «чтобы позеленело». `tree.ogg` — единственный
// такой файл, это МУЗЫКА пасхалки с деревом Делтарун. Перекодировать его надо
// `tools/build_easter_audio.py --apply` на машине с ffmpeg (на машине, где найден дефект,
// его нет). Пережать нечем прямо сейчас — но и промолчать нельзя: на айфоне музыки не
// слышно, и это ровно половина жалобы «музыка и некоторые звуки на телефонах не слышно».
// На Android (наше приложение в RuStore) Vorbis играет, поэтому дефект виден только в
// мобильном Safari.
const OGG_TODO = ['tree.ogg']

test('расширения звука — только те, что играют ВЕЗДЕ, включая Safari', () => {
  const ALLOWED = ['.m4a', '.mp3', '.wav']
  const snd = readdirSync(join(PUBLIC, 'easter', 'snd'))
  const bad = snd.filter((f) => !ALLOWED.some((e) => f.endsWith(e)) && !OGG_TODO.includes(f))
  assert.deepEqual(bad, [],
    `неподходящий формат звука: ${bad.join(', ')} — пережмите tools/build_easter_audio.py --apply`)
})

test('в списке «пережать» нет мёртвых записей', () => {
  // Мёртвая запись превращает исключение в забытый случай: файл давно пережали, а список
  // продолжает молча разрешать формат, которого уже нет.
  const snd = readdirSync(join(PUBLIC, 'easter', 'snd'))
  const dead = OGG_TODO.filter((f) => !snd.includes(f))
  assert.deepEqual(dead, [],
    `эти файлы уже пережаты — уберите их из OGG_TODO: ${dead.join(', ')}`)
})

test('звук пасхалок остаётся в разумном весе', () => {
  //Не «красивое число», а граница, за которой цена снова становится заметной: всё из
  //public/ едет и в OTA-бандл на каждый телефон, и в .exe. До сжатия было 4.2 МБ.
  const dir = join(PUBLIC, 'easter', 'snd')
  const total = readdirSync(dir).reduce((s, f) => s + statSync(join(dir, f)).size, 0)
  assert.ok(total < 2.5 * 1024 * 1024,
    `звук пасхалок разросся до ${(total / 1024 / 1024).toFixed(1)} МБ — пережми tools/build_easter_audio.py`)
})
