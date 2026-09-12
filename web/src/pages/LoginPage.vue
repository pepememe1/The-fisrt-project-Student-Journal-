<script setup>
// LoginPage — экран входа (порт ui/auth_pages.py): светлый фон-«соты», три колонки —
// «Вектор» слева (наведение → поза «думает» + облачко-совет с ротацией), карточка
// входа по центру, карточка «фичи» справа. Адрес сервера НЕ спрашиваем (same-origin).
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { Eye, EyeOff, Bot, Globe, ShieldCheck, Trophy, Monitor, Smartphone, Download, Fingerprint, ShieldAlert, CalendarDays } from '@lucide/vue'
import { useAuthStore } from '@/stores/auth'
import { RouterLink } from 'vue-router'
import { useLocaleStore } from '@/stores/locale'
import { desktopApi, appApi } from '@/api/endpoints'
import { platformAuthenticatorAvailable } from '@/api/webauthn'
import { HOME_BY_ROLE } from '@/config/nav'
import { isDesktopApp, isAndroidBrowser } from '@/utils/platform'
import AppButton from '@/components/ui/AppButton.vue'
import DeviceApproval from '@/components/DeviceApproval.vue'
import HexBackground from '@/components/HexBackground.vue'
import EasterEggHost from '@/components/easter/EasterEggHost.vue'
import { useEasterStore } from '@/stores/easterEggs'
import LanguagePicker from '@/components/ui/LanguagePicker.vue'
import BrandLogo from '@/components/BrandLogo.vue'
import Mascot from '@/components/Mascot.vue'
import RegisterDialog from '@/components/RegisterDialog.vue'
import RecoverDialog from '@/components/RecoverDialog.vue'

const router = useRouter()
const auth = useAuthStore()
import MfaPrompt from '@/components/auth/MfaPrompt.vue'
const easter = useEasterStore()
const loc = useLocaleStore()

const login = ref('')
const password = ref('')
const showPass = ref(false)
const needApproval = ref(false)

// Секция «скачать десктоп» — только для ПК (мышь), не для телефонов/планшетов (touch)
// и НЕ ВНУТРИ самой десктоп-версии: предлагать скачать программу тому, кто уже в ней,
// это ошибка, а не реклама.
const isDesktop = ref(false)
const insideApp = isDesktopApp()
const desktop = ref({ available: false })
// Мобильное приложение предлагаем скачать только в браузере на Android (см.
// isAndroidBrowser) и только если сервер реально отдаёт файл: блок «скачать», за
// которым нет файла, хуже отсутствующего блока.
const isAndroid = isAndroidBrowser()
const apk = ref({ url: '', version: '' })
// Кнопку «Войти по биометрии» показываем только если на устройстве есть встроенный
// биометрический аутентификатор (Face ID/отпечаток) и браузер поддерживает passkeys.
const canBiometric = ref(false)
onMounted(async () => {
  // greeting держим ~1.6 с, потом плавный переход в покой (без частого мелькания).
  setTimeout(() => { greetingDone.value = true }, 1600)
  isDesktop.value = window.matchMedia?.('(hover: hover) and (pointer: fine)').matches ?? true
  try { desktop.value = (await desktopApi.info()).data } catch { desktop.value = { available: false } }
  //Спрашиваем сервер об .apk только там, где кнопка вообще может появиться —
  //лишний запрос с каждого захода на сайт с ПК не нужен никому.
  if (isAndroid) {
    try {
      const { data } = await appApi.apkInfo()
      if (data?.url) apk.value = { url: data.url, version: data.versionName || '' }
    } catch { apk.value = { url: '', version: '' } }
  }
  try { canBiometric.value = await platformAuthenticatorAvailable() } catch { canBiometric.value = false }
})

