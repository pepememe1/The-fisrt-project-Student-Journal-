/**
 * endpoints.js — типизированные обёртки над REST API.
 *
 * Здесь собран ВЕСЬ контракт, который использует веб-клиент. Часть эндпоинтов уже
 * есть на сервере (/auth/*, /me/*, /admin/*, /connect/*), часть — новые role-scoped
 * READ-представления под веб (/web/*), которые считаются на сервере (grading.py как
 * единый источник расчёта). Пока /web/* не реализованы — страницы показывают пустое
 * состояние (запрос вернёт 404), это ожидаемо на текущем этапе.
 *
 * Почему вебу нужны отдельные /web/*, а не /sync/pull: pull отдаёт ВСЕ строки всех
 * таблиц (включая password_hash и чужие оценки) — в браузер это выгружать нельзя.
 * /web/* возвращают только то, что роль вправе видеть, уже в готовом для UI виде.
 */
import { api, rawApi } from './client'
import { peekCached } from './offlineCache'

// АВТОРИЗАЦИЯ ──────────────────────────────────────────────────────────────────
export const authApi = {
  login: (login, password) => api.post('/auth/login', { login, password }),
  refresh: (refresh_token) => api.post('/auth/refresh', { refresh_token }),
  logout: () => api.post('/auth/logout'),
  // Самостоятельная регистрация студента (заявка админу) и восстановление пароля.
  register: (payload) => api.post('/auth/register', payload),
  recover: (email) => api.post('/auth/recover', { email }),
  // Регистрация по ссылке-приглашению куратора. Отличие от `register` выше: там заявка
  // ЖДЁТ одобрения администратора, здесь одобрением служит сама ссылка — аккаунт
  // создаётся сразу. Группу берёт сервер ИЗ ПРИГЛАШЕНИЯ, из тела её подставить нельзя.
  inviteInfo: (token) => api.get(`/auth/invite/${encodeURIComponent(token)}`),
  registerByInvite: (payload) => api.post('/auth/register-invite', payload),
  // Смена пароля по одноразовой ссылке из письма — ЕДИНСТВЕННОЕ место, где
  // пароль реально меняется (сам /auth/recover его больше не трогает).
  //`code` — второй фактор, если он у человека включён. Поле НЕОБЯЗАТЕЛЬНОЕ и
  //появляется на странице только после того, как сервер сам его потребовал
  //(401 + X-Gb-Reason: mfa_required): спрашивать код у того, кто его не заводил,
  //значит пугать человека на последней двери, за которой он и так оказался
  //потому, что не может войти.
  recoverConfirm: (token, password, code = '') =>
    api.post('/auth/recover/confirm', { token, password, code }),
  // Passkeys (вход по Face ID / отпечатку). begin выдаёт опции с challenge, complete
  // проверяет ответ устройства. register — под токеном (включить), login — публичный.
  // ── Второй фактор входа (TOTP) ──
  // ⚠️ `mfaVerify` — ВТОРОЙ шаг входа, и токена авторизации у него нет: пропуском
  // служит короткий challenge из ответа /auth/login. Остальные ручки требуют уже
  // открытой сессии (фактор заводят изнутри кабинета).
  mfaStatus: () => api.get('/auth/mfa/status'),
  mfaSetup: () => api.post('/auth/mfa/setup'),
  mfaConfirm: (code) => api.post('/auth/mfa/confirm', { code }),
  mfaDisable: (code) => api.post('/auth/mfa/disable', { code }),
  mfaRegenerate: (code) => api.post('/auth/mfa/recovery/regenerate', { code }),
  mfaVerify: (challenge, code) => api.post('/auth/mfa/verify', { challenge, code }),

  webauthnRegisterBegin: () => api.post('/auth/webauthn/register/begin'),
  webauthnRegisterComplete: (payload) => api.post('/auth/webauthn/register/complete', payload),
  webauthnLoginBegin: (login = '') => api.post('/auth/webauthn/login/begin', { login }),
  webauthnLoginComplete: (payload) => api.post('/auth/webauthn/login/complete', payload),
  webauthnList: () => api.get('/auth/webauthn/credentials'),
  webauthnDelete: (id) => api.post('/auth/webauthn/credentials/delete', { id }),
}

// ЛИЧНЫЕ НАСТРОЙКИ (тема и пр.) ──────────────────────────────────────────────────
// Пасхалки: бросок шанса и закрытие находки. Обе ручки СЕРВЕРНЫЕ по существу —
// клиент не решает ни выпала ли пасхалка, ни заслужена ли ачивка (см. easter_eggs.py).
export const easterApi = {
  roll: (body) => api.post('/web/easter-eggs/roll', body),
  // Что показать сразу после входа. Одним запросом, а не пятью бросками: условия
  // («ночь», «до этого были неудачи», «сегодня день рождения») считает сервер — в
  // браузере любое из них подделывается строкой в консоли.
  //`{ scene: 0 }` — бросок сцены уже был (F5), нужна только метка аватарки.
  onLogin: (params = {}) => api.get('/web/easter-eggs/on-login', { params }),
  journal: () => api.get('/web/easter-eggs/journal'),
  claim: (body) => api.post('/web/easter-eggs/claim', body),
}

export const meApi = {
  getPrefs: () => api.get('/me/prefs'),
  // Ачивки за пасхалки. Сервер отдаёт только идентификаторы — названия и значки
  // статика и живут в config/achievements.js, где переводятся вместе с интерфейсом.
  achievements: () => api.get('/web/achievements'),
  // Витрина: какие ачивки показывать другим в своём профиле. Приходит ПОЛНЫЙ список
  // отмеченных, а не разница, — так нельзя случайно оставить включённой старую.
  setShowcase: (ids) => api.post('/web/achievements/showcase', { ids }),
  setPrefs: (prefs) => api.post('/me/prefs', { prefs }),
  // Пуш-уведомления (только в мобильном приложении — в браузере токена нет).
  // Токен подтверждаем при КАЖДОМ запуске: сервер по нему обновляет владельца
  // устройства и метку «живо», иначе уборка выбросит рабочий телефон.
  registerPushToken: (token, platform = 'android') =>
    api.post('/me/push-token', { token, platform }),
  deletePushToken: (token) => api.delete('/me/push-token', { data: { token } }),
  // Куда открыть экран по нажатому уведомлению (детали НЕ приходят в самом пуше).
  getEvent: (id) => api.get(`/me/events/${id}`),
  // Без параметров сервер отдаёт ТОЛЬКО непрочитанные — так исторически ждёт
  // мобильное приложение, поэтому вызов оставлен как есть.
  unreadEvents: () => api.get('/me/events'),
  // Вкладка «Уведомления»: вся почта, включая прочитанное.
  events: (params = {}) => api.get('/me/events', { params: { filter: 'all', ...params } }),
  unreadCount: () => api.get('/me/events/unread-count'),
  markEventRead: (id) => api.post(`/me/events/${id}/read`),
  markAllEventsRead: () => api.post('/me/events/read-all'),
}

// ВЕБ-ПОДТВЕРЖДЕНИЕ УСТРОЙСТВА (для teacher/admin) ────────────────────────────────
// Тот же поток, что в десктопе: запрос → админ выдаёт 6-значный код → ввод кода.
export const connectApi = {
  request: (device_id, hostname) => api.post('/connect/request', { device_id, hostname }),
  status: (device_id) => api.get('/connect/status', { params: { device_id } }),
  verify: (device_id, code) => api.post('/connect/verify', { device_id, code }),
  // админ:
  list: () => api.get('/connect/requests'),
  approve: (device_id) => api.post('/connect/approve', { device_id }),
  reject: (device_id) => api.post('/connect/reject', { device_id }),
}

// СТУДЕНТ ────────────────────────────────────────────────────────────────────────
export const studentApi = {
  overview: () => api.get('/web/student/overview'),
  // ⚠️ АРХИВА ПРОШЛЫХ СЕМЕСТРОВ У СТУДЕНТА НЕТ (06.09.2026): сервер всегда отдаёт
  // ТЕКУЩИЙ период, что бы ни прислали. Причина арифметическая — средний балл, смешавший
  // два семестра, не описывает ни один из них. Прошлое смотрит администратор.
  // Параметры оставлены, чтобы старые ссылки не падали, но они ни на что не влияют.
  journal: () => api.get('/web/student/journal'),
  stats: () => api.get('/web/student/stats'),
  insights: () => api.get('/web/student/insights'),
  // ЗЕТ (docs/done/PLAN-ZET.md) — пусто (subjects: []), пока администратор не задал ни одного.
  zet: (params = {}) => api.get('/web/student/zet', { params }),
}

