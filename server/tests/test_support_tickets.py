"""
test_support_tickets.py — обращения в модерацию: автоответчик, темы, срочность, номер
модератора и «умное» закрытие (12.09.2026, требование Влада).

━━ ЧТО ЗДЕСЬ ЗАЩИЩАЕТСЯ ━━
Очередь поддержки ломается ТИХО: человек написал, тикета не завелось, и обращение просто
не существует ни для кого. Ни одна ошибка при этом не всплывает — сообщение-то отправлено.

🔴 И отдельно — дверь наружу из автоответчика. Робот, из которого нельзя выйти к человеку,
превращает поддержку в тупик ровно для тех случаев, которые в список тем не попали.

⚠️ ОБРАТНЫЙ ХОД ПРОВЕРЕН: убрать вызов `on_moderation_message` из отправки — краснеет
первый тест; убрать «human» из URGENT_CATEGORIES — краснеет тест срочности; снять
подстановку номера в `moderator_display_name` — краснеет тест представления.
"""
from datetime import datetime, timedelta, timezone

from app.security import hash_password

from conftest import make_admin


def _moderator(client, admin, login="moderator", password="moderpass1"):
    r = client.post("/web/admin/moderators",
                    json={"login": login, "password": password, "full_name": "Дежурный"},
                    headers=admin)
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _student(client, admin, login="bob"):
    r = client.post("/sync/push", json={"changes": {"users": [{
        "id": "stud:" + login, "role": "student", "login": login,
        "password_hash": hash_password("studpass1"), "full_name": "Боб Бобов",
        "surname": "Боб", "name": "Бобов", "group_name": "К-24",
    }]}}, headers=admin)
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"login": login, "password": "studpass1"})
    return "stud:" + login, {"Authorization": "Bearer " + r.json()["access_token"]}


