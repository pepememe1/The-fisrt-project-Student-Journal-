<script setup>
// AppShell — оболочка после входа (порт компоновки десктопа): широкая шапка-градиент
// СВЕРХУ на всю ширину, ниже — сайдбар слева и область контента. Каждая страница
// показывает свой заголовок (как title_lbl в десктопе). Адаптив: на телефоне
// сайдбар выезжает поверх как drawer.
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
//Тихая догрузка остальных страниц СВОЕЙ роли: страницы разрезаны по маршрутам,
//и без прогрева не посещённая страница не попадёт в офлайн-кэш браузера.
import { routes as appRoutes } from '@/router'
import { prefetchRoutes } from '@/utils/routePrefetch'
import { useRoute } from 'vue-router'
import { PanelRightOpen } from '@lucide/vue'
import Sidebar from '@/components/Sidebar.vue'
import HeaderBar from '@/components/HeaderBar.vue'
import VectorDock from '@/components/VectorDock.vue'
import ActivityShell from '@/components/activity/ActivityShell.vue'
import ActivityLauncher from '@/components/activity/ActivityLauncher.vue'
import ActivityPortfolio from '@/components/activity/ActivityPortfolio.vue'
import TimerAlarm from '@/components/activity/timer/TimerAlarm.vue'
import EasterEggHost from '@/components/easter/EasterEggHost.vue'
import { useEasterStore } from '@/stores/easterEggs'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useVectorStore } from '@/stores/vector'
import { useActivityStore } from '@/stores/activity'
import { useTtsStore } from '@/stores/tts'
import { useMessengerStore } from '@/stores/messenger'
import { useLocaleStore } from '@/stores/locale'

const locale = useLocaleStore()
const theme = useThemeStore()
const vector = useVectorStore()
const activity = useActivityStore()
const tts = useTtsStore()
const messenger = useMessengerStore()
const route = useRoute()
const sidebarOpen = ref(false)

// ━━━ ВЫЕЗЖАЮЩАЯ ШТОРКА (телефон) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//
// 🔥 ЗАКРЫТИЕ ЖИВЁТ ЗДЕСЬ, А НЕ У КАЖДОЙ ССЫЛКИ (07.09.2026, жалоба Влада: «при открытии
// настроек шторка слева остаётся и нужно кликать по пустому месту, с другими вкладками
// такого нет»). Раньше шторку гасил `@navigate` от `Sidebar`, то есть КАЖДАЯ ссылка
// обязана была не забыть о нём сообщить. Забыли ровно одну — шестерёнку настроек в
// карточке себя (`SidebarUserPanel.vue`): это отдельный `RouterLink`, до `Sidebar` его
// клик не доходит. Наш обычный класс дефекта «первое забытое место»: правило
// выполнялось в N местах вместо одного.
//
// Теперь правило одно и оно про СОБЫТИЕ, а не про ссылку: сменился маршрут — шторки
// нет. Оно верно и для тех дверей, которых ещё не написали.
const pendingTo = ref('')
let pendingTimer = null

// Сколько ждём страницу, прежде чем убрать шторку без неё. Не «таймаут загрузки», а
// защита от навигации, которая не состоялась вовсе (страж роутера увёл в сторону,
// чанк не скачался): без неё шторка осталась бы висеть с подсвеченным пунктом.
const PENDING_MAX_MS = 2000

function closeDrawer() {
  sidebarOpen.value = false
  pendingTo.value = ''
  if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null }
}

/**
 * Нажали пункт меню. Шторку НЕ гасим сразу — сначала подсвечивается выбранное.
 *
 * ⚠️ Нажатие в ТОТ ЖЕ раздел закрывает шторку немедленно: маршрут не сменится, а значит
 * ждать нечего и `watch` ниже не сработает НИКОГДА. Без этой ветки повторное нажатие по
 * уже открытому пункту оставляло бы меню висеть — то есть починка одного залипания
 * завела бы другое.
 */