// ПРЕПОДАВАТЕЛЬ ───────────────────────────────────────────────────────────────────
export const teacherApi = {
  overview: () => api.get('/web/teacher/overview'),
  // params: { year, semester } — архив прошлого семестра; без них — текущий.
  journal: (group, subject, params = {}) => api.get('/web/teacher/journal', { params: { group, subject, ...params } }),
  students: (group) => api.get('/web/teacher/students', { params: { group } }),
  // Сводка «мои группы × мои предметы» ОДНИМ запросом (для панели на вкладке «ИИ
  // Помощник»): перебор stats() по каждой паре означал бы десяток запросов на открытие.
  summary: () => api.get('/web/teacher/summary'),
  // Диаграммы вкладки «ИИ Помощник» (3.6.1): общая категоризация + по предметам, и
  // drill-down по группам внутри одного предмета.
  subjectsOverview: () => api.get('/web/teacher/subjects-overview'),
  subjectsOverviewGroups: (subject) => api.get('/web/teacher/subjects-overview/groups', { params: { subject } }),
  stats: (group, subject, params = {}) => api.get('/web/teacher/stats', { params: { group, subject, ...params } }),
  insights: (group) => api.get('/web/teacher/insights', { params: { group } }),
  // Запись оценки (Phase B). Пустой grade = снять оценку. Сервер ставит метку LWW.
  // Разбор голосовой команды (только предложение — записывает setGrade ниже).
  voiceCommand: (text, group, subject) =>
    api.post('/web/vector/voice/command', { text, group, subject }),
  setGrade: (surname, name, lesson_id, grade) =>
    api.post('/web/teacher/grade', { surname, name, lesson_id, grade }),
  // Занятия/пары (Phase B): наполнение журнала. id = uuid на сервере.
  createLesson: (payload) => api.post('/web/teacher/lesson', payload),
  updateLesson: (id, payload) => api.put(`/web/teacher/lesson/${encodeURIComponent(id)}`, payload),
  deleteLesson: (id) => api.delete(`/web/teacher/lesson/${encodeURIComponent(id)}`),
  // Экспорт журнала (fmt=xlsx|docx) за выбранный семестр. Единый стиль TNR 14, ч/б.
  journalExport: (group, subject, params = {}) =>
    api.get('/web/teacher/journal-export', { params: { group, subject, ...params }, responseType: 'blob' }),
  // Итоговые оценки за семестр (промежуточная аттестация) + ведомость (fmt=xlsx|docx).
  setTermGrade: (payload) => api.post('/web/teacher/term-grade', payload),
  termGrades: (group, subject, params = {}) => api.get('/web/teacher/term-grades', { params: { group, subject, ...params } }),
  vedomost: (group, subject, params = {}) =>
    api.get('/web/teacher/vedomost', { params: { group, subject, ...params }, responseType: 'blob' }),
}

// УЧЕБНЫЙ ПЕРИОД (год/семестр) ────────────────────────────────────────────────────
export const termsApi = {
  list: () => api.get('/web/terms'),
}

// КУРАТОР (read-only по курируемым группам) ───────────────────────────────────────
export const curatorApi = {
  groups: () => api.get('/web/curator/groups'),
  // group/subject идут в QUERY, а не в путь: имена групп содержат слэш («К75/1»), и в
  // пути %2F ломает роутинг FastAPI (эндпоинт молча 404-ил → «нет предметов»).
  subjects: (group, params = {}) =>
    api.get('/web/curator/subjects', { params: { group, ...params } }),
  groupSubject: (group, subject, params = {}) =>
    api.get('/web/curator/group-subject', { params: { group, subject, ...params } }),
  // Сводный отчёт успеваемости. groups: 'all' | 'К74/1,К75/1'; fmt: 'xlsx' | 'docx'.
  // responseType blob — сервер отдаёт файл, а не JSON.
  report: (groups, fmt, params = {}) =>
    api.get('/web/curator/report', {
      params: { groups, fmt, ...params }, responseType: 'blob',
    }),
  // Таблица перевода на курс по ЗЕТ (docs/done/PLAN-ZET.md §7.4) — «главная фича» отчёта.
  zetReport: (group, params = {}) =>
    api.get('/web/curator/zet-report', { params: { group, ...params } }),
  // Риск отчисления по ВСЕЙ группе (3.6, dropout_risk.py). Сервер отдаёт только тех, у
  // кого риск реально есть, и уже отсортированными по убыванию — к кому идти первым.
  risk: (group) => api.get('/web/curator/risk', { params: { group } }),
  // Раздельное обучение (§ролей, 3.6.1): куратор ставит галочку на предмете — ПОСЛЕ
  // этого админ может занять второго преподавателя в редакторе часов группы.
  setSubjectSplit: (group, subject, split, params = {}) =>
    api.post('/web/curator/subject-split', { group, subject, split, ...params }),
  // Роспись студентов по подгруппам (1/2) этого предмета — вся группа, с уже
  // назначенными и ещё нет.
  subgroups: (group, subject, params = {}) =>
    api.get('/web/curator/subgroups', { params: { group, subject, ...params } }),
  // assignments — {student_id: 1|2|null}, null снимает распределение.
  saveSubgroups: (group, subject, assignments, params = {}) =>
    api.post('/web/curator/subgroups', { group, subject, assignments, ...params }),
}

