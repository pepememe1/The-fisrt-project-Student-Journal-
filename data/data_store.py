"""
data_store.py — Локальное хранилище данных GradeBookAI (SQLite).

Никаких обращений в интернет напрямую отсюда нет.

Архитектура (offline-first):
  - Рабочее чтение/запись идёт в локальный SQLite (мгновенно, без блокировки UI).
  - Обмен с общей базой колледжа идёт через REST API-сервер ВСГУТУ отдельным
    фоновым потоком (см. sync_runner) — прямого подключения к серверной БД нет.

Данные хранятся в таблице kv_store (ключ → JSON):
  students, teachers, groups, config

Пароли НЕ хранятся в открытом виде: при записи поле "password" автоматически
превращается в "password_hash" (PBKDF2-HMAC-SHA256, см. security.py).
"""
import json
import log


#Сколько раз подряд не удалось разбудить синк. Считаем, чтобы сказать об этом ОДИН раз:
#функция зовётся на каждое сохранение данных, и построчный лог утопил бы всё остальное.
_wake_failures = 0


def _wake_sync() -> None:
    """Разбудить фоновую синхронизацию, чтобы правка уехала сразу, а не через интервал.

    Три копии этого кода жили в трёх местах, и все три глотали ошибку молча. Сбой тут
    НЕ страшен сам по себе — цикл синка всё равно проснётся по своему интервалу, — но
    молчание означало, что «правка сохранена, а наверх не поехала» выглядело бы
    совершенно нормально. Пишем в лог ОДИН раз: повторять на каждое сохранение нельзя.

    ⚠️ Первый сбой — штатная ситуация на пути запуска: данные пишутся ДО того, как
    пользователь вошёл и синк стартовал. Поэтому уровень debug, а не warning.
    """
    global _wake_failures
    try:
        from sync.sync_runner import trigger as _sync_trigger
        _sync_trigger()
        _wake_failures = 0
    except Exception as e:      # noqa: BLE001
        _wake_failures += 1
        if _wake_failures == 1:
            log.get("data_store").debug(
                "[sync] не удалось разбудить синк (%s) — правка уедет очередным циклом", e)

#Сбой одного и того же рода сообщаем ОДИН раз за запуск (те же соображения, что у
#_wake_failures выше и _report_once в data/core.py): функции ниже зовутся на каждое
#чтение таблицы/kv, построчный лог утопил бы настоящие записи, и его перестали бы читать.
_reported: set[str] = set()


def _report_once(tag: str, message: str, *args, level: str = "warning") -> None:
    """Записать сбой в лог один раз за запуск (ключ повтора — tag)."""
    if tag in _reported:
        return
    _reported.add(tag)
    getattr(log.get("data_store"), level)(message, *args)


from data.core import DBManager
from data.security import hash_password, verify_password, encrypt_value, decrypt_value

#Логин администратора не секрет (секрет — пароль). Дефолтного ПАРОЛЯ больше нет:
#раньше тут лежал захардкоженный "vsgutu_admin_online", который принимался при
#первом входе — это был бэкдор.
#⚠️ ЧЕСТНО ПРО «задаётся вручную при первом запуске»: экрана, который это делал
#(`auth_pages` в Qt-оболочке), больше нет, и `setup_admin_password` ниже не зовёт НИКТО.
#Сегодня первый администратор заводится НА СЕРВЕРЕ — `POST /auth/bootstrap-admin`
#(на непустой базе он отвечает 403), а десктоп получает пользователей синком. Локальная
#ветка осталась заготовкой на случай полностью автономного ПК; работоспособной её
#считать нельзя, пока у неё нет вызывающего.
DEFAULT_ADMIN_LOGIN = "admin"

#Старый скомпрометированный дефолтный пароль. Его мог записать в базу старый код.
#Новый код считает такой пароль НЕ заданным: вход с ним запрещён, а при попытке
#войти администратором запускается принудительная установка нового пароля.
#Здесь он нужен ТОЛЬКО чтобы распознать и отвергнуть наследие — это не бэкдор.
_LEGACY_DEFAULT_ADMIN_PASSWORD = "vsgutu_admin_online"


def _is_legacy_default(stored_hash: str) -> bool:
    """True, если сохранённый хеш — это старый дефолтный пароль (его надо отвергнуть)."""
    return bool(stored_hash) and verify_password(_LEGACY_DEFAULT_ADMIN_PASSWORD, stored_hash)


class AccountLocked(Exception):
    """Логин временно заблокирован из-за серии неверных попыток (анти-брутфорс)."""
    def __init__(self, seconds_left: int):
        super().__init__(f"Вход заблокирован, осталось {seconds_left} с")
        self.seconds = seconds_left


#Низкоуровневый key-value доступ (SQLite + async PG)
def _ensure_kv(cur):
    cur.execute(
        "CREATE TABLE IF NOT EXISTS kv_store "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )


