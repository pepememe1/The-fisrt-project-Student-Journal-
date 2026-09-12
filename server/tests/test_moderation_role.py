"""
test_moderation_role.py — роль `moderator`, ограничение СО СРОКОМ, блокировка и жалобы
на профиль (заход 11.09.2026).

⚠️ У КАЖДОЙ ПРОВЕРКИ ЗДЕСЬ ЕСТЬ ОБРАТНЫЙ ХОД, и это не формальность: сторож, который
зелен и без починки, неотличим от исправного кода и хуже отсутствующей проверки. Там, где
обратный ход невозможен без правки продукта, он назван словами в докстринге.

Что именно проверяется и почему это важно:
• модератор НЕ проходит `require_admin` — иначе человек, заведённый разбирать жалобы,
  получил бы управление журналом всего колледжа, причём молча;
• мьют ИСТЕКАЕТ сам, и истёкшая строка УДАЛЯЕТСЯ — иначе список модерации и барьер
  записи показывали бы разное;
• замьюченный ПИШЕТ в чат с модерацией — наказание без возможности обжаловать это тупик;
• модератор не видит человека, на которого нет жалобы, — граница проведена отсутствием
  данных, а не проверкой на экране;
• по закрытому тикету наказать нельзя — на СЕРВЕРЕ, а не погашенной кнопкой.
"""
from datetime import datetime, timedelta, timezone

from conftest import make_admin, make_teacher
from app.security import hash_password


def _make_user(client, admin_headers, uid, login, role, name):
    """Завести пользователя ЛЮБОЙ роли напрямую через синк (как и соседние тесты)."""
    r = client.post("/sync/push", json={"changes": {"users": [{
        "id": uid, "role": role, "login": login,
        "password_hash": hash_password("passw0rd1"), "full_name": name,
        "surname": name.split(" ")[0], "name": name.split(" ")[-1],
    }]}}, headers=admin_headers)
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"login": login, "password": "passw0rd1"})
    assert r.status_code == 200, r.text
    return uid, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _student(client, admin_headers, login, name):
    uid = f"stud:{login}"
    parts = name.split(" ", 1)
    r = client.post("/sync/push", json={"changes": {"users": [{
        "id": uid, "role": "student", "login": login,
        "password_hash": hash_password("studpass1"), "full_name": name,
        "surname": parts[0], "name": parts[1] if len(parts) > 1 else "",
        "group_name": "К-24",
    }]}}, headers=admin_headers)
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"login": login, "password": "studpass1"})
    return uid, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _setup(client):
    admin = make_admin(client)
    mod_id, mod = _make_user(client, admin, "mod:moderator", "moderator",
                             "moderator", "Модерация Дежурная")
    b_id, b = _student(client, admin, "bob", "Боб Бобов")
    c_id, c = _student(client, admin, "carol", "Кэрол Кэрова")
    return admin, (mod_id, mod), (b_id, b), (c_id, c)


