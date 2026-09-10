"""
db.py — Подключение к базе (SQLAlchemy).

━━ БАЗА У ПРОДУКТА ОДНА: SQLite (+ SQLCipher на бою) ━━
Решение 04.09.2026, Ярослав: PostgreSQL из проекта убран ЦЕЛИКОМ. Причина не в
идеологии, а в том, что двух баз у нас никогда и не было по-настоящему: на бою
всё время работал SQLite под SQLCipher (проверено 02.09.2026 — `systemctl is-active
postgresql` отвечал inactive), а ветка PostgreSQL не проверялась НИ РАЗУ ни одним
прогоном. То есть в коде жил второй, непроверенный путь, про который документация
уверенно писала «на бою он и работает», и по этой неправде уже строились планы.

⚠️ **Непроверенная ветка хуже отсутствующей.** Она даёт ложную уверенность («у нас
поддержаны обе СУБД»), требует оглядки при каждой миграции и при этом не защищена
ни одним тестом. Убрать её — не потеря возможности, а приведение кода к тому, что
есть на самом деле.

⚠️ Что это НЕ значит: масштаб мы не потеряли. Узкое место SQLite — одновременная
ЗАПИСЬ, и она закрыта режимом WAL плюс `busy_timeout` (см. `_sqlite_pragmas`), а
объёмы колледжа — тысячи строк, а не миллионы. На машине ВСГУТУ (Ryzen 9, 32 ГБ)
запас только вырастет.
"""
import re

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool

from .config import DATABASE_URL, DB_KEY
#⚠️ Импорт НА УРОВНЕ МОДУЛЯ, а не внутри `_sqlite_pragmas`. Ленивый импорт там выглядел
#осторожнее, но исполнялся бы на КАЖДОЕ новое соединение с базой — и однажды поймал бы
#файл в момент записи. Цикла зависимостей нет: `hostcaps` знает только про `hostinfo`,
#а тот — про стандартную библиотеку.
from . import hostcaps

_IS_SQLITE = DATABASE_URL.startswith("sqlite")
#Для SQLite нужен check_same_thread=False (FastAPI работает в нескольких потоках).
_connect_args = {"check_same_thread": False} if _IS_SQLITE else {}


def _build_engine(url: str = None, key: str = None):
    """Движок БД. Если задан GRADEBOOK_DB_KEY и доступен драйвер sqlcipher3 — поднимаем
    движок поверх SQLCipher: файл БД шифруется ЦЕЛИКОМ (AES-256), ПДн at rest (152-ФЗ).
    Ключ (64 hex, raw 256-bit) задаётся PRAGMA key ПЕРВОЙ операцией КАЖДОГО соединения.
    Нет ключа/драйвера (напр. Windows-dev, CI без ключа) → обычный SQLite: схема id и
    тесты не меняются. Ключ БД нигде не логируем."""
    if _IS_SQLITE and DB_KEY:
        try:
            import sqlcipher3
        except ImportError:
            print("[db] GRADEBOOK_DB_KEY задан, но драйвер sqlcipher3 не установлен "
                  "(на Windows-dev это нормально) — БД работает БЕЗ шифрования файла.")
        else:
            path = DATABASE_URL.split("sqlite:///", 1)[-1]
            #🔒 Ключ подставляется в текст PRAGMA (параметризовать PRAGMA нельзя), поэтому
            #он ОБЯЗАН быть чистым hex — иначе кавычка внутри значения разрывает строку
            #запроса. Источник ключа доверенный (server/.env), но опечатка в нём не должна
            #превращаться в SQL, а «тихо открылось без шифрования» — худший исход из всех.
            if not re.fullmatch(r"[0-9a-fA-F]{64}", DB_KEY):
                raise RuntimeError(
                    "GRADEBOOK_DB_KEY должен быть ровно 64 hex-символа (32 байта). "
                    "Проверьте server/.env — с неверным ключом база не откроется.")

            def _creator():
                conn = sqlcipher3.connect(path, check_same_thread=False)
                conn.execute("PRAGMA key = \"x'%s'\"" % DB_KEY)   # ДО любых других операций
                return conn

            print("[db] Файл БД шифруется (SQLCipher, AES-256).")
            #poolclass=QueuePool — ОБЯЗАТЕЛЬНО, не убирать.
            #Файл БД мы открываем через creator, поэтому URL здесь пустой («sqlite://»).
            #Но для такого URL SQLAlchemy считает базу IN-MEMORY и молча берёт
            #SingletonThreadPool — пул «одно соединение на поток», предназначенный для
            #тестов с памятью. Он ЗАКРЫВАЕТ лишние соединения сверх size, а FastAPI
            #обслуживает синхронные эндпоинты из пула потоков → соединение закрывалось
            #под работающей сессией, и SQLAlchemy падал на откате:
            #   sqlcipher3.dbapi2.ProgrammingError: Cannot operate on a closed database
            #(ловили на бою при живых пользователях). QueuePool — нормальный пул с
            #переиспользованием: соединения не закрываются произвольно, а PRAGMA key
            #(тяжёлый KDF) выполняется только при создании нового соединения, а не на
            #каждый запрос. Потокобезопасно: creator ставит check_same_thread=False.
            return create_engine("sqlite://", creator=_creator, pool_pre_ping=True,
                                 poolclass=QueuePool)
    return create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)


engine = _build_engine()


def rebind(url: str, key: str = "") -> None:
    """Переключить ВЕСЬ доступ к базе на другой файл — БЕЗ перезапуска процесса.

    ━━ ЗАЧЕМ ЭТО ЕСТЬ ━━
    Нужно РОВНО ОДНОМУ потребителю — локальному серверу внутри десктопа
    (`desktop/local_api.py`). Там форма входа теперь веб-овая, значит сервер обязан
    подняться ДО того, как известно, кто войдёт. А копия базы у каждого пользователя
    СВОЯ (изоляция данных: иначе следующий вошедший видит оценки предыдущего). Раньше
    из-за этого всё ложилось в общий «анонимный» файл — та самая утечка, которую
    раздельные файлы и должны были закрыть.

    ⚠️ НА БОЮ НЕ ВЫЗЫВАЕТСЯ НИКОГДА: там база одна на процесс и задаётся окружением.
    Функция существует только ради десктопного сценария «сначала окно, потом вход».

    Почему это безопасно сделать на живом приложении:
      • `SessionLocal.configure()` меняет привязку У СУЩЕСТВУЮЩЕГО объекта, поэтому все
        модули, сделавшие `from .db import SessionLocal`, автоматически видят новую базу.
        Пересоздать `SessionLocal` было бы НЕЛЬЗЯ: у них остались бы ссылки на старый.
      • Старый движок закрываем (`dispose`), иначе его соединения продолжали бы держать
        прежний файл открытым — на Windows это мешает даже переименовать копию.
      • PRAGMA-хук вешаем на НОВЫЙ движок: он привязан к конкретному движку, а не к модулю.
    """
    global engine, DATABASE_URL, DB_KEY, _IS_SQLITE, _connect_args
    old = engine
    DATABASE_URL = url
    DB_KEY = key or ""
    _IS_SQLITE = DATABASE_URL.startswith("sqlite")
    _connect_args = {"check_same_thread": False} if _IS_SQLITE else {}
    engine = _build_engine()
    if _IS_SQLITE:
        event.listen(engine, "connect", _sqlite_pragmas)
    SessionLocal.configure(bind=engine)
    try:
        old.dispose()
    except Exception:
        pass
    init_db()          #создать таблицы и прогнать идемпотентные мини-миграции


