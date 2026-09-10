"""
admin_read.py — Администратор: справочники и списки (чтение).

Часть пакета `routers/web` (разрезан в 3.6: один файл на 4288 строк правили
62 коммита за полгода — он и был главным источником конфликтов при
одновременной работе). Общий роутер и хелперы — в `_common.py`; порядок
регистрации маршрутов задаёт `__init__.py`.
"""
from ._common import *      # noqa: F401,F403 — общий router, модели, хелперы


# АДМИНИСТРАТОР ───────────────────────────────────────────────────────────────────
@router.get("/admin/overview")
def admin_overview(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Сводка по учреждению — счётчики сущностей."""
    def count(model, **flt):
        q = db.query(model).filter(model.deleted == False)  # noqa: E712
        for k, v in flt.items():
            q = q.filter(getattr(model, k) == v)
        return q.count()
    return {
        "teachers": count(User, role="teacher"),
        "students": count(User, role="student"),
        "admins": count(User, role="admin"),
        "groups": count(Group),
        "subjects": count(Subject),
        "lessons": count(Lesson),
        "grades": count(Grade),
    }


@router.get("/admin/teachers")
def admin_teachers(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(User).filter(
        User.role == "teacher", User.deleted == False).order_by(User.full_name).all()  # noqa: E712
    info = _contact_info(db, [u.login for u in rows])
    return {"teachers": [dict(
        {"id": u.id, "login": u.login, "name": W.display_name(u),
         "subjects": list(u.subjects or []), "curated_groups": list(u.curated_groups or []),
         #Когда пароль выдан в последний раз. Сам пароль показать нельзя (в базе хеш),
         #а дата отвечает на реальный вопрос админа: «свежий он или полугодовой».
         "password_set_at": u.password_set_at or ""},
        **info.get(u.login, {})) for u in rows]}


@router.get("/admin/students")
def admin_students(group: str = Query(""),
                   _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    q = db.query(User).filter(User.role == "student", User.deleted == False)  # noqa: E712
    if group:
        q = q.filter(User.group_name == group)
    rows = q.order_by(User.group_name, User.surname, User.name).all()
    info = _contact_info(db, [u.login for u in rows])
    #Курс студента = курс его ГРУППЫ. Считаем по разу на группу, а не на человека:
    #иначе на списке в несколько сотен строк это сотни одинаковых запросов.
    #⚠️ Источник ОДИН — webdata.group_course (портал ВСГУТУ, при его молчании — год
    #поступления + текущий термин). Своей арифметики здесь нет намеренно: курс уже
    #показывается в расписании и на главной студента, и третья версия расчёта
    #разъехалась бы с ними — это уже случалось трижды подряд, см. историю границы
    #учебного года в db.default_term и порядок источников в webdata.group_course.
    cfg = W.load_config(db)
    course_by_group = {}
    for g in {u.group_name for u in rows if u.group_name}:
        course_by_group[g] = W.group_course(db, g, cfg)
    #name отдаём как есть (полная форма — совместимость), плюс имя и отчество РАЗДЕЛЬНО.
    return {"students": [dict(
        #id нужен для привязки родителя: связь ведётся по НЕИЗМЕНЯЕМОМУ users.id, а не по
        #ФИО (фамилия меняется — см. §12 миграции оценок, там это уже стоило истории).
        {"id": u.id, "login": u.login, "surname": u.surname, "name": u.name,
         "first_name": W.first_name(u), "patronymic": W.patronymic_of(u),
         "group": u.group_name,
         #«ДД.ММ», без года: поздравлению год не нужен, а полная дата рождения — другой
         #уровень чувствительности. Пусто честно значит «админ не заполнял».
         "birthday": u.birthday or "",
         #null — год поступления группы не задан администратором (сортировка по курсу
         #такие строки покажет отдельно, а не выдумает им первый курс).
         "course": course_by_group.get(u.group_name),
         #см. пояснение у преподавателей выше
         "password_set_at": u.password_set_at or ""},
        **info.get(u.login, {})) for u in rows]}


@router.get("/admin/groups")
def admin_groups(include_archived: bool = Query(False),
                 _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Список групп. Архивные по умолчанию НЕ показываются.

    ⚠️ Флаг именно на ВКЛЮЧЕНИЕ, а не на исключение: иначе архив не значил бы ничего —
    выпустившиеся группы продолжали бы висеть в каждом выпадающем списке, ради чего его
    и заводили. Кому нужен полный перечень (перенос студента в живую группу из
    архивной), тот просит явно."""
    q = db.query(Group).filter(Group.deleted == False)  # noqa: E712
    if not include_archived:
        q = q.filter(Group.archived == False)  # noqa: E712
    rows = q.order_by(Group.name).all()
    out = []
    for g in rows:
        n = db.query(User).filter(User.role == "student", User.group_name == g.name,
                                  User.deleted == False).count()  # noqa: E712
        out.append({"name": g.name, "subjects": list(g.subjects or []), "students": n,
                    "category": g.category or "college",
                    "archived": bool(g.archived)})
    return {"groups": out}


# --- Учебные часы группы (план на семестр) ------------------------------------------
@router.get("/admin/group-hours")
def admin_group_hours(group: str = Query(...), year: str = Query(""), semester: int = Query(0),
                      _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Предметы группы с плановыми и уже пройденными часами за семестр.

    Пройденные показываем и админу: без них поле «часов на семестр» заполняется вслепую,
    а «занятий уже 30, а план 24» — единственный способ заметить опечатку сразу."""
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, year, semester)
    grp = db.get(Group, f"grp:{group}")
    if grp is None or grp.deleted:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    lessons = W.group_lessons(db, group, year=ty, semester=ts)
    by_subject = {}
    for l in lessons:
        by_subject.setdefault(l.subject, []).append(l)
    #Назначения преподов на эту группу за термин — одним запросом, не по одному на предмет.
    hrows = {r.subject: r for r in db.query(SubjectHours).filter(
        SubjectHours.group_name == group, SubjectHours.year == ty,
        SubjectHours.semester == ts, SubjectHours.deleted == False).all()}  # noqa: E712
    tids = {r.teacher_id for r in hrows.values() if r.teacher_id}
    tids |= {r.teacher_id_2 for r in hrows.values() if r.teacher_id_2}
    tnames = {u.id: W.display_name(u) for u in db.query(User).filter(User.id.in_(tids)).all()} if tids else {}
    out = []
    for subj in list(grp.subjects or []):
        row = hrows.get(subj)
        tid = (row.teacher_id if row else "") or ""
        tid2 = (row.teacher_id_2 if row else "") or ""
        hrs = W.hours_plan(db, group, subj, ty, ts)
        zet = row.zet if row else None
        out.append({"subject": subj, "hours_total": hrs,
                    "hours_done": W.hours_done(by_subject.get(subj, [])),
                    "teacher_id": tid, "teacher_name": tnames.get(tid, ""),
                    #ЗЕТ (docs/done/PLAN-ZET.md): zet — уже подтверждённое администратором
                    #значение (None — не задано, тогда интерфейс строку не показывает);
                    #zet_hint — ТОЛЬКО подсказка по формуле, никогда не подставляется сама.
                    "zet": zet, "zet_hint": W.study_hours.zet_hint(hrs),
                    #Раздельное обучение (§ролей, 3.6.1): флаг ставит куратор во вкладке
                    #«Курирование» — здесь только читаем и, если он стоит, даём занять
                    #второго преподавателя (см. admin_set_group_hours::teachers2).
                    "split": bool(row and row.split),
                    "teacher_id_2": tid2, "teacher_name_2": tnames.get(tid2, "")})
    return {"group": group, "term": {"year": ty, "semester": ts}, "subjects": out}


@router.get("/admin/group-archive")
def admin_group_archive(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Архив групп: уже убранные + кандидаты на уборку.

    Кандидаты — ПРЕДЛОЖЕНИЕ, а не свершившийся факт (см. `group_archive`): группа
    пропадает из расписания и при сбое портала, а за ней живые студенты и оценки.
    Ручка ничего не меняет — её можно открывать сколько угодно раз.

    ⚠️ Списка «нужен перевод на курс» здесь БОЛЬШЕ НЕТ (06.09.2026). Перевод стал
    автоматическим (`course_rollover.autorun` на старте сервера), а список существовал
    ровно затем, чтобы кормить кнопку. Оставить его значило бы показывать администратору
    очередь дел, которых у него нет."""
    from ... import group_archive as GA
    return {"archived": GA.archived_list(db), "candidates": GA.candidates(db)}


@router.get("/admin/group-archive/detail")
def admin_group_archive_detail(group: str = Query(...),
                               _admin: User = Depends(require_admin),
                               db: Session = Depends(get_db)):
    """Карточка архивной группы: предметы, студенты, преподаватели, кураторы.

    ⚠️ Имя группы приходит ПАРАМЕТРОМ ЗАПРОСА, а не сегментом пути: Starlette
    раскодирует `%2F` ДО роутинга, и «К74/1» разваливает путь на лишний сегмент."""
    from ... import group_archive as GA
    data = GA.detail(db, group)
    if data is None:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    return data


@router.get("/admin/group-subject-archive")
def admin_group_subject_archive(group: str = Query(...),
                                _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Архив предметов группы по семестрам (живой запрос 3.6.1: «текущие должны
    перезаписаться [реимпортом плана], а старые улететь в архив, где видны в
    админке»). Один снимок на каждый (year, semester), где у группы есть хоть какой-то
    след — активная ИЛИ погашенная строка `SubjectHours`, либо занятия.

    ⚠️ ТЕКУЩИЙ термин показывает ОБА состояния разом: действующий план (`active=true`)
    и то, что последний реимпорт/правка плана только что вытеснили (`active=false`,
    строка гашена — см. `write._archive_dropped_subjects`) — это и есть «архив» в
    буквальном смысле запроса, а не только старые годы. Прошлые термины отдают то, что
    реально велось тогда (все их строки исторически активны — гашение затрагивает
    только ТЕКУЩИЙ термин, история не переписывается)."""
    grp = db.get(Group, f"grp:{group}")
    if grp is None or grp.deleted:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    cfg = W.load_config(db)
    cur_y, cur_s = W.current_term(cfg)

    all_rows = db.query(SubjectHours).filter(SubjectHours.group_name == group).all()
    lesson_terms = (db.query(Lesson.year, Lesson.semester)
                    .filter(Lesson.group_name == group, Lesson.deleted == False,  # noqa: E712
                            Lesson.year != "").distinct().all())
    term_keys = {(r.year, int(r.semester or 0)) for r in all_rows if r.year}
    term_keys |= {(y, int(s or 0)) for y, s in lesson_terms if y}
    term_keys.add((cur_y, cur_s))

    tids = {r.teacher_id for r in all_rows if r.teacher_id}
    tnames = {u.id: W.display_name(u) for u in db.query(User).filter(User.id.in_(tids)).all()} if tids else {}

    terms_out = []
    for y, s in sorted(term_keys, key=lambda t: (t[0], t[1]), reverse=True):
        is_current = (y, s) == (cur_y, cur_s)
        rows_here = [r for r in all_rows if r.year == y and int(r.semester or 0) == s]
        seen = {r.subject for r in rows_here}
        subs = [{"subject": r.subject, "hours_total": r.hours_total or 0, "zet": r.zet,
                "teacher_name": tnames.get(r.teacher_id, ""), "active": not r.deleted}
               for r in rows_here]
        if is_current:
            #Предметы плана без своей строки часов — план без цифр всё равно план.
            for subj in (grp.subjects or []):
                if subj not in seen:
                    subs.append({"subject": subj, "hours_total": 0, "zet": None,
                                "teacher_name": "", "active": True})
                    seen.add(subj)
        elif not rows_here:
            #Прошлый термин совсем без строк часов — предметы только из занятий.
            for (subj,) in (db.query(Lesson.subject).filter(
                    Lesson.group_name == group, Lesson.year == y, Lesson.semester == s,
                    Lesson.deleted == False, Lesson.subject != "").distinct().all()):  # noqa: E712
                if subj not in seen:
                    subs.append({"subject": subj, "hours_total": 0, "zet": None,
                                "teacher_name": "", "active": True})
                    seen.add(subj)
        subs.sort(key=lambda x: (not x["active"], x["subject"]))
        terms_out.append({"year": y, "semester": s, "is_current": is_current, "subjects": subs})

    return {"group": group, "current": {"year": cur_y, "semester": cur_s}, "terms": terms_out}


@router.post("/admin/group-hours")
def admin_set_group_hours(payload: dict = Body(...),
                          _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Сохранить часы + назначение препода + ЗЕТ по предметам группы:
    {group, year, semester, hours: {предмет: N}, teachers: {предмет: teacher_id | ""},
    teachers2: {предмет: teacher_id | ""}, zet: {предмет: float | null}}.

    Пишем ПАЧКОЙ (кнопка «Сохранить» в админке правит сразу несколько предметов) — иначе
    полтора десятка запросов на одно нажатие и половинчатое состояние при обрыве связи.
    `teachers` — §ролей препод↔предмет↔группа: единственный источник правды «кто ведёт
    какую группу по какому предмету», см. webdata.teacher_assignments. `teachers2` —
    ВТОРОЙ преподаватель (подгруппа 2) раздельного обучения (3.6.1); принимается ТОЛЬКО
    если куратор уже поставил галочку «раздельное обучение» на этом предмете
    (SubjectHours.split — см. curator.py::curator_set_subject_split), иначе 400: часы
    редактор не место заводить раздельное обучение, только назначать в него людей.
    `zet` — docs/done/PLAN-ZET.md: null (или ключ отсутствует) — не трогать/не задано, число —
    подтверждённое администратором значение (подсказка zet_hint сюда НЕ подставляется
    автоматически, только человеком)."""
    group = (payload.get("group") or "").strip()
    hours = payload.get("hours") or {}
    teachers = payload.get("teachers") or {}
    teachers2 = payload.get("teachers2") or {}
    zets = payload.get("zet") or {}
    if not group or not isinstance(hours, dict) or not isinstance(teachers, dict) \
            or not isinstance(teachers2, dict) or not isinstance(zets, dict):
        raise HTTPException(status_code=400, detail="Нужны group и hours")
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, payload.get("year") or "", int(payload.get("semester") or 0))
    #Пустая строка teacher_id / null ЗЕТ = «снять назначение»/«снять ЗЕТ» — ЗНАЧИМОЕ
    #намерение, поэтому обходим объединение ключей hours∪teachers∪teachers2∪zet, а не
    #только те, где задано число часов.
    subjects_touched = ({(s or "").strip() for s in hours}
                        | {(s or "").strip() for s in teachers}
                        | {(s or "").strip() for s in teachers2}
                        | {(s or "").strip() for s in zets})
    subjects_touched.discard("")
    saved = 0
    for subject in subjects_touched:
        hid = subject_hours_id(group, subject, ty, ts)
        row = db.get(SubjectHours, hid)
        if row is None:
            row = SubjectHours(id=hid, group_name=group, subject=subject, year=ty, semester=ts)
            db.add(row)
        if subject in hours:
            try:
                row.hours_total = max(0, int(hours[subject] or 0))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400,
                                    detail=f"Часы по предмету «{subject}» должны быть числом")
        if subject in teachers:
            tid = (teachers[subject] or "").strip()
            if tid:
                t = db.query(User).filter(User.id == tid, User.role == "teacher",
                                          User.deleted == False).first()  # noqa: E712
                if t is None:
                    raise HTTPException(status_code=404, detail="Преподаватель не найден")
                if subject not in (t.subjects or []):
                    raise HTTPException(
                        status_code=400,
                        detail=f"У преподавателя «{W.display_name(t)}» нет предмета «{subject}»")
            row.teacher_id = tid
        if subject in teachers2:
            if not row.split:
                raise HTTPException(
                    status_code=400,
                    detail=f"Предмет «{subject}» не отмечен куратором как раздельный")
            tid2 = (teachers2[subject] or "").strip()
            if tid2:
                t2 = db.query(User).filter(User.id == tid2, User.role == "teacher",
                                           User.deleted == False).first()  # noqa: E712
                if t2 is None:
                    raise HTTPException(status_code=404, detail="Преподаватель не найден")
                if subject not in (t2.subjects or []):
                    raise HTTPException(
                        status_code=400,
                        detail=f"У преподавателя «{W.display_name(t2)}» нет предмета «{subject}»")
            row.teacher_id_2 = tid2
        if subject in zets:
            zv = zets[subject]
            if zv is None or zv == "":
                row.zet = None
            else:
                try:
                    row.zet = float(zv)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400,
                                        detail=f"ЗЕТ по предмету «{subject}» должен быть числом")
                if row.zet < 0:
                    raise HTTPException(status_code=400, detail="ЗЕТ не может быть отрицательным")
        row.updated_at = _now_iso()
        #Ноль часов — это «план снят», а не удаление строки: надгробие уехало бы на десктоп и
        #там часы просто исчезли бы, вместо того чтобы показать «не задано». Та же логика
        #для назначения — снятие препода (tid="") не трогает саму строку часов.
        row.deleted = False
        saved += 1
    db.commit()
    audit.log(db, actor=_admin.login, role="admin", action="group.hours",
              target=group, detail=f"предметов: {saved}")
    return {"ok": True, "saved": saved, "term": {"year": ty, "semester": ts}}