// АДМИН ──────────────────────────────────────────────────────────────────────────
export const adminApi = {
  // 🔥 Журнал значимых действий. Ручка существовала и работала, а звать её было
  // некому — журнал писался, посмотреть его было НЕГДЕ. Для продукта с ПДн это не
  // косметика: аудит существует ради разбора «кто изменил оценку», и журнал без
  // доступа эту задачу не решает вовсе.
  audit: (params = {}) => api.get('/web/admin/audit', { params }),
  // Сходится ли цепочка целостности журнала. Отдельным вызовом, а не полем в `audit`:
  // проверка пересчитывает записи, а список читается постоянно — вешать пересчёт на
  // каждое открытие страницы значит нагружать одноядерный бой ради строки, которую
  // смотрят раз в месяц.
  auditIntegrity: (params = {}) => api.get('/web/admin/audit/integrity', { params }),
  overview: () => api.get('/web/admin/overview'),

  // ── Данные и резервные копии (AdminData.vue) ─────────────────────────────────────
  // Выгрузка = резервная копия: один формат на оба применения, чтобы скачанное
  // «на всякий случай» гарантированно принималось обратно.
  dataDatasets: () => api.get('/web/admin/data/datasets'),
  dataExport: (sets = '') =>
    api.get('/web/admin/data/export', { params: { sets }, responseType: 'blob' }),
  dataInspect: (file) => {
    //Отдельный шаг «что в файле» ДО записи: см. пояснение в AdminData.vue.
    const form = new FormData()
    form.append('file', file)
    return api.post('/web/admin/data/inspect', form)
  },
  dataImport: (file, sets) => {
    const form = new FormData()
    form.append('file', file)
    form.append('sets', sets)
    return api.post('/web/admin/data/import', form)
  },
  teachers: () => api.get('/web/admin/teachers'),
  students: (group = '') => api.get('/web/admin/students', { params: { group } }),
  groups: () => api.get('/web/admin/groups'),
  subjects: () => api.get('/web/admin/subjects'),
  // CRUD студентов (Phase B). id на сервере = stud:login (как в синке десктопа).
  createStudent: (payload) => api.post('/web/admin/students', payload),
  updateStudent: (login, payload) =>
    api.put(`/web/admin/students/${encodeURIComponent(login)}`, payload),
  deleteStudent: (login) =>
    api.delete(`/web/admin/students/${encodeURIComponent(login)}`),
  // CRUD групп и предметов (Phase B).
  createGroup: (payload) => api.post('/web/admin/groups', payload),
  updateGroup: (name, payload) => api.put(`/web/admin/groups/${encodeURIComponent(name)}`, payload),
  deleteGroup: (name) => api.delete(`/web/admin/groups/${encodeURIComponent(name)}`),
  // Привязать предметы из расписания ко ВСЕМ группам (+ пополнить каталог).
  bindSubjects: () => api.post('/web/admin/groups/bind-subjects'),
  // Кто ведёт предмет ПО РАСПИСАНИЮ портала — подсказки админу. Ручка ничего не пишет:
  // разбор ФИО в ячейке best-effort, и молчаливое назначение выдало бы чужой журнал.
  teacherSuggestions: (group) =>
    api.get('/web/admin/schedule/teacher-suggestions', { params: group ? { group } : {} }),
  // Применить ПОДТВЕРЖДЁННЫЕ назначения: [{hours_id, teacher_id}].
  applyTeachers: (entries) =>
    api.post('/web/admin/schedule/apply-teachers', { entries }),
  // Завести группу-каталожную запись из НЕколледжевой категории расписания
  // (Бакалавриат/Заочное 1/2) — имя сверяется с реальным списком портала.
  importScheduleCategory: (category, groupName) =>
    api.post('/web/admin/groups/import-schedule-category',
            { category, group_name: groupName }),
  // «Все» — массовый импорт ВСЕХ групп категории; снимок строится в фоне на сервере
  // (~минута на первый запрос) — {ok, building, imported, skipped, total}.
  importScheduleCategoryAll: (category) =>
    api.post('/web/admin/groups/import-schedule-category-all', { category }),
  // Учебные часы группы: план на семестр по каждому предмету + уже пройденное.
  groupHours: (group) => api.get('/web/admin/group-hours', { params: { group } }),
  // Архив предметов группы по семестрам — текущий термин показывает и активный план, и
  // то, что только что вытеснил реимпорт (active:false), прошлые — что велось тогда.
  groupSubjectArchive: (group) => api.get('/web/admin/group-subject-archive', { params: { group } }),
  // Архив ГРУПП (03.09.2026): выпустившиеся и не перешедшие на следующий курс.
  // ⚠️ Имя группы — параметром запроса и телом, НИКОГДА не сегментом пути: Starlette
  // раскодирует `%2F` до роутинга, и «К74/1» разваливает маршрут на лишний сегмент.
  groupArchive: () => api.get('/web/admin/group-archive'),
  groupArchiveDetail: (group) => api.get('/web/admin/group-archive/detail', { params: { group } }),
  setGroupArchived: (group, archived, reason = '') =>
    api.post('/web/admin/groups/archive', { group, archived, reason }),
  groupArchiveWitness: () => api.post('/web/admin/groups/archive-witness'),
  // teachers — §ролей: {предмет: teacher_id | ''} — назначение препода на (группа,предмет);
  // teachers2 — второй преподаватель раздельного обучения (§ролей, 3.6.1; принимается,
  // только если куратор уже поставил split на предмете, см. curatorApi.setSubjectSplit);
  // zet — docs/done/PLAN-ZET.md: {предмет: float | null}. Та же строка subject_hours, что и часы
  // (см. server/app/routers/web/admin_read.py::admin_set_group_hours).
  saveGroupHours: (group, hours, teachers = {}, teachers2 = {}, zet = {}) =>
    api.post('/web/admin/group-hours', { group, hours, teachers, teachers2, zet }),
  // Импорт специальности/учебного плана ВСГУТУ (parsers/esstu_parser.py на сервере):
  // список специальностей сайта колледжа + сам импорт часов/ЗЕТ текущего семестра в группу.
  esstuSpecialties: (group = '') => api.get('/web/admin/esstu/specialties', { params: { group } }),
  // Реально опубликованные на сайте годы набора для специальности — наполняет
  // выпадающий список «Год поступления» (вместо ручного ввода числа).
  esstuPlanYears: (specialtyCode) =>
    api.get('/web/admin/esstu/plan-years', { params: { specialty_code: specialtyCode } }),
  importEsstu: (group, specialtyCode, enrollmentYear) =>
    api.post('/web/admin/groups/import-esstu',
            { group, specialty_code: specialtyCode, enrollment_year: enrollmentYear }),
  createSubject: (name) => api.post('/web/admin/subjects', { name }),
  renameSubject: (name, newName) => api.put(`/web/admin/subjects/${encodeURIComponent(name)}`, { name: newName }),
  deleteSubject: (name) => api.delete(`/web/admin/subjects/${encodeURIComponent(name)}`),
  // Каталог предметов, отсортированный по категориям портала (колледж/бакалавриат/
  // заочное 1/2) — та же сортировка, что уже есть у Расписания/Групп/Студентов.
  // «- N п/г»-дубли уже сняты на сервере (schedule/model.py::strip_subgroup_tag).
  subjectsPortal: (category) => api.get('/web/admin/subjects/portal', { params: { category } }),
  // names — конкретный отбор (галочки), не передан — импортировать ВСЕ предметы категории.
  importSubjectsPortal: (category, names) =>
    api.post('/web/admin/subjects/import-portal', { category, names }),
  // CRUD преподавателей (Phase B). id на сервере = teach:login.
  createTeacher: (payload) => api.post('/web/admin/teachers', payload),
  updateTeacher: (login, payload) => api.put(`/web/admin/teachers/${encodeURIComponent(login)}`, payload),
  deleteTeacher: (login) => api.delete(`/web/admin/teachers/${encodeURIComponent(login)}`),

  // МОДЕРАТОРЫ. Дверь админская (`require_admin`): модератор не заводит модераторов и не
  // перевыдаёт пароли — иначе роль, созданная разбирать жалобы, сама выписывает себе
  // подкрепление. Сервер это и проверяет, клиент лишь не показывает пункт.
  // ⚠️ Пароль передаём ТОЛЬКО когда его меняют: пустое поле значит «не менять», а не
  // «стереть» (страница пароль не показывает и показать не может).
  moderators: () => api.get('/web/admin/moderators'),
  createModerator: (payload) => api.post('/web/admin/moderators', payload),
  updateModerator: (login, payload) =>
    api.put(`/web/admin/moderators/${encodeURIComponent(login)}`, payload),
  deleteModerator: (login) =>
    api.delete(`/web/admin/moderators/${encodeURIComponent(login)}`),
  // Перевод на курс (rollover): продвинуть текущий учебный период. Прошлые — в архив.
  rolloverTerm: (payload = {}) => api.post('/web/admin/term/rollover', payload),
  // Дата «ДД.ММ», после которой студент видит только итоговые оценки (пусто — выключено).
  gradesFreeze: () => api.get('/web/admin/term/grades-freeze'),
  setGradesFreeze: (date) => api.post('/web/admin/term/grades-freeze', { date }),
  // Заявки на регистрацию студентов.
  // Приглашения студентов ссылкой. Выдать может админ ЛЮБОЙ группе, преподаватель —
  // только своим курируемым (проверяет сервер, клиентскому списку он не верит).
  invites: (group = '') => api.get('/web/admin/invites', { params: group ? { group } : {} }),
  createInvite: (group, { days, maxUses, note } = {}) =>
    api.post('/web/admin/invites', { group, days, max_uses: maxUses, note }),
  revokeInvite: (token) =>
    api.post(`/web/admin/invites/${encodeURIComponent(token)}/revoke`),

  registrations: () => api.get('/web/admin/registrations'),
  approveRegistration: (id) => api.post('/web/admin/registrations/approve', { id }),
  rejectRegistration: (id, note = '') => api.post('/web/admin/registrations/reject', { id, note }),
  // Настройки ИИ «Вектор» (провайдер + ключ GigaChat/модель Ollama). Хранятся в той же
  // строке config, что и на десктопе → синхронизируются в обе стороны.
  aiConfig: () => api.get('/web/admin/ai-config'),
  aiConfigSave: (payload) => api.post('/web/admin/ai-config', payload),
  aiConfigTest: (payload) => api.post('/web/admin/ai-config/test', payload),
  // Редактор расписания (правки ПОВЕРХ портала). schedule — слитое расписание + правки;
  // set — задать/заменить/скрыть пару в ячейке; del — убрать правку (вернуться к порталу).
  //category необязателен — сервер сам возьмёт Group.category из базы, если не передать
  //(см. admin_schedule_get); передаём явно, когда он уже известен клиенту (AdminSchedule.vue).
  schedule: (group, category) => api.get('/web/admin/schedule', { params: { group, category } }),
  setScheduleOverride: (payload) => api.post('/web/admin/schedule/override', payload),
  deleteScheduleOverride: (id) => api.delete(`/web/admin/schedule/override/${encodeURIComponent(id)}`),
  // Пачечное сохранение черновика редактора (переносы/правки) — одной транзакцией.
  saveScheduleOverrides: (overrides) => api.post('/web/admin/schedule/overrides', { overrides }),
  // «Взять с ВСГУТУ» — форс-обновление кэша портала (для группы или для всех — в фоне).
  refreshSchedule: (group = '', all = false) =>
    api.post('/web/admin/schedule/refresh', null, { params: all ? { all: 1 } : { group } }),
  // Сброс правок: снова берётся портал. all=1 стирает ВСЕ правки колледжа (подтверждать!).
  resetSchedule: (group = '', all = false) =>
    api.post('/web/admin/schedule/reset', null, { params: all ? { all: 1 } : { group } }),
  // Накладки конкретного слота при переносе пары (подсветка сразу после drag-drop).
  slotConflicts: (params) => api.get('/web/admin/schedule/slot-conflicts', { params }),
  // Сверка расписаний: накладки (один преподаватель/аудитория в одном слоте у разных
  // групп). ⚠️ Ответ с building=true означает «снимок портала ещё собирается», а НЕ
  // «накладок нет» — показывать эти состояния надо по-разному.
  scheduleConflicts: () => api.get('/web/admin/schedule/conflicts'),
  // Пометить слот совместной парой (законное совпадение) / снять пометку.
  setScheduleJoint: (payload) => api.post('/web/admin/schedule/joint', payload),
  deleteScheduleJoint: (id) => api.delete(`/web/admin/schedule/joint/${encodeURIComponent(id)}`),
  // Разослать уведомление об изменении расписания группы (явная кнопка, а не автомат
  // на каждую правку ячейки — иначе студент получит десятки уведомлений подряд).
  publishSchedule: (group) => api.post('/web/admin/schedule/publish', { group }),
  // Инфо-панель «Сервер и сайт» (адрес, БД, шифрование, ГОСТ, онлайн, период).
  serverInfo: () => api.get('/web/admin/server-info'),
  // Состояние МАШИНЫ, на которой работает сервер: диск, память, аптайм, размер базы.
  // Только просмотр — и на сайте, и в программе (см. server/app/routers/serverinfo.py).
  serverMetrics: () => api.get('/web/admin/server/metrics'),
  serverBackups: () => api.get('/web/admin/server/backups'),
  serverTree: (path = '') => api.get('/web/admin/server/tree', { params: { path } }),
  // Порог ЗЕТ для перевода группы на курс (docs/done/PLAN-ZET.md §7.2). min_zet: null — снять.
  zetThreshold: (group, params = {}) =>
    api.get('/web/admin/zet-thresholds', { params: { group, ...params } }),
  setZetThreshold: (payload) => api.post('/web/admin/zet-thresholds', payload),
  // Перевод выбранных студентов на следующий курс (только eligible, сервер перепроверяет).
  promoteGroup: (payload) => api.post('/web/admin/groups/promote', payload),
  // Служебное — уже реализовано на сервере:
  online: () => api.get('/admin/online'),
  events: (since = 0) => api.get('/admin/events', { params: { since } }),
  sessions: (active = true) => api.get('/admin/sessions', { params: { active } }),
  revoke: (payload) => api.post('/admin/sessions/revoke', payload),
}

