<script setup>
// ChatList — левая колонка навигации (стиль Telegram): поиск по ФИО, вкладки
// «Чаты / Преподаватели / Студенты / Каналы», кнопка «+» (новая группа/канал). Клик по
// чату открывает переписку; по человеку — личный чат; по каналу — вступление/открытие.
import { ref, computed, watch, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { Search, Plus, Users, Radio, Megaphone, Briefcase, Star, Archive, MoreVertical,
         Pin, PinOff, ArchiveRestore, PieChart, UserPlus,
         Bell, BellOff, MailOpen, Eraser, Trash2, Ban } from '@lucide/vue'
import { useMessengerStore } from '@/stores/messenger'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import { useLocaleStore } from '@/stores/locale'
import { roleLabel } from '@/config/roles'
import { profilePlate } from '@/theme/palette'
import { nameDecor } from '@/config/nameEffects'
import { curatorApi, messengerApi } from '@/api/endpoints'
import { messagePreview } from '@/utils/messagePreview'
import CreateChatDialog from './CreateChatDialog.vue'
import GroupPickDialog from './GroupPickDialog.vue'
import CuratorReportDialog from './CuratorReportDialog.vue'
import MyStatusPicker from './MyStatusPicker.vue'
import Avatar from '@/components/ui/Avatar.vue'

const m = useMessengerStore()
const toast = useToast()
const auth = useAuthStore()
const locale = useLocaleStore()
const { chats, invites, dir, channels, activeId, loadingChats } = storeToRefs(m)
// ГРУППЫ студентам открыты (решение Ярослава 28.08.2026): «разрешить студентам делать
// группы между собой». КАНАЛЫ — по-прежнему только преподавателям и админу: канал это
// вещание (один пишет, сотня читает), и такой рупор студенту не даём.
// ⚠️ Родитель не создаёт ничего: каталог не показывает ему ни студентов, ни
// преподавателей, и собирать группу ему не из кого (сервер это тоже проверит).
const canCreateGroup = computed(() => ['student', 'teacher', 'admin'].includes(auth.role))
const canCreateChannel = computed(() => ['teacher', 'admin'].includes(auth.role))
const canCreate = computed(() => canCreateGroup.value || canCreateChannel.value)

// Пока ответ едет, кнопки заявки блокируем: второй клик по «Принять» ушёл бы уже
// на израсходованную заявку и вернул 404, а человек увидел бы отказ на успешное действие.
const answering = ref('')
async function answerInvite(convId, accept) {
  answering.value = convId
  try { await m.answerInvite(convId, accept) } finally { answering.value = '' }
}

const tab = ref('chats')            // chats | teacher | student | channels | archive
const q = ref('')
const showNew = ref(false)
const createKind = ref('')          // '' | group | channel
const menuFor = ref('')             // conversation_id чата, у которого открыто меню ⋮
let debounce = null

function initials(name) {
  const p = (name || '').trim().split(/\s+/)
  return ((p[0]?.[0] || '') + (p[1]?.[0] || '')).toUpperCase() || '?'
}
const BCP47 = { ru: 'ru-RU', en: 'en-US', zh: 'zh-CN' }
function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const today = new Date()
  const tag = BCP47[locale.active] || 'ru-RU'
  return d.toDateString() === today.toDateString()
    ? d.toLocaleTimeString(tag, { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString(tag, { day: '2-digit', month: '2-digit' })
}

// «Чаты» — без архивных (у них отдельная вкладка) и без «Избранного» (оно — отдельной
// закреплённой строкой сверху, как Saved Messages в Telegram, а не в общем списке).
const archivedCount = computed(() => chats.value.filter(c => c.archived).length)
const savedChat = computed(() => chats.value.find(c => c.kind === 'saved') || null)
const shownChats = computed(() => {
  const ql = q.value.trim().toLowerCase()
  const base = chats.value.filter(c => c.kind !== 'saved' &&
    (tab.value === 'archive' ? c.archived : !c.archived))
  return ql ? base.filter(c => (c.title || '').toLowerCase().includes(ql)) : base
})

// §12: вкладка «Родители» — ТОЛЬКО у admin и настоящего куратора (canSearchParents, см. ниже).
const catalogTabs = computed(() => {
  const t = [['chats', locale.t('messenger.tab.chats', 'Чаты')],
             ['teacher', locale.t('messenger.tab.teacher', 'Препод.')],
             ['student', locale.t('messenger.tab.student', 'Студенты')]]
  if (canSearchParents.value) t.push(['parent', locale.t('messenger.tab.parent', 'Родители')])
  t.push(['channels', locale.t('messenger.tab.channels', 'Каналы')])
  return t
})

function refresh() {
  if (['teacher', 'student', 'parent'].includes(tab.value)) m.searchUsers(tab.value, q.value)
  else if (tab.value === 'channels') m.loadChannels(q.value)
}
watch(tab, refresh)
watch(q, () => {
  if (tab.value === 'chats' || tab.value === 'archive') return
  clearTimeout(debounce)
  debounce = setTimeout(refresh, 300)
})

function startCreate(kind) { showNew.value = false; createKind.value = kind }

// Строка предпросмотра в списке. Разбор общий с лентой (utils/messagePreview): служебные
// события здесь показывались сырым шаблоном («user_joined␟admin:admin␟…»).
function preview(c) {
  return messagePreview(c.last_message, { withSender: c.kind !== 'direct' })
    || (c.kind === 'saved' ? locale.t('messenger.savedHint', 'Заметки, ссылки, код себе')
                           : locale.t('messenger.noMessages', 'Нет сообщений'))
}

// §D12: канал «Объявления · Группа» — существующие читатели уже подписаны (студенты
// группы), пост уходит обычным send() дальше в ChatThread. Группу выбирают из списка:
// ручной ввод давал опечатки в именах вида «К75/1», и канал молча не открывался.
const showGroupPick = ref(false)
const dialogError = ref('')
const dialogBusy = ref(false)
// ⚠️ ОДИН диалог выбора группы на оба канала, а назначение хранится отдельно.
// Второй такой же диалог означал бы две копии одного окна, которые разъедутся на первой
// же правке — и заметить это можно будет только открыв оба.
const groupPickFor = ref('announcements')      // announcements | practice

function openAnnouncements() {
  showNew.value = false
  dialogError.value = ''
  groupPickFor.value = 'announcements'
  showGroupPick.value = true
}
// 🔥 Канал практики. Серверная ручка была с самого начала и не имела НИ ОДНОГО
// вызывающего — создать его из веба было нельзя вовсе. Нашлось сверкой контракта
// (`tools/graph_api_bridge.py`), где она лежала в списке «сервер никто не зовёт».
function openPractice() {
  showNew.value = false
  dialogError.value = ''
  groupPickFor.value = 'practice'
  showGroupPick.value = true
}
async function onGroupPicked(group) {
  dialogBusy.value = true
  const err = groupPickFor.value === 'practice'
    ? await m.openPracticeChannel(group)
    : await m.openAnnouncementsChannel(group)
  dialogBusy.value = false
  dialogError.value = err
  if (!err) showGroupPick.value = false
}
// §12: вкладка «Родители» в каталоге мессенджера и пункт «Отчёт для родителей» — ТОЛЬКО
// admin и НАСТОЯЩИЙ куратор (curated_groups непустой, а не просто role==='teacher':
// обычному преподавателю пункт меню открывал бы окно, где всё равно нечего выбрать).
// Обычным преподавателям/студентам родителей в поиске не видно — сервер (messenger.py::
// directory) и так их скрывает, это лишь клиентский гейт вкладки.
const myCuratedGroups = ref([])
if (auth.role === 'teacher') {
  curatorApi.groups().then(r => { myCuratedGroups.value = r.data.groups || [] }).catch(() => {})
}
const canSearchParents = computed(() => auth.role === 'admin' || myCuratedGroups.value.length > 0)
// §12: отчёт — обычное сообщение-кнопка, а не отдельный канал: выбираем группу и
// адресатов (родители в ЛС / беседы), либо не выбираем никого — тогда он ляжет в
// «Избранное», откуда его пересылают.
const showReport = ref(false)
function openCuratorReports() {
  showNew.value = false
  dialogError.value = ''
  showReport.value = true
}
async function onCreateReport({ group, userIds, convIds, channel }) {
  dialogBusy.value = true
  const targets = [...convIds]
  if (channel) {
    //Канал заводим ДО отчёта: если сервер откажет (у группы нет подтверждённых родителей),
    //отчёт не создаём вовсе — иначе он ушёл бы «наполовину», и было бы непонятно куда.
    const { id, error } = await m.ensureReportsChannel(group)
    if (error) { dialogBusy.value = false; dialogError.value = error; return }
    targets.push(id)
  }
  const err = await m.createReport(group, userIds, targets)
  dialogBusy.value = false
  dialogError.value = err
  if (!err) showReport.value = false
}
async function onCreate(payload) {
  const kind = createKind.value
  createKind.value = ''
  if (kind === 'group') await m.createGroup(payload.title, payload.ids, payload.about, payload.classGroups)
  else await m.createChannel(payload.title, payload.ids, payload.isPublic, payload.about)
}

// Меню строки чата. Открывается «⋮» И ПРАВОЙ КНОПКОЙ по самой строке — как в Telegram и
// Discord, откуда пришёл присланный образец. Все пункты ЛИЧНЫЕ: собеседник о них не
// узнаёт (см. docs/MESSENGER-ADDON-PLAN-GPT*.md).
//
// ⚠️ «Открыть в отдельном окне» из образца НЕ реализовано, и это осознанно: у нас одна
// SPA на сайт, телефон и окно программы. В браузере это было бы `window.open`, в
// Capacitor и WebView2 — ничего (второго окна там не бывает), то есть пункт работал бы у
// одной платформы из трёх и молча не работал у двух. Пункт, который иногда ничего не
// делает, хуже отсутствующего.
function toggleMenu(convId) { menuFor.value = menuFor.value === convId ? '' : convId }

// ПКМ по строке: открыть меню ЭТОГО чата, а не родное меню браузера. Родное здесь
// бесполезно («Назад», «Обновить»), а привычка нажимать правой на строку списка — общая
// для всех мессенджеров.
function onRowContext(convId) { menuFor.value = convId }

async function onPin(chat) { menuFor.value = ''; await m.togglePinChat(chat.conversation_id, !chat.pinned) }
async function onArchive(chat) { menuFor.value = ''; await m.toggleArchiveChat(chat.conversation_id, !chat.archived) }
async function onMute(chat) { menuFor.value = ''; await m.muteChatById(chat.conversation_id, !chat.muted) }

// «Пометить непрочитанной» — сервер сдвигает метку прочтения назад (флага «непрочитано»
// у нас нет намеренно: он стал бы вторым источником правды рядом с меткой, по которой
// считаются и счётчик, и галочки у собеседника). См. chats.mark_unread.
async function onUnread(chat) {
  menuFor.value = ''
  await m.markChatUnread(chat.conversation_id)
}

async function onClear(chat) {
  menuFor.value = ''
  if (!window.confirm(locale.t('messenger.confirmClear', 'Очистить историю у себя? Сообщения пропадут только у вас.'))) return
  await m.clearChatById(chat.conversation_id)
}

async function onDelete(chat) {
  menuFor.value = ''
  if (!window.confirm(locale.t('messenger.confirmDelete', 'Удалить чат у себя? У собеседника переписка сохранится.'))) return
  await m.deleteChatById(chat.conversation_id)
}

// Блокировка доступна только в ЛИЧНОМ чате: в группе и канале блокировать нечего —
// человек там пишет всем сразу, и запрет означал бы, что один участник вычёркивает
// другого из учебной беседы (для этого есть модерация).
function peerOf(chat) {
  return chat.kind === 'direct' ? (chat.peer_id || chat.peer?.id || '') : ''
}

async function onBlock(chat) {
  menuFor.value = ''
  const uid = peerOf(chat)
  if (!uid) return
  if (!window.confirm(locale.t('messenger.confirmBlock', 'Заблокировать собеседника? Он не сможет вам писать и не узнает об этом.'))) return
  //⚠️ Молчать нельзя ни в успехе, ни в отказе. Блокировка НИЧЕГО не меняет на экране —
  //чат остаётся на месте, собеседник не уведомляется, — поэтому без подтверждения человек
  //не знает, сработало ли нажатие, и жмёт ещё раз. А проглоченная ошибка выглядела бы
  //ровно так же, как успех.
  try {
    await messengerApi.blockUser(uid, true)
    toast.show(locale.t('peerProfile.blocked', 'Заблокирован. Он об этом не узнает.'))
  } catch {
    toast.show(locale.t('common.error', 'Не получилось'))
  }
}

//Просьба «добавить в группу» из карточки профиля: открываем пикер с уже отмеченным
//человеком. Наблюдение, а не событие, — см. объяснение у `groupDraftPeer` в сторе.
watch(() => m.groupDraftPeer, (peer) => { if (peer) createKind.value = 'group' })

onMounted(() => { m.loadChats() })
</script>

<template>
  <!-- На мобилке при открытом чате прячем список — иначе он (w-full) перекрывает тред, и
       по тапу «ничего не происходит». С sm — обе колонки видны рядом. -->
  <aside class="h-full w-full flex-col border-r border-border bg-card sm:w-80 sm:shrink-0"
         :class="activeId ? 'hidden sm:flex' : 'flex'">
    <!-- Поиск + «Новый» -->
    <div class="shrink-0 border-b border-border p-2.5">
      <div class="flex items-center gap-2">
        <div class="flex flex-1 items-center gap-2 rounded-lg border border-border2 bg-card2 px-3">
          <Search class="size-4 shrink-0 text-text3" />
          <input v-model="q" :placeholder="locale.t('messenger.searchByName', 'Поиск по ФИО…')"
                 class="h-9 min-w-0 flex-1 bg-transparent text-sm text-text outline-none" />
        </div>
        <div v-if="canCreate" class="relative">
          <button type="button" @click="showNew = !showNew" :aria-label="locale.t('messenger.create', 'Создать')"
                  class="grid size-9 place-items-center rounded-lg bg-accent text-white hover:bg-accent2"><Plus class="size-5" /></button>
          <div v-if="showNew" class="absolute right-0 top-full z-20 mt-1 w-44 overflow-hidden rounded-lg border border-border2 bg-card py-1 shadow-card">
            <button type="button" @click="startCreate('group')" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2"><Users class="size-4 text-text3" />{{ locale.t('messenger.newGroup', 'Новая группа') }}</button>
            <button v-if="canCreateChannel" type="button" @click="startCreate('channel')" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2"><Radio class="size-4 text-text3" />{{ locale.t('messenger.newChannel', 'Новый канал') }}</button>
            <!-- §D12: авто-канал «Объявления · Группа» — студенты группы уже читатели. -->
            <button v-if="canCreateChannel" type="button" @click="openAnnouncements" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2"><Megaphone class="size-4 text-text3" />{{ locale.t('messenger.groupAnnouncements', 'Объявления группы') }}</button>
            <!-- §D12(5): «Практика · Группа» — канал НЕ автоматический: данных о практике
                 в журнале нет и выдумывать их нельзя. Его ведёт руками учебная часть. -->
            <button v-if="canCreateChannel" type="button" @click="openPractice" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2"><Briefcase class="size-4 text-text3" />{{ locale.t('messenger.groupPractice', 'Практика группы') }}</button>
            <!-- §12: только куратору — отчёт по успеваемости своей группы для родителей. -->
            <button v-if="canSearchParents" type="button" @click="openCuratorReports" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2"><PieChart class="size-4 text-text3" />{{ locale.t('messenger.parentReport', 'Отчёт для родителей') }}</button>
          </div>
        </div>
      </div>
      <div class="mt-2 flex gap-1">
        <button v-for="t in catalogTabs"
                :key="t[0]" type="button" @click="tab = t[0]"
                class="flex-1 rounded-md px-1.5 py-1.5 text-[11px] font-semibold transition-colors"
                :class="tab === t[0] ? 'bg-accent-glow text-accent' : 'text-text3 hover:bg-bg2 hover:text-text'">
          {{ t[1] }}
        </button>
      </div>
      <!-- §D7: мой статус (поверх presence) — виден другим в каталоге/карточке участника. -->
      <MyStatusPicker class="mt-1" />
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <!-- Чаты (+ архив той же вёрсткой, отдельным «режимом» той же вкладки) -->
      <template v-if="tab === 'chats' || tab === 'archive'">
        <!-- «Избранное» — личный чат с самим собой, как Saved Messages в Telegram: всегда
             сверху, отдельно от общего списка (докладов не бывает). -->
        <button v-if="tab === 'chats'" type="button" @click="m.openSaved()"
                class="flex w-full items-center gap-3 border-b border-border/50 px-3 py-2.5 text-left transition-colors"
                :class="savedChat && activeId === savedChat.conversation_id ? 'bg-accent-glow' : 'hover:bg-bg2'">
          <div class="grid size-10 shrink-0 place-items-center rounded-full bg-accent2 text-white"><Star class="size-5" /></div>
          <div class="min-w-0 flex-1">
            <div class="text-sm font-semibold text-text">{{ locale.t('messenger.saved', 'Избранное') }}</div>
            <div class="truncate text-xs text-text3">{{ savedChat ? preview(savedChat) : locale.t('messenger.savedHint', 'Заметки, ссылки, код себе') }}</div>
          </div>
        </button>
        <button v-if="tab === 'chats' && archivedCount" type="button" @click="tab = 'archive'"
                class="flex w-full items-center gap-3 border-b border-border/50 px-3 py-2.5 text-left transition-colors hover:bg-bg2">
          <div class="grid size-10 shrink-0 place-items-center rounded-full bg-bg2 text-text3"><Archive class="size-5" /></div>
          <div class="min-w-0 flex-1 text-sm font-semibold text-text">{{ locale.t('messenger.archive', 'Архив') }}</div>
          <span class="shrink-0 rounded-full bg-bg2 px-2 py-0.5 text-[11px] font-semibold text-text3">{{ archivedCount }}</span>
        </button>
        <div v-if="tab === 'archive'" class="flex items-center gap-2 border-b border-border/50 px-3 py-2">
          <button type="button" @click="tab = 'chats'" class="text-xs font-semibold text-accent hover:underline">← {{ locale.t('messenger.backToChats', 'Назад к чатам') }}</button>
        </div>

        <!-- ЗАЯВКИ В БЕСЕДУ. Студент позвал преподавателя в свою группу; пока заявка не
             принята, участника НЕТ вовсе, поэтому такая беседа не приходит в /chats и
             показать её больше негде. Держим НАД списком: заявка требует ответа, а
             ниже по списку её просто не заметят и она провисит неделю. -->
        <div v-if="tab === 'chats' && invites.length" class="border-b border-border/50 bg-accent-glow/40">
          <div v-for="inv in invites" :key="inv.conversation_id" class="px-3 py-2.5">
            <div class="flex items-start gap-2">
              <UserPlus class="mt-0.5 size-4 shrink-0 text-accent" />
              <div class="min-w-0 flex-1">
                <div class="truncate text-sm font-semibold text-text">{{ inv.title }}</div>
                <div class="truncate text-[11px] text-text3">
                  {{ locale.t('messenger.invitedBy', { name: inv.invited_by_name }) }}
                  <span v-if="inv.members"> · {{ locale.t('messenger.inviteMembers', { n: inv.members }) }}</span>
                </div>
              </div>
            </div>
            <div class="mt-2 flex gap-2">
              <button type="button" :disabled="answering === inv.conversation_id"
                      @click="answerInvite(inv.conversation_id, true)"
                      class="flex-1 rounded-md bg-accent px-2 py-1.5 text-xs font-semibold text-white hover:bg-accent2 disabled:opacity-50">
                {{ locale.t('messenger.inviteAccept', 'Принять') }}
              </button>
              <button type="button" :disabled="answering === inv.conversation_id"
                      @click="answerInvite(inv.conversation_id, false)"
                      class="flex-1 rounded-md border border-border2 px-2 py-1.5 text-xs font-semibold text-text3 hover:bg-bg2 disabled:opacity-50">
                {{ locale.t('messenger.inviteDecline', 'Отклонить') }}
              </button>
            </div>
          </div>
        </div>

        <p v-if="!loadingChats && !shownChats.length && !invites.length" class="p-4 text-center text-sm text-text3">
          {{ tab === 'archive' ? locale.t('messenger.archiveEmpty', 'В архиве пусто.') : locale.t('messenger.noChatsYet', 'Пока нет переписок. Найдите человека через поиск') }}<span v-if="canCreate && tab === 'chats'"> {{ canCreateChannel ? locale.t('messenger.orCreateHint', 'или создайте группу/канал кнопкой «+»') : locale.t('messenger.orCreateGroupHint', 'или соберите группу кнопкой «+»') }}</span>.
        </p>
        <!-- ПКМ по строке открывает то же меню, что «⋮». Родное меню браузера здесь
             бесполезно («Назад», «Обновить»), а привычка нажимать правой на строку
             списка — общая для всех мессенджеров, откуда и пришёл образец. -->
        <div v-for="c in shownChats" :key="c.conversation_id"
             @contextmenu.prevent="onRowContext(c.conversation_id)"
             class="group relative flex w-full items-center gap-3 border-b border-border/50 px-3 py-2.5 transition-colors"
             :class="activeId === c.conversation_id ? 'bg-accent-glow' : 'hover:bg-bg2'">
          <button type="button" @click="m.selectChat(c)" class="flex min-w-0 flex-1 items-center gap-3 text-left">
            <!-- Личный чат — аватар собеседника; «Модерация» — та же Avatar, но со
                 значком role="moderation" (щит), сервер её НЕ отдаёт как обычного
                 peer'а — kind='moderation' это служебная беседа с администрацией
                 целиком, а не переписка с чьим-то аккаунтом, см. RoleAvatarIcon.vue;
                 группа/канал — цветной кружок с инициалами, как раньше. -->
            <Avatar v-if="c.kind === 'direct'" :src="c.peer?.avatar" :name="c.title"
                    :role="c.peer?.role" :color="profilePlate(c.peer?.profile_color)"
                    :online="!!c.peer?.online" :size="40" />
            <Avatar v-else-if="c.kind === 'moderation'" :name="c.title" role="moderation" :size="40" />
            <!-- «Избранное» и системные каналы («Мои оценки», «Расписание · Группа»,
                 «Объявления») ведёт ВЕКТОР — показываем его лицо, а не кружок с
                 инициалами. Инициалы там читались как чужой аккаунт («МО», «РГ»), хотя
                 собеседника у этих бесед нет вовсе: пишет в них продукт.
                 ⚠️ `is_system` приходит С СЕРВЕРА (см. chats.py::list_chats), а не
                 выводится из формата id: разбирать `sys:*:{группа}` в браузере значило бы
                 завести второй источник правды о том, что такое системный канал. -->
            <img v-else-if="c.kind === 'saved' || c.is_system" src="/mascot/vector-avatar.webp"
                 alt="" width="40" height="40" loading="lazy" decoding="async"
                 class="size-10 shrink-0 rounded-full bg-bg2 object-cover" />
            <!-- Группа/канал: своя аватарка, если её поставили (02.09.2026, просьба
                 Влада). Не поставили — прежний цветной кружок с инициалами.
                 ⚠️ Порядок именно такой: системные каналы выше по списку ветвлений, и их
                 лицо задаём МЫ (Вектор) — своя картинка там не предусмотрена вовсе. -->
            <img v-else-if="c.avatar" :src="c.avatar" alt="" width="40" height="40"
                 loading="lazy" decoding="async"
                 class="size-10 shrink-0 rounded-full bg-bg2 object-cover" />
            <div v-else class="grid size-10 shrink-0 place-items-center rounded-full text-sm font-bold text-white"
                 :class="c.kind === 'channel' ? 'bg-accent2' : 'bg-blue'">
              {{ initials(c.title) }}
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center justify-between gap-2">
                <span class="flex items-center gap-1 truncate text-sm font-semibold text-text"
                      v-bind="c.kind === 'direct' ? nameDecor(c.peer || {}) : {}">
                  <Pin v-if="c.pinned" class="size-3 shrink-0 text-text3" />{{ c.title || locale.t('messenger.dialog', 'Диалог') }}
                </span>
                <span class="shrink-0 text-[11px] text-text3">{{ fmtTime(c.last_at) }}</span>
              </div>
              <div class="flex items-center justify-between gap-2">
                <span class="min-w-0 truncate text-xs text-text3">
                  {{ preview(c) }}
                </span>
                <!-- Меня отметили в непрочитанном — вместо ЧИСЛА сообщений показываем «@».
                     Число говорит «сколько тут болтали», «@» — «обращались лично к тебе»,
                     и второе важнее: именно оно решает, открывать чат сейчас или потом.
                     Громкая отметка (/@!) выделена цветом — она и звонила. -->
                <span v-if="c.mention_message_id"
                      :title="c.mention_loud ? locale.t('messenger.mentionedLoud', 'Вас отметили (со звуком)') : locale.t('messenger.mentioned', 'Вас отметили')"
                      class="grid h-5 min-w-5 shrink-0 place-items-center rounded-full px-1.5 text-[11px] font-bold text-white"
                      :class="c.mention_loud ? 'bg-red' : 'bg-accent'">@</span>
                <span v-else-if="c.unread" class="grid h-5 min-w-5 shrink-0 place-items-center rounded-full bg-accent px-1.5 text-[11px] font-bold text-white">{{ c.unread }}</span>
              </div>
            </div>
          </button>
          <div class="relative shrink-0">
            <button type="button" @click.stop="toggleMenu(c.conversation_id)" :aria-label="locale.t('messenger.actions', 'Действия')"
                    class="grid size-7 place-items-center rounded-md text-text3 opacity-0 hover:bg-bg2 hover:text-text group-hover:opacity-100"
                    :class="{ 'opacity-100 bg-bg2': menuFor === c.conversation_id }">
              <MoreVertical class="size-4" />
            </button>
            <!-- Подложка-ловушка: без неё меню закрывается только повторным нажатием на
                 «⋮», и промахнувшийся мимо пункта остаётся с открытым меню. -->
            <div v-if="menuFor === c.conversation_id" class="fixed inset-0 z-10" @click.stop="menuFor = ''" />
            <div v-if="menuFor === c.conversation_id"
                 class="absolute right-0 top-full z-20 mt-1 w-56 overflow-hidden rounded-lg border border-border2 bg-card py-1 shadow-card">
              <button type="button" @click.stop="onArchive(c)" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2">
                <component :is="c.archived ? ArchiveRestore : Archive" class="size-4 text-text3" />{{ c.archived ? locale.t('messenger.unarchive', 'Из архива') : locale.t('messenger.archiveAction', 'В архив') }}
              </button>
              <button type="button" @click.stop="onPin(c)" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2">
                <component :is="c.pinned ? PinOff : Pin" class="size-4 text-text3" />{{ c.pinned ? locale.t('messenger.unpin', 'Открепить') : locale.t('messenger.pin', 'Закрепить') }}
              </button>
              <button type="button" @click.stop="onMute(c)" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2">
                <component :is="c.muted ? Bell : BellOff" class="size-4 text-text3" />{{ c.muted ? locale.t('messenger.unmuteChat', 'Включить уведомления') : locale.t('messenger.muteChat', 'Выключить уведомления') }}
              </button>
              <button type="button" @click.stop="onUnread(c)" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2">
                <MailOpen class="size-4 text-text3" />{{ locale.t('messenger.markUnread', 'Пометить непрочитанным') }}
              </button>

              <div class="my-1 h-px bg-border2" />

              <button type="button" @click.stop="onClear(c)" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2">
                <Eraser class="size-4 text-text3" />{{ locale.t('messenger.clearHistory', 'Очистить историю') }}
              </button>
              <!-- Разрушительное — внизу и цветом: промахнувшийся мимо «Очистить» не
                   должен попасть в «Удалить». -->
              <button v-if="c.kind !== 'saved'" type="button" @click.stop="onDelete(c)"
                      class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red hover:bg-bg2">
                <Trash2 class="size-4" />{{ locale.t('messenger.deleteChat', 'Удалить чат') }}
              </button>
              <button v-if="peerOf(c)" type="button" @click.stop="onBlock(c)"
                      class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red hover:bg-bg2">
                <Ban class="size-4" />{{ locale.t('messenger.block', 'Заблокировать') }}
              </button>
            </div>
          </div>
        </div>
      </template>

      <!-- Каталог людей -->
      <template v-else-if="tab === 'teacher' || tab === 'student' || tab === 'parent'">
        <p v-if="dir.loading" class="p-4 text-center text-sm text-text3">{{ locale.t('common.search', 'Поиск') }}…</p>
        <p v-else-if="!dir.users.length" class="p-4 text-center text-sm text-text3">{{ locale.t('messenger.nobodyFound', 'Никого не найдено.') }}</p>
        <button v-for="u in dir.users" :key="u.id" type="button" @click="m.openWith(u)"
                class="flex w-full items-center gap-3 border-b border-border/50 px-3 py-2.5 text-left transition-colors hover:bg-bg2">
          <Avatar :src="u.avatar" :name="u.full_name" :role="u.role" :color="profilePlate(u.profile_color)" :online="!!u.online" :size="40" />
          <div class="min-w-0 flex-1">
            <div class="truncate text-sm font-semibold text-text">{{ u.full_name }}</div>
            <div class="truncate text-xs text-text3">
              <template v-if="u.role === 'teacher'">{{ (u.subjects || []).join(', ') || roleLabel('teacher') }}</template>
              <template v-else-if="u.role === 'parent'">{{ u.groups?.length ? locale.t('messenger.parentOf', 'род. ') + u.groups.join(', ') : roleLabel('parent') }}</template>
              <template v-else>{{ locale.t('messenger.groupLabel', 'Группа') }} {{ u.group_name || '—' }}</template>
            </div>
          </div>
        </button>
      </template>

      <!-- Каталог каналов -->
      <template v-else>
        <p v-if="!channels.length" class="p-4 text-center text-sm text-text3">{{ locale.t('messenger.noPublicChannels', 'Публичных каналов пока нет.') }}</p>
        <button v-for="ch in channels" :key="ch.conversation_id" type="button" @click="m.joinChannel(ch.conversation_id)"
                class="flex w-full items-center gap-3 border-b border-border/50 px-3 py-2.5 text-left transition-colors hover:bg-bg2">
          <div class="grid size-10 shrink-0 place-items-center rounded-full bg-accent2 text-sm font-bold text-white">{{ initials(ch.title) }}</div>
          <div class="min-w-0 flex-1">
            <div class="truncate text-sm font-semibold text-text">{{ ch.title }}</div>
            <div class="truncate text-xs text-text3">{{ locale.t('messenger.subscribersCount', { n: ch.subscribers }) }} · {{ ch.about || locale.t('messenger.channelWord', 'канал') }}</div>
          </div>
          <span class="shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold"
                :class="ch.joined ? 'bg-bg2 text-text3' : 'bg-accent-glow text-accent'">
            {{ ch.joined ? locale.t('messenger.open', 'Открыть') : locale.t('messenger.join', 'Присоединиться') }}
          </span>
        </button>
      </template>
    </div>

    <CreateChatDialog v-if="createKind" :kind="createKind" :preset="m.groupDraftPeer"
                      @create="onCreate" @close="createKind = ''; m.clearGroupDraft()" />
    <GroupPickDialog v-if="showGroupPick"
                     :title="groupPickFor === 'practice'
                       ? locale.t('messenger.groupPractice', 'Практика группы')
                       : locale.t('messenger.groupAnnouncements', 'Объявления группы')"
                     :submit-label="locale.t('messenger.openChannel', 'Открыть канал')"
                     :hint="groupPickFor === 'practice'
                       ? locale.t('messenger.practiceHint', 'Канал «Практика · Группа»: направления, договоры, сроки сдачи дневника. Студенты группы уже подписаны.')
                       : locale.t('messenger.announcementsHint', 'Канал «Объявления · Группа». Студенты группы уже подписаны.')"
                     :error="dialogError" :busy="dialogBusy"
                     @pick="onGroupPicked" @close="showGroupPick = false" />
    <CuratorReportDialog v-if="showReport" :error="dialogError" :busy="dialogBusy"
                         @create="onCreateReport" @close="showReport = false" />
  </aside>
</template>
