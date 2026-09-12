<script setup>
// ConversationInfo — модалка «О беседе»: описание, ВЛАДЕЛЕЦ и список участников с
// аватарками и ролями. Открывается кликом по шапке чата (как в Telegram), поэтому
// доступна на ЛЮБОЙ ширине — боковая ProfilePanel показывается только с xl и на
// десктопе/телефоне была не видна.
// Здесь же — выход из группы/канала и удаление переписки у себя.
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { X, Crown, Trash2, LogOut, Users, ShieldCheck, Pencil, Check, MoreVertical,
         UserPlus, Bell, BellOff, Search, Image, Star, FileText, ScrollText, Camera } from '@lucide/vue'
import AvatarCropper from '@/components/AvatarCropper.vue'
import { useMessengerStore } from '@/stores/messenger'
import { useToast } from '@/composables/useToast'
import Avatar from '@/components/ui/Avatar.vue'
import { messengerApi } from '@/api/endpoints'
import { humanSize } from '@/utils/docPreview'
import RoleManagerDialog from '@/components/messenger/RoleManagerDialog.vue'
import AddMembersDialog from '@/components/messenger/AddMembersDialog.vue'
import PeerProfileModal from '@/components/messenger/PeerProfileModal.vue'
import PeerProfileCard from '@/components/messenger/PeerProfileCard.vue'
import SharedGroupsChannels from '@/components/messenger/SharedGroupsChannels.vue'
import { statusLabel as sharedStatusLabel, statusColor as sharedStatusColor } from '@/config/status'
import { roleLabel as sharedRoleLabel } from '@/config/roles'
import { profilePlate } from '@/theme/palette'
import { useLocaleStore } from '@/stores/locale'

const emit = defineEmits(['close', 'search', 'summary', 'open-file'])
const m = useMessengerStore()
const toast = useToast()
const locale = useLocaleStore()
const { activeInfo, activePeer, isModeration, activeKind, activeChat } = storeToRefs(m)

// §ролей: права звонящего в ЭТОЙ беседе + личный игнор-лист + мой user_id — всё уже
// приезжает в conversation_info (см. routers/messenger.py::conversation_info). Свой id
// клиент иначе не знает (в JWT/сторе только логин+роль, см. комментарий у _msg_out).
const myPermissions = computed(() => new Set(activeInfo.value?.my_permissions || []))
const myIgnoredIds = computed(() => new Set(activeInfo.value?.my_ignored_user_ids || []))
const myUserId = computed(() => activeInfo.value?.my_user_id || '')
const canKick = computed(() => myPermissions.value.has('kick'))
const canManageRoles = computed(() => myPermissions.value.has('manage_roles'))
// 🔥 ДОБАВЛЕНИЕ УЧАСТНИКОВ. Право то же, что у кика (`kick`): кто может выгнать —
// может и позвать. Отдельного права не заводим — набор прав у нас намеренно узкий
// (см. `_ALL_PERMISSIONS`), а сервер всё равно проверяет `_require_manager`.
const addOpen = ref(false)
const addBusy = ref(false)
const canAdd = computed(() => activeKind.value !== 'direct'
  && activeKind.value !== 'saved'
  && (myPermissions.value.has('kick') || myPermissions.value.has('manage_roles')))
const existingIds = computed(() => (activeInfo.value?.participants || []).map((p) => p.user_id))

async function doAddMembers({ userIds, classGroups }) {
  addBusy.value = true
  const added = await m.addMembers(userIds, classGroups)
  addBusy.value = false
  addOpen.value = false
  // ⚠️ Сообщаем ЧИСЛО, а не «готово»: сервер молча пропускает тех, кто уже в беседе,
  // и бодрое «добавлено» при нуле заставило бы человека думать, что люди в чате.
  toast(added
    ? locale.t('members.added', { n: added })
    : locale.t('members.addedNobody', 'Никого не добавили — возможно, они уже в беседе'))
}

// ━━ ПАНЕЛЬ БЕСЕДЫ (переделка 25.08.2026 по образцу Telegram, просьба Влада) ━━
//
// Шапка (аватар, имя, описание) → три кнопки (звук/поиск/ещё) → вкладки. Поиск и
// сводка ЖИЛИ В ЛЕНТЕ и переехали сюда — но код их не переехал: панель только шлёт
// наверх событие, а открывает по-прежнему `ChatThread`. Вторая копия поиска разошлась
// бы с первой, и разошлась бы молча.
const tab = ref('members')            // members | media | saved | files
const moreOpen = ref(false)
const media = ref([])
const files = ref([])
const savedItems = ref([])
const tabLoading = ref(false)

