<script setup>
// ChatThread — правая колонка: переписка активной беседы (пузыри в стиле Telegram) +
// действия над сообщением (Фаза 3): оверлей по тапу, плашка закреплённого, режим
// выделения, ответ/пересылка/удаление(у себя|у всех)/жалоба. ⚙-чат модерации — Фаза 4.
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import {
  Send, ArrowLeft, Pin, X, Reply as ReplyIcon, Forward, Trash2, LifeBuoy,
  Bold, Italic, Underline, Strikethrough, Code, Quote, ChevronDown, History,
  Search, Zap, MessageSquare, Eye, Plus, ScrollText, Check, CheckCheck, Clock, PieChart,
  Languages, Star, SmilePlus, ClipboardList, Paperclip, MoreVertical,
} from '@lucide/vue'
import { messengerApi } from '@/api/endpoints'
import { useMessengerStore } from '@/stores/messenger'
import { useAuthStore } from '@/stores/auth'
import { useTranslateStore } from '@/stores/translate'
import GifImage from '@/components/messenger/GifImage.vue'
import { useGifStore } from '@/stores/gif'
import { useTtsStore } from '@/stores/tts'
import { renderMarkdownLite } from '@/utils/markdownLite'
import { extractVideos, embedMode } from '@/utils/videoEmbed'
import { isNativeApp } from '@/api/server'
import { extractGifLinks } from '@/utils/gifEmbed'
import { formatSystemMessage } from '@/utils/messagePreview'
import { copyText } from '@/utils/clipboard'
import { highlightIn } from '@/utils/highlightFragment'
import { markupSpans, toggleWrap, selectionWrapped } from '@/utils/markupSpans'
//Отступ от края окна берём ОТТУДА ЖЕ, где его знает меню сообщения: два разных
//просвета у двух всплывающих слоёв читаются как небрежность, а не как решение.
import { EDGE } from '@/utils/menuPlacement'
import { useRoute } from 'vue-router'
import { useConfirm } from '@/composables/useConfirm'
import MessageActionsOverlay from './MessageActionsOverlay.vue'
import ReportDialog from './ReportDialog.vue'
import ForwardPicker from './ForwardPicker.vue'
import ConversationInfo from './ConversationInfo.vue'
import PeerProfileModal from './PeerProfileModal.vue'
import MascotCooldown from './MascotCooldown.vue'
import ReminderDialog from './ReminderDialog.vue'
import CuratorReportOverlay from './CuratorReportOverlay.vue'
import ActivityCard from '@/components/activity/ActivityCard.vue'
import BoardCard from '@/components/activity/BoardCard.vue'
import TranslateDialog from './TranslateDialog.vue'
import GifPicker from './GifPicker.vue'
import QuickReplyWheel from './QuickReplyWheel.vue'
import FilePreview from './FilePreview.vue'
import { humanSize } from '@/utils/docPreview'
import Avatar from '@/components/ui/Avatar.vue'
import haptics from '@/utils/haptics'
import { profilePlate } from '@/theme/palette'
import { nameDecor } from '@/config/nameEffects'
import { statusLabel } from '@/config/status'
import { roleLabel as sharedRoleLabel } from '@/config/roles'
import { useLocaleStore } from '@/stores/locale'
import { useActivityStore } from '@/stores/activity'
import TimerStrip from '@/components/activity/timer/TimerStrip.vue'
import PollMessage from '@/components/activity/poll/PollMessage.vue'

const locale = useLocaleStore()
const BCP47 = { ru: 'ru-RU', en: 'en-US', zh: 'zh-CN' }
const m = useMessengerStore()
const tr = useTranslateStore()
const showTranslate = ref(false)
const showGifPicker = ref(false)
// Меню «+» слева от поля ввода: три категории вместо четырёх постоянных кнопок над ним.
const plusMenu = ref(false)
// Форма «свой шаблон» — отдельно от колеса выбора: колесо закрывается по первому же
// выбору, а ввод текста это обратное действие (см. QuickReplyWheel.vue).
const showTemplateForm = ref(false)

// ── Вложение файла ─────────────────────────────────────────────────────────────────
// ⚠️ Файл сначала ПОКАЗЫВАЕМ, потом отправляем. Мессенджер, отправляющий документ по
// одному клику, рано или поздно отправит не тот файл не в тот чат — а «удалить у всех»
// в учебной переписке уже не помогает: его увидели.
const fileInput = ref(null)
const pendingFile = ref(null)       //выбран, но ещё не отправлен
const previewFile = ref(null)       //что показываем в просмотрщике
const previewAtt = ref(null)
const uploadPct = ref(0)
const uploadLimits = ref({ configured: true, max_size: 0, ext: [] })

async function loadUploadLimits() {
  try { uploadLimits.value = (await messengerApi.uploadLimits()).data } catch { /* не критично */ }
}
loadUploadLimits()

function pickFile() {
  if (!uploadLimits.value.configured) {
    // ⚠️ Причина важнее факта: «мало места» и «нет ключей» лечатся по-разному, и без
    // подсказки администратор будет искать проблему не там.
    m.setNotice(locale.t('files.notConfigured', 'Хранилище файлов пока не настроено'))
    return
  }
  fileInput.value?.click()
}

function onFileChosen(e) {
  const f = e.target.files?.[0]
  e.target.value = ''            //чтобы выбор ТОГО ЖЕ файла второй раз снова сработал
  if (!f) return
  const cap = uploadLimits.value.max_size || 0
  if (cap && f.size > cap) {
    m.setNotice(locale.t('files.tooBig', { mb: Math.round(cap / 1048576) }))
    return
  }
  pendingFile.value = f
}

async function sendPendingFile() {
  if (!pendingFile.value) return
  uploadPct.value = 1
  const caption = draft.value.trim()
  const ok = await m.sendFile(pendingFile.value, caption, (p) => { uploadPct.value = p })
  uploadPct.value = 0
  if (ok) {
    pendingFile.value = null
    draft.value = ''
    m.clearDraft(activeId.value)
  }
}
const auth = useAuthStore()
const gif = useGifStore()
const tts = useTtsStore()
const { confirm } = useConfirm()
const { activeId, activePeer, messages, loadingMessages, loadingOlder, hasOlder, sending, replyTo, replyQuote, pinned, selectionMode, selectedIds, isModeration, activeInfo, peerTyping, notice, activeChat, activeKind, mascotCooldown, templates, activeThread, searchResults, searching, searchExpanded } = storeToRefs(m)

// ── Плавное появление НОВЫХ сообщений ────────────────────────────────────────────
// ⚠️ Именно новых. Анимировать всю ленту при открытии беседы нельзя: пятьдесят
// одновременно всплывающих пузырей — это не «плавно», это рябь, и на слабой машине
// она ещё и роняет частоту кадров ровно в тот момент, когда человек ждёт содержимое.
// Поэтому первый показ беседы идёт БЕЗ анимации (список известных id пуст — значит
// это первичная загрузка), а анимируется только то, что пришло позже.
const enteringIds = ref(new Set())
let знакомыеId = new Set()
watch(
  () => messages.value.map((x) => x.id),
  (ids) => {
    if (знакомыеId.size) {
      // ⚠️ ТОЛЬКО ХВОСТ. «Новое» — это пришедшее В КОНЕЦ ленты. При подгрузке истории
      // пятьдесят сообщений подставляются В НАЧАЛО, и без этой проверки они всплывали
      // все разом — та самая рябь, которую комментарий выше запрещает, причём прямо
      // под рукой у прокручивающего. Поймано Полковником, а не тестом: моё утверждение
      // «анимируются только новые» было верно лишь для открытия беседы.
      const хвост = []
      for (let i = ids.length - 1; i >= 0; i--) {
        if (знакомыеId.has(ids[i])) break
        хвост.unshift(ids[i])
      }
      const свежие = хвост
      if (свежие.length) {
        const next = new Set(enteringIds.value)
        for (const id of свежие) next.add(id)
        enteringIds.value = next
        setTimeout(() => {
          const после = new Set(enteringIds.value)
          for (const id of свежие) после.delete(id)
          enteringIds.value = после
        }, 400)
      }
    }
    знакомыеId = new Set(ids)
  },
)
// Смена беседы — новая история: следующий список считаем первичным, иначе переход
// между чатами давал бы ту самую рябь.
watch(activeId, () => { знакомыеId = new Set(); enteringIds.value = new Set() })
//Свой id клиент знает только из conversation_info: в JWT лежат логин и роль.
//Нужен, чтобы не предлагать перевод СВОЕЙ же реплики.
const myUserId = computed(() => activeInfo.value?.my_user_id || '')
const canManageTemplates = computed(() => ['teacher', 'admin'].includes(auth.role))
// Админ САМ и есть модерация — кнопка «Написать модерации» ему не нужна (и сервер её закрыл).
const isAdmin = computed(() => auth.role === 'admin')

// Тип беседы и права (для шапки/композера групп и каналов).
//Тип беседы берём из стора (activeKind): он известен ещё до ответа /chats/{id}, иначе
//«Избранное» и группы на долю секунды рисовались как личный чат («был(а) недавно»).
const kind = computed(() => activeKind.value)
const isGroupOrChannel = computed(() => kind.value === 'group' || kind.value === 'channel')
const canPost = computed(() => {
  if (kind.value !== 'channel') return true
  return ['owner', 'admin', 'writer'].includes(activeInfo.value?.my_role)
})
// §D1: заголовки # / ## разрешены только в каналах (в личных чатах/группах — просто текст).
const isChannel = computed(() => kind.value === 'channel')
//Активности бывают только в общих беседах (PLAN-ACTIVITIES §1): в личном чате один
//собеседник, в «Избранном» один человек, в чате модерации служебный поток.
const isGroupLike = computed(() => kind.value === 'group' || kind.value === 'channel')

const draft = ref('')
const scroller = ref(null)
const composer = ref(null)

// Оверлей действий, модалки.
// `selection` — ВЫДЕЛЕННЫЙ внутри сообщения текст на момент открытия меню. Снимок, а не
// ссылка на живое выделение: клик по пункту меню выделение снимает, и к моменту, когда
// действие выполняется, спрашивать браузер уже поздно.
const overlay = ref({ open: false, message: null, x: 0, y: 0, selection: '' })
const reportMsg = ref(null)                 // сообщение, на которое жалуемся
const openReportOverlay = ref(null)         // §12: id открытого отчёта куратора (или null)
const activity = useActivityStore()         // кнопка активностей в шапке + значок «новая»
const forwardState = ref({ open: false, ids: [] })
const deleteTargets = ref(null)             // [message,…] для выбора «у себя/у всех» (все свои)
const copied = ref(false)
const showInfo = ref(false)                 // панель «О беседе» (участники/владелец)
// Все ли видимые сообщения уже выделены — для кнопки «Выбрать всё / Снять всё».
const allSelected = computed(() => messages.value.length > 0 && selectedIds.value.length === messages.value.length)

async function scrollDown() {
  await nextTick()
  scroller.value?.scrollTo({ top: scroller.value.scrollHeight })
  showScrollBtn.value = false
}

// §D5: плавающая кнопка «↓ N новых» — видна, когда пользователь прокрутил вверх (читает
// историю); новые сообщения тогда НЕ дёргают его вниз силой (кроме своих же исходящих).
const showScrollBtn = ref(false)
function onScrollerScroll() {
  const el = scroller.value
  if (!el) return
  showScrollBtn.value = (el.scrollHeight - el.scrollTop - el.clientHeight) > 200
  // Подошли к началу видимой истории — тянем предыдущую страницу. Порог не нулевой:
  // ждать буквального упора в край значит показать человеку пустоту и рывок.
  if (el.scrollTop < 200) подтянутьИсторию()
}

/**
 * Догрузка истории вверх с СОХРАНЕНИЕМ ПОЛОЖЕНИЯ. Без второй половины первая бесполезна:
 * пятьдесят сообщений, вставленных сверху, уносят прочитанное место вниз, и лента
 * прыгает под руками — так «подгрузка» превращается в дефект.
 */
async function подтянутьИсторию() {
  const el = scroller.value
  if (!el || loadingOlder.value || !hasOlder.value) return
  const высотаДо = el.scrollHeight
  const сверхуДо = el.scrollTop
  const добавлено = await m.loadOlder()
  if (!добавлено) return
  await nextTick()
  // Сдвигаем ровно на прирост высоты — тогда под курсором остаётся та же строка.
  el.scrollTop = сверхуДо + (el.scrollHeight - высотаДо)
}

watch(() => messages.value.length, async () => {
  const wasNearBottom = !showScrollBtn.value
  const lastMsg = messages.value[messages.value.length - 1]
  if (wasNearBottom || lastMsg?.mine) scrollDown()
  else await nextTick().then(onScrollerScroll)
})

// §D5: разделитель «Новые сообщения» — первое сообщение позже метки прочтения ДО открытия
// чата (activeInfo.my_last_read_at снимается в сторе ДО markReadActive, см. messenger.js).
const firstUnreadId = computed(() => {
  const lastRead = activeInfo.value?.my_last_read_at
  if (!lastRead) return null
  const found = messages.value.find(x => x.created_at > lastRead)
  return found ? found.id : null
})

// Разделитель по датам (как в Telegram): «Сегодня» / «Вчера» / число — граница СУТОК по
// часовому поясу УСТРОЙСТВА (Date у клиента), а не сервера. Наступление 00:00 у пользователя
// само переносит вчерашний ярлык в дату — computed пересчитывается при каждом новом сообщении.
function _dayKey(iso) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toDateString()
}
function _dayLabel(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  const key = d.toDateString()
  if (key === now.toDateString()) return locale.t('chatThread.today', 'Сегодня')
  if (key === yesterday.toDateString()) return locale.t('chatThread.yesterday', 'Вчера')
  return d.toLocaleDateString(BCP47[locale.active] || 'ru-RU', d.getFullYear() === now.getFullYear()
    ? { day: 'numeric', month: 'long' } : { day: 'numeric', month: 'long', year: 'numeric' })
}
const dateBreaks = computed(() => {
  const map = new Map()   // msg.id → подпись (первое сообщение НОВЫХ суток)
  let prevKey = null
  for (const msg of messages.value) {
    const key = _dayKey(msg.created_at)
    if (key && key !== prevKey) {
      map.set(msg.id, _dayLabel(msg.created_at))
      prevKey = key
    }
  }
  return map
})

watch(activeId, async (newId, oldId) => {
  // Черновики (клиент-only, docs/MESSENGER-ADDON-PLAN-GPT.md «Черновики»): сохраняем
  // недописанное перед уходом из чата и восстанавливаем при возврате в него.
  if (oldId) m.saveDraft(oldId, draft.value)
  draft.value = newId ? m.draftFor(newId) : ''
  closeSearchPanel()
  if (!newId) return
  // Ждём, пока стор подгрузит и сообщения, и activeInfo (для позиции разделителя), но не
  // дольше секунды — иначе просто скроллим вниз как обычно (не хуже прежнего поведения).
  for (let i = 0; i < 20 && (!messages.value.length || activeInfo.value === null); i++) {
    await new Promise((r) => setTimeout(r, 50))
  }
  await nextTick()
  if (firstUnreadId.value) {
    const el = document.getElementById(`gb-msg-${firstUnreadId.value}`)
    if (el) { el.scrollIntoView({ behavior: 'instant', block: 'center' }); onScrollerScroll(); return }
  }
  scrollDown()
})

onMounted(() => {
  if (activeId.value) draft.value = m.draftFor(activeId.value)
  m.loadTemplates()
  //Избранное нужно знать ДО открытия пикера — звезда на уже отправленной гифке (см.
  //gifEmbeds/msg.kind==='gif' ниже) должна сразу показывать верное состояние, а не
  //только после первого захода в GifPicker.vue (там тот же load(), идемпотентно).
  gif.load()
})

function fmtTime(iso) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString(locale.active === 'en' ? 'en-US' : locale.active === 'zh' ? 'zh-CN' : 'ru-RU', { hour: '2-digit', minute: '2-digit' })
}
function quoted(id) {
  const src = messages.value.find(x => x.id === id)
  return src ? (src.deleted ? locale.t('chatThread.deleted', 'Сообщение удалено') : src.body) : ''
}

// ── Цитата в пузыре (как в Telegram): имя автора + процитированный кусок ───────────────
// Кусок берём из `reply_quote` — его прислал СЕРВЕР, проверив, что он реально есть в
// оригинале. Пусто (обычный ответ) — показываем начало исходного сообщения, как было.
//
// ⚠️ Имя автора обязательно: в группе без него цитата выглядит как чей угодно текст, и
// смысл ответа меняется на противоположный, когда рядом спорят двое.
function quoteAuthor(id) {
  const src = messages.value.find(x => x.id === id)
  if (!src) return ''
  if (src.mine) return locale.t('chatThread.you', 'Вы')
  return senderName(src) || ''
}
function quoteText(msg) {
  if (msg.reply_quote) return msg.reply_quote
  return quoted(msg.reply_to_id)
}

// Клик по цитате: перемотать к оригиналу и подсветить ИМЕННО процитированный кусок.
//
// ⚠️ Оригинал может быть выше подгруженной страницы (лента отдаёт по 50). Тогда честно
// говорим, что дальше не листали, — молчаливое «ничего не произошло» человек читает как
// поломку и жмёт ещё раз.
async function jumpToQuoted(msg) {
  const id = msg.reply_to_id
  if (!id) return
  const el = document.getElementById(`gb-msg-${id}`)
  if (!el) {
    m.setNotice(locale.t('chatThread.quoteNotLoaded', 'Исходное сообщение ещё не загружено — пролистайте историю выше.'))
    return
  }
  jumpTo(id)
  await nextTick()
  const body = el.querySelector('.msg-body')
  const piece = (msg.reply_quote || '').trim()
  //Нашли кусок — подсвечиваем его; не нашли (оригинал отредактировали) — подсвечиваем
  //сообщение целиком: перемотка всё равно привела туда, куда обещала.
  if (!piece || !body || !highlightIn(body, piece)) flashMessage(id)
}

// §D6: системные сообщения (вступил/вышел/переименовано/закреп) — сервер шлёт шаблон
// "событие\x1fаргументы". Разбор общий с левым списком чатов (utils/messagePreview):
// две копии разбора уже разъезжались — в ленте текст был человеческий, а в списке сырой.

// §D1: рендер тела сообщения (Markdown-lite → безопасный HTML для v-html).
// §D8: подсветка @Фамилия/@!Фамилия — визуальная, ПОСЛЕ Markdown (безопасно: результат
// markdownLite уже экранирован, «@Слово» — обычный текст ни в одном из вставленных тегов).
// Отметка → user_id, ТЕМ ЖЕ способом, что сервер (_parse_mentions в routers/messenger.py):
// по ПЕРВОМУ слову ФИО, регистронезависимо. Строим карту из participants ЭТОЙ беседы —
// тех же, что уже приезжают в conversation_info для автодополнения (mentionCandidates).
const mentionUidBySurname = computed(() => {
  const map = new Map()
  for (const p of (activeInfo.value?.participants || [])) {
    const first = (p.full_name || '').trim().split(/\s+/)[0]
    if (first) map.set(first.toLowerCase(), p.user_id)
  }
  return map
})

