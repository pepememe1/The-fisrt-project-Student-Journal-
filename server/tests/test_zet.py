"""
test_zet.py — Зачётные единицы трудоёмкости (ЗЕТ), docs/done/PLAN-ZET.md.

Проверяем: зачёт ЗЕТ только при сдаче предмета (экзамен/зачёт по последней попытке,
без экзамена — средний практик >= 3.0), ЗЕТ не заданы администратором → предмет нигде
не участвует, отчёт группы (сортировка/eligible), перевод на курс (промоут) и границы
доступа (403 для чужого студента/родителя, 200 куратору/админу).
"""
from conftest import make_admin, make_teacher, assign_teacher


def _group(client, admin, name="ИС-21", subjects=("Математика", "Физика")):
    r = client.post("/web/admin/groups", json={"name": name, "subjects": list(subjects)},
                    headers=admin)
    assert r.status_code == 200, r.text


def _student(client, admin, login="ivanova", surname="Иванова", name="Мария", group="ИС-21"):
    r = client.post("/web/admin/students", json={
        "login": login, "surname": surname, "name": name, "group": group,
        "password": "studpass1"}, headers=admin)
    assert r.status_code == 200, r.text
    tok = client.post("/auth/login", json={"login": login, "password": "studpass1"})
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}", "X-Client": "web"}


def _push_lesson(client, admin, lesson_id, group, subject, ltype, number=1,
                 retake_date="", extra=None):
    row = {"id": lesson_id, "group_name": group, "subject": subject, "type": ltype,
          "number": number, "topic": "т", "date": "01.09.2025", "retake_date": retake_date}
    if extra:
        row["extra"] = extra
    r = client.post("/sync/push", json={"changes": {"lessons": [row]}}, headers=admin)
    assert r.status_code == 200, r.text


def _push_grade(client, admin, lesson_id, surname, name, grade):
    r = client.post("/sync/push", json={"changes": {"grades": [
        {"id": f"{surname}|{name}|{lesson_id}", "student_f": surname, "student_n": name,
         "lesson_id": lesson_id, "grade": grade}]}}, headers=admin)
    assert r.status_code == 200, r.text


def _set_zet(client, admin, group, subject, zet, hours=72):
    r = client.post("/web/admin/group-hours", json={
        "group": group, "hours": {subject: hours}, "zet": {subject: zet}}, headers=admin)
    assert r.status_code == 200, r.text


def test_zet_hint_returned_but_not_saved(client):
    """PATCH часов без zet — сервер отдаёт zet_hint, но НЕ сохраняет его (только человек)."""
    admin = make_admin(client)
    _group(client, admin)
    client.post("/web/admin/group-hours",
                json={"group": "ИС-21", "hours": {"Математика": 72}}, headers=admin)
    r = client.get("/web/admin/group-hours?group=ИС-21", headers=admin)
    row = next(s for s in r.json()["subjects"] if s["subject"] == "Математика")
    assert row["zet"] is None and row["zet_hint"] == 2.0


def test_zet_earned_when_exam_passed(client):
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    stud = _student(client, admin)
    _set_zet(client, admin, "ИС-21", "Математика", 2.0)
    _push_lesson(client, admin, "E1", "ИС-21", "Математика", "Экзамен")
    _push_grade(client, admin, "E1", "Иванова", "Мария", "4 (Зачтено)")

    r = client.get("/web/student/zet", headers=stud)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["earned"] == 2.0 and body["total"] == 2.0 and body["pct"] == 100.0
    assert body["subjects"] == [{"subject": "Математика", "zet": 2.0, "earned": 2.0,
                                "state": "passed", "passed": True}]


def test_zet_not_earned_when_exam_failed(client):
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    stud = _student(client, admin)
    _set_zet(client, admin, "ИС-21", "Математика", 2.0)
    _push_lesson(client, admin, "E1", "ИС-21", "Математика", "Экзамен")
    _push_grade(client, admin, "E1", "Иванова", "Мария", "2 (Не зачтено)")

    r = client.get("/web/student/zet", headers=stud)
    body = r.json()
    assert body["earned"] == 0.0
    assert body["subjects"][0]["passed"] is False


def test_zet_earned_after_retake(client):
    """Провалил основную попытку, но пересдал — ЗЕТ засчитаны по ПОСЛЕДНЕЙ попытке."""
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    stud = _student(client, admin)
    _set_zet(client, admin, "ИС-21", "Математика", 2.0)
    _push_lesson(client, admin, "E1", "ИС-21", "Математика", "Экзамен", retake_date="10.09.2025")
    _push_grade(client, admin, "E1", "Иванова", "Мария", "2 (Не зачтено)")
    _push_grade(client, admin, "E1_retake", "Иванова", "Мария", "4 (Зачтено)")

    r = client.get("/web/student/zet", headers=stud)
    body = r.json()
    assert body["subjects"][0]["passed"] is True
    assert body["earned"] == 2.0


