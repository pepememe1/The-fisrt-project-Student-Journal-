// sharedAudioContext.test.mjs — ЗВУК БУДЯТ В ОДНОМ МЕСТЕ.
//
// 🔥 ЖАЛОБА (Влад, 07.09.2026): «музыка и некоторые звуки на телефонах не слышно».
//
// Причина: AudioContext'ов в проекте было ЧЕТЫРЕ, каждый заводил свой, а будили ДВА.
// Пинг отметки (`pingSound`) и бубнёж пасхалок (`mumble`) создавали контекст в момент,
// когда звук уже нужен, — то есть вне жеста человека. На телефоне политика автоплея
// оставляет такой контекст `suspended`, и не играет ничего. Молча: каждый вызов обёрнут
// в try/catch, ошибки нет ни в консоли, ни на экране.
//
// ⚠️ И это был наш записанный класс: в шапке `pingSound.js` стояло «тот же AudioContext
// „будит" вход в приложение» — обещание, которого код не выполнял ни дня.
//
// Сторож проверяет СВОЙСТВО «своих контекстов больше не заводят», а не список файлов:
// пятый заведут завтра, и список о нём не узнает.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'

const SRC = new URL('../src/', import.meta.url)

/** Все .js/.vue в src/, кроме самого модуля общего контекста. */
function walk(dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const u = new URL(e.name + (e.isDirectory() ? '/' : ''), dir)
    if (e.isDirectory()) walk(u, out)
    else if (/\.(js|vue)$/.test(e.name)) out.push(u)
  }
  return out
}

// ⚠️ ИСКЛЮЧЕНИЕ С ПРИЧИНОЙ, а не «чтобы позеленело»: у озвучки Вектора свой конвейер
// (декодирование буферов, barge-in по поколениям), и она САМА будит свой контекст из
// жеста — `tts.unlock()` зовут ChatThread.vue и Settings.vue. Трогать рабочее ради
// единообразия дороже, чем описать здесь. Если у неё пропадёт `unlock` — покраснеет
// проверка ниже.
const ALLOWED_OWN_CONTEXT = ['src/stores/tts.js']

const files = walk(SRC).filter((u) => !String(u).endsWith('src/utils/audioContext.js'))

test('свой AudioContext заводит только тот, кому разрешено — и с причиной', () => {
  // ⚠️ ЛОВИМ САМО ИМЯ, А НЕ ФОРМУ ВЫЗОВА. Первая редакция этого сторожа искала
  // `new AudioContext(` / `new window.AudioContext` — и НЕ ПОКРАСНЕЛА на обратном ходе,
  // потому что настоящий дефект был написан иначе:
  //     const Ctor = window.AudioContext || window.webkitAudioContext
  //     _ctx = new Ctor()
  // То есть проверка не увидела бы ровно ту форму, ради которой заведена, — наш
  // записанный класс «сторож, который не может покраснеть». Форм записи бесконечно
  // много, а имя одно: у того, кто пользуется общим контекстом, слова `AudioContext` в
  // файле нет вовсе.
  const guilty = []
  for (const u of files) {
    const rel = 'src/' + String(u).split('/src/')[1]
    if (ALLOWED_OWN_CONTEXT.includes(rel)) continue
    // Комментарии выбрасываем: про контекст можно и нужно ПИСАТЬ, нельзя его заводить.
    // Комментарии выбрасываем ВСЕ, включая хвостовые: про контекст можно и нужно
    // ПИСАТЬ (в ChatThread.vue и Settings.vue стоит пояснение «разбудить AudioContext из
    // жеста»), нельзя его ЗАВОДИТЬ. Без хвостовых сторож ловил бы эти пояснения и
    // требовал бы их убрать — то есть наказывал бы за правильно написанный комментарий.
    const code = readFileSync(u, 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*(\/\/|\*).*$/gm, '')
      .replace(/([^:])\/\/.*$/gm, '$1')
    // ⚠️ БЕЗ РЕГУЛЯРКИ НАМЕРЕННО. Первая редакция искала `/\b(webkit)?AudioContext\b/`,
    // но патч писался питоновским скриптом, где `\b` — это символ BACKSPACE (0x08).
    // Регулярка выглядела безупречно и не совпадала НИ С ЧЕМ: сторож был зелёным при
    // нарочно возвращённом дефекте. Ровно грабля из CLAUDE.md §8.1(5). Простое вхождение
    // подстроки здесь и точнее, и сломать его нечем.
    if (code.includes('AudioContext')) guilty.push(rel)
  }
  assert.deepEqual(guilty, [],
    `эти модули добираются до AudioContext сами — на телефоне его никто не разбудит: ${guilty.join(', ')}`)
})

test('исключение не протухло: у озвучки по-прежнему есть своя разблокировка', () => {
  // Мёртвая запись в списке исключений — это забытый случай, а не исключение.
  const tts = readFileSync(new URL('stores/tts.js', SRC), 'utf8')
  assert.ok(/unlock/.test(tts),
    'у stores/tts.js пропал unlock() — значит исключение больше не обосновано, переводите на общий контекст')
})

test('пинг и бубнёж играют через общий контекст', () => {
  for (const f of ['utils/pingSound.js', 'utils/mumble.js', 'utils/alarmSound.js']) {
    const text = readFileSync(new URL(f, SRC), 'utf8')
    assert.ok(/from '\.\/audioContext'/.test(text), `${f} не подключён к общему контексту`)
    assert.ok(/withAudio\(/.test(text), `${f} не пользуется withAudio — звук снова может уйти в тишину`)
  }
})

test('🔒 контекст будят ПЕРВЫМ жестом, и это записано кодом, а не обещанием', () => {
  const main = readFileSync(new URL('main.js', SRC), 'utf8')
  assert.ok(/primeAudio/.test(main), 'никто не будит общий контекст при старте')
  assert.ok(/addEventListener\('pointerdown',\s*primeAudio/.test(main),
    'разблокировка не привязана к жесту — вне жеста браузер её игнорирует')
})

test('🔥 ноты планируют ПОСЛЕ пробуждения, а не сразу за resume()', () => {
  // Вторая половина дефекта: resume() асинхронный. У спящего контекста часы стоят, и
  // звук, назначенный на currentTime следующей же строкой, попадал в УЖЕ ПРОШЕДШЕЕ
  // время — то есть не звучал вовсе, даже когда контекст успевал проснуться.
  const ctx = readFileSync(new URL('utils/audioContext.js', SRC), 'utf8')
  assert.ok(/\.then\(run/.test(ctx),
    'после resume() нет ожидания — звук снова будет назначаться в прошлое')
  for (const f of ['utils/pingSound.js', 'utils/mumble.js', 'utils/alarmSound.js']) {
    const text = readFileSync(new URL(f, SRC), 'utf8')
    assert.ok(!/\.resume\(\)/.test(text),
      `${f} будит контекст сам — значит снова планирует звук, не дождавшись пробуждения`)
  }
})
