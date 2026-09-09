"""
test_login_bridge.py — ВХОД через локальное серверное приложение (local_api).

Это то, что позволяет убрать нативный экран входа и оставить один интерфейс на все
платформы. Логика двухступенчатая и порядок ступеней — не деталь реализации, а само
обещание продукта:

  1) СНАЧАЛА локальная копия базы → человек входит БЕЗ СЕТИ (offline-first);
  2) не вышло → боевой сервер → выдаём СВОЙ токен и запускаем синхронизацию.

Обратный порядок («сначала спросим сервер») выглядит безобиднее, но убивает
offline-first: в колледже без интернета журнал не открылся бы вовсе.

Тесты поднимают НАСТОЯЩЕЕ серверное приложение на временной базе — заглушка не заметила
бы, что маршрут перехвачен заглушкой SPA или что выданный токен не открывает `/web/*`.
"""
import json
import os
import tempfile
import urllib.error
import urllib.request

import pytest

from desktop import local_api


#Успешный вход ЗАПОМИНАЕТ сессию: запускает синхронизацию и пишет логин в настройки
#приложения. В тестах это недопустимо — настройки на машине разработчика НАСТОЯЩИЕ, и
#прогон тестов подменял бы в них сохранённый вход выдуманным «loc» (поймано ровно так:
#соседний тест начал видеть чужой логин, а на машине слетела сохранённая сессия).
#Подменяем сам «запомнить», а не его внутренности: так проверяется и то, что мост его
#действительно зовёт, и ничего настоящего не трогается.
_remembered = []
#Оригинал держим отдельно: тест ниже читает ЕГО исходник, а модульный атрибут на время
#прогона подменён заглушкой.
_ORIGINAL_REMEMBER = local_api._remember_session
_ORIGINAL_SWITCH = local_api.switch_user_db

#🔥 ВТОРАЯ СТУПЕНЬ ВХОДА ХОДИЛА НА БОЕВОЙ СЕРВЕР (найдено 05.09.2026).
#Локальная проверка неверного пароля не проходит по построению, и мост честно шёл дальше
#— к настоящему бою: `app_settings.get_api_url()` без настройки отдаёт ЗАШИТЫЙ боевой
#домен. То есть каждый полный прогон совершал на проде неудачные попытки входа, кормил
#его анти-брутфорс и писал в его журнал аудита. После нескольких прогонов подряд бой
#отвечал 429, мост отдавал 503 «нет связи» вместо 401 — и два теста краснели при
#полностью исправном коде.
#⚠️ Заглушка ставится НЕ ради «чтобы позеленело»: проверяемое здесь свойство — как мост
#ПЕРЕВОДИТ ответ второй ступени в ответ человеку, а не работает ли сегодня интернет.
#Обе ветки («сервер сказал нет» и «до сервера не дошли») теперь проверяются отдельно и
#детерминированно — раньше вторая не проверялась вовсе.
_remote_answer = [(None, "unauthorized")]
_remote_calls = []


def _fake_remote(login: str, password: str):
    _remote_calls.append((login, password))
    return _remote_answer[0]


@pytest.fixture(scope="module", autouse=True)
def _no_real_session():
    #Переключение на личную копию проверяется ОТДЕЛЬНО (ниже). Здесь его гасим: иначе
    #вход уводил бы сервер на другую базу прямо посреди прогона, и тесты проверяли бы
    #пустую копию вместо подготовленной фикстурой.
    original_switch = local_api.switch_user_db
    #Сигнатура зеркалит продукт: у `switch_user_db` появился `authenticated` —
    #заглушка без него роняла вход 500-й ошибкой, а не краснела внятно.
    local_api.switch_user_db = lambda login, authenticated=False: False
    original = local_api._remember_session
    local_api._remember_session = lambda login, password, role: _remembered.append(
        (login, role))
    original_remote = local_api._try_remote_login
    local_api._try_remote_login = _fake_remote
    yield
    local_api._remember_session = original
    local_api.switch_user_db = original_switch
    local_api._try_remote_login = original_remote


@pytest.fixture(scope="module")
def api(_no_real_session):
    tmp = os.path.join(tempfile.mkdtemp(), "login_bridge_test.db").replace("\\", "/")
    os.environ["GRADEBOOK_DB_URL"] = f"sqlite:///{tmp}"
    srv = local_api.instance()
    if not srv.start():
        pytest.skip(f"серверный пакет недоступен в этом окружении: {srv.error}")
    _add_user(login="loc", password="mypass123")
    yield srv
    srv.stop()


