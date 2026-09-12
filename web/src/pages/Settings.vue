<script setup>
// Settings — «Настройки» (для студента/преподавателя/админа). Сюда вынесены персональные
// настройки, раньше сваленные в «Профиль»: оформление (темы), вход по биометрии (2FA) и
// озвучка Вектора. В «Профиле» остаются только сведения об аккаунте и уведомления.
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useEasterStore } from '@/stores/easterEggs'
import { useRouter } from 'vue-router'
import { Fingerprint, Trash2, ShieldCheck, Volume2, VolumeX, AudioLines, GraduationCap, Check, Mic, MicOff, BellOff, RefreshCw, TriangleAlert, LogOut, X, ChevronLeft, ChevronRight, Pencil, Vibrate, VibrateOff } from '@lucide/vue'
import { adminApi, authApi, meApi } from '@/api/endpoints'
import FarewellOverlay from '@/components/FarewellOverlay.vue'
import DarkSoulsFarewell from '@/components/easter/DarkSoulsFarewell.vue'
import { platformAuthenticatorAvailable, enablePasskey } from '@/api/webauthn'
import MfaCard from '@/components/settings/MfaCard.vue'
import { useTtsStore } from '@/stores/tts'
import { useVoiceStore } from '@/stores/voice'
import { useAuthStore } from '@/stores/auth'
import { SCALES } from '@/utils/grading'
import Card from '@/components/ui/Card.vue'
import AppButton from '@/components/ui/AppButton.vue'
import ToggleRow from '@/components/ui/ToggleRow.vue'
import ThemeCustomizer from '@/pages/admin/ThemePage.vue'
import LanguagePicker from '@/components/ui/LanguagePicker.vue'
import { useLocaleStore } from '@/stores/locale'
import { catsForRole, RAILLESS_VIEWS } from '@/config/settingsSections'
import haptics from '@/utils/haptics'
// Профиль переехал ВНУТРЬ настроек отдельной категорией (просьба Влада): страницы
// `/…/profile` больше нет в меню, редактор открывается отсюда и из карточки себя.
import ProfilePage from '@/pages/Profile.vue'
import Avatar from '@/components/ui/Avatar.vue'
import { useProfileStore } from '@/stores/profile'
import { profilePlate } from '@/theme/palette'

const tts = useTtsStore()
const auth = useAuthStore()
const voice = useVoiceStore()
const loc = useLocaleStore()

//Вибрация: настройка УСТРОЙСТВА (localStorage), как микрофон и озвучка. Держим локальным
//ref'ом, а не стором: у неё нет ни сетевой части, ни состояния сложнее «вкл/выкл», и
//отдельный стор ради булева значения — лишний слой.
const hapticsSupported = haptics.supported()
const hapticsOn = ref(haptics.enabled())
//Системное «уменьшить движение» сильнее нашего тумблера — показываем это честно, иначе
//включённая и молчащая отдача читается как поломка.
const reducedMotion = ref(false)
try {
  reducedMotion.value = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
} catch {
  //Старый движок без matchMedia — считаем, что ограничения нет.
}
function toggleHaptics() {
  hapticsOn.value = !hapticsOn.value
  haptics.setEnabled(hapticsOn.value)
}
const router = useRouter()
const profileStore = useProfileStore()
//Роль решает, показывать ли второй фактор: у администратора его нет вовсе (см. карточку).
const isAdminRole = computed(() => auth.role === 'admin')

// ── Заморозка оценок (только администратор) ──────────────────────────────────────
// Дата «ДД.ММ», после которой студент видит только итоговые. Пусто — правило выключено.
// ⚠️ Год не хранится: дата повторяется каждый учебный год, и записанный год пришлось бы
// править вручную каждый июнь.
const freezeDate = ref('')
const freezePhase = ref('')
const freezeError = ref('')
const freezeSaving = ref(false)
async function loadFreeze() {
  if (auth.role !== 'admin') return
  try {
    const { data } = await adminApi.gradesFreeze()
    freezeDate.value = data.date || ''
    freezePhase.value = data.phase || ''
  } catch { /* не критично: карточка просто покажет пустое поле */ }
}
async function saveFreeze() {
  freezeSaving.value = true
  freezeError.value = ''
  try {
    const { data } = await adminApi.setGradesFreeze(freezeDate.value.trim())
    freezePhase.value = data.phase || ''
  } catch (e) {
    //Отказ ПОКАЗЫВАЕМ: молча не сохранённая дата означала бы выключённое правило при
    //заполненном поле — админ считал бы, что настроил.
    freezeError.value = e?.response?.data?.detail || loc.t('settings.freezeFailed', 'Не удалось сохранить дату')
  } finally { freezeSaving.value = false }
}
onMounted(loadFreeze)

// ── Выход из аккаунта ────────────────────────────────────────────────────────────
// Переехал сюда из шапки (живой отзыв 3.5.6). Причина не косметическая: выход стоял
// в постоянно видимой полосе рядом с переключателем темы и статусом, то есть рядом с
// тем, что нажимают по несколько раз в день — а сам он действие редкое и необратимое
// (JWT живёт жёстко 5 ч, §6: после выхода нужен полный повторный вход с паролем).
// Здесь он в самом низу страницы, до которой надо осознанно долистать.
// Прощальная анимация (Вектор машет ~1.2 с) уехала вместе с ним — сам logout при этом
// выполняется СРАЗУ, анимация задерживает только переход экрана: если что-то пойдёт не
// так, человек всё равно окажется разлогинен.
const FAREWELL_MS = 1200
const farewell = ref(false)
// Прощание Dark Souls. Местное состояние, а не стор — см. onLogout ниже.
const darkSouls = ref(false)
const farewellName = computed(() => (auth.user?.name || '').trim())
async function onLogout() {
  // ⚠️ Dark Souls ЗАМЕНЯЕТ обычное прощание Вектора: две прощальные заставки подряд
  // читались бы как сбой, поэтому либо одна, либо другая.
  const egg = await easter.roll('dark_souls_logout')

  // 🔥 АЧИВКУ ЗАБИРАЕМ ЗДЕСЬ, ДО `logout()`, И ЭТО КУПЛЕНО ДЕФЕКТОМ (23.08.2026).
  // Раньше её закрывала сама сцена — через 700 мс после показа. Но `auth.logout()`
  // стирает токен НЕМЕДЛЕННО, и к этому моменту запрос уходил уже без авторизации:
  // сервер отвечал 401, ачивка не выдавалась, а человек честно видел пасхалку и
  // считал, что его обманули. Ошибка тихая — на экране всё правильно.
  // ⚠️ Правило общее: пасхалка, привязанная к ВЫХОДУ, обязана закрываться до выхода.
  // Ждать её показа нельзя (см. ниже), значит момент один — этот.
  if (egg) {
    await easter.claim('dark_souls_logout')
    // 🔥 СЦЕНУ ПОКАЗЫВАЕМ ИЗ МЕСТНОГО СОСТОЯНИЯ, А НЕ ИЗ СТОРА (найдено 24.08.2026 по
    // точному наблюдению Влада: «ачивка есть, анимации нет, но выход подвисает на пару
    // секунд»). Это подвисание и БЫЛО пасхалкой: `auth.logout()` ниже зовёт
    // `easter.reset()` — он обнуляет стор, чтобы тост не утёк следующему человеку на
    // общем компьютере, — и тем самым СТИРАЕТ сцену за мгновение до её показа. На
    // экране оставались пустые 5.2 секунды ожидания, то есть ровно «фриз».
    //
    // ⚠️ Конфликт неустраним по существу: выход обязан стирать состояние пасхалок, а
    // прощальная сцена обязана этот выход пережить. Значит она не состояние пасхалок, а
    // состояние СТРАНИЦЫ — как обычное прощание Вектора рядом. Слот в сторе освобождаем
    // сразу, чтобы он не держал ни замок перехода, ни вопрос «точно уйти?».
    easter.close()
    darkSouls.value = true
  }

  const wait = egg ? 5200 : FAREWELL_MS         // сцене нужно время догореть
  if (!egg) farewell.value = true
  // ⚠️ Сам выход выполняется СРАЗУ и не ждёт анимацию: заставка задерживает только
  // переход экрана. Пойдёт что-то не так — человек всё равно окажется разлогинен.
  try { await auth.logout() } finally {
    setTimeout(() => {
      //⚠️ Гасим сцену ПЕРЕД переходом. Флаг живёт в сторе, а у экрана входа свой хост
      //пасхалок (он нужен для Far Cry) — не сняв флаг, мы получаем ВТОРОЙ показ той же
      //заставки уже поверх формы входа, с начала. Именно это Влад и увидел.
      easter.close()
      router.push('/login')
    }, wait)
  }
}

