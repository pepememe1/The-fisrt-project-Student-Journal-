<script setup>
// PeerProfileCard — Discord-style карточка профиля (§5.4). ЕДИНАЯ реализация для ДВУХ
// контекстов, чтобы не разъезжались («о себе» и заметка должны выглядеть и вести себя
// одинаково, кто бы ни смотрел):
//   • editable=true  — СВОЙ профиль (Profile.vue): аватарка/«О себе» редактируются прямо
//     здесь, карточка сама читает useProfileStore — родителю передавать draft-значения не
//     нужно, живой предпросмотр получается сам собой (карточка И ЕСТЬ то, что видят другие).
//   • editable=false — ЧУЖОЙ профиль (PeerProfileModal, из мессенджера): данные приходят
//     готовым объектом (`peerData`, если он уже есть у вызывающего, например activePeer) или
//     подгружаются по `userId` через уже существующий, но раньше нигде не используемый
//     GET /web/messenger/users/{id}/profile.
// Заметка (§5.4 «заметка… видна только нам», Discord «Notes») — ОДИНАКОВО работает в ОБОИХ
// режимах: это всегда «моя личная запись про этого человека», и про самого себя тоже
// (памятка себе на своей же карточке — так в ТЗ, отдельно не выпиливаем).
// ⚠️ (живой отзыв Влада) «О себе» и заметка раньше сохранялись КАЖДАЯ ПО-СВОЕМУ — «о
// себе» отдельной кнопкой прямо тут, заметка ПО BLUR без единой кнопки вовсе (и терялась
// при уходе со вкладки, если blur не успевал). Обе САМИ по себе больше НЕ сохраняют —
// черновик копится здесь, а сохраняет ОБЩАЯ кнопка в Profile.vue через `commit()`
// (defineExpose ниже), одним действием со сменой цвета/шрифта. Компонент editable=false
// (чужой профиль) этого не касается — там ни «о себе», ни заметка не редактируются.
import { ref, computed, onMounted, watch } from 'vue'
import { resetDraft, syncDraft } from '@/utils/draftSync'
import { Camera, Send, Pencil, ImageIcon, Film, Trash2 } from '@lucide/vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useProfileStore, BIO_LIMIT } from '@/stores/profile'
import { useMessengerStore } from '@/stores/messenger'
import { useLocaleStore } from '@/stores/locale'
import { profilePlate } from '@/theme/palette'
import { nameDecor } from '@/config/nameEffects'
import { roleLabel } from '@/config/roles'
import { messengerApi } from '@/api/endpoints'
import Avatar from '@/components/ui/Avatar.vue'
import AvatarCropper from '@/components/AvatarCropper.vue'
import GifPicker from '@/components/messenger/GifPicker.vue'
import StampEgg from '@/components/easter/StampEgg.vue'
import { useToast } from '@/composables/useToast'

const props = defineProps({
  editable: { type: Boolean, default: false },
  userId: { type: String, default: '' },
  peerData: { type: Object, default: null },
  // Черновики цвета/шрифта живут В Profile.vue (там же, где сами пикеры) — картинка
  // предпросмотра обязана их видеть ДО сохранения, иначе смена цвета «не работала бы
  // на глаз», пока не нажата общая кнопка. null — использовать сохранённое значение стора.
  colorOverride: { type: String, default: null },
  fontOverride: { type: String, default: null },
  effectOverride: { type: String, default: null },
  nameColorOverride: { type: String, default: null },
})
const emit = defineEmits(['messaged'])

const auth = useAuthStore()
const profile = useProfileStore()
const messenger = useMessengerStore()
const locale = useLocaleStore()
const router = useRouter()
const toast = useToast()

// ── Данные карточки ──────────────────────────────────────────────────────────────────
const fetched = ref(null)
async function loadPeer() {
  if (props.editable || props.peerData || !props.userId) return
  try {
    const { data } = await messengerApi.profile(props.userId)
    fetched.value = data.profile
  } catch { fetched.value = null }
}
onMounted(loadPeer)
watch(() => props.userId, loadPeer)

