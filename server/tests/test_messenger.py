"""
test_messenger.py — ядро мессенджера, Фаза 1 (личные чаты).

Проверяем: открытие direct-чата идемпотентно; отправка/история; серверную метку времени;
роль-скоуп (не-участник не читает/не пишет → 403); список чатов с непрочитанными; опрос
новых по ?after=; отметку прочтения; связь ответа reply_to_id.
"""
from conftest import make_admin, make_teacher, assign_teacher
from app.security import hash_password


def _make_student(client, admin_headers, login, name="Студент"):
    """Завести студента (через push админа) и вернуть (id, headers). surname/name — из
    первого/остальных слов full_name (конвенция «Фамилия Имя») — нужны отдельно для
    /web/teacher/grade, который матчит студента ИМЕННО по этим двум колонкам, не по full_name."""
    uid = f"stud:{login}"
    parts = name.split(" ", 1)
    surname, given = parts[0], (parts[1] if len(parts) > 1 else "")
    r = client.post("/sync/push", json={"changes": {"users": [{
        "id": uid, "role": "student", "login": login,
        "password_hash": hash_password("studpass1"), "full_name": name,
        "surname": surname, "name": given,
        "group_name": "К-24",
    }]}}, headers=admin_headers)
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"login": login, "password": "studpass1"})
    assert r.status_code == 200, r.text
    return uid, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _setup(client):
    """admin + преподаватель A + студенты B и C."""
    admin = make_admin(client)
    a = make_teacher(client, admin)                      # id teach:teacher1
    b_id, b = _make_student(client, admin, "bob", "Боб Бобов")
    c_id, c = _make_student(client, admin, "carol", "Кэрол Кэрова")
    return admin, ("teach:teacher1", a), (b_id, b), (c_id, c)


# ── Открытие личного чата ────────────────────────────────────────────────────────────
def test_open_direct_is_idempotent(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    r1 = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a)
    assert r1.status_code == 200, r1.text
    conv = r1.json()["conversation_id"]
    assert r1.json()["peer"]["full_name"] == "Боб Бобов"
    #Повторный вызов (и с ДРУГОЙ стороны) — та же беседа, без дублей.
    r2 = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b)
    assert r2.status_code == 200 and r2.json()["conversation_id"] == conv


def test_open_direct_rejects_self_and_missing(client):
    _, (a_id, a), _, _ = _setup(client)
    assert client.post(f"/web/messenger/chats/direct/{a_id}", headers=a).status_code == 400
    assert client.post("/web/messenger/chats/direct/stud:nobody", headers=a).status_code == 404


# ── Отправка и история ───────────────────────────────────────────────────────────────
def test_send_and_history_server_timestamp(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "Привет, Боб!"}, headers=a)
    assert r.status_code == 200, r.text
    msg = r.json()
    assert msg["body"] == "Привет, Боб!" and msg["sender_id"] == a_id
    assert msg["created_at"], "сервер обязан проставить метку времени"
    #Собеседник видит сообщение в истории.
    hist = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert [m["body"] for m in hist] == ["Привет, Боб!"]


def test_empty_message_rejected(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "   "}, headers=a).status_code == 400


# ── Роль-скоуп: не-участник не имеет доступа ─────────────────────────────────────────
def test_outsider_cannot_read_or_write(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "секрет"}, headers=a)
    #C не участник этого чата.
    assert client.get(f"/web/messenger/chats/{conv}/messages", headers=c).status_code == 403
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "влезаю"}, headers=c).status_code == 403


# ── Список чатов и непрочитанные ─────────────────────────────────────────────────────
def test_chat_list_unread_and_read(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "раз"}, headers=a)
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "два"}, headers=a)

    #У Боба два непрочитанных; заголовок = имя собеседника (A).
    chats_b = client.get("/web/messenger/chats", headers=b).json()["chats"]
    assert len(chats_b) == 1
    assert chats_b[0]["unread"] == 2
    assert chats_b[0]["last_message"]["body"] == "два"

    #Отправитель свои же сообщения непрочитанными не считает.
    chats_a = client.get("/web/messenger/chats", headers=a).json()["chats"]
    assert chats_a[0]["unread"] == 0

    #Боб прочитал — непрочитанных ноль.
    client.post(f"/web/messenger/chats/{conv}/read", json={}, headers=b)
    chats_b = client.get("/web/messenger/chats", headers=b).json()["chats"]
    assert chats_b[0]["unread"] == 0


# ── Опрос новых по ?after= ───────────────────────────────────────────────────────────
def test_poll_after_returns_only_new(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    m1 = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "первое"}, headers=a).json()
    #after=0 → всё; after=m1 → только новое.
    got = client.get(f"/web/messenger/chats/{conv}/messages?after=0", headers=b).json()["messages"]
    assert [m["id"] for m in got] == [m1["id"]]
    m2 = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "второе"}, headers=a).json()
    got = client.get(f"/web/messenger/chats/{conv}/messages?after={m1['id']}", headers=b).json()["messages"]
    assert [m["body"] for m in got] == ["второе"] and got[0]["id"] == m2["id"]


# ── Каталог/поиск людей и профиль ────────────────────────────────────────────────────
def test_directory_search_role_and_safe_fields(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    #Студенты — Боб и Кэрол (себя и преподавателя в списке нет).
    r = client.get("/web/messenger/users?role=student", headers=a).json()
    ids = {u["id"] for u in r["users"]}
    assert ids == {b_id, c_id}
    #Поиск по ФИО (кириллица, регистронезависимо).
    r = client.get("/web/messenger/users?role=student&q=боб", headers=a).json()
    assert [u["id"] for u in r["users"]] == [b_id]
    #Вкладка преподавателей.
    r = client.get("/web/messenger/users?role=teacher", headers=b).json()
    assert a_id in {u["id"] for u in r["users"]}
    #Безопасные поля: НИКАКИХ логина/хэша/почты (§9).
    u = client.get("/web/messenger/users?role=student", headers=a).json()["users"][0]
    for leak in ("login", "password_hash", "email", "prefs"):
        assert leak not in u, f"каталог не должен отдавать {leak}"


def test_profile_safe_fields(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    p = client.get(f"/web/messenger/users/{b_id}/profile", headers=a).json()["profile"]
    assert p["full_name"] == "Боб Бобов" and p["group_name"] == "К-24"
    assert "password_hash" not in p and "login" not in p


def test_mine_flag(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "моё"}, headers=a)
    assert client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"][0]["mine"] is True
    assert client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"][0]["mine"] is False


# ── Ответ на сообщение ───────────────────────────────────────────────────────────────
def test_reply_links_valid_message_only(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    base = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "вопрос"}, headers=a).json()
    #Валидный ответ — связь сохраняется.
    rep = client.post(f"/web/messenger/chats/{conv}/messages",
                      json={"body": "ответ", "reply_to_id": base["id"]}, headers=b).json()
    assert rep["reply_to_id"] == base["id"]
    #Ответ на сообщение из ДРУГОЙ беседы — связь отбрасывается (reply_to = None).
    conv2 = client.post(f"/web/messenger/chats/direct/{c_id}", headers=a).json()["conversation_id"]
    rep2 = client.post(f"/web/messenger/chats/{conv2}/messages",
                       json={"body": "чужой ответ", "reply_to_id": base["id"]}, headers=a).json()
    assert rep2["reply_to_id"] is None


# ── Фаза 3: действия над сообщением ──────────────────────────────────────────────────
def _conv(client, headers, peer_id):
    return client.post(f"/web/messenger/chats/direct/{peer_id}", headers=headers).json()["conversation_id"]


def test_delete_self_hides_only_for_me(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "текст"}, headers=a).json()["id"]
    #Боб скрывает у себя — у него сообщение исчезает, у А остаётся.
    assert client.delete(f"/web/messenger/messages/{mid}?scope=self", headers=b).status_code == 200
    assert client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"] == []
    assert len(client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]) == 1


def test_delete_all_author_only_in_direct(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "секрет"}, headers=a).json()["id"]
    #Боб (не автор, обычный участник) не может удалить у всех.
    assert client.delete(f"/web/messenger/messages/{mid}?scope=all", headers=b).status_code == 403
    #Автор может — сообщение становится тумбстоуном у обоих.
    assert client.delete(f"/web/messenger/messages/{mid}?scope=all", headers=a).status_code == 200
    got = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"][0]
    assert got["deleted"] is True and got["body"] == ""


def test_pin_unpin_and_list(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "важное"}, headers=a).json()["id"]
    #В личном чате закреплять может любой участник.
    assert client.post(f"/web/messenger/messages/{mid}/pin", headers=b).json()["pinned"] is True
    pinned = client.get(f"/web/messenger/chats/{conv}/pinned", headers=a).json()["pinned"]
    assert [p["id"] for p in pinned] == [mid]
    #Открепить.
    assert client.delete(f"/web/messenger/messages/{mid}/pin", headers=a).status_code == 200
    assert client.get(f"/web/messenger/chats/{conv}/pinned", headers=a).json()["pinned"] == []


def test_forward_carries_source_snapshot(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv_ab = _conv(client, a, b_id)
    conv_ac = _conv(client, a, c_id)
    mid = client.post(f"/web/messenger/chats/{conv_ab}/messages", json={"body": "перешлю"}, headers=a).json()["id"]
    r = client.post("/web/messenger/messages/forward",
                    json={"message_ids": [mid], "to_conversation_ids": [conv_ac]}, headers=a)
    assert r.status_code == 200 and r.json()["forwarded"] == 1
    fwd = client.get(f"/web/messenger/chats/{conv_ac}/messages", headers=a).json()["messages"][-1]
    assert fwd["body"] == "перешлю" and fwd["forwarded_from"] == "Преподаватель"


def test_forward_skips_unauthorized_target(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv_ab = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv_ab}/messages", json={"body": "x"}, headers=a).json()["id"]
    #C пытается переслать в чат A-B, где он не участник → 0 переслано.
    conv_ac = _conv(client, a, c_id)
    r = client.post("/web/messenger/messages/forward",
                    json={"message_ids": [mid], "to_conversation_ids": [conv_ab]}, headers=c)
    assert r.json()["forwarded"] == 0


def test_report_creates_ticket_with_snapshot(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "плохое слово"}, headers=a).json()["id"]
    #На своё жаловаться нельзя.
    assert client.post("/web/messenger/reports",
                       json={"message_id": mid, "reason_code": "spam"}, headers=a).status_code == 400
    #Боб жалуется — тикет создаётся.
    r = client.post("/web/messenger/reports",
                    json={"message_id": mid, "reason_code": "harassment", "description": "оскорбляет"}, headers=b)
    assert r.status_code == 200 and r.json()["report_id"]
    #Снимок текста сохранён даже после удаления сообщения у всех.
    from app.db import SessionLocal
    from app.models import MessageReport
    db = SessionLocal()
    try:
        rep = db.query(MessageReport).first()
        assert rep.message_snapshot == "плохое слово" and rep.reason_code == "harassment"
        assert rep.reported_user_id == a_id and rep.status == "open"
    finally:
        db.close()


def test_edit_own_message_only(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "опечтка"}, headers=a).json()["id"]
    r = client.patch(f"/web/messenger/messages/{mid}", json={"body": "опечатка"}, headers=a)
    assert r.status_code == 200 and r.json()["body"] == "опечатка" and r.json()["edited_at"]
    #Чужое править нельзя.
    assert client.patch(f"/web/messenger/messages/{mid}", json={"body": "взлом"}, headers=b).status_code == 403


# ── Фаза 4: модерация ────────────────────────────────────────────────────────────────
def test_moderation_chat_user_and_admin_reply(client):
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.get("/web/messenger/moderation", headers=b).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "помогите"}, headers=b)
    #Админ читает беседу и отвечает — видит ФИО автора (иначе не понять, кто писал).
    msgs = client.get(f"/web/admin/messenger/conversations/{conv}/messages", headers=admin).json()["messages"]
    assert [x["body"] for x in msgs] == ["помогите"]
    assert msgs[0]["sender_name"] == "Боб Бобов"
    assert client.post(f"/web/admin/messenger/conversations/{conv}/reply",
                       json={"body": "разберёмся"}, headers=admin).status_code == 200
    #Пользователь видит ответ модерации в своём чате.
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert [x["body"] for x in msgs] == ["помогите", "разберёмся"]


def test_report_queue_and_resolve(client):
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "грубость"}, headers=a).json()["id"]
    rid = client.post("/web/messenger/reports",
                      json={"message_id": mid, "reason_code": "harassment"}, headers=b).json()["report_id"]
    q = client.get("/web/admin/messenger/reports?status=open", headers=admin).json()["reports"]
    assert any(t["id"] == rid and t["message_snapshot"] == "грубость"
               and t["reported_name"] == "Преподаватель" for t in q)
    assert client.post(f"/web/admin/messenger/reports/{rid}/resolve",
                       json={"status": "resolved", "resolution_note": "предупреждение"},
                       headers=admin).status_code == 200
    left = client.get("/web/admin/messenger/reports?status=open", headers=admin).json()["reports"]
    assert all(t["id"] != rid for t in left)


def test_moderation_requires_admin(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    assert client.get("/web/admin/messenger/reports", headers=a).status_code == 403
    assert client.get("/web/admin/messenger/reports", headers=b).status_code == 403


def test_moderation_view_writes_audit(client):
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "x"}, headers=a)
    client.get(f"/web/admin/messenger/conversations/{conv}/messages", headers=admin)
    from app.db import SessionLocal
    from app.models import AuditEvent
    db = SessionLocal()
    try:
        n = (db.query(AuditEvent)
             .filter(AuditEvent.action == "msg.moderation.view", AuditEvent.target == conv).count())
        assert n >= 1, "просмотр переписки модерацией обязан писаться в аудит"
    finally:
        db.close()