def test_zet_pending_without_exam_while_semester_runs(client):
    """ВАРИАНТ C (баг Влада, 26.08.2026): предмет без экзамена, пока идёт семестр и не
    пройдены плановые часы, — «ожидается», а НЕ засчитан. Одна оценка больше не закрывает
    весь предмет («одна оценка, а пишет, будто весь семестр прошёл»).

    Обратный ход: до варианта C средний 3.5 давал passed=True и earned=2.0 немедленно."""
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    stud = _student(client, admin)
    _set_zet(client, admin, "ИС-21", "Математика", 2.0, hours=72)  # большой план — одной парой не пройти
    _push_lesson(client, admin, "P1", "ИС-21", "Математика", "Практика", number=1)
    _push_lesson(client, admin, "P2", "ИС-21", "Математика", "Практика", number=2)
    _push_grade(client, admin, "P1", "Иванова", "Мария", "4")
    _push_grade(client, admin, "P2", "Иванова", "Мария", "3")

    r = client.get("/web/student/zet", headers=stud)
    body = r.json()
    assert body["earned"] == 0.0                       # средний 3.5, но семестр идёт — рано
    assert body["pending"] == 2.0                      # показываем «ожидается»
    assert body["subjects"][0]["state"] == "pending"
    assert body["subjects"][0]["passed"] is False


def test_zet_settled_without_exam_after_hours_completed(client):
    """Тот же предмет без экзамена, но плановые часы ПРОЙДЕНЫ (рубеж по практике) — итог
    подводится по среднему: сдан. План 2 ч = одна пара, одно проведённое занятие его
    закрывает."""
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    stud = _student(client, admin)
    _set_zet(client, admin, "ИС-21", "Математика", 2.0, hours=2)
    _push_lesson(client, admin, "P1", "ИС-21", "Математика", "Практика", number=1)
    _push_grade(client, admin, "P1", "Иванова", "Мария", "4")

    r = client.get("/web/student/zet", headers=stud)
    body = r.json()
    assert body["earned"] == 2.0                       # часы 2 из 2 пройдены → рубеж → сдан
    assert body["pending"] == 0.0
    assert body["subjects"][0]["state"] == "passed"


def test_zet_not_earned_when_practice_average_below_three(client):
    """Низкий средний в ИДУЩЕМ семестре — тоже не засчитан. Но состояние "pending", а не
    "failed": семестр не закончен, у студента ещё есть время исправить (вариант C)."""
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    stud = _student(client, admin)
    _set_zet(client, admin, "ИС-21", "Математика", 2.0)   # hours=72 по умолчанию — семестр идёт
    _push_lesson(client, admin, "P1", "ИС-21", "Математика", "Практика", number=1)
    _push_grade(client, admin, "P1", "Иванова", "Мария", "2")

    r = client.get("/web/student/zet", headers=stud)
    body = r.json()
    assert body["subjects"][0]["passed"] is False
    assert body["subjects"][0]["state"] == "pending"


def test_subject_without_zet_is_absent_from_summary(client):
    """ЗЕТ не задан администратором — предмет вообще НЕ появляется в сводке."""
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    stud = _student(client, admin)
    _push_lesson(client, admin, "P1", "ИС-21", "Математика", "Практика")
    _push_grade(client, admin, "P1", "Иванова", "Мария", "5")

    r = client.get("/web/student/zet", headers=stud)
    body = r.json()
    assert body["subjects"] == [] and body["total"] == 0.0