// ⚠️ Свой id берём из стора профиля (он приходит в /me/prefs), а НЕ из auth.user —
// «визитка» после входа состоит из логина, роли и ФИО, id в ней нет и не было. Пока
// карточка читала `auth.user?.id`, свой id был пустой строкой ВСЕГДА, и всё, что
// адресует человека путём, тихо выключалось: заметка про себя не загружалась и не
// сохранялась (запрос не уходил вовсе — см. ранний выход по `!id` ниже), а кнопка
// «Написать» показывалась на собственном профиле, потому что isSelf не мог стать true.
const myUserId = computed(() => profile.userId)
// Стор грузится один раз за вкладку и обычно уже наполнен нижней панелью сайдбара; зовём
// на всякий случай и здесь — вызов идемпотентный (второго запроса не будет), а во
// встроенном режиме окна программы сайдбара нет вовсе и наполнить его больше некому.
// Не только для editable: без своего id и на ЧУЖОЙ карточке нельзя понять, что открыт
// собственный профиль, — тогда там появляется кнопка «Написать» самому себе.
onMounted(() => profile.load())

// «Кого показываем» — три источника по приоритету: свой профиль (реактивно к сторам,
// живой предпросмотр без сохранения) → готовые данные от вызывающего → подгруженные сами.
const shown = computed(() => {
  if (props.editable) {
    return {
      id: myUserId.value, full_name: auth.user?.name || '', role: auth.role,
      group_name: auth.user?.group_name || '', birthday: profile.birthday || '',
      avatar: profile.avatar, bio: profile.bio,
      profile_banner: profile.banner,
      profile_color: props.colorOverride ?? profile.color,
      name_font: props.fontOverride ?? profile.font,
      name_effect: props.effectOverride ?? profile.effect,
      name_color: props.nameColorOverride ?? profile.nameColor,
      login: auth.user?.login || '',
      subjects: auth.user?.subjects || [],
    }
  }
  return props.peerData || fetched.value || {}
})
const isSelf = computed(() => !!shown.value.id && shown.value.id === myUserId.value)

const plate = computed(() => profilePlate(shown.value.profile_color))
// Имя рисуем ЕДИНОЙ nameDecor (шрифт + эффект + цвет), а не одним лишь семейством
// шрифта: иначе выбранный эффект был бы виден в диалоге выбора и нигде больше.
const nameDecoration = computed(() => nameDecor(shown.value))
const metaLine = computed(() => {
  const u = shown.value
  const parts = [roleLabel(u.role)]
  if (u.role === 'teacher' && (u.subjects || []).length) parts.push(u.subjects.join(', '))
  else if (u.group_name) parts.push(u.group_name)
  //⚠️ Дня рождения здесь БОЛЬШЕ НЕТ: он вынесен отдельной строкой ниже (правка Влада
  //23.08.2026). В перечне «роль · группа · дата» его искали глазами и не находили.
  return parts.filter(Boolean).join(' · ')
})

// ── Баннер карточки ──────────────────────────────────────────────────────────────────
// Гифка с Klipy вместо однотонной плашки. Цвет профиля она НЕ отменяет: он по-прежнему
// красит подложку значка роли и участвует в оформлении имени — гифка ложится только на
// верхнюю полосу. Поэтому «убрать баннер» возвращает цвет, а не оставляет пустоту.
const bannerUrl = computed(() => shown.value.profile_banner || '')

// Фуллскрин-просмотр аватарки/баннера ЧУЖОГО профиля по клику (Влад): полноценный файл
// без круглой/квадратной обрезки. '' — закрыт. В своём (editable) профиле клик по картинке
// уже занят редактированием, поэтому лупа только на чужой карточке.
const lightbox = ref('')

// ── Аватарка и баннер: выбор источника (только editable) ─────────────────────────────
const editingAvatar = ref(false)
const avatarMenuOpen = ref(false)
// '' | 'avatar' | 'banner' — один и тот же пикер Klipy на два места назначения.
const gifPickerFor = ref('')

