"""
test_message_alive_predicate.py — «сообщение не удалено» проверяется ОДНОЙ формой
(12.09.2026).

🔥 НАЙДЕНО НАСТОЯЩИМ ДЕФЕКТОМ, и дефект был тихий. `Message.deleted_at` объявлен как
`Column(String, default="")` — у живого сообщения там ПУСТАЯ СТРОКА, а не NULL. Значит
фильтр `Message.deleted_at.is_(None)` не совпадает НИ С ОДНИМ живым сообщением: запрос
честно отрабатывает и возвращает пусто.

Так молча не работали ДВЕ вещи в продукте:
• «Пометить непрочитанным» (`chats.py`): `last` всегда None, эндпоинт отвечал
  `{"ok": true, "unread": 0}` и не делал ничего. Человек нажимал — и ничего не
  происходило, причём ответ был успешным;
• последнее сообщение и счётчик новых в очереди обращений (`moderation._inbox_state`) —
  ровно то, ради чего этот код и писался: в его докстринге стоит «обращения показывают
  последнее сообщение и число новых», и это не работало.

⚠️ Ни один прогон этого не ловил: запрос валиден, ошибок нет, а пустой результат
неотличим от «сообщений действительно нет».

⚠️ Обратный ход проверен: вернуть `is_(None)` в любом из мест — краснеет первый тест.
"""
from pathlib import Path

from app.security import hash_password

from conftest import make_admin

PKG = Path(__file__).resolve().parents[1] / "app" / "routers" / "messenger"


def test_nobody_filters_messages_with_is_none():
    """СВОЙСТВО по всему пакету, а не перечисление мест. Форма выглядит правильной
    («не удалено = нет метки»), поэтому её пишут снова и снова — и каждый раз получают
    запрос, который молча ничего не находит."""
    offenders = []
    for p in sorted(PKG.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.split("\n"), 1):
            if "deleted_at.is_(None)" in line and not line.strip().startswith("#"):
                offenders.append(f"{p.name}:{i}")
    assert offenders == [], (
        "фильтр deleted_at.is_(None) не совпадает ни с одним живым сообщением: "
        "у них пустая строка, а не NULL")


def test_mark_unread_actually_moves_the_mark(client):
    """Регрессия на первый из двух дефектов: кнопка отвечала «ок» и не делала ничего."""
    admin = make_admin(client)
    for login in ("bob", "carol"):
        r = client.post("/sync/push", json={"changes": {"users": [{
            "id": "stud:" + login, "role": "student", "login": login,
            "password_hash": hash_password("studpass1"), "full_name": login,
            "surname": login, "name": login, "group_name": "К-24",
        }]}}, headers=admin)
        assert r.status_code == 200, r.text
    bob = {"Authorization": "Bearer " + client.post(
        "/auth/login", json={"login": "bob", "password": "studpass1"}).json()["access_token"]}
    carol = {"Authorization": "Bearer " + client.post(
        "/auth/login", json={"login": "carol", "password": "studpass1"}).json()["access_token"]}

    conv = client.post("/web/messenger/chats/direct/stud:carol", headers=bob).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "раз"}, headers=bob)
    client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "два"}, headers=bob)
    #Кэрол прочитала всё.
    client.get(f"/web/messenger/chats/{conv}/messages", headers=carol)
    r = client.post(f"/web/messenger/chats/{conv}/unread", headers=carol)
    assert r.status_code == 200, r.text
    #Главное: метка сдвинулась, и беседа снова считается непрочитанной.
    chats = client.get("/web/messenger/chats", headers=carol).json()["chats"]
    mine = [c for c in chats if c["conversation_id"] == conv]
    assert mine and mine[0].get("unread", 0) > 0, mine


def test_the_support_inbox_shows_the_last_message(client):
    """Регрессия на второй дефект: очередь обращений показывала пустое «последнее
    сообщение» у каждого обращения, то есть ровно то, ради чего её и переписывали."""
    admin = make_admin(client)
    r = client.post("/sync/push", json={"changes": {"users": [{
        "id": "stud:bob", "role": "student", "login": "bob",
        "password_hash": hash_password("studpass1"), "full_name": "Боб",
        "surname": "Боб", "name": "Бобов", "group_name": "К-24",
    }]}}, headers=admin)
    assert r.status_code == 200, r.text
    bob = {"Authorization": "Bearer " + client.post(
        "/auth/login", json={"login": "bob", "password": "studpass1"}).json()["access_token"]}
    conv = client.get("/web/messenger/moderation", headers=bob).json()["conversation_id"]
    client.post(f"/web/messenger/chats/{conv}/messages",
                json={"body": "сломался вход"}, headers=bob)
    rows = client.get("/web/admin/messenger/conversations",
                      params={"kind": "moderation"}, headers=admin).json()["conversations"]
    assert rows, "обращение не попало в очередь"
    assert rows[0]["last_body"], "последнее сообщение обращения пустое"