function onDrawerNavigate(to) {
  if (!to || to === route.path) { closeDrawer(); return }
  pendingTo.value = to
  if (pendingTimer) clearTimeout(pendingTimer)
  pendingTimer = setTimeout(closeDrawer, PENDING_MAX_MS)
}

// Маршрут сменился — страница уже разрешена и её чанк скачан (страницы ленивые, роутер
// ждёт загрузки). Значит момент честный: шторка уезжает не «через столько-то мс», а
// когда за ней действительно есть что показать.
watch(() => route.path, () => { if (sidebarOpen.value) closeDrawer() })

onBeforeUnmount(() => { if (pendingTimer) clearTimeout(pendingTimer) })

// Embed-режим: SPA, встроенная в чужую оболочку, — тогда прячем свою шапку и/или меню,
// чтобы не вышло «навигации внутри навигации». Флаг приходит из localStorage `gb.embed`
// (его ставит страница-передатчик десктопа) либо из `?embed=` в адресе; фиксируется один
// раз на загрузке.
//   '1'   — встроена ОДНА страница: прячем и шапку, и меню;
//   'nav' — встроен весь кабинет: прячем ТОЛЬКО шапку (её роль играет заголовок окна);
//   иначе — обычный сайт/телефон/окно программы, всё своё.
//
// 🔴 ЧЕСТНО: СЕГОДНЯ НИ ОДИН ИЗ ДВУХ РЕЖИМОВ НЕ ВКЛЮЧАЕТСЯ. Ключ `gb.embed` ставит
// страница-передатчик сессии `desktop/local_api.py::install_desktop_bootstrap`, а ЗНАЧЕНИЕ
// ей передаёт `desktop/webview2_app.py` — и передаёт '0', то есть
// «обычный режим». Прежний комментарий описывал ситуацию, когда десктоп встраивал SPA
// нативной оболочкой (`ui/messenger_web.py`, `ui/vue_dashboard.py`, `ui/vue_shell.py`) —
// все три модуля удалены вместе с Qt, окно программы теперь показывает ту же SPA целиком
// и своей шапки не рисует. Ветки оставлены рабочими намеренно: `?embed=1` — дешёвый
// способ встроить кабинет в чужую страницу, и ломать его уборкой незачем. Но читать это
// как «так работает десктоп» больше нельзя.
const embedMode = (() => {
  try {
    const q = new URLSearchParams(window.location.search)
    const v = q.get('embed') || localStorage.getItem('gb.embed') || ''
    return v === '1' || v === 'nav' ? v : ''
  } catch { return '' }
})()
const embed = embedMode === '1'                 //прятать навигацию (одна страница)
const chromeless = embedMode !== ''             //прятать шапку (любой режим внутри окна)

// ⚠️ НИ ЗАГОЛОВКА, НИ ПОДЗАГОЛОВКА СТРАНИЦЫ ЗДЕСЬ НЕТ. Крупный заголовок убрали раньше
// (дублировал подсвеченный пункт сайдбара), подзаголовок — 25.08.2026: он делал то же
// самое, только мелким шрифтом («Расписание» → «Пары ВСГУТУ»). Имя раздела видно в
// сайдбаре, а на телефоне его называет мобильная полоса (HeaderBar.vue, meta.title).
//
// Осталось ПОЯСНЕНИЕ (`meta.note`) — но это другая вещь, см. `noted()` в router/index.js:
// оно говорит о странице то, чего по названию не видно. Сейчас такое ровно одно.
const note = computed(() => (route.meta?.note
  ? locale.t(route.meta.i18nNote || '', route.meta.note)
  : ''))
// Боковой Вектор виден на всех страницах, КРОМЕ самой вкладки «ИИ Помощник»
// (там полноразмерный Вектор в контенте). Только десктоп (на мобиле не показываем).
const onVectorPage = computed(() => route.path.endsWith('/vector'))
const showDock = computed(() => !onVectorPage.value)

