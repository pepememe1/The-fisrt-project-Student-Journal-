/**
 * router/index.js — маршруты и ролевые guard'ы.
 *
 * Три ветки под роли (student/teacher/admin) в оболочке AppShell. Состав страниц —
 * как в десктопных дашбордах (см. config/nav.js). Guard не пускает в чужую ветку и на
 * защищённые страницы без входа.
 */
import { createRouter, createWebHistory } from 'vue-router'
import { useEasterStore, leaveAsk } from '@/stores/easterEggs'
import { useConfirm } from '@/composables/useConfirm'
import { useAuthStore } from '@/stores/auth'
import { needsServer } from '@/api/server'
import { HOME_BY_ROLE } from '@/config/nav'

// 🔥 СТРАНИЦЫ ГРУЖУТСЯ ПО ТРЕБОВАНИЮ, А НЕ ВСЕ СРАЗУ (28.08.2026, просьба Ярослава
// «убери мусор, который замедляет веб»).
//
// До этого все 45 страниц импортировались статически, и сборщик складывал их в ОДИН
// файл на 1 468 кБ (415 кБ после сжатия). Его целиком скачивал каждый — студент вместе
// с админкой, экраном сервера, модерацией и разбором расписания, то есть с кодом,
// которого он не увидит никогда. На телефоне по вузовскому Wi-Fi это и есть та самая
// пауза перед первым экраном.
//
// `() => import(...)` заставляет Vite нарезать по маршрутам: страница едет в момент
// перехода на неё. Роутер такую форму понимает сам, никаких дополнительных обёрток.
//
// ⚠️ ТРИ ИСКЛЮЧЕНИЯ ОСТАЮТСЯ СТАТИЧЕСКИМИ, и это не недосмотр: `AppShell` — оболочка,
// она нужна в первом же кадре; `LoginPage` — первый экран, отложить его значит добавить
// сетевой круг ровно там, где задержка заметнее всего; `NotFoundPage` крошечная, и
// подгружать её отдельным запросом ради экономии килобайта бессмысленно.
//
// ⚠️ Оффлайн (десктоп и мобилка) не страдает: там весь бандл уже лежит на устройстве —
// service worker и OTA-бандл кладут все чанки рядом, «подгрузка» читается с диска.
import AppShell from '@/layouts/AppShell.vue'
import LoginPage from '@/pages/LoginPage.vue'
import NotFoundPage from '@/pages/NotFoundPage.vue'
import { decideMiss } from '@/utils/missedRoute'
const NotificationsPage = () => import('@/pages/NotificationsPage.vue')
const ConnectServer = () => import('@/pages/ConnectServer.vue')
const ResetPassword = () => import('@/pages/ResetPassword.vue')
const InviteRegister = () => import('@/pages/InviteRegister.vue')
const VectorPage = () => import('@/pages/VectorPage.vue')
const MessengerPage = () => import('@/pages/MessengerPage.vue')
const SchedulePage = () => import('@/pages/SchedulePage.vue')
const CoursesPage = () => import('@/pages/CoursesPage.vue')
const CourseDetailPage = () => import('@/pages/CourseDetailPage.vue')
const Settings = () => import('@/pages/Settings.vue')
const StudentDashboard = () => import('@/pages/student/StudentDashboard.vue')
const StudentJournal = () => import('@/pages/student/StudentJournal.vue')
const StudentStats = () => import('@/pages/student/StudentStats.vue')
const TeacherJournal = () => import('@/pages/teacher/TeacherJournal.vue')
const TeacherStudents = () => import('@/pages/teacher/TeacherStudents.vue')
const TeacherStats = () => import('@/pages/teacher/TeacherStats.vue')
const CuratorView = () => import('@/pages/teacher/CuratorView.vue')
const AdminDashboard = () => import('@/pages/admin/AdminDashboard.vue')
const AdminTeachers = () => import('@/pages/admin/AdminTeachers.vue')
const AdminStudents = () => import('@/pages/admin/AdminStudents.vue')
const AdminRegistrations = () => import('@/pages/admin/AdminRegistrations.vue')
const AdminGroups = () => import('@/pages/admin/AdminGroups.vue')
const AdminSubjectArchive = () => import('@/pages/admin/AdminSubjectArchive.vue')
const AdminGroupArchive = () => import('@/pages/admin/AdminGroupArchive.vue')
const AdminSubjects = () => import('@/pages/admin/AdminSubjects.vue')
const AdminSchedule = () => import('@/pages/admin/AdminSchedule.vue')
const AdminScheduleIssues = () => import('@/pages/admin/AdminScheduleIssues.vue')
const AdminSessions = () => import('@/pages/admin/AdminSessions.vue')
const AdminRequests = () => import('@/pages/admin/AdminRequests.vue')
const MonitorPage = () => import('@/pages/admin/MonitorPage.vue')
const AdminAiSettings = () => import('@/pages/admin/AdminAiSettings.vue')
const AdminData = () => import('@/pages/admin/AdminData.vue')
const AdminServer = () => import('@/pages/admin/AdminServer.vue')
const AdminMessenger = () => import('@/pages/admin/AdminMessenger.vue')
const RulesPage = () => import('@/pages/RulesPage.vue')
const ParentJournal = () => import('@/pages/parent/ParentJournal.vue')
const AdminParents = () => import('@/pages/admin/AdminParents.vue')
const AdminAudit = () => import('@/pages/admin/AdminAudit.vue')

