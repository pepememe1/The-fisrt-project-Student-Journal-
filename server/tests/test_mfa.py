# -*- coding: utf-8 -*-
"""Второй фактор входа: заведение, проверка, обязательность для администратора.

⚠️ Тесты гоняют НАСТОЯЩИЙ алгоритм, а не заглушку. Крипту нельзя замокать
осмысленно: подменив проверку кода, мы проверили бы собственную подмену. Коды
считаются тем же `totp`, что и в продукте, — как их считает телефон.
"""

import time

import pytest

from app import config, totp
from app.db import SessionLocal
from app.models import UserMFA
from conftest import make_admin, make_teacher


def _next_code(secret):
    """Код СЛЕДУЮЩЕГО шага времени.

    ⚠️ Нужен потому, что защита от повтора работает: код, которым только что
    подтвердили настройку, тем же кодом войти уже не даст — его шаг погашен.
    В жизни это правильно (человек подождёт полминуты), в тесте — просто берём
    следующий шаг. Он попадает в окно допуска ±1, поэтому сервер его примет.
    """
    return totp.code(secret, at=time.time() + totp.STEP_SECONDS)


def _enable_mfa(client, headers):
    """Пройти путь человека целиком: получить секрет, подтвердить кодом."""
    r = client.post("/auth/mfa/setup", headers=headers)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    r = client.post("/auth/mfa/confirm", headers=headers,
                    json={"code": totp.code(secret)})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


# ─────────────────────────────────────────────────────────────────────────────────
# Алгоритм
# ─────────────────────────────────────────────────────────────────────────────────

def test_code_from_the_phone_is_accepted_and_a_wrong_one_is_not():
    secret = totp.new_secret()
    assert totp.verify(secret, totp.code(secret)) is not None
    assert totp.verify(secret, "000000") is None
    assert totp.verify(secret, "12345") is None       # не шесть цифр
    assert totp.verify(secret, "абвгде") is None


def test_clock_drift_of_half_a_minute_is_tolerated():
    """Часы телефона и сервера расходятся ВСЕГДА.

    Без запаса половина людей не войдёт с исправным приложением, и виноватым
    окажется «сломанный вход», а не время на телефоне.
    """
    secret = totp.new_secret()
    now = 1_700_000_000
    for shift in (-totp.STEP_SECONDS, 0, totp.STEP_SECONDS):
        code = totp.code(secret, at=now + shift)
        assert totp.verify(secret, code, at=now) is not None, f"сдвиг {shift} с отвергнут"
    #А вот две минуты — уже нет: окно перебора растёт линейно с запасом.
    far = totp.code(secret, at=now + 120)
    assert totp.verify(secret, far, at=now) is None


def test_the_same_code_never_works_twice():
    """🔒 Подсмотренный через плечо код живёт 30 секунд. Второй раз — нельзя."""
    secret = totp.new_secret()
    code = totp.code(secret)
    step = totp.verify(secret, code)
    assert step is not None
    assert totp.verify(secret, code, after_step=step) is None


def test_recovery_codes_are_stored_only_as_hashes():
    codes = totp.new_recovery_codes(3)
    stored = [totp.hash_recovery(c) for c in codes]
    for raw, h in zip(codes, stored, strict=True):
        assert raw not in h, "код восстановления виден в хеше — это второй пароль открытым текстом"
        assert totp.check_recovery(raw, h)
    assert not totp.check_recovery(codes[0], stored[1])


# ─────────────────────────────────────────────────────────────────────────────────
# Заведение
# ─────────────────────────────────────────────────────────────────────────────────

def test_factor_does_not_work_until_confirmed(client):
    """Начал настройку и закрыл вкладку — вход обязан остаться прежним.

    Иначе человек запер бы себя навсегда: секрет в базе есть, в телефоне нет.
    """
    headers = make_admin(client)
    client.post("/auth/mfa/setup", headers=headers)
    assert client.get("/auth/mfa/status", headers=headers).json()["enabled"] is False

    r = client.post("/auth/login", json={"login": "admin", "password": "adminpass1"})
    assert r.status_code == 200
    assert "access_token" in r.json(), "неподтверждённый фактор не должен мешать входу"


def test_recovery_codes_are_shown_once_and_never_again(client):
    headers = make_admin(client)
    _secret, codes = _enable_mfa(client, headers)
    assert len(codes) == totp.RECOVERY_COUNT

    #Второго способа их увидеть нет по построению — в базе только хеши.
    with SessionLocal() as db:
        row = db.query(UserMFA).first()
        blob = " ".join(row.recovery_hashes or [])
    for c in codes:
        assert c not in blob


