"""
test_course_rollover.py — ПЕРЕВОД ГРУППЫ НА НОВЫЙ КУРС: старое в архив, преподы отцеплены.

Живая жалоба Влада (05.09.2026): «ещё в июле была старая программа обучения за второй
курс, первого сентября программа сменилась, но некоторые предметы остались те же — в
итоге сейчас видно старые оценки»; «при смене курса все преподаватели открепляются от
предметов — сейчас этого не происходит».

Что здесь держится (и что покраснеет, если правку откатить):
  • занятие БЕЗ штампа периода перестаёт считаться текущим — именно оно и протаскивало
    прошлогодние оценки в витрину студента, статистику, ЗЕТ, долги и ответы Вектора;
  • период берётся из ДАТЫ занятия, а не «какой-нибудь»: мартовское занятие уходит в
    весну прошлого года, а не в период, из которого группа уходит;
  • предмет, ОСТАВШИЙСЯ в новой программе под тем же именем, тоже теряет старые оценки —
    ровно этот случай `_archive_dropped_subjects` не ловил никогда;
  • преподаватель после перевода группу НЕ видит, включая путь через мост назначений;
  • группу, которую НЕ переводили, мост не теряет — иначе правка выбила бы журнал у всех
    сразу, как 30.07.2026;
  • группа, пропавшая из расписания, называется «закончили обучение», а не «курс не
    определяется»;
  • формула календаря ОДНА: `db.term_for_date` и `db.default_term` обязаны сходиться.

⚠️ ОБРАТНЫЙ ХОД проверен откатом: вернуть `stamp_untermed_lessons` к пустому телу —
краснеют тесты 3 и 4; вернуть мост без `_drop_groups_with_explicit_assignments` —
краснеет тест 5.
"""
from datetime import datetime, timezone

from app import course_rollover as CR
from app import schedule_web
from app import webdata as W
from app.db import SessionLocal, default_term, term_for_date
from app.models import Group, Lesson, SubjectHours
from conftest import assign_teacher, make_admin, make_teacher


def _session():
    return SessionLocal()


def _group(client, admin, name, subjects=(), enrollment_year=None):
    payload = {"id": f"grp:{name}", "name": name, "subjects": list(subjects)}
    if enrollment_year is not None:
        payload["enrollment_year"] = enrollment_year
    r = client.post("/sync/push", json={"changes": {"groups": [payload]}}, headers=admin)
    assert r.status_code == 200, r.text


def _lesson(db, lid, group, subject, *, date="", year="", semester=0):
    db.add(Lesson(id=lid, group_name=group, subject=subject, type="Практика", number=1,
                  topic="", date=date, hour=1, year=year, semester=int(semester),
                  updated_at="2026-07-01T00:00:00+00:00", deleted=False))


def _cur_term():
    db = _session()
    try:
        return W.current_term(W.load_config(db))
    finally:
        db.close()


# 1. Период занятия берётся из его ДАТЫ ────────────────────────────────────────────
def test_untermed_lesson_gets_the_term_of_its_own_date(client):
    """Занятие 12 марта — это весна прошлого учебного года, а не «предыдущий период».

    Разница не косметическая: у группы, которую перевели зимой, «предыдущий период» и
    «период, в котором занятие реально было» — РАЗНЫЕ, и подстановка одного вместо
    другого отправила бы занятие в семестр, где его не было.
    """
    admin = make_admin(client)
    _group(client, admin, "К74/1", subjects=["Математика"])
    db = _session()
    try:
        _lesson(db, "l-march", "К74/1", "Математика", date="12.03.2026")
        db.commit()
        CR.stamp_untermed_lessons(db, "К74/1", ("2099/2100", 1))
        db.commit()
        row = db.get(Lesson, "l-march")
        assert (row.year, row.semester) == ("2025/2026", 2)
    finally:
        db.close()