// РАСПИСАНИЕ (сервер отдаёт снимок с portal.esstu.ru; ПДн не участвуют) ───────────
// category — один из 4 разделов портала (schedule/parser.py::CATEGORIES); без
// параметра — «колледж» (DEFAULT_CATEGORY), 100% прежнее поведение.
/**
 * Расписание БЕЗ входа в аккаунт (`/public/schedule*`).
 *
 * 🔥 ЗАЧЕМ (01.09.2026, прямая просьба Ярослава): «из акка вылетело, надо быстро чекнуть
 * расписание, а его нет — бесит». Так и было: истёк токен → клиент чистит сессию и
 * выбрасывает на вход, вместе с кабинетом исчезает и расписание. А расписание —
 * ОБЩЕДОСТУПНАЯ информация: оно берётся с публичного портала ВСГУТУ, который отдаёт те
 * же страницы кому угодно (см. server/app/routers/publicschedule.py, там же объяснено,
 * почему это не дыра). Требовать токен там, где он ничего не защищает, — значит забирать
 * у человека то, что и так открыто.
 *
 * ⚠️ Ходим ГОЛЫМ axios, мимо общего клиента: у того на 401 стоит обновление токена и
 * выброс на экран входа, а здесь никакого токена нет и быть не должно. Пропусти мы
 * запрос через общий клиент — неудача публичного расписания выкидывала бы из аккаунта
 * человека, который в нём и не был.
 */
export const publicScheduleApi = {
  group: (group, category = '') =>
    rawApi.get('/public/schedule', { params: { group, category } }),
  //Категории и список групп — теми же путями и в той же форме, что в кабинете
  //(`scheduleApi.categories/groups`), только без токена. Страница разбирает оба ответа
  //ОДНИМ кодом; второй формат разошёлся бы с первым на первой же правке реестра.
  categories: () => rawApi.get('/public/schedule/categories'),
  groups: (category = '') =>
    rawApi.get('/public/schedule/groups', { params: { category } }),
  teacher: (name, category = '') =>
    rawApi.get('/public/schedule/teacher', { params: { name, category } }),
  week: () => rawApi.get('/public/week'),
}

// ⚠️ ПАРАМЕТРЫ ЗАПРОСА СОБИРАЮТСЯ ОДИН РАЗ (07.09.2026). У расписания появилась вторая
// дверь — «покажи сохранённую копию, не дожидаясь сети» (peekCached), а ключ кэша
// строится ИЗ ТЕХ ЖЕ пути и параметров (см. offlineCache.reqKey). Собери параметры в двух
// местах — и они разойдутся молча: копия просто перестанет находиться, экран вернётся к
// спиннеру, и никакой ошибки при этом не будет.
const scheduleGetParams = (group, category = '') => ({ group, category })
const scheduleTeacherParams = (name = '', category = '') => ({ name, category })

// 🔥 А ВОТ ПУТЬ ОСТАЁТСЯ ЛИТЕРАЛОМ В КАЖДОМ ВЫЗОВЕ, И ЭТО НЕ НЕДОСМОТР. Первая редакция
// вынесла его в константу «чтобы наверняка» — и ослепила мост HTTP-контракта
// (`tools/graph_api_bridge.py`): он ищет `api.get('…')` с ЛИТЕРАЛОМ, поэтому после
// правки `/web/schedule` и `/web/schedule/teacher` стали числиться «невостребованными
// роутами». Карта, считающая живой роут мёртвым, опаснее дублирующейся строки: по ней
// его однажды удалят. Совпадение путей держит `web/tests/scheduleInstant.test.mjs`.
export const scheduleApi = {
  categories: () => api.get('/web/schedule/categories'),
  get: (group, category = '') =>
    api.get('/web/schedule', { params: scheduleGetParams(group, category) }),
  groups: (category = '') => api.get('/web/schedule/groups', { params: { category } }),
  // Расписание преподавателя (пункт 2): без name сервер матчит ФИО текущего юзера.
  teacher: (name = '', category = '') =>
    api.get('/web/schedule/teacher', { params: scheduleTeacherParams(name, category) }),

  /** Копия прошлого ответа БЕЗ похода в сеть: `{data, at}` или null. */
  cachedGet: (group, category = '') =>
    peekCached('/web/schedule', scheduleGetParams(group, category)),
  cachedTeacher: (name = '', category = '') =>
    peekCached('/web/schedule/teacher', scheduleTeacherParams(name, category)),
  // Выгрузка расписания группы файлом (fmt: xlsx|docx). Строится из ТОГО ЖЕ слитого
  // расписания (портал + правки админа), что показано на сайте.
  exportFile: (group, fmt = 'xlsx', category = '') =>
    api.get('/web/schedule/export', { params: { group, fmt, category }, responseType: 'blob' }),
}

// РОДИТЕЛЬ — самый ограниченный кабинет: журнал своих детей и Вектор по ним же.
// Доступ открывает не привязка сотрудника, а подтверждение самого студента, поэтому
// journal/vector отвечают 403, пока согласия нет (см. server/app/routers/parent.py).
export const parentApi = {
  children: () => api.get('/web/parent/children'),
  journal: (studentId = '', year = '', semester = 0) =>
    api.get('/web/parent/journal', { params: { student_id: studentId, year, semester } }),
  vectorAsk: (message, studentId = '') =>
    api.post('/web/parent/vector/ask', { message, student_id: studentId }),
  // ЗЕТ ребёнка (docs/done/PLAN-ZET.md §7.6) — та же строка, что у студента, без таблицы
  // группы и порога перевода (это дело куратора/администрации).
  zet: (studentId = '', year = '', semester = 0) =>
    api.get('/web/parent/zet', { params: { student_id: studentId, year, semester } }),
  // Формат ОДИН В ОДИН со studentApi.stats — сводка успеваемости у родителя и студента
  // рисуется одной и той же карточкой (см. GradesOverview.vue).
  stats: (studentId = '', year = '', semester = 0) =>
    api.get('/web/parent/stats', { params: { student_id: studentId, year, semester } }),
}

// Согласие студента на доступ родителя к его журналу (152-ФЗ) — в кабинете студента.
export const consentApi = {
  links: () => api.get('/web/student/parent-links'),
  decide: (linkId, approve) =>
    api.post(`/web/student/parent-links/${encodeURIComponent(linkId)}/decide`, { approve }),
}

