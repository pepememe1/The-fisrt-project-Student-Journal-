"""
models.py — Таблицы БД (SQLAlchemy).

Каждая синхронизируемая сущность несёт служебные поля для offline-first синка:
  • updated_at — ISO-метка последнего изменения (строка, лексикографически
    сортируется как время). По ней работает дельта-синк и разрешение конфликтов
    «последний по времени побеждает».
  • deleted — «надгробие» (tombstone): удаление не стирает строку, а помечает её,
    чтобы удаление доехало до всех ПК (иначе на других ПК запись «воскресала» бы).
"""
from sqlalchemy import Column, Integer, String, Boolean, JSON, Float

from .db import Base


class User(Base):
    """Пользователь: администратор / модератор / преподаватель / студент / родитель."""
    __tablename__ = "users"
    id = Column(String, primary_key=True)              #uuid
    #⚠️ РОЛЬ — ПРОСТАЯ СТРОКА, И БАЗА ЕЁ НЕ ОГРАНИЧИВАЕТ. Значит опечатка в ней заводит
    #человека, который не подходит ни под одну проверку: войти он сможет, а не увидит
    #ничего и нигде, причём молча. Единственный список известных ролей —
    #`deps.KNOWN_ROLES`, и новая роль обязана попасть туда же, а не «просто записаться».
    role = Column(String, nullable=False)              #admin | moderator | teacher | student | parent
    login = Column(String, index=True, default="")
    password_hash = Column(String, default="")
    #Когда пароль выдавали в последний раз (ISO-строка, как updated_at). Нужна админу,
    #чтобы понимать, свежий пароль у человека или выдан полгода назад: САМ пароль
    #показать нельзя — в базе только необратимый хеш. Пусто = пароль не менялся с тех
    #пор, как у учётной записи появилось это поле (задним числом дату не выдумываем).
    password_set_at = Column(String, default="")
    #День рождения в виде «ДД.ММ», БЕЗ ГОДА. Год здесь не нужен ни для чего: поздравление
    #смотрит только на день и месяц, а полная дата рождения — совсем другой уровень
    #чувствительности и лишний повод объясняться при проверке 152-ФЗ. Задаёт админ;
    #пусто честно значит «не знаем», и тогда никого не поздравляем.
    birthday = Column(String, default="")
    full_name = Column(String, default="")             #ФИО (у преподавателя — ключ)
    surname = Column(String, default="")
    #ВАЖНО: name хранит «Имя Отчество» (полную форму) и служит КЛЮЧОМ оценок
    #(grades.student_n = name) и ключом студента при синке (stud:surname|name|group).
    #Менять его формат нельзя — иначе осиротеют оценки и рассинхронится десктоп.
    name = Column(String, default="")
    #Отчество ОТДЕЛЬНЫМ полем (для раздельного показа/ввода). Дублирует хвост name,
    #но name остаётся неизменным ключом. first_name для показа = name без patronymic.
    patronymic = Column(String, default="")
    group_name = Column(String, default="")            #для студента
    subjects = Column(JSON, default=list)              #для преподавателя
    group_assignments = Column(JSON, default=dict)     #для преподавателя
    #Группы, которые преподаватель КУРИРУЕТ (роль куратора). Непустой список = куратор;
    #отдельная роль не заводится (role остаётся teacher). Куратор ВИДИТ все предметы своих
    #групп (в т.ч. чужие) в режиме ТОЛЬКО ЧТЕНИЕ — см. routers/web.py /web/curator/*.
    curated_groups = Column(JSON, default=list)
    #Личные настройки пользователя (тема оформления и пр.). Меняет ТОЛЬКО сам
    #пользователь через self-эндпоинт POST /me/prefs (роли/пароль не затрагиваются).
    #Уезжает клиентам обычным pull (как и прочие столбцы) — так тема «роумится».
    prefs = Column(JSON, default=dict)
    updated_at = Column(String, default="", index=True)
    deleted = Column(Boolean, default=False)


class Group(Base):
    __tablename__ = "groups"
    id = Column(String, primary_key=True)
    name = Column(String, index=True, default="")
    subjects = Column(JSON, default=list)
    #Специальность (код классификатора СПО, «09.02.07») и год поступления —
    #NULLABLE: заполняются импортом учебного плана ВСГУТУ (parsers/esstu_parser.py,
    #POST /web/admin/groups/import-esstu), у групп без импорта остаются пустыми.
    #Хранятся именно ЗДЕСЬ (не только в параметрах запроса импорта), чтобы курс и
    #семестр группы можно было пересчитать ПОЗЖЕ, в любой момент — по мере того как
    #идёт время, group.enrollment_year не меняется, а «текущий курс» так и остаётся
    #производным от него (см. study_hours.course_and_semester), а не хранимым числом.
    specialty_code = Column(String, nullable=True)
    enrollment_year = Column(Integer, nullable=True)
    #Категория расписания портала (schedule/parser.py::CATEGORIES — "college"/
    #"bakalavriat"/"zo1"/"zo2"), НЕ путать со specialty_code выше (тот — код СПО с
    #ДРУГОГО сайта, esstu.ru/uportal, к порталу расписания отношения не имеет).
    #NULL у всех старых строк — везде по коду трактуется как "college" (единственная
    #категория с реальными аккаунтами/журналом/оценками; остальные заводятся вручную
    #или импортом имени с портала — см. POST /web/admin/groups/import-schedule-category
    #— просто как каталожные записи для просмотра расписания и группировки студентов).
    category = Column(String, nullable=True)
    #━━ АРХИВ ГРУППЫ (новый учебный год, 03.09.2026) ━━
    #Выпустилась или расформирована — но УДАЛЯТЬ нельзя: за ней живые студенты, оценки и
    #история занятий. `archived` убирает её из рабочих списков, оставляя всё содержимое
    #доступным для просмотра. Это НЕ `deleted`: удаление скрывает данные, архив их
    #показывает.
    archived = Column(Boolean, default=False)
    archived_at = Column(String, default="")           #серверная метка, как везде
    archived_reason = Column(String, default="")       #«не перешла на 4 курс» и т. п.
    #Последний ЗАСВИДЕТЕЛЬСТВОВАННЫЙ курс и учебный год, в котором его видели. По этой
    #паре и вычисляется «новый год начался, а группа не перешла»: без неё сравнивать
    #не с чем — курс приходит с портала и своей истории не имеет.
    #⚠️ Заполняется на переводе периода и при явном пересчёте админом, а НЕ при чтении:
    #список кандидатов обязан быть повторяемым, иначе первый же его показ стирал бы
    #основание, по которому группа в него попала.
    last_course = Column(Integer, nullable=True)
    last_course_year = Column(String, default="")
    #━━ ПЕРЕВОД НА НОВЫЙ КУРС (05.09.2026) ━━
    #Учебный период, начиная с которого назначения преподавателей у ЭТОЙ группы обязаны
    #быть ЯВНЫМИ («2026/2027·1»). Пусто — группу не переводили.
    #
    #⚠️ Зачем отдельная метка, а не «фолбэк только по текущему термину». Мост
    #`webdata._assignments_fallback` (поставлен после боевого простоя 30.07.2026) отдаёт
    #преподавателю группы по его ЗАНЯТИЯМ, без фильтра по периоду — поэтому после смены
    #курса он продолжает видеть прошлогодние группы как действующие. Глобально сузить мост
    #до текущего термина нельзя: сразу после перевода текущих занятий нет НИ У КОГО, и все
    #преподаватели разом ушли бы во вторую ветку фолбэка — «все группы, где числится мой
    #предмет», то есть весь колледж. Метка выключает мост ровно у той группы, по которой
    #администратор уже принял решение.
    assignments_reset_term = Column(String, default="")
    updated_at = Column(String, default="", index=True)
    deleted = Column(Boolean, default=False)


class Subject(Base):
    __tablename__ = "subjects"
    id = Column(String, primary_key=True)
    name = Column(String, index=True, default="")
    updated_at = Column(String, default="", index=True)
    deleted = Column(Boolean, default=False)


class Lesson(Base):
    __tablename__ = "lessons"
    id = Column(String, primary_key=True)              #uuid (как в десктопе)
    group_name = Column(String, index=True, default="")
    subject = Column(String, index=True, default="")
    type = Column(String, default="")
    number = Column(Integer, default=0)
    topic = Column(String, default="")
    date = Column(String, default="")
    retake_date = Column(String, default="")
    hour = Column(Integer, default=0)
    #Измерение УЧЕБНОГО ПЕРИОДА (фундамент долгосрочного журнала): год «2025/2026» и
    #семестр (1..8). Оценки наследуют период от занятия (ключ оценки не трогаем). Новые
    #занятия штампуются ТЕКУЩИМ термином из config; старые бэкфилл-ятся при миграции.
    year = Column(String, index=True, default="")
    semester = Column(Integer, index=True, default=0)
    extra = Column(JSON, default=dict)                 #retake_date_2..5 и пр.
    #Раздельное обучение (§ролей, 3.6.1): 0 — обычное занятие (предмет не разделён, ИЛИ
    #«Совместно» — занятие сразу для обеих подгрупп), 1/2 — только для этой подгруппы.
    #Нумерация («Практика №N») считается ОТДЕЛЬНО в пределах каждого значения — иначе
    #у двух преподавателей одной группы независимые «Практика №1» слились бы в одну
    #серию и сбивали счёт друг другу (см. webdata.next_lesson_number).
    subgroup = Column(Integer, default=0)
    updated_at = Column(String, default="", index=True)
    deleted = Column(Boolean, default=False)


class Grade(Base):
    __tablename__ = "grades"
    id = Column(String, primary_key=True)              #f|n|lesson_id
    student_f = Column(String, index=True, default="")
    student_n = Column(String, index=True, default="")
    lesson_id = Column(String, index=True, default="")
    grade = Column(String, default="")
    device = Column(String, default="")                #имя ПК — для конфликтов
    updated_at = Column(String, default="", index=True)
    deleted = Column(Boolean, default=False)
    #Неизменяемая привязка к студенту (users.id). ЭТАП 1 миграции с ФИО-ключей: поле
    #ДОБАВОЧНОЕ, первичный ключ пока прежний (f|n|lesson_id). Пусто допустимо — старые
    #строки и клиенты, которые о поле не знают. Смысл: ФИО меняется, id нет.
    student_id = Column(String, index=True, default="")