// Наведение на Вектора: поза «думает» + облачко-совет. Крутятся полезные подсказки и
// интересные факты о ВСГУТУ, обо мне и о разработке GradeBookAI (без «внутренностей»).
const TIPS = [
  // — Важное про вход —
  'За логином и паролем обратитесь к администратору колледжа.',
  'Пароль вводите внимательно: после нескольких неудачных попыток вход ненадолго блокируется.',
  'Студенты и преподаватели входят по логину и паролю, которые выдал администратор.',
  // — Полезное про приложение —
  'Десктоп-версия работает и без интернета: данные сохранятся локально и синхронизируются, когда сеть вернётся.',
  'Оценки, средний балл, долги и пропуски всегда под рукой — и в приложении, и на сайте.',
  'Я беру цифры из реальных данных журнала, а не выдумываю их — на меня можно положиться.',
  'Тёмную тему можно включить по расписанию: вечером сама затемняется, утром светлеет.',
  'Расписание тянется прямо с портала ВСГУТУ и обновляется автоматически.',
  'Забыл пароль? Его выдаёт и меняет администратор колледжа — обратись к нему.',
  'Один аккаунт — на всех устройствах: тема и данные «переезжают» за тобой.',
  // — Про ВСГУТУ —
  'Факт: ВСГУТУ — Восточно-Сибирский государственный университет технологий и управления в Улан-Удэ.',
  'Технологический колледж ВСГУТУ готовит специалистов среднего звена — для них и сделан этот журнал.',
  'Учебный год в колледже идёт по неделям I и II — расписание это учитывает.',
  // — Про меня, Вектора —
  'Меня зовут Вектор, я тигр — символ силы и упорства. Помогаю тебе держать успеваемость в тонусе.',
  'Арт для меня нарисовала участница команды Synapse.',
  // — Про разработку —
  'GradeBookAI создала студенческая команда Synapse.',
  'Проект — победитель хакатона «Мы — будущее IT Бурятии».',
  'Приложение спроектировано под 152-ФЗ: персональные данные шифруются, канал защищён HTTPS.',
]
const hovered = ref(false)
const tipIndex = ref(0)
const tip = computed(() => TIPS[tipIndex.value])
function onEnter() { hovered.value = true; tipIndex.value = (tipIndex.value + 1) % TIPS.length }

// Анимированный Вектор на входе: появляется с «приветствием» (машет), через ~1.6 с
// успокаивается в покой (idle) — задержка убирает мелькание greeting↔idle. Наведение
// мыши → «думает». Все три состояния — тот же анимированный WebP, что и в чате.
const greetingDone = ref(false)

// ── Вектор закрывает глаза, пока набран пароль ──────────────────────────────────────
// Жест смысловой, а не декоративный: маскот показывает, что НЕ подсматривает. Поэтому
// он привязан к наличию символов в поле, а не к наведению мыши — важно, что пароль
// введён, а не куда смотрит курсор.
//
// ⚠️ Обратный ход — ОТДЕЛЬНЫЙ файл `eyes_open` (собран реверсом того же ролика, см.
// tools/build_mascot_anim.py): ни браузер, ни Qt не умеют отматывать анимированный WebP
// назад. Поэтому «убирает лапы» — это проигрывание второго файла, после которого сами
// возвращаемся в покой (в самом WebP loop=1, он застывает на последнем кадре).
const eyesState = ref('')            // '' | 'eyes_close' | 'eyes_open'
// 10 кадров при FPS=12 в сборщике → ~830 мс. Держим с небольшим запасом, чтобы покой
// не начался раньше, чем лапы действительно опустились.
const EYES_OPEN_MS = 900
let eyesTimer = null

watch(() => password.value.length > 0, (typed) => {
  clearTimeout(eyesTimer)
  if (typed) { eyesState.value = 'eyes_close'; return }
  // Поле опустело. Если глаза и не закрывались (страница только открылась), открывать
  // нечего — иначе на пустой форме проигрывалось бы «убирает лапы» из ниоткуда.
  if (!eyesState.value) return
  eyesState.value = 'eyes_open'
  eyesTimer = setTimeout(() => { eyesState.value = '' }, EYES_OPEN_MS)
})
onBeforeUnmount(() => clearTimeout(eyesTimer))

// Закрытые глаза ВАЖНЕЕ «думает» при наведении: пока пароль в поле, маскот не
// подсматривает — это обещание, а подсказка при наведении может и подождать.
const loginAnim = computed(() => {
  if (eyesState.value) return eyesState.value
  return hovered.value ? 'thinking' : (greetingDone.value ? 'idle' : 'greeting')
})

const canSubmit = computed(() => login.value.trim() && password.value && !auth.loading)

// Предложить браузеру/менеджеру паролей сохранить вход. Для SPA/AJAX-входа одних
// autocomplete-атрибутов мало — надёжно срабатывает Credential Management API
// (navigator.credentials.store). Побочный бонус: когда пароль сохранён в системном
// менеджере, iOS предлагает автозаполнение по Face ID, Android — по отпечатку. Где API
// нет (Safari/Firefox) — молча полагаемся на autocomplete-эвристику браузера.
async function saveCredential(id, pass) {
  try {
    if (window.PasswordCredential) {
      const cred = new window.PasswordCredential({ id, password: pass, name: id })
      await navigator.credentials.store(cred)
    }
  } catch { /* сохранение необязательно — не мешаем входу */ }
}