// ── Уведомления: какие категории человек согласен получать ───────────────────────
// Настройка АККАУНТА, а не устройства: решение «слать или нет» принимает сервер до
// отправки (rustore_push.notify_login). Держать её в localStorage было бы бессмысленно —
// пуш уже прилетел бы на телефон, и выключать его было бы поздно.
//
// Ключи обязаны совпадать с rustore_push.ALL_CATEGORIES: сервер знает только их.
const NOTIFY_KINDS = computed(() => [
  { key: 'grades', label: loc.t('settings.notify.grades.label', 'Оценки'), hint: loc.t('settings.notify.grades.hint', 'Новая оценка и исправление уже выставленной') },
  { key: 'homework', label: loc.t('settings.notify.homework.label', 'Домашние задания'), hint: loc.t('settings.notify.homework.hint', 'Преподаватель задал работу на дом') },
  { key: 'schedule', label: loc.t('settings.notify.schedule.label', 'Расписание'), hint: loc.t('settings.notify.schedule.hint', 'Замены и правки в расписании вашей группы') },
  { key: 'messages', label: loc.t('settings.notify.messages.label', 'Сообщения'), hint: loc.t('settings.notify.messages.hint', 'Личные чаты, группы и каналы') },
  { key: 'events', label: loc.t('settings.notify.events.label', 'Мероприятия'), hint: loc.t('settings.notify.events.hint', 'Олимпиады, конкурсы, объявления') },
  { key: 'reminders', label: loc.t('settings.notify.reminders.label', 'Напоминания'), hint: loc.t('settings.notify.reminders.hint', 'То, о чём вы сами просили напомнить') },
  // Приходит ТОЛЬКО куратору (о студентах его группы) — остальным ролям строку не
  // показываем: у студента переключатель «риск отчисления» читался бы как предложение
  // отключить сам риск, а не уведомление о нём.
  { key: 'risk', label: loc.t('settings.notify.risk.label', 'Риск отчисления'), hint: loc.t('settings.notify.risk.hint', 'Куратору — о студентах его группы в зоне риска'), roles: ['teacher', 'admin'] },
])
// Что реально показываем этой роли. Ключ без ограничения виден всем.
const visibleNotifyKinds = computed(() =>
  NOTIFY_KINDS.value.filter((k) => !k.roles || k.roles.includes(auth.role)))
const notify = ref(Object.fromEntries(NOTIFY_KINDS.value.map((k) => [k.key, true])))
const notifySaving = ref('')
const notifyError = ref('')

async function loadNotify() {
  try {
    const { data } = await meApi.getPrefs()
    const box = data?.prefs?.notify || {}
    // ОТСУТСТВИЕ ключа значит «включено» — ровно как трактует его сервер. Иначе первый
    // же заход в настройки показал бы всё выключенным, хотя уведомления приходят.
    for (const k of NOTIFY_KINDS.value) {
      notify.value[k.key] = box[k.key] !== false
    }
  } catch { /* не загрузилось — показываем значения по умолчанию */ }
}

async function toggleNotify(key, value) {
  const prev = notify.value[key]
  notify.value[key] = value          // отвечаем сразу: переключатель не должен «залипать»
  notifySaving.value = key
  notifyError.value = ''
  try {
    await meApi.setPrefs({ notify: { ...notify.value } })
  } catch (e) {
    // Откатываем ВИДИМОЕ состояние: молча оставить переключатель в новом положении
    // значит соврать — человек уверен, что отключил, а уведомления продолжают идти.
    notify.value[key] = prev
    notifyError.value = e.response?.data?.detail || loc.t('settings.notifySaveFailed', 'Не удалось сохранить настройку.')
  } finally {
    notifySaving.value = ''
  }
}

// ── Пуши на этом телефоне: почему их может не быть ───────────────────────────────
// Уведомления — единственная часть продукта, отказ которой невидим: и человек, и мы
// узнаём о нём только по отсутствию сообщений, то есть никогда. Поэтому состояние
// моста показываем явно.
const pushInfo = ref(null)
async function loadPushInfo() {
  try {
    const push = await import('@/services/push')
    if (!push.isAvailable()) return          // сайт/десктоп — раздела просто нет
    pushInfo.value = push.diagnostics() || { has_token: false, error: '', permission: true }
  } catch { pushInfo.value = null }
}

// ── Версия веб-части (обновления «по воздуху») ───────────────────────────────────
const bundle = ref(null)          // { version } | null — вне приложения null
const bundleBusy = ref(false)
const bundleMsg = ref('')
const otaLog = ref([])            // последние события обновления (см. main.js)

async function loadBundle() {
  try {
    const { CapacitorUpdater } = await import('@capgo/capacitor-updater')
    const cur = await CapacitorUpdater.current()
    bundle.value = { version: cur?.bundle?.version || loc.t('settings.bundleBuiltIn', 'встроенная') }
  } catch { bundle.value = null }
  try {
    otaLog.value = JSON.parse(localStorage.getItem('gb_ota_log') || '[]')
  } catch { otaLog.value = [] }
}

// ⚠️ Живой отзыв Ярослава (11.08.2026): «вроде реализовано автообновление, но оно не
// работает — написано, что доступна какая-то версия, а как её скачать непонятно».
// Так и было: проверка ТОЛЬКО печатала номер версии, а `download()`/`set()` не звал
// никто во всём web/src — кнопки установки физически не существовало. Держим найденное
// обновление здесь, чтобы кнопка появлялась ровно тогда, когда есть что ставить.
const pending = ref(null)         // { version, url, checksum } | null

async function checkUpdate() {
  bundleBusy.value = true
  bundleMsg.value = ''
  pending.value = null
  try {
    const { CapacitorUpdater } = await import('@capgo/capacitor-updater')
    const latest = await CapacitorUpdater.getLatest()
    if (!latest?.version || latest.version === bundle.value?.version) {
      bundleMsg.value = loc.t('settings.bundleUpToDate', 'У вас последняя версия.')
    } else if (!latest.url) {
      // Версия новее есть, а ссылки нет — качать нечего. Молчать здесь нельзя: человек
      // увидел бы «доступна версия» и снова искал бы несуществующую кнопку.
      bundleMsg.value = loc.t('settings.bundleNoUrl', { version: latest.version })
    } else {
      pending.value = { version: latest.version, url: latest.url, checksum: latest.checksum || '' }
      bundleMsg.value = loc.t('settings.bundleAvailable', { version: latest.version })
    }
  } catch (e) {
    bundleMsg.value = loc.t('settings.bundleCheckFailed', { error: e?.message || e })
  } finally {
    bundleBusy.value = false
  }
}

