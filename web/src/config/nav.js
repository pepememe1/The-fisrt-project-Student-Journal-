/**
 * nav.js — разделы боковой навигации по ролям. ЕДИНСТВЕННЫЙ источник меню на всех
 * платформах: сайт, мобилка и окно программы показывают одну и ту же SPA.
 *
 * ⚠️ Здесь стоял список «точные названия и секции десктопа» со ссылками на
 * ui/dashboards.py, ui/teacher_dashboard.py и ui/admin_dashboard.py — все три нативных
 * дашборда удалены вместе с Qt. Сверять названия больше не с чем и не нужно: разделы,
 * их порядок и секции задаются ЗДЕСЬ, и это не «порт» чего-то, а оригинал.
 *
 * Пункт-секция: { section: 'ЗАГОЛОВОК' }. Пункт-ссылка: { key, label, icon, to }.
 *
 * ⚠️ `phoneOnly: true` — пункт виден ТОЛЬКО на узком экране (класс `lg:hidden`).
 * Так помечены «Настройки»: на ПК кнопка переехала в карточку себя в левом нижнем углу
 * (как в Discord), и второй вход тем же именем в меню читался бы как два разных места.
 * На телефоне угловой карточки с шестерёнкой нет — там пункт меню остаётся единственной
 * дверью, поэтому убрать его совсем нельзя.
 *
 * ⚠️ «Профиль» из меню УБРАН НАСОВСЕМ (31.08.2026): он стал категорией внутри настроек.
 * Маршрут `/…/profile` намеренно ОСТАВЛЕН в роутере — на него ведут ссылки из чужих
 * карточек и старые закладки, и отдавать по ним 404 значило бы сломать работающее.
 */
import {
  Home, ClipboardList, ClipboardCheck, CalendarDays, BarChart3, Bot,
  BookOpen, Users, GraduationCap, Boxes, Library, Server,
  MonitorSmartphone, Activity, LayoutDashboard, UserPlus,
  AlertTriangle, MessagesSquare, ShieldAlert, ShieldCheck, UsersRound, Database,
  Archive, BookMarked, ScrollText, Cpu, KeyRound, Inbox, PackageOpen } from '@lucide/vue'

