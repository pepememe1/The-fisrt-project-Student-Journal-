"""
test_canary.py — ПРИМАНКИ: пути, к которым не обращается никто законный (04.09.2026).

Идея Ярослава по мотивам «Мистер Робот»; разбор и требования — `docs/PLAN-HONEYPOT.md`.
Приманка — ДЕТЕКТОР, а не щит: она не мешает попасть внутрь, она делает попытку заметной
с первой секунды, потому что шума в ней нет по построению.

Что здесь держится (и что покраснеет, если правку откатить):
  • обращение к приманке банит источник и оставляет след;
  • 🔴 ОБЫЧНЫЙ путь бана НЕ даёт — иначе сторож ловит своих, и журнал перестаёт
    открываться колледжу;
  • 🔴 доверенный адрес не банится НИКОГДА, даже на приманке (колледж за одним NAT:
    один студент со сканером отрезал бы всё здание);
  • honeytoken не пускает внутрь ни при каких условиях и отвечает КАК обычная неудача;
  • ответ уходит мгновенно — в middleware нет ни сна, ни похода в сеть (одно ядро,
    один процесс: задержка там останавливает журнал всему колледжу).
"""
import time

from app import canary, throttle
from conftest import make_admin


def _reset():
    throttle.reset()
    #⚠️ Дедупликация записей живёт в памяти модуля и переживает тесты: без сброса
    #второй тест в файле не увидел бы своей записи и был бы зелёным по чужой причине.
    canary.reset_seen()
    #Счётчик Moving Target тоже в памяти модуля: без сброса «повторное» касание
    #в новом тесте унаследовало бы число итераций от предыдущего.
    canary.reset_mine()


# ── приманки ────────────────────────────────────────────────────────────────────────

def test_the_hit_lands_in_the_persistent_audit_log(client, monkeypatch):
    """🔥 СИГНАЛ ОБЯЗАН ПЕРЕЖИТЬ ПЕРЕЗАПУСК СЛУЖБЫ.

    Дефект, найденный 05.09.2026 сверкой плана с кодом: срабатывание писалось в
    `events.record` — кольцевой буфер на 500 записей В ПАМЯТИ ПРОЦЕССА. Фаза 5
    `docs/PLAN-HONEYPOT.md` требует `audit_events`, и требует не из аккуратности:

      • буфер очищается РЕСТАРТОМ, а рестарт — первое, что делают, когда «сервер
        странно себя ведёт», то есть ровно в момент инцидента;
      • буфер ВЫТЕСНЯЕТСЯ: туда же идут все действия и ошибки, и сканер, прошедший
        пятьсот путей, вытолкнул бы собственные первые попадания;
      • `verify_audit.py` его не видит — доказать факт сканирования было нечем.

    ⚠️ Соседние тесты этого файла были ЗЕЛЁНЫМИ рядом с этим дефектом: они проверяли
    бан и плашку, то есть поведение, а не то, доехал ли сигнал. Зелёный тест рядом с
    дефектом означает «случай не покрыт», а не «исправно».

    Обратный ход: верни в `main._canary` запись через `events.record` вместо
    `run_in_threadpool(canary.record_hit, ...)` — тест краснеет.
    """
    from app import audit
    from app.db import SessionLocal
    _reset()
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: False)

    db = SessionLocal()
    try:
        before = len([r for r in audit.recent(db, limit=200)
                      if r.get("action") == "canary.hit"])
    finally:
        db.close()

    assert client.get("/.env").status_code == 200

    db = SessionLocal()
    try:
        rows = [r for r in audit.recent(db, limit=200)
                if r.get("action") == "canary.hit"]
    finally:
        db.close()

    assert len(rows) == before + 1, (
        "срабатывание приманки не попало в персистентный журнал — значит оно исчезнет "
        "при первом же рестарте, а именно рестартом начинают разбор инцидента")

    row = rows[0]
    assert "/.env" in (row.get("target") or ""), "в записи нет пути, по которому пришли"
    assert not (row.get("actor") or ""), (
        "в записи о приманке появился актор — к приманке не обращается никто законный, "
        "и приписывать обращение человеку нельзя")