#Модель хранения kv_store (студенты, преподаватели, конфиг, пароль-хеш админа):
#Локально в SQLite значение ЗАШИФРОВАНО ключом этого ПК (DPAPI-привязка к
#учётной записи Windows) — защищает данные на украденном/чужом компьютере,
#при этом ничего не спрашивает у пользователя.
#В общую базу колледжа эти данные попадают через API-сервер ВСГУТУ (см.
#sync_runner); канал защищён TLS, сервер размещается в РФ (152-ФЗ). Пароли
#пользователей в любом случае хранятся только хешами.
def _kv_get(key: str, default):
    conn = DBManager.get_conn()
    cur = conn.cursor()
    _ensure_kv(cur)
    cur.execute("SELECT value FROM kv_store WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    if row:
        try:
            #decrypt_value прозрачно вернёт и открытый текст (на случай миграции).
            return json.loads(decrypt_value(row[0]))
        except Exception as e:
            #Значение в kv не расшифровалось/не разобралось — раньше молча возвращался
            #default, и «настройка сбросилась» было неотличимо от «её не задавали».
            #Значение по умолчанию оставляем (уронить чтение нельзя), но след обязателен.
            _report_once(f"kv:{key}", "[kv] значение «%s» не прочитано (%s) — взят default", key, e)
            return default
    return default


def _kv_set(key: str, value, wake: bool = True) -> bool:
    plain = json.dumps(value, ensure_ascii=False)
    local_blob = encrypt_value(plain)        # локально (SQLite) — шифруем
    conn = DBManager.get_conn()
    cur = conn.cursor()
    _ensure_kv(cur)
    cur.execute("INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)", (key, local_blob))
    conn.commit()
    conn.close()
    #Разбудить фоновую синхронизацию с сервером (API-режим), чтобы изменение
    #ушло сразу, а не через интервал. Офлайн/нет сервера — это безвредный no-op.
    #wake=False нужен для служебных записей (например, метки last_sync), которые сами
    #по себе НЕ являются данными для отправки — иначе их запись будила бы синк, а тот
    #опять писал бы метку: получился бы бесконечный цикл «синк → метка → синк».
    if wake:
        _wake_sync()
    return True


#Метка последней синхронизации (граница дельта-pull). ЛОКАЛЬНАЯ и устройство-
#зависимая: на сервер НЕ уходит (collect_local не собирает этот ключ) и не будит
#синк (wake=False). Хранит server_time последнего успешного pull — следующий pull
#просит у сервера только изменения позже неё (не качаем всю базу каждый цикл).
_SYNC_WATERMARK_KEY = "_sync_watermark"


def get_sync_watermark() -> str:
    """Метка времени последнего успешного pull ('' — синхронизаций ещё не было)."""
    val = _kv_get(_SYNC_WATERMARK_KEY, "")
    return val if isinstance(val, str) else ""


def set_sync_watermark(server_time: str):
    """Сохраняет границу дельты молча (без пробуждения синка)."""
    _kv_set(_SYNC_WATERMARK_KEY, server_time or "", wake=False)


#Граница дельта-PUSH — зеркало предыдущей метки, но для отправки. Раньше push слал
#ПОЛНЫЙ снимок локальной базы каждый цикл: сервер применял только изменившееся, зато
#трафик рос вместе с числом занятий/оценок (на большой базе — мегабайты каждые 30 с).
#Метка ЛОКАЛЬНАЯ (на сервер не уходит, синк не будит), хранит локальное UTC-время
#начала последнего успешного push.
_PUSH_WATERMARK_KEY = "_push_watermark"


def get_push_watermark() -> str:
    """Локальное время начала последнего успешного push ('' — пушей ещё не было)."""
    val = _kv_get(_PUSH_WATERMARK_KEY, "")
    return val if isinstance(val, str) else ""


def set_push_watermark(local_time: str):
    """Сохраняет границу дельта-push молча (без пробуждения синка)."""
    _kv_set(_PUSH_WATERMARK_KEY, local_time or "", wake=False)


#Локальные настройки ПК (НЕ синхронизируются): адрес сервера API и т.п.
#Лежат в том же kv_store, но под служебным префиксом "_local:" и пишутся с
#wake=False. collect_local их не собирает (на сервер уходят только
#users/groups/subjects/config/lessons/grades), поэтому на чужие ПК они не
#уезжают и синк не будят. Идея: локальная настройка живёт «в программе» (в её
#БД), а не в отдельном json-файле рядом с exe, который правят руками.
def local_get(key: str, default=None):
    """Читает локальную настройку этого ПК ('' / default — если не задана)."""
    return _kv_get(f"_local:{key}", default)


def local_set(key: str, value) -> bool:
    """Сохраняет локальную настройку этого ПК (молча, без пробуждения синка)."""
    return _kv_set(f"_local:{key}", value, wake=False)


def _now_iso() -> str:
    from datetime import datetime, timezone
    #Время в UTC и с микросекундами. UTC — чтобы метки разных ПК сравнивались
    #честно, без зависимости от часового пояса/перевода часов (LWW по строке).
    #Микросекунды — чтобы создание и удаление в одну секунду различались.
    return datetime.now(timezone.utc).isoformat()


def _stamp_records(new_records, old_records, key_fn):
    """Проставляет updated_at=now только тем записям, что появились или изменились
    (сравнение с прошлой версией по ключу, без учёта самого updated_at). Неизменные
    сохраняют свою метку. Так синхронизация шлёт только реальные изменения, а не
    весь список заново — корректный LWW между ПК без лишнего «шума»."""
    now = _now_iso()
    old_map = {key_fn(r): r for r in old_records}
    for r in new_records:
        prev = old_map.get(key_fn(r))
        changed = (prev is None or
                   {k: v for k, v in r.items() if k != "updated_at"} !=
                   {k: v for k, v in prev.items() if k != "updated_at"})
        if changed:
            r["updated_at"] = now
        elif not r.get("updated_at"):
            r["updated_at"] = prev.get("updated_at", now)
    return new_records


def _merge_list_tombstones(new_live, old_raw, key_fn):
    """Возвращает «сырой» список со штампами и надгробиями: записи, которые БЫЛИ,
    а в новом списке исчезли, помечаются deleted=True (а не пропадают) — чтобы
    удаление доехало до других ПК. Существующие надгробия сохраняются."""
    now = _now_iso()
    old_map = {key_fn(r): r for r in old_raw}
    old_live = [r for r in old_raw if not r.get("deleted")]
    _stamp_records(new_live, old_live, key_fn)
    out, new_keys = [], set()
    for r in new_live:
        r["deleted"] = False
        new_keys.add(key_fn(r))
        out.append(r)
    for k, r in old_map.items():
        if k in new_keys:
            continue
        if r.get("deleted"):
            out.append(r)                       # уже надгробие — сохраняем
        else:
            t = dict(r); t["deleted"] = True; t["updated_at"] = now
            out.append(t)                       # было живым, исчезло → надгробие
    return out

#  Группы: хранятся в ТАБЛИЦЕ groups (серверная форма), а не JSON-блобом в kv_store —
#  синк ходит прямым upsert'ом без переводчика (план техдолга №2). Публичный API
#  get_groups/set_groups сохраняет прежнюю форму [{name, subjects, updated_at, deleted}],
#  поэтому UI не трогаем. Имена групп/предметы не ПДн — таблица без шифрования оправдана
#  (в отличие от users с хешами паролей, которые остаются в зашифрованном kv).
def _groups_read_raw() -> list:
    """Все группы из таблицы (вкл. надгробия) в форме [{name, subjects, updated_at,
    deleted, category, specialty_code, enrollment_year}].

    Последние три поля пишет только сервер (импорт учебного плана/категории
    расписания через REST — _open_import_esstu/_open_import_schedule_category),
    десктопный UI их не редактирует напрямую — но ОБЯЗАН прочитать и вернуть
    как есть: set_groups() перезаписывает ВСЮ таблицу целиком, и если эти поля
    не попадут в прочитанный словарь, любая локальная правка (например, правка
    предметов группы через _open_group) молча обнулила бы их у ВСЕХ групп при
    следующей записи — не только у редактируемой."""
    _ensure_groups_migrated()
    conn = DBManager.get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT name, COALESCE(subjects,'[]'), COALESCE(updated_at,''), "
                    "COALESCE(deleted,0), category, specialty_code, enrollment_year FROM groups")
        rows = cur.fetchall()
    except Exception:
        #🔥 РАНЬШЕ ЗДЕСЬ БЫЛО `rows = []`. Это ТА ЖЕ мина, что уже обезврежена в
        #`_users_table_read` — просто починили тогда одну таблицу из двух.
        #Пробрасываем — решение «писать ли поверх» принимает вызывающий, он знает, чем
        #рискует; молча вернуть «групп нет» не имеет права никто.
        #
        #Что это чинит СЕГОДНЯ: единственные читатели этой функции в продукте —
        #`data/utils.py::get_groups()`. С пустым списком он честно отвечал «групп нет»
        #при залоченной или битой базе, и вызывающий не мог отличить это от реальной
        #пустоты. Теперь отличит.
        #
        #⚠️ ЧЕГО ЭТО НЕ ЧИНИТ, вопреки прежней редакции комментария. Здесь стояло, что
        #пустое чтение убивает надгробия в `set_groups(stamp=True)` и «админ удалил
        #группу, у себя её не видит, а на сайте она живёт». Цепочка по коду верна
        #(`_merge_list_tombstones` сравнивает новый список с тем, что сейчас в базе), но
        #ВХОДА В НЕЁ СЕГОДНЯ НЕТ: `set_groups()` не зовёт ни один продуктовый модуль —
        #только тесты. Нативной админки групп нет с удаления Qt, а SPA удаляет группу
        #через `/web/admin/groups` на локальном сервере, в `local_app.db`, мимо
        #`data_store`. Сам синк пишет группы прямо в таблицу (`sync_engine`), тоже минуя
        #`set_groups`. Формулировку исправляем, чтобы следующий читатель не решил по ней,
        #что удаление групп на десктопе живо, — но `raise` остаётся: путь надгробий
        #существует в коде, и появись у него вызывающий, мина сработала бы снова.
        conn.close()
        raise
    conn.close()
    out = []
    for name, subj, uat, deleted, category, specialty_code, enrollment_year in rows:
        try:
            subjects = json.loads(subj) if subj else []
        except Exception as e:
            #Битый JSON в одной строке не должен ронять весь список групп: пустой список
            #предметов у ОДНОЙ группы виден человеку сразу, а исключение здесь закрыло бы
            #экран целиком. Но след оставляем — раньше эта ветка молчала, и «у группы
            #пропали предметы» выглядело как чужая ошибка.
            log.get("data_store").warning(
                f"[groups] не разобран список предметов группы {name!r}: {e}")
            subjects = []
        out.append({"name": name, "subjects": subjects,
                    "updated_at": uat, "deleted": bool(deleted),
                    "category": category or "", "specialty_code": specialty_code or "",
                    "enrollment_year": enrollment_year})
    return out