def _sqlite_pragmas(dbapi_conn, _rec):
    """Настраиваем КАЖДОЕ SQLite-соединение под конкурентную нагрузку.

    Зачем: под утренним «гердом» входов колледжа несколько запросов пишут в БД
    одновременно (вход теперь ещё и создаёт строки сессии — auth_sessions). Без
    этих PRAGMA SQLite сериализует запись и при конфликте СРАЗУ падает «database is
    locked». С WAL читатели не блокируют писателя, а busy_timeout заставляет
    писателей ЖДАТЬ освобождения блокировки, а не падать. (Как в десктопном core.py.)
    Именно эти PRAGMA, а не другая СУБД, и держат нагрузку колледжа: узкое место
    SQLite — одновременная ЗАПИСЬ, и WAL с ожиданием блокировки закрывают ровно её.

    ⚠️ Обычная функция, а не @event.listens_for: тот же хук нужно навесить и на НОВЫЙ
    движок после `rebind()` — декоратор привязал бы его только к первому."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")     #параллельные читатели + один писатель
    cur.execute("PRAGMA busy_timeout=5000")    #ждать блокировку до 5 c, а не падать
    cur.execute("PRAGMA synchronous=NORMAL")   #безопасно и быстрее при WAL

    # ━━ ПАМЯТЬ: РАЗМЕР КЕША СПРАШИВАЕМ У МАШИНЫ, А НЕ ПИШЕМ ЧИСЛОМ ━━━━━━━━━━━━━━━━━━
    # Здесь стояло жёсткое `-4000` (4 МБ), и для нынешнего боя это ПРАВИЛЬНОЕ число:
    # одно ядро и 960 МБ, из которых Python с Caddy уже занимают около 450; боевой файл
    # базы — около 3 МБ, то есть 4 МБ вмещают его целиком вместе с индексами, а всё
    # лишнее ушло бы в своп и сделало базу МЕДЛЕННЕЕ, чем она была.
    #
    # ⚠️ Но это снимок ОДНОЙ машины, а сервер переезжает на компьютер ВСГУТУ (32 ГБ).
    # Там те же 4 МБ — не бережливость, а забытая настройка: её пришлось бы ВСПОМНИТЬ и
    # поправить руками, а забывают такое ровно в день переезда. Поэтому число приходит
    # из `hostcaps.sqlite_cache_kib()`: на слабой машине оно то же самое, что и было
    # (проверенное на бою поведение переезд менять не имеет права), на мощной — больше.
    #
    # ⚠️ Минус означает килобайты (-4000 = 4 МБ), а не число страниц: запись через
    # страницы зависела бы от page_size и молча поехала бы при его смене.
    cur.execute("PRAGMA cache_size=-%d" % hostcaps.sqlite_cache_kib())
    # Временные таблицы (сортировки отчётов куратора, поиск по переписке) — в памяти.
    # Они небольшие и живут доли секунды, а на диске это лишняя запись на том же SSD,
    # куда одновременно идёт WAL.
    cur.execute("PRAGMA temp_store=MEMORY")
    # ━━ ОТОБРАЖЕНИЕ ФАЙЛА В ПАМЯТЬ: ТОЛЬКО ТАМ, ГДЕ ПАМЯТЬ ЕСТЬ ━━━━━━━━━━━━━━━━━━━━━
    # Здесь до 09.09.2026 стояло «mmap_size НЕ ТРОГАЕМ СОЗНАТЕЛЬНО», и довод был верным
    # для боевого VPS, но опирался на рассуждение, а не на замер. Теперь замер есть
    # (`server/bench_messenger.py --encrypt --paired-mmap 256`): на шифрованной базе в
    # 922 500 сообщений выигрыш 1–3 мс из 26 на самом тяжёлом пути, знак устойчив.
    # Величина мала, поэтому включаем ровно там, где включение ничего не стоит, —
    # решает `hostcaps`, а не число в коде (то же правило, что у `cache_size`).
    # ⚠️ Ноль — это НЕ «выключили»: ноль и есть значение SQLite по умолчанию, то есть на
    # слабой машине соединение настраивается ровно как раньше.
    _mmap = hostcaps.sqlite_mmap_bytes()
    if _mmap:
        cur.execute("PRAGMA mmap_size=%d" % _mmap)
    cur.close()


if _IS_SQLITE:
    event.listen(engine, "connect", _sqlite_pragmas)


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    """Зависимость FastAPI: открыть сессию на запрос и гарантированно закрыть."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Создаёт таблицы, если их нет. Вызывается при старте приложения."""
    from . import models  #noqa: F401 — регистрируем модели в метаданных
    Base.metadata.create_all(bind=engine)
    _ensure_user_prefs_column()
    _ensure_user_patronymic_column()
    _backfill_patronymic()
    _ensure_lesson_term_columns()
    _backfill_lesson_term()
    _ensure_user_curated_groups_column()
    _ensure_user_password_set_at_column()
    _ensure_user_birthday_column()
    _ensure_grade_student_id_columns()
    _ensure_notify_event_columns()
    _ensure_participant_state_columns()
    _ensure_message_addon_columns()
    _ensure_message_report_target_column()
    _ensure_conversation_system_columns()
    _ensure_subject_hours_teacher_column()
    _ensure_subject_hours_zet_column()
    _ensure_subject_hours_split_columns()
    _ensure_lesson_subgroup_column()
    _ensure_group_specialty_columns()
    _ensure_group_category_column()
    _ensure_group_archive_columns()
    _ensure_schedule_override_subgroup_column()
    _ensure_auth_session_client_column()
    _ensure_quiz_time_limit_column()
    _ensure_quiz_kind_column()
    _ensure_audit_chain_columns()
    _ensure_conversation_avatar_column()
    _ensure_hot_path_indexes()
    _migrate_slash_in_ids()
    _refresh_query_planner_stats()