def test_the_raw_address_never_reaches_the_permanent_log(client, monkeypatch):
    """🔒 СЫРОЙ АДРЕС В ЖУРНАЛ НЕ ПИШЕТСЯ — только солёный отпечаток.

    Требование Ярослава 05.09.2026: «не делай пункт в honeypot нарушающий ПДн». Оно
    попало в точку: IP — персональные данные, когда связывается с человеком, а
    `audit_events` НЕ ЧИСТИТСЯ НИКОГДА (осознанно, см. `retention.py`). То есть сырой
    адрес лёг бы туда бессрочно — ровно то, что запрещает раздел 4
    `docs/PLAN-HONEYPOT.md`: «срок хранения записей — иначе бессрочное накопление ПДн
    без основания». Код нарушал собственный план.

    ⚠️ Детектор при этом ничего не теряет: одинаковый источник даёт одинаковый отпечаток,
    то есть планомерное сканирование по-прежнему отличимо от одиночного касания. А
    настоящий адрес остаётся в журнале доступа Caddy, у которого есть ротация и конечный
    срок — он исчезает сам, как и должен.

    Обратный ход: верни `ip=ip` в вызов `audit.log` — тест краснеет.
    """
    from app import audit, canary as canary_mod
    from app.db import SessionLocal
    _reset()
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: False)
    monkeypatch.setattr(throttle, "client_ip", lambda request: "203.0.113.77")

    assert client.get("/wp-login.php").status_code == 200

    db = SessionLocal()
    try:
        rows = [r for r in audit.recent(db, limit=200)
                if r.get("action") == "canary.hit"]
    finally:
        db.close()
    assert rows, "срабатывание не записалось вовсе"

    blob = " ".join(str(v) for r in rows for v in r.values())
    assert "203.0.113.77" not in blob, (
        "сырой IP попал в постоянный журнал: он не удаляется никогда, то есть это "
        "бессрочное хранение персональных данных")

    #Отпечаток при этом ЕСТЬ — иначе сторож был бы зелёным и при полностью потерянном
    #источнике, то есть проверял бы отсутствие данных вместо их обезличивания.
    assert canary_mod.ip_fingerprint("203.0.113.77") in blob, (
        "отпечатка источника нет — повторные обращения одного сканера станут "
        "неотличимы друг от друга, и детектор потеряет смысл")

    #Отпечаток УСТОЙЧИВ между вызовами: иначе связать два обращения было бы нечем.
    assert (canary_mod.ip_fingerprint("203.0.113.77")
            == canary_mod.ip_fingerprint("203.0.113.77"))
    #И РАЗЛИЧАЕТ источники — иначе он не отпечаток, а константа.
    assert (canary_mod.ip_fingerprint("203.0.113.77")
            != canary_mod.ip_fingerprint("198.51.100.4"))


def test_canary_path_bans_the_source(client, monkeypatch):
    """Обращение к приманке банит адрес и отдаёт плашку, а не 404."""
    _reset()
    #Клиент тестов приходит как «testclient» — он в доверенных, иначе забанил бы себя.
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: False)

    r = client.get("/.env")
    assert r.status_code == 200, r.text
    assert "Loading" in r.text and "<html" in r.text.lower()

    #Следующий запрос — уже отказ, независимо от пути.
    assert client.get("/health").status_code == 429


def test_ordinary_paths_never_ban(client, monkeypatch):
    """🔴 ОБРАТНЫЙ ХОД: обычные пути бана не дают.

    Уберёшь список и начнёшь банить по догадке — этот тест краснеет. Он и есть граница
    между «поймали сканер» и «отрезали колледж».
    """
    _reset()
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: False)

    for path in ("/health", "/robots.txt", "/offer.html", "/web/student/overview"):
        client.get(path)
        assert throttle.seconds_until_unbanned("testclient") == 0, path


def test_trusted_ip_is_never_banned(client, monkeypatch):
    """🔴 Доверенный адрес не банится даже на приманке.

    Без белого списка фичу включать нельзя вовсе: колледж за одним NAT, и один сканер
    с чьего-то ноутбука закрыл бы журнал всему зданию.
    """
    _reset()
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: True)

    assert client.get("/.env").status_code == 200
    assert client.get("/health").status_code != 429, "доверенный адрес забанен"


def test_trust_list_comes_from_the_environment(monkeypatch):
    """Список доверенных читается из окружения — правят его в аварийной ситуации."""
    _reset()
    monkeypatch.setenv("GRADEBOOK_TRUST_IPS", "10.0.0.7, 10.0.0.8")
    assert throttle.is_trusted("10.0.0.7") and throttle.is_trusted("10.0.0.8")
    assert not throttle.is_trusted("10.0.0.9")


def test_loopback_is_trusted_by_default():
    """Петля своя всегда: иначе локальный сервер программы забанил бы сам себя."""
    _reset()
    assert throttle.is_trusted("127.0.0.1") and throttle.is_trusted("::1")


