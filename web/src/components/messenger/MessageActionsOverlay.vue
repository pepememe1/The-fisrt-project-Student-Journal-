<script setup>
// MessageActionsOverlay — контекстное меню действий над сообщением (как в Telegram).
// Набор кнопок решает НЕ этот файл, а `utils/messageMenu.js`: правило «что показывать»
// зависит от прав, вида беседы и от того, выделен ли текст, — и проверяется числами
// (`web/tests/messageMenu.test.mjs`), а не глазами по скриншоту. Оверлей эмитит
// выбранное действие наверх (ChatThread выполняет), сам ничего с данными не делает.
//
// 🔥 ПОЗИЦИЯ БОЛЬШЕ НЕ УГАДЫВАЕТСЯ ЧИСЛОМ (05.09.2026, жалоба Влада: «меню уезжает за
// край окна, если сообщение внизу»). Здесь стояло `Math.min(y, innerHeight - 360)`, то
// есть высота меню объявлялась константой 360 — а она собирается из пунктов по правам
// плюс две строки реакций и гуляет вдвое. Теперь меню ИЗМЕРЯЕТСЯ после отрисовки, и
// куда его класть, решает `utils/menuPlacement.js`: не влезло вниз — разворачиваем вверх,
// не влезло никуда — прижимаем к верхнему краю и даём меню собственную прокрутку.
// Обрезать снизу нельзя: там «Удалить» и «Пожаловаться», ради которых меню и открывают.
//
// ⚠️ Первый кадр рисуем НЕВИДИМЫМ (`opacity-0`): пока размер неизвестен, меню стояло бы
// не на месте и прыгало на глазах. Один кадр невидимости человек не замечает, прыжок —
// замечает всегда.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Reply, Pin, PinOff, Copy, Forward, Trash2, ListChecks, Flag, AlarmClock, Languages,
  Volume2, SmilePlus, MoreHorizontal, Quote, Link2,
} from '@lucide/vue'
import { useLocaleStore } from '@/stores/locale'
import { menuMaxHeight, placeMenu } from '@/utils/menuPlacement'
import { messageMenuItems, selectionMenuItems } from '@/utils/messageMenu'

const locale = useLocaleStore()

const props = defineProps({
  message: { type: Object, required: true },
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
  // Уже показан ли перевод ЭТОГО сообщения (ChatThread — единственный, кто знает
  // translate-стор) — только чтобы подписать пункт «Перевести»/«Скрыть перевод»,
  // сам оверлей переводом не занимается (см. докстринг файла).
  translated: { type: Boolean, default: false },
  // Выделенный внутри сообщения текст. Не пусто — меню становится «телеграмным»:
  // «Ответить с цитатой», «Копировать выделенное» и т. д. (снимки 02/03 задания).
  selection: { type: String, default: '' },
  // Вид беседы и права — от них зависит СОСТАВ меню по выделению (см. messageMenu.js).
  kind: { type: String, default: 'direct' },
  canPin: { type: Boolean, default: true },
  canLink: { type: Boolean, default: false },
})
const emit = defineEmits(['pick', 'react', 'close'])

// §D3: реакции-эмодзи — тот же белый список, что на сервере (MessageReaction.emoji).
const REACTIONS = ['👍', '✅', '❤️', '😂', '👀', '🔥', '💯', '❓', '📌']

const m = computed(() => props.message)
const hasSelection = computed(() => !!props.selection.trim())

// Подписи и значки — ОДНОЙ таблицей по ключу действия. Так состав меню (messageMenu.js)
// и его вид не могут разъехаться: нет подписи — нет и пункта, это видно сразу.
const LABELS = {
  reply: () => ({ label: locale.t('msgAction.reply', 'Ответить'), icon: Reply }),
  'quote-reply': () => ({ label: locale.t('msgAction.quoteReply', 'Ответить с цитатой'), icon: Quote }),
  copy: () => ({ label: locale.t('msgAction.copy', 'Копировать текст'), icon: Copy }),
  'copy-selection': () => ({ label: locale.t('msgAction.copySelection', 'Копировать выделенное'), icon: Copy }),
  'copy-link': () => ({ label: locale.t('msgAction.copyLink', 'Копировать ссылку на сообщение'), icon: Link2 }),
  forward: () => ({ label: locale.t('forward.title', 'Переслать'), icon: Forward }),
  pin: () => ({ label: locale.t('messenger.pin', 'Закрепить'), icon: Pin }),
  unpin: () => ({ label: locale.t('messenger.unpin', 'Открепить'), icon: PinOff }),
  delete: () => ({ label: locale.t('common.delete'), icon: Trash2, danger: true }),
  report: () => ({ label: locale.t('msgAction.report', 'Пожаловаться'), icon: Flag, danger: true }),
  select: () => ({ label: locale.t('msgAction.select', 'Выделить'), icon: ListChecks }),
  speak: () => ({ label: locale.t('msgAction.speak', 'Зачитать сообщение'), icon: Volume2 }),
  remind: () => ({ label: locale.t('msgAction.remind', 'Напомнить'), icon: AlarmClock }),
  'reactions-info': () => ({ label: locale.t('msgAction.reactionsInfo', 'Реакции'), icon: SmilePlus }),
  translate: () => ({
    label: props.translated
      ? locale.t('msgAction.hideTranslation', 'Скрыть перевод')
      : locale.t('msgAction.translate', 'Перевести'),
    icon: Languages,
  }),
}
const decorate = (keys) => keys.map((key) => ({ key, ...LABELS[key]() })).filter((it) => it.label)

