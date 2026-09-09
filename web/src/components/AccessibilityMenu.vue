<script setup>
// AccessibilityMenu — кнопка-очки «Версия для слабовидящих» + всплывающее меню.
//
// Ровно тот элемент, что стоит на порталах вузов (иконка очков): крупность шрифта в три
// ступени и высокий контраст. Настройки живут в stores/a11y.js (устройство, localStorage).
//
// Размещается в сайдбаре (десктоп) и на мобильной полосе — всегда на виду, потому что
// человеку со слабым зрением её нельзя прятать в глубину настроек.
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { menuMaxHeight, placeAnchored } from '@/utils/menuPlacement'
import { Glasses, Contrast, X } from '@lucide/vue'
import { useA11yStore } from '@/stores/a11y'
import { useLocaleStore } from '@/stores/locale'

// Открывать меню вверх (в сайдбаре кнопка внизу) или вниз (на мобильной полосе сверху).
// `placement` читается прямо в шаблоне (<script setup> раскрывает пропсы автоматически).
const props = defineProps({ placement: { type: String, default: 'up' } })

const a11y = useA11yStore()
const loc = useLocaleStore()
const open = ref(false)
const rootEl = ref(null)
// Ссылка на само меню — без неё нечего измерять, а без измерения вертикальный кламп
// остаётся обещанием в комментарии (см. place()).
const menuEl = ref(null)

// ━━ ПОЛОЖЕНИЕ МЕНЮ СЧИТАЕТСЯ, А НЕ ЗАДАЁТСЯ ПРИВЯЗКОЙ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 🔥 Куплено дефектом (23.08.2026, отзыв Влада со скриншотом). Меню было `absolute` с
// шириной `w-60`, то есть 15rem — В ЕДИНИЦАХ ШРИФТА. А само оно этот шрифт и
// увеличивает: включаешь «Максимум» — панель разом становится в полтора раза шире и
// уезжает за левый край экрана, вместе с кнопкой «вернуть обычный вид». То есть режим
// для слабовидящих ломал ровно тот элемент, которым из него выходят.
//
// ⚠️ Вторая причина того же: сайдбар теперь тянется, и в свёрнутом виде он шириной с
// иконку — меню, привязанное правым краем к кнопке, ушло бы за экран и без всякого
// шрифта.
//
// Поэтому: ширина в ПИКСЕЛЯХ (шрифт её не двигает), координаты считаются от кнопки при
// открытии и ЗАЖИМАЮТСЯ границами окна. `fixed`, а не `absolute` — иначе прижатие к
// окну невозможно в принципе, координаты считались бы от родителя.
const MENU_W = 250
const pos = ref({ left: 0, top: 0 })

// 🔥 ЗДЕСЬ СТОЯЛ КОММЕНТАРИЙ «вылезло за край — прижимаем», А ПРИЖИМАЛАСЬ ТОЛЬКО
// ГОРИЗОНТАЛЬ (найдено при разборе жалобы про меню сообщения, 05.09.2026). Вертикаль
// считалась как `r.top - pad` со сдвигом `translateY(-100%)` в стиле: у верхней кромки
// экрана меню уезжало за неё целиком, и заметно это только на низком окне. Комментарий,
// обещающий проверку, которой нет, — тот же класс дефекта, что константа 360 в меню
// сообщения: он не краснеет, он просто перестаёт быть правдой.
//
// ⚠️ Считаем по ИЗМЕРЕННОЙ высоте, поэтому `place()` зовётся уже после отрисовки меню
// (см. watch на `open`), а не в момент нажатия кнопки.
function place() {
  const el = rootEl.value
  if (!el) return
  const anchor = el.getBoundingClientRect()
  const box = menuEl.value?.getBoundingClientRect()
  const h = box?.height || 0
  //Высота ещё не известна (первый кадр) — ставим по-старому и переставим следующим
  //тиком: мигание на кадр незаметно, прыжок готового меню — заметен.
  const r = placeAnchored({
    anchor, w: MENU_W, h, vw: window.innerWidth, vh: window.innerHeight,
    prefer: props.placement === 'up' ? 'up' : 'down',
  })
  //`up` в состоянии больше не хранится: разворот учтён в самом `top`, а лишнее поле
  //однажды прочитали бы как «меню открыто вверх» и снова прибавили бы к нему transform.
  pos.value = { left: r.left, top: r.top }
}

const t = (k, f) => loc.t(k, f)
// Ступени крупности: подпись и множитель для показа.
const steps = computed(() => [
  { step: 0, label: t('a11y.sizeNormal', 'Обычный') },
  { step: 1, label: t('a11y.sizeLarge', 'Крупнее') },
  { step: 2, label: t('a11y.sizeXL', 'Максимум') },
])

