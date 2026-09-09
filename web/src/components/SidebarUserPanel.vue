<script setup>
// SidebarUserPanel — «карточка себя» в САМОМ НИЗУ сайдбара (компоновка Discord).
//
// Зачем появилась (живой отзыв 3.5.6): раньше всё это жило в широкой акцентной шапке
// на всю ширину окна — 60 px по вертикали ради данных, на которые смотрят раз в день
// (кто я, какая роль, статус). Шапка ушла, её содержимое переехало сюда: левый нижний
// угол в прежней раскладке не использовался вовсе, а здесь блок стоит ровно там, где
// его ищут по привычке из Discord/Slack/Teams.
//
// Что показываем: аватар (prefs.avatar, тот же источник, что в профиле и мессенджере),
// ФИО, логин, роль и СВОЙ статус (§D7 — меняется одним кликом с любой страницы).
// Кнопки выхода здесь НЕТ намеренно: она переехала в самый низ «Настроек» (отзыв
// «случайно жму выход») — выход это редкое и необратимое действие, ему не место рядом
// с постоянно нажимаемым меню.
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { RouterLink } from 'vue-router'
import { Moon, Sun, ChevronDown, SlidersHorizontal } from '@lucide/vue'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useProfileStore } from '@/stores/profile'
import { useMessengerStore } from '@/stores/messenger'
import { useLocaleStore } from '@/stores/locale'
import { STATUS_KINDS, myStatusLabel } from '@/config/status'
import { roleLabel as roleLabelOf } from '@/config/roles'
import { nameDecor } from '@/config/nameEffects'
import { profilePlate } from '@/theme/palette'
import Avatar from '@/components/ui/Avatar.vue'
import AvatarEggs from '@/components/easter/AvatarEggs.vue'
import { useEasterStore } from '@/stores/easterEggs'
import { studentApi } from '@/api/endpoints'
import SidebarUserOverlay from '@/components/SidebarUserOverlay.vue'

const auth = useAuthStore()
const theme = useThemeStore()
const profile = useProfileStore()
const messenger = useMessengerStore()
const locale = useLocaleStore()

const roleLabel = computed(() => roleLabelOf(auth.role))
const fullName = computed(() => (auth.user?.name || '').trim() || roleLabel.value)
const login = computed(() => (auth.user?.login || '').trim())
// Своё имя рисуется тем же стилем, что видят другие: шрифт, эффект и цвет разом.
const myNameDecor = computed(() => nameDecor({
  name_font: profile.font, name_effect: profile.effect,
  name_color: profile.nameColor, profile_color: profile.color,
}))

// Статус — тот же общий словарь (config/status.js), что у мессенджера: второй копии нет.
// Сам ВЫБОР статуса живёт в раскрывающейся карточке (SidebarUserOverlay) — здесь нужна
// только точка нужного цвета рядом с аватаром.
const cardOpen = ref(false)
const myStatus = computed(() =>
  STATUS_KINDS.find(k => k.kind === messenger.myStatus.kind) || STATUS_KINDS[0])
// Пасхалки на аватарке (Detroit и DOOM). Средний балл нужен обеим: у первой он задаёт
// цвет кольца, у второй — состояние. Спрашиваем ОДИН раз и только у студента: другим
// ролям пасхалки не показываются вовсе, а «своего среднего» у них и не существует.
const easter = useEasterStore()
const ledActive = computed(() => !!easter.inPage.detroit_led)
const avgGrade = ref(0)
// ⚠️ Средний спрашиваем ЛЕНИВО — только когда пасхалка реально включилась, и ровно один
// раз. Безусловный запрос на монтировании стоил бы лишнего похода на сервер при КАЖДОМ
// открытии программы ради того, что нужно одной картинке в углу.
let avgAsked = false
watch(() => easter.inPage.detroit_led || easter.inPage.doom_avatar, async (on) => {
  if (!on || avgAsked || auth.role !== 'student') return
  avgAsked = true
  try {
    const { data } = await studentApi.stats()
    avgGrade.value = Number(data?.average || 0)
  } catch { /* нет сети — пасхалка просто покажет нейтральное состояние */ }
}, { immediate: true })

const statusText = computed(() =>
  myStatusLabel(messenger.myStatus.kind, messenger.myStatus.custom_text))

// Связь показываем, ТОЛЬКО когда её нет (то же решение, что было в шапке): «онлайн»
// рядом с выбираемым статусом читалось как один составной статус, и «Не беспокоить»
// уживалось с бодрым «Онлайн».
const online = ref(navigator.onLine)
function updateOnline() { online.value = navigator.onLine }

// 🔥 ОБРАБОТЧИК, КОТОРЫЙ НЕ СНИМАЮТ, НАКАПЛИВАЕТСЯ (07.09.2026).
//
// Панель монтируется в сайдбаре, а на телефоне сайдбар — выезжающая шторка: каждое её
// открытие создаёт новый экземпляр, каждое закрытие его уничтожает. `addEventListener`
// был, парного `removeEventListener` не было вовсе — то есть за день работы на телефоне
// на `online`/`offline` копилось столько обработчиков, сколько раз человек открыл меню,
// и каждый скачок связи будил их все разом.
// ⚠️ Отдельно от этого убран сам ИСТОЧНИК дубля (см. AppShell.vue): панель существовала
// в двух экземплярах одновременно — скрытая CSS-классом десктопная и живая мобильная.
onBeforeUnmount(() => {
  window.removeEventListener('online', updateOnline)
  window.removeEventListener('offline', updateOnline)
})