// Привязка родителей сотрудником (админ — любые группы, куратор — только свои).
export const staffParentApi = {
  parents: () => api.get('/web/staff/parents'),
  links: (group = '') => api.get('/web/staff/parent-links', { params: { group } }),
  link: (parentId, studentId) =>
    api.post('/web/staff/parent-links', { parent_id: parentId, student_id: studentId }),
  unlink: (linkId) => api.delete(`/web/staff/parent-links/${encodeURIComponent(linkId)}`),
  create: (payload) => api.post('/web/admin/parents', payload),
  //Логин не редактируется (сервер это и не поддерживает — см. admin_update_parent).
  update: (id, payload) => api.put(`/web/admin/parents/${encodeURIComponent(id)}`, payload),
  remove: (id) => api.delete(`/web/admin/parents/${encodeURIComponent(id)}`),
}

// «ВЕКТОР» — серверный анти-галлюцинационный конвейер (intents→SQL→анонимизация→LLM)
export const vectorApi = {
  ask: (message) => api.post('/web/vector/ask', { message }),
  // Озвучка (TTS): текст ответа → WAV-аудио. Голос — male|female из настроек.
  // arraybuffer — чтобы декодировать через Web Audio API (надёжнее автоплея <audio>).
  ttsStatus: () => api.get('/web/vector/tts/status'),
  tts: (text, voice = 'male') =>
    api.post('/web/vector/tts', { text, voice }, { responseType: 'arraybuffer' }),
  // Распознавание речи (STT). Чем распознавать — решает СЕРВЕР (`engine`: whisper |
  // browser), а не клиент: иначе десктоп и сайт разъехались бы в поведении, а
  // переключатель админа «включить для всех» стал бы невозможен.
  // ⚠️ Раньше эти два вызова шли мимо контракта, прямо из utils/voiceInput.js. Из-за
  // этого их не видела ни карта связей репозитория, ни человек, читающий этот файл, —
  // хотя докстринг файла обещает, что здесь собран ВЕСЬ контракт с сервером.
  sttStatus: () => api.get('/web/vector/stt/status'),
  stt: (form) => api.post('/web/vector/stt', form),
}

