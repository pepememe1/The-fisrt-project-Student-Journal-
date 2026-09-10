<script setup>
// AdminMessenger — модерация мессенджера (только админ). Две вкладки:
//   • «Жалобы» — очередь тикетов (report на конкретное сообщение) со снимком текста + обработка;
//   • «Обращения» — чаты поддержки (кнопка ⚙ «Написать модерации» у пользователя): входящие
//     сообщения + ответ от лица модерации. Каждый просмотр/ответ пишется в аудит на сервере.
// См. docs/done/MESSENGER-PLAN.md §6, §10.
import { ref, computed, onMounted } from 'vue'
import { messengerModApi } from '@/api/endpoints'
import Avatar from '@/components/ui/Avatar.vue'
import { useLocaleStore } from '@/stores/locale'
import { roleLabel } from '@/config/roles'
import { profilePlate } from '@/theme/palette'

const BCP47 = { ru: 'ru-RU', en: 'en-US', zh: 'zh-CN' }

const locale = useLocaleStore()

// Мета-строка пользователя: студенту — группа, преподавателю — предметы.
function userMeta(u) {
  if (!u) return ''
  if (u.role === 'teacher') return (u.subjects || []).join(', ') || roleLabel('teacher')
  if (u.role === 'student') return locale.t('adminMessenger.studentGroup', { group: u.group_name || '—' })
  return u.role === 'admin' ? roleLabel('admin') : ''
}

const REASON_LABELS = computed(() => ({
  spam: locale.t('adminMessenger.reasonSpam', 'Спам'),
  harassment: locale.t('adminMessenger.reasonHarassment', 'Оскорбления'),
  threats: locale.t('adminMessenger.reasonThreats', 'Угрозы'),
  fraud: locale.t('adminMessenger.reasonFraud', 'Мошенничество'),
  illegal: locale.t('adminMessenger.reasonIllegal', 'Запрещённый контент'),
  flood: locale.t('adminMessenger.reasonFlood', 'Флуд'),
  other: locale.t('adminMessenger.reasonOther', 'Другое'),
}))

const view = ref('reports')          // reports | inbox — активная вкладка
const statusFilter = ref('open')
const reports = ref([])
const loading = ref(false)
// mode='report' — просмотр только для чтения (из тикета); mode='inbox' — с ответом модерации.
const viewer = ref({ open: false, conv: '', mode: 'report', messages: [], loading: false })
const replyDraft = ref('')

// ── Обращения (чаты поддержки) ──────────────────────────────────────────────────────
const convs = ref([])
const loadingConvs = ref(false)

async function load() {
  loading.value = true
  try { reports.value = (await messengerModApi.reports(statusFilter.value)).data.reports || [] }
  catch { reports.value = [] }
  finally { loading.value = false }
}

async function loadInbox() {
  loadingConvs.value = true
  try { convs.value = (await messengerModApi.conversations('', 'moderation')).data.conversations || [] }
  catch { convs.value = [] }
  finally { loadingConvs.value = false }
}

function switchView(v) {
  view.value = v
  if (v === 'inbox' && !convs.value.length) loadInbox()
}

onMounted(load)

async function resolve(r, status) {
  const note = status === 'dismissed' ? '' : (window.prompt(locale.t('adminMessenger.moderationNotePrompt', 'Заметка модерации (необязательно):')) || '')
  try { await messengerModApi.resolve(r.id, status, note); await load() } catch { /* noop */ }
}

// Удалить сообщение у всех (модерация, пишется в аудит). Работает в любой переписке —
// в отличие от пользовательского удаления, где нужно быть автором/участником.
async function deleteMessage(mm) {
  if (!window.confirm(locale.t('adminMessenger.confirmDeleteMessage', 'Удалить это сообщение у всех? Текст будет стёрт безвозвратно.'))) return
  try {
    await messengerModApi.deleteMessage(mm.id)
    const conv = viewer.value.conv
    viewer.value.messages = (await messengerModApi.conversationMessages(conv)).data.messages || []
  } catch { /* noop */ }
}

// Глобальный мьют/размьют пользователя (не сможет писать никому). after — что перечитать.
async function toggleMute(user, after) {
  if (!user?.id) return
  const message = user.muted
    ? locale.t('adminMessenger.confirmUnmute', { name: user.full_name })
    : locale.t('adminMessenger.confirmMute', { name: user.full_name })
  if (!window.confirm(message)) return
  try { await messengerModApi.muteUser(user.id, !user.muted); if (after) await after() }
  catch { /* noop */ }
}