def _refresh_query_planner_stats():
    """Обновить статистику планировщика (`PRAGMA optimize`). Один раз, на старте.

    ⚠️ ЗАМЕР, А НЕ СОВЕТ (09.09.2026). Внешний разбор предлагал звать это «при закрытии
    каждого соединения», и первая моя реакция была «нельзя: ANALYZE ПИШЕТ, а запись —
    наше единственное узкое место». Реакция оказалась неверной, и показал это замер:
      • первый вызов на шифрованной базе в 198 МБ — 281 мс, пишет 16 строк статистики;
      • повторные вызовы — 0.01–0.68 мс, потому что при свежей статистике `optimize`
        не делает НИЧЕГО и ничего не пишет.
    То есть дорогого «на каждом закрытии» нет. Но и весь выигрыш даёт первый вызов, а
    соединения у нас живут в пуле и почти не закрываются — крючок на `close` был бы
    холостым по построению. Отсюда единственное честное место: старт службы.

    ⚠️ Что это даёт: 0…−2.4 мс на списке чатов преподавателя (26 мс). Величина на
    границе шума стенда, знак непостоянен — поэтому здесь нет ни слова о «приросте
    производительности». Смысл в другом: без статистики планировщик выбирает план по
    умолчанию, и цена этого РАСТЁТ вместе с базой, а 281 мс на старте не стоят ничего.

    🔥 И ГЛАВНОЕ, ЧТО НАШЛОСЬ ПРИ ПРОВЕРКЕ: `PRAGMA optimize` ЗАВИСИТ ОТ ВЕРСИИ SQLite,
    и на старой молча не делает НИЧЕГО. Он анализирует только те таблицы, к которым
    ТЕКУЩЕЕ СОЕДИНЕНИЕ уже обращалось запросом, — а на старте службы соединение свежее
    и запросов на нём не было. Замерено обеими сторонами:
      • stdlib sqlite3 3.40.1 — `optimize` на свежем соединении `sqlite_stat1` НЕ создаёт;
      • sqlcipher3 3.51.1 — создаёт (в 3.46 добавили разбор таблиц вовсе без статистики).
    Версию SQLCipher на боевой машине мы не знаем и знать не обязаны, а «настройка,
    которая, возможно, работает» — это худший вид настройки: она создаёт уверенность и
    не даёт эффекта. Поэтому после `optimize` проверяем ФАКТ (появилась ли таблица
    статистики) и при её отсутствии зовём `ANALYZE` явно.

    ⚠️ Цена явного `ANALYZE` замерена там же: 337 мс на базе 198 МБ, и он НЕ умеет
    пропускать работу — повторный вызов стоит те же 304 мс. Поэтому он именно запасной
    и только когда статистики нет вовсе, а не «на всякий случай каждый старт».

    ⚠️ Молчаливого отказа быть не должно, но и падать нельзя: не обновилась статистика —
    сервер работает ровно как раньше, просто скажет об этом в журнал.
    """
    if not _IS_SQLITE:
        return
    try:
        with engine.begin() as conn:
            #`analysis_limit` ограничивает работу ANALYZE: без него на очень большой базе
            #первый вызов растёт вместе с ней, а нам нужен предсказуемый старт.
            conn.exec_driver_sql("PRAGMA analysis_limit=400")
            conn.exec_driver_sql("PRAGMA optimize")
            have = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='sqlite_stat1'").scalar()
            if not have:
                conn.exec_driver_sql("ANALYZE")
    except Exception as e:                                          # noqa: BLE001
        print("[db] статистика планировщика не обновлена: %s" % e)


#━━ ИНДЕКСЫ ГОРЯЧИХ ПУТЕЙ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#⚠️ ПОЧЕМУ ОТДЕЛЬНАЯ МИГРАЦИЯ, А НЕ ПРОСТО `index=True` В МОДЕЛИ.
#`create_all` заводит индексы только вместе с НОВОЙ таблицей. На боевой базе, где
#`messages` существует с самого мессенджера, дописанный в модель `index=True` не создаст
#ничего и не скажет об этом ни слова — ровно та же грабля, что с новой КОЛОНКОЙ в
#существующей таблице, и настолько же тихая. В свежей тестовой базе индекс появится сам,
#поэтому зелёные тесты сами по себе тут ничего не доказывают.
#
#⚠️ Список ведётся ПО ЗАМЕРУ, а не «на всякий случай». Лишний индекс не бесплатен: он
#замедляет запись и занимает место, а узкое место SQLite — именно ЗАПИСЬ. Каждая строка
#ниже обязана называть, какой замер её оправдывает.
#━━ ИМЕНА В DDL ПРОВЕРЯЮТСЯ, А НЕ ПОДСТАВЛЯЮТСЯ КАК ЕСТЬ (09.09.2026) ━━━━━━━━━━━━━━━━
#Имя таблицы, столбца и тип нельзя передать параметром запроса — SQLite их подстановку
#не поддерживает, поэтому все мини-миграции собирают строку сами. Сегодня это безопасно:
#значения приходят из литеральных списков в этом же файле, снаружи в них не попадает
#ничего. Замечание внешнего разбора (09.09.2026) с этим и не спорит — он называет это
#«неряшливостью, а не дырой», и он прав.
#⚠️ Проверка заведена не от сегодняшнего риска, а от завтрашнего: список однажды начнут
#собирать из настройки, из ответа `inspect` или из аргумента функции — и превращение
#мини-миграции в исполняемый SQL пройдёт незамеченным, потому что рядом не будет ничего,
#что об этом напомнит. Тот же приём, которым закрыт `desktop/server_admin.py`: граница
#проводится КОДОМ, а не обещанием в комментарии.
#⚠️ Отказ ГРОМКИЙ (исключение), а не «пропустим строку»: миграция, молча не создавшая
#столбец, — это наш любимый тихий отказ, и он выясняется на первом запросе к базе.
_DDL_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
#Тип колонки — тоже часть строки запроса. Форм у нас ровно две: «ТИП» и
#«ТИП DEFAULT <литерал>»; ничего другого в списках не встречается.
_DDL_TYPE_RE = re.compile(r"[A-Z]+(\(\d+\))?( DEFAULT ('[^']*'|-?\d+))?$")


