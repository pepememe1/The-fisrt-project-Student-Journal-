<script setup>
// MessageActionsOverlay — контекстное меню действий над сообщением (как в Telegram).
// Появляется по тапу на сообщение рядом с ним. Набор кнопок зависит от прав
// (своё/чужое, закреплено ли, удалено ли) — см. MESSENGER-PLAN.md §6.8. Эмитит выбранное
// действие наверх (ChatThread выполняет), сам ничего не делает с данными.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Reply, Pin, PinOff, Copy, Forward, Trash2, ListChecks, Flag, AlarmClock, Languages, Volume2, SmilePlus } from '@lucide/vue'
import { useLocaleStore } from '@/stores/locale'

const locale = useLocaleStore()

const props = defineProps({
  message: { type: Object, required: true },
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
  // Уже показан ли перевод ЭТОГО сообщения (ChatThread — единственный, кто знает
  // translate-стор) — только чтобы подписать пункт «Перевести»/«Скрыть перевод»,
  // сам оверлей переводом не занимается (см. докстринг файла).
  translated: { type: Boolean, default: false },
})
const emit = defineEmits(['pick', 'react', 'close'])

// §D3: реакции-эмодзи — тот же белый список, что на сервере (MessageReaction.emoji).
const REACTIONS = ['👍', '✅', '❤️', '😂', '👀', '🔥', '💯', '❓', '📌']

const m = computed(() => props.message)

/**
 * 🔥 ВЫСОТУ МЕНЮ МЕРЯЕМ, А НЕ ПРЕДПОЛАГАЕМ (07.09.2026).
 *
 * Здесь стояло `Math.min(y, innerHeight - 360)` — то есть высота считалась постоянной и
 * равной 360 px. Настоящая зависит от набора пунктов (их от 3 до 12, плюс две строки
 * реакций) и на обычном телефоне 360×640 доходит до 405: нижний пункт «Удалить» уезжал
 * за край экрана, и добраться до него было НЕЧЕМ — прокрутки у меню тоже не было.
 * Замер воспроизведения: верх 280, низ 685 при экране 640.
 *
 * Хуже того, `Math.min` без нижней границы: на низком экране (клавиатура открыта,
 * ландшафт) `innerHeight - 360` уходит в минус, меню вылезало ВВЕРХ за край, и вместе с
 * ним пропадала строка реакций.
 *
 * ⚠️ Мерить надо ПОСЛЕ отрисовки — до неё высоты не существует. Поэтому положение живёт
 * в ref и уточняется в `onMounted`/`nextTick`, а не вычисляется одним `computed`. Первый
 * кадр рисуется по осторожной оценке, чтобы меню не мигало из угла.
 */
const HALF = 240                    // ширина меню (w-56 = 224) + запас
const GUESS_H = 380                 // оценка до первого замера
const EDGE = 8                      // безопасный отступ от краёв экрана

const box = ref(null)
const pos = ref({ top: 0, left: 0 })
const maxH = ref(0)

function viewport() {
  if (typeof window === 'undefined') return { w: 1200, h: 800 }
  //visualViewport учитывает ЭКРАННУЮ КЛАВИАТУРУ: без него на телефоне меню считает
  //доступной высоту, половину которой занимает клавиатура.
  const vv = window.visualViewport
  return { w: vv?.width || window.innerWidth, h: vv?.height || window.innerHeight }
}

function place() {
  const { w, h } = viewport()
  const own = box.value?.offsetHeight || GUESS_H
  //Меню выше экрана целиком — отдаём ему всё, что есть, и включаем прокрутку внутри.
  const avail = h - EDGE * 2
  maxH.value = own > avail ? avail : 0
  const height = Math.min(own, avail)
  //Клампим С ОБЕИХ сторон: верхняя граница нужна не меньше нижней (см. докстринг).
  pos.value = {
    top: Math.max(EDGE, Math.min(props.y, h - height - EDGE)),
    left: Math.max(EDGE, Math.min(props.x, w - HALF - EDGE)),
  }
}

const style = computed(() => ({
  top: `${pos.value.top}px`,
  left: `${pos.value.left}px`,
  //0 означает «влезает целиком» — тогда ограничение не навязываем вовсе.
  ...(maxH.value ? { maxHeight: `${maxH.value}px`, overflowY: 'auto' } : {}),
}))

onMounted(async () => {
  place()
  await nextTick()
  place()                            // теперь высота настоящая
  if (typeof window !== 'undefined') {
    window.visualViewport?.addEventListener('resize', place)
    window.addEventListener('resize', place)
  }
})

onBeforeUnmount(() => {
  if (typeof window === 'undefined') return
  window.visualViewport?.removeEventListener('resize', place)
  window.removeEventListener('resize', place)
})