def test_ban_expires(monkeypatch):
    """Бан срочный, а не вечный: вечный оборачивается разблокировкой руками."""
    _reset()
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: False)
    assert throttle.ban_ip("203.0.113.5", 1) is True
    assert throttle.seconds_until_unbanned("203.0.113.5") > 0
    time.sleep(1.1)
    assert throttle.seconds_until_unbanned("203.0.113.5") == 0


def test_directory_prefixes_are_covered(monkeypatch):
    """В деревья (`/.git/…`) лезут целиком — ловим по префиксу, а не поштучно."""
    assert canary.is_canary("/.git/config") and canary.is_canary("/.aws/credentials")
    assert canary.is_canary("/wp-content/uploads/x.php")


def test_legitimate_paths_are_not_in_the_list():
    """Сторож на состав списка: законный путь в приманках — это бан живого человека."""
    for path in ("/", "/health", "/robots.txt", "/sitemap.xml", "/offer.html",
                 "/privacy.html", "/terms.html", "/favicon.svg",
                 "/web/student/overview", "/auth/login", "/downloads/GradeBookAI.exe"):
        assert not canary.is_canary(path), path


# ── honeytoken ──────────────────────────────────────────────────────────────────────

def test_honeytoken_never_lets_anyone_in(client, monkeypatch):
    """Вход под приманкой невозможен и выглядит как обычная неудача.

    ⚠️ Ответ обязан совпадать с обычным отказом: иное сообщение объяснило бы
    атакующему, какие именно данные у него помечены.
    """
    _reset()
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: False)

    r = client.post("/auth/login", json={"login": canary.HONEY_LOGIN, "password": "x"})
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "Неверный логин или пароль"
    assert throttle.seconds_until_unbanned("testclient") > 0, "утечка не забанена"


def test_honeytoken_is_not_a_row_in_users(client):
    """🔴 Приманки НЕТ в таблице пользователей — и не должно быть.

    Строка-приманка попала бы в списки, выгрузки, средние баллы и отчёт куратора, и её
    пришлось бы не забыть исключить в двух десятках выборок. Ровно тот класс дефекта,
    из-за которого заявка в беседу сделана отдельной таблицей.
    """
    _reset()
    admin = make_admin(client)
    logins = {s["login"] for s in
              client.get("/web/admin/students", headers=admin).json()["students"]}
    logins |= {t["login"] for t in
               client.get("/web/admin/teachers", headers=admin).json()["teachers"]}
    assert canary.HONEY_LOGIN not in logins


# ── свойство: приманка не тормозит сервер ───────────────────────────────────────────

def test_decoy_answers_instantly(client, monkeypatch):
    """🔴 НИКАКОГО TARPIT. Одно ядро и один процесс: задержка в middleware
    останавливает журнал всему колледжу. «Загрузку» рисует CSS у клиента."""
    _reset()
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: False)
    started = time.monotonic()
    client.get("/.env")
    assert time.monotonic() - started < 1.0, "приманка держит соединение — это tarpit"


def test_canary_module_has_no_sleep_or_network():
    """Свойство по тексту модуля: сна и сети в приманке нет и быть не может.

    Тот же приём, что у `test_event_loop_not_blocked.py`. Докстринги вырезаем — они
    обязаны ОБЪЯСНЯТЬ запрет, и упоминание слова в объяснении не нарушение.
    """
    import ast
    import inspect
    src = inspect.getsource(canary)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            node.value.value = ""          # docstring → пусто
    body = ast.unparse(tree)
    for bad in ("sleep", "requests.", "httpx.", "urlopen"):
        assert bad not in body, f"в приманке появился {bad} — это tarpit"


