"""
test_db_migrations.py — идемпотентные мини-миграции ALTER TABLE (server/app/db.py).

Обычный тестовый фикстура `client` пересоздаёт схему через Base.metadata.create_all,
которая для СВЕЖЕЙ таблицы включает ВСЕ текущие колонки сразу — поэтому ветка «колонки
не было, добавляем ALTER-ом» в обычных тестах никогда не срабатывает и ловит регрессии
только на боевой БД, где таблица появилась РАНЬШЕ новых колонок. Ровно так один раз уже
проехало в бою (§ролей, 3.1.5): create_all не досоздаёт колонки в СУЩЕСТВУЮЩЕЙ таблице,
conversation_participants на проде осталась без custom_role_id/silenced до первого
рестарта после деплоя миграции — conversation_info/send_message падали бы «no such
column» на любой группе/канале. Здесь эта ветка эмулируется явно.
"""
from sqlalchemy import text, inspect

from app.db import (engine, _ensure_participant_state_columns,
                    _ensure_subject_hours_teacher_column, _ensure_subject_hours_zet_column,
                    _ensure_notify_event_columns, _ensure_group_category_column,
                    _ensure_user_password_set_at_column, _ensure_user_birthday_column,
                    _ensure_message_report_target_column, _ensure_audit_chain_columns,
                    _ensure_conversation_avatar_column, _ensure_message_addon_columns)


def test_ensure_participant_state_columns_adds_role_columns_to_old_schema(client):
    """Таблица без custom_role_id/silenced (схема ДО §ролей) — миграция должна дописать
    обе колонки ALTER-ом, а не молча оставить их отсутствующими."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE conversation_participants"))
        conn.execute(text("""CREATE TABLE conversation_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id VARCHAR, user_id VARCHAR,
            role VARCHAR, joined_at VARCHAR, last_read_at VARCHAR,
            muted BOOLEAN DEFAULT 0, pinned BOOLEAN DEFAULT 0, cleared_at VARCHAR DEFAULT '',
            hidden BOOLEAN DEFAULT 0, archived BOOLEAN DEFAULT 0,
            cleared_upto_id INTEGER DEFAULT 0
        )"""))
    #Сбросить пул: у SQLite соединение, открытое ДО этого DROP/CREATE (например, во время
    #старта приложения в фикстуре `client`), держит снимок схемы с прошлой транзакции —
    #без dispose() следующий PRAGMA table_info на ТОЙ ЖЕ пуловой коннекции видит СТАРУЮ
    #схему и ALTER падает «duplicate column». В бою это не воспроизводится: миграция
    #гоняется ОДИН раз на холодном движке сразу после create_all, старых соединений в
    #пуле ещё нет — здесь дублируем именно эту (тёплый пул) особенность теста, не бага.
    engine.dispose()
    _ensure_participant_state_columns()
    cols = {c["name"] for c in inspect(engine).get_columns("conversation_participants")}
    assert "custom_role_id" in cols and "silenced" in cols


def test_ensure_participant_state_columns_is_idempotent(client):
    """Повторный вызов на уже мигрированной таблице не падает (обычный старт сервера
    гоняет эти функции при КАЖДОМ запуске, не только один раз)."""
    _ensure_participant_state_columns()
    _ensure_participant_state_columns()
    cols = {c["name"] for c in inspect(engine).get_columns("conversation_participants")}
    assert "custom_role_id" in cols and "silenced" in cols


def test_ensure_subject_hours_teacher_column_adds_to_old_schema(client):
    """Таблица без teacher_id (схема ДО назначений препод↔предмет↔группа) — миграция
    должна дописать колонку ALTER-ом. Тот же прогретый-пул нюанс, что и выше — dispose()."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE subject_hours"))
        conn.execute(text("""CREATE TABLE subject_hours (
            id VARCHAR PRIMARY KEY, group_name VARCHAR, subject VARCHAR,
            year VARCHAR, semester INTEGER DEFAULT 0, hours_total INTEGER DEFAULT 0,
            updated_at VARCHAR DEFAULT '', deleted BOOLEAN DEFAULT 0
        )"""))
    engine.dispose()
    _ensure_subject_hours_teacher_column()
    cols = {c["name"] for c in inspect(engine).get_columns("subject_hours")}
    assert "teacher_id" in cols
    _ensure_subject_hours_teacher_column()  # идемпотентность — второй вызов не падает