def _direct(client, headers, peer_id):
    r = client.post(f"/web/messenger/chats/direct/{peer_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["conversation_id"]


# ── Роль: что открыто и что закрыто ──────────────────────────────────────────────────
def test_moderator_reaches_moderation_but_not_admin_endpoints(client):
    """Главная проверка захода: модератор — НЕ администратор.

    Обратный ход: поменять в `mod_router` дверь обратно на `require_admin` — покраснеет
    первая половина; добавить "moderator" в `require_admin` — покраснеет вторая.
    """
    admin, (_, mod), _, _ = _setup(client)
    #Очередь жалоб ему открыта.
    assert client.get("/web/admin/messenger/reports?status=open", headers=mod).status_code == 200
    #А административные разделы — нет, и это ровно то, ради чего заведена отдельная дверь.
    #⚠️ Адреса взяты СУЩЕСТВУЮЩИЕ (сверены с `app.router`): тест, стучащийся в опечатанный
    #путь, получил бы 404 и зеленел бы, ничего не проверяя, — наш записанный урок про
    #заглушку SPA.
    for path in ("/web/admin/groups", "/web/admin/audit", "/web/admin/audit/integrity",
                 "/web/admin/ai-config", "/web/admin/data/datasets", "/web/admin/invites"):
        assert client.get(path, headers=mod).status_code == 403, path


def test_ordinary_roles_still_cannot_moderate(client):
    """Студент и преподаватель в модерацию не попадают — дверь расширена, а не открыта."""
    admin, _, (_, b), _ = _setup(client)
    teacher = make_teacher(client, admin)
    assert client.get("/web/admin/messenger/reports", headers=b).status_code == 403
    assert client.get("/web/admin/messenger/reports", headers=teacher).status_code == 403


# ── Срок ограничения ─────────────────────────────────────────────────────────────────
def test_mute_requires_a_term(client):
    """Бессрочное ограничение больше не выдаётся.

    Обратный ход: убрать проверку `total <= 0` в `_mute_minutes` — тест покраснеет.
    """
    admin, _, (b_id, _), _ = _setup(client)
    r = client.post(f"/web/admin/messenger/users/{b_id}/mute", json={"muted": True}, headers=admin)
    assert r.status_code == 400
    r = client.post(f"/web/admin/messenger/users/{b_id}/mute",
                    json={"muted": True, "days": 400}, headers=admin)
    assert r.status_code == 400, "срок больше года обязан отвергаться"


def test_mute_expires_by_itself_and_the_row_disappears(client):
    """🔥 Ограничение снимается САМО, и строка в базе исчезает.

    Проверяются ОБЕ половины. Если бы истёкшая строка оставалась, писать человек бы уже
    мог, а список модерации показывал бы его наказанным — два состояния вместо одного.
    Обратный ход: убрать `db.delete(row)` из `_mute_row` — покраснеет вторая половина.
    """
    from app.db import SessionLocal
    from app.models import MutedUser

    admin, _, (b_id, b), (c_id, _) = _setup(client)
    conv = _direct(client, b, c_id)
    client.post(f"/web/admin/messenger/users/{b_id}/mute",
                json={"muted": True, "hours": 1, "reason": "проверка"}, headers=admin)
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "нельзя"}, headers=b).status_code == 403

    #Отматываем срок в прошлое — то же, что подождать час, только без ожидания.
    db = SessionLocal()
    row = db.query(MutedUser).filter(MutedUser.user_id == b_id).first()
    row.muted_until = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db.commit()
    db.close()

    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "снова можно"}, headers=b).status_code == 200
    db = SessionLocal()
    assert db.query(MutedUser).filter(MutedUser.user_id == b_id).first() is None, \
        "истёкшая строка обязана удаляться, иначе список модерации покажет наказанным того, кто уже пишет"
    db.close()


def test_muted_user_can_still_reach_moderation(client):
    """🔥 Замьюченный обязан иметь возможность обжаловать.

    До этого захода мьют глушил ВСЁ, включая беседу `mod:{id}`, — то есть наказанный не мог
    спросить «за что». Обратный ход: убрать ветку `conv.kind == "moderation"` из
    `_guard_can_write` — тест покраснеет.
    """
    admin, _, (b_id, b), (c_id, _) = _setup(client)
    conv = _direct(client, b, c_id)
    mod_chat = client.get("/web/messenger/moderation", headers=b).json()["conversation_id"]
    client.post(f"/web/admin/messenger/users/{b_id}/mute",
                json={"muted": True, "hours": 2, "reason": "оскорбления"}, headers=admin)

    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "обычный чат"}, headers=b).status_code == 403
    assert client.post(f"/web/messenger/chats/{mod_chat}/messages",
                       json={"body": "за что меня ограничили?"}, headers=b).status_code == 200


def test_restriction_tells_the_person_the_term_and_the_reason(client):
    """Человек видит СРОК и ПРИЧИНУ, а не глухое «вы ограничены».

    Иначе единственным его действием становится обращение в поддержку, которое разбирает
    та же модерация: наказание само себе создаёт работу.
    """
    admin, _, (b_id, b), _ = _setup(client)
    assert client.get("/web/messenger/my-restriction", headers=b).json()["muted"] is False
    client.post(f"/web/admin/messenger/users/{b_id}/mute",
                json={"muted": True, "hours": 3, "reason": "спам"}, headers=admin)
    data = client.get("/web/messenger/my-restriction", headers=b).json()
    assert data["muted"] is True
    assert data["reason"] == "спам"
    assert data["muted_until"], "срок обязан приезжать клиенту — по нему считается отсчёт"