# 2. Даты нет — берём период, из которого группа уходит ─────────────────────────────
def test_lesson_without_a_date_falls_back_to_the_term_being_left(client):
    """Гадать не на чем — но «сегодняшним» такое занятие быть не имеет права.

    Это и есть смысл запасного значения: точнее мы не знаем, а оставить занятие
    беспериодным значит оставить дефект.
    """
    admin = make_admin(client)
    _group(client, admin, "К74/1", subjects=["Математика"])
    db = _session()
    try:
        _lesson(db, "l-nodate", "К74/1", "Математика", date="")
        db.commit()
        CR.stamp_untermed_lessons(db, "К74/1", ("2024/2025", 1))
        db.commit()
        row = db.get(Lesson, "l-nodate")
        assert (row.year, row.semester) == ("2024/2025", 1)
    finally:
        db.close()


# 3. Главное: после перевода прошлое не считается текущим ───────────────────────────
def test_after_advance_old_lessons_stop_counting_as_current(client):
    """Тот самый дефект: занятие без штампа проходило `current_term_lessons` насквозь.

    ⚠️ Проверяем ИМЕННО эту функцию, а не «журнал пустой»: на ней стоят витрина
    студента, статистика, ЗЕТ, долги, пропуски и Вектор — то есть все экраны, где
    старые оценки и были видны.
    """
    admin = make_admin(client)
    _group(client, admin, "К74/1", subjects=["Математика"])
    db = _session()
    try:
        _lesson(db, "l-old", "К74/1", "Математика", date="12.03.2026")
        db.commit()
        before = W.current_term_lessons(db, "К74/1", W.group_lessons(db, "К74/1"))
        assert [l.id for l in before] == ["l-old"], "до правки занятие считалось текущим"
        assert CR.advance(db, "К74/1")["lessons_stamped"] == 1
        db.commit()
        after = W.current_term_lessons(db, "К74/1", W.group_lessons(db, "К74/1"))
        assert [l.id for l in after] == []
    finally:
        db.close()


# 4. Предмет ОСТАЛСЯ в программе — оценки всё равно в архив ─────────────────────────
def test_a_subject_that_survived_the_new_programme_still_loses_its_old_grades(client):
    """Дословно из жалобы: «некоторые предметы остались те же — видно старые оценки».

    Фильтр по ИМЕНИ предмета (`current_subject_lessons`) такой случай пропускает по
    построению — предмет-то в плане. Значит закрыть его может только период.
    """
    admin = make_admin(client)
    _group(client, admin, "К74/1", subjects=["Математика"])
    db = _session()
    try:
        _lesson(db, "l-2course", "К74/1", "Математика", date="12.03.2026")
        db.commit()
        CR.advance(db, "К74/1")
        db.commit()
        #Предмет в плане остался — именно это и было условием дефекта.
        assert "Математика" in W.group_plan_subjects(db, "К74/1")
        kept = W.current_subject_lessons(db, "К74/1", W.group_lessons(db, "К74/1"))
        assert [l.id for l in kept] == ["l-2course"], "фильтр по имени его и не должен убирать"
        current = W.current_term_lessons(db, "К74/1", kept)
        assert current == []
    finally:
        db.close()


# 5. Преподаватель откреплён — включая путь через мост ──────────────────────────────
def test_teacher_loses_the_group_after_the_course_change(client):
    """«При смене курса все преподы открепляются — сейчас этого не происходит».

    Двух проверок мало по отдельности: снять `teacher_id` недостаточно, потому что мост
    `_assignments_fallback` возвращает группу по прошлогодним ЗАНЯТИЯМ, у которых
    фильтра по периоду нет вовсе.
    """
    admin = make_admin(client)
    make_teacher(client, admin, login="t1", subjects=("Математика",))
    _group(client, admin, "К74/1", subjects=["Математика"])
    ty, ts = _cur_term()
    assign_teacher(client, admin, "teach:t1", "К74/1", "Математика", ty, ts)
    db = _session()
    try:
        _lesson(db, "l-old", "К74/1", "Математика", date="12.03.2026")
        db.commit()
        assert ("К74/1", "Математика") in W.teacher_assignments(db, "teach:t1", ty, ts)
        CR.advance(db, "К74/1")
        db.commit()
        assert W.teacher_assignments(db, "teach:t1", ty, ts) == []
    finally:
        db.close()


