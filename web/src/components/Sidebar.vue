<script setup>
import { useLocaleStore } from '@/stores/locale'
// Sidebar — боковая навигация. 250px, фон bg2, секции-заголовки (uppercase) + пункты с
// иконками; активный подсвечен акцентом.
//
// С 3.5.6 сайдбар несёт ТРИ этажа, а не один список (живой отзыв: «сайдбар пустой, а
// хэдер жирный и бесполезный»):
//   1) шапка — фирменный знак и название (переехали из акцентной полосы во всю ширину);
//   2) сам список разделов — растягивается, прокручивается только он;
//   3) карточка себя (SidebarUserPanel) — аватар/ФИО/логин/роль/статус, компоновка Discord.
// Из-за этого корневой aside стал flex-колонкой с overflow-y ТОЛЬКО на середине: иначе
// карточка себя уезжала бы вверх вместе с прокруткой длинного админского меню.
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useMessengerStore } from '@/stores/messenger'
import { NAV } from '@/config/nav'
import { curatorApi, adminApi, meApi } from '@/api/endpoints'
import BrandLogo from '@/components/BrandLogo.vue'
import SidebarUserPanel from '@/components/SidebarUserPanel.vue'
import ConnectionBadge from '@/components/ui/ConnectionBadge.vue'
import SyncIssuesBadge from '@/components/ui/SyncIssuesBadge.vue'
import RejectedWritesBadge from '@/components/ui/RejectedWritesBadge.vue'
import AccessibilityMenu from '@/components/AccessibilityMenu.vue'
import ReportProblemButton from '@/components/ReportProblemButton.vue'
import SidebarResizer from '@/components/SidebarResizer.vue'
import { useSidebarStore } from '@/stores/sidebar'

const props = defineProps({
  open: { type: Boolean, default: false },
  // Куда человек нажал, пока страница ещё грузится. Нужен, чтобы пункт подсветился ПОД
  // ПАЛЬЦЕМ сразу, а не через круг до сервера: `route.path` меняется только после того,
  // как маршрут разрешён (а страницы у нас ленивые — это ещё и загрузка чанка).
  pending: { type: String, default: '' },
})
const openProp = computed(() => props.open)
const emit = defineEmits(['navigate'])

const auth = useAuthStore()
const loc = useLocaleStore()
const messenger = useMessengerStore()
const route = useRoute()
// ━━ ШИРИНА ━━ Тянется мышью за правый край и запоминается на устройстве (см. стор).
// ⚠️ В ВЫЕЗЖАЮЩЕЙ шторке (телефон) ширина НЕ применяется: там сайдбар и так во весь
// экран по высоте и занимает почти всю ширину, а свёрнутый до иконок он превратился бы
// в бесполезную полоску поверх контента, которую нечем закрыть.
const sidebar = useSidebarStore()
const drawer = computed(() => !!openProp.value)
// Значение бейджа пункта: непрочитанные сообщения берём из стора мессенджера (живой
// счётчик), остальные — из локальной карты badges (напр., накладки расписания).
function badgeCount(key) {
  if (key === 'messagesUnread') return messenger.totalUnread
  return badges.value[key] || 0
}
// Пункт «Курирование» (curatorOnly) виден только преподавателю-куратору.
const isCurator = ref(false)
// Счётчики у пунктов меню (nav.badge → число): накладки расписания и непрочитанные
// уведомления.
const badges = ref({})
let notifyTimer = null
let onNotifyVisible = null
/**
 * Непрочитанные уведомления для значка у пункта «Уведомления».
 * 🔎 Ручка `GET /me/events/unread-count` существовала и была объявлена в контракте, но
 * её не звал НИКТО — наш обычный класс дефекта («обещание без вызывающего»). Раз лента
 * стала отдельным разделом, значок нужен: иначе о новом письме узнают, только зайдя.
 */
async function loadNotifyUnread() {
  try { badges.value.notifyUnread = (await meApi.unreadCount()).data?.count || 0 }
  catch { /* нет связи — значок просто не обновится, ломать меню незачем */ }
}
onMounted(async () => {
  if (auth.role === 'teacher') {
    try { isCurator.value = ((await curatorApi.groups()).data.groups || []).length > 0 } catch { /* */ }
  }
  if (auth.role === 'admin') {
    // Ошибку глушим намеренно: недоступная проверка расписания не повод ломать меню.
    try { badges.value.scheduleIssues = (await adminApi.scheduleConflicts()).data.count || 0 } catch { /* */ }
  }
  loadNotifyUnread()
  // ⚠️ Тикает РЕДКО (минута): значок «есть непрочитанные» — не чат, задержка в минуту
  // ничего не стоит, а частый опрос на одноядерном бою стоит. Тот же расчёт, что у
  // счётчика сообщений (20 с) — там письмо ждут сразу, здесь нет.
  //⚠️ И молчим, пока экрана не видно: в свёрнутом приложении таймер WebView продолжает
  //тикать, и мы будили бы радиомодуль раз в минуту при погашенном экране — ради значка,
  //которого человек в этот момент не видит. Вернулся — обновляем сразу.
  notifyTimer = setInterval(() => { if (!document.hidden) loadNotifyUnread() }, 60000)
  onNotifyVisible = () => { if (!document.hidden) loadNotifyUnread() }
  document.addEventListener('visibilitychange', onNotifyVisible)
})
onBeforeUnmount(() => {
  clearInterval(notifyTimer)
  if (onNotifyVisible) document.removeEventListener('visibilitychange', onNotifyVisible)
})
// Свёрнут ли до иконок. В шторке — никогда: там ширина фиксированная.
const compact = computed(() => !drawer.value && sidebar.compact)
const items = computed(() => (NAV[auth.role] || []).filter((it) => !it.curatorOnly || isCurator.value))