// «Вектор виден» = полноэкранный на вкладке ИИ ЛИБО открытая боковая шторка (шторка —
// только на широком экране lg, на мобиле её нет). Озвучка звучит, пока виден хоть один
// Вектор, и обрывается РОВНО когда пропал последний: ушли на вкладку без шторки — тишина;
// на вкладке шторка открыта — Вектор договаривает; закрыли шторку — тишина.
// ⚠️ ЧИСЛО ОБЯЗАНО СОВПАДАТЬ С `--breakpoint-lg` в style.css. Разъедутся — появится
// полоса ширины, где разметка уже «настольная», а логика ещё считает нас телефоном (или
// наоборот): сайдбар нарисован, а шторка думает, что она нужна. Держит
// `web/tests/breakpoint.test.mjs`.
const LG_PX = 940
const isLg = ref(typeof window !== 'undefined' && window.matchMedia(`(min-width:${LG_PX}px)`).matches)
let _mq = null
const _onMq = (e) => { isLg.value = e.matches }
if (typeof window !== 'undefined') {
  _mq = window.matchMedia(`(min-width:${LG_PX}px)`)
  _mq.addEventListener('change', _onMq)
}
const dockShown = computed(() => showDock.value && !vector.collapsed && isLg.value)
const vectorShown = computed(() => onVectorPage.value || dockShown.value)
watch(vectorShown, (now, was) => { if (was && !now) tts.stop() })

// Фоновый счётчик непрочитанных для бейджа «Сообщения» в меню (живёт на всех страницах).
// На самой вкладке мессенджера свой опрос чаще — этот лишь держит бейдж свежим глобально.
let _unreadTimer = null
let _onVisible = null       // обновить счётчик, когда вкладку вернули
onMounted(() => {
  // ⚠️ (живой отзыв Влада) Тема веб≠десктоп на ОДНОМ аккаунте — оказалось, что здесь
  // роуминг был выключен для ВСЕХ embed-режимов разом, хотя причина («оболочка окна уже
  // покрашена, роуминг перекрасил бы её в другую») касалась ТОЛЬКО режима '1' — узкой
  // одностраничной встройки, у которой была отдельно раскрашенная нативная рамка.
  // Нативных рамок больше нет вовсе (Qt удалён), так что тема роумится всегда — как на
  // сайте. Условие оставлено на случай, если SPA снова встроят одной страницей в чужую
  // раскрашенную оболочку. Ещё нужен пробитый прокси `/me/prefs`
  // (см. `desktop/local_api.py::_PROXY_PREFIXES`) — без него запрос уходил бы в
  // локальную зеркальную копию, а не на бой.
  if (embedMode !== '1') theme.loadFromPrefs()
  messenger.loadChats()
  //Прогрев идёт в простое время и НИЧЕГО не блокирует: экран уже показан. Смысл —
  //офлайн-кэш браузера и мгновенный первый переход по меню (см. utils/routePrefetch.js).
  prefetchRoutes(appRoutes, auth.role)
  //🔥 ВИДЖЕТ РАСПИСАНИЯ НАПОЛНЯЕМ И ЗДЕСЬ, а не только при вводе пароля (01.09.2026).
  //Обновление жило в `_afterLogin`, то есть срабатывало ТОЛЬКО при свежем входе — а на
  //телефоне сессия живёт семь суток, и всё это время виджет показывал снимок недельной
  //давности. Оболочка монтируется у каждого вошедшего при каждом запуске приложения, и
  //это единственное место, где «человек открыл журнал» видно наверняка.
  //⚠️ Внутри стоит порог по времени (шесть часов), поэтому десять запусков подряд дадут
  //один запрос, а не десять. Ошибку глотаем: виджет — дополнение, ронять из-за него
  //открытие журнала нельзя.
  import('@/services/scheduleWidget')
    .then(({ refreshIfStale }) => refreshIfStale(auth.role))
    .catch(() => { /* виджета на этой платформе нет — обычное дело */ })
  // ⚠️ Счётчик непрочитанного тикает и ВНЕ мессенджера (значок в меню), поэтому таймер
  // живёт здесь, а не в сторе — и `stopPolling()` его не гасит. В свёрнутой вкладке
  // ходить на сервер незачем: человек её не видит, а вернувшись, всё равно получит
  // свежие числа первым же тиком (обновляем сразу при возврате).
  _unreadTimer = setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return
    messenger.loadChats()
  }, 20000)
  _onVisible = () => { if (!document.hidden) messenger.loadChats() }
  document.addEventListener('visibilitychange', _onVisible)
})
onBeforeUnmount(() => {
  if (_mq) _mq.removeEventListener('change', _onMq)
  if (_unreadTimer) clearInterval(_unreadTimer)
  if (_onVisible) document.removeEventListener('visibilitychange', _onVisible)
})