def test_a_trusted_address_is_not_banned_but_IS_recorded(client, monkeypatch):
    """🔥 ДЕТЕКТОР НЕ ИМЕЕТ ПРАВА БЫТЬ СЛЕПЫМ К ИНСАЙДЕРУ (нашёл Полковник, 05.09.2026).

    Запись стояла ВНУТРИ `if throttle.ban_ip(...)`, а `ban_ip` возвращает False для
    доверенного адреса. В белом списке у нас сеть колледжа — один NAT (иначе первый же
    студент со сканером отрезал бы всё здание). Значит студент с ноутбука в аудитории
    проходил `/.env`, `/wp-login.php`, `/.git/config`, получал плашку — и журнал
    безопасности молчал. Приманка была слепа ровно к самому вероятному нарушителю.

    🔑 Разделение принципиальное: **банить и замечать — разные решения.** Бан щадит
    доверенных осознанно; запись не щадит никого, потому что она и есть смысл приманки.

    ⚠️ Это же обещано публично: `web/public/privacy.html` §9.1 говорит, что факт
    обращения фиксируется в журнале, а `docs/INCIDENT-RESPONSE.md` велит администратору
    искать там `canary.hit`. Документ, обещающий запись, которой нет, — хуже отсутствия
    документа.

    Обратный ход: верни запись внутрь `if throttle.ban_ip(...)` — тест краснеет.
    """
    from app import audit
    from app.db import SessionLocal
    _reset()
    #Адрес ДОВЕРЕННЫЙ — как компьютер внутри колледжа.
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: True)
    monkeypatch.setattr(throttle, "client_ip", lambda request: "10.0.0.5")

    assert client.get("/.git/config").status_code == 200

    #Бана нет — и это правильно: иначе один сканер отрезал бы весь колледж.
    assert throttle.seconds_until_unbanned("10.0.0.5") == 0,         "доверенный адрес забанен — так один студент положит доступ всему зданию"

    db = SessionLocal()
    try:
        rows = [r for r in audit.recent(db, limit=200)
                if r.get("action") == "canary.hit"]
    finally:
        db.close()
    assert rows, (
        "обращение к приманке с доверенного адреса не записано — детектор слеп к "
        "инсайдеру, а именно он и есть самый вероятный нарушитель")


def test_a_scanner_does_not_flood_the_security_log(client, monkeypatch):
    """Сканер шлёт сотни запросов — записей должно остаться немного.

    Строка на каждый запрос превратила бы журнал безопасности в лог доступа, где нужную
    запись уже не найти. Раньше дедупликацию давал побочный эффект (ранний 429 выше по
    потоку), то есть её не было там, где бана нет, — см. тест выше.
    """
    from app import audit
    from app.db import SessionLocal
    _reset()
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: True)   #бана нет вовсе
    monkeypatch.setattr(throttle, "client_ip", lambda request: "10.0.0.9")

    for path in ("/.env", "/wp-login.php", "/phpmyadmin", "/backup.sql", "/.git/config"):
        client.get(path)

    db = SessionLocal()
    try:
        rows = [r for r in audit.recent(db, limit=200)
                if r.get("action") == "canary.hit"]
    finally:
        db.close()
    assert len(rows) == 1, (
        "пять обращений подряд дали %d записей — журнал безопасности заполняется "
        "шумом одного сканера" % len(rows))


def test_the_live_console_still_shows_where_it_came_from(client, monkeypatch):
    """Живой мониторинг администратора обязан показывать источник.

    Нашёл Полковник: «починив» ПДн, я передал в `audit.log` пустой `ip`, а тот дублирует
    запись в живую консоль (`events.record(..., ip=ip)`). Админ видел «canary.hit /.env»
    без единого признака источника и не мог сказать, один это сканер или десять. До
    правки там был настоящий адрес — то есть починка ПДн молча отняла полезное.

    Решение: в поле `ip` кладём ОТПЕЧАТОК с префиксом `h:` — спутать с адресом нельзя,
    а отличить один источник от другого можно.
    """
    from app import events
    _reset()
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: False)
    monkeypatch.setattr(throttle, "client_ip", lambda request: "198.51.100.9")

    client.get("/.env")

    live = [e for e in events.recent(0)["events"] if e.get("kind") == "canary.hit"]
    assert live, (
        "в живой консоли администратора записи о приманке нет вовсе — а именно её он "
        "видит первой, ещё до того, как откроет журнал аудита")
    src = " ".join(str(v) for e in live for v in e.values())
    assert "h:" in src, (
        "в живой консоли нет признака источника: админ видит «canary.hit /.env» и не "
        "может сказать, один это сканер или десять")
    assert "198.51.100.9" not in src, "в живую консоль утёк сырой адрес"


# ── «мина» в заголовках (Moving Target) ─────────────────────────────────────────────

import re as _re

_MINE_RE = _re.compile(r"^hybrid_sha512_gost512\$(\d+)-(\d+)\$([0-9a-f]+)\$([0-9a-f]+)$")


def _mine_iters_of(resp):
    """Достать число SHA-итераций из заголовка-мины ответа. Заодно проверяет форму."""
    v = resp.headers.get(canary.MINE_HEADER)
    assert v, "в ответе приманки нет заголовка-мины"
    m = _MINE_RE.match(v)
    assert m, "заголовок-мина не в нашем формате hybrid_sha512_gost512$..: %r" % v
    return int(m.group(1))