def _groups_write_raw(groups: list) -> None:
    """Полностью перезаписывает таблицу групп СЫРЫМ списком (уже со штампами/надгробиями).

    category/specialty_code/enrollment_year — см. докстринг _groups_read_raw: ЛЮБОЙ
    вызывающий код обязан был получить их через get_groups()/get_groups_raw() и
    передать обратно как есть, здесь просто сохраняем то, что пришло (пусто —
    значит вызывающий код и не должен был их знать, не наша забота гадать)."""
    conn = DBManager.get_conn(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS groups (id TEXT PRIMARY KEY, name TEXT, "
                "subjects TEXT DEFAULT '[]', updated_at TEXT DEFAULT '', deleted INTEGER DEFAULT 0, "
                "category TEXT, specialty_code TEXT, enrollment_year INTEGER)")
    cur.execute("DELETE FROM groups")
    for g in groups:
        name = g.get("name", "")
        cur.execute("INSERT OR REPLACE INTO groups "
                    "(id,name,subjects,updated_at,deleted,category,specialty_code,enrollment_year) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (f"grp:{name}", name,
                     json.dumps(g.get("subjects") or [], ensure_ascii=False),
                     g.get("updated_at", ""), 1 if g.get("deleted") else 0,
                     g.get("category") or None, g.get("specialty_code") or None,
                     g.get("enrollment_year")))
    conn.commit(); conn.close()


def _ensure_groups_migrated() -> None:
    """Одноразовый перенос групп kv_store → таблица (старые базы). Идемпотентно по СОСТОЯНИЮ
    БД (не по флагу — чтобы работало и в тестах): если таблица пуста, а в kv есть группы —
    копируем и убираем kv-ключ. Дёшево (один COUNT), безопасно звать на каждом чтении."""
    conn = DBManager.get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM groups")
        has_rows = cur.fetchone()[0] > 0
    except Exception as e:
        #«no such table» — штатно (init создаст), молчим. Любая ДРУГАЯ причина
        #(залочена/битая база) тоже пропускает миграцию — это уже стоит видеть.
        has_rows = True
        if "no such table" not in str(e).lower():
            _report_once("mig_groups",
                         "[mig] проверка таблицы groups не удалась (%s) — миграция пропущена", e)
    conn.close()
    if has_rows:
        return
    kv_groups = _kv_get("groups", None)
    if kv_groups:
        _groups_write_raw(kv_groups)
        conn = DBManager.get_conn(); cur = conn.cursor()
        cur.execute("DELETE FROM kv_store WHERE key='groups'")
        conn.commit(); conn.close()