# ── §правка: закрытый тикет закрывает и просмотр переписки, полная история правок ─────
def test_mod_conversation_messages_blocked_after_report_closed(client):
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "грубость"}, headers=a).json()["id"]
    rid = client.post("/web/messenger/reports",
                      json={"message_id": mid, "reason_code": "harassment"}, headers=b).json()["report_id"]
    #Открыт — читается ЧЕРЕЗ report_id.
    r = client.get(f"/web/admin/messenger/conversations/{conv}/messages",
                   params={"report_id": rid}, headers=admin)
    assert r.status_code == 200, r.text
    client.post(f"/web/admin/messenger/reports/{rid}/resolve",
               json={"status": "resolved"}, headers=admin)
    #Закрыт — та же ссылка теперь 403, а не тихо отдаёт данные.
    r2 = client.get(f"/web/admin/messenger/conversations/{conv}/messages",
                    params={"report_id": rid}, headers=admin)
    assert r2.status_code == 403, r2.text
    #Обходной путь: та же беседа БЕЗ report_id (как открывает вкладка «Обращения») —
    #сознательно НЕ блокируется тикетом другой вкладки, это разные потоки доступа.
    r3 = client.get(f"/web/admin/messenger/conversations/{conv}/messages", headers=admin)
    assert r3.status_code == 200, r3.text


def test_mod_conversation_messages_report_id_must_match_conversation(client):
    """report_id для ЧУЖОЙ беседы не подделать — 404, не молчаливая утечка."""
    admin, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv_ab = _conv(client, a, b_id)
    conv_ac = _conv(client, a, c_id)
    mid = client.post(f"/web/messenger/chats/{conv_ab}/messages", json={"body": "x"}, headers=a).json()["id"]
    rid = client.post("/web/messenger/reports",
                      json={"message_id": mid, "reason_code": "spam"}, headers=b).json()["report_id"]
    r = client.get(f"/web/admin/messenger/conversations/{conv_ac}/messages",
                   params={"report_id": rid}, headers=admin)
    assert r.status_code == 404, r.text


def test_mod_conversation_messages_shows_deleted_body_and_edit_chain(client):
    """Модерация видит ТЕКСТ удалённого и ВСЮ цепочку правок — обычные читатели нет."""
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    edited = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "опечтка"}, headers=a).json()["id"]
    client.patch(f"/web/messenger/messages/{edited}", json={"body": "опечатка"}, headers=a)
    client.patch(f"/web/messenger/messages/{edited}", json={"body": "опечатка исправлена"}, headers=a)
    deleted = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "секрет"}, headers=a).json()["id"]
    client.delete(f"/web/messenger/messages/{deleted}", params={"scope": "all"}, headers=a)

    msgs = client.get(f"/web/admin/messenger/conversations/{conv}/messages", headers=admin).json()["messages"]
    by_id = {m["id"]: m for m in msgs}
    assert by_id[edited]["edit_versions"] == [
        {"body": "опечтка", "at": by_id[edited]["edit_versions"][0]["at"]},
        {"body": "опечатка", "at": by_id[edited]["edit_versions"][1]["at"]},
        {"body": "опечатка исправлена", "at": by_id[edited]["edit_versions"][2]["at"]},
    ]
    assert by_id[deleted]["deleted"] is True and by_id[deleted]["body"] == "секрет"
    #Обычный участник по-прежнему НЕ видит текст удалённого (инвариант не ослаблен).
    own = client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]
    assert [m for m in own if m["id"] == deleted][0]["body"] == ""


def test_expire_stale_reports_closes_after_10h_and_notifies(client):
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "x"}, headers=a).json()["id"]
    rid = client.post("/web/messenger/reports",
                      json={"message_id": mid, "reason_code": "spam"}, headers=b).json()["report_id"]
    from datetime import datetime, timedelta, timezone
    from app.db import SessionLocal
    from app.models import MessageReport
    db = SessionLocal()
    try:
        rep = db.query(MessageReport).filter(MessageReport.id == rid).first()
        rep.created_at = (datetime.now(timezone.utc) - timedelta(hours=11)).isoformat()
        db.commit()
    finally:
        db.close()
    #Любой пришедший за уведомлениями (не обязательно сам заявитель) триггерит проверку.
    client.get("/me/events?filter=all&limit=5", headers=a)
    left = client.get("/web/admin/messenger/reports?status=open", headers=admin).json()["reports"]
    assert all(t["id"] != rid for t in left)
    expired = client.get("/web/admin/messenger/reports?status=expired", headers=admin).json()["reports"]
    assert any(t["id"] == rid for t in expired)
    #Заявитель (Боб) получил системное уведомление — не тот, на кого жаловались.
    items = client.get("/me/events?filter=all&limit=100", headers=b).json()["items"]
    assert any(i["kind"] == "report_expired" for i in items)


def test_expire_stale_reports_leaves_fresh_and_in_review_alone(client):
    admin, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv1 = _conv(client, a, b_id)
    mid1 = client.post(f"/web/messenger/chats/{conv1}/messages", json={"body": "x"}, headers=a).json()["id"]
    fresh_rid = client.post("/web/messenger/reports",
                            json={"message_id": mid1, "reason_code": "spam"}, headers=b).json()["report_id"]

    conv2 = _conv(client, a, c_id)
    mid2 = client.post(f"/web/messenger/chats/{conv2}/messages", json={"body": "y"}, headers=a).json()["id"]
    old_in_review_rid = client.post("/web/messenger/reports",
                                    json={"message_id": mid2, "reason_code": "spam"}, headers=c).json()["report_id"]
    client.post(f"/web/admin/messenger/reports/{old_in_review_rid}/resolve",
               json={"status": "in_review"}, headers=admin)
    from datetime import datetime, timedelta, timezone
    from app.db import SessionLocal
    from app.models import MessageReport
    db = SessionLocal()
    try:
        rep = db.query(MessageReport).filter(MessageReport.id == old_in_review_rid).first()
        rep.created_at = (datetime.now(timezone.utc) - timedelta(hours=11)).isoformat()
        db.commit()
    finally:
        db.close()
    client.get("/me/events?filter=all&limit=5", headers=a)
    open_now = {t["id"]: t["status"] for t in
               client.get("/web/admin/messenger/reports?status=", headers=admin).json()["reports"]}
    assert open_now[fresh_rid] == "open"                # свежий — не тронут
    assert open_now[old_in_review_rid] == "in_review"    # «в работе» — не авто-закрывается


# ── Фазы 5–6: группы и каналы ────────────────────────────────────────────────────────
def test_create_group_send_and_names(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Проект", "member_ids": [b_id, c_id]}, headers=a).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "привет всем"}, headers=a)
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "ответ"}, headers=b)
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=c).json()["messages"]
    assert [m["body"] for m in msgs] == ["привет всем", "ответ"]
    assert msgs[0]["sender_name"] == "Преподаватель"        # автор виден в группе
    info = client.get(f"/web/messenger/chats/{conv}", headers=b).json()
    assert info["kind"] == "group" and info["my_role"] == "member" and info["subscribers"] == 3


def test_group_member_management_permissions(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Г", "member_ids": [b_id]}, headers=a).json()["conversation_id"]
    #Обычный участник не может добавлять.
    assert client.post(f"/web/messenger/chats/{conv}/members",
                       json={"user_ids": [c_id]}, headers=b).status_code == 403
    #Владелец добавляет и назначает b админом.
    assert client.post(f"/web/messenger/chats/{conv}/members",
                       json={"user_ids": [c_id]}, headers=a).json()["added"] == 1
    assert client.post(f"/web/messenger/chats/{conv}/members/{b_id}/role",
                       json={"role": "admin"}, headers=a).status_code == 200
    #Теперь b (админ) может убрать c.
    assert client.delete(f"/web/messenger/chats/{conv}/members/{c_id}", headers=b).status_code == 200
    assert client.get(f"/web/messenger/chats/{conv}/messages", headers=c).status_code == 403
    #b покидает группу сам.
    assert client.post(f"/web/messenger/chats/{conv}/leave", headers=b).status_code == 200


def test_channel_reader_cannot_post_writer_can(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/channel",
                       json={"title": "Новости", "is_public": True, "writer_ids": [b_id]},
                       headers=a).json()["conversation_id"]
    assert client.post(f"/web/messenger/chats/{conv}/join", headers=c).status_code == 200
    #Читатель не пишет, писатель и владелец — пишут.
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "я читатель"}, headers=c).status_code == 403
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "пост"}, headers=b).status_code == 200
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "от owner"}, headers=a).status_code == 200
    #Канал появился в списке чатов читателя.
    chats = client.get("/web/messenger/chats", headers=c).json()["chats"]
    assert any(x["conversation_id"] == conv for x in chats)