def test_canary_response_carries_the_header_mine(client, monkeypatch):
    """Ответ приманки несёт «мину» в НАШЕМ формате, обычный путь — нет.

    Мина должна быть видна консольным утилитам (curl показывает заголовки), поэтому она
    едет заголовком, а не в теле. Форма имитирует `security.py::hash_password`, чтобы
    сканер принял её за настоящий хеш и скормил Hashcat.

    Обратный ход: убери из `main._canary` цикл `for _k,_v in canary.mine_headers(...)`
    — тест краснеет (заголовка нет).
    """
    _reset()
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: False)

    r = client.get("/.env")
    assert r.status_code == 200
    #Форма проверяется здесь же — падение с внятным сообщением.
    _mine_iters_of(r)

    #Обычная страница мины НЕ несёт: заголовок — признак попадания в приманку.
    _reset()
    r2 = client.get("/health")
    assert canary.MINE_HEADER not in r2.headers, "мина утекла на законный путь"


def test_the_mine_costs_us_nothing_and_leaks_nothing(monkeypatch):
    """🔴 CPU у НАС = 0, и ни одного настоящего секрета в мине.

    Смысл «Бесконечного Стрибога»: под случайные 64 байта нет прообраза, значит перебор
    бесконечен, а мы при этом не хешируем НИЧЕГО. Настоящий хеш стоил бы нам 200 000
    итераций на запрос (на одном ядре — оружие против самих себя) и дал бы атакующему
    ответ, на котором перебор кончается.

    Обратный ход: начни в `mine_headers` считать реальный хеш через `security` — сторож
    по тексту модуля краснеет; захардкодь `JWT_SECRET` в значение — краснеет проверка
    утечки.
    """
    import inspect
    src = inspect.getsource(canary)
    #Модуль приманки не имеет права считать настоящий KDF — иначе это не мина, а
    #самоограбление процессора. `security.py` эти токены содержит, `canary.py` — не смеет.
    for bad in ("pbkdf2", "hash_password", "_gost_pbkdf2", "import security",
                "from .security", "from app.security"):
        assert bad not in src, "в приманке появился настоящий расчёт хеша: %r" % bad

    #Значение мины не содержит настоящего секрета сервера.
    from app.config import JWT_SECRET
    val = canary.mine_headers("203.0.113.9")[canary.MINE_HEADER]
    assert JWT_SECRET not in val, "в мину утёк JWT_SECRET"

    #Соль и «хеш» случайны — два ответа не совпадают (иначе цель для Hashcat статична).
    a = canary.mine_headers("203.0.113.9")[canary.MINE_HEADER]
    b = canary.mine_headers("203.0.113.9")[canary.MINE_HEADER]
    assert a != b, "мина отдаёт один и тот же хеш — прогресс перебора не обнуляется"


def test_moving_target_bumps_iterations_on_repeat(client, monkeypatch):
    """🔴 MOVING TARGET: повторное касание с того же источника ПОДНИМАЕТ число итераций.

    Для Hashcat смена параметров = новая задача, накопленный прогресс сбрасывается.
    Источник ДОВЕРЕННЫЙ (is_trusted=True), чтобы бан не превратил второй запрос в 429 и
    дал повторно получить плашку с миной — мина работает и против инсайдера.

    Обратный ход: сделай в `_mine_iters` число постоянным (верни `_MINE_BASE_ITERS`) —
    тест краснеет.
    """
    _reset()
    monkeypatch.setattr(throttle, "is_trusted", lambda ip: True)   #бана нет — можно бить
    monkeypatch.setattr(throttle, "client_ip", lambda request: "10.0.0.42")

    first = _mine_iters_of(client.get("/.env"))
    second = _mine_iters_of(client.get("/wp-login.php"))
    third = _mine_iters_of(client.get("/.git/config"))
    assert second > first, "итерации не выросли на повторном касании (Moving Target мёртв)"
    assert third > second, "итерации перестали расти со второго касания"


def test_mine_iterations_have_a_ceiling():
    """Число итераций не растёт бесконечно — иначе оно перестаёт быть правдоподобным.

    Правдоподобие и есть оружие: неправдоподобно огромное число сканер отбросит как
    мусор и не станет тратить на него GPU.
    """
    canary.reset_mine()
    import time
    now = time.time()
    last = 0
    for _ in range(100):
        last = canary._mine_iters("198.51.100.200", now)
    assert last == canary._MINE_MAX_ITERS, "потолок итераций не соблюдается: %d" % last