def test_moderator_cannot_mute_moderation_or_self(client):
    """Модераторы не глушат друг друга и себя."""
    admin, (mod_id, mod), _, _ = _setup(client)
    #Себя.
    assert client.post(f"/web/admin/messenger/users/{mod_id}/mute",
                       json={"muted": True, "hours": 1}, headers=mod).status_code == 400
    #Другого модератора (админ — тоже модерация). Его id детерминирован: `admin:{login}`
    #по той же конвенции префиксов, что `stud:`/`teach:` (см. §10 CLAUDE.md).
    assert client.post("/web/admin/messenger/users/admin:admin/mute",
                       json={"muted": True, "hours": 1}, headers=mod).status_code == 400


def test_closed_ticket_blocks_punishment_on_the_server(client):
    """🔒 По закрытому тикету наказать нельзя — проверкой СЕРВЕРА, а не погашенной кнопкой.

    Дизейбл во фронте обходится прямым запросом; поэтому правило живёт в
    `_require_live_ticket`, общем с просмотром переписки.
    Обратный ход: убрать вызов `_require_live_ticket` из `mod_mute_user` — тест покраснеет.
    """
    admin, _, (b_id, b), (c_id, c) = _setup(client)
    conv = _direct(client, b, c_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages",
                      json={"body": "грубость"}, headers=b).json()["id"]
    rid = client.post("/web/messenger/reports",
                      json={"message_id": mid, "reason_code": "harassment"},
                      headers=c).json()["report_id"]
    #Пока тикет живой — наказать можно.
    assert client.post(f"/web/admin/messenger/users/{b_id}/mute",
                       json={"muted": True, "hours": 1, "report_id": rid},
                       headers=admin).status_code == 200
    client.post(f"/web/admin/messenger/users/{b_id}/mute", json={"muted": False}, headers=admin)
    #Закрыли — и по нему больше ничего.
    client.post(f"/web/admin/messenger/reports/{rid}/resolve",
                json={"status": "resolved"}, headers=admin)
    assert client.post(f"/web/admin/messenger/users/{b_id}/mute",
                       json={"muted": True, "hours": 1, "report_id": rid},
                       headers=admin).status_code == 403
    #А просмотр переписки по нему закрыт тем же правилом.
    assert client.get(f"/web/admin/messenger/conversations/{conv}/messages?report_id={rid}",
                      headers=admin).status_code == 403


# ── Блокировка человек↔человек ───────────────────────────────────────────────────────
def test_block_stops_messages_in_both_directions(client):
    """Блокировка запрещает переписку ОБОИМ.

    Односторонний запрет дал бы заблокировавшему канал, в котором нельзя ответить.
    Обратный ход: оставить в `_blocked_between` только одно направление — покраснеет
    вторая половина теста.
    """
    admin, _, (b_id, b), (c_id, c) = _setup(client)
    conv = _direct(client, b, c_id)
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "привет"}, headers=b).status_code == 200

    assert client.post(f"/web/messenger/users/{b_id}/block",
                       json={"blocked": True}, headers=c).status_code == 200
    #Заблокированный не пишет…
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "ещё раз"}, headers=b).status_code == 403
    #…и заблокировавший тоже: односторонняя переписка хуже для обоих.
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "а я могу"}, headers=c).status_code == 403

    client.post(f"/web/messenger/users/{b_id}/block", json={"blocked": False}, headers=c)
    assert client.post(f"/web/messenger/chats/{conv}/messages",
                       json={"body": "снова можно"}, headers=b).status_code == 200


def test_block_refusal_never_says_it_was_a_block(client):
    """Отказ НЕ называет причину: иначе блокировка становится способом сообщить человеку,
    что он неприятен. Проверяем текст, потому что именно он и есть решение."""
    admin, _, (b_id, b), (c_id, c) = _setup(client)
    conv = _direct(client, b, c_id)
    client.post(f"/web/messenger/users/{b_id}/block", json={"blocked": True}, headers=c)
    detail = client.post(f"/web/messenger/chats/{conv}/messages",
                         json={"body": "?"}, headers=b).json()["detail"]
    assert "заблок" not in str(detail).lower(), "отказ не имеет права раскрывать блокировку"


def test_moderation_cannot_be_blocked(client):
    """Администрацию и модерацию заблокировать нельзя — иначе нарушитель закрывает себе
    канал, по которому с ним и разговаривают о нарушении."""
    admin, (mod_id, _), _, (c_id, c) = _setup(client)
    assert client.post(f"/web/messenger/users/{mod_id}/block",
                       json={"blocked": True}, headers=c).status_code == 400


