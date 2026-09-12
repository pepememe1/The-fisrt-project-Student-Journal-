"""
test_moderator_admin_crud.py — модератор заводится и меняет пароль ИЗ АДМИНКИ (12.09.2026).

━━ ЗАЧЕМ ━━
Раньше учётная запись модератора заводилась только консольным скриптом на боевой машине.
Требование Влада — «пароль модератора по аналогии с паролем админа», то есть управление
из продукта: администратор создаёт запись и меняет ей пароль там же, где заводит
преподавателей.

🔒 ГЛАВНАЯ ГРАНИЦА, РАДИ КОТОРОЙ ЭТОТ ФАЙЛ: дверь — `require_admin`, а НЕ
`require_moderation`. Модератор не имеет права заводить модераторов и перевыдавать
пароли: иначе роль, созданная разбирать жалобы, сама выписывает себе подкрепление и
меняет пароль тому, кто её проверяет. Проверка «роль не та» выглядит одной строкой, а её
отсутствие не видно ни на одном экране.

⚠️ Обратный ход проверен: подменить `require_admin` на `require_moderation` — краснеет
`test_moderator_cannot_manage_moderators`; убрать проверку занятого логина — краснеет
`test_another_roles_login_is_never_converted`; начать менять пароль при пустом поле —
краснеет `test_empty_password_means_do_not_change`.
"""
from app.security import hash_password, MIN_PASSWORD_LEN

from conftest import make_admin


GOOD = "moderpass1"


def _create(client, admin, login="moderator", password=GOOD, name="Модерация Дежурная"):
    return client.post("/web/admin/moderators",
                       json={"login": login, "password": password, "full_name": name},
                       headers=admin)


def _login(client, login="moderator", password=GOOD):
    return client.post("/auth/login", json={"login": login, "password": password})


def _bearer(resp):
    return {"Authorization": "Bearer " + resp.json()["access_token"]}


def test_admin_creates_a_moderator_who_can_log_in(client):
    """Главное свойство: заведённый из админки человек ВХОДИТ. Учётная запись, которая
    появилась в списке и не пускает, — худший исход: искать причину будут в пароле."""
    admin = make_admin(client)
    assert _create(client, admin).status_code == 200
    r = _login(client)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "moderator"


def test_the_id_matches_the_console_script_exactly(client):
    """id — `mod:{login}`, тот же, что у `server/create_moderator.py`. Разойдись они, и
    один человек завёлся бы ДВАЖДЫ разными строками, причём незаметно."""
    admin = make_admin(client)
    _create(client, admin)
    from app.db import SessionLocal
    from app.models import User
    db = SessionLocal()
    try:
        row = db.get(User, "mod:moderator")
        assert row is not None and row.role == "moderator"
    finally:
        db.close()


def test_a_short_password_is_refused_and_nobody_is_created(client):
    """Отказ обязан быть ПОЛНЫМ: строка, оставшаяся после неудачного заведения, выглядит
    заведённой и не работает."""
    admin = make_admin(client)
    r = _create(client, admin, password="x" * (MIN_PASSWORD_LEN - 1))
    assert r.status_code == 400, r.text
    assert client.get("/web/admin/moderators", headers=admin).json()["moderators"] == []


def test_password_is_required_at_creation(client):
    """В отличие от преподавателя, модератор без пароля не заводится: у преподавателя это
    осмысленно (нагрузку заводят заранее), а здесь получится запись, которая выглядит
    рабочей и молча не пускает."""
    admin = make_admin(client)
    r = client.post("/web/admin/moderators",
                    json={"login": "moderator", "full_name": "Без пароля"}, headers=admin)
    assert r.status_code == 400, r.text


def test_empty_password_means_do_not_change(client):
    """⚠️ ПУСТОЕ ПОЛЕ = «НЕ МЕНЯТЬ», а не «стереть». Страница пароль не показывает, поэтому
    при правке одного имени поле придёт пустым; молчаливое стирание выбило бы человека из
    продукта, и причину он бы не узнал. То же правило, что у ключа GigaChat."""
    admin = make_admin(client)
    _create(client, admin)
    r = client.put("/web/admin/moderators/moderator",
                   json={"full_name": "Новое имя"}, headers=admin)
    assert r.status_code == 200, r.text
    assert _login(client).status_code == 200, "прежний пароль перестал работать"


def test_a_new_password_replaces_the_old_one(client):
    admin = make_admin(client)
    _create(client, admin)
    r = client.put("/web/admin/moderators/moderator",
                   json={"password": "anotherpass2"}, headers=admin)
    assert r.status_code == 200, r.text
    assert _login(client, password="anotherpass2").status_code == 200
    assert _login(client).status_code == 401, "старый пароль обязан перестать работать"