function isActive(to) {
  if (to.split('/').length <= 2) return route.path === to
  return route.path === to || route.path.startsWith(to + '/')
}

/**
 * Подсвечен ли пункт: он открыт ЛИБО его прямо сейчас открывают.
 *
 * ⚠️ Второе слагаемое — не украшение. Пункт, нажатый на телефоне, до этой правки не
 * менялся вообще ничем, пока не догрузится страница: палец уже убрали, а меню выглядит
 * так, будто нажатие не засчиталось. Отсюда повторные нажатия по соседним пунктам.
 */
function highlighted(to) {
  return isActive(to) || props.pending === to
}
</script>

<template>
  <!-- ⚠️ Безопасные зоны (3.7, живой отзыв Ярослава с APK: «при заходе в бургер-меню
       надпись GradeBookAI залазит под шторку уведомлений»). Выезжающий сайдбар —
       `fixed inset-y-0` (AppShell), то есть начинается от координаты 0, а в приложении
       это ПОД системной шторкой: бренд оказывался наполовину перекрыт. Мобильная полоса
       свой отступ уже учитывает (`HeaderBar.vue`), а сайдбар — нет, потому что он не
       часть той же колонки. Заодно нижняя вставка спасает карточку профиля внизу от
       полосы жестов. На сайте и в десктопе обе вставки нулевые — поведение прежнее. -->
  <aside class="relative flex h-full shrink-0 flex-col border-r border-border bg-bg2"
         :class="[drawer ? 'w-[250px]' : '', sidebar.dragging ? '' : 'transition-[width] duration-150']"
         :style="{ ...(drawer ? {} : { width: sidebar.width + 'px' }),
                   paddingTop: 'var(--gb-safe-top)', paddingBottom: 'var(--gb-safe-bottom)' }">
    <!-- Полоску не показываем в шторке: там ширину менять нечем и незачем. -->
    <SidebarResizer v-if="!drawer" />
    <!-- 1. Шапка: фирменный знак и название. Раньше стояли в акцентной полосе во всю
         ширину окна — здесь занимают ту же строку, но не отнимают высоту у контента. -->
    <div class="flex shrink-0 items-center gap-2.5 px-3 py-3"
         :class="compact ? 'flex-col gap-2' : ''">
      <BrandLogo :size="28" class="shrink-0" />
      <div v-if="!compact" class="min-w-0 flex-1 leading-tight">
        <!-- 13px + отрицательный трекинг (apple-design §15): «GradeBookAI» в Syne
             extrabold шире места между лого и меню-очками (замерено: натуральные 147px
             против доступных ~139px при 15px) и обрезался в «GradeBoo…». Имя продукта
             резать нельзя — 13px влезает с запасом; подпись колледжа под ним может. -->
        <p class="truncate font-title text-[13px] font-extrabold tracking-tight text-text">GradeBookAI</p>
        <p class="truncate text-[10px] font-semibold text-text3">{{ loc.t('app.college') }}</p>
      </div>
      <!-- Версия для слабовидящих — всегда на виду в шапке (как иконка-очки на порталах). -->
      <AccessibilityMenu placement="down" class="shrink-0" />
    </div>
    <div class="mx-3 h-px shrink-0 bg-border" />

    <!-- 2. Разделы — единственная прокручиваемая часть. -->
    <nav class="min-h-0 flex-1 overflow-y-auto px-2.5 py-2">
      <template v-for="(item, i) in items" :key="i">
        <!-- Заголовок раздела в свёрнутом виде превращается в черту: слово туда не
             влезает, а совсем убрать группировку значит слить меню в один список. -->
        <p v-if="item.section && !compact"
           class="px-2.5 pb-1 pt-3.5 text-[10px] font-medium uppercase tracking-wide text-text2 first:pt-1">
          {{ item.i18n ? loc.t(item.i18n, item.section) : item.section }}
        </p>
        <div v-else-if="item.section" class="mx-2 my-2 h-px bg-border first:mt-0"></div>
        <RouterLink
          v-else
          :to="item.to"
          :title="compact ? (item.i18n ? loc.t(item.i18n, item.label) : item.label) : null"
          class="relative mb-0.5 flex rounded-sm transition-colors"
          :class="[
            compact ? 'flex-col items-center gap-1 px-1 py-2' : 'items-center gap-2.5 px-3.5 py-2.5 text-sm',
            // `phoneOnly` — пункт только для узкого экрана (см. nav.js). Сейчас так
            // помечены «Настройки»: на ПК их дверь — шестерёнка в карточке себя внизу
            // слева, и дублировать её пунктом меню значило бы показать два входа в одно
            // место. Прячем КЛАССОМ, а не `v-if`: разбор ширины в JS завёл бы вторую
            // границу рядом с `LG_PX` оболочки (см. web/tests/breakpoint.test.mjs).
            item.phoneOnly ? 'lg:hidden' : '',
            highlighted(item.to)
              ? 'bg-accent-glow font-semibold text-accent'
              : 'font-medium text-text3 hover:bg-accent-glow hover:text-accent active:bg-accent-glow active:text-accent',
          ]"
          @click="emit('navigate', item.to)"
        >
          <component :is="item.icon" class="size-[18px] shrink-0" />
          <!-- ⚠️ В СВЁРНУТОМ виде подпись ПОВЁРНУТА, а не обрезана. Обрезка до «Расп…»
               не экономит место и не сообщает ничего; поворот на 90° сохраняет слово
               целиком, а колонка остаётся шириной в иконку. Показывается ТОЛЬКО на
               минимуме — на промежуточной ширине подпись обычная, горизонтальная. -->
          <span v-if="compact" class="gb-vlabel">
            {{ item.i18n ? loc.t(item.i18n, item.label) : item.label }}
          </span>
          <span v-else class="truncate">{{ item.i18n ? loc.t(item.i18n, item.label) : item.label }}</span>
          <!-- Счётчик у пункта. Непрочитанные сообщения — акцентом (информация), накладки
               расписания — красным (требует вмешательства). Ноль не показываем.
               В свёрнутом виде — точкой в углу: число там нечитаемо, а факт «есть новое»
               важнее его количества. -->
          <span v-if="item.badge && badgeCount(item.badge)"
                class="grid shrink-0 place-items-center rounded-full font-bold text-white"
                :class="[
                  item.badge === 'messagesUnread' ? 'bg-accent' : 'bg-red',
                  compact ? 'absolute right-1 top-1 size-2 p-0' : 'ml-auto min-w-5 px-1.5 text-tiny',
                ]">
            <template v-if="!compact">{{ badgeCount(item.badge) }}</template>
          </span>
        </RouterLink>
      </template>
    </nav>

    <!-- Режим связи — НАД карточкой себя: это состояние всего приложения, а не свойство
         аккаунта. В приложении виден всегда, на сайте появляется только при потере сети
         (см. сам компонент о том, почему постоянное «Онлайн» в браузере — шум). -->
    <div class="shrink-0 px-3 pb-1">
      <ConnectionBadge v-if="!compact" />
    </div>

    <!-- Рядом, но ОТДЕЛЬНО от значка связи: тот про «есть ли сеть», этот про «дошли ли
         правки». Состояния не совпадают — связь бывает прекрасной, а оценка всё равно
         остаётся только на этом ПК. Появляется, лишь когда есть о чём сказать. -->
    <div class="shrink-0 px-3 pb-1">
      <SyncIssuesBadge v-if="!compact" />
    </div>

    <!-- 🔥 Отказы сервера — ОТДЕЛЬНО от соседа сверху и по ДРУГИМ правилам.
         Тот про синхронизацию ПРОГРАММЫ (маршрута нет ни на сайте, ни в мобилке), этот —
         про очередь офлайн-правок, которая живёт на всех трёх платформах: именно на
         телефоне преподаватель и ставит оценки без сети.
         ⚠️ БЕЗ `v-if="!compact"`. Свёрнутая панель не имеет права спрятать сообщение о
         потерянной работе — а именно это и случилось с соседним значком (см. историю
         desktopSync.js). Узкий столбец переживёт: содержимое переносится по словам. -->
    <div class="shrink-0 px-3 pb-1">
      <RejectedWritesBadge />
    </div>

    <!-- «Сообщить о проблеме» — встроенный канал обратной связи (ClassDojo, §9 №3).
         Над карточкой себя, спокойным стилем: действие редкое, но должно быть под рукой
         из любого раздела. Админу компонент себя не рисует (он и есть модерация). -->
    <div class="shrink-0 px-2.5 pb-1">
      <ReportProblemButton />
    </div>

    <!-- 3. Карточка себя — прижата к нижнему краю (Discord). Занимает ровно тот угол,
         который в прежней раскладке пустовал. -->
    <SidebarUserPanel />
  </aside>
</template>

<style scoped>
/* Вертикальная подпись свёрнутого пункта. `writing-mode` вместо `rotate` намеренно:
   поворот трансформацией не отдаёт высоту потоку, и соседние пункты налезали бы друг
   на друга. Здесь браузер сам считает высоту строки как высоту блока. */
.gb-vlabel {
  writing-mode: vertical-rl;
  text-orientation: mixed;
  max-height: 92px;
  overflow: hidden;
  font-size: 10px;
  line-height: 1;
  letter-spacing: .02em;
}
</style>