// Сохраняем СРАЗУ, а не черновиком под общую кнопку внизу страницы (как цвет и шрифт).
// Причина: и картинку, и гифку выбирают в отдельном окне, которое закрывается по выбору,
// — «выбрал и закрылось» человек читает как «применено». Копить это в черновике значит
// однажды потерять выбор, уйдя со страницы мимо кнопки.
async function applyAvatar(src) {
  const r = await profile.save(src)
  if (!r.ok) reportSaveFailure(r)
}
async function applyBanner(url) {
  const before = profile.banner
  const r = await profile.saveProfile({ banner: url })
  if (!r.ok) {
    profile.banner = before      // на экране не должно висеть то, чего нет на сервере
    reportSaveFailure(r)
  }
}
function reportSaveFailure(r) {
  toast.error(r.offline
    ? locale.t('profile.mediaOffline', 'Нет связи с сервером — изменение не сохранено.')
    : (r.detail || locale.t('profile.mediaFailed', 'Не удалось сохранить изображение.')))
}

async function onSaveAvatar(dataUrl) {
  editingAvatar.value = false
  await applyAvatar(dataUrl)
}
function onGifPicked(item) {
  const target = gifPickerFor.value
  gifPickerFor.value = ''
  if (!item) return
  // ⚠️ Берём `url` (это gif, см. gif_service._simplify), а НЕ лёгкое превью `thumb_url`,
  // хотя аватарка рисуется размером 32–80 px и превью хватило бы по пикселям. Причина:
  // превью — webp, и АНИМИРОВАН ли он, проверить не удалось (ключ Klipy живёт только на
  // сервере, а доступа к нему в момент правки не было). Неподвижная «гифка-аватарка» —
  // это молчаливый отказ фичи, а лишние килобайты — всего лишь лишние килобайты.
  // Подтвердится, что превью анимировано, — здесь меняется одно слово.
  const src = item.url || item.thumb_url || ''
  if (target === 'avatar') applyAvatar(src)
  else if (target === 'banner') applyBanner(src)
}

// ── «О себе» (только editable — у чужого профиля это чистый текст) ──────────────────
// 🔥 ЧЕРНОВИК ДОГОНЯЕТ СОХРАНЁННОЕ ЧЕРЕЗ `lastSyncedBio`, А НЕ ЧЕРЕЗ `bioDirty`
// (06.09.2026, жалоба Влада: «иногда при выходе вылазит окошко „точно хотите выйти не
// подтвердив действия", кнопки ни на что не влияют»).
//
// Здесь стояло `watch(() => profile.bio, (v) => { if (!bioDirty.value) draftBio.value = v })`.
// Сторож читал `bioDirty`, а тот считается по УЖЕ НОВОМУ `profile.bio` — значит в момент
// прихода настроек с сервера (а они приходят ПОСЛЕ монтирования карточки) условие всегда
// ложно: черновик пуст, сохранённое непусто. Черновик так и оставался пустым, и
// `isDirty` горел ВЕЧНО без единого нажатия. Отсюда и «иногда»: у кого «о себе» пустое,
// тот ничего не замечал.
//
// ⚠️ И вторая половина ХУЖЕ первой: по кнопке «Сохранить» уезжал ПУСТОЙ bio — то есть
// диалог, предлагавший сохранить несуществующие правки, СТИРАЛ настоящий текст «о себе».
// Со стороны это и выглядит как «кнопка ничего не делает»: поле и так показывало пустоту.
//
// Правило вынесено в `utils/draftSync.js` и проверяется числами: тот же приём с «последним
// синхронизированным значением» уже стоял в `Profile.vue` для цвета и шрифта и был там
// написан ВЕРНО — то есть правило одно, а реализаций было две, и разошлись они молча.
const draftBio = ref(profile.bio)
let lastSyncedBio = profile.bio
const bioDirty = computed(() => props.editable && draftBio.value !== profile.bio)
onMounted(() => { const r = resetDraft(profile.bio); draftBio.value = r.draft; lastSyncedBio = r.lastSynced })
watch(() => profile.bio, (v) => {
  const next = syncDraft({ draft: draftBio.value, lastSynced: lastSyncedBio }, v)
  draftBio.value = next.draft
  lastSyncedBio = next.lastSynced
})

