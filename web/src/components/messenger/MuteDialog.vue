<script setup>
/**
 * MuteDialog.vue — выдача ограничения переписки со СРОКОМ.
 *
 * 🔥 БЕССРОЧНОГО ВАРИАНТА ЗДЕСЬ НЕТ, и это не забывчивость: сервер его и не примет
 * (`mod_mute_user`). Наказание «пока не снимут» снимать некому — о замьюченном просто
 * перестают вспоминать, он тихо перестаёт писать, и продукт считает это нормой.
 *
 * ⚠️ КНОПКА ГАСНЕТ ПРИ ЗАКРЫТОМ ТИКЕТЕ (`disabled`), но это лишь подсказка: настоящая
 * проверка стоит на сервере, потому что погашенную кнопку обходит прямой запрос.
 * Показывать её всё же надо — исчезнувшая кнопка читается как «сломалось», а не как
 * «по этому тикету расследование окончено».
 *
 * ⚠️ ПРИЧИНА НЕОБЯЗАТЕЛЬНА, НО ПОДСКАЗАНА. Требовать её значит получить «-» в поле у
 * половины наказаний; не показывать вовсе — значит, что человек, спросивший «за что»,
 * получит в ответ пустоту, и разбираться придётся тому же модератору.
 */
import { ref, computed, watch } from 'vue'
import { useLocaleStore } from '@/stores/locale'
import { X, Clock, ShieldAlert } from '@lucide/vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  user: { type: Object, default: null },
  // Тикет, из которого выдаётся наказание. 0 — выдача не из жалобы (вкладка «Люди»).
  reportId: { type: Number, default: 0 },
  // Тикет закрыт/истёк — действия по нему недоступны (то же правило, что у просмотра
  // переписки: расследование окончено, наказывать задним числом нельзя).
  ticketClosed: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'submit'])
const locale = useLocaleStore()

// Готовые сроки — ровно те же, что перечислены на сервере (MUTE_PRESET_HOURS). Список
// продублирован намеренно: сервер обязан проверять границы сам, а клиент обязан
// предлагать выбор; ссылаться друг на друга им нечем, поэтому расхождение ловит тест.
const PRESETS = [1, 3, 5, 12, 24]

const preset = ref(1)          // выбранный готовый срок в часах; 0 — свой
const days = ref(0)
const hours = ref(0)
const minutes = ref(0)
const reason = ref('')

watch(() => props.open, (v) => {
  if (!v) return
  preset.value = 1; days.value = 0; hours.value = 0; minutes.value = 0; reason.value = ''
})

const totalMinutes = computed(() =>
  preset.value > 0 ? preset.value * 60
    : (Number(days.value) || 0) * 1440 + (Number(hours.value) || 0) * 60 + (Number(minutes.value) || 0))

// Человеческий срок — чтобы модератор видел, что именно он сейчас выдаст. «90 минут» и
// «полтора часа» это одно и то же число, но ошибаются люди именно на нём.
const humanTerm = computed(() => {
  const m = totalMinutes.value
  if (m <= 0) return ''
  const d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60), mi = m % 60
  return [d && `${d} д`, h && `${h} ч`, mi && `${mi} мин`].filter(Boolean).join(' ')
})

const canSubmit = computed(() =>
  !props.busy && !props.ticketClosed && totalMinutes.value > 0 && totalMinutes.value <= 365 * 1440)

function submit() {
  if (!canSubmit.value) return
  emit('submit', {
    days: preset.value > 0 ? 0 : Number(days.value) || 0,
    hours: preset.value > 0 ? preset.value : Number(hours.value) || 0,
    minutes: preset.value > 0 ? 0 : Number(minutes.value) || 0,
    reason: reason.value.trim(),
    reportId: props.reportId,
  })
}
</script>

