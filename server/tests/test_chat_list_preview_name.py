"""
test_chat_list_preview_name.py — ПОДПИСЬ АВТОРА В СПИСКЕ ЧАТОВ.

🔥 Заведён 09.09.2026 по возражению Полковника. Список чатов переписан на пакетные
выборки, и вместе с этим подпись автора превью поехала считаться через `_names_for` —
общий помощник ленты, у которого правило ДРУГОЕ: `full_name or name or login or id`.
Прежнее правило списка — `full_name or name or ""`.

Разница видна ровно у одного человека: у того, кто заведён синком по одному логину и чьё
ФИО ещё не приехало. Строка списка чатов из «текст» превращалась в «s1: текст».

⚠️ Случай был НЕ ПОКРЫТ ни одним из 165 тестов мессенджера — все они заводят людей с
полным ФИО, и подмена правила прошла мимо зелёного прогона. Это ровно наш класс «зелёный
тест рядом с дефектом означает „случай не покрыт“, а не „исправно“».

⚠️ Пустая строка здесь — не отсутствие данных, а РЕШЕНИЕ: «не подписывать автора вовсе».
Логин в списке чатов подставлять нельзя — это внутренний идентификатор, человек его не
знает и в переписке никогда не видит.
"""
from conftest import make_admin, make_teacher
from app.security import hash_password


def _push_user(client, admin_headers, uid, login, **fields):
    """Завести пользователя синком — тем же путём, каким приходят люди с десктопа."""
    row = {"id": uid, "role": "student", "login": login,
           "password_hash": hash_password("studpass1"), "group_name": "К-24"}
    row.update(fields)
    r = client.post("/sync/push", json={"changes": {"users": [row]}}, headers=admin_headers)
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"login": login, "password": "studpass1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _group_with(client, owner_headers, member_ids, title="Группа"):
    r = client.post("/web/messenger/chats/group",
                    json={"title": title, "member_ids": member_ids}, headers=owner_headers)
    assert r.status_code == 200, r.text
    return r.json()["conversation_id"]


def _row(chats, conv_id):
    return next(c for c in chats if c["conversation_id"] == conv_id)


def test_preview_is_not_prefixed_with_a_login_when_the_author_has_no_name(client):
    """Автор без ФИО не подписывается ВОВСЕ — ни логином, ни идентификатором."""
    admin = make_admin(client)
    teacher = make_teacher(client, admin)
    nameless = _push_user(client, admin, "stud:nameless", "s1", full_name="", name="", surname="")

    conv = _group_with(client, teacher, ["stud:nameless"])
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "текст"}, headers=nameless).status_code == 200

    chats = client.get("/web/messenger/chats", headers=teacher).json()["chats"]
    row = _row(chats, conv)
    assert row["last_message"]["body"] == "текст"
    #🔒 Обратный ход этой проверки — вернуть `_names_for` в подпись: там `login`, и
    #ожидание сразу становится "s1".
    assert row["last_message"]["sender_name"] == "", (
        "в подпись автора попал логин или id — человек такого о себе не писал: "
        f"{row['last_message']['sender_name']!r}")


def test_author_with_a_name_is_still_signed(client):
    """Обратная половина: у человека с ФИО подпись обязана быть.

    Без неё предыдущая проверка проходила бы и на коде, который не подписывает НИКОГО, —
    то есть сторож был бы зелёным рядом с настоящей потерей имени автора.
    """
    admin = make_admin(client)
    teacher = make_teacher(client, admin)
    named = _push_user(client, admin, "stud:named", "s2",
                       full_name="Боб Бобов", name="Боб", surname="Бобов")

    conv = _group_with(client, teacher, ["stud:named"], title="Вторая")
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "привет"}, headers=named).status_code == 200

    chats = client.get("/web/messenger/chats", headers=teacher).json()["chats"]
    assert _row(chats, conv)["last_message"]["sender_name"] == "Боб Бобов"


def test_own_message_is_never_signed(client):
    """Своё сообщение автор не подписывает — клиент рисует его как «Вы»."""
    admin = make_admin(client)
    teacher = make_teacher(client, admin)
    other = _push_user(client, admin, "stud:mine", "s3",
                       full_name="Кэрол Кэрова", name="Кэрол", surname="Кэрова")
    assert other  #участник нужен, чтобы беседа была групповой, а не пустой

    conv = _group_with(client, teacher, ["stud:mine"], title="Третья")
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "моё"}, headers=teacher).status_code == 200

    chats = client.get("/web/messenger/chats", headers=teacher).json()["chats"]
    assert _row(chats, conv)["last_message"]["sender_name"] == ""