class TermGrade(Base):
    """Итоговая оценка за семестр по предмету (промежуточная аттестация) — отдельно от
    Grade (та — по конкретным занятиям). Ключ: студент+предмет+год+семестр. Из неё
    строятся ведомости. Входит в SYNC_MODELS: десктоп ведёт аттестацию/ведомости
    наравне с вебом, данные общие (десктопная таблица term_grades ↔ эта)."""
    __tablename__ = "term_grades"
    id = Column(String, primary_key=True)              #f|n|subject|year|semester
    student_f = Column(String, index=True, default="")
    student_n = Column(String, index=True, default="")
    subject = Column(String, index=True, default="")
    year = Column(String, index=True, default="")
    semester = Column(Integer, index=True, default=0)
    grade = Column(String, default="")                 #5/4/3/2 | Зачтено/Не зачтено
    form = Column(String, default="")                  #зачёт | экзамен | диффзачёт
    updated_at = Column(String, default="", index=True)
    deleted = Column(Boolean, default=False)
    #Неизменяемая привязка к студенту (users.id). ЭТАП 1 миграции с ФИО-ключей: поле
    #ДОБАВОЧНОЕ, первичный ключ пока прежний (f|n|subject|year|semester). Пусто допустимо — старые
    #строки и клиенты, которые о поле не знают. Смысл: ФИО меняется, id нет.
    student_id = Column(String, index=True, default="")


class ParentLink(Base):
    """Связь «родитель → студент»: кто из родителей вправе видеть журнал этого студента.

    Привязку создаёт СОТРУДНИК (админ или куратор своей группы), но сама по себе она
    доступа НЕ даёт: пока студент не подтвердил её в своём кабинете, статус `pending` и
    родитель журнала не видит. Причина не техническая, а правовая — в колледже учатся и
    совершеннолетние, и для них доступ родителя без их согласия нарушает 152-ФЗ. Схема с
    подтверждением работает для любого возраста, поэтому дату рождения ради этой проверки
    хранить не нужно вовсе (меньше персональных данных — меньше рисков).

    Связь всегда по НЕИЗМЕНЯЕМЫМ users.id. По ФИО нельзя: фамилия меняется (см. §12
    миграции оценок), и связь бы осиротела ровно тогда, когда она нужнее всего.

    НЕ входит в SYNC_MODELS: кабинет родителя — онлайн-раздел (на десктопе он открывается
    тем же веб-представлением, что мессенджер), и десктопу эта таблица не нужна."""
    __tablename__ = "parent_links"
    id = Column(String, primary_key=True)              #plink:{parent_id}|{student_id}
    parent_id = Column(String, index=True, default="")
    student_id = Column(String, index=True, default="")
    #pending — ждёт согласия студента; active — согласие дано; revoked — отозвано/отклонено.
    #Отозванную связь НЕ удаляем: должно остаться видно, что доступ был и когда его сняли.
    status = Column(String, index=True, default="pending")
    created_at = Column(String, default="")
    created_by = Column(String, default="")            #логин сотрудника (для разбора)
    decided_at = Column(String, default="")            #когда студент подтвердил/отклонил


def parent_link_id(parent_id: str, student_id: str) -> str:
    """Детерминированный ключ: повторная привязка тех же двоих ЗАМЕНЯЕТ запись, а не
    плодит вторую (иначе отозванная связь соседствовала бы с новой активной)."""
    return f"plink:{parent_id}|{student_id}"


def set_user_password(row: "User", plain: str) -> None:
    """ЕДИНСТВЕННОЕ место, где меняется пароль: хеш и дата выдачи ставятся вместе.

    Раздельно их ставить нельзя — не «некрасиво», а по опыту: правило, расписанное по
    семи местам вместо одного, здесь уже приводило к тому, что в одном из семи его
    забыли (см. историю с `collect_failed` в шапке CLAUDE.md). Мест, где пароль
    меняется, ровно семь: регистрация, восстановление по почте, заведение и правка
    студента/преподавателя/родителя, одобрение заявки. Новое восьмое место обязано
    звать эту функцию, а не присваивать `password_hash` руками.
    """
    from datetime import datetime, timezone
    from .security import hash_password
    row.password_hash = hash_password(plain)
    row.password_set_at = datetime.now(timezone.utc).isoformat()


class ConfigKV(Base):
    """Глобальные настройки (ключ → JSON): API-ключи, методика оценок и т.п."""
    __tablename__ = "config"
    key = Column(String, primary_key=True)
    value = Column(JSON)
    updated_at = Column(String, default="", index=True)
    deleted = Column(Boolean, default=False)


class ApprovedDevice(Base):
    """Устройство (ПК), которому администратор разрешил подключаться к серверу.

    Барьер подтверждения: пока device_id не лежит здесь, сервер отвергает вход и
    синхронизацию с этого устройства (см. connect.device_allowed). В отличие от
    «ожидающих» запросов (они транзиентны, живут в памяти — connect.py), одобренные
    устройства ДОЛЖНЫ переживать перезапуск сервера, поэтому хранятся в БД.

    Намеренно НЕ входит в SYNC_MODELS: это серверная деталь доступа, клиентам её
    синхронизировать незачем."""
    __tablename__ = "approved_devices"
    device_id = Column(String, primary_key=True)       #uuid с клиента (X-Device-Id)
    ip = Column(String, default="")                    #IP на момент одобрения
    hostname = Column(String, default="")              #имя ПК (для опознания админом)
    approved_at = Column(String, default="")           #ISO-метка одобрения (UTC)
    approved_by = Column(String, default="")           #логин админа, одобрившего


class AuthSession(Base):
    """Выданный токен (сессия). Нужен для трёх вещей сразу:

      • ЧЁРНЫЙ СПИСОК / отзыв: пока запись не `revoked`, токен с этим `jti` валиден;
        админ (или logout) ставит `revoked=True` — и сервер мгновенно перестаёт пускать
        этот токен, даже если по подписи и `exp` он ещё «живой» (важно для 152-ФЗ:
        экстренная блокировка, безопасный выход, смена ролей на лету);
      • REFRESH: долгоживущий refresh-токен (kind='refresh') обменивается на новый
        access через /auth/refresh — клиент не выкидывает пользователя на логин, когда
        короткий access протух посреди работы;
      • ВИДИМОСТЬ: админ видит список активных сессий (кто, с какого устройства, до когда).

    Серверная деталь доступа — НЕ входит в SYNC_MODELS (клиентам синхронизировать незачем)."""
    __tablename__ = "auth_sessions"
    jti = Column(String, primary_key=True)             #уникальный id токена (из payload)
    login = Column(String, index=True, default="")
    role = Column(String, default="")
    kind = Column(String, default="access")            #access | refresh
    #Каким клиентом выдана сессия: 'android' | 'web' | '' (десктоп). Определяет
    #АБСОЛЮТНЫЙ ПОТОЛОК сессии: телефон личный и под блокировкой экрана, поэтому у него
    #неделя, а у сайта и десктопа — прежние 5 часов (общий ПК колледжа за спиной у
    #человека остаётся открытым). Хранится ИМЕННО ЗДЕСЬ, а не читается из заголовка при
    #каждом refresh: иначе браузер, приславший X-Client: android, растянул бы уже
    #выданную веб-сессию до недели — заголовок стал бы способом обойти потолок.
    client = Column(String, default="")
    device_id = Column(String, default="")             #X-Device-Id, с которого выдан
    ip = Column(String, default="")
    issued_at = Column(String, default="")             #ISO UTC
    expires_at = Column(Integer, default=0, index=True)  #unix ts (быстрый фильтр/чистка)
    revoked = Column(Boolean, default=False)
    pair_jti = Column(String, default="")              #связанный токен (access↔refresh)


class RegistrationRequest(Base):
    """Заявка студента на самостоятельную регистрацию с экрана входа. Ждёт одобрения
    администратора: тот подтверждает → система генерирует логин(=email)+пароль, заводит
    студента и шлёт креды на почту. Серверная деталь доступа — НЕ входит в SYNC_MODELS
    (заявки клиентам синхронизировать незачем)."""
    __tablename__ = "registration_requests"
    id = Column(String, primary_key=True)              #uuid
    full_name = Column(String, default="")
    group_name = Column(String, default="")            #РАЗРЕШЁННАЯ группа (после resolve)
    phone = Column(String, default="")
    email = Column(String, index=True, default="")     #станет логином
    status = Column(String, default="pending")         #pending | approved | rejected
    created_at = Column(String, default="")
    note = Column(String, default="")                  #причина отклонения и т.п.


class StudentInvite(Base):
    """Приглашение в группу: ссылка, по которой студент заводит себе аккаунт САМ.

    Просьба Ярослава: «фича для приглашения студентов». До этого путей было ровно два, и
    оба плохие для сентября: админ заводит тридцать человек руками, либо каждый студент
    подаёт заявку с экрана входа и ждёт, пока её одобрят по одной.

    🔑 ПРИГЛАШЕНИЕ — ЭТО И ЕСТЬ ОДОБРЕНИЕ. Куратор выдал ссылку своей группе; тот, у кого
    она есть, регистрируется без второго круга согласований. Поэтому у ссылки обязаны
    быть все три ограничителя сразу, и ни один не лишний:
      • `expires_at` — ссылка живёт неделями, а группа набирается за неделю; вечная
        ссылка в чате курса переживёт и выпуск, и смену куратора;
      • `max_uses` — приглашение на группу из 25 человек не должно заводить сто аккаунтов,
        если ссылку переслали наружу;
      • `revoked` — единственный способ закрыть уже утёкшую ссылку, не дожидаясь срока.
    ⚠️ Токен — секрет: он и есть право на регистрацию. Генерируется `secrets`, не uuid4
    (uuid4 в CPython тоже криптостойкий, но это не его назначенная роль, и следующий
    читатель не обязан это знать).

    ⚠️ НЕ в SYNC_MODELS: это серверная деталь доступа, как RegistrationRequest.
    """
    __tablename__ = "student_invites"
    id = Column(String, primary_key=True)              #сам токен из ссылки
    group_name = Column(String, index=True, default="")
    created_by = Column(String, default="")            #users.id выдавшего
    created_at = Column(String, default="")
    expires_at = Column(String, default="")            #ISO; пусто = бессрочно (не выдаём)
    max_uses = Column(Integer, default=0)              #0 = без ограничения по числу
    uses = Column(Integer, default=0)
    revoked = Column(Boolean, default=False)
    note = Column(String, default="")                  #подпись выдавшего («1 курс, сентябрь»)


class PasswordReset(Base):
    """Одноразовая ссылка на смену пароля.

    🔒 ЗАЧЕМ ОНА ПОЯВИЛАСЬ. Раньше `POST /auth/recover` менял пароль СРАЗУ: знающий
    почту студента (а почта у нас и есть логин, доменов всего три) обнулял ему доступ
    и отзывал все сессии — без единого подтверждения, повторяемо, из любого браузера.
    Захвата аккаунта это не давало (новый пароль уходил владельцу), но выкидывало
    человека из журнала посреди пары, и защититься он не мог никак.

    Теперь письмо несёт ССЫЛКУ, а пароль меняется только по переходу по ней. Тот, кто
    почтой не владеет, не меняет НИЧЕГО: запрос без перехода — это просто письмо.

    Свойства, каждое из которых обязательно:
      • `token` — случайный, из `secrets` (не `random`), и он же первичный ключ:
        предъявить можно только то, что реально пришло на почту;
      • `expires_at` — короткий срок (см. `PASSWORD_RESET_TTL_MIN`): украденное письмо
        полугодовой давности не должно открывать дверь;
      • `used_at` — ОДИН раз. Иначе ссылка из почтового ящика остаётся вечным ключом,
        и увольнение сотрудника/потеря телефона перестают что-либо значить;
      • храним ЛОГИН, а не ссылку на объект: аккаунт могли удалить, и подтверждение
        обязано в этом случае честно отказать, а не упасть.

    Серверная деталь доступа — НЕ входит в SYNC_MODELS."""
    __tablename__ = "password_resets"
    token = Column(String, primary_key=True)           #секрет из письма (secrets.token_urlsafe)
    login = Column(String, index=True, default="")     #кому меняем пароль
    created_at = Column(String, default="", index=True)
    expires_at = Column(String, default="")            #ISO UTC — после этого не годится
    used_at = Column(String, default="")               #когда воспользовались ("" — ещё нет)
    ip = Column(String, default="")                    #откуда запросили (для разбора)