// ━━ ПАСХАЛКИ ━━ Дерево Делтарун выпадает ИМЕННО на переходе между вкладками, поэтому
// бросок живёт здесь, в оболочке: страница о нём знать не должна, а хост переживает
// смену маршрута.
//
// 🔥 ЗДЕСЬ СТОЯЛ «запрос на каждый переход не страшен» — и это оказалось неправдой,
// измеренной по журналу боевой машины: **3777 бросков за сутки**, почти все отсюда.
// Каждый клик по чату в мессенджере меняет путь, то есть один человек за минуту
// перелистывания даёт десятки запросов. На одном ядре это заметно, а при шансе 1/66
// дерево ещё и выпадало по нескольку раз за сеанс и перестало читаться как находка.
//
// Лечится в ДВУХ местах, и оба нужны:
//   • на сервере — кулдаун 5 минут ПОШТУЧНО для этой пасхалки (`EGG_COOLDOWN_S`),
//     он и есть настоящее правило: клиенту такое доверять нельзя;
//   • здесь — минимальный промежуток между ЗАПРОСАМИ. Он ничего не решает по правилам
//     и нужен ровно для того, чтобы не ломиться на сервер тридцать раз в минуту за
//     ответом, который заведомо будет «нет».
//
// 🔥 ПРОМЕЖУТОК БЫЛ 45 СЕКУНД, И ЭТО СДЕЛАЛО ДЕРЕВО НЕДОСТИЖИМЫМ (правка 24.08.2026).
// Он задумывался как защита от лишних запросов, но оказался ГЛАВНЫМ ограничителем:
// 45 секунд — это 1.3 броска в минуту, при шансе 1/66 ждать в среднем 50 МИНУТ, а за
// пять минут кликанья шанс увидеть дерево — 10 %. Влад с Ярославом честно щёлкали
// вкладками и не видели его ни разу; я списывал это на невезение, пока не посчитал.
//
// ⚠️ Настоящий ограничитель частоты — КУЛДАУН НА СЕРВЕРЕ (5 минут). Он и так не даёт
// дереву мозолить глаза: чаще, чем раз в пять минут, оно не выпадет физически. Держать
// поверх него ещё и клиентскую задержку значило поставить вторую стену там, где первая
// уже всё решает, — и первая при этом видимая, а вторая нет.
//
// 5 секунд достаточно, чтобы не слать по три запроса на один быстрый двойной клик, и
// не мешают: при обычных 12 переключениях в минуту дерево ищется около пяти минут.
const easter = useEasterStore()
const TREE_ASK_MS = 5_000
let treeAskedAt = 0
watch(() => route.path, () => {
  const now = Date.now()
  if (now - treeAskedAt < TREE_ASK_MS) return
  treeAskedAt = now
  easter.roll('deltarune_tree')
})