def test_setup_cannot_be_restarted_while_the_factor_is_on(client):
    """🔒 Иначе добравшийся до открытой сессии просто перезаведёт фактор на себя."""
    headers = make_admin(client)
    _enable_mfa(client, headers)
    r = client.post("/auth/mfa/setup", headers=headers)
    assert r.status_code == 409



# ⚠️ ВХОД С ФАКТОРОМ ПРОВЕРЯЕМ НА ПРЕПОДАВАТЕЛЕ, А НЕ НА АДМИНИСТРАТОРЕ (05.09.2026).
# У роли `admin` второй фактор отключён целиком (решение Влада: аутентификатор живёт на
# одном устройстве, а админ колледжа садится за разные машины). Механизм при этом
# никуда не делся и обязан работать — просто проверять его надо на той роли, где он
# действует. Оставить эти тесты на админе значило бы либо удалить проверку рабочего
# механизма, либо «подогнать» её под новое поведение, не проверяя ничего.
TEACHER_LOGIN, TEACHER_PASS = "mfateacher", "teacherpass1"


def _teacher(client):
    """Преподаватель + его заголовки. Фактор у этой роли действует."""
    admin = make_admin(client)
    return make_teacher(client, admin, login=TEACHER_LOGIN, password=TEACHER_PASS)


# ─────────────────────────────────────────────────────────────────────────────────
# Вход
# ─────────────────────────────────────────────────────────────────────────────────

def test_login_with_the_factor_gives_no_token_at_all(client):
    """🔥 Главное свойство: пока фактор не пройден, токена НЕ СУЩЕСТВУЕТ.

    Не «токен с пометкой», которую пришлось бы проверять в двух сотнях ручек, —
    а именно отсутствие токена.
    """
    headers = _teacher(client)
    _enable_mfa(client, headers)

    r = client.post("/auth/login", json={"login": TEACHER_LOGIN, "password": TEACHER_PASS})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("mfa_required") is True
    assert body.get("challenge")
    assert "access_token" not in body
    assert "refresh_token" not in body


def test_the_challenge_is_not_a_working_access_token(client):
    """🔒 Подпись у него та же — годиться как пропуск он не должен."""
    headers = _teacher(client)
    _enable_mfa(client, headers)
    challenge = client.post("/auth/login",
                            json={"login": TEACHER_LOGIN, "password": TEACHER_PASS}).json()["challenge"]

    #⚠️ Спрашиваем НАСТОЯЩУЮ защищённую ручку. Первая версия теста стучалась в «/me»,
    #а такого маршрута нет — запрос уходил в SPA-заглушку и возвращал 200 (страницу).
    #Тест «краснел» на пустом месте и точно так же мог бы позеленеть на пустом месте.
    r = client.get("/me/prefs", headers={"Authorization": f"Bearer {challenge}"})
    assert r.status_code in (401, 403), "challenge пустили как обычный токен"


def test_correct_code_completes_the_login(client):
    headers = _teacher(client)
    secret, _codes = _enable_mfa(client, headers)
    challenge = client.post("/auth/login",
                            json={"login": TEACHER_LOGIN, "password": TEACHER_PASS}).json()["challenge"]

    r = client.post("/auth/mfa/verify", json={"challenge": challenge, "code": _next_code(secret)})
    assert r.status_code == 200, r.text
    assert r.json().get("access_token")


def test_wrong_code_does_not_complete_the_login(client):
    headers = _teacher(client)
    _enable_mfa(client, headers)
    challenge = client.post("/auth/login",
                            json={"login": TEACHER_LOGIN, "password": TEACHER_PASS}).json()["challenge"]
    r = client.post("/auth/mfa/verify", json={"challenge": challenge, "code": "000000"})
    assert r.status_code == 400


def test_a_recovery_code_works_exactly_once(client):
    """Потерянный телефон — это то, ради чего коды и заведены."""
    headers = _teacher(client)
    _secret, codes = _enable_mfa(client, headers)

    def login_challenge():
        return client.post("/auth/login",
                           json={"login": TEACHER_LOGIN, "password": TEACHER_PASS}).json()["challenge"]

    r = client.post("/auth/mfa/verify", json={"challenge": login_challenge(), "code": codes[0]})
    assert r.status_code == 200, r.text
    assert r.json().get("access_token")

    #Тот же код второй раз — нет. Иначе утёкший список работает вечно.
    r = client.post("/auth/mfa/verify", json={"challenge": login_challenge(), "code": codes[0]})
    assert r.status_code == 400


def test_turning_the_factor_off_requires_a_working_code(client):
    """Пароля мало: сессия уже открыта под паролем, проверять его нечего."""
    headers = make_admin(client)
    secret, _codes = _enable_mfa(client, headers)

    assert client.post("/auth/mfa/disable", headers=headers,
                       json={"code": "000000"}).status_code == 400
    assert client.get("/auth/mfa/status", headers=headers).json()["enabled"] is True

    r = client.post("/auth/mfa/disable", headers=headers, json={"code": _next_code(secret)})
    assert r.status_code == 200, r.text
    assert client.get("/auth/mfa/status", headers=headers).json()["enabled"] is False