def _ddl_ident(value):
    """Вернуть имя таблицы/столбца/индекса, годное для подстановки в DDL."""
    if not isinstance(value, str) or not _DDL_IDENT_RE.match(value):
        raise ValueError("недопустимое имя в DDL миграции: %r" % (value,))
    return value


def _ddl_columns(value):
    """То же для списка столбцов индекса («a» или «a, b»)."""
    parts = [p.strip() for p in str(value).split(",")]
    return ", ".join(_ddl_ident(p) for p in parts)


def _ddl_type(value):
    """Тип столбца вместе с необязательным DEFAULT."""
    if not isinstance(value, str) or not _DDL_TYPE_RE.match(value):
        raise ValueError("недопустимый тип столбца в DDL миграции: %r" % (value,))
    return value


_HOT_INDEXES = [
    #Значок «N ответов» в тредах: `_attach_reply_counts` группирует по reply_to_id при
    #открытии ЛЮБОЙ беседы. Замер 09.09.2026 (server/bench_messenger.py, профиль big,
    #922 500 сообщений): 139 мс из 143 мс всего ответа. После индекса — доли миллисекунды.
    ("messages", "ix_messages_reply_to_id", "reply_to_id"),
]


def _ensure_hot_path_indexes():
    """Досоздать индексы горячих путей на УЖЕ СУЩЕСТВУЮЩЕЙ базе. Идемпотентно."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    for table, name, columns in _HOT_INDEXES:
        try:
            if table not in insp.get_table_names():
                continue                     #таблицы ещё нет — create_all создаст с индексом
            have = {i["name"] for i in insp.get_indexes(table)}
        except Exception:
            continue
        if name in have:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS %s ON %s (%s)"
                    % (_ddl_ident(name), _ddl_ident(table), _ddl_columns(columns))))
            print("[db] создан индекс %s (%s.%s)" % (name, table, columns))
        except Exception as e:
            #Индекс — ускорение, а не условие работы: не смогли создать — работаем как
            #раньше, но ГРОМКО, а не молча. Тихий отказ здесь означал бы, что причину
            #медленной работы будут искать где угодно, кроме этого места.
            print("[db] НЕ УДАЛОСЬ создать индекс %s: %s" % (name, e))


def _ensure_conversation_avatar_column():
    """Идемпотентная мини-миграция: conversations.avatar (аватарка группы/канала).

    Таблица `conversations` на бою существует с самого мессенджера и полна бесед, а
    `create_all` новые СТОЛБЦЫ в существующую таблицу не добавляет никогда. В свежей
    тестовой базе ветка «колонки не было» не срабатывает вовсе — то есть зелёные тесты
    сами по себе здесь ничего не значат; регрессия живёт в `test_db_migrations.py`.

    ⚠️ Существующие беседы получают пустую строку, и это правильный исход: пустая
    аватарка означает «рисуй как раньше» (буква названия на цветной плашке), а не
    битую картинку."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("conversations")}
    except Exception:
        return
    if "avatar" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE conversations ADD COLUMN avatar VARCHAR DEFAULT ''"))


def _ensure_audit_chain_columns():
    """Идемпотентная мини-миграция: audit_events.prev_hash / entry_hash (цепочка целостности).

    Таблица `audit_events` на бою существует давно и полна записей, а `create_all` новые
    СТОЛБЦЫ в существующую таблицу не добавляет никогда — в свежей тестовой базе ветка
    «колонки не было» не срабатывает вовсе, поэтому зелёные тесты сами по себе ничего
    здесь не значат (регрессия — `server/tests/test_db_migrations.py`).

    ⚠️ Существующие записи остаются БЕЗ хешей, и задним числом мы их не подписываем.
    Это принципиально: подписать сейчас значит заверить своей же подписью данные, за
    которые никто не может поручиться, — цепочка стала бы выглядеть целой ровно там, где
    гарантий нет. Проверка честно называет такие записи унаследованными и начинает
    цепочку с первой подписанной (см. `audit.verify_chain`).
    """
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("audit_events")}
    except Exception:
        return
    wanted = (("prev_hash", "VARCHAR DEFAULT ''"),
              ("entry_hash", "VARCHAR DEFAULT ''"))
    missing = [(n, t) for n, t in wanted if n not in columns]
    if not missing:
        return
    with engine.begin() as conn:
        for name, coltype in missing:
            conn.execute(text("ALTER TABLE audit_events ADD COLUMN %s %s" % (_ddl_ident(name), _ddl_type(coltype))))


def _ensure_quiz_kind_column():
    """Идемпотентная мини-миграция: quiz_sets.kind (для какой категории набор).

    ⚠️ Существующие наборы получают 'quiz' — они и создавались как обычные викторины.
    `create_all` колонку в существующую таблицу не добавляет никогда."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("quiz_sets")}
    except Exception:
        return
    if "kind" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE quiz_sets ADD COLUMN kind VARCHAR DEFAULT 'quiz'"))
        conn.execute(text("UPDATE quiz_sets SET kind = 'quiz' WHERE kind IS NULL"))


def _ensure_quiz_time_limit_column():
    """Идемпотентная мини-миграция: quiz_sets.time_limit_s (ограничение времени на тест).

    ⚠️ `create_all` досоздаёт только ОТСУТСТВУЮЩИЕ таблицы, но НЕ добавляет колонки в
    существующие — в свежей тестовой базе таблица создаётся целиком, и ветка «колонки не
    было» там не срабатывает никогда. Это правило спасало прод уже четырежды."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("quiz_sets")}
    except Exception:
        return
    if "time_limit_s" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE quiz_sets ADD COLUMN time_limit_s INTEGER DEFAULT 0"))


