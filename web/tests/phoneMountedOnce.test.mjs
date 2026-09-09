/**
 * phoneMountedOnce.test.mjs — ЧТО НЕ ВИДНО, ТО НЕ ДОЛЖНО И РАБОТАТЬ (B5 и B6, 07.09.2026).
 *
 * ━━ ДВА ДЕФЕКТА ОДНОГО КЛАССА ━━
 * B5. `<div v-if="!embed" class="hidden lg:block"><Sidebar /></div>` — класс прячет
 *     панель ГЛАЗАМИ, но оставляет её СМОНТИРОВАННОЙ. На телефоне постоянная боковая
 *     панель жила всё время: грузила профиль, спрашивала статус, вела слежение за
 *     бездействием. А при открытии мобильной шторки рядом появлялся ВТОРОЙ такой же
 *     экземпляр со своими запросами и таймерами — то есть телефон, где панели не видно
 *     вовсе, делал её работу дважды.
 * B6. В `SidebarUserPanel.vue` был `addEventListener`, а парного `removeEventListener`
 *     не было вовсе. Каждое открытие шторки добавляло два обработчика, и каждый скачок
 *     связи будил их все разом.
 *
 * ━━ ПОЧЕМУ СВОЙСТВО, А НЕ ДВА ТОЧЕЧНЫХ ТЕСТА ━━
 * B6 — не свойство одного файла, а свойство ЛЮБОГО компонента: он монтируется столько
 * раз, сколько человек откроет экран, и не снятый обработчик копится линейно. Поэтому
 * проверяются ВСЕ компоненты сразу, а точечно — только разметка оболочки.
 *
 * ⚠️ Модули уровня приложения (`main.js`, `router/index.js`, `services/push.js`,
 * `utils/appVersion.js`) под правило НЕ подпадают и это записанное решение, а не
 * недосмотр: они исполняются один раз за сеанс, их обработчик обязан жить всё время
 * работы приложения, и снимать его негде и незачем. Исключение сформулировано КАТАЛОГАМИ
 * (компоненты/страницы/раскладки), а не списком имён, — списки имён устаревают молча.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const WEB = dirname(dirname(fileURLToPath(import.meta.url)))

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(vue|js)$/.test(name)) out.push(p)
  }
  return out
}

function code(path) {
  return readFileSync(path, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*/gm, '')
    .replace(/<!--[\s\S]*?-->/g, '')
}

/** Пары «цель + событие», которые вешают, но не снимают. */
function unpairedListeners(src) {
  const added = new Set()
  const removed = new Set()
  const add = /(window|document)\.addEventListener\(\s*['"]([\w:-]+)['"]/g
  const rem = /(window|document)\.removeEventListener\(\s*['"]([\w:-]+)['"]/g
  let m
  while ((m = add.exec(src))) added.add(m[1] + ':' + m[2])
  while ((m = rem.exec(src))) removed.add(m[1] + ':' + m[2])
  return [...added].filter((k) => !removed.has(k)).sort()
}

// ── B6: обработчики снимаются ───────────────────────────────────────────────────
test('сам разбор работает: видит непарный обработчик и не видит парный', () => {
  // 🔒 Проверка НАД проверкой: сломанный разбор делает весь файл зелёным впустую.
  assert.deepEqual(unpairedListeners("window.addEventListener('online', f)"),
    ['window:online'], 'разбор не находит обработчик вовсе')
  assert.deepEqual(unpairedListeners(
    "window.addEventListener('online', f); window.removeEventListener('online', f)"),
  [], 'разбор не видит снятия — будут ложные срабатывания')
})

test('ни один компонент не оставляет висеть обработчик окна', () => {
  const dirs = ['src/components', 'src/pages', 'src/layouts']
  const offenders = []
  for (const rel of dirs) {
    for (const p of walk(join(WEB, rel))) {
      const miss = unpairedListeners(code(p))
      if (miss.length) offenders.push(p.slice(p.indexOf('src')) + ' → ' + miss.join(', '))
    }
  }
  assert.deepEqual(offenders, [],
    'обработчик вешается и не снимается — за день работы на телефоне их накопятся десятки')
})

test('ОБРАТНЫЙ ХОД: правило ловит дословный код SidebarUserPanel до починки', () => {
  const OLD = `
onMounted(() => {
  window.addEventListener('online', updateOnline)
  window.addEventListener('offline', updateOnline)
})`
  assert.deepEqual(unpairedListeners(OLD), ['window:offline', 'window:online'],
    'правило не видит прежний дефект — оно не защищает ни от чего')
})

// ── B5: панель не смонтирована там, где её не видно ─────────────────────────────
const shell = readFileSync(join(WEB, 'src/layouts/AppShell.vue'), 'utf8')

/** Условие `v-if` у блока с постоянной боковой панелью. */
function permanentSidebarCondition(src) {
  // Берём блок, внутри которого класс `lg:block` — именно им панель и прятали «глазами».
  const m = src.match(/<div\s+v-if="([^"]+)"[^>]*class="[^"]*lg:block[^"]*"/)
  return m ? m[1] : null
}

test('постоянная панель СМОНТИРОВАНА только на широком экране', () => {
  const cond = permanentSidebarCondition(shell)
  assert.ok(cond, 'не нашёл блок постоянной панели — разбор устарел вместе с разметкой')
  assert.match(cond, /\bisLg\b/,
    'панель снова прячется только классом: на телефоне она живёт, грузит профиль и '
    + 'дублируется вторым экземпляром при открытии шторки')
})

test('порог берётся из общего LG_PX, а не заводится второй копией', () => {
  // Разъедутся — появится полоса ширины, где панель нарисована, а код считает нас
  // телефоном. Ту же связь держит breakpoint.test.mjs — здесь только про монтирование.
  assert.match(shell, /const LG_PX = \d+/, 'порог пропал из оболочки')
  assert.match(shell, /isLg\s*=\s*ref\([^)]*matchMedia/,
    'isLg перестал считаться по медиазапросу — он разойдётся с CSS')
})

test('ОБРАТНЫЙ ХОД: правило ловит дословную разметку до починки', () => {
  const OLD = '<div v-if="!embed" class="hidden lg:block">\n      <Sidebar />\n    </div>'
  const cond = permanentSidebarCondition(OLD)
  assert.equal(cond, '!embed', 'разбор не нашёл условие в прежней разметке')
  assert.ok(!/\bisLg\b/.test(cond),
    'правило считает прежнюю разметку исправной — оно бесполезно')
})
