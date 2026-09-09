<script setup>
// PollMessage — опрос ПРЯМО В ЛЕНТЕ, как в Telegram: голосуют кнопками в сообщении,
// ничего не открывая.
//
// Почему не оверлей, как остальные активности: опрос — это вопрос на один клик. Ради
// него открывать полноэкранное окно, терять из виду переписку и потом закрывать окно —
// три действия вместо одного, и до конца доходит меньше людей, чем спросили.
//
// 🔒 Распределение голосов приходит с сервера — и он же решает, кому: автору всегда,
// всем сразу при открытых голосах, всем после завершения при включённом раскрытии.
// Здесь его не вычисляют: не прислал `tally` — показывать нечего, и это не «спрятано в
// интерфейсе», а не отдано вовсе.
import { ref, computed, watch } from 'vue'
import { useSharedNow } from '@/utils/sharedClock'
import { BarChart3, Check, Clock, Trophy } from '@lucide/vue'
import { useLocaleStore } from '@/stores/locale'
import { activitiesApi } from '@/api/endpoints'
import { pollWinners } from '@/utils/pollResult'

const props = defineProps({ activity: { type: Object, required: true } })
const locale = useLocaleStore()

const busy = ref(false)
const mine = ref(props.activity.my_choice ?? null)
const votedCount = ref(Number(props.activity.voted_count || 0))
const tally = ref(props.activity.tally || null)

const options = computed(() => props.activity.options || [])

// Счётчик и распределение приходят снаружи (WS-кадр патчит карточку в сторе) — держим
// локальные значения в согласии с ними, иначе чужой голос был бы виден только после
// перезахода в беседу.
watch(() => props.activity.voted_count, (v) => {
  if (v !== undefined && v !== null) votedCount.value = Number(v)
})
watch(() => props.activity.tally, (v) => { if (v) tally.value = v })
watch(() => props.activity.my_choice, (v) => { if (v !== undefined && mine.value === null) mine.value = v })
const closed = computed(() => props.activity.status !== 'running' || expired.value)

// Срок окончания — как в Telegram. Считает клиент от присланного сервером времени:
// тикать по сокету всем участникам ради подписи «осталось 4 мин» слишком дорого.
//
// 🔑 Часы ОБЩИЕ и идут ровно пока есть что отсчитывать (B7 разбора 07.09.2026). Прежде
// здесь стоял свой `setInterval` БЕЗУСЛОВНО: закрытый прошлогодний опрос в истории
// беседы тикал ровно так же, как идущий, — и таких карточек в ленте столько, сколько
// опросов провели за семестр.
//
// ⚠️ Три условия, и каждое отключает часы само по себе: опрос не идёт; срока нет вовсе
// (тогда отсчитывать нечего — большинство опросов такие); срок уже вышел (`expired`
// однажды став истинным, истинным и останется, даже если время замерло).
const endsAt = computed(() => Date.parse(props.activity.ends_at || ''))
const ticking = ref(props.activity.status === 'running'
  && Number.isFinite(endsAt.value) && endsAt.value > Date.now())
const now = useSharedNow(ticking)
const expired = computed(() => Number.isFinite(endsAt.value) && endsAt.value <= now.value)
watch([expired, () => props.activity.status, endsAt], () => {
  ticking.value = props.activity.status === 'running'
    && Number.isFinite(endsAt.value) && !expired.value
})
// Абсолютное время окончания («закончится в 12:42»), а не обратный отсчёт: конкретный
// момент держать в уме не нужно, в отличие от «осталось 3 мин». Считает клиент в СВОЁМ
// поясе от присланного сервером `ends_at`.
const endTimeLabel = computed(() => {
  if (!Number.isFinite(endsAt.value)) return ''
  const d = new Date(endsAt.value)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return locale.t('poll.endsAt', { time: `${hh}:${mm}` })
})

const total = computed(() => (tally.value || []).reduce((a, b) => a + b, 0) || votedCount.value)
function percent(i) {
  if (!tally.value || !total.value) return 0
  return Math.round((tally.value[i] / total.value) * 100)
}

// ── Победитель ──────────────────────────────────────────────────────────────────────
// Кубок появляется там, где есть `tally`, а решает это СЕРВЕР (`_poll_cell`): автору —
// всегда, всем сразу — при открытых голосах, всем после завершения — если автор не
// снял галочку раскрытия. Здесь ничего не вычисляется и не «прячется в интерфейсе»:
// нет распределения — нечего и показывать.
//
// До завершения кубка нет намеренно: подсвеченный лидер тянет за собой голоса
// (эффект присоединения к большинству), и опрос перестаёт мерить то, ради чего затеян.
const winners = computed(() => pollWinners(tally.value, closed.value))
// Ничья — это НЕ «победитель первый по списку». Отмечаем всех лидеров и говорим прямо:
// молчаливый выбор одного из равных был бы враньём в самом заметном месте карточки.
const isTie = computed(() => winners.value.length > 1)
function isWinner(i) { return winners.value.includes(i) }