def _open_chat(client, headers):
    r = client.get("/web/messenger/moderation", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["conversation_id"]


def _say(client, headers, conv, body):
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": body}, headers=headers)
    assert r.status_code == 200, r.text
    return r


def _messages(client, headers, conv):
    r = client.get(f"/web/messenger/chats/{conv}/messages", headers=headers)
    assert r.status_code == 200, r.text
    return r.json().get("messages", [])


def test_first_message_gets_the_autoresponder(client):
    """Главное свойство: человек написал — и сразу видит, что его услышали и чего от него
    хотят. Молчание в ответ читается как «сюда никто не смотрит»."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "у меня проблема")
    bodies = [m["body"] for m in _messages(client, bob, conv)]
    assert any("выберите тему" in b.lower() for b in bodies), bodies


def test_the_autoresponder_does_not_repeat_itself(client):
    """Повтор на каждое сообщение превращает чат в переписку с роботом, из которой
    уходят, не дождавшись."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "первое")
    _say(client, bob, conv, "второе")
    bodies = [m["body"] for m in _messages(client, bob, conv)]
    greetings = [b for b in bodies if "выберите тему" in b.lower()]
    assert len(greetings) == 1, bodies


def test_asking_for_a_human_opens_an_urgent_ticket_immediately(client):
    """🔴 ДВЕРЬ НАРУЖУ. Попросил человека — тикет заводится сразу и СРОЧНЫМ, без
    прохождения анкеты: список тем его случай уже не описывает."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите живого человека пожалуйста")
    mod = _moderator(client, admin)
    tickets = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert len(tickets) == 1, tickets
    assert tickets[0]["urgent"] is True
    assert tickets[0]["category"] == "human"


def test_other_category_is_urgent_too(client):
    """«Другое» означает, что наш список не подошёл, — значит дальше выбирать нечего."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "здравствуйте")
    r = client.post("/web/messenger/moderation/category", json={"code": "other"}, headers=bob)
    assert r.status_code == 200, r.text
    assert r.json()["urgent"] is True


def test_an_ordinary_category_is_not_urgent(client):
    """Иначе срочными станут все, и пометка перестанет что-либо значить."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "здравствуйте")
    r = client.post("/web/messenger/moderation/category", json={"code": "spam"}, headers=bob)
    assert r.status_code == 200 and r.json()["urgent"] is False, r.text


def test_unknown_category_is_refused(client):
    """Список закрытый: иначе в очередь приезжает произвольная строка от клиента, и
    фильтр по теме перестаёт что-либо значить."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    _open_chat(client, bob)
    r = client.post("/web/messenger/moderation/category", json={"code": "вымысел"}, headers=bob)
    assert r.status_code == 400, r.text


def test_urgent_tickets_stand_at_the_very_top(client):
    """Порядок задаёт СЕРВЕР: очередь читают разные экраны, и «срочное наверху» обязано
    означать одно и то же на всех."""
    admin = make_admin(client)
    _, bob = _student(client, admin, "bob")
    _, carol = _student(client, admin, "carol")
    cb = _open_chat(client, bob)
    _say(client, bob, cb, "здравствуйте")
    client.post("/web/messenger/moderation/category", json={"code": "spam"}, headers=bob)
    cc = _open_chat(client, carol)
    _say(client, carol, cc, "позовите человека")
    mod = _moderator(client, admin)
    tickets = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert len(tickets) == 2, tickets
    assert tickets[0]["urgent"] is True, "срочное обращение не первое в очереди"


def test_claiming_introduces_the_moderator_by_number(client):
    """🔢 Модератор представляется НОМЕРОМ. До этого человек разговаривал с роботом — эта
    реплика и есть момент, когда обращение становится разговором."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите человека")
    mod = _moderator(client, admin)
    tid = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"][0]["id"]
    r = client.post(f"/web/admin/messenger/support/{tid}/claim", headers=mod)
    assert r.status_code == 200, r.text
    bodies = [m["body"] for m in _messages(client, bob, conv)]
    assert any("Модератор №1" in b and "Чем могу помочь" in b for b in bodies), bodies


def test_the_user_never_sees_the_moderator_real_name(client):
    """🔒 Получивший ограничение не должен уносить из чата фамилию того, кто его выдал:
    иначе разговор продолжится в коридоре, а не в тикете."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите человека")
    mod = _moderator(client, admin)
    tid = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"][0]["id"]
    client.post(f"/web/admin/messenger/support/{tid}/claim", headers=mod)
    msgs = _messages(client, bob, conv)
    blob = str(msgs)
    assert "Дежурный" not in blob, "в переписку утекло настоящее имя модератора"
    assert "Модератор №1" in blob


def test_claiming_twice_does_not_greet_twice(client):
    """Иначе модератор, вернувшийся в тикет, здоровается заново — как будто разговора не было."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите человека")
    mod = _moderator(client, admin)
    tid = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"][0]["id"]
    client.post(f"/web/admin/messenger/support/{tid}/claim", headers=mod)
    client.post(f"/web/admin/messenger/support/{tid}/claim", headers=mod)
    bodies = [m["body"] for m in _messages(client, bob, conv)]
    assert len([b for b in bodies if "Чем могу помочь" in b]) == 1, bodies


def test_a_taken_ticket_is_not_stolen_by_another_moderator(client):
    """Два модератора в одном обращении — это два разных ответа человеку на один вопрос."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите человека")
    m1 = _moderator(client, admin, "mod1")
    m2 = _moderator(client, admin, "mod2", "moderpass2")
    tid = client.get("/web/admin/messenger/support", headers=m1).json()["tickets"][0]["id"]
    assert client.post(f"/web/admin/messenger/support/{tid}/claim", headers=m1).status_code == 200
    r = client.post(f"/web/admin/messenger/support/{tid}/claim", headers=m2)
    assert r.status_code == 409, r.text