def test_public_channel_catalog_and_join(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post("/web/messenger/chats/channel",
                       json={"title": "Объявления", "is_public": True}, headers=a).json()["conversation_id"]
    cat = client.get("/web/messenger/channels", headers=b).json()["channels"]
    row = [c for c in cat if c["conversation_id"] == conv][0]
    assert row["title"] == "Объявления" and row["joined"] is False
    client.post(f"/web/messenger/chats/{conv}/join", headers=b)
    cat = client.get("/web/messenger/channels", headers=b).json()["channels"]
    assert [c for c in cat if c["conversation_id"] == conv][0]["joined"] is True


# ── Фаза 7: presence + WebSocket ─────────────────────────────────────────────────────
def test_presence_online_flag(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    #Боб делает авторизованный запрос → отмечается онлайн (get_current_user → events.touch).
    client.get("/web/messenger/chats", headers=b)
    users = client.get("/web/messenger/users?role=student", headers=a).json()["users"]
    bob = [u for u in users if u["id"] == b_id][0]
    assert bob["online"] is True


def test_ws_query_token_is_no_longer_accepted(client):
    """Обратный ход к правке безопасности: ?token= в query авторизацию БОЛЬШЕ не даёт.

    Он клал JWT в access-лог Caddy (`uri` там не редактируется), а живые клиенты ходят
    сабпротоколом. Вернут фолбэк — тест покраснеет. Валидный по подписи токен, поданный
    через query, обязан привести к отказу: сокет не открывается."""
    import pytest
    from starlette.websockets import WebSocketDisconnect
    _, (a_id, a), (b_id, b), _ = _setup(client)
    token = a["Authorization"].split(" ", 1)[1]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/web/messenger/ws?token={token}"):
            pass


def test_ws_connect_with_subprotocol(client):
    """Токен через Sec-WebSocket-Protocol (['bearer', <jwt>]) — не светит в URL/логах."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    token = a["Authorization"].split(" ", 1)[1]
    with client.websocket_connect("/web/messenger/ws",
                                  subprotocols=["bearer", token]) as wsconn:
        wsconn.send_json({"type": "typing", "conversation_id": "nope"})


def test_typing_is_not_relayed_into_a_foreign_chat(client, monkeypatch):
    """Сокет — единственное место мессенджера, где беседу называет САМ клиент.

    Пока участие здесь не проверялось, любой вошедший мог разослать «печатает…» в
    чужой чат, просто подставив его id: содержимое это не раскрывало, но позволяло
    перебором проверять существование бесед и подсовывать людям несуществующего
    собеседника. Во всех остальных эндпоинтах участие сверяет `_require_participant`.
    """
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = _conv(client, a, b_id)          # беседа преподавателя и Боба, Кэрол в ней нет

    from app.routers import messenger as mod
    delivered = []

    async def spy(ids, payload):
        delivered.append((list(ids), payload))
    monkeypatch.setattr(mod.ws_manager, "send_users", spy)

    token = c["Authorization"].split(" ", 1)[1]
    with client.websocket_connect("/web/messenger/ws", subprotocols=["bearer", token]) as wsconn:
        wsconn.send_json({"type": "typing", "conversation_id": conv})
    assert delivered == [], "посторонний не имеет права слать «печатает…» в чужую беседу"


def test_typing_still_reaches_the_other_participant(client, monkeypatch):
    """Проверка участия не должна сломать саму фичу: своим «печатает…» доезжает."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)

    from app.routers import messenger as mod
    delivered = []

    async def spy(ids, payload):
        delivered.append((list(ids), payload))
    monkeypatch.setattr(mod.ws_manager, "send_users", spy)

    token = a["Authorization"].split(" ", 1)[1]
    with client.websocket_connect("/web/messenger/ws", subprotocols=["bearer", token]) as wsconn:
        wsconn.send_json({"type": "typing", "conversation_id": conv})
    assert delivered and delivered[0][0] == [b_id], delivered


def test_ws_rejects_bad_subprotocol_token(client):
    """Мусорный токен в сабпротоколе → соединение отклоняется (не открывается)."""
    import pytest
    from starlette.websockets import WebSocketDisconnect
    _setup(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/web/messenger/ws",
                                      subprotocols=["bearer", "not-a-jwt"]):
            pass


# ── Защита от злоупотребления: анти-флуд, мьют, гейт создания, границы модерации ──────
def test_flood_limit_blocks_burst(client):
    """Всплеск отправки упирается в 429 (анти-флуд). Живой темп в порог не бьётся, автомат —
    бьётся. Первые сообщения проходят, дальше сервер просит не частить."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    codes = []
    for i in range(15):
        codes.append(client.post(f"/web/messenger/chats/{conv}/messages",
                                 json={"body": f"m{i}"}, headers=a).status_code)
    assert 200 in codes and 429 in codes, codes
    #429 приходит именно ПОСЛЕ пачки успешных, а не сразу.
    assert codes[0] == 200 and codes[-1] == 429


def test_global_mute_blocks_send_and_create(client):
    """Замьюченный модерацией не может ни писать, ни создавать беседы; снятие мьюта — снова может."""
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    #Мьютим преподавателя A глобально. ⚠️ СРОК ОБЯЗАТЕЛЕН с 11.09.2026 — бессрочный мьют
    #снимать некому, о наказанном просто перестают вспоминать (см. `mod_mute_user`).
    r = client.post(f"/web/admin/messenger/users/{a_id}/mute",
                    json={"muted": True, "hours": 3}, headers=admin)
    assert r.status_code == 200 and r.json()["muted"] is True
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "нельзя"}, headers=a).status_code == 403
    assert client.post("/web/messenger/chats/group",
                       json={"title": "Х"}, headers=a).status_code == 403
    #Снимаем мьют — запись снова доступна.
    client.post(f"/web/admin/messenger/users/{a_id}/mute", json={"muted": False}, headers=admin)
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "снова можно"}, headers=a).status_code == 200


def test_mute_requires_admin_and_not_admin_target(client):
    """Мьютить может только модерация; замьютить администратора нельзя."""
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    #Преподаватель не может мьютить (require_moderation → 403).
    assert client.post(f"/web/admin/messenger/users/{b_id}/mute",
                       json={"muted": True, "hours": 1}, headers=a).status_code == 403
    #Замьютить администратора нельзя (модераторы не глушат друг друга) → 400.
    from app.db import SessionLocal
    from app.models import User
    db = SessionLocal()
    try:
        admin_uid = db.query(User).filter(User.role == "admin").first().id
    finally:
        db.close()
    assert client.post(f"/web/admin/messenger/users/{admin_uid}/mute",
                       json={"muted": True}, headers=admin).status_code == 400


def test_muted_user_flag_visible_to_admin_only(client):
    """Флаг мьюта видит модерация (в жалобе), но каталог рядовому пользователю его не «зажигает»."""
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages",
                      json={"body": "грубо"}, headers=a).json()["id"]
    client.post("/web/messenger/reports",
                json={"message_id": mid, "reason_code": "harassment"}, headers=b)
    client.post(f"/web/admin/messenger/users/{a_id}/mute",
                json={"muted": True, "hours": 1}, headers=admin)
    rep = client.get("/web/admin/messenger/reports?status=open", headers=admin).json()["reports"][0]
    assert rep["reported"]["muted"] is True
    #В обычном каталоге муты чужого аккаунта всегда False (не палим модерационное состояние).
    cat = client.get("/web/messenger/users?role=teacher", headers=b).json()["users"]
    assert all(u["muted"] is False for u in cat)


def test_only_teachers_create_channels(client):
    """КАНАЛЫ создаёт только преподаватель/админ. Группы студенту открыты (28.08.2026).

    🔥 Раньше этот тест назывался «...groups_and_channels» и требовал 403 на ОБА вида.
    Правило изменено по решению Ярослава: «разрешить студентам делать группы между
    собой». Тест не удалён и не ослаблен до бессмысленного — сужен до половины, которая
    ОСТАЛАСЬ в силе, и она важна: канал это вещание (один пишет, сотня читает), и такой
    рупор студенту по-прежнему не даём. Именно ради этого запрет и заводился.

    Что стало можно и почему это безопасно — `test_student_groups.py`: собрать в группу
    можно только тех, кого человек и так находит в каталоге, войти в чужую по своей воле
    нельзя, а СОТРУДНИКА студент не записывает, а приглашает заявкой."""
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    assert client.post("/web/messenger/chats/channel",
                       json={"title": "Студканал"}, headers=b).status_code == 403
    #А группу — теперь может.
    assert client.post("/web/messenger/chats/group",
                       json={"title": "Студгруппа"}, headers=b).status_code == 200
    assert client.post("/web/messenger/chats/group",
                       json={"title": "Проект", "member_ids": [b_id]}, headers=a).status_code == 200
    #Студент всё ещё может открыть личный чат.
    assert client.post(f"/web/messenger/chats/direct/{c_id}", headers=b).status_code == 200


def test_mod_reply_only_into_moderation_chat(client):
    """Ответ модерации разрешён только в чат обращений (kind='moderation'), не в чужой личный."""
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    direct = _conv(client, a, b_id)
    #В приватный 1-на-1 чужих людей админ вписать сообщение НЕ может.
    assert client.post(f"/web/admin/messenger/conversations/{direct}/reply",
                       json={"body": "влезаю в личку"}, headers=admin).status_code == 403
    #А в официальный чат обращений — может.
    mod = client.get("/web/messenger/moderation", headers=b).json()["conversation_id"]
    assert client.post(f"/web/admin/messenger/conversations/{mod}/reply",
                       json={"body": "ответ поддержки"}, headers=admin).status_code == 200


def test_delete_conversation_hides_only_for_me(client):
    """Удаление переписки — ЛИЧНОЕ действие: у меня чат исчезает вместе с историей,
    у собеседника остаётся целиком."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "старое"}, headers=a)
    assert client.delete(f"/web/messenger/chats/{conv}", headers=b).status_code == 200
    #У Боба чата нет и история пуста…
    assert all(c["conversation_id"] != conv for c in
               client.get("/web/messenger/chats", headers=b).json()["chats"])
    assert client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"] == []
    #…а у собеседника всё на месте.
    assert [m["body"] for m in
            client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]] == ["старое"]


def test_deleted_conversation_returns_on_new_message(client):
    """Новое сообщение возвращает удалённый чат, но СТАРУЮ историю не воскрешает."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "до удаления"}, headers=a)
    client.delete(f"/web/messenger/chats/{conv}", headers=b)
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "после"}, headers=a)
    chats = client.get("/web/messenger/chats", headers=b).json()["chats"]
    row = [c for c in chats if c["conversation_id"] == conv]
    assert row and row[0]["last_message"]["body"] == "после"
    got = [m["body"] for m in
           client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]]
    assert got == ["после"], "старая история не должна возвращаться"


def test_clear_history_keeps_chat_in_list(client):
    """clear_only=1 — история скрыта, но сам чат остаётся в списке."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "текст"}, headers=a)
    assert client.delete(f"/web/messenger/chats/{conv}?clear_only=true",
                         headers=b).json()["cleared"] is True
    assert client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"] == []
    assert any(c["conversation_id"] == conv
               for c in client.get("/web/messenger/chats", headers=b).json()["chats"])


def test_delete_group_chat_also_leaves(client):
    """Удаление группы у себя = выход из неё: доступ к беседе пропадает."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Проект", "member_ids": [b_id]}, headers=a).json()["conversation_id"]
    assert client.delete(f"/web/messenger/chats/{conv}", headers=b).status_code == 200
    assert client.get(f"/web/messenger/chats/{conv}/messages", headers=b).status_code == 403


def test_admin_deletes_any_message_with_audit(client):
    """Модерация удаляет ЛЮБОЕ сообщение у всех (даже в чужой личке) — с записью в аудит."""
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages",
                      json={"body": "нарушение"}, headers=a).json()["id"]
    assert client.delete(f"/web/admin/messenger/messages/{mid}", headers=admin).status_code == 200
    got = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"][0]
    assert got["deleted"] is True and got["body"] == ""
    #Не-админу такой эндпоинт закрыт.
    assert client.delete(f"/web/admin/messenger/messages/{mid}", headers=b).status_code == 403
    from app.db import SessionLocal
    from app.models import AuditEvent
    db = SessionLocal()
    try:
        assert db.query(AuditEvent).filter(AuditEvent.action == "msg.moderation.delete").count() >= 1
    finally:
        db.close()


def test_public_profile_fields_are_clamped_and_shared(client):
    """«О себе» режется по лимиту на СЕРВЕРЕ (клиента можно обойти) и виден другим."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    client.post("/me/prefs", json={"bio": "x" * 900, "profile_color": "violet"}, headers=a)
    card = client.get(f"/web/messenger/users/{a_id}/profile", headers=b).json()["profile"]
    assert len(card["bio"]) == 400 and card["profile_color"] == "violet"


def test_conversation_info_lists_owner_first_with_avatars(client):
    """Инфо о беседе: владелец первым, у карточек есть поля для аватарки и контекста."""
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Г", "member_ids": [b_id, c_id]}, headers=a).json()["conversation_id"]
    info = client.get(f"/web/messenger/chats/{conv}", headers=b).json()
    assert info["owner_id"] == a_id
    people = info["participants"]
    assert people[0]["user_id"] == a_id and people[0]["role"] == "owner"
    for p in people:
        assert "avatar" in p and "user_role" in p


# ── Стиль никнейма (§5.4, «карточка профиля») ────────────────────────────────────────
def test_name_font_visible_in_peer_profile(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    client.post("/me/prefs", json={"name_font": "caveat"}, headers=a)
    card = client.get(f"/web/messenger/users/{a_id}/profile", headers=b).json()["profile"]
    assert card["name_font"] == "caveat"


def test_name_font_defaults_to_empty_string(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    card = client.get(f"/web/messenger/users/{a_id}/profile", headers=b).json()["profile"]
    assert card["name_font"] == ""


def test_name_font_visible_in_conversation_info(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    client.post("/me/prefs", json={"name_font": "ptmono"}, headers=a)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Г", "member_ids": [b_id, c_id]}, headers=a).json()["conversation_id"]
    info = client.get(f"/web/messenger/chats/{conv}", headers=b).json()
    owner = next(p for p in info["participants"] if p["user_id"] == a_id)
    assert owner["name_font"] == "ptmono"


def test_name_effect_and_color_travel_with_the_profile(client):
    """Эффект и цвет имени (3.7) обязаны доезжать до ОБОИХ мест, где клиент рисует чужое
    имя: карточка профиля и список участников беседы. Раньше здесь ездил один шрифт, и
    забытое поле выглядело бы не как ошибка, а как «человек ничего не настраивал»."""
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    client.post("/me/prefs",
                json={"name_font": "russo", "name_effect": "rainbow", "name_color": "violet"},
                headers=a)

    card = client.get(f"/web/messenger/users/{a_id}/profile", headers=b).json()["profile"]
    assert (card["name_font"], card["name_effect"], card["name_color"]) == ("russo", "rainbow", "violet")

    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Г", "member_ids": [b_id, c_id]}, headers=a).json()["conversation_id"]
    info = client.get(f"/web/messenger/chats/{conv}", headers=b).json()
    owner = next(p for p in info["participants"] if p["user_id"] == a_id)
    assert (owner["name_effect"], owner["name_color"]) == ("rainbow", "violet")


def test_mute_conversation_endpoint_and_push_suppression(client, monkeypatch):
    """Мьют беседы у себя: флаг отражается в списке чатов и глушит пуш по этой беседе."""
    import app.rustore_push as rp
    calls = []
    monkeypatch.setattr(rp, "notify_login",
                        lambda db, login, title, body, data=None: calls.append(login) or 1)
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    #Боб мьютит беседу — флаг виден в его списке чатов.
    assert client.post(f"/web/messenger/chats/{conv}/mute",
                       json={"muted": True}, headers=b).json()["muted"] is True
    row = [x for x in client.get("/web/messenger/chats", headers=b).json()["chats"]
           if x["conversation_id"] == conv][0]
    assert row["muted"] is True
    #Гасим presence Боба (мьют-запрос отметил его онлайн) и шлём — пуш не должен уйти из-за мьюта.
    from app import events
    events.reset()
    calls.clear()
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "тук-тук"}, headers=a)
    assert "bob" not in calls


# ── Фаза 8: пуш офлайн-получателю ────────────────────────────────────────────────────
def test_push_to_offline_recipient(client, monkeypatch):
    import app.rustore_push as rp
    calls = []
    monkeypatch.setattr(rp, "notify_login",
                        lambda db, login, title, body, data=None: calls.append(login) or 1)
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    #Боб офлайн (не делал авторизованных запросов) → пуш уходит.
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "привет"}, headers=a)
    assert "bob" in calls


def test_no_push_to_online_recipient(client, monkeypatch):
    import app.rustore_push as rp
    calls = []
    monkeypatch.setattr(rp, "notify_login",
                        lambda db, login, title, body, data=None: calls.append(login) or 1)
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    client.get("/web/messenger/chats", headers=b)   #Боб онлайн
    calls.clear()
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "привет"}, headers=a)
    assert "bob" not in calls


# ── §D-фичи (Discord-аддоны): идемпотентность, реакции, системные, история правок ─────
def test_d10_idempotency_client_nonce(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    n = "nonce-abc-123"
    m1 = client.post(f"/web/messenger/chats/{conv}/messages",
                     json={"body": "привет", "client_nonce": n}, headers=a).json()
    m2 = client.post(f"/web/messenger/chats/{conv}/messages",
                     json={"body": "привет", "client_nonce": n}, headers=a).json()
    assert m1["id"] == m2["id"], "повтор с тем же nonce — тот же message, не дубль"
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]
    assert len([m for m in msgs if m["body"] == "привет"]) == 1
    assert m1["body_format"] == "markdown"      # §D1


def test_d3_reactions_add_remove(client):
    from urllib.parse import quote
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "оценка"}, headers=a).json()["id"]
    #Недопустимый эмодзи отвергается.
    assert client.post(f"/web/messenger/messages/{mid}/reactions",
                       json={"emoji": "X"}, headers=b).status_code == 400
    #b ставит 👍 (повтор идемпотентен).
    assert client.post(f"/web/messenger/messages/{mid}/reactions",
                       json={"emoji": "👍"}, headers=b).status_code == 200
    client.post(f"/web/messenger/messages/{mid}/reactions", json={"emoji": "👍"}, headers=b)
    ra = [m for m in client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]
          if m["id"] == mid][0]["reactions"]
    assert ra == [{"emoji": "👍", "count": 1, "mine": False}]
    rb = [m for m in client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
          if m["id"] == mid][0]["reactions"]
    assert rb[0]["mine"] is True
    #Снять свою реакцию.
    client.delete(f"/web/messenger/messages/{mid}/reactions/{quote('👍')}", headers=b)
    ra = [m for m in client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]
          if m["id"] == mid][0]["reactions"]
    assert ra == []


def test_d6_system_message_on_join(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Проект", "member_ids": [b_id]}, headers=a).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/members", json={"user_ids": [c_id]}, headers=a)
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]
    sysm = [m for m in msgs if m["kind"] == "system"]
    assert any(m["body"].startswith("user_joined") and c_id in m["body"] for m in sysm)


def test_channels_never_get_join_leave_system_messages(client):
    """§12: «вступил/вышел» — ТОЛЬКО для групп. Канал может разом набрать много читателей
    (весь курс), и лента не должна тонуть в системных строчках — в отличие от групп
    (см. test_d6_system_message_on_join выше, где это ожидаемое поведение)."""
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/channel",
                       json={"title": "Курс", "writer_ids": [b_id]}, headers=a).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/members", json={"user_ids": [c_id]}, headers=a)
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]
    sysm = [m for m in msgs if m["kind"] == "system"]
    assert not any(m["body"].startswith("user_joined") for m in sysm)
    #Выход тоже без системного сообщения для канала.
    client.delete(f"/web/messenger/chats/{conv}/members/{c_id}", headers=a)
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]
    sysm = [m for m in msgs if m["kind"] == "system"]
    assert not any(m["body"].startswith("user_left") for m in sysm)


