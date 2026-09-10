"""
test_subject_hours.py — учебные часы «пройдено X из Y» (3.1).

Главное, что здесь защищается, — ПРАВИЛО ПОДСЧЁТА. Лекция лежит в базе двумя строками
(hour=1 и hour=2 — одна пара, разбитая по академическим часам), практика — одной. Наивный
подсчёт строк дал бы лекции 2 часа, а практике 1, то есть счётчик врал бы ровно вдвое на
половине занятий. Считаем уникальные пары (тип, номер) × 2 — решение заказчика.

Остальное: ДЗ часов не даёт (делается вне аудитории), план задаёт только админ, и
незаданный план отдаётся нулём, а не выдумывается.
"""
from conftest import make_admin, make_teacher, assign_teacher


def _lesson(client, headers, ltype, number, hour=0, group="ИС-21", subject="Математика"):
    r = client.post("/sync/push", json={"changes": {"lessons": [
        {"id": f"L-{ltype}-{number}-{hour}", "group_name": group, "subject": subject,
         "type": ltype, "number": number, "topic": "т", "date": "01.09.2025",
         "hour": hour}]}}, headers=headers)
    assert r.status_code == 200, r.text


def _group(client, admin, name="ИС-21", subjects=("Математика",)):
    r = client.post("/web/admin/groups", json={"name": name, "subjects": list(subjects)},
                    headers=admin)
    assert r.status_code == 200, r.text


def test_lecture_and_practice_give_equal_hours(client):
    """Лекция (2 строки) и практика (1 строка) — это по одной паре, то есть по 2 часа."""
    admin = make_admin(client)
    teach = make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    _lesson(client, teach, "Лекция", 1, hour=1)
    _lesson(client, teach, "Лекция", 1, hour=2)     #та же пара, второй академический час
    _lesson(client, teach, "Практика", 1)

    r = client.get("/web/teacher/journal?group=ИС-21&subject=Математика", headers=teach)
    assert r.status_code == 200, r.text
    assert r.json()["hours"]["done"] == 4, r.json()["hours"]


def test_homework_gives_no_hours(client):
    """ДЗ в счёт часов не идёт: «в счёт часов идут лекции и практики»."""
    admin = make_admin(client)
    teach = make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    _lesson(client, teach, "Практика", 1)
    _lesson(client, teach, "ДЗ", 1)

    r = client.get("/web/teacher/journal?group=ИС-21&subject=Математика", headers=teach)
    assert r.json()["hours"]["done"] == 2, r.json()["hours"]


def test_plan_is_zero_until_admin_sets_it(client):
    """Незаданный план — ноль, а не выдуманное число: клиент по нему прячет строку."""
    admin = make_admin(client)
    teach = make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    _lesson(client, teach, "Практика", 1)
    r = client.get("/web/teacher/journal?group=ИС-21&subject=Математика", headers=teach)
    assert r.json()["hours"]["total"] == 0


def test_admin_sets_hours_and_teacher_sees_them(client):
    admin = make_admin(client)
    _group(client, admin)
    teach = make_teacher(client, admin, subjects=["Математика"])
    _lesson(client, teach, "Практика", 1)

    r = client.post("/web/admin/group-hours",
                    json={"group": "ИС-21", "hours": {"Математика": 72},
                         "teachers": {"Математика": "teach:teacher1"}}, headers=admin)
    assert r.status_code == 200, r.text

    r = client.get("/web/teacher/journal?group=ИС-21&subject=Математика", headers=teach)
    assert r.json()["hours"] == {"done": 2, "total": 72}, r.json()["hours"]


def test_admin_sees_plan_and_progress_together(client):
    """В админке рядом с полем плана видно уже пройденное — иначе план вбивают вслепую."""
    admin = make_admin(client)
    _group(client, admin)
    teach = make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    _lesson(client, teach, "Лекция", 1, hour=1)
    _lesson(client, teach, "Лекция", 1, hour=2)
    client.post("/web/admin/group-hours",
                json={"group": "ИС-21", "hours": {"Математика": 72}}, headers=admin)

    r = client.get("/web/admin/group-hours?group=ИС-21", headers=admin)
    assert r.status_code == 200, r.text
    row = next(s for s in r.json()["subjects"] if s["subject"] == "Математика")
    #§ролей: строка теперь несёт и назначение препода (teacher_id/teacher_name), и ЗЕТ
    #(docs/done/PLAN-ZET.md: zet=None — не задано, zet_hint — подсказка по формуле часов/36),
    #и раздельное обучение (3.6.1: split/teacher_id_2/teacher_name_2 — по умолчанию нет).
    assert row == {"subject": "Математика", "hours_total": 72, "hours_done": 2,
                   "teacher_id": "teach:teacher1", "teacher_name": "Преподаватель",
                   "zet": None, "zet_hint": 2.0,
                   "split": False, "teacher_id_2": "", "teacher_name_2": ""}, row


def test_saving_hours_twice_replaces_not_duplicates(client):
    """Ключ детерминированный: повторное сохранение ЗАМЕНЯЕТ план, а не плодит строки."""
    admin = make_admin(client)
    _group(client, admin)
    for value in (72, 36):
        r = client.post("/web/admin/group-hours",
                        json={"group": "ИС-21", "hours": {"Математика": value}}, headers=admin)
        assert r.status_code == 200, r.text

    r = client.get("/web/admin/group-hours?group=ИС-21", headers=admin)
    rows = [s for s in r.json()["subjects"] if s["subject"] == "Математика"]
    assert len(rows) == 1 and rows[0]["hours_total"] == 36, rows


def test_teacher_cannot_set_hours(client):
    """Часы задаёт только администратор (учебный план — не епархия преподавателя)."""
    admin = make_admin(client)
    _group(client, admin)
    teach = make_teacher(client, admin, subjects=["Математика"])
    r = client.post("/web/admin/group-hours",
                    json={"group": "ИС-21", "hours": {"Математика": 72}}, headers=teach)
    assert r.status_code == 403, r.text


def test_student_sees_hours_per_subject(client):
    admin = make_admin(client)
    _group(client, admin)
    teach = make_teacher(client, admin, subjects=["Математика"])
    _lesson(client, teach, "Практика", 1)
    client.post("/web/admin/group-hours",
                json={"group": "ИС-21", "hours": {"Математика": 72}}, headers=admin)
    client.post("/web/admin/students", json={
        "login": "ivanova", "surname": "Иванова", "name": "Мария", "group": "ИС-21",
        "password": "studpass1"}, headers=admin)

    tok = client.post("/auth/login", json={"login": "ivanova", "password": "studpass1"})
    sh = {"Authorization": f"Bearer {tok.json()['access_token']}", "X-Client": "web"}
    r = client.get("/web/student/journal", headers=sh)
    assert r.status_code == 200, r.text
    subj = next(s for s in r.json()["subjects"] if s["subject"] == "Математика")
    assert subj["hours"] == {"done": 2, "total": 72}, subj["hours"]


def test_hours_sync_to_desktop(client):
    """Часы входят в SYNC_MODELS — иначе нативный журнал на ПК показывал бы пустоту."""
    admin = make_admin(client)
    _group(client, admin)
    client.post("/web/admin/group-hours",
                json={"group": "ИС-21", "hours": {"Математика": 72}}, headers=admin)

    r = client.get("/sync/pull", headers=admin)
    assert r.status_code == 200, r.text
    rows = r.json()["changes"].get("subject_hours") or []
    assert any(x["group_name"] == "ИС-21" and x["subject"] == "Математика"
               and x["hours_total"] == 72 for x in rows), rows