const muted = computed(() => !!activeChat.value?.muted)

/** Звук: гасит пуши беседы, громкие отметки И напоминания-таймеры (см. routers/me.py). */
async function toggleMute() {
  await m.muteConversation(!muted.value)
}

// ⚠️ Содержимое вкладки грузим ПРИ ОТКРЫТИИ, а не заранее: три лишних запроса на каждое
// открытие панели ради вкладок, в которые чаще всего не заходят, — плохая сделка на
// одноядерном сервере.
async function openTab(name) {
  tab.value = name
  if (name === 'members') return
  const cache = { media, files, saved: savedItems }[name]
  if (cache.value.length) return
  tabLoading.value = true
  try {
    if (name === 'media') media.value = (await messengerApi.chatMedia(m.activeId)).data.media || []
    if (name === 'files') files.value = (await messengerApi.chatFiles(m.activeId)).data.files || []
    if (name === 'saved') savedItems.value = (await messengerApi.chatSaved(m.activeId)).data.saved || []
  } catch { /* пусто — покажем «ничего нет», это честнее ошибки */ }
  finally { tabLoading.value = false }
}

const BCP47 = { ru: 'ru-RU', en: 'en-US', zh: 'zh-CN' }

/**
 * Время отправки сохранённого сообщения.
 *
 * ⚠️ Здесь НЕЛЬЗЯ показывать одно только «ЧЧ:ММ», как в ленте: лента идёт по порядку и
 * разбита разделителями дат, а «Избранное» — выжимка за всё время беседы, и «22:53» без
 * даты не отвечает ни на один вопрос. Сегодняшнее показываем часами (дата очевидна),
 * остальное — с датой.
 */