def _ensure_message_addon_columns():
    """Мини-миграция: messages.kind/body_format/client_nonce/mentions — фичи мессенджера §D
    (системные сообщения, Markdown-формат, идемпотентность, упоминания). Таблица messages
    уже могла появиться на проде, а create_all новые СТОЛБЦЫ не досоздаёт → ALTER по одному."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("messages")}
    except Exception:
        return          #таблицы ещё нет — create_all создал её сразу со столбцами
    #⚠️ Новая колонка в СУЩЕСТВУЮЩЕЙ таблице заводится только здесь: `create_all`
    #досоздаёт лишь отсутствующие ТАБЛИЦЫ. В свежей тестовой БД ветка «колонки не было»
    #не срабатывает никогда, поэтому зелёные тесты про эту миграцию ничего не говорят —
    #её держит `tests/test_db_migrations.py`.
    wanted = (("kind", "VARCHAR DEFAULT 'text'"),
              ("body_format", "VARCHAR DEFAULT 'markdown'"),
              ("client_nonce", "VARCHAR DEFAULT ''"),
              ("attachment_id", "VARCHAR DEFAULT ''"),
              ("mentions", "JSON"),
              #«Ответить с цитатой» (05.09.2026): выделенный кусок исходного сообщения.
              #Пусто у всех уже лежащих строк — и это верный исход: старый ответ цитирует
              #оригинал целиком, как и цитировал.
              ("reply_quote", "VARCHAR DEFAULT ''"))
    with engine.begin() as conn:
        for name, coltype in wanted:
            if name not in columns:
                conn.execute(text("ALTER TABLE messages ADD COLUMN %s %s" % (_ddl_ident(name), _ddl_type(coltype))))


def _ensure_conversation_system_columns():
    """Мини-миграция: conversations.is_system/system_kind — §D12, автоматические
    системные каналы (оценки/объявления/расписание). Тот же паттерн, что и выше."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("conversations")}
    except Exception:
        return
    wanted = (("is_system", "BOOLEAN DEFAULT 0"), ("system_kind", "VARCHAR DEFAULT ''"))
    with engine.begin() as conn:
        for name, coltype in wanted:
            if name not in columns:
                conn.execute(text("ALTER TABLE conversations ADD COLUMN %s %s" % (_ddl_ident(name), _ddl_type(coltype))))


def _ensure_auth_session_client_column():
    """Идемпотентная мини-миграция: auth_sessions.client — каким клиентом выдана сессия
    ('android' | 'web' | '' для десктопа).

    Зачем колонка, а не заголовок запроса. Абсолютный потолок сессии (§6) считается на
    КАЖДОМ `/auth/refresh` от `issued_at`. Если брать «мобильный ли клиент» из заголовка
    прямо там, то любой браузер, приславший `X-Client: android`, продлил бы обычную
    веб-сессию с пяти часов до недели — то есть заголовок стал бы способом обойти
    потолок. Записанный ОДИН раз при входе, он этого не позволяет: подделать можно
    только собственную новую сессию, а не растянуть уже выданную.

    Тот же паттерн, что и у остальных мини-миграций: create_all не досоздаёт СТОЛБЕЦ в
    уже существующей таблице на боевой БД (в свежей тестовой создаёт сразу целиком —
    поэтому ветка «колонки не было» в обычных тестах не срабатывает, см. урок про
    conversation_participants)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("auth_sessions")}
    except Exception:
        return  #таблицы ещё нет — create_all создаст её сразу со столбцом
    if "client" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE auth_sessions ADD COLUMN client VARCHAR DEFAULT ''"))


def _ensure_subject_hours_teacher_column():
    """Идемпотентная мини-миграция: subject_hours.teacher_id — назначение препода на
    пару (группа,предмет) на семестр (см. models.SubjectHours). Тот же паттерн: create_all
    не досоздаёт колонку в уже существующей таблице на боевой БД, только ALTER."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("subject_hours")}
    except Exception:
        return  #таблицы ещё нет — create_all создаст её сразу со столбцом
    if "teacher_id" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE subject_hours ADD COLUMN teacher_id VARCHAR DEFAULT ''"))


def _ensure_subject_hours_zet_column():
    """Идемпотентная мини-миграция: subject_hours.zet (ЗЕТ, docs/done/PLAN-ZET.md). NULLABLE
    без дефолта — NULL значит «администратор не задавал», отличать от 0.0 обязательно."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("subject_hours")}
    except Exception:
        return
    if "zet" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE subject_hours ADD COLUMN zet FLOAT"))


def _ensure_subject_hours_split_columns():
    """Идемпотентная мини-миграция: subject_hours.split/teacher_id_2 (раздельное
    обучение, §ролей 3.6.1). Тот же паттерн, что уже трижды применён для subject_hours."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("subject_hours")}
    except Exception:
        return  #таблицы ещё нет — create_all создаст её сразу с обеими колонками
    with engine.begin() as conn:
        if "split" not in columns:
            conn.execute(text("ALTER TABLE subject_hours ADD COLUMN split BOOLEAN DEFAULT 0"))
        if "teacher_id_2" not in columns:
            conn.execute(text("ALTER TABLE subject_hours ADD COLUMN teacher_id_2 VARCHAR DEFAULT ''"))


def _ensure_lesson_subgroup_column():
    """Идемпотентная мини-миграция: lessons.subgroup (раздельное обучение, §ролей
    3.6.1) — 0 занятие общее/не разделённого предмета, 1/2 — своя подгруппа."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("lessons")}
    except Exception:
        return
    if "subgroup" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE lessons ADD COLUMN subgroup INTEGER DEFAULT 0"))


def _ensure_group_specialty_columns():
    """Идемпотентная мини-миграция: groups.specialty_code/enrollment_year — импорт
    учебного плана ВСГУТУ (parsers/esstu_parser.py). Тот же паттерн, что уже трижды
    применён для subject_hours: create_all не досоздаёт колонки в УЖЕ существующей
    таблице на боевой БД, только ALTER по одной."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("groups")}
    except Exception:
        return  #таблицы ещё нет — create_all создаст её сразу с обеими колонками
    with engine.begin() as conn:
        if "specialty_code" not in columns:
            conn.execute(text("ALTER TABLE groups ADD COLUMN specialty_code VARCHAR"))
        if "enrollment_year" not in columns:
            conn.execute(text("ALTER TABLE groups ADD COLUMN enrollment_year INTEGER"))


def _ensure_group_category_column():
    """Идемпотентная мини-миграция: groups.category — категория расписания портала
    (schedule/parser.py::CATEGORIES), НЕ путать с specialty_code (тот с другого
    сайта). Тот же паттерн ALTER-по-одной, что и у миграции выше."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("groups")}
    except Exception:
        return  #таблицы ещё нет — create_all создаст её сразу с колонкой
    if "category" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE groups ADD COLUMN category VARCHAR"))


def _ensure_schedule_override_subgroup_column():
    """Идемпотентная мини-миграция: schedule_overrides.subgroup (03.09.2026).

    Позволяет двум разным предметам стоять в ОДНОЙ паре — по одному на подгруппу. До
    этого вторая пара с тем же номером затирала первую, потому что номер входил в
    первичный ключ.

    ⚠️ Тот же паттерн, что у соседних миграций: `create_all` колонку в существующую
    таблицу не добавляет никогда, а в свежей тестовой базе таблица создаётся сразу с
    ней — ветка «колонки не было» там не исполняется, и зелёные тесты про эту миграцию
    не значат ничего. Держит регрессия в `test_db_migrations.py`."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("schedule_overrides")}
    except Exception:
        return  #таблицы ещё нет — create_all создаст её сразу с колонкой
    if "subgroup" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE schedule_overrides ADD COLUMN subgroup INTEGER DEFAULT 0"))


