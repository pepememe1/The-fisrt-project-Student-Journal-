<script setup>
// AdminMessenger — рабочее место МОДЕРАЦИИ. Открыта админу и роли `moderator`
// (страница одна на обе роли — расходиться двум копиям одного экрана нечем).
//
// Четыре вкладки:
//   • «Жалобы» — тикеты на конкретное СООБЩЕНИЕ со снимком текста;
//   • «Профили» — тикеты на ПРОФИЛЬ (имя, «о себе», аватарка). Отдельная очередь, а не
//     фильтр: разбор начинается с другого и заканчивается другими действиями;
//   • «Обращения» — чаты поддержки (кнопка ⚙ у пользователя) + ответ модерации;
//   • «Люди» — те, НА КОГО ЕСТЬ ЖАЛОБА. Не каталог колледжа: полный список модератору не
//     нужен ни для одной задачи, и сервер его не отдаёт (см. mod_users).
//
// Каждый просмотр чужой переписки и каждое наказание пишется в аудит на сервере.
// См. docs/done/MESSENGER-PLAN.md §6, §10.
import { ref, computed, onMounted } from 'vue'
import { messengerModApi } from '@/api/endpoints'
import Avatar from '@/components/ui/Avatar.vue'
import MuteDialog from '@/components/messenger/MuteDialog.vue'
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

// ── Жалобы на профили ───────────────────────────────────────────────────────────────
const userReports = ref([])
const loadingUserReports = ref(false)

// ── Люди, на которых есть жалоба ────────────────────────────────────────────────────
const people = ref([])
const loadingPeople = ref(false)
const peopleQuery = ref('')

// Сколько новых сообщений ждут ответа во всех обращениях. Считается по полю `fresh`,
// которое сервер выводит из «написал ли человек ПОСЛЕ последнего ответа модерации» —
// а не по чьей-то метке прочтения: модераторов несколько, и «не читал лично я» означало
// бы, что одно обращение висит новым у каждого по отдельности.
const inboxFresh = computed(() => convs.value.reduce((s, c) => s + (c.fresh || 0), 0))

// ── Диалог ограничения и карточка истории ───────────────────────────────────────────
const muteFor = ref({ open: false, user: null, reportId: 0, closed: false })
const muteBusy = ref(false)
const historyFor = ref({ open: false, user: null, data: null, loading: false })

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

// ⚠️ «Не удалось загрузить» и «ничего нет» — РАЗНЫЕ сообщения. Показав пустоту при сбое
// сети, мы говорим модератору «всё чисто»: он закроет вкладку, а жалобы останутся
// неразобранными. Отказ, выглядящий как хорошая новость, — худший вид тихого отказа.
const loadError = ref('')

async function loadUserReports() {
  loadingUserReports.value = true
  loadError.value = ''
  try { userReports.value = (await messengerModApi.userReports(statusFilter.value)).data.reports || [] }
  catch {
    userReports.value = []
    loadError.value = locale.t('adminMessenger.loadFailed', 'Не удалось загрузить.')
  }
  finally { loadingUserReports.value = false }
}

async function loadPeople() {
  loadingPeople.value = true
  loadError.value = ''
  try { people.value = (await messengerModApi.users(peopleQuery.value)).data.users || [] }
  catch {
    people.value = []
    loadError.value = locale.t('adminMessenger.loadFailed', 'Не удалось загрузить.')
  }
  finally { loadingPeople.value = false }
}

function switchView(v) {
  view.value = v
  if (v === 'inbox' && !convs.value.length) loadInbox()
  if (v === 'profiles' && !userReports.value.length) loadUserReports()
  if (v === 'people') loadPeople()
}

// Перечитать ТУ вкладку, которая открыта: после наказания устаревает именно она, а
// перечитывать все четыре — три лишних запроса на каждое нажатие.
async function reloadCurrent() {
  if (view.value === 'reports') return load()
  if (view.value === 'profiles') return loadUserReports()
  if (view.value === 'inbox') return loadInbox()
  return loadPeople()
}

async function resolveUserReport(r, status) {
  const note = status === 'dismissed' ? ''
    : (window.prompt(locale.t('adminMessenger.moderationNotePrompt', 'Заметка модерации (необязательно):')) || '')
  try { await messengerModApi.resolveUserReport(r.id, status, note); await loadUserReports() }
  catch { window.alert(locale.t('adminMessenger.loadFailed', 'Не удалось загрузить.')) }
}