// ⚠️ ЗДЕСЬ БЫЛ КЭШ РАЗБОРА, И ОН БЫЛ УБРАН ПО ЗАМЕРУ (24.08.2026). Гипотеза выглядела
// очевидной: функция зовётся из шаблона на каждое сообщение, внутри разбор Markdown, в
// ленте бывает 400 сообщений. Замер на стенде (та же беседа, шесть прогонов, медиана
// самой длинной задачи главного потока при приходе сообщения): 31 мс без кэша против
// 32 мс с кэшем — разница внутри шума. Причина в том, что Vue патчит по ключу ТОЛЬКО
// изменившийся узел, а не перерисовывает список целиком, и разбор старых тел заново не
// выполняется. Кэш при этом стоил инвалидации по метке правки — то есть риска показать
// старый текст у отредактированного сообщения. Не возвращать без нового замера.
function renderBody(msg) {
  const html = renderMarkdownLite(msg.body, isChannel.value)
  //Подсвечиваем ВСЕ формы отметки, включая «/@» и «/@!» — иначе тихий и громкий пинги
  //в ленте выглядели обычным текстом со слэшем. Набор форм — как у сервера (_MENTION_RE).
  // Клик по плашке открывает профиль отмеченного (см. onBodyClick) — уже отметку
  // сопоставили с user_id прямо здесь, чтобы не таскать это состояние отдельно.
  return html.replace(/(\/?(?:@!?|!@))([A-Za-zА-Яа-яЁё]+)/g, (hit, _prefix, name) => {
    const uid = mentionUidBySurname.value.get(name.toLowerCase())
    return uid ? `<span class="mention" data-mention-uid="${uid}">${hit}</span>`
               : `<span class="mention">${hit}</span>`
  })
}

// Фаза 1 ссылок/видео (docs/done/MESSENGER-ATTACHMENTS-PLAN.md): клик по обычной ссылке в теле
// сообщения (data-external-link, см. markdownLite.js) — подтверждение «Переадресация»
// перед уходом с сайта, как договорились (видео из белого списка сюда не попадают —
// они рендерятся ОТДЕЛЬНОЙ карточкой ниже, см. videoEmbeds()).
async function onBodyClick(e) {
  // Отметка — раньше просто подсветка, теперь ведёт на профиль отмеченного (как клик по
  // участнику в ConversationInfo). Проверяем ДО ссылок: разметка не пересекается, но
  // порядок проверки не имеет значения — просто первым делом.
  const mention = e.target.closest('.mention[data-mention-uid]')
  if (mention) { peerProfileId.value = mention.dataset.mentionUid; return }

  const a = e.target.closest('a[data-external-link]')
  if (!a) return
  e.preventDefault()
  const url = a.dataset.externalLink
  const ok = await confirm({
    title: locale.t('chatThread.redirectTitle', 'Переадресация'),
    message: locale.t('chatThread.redirectMessage', { url }),
    okText: locale.t('chatThread.openAction', 'Открыть'), cancelText: locale.t('common.cancel'),
  })
  if (ok) window.open(url, '_blank', 'noopener,noreferrer')
}
// Чей профиль показать в модалке. Имя общее, а не `mentionProfileId`, как было: открыть
// профиль можно тремя путями — по отметке в тексте, по аватарке автора и по его имени, —
// и название, говорящее только про отметки, соврало бы читателю уже на второй.
const peerProfileId = ref('')

// Профиль автора сообщения — по клику на аватарку или на его имя над пузырём (просьба
// Влада, 02.09.2026). Механизм был готов давно (PeerProfileModal), а в ЛЕНТЕ его не звал
// никто: в докстринге модалки при этом написано «открывается кликом по аватарке/имени
// человека где угодно в мессенджере». Наш обычный класс — обещание без вызывающего,
// только замеченное со стороны документации.
//
// ⚠️ У Вектора и системных сообщений отправитель — строка 'system', человека за ней нет.
// Открывать «профиль системы» нечем, поэтому и кликабельными они не становятся: мёртвая
// кнопка хуже её отсутствия, человек жмёт и решает, что подвисло.
function canOpenSenderProfile(msg) {
  return !!msg && !msg.mine && !isVector(msg) && !!msg.sender_id
}
function openSenderProfile(msg) {
  if (!canOpenSenderProfile(msg)) return
  peerProfileId.value = msg.sender_id
}

// Видео из белого списка (YouTube/VK/Rutube) — отдельная карточка ПОД текстом сообщения
// (v-html статичен и не даёт повесить Vue-обработчик внутрь), плеер виден сразу.
function videoEmbeds(msg) {
  if (msg.deleted || isHiddenByIgnore(msg)) return []
  return extractVideos(msg.body)
}

// ГЕЙТ встроенного плеера. В браузере чужой фрейм ограничен и sandbox'ом, и заголовками
// Caddy (frame-src белым списком), и самим origin-барьером. В мобильном приложении не
// действует НИ ОДНО из трёх: страница отдаётся из бандла (https://localhost), заголовки
// сервера туда не доезжают, а нативные мосты повешены через addJavascriptInterface —
// у него нет привязки к origin (для неё у Google заведён отдельный addWebMessageListener
// с allowedOriginRules). Значит внутри APK встроенного видеохостинга быть не должно.
// Признак «мы в приложении» берём ОБЩИЙ (api/server.js) — вторая его копия разошлась бы
// молча и именно в сторону «дверь снова открыта». Решение — в utils/videoEmbed.js,
// сторож — web/tests/videoEmbedNative.test.mjs.
const videoIframeAllowed = computed(() => embedMode(isNativeApp()) === 'iframe')

// Имя площадки для запасной карточки. Названия сервисов не переводятся — переводится
// только глагол вокруг них (ключ chatThread.openVideoAction).
const VIDEO_HOSTS = { youtube: 'YouTube', vk: 'VK Видео', rutube: 'Rutube' }
function videoHostLabel(v) { return VIDEO_HOSTS[v.provider] || v.provider }

// Открыть ролик СНАРУЖИ приложения. Подтверждения «Переадресация» здесь нет намеренно:
// в отличие от голой ссылки в тексте, тут человек жмёт кнопку, на которой написано, куда
// он идёт, — второй вопрос об этом же был бы шумом. @capacitor/browser грузим ленивым
// импортом: на сайте пакета в бандле быть не должно, а в приложении он уже установлен
// (он есть в зависимостях с самого начала, пересборка APK ради этого не нужна).
async function openExternalVideo(v) {
  try {
    const { Browser } = await import('@capacitor/browser')
    await Browser.open({ url: v.sourceUrl })
  } catch {
    // Нет плагина (сайт, десктоп, старая сборка) — обычная вкладка. Отказ открыть ролик
    // молча был бы хуже: человек нажал кнопку и не получил ничего.
    window.open(v.sourceUrl, '_blank', 'noopener,noreferrer')
  }
}

// Голая ссылка на CDN Klipy ВНУТРИ обычного текстового сообщения (не через пикер,
// msg.kind остаётся 'text') — раньше просто лежала мёртвым текстом, теперь тоже
// картинка, тем же приёмом, что видео выше. msg.kind==='gif' сюда не попадает —
// у него уже есть свой <img> (тело сообщения — САМА ссылка, дублировать нечего).
function gifEmbeds(msg) {
  if (msg.deleted || isHiddenByIgnore(msg) || msg.kind === 'gif') return []
  return extractGifLinks(msg.body)
}

// §D3: клик по реакции — поставить/снять свою.
function onReactionClick(msg, emoji) { m.toggleReaction(msg.id, emoji) }
function onReact(emoji) {
  const msg = overlay.value.message
  if (msg) m.toggleReaction(msg.id, emoji)
}

// §D11: попап «История изменений» у отредактированного сообщения.
const historyPopup = ref({ open: false, versions: [] })
async function openHistory(msg) {
  historyPopup.value = { open: true, versions: await m.messageHistory(msg.id) }
}
function fmtFull(iso) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(BCP47[locale.active] || 'ru-RU', { dateStyle: 'short', timeStyle: 'short' })
}

// ── §D1 → облачко форматирования над выделением (05.09.2026) ────────────────────────
// Просьба Влада: «меню над сообщением урезать максимально… если написали текст в ячейке и
// выделили — вылезет его форматирование… над выделенным текстом выйдет квадратное
// облачко с кнопками, при нажатии добавит символы».
//
// Постоянный ряд B/I/U/S над полем ввода убран: он занимал строку у КАЖДОГО, кто просто
// пишет сообщение, а нужен ровно в тот момент, когда текст выделен. Клавиатурные
// сокращения (Ctrl+B/I/U) остались — они и были главным способом.
//
// 🔥 ПОЧЕМУ РЯДОМ С ПОЛЕМ ЖИВЁТ ЗЕРКАЛО. Приглушить символы разметки прямо в `<textarea>`
// нельзя в принципе — покрасить часть её содержимого браузер не даёт. Поэтому текст поля
// делается прозрачным (виден только курсор), а под ним лежит `div`, рисующий ТОТ ЖЕ текст
// с приглушёнными парными обёртками — как в Discord (снимок 07 задания). Разбор на куски
// — чистая функция `utils/markupSpans.js`, проверяемая числами; зеркало отвечает только
// за отрисовку.
//
// ⚠️ Зеркало обязано совпадать с полем ПО ВСЕМ метрикам (шрифт, размер, отступы, перенос
// слов, прокрутка) — иначе приглушённые символы разъедутся с настоящими, и это будет
// выглядеть как рябь. Отсюда одинаковые классы и синхронизация `scrollTop`.
const mirror = ref(null)
const fmtBubble = ref({ open: false, left: 0, top: 0, start: 0, end: 0 })
//Ссылка на само облачко: его ширину надо ИЗМЕРИТЬ, а не предполагать (см. clampFmtBubble).
const fmtEl = ref(null)
const draftSpans = computed(() => markupSpans(draft.value))

// Символы, которые предлагает облачко. Ровно те, что понимает `markdownLite` — обещать
// форматирование, которого не будет в отправленном сообщении, нельзя.
const FMT_BUTTONS = computed(() => [
  { marker: '**', icon: Bold, title: locale.t('chatThread.fmt.bold', 'Жирный (Ctrl+B)') },
  { marker: '*', icon: Italic, title: locale.t('chatThread.fmt.italic', 'Курсив (Ctrl+I)') },
  { marker: '__', icon: Underline, title: locale.t('chatThread.fmt.underline', 'Подчёркнутый (Ctrl+U)') },
  { marker: '~~', icon: Strikethrough, title: locale.t('chatThread.fmt.strike', 'Зачёркнутый') },
  { marker: '`', icon: Code, title: locale.t('chatThread.fmt.code', 'Код') },
])

// Узел зеркала и смещение внутри него для позиции `pos` в тексте. Зеркало содержит РОВНО
// тот же текст, что поле, — просто разложенный по span'ам, — поэтому обход текстовых
// узлов даёт точное соответствие.
function mirrorPoint(pos) {
  const root = mirror.value
  if (!root) return null
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  let seen = 0
  let node = walk.nextNode()
  while (node) {
    const len = node.nodeValue.length
    if (seen + len >= pos) return { node, offset: pos - seen }
    seen += len
    node = walk.nextNode()
  }
  return node ? { node, offset: node.nodeValue.length } : null
}

function updateFmtBubble() {
  const el = composer.value
  const wrap = el?.parentElement
  if (!el || !wrap) { fmtBubble.value.open = false; return }
  const start = el.selectionStart ?? 0
  const end = el.selectionEnd ?? 0
  if (start === end) { fmtBubble.value.open = false; return }
  const a = mirrorPoint(start)
  const b = mirrorPoint(end)
  if (!a || !b) { fmtBubble.value.open = false; return }
  try {
    const range = document.createRange()
    range.setStart(a.node, a.offset)
    range.setEnd(b.node, b.offset)
    const r = range.getBoundingClientRect()
    const box = wrap.getBoundingClientRect()
    fmtBubble.value = {
      open: true,
      left: r.left + r.width / 2 - box.left,   //центр выделения, поправим по измерению
      top: r.top - box.top - 8,
      start,
      end,
    }
    nextTick(clampFmtBubble)
  } catch { fmtBubble.value.open = false }
}

/**
 * Вписать облачко форматирования в экран ПО ИЗМЕРЕНИЮ, а не по вписанному числу.
 *
 * 🔥 ТОТ ЖЕ ДЕФЕКТ, ЧТО ЧИНИЛИ У МЕНЮ СООБЩЕНИЯ (найден 07.09.2026 при проверке «нет ли
 * похожего в других местах»). Здесь стояло `const half = 96`, то есть ширина облачка
 * объявлялась константой в 192 px. Она неверна по построению: кнопок в облачке столько,
 * сколько разметок мы поддерживаем, и стоит добавить одну — облачко станет шире, а
 * кламп продолжит считать по-старому и пустит его за край.
 *
 * ⚠️ Вписываем в ОКНО, а не в обёртку. Обёртка — поле ввода, у него свои поля и на узком
 * экране оно уже окна: кламп по обёртке отодвигал бы облачко там, где место есть, и не
 * спасал бы там, где его нет.
 *
 * ⚠️ `left` — это ЦЕНТР (у элемента `-translate-x-1/2`), поэтому и границы считаются от
 * половины ширины. Забыть про это — значит увести облачко ровно на полширины.
 */
function clampFmtBubble() {
  const box = composer.value?.parentElement?.getBoundingClientRect()
  const bw = fmtEl.value?.offsetWidth
  if (!box || !bw) return                    //ещё не отрисовалось — поправим в следующий раз
  const half = bw / 2
  const minCenter = EDGE + half - box.left
  const maxCenter = window.innerWidth - EDGE - half - box.left
  //Окно уже самого облачка (очень узкий экран) — центрируем, обрезать симметрично лучше,
  //чем прижать к одному краю и спрятать половину кнопок за другим.
  fmtBubble.value.left = maxCenter < minCenter
    ? window.innerWidth / 2 - box.left
    : Math.max(minCenter, Math.min(fmtBubble.value.left, maxCenter))
}

function applyFmt(marker) {
  const { start, end } = fmtBubble.value
  const res = toggleWrap(draft.value, start, end, marker)
  draft.value = res.text
  nextTick(() => {
    const el = composer.value
    if (!el) return
    el.focus()
    el.setSelectionRange(res.start, res.end)
    updateFmtBubble()
  })
}
// Кнопка показывает СОСТОЯНИЕ, а не только действие: уже обёрнутый кусок она снимет.
function fmtActive(marker) {
  return selectionWrapped(draft.value, fmtBubble.value.start, fmtBubble.value.end, marker)
}

// §D1: клавиатурные сокращения — тот же переключатель, что у облачка. Раньше здесь была
// своя вставка символов, и повторное нажатие давало `****текст****`: кнопка, которой
// нельзя отменить саму себя.
function wrapSelection(marker) {
  const el = composer.value
  if (!el) return
  const start = el.selectionStart ?? draft.value.length
  const end = el.selectionEnd ?? draft.value.length
  const res = toggleWrap(draft.value, start, end, marker)
  draft.value = res.text
  nextTick(() => {
    el.focus()
    el.setSelectionRange(res.start, res.end)
    updateFmtBubble()
  })
}

// Зеркало прокручиваем вслед за полем — иначе при длинном черновике приглушённые символы
// останутся на месте, а настоящие уедут.
function syncMirrorScroll() {
  if (mirror.value && composer.value) mirror.value.scrollTop = composer.value.scrollTop
}
function onComposerKeydown(e) {
  if (e.ctrlKey || e.metaKey) {
    if (e.key === 'b') { e.preventDefault(); wrapSelection('**'); return }
    if (e.key === 'i') { e.preventDefault(); wrapSelection('*'); return }
    if (e.key === 'u') { e.preventDefault(); wrapSelection('__'); return }
  }
  if (mentionCandidates.value.length && e.key === 'Escape') { mentionQuery.value = null; return }
  if (slashCandidates.value.length && e.key === 'Escape') { slashQuery.value = null; return }
  onKey(e)
}

// ── Автодополнение слэш-команд «/» (как в Telegram) ─────────────────────────────────────
// Команда доступна только если это ВЕСЬ текст от начала сообщения до курсора (без
// пробела) — как только начат аргумент («/vector когда…»), подсказка сама пропадает.
// §12: /отчет доступен куратору (и администрации) в ЛЮБОЙ беседе, где он пишет: «/отчет
// К75/1» публикует отчёт СРАЗУ сюда — например в чат родителей. В канале «Отчёты · Группа»
// группу называть не нужно, она известна из самого канала.
const reportGroups = ref([])
onMounted(async () => {
  if (!['teacher', 'admin'].includes(auth.role)) return
  try {
    const all = (await messengerApi.myGroups()).data.groups || []
    reportGroups.value = (auth.role === 'admin' ? all : all.filter(g => g.curated)).map(g => g.name)
  } catch { /* не куратор — команды просто не будет */ }
})
const inReportsChannel = computed(() => activeInfo.value?.system_kind === 'curator_reports')
const canReport = computed(() => reportGroups.value.length > 0)
// §ролей: доступность /mute и /clear решает my_permissions из conversation_info — то же
// поле, что гейтит кнопки «Выгнать»/«Выдать роль» в ConversationInfo.vue.
const myPermissions = computed(() => new Set(activeInfo.value?.my_permissions || []))
const canMute = computed(() => myPermissions.value.has('cmd_mute'))
const canClear = computed(() => myPermissions.value.has('cmd_clear'))
// Право начать активность ЗЕРКАЛИТ серверный `activities._require_can_run`, а не
// придумывает своё: беседа общая + (право роли «activities» ИЛИ системная роль
// преподавателя/админа). Второе слагаемое обязательно — преподаватель ведёт занятие, а не
// администрирует чат, и просить владельца беседы выдать ему роль ради пары странно.
// ⚠️ Это подсказка, а не защита: настоящую проверку делает сервер и вернёт 403 с причиной.
// Здесь она нужна, чтобы команда не выглядела рабочей у того, кому нельзя.
const canRunActivity = computed(() =>
  isGroupOrChannel.value
  && (myPermissions.value.has('activities')
      || auth.user?.role === 'teacher' || auth.user?.role === 'admin'))
