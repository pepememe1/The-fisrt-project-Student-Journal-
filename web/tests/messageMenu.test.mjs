// messageMenu.test.mjs — СОСТАВ меню сообщения: урезанного и «по выделению».
//
// Две просьбы Влада (05.09.2026) сходятся здесь:
//  • «меню над сообщением урезать максимально» — на снимке нашего мессенджера восемь
//    пунктов плюс две строки реакций, и меню не помещается в окно;
//  • «при выделении текста и нажатии ПКМ выйдет меню как в телеграм, в зависимости от
//    того, где мы выделяем (ЛС или канал)».
//
// Обратный ход (проверен): вернуть в `messageMenuItems` все пункты первым уровнем —
// краснеет «первый уровень не длиннее телеграмного»; убрать ветку `msg.mine` в
// `selectionMenuItems` — краснеют оба теста про своё/чужое сообщение.
import test from 'node:test'
import assert from 'node:assert/strict'
import { messageMenuItems, selectionMenuItems } from '../src/utils/messageMenu.js'

const own = { id: 1, mine: true, body: 'текст', deleted: false, pinned: false }
const foreign = { id: 2, mine: false, body: 'текст', deleted: false, pinned: false }

test('первый уровень меню не длиннее телеграмного', () => {
  // На снимках Telegram — шесть строк. Больше не помещается на телефоне в альбомной
  // ориентации, и именно это было видно на жалобе.
  for (const msg of [own, foreign]) {
    assert.ok(messageMenuItems(msg).primary.length <= 6,
      `первый уровень раздулся: ${messageMenuItems(msg).primary}`)
  }
})

test('редкое уехало под «Ещё», а не удалено', () => {
  // Вырезать «Зачитать», «Напомнить» и перевод значило бы чинить длину меню потерей
  // возможностей — за ними приходят редко, но приходят.
  const more = messageMenuItems(foreign).more
  for (const key of ['speak', 'translate', 'remind']) {
    assert.ok(more.includes(key), `${key} пропал из меню совсем`)
  }
})

test('перевод предлагается только для ЧУЖОГО сообщения', () => {
  // Своё и так на том языке, на котором написано.
  assert.ok(!messageMenuItems(own).more.includes('translate'))
})

test('у удалённого сообщения меню состоит из одного «Выделить»', () => {
  // Остальное относится к тексту, которого больше нет: «Ответить» на тумбстоуне —
  // предложение невыполнимого.
  const menu = messageMenuItems({ ...foreign, deleted: true })
  assert.deepEqual(menu.primary, ['select'])
  assert.deepEqual(menu.more, [])
})

test('закрепление показывается одним пунктом, а не двумя сразу', () => {
  assert.ok(messageMenuItems(own).primary.includes('pin'))
  assert.ok(messageMenuItems({ ...own, pinned: true }).primary.includes('unpin'))
  assert.ok(!messageMenuItems({ ...own, pinned: true }).primary.includes('pin'))
})

// ── Меню по выделению (снимки 02 и 03 задания) ─────────────────────────────────────
test('по выделению первым идёт «Ответить с цитатой»', () => {
  // Это и есть то действие, ради которого текст выделяли.
  assert.equal(selectionMenuItems(foreign, { kind: 'group' })[0], 'quote-reply')
})

test('у ЧУЖОГО сообщения есть «Пожаловаться» и нет «Удалить»', () => {
  const items = selectionMenuItems(foreign, { kind: 'group', canPin: true })
  assert.ok(items.includes('report'))
  assert.ok(!items.includes('delete'))
})

test('у СВОЕГО — «Закрепить» и «Удалить», без жалобы на себя', () => {
  const items = selectionMenuItems(own, { kind: 'group', canPin: true })
  assert.ok(items.includes('pin') && items.includes('delete'))
  assert.ok(!items.includes('report'))
})

test('без права закреплять пункта закрепления нет', () => {
  // Кнопка, которая гарантированно вернёт 403, хуже отсутствующей.
  const items = selectionMenuItems(own, { kind: 'channel', canPin: false })
  assert.ok(!items.includes('pin') && !items.includes('unpin'))
})

test('ссылка на сообщение — не в ЛИЧНОЙ переписке', () => {
  // Ссылка из чата на двоих либо не откроется у третьего, либо откроет ему чужое.
  assert.ok(!selectionMenuItems(foreign, { kind: 'direct', canLink: true }).includes('copy-link'))
  assert.ok(selectionMenuItems(foreign, { kind: 'channel', canLink: true }).includes('copy-link'))
})

test('ссылки нет вовсе, если клиент её не умеет', () => {
  // Пункт, кладущий в буфер адрес, по которому ничего не открывается, хуже отсутствующего:
  // человек отправит его собеседнику и узнает об этом от него.
  assert.ok(!selectionMenuItems(foreign, { kind: 'channel', canLink: false }).includes('copy-link'))
})

test('на удалённом сообщении меню по выделению пустое', () => {
  assert.deepEqual(selectionMenuItems({ ...foreign, deleted: true }, { kind: 'group' }), [])
})