def _ensure_group_archive_columns():
    """Идемпотентная мини-миграция: архив групп (03.09.2026).

    `groups.archived/archived_at/archived_reason/last_course/last_course_year` — группа,
    не перешедшая на следующий курс в новом учебном году, уходит в архив вместо удаления.

    ⚠️ Тот же паттерн ALTER-по-одной, что и у четырёх миграций выше: `create_all`
    досоздаёт только ОТСУТСТВУЮЩИЕ таблицы и НЕ добавляет колонки в существующие. В
    свежей тестовой базе таблица создаётся сразу со всеми полями, поэтому ветка «колонки
    не было» там не исполняется НИКОГДА — зелёные тесты про эту миграцию не значат
    ничего, её держит отдельная регрессия в `test_db_migrations.py`."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("groups")}
    except Exception:
        return  #таблицы ещё нет — create_all создаст её сразу со всеми колонками
    adds = [
        ("archived", "BOOLEAN DEFAULT 0"),
        ("archived_at", "VARCHAR DEFAULT ''"),
        ("archived_reason", "VARCHAR DEFAULT ''"),
        ("last_course", "INTEGER"),
        ("last_course_year", "VARCHAR DEFAULT ''"),
        #Перевод на новый курс (05.09.2026): термин, начиная с которого назначения
        #преподавателей обязаны быть ЯВНЫМИ. Пусто — группу не переводили, мост
        #`_assignments_fallback` работает как прежде (см. course_rollover.py).
        ("assignments_reset_term", "VARCHAR DEFAULT ''"),
    ]
    with engine.begin() as conn:
        for name, coltype in adds:
            if name not in columns:
                conn.execute(text("ALTER TABLE groups ADD COLUMN %s %s" % (_ddl_ident(name), _ddl_type(coltype))))


def _ensure_user_prefs_column():
    """Идемпотентная мини-миграция: добавляет столбец users.prefs на УЖЕ
    существующей базе. create_all создаёт только отсутствующие ТАБЛИЦЫ, новые
    столбцы он не досоздаёт — поэтому на старой базе колледжа prefs надо добавить
    ALTER-ом. На свежей БД столбец уже создан через create_all, и мы просто выходим."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("users")}
    except Exception:
        return  #таблицы ещё нет — её только что создал create_all со столбцом
    if "prefs" in columns:
        return
    #Тип столбца — JSON: в SQLite он хранится как TEXT, а SQLAlchemy сам сериализует
    #и разбирает значение. База у продукта одна (см. шапку модуля), поэтому выбирать
    #тип по СУБД больше не нужно.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN prefs JSON"))