onMounted(async () => {
  window.addEventListener('online', updateOnline)
  window.addEventListener('offline', updateOnline)
  profile.load()
  await messenger.loadMyStatus()
  //Авто-«отошёл» по бездействию (как в Discord). Запускаем ЗДЕСЬ, а не на странице
  //мессенджера: панель видна на всех страницах, а отлучиться можно и из журнала —
  //привяжи слежение к одной вкладке, и статус завис бы на «в сети» у всех остальных.
  messenger.startIdleWatch()
})
</script>

<template>
  <div class="relative shrink-0 border-t border-border px-2 py-2">
    <!-- Карточка себя раскрывается ВВЕРХ: панель прижата к нижнему краю окна. -->
    <SidebarUserOverlay v-if="cardOpen" @close="cardOpen = false" />
    <!-- Клик мимо карточки закрывает её (глобальной директивы click-outside в проекте нет). -->
    <div v-if="cardOpen" class="fixed inset-0 z-30" @click="cardOpen = false" />

    <div class="relative z-10 flex items-center gap-2 rounded-md px-1.5 py-1.5 transition-colors hover:bg-bg">
      <!-- ⚠️ В самой панели — ТОЛЬКО самое частое: аватар, имя, логин и точка статуса.
           Роль и статус словами сюда не помещались (склеивались в «Студент · Не
           беспокоить» и обрезались) — они переехали в карточку по клику. -->
      <button type="button" @click="cardOpen = !cardOpen"
              class="flex min-w-0 flex-1 items-center gap-2 text-left"
              :aria-label="locale.t('userOverlay.open', 'Профиль и статус')"
              :title="locale.t('header.myStatus', { status: statusText })">
        <span class="relative shrink-0">
          <!-- ⚠️ Аватарка поднята НАД эффектами пасхалок (z-10). Свечение DOOM и лучи
               рисуются позже по разметке и без этого ложились ПОВЕРХ лица — человек
               видел мутное пятно вместо себя.
               ⚠️ Поднимаем аватарку, а НЕ утапливаем эффекты отрицательным z-index:
               отрицательный увёл бы их за непрозрачный фон сайдбара, и лучей не стало
               бы видно вовсе. На этом уже спотыкались (см. докстринг AvatarEggs). -->
          <span class="relative z-10 block">
            <Avatar :src="profile.avatar" :name="fullName" :role="auth.role" :color="profilePlate(profile.color)" :size="36" />
          </span>
          <!-- Кружок статуса прячем, когда выпало кольцо Detroit: оно встаёт ВМЕСТО
               него, а не рядом — два индикатора в одном углу читались бы как поломка. -->
          <span v-if="!ledActive" class="absolute bottom-0 right-0 z-20 size-3 rounded-full border-2 border-card"
                :style="{ background: myStatus.color }" />
          <AvatarEggs :average="avgGrade" />
        </span>
        <span class="min-w-0 flex-1">
          <!-- Полное ФИО не обрезаем в «…»: длинное «Фамилия Имя Отчество» переносим в
               2 строки (line-clamp-2). tracking-tight — по apple-design §15 (крупный
               текст хочет отрицательный трекинг) и заодно даёт лишний символ в строку. -->
          <span class="block text-[13px] font-semibold leading-tight tracking-tight text-text [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] overflow-hidden"
                :title="fullName" v-bind="myNameDecor">{{ fullName }}</span>
          <span v-if="login" class="block truncate text-[11px] leading-tight text-text3">@{{ login }}</span>
        </span>
        <ChevronDown class="size-3.5 shrink-0 text-text3 transition-transform"
                     :class="cardOpen ? 'rotate-180' : ''" />
      </button>

      <button type="button" @click="theme.toggleMode()"
              class="grid size-8 shrink-0 place-items-center rounded-md text-text3 transition-colors hover:bg-bg2 hover:text-accent"
              :aria-label="theme.isDark ? locale.t('header.lightTheme', 'Светлая тема') : locale.t('header.darkTheme', 'Тёмная тема')">
        <Sun v-if="theme.isDark" class="size-4" />
        <Moon v-else class="size-4" />
      </button>

      <!-- Шестерёнка настроек — рядом с карточкой себя, в нижнем углу (Discord,
           просьба Влада 31.08.2026). Раньше это был пункт меню наравне с разделами
           кабинета, хотя настройки к учебным разделам не относятся вовсе.
           ⚠️ Видна и на телефоне: сама панель там тоже прижата к низу выезжающей шторки,
           то есть «нижний угол» существует на обеих платформах. Первая редакция прятала
           её под `lg:grid` из предположения, что на телефоне такого угла нет, — Влад
           открыл шторку и показал, что есть. Предположение о чужом экране стоило
           лишнего пункта меню.
           ⚠️ Пункт «Настройки» из меню при этом УБРАН: две двери в одно место, видимые
           одновременно, читаются как два разных места. -->
      <RouterLink :to="`/${auth.role || 'student'}/settings`"
                  class="grid size-8 shrink-0 place-items-center rounded-md text-text3 transition-colors hover:bg-bg2 hover:text-accent"
                  :aria-label="locale.t('nav.settings', 'Настройки')"
                  :title="locale.t('nav.settings', 'Настройки')">
        <SlidersHorizontal class="size-4" />
      </RouterLink>
    </div>

    <!-- Нет связи — единственное состояние сети, о котором стоит сообщать. -->
    <p v-if="!online" class="mt-1 px-1.5 text-[11px] font-semibold text-red"
       :title="locale.t('header.offlineHint', 'Нет связи с сетью — данные подтянутся, когда связь вернётся.')">
      ● {{ locale.t('header.offline', 'Офлайн') }}
    </p>
  </div>
</template>