// ━━ ОБРАТНЫЙ ОТСЧЁТ БЛОКИРОВКИ ━━
// Сервер присылает Retry-After один раз; дальше время идёт у человека на глазах.
// Без отсчёта «подождите» ничего не сообщает: непонятно, минуту ждать или полчаса, и
// люди продолжают жать кнопку, продлевая себе же ожидание.
const lockLeft = ref(0)
let lockTimer = 0
watch(() => auth.lockedFor, (secs) => {
  clearInterval(lockTimer)
  lockLeft.value = secs || 0
  if (!secs) return
  lockTimer = setInterval(() => {
    lockLeft.value -= 1
    if (lockLeft.value <= 0) clearInterval(lockTimer)
  }, 1000)
})
onBeforeUnmount(() => clearInterval(lockTimer))
const lockLabel = computed(() => {
  const s = Math.max(0, lockLeft.value)
  const m = Math.floor(s / 60)
  return m > 0 ? `${m}:${String(s % 60).padStart(2, '0')}` : `${s} с`
})

async function submit() {
  needApproval.value = false
  try {
    const user = await auth.login(login.value, password.value)
    //Пароль верен, но нужен второй фактор: токенов ещё нет, и переходить некуда.
    //⚠️ Пароль в связку НЕ сохраняем до конца входа — иначе браузер запомнил бы
    //его как рабочий, а вход мог и не состояться.
    if (user?.mfaRequired) return
    await saveCredential(login.value, password.value)
    router.push(HOME_BY_ROLE[user.role] || '/')
  } catch (e) {
    if (e.response?.status === 403) needApproval.value = true
    // Far Cry: решение принял СЕРВЕР (седьмая неудача подряд + шанс) и сообщил
    // заголовком. Клиент только рисует — своего броска здесь нет и быть не может.
    if (e.response?.headers?.['x-gb-egg'] === 'farcry_vaas_quote') easter.show('farcry_vaas_quote')
  }
}

// Вход по биометрии (passkey). Отмену пользователем игнорируем молча.
async function submitPasskey() {
  needApproval.value = false
  try {
    const user = await auth.loginPasskey()
    router.push(HOME_BY_ROLE[user.role] || '/')
  } catch {
    /* ошибка уже в auth.error (или отмена — молчим) */
  }
}
function onApproved() { needApproval.value = false; submit() }

//Второй шаг входа завершён — дальше как при обычном входе.
//⚠️ Незавершённый вход гасим ЗДЕСЬ, а не в сторе: пока `auth.mfaChallenge` не пуст,
//на экране висит окно кода, и обнулять его должен тот, кто уже получил управление.
//Если сделать это раньше (в `verifyMfa`), Vue снимет окно с экрана прежде, чем оно
//успеет сообщить об успехе, — и переход не запросит НИКТО. Ровно этот дефект и был
//пойман 03.09.2026; см. подробный разбор в `stores/auth.js::verifyMfa`.
async function onMfaDone(user) {
  auth.cancelMfa()
  await saveCredential(login.value, password.value)
  router.push(HOME_BY_ROLE[user.role] || '/')
}

// Регистрация студента / восстановление пароля — модалки под кнопкой «Войти».
const showRegister = ref(false)
const showRecover = ref(false)