#  Пользователи (студенты/преподаватели) — в таблице users, но payload ЗАШИФРОВАН blob'ом
#  (Fernet+DPAPI: та же защита, что была у kv). Хранится СЕРВЕРНАЯ форма → синк ходит прямым
#  upsert'ом по строке (без переводчика). data_store конвертирует server↔desktop-форму на
#  границе UI (get/set_students, get/set_teachers), поэтому UI не трогаем. Админ здесь НЕ
#  живёт (его хеш — в config). План техдолга №2, Стадия 2.
def _student_id(s: dict) -> str:
    """Идентификатор студента для синка. Порядок источников важен.

    1. Уже присвоенный id — возвращаем как есть. Он НЕИЗМЕНЯЕМ: id это ключ строки в
       users на сервере, и его смена = осиротевший аккаунт плюс рассинхрон десктопа.
    2. Логин — стабилен (смена логина трактуется как новый аккаунт, см. web.py).
    3. Иначе — СЛУЧАЙНЫЙ id `stud:u:{uuid4}`.

    Пункт 3 — исправление старого дефекта. Раньше безлогинный студент получал
    `stud:{фамилия}|{имя}|{группа}`, то есть его id зависел от ФИО: переименование или
    перевод в другую группу порождали НОВОГО пользователя, а старая строка оставалась
    висеть вместе с оценками. Ровно та же мина, что и в ключах оценок. Случайный id её
    снимает: ФИО меняется сколько угодно, id держит связь.

    ⚠️ Уже существующим студентам id НЕ пересчитываем (ветка 1) — на боевых данных это
    оборвало бы связь с их оценками и аккаунтом на сервере."""
    import uuid
    existing = (s.get("id") or "").strip()
    if existing:
        return existing
    login = (s.get("login") or "").strip()
    return f"stud:{login}" if login else f"stud:u:{uuid.uuid4()}"


def student_id_by_name(f: str, n: str, group: str = "") -> str:
    """ФИО (+ группа, если задана) → id студента. '' — не нашли или неоднозначно.

    Однофамильцы с одинаковым именем в РАЗНЫХ группах — обычное дело, поэтому при
    нескольких совпадениях без указания группы возвращаем '': лучше оставить привязку по
    ФИО, чем уверенно приписать оценку не тому человеку. Надгробия пропускаем."""
    f, n, group = (f or "").strip(), (n or "").strip(), (group or "").strip()
    if not (f and n):
        return ""
    hits = [u for u in _users_table_read()
            if u.get("role") == "student" and not u.get("deleted")
            and (u.get("surname") or "").strip() == f
            and (u.get("name") or "").strip() == n
            and (not group or (u.get("group_name") or "").strip() == group)]
    return hits[0].get("id", "") if len(hits) == 1 else ""


def _teacher_id(fullname: str, d: dict) -> str:
    login = (d.get("login") or "").strip()
    return f"teach:{login}" if login else f"teach:{fullname}"


def _student_to_server(s: dict) -> dict:
    """desktop-форма студента → строка users(role=student). password_hash тут ПЛЕЙНТЕКСТ
    (шифруется при записи blob'а). Прежний translator students_to_users, но со всеми полями."""
    return {
        "id": _student_id(s), "role": "student",
        "login": s.get("login", ""), "password_hash": s.get("password_hash", ""),
        "surname": s.get("surname", ""), "name": s.get("name", ""),
        "patronymic": s.get("patronymic", ""), "group_name": s.get("group", ""),
        "full_name": "", "subjects": [], "group_assignments": {}, "curated_groups": [],
        "prefs": s.get("prefs") or {},
        "updated_at": s.get("updated_at", "") or _now_iso(),
        "deleted": bool(s.get("deleted", False)),
    }


def _server_to_student(u: dict) -> dict:
    #id ОБЯЗАН доехать до UI-формы и вернуться обратно: на нём держится сопоставление
    #«старый ↔ новый» в _merge_list_tombstones. Потеряем — set_students примет студента
    #за нового (новый случайный id), а прежнюю строку прикопает надгробием вместе с
    #привязкой оценок. Раньше id тут терялся молча — сходило с рук лишь потому, что
    #_student_id пересчитывал его из ФИО (та самая мина, которую и убираем).
    return {
        "id": u.get("id", ""),
        "surname": u.get("surname", ""), "name": u.get("name", ""),
        "group": u.get("group_name", ""), "login": u.get("login", ""),
        "password_hash": u.get("password_hash", ""), "patronymic": u.get("patronymic", ""),
        "prefs": u.get("prefs") or {},
        "updated_at": u.get("updated_at", ""), "deleted": bool(u.get("deleted", False)),
    }


def _teacher_to_server(fullname: str, d: dict) -> dict:
    return {
        "id": _teacher_id(fullname, d), "role": "teacher",
        "login": d.get("login", ""), "password_hash": d.get("password_hash", ""),
        "full_name": fullname, "surname": "", "name": "", "patronymic": "", "group_name": "",
        "subjects": d.get("subjects") or [], "group_assignments": d.get("group_assignments") or {},
        "curated_groups": d.get("curated_groups") or [], "prefs": d.get("prefs") or {},
        "updated_at": d.get("updated_at", "") or _now_iso(),
        "deleted": bool(d.get("deleted", False)),
    }


def _server_to_teacher(u: dict) -> dict:
    """Строка users(role=teacher) → значение teachers-dict (ключ — full_name отдельно).

    "id" нужен для teacher_assignments (§ролей препод↔предмет↔группа) — subject_hours.
    teacher_id хранит именно его, не логин/ФИО, и локальной реконструкцией id не
    восстановить (формат teach:{login|ФИО} — не всегда логин)."""
    return {
        "id": u.get("id", ""),
        "login": u.get("login", ""), "password_hash": u.get("password_hash", ""),
        "subjects": u.get("subjects") or [], "group_assignments": u.get("group_assignments") or {},
        "curated_groups": u.get("curated_groups") or [], "prefs": u.get("prefs") or {},
        "updated_at": u.get("updated_at", ""), "deleted": bool(u.get("deleted", False)),
    }