def test_curator_bulk_adds_class_group_to_new_chat_group(client):
    """§12: режим куратора — при создании ЧАТ-группы куратор указывает УЧЕБНУЮ группу
    целиком (class_groups), и все её студенты становятся участниками автоматически,
    вперемешку с индивидуально выбранными member_ids."""
    admin, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    #Делаем "a" куратором группы К-24 (оба студента b/c числятся в ней, см. _setup).
    r = client.post("/sync/push", json={"changes": {"users": [
        {"id": a_id, "curated_groups": ["К-24"]}]}}, headers=admin)
    assert r.status_code == 200, r.text
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "К-24 в сборе", "class_groups": ["К-24"]},
                       headers=a).json()["conversation_id"]
    info = client.get(f"/web/messenger/chats/{conv}", headers=a).json()
    ids = {p["user_id"] for p in info["participants"]}
    assert b_id in ids and c_id in ids, "оба студента группы должны попасть в чат-группу автоматически"


def test_non_curator_class_groups_are_ignored(client):
    """Учитель БЕЗ curated_groups не может массово затащить чужую группу — сервер
    молча игнорирует названия групп, которые он не курирует (скоуп проверяется на
    сервере, не на клиенте)."""
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Без права", "class_groups": ["К-24"]},
                       headers=a).json()["conversation_id"]
    info = client.get(f"/web/messenger/chats/{conv}", headers=a).json()
    ids = {p["user_id"] for p in info["participants"]}
    assert b_id not in ids and c_id not in ids


def test_d11_edit_history(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "опечтка"}, headers=a).json()["id"]
    client.patch(f"/web/messenger/messages/{mid}", json={"body": "опечатка"}, headers=a)
    v = client.get(f"/web/messenger/messages/{mid}/history", headers=a).json()["versions"]
    assert v[0]["body"] == "опечтка"
    assert v[-1]["body"] == "опечатка" and v[-1].get("current")


def test_d6_rename_group_writes_system_message(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Старое имя", "member_ids": [b_id]}, headers=a).json()["conversation_id"]
    #Обычный участник (не owner/admin) не может переименовать.
    assert client.patch(f"/web/messenger/chats/{conv}",
                        json={"title": "Взлом"}, headers=b).status_code == 403
    #Владелец переименовывает — событие видно всем участникам.
    r = client.patch(f"/web/messenger/chats/{conv}", json={"title": "Новое имя"}, headers=a)
    assert r.status_code == 200 and r.json()["title"] == "Новое имя"
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    sysm = [m for m in msgs if m["kind"] == "system"]
    assert any(m["body"] == "title_changedНовое имя" for m in sysm)


def test_d6_rename_rejects_direct_chat(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    assert client.patch(f"/web/messenger/chats/{conv}", json={"title": "X"}, headers=a).status_code == 400


# ── §D2: маскот-замедление (эскалирующий кулдаун вместо холодного 429) ────────────────
def test_d2_mascot_cooldown_shape_on_burst(client):
    from app import msg_limit
    msg_limit.reset()
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    #5 быстрых сообщений проходят (это и есть сам всплеск-триггер, но проверка идёт
    #ДО регистрации текущей попытки — 5-е ещё пройдёт, 6-е уже упрётся в порог).
    for i in range(5):
        r = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": f"msg{i}"}, headers=a)
        assert r.status_code == 200, r.text
    r = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "перебор"}, headers=a)
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["mascot"] is True and detail["cooldown_seconds"] == 8


def test_d2_systematic_flood_creates_moderation_ticket(client):
    from app import msg_limit
    msg_limit.reset()
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    #Готовим 2 уже «остывших» нарушения напрямую (не гонять реальные 5+5 минут ожидания).
    for _ in range(2):
        for i in range(5):
            client.post(f"/web/messenger/chats/{conv}/messages", json={"body": f"x{i}"}, headers=a)
        client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "over"}, headers=a)
        msg_limit._violations[a_id][-1] -= msg_limit._MASCOT_BURST_WINDOW + 1
        msg_limit._events[a_id].clear()             #новый «чистый» всплеск для следующей серии
    #Третий всплеск — систематика: 60 c + автотикет модерации.
    for i in range(5):
        client.post(f"/web/messenger/chats/{conv}/messages", json={"body": f"y{i}"}, headers=a)
    r = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "flood"}, headers=a)
    assert r.status_code == 429 and r.json()["detail"]["cooldown_seconds"] == 60

    reports = client.get("/web/admin/messenger/reports?status=open", headers=admin).json()["reports"]
    flood = [t for t in reports if t["reason_code"] == "flood" and t["reported_name"] == "Преподаватель"]
    assert flood, "систематический флуд должен создать автотикет модерации"


# ── §D7: статус пользователя (dnd/studying/away + текст у преподавателя) ─────────────
def test_d7_set_and_read_own_status(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    #Без статуса — пусто.
    assert client.get("/web/messenger/status", headers=a).json() == {"kind": "", "custom_text": ""}
    #Преподаватель ставит статус с текстом.
    r = client.post("/web/messenger/status", json={"kind": "dnd", "custom_text": "На паре"}, headers=a)
    assert r.status_code == 200 and r.json() == {"ok": True, "kind": "dnd", "custom_text": "На паре"}
    assert client.get("/web/messenger/status", headers=a).json() == {"kind": "dnd", "custom_text": "На паре"}
    #Некорректный kind отвергается.
    assert client.post("/web/messenger/status", json={"kind": "bogus"}, headers=a).status_code == 400


def test_d7_custom_text_ignored_for_non_teacher(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    r = client.post("/web/messenger/status",
                    json={"kind": "studying", "custom_text": "Готовлюсь к экзамену"}, headers=b)
    #Студент: kind принимается, но custom_text — нет (только у преподавателя).
    assert r.json() == {"ok": True, "kind": "studying", "custom_text": ""}


def test_d7_status_visible_in_directory_and_conv_info(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    client.post("/web/messenger/status", json={"kind": "dnd", "custom_text": "Занят"}, headers=a)
    users = client.get("/web/messenger/users?role=teacher", headers=b).json()["users"]
    teacher = [u for u in users if u["id"] == a_id][0]
    assert teacher["status_kind"] == "dnd" and teacher["status_text"] == "Занят"

    conv = _conv(client, a, b_id)
    info = client.get(f"/web/messenger/chats/{conv}", headers=b).json()
    person = [p for p in info["participants"] if p["user_id"] == a_id][0]
    assert person["status_kind"] == "dnd" and person["status_text"] == "Занят"


def test_d7_status_follows_peer_in_chat_list(client):
    """Статус собеседника приходит в СПИСКЕ чатов и обновляется при смене.

    Карточка собеседника в вебе бралась один раз при входе в чат и дальше не
    перечитывалась, поэтому смена статуса доезжала только после повторного открытия
    переписки — со стороны это выглядело как «статус не работает». Клиент теперь
    обновляет её из этого же списка, который и так опрашивается на каждом тике."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "привет"}, headers=a)

    def _peer():
        rows = client.get("/web/messenger/chats", headers=b).json()["chats"]
        return [c for c in rows if c["conversation_id"] == conv][0]["peer"]

    assert _peer()["status_kind"] == ""
    client.post("/web/messenger/status", json={"kind": "away"}, headers=a)
    assert _peer()["status_kind"] == "away"
    #Сброс в «Обычный» тоже должен доехать, иначе статус «залипал» бы навсегда.
    client.post("/web/messenger/status", json={"kind": ""}, headers=a)
    assert _peer()["status_kind"] == ""


# ── §D8: упоминания (@Фамилия — обычное, @!Фамилия — тихое, без пуша) ────────────────
def test_d8_mention_parsed_in_group(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Группа", "member_ids": [b_id, c_id]}, headers=a).json()["conversation_id"]
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "@Боб, посмотри домашку"}, headers=a)
    msg = r.json()
    assert msg["mentions"] == [{"user_id": b_id, "silent": False, "loud": False}]


def test_d8_silent_mention_marked(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "@!Боб, не срочно, просто на будущее"}, headers=a)
    #Историческая форма «@!Фамилия» остаётся ТИХОЙ: такие сообщения уже есть в
    #переписках, и переворачивать их смысл задним числом нельзя (см. _parse_mentions).
    assert r.json()["mentions"] == [{"user_id": b_id, "silent": True, "loud": False}]


# ── Пинги: /@Фамилия — тихо, /@!Фамилия и /!@Фамилия — громко ───────────────────────
def test_slash_mention_is_quiet(client):
    """`/@Фамилия` отмечает, но не звонит: ни звука, ни письма в «Систему»."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "/@Боб глянь когда сможешь"}, headers=a)
    assert r.json()["mentions"] == [{"user_id": b_id, "silent": True, "loud": False}]
    #Письма во вкладке «Уведомления» быть не должно — это и есть «тихо».
    events = client.get("/me/events?filter=all", headers=b).json()["items"]
    assert [e for e in events if e["kind"] == "mention"] == []


def test_loud_mention_creates_system_notification(client):
    """`/@!Фамилия` — громкая отметка: письмо во вкладку «Система» получателю."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "/@!Боб срочно нужен ответ"}, headers=a)
    assert r.json()["mentions"] == [{"user_id": b_id, "silent": False, "loud": True}]
    events = client.get("/me/events?filter=all", headers=b).json()["items"]
    mention = [e for e in events if e["kind"] == "mention"]
    assert len(mention) == 1, events
    assert "отметил" in mention[0]["body"].lower()
    #Автору письмо не приходит: отметить кого-то — не событие для самого отметившего.
    mine = client.get("/me/events?filter=all", headers=a).json()["items"]
    assert [e for e in mine if e["kind"] == "mention"] == []


def test_loud_mention_accepts_both_bang_orders(client):
    """`/!@Фамилия` — та же громкая отметка, что и `/@!Фамилия`.

    Порядок двух подряд идущих символов не запоминается, а цена ошибки несимметрична:
    вместо звонка человек получил бы тишину и не увидел срочное сообщение."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "/!@Боб посмотри пожалуйста"}, headers=a)
    assert r.json()["mentions"] == [{"user_id": b_id, "silent": False, "loud": True}]


def test_loud_mention_respects_conversation_mute(client):
    """Замьютивший беседу просил тишины — «громкость» отметки это не обходит."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    assert client.post(f"/web/messenger/chats/{conv}/mute",
                       json={"muted": True}, headers=b).status_code == 200
    client.post(f"/web/messenger/chats/{conv}/messages",
                json={"body": "/@!Боб отзовись"}, headers=a)
    events = client.get("/me/events?filter=all", headers=b).json()["items"]
    assert [e for e in events if e["kind"] == "mention"] == []


def test_chat_list_reports_unread_mention(client):
    """Список чатов отдаёт id САМОГО РАННЕГО непрочитанного упоминания — по нему клиент
    рисует «@» вместо счётчика и перематывает ленту к отметке."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    first = client.post(f"/web/messenger/chats/{conv}/messages",
                        json={"body": "/@!Боб первое"}, headers=a).json()["id"]
    client.post(f"/web/messenger/chats/{conv}/messages",
                json={"body": "/@Боб второе"}, headers=a)
    row = [c for c in client.get("/web/messenger/chats", headers=b).json()["chats"]
           if c["conversation_id"] == conv][0]
    assert row["mention_message_id"] == first
    assert row["mention_loud"] is True
    #Прочитали — значок гаснет (иначе он висел бы вечно).
    client.post(f"/web/messenger/chats/{conv}/read", json={}, headers=b)
    row2 = [c for c in client.get("/web/messenger/chats", headers=b).json()["chats"]
            if c["conversation_id"] == conv][0]
    assert row2["mention_message_id"] == 0


def test_d8_mention_ignores_non_participant(client):
    """Фамилия, которой нет среди участников ЭТОЙ беседы, не резолвится — не даёт
    заглянуть в чужой аккаунт по совпадению фамилии в другой беседе."""
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = _conv(client, a, b_id)          #Кэрол НЕ участник этой беседы
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "Кэров, ты здесь?"}, headers=a)
    assert r.json()["mentions"] == []


def test_d8_silent_mention_skips_push(client, monkeypatch):
    import app.rustore_push as rp
    calls = []
    monkeypatch.setattr(rp, "notify_login",
                        lambda db, login, title, body, data=None: calls.append(login) or 1)
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    #Боб офлайн — обычно получил бы пуш, но упоминание тихое → пуша быть не должно.
    client.post(f"/web/messenger/chats/{conv}/messages",
               json={"body": "@!Боб проверь позже"}, headers=a)
    assert "bob" not in calls


def test_d8_loud_mention_still_pushes(client, monkeypatch):
    import app.rustore_push as rp
    calls = []
    monkeypatch.setattr(rp, "notify_login",
                        lambda db, login, title, body, data=None: calls.append(login) or 1)
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = _conv(client, a, b_id)
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "@Боб, ответь"}, headers=a)
    assert "bob" in calls


# ── §D12: автоматические системные каналы (оценки/объявления/расписание) ────────────
def test_d12_grade_posted_creates_personal_channel(client):
    """Выставление оценки (Phase B /web/teacher/grade) само создаёт «Мои оценки» и
    публикует туда пост от лица «Вектора» — без ручного создания канала."""
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    assign_teacher(client, admin, a_id, "К-24", "Математика")
    #Занятие по группе Боба (см. _make_student — группа "К-24").
    r = client.post("/web/teacher/lesson", json={
        "group": "К-24", "subject": "Математика", "type": "Практика",
        "number": 1, "topic": "Тема", "date": "2026-01-10",
    }, headers=a)
    assert r.status_code == 200, r.text
    lesson_id = r.json()["id"]
    r = client.post("/web/teacher/grade",
                    json={"surname": "Боб", "name": "Бобов", "lesson_id": lesson_id, "grade": "5"},
                    headers=a)
    assert r.status_code == 200, r.text

    conv_id = f"sys:grades:{b_id}"
    chats = client.get("/web/messenger/chats", headers=b).json()["chats"]
    assert any(c["conversation_id"] == conv_id for c in chats), "канал должен сам появиться у студента"
    msgs = client.get(f"/web/messenger/chats/{conv_id}/messages", headers=b).json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["sender_name"] == "Вектор" and "5" in msgs[0]["body"] and "Математика" in msgs[0]["body"]
    #Читатель — не писатель: пробовать написать туда самому нельзя.
    assert client.post(f"/web/messenger/chats/{conv_id}/messages",
                       json={"body": "спасибо"}, headers=b).status_code == 403