async function installUpdate() {
  if (!pending.value) return
  bundleBusy.value = true
  bundleMsg.value = loc.t('settings.bundleDownloading', 'Скачиваем обновление…')
  try {
    const { CapacitorUpdater } = await import('@capgo/capacitor-updater')
    const info = await CapacitorUpdater.download({
      url: pending.value.url,
      version: pending.value.version,
      ...(pending.value.checksum ? { checksum: pending.value.checksum } : {}),
    })
    if (!info?.id) throw new Error('bundle id')
    bundleMsg.value = loc.t('settings.bundleApplying', 'Применяем — приложение перезапустится…')
    // ⚠️ После set() плагин сам перезагружает веб-часть, и код ПОСЛЕ этого вызова уже не
    // отработает (так прямо написано в его документации) — поэтому ни сообщений, ни
    // снятия флага занятости здесь быть не должно.
    await CapacitorUpdater.set({ id: info.id })
  } catch (e) {
    bundleMsg.value = loc.t('settings.bundleInstallFailed', { error: e?.message || e })
    bundleBusy.value = false
  }
}

// ── Нативная часть (сам APK) ─────────────────────────────────────────────────────
// OTA везёт ТОЛЬКО интерфейс. Виджет расписания, мост пушей и права — внутри apk, и
// меняются лишь переустановкой; человеку это не видно, поэтому проверяем отдельно и
// говорим прямо, что нужно скачать файл.
const apk = ref(null)             // { version, url } | null — есть сборка новее нашей
async function loadApkInfo() {
  if (!bundle.value) return       // не приложение — нечего и сравнивать
  try {
    const { App } = await import('@capacitor/app')
    const info = await App.getInfo()
    const mine = parseInt(info?.build || '0', 10) || 0
    const { appApi } = await import('@/api/endpoints')
    const { data } = await appApi.apkInfo()
    const theirs = parseInt(data?.nativeVersion || 0, 10) || 0
    if (theirs > mine && data?.url) apk.value = { version: data.versionName || String(theirs), url: data.url }
  } catch { apk.value = null }
}
async function openApk() {
  if (!apk.value) return
  try {
    const { Browser } = await import('@capacitor/browser')
    await Browser.open({ url: apk.value.url })
  } catch { window.open(apk.value.url, '_blank') }
}

// ── Шкала оценивания (§ролей, 3.3.1) — только препод: в ЧЁМ он вводит/видит оценки.
// Средний балл/итоговая всё равно всегда в 5-балльной — сервер сам конвертирует.
const scaleOptions = computed(() => Object.entries(SCALES).map(([id, s]) => ({ id, label: s.label })))
const gradingScale = ref('5')
const scaleSaving = ref(false)
async function loadGradingScale() {
  try {
    const { data } = await meApi.getPrefs()
    gradingScale.value = data?.prefs?.grading_scale || '5'
  } catch { /* дефолт "5" уже стоит */ }
}
async function pickScale(id) {
  if (id === gradingScale.value || scaleSaving.value) return
  scaleSaving.value = true
  try {
    await meApi.setPrefs({ grading_scale: id })
    gradingScale.value = id
  } finally { scaleSaving.value = false }
}

// ── Озвучка Вектора: 3 режима (Голос → Бубнеж → Выкл) + выбор голоса ──────────────
function cycleVoiceMode() {
  tts.unlock()          // «разбудить» AudioContext из жеста — иначе проба не зазвучит
  tts.cycleMode()
  if (tts.mode === 'mumble') previewMumble()
}
function previewVoice(v) {
  tts.unlock()
  tts.setVoice(v)
  if (tts.mode === 'voice') tts.speak(loc.t('settings.ttsPreviewVoice', 'Привет! Я Вектор. Буду озвучивать ответы этим голосом.'))
}
function previewMumble() {
  tts.unlock()
  tts.speak(loc.t('settings.ttsPreviewMumble', 'Привет! Я Вектор.'))
}

// ── Вход по биометрии (passkeys / 2FA) ───────────────────────────────────────────
const canBiometric = ref(false)
const passkeys = ref([])
const pkBusy = ref(false)
const pkMsg = ref('')

async function loadPasskeys() {
  try { passkeys.value = (await authApi.webauthnList()).data.credentials || [] } catch { passkeys.value = [] }
}
onMounted(async () => {
  tts.refreshStatus()
  //Список микрофонов — БЕЗ запроса разрешения (ask=false): всплывающее окно «разрешить
  //микрофон» при простом заходе в настройки выглядит как попытка подслушать. Названия
  //подтянутся по кнопке «Обновить список», то есть по осознанному действию.
  voice.refresh(false)
  loadNotify()
  loadPushInfo()
  await loadBundle()
  loadApkInfo()          //строго ПОСЛЕ loadBundle: вне приложения проверять нечего
  try { canBiometric.value = await platformAuthenticatorAvailable() } catch { canBiometric.value = false }
  if (canBiometric.value) await loadPasskeys()
  if (auth.role === 'teacher') await loadGradingScale()
})

async function addPasskey() {
  pkBusy.value = true; pkMsg.value = ''
  try {
    const name = navigator.platform || loc.t('settings.thisDevice', 'Это устройство')
    await enablePasskey(name)
    pkMsg.value = loc.t('settings.passkeyAdded', 'Готово! Теперь можно входить по Face ID / отпечатку.')
    await loadPasskeys()
  } catch (e) {
    if (e?.name === 'NotAllowedError' || e?.name === 'AbortError') pkMsg.value = ''
    else pkMsg.value = e.response?.data?.detail || loc.t('settings.passkeyFailed', 'Не удалось включить биометрию.')
  } finally { pkBusy.value = false }
}
async function removePasskey(id) {
  pkBusy.value = true
  try { await authApi.webauthnDelete(id); await loadPasskeys() } finally { pkBusy.value = false }
}
function fmtDate(s) { return (s || '').slice(0, 10) }
// G-Man в «Настройках»: он проявляется по мере того, как человек тут сидит,
// поэтому бросок делаем на входе, а само проявление тянется внутри сцены.
const easter = useEasterStore()
onMounted(() => easter.roll('gman_observer'))

// ── Категории и подкатегории (просьба Влада, 31.08.2026: «как в дс») ────────────────
// Состав живёт в `@/config/settingsSections.js` — один список на ТРИ потребителя:
// рельс категорий на ПК, двухуровневый список на телефоне и выпадающие подкатегории.
// Держать его здесь означало бы три копии, обязанные разойтись.
//⚠️ Способности УСТРОЙСТВА передаём сюда же: пункт «Вибрация» обязан появляться и
//пропадать вместе со своей карточкой, иначе человек нажмёт его и увидит пустоту.
const cats = computed(() => catsForRole(auth.role, { haptics: hapticsSupported }))
// Профиль открыт первым: на него ведут карточка себя сверху, кнопка в шапке и
// переадресация со старого `/…/profile`. Пунктом рельса он больше не значится
// (просьба Влада, 02.09.2026 — см. пояснение в `settingsSections.js`).
const cat = ref('profile')
// Телефон: сперва список категорий (как в Discord), потом содержимое с кнопкой «назад».
// На ПК не используется вовсе — там рельс и содержимое видны одновременно.
const showList = ref(true)
// Какая категория раскрыта в рельсе (выпадающий список подкатегорий).
// ⚠️ Пусто на старте: раскрывать нечего — профиль из рельса убран, а раскрытый список
// чужой категории обещал бы, что открыта именно она.
const openSub = ref('')