def _users_table_read() -> list:
    """Все пользователи (студенты+преподаватели, вкл. надгробия) в СЕРВЕРНОЙ форме —
    расшифровываем blob. Мигрирует старую kv-базу при первом доступе."""
    _ensure_users_migrated()
    conn = DBManager.get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, role, COALESCE(updated_at,''), COALESCE(deleted,0), "
                    "COALESCE(blob,'') FROM users")
        rows = cur.fetchall()
    except Exception:
        #🔥 РАНЬШЕ ЗДЕСЬ БЫЛО `rows = []`, И ЭТО ТЕРЯЛО ПРАВКИ МОЛЧА. Пустой список
        #неотличим от «пользователей нет»: push уходил успешным, водяной знак дельты
        #уезжал вперёд, и заведённый офлайн студент, переименованный преподаватель или
        #надгробие удалённого больше в дельту не попадали — до ближайшего полного снимка,
        #то есть до 20 циклов. Защита от этого в синке есть (`SyncSession.collect_failed`),
        #но она рассчитана на ИСКЛЮЧЕНИЕ, а мы его гасили. Пробрасываем: решение «сдвигать
        #ли метку» принимает синк, у него есть контекст, а у нас его нет.
        conn.close()
        raise
    conn.close()
    out = []
    for uid, role, uat, deleted, blob in rows:
        d = {}
        if blob:
            try:
                d = json.loads(decrypt_value(blob))
            except Exception as e:
                #🔥 РАНЬШЕ МОЛЧА `d = {}`. Пустой payload — это НЕ «пустой пользователь»:
                #из открытых колонок останутся только id/role, а логин, хеш пароля и
                #назначения исчезнут — и такой «пустой» пользователь мог УЕХАТЬ синком
                #поверх целого на сервере (LWW сравнивает содержимое). Значение оставляем
                #(уронить чтение ВСЕХ пользователей из-за одной строки нельзя), но кричим:
                #систематический сбой здесь = сломанный ключ шифрования, это надо видеть.
                _report_once("user_blob",
                             "[users] payload пользователя «%s» не расшифрован (%s) — строка "
                             "прочитана без данных; возможны и другие", uid, e)
                d = {}
        #Служебные поля берём из ОТКРЫТЫХ колонок (они — источник правды для синка).
        d["id"] = uid
        d["role"] = role or d.get("role", "")
        d["updated_at"] = uat
        d["deleted"] = bool(deleted)
        out.append(d)
    return out


def _users_table_write(server_users: list) -> None:
    """Полная перезапись таблицы users СЕРВЕРНЫМ списком (payload шифруем в blob)."""
    conn = DBManager.get_conn(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, role TEXT, "
                "updated_at TEXT DEFAULT '', deleted INTEGER DEFAULT 0, blob TEXT DEFAULT '')")
    cur.execute("DELETE FROM users")
    for u in server_users:
        uid = u.get("id", "")
        if not uid:
            continue
        cur.execute("INSERT OR REPLACE INTO users (id,role,updated_at,deleted,blob) "
                    "VALUES (?,?,?,?,?)",
                    (uid, u.get("role", ""), u.get("updated_at", ""),
                     1 if u.get("deleted") else 0,
                     encrypt_value(json.dumps(u, ensure_ascii=False))))
    conn.commit(); conn.close()


def _ensure_users_migrated() -> None:
    """Одноразовый перенос kv (students-список + teachers-dict) → таблицу users.
    НЕДЕСТРУКТИВНО: старые kv-ключи НЕ удаляем (бэкап на случай отката). Идемпотентно по
    состоянию БД: если в таблице уже есть строки — не мигрируем."""
    conn = DBManager.get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM users")
        has_rows = cur.fetchone()[0] > 0
    except Exception as e:
        #«no such table» — штатно (init создаст), молчим. Любая ДРУГАЯ причина
        #(залочена/битая база) тоже пропускает миграцию — это уже стоит видеть.
        has_rows = True
        if "no such table" not in str(e).lower():
            _report_once("mig_users",
                         "[mig] проверка таблицы users не удалась (%s) — миграция пропущена", e)
    conn.close()
    if has_rows:
        return
    old_students = _kv_get("students", None)
    old_teachers = _kv_get("teachers", None)
    if not old_students and not old_teachers:
        return
    server = [_student_to_server(s) for s in (old_students or [])]
    server += [_teacher_to_server(fn, d) for fn, d in (old_teachers or {}).items()]
    _users_table_write(server)


def users_for_sync() -> list:
    """Все пользователи в СЕРВЕРНОЙ форме (вкл. надгробия) — для collect_local (прямой push)."""
    return _users_table_read()