// Почистить публичное поле профиля. Сервер разрешит только при ОТКРЫТОЙ жалобе и только
// очистит поле — вписать что-либо от имени человека нельзя по построению.
async function clearProfileField(user, field) {
  const names = { bio: 'описание «о себе»', avatar: 'аватарку', profile_banner: 'баннер',
                  name_effect: 'эффект имени', name_color: 'цвет имени' }
  if (!window.confirm(`Очистить ${names[field] || field} у «${user.full_name}»?`)) return
  try { await messengerModApi.clearProfile(user.id, [field]); await reloadCurrent() }
  catch { window.alert(locale.t('adminMessenger.clearFailed', 'Не удалось — возможно, по этому человеку нет открытой жалобы.')) }
}

// История наказаний: первый это раз или пятый. Читается из журнала аудита — строка мьюта
// живёт только до истечения срока и прошлого помнить не может.
async function openHistory(user) {
  historyFor.value = { open: true, user, data: null, loading: true }
  try { historyFor.value.data = (await messengerModApi.userHistory(user.id)).data }
  catch { historyFor.value.data = null }
  finally { historyFor.value.loading = false }
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

// Ограничение переписки. Выдача — через диалог со СРОКОМ (бессрочных мьютов больше нет,
// сервер их и не примет), снятие — сразу, одним подтверждением: снять наказание это не то
// действие, которое надо затруднять.
//
// ⚠️ `reportId` передаётся, когда наказание выдаётся ИЗ тикета: сервер проверит, что тикет
// ещё живой. Дизейбл кнопки — честная подсказка, а не защита: кнопку обходит прямой запрос.
async function openMute(user, { reportId = 0, closed = false } = {}) {
  if (!user?.id) return
  muteFor.value = { open: true, user, reportId, closed }
}

async function submitMute(payload) {
  const user = muteFor.value.user
  if (!user) return
  muteBusy.value = true
  try {
    await messengerModApi.muteUser(user.id, payload)
    muteFor.value = { open: false, user: null, reportId: 0, closed: false }
    await reloadCurrent()
  } catch (e) {
    window.alert(e?.response?.data?.detail || locale.t('common.error', 'Не получилось'))
  } finally { muteBusy.value = false }
}

async function unmute(user) {
  if (!user?.id) return
  if (!window.confirm(locale.t('adminMessenger.confirmUnmute', { name: user.full_name }))) return
  try { await messengerModApi.unmuteUser(user.id); await reloadCurrent() }
  catch { window.alert(locale.t('common.error', 'Не получилось')) }
}

// Срок ограничения словами — модератор должен видеть, до каких пор оно действует, а не
// вычислять это из метки времени.
function muteLeft(iso) {
  if (!iso) return ''
  const ms = new Date(iso).getTime() - Date.now()
  if (!Number.isFinite(ms) || ms <= 0) return ''
  const mins = Math.ceil(ms / 60000)
  if (mins < 60) return `${mins} мин`
  const hours = Math.ceil(mins / 60)
  return hours < 24 ? `${hours} ч` : `${Math.ceil(hours / 24)} д`
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
      <button type="button" @click="switchView('profiles')"
              class="flex-1 rounded-md px-3 py-1.5 text-sm font-semibold transition-colors"
              :class="view === 'profiles' ? 'bg-accent text-white' : 'text-text3 hover:text-text'">
        {{ locale.t('adminMessenger.tabProfiles', 'Профили') }}
      </button>
      <button type="button" @click="switchView('inbox')"
              class="flex-1 rounded-md px-3 py-1.5 text-sm font-semibold transition-colors"
              :class="view === 'inbox' ? 'bg-accent text-white' : 'text-text3 hover:text-text'">
        {{ locale.t('adminMessenger.tabInbox', 'Обращения') }}
        <!-- Число новых сообщений в очереди обращений: очередь, по которой надо ходить
             руками, чтобы узнать, есть ли в ней что-то, перестают просматривать. -->
        <span v-if="inboxFresh" class="ml-1 rounded-full bg-red px-1.5 text-[11px] text-white">{{ inboxFresh }}</span>
      </button>
      <button type="button" @click="switchView('people')"
              class="flex-1 rounded-md px-3 py-1.5 text-sm font-semibold transition-colors"
              :class="view === 'people' ? 'bg-accent text-white' : 'text-text3 hover:text-text'">
        {{ locale.t('adminMessenger.tabPeople', 'Люди') }}
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
            <!-- Ограничение переписки для автора сообщения.
                 ⚠️ КНОПКА ГАСНЕТ ПРИ ЗАКРЫТОМ ТИКЕТЕ — то же правило, что у просмотра
                 переписки: расследование окончено, наказывать по нему задним числом
                 нельзя. Проверку делает СЕРВЕР (`_require_live_ticket`); здесь только
                 честная подсказка, потому что погашенную кнопку обходит прямой запрос.
                 Снятие ограничения доступно ВСЕГДА: отменять наказание по закрытому
                 тикету не только можно, но и нужно — иначе человек остался бы наказан
                 ровно потому, что разбор довели до конца. -->
            <button v-if="r.reported && r.reported.role !== 'admin' && !r.reported.muted"
                    type="button" @click="openMute(r.reported, { reportId: r.id, closed: !isReportOpen(r) })"
                    :disabled="!isReportOpen(r)"
                    :title="isReportOpen(r) ? '' : locale.t('adminMessenger.muteClosedHint', 'Тикет закрыт — действия по нему недоступны')"
                    class="ml-auto rounded-md border border-red/40 px-3 py-1.5 text-xs font-semibold text-red
                           hover:bg-red/10 disabled:cursor-not-allowed disabled:opacity-40">
              {{ locale.t('adminMessenger.muteAuthor', 'Ограничить автора') }}
            </button>
            <button v-else-if="r.reported && r.reported.muted" type="button" @click="unmute(r.reported)"
                    class="ml-auto rounded-md border border-border2 px-3 py-1.5 text-xs font-semibold text-text2 hover:bg-bg2">
              {{ locale.t('adminMessenger.unmuteAuthor', 'Снять ограничение') }}
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
          <!-- Ограничение обратившегося. Тикета здесь нет (это чат поддержки, а не
               жалоба), поэтому и гасить нечего: наказание выдаётся по существу разговора. -->
          <button v-if="c.people?.[0] && c.people[0].role !== 'admin'" type="button"
                  @click="c.people[0].muted ? unmute(c.people[0]) : openMute(c.people[0])"
                  class="shrink-0 rounded-md border px-2.5 py-1.5 text-xs font-semibold"
                  :class="c.people[0].muted ? 'border-border2 text-text2 hover:bg-bg2' : 'border-red/40 text-red hover:bg-red/10'">
            {{ c.people[0].muted ? locale.t('adminMessenger.unmuteShort', 'Снять') : locale.t('adminMessenger.muteShort', 'Ограничить') }}
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

    <!-- ── Жалобы на ПРОФИЛИ ─────────────────────────────────────────────────────── -->
    <template v-if="view === 'profiles'">
      <p v-if="loadingUserReports" class="p-4 text-center text-sm text-text3">{{ locale.t('common.loading', 'Загрузка') }}…</p>
      <p v-else-if="loadError" class="rounded-xl border border-red/40 bg-red/10 p-6 text-center text-sm text-red">
        {{ loadError }}
      </p>
      <p v-else-if="!userReports.length" class="rounded-xl border border-border2 bg-card p-6 text-center text-sm text-text3">
        {{ locale.t('adminMessenger.noProfileReports', 'Жалоб на профили нет.') }}
      </p>
      <div v-else class="space-y-3">
        <div v-for="r in userReports" :key="r.id" class="rounded-xl border border-border2 bg-card p-4">
          <div class="mb-3 flex items-start gap-3">
            <Avatar :src="r.reported?.avatar" :name="r.reported?.full_name" :role="r.reported?.role"
                    :color="profilePlate(r.reported?.profile_color)" :size="40" />
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-semibold text-text">{{ r.reported?.full_name || '—' }}</div>
              <div class="truncate text-xs text-text3">{{ userMeta(r.reported) }}</div>
            </div>
            <div class="shrink-0 text-right text-xs text-text3">
              <div>{{ fmt(r.created_at) }}</div>
              <div class="font-semibold text-red">{{ REASON_LABELS[r.reason_code] || r.reason_code }}</div>
            </div>
          </div>

          <!-- СНИМОК поля на момент жалобы. Без него модератор открывает профиль и видит
               уже переписанный текст — а разбирать надо то, на что пожаловались. -->
          <div class="mb-2 rounded-lg border border-border bg-card2 p-3">
            <div class="mb-1 text-[11px] uppercase tracking-wide text-text3">
              {{ locale.t('adminMessenger.snapshotOf', 'На момент жалобы') }} · {{ r.field }}
            </div>
            <img v-if="r.field === 'avatar' || r.field === 'banner'" :src="r.snapshot" alt=""
                 class="max-h-40 rounded-lg object-contain" />
            <p v-else class="whitespace-pre-wrap break-words text-sm text-text">{{ r.snapshot || '—' }}</p>
          </div>
          <p v-if="r.description" class="mb-2 text-sm text-text2">{{ r.description }}</p>

          <div class="flex flex-wrap items-center gap-2">
            <button v-if="r.status === 'open'" type="button" @click="resolveUserReport(r, 'in_review')"
                    class="rounded-md border border-border2 px-3 py-1.5 text-xs text-text2 hover:bg-bg2">{{ locale.t('adminMessenger.markInReview', 'В работу') }}</button>
            <button type="button" @click="resolveUserReport(r, 'resolved')"
                    class="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:bg-accent2">{{ locale.t('adminMessenger.markResolved', 'Решено') }}</button>
            <button type="button" @click="resolveUserReport(r, 'dismissed')"
                    class="rounded-md border border-border2 px-3 py-1.5 text-xs text-text2 hover:bg-bg2">{{ locale.t('adminMessenger.dismiss', 'Отклонить') }}</button>

            <!-- Поле только ОЧИЩАЕТСЯ. Вписать что-либо от имени человека нельзя по
                 построению (сервер принимает лишь список полей) — под текстом стоит его
                 лицо и его фамилия. -->
            <button v-if="r.reported && r.field !== 'profile' && r.field !== 'name'" type="button"
                    @click="clearProfileField(r.reported, r.field === 'banner' ? 'profile_banner' : r.field)"
                    class="rounded-md border border-border2 px-3 py-1.5 text-xs text-text2 hover:bg-bg2">
              {{ locale.t('adminMessenger.clearField', 'Очистить поле') }}
            </button>
            <button v-if="r.reported" type="button" @click="openHistory(r.reported)"
                    class="rounded-md border border-border2 px-3 py-1.5 text-xs text-text2 hover:bg-bg2">
              {{ locale.t('adminMessenger.history', 'История') }}
            </button>
            <button v-if="r.reported && r.reported.role !== 'admin'" type="button"
                    @click="r.reported.muted ? unmute(r.reported) : openMute(r.reported)"
                    class="ml-auto rounded-md border px-3 py-1.5 text-xs font-semibold"
                    :class="r.reported.muted ? 'border-border2 text-text2 hover:bg-bg2' : 'border-red/40 text-red hover:bg-red/10'">
              {{ r.reported.muted ? locale.t('adminMessenger.unmuteShort', 'Снять') : locale.t('adminMessenger.muteShort', 'Ограничить') }}
            </button>
          </div>
        </div>
      </div>
    </template>

    <!-- ── Люди, на которых есть жалоба ──────────────────────────────────────────── -->
    <template v-if="view === 'people'">
      <div class="mb-3 flex gap-2">
        <input v-model="peopleQuery" type="search" @keydown.enter="loadPeople"
               :placeholder="locale.t('adminMessenger.searchPeople', 'Поиск по имени')"
               class="min-w-0 flex-1 rounded-lg border border-border2 bg-card2 px-3 py-2 text-sm text-text" />
        <button type="button" @click="loadPeople"
                class="rounded-lg border border-border2 px-3 py-2 text-sm text-text2 hover:bg-bg2">
          {{ locale.t('common.search', 'Поиск') }}
        </button>
      </div>
      <!-- ⚠️ Здесь НЕ каталог колледжа: сервер отдаёт только тех, на кого есть жалоба или
           действующее ограничение. Поиск работает внутри этого множества и найти
           постороннего не может по построению. -->
      <p class="mb-3 text-xs text-text3">
        {{ locale.t('adminMessenger.peopleHint', 'Только те, на кого поступала жалоба или у кого действует ограничение.') }}
      </p>
      <p v-if="loadingPeople" class="p-4 text-center text-sm text-text3">{{ locale.t('common.loading', 'Загрузка') }}…</p>
      <p v-else-if="loadError" class="rounded-xl border border-red/40 bg-red/10 p-6 text-center text-sm text-red">
        {{ loadError }}
      </p>
      <p v-else-if="!people.length" class="rounded-xl border border-border2 bg-card p-6 text-center text-sm text-text3">
        {{ locale.t('adminMessenger.noPeople', 'Никого нет — жалоб не поступало.') }}
      </p>
      <div v-else class="divide-y divide-border/50 overflow-hidden rounded-xl border border-border2 bg-card">
        <div v-for="u in people" :key="u.id" class="flex items-center gap-3 p-3">
          <Avatar :src="u.avatar" :name="u.full_name" :role="u.role" :color="profilePlate(u.profile_color)" :size="40" />
          <div class="min-w-0 flex-1">
            <div class="truncate text-sm font-semibold text-text">{{ u.full_name }}</div>
            <div class="truncate text-xs text-text3">
              {{ userMeta(u) }}
              <span v-if="u.reports_open" class="text-red">
                · {{ locale.t('adminMessenger.openReports', 'открытых жалоб') }}: {{ u.reports_open }}
              </span>
              <span v-if="u.reports_total"> · {{ locale.t('adminMessenger.totalReports', 'всего') }}: {{ u.reports_total }}</span>
            </div>
            <div v-if="u.muted" class="truncate text-xs text-red">
              {{ locale.t('adminMessenger.restricted', 'Ограничен') }}
              <span v-if="muteLeft(u.muted_until)"> · {{ muteLeft(u.muted_until) }}</span>
              <span v-if="u.mute_reason"> · {{ u.mute_reason }}</span>
            </div>
          </div>
          <button type="button" @click="openHistory(u)"
                  class="shrink-0 rounded-md border border-border2 px-2.5 py-1.5 text-xs text-text2 hover:bg-bg2">
            {{ locale.t('adminMessenger.history', 'История') }}
          </button>
          <button v-if="u.role !== 'admin'" type="button"
                  @click="u.muted ? unmute(u) : openMute(u)"
                  class="shrink-0 rounded-md border px-2.5 py-1.5 text-xs font-semibold"
                  :class="u.muted ? 'border-border2 text-text2 hover:bg-bg2' : 'border-red/40 text-red hover:bg-red/10'">
            {{ u.muted ? locale.t('adminMessenger.unmuteShort', 'Снять') : locale.t('adminMessenger.muteShort', 'Ограничить') }}
          </button>
        </div>
      </div>
    </template>

    <!-- Диалог ограничения со сроком (бессрочных мьютов нет — сервер их не примет). -->
    <MuteDialog :open="muteFor.open" :user="muteFor.user" :report-id="muteFor.reportId"
                :ticket-closed="muteFor.closed" :busy="muteBusy"
                @close="muteFor = { open: false, user: null, reportId: 0, closed: false }"
                @submit="submitMute" />

    <!-- История наказаний: первый это раз или пятый. -->
    <div v-if="historyFor.open" class="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4"
         @click.self="historyFor.open = false">
      <div class="max-h-[80vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border2 bg-card p-5">
        <h3 class="mb-1 font-title text-base font-bold text-text">
          {{ locale.t('adminMessenger.historyOf', 'История') }}: {{ historyFor.user?.full_name }}
        </h3>
        <p v-if="historyFor.loading" class="py-6 text-center text-sm text-text3">{{ locale.t('common.loading', 'Загрузка') }}…</p>
        <template v-else-if="historyFor.data">
          <p class="mb-3 text-xs text-text3">
            {{ locale.t('adminMessenger.reportsOnMessages', 'жалоб на сообщения') }}: {{ historyFor.data.reports_on_messages }} ·
            {{ locale.t('adminMessenger.reportsOnProfile', 'на профиль') }}: {{ historyFor.data.reports_on_profile }}
          </p>
          <!-- ⚠️ Честная граница: журнал ведётся не с начала времён, и пустая история НЕ
               означает «нарушений не было». Пишем это прямо, иначе интерфейс соврёт
               молчанием. -->
          <p v-if="historyFor.data.audit_since" class="mb-3 text-[11px] text-text3">
            {{ locale.t('adminMessenger.auditSince', 'Журнал ведётся с') }} {{ fmt(historyFor.data.audit_since) }}
          </p>
          <p v-if="!historyFor.data.actions?.length" class="py-4 text-center text-sm text-text3">
            {{ locale.t('adminMessenger.noActions', 'Действий модерации не было.') }}
          </p>
          <ul v-else class="space-y-2">
            <li v-for="(a, i) in historyFor.data.actions" :key="i"
                class="rounded-lg border border-border bg-card2 p-2.5 text-xs">
              <div class="flex justify-between gap-2">
                <span class="font-semibold text-text">{{ a.action }}</span>
                <span class="text-text3">{{ fmt(a.at) }}</span>
              </div>
              <div class="text-text3">{{ a.by }} ({{ a.role }})</div>
              <div v-if="a.detail" class="mt-0.5 break-words text-text2">{{ a.detail }}</div>
            </li>
          </ul>
        </template>
        <p v-else class="py-6 text-center text-sm text-text3">{{ locale.t('adminMessenger.loadFailed', 'Не удалось загрузить.') }}</p>
        <div class="mt-4 flex justify-end">
          <button type="button" @click="historyFor.open = false"
                  class="rounded-lg border border-border2 px-3 py-1.5 text-sm text-text2 hover:bg-bg2">
            {{ locale.t('common.close', 'Закрыть') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