// ━━━ САМООБСЛУЖИВАНИЕ ВЫКЛЮЧЕНО (12.09.2026, требование Влада) ━━━━━━━━━━━━━━━━━━━━━
//
// 🔑 ПОЧЕМУ. Дословно: «на сайте ВСГУТУ нет кнопки регистрации и подобного, только вход
// по выданному логину и паролю». Наш экран входа предлагал завести аккаунт самому и
// восстановить пароль по почте — то есть обещал порядок, которого у заказчика нет.
// Обещание, которого продукт не выполняет, хуже отсутствующей кнопки: студент нажимает,
// подаёт заявку и ЖДЁТ, а ждать нечего — учётные данные выдаёт колледж.
//
// ⚠️ КОД НЕ УДАЛЁН НАМЕРЕННО (прямое условие Влада: «не удаляй»). Оба диалога и обе
// серверные ручки живы и работоспособны: политика приёма студентов — решение заказчика,
// и оно может смениться обратно. Снимается запрет ОДНОЙ строкой здесь.
//
// 🔒 ЗАМКОВ ТРИ, И ОДНОГО БЫЛО БЫ МАЛО. Спрятать кнопку — не защита: ref остаётся
// доступным, и любая будущая строка `showRegister = true` (горячая клавиша, переход по
// адресу, чужая правка) снова покажет окно, причём молча. Поэтому:
//   1) кнопок нет в разметке вовсе (`v-if`) — их не видно, не нажать и не поймать Tab'ом;
//   2) сами окна тоже под `v-if` с этим флагом — подняли ref обходным путём, а показывать
//      нечего;
//   3) открывают их только эти две функции, и они отказывают первой же строкой.
// Это тот же приём, которым в проекте проведена граница у раздела «Сервер»: надёжен не
// спрятанный элемент, а ОТСУТСТВУЮЩИЙ путь.
const SELF_SERVICE_ENABLED = false

function openRegister() {
  if (!SELF_SERVICE_ENABLED) return
  showRegister.value = true
}
function openRecover() {
  if (!SELF_SERVICE_ENABLED) return
  showRecover.value = true
}
</script>

