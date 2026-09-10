<script setup>
// ZetProgress — переиспользуемый прогресс ЗЕТ (docs/done/PLAN-ZET.md §7.5): дашборд студента,
// отчёт куратора, кабинет родителя. Пусто (total=0) — компонент НЕ рендерит себя вовсе,
// вызывающая сторона должна сама не монтировать его, пока ни один предмет не имеет ЗЕТ
// (см. docs/done/PLAN-ZET.md §10 — «не показывать строку ЗЕТ, если поле NULL»).
import { useLocaleStore } from '@/stores/locale'
const locale = useLocaleStore()
defineProps({
  earned: { type: Number, required: true },
  total: { type: Number, required: true },
  // «Ожидается» — ЗЕТ по предметам, семестр которых ещё идёт (вариант C, docs/done/PLAN-ZET.md §2):
  // не засчитаны, но и не потеряны. Показываем серым отдельно от набранных.
  pending: { type: Number, default: 0 },
  minZet: { type: Number, default: null },
  subjects: { type: Array, default: () => [] },   // [{subject, zet, earned, state, passed}]
  showDetails: { type: Boolean, default: false },
})

// Подпись и цвет строки предмета — по трём состояниям (passed/pending/failed).
const SUBJECT_STATE = {
  passed:  { cls: 'text-accent', key: 'zetProgress.passed',    fallback: '✅ засчитаны' },
  pending: { cls: 'text-text3',  key: 'zetProgress.awaiting',  fallback: '⏳ ожидается' },
  failed:  { cls: 'text-red',    key: 'zetProgress.notPassed', fallback: '❌ не сдан' },
}
// state может отсутствовать у старых ответов (до варианта C) — тогда падаем на passed-флаг.
function stateOf(s) { return s.state || (s.passed ? 'passed' : 'failed') }

// Цвет — по СТАТУСУ (набрано/почти/мало), не категориальный: accent (в этой палитре
// он и есть «хорошо», отдельного зелёного токена в теме нет), yellow — 80–99% от порога,
// red — ниже 80%. Без порога — всегда accent (пока не с чем сравнивать).
// Классы — ПОЛНЫМИ литеральными строками (не шаблонной интерполяцией `bg-${x}`): Tailwind
// JIT видит только буквальные вхождения при сборке, динамически склеенное имя класса
// рискует не попасть в итоговый CSS.
const STATUS_CLASSES = {
  accent: { text: 'text-accent', bg: 'bg-accent' },
  yellow: { text: 'text-yellow', bg: 'bg-yellow' },
  red: { text: 'text-red', bg: 'bg-red' },
}

function status(earned, total, minZet) {
  if (minZet == null || earned >= minZet) return 'accent'
  const pct = minZet > 0 ? (earned / minZet) * 100 : 100
  return pct >= 80 ? 'yellow' : 'red'
}
</script>

<template>
  <div>
    <div class="flex items-baseline justify-between gap-2">
      <span class="text-sm font-medium text-text2">{{ locale.t('zetProgress.title', 'ЗЕТ за семестр') }}</span>
      <span class="font-title text-base font-bold"
            :class="STATUS_CLASSES[status(earned, total, minZet)].text">
        {{ earned }} / {{ total }}
        <!-- «Ожидается» — серым, рядом с набранным: сколько ЗЕТ ещё в идущих предметах. -->
        <span v-if="pending > 0" class="ml-1 text-xs font-medium text-text3"
              :title="locale.t('zetProgress.awaitingHint', 'ЗЕТ по предметам, семестр которых ещё идёт — засчитаются после его завершения')">
          {{ locale.t('zetProgress.awaitingBadge', { n: pending }) }}
        </span>
      </span>
    </div>
    <div class="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-bg2">
      <div class="h-full rounded-full transition-all"
           :class="STATUS_CLASSES[status(earned, total, minZet)].bg"
           :style="{ width: `${total ? Math.min(100, (earned / total) * 100) : 0}%` }" />
    </div>
    <p v-if="minZet != null" class="mt-1 text-xs text-text3">
      <template v-if="earned >= minZet">{{ locale.t('zetProgress.thresholdMet', { min: minZet }) }}</template>
      <template v-else>{{ locale.t('zetProgress.thresholdMissing', { min: minZet, missing: Math.round((minZet - earned) * 10) / 10 }) }}</template>
    </p>

    <ul v-if="showDetails && subjects.length" class="mt-3 space-y-1">
      <li v-for="s in subjects" :key="s.subject" class="flex items-center justify-between text-xs">
        <span class="min-w-0 truncate text-text2" :title="s.subject">{{ s.subject }}</span>
        <span class="shrink-0" :class="SUBJECT_STATE[stateOf(s)].cls">
          {{ locale.t('zetProgress.zetCount', { n: s.zet }) }}
          {{ locale.t(SUBJECT_STATE[stateOf(s)].key, SUBJECT_STATE[stateOf(s)].fallback) }}
        </span>
      </li>
    </ul>
  </div>
</template>
