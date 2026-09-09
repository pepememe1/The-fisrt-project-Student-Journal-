<script setup>
// HeaderBar — компактная полоса ТОЛЬКО для телефона (lg:hidden).
//
// ⚠️ Раньше это была широкая акцентная шапка на всю ширину окна (60 px) на ВСЕХ
// экранах: лого, название колледжа, индикатор связи, статус, тема, роль, ФИО и выход.
// Живой отзыв 3.5.6: «хэдер беспричинно огромный, там нет ничего часто используемого,
// что требовало бы такой заметной полосы». Всё её содержимое переехало туда, где его
// ищут: бренд — в шапку сайдбара, аватар/ФИО/логин/роль/статус/тема — в карточку себя
// внизу сайдбара (SidebarUserPanel.vue, компоновка Discord), выход — в самый низ
// «Настроек». На десктопе полосы не осталось вовсе: вертикаль экрана вернулась контенту.
//
// На телефоне сайдбар скрыт за бургером, поэтому минимальная полоса всё же нужна — но
// именно минимальная: кнопка меню, название текущего раздела (ориентир вместо
// подсвеченного пункта сайдбара) и аватар-ссылка на профиль. 48 px вместо 60.
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Menu } from '@lucide/vue'
import { useAuthStore } from '@/stores/auth'
import { useProfileStore } from '@/stores/profile'
import { useLocaleStore } from '@/stores/locale'
import { profilePlate } from '@/theme/palette'
import Avatar from '@/components/ui/Avatar.vue'
import AccessibilityMenu from '@/components/AccessibilityMenu.vue'

const emit = defineEmits(['toggle-sidebar'])
const auth = useAuthStore()
const profile = useProfileStore()
const locale = useLocaleStore()
const route = useRoute()

// Тот же заголовок, что раньше рисовался крупным блоком над контентом (meta.i18nTitle).
// Фолбэк на бренд обязателен: у части маршрутов meta пустая (например, дашборд студента —
// `path: '', meta: {}` в router/index.js), и полоса осталась бы с одним бургером и
// аватаром по краям — пустая строка посередине читается как недогрузившийся экран.
const title = computed(() => (route.meta?.i18nTitle
  ? locale.t(route.meta.i18nTitle, route.meta.title)
  : (route.meta?.title || '')) || 'GradeBookAI')
const fullName = computed(() => (auth.user?.name || '').trim())
</script>

<template>
  <header
    class="flex shrink-0 items-center gap-2 border-b border-border bg-bg2 px-2 lg:hidden"
    style="height: calc(48px + env(safe-area-inset-top)); padding-top: env(safe-area-inset-top);"
  >
    <!-- На телефоне весь список разделов живёт за этой дверью. Один «гамбургер» хорошо
         узнают разработчики, но для нового преподавателя или родителя он не объясняет,
         где искать журнал, расписание и настройки. Короткая подпись делает действие
         явным и не отнимает отдельную строку у контента. -->
    <button
      class="flex h-9 shrink-0 items-center gap-1 rounded-md px-1.5 text-text2 hover:bg-bg hover:text-accent"
      :aria-label="locale.t('header.menu', 'Меню')" @click="emit('toggle-sidebar')"
    >
      <Menu class="size-5" />
      <span class="text-xs font-semibold">{{ locale.t('header.menu', 'Меню') }}</span>
    </button>

    <p class="min-w-0 flex-1 truncate font-title text-sm font-bold text-text">{{ title }}</p>

    <!-- Версия для слабовидящих — на телефоне полоса всегда видна, здесь ей и место. -->
    <AccessibilityMenu placement="down" class="shrink-0" />

    <RouterLink :to="`/${auth.role}/settings`" class="shrink-0"
                :aria-label="locale.t('nav.profile', 'Профиль')">
      <Avatar :src="profile.avatar" :name="fullName" :role="auth.role" :color="profilePlate(profile.color)" :size="30" />
    </RouterLink>
  </header>
</template>