def test_ensure_subject_hours_zet_column_adds_to_old_schema(client):
    """Таблица без zet (схема ДО ЗЕТ, docs/PLAN-ZET.md) — миграция должна дописать
    колонку ALTER-ом, как и teacher_id выше."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE subject_hours"))
        conn.execute(text("""CREATE TABLE subject_hours (
            id VARCHAR PRIMARY KEY, group_name VARCHAR, subject VARCHAR,
            year VARCHAR, semester INTEGER DEFAULT 0, hours_total INTEGER DEFAULT 0,
            updated_at VARCHAR DEFAULT '', deleted BOOLEAN DEFAULT 0,
            teacher_id VARCHAR DEFAULT ''
        )"""))
    engine.dispose()
    _ensure_subject_hours_zet_column()
    cols = {c["name"] for c in inspect(engine).get_columns("subject_hours")}
    assert "zet" in cols
    _ensure_subject_hours_zet_column()  # идемпотентность — второй вызов не падает


def test_ensure_group_category_column_adds_to_old_schema(client):
    """Таблица groups без category (схема ДО категорий расписания портала) — миграция
    должна дописать колонку ALTER-ом, как teacher_id/zet выше."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE groups"))
        conn.execute(text("""CREATE TABLE groups (
            id VARCHAR PRIMARY KEY, name VARCHAR, subjects TEXT DEFAULT '[]',
            updated_at VARCHAR DEFAULT '', deleted BOOLEAN DEFAULT 0,
            specialty_code VARCHAR, enrollment_year INTEGER
        )"""))
    engine.dispose()
    _ensure_group_category_column()
    cols = {c["name"] for c in inspect(engine).get_columns("groups")}
    assert "category" in cols
    _ensure_group_category_column()  # идемпотентность — второй вызов не падает


def test_ensure_notify_event_columns_adds_author_columns_to_old_schema(client):
    """Таблица без author_login/batch_id (схема ДО вкладки «Отправленные»).

    Ровно тот случай из шапки файла: notify_events живёт на проде с самых пушей об
    оценках, и create_all новые столбцы в неё не добавит. Без ALTER-а вкладка
    «Отправленные» падала бы «no such column: author_login» у каждого преподавателя, а
    заодно сломалась бы ЛЮБАЯ отправка уведомления — запись события идёт тем же путём."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE notify_events"))
        conn.execute(text("""CREATE TABLE notify_events (
            id VARCHAR PRIMARY KEY, login VARCHAR, kind VARCHAR,
            subject VARCHAR, lesson_id VARCHAR, created_at VARCHAR, read_at VARCHAR,
            title VARCHAR DEFAULT '', body VARCHAR DEFAULT '', payload JSON
        )"""))
    engine.dispose()          #см. пояснение про тёплый пул в тесте выше
    _ensure_notify_event_columns()
    cols = {c["name"] for c in inspect(engine).get_columns("notify_events")}
    assert "author_login" in cols and "batch_id" in cols


def test_ensure_notify_event_columns_is_idempotent(client):
    """Второй прогон на уже актуальной схеме не должен падать «duplicate column»:
    миграции гоняются при КАЖДОМ старте сервера, а не один раз."""
    engine.dispose()
    _ensure_notify_event_columns()
    _ensure_notify_event_columns()


def test_ensure_user_password_set_at_column_adds_to_old_schema(client):
    """Таблица users без password_set_at (схема ДО даты выдачи пароля).

    Именно тот случай из шапки файла: users живёт на проде с самого начала, и
    create_all новую колонку в неё не добавит. Без ALTER-а список студентов
    (`/web/admin/students` читает `u.password_set_at`) падал бы «no such column» у
    администратора сразу после деплоя — то есть на самом видном экране."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE users"))
        conn.execute(text("""CREATE TABLE users (
            id VARCHAR PRIMARY KEY, role VARCHAR NOT NULL, login VARCHAR,
            password_hash VARCHAR, full_name VARCHAR, surname VARCHAR, name VARCHAR,
            patronymic VARCHAR, group_name VARCHAR, subjects TEXT DEFAULT '[]',
            group_assignments TEXT DEFAULT '{}', curated_groups TEXT DEFAULT '[]',
            prefs TEXT DEFAULT '{}', updated_at VARCHAR DEFAULT '',
            deleted BOOLEAN DEFAULT 0
        )"""))
    engine.dispose()
    _ensure_user_password_set_at_column()
    cols = {c["name"] for c in inspect(engine).get_columns("users")}
    assert "password_set_at" in cols
    _ensure_user_password_set_at_column()  # идемпотентность — второй вызов не падает