def test_subject_with_zet_but_no_lessons_yet_still_counts_toward_total(client):
    """§3.5.5, живой баг: предмет с заданным ЗЕТ, по которому препод ЕЩЁ не завёл ни
    одного занятия в этом термине, обязан войти в TOTAL (не сдан — да, но он ЧАСТЬ
    учебного плана семестра), а не исчезнуть из суммы целиком. Раньше пропадал —
    группа с 5 предметами по плану, но занятиями заведёнными только по одному,
    показывала «всего 2.9» вместо суммы всех пяти."""
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, login="teacher1", subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    stud = _student(client, admin)
    _set_zet(client, admin, "ИС-21", "Математика", 2.0)
    _set_zet(client, admin, "ИС-21", "Физика", 3.0)     # ни одного занятия по ней ещё нет
    _push_lesson(client, admin, "E1", "ИС-21", "Математика", "Экзамен")
    _push_grade(client, admin, "E1", "Иванова", "Мария", "4 (Зачтено)")

    r = client.get("/web/student/zet", headers=stud)
    body = r.json()
    assert body["total"] == 5.0             # 2.0 (Математика) + 3.0 (Физика), не 2.0
    assert body["earned"] == 2.0            # Физика ещё не сдана — занятий нет
    fiz = next(s for s in body["subjects"] if s["subject"] == "Физика")
    # Физика без занятий и без экзамена, семестр идёт → «ожидается» (не «не сдан»).
    assert fiz == {"subject": "Физика", "zet": 3.0, "earned": 0.0,
                   "state": "pending", "passed": False}