<template>
  <div v-if="open" class="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4"
       @click.self="emit('close')">
    <div class="w-full max-w-md rounded-2xl border border-border2 bg-card p-5 shadow-xl">
      <div class="mb-4 flex items-start justify-between gap-3">
        <div class="flex items-center gap-2">
          <ShieldAlert class="size-5 text-red" />
          <h3 class="font-title text-base font-bold text-text">
            {{ locale.t('mute.title', 'Ограничить переписку') }}
          </h3>
        </div>
        <button type="button" @click="emit('close')" class="text-text3 hover:text-text">
          <X class="size-5" />
        </button>
      </div>

      <p class="mb-4 text-sm text-text2">
        {{ user?.full_name || '' }}
      </p>

      <!-- Закрытый тикет: объясняем ПРИЧИНУ недоступности. Просто погашенная кнопка без
           объяснения читается как поломка, и следующий вопрос приходит нам. -->
      <div v-if="ticketClosed"
           class="mb-4 rounded-xl border border-border2 bg-bg2 px-3 py-2 text-xs text-text2">
        {{ locale.t('mute.ticketClosed', 'Тикет закрыт — действия по нему недоступны. Наказывать по завершённому разбору нельзя; откройте тикет заново, если разбор продолжается.') }}
      </div>

      <div class="mb-4">
        <div class="mb-2 text-xs font-semibold uppercase tracking-wide text-text3">
          {{ locale.t('mute.term', 'Срок') }}
        </div>
        <div class="flex flex-wrap gap-2">
          <button v-for="h in PRESETS" :key="h" type="button" @click="preset = h"
                  :disabled="ticketClosed"
                  class="rounded-full border px-3 py-1.5 text-sm disabled:opacity-40"
                  :class="preset === h ? 'border-accent bg-accent/10 text-accent'
                                       : 'border-border2 text-text2 hover:bg-bg2'">
            {{ h }} {{ locale.t('mute.hoursShort', 'ч') }}
          </button>
          <button type="button" @click="preset = 0" :disabled="ticketClosed"
                  class="rounded-full border px-3 py-1.5 text-sm disabled:opacity-40"
                  :class="preset === 0 ? 'border-accent bg-accent/10 text-accent'
                                       : 'border-border2 text-text2 hover:bg-bg2'">
            {{ locale.t('mute.custom', 'Свой') }}
          </button>
        </div>
      </div>

      <div v-if="preset === 0" class="mb-4 grid grid-cols-3 gap-2">
        <label class="block">
          <span class="mb-1 block text-xs text-text3">{{ locale.t('mute.days', 'Дни') }}</span>
          <input v-model.number="days" type="number" min="0" max="365" :disabled="ticketClosed"
                 class="w-full rounded-lg border border-border2 bg-bg2 px-2 py-1.5 text-sm text-text" />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs text-text3">{{ locale.t('mute.hours', 'Часы') }}</span>
          <input v-model.number="hours" type="number" min="0" max="23" :disabled="ticketClosed"
                 class="w-full rounded-lg border border-border2 bg-bg2 px-2 py-1.5 text-sm text-text" />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs text-text3">{{ locale.t('mute.minutes', 'Минуты') }}</span>
          <input v-model.number="minutes" type="number" min="0" max="59" :disabled="ticketClosed"
                 class="w-full rounded-lg border border-border2 bg-bg2 px-2 py-1.5 text-sm text-text" />
        </label>
      </div>

      <label class="mb-4 block">
        <span class="mb-1 block text-xs font-semibold uppercase tracking-wide text-text3">
          {{ locale.t('mute.reason', 'Причина (увидит наказанный)') }}
        </span>
        <input v-model="reason" type="text" maxlength="300" :disabled="ticketClosed"
               :placeholder="locale.t('mute.reasonHint', 'Например: оскорбления в беседе группы')"
               class="w-full rounded-lg border border-border2 bg-bg2 px-3 py-2 text-sm text-text" />
      </label>

      <div class="flex items-center justify-between gap-3">
        <div class="flex items-center gap-1.5 text-xs text-text3">
          <Clock class="size-3.5" />
          <span v-if="humanTerm">{{ locale.t('mute.willLast', 'Ограничение на') }} {{ humanTerm }}</span>
          <span v-else>{{ locale.t('mute.pickTerm', 'Укажите срок') }}</span>
        </div>
        <div class="flex gap-2">
          <button type="button" @click="emit('close')"
                  class="rounded-lg border border-border2 px-3 py-1.5 text-sm text-text2 hover:bg-bg2">
            {{ locale.t('common.cancel', 'Отмена') }}
          </button>
          <button type="button" @click="submit" :disabled="!canSubmit"
                  class="rounded-lg bg-red px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40">
            {{ locale.t('mute.apply', 'Ограничить') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