# ── Жалобы на профиль и граница видимости людей ──────────────────────────────────────
def test_profile_report_snapshots_the_field_on_the_server(client):
    """Снимок делает СЕРВЕР: присланный клиентом — это текст, который пишет жалующийся."""
    admin, (_, mod), (b_id, b), (c_id, c) = _setup(client)
    client.post("/me/prefs", json={"prefs": {"bio": "оскорбительное описание"}}, headers=b)
    r = client.post("/web/messenger/user-reports",
                    json={"user_id": b_id, "reason_code": "harassment", "field": "bio",
                          "snapshot": "подсунутый текст", "description": "смотрите"},
                    headers=c)
    assert r.status_code == 200, r.text
    rows = client.get("/web/admin/messenger/user-reports?status=open", headers=mod).json()["reports"]
    assert rows and rows[0]["snapshot"] == "оскорбительное описание"


def test_moderator_sees_only_people_with_complaints(client):
    """🔒 Список людей — не каталог колледжа.

    Обратный ход: убрать фильтр по жалобам в `mod_users` — покраснеет первая половина.
    """
    admin, (_, mod), (b_id, b), (c_id, c) = _setup(client)
    assert client.get("/web/admin/messenger/users", headers=mod).json()["users"] == []
    client.post("/web/messenger/user-reports",
                json={"user_id": b_id, "reason_code": "spam"}, headers=c)
    users = client.get("/web/admin/messenger/users", headers=mod).json()["users"]
    assert [u["id"] for u in users] == [b_id]
    assert users[0]["reports_open"] == 1


def test_profile_cleanup_needs_an_open_complaint(client):
    """Правка чужого профиля без повода — это доступ к чужому аккаунту, а не модерация."""
    admin, (_, mod), (b_id, b), (c_id, c) = _setup(client)
    client.post("/me/prefs", json={"prefs": {"bio": "текст"}}, headers=b)
    #Без жалобы — отказ.
    assert client.post(f"/web/admin/messenger/users/{b_id}/profile",
                       json={"clear": ["bio"]}, headers=mod).status_code == 403
    client.post("/web/messenger/user-reports",
                json={"user_id": b_id, "reason_code": "harassment", "field": "bio"}, headers=c)
    assert client.post(f"/web/admin/messenger/users/{b_id}/profile",
                       json={"clear": ["bio"]}, headers=mod).status_code == 200
    assert client.get("/me/prefs", headers=b).json().get("prefs", {}).get("bio", "") == ""


def test_profile_cleanup_can_only_clear_not_write(client):
    """Поле только ОЧИЩАЕТСЯ: вписать что-то от имени человека нельзя по построению —
    под текстом стоит его лицо и фамилия. Проверяем, что произвольное значение не
    принимается вовсе (сервер знает только список полей)."""
    admin, (_, mod), (b_id, b), (c_id, c) = _setup(client)
    client.post("/web/messenger/user-reports",
                json={"user_id": b_id, "reason_code": "harassment"}, headers=c)
    #ФИО в список разрешённых не входит — значит и тронуть его нечем: 400, и имя цело.
    #Имя читаем из каталога мессенджера — отдельной ручки «мой профиль» у продукта нет.
    r = client.post(f"/web/admin/messenger/users/{b_id}/profile",
                    json={"clear": ["full_name"]}, headers=mod)
    assert r.status_code == 400
    found = client.get("/web/admin/messenger/users", headers=mod).json()["users"]
    assert [u["full_name"] for u in found if u["id"] == b_id] == ["Боб Бобов"]


def test_block_cannot_be_bypassed_by_forwarding(client):
    """🔥 ПЕРЕСЫЛКА ОБХОДИЛА БЛОКИРОВКУ — найдено перепроверкой, а не тестом.

    `send_message` блокировку уважал, а `forward_messages` нет: участие в беседе у
    заблокированного остаётся, и «переслать» клало в ту же личку что угодно. Правило было
    расписано у одного потребителя из двух — наш класс «первое забытое место».

    Обратный ход: убрать вызов `_guard_direct_write` из `forward_messages` — покраснеет.
    """
    admin, _, (b_id, b), (c_id, c) = _setup(client)
    conv = _direct(client, b, c_id)
    #Своё сообщение в другой беседе, которое потом попробуем переслать.
    own = client.post("/web/messenger/chats/saved", headers=b)
    saved = own.json()["conversation_id"]
    mid = client.post(f"/web/messenger/chats/{saved}/messages",
                      json={"body": "заметка"}, headers=b).json()["id"]

    client.post(f"/web/messenger/users/{b_id}/block", json={"blocked": True}, headers=c)
    r = client.post("/web/messenger/messages/forward",
                    json={"message_ids": [mid], "to_conversation_ids": [conv]}, headers=b)
    assert r.status_code == 403, "пересылка обошла блокировку"

    #И обратная сторона: в СВОЁ «Избранное» пересылать по-прежнему можно — блокировка
    #касается только личной беседы с конкретным человеком.
    assert client.post("/web/messenger/messages/forward",
                       json={"message_ids": [mid], "to_conversation_ids": [saved]},
                       headers=b).status_code == 200