// МЕССЕНДЖЕР (см. docs/done/MESSENGER-PLAN.md) ─────────────────────────────────────────
// Отдельная онлайн-подсистема (НЕ через /sync). Фаза 1/2: личные чаты + каталог людей.
export const messengerApi = {
  // Перевод сообщений. ⚠️ Переводит ЛОКАЛЬНЫЙ Argos на самом сервере, а НЕ ИИ-модель
  // «Вектора» и не Google (см. server/app/translate_service.py). Здесь стояло «через ту
  // же ИИ-модель, что настроена для Вектора» — с 29.08.2026 это неправда, а по такой
  // строке легко решить, что личная переписка едет в облако вместе с промптом.
  // Эндпоинт не привязан к id сообщения: им пользуется и кнопка на чужой реплике, и
  // предпросмотр собственного текста до отправки.
  translate: (text, to, from = 'auto') =>
    api.post('/web/messenger/translate', { text, to, from }),
  translateLanguages: () => api.get('/web/messenger/translate/languages'),
  // Доступен ли переводчик на этом сервере. Отдельно от списка языков: список
  // клиент кэширует надолго, а доступность меняется установкой пакета на машине.
  translateStatus: () => api.get('/web/messenger/translate/status'),
  // GIF-пикер (Klipy) — сервер проксирует запросы, ключ не покидает бэкенд.
  gifCategories: () => api.get('/web/messenger/gifs/categories'),
  gifTrending: (page = 1) => api.get('/web/messenger/gifs/trending', { params: { page } }),
  gifSearch: (q, page = 1) => api.get('/web/messenger/gifs/search', { params: { q, page } }),
  // Каталог/поиск людей для выбора собеседника (role: student|teacher).
  users: (role = 'student', q = '', page = 0) =>
    api.get('/web/messenger/users', { params: { role, q, page } }),
  profile: (id) => api.get(`/web/messenger/users/${encodeURIComponent(id)}/profile`),
  // Личная заметка о человеке (Discord-style «Notes») — видна только автору.
  note: (id) => api.get(`/web/messenger/users/${encodeURIComponent(id)}/note`),
  saveNote: (id, text) => api.post(`/web/messenger/users/${encodeURIComponent(id)}/note`, { text }),
  shared: (id) => api.get(`/web/messenger/users/${encodeURIComponent(id)}/shared`),
  // Чаты.
  chats: () => api.get('/web/messenger/chats'),
  openDirect: (userId) => api.post(`/web/messenger/chats/direct/${encodeURIComponent(userId)}`),
  // Сообщения: history (before=<id>) / poll (after=<id>) / последние (без параметров).
  messages: (convId, params = {}) =>
    api.get(`/web/messenger/chats/${encodeURIComponent(convId)}/messages`, { params }),
  // clientNonce (§D10): UUID клиента — повтор с тем же nonce не создаёт дубль на сервере
  // (защита от ретраев при обрыве сети/двойном клике).
  // extra — доп. поля payload (kind/gif_slug для GIF-сообщений, см. GifPicker.vue),
  // необязательные и не влияют на обычную отправку текста.
  send: (convId, body, replyToId = 0, clientNonce = '', extra = {}) =>
    api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/messages`,
      { body, reply_to_id: replyToId, client_nonce: clientNonce, ...extra }),
  read: (convId, lastMessageId = 0) =>
    api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/read`,
      { last_message_id: lastMessageId }),
  // Мьют беседы у себя (без пушей по ней).
  muteChat: (convId, muted = true) =>
    api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/mute`, { muted }),
  // Удалить переписку у себя (clearOnly — только очистить историю, чат остаётся).
  deleteChat: (convId, clearOnly = false) =>
    api.delete(`/web/messenger/chats/${encodeURIComponent(convId)}`,
      { params: { clear_only: clearOnly } }),
  // Действия над сообщением (Фаза 3).
  edit: (id, body) => api.patch(`/web/messenger/messages/${id}`, { body }),
  deleteMessage: (id, scope = 'self') =>
    api.delete(`/web/messenger/messages/${id}`, { params: { scope } }),
  pin: (id) => api.post(`/web/messenger/messages/${id}/pin`),
  unpin: (id) => api.delete(`/web/messenger/messages/${id}/pin`),
  pinned: (convId) => api.get(`/web/messenger/chats/${encodeURIComponent(convId)}/pinned`),
  forward: (messageIds, toConversationIds) =>
    api.post('/web/messenger/messages/forward',
      { message_ids: messageIds, to_conversation_ids: toConversationIds }),
  // Реакции-эмодзи (§D3) и история редактирования (§D11).
  addReaction: (mid, emoji) => api.post(`/web/messenger/messages/${mid}/reactions`, { emoji }),
  removeReaction: (mid, emoji) =>
    api.delete(`/web/messenger/messages/${mid}/reactions/${encodeURIComponent(emoji)}`),
  reactionUsers: (mid) => api.get(`/web/messenger/messages/${mid}/reactions`),
  messageHistory: (mid) => api.get(`/web/messenger/messages/${mid}/history`),
  report: (messageId, reasonCode, description = '') =>
    api.post('/web/messenger/reports',
      { message_id: messageId, reason_code: reasonCode, description }),
  // Чат с модерацией (кнопка ⚙).
  moderation: () => api.get('/web/messenger/moderation'),
  // Темы обращения для автоответчика + СОСТОЯНИЕ моего обращения (`current`). Состояние
  // приходит с сервера, а не хранится во вкладке: обращение у человека одно, а заходит
  // он и с телефона, и с компьютера.
  supportCategories: () => api.get('/web/messenger/moderation/categories'),
  pickSupportCategory: (code) => api.post('/web/messenger/moderation/category', { code }),
  // Жалоба на ПРОФИЛЬ (не на сообщение) — отдельная очередь у модерации. Снимок полей
  // делает сервер: присланный клиентом снимок это текст, который пишет жалующийся.
  reportUser: (userId, reasonCode, description = '', field = 'profile') =>
    api.post('/web/messenger/user-reports',
      { user_id: userId, reason_code: reasonCode, description, field }),
  // Блокировка человек↔человек. Односторонняя и МОЛЧАЛИВАЯ: заблокированному не сообщают
  // (иначе это способ объявить «ты мне неприятен», а не защита) — см. safety.py.
  blocks: () => api.get('/web/messenger/blocks'),
  blockUser: (uid, blocked = true) =>
    api.post(`/web/messenger/users/${encodeURIComponent(uid)}/block`, { blocked }),
  // Моё ограничение: до каких пор и за что. Без него человек узнавал о мьюте только по
  // отказу при отправке — то есть написав сообщение и потеряв его.
  myRestriction: () => api.get('/web/messenger/my-restriction'),
  // Пометить беседу непрочитанной (личное состояние, на собеседника не влияет).
  markUnread: (convId) =>
    api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/unread`),
  // Группы и каналы (Фазы 5–6).
  // classGroups (§12, режим куратора) — названия учебных групп, чьи студенты добавятся
  // автоматически; сервер сам ограничит их курируемыми группами звонящего.
  createGroup: (title, memberIds = [], about = '', classGroups = []) =>
    api.post('/web/messenger/chats/group',
      { title, member_ids: memberIds, about, class_groups: classGroups }),
  createChannel: (title, writerIds = [], isPublic = true, about = '') =>
    api.post('/web/messenger/chats/channel', { title, writer_ids: writerIds, is_public: isPublic, about }),
  channels: (q = '') => api.get('/web/messenger/channels', { params: { q } }),
  join: (convId) => api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/join`),
  leave: (convId) => api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/leave`),
  convInfo: (convId) => api.get(`/web/messenger/chats/${encodeURIComponent(convId)}`),
  // §D6: переименовать группу/канал (owner/admin) — пишет системное сообщение.
  renameChat: (convId, title, about) =>
    api.patch(`/web/messenger/chats/${encodeURIComponent(convId)}`, { title, about }),
  // Аватарка группы/канала (02.09.2026, просьба Влада). ТА ЖЕ ручка, что переименование:
  // сервер смотрит на ПРИСУТСТВИЕ ключа, поэтому здесь шлём ТОЛЬКО avatar — иначе запрос
  // «поменять картинку» затирал бы название и описание пустотой.
  // Пустая строка — убрать аватарку (беседа снова рисуется буквой на плашке).
  setChatAvatar: (convId, avatar) =>
    api.patch(`/web/messenger/chats/${encodeURIComponent(convId)}`, { avatar }),
  // §ролей: выгнать участника (право kick), назначить билдовую/кастомную роль, кастомные
  // роли беседы, личный игнор (не модерация — см. модель ConversationIgnore).
  // 🔥 ДОБАВЛЕНИЕ УЧАСТНИКОВ. Ручка на сервере существует и работает с самого начала
  // (`POST /chats/{id}/members`, messenger/chats.py::add_members), а вызывающего у неё
  // не было НИ ОДНОГО — то есть добавить человека в беседу через продукт было нельзя
  // вовсе, только правкой базы руками. Влад сообщил это как «в беседы невозможно
  // добавить новых людей»; нашлось сверкой `tools/graph_api_bridge.py`, где эта ручка
  // всё время лежала в списке «сервер никто не зовёт».
  // ── Вложения и вкладки панели беседы (25.08.2026) ──────────────────────────
  // ⚠️ Файл НЕ проходит через наш сервер: `signUpload` отдаёт подписанную ссылку,
  // браузер кладёт файл ПРЯМО в хранилище, потом `confirmUpload`. Разбор, почему иначе
  // нельзя, — docs/done/MESSENGER-ATTACHMENTS-PLAN.md и server/app/storage.py.
  uploadLimits: () => api.get('/web/messenger/uploads/limits'),
  signUpload: (convId, { name, size, mime }) =>
    api.post('/web/messenger/uploads/sign', { conversation_id: convId, name, size, mime }),
  confirmUpload: (attId) =>
    api.post(`/web/messenger/uploads/${encodeURIComponent(attId)}/done`),
  attachmentUrl: (attId) =>
    api.get(`/web/messenger/attachments/${encodeURIComponent(attId)}/url`),
  chatFiles: (convId) =>
    api.get(`/web/messenger/chats/${encodeURIComponent(convId)}/files`),
  chatMedia: (convId) =>
    api.get(`/web/messenger/chats/${encodeURIComponent(convId)}/media`),
  chatSaved: (convId) =>
    api.get(`/web/messenger/chats/${encodeURIComponent(convId)}/saved`),
  // Личный вопрос Вектору из ЛЮБОЙ беседы. Ответ приходит СЮДА и нигде не сохраняется:
  // в общем чате реплика Вектора показала бы соседям выборку, скоупленную по спросившему.
  // В «Избранном» команда работает по-старому — обычным сообщением, там история нужна.
  askVectorInChat: (convId, question) =>
    api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/vector`, { question }),

  // Заявки: студент позвал преподавателя в свою группу. Пока заявка не принята, участника
  // НЕТ вовсе — это отдельная таблица, а не флаг на участнике (см. ConversationInvite).
  invites: () => api.get('/web/messenger/invites'),
  acceptInvite: (convId) =>
    api.post(`/web/messenger/invites/${encodeURIComponent(convId)}/accept`),
  declineInvite: (convId) =>
    api.post(`/web/messenger/invites/${encodeURIComponent(convId)}/decline`),

  addMembers: (convId, userIds, classGroups) =>
    api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/members`,
      { user_ids: userIds, class_groups: classGroups || [] }),
  removeMember: (convId, userId) =>
    api.delete(`/web/messenger/chats/${encodeURIComponent(convId)}/members/${encodeURIComponent(userId)}`),
  setMemberRole: (convId, userId, { role, customRoleId } = {}) =>
    api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/members/${encodeURIComponent(userId)}/role`,
      customRoleId ? { custom_role_id: customRoleId } : { role }),
  roles: (convId) => api.get(`/web/messenger/chats/${encodeURIComponent(convId)}/roles`),
  createRole: (convId, name, permissions) =>
    api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/roles`, { name, permissions }),
  updateRole: (convId, roleId, payload) =>
    api.put(`/web/messenger/chats/${encodeURIComponent(convId)}/roles/${encodeURIComponent(roleId)}`, payload),
  deleteRole: (convId, roleId) =>
    api.delete(`/web/messenger/chats/${encodeURIComponent(convId)}/roles/${encodeURIComponent(roleId)}`),
  ignoreMember: (convId, userId) =>
    api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/ignore/${encodeURIComponent(userId)}`),
  unignoreMember: (convId, userId) =>
    api.delete(`/web/messenger/chats/${encodeURIComponent(convId)}/ignore/${encodeURIComponent(userId)}`),
  // §D7: статус (поверх presence) — dnd/studying/away + текст (только у преподавателя).
  getStatus: () => api.get('/web/messenger/status'),
  setStatus: (kind, customText = '') =>
    api.post('/web/messenger/status', { kind, custom_text: customText }),
  // Группы, которые сотрудник может адресовать (объявления/отчёты) — для выпадающего
  // списка вместо ручного ввода.
  myGroups: () => api.get('/web/messenger/my-groups'),
  // §D12: открыть/создать системный канал «Объявления · Группа» (teacher/admin), дальше
  // публикация — обычным send() в этот же чат (это просто kind='channel').
  // ⚠️ Группа идёт в QUERY, а не в путь: имена вида «К75/1» в пути превращались в %2F,
  // Starlette декодировал его обратно в «/», роут не совпадал и запрос молча 404-ил.
  ensureAnnouncementsChannel: (groupName) =>
    api.post('/web/messenger/channels/announcements', null, { params: { group: groupName } }),
  // 🔥 Канал практики. Ручка была с самого начала и не имела ни одного вызывающего —
  // то есть создать его из веба было нельзя вовсе (нашлось сверкой graph_api_bridge).
  // ⚠️ Имя группы в QUERY, а не в пути: Starlette раскодирует `%2F` до роутинга, и
  // группа «К74/1» развалила бы путь на лишний сегмент.
  ensurePracticeChannel: (groupName) =>
    api.post('/web/messenger/channels/practice', null, { params: { group: groupName } }),
  // §12: канал «Отчёты · Группа» (куратор). Группа — тоже в query, причина та же.
  ensureCuratorReportsChannel: (groupName) =>
    api.post('/web/messenger/channels/curator-reports', null, { params: { group: groupName } }),
  // §12: кому предложить отчёт (родители с подтверждённой связью + подходящие беседы).
  reportRecipients: (group) =>
    api.get('/web/messenger/report-recipients', { params: { group } }),
  // §12: создать отчёт и отправить его сообщением. Без адресатов — себе в «Избранное».
  createReport: (group, toUserIds = [], toConversationIds = []) =>
    api.post('/web/messenger/curator-reports',
      { group, to_user_ids: toUserIds, to_conversation_ids: toConversationIds }),
  // Организация списка чатов (docs/MESSENGER-ADDON-PLAN-GPT*.md): закреп/архив/избранное.
  pinChat: (convId) => api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/pin`),
  unpinChat: (convId) => api.delete(`/web/messenger/chats/${encodeURIComponent(convId)}/pin`),
  archiveChat: (convId) => api.post(`/web/messenger/chats/${encodeURIComponent(convId)}/archive`),
  unarchiveChat: (convId) => api.delete(`/web/messenger/chats/${encodeURIComponent(convId)}/archive`),
  openSaved: () => api.post('/web/messenger/chats/saved'),
  // Треды (ответы на сообщение) и поиск внутри чата.
  thread: (convId, messageId) =>
    api.get(`/web/messenger/chats/${encodeURIComponent(convId)}/messages/thread/${messageId}`),
  searchInChat: (convId, q) =>
    api.get(`/web/messenger/chats/${encodeURIComponent(convId)}/messages/search`, { params: { q } }),
  // §17: поиск по СМЫСЛУ. Модель расширяет ЗАПРОС словоформами и синонимами; сама
  // переписка ей не показывается (сопоставление делает сервер). Нет модели — результат
  // совпадает с обычным searchInChat, поле expanded приходит пустым.
  aiSearchInChat: (convId, q) =>
    api.get(`/web/messenger/chats/${encodeURIComponent(convId)}/messages/ai-search`, { params: { q } }),
  // §18: сводка переписки. Запускается ТОЛЬКО кнопкой (запрос к модели на каждый вход в
  // чат — заметная нагрузка на одноядерный VPS). ФИО маскируются на сервере до отправки.
  chatSummary: (convId) =>
    api.get(`/web/messenger/chats/${encodeURIComponent(convId)}/summary`),
  // §19: напоминания. Дату из текста разбирает СЕРВЕР детерминированно, без модели.
  reminderSuggest: (mid) => api.get(`/web/messenger/messages/${mid}/reminder-suggest`),
  createReminder: (mid, when = '') =>
    api.post(`/web/messenger/messages/${mid}/reminder`, when ? { when } : {}),
  reminders: () => api.get('/web/messenger/reminders'),
  deleteReminder: (rid) => api.delete(`/web/messenger/reminders/${rid}`),
  // Кто прочитал конкретное сообщение (переиспользует last_read_at участников).
  readBy: (mid) => api.get(`/web/messenger/messages/${mid}/read_by`),
  // Личные шаблоны быстрых ответов преподавателя.
  templates: () => api.get('/web/messenger/templates'),
  createTemplate: (body) => api.post('/web/messenger/templates', { body }),
  deleteTemplate: (id) => api.delete(`/web/messenger/templates/${id}`),
  // §12: данные оверлея отчёта (круговая + плоские по предметам) и дрилл-даун по предмету.
  reportOverview: (id) => api.get(`/web/messenger/reports/${encodeURIComponent(id)}`),
  reportSubject: (id, subject) =>
    api.get(`/web/messenger/reports/${encodeURIComponent(id)}/subject`, { params: { subject } }),
}