def test_d12_teacher_opens_announcements_channel(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    r = client.post("/web/messenger/channels/announcements",
                   params={"group": "К-24"}, headers=a)
    assert r.status_code == 200, r.text
    conv_id = r.json()["conversation_id"]
    #Преподаватель (писатель) публикует ОБЫЧНЫМ send — отдельный эндпоинт не нужен.
    r2 = client.post(f"/web/messenger/chats/{conv_id}/messages",
                     json={"body": "Завтра пары не будет"}, headers=a)
    assert r2.status_code == 200, r2.text
    #Студенты той же группы видят объявление как читатели.
    chats_b = client.get("/web/messenger/chats", headers=b).json()["chats"]
    assert any(x["conversation_id"] == conv_id for x in chats_b)
    #Студент не может постить в канал объявлений (только читатель).
    assert client.post(f"/web/messenger/chats/{conv_id}/messages",
                       json={"body": "можно вопрос?"}, headers=b).status_code == 403


def test_d12_announcements_forbidden_for_student(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    assert client.post("/web/messenger/channels/announcements",
                       params={"group": "К-24"}, headers=b).status_code == 403


def test_d12_schedule_publish_posts_to_channel(client):
    admin, (a_id, a), (b_id, b), _ = _setup(client)
    r = client.post("/web/admin/schedule/publish", json={"group": "К-24"}, headers=admin)
    assert r.status_code == 200, r.text
    conv_id = "sys:schedule:К-24"
    msgs = client.get(f"/web/messenger/chats/{conv_id}/messages", headers=b).json()["messages"]
    assert len(msgs) == 1 and "К-24" in msgs[0]["body"] and msgs[0]["sender_name"] == "Вектор"


def test_d12_schedule_channel_addressable_for_group_with_slash(client):
    """Канал «Расписание · Группа» у группы со слэшем в имени обязан открываться по HTTP.

    Тот же класс дефекта, что уже был у объявлений/отчётов (см. _gtoken): слэш в id
    («sys:schedule:К74/1») приезжает в URL как %2F, Starlette раскодирует его обратно —
    путь распадается на лишний сегмент, GET проваливается в SPA-фолбэк (HTML вместо
    JSON), и канал не открывается. Соседний тест держал группу «К-24» без слэша, поэтому
    оставался зелёным рядом с дефектом — обратный ход обязателен именно на «К74/1»."""
    from urllib.parse import quote
    admin = make_admin(client)
    a = make_teacher(client, admin)
    sid = "stud:slashboy"
    client.post("/sync/push", json={"changes": {"users": [{
        "id": sid, "role": "student", "login": "slashboy",
        "password_hash": hash_password("studpass1"), "full_name": "Слэшев Слэш",
        "surname": "Слэшев", "name": "Слэш", "group_name": "К74/1",
    }]}}, headers=admin)
    r = client.post("/auth/login", json={"login": "slashboy", "password": "studpass1"})
    b = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = client.post("/web/admin/schedule/publish", json={"group": "К74/1"}, headers=admin)
    assert r.status_code == 200, r.text
    conv_id = r.json().get("conversation_id") or "sys:schedule:К74~1"
    assert "/" not in conv_id, "слэш в id канала расписания делает его недоступным по HTTP"
    resp = client.get(f"/web/messenger/chats/{quote(conv_id, safe='')}/messages", headers=b)
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("content-type", "").startswith("application/json"), \
        "GET ушёл в SPA-фолбэк — путь со слэшем не совпал ни с одним роутом"
    assert resp.json()["messages"], "канал пуст: публикация не доехала до адресуемой беседы"


# ── Команда /vector — docs/MESSENGER-ADDON-PLAN-GPT.md (заметка в конце файла): «AI-поиск
# по смыслу» реализован переиспользованием УЖЕ существующего анти-галлюцинационного Вектора
# (/web/vector/ask), а не отдельной embedding-инфраструктурой — как и предложено в заметке.
def test_vector_command_answers_in_saved(client):
    """`/vector` работает в «Избранном» — личном чате с самим собой."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post("/web/messenger/chats/saved", headers=b).json()["conversation_id"]
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "/vector привет"}, headers=b)
    assert r.status_code == 200, r.text
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert len(msgs) == 2
    assert msgs[0]["body"] == "/vector привет" and msgs[0]["sender_id"] == b_id
    assert msgs[1]["sender_id"] == "system" and msgs[1]["kind"] == "text" and msgs[1]["body"]
    #Тот же факт-движок, что у выделенной вкладки «ИИ Помощник» — слово в слово.
    direct = client.post("/web/vector/ask", json={"message": "привет"}, headers=b).json()
    assert msgs[1]["body"] == direct["text"]


def test_vector_command_ignored_outside_saved(client):
    """В ОБЫЧНОМ чате команда не срабатывает: ответ Вектора публиковался бы всем участникам,
    а роль-скоуп считается по спросившему — соседи увидели бы чужую выборку. Сообщение при
    этом остаётся обычным текстом, отправку не ломаем."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b).json()["conversation_id"]
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "/vector привет"}, headers=b)
    assert r.status_code == 200, r.text
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert len(msgs) == 1, "в обычном чате ИИ-ответа быть не должно"
    assert all(m["sender_id"] != "system" for m in msgs)


def test_reply_to_vector_continues_conversation_without_prefix(client):
    """Ответ на реплику Вектора — следующий вопрос ему же, БЕЗ повторного «/vector».

    Цепочка уточнений иначе требовала писать префикс на каждой строке, хотя адресат из
    ответа однозначен."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post("/web/messenger/chats/saved", headers=b).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages",
                json={"body": "/vector привет"}, headers=b)
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    vector_msg = msgs[1]
    assert vector_msg["sender_id"] == "system"

    #Отвечаем на сообщение Вектора обычным текстом — без «/vector».
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "а какой у меня средний балл",
                          "reply_to_id": vector_msg["id"]}, headers=b)
    assert r.status_code == 200, r.text
    msgs2 = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert len(msgs2) == 4, msgs2
    assert msgs2[3]["sender_id"] == "system" and msgs2[3]["body"]


def test_reply_to_own_note_does_not_call_vector(client):
    """Ответ на СВОЮ заметку — обычная цитата: личные записи не должны внезапно уходить
    в модель только потому, что на них ответили."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post("/web/messenger/chats/saved", headers=b).json()["conversation_id"]
    mine = client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "купить тетрадь"}, headers=b).json()
    client.post(f"/web/messenger/chats/{conv}/messages",
                json={"body": "и ручку", "reply_to_id": mine["id"]}, headers=b)
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert len(msgs) == 2
    assert all(m["sender_id"] != "system" for m in msgs)


def test_reply_to_vector_outside_saved_stays_silent(client):
    """Граница «только Избранное» действует и для цепочки: в обычном чате ответ на любое
    сообщение остаётся обычным ответом."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b).json()["conversation_id"]
    first = client.post(f"/web/messenger/chats/{conv}/messages",
                        json={"body": "привет"}, headers=b).json()
    client.post(f"/web/messenger/chats/{conv}/messages",
                json={"body": "какой средний балл", "reply_to_id": first["id"]}, headers=b)
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert all(m["sender_id"] != "system" for m in msgs)


def test_saved_chat_cannot_be_deleted_only_cleared(client):
    """«Избранное» — один на пользователя и всегда в списке: удаление сводится к очистке."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post("/web/messenger/chats/saved", headers=b).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "заметка"}, headers=b)
    r = client.delete(f"/web/messenger/chats/{conv}", headers=b)
    assert r.status_code == 200 and r.json().get("cleared") is True
    #История очищена, но сам раздел остался в списке чатов.
    assert client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"] == []
    assert any(c["conversation_id"] == conv
               for c in client.get("/web/messenger/chats", headers=b).json()["chats"])


def test_vector_command_ignored_without_question(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "/vector"}, headers=b)
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert len(msgs) == 1, "команда без вопроса — ИИ-ответа быть не должно"


def test_vector_context_includes_saved_notes(client):
    """Команда /vector получает предыдущие заметки как контекст — иначе «а это когда?»
    отвечать не по чему."""
    from app.routers import messenger as M
    from app.db import SessionLocal
    from app.models import User
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post("/web/messenger/chats/saved", headers=b).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages",
                json={"body": "экзамен по сетям 14 июня"}, headers=b)
    client.post(f"/web/messenger/chats/{conv}/messages",
                json={"body": "принести зачётку"}, headers=b)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == b_id).first()
        ctx = M._saved_context(db, conv, user)
    finally:
        db.close()
    assert "экзамен по сетям 14 июня" in ctx and "принести зачётку" in ctx
    assert ctx.count("Я:") >= 2, "заметки помечаются автором"


def test_vector_context_masks_real_names(client):
    """⚠️ 152-ФЗ: заметки уходят в облачную модель, поэтому ФИО реальных людей обязаны
    быть замаскированы (продукт публично обещает, что ПДн в облако не уходят)."""
    from app.routers import messenger as M
    from app.db import SessionLocal
    from app.models import User
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post("/web/messenger/chats/saved", headers=b).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages",
                json={"body": "спросить у Кэрол Кэровой про долг"}, headers=b)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == b_id).first()
        ctx = M._saved_context(db, conv, user)
    finally:
        db.close()
    assert "Кэрол" not in ctx and "Кэрова" not in ctx, "ФИО не должно уехать в модель"
    assert "Студент" in ctx and "про долг" in ctx, "смысл заметки при этом сохраняется"


def test_vector_context_respects_cleared_history(client):
    """Очищенные заметки в контекст не попадают — иначе очистка была бы фикцией."""
    from app.routers import messenger as M
    from app.db import SessionLocal
    from app.models import User
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post("/web/messenger/chats/saved", headers=b).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "старая тайна"}, headers=b)
    client.delete(f"/web/messenger/chats/{conv}", headers=b)          #у saved = очистка
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "новая заметка"}, headers=b)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == b_id).first()
        ctx = M._saved_context(db, conv, user)
    finally:
        db.close()
    assert "старая тайна" not in ctx and "новая заметка" in ctx


def test_vector_command_scoped_to_caller_role(client):
    """Роль вызывающего скоупит данные — как и в дедике /web/vector/ask: ответ считается по
    ВЫЗЫВАЮЩЕМУ (студенту b). Личный чат делает это безопасным: выборку видит только он."""
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/saved", headers=b).json()["conversation_id"]
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "/vector сколько у меня оценок"}, headers=b)
    assert r.status_code == 200
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert len(msgs) == 2 and msgs[1]["sender_id"] == "system"


def test_vector_command_does_not_trigger_on_normal_message(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "просто привет"}, headers=b)
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert len(msgs) == 1


# ── Организация списка чатов: закреп/архив/избранное (docs/MESSENGER-ADDON-PLAN-GPT*.md) ─
def test_pin_and_unpin_chat(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b).json()["conversation_id"]
    chats = client.get("/web/messenger/chats", headers=b).json()["chats"]
    assert next(c for c in chats if c["conversation_id"] == conv)["pinned"] is False

    assert client.post(f"/web/messenger/chats/{conv}/pin", headers=b).status_code == 200
    chats = client.get("/web/messenger/chats", headers=b).json()["chats"]
    assert next(c for c in chats if c["conversation_id"] == conv)["pinned"] is True
    #Личное состояние: у собеседника (a) чат НЕ закреплён.
    chats_a = client.get("/web/messenger/chats", headers=a).json()["chats"]
    assert next(c for c in chats_a if c["conversation_id"] == conv)["pinned"] is False

    assert client.delete(f"/web/messenger/chats/{conv}/pin", headers=b).status_code == 200
    chats = client.get("/web/messenger/chats", headers=b).json()["chats"]
    assert next(c for c in chats if c["conversation_id"] == conv)["pinned"] is False


def test_pinned_chat_sorts_above_recent(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv_old = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv_old}/pin", headers=b)
    conv_new = client.post(f"/web/messenger/chats/direct/{c_id}", headers=b).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv_new}/messages", json={"body": "привет"}, headers=b)
    chats = client.get("/web/messenger/chats", headers=b).json()["chats"]
    assert chats[0]["conversation_id"] == conv_old, "закреплённый — всегда сверху, даже если старее"


def test_archive_and_unarchive_chat(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b).json()["conversation_id"]
    assert client.post(f"/web/messenger/chats/{conv}/archive", headers=b).status_code == 200
    chats = client.get("/web/messenger/chats", headers=b).json()["chats"]
    row = next(c for c in chats if c["conversation_id"] == conv)
    assert row["archived"] is True
    #Архив — НЕ удаление: сообщения/доступ никуда не делись.
    assert client.get(f"/web/messenger/chats/{conv}/messages", headers=b).status_code == 200
    #Собеседника архив не касается (личное состояние).
    chats_a = client.get("/web/messenger/chats", headers=a).json()["chats"]
    assert next(c for c in chats_a if c["conversation_id"] == conv)["archived"] is False

    assert client.delete(f"/web/messenger/chats/{conv}/archive", headers=b).status_code == 200
    chats = client.get("/web/messenger/chats", headers=b).json()["chats"]
    assert next(c for c in chats if c["conversation_id"] == conv)["archived"] is False


def test_saved_messages_is_personal_and_pinned(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    r = client.post("/web/messenger/chats/saved", headers=b)
    assert r.status_code == 200, r.text
    conv = r.json()["conversation_id"]
    #Идемпотентно — повторный вызов возвращает ТУ ЖЕ беседу.
    assert client.post("/web/messenger/chats/saved", headers=b).json()["conversation_id"] == conv
    chats = client.get("/web/messenger/chats", headers=b).json()["chats"]
    row = next(c for c in chats if c["conversation_id"] == conv)
    assert row["kind"] == "saved" and row["pinned"] is True and row["title"] == "Избранное"
    #Заметка себе — обычная отправка, инфраструктура полностью переиспользуется.
    r2 = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "не забыть про лабу"}, headers=b)
    assert r2.status_code == 200, r2.text
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert len(msgs) == 1 and msgs[0]["body"] == "не забыть про лабу"
    #Чужой (a) доступа к моему «Избранному» не имеет.
    assert client.get(f"/web/messenger/chats/{conv}/messages", headers=a).status_code == 403


