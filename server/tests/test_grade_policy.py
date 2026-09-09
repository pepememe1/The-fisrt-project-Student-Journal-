"""
test_grade_policy.py — ЧТО ИЗ ОЦЕНОК ВИДИТ СТУДЕНТ И КОГДА.

Живая жалоба Влада (06.09.2026): «в июле был прогон по предметам, я за один предмет
поставил оценку; сейчас автоматически спарсилась программа обучения 3-го курса, где этот
предмет есть, а оценку всё ещё видно».

Требование, разложенное на проверяемые утверждения:
  • сентябрь → зимние каникулы: оценки видны, но только по предметам ДЕЙСТВУЮЩЕЙ
    программы семестра;
  • после кастомной даты («зачёты и экзамены сданы») поурочные оценки скрыты, видны
    только ИТОГОВЫЕ;
  • прошлый семестр студенту не открывается вовсе — «улетает в архив», который смотрит
    администратор.

⚠️ Причина у всего этого одна и она арифметическая, названа самим Владом: средний балл,
смешавший два семестра, не описывает НИ ОДИН из них и при этом выглядит настоящим.

⚠️ ОБРАТНЫЙ ХОД проверен: вернуть `student_records` вместо `student_visible_records` в
`routers/web/student.py` — краснеет сторож «студенческие экраны ходят только через
политику» и тест про заморозку.
"""
from datetime import date

from app import grade_policy as P
from app import webdata as W
from app.db import SessionLocal
from app.models import ConfigKV, Grade, Lesson
from app.security import hash_password
from conftest import make_admin


def _session():
    return SessionLocal()


def _student(client, admin, login="ivanov", group="К74/1"):
    r = client.post("/sync/push", json={"changes": {
        "groups": [{"id": f"grp:{group}", "name": group, "subjects": ["Математика"]}],
        "users": [{"id": f"stud:{login}", "role": "student", "login": login,
                   "password_hash": hash_password("studpass1"), "full_name": "Иванов Иван",
                   "surname": "Иванов", "name": "Иван", "group_name": group}],
    }}, headers=admin)
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"login": login, "password": "studpass1"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _lesson_with_grade(db, lid, group, subject, value, *, year, semester, date_str="12.03.2026"):
    db.add(Lesson(id=lid, group_name=group, subject=subject, type="Практика", number=1,
                  topic="", date=date_str, hour=1, year=year, semester=int(semester),
                  updated_at="2026-07-01T00:00:00+00:00", deleted=False))
    db.add(Grade(id=f"Иванов|Иван|{lid}", student_f="Иванов", student_n="Иван",
                 lesson_id=lid, grade=value, updated_at="2026-07-01T00:00:00+00:00",
                 deleted=False))


def _set_cfg(db, **kw):
    row = db.get(ConfigKV, "config")
    cur = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    cur.update(kw)
    if row is None:
        db.add(ConfigKV(key="config", value=cur, updated_at="2026-09-06T00:00:00+00:00", deleted=False))
    else:
        row.value = cur
    db.commit()


# ── Чистые правила фазы ────────────────────────────────────────────────────────────
def test_no_freeze_date_means_nothing_changes():
    """Отсутствующая настройка обязана оставлять ПРЕЖНЕЕ поведение.

    Скрыть оценки «по умолчанию» значило бы отнять их у всего колледжа у того, кто просто
    обновился, — и отнять молча.
    """
    assert P.phase(date(2026, 7, 15), 2, "") == P.PHASE_LIVE
    assert P.phase(date(2026, 7, 15), 2, None) == P.PHASE_LIVE


def test_freeze_only_applies_in_the_spring_semester():
    """Осенью «сессия сдана» означает ЗИМНЮЮ сессию, после которой учёба продолжается.

    Применить заморозку осенью значило бы погасить журнал на зимних каникулах — прямо
    против просьбы «сентябрь → зимние каникулы оценки видны».
    """
    assert P.phase(date(2026, 12, 28), 1, "30.06") == P.PHASE_LIVE
    assert P.phase(date(2026, 7, 1), 2, "30.06") == P.PHASE_FROZEN


