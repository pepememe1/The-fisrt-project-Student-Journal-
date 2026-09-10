"""
test_activities.py — «Активности» в беседах (docs/done/PLAN-ACTIVITIES.md §11).

Проверяем не «эндпоинт отвечает 200», а инварианты, которые дороже всего сломать:
ключ викторины не уезжает студенту, автор отзыва не виден преподавателю, вторая
активность в беседе не запускается, повторная отправка не сдваивает результат.
"""
from conftest import make_admin, make_teacher
from test_messenger import _make_student

A = "/web/messenger/activities"


def _setup(client):
    admin = make_admin(client)
    t = make_teacher(client, admin)
    b_id, b = _make_student(client, admin, "bob", "Боб Бобов")
    c_id, c = _make_student(client, admin, "carol", "Кэрол Кэрова")
    return admin, ("teach:teacher1", t), (b_id, b), (c_id, c)


def _group(client, headers, member_ids, title="Группа"):
    return client.post("/web/messenger/chats/group",
                       json={"title": title, "member_ids": member_ids},
                       headers=headers).json()["conversation_id"]


def _quiz(client, headers, questions=None, title="Дроби", tags=None):
    body = {"title": title, "tags": tags if tags is not None else ["Математика", " дроби "],
            "questions": questions if questions is not None else [
                {"type": "single", "text": "2+2?", "points": 1,
                 "options": [{"text": "4", "is_correct": True}, {"text": "5"}]},
            ]}
    r = client.post(f"{A}/quizzes", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _start(client, headers, conv, kind, params=None, title=""):
    return client.post(f"{A}/start", headers=headers,
                       json={"conversation_id": conv, "kind": kind,
                             "params": params or {}, "title": title})


# ── Права и границы ──────────────────────────────────────────────────────────────────
def test_teacher_can_start_without_any_conversation_role(client):
    """Преподаватель ведёт занятие, а не администрирует чат: системная роль даёт право
    запуска и в беседе, которую создал кто-то другой."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, admin, [t_id, b_id])       #владелец — админ, преподаватель просто участник
    r = _start(client, t, conv, "timer", {"duration_s": 300})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "timer"


def test_student_cannot_start(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    assert _start(client, b, conv, "timer", {"duration_s": 60}).status_code == 403


def test_student_with_granted_permission_can_start(client):
    """Владелец беседы вправе выдать право старосте — через уже существующий редактор ролей."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    role = client.post(f"/web/messenger/chats/{conv}/roles",
                       json={"name": "Староста", "permissions": ["activities"]},
                       headers=t).json()["id"]
    client.post(f"/web/messenger/chats/{conv}/members/{b_id}/role",
                json={"custom_role_id": role}, headers=t)
    assert _start(client, b, conv, "timer", {"duration_s": 60}).status_code == 200


def test_no_activities_in_direct_chat(client):
    """В личном чате активность бессмысленна: там один собеседник."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = client.post(f"/web/messenger/chats/direct/{b_id}",
                       headers=t).json()["conversation_id"]
    r = _start(client, t, conv, "timer", {"duration_s": 60})
    assert r.status_code == 403
    assert "групп" in r.json()["detail"].lower()


def test_second_activity_of_the_SAME_kind_is_refused_but_others_are_allowed(client):
    """Правило сменилось по живому отзыву: беседа держит НЕСКОЛЬКО активностей, но не
    двух одинаковых.

    Раньше активность была одна на беседу любого вида, и запустить таймер рядом с доской
    было нельзя — а это ровно то, что делают на паре. Два таймера или два среза
    по-прежнему бессмысленны: два отсчёта на экране и два одинаковых опроса понимания.
    Опрос — исключение: он живёт сообщением в ленте и никому не мешает."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])

    assert _start(client, t, conv, "timer", {"duration_s": 60}, "Первая").status_code == 200
    #Тот же вид — отказ.
    dup = _start(client, t, conv, "timer", {"duration_s": 60}, "Вторая")
    assert dup.status_code == 409, dup.text
    #Другой вид — можно: таймер рядом с доской это нормальная работа.
    assert _start(client, t, conv, "board", {"sheet": "grid"}).status_code == 200
    #Опросов можно несколько.
    assert _start(client, t, conv, "poll", {"question": "А?", "options": ["1", "2"]}).status_code == 200
    assert _start(client, t, conv, "poll", {"question": "Б?", "options": ["1", "2"]}).status_code == 200

    running = client.get(f"{A}/running", params={"conversation_id": conv}, headers=b).json()["activities"]
    assert sorted(x["kind"] for x in running) == ["board", "poll", "poll", "timer"], running

def test_finishing_frees_the_conversation(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "timer", {"duration_s": 60}).json()["id"]
    assert client.post(f"{A}/{a}/finish", json={}, headers=t).status_code == 200
    assert _start(client, t, conv, "timer", {"duration_s": 60}).status_code == 200


def test_only_host_can_finish(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "timer", {"duration_s": 60}).json()["id"]
    assert client.post(f"{A}/{a}/finish", json={}, headers=b).status_code == 403


# ── Ключ викторины ───────────────────────────────────────────────────────────────────
def test_student_never_receives_the_answer_key(client):
    """🔒 ГЛАВНЫЙ тест категории. Получив ключ, студент прочитает его в инструментах
    разработчика до начала. Проверяем не «поле пустое», а что его НЕТ ВООБЩЕ: пустое
    поле в JSON тоже сообщает, где ключ."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t)
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]
    body = client.get(f"{A}/{a}/questions", headers=b).json()
    assert body["questions"], body
    for q in body["questions"]:
        for opt in q["options"]:
            assert "is_correct" not in opt
            assert "correct_position" not in opt
            assert "match_key" not in opt
    #И в сыром тексте ответа тоже — на случай, если ключ протечёт другим полем.
    raw = client.get(f"{A}/{a}/questions", headers=b).text
    assert "is_correct" not in raw


def test_host_also_gets_questions_without_key(client):
    """Ведущему ключ здесь не нужен (он смотрит свою викторину в конструкторе), а ветка
    «а хосту отдадим с ключом» превратилась бы в вопрос «а точно ли это хост» ровно там,
    где ошибиться дороже всего."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t)
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]
    assert "is_correct" not in client.get(f"{A}/{a}/questions", headers=t).text