// §ролей: игнор — по клику раскрывается ЛОКАЛЬНО (revealedIds), снова прячется повторным
// кликом; сервер тут не участвует, это личное удобство, а не приватность (как в Discord).
const myIgnoredIds = computed(() => new Set(activeInfo.value?.my_ignored_user_ids || []))
const revealedIds = ref(new Set())
function isHiddenByIgnore(msg) {
  return myIgnoredIds.value.has(msg.sender_id) && !revealedIds.value.has(msg.id)
}
function toggleReveal(id) {
  const next = new Set(revealedIds.value)
  if (next.has(id)) next.delete(id); else next.add(id)
  revealedIds.value = next
}
// По «/» показываем ВСЕ команды мессенджера, а не только годные здесь: иначе в обычном
// чате список выходил пустым, и выглядело так, будто команд нет вовсе. Недоступные
// показываем блёклыми и с причиной — сразу видно, где команда сработает.
const SLASH_COMMANDS = computed(() => [
  //⚠️ Подсказка РАЗНАЯ, и это не украшение: в «Избранном» разговор с Вектором остаётся
  //в переписке, а в общей беседе ответ личный и исчезает при перезагрузке. Человек должен
  //знать это ДО вопроса — иначе либо промолчит о нужном, решив, что увидят все, либо
  //понадеется на историю, которой не будет.
  {
    cmd: '/vector',
    hint: isSaved.value
      ? locale.t('chatThread.cmd.vectorHintSaved', 'Спросить ИИ-помощника — например: /vector когда экзамен по физике? Дальше отвечайте на его сообщения — префикс больше не нужен.')
      : locale.t('chatThread.cmd.vectorHintChat', 'Спросить ИИ-помощника — ответ увидите только вы, в переписке он не останется.'),
    ok: true,
    why: '',
  },
  {
    cmd: '/отчет',
    hint: inReportsChannel.value
      ? locale.t('chatThread.cmd.reportHintChannel', 'Отчёт по успеваемости группы этого канала')
      : locale.t('chatThread.cmd.reportHint', { cmd: '/отчет', group: reportGroups.value[0] || 'К74/1' }),
    ok: canReport.value,
    why: locale.t('chatThread.cmd.reportWhy', 'Отчёт по группе выпускает её куратор или администрация'),
  },
  //Отметки — тоже «команды с /»: иначе про них никто не узнает. Доступны всегда, где
  //есть кого отмечать (в «Избранном» человек один — ты сам, отмечать некого).
  {
    cmd: '/@',
    hint: locale.t('chatThread.cmd.mentionQuietHint', 'Тихо отметить участника — например: /@Иванов (только значок «@» у него в списке)'),
    ok: !isSaved.value,
    why: locale.t('chatThread.cmd.noOneToMention', 'В личных заметках отмечать некого'),
  },
  {
    cmd: '/@!',
    hint: locale.t('chatThread.cmd.mentionLoudHint', 'Громко отметить — звук у собеседника и письмо во вкладку «Уведомления»'),
    ok: !isSaved.value,
    why: locale.t('chatThread.cmd.noOneToMention', 'В личных заметках отмечать некого'),
  },
  //ℹ️ `/активность` ЗДЕСЬ БОЛЬШЕ НЕТ (17.08.2026, решение Влада). Команда была
  //ЕДИНСТВЕННОЙ дверью к активностям — отсюда и запись в списке, и сторож
  //`web/tests/slashCommandsReachable.test.mjs`. С 3.7.4 дверь другая и лучше: кнопка в
  //шапке беседы, которая зовёт лаунчер напрямую (`activity.openLauncher`), без похода на
  //сервер. Держать рядом ещё и команду значит иметь два входа в одно место, из которых
  //один надо помнить наизусть. Сторож при этом НЕ ослаблен и исключения в него не
  //дописано: он выводит список команд из серверных регулярок, а разбор `/активность` на
  //сервере удалён тем же заходом — то есть требовать её он перестал сам.
  {
    cmd: '/mute',
    hint: locale.t('chatThread.cmd.muteHint', 'Заглушить участника в этой беседе — например: /mute @Иванов (повтор снимает)'),
    ok: canMute.value,
    why: locale.t('chatThread.cmd.permWhy', 'Право есть у владельца/админа беседы или роли с ним'),
  },
  {
    cmd: '/clear',
    hint: locale.t('chatThread.cmd.clearHint', 'Удалить последние N сообщений беседы — например: /clear 20'),
    ok: canClear.value,
    why: locale.t('chatThread.cmd.permWhy', 'Право есть у владельца/админа беседы или роли с ним'),
  },
])
const slashQuery = ref(null)     // null — закрыто; иначе введённое после «/»
const slashCandidates = computed(() => {
  if (slashQuery.value === null) return []
  const q = slashQuery.value.toLowerCase()
  return SLASH_COMMANDS.value.filter((c) => c.cmd.slice(1).toLowerCase().startsWith(q))
})
function insertSlashCommand(c) {
  if (!c.ok) return          //недоступную здесь команду не подставляем — сервер её отклонит
  //Отметка склеена с именем («/@Иванов»), пробел после префикса её бы разорвал — сразу
  //за подстановкой открываем список участников, чтобы имя выбиралось, а не набиралось.
  const isMention = c.cmd.includes('@')
  draft.value = isMention ? c.cmd : c.cmd + ' '
  slashQuery.value = null
  nextTick(() => {
    composer.value?.focus()
    if (isMention) onComposerInput()
  })
}

// §D8: автодополнение отметки в композере — среди участников ЭТОЙ беседы.
// Ловим ВСЕ формы, которые понимает сервер (_parse_mentions в routers/messenger.py):
//   @Фам   — обычная (пуш есть)      /@Фам   — тихая (только значок «@»)
//   @!Фам  — тихая, старая форма     /@!Фам, /!@Фам — громкая (звук + письмо в «Систему»)
// Прежняя регулярка требовала пробел (или начало строки) ПЕРЕД «@», поэтому после «/»
// список участников не открывался вовсе — а именно так отметку и начинают набирать.
// Группа 1 — сам префикс (его сохраняем при подстановке, иначе «громко» стало бы «тихо»),
// группа 2 — уже набранные буквы имени.
const _MENTION_TOKEN_RE = /(?:^|\s)(\/?(?:@!?|!@))([A-Za-zА-Яа-яЁё]*)$/
const mentionQuery = ref(null)     // null — закрыто; иначе набранное после «@»
const mentionPrefix = ref('@')     // чем человек начал отметку — подставляем обратно как есть
const mentionStart = ref(0)        // индекс начала токена в тексте (по нему и заменяем)
let draftTimer = null
function onComposerInput() {
  m.sendTyping()
  clearTimeout(draftTimer)
  draftTimer = setTimeout(() => m.saveDraft(activeId.value, draft.value), 400)
  const el = composer.value
  if (!el) { mentionQuery.value = null; slashQuery.value = null; return }
  const pos = el.selectionStart ?? draft.value.length
  const before = draft.value.slice(0, pos)
  const at = _MENTION_TOKEN_RE.exec(before)
  if (at) {
    mentionPrefix.value = at[1]
    mentionQuery.value = at[2]
    mentionStart.value = pos - at[1].length - at[2].length
  } else {
    mentionQuery.value = null
  }
  // «/» — только пока курсор ещё в первом токене сообщения (аргумент не начат). Когда
  // «/» уже перешёл в отметку («/@…»), список команд не нужен — там своё меню.
  const slash = /^\/(\S*)$/.exec(before)
  slashQuery.value = (slash && mentionQuery.value === null) ? slash[1] : null
}
// Кого можно отметить. Раньше список показывался ТОЛЬКО в группах и каналах, и в личной
// переписке «@» просто ничего не открывал. Отмечать собеседника в ЛС осмысленно — это
// адресное «обрати внимание» в длинной ветке, и сервер такие отметки уже принимает.
// Себя из списка убираем: отметить самого себя нечем помочь (и пинг себе не уходит).
const mentionCandidates = computed(() => {
  if (mentionQuery.value === null) return []
  const q = mentionQuery.value.toLowerCase()
  const myId = activeInfo.value?.my_user_id || ''
  return (activeInfo.value?.participants || [])
    .filter(p => p.user_id !== myId)
    // Сужаем по ЛЮБОМУ слову ФИО, а не только по фамилии: человека ищут и по имени
    // («Влад…»), а раньше такой ввод давал пустой список.
    .filter(p => !q || (p.full_name || '').toLowerCase().split(/\s+/).some(w => w.startsWith(q)))
    .slice(0, 8)
})
// Что произойдёт при выбранном префиксе — та же таблица, что у сервера в _parse_mentions.
const mentionKindHint = computed(() => {
  const p = mentionPrefix.value
  if (p === '/@!' || p === '/!@') return locale.t('chatThread.mentionLoud', 'Громкая отметка: звук и уведомление в «Системе»')
  if (p === '/@' || p === '@!') return locale.t('chatThread.mentionQuiet', 'Тихая отметка: только значок «@» в списке чатов')
  return locale.t('chatThread.mentionNormal', 'Обычная отметка')
})
// Подпись справа в списке: у преподавателя — предметы, у студента — группа. Тот же
// принцип, что в карточке участника (ConversationInfo.vue::meta) — однофамильцев в
// колледже хватает, и без контекста непонятно, кого отмечаешь.
function meta(p) {
  if (p.user_role === 'teacher') return (p.subjects || []).join(', ') || sharedRoleLabel('teacher')
  if (p.user_role === 'admin') return sharedRoleLabel('admin')
  return p.group_name || ''
}
function insertMention(p) {
  //Сервер сопоставляет отметку по ПЕРВОМУ слову ФИО — подставляем ровно его.
  const first = (p.full_name || '').trim().split(/\s+/)[0] || ''
  if (!first) return
  const el = composer.value
  const pos = el?.selectionStart ?? draft.value.length
  const start = mentionStart.value
  draft.value = draft.value.slice(0, start) + mentionPrefix.value + first + ' '
    + draft.value.slice(pos)
  mentionQuery.value = null
  slashQuery.value = null
  nextTick(() => {
    el?.focus()
    //Курсор — сразу за подставленным именем, а не в конце строки: иначе продолжение
    //фразы приходилось бы доводить мышью.
    const caret = start + mentionPrefix.value.length + first.length + 1
    el?.setSelectionRange(caret, caret)
  })
}

// ── Жесты над сообщением (как в Telegram) ────────────────────────────────────────────
// ПК: меню действий — по ПРАВОЙ кнопке (ЛКМ ничего не открывает, чтобы не мешать выделению
// текста). Мобилка: по ДОЛГОМУ нажатию; свайп вправо по сообщению — быстрый ответ.
const LONG_PRESS_MS = 450     // сколько держать палец до появления меню
const SWIPE_REPLY_PX = 60     // насколько сдвинуть сообщение, чтобы сработал ответ
const swipe = ref({ id: 0, dx: 0 })   // визуальный сдвиг пузыря во время свайпа

let pressTimer = null
let touchStart = null         // {x, y, msg} — начало касания
let touchMoved = false        // был ли жест (тогда не считаем его тапом)

// Что человек выделил ВНУТРИ этого сообщения. Пусто — обычное меню, не пусто —
// «телеграмное» меню по выделению (см. utils/messageMenu.js).
//
// ⚠️ Проверяем, что выделение лежит ИМЕННО в этом пузыре. Без этого меню на одном
// сообщении показывало бы цитату из соседнего — выделение живёт на всей странице, а не
// внутри элемента, по которому нажали.
function selectionInside(el) {
  try {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || !sel.rangeCount) return ''
    const range = sel.getRangeAt(0)
    if (!el || !el.contains(range.commonAncestorContainer)) return ''
    return String(sel).trim()
  } catch { return '' }
}

function openMenu(msg, x, y, selection = '') {
  if (selectionMode.value) return
  overlay.value = { open: true, message: msg, x, y, selection }
}

// ЛКМ: только отметка в режиме выделения (меню сюда больше не привязано).
function onMessageClick(msg) {
  if (selectionMode.value) m.toggleSelect(msg.id)
}

// ПКМ на ПК и долгое нажатие в мобильных браузерах (они шлют contextmenu сами).
function onContextMenu(msg, e) {
  e.preventDefault()
  openMenu(msg, e.clientX, e.clientY, selectionInside(e.currentTarget))
}

function onTouchStart(msg, e) {
  const t = e.touches?.[0]
  if (!t) return
  touchStart = { x: t.clientX, y: t.clientY, msg, el: e.currentTarget }
  touchMoved = false
  clearTimeout(pressTimer)
  // Свой таймер долгого нажатия — в WebView приложения contextmenu приходит не всегда.
  pressTimer = setTimeout(() => {
    if (!touchMoved && touchStart) {
      //Отдача РОВНО в момент срабатывания: у долгого нажатия нет визуального якоря, и до
      //появления меню человек не знает, сколько держать. Это первый из двух случаев в
      //haptics.js — «палец действует вслепую».
      haptics.tap()
      //На телефоне выделение делают долгим нажатием ЖЕ — к моменту нашего таймера оно
      //уже есть, если человек выделял. Спрашиваем тот же элемент, что и на ПК.
      openMenu(msg, touchStart.x, touchStart.y, selectionInside(touchStart.el))
    }
  }, LONG_PRESS_MS)
}

function onTouchMove(e) {
  const t = e.touches?.[0]
  if (!t || !touchStart) return
  const dx = t.clientX - touchStart.x
  const dy = t.clientY - touchStart.y
  if (Math.abs(dx) > 8 || Math.abs(dy) > 8) {
    touchMoved = true
    clearTimeout(pressTimer)
  }
  // Горизонтальный жест вправо — тянем пузырь за пальцем (вертикальную прокрутку не трогаем).
  if (Math.abs(dx) > Math.abs(dy) && dx > 0 && !touchStart.msg.deleted) {
    const wasArmed = swipe.value.dx >= SWIPE_REPLY_PX
    swipe.value = { id: touchStart.msg.id, dx: Math.min(dx, 90) }
    //Отдача в момент ПЕРЕСЕЧЕНИЯ порога, а не при отпускании: она отвечает на вопрос
    //«уже хватит тянуть?», который человек задаёт себе ВО ВРЕМЯ жеста. Сработай она
    //в конце — сообщила бы о том, что и так видно по появившейся цитате.
    if (!wasArmed && swipe.value.dx >= SWIPE_REPLY_PX) haptics.tap()
  }
}

function onTouchEnd() {
  clearTimeout(pressTimer)
  const s = swipe.value
  const msg = touchStart?.msg
  swipe.value = { id: 0, dx: 0 }
  touchStart = null
  if (msg && s.id === msg.id && s.dx >= SWIPE_REPLY_PX && !msg.deleted) {
    m.setReply(msg)                       // свайп вправо = ответить
    nextTick(() => composer.value?.focus())
  }
}

async function onPick(action) {
  const msg = overlay.value.message
  const picked = (overlay.value.selection || '').trim()
  if (!msg) return
  if (action === 'reply') { m.setReply(msg); await nextTick(); composer.value?.focus() }
  //«Ответить с цитатой»: в ответ уезжает ВЫДЕЛЕННЫЙ кусок, а не всё сообщение. Сервер
  //проверит, что кусок реально есть в оригинале (подделать чужие слова нельзя).
  else if (action === 'quote-reply') {
    m.setReply(msg, picked)
    await nextTick()
    composer.value?.focus()
  }
  else if (action === 'copy-selection') {
    if (await copyText(picked)) flashCopied()
    else m.setNotice(locale.t('chatThread.copyFailed', 'Не удалось скопировать: браузер не дал доступ к буферу обмена.'))
  }
  else if (action === 'copy-link') {
    if (await copyText(messageLink(msg))) flashCopied()
    else m.setNotice(locale.t('chatThread.copyFailed', 'Не удалось скопировать: браузер не дал доступ к буферу обмена.'))
  }
  else if (action === 'pin') await m.setPinned(msg.id, true)
  else if (action === 'unpin') await m.setPinned(msg.id, false)
  //Копирование — через utils/clipboard (с фолбэком): в десктопном веб-виде и по HTTP
  //navigator.clipboard недоступен. Неудачу ПОКАЗЫВАЕМ: раньше ошибку глотал пустой
  //catch, и кнопка молча «просто нажималась».
  else if (action === 'copy') {
    if (await copyText(msg.body || '')) flashCopied()
    else m.setNotice(locale.t('chatThread.copyFailed', 'Не удалось скопировать: браузер не дал доступ к буферу обмена.'))
  }
  else if (action === 'translate') tr.toggleMessage(msg.id, msg.body)
  else if (action === 'forward') forwardState.value = { open: true, ids: [msg.id] }
  else if (action === 'select') m.enterSelection(msg.id)
  else if (action === 'delete') requestDelete([msg])
  else if (action === 'report') reportMsg.value = msg
  else if (action === 'remind') remindMsg.value = msg
  else if (action === 'speak') speakMessage(msg)
  else if (action === 'reactions-info') showMessageInfo(msg)
}

// ── «Зачитать сообщение» (Discord-style) — той же говорилкой, что у Вектора (§5.3) ──────
function speakMessage(msg) {
  const name = speakerName(msg)
  const phrase = name
    ? locale.t('chatThread.speakPhrase', { name, text: msg.body || '' })
    : (msg.body || '')
  tts.unlock()   // «разбудить» AudioContext из жеста клика — иначе первая фраза не прозвучит
  // forceVoice: true — сообщение зачитывается речью ВСЕГДА, даже если у Вектора выбран
  // «бубнёж»/выключено (живой отзыв: бубнёж читал имя+текст неразборчивым набором
  // «блипов», хотя это не ответ Вектора, а прямая цитата). Настройку режима не трогаем —
  // следующий ответ самого Вектора продолжит звучать так, как выбрано в настройках.
  tts.speak(phrase, { forceVoice: true })
}

// ── «Реакции» — своё сообщение: кто поставил реакцию + кто просмотрел (когда), в одной
// панели (аналог Telegram «Message Info»). Реакции — GET .../reactions (уже был на
// сервере, просто не вызывался ни с одной страницы); просмотры — тот же readBy(), что и
// у попапа «Кто прочитал» под сообщением, только со временем (last_read_at участника —
// отдельной метки «прочитал ИМЕННО ЭТО сообщение тогда-то» у нас нет, см. докстринг
// эндпоинта на сервере).
const msgInfoPopup = ref({ open: false, reactions: [], viewed: [] })
async function showMessageInfo(msg) {
  msgInfoPopup.value = { open: true, reactions: [], viewed: [] }
  const [reactRes, viewedRes] = await Promise.all([
    messengerApi.reactionUsers(msg.id).then((r) => r.data.reactions || []).catch(() => []),
    m.readBy(msg.id),
  ])
  msgInfoPopup.value = { open: true, reactions: reactRes, viewed: viewedRes }
}

// §D19: напоминание о сообщении. Дату из текста разбирает сервер (детерминированно),
// диалог только показывает её и даёт поправить.
const remindMsg = ref(null)

// §D18: сводка переписки. Запускается ТОЛЬКО кнопкой — автоматический пересказ при каждом
// открытии чата стоил бы запроса к модели на каждый вход, а прочитали бы его единицы.
const summary = ref({ open: false, text: '', loading: false, note: '' })
async function openSummary() {
  summary.value = { open: true, text: '', loading: true, note: '' }
  try {
    const { data } = await messengerApi.chatSummary(activeId.value)
    if (data.summary) summary.value = { open: true, text: data.summary, loading: false, note: '' }
    else summary.value = {
      open: true, text: '', loading: false,
      // Честно говорим, ЧТО не так, вместо выдуманного пересказа.
      note: data.reason === 'too_short'
        ? locale.t('chatThread.summaryTooShort', 'В переписке пока слишком мало сообщений, чтобы было что пересказывать.')
        : locale.t('chatThread.summaryUnavailable', 'ИИ-модель сейчас недоступна — сводку сделать не удалось. Настройки модели у администратора.'),
    }
  } catch {
    summary.value = { open: true, text: '', loading: false, note: locale.t('chatThread.summaryFailed', 'Не удалось получить сводку.') }
  }
}

function flashCopied() { copied.value = true; setTimeout(() => { copied.value = false }, 1200) }