function onDocClick(e) {
  if (open.value && rootEl.value && !rootEl.value.contains(e.target)) open.value = false
}
function onKey(e) { if (e.key === 'Escape') open.value = false }
watch(open, (v) => { if (v) nextTick(place) })
//Окно меняет размер (и шрифт меняет разметку) — пересчитываем, иначе меню «отклеится».
function onViewportChange() { if (open.value) place() }

onMounted(() => {
  document.addEventListener('pointerdown', onDocClick, true)
  document.addEventListener('keydown', onKey)
  //⚠️ Прокрутка слушается в ФАЗЕ ЗАХВАТА и по всему документу: кнопка стоит в сайдбаре,
  //у которого своя прокрутка, и обычный слушатель на window её не увидит — меню повисло
  //бы в воздухе, оторвавшись от кнопки.
  window.addEventListener('resize', onViewportChange)
  document.addEventListener('scroll', onViewportChange, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocClick, true)
  document.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', onViewportChange)
  document.removeEventListener('scroll', onViewportChange, true)
})
</script>

<template>
  <div ref="rootEl" class="relative">
    <!-- Кнопка-очки. Подсвечена акцентом, когда режим включён. -->
    <button type="button" @click="open = !open"
            :aria-label="t('a11y.title', 'Версия для слабовидящих')"
            :title="t('a11y.title', 'Версия для слабовидящих')"
            :aria-pressed="a11y.active"
            class="grid size-9 place-items-center rounded-md border transition-colors"
            :class="a11y.active
              ? 'border-accent bg-accent-glow text-accent'
              : 'border-transparent text-text3 hover:bg-accent-glow hover:text-accent'">
      <Glasses class="size-5" />
    </button>

    <!-- Меню -->
    <transition name="a11y-pop">
      <div v-if="open" ref="menuEl"
           class="fixed z-50 overflow-y-auto rounded-xl border border-border2 bg-card p-3 shadow-card"
           :style="{ left: pos.left + 'px', top: pos.top + 'px', width: MENU_W + 'px',
                     maxWidth: 'calc(100vw - 16px)',
                     maxHeight: menuMaxHeight(typeof window === 'undefined' ? 800 : window.innerHeight) + 'px' }">
        <div class="mb-2 flex items-center gap-2">
          <Glasses class="size-4 shrink-0 text-accent" />
          <p class="min-w-0 flex-1 truncate font-title text-sm font-bold text-text">
            {{ t('a11y.title', 'Версия для слабовидящих') }}
          </p>
          <button type="button" @click="open = false"
                  :aria-label="t('common.close', 'Закрыть')"
                  class="grid size-6 shrink-0 place-items-center rounded text-text3 hover:bg-bg2 hover:text-text">
            <X class="size-4" />
          </button>
        </div>

        <!-- Крупность шрифта -->
        <p class="mb-1 text-tiny font-medium uppercase tracking-wide text-text2">
          {{ t('a11y.fontSize', 'Размер шрифта') }}
        </p>
        <div class="mb-3 flex gap-1">
          <button v-for="s in steps" :key="s.step" type="button"
                  @click="a11y.setFontScale(s.step)"
                  class="flex-1 rounded-md border px-1 py-1.5 text-center transition-colors"
                  :class="a11y.fontScale === s.step
                    ? 'border-accent bg-accent-glow text-accent'
                    : 'border-border2 text-text3 hover:border-accent hover:text-accent'">
            <span class="block font-bold leading-none"
                  :style="{ fontSize: (12 + s.step * 4) + 'px' }">А</span>
            <span class="mt-0.5 block text-tiny">{{ s.label }}</span>
          </button>
        </div>

        <!-- Высокий контраст -->
        <button type="button" @click="a11y.toggleContrast()"
                class="flex w-full items-center gap-2 rounded-md border px-2.5 py-2 text-left text-sm transition-colors"
                :class="a11y.contrast
                  ? 'border-accent bg-accent-glow text-accent'
                  : 'border-border2 text-text2 hover:border-accent'">
          <Contrast class="size-4 shrink-0" />
          <span class="min-w-0 flex-1 truncate">{{ t('a11y.contrast', 'Высокий контраст') }}</span>
          <span class="shrink-0 rounded-full px-2 py-0.5 text-tiny font-bold"
                :class="a11y.contrast ? 'bg-accent text-white' : 'bg-bg2 text-text3'">
            {{ a11y.contrast ? t('common.on', 'вкл') : t('common.off', 'выкл') }}
          </span>
        </button>

        <!-- Сброс -->
        <button v-if="a11y.active" type="button" @click="a11y.reset()"
                class="mt-2 w-full rounded-md px-2.5 py-1.5 text-center text-xs text-text3 transition-colors hover:bg-bg2 hover:text-text">
          {{ t('a11y.reset', 'Вернуть обычный вид') }}
        </button>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.a11y-pop-enter-active,
.a11y-pop-leave-active { transition: opacity 0.12s ease, transform 0.12s ease; }
.a11y-pop-enter-from,
.a11y-pop-leave-to { opacity: 0; transform: translateY(4px); }
</style>