def _ensure_user_patronymic_column():
    """Идемпотентная мини-миграция: добавляет столбец users.patronymic (отчество
    отдельным полем) на уже существующей базе. На свежей БД он создан create_all."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("users")}
    except Exception:
        return
    if "patronymic" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN patronymic VARCHAR DEFAULT ''"))


def _backfill_patronymic():
    """Разово заполняет patronymic из уже существующих ФИО: отчество исторически
    хранилось внутри name («Имя Отчество»). Берём хвост после ПЕРВОГО пробела.

    ВАЖНО: name НЕ меняем (он — ключ оценок/синка). Трогаем только строки, где
    patronymic ещё пуст, а в name есть пробел — идемпотентно и безопасно к повтору.
    Заполненное вручную отчество не перетираем."""
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT id, name FROM users WHERE role='student' "
                "AND (patronymic IS NULL OR patronymic='') "
                "AND name LIKE '% %'")).fetchall()
            for uid, name in rows:
                patr = (name or "").split(" ", 1)[1].strip() if " " in (name or "") else ""
                if patr:
                    conn.execute(text("UPDATE users SET patronymic=:p WHERE id=:i"),
                                 {"p": patr, "i": uid})
    except Exception as e:
        print(f"[db] backfill patronymic пропущен: {e}")


#День августа, с которого «текущим» считается уже НОВЫЙ учебный год. Вынесен константой
#(а не зашит в условие) — это учебная политика колледжа, её меняют, а не рефакторят.
def default_term() -> tuple:
    """Текущий учебный термин по дате сервера: (год «YYYY/YYYY+1», семестр 1|2).

    Календарь:
      • сен–дек   → осень (сем1) наступившего года Y/Y+1;
      • янв       → осень (сем1) продолжается, год Y-1/Y;
      • фев–авг   → весна (сем2), год Y-1/Y (ВЕСЬ июль и август целиком).

    ⚠️ ГРАНИЦА ЛЕТА МЕНЯЛАСЬ ДВАЖДЫ, и это не произвол — обе правки чинили одну и ту
    же болезнь, просто вторая доводит первую до конца. 1 ИЮЛЯ (до 3.6): занятия,
    заведённые летом на сентябрь, сразу попадали в текущий термин — удобно, но от
    термина считается ещё и КУРС студента (`study_hours.course_and_semester`), а курс
    группы независимо показывает портал ВСГУТУ по своему индексу расписания — и всё
    лето эти два источника расходились ровно на год: К74/1 по расписанию второй курс,
    по нашей дате уже третий.

    25 АВГУСТА (3.6) должно было это закрыть «с запасом на подготовку», но не закрыло
    до конца — ЖИВОЙ отзыв 3.6.1 поймал ту же болезнь в миниатюре, просто на неделю
    короче: портал (и реальный учебный год) переключается РОВНО 1 сентября, а наша
    граница стояла на неделю раньше — и всю эту неделю импорт учебного плана в группу
    (`admin_import_esstu`/`W.current_term`) считал курс уже НОВЫМ, а расписание
    портала — ещё СТАРЫМ, то же расхождение К74/1 «2 курс vs 3 курс» возвращалось.

    Граница ТЕПЕРЬ РОВНО 1 СЕНТЯБРЯ: единственная дата, которая не расходится с
    порталом НИКОГДА (не «с запасом», а без исключений), ценой недели более позднего
    появления в системе разделов на новый год — кому нужно раньше, выбирает термин
    вручную селектором, это осознанный клик, а не автоматика по умолчанию.

    Единый источник дефолта для миграции и конфига (data/terms.py дублирует —
    формула обязана совпадать ДО СИМВОЛА, иначе ключи term_grades разъедутся).

    ⚠️ САМА ФОРМУЛА ЖИВЁТ В `term_for_date` (05.09.2026). Здесь остался только «какая
    сейчас дата»: перевод группы на новый курс обязан спрашивать термин у ДАТЫ ЗАНЯТИЯ,
    а не у сегодняшнего числа, и без выноса появилась бы ТРЕТЬЯ копия календаря — при
    том что докстринг выше сам предупреждает, чем кончаются копии."""
    from datetime import datetime, timezone
    return term_for_date(datetime.now(timezone.utc))


def term_for_date(when) -> tuple:
    """Учебный термин, которому принадлежит УКАЗАННАЯ дата: (год «YYYY/YYYY+1», 1|2).

    Календарь — тот же, что описан в `default_term` (сен–дек и январь — осень, фев–авг —
    весна). Отдельная функция нужна `course_rollover`: занятие без штампа термина надо
    отнести к периоду, в котором оно РЕАЛЬНО было, а это знает только его дата.

    ⚠️ Копий формулы быть не должно — `default_term()` зовёт эту же функцию. Иначе
    «сегодняшний» и «датный» термины разошлись бы на границе 1 сентября, и занятие,
    заведённое 31 августа, попало бы в один период, а посчиталось бы в другом.
    """
    y, m = when.year, when.month
    if m >= 9:
        return f"{y}/{y + 1}", 1
    if m == 1:
        return f"{y - 1}/{y}", 1
    return f"{y - 1}/{y}", 2


def _ensure_participant_state_columns():
    """Идемпотентная мини-миграция: столбцы conversation_participants.muted / .pinned
    (мьют беседы и закрепление чата у пользователя). На свежей БД их создаёт create_all;
    на базе, где таблица участников появилась раньше этих полей, добавляем ALTER-ом —
    иначе запрос/запись p.muted упали бы. Таблицы может не быть (мессенджер не
    инициализирован) — тогда просто выходим."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("conversation_participants")}
    except Exception:
        return  #таблицы ещё нет — create_all создаст её со всеми столбцами
    with engine.begin() as conn:
        if "muted" not in columns:
            conn.execute(text(
                "ALTER TABLE conversation_participants ADD COLUMN muted BOOLEAN DEFAULT 0"))
        if "pinned" not in columns:
            conn.execute(text(
                "ALTER TABLE conversation_participants ADD COLUMN pinned BOOLEAN DEFAULT 0"))
        #«Удалить переписку у себя»: граница видимости истории и скрытие чата из списка.
        if "cleared_at" not in columns:
            conn.execute(text(
                "ALTER TABLE conversation_participants ADD COLUMN cleared_at VARCHAR DEFAULT ''"))
        if "hidden" not in columns:
            conn.execute(text(
                "ALTER TABLE conversation_participants ADD COLUMN hidden BOOLEAN DEFAULT 0"))
        if "archived" not in columns:
            conn.execute(text(
                "ALTER TABLE conversation_participants ADD COLUMN archived BOOLEAN DEFAULT 0"))
        #Граница очистки по номеру сообщения (устойчива к совпадению тика часов).
        if "cleared_upto_id" not in columns:
            conn.execute(text(
                "ALTER TABLE conversation_participants ADD COLUMN cleared_upto_id INTEGER DEFAULT 0"))
        #§ролей (3.1.5): кастомная роль беседы + «/mute»-заглушка модератора.
        if "custom_role_id" not in columns:
            conn.execute(text(
                "ALTER TABLE conversation_participants ADD COLUMN custom_role_id VARCHAR"))
        if "silenced" not in columns:
            conn.execute(text(
                "ALTER TABLE conversation_participants ADD COLUMN silenced BOOLEAN DEFAULT 0"))


def _ensure_grade_student_id_columns():
    """Идемпотентная мини-миграция: grades.student_id / term_grades.student_id.

    ЭТАП 1 перехода с ФИО-ключей на неизменяемый id студента. Столбец ДОБАВОЧНЫЙ:
    первичный ключ остаётся прежним (f|n|lesson_id), поэтому старые клиенты, которые о
    поле не знают, продолжают работать — они просто шлют записи без него.

    Зачем вообще: оценка ключуется по написанию ФИО. Студентка выходит замуж, админ
    правит фамилию — и вся история оценок остаётся висеть на старом ключе. id строки
    users не меняется никогда, поэтому привязка к нему такой проблемы не имеет.
    """
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in ("grades", "term_grades"):
            try:
                columns = {c["name"] for c in insp.get_columns(table)}
            except Exception:
                continue        #таблицы ещё нет — create_all создал её уже со столбцом
            if "student_id" not in columns:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN student_id VARCHAR DEFAULT ''"))


def _ensure_lesson_term_columns():
    """Идемпотентная мини-миграция: столбцы lessons.year / lessons.semester (учебный
    период). На свежей БД создаются через create_all, на существующей — ALTER-ом."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("lessons")}
    except Exception:
        return
    with engine.begin() as conn:
        if "year" not in columns:
            conn.execute(text("ALTER TABLE lessons ADD COLUMN year VARCHAR DEFAULT ''"))
        if "semester" not in columns:
            conn.execute(text("ALTER TABLE lessons ADD COLUMN semester INTEGER DEFAULT 0"))


def _backfill_lesson_term():
    """Разово проставляет период занятиям, у которых он ещё не задан (историческим).
    Ставим ТЕКУЩИЙ термин по дате — чтобы старые занятия попали в актуальный период и
    не «выпали» из фильтра. Идемпотентно: трогаем только year='' / NULL."""
    from sqlalchemy import text
    try:
        y, s = default_term()
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE lessons SET year=:y, semester=:s "
                "WHERE (year IS NULL OR year='') "), {"y": y, "s": s})
            #у части могло проставиться year, но semester=0 — добьём семестр
            conn.execute(text(
                "UPDATE lessons SET semester=:s WHERE (semester IS NULL OR semester=0)"),
                {"s": s})
    except Exception as e:
        print(f"[db] backfill lesson term пропущен: {e}")


def _ensure_user_password_set_at_column():
    """Идемпотентная мини-миграция: users.password_set_at (когда пароль выдан в
    последний раз). Существующим строкам НЕ проставляем дату задним числом — пусто
    честно значит «неизвестно», а выдуманная дата выглядела бы как факт."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("users")}
    except Exception:
        return
    if "password_set_at" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN password_set_at VARCHAR"))


