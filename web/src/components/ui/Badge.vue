<script setup>
// Badge — пилюля-бейдж (порт QLabel#badge_* из styles.py).
import { computed } from 'vue'
const props = defineProps({ variant: { type: String, default: 'green' } }) // green | blue | red | muted
const VARIANTS = {
  green: 'bg-accent-glow text-accent border-accent',
  blue: 'bg-blue/10 text-blue border-blue',
  red: 'bg-red/10 text-red border-red',
  muted: 'bg-bg2 text-text2 border-border2',
}
const cls = computed(
  //🔥 `whitespace-nowrap` ОБЯЗАТЕЛЕН (06.09.2026, жалоба Влада: «в расписании 1 п/г из-за
  //небольшого места все символы становятся столбцом»). Бейдж — короткий ярлык, и перенос
  //внутри него не «ужимает», а разваливает: «1 п/г» в узкой колонке дня превращался в
  //столбик из четырёх символов. Пусть лучше вылезет за край — это видно и чинится, чем
  //молча станет нечитаемым.
  () => `inline-flex shrink-0 items-center whitespace-nowrap rounded-full border px-3 py-1 text-tiny font-medium ${VARIANTS[props.variant] || VARIANTS.green}`,
)
</script>

<template>
  <span :class="cls"><slot /></span>
</template>