#  Высокоуровневый интерфейс к локальному хранилищу
class LocalStore:
    """
    Единая точка доступа к данным приложения (студенты, преподаватели, группы,
    конфиг). Данные лежат в локальном SQLite (kv_store); обмен с общей базой
    колледжа идёт через API-сервер (см. sync_runner).
    """

    #Студенты (в таблице users, форма API сохранена — UI не трогаем)
    #get_* отдают только ЖИВЫЕ записи (UI не видит надгробий).
    #get_*_raw отдают всё, включая надгробия — нужно синхронизации.
    def get_students(self) -> list:
        return [_server_to_student(u) for u in _users_table_read()
                if u.get("role") == "student" and not u.get("deleted")]

    def get_students_raw(self) -> list:
        return [_server_to_student(u) for u in _users_table_read()
                if u.get("role") == "student"]

    def set_students(self, students: list, stamp: bool = True, wake: bool = True) -> bool:
        all_users = _users_table_read()
        others = [u for u in all_users if u.get("role") != "student"]   #преподы не трогаем
        old_students = [u for u in all_users if u.get("role") == "student"]
        #Внутри работаем в СЕРВЕРНОЙ форме (её же кладём в blob). Пароль→хеш через _hash_record.
        new_students = [_student_to_server(self._hash_record(s)) for s in students]
        #stamp=True — правка/удаление в UI: исчезнувшие → надгробия, изменённые — штамп
        #(та же оттестированная логика _merge_list_tombstones, ключ — стабильный id).
        if stamp:
            new_students = _merge_list_tombstones(new_students, old_students,
                                                  lambda r: r.get("id", ""))
        _users_table_write(others + new_students)
        return self._after_users_write(wake)

    #Преподаватели (в таблице users, dict по ФИО сохранён — UI не трогаем)
    def get_teachers(self) -> dict:
        return {u["full_name"]: _server_to_teacher(u) for u in _users_table_read()
                if u.get("role") == "teacher" and not u.get("deleted") and u.get("full_name")}

    def get_teachers_raw(self) -> dict:
        return {u["full_name"]: _server_to_teacher(u) for u in _users_table_read()
                if u.get("role") == "teacher" and u.get("full_name")}

    def set_teachers(self, teachers: dict, stamp: bool = True, wake: bool = True) -> bool:
        all_users = _users_table_read()
        others = [u for u in all_users if u.get("role") != "teacher"]   #студентов не трогаем
        old_teachers = [u for u in all_users if u.get("role") == "teacher"]
        new_teachers = [_teacher_to_server(name, self._hash_record(data))
                        for name, data in teachers.items()]
        if stamp:
            new_teachers = _merge_list_tombstones(new_teachers, old_teachers,
                                                  lambda r: r.get("id", ""))
        _users_table_write(others + new_teachers)
        return self._after_users_write(wake)

    @staticmethod
    def _after_users_write(wake: bool) -> bool:
        #Разбудить синк (как делал _kv_set при wake=True) — UI-правка уедет сразу.
        if wake:
            _wake_sync()
        return True

    #Назначения препод↔предмет↔группа (§ролей, 3.3.1) — читает синкнутую subject_hours
    #(sync/sync_engine.py::_merge_subject_hours уже пишет teacher_id в неё). Единственный
    #источник правды «какие группы видит преподаватель» — зеркало серверного
    #webdata.teacher_assignments, ЗАМЕНЯЕТ мёртвый group_assignments (никогда не
    #заполнялся, форма «предмет→ОДНА группа» и не позволила бы двух групп на предмет).
    def get_teacher_assignments(self, teacher_id: str, year: str = "", semester=None) -> list:
        if not teacher_id:
            return []
        if not year or semester is None:
            from data import terms
            year, semester = terms.current_term()
        conn = DBManager.get_conn(); cur = conn.cursor()
        try:
            cur.execute(
                "SELECT DISTINCT group_name, subject FROM subject_hours "
                "WHERE teacher_id=? AND year=? AND semester=? AND COALESCE(deleted,0)=0 "
                "AND group_name!='' AND subject!=''",
                (teacher_id, year, int(semester or 0)))
            rows = cur.fetchall()
        except Exception as e:
            #Пустой список читается как «у преподавателя нет групп» — сбой чтения
            #неотличим от реальной пустоты. Значение оставляем, но след обязателен.
            _report_once("teacher_assign",
                         "[assign] назначения преподавателя не прочитаны (%s) — "
                         "список групп будет пуст", e)
            rows = []
        finally:
            conn.close()
        return sorted({(r[0], r[1]) for r in rows})

    def get_subject_teacher_id(self, group: str, subject: str, year: str = "", semester=None) -> str:
        """Обратная сторона get_teacher_assignments: по (группа,предмет) — id назначенного
        препода, '' если не назначен. Используется редактором расписания для автозаполнения
        графы «Преподаватель» (§ролей)."""
        if not group or not subject:
            return ""
        if not year or semester is None:
            from data import terms
            year, semester = terms.current_term()
        conn = DBManager.get_conn(); cur = conn.cursor()
        try:
            cur.execute(
                "SELECT teacher_id FROM subject_hours WHERE group_name=? AND subject=? "
                "AND year=? AND semester=? AND COALESCE(deleted,0)=0 AND COALESCE(teacher_id,'')!=''",
                (group, subject, year, int(semester or 0)))
            row = cur.fetchone()
        except Exception as e:
            #Сбой чтения → графа «Преподаватель» в редакторе расписания молча пустеет,
            #как если бы никто не был назначен. Значение оставляем, но след обязателен.
            _report_once("subject_teacher",
                         "[assign] преподаватель предмета не прочитан (%s) — "
                         "графа автозаполнения останется пустой", e)
            row = None
        finally:
            conn.close()
        return row[0] if row else ""

    def get_group_zet(self, group: str, year: str = "", semester=None) -> dict:
        """{предмет: ЗЕТ} для группы за термин (docs/done/PLAN-ZET.md) — только предметы, где
        администратор явно задал значение (NULL пропускается). Порог перевода
        (ZetThreshold) сюда НЕ приезжает синком — серверная политика, не офлайн-данные,
        см. server/app/models.py::ZetThreshold."""
        if not group:
            return {}
        if not year or semester is None:
            from data import terms
            year, semester = terms.current_term()
        conn = DBManager.get_conn(); cur = conn.cursor()
        try:
            cur.execute(
                "SELECT subject, zet FROM subject_hours WHERE group_name=? AND year=? "
                "AND semester=? AND COALESCE(deleted,0)=0 AND zet IS NOT NULL",
                (group, year, int(semester or 0)))
            return {row[0]: row[1] for row in cur.fetchall()}
        except Exception as e:      # noqa: BLE001
            #Пустой словарь читается интерфейсом как «ЗЕТ по предметам не заданы» — то
            #есть сбой чтения неотличим от незаполненного администратором плана, и
            #баланс ЗЕТ студента молча считается от нуля. Значение по умолчанию
            #оставляем (уронить журнал из-за одной таблицы нельзя), но след обязателен.
            log.get("data_store").error(
                "[zet] часы/ЗЕТ группы «%s» не прочитались (%s) — баланс будет "
                "посчитан без них", group, e)
            return {}
        finally:
            conn.close()

    #Группы (в таблице groups; форма API сохранена — UI не трогаем)
    def get_groups(self) -> list:
        return [g for g in _groups_read_raw() if not g.get("deleted")]

    def get_groups_raw(self) -> list:
        return _groups_read_raw()

    def set_groups(self, groups: list, stamp: bool = True, wake: bool = True) -> bool:
        #stamp=True — правка в UI: штампуем изменённые, исчезнувшие → надгробия (та же
        #оттестированная логика _merge_list_tombstones, что была для kv-списка).
        if stamp:
            groups = _merge_list_tombstones(groups, _groups_read_raw(),
                                            lambda r: r.get("name", ""))
        _groups_write_raw(groups)
        if wake:   #разбудить синк (как делал _kv_set при wake=True) — UI-правка уедет сразу
            _wake_sync()
        return True

    #Конфиг приложения
    def _config(self) -> dict:
        return _kv_get("config", {})

    #Пароль администратора (хранится только хеш)
    def get_admin_login(self) -> str:
        return self._config().get("admin_login", DEFAULT_ADMIN_LOGIN)

    def has_admin_password(self) -> bool:
        """True, если задан ВАЛИДНЫЙ пароль администратора (хеш есть и это не старый
        дефолт). На хост-ПК при первом запуске — False → нужен first-run диалог.
        На остальных ПК конфиг приезжает синхронизацией с сервера уже с хешем → True.
        Старый дефолтный пароль считаем «не заданным», чтобы заставить сменить его."""
        h = self._config().get("admin_password_hash")
        if not h or _is_legacy_default(h):
            return False
        return True

    def set_admin_password(self, pw: str) -> bool:
        #Не даём установить старый скомпрометированный дефолт — иначе бэкдор вернётся.
        if pw == _LEGACY_DEFAULT_ADMIN_PASSWORD:
            return False
        cfg = self._config()
        cfg["admin_password_hash"] = hash_password(pw)
        #Метку ставим ЗДЕСЬ, при реальной смене пароля. Без неё admin_user_from_config
        #подставлял _now() на каждом сборе, и запись администратора попадала в дельту
        #КАЖДЫЙ push — даже когда ничего не менялось («грязная» дельта).
        cfg["admin_updated_at"] = _now_iso()
        return _kv_set("config", cfg)

    def setup_admin_password(self, pw: str) -> bool:
        """Первичная установка пароля администратора (только если он ещё не задан).
        Защищает от перезаписи уже настроенного пароля на клиентских ПК."""
        if self.has_admin_password():
            return False
        return self.set_admin_password(pw)

    def check_admin_password(self, pw: str) -> bool:
        h = self._config().get("admin_password_hash")
        if not h or _is_legacy_default(h):
            return False  #пароль не задан или это старый дефолт — вход запрещён
        return verify_password(pw, h)

    #Аутентификация (логин + пароль)
    def authenticate(self, login: str, password: str) -> dict | None:
        """
        Единая точка входа. Возвращает:
          {"role": "admin"}
          {"role": "teacher", "name": <ФИО>, "data": {...}}
          {"role": "student", "stud": {"f":..,"n":..,"g":..}}
        или None, если логин/пароль неверны.
        Бросает AccountLocked, если логин временно заблокирован за перебор.

        Здесь же — журнал аудита и анти-брутфорс: фиксируем каждый вход и блокируем
        логин после серии неверных попыток (152-ФЗ / приказ ФСТЭК №21).
        """
        from data.audit import (is_locked, register_failure, register_success,
                            log_event)

        login = (login or "").strip()
        if not login:
            return None

        locked, left = is_locked(login)
        if locked:
            log_event("login_locked", login, f"осталось {left}s")
            raise AccountLocked(left)

        result = self._authenticate_inner(login, password)

        if result is None:
            register_failure(login)
            log_event("login_failed", login)
        else:
            register_success(login)
            log_event("login_success", login, result.get("role", ""))
            #Запускается фоновая синхронизация с сервером (если задан адрес API).
            #Офлайн / без сервера — внутри просто ничего не делает.
            try:
                from sync.sync_runner import start as _sync_start
                _sync_start(login, password, result.get("role", ""))
            except Exception as e:
                log.get("data_store").warning(f"[sync] не удалось запустить: {e}")
        return result

    def authenticate_trusted(self, login: str, password: str) -> dict | None:
        """Собрать сессию для пользователя, ПАРОЛЬ КОТОРОГО УЖЕ ПРОВЕРЕН СЕРВЕРОМ.

        Зачем: на десктопе (Windows, без OpenSSL GOST-провайдера) локальная проверка
        серверных гибридных хешей идёт медленным pure-Python Стрибогом (2000 итераций) —
        вход висел секунды. Когда есть сеть, пароль проверяет сервер (C-скорость), а здесь
        мы лишь СОБИРАЕМ payload дашборда из локальных данных — БЕЗ повторного хеша.
        Аудит успеха и фоновый синк — как в authenticate. None, если пользователя ещё нет
        в локальной базе (первый вход на чистом ПК — сперва нужен pull)."""
        login = (login or "").strip()
        sess = self.lookup_session(login)
        if sess is None:
            return None
        role, payload = sess
        if role == "admin":
            res = {"role": "admin"}
        elif role == "teacher":
            res = {"role": "teacher", "name": payload[0], "data": payload[1]}
        elif role == "parent":
            res = {"role": "parent", "parent": payload}
        else:
            res = {"role": "student", "stud": payload}
        from data.audit import register_success, log_event
        register_success(login)
        log_event("login_success", login, role)
        try:
            from sync.sync_runner import start as _sync_start
            _sync_start(login, password, role)
        except Exception as e:
            log.get("data_store").warning(f"[sync] не удалось запустить: {e}")
        return res

    def _authenticate_inner(self, login: str, password: str) -> dict | None:
        """Сама проверка логина/пароля без аудита и блокировок."""
        if login == self.get_admin_login() and self.check_admin_password(password):
            return {"role": "admin"}

        for name, data in self.get_teachers().items():
            if (data.get("login") or "").strip() == login:
                return {"role": "teacher", "name": name, "data": data} \
                    if self._verify(data, password) else None

        for s in self.get_students():
            if (s.get("login") or "").strip() == login:
                if self._verify(s, password):
                    return {"role": "student", "stud": {
                        "f": s.get("surname", ""),
                        "n": s.get("name", ""),
                        "g": s.get("group", ""),
                    }}
                return None

        #Родитель. Его кабинет на десктопе — веб-представление (онлайн-раздел), но САМ
        #ВХОД должен работать так же, как у остальных ролей: строка пользователя приезжает
        #обычным синком в таблицу users, и хеш пароля тут тот же гибридный.
        for p in self.get_parents():
            if (p.get("login") or "").strip() == login:
                return {"role": "parent", "parent": p} if self._verify(p, password) else None
        return None

    def lookup_session(self, login: str):
        """Находит пользователя ПО ЛОГИНУ без проверки пароля и возвращает (role, payload)
        в том же виде, что ждут экраны/сигналы входа:
          ("admin", None) | ("teacher", (ФИО, data)) | ("student", stud) | None.

        Нужна для ПЕРСИСТЕНТНОГО входа: при старте мы доверяем сохранённой сессии
        (вход уже был выполнен ранее, а доступ к серверу держит сохранённый токен),
        поэтому пароль повторно не спрашиваем — только пересобираем payload дашборда
        из СВЕЖИХ локальных данных (после реконсиляции с сервером)."""
        login = (login or "").strip()
        if not login:
            return None
        if login == self.get_admin_login():
            return ("admin", None)
        for name, data in self.get_teachers().items():
            if (data.get("login") or "").strip() == login:
                return ("teacher", (name, data))
        for s in self.get_students():
            if (s.get("login") or "").strip() == login:
                return ("student", {"f": s.get("surname", ""), "n": s.get("name", ""),
                                    "g": s.get("group", "")})
        for p in self.get_parents():
            if (p.get("login") or "").strip() == login:
                return ("parent", p)
        return None

    def get_parents(self) -> list:
        """Родители из таблицы users (без надгробий).

        Отдаём «серверную» форму как есть: десктопу от родителя нужны только логин, ФИО и
        хеш пароля — его кабинет целиком живёт в веб-представлении, локальных данных у
        него нет и быть не должно (журнал ребёнка — онлайн-раздел с проверкой согласия)."""
        return [dict(u) for u in _users_table_read()
                if u.get("role") == "parent" and not u.get("deleted")]

    #Внутреннее
    @staticmethod
    def _hash_record(rec: dict) -> dict:
        """Если в записи есть открытый пароль — заменяем на хеш."""
        rec = dict(rec)
        pw = rec.pop("password", None)
        if pw:
            rec["password_hash"] = hash_password(pw)
        return rec

    @staticmethod
    def _verify(rec: dict, pw: str) -> bool:
        h = rec.get("password_hash")
        if h:
            return verify_password(pw, h)
        if rec.get("password"):           #старые записи в открытом виде
            return rec["password"] == pw
        return (pw or "") == ""           #пароль не задан → вход без пароля