// Удаление: если ВСЕ цели свои — предлагаем «у себя / у всех»; иначе (есть чужие) — только у себя.
function requestDelete(msgs) {
  if (msgs.length && msgs.every(x => x.mine)) { deleteTargets.value = msgs; return }
  msgs.forEach(x => m.removeMessage(x.id, 'self'))
  m.clearSelection()
}
async function applyDelete(scope) {
  const targets = deleteTargets.value || []
  deleteTargets.value = null
  for (const x of targets) await m.removeMessage(x.id, scope)
  m.clearSelection()
}

async function onReportSubmit(reason, description) {
  const msg = reportMsg.value
  reportMsg.value = null
  if (msg) await m.reportMessage(msg.id, reason, description)
}

async function onForwardSubmit(convIds) {
  const ids = forwardState.value.ids
  forwardState.value = { open: false, ids: [] }
  await m.forwardMessages(ids, convIds)
  m.clearSelection()
}

// Панель выделения.
const selectedMsgs = computed(() => messages.value.filter(x => selectedIds.value.includes(x.id)))
function bulkForward() {
  if (selectedIds.value.length) forwardState.value = { open: true, ids: [...selectedIds.value] }
}
function bulkDelete() { requestDelete(selectedMsgs.value) }

// ── Личный вопрос Вектору из общей беседы ──────────────────────────────────────────
// Ответ приходит ТОЛЬКО спросившему и НИГДЕ не сохраняется (см. серверную ручку
// `vector_in_chat`): в группе реплика Вектора показала бы соседям выборку, скоупленную
// по спросившему. Поэтому в общих беседах команда не отправляет сообщение вовсе — ни
// вопрос, ни ответ; это личное обращение к помощнику из поля ввода.
//
// ⚠️ В «Избранном» поведение ПРЕЖНЕЕ — обычные сообщения через `m.send`. Там собеседника
// нет, скрывать не от кого, а история разговора с Вектором как раз полезна.
const VECTOR_CMD = /^\/vector\s+([\s\S]+)$/i
const vectorReply = ref(null)     // { question, text, pending } — живёт до перезагрузки
const vectorBusy = ref(false)

function isVectorAsk(text) {
  return activeKind.value !== 'saved' && VECTOR_CMD.test(text)
}

async function askVector(text) {
  const question = (text.match(VECTOR_CMD) || [])[1]?.trim()
  if (!question) return
  vectorBusy.value = true
  vectorReply.value = { question, text: '', pending: true }
  try {
    const { data } = await messengerApi.askVectorInChat(activeId.value, question)
    vectorReply.value = { question, text: data.text || '', pending: false }
  } catch (e) {
    //Отказ показываем ЯВНО, а не молча гасим карточку: тишина после нажатия читается
    //как «сломалось», и человек повторяет вопрос — а при 429 это ровно то, чего делать
    //не надо. Текст берём серверный (там названа причина: мьют, частота, пустой вопрос).
    vectorReply.value = {
      question, pending: false,
      text: e?.response?.data?.detail
        || locale.t('chatThread.vectorFailed', 'Вектор сейчас не отвечает.'),
    }
  } finally { vectorBusy.value = false }
}

async function submit() {
  let t = draft.value.trim()
  if (!t) return
  //Перехват ДО автоперевода: вопрос помощнику переводить собеседнику незачем — он его
  //и не увидит.
  if (isVectorAsk(t)) {
    draft.value = ''
    m.clearDraft(activeId.value)
    await askVector(t)
    return
  }
  //Автоперевод исходящих. Делается ЗДЕСЬ, до отправки, а не на сервере: человек обязан
  //увидеть, что уйдёт собеседнику. Сбой переводчика возвращает исходный текст — съесть
  //уже написанное сообщение из-за недоступной модели недопустимо (см. stores/translate).
  if (tr.enabled) {
    const translated = await tr.outgoing(t)
    if (translated && translated !== t) {
      t = translated
      draft.value = t          //показываем результат в поле: отправляем именно это
    }
  }
  draft.value = ''
  m.clearDraft(activeId.value)
  const ok = await m.send(t)
  if (!ok) { draft.value = t; m.saveDraft(activeId.value, t) }   //отклонили — вернуть текст и черновик
}
//Ответ Вектора относится к ТОЙ беседе, где его спросили: при переходе в другую он
//обязан исчезнуть, иначе выглядит ответом на здешний разговор.
watch(activeId, () => { vectorReply.value = null })

function onKey(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }

// ── Быстрые ответы и шаблоны преподавателя (docs/MESSENGER-ADDON-PLAN-GPT.md) ───────────
// Фиксированный универсальный набор — клиент-only, без AI: короткая типовая реплика одним
// кликом, отправляется СРАЗУ (как бот-команды в Slack/Teams), а не просто вставляется.
const FIXED_QUICK_REPLIES = computed(() => [
  locale.t('chatThread.quick.accepted', '👍 Принято'),
  locale.t('chatThread.quick.thanks', 'Спасибо!'),
  locale.t('chatThread.quick.ok', 'Хорошо'),
  locale.t('chatThread.quick.got', 'Понял(а)'),
  locale.t('chatThread.quick.willCheck', 'Уточню и отвечу'),
])
const showQuickReplies = ref(false)
const newTemplateText = ref('')
// Что показать в колесе: общий набор + личные шаблоны преподавателя. `removable` отличает
// свои от общих — удалить можно только своё.
const quickReplyItems = computed(() => [
  ...FIXED_QUICK_REPLIES.value.map((text, i) => ({ id: `fix-${i}`, text, removable: false })),
  ...(templates.value || []).map((t) => ({ id: `tpl-${t.id}`, tplId: t.id, text: t.body, removable: true })),
])
async function sendQuickReply(text) {
  showQuickReplies.value = false
  await m.send(text)
}
function onQuickReplyPick(item) { sendQuickReply(item.text) }
function onQuickReplyRemove(item) { if (item.tplId) m.removeTemplate(item.tplId) }

// Уход из поля гасит облачко. Кнопки самого облачка сюда не приводят: у них
// `mousedown.prevent`, то есть фокус из поля не уходит вовсе — иначе выделение снималось
// бы раньше, чем нажатие успевало сработать.
function onComposerBlur() { fmtBubble.value.open = false }
async function addTemplateFromInput() {
  const t = newTemplateText.value.trim()
  if (!t) return
  newTemplateText.value = ''
  await m.addTemplate(t)
}

// ── Треды: ответы на сообщение (переиспользует reply_to_id, см. messenger.js) ───────────
const threadParent = computed(() => messages.value.find(x => x.id === activeThread.value?.parentId) || null)
function replyInThread() {
  if (threadParent.value) m.setReply(threadParent.value)
  m.closeThread()
  nextTick(() => composer.value?.focus())
}

// ── Поиск внутри чата ────────────────────────────────────────────────────────────────
// Панель поиска открывается напрямую из панели беседы (ConversationInfo → @search),
// собственной кнопки в шапке чата больше нет (убрана как дубль в 3.8) — поэтому
// отдельного toggle не осталось, только явное закрытие.
const showSearch = ref(false)
const searchQ = ref('')
let searchTimer = null
function closeSearchPanel() { showSearch.value = false; searchQ.value = ''; m.clearSearch() }
function onSearchInput() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => m.searchInActive(searchQ.value), 300)
}
function jumpToSearchResult(msg) {
  closeSearchPanel()
  jumpTo(msg.id)
}

// ── Кто прочитал сообщение (по клику, popup) — переиспользует last_read_at участников ───
const readByPopup = ref({ open: false, names: [] })
async function showReadBy(msg) {
  const users = await m.readBy(msg.id)
  readByPopup.value = { open: true, names: users.map(u => u.full_name) }
}

// ── Галочки отправлено/прочитано в ЛС (как в Telegram) ──────────────────────────────────
// В группах/каналах читателей несколько — там смысла в одной галочке нет, оставляем
// попап «Кто прочитал» (см. выше). В ЛС читатель ровно один — сравниваем время своего
// сообщения с last_read_at собеседника, которое уже приходит в activeInfo.participants
// (см. server/app/routers/messenger.py::conversation_info) — без похода на сервер за каждым тиком.
const peerLastReadAt = computed(() => {
  if (kind.value !== 'direct' || !activePeer.value?.id) return ''
  const p = (activeInfo.value?.participants || []).find(x => x.user_id === activePeer.value.id)
  return p?.last_read_at || ''
})
function isReadByPeer(msg) {
  return !!(peerLastReadAt.value && msg.created_at <= peerLastReadAt.value)
}