// Пасхалки входа спрашиваем один раз НА ЧЕЛОВЕКА, а не на вкладку. Замок нужен против
// F5: без него перезагрузка страницы давала бы новый бросок, и «редкое при входе»
// превратилось бы в «частое при обновлении».
//
// 🔥 ЗАМОК КЛЮЧУЕТСЯ ЛОГИНОМ, И ЭТО КУПЛЕНО ДЕФЕКТОМ (23.08.2026). Раньше он был
// просто «=== '1'» на всю жизнь вкладки: поставили однажды — и больше пасхалки входа
// не спрашивались НИ РАЗУ. Ни после выхода и повторного входа, ни при смене человека
// за общим компьютером колледжа. Влад поставил себе сегодняшнюю дату рождения, вошёл
// в той же вкладке — и торта не увидел вовсе, потому что запроса просто не было.
// Ошибки при этом нигде: ни в консоли, ни в журнале сервера.
//
// ⚠️ И ЖДЁМ РОЛЬ. `afterLogin()` для не-студента выходит сразу и второй попытки не
// делает, а роль к моменту монтажа оболочки может быть ещё не подставлена. Без
// ожидания это тот же немой отказ, только по другой причине.
const auth = useAuthStore()
async function askLoginEggs() {
  if (!auth.role) {
    //Ждём роль, но не бесконечно: полсекунды с запасом, дальше просто не спрашиваем.
    await new Promise((resolve) => {
      const stop = watch(() => auth.role, (r) => { if (r) { stop(); resolve() } })
      setTimeout(() => { stop(); resolve() }, 3000)
    })
  }
  if (auth.role !== 'student') return

  // 🔥 СПИСОК ПОЛУЧЕННЫХ АЧИВОК ГРУЗИМ ВСЕГДА — ДО замка ниже (найдено 23.08.2026).
  // Раньше он загружался внутри `afterLogin()`, то есть ПОД тем же замком «один раз на
  // вкладку». После обычной перезагрузки страницы замок уже стоял, список оставался
  // пустым — и правило «не спрашивать про уже полученную ачивку» переставало работать
  // целиком. Наружу это выглядело так: человек, давно закрывший находку, снова получал
  // вопрос «останьтесь, а то не заберёте», хотя забирать нечего.
  // ⚠️ Замок существует ради БРОСКА (чтобы F5 не давал новый шанс). К чтению своего
  // списка он отношения не имеет, и держать их вместе было ошибкой.
  easter.loadOwned()

  // 🔥 ЗАМОК ГАСИТ ТОЛЬКО БРОСОК СЦЕНЫ, А НЕ ВЕСЬ ЗАПРОС. Раньше он стоял вокруг всего
  // `afterLogin()`, и после F5 клиент не спрашивал сервер ни о чём — вместе с броском
  // пропадала и метка аватарки, хотя она не бросок вовсе, а свойство человека и дня.
  // Одно и то же украшение то появлялось само, то исчезало само.
  let scene = true
  const key = `gb.egg.login:${auth.user?.login || ''}`
  try {
    if (sessionStorage.getItem(key) === '1') scene = false
    else sessionStorage.setItem(key, '1')
  } catch { /* приватный режим — значит просто спросим ещё раз, не страшно */ }
  easter.afterLogin(scene)
}
onMounted(askLoginEggs)

</script>