def test_zet_sum_and_pct_across_subjects(client):
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, login="teacher1", subjects=["Математика"])
    make_teacher(client, admin, login="teacher2", subjects=["Физика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    assign_teacher(client, admin, "teach:teacher2", "ИС-21", "Физика")
    stud = _student(client, admin)
    _set_zet(client, admin, "ИС-21", "Математика", 2.0)
    _set_zet(client, admin, "ИС-21", "Физика", 3.0)
    _push_lesson(client, admin, "E1", "ИС-21", "Математика", "Экзамен")
    _push_grade(client, admin, "E1", "Иванова", "Мария", "4 (Зачтено)")
    _push_lesson(client, admin, "E2", "ИС-21", "Физика", "Экзамен")
    _push_grade(client, admin, "E2", "Иванова", "Мария", "2 (Не зачтено)")

    r = client.get("/web/student/zet", headers=stud)
    body = r.json()
    assert body["earned"] == 2.0 and body["total"] == 5.0 and body["pct"] == 40.0


def test_group_zet_report_sorts_not_ready_first(client):
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    _set_zet(client, admin, "ИС-21", "Математика", 4.0)
    _student(client, admin, login="ivanova", surname="Иванова", name="Мария")
    _student(client, admin, login="petrov", surname="Петров", name="Пётр")
    _push_lesson(client, admin, "E1", "ИС-21", "Математика", "Экзамен")
    _push_grade(client, admin, "E1", "Иванова", "Мария", "2 (Не зачтено)")   # не сдала
    _push_grade(client, admin, "E1", "Петров", "Пётр", "5 (Зачтено)")        # сдал

    client.post("/web/admin/zet-thresholds",
               json={"group": "ИС-21", "min_zet": 4.0}, headers=admin)
    r = client.get("/web/admin/zet-thresholds?group=ИС-21", headers=admin)
    assert r.json()["min_zet"] == 4.0

    curator = make_teacher(client, admin, login="curator1", subjects=["Математика"])
    client.put("/web/admin/teachers/curator1", json={"curated_groups": ["ИС-21"]},
              headers=admin)
    r = client.get("/web/curator/zet-report?group=ИС-21", headers=curator)
    assert r.status_code == 200, r.text
    students = r.json()["students"]
    assert [s["display_name"] for s in students] == ["Иванова Мария", "Петров Пётр"]
    assert students[0]["eligible"] is False and students[0]["missing_zet"] == 4.0
    assert students[1]["eligible"] is True and students[1]["missing_zet"] == 0.0


def test_group_zet_report_includes_per_subject_breakdown(client):
    """CuratorView (3.6.1) показывает earned/zet ОДНОГО выбранного предмета (выпадающий
    список сверху), а не только сумму по всем — эта пара обязана прийти В КАЖДОЙ строке
    отчёта, чтобы фронт не ходил на сервер повторно при переключении предмета."""
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, login="teacher1", subjects=["Математика"])
    make_teacher(client, admin, login="teacher2", subjects=["Физика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    assign_teacher(client, admin, "teach:teacher2", "ИС-21", "Физика")
    _set_zet(client, admin, "ИС-21", "Математика", 2.0)
    _set_zet(client, admin, "ИС-21", "Физика", 3.0)
    _student(client, admin, login="ivanova", surname="Иванова", name="Мария")
    _push_lesson(client, admin, "E1", "ИС-21", "Математика", "Экзамен")
    _push_grade(client, admin, "E1", "Иванова", "Мария", "4 (Зачтено)")
    _push_lesson(client, admin, "E2", "ИС-21", "Физика", "Экзамен")
    _push_grade(client, admin, "E2", "Иванова", "Мария", "2 (Не зачтено)")

    curator = make_teacher(client, admin, login="curator1", subjects=["Математика"])
    client.put("/web/admin/teachers/curator1", json={"curated_groups": ["ИС-21"]},
              headers=admin)
    r = client.get("/web/curator/zet-report?group=ИС-21", headers=curator)
    assert r.status_code == 200, r.text
    row = r.json()["students"][0]
    assert row["earned"] == 2.0 and row["total"] == 5.0   # общая сумма — как раньше
    by_subject = {x["subject"]: x for x in row["subjects"]}
    assert by_subject["Математика"] == {"subject": "Математика", "zet": 2.0,
                                        "earned": 2.0, "state": "passed", "passed": True}
    # Физика: экзамен ПРОВАЛЕН — это уже итог "failed", а не «ожидается».
    assert by_subject["Физика"] == {"subject": "Физика", "zet": 3.0,
                                    "earned": 0.0, "state": "failed", "passed": False}


def test_promote_accepts_eligible_and_rejects_not_eligible(client):
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    _set_zet(client, admin, "ИС-21", "Математика", 4.0)
    _student(client, admin, login="ivanova", surname="Иванова", name="Мария")
    _student(client, admin, login="petrov", surname="Петров", name="Пётр")
    _push_lesson(client, admin, "E1", "ИС-21", "Математика", "Экзамен")
    _push_grade(client, admin, "E1", "Иванова", "Мария", "2 (Не зачтено)")
    _push_grade(client, admin, "E1", "Петров", "Пётр", "5 (Зачтено)")
    client.post("/web/admin/zet-thresholds",
               json={"group": "ИС-21", "min_zet": 4.0}, headers=admin)

    ids = {s["surname"]: s["id"] for s in
          client.get("/web/admin/students?group=ИС-21", headers=admin).json()["students"]}
    r = client.post("/web/admin/groups/promote", json={
        "group": "ИС-21", "student_ids": [ids["Иванова"], ids["Петров"]]}, headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["display_name"] for p in body["promoted"]] == ["Петров Пётр"]
    assert body["rejected"][0]["student_id"] == ids["Иванова"]
    assert "не хватает" in body["rejected"][0]["reason"]


def test_student_zet_is_only_own(client):
    """/web/student/zet отвечает от лица ТЕКУЩЕГО студента — своей группы, не чужой."""
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    _set_zet(client, admin, "ИС-21", "Математика", 2.0)
    stud1 = _student(client, admin, login="ivanova", surname="Иванова", name="Мария")
    _student(client, admin, login="petrov", surname="Петров", name="Пётр")
    _push_lesson(client, admin, "E1", "ИС-21", "Математика", "Экзамен")
    _push_grade(client, admin, "E1", "Петров", "Пётр", "5 (Зачтено)")

    r = client.get("/web/student/zet", headers=stud1)
    assert r.json()["subjects"][0]["passed"] is False   # Иванова НЕ сдала — свои, не Петрова


def test_curator_zet_report_403_for_non_curator(client):
    admin = make_admin(client)
    _group(client, admin)
    teach = make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    r = client.get("/web/curator/zet-report?group=ИС-21", headers=teach)
    assert r.status_code == 403, r.text


def test_zet_thresholds_admin_only(client):
    """НЕ куратор и НЕ админ — 403 (обычный преподаватель не решает вопрос перевода)."""
    admin = make_admin(client)
    _group(client, admin)
    teach = make_teacher(client, admin, subjects=["Математика"])
    r = client.post("/web/admin/zet-thresholds",
                    json={"group": "ИС-21", "min_zet": 10}, headers=teach)
    assert r.status_code == 403, r.text


def test_curator_can_set_threshold_and_promote(client):
    """Куратор СВОЕЙ группы — не только админ — может задать порог и перевести (это
    решение куратор принимает каждый семестр сам, без администратора)."""
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    curator = make_teacher(client, admin, login="curator1", subjects=["Математика"])
    client.put("/web/admin/teachers/curator1", json={"curated_groups": ["ИС-21"]},
              headers=admin)
    _set_zet(client, admin, "ИС-21", "Математика", 2.0)
    _student(client, admin, login="ivanova", surname="Иванова", name="Мария")
    _push_lesson(client, admin, "E1", "ИС-21", "Математика", "Экзамен")
    _push_grade(client, admin, "E1", "Иванова", "Мария", "5 (Зачтено)")

    r = client.post("/web/admin/zet-thresholds",
                    json={"group": "ИС-21", "min_zet": 2.0}, headers=curator)
    assert r.status_code == 200, r.text

    sid = client.get("/web/admin/students?group=ИС-21", headers=admin).json()["students"][0]["id"]
    r = client.post("/web/admin/groups/promote",
                    json={"group": "ИС-21", "student_ids": [sid]}, headers=curator)
    assert r.status_code == 200, r.text
    assert len(r.json()["promoted"]) == 1


def test_curator_cannot_promote_group_they_dont_curate(client):
    admin = make_admin(client)
    _group(client, admin, name="ИС-21")
    _group(client, admin, name="ИС-22")
    curator = make_teacher(client, admin, login="curator1", subjects=["Математика"])
    client.put("/web/admin/teachers/curator1", json={"curated_groups": ["ИС-21"]},
              headers=admin)
    r = client.post("/web/admin/zet-thresholds",
                    json={"group": "ИС-22", "min_zet": 5.0}, headers=curator)
    assert r.status_code == 403, r.text


def test_promote_writes_audit(client):
    admin = make_admin(client)
    _group(client, admin)
    make_teacher(client, admin, subjects=["Математика"])
    assign_teacher(client, admin, "teach:teacher1", "ИС-21", "Математика")
    _set_zet(client, admin, "ИС-21", "Математика", 2.0)
    _student(client, admin, login="ivanova", surname="Иванова", name="Мария")
    _push_lesson(client, admin, "E1", "ИС-21", "Математика", "Экзамен")
    _push_grade(client, admin, "E1", "Иванова", "Мария", "5 (Зачтено)")
    sid = client.get("/web/admin/students?group=ИС-21", headers=admin).json()["students"][0]["id"]

    client.post("/web/admin/groups/promote",
               json={"group": "ИС-21", "student_ids": [sid]}, headers=admin)
    r = client.get("/web/admin/server-info", headers=admin)  # sanity: server stayed up
    assert r.status_code == 200


def test_zet_threshold_clear_with_null(client):
    admin = make_admin(client)
    _group(client, admin)
    client.post("/web/admin/zet-thresholds",
               json={"group": "ИС-21", "min_zet": 10.0}, headers=admin)
    client.post("/web/admin/zet-thresholds",
               json={"group": "ИС-21", "min_zet": None}, headers=admin)
    r = client.get("/web/admin/zet-thresholds?group=ИС-21", headers=admin)
    assert r.json()["min_zet"] is None


# ── Порог доезжает до программы синком (3.7) ──────────────────────────────────────
def test_zet_threshold_travels_by_sync_so_desktop_knows_the_bar(client):
    """Порог перевода обязан приезжать в программу через `/sync/pull`.

    Пока `ZetThreshold` не было в SYNC_MODELS, внутри десктопа получалось хуже, чем
    «неизвестно»: `/web/student/zet` честно отдавал `min_zet=None`, страница молча не
    рисовала строку порога, и студент видел свой баланс БЕЗ единого ограничения рядом.
    Читается это ровно как «перевод возможен при любом балансе» — то есть отсутствие
    данных выглядело утверждением, а не пробелом.

    Проверяем не «ключ есть в словаре», а СКВОЗНОЙ путь: админ задал порог на сервере —
    строка приехала в дельту синка со своим значением.
    """
    admin = make_admin(client)
    _group(client, admin, "ИС-21")
    r = client.post("/web/admin/zet-thresholds",
                    json={"group": "ИС-21", "year": "2025/2026", "semester": 2,
                          "min_zet": 27.5},
                    headers=admin)
    assert r.status_code == 200, r.text

    pull = client.get("/sync/pull", headers={**admin, "X-Client": "desktop"})
    assert pull.status_code == 200, pull.text
    rows = (pull.json().get("changes") or {}).get("zet_thresholds")
    assert rows, "порог не попал в дельту — внутри программы он останется неизвестным"
    assert any(abs((x.get("min_zet") or 0) - 27.5) < 1e-6 for x in rows), rows


def test_zet_threshold_editor_is_proxied_not_written_locally():
    """Редактор порогов обязан уходить на бой, а не в локальное зеркало.

    Пара к тесту выше и намеренно с ДРУГИМ ответом: читать порог можно синком, а вот
    ЗАПИСЬ внутри программы уходит в `local_app.db` — копию, из которой обратного пути
    на боевой сервер нет. Сохранённый там порог не потерялся бы «когда-нибудь», а
    исчез бы сразу и молча, и админ узнал бы об этом от куратора через семестр.
    """
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    for p in (root, root / "ui"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from desktop import local_api
    assert "/web/admin/zet-thresholds" in local_api._PROXY_PREFIXES
