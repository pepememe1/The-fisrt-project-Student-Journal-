<script setup>
// QuickReplyWheel — выбор быстрого ответа КОЛЕСОМ (просьба Влада, 05.09.2026:
// «быстрый ответ откроет колесо выбора (на подобие колеса выбора активностей), по кругу
// будут варианты фраз а посередине крестик который закроет колесо»).
//
// ━━ ГЕОМЕТРИЯ НЕ ИЗОБРЕТАЕТСЯ ЗАНОВО ━━
// Секторы считает тот же `utils/wheelGeometry.js`, что и колесо активностей, и по той же
// причине: внутри .vue формулы не проверить без браузера, а копия формулы — это две
// геометрии, которые разъедутся на первой же правке. Здесь нужна ровно её часть
// (`sectorPath` + `pt`): картинок в секторах нет, значит нет и вписывания снимка.
//
// ⚠️ ФРАЗ БЫВАЕТ СКОЛЬКО УГОДНО, В ОТЛИЧИЕ ОТ ШЕСТИ АКТИВНОСТЕЙ. У преподавателя к
// пятерым общим добавляются его личные шаблоны (до 20). Поэтому:
//  • угол сектора считается от ЧИСЛА фраз, а не задан константой 60°;
//  • длинную фразу сжимаем `textLength`, а не даём ей вылезти за клин — тот же приём и
//    та же причина, что у подписи в колесе активностей (её угол уходил за край на
//    пропорции 4:3, и увидеть это можно было только числами);
//  • при совсем большом числе фраз колесо не превращается в шестерёнку: сверх
//    `MAX_ON_WHEEL` остаток показывается обычным списком под колесом. Двадцать пять
//    секторов по 14° — это не радиальное меню, а линейка, читать её невозможно.
//
// ⚠️ Крестик В ЦЕНТРЕ, а не в углу — так просили, и это правильно: центр колеса
// равноудалён от всех вариантов, то есть «передумал» стоит ровно столько же, сколько
// любой выбор. В колесе активностей центр занят журналом по той же логике «относится ко
// всем сразу».
import { computed, ref } from 'vue'
import { X } from '@lucide/vue'
import { C, HALF as half, pt, sectorPath } from '@/utils/wheelGeometry'
import { useLocaleStore } from '@/stores/locale'

const props = defineProps({
  // [{ id, text, removable }] — общий набор + личные шаблоны преподавателя.
  items: { type: Array, required: true },
  canManage: { type: Boolean, default: false },
})
const emit = defineEmits(['pick', 'remove', 'add', 'close'])
const locale = useLocaleStore()

const MAX_ON_WHEEL = 12
const R_IN = 0.32
const R_OUT = 0.94
const R_OUT_HOVER = 1.0
const GAP = 1.2                     // просвет между секторами, градусы

const hovered = ref('')
const onWheel = computed(() => props.items.slice(0, MAX_ON_WHEEL))
const rest = computed(() => props.items.slice(MAX_ON_WHEEL))
const step = computed(() => 360 / Math.max(1, onWheel.value.length))

function sector(i) {
  const a0 = i * step.value + GAP / 2
  const a1 = (i + 1) * step.value - GAP / 2
  return { a0, a1, mid: (a0 + a1) / 2 }
}
function pathFor(i, id) {
  const { a0, a1 } = sector(i)
  const rOut = half * (hovered.value === id ? R_OUT_HOVER : R_OUT)
  return sectorPath(a0, a1, half * R_IN, rOut)
}
// Подпись кладём на середину клина по радиусу — там ширина клина максимальна из
// доступного, и текст не прижимается ни к втулке, ни к внешнему краю.
function labelPos(i) {
  const { mid } = sector(i)
  const [x, y] = pt(half * (R_IN + R_OUT) / 2, mid)
  return { x, y }
}
// Ширина клина на радиусе подписи — сюда и обязана поместиться фраза.
const labelWidth = computed(() => {
  const r = half * (R_IN + R_OUT) / 2
  const angle = (step.value - GAP) * Math.PI / 180
  return Math.max(40, 2 * r * Math.sin(angle / 2) * 0.92)
})
</script>