def test_closing_tells_the_person_about_it(client):
    """⚠️ Молчаливое закрытие — худший вариант: человек продолжает ждать ответа в чате,
    который для модерации уже не существует."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите человека")
    mod = _moderator(client, admin)
    tid = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"][0]["id"]
    client.post(f"/web/admin/messenger/support/{tid}/claim", headers=mod)
    r = client.post(f"/web/admin/messenger/support/{tid}/resolve",
                    json={"note": "разобрались"}, headers=mod)
    assert r.status_code == 200, r.text
    bodies = [m["body"] for m in _messages(client, bob, conv)]
    assert any("закрыто модерацией" in b.lower() for b in bodies), bodies


def test_writing_after_a_close_opens_a_new_ticket(client):
    """Закрытие не запирает человека: следующее сообщение заводит новый тикет, а
    переписка остаётся на месте."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите человека")
    mod = _moderator(client, admin)
    tid = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"][0]["id"]
    client.post(f"/web/admin/messenger/support/{tid}/resolve", json={}, headers=mod)
    _say(client, bob, conv, "снова нужна помощь, позовите человека")
    tickets = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert len(tickets) == 1 and tickets[0]["id"] != tid, tickets


def _age_conversation(conv: str, days: int):
    """Отмотать обращение на `days` суток назад, СОХРАНИВ ПОРЯДОК реплик и меток.

    🔥 ЭТА ФУНКЦИЯ САМА БЫЛА ДЕФЕКТНОЙ, И ЭТО СТОИТ ПОМНИТЬ. Первая версия не сдвигала
    метки, а СПЛЮЩИВАЛА их в одно значение — и `last_user_at` становился равен
    `claimed_at`. А весь разбираемый дефект жил ровно на том, что человек отвечает ПОЗЖЕ
    взятия в работу: с равными метками прежнее (сломанное) условие вело себя правильно, и
    тест оставался ЗЕЛЁНЫМ рядом с настоящим дефектом. Поймано обратным ходом, а не
    чтением. **Тест, стирающий измерение, по которому ломается продукт, не проверяет
    ничего** — тот же урок, что «ключ пары забыл про подгруппу».

    ⚠️ Сдвигаем ВСЁ: и сообщения, и `claimed_at`, и `last_user_at`. Двигать одну метку
    нельзя — срок молчания считается от ПОСЛЕДНЕГО ОТВЕТА МОДЕРАЦИИ, и тикет со свежим
    ответом закрываться не должен."""
    from app.db import SessionLocal
    from app.models import Message, SupportTicket
    back = timedelta(days=days)

    def shift(iso: str) -> str:
        if not iso:
            return iso
        return (datetime.fromisoformat(iso) - back).isoformat()

    db = SessionLocal()
    try:
        for m in db.query(Message).filter(Message.conversation_id == conv).all():
            m.created_at = shift(m.created_at or "")
        for r in db.query(SupportTicket).filter(SupportTicket.conversation_id == conv).all():
            r.claimed_at = shift(r.claimed_at or "")
            r.last_user_at = shift(r.last_user_at or "")
        db.commit()
    finally:
        db.close()


def test_a_silent_ticket_closes_itself(client):
    """«Умное» закрытие: разобранное обращение не имеет права висеть в очереди вечно —
    очередь, которую не разгребают, перестают просматривать."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите человека")
    mod = _moderator(client, admin)
    tid = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"][0]["id"]
    client.post(f"/web/admin/messenger/support/{tid}/claim", headers=mod)
    _age_conversation(conv, 60)              #и ответ модерации, и метки — за границей срока
    tickets = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert tickets == [], "тикет без ответа не закрылся сам"


def test_autoclose_works_after_a_real_exchange(client):
    """🔥 ГЛАВНЫЙ СЛУЧАЙ, И ИМЕННО ОН НЕ РАБОТАЛ (нашёл Полковник 12.09.2026).

    Обычный разобранный тикет: модератор взял, поздоровался, человек ответил, модератор
    разобрал — и человек замолчал. Прежнее условие сравнивало метку человека с моментом
    ВЗЯТИЯ, а `claim` сам пишет в чат приветствие: человек почти всегда отвечает ПОЗЖЕ
    взятия, значит `last_user_at > claimed_at` истинно НАВСЕГДА и тикет висел вечно.
    То есть автозакрытие не срабатывало ровно там, ради чего написано, а зелёным
    оставался только искусственный случай «человек не ответил на приветствие»."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите человека")
    mod = _moderator(client, admin)
    tid = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"][0]["id"]
    client.post(f"/web/admin/messenger/support/{tid}/claim", headers=mod)
    _say(client, bob, conv, "вот что случилось")                       #человек ответил
    client.post(f"/web/admin/messenger/conversations/{conv}/reply",
                json={"body": "разобрались, вопрос закрыт"}, headers=mod)
    _age_conversation(conv, 60)              #с тех пор молчат обе стороны
    tickets = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert tickets == [], "разобранный тикет висит в очереди вечно"