function jumpTo(id) {
  const el = document.getElementById(`gb-msg-${id}`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

// ── Отметки: перемотка к сообщению, где меня упомянули ────────────────────────────────
// Само сообщение приходит в строке списка чатов (mention_message_id) — сервер уже нашёл
// самое РАННЕЕ непрочитанное упоминание, второй раз искать его на клиенте незачем.
const mentionMessageId = computed(() => activeChat.value?.mention_message_id || 0)
const mentionLoud = computed(() => !!activeChat.value?.mention_loud)
const flashMentionId = ref(0)
// Подсветка на пару секунд: без неё после прокрутки непонятно, какое именно сообщение
// искали — в плотной переписке центр экрана ни на что не указывает.
// ⚠️ Имя общее (`flashMessage`), а не `flashMention`: подсветку зовут ТРИ пути — отметка,
// переход по цитате и ссылка на сообщение, — и название, говорящее только про отметки,
// соврало бы читателю уже на втором.
function flashMessage(id) {
  if (!id) return
  flashMentionId.value = id
  setTimeout(() => { if (flashMentionId.value === id) flashMentionId.value = 0 }, 2500)
}
function jumpToMention() {
  const id = mentionMessageId.value
  if (!id) return
  jumpTo(id)
  flashMessage(id)
}

// ── Ссылка на сообщение (пункт меню по выделению) ────────────────────────────────────
// ⚠️ Ссылка ОТНОСИТЕЛЬНАЯ и ведёт внутрь кабинета: беседа откроется только участнику, а
// постороннему сервер ответит 403. Это навигация, а не способ поделиться перепиской.
// ⚠️ В ЛИЧНОЙ переписке такого пункта нет вовсе (см. utils/messageMenu.js): «ссылка на
// сообщение» из чата на двоих либо не откроется у третьего, либо откроет ему чужое.
const route = useRoute()
const canLink = computed(() => isGroupOrChannel.value)
function messageLink(msg) {
  const q = new URLSearchParams({ chat: activeId.value || '', msg: String(msg.id) })
  return `${location.origin}${route.path}?${q.toString()}`
}
// Закреплять: в личном чате — оба, в канале — авторы, в группе — владелец/админ
// (зеркалит серверный `_can_pin`; это подсказка, отказ всё равно даёт сервер).
const canPin = computed(() => {
  if (!isGroupOrChannel.value) return true
  const role = activeInfo.value?.my_role
  return isChannel.value
    ? ['owner', 'admin', 'writer'].includes(role)
    : ['owner', 'admin'].includes(role)
})

// Перемотка по ссылке `?msg=…`: стор просит, лента исполняет — к этому моменту сообщения
// уже отрисованы, а `openById` вернул управление задолго до того.
watch(() => [m.pendingJump, messages.value.length], async () => {
  const id = m.pendingJump
  if (!id) return
  await nextTick()
  if (!document.getElementById(`gb-msg-${id}`)) return   //ещё не догрузилась — ждём тика
  jumpTo(id)
  flashMessage(id)
  m.pendingJump = 0
}, { immediate: true })

// «Избранное» — личный блокнот, а не переписка: ни собеседника, ни его статуса тут нет.
const isSaved = computed(() => kind.value === 'saved')
const peerName = computed(() => {
  if (isSaved.value) return locale.t('messenger.saved', 'Избранное')
  if (isModeration.value) return locale.t('nav.moderation', 'Модерация')
  if (isGroupOrChannel.value) return activeInfo.value?.title || activePeer.value?.full_name || locale.t('messenger.dialog', 'Беседа')
  return activePeer.value?.full_name || locale.t('conversationInfo.dialog', 'Диалог')
})
// §D7: подпись статуса поверх presence (dnd/studying/away + текст преподавателя).
const subtitle = computed(() => {
  //Свой блокнот: показывать «в сети»/роль бессмысленно — это ты сам.
  if (isSaved.value) return locale.t('chatThread.notesOnlyForYou', 'Заметки только для вас')
  if (isModeration.value) return locale.t('chatThread.officialSupport', 'Официальная поддержка')
  if (peerTyping.value) return locale.t('chatThread.typing', 'печатает…')
  if (kind.value === 'channel') return locale.t('conversationInfo.subscribersCount', { n: activeInfo.value?.subscribers || 0 })
  if (kind.value === 'group') return locale.t('conversationInfo.membersCount', { n: activeInfo.value?.subscribers || 0 })
  const sk = activePeer.value?.status_kind
  if (sk) return statusLabel(sk, activePeer.value?.status_text)
  return activePeer.value?.online ? locale.t('profilePanel.online', 'в сети') : locale.t('chatThread.wasRecently', 'был(а) недавно')
})
const topPinned = computed(() => pinned.value[0] || null)
//Подсказка в поле ввода: разметку показывают кнопки тулбара, поэтому в ней только то,
//что иначе не найти — команда Вектора, и лишь в «Избранном», где она и работает.
const composerHint = computed(() => {
  if (replyingToVector.value) return locale.t('chatThread.askVectorMore', 'Спросите Вектора дальше…')
  return isSaved.value ? locale.t('chatThread.notePlaceholder', 'Заметка… (/vector — спросить ИИ)') : locale.t('chatThread.messagePlaceholder', 'Сообщение…')
})

// ── Оформление ленты (как в Telegram) ────────────────────────────────────────────────
// Свои сообщения — справа, чужие — слева с аватаркой и ФИО. Если человек пишет НЕСКОЛЬКО
// подряд, шапка (ава + имя) рисуется только у ВЕРХНЕГО сообщения пачки — лента не рябит.
const runStarts = computed(() => {
  const starts = new Set()
  let prev = null
  for (const msg of messages.value) {
    if (msg.sender_id !== prev) starts.add(msg.id)
    prev = msg.sender_id
  }
  return starts
})

// §5.4: стиль никнейма отправителя — та же карта, что и аватарки (состав беседы или
// собеседник ЛС). Применяем ТОЛЬКО здесь и в PeerProfileCard/Profile.vue — заказчик
// прямо просил не разносить это по остальным вкладкам (журнал/расписание и т.п.).
// Держим участника ЦЕЛИКОМ, а не одно поле шрифта: стиль имени — это тройка
// «шрифт + эффект + цвет», и nameDecor берёт её сама (цвет умеет наследоваться от
// цвета профиля, поэтому обрезать объект до пары полей нельзя).
const styleBySender = computed(() => {
  const map = {}
  for (const p of activeInfo.value?.participants || []) map[p.user_id] = p
  if (activePeer.value?.id) map[activePeer.value.id] = activePeer.value
  return map
})
function senderDecor(msg) {
  // Как и senderName(msg) чуть ниже — вызывается ТОЛЬКО для чужих сообщений (шаблон
  // гейтит `v-if="!msg.mine"`), поэтому свой стиль здесь разбирать не нужно.
  return nameDecor(styleBySender.value[msg.sender_id] || activePeer.value || {})
}

// Аватарки отправителей: в группе/канале — из состава беседы, в личном чате — собеседника.
const avatarBySender = computed(() => {
  const map = {}
  for (const p of activeInfo.value?.participants || []) map[p.user_id] = p.avatar || ''
  return map
})
// Ответы Вектора приходят от служебного отправителя 'system'. Его нет в справочнике
// пользователей, поэтому подпись и аватар подставлялись от СОБЕСЕДНИКА — в «Избранном»
// это ты сам, и ответ ИИ выглядел как твоё же сообщение с твоей аватаркой.
const VECTOR_SENDER = 'system'
//Отдельный файл, а НЕ спрайт настроения. Раньше сюда подставлялся `neutral-idle` —
//спрайт из набора эмоций: он рисован в полный рост, и в кружке 32 px от Вектора
//оставалась неразличимая фигурка. Этот файл нарисован именно как аватарка (голова
//крупно), с прозрачным фоном — иначе белый угол светил бы бельмом на тёмной теме.
//Набор эмоций не трогаем: он живёт своей жизнью и используется дашбордом.
const VECTOR_AVATAR = '/mascot/vector-avatar.webp'
function isVector(msg) { return msg.sender_id === VECTOR_SENDER }
//Отвечаем на реплику Вектора → это продолжение разговора с ним, а не обычная цитата.
const replyingToVector = computed(() => isSaved.value && !!replyTo.value && isVector(replyTo.value))
function senderAvatar(msg) {
  if (isVector(msg)) return VECTOR_AVATAR
  return avatarBySender.value[msg.sender_id] ?? (msg.mine ? '' : (activePeer.value?.avatar || ''))
}
function senderName(msg) {
  if (isVector(msg)) return locale.t('chatThread.vectorName', 'Вектор')
  return msg.sender_name || (isSaved.value ? '' : (activePeer.value?.full_name || ''))
}
// «Зачитать сообщение» — senderName(msg) выше НАМЕРЕННО не считает своё (гейтится
// v-if="!msg.mine" в шаблоне), а для озвучки имя нужно и на своих сообщениях тоже.
function speakerName(msg) {
  if (isVector(msg)) return locale.t('chatThread.vectorName', 'Вектор')
  if (msg.mine) return auth.user?.name || auth.user?.login || ''
  return senderName(msg)
}
// Роль/цвет отправителя для значка-аватарки по умолчанию (RoleAvatarIcon, см. Avatar.vue) —
// тот же приём, что avatarBySender выше. ⚠️ p.user_role (роль В СИСТЕМЕ), НЕ p.role (та —
// роль УЧАСТНИКА БЕСЕДЫ owner/admin/writer/…, см. предупреждение в ConversationInfo.vue).
const roleBySender = computed(() => {
  const map = {}
  for (const p of activeInfo.value?.participants || []) map[p.user_id] = p.user_role || ''
  return map
})
const colorBySender = computed(() => {
  const map = {}
  for (const p of activeInfo.value?.participants || []) map[p.user_id] = p.profile_color || ''
  return map
})
function senderRole(msg) {
  if (isVector(msg)) return ''
  return roleBySender.value[msg.sender_id] ?? (msg.mine ? '' : (activePeer.value?.role || ''))
}
function senderColor(msg) {
  if (isVector(msg)) return ''
  const id = colorBySender.value[msg.sender_id] ?? (msg.mine ? '' : (activePeer.value?.profile_color || ''))
  return profilePlate(id)
}

// Шапка чата — в цвет плашки, которую собеседник выбрал в своём профиле.
const headerTint = computed(() =>
  (!isGroupOrChannel.value && !isModeration.value && activePeer.value?.profile_color)
    ? profilePlate(activePeer.value.profile_color) : '')

// Стиль никнейма собеседника — ТОЖЕ «сверху в мессенджере» (заголовок открытого чата),
// не только у сообщений/списка чатов/карточки. Только для личного чата с реальным
// человеком — у «Избранного»/«Модерации»/групп-каналов peerName не имя человека.
const peerNameDecor = computed(() =>
  (!isGroupOrChannel.value && !isSaved.value && !isModeration.value)
    ? nameDecor(activePeer.value || {}) : {})

// §правка (3.8): поиск, сводка и мьют переехали в панель беседы (ConversationInfo,
// открывается кликом по шапке) — держать их вторым набором иконок в шапке было дублем
// (указал Влад). В шапке остались только перевод (в панели его нет) и активности; на
// телефоне — прямая иконка перевода. Прежнего мобильного меню-дропдауна больше нет.

// §правка: Telegram-style «здесь ничего нет» + случайная гифка-приветствие — ТОЛЬКО
// для личных чатов без единого сообщения (не групп/каналов/«Избранного»/модерации —
// там либо бессмысленно, либо неуместно). Запрос к Klipy — фиксированное "hello"
// (англоязычная база, у "привет" выдача заметно скуднее), случайный элемент с ПЕРВОЙ
// страницы — заводить отдельный эндпоинт ради одной картинки не нужно, переиспользуем
// уже существующий gifSearch (тот же, что у GifPicker.vue).
const isNewDirectConversation = computed(() => kind.value === 'direct' && !messages.value.length)
const greetingGif = ref(null)
const greetingGifLoading = ref(false)
async function loadGreetingGif() {
  greetingGifLoading.value = true
  try {
    const { data } = await messengerApi.gifSearch('hello')
    const items = data.items || []
    greetingGif.value = items.length ? items[Math.floor(Math.random() * items.length)] : null
  } catch { greetingGif.value = null } finally { greetingGifLoading.value = false }
}
watch(isNewDirectConversation, (on) => { if (on) loadGreetingGif(); else greetingGif.value = null }, { immediate: true })
// Клик по гифке = отправка ЕЁ ЖЕ, без промежуточного пикера (тот же sendGif, что и у
// GifPicker.vue) — «нажимаем и отправляется именно она».
async function sendGreetingGif() { if (greetingGif.value) await m.sendGif(greetingGif.value) }

// Меню «⋮» в шапке — ТОЛЬКО на телефоне. Образец взят у строки чата в `ChatList.vue`,
// чтобы два меню в одном разделе открывались и закрывались одинаково.
// ⚠️ Меню собрано не ради чистоты шапки, а потому что кнопка активностей на узком
// экране не показывалась ВООБЩЕ: она жила в блоке `hidden sm:flex`, и другой двери в
// подсистему в интерфейсе нет (команду `/активность` удалили). То есть на телефоне —
// а значит и в приложении RuStore, это та же SPA — ни запустить активность, ни войти
// в идущую было нельзя.
const headerMenu = ref(false)
function closeHeaderMenu() { headerMenu.value = false }
// Смена беседы обязана закрывать меню: иначе оно висит поверх ДРУГОГО чата, и его
// пункты («написать модерации», «активности») относятся уже не к тому, что на экране.
// ⚠️ Вотчер объявлен ЗДЕСЬ, а не рядом с остальными наверху файла: там он читал бы
// `headerMenu` из мёртвой зоны — ровно та грабля, что описана в §7.1.
watch(activeId, closeHeaderMenu)

// ⚠️ Условие видимости активностей — ОДНО на обе точки показа (кнопка на ПК и пункт
// меню на телефоне). Двумя копиями оно разъехалось бы молча и в худшую сторону: на
// одной ширине дверь есть, на другой нет, и понять это можно только открыв продукт
// на втором устройстве.
const canOpenActivities = computed(() =>
  isGroupLike.value && (canRunActivity.value || activity.running.length > 0))
// Куда ведёт кнопка, зависит от прав: кто вправе — в выбор категории, кто нет — сразу
// в идущую активность (ради неё он и нажал). Пояснение — у кнопки в разметке.
function openActivities() {
  closeHeaderMenu()
  if (canRunActivity.value) activity.openLauncher(activeId.value)
  else if (activity.running.length) activity.open(activity.running[0].id)
}
</script>

<template>
  <section class="flex min-w-0 flex-1 flex-col bg-bg" :class="{ 'hidden sm:flex': !activeId }">
    <div v-if="!activeId" class="grid flex-1 place-items-center p-6 text-center text-sm text-text3">
      {{ locale.t('chatThread.pickChat', 'Выберите чат слева или найдите человека через поиск.') }}
    </div>

    <template v-else>
      <!-- Верхняя полоска / панель выделения -->
      <!-- Шапка — в цвет плашки собеседника (её выбирает он сам в профиле). -->
      <div v-if="!selectionMode" class="flex h-14 shrink-0 items-center gap-3 border-b border-border px-3"
           :class="headerTint ? '' : 'bg-card'" :style="headerTint ? { background: headerTint } : {}">
        <button type="button" @click="m.clearActive()" :aria-label="locale.t('chatThread.back', 'Назад')"
                class="grid size-8 place-items-center rounded-md hover:bg-black/10 sm:hidden"
                :class="headerTint ? 'text-white' : 'text-text2'">
          <ArrowLeft class="size-5" />
        </button>
        <!-- Клик по шапке — профиль собеседника / состав беседы (как в Telegram). -->
        <button type="button" @click="showInfo = true"
                class="min-w-0 flex-1 rounded-md px-1 py-0.5 text-left transition-colors"
                :class="headerTint ? 'hover:bg-white/10' : 'hover:bg-bg2'"
                :title="locale.t('chatThread.openProfile', 'Открыть профиль')">
          <div class="truncate font-title text-base font-bold"
               :class="headerTint ? 'text-white' : 'text-text'"
               v-bind="peerNameDecor">{{ peerName }}</div>
          <div class="text-xs" :class="headerTint ? 'text-white/75' : 'text-text3'">{{ subtitle }}</div>
        </button>

        <!-- Перевод и активности — на sm+ прямо в строке. Поиск, сводка и мьют переехали
             в панель беседы (ConversationInfo, открывается кликом по шапке): держать их
             ЗДЕСЬ вторым набором — тот самый дубль, на который указал Влад (3.8). Перевод
             в панели нет, активности — отдельная подсистема, поэтому эти две остаются. -->
        <div class="hidden items-center gap-1 sm:flex">
          <!-- 🌐 — настройки перевода переписки. Ярче обычного, когда автоперевод включён:
               человек должен видеть, что его сообщения уходят переведёнными, а не гадать. -->
          <button type="button" @click="showTranslate = true"
                  :aria-label="tr.enabled ? locale.t('chatThread.autoTranslateOn', 'Автоперевод включён') : locale.t('translate.title', 'Перевод')"
                  :title="tr.enabled ? locale.t('chatThread.autoTranslateOn', 'Автоперевод включён') : locale.t('chatThread.configureTranslate', 'Настроить перевод')"
                  class="grid size-8 shrink-0 place-items-center rounded-md"
                  :class="headerTint ? 'text-white/80 hover:bg-white/15 hover:text-white'
                    : (tr.enabled ? 'text-accent hover:bg-bg2' : 'text-text3 hover:bg-bg2 hover:text-text')">
            <Languages class="size-5" />
          </button>
          <!-- Журнал активностей беседы (PLAN-ACTIVITIES §9). Только в группах и каналах:
               в личном чате и «Избранном» активностей не бывает, и пустая кнопка там
               читалась бы как поломка. -->
          <!-- Кнопка ведёт в САМИ АКТИВНОСТИ, а не в журнал: журнал — редкий взгляд
               назад, а запуск и вход — ежедневное действие, и держать его за командой
               `/активность` значило прятать главное. Журнал открывается изнутри окна
               активностей. Восклицательный знак — в беседе началась активность, которую
               ещё не открывали.
               🔥 ДЕЛАЕТ РАЗНОЕ У РАЗНЫХ РОЛЕЙ, и это не украшение. `ActivityLauncher` —
               чистое «ЗАПУСТИТЬ»: ни входа в идущую активность, ни проверки прав внутри
               него нет. Пока дверью была команда `/активность`, право проверял сервер ДО
               открытия окна и отвечал внятным отказом; кнопка эту проверку не унаследовала,
               и студент, нажав на «!», доходил до выбора категории и получал 403 — ровно
               тот «самый обидный способ узнать, что тебе нельзя», от которого защищался
               удалённый обработчик. Поэтому: кто вправе — открывает выбор категории, кто
               нет — попадает в ИДУЩУЮ активность (ради неё он и нажал), а если ничего не
               идёт, кнопки у него нет вовсе: пустая дверь читается как поломка. -->
          <button v-if="canOpenActivities" type="button"
                  @click="openActivities()"
                  :aria-label="locale.t('activity.open', 'Активности')"
                  :title="locale.t('activity.open', 'Активности')"
                  class="relative grid size-8 shrink-0 place-items-center rounded-md"
                  :class="headerTint ? 'text-white/80 hover:bg-white/15 hover:text-white'
                    : 'text-text3 hover:bg-bg2 hover:text-text'">
            <ClipboardList class="size-5" />
            <span v-if="activity.unseen"
                  class="absolute -right-0.5 -top-0.5 grid size-4 place-items-center rounded-full bg-red text-[10px] font-bold leading-none text-white">!</span>
          </button>
          <!-- ⚙ — открыть чат с модерацией (см. MESSENGER-PLAN.md §6) -->
          <button v-if="!isModeration && !isAdmin" type="button" @click="m.openModeration()"
                  :aria-label="locale.t('nav.moderation', 'Модерация')" :title="locale.t('chatThread.writeToModeration', 'Написать модерации')"
                  class="grid size-8 shrink-0 place-items-center rounded-md"
                  :class="headerTint ? 'text-white/80 hover:bg-white/15 hover:text-white'
                    : 'text-text3 hover:bg-bg2 hover:text-text'">
            <LifeBuoy class="size-5" />
          </button>
        </div>

        <!-- Телефон: те же действия, свёрнутые в «⋮». Раньше здесь стоял ОДИН перевод, а
             активности и модерация жили в блоке `hidden sm:flex` — то есть на узком экране
             активностей не было вовсе (см. пояснение у `canOpenActivities`).
             ⚠️ Значок «идёт активность» ДУБЛИРУЕТСЯ на самой кнопке «⋮»: свёрнутый в меню
             он не виден, а он и есть повод её открыть. -->
        <div class="relative shrink-0 sm:hidden">
          <button type="button" @click.stop="headerMenu = !headerMenu"
                  :aria-label="locale.t('messenger.actions', 'Действия')"
                  class="relative grid size-8 place-items-center rounded-md"
                  :class="headerTint ? 'text-white/80 hover:bg-white/15 hover:text-white'
                    : 'text-text3 hover:bg-bg2 hover:text-text'">
            <MoreVertical class="size-5" />
            <span v-if="canOpenActivities && activity.unseen"
                  class="absolute -right-0.5 -top-0.5 grid size-4 place-items-center rounded-full bg-red text-[10px] font-bold leading-none text-white">!</span>
          </button>
          <!-- Подложка закрывает меню кликом мимо. Отдельным элементом, а не слушателем на
               документе: слушатель надо снимать при размонтировании, и забытый снимок —
               обычный источник «меню закрывается от клика в другом чате». -->
          <div v-if="headerMenu" class="fixed inset-0 z-20" @click="closeHeaderMenu()"></div>
          <div v-if="headerMenu"
               class="absolute right-0 top-full z-30 mt-1 w-56 overflow-hidden rounded-lg border border-border2 bg-card py-1 shadow-card">
            <button type="button" @click="closeHeaderMenu(); showTranslate = true"
                    class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-bg2"
                    :class="tr.enabled ? 'text-accent' : 'text-text'">
              <Languages class="size-4 shrink-0" :class="tr.enabled ? 'text-accent' : 'text-text3'" />
              {{ tr.enabled ? locale.t('chatThread.autoTranslateOn', 'Автоперевод включён')
                            : locale.t('chatThread.configureTranslate', 'Настроить перевод') }}
            </button>
            <button v-if="canOpenActivities" type="button" @click="openActivities()"
                    class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2">
              <ClipboardList class="size-4 shrink-0 text-text3" />
              <span class="flex-1">{{ locale.t('activity.open', 'Активности') }}</span>
              <span v-if="activity.unseen"
                    class="grid size-4 shrink-0 place-items-center rounded-full bg-red text-[10px] font-bold leading-none text-white">!</span>
            </button>
            <button v-if="!isModeration && !isAdmin" type="button" @click="closeHeaderMenu(); m.openModeration()"
                    class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2">
              <LifeBuoy class="size-4 shrink-0 text-text3" />
              {{ locale.t('chatThread.writeToModeration', 'Написать модерации') }}
            </button>
          </div>
        </div>
      </div>
      <div v-else class="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card px-3">
        <button type="button" @click="m.clearSelection()" :aria-label="locale.t('common.cancel')"
                class="grid size-8 place-items-center rounded-md text-text2 hover:bg-bg2"><X class="size-5" /></button>
        <span class="flex-1 text-sm font-semibold text-text">{{ locale.t('chatThread.selectedCount', { n: selectedIds.length }) }}</span>
        <!-- «Выбрать всё / Снять всё» — иначе выделять пачку приходилось по одному. -->
        <button type="button" @click="allSelected ? m.selectNone() : m.selectAll()"
                class="shrink-0 rounded-md border border-border2 px-2.5 py-1.5 text-xs text-text2 hover:bg-bg2">
          {{ allSelected ? locale.t('chatThread.deselectAll', 'Снять всё') : locale.t('chatThread.selectAll', 'Выбрать всё') }}
        </button>
        <button type="button" @click="bulkForward" :disabled="!selectedIds.length" :aria-label="locale.t('forward.title', 'Переслать')"
                class="grid size-9 place-items-center rounded-md text-text2 hover:bg-bg2 disabled:opacity-40"><Forward class="size-5" /></button>
        <button type="button" @click="bulkDelete" :disabled="!selectedIds.length" :aria-label="locale.t('common.delete')"
                class="grid size-9 place-items-center rounded-md text-red hover:bg-bg2 disabled:opacity-40"><Trash2 class="size-5" /></button>
      </div>

      <!-- Поиск внутри чата: строка + результаты (без встроенного скролла к старым — если
           сообщение уже не подгружено в ленту, просто показываем его текст здесь). -->
      <div v-if="showSearch" class="shrink-0 border-b border-border bg-card">
        <div class="flex items-center gap-2 px-3 py-2">
          <Search class="size-4 shrink-0 text-text3" />
          <input v-model="searchQ" @input="onSearchInput" autofocus :placeholder="locale.t('chatThread.searchByMeaning', 'Поиск по смыслу…')"
                 class="h-8 min-w-0 flex-1 bg-transparent text-sm text-text outline-none" />
          <button type="button" @click="closeSearchPanel" :aria-label="locale.t('chatThread.closeSearch', 'Закрыть поиск')"
                  class="grid size-7 shrink-0 place-items-center rounded-md text-text3 hover:bg-bg2"><X class="size-4" /></button>
        </div>
        <!-- Показываем, чем модель дополнила запрос: иначе непонятно, почему нашлось
             сообщение, в котором искомого слова нет. -->
        <p v-if="searchExpanded.length && searchQ.trim()"
           class="border-t border-border px-3 py-1.5 text-[11px] text-text3">
          {{ locale.t('chatThread.alsoSearching', { terms: searchExpanded.join(', ') }) }}
        </p>
        <div v-if="searchQ.trim()" class="max-h-56 overflow-y-auto border-t border-border">
          <p v-if="searching" class="p-3 text-center text-xs text-text3">{{ locale.t('chatThread.searching', 'Ищем…') }}</p>
          <p v-else-if="!searchResults?.length" class="p-3 text-center text-xs text-text3">{{ locale.t('chatThread.nothingFound', 'Ничего не найдено.') }}</p>
          <button v-for="r in searchResults" :key="r.id" type="button" @click="jumpToSearchResult(r)"
                  class="flex w-full flex-col items-start gap-0.5 border-b border-border/50 px-3 py-2 text-left hover:bg-bg2">
            <span class="text-[11px] font-semibold text-accent">{{ r.sender_name || (r.mine ? locale.t('chatThread.you', 'Вы') : '') }} · {{ fmtTime(r.created_at) }}</span>
            <span class="line-clamp-2 text-sm text-text">{{ r.body }}</span>
          </button>
        </div>
      </div>

      <!-- Плашка закреплённого -->
      <button v-if="topPinned" type="button" @click="jumpTo(topPinned.id)"
              class="flex shrink-0 items-center gap-2 border-b border-border bg-card px-3 py-1.5 text-left">
        <Pin class="size-4 shrink-0 text-accent" />
        <div class="min-w-0">
          <div class="text-[11px] font-semibold text-accent">{{ locale.t('chatThread.pinnedLabel', 'Закреплённое') }}{{ pinned.length > 1 ? ` · ${pinned.length}` : '' }}</div>
          <div class="truncate text-xs text-text2">{{ topPinned.body }}</div>
        </div>
      </button>

      <!-- Идущий тайм-бокс — СВОЕЙ строкой сразу под шапкой: на него поглядывают,
           продолжая работать, а полноэкранное окно закрывает как раз то, над чем
           работают. Кнопки паузы и завершения — только у ведущего. -->
      <TimerStrip />

      <!-- Лента -->
      <!-- Отступы задаём НА САМОМ сообщении (mt-*), а не через space-y на контейнере:
           селектор space-y перебивал бы mt-* по специфичности, и пачки склеивались бы
           с соседними — визуальной группировки не получалось. -->
      <!-- Обёртка relative — плавающая кнопка «вниз» позиционируется absolute ОТНОСИТЕЛЬНО
           этой области (а не всей секции чата, где съезжала бы на композер). -->
      <div class="relative min-h-0 flex-1">
      <div ref="scroller" class="h-full overflow-y-auto px-3 py-4" @scroll="onScrollerScroll">
        <!-- Личный чат без единого сообщения (§правка, «как в Телеграме») — компактно,
             без баннеров на пол-экрана. -->
        <div v-if="isNewDirectConversation" class="flex flex-col items-center justify-center gap-2 py-8 text-center">
          <p class="text-sm font-medium text-text2">{{ locale.t('chatThread.emptyConversation', 'Здесь пока ничего нет') }}</p>
          <p class="text-xs text-text3">{{ locale.t('chatThread.emptyConversationHint', 'Отправьте сообщение или нажмите на гифку ниже') }}</p>
          <button v-if="greetingGif" type="button" @click="sendGreetingGif"
                  class="mt-1 overflow-hidden rounded-lg border border-border2 transition-transform hover:scale-105"
                  :title="locale.t('chatThread.sendGreetingGif', 'Отправить приветствие')">
            <img :src="greetingGif.thumb_url || greetingGif.url" alt=""
                 class="block h-28 w-auto max-w-[180px] object-cover" />
          </button>
          <p v-else-if="greetingGifLoading" class="mt-1 text-xs text-text3">{{ locale.t('common.loading') }}</p>
        </div>
        <!-- Полоска «подгружаем предыдущие»: без неё пауза на медленной сети читается как
             конец переписки. Показываем только когда действительно грузим. -->
        <div v-if="loadingOlder" class="flex justify-center py-2">
          <span class="gb-skeleton h-4 w-28 rounded-full" />
        </div>
        <!-- СКЕЛЕТ. Раньше на время загрузки здесь была пустота: шапка с именем уже
             нарисована, а лента появлялась рывком через сетевой круг — этот разрыв и
             читается как «дёшево». Заглушки держат ту же геометрию, что настоящие
             пузыри, поэтому содержимое не прыгает, когда доедет. Показываем только при
             ПЕРВОЙ загрузке беседы (messages пуст): на обновлении уже показанной ленты
             скелет означал бы шаг назад. -->
        <div v-if="loadingMessages && !messages.length" class="space-y-2" aria-hidden="true">
          <div v-for="(w, i) in [58, 72, 40, 66, 50, 78]" :key="i"
               class="flex" :class="i % 2 ? 'justify-end' : 'justify-start'">
            <div class="gb-skeleton h-9 rounded-2xl" :style="{ width: w + '%' }" />
          </div>
        </div>
        <template v-for="msg in messages" :key="msg.id">
          <!-- Разделитель по датам — над «Новые сообщения», если оба совпали на одном msg. -->
          <div v-if="dateBreaks.has(msg.id)" class="my-3 flex justify-center">
            <span class="rounded-full bg-card2 px-3 py-1 text-[11px] font-semibold text-text3">{{ dateBreaks.get(msg.id) }}</span>
          </div>
          <!-- §D5: разделитель «Новые сообщения» — перед первым непрочитанным на момент открытия. -->
          <div v-if="msg.id === firstUnreadId" class="my-3 flex items-center gap-3 text-xs font-semibold text-accent">
            <span class="h-px flex-1 bg-accent/40" /> {{ locale.t('chatThread.newMessages', 'Новые сообщения') }} <span class="h-px flex-1 bg-accent/40" />
          </div>

          <!-- §D6: системное сообщение — центрированная строка, не пузырь. -->
          <div v-if="msg.kind === 'system'" :id="`gb-msg-${msg.id}`" class="my-1.5 text-center text-xs text-text3">
            {{ formatSystemMessage(msg.body) }}
          </div>

          <!-- §12: кнопка «Отчёт №N» куратора. Группу и дату границы пишем НА кнопке:
               без них подряд лежащие отчёты неразличимы, и непонятно, сработала ли
               команда — куратор жал «/отчет» ещё раз и плодил дубли. -->
          <div v-else-if="msg.kind === 'report' && msg.report" :id="`gb-msg-${msg.id}`"
               class="my-1 flex" :class="msg.mine ? 'justify-end' : 'justify-start'">
            <button type="button" @click="openReportOverlay = msg.report.id"
                    class="flex items-center gap-2 rounded-2xl border border-border2 bg-card px-4 py-2 text-sm font-semibold text-accent shadow-sm hover:bg-bg2">
              <PieChart class="size-4 shrink-0" />
              <span class="flex flex-col items-start leading-tight">
                <span>{{ locale.t('chatThread.reportNumber', { n: msg.report.seq }) }}<span v-if="msg.report.group" class="text-text2"> · {{ msg.report.group }}</span></span>
                <span class="text-[11px] font-medium text-text3">
                  {{ msg.report.cutoff_date ? locale.t('curatorReport.asOf', { date: msg.report.cutoff_date }) : locale.t('chatThread.groupPerformance', 'успеваемость группы') }}
                  · {{ fmtTime(msg.created_at) }}
                </span>
              </span>
              <span v-if="msg.report.archived" class="rounded-full bg-bg2 px-1.5 py-0.5 text-[10px] font-medium text-text3">{{ locale.t('curatorReport.archived', 'Архив') }}</span>
            </button>
          </div>

          <!-- Активность и сохранённая доска (PLAN-ACTIVITIES §10). Тот же приём, что у
               отчёта выше: в теле сообщения только id, объект подмешал сервер. -->
          <!-- Опрос голосуется ПРЯМО в ленте (как в Telegram), а не открывает оверлей.
               ⚠️ `gb-feed-indent` — та же левая граница, что у входящих сообщений. У них
               колонка аватарки (32 px + зазор) держится ВСЕГДА, даже когда сам аватар не
               рисуется: иначе пачка сообщений одного человека разъезжалась бы по уровням.
               Карточки поначалу вставили мимо этой строки, они прижимались к самому краю,
               и на телефоне лента выглядела как две разные левые границы — сообщения
               «уехали к центру» относительно карточек (живой отзыв со скриншотом). -->
          <div v-else-if="msg.kind === 'poll' && msg.activity" :id="`gb-msg-${msg.id}`"
               class="flex" :class="msg.mine ? 'justify-end' : 'justify-start gb-feed-indent'">
            <PollMessage :activity="msg.activity" />
          </div>
          <div v-else-if="msg.kind === 'activity' && msg.activity" :id="`gb-msg-${msg.id}`">
            <!-- Разделитель активности НЕ сдвигаем: он и должен идти во всю ширину по
                 центру — это событие беседы, а не чья-то реплика. -->
            <ActivityCard :activity="msg.activity" :created-at="msg.created_at" />
          </div>
          <div v-else-if="msg.kind === 'board' && msg.board" :id="`gb-msg-${msg.id}`"
               class="flex" :class="msg.mine ? 'justify-end' : 'justify-start gb-feed-indent'">
            <BoardCard :board="msg.board" />
          </div>

          <div v-else :id="`gb-msg-${msg.id}`"
               class="flex items-end gap-2"
               :class="[msg.mine ? 'justify-end' : 'justify-start',
                        runStarts.has(msg.id) ? 'mt-4 first:mt-0' : 'mt-0.5']">
            <input v-if="selectionMode" type="checkbox" :checked="selectedIds.includes(msg.id)"
                   @change="m.toggleSelect(msg.id)" class="order-first accent-[var(--gb-accent)]" />
            <!-- Аватарка собеседника — только у верхнего сообщения пачки; ниже держим отступ. -->
            <div v-if="!msg.mine" class="w-8 shrink-0">
              <!-- Аватарка ведёт в профиль автора. Кнопкой она становится ТОЛЬКО когда
                   профиль есть кому открыть (не Вектор, не системное) — иначе получилась
                   бы кнопка, которая ничего не делает. -->
              <button v-if="runStarts.has(msg.id) && canOpenSenderProfile(msg)" type="button"
                      @click="openSenderProfile(msg)"
                      class="rounded-full outline-none ring-accent transition-opacity hover:opacity-80 focus-visible:ring-2"
                      :title="locale.t('chatThread.openSenderProfile', 'Открыть профиль')"
                      :aria-label="locale.t('chatThread.openSenderProfile', 'Открыть профиль')">
                <Avatar :src="senderAvatar(msg)" :name="senderName(msg)" :role="senderRole(msg)"
                        :color="senderColor(msg)" :size="32" position="center" />
              </button>
              <Avatar v-else-if="runStarts.has(msg.id)" :src="senderAvatar(msg)"
                      :name="senderName(msg)" :role="senderRole(msg)" :color="senderColor(msg)" :size="32"
                      :position="isVector(msg) ? 'top' : 'center'" />
            </div>
            <!-- div, а не button: внутри есть свои интерактивные элементы (реакции) —
                 кнопка-в-кнопке невалидна. Жесты (клик/ПКМ/тач) не завязаны на семантику тега. -->
            <div role="button" tabindex="0" @click="onMessageClick(msg)"
                 @keydown.enter="onMessageClick(msg)"
                 @contextmenu="onContextMenu(msg, $event)"
                 @touchstart.passive="onTouchStart(msg, $event)"
                 @touchmove.passive="onTouchMove($event)"
                 @touchend="onTouchEnd" @touchcancel="onTouchEnd"
                 class="max-w-[75%] select-text rounded-2xl px-3 py-1.5 text-left text-sm shadow-sm outline-none transition-shadow hover:shadow"
                 :class="[msg.mine ? 'bg-accent text-white' : 'bg-card text-text',
                          flashMentionId === msg.id ? 'ring-2 ring-accent' : '',
                          // Черновик (ещё не подтверждён сервером) слегка приглушён — тот же
                          // приём, что у Telegram: сообщение уже на месте и его видно, но
                          // видно и то, что оно «в пути». Разница мягкая намеренно: резкая
                          // читалась бы как ошибка отправки.
                          msg.pending ? 'opacity-60' : '',
                          enteringIds.has(msg.id) ? 'gb-msg-in' : '']"
                 :style="swipe.id === msg.id ? `transform: translateX(${swipe.dx}px)` : ''">
              <!-- ФИО автора — у верхнего сообщения пачки (в своих не нужно). -->
              <!-- ⚠️ .stop обязателен: имя лежит ВНУТРИ пузыря, а у пузыря свой @click,
                   открывающий меню сообщения. Без остановки всплытия клик по имени
                   открыл бы и профиль, и меню разом. -->
              <button v-if="!msg.mine && runStarts.has(msg.id) && senderName(msg) && canOpenSenderProfile(msg)"
                      type="button" @click.stop="openSenderProfile(msg)"
                      class="mb-0.5 block text-left text-[11px] font-semibold text-accent underline-offset-2 outline-none hover:underline focus-visible:underline"
                      v-bind="senderDecor(msg)"
                      :title="locale.t('chatThread.openSenderProfile', 'Открыть профиль')">
                {{ senderName(msg) }}
              </button>
              <div v-else-if="!msg.mine && runStarts.has(msg.id) && senderName(msg)"
                   class="mb-0.5 text-[11px] font-semibold text-accent" v-bind="senderDecor(msg)">
                {{ senderName(msg) }}
              </div>
              <div v-if="msg.forwarded_from" class="mb-0.5 text-[11px] italic opacity-80">
                {{ locale.t('chatThread.forwardedFrom', { name: msg.forwarded_from }) }}
              </div>
              <!-- Цитата (как в Telegram): полоса, имя автора, процитированный кусок и
                   значок кавычек справа. Нажатие ведёт к оригиналу и подсвечивает именно
                   этот кусок (см. jumpToQuoted). Кнопка, а не div: по ней ЖМУТ. -->
              <button v-if="msg.reply_to_id && quoteText(msg)" type="button"
                      @click.stop="jumpToQuoted(msg)"
                      class="mb-1 flex w-full items-start gap-1.5 rounded-md border-l-2 py-0.5 pl-2 pr-1 text-left text-xs transition-colors"
                      :class="msg.mine ? 'border-white/60 hover:bg-white/10' : 'border-accent hover:bg-bg2'"
                      :title="locale.t('chatThread.goToQuoted', 'Перейти к исходному сообщению')">
                <span class="min-w-0 flex-1">
                  <span v-if="quoteAuthor(msg.reply_to_id)" class="block font-semibold opacity-90">
                    {{ quoteAuthor(msg.reply_to_id) }}
                  </span>
                  <span class="line-clamp-3 block opacity-80">{{ quoteText(msg) }}</span>
                </span>
                <Quote class="mt-0.5 size-3 shrink-0 opacity-60" />
              </button>
              <span v-if="msg.deleted" class="italic opacity-70">{{ locale.t('chatThread.deleted', 'Сообщение удалено') }}</span>
              <!-- §ролей: игнор — ЛИЧНОЕ, не модерация; сервер текст отдаёт как обычно,
                   прячем и раскрываем на клиенте (клик по плейсхолдеру). -->
              <button v-else-if="isHiddenByIgnore(msg)" type="button" @click.stop="toggleReveal(msg.id)"
                      class="italic opacity-70 underline decoration-dotted">{{ locale.t('chatThread.hiddenByIgnore', 'Скрыто (игнор) — показать') }}</button>
              <!-- GIF (Klipy) — тело сообщения это прямая ссылка на CDN, картинка, а не
                   markdown-текст; ссылку не через renderBody (её незачем делать кликабельной
                   с подтверждением «Переадресация» — это уже картинка, а не переход).
                   Звезда — как в пикере (GifPicker.vue): наведение на УЖЕ ОТПРАВЛЕННУЮ гифку
                   тоже добавляет её в избранное (Discord), дедуп здесь по url — у сообщения
                   нет slug/title Klipy, только сама ссылка (см. stores/gif.js). -->
              <span v-else-if="msg.kind === 'gif'" class="group relative block w-fit">
                <GifImage :src="msg.body" />
                <span role="button" tabindex="0" @click.stop="gif.toggleFavoriteByUrl(msg.body)"
                      :aria-label="gif.isFavoriteUrl(msg.body) ? locale.t('gif.removeFavorite', 'Убрать из избранного') : locale.t('gif.addFavorite', 'В избранное')"
                      class="absolute right-1.5 top-1.5 grid size-6 place-items-center rounded-full bg-black/50
                             opacity-0 transition-opacity group-hover:opacity-100 hover:bg-black/70"
                      :class="{ '!opacity-100': gif.isFavoriteUrl(msg.body) }">
                  <Star class="size-3.5" :class="gif.isFavoriteUrl(msg.body) ? 'fill-yellow-400 text-yellow-400' : 'text-white'" />
                </span>
              </span>
              <!-- Вложение: карточка с именем и размером. Клик — предпросмотр во вкладке
                   (PDF рисует браузер, DOCX распаковываем сами), а не скачивание: чаще
                   всего человеку нужно просто убедиться, что это тот файл. -->
              <span v-else-if="msg.kind === 'file' && msg.attachment" class="block w-fit max-w-full">
                <button type="button" @click="previewAtt = msg.attachment"
                        class="flex items-center gap-2.5 rounded-lg border border-border2 bg-card2 px-3 py-2
                               text-left transition-colors hover:border-accent">
                  <Paperclip class="size-4 shrink-0 text-text3" />
                  <span class="min-w-0">
                    <span class="block truncate text-sm text-text">{{ msg.attachment.name }}</span>
                    <span class="block text-[11px] text-text3">{{ humanSize(msg.attachment.size) }}</span>
                  </span>
                </button>
                <span v-if="msg.body" class="msg-body mt-1 block whitespace-pre-wrap break-words"
                      v-html="renderBody(msg)"></span>
              </span>
              <!-- §D1: Markdown-lite (текст экранирован ДО рендера — см. utils/markdownLite). -->
              <div v-else class="msg-body whitespace-pre-wrap break-words" v-html="renderBody(msg)"
                   @click="onBodyClick" />
              <!-- Перевод ПОД оригиналом, а не вместо него: подмена чужой реплики
                   переводом скрывает то, что человек написал на самом деле, и спорить
                   потом не о чем. Оригинал всегда виден. GIF — не текст, переводить нечего. -->
              <p v-if="!msg.deleted && msg.kind !== 'gif' && tr.shownFor(msg.id)"
                 class="mt-1.5 border-l-2 border-accent/60 pl-2 text-sm text-text2">
                {{ tr.shownFor(msg.id) }}
              </p>
              <button v-if="!msg.deleted && msg.kind !== 'gif' && msg.body && msg.sender_id !== myUserId"
                      type="button" :disabled="tr.busy"
                      class="mt-1 text-tiny text-text3 transition-colors hover:text-accent disabled:opacity-50"
                      @click.stop="tr.toggleMessage(msg.id, msg.body)">
                {{ tr.shownFor(msg.id) ? locale.t('chatThread.hideTranslationAction', 'скрыть перевод') : (tr.busy ? locale.t('chatThread.translating', 'переводим…') : locale.t('chatThread.translateAction', 'перевести')) }}
              </button>
              <!-- Видео из белого списка (YouTube/VK/Rutube, Фаза 1) — карточка ПОД текстом,
                   плеер сразу виден (без лишнего клика «показать видео» — Влад). Шире, чем
                   было (max-w-xs→max-w-sm): раньше карточка была узкой НАМЕРЕННО — свёрнутая
                   плашка «показать видео» не нуждалась в размере плеера; теперь, когда плеер
                   всегда развёрнут, той же ширины не хватает для полной панели управления
                   YouTube/VK/Rutube (там теснится и громкость, и полноэкранный режим). -->
              <div v-for="v in videoEmbeds(msg)" :key="v.sourceUrl" class="mt-1.5" @click.stop>
                <!-- referrerpolicy — ОБЯЗАТЕЛЕН здесь. Сайт целиком отдаёт
                     `Referrer-Policy: no-referrer` (Caddyfile, 152-ФЗ), и БЕЗ этого
                     атрибута запрос к youtube.com/vk.com/rutube.ru уходит вовсе без
                     реферера — плеер YouTube не может проверить контекст встраивания и
                     падает с нечитаемой «Ошибка 153» (эмпирически найдено — Влад,
                     воспроизводится и на сайте, не только в десктопе). Атрибут iframe
                     ПЕРЕБИВАЕТ страничный заголовок только для ЭТОГО запроса — общий
                     no-referrer для остальной страницы не трогаем. "origin" отдаёт
                     ТОЛЬКО домен (без пути) — этого плееру достаточно, полный URL
                     переписки видеохостингу не уходит. -->
                <!-- loading="lazy" — не косметика: КАЖДЫЙ такой фрейм это чужая страница
                     со своим JS на сотни килобайт, и браузер поднимал их все сразу, включая
                     те, что лежат в истории далеко выше экрана. В переписке с несколькими
                     ссылками на разбор это несколько сторонних плееров, соревнующихся за тот
                     же поток, что рисует ленту. С lazy браузер поднимает фрейм, когда тот
                     подъезжает к видимой области; для уже видимого плеера не меняется ничего. -->
                <iframe v-if="videoIframeAllowed"
                        :src="v.embedUrl" class="aspect-video w-full max-w-sm rounded-lg border-0"
                        sandbox="allow-scripts allow-same-origin allow-presentation allow-popups"
                        referrerpolicy="origin"
                        loading="lazy"
                        allowfullscreen />
                <!-- Мобильное приложение: вместо чужого фрейма — карточка, уводящая ролик
                     ЗА пределы приложения (см. videoIframeAllowed выше). Ветка обязательна:
                     без неё видео в приложении исчезло бы без следа, и со стороны это
                     читается как пустое сообщение, то есть как поломка мессенджера. -->
                <button v-else type="button" @click="openExternalVideo(v)"
                        class="flex w-full max-w-sm items-center gap-2.5 rounded-lg border border-border2
                               px-3 py-2 text-left transition-colors hover:border-accent">
                  <span class="grid size-8 shrink-0 place-items-center rounded-full bg-accent/15 text-accent"
                        aria-hidden="true">&#9654;</span>
                  <span class="min-w-0">
                    <span class="block truncate text-sm text-text">{{ locale.t('chatThread.openVideoAction', { host: videoHostLabel(v) }) }}</span>
                    <span class="block truncate text-tiny text-text3">{{ locale.t('chatThread.openVideoHint') }}</span>
                  </span>
                </button>
              </div>
              <!-- Голая ссылка на CDN Klipy в ОБЫЧНОМ тексте (не через пикер) — та же
                   картинка, что у msg.kind==='gif', и та же звезда-избранное по наведению. -->
              <span v-for="g in gifEmbeds(msg)" :key="g.sourceUrl"
                    class="group relative mt-1.5 block w-fit" @click.stop>
                <GifImage :src="g.sourceUrl" />
                <span role="button" tabindex="0" @click.stop="gif.toggleFavoriteByUrl(g.sourceUrl)"
                      :aria-label="gif.isFavoriteUrl(g.sourceUrl) ? locale.t('gif.removeFavorite', 'Убрать из избранного') : locale.t('gif.addFavorite', 'В избранное')"
                      class="absolute right-1.5 top-1.5 grid size-6 place-items-center rounded-full bg-black/50
                             opacity-0 transition-opacity group-hover:opacity-100 hover:bg-black/70"
                      :class="{ '!opacity-100': gif.isFavoriteUrl(g.sourceUrl) }">
                  <Star class="size-3.5" :class="gif.isFavoriteUrl(g.sourceUrl) ? 'fill-yellow-400 text-yellow-400' : 'text-white'" />
                </span>
              </span>
              <span class="ml-2 align-bottom text-[10px]" :class="msg.mine ? 'text-white/70' : 'text-text3'">
                <Pin v-if="msg.pinned" class="mr-0.5 inline size-2.5" />
                <!-- §D11: «изм.» кликабельно — открывает историю версий. -->
                <button v-if="msg.edited_at" type="button" @click.stop="openHistory(msg)"
                        class="underline decoration-dotted hover:opacity-80">{{ locale.t('chatThread.editedShort', 'изм.') }}</button>
                {{ fmtTime(msg.created_at) }}
                <!-- ЛС: галочки отправлено/прочитано (как в Telegram). -->
                <template v-if="msg.mine && kind === 'direct'">
                  <!-- Своё сообщение рисуется сразу по нажатию, до ответа сервера (см.
                       messenger.js::send). Пока подтверждения нет — часики, а не галочка:
                       галочка на неподтверждённом сообщении была бы прямой ложью о том,
                       что оно доставлено. -->
                  <Clock v-if="msg.pending" :title="locale.t('chatThread.sendingTitle', 'Отправляется')" class="ml-0.5 inline size-3 opacity-70" />
                  <CheckCheck v-else-if="isReadByPeer(msg)" :title="locale.t('chatThread.readTitle', 'Прочитано')" class="ml-0.5 inline size-3" />
                  <Check v-else :title="locale.t('chatThread.sentTitle', 'Отправлено')" class="ml-0.5 inline size-3 opacity-70" />
                </template>
                <!-- Группа/канал: у неподтверждённого показывать «кто прочитал» нечего. -->
                <Clock v-else-if="msg.mine && msg.pending" :title="locale.t('chatThread.sendingTitle', 'Отправляется')" class="ml-0.5 inline size-3 opacity-70" />
                <!-- Группа/канал: читателей несколько — попап со списком (см. showReadBy). -->
                <button v-else-if="msg.mine && !msg.pending" type="button" @click.stop="showReadBy(msg)" :title="locale.t('chatThread.whoRead', 'Кто прочитал')"
                        class="ml-0.5 inline-flex align-middle hover:opacity-80"><Eye class="size-2.5" /></button>
              </span>

              <!-- §D3: пилюли реакций — клик по своей снимает её, по чужой добавляет ту же. -->
              <div v-if="msg.reactions?.length" class="mt-1 flex flex-wrap gap-1">
                <button v-for="r in msg.reactions" :key="r.emoji" type="button"
                        @click.stop="onReactionClick(msg, r.emoji)"
                        class="flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-xs transition-colors"
                        :class="r.mine
                          ? (msg.mine ? 'border-white/50 bg-white/20' : 'border-accent bg-accent-glow text-accent')
                          : (msg.mine ? 'border-white/30 hover:bg-white/10' : 'border-border2 hover:bg-bg2')">
                  {{ r.emoji }} {{ r.count }}
                </button>
              </div>

              <!-- Треды: «N ответов» — открывает панель с ответами (reply_to_id), не
                   загромождая основную ленту (docs/MESSENGER-ADDON-PLAN-GPT-SMART.md §3.3). -->
              <button v-if="msg.reply_count" type="button" @click.stop="m.openThread(msg.id)"
                      class="mt-1 flex items-center gap-1 text-xs font-semibold hover:underline"
                      :class="msg.mine ? 'text-white/90' : 'text-accent'">
                <MessageSquare class="size-3" />{{ locale.t('chatThread.repliesCount', { n: msg.reply_count }) }}
              </button>
            </div>
          </div>
        </template>
      </div>

      <!-- Кнопка «@» — НАД стрелкой вниз: перематывает к сообщению, где вас отметили.
           Нужна именно отдельная кнопка: отметка легко теряется в переписке, а «вниз»
           уводит в конец — то есть мимо неё. Видна, пока отметка не прочитана. -->
      <transition name="fade">
        <button v-if="mentionMessageId" type="button" @click="jumpToMention"
                :aria-label="locale.t('chatThread.mentionJumpAria', 'К сообщению, где вас отметили')" :title="locale.t('chatThread.mentionJumpTitle', 'Вас отметили — перейти')"
                class="absolute bottom-16 right-3 grid size-10 place-items-center rounded-full border text-lg font-bold shadow-card"
                :class="mentionLoud
                  ? 'border-red/50 bg-red text-white hover:opacity-90'
                  : 'border-border2 bg-card text-accent hover:bg-bg2'">
          @
        </button>
      </transition>

      <!-- §D5: плавающая кнопка «вниз» с числом непрочитанных — видна, когда прокручено вверх.
           Вне scroller (в relative-обёртке) — не уезжает вместе с содержимым при скролле. -->
      <transition name="fade">
        <button v-if="showScrollBtn" type="button" @click="scrollDown"
                :aria-label="locale.t('chatThread.toLastMessages', 'К последним сообщениям')"
                class="absolute bottom-3 right-3 grid size-10 place-items-center rounded-full border border-border2 bg-card shadow-card hover:bg-bg2">
          <ChevronDown class="size-5 text-text2" />
          <span v-if="activeChat?.unread" class="absolute -top-1.5 -right-1.5 grid h-5 min-w-5 place-items-center rounded-full bg-accent px-1 text-[10px] font-bold text-white">
            {{ activeChat.unread }}
          </span>
        </button>
      </transition>
      </div>

      <!-- §D2: Вектор с репликой вместо холодного 429 — композер блокируется на remaining c. -->
      <!-- Личный ответ Вектора: НЕ сообщение ленты, а карточка над полем ввода. Место
           выбрано не случайно — в ленте её приняли бы за реплику, которую видят все, а
           здесь она читается как ответ лично тебе, рядом с местом, где вопрос задавали.
           ⚠️ Подпись «видно только вам» обязательна: без неё человек не отличит личный
           ответ от публичного и либо промолчит о нужном, либо расскажет лишнее. -->
      <div v-if="vectorReply" class="mx-2.5 mb-1.5 rounded-xl border border-accent/40 bg-accent-glow px-3 py-2">
        <div class="mb-1 flex items-center gap-2">
          <img src="/mascot/vector-avatar.webp" alt="" class="size-5 shrink-0 rounded-full" />
          <span class="min-w-0 flex-1 truncate text-xs font-semibold text-accent">
            {{ locale.t('chatThread.vectorPrivate', 'Вектор · видно только вам') }}
          </span>
          <button type="button" @click="vectorReply = null"
                  :aria-label="locale.t('common.close', 'Закрыть')"
                  class="grid size-5 shrink-0 place-items-center rounded-md text-text3 hover:text-text">
            <X class="size-3.5" />
          </button>
        </div>
        <p class="mb-1 truncate text-[11px] text-text3">{{ vectorReply.question }}</p>
        <p v-if="vectorReply.pending" class="text-sm text-text3">
          {{ locale.t('chatThread.vectorThinking', 'Вектор думает…') }}
        </p>
        <p v-else class="max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-sm text-text">
          {{ vectorReply.text }}
        </p>
      </div>
      <MascotCooldown v-if="mascotCooldown.active" :cooldown="mascotCooldown" />

      <!-- Плашка анти-флуда/мьюта: «не отправляйте так часто» / «вы ограничены модерацией» -->
      <div v-if="notice" class="shrink-0 border-t border-border bg-red/10 px-3 py-2 text-center text-xs font-semibold text-red">
        {{ notice }}
      </div>

      <!-- Композер с превью ответа (или плашка для читателя канала) -->
      <div class="shrink-0 border-t border-border bg-card">
        <template v-if="canPost">
          <div v-if="replyTo" class="flex items-center gap-2 border-b border-border px-3 py-1.5">
            <ReplyIcon class="size-4 shrink-0 text-accent" />
            <div class="min-w-0 flex-1">
              <!-- Ответ на реплику Вектора — это следующий вопрос ему же (сервер разберёт
                   его без префикса «/vector», см. _handle_vector_command). Подписываем
                   явно: иначе непонятно, что цепочка продолжится, и человек снова пишет
                   «/vector». -->
              <div class="text-[11px] font-semibold text-accent">
                {{ replyingToVector ? locale.t('chatThread.askVectorNoPrefix', 'Вопрос Вектору — можно без «/vector»')
                   : (replyQuote ? locale.t('chatThread.quoteLabel', 'Цитата') : locale.t('chatThread.replyLabel', 'Ответ')) }}
              </div>
              <!-- Что именно процитируется — видно ДО отправки. Выделение к этому моменту
                   уже снято, и без показа человек отправлял бы ответ вслепую. -->
              <div class="truncate text-xs text-text3">{{ replyTo.deleted ? locale.t('chatThread.deleted', 'Сообщение удалено') : (replyQuote || replyTo.body) }}</div>
            </div>
            <button type="button" @click="m.clearReply()" :aria-label="locale.t('chatThread.cancelReply', 'Отменить ответ')"
                    class="grid size-6 place-items-center rounded-md text-text3 hover:bg-bg2"><X class="size-4" /></button>
          </div>
          <!-- Автодополнение слэш-команд (как в Telegram) — список + краткое пояснение. -->
          <div v-if="slashCandidates.length" class="border-b border-border p-1.5">
            <button v-for="c in slashCandidates" :key="c.cmd" type="button"
                    @mousedown.prevent="insertSlashCommand(c)"
                    class="flex w-full flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-left"
                    :class="c.ok ? 'hover:bg-bg2' : 'cursor-default opacity-50'">
              <span class="text-sm font-semibold" :class="c.ok ? 'text-accent' : 'text-text3'">{{ c.cmd }}</span>
              <span class="text-xs text-text3">{{ c.ok ? c.hint : c.why }}</span>
            </button>
          </div>
          <!-- §D8: подсказки отметки — участники ЭТОЙ беседы (в ЛС это вы и собеседник). -->
          <div v-if="mentionCandidates.length" class="border-b border-border p-1.5">
            <!-- Подпись «какой это будет пинг»: по одному «!» в наборе понять нельзя,
                 а разница слышимая — громкий звонит человеку и пишет ему в «Систему». -->
            <p class="px-2 pb-1 text-[11px] text-text3">{{ mentionKindHint }}</p>
            <button v-for="p in mentionCandidates" :key="p.user_id" type="button"
                    @mousedown.prevent="insertMention(p)"
                    class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-text hover:bg-bg2">
              <Avatar :src="p.avatar" :name="p.full_name" :role="p.user_role" :color="profilePlate(p.profile_color)" :size="24" />
              <span class="min-w-0 flex-1 truncate">{{ p.full_name }}</span>
              <span class="shrink-0 text-[11px] text-text3">{{ meta(p) }}</span>
            </button>
          </div>
          <!-- Форма добавления личного шаблона (препод/админ). Сам ВЫБОР фразы уехал в
               колесо (QuickReplyWheel), а заводить шаблон там негде: колесо закрывается
               по первому же выбору, а ввод текста — обратное действие. -->
          <form v-if="showTemplateForm && canManageTemplates" class="flex items-center gap-1.5 border-b border-border p-2"
                @submit.prevent="addTemplateFromInput">
            <input v-model="newTemplateText" :placeholder="locale.t('chatThread.customTemplatePlaceholder', 'Свой шаблон (напр. «Работа принята»)…')"
                   class="h-7 min-w-0 flex-1 rounded-md border border-border2 bg-card2 px-2 text-xs text-text outline-none focus:border-accent" />
            <button type="submit" :disabled="!newTemplateText.trim()" :aria-label="locale.t('chatThread.addTemplate', 'Добавить шаблон')"
                    class="grid size-7 shrink-0 place-items-center rounded-md bg-accent text-white disabled:opacity-40"><Plus class="size-3.5" /></button>
            <button type="button" class="grid size-7 shrink-0 place-items-center rounded-md text-text3 hover:bg-bg2"
                    :aria-label="locale.t('common.cancel')" @click="showTemplateForm = false"><X class="size-3.5" /></button>
          </form>
          <!-- Выбранный файл: показываем ДО отправки, с возможностью открыть и передумать. -->
          <div v-if="pendingFile" class="mx-2.5 mb-1 flex items-center gap-2 rounded-lg border border-border2 bg-card2 px-2.5 py-1.5">
            <Paperclip class="size-4 shrink-0 text-text3" />
            <button type="button" class="min-w-0 flex-1 truncate text-left text-xs text-text hover:text-accent"
                    @click="previewFile = pendingFile">
              {{ pendingFile.name }}
              <span class="text-text3">· {{ humanSize(pendingFile.size) }}</span>
            </button>
            <span v-if="uploadPct" class="shrink-0 text-[11px] tabular-nums text-text3">{{ uploadPct }}%</span>
            <button v-else type="button" class="shrink-0 text-text3 hover:text-red"
                    :aria-label="locale.t('common.remove', 'Убрать')" @click="pendingFile = null">✕</button>
          </div>

          <form class="relative flex items-end gap-2 p-2.5" @submit.prevent="pendingFile ? sendPendingFile() : submit()">
            <!-- 🔥 ОДНА КНОПКА «+» ВМЕСТО ЧЕТЫРЁХ (просьба Влада 05.09.2026: «гифки,
                 быстрые ответы, картинки и файлы ужать, сделай на месте скрепки знак „+“,
                 при нажатии вылезут категории»). Постоянный ряд кнопок занимал строку у
                 каждого, кто просто пишет сообщение; здесь их три, и они рядом с полем.
                 Сам файл через наш сервер не проходит: браузер кладёт его прямо в
                 хранилище по подписанной ссылке — см. stores/messenger.js::sendFile. -->
            <input ref="fileInput" type="file" class="hidden" @change="onFileChosen"
                   :accept="(uploadLimits.ext || []).join(',')" />
            <div class="relative shrink-0">
              <button type="button" @click="plusMenu = !plusMenu" :disabled="mascotCooldown.active"
                      :title="locale.t('chatThread.attachMenu', 'Прикрепить')"
                      :aria-label="locale.t('chatThread.attachMenu', 'Прикрепить')"
                      class="grid size-10 place-items-center rounded-lg border border-border2 text-text3
                             transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
                      :class="{ 'border-accent text-accent': plusMenu }">
                <Plus class="size-5" />
              </button>
              <!-- Подложка: клик мимо закрывает. Меню открывается ВВЕРХ — под кнопкой у
                   композера места нет никогда, он и так прижат к низу экрана. -->
              <div v-if="plusMenu" class="fixed inset-0 z-30" @click="plusMenu = false"></div>
              <div v-if="plusMenu"
                   class="absolute bottom-12 left-0 z-40 w-52 overflow-hidden rounded-xl border border-border2 bg-card py-1 shadow-card">
                <button type="button" @click="plusMenu = false; pickFile()"
                        class="flex w-full items-center gap-3 px-3.5 py-2 text-left text-sm text-text hover:bg-bg2">
                  <Paperclip class="size-4 shrink-0 text-text3" />
                  {{ locale.t('files.attach', 'Прикрепить файл') }}
                </button>
                <button type="button" @click="plusMenu = false; showGifPicker = true"
                        class="flex w-full items-center gap-3 px-3.5 py-2 text-left text-sm text-text hover:bg-bg2">
                  <span class="w-4 shrink-0 text-[11px] font-extrabold tracking-tight text-text3">GIF</span>
                  {{ locale.t('chatThread.gifPick', 'GIF') }}
                </button>
                <button type="button" @click="plusMenu = false; showQuickReplies = true"
                        class="flex w-full items-center gap-3 px-3.5 py-2 text-left text-sm text-text hover:bg-bg2">
                  <Zap class="size-4 shrink-0 text-text3" />
                  {{ locale.t('chatThread.quickReplies', 'Быстрые ответы') }}
                </button>
              </div>
            </div>
            <!-- Поле ввода и ЗЕРКАЛО. Текст поля прозрачный (виден только курсор), рисует
                 его зеркало — иначе приглушить символы разметки нечем: покрасить часть
                 содержимого textarea браузер не даёт.
                 🔥 РАМКА И ФОН ЖИВУТ НА ОБЁРТКЕ, А НЕ НА ПОЛЕ (06.09.2026, жалоба Влада
                 «когда набираем текст, его вообще не видно»). Это была настоящая
                 регрессия, доехавшая до боя: у поля стоял непрозрачный `bg-card2`, оно
                 лежало ПОВЕРХ зеркала и закрывало его своим фоном — а свой текст у поля
                 прозрачный. Видно не было НИЧЕГО.
                 ⚠️ Урок общий: делая элемент прозрачным, проверь, что под ним ВИДНО. Фон
                 соседа — такая же преграда, как непрозрачный текст, и ни сборка, ни
                 линтер, ни тесты этого не показывают. Меня это стоило прода.
                 ⚠️ Подсветка фокуса — через `focus-within` на обёртке: фокус получает
                 поле, а рамку теперь рисует не оно. -->
            <div class="gb-composer-wrap relative min-w-0 flex-1 rounded-lg border border-border2 bg-card2">
              <div ref="mirror" aria-hidden="true"
                   class="gb-composer-box gb-composer-mirror pointer-events-none absolute inset-0 overflow-hidden whitespace-pre-wrap break-words">
                <span v-for="(sp, i) in draftSpans" :key="i" :class="sp.dim ? 'gb-md-dim' : 'gb-md-plain'">{{ sp.text }}</span>
              </div>
              <textarea ref="composer" v-model="draft" rows="1" :placeholder="composerHint"
                        @keydown="onComposerKeydown" @input="onComposerInput" @scroll="syncMirrorScroll"
                        @select="updateFmtBubble" @mouseup="updateFmtBubble" @keyup="updateFmtBubble"
                        @blur="onComposerBlur"
                        :disabled="mascotCooldown.active"
                        class="gb-composer-box gb-composer-input relative w-full resize-none border-none bg-transparent outline-none disabled:opacity-60" />
              <!-- Квадратное облачко над выделением (снимок 06 задания). Появляется ТОЛЬКО
                   когда есть что форматировать. mousedown.prevent обязателен: без него
                   нажатие сначала снимает выделение в поле, и оборачивать становится
                   нечего — кнопка выглядела бы сломанной. -->
              <div v-if="fmtBubble.open" ref="fmtEl"
                   class="absolute z-40 -translate-x-1/2 -translate-y-full rounded-lg border border-border2 bg-card p-0.5 shadow-card"
                   :style="{ left: fmtBubble.left + 'px', top: fmtBubble.top + 'px' }">
                <div class="flex items-center gap-0.5">
                  <button v-for="b in FMT_BUTTONS" :key="b.marker" type="button" :title="b.title"
                          @mousedown.prevent="applyFmt(b.marker)"
                          class="grid size-7 place-items-center rounded-md transition-colors hover:bg-bg2"
                          :class="fmtActive(b.marker) ? 'bg-bg2 text-accent' : 'text-text3 hover:text-text'">
                    <component :is="b.icon" class="size-4" />
                  </button>
                </div>
              </div>
            </div>
            <button type="submit" :disabled="(!draft.trim() && !pendingFile) || sending || mascotCooldown.active" :aria-label="locale.t('chatThread.send', 'Отправить')"
                    class="grid size-10 shrink-0 place-items-center rounded-lg bg-accent text-white transition-colors hover:bg-accent2 disabled:opacity-50">
              <Send class="size-5" />
            </button>
          </form>
        </template>
        <!-- Читатель канала: писать нельзя, только подписка -->
        <div v-else class="flex items-center justify-between gap-2 p-3 text-sm text-text3">
          <span>{{ locale.t('chatThread.subscribedToChannel', 'Вы подписаны на канал') }}</span>
          <button type="button" @click="m.leaveActive()"
                  class="rounded-lg border border-border2 px-3 py-1.5 text-text2 hover:bg-bg2">{{ locale.t('conversationInfo.leaveAction', 'Покинуть') }}</button>
        </div>
      </div>
    </template>

    <FilePreview v-if="previewFile || previewAtt" :file="previewFile" :attachment="previewAtt"
                 @close="previewFile = null; previewAtt = null" />

    <!-- Треды: ответы на сообщение (docs/MESSENGER-ADDON-PLAN-GPT-SMART.md §3.3) -->
    <div v-if="activeThread" class="fixed inset-0 z-50 grid place-items-center p-4"
         style="background: var(--gb-overlay)" @click.self="m.closeThread()">
      <div class="flex max-h-[80vh] w-full max-w-sm flex-col rounded-xl border border-border2 bg-card p-4 shadow-card">
        <div class="mb-2 flex items-center gap-2">
          <MessageSquare class="size-4 text-accent" />
          <h3 class="flex-1 font-title text-sm font-bold text-text">{{ locale.t('chatThread.repliesTitle', 'Ответы') }}</h3>
          <button type="button" @click="m.closeThread()" :aria-label="locale.t('common.close')"
                  class="grid size-7 place-items-center rounded-md text-text3 hover:bg-bg2"><X class="size-4" /></button>
        </div>
        <div v-if="threadParent" class="mb-2 rounded-md border-l-2 border-accent bg-bg2 px-3 py-2 text-xs text-text2">
          {{ threadParent.deleted ? locale.t('chatThread.deleted', 'Сообщение удалено') : threadParent.body }}
        </div>
        <div class="min-h-0 flex-1 space-y-2 overflow-y-auto">
          <div v-for="r in activeThread.messages" :key="r.id" class="rounded-md bg-bg2 px-3 py-2 text-sm">
            <div class="mb-0.5 text-[11px] font-semibold text-accent">{{ r.sender_name || (r.mine ? locale.t('chatThread.you', 'Вы') : '') }} · {{ fmtTime(r.created_at) }}</div>
            <div class="text-text">{{ r.deleted ? locale.t('chatThread.deleted', 'Сообщение удалено') : r.body }}</div>
          </div>
          <p v-if="!activeThread.messages.length" class="p-2 text-center text-xs text-text3">{{ locale.t('chatThread.noRepliesYet', 'Пока нет ответов.') }}</p>
        </div>
        <button type="button" @click="replyInThread"
                class="mt-3 w-full rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent2">{{ locale.t('msgAction.reply', 'Ответить') }}</button>
      </div>
    </div>

    <!-- §D18: сводка переписки -->
    <div v-if="summary.open" class="fixed inset-0 z-50 grid place-items-center p-4"
         style="background: var(--gb-overlay)" @click.self="summary.open = false">
      <div class="flex max-h-[80vh] w-full max-w-md flex-col rounded-xl border border-border2 bg-card p-4 shadow-card">
        <div class="mb-2 flex items-center gap-2">
          <ScrollText class="size-4 text-accent" />
          <h3 class="flex-1 font-title text-sm font-bold text-text">{{ locale.t('chatThread.summaryTitle', 'Краткая сводка') }}</h3>
          <button type="button" @click="summary.open = false" :aria-label="locale.t('common.close')"
                  class="grid size-7 place-items-center rounded-md text-text3 hover:bg-bg2"><X class="size-4" /></button>
        </div>
        <p v-if="summary.loading" class="py-6 text-center text-sm text-text3">{{ locale.t('chatThread.summaryLoading', 'Читаем переписку…') }}</p>
        <p v-else-if="summary.note" class="py-4 text-sm text-text3">{{ summary.note }}</p>
        <div v-else class="min-h-0 flex-1 overflow-y-auto whitespace-pre-line text-sm leading-relaxed text-text">
          {{ summary.text }}
        </div>
        <p v-if="summary.text" class="mt-3 border-t border-border pt-2 text-[11px] text-text3">
          {{ locale.t('chatThread.summaryFooter', 'Составлено ИИ по переписке. Имена обезличены перед отправкой в модель.') }}
        </p>
      </div>
    </div>

    <!-- §D19: напоминание о сообщении -->
    <ReminderDialog v-if="remindMsg" :message="remindMsg" @close="remindMsg = null" />

    <!-- Кто прочитал сообщение -->
    <div v-if="readByPopup.open" class="fixed inset-0 z-50 grid place-items-center p-4"
         style="background: var(--gb-overlay)" @click.self="readByPopup.open = false">
      <div class="w-full max-w-xs rounded-xl border border-border2 bg-card p-4 shadow-card">
        <div class="mb-2 flex items-center gap-2">
          <Eye class="size-4 text-accent" />
          <h3 class="font-title text-sm font-bold text-text">{{ locale.t('chatThread.readByTitle', 'Прочитали') }}</h3>
        </div>
        <p v-if="!readByPopup.names.length" class="text-sm text-text3">{{ locale.t('chatThread.noOneReadYet', 'Пока никто не прочитал.') }}</p>
        <ul v-else class="space-y-1 text-sm text-text">
          <li v-for="(n, i) in readByPopup.names" :key="i">{{ n }}</li>
        </ul>
        <button type="button" @click="readByPopup.open = false"
                class="mt-3 w-full rounded-lg border border-border2 px-4 py-2 text-sm text-text2 hover:bg-bg2">{{ locale.t('common.close') }}</button>
      </div>
    </div>

    <!-- «Реакции» (Message Info): кто поставил реакцию + кто просмотрел, с временем. -->
    <div v-if="msgInfoPopup.open" class="fixed inset-0 z-50 grid place-items-center p-4"
         style="background: var(--gb-overlay)" @click.self="msgInfoPopup.open = false">
      <div class="w-full max-w-xs rounded-xl border border-border2 bg-card p-4 shadow-card">
        <div class="mb-2 flex items-center gap-2">
          <SmilePlus class="size-4 text-accent" />
          <h3 class="font-title text-sm font-bold text-text">{{ locale.t('msgAction.reactionsInfo', 'Реакции') }}</h3>
        </div>
        <div v-if="msgInfoPopup.reactions.length" class="mb-3 space-y-1.5">
          <div v-for="r in msgInfoPopup.reactions" :key="r.emoji" class="flex items-start gap-2 text-sm">
            <span class="text-base leading-5">{{ r.emoji }}</span>
            <span class="text-text2">{{ r.users.join(', ') }}</span>
          </div>
        </div>
        <p v-else class="mb-3 text-sm text-text3">{{ locale.t('chatThread.noReactionsYet', 'Пока никто не отреагировал.') }}</p>
        <div class="mb-1.5 flex items-center gap-2 border-t border-border pt-2.5">
          <Eye class="size-3.5 text-text3" />
          <span class="text-tiny font-semibold uppercase tracking-wide text-text3">{{ locale.t('chatThread.readByTitle', 'Прочитали') }}</span>
        </div>
        <p v-if="!msgInfoPopup.viewed.length" class="text-sm text-text3">{{ locale.t('chatThread.noOneReadYet', 'Пока никто не прочитал.') }}</p>
        <ul v-else class="space-y-1 text-sm text-text">
          <li v-for="u in msgInfoPopup.viewed" :key="u.id" class="flex items-center justify-between gap-2">
            <span class="min-w-0 truncate">{{ u.full_name }}</span>
            <span class="shrink-0 text-tiny text-text3">{{ fmtTime(u.last_read_at) }}</span>
          </li>
        </ul>
        <button type="button" @click="msgInfoPopup.open = false"
                class="mt-3 w-full rounded-lg border border-border2 px-4 py-2 text-sm text-text2 hover:bg-bg2">{{ locale.t('common.close') }}</button>
      </div>
    </div>

    <!-- Оверлей действий -->
    <MessageActionsOverlay v-if="overlay.open" :message="overlay.message" :x="overlay.x" :y="overlay.y"
                           :translated="!!tr.shownFor(overlay.message?.id)"
                           :selection="overlay.selection" :kind="kind" :can-pin="canPin" :can-link="canLink"
                           @pick="onPick" @react="onReact" @close="overlay.open = false" />

    <!-- §D11: история редактирования сообщения -->
    <div v-if="historyPopup.open" class="fixed inset-0 z-50 grid place-items-center p-4"
         style="background: var(--gb-overlay)" @click.self="historyPopup.open = false">
      <div class="w-full max-w-sm rounded-xl border border-border2 bg-card p-4 shadow-card">
        <div class="mb-2 flex items-center gap-2">
          <History class="size-4 text-accent" />
          <h3 class="font-title text-sm font-bold text-text">{{ locale.t('chatThread.editHistoryTitle', 'История изменений') }}</h3>
        </div>
        <div class="max-h-72 space-y-2 overflow-y-auto">
          <div v-for="(v, i) in historyPopup.versions" :key="i"
               class="rounded-md border border-border bg-bg2 px-3 py-2 text-sm">
            <div class="mb-0.5 text-[11px] text-text3">
              {{ fmtFull(v.at) }}{{ v.current ? ' · ' + locale.t('chatThread.currentVersion', 'текущая версия') : '' }}
            </div>
            <div class="text-text">{{ v.body }}</div>
          </div>
        </div>
        <button type="button" @click="historyPopup.open = false"
                class="mt-3 w-full rounded-lg border border-border2 px-4 py-2 text-sm text-text2 hover:bg-bg2">{{ locale.t('common.close') }}</button>
      </div>
    </div>

    <!-- Выбор режима удаления своего сообщения(-й) -->
    <div v-if="deleteTargets" class="fixed inset-0 z-50 grid place-items-center p-4"
         style="background: var(--gb-overlay)" @click.self="deleteTargets = null">
      <div class="w-full max-w-xs rounded-xl border border-border2 bg-card p-5 shadow-card">
        <h3 class="font-title text-base font-bold text-text">
          {{ deleteTargets.length > 1 ? locale.t('chatThread.deleteManyQuestion', { n: deleteTargets.length }) : locale.t('chatThread.deleteOneQuestion', 'Удалить сообщение?') }}
        </h3>
        <p class="mt-1 text-xs text-text3">{{ locale.t('chatThread.deleteScopeHint', '«У всех» сотрёт текст у собеседника, «у себя» — скроет только у вас.') }}</p>
        <div class="mt-4 space-y-2">
          <button type="button" @click="applyDelete('all')"
                  class="w-full rounded-lg bg-red px-4 py-2 text-sm font-semibold text-white hover:opacity-90">{{ locale.t('chatThread.deleteForAll', 'Удалить у всех') }}</button>
          <button type="button" @click="applyDelete('self')"
                  class="w-full rounded-lg border border-border2 px-4 py-2 text-sm text-text hover:bg-bg2">{{ locale.t('chatThread.deleteForSelf', 'Удалить у себя') }}</button>
          <button type="button" @click="deleteTargets = null"
                  class="w-full rounded-lg px-4 py-2 text-sm text-text3 hover:bg-bg2">{{ locale.t('common.cancel') }}</button>
        </div>
      </div>
    </div>

    <!-- «О беседе»: профиль собеседника / участники группы с владельцем -->
    <!-- Панель беседы шлёт события, а открывает по-прежнему лента: поиск и сводка
         живут здесь со своим состоянием, и вторая их копия в панели разошлась бы с
         первой молча. -->
    <ConversationInfo v-if="showInfo" @close="showInfo = false"
                      @search="showInfo = false; showSearch = true"
                      @summary="showInfo = false; openSummary()"
                      @open-file="showInfo = false; previewAtt = $event" />
    <!-- Клик по отметке "@Фамилия"/"/@Фамилия"/"/@!Фамилия" в теле сообщения (см. onBodyClick) -->
    <PeerProfileModal v-if="peerProfileId" :user-id="peerProfileId" @close="peerProfileId = ''" />

    <ReportDialog v-if="reportMsg" :message="reportMsg" @submit="onReportSubmit" @close="reportMsg = null" />
    <!-- §12: оверлей отчёта куратора (круговая + плоские по предметам + дрилл-даун). -->
    <TranslateDialog v-if="showTranslate" @close="showTranslate = false" />
    <GifPicker v-if="showGifPicker" @pick="m.sendGif($event)" @close="showGifPicker = false" />

    <!-- Колесо быстрых ответов (просьба Влада 05.09.2026). Геометрия — общая с колесом
         активностей (utils/wheelGeometry.js), крестик в центре закрывает. -->
    <QuickReplyWheel v-if="showQuickReplies" :items="quickReplyItems" :can-manage="canManageTemplates"
                     @pick="onQuickReplyPick" @remove="onQuickReplyRemove"
                     @add="showQuickReplies = false; showTemplateForm = true"
                     @close="showQuickReplies = false" />
    <CuratorReportOverlay v-if="openReportOverlay" :report-id="openReportOverlay" @close="openReportOverlay = null" />
    <ForwardPicker v-if="forwardState.open" :count="forwardState.ids.length"
                   @submit="onForwardSubmit" @close="forwardState = { open: false, ids: [] }" />

    <!-- Тост «скопировано» -->
    <transition name="fade">
      <div v-if="copied" class="pointer-events-none fixed bottom-24 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-text px-3 py-1.5 text-xs text-card shadow-card">
        {{ locale.t('chatThread.copied', 'Скопировано') }}
      </div>
    </transition>
  </section>