// МОДЕРАЦИЯ МЕССЕНДЖЕРА (только админ) ─────────────────────────────────────────────
// Активности в беседах (docs/done/PLAN-ACTIVITIES.md). Префикс `/web/messenger/...` выбран не
// случайно: он уже в `_PROXY_PREFIXES` десктопа, поэтому внутри программы всё работает
// без единой правки локального сервера.
export const activitiesApi = {
  start: (conversationId, kind, params = {}, title = '') =>
    api.post('/web/messenger/activities/start',
      { conversation_id: conversationId, kind, params, title }),
  //Все идущие активности беседы: их может быть несколько разных категорий.
  running: (conversationId) =>
    api.get('/web/messenger/activities/running', { params: { conversation_id: conversationId } }),
  current: (conversationId) =>
    api.get('/web/messenger/activities/current', { params: { conversation_id: conversationId } }),
  get: (id) => api.get(`/web/messenger/activities/${encodeURIComponent(id)}`),
  finish: (id, save = false) =>
    api.post(`/web/messenger/activities/${encodeURIComponent(id)}/finish`, { save }),
  journal: (conversationId) =>
    api.get('/web/messenger/activities/journal', { params: { conversation_id: conversationId } }),
  // Тайм-бокс: сервер один раз присылает время окончания, отсчёт ведёт клиент.
  timer: (id, action, seconds = 0) =>
    api.post(`/web/messenger/activities/${encodeURIComponent(id)}/timer`, { action, seconds }),
  // Опрос.
  vote: (id, choice) =>
    api.post(`/web/messenger/activities/${encodeURIComponent(id)}/vote`, { choice }),
  pollResults: (id) => api.get(`/web/messenger/activities/${encodeURIComponent(id)}/poll-results`),
  // Срез понимания.
  sendFeedback: (id, score, reasonCode = '', text = '') =>
    api.post(`/web/messenger/activities/${encodeURIComponent(id)}/feedback`,
      { score, reason_code: reasonCode, text }),
  feedbackSummary: (id) => api.get(`/web/messenger/activities/${encodeURIComponent(id)}/feedback`),
  reportFeedback: (feedbackId, reasonCode, description = '') =>
    api.post(`/web/messenger/activities/feedback/${feedbackId}/report`,
      { reason_code: reasonCode, description }),
  // Викторина и соревнование.
  questions: (id) => api.get(`/web/messenger/activities/${encodeURIComponent(id)}/questions`),
  submit: (id, answers, durationMs = 0) =>
    api.post(`/web/messenger/activities/${encodeURIComponent(id)}/submit`,
      { answers, duration_ms: durationMs }),
  answer: (id, answer) =>
    api.post(`/web/messenger/activities/${encodeURIComponent(id)}/answer`, { answer }),
  next: (id) => api.post(`/web/messenger/activities/${encodeURIComponent(id)}/next`),
  //Шкала прогресса у ведущего: только число пройденных заданий, без ответов.
  progress: (id, answered) =>
    api.post(`/web/messenger/activities/${encodeURIComponent(id)}/progress`, { answered }),
  results: (id) => api.get(`/web/messenger/activities/${encodeURIComponent(id)}/results`),
  // Библиотека викторин.
  quizzes: (params = {}) => api.get('/web/messenger/activities/quizzes', { params }),
  quiz: (id) => api.get(`/web/messenger/activities/quizzes/${encodeURIComponent(id)}`),
  similar: (id) => api.get(`/web/messenger/activities/quizzes/${encodeURIComponent(id)}/similar`),
  createQuiz: (body) => api.post('/web/messenger/activities/quizzes', body),
  updateQuiz: (id, body) => api.put(`/web/messenger/activities/quizzes/${encodeURIComponent(id)}`, body),
  copyQuiz: (id) => api.post(`/web/messenger/activities/quizzes/${encodeURIComponent(id)}/copy`),
  deleteQuiz: (id) => api.delete(`/web/messenger/activities/quizzes/${encodeURIComponent(id)}`),
  // Доска.
  strokes: (id, strokes, clear = false) =>
    api.post(`/web/messenger/activities/${encodeURIComponent(id)}/strokes`, { strokes, clear }),
  pen: (id, body) => api.post(`/web/messenger/activities/${encodeURIComponent(id)}/pen`, body),
  snapshot: (id) => api.post(`/web/messenger/activities/${encodeURIComponent(id)}/snapshot`),
  boards: (conversationId) =>
    api.get('/web/messenger/activities/boards', { params: { conversation_id: conversationId } }),
  board: (id) => api.get(`/web/messenger/activities/boards/${encodeURIComponent(id)}`),
}