// §правка: тикет закрыт (не 'open'/'in_review') — переписку по нему больше не открыть.
// Кнопка дизейблится этим же признаком, но решает СЕРВЕР (report_id передаётся ниже) —
// дизейбл кнопки сам по себе обходится прямым запросом к API, поэтому это не защита,
// а просто честная подсказка «сюда уже нельзя».
function isReportOpen(r) { return r.status === 'open' || r.status === 'in_review' }

async function openConversation(convId, mode = 'report', reportId = 0) {
  viewer.value = { open: true, conv: convId, mode, messages: [], loading: true, error: '' }
  replyDraft.value = ''
  try { viewer.value.messages = (await messengerModApi.conversationMessages(convId, reportId)).data.messages || [] }
  catch (e) {
    viewer.value.messages = []
    viewer.value.error = e?.response?.status === 403
      ? locale.t('adminMessenger.reportClosedError', 'Тикет закрыт — переписка больше не открывается.')
      : locale.t('adminMessenger.loadFailed', 'Не удалось загрузить переписку.')
  }
  finally { viewer.value.loading = false }
}

// Ответ модерации в чат поддержки (эндпоинт mod_reply, пишется в аудит).
async function sendReply() {
  const body = replyDraft.value.trim()
  if (!body || !viewer.value.conv) return
  try {
    await messengerModApi.reply(viewer.value.conv, body)
    replyDraft.value = ''
    viewer.value.messages = (await messengerModApi.conversationMessages(viewer.value.conv)).data.messages || []
  } catch { /* noop */ }
}

// В чате поддержки id беседы = `mod:{user.id}` (см. moderation_chat на сервере). Значит
// сообщение с sender_id, равным этому id, — от пользователя; иначе это ответ модерации.
const supportUserId = computed(() => (viewer.value.conv || '').replace(/^mod:/, ''))
function isModReply(mm) {
  return viewer.value.mode === 'inbox' && mm.sender_id && mm.sender_id !== supportUserId.value
}

// §правка: полная цепочка правок «изначальное->после правки->после правки» — сервер
// присылает её ТОЛЬКО сюда (mod_conversation_messages), обычным читателям недоступно.
function editChainText(mm) {
  return mm.edit_versions?.length ? mm.edit_versions.map((v) => v.body).join(' → ') : mm.body
}

function fmt(iso) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(BCP47[locale.active] || 'ru-RU', { dateStyle: 'short', timeStyle: 'short' })
}
</script>

