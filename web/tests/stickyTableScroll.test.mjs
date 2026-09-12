/**
 * stickyTableScroll.test.mjs — ползунок широкой таблицы прижат к низу экрана (12.09.2026).
 *
 * ━━ ЗАЧЕМ ━━
 * Жалоба Влада: у таблиц, которые не влезают в монитор по ширине, полоса прокрутки живёт
 * на нижней границе САМОЙ ТАБЛИЦЫ. У журнала на тридцать студентов это значит: чтобы
 * сдвинуть таблицу вбок, надо промотать страницу до самого низа, там подвинуть ползунок и
 * вернуться наверх — и так на каждый шаг. Прокрутка формально есть, пользоваться ею нельзя.
 *
 * ⚠️ Ни сборка, ни линтер этого не видят: разметка валидна, ошибок нет. И «посмотреть
 * глазами» тоже не поможет — на широком мониторе разработчика таблица помещается.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const SRC = new URL('../src/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')

function walk(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) walk(full, acc)
    else if (name.endsWith('.vue')) acc.push(full)
  }
  return acc
}

const FILES = walk(SRC).map((p) => ({
  rel: p.slice(SRC.length).replace(/\\/g, '/'),
  text: readFileSync(p, 'utf8'),
}))
const component = FILES.find((f) => f.rel.endsWith('ui/StickyXScroll.vue'))

/**
 * Обёртки, где горизонтальная прокрутка НЕ про таблицу, — с причиной у каждой.
 *
 * ⚠️ Список закрытый и проверяется на мёртвые записи: исключение, объяснённое одной
 * фразой и забытое, — это не исключение, а пропущенный случай (наш урок про «downloads»
 * в списке префиксов API).
 */
const NOT_A_TABLE = {
  'components/activity/board/BoardCanvas.vue': 'доска активности — своя область прокрутки и свои жесты',
  'components/messenger/SharedGroupsChannels.vue': 'горизонтальная лента карточек, а не таблица',
  'components/server/RemoteConsole.vue': 'вывод консоли: длинные строки, прокрутка внутри блока',
  'components/ui/StickyXScroll.vue': 'сам компонент',
  'components/messenger/CuratorReportOverlay.vue':
    'таблица внутри ОВЕРЛЕЯ со своей прокруткой — липкий низ приклеился бы к окну модалки, а не к экрану',
  'pages/admin/AdminServer.vue': 'обзор каталога: колонки узкие, таблицы нет',
}

test('компонент прижимает полосу к низу экрана и синхронизирует обе прокрутки', () => {
  assert.ok(component, 'компонент StickyXScroll.vue не найден')
  const t = component.text
  assert.ok(/sticky bottom-0/.test(t), 'полоса не липкая — она снова уедет вниз страницы')
  // Синхронизация обязана быть В ОБЕ стороны: таблицу тянут и за неё саму (колесом с
  // Shift, свайпом), и за полосу.
  assert.ok(/bar\.value\.scrollLeft = body\.value\.scrollLeft/.test(t), 'нет синхронизации таблица → полоса')
  assert.ok(/body\.value\.scrollLeft = bar\.value\.scrollLeft/.test(t), 'нет синхронизации полоса → таблица')
  // Без флага присваивание scrollLeft рождает событие у соседа — и прокрутка задребезжит.
  assert.ok(/syncing/.test(t), 'нет защиты от петли синхронизации')
})

test('полоса показывается, только когда есть что прокручивать', () => {
  // Пустая полоса под каждой узкой таблицей — шум, который перестают замечать.
  const t = component.text
  assert.ok(/v-show="needed"/.test(t), 'полоса рисуется всегда')
  assert.ok(/scrollWidth - el\.clientWidth > 1/.test(t),
    'переполнение считается без запаса — дробные ширины дадут вечную полосу')
  assert.ok(/ResizeObserver/.test(t),
    'ширина не пересчитывается: столбцы меняются от данных, а не только от размера окна')
})

test('каждая таблица лежит в StickyXScroll, а не в голом overflow-x-auto', () => {
  const offenders = []
  for (const f of FILES) {
    if (NOT_A_TABLE[f.rel]) continue
    if (!f.text.includes('<table')) continue
    // Голая обёртка рядом с таблицей — это ровно тот случай, из-за которого поступила
    // жалоба: прокрутка есть, но ползунок внизу таблицы.
    if (/<div[^>]*class="[^"]*\boverflow-x-auto\b/.test(f.text) && !f.text.includes('<StickyXScroll')) {
      offenders.push(f.rel)
    }
  }
  assert.deepEqual(offenders, [],
    'таблица прокручивается голым overflow-x-auto: ползунок останется на дне страницы')
})

test('в списке исключений нет мёртвых записей', () => {
  // Файл переименовали или обёртку убрали — исключение обязано исчезнуть вместе с ним,
  // иначе список молча защищает то, чего нет.
  const known = new Set(FILES.map((f) => f.rel))
  const dead = Object.keys(NOT_A_TABLE).filter((rel) => !known.has(rel))
  assert.deepEqual(dead, [], 'исключение указывает на несуществующий файл')
  for (const [rel, why] of Object.entries(NOT_A_TABLE)) {
    assert.ok(why && why.length > 10, `у исключения ${rel} нет внятной причины`)
  }
})

test('обратный ход: голая обёртка вокруг таблицы обязана ловиться', () => {
  // Сторож, зелёный и без починки, неотличим от исправного кода.
  const broken = '<div class="overflow-x-auto rounded-lg"><table class="w-full"></table></div>'
  assert.ok(/<div[^>]*class="[^"]*\boverflow-x-auto\b/.test(broken) && !broken.includes('<StickyXScroll'),
    'разбор не видит дословную форму дефекта')
  const fixed = '<StickyXScroll class="rounded-lg"><table class="w-full"></table></StickyXScroll>'
  assert.ok(fixed.includes('<StickyXScroll'), 'починенная форма обязана считаться исправной')
})