# ─────────────────────────────────────────────────────────────────────────────────
# Обязательность для администратора
# ─────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def as_production(monkeypatch):
    """Притвориться боевым сервером.

    Признак «бой» выводится из настроек (`config.IS_PROD`), отдельного флага в
    продукте нет намеренно — он бы разошёлся с действительностью. Здесь подменяем
    именно его, а не заводим тестовый режим: тестовый режим проверял бы сам себя.
    """
    monkeypatch.setattr(config, "IS_PROD", True)


def test_admin_works_on_production_without_any_factor(client, as_production):
    """🔥 ТРЕБОВАНИЕ СНЯТО (05.09.2026, решение Влада).

    Здесь стояло обратное: администратор без фактора получал 403 и не мог ничего.
    Живая жалоба — «при входе в админку она не работает, для этого нужен
    аутентификатор». Причина, по которой правило не работало в жизни: аутентификатор
    привязан к ОДНОМУ устройству, а админ колледжа садится за разные компьютеры.

    ⚠️ Цена названа честно: административный доступ снова держится на одном пароле.
    Тест закрепляет именно РЕШЕНИЕ, а не удобство: вернётся `required_for` — покраснеет.
    """
    headers = make_admin(client)
    r = client.get("/web/admin/groups", headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Gb-Reason") is None


def test_admin_with_a_configured_factor_is_not_asked_for_a_code(client, as_production):
    """🔥 Снять только обязательность БЫЛО БЫ МАЛО.

    Администратор, у которого фактор уже заведён, продолжал бы получать запрос кода на
    каждом входе — то есть остался бы заперт ровно как прежде. Поэтому у роли `admin`
    фактор не действует и после настройки.
    """
    headers = make_admin(client)
    _enable_mfa(client, headers)

    r = client.post("/auth/login", json={"login": "admin", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("mfa_required") is not True, "у админа снова спрашивают код"
    assert body.get("access_token"), "вход не выдал токен"
    assert client.get("/web/admin/groups", headers=headers).status_code == 200


def test_the_factor_is_still_available_to_anyone_who_wants_it(client, as_production):
    """Снят замок, а не дверь: завести фактор по-прежнему можно."""
    headers = make_admin(client)
    assert client.post("/auth/mfa/setup", headers=headers).status_code == 200
    assert client.get("/auth/mfa/status", headers=headers).status_code == 200


def test_the_requirement_does_not_apply_to_the_local_desktop_server(client, monkeypatch):
    """Обратный ход политики: без `IS_PROD` замка нет.

    Секрет фактора намеренно не синхронизируется на компьютеры, а журнал обязан
    открываться офлайн. Замок здесь означал бы, что администратор с отключённым
    интернетом не может работать вовсе.
    """
    #⚠️ Подменяем ЯВНО, а не полагаемся на окружение прогона. На машине разработчика
    #рядом лежит `server/.env`, и `config.IS_PROD` там ИСТИННО — то есть тест,
    #опирающийся на окружение, у одного человека проверял бы одно, у другого другое.
    monkeypatch.setattr(config, "IS_PROD", False)
    headers = make_admin(client)
    assert client.get("/web/admin/groups", headers=headers).status_code == 200


def test_the_requirement_does_not_touch_other_roles(client, as_production):
    """Преподавателю фактор не навязываем.

    Решение осознанное и его цена названа: у преподавателя доступ к своим группам,
    а не ко всему колледжу, и обязательный второй фактор для полусотни человек —
    это полсотни потерянных телефонов и очередь к администратору. Понадобится —
    правится ОДНА строка `mfa.required_for`.
    """
    admin_headers = make_admin(client)
    #Заводим преподавателя, пока у админа ещё нет фактора… но на «бою» админ уже
    #заперт, поэтому фактор ему сначала включаем.
    _enable_mfa(client, admin_headers)
    teacher_headers = make_teacher(client, admin_headers)
    r = client.get("/me/prefs", headers=teacher_headers)
    assert r.status_code == 200
    assert client.get("/auth/mfa/status", headers=teacher_headers).json()["required"] is False


def test_admin_without_the_factor_is_stopped_everywhere_not_just_in_admin_sections(
        client, as_production):
    """🔥 Проверка стоит в `get_current_user`, а не в `require_admin`, — и вот почему.

    `require_admin` закрывает административные РАЗДЕЛЫ. Но админские полномочия
    живут и внутри обычных ручек: в активностях админ распоряжается чужой
    активностью (восемь мест с `user.role == "admin"` прямо в теле), в мессенджере
    попадает в получатели обращений. Через `require_admin` эти ветки не проходят
    вовсе — то есть админ без второго фактора сохранял бы часть полномочий по
    одному паролю, а докстринг утверждал бы обратное.

    ⚠️ ТРЕБОВАНИЕ СНЯТО 05.09.2026, и тест переписан, а не удалён. Само устройство
    проверки — в `get_current_user`, а не в `require_admin` — осталось верным и важным:
    вернётся обязательность (для любой роли), и она обязана действовать ВЕЗДЕ, включая
    обычные ручки. Здесь закрепляется, что сейчас админа не останавливает ничто.
    """
    headers = make_admin(client)
    #Обычная, НЕ административная ручка.
    r = client.get("/web/messenger/chats", headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Gb-Reason") is None
    #И административная — тоже открыта.
    assert client.get("/web/admin/groups", headers=headers).status_code == 200


def test_the_allowed_list_is_exactly_enough_to_set_the_factor_up(client, as_production):
    """Замок обязан оставлять дверь — и ровно дверь, не шире.

    Настройка идёт со страницы настроек: ей нужны свой статус, ручки фактора и
    настройки профиля, чтобы страница вообще отрисовалась. Всё остальное закрыто.
    """
    headers = make_admin(client)
    for path in ("/auth/mfa/status", "/me/prefs"):
        assert client.get(path, headers=headers).status_code == 200, path
    assert client.post("/auth/mfa/setup", headers=headers).status_code == 200


def test_admin_with_a_configured_factor_can_still_reset_his_password(client):
    """🔥 ЗАМКОВ БЫЛО ТРИ, А СНЯЛИ ДВА (нашёл Полковник 06.09.2026).

    `mfa.guard_action` спрашивал таблицу НАПРЯМУЮ (`row_for` + `confirmed_at`) и решение
    «действует ли фактор» принимал МИМО `is_active`. Итог: администратор, снятый с фактора
    на входе и на длине сессии, оставался заперт ровно в той двери, куда попадает, потеряв
    доступ, — на восстановлении пароля. И это тот же самый человек из жалобы Влада, у
    которого аутентификатор остался на другом устройстве.

    ⚠️ Обратный ход: вернуть `guard_action` к прямому `row_for` — тест краснеет (401 с
    `X-Gb-Reason: mfa_required` вместо успешного сброса).
    """
    from app.models import User
    from app.routers import mfa

    admin = make_admin(client)
    #Заводим и ПОДТВЕРЖДАЕМ фактор администратору — состояние из жалобы.
    secret = client.post("/auth/mfa/setup", headers=admin).json()["secret"]
    assert client.post("/auth/mfa/confirm", json={"code": totp.code(secret)},
                       headers=admin).status_code == 200

    db = SessionLocal()
    try:
        row = db.query(User).filter(User.role == "admin").first()
        assert mfa.row_for(db, row.id).confirmed_at, "фактор обязан быть заведён"
        #Сам механизм: для админа он больше не действует НИ В ОДНОМ потребителе.
        assert mfa.is_active(db, row.id, "admin") is False
        #И `guard_action` обязан пропускать БЕЗ кода — иначе дверь заперта.
        mfa.guard_action(db, row, "", None, "тест")
    finally:
        db.close()


def test_nobody_is_told_to_set_up_a_factor_while_nobody_is_required_to(client):
    """🔎 ДВЕ ПОЛОВИНЫ ОДНОГО РЕШЕНИЯ ДЕРЖАТСЯ ВМЕСТЕ (06.09.2026).

    Жалоба Влада «при входе в новый аккаунт открыто окно оверлея настроек» разбиралась
    цепочкой: `deps.require_admin` отвечал 403 с `X-Gb-Reason: mfa_setup_required`,
    `api/client.js` звал обработчик, а `App.vue` делал `router.push('/{role}/settings')`.
    То есть новый администратор без аутентификатора попадал в настройки на ПЕРВОМ же
    запросе — и это выглядело как «настройки открылись сами».

    Обязательность снята, значит отказ больше не имеет права появляться НИ У КОГО. Но
    половины живут в разных файлах: вернёт кто-нибудь `required_for` и не вспомнит про
    клиентский обработчик — и оверлей вернётся вместе с ним, уже без жалобы и без
    объяснения. Тест связывает их: пока никого не обязывают, сервер молчит.
    """
    from app.models import User as _User
    from app.routers import mfa as _mfa

    admin = make_admin(client)
    for role in ("admin", "teacher", "student", "parent"):
        assert _mfa.required_for(_User(id="x", role=role)) is False, role

    r = client.get("/web/admin/groups", headers=admin)
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Gb-Reason") != "mfa_setup_required"