# 6. Мост не ломается у тех, кого НЕ переводили ─────────────────────────────────────
def test_the_bridge_still_works_for_a_group_that_was_not_advanced(client):
    """Мост поставлен после боевого простоя 30.07.2026, когда журнал опустел у ВСЕХ.

    Правка обязана выключать его ПОГРУППНО. Глобальное сужение до текущего периода
    увело бы всех преподавателей во вторую ветку фолбэка — «все группы, где числится мой
    предмет», то есть весь колледж.
    """
    admin = make_admin(client)
    make_teacher(client, admin, login="t1", subjects=("Математика",))
    _group(client, admin, "К74/1", subjects=["Математика"])
    _group(client, admin, "К75/1", subjects=["Математика"])
    ty, ts = _cur_term()
    db = _session()
    try:
        #Ни одного явного назначения — работает мост (занятия обеих групп).
        _lesson(db, "l-a", "К74/1", "Математика", date="12.03.2026")
        _lesson(db, "l-b", "К75/1", "Математика", date="12.03.2026")
        db.commit()
        both = W.teacher_assignments(db, "teach:t1", ty, ts)
        assert {g for g, _s in both} == {"К74/1", "К75/1"}
        CR.advance(db, "К74/1")
        db.commit()
        left = W.teacher_assignments(db, "teach:t1", ty, ts)
        assert {g for g, _s in left} == {"К75/1"}
    finally:
        db.close()


# 7. Пропала из расписания — «закончили обучение» ───────────────────────────────────
def test_group_missing_from_the_schedule_is_called_graduated(client, monkeypatch):
    """Формулировка описывает ГРУППУ, а не наше незнание.

    Требование дословно: «если группа закончила последний курс — она вся идёт в архив и
    пишет „закончили обучение“, признак — её не видно в расписании».
    ⚠️ Основание пишем РЯДОМ: вывод без доказательства нечем перепроверить, а портал
    пропадает и по своим причинам.
    """
    admin = make_admin(client)
    monkeypatch.setattr(schedule_web, "groups_by_course_cached", lambda category="": {})
    _group(client, admin, "К74/1", subjects=["Математика"])
    db = _session()
    try:
        row = db.get(Group, "grp:К74/1")
        row.last_course = 4
        row.last_course_year = "2025/2026"
        db.commit()
        from app import group_archive as GA
        found = [c for c in GA.candidates(db) if c["group"] == "К74/1"]
        assert found, "группа без курса в расписании обязана попасть в кандидаты"
        assert found[0]["reason"].startswith(GA.REASON_GRADUATED)
        assert "расписании" in found[0]["reason"]
    finally:
        db.close()


# 8. Архив группы сам откручивает преподавателей ────────────────────────────────────
def test_archiving_a_group_detaches_its_teachers(client):
    """Выпустившаяся группа не может оставаться чьей-то нагрузкой.

    Пока строки часов текущего периода несли `teacher_id`, архивная группа продолжала
    появляться у преподавателя в выборе и открывала журнал — то есть архив был только
    надписью.
    """
    admin = make_admin(client)
    make_teacher(client, admin, login="t1", subjects=("Математика",))
    _group(client, admin, "К74/1", subjects=["Математика"])
    ty, ts = _cur_term()
    assign_teacher(client, admin, "teach:t1", "К74/1", "Математика", ty, ts)
    r = client.post("/web/admin/groups/archive",
                    json={"group": "К74/1", "archived": True}, headers=admin)
    assert r.status_code == 200, r.text
    db = _session()
    try:
        rows = db.query(SubjectHours).filter(SubjectHours.group_name == "К74/1",
                                             SubjectHours.year == ty).all()
        assert rows, "строка часов обязана остаться — архив показывает, а не прячет"
        assert all(not r.teacher_id and not r.teacher_id_2 for r in rows)
        assert W.teacher_assignments(db, "teach:t1", ty, ts) == []
    finally:
        db.close()