def test_a_fresh_reply_never_closes_the_ticket_instantly(client):
    """Зеркальная половина того же дефекта: пока срок считался от ВЗЯТИЯ, тикет, взятый
    три недели назад, закрывался на первом же чтении очереди СРАЗУ после ответа модератора
    — человек получал «обращение закрыто» через минуту после ответа, не успев его
    прочитать."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите человека")
    mod = _moderator(client, admin)
    tid = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"][0]["id"]
    client.post(f"/web/admin/messenger/support/{tid}/claim", headers=mod)
    _age_conversation(conv, 60)              #взяли давно и давно молчали
    #А теперь модерация отвечает — СЕЙЧАС. Срок молчания начинается заново.
    client.post(f"/web/admin/messenger/conversations/{conv}/reply",
                json={"body": "извините за задержку, отвечаю"}, headers=mod)
    tickets = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert len(tickets) == 1, "свежий ответ модерации закрыл обращение мгновенно"


def test_autoclose_never_hides_the_moderations_own_debt(client):
    """⚠️ Закрываем ТОЛЬКО те, где последним говорила модерация. Если последним написал
    человек — молчит как раз модерация, и автозакрытие спрятало бы её же долг.

    Здесь всё обращение старше срока, и единственная причина оставить его открытым —
    ПОРЯДОК реплик: последним написал человек."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите человека")
    mod = _moderator(client, admin)
    tid = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"][0]["id"]
    client.post(f"/web/admin/messenger/support/{tid}/claim", headers=mod)
    #Отматываем ответ модерации за границу срока, а человек пишет ПОСЛЕ него.
    _age_conversation(conv, 60)
    _say(client, bob, conv, "жду ответа")          #последнее слово за человеком
    tickets = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert len(tickets) == 1, "закрыли обращение, на которое сама модерация не ответила"


def test_the_queue_is_closed_to_everyone_but_moderation(client):
    """Очередь обращений — чужие личные разговоры о конфликтах."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    assert client.get("/web/admin/messenger/support", headers=bob).status_code == 403


def test_numbers_are_reused_after_a_moderator_is_removed(client):
    """Номер удалённого освобождается: иначе счётчик растёт вместе с текучкой, и
    «Модератор №137» в колледже на двух модераторов читается как ошибка."""
    admin = make_admin(client)
    _moderator(client, admin, "mod1")
    rows = client.get("/web/admin/moderators", headers=admin).json()["moderators"]
    assert rows[0]["mod_number"] == 1
    assert client.delete("/web/admin/moderators/mod1", headers=admin).status_code == 200
    _moderator(client, admin, "mod2", "moderpass2")
    rows = client.get("/web/admin/moderators", headers=admin).json()["moderators"]
    assert rows[0]["mod_number"] == 1, rows


def test_a_plain_first_message_still_lands_in_the_queue(client):
    """🔥 ГЛАВНОЕ СВОЙСТВО ОЧЕРЕДИ, и оно оплачено настоящим дефектом. Пока тикет заводился
    только по нажатию темы, человек, написавший «у меня проблема» и не выбравший ничего,
    ПРОПАДАЛ из очереди целиком: сообщение отправлено, ошибок нет, тикета нет — ровно тот
    тихий отказ, против которого очередь и заведена.

    ⚠️ Тема — СОСТОЯНИЕ обращения, а не условие его существования."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "у меня проблема")
    mod = _moderator(client, admin)
    tickets = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert len(tickets) == 1, tickets
    assert tickets[0]["urgent"] is False, "обычное обращение стало срочным"
    assert tickets[0]["category"] == "", tickets
    #Пустая подпись в очереди читается как поломка выдачи, а не как «ещё не выбрал».
    assert tickets[0]["category_label"], tickets