// ⚠️ Выбранная категория может пропасть у роли: тогда правая часть оказалась бы пустой
// без объяснения. Возвращаемся к первой доступной.
// ⚠️ Разделы БЕЗ пункта в рельсе (профиль) под это правило не попадают: их в списке нет
// по замыслу, и сброс выбрасывал бы человека из профиля при каждом пересчёте ролей.
watch(cats, (list) => {
  if (RAILLESS_VIEWS.includes(cat.value)) return
  if (!list.some((c) => c.id === cat.value)) cat.value = list[0]?.id || 'appearance'
})

/**
 * Классы секции: показываем только выбранную категорию.
 * ⚠️ Именно `hidden`, а не `lg:hidden`: с двухуровневым телефоном (список → раздел)
 * категории работают на ОБЕИХ платформах. С `lg:` телефон показывал бы все разделы
 * подряд под заголовком одного — то есть заголовок врал бы о содержимом.
 */
function sec(id) { return cat.value === id ? '' : 'hidden' }

// Заголовок правой части. ⚠️ У профиля пункта в рельсе нет, поэтому `cats.find` его не
// найдёт — без этой ветки открытый профиль был бы подписан словом «Настройки», то есть
// заголовок врал бы о содержимом (ровно то, из-за чего `sec()` вообще завели).
const headTitle = computed(() => {
  if (cat.value === 'profile') return loc.t('nav.profile', 'Профиль')
  const c = cats.value.find((x) => x.id === cat.value)
  return loc.t(c?.i18n || 'nav.settings', c?.label || 'Настройки')
})

// Профиль монтируем при первом открытии его категории и больше не снимаем — см.
// пояснение у <ProfilePage> в разметке (пасхалки бросаются в onMounted).
const profileSeen = ref(false)
watch(cat, (id) => { if (id === 'profile') profileSeen.value = true }, { immediate: true })

function pickCat(id) {
  cat.value = id
  openSub.value = openSub.value === id ? '' : id   // повторное нажатие складывает список
  showList.value = false
}

/**
 * Прокрутка к подкатегории. Якорь — `set-<id>`, он же `id` карточки в разметке.
 * ⚠️ Промах здесь ТИХИЙ: элемента нет — ничего не происходит, ошибки не будет. Поэтому
 * состав подкатегорий и якоря в разметке держит `web/tests/settingsCategories.test.mjs`.
 */
