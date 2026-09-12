"""
test_vector_in_chat.py — Вектор в ЛЮБОЙ беседе: ответ личный и не оседает в переписке.

Раньше `/vector` работал только в «Избранном», и это было осознанно: Вектор скоупит данные
по СПРОСИВШЕМУ, поэтому его реплика в общей беседе показала бы соседям выборку, к которой
у них доступа нет. По решению Ярослава (27.08.2026) команда открыта везде, а ограничение
снято не «пометкой личного сообщения», а тем, что ответ ВООБЩЕ НЕ СТАНОВИТСЯ СООБЩЕНИЕМ.

🔒 ГЛАВНЫЙ ИНВАРИАНТ ФАЙЛА — `test_nothing_is_written_to_the_conversation`. Сообщения
выбираются из БД в двух десятках мест (лента, дельта, поиск, последнее в списке чатов,
счётчик непрочитанного, медиа, модерация). Пометить ответ «личным» значило бы не забыть
флаг в КАЖДОМ из них, и первая же забытая выборка — это не косметика, а показ чужих данных
всей группе. Здесь забыть нечего по построению, и тест держит именно это свойство: после
вопроса Вектору в беседе не появляется НИ ОДНОГО сообщения.

⚠️ Ответ модели мокается. Проверяем не качество ответа (это дело `test_vector_*`), а
границы: кто вправе спросить, что уходит наружу и что остаётся в базе.
"""
import pytest

from conftest import make_admin, make_teacher
from app.security import hash_password


@pytest.fixture(autouse=True)
def _stub_vector(monkeypatch):
    """Подменяем сам ответ Вектора: LLM в тестах не поднимаем, а поход в неё медленный."""
    #⚠️ Патчим ИМЯ В ПАКЕТЕ `app.routers.web`, а не в модуле `web.vector`: ручка берёт
    #функцию через `from ..web import answer_vector_question`, то есть через ре-экспорт
    #в `__init__.py`. Подмена в модуле-источнике проходит мимо — и тест молча проверяет
    #настоящий поход в Вектора вместо заглушки (поймано на первом же прогоне).
    from app.routers import web as web_pkg
    monkeypatch.setattr(web_pkg, "answer_vector_question",
                        lambda q, user, db, **kw: {"text": f"ответ на «{q}»", "intent": "grades"})


def _student(client, admin, login, name="Студент Студентов"):
    parts = name.split(" ", 1)
    r = client.post("/sync/push", json={"changes": {"users": [{
        "id": f"stud:{login}", "role": "student", "login": login,
        "password_hash": hash_password("studpass1"), "full_name": name,
        "surname": parts[0], "name": parts[1] if len(parts) > 1 else "",
        "group_name": "К-24",
    }]}}, headers=admin)
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"login": login, "password": "studpass1"})
    assert r.status_code == 200, r.text
    return f"stud:{login}", {"Authorization": f"Bearer {r.json()['access_token']}"}


def _group(client, owner, member_ids, title="Проект"):
    r = client.post("/web/messenger/chats/group",
                    json={"title": title, "member_ids": member_ids}, headers=owner)
    assert r.status_code == 200, r.text
    return r.json()["conversation_id"]


def _ask(client, conv, headers, question="какой у меня средний балл"):
    return client.post(f"/web/messenger/chats/{conv}/vector",
                       json={"question": question}, headers=headers)