def _add_user(login, password, role="student"):
    from app.db import SessionLocal
    from app.models import User
    from app.security import hash_password
    db = SessionLocal()
    try:
        db.merge(User(id=f"stud:{login}", login=login, role=role,
                      surname="Тестов", name="Тест", group_name="К74/1", deleted=False,
                      password_hash=hash_password(password),
                      updated_at="2026-07-30T00:00:00+00:00"))
        db.commit()
    finally:
        db.close()


def _post(api, path, payload):
    req = urllib.request.Request(api.url(path), data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _get(api, path, token):
    req = urllib.request.Request(api.url(path), headers={
        "Authorization": f"Bearer {token}", "X-Client": "web"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_login_works_offline_from_local_copy(api):
    """Главное свойство: человек входит, когда сети нет вовсе.

    Хеш владельца сессии в локальной копии есть (чужие сервер вырезает при выдаче),
    поэтому проверка пароля целиком локальная."""
    code, body = _post(api, "/auth/login", {"login": "loc", "password": "mypass123"})
    assert code == 200, body
    assert body.get("access_token") and body.get("role") == "student"


def test_issued_token_opens_the_cabinet(api):
    """Выданный токен обязан открывать `/web/*` ЭТОГО же сервера. Иначе человек «вошёл»,
    а кабинет отвечает «требуется авторизация» — ровно та форма входа во вкладке,
    которую уже ловили в 3.4."""
    _, body = _post(api, "/auth/login", {"login": "loc", "password": "mypass123"})
    assert _get(api, "/web/student/overview", body["access_token"]) == 200


def test_wrong_password_is_refused(api):
    """Сервер сказал «нет» → человеку 401.

    Локальная ступень неверный пароль не пропускает по построению, поэтому запрос
    доходит до второй ступени — она здесь заглушена (см. `_fake_remote`), иначе тест
    ходил бы на настоящий бой."""
    _remote_answer[0] = (None, "unauthorized")
    code, _ = _post(api, "/auth/login", {"login": "loc", "password": "не тот"})
    assert code == 401


def test_error_does_not_reveal_whether_login_exists(api):
    """Ответ одинаков для «нет такого логина» и «пароль не тот»: разделение подсказало бы
    подбирающему, какие логины существуют."""
    _remote_answer[0] = (None, "unauthorized")
    _, a = _post(api, "/auth/login", {"login": "loc", "password": "не тот"})
    _, b = _post(api, "/auth/login", {"login": "нет-такого-человека", "password": "x"})
    assert a.get("detail") == b.get("detail")


def test_unreachable_server_says_so_instead_of_blaming_the_password(api):
    """🔥 Эта ветка НЕ ПРОВЕРЯЛАСЬ ВООБЩЕ, пока вторая ступень ходила на живой бой.

    «До сервера не дошли» и «сервер отклонил» — разные события, и путать их дорого:
    ответ «неверный логин или пароль» отправляет человека менять пароль вместо того,
    чтобы проверить связь. Поэтому здесь 503 и текст про связь, а не 401."""
    _remote_answer[0] = (None, "offline: ConnectError: сеть недоступна")
    code, body = _post(api, "/auth/login", {"login": "loc", "password": "не тот"})
    assert code == 503, body
    assert "сервер" in (body.get("detail") or "").lower()
    _remote_answer[0] = (None, "unauthorized")


def test_the_ladder_really_reaches_the_second_step(api):
    """Обратный ход заглушки: она обязана ВЫЗЫВАТЬСЯ.

    ⚠️ Без этой проверки заглушка второй ступени неотличима от ситуации «до неё вообще
    не доходит»: тесты выше зеленели бы и в том случае, если бы мост отвечал 401 сам, не
    спрашивая сервер, — то есть перестал бы пускать тех, кого в локальной копии ещё нет.
    Ровно наш класс «зелёный тест рядом с дефектом»."""
    _remote_answer[0] = (None, "unauthorized")
    _remote_calls.clear()
    _post(api, "/auth/login", {"login": "loc", "password": "не тот"})
    assert _remote_calls, "мост не дошёл до второй ступени — ladder сломан"


def test_empty_fields_are_rejected(api):
    assert _post(api, "/auth/login", {"login": "", "password": ""})[0] == 400
    assert _post(api, "/auth/login", {"login": "loc", "password": ""})[0] == 400


def test_bridge_route_wins_over_spa_fallback(api):
    """Наш маршрут обязан стоять ПЕРВЫМ. Приложение раздаёт SPA заглушкой «всё
    остальное → index.html»; зарегистрируйся мост позже — сервер честно отвечал бы 200 и
    отдавал HTML вместо токена. Тем же приёмом решён bootstrap-маршрут."""
    import inspect
    src = inspect.getsource(local_api.install_login_bridge)
    assert "routes.insert(0" in src, "маршрут входа должен вставать в начало списка"


def test_local_check_runs_before_network(api):
    """Порядок ступеней закреплён в коде: локальная проверка ВЫШЕ обращения к серверу.
    Перестановка не ломает тесты выше (локальный вход всё равно сработает), но убивает
    offline-first — поэтому проверяем именно порядок."""
    import inspect
    src = inspect.getsource(local_api.install_login_bridge)
    assert src.index("_try_local_login") < src.index("_try_remote_login")


def test_successful_login_starts_sync_and_remembers_who(api):
    """После входа синхронизация обязана получить логин и роль: без этого кабинет открыт,
    а данные не обновляются — со стороны выглядит как «журнал завис на вчерашнем»."""
    _remembered.clear()
    _post(api, "/auth/login", {"login": "loc", "password": "mypass123"})
    assert ("loc", "student") in _remembered


def test_failed_login_remembers_nothing(api):
    """Неудачный вход не имеет права трогать сохранённую сессию: иначе опечатка в пароле
    выбрасывала бы человека из уже открытой сессии."""
    _remembered.clear()
    _post(api, "/auth/login", {"login": "loc", "password": "не тот"})
    assert _remembered == []


def test_password_is_not_persisted_by_the_bridge(api):
    """Инвариант §7: пароль не хранится. Мост передаёт его синхронизации В ПАМЯТЬ (ей он
    нужен, чтобы переполучать 5-часовой токен), но на диск не пишет."""
    import inspect
    src = inspect.getsource(_ORIGINAL_REMEMBER)
    assert "set_saved_session(login" in src, "сохраняем логин и роль"
    assert "password" not in src.split("set_saved_session")[1], "пароль на диск не уходит"


# ── Личная копия базы: изоляция данных ──────────────────────────────────────────────
def test_login_switches_to_the_personal_copy(api, monkeypatch):
    local_api.switch_user_db = _ORIGINAL_SWITCH
    """После входа сервер обязан перейти на ЛИЧНУЮ копию базы этого человека.

    Форма входа веб-овая, значит сервер поднимается ДО того, как известно, кто войдёт —
    на «анонимной» базе. Без переключения данные вошедшего легли бы в общий файл, и
    следующий вошедший на этом компьютере прочитал бы чужие оценки. Ровно эта утечка уже
    была найдена на живой машине (44 оценки шести студентов), раздельные копии её
    закрыли — и веб-вход чуть не вернул её обратно."""
    switched = []
    monkeypatch.setattr(local_api, "_ensure_copy_openable", lambda login, enc: None)
    from app import db as real_db
    monkeypatch.setattr(real_db, "DATABASE_URL", "sqlite:///anon.db")
    monkeypatch.setattr(real_db, "rebind", lambda url, key="": switched.append(url))

    assert local_api.switch_user_db("ivanov") is True
    assert switched and "local_app_" in switched[0], switched
    #Ключ логина попал в ИМЯ файла хешем, а не открытым текстом (логин — тоже ПДн).
    assert "ivanov" not in switched[0]


def test_switch_is_skipped_when_already_on_that_copy(api, monkeypatch):
    local_api.switch_user_db = _ORIGINAL_SWITCH
    """Повторный вход тем же человеком не должен пересоздавать движок БД: лишняя
    перепривязка рвёт открытые соединения на ровном месте."""
    calls = []
    from app import db as real_db
    monkeypatch.setattr(real_db, "DATABASE_URL", local_api.local_db_url("ivanov"))
    monkeypatch.setattr(real_db, "rebind", lambda url, key="": calls.append(url))

    assert local_api.switch_user_db("ivanov") is True
    assert calls == [], "переключать нечего — база уже личная"


def test_switch_without_login_does_nothing(api):
    local_api.switch_user_db = _ORIGINAL_SWITCH
    """Пустой логин — не повод трогать привязку базы."""
    assert local_api.switch_user_db("") is False