def test_constructor_does_give_the_key_to_the_author(client):
    """Обратная сторона: без ключа в конструкторе викторину нельзя было бы редактировать.
    Без этой проверки «починка» вида «не отдавать ключ никогда» была бы зелёной."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    quiz = _quiz(client, t)
    out = client.get(f"{A}/quizzes/{quiz}", headers=t).json()
    assert out["questions"][0]["options"][0]["is_correct"] is True


# ── Прохождение ──────────────────────────────────────────────────────────────────────
def _two_question_quiz(client, headers):
    return _quiz(client, headers, questions=[
        {"type": "single", "text": "2+2?", "points": 1,
         "options": [{"text": "4", "is_correct": True}, {"text": "5"}]},
        {"type": "multi", "text": "Чётные?", "points": 2,
         "options": [{"text": "2", "is_correct": True}, {"text": "3"},
                     {"text": "4", "is_correct": True}]},
    ])


def _key_of(client, headers, quiz):
    """Правильные ответы из конструктора — тест не должен угадывать их по порядку."""
    out = client.get(f"{A}/quizzes/{quiz}", headers=headers).json()
    key = {}
    for q in out["questions"]:
        correct = [o["id"] for o in q["options"] if o["is_correct"]]
        key[q["id"]] = correct[0] if q["type"] == "single" else correct
    return key


def test_submit_grades_on_the_server(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _two_question_quiz(client, t)
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]
    key = _key_of(client, t, quiz)
    r = client.post(f"{A}/{a}/submit", json={"answers": key}, headers=b)
    assert r.status_code == 200, r.text
    assert r.json()["correct_count"] == 2
    assert r.json()["score"] == 3.0            #1 + 2 очка


def test_partially_correct_multi_is_wrong(client):
    """«Всё или ничего» в multi — осознанный выбор, а не упрощение (§8.4 плана)."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _two_question_quiz(client, t)
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]
    key = _key_of(client, t, quiz)
    multi_qid = [k for k, v in key.items() if isinstance(v, list)][0]
    key[multi_qid] = key[multi_qid][:1]        #только половина верных
    r = client.post(f"{A}/{a}/submit", json={"answers": key}, headers=b).json()
    assert r["correct_count"] == 1


def test_submit_is_graded_once_and_cannot_be_farmed(client):
    """🔒 Ответ на отправку содержит РАЗБОР («где верно, где нет»). Если пускать вторую
    отправку, ключ не нужен вовсе: шлём пустой набор, читаем из ответа список неверных,
    исправляем, шлём снова — для вопросов с четырьмя вариантами это ≤4 запроса до ста
    баллов, без всяких инструментов разработчика. Поэтому проверка ОДНА, а повтор
    возвращает уже сохранённое."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _two_question_quiz(client, t)
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]
    key = _key_of(client, t, quiz)
    first = client.post(f"{A}/{a}/submit", json={"answers": {}}, headers=b).json()
    assert first["correct_count"] == 0
    #Вторая попытка с ПРАВИЛЬНЫМИ ответами не должна поднять балл.
    second = client.post(f"{A}/{a}/submit", json={"answers": key}, headers=b).json()
    assert second["already_submitted"] is True
    assert second["correct_count"] == 0
    res = client.get(f"{A}/{a}/results", headers=t).json()["results"]
    assert len(res) == 1                       #и строка по-прежнему одна
    assert res[0]["correct_count"] == 0


def test_network_retry_gets_its_own_result_back(client):
    """Обратная сторона: сетевой ретрай шлёт ТЕ ЖЕ ответы и обязан получить свой
    результат, а не отказ. Без этой проверки «починка» вида «второй submit — 409»
    выглядела бы правильной и ломала бы человека с дрогнувшей сетью."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _two_question_quiz(client, t)
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]
    key = _key_of(client, t, quiz)
    one = client.post(f"{A}/{a}/submit", json={"answers": key}, headers=b)
    two = client.post(f"{A}/{a}/submit", json={"answers": key}, headers=b)
    assert one.status_code == 200 and two.status_code == 200
    assert two.json()["score"] == one.json()["score"] == 3.0


def test_participant_sees_only_own_result(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _two_question_quiz(client, t)
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]
    key = _key_of(client, t, quiz)
    client.post(f"{A}/{a}/submit", json={"answers": key}, headers=b)
    client.post(f"{A}/{a}/submit", json={"answers": {}}, headers=c)
    assert len(client.get(f"{A}/{a}/results", headers=t).json()["results"]) == 2
    mine = client.get(f"{A}/{a}/results", headers=c).json()["results"]
    assert len(mine) == 1 and mine[0]["user_id"] == c_id


# ── Опрос ────────────────────────────────────────────────────────────────────────────
def _poll(client, t, conv):
    return _start(client, t, conv, "poll",
                  {"question": "Понятно?", "options": ["Да", "Нет"]}).json()["id"]


