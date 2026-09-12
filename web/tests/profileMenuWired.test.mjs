/**
 * profileMenuWired.test.mjs — у каждого пункта меню профиля есть ВЫЗЫВАЮЩИЙ (12.09.2026).
 *
 * ━━ ЗАЧЕМ ━━
 * 🔥 Этот файл заведён по СЛУЧИВШЕМУСЯ дефекту, а не на всякий случай. Пункт «Добавить в
 * группу» был написан, отрисован и не делал НИЧЕГО: карточка профиля эмитила событие,
 * а слушателя не было ни одного. Ни сборка, ни линтер такого не видят — разметка верна,
 * ошибок в консоли нет, кнопка просто молчит. Это наш самый частый класс дефекта
 * («обещание без вызывающего»), и нашёлся он перечитыванием собственной работы.
 *
 * ⚠️ Проверяем СВЯЗЬ, а не поведение: поведение живёт в браузере, а связь — это ровно то,
 * что теряется при правке соседнего компонента. Сторож обязан краснеть, если удалить
 * строку обработчика.
 *
 * ⚠️ ОБРАТНЫЙ ХОД ПРОВЕРЕН: убрать `@chat-action` у карточки в ConversationInfo —
 * краснеет первый тест; убрать ветку 'add-to-group' из `onChatAction` — второй; убрать
 * `watch` за `groupDraftPeer` в списке чатов — третий.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (rel) => readFileSync(
  new URL(rel, import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'), 'utf8')

const card = read('../src/components/messenger/PeerProfileCard.vue')
const info = read('../src/components/messenger/ConversationInfo.vue')
const list = read('../src/components/messenger/ChatList.vue')
const store = read('../src/stores/messenger.js')

test('каждое действие из трёх точек кем-то обрабатывается', () => {
  //Действия, которые карточка отправляет наружу: `ask('<action>')`.
  const actions = [...card.matchAll(/ask\('([a-z-]+)'\)/g)].map((m) => m[1])
  assert.ok(actions.length >= 3, 'меню профиля опустело — проверьте, не потерялись ли пункты')

  //Карточка сама их не выполняет: она может не знать беседы (её открывают и из каталога).
  assert.ok(/@chat-action="onChatAction"/.test(info),
            'ConversationInfo не слушает chat-action — все пункты меню будут молчать')

  for (const a of actions) {
    assert.ok(info.includes(`'${a}'`),
              `действие «${a}» отправляется, но в onChatAction его никто не разбирает`)
  }
})

test('«Добавить в группу» доходит до пикера участников', () => {
  //Цепочка: карточка → ConversationInfo → стор → список чатов → диалог.
  //Она проходит через РАЗНЫЕ ветки дерева, поэтому связь и рвётся незаметно.
  assert.ok(/askAddToGroup\(/.test(info), 'просьба не уходит в стор')
  assert.ok(/askAddToGroup/.test(store) && /groupDraftPeer/.test(store),
            'в сторе нет канала для просьбы')
  assert.ok(/groupDraftPeer/.test(list), 'список чатов не следит за просьбой')
  assert.ok(/:preset="m\.groupDraftPeer"/.test(list),
            'предвыбранный человек не передаётся в пикер — он окажется пустым')
})

test('очистка просьбы при закрытии пикера', () => {
  //Без неё вторая попытка открыть создание группы вручную сразу подставляла бы того же
  //человека — и объяснить это было бы нечем.
  assert.ok(/clearGroupDraft\(\)/.test(list), 'просьба не снимается при закрытии диалога')
})