// Меню по выделению — ПЛОСКОЕ, без «Ещё»: в нём и так шесть строк, как в Telegram.
const plan = computed(() => (hasSelection.value
  ? { primary: selectionMenuItems(m.value, { kind: props.kind, canPin: props.canPin, canLink: props.canLink }), more: [] }
  : messageMenuItems(m.value)))
const primary = computed(() => decorate(plan.value.primary))
const more = computed(() => decorate(plan.value.more))

const showMore = ref(false)
// Раскрытие «Ещё» меняет высоту — значит меню надо ПЕРЕСТАВИТЬ, иначе оно уедет за край
// ровно тем же способом, от которого мы и лечимся.
watch(showMore, () => nextTick(reposition))

const box = ref(null)
const pos = ref(null)          // null — ещё не измерили, показывать нельзя
const maxH = ref(600)

function reposition() {
  const el = box.value
  if (!el) return
  // ⚠️ visualViewport, а НЕ innerHeight: при открытой экранной клавиатуре
  // innerHeight по-прежнему считает высоту всего окна, половину которого
  // клавиатура уже занимает — меню разворачивалось бы вниз, под неё, и
  // нижние пункты («Удалить», «Пожаловаться») снова оказались бы недоступны.
  // На настольном браузере visualViewport совпадает с innerWidth/Height.
  const vp = window.visualViewport
  const vw = vp ? vp.width : window.innerWidth
  const vh = vp ? vp.height : window.innerHeight
  maxH.value = menuMaxHeight(vh)
  const r = el.getBoundingClientRect()
  pos.value = placeMenu({ x: props.x, y: props.y, w: r.width, h: r.height, vw, vh })
}

onMounted(() => {
  nextTick(reposition)
  //Поворот телефона и появление экранной клавиатуры меняют окно под уже открытым меню.
  window.addEventListener('resize', reposition)
})
onBeforeUnmount(() => window.removeEventListener('resize', reposition))
watch(() => [props.x, props.y, props.selection], () => nextTick(reposition))

const style = computed(() => ({
  left: `${pos.value ? pos.value.left : props.x}px`,
  top: `${pos.value ? pos.value.top : props.y}px`,
  maxHeight: `${maxH.value}px`,
}))
</script>

<template>
  <!-- Полупрозрачная подложка: клик мимо — закрыть -->
  <div class="fixed inset-0 z-40" @click="emit('close')" @contextmenu.prevent="emit('close')">
    <div ref="box"
         class="fixed z-50 w-60 overflow-y-auto overflow-x-hidden rounded-xl border border-border2 bg-card py-1 shadow-card transition-opacity"
         :class="pos ? 'opacity-100' : 'opacity-0'"
         :style="style" @click.stop>
      <!-- §D3: быстрые реакции — строка эмодзи над списком действий (как в Telegram).
           При выделенном тексте их НЕТ: там разговор про кусок текста, а реакция ставится
           на всё сообщение — два разных адресата в одном меню только путают.
           flex-wrap — 9 эмодзи не помещаются в один ряд узкой панели, переносим на вторую. -->
      <div v-if="!m.deleted && !hasSelection"
           class="flex flex-wrap justify-center gap-0.5 border-b border-border px-1.5 py-1.5">
        <button v-for="e in REACTIONS" :key="e" type="button"
                @click="emit('react', e); emit('close')"
                class="grid size-7 place-items-center rounded-md text-base transition-colors hover:bg-bg2">
          {{ e }}
        </button>
      </div>
      <!-- Что именно процитируется — видно ДО нажатия. Иначе «Ответить с цитатой»
           обещает неизвестно что, а выделение к этому моменту уже не на виду. -->
      <p v-if="hasSelection" class="truncate border-b border-border px-3.5 py-1.5 text-[11px] text-text3">
        «{{ selection.trim() }}»
      </p>
      <button v-for="it in primary" :key="it.key" type="button"
              @click="emit('pick', it.key); emit('close')"
              class="flex w-full items-center gap-3 px-3.5 py-2 text-left text-sm transition-colors hover:bg-bg2"
              :class="it.danger ? 'text-red' : 'text-text'">
        <component :is="it.icon" class="size-4 shrink-0" :class="it.danger ? 'text-red' : 'text-text3'" />
        {{ it.label }}
      </button>
      <!-- «Ещё» — не свалка, а второй уровень: сюда уехало то, за чем приходят редко
           (зачитать, напомнить, перевод одного сообщения, реакции). Вырезать их совсем
           значило бы чинить длину меню потерей возможностей. -->
      <template v-if="more.length">
        <button v-if="!showMore" type="button" @click.stop="showMore = true"
                class="flex w-full items-center gap-3 border-t border-border px-3.5 py-2 text-left text-sm text-text3 transition-colors hover:bg-bg2">
          <MoreHorizontal class="size-4 shrink-0" />
          {{ locale.t('msgAction.more', 'Ещё') }}
        </button>
        <div v-else class="border-t border-border">
          <button v-for="it in more" :key="it.key" type="button"
                  @click="emit('pick', it.key); emit('close')"
                  class="flex w-full items-center gap-3 px-3.5 py-2 text-left text-sm text-text transition-colors hover:bg-bg2">
            <component :is="it.icon" class="size-4 shrink-0 text-text3" />
            {{ it.label }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>