// i18nTitle — необязательный ключ словаря; без него заголовок остаётся русским
// литералом title (обратная совместимость).
//
// 🔥 ПОДЗАГОЛОВКА СТРАНИЦЫ БОЛЬШЕ НЕТ (25.08.2026, просьба Влада). Он стоял строкой над
// содержимым и пересказывал название уже выбранной вкладки: «Расписание» → «Пары
// ВСГУТУ», «Курсы» → «Учебные курсы вашей группы», «Журнал оценок» → «Ваши оценки по
// предметам». Раздел подсвечен в сайдбаре, а на телефоне его называет мобильная полоса
// — третья подпись к тому же месту была шумом.
// ⚠️ Строки удалены ВМЕСТЕ с отрисовкой, а не оставлены «на всякий случай»: аргумент,
// который никуда не едет, читается следующим как рабочий и однажды введёт в
// заблуждение. Понадобится пояснение к конкретной странице — ему место ВНУТРИ этой
// страницы, рядом с тем, что оно поясняет.
const page = (path, component, title, i18nTitle) =>
  ({ path, component, meta: { title, i18nTitle } })

// ⚠️ `note` — НЕ вернувшийся подзаголовок, и разницу надо держать в голове, иначе он
// расползётся обратно по всем страницам. Подзаголовок пересказывал название вкладки
// («Расписание» → «Пары ВСГУТУ») и был шумом. Пояснение говорит о странице то, чего по
// её названию НЕ ВИДНО, и что человек иначе поймёт неверно.
//
// Правило отбора простое: убери строку — потеряется ли ФАКТ? Если теряется только
// повторение названия, строки быть не должно.
//
// Сейчас такая строка ровно одна (см. админское расписание ниже). Соберётся вторая —
// проверь её этим же вопросом, а не «у соседней страницы же есть».
const noted = (route, note, i18nNote) =>
  ({ ...route, meta: { ...route.meta, note, i18nNote } })

// 🔥 «ПРОФИЛЬ» — ЭТО НЕ СТРАНИЦА, А ВКЛАДКА НАСТРОЕК (02.09.2026, баг Влада: «открываем
// профиль через меню в левом нижнем углу — открывается как страница, а не как вкладка в
// оверлее настроек»). Профиль переехал внутрь настроек ещё 31.08, но МАРШРУТ остался
// рабочим и продолжал рисовать ту же страницу отдельно — то есть у одного содержимого
// стало два разных вида, и какой человек увидит, зависело от того, откуда он нажал.
// Маршрут не удаляем: на него ведут старые закладки и ссылки из чужих карточек, и 404
// там сломал бы работающее. Теперь он ПЕРЕАДРЕСУЕТ в настройки, где профиль открыт
// первым — то есть старая ссылка приводит ровно туда же, куда новая кнопка.
// ⚠️ Переадресация функцией, а не строкой: у каждой роли свой префикс (`/student`,
// `/teacher`, `/admin`, `/parent`), и строковый путь пришлось бы писать четыре раза.
const profileToSettings = {
  path: 'profile',
  redirect: (to) => to.path.replace(/\/profile\/?$/, '/settings'),
}