<template>
  <!-- Корень — ГОРИЗОНТАЛЬНЫЙ: сайдбар и контент рядом, полосы во всю ширину сверху
       больше нет (см. HeaderBar.vue — там осталась только мобильная версия). -->
  <div class="flex h-full overflow-hidden">
    <!-- Десктоп: постоянный сайдбар -->
    <div v-if="!embed" class="hidden lg:block">
      <Sidebar />
    </div>

    <!-- Мобайл: выезжающий сайдбар -->
    <transition name="drawer">
      <div v-if="sidebarOpen" class="fixed inset-0 z-40 lg:hidden">
        <div class="absolute inset-0" style="background: var(--gb-overlay)" @click="closeDrawer" />
        <div class="gb-drawer-panel absolute inset-y-0 left-0 z-50 shadow-xl">
          <Sidebar :open="sidebarOpen" :pending="pendingTo" @navigate="onDrawerNavigate" />
        </div>
      </div>
    </transition>

    <div class="flex min-h-0 min-w-0 flex-1 flex-col">
      <!-- Компактная полоса — ТОЛЬКО телефон (сама прячется с lg): там сайдбар за
           бургером, и без неё не было бы ни меню, ни понимания, какой раздел открыт. -->
      <HeaderBar v-if="!chromeless" @toggle-sidebar="sidebarOpen = !sidebarOpen" />

      <!-- Контент: мягкий фон + сетка (как AnimatedBackground в десктопе) -->
      <main class="app-canvas min-h-0 flex-1 overflow-y-auto" style="padding-bottom: env(safe-area-inset-bottom)">
        <!-- Отступы ужаты (3.5.6): было p-4 / sm:px-7 sm:py-6 — «жирная рамка» вокруг
             активного окна съедала полосу по всему периметру, ничего в ней не показывая. -->
        <!-- Правый отступ добавляем ТОЛЬКО когда у края висит вкладка-возврат Вектора:
             она fixed и иначе ложится на последнюю колонку страницы (на расписании
             накрывала субботу). Панель раскрыта или её нет — отступ не нужен. -->
        <!-- ⚠️ `gb-pages` (position: relative) — система координат для УХОДЯЩЕЙ страницы,
             см. стили внизу файла. Без неё две страницы во время перехода встают друг
             под другом обычным потоком. -->
        <p v-if="note && !embed" class="mb-2.5 text-xs text-text3">{{ note }}</p>
        <div class="gb-pages"
             :class="[embed ? 'h-full p-0' : 'mx-auto max-w-[1700px] p-3 sm:px-4 sm:py-3.5',
                      (!embed && showDock && vector.collapsed) ? 'lg:pr-10' : '']">
                    <!-- ⚠️ «Вкладка не прогружается, помогает только F5» — НАЙДЕНО живым прогоном
               (Влад, DevTools в браузере): в момент бага на месте страницы в DOM стоял
               ПУСТОЙ узел-комментарий Vue вместо компонента, при этом `route.meta`
               (подзаголовок) уже отражал НОВЫЙ маршрут и ошибок в консоли не было —
               то есть роутинг отрабатывал верно, а `Component` из слота `RouterView` в
               этот момент был `undefined`. Прежние страховки (`app.config.errorHandler`,
               `:key="matched.fullPath"`) не били по этой причине — ошибки и не было,
               ловить было нечего, а форс-ремонт нового компонента не помогает, если сам
               Vue ещё не отдал компонент для монтирования.
               Корень — `mode="out-in"`: у него есть встроенная очередь «сначала дождись
               ухода старого узла, потом покажи новый». Быстрое «зашёл на страницу и сразу
               ушёл» (Профиль/Настройки — тяжелее остальных по числу дочерних компонентов,
               поэтому чаще всего попадали в это окно первыми) укладывает ВТОРОЙ переход
               внутрь ещё не завершённого ухода первого — и `<transition>` способен
               застрять в этом ожидании насовсем: именно поэтому ломались уже ВСЕ
               последующие страницы, а не только та, куда шёл переход в момент сбоя.
               Фикс — убрать `mode="out-in"` целиком: без него уход старой и появление
               новой страницы идут ОДНОВРЕМЕННО (обычный кроссфейд), и очереди/ожидания,
               в которой можно застрять, просто не существует. Разница на глаз — тот же
               плавный переход 0.15с, только старая и новая страница на миг видны
               одновременно вместо строгой смены друг за другом. `:key="matched.fullPath"`
               и `app.config.errorHandler` оставлены — они не лишние (первый всё ещё
               нужен для смены query/параметров у ОДНОГО и того же компонента, второй —
               диагностика на будущее), просто не были причиной именно этого бага. -->
          <RouterView v-slot="{ Component, route: matched }">
            <transition name="fade" :duration="150">
              <component :is="Component" :key="matched.fullPath" />
            </transition>
          </RouterView>
        </div>
      </main>
    </div>

    <!-- Боковой Вектор — ОВЕРЛЕЙ поверх страницы, а не колонка в потоке (живой отзыв
         3.5.6: «ИИ-шторка должна налазить на текущую страницу, а не сдвигать её»).
         Раньше это был обычный <aside> в той же flex-строке: открытие шторки сжимало
         контент на 384 px, таблицы журнала перевёрстывались, и человек терял место, на
         которое смотрел. Теперь она fixed поверх, с тенью и выездом справа. -->
    <transition name="dock">
      <div v-if="!embed && dockShown" class="fixed inset-y-0 right-0 z-30 hidden shadow-2xl lg:block">
        <VectorDock />
      </div>
    </transition>

    <!-- Панель скрыта → вкладка-возврат у правого края (десктоп).
         ⚠️ Раньше рядом с иконкой стояло слово «Вектор», набранное ВЕРТИКАЛЬНО
         (writing-mode). Вкладка от этого была вдвое шире и заметно ложилась на правую
         колонку страницы — на расписании накрывала субботу. Осталась одна иконка с
         подсказкой: назначение читается по ней, а место она занимает вдвое меньше. -->
    <!-- Активности (docs/PLAN-ACTIVITIES.md §10). ⚠️ РЯДОМ с <RouterView>, а НЕ внутри
         него: свёрнутое окно обязано пережить переход в другой раздел, а вмешиваться в
         <transition> этого файла нельзя — именно там `mode="out-in"` однажды намертво
         вешал переходы между страницами (3.6.6). Оверлей выбора категории живёт здесь же
         по той же причине: команду `/активность` набирают в мессенджере, но открыть его
         должен слой, переживающий навигацию. -->
    <ActivityShell v-if="!embed" />
    <ActivityLauncher v-if="!embed && activity.launcherFor"
                      :conversation-id="activity.launcherFor"
                      @close="activity.closeLauncher()" />
    <!-- Журнал открывается ИЗНУТРИ окна активностей (ссылка под категориями), а
         не отдельной кнопкой в шапке: место в шапке одно, и оно отдано частому
         действию — запуску активности. -->
    <!-- Окно «время вышло» — на ЛЮБОЙ вкладке: таймер ставят и идут работать. -->
    <TimerAlarm />

    <!-- Втулка колеса открывает ТОТ ЖЕ журнал, что и ссылка внутри активности
         (ActivityShell): раньше здесь висела вторая, урезанная его версия — строка с одним
         числом вместо участников и баллов, — и нажавший «Журнал» видел именно её. -->
    <ActivityPortfolio v-if="!embed && activity.journalFor"
                       :conversation-id="activity.journalFor" @close="activity.closeJournal()" />

    <button v-if="!embed && showDock && vector.collapsed" @click="vector.setCollapsed(false)"
            :aria-label="locale.t('vectorDock.showPanel', 'Показать панель Вектора')"
            :title="locale.t('vectorDock.showPanelShort', 'Показать Вектора')"
            class="fixed right-0 top-1/2 z-30 hidden -translate-y-1/2 place-items-center rounded-l-lg border border-r-0 border-border bg-card px-1.5 py-3 text-accent shadow-card transition-colors hover:bg-accent-glow lg:grid">
      <PanelRightOpen class="size-5" />
    </button>
  </div>
    <EasterEggHost />