#Singleton
_store: LocalStore | None = None


def rekey_student_grades(old_f: str, old_n: str, new_f: str, new_n: str) -> int:
    """Обновляет ФИО в строках оценок студента. Возвращает число тронутых строк.

    ЭТАП 3 сильно упростил эту функцию (зеркало серверного _rekey_student_grades).
    Раньше оценки ключевались по ФИО, и переименование означало ПЕРЕНОС истории: под
    новым ФИО создавались копии, а старые прикапывались надгробиями.

    Так делать больше НЕЛЬЗЯ, и это не вкусовщина. У живой строки и её надгробия один и
    тот же student_id, значит при push обе дают ОДИН серверный ключ — сервер применил бы
    ту, что пришла последней, и с вероятностью 50% похоронил бы живую оценку. Поэтому
    теперь строго UPDATE на месте: одна строка, ключ не двигается, хоронить нечего.

    ФИО в таблицах остаётся денормализованной копией для показа и старых выборок,
    поэтому обновить его всё же надо — иначе оценки «пропадут» из выдачи по ФИО.
    """
    if (old_f, old_n) == (new_f, new_n) or not (old_f or old_n):
        return 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    touched = 0
    conn = DBManager.get_conn()
    cur = conn.cursor()
    try:
        #INSERT OR REPLACE не годится: в grades ФИО входит в PRIMARY KEY, и обычный
        #UPDATE упрётся в конфликт, если строка с новым ФИО уже есть (перезапуск
        #переименования). Поэтому сначала убираем возможного двойника.
        cur.execute("SELECT lesson_id FROM grades WHERE student_f=? AND student_n=?",
                    (old_f, old_n))
        for (lesson_id,) in cur.fetchall():
            cur.execute("DELETE FROM grades WHERE student_f=? AND student_n=? AND "
                        "lesson_id=?", (new_f, new_n, lesson_id))
            cur.execute("UPDATE grades SET student_f=?, student_n=?, updated_at=? "
                        "WHERE student_f=? AND student_n=? AND lesson_id=?",
                        (new_f, new_n, now, old_f, old_n, lesson_id))
            touched += 1
        #У итоговых ФИО не в ключе (ключ — id), поэтому просто правим поля.
        cur.execute("UPDATE term_grades SET student_f=?, student_n=?, updated_at=? "
                    "WHERE student_f=? AND student_n=?",
                    (new_f, new_n, now, old_f, old_n))
        touched += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
    finally:
        conn.close()
    return touched