function savedTime(it) {
  const d = new Date(it.sent_at || '')
  if (Number.isNaN(d.getTime())) return ''
  const loc = BCP47[locale.active] || 'ru-RU'
  const now = new Date()
  const sameDay = d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
  return sameDay
    ? d.toLocaleTimeString(loc, { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString(loc, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const tabDefs = computed(() => [
  ['members', locale.t('profilePanel.participants', 'Участники'), Users],
  ['media', locale.t('conversationInfo.media', 'Медиа'), Image],
  ['saved', locale.t('conversationInfo.saved', 'Избранное'), Star],
  ['files', locale.t('conversationInfo.files', 'Файлы'), FileText],
])

const openMenuFor = ref(null)
const roleDialogFor = ref(null)
// §5.4: клик по аватарке/имени — открыть Discord-style карточку профиля поверх этой
// модалки (в неё же вынесена заметка и кнопка «Сообщение», которых в этом плотном
// списке участников попросту нет места показать).
const profileFor = ref(null)

function toggleMenu(uid) { openMenuFor.value = openMenuFor.value === uid ? null : uid }
// Клик вне меню участника — закрыть (нет глобальной v-click-outside директивы в проекте,
// делаем локально одним слушателем на весь компонент).
function onDocClick(e) {
  if (openMenuFor.value && !e.target.closest('[data-role-menu]')) openMenuFor.value = null
}
onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))

async function kick(p) {
  openMenuFor.value = null
  if (!window.confirm(locale.t('conversationInfo.confirmKick', { name: p.full_name }))) return
  const ok = await m.kickMember(p.user_id)
  if (!ok) toast.error(locale.t('conversationInfo.kickFailed', 'Не удалось выгнать участника'))
}
async function toggleIgnore(p) {
  openMenuFor.value = null
  const ok = await m.toggleIgnore(p.user_id, myIgnoredIds.value.has(p.user_id))
  if (!ok) toast.error(locale.t('conversationInfo.ignoreFailed', 'Не удалось изменить игнор'))
}
function openRoleDialog(p) {
  openMenuFor.value = null
  roleDialogFor.value = p
}

const kind = computed(() => activeKind.value)
const isGroupOrChannel = computed(() => ['group', 'channel'].includes(kind.value))
const isSaved = computed(() => kind.value === 'saved')
const people = computed(() => activeInfo.value?.participants || [])
const ownerId = computed(() => activeInfo.value?.owner_id || '')
// ЛС с реальным собеседником — карточка профиля ТЕПЕРЬ полноценная (3.6.1), с правой
// колонкой «Общие группы/каналы» по аналогии с PeerProfileModal — модалке нужно больше
// места. Остальные случаи (список участников группы/канала, «Избранное», модерация)
// остаются в прежней узкой ширине.
const wide = computed(() => !isGroupOrChannel.value && !isSaved.value && !isModeration.value && !!activePeer.value)

const KIND_RU = computed(() => ({
  group: locale.t('messenger.groupLabel', 'Группа'),
  channel: locale.t('messenger.channelWord', 'Канал'),
  moderation: locale.t('nav.moderation', 'Модерация'),
  direct: locale.t('conversationInfo.directChat', 'Личный чат'),
}))
const ROLE_RU = computed(() => ({
  owner: locale.t('conversationRole.owner', 'владелец'),
  admin: locale.t('conversationRole.admin', 'админ'),
  writer: locale.t('conversationRole.writer', 'автор'),
  member: locale.t('conversationRole.member', 'участник'),
  reader: locale.t('conversationRole.reader', 'читатель'),
}))

// §D7: статус поверх presence. Сервер отдаёт его и в карточке собеседника (_safe_user),
// и у каждого участника (conversation_info) — но панель их не показывала вовсе, и со
// стороны это читалось как «смена статуса не работает»: человек его выбрал, а никто не
// видит. Общий словарь/цвета — @/config/status (те же, что в MyStatusPicker).
function statusLabel(p) {
  return sharedStatusLabel(p?.status_kind, p?.status_text)
}
function statusColor(p) {
  return sharedStatusColor(p?.status_kind)
}

// Подпись под именем: у преподавателя — предметы, у студента — группа.
function meta(p) {
  if (p.user_role === 'teacher') return (p.subjects || []).join(', ') || sharedRoleLabel('teacher')
  if (p.user_role === 'student') return p.group_name ? locale.t('conversationInfo.groupOf', { group: p.group_name }) : sharedRoleLabel('student')
  return p.user_role === 'admin' ? sharedRoleLabel('admin') : ''
}


const title = computed(() => {
  if (isSaved.value) return locale.t('messenger.saved', 'Избранное')
  if (isModeration.value) return locale.t('nav.moderation', 'Модерация')
  if (isGroupOrChannel.value) return activeInfo.value?.title || locale.t('messenger.dialog', 'Беседа')
  return activePeer.value?.full_name || locale.t('conversationInfo.dialog', 'Диалог')
})

// §D6: переименование группы/канала — только owner/admin.
const canRename = computed(() => isGroupOrChannel.value && ['owner', 'admin'].includes(activeInfo.value?.my_role))
const editing = ref(false)
const titleDraft = ref('')
const aboutDraft = ref('')
function startEdit() {
  titleDraft.value = activeInfo.value?.title || ''
  aboutDraft.value = activeInfo.value?.about || ''
  editing.value = true
}
async function saveEdit() {
  const t = titleDraft.value.trim()
  if (!t) return
  if (await m.renameActive(t, aboutDraft.value.trim())) editing.value = false
}

// ── Аватарка группы/канала (02.09.2026, просьба Влада) ─────────────────────────────
// Право то же, что у переименования: и то и другое меняет ЛИЦО беседы для всех
// участников. Заводить для картинки отдельное право значило бы объяснять человеку
// разницу, которой нет.
// ⚠️ Редактор — ТОТ ЖЕ `AvatarCropper`, что у профиля: он уже умеет обрезку, сжатие в
// data:URL и удаление (пустая строка). Своя копия здесь означала бы второй формат
// картинки, а проверку на сервере проходит ровно один.
const avatarEditor = ref(false)
async function saveAvatar(dataUrl) {
  await m.setActiveAvatar(dataUrl)
  avatarEditor.value = false
}

async function leave() {
  const what = kind.value === 'channel' ? locale.t('messenger.channelWord', 'канала').toLowerCase() : locale.t('messenger.groupLabel', 'группы').toLowerCase()
  if (!window.confirm(locale.t('conversationInfo.confirmLeave', { what }))) return
  await m.leaveActive()
  emit('close')
}

async function removeChat() {
  const what = isGroupOrChannel.value
    ? locale.t('conversationInfo.confirmDeleteGroup', 'Удалить беседу у себя? Вы также выйдете из неё.')
    : locale.t('conversationInfo.confirmDeleteDirect', 'Удалить переписку у себя? У собеседника она сохранится.')
  if (!window.confirm(what)) return
  await m.deleteConversation(false)
  emit('close')
}

async function clearHistory() {
  if (!window.confirm(locale.t('conversationInfo.confirmClear', 'Очистить историю у себя? Сообщения пропадут только у вас.'))) return
  await m.deleteConversation(true)
  emit('close')
}

// Действия над чатом, запрошенные из трёх точек карточки профиля.
//
// 🔑 КАРТОЧКА ИХ НЕ ВЫПОЛНЯЕТ САМА, и это не церемония: её показывают и из каталога, где
// беседы ещё нет вовсе, и из группы, где «удалить переписку» означало бы совсем другое.
// Знает текущую беседу вызывающий — он и делает. Вторая реализация внутри карточки
// разошлась бы с этой молча (наш класс «правило одно, реализаций две»).
function onChatAction(action) {
  if (action === 'clear-history') return clearHistory()
  if (action === 'delete-chat') return removeChat()
  if (action === 'add-to-group') {
    //Пикер участников живёт в CreateChatDialog, и открывает его СПИСОК ЧАТОВ — другая
    //ветка дерева. Просьба уходит через стор: дублировать диалог ради одной кнопки
    //незачем, а событие пришлось бы протащить через четыре компонента.
    m.askAddToGroup(activePeer.value)
    emit('close')
  }
}
</script>

<template>
  <div class="fixed inset-0 z-50 grid place-items-center p-4" style="background: var(--gb-overlay)"
       @click.self="emit('close')">
    <div class="flex max-h-[85vh] w-full flex-col rounded-xl border border-border2 bg-card shadow-card"
         :class="wide ? 'max-w-3xl' : 'max-w-md'">
      <!-- Шапка -->
      <div class="flex items-center gap-2 border-b border-border p-4">
        <!-- Лицо беседы. У группы/канала это АВАТАРКА (её ставит owner/admin), у личного
             чата и модерации — значок типа: там лицо задаёт собеседник, и своя картинка
             поверх означала бы, что человек выглядит по-разному у разных собеседников. -->
        <button v-if="isGroupOrChannel" type="button" :disabled="!canRename"
                @click="avatarEditor = true"
                class="group relative grid size-10 shrink-0 place-items-center rounded-full"
                :class="canRename ? 'cursor-pointer' : 'cursor-default'"
                :title="canRename ? locale.t('conversationInfo.changeAvatar', 'Сменить аватарку беседы') : ''"
                :aria-label="locale.t('conversationInfo.changeAvatar', 'Сменить аватарку беседы')">
          <Avatar :src="activeInfo?.avatar || ''" :name="title" :size="40" />
          <span v-if="canRename"
                class="absolute inset-0 grid place-items-center rounded-full bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
            <Camera class="size-4 text-white" />
          </span>
        </button>
        <component v-else :is="isModeration ? ShieldCheck : Users"
                   class="size-5 shrink-0 text-accent" />
        <div class="min-w-0 flex-1">
          <h3 class="truncate font-title text-base font-bold text-text">{{ title }}</h3>
          <p class="text-xs text-text3">
            {{ KIND_RU[kind] || locale.t('messenger.dialog', 'Беседа') }}
            <span v-if="isGroupOrChannel"> · {{ activeInfo?.subscribers || 0 }}
              {{ kind === 'channel' ? locale.t('conversationInfo.subscribersWord', 'подписчиков') : locale.t('conversationInfo.membersWord', 'участников') }}</span>
          </p>
        </div>
        <!-- §D6: переименовать (owner/admin) -->
        <button v-if="canRename && !editing" type="button" @click="startEdit" :aria-label="locale.t('conversationInfo.rename', 'Переименовать')"
                class="grid size-8 shrink-0 place-items-center rounded-md text-text3 hover:bg-bg2 hover:text-text">
          <Pencil class="size-4" />
        </button>
        <button type="button" @click="emit('close')" :aria-label="locale.t('common.close')"
                class="grid size-8 shrink-0 place-items-center rounded-md text-text3 hover:bg-bg2 hover:text-text">
          <X class="size-5" />
        </button>
      </div>

      <!-- ЛС с реальным собеседником — flex-строка: карточка профиля СЛЕВА (скроллится
           отдельно), «Общие группы/каналы» СПРАВА (§5.4, 3.6.1 — по аналогии с
           PeerProfileModal, заказчик прямо просил). Остальные случаи (переименование,
           «Избранное», модерация, список участников группы/канала) — как раньше, в
           обычной колонке с отступом p-4. -->
      <div v-if="wide" class="flex min-h-0 flex-1">
        <div class="min-w-0 flex-1 overflow-y-auto p-4">
          <!-- §5.4 (живой отзыв 3.6.1): в ЛС карточка собеседника ЭТО ЖЕ полноценная
               Discord-style PeerProfileCard (баннер/аватар/«о себе»/заметка), а не её
               урезанная копия за вторым кликом — та версия оставалась только для
               УЧАСТНИКОВ ГРУППЫ/КАНАЛА ниже (там «очистить историю»/«удалить
               переписку» этого конкретного человека не имеют смысла, а здесь беседа и
               есть личный чат с ним же). -->
          <PeerProfileCard :peer-data="activePeer" @messaged="emit('close')"
                           @chat-action="onChatAction" />
          <!-- ЛС с администратором — другой контекст переписки, поясняем границы. -->
          <div v-if="activePeer.role === 'admin'" class="mt-3 rounded-lg border border-border bg-card2 p-3 text-sm text-text2">
            <p class="mb-1 text-[11px] uppercase tracking-wide text-text3">{{ locale.t('profilePanel.notHere', 'Лучше не сюда') }}</p>
            {{ locale.t('conversationInfo.notHereShort', 'Жалобы на пользователей — через «Модерация» (кнопка ⚙ у чата). Учебные вопросы (оценки, расписание) — своему куратору или преподавателю.') }}
          </div>
        </div>
        <SharedGroupsChannels :user-id="activePeer.id" class="hidden sm:block" @navigate="emit('close')" />
      </div>

      <div v-else class="min-h-0 flex-1 overflow-y-auto p-4">
        <!-- §D6: форма переименования -->
        <div v-if="editing" class="mb-4 space-y-2 rounded-lg border border-border2 bg-card2 p-3">
          <input v-model="titleDraft" :placeholder="locale.t('conversationInfo.namePlaceholder', 'Название')" maxlength="120"
                 class="w-full rounded-md border border-border2 bg-card px-2.5 py-1.5 text-sm text-text outline-none focus:border-accent" />
          <textarea v-model="aboutDraft" :placeholder="locale.t('createChat.descOptional', 'Описание (необязательно)')" rows="2" maxlength="500"
                    class="w-full resize-none rounded-md border border-border2 bg-card px-2.5 py-1.5 text-sm text-text outline-none focus:border-accent" />
          <div class="flex justify-end gap-2">
            <button type="button" @click="editing = false" class="rounded-md px-3 py-1.5 text-xs text-text3 hover:bg-bg2">{{ locale.t('common.cancel') }}</button>
            <button type="button" @click="saveEdit" :disabled="!titleDraft.trim()"
                    class="flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:bg-accent2 disabled:opacity-50">
              <Check class="size-3.5" />{{ locale.t('common.save') }}
            </button>
          </div>
        </div>
        <p v-if="activeInfo?.about" class="mb-4 text-sm text-text2">{{ activeInfo.about }}</p>

        <!-- В «Избранном» карточки «собеседника» нет: это ты сам. -->
        <p v-if="isSaved" class="text-sm text-text2">
          {{ locale.t('conversationInfo.savedHint1', 'Личные заметки: ссылки, файлы и мысли для себя. Здесь же работает команда') }}
          <span class="font-semibold text-accent">/vector</span> {{ locale.t('conversationInfo.savedHint2', '— спросить ИИ-помощника.') }}
        </p>
        <!-- Модерация раньше рендерилась как обычная карточка собеседника (activePeer =
             {full_name:'Модерация', role:'moderation'}) — тоже падала в «Студент». -->
        <div v-else-if="isModeration" class="rounded-lg border border-border bg-card2 p-4 text-sm text-text2">
          <div class="mb-2 flex items-center gap-2 text-text">
            <ShieldCheck class="size-5 text-accent" /><span class="font-semibold">{{ locale.t('nav.moderation', 'Модерация') }}</span>
          </div>
          {{ locale.t('conversationInfo.modText', 'Сюда можно написать о проблеме: жалоба на пользователя, технический вопрос, нарушение правил. Переписка может быть просмотрена модерацией в целях безопасности.') }}
        </div>

        <!-- Группа/канал: панель в стиле Telegram — кнопки, вкладки, содержимое -->
        <template v-else-if="isGroupOrChannel">

          <!-- ТРИ КНОПКИ. Поиск и сводка переехали сюда из ленты (просьба Влада):
               в шапке чата они конкурировали за место с названием беседы, а тут у них
               своя строка и понятная компания. Открывает их по-прежнему лента — панель
               только сообщает о нажатии, чтобы не завести вторую копию поиска. -->
          <div class="mb-3 grid grid-cols-3 gap-2">
            <button type="button" @click="toggleMute"
                    class="flex flex-col items-center gap-1 rounded-xl bg-card2 py-2.5 text-xs transition-colors hover:bg-bg2"
                    :class="muted ? 'text-text3' : 'text-accent'">
              <component :is="muted ? BellOff : Bell" class="size-5" />
              {{ muted ? locale.t('conversationInfo.muted', 'без звука') : locale.t('conversationInfo.sound', 'звук') }}
            </button>
            <button type="button" @click="emit('search')"
                    class="flex flex-col items-center gap-1 rounded-xl bg-card2 py-2.5 text-xs text-accent transition-colors hover:bg-bg2">
              <Search class="size-5" />
              {{ locale.t('conversationInfo.searchBtn', 'поиск') }}
            </button>
            <div class="relative">
              <button type="button" @click="moreOpen = !moreOpen"
                      class="flex w-full flex-col items-center gap-1 rounded-xl bg-card2 py-2.5 text-xs text-accent transition-colors hover:bg-bg2">
                <MoreVertical class="size-5" />
                {{ locale.t('conversationInfo.more', 'ещё') }}
              </button>
              <!-- «Ещё»: редкие и необратимые действия. Держать удаление переписки на
                   виду рядом с обычными кнопками — способ однажды нажать не туда. -->
              <div v-if="moreOpen"
                   class="absolute right-0 top-full z-20 mt-1 w-56 overflow-hidden rounded-lg border border-border bg-card shadow-xl">
                <button type="button" @click="moreOpen = false; emit('summary')"
                        class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-bg2">
                  <ScrollText class="size-4 text-text3" />
                  {{ locale.t('conversationInfo.summaryAction', 'Краткая сводка') }}
                </button>
                <button type="button" @click="moreOpen = false; clearHistory()"
                        class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-bg2">
                  <Trash2 class="size-4 text-text3" />
                  {{ locale.t('conversationInfo.clearHistory', 'Очистить историю') }}
                </button>
                <button type="button" @click="moreOpen = false; removeChat()"
                        class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red hover:bg-bg2">
                  <Trash2 class="size-4" />
                  {{ locale.t('conversationInfo.deleteAction', 'Удалить переписку') }}
                </button>
                <button v-if="isGroupOrChannel" type="button" @click="moreOpen = false; leave()"
                        class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red hover:bg-bg2">
                  <LogOut class="size-4" />
                  {{ locale.t('conversationInfo.leaveAction', 'Покинуть') }}
                </button>
              </div>
            </div>
          </div>

          <!-- ВКЛАДКИ. Разделение по смыслу: человек ищет «того участника», «ту
               картинку» и «тот документ» в разных местах, и одна общая лента не
               заменяет ни одного из трёх. -->
          <div class="mb-3 flex gap-1 rounded-xl bg-card2 p-1">
            <button v-for="[key, label, icon] in tabDefs" :key="key" type="button" @click="openTab(key)"
                    class="flex flex-1 items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs transition-colors"
                    :class="tab === key ? 'bg-card text-text shadow-sm' : 'text-text3 hover:text-text'">
              <component :is="icon" class="size-3.5" />
              <span class="hidden sm:inline">{{ label }}</span>
            </button>
          </div>

          <p v-if="tabLoading" class="py-8 text-center text-xs text-text3">
            {{ locale.t('common.loading', 'Загрузка…') }}
          </p>

          <!-- Медиа: гифки и видео-ссылки. Видео у нас живёт ССЫЛКОЙ на видеохостинг,
               а не файлом, поэтому вкладка собирается из тела сообщений. -->
          <div v-else-if="tab === 'media'">
            <p v-if="!media.length" class="py-8 text-center text-xs text-text3">
              {{ locale.t('conversationInfo.noMedia', 'В этой беседе пока нет медиа') }}
            </p>
            <div v-else class="grid grid-cols-3 gap-1.5">
              <a v-for="it in media" :key="it.message_id" :href="it.url" target="_blank" rel="noopener"
                 class="block overflow-hidden rounded-lg border border-border bg-card2">
                <img v-if="it.kind === 'gif'" :src="it.url" alt="" class="h-24 w-full object-cover" loading="lazy" />
                <span v-else class="flex h-24 items-center justify-center px-2 text-center text-[10px] text-text3">
                  {{ locale.t('conversationInfo.videoLink', 'видео') }}
                </span>
              </a>
            </div>
          </div>

          <!-- Избранное: что ИЗ ЭТОЙ беседы человек унёс к себе. Ответ строго личный —
               показать это другим участникам значило бы раскрыть, что он счёл важным. -->
          <div v-else-if="tab === 'saved'">
            <p v-if="!savedItems.length" class="py-8 text-center text-xs text-text3">
              {{ locale.t('conversationInfo.noSaved', 'Вы ничего не сохраняли из этой беседы') }}
            </p>
            <!-- Строка сохранённого — та же карточка, что у сообщения в ленте: аватарка,
                 полное имя автора, текст и ВРЕМЯ ОТПРАВКИ оригинала. Раньше здесь было
                 одно имя и текст, и понять, чьё это и когда сказано, было нельзя. -->
            <div v-else class="space-y-1">
              <div v-for="it in savedItems" :key="it.message_id"
                   class="flex min-w-0 gap-2.5 rounded-lg border border-border bg-card2 px-3 py-2">
                <Avatar :src="it.from_avatar" :name="it.from_name" :role="it.from_role"
                        :color="profilePlate(it.from_color)" :size="32" class="mt-0.5 shrink-0" />
                <div class="min-w-0 flex-1">
                  <div class="mb-0.5 flex items-baseline gap-2">
                    <span class="min-w-0 flex-1 truncate text-xs font-semibold text-text">
                      {{ it.from_name || locale.t('conversationInfo.savedUnknownAuthor', 'Неизвестный автор') }}
                    </span>
                    <span class="shrink-0 text-[11px] tabular-nums text-text3">{{ savedTime(it) }}</span>
                  </div>
                  <p class="whitespace-pre-wrap break-words text-sm text-text2">{{ it.body }}</p>
                </div>
              </div>
            </div>
          </div>

          <!-- Файлы: документы беседы. Ссылку на скачивание здесь НЕ держим — её
               выдаёт отдельная ручка, она живёт минуты и только участнику. -->
          <div v-else-if="tab === 'files'">
            <p v-if="!files.length" class="py-8 text-center text-xs text-text3">
              {{ locale.t('conversationInfo.noFiles', 'В этой беседе пока нет файлов') }}
            </p>
            <div v-else class="space-y-1">
              <button v-for="f in files" :key="f.id" type="button" @click="emit('open-file', f)"
                      class="flex w-full items-center gap-2.5 rounded-lg border border-border bg-card2 px-3 py-2 text-left hover:border-accent">
                <FileText class="size-4 shrink-0 text-text3" />
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-sm text-text">{{ f.name }}</span>
                  <span class="block text-[11px] text-text3">{{ humanSize(f.size) }}</span>
                </span>
              </button>
            </div>
          </div>

          <template v-else>
          <p class="mb-2 text-[11px] uppercase tracking-wide text-text3">
            {{ locale.t('profilePanel.participants', 'Участники') }} · {{ people.length }}
          </p>

          <!-- 🔥 «Добавить участников» — до 25.08.2026 этой кнопки не было, и добавить
               человека в беседу через продукт было НЕЛЬЗЯ ВОВСЕ: серверная ручка
               работала, а звать её было некому. Стоит ПЕРВОЙ строкой списка, как в
               Telegram: искать её в меню «ещё» никто не станет. -->
          <button v-if="canAdd" type="button" @click="addOpen = true"
                  class="mb-1 flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left
                         text-accent transition-colors hover:bg-bg2">
            <span class="grid size-7 place-items-center rounded-full border border-dashed border-accent/50">
              <UserPlus :size="14" />
            </span>
            <span class="text-sm font-medium">
              {{ activeKind === 'channel'
                 ? locale.t('members.addToChannel', 'Добавить читателей')
                 : locale.t('members.addToGroup', 'Добавить участников') }}
            </span>
          </button>
          <div class="space-y-1">
            <div v-for="p in people" :key="p.user_id"
                 class="relative flex items-center gap-2.5 rounded-lg px-2 py-1.5 hover:bg-bg2">
              <!-- §5.4: клик по аватарке/имени — карточка профиля этого участника. -->
              <button type="button" @click="profileFor = { userId: p.user_id }"
                      class="flex min-w-0 flex-1 items-center gap-2.5 text-left">
                <!-- ⚠️ p.role здесь — роль УЧАСТНИКА БЕСЕДЫ (owner/admin/writer/member/
                     reader, §ролей), а НЕ роль в системе — та отдельным полем
                     p.user_role (student/teacher/admin/parent), не перепутать. -->
                <Avatar :src="p.avatar" :name="p.full_name" :role="p.user_role" :color="profilePlate(p.profile_color)" :online="!!p.online" :size="36" />
                <div class="min-w-0 flex-1">
                  <div class="flex items-center gap-1.5">
                    <span class="truncate text-sm font-medium text-text">{{ p.full_name }}</span>
                    <Crown v-if="p.user_id === ownerId || p.role === 'owner'"
                           class="size-3.5 shrink-0 text-accent" :aria-label="locale.t('conversationInfo.owner', 'Владелец')" />
                    <span v-if="p.silenced" :title="locale.t('conversationInfo.silencedHint', 'Заглушён(а) — /mute')" class="shrink-0 text-xs">🔇</span>
                  </div>
                  <div class="flex items-center gap-1.5 truncate text-[11px] text-text3">
                    <!-- Статус участника — тем же кружком, что и в карточке собеседника. -->
                    <span v-if="statusLabel(p)" class="size-2 shrink-0 rounded-full"
                          :style="{ background: statusColor(p) }" />
                    <span class="truncate">{{ statusLabel(p) || meta(p) }}</span>
                  </div>
                </div>
              </button>
              <span class="shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold"
                    :class="(p.user_id === ownerId || p.role === 'owner')
                      ? 'bg-accent-glow text-accent' : 'bg-bg2 text-text3'">
                {{ p.custom_role_name || ROLE_RU[p.role] || p.role }}
              </span>
              <!-- §ролей: меню участника — кик/игнор/роль. Игнор доступен всем (личное),
                   кик/роль — по правам. Себя выгнать/игнорировать нельзя из этого меню. -->
              <button v-if="p.user_id !== myUserId" type="button" data-role-menu @click.stop="toggleMenu(p.user_id)"
                      :aria-label="locale.t('messenger.actions', 'Действия')" class="grid size-7 shrink-0 place-items-center rounded-md text-text3 hover:bg-bg2 hover:text-text">
                <MoreVertical class="size-4" />
              </button>
              <div v-if="openMenuFor === p.user_id" data-role-menu
                   class="absolute right-2 top-10 z-10 w-44 rounded-lg border border-border2 bg-card p-1 shadow-card">
                <button type="button" @click="toggleIgnore(p)"
                        class="block w-full rounded-md px-2.5 py-1.5 text-left text-sm text-text2 hover:bg-bg2">
                  {{ myIgnoredIds.has(p.user_id) ? locale.t('conversationInfo.showMessages', 'Показать сообщения') : locale.t('conversationInfo.ignore', 'Игнорировать') }}
                </button>
                <button v-if="canManageRoles && p.role !== 'owner'" type="button" @click="openRoleDialog(p)"
                        class="block w-full rounded-md px-2.5 py-1.5 text-left text-sm text-text2 hover:bg-bg2">
                  {{ locale.t('conversationInfo.grantRole', 'Выдать роль') }}
                </button>
                <button v-if="canKick && p.role !== 'owner'" type="button" @click="kick(p)"
                        class="block w-full rounded-md px-2.5 py-1.5 text-left text-sm text-red hover:bg-red/10">
                  {{ locale.t('conversationInfo.kickAction', 'Выгнать') }}
                </button>
              </div>
            </div>
          </div>
          </template>
        </template>
      </div>

      <AddMembersDialog v-if="addOpen" :existing-ids="existingIds" :kind="activeKind" :busy="addBusy"
                        @add="doAddMembers" @close="addOpen = false" />

      <RoleManagerDialog v-if="roleDialogFor" :user-id="roleDialogFor.user_id"
                         :user-name="roleDialogFor.full_name" @close="roleDialogFor = null" />
      <PeerProfileModal v-if="profileFor" :user-id="profileFor.userId || profileFor.id || ''"
                        :peer-data="profileFor.id ? profileFor : null" @close="profileFor = null" />

      <!-- Действия -->
      <div class="flex flex-wrap gap-2 border-t border-border p-3">
        <!-- ⚠️ Для группы и канала этих кнопок здесь БОЛЬШЕ НЕТ: они переехали в «ещё»
             наверху (25.08.2026). Два места для одного действия — верный способ
             однажды поправить одно и забыть другое.
             ⚠️ 11.09.2026 у ЛИЧНОГО чата они переехали туда же — в три точки карточки
             профиля, по прямому требованию «в эту же категорию перенеси очистку чата и
             удаление переписки, чтобы не захламлять». Ряд оставлен только для
             «Избранного»: своей карточки профиля у чата с самим собой нет, и без этой
             кнопки очистить блокнот стало бы нечем. -->
        <button v-if="isSaved" type="button" @click="clearHistory"
                class="rounded-lg border border-border2 px-3 py-2 text-sm text-text2 hover:bg-bg2">
          {{ locale.t('conversationInfo.clearHistory', 'Очистить историю') }}
        </button>
      </div>

      <!-- Редактор аватарки беседы. ТОТ ЖЕ компонент, что у профиля: обрезка, сжатие в
           data:URL и удаление (пустая строка) уже реализованы там, а второй формат
           картинки не прошёл бы проверку на сервере — она одна на обе стороны. -->
      <AvatarCropper v-if="avatarEditor" :current="activeInfo?.avatar || ''"
                     @save="saveAvatar" @close="avatarEditor = false" />
    </div>
  </div>
</template>