def test_poll_results_only_for_creator(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _poll(client, t, conv)
    client.post(f"{A}/{a}/vote", json={"choice": 0}, headers=b)
    assert client.get(f"{A}/{a}/poll-results", headers=b).status_code == 403
    out = client.get(f"{A}/{a}/poll-results", headers=t).json()
    assert out["counts"] == [1, 0]


def test_poll_results_stay_private_after_finish(client):
    """Обещание «распределение видит только преподаватель» не может истекать по времени."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _poll(client, t, conv)
    client.post(f"{A}/{a}/vote", json={"choice": 1}, headers=b)
    client.post(f"{A}/{a}/finish", json={}, headers=t)
    assert client.get(f"{A}/{a}/poll-results", headers=b).status_code == 403


def test_timer_control_does_not_report_success_when_state_is_gone(client, monkeypatch):
    """🔥 НАЙДЕНО ПОЛКОВНИКОМ, а не проверкой типов — и это важно: mypy это место НЕ
    подсвечивает, потому что снимок здесь не индексируется, а просто возвращается наружу.

    Та же гонка, что и у голосования (завершили активность между чтением и записью, либо
    служба перезапускалась), но исход хуже: `_emit_state` молча выходит на None, и ведущий
    получает **200 `{"ok": true, "state": null}`**. То есть «Продлить» отвечает УСПЕХОМ,
    ничего не продлив и никому не разослав кадр. Тихий ложный успех опаснее пятисотки:
    пятисотку видно, а этот отказ выглядит как исправная работа.

    Обратный ход: снять `_require_state` у `control_timer` — тест краснеет (200 вместо 400).
    """
    from app import activity_state

    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "timer", {"duration_s": 300}).json()["id"]

    monkeypatch.setattr(activity_state, "patch", lambda *args, **kwargs: None)
    r = client.post(f"{A}/{a}/timer", json={"action": "extend", "seconds": 60}, headers=t)
    assert r.status_code == 400, f"тихий успех вместо отказа: {r.status_code} {r.text}"


def test_vote_survives_activity_finished_between_read_and_write(client, monkeypatch):
    """🔥 ГОНКА, НАЙДЕННАЯ ПРОВЕРКОЙ ТИПОВ. Обработчик голоса сперва читает состояние
    (`activity_state.get` — с честной проверкой на None), а потом ПИШЕТ его
    (`activity_state.patch`) и сразу лезет в результат: `snap["seq"]`. Между этими двумя
    вызовами активность может исчезнуть из памяти процесса — ведущий нажал «Завершить»,
    а обработчики FastAPI выполняются в пуле потоков, то есть параллельно по-настоящему.
    Тогда `patch` возвращает None, и студент получает 500 с трейсбеком вместо внятного
    «Опрос уже завершён».

    Состояние активностей живёт В ПАМЯТИ ПРОЦЕССА (инвариант «один uvicorn»), поэтому
    тот же None приходит и после обычного перезапуска службы — то есть после каждого
    деплоя, пока в беседе идёт опрос.

    Гонку воспроизводим детерминированно: `patch` подменяем на «активности уже нет».
    Обратный ход: снять проверку после patch — тест краснеет (500 вместо 400).
    """
    from app import activity_state

    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _poll(client, t, conv)

    monkeypatch.setattr(activity_state, "patch", lambda *args, **kwargs: None)
    r = client.post(f"{A}/{a}/vote", json={"choice": 0}, headers=b)
    assert r.status_code == 400, f"ожидали внятный отказ, получили {r.status_code}"


def test_revote_replaces_and_students_see_only_the_counter(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _poll(client, t, conv)
    client.post(f"{A}/{a}/vote", json={"choice": 0}, headers=b)
    client.post(f"{A}/{a}/vote", json={"choice": 1}, headers=b)   #передумал
    assert client.get(f"{A}/{a}/poll-results", headers=t).json()["counts"] == [0, 1]
    state = client.get(f"{A}/{a}", headers=c).json()["state"]["payload"]
    assert state["voted_count"] == 1
    assert "votes" not in state                #карта голосов наружу не уходит


# ── Срез понимания ───────────────────────────────────────────────────────────────────
def test_host_feedback_never_leaks_authors(client):
    """🔑 Автор отзыва в базе ЕСТЬ (иначе жалоба админу никуда не ведёт), но интерфейс
    преподавателя его не видит никогда. Проверяем сырой текст ответа целиком: утечь мог
    бы не только `user_id`, но и любое поле, по которому автора вычисляют."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "pulse", {"duration_s": 60}).json()["id"]
    client.post(f"{A}/{a}/feedback",
                json={"score": 3, "reason_code": "tempo", "text": "Слишком быстро"},
                headers=b)
    raw = client.get(f"{A}/{a}/feedback", headers=t)
    assert raw.status_code == 200, raw.text
    assert b_id not in raw.text
    assert "user_id" not in raw.text
    body = raw.json()
    assert body["answers"] == 1 and body["items"][0]["text"] == "Слишком быстро"


def test_feedback_report_gives_the_author_to_the_admin(client):
    """Обратная сторона того же: жалоба обязана вести к человеку, иначе она бессмысленна."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "pulse", {"duration_s": 60}).json()["id"]
    client.post(f"{A}/{a}/feedback", json={"score": 1, "text": "оскорбление"}, headers=b)
    fid = client.get(f"{A}/{a}/feedback", headers=t).json()["items"][0]["id"]
    r = client.post(f"{A}/feedback/{fid}/report", json={"reason_code": "harassment"},
                    headers=t)
    assert r.status_code == 200, r.text
    reports = client.get("/web/admin/messenger/reports", headers=admin).json()["reports"]
    mine = [x for x in reports if x["id"] == r.json()["report_id"]][0]
    assert mine["reported"]["id"] == b_id     #админ видит автора


def test_feedback_report_is_marked_as_not_a_message(client):
    """⚠️ У такого тикета `message_id` — это id строки отзыва, а НЕ сообщения. Без
    `target_kind` админка предложила бы «удалить сообщение», и удалён был бы посторонний
    текст с совпавшим номером."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "pulse", {"duration_s": 60}).json()["id"]
    client.post(f"{A}/{a}/feedback", json={"score": 2, "text": "плохо"}, headers=b)
    fid = client.get(f"{A}/{a}/feedback", headers=t).json()["items"][0]["id"]
    rid = client.post(f"{A}/feedback/{fid}/report", json={}, headers=t).json()["report_id"]
    reports = client.get("/web/admin/messenger/reports", headers=admin).json()["reports"]
    mine = [x for x in reports if x["id"] == rid][0]
    assert mine["target_kind"] == "activity_feedback"


def test_ordinary_message_report_stays_a_message(client):
    """Обратный ход: старые тикеты остались тем, чем были (умолчание, а не пустое поле)."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    mid = client.post(f"/web/messenger/chats/{conv}/messages", json={"body": "текст"},
                      headers=b).json()["id"]
    client.post("/web/messenger/reports",
                json={"message_id": mid, "reason_code": "spam"}, headers=t)
    reports = client.get("/web/admin/messenger/reports", headers=admin).json()["reports"]
    assert reports[0]["target_kind"] == "message"


# ── Библиотека викторин ──────────────────────────────────────────────────────────────
def test_foreign_quiz_cannot_be_edited_but_can_be_copied(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    other = make_teacher(client, admin, login="teacher2")
    quiz = _quiz(client, t)
    client.put(f"{A}/quizzes/{quiz}", json={"visibility": "college"}, headers=t)
    assert client.put(f"{A}/quizzes/{quiz}", json={"title": "Моё"},
                      headers=other).status_code == 403
    copy = client.post(f"{A}/quizzes/{quiz}/copy", headers=other)
    assert copy.status_code == 200, copy.text
    assert copy.json()["parent_id"] == quiz


def test_copy_chain_is_hidden_while_it_has_one_author(client):
    """Ветки внутри одного преподавателя — его кухня, а не история заимствований.
    Сворачивание делает СЕРВЕР: «скрытая» в UI цепочка всё равно уезжала бы в ответе."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    quiz = _quiz(client, t)
    mine = client.post(f"{A}/quizzes/{quiz}/copy", headers=t).json()["id"]
    assert client.get(f"{A}/quizzes/{mine}/similar", headers=t).json()["chain"] == []
    other = make_teacher(client, admin, login="teacher2")
    client.put(f"{A}/quizzes/{mine}", json={"visibility": "college"}, headers=t)
    theirs = client.post(f"{A}/quizzes/{mine}/copy", headers=other).json()["id"]
    chain = client.get(f"{A}/quizzes/{theirs}/similar", headers=other).json()["chain"]
    assert len(chain) == 3                     #двое авторов — цепочка показывается


def test_tags_are_normalised_so_search_finds_them(client):
    """«матем» / «Математика» / « математика » должны быть одним тегом, иначе поиск по
    тегу перестаёт находить половину библиотеки."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    _quiz(client, t, tags=["  Математика ", "МАТЕМАТИКА", "Дроби"])
    out = client.get(f"{A}/quizzes", params={"tag": "математика"}, headers=t).json()
    assert len(out["quizzes"]) == 1
    assert out["quizzes"][0]["tags"] == ["математика", "дроби"]     #дубль схлопнут


def test_quiz_search_is_case_insensitive_for_cyrillic(client):
    """SQLite без ICU не умеет регистронезависимый LIKE для кириллицы — поиск идёт в
    Python, и это надо проверять, а не подразумевать."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    _quiz(client, t, title="Логарифмы и Дроби")
    assert client.get(f"{A}/quizzes", params={"q": "логарифмы"},
                      headers=t).json()["quizzes"]


def test_student_has_no_access_to_the_library(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    assert client.get(f"{A}/quizzes", headers=b).status_code == 403


# ── Соревнование ─────────────────────────────────────────────────────────────────────
def test_contest_refuses_order_and_match_questions(client):
    """На общем табло спор о цене частично верного ответа недопустим (§8.5)."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t, questions=[
        {"type": "order", "text": "По порядку", "options": [
            {"text": "раз", "correct_position": 1}, {"text": "два", "correct_position": 2}]},
    ])
    r = _start(client, t, conv, "contest", {"quiz_id": quiz})
    assert r.status_code == 400
    assert "соревновании" in r.json()["detail"].lower()


def test_contest_hides_questions_until_the_host_shows_them(client):
    """Отдать все вопросы сразу значит отдать и те, что ещё не задавали."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _two_question_quiz(client, t)
    a = _start(client, t, conv, "contest", {"quiz_id": quiz}).json()["id"]
    assert client.get(f"{A}/{a}/questions", headers=b).json()["questions"] == []
    client.post(f"{A}/{a}/next", headers=t)
    shown = client.get(f"{A}/{a}/questions", headers=b).json()
    assert len(shown["questions"]) == 1 and shown["index"] == 0


def test_contest_answer_is_accepted_once(client):
    """Второй ответ был бы способом выбрать наугад, а потом исправиться без потери времени."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _two_question_quiz(client, t)
    a = _start(client, t, conv, "contest", {"quiz_id": quiz}).json()["id"]
    client.post(f"{A}/{a}/next", headers=t)
    q = client.get(f"{A}/{a}/questions", headers=b).json()["questions"][0]
    first = client.post(f"{A}/{a}/answer", json={"answer": q["options"][0]["id"]}, headers=b)
    assert first.status_code == 200
    again = client.post(f"{A}/{a}/answer", json={"answer": q["options"][1]["id"]}, headers=b)
    assert again.status_code == 409


def test_contest_scores_survive_the_finish(client):
    """Баллы живут в памяти, пока идёт соревнование, но журнал беседы обязан пережить
    перезапуск — итог переносится в БД один раз, при завершении."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _two_question_quiz(client, t)
    a = _start(client, t, conv, "contest", {"quiz_id": quiz}).json()["id"]
    client.post(f"{A}/{a}/next", headers=t)
    q = client.get(f"{A}/{a}/questions", headers=b).json()["questions"][0]
    correct = [o for o in q["options"] if o["text"] == "4"][0]["id"]
    client.post(f"{A}/{a}/answer", json={"answer": correct}, headers=b)
    client.post(f"{A}/{a}/finish", json={}, headers=t)
    res = client.get(f"{A}/{a}/results", headers=t).json()["results"]
    assert len(res) == 1 and res[0]["score"] > 0


# ── Доска ────────────────────────────────────────────────────────────────────────────
def test_only_pen_holder_can_draw(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "board", {"sheet": "grid"}).json()["id"]
    assert client.post(f"{A}/{a}/strokes", json={"strokes": [{"p": [1, 2]}]},
                       headers=b).status_code == 403
    client.post(f"{A}/{a}/pen", json={"user_id": b_id}, headers=t)
    assert client.post(f"{A}/{a}/strokes", json={"strokes": [{"p": [1, 2]}]},
                       headers=b).status_code == 200


def test_board_is_saved_only_when_asked(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "board", {}).json()["id"]
    client.post(f"{A}/{a}/strokes", json={"strokes": [{"p": [1, 2]}]}, headers=t)
    client.post(f"{A}/{a}/finish", json={"save": False}, headers=t)
    assert client.get(f"{A}/boards", params={"conversation_id": conv},
                      headers=t).json()["boards"] == []


def test_saved_board_can_be_continued_with_its_strokes(client):
    """Продолжить можно ТОЛЬКО штрихами — поверх растра рисуют заново (§8.1)."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "board", {"sheet": "lined"}).json()["id"]
    client.post(f"{A}/{a}/strokes", json={"strokes": [{"p": [1, 2]}, {"p": [3, 4]}]}, headers=t)
    client.post(f"{A}/{a}/finish", json={"save": True}, headers=t)
    board = client.get(f"{A}/boards", params={"conversation_id": conv},
                       headers=t).json()["boards"][0]
    assert board["strokes_count"] == 2
    again = _start(client, t, conv, "board", {"continue_board_id": board["id"]}).json()
    assert len(again["state"]["payload"]["strokes"]) == 2
    assert again["state"]["payload"]["sheet"] == "lined"


def test_board_from_another_conversation_is_not_picked_up(client):
    """id доски из чужой группы не должен подтягивать её содержимое сюда."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv1 = _group(client, t, [b_id], title="Первая")
    conv2 = _group(client, t, [c_id], title="Вторая")
    a = _start(client, t, conv1, "board", {}).json()["id"]
    client.post(f"{A}/{a}/strokes", json={"strokes": [{"p": [9, 9]}]}, headers=t)
    client.post(f"{A}/{a}/finish", json={"save": True}, headers=t)
    board_id = client.get(f"{A}/boards", params={"conversation_id": conv1},
                          headers=t).json()["boards"][0]["id"]
    out = _start(client, t, conv2, "board", {"continue_board_id": board_id}).json()
    assert out["state"]["payload"]["strokes"] == []


def test_snapshot_does_not_finish_the_board(client):
    """На доске много написано, и снимок не должен закрывать пару."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "board", {}).json()["id"]
    client.post(f"{A}/{a}/strokes", json={"strokes": [{"p": [1, 1]}]}, headers=t)
    assert client.post(f"{A}/{a}/snapshot", headers=t).status_code == 200
    assert client.get(f"{A}/{a}", headers=t).json()["status"] == "running"


# ── Карточка в ленте ─────────────────────────────────────────────────────────────────
#ℹ️ Здесь были два теста команды `/активность` — она удалена 17.08.2026 (решение Влада:
#активности открывает кнопка в шапке беседы). Второй из них проверял важное: студенту
#отказывали ДО выбора категории, а не после. Кнопка эту проверку не унаследовала, поэтому
#её пришлось восстановить на клиенте (`ChatThread.vue`: у того, кто не вправе запускать,
#кнопка ведёт в идущую активность, а при её отсутствии не показывается вовсе). Серверная
#часть того инварианта жива и покрыта отдельно — `POST /activities/start` отвечает 403,
#см. `test_student_cannot_start` в начале этого файла.
def test_feed_card_carries_the_activity_object(client):
    """В теле сообщения лежит только id — объект подмешивает сервер, потому что статус
    меняется ПОСЛЕ отправки."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "board", {"sheet": "grid"}, "Пятиминутка").json()["id"]
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    card = [m for m in msgs if m["kind"] == "activity"][0]
    assert card["activity"]["id"] == a
    assert card["activity"]["status"] == "running"
    client.post(f"{A}/{a}/finish", json={}, headers=t)
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    card = [m for m in msgs if m["kind"] == "activity"][0]
    assert card["activity"]["status"] == "finished"    #кнопка гаснет по статусу


def test_chat_list_preview_also_resolves_the_card(client):
    """Превью последнего сообщения — то место, куда заглядывают реже всего, и именно там
    сырой `act:9f3…` жил бы дольше всего незамеченным."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    _start(client, t, conv, "board", {"sheet": "grid"}, "Пятиминутка")
    chats = client.get("/web/messenger/chats", headers=b).json()["chats"]
    row = [x for x in chats if x["conversation_id"] == conv][0]
    assert row["last_message"]["activity"]["title"] == "Пятиминутка"


# ── Журнал беседы ────────────────────────────────────────────────────────────────────
def test_journal_full_for_host_and_own_rows_for_participant(client):
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _two_question_quiz(client, t)
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]
    client.post(f"{A}/{a}/submit", json={"answers": _key_of(client, t, quiz)}, headers=b)
    client.post(f"{A}/{a}/finish", json={}, headers=t)
    full = client.get(f"{A}/journal", params={"conversation_id": conv}, headers=t).json()
    assert full["full"] is True and full["items"][0]["participants"] == 1
    mine = client.get(f"{A}/journal", params={"conversation_id": conv}, headers=b).json()
    assert mine["full"] is False and mine["items"][0]["my_correct"] == 2
    #Кэрол не участвовала — в её журнале этой активности нет вовсе.
    theirs = client.get(f"{A}/journal", params={"conversation_id": conv}, headers=c).json()
    assert theirs["items"] == []


# ── Маршрутизация ────────────────────────────────────────────────────────────────────
def test_single_segment_routes_are_not_shadowed(client):
    """⚠️ `GET /{activity_id}` — параметр без ограничений: объяви его выше, и `/quizzes`,
    `/journal`, `/boards` начнут отвечать «Активность не найдена» при исправном коде и
    зелёных тестах на сами эти эндпоинты. Проверяем именно ЭТО, а не их работу."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    for path, params in ((f"{A}/quizzes", {}),
                         (f"{A}/journal", {"conversation_id": conv}),
                         (f"{A}/boards", {"conversation_id": conv}),
                         (f"{A}/current", {"conversation_id": conv})):
        r = client.get(path, params=params, headers=t)
        assert r.status_code == 200, f"{path} перехвачен чужим обработчиком: {r.text}"
        assert "Активность не найдена" not in r.text


# ── Границы карточки (возражения Полковника, 15.08.2026) ─────────────────────────────
def test_activity_card_cannot_be_forwarded_to_another_conversation(client):
    """Карточка привязана к своей беседе: снаружи она не открывается (403), то есть в
    чужой ленте повисла бы нерабочая кнопка — а вместе с ней уехали бы заголовок и
    статус (заголовок пишет преподаватель, там бывает тема контрольной)."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv1 = _group(client, t, [b_id], title="Первая")
    conv2 = _group(client, t, [c_id], title="Вторая")
    _start(client, t, conv1, "board", {"sheet": "grid"}, "Контрольная по дробям")
    msgs = client.get(f"/web/messenger/chats/{conv1}/messages", headers=t).json()["messages"]
    card = [m for m in msgs if m["kind"] == "activity"][0]
    client.post("/web/messenger/messages/forward",
                json={"message_ids": [card["id"]], "to_conversation_ids": [conv2]}, headers=t)
    there = client.get(f"/web/messenger/chats/{conv2}/messages", headers=t).json()["messages"]
    assert not any(m["kind"] == "activity" for m in there)
    assert not any("Контрольная по дробям" in (m.get("body") or "") for m in there)


def test_card_object_is_not_attached_outside_its_own_conversation(client):
    """Оборона в глубину: даже если строка КАК-ТО оказалась в чужой ленте, объект к ней
    не подмешивается — заголовок и статус наружу не уходят."""
    from app.db import SessionLocal
    from app.models import Message
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv1 = _group(client, t, [b_id], title="Первая")
    conv2 = _group(client, t, [c_id], title="Вторая")
    aid = _start(client, t, conv1, "board", {"sheet": "grid"}, "Тема контрольной").json()["id"]
    db = SessionLocal()
    db.add(Message(conversation_id=conv2, sender_id=t_id, body=aid,
                   created_at="2026-08-15T00:00:00+00:00", kind="activity", body_format="plain"))
    db.commit()
    db.close()
    raw = client.get(f"/web/messenger/chats/{conv2}/messages", headers=c)
    #Сырой текст ответа целиком: заголовок не должен просочиться НИ ОДНИМ полем.
    assert "Тема контрольной" not in raw.text
    card = [m for m in raw.json()["messages"] if m["kind"] == "activity"][0]
    assert card["activity"] is None


def test_pinned_and_search_resolve_the_card_too(client):
    """⚠️ Обёртка `_attach_rich_meta` заведена ради «нельзя забыть место», но звалась не
    отовсюду: закреплённые, поиск, ветка ответов и модерация отдавали сырой `act:…`."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    _start(client, t, conv, "board", {"sheet": "grid"}, "Пятиминутка")
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=t).json()["messages"]
    card = [m for m in msgs if m["kind"] == "activity"][0]
    client.post(f"/web/messenger/messages/{card['id']}/pin", json={}, headers=t)
    pinned = client.get(f"/web/messenger/chats/{conv}/pinned", headers=t).json()["pinned"]
    got = [m for m in pinned if m["kind"] == "activity"]
    assert got and got[0]["activity"]["title"] == "Пятиминутка"
    #И модерация читает ту же ленту, что участники.
    mod = client.get(f"/web/admin/messenger/conversations/{conv}/messages", headers=admin).json()
    got2 = [m for m in mod["messages"] if m["kind"] == "activity"]
    assert got2 and got2[0]["activity"]["title"] == "Пятиминутка"