</template>

<style scoped>
/* Появление нового сообщения: короткое и мелкое. Длинная или размашистая анимация в
   ленте, куда сообщения приходят пачками, превращается в шум — Discord и Telegram
   держатся тех же ~250 мс и нескольких пикселей смещения. */
@keyframes gb-msg-in {
  from { opacity: 0; transform: translateY(6px) scale(0.985); }
  to   { opacity: 1; transform: none; }
}
.gb-msg-in { animation: gb-msg-in 0.26s cubic-bezier(0.2, 0.7, 0.2, 1); }

/* Скелет — «дышит», а не бежит: бегущий блик по всей ленте притягивает взгляд к
   заглушке вместо содержимого, ради которого человек и открыл беседу. */
@keyframes gb-skeleton-pulse {
  0%, 100% { opacity: 0.45; }
  50%      { opacity: 0.75; }
}
.gb-skeleton {
  background: var(--gb-card2, rgba(127, 127, 127, 0.18));
  animation: gb-skeleton-pulse 1.4s ease-in-out infinite;
}

/* Уважение к системной настройке: человеку, который выключил анимации, они выключены
   и здесь. Содержимое при этом остаётся на месте — гасим движение, а не показ. */
@media (prefers-reduced-motion: reduce) {
  .gb-msg-in { animation: none; }
  .gb-skeleton { animation: none; }
}
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* §D1: оформление Markdown-lite внутри пузыря сообщения (см. utils/markdownLite.js). */
.msg-body :deep(strong) { font-weight: 700; }
.msg-body :deep(em) { font-style: italic; }
.msg-body :deep(u) { text-decoration: underline; }
.msg-body :deep(s) { text-decoration: line-through; opacity: 0.75; }
.msg-body :deep(code) {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  background: rgba(0, 0, 0, 0.12);
  padding: 1px 5px; border-radius: 4px; font-size: 0.88em;
}
.msg-body :deep(pre) {
  background: rgba(0, 0, 0, 0.12);
  padding: 8px 12px; border-radius: 8px; overflow-x: auto;
  margin: 4px 0; font-size: 0.85em;
}
.msg-body :deep(pre code) { background: none; padding: 0; }
/* ── Зеркало композера ────────────────────────────────────────────────────────────────
   Поле ввода и зеркало обязаны совпадать ПО ВСЕМ метрикам: шрифт, размер, межстрочный
   интервал, отступы, перенос слов. Разойдётся хоть одна — приглушённые символы съедут с
   настоящих, и это будет выглядеть как рябь в поле ввода. Поэтому раскладка задана ОДНИМ
   классом на оба элемента, а не двумя похожими наборами утилит. */