class AuditEvent(Base):
    """Журнал значимых действий (ФСТЭК №21, п. регистрации событий безопасности).

    В отличие от `events.py` (кольцевой буфер В ПАМЯТИ, живёт до перезапуска) — это
    ПЕРСИСТЕНТНЫЙ, только-на-добавление журнал в БД: входы/выходы, выдача и отзыв
    доступа, изменения оценок и ПДн, одобрение/отклонение регистраций. Нужен для
    разбора инцидентов и как доказательная база при проверке. Записи НЕ редактируются
    и НЕ удаляются приложением. Серверная деталь — НЕ входит в SYNC_MODELS."""
    __tablename__ = "audit_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_ts = Column(Integer, default=0, index=True)   #unix ts — быстрый фильтр/сортировка
    ts = Column(String, default="")                       #ISO UTC — человекочитаемо
    actor = Column(String, index=True, default="")        #логин инициатора ("" = аноним/система)
    role = Column(String, default="")
    ip = Column(String, default="")
    device = Column(String, default="")                   #X-Device-Id (усечённо)
    action = Column(String, index=True, default="")       #короткий код: login.ok, grade.set…
    target = Column(String, default="")                   #на кого/что подействовали
    detail = Column(String, default="")                   #доп.контекст (без «сырых» ПДн)
    level = Column(String, default="info")                #info | warn | error

    #🔒 ЦЕПОЧКА ЦЕЛОСТНОСТИ (01.09.2026, ASVS V16.4.2).
    #Приложение журнал не правит и не удаляет — но у того, кто получил доступ К БАЗЕ
    #(root на VPS), препятствий не было НИКАКИХ: строку можно изменить или убрать, и
    #заметить это было бы нечем. То есть след, ради которого журнал и заведён, не
    #переживал ровно того события, ради которого его читают.
    #Каждая запись несёт хеш предыдущей: правка, удаление из середины, вставка и
    #перестановка перестают сходиться при пересчёте (см. `audit.verify_chain`).
    #⚠️ Цепочка ловит НЕЗАМЕТНУЮ подмену, но не мешает переписать журнал ЦЕЛИКОМ — для
    #этого нужна контрольная точка вне машины (`audit.checkpoint`), и это отдельный шаг.
    prev_hash = Column(String, default="")                #entry_hash предыдущей записи
    entry_hash = Column(String, default="")               #sha256 полей этой записи + prev_hash


class WebAuthnCredential(Base):
    """Passkey (WebAuthn) — публичный ключ пользователя для входа по биометрии (Face ID/
    отпечаток) БЕЗ пароля. Приватный ключ НИКОГДА не покидает устройство пользователя;
    сервер хранит только публичную часть и проверяет ею подпись при входе. `sign_count`
    растёт при каждом использовании — защита от клонирования ключа. Серверная деталь
    доступа — НЕ входит в SYNC_MODELS."""
    __tablename__ = "webauthn_credentials"
    credential_id = Column(String, primary_key=True)   #base64url id ключа (от аутентификатора)
    login = Column(String, index=True, default="")     #чей ключ
    public_key = Column(String, default="")            #base64url COSE-публичный ключ
    sign_count = Column(Integer, default=0)            #счётчик подписей (анти-клон)
    transports = Column(String, default="")            #csv: internal,hybrid,usb…
    device_name = Column(String, default="")           #как показать пользователю
    created_at = Column(String, default="")
    last_used = Column(String, default="")


class ScheduleOverride(Base):
    """Правка расписания администратором ПОВЕРХ портала (overlay).

    Расписание тянется с portal.esstu.ru (read-only). Здесь админ точечно правит его для
    группы: `action='set'` — задать/заменить пару в ячейке (неделя, день, номер пары),
    `action='remove'` — скрыть портальную пару в этой ячейке. При выдаче расписания
    группы правки НАКЛАДЫВАЮТСЯ на портальные данные (см. web._apply_overrides). Так
    работает и для колледжей без портала (там просто пустая основа + ручные пары).
    ВХОДИТ в SYNC_MODELS — правки расписания ОБЩИЕ для веб и десктопа: админ правит на любой
    платформе, изменение синхронизируется на другую (и работает офлайн на десктопе)."""
    __tablename__ = "schedule_overrides"
    #Строковый детерминированный id `sovr:{group}|{week}|{day}|{pair}` — как у остальных
    #синкуемых сущностей (grp:/subj:/...). Автоинкремент-int столкнулся бы между ПК.
    id = Column(String, primary_key=True)
    group_name = Column(String, index=True, default="")
    week = Column(Integer, default=1)          #1 (I неделя) / 2 (II неделя)
    day = Column(String, default="")           #короткое имя: Пнд Втр Срд Чтв Птн Сбт
    pair_no = Column(Integer, default=0)        #номер пары в дне
    action = Column(String, default="set")      #set | remove
    subject = Column(String, default="")
    time = Column(String, default="")
    room = Column(String, default="")
    teacher = Column(String, default="")
    kind = Column(String, default="")           #Лекция/Практика и т.п.
    #Подгруппа: 0 — пара для всей группы («Совместно»), 1/2 — только для своей половины.
    #Ровно та же нумерация, что у `Lesson.subgroup` в журнале, — вторая система координат
    #для одного и того же понятия разъехалась бы с первой.
    subgroup = Column(Integer, default=0)
    updated_at = Column(String, default="")
    deleted = Column(Boolean, default=False)


def schedule_override_id(group: str, week, day: str, pair_no, subgroup=0) -> str:
    """Детерминированный id ячейки — одинаков на вебе и десктопе (ключ синка).

    🔥 ПОДГРУППА ВХОДИТ В КЛЮЧ, И БЕЗ ЭТОГО БЫЛ ДЕФЕКТ (жалоба Влада 03.09.2026):
    «если выставить у новой пары номер, который уже есть, новая пара ЗАМЕНЯЕТ старую».
    Так и было по построению — в ячейку (неделя, день, номер) помещалась ровно одна
    пара, и вторая затирала первую молча, вместе с чужой работой.

    ⚠️ Для `subgroup == 0` («Совместно») формат СТАРЫЙ, без хвоста. Это не косметика:
    ключ синкуется между десктопом и сервером, и смена формата у существующих строк
    означала бы, что каждая правка расписания приедет на другой ПК как НОВАЯ, рядом со
    старой. Новый хвост появляется только у подгрупп, которых раньше не существовало.
    """
    tail = f"|{int(subgroup)}" if int(subgroup or 0) else ""
    return f"sovr:{group}|{int(week)}|{day}|{int(pair_no)}{tail}"


class SubjectHours(Base):
    """Плановые учебные часы предмета для группы на семестр.

    Задаёт администратор («Группы» → группа → предмет → часов на семестр); преподаватель
    и студент видят «пройдено X из Y часов». ПРОЙДЕННЫЕ часы здесь НЕ хранятся — они
    считаются из занятий (см. webdata.hours_progress). Хранимый счётчик пришлось бы
    править при каждом удалении занятия, и он бы неминуемо разъехался с журналом.

    ВХОДИТ в SYNC_MODELS: журнал на десктопе нативный и работает офлайн, а счётчик часов
    нужен именно там, где преподаватель ведёт занятия. Без синка он был бы пустым на
    единственной платформе, где он по-настоящему нужен.

    ⚠️ Почему ОТДЕЛЬНАЯ таблица, а не колонка в `groups`: часы зависят ещё и от предмета,
    года и семестра, то есть одной строкой на группу не описываются. Плюс урок таблицы
    `muted_users` — `_row_to_dict` синка отдал бы десктопу неизвестную ему колонку."""
    __tablename__ = "subject_hours"
    id = Column(String, primary_key=True)      #hrs:{группа}|{предмет}|{год}|{семестр}
    group_name = Column(String, index=True, default="")
    subject = Column(String, index=True, default="")
    year = Column(String, index=True, default="")
    semester = Column(Integer, index=True, default=0)
    hours_total = Column(Integer, default=0)   #сколько часов запланировано на семестр
    #Назначение препода на эту пару (группа,предмет) — та же строка, что и часы, потому
    #что гранулярность идентична (группа+предмет+год+семестр) и именно здесь уже сидит
    #админ-редактор («Группы» → 🕐), где Влад просил заводить выбор преподавателя. Пусто —
    #«не назначено» (обратная совместимость: старые строки без препода работают как раньше,
    #это ЕДИНСТВЕННЫЙ источник правды «кто ведёт какую группу по какому предмету» —
    #заменяет неиспользуемый User.group_assignments, см. routers/webdata.py::teacher_assignments).
    teacher_id = Column(String, index=True, default="")
    #ЗЕТ (зачётная единица трудоёмкости, ФГОС) предмета на семестр — nullable: NULL значит
    #«администратор не задавал», и интерфейс НЕ показывает строку ЗЕТ вовсе (см.
    #docs/done/PLAN-ZET.md). Задаётся вручную в том же редакторе, что и hours_total — гранулярность
    #та же (группа+предмет+год+семестр), отдельная колонка в существующей таблице проще,
    #чем новая сущность.
    zet = Column(Float, nullable=True)
    #Раздельное обучение (§ролей, 3.6.1) — куратор ставит галочку в «Курировании», ПОСЛЕ
    #этого админ вправе занять teacher_id_2 (см. ниже). Флаг живёт на самой строке часов,
    #а не отдельной таблицей: гранулярность та же (группа+предмет+год+семестр).
    split = Column(Boolean, default=False)
    #Второй преподаватель — подгруппа 2. Пусто при split=True значит «подгруппа 2 тоже за
    #teacher_id» (случай «маленький класс, один препод ведёт обе подгруппы по очереди» —
    #сознательное решение не заводить для этого отдельный переключатель, см. CLAUDE.md).
    #Бессмысленно, если split=False — writer обязан это проверять (см. admin_read.py).
    teacher_id_2 = Column(String, index=True, default="")
    updated_at = Column(String, default="", index=True)
    deleted = Column(Boolean, default=False)