def test_short_lived_activities_leave_no_trace_in_the_feed(client):
    """Таймер и срез понимания НЕ оставляют карточку в ленте.

    Они живут минуты и всплывают у всех сами — заходить в них из истории незачем, а на
    паре их запускают по нескольку раз, и отметка о каждом превращала переписку в мусор.
    Обратная половина обязательна: викторина/соревнование/доска карточку оставляют, иначе
    «починка» вида «не создавать сообщение никогда» прошла бы незамеченной."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])

    for kind, params in (("timer", {"duration_s": 60}), ("pulse", {"duration_s": 60})):
        a = _start(client, t, conv, kind, params).json()["id"]
        msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
        assert not [m for m in msgs if m["kind"] == "activity"], f"{kind} оставил след в ленте"
        client.post(f"{A}/{a}/finish", json={}, headers=t)

    a = _start(client, t, conv, "board", {"sheet": "grid"}).json()["id"]
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    cards = [m for m in msgs if m["kind"] == "activity"]
    assert len(cards) == 1 and cards[0]["activity"]["id"] == a, "доска обязана оставить карточку"


def test_review_shows_the_key_only_after_submitting(client):
    """Разбор с правильными ответами приходит ВМЕСТЕ с результатом отправки — и это
    безопасно ровно потому, что пересдать нельзя (см. `submit_quiz` про оракул).

    Проверяем обе половины: до отправки вопросы приходят БЕЗ ключа, после — с ключом и
    с максимальным баллом. Без первой половины тест был бы зелёным и в том случае, если
    ключ утекает студенту до начала прохождения."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t)
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]

    qs = client.get(f"{A}/{a}/questions", headers=b).json()["questions"]
    raw = str(qs)
    assert "is_correct" not in raw, "ключ не должен приходить ДО отправки"

    r = client.post(f"{A}/{a}/submit", json={"answers": {}, "duration_ms": 1000}, headers=b)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("max_score"), "максимальный балл нужен экрану итогов"
    assert body.get("review"), "разбор обязан прийти после отправки"
    assert any(o["is_correct"] for q in body["review"] for o in q["options"]), \
        "в разборе должен быть отмечен верный вариант"