.gb-composer-wrap { transition: border-color .15s, background-color .15s; }
.gb-composer-wrap:focus-within {
  border-color: var(--gb-accent);
  background: var(--gb-surface);
}
.gb-composer-box {
  box-sizing: border-box;
  min-height: 40px;
  max-height: 8rem;
  padding: 0.5rem 0.75rem;
  font-size: 1rem;
  line-height: 1.5;
  font-family: inherit;
  border-radius: 0.5rem;
  /* ⚠️ РАМКИ здесь нет вовсе: её рисует ОБЁРТКА (`gb-composer-wrap`), а поле и зеркало
     лежат внутри неё одинаковыми прямоугольниками. Раньше рамка была на поле, и зеркалу
     приходилось подставлять прозрачную такой же ширины, чтобы текст не съезжал на
     пиксель, — лишняя связь, которую легко разорвать правкой одного из двух. */
  border: 0;
}
@media (min-width: 640px) {
  .gb-composer-box { font-size: 0.875rem; }
}
/* Текст поля прозрачный — его рисует зеркало. Курсор и выделение остаются видимыми:
   `caret-color` и `::selection` от `color` не зависят. */
.gb-composer-input {
  color: transparent;
  caret-color: var(--gb-text);
  overflow-y: auto;
}
.gb-composer-input::selection { background: color-mix(in srgb, var(--gb-accent) 35%, transparent); }
/* ⚠️ Плейсхолдер задаём ЯВНО: он наследует `color`, то есть вместе с текстом стал бы
   прозрачным — подсказка в пустом поле исчезла бы совсем. */