</template>

<style scoped>
/* Мягкий градиент bg→bg2 + едва заметная акцентная сетка — как статичный
   AnimatedBackground в десктопе. */
.app-canvas {
  background:
    linear-gradient(180deg, var(--gb-bg), var(--gb-bg2)),
    repeating-linear-gradient(0deg, transparent 0 63px, color-mix(in srgb, var(--gb-accent) 4%, transparent) 63px 64px),
    repeating-linear-gradient(90deg, transparent 0 63px, color-mix(in srgb, var(--gb-accent) 4%, transparent) 63px 64px);
}
.fade-enter-active,
.fade-leave-active { transition: opacity 0.15s ease; }
.fade-enter-from,
.fade-leave-to { opacity: 0; }

/* ━━━ ШТОРКА: ЗАТЕМНЕНИЕ ГАСНЕТ, ПАНЕЛЬ ЕДЕТ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Раньше здесь стоял общий `fade`, то есть меню ПРОЯВЛЯЛОСЬ на месте — отсюда «всё
   слишком резко»: панель возникала целиком, без направления, и человеку нечем было
   понять, откуда она взялась и куда денется. Движение тут несёт смысл: слева пришло —
   слева и уйдёт.

   Кривая `cubic-bezier(.32,.72,0,1)` — быстрый старт и длинное мягкое торможение (так
   ведут себя панели в iOS). Обычный `ease` тормозит слишком поздно, и на телефоне это
   читается как рывок в конце.

   ⚠️ Уход КОРОЧЕ прихода (.22 против .3): открытие человек рассматривает, закрытие он
   уже решил и ждёт результата — одинаковая длительность ощущается вязкой. */