async function goSub(catId, subId) {
  if (cat.value !== catId) { cat.value = catId; showList.value = false }
  await nextTick()
  const el = document.getElementById(`set-${subId}`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// Закрытие оверлея = уход со страницы настроек. `back()` возвращает туда, откуда
// пришли; если истории нет (открыли по прямой ссылке) — на главную своей роли.
function closeSettings() {
  if (window.history.length > 1) router.back()
  else router.push(`/${auth.role || 'student'}`)
}
function onEsc(e) { if (e.key === 'Escape') closeSettings() }
onMounted(() => window.addEventListener('keydown', onEsc))
onBeforeUnmount(() => window.removeEventListener('keydown', onEsc))
</script>

<template>
  <!-- flex+gap, а НЕ space-y-*: в Tailwind 4 `space-y` разворачивается в правило с
       нулевой специфичностью, и любой конкурирующий margin съедает промежуток без следа
       в разметке (уже ловили на статистике студента). -->
  <!-- ⚠️ НА ПК ЭТО ОВЕРЛЕЙ, НА ТЕЛЕФОНЕ — ОБЫЧНАЯ СТРАНИЦА, и переключает их ТОЛЬКО CSS
       (`lg:`). Второй `matchMedia` здесь завёл бы вторую границу ширины рядом с `LG_PX`
       оболочки; `web/tests/breakpoint.test.mjs` заведён ровно против этого — разъехавшись,
       они дают полосу ширины, где раскладка и логика спорят, и видно это на одной
       конкретной ширине окна, то есть почти никогда.
       Размытие — `lg:backdrop-blur-md` по подложке: за ней остаётся оболочка (сайдбар,
       шапка), и это и есть «фон размывается». -->
  <div class="lg:fixed lg:inset-0 lg:z-40 lg:grid lg:place-items-center lg:bg-black/55 lg:p-6 lg:backdrop-blur-md"
       @click.self="closeSettings()">
    <div class="lg:flex lg:h-full lg:max-h-[52rem] lg:w-full lg:max-w-5xl lg:overflow-hidden
                lg:rounded-2xl lg:border lg:border-border2 lg:bg-card lg:shadow-card">

      <!-- ЛЕВЫЙ СТОЛБЕЦ (ПК) / ПЕРВЫЙ ЭКРАН (телефон): профиль сверху + категории.
           ⚠️ На телефоне это ОТДЕЛЬНЫЙ экран, а не колонка: список категорий и их
           содержимое рядом на 360 px не помещаются, и попытка ужать рельс дала бы
           колонку в треть экрана с обрезанными подписями. Поэтому два уровня, как в
           приложении Discord: список → раздел → «назад». -->
      <aside class="flex flex-col gap-0.5 lg:w-60 lg:shrink-0 lg:overflow-y-auto
                    lg:border-r lg:border-border lg:bg-bg2 lg:p-3"
             :class="showList ? 'flex' : 'hidden lg:flex'">

        <!-- Шапка с профилем — ЕДИНСТВЕННЫЙ вход в редактор профиля (02.09.2026).
             Пункт «Профиль» из списка ниже убран: он вёл ровно сюда же, а два пункта в
             одно место читаются как два разных раздела. Подсветка обязательна — без неё
             открытый профиль выглядит как «ни одна категория не выбрана». -->
        <button type="button" @click="pickCat('profile')"
                class="mb-2 flex items-center gap-2.5 rounded-lg border bg-card px-2.5 py-2
                       text-left transition-colors hover:border-accent lg:bg-card"
                :class="cat === 'profile' ? 'border-accent' : 'border-border2'"
                :aria-label="loc.t('profile.openEditor', 'Открыть редактор профиля')">
          <Avatar :src="profileStore.avatar" :name="auth.user?.name || ''" :role="auth.role"
                  :color="profilePlate(profileStore.color)" :size="36" />
          <span class="min-w-0 flex-1">
            <span class="block truncate text-[13px] font-semibold leading-tight text-text">{{ auth.user?.name || '' }}</span>
            <span class="block truncate text-[11px] leading-tight text-text3">
              {{ loc.t('profile.editProfile', 'Редактировать профиль') }}
            </span>
          </span>
          <Pencil class="size-3.5 shrink-0 text-text3" />
        </button>

        <p class="mb-1 px-2 pt-1 text-tiny font-bold uppercase tracking-wide text-text3">
          {{ loc.t('nav.settings', 'Настройки') }}
        </p>

        <template v-for="c in cats" :key="c.id">
          <button type="button" @click="pickCat(c.id)"
                  class="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors"
                  :class="cat === c.id ? 'bg-accent-glow font-semibold text-accent'
                                       : 'text-text2 hover:bg-bg hover:text-text'">
            <component :is="c.icon" class="size-4 shrink-0" />
            <span class="min-w-0 flex-1 truncate">{{ loc.t(c.i18n, c.label) }}</span>
            <!-- Стрелка: на телефоне «войти в раздел», на ПК «раскрыть подкатегории».
                 ⚠️ Крутим её ТОЛЬКО когда список раскрыт, иначе она обещает раскрытие
                 там, где его уже нет. -->
            <ChevronRight class="size-3.5 shrink-0 text-text3 transition-transform"
                          :class="openSub === c.id ? 'lg:rotate-90' : ''" />
          </button>

          <!-- Подкатегории: прокрутка к нужному месту раздела. На телефоне не
               показываем — там раздел открывается целиком отдельным экраном, и
               оглавление к одному экрану было бы лишним уровнем. -->
          <div v-if="openSub === c.id && c.subs.length > 1" class="hidden lg:block lg:pb-1 lg:pl-8">
            <button v-for="s in c.subs" :key="s.id" type="button" @click="goSub(c.id, s.id)"
                    class="block w-full truncate rounded px-2 py-1 text-left text-[12.5px] text-text3
                           transition-colors hover:bg-bg hover:text-text">
              {{ loc.t(s.i18n, s.label) }}
            </button>
          </div>
        </template>
      </aside>

      <!-- ПРАВЫЙ СТОЛБЕЦ (ПК) / ВТОРОЙ ЭКРАН (телефон). -->
      <div class="flex-col gap-6 lg:min-w-0 lg:flex-1 lg:overflow-y-auto lg:p-6"
           :class="showList ? 'hidden lg:flex' : 'flex'">
        <!-- Заголовок с крестиком — только на ПК: на телефоне выход со страницы делает
             обычная кнопка «назад» оболочки, и второй крестик читался бы как дубль. -->
        <div class="flex items-center gap-2">
          <!-- «Назад» к списку категорий — только на телефоне: на ПК рельс виден всегда,
               и кнопка возврата к нему вела бы в никуда. -->
          <button type="button" @click="showList = true" class="grid size-8 shrink-0 place-items-center
                  rounded-md text-text2 hover:bg-bg2 hover:text-text lg:hidden"
                  :aria-label="loc.t('common.back', 'Назад')">
            <ChevronLeft class="size-5" />
          </button>
          <h2 class="min-w-0 flex-1 truncate font-title text-xl font-extrabold text-text">
            {{ headTitle }}
          </h2>
          <button type="button" @click="closeSettings()"
                  :aria-label="loc.t('common.close', 'Закрыть')"
                  :title="loc.t('common.close', 'Закрыть') + ' (Esc)'"
                  class="hidden size-8 shrink-0 place-items-center rounded-md text-text3 hover:bg-bg2 hover:text-text lg:grid">
            <X class="size-5" />
          </button>
        </div>

    <!-- ПРОФИЛЬ — переехал сюда из отдельной вкладки меню (просьба Влада).
         🔥 МОНТИРУЕТСЯ ОДИН РАЗ И БОЛЬШЕ НЕ СНИМАЕТСЯ, дальше прячется классом. Так
         сделано ради пасхалок: страница профиля бросает штамп Papers Please и точку
         сохранения Undertale в `onMounted`, а у этих двух нет кулдауна. Через обычный
         `v-if` бросок случался бы на КАЖДОМ возврате в категорию — щёлкая по рельсу,
         человек выбивал бы «редкую находку» за полминуты, и она перестала бы быть
         находкой. Один монтаж = один бросок за визит, как было у отдельной страницы. -->
    <ProfilePage v-if="profileSeen" :class="sec('profile')" />

    <!-- Оформление (полный кастомайзер тем: пресеты + свой цвет + режим + расписание). -->
    <div id="set-theme" :class="sec('appearance')">
      <h2 class="mb-3 font-title text-lg font-extrabold text-text lg:hidden">{{ loc.t('settings.appearance') }}</h2>
      <ThemeCustomizer />
    </div>

    <!-- Язык интерфейса. Выбор делается ещё на экране входа (там глобус), здесь его
         можно сменить и, главное, ВЫКЛЮЧИТЬ перевод — не теряя выбранный язык. -->
    <Card id="set-language" :class="sec('appearance')" :title="loc.t('settings.language')" :subtitle="loc.t('settings.languageHint')" pad>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex flex-wrap gap-2">
          <button v-for="l in loc.locales" :key="l.code" type="button"
                  class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors"
                  :class="l.code === loc.locale && loc.translateUi
                    ? 'border-accent bg-accent-glow text-text' : 'border-border text-text2 hover:border-accent'"
                  @click="loc.set(l.code)">
            <span class="text-base leading-none">{{ l.flag }}</span>{{ l.name }}
          </button>
        </div>
        <LanguagePicker />
      </div>

      <div class="mt-4">
        <ToggleRow :label="loc.t('settings.languageOff')"
                   :hint="loc.t('settings.languageOffHint')"
                   :model-value="!loc.translateUi"
                   @update:model-value="(v) => loc.setTranslateUi(!v)" />
      </div>
    </Card>

    <!-- Уведомления: что присылать. Настройка АККАУНТА — решение принимает сервер до
         отправки, поэтому она одинакова на телефоне, сайте и десктопе. -->
    <Card id="set-notify" :class="sec('notifications')" :title="loc.t('settings.notifications')" :subtitle="loc.t('settings.notifyHint', 'Что присылать на телефон')">
      <div class="flex flex-col gap-2">
        <ToggleRow v-for="k in visibleNotifyKinds" :key="k.key"
                   :label="k.label" :hint="k.hint"
                   :model-value="notify[k.key]"
                   :disabled="notifySaving === k.key"
                   @update:model-value="(v) => toggleNotify(k.key, v)" />
      </div>

      <p v-if="notifyError" class="mt-3 text-sm text-red">{{ notifyError }}</p>
      <div class="mt-3 flex items-start gap-2.5 rounded-lg border border-border bg-card2 px-3 py-2.5 text-xs text-text3">
        <BellOff class="mt-0.5 size-4 shrink-0" />
        <p>{{ loc.t('settings.notifyDisabledHint', 'Выключенное перестаёт приходить на телефон, но остаётся во вкладке «Уведомления» — историю оценок и заданий выключатель не стирает.') }}</p>
      </div>

      <!-- Состояние пушей на ЭТОМ телефоне. Отказ доставки иначе невидим: и человек,
           и мы узнаём о нём только по отсутствию уведомлений, то есть никогда. -->
      <div v-if="pushInfo" class="mt-3">
        <div v-if="pushInfo.has_token && pushInfo.permission"
             class="flex items-start gap-2.5 rounded-lg border border-border bg-card2 px-3 py-2.5 text-xs text-text3">
          <ShieldCheck class="mt-0.5 size-4 shrink-0 text-accent" />
          <p>{{ loc.t('settings.pushConnected', 'Этот телефон подключён к уведомлениям.') }}</p>
        </div>
        <div v-else class="flex items-start gap-2.5 rounded-lg border border-red/40 bg-card2 px-3 py-2.5 text-xs text-text2">
          <TriangleAlert class="mt-0.5 size-4 shrink-0 text-red" />
          <p v-if="!pushInfo.permission">
            {{ loc.t('settings.pushNoPermission', 'Показ уведомлений запрещён в настройках телефона — разрешите их для GradeBookAI, иначе ничего не придёт.') }}
          </p>
          <p v-else>
            {{ loc.t('settings.pushNotConnected', 'Телефон пока не подключён к уведомлениям.') }}
            <template v-if="pushInfo.error"> {{ loc.t('settings.pushReason', { error: pushInfo.error }) }}</template>
            {{ loc.t('settings.pushRustoreHint', 'Доставку обеспечивает RuStore — на телефоне без него уведомления работать не будут.') }}
          </p>
        </div>
      </div>
    </Card>

    <!-- Версия веб-части. Только в приложении: на сайте и десктопе обновление приезжает
         обычной загрузкой страницы, и показывать номер бандла там не о чем. -->
    <Card id="set-version" :class="sec('about')" v-if="bundle" :title="loc.t('settings.appVersion', 'Версия приложения')"
          :subtitle="loc.t('settings.appVersionHint', 'Интерфейс обновляется сам, без переустановки из магазина')">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="text-sm text-text2">{{ loc.t('settings.installed', 'Установлена:') }} <span class="font-semibold text-text">{{ bundle.version }}</span></p>
        <div class="flex flex-wrap gap-2">
          <AppButton variant="ghost" :disabled="bundleBusy" @click="checkUpdate">
            <RefreshCw class="mr-2 inline size-4" />{{ bundleBusy ? loc.t('settings.checking', 'Проверяем…') : loc.t('settings.checkUpdate', 'Проверить обновление') }}
          </AppButton>
          <!-- Кнопка появляется, ТОЛЬКО когда есть что ставить: пустая кнопка
               «Установить», которая ничего не делает, — это то же самое «непонятно, как
               скачать», с которого начался этот разбор. -->
          <AppButton v-if="pending" variant="green" :disabled="bundleBusy" @click="installUpdate">
            {{ loc.t('settings.bundleInstall', 'Скачать и установить') }}
          </AppButton>
        </div>
      </div>
      <p v-if="bundleMsg" class="mt-3 text-sm text-text3">{{ bundleMsg }}</p>

      <!-- Новая НАТИВНАЯ сборка: её OTA не привезёт никогда, нужен файл из магазина или
           с сайта. Говорим об этом прямо, а не молчим — иначе человек ждёт обновления,
           которого по этому каналу не бывает. -->
      <div v-if="apk" class="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-accent/40 bg-accent-glow px-3 py-2.5">
        <p class="text-sm text-text2">{{ loc.t('settings.apkAvailable', { version: apk.version }) }}</p>
        <AppButton variant="green" size="sm" @click="openApk">
          {{ loc.t('settings.apkDownload', 'Скачать APK') }}
        </AppButton>
      </div>

      <!-- Ход последних обновлений. Нужен, пока не выяснена причина, по которой бандл
           скачивается и не приживается: она остаётся на устройстве и в логи сервера не
           попадает. Человеку с телефоном достаточно прочитать строку и назвать её. -->
      <details v-if="otaLog.length" class="mt-3">
        <summary class="cursor-pointer text-xs text-text3">{{ loc.t('settings.otaLog', 'Что происходило с обновлениями') }}</summary>
        <ul class="mt-2 flex flex-col gap-1 font-mono text-tiny text-text3">
          <li v-for="(e, i) in otaLog" :key="i" class="break-all">
            {{ e.at?.slice(5, 16).replace('T', ' ') }} · {{ e.kind }}
            <template v-if="e.data && Object.keys(e.data).length"> · {{ JSON.stringify(e.data) }}</template>
          </li>
        </ul>
      </details>
    </Card>

    <!-- Озвучка Вектора: Голос → Бубнеж → Выкл. -->
    <Card id="set-tts" :class="sec('voice')" :title="loc.t('settings.tts')" :subtitle="loc.t('settings.ttsHint', 'Как Вектор проговаривает свои ответы')">
      <button type="button"
              class="flex w-full items-center gap-3 rounded-md border border-border p-3 text-left transition-colors hover:border-accent"
              @click="cycleVoiceMode">
        <span class="grid size-10 shrink-0 place-items-center rounded-md"
              :class="tts.mode === 'off' ? 'bg-card2 text-text3' : 'bg-accent-glow text-accent'">
          <VolumeX v-if="tts.mode === 'off'" class="size-5" />
          <AudioLines v-else-if="tts.mode === 'mumble'" class="size-5" />
          <Volume2 v-else class="size-5" />
        </span>
        <span class="min-w-0 flex-1">
          <span class="block text-sm font-semibold text-text">{{ tts.modeLabel }}</span>
          <span class="block text-xs text-text3">{{ loc.t('settings.ttsCycleHint', 'Нажмите, чтобы переключить: Голос → Бубнеж → Выкл') }}</span>
        </span>
      </button>

      <!-- Выбор голоса — только в режиме «Голос». -->
      <div v-if="tts.mode === 'voice'" class="mt-4">
        <p class="mb-2 text-sm font-medium text-text2">{{ loc.t('settings.voiceLabel', 'Голос') }}</p>
        <div class="flex gap-2">
          <button type="button" @click="previewVoice('male')"
                  class="flex-1 rounded-md border p-3 text-left transition-colors"
                  :class="tts.voice === 'male' ? 'border-accent bg-accent-glow' : 'border-border hover:border-accent'">
            <span class="block text-sm font-semibold text-text">{{ loc.t('settings.voiceMale', 'Мужской') }}</span>
            <span class="block text-xs text-text3">{{ loc.t('settings.voiceMaleHint', 'По умолчанию · нажмите, чтобы услышать') }}</span>
          </button>
          <button type="button" @click="previewVoice('female')"
                  class="flex-1 rounded-md border p-3 text-left transition-colors"
                  :class="tts.voice === 'female' ? 'border-accent bg-accent-glow' : 'border-border hover:border-accent'">
            <span class="block text-sm font-semibold text-text">{{ loc.t('settings.voiceFemale', 'Женский') }}</span>
            <span class="block text-xs text-text3">{{ loc.t('settings.voiceFemaleHint', 'Нажмите, чтобы услышать') }}</span>
          </button>
        </div>
      </div>

      <!-- Бубнеж — короткое пояснение + проба. -->
      <div v-else-if="tts.mode === 'mumble'" class="mt-4 flex items-center justify-between gap-3 rounded-md border border-border bg-card2 px-3 py-2.5">
        <p class="text-xs text-text3">{{ loc.t('settings.mumbleHint', 'Имитация речи короткими сигналами (как голоса в играх), без интернета.') }}</p>
        <AppButton variant="ghost" @click="previewMumble">{{ loc.t('settings.tryIt', 'Проверить') }}</AppButton>
      </div>
    </Card>

    <!-- Голосовой ввод: тумблер + выбор микрофона (настройка ЭТОГО устройства). -->
    <Card id="set-mic" :class="sec('voice')" :title="loc.t('settings.voice')" :subtitle="loc.t('settings.voiceHint', 'Микрофон для «Вектора»: сказать вместо набора текста')">
      <div v-if="!voice.supported"
           class="flex items-start gap-3 rounded-lg border border-border bg-card2 px-3 py-2.5 text-sm text-text3">
        <MicOff class="mt-0.5 size-4 shrink-0" />
        <p>{{ loc.t('settings.voiceUnsupported', 'Это устройство не умеет записывать звук — голосовой ввод недоступен.') }}</p>
      </div>

      <template v-else>
        <button type="button" @click="voice.toggle()"
                class="flex w-full items-center gap-3 rounded-lg border border-border bg-card2 px-3 py-2.5 text-left transition-colors hover:border-accent">
          <span class="grid size-10 shrink-0 place-items-center rounded-md"
                :class="voice.enabled ? 'bg-accent-glow text-accent' : 'bg-card2 text-text3'">
            <Mic v-if="voice.enabled" class="size-5" />
            <MicOff v-else class="size-5" />
          </span>
          <span class="flex-1">
            <span class="block text-sm font-semibold text-text">
              {{ voice.enabled ? loc.t('settings.on', 'Включён') : loc.t('settings.off', 'Выключен') }}
            </span>
            <span class="block text-xs text-text3">
              {{ voice.enabled
                 ? loc.t('settings.voiceButtonShown', 'Кнопка 🎤 доступна рядом с полем вопроса')
                 : loc.t('settings.voiceButtonHidden', 'Кнопка микрофона скрыта') }}
            </span>
          </span>
          <span class="relative h-6 w-11 shrink-0 rounded-full transition-colors"
                :class="voice.enabled ? 'bg-accent' : 'bg-border2'">
            <span class="absolute top-0.5 size-5 rounded-full bg-white transition-all"
                  :class="voice.enabled ? 'left-[22px]' : 'left-0.5'" />
          </span>
        </button>

        <!-- Выбор устройства. Названия микрофонов браузер раскрывает только после
             разрешения, поэтому запрашиваем его кнопкой — то есть из жеста человека. -->
        <div v-if="voice.enabled" class="mt-4">
          <div class="mb-2 flex items-center justify-between gap-2">
            <span class="text-sm font-medium text-text2">{{ loc.t('settings.microphone', 'Микрофон') }}</span>
            <button type="button" :disabled="voice.loading" @click="voice.refresh(true)"
                    class="text-xs text-accent hover:underline disabled:opacity-50">
              {{ voice.loading ? loc.t('settings.searching', 'Ищем…') : loc.t('settings.refreshList', 'Обновить список') }}
            </button>
          </div>

          <select :value="voice.deviceId" @change="voice.setDevice($event.target.value)"
                  class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent">
            <option value="">{{ loc.t('settings.systemDefault', 'Как в системе') }}</option>
            <option v-for="d in voice.devices" :key="d.deviceId" :value="d.deviceId">{{ d.label }}</option>
          </select>

          <p v-if="voice.denied" class="mt-2 text-xs text-red">
            {{ loc.t('settings.micDenied', 'Доступ к микрофону запрещён. Разрешите запись в настройках браузера или системы.') }}
          </p>
          <p v-else-if="!voice.devices.length" class="mt-2 text-xs text-text3">
            {{ loc.t('settings.micRefreshHint', 'Нажмите «Обновить список», чтобы выбрать конкретный микрофон — до разрешения браузер не сообщает их названия.') }}
          </p>
          <p v-else class="mt-2 text-xs text-text3">
            {{ loc.t('settings.micPrivacyHint', 'Речь распознаёт сервер, с которого открыт интерфейс. Внутри программы это локальный сервер на вашем компьютере — запись его не покидает.') }}
          </p>
        </div>
      </template>
    </Card>

    <!-- Вибрация (тактильная отдача). Настройка УСТРОЙСТВА, как микрофон и озвучка:
         вибромотор — свойство телефона, а не человека.

         🔥 КАРТОЧКИ ЗДЕСЬ НЕТ ВОВСЕ ТАМ, ГДЕ ВИБРИРОВАТЬ НЕЧЕМ (правка 02.09.2026 по
         жалобе Ярослава). Раньше на компьютере она показывалась с подписью «это
         устройство не умеет вибрировать» — то есть занимала место и пункт в рельсе
         ради сообщения «здесь ничего нет». Хуже того, `haptics.supported()` до той же
         правки возвращал ПРАВДУ на настольном Chrome (`navigator.vibrate` там объявлен
         и молча ничего не делает), поэтому в десктопной программе и в браузере на ПК
         рисовался РАБОЧИЙ тумблер несуществующей функции.

         ⚠️ Условие одно и то же и для карточки, и для пункта рельса
         (`catsForRole(role, { haptics })`): разойдутся — человек нажмёт «Вибрация» в
         списке слева и увидит пустую категорию. -->
    <Card v-if="hapticsSupported"
          id="set-haptics" :class="sec('voice')" :title="loc.t('settings.haptics', 'Вибрация')"
          :subtitle="loc.t('settings.hapticsHint', 'Короткий отклик на нажатие, подтверждение и ошибку')">
      <button type="button" @click="toggleHaptics"
              class="flex w-full items-center gap-3 rounded-lg border border-border bg-card2 px-3 py-2.5 text-left transition-colors hover:border-accent">
        <span class="grid size-10 shrink-0 place-items-center rounded-md"
              :class="hapticsOn ? 'bg-accent-glow text-accent' : 'bg-card2 text-text3'">
          <Vibrate v-if="hapticsOn" class="size-5" />
          <VibrateOff v-else class="size-5" />
        </span>
        <span class="flex-1">
          <span class="block text-sm font-semibold text-text">
            {{ hapticsOn ? loc.t('settings.on', 'Включена') : loc.t('settings.off', 'Выключена') }}
          </span>
          <span class="block text-xs text-text3">
            {{ hapticsOn
               ? loc.t('settings.hapticsOnDesc', 'Телефон коротко откликается на действия')
               : loc.t('settings.hapticsOffDesc', 'Отдача выключена — телефон молчит') }}
          </span>
        </span>
        <span class="relative h-6 w-11 shrink-0 rounded-full transition-colors"
              :class="hapticsOn ? 'bg-accent' : 'bg-border2'">
          <span class="absolute top-0.5 size-5 rounded-full bg-white transition-all"
                :class="hapticsOn ? 'left-[22px]' : 'left-0.5'" />
        </span>
      </button>

      <!-- ⚠️ Системная настройка сильнее нашей, и об этом надо сказать прямо: иначе
           человек включает тумблер, ничего не чувствует и считает функцию сломанной. -->
      <p v-if="hapticsOn && reducedMotion" class="mt-3 text-xs text-text3">
        {{ loc.t('settings.hapticsReduced', 'В системе включено «уменьшить движение» — отдача не срабатывает, пока это так.') }}
      </p>
    </Card>

    <!-- Шкала оценивания — только преподаватель. -->
    <Card id="set-scale" :class="sec('teaching')" v-if="auth.role === 'teacher'" :title="loc.t('settings.gradingScale', 'Шкала оценивания')"
          :subtitle="loc.t('settings.gradingScaleHint', 'В чём вы вводите и видите оценки за практики/ДЗ. Средний балл и итоговая — всегда в 5-балльной')">
      <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <button v-for="s in scaleOptions" :key="s.id" type="button" :disabled="scaleSaving"
                @click="pickScale(s.id)"
                class="flex items-center gap-2.5 rounded-md border p-3 text-left transition-colors disabled:opacity-50"
                :class="gradingScale === s.id ? 'border-accent bg-accent-glow' : 'border-border hover:border-accent'">
          <GraduationCap class="size-4 shrink-0 text-accent" />
          <span class="flex-1 text-sm font-medium text-text">{{ s.label }}</span>
          <Check v-if="gradingScale === s.id" class="size-4 shrink-0 text-accent" />
        </button>
      </div>
    </Card>

    <!-- 🎓 ЗАМОРОЗКА ОЦЕНОК (06.09.2026, требование Влада: «время доходит до лета —
         оценки останавливаются и видны только итоговые, выставим кастомную дату, когда
         по сути зачёты и экзамены уже сданы»).
         ⚠️ Показываем и ТЕКУЩУЮ ФАЗУ: настройка, по которой нельзя проверить, сработала
         ли она, проверяется единственным способом — дождаться лета. Это не проверка. -->
    <Card v-if="isAdminRole" id="set-gradesFreeze" :class="sec('academicYear')"
          :title="loc.t('settings.gradesFreeze', 'Заморозка оценок')"
          :subtitle="loc.t('settings.gradesFreezeHint', 'После этой даты студент видит только итоговые оценки. Пусто — правило выключено.')">
      <div class="flex flex-wrap items-center gap-2">
        <input v-model="freezeDate" placeholder="30.06" maxlength="5"
               class="h-10 w-28 rounded-lg border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" />
        <AppButton :disabled="freezeSaving" @click="saveFreeze">{{ loc.t('common.save', 'Сохранить') }}</AppButton>
        <span v-if="freezeError" class="text-sm text-red">{{ freezeError }}</span>
        <span v-else-if="freezePhase" class="text-sm text-text3">
          {{ freezePhase === 'frozen'
             ? loc.t('settings.freezeNow', 'Сейчас: показаны только итоговые')
             : loc.t('settings.freezeLive', 'Сейчас: оценки видны как обычно') }}
        </span>
      </div>
    </Card>

    <!-- Второй фактор входа. ⚠️ БЕЗ v-if по устройству: код из приложения работает где
         угодно. НО НЕ У АДМИНИСТРАТОРА (06.09.2026) — здесь стояло обратное, «ему он
         ОБЯЗАТЕЛЕН», и это перестало быть правдой: обязательность снята, а `mfa.is_active`
         для роли `admin` возвращает False, то есть заведённый фактор не действует. Карточка
         осталась бы тумблером, который включается и заведомо ничего не делает.
         Довод Влада: «кто угодно может добавить свой аутентификатор и входить в админку» —
         при нескольких администраторах фактор не защищает, а лишь создаёт видимость. -->
    <Card v-if="!isAdminRole" id="set-mfa" :class="sec('security')" :title="loc.t('settings.mfa', 'Второй фактор входа')"
          :subtitle="loc.t('settings.mfaHint', 'Одноразовый код из приложения-аутентификатора в дополнение к паролю')">
      <MfaCard />
    </Card>

    <!-- Вход по биометрии / 2FA — виден только на устройствах с Face ID/отпечатком. -->
    <Card id="set-biometric" :class="sec('security')" v-if="canBiometric" :title="loc.t('settings.biometric', 'Вход по биометрии')"
          :subtitle="loc.t('settings.biometricHint', 'Быстрый вход по Face ID, отпечатку или ключу доступа — без пароля')">
      <div class="flex items-start gap-3 rounded-lg border border-border bg-card2 px-3 py-2.5 text-sm text-text3">
        <ShieldCheck class="mt-0.5 size-4 shrink-0 text-accent" />
        <p>{{ loc.t('settings.biometricExplain', 'Приватный ключ хранится в защищённом чипе устройства и никогда его не покидает. Сервер знает только публичную часть. Пароль при таком входе не используется.') }}</p>
      </div>

      <ul v-if="passkeys.length" class="mt-4 space-y-2">
        <li v-for="k in passkeys" :key="k.id"
            class="flex items-center justify-between rounded-md border border-border px-3 py-2">
          <div class="flex items-center gap-2.5 text-sm">
            <Fingerprint class="size-4 text-accent" />
            <span class="font-medium text-text">{{ k.device_name || loc.t('settings.device', 'Устройство') }}</span>
            <span class="text-tiny text-text3">{{ loc.t('settings.addedOn', { date: fmtDate(k.created_at) }) }}</span>
          </div>
          <button type="button" :disabled="pkBusy" class="text-text3 transition-colors hover:text-red disabled:opacity-50"
                  :aria-label="loc.t('settings.removeKey', 'Удалить ключ')" @click="removePasskey(k.id)">
            <Trash2 class="size-4" />
          </button>
        </li>
      </ul>
      <p v-else class="mt-4 text-sm text-text3">{{ loc.t('settings.noKeys', 'Пока нет ни одного ключа на этом аккаунте.') }}</p>

      <div class="mt-4 flex flex-wrap items-center gap-3">
        <AppButton variant="green" :disabled="pkBusy" @click="addPasskey">
          <Fingerprint class="mr-2 inline size-4" />{{ pkBusy ? loc.t('settings.settingUp', 'Настраиваем…') : loc.t('settings.addThisDevice', 'Добавить это устройство') }}
        </AppButton>
        <p v-if="pkMsg" class="text-sm font-medium text-accent">{{ pkMsg }}</p>
      </div>
    </Card>

    <!-- ВЫХОД — в самом низу страницы, последним блоком. Раньше жил в шапке рядом с
         темой и статусом; здесь до него надо осознанно долистать. -->
    <Card id="set-logout" :class="sec('account')" :title="loc.t('settings.account', 'Аккаунт')"
          :subtitle="loc.t('settings.accountHint', 'Вход в систему на этом устройстве')">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="text-sm text-text3">
          {{ loc.t('settings.logoutExplain', 'После выхода понадобится снова ввести логин и пароль — сохранённая сессия будет удалена с этого устройства.') }}
        </p>
        <AppButton variant="red" @click="onLogout">
          <LogOut class="mr-2 inline size-4" />{{ loc.t('nav.logout', 'Выйти') }}
        </AppButton>
      </div>
    </Card>

    <!-- 🔒 Юридические документы. Дублируют ссылки с экрана входа НАМЕРЕННО, и это не
         тот случай, когда «две одинаковые кнопки читаются как недоделка»: на входе они
         нужны ДО акцепта, здесь — чтобы перечитать их потом, не выходя из аккаунта.
         Право субъекта ПДн получать сведения об обработке (ст. 14 152-ФЗ) не
         прекращается после входа, а искать документ на экране, куда попадают раз в
         пять часов, человек не станет.
         Обычные ссылки, не роутер: страницы статические, лежат вне SPA и одинаково
         открываются на сайте, внутри программы и в приложении Android. -->
    <Card id="set-legal" :class="sec('about')" :title="loc.t('settings.legal', 'Документы')"
          :subtitle="loc.t('settings.legalHint', 'Условия использования и порядок обработки персональных данных')">
      <div class="flex flex-col gap-2 sm:flex-row">
        <a href="/terms.html" target="_blank" rel="noopener"
           class="flex-1 rounded-sm border border-border bg-card2 px-4 py-2.5 text-center text-sm font-semibold text-text transition-colors hover:border-accent hover:text-accent">
          {{ loc.t('login.legalTerms', 'Пользовательское соглашение') }}
        </a>
        <a href="/privacy.html" target="_blank" rel="noopener"
           class="flex-1 rounded-sm border border-border bg-card2 px-4 py-2.5 text-center text-sm font-semibold text-text transition-colors hover:border-accent hover:text-accent">
          {{ loc.t('login.legalPrivacy', 'Политику обработки персональных данных') }}
        </a>
        <!-- ⚠️ Правила — RouterLink, а не `<a href>`: это НАША страница внутри приложения
             (`/rules`), а не статический файл из `web/public`, как соседние два. Ссылка
             через `href` перезагрузила бы всю SPA ради перехода на соседний экран. -->
        <RouterLink to="/rules" target="_blank"
           class="flex-1 rounded-sm border border-border bg-card2 px-4 py-2.5 text-center text-sm font-semibold text-text transition-colors hover:border-accent hover:text-accent">
          {{ loc.t('rules.title', 'Правила сообщества') }}
        </RouterLink>
      </div>
    </Card>
      </div><!-- правый столбец -->
    </div><!-- панель -->
  </div><!-- подложка -->
  <!-- ⚠️ Прощальные сцены — ВНЕ оверлея: они показываются в момент выхода, когда панель
       настроек уже не нужна, и внутри неё оказались бы обрезаны её же рамкой. -->
  <FarewellOverlay v-if="farewell" :name="farewellName" />
  <DarkSoulsFarewell v-if="darkSouls" @close="darkSouls = false" />
</template>
