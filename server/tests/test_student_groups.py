"""
test_student_groups.py — студенты заводят групповые чаты сами, сотрудника ЗОВУТ заявкой.

Решение Ярослава (28.08.2026): «разрешить студентам делать группы между собой. И если
добавляют препода, то препод подтверждает». До этого `_guard_can_create` пускал в
создание только преподавателей и админа.

🔒 ГЛАВНЫЙ ИНВАРИАНТ ФАЙЛА — `test_pending_teacher_is_not_a_participant_anywhere`.
Приглашение сделано ОТДЕЛЬНОЙ ТАБЛИЦЕЙ, а не флагом `pending` на участнике, ровно по той
же причине, по которой ответ Вектора в общей беседе не пишется в БД под флагом видимости:
участники выбираются из базы в двух десятках мест, и первая забытая проверка флага — это
преподаватель, получающий сообщения беседы, куда он ещё не согласился войти. Пока заявка
не принята, строки участника НЕ СУЩЕСТВУЕТ, и все существующие запросы правы сами.

⚠️ Обратный ход проверен откатом: если в `create_group` убрать ветку `_needs_invite` и
добавлять сотрудника напрямую (`_needs_invite` -> False), краснеют ВОСЕМЬ тестов
этого файла — проверено прогоном, а не на глаз.
"""
import pytest

from conftest import make_admin, make_teacher
from app.security import hash_password


def _student(client, admin, login, name="Иван Иванов", group="К-24"):
    parts = name.split(" ", 1)
    r = client.post("/sync/push", json={"changes": {"users": [{
        "id": f"stud:{login}", "role": "student", "login": login,
        "password_hash": hash_password("studpass1"), "full_name": name,
        "surname": parts[0], "name": parts[1] if len(parts) > 1 else "",
        "group_name": group,
    }]}}, headers=admin)
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"login": login, "password": "studpass1"})
    assert r.status_code == 200, r.text
    return f"stud:{login}", {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def cast(client):
    """Админ, преподаватель и два студента — минимальный состав для всех проверок."""
    admin = make_admin(client)
    teacher = make_teacher(client, admin, login="tprep")
    t_id = "teach:tprep"
    s1_id, s1 = _student(client, admin, "stud_a", "Анна Антонова")
    s2_id, s2 = _student(client, admin, "stud_b", "Борис Борисов")
    return {"admin": admin, "teacher": teacher, "teacher_id": t_id,
            "s1": s1, "s1_id": s1_id, "s2": s2, "s2_id": s2_id}


def _members(client, conv, headers):
    r = client.get(f"/web/messenger/chats/{conv}", headers=headers)
    assert r.status_code == 200, r.text
    return {p["user_id"] for p in r.json().get("participants", [])}


# ── что студенту теперь можно ───────────────────────────────────────────────────────

def test_student_creates_a_group_with_other_students(client, cast):
    r = client.post("/web/messenger/chats/group",
                    json={"title": "Курсовая", "member_ids": [cast["s2_id"]]},
                    headers=cast["s1"])
    assert r.status_code == 200, r.text
    conv = r.json()["conversation_id"]
    # Второй студент — участник СРАЗУ: подтверждения между равными не требуется, иначе
    # каждый учебный чат превратился бы в очередь согласований.
    assert cast["s2_id"] in _members(client, conv, cast["s1"])
    assert r.json()["invited"] == 0


def test_student_still_cannot_create_a_channel(client, cast):
    """Канал — вещание (один пишет, сотня читает). Исходный запрет заказчика в силе."""
    r = client.post("/web/messenger/chats/channel",
                    json={"title": "Объявления курса"}, headers=cast["s1"])
    assert r.status_code == 403, r.text


def test_muted_student_cannot_create_a_group(client, cast):
    """Мьют модерацией закрывает и новую дверь, а не только отправку сообщений."""
    #⚠️ СРОК ОБЯЗАТЕЛЕН с 11.09.2026: бессрочное ограничение сервер не принимает —
    #наказание «пока не снимут» снимать некому (см. `mod_mute_user`).
    r = client.post(f"/web/admin/messenger/users/{cast['s1_id']}/mute",
                    json={"muted": True, "hours": 1}, headers=cast["admin"])
    assert r.status_code == 200, r.text
    r = client.post("/web/messenger/chats/group",
                    json={"title": "Обход"}, headers=cast["s1"])
    assert r.status_code == 403, r.text


# ── заявка вместо молчаливого добавления ────────────────────────────────────────────

def test_teacher_added_by_a_student_gets_an_invite_not_a_seat(client, cast):
    r = client.post("/web/messenger/chats/group",
                    json={"title": "Проект", "member_ids": [cast["s2_id"], cast["teacher_id"]]},
                    headers=cast["s1"])
    assert r.status_code == 200, r.text
    conv = r.json()["conversation_id"]
    assert r.json()["invited"] == 1
    assert cast["teacher_id"] not in _members(client, conv, cast["s1"])

    r = client.get("/web/messenger/invites", headers=cast["teacher"])
    assert r.status_code == 200, r.text
    invites = r.json()["invites"]
    assert [i["conversation_id"] for i in invites] == [conv]
    assert invites[0]["title"] == "Проект"
    # Имя пригласившего в НАШЕМ ответе есть: без него заявка нечитаема («вас куда-то зовут»).
    assert "Анна" in invites[0]["invited_by_name"]


def test_pending_teacher_is_not_a_participant_anywhere(client, cast):
    """🔒 Свойство, ради которого заведена отдельная таблица.

    Проверяем не «нет флага», а наблюдаемое следствие: непринятой заявки не видно НИ В
    ОДНОЙ выборке, которой пользуется продукт, — ни в списке чатов приглашённого, ни в
    ленте беседы."""
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Проект", "member_ids": [cast["teacher_id"]]},
                       headers=cast["s1"]).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages",
                json={"body": "секретное обсуждение оценок"}, headers=cast["s1"])

    chats = client.get("/web/messenger/chats", headers=cast["teacher"]).json()["chats"]
    assert conv not in {c["conversation_id"] for c in chats}

    # Лента чужой беседы недоступна как любая другая, где человек не участник.
    r = client.get(f"/web/messenger/chats/{conv}/messages", headers=cast["teacher"])
    assert r.status_code == 403, r.text