def subject_hours_id(group: str, subject: str, year: str, semester) -> str:
    """Детерминированный ключ (как sovr:/grp:/subj:) — автоинкремент столкнулся бы между
    ПК. Повторное сохранение часов ЗАМЕНЯЕТ строку, а не плодит дубли."""
    return f"hrs:{group}|{subject}|{year}|{int(semester or 0)}"


class StudentSubgroup(Base):
    """Подгруппа (1 или 2) студента ПО ОДНОМУ раздельному предмету на семестр (§ролей,
    3.6.1) — куратор расставляет галочками, ПОСЛЕ того как админ занял teacher_id_2 у
    SubjectHours этого предмета. Один студент может быть в 1-й подгруппе по английскому
    и во 2-й по физре одновременно — поэтому ключ включает предмет, а не только группу.

    ВХОДИТ в SYNC_MODELS: журнал на десктопе нативный (офлайн), и роспись по подгруппам
    должна быть видна там же, где преподаватель ведёт занятия, без похода в сеть."""
    __tablename__ = "student_subgroups"
    id = Column(String, primary_key=True)      #sg:{группа}|{предмет}|{год}|{семестр}|{student_id}
    group_name = Column(String, index=True, default="")
    subject = Column(String, index=True, default="")
    year = Column(String, index=True, default="")
    semester = Column(Integer, index=True, default=0)
    student_id = Column(String, index=True, default="")
    subgroup = Column(Integer, default=1)      #1 или 2
    updated_at = Column(String, default="", index=True)
    deleted = Column(Boolean, default=False)


def student_subgroup_id(group: str, subject: str, year: str, semester, student_id: str) -> str:
    """Детерминированный ключ — повторная простановка подгруппы ЗАМЕНЯЕТ запись."""
    return f"sg:{group}|{subject}|{year}|{int(semester or 0)}|{student_id}"


class ZetThreshold(Base):
    """Минимальный порог ЗЕТ за семестр для перевода группы на следующий курс
    (docs/done/PLAN-ZET.md). Задаёт куратор или админ.

    ⚠️ ВХОДИТ в SYNC_MODELS с 3.7. Раньше здесь стояло «НЕ входит — серверная политика,
    ровно как ScheduleOverride», и эта формулировка успела устареть дважды: сам
    `ScheduleOverride` в SYNC_MODELS давно добавлен, а без порога внутри программы
    получалось хуже, чем «неизвестно». `/web/student/zet` честно отдавал `min_zet=None`,
    страница молча не рисовала строку порога — и студент, глядя на свой баланс без
    единого ограничения рядом, делал ровно тот вывод, которого быть не должно: «перевод
    возможен при любом балансе». Порог маленький, меняется раз в семестр и студенту
    нужен ТОЛЬКО на чтение — это ровно тот случай, для которого синк и существует.

    Пересылать эти три пути (`/web/student/zet`, `/web/parent/zet`,
    `/web/curator/zet-report`) вместо синка НЕЛЬЗЯ: они лежат внутри корней, обязанных
    читаться офлайн, и пересылка выключила бы офлайн-показ уже работающего баланса ЗЕТ
    (он считается из `SubjectHours.zet`, который синкается) ради одного недостающего
    поля. Сторож `tests/test_proxy_security.py` ловит такую попытку.

    РЕДАКТОР порогов (`/web/admin/zet-thresholds`) — наоборот, пересылается: запись
    внутри программы уходит в read-only зеркало `local_app.db`, обратного пути на бой у
    Phase B-записей нет, и сохранённый порог потерялся бы молча."""
    __tablename__ = "zet_thresholds"
    id = Column(String, primary_key=True)      #zth:{группа}|{год}|{семестр}
    group_name = Column(String, index=True, default="")
    year = Column(String, index=True, default="")
    semester = Column(Integer, index=True, default=0)
    min_zet = Column(Float, default=0.0)
    updated_at = Column(String, default="", index=True)
    updated_by = Column(String, default="")    #login администратора/куратора
    deleted = Column(Boolean, default=False)


def zet_threshold_id(group: str, year: str, semester) -> str:
    return f"zth:{group}|{year}|{int(semester or 0)}"


class ScheduleJointMark(Base):
    """Отметка «совместная пара» — законное совпадение, а не накладка.

    Один преподаватель в одно время у ДВУХ групп обычно означает ошибку составителя.
    Но лекция, которую читают сразу двум группам в одной аудитории, — нормальная
    практика, и детектор накладок обязан такие слоты пропускать, иначе админ утонет в
    ложных тревогах и перестанет смотреть в центр проблем вовсе.

    Почему ОТДЕЛЬНАЯ таблица, а не колонка в ScheduleOverride (решение Влада):
      • пометить нужно и ПОРТАЛЬНУЮ пару, которой в overrides нет вообще; иначе ради
        одной галочки пришлось бы создавать override-копию и тем самым подменять данные
        портала своими;
      • ScheduleOverride синхронизируется с десктопом, а это чисто серверная деталь
        админского редактора — в SYNC_MODELS ей делать нечего.

    Ключ — слот + то, что совпало: kind='teacher' → ФИО, kind='room' → аудитория."""
    __tablename__ = "schedule_joint_marks"
    id = Column(String, primary_key=True)
    kind = Column(String, default="teacher")    #teacher | room
    week = Column(Integer, default=1)
    day = Column(String, default="")
    pair_no = Column(Integer, default=0)
    value = Column(String, default="")          #ФИО преподавателя либо номер аудитории
    note = Column(String, default="")           #зачем помечено (для следующего админа)
    created_at = Column(String, default="")
    created_by = Column(String, default="")


def joint_mark_id(kind: str, week, day: str, pair_no, value: str) -> str:
    """Детерминированный ключ отметки: повторная пометка того же слота ЗАМЕНЯЕТ прежнюю,
    а не плодит дубли. Значение приводим к нижнему регистру без крайних пробелов —
    «Иванов И.И.» и «иванов и.и. » это один и тот же преподаватель."""
    v = (value or "").strip().lower()
    return f"joint:{kind}|{int(week)}|{day}|{int(pair_no)}|{v}"


#Ключи оценок — ЭТАП 3 миграции. Формат один на обе платформы (§10), считается ТОЛЬКО
#здесь и в десктопном зеркале (core.grade_id / core.term_grade_id): раньше строка
#f"{f}|{n}|{lesson_id}" собиралась в семи местах, и любое расхождение молча плодило
#дубли.
#
#Что изменилось: ключом стал НЕИЗМЕНЯЕМЫЙ student_id вместо написания ФИО. Раньше
#переименование студентки (замужество, исправление опечатки) означало перенос всей
#истории на новые ключи с надгробиями на старых — хрупкая операция, которая при любом
#сбое посреди пути оставляла оценки осиротевшими. Теперь ФИО в таблицах остаётся, но
#только как ДЕНОРМАЛИЗОВАННАЯ КОПИЯ для показа и старых выборок: смена фамилии — это
#обычный UPDATE двух полей, ключи не двигаются вообще.
def grade_id(student_id: str, lesson_id: str) -> str:
    """Ключ оценки за занятие: `{student_id}|{lesson_id}`."""
    return f"{student_id}|{lesson_id}"


def term_grade_id(student_id: str, subject: str, year: str, semester) -> str:
    """Ключ итоговой оценки: `{student_id}|{subject}|{year}|{semester}`."""
    return f"{student_id}|{subject}|{year}|{int(semester or 0)}"


def is_new_style_key(key: str) -> bool:
    """Ключ уже в новом формате (начинается с id студента)?

    Нужно на приёме push: старый клиент шлёт `Иванова|Мария|L1`, и без распознавания
    рядом с `stud:ivanova|L1` появился бы ДУБЛЬ той же оценки. Все id пользователей
    имеют префикс роли (`stud:`), поэтому проверка дешёвая и однозначная — ФИО с
    двоеточием в начале не бывает."""
    return (key or "").startswith("stud:")


#Карта «имя сущности → модель» для обобщённого синка push/pull.
#term_grades включены: десктоп теперь ведёт итоговые оценки/ведомости (аттестацию)
#наравне с вебом — данные общие через синк (ключ f|n|subject|year|semester).
class PushToken(Base):
    """Токен устройства для пуш-уведомлений (RuStore Push).

    НЕ входит в SYNC_MODELS намеренно: токен привязан к КОНКРЕТНОМУ телефону и на
    другие устройства пользователя ему ехать незачем — это чужая железка. К тому же
    синк выгружает таблицы целиком, и токены чужих устройств оказались бы на каждом ПК.

    Один пользователь = много устройств (телефон + планшет), поэтому ключ — сам токен.
    RuStore выдаёт новый токен при переустановке и иногда обновляет его сам, поэтому
    старые записи протухают: чистим их по last_seen (см. prune_stale)."""
    __tablename__ = "push_tokens"
    token = Column(String, primary_key=True)
    login = Column(String, index=True, default="")      #кому принадлежит устройство
    platform = Column(String, default="android")        #задел: ios/huawei
    #Когда токен последний раз подтверждён приложением. RuStore не сообщает об удалении
    #приложения — единственный признак «устройства больше нет» это молчание.
    last_seen = Column(String, default="", index=True)
    created_at = Column(String, default="")
    #Провалы доставки подряд. RuStore отвечает ошибкой на мёртвый токен; после нескольких
    #подряд запись убираем, чтобы не долбить сервис впустую.
    fail_count = Column(Integer, default=0)


class NotifyEvent(Base):
    """Событие для уведомления + КУДА по нему открыть экран.

    Зачем отдельная таблица, а не поля прямо в пуше. Тело уведомления идёт через
    серверы RuStore (третья сторона), поэтому предмет и балл туда класть нельзя —
    это данные об успеваемости конкретного студента. В пуш уходит только id события,
    а подробности приложение забирает у нас по защищённому каналу.

    Побочно это решает и «сгоревший токен»: приложение запоминает id, показывает вход,
    и после входа переход всё равно происходит — событие никуда не делось.

    В SYNC_MODELS НЕ входит: это серверная деталь доставки, десктопу она не нужна."""
    __tablename__ = "notify_events"
    id = Column(String, primary_key=True)               #uuid, он же уезжает в пуш
    login = Column(String, index=True, default="")      #владелец: чужому не отдаём
    #grade — новая оценка, grade_changed — оценку исправили, schedule_changed — правка
    #расписания ЕГО группы (о чужих группах не уведомляем: это и спам, и лишние сведения).
    kind = Column(String, default="grade")
    #Куда открыть. Предмет нужен, чтобы попасть сразу на нужную вкладку журнала.
    subject = Column(String, default="")
    lesson_id = Column(String, default="")
    created_at = Column(String, default="", index=True)
    read_at = Column(String, default="")                #когда открыли (для значка)

    #Текст письма для вкладки «Уведомления». Рендерится на СЕРВЕРЕ в момент создания, а
    #не на клиенте при показе. Причин две. Тон зависит от роли (студенту — дружелюбно,
    #преподавателю — официально), и три платформы неизбежно разъехались бы в
    #формулировках. И главное: история должна оставаться правдивой — правка шаблона не
    #имеет права задним числом переписывать то, что человек уже получил.
    #⚠️ В САМ ПУШ это не уходит (см. шапку rustore_push): тело идёт через посредника.
    title = Column(String, default="")
    body = Column(String, default="")
    #Подробности показа: {"old": "3", "new": "5"} у смены оценки, {"group": "К-24"} у
    #расписания. JSON, а не отдельные колонки — набор полей зависит от kind и будет расти.
    payload = Column(JSON, default=dict)

    #КТО отправил и ОДНОЙ ли рассылкой (вкладка «Отправленные» у препода/админа).
    #Письмо лежит строкой на КАЖДОГО получателя, поэтому без метки партии список
    #отправленного пришлось бы собирать по совпадению текста — а два одинаковых
    #объявления в разные дни слиплись бы в одно. batch_id делает группировку точной,
    #а счётчик получателей — настоящим, а не оценочным.
    #Пусто у автоматических писем (оценка, расписание): у них автора-человека нет.
    author_login = Column(String, index=True, default="")
    batch_id = Column(String, index=True, default="")