def test_poll_is_a_chat_message_not_a_link_to_an_overlay(client):
    """Опрос — сообщение В ЛЕНТЕ с кнопками (как в Telegram), а не карточка-ссылка.

    Обратная половина: доска по-прежнему оставляет `kind="activity"`. Без неё «починка»
    вида «называть всё опросом» осталась бы незамеченной."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "poll",
               {"question": "Когда пересдача?", "options": ["Вторник", "Четверг"]}).json()["id"]

    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    polls = [m for m in msgs if m["kind"] == "poll"]
    assert len(polls) == 1, msgs
    cell = polls[0]["activity"]
    assert cell["id"] == a
    assert cell["options"] == ["Вторник", "Четверг"], cell
    assert cell["my_choice"] is None
    #🔒 Распределение не создателю не отдаём.
    assert "tally" not in cell, "чужие голоса студенту показывать нельзя"

    host_msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=t).json()["messages"]
    host_cell = [m for m in host_msgs if m["kind"] == "poll"][0]["activity"]
    assert "tally" in host_cell, "автору опроса распределение нужно"

    client.post(f"{A}/{a}/finish", json={}, headers=t)
    _start(client, t, conv, "board", {"sheet": "grid"})
    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert [m for m in msgs if m["kind"] == "activity"], "доска обязана остаться карточкой"


def test_match_pool_is_given_without_revealing_the_mapping(client):
    """Сопоставление: студент получает СПИСОК правых половин (иначе выбирать не из чего),
    но не узнаёт, какая к какой — `match_key` у вариантов по-прежнему нет."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t, questions=[{
        "type": "match", "text": "Сопоставьте", "points": 2,
        "options": [{"text": "HTTP", "match_key": "протокол"},
                    {"text": "HTML", "match_key": "разметка"}],
    }])
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]
    q = client.get(f"{A}/{a}/questions", headers=b).json()["questions"][0]

    assert sorted(q["match_pool"]) == ["протокол", "разметка"], q
    assert all("match_key" not in o for o in q["options"]), "ключ соответствия утёк"