# ── Треды: ответы на сообщение (docs/MESSENGER-ADDON-PLAN-GPT-SMART.md §3.3) ─────────────
def test_thread_reply_count_and_view(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b).json()["conversation_id"]
    root = client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "как решать задачу 3?"}, headers=b).json()
    client.post(f"/web/messenger/chats/{conv}/messages",
               json={"body": "смотри теорему 2", "reply_to_id": root["id"]}, headers=a)
    client.post(f"/web/messenger/chats/{conv}/messages",
               json={"body": "спасибо, разобрался", "reply_to_id": root["id"]}, headers=b)

    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    root_out = next(m for m in msgs if m["id"] == root["id"])
    assert root_out["reply_count"] == 2

    thread = client.get(f"/web/messenger/chats/{conv}/messages/thread/{root['id']}", headers=b).json()
    assert len(thread["messages"]) == 2
    assert [m["body"] for m in thread["messages"]] == ["смотри теорему 2", "спасибо, разобрался"]


# ── Поиск внутри чата ─────────────────────────────────────────────────────────────────
def test_search_within_chat_case_insensitive(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "Когда экзамен по Математике?"}, headers=b)
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "Завтра пара по физике"}, headers=a)

    r = client.get(f"/web/messenger/chats/{conv}/messages/search", params={"q": "МАТЕМАТИКЕ"}, headers=b)
    assert r.status_code == 200
    found = r.json()["messages"]
    assert len(found) == 1 and "Математике" in found[0]["body"]

    empty = client.get(f"/web/messenger/chats/{conv}/messages/search", params={"q": "химия"}, headers=b).json()
    assert empty["messages"] == []


def test_search_within_chat_forbidden_for_non_participant(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b).json()["conversation_id"]
    assert client.get(f"/web/messenger/chats/{conv}/messages/search",
                      params={"q": "х"}, headers=c).status_code == 403


# ── Кто прочитал сообщение (переиспользует last_read_at) ─────────────────────────────────
def test_read_by_reflects_participant_read_state(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{a_id}", headers=b).json()["conversation_id"]
    m = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "видел объявление?"}, headers=b).json()
    #Пока препод не читал беседу — среди прочитавших не найдётся никого (кроме автора).
    before = client.get(f"/web/messenger/messages/{m['id']}/read_by", headers=b).json()
    assert before["users"] == []
    #Препод открывает беседу и читает до последнего сообщения.
    client.post(f"/web/messenger/chats/{conv}/read", headers=a)
    after = client.get(f"/web/messenger/messages/{m['id']}/read_by", headers=b).json()
    assert len(after["users"]) == 1 and after["users"][0]["id"] == a_id
    #Панель «Реакции» (контекстное меню) показывает время — переиспользует last_read_at,
    #отдельной таблицы «просмотрено именно это сообщение тогда-то» нет и не будет.
    assert after["users"][0]["last_read_at"]


# ── Шаблоны быстрых ответов преподавателя ────────────────────────────────────────────
def test_teacher_templates_crud(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    assert client.get("/web/messenger/templates", headers=a).json()["templates"] == []

    r = client.post("/web/messenger/templates", json={"body": "Работа принята"}, headers=a)
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    templates = client.get("/web/messenger/templates", headers=a).json()["templates"]
    assert templates == [{"id": tid, "body": "Работа принята"}]

    assert client.delete(f"/web/messenger/templates/{tid}", headers=a).status_code == 200
    assert client.get("/web/messenger/templates", headers=a).json()["templates"] == []


def test_teacher_templates_forbidden_for_student(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    assert client.post("/web/messenger/templates", json={"body": "тест"}, headers=b).status_code == 403
    #Список — читать может кто угодно (пустой), запрет только на создание.
    assert client.get("/web/messenger/templates", headers=b).json()["templates"] == []


# ── Цензура мата (profanity_filter.py) ───────────────────────────────────────────────
def test_send_message_censors_profanity(client):
    """Отправка МАСКИРУЕТ найденное слово блоками, а не отклоняет сообщение —
    как автомодерация Twitch/Discord, а не жёсткий бан на отправку. Маска в
    мессенджере — НЕ «*» (см. test_messenger_mask_is_not_markdown_syntax ниже)."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "ты хуйню написал"}, headers=a)
    assert r.status_code == 200, r.text
    #Маскируется только сам корень («хуй»), а не всё склонённое слово — так и задуман
    #модуль (см. profanity_filter.py), не расширяем матч руками до границ слова.
    assert r.json()["body"] == "ты ███ню написал"
    #Собеседник в истории тоже видит уже зацензуренный текст, не оригинал.
    hist = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert hist[0]["body"] == "ты ███ню написал"


def test_send_message_clean_text_untouched(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "Привет, как дела?"}, headers=a)
    assert r.json()["body"] == "Привет, как дела?"


def test_edit_message_censors_profanity(client):
    """Правка на матерный текст тоже маскируется, а не проходит как есть."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    mid = client.post(f"/web/messenger/chats/{conv}/messages",
                      json={"body": "нормальный текст"}, headers=a).json()["id"]
    r = client.patch(f"/web/messenger/messages/{mid}", json={"body": "сам ты мудак"}, headers=a)
    assert r.status_code == 200, r.text
    assert r.json()["body"] == "сам ты █████"


def test_messenger_mask_is_not_markdown_syntax(client):
    """Регрессия на реальный баг: два цензурируемых слова в одном сообщении дают ДВА
    отдельных прогона маски — если маска сама «*», markdownLite.js на клиенте может
    склеить «**»/«*» одного прогона с другим и превратить текст МЕЖДУ ними в
    жирный/курсив (проверено эмпирически до фикса). Маска мессенджера обязана не
    входить ни в один спецсимвол разметки, тогда коллизия невозможна ни при каких
    соседних символах — не только в конкретно этом примере."""
    import profanity_filter
    assert profanity_filter.MESSENGER_SAFE_MASK not in "*_~#>-`"
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "бля, ты мудак"}, headers=a)
    assert r.status_code == 200, r.text
    body = r.json()["body"]
    assert "*" not in body


# ── Личные заметки о людях (Discord-style «Notes», карточка профиля) ─────────────────
def test_note_roundtrip_and_default_empty(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    assert client.get(f"/web/messenger/users/{b_id}/note", headers=a).json()["text"] == ""

    r = client.post(f"/web/messenger/users/{b_id}/note", json={"text": "любит опаздывать"}, headers=a)
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "любит опаздывать"
    assert client.get(f"/web/messenger/users/{b_id}/note", headers=a).json()["text"] == "любит опаздывать"


def test_note_is_private_to_author(client):
    """Заметка автора о Бобе не видна ни самому Бобу, ни третьему лицу — у каждого своя."""
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    client.post(f"/web/messenger/users/{b_id}/note", json={"text": "секрет препода"}, headers=a)
    assert client.get(f"/web/messenger/users/{b_id}/note", headers=b).json()["text"] == ""
    assert client.get(f"/web/messenger/users/{b_id}/note", headers=c).json()["text"] == ""
    #у самого Боба про Боба (=про себя) — своя, независимая запись
    client.post(f"/web/messenger/users/{b_id}/note", json={"text": "памятка себе"}, headers=b)
    assert client.get(f"/web/messenger/users/{b_id}/note", headers=a).json()["text"] == "секрет препода"
    assert client.get(f"/web/messenger/users/{b_id}/note", headers=b).json()["text"] == "памятка себе"


def test_note_self_note_supported(client):
    """Заметка про самого себя (author_id == about_user_id) — «памятка себе» на своей же
    карточке, заказчик отдельно просил не выпиливать этот случай."""
    _, (a_id, a), _, _ = _setup(client)
    r = client.post(f"/web/messenger/users/{a_id}/note", json={"text": "не забыть про журнал"}, headers=a)
    assert r.status_code == 200, r.text
    assert client.get(f"/web/messenger/users/{a_id}/note", headers=a).json()["text"] == "не забыть про журнал"


def test_self_note_works_with_the_id_a_client_can_actually_obtain(client):
    """Заметка про себя достижима ТЕМ id, который клиент реально может узнать.

    Тест выше (`test_note_self_note_supported`) годами был зелёным, а функция при этом
    не работала: он берёт id из внутренностей теста, а живому клиенту брать его было
    неоткуда — после входа сервер отдаёт только логин, роль и ФИО. Страница профиля
    подставляла в путь пустую строку, запрос не уходил вовсе, и кнопка «Сохранить»
    молча ничего не делала. Поэтому здесь id добывается ровно так же, как в браузере —
    из /me/prefs, — и только потом проверяется сама заметка."""
    _, (a_id, a), _, _ = _setup(client)

    me = client.get("/me/prefs", headers=a)
    assert me.status_code == 200, me.text
    my_id = me.json().get("user_id")
    assert my_id == a_id, "клиент обязан узнавать СВОЙ id, иначе путь /users/{id}/… пуст"

    r = client.post(f"/web/messenger/users/{my_id}/note", json={"text": "памятка себе"}, headers=a)
    assert r.status_code == 200, r.text
    assert client.get(f"/web/messenger/users/{my_id}/note", headers=a).json()["text"] == "памятка себе"


def test_note_overwrite_upserts(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    client.post(f"/web/messenger/users/{b_id}/note", json={"text": "первая версия"}, headers=a)
    client.post(f"/web/messenger/users/{b_id}/note", json={"text": "вторая версия"}, headers=a)
    assert client.get(f"/web/messenger/users/{b_id}/note", headers=a).json()["text"] == "вторая версия"


def test_note_empty_text_deletes_row(client):
    """Пустой текст после обрезки — удаляет строку, а не оставляет пустую (не копим
    мёртвые записи на каждое «написал и стёр»)."""
    _, (a_id, a), (b_id, b), _ = _setup(client)
    client.post(f"/web/messenger/users/{b_id}/note", json={"text": "черновик"}, headers=a)
    r = client.post(f"/web/messenger/users/{b_id}/note", json={"text": "   "}, headers=a)
    assert r.status_code == 200 and r.json()["text"] == ""
    assert client.get(f"/web/messenger/users/{b_id}/note", headers=a).json()["text"] == ""

    from app.db import SessionLocal
    from app.models import UserNote
    db = SessionLocal()
    try:
        assert db.query(UserNote).filter(UserNote.author_id == a_id, UserNote.about_user_id == b_id).count() == 0
    finally:
        db.close()


def test_note_is_clamped_to_max_chars(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    r = client.post(f"/web/messenger/users/{b_id}/note", json={"text": "x" * 900}, headers=a)
    assert r.status_code == 200
    assert len(r.json()["text"]) == 300


def test_note_requires_auth(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    assert client.get(f"/web/messenger/users/{b_id}/note").status_code == 401
    assert client.post(f"/web/messenger/users/{b_id}/note", json={"text": "x"}).status_code == 401


# ── «Общие группы»/«Общие каналы» на карточке профиля ─────────────────────────────────
def test_shared_groups_and_channels_are_the_intersection(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    #Группа с a+b (общая), канал с a+b (общий), и группа с a+c (НЕ общая с b).
    g_shared = client.post("/web/messenger/chats/group",
                           json={"title": "Общая группа", "member_ids": [b_id]}, headers=a).json()["conversation_id"]
    ch_shared = client.post("/web/messenger/chats/channel",
                            json={"title": "Общий канал", "writer_ids": [b_id]}, headers=a).json()["conversation_id"]
    client.post("/web/messenger/chats/group", json={"title": "Только a+c", "member_ids": [c_id]}, headers=a)

    r = client.get(f"/web/messenger/users/{b_id}/shared", headers=a)
    assert r.status_code == 200, r.text
    data = r.json()
    assert [x["id"] for x in data["groups"]] == [g_shared]
    assert [x["id"] for x in data["channels"]] == [ch_shared]


def test_shared_empty_when_no_mutual_conversations(client):
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    r = client.get(f"/web/messenger/users/{c_id}/shared", headers=a)
    assert r.status_code == 200
    assert r.json() == {"groups": [], "channels": []}


def test_shared_requires_auth(client):
    _, (a_id, a), (b_id, b), _ = _setup(client)
    assert client.get(f"/web/messenger/users/{b_id}/shared").status_code == 401


# ── Гифка-баннер профиля (3.7) ───────────────────────────────────────────────────────
def test_profile_banner_travels_to_other_people(client):
    """Баннер обязан доезжать до ОБОИХ мест, где клиент рисует чужую карточку: до
    отдельного профиля и до списка участников беседы. Поля добавляют по одному месту за
    раз, и «в личке видно, а в группе нет» уже случалось со стилем никнейма."""
    _, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    url = "https://static.klipy.com/gif/xyz/md.gif"
    client.post("/me/prefs", json={"profile_banner": url}, headers=a)

    card = client.get(f"/web/messenger/users/{a_id}/profile", headers=b).json()["profile"]
    assert card["profile_banner"] == url

    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Г", "member_ids": [b_id, c_id]}, headers=a).json()["conversation_id"]
    info = client.get(f"/web/messenger/chats/{conv}", headers=b).json()
    owner = next(p for p in info["participants"] if p["user_id"] == a_id)
    assert owner["profile_banner"] == url


def test_profile_banner_defaults_to_empty_string(client):
    #Пусто — клиент рисует обычную плашку цвета профиля, как и до появления баннеров.
    _, (a_id, a), (b_id, b), _ = _setup(client)
    card = client.get(f"/web/messenger/users/{a_id}/profile", headers=b).json()["profile"]
    assert card["profile_banner"] == ""


def test_gif_avatar_is_visible_to_others_like_a_picture_one(client):
    #Аватарка-гифка — то же поле `avatar`, что и своя картинка (см. me.py). Тест держит
    #это свойство: разведи их на два поля — и половина мест перестанет её показывать.
    _, (a_id, a), (b_id, b), _ = _setup(client)
    url = "https://static.klipy.com/gif/ava/xs.webp"
    client.post("/me/prefs", json={"avatar": url}, headers=a)
    card = client.get(f"/web/messenger/users/{a_id}/profile", headers=b).json()["profile"]
    assert card["avatar"] == url

# ── Метка клиента (client_nonce) и оптимистичная отправка ────────────────────────────
# Заведено 24.08.2026. Веб рисует своё сообщение СРАЗУ по нажатию, черновиком, и опознаёт
# в ленте настоящую версию по этой метке: то же сообщение приходит двумя путями (ответом
# на POST и событием сокета), кто успеет первым — зависит от сети.
def test_client_nonce_returns_to_author_only(client):
    """Свою метку автор получает, ЧУЖУЮ не видит никто.

    Найдено Полковником: поле клали в ответ безусловно, то есть чужие метки уезжали всем
    участникам беседы. Прав это не давало, но выносило наружу поле, до тех пор жившее
    только в базе, — а вместе с дедупом без отправителя (см. следующий тест) позволяло
    подменить себе собственный черновик чужим сообщением.
    """
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "Черновик и метка", "client_nonce": "nonce-A-1"}, headers=a)
    assert r.status_code == 200, r.text
    assert r.json()["client_nonce"] == "nonce-A-1", "автор обязан получить свою метку обратно"

    #Собеседник видит сообщение, но метки в нём нет.
    hist = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    чужое = [m for m in hist if m["sender_id"] == a_id]
    assert чужое and all(m["client_nonce"] == "" for m in чужое), "чужая метка ушла наружу"
    #А в своей истории автор её по-прежнему видит.
    своё = client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]
    assert [m["client_nonce"] for m in своё if m["sender_id"] == a_id] == ["nonce-A-1"]


def test_nonce_dedup_is_per_sender(client):
    """Метка уникальна для КЛИЕНТА, а не для беседы.

    Без sender_id в дедупе участник, взявший чужую метку, получал бы в ответ ЧУЖОЕ
    сообщение — и клиент подменил бы им собственный черновик.
    """
    _, (a_id, a), (b_id, b), _ = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages",
                json={"body": "Сообщение автора", "client_nonce": "общая-метка"}, headers=a)
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "Сообщение собеседника", "client_nonce": "общая-метка"}, headers=b)
    assert r.status_code == 200, r.text
    assert r.json()["sender_id"] == b_id, "вернулось чужое сообщение вместо своего"
    assert r.json()["body"] == "Сообщение собеседника"

    #При этом СВОЙ повтор с той же меткой по-прежнему идемпотентен (§D10, ретрай сети).
    повтор = client.post(f"/web/messenger/chats/{conv}/messages",
                         json={"body": "Сообщение автора", "client_nonce": "общая-метка"}, headers=a)
    assert повтор.json()["id"] == [m for m in
        client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]
        if m["sender_id"] == a_id][0]["id"], "повтор с той же меткой создал дубль"



