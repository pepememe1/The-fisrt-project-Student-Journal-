"""
test_reply_quote.py — «ОТВЕТИТЬ С ЦИТАТОЙ»: выделенный кусок исходного сообщения.

Просьба Влада (05.09.2026): «при выделении текста и нажатии ПКМ выйдет меню как в
телеграм… при ответе цитатой будет как в телеграм, и при нажатии перекинет к выделенному
фрагменту и подсветит его».

Что здесь держится (и что покраснеет, если правку откатить):
  🔒 фрагмент обязан РЕАЛЬНО быть в оригинале. Без этой проверки в цитату вписываются
     слова, которых собеседник не писал, и выглядят они как его собственные — подделка,
     которую замечает только тот, кто пойдёт сверять исходное сообщение;
  • выделение через перенос строки — ЧЕСТНОЕ: браузер отдаёт перенос пробелом, и точное
     вхождение не совпало бы у совершенно нормального выделения;
  • цитата — СНИМОК: правка оригинала её не меняет (иначе ответ «нет, это не так» повисал
     бы под изменившимся текстом);
  • но удалению оригинала цитата УСТУПАЕТ: иначе «удалить у всех» обходится цитатой;
  • длинное выделение подрезается, а не ложится в базу вторым экземпляром сообщения.

⚠️ ОБРАТНЫЙ ХОД проверен: снять проверку вхождения в `_clean_quote` — краснеет
`test_a_quote_that_is_not_in_the_original_is_dropped`; убрать `_blank_quotes_of_deleted` —
краснеет `test_deleting_the_original_takes_the_quote_with_it`.
"""
from app.security import hash_password
from conftest import make_admin, make_teacher


def _student(client, admin, login, name="Боб Бобов"):
    uid = f"stud:{login}"
    surname, given = (name.split(" ", 1) + [""])[:2]
    r = client.post("/sync/push", json={"changes": {"users": [{
        "id": uid, "role": "student", "login": login,
        "password_hash": hash_password("studpass1"), "full_name": name,
        "surname": surname, "name": given, "group_name": "К-24",
    }]}}, headers=admin)
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"login": login, "password": "studpass1"})
    return uid, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _chat(client, body="Первая строка\nвторая строка разговора"):
    """admin + препод A + студент B, открытая личная беседа и одно сообщение от A."""
    admin = make_admin(client)
    a = make_teacher(client, admin)
    b_id, b = _student(client, admin, "bob")
    conv = client.post(f"/web/messenger/chats/direct/{b_id}", headers=a).json()["conversation_id"]
    src = client.post(f"/web/messenger/chats/{conv}/messages",
                      json={"body": body}, headers=a).json()
    return conv, a, b, src


def _reply(client, conv, headers, src_id, quote, body="Отвечаю"):
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": body, "reply_to_id": src_id, "reply_quote": quote},
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_selected_fragment_travels_with_the_reply(client):
    """Основной путь: выделил кусок — он и приехал в ответ, а не всё сообщение целиком."""
    conv, a, b, src = _chat(client)
    out = _reply(client, conv, b, src["id"], "вторая строка")
    assert out["reply_quote"] == "вторая строка"
    assert out["reply_to_id"] == src["id"]
    #И у собеседника в ленте — тот же кусок.
    hist = client.get(f"/web/messenger/chats/{conv}/messages", headers=a).json()["messages"]
    assert [m["reply_quote"] for m in hist if m["reply_to_id"]] == ["вторая строка"]


def test_a_quote_that_is_not_in_the_original_is_dropped(client):
    """🔒 Слова, которых собеседник не писал, цитатой не становятся.

    Цитата ведёт к оригиналу — то есть проверять её пошли бы по ссылке, которую поставил
    сам подделыватель. Значит проверка обязана стоять НА СЕРВЕРЕ, а не на доверии.
    """
    conv, a, b, src = _chat(client)
    out = _reply(client, conv, b, src["id"], "я согласен перевести деньги")
    assert out["reply_quote"] == "", "сервер принял выдуманную цитату"
    assert out["reply_to_id"] == src["id"], "сама связь ответа при этом теряться не должна"