# 9. Возврат из архива возвращает и мост ────────────────────────────────────────────
def test_unarchiving_lifts_the_explicit_assignments_mark(client):
    """Дверь наружу обязана возвращать группу РАБОЧЕЙ, а не пустой.

    Группа, вернувшаяся из архива без единого преподавателя и без объяснения, куда они
    делись, — это отказ, который чинят правкой базы руками.
    """
    admin = make_admin(client)
    _group(client, admin, "К74/1", subjects=["Математика"])
    client.post("/web/admin/groups/archive",
                json={"group": "К74/1", "archived": True}, headers=admin)
    db = _session()
    try:
        assert db.get(Group, "grp:К74/1").assignments_reset_term
    finally:
        db.close()
    client.post("/web/admin/groups/archive",
                json={"group": "К74/1", "archived": False}, headers=admin)
    db = _session()
    try:
        assert db.get(Group, "grp:К74/1").assignments_reset_term == ""
    finally:
        db.close()


# 11. Формула календаря — ОДНА ─────────────────────────────────────────────────────
def test_the_calendar_formula_lives_in_exactly_one_place():
    """`default_term()` обязан быть `term_for_date(сейчас)`, а не второй копией.

    Докстринг самой `default_term` предупреждает, чем кончаются копии формулы («обязана
    совпадать ДО СИМВОЛА»). Тест проверяет СВОЙСТВО, а не текст: разойдутся — покраснеет.
    """
    assert default_term() == term_for_date(datetime.now(timezone.utc))
    #Границы календаря, ради которых формула и переписывалась дважды.
    assert term_for_date(datetime(2026, 8, 31)) == ("2025/2026", 2)
    assert term_for_date(datetime(2026, 9, 1)) == ("2026/2027", 1)
    assert term_for_date(datetime(2027, 1, 15)) == ("2026/2027", 1)


# 15. Молчащий портал не выпускает полколледжа ──────────────────────────────────────
def test_a_silent_portal_does_not_graduate_everyone(client, monkeypatch):
    """🔥 «Нет в расписании» значит «выпустилась» ТОЛЬКО пока это единичный случай.

    Возражение Полковника 06.09.2026: портал уже ложился на день — из-за этого и заведён
    `stale_groups`, — и тогда курс пропадает СРАЗУ У ВСЕХ. Формулировка «закончили
    обучение» в такой день объявила бы десятки живых групп выпустившимися, а решение
    администратор принимает именно по ней.

    ⚠️ Порог по МАССОВОСТИ, а не по силе признака — тот же приём, что у подтверждения
    массовой подмены предметов.
    """
    from app import group_archive as GA
    admin = make_admin(client)
    monkeypatch.setattr(schedule_web, "groups_by_course_cached", lambda category="": {})
    names = ["К74/1", "К74/2", "К74/3", "К74/4"]
    for name in names:
        _group(client, admin, name, subjects=["Математика"])
    db = _session()
    try:
        for name in names:
            row = db.get(Group, f"grp:{name}")
            row.last_course = 4
            row.last_course_year = "2025/2026"
        db.commit()
        reasons = {c["group"]: c["reason"] for c in GA.candidates(db)}
        assert set(names) <= set(reasons), "группы обязаны остаться кандидатами"
        for name in names:
            assert GA.REASON_PORTAL_SILENT in reasons[name], reasons[name]
            assert GA.REASON_GRADUATED not in reasons[name], (
                "молчащий портал не имеет права объявлять группу выпустившейся")
    finally:
        db.close()


def test_one_missing_group_is_still_called_graduated(client, monkeypatch):
    """Единичный случай — это факт, и называть его надо фактом.

    Иначе порог съел бы сам признак: у колледжа всегда есть группы, которые действительно
    доучились, и прятать их за «портал молчит» значило бы не показать их никогда.
    """
    from app import group_archive as GA
    admin = make_admin(client)
    #Портал знает про соседей и НЕ знает про К74/1 — значит он жив, а группы нет.
    monkeypatch.setattr(schedule_web, "groups_by_course_cached",
                        lambda category="": {3: ["К75/1", "К75/2"]})
    for name in ["К74/1", "К75/1", "К75/2"]:
        _group(client, admin, name, subjects=["Математика"])
    db = _session()
    try:
        row = db.get(Group, "grp:К74/1")
        row.last_course = 4
        row.last_course_year = "2025/2026"
        db.commit()
        found = [c for c in GA.candidates(db) if c["group"] == "К74/1"]
        assert found and GA.REASON_GRADUATED in found[0]["reason"], found
    finally:
        db.close()