def test_members_can_be_added_to_an_existing_chat(client):
    """Добавление участников в УЖЕ СОЗДАННУЮ беседу — и людьми, и учебной группой.

    🔥 До 25.08.2026 у ручки `POST /chats/{id}/members` не было НИ ОДНОГО вызывающего:
    она работала, а через продукт добавить человека в беседу было нельзя вовсе —
    только правкой базы руками. Влад сообщил это как «в беседы невозможно добавить
    новых людей». Наш самый частый класс дефекта: поведение проверено, вызова нет.

    Здесь проверяется САМА ручка; за то, что её кто-то зовёт, отвечает сверка контракта
    `tools/graph_api_bridge.py` (она эту ручку всё время показывала в списке «сервер
    никто не зовёт», просто список не печатался)."""
    admin, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Пустая"}, headers=a).json()["conversation_id"]
    ids = {p["user_id"] for p in client.get(f"/web/messenger/chats/{conv}", headers=a).json()["participants"]}
    assert ids == {a_id}, "в новой группе должен быть только создатель"

    #Поимённо.
    r = client.post(f"/web/messenger/chats/{conv}/members", json={"user_ids": [b_id]}, headers=a)
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1
    ids = {p["user_id"] for p in client.get(f"/web/messenger/chats/{conv}", headers=a).json()["participants"]}
    assert b_id in ids

    #Повторное добавление того же человека не задваивает участника и не врёт про успех.
    assert client.post(f"/web/messenger/chats/{conv}/members",
                       json={"user_ids": [b_id]}, headers=a).json()["added"] == 0


def test_adding_a_whole_class_group_obeys_the_same_curator_scope(client):
    """Учебной группой добавлять можно, но ТОЛЬКО свою курируемую.

    ⚠️ Правило то же, что при создании беседы, и это ОДНА функция на оба места
    (`_expand_class_groups`). Вторая копия правила ДОСТУПА разошлась бы с первой молча
    и в опасную сторону: «преподаватель массово добавил чужих студентов».

    Обратный ход: убери проверку `gname not in curated` — второй assert падает."""
    admin, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Позовём группу"}, headers=a).json()["conversation_id"]

    #Пока "a" НЕ куратор — чужая группа игнорируется молча.
    r = client.post(f"/web/messenger/chats/{conv}/members",
                    json={"user_ids": [], "class_groups": ["К-24"]}, headers=a)
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 0, "не куратор массово затащил чужую учебную группу"

    #Делаем куратором — теперь та же просьба срабатывает.
    client.post("/sync/push", json={"changes": {"users": [
        {"id": a_id, "curated_groups": ["К-24"]}]}}, headers=admin)
    r = client.post(f"/web/messenger/chats/{conv}/members",
                    json={"user_ids": [], "class_groups": ["К-24"]}, headers=a)
    assert r.json()["added"] >= 2, "куратор не смог добавить свою же группу"
    ids = {p["user_id"] for p in client.get(f"/web/messenger/chats/{conv}", headers=a).json()["participants"]}
    assert b_id in ids and c_id in ids


def test_attachments_refuse_everything_that_should_be_refused(client, monkeypatch):
    """Подпись загрузки проверяет участие, размер и тип — на СЕРВЕРЕ.

    ⚠️ Клиентская проверка существует ради вежливого сообщения, а не ради безопасности:
    обойти её можно curl'ом за секунду. Здесь проверяется настоящая дверь."""
    from app import storage
    admin, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Файлы"}, headers=a).json()["conversation_id"]

    #Без хранилища — ЧЕСТНЫЙ отказ, а не молчаливая потеря файла.
    #⚠️ Гасим режим ЯВНО. Раньше здесь хватало пустого `ENDPOINT`, но теперь способ
    #выбирается сам: нет ключей S3 — система смотрит на свободное место и включает
    #локальное хранение. На машине разработчика места хватает, поэтому «пустые ключи»
    #больше не означают «выключено».
    monkeypatch.setattr(storage, "MODE", "off")
    monkeypatch.setattr(storage, "ENDPOINT", "")
    r = client.post("/web/messenger/uploads/sign", headers=a,
                    json={"conversation_id": conv, "name": "x.pdf", "size": 10,
                          "mime": "application/pdf"})
    assert r.status_code == 503, "молча приняли файл при ненастроенном хранилище"

    #Настраиваем фиктивное хранилище: подпись считается локально, наружу ничего не идёт.
    monkeypatch.setattr(storage, "MODE", "s3")
    monkeypatch.setattr(storage, "MODE", "s3")
    for k, v in (("ENDPOINT", "https://s3.example"), ("BUCKET", "gb"),
                 ("ACCESS_KEY", "key"), ("SECRET_KEY", "secret")):
        monkeypatch.setattr(storage, k, v)

    #Чужая беседа — не участник.
    r = client.post("/web/messenger/uploads/sign", headers=b,
                    json={"conversation_id": conv, "name": "x.pdf", "size": 10,
                          "mime": "application/pdf"})
    assert r.status_code in (403, 404), "посторонний подписал загрузку в чужую беседу"

    #Слишком большой файл.
    r = client.post("/web/messenger/uploads/sign", headers=a,
                    json={"conversation_id": conv, "name": "x.pdf",
                          "size": storage.MAX_SIZE + 1, "mime": "application/pdf"})
    assert r.status_code == 413

    #Тип вне белого списка. ⚠️ Именно белого: чёрный всегда неполон, и первым же
    #пропущенным типом окажется исполняемый.
    r = client.post("/web/messenger/uploads/sign", headers=a,
                    json={"conversation_id": conv, "name": "x.exe", "size": 10,
                          "mime": "application/x-msdownload"})
    assert r.status_code == 415

    #Законный файл — подпись выдаётся, но вложение ещё НЕ готово.
    r = client.post("/web/messenger/uploads/sign", headers=a,
                    json={"conversation_id": conv, "name": "лекция.pdf", "size": 1024,
                          "mime": "application/pdf"})
    assert r.status_code == 200, r.text
    att = r.json()["attachment_id"]
    assert r.json()["url"].startswith("https://s3.example/gb/messenger/")
    assert "X-Amz-Signature=" in r.json()["url"], "ссылка без подписи"

    #Пока загрузка не подтверждена, приложить файл к сообщению нельзя: иначе в ленте
    #появится карточка файла, которого в хранилище нет.
    r = client.post(f"/web/messenger/chats/{conv}/messages", headers=a,
                    json={"body": "вот", "attachment_id": att})
    assert r.status_code == 400, "неподтверждённое вложение уехало в ленту"

    #Подтверждаем — и теперь можно.
    assert client.post(f"/web/messenger/uploads/{att}/done", headers=a).status_code == 200
    r = client.post(f"/web/messenger/chats/{conv}/messages", headers=a,
                    json={"body": "вот", "attachment_id": att})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "file"
    assert r.json()["attachment"]["name"] == "лекция.pdf"

    #Вкладка «Файлы» его видит.
    files = client.get(f"/web/messenger/chats/{conv}/files", headers=a).json()["files"]
    assert [f["id"] for f in files] == [att]

    #Ссылку на скачивание получает участник — и только он.
    assert client.get(f"/web/messenger/attachments/{att}/url", headers=a).status_code == 200
    assert client.get(f"/web/messenger/attachments/{att}/url", headers=b).status_code in (403, 404)


def test_one_account_cannot_take_the_whole_storage_in_one_evening(client, monkeypatch):
    """🔒 СУТОЧНЫЙ ПОТОЛОК НА ЧЕЛОВЕКА (находка пентеста 3.7.8, п. 2).

    Потолок на ОДИН файл был всегда, а на пользователя за сутки — нет. Значит один
    аккаунт клал сорок файлов по 25 МБ за вечер и занимал хранилище целиком, а остальные
    получали «места нет» — отказ, который читается как поломка сервера, а не как чьё-то
    злоупотребление.

    ⚠️ В отчёте пункт был отложен с пометкой «модуль ещё не подключён». Это перестало
    быть правдой 25.08.2026, когда вложения заработали: `uploads.py` исчез, а дыра
    переехала в `storage.py`. Отложенная находка не отменяется тем, что код переписали.

    Обратный ход: убери проверку `used + size > storage.MAX_USER_DAY_BYTES` в
    `sign_upload` — и этот тест краснеет на четвёртой подписи.
    """
    from app import storage
    admin, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Файлы"}, headers=a).json()["conversation_id"]
    for k, v in (("MODE", "s3"), ("ENDPOINT", "https://s3.example"), ("BUCKET", "gb"),
                 ("ACCESS_KEY", "key"), ("SECRET_KEY", "secret")):
        monkeypatch.setattr(storage, k, v)
    #Потолок опускаем до трёх файлов по 10 МБ — проверяем ПРАВИЛО, а не конкретное число.
    #Держать в тесте боевые 200 МБ значило бы гонять двадцать подписей ради того же вывода.
    monkeypatch.setattr(storage, "MAX_SIZE", 10 * 1024 * 1024)
    monkeypatch.setattr(storage, "MAX_USER_DAY_BYTES", 30 * 1024 * 1024)

    #У каждого своя беседа: потолок персональный, и проверять его надо там, где отказ
    #может прийти ТОЛЬКО из-за него. Пусти второго в чужую беседу — и 403 «не участник»
    #прочитался бы как сработавший лимит, то есть тест доказывал бы не то.
    conv_b = client.post("/web/messenger/chats/group",
                         json={"title": "Файлы Б"}, headers=b).json()["conversation_id"]

    def _sign(headers, mb=10, where=None):
        return client.post("/web/messenger/uploads/sign", headers=headers,
                           json={"conversation_id": where or conv, "name": "лекция.pdf",
                                 "size": mb * 1024 * 1024, "mime": "application/pdf"})

    for i in range(3):
        assert _sign(a).status_code == 200, "законная загрузка №%d отклонена" % (i + 1)

    over = _sign(a)
    assert over.status_code == 429, (
        "четвёртая загрузка прошла — суточного потолка нет, один аккаунт займёт "
        "хранилище целиком")
    #Отказ обязан НАЗЫВАТЬ причину: «попробуйте позже» без цифр отправит человека к
    #администратору выяснять, что сломалось.
    assert "МБ" in over.json()["detail"]

    #⚠️ Потолок ПЕРСОНАЛЬНЫЙ. Общий на всех превратил бы одного шумного пользователя в
    #отказ для всего колледжа — ровно то, от чего защищаемся.
    assert _sign(b, where=conv_b).status_code == 200, \
        "лимит одного человека закрыл загрузку другому"

    #Файл, который ВЛЕЗАЕТ в остаток, принимается: потолок ограничивает объём, а не
    #число попыток. Иначе человек с одним недогруженным файлом остался бы без вложений
    #на сутки.
    monkeypatch.setattr(storage, "MAX_USER_DAY_BYTES", 35 * 1024 * 1024)
    assert _sign(a, mb=4).status_code == 200, "мелкий файл не пустили в оставшийся объём"


def test_someone_elses_attachment_cannot_be_signed_onto_a_message(client, monkeypatch):
    """Чужим вложением подписаться нельзя.

    Обратный ход: убери из проверки `att.uploader_id != user.id` — тест краснеет."""
    from app import storage
    admin, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    monkeypatch.setattr(storage, "MODE", "s3")
    for k, v in (("ENDPOINT", "https://s3.example"), ("BUCKET", "gb"),
                 ("ACCESS_KEY", "key"), ("SECRET_KEY", "secret")):
        monkeypatch.setattr(storage, k, v)

    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Общая", "member_ids": [b_id]}, headers=a).json()["conversation_id"]
    r = client.post("/web/messenger/uploads/sign", headers=a,
                    json={"conversation_id": conv, "name": "своё.pdf", "size": 10,
                          "mime": "application/pdf"})
    att = r.json()["attachment_id"]
    client.post(f"/web/messenger/uploads/{att}/done", headers=a)

    #Загрузил "a", отправить пытается "b" — участник той же беседы.
    r = client.post(f"/web/messenger/chats/{conv}/messages", headers=b,
                    json={"body": "не моё", "attachment_id": att})
    assert r.status_code == 400, "чужое вложение удалось приложить к своему сообщению"

    #И подтвердить чужую загрузку тоже нельзя.
    assert client.post(f"/web/messenger/uploads/{att}/done", headers=b).status_code == 404