class DropoutRiskNotice(Base):
    """Последний УЖЕ РАЗОСЛАННЫЙ куратору уровень риска отчисления по студенту (3.6).

    Нужна ровно для одного: не слать одно и то же уведомление снова и снова. Риск
    пересчитывается при каждой выставленной оценке, и без памяти о прошлом уровне
    куратор получал бы «у студента высокий риск» после каждой двойки подряд — такие
    уведомления перестают читать через день, и функция превращается в шум.

    Правило простое: пишем письмо, только когда уровень СТАЛ ХУЖЕ отправленного в
    прошлый раз. Когда риск снижается, строка молча обновляется вниз — чтобы повторный
    рост снова считался новостью.

    В SYNC_MODELS НЕ входит: это серверная деталь доставки (как NotifyEvent), десктопу
    она не нужна — он считает риск сам из своей копии данных."""
    __tablename__ = "dropout_risk_notices"
    id = Column(String, primary_key=True)          #student_id — одна строка на студента
    group_name = Column(String, index=True, default="")
    level = Column(String, default="none")         #код уровня из dropout_risk.LEVELS
    score = Column(Integer, default=0)
    updated_at = Column(String, default="", index=True)


# ── Мессенджер (см. docs/done/MESSENGER-PLAN.md) ─────────────────────────────────────────
# ОТДЕЛЬНАЯ онлайн-подсистема: НЕ входит в SYNC_MODELS (реконсиляция «сервер=истина»
# вычистила бы кэш чатов; сообщения append-only, истина всегда на сервере — конфликтов
# LWW нет). Поэтому здесь НЕТ служебных полей синка (updated_at/deleted) — вместо
# tombstone'ов используем явные deleted_at/скрытие. id сообщений — АВТОИНКРЕМЕНТ: даёт
# бесплатный монотонный порядок и курсорную пагинацию (before/after), а межузловая
# детерминированность ключей тут не нужна (в отличие от синкуемых сущностей).

class Conversation(Base):
    """Беседа: личный чат / группа / канал / чат с модерацией."""
    __tablename__ = "conversations"
    id = Column(String, primary_key=True)              #conv:{uuid4}; direct — детерминир. ключ пары
    kind = Column(String, default="direct")            #direct | group | channel | moderation
    title = Column(String, default="")                 #для групп/каналов (у direct имя = собеседник)
    about = Column(String, default="")
    #Аватарка группы/канала (02.09.2026, просьба Влада «в группах всё ещё нельзя ставить
    #аватарки»). У direct её нет и быть не может: там лицо беседы — сам собеседник, и своя
    #картинка поверх его аватарки означала бы, что человек выглядит по-разному у разных
    #собеседников. Хранится ТЕМ ЖЕ форматом, что аватарка профиля (`data:image/…` либо
    #ссылка на CDN Klipy), и проверяется ТОЙ ЖЕ функцией — второй копии правила нет.
    #⚠️ Колонка НОВАЯ в СУЩЕСТВУЮЩЕЙ таблице → идемпотентный ALTER в db.py.
    avatar = Column(String, default="")
    owner_id = Column(String, index=True, default="")  #создатель (у direct/moderation пусто)
    is_public = Column(Boolean, default=False)         #канал виден в каталоге и свободно вступаем
    created_at = Column(String, default="", index=True)
    #§D12: автоматические системные каналы («Мои оценки», «Объявления группы», «Расписание»).
    #Их нельзя покинуть/переименовать обычными эндпоинтами — см. routers/messenger.py.
    is_system = Column(Boolean, default=False)
    system_kind = Column(String, default="")           #grades | announcements | schedule | ''


class ConversationParticipant(Base):
    """Участник беседы: роль + состояние прочтения/закрепления у этого пользователя."""
    __tablename__ = "conversation_participants"
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String, index=True, default="")
    user_id = Column(String, index=True, default="")
    role = Column(String, default="member")            #owner | admin | writer | member | reader
    joined_at = Column(String, default="")
    last_read_at = Column(String, default="")          #ISO последней прочитанной метки (непрочитанное)
    muted = Column(Boolean, default=False)             #без пушей по этой беседе
    pinned = Column(Boolean, default=False)            #закреплён ли сам чат в списке у этого юзера
    #«Удалить переписку У СЕБЯ»: сообщения СТАРШЕ этой метки пользователь больше не видит.
    #Массовый аналог MessageHidden — не плодит по строке на каждое сообщение. У собеседника
    #переписка остаётся (личное состояние, как last_read_at).
    cleared_at = Column(String, default="")
    #Граница очистки по НОМЕРУ сообщения. Метка времени для этого не годится: сообщение,
    #пришедшее в ТОТ ЖЕ тик часов, что и очистка, проваливалось бы под условие «строго
    #позже» и исчезало для пользователя НАВСЕГДА (на Windows тик ~16 мс — ловилось
    #плавающим падением теста). id автоинкрементный и от часов не зависит.
    #0 = очистки не было. Старые строки (очищены до этого поля) продолжают жить на
    #cleared_at — оба фильтра применяются вместе, см. routers/messenger.py.
    cleared_upto_id = Column(Integer, default=0)
    #Чат убран из списка до первой новой активности (тогда снимаем флаг и он вернётся).
    hidden = Column(Boolean, default=False)
    #Архив: чат убран из основного списка БЕЗ удаления (в отличие от hidden — не возвращается
    #сам новым сообщением, только явной расархивацией). Личное состояние, как pinned/muted.
    archived = Column(Boolean, default=False)
    #Роли и права (§ролей): если задана — переопределяет права, вычисленные из билдовой
    #`role`, см. routers/messenger.py::_permissions_for. Билдовая `role` остаётся (нужна
    #direct/channel-логике и порядку сортировки), custom_role_id — надстройка ТОЛЬКО для
    #групп/каналов. NULL = участник живёт на правах по умолчанию для своей билдовой role.
    custom_role_id = Column(String, index=True, default=None, nullable=True)
    #«/mute» модератора ЦЕЛИ (НЕ путать с `muted` выше — тот «я не хочу пуши от этого
    #чата», личная настройка САМОГО участника). silenced=True — участник не может писать
    #В ЭТУ беседу (см. send_message), независимо от того, что видит сам заглушённый.
    silenced = Column(Boolean, default=False)


class ConversationRole(Base):
    """Кастомная роль ВНУТРИ одной беседы (группы/канала) — Discord-style права.

    Права — фиксированный небольшой набор строк-ключей (kick/manage_roles/cmd_mute/
    cmd_clear), не гранулярная матрица: ровно то, что просили, без раздувания. Билдовые
    роли (owner/admin/writer/member/reader) НЕ переносим сюда — они остаются в
    ConversationParticipant.role как раньше; эта таблица — только для КАСТОМНЫХ ролей,
    которые создатель/обладатель manage_roles заводит сверху (см. _permissions_for)."""
    __tablename__ = "conversation_roles"
    id = Column(String, primary_key=True)              #crole:{uuid4}
    conversation_id = Column(String, index=True, default="")
    name = Column(String, default="")
    permissions = Column(JSON, default=list)            #["kick","manage_roles","cmd_mute","cmd_clear"]
    created_at = Column(String, default="")


class ConversationIgnore(Base):
    """«Игнорировать участника» — ЛИЧНОЕ решение зрителя, не модерация: сообщения от
    ignored_user_id в этой беседе клиент прячет за плейсхолдер «Скрыто» (с раскрытием по
    клику), сервер текст всё равно отдаёт как обычно (это удобство, а не приватность —
    как в Discord). Действует и на БУДУЩИЕ сообщения — поэтому не строка на сообщение,
    как MessageHidden (та таблица — для «удалить у себя» уже существующих сообщений)."""
    __tablename__ = "conversation_ignores"
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String, index=True, default="")
    viewer_id = Column(String, index=True, default="")
    ignored_user_id = Column(String, index=True, default="")


class ConversationInvite(Base):
    """Приглашение сотрудника в СТУДЕНЧЕСКУЮ группу — пока не принято, человек НЕ участник.

    Студенты заводят групповые чаты между собой сами (решение Ярослава), но затащить в
    такой чат преподавателя молча нельзя: это его рабочее время и его имя в переписке,
    в которой он не соглашался участвовать. Поэтому добавление сотрудника студентом даёт
    не участника, а ЗАЯВКУ — принял её человек, стал участником; отклонил, строка ушла.

    🔥 ПОЧЕМУ ОТДЕЛЬНАЯ ТАБЛИЦА, А НЕ ФЛАГ `pending` НА УЧАСТНИКЕ. Флаг пришлось бы не
    забыть в КАЖДОЙ выборке участников — а их два десятка (лента, список чатов, счётчик
    непрочитанного, «кто прочитал», рассылка по сокету, пуши, права, упоминания,
    модерация). Первая забытая выборка — это преподаватель, который получает сообщения
    беседы, куда ещё не согласился войти. Здесь забыть нечего по построению: пока
    приглашение не принято, строки участника не существует, и все существующие запросы
    правы без единой правки. Ровно та же причина, по которой ответ Вектора в общей
    беседе не пишется в БД под флагом видимости.

    ⚠️ НЕ в SYNC_MODELS — как весь мессенджер (§5.4), он живёт только на сервере.
    ⚠️ Пара (беседа, приглашённый) уникальна логикой ручки, а не индексом: повторное
    приглашение того же человека просто не создаёт вторую строку.
    """
    __tablename__ = "conversation_invites"
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String, index=True, default="")
    user_id = Column(String, index=True, default="")     #кого зовут
    invited_by = Column(String, default="")              #кто позвал (показываем в заявке)
    created_at = Column(String, default="")