def test_a_selection_across_a_line_break_is_honest(client):
    """Выделение мышью через перенос строки браузер отдаёт с пробелом вместо переноса.

    Точное вхождение здесь не совпало бы у совершенно нормального выделения, и человек
    получил бы «цитата пропала» без всякой причины.
    """
    conv, a, b, src = _chat(client)
    out = _reply(client, conv, b, src["id"], "строка вторая строка")
    assert out["reply_quote"] == "строка вторая строка"


def test_editing_the_original_does_not_rewrite_the_quote(client):
    """Цитата — СНИМОК. Иначе ответ «нет, это не так» повисает под изменившимся текстом."""
    conv, a, b, src = _chat(client)
    out = _reply(client, conv, b, src["id"], "вторая строка")
    r = client.patch(f"/web/messenger/messages/{src['id']}",
                     json={"body": "совсем другой текст"}, headers=a)
    assert r.status_code == 200, r.text
    hist = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    quoted = [m for m in hist if m["id"] == out["id"]][0]
    assert quoted["reply_quote"] == "вторая строка"


def test_deleting_the_original_takes_the_quote_with_it(client):
    """🔒 «Удалить у всех» не должно обходиться цитатой.

    Проверять это на клиенте мало: оригинал бывает старше подгруженной страницы (лента
    отдаёт по 50), и тогда клиенту нечего сверять.
    """
    conv, a, b, src = _chat(client)
    out = _reply(client, conv, b, src["id"], "вторая строка")
    r = client.delete(f"/web/messenger/messages/{src['id']}?scope=all", headers=a)
    assert r.status_code == 200, r.text
    hist = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    quoted = [m for m in hist if m["id"] == out["id"]][0]
    assert quoted["reply_quote"] == ""


def test_long_selection_is_trimmed(client):
    """Цитата — указатель на кусок разговора, а не его второй экземпляр в базе."""
    long_body = "а" * 900
    conv, a, b, src = _chat(client, body=long_body)
    out = _reply(client, conv, b, src["id"], long_body)
    assert 0 < len(out["reply_quote"]) <= 300


def test_plain_reply_still_has_no_quote(client):
    """Обычный ответ работает как раньше — пусто значит «цитируй начало оригинала»."""
    conv, a, b, src = _chat(client)
    r = client.post(f"/web/messenger/chats/{conv}/messages",
                    json={"body": "Просто ответ", "reply_to_id": src["id"]}, headers=b)
    assert r.status_code == 200, r.text
    assert r.json()["reply_quote"] == ""


def test_quoting_an_already_deleted_message_is_refused_at_write_time(client):
    """🔒 НЕ ПУСКАТЬ, А НЕ ПОДЧИЩАТЬ (возражение Полковника 06.09.2026).

    Тумбстоун тело сообщения НЕ стирает — оно остаётся в строке ради модерации. Поэтому
    проверка принадлежности фрагмента проходила успешно и цитата УЖЕ удалённого сообщения
    ложилась в базу; гашение при выдаче убирало её из ленты, но строка существовала и
    уезжала путями, которые гашения не зовут (превью списка чатов, очередь модерации).
    """
    conv, a, b, src = _chat(client)
    assert client.delete(f"/web/messenger/messages/{src['id']}?scope=all",
                         headers=a).status_code == 200
    out = _reply(client, conv, b, src["id"], "вторая строка")
    assert out["reply_quote"] == "", "цитата удалённого сообщения попала в базу"


def test_chat_list_preview_never_carries_a_quote(client):
    """Превью последнего сообщения идёт МИМО `_attach_rich_meta`, значит и мимо гашения.

    Список чатов рисует только текст (`utils/messagePreview.js`), то есть поле там не
    показывается вовсе — убрать лишнее дешевле и надёжнее, чем не забыть подчистить его
    в третьем месте.
    """
    conv, a, b, src = _chat(client)
    _reply(client, conv, b, src["id"], "вторая строка")
    chats = client.get("/web/messenger/chats", headers=b).json()["chats"]
    here = [c for c in chats if c["conversation_id"] == conv]
    assert here, "беседа обязана быть в списке"
    assert here[0]["last_message"]["reply_quote"] == ""