def test_a_file_alone_is_a_valid_message(client, monkeypatch):
    """Файл БЕЗ подписи — законное сообщение.

    🔥 Находка Полковника 25.08.2026. Гейт пустого текста стоял РАНЬШЕ разбора вложения,
    поэтому «выбрал файл, ничего не написал, отправил» отвечало 400 — но уже ПОСЛЕ того,
    как файл лёг в хранилище: объект оставался сиротой и оплаченным трафиком, а человек
    видел ошибку на ровном месте. Оба прежних теста слали body="вот" и покраснеть не
    могли — проверяли ровно тот путь, который работал.

    Обратный ход: убери из гейта проверку attachment_id — тест падает."""
    from app import storage
    admin, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    monkeypatch.setattr(storage, "MODE", "s3")
    for k, v in (("ENDPOINT", "https://s3.example"), ("BUCKET", "gb"),
                 ("ACCESS_KEY", "key"), ("SECRET_KEY", "secret")):
        monkeypatch.setattr(storage, k, v)
    monkeypatch.setattr(storage, "head_object", lambda key: {})

    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Только файл"}, headers=a).json()["conversation_id"]
    att = client.post("/web/messenger/uploads/sign", headers=a,
                      json={"conversation_id": conv, "name": "к.pdf", "size": 100,
                            "mime": "application/pdf"}).json()["attachment_id"]
    client.post(f"/web/messenger/uploads/{att}/done", headers=a)

    r = client.post(f"/web/messenger/chats/{conv}/messages", headers=a,
                    json={"body": "", "attachment_id": att})
    assert r.status_code == 200, f"файл без подписи отвергнут: {r.text}"
    assert r.json()["kind"] == "file"

    #А вот совсем пустое сообщение по-прежнему нельзя.
    assert client.post(f"/web/messenger/chats/{conv}/messages", headers=a,
                       json={"body": ""}).status_code == 400


def test_upload_confirmation_checks_what_actually_landed(client, monkeypatch):
    """Подтверждение сверяет ФАКТ, а не заявленное.

    🔥 Находка Полковника: подписанный PUT не умеет ограничивать РАЗМЕР
    (content-length-range есть только у POST-policy). По ссылке, выданной под
    «конспект.txt, 1 КБ», можно было положить пятигигабайтный исполняемый файл — наши
    413/415 проверяли ЗАЯВЛЕННОЕ. Тип теперь связан подписью, размер сверяется здесь.

    Обратный ход: убери вызов storage.head_object из confirm_upload — падает."""
    from app import storage
    admin, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    monkeypatch.setattr(storage, "MODE", "s3")
    for k, v in (("ENDPOINT", "https://s3.example"), ("BUCKET", "gb"),
                 ("ACCESS_KEY", "key"), ("SECRET_KEY", "secret")):
        monkeypatch.setattr(storage, k, v)
    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Проверка"}, headers=a).json()["conversation_id"]

    def sign(name="к.txt", mime="text/plain"):
        return client.post("/web/messenger/uploads/sign", headers=a,
                           json={"conversation_id": conv, "name": name, "size": 100,
                                 "mime": mime}).json()["attachment_id"]

    deleted = []
    monkeypatch.setattr(storage, "delete_object", lambda key: deleted.append(key) or True)

    #Легло СИЛЬНО больше заявленного.
    att = sign()
    monkeypatch.setattr(storage, "head_object",
                        lambda key: {"size": storage.MAX_SIZE + 1, "mime": "text/plain"})
    r = client.post(f"/web/messenger/uploads/{att}/done", headers=a)
    assert r.status_code == 413, "подтвердили файл, который вырос после подписи"
    assert deleted, "объект-нарушитель остался в хранилище"

    #Лёг ЧУЖОЙ тип.
    att = sign()
    monkeypatch.setattr(storage, "head_object",
                        lambda key: {"size": 100, "mime": "application/x-msdownload"})
    assert client.post(f"/web/messenger/uploads/{att}/done", headers=a).status_code == 415

    #Всё сошлось — и размер записывается НАСТОЯЩИЙ, а не заявленный.
    att = sign()
    monkeypatch.setattr(storage, "head_object", lambda key: {"size": 4242, "mime": "text/plain"})
    r = client.post(f"/web/messenger/uploads/{att}/done", headers=a)
    assert r.status_code == 200, r.text
    assert r.json()["attachment"]["size"] == 4242, "в базе осталось заявленное, а не реальное"


def test_signature_binds_the_content_type_and_download_carries_the_name():
    """Подпись связывает тип, а ссылка на скачивание несёт настоящее имя.

    🔥 Обе половины — находки Полковника. content_type принимался и НИГДЕ не
    использовался (подпись связывала только хост), а докстринг обещал подстановку имени
    заголовком, чего в коде не было: браузер сохранял файл под ключом att:<hex>.

    Обратный ход: убери content-type из signed — падает первое; убери extra из
    download_url — второе."""
    from app import storage
    old = {k: getattr(storage, k) for k in ("ENDPOINT", "BUCKET", "ACCESS_KEY", "SECRET_KEY")}
    try:
        storage.ENDPOINT, storage.BUCKET = "https://s3.example", "gb"
        storage.ACCESS_KEY, storage.SECRET_KEY = "key", "secret"

        put = storage.upload_url("messenger/c/att1", "application/pdf")
        assert "content-type" in put, "тип не входит в подпись — залить можно что угодно"

        get = storage.download_url("messenger/c/att1", "лекция 1.pdf", "application/pdf")
        assert "response-content-disposition" in get, "имя файла не уедет в скачивание"
        #⚠️ Кодирование ДВОЙНОЕ, и это правильно: сначала имя по RFC 5987 (`%D0…`), потом
        #весь параметр как значение query (`%25D0…`). Браузер раскодирует один раз и
        #получит `filename*=UTF-8''%D0…`. Первая версия теста искала одинарное `%D0` и
        #падала на КОРРЕКТНОЙ ссылке — проверка была неверна, а не код.
        assert "filename%2A%3DUTF-8" in get, get
        assert "%25D0" in get, "кириллица в имени не закодирована"
        from urllib.parse import unquote
        assert "filename*=UTF-8''%D0" in unquote(get), unquote(get)
    finally:
        for k, v in old.items():
            setattr(storage, k, v)


def test_thinning_never_returns_more_than_asked():
    """`_thin` действительно прореживает — при любом лимите.

    🔥 Находка Полковника: при limit < 3 хвост получался нулевым, а messages[-0:] — это
    ВЕСЬ список. Функция, обещающая «проредить до limit», возвращала переписку целиком.
    Дефект латентный: вылез бы у того, кто уменьшит SUMMARY_MESSAGES, увидев расход
    токенов, — то есть когда причину искать будут в последнюю очередь.

    Обратный ход: верни head = tail = limit // 3 — падает на limit 1 и 2."""
    from app.messenger_ai import _thin
    src = list(range(500))
    for limit in (1, 2, 3, 7, 50, 400):
        got = _thin(src, limit)
        real = [x for x in got if x is not None]
        #⚠️ При limit < 2 оба конца физически не помещаются. Держать конец переписки
        #важнее, чем уложиться в бюджет: сводка без развязки бесполезна.
        assert len(real) <= max(2, limit), f"limit={limit}: вернулось {len(real)} сообщений"
        assert real[0] == src[0], f"limit={limit}: потеряно начало переписки"
        assert real[-1] == src[-1], f"limit={limit}: потерян конец переписки"
    assert _thin(src, 0) == []
    assert _thin([1, 2, 3], 400) == [1, 2, 3], "короткую переписку трогать не надо"


def test_attachment_survives_every_serialization_path(client, monkeypatch):
    """Вложение видно и в ленте, и в закреплённых, и в списке чатов.

    🔥 Находка Полковника: оно терялось на трёх путях сразу. Расхождение между
    сериализациями ОДНОГО объекта замечают в последнюю очередь — каждая по отдельности
    выглядит рабочей.

    Обратный ход: верни в pinned_messages вызов _msg_out без карты вложений — падает
    утверждение про закреплённые."""
    from app import storage
    admin, (a_id, a), (b_id, b), (c_id, c) = _setup(client)
    monkeypatch.setattr(storage, "MODE", "s3")
    for k, v in (("ENDPOINT", "https://s3.example"), ("BUCKET", "gb"),
                 ("ACCESS_KEY", "key"), ("SECRET_KEY", "secret")):
        monkeypatch.setattr(storage, k, v)
    monkeypatch.setattr(storage, "head_object", lambda key: {})

    conv = client.post("/web/messenger/chats/group",
                       json={"title": "Пути"}, headers=a).json()["conversation_id"]
    att = client.post("/web/messenger/uploads/sign", headers=a,
                      json={"conversation_id": conv, "name": "план.pdf", "size": 10,
                            "mime": "application/pdf"}).json()["attachment_id"]
    client.post(f"/web/messenger/uploads/{att}/done", headers=a)
    mid = client.post(f"/web/messenger/chats/{conv}/messages", headers=a,
                      json={"body": "", "attachment_id": att}).json()["id"]

    #1. Лента.
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]
    assert any((x.get("attachment") or {}).get("name") == "план.pdf" for x in msgs), "лента"

    #2. Список чатов: сообщение состоит ТОЛЬКО из файла, строка не должна быть пустой.
    #⚠️ ПРОВЕРЯЕМ ДО ЗАКРЕПЛЕНИЯ: `pin` постит системное сообщение, и последним станет
    #оно — проверка после смотрела бы не на тот объект. Поймано на себе.
    chats = client.get("/web/messenger/chats", headers=a).json()["chats"]
    row = [ch for ch in chats if ch["conversation_id"] == conv][0]
    assert (row["last_message"].get("attachment") or {}).get("name") == "план.pdf",         "в списке чатов вложение потеряно — строка окажется пустой"

    #3. Закреплённые.
    client.post(f"/web/messenger/messages/{mid}/pin", headers=a)
    pinned = client.get(f"/web/messenger/chats/{conv}/pinned", headers=a).json()["pinned"]
    assert pinned and (pinned[0].get("attachment") or {}).get("name") == "план.pdf",         "в закреплённых вложение потеряно"


def test_storage_mode_is_chosen_by_the_machine_not_by_a_switch(monkeypatch):
    """Способ хранения выбирается САМ — в этом весь смысл (просьба Влада 25.08.2026).

    «Когда переедем, хранилище сразу будет большое — сделай, чтобы при переезде лишних
    настроек не делать.» Поэтому решает не человек, а машина:
      ключи S3 есть        → объектное хранилище;
      ключей нет, места    → храним у себя;
      ключей нет, места нет → честно выключено.

    ⚠️ Порог по свободному месту, а не тумблер: тумблер придётся вспомнить и
    переключить, а забудут ровно в день переезда — и «файлы почему-то не работают»
    будут искать в коде.

    Обратный ход: верни в `mode()` `return "off"` вместо ветки про место — падает
    утверждение про большую машину."""
    from app import storage

    monkeypatch.setattr(storage, "MODE", "auto")

    #1. Есть ключи S3 — работаем через объектное хранилище, место неважно.
    for k, v in (("ENDPOINT", "https://s3.example"), ("BUCKET", "gb"),
                 ("ACCESS_KEY", "key"), ("SECRET_KEY", "secret")):
        monkeypatch.setattr(storage, k, v)
    monkeypatch.setattr(storage, "free_bytes", lambda path="": 0)
    assert storage.mode() == "s3"

    #2. Ключей нет, места много — храним у себя. Ровно это должно случиться после
    #переезда на большую машину, БЕЗ единой новой настройки.
    monkeypatch.setattr(storage, "ENDPOINT", "")
    monkeypatch.setattr(storage, "free_bytes", lambda path="": 500 * 1024 ** 3)
    assert storage.mode() == "local", "на большой машине вложения не включились сами"
    assert storage.configured() is True

    #3. Ключей нет, места мало — выключено. Это боевой VPS: 2.9 ГБ свободно.
    monkeypatch.setattr(storage, "free_bytes", lambda path="": 3 * 1024 ** 3)
    assert storage.mode() == "off", "на тесной машине файлы включились — диск под угрозой"
    assert storage.configured() is False

    #4. Явный режим уважаем, но не притворяемся: `s3` без ключей — это «выключено»,
    #а не «готово». Иначе приняли бы файл и потеряли его.
    monkeypatch.setattr(storage, "MODE", "s3")
    monkeypatch.setattr(storage, "free_bytes", lambda path="": 500 * 1024 ** 3)
    assert storage.mode() == "off", "режим s3 без ключей выдал себя за рабочий"


def test_local_upload_link_is_signed_and_narrow(monkeypatch):
    """Локальная ссылка подписана и годится только для одного файла и действия.

    ⚠️ Без подписи это была бы дыра: `POST /uploads/local/att:<id>` угадывается, а «он
    же знает id» защитой не является.

    Обратный ход: сделай `local_token_ok` всегда True — падает всё ниже."""
    from app import storage
    monkeypatch.setattr(storage, "MODE", "local")

    t = storage.local_token("att:abc", "put", 300)
    assert storage.local_token_ok("att:abc", "put", t)
    assert not storage.local_token_ok("att:xyz", "put", t), "подпись подошла чужому файлу"
    assert not storage.local_token_ok("att:abc", "get", t), "подпись подошла другому действию"
    assert not storage.local_token_ok("att:abc", "put", "мусор"), "мусор прошёл как подпись"
    expired = storage.local_token("att:abc", "put", -10)
    assert not storage.local_token_ok("att:abc", "put", expired), "протухшая подпись прошла"

    #Имя файла на диске — только id: путь от человека сюда не попадает никогда.
    import os
    assert ".." not in os.path.basename(storage.local_path("../../etc/passwd"))