def get_store() -> LocalStore:
    """Всегда возвращает рабочее хранилище (SQLite доступен всегда)."""
    global _store
    if _store is None:
        _store = LocalStore()
    return _store


def reset_store():
    global _store
    _store = None


def reset_synced_local_data():
    """Стирает СИНХРОНИЗИРУЕМЫЙ кэш этого ПК, сохраняя локальные настройки.

    Зачем. Слияние с сервером аддитивное (LWW-upsert по строкам): локальная запись
    убирается только по серверному надгробию. Поэтому запись, оставшаяся ТОЛЬКО локально
    (от прежнего аккаунта/сессии или после обновления), «протекала» в следующий аккаунт
    на этом ПК (баг с фантомным студентом). Реконсиляция на границе сессии: стираем кэш и
    затем полным pull приводим локаль в точное соответствие серверу (server wins).

    Стираем: пользователей/группы/занятия/оценки/итоги/конфликты (таблицы users/groups/
    lessons/grades/term_grades — через clear_synced_tables), предметы (subjects.json), старые
    kv-бэкапы students/teachers, метку дельты. НЕ трогаем:
      • ключи с префиксом `_local:` — адрес сервера, device_id, токен, сохранённая
        сессия, host-флаги, тема, offline_ack (иначе слетели бы подключение и вход);
      • `config` — хеш пароля админа и тема вуза; на полном pull сервер перезапишет свои
        ключи, а лишних «осиротевших» ключей в config практически не бывает.
    """
    #Пустые коллекции пишем без пробуждения синка (это не правка данных, а очистка
    #кэша — будить синхронизацию незачем, да и нечего отправлять).
    _kv_set("students", [], wake=False)
    _kv_set("teachers", {}, wake=False)
    #Группы теперь в таблице groups — очистит DBManager.clear_synced_tables() ниже.
    #Метку дельты сбрасываем, чтобы следующий pull был полным и наполнил кэш заново.
    set_sync_watermark("")
    #И метку push — кэш стёрт, «уже отправленного» больше нет; следующий push обязан
    #быть полным, иначе уцелевшие после реконсиляции локальные правки не уехали бы.
    set_push_watermark("")
    #Предметы отдельной очистки больше не требуют: локального `subjects.json` нет с
    #17.08.2026 (удалён вместе с `subjects.py` — список берётся с портала ВСГУТУ, а в
    #программу приезжает обычным pull в зеркальную копию `local_app.db`). Саму таблицу
    #`subjects` в зеркале чистит `clear_synced_tables()` ниже, как и остальные.
    try:
        from data.core import DBManager
        DBManager.clear_synced_tables()
    except Exception as e:
        log.get("data_store").warning(f"[reset] таблицы занятий/оценок не очищены: {e}")
    #Сбрасываем singleton — чтобы в памяти не осталось ссылок на старые данные.
    reset_store()