// ── Заметка (в ОБОИХ режимах — всегда про shown.value.id) ───────────────────────────
const note = ref('')
const noteSaved = ref('')
const noteDirty = computed(() => note.value !== noteSaved.value)
async function loadNote() {
  if (!shown.value.id) return
  // ⚠️ Свой id теперь приезжает вместе с prefs, то есть ПОСЛЕ монтирования карточки —
  // значит загрузка заметки может застать человека уже печатающим. Набранный текст в
  // этом случае не трогаем: перезаписать его серверным ответом означало бы стереть
  // правку у того, кто просто начал печатать быстрее, чем ответила сеть.
  const hadDraft = noteDirty.value
  try {
    const { data } = await messengerApi.note(shown.value.id)
    noteSaved.value = data.text || ''
    if (!hadDraft) note.value = noteSaved.value
  } catch { if (!hadDraft) { note.value = ''; noteSaved.value = '' } }
}
onMounted(loadNote)
watch(() => shown.value.id, loadNote)
async function saveNote() {
  const id = shown.value.id
  if (!id) return
  try {
    const { data } = await messengerApi.saveNote(id, note.value)
    noteSaved.value = data.text || ''
  } catch { /* офлайн-мессенджер не бывает, но не роняем интерфейс */ }
}
// ⚠️ В editable-режиме заметка НЕ сохраняется сама по blur — это раньше и было
// причиной «написал, при перезаходе пропало» (уход со страницы кликом по сайдбару не
// гарантированно успевает довести blur→запрос до конца, а второй клик — уже не на этом
// компоненте). Теперь она копится черновиком и уходит ТОЛЬКО через commit() ниже, одним
// действием с «о себе»/цветом/шрифтом. В НЕ-editable режиме (чужой профиль в модалке —
// там нет общей кнопки сохранения) поведение прежнее: реальный blur сохраняет сразу.
function onNoteBlur() { if (!props.editable && noteDirty.value) saveNote() }

// ── Общее сохранение (вызывается ИЗВНЕ, из Profile.vue) ─────────────────────────────
const isDirty = computed(() => bioDirty.value || (props.editable && noteDirty.value))
async function commit() {
  const tasks = []
  if (bioDirty.value) tasks.push(profile.saveProfile({ bio: draftBio.value }))
  if (props.editable && noteDirty.value) tasks.push(saveNote())
  await Promise.all(tasks)
}
function discard() {
  //Метку двигаем вместе с черновиком — иначе после «Отменить» первое же обновление с
  //сервера снова разошлось бы с черновиком и зажгло «есть несохранённые изменения».
  const r = resetDraft(profile.bio)
  draftBio.value = r.draft
  lastSyncedBio = r.lastSynced
  note.value = noteSaved.value
}
// Левая колонка Profile.vue ведёт в те же самые действия — теперь их два вида (картинка
// и гифка), поэтому наружу отдаём открытие МЕНЮ, а не сразу обрезалки: иначе кнопка
// «Изменить аватарку» молча означала бы «только картинку», и гифку нашли бы лишь те, кто
// догадался нажать на саму аватарку.
defineExpose({
  openAvatarEditor: () => { avatarMenuOpen.value = true },
  openBannerPicker: () => { gifPickerFor.value = 'banner' },
  removeBanner: () => applyBanner(''),
  isDirty, commit, discard,
})

// ── Кнопка «Сообщение» ────────────────────────────────────────────────────────────────
async function sendMessage() {
  if (isSelf.value) return
  await messenger.openWith({ id: shown.value.id, full_name: shown.value.full_name })
  router.push(`/${auth.role}/messages`)
  emit('messaged')
}

// Левая колонка Profile.vue дублирует вход в тот же редактор аватарки (как в Discord —
// превью-аватар и «Изменить аватарку» слева ведут в ОДИН диалог), поэтому открывать его
// нужно и СНАРУЖИ, не только кликом по самой карточке. Экспортирован ВЫШЕ, вместе с
// isDirty/commit/discard — второй defineExpose Vue тихо проигнорировал бы.
</script>

