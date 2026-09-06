<script setup>
// MessengerPage — вкладка «Сообщения». Трёхколоночный каркас в стиле Telegram:
//   A — навигация (поиск + вкладки + список чатов/каталог)  [ChatList]
//   B — карточка собеседника СЛЕВА от чата (портфолио)       [ProfilePanel]  (виден с xl)
//   C — переписка СПРАВА (лента + композер)                  [ChatThread]
// Транспорт Фазы 2 — опрос (store.startPolling); WebSocket добавим отдельной фазой.
import { onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { useEasterStore } from '@/stores/easterEggs'
import { useMessengerStore } from '@/stores/messenger'
import ChatList from '@/components/messenger/ChatList.vue'
import ProfilePanel from '@/components/messenger/ProfilePanel.vue'
import ChatThread from '@/components/messenger/ChatThread.vue'

const m = useMessengerStore()

// В embed (десктоп-веб-view) шапка и заголовок SPA скрыты, поэтому вычитать их высоту
// (calc(100dvh-8rem)) нельзя — иначе снизу остаётся пустая полоса. Тогда тянемся на всю
// высоту родителя (AppShell в embed даёт контенту h-full).
const embed = (() => {
  try {
    return new URLSearchParams(location.search).get('embed') === '1'
      || localStorage.getItem('gb.embed') === '1'
  } catch { return false }
})()

// Ссылка на сообщение (`?chat=…&msg=…`) — пункт «Копировать ссылку на сообщение» в меню
// по выделению. Открываем беседу и просим ленту перемотаться к строке.
//
// ⚠️ Ссылка ОТНОСИТЕЛЬНАЯ и ведёт внутрь кабинета: беседа всё равно откроется только
// участнику, а неучастнику сервер ответит 403. То есть ссылка — удобство навигации, а
// не способ поделиться перепиской.
//
// ⚠️ Сначала СПИСОК чатов, потом переход: `openById` берёт из него заголовок, чтобы шапка
// не мигнула пустой. Отказ загрузки списка переходу не мешает.
const route = useRoute()
onMounted(async () => {
  await m.loadChats().catch(() => {})
  m.startPolling()
  const conv = String(route.query.chat || '')
  if (conv) m.openById(conv, Number(route.query.msg || 0))
})
onBeforeUnmount(() => { m.stopPolling() })
// Hotline Miami при входе во вкладку «Сообщения». Бросок серверный, как и везде.
const easter = useEasterStore()
onMounted(() => easter.roll('hotline_miami'))

</script>

<template>
  <!-- Высота — от ОДНОЙ переменной оболочки (--gb-page-offset в style.css), а не от
       собственного набора чисел: свой набор устарел молча после перекомпоновки 3.5.6, и
       снизу осталась мёртвая полоса в полторы сотни пикселей.
       ⚠️ `gb-fullbleed` (3.7) действует ТОЛЬКО ниже sm и делает две вещи разом: убирает
       боковую рамку контейнера и пересчитывает высоту под свой, уже без отступов, хром
       (см. комментарий у класса в style.css). Классы Tailwind ниже он перебивает не
       «по случайности» — правило вне @layer всегда сильнее утилит внутри слоя. -->
  <div class="flex overflow-hidden rounded-lg border border-border bg-card shadow-card"
       :class="embed ? 'h-full' : 'gb-fullbleed h-[calc(100dvh-var(--gb-page-offset))]'">
    <ChatList />
    <ProfilePanel />
    <ChatThread />
  </div>
</template>