# ── АВТОМАТИЧЕСКИЙ ПЕРЕВОД (06.09.2026, решение Влада «кнопку убрать») ──────────────
def test_autorun_advances_a_group_that_has_past_lessons(client):
    """Перевод происходит САМ, без единого нажатия.

    ⚠️ Проверяем СВОЙСТВО, ради которого всё делалось: после автопрогона занятие прошлого
    периода перестаёт считаться текущим. «Функция отработала» — не то же самое.
    """
    admin = make_admin(client)
    _group(client, admin, "К74/1", subjects=["Математика"])
    db = _session()
    try:
        _lesson(db, "l-old", "К74/1", "Математика", date="12.03.2026")
        db.commit()
        assert CR.autorun(db), "автомат обязан был перевести группу"
        db.commit()
        assert W.current_term_lessons(db, "К74/1", W.group_lessons(db, "К74/1")) == []
    finally:
        db.close()


def test_autorun_is_idempotent_within_one_academic_year(client):
    """Второй запуск сервера в том же учебном году ничего не переводит повторно.

    Без этого открепление преподавателей случалось бы при КАЖДОМ старте, то есть
    администратор не смог бы назначить их вовсе — назначения жили бы до перезапуска.
    """
    admin = make_admin(client)
    _group(client, admin, "К74/1", subjects=["Математика"])
    db = _session()
    try:
        _lesson(db, "l-old", "К74/1", "Математика", date="12.03.2026")
        db.commit()
        assert len(CR.autorun(db)) == 1
        db.commit()
        assert CR.autorun(db) == [], "автомат перевёл группу второй раз"
    finally:
        db.close()


def test_autorun_never_touches_a_freshly_created_group(client):
    """🔒 Группа, заведённая сегодня, переводу НЕ подлежит.

    У неё нет метки — и по одному этому признаку автомат «перевёл» бы её на первом же
    запуске сервера, открепив преподавателей, которых администратор только что назначил.
    «Нет прошлого» значит «переводить не с чего», а не «переводить пора».
    """
    admin = make_admin(client)
    make_teacher(client, admin, login="t1", subjects=("Математика",))
    _group(client, admin, "К74/1", subjects=["Математика"])
    ty, ts = _cur_term()
    assign_teacher(client, admin, "teach:t1", "К74/1", "Математика", ty, ts)
    db = _session()
    try:
        #Занятие ТЕКУЩЕГО периода — прошлого у группы нет.
        _lesson(db, "l-now", "К74/1", "Математика", date="01.10.2026", year=ty, semester=ts)
        db.commit()
        assert CR.autorun(db) == []
        db.commit()
        assert ("К74/1", "Математика") in W.teacher_assignments(db, "teach:t1", ty, ts), \
            "автомат открепил преподавателя у только что заведённой группы"
    finally:
        db.close()


def test_autorun_does_not_ask_the_portal(client, monkeypatch):
    """🔒 Автомат обязан работать в день, когда портал лежит.

    Именно портальный признак («курса нет в расписании») и был причиной, по которой
    перевод держали ручным. Автомат смотрит на КАЛЕНДАРЬ и на собственные данные группы,
    поэтому молчащий портал ему безразличен.

    🔥 СЧИТАЕМ ВЫЗОВЫ, А НЕ РОНЯЕМ ИХ ИСКЛЮЧЕНИЕМ (06.09.2026, нашёл Полковник). Первая
    версия этого теста подменяла обращение к расписанию функцией, бросающей AssertionError,
    — и была ЗЕЛЁНОЙ рядом с настоящим походом в портал: `webdata.portal_course` обёрнут
    в свой `try/except`, который любую ошибку глотает. То есть сторож, заведённый против
    похода в портал, не мог его заметить в принципе. Наш записанный класс «зелёный тест
    рядом с дефектом», пойманный на собственном стороже.
    """
    calls = []
    real = schedule_web.groups_by_course_cached
    monkeypatch.setattr(schedule_web, "groups_by_course_cached",
                        lambda *a, **kw: (calls.append(a), real(*a, **kw))[1])
    admin = make_admin(client)
    _group(client, admin, "К74/1", subjects=["Математика"])
    db = _session()
    try:
        _lesson(db, "l-old", "К74/1", "Математика", date="12.03.2026")
        db.commit()
        assert len(CR.autorun(db)) == 1
        assert calls == [], f"автомат обратился к расписанию портала {len(calls)} раз"
    finally:
        db.close()