@router.get("/admin/subjects")
def admin_subjects(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Subject).filter(Subject.deleted == False).order_by(Subject.name).all()  # noqa: E712
    return {"subjects": [{"name": s.name} for s in rows]}


@router.get("/admin/subjects/portal")
def admin_subjects_portal(category: str = Query(""), _admin: User = Depends(require_admin),
                          db: Session = Depends(get_db)):
    """Предметы категории портала (колледж/бакалавриат/заочное 1/заочное 2) — та же
    сортировка по категориям, что уже есть у «Расписания»/«Групп»/«Студентов» (§5.1
    CLAUDE.md), только для каталога предметов. Источник — Snapshot.subjects того же
    снимка, что и массовый импорт групп (`admin_import_schedule_category_all`) —
    второй краулер не заводим. Он УЖЕ приходит без «- N п/г»-дублей (см.
    schedule/model.py::strip_subgroup_tag) — здесь их сверять НЕ нужно ещё раз.

    `in_catalog` — уже ли предмет в каталоге (Subject) — чтобы кнопка «Добавить»
    в интерфейсе показывалась только для того, чего реально не хватает."""
    if category and category not in schedule_parser.CATEGORIES:
        raise HTTPException(status_code=400, detail="Неизвестная категория расписания")
    snap, building = schedule_web.full_state(category)
    existing = {s.name for s in db.query(Subject).filter(Subject.deleted == False).all()}  # noqa: E712
    portal_subjects = sorted(snap.subjects) if snap else []
    return {"category": category or schedule_web.default_category(), "building": building,
            "subjects": [{"name": s, "in_catalog": s in existing} for s in portal_subjects]}