def test_quiz_time_limit_survives_save_and_reaches_the_player(client):
    """Ограничение времени задаётся в конструкторе и доезжает до прохождения.
    Ноль — «без ограничения», и это тоже проверяем: пустое поле не должно превращаться
    в мгновенно истёкший таймер."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t)

    r = client.put(f"{A}/quizzes/{quiz}", json={"time_limit_s": 600}, headers=t)
    assert r.status_code == 200, r.text
    assert client.get(f"{A}/quizzes/{quiz}", headers=t).json()["time_limit_s"] == 600

    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]
    assert client.get(f"{A}/{a}/questions", headers=b).json()["time_limit_s"] == 600

    client.put(f"{A}/quizzes/{quiz}", json={"time_limit_s": 0}, headers=t)
    assert client.get(f"{A}/quizzes/{quiz}", headers=t).json()["time_limit_s"] == 0


def test_finished_poll_keeps_its_results_in_the_feed(client):
    """Завершённый опрос обязан ПОМНИТЬ голоса.

    🔥 Живое состояние после завершения гасится, а опрос — это сообщение, которое
    остаётся в ленте навсегда. Без снимка он показывал бы «проголосовало: 0» и пустые
    полосы, то есть выглядел бы так, будто в нём никто не участвовал. Найдено
    самопроверкой перед выкладкой, а не тестом, — поэтому тест и появился."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "poll",
               {"question": "Когда пересдача?", "options": ["Вторник", "Четверг"]}).json()["id"]

    assert client.post(f"{A}/{a}/vote", json={"choice": 1}, headers=b).status_code == 200
    assert client.post(f"{A}/{a}/vote", json={"choice": 1}, headers=c).status_code == 200
    client.post(f"{A}/{a}/finish", json={}, headers=t)

    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=t).json()["messages"]
    cell = [m for m in msgs if m["kind"] == "poll"][0]["activity"]
    assert cell["voted_count"] == 2, cell
    assert cell["tally"] == [0, 2], cell

    #И у проголосовавшего его выбор не должен потеряться вместе с живым состоянием.
    mine = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    my_cell = [m for m in mine if m["kind"] == "poll"][0]["activity"]
    assert my_cell["my_choice"] == 1, my_cell


def test_host_sees_the_whole_roster_with_a_progress_scale(client):
    """Ведущий видит ШКАЛУ по каждому участнику — и в викторине, и в соревновании.

    Раньше он получал только тех, кто уже закончил, и «пусто» было неотличимо от «все
    закончили мгновенно». Плюс шкале нужно число заданий: без `total_questions` делить
    полосу не на что."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t, questions=[
        {"type": "single", "text": "1?", "points": 1,
         "options": [{"text": "a", "is_correct": True}, {"text": "b"}]},
        {"type": "single", "text": "2?", "points": 1,
         "options": [{"text": "a", "is_correct": True}, {"text": "b"}]},
    ])
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]

    st = client.get(f"{A}/{a}", headers=t).json()["state"]["payload"]
    assert st["total_questions"] == 2, st
    #Оба участника беседы в списке, ещё до того как кто-то начал.
    assert {r["user_id"] for r in st["progress"]} == {b_id, c_id}, st["progress"]
    assert all(r["walked"] == 0 and not r["done"] for r in st["progress"])

    #Студент дошёл до второго задания — шкала у ведущего сдвинулась.
    assert client.post(f"{A}/{a}/progress", json={"answered": 1}, headers=b).status_code == 200
    st = client.get(f"{A}/{a}", headers=t).json()["state"]["payload"]
    assert [r["walked"] for r in st["progress"] if r["user_id"] == b_id] == [1], st["progress"]

    #🔒 Чужой прогресс студенту не отдаём: у него в состоянии нет ни `walk`, ни `progress`.
    st_b = client.get(f"{A}/{a}", headers=b).json()["state"]["payload"]
    assert "walk" not in st_b and "progress" not in st_b, st_b


def test_progress_never_goes_backwards(client):
    """Человек вернулся к предыдущему вопросу — пройденного это не отменяет.
    Прыгающая назад шкала у ведущего читается как сбой."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t)
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]

    client.post(f"{A}/{a}/progress", json={"answered": 3}, headers=b)
    client.post(f"{A}/{a}/progress", json={"answered": 1}, headers=b)
    st = client.get(f"{A}/{a}", headers=t).json()["state"]["payload"]
    assert [r["walked"] for r in st["progress"] if r["user_id"] == b_id] == [3], st["progress"]


