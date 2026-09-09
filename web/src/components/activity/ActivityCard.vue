<script setup>
// ActivityCard — карточка-кнопка активности в ленте (PLAN-ACTIVITIES §10).
//
// Механизм готовый: ровно так уже устроена кнопка «Отчёт №N» куратора — в теле сообщения
// лежит только id, а объект подмешивает сервер (`_attach_rich_meta`). Статус активности
// меняется ПОСЛЕ отправки (идёт → завершена), поэтому кнопка гаснет по `status`, а не по
// содержимому сообщения: переотправлять его ради этого незачем.
import { computed } from 'vue'
import { useSharedNow } from '@/utils/sharedClock'
import { Presentation, ListChecks, Trophy, BarChart3, Gauge, Timer } from '@lucide/vue'
import { useLocaleStore } from '@/stores/locale'
import { useActivityStore } from '@/stores/activity'

const props = defineProps({
  activity: { type: Object, required: true },
  createdAt: { type: String, default: '' },
})
const locale = useLocaleStore()
const act = useActivityStore()

const ICONS = {
  board: Presentation, quiz: ListChecks, contest: Trophy,
  poll: BarChart3, pulse: Gauge, timer: Timer,
}
const icon = computed(() => ICONS[props.activity.kind] || ListChecks)
const running = computed(() => props.activity.status === 'running')
const kindLabel = computed(() =>
  locale.t(`activity.kind.${props.activity.kind}`, props.activity.kind))

// Таймер «идёт N мин» — от начала активности. Тикаем локально: слать секунды по сокету
// всем участникам ради подписи на кнопке — самая дорогая реализация самой дешёвой вещи.
//
// 🔑 Часы ОБЩИЕ и идут ТОЛЬКО у идущей активности (B7 разбора 07.09.2026). Прежде здесь
// стоял свой `setInterval` БЕЗУСЛОВНО — то есть завершённая активность, у которой время
// уже никогда не изменится, до конца сеанса будила реактивность раз в секунду. В ленте
// таких карточек столько, сколько их было в беседе за семестр.
const now = useSharedNow(running)

const elapsed = computed(() => {
  const started = Date.parse(props.activity.started_at || '')
  if (!Number.isFinite(started)) return ''
  const end = running.value ? now.value : Date.parse(props.activity.finished_at || '') || now.value
  const secs = Math.max(0, Math.floor((end - started) / 1000))
  const mm = String(Math.floor(secs / 60)).padStart(2, '0')
  const ss = String(secs % 60).padStart(2, '0')
  return `${mm}:${ss}`
})
</script>

<template>
  <!-- Активность стоит ПО ЦЕНТРУ ленты разделителем, как метка даты, а не пузырём
       сообщения слева. Причина не косметическая: активность — не чья-то реплика, а
       событие всей беседы, и пузырь от лица преподавателя читался как его сообщение,
       которое можно процитировать, переслать или удалить. Разделитель этого не обещает.
       Кликабелен целиком, пока активность идёт. -->
  <div class="my-3 flex items-center gap-3">
    <span class="h-px min-w-4 flex-1 bg-border2" />
    <button type="button" :disabled="!running" @click="act.open(activity.id)"
            class="flex max-w-[80%] min-w-0 items-center gap-2 rounded-full border px-4 py-1.5 text-center text-sm transition-colors"
            :class="running
              ? 'border-accent/40 bg-accent/10 font-semibold text-accent hover:bg-accent/20'
              : 'cursor-default border-border2 bg-bg2 text-text3'">
      <component :is="icon" class="size-4 shrink-0" />
      <span class="min-w-0 truncate">
        {{ activity.title || kindLabel }}
        <span v-if="activity.title" class="font-medium opacity-70">· {{ kindLabel }}</span>
      </span>
      <span class="shrink-0 text-[11px] font-medium opacity-70">
        {{ running ? locale.t('activity.card.running', 'идёт') : locale.t('activity.card.finished', 'завершена') }}
        · {{ elapsed }}
      </span>
    </button>
    <span class="h-px min-w-4 flex-1 bg-border2" />
  </div>
</template>