.drawer-enter-active,
.drawer-leave-active { transition: opacity 0.24s ease; }
.drawer-enter-from,
.drawer-leave-to { opacity: 0; }

.drawer-enter-active .gb-drawer-panel {
  transition: transform 0.3s cubic-bezier(0.32, 0.72, 0, 1);
}
.drawer-leave-active .gb-drawer-panel {
  transition: transform 0.22s cubic-bezier(0.32, 0.72, 0, 1);
}
.drawer-enter-from .gb-drawer-panel,
.drawer-leave-to .gb-drawer-panel { transform: translateX(-100%); }

/* Человеку с `prefers-reduced-motion` шторка появляется и исчезает без поездки — но
   появляется, а не пропадает вместе с меню. */
@media (prefers-reduced-motion: reduce) {
  .drawer-enter-active,
  .drawer-leave-active { transition: none; }
  .drawer-enter-active .gb-drawer-panel,
  .drawer-leave-active .gb-drawer-panel { transition: none; }
  .drawer-enter-from .gb-drawer-panel,
  .drawer-leave-to .gb-drawer-panel { transform: none; }
}

/* 🔥 УХОДЯЩАЯ СТРАНИЦА ВЫНИМАЕТСЯ ИЗ ПОТОКА (25.08.2026). Влад: «переключался быстро
   между ИИ-помощником и сообщениями — внизу экрана было видно верхнюю часть страницы
   сообщений». Это не мигание и не подгрузка, а прямое следствие прежней починки:
   когда убрали `mode="out-in"` (он подвешивал переходы намертво, см. длинный
   комментарий у RouterView), старая и новая страницы стали жить в DOM ОДНОВРЕМЕННО —
   и вторая честно вставала обычным блоком ПОД первой на те самые 150 мс.

   Вернуть `out-in` нельзя: там очередь ожидания, в которой переход застревал совсем.
   Поэтому уходящую вынимаем из потока и кладём поверх — тогда обе занимают одно место,
   а не два, и переход выглядит кроссфейдом, каким и задумывался.

   ⚠️ `pointer-events: none` обязателен: уходящая страница ещё полсекунды перехватывала
   бы клики поверх новой — человек попадал бы по кнопке, которой на экране уже нет.
   ⚠️ Работает только вместе с `position: relative` на `.gb-pages` (обёртка выше).
   Уберут её — уходящая страница отсчитается от окна и уедет под шапку. */
.fade-leave-active {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.gb-pages { position: relative; }

/* Смены страницы человеку с `prefers-reduced-motion` не нужно: показываем сразу. */
@media (prefers-reduced-motion: reduce) {
  .fade-enter-active,
  .fade-leave-active { transition: none; }
}
/* Шторка Вектора выезжает справа поверх страницы (не раздвигает её). */
.dock-enter-active,
.dock-leave-active { transition: transform 0.18s ease, opacity 0.18s ease; }
.dock-enter-from,
.dock-leave-to { transform: translateX(16px); opacity: 0; }
</style>
