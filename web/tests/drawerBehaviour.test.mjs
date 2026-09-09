// drawerBehaviour.test.mjs — ВЫЕЗЖАЮЩАЯ ШТОРКА НА ТЕЛЕФОНЕ.
//
// Две жалобы Влада (07.09.2026), и лечатся они одной перестройкой:
//  1. «при открытии настроек шторка слева остаётся, нужно кликать по пустому месту, с
//     другими вкладками такого нет»;
//  2. «при открытии вкладки всё слишком резко — надо, чтобы сначала подсветилась вкладка,
//     а после подгрузки шторка плавно уехала за экран».
//
// 🔥 ПОЧЕМУ ПЕРВОЕ СЛУЧИЛОСЬ. Шторку гасил `@navigate` от Sidebar, то есть закрытие
// зависело от того, что КАЖДАЯ ссылка о себе сообщит. Шестерёнка настроек живёт в
// карточке себя (SidebarUserPanel.vue) отдельным RouterLink — до Sidebar её клик не
// доходит. Классический «первое забытое место»: правило исполнялось в N местах.
//
// Проверяем СВОЙСТВО «закрытие привязано к смене маршрута», а не наличие обработчика у
// конкретной ссылки: завтра появится вторая такая дверь, и перечисление её не поймает.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const shell = readFileSync(new URL('../src/layouts/AppShell.vue', import.meta.url), 'utf8')
const sidebar = readFileSync(new URL('../src/components/Sidebar.vue', import.meta.url), 'utf8')
const userPanel = readFileSync(new URL('../src/components/SidebarUserPanel.vue', import.meta.url), 'utf8')
const styles = shell.split('<style')[1] || ''

test('🔥 шторку закрывает СМЕНА МАРШРУТА, а не отдельная ссылка', () => {
  assert.ok(/watch\(\(\) => route\.path,[\s\S]{0,200}?closeDrawer\(\)/.test(shell),
    'нет наблюдения за route.path — значит закрытие снова держится на аккуратности каждой ссылки')
})

test('🔒 дверь настроек из карточки себя действительно НЕ сообщает о переходе', () => {
  // Это не придирка к SidebarUserPanel, а фиксация условия задачи: пока такая ссылка
  // существует и молчит, правило ОБЯЗАНО жить в оболочке. Начнёт сообщать — этот тест
  // напомнит перечитать закрытие целиком.
  const gear = userPanel.match(/<RouterLink[^>]*settings[\s\S]{0,400}?<\/RouterLink>/)?.[0] || ''
  assert.ok(gear, 'в карточке себя не нашли ссылку на настройки — тест ослеп')
  assert.ok(!/emit\(/.test(gear),
    'ссылка настроек начала что-то эмитить: перепроверьте, не появилось ли ВТОРОЕ правило закрытия')
})

test('нажатие в ТОТ ЖЕ раздел закрывает шторку сразу', () => {
  // Маршрут не сменится, значит watch не сработает НИКОГДА — без этой ветки повторное
  // нажатие по уже открытому пункту оставляло бы меню висеть.
  assert.ok(/to === route\.path/.test(shell),
    'не обработано нажатие в текущий раздел — шторка залипнет на нём')
})

test('пункт подсвечивается ДО загрузки страницы', () => {
  assert.ok(/pendingTo/.test(shell), 'оболочка не помнит, куда нажали')
  assert.ok(/:pending="pendingTo"/.test(shell), 'подсветка не доезжает до Sidebar')
  assert.ok(/props\.pending === to/.test(sidebar),
    'Sidebar не учитывает «нажали, но ещё грузится» — нажатие снова будет выглядеть незасчитанным')
})

test('🔒 подсветка не может залипнуть навсегда', () => {
  // Навигация может не состояться вовсе (страж увёл, чанк не скачался). Без запасного
  // срока шторка осталась бы висеть с подсвеченным пунктом — хуже исходной жалобы.
  assert.ok(/PENDING_MAX_MS/.test(shell) && /setTimeout\(closeDrawer/.test(shell),
    'нет запасного срока: несостоявшийся переход оставит шторку открытой')
})

test('панель ЕДЕТ, а не проявляется', () => {
  assert.ok(/name="drawer"/.test(shell), 'шторка снова на общем fade — движения нет')
  assert.ok(/\.drawer-enter-from \.gb-drawer-panel[\s\S]{0,120}?translateX\(-100%\)/.test(styles)
    || /translateX\(-100%\)/.test(styles),
    'панель не выезжает слева — «слишком резко» вернётся')
  assert.ok(/cubic-bezier/.test(styles), 'без своей кривой движение читается рывком')
})

test('уход короче прихода', () => {
  const enter = Number(styles.match(/\.drawer-enter-active \.gb-drawer-panel\s*\{\s*transition:\s*transform\s*([\d.]+)s/)?.[1])
  const leave = Number(styles.match(/\.drawer-leave-active \.gb-drawer-panel\s*\{\s*transition:\s*transform\s*([\d.]+)s/)?.[1])
  assert.ok(enter > 0 && leave > 0, 'не нашли длительности — тест ослеп')
  assert.ok(leave < enter,
    'закрытие не быстрее открытия: решение уже принято, и равная длительность ощущается вязкой')
})

test('движение уважает prefers-reduced-motion', () => {
  assert.ok(/prefers-reduced-motion[\s\S]*?drawer-enter-active/.test(styles),
    'шторка едет даже у того, кто просил не двигать интерфейс')
})