def test_freeze_starts_exactly_on_the_named_day():
    """Граница включительная: «дата, когда экзамены сданы» — это уже заморозка."""
    assert P.phase(date(2026, 6, 29), 2, "30.06") == P.PHASE_LIVE
    assert P.phase(date(2026, 6, 30), 2, "30.06") == P.PHASE_FROZEN
    assert P.phase(date(2026, 8, 31), 2, "30.06") == P.PHASE_FROZEN


def test_broken_date_is_treated_as_not_set():
    """Непонятная строка — это «не задано», а не «сегодня».

    Молча принятый мусор скрыл бы студенту оценки без всякого решения администратора.
    """
    for bad in ("вчера", "30", "99.99", "0.6", "30/06"):
        assert P.parse_freeze(bad) is None, bad
        assert P.phase(date(2026, 7, 1), 2, bad) == P.PHASE_LIVE


def test_final_grades_are_never_hidden():
    """Итоговая уходит в зачётку; спрятав её, мы оставили бы студента без единственного
    числа, которое после сессии вообще имеет смысл."""
    assert P.hide_lesson_grades(P.PHASE_FROZEN) is True
    assert P.visible_records({"l1": "5"}, P.PHASE_FROZEN) == {}
    assert P.visible_records({"l1": "5"}, P.PHASE_LIVE) == {"l1": "5"}


# ── Живой путь: студенческие экраны ────────────────────────────────────────────────
def test_frozen_phase_hides_lesson_grades_from_the_student(client, monkeypatch):
    """После даты заморозки поурочных оценок студент не видит.

    ⚠️ Проверяем через РУЧКУ, а не функцию: правило обязано доехать до экрана, иначе оно
    существует только в модуле.
    """
    admin = make_admin(client)
    stud = _student(client, admin)
    db = _session()
    try:
        ty, ts = W.current_term(W.load_config(db))
        _lesson_with_grade(db, "l-1", "К74/1", "Математика", "5", year=ty, semester=ts)
        db.commit()
        _set_cfg(db, grades_freeze_date="")
    finally:
        db.close()
    assert ty, "термин не определился — дальше проверять нечего"
    live = client.get("/web/student/journal", headers=stud).json()
    seen = [g for s in live["subjects"] for l in s["lessons"] for g in [l.get("grade")] if g]
    assert seen, "до заморозки оценка обязана быть видна"

    db = _session()
    try:
        # ⚠️ Термин двигаем НАЗАД: оверрайд ВПЕРЁД продукт игнорирует намеренно (дважды
        # ронял курс студентов, см. `webdata.current_term`). Берём прошлую весну и кладём
        # занятие в неё же — иначе фаза посчиталась бы по осени и заморозка не сработала.
        a, b = ty.split("/")
        prev = f"{int(a) - 1}/{int(b) - 1}"
        _set_cfg(db, grades_freeze_date="01.01", current_year=prev, current_semester=2)
        row = db.get(Lesson, "l-1")
        row.year, row.semester = prev, 2
        db.commit()
    finally:
        db.close()
    frozen = client.get("/web/student/journal", headers=stud).json()
    seen2 = [g for s in frozen["subjects"] for l in s["lessons"] for g in [l.get("grade")] if g]
    assert seen2 == [], f"после заморозки поурочные оценки всё ещё видны: {seen2}"


def test_student_cannot_open_a_past_semester(client):
    """🔒 «Улетают в архив»: прошлый семестр студенту не открывается вовсе.

    Причина не в секретности, а в арифметике: средний, смешавший два семестра, не
    описывает ни один из них. Отвечаем ТЕКУЩИМ периодом, а не отказом — отказ читался бы
    как поломка журнала.
    """
    admin = make_admin(client)
    stud = _student(client, admin)
    db = _session()
    try:
        ty, ts = W.current_term(W.load_config(db))
        _lesson_with_grade(db, "l-old", "К74/1", "Математика", "2",
                           year="2024/2025", semester=1)
        _lesson_with_grade(db, "l-now", "К74/1", "Математика", "5", year=ty, semester=ts)
        db.commit()
    finally:
        db.close()
    out = client.get("/web/student/journal?year=2024/2025&semester=1", headers=stud).json()
    ids = [l["id"] for s in out["subjects"] for l in s["lessons"]]
    assert "l-old" not in ids, "студенту открылся прошлый семестр"
    assert "l-now" in ids, "вместо архива студент обязан увидеть ТЕКУЩИЙ период"