//Экспортируем таблицу: по ней `utils/routePrefetch.js` находит ленивые загрузчики
//страниц роли, а тест проверяет, что нарезка вообще состоялась.
export const routes = [
  { path: '/connect', component: ConnectServer, meta: { public: true } },
  { path: '/login', component: LoginPage, meta: { public: true } },
  //🔥 Расписание БЕЗ входа (01.09.2026). Раньше, вылетев из аккаунта по истечении токена,
  //человек терял и расписание — хотя оно общедоступно и лежит на портале ВСГУТУ открыто.
  //Ленивый импорт: страницу открывают редко, а её вес иначе поехал бы в первый кадр
  //всем, включая тех, кто просто входит.
  //🔥 АДРЕС «/schedule», А НЕ «/public/schedule» (02.09.2026). Заведённая накануне
  //страница жила по адресу, который на сервере уже занят JSON-ручкой виджета
  //(`routers/publicschedule.py`). Роутер Vue об этом не знает: переход ПО ССЫЛКЕ внутри
  //сайта работал, а прямой заход, обновление страницы и пересланная ссылка отдавали
  //голый JSON — то есть страница была недостижима ровно тогда, когда нужна (человека
  //выбросило из аккаунта, он открывает адрес заново). Освободить занят адрес нельзя:
  //по нему ходит виджет из УЖЕ ОПУБЛИКОВАННОГО в RuStore APK, и сменить его там нечем.
  //Правило: страница и ручка API не делят один URL — у них разные ответы на один
  //запрос, и кто выиграет, решает порядок подключения, а не замысел.
  { path: '/schedule', component: () => import('@/pages/PublicSchedule.vue'),
    meta: { public: true } },
  // Публичная намеренно: человек приходит сюда именно потому, что войти не может.
  { path: '/reset-password', component: ResetPassword, meta: { public: true } },
  // Приглашение куратора: сюда человек приходит ДО того, как у него появился аккаунт.
  // Токен в ПУТИ, а не в query: так ссылка читается как приглашение и переживает
  // пересылку мессенджерами, которые любят обрезать «?…».
  { path: '/invite/:token', component: InviteRegister, meta: { public: true } },
  {
    path: '/',
    redirect: () => {
      const auth = useAuthStore()
      return auth.isAuthenticated ? HOME_BY_ROLE[auth.role] || '/login' : '/login'
    },
  },

  // СТУДЕНТ ───────────────────────────────────────────────
  {
    path: '/student', component: AppShell, meta: { requiresAuth: true, role: 'student' },
    children: [
      // Главная показывает ИМЯ студента как заголовок (title_lbl в десктопе) — рендерит
      // сама StudentDashboard, поэтому статический title из AppShell тут не нужен.
      { path: '', component: StudentDashboard, meta: {} },
      page('journal', StudentJournal, 'Журнал оценок', 'nav.journal'),
      page('schedule', SchedulePage, 'Расписание', 'nav.schedule'),
      page('stats', StudentStats, 'Моя статистика', 'router.myStats'),
      page('courses', CoursesPage, 'Курсы', 'nav.courses'),
      { path: 'courses/:id', component: CourseDetailPage, meta: { title: 'Курс', i18nTitle: 'nav.courses' } },
      { path: 'vector', component: VectorPage, meta: { title: 'ИИ Помощник', i18nTitle: 'nav.ai', } },
      { path: 'messages', component: MessengerPage, meta: { title: 'Сообщения', i18nTitle: 'nav.messages' } },
      { path: 'notifications', component: NotificationsPage, meta: { title: 'Уведомления', i18nTitle: 'nav.notifications' } },
      profileToSettings,
      page('settings', Settings, 'Настройки', 'nav.settings'),
    ],
  },

  // ПРЕПОДАВАТЕЛЬ ─────────────────────────────────────────
  {
    path: '/teacher', component: AppShell, meta: { requiresAuth: true, role: 'teacher' },
    children: [
      { path: '', component: TeacherJournal, meta: { title: 'Журнал преподавателя', i18nTitle: 'router.teacherJournalTitle', } },
      page('students', TeacherStudents, 'Студенты группы', 'router.groupStudents'),
      page('curator', CuratorView, 'Курирование', 'nav.curator'),
      // Куратор привязывает родителей к студентам СВОИХ групп (скоуп режет сервер).
      page('parents', AdminParents, 'Родители', 'nav.parents'),
      page('schedule', SchedulePage, 'Расписание', 'nav.schedule'),
      page('stats', TeacherStats, 'Статистика группы', 'router.groupStats'),
      page('courses', CoursesPage, 'Курсы', 'nav.courses'),
      { path: 'courses/:id', component: CourseDetailPage, meta: { title: 'Курс', i18nTitle: 'nav.courses' } },
      { path: 'vector', component: VectorPage, meta: { title: 'ИИ Помощник', i18nTitle: 'nav.ai', } },
      { path: 'messages', component: MessengerPage, meta: { title: 'Сообщения', i18nTitle: 'nav.messages' } },
      { path: 'notifications', component: NotificationsPage, meta: { title: 'Уведомления', i18nTitle: 'nav.notifications' } },
      profileToSettings,
      page('settings', Settings, 'Настройки', 'nav.settings'),
    ],
  },

  // АДМИНИСТРАТОР ─────────────────────────────────────────
  {
    path: '/admin', component: AppShell, meta: { requiresAuth: true, role: 'admin' },
    children: [
      { path: '', component: AdminDashboard, meta: { title: 'Панель администратора', i18nTitle: 'router.adminDashboardTitle' } },
      page('teachers', AdminTeachers, 'Преподаватели', 'nav.teachers'),
      page('students', AdminStudents, 'Студенты', 'nav.students'),
      page('parents', AdminParents, 'Родители', 'nav.parents'),
      page('registrations', AdminRegistrations, 'Заявки на регистрацию', 'nav.registrations'),
      page('groups', AdminGroups, 'Группы', 'nav.groups'),
      page('group-archive', AdminGroupArchive, 'Архив групп', 'nav.groupArchive'),
      page('subject-archive', AdminSubjectArchive, 'Архив предметов', 'nav.subjectArchive'),
      page('subjects', AdminSubjects, 'Предметы', 'nav.subjects'),
      page('courses', CoursesPage, 'Курсы', 'nav.courses'),
      { path: 'courses/:id', component: CourseDetailPage, meta: { title: 'Курс', i18nTitle: 'nav.courses' } },
      // Пояснение оставлено намеренно (решение Влада 25.08.2026): админ правит не своё
      // расписание, а НАЛОЖЕНИЕ поверх портала ВСГУТУ. По названию вкладки этого не
      // видно, и без строки человек ждёт полноценный редактор.
      noted(page('schedule', AdminSchedule, 'Расписание', 'nav.schedule'),
            'Правки поверх портала ВСГУТУ', 'router.scheduleSubtitle'),
      page('schedule-issues', AdminScheduleIssues, 'Накладки расписания', 'router.scheduleIssuesTitle'),
      { path: 'api', component: AdminAiSettings, meta: { title: 'Настройки ИИ-помощника «Вектор»', i18nTitle: 'router.aiSettingsTitle', } },
      // В программе на этой же странице появляется управление по SSH (список серверов,
      // команды, перенос). На сайте его нет: маршруты /desk/* подключает только
      // локальный сервер программы — см. шапку AdminServer.vue.
      page('server', AdminServer, 'Сервер', 'nav.server'),
      page('data', AdminData, 'Данные и резервные копии', 'router.dataTitle'),
      page('requests', AdminRequests, 'Запросы на подключение', 'nav.requests'),
      page('access', AdminSessions, 'Сессии и доступ', 'nav.sessions'),
      page('audit', AdminAudit, 'Журнал действий', 'nav.audit'),
      // Раньше отсутствовал: SidebarUserOverlay/HeaderBar ссылаются на `/${role}/profile`
      // безусловно для ВСЕХ ролей — у админа маршрута не было, и переход падал в
      // catch-all редирект на "/". Найдено при разведке под Discord-style профиль (3.6).
      profileToSettings,
      page('settings', Settings, 'Настройки', 'nav.settings'),
      { path: 'monitor', component: MonitorPage, meta: { title: 'Мониторинг', i18nTitle: 'nav.monitor', } },
      { path: 'vector', component: VectorPage, meta: { title: 'ИИ Помощник', i18nTitle: 'nav.ai', } },
      { path: 'messages', component: MessengerPage, meta: { title: 'Сообщения', i18nTitle: 'nav.messages' } },
      { path: 'notifications', component: NotificationsPage, meta: { title: 'Уведомления', i18nTitle: 'nav.notifications' } },
      page('moderation', AdminMessenger, 'Модерация чатов', 'nav.moderation'),
    ],
  },

  // МОДЕРАТОР ────────────────────────────────────────────
  // Отдельная ветка, а не «админ с урезанным меню»: страж ниже сверяет `meta.role` с
  // ролью человека, то есть чужие разделы недостижимы адресом, а не спрятаны. Разделов
  // ровно столько, сколько в NAV.moderator, — пункт меню без маршрута даёт 404 у
  // человека, пункт без меню даёт раздел, в который никто не войдёт (и то и другое у
  // нас уже случалось, см. RAILLESS_VIEWS).
  {
    path: '/moderator', component: AppShell, meta: { requiresAuth: true, role: 'moderator' },
    children: [
      { path: '', component: AdminMessenger, meta: { title: 'Модерация', i18nTitle: 'nav.moderation' } },
      { path: 'messages', component: MessengerPage, meta: { title: 'Сообщения', i18nTitle: 'nav.messages' } },
      { path: 'notifications', component: NotificationsPage, meta: { title: 'Уведомления', i18nTitle: 'nav.notifications' } },
      profileToSettings,
      page('settings', Settings, 'Настройки', 'nav.settings'),
    ],
  },

  // РОДИТЕЛЬ ─────────────────────────────────────────────
  // Пять страниц и ни одной больше. Guard по роли ниже не пустит его в чужую ветку, но
  // на защиту это не влияет: каждый серверный эндпоинт проверяет роль сам (инвариант §6).
  {
    path: '/parent', component: AppShell, meta: { requiresAuth: true, role: 'parent' },
    children: [
      { path: '', component: ParentJournal, meta: { title: 'Журнал', i18nTitle: 'nav.teacherJournal', } },
      page('courses', CoursesPage, 'Курсы', 'nav.courses'),
      { path: 'courses/:id', component: CourseDetailPage, meta: { title: 'Курс', i18nTitle: 'nav.courses' } },
      { path: 'vector', component: VectorPage, meta: { title: 'ИИ Помощник', i18nTitle: 'nav.ai', } },
      { path: 'messages', component: MessengerPage, meta: { title: 'Сообщения', i18nTitle: 'nav.messages' } },
      { path: 'notifications', component: NotificationsPage, meta: { title: 'Уведомления', i18nTitle: 'nav.notifications' } },
      profileToSettings,
      page('settings', Settings, 'Настройки', 'nav.settings'),
    ],
  },

  // ⚠️ «Не найдено» — НЕ универсальный ответ на любой промах. Правило такое:
  //   • чужая роль в адресе (студент набрал /admin/…) → сюда, 404;
  //   • своя роль, но такой страницы нет → молча на главную.
  // Это разные события: первое значит «мне сюда нельзя», второе — «я ошибся буквой»,
  // и раньше оба заканчивались дашбордом, из-за чего первое выглядело как сбой.
  // Решение принимает страж ниже; здесь только сама страница внутри оболочки.
  // ⚠️ БЕЗ AppShell: страница самостоятельная, как экран входа. Сюда попадают, только
  // если в адресе чужая роль, и показывать при этом чужое меню было бы странно вдвойне.
  { path: '/404', component: NotFoundPage, meta: { requiresAuth: true } },
  // Правила сообщества. ПУБЛИЧНЫЕ и по прямому адресу — требование дословное: «сделай её
  // как отдельную вкладку на сайте, чтобы можно было открыть как по кнопке, так и
  // изменением адреса в адресной строке». Без входа — потому что человек читает их ровно
  // тогда, когда собрался пожаловаться или уже получил ограничение, а второе состояние
  // легко совпадает с «не могу войти».
  { path: '/rules', component: RulesPage, meta: { public: true, title: 'Правила сообщества', i18nTitle: 'router.rulesTitle' } },
  { path: '/:pathMatch(.*)*', redirect: (to) => ({ path: '/404', query: { from: to.path } }) },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

// ━━ ПАСХАЛКА НА ЭКРАНЕ: СПРАШИВАЕМ, ПРЕЖДЕ ЧЕМ УЙТИ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Просьба Влада: находка редкая, а заметить её можно не сразу — человек уже потянулся
// к другой вкладке. Уйдёшь — пасхалка пропала, и второй раз она может не выпасть
// месяцами. Тот же приём, что у несохранённой формы, и по той же причине: цена
// случайного перехода несоразмерна цене одного вопроса.
//
// ⚠️ Спрашиваем ТОЛЬКО про то, что можно пропустить (`easter.pending`). Постоянные
// пасхалки — кольцо Detroit, состояние DOOM, счётчик ULTRAKILL — висят у студента
// всё время, и вопрос на каждом переходе сломал бы навигацию всему продукту.
//
// ⚠️ Выход из аккаунта не сторожим: на `/login` уводит logout, и «точно уйти?» поверх
// уже начатого выхода — это ловушка, а не забота. Плюс сама сцена Dark Souls играет
// ИМЕННО при выходе, то есть вопрос задавался бы всегда.
//
// ⚠️ На ДЕСКТОПЕ отдельного механизма не нужно и заводить его не надо: программа
// показывает ЭТОТ ЖЕ Vue-SPA в окне на движке Edge (§11), то есть страж работает там
// сам собой. А вот закрытие ОКНА роутер не видит — на этот случай ниже beforeunload.
async function confirmLeavingEasterEgg(to, from) {
  const easter = useEasterStore()
  if (to.path === from.path || to.path === '/login') return true
  // ⚠️ ЗАМОК ПРОВЕРЯЕМ ПЕРВЫМ и молча. Полноэкранная сцена только что возникла поверх
  // страницы — спрашивать «точно уйти?» в этот момент бессмысленно: человек ещё не успел
  // понять, что появилось, а диалог поверх сцены закрывает её же собой. Пара секунд
  // задержки объясняет себя сама, потому что на экране в это время идёт находка.
  if (easter.navLocked()) return false
  if (!easter.pending) return true
  const { confirm } = useConfirm()
  const ask = leaveAsk(easter.pending, easter.pendingOwned)
  const ok = await confirm({
    title: ask.title, message: ask.message, okText: ask.ok, cancelText: ask.cancel,
  })
  if (ok) easter.dismissPending()
  return ok
}

// Закрытие вкладки или окна программы роутер не перехватывает вовсе — только браузер.
// Диалог здесь системный и текст свой поставить нельзя (так устроено во всех браузерах
// с 2019 года); это осознанное ограничение, а не недоделка.
// Мост для ОБОЛОЧКИ ПРОГРАММЫ: закрытие окна — событие рабочего стола, до JS оно не
// доходит, и `beforeunload` там не срабатывает. Десктоп спрашивает эту функцию из
// `desktop/webview2_app.py::_may_close` перед тем, как закрыть окно.
// ⚠️ Возвращает СТРОКУ (id пасхалки) или '' — не объект: значение уезжает через
// evaluate_js, и чем проще тип, тем меньше поводов ему сломаться по дороге.
window.__gbEasterPending = () => {
  try { return useEasterStore().pending || '' } catch { return '' }
}

window.addEventListener('beforeunload', (e) => {
  let easter
  try { easter = useEasterStore() } catch { return }   // Pinia ещё не поднялась
  if (!easter.pending) return
  e.preventDefault()
  e.returnValue = ''
})

router.beforeEach(async (to, from) => {
  // Приложение без заданного адреса сервера (первый запуск) → сперва экран подключения.
  if (needsServer() && to.path !== '/connect') return '/connect'
  const auth = useAuthStore()
  if (to.meta.public) {
    if (to.path === '/login' && auth.isAuthenticated) return HOME_BY_ROLE[auth.role] || '/'
    return true
  }
  if (!auth.isAuthenticated) return { path: '/login' }
  //Страница существует, но принадлежит другой роли — это «нельзя», а не «нет такой».
  if (to.meta.role && to.meta.role !== auth.role) return { path: '/404', query: { from: to.path } }
  //А несуществующий адрес разбираем по первому сегменту: свой раздел — обычная опечатка,
  //возвращаем на главную; чужой — оставляем на 404, куда его уже направил catch-all.
  if (to.path === '/404') {
    if (decideMiss(String(to.query.from || ''), auth.role) === 'home')
      return HOME_BY_ROLE[auth.role] || '/'
  }
  //Вопрос про пасхалку задаём ПОСЛЕДНИМ: сначала пусть отработают все перенаправления
  //(нет сервера, не вошёл, чужая роль). Иначе человека спрашивали бы «точно уйти?»
  //перед переходом, который всё равно не состоится.
  if (!(await confirmLeavingEasterEgg(to, from))) return false
  return true
})
// Обрыв озвучки при уходе от Вектора живёт НЕ здесь: глушить на каждом переходе неверно
// (на странице с открытой шторкой Вектор ещё виден и должен договорить). Правило «звук
// играет, пока виден хоть один Вектор» — реактивно в AppShell.vue (watch vectorShown).