class Message(Base):
    """Сообщение. created_at ставит СЕРВЕР (UTC). Порядок/курсор — по автоинкрементному id."""
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String, index=True, default="")
    sender_id = Column(String, index=True, default="")
    body = Column(String, default="")
    created_at = Column(String, default="", index=True)
    edited_at = Column(String, default="")
    deleted_at = Column(String, default="")            #«удалено у всех» (тумбстоун для всех)
    #⚠️ ИНДЕКС ОБЯЗАТЕЛЕН, и это не «на всякий случай» (09.09.2026, замер).
    #По этому полю считается значок «N ответов» (`_attach_reply_counts`), и делается
    #это при открытии ЛЮБОЙ беседы. Без индекса запрос сканировал таблицу целиком:
    #на 922 500 сообщениях замер дал 139 мс из 143 мс всего ответа — то есть 97 %
    #времени открытия чата уходило на один запрос, а число запросов при этом было
    #маленьким (семь) и потому не вызывало подозрений.
    reply_to_id = Column(Integer, default=0, index=True)   #ответ на сообщение (0 = нет)
    #«Ответить с цитатой» (05.09.2026): ВЫДЕЛЕННЫЙ кусок исходного сообщения, а не всё оно.
    #Пусто — обычный ответ (цитируется первая строка оригинала, как было).
    #
    #⚠️ Хранится СНИМКОМ, и это не дубль `reply_to_id`. Оригинал могут отредактировать или
    #удалить, а цитата обязана остаться тем, на что человек отвечал, — иначе ответ «нет,
    #это не так» повисает под изменившимся текстом. Тот же приём, что у пересылки
    #(`fwd_sender_name`) и у напоминаний.
    #
    #🔒 Сервер проверяет, что кусок РЕАЛЬНО есть в оригинале (см. messages.send_message).
    #Без проверки в цитату можно было бы вписать слова, которых собеседник не писал, и
    #выглядели бы они как его собственные — подделка, заметная только тому, кто пойдёт
    #сверять исходное сообщение.
    reply_quote = Column(String, default="")
    #Пересылка — снимок источника (оригинал может исчезнуть):
    fwd_from_sender_id = Column(String, default="")
    fwd_from_conv_id = Column(String, default="")
    fwd_from_created_at = Column(String, default="")
    fwd_sender_name = Column(String, default="")
    #Закрепление:
    pinned = Column(Boolean, default=False, index=True)
    pinned_at = Column(String, default="")
    pinned_by = Column(String, default="")
    #§D6: тип сообщения. text — обычное, system — служебное (вступил/вышел/закрепил/…).
    #Тело system-сообщения — шаблон "event:arg1:arg2" (см. routers/messenger._system).
    kind = Column(String, default="text")
    #Вложение (см. Attachment). Пусто у обычных сообщений.
    attachment_id = Column(String, default="", index=True)
    #§D1: формат тела. markdown — рендерить Markdown-lite, plain — как есть (старые строки).
    body_format = Column(String, default="markdown")
    #§D10: ключ идемпотентности от клиента (UUID). Повторный POST с тем же nonce возвращает
    #уже созданное сообщение — защита от дублей при ретраях/обрыве сети.
    client_nonce = Column(String, index=True, default="")
    #§D8: упоминания в тексте — [{"user_id":.., "silent": bool}, ...]. silent=true — пуш
    #НЕ шлём (только бейдж), обычное @Фамилия — шлём как всегда. Разбирается на отправке
    #(см. routers/messenger._parse_mentions), клиент подсвечивает по этому списку.
    mentions = Column(JSON, default=list)


class MessageReaction(Base):
    """§D3: реакция-эмодзи на сообщение. Ключ — (сообщение, пользователь, эмодзи): один
    пользователь может поставить разные эмодзи, но каждую — один раз. НЕ в SYNC_MODELS."""
    __tablename__ = "message_reactions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(Integer, index=True, default=0)
    user_id = Column(String, index=True, default="")
    emoji = Column(String, default="")
    created_at = Column(String, default="")


class Reminder(Base):
    """§D19: напоминание, созданное пользователем из сообщения переписки.

    Срабатывание СОБЫТИЙНОЕ, без планировщика: при запросе своих уведомлений сервер
    материализует все просроченные напоминания этого пользователя в обычные NotifyEvent
    (см. routers/me.py). Так фича не требует ни cron, ни фонового цикла — а на боевом
    одноядерном VPS лишний вечный поток стоит дороже, чем проверка одной строки по индексу.
    Плата за это — напоминание видно с момента, когда человек откроет приложение, но
    уведомления он и так читает только там.

    Текст храним СНИМКОМ, а не ссылкой на сообщение: автор может его отредактировать или
    удалить, а напоминание должно остаться тем, на что человек рассчитывал.

    НЕ входит в SYNC_MODELS: мессенджер — онлайн-подсистема, десктопу таблица не нужна."""
    __tablename__ = "reminders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    login = Column(String, index=True, default="")      #чьё напоминание
    conversation_id = Column(String, default="")        #куда вернуть по клику
    message_id = Column(Integer, default=0)
    text = Column(String, default="")                   #снимок текста сообщения
    remind_at = Column(String, index=True, default="")  #ISO UTC — когда напомнить
    created_at = Column(String, default="")
    fired_at = Column(String, default="")               #когда материализовано в событие


class Attachment(Base):
    """Вложение мессенджера: ТОЛЬКО метаданные. Самого файла у нас нет.

    Файл лежит в объектном хранилище и ходит туда-обратно МИМО нашего сервера (см.
    app/storage.py и docs/done/MESSENGER-ATTACHMENTS-PLAN.md): на боевой машине одно ядро и
    один процесс uvicorn, раздающий журнал, и прогонять через него файлы нельзя.

    ⚠️ `orphan_at` — когда у вложения не осталось ЖИВЫХ сообщений-ссылок. Физически
    объект стирает уборка по сроку, а не удаление сообщения: сообщения у нас удаляются
    тумбстоуном ради модерации (жалоба обязана показать оригинал, и автор не должен
    заметать следы, удалив сообщение после жалобы). Файл — часть сообщения, правило то же.

    ⚠️ Ссылок может быть НЕСКОЛЬКО: пересылка ссылается на тот же файл, а не копирует его.
    Поэтому сирота считается по числу живых ссылок, а не по «удалили сообщение».

    НЕ входит в SYNC_MODELS: мессенджер — онлайн-подсистема (§5.4)."""
    __tablename__ = "attachments"
    id = Column(String, primary_key=True)                 #att:<uuid4>
    conversation_id = Column(String, index=True, default="")
    uploader_id = Column(String, index=True, default="")
    name = Column(String, default="")                     #исходное имя, для скачивания
    size = Column(Integer, default=0)
    mime = Column(String, default="")
    storage_key = Column(String, default="")              #путь объекта в хранилище
    created_at = Column(String, default="")
    ready = Column(Boolean, default=False)                #загрузка подтверждена клиентом
    orphan_at = Column(String, default="")                #когда осталось ноль ссылок


class MessageEdit(Base):
    """§D11: история редактирования — текст ДО правки. Нужна модерации: автор мог
    исправить сообщение после жалобы, и тикет должен показывать оригинал. НЕ в SYNC_MODELS."""
    __tablename__ = "message_edits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(Integer, index=True, default=0)
    body_before = Column(String, default="")
    edited_at = Column(String, default="")


class MessageTemplate(Base):
    """Шаблон быстрого ответа преподавателя («Работа принята», «Исправьте ошибки») —
    личный набор, вставляется в композер одним кликом. НЕ в SYNC_MODELS (деталь той же
    онлайн-подсистемы мессенджера)."""
    __tablename__ = "message_templates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True, default="")
    body = Column(String, default="")
    position = Column(Integer, default=0)
    created_at = Column(String, default="")


class CuratorReport(Base):
    """§12 плана: отчёт куратора об успеваемости группы для родителей (команда `/отчет`
    в канале «Отчёты · Группа»). Кнопка «Отчёт №N» — «вечная»: не хранит вычисленные
    цифры (те считаются ЖИВЬЁМ через curator_report.collect_group при каждом открытии),
    а только ГРАНИЦУ снимка — термин + дату создания включительно, чтобы отчёт не «дрейфовал»
    при появлении новых занятий после его выпуска. `archived` ставит rollover семестра
    (см. routers/web.py::admin_term_rollover) — новый /отчет уже не берёт старый термин,
    а старые кнопки в списке помечаются как архивные (не удаляются, см. §12 запрос
    «кнопки должны быть вечными»). НЕ в SYNC_MODELS (та же онлайн-подсистема мессенджера)."""
    __tablename__ = "curator_reports"
    id = Column(String, primary_key=True)          #rpt:{group}|{seq}
    group_name = Column(String, index=True, default="")
    seq = Column(Integer, default=0)               #номер по порядку от 1, свой на группу
    year = Column(String, default="")              #термин НА МОМЕНТ создания отчёта
    semester = Column(Integer, default=0)
    cutoff_date = Column(String, default="")       #"ДД.ММ.ГГГГ" — занятия ПОСЛЕ неё не входят
    conversation_id = Column(String, default="")
    message_id = Column(Integer, default=0)
    created_by = Column(String, default="")        #id куратора
    created_at = Column(String, default="")
    archived = Column(Boolean, default=False)


class UserStatus(Base):
    """§D7: статус пользователя ПОВЕРХ presence (online/оффлайн уже даёт events.online()).
    kind='' — статуса нет, показываем просто presence; иначе dnd/studying/away — накладка
    поверх неё. custom_text — только у преподавателя (см. routers/messenger.py). НЕ в
    SYNC_MODELS: серверная деталь той же online-подсистемы, что и presence."""
    __tablename__ = "user_statuses"
    user_id = Column(String, primary_key=True)
    kind = Column(String, default="")
    custom_text = Column(String, default="")
    updated_at = Column(String, default="")


class BlockedUser(Base):
    """Блокировка ЧЕЛОВЕК↔ЧЕЛОВЕК (личное решение, НЕ модерация). Заблокированный не может
    написать заблокировавшему в личку и не видит его сообщений; беседа перестаёт создаваться
    заново. НЕ в SYNC_MODELS — как весь мессенджер.

    ⚠️ ОТЛИЧАТЬ ОТ СОСЕДЕЙ, иначе разведётся четвёртая копия одного и того же:
    • `MutedUser` — наказание МОДЕРАЦИЕЙ, действует на весь продукт;
    • `ConversationIgnore` — «скрыть сообщения этого участника У СЕБЯ В ЭТОЙ беседе»,
      текст сервер всё равно отдаёт, прячет клиент, писать человек по-прежнему может;
    • `MessageHidden` — «удалить у себя» одно уже существующее сообщение;
    • здесь — ЗАПРЕТ ПИСАТЬ, и проверяет его СЕРВЕР.

    ⚠️ Блокировка ОДНОСТОРОННЯЯ и МОЛЧАЛИВАЯ: заблокированному не сообщают. Иначе она
    превращается в способ объявить человеку «ты мне неприятен» — то же решение, что у
    Telegram и Discord. Поэтому и отказ при отправке общий («сообщение не доставлено»),
    а не «вас заблокировали».
    ⚠️ Блокировка НЕ действует на общие группы и каналы: там человек пишет всем сразу, и
    запрет означал бы, что один участник вычёркивает другого из учебной беседы. Для этого
    есть модерация, а для «не хочу видеть» — `ConversationIgnore`."""
    __tablename__ = "blocked_users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    blocker_id = Column(String, index=True, default="")   #кто заблокировал
    blocked_id = Column(String, index=True, default="")   #кого заблокировали
    created_at = Column(String, default="")