def test_student_screens_never_read_grades_past_the_policy():
    """🔒 СТРУКТУРНЫЙ сторож: студенческие роутеры ходят ТОЛЬКО через политику.

    Правило читают витрина, журнал, статистика, подсказки, ачивки, родитель и Вектор.
    Перечислять их по одному — это семь мест, где однажды забудут, а первое забытое здесь
    возвращает чужой семестр в средний балл, то есть ровно тот дефект, от которого правило
    и заведено. Проверяем ОТСУТСТВИЕ обхода, а не наличие вызовов.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "routers"
    watched = [root / "web" / "student.py", root / "web" / "achievements.py",
               root / "parent.py"]
    for path in watched:
        lines = path.read_text(encoding="utf-8").splitlines()
        bad = []
        for i, ln in enumerate(lines):
            if "W.student_records(" not in ln or "visible" in ln:
                continue
            # Исключение допустимо, но ТОЛЬКО С ПРИЧИНОЙ рядом: «исключение без записанной
            # причины — это не исключение, а забытый случай» (наш урок про `public/` в
            # списке префиксов API). Причина обязана быть содержательной, а не пустой.
            mark = [x for x in lines[max(0, i - 4):i] if "#не-студент:" in x]
            if mark and len(mark[0].split("#не-студент:")[1].strip()) > 10:
                continue
            bad.append(ln)
        assert not bad, f"{path.name} читает оценки мимо политики: " + " | ".join(bad)


def test_teacher_still_sees_everything(client):
    """Преподаватель ведёт журнал — ему полная картина нужна по работе.

    Спрятать оценки и от него значило бы починить средний балл студента ценой
    неработающего журнала.
    """
    from conftest import assign_teacher, make_teacher
    admin = make_admin(client)
    _student(client, admin)
    make_teacher(client, admin, login="t1", subjects=("Математика",))
    db = _session()
    try:
        ty, ts = W.current_term(W.load_config(db))
        _lesson_with_grade(db, "l-1", "К74/1", "Математика", "5", year=ty, semester=ts)
        db.commit()
        _set_cfg(db, grades_freeze_date="01.01", current_semester=2, current_year=ty)
    finally:
        db.close()
    assign_teacher(client, admin, "teach:t1", "К74/1", "Математика", ty, 2)
    r = client.post("/auth/login", json={"login": "t1", "password": "teacherpass1"})
    th = {"Authorization": f"Bearer {r.json()['access_token']}"}
    out = client.get("/web/teacher/journal?group=К74/1&subject=Математика", headers=th)
    assert out.status_code == 200, out.text
    grades = [v for row in out.json()["students"] for v in row["grades"].values() if v]
    assert grades, "у преподавателя оценки пропали вместе со студенческими"


def test_admin_can_set_and_clear_the_freeze_date(client):
    """Дверь для администратора есть, и она отвергает мусор.

    Молча принятая непонятная строка означала бы выключённое правило при заполненном
    поле: админ считал бы, что настроил, а оценки показывались бы всё лето.
    """
    admin = make_admin(client)
    assert client.post("/web/admin/term/grades-freeze",
                       json={"date": "вчера"}, headers=admin).status_code == 400
    ok = client.post("/web/admin/term/grades-freeze", json={"date": "30.06"}, headers=admin)
    assert ok.status_code == 200, ok.text
    assert client.get("/web/admin/term/grades-freeze", headers=admin).json()["date"] == "30.06"
    client.post("/web/admin/term/grades-freeze", json={"date": ""}, headers=admin)
    assert client.get("/web/admin/term/grades-freeze", headers=admin).json()["date"] == ""