def test_teacher_adding_students_needs_no_confirmation(client, cast):
    """Обратное направление осталось прямым: это учебная группа и полномочия преподавателя.

    Симметрия здесь была бы вредна — она превратила бы каждый рабочий чат в согласование."""
    r = client.post("/web/messenger/chats/group",
                    json={"title": "Семинар", "member_ids": [cast["s1_id"], cast["s2_id"]]},
                    headers=cast["teacher"])
    assert r.status_code == 200, r.text
    conv = r.json()["conversation_id"]
    assert r.json()["invited"] == 0
    assert {cast["s1_id"], cast["s2_id"]} <= _members(client, conv, cast["teacher"])


def test_adding_a_teacher_later_also_goes_through_an_invite(client, cast):
    """Правило живёт в ОДНОМ месте (_needs_invite), поэтому вторая дверь его не обходит."""
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Курсовая"}, headers=cast["s1"]).json()["conversation_id"]
    r = client.post(f"/web/messenger/chats/{conv}/members",
                    json={"user_ids": [cast["teacher_id"]]}, headers=cast["s1"])
    assert r.status_code == 200, r.text
    assert r.json() == {"added": 0, "invited": 1}
    assert cast["teacher_id"] not in _members(client, conv, cast["s1"])


def test_repeated_invite_does_not_pile_up(client, cast):
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Курсовая", "member_ids": [cast["teacher_id"]]},
                       headers=cast["s1"]).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/members",
                json={"user_ids": [cast["teacher_id"]]}, headers=cast["s1"])
    invites = client.get("/web/messenger/invites", headers=cast["teacher"]).json()["invites"]
    assert len(invites) == 1


