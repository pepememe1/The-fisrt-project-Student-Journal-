<script setup>
/**
 * ReportUserDialog.vue — жалоба на ПРОФИЛЬ человека (не на сообщение).
 *
 * ⚠️ ПОЧЕМУ ОТДЕЛЬНЫЙ ДИАЛОГ, А НЕ ТОТ ЖЕ, ЧТО У СООБЩЕНИЯ. У жалобы на сообщение
 * предмет очевиден — вот текст; здесь его надо НАЗВАТЬ: имя, «о себе», аватарка или
 * баннер. Без этого модератор открывает профиль и гадает, что именно не так — а профиль
 * к тому времени уже переписан.
 *
 * ⚠️ Снимок поля делает СЕРВЕР. Присланный клиентом снимок — это текст, который пишет
 * жалующийся, то есть готовый способ приписать чужому профилю то, чего там не было.
 *
 * ⚠️ Ссылка на правила здесь не для вида: причина жалобы должна опираться на правило, а
 * не на «мне не нравится». Открывается в новой вкладке, чтобы не потерять набранное.
 */
import { ref, watch } from 'vue'
import { useLocaleStore } from '@/stores/locale'
import { X, Flag, ExternalLink } from '@lucide/vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  user: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'submit'])
const locale = useLocaleStore()

// Коды причин ТЕ ЖЕ, что у жалобы на сообщение (_REASONS на сервере): две очереди, один
// словарь причин — иначе отчёт «на что жалуются чаще» пришлось бы собирать по двум
// несовпадающим наборам.
const REASONS = [
  ['harassment', 'Оскорбления или травля'],
  ['threats', 'Угрозы'],
  ['spam', 'Спам или реклама'],
  ['fraud', 'Обман, выманивание доступа'],
  ['illegal', 'Запрещённое содержимое'],
  ['other', 'Другое'],
]
const FIELDS = [
  ['profile', 'Профиль целиком'],
  ['name', 'Имя'],
  ['bio', 'Описание «о себе»'],
  ['avatar', 'Аватарка'],
  ['banner', 'Баннер'],
]

const reason = ref('harassment')
const field = ref('profile')
const description = ref('')

watch(() => props.open, (v) => {
  if (!v) return
  reason.value = 'harassment'; field.value = 'profile'; description.value = ''
})

function submit() {
  if (props.busy) return
  emit('submit', { reasonCode: reason.value, field: field.value,
                   description: description.value.trim() })
}
</script>

<template>
  <div v-if="open" class="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4"
       @click.self="emit('close')">
    <div class="w-full max-w-md rounded-2xl border border-border2 bg-card p-5 shadow-xl">
      <div class="mb-4 flex items-start justify-between gap-3">
        <div class="flex items-center gap-2">
          <Flag class="size-5 text-red" />
          <h3 class="font-title text-base font-bold text-text">
            {{ locale.t('reportUser.title', 'Пожаловаться на профиль') }}
          </h3>
        </div>
        <button type="button" @click="emit('close')" class="text-text3 hover:text-text">
          <X class="size-5" />
        </button>
      </div>

      <p class="mb-4 text-sm text-text2">{{ user?.full_name || '' }}</p>

      <label class="mb-3 block">
        <span class="mb-1 block text-xs font-semibold uppercase tracking-wide text-text3">
          {{ locale.t('reportUser.what', 'Что не так') }}
        </span>
        <select v-model="field"
                class="w-full rounded-lg border border-border2 bg-bg2 px-3 py-2 text-sm text-text">
          <option v-for="([v, label]) in FIELDS" :key="v" :value="v">{{ label }}</option>
        </select>
      </label>

      <label class="mb-3 block">
        <span class="mb-1 block text-xs font-semibold uppercase tracking-wide text-text3">
          {{ locale.t('reportUser.reason', 'Причина') }}
        </span>
        <select v-model="reason"
                class="w-full rounded-lg border border-border2 bg-bg2 px-3 py-2 text-sm text-text">
          <option v-for="([v, label]) in REASONS" :key="v" :value="v">{{ label }}</option>
        </select>
      </label>

      <label class="mb-3 block">
        <span class="mb-1 block text-xs font-semibold uppercase tracking-wide text-text3">
          {{ locale.t('reportUser.details', 'Подробности') }}
        </span>
        <textarea v-model="description" rows="3" maxlength="2000"
                  :placeholder="locale.t('reportUser.detailsHint', 'Что именно нарушает правила — модератор увидит это первым')"
                  class="w-full resize-none rounded-lg border border-border2 bg-bg2 px-3 py-2 text-sm text-text" />
      </label>

      <RouterLink to="/rules" target="_blank"
                  class="mb-4 inline-flex items-center gap-1.5 text-xs text-accent hover:underline">
        <ExternalLink class="size-3.5" />{{ locale.t('reportUser.rules', 'Правила сообщества') }}
      </RouterLink>

      <p class="mb-4 text-xs text-text3">
        {{ locale.t('reportUser.honest', 'Модератор видит, кто пожаловался. Жалоба «чтобы наказать» — тоже нарушение.') }}
      </p>

      <div class="flex justify-end gap-2">
        <button type="button" @click="emit('close')"
                class="rounded-lg border border-border2 px-3 py-1.5 text-sm text-text2 hover:bg-bg2">
          {{ locale.t('common.cancel', 'Отмена') }}
        </button>
        <button type="button" @click="submit" :disabled="busy"
                class="rounded-lg bg-red px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40">
          {{ locale.t('reportUser.send', 'Отправить жалобу') }}
        </button>
      </div>
    </div>
  </div>
</template>