def test_contest_host_also_gets_the_roster_not_the_question(client):
    """Соревнование: ведущему тоже нужен ход, а не задание — он его не решает."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t)
    a = _start(client, t, conv, "contest", {"quiz_id": quiz}).json()["id"]

    st = client.get(f"{A}/{a}", headers=t).json()["state"]["payload"]
    assert "progress" in st and {r["user_id"] for r in st["progress"]} == {b_id, c_id}, st
    assert st.get("total_questions") == 1, st


def test_contest_counts_everyone_who_answered_even_with_zero(client):
    """Ответил на всё неправильно — это ТОЖЕ результат, и он обязан быть в таблице.

    🔥 Раньше итоги собирались по `scores`, а туда человек попадает только когда что-то
    заработал: ответивший мимо не получал строки ВООБЩЕ и пропадал из результатов — со
    стороны выглядело как «соревнование не считает». Именно нулевая строка и говорит
    преподавателю, с кем разбирать тему."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t, questions=[{
        "type": "single", "text": "2+2?", "points": 1,
        "options": [{"text": "4", "is_correct": True}, {"text": "5"}],
    }])
    a = _start(client, t, conv, "contest", {"quiz_id": quiz}).json()["id"]
    client.post(f"{A}/{a}/next", json={}, headers=t)

    qs = client.get(f"{A}/{a}/questions", headers=b).json()["questions"][0]
    wrong = [o["id"] for o in qs["options"] if o["text"] == "5"][0]
    assert client.post(f"{A}/{a}/answer", json={"answer": wrong}, headers=b).status_code == 200

    #Шкала у ведущего обязана видеть, что человек ответил, а не стоять на нуле.
    st = client.get(f"{A}/{a}", headers=t).json()["state"]["payload"]
    assert [r["walked"] for r in st["progress"] if r["user_id"] == b_id] == [1], st["progress"]

    client.post(f"{A}/{a}/finish", json={}, headers=t)
    res = client.get(f"{A}/{a}/results", headers=t).json()["results"]
    assert b_id in {r["user_id"] for r in res}, res
    assert [r["score"] for r in res if r["user_id"] == b_id] == [0.0], res


def test_host_progress_actually_reaches_the_host_over_the_socket(client, monkeypatch):
    """🔥 СТОРОЖ НА ЖИВУЮ ШКАЛУ, а не на её расчёт.

    Расчёт был исправен и покрыт тестом — а мониторинг у преподавателя всё равно выглядел
    мёртвым: `progress` попадает клиенту только в проекции состояния, то есть ОДИН раз
    при открытии, а кадры по сокету несут лишь изменившиеся поля и подмешиваются поверх.
    Значит после открытия список застывал в моменте, когда никто ещё не начинал.
    Проверяем именно ДОСТАВКУ: что при отчёте о прогрессе ведущему уходит кадр со шкалой.
    """
    sent = []

    def _spy(user_ids, data):
        sent.append((list(user_ids), data))

    from app.routers import activities as act_mod
    monkeypatch.setattr(act_mod, "_emit_to", _spy)

    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t)
    a = _start(client, t, conv, "quiz", {"quiz_id": quiz}).json()["id"]

    sent.clear()
    assert client.post(f"{A}/{a}/progress", json={"answered": 1}, headers=b).status_code == 200

    to_host = [d for ids, d in sent if t_id in ids]
    assert to_host, f"ведущему не ушёл ни один кадр: {sent}"
    payload = to_host[-1]["payload"]
    assert "progress" in payload, payload
    assert [r["walked"] for r in payload["progress"] if r["user_id"] == b_id] == [1], payload

    #🔒 И это ИМЕННО адресный кадр: чужой прогресс студентам не полагается.
    assert all(t_id in ids for ids, _ in sent), sent


def test_contest_advances_by_time_and_finishes_itself_after_the_last_question(client):
    """Ход ведёт ВРЕМЯ, а ведущий может его обогнать.

    🔥 Чинит тупик: на последнем вопросе «следующий» отвечал ошибкой «вопросы
    закончились», выбрать ответ было уже нельзя, а итоги появлялись, только если ведущий
    вручную нажимал «завершить». Двигает СЕРВЕР: у тридцати человек тридцать своих часов,
    и переход по клиентскому таймеру случился бы тридцать раз в разные моменты."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    qs = [{"type": "single", "text": f"{i}?", "points": 1,
           "options": [{"text": "да", "is_correct": True}, {"text": "нет"}]} for i in (1, 2)]
    quiz = _quiz(client, t, questions=qs)
    #Минимум, который принимает сервер, — 5 секунд (ниже вопрос не успеть прочитать).
    #Ждать в тесте настоящие тридцать нельзя, поэтому берём именно минимум.
    a = _start(client, t, conv, "contest", {"quiz_id": quiz, "limit_ms": 5000}).json()["id"]
    client.post(f"{A}/{a}/next", json={}, headers=t)
    assert client.get(f"{A}/{a}", headers=b).json()["state"]["payload"]["question_index"] == 0

    import time
    time.sleep(5.3)
    #Любое обращение к API подметает истёкшее — как и у таймера.
    client.get(f"{A}/running", params={"conversation_id": conv}, headers=b)
    st = client.get(f"{A}/{a}", headers=b).json()["state"]["payload"]
    assert st["question_index"] == 1, f"время вышло — вопрос обязан смениться сам: {st}"

    time.sleep(5.3)
    client.get(f"{A}/running", params={"conversation_id": conv}, headers=b)
    fresh = client.get(f"{A}/{a}", headers=b).json()
    assert fresh["status"] == "finished", "после последнего вопроса активность завершается сама"


def test_contest_leaderboard_is_open_to_everyone_after_it_ends(client):
    """Пьедестал — смысл категории, его видят ВСЕ. Но только после завершения: до него
    чужие баллы никому не полагаются. В обычной викторине таблица так и остаётся личной."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t)
    a = _start(client, t, conv, "contest", {"quiz_id": quiz}).json()["id"]
    client.post(f"{A}/{a}/next", json={}, headers=t)
    q = client.get(f"{A}/{a}/questions", headers=b).json()["questions"][0]
    client.post(f"{A}/{a}/answer", json={"answer": q["options"][0]["id"]}, headers=b)
    client.post(f"{A}/{a}/answer", json={"answer": q["options"][1]["id"]}, headers=c)

    #До завершения участник видит только себя.
    mine = client.get(f"{A}/{a}/results", headers=b).json()["results"]
    assert {r["user_id"] for r in mine} <= {b_id}, mine

    client.post(f"{A}/{a}/finish", json={}, headers=t)
    board = client.get(f"{A}/{a}/results", headers=b).json()["results"]
    assert {b_id, c_id} <= {r["user_id"] for r in board}, board
    #«Выполнено» считает ОТВЕТИВШИХ, даже если ответ неверный.
    assert all(r["answered_count"] == 1 for r in board), board