.gb-composer-input::placeholder { color: var(--gb-text-dim); opacity: 1; }
.gb-md-plain { color: var(--gb-text); }
/* Символы закрытой пары — приглушены, как в Discord (снимок 07 задания). */
.gb-md-dim { color: var(--gb-text-dim); opacity: 0.55; }

/* Подсветка процитированного куска после перехода по цитате. Временная: постоянная
   осталась бы висеть после прочтения, и следующий переход дал бы два «важных» места. */
.msg-body :deep(mark.gb-quote-hit),
mark.gb-quote-hit {
  background: color-mix(in srgb, var(--gb-accent) 35%, transparent);
  color: inherit;
  border-radius: 3px;
  padding: 0 1px;
}

.msg-body :deep(blockquote) {
  border-left: 3px solid currentColor;
  margin: 4px 0; padding: 2px 10px; opacity: 0.85;
}
.msg-body :deep(h1) { font-size: 1.3em; font-weight: 800; margin: 4px 0 2px; }
.msg-body :deep(h2) { font-size: 1.15em; font-weight: 700; margin: 4px 0 2px; }
.msg-body :deep(ul) { margin: 2px 0 2px 1.1em; list-style: disc; }
/* §D8: подсветка упоминаний — заметно, но не спорит с цветом текста своих/чужих пузырей. */
.msg-body :deep(.mention) { font-weight: 700; text-decoration: underline; text-underline-offset: 2px; }
.msg-body :deep(.mention[data-mention-uid]) { cursor: pointer; }
</style>