//Набор пунктов зависит от прав и от того, показан ли перевод: изменился — высота другая.
watch(() => [props.message?.id, props.translated], async () => {
  await nextTick()
  place()
})

// Список действий по правам (Фаза 3 — личные чаты).
const items = computed(() => {
  const d = m.value.deleted
  const list = []
  if (!d) list.push({ key: 'reply', label: locale.t('msgAction.reply', 'Ответить'), icon: Reply })
  if (!d) list.push(m.value.pinned
    ? { key: 'unpin', label: locale.t('messenger.unpin', 'Открепить'), icon: PinOff }
    : { key: 'pin', label: locale.t('messenger.pin', 'Закрепить'), icon: Pin })
  if (!d) list.push({ key: 'copy', label: locale.t('msgAction.copy', 'Копировать текст'), icon: Copy })
  // Зачитать вслух — той же говорилкой, что у Вектора (§5.3). Только там, где есть что
  // читать: у GIF/пустого текста после slash-команд озвучивать нечего.
  if (!d && m.value.body) list.push({ key: 'speak', label: locale.t('msgAction.speak', 'Зачитать сообщение'), icon: Volume2 })
  // «Реакции» (по аналогии с Message Info в Telegram) — только СВОИ сообщения: кто
  // поставил реакцию и кто просмотрел, с временем. У чужого сообщения этот список не
  // наш секрет — и технически, и по смыслу («кто прочитал» тут же снизу под своим же
  // сообщением, как обычно).
  if (!d && m.value.mine) list.push({ key: 'reactions-info', label: locale.t('msgAction.reactionsInfo', 'Реакции'), icon: SmilePlus })
  //Перевод — только ЧУЖИХ сообщений (своё и так на языке, на котором написано);
  //тот же переключатель, что у ссылки «перевести» под самим сообщением.
  if (!d && !m.value.mine && m.value.body) {
    list.push({ key: 'translate', label: props.translated ? locale.t('msgAction.hideTranslation', 'Скрыть перевод') : locale.t('msgAction.translate', 'Перевести'),
               icon: Languages })
  }
  if (!d) list.push({ key: 'forward', label: locale.t('forward.title', 'Переслать'), icon: Forward })
  // §D19: «Напомнить» показываем ВСЕГДА, а не только когда в тексте нашлась дата —
  // человек может захотеть напомнить себе о сообщении без всякой даты. Разобранную из
  // текста дату диалог просто подставит в поле как готовый вариант.
  if (!d) list.push({ key: 'remind', label: locale.t('msgAction.remind', 'Напомнить'), icon: AlarmClock })
  list.push({ key: 'select', label: locale.t('msgAction.select', 'Выделить'), icon: ListChecks })
  list.push({ key: 'delete', label: locale.t('common.delete'), icon: Trash2, danger: true })
  if (!m.value.mine && !d) list.push({ key: 'report', label: locale.t('msgAction.report', 'Пожаловаться'), icon: Flag, danger: true })
  return list
})
</script>

<template>
  <!-- Полупрозрачная подложка: клик мимо — закрыть -->
  <div class="fixed inset-0 z-40" @click="emit('close')" @contextmenu.prevent="emit('close')">
    <!-- ⚠️ `overflow-hidden` убран: он и превращал не поместившиеся пункты в
         недостижимые. Прокрутку включает `style` — но только когда меню правда не
         влезает, иначе у короткого меню появлялась бы лишняя полоса. -->
    <div ref="box"
         class="fixed z-50 w-56 rounded-xl border border-border2 bg-card py-1 shadow-card"
         :style="style" @click.stop>
      <!-- §D3: быстрые реакции — строка эмодзи над списком действий (как в Telegram).
           flex-wrap — 9 эмодзи не помещаются в один ряд узкой панели, переносим на вторую. -->
      <div v-if="!m.deleted" class="flex flex-wrap justify-center gap-0.5 border-b border-border px-1.5 py-1.5">
        <button v-for="e in REACTIONS" :key="e" type="button"
                @click="emit('react', e); emit('close')"
                class="grid size-7 place-items-center rounded-md text-base transition-colors hover:bg-bg2">
          {{ e }}
        </button>
      </div>
      <button v-for="it in items" :key="it.key" type="button"
              @click="emit('pick', it.key); emit('close')"
              class="flex w-full items-center gap-3 px-3.5 py-2 text-left text-sm transition-colors hover:bg-bg2"
              :class="it.danger ? 'text-red' : 'text-text'">
        <component :is="it.icon" class="size-4 shrink-0" :class="it.danger ? 'text-red' : 'text-text3'" />
        {{ it.label }}
      </button>
    </div>
  </div>
</template>