<template>
  <!-- relative — якорь для штампа Papers Please: он ложится в случайное место
       ИМЕННО этой карточки, а не всей страницы, поэтому виден в любом окне,
       где карточку показывают (свой профиль, чужой из группы, чужой из ЛС). -->
  <div class="relative overflow-hidden rounded-xl border border-border2 bg-card">
    <StampEgg />
    <!-- Баннер: гифка, если выбрана, иначе однотонная плашка цвета профиля. -->
    <!-- ⚠️ Баннеру отдана заметная высота (было 80 px). Смысл баннера в том, чтобы
         его было видно; в узкой полосе гифка превращалась в мазок, то есть
         возможность его поставить существовала, а возможности разглядеть — нет. -->
    <!-- 🔥 У СЕБЯ БАННЕР КЛИКАБЕЛЕН ЦЕЛИКОМ (просьба Влада, 31.08.2026: «чтобы при
         нажатии на текущую аватарку или баннер можно было менять»). Раньше менять его
         давал только карандаш 32×32 в углу — на телефоне это цель меньше подушечки
         пальца, а сам баннер, занимающий полэкрана, на нажатие не отвечал никак. Теперь
         нажимается вся полоса; карандаш остаётся ПОДСКАЗКОЙ, что тут вообще что-то
         меняется (без него у пустой цветной плашки нет ни одного признака кнопки).
         ⚠️ На ЧУЖОМ профиле поведение прежнее — лупа во весь экран: там менять нечего. -->
    <div class="relative h-40 shrink-0 overflow-hidden sm:h-52"
         :class="editable ? 'cursor-pointer' : ''"
         :style="bannerUrl ? undefined : { background: plate }"
         @click="editable && (gifPickerFor = 'banner')">
      <img v-if="bannerUrl" :src="bannerUrl" alt="" class="size-full object-cover"
           :class="{ 'cursor-zoom-in': !editable }"
           @click="!editable && (lightbox = bannerUrl)" />
      <!-- Карандаш виден ВСЕГДА, а не только при наведении: на телефоне наведения не
           существует вовсе, и подсказка «здесь можно поменять» иначе не появилась бы
           никогда. При наведении просто становится заметнее. -->
      <button v-if="editable" type="button" @click.stop="gifPickerFor = 'banner'"
              class="absolute right-2 top-2 grid size-8 place-items-center rounded-full bg-black/45
                     text-white opacity-80 transition hover:bg-black/70 hover:opacity-100"
              :title="locale.t('profile.editBanner', 'Сменить баннер')"
              :aria-label="locale.t('profile.editBanner', 'Сменить баннер')">
        <Pencil class="size-4" />
      </button>
      <button v-if="editable && bannerUrl" type="button" @click.stop="applyBanner('')"
              class="absolute right-11 top-2 grid size-8 place-items-center rounded-full bg-black/45
                     text-white opacity-80 transition hover:bg-black/70 hover:opacity-100"
              :title="locale.t('profile.removeBanner', 'Убрать баннер')"
              :aria-label="locale.t('profile.removeBanner', 'Убрать баннер')">
        <Trash2 class="size-4" />
      </button>
    </div>
    <!-- ⚠️ Ширина текста ограничена. Окно теперь почти во весь экран, и без предела
         «о себе» и заметка растягивались на всю ширину монитора — строку в 1800 px
         невозможно читать, глаз теряет начало следующей. -->
    <div class="mx-auto w-full max-w-3xl -mt-6 px-5 pb-6 sm:-mt-7">
      <div class="group relative inline-block">
        <button v-if="editable" type="button" @click="avatarMenuOpen = !avatarMenuOpen"
                class="relative block size-24 overflow-hidden rounded-full ring-[5px] ring-card"
                :title="locale.t('profile.editAvatar', 'Изменить аватарку')">
          <Avatar :src="shown.avatar" :name="shown.full_name" :role="shown.role" :color="plate" :size="96" />
          <span class="absolute inset-0 grid place-items-center bg-black/40 opacity-0 transition-opacity group-hover:opacity-100">
            <Camera class="size-6 text-white" />
          </span>
        </button>
        <button v-else-if="shown.avatar" type="button" @click="lightbox = shown.avatar"
                class="block cursor-zoom-in rounded-full ring-[5px] ring-card"
                :title="locale.t('peerProfile.viewAvatar', 'Открыть аватарку')">
          <Avatar :src="shown.avatar" :name="shown.full_name" :role="shown.role" :color="plate" :size="96" />
        </button>
        <div v-else class="w-fit rounded-full ring-[5px] ring-card">
          <Avatar :src="shown.avatar" :name="shown.full_name" :role="shown.role" :color="plate" :size="96" />
        </div>

        <!-- Выбор источника аватарки. Тот же приём, что у MyStatusPicker: список плюс
             прозрачная подложка на весь экран, закрывающая его кликом мимо, — иначе на
             телефоне меню нечем закрыть, не выбрав пункт.
             🔥 НА ТЕЛЕФОНЕ МЕНЮ — ЛИСТ СНИЗУ, А НЕ ВЫПАДАЮЩИЙ СПИСОК (жалоба Ярослава,
             31.08.2026: «нажимаешь изменить аву — всё ломается и ничего не вылазит»).
             Причина: список привязан `absolute` к АВАТАРКЕ внутри карточки, а открывают
             его ещё и кнопкой из левой колонки редактора профиля. На широком экране
             колонки стоят рядом и меню рядом с кнопкой; на узком они складываются
             стопкой, карточка уезжает вниз — и меню открывалось на 1298 px при высоте
             экрана 740, то есть на 558 px ниже видимой области (замерено).
             ⚠️ Хуже всего было то, что подложка `fixed inset-0` при этом рисуется
             ИСПРАВНО и на весь экран: невидимое меню плюс прозрачная стена, глотающая
             нажатия, читаются как «приложение зависло», а не как «меню за краем». Отсюда
             вторая половина жалобы — «всё ломается».
             ⚠️ Якорь к аватарке на `sm+` СОХРАНЁН: в карточке собеседника меню открывают
             нажатием на саму аватарку, и там выпадающий список стоит на своём месте. -->
        <template v-if="editable && avatarMenuOpen">
          <div class="fixed inset-0 z-30" @click="avatarMenuOpen = false" />
          <div class="fixed inset-x-3 bottom-3 z-40 overflow-hidden rounded-lg border border-border2
                      bg-card py-1 shadow-card
                      sm:absolute sm:inset-x-auto sm:bottom-auto sm:left-0 sm:top-[calc(100%+0.375rem)] sm:w-52">
            <button type="button" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2"
                    @click="avatarMenuOpen = false; editingAvatar = true">
              <ImageIcon class="size-4 shrink-0 text-text3" />{{ locale.t('profile.avatarImage', 'Изображение') }}
            </button>
            <button type="button" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text hover:bg-bg2"
                    @click="avatarMenuOpen = false; gifPickerFor = 'avatar'">
              <Film class="size-4 shrink-0 text-text3" />{{ locale.t('profile.avatarGif', 'GIF') }}
            </button>
            <button v-if="shown.avatar" type="button"
                    class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red hover:bg-bg2"
                    @click="avatarMenuOpen = false; applyAvatar('')">
              <Trash2 class="size-4 shrink-0" />{{ locale.t('profile.avatarRemove', 'Убрать аватарку') }}
            </button>
          </div>
        </template>
      </div>

      <!-- Порядок задан Владом и повторяет Discord: имя → логин → действие. Имя и
           логин разными строками, а не через точку: логин это адрес, а не подпись. -->
      <div class="mt-3 min-w-0">
        <p class="truncate font-title text-2xl font-extrabold leading-tight text-text" v-bind="nameDecoration">
          {{ shown.full_name || '…' }}
        </p>
        <p v-if="shown.login" class="mt-0.5 truncate text-sm text-text3">@{{ shown.login }}</p>
        <p class="mt-1 truncate text-[13px] text-text2">{{ metaLine }}</p>
      </div>

      <button v-if="!isSelf" type="button" @click="sendMessage"
              class="mt-3.5 flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5
                     text-sm font-semibold text-white transition hover:bg-accent2 sm:w-auto sm:justify-start">
        <Send class="size-4" />{{ locale.t('peerProfile.messageLong', 'Написать сообщение') }}
      </button>

      <!-- О себе -->
      <div class="mt-4 rounded-lg border border-border bg-card2 p-3">
        <p class="mb-1.5 text-[11px] uppercase tracking-wide text-text3">{{ locale.t('profile.about', 'О себе') }}</p>
        <template v-if="editable">
          <textarea v-model="draftBio" :maxlength="BIO_LIMIT" rows="3"
                    :placeholder="locale.t('profile.aboutPlaceholder', 'Например: куратор группы К-24, веду сети и базы данных.')"
                    class="w-full resize-none rounded-md border border-border2 bg-card px-2.5 py-1.5 text-sm text-text outline-none focus:border-accent" />
          <div class="mt-1.5 flex items-center gap-3">
            <span class="text-xs" :class="(BIO_LIMIT - draftBio.length) <= 20 ? 'text-red' : 'text-text3'">
              {{ locale.t('profile.charsLeft', { n: BIO_LIMIT - draftBio.length }) }}
            </span>
            <!-- Своей кнопки «Сохранить» тут больше нет — сохраняет общая кнопка в
                 Profile.vue (см. commit() выше), одним действием со всем профилем. -->
          </div>
        </template>
        <p v-else class="whitespace-pre-wrap text-sm text-text2">
          {{ shown.bio || locale.t('peerProfile.noBio', 'Пока ничего не написал(а).') }}
        </p>
      </div>

      <!-- День рождения отдельной строкой, а не в общей подписи под именем: его ищут
           глазами, чтобы поздравить, и в перечне «роль · группа · дата» он терялся.
           Год не хранится и не показывается — только день и месяц. -->
      <div v-if="shown.birthday" class="mt-3 flex items-center gap-2 rounded-lg border border-border
                                        bg-card2 px-3 py-2.5">
        <span class="text-base leading-none">🎂</span>
        <span class="text-[11px] uppercase tracking-wide text-text3">
          {{ locale.t('peerProfile.birthday', 'День рождения') }}
        </span>
        <span class="ml-auto text-sm font-semibold text-text">{{ shown.birthday }}</span>
      </div>

      <!-- Заметка — видна только автору, в обоих режимах. САМАЯ НИЖНЯЯ: это рабочая
           запись о человеке, а не часть его профиля. -->
      <div class="mt-3 rounded-lg border border-dashed border-border2 bg-card2 p-3">
        <p class="mb-1.5 text-[11px] uppercase tracking-wide text-text3">
          {{ locale.t('peerProfile.note', 'Заметка') }}
          <span class="normal-case text-text3">— {{ locale.t('peerProfile.noteHint', 'видна только вам') }}</span>
        </p>
        <textarea v-model="note" @blur="onNoteBlur" rows="2" maxlength="300"
                  :placeholder="locale.t('peerProfile.notePlaceholder', 'Личная заметка (не видна собеседнику)')"
                  class="w-full resize-none rounded-md border border-border2 bg-card px-2.5 py-1.5 text-sm text-text outline-none focus:border-accent" />
      </div>
    </div>

    <AvatarCropper v-if="editingAvatar" :current="profile.avatar" @save="onSaveAvatar" @close="editingAvatar = false" />
    <!-- Тот же пикер Klipy, что у поля ввода в чате; здесь по центру экрана — у карточки
         профиля нет поля ввода внизу справа, к которому он приклеен в мессенджере. -->
    <GifPicker v-if="gifPickerFor" anchor="center"
               :title="gifPickerFor === 'banner'
                 ? locale.t('profile.pickBannerGif', 'Гифка на баннер профиля')
                 : locale.t('profile.pickAvatarGif', 'Гифка на аватарку')"
               @pick="onGifPicked" @close="gifPickerFor = ''" />

    <!-- Фуллскрин аватарки/баннера чужого профиля: полноценный файл без обрезки,
         клик по фону закрывает (Влад). -->
    <div v-if="lightbox" class="fixed inset-0 z-[80] grid place-items-center bg-black/80 p-4"
         @click="lightbox = ''">
      <img :src="lightbox" alt="" class="max-h-[90vh] max-w-[90vw] rounded-lg object-contain shadow-2xl" />
    </div>
  </div>
</template>