def _ensure_user_birthday_column():
    """Идемпотентная мини-миграция: users.birthday («ДД.ММ», без года).

    ⚠️ Одного `models.py` тут НЕДОСТАТОЧНО: `create_all` досоздаёт отсутствующие
    ТАБЛИЦЫ, но не добавляет колонки в существующую. На свежей тестовой базе разницы
    не видно — таблица создаётся сразу целиком, — поэтому проверять надо на боевой
    схеме, и ровно на этом проект уже обжигался (см. participant_state)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("users")}
    except Exception:
        return
    if "birthday" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN birthday VARCHAR"))


def _ensure_message_report_target_column():
    """Идемпотентная мини-миграция: message_reports.target_kind — на ЧТО жалоба
    (message | activity_feedback, см. PLAN-ACTIVITIES §8.3).

    Таблица жалоб на проде существует давно, а `create_all` досоздаёт только целые
    таблицы, но НЕ колонки в существующие — этот же промах уже чуть не улетел на прод с
    `conversation_participants` (§10 CLAUDE.md). Умолчание проставляем и НОВОЙ колонке, и
    УЖЕ накопленным строкам: без второго UPDATE старые тикеты приехали бы с NULL, и
    фильтр `target_kind == "message"` перестал бы их находить — то есть вся прежняя
    очередь модерации молча исчезла бы из вкладки «Жалобы»."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("message_reports")}
    except Exception:
        return
    if "target_kind" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE message_reports ADD COLUMN target_kind VARCHAR "
                          "DEFAULT 'message'"))
        conn.execute(text("UPDATE message_reports SET target_kind = 'message' "
                          "WHERE target_kind IS NULL"))


def _ensure_user_curated_groups_column():
    """Идемпотентная мини-миграция: users.curated_groups (JSON-список групп, которые
    курирует преподаватель). Непустой список = роль куратора (role остаётся teacher)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("users")}
    except Exception:
        return
    if "curated_groups" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN curated_groups JSON"))


def _ensure_notify_event_columns():
    """Идемпотентная мини-миграция: notify_events.title/body/payload — готовый текст
    уведомления для вкладки «Уведомления».

    Таблица уже существует на проде (приехала вместе с пушами об оценках), а create_all
    новые СТОЛБЦЫ не досоздаёт — поэтому ALTER. Уже накопленные события останутся с
    пустым текстом, и это нормально: клиент умеет показать их по kind, как и раньше.
    Задним числом сочинять им тело мы не имеем права — человек получал другое."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    try:
        columns = {c["name"] for c in insp.get_columns("notify_events")}
    except Exception:
        return          #таблицы ещё нет — create_all создал её сразу со столбцами
    #Столбцы добавляем по одному: часть могла появиться в прошлый запуск, а СУБД
    #откажет на попытке добавить уже существующий.
    wanted = (("title", "VARCHAR DEFAULT ''"),
              ("body", "VARCHAR DEFAULT ''"),
              ("payload", "JSON"),
              #Автор и метка партии — для вкладки «Отправленные». У писем, накопленных
              #до этой правки, останутся пустыми: кто их отправил, задним числом не
              #восстановить, и придумывать автора нельзя.
              ("author_login", "VARCHAR DEFAULT ''"),
              ("batch_id", "VARCHAR DEFAULT ''"))
    with engine.begin() as conn:
        for name, coltype in wanted:
            if name not in columns:
                conn.execute(text(
                    f"ALTER TABLE notify_events ADD COLUMN {name} {coltype}"))


def _migrate_slash_in_ids():
    """Убирает «/» из идентификаторов бесед и отчётов (см. messenger._gtoken).

    Группы колледжа называются «К74/1», и слэш попадал в id системного канала
    («sys:announce:К74/1») и отчёта («rpt:К74/1|3»). В URL он приезжает как %2F,
    Starlette раскодирует его обратно ДО подбора роута, путь распадается на лишний
    сегмент — и запрос не совпадает ни с одним эндпоинтом мессенджера: GET проваливался
    в SPA-фолбэк (клиент получал HTML вместо JSON — оверлей отчёта открывался и тут же
    закрывался), POST отвечал 405. У групп без слэша всё работало, поэтому баг долго
    выглядел как «иногда не открывается».

    Меняем «/» на «~» ВЕЗДЕ, где такой id хранится, одной транзакцией: сама беседа,
    участники, сообщения, отчёты и их кнопки (тело сообщения kind='report' — это id
    отчёта), напоминания. Строки без слэша не трогаем, повторный запуск безвреден."""
    from sqlalchemy import text
    #Что чиним: (таблица, столбец, условие отбора). Порядок неважен — это одна транзакция.
    targets = [
        ("conversations", "id", "id LIKE 'sys:%' AND id LIKE '%/%'"),
        ("conversation_participants", "conversation_id",
         "conversation_id LIKE 'sys:%' AND conversation_id LIKE '%/%'"),
        ("messages", "conversation_id",
         "conversation_id LIKE 'sys:%' AND conversation_id LIKE '%/%'"),
        ("curator_reports", "id", "id LIKE 'rpt:%' AND id LIKE '%/%'"),
        ("curator_reports", "conversation_id",
         "conversation_id LIKE 'sys:%' AND conversation_id LIKE '%/%'"),
        ("messages", "body", "kind = 'report' AND body LIKE 'rpt:%' AND body LIKE '%/%'"),
        ("reminders", "conversation_id",
         "conversation_id LIKE 'sys:%' AND conversation_id LIKE '%/%'"),
    ]
    fixed = 0
    with engine.begin() as conn:
        for table, column, where in targets:
            try:
                res = conn.execute(text(
                    #SAST B608: table/column/where — из нашего же описания
                    #миграции, снаружи не приходят.
                    f"UPDATE {table} SET {column} = replace({column}, '/', '~') WHERE {where}"))  # nosec B608
                fixed += res.rowcount or 0
            except Exception:
                continue        #таблицы ещё нет (свежая БД) — чинить нечего
    if fixed:
        print(f"[db] id со слэшем починены: {fixed} строк (см. _migrate_slash_in_ids)")