<template>
  <!-- ⚠️ ПРОКРУТКА НА ТЕЛЕФОНЕ (3.7, живой отзыв: «не вся страница влазит на экран»).
       Здесь стоял `overflow-hidden` вместе с вертикальным центрированием — на широком
       экране это правильно (фон-соты не должен давать полосу прокрутки), но на телефоне
       содержимое ВЫШЕ экрана, и `overflow-hidden` не просто прячет лишнее, а лишает
       возможности до него добраться: прокрутить нельзя, низа карточки не существует.
       Теперь режем только по горизонтали (соты), а по вертикали содержимое прокручивается;
       `items-start` на узком экране — чтобы форма начиналась сверху, а не «висела» по
       центру, уводя часть себя за верхний край. -->
  <!-- ⚠️ ЦЕНТРИРУЕМ ВЕЗДЕ, но БЕЗ overflow-hidden. Три состояния этой строки стоит
       помнить, чтобы не ходить по кругу третий раз: сперва было центрирование вместе с
       `overflow-hidden` — на невысоком экране низ формы становился физически
       недостижим; потом `items-start` на телефоне — форма влезла, но прилипла к верху
       и под ней осталось полэкрана пустоты (живой скриншот). Правильно — обычное
       центрирование в ПРОКРУЧИВАЕМОМ контейнере: помещается — стоит по центру, не
       помещается — прокручивается. -->
  <div class="relative flex min-h-full items-center justify-center overflow-x-hidden overflow-y-auto p-4"
       style="padding-top: calc(1rem + env(safe-area-inset-top)); padding-bottom: calc(1rem + env(safe-area-inset-bottom))">
    <HexBackground />

    <!-- Угловой логотип с названием убран по требованию: знак теперь только в карточке входа. -->

    <!-- Сетка 1fr · auto · 1fr: карточка входа СТРОГО по центру экрана (боковые колонки
         равны). items-center — блок по центру по вертикали (не прижат к низу). -->
    <div class="relative z-10 grid w-full max-w-6xl grid-cols-1 items-center gap-x-6 gap-y-4 lg:grid-cols-[1fr_auto_1fr]">
      <!-- «Вектор» слева, прижат к правому краю колонки (ближе к карточке). Облачко —
           АБСОЛЮТНО НАД маскотом (bottom-full), поэтому не залазит на него. -->
      <div class="relative hidden justify-self-end lg:block"
           @mouseenter="onEnter" @mouseleave="hovered = false">
        <transition name="pop">
          <div v-if="hovered" class="absolute bottom-full left-1/2 mb-3 w-64 -translate-x-1/2 rounded-2xl border border-border2 bg-card px-4 py-3 shadow-card">
            <p class="text-sm font-extrabold text-accent">💡 Совет Вектора</p>
            <p class="mt-1 text-sm font-semibold text-text">{{ tip }}</p>
            <div class="absolute -bottom-2 left-1/2 size-4 -translate-x-1/2 rotate-45 border-b border-r border-border2 bg-card" />
          </div>
        </transition>
        <Mascot :anim="loginAnim" scope="login" class="h-[30rem] w-80 cursor-pointer" />
      </div>

      <!-- Карточка входа (центр экрана) -->
      <div class="mx-auto w-full max-w-sm justify-self-center rounded-2xl border border-border bg-card p-4 shadow-card sm:p-7">
        <!-- Глобус выбора языка. Стоит НА ЭКРАНЕ ВХОДА, до аккаунта: человек, который не
             читает по-русски, должен переключить язык раньше, чем начнёт разбираться
             в форме. Выбор переживает вход (см. stores/locale.js). -->
        <div class="mb-1 flex justify-end">
          <LanguagePicker />
        </div>
        <div class="mb-3 flex flex-col items-center text-center sm:mb-5">
          <!-- Знак над названием журнала: цвет из темы (кольца — акцентным цветом,
               видны на белой карточке; ядро двухцветное). -->
          <!-- ⚠️ Прятать классом ПРЯМО НА КОМПОНЕНТЕ нельзя: у BrandLogo корневой
               <svg> несёт инлайновый style="display:block", а инлайновый стиль
               сильнее класса `sm:hidden` — на экране оказывались ОБА знака сразу
               (поймано скриншотом с телефона). Скрываем обёртки, у них своих
               инлайновых стилей нет. -->
          <!-- Знак ужимали ради того, чтобы форма влезла на телефон. Влезла она за счёт
               других правок, а мелкий знак остался и стал выглядеть случайным (живой
               отзыв). Возвращаем крупный: место, как показал скриншот, есть. -->
          <div class="sm:hidden"><BrandLogo :size="84" oncard /></div>
          <div class="hidden sm:block"><BrandLogo :size="96" oncard /></div>
          <h1 class="mt-2 font-title text-xl font-extrabold text-text sm:mt-3 sm:text-2xl">GradeBookAI</h1>
          <p class="mt-1 hidden text-sm text-text3 sm:block">{{ loc.t('app.subtitle') }}</p>
          <p class="mt-1 text-xs font-semibold text-accent sm:text-sm">{{ loc.t('app.college') }}</p>
        </div>

        <!-- Второй фактор: пароль уже принят, показываем ТОЛЬКО поле кода.
             Форму входа при этом прячем целиком — оставлять её рядом значит
             предлагать человеку ввести пароль ещё раз там, где он не нужен. -->
        <MfaPrompt v-if="auth.mfaChallenge" @done="onMfaDone" @cancel="password = ''" />

        <form v-else class="space-y-3 sm:space-y-4" @submit.prevent="submit">
          <div>
            <label class="mb-1.5 block text-xs font-medium text-text3">{{ loc.t('login.login') }}</label>
            <input v-model="login" id="login" name="username" autocomplete="username"
                   class="h-11 w-full rounded-sm border border-border2 bg-card2 px-3.5 text-text outline-none transition-colors focus:border-accent focus:bg-card"
                   :placeholder="loc.t('login.loginPlaceholder')" />
          </div>
          <div>
            <label class="mb-1.5 block text-xs font-medium text-text3">{{ loc.t('login.password') }}</label>
            <div class="relative">
              <input v-model="password" id="password" name="password" :type="showPass ? 'text' : 'password'" autocomplete="current-password"
                     class="h-11 w-full rounded-sm border border-border2 bg-card2 px-3.5 pr-11 text-text outline-none transition-colors focus:border-accent focus:bg-card"
                     placeholder="••••••••" />
              <button type="button" class="absolute right-2 top-1/2 grid size-8 -translate-y-1/2 place-items-center rounded-sm text-text2 hover:text-accent"
                      :aria-label="showPass ? loc.t('login.hide') : loc.t('login.show')" @click="showPass = !showPass">
                <EyeOff v-if="showPass" class="size-4" /><Eye v-else class="size-4" />
              </button>
            </div>
          </div>

          <!-- ⚠️ БЛОКИРОВКА И ОПЕЧАТКА — РАЗНЫЕ СОСТОЯНИЯ, и выглядеть одинаково они не
               должны. Раньше обе показывались одним красным прямоугольником, и человек,
               набравший верный пароль после семи неудач, видел ровно то же, что и при
               ошибке: единственный возможный вывод — «журнал не принимает мой пароль».
               На самом деле восьмая попытка отвергается ДО сверки пароля: пара
               (IP, логин) заперта на пять минут (throttle.MAX_FAILS). Пароль тут ни при
               чём, и сказать это надо прямо. -->
          <div v-if="lockLeft > 0"
               class="flex items-start gap-2 rounded-sm border border-orange/40 bg-orange/10 px-3 py-2 text-sm">
            <ShieldAlert class="mt-0.5 size-4 shrink-0 text-orange" />
            <span class="text-text2">
              <b class="text-text">Вход временно заперт</b> — слишком много неудачных попыток.
              Это защита от подбора: пароль сейчас <b>не проверяется вовсе</b>, даже верный.
              Осталось <b class="tabular-nums text-text">{{ lockLabel }}</b>.
            </span>
          </div>
          <p v-else-if="auth.error" class="rounded-sm border border-red/40 bg-red/10 px-3 py-2 text-sm text-red">{{ auth.error }}</p>

          <AppButton type="submit" class="w-full" :disabled="!canSubmit">
            {{ auth.loading ? loc.t('login.submitting') : loc.t('login.submit') }}
          </AppButton>
        </form>

        <!-- Вход по passkey. На телефоне это Face ID/отпечаток, на ПК — «ключ доступа»
             (Windows Hello / PIN / аппаратный ключ), поэтому подпись зависит от устройства.
             Включается в настройках профиля после обычного входа. -->
        <div v-if="canBiometric && !auth.mfaChallenge" class="mt-3">
          <button type="button" :disabled="auth.loading"
                  class="flex w-full items-center justify-center gap-2 rounded-sm border border-accent/50 px-4 py-2.5 text-sm font-semibold text-accent transition-colors hover:bg-accent-glow disabled:opacity-50"
                  @click="submitPasskey">
            <Fingerprint class="size-4" />
            {{ isDesktop ? loc.t('login.passkey') : loc.t('login.biometry') }}
          </button>
          <p class="mt-1.5 hidden text-center text-tiny text-text3 sm:block">Функция настраивается в настройках профиля</p>
        </div>

        <!-- Вход общий для трёх ролей. Раньше первым и единственным явным ориентиром
             был «Для обучающихся», а пояснение преподавателю исчезало на телефоне;
             родитель вообще не видел, что эта форма ему подходит. Регистрация остаётся
             только студенческой, но принадлежность самой формы теперь названа до выбора
             действия — человеку не приходится угадывать свой маршрут. -->
        <div class="mt-3 border-t border-border pt-2.5 text-center sm:mt-4 sm:pt-3">
          <p class="text-xs font-semibold text-text2">{{ loc.t('login.audience') }}</p>
          <!-- ⚠️ «Для обучающихся:» гаснет ВМЕСТЕ с кнопками. Это подпись К НИМ, и без
               них она указывает в пустоту — читается как «здесь что-то не прогрузилось». -->
          <p v-if="SELF_SERVICE_ENABLED" class="text-xs text-text3">{{ loc.t('login.forStudents') }}</p>
          <div v-if="SELF_SERVICE_ENABLED" class="mt-1.5 flex flex-wrap items-center justify-center gap-2">
            <button type="button" class="rounded-sm border border-accent/40 px-3 py-1.5 text-xs font-semibold text-accent transition-colors hover:bg-accent-glow"
                    @click="openRegister">{{ loc.t('login.register') }}</button>
            <button type="button" class="rounded-sm border border-border2 px-3 py-1.5 text-xs font-medium text-text3 transition-colors hover:border-accent hover:text-accent"
                    @click="openRecover">{{ loc.t('login.recover') }}</button>
          </div>
          <!-- Подсказка меняется вместе с порядком: пока регистрации нет, «обратитесь к
               администратору» относится КО ВСЕМ, а не только к преподавателю и родителю —
               студенту тоже больше некуда нажать. -->
          <p class="mt-2 text-tiny leading-relaxed text-text3">
            {{ SELF_SERVICE_ENABLED
              ? loc.t('login.accountHelp')
              : loc.t('login.accountHelpIssued', 'Логин и пароль выдаёт колледж. Нет учётных данных — обратитесь к администратору.') }}
          </p>
        </div>

        <!-- ⚠️ Кнопка одностраничника для приёмной комиссии здесь БЫЛА и убрана
             (23.08.2026): такая же стоит в правой панели, и две одинаковые кнопки на
             одном экране читаются не как «важно», а как недоделка. Оставлена ПРАВАЯ —
             она в блоке «о продукте», где её и ищет тот, кто пришёл смотреть, а не
             входить. Ссылка одна и та же: /offer.html, обычная навигация, не роутер.
             Не возвращать сюда, не сверившись с правой панелью. -->

        <!-- Адрес сервера (как «⚙ Сервер синхронизации» в десктопе): сменить/задать вручную. -->
        <div class="mt-3 text-center">
          <button type="button" class="text-tiny text-text3 transition-colors hover:text-accent" @click="router.push('/connect')">
            ⚙ Сервер синхронизации
          </button>
        </div>

        <!-- 🔒 ЮРИДИЧЕСКИЕ ДОКУМЕНТЫ — ИМЕННО ЗДЕСЬ, НА ЭКРАНЕ ВХОДА, и это не украшение.
             Соглашение акцептуется конклюдентными действиями (п. 3 ст. 438 ГК РФ): вход
             в систему И ЕСТЬ акцепт. Значит документ обязан быть доступен ДО входа, а не
             в настройках кабинета, куда попадают уже согласившись. Политику обработки
             ПДн, кроме того, закон требует публиковать с НЕОГРАНИЧЕННЫМ доступом
             (п. 1 ч. 2 ст. 18.1 152-ФЗ).
             ⚠️ Обычные ссылки, а не роутер: страницы статические и лежат вне SPA. Так
             они одинаково открываются и на сайте, и внутри программы (локальный сервер
             отдаёт ту же сборку), и в приложении Android из бандла.
             ⚠️ Не прятать за «Подробнее» и не превращать в галочку «согласен»:
             галочка без прочитанного текста хуже её отсутствия. -->
        <!-- 🔥 РАСПИСАНИЕ ДОСТУПНО БЕЗ ВХОДА (01.09.2026, просьба Ярослава). Причина
             живая: «из акка вылетело, надо быстро чекнуть расписание, а его нет». Токен
             живёт ограниченно, и вместе с ним пропадала информация, которая ОБЩЕДОСТУПНА
             — расписание лежит на портале ВСГУТУ открыто. Ссылка стоит ЗДЕСЬ, потому что
             сюда и выбрасывает после истечения сессии: именно в этот момент она нужна.
             ⚠️ Роутером, а не обычной ссылкой: страница — часть SPA, и переход по href
             перезагрузил бы всё приложение ради одного экрана. -->
        <p class="mt-5 text-center">
          <RouterLink to="/schedule"
                      class="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold text-accent transition-colors hover:bg-accent-glow">
            <CalendarDays class="size-4" />
            {{ loc.t('login.publicSchedule', 'Расписание без входа') }}
          </RouterLink>
        </p>

        <p class="mt-4 text-center text-tiny leading-relaxed text-text3">
          {{ loc.t('login.legalIntro', 'Входя, вы принимаете') }}
          <a href="/terms.html" class="text-text2 underline underline-offset-2 transition-colors hover:text-accent">{{ loc.t('login.legalTerms', 'Пользовательское соглашение') }}</a>
          {{ loc.t('login.legalAnd', 'и') }}
          <a href="/privacy.html" class="text-text2 underline underline-offset-2 transition-colors hover:text-accent">{{ loc.t('login.legalPrivacy', 'Политику обработки персональных данных') }}</a>.
        </p>

        <DeviceApproval v-if="needApproval" @approved="onApproved" />
      </div>

      <!-- СКАЧАТЬ ПРИЛОЖЕНИЕ — отдельная ячейка сетки, а НЕ часть правой колонки.
           ⚠️ Сначала блок стоял именно там, рядом со «скачать .exe» — и на телефоне его
           не было видно вовсе: та колонка `hidden … lg:flex`, то есть существует только
           с lg. А единственная платформа, где кнопка вообще уместна, — Android-телефон,
           то есть ровно та ширина, на которой колонки нет. Здесь блок живёт в общем
           потоке и стоит ПОД карточкой входа; на широком экране его не бывает по
           самому условию (isAndroid), поэтому раскладку ПК он не задевает.
           Вид — ОДНА строка-кнопка без карточки и описания: экран входа на телефоне
           обязан помещаться целиком, а рассказ о возможностях приложения человек и так
           увидит сразу после установки. Версию оставляем: по ней видно, что файл свежий. -->
      <a v-if="isAndroid && apk.url" :href="apk.url" download
         class="mx-auto flex w-full max-w-sm lg:hidden items-center justify-center gap-2 rounded-2xl border border-border bg-card px-4 py-3 text-sm font-semibold text-text shadow-card transition-colors hover:border-accent">
        <Smartphone class="size-4 text-accent" />
        {{ loc.t('login.mobileDownload', 'Скачать приложение') }}
        <span v-if="apk.version" class="text-xs font-medium text-text3">· {{ apk.version }}</span>
        <Download class="size-4 text-text3" />
      </a>

      <!-- Правая колонка: карточка «фичи» + (только для ПК) скачать десктоп -->
      <div class="hidden w-72 flex-col gap-4 justify-self-start lg:flex">
        <div class="rounded-2xl border border-border bg-card p-6 shadow-card">
        <h2 class="font-title text-2xl font-extrabold leading-tight text-text">Журнал, который думает вместе с вами</h2>
        <p class="mt-3 text-sm text-text3">
          Электронный журнал с ИИ-помощником «Вектор»: оценки, средний балл, долги и пропуски — понятно и под рукой.
        </p>
        <ul class="mt-5 space-y-3 text-sm">
          <li class="flex items-center gap-3 text-text"><Bot class="size-5 text-accent" /> ИИ-помощник «Вектор»</li>
          <li class="flex items-center gap-3 text-text"><Globe class="size-5 text-accent" /> Работает на всех устройствах</li>
          <li class="flex items-center gap-3 text-text"><ShieldCheck class="size-5 text-accent" /> Безопасно — по 152-ФЗ</li>
        </ul>
        <div class="mt-5 flex items-start gap-2 rounded-lg border border-border bg-card2 px-3 py-2.5">
          <Trophy class="mt-0.5 size-4 shrink-0 text-yellow" />
          <p class="text-xs font-medium text-text3">Победитель хакатона «Мы — будущее IT Бурятии»</p>
        </div>
          <a href="/offer.html"
             class="mt-5 flex items-center justify-center gap-2 rounded-sm bg-accent px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-accent2">
            <ShieldCheck class="size-4" /> Почему мы — сравнение и безопасность
          </a>
          <p class="mt-4 text-tiny text-text2">GradeBookAI · Web Edition</p>
        </div>

        <!-- Скачать десктоп-версию — только на ПК (мышь), не на телефоне/планшете и
             не внутри самой программы (там она уже установлена). -->
        <div v-if="isDesktop && !insideApp" class="rounded-2xl border border-border bg-card p-5 shadow-card">
          <div class="flex items-center gap-2">
            <Monitor class="size-5 text-accent" />
            <h3 class="font-title text-base font-extrabold text-text">{{ loc.t('login.desktop') }}</h3>
          </div>
          <p class="mt-2 text-xs text-text3">
            Полноценный офлайн-first клиент для Windows: работает без интернета, данные
            хранятся локально и синхронизируются позже. Быстрый, со встроенным Вектором.
          </p>
          <a v-if="desktop.available" :href="desktop.url" download
             class="mt-3 flex items-center justify-center gap-2 rounded-sm bg-accent px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-accent2">
            <Download class="size-4" /> Скачать GradeBookAI.exe
            <span v-if="desktop.size_mb" class="text-xs opacity-80">· {{ desktop.size_mb }} МБ</span>
          </a>
          <div v-else class="mt-3 rounded-sm border border-border2 bg-card2 px-4 py-2.5 text-center text-xs font-medium text-text3">
            Установщик готовится к выпуску
          </div>
        </div>

      </div>
    </div>

    <!-- 🔒 Второй замок: даже если `showRegister`/`showRecover` кто-то поднимет в обход
         (горячая клавиша, чужая правка, отладка), показывать будет нечего. Компоненты
         НЕ удалены — запрет снимается одной строкой `SELF_SERVICE_ENABLED`. -->
    <RegisterDialog v-if="SELF_SERVICE_ENABLED && showRegister" @close="showRegister = false" />
    <RecoverDialog v-if="SELF_SERVICE_ENABLED && showRecover" @close="showRecover = false" />

    <!-- Футер — заполняет низ, даёт «завершённость» экрану. -->
    <p class="absolute bottom-3 left-1/2 z-10 hidden w-full max-w-[94vw] -translate-x-1/2 px-4 text-center text-tiny leading-relaxed text-text3 sm:block">
      © 2026 GradeBookAI · Технологический колледж ВСГУТУ · команда Synapse
    </p>
  </div>
    <EasterEggHost />
</template>

<style scoped>
.pop-enter-active, .pop-leave-active { transition: opacity 0.16s ease, transform 0.16s ease; }
.pop-enter-from, .pop-leave-to { opacity: 0; transform: translate(-50%, 6px); }
</style>