def test_a_short_password_is_refused_on_update_too(client):
    """Минимум один — и на заведении, и на смене. Иначе длинный пароль меняется на «1»
    второй кнопкой, и правило не значит ничего."""
    admin = make_admin(client)
    _create(client, admin)
    r = client.put("/web/admin/moderators/moderator",
                   json={"password": "x" * (MIN_PASSWORD_LEN - 1)}, headers=admin)
    assert r.status_code == 400, r.text
    assert _login(client).status_code == 200, "неудачная смена не имеет права тронуть пароль"


def test_moderator_cannot_manage_moderators(client):
    """🔒 ГЛАВНЫЙ СТОРОЖ. Модератор не заводит модераторов и не перевыдаёт пароли."""
    admin = make_admin(client)
    _create(client, admin)
    mod = _bearer(_login(client))
    assert client.get("/web/admin/moderators", headers=mod).status_code == 403
    assert _create(client, mod, login="second").status_code == 403
    assert client.put("/web/admin/moderators/moderator",
                      json={"password": "hijacked01"}, headers=mod).status_code == 403
    assert client.delete("/web/admin/moderators/moderator", headers=mod).status_code == 403
    # И пароль от этого не изменился — отказ обязан быть ДО записи, а не после.
    assert _login(client).status_code == 200


def test_another_roles_login_is_never_converted(client):
    """🔒 Под тем же логином может жить преподаватель: смена роли отобрала бы у него журнал
    и выдала доступ к чужой переписке — одним нажатием. Та же проверка есть в консольном
    скрипте, и разойтись они не имеют права: иначе запрет обходится выбором двери."""
    admin = make_admin(client)
    r = client.post("/sync/push", json={"changes": {"users": [{
        "id": "teach:petrov", "role": "teacher", "login": "petrov",
        "password_hash": hash_password("teacherpass1"), "full_name": "Петров",
    }]}}, headers=admin)
    assert r.status_code == 200, r.text
    r = _create(client, admin, login="petrov")
    assert r.status_code == 409, r.text
    #🔥 ПРОВЕРЯЕМ ПРИЧИНУ ОТКАЗА, А НЕ ТОЛЬКО КОД. Обратный ход поймал ровно здесь: сняв
    #ролевую проверку, тест оставался ЗЕЛЁНЫМ — ниже стоит вторая проверка «логин занят»,
    #и 409 приходил от неё. Разница не косметическая: без ролевой проверки администратор
    #прочитал бы «модератор с таким логином уже есть» про ПРЕПОДАВАТЕЛЯ и пошёл бы искать
    #модератора, которого нет. Сторож, не различающий две причины одного кода, не стережёт.
    assert "teacher" in r.json()["detail"], r.text
    # Преподаватель остался преподавателем и по-прежнему входит.
    r = client.post("/auth/login", json={"login": "petrov", "password": "teacherpass1"})
    assert r.status_code == 200 and r.json()["role"] == "teacher"


def test_the_list_never_leaks_the_hash(client):
    """Наружу уходит признак «пароль задан», а не хеш: иначе страница администратора
    становится способом унести хеши на офлайн-перебор."""
    admin = make_admin(client)
    _create(client, admin)
    body = client.get("/web/admin/moderators", headers=admin).json()
    assert body["moderators"][0]["has_password"] is True
    text = str(body)
    assert "hash" not in text.lower()
    assert "$" not in text, "похоже на хеш в ответе"


def test_delete_is_soft_and_closes_the_door(client):
    """Удаление — надгробие (deleted=1), чтобы доехало до десктопа обычным pull. Но войти
    удалённый больше не может."""
    admin = make_admin(client)
    _create(client, admin)
    assert client.delete("/web/admin/moderators/moderator", headers=admin).status_code == 200
    assert client.get("/web/admin/moderators", headers=admin).json()["moderators"] == []
    assert _login(client).status_code == 401


def test_duplicate_login_is_refused(client):
    """Второй раз тот же логин — 409, а не тихая перевыдача пароля. Перевыдача есть, но она
    делается ЯВНО, через правку: иначе «создать» незаметно меняло бы пароль живому человеку."""
    admin = make_admin(client)
    assert _create(client, admin).status_code == 200
    assert _create(client, admin, password="otherpass9").status_code == 409
    assert _login(client).status_code == 200, "повторное создание тронуло чужой пароль"