def test_picking_a_theme_does_not_open_a_second_ticket(client):
    """У человека одно обращение, и два тикета на него разошлись бы по разным модераторам."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "здравствуйте")
    r = client.post("/web/messenger/moderation/category", json={"code": "spam"}, headers=bob)
    assert r.status_code == 200, r.text
    mod = _moderator(client, admin)
    tickets = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert len(tickets) == 1, tickets
    assert tickets[0]["category"] == "spam", tickets


def test_the_door_out_works_inside_an_open_ticket_too(client):
    """🔴 ДВЕРЬ НАРУЖУ НЕ ЗАКРЫВАЕТСЯ ПОСЛЕ АНКЕТЫ. Тупик чаще всего случается ПОСЛЕ выбора
    темы: человек честно ответил роботу, разговор никуда не едет — и если просьба позвать
    человека работает только первым сообщением, выбравший тему в ней заперт."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "здравствуйте")
    client.post("/web/messenger/moderation/category", json={"code": "spam"}, headers=bob)
    mod = _moderator(client, admin)
    before = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert before[0]["urgent"] is False, before
    _say(client, bob, conv, "это не помогает, позовите живого человека")
    after = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert len(after) == 1, "эскалация завела второй тикет на то же обращение"
    assert after[0]["urgent"] is True, "просьба позвать человека внутри тикета ничего не дала"
    bodies = [m["body"] for m in _messages(client, bob, conv)]
    assert any("зову человека" in b.lower() for b in bodies), bodies


def test_escalation_does_not_repeat_itself(client):
    """Уже срочное обращение не отвечает «зову человека» на каждое следующее сообщение:
    повтор читается как «тебя так и не услышали»."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "позовите человека")
    _say(client, bob, conv, "позовите человека ещё раз")
    bodies = [m["body"] for m in _messages(client, bob, conv)]
    acks = [b for b in bodies if "зову человека" in b.lower()]
    assert len(acks) == 1, bodies


def test_the_client_learns_the_state_of_my_own_request(client):
    """Клиент не имеет права решать «выбрана ли тема» памятью вкладки: обращение у человека
    ОДНО, а заходит он и с телефона, и с компьютера — вкладочная память показала бы кнопки
    заново тому, кто всё уже выбрал."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    r = client.get("/web/messenger/moderation/categories", headers=bob)
    assert r.status_code == 200, r.text
    assert r.json()["current"] is None, "обращения ещё нет, а состояние есть"
    _say(client, bob, conv, "здравствуйте")
    cur = client.get("/web/messenger/moderation/categories", headers=bob).json()["current"]
    assert cur and cur["category"] == "", cur
    client.post("/web/messenger/moderation/category", json={"code": "spam"}, headers=bob)
    cur = client.get("/web/messenger/moderation/categories", headers=bob).json()["current"]
    assert cur and cur["category"] == "spam" and cur["category_label"], cur