def _messages(client, conv, headers):
    r = client.get(f"/web/messenger/chats/{conv}/messages", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["messages"]


def test_participant_gets_an_answer_in_a_group(client):
    """Команда работает в группе — то, чего раньше не было вовсе."""
    admin = make_admin(client)
    teacher = make_teacher(client, admin)
    bob_id, bob = _student(client, admin, "bob", "Боб Бобов")
    conv = _group(client, teacher, [bob_id])

    r = _ask(client, conv, bob)
    assert r.status_code == 200, r.text
    assert "ответ на" in r.json()["text"]


def test_works_in_a_direct_chat_too(client):
    """И в личном чате: раньше там `/vector` молчал так же, как в группе."""
    admin = make_admin(client)
    teacher = make_teacher(client, admin)
    bob_id, bob = _student(client, admin, "bob", "Боб Бобов")
    conv = client.post(f"/web/messenger/chats/direct/{bob_id}", headers=teacher).json()["conversation_id"]

    assert _ask(client, conv, bob).status_code == 200


def test_nothing_is_written_to_the_conversation(client):
    """🔒 ГЛАВНОЕ: ответ не становится сообщением — ни у спросившего, ни у соседей.

    Обратный ход к прежнему поведению: начни сохранять реплику Вектора в беседу (как это
    делает `_handle_vector_command` в «Избранном») — и тест покраснеет сразу у обоих."""
    admin = make_admin(client)
    teacher = make_teacher(client, admin)
    bob_id, bob = _student(client, admin, "bob", "Боб Бобов")
    carol_id, carol = _student(client, admin, "carol", "Кэрол Кэрова")
    conv = _group(client, teacher, [bob_id, carol_id])

    assert _messages(client, conv, bob) == []
    assert _ask(client, conv, bob).status_code == 200

    #Ни вопроса, ни ответа: в БД не появилось НИЧЕГО.
    assert _messages(client, conv, bob) == [], "ответ Вектора осел в переписке спросившего"
    assert _messages(client, conv, carol) == [], "ответ Вектора виден соседу по беседе"
    assert _messages(client, conv, teacher) == [], "ответ Вектора виден владельцу беседы"


def test_answer_never_reaches_the_search_or_the_chat_list(client):
    """Тот же инвариант с другой стороны: выборки, через которые утечка и произошла бы.

    Поиск по беседе и «последнее сообщение» в списке чатов — две отдельные выборки из
    `Message`. Если ответ когда-нибудь начнут сохранять, они покажут его соседу даже при
    отфильтрованной ленте."""
    admin = make_admin(client)
    teacher = make_teacher(client, admin)
    bob_id, bob = _student(client, admin, "bob", "Боб Бобов")
    carol_id, carol = _student(client, admin, "carol", "Кэрол Кэрова")
    conv = _group(client, teacher, [bob_id, carol_id])

    _ask(client, conv, bob, question="средний балл")

    found = client.get(f"/web/messenger/chats/{conv}/messages/search",
                       params={"q": "ответ"}, headers=carol)
    assert found.status_code == 200, found.text
    assert found.json().get("messages") == [], "ответ Вектора нашёлся поиском у соседа"

    chats = client.get("/web/messenger/chats", headers=carol).json()["chats"]
    row = next(c for c in chats if c["conversation_id"] == conv)
    assert row.get("last_message") is None, "ответ Вектора стал последним сообщением беседы"
    assert not row.get("unread"), "личный ответ Вектора зажёг соседу счётчик непрочитанного"


def test_outsider_cannot_ask_in_someone_elses_chat(client):
    """Не участник — 403. Иначе ручка стала бы способом узнать, что беседа существует."""
    admin = make_admin(client)
    teacher = make_teacher(client, admin)
    bob_id, _bob = _student(client, admin, "bob", "Боб Бобов")
    _carol_id, carol = _student(client, admin, "carol", "Кэрол Кэрова")
    conv = _group(client, teacher, [bob_id])

    assert _ask(client, conv, carol).status_code == 403


def test_empty_question_is_rejected(client):
    admin = make_admin(client)
    teacher = make_teacher(client, admin)
    bob_id, bob = _student(client, admin, "bob", "Боб Бобов")
    conv = _group(client, teacher, [bob_id])

    assert _ask(client, conv, bob, question="   ").status_code == 400


def test_globally_muted_user_cannot_ask(client):
    """Мьют модерацией закрывает и эту дверь.

    Иначе замьюченный сохранял бы доступ к самой дорогой операции беседы — походу в
    модель, — просто через другой эндпоинт."""
    admin = make_admin(client)
    teacher = make_teacher(client, admin)
    bob_id, bob = _student(client, admin, "bob", "Боб Бобов")
    conv = _group(client, teacher, [bob_id])

    #⚠️ Срок обязателен с 11.09.2026 — бессрочных мьютов сервер не выдаёт.
    r = client.post(f"/web/admin/messenger/users/{bob_id}/mute",
                    json={"muted": True, "hours": 1}, headers=admin)
    assert r.status_code == 200, r.text
    assert _ask(client, conv, bob).status_code == 403


def test_saved_chat_keeps_its_own_behaviour(client):
    """«Избранное» не тронуто: там разговор с Вектором по-прежнему СОХРАНЯЕТСЯ.

    Сторож против «унифицировали и заодно сломали»: в личных заметках история полезна и
    скрывать её не от кого — собеседника там нет."""
    admin = make_admin(client)
    _teacher = make_teacher(client, admin)
    _bob_id, bob = _student(client, admin, "bob", "Боб Бобов")

    conv = client.post("/web/messenger/chats/saved", headers=bob).json()["conversation_id"]
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "/vector средний балл"}, headers=bob)
    assert r.status_code == 200, r.text

    bodies = [m["body"] for m in _messages(client, conv, bob)]
    assert any("/vector" in b for b in bodies), "вопрос пропал из «Избранного»"
    assert any("ответ на" in b for b in bodies), "ответ Вектора перестал сохраняться в «Избранном»"