def test_manual_endpoint_is_gone(client):
    """Ручки ручного перевода больше нет — кнопку убрали вместе с ней.

    Оставленная ручка без единого вызывающего — наш обычный класс «обещание без
    вызывающего»: она не проверяется, стареет и однажды делает не то, что написано.
        ⚠️ Проверяем СПИСОК МАРШРУТОВ, а не код ответа: соседний `PUT /admin/groups/{name}`
    ловит тот же путь и отвечает 405, то есть по коду «удалено» и «есть, но другим
    методом» неразличимы — а нам нужно именно отсутствие.
    """
    from app.main import app
    paths = set()
    def _walk(router):
        for r in getattr(router, "routes", []):
            paths.add(getattr(r, "path", ""))
            inner = getattr(r, "original_router", None) or getattr(r, "router", None)
            if inner is not None:
                _walk(inner)
    _walk(app.router)
    assert not any("advance-course" in p for p in paths), sorted(p for p in paths if "advance" in p)


# ── Создание группы: курс + год + программа обучения ───────────────────────────────
def test_group_is_created_by_course_and_gets_the_enrollment_year(client):
    """Администратор вводит КУРС, система хранит ГОД ПОСТУПЛЕНИЯ.

    Хранить курс нельзя: он обязан расти по календарю сам, и хранимое число пришлось бы
    править руками раз в год — ровно этим и был вызван баг «в расписании третий курс, а в
    профилях второй».
    """
    admin = make_admin(client)
    r = client.post("/web/admin/groups", json={"name": "К74/9", "course": 3}, headers=admin)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["course"] == 3
    ty, ts = _cur_term()
    from study_hours import course_and_semester
    assert course_and_semester(out["enrollment_year"], ty, ts)[0] == 3
    db = _session()
    try:
        row = db.get(Group, "grp:К74/9")
        assert row.enrollment_year == out["enrollment_year"]
        #Свидетельство курса ставится СРАЗУ — иначе первый же сентябрь прошёл бы мимо.
        assert row.last_course == 3 and row.last_course_year == ty
    finally:
        db.close()


def test_group_created_by_year_reports_the_same_course(client):
    """Год и курс — одно число в двух видах, и разойтись они не имеют права."""
    admin = make_admin(client)
    ty, _ = _cur_term()
    from study_hours import enrollment_year_for_course
    year = enrollment_year_for_course(2, ty, 1)
    r = client.post("/web/admin/groups",
                    json={"name": "К74/8", "enrollment_year": year}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["course"] == 2


def test_course_out_of_range_is_refused_by_the_server(client):
    """🔒 Границу курса ставит СЕРВЕР, а не форма.

    Та же ручка доступна из десктопа, из офлайн-очереди и просто curl'ом. Курс 0 давал
    год поступления «в будущем», от него `group_course` возвращает None — у группы
    навсегда «курс неизвестен», и ни одной ошибки при этом не показывалось.
    """
    admin = make_admin(client)
    for bad in (0, -3, 99):
        r = client.post("/web/admin/groups", json={"name": f"К-{bad}", "course": bad},
                        headers=admin)
        assert r.status_code == 400, (bad, r.status_code, r.text)
    #Разумный курс по-прежнему проходит — граница не должна съесть рабочий случай.
    assert client.post("/web/admin/groups", json={"name": "К-ok", "course": 4},
                       headers=admin).status_code == 200