class UserReport(Base):
    """Жалоба на ПРОФИЛЬ человека (не на сообщение) — отдельная очередь модерации.

    🔑 ОТДЕЛЬНАЯ ТАБЛИЦА, А НЕ `target_kind` В `MessageReport`, и причина не в чистоте.
    У жалобы на сообщение есть `message_id`, `conversation_id` и СНИМОК текста — то, ради
    чего её и читают. У жалобы на профиль ничего этого нет и быть не может, зато есть своё:
    на какое поле профиля жалуются (имя, «о себе», аватарка, баннер) и его снимок. Сложив их
    в одну таблицу, мы получили бы строку, где половина колонок всегда пуста, и КАЖДАЯ
    выборка очереди обязана была бы помнить, какая именно половина. Первая забывшая — это
    жалоба, показанная модератору без содержимого.

    ⚠️ Снимок обязателен по той же причине, что у `MessageReport`: пока тикет ждёт разбора,
    человек успевает переписать «о себе», и модератор увидит невинный текст.
    ⚠️ Не в SYNC_MODELS (весь мессенджер и модерация — серверные)."""
    __tablename__ = "user_reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    reported_user_id = Column(String, index=True, default="")
    reporter_id = Column(String, index=True, default="")
    reason_code = Column(String, default="other")
    description = Column(String, default="")
    #На что именно жалуются и как оно выглядело в момент жалобы.
    field = Column(String, default="profile")          #profile|name|bio|avatar|banner
    snapshot = Column(String, default="")
    created_at = Column(String, default="", index=True)
    status = Column(String, default="open", index=True)   #open|in_review|resolved|dismissed
    handled_by = Column(String, default="")
    handled_at = Column(String, default="")
    resolution_note = Column(String, default="")


class MessageHidden(Base):
    """«Удалить у себя»: сообщение скрыто у конкретного пользователя (на других не влияет)."""
    __tablename__ = "message_hidden"
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(Integer, index=True, default=0)
    user_id = Column(String, index=True, default="")


class MessageReport(Base):
    """Жалоба на сообщение = ТИКЕТ модерации (см. MESSENGER-PLAN.md §6.7, §10). Хранит СНИМОК
    текста, чтобы контекст жил, даже если сообщение потом удалят. НЕ в SYNC_MODELS."""
    __tablename__ = "message_reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(Integer, index=True, default=0)
    #На ЧТО жалоба. Появилось с активностями (PLAN-ACTIVITIES §8.3): преподаватель может
    #пожаловаться админу на отзыв среза понимания, и это НЕ сообщение — `message_id` там
    #хранит id строки activity_feedback. Умолчание "message" оставляет все прежние тикеты
    #ровно тем, чем они были. ⚠️ Колонка в СУЩЕСТВУЮЩУЮ таблицу — нужен идемпотентный
    #ALTER в db.py (`create_all` досоздаёт только целые таблицы), см. §10 CLAUDE.md.
    target_kind = Column(String, default="message")    #message | activity_feedback
    conversation_id = Column(String, default="")
    message_snapshot = Column(String, default="")      #текст на момент жалобы
    reporter_id = Column(String, index=True, default="")
    reported_user_id = Column(String, index=True, default="")
    reason_code = Column(String, default="other")      #spam|harassment|threats|fraud|illegal|flood|other
    description = Column(String, default="")
    created_at = Column(String, default="", index=True)
    status = Column(String, default="open", index=True)  #open | in_review | resolved | dismissed
    handled_by = Column(String, default="")
    handled_at = Column(String, default="")
    resolution_note = Column(String, default="")


class MutedUser(Base):
    """Глобальный мьют пользователя МОДЕРАЦИЕЙ: он не может отправлять сообщения и создавать
    беседы в мессенджере (см. routers/messenger.py). Отдельная СЕРВЕРНАЯ таблица (НЕ в
    SYNC_MODELS): состояние мьюта не должно уезжать на десктоп обычным pull — мессенджер
    целиком онлайновый, а _row_to_dict синка отдал бы неизвестную десктопу колонку. Наличие
    строки = замьючен; снятие мьюта = удаление строки."""
    __tablename__ = "muted_users"
    user_id = Column(String, primary_key=True)
    muted_by = Column(String, default="")              #логин того, кто наложил мьют
    muted_by_role = Column(String, default="")         #admin | moderator — кто именно
    muted_at = Column(String, default="")
    #🔥 СРОК. Пустая строка = бессрочно (прежнее поведение всех уже наложенных мьютов:
    #у существующих строк колонки нет, ALTER даёт им "" — то есть обновление не
    #освобождает молча никого, кого уже наказали). Иначе ISO UTC, и по его наступлении
    #мьют снимается САМ — см. _common._is_muted, где это единственная точка проверки.
    #⚠️ Планировщика здесь нет и не надо: срок проверяется при ЧТЕНИИ, тем же приёмом,
    #что у напоминаний (_fire_due_reminders). Фоновый поток на одноядерном бою — цена
    #заметно выше пользы, а «снять вовремя» и «снять к моменту, когда человек написал»
    #для мьюта неотличимы: узнать о снятии можно только попыткой отправить сообщение.
    muted_until = Column(String, default="")
    reason = Column(String, default="")                #видна САМОМУ замьюченному


class UserMFA(Base):
    """Второй фактор входа (одноразовый код из приложения). СЕРВЕРНАЯ таблица.

    ⚠️ НЕ в SYNC_MODELS, и это решение, а не недосмотр. Синхронизация разослала бы
    секрет второго фактора на КАЖДЫЙ компьютер, где человек открывал программу, —
    то есть размножила бы то единственное, что должно существовать в одном месте.
    Побочно: у синка нет понятия «эта колонка не для десктопа», он отдаёт строку
    целиком.

    Следствие названо честно: ОФЛАЙНОВЫЙ вход администратора в программе остаётся
    однофакторным. Его вторым фактором служит сама машина — она проходит барьер
    устройства (§4.11, для десктопа он жёсткий) и держит локальную базу под
    шифрованием ключом этого компьютера. Онлайн-вход, то есть любой вход через
    боевой сервер, второй фактор требует.

    Новая ТАБЛИЦА заводится `create_all` сама — ручной ALTER нужен только для новой
    КОЛОНКИ в существующей таблице (см. db.py).
    """
    __tablename__ = "user_mfa"
    user_id = Column(String, primary_key=True)
    #Секрет base32. Лежит в базе, которая на бою зашифрована целиком (SQLCipher).
    secret = Column(String, default="")
    #Пока не подтверждён кодом — фактор НЕ действует. Иначе человек, начавший
    #настройку и закрывший вкладку, запер бы себя навсегда: секрет в базе есть,
    #а в телефоне его нет.
    confirmed_at = Column(String, default="")
    #Номер последнего использованного шага времени. Защита от повтора: код живёт
    #30 секунд, и подсмотренный через плечо иначе годился бы весь этот срок.
    last_step = Column(Integer, default=0)
    #Коды восстановления — ТОЛЬКО хеши, json-список. Открытый текст здесь был бы
    #вторым паролем, лежащим рядом с первым.
    recovery_hashes = Column(JSON, default=list)
    recovery_used = Column(Integer, default=0)
    created_at = Column(String, default="")
    #Кто и когда сбрасывал — для журнала аудита и разбора «почему у меня не просит».
    reset_by = Column(String, default="")
    reset_at = Column(String, default="")


class UserNote(Base):
    """Личная заметка о человеке (Discord-style «Notes») — видна ТОЛЬКО автору, даже если
    заметка о самом себе (self-note, «памятка себе» на своей же карточке профиля — так
    просил заказчик, отдельно не выпиливаем этот случай). Ключ (author_id, about_user_id):
    один автор — одна заметка про конкретного человека, повторное сохранение перезаписывает.
    НЕ в SYNC_MODELS: та же онлайн-подсистема карточки профиля/мессенджера, что и остальное
    в этом файле ниже User/Group — офлайн-десктопу заметка не нужна."""
    __tablename__ = "user_notes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    author_id = Column(String, index=True, default="")
    about_user_id = Column(String, index=True, default="")
    text = Column(String, default="")
    updated_at = Column(String, default="")


class Activity(Base):
    """Активность в беседе — совместное действие участников (docs/done/PLAN-ACTIVITIES.md).

    Живёт в БЕСЕДЕ, а не в учебной группе: у `Conversation` связи с `Group` нет вовсе,
    чат-группа это просто список людей. НЕ в SYNC_MODELS — как весь мессенджер: офлайн-
    десктопу активности не нужны, внутрь программы они приезжают прокси-префиксом
    `/web/messenger` (desktop/local_api.py).

    ⚠️ `id` — uuid, а НЕ составной ключ с именем беседы/группы. Имя группы в пути URL уже
    стоило поломки всех отчётов на группах со слэшем («К74/1» → `%2F` → Starlette
    раскодирует ДО роутинга, и путь распадается на лишний сегмент). В путь идёт только uuid.
    """
    __tablename__ = "activities"
    id = Column(String, primary_key=True)              #act:{uuid4().hex}
    conversation_id = Column(String, index=True, default="")
    kind = Column(String, default="")                  #board|quiz|contest|poll|pulse|timer
    host_id = Column(String, index=True, default="")   #кто запустил
    title = Column(String, default="")                 #что показывать в карточке ленты
    params = Column(JSON, default=dict)                #параметры категории
    status = Column(String, index=True, default="running")   #running | finished
    started_at = Column(String, default="")            #ISO, ставит СЕРВЕР
    finished_at = Column(String, default="")
    message_id = Column(Integer, default=0)            #карточка-кнопка в ленте