def test_block_also_stops_reactions_and_pins(client):
    """Блокировка закрывает ВСЁ, что появляется у собеседника на экране.

    Реакция и закрепление — не сообщения, но видны немедленно: эмодзи под его строкой и
    системная запись о закреплении. Пропусти их — и блокировка дырява ровно в ту сторону,
    ради которой её ставят.
    Обратный ход: убрать `_guard_direct_write` из `add_reaction`/`pin_message` — краснеет.
    """
    admin, _, (b_id, b), (c_id, c) = _setup(client)
    conv = _direct(client, b, c_id)
    mid = client.post(f"/web/messenger/chats/{conv}/messages",
                      json={"body": "до блокировки"}, headers=c).json()["id"]
    #До блокировки и реакция, и закрепление доступны.
    assert client.post(f"/web/messenger/messages/{mid}/reactions",
                       json={"emoji": "👍"}, headers=b).status_code == 200

    client.post(f"/web/messenger/users/{b_id}/block", json={"blocked": True}, headers=c)
    assert client.post(f"/web/messenger/messages/{mid}/reactions",
                       json={"emoji": "🔥"}, headers=b).status_code == 403
    assert client.post(f"/web/messenger/messages/{mid}/pin",
                       headers=b).status_code == 403


def test_moderator_uses_the_ordinary_messenger_but_sees_no_extra_people(client):
    """Модератор пользуется обычным мессенджером — отвечать в обращение его работа.

    ⚠️ И при этом НЕ получает расширенного каталога: в поиске людей он видит ровно то же,
    что преподаватель. Полномочия дают только ручки `mod_router`; обычные ручки роль
    модератора ничем не выделяют, и это правильно — иначе «зашёл посмотреть» стало бы
    доступом к колледжу мимо всякого тикета.
    """
    admin, (_, mod), (b_id, b), (c_id, c) = _setup(client)
    #Обычный каталог ему открыт как всем.
    r = client.get("/web/messenger/users?role=student", headers=mod)
    assert r.status_code == 200
    #…и состояние мьюта чужих аккаунтов в нём НЕ раскрывается (это админ-контекст).
    assert all(u["muted"] is False for u in r.json()["users"])
    #Личную беседу он открывает как обычный человек.
    assert client.post(f"/web/messenger/chats/direct/{b_id}", headers=mod).status_code == 200


def test_moderator_cannot_reach_student_or_teacher_data(client):
    """Журнала, оценок и расписания у модератора нет — их ручки требуют своих ролей.

    Это и есть обещанная граница: разделы не «спрятаны в меню», их просто некому открыть.
    """
    admin, (_, mod), _, _ = _setup(client)
    #⚠️ Адреса СВЕРЕНЫ с `app.router`, и ждём именно 403, а не «403 или 404».
    #Первая версия этого теста пускала и 404 — и один адрес в ней оказался выдуманным
    #(`/web/teacher/assignments` не существует). Тест зеленел, ничего не проверяя: ровно
    #тот случай, ради которого заглушка SPA отдаёт честный 404.
    #⚠️ Берём ручки БЕЗ обязательных параметров: `/web/teacher/journal` отвечает 422
    #(не хватает группы и предмета) ДО того, как дело дойдёт до роли — данных он при этом
    #не отдаёт, но и роль таким ответом не проверяется. Держать его здесь значило бы
    #засчитывать себе проверку, которой не было.
    for path in ("/web/student/overview", "/web/teacher/overview",
                 "/web/curator/groups"):
        code = client.get(path, headers=mod).status_code
        assert code == 403, "%s отдал %s вместо 403" % (path, code)