def test_forwarding_into_the_moderation_chat_opens_a_ticket(client):
    """🔥 ПЕРЕСЫЛКА — ТОЖЕ ОБРАЩЕНИЕ (нашёл Полковник 12.09.2026).

    Самый естественный способ пожаловаться: человек видит оскорбление, открывает ⚙
    «Модерация», ПЕРЕСЫЛАЕТ туда сообщение и не пишет ни слова. Пока хук стоял только в
    `send_message`, тикета не заводилось: сообщение в чате есть, в очереди обращения нет,
    метка «человек написал» не двигается. Тот же тихий отказ, против которого очередь и
    заведена, и вход в него — одна кнопка.

    ⚠️ Проверяем и то, что тикет НЕ срочный: в пересланном тексте могут быть чужие слова
    вроде «позовите человека», и поднимать по ним срочность значило бы решать за
    пересылающего то, чего он не говорил."""
    admin = make_admin(client)
    _, bob = _student(client, admin, "bob")
    _, carol = _student(client, admin, "carol")
    #Обычная переписка, откуда берём сообщение.
    direct = client.post("/web/messenger/chats/direct/stud:carol", headers=bob).json()["conversation_id"]
    mid = client.post(f"/web/messenger/chats/{direct}/messages",
                      json={"body": "позовите живого человека, это грубость"},
                      headers=carol).json()["id"]
    conv = _open_chat(client, bob)
    r = client.post("/web/messenger/messages/forward",
                    json={"message_ids": [mid], "to_conversation_ids": [conv]}, headers=bob)
    assert r.status_code == 200, r.text
    mod = _moderator(client, admin)
    tickets = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"]
    assert len(tickets) == 1, "пересылка в чат модерации не завела обращение"
    assert tickets[0]["urgent"] is False, "чужие слова из пересылки подняли срочность"
    bodies = [m["body"] for m in _messages(client, bob, conv)]
    assert any("выберите тему" in b.lower() for b in bodies), bodies


def test_a_broken_queue_never_fails_the_message(client, monkeypatch):
    """Хук зовётся ПОСЛЕ commit: сбой очереди обращений не имеет права уронить уже
    отправленное человеком сообщение (то же правило, что у системных каналов оценок)."""
    from app.routers.messenger import moderation
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)

    def boom(*a, **kw):
        raise RuntimeError("очередь обращений сломалась")
    monkeypatch.setattr(moderation, "_post_system", boom)
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "у меня проблема"}, headers=bob)
    assert r.status_code == 200, r.text
    monkeypatch.undo()
    assert "у меня проблема" in [m["body"] for m in _messages(client, bob, conv)]


def test_the_swallowed_hook_also_rolls_the_session_back():
    """⚠️ ПРОГЛОТИТЬ ИСКЛЮЧЕНИЕ МАЛО — НАДО ВЕРНУТЬ СЕССИЮ В РАБОЧЕЕ СОСТОЯНИЕ
    (подозрение Полковника 12.09.2026, принято и закрыто).

    Внутри хука есть свои `commit`. Упавший коммит оставляет сессию «нужен откат», и
    следующее же обращение к объекту сообщения в той же ручке дало бы
    `PendingRollbackError`: человек получил бы 500 на сообщение, которое НА САМОМ ДЕЛЕ
    сохранено и разослано по сокету, а клиент оставил бы его неотправленным черновиком и
    послал второй раз.

    ⚠️ Проверка СТРУКТУРНАЯ, и граница названа честно: воспроизвести падение коммита в
    тестовой базе нечем, а сторож, который не может покраснеть, хуже отсутствующего.
    Здесь проверяется ровно то, что можно проверить, — что откат в коде есть."""
    import inspect

    from app.routers.messenger import messages as messages_mod
    src = inspect.getsource(messages_mod._hook_moderation)
    body = src.split("except Exception", 1)
    assert len(body) == 2, "хук больше не глушит исключение — проверь правило заново"
    assert "db.rollback()" in body[1], (
        "в except нет rollback: сессия останется сломанной для остатка ручки")


def test_a_closed_request_stops_being_the_current_one(client):
    """Закрытое обращение больше не состояние человека — иначе кнопки тем не вернутся
    никогда, и следующее обращение придётся описывать словами заново."""
    admin = make_admin(client)
    _, bob = _student(client, admin)
    conv = _open_chat(client, bob)
    _say(client, bob, conv, "здравствуйте")
    client.post("/web/messenger/moderation/category", json={"code": "spam"}, headers=bob)
    mod = _moderator(client, admin)
    tid = client.get("/web/admin/messenger/support", headers=mod).json()["tickets"][0]["id"]
    client.post(f"/web/admin/messenger/support/{tid}/resolve", json={}, headers=mod)
    cur = client.get("/web/messenger/moderation/categories", headers=bob).json()["current"]
    assert cur is None, cur