const privacyNote = computed(() => {
  if (props.activity.public_votes) return locale.t('poll.public', 'видно, кто как проголосовал')
  // `reveal_results` приходит с сервера; у опросов, созданных до появления настройки,
  // поля нет — считаем «показывать», как и по умолчанию на сервере.
  if (props.activity.reveal_results !== false) {
    return locale.t('poll.revealAfter', 'итог увидят все после завершения')
  }
  return locale.t('poll.private', 'результаты видит преподаватель')
})

async function vote(i) {
  if (busy.value || closed.value) return
  busy.value = true
  const prev = mine.value
  mine.value = i                                     //оптимистично: клик обязан отзываться сразу
  try {
    const { data } = await activitiesApi.vote(props.activity.id, i)
    votedCount.value = Number(data.voted_count ?? votedCount.value)
    if (data.tally) tally.value = data.tally
  } catch {
    mine.value = prev                                //не прошло — возвращаем как было
  } finally { busy.value = false }
}
</script>

<template>
  <div class="my-1 w-full max-w-md rounded-2xl border border-border2 bg-card p-3">
    <div class="mb-2 flex items-start gap-2">
      <BarChart3 class="mt-0.5 size-4 shrink-0 text-accent" />
      <span class="min-w-0 flex-1 break-words text-sm font-semibold text-text">
        {{ activity.question || activity.title }}
        <!-- Кто спросил. Опрос постит система, поэтому без подписи было непонятно, чей
             это вопрос — а от этого зависит, отвечать ли на него вообще. -->
        <span v-if="activity.host_name" class="block text-tiny font-normal text-text3">
          {{ locale.t('poll.askedBy', { name: activity.host_name }) }}
        </span>
      </span>
    </div>

    <div class="flex flex-col gap-1.5">
      <button v-for="(o, i) in options" :key="i" type="button"
              :disabled="busy || closed" @click="vote(i)"
              class="relative w-full overflow-hidden rounded-lg border px-3 py-2 text-left text-sm transition-colors disabled:cursor-default"
              :class="isWinner(i) ? 'border-accent bg-accent/10 font-semibold text-text'
                      : (mine === i ? 'border-accent text-text'
                                    : 'border-border2 text-text2 hover:border-accent')">
        <!-- Полоса доли — ПОД текстом, а не вместо него: процент читают, а вариант
             выбирают, и подменять одно другим нельзя. -->
        <span v-if="tally" class="absolute inset-y-0 left-0 bg-accent/15"
              :style="{ width: percent(i) + '%' }" />
        <span class="relative flex items-center gap-2">
          <Trophy v-if="isWinner(i)" class="size-3.5 shrink-0 text-accent" />
          <Check v-else-if="mine === i" class="size-3.5 shrink-0 text-accent" />
          <span class="min-w-0 flex-1 break-words">{{ o }}</span>
          <span v-if="tally" class="shrink-0 text-xs font-semibold tabular-nums text-text3">
            {{ percent(i) }}%
          </span>
        </span>
      </button>
    </div>

    <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-tiny text-text3">
      <span>{{ locale.t('poll.votedCount', { n: total }) }}</span>
      <span v-if="isTie" class="flex items-center gap-1 font-semibold text-accent">
        <Trophy class="size-3" />{{ locale.t('poll.tie', 'ничья') }}
      </span>
      <!-- Завершённый опрос прямо говорит «завершён», и голосовать в нём уже нельзя
           (кнопки disabled по `closed`). Пока идёт — показываем МОМЕНТ окончания. -->
      <span v-if="closed" class="flex items-center gap-1 font-semibold text-text2">
        {{ locale.t('poll.closed', 'опрос завершён') }}
      </span>
      <span v-else-if="endTimeLabel" class="flex items-center gap-1">
        <Clock class="size-3" />{{ endTimeLabel }}
      </span>
      <!-- Честная подпись, и она же — ОБЕЩАНИЕ, данное ДО голосования. Именно поэтому
           раскрытие итога в конце не является изменением правил задним числом: человек
           видел эту строку, когда нажимал вариант. Три состояния, а не два. -->
      <span>{{ privacyNote }}</span>
    </div>
  </div>
</template>