# ── ответ на заявку ─────────────────────────────────────────────────────────────────

def test_accepting_makes_an_ordinary_member(client, cast):
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Проект", "member_ids": [cast["teacher_id"]]},
                       headers=cast["s1"]).json()["conversation_id"]
    r = client.post(f"/web/messenger/invites/{conv}/accept", headers=cast["teacher"])
    assert r.status_code == 200, r.text
    assert cast["teacher_id"] in _members(client, conv, cast["s1"])
    # Заявка израсходована.
    assert client.get("/web/messenger/invites", headers=cast["teacher"]).json()["invites"] == []
    # И теперь беседа у него в списке.
    chats = client.get("/web/messenger/chats", headers=cast["teacher"]).json()["chats"]
    assert conv in {c["conversation_id"] for c in chats}


def test_accepted_teacher_is_a_member_not_an_admin(client, cast):
    """⚠️ Преподаватель вошёл в ЧУЖУЮ беседу, которую завели студенты.

    Выдать ему права над ней по факту должности значило бы отдать чужую группу первому
    приглашённому: он смог бы выгонять её создателя."""
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Проект", "member_ids": [cast["teacher_id"]]},
                       headers=cast["s1"]).json()["conversation_id"]
    client.post(f"/web/messenger/invites/{conv}/accept", headers=cast["teacher"])
    r = client.delete(f"/web/messenger/chats/{conv}/members/{cast['s1_id']}",
                      headers=cast["teacher"])
    assert r.status_code == 403, r.text


def test_declining_removes_the_invite_and_tells_the_group(client, cast):
    """Тихий отказ неотличим от «ещё не посмотрел» — пригласившие ждали бы молча."""
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Проект", "member_ids": [cast["teacher_id"]]},
                       headers=cast["s1"]).json()["conversation_id"]
    r = client.post(f"/web/messenger/invites/{conv}/decline", headers=cast["teacher"])
    assert r.status_code == 200, r.text
    assert client.get("/web/messenger/invites", headers=cast["teacher"]).json()["invites"] == []
    assert cast["teacher_id"] not in _members(client, conv, cast["s1"])
    msgs = client.get(f"/web/messenger/chats/{conv}/messages",
                      headers=cast["s1"]).json()["messages"]
    assert any(m["kind"] == "system" and m["body"].startswith("invite_declined") for m in msgs)


def test_answering_a_foreign_invite_is_not_possible(client, cast):
    """Заявка адресная: чужую нельзя ни принять, ни отклонить."""
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Проект", "member_ids": [cast["teacher_id"]]},
                       headers=cast["s1"]).json()["conversation_id"]
    assert client.post(f"/web/messenger/invites/{conv}/accept",
                       headers=cast["s2"]).status_code == 404
    assert client.post(f"/web/messenger/invites/{conv}/decline",
                       headers=cast["s2"]).status_code == 404
    # И настоящая заявка при этом цела.
    assert len(client.get("/web/messenger/invites", headers=cast["teacher"]).json()["invites"]) == 1


def test_invite_notification_reaches_the_teacher(client, cast):
    """Заявка без уведомления бесполезна: раздел чатов по расписанию никто не открывает.

    ⚠️ В НАШЕМ письме имя и название есть — оно едет по TLS в нашу же базу. Наружу, в
    пуш RuStore, уходит только нейтральный текст (см. test_push_notifications.py)."""
    client.post("/web/messenger/chats/group",
                json={"title": "Курсовая", "member_ids": [cast["teacher_id"]]},
                headers=cast["s1"])
    r = client.get("/me/events", headers=cast["teacher"])
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    mine = [e for e in items if e["kind"] == "chat_invite"]
    assert mine, items
    # Название беседы и имя пригласившего — В НАШЕМ письме, не в пуше.
    assert "Курсовая" in mine[0]["body"] and "Анна" in mine[0]["body"]
