<script setup>
// RejectedWritesBadge — «сервер НЕ ПРИНЯЛ вашу работу, и повтор не поможет».
//
// 🔥 Зачем заведён (07.09.2026). Очередь офлайн-правок (`api/outbox.js`) с самого начала
// умела различать две беды: запись ещё в пути (`pending`) и запись ОТВЕРГНУТА по
// существу (`rejected` — предмет больше не ваш, занятие удалено, студента нет в группе).
// Докстринг очереди прямо обещал: «такие записи уходят в список rejected и показываются
// человеку. Молча выбрасывать их нельзя: для преподавателя это потерянная работа, о
// которой он не узнает». Обещание не выполнялось: потребителя у `rejected` не было НИ
// ОДНОГО, а ConnectionBadge считает только `pending`.
// Со стороны это выглядело хуже, чем ошибка: счётчик «не отправлено: 3» ИСЧЕЗАЛ — то
// есть сообщал «всё уехало» ровно в тот момент, когда сервер отказал.
// Наш класс «обещание без вызывающего», только с ценой в виде выставленных и пропавших
// оценок.
//
// ⚠️ Отдельный компонент, а не строка в ConnectionBadge. Там состояние СВЯЗИ, оно
// проходит само; здесь — работа, которая не будет сделана, пока человек не вмешается.
// Смешать их значило бы показать «потеряна оценка» тем же спокойным серым, что «офлайн».
//
// ⚠️ Кнопки «повторить» здесь НЕТ намеренно. Сервер отказал по СУЩЕСТВУ: сколько ни шли,
// ответ будет тот же (см. ветку 4xx в outbox.flushOutbox). Кнопка, которая заведомо не
// поможет, хуже её отсутствия — человек нажмёт её трижды, прежде чем прочитает причину.
import { computed, ref } from 'vue'
import { OctagonAlert, X } from '@lucide/vue'

import { dismissRejected, rejected } from '@/api/outbox'
import { useLocaleStore } from '@/stores/locale'

const locale = useLocaleStore()
const open = ref(false)

const items = computed(() => rejected.value || [])
const visible = computed(() => items.value.length > 0)

// Человеку нужно узнать СВОЮ работу, а не служебный вид записи: «оценка 4 — Иванов И.»
// читается, `{kind: "grade", payload: {...}}` — нет.
function describe(e) {
  const p = e?.payload || {}
  const who = [p.surname, p.name].filter(Boolean).join(' ')
  if (e?.kind === 'grade') return locale.t('rejected.grade', { grade: p.grade || '—', who: who || '—' })
  if (e?.kind === 'term') return locale.t('rejected.term', { grade: p.grade || '—', who: who || '—' })
  if (e?.kind === 'lesson.create') {
    return locale.t('rejected.lessonCreate', { what: [p.subject, p.topic].filter(Boolean).join(' · ') || '—' })
  }
  if (e?.kind === 'lesson.update') {
    return locale.t('rejected.lessonUpdate', { what: [p.subject, p.topic].filter(Boolean).join(' · ') || '—' })
  }
  if (e?.kind === 'lesson.delete') return locale.t('rejected.lessonDelete', 'Удаление занятия')
  return locale.t('rejected.entry', 'Запись')
}

function dismissAll() {
  // По одной, через ту же дверь, что и штучное закрытие: второй способ очистки разошёлся
  // бы с первым (`clearOutbox` сносит заодно ЕЩЁ НЕ ОТПРАВЛЕННОЕ — здесь это была бы
  // потеря работы вместо уборки уведомления).
  items.value.map((e) => e.key).forEach(dismissRejected)
}
</script>

<template>
  <div v-if="visible"
       class="rounded-sm border border-red/40 bg-red/10 px-2.5 py-2 text-tiny text-text2">
    <button type="button" class="flex w-full items-start gap-2 text-left" @click="open = !open">
      <OctagonAlert class="mt-px size-3.5 shrink-0 text-red" />
      <span class="min-w-0 flex-1">
        <span class="block font-semibold text-text">{{ locale.t('rejected.title', 'Сервер не принял правки') }}</span>
        <span class="block opacity-80">{{ locale.t('rejected.count', { n: items.length }) }}</span>
      </span>
    </button>

    <div v-if="open" class="mt-1.5 border-t border-red/25 pt-1.5">
      <p class="mb-1.5 opacity-80">{{ locale.t('rejected.hint') }}</p>
      <ul class="space-y-1">
        <li v-for="e in items" :key="e.key" class="flex items-start gap-1.5">
          <span class="min-w-0 flex-1 break-words">
            <span class="block text-text">{{ describe(e) }}</span>
            <span class="block opacity-70">{{ e.reason }}</span>
          </span>
          <button type="button" class="shrink-0 rounded p-0.5 hover:bg-red/15"
                  :title="locale.t('rejected.dismiss', 'Понятно, убрать')"
                  @click="dismissRejected(e.key)">
            <X class="size-3" />
          </button>
        </li>
      </ul>
      <button v-if="items.length > 1" type="button"
              class="mt-1.5 rounded px-1.5 py-0.5 text-tiny underline opacity-80 hover:opacity-100"
              @click="dismissAll">{{ locale.t('rejected.dismissAll', 'Убрать все') }}</button>
    </div>
  </div>
</template>