def test_correct_contest_answer_actually_scores(client):
    """⚠️ Обратный тест к остальным: ВЕРНЫЙ ответ обязан давать баллы.

    Нужен потому, что соседние тесты слали поле `value`, которого сервер не читает
    (он ждёт `answer`): ответ молча считался неотвеченным и оценивался как неверный, а
    проверки на ноль баллов при этом оставались зелёными. То есть весь путь начисления
    баллов не был покрыт ни разу — тесты подтверждали не то, что проверяли."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t)
    a = _start(client, t, conv, "contest", {"quiz_id": quiz}).json()["id"]
    client.post(f"{A}/{a}/next", json={}, headers=t)

    q = client.get(f"{A}/{a}/questions", headers=b).json()["questions"][0]
    right = [o["id"] for o in q["options"] if o["text"] == "4"][0]
    r = client.post(f"{A}/{a}/answer", json={"answer": right}, headers=b)
    assert r.status_code == 200, r.text
    assert r.json()["correct"] is True, r.json()
    assert r.json()["gain"] > 0, r.json()

    client.post(f"{A}/{a}/finish", json={}, headers=t)
    board = client.get(f"{A}/{a}/results", headers=b).json()["results"]
    mine = [x for x in board if x["user_id"] == b_id][0]
    assert mine["score"] > 0 and mine["correct_count"] == 1, mine


def test_leaderboard_updates_live_for_everyone_during_the_contest(client):
    """Табло обязано жить ВО ВРЕМЯ игры, а не только при открытии.

    🔥 Тот же класс, что был у шкалы прогресса: `board` считается в проекции состояния и
    попадает клиенту один раз — кнопка «Лидерборд» посреди соревнования показывала пустой
    список всем, кто не перезаходил в активность. Проверяем, что кадр после ответа несёт
    свежее табло."""
    sent = []
    from app.routers import activities as act_mod
    orig = act_mod._emit

    def _spy(db, conv_id, data):
        sent.append(data)
        return orig(db, conv_id, data)

    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    quiz = _quiz(client, t)
    a = _start(client, t, conv, "contest", {"quiz_id": quiz}).json()["id"]
    client.post(f"{A}/{a}/next", json={}, headers=t)
    q = client.get(f"{A}/{a}/questions", headers=b).json()["questions"][0]
    right = [o["id"] for o in q["options"] if o["text"] == "4"][0]

    act_mod._emit = _spy
    try:
        client.post(f"{A}/{a}/answer", json={"answer": right}, headers=b)
    finally:
        act_mod._emit = orig

    frames = [d for d in sent if "board" in (d.get("payload") or {})]
    assert frames, f"кадр с табло не ушёл: {sent}"
    board = frames[-1]["payload"]["board"]
    assert any(r["user_id"] == b_id and r["score"] > 0 for r in board), board


def test_poll_card_names_who_asked(client):
    """Опрос подписан АВТОРОМ, и подпись приходит с сервера.

    Опрос постит система (в ленте у него нет обычного отправителя), поэтому без подписи
    было непонятно, чей это вопрос — старосты, куратора или преподавателя. От этого
    зависит, отвечать ли на него вообще: «когда удобно пересдача» от преподавателя и от
    однокурсника — разные по весу вопросы.

    Собирать ФИО на клиенте нечем: id автора (`teach:…`) в ленте есть, а справочника
    пользователей у страницы нет — за именем пришлось бы ходить отдельным запросом на
    каждое сообщение."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    _start(client, t, conv, "poll",
           {"question": "Когда пересдача?", "options": ["Вторник", "Четверг"]})

    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    cell = [m for m in msgs if m["kind"] == "poll"][0]["activity"]
    assert cell.get("host_name"), f"опрос без подписи автора: {cell}"
    #Имя, а не id: `teach:petrova` в ленте читается как техническая строка.
    assert not cell["host_name"].startswith("teach:"), cell["host_name"]

    #🔒 Подпись автора НЕ открывает распределение: это разные вещи, и добавление первой
    #не имеет права протащить вторую.
    assert "tally" not in cell, "чужие голоса студенту показывать нельзя"


# ── Итог опроса: кто победил и кому это видно ────────────────────────────────────────
def test_expired_poll_finishes_itself_and_keeps_the_votes(client):
    """🔥 Опрос с истёкшим сроком обязан ЗАВЕРШИТЬСЯ САМ.

    Раньше подметались только таймеры и соревнования. Опрос со сроком доходил до нуля и
    оставался `running` навсегда: снимок итогов (`final_counts`) сохраняется ТОЛЬКО при
    завершении, а живые голоса лежат в памяти процесса. Значит при первом же рестарте
    сервера такой опрос закрывался общей уборкой `_sweep_stale` уже БЕЗ снимка — и
    показывал «проголосовало: 0», хотя голосовала вся группа. Это не косметика: голоса
    людей пропадали.

    Заодно это условие видимости итога: пока опрос числится идущим, победителя показывать
    нельзя (см. соседний тест)."""
    from datetime import datetime, timedelta, timezone
    from app.routers import activities as act

    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "poll",
               {"question": "Пересдача?", "options": ["Вторник", "Четверг"],
                "duration_s": 60}).json()["id"]
    assert client.post(f"{A}/{a}/vote", json={"choice": 1}, headers=b).status_code == 200
    assert client.post(f"{A}/{a}/vote", json={"choice": 1}, headers=c).status_code == 200

    #Переводим стрелки, а не спим: минимальный срок опроса — 30 секунд (осознанное
    #продуктовое ограничение), и ждать их в тесте незачем.
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    act.activity_state.patch(a, {"ends_at": past})
    #Уборка идёт попутно на обычном запросе — отдельного планировщика у нас нет намеренно.
    client.get(f"{A}/running", params={"conversation_id": conv}, headers=t)

    msgs = client.get(f"/web/messenger/chats/{conv}/messages", headers=t).json()["messages"]
    cell = [m for m in msgs if m["kind"] == "poll"][0]["activity"]
    assert cell["status"] == "finished", f"опрос со сроком остался идущим: {cell}"
    assert cell["voted_count"] == 2, cell
    assert cell["tally"] == [0, 2], cell


def test_finished_poll_shows_the_result_to_everyone_by_default(client):
    """Итог завершённого опроса виден всем — иначе кубок победителя не увидит никто,
    кроме автора, и вся затея бессмысленна.

    🔒 Обещание даётся ЗАРАНЕЕ, до голосования: подпись под опросом так и говорит, и
    именно поэтому раскрытие после завершения не является нарушением. Задним числом
    менять правила нельзя, а объявить их вперёд — можно."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "poll",
               {"question": "Пересдача?", "options": ["Вторник", "Четверг"]}).json()["id"]
    client.post(f"{A}/{a}/vote", json={"choice": 0}, headers=b)
    client.post(f"{A}/{a}/vote", json={"choice": 1}, headers=c)

    #Пока идёт — распределения студенту НЕ отдаём ни при каких настройках: подсвеченный
    #лидер тянет за собой голоса, и опрос перестаёт мерить то, ради чего затеян.
    live = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    assert "tally" not in [m for m in live if m["kind"] == "poll"][0]["activity"]

    client.post(f"{A}/{a}/finish", json={}, headers=t)
    done = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    cell = [m for m in done if m["kind"] == "poll"][0]["activity"]
    assert cell.get("tally") == [1, 1], f"итог завершённого опроса не доехал до студента: {cell}"


def test_author_can_keep_the_result_private(client):
    """Обратная половина: автор вправе оставить итог себе — например, когда вопрос
    чувствительный. Тогда после завершения студент по-прежнему не видит распределения."""
    admin, (t_id, t), (b_id, b), (c_id, c) = _setup(client)
    conv = _group(client, t, [b_id, c_id])
    a = _start(client, t, conv, "poll",
               {"question": "Понятна ли тема?", "options": ["Да", "Нет"],
                "reveal_results": False}).json()["id"]
    client.post(f"{A}/{a}/vote", json={"choice": 1}, headers=b)
    client.post(f"{A}/{a}/finish", json={}, headers=t)

    done = client.get(f"/web/messenger/chats/{conv}/messages", headers=b).json()["messages"]
    cell = [m for m in done if m["kind"] == "poll"][0]["activity"]
    assert "tally" not in cell, f"автор запретил раскрытие, а итог всё равно виден: {cell}"
    #Автору — как и раньше, всегда.
    mine = client.get(f"/web/messenger/chats/{conv}/messages", headers=t).json()["messages"]
    assert "tally" in [m for m in mine if m["kind"] == "poll"][0]["activity"]