def test_ensure_user_birthday_column_adds_to_old_schema(client):
    """Таблица users без birthday (схема ДО поздравления с днём рождения).

    Тот же случай, что и с password_set_at: users живёт на бою с самого начала, и
    create_all колонку в неё не добавит. Без ALTER-а список студентов у админа и
    КАЖДАЯ карточка профиля (`_safe_user` читает `u.birthday`) падали бы
    «no such column» сразу после деплоя."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE users"))
        conn.execute(text("""CREATE TABLE users (
            id VARCHAR PRIMARY KEY, role VARCHAR NOT NULL, login VARCHAR,
            password_hash VARCHAR, full_name VARCHAR, surname VARCHAR, name VARCHAR,
            patronymic VARCHAR, group_name VARCHAR, subjects TEXT DEFAULT '[]',
            group_assignments TEXT DEFAULT '{}', curated_groups TEXT DEFAULT '[]',
            prefs TEXT DEFAULT '{}', updated_at VARCHAR DEFAULT '',
            deleted BOOLEAN DEFAULT 0
        )"""))
    engine.dispose()
    _ensure_user_birthday_column()
    cols = {c["name"] for c in inspect(engine).get_columns("users")}
    assert "birthday" in cols
    _ensure_user_birthday_column()   # идемпотентность — второй вызов не падает


def test_ensure_message_report_target_column_adds_to_old_schema(client):
    """Таблица message_reports без target_kind (схема ДО активностей).

    Колонка появилась вместе с жалобой на отзыв среза понимания: у такого тикета
    `message_id` — это id строки отзыва, а НЕ сообщения. Без ALTER-а очередь модерации
    падала бы «no such column» у администратора на первом же открытии вкладки «Жалобы».
    """
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE message_reports"))
        conn.execute(text("""CREATE TABLE message_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER DEFAULT 0,
            conversation_id VARCHAR, message_snapshot VARCHAR, reporter_id VARCHAR,
            reported_user_id VARCHAR, reason_code VARCHAR, description VARCHAR,
            created_at VARCHAR, status VARCHAR DEFAULT 'open', handled_by VARCHAR,
            handled_at VARCHAR, resolution_note VARCHAR
        )"""))
        #Тикет, накопленный ДО миграции: он обязан остаться в очереди модерации.
        conn.execute(text("INSERT INTO message_reports (message_id, status) VALUES (7, 'open')"))
    engine.dispose()
    _ensure_message_report_target_column()
    cols = {c["name"] for c in inspect(engine).get_columns("message_reports")}
    assert "target_kind" in cols
    with engine.begin() as conn:
        got = conn.execute(text("SELECT target_kind FROM message_reports")).scalar()
    #⚠️ Не NULL: фильтр `target_kind == "message"` иначе перестал бы находить старые
    #тикеты, и вся прежняя очередь молча исчезла бы из вкладки «Жалобы».
    assert got == "message"
    _ensure_message_report_target_column()  # идемпотентность — второй вызов не падает


def test_quiz_time_limit_column_is_added_to_an_old_schema():
    """quiz_sets.time_limit_s на СТАРОЙ схеме.

    ⚠️ `create_all` досоздаёт только отсутствующие ТАБЛИЦЫ — колонку в существующую он не
    добавляет никогда, а в свежей тестовой базе таблица создаётся сразу целиком, и ветка
    «колонки не было» там не срабатывает. Поэтому эмулируем старую схему явным DROP+CREATE.
    Это правило спасало прод уже четырежды."""
    from sqlalchemy import text, inspect
    from app.db import engine, _ensure_quiz_time_limit_column

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS quiz_sets"))
        conn.execute(text(
            "CREATE TABLE quiz_sets (id VARCHAR PRIMARY KEY, author_id VARCHAR, "
            "title VARCHAR, description VARCHAR, tags JSON, visibility VARCHAR, "
            "parent_id VARCHAR, created_at VARCHAR, updated_at VARCHAR, deleted BOOLEAN)"))
    #Пул прогрет предыдущими тестами — без dispose PRAGMA увидит снимок ДО DDL с другой
    #пуловой коннекции (та же грабля, что уже описана у соседних миграций).
    engine.dispose()
    assert "time_limit_s" not in {c["name"] for c in inspect(engine).get_columns("quiz_sets")}

    _ensure_quiz_time_limit_column()
    engine.dispose()
    assert "time_limit_s" in {c["name"] for c in inspect(engine).get_columns("quiz_sets")}

    #Повторный вызов не должен падать: миграция обязана быть идемпотентной.
    _ensure_quiz_time_limit_column()


def test_ensure_audit_chain_columns_adds_to_old_schema(client):
    """Журнал БЕЗ prev_hash/entry_hash (схема ДО цепочки целостности, 01.09.2026).

    На бою `audit_events` существует давно и полна записей — то есть это ровно та
    ситуация, в которой `create_all` не делает НИЧЕГО, а без колонок `audit.log` упал бы
    на первой же записи. Причём упал бы ТИХО: запись журнала обёрнута в except по
    инварианту «аудит не роняет бизнес-операцию», и продукт продолжил бы работать вообще
    без следа — до тех пор, пока след не понадобится.
    """
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE audit_events"))
        conn.execute(text("""CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts INTEGER DEFAULT 0,
            ts VARCHAR DEFAULT '', actor VARCHAR DEFAULT '', role VARCHAR DEFAULT '',
            ip VARCHAR DEFAULT '', device VARCHAR DEFAULT '', action VARCHAR DEFAULT '',
            target VARCHAR DEFAULT '', detail VARCHAR DEFAULT '', level VARCHAR DEFAULT 'info'
        )"""))
        #Живая запись «из прошлого»: она обязана уцелеть и остаться унаследованной,
        #а не быть подписанной задним числом — заверять своей подписью данные, за
        #которые никто не ручается, значит выдать отсутствие гарантии за гарантию.
        conn.execute(text("INSERT INTO audit_events (created_ts, ts, actor, action) "
                          "VALUES (1, '2026-01-01T00:00:00+00:00', 'old', 'login.ok')"))
    engine.dispose()
    _ensure_audit_chain_columns()
    cols = {c["name"] for c in inspect(engine).get_columns("audit_events")}
    assert "prev_hash" in cols and "entry_hash" in cols
    _ensure_audit_chain_columns()   # идемпотентность — второй вызов не падает

    #Старая запись на месте и БЕЗ подписи.
    with engine.begin() as conn:
        row = conn.execute(text("SELECT actor, entry_hash FROM audit_events")).fetchone()
    assert row[0] == "old"
    assert not (row[1] or ""), "унаследованную запись подписали задним числом"


def test_audit_writes_into_a_migrated_old_table(client):
    """После миграции журнал реально ПИШЕТСЯ, а не только обзаводится колонками.

    Проверка отдельная, потому что наличие колонки и работающая запись — разные факты, а
    отказ записи здесь тихий по построению.
    """
    from app import audit
    from app.db import SessionLocal
    from app.models import AuditEvent

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE audit_events"))
        conn.execute(text("""CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts INTEGER DEFAULT 0,
            ts VARCHAR DEFAULT '', actor VARCHAR DEFAULT '', role VARCHAR DEFAULT '',
            ip VARCHAR DEFAULT '', device VARCHAR DEFAULT '', action VARCHAR DEFAULT '',
            target VARCHAR DEFAULT '', detail VARCHAR DEFAULT '', level VARCHAR DEFAULT 'info'
        )"""))
        conn.execute(text("INSERT INTO audit_events (created_ts, ts, actor, action) "
                          "VALUES (1, '2026-01-01T00:00:00+00:00', 'old', 'login.ok')"))
    engine.dispose()
    _ensure_audit_chain_columns()

    db = SessionLocal()
    try:
        audit.log(db, actor="admin", role="admin", action="mfa.reset", target="teacher1")
        rows = db.query(AuditEvent).order_by(AuditEvent.id.asc()).all()
        assert len(rows) == 2, "событие не записалось в мигрированную таблицу"
        assert rows[1].entry_hash, "новая запись легла без подписи"
        #Унаследованная запись не подписывает следующую — связь начинается с новой.
        assert rows[1].prev_hash == ""
        report = audit.verify_chain(db)
        assert report["status"] == "ok", report
        assert report["legacy"] == 1 and report["checked"] == 1
    finally:
        db.close()


def test_ensure_conversation_avatar_column_adds_to_old_schema(client):
    """Беседы БЕЗ колонки `avatar` (схема ДО аватарок групп, 02.09.2026).

    На бою `conversations` существует с самого мессенджера и полна бесед — то есть это
    ровно та ситуация, в которой `create_all` не делает НИЧЕГО. Без колонки список чатов
    падал бы на первом же запросе: `list_chats` читает `conv.avatar` у каждой строки.

    ⚠️ В свежей тестовой базе ветка «колонки не было» не срабатывает вовсе, поэтому
    зелёные тесты мессенджера сами по себе здесь не значат ничего.
    """
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE conversations"))
        conn.execute(text("""CREATE TABLE conversations (
            id VARCHAR PRIMARY KEY, kind VARCHAR DEFAULT 'direct', title VARCHAR DEFAULT '',
            about VARCHAR DEFAULT '', owner_id VARCHAR DEFAULT '',
            is_public BOOLEAN DEFAULT 0, created_at VARCHAR DEFAULT '',
            is_system BOOLEAN DEFAULT 0, system_kind VARCHAR DEFAULT ''
        )"""))
        #Живая беседа «из прошлого»: она обязана уцелеть и получить ПУСТУЮ аватарку.
        #Пусто значит «рисуй как раньше» (буква названия на плашке), а не битая картинка.
        conn.execute(text("INSERT INTO conversations (id, kind, title) "
                          "VALUES ('conv:old', 'group', 'Старая группа')"))
    engine.dispose()
    _ensure_conversation_avatar_column()
    cols = {c["name"] for c in inspect(engine).get_columns("conversations")}
    assert "avatar" in cols
    _ensure_conversation_avatar_column()   # идемпотентность — второй вызов не падает

    with engine.begin() as conn:
        row = conn.execute(text("SELECT title, avatar FROM conversations")).fetchone()
    assert row[0] == "Старая группа"
    assert not (row[1] or ""), "старой беседе подставили какую-то картинку"
def test_group_archive_columns_are_added_to_an_old_schema():
    """groups.archived/… на СТАРОЙ схеме (архив групп, 03.09.2026).

    ⚠️ Та же грабля, что у пяти миграций выше: `create_all` досоздаёт только отсутствующие
    ТАБЛИЦЫ, а колонку в существующую — никогда. В свежей тестовой базе таблица
    создаётся сразу со всеми полями, поэтому ветка «колонки не было» там не исполняется
    и зелёные тесты про неё не значат ничего. Эмулируем старую схему явным DROP+CREATE.

    Обратный ход проверен откатом: убери вызов `_ensure_group_archive_columns()` из
    `db.init_db()` — этот тест продолжит проходить (он зовёт функцию сам), а вот
    `test_group_archive.py` целиком ляжет на боевой схеме. Поэтому здесь проверяется
    САМА миграция, а её ВЫЗОВ — тем, что продукт работает."""
    from sqlalchemy import text, inspect
    from app.db import engine, _ensure_group_archive_columns

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS groups"))
        conn.execute(text(
            "CREATE TABLE groups (id VARCHAR PRIMARY KEY, name VARCHAR, subjects JSON, "
            "specialty_code VARCHAR, enrollment_year INTEGER, category VARCHAR, "
            "updated_at VARCHAR, deleted BOOLEAN)"))
    #Пул прогрет предыдущими тестами — без dispose инспектор увидит снимок ДО DDL.
    engine.dispose()
    before = {c["name"] for c in inspect(engine).get_columns("groups")}
    assert "archived" not in before and "last_course" not in before

    _ensure_group_archive_columns()
    engine.dispose()
    after = {c["name"] for c in inspect(engine).get_columns("groups")}
    for col in ("archived", "archived_at", "archived_reason",
                "last_course", "last_course_year",
                #Перевод группы на новый курс (05.09.2026): без этой колонки
                #`_assignments_fallback` возвращал бы преподавателю прошлогодние группы,
                #то есть открепление осталось бы только на бумаге, а на боевой базе
                #каждый её запрос падал бы на «no such column».
                "assignments_reset_term"):
        assert col in after, f"миграция не добавила {col}"

    #Идемпотентность: второй прогон на уже мигрированной таблице не должен падать.
    _ensure_group_archive_columns()


def test_schedule_override_subgroup_column_is_added_to_an_old_schema():
    """schedule_overrides.subgroup на СТАРОЙ схеме (подгруппы в паре, 03.09.2026).

    ⚠️ Та же грабля, что у соседних миграций: `create_all` колонку в существующую
    таблицу не добавляет никогда, а в свежей тестовой базе таблица создаётся сразу с
    ней. Без этой регрессии на боевой базе не было бы поля, и КАЖДАЯ правка расписания
    падала бы на «no such column» — то есть починка расписания сломала бы расписание."""
    from sqlalchemy import text, inspect
    from app.db import engine, _ensure_schedule_override_subgroup_column

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS schedule_overrides"))
        conn.execute(text(
            "CREATE TABLE schedule_overrides (id VARCHAR PRIMARY KEY, group_name VARCHAR, "
            "week INTEGER, day VARCHAR, pair_no INTEGER, action VARCHAR, subject VARCHAR, "
            "time VARCHAR, room VARCHAR, teacher VARCHAR, kind VARCHAR, "
            "updated_at VARCHAR, deleted BOOLEAN)"))
    #Пул прогрет предыдущими тестами — без dispose инспектор увидит снимок ДО DDL.
    engine.dispose()
    assert "subgroup" not in {c["name"] for c in inspect(engine).get_columns("schedule_overrides")}

    _ensure_schedule_override_subgroup_column()
    engine.dispose()
    assert "subgroup" in {c["name"] for c in inspect(engine).get_columns("schedule_overrides")}

    #Идемпотентность: повтор на уже мигрированной таблице не должен падать.
    _ensure_schedule_override_subgroup_column()


def test_message_reply_quote_column_is_added_to_an_old_schema(client):
    """messages.reply_quote на СТАРОЙ схеме («ответить с цитатой», 05.09.2026).

    Без ALTER-а КАЖДАЯ отправка сообщения падала бы «no such column» — то есть мессенджер
    колледжа встал бы целиком на первом же деплое. Ветка «колонки не было» в свежей
    тестовой базе не исполняется никогда (create_all создаёт таблицу сразу со всеми
    полями), поэтому эмулируем старую схему явно.

    ⚠️ Уже лежащие ответы обязаны пережить миграцию с ПУСТОЙ цитатой: пусто означает
    «обычный ответ», и клиент показывает начало оригинала — ровно как показывал до правки.
    """
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS messages"))
        conn.execute(text("""CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id VARCHAR,
            sender_id VARCHAR, body VARCHAR, created_at VARCHAR, edited_at VARCHAR,
            deleted_at VARCHAR, reply_to_id INTEGER DEFAULT 0,
            fwd_from_sender_id VARCHAR, fwd_from_conv_id VARCHAR,
            fwd_from_created_at VARCHAR, fwd_sender_name VARCHAR,
            pinned BOOLEAN, pinned_at VARCHAR, pinned_by VARCHAR
        )"""))
        conn.execute(text("INSERT INTO messages (body, reply_to_id) VALUES ('старый ответ', 3)"))
    engine.dispose()
    before = {c["name"] for c in inspect(engine).get_columns("messages")}
    assert "reply_quote" not in before

    _ensure_message_addon_columns()
    engine.dispose()
    assert "reply_quote" in {c["name"] for c in inspect(engine).get_columns("messages")}
    with engine.begin() as conn:
        got = conn.execute(text("SELECT body, reply_quote FROM messages")).fetchone()
    assert got[0] == "старый ответ"
    assert not (got[1] or ""), "старому ответу подставили какую-то цитату"

    _ensure_message_addon_columns()   # идемпотентность — второй вызов не падает


# ─────────────────────────────────────────────────────────────────────────────────
# СТАТИСТИКА ПЛАНИРОВЩИКА (PRAGMA optimize)
# ─────────────────────────────────────────────────────────────────────────────────

def test_startup_actually_refreshes_planner_stats(monkeypatch):
    """🔒 Проверяем ВЫЗОВ, а не поведение функции.

    Наш самый частый класс дефекта — «обещание без вызывающего»: функция есть, тесты её
    поведения зелёные, а в продукте её не зовёт НИКТО. Здесь это особенно легко: убрать
    строку из `init_db` можно правкой в один символ, и не покраснеет ничего — статистика
    просто не обновится, а узнать об этом будет неоткуда.
    """
    from app import db as db_mod

    called = []
    monkeypatch.setattr(db_mod, "_refresh_query_planner_stats",
                        lambda: called.append(1))
    db_mod.init_db()
    assert called, ("init_db больше не обновляет статистику планировщика — "
                    "строку вызова удалили, и заметить это нечем")


def test_planner_stats_failure_never_breaks_startup(monkeypatch):
    """Сбой обновления статистики не имеет права уронить запуск службы.

    Статистика — ускорение, а не условие работы. Упавший здесь старт означал бы, что
    журнал недоступен всему колледжу из-за строки, без которой он прекрасно работает.
    """
    from app import db as db_mod

    class _Boom:
        def begin(self):
            raise RuntimeError("база занята")

    monkeypatch.setattr(db_mod, "engine", _Boom())
    db_mod._refresh_query_planner_stats()          #не должно бросить


def test_planner_stats_really_appear_on_this_machine():
    """🔥 ОБРАТНАЯ ПОЛОВИНА, и она поймала настоящий дефект в день написания.

    Первая версия проверяла ПОВЕДЕНИЕ `PRAGMA optimize` на отдельной базе — и покраснела.
    Причина оказалась не в тесте: `optimize` анализирует только те таблицы, к которым
    ТЕКУЩЕЕ соединение уже обращалось запросом, а на старте службы соединение свежее.
    То есть рекомендация «звать optimize» дала бы у нас тихий холостой вызов.
    Замер обеих сторон: stdlib sqlite3 3.40.1 статистику не создаёт, sqlcipher3 3.51.1
    создаёт (поведение добавлено в 3.46). Версию SQLCipher на бою мы не знаем.

    Поэтому проверяется РЕЗУЛЬТАТ у продукта: после вызова статистика обязана
    существовать — на любом драйвере. Прогон идёт на stdlib-драйвере, то есть ровно на
    строгой стороне, и без запасного `ANALYZE` этот тест краснеет.
    """
    from sqlalchemy import text

    from app import db as db_mod

    with db_mod.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS sqlite_stat1"))

    db_mod._refresh_query_planner_stats()

    with db_mod.engine.begin() as conn:
        have = conn.execute(text(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='sqlite_stat1'")).scalar()
    assert have == 1, (
        "после обновления статистики таблицы sqlite_stat1 нет — значит вызов оказался "
        "холостым, а узнать об этом в бою было бы неоткуда")