export const messengerModApi = {
  reports: (status = 'open') => api.get('/web/admin/messenger/reports', { params: { status } }),
  resolve: (id, status, note = '') =>
    api.post(`/web/admin/messenger/reports/${id}/resolve`, { status, resolution_note: note }),
  conversations: (q = '', kind = '') =>
    api.get('/web/admin/messenger/conversations', { params: { q, kind } }),
  // reportId — только из вкладки «Жалобы»: сервер проверяет, что ЭТОТ тикет ещё
  // открыт, и отвечает 403, когда его закрыли (см. mod_conversation_messages).
  conversationMessages: (id, reportId = 0) =>
    api.get(`/web/admin/messenger/conversations/${encodeURIComponent(id)}/messages`,
            { params: reportId ? { report_id: reportId } : {} }),
  reply: (id, body) =>
    api.post(`/web/admin/messenger/conversations/${encodeURIComponent(id)}/reply`, { body }),
  // Глобальный мьют пользователя модерацией — ТОЛЬКО СО СРОКОМ (дни/часы/минуты
  // складываются). Бессрочный мьют сервер не принимает: наказание «пока не снимут»
  // снимать некому — см. mod_mute_user.
  // reportId — когда мьют выдаётся ИЗ жалобы: сервер проверит, что тикет ещё живой.
  muteUser: (uid, { hours = 0, minutes = 0, days = 0, reason = '', reportId = 0 } = {}) =>
    api.post(`/web/admin/messenger/users/${encodeURIComponent(uid)}/mute`,
      { muted: true, hours, minutes, days, reason, report_id: reportId }),
  unmuteUser: (uid) =>
    api.post(`/web/admin/messenger/users/${encodeURIComponent(uid)}/mute`, { muted: false }),
  // История наказаний и жалоб: первый это раз или пятый. Читается из журнала аудита —
  // строка мьюта живёт только до истечения срока и помнить прошлое не может.
  userHistory: (uid) =>
    api.get(`/web/admin/messenger/users/${encodeURIComponent(uid)}/history`),
  // Очередь жалоб на ПРОФИЛИ — отдельная от жалоб на сообщения (разные действия).
  userReports: (status = 'open') =>
    api.get('/web/admin/messenger/user-reports', { params: { status } }),
  resolveUserReport: (id, status, note = '') =>
    api.post(`/web/admin/messenger/user-reports/${id}/resolve`, { status, resolution_note: note }),
  // Люди, НА КОТОРЫХ ЕСТЬ ЖАЛОБА (не каталог колледжа — см. mod_users).
  users: (q = '') => api.get('/web/admin/messenger/users', { params: { q } }),
  // Почистить публичные поля профиля — сервер разрешит только при открытой жалобе.
  clearProfile: (uid, fields) =>
    api.post(`/web/admin/messenger/users/${encodeURIComponent(uid)}/profile`, { clear: fields }),
  // Удалить любое сообщение у всех (модерация; пишется в аудит).
  deleteMessage: (mid) => api.delete(`/web/admin/messenger/messages/${mid}`),
  // ── Очередь обращений (тикеты) ──────────────────────────────────────────────────
  // ⚠️ Порядок задаёт СЕРВЕР (срочные наверху, дальше кто дольше ждёт) и клиент его НЕ
  // пересортировывает: очередь читают разные экраны, и «срочное наверху» обязано
  // означать на всех одно и то же.
  support: (status = 'open') =>
    api.get('/web/admin/messenger/support', { params: { status } }),
  // Взять обращение: модератор подключается к чату и представляется своим НОМЕРОМ.
  claimSupport: (id) => api.post(`/web/admin/messenger/support/${id}/claim`),
  resolveSupport: (id, note = '') =>
    api.post(`/web/admin/messenger/support/${id}/resolve`, { note }),
}

// Мероприятия/события (олимпиады, конкурсы и т.п.) — заводит преподаватель/админ,
// уходит уведомлением выбранной аудитории (вкладка «Мероприятия» в NotificationsInbox).
export const eventsApi = {
  create: (title, body, groups = []) => api.post('/web/events', { title, body, groups }),
  //Что Я разослал — одна строка на рассылку (только препод/админ).
  sent: (limit = 50) => api.get('/web/events/sent', { params: { limit } }),
}

// Десктоп-клиент (публичный эндпоинт: доступность и размер GradeBookAI.exe).
export const desktopApi = {
  info: () => api.get('/desktop-info'),
}

// УПРАВЛЕНИЕ СЕРВЕРОМ ПО SSH — ТОЛЬКО В ПРОГРАММЕ ────────────────────────────────
// Эти маршруты подключает ЛОКАЛЬНЫЙ сервер программы (desktop/server_admin.py). На боевом
// сервере их нет вовсе: там работает только просмотр (adminApi.serverMetrics и рядом).
// Граница проходит по наличию кода, а не по проверке роли — роль можно обойти,
// отсутствующий код нельзя.
//
// ⚠️ ПРОВЕРЯТЬ ДОСТУПНОСТЬ НАДО ПО ТИПУ ОТВЕТА, А НЕ ПО КОДУ. На сайте неизвестный
// путь перехватывает заглушка SPA и отдаёт index.html с кодом 200 — «раздел доступен»
// по коду ответа тут получилось бы всегда.
export const deskServerApi = {
  async available() {
    try {
      const r = await api.get('/desk/servers')
      const ct = String(r.headers?.['content-type'] || '')
      return ct.includes('application/json') && Array.isArray(r.data?.items)
    } catch { return false }
  },
  list: () => api.get('/desk/servers'),
  // password передаём ТОЛЬКО когда его меняют: пустое поле не значит «сотри» (страница
  // пароль не показывает, и обычное сохранение имени пришло бы с пустым полем).
  // Убрать пароль — явным clear_password: true.
  save: (payload) => api.post('/desk/servers', payload),
  remove: (id) => api.delete(`/desk/servers/${encodeURIComponent(id)}`),
  metrics: (id) => api.get(`/desk/servers/${encodeURIComponent(id)}/metrics`),
  tree: (id, path = '') =>
    api.get(`/desk/servers/${encodeURIComponent(id)}/tree`, { params: { path } }),
  // Резервные копии БОЕВОЙ машины (не путать с adminApi.dataExport — то выгрузка
  // справочников в архив, а это снимок базы вместе с ключом шифрования).
  backups: (id) => api.get(`/desk/servers/${encodeURIComponent(id)}/backups`),
  makeBackup: (id) => api.post(`/desk/servers/${encodeURIComponent(id)}/backups`),
  downloadBackup: (id, name) =>
    api.post(`/desk/servers/${encodeURIComponent(id)}/backups/download`, { name }),
  // Положить открытый ключ этого компьютера на сервер — чтобы уйти с пароля на ключ.
  installKey: (id) => api.post(`/desk/servers/${encodeURIComponent(id)}/install-key`),
  // Запуск/остановка/перезапуск службы. Останов кладёт сайт для всего колледжа,
  // поэтому сервер отвечает 409 «confirm-required», пока не подтвердили осознанно.
  service: (id, name, action, confirm = false) =>
    api.post(`/desk/servers/${encodeURIComponent(id)}/service`, { name, action, confirm }),
  // confirm=true нужен для опасных команд: сервер отвечает 409 «confirm-required»,
  // пока человек не подтвердил осознанно (см. is_dangerous в desktop/server_admin.py).
  exec: (id, command, { confirm = false, long = false } = {}) =>
    api.post(`/desk/servers/${encodeURIComponent(id)}/exec`, { command, confirm, long }),
  migrationPlan: (source, target) =>
    api.post('/desk/servers/migrate/plan', { source, target }),
  migrationStep: (source, target, step) =>
    api.post('/desk/servers/migrate/step', { source, target, step }),
}

// ОБНОВЛЕНИЕ МОБИЛЬНОГО ПРИЛОЖЕНИЯ ─────────────────────────────────────────────
// Веб-часть (интерфейс) приезжает «по воздуху» сама — её манифест читает плагин Capgo
// напрямую по updateUrl из capacitor.config, минуя этот файл. А вот НАТИВНУЮ часть
// (сам APK) OTA не везёт никогда: виджет, мост пушей и права живут в apk и меняются
// только переустановкой. `/app/apk-info` отдаёт номер последней собранной сборки и
// ссылку на файл — приложение сравнивает его со СВОИМ build и предлагает скачать.
// ⚠️ Эндпоинт существовал с самого появления OTA и не вызывался ниоткуда — ровно тот
// класс «мёртвого обещания», что уже ловили с `:min-zet` и `saveEndpoint()`.
export const appApi = {
  apkInfo: () => api.get('/app/apk-info'),
}

// КУРСЫ ─────────────────────────────────────────────────────────────────────────
// Учебные курсы (структура, материалы, задания). Скоуп по роли считает сервер
// (routers/web/courses.py): студент — своя группа, преподаватель — свои, админ — все.
export const coursesApi = {
  list: (q = '', includeArchived = false) =>
    api.get('/web/courses', { params: { q, include_archived: includeArchived } }),
  get: (id) => api.get(`/web/courses/${id}`),
  create: (payload) => api.post('/web/courses', payload),   // {title, subject, group_name, description}
  archive: (id) => api.delete(`/web/courses/${id}`),
  addSection: (id, title, position = 0) =>
    api.post(`/web/courses/${id}/sections`, { title, position }),
  delSection: (id, sectionId) => api.delete(`/web/courses/${id}/sections/${sectionId}`),
  addMaterial: (id, { title, url = '', kind = 'link', sectionId = 0 }) =>
    api.post(`/web/courses/${id}/materials`, { title, url, kind, section_id: sectionId }),
  delMaterial: (id, materialId) => api.delete(`/web/courses/${id}/materials/${materialId}`),
  addAssignment: (id, { title, dueDate = '', description = '', url = '' }) =>
    api.post(`/web/courses/${id}/assignments`, { title, due_date: dueDate, description, url }),
  delAssignment: (id, assignmentId) => api.delete(`/web/courses/${id}/assignments/${assignmentId}`),
}