export const NAV = {
  student: [
    { section: 'Обучение', i18n: 'nav.sectionStudy' },
    { key: 'dash', label: 'Главная', i18n: 'nav.home', icon: Home, to: '/student' },
    { key: 'journal', label: 'Мой журнал', i18n: 'nav.journal', icon: ClipboardList, to: '/student/journal' },
    { key: 'schedule', label: 'Расписание', i18n: 'nav.schedule', icon: CalendarDays, to: '/student/schedule' },
    { key: 'stats', label: 'Статистика', i18n: 'nav.stats', icon: BarChart3, to: '/student/stats' },
    { key: 'courses', label: 'Курсы', i18n: 'nav.courses', icon: BookMarked, to: '/student/courses' },
    { key: 'ai', label: 'ИИ Помощник', i18n: 'nav.ai', icon: Bot, to: '/student/vector' },
    { key: 'messages', label: 'Сообщения', i18n: 'nav.messages', icon: MessagesSquare, to: '/student/messages', badge: 'messagesUnread' },
    { key: 'notifications', label: 'Уведомления', i18n: 'nav.notifications', icon: Inbox, to: '/student/notifications', badge: 'notifyUnread' },
  ],
  teacher: [
    { section: 'Преподавание', i18n: 'nav.sectionTeaching' },
    { key: 'journal', label: 'Журнал', i18n: 'nav.teacherJournal', icon: BookOpen, to: '/teacher' },
    { key: 'students', label: 'Студенты', i18n: 'nav.students', icon: Users, to: '/teacher/students' },
    { key: 'curator', label: 'Курирование', i18n: 'nav.curator', icon: ClipboardCheck, to: '/teacher/curator', curatorOnly: true },
    { key: 'parents', label: 'Родители', i18n: 'nav.parents', icon: UsersRound, to: '/teacher/parents', curatorOnly: true },
    { key: 'schedule', label: 'Расписание', i18n: 'nav.schedule', icon: CalendarDays, to: '/teacher/schedule' },
    { key: 'stats', label: 'Статистика', i18n: 'nav.stats', icon: BarChart3, to: '/teacher/stats' },
    { key: 'courses', label: 'Курсы', i18n: 'nav.courses', icon: BookMarked, to: '/teacher/courses' },
    { key: 'ai', label: 'ИИ Помощник', i18n: 'nav.ai', icon: Bot, to: '/teacher/vector' },
    { key: 'messages', label: 'Сообщения', i18n: 'nav.messages', icon: MessagesSquare, to: '/teacher/messages', badge: 'messagesUnread' },
    { key: 'notifications', label: 'Уведомления', i18n: 'nav.notifications', icon: Inbox, to: '/teacher/notifications', badge: 'notifyUnread' },
  ],
  // РОДИТЕЛЬ — ровно пять пунктов и ничего сверх. Ни списка студентов, ни статистики
  // группы, ни расписания преподавателей: это внешний человек, которому открыт доступ
  // к данным своего ребёнка, и всё лишнее здесь — расширение доступа к чужим ПДн.
  parent: [
    { section: 'Ребёнок', i18n: 'nav.section.child' },
    { key: 'journal', label: 'Журнал', i18n: 'nav.teacherJournal', icon: ClipboardList, to: '/parent' },
    { key: 'courses', label: 'Курсы', i18n: 'nav.courses', icon: BookMarked, to: '/parent/courses' },
    { key: 'ai', label: 'ИИ Помощник', i18n: 'nav.ai', icon: Bot, to: '/parent/vector' },
    { key: 'messages', label: 'Сообщения', i18n: 'nav.messages', icon: MessagesSquare, to: '/parent/messages', badge: 'messagesUnread' },
    { key: 'notifications', label: 'Уведомления', i18n: 'nav.notifications', icon: Inbox, to: '/parent/notifications', badge: 'notifyUnread' },
  ],
  // §живой отзыв: пункты «понапиханы что-куда» — было ОДНО «Система» на всё, что не
  // «Управление», хотя туда смешались три РАЗНЫХ вещи: инфраструктура/безопасность
  // (Сервер/Мониторинг/Данные/Запросы/Сессии), общение (ИИ Помощник/Сообщения/
  // Модерация — те же функции, что у student/teacher есть в СВОЁМ основном разделе, не
  // в «системном»), и личное (Настройки — та же страница темы/озвучки/безопасности,
  // что у остальных ролей в «Личном», а не системный конфиг). У student/teacher/parent
  // уже был «Личное» (Профиль+Настройки) — у admin не было вовсе, «Профиль» даже не
  // значился в этом списке (хотя маршрут /admin/profile существует, см. router/index.js
  // и запись 3.6 в CLAUDE.md про мини-карточку сайдбара). Четыре секции вместо двух —
  // не искусственное дробление, а тот же принцип «Личное» + по одной на КАЖДУЮ реально
  // разную область, а не всё не-«Управление» одной кучей.
  admin: [
    { section: 'Управление', i18n: 'nav.sectionManage' },
    { key: 'dash', label: 'Дашборд', i18n: 'nav.dashboard', icon: LayoutDashboard, to: '/admin' },
    { key: 'teachers', label: 'Преподаватели', i18n: 'nav.teachers', icon: GraduationCap, to: '/admin/teachers' },
    { key: 'students', label: 'Студенты', i18n: 'nav.students', icon: Users, to: '/admin/students' },
    { key: 'parents', label: 'Родители', i18n: 'nav.parents', icon: UsersRound, to: '/admin/parents' },
    { key: 'moderators', label: 'Модераторы', i18n: 'nav.moderators', icon: ShieldCheck, to: '/admin/moderators' },
    { key: 'registrations', label: 'Заявки на регистрацию', i18n: 'nav.registrations', icon: UserPlus, to: '/admin/registrations' },
    { key: 'groups', label: 'Группы', i18n: 'nav.groups', icon: Boxes, to: '/admin/groups' },
    { key: 'subjects', label: 'Предметы', i18n: 'nav.subjects', icon: Library, to: '/admin/subjects' },
    { key: 'courses', label: 'Курсы', i18n: 'nav.courses', icon: BookMarked, to: '/admin/courses' },
    // ⚠️ Иконка НЕ `Boxes` (она у «Групп») и не `Archive` (у архива предметов): три
    // соседних пункта с одинаковым значком читаются как один и тот же раздел — та же
    // претензия, что была к двум шестерёнкам у настроек и модерации.
    { key: 'groupArchive', label: 'Архив групп', i18n: 'nav.groupArchive', icon: PackageOpen, to: '/admin/group-archive' },
    { key: 'subjectArchive', label: 'Архив предметов', i18n: 'nav.subjectArchive', icon: Archive, to: '/admin/subject-archive' },
    { key: 'schedule', label: 'Расписание', i18n: 'nav.schedule', icon: CalendarDays, to: '/admin/schedule' },
    // badge: 'scheduleIssues' — Sidebar подставит число найденных накладок.
    { key: 'issues', label: 'Накладки расписания', icon: AlertTriangle,
      to: '/admin/schedule-issues', badge: 'scheduleIssues' },
    { section: 'Общение', i18n: 'nav.sectionCommunication' },
    { key: 'ai', label: 'ИИ Помощник', i18n: 'nav.ai', icon: Bot, to: '/admin/vector' },
    { key: 'messages', label: 'Сообщения', i18n: 'nav.messages', icon: MessagesSquare, to: '/admin/messages', badge: 'messagesUnread' },
    { key: 'notifications', label: 'Уведомления', i18n: 'nav.notifications', icon: Inbox, to: '/admin/notifications', badge: 'notifyUnread' },
    { key: 'moderation', label: 'Модерация чатов', i18n: 'nav.moderation', icon: ShieldAlert, to: '/admin/moderation' },
    { section: 'Система', i18n: 'nav.sectionSystem' },
    { key: 'api', label: 'Настройки ИИ', i18n: 'nav.aiSettings', icon: Cpu, to: '/admin/api' },
    { key: 'server', label: 'Сервер', i18n: 'nav.server', icon: Server, to: '/admin/server' },
    { key: 'mon', label: 'Мониторинг', i18n: 'nav.monitor', icon: Activity, to: '/admin/monitor' },
    { key: 'data', label: 'Данные и копии', i18n: 'nav.data', icon: Database, to: '/admin/data' },
    { key: 'requests', label: 'Запросы на подключение', i18n: 'nav.requests', icon: MonitorSmartphone, to: '/admin/requests' },
    { key: 'sessions', label: 'Сессии и доступ', i18n: 'nav.sessions', icon: KeyRound, to: '/admin/access' },
    // 🔥 Журнал ПИСАЛСЯ, а посмотреть его было негде: ручка существовала и работала,
    // а звать её было некому. Для продукта с ПДн аудит без доступа не решает задачу,
    // ради которой заведён, — разобрать «кто изменил оценку».
    { key: 'audit', label: 'Журнал действий', i18n: 'nav.audit', icon: ScrollText, to: '/admin/audit' },
  ],
  // МОДЕРАТОР — четыре пункта и ни одного больше.
  //
  // 🔒 Список короткий НЕ из скромности. Модератор заведён разбирать конфликты, и всё,
  // что ему показано, он видит по долгу службы — включая чужую переписку. Каждый лишний
  // пункт здесь это ещё один раздел с ПДн студентов, открытый человеку, которому он для
  // работы не нужен. Журнала, оценок, групп и расписания у него нет вовсе — и не потому,
  // что скрыты: серверные ручки этих разделов требуют своих ролей и отвечают 403.
  //
  // ⚠️ «Сообщения» оставлены намеренно: отвечать на обращение — его прямая работа, а
  // писать он должен из обычного мессенджера, а не из окна модерации. Иначе появился бы
  // второй композер со своим набором возможностей, и они разошлись бы.
  moderator: [
    { section: 'Модерация', i18n: 'nav.sectionModeration' },
    { key: 'moderation', label: 'Модерация', i18n: 'nav.moderation', icon: ShieldAlert, to: '/moderator', badge: 'moderationOpen' },
    { key: 'messages', label: 'Сообщения', i18n: 'nav.messages', icon: MessagesSquare, to: '/moderator/messages', badge: 'messagesUnread' },
    { key: 'notifications', label: 'Уведомления', i18n: 'nav.notifications', icon: Inbox, to: '/moderator/notifications', badge: 'notifyUnread' },
    { key: 'settings', label: 'Настройки', i18n: 'nav.settings', icon: Server, to: '/moderator/settings', phoneOnly: true },
  ],
}

export const HOME_BY_ROLE = { student: '/student', teacher: '/teacher', admin: '/admin',
  parent: '/parent', moderator: '/moderator' }