<template>
  <!-- Оверлей ПРОЗРАЧНЫЙ и без карточки — как у колеса активностей: рамка вокруг
       радиального меню возвращает ему вид «окна с настройками», от которого и уходили.
       Но кликабельный: сквозные клики попадали бы по кнопкам под колесом. -->
  <div class="fixed inset-0 z-50 grid place-items-center p-4"
       style="background: var(--gb-overlay)"
       @click.self="emit('close')" @keydown.esc="emit('close')">
    <div class="flex w-full max-w-[420px] flex-col items-center gap-3">
      <svg viewBox="0 0 400 400" class="w-full max-w-[360px] select-none" role="menu"
           :aria-label="locale.t('chatThread.quickReplies', 'Быстрые ответы')">
        <g v-for="(it, i) in onWheel" :key="it.id">
          <path :d="pathFor(i, it.id)"
                :fill="hovered === it.id ? 'var(--gb-accent)' : 'var(--gb-surface-2)'"
                stroke="var(--gb-border)" stroke-width="1"
                class="cursor-pointer transition-[fill]"
                @mouseenter="hovered = it.id" @mouseleave="hovered = ''"
                @click="emit('pick', it)" />
          <text :x="labelPos(i).x" :y="labelPos(i).y"
                text-anchor="middle" dominant-baseline="middle"
                :textLength="Math.min(labelWidth, it.text.length * 7)" lengthAdjust="spacingAndGlyphs"
                :fill="hovered === it.id ? '#fff' : 'var(--gb-text)'"
                font-size="13" class="pointer-events-none">{{ it.text }}</text>
        </g>
        <!-- Втулка: крестик закрывает колесо -->
        <circle :cx="C" :cy="C" :r="half * R_IN - 4" fill="var(--gb-surface)"
                stroke="var(--gb-border)" stroke-width="1"
                class="cursor-pointer" @click="emit('close')" />
        <foreignObject :x="C - 16" :y="C - 16" width="32" height="32" class="pointer-events-none">
          <div class="grid h-8 w-8 place-items-center" style="color: var(--gb-text-dim)">
            <X :size="20" />
          </div>
        </foreignObject>
      </svg>

      <!-- Остаток фраз: сверх дюжины колесо перестаёт быть колесом (см. докстринг). -->
      <div v-if="rest.length" class="flex max-w-full flex-wrap justify-center gap-1.5">
        <button v-for="it in rest" :key="it.id" type="button" @click="emit('pick', it)"
                class="rounded-full border border-border2 bg-card px-2.5 py-1 text-xs text-text hover:bg-bg2">
          {{ it.text }}
        </button>
      </div>

      <!-- Управление личными шаблонами — только у того, кто их заводит.
           ⚠️ Добавление — КНОПКОЙ, а не «закрыл колесо и получил форму»: форма, всплывающая
           после каждого закрытия, читается как ошибка, а не как предложение. -->
      <button v-if="canManage" type="button" @click="emit('add')"
              class="rounded-full border border-border2 bg-card px-3 py-1 text-xs text-text hover:bg-bg2">
        + {{ locale.t('chatThread.addTemplate', 'Добавить шаблон') }}
      </button>
      <div v-if="canManage && items.some(i => i.removable)" class="flex max-w-full flex-wrap justify-center gap-1.5">
        <button v-for="it in items.filter(i => i.removable)" :key="`rm-${it.id}`" type="button"
                @click="emit('remove', it)"
                class="rounded-full border border-border2 bg-card px-2.5 py-1 text-[11px] text-text3 hover:text-red">
          ✕ {{ it.text }}
        </button>
      </div>
    </div>
  </div>
</template>