<template>
  <div>
    <!-- Переключатель вкладок -->
    <div class="mb-4 flex gap-1 rounded-lg border border-border2 bg-card2 p-1">
      <button type="button" @click="switchView('reports')"
              class="flex-1 rounded-md px-3 py-1.5 text-sm font-semibold transition-colors"
              :class="view === 'reports' ? 'bg-accent text-white' : 'text-text3 hover:text-text'">
        {{ locale.t('adminMessenger.tabReports', 'Жалобы') }}
      </button>
      <button type="button" @click="switchView('inbox')"
              class="flex-1 rounded-md px-3 py-1.5 text-sm font-semibold transition-colors"
              :class="view === 'inbox' ? 'bg-accent text-white' : 'text-text3 hover:text-text'">
        {{ locale.t('adminMessenger.tabInbox', 'Обращения') }}
      </button>
    </div>

    <!-- ── Жалобы (тикеты) ──────────────────────────────────────────────────────────── -->
    <template v-if="view === 'reports'">
      <div class="mb-4 flex items-center gap-2">
        <label class="text-sm text-text2">{{ locale.t('adminMessenger.statusLabel', 'Статус:') }}</label>
        <select v-model="statusFilter" @change="load"
                class="rounded-md border border-border2 bg-card2 px-3 py-1.5 text-sm text-text outline-none focus:border-accent">
          <option value="open">{{ locale.t('adminMessenger.statusOpen', 'Новые') }}</option>
          <option value="in_review">{{ locale.t('adminMessenger.statusInReview', 'В работе') }}</option>
          <option value="resolved">{{ locale.t('adminMessenger.statusResolved', 'Решённые') }}</option>
          <option value="dismissed">{{ locale.t('adminMessenger.statusDismissed', 'Отклонённые') }}</option>
          <!-- §правка: авто-закрытые по таймауту 10 ч (see me.py::_expire_stale_reports) —
               отдельно от «Отклонённые», чтобы было видно, что их никто не разобрал. -->
          <option value="expired">{{ locale.t('adminMessenger.statusExpired', 'Истёкшие') }}</option>
          <option value="">{{ locale.t('adminMessenger.statusAll', 'Все') }}</option>
        </select>
        <button type="button" @click="load" class="rounded-md border border-border2 px-3 py-1.5 text-sm text-text2 hover:bg-bg2">{{ locale.t('common.refresh') }}</button>
      </div>

      <p v-if="loading" class="text-sm text-text3">{{ locale.t('common.loading') }}</p>
      <p v-else-if="!reports.length" class="rounded-lg border border-dashed border-border2 bg-card2/50 px-6 py-12 text-center text-sm text-text3">
        {{ locale.t('adminMessenger.noReports', 'Жалоб нет.') }}
      </p>

      <div class="space-y-3">
        <div v-for="r in reports" :key="r.id" class="rounded-lg border border-border bg-card p-4 shadow-card">
          <div class="mb-2 flex flex-wrap items-center gap-2">
            <span class="rounded-full bg-red/15 px-2.5 py-0.5 text-xs font-semibold text-red">{{ REASON_LABELS[r.reason_code] || r.reason_code }}</span>
            <!-- ⚠️ Жалоба на отзыв среза понимания — НЕ на сообщение: у такого тикета
                 `message_id` это id строки отзыва. Без явной пометки админ читал бы его
                 как обычную жалобу и искал бы в переписке текст, которого там нет
                 (PLAN-ACTIVITIES §8.3). -->
            <span v-if="r.target_kind === 'activity_feedback'"
                  class="rounded-full bg-blue/15 px-2.5 py-0.5 text-xs font-semibold text-blue">
              {{ locale.t('adminMessenger.fromActivity', 'Отзыв на активности') }}
            </span>
            <span class="text-xs text-text3">{{ fmt(r.created_at) }}</span>
            <span class="ml-auto text-xs text-text3">#{{ r.id }} · {{ r.status }}</span>
          </div>
          <p class="rounded-md border border-border bg-bg2 px-3 py-2 text-sm text-text">«{{ r.message_snapshot }}»</p>
          <p v-if="r.description" class="mt-2 text-sm text-text2">{{ locale.t('adminMessenger.commentLabel', 'Комментарий:') }} {{ r.description }}</p>
          <!-- Кто и на кого — с аватаром, ФИО и группой/предметами. -->
          <div class="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div class="flex items-center gap-2 rounded-md border border-border bg-bg2 p-2">
              <Avatar :src="r.reported?.avatar" :name="r.reported?.full_name || r.reported_name"
                      :role="r.reported?.role" :color="profilePlate(r.reported?.profile_color)" :size="36" />
              <div class="min-w-0">
                <div class="truncate text-xs font-semibold text-text">{{ locale.t('adminMessenger.reportedLabel', 'На:') }} {{ r.reported?.full_name || r.reported_name }}</div>
                <div class="truncate text-[11px] text-text3">{{ userMeta(r.reported) }}</div>
              </div>
            </div>
            <div class="flex items-center gap-2 rounded-md border border-border bg-bg2 p-2">
              <Avatar :src="r.reporter?.avatar" :name="r.reporter?.full_name || r.reporter_name"
                      :role="r.reporter?.role" :color="profilePlate(r.reporter?.profile_color)" :size="36" />
              <div class="min-w-0">
                <div class="truncate text-xs font-semibold text-text">{{ locale.t('adminMessenger.reporterLabel', 'От:') }} {{ r.reporter?.full_name || r.reporter_name }}</div>
                <div class="truncate text-[11px] text-text3">{{ userMeta(r.reporter) }}</div>
              </div>
            </div>
          </div>
          <div class="mt-3 flex flex-wrap gap-2">
            <button type="button" @click="openConversation(r.conversation_id, 'report', r.id)"
                    :disabled="!isReportOpen(r)" :title="isReportOpen(r) ? '' : locale.t('adminMessenger.reportClosedError', 'Тикет закрыт — переписка больше не открывается.')"
                    class="rounded-md border border-border2 px-3 py-1.5 text-xs text-text2 hover:bg-bg2 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent">{{ locale.t('adminMessenger.openConversation', 'Открыть переписку') }}</button>
            <button v-if="r.status === 'open'" type="button" @click="resolve(r, 'in_review')"
                    class="rounded-md border border-border2 px-3 py-1.5 text-xs text-text2 hover:bg-bg2">{{ locale.t('adminMessenger.markInReview', 'В работу') }}</button>
            <button type="button" @click="resolve(r, 'resolved')"
                    class="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:bg-accent2">{{ locale.t('adminMessenger.markResolved', 'Решено') }}</button>
            <button type="button" @click="resolve(r, 'dismissed')"
                    class="rounded-md border border-border2 px-3 py-1.5 text-xs text-text2 hover:bg-bg2">{{ locale.t('adminMessenger.dismiss', 'Отклонить') }}</button>
            <!-- Глобальный мьют автора сообщения (не сможет писать никому). -->
            <button v-if="r.reported && r.reported.role !== 'admin'" type="button"
                    @click="toggleMute(r.reported, load)"
                    class="ml-auto rounded-md border px-3 py-1.5 text-xs font-semibold"
                    :class="r.reported.muted ? 'border-border2 text-text2 hover:bg-bg2' : 'border-red/40 text-red hover:bg-red/10'">
              {{ r.reported.muted ? locale.t('adminMessenger.unmuteAuthor', 'Снять мьют автора') : locale.t('adminMessenger.muteAuthor', 'Замьютить автора') }}
            </button>
          </div>
        </div>
      </div>
    </template>

    <!-- ── Обращения (чаты поддержки) ───────────────────────────────────────────────── -->
    <template v-else>
      <div class="mb-4 flex items-center gap-2">
        <button type="button" @click="loadInbox" class="rounded-md border border-border2 px-3 py-1.5 text-sm text-text2 hover:bg-bg2">{{ locale.t('common.refresh') }}</button>
      </div>
      <p v-if="loadingConvs" class="text-sm text-text3">{{ locale.t('common.loading') }}</p>
      <p v-else-if="!convs.length" class="rounded-lg border border-dashed border-border2 bg-card2/50 px-6 py-12 text-center text-sm text-text3">
        {{ locale.t('adminMessenger.noInbox', 'Обращений в поддержку нет.') }}
      </p>
      <div class="space-y-2">
        <div v-for="c in convs" :key="c.conversation_id"
             class="flex items-center gap-3 rounded-lg border border-border bg-card p-3 shadow-card">
          <button type="button" @click="openConversation(c.conversation_id, 'inbox')"
                  class="flex min-w-0 flex-1 items-center gap-3 text-left transition-colors hover:opacity-80">
            <Avatar :src="c.people?.[0]?.avatar" :name="c.people?.[0]?.full_name || (c.participants || []).join(', ')"
                    :role="c.people?.[0]?.role" :color="profilePlate(c.people?.[0]?.profile_color)" :size="40" />
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-semibold text-text">{{ c.people?.[0]?.full_name || (c.participants || []).join(', ') || locale.t('adminMessenger.unknownUser', 'Пользователь') }}</div>
              <div class="truncate text-xs text-text3">{{ userMeta(c.people?.[0]) || locale.t('adminMessenger.supportRequestFallback', 'Обращение в поддержку') }}</div>
            </div>
          </button>
          <!-- Глобальный мьют обратившегося (не сможет писать никому). -->
          <button v-if="c.people?.[0] && c.people[0].role !== 'admin'" type="button"
                  @click="toggleMute(c.people[0], loadInbox)"
                  class="shrink-0 rounded-md border px-2.5 py-1.5 text-xs font-semibold"
                  :class="c.people[0].muted ? 'border-border2 text-text2 hover:bg-bg2' : 'border-red/40 text-red hover:bg-red/10'">
            {{ c.people[0].muted ? locale.t('adminMessenger.unmuteShort', 'Снять мьют') : locale.t('adminMessenger.muteShort', 'Замьютить') }}
          </button>
        </div>
      </div>
    </template>

    <!-- Просмотр переписки (аудируется на сервере). В режиме «Обращения» — с ответом модерации. -->
    <div v-if="viewer.open" class="fixed inset-0 z-50 grid place-items-center p-4"
         style="background: var(--gb-overlay)" @click.self="viewer.open = false">
      <div class="flex max-h-[80vh] w-full max-w-lg flex-col rounded-xl border border-border2 bg-card shadow-card">
        <div class="flex items-center justify-between border-b border-border p-4">
          <h3 class="font-title text-base font-bold text-text">{{ viewer.mode === 'inbox' ? locale.t('adminMessenger.supportRequestFallback', 'Обращение в поддержку') : locale.t('adminMessenger.conversationTitle', 'Переписка') }}</h3>
          <button type="button" @click="viewer.open = false" class="text-text3 hover:text-text">✕</button>
        </div>
        <div class="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-4">
          <p v-if="viewer.loading" class="text-sm text-text3">{{ locale.t('common.loading') }}</p>
          <p v-else-if="viewer.error" class="text-sm text-red">{{ viewer.error }}</p>
          <!-- Режим тикета: плоский список для чтения -->
          <template v-else-if="viewer.mode !== 'inbox'">
            <div v-for="mm in viewer.messages" :key="mm.id" class="group flex items-start gap-2 text-sm">
              <span class="shrink-0 text-text3">[{{ fmt(mm.created_at) }}]</span>
              <span class="min-w-0 flex-1 break-words text-text">
                <span v-if="mm.sender_name" class="font-semibold text-text2">{{ mm.sender_name }}:</span>
                <!-- §правка: модерация видит текст удалённого и ВСЮ цепочку правок,
                     см. mod_conversation_messages/editChainText. -->
                <span v-if="mm.deleted" class="font-semibold text-red">({{ locale.t('adminMessenger.deletedTag', 'удал.') }})</span>
                <span v-else-if="mm.edit_versions?.length" class="font-semibold text-orange">({{ locale.t('adminMessenger.editedTag', 'ред.') }})</span>
                {{ editChainText(mm) }}
              </span>
              <!-- Удалить нарушающее сообщение у всех (модерация). -->
              <button v-if="!mm.deleted" type="button" @click="deleteMessage(mm)"
                      :title="locale.t('adminMessenger.deleteForAllTitle', 'Удалить у всех')"
                      class="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold text-red opacity-0 transition-opacity hover:bg-red/10 group-hover:opacity-100">
                {{ locale.t('common.delete') }}
              </button>
            </div>
          </template>
          <!-- Режим обращения: пузыри, ответы модерации справа/акцентом -->
          <template v-else>
            <div v-for="mm in viewer.messages" :key="mm.id" class="group flex items-center gap-2"
                 :class="isModReply(mm) ? 'justify-end' : 'justify-start'">
              <button v-if="!mm.deleted && !isModReply(mm)" type="button" @click="deleteMessage(mm)"
                      :title="locale.t('adminMessenger.deleteForAllTitle', 'Удалить у всех')"
                      class="order-last shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold text-red opacity-0 transition-opacity hover:bg-red/10 group-hover:opacity-100">
                {{ locale.t('common.delete') }}
              </button>
              <div class="max-w-[80%] rounded-2xl px-3 py-1.5 text-sm"
                   :class="isModReply(mm) ? 'bg-accent text-white' : 'bg-card2 text-text'">
                <span v-if="mm.deleted" class="italic opacity-70">
                  <span class="not-italic font-semibold">({{ locale.t('adminMessenger.deletedTag', 'удал.') }})</span>
                  {{ mm.body }}
                </span>
                <span v-else class="whitespace-pre-wrap break-words">
                  <span v-if="mm.edit_versions?.length" class="font-semibold" :class="isModReply(mm) ? 'text-white' : 'text-orange'">({{ locale.t('adminMessenger.editedTag', 'ред.') }})</span>
                  {{ editChainText(mm) }}
                </span>
                <div class="mt-0.5 text-[10px]" :class="isModReply(mm) ? 'text-white/70' : 'text-text3'">
                  {{ isModReply(mm) ? locale.t('adminMessenger.moderationPrefix', 'Модерация · ') : '' }}{{ fmt(mm.created_at) }}
                </div>
              </div>
            </div>
          </template>
          <p v-if="!viewer.loading && !viewer.error && !viewer.messages.length" class="text-sm text-text3">{{ locale.t('adminMessenger.emptyDot', 'Пусто.') }}</p>
        </div>
        <!-- Композер ответа модерации (только вкладка «Обращения») -->
        <form v-if="viewer.mode === 'inbox'" class="flex items-end gap-2 border-t border-border p-3"
              @submit.prevent="sendReply">
          <textarea v-model="replyDraft" rows="1" :placeholder="locale.t('adminMessenger.replyPlaceholder', 'Ответить от лица модерации…')"
                    @keydown.enter.exact.prevent="sendReply"
                    class="max-h-32 min-h-[40px] min-w-0 flex-1 resize-none rounded-lg border border-border2 bg-card2 px-3 py-2 text-sm text-text outline-none focus:border-accent focus:bg-card" />
          <button type="submit" :disabled="!replyDraft.trim()"
                  class="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent2 disabled:opacity-50">
            {{ locale.t('adminMessenger.sendReply', 'Ответить') }}
          </button>
        </form>
      </div>
    </div>
  </div>
</template>