class QuizSet(Base):
    """Викторина — набор вопросов в библиотеке преподавателя. НЕ в SYNC_MODELS."""
    __tablename__ = "quiz_sets"
    id = Column(String, primary_key=True)              #quiz:{uuid4().hex}
    author_id = Column(String, index=True, default="")
    title = Column(String, default="")
    description = Column(String, default="")
    tags = Column(JSON, default=list)                  #нормализованные (регистр/пробелы)
    #private — только автору; college — всем преподавателям колледжа; stock — стартовые
    #(ознакомительные, чтобы первый открывший не увидел пустой экран).
    visibility = Column(String, index=True, default="private")
    #Цепочка копий: оригинал → копия 1 → копия 2. Чужую викторину нельзя править — её
    #копируют себе, и parent_id хранит, откуда она взялась.
    parent_id = Column(String, index=True, default="")
    #Ограничение времени на ВЕСЬ тест, секунды. 0 — без ограничения (так было всегда).
    #Счётчик у студента идёт от ОТКРЫТИЯ теста, а не от запуска активности: человек мог
    #зайти в беседу через десять минут после старта, и общий отсчёт съел бы его время.
    time_limit_s = Column(Integer, default=0)
    #Для какой категории набор: 'quiz' (обычная викторина) или 'contest' (соревнование).
    #Библиотеки РАЗДЕЛЬНЫЕ — у категорий разные допустимые типы заданий и разный смысл,
    #и общий список заставлял преподавателя гадать, что откуда запускается.
    kind = Column(String, index=True, default="quiz")
    created_at = Column(String, default="")
    updated_at = Column(String, default="")
    deleted = Column(Boolean, default=False, index=True)   #мягкое удаление


class QuizQuestion(Base):
    """Вопрос викторины. `type`: single|multi|order|match (см. PLAN-ACTIVITIES §8.4)."""
    __tablename__ = "quiz_questions"
    id = Column(String, primary_key=True)              #qq:{uuid4().hex}
    quiz_id = Column(String, index=True, default="")
    order_no = Column(Integer, default=0)
    type = Column(String, default="single")
    text = Column(String, default="")
    points = Column(Integer, default=1)


class QuizOption(Base):
    """Вариант ответа.

    🔒 `is_correct` / `correct_position` / `match_key` — КЛЮЧ. Он не покидает сервер для
    студента: эндпоинт выдачи вопросов имеет два режима (см. routers/activities.py
    `_question_out`), и режим студента эти поля не кладёт вовсе. Проверять ответы на
    клиенте нельзя ни при каких условиях — для локальной проверки нужен ключ, а любой
    студент прочитает его в инструментах разработчика до начала."""
    __tablename__ = "quiz_options"
    id = Column(String, primary_key=True)              #qo:{uuid4().hex}
    question_id = Column(String, index=True, default="")
    order_no = Column(Integer, default=0)
    text = Column(String, default="")
    is_correct = Column(Boolean, default=False)        #single|multi
    match_key = Column(String, default="")             #match: с чем сопоставляется
    correct_position = Column(Integer, default=0)      #order: верное место (1..N)


class ActivityResult(Base):
    """Итог участника по активности.

    Уникальность по паре (activity_id, user_id) держит КОД (upsert в
    `activity_grading.save_result`), а не составной первичный ключ: строка нужна с
    автоинкрементным id для единообразия с остальными таблицами мессенджера. Смысл тот
    же — повторная отправка ЗАМЕНЯЕТ результат, а не создаёт второй: дрогнувшая сеть не
    должна сдваивать результат теста."""
    __tablename__ = "activity_results"
    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(String, index=True, default="")
    user_id = Column(String, index=True, default="")
    score = Column(Float, default=0.0)
    correct_count = Column(Integer, default=0)
    total_count = Column(Integer, default=0)
    answers = Column(JSON, default=dict)               #что именно ответил — для разбора
    finished_at = Column(String, default="")
    duration_ms = Column(Integer, default=0)


class ActivityFeedback(Base):
    """Срез понимания (1–10) + «что именно непонятно».

    🔑 `user_id` ХРАНИТСЯ, но преподавателю не отдаётся НИКОГДА (`_feedback_out` не кладёт
    его ни в одно поле). Хранить обязательно: у отзыва есть кнопка жалобы, и жалоба уходит
    АДМИНУ — без автора она никуда не ведёт. Скрывает автора интерфейс преподавателя, а не
    база, поэтому студенту пишем «преподаватель не увидит, кто это написал», а не
    «анонимно»: первое правда, второе нет, и первая же разобранная жалоба это вскроет."""
    __tablename__ = "activity_feedback"
    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(String, index=True, default="")
    user_id = Column(String, index=True, default="")
    score_1_10 = Column(Integer, default=0)
    reason_code = Column(String, default="")
    custom_text = Column(String, default="")
    created_at = Column(String, default="")


class BoardArtifact(Base):
    """Сохранённая доска — ШТРИХАМИ, а не картинкой.

    Файлового хранилища в проекте нет вообще (картинки живут data:-строками в JSON), и
    PNG доски — это сотни килобайт base64 в теле сообщения: раздутая таблица и лишний
    трафик при каждой загрузке ленты. Плюс продолжить рисование можно ТОЛЬКО штрихами —
    поверх растра рисуют заново."""
    __tablename__ = "board_artifacts"
    id = Column(String, primary_key=True)              #board:{uuid4().hex}
    conversation_id = Column(String, index=True, default="")
    activity_id = Column(String, index=True, default="")
    author_id = Column(String, default="")
    sheet = Column(String, default="blank")            #blank | grid | lined
    strokes = Column(JSON, default=list)
    title = Column(String, default="")
    created_at = Column(String, default="")


def direct_conversation_id(uid_a: str, uid_b: str) -> str:
    """Детерминированный id личного чата — чтобы пара не плодила дубли (ищем по нему перед
    созданием). Порядок id участников не важен: сортируем."""
    lo, hi = sorted([uid_a or "", uid_b or ""])
    return f"direct:{lo}|{hi}"


# ── КУРСЫ (учебные курсы: структура, материалы, задания; аналог «Курсы» портала) ──────
# 🔒 СЕРВЕРНАЯ подсистема, как мессенджер: НЕ в SYNC_MODELS. Курсы контентные (материалы —
# файлы/ссылки), офлайн-синк им не нужен и файлы по нему возить нельзя; на десктопе-онлайн
# приходят через тот же REST. Все пять — НОВЫЕ таблицы, поэтому create_all заводит их сам
# (ручной идемпотентный ALTER в db.py нужен ТОЛЬКО для новых КОЛОНОК в существующих таблицах,
# см. §10 CLAUDE.md — здесь его заводить не надо).
class Course(Base):
    """Учебный курс по предмету для группы. Автор(ы) — преподаватель(и) (CourseAuthor).
    Виден студентам своей группы, авторам и админу. Архивируется (`archived`), жёстко не
    удаляется: на материалы/задания могут быть ссылки, а история курса — учебный документ."""
    __tablename__ = "courses"
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, default="", index=True)
    subject = Column(String, default="", index=True)      #предмет курса
    group_name = Column(String, default="", index=True)   #для какой учебной группы
    description = Column(String, default="")
    created_by = Column(String, default="")               #user.id создателя
    created_at = Column(String, default="")
    updated_at = Column(String, default="")
    archived = Column(Boolean, default=False)


class CourseAuthor(Base):
    """Автор курса (преподаватель). M2M курс↔преподаватель; создатель добавляется первым."""
    __tablename__ = "course_authors"
    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, index=True)
    teacher_id = Column(String, default="", index=True)   #user.id преподавателя
    added_at = Column(String, default="")


class CourseSection(Base):
    """Раздел структуры курса — нумерованный пункт списка «Структура курса»."""
    __tablename__ = "course_sections"
    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, index=True)
    title = Column(String, default="")
    position = Column(Integer, default=0)                 #порядок (№ п/п)
    created_at = Column(String, default="")


class CourseMaterial(Base):
    """Материал курса. MVP — `kind='link'` (url) и `kind='text'` (пояснение в title/url);
    `kind='file'` зарезервирован под загрузку файлов (следующий шаг — через уже готовую
    инфраструктуру вложений `app/storage.py`, ту же, что у мессенджера)."""
    __tablename__ = "course_materials"
    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, index=True)
    section_id = Column(Integer, default=0)               #0 — материал вне раздела
    kind = Column(String, default="link")                #link | file | text
    title = Column(String, default="")
    url = Column(String, default="")                     #для link
    file_path = Column(String, default="")               #для file (пока не используется)
    position = Column(Integer, default=0)
    created_at = Column(String, default="")


class CourseAssignment(Base):
    """Задание курса (таблица «Задания»): что сдать и до какого срока, кто выдал."""
    __tablename__ = "course_assignments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, index=True)
    title = Column(String, default="")
    description = Column(String, default="")
    due_date = Column(String, default="")                #срок ('ДД.ММ.ГГГГ' или ISO)
    teacher_id = Column(String, default="")              #кто выдал (user.id)
    url = Column(String, default="")                     #прикреплённая ссылка (файл — потом)
    created_at = Column(String, default="")


class UserAchievement(Base):
    """Открытая пользователем ачивка (пасхалки, docs/done/PLAN-EASTER-EGGS.md).

    ⚠️ Таблицы-справочника ачивок здесь НЕТ намеренно, хотя план её называл. Название,
    описание, значок и редкость — статика, и держать её в БД значит завести вторую
    копию того, что уже лежит в `web/src/config/achievements.js`, плюс миграцию на
    каждую новую пасхалку и перевод, которого в БД взяться неоткуда. Сервер хранит
    только ФАКТ открытия и сверяет id с белым списком `easter_eggs.ACHIEVEMENT_IDS` —
    иначе в профиль можно было бы протащить произвольную строку и показать её другим.

    ОНЛАЙН-ONLY: не в SYNC_MODELS, как весь мессенджер. Офлайн ачивки не выдаются.

    `showcase` — «показывать в моём профиле». Это ПУБЛИЧНОЕ поле: отмеченные ачивки
    видят другие люди, открывшие карточку. Поэтому список того, что показывать,
    выбирает сам человек, а не мы за него."""
    __tablename__ = "user_achievements"
    user_id = Column(String, primary_key=True, index=True)
    achievement_id = Column(String, primary_key=True)
    unlocked_at = Column(String, default="")             #ISO UTC, ставит сервер
    showcase = Column(Boolean, default=False)


class EasterEggLog(Base):
    """След срабатывания пасхалки — против того, чтобы одна и та же лезла каждый день.

    Живёт отдельно от ачивки: ачивка выдаётся ОДИН раз, а пасхалка может сработать
    снова (у неё свой суточный кулдаун). Строк будет много, поэтому уборка старше
    полугода — в `retention.py`, рядом с надгробиями и уведомлениями."""
    __tablename__ = "easter_egg_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True)
    egg_id = Column(String, index=True)
    triggered_at = Column(String, default="")            #ISO UTC
    created_ts = Column(Integer, default=0, index=True)  #unix — быстрый фильтр уборки


SYNC_MODELS = {
    "users": User,
    "groups": Group,
    "subjects": Subject,
    "lessons": Lesson,
    "grades": Grade,
    "term_grades": TermGrade,
    "schedule_overrides": ScheduleOverride,   #правки расписания — общие веб↔десктоп
    "subject_hours": SubjectHours,            #плановые часы — нужны нативному журналу ПК
    "student_subgroups": StudentSubgroup,     #раздельное обучение — нужны нативному журналу ПК
    "zet_thresholds": ZetThreshold,           #порог перевода — без него «ограничений нет»
    "config": ConfigKV,
}
