"""
curator.py — Куратор: все предметы курируемой группы (только чтение), ЗЕТ-отчёт, риск отчисления.

Часть пакета `routers/web` (разрезан в 3.6: один файл на 4288 строк правили
62 коммита за полгода — он и был главным источником конфликтов при
одновременной работе). Общий роутер и хелперы — в `_common.py`; порядок
регистрации маршрутов задаёт `__init__.py`.
"""
from ._common import *      # noqa: F401,F403 — общий router, модели, хелперы


# КУРАТОР (только чтение по курируемым группам) ───────────────────────────────────
# Куратор — это teacher с непустым curated_groups. В отличие от журнала препода (только
# СВОИ предметы), куратор видит ВСЕ предметы курируемой группы, но ТОЛЬКО НА ЧТЕНИЕ.


@router.get("/curator/groups")
def curator_groups(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Группы, которые курирует преподаватель. Пусто → он не куратор (вкладка скрыта)."""
    _require("teacher", user)
    return {"groups": list(user.curated_groups or [])}


@router.get("/curator/risk")
def curator_risk(group: str = Query(...),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Риск отчисления по ВСЕМ студентам курируемой группы (3.6).

    В отличие от списка преподавателя (там риск считается только по ЕГО предметам —
    иначе он видел бы число, собранное из недоступных ему данных), куратор получает
    целостную картину: все предметы группы. Это и есть его работа — заметить студента,
    у которого по каждому предмету «ещё терпимо», а в сумме уже нет.

    Отдаём ТОЛЬКО тех, у кого риск реально есть (`visible`, порог dropout_risk.MIN_VISIBLE):
    список из тридцати строк, где двадцать восемь — «риска нет», читать невозможно, а
    именно читаемость и делает эту функцию полезной."""
    _require("teacher", user)
    _curator_check(user, group)
    rows = [r for r in W.group_risk_report(db, group) if r["risk"]["visible"]]
    #Сортировка по убыванию индекса: первым идёт тот, к кому идти первым.
    rows.sort(key=lambda r: -r["risk"]["score"])
    return {"group": group, "students": rows,
            "total": len(W.students_in_group(db, group)), "at_risk": len(rows)}


#Порядок уровней для сравнения «стало хуже» и порог, с которого вообще беспокоим куратора.
#«Умеренный»/«повышенный» — это повод посмотреть на экран, а не получить уведомление:
#на группе в 25 человек такие срабатывают у трети, и письмо перестают открывать.
_RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_RISK_NOTIFY_FROM = _RISK_ORDER["high"]


def _maybe_notify_dropout_risk(db, stud: User, group: str) -> None:
    """Пересчитать риск студента и написать куратору, ЕСЛИ уровень стал хуже прежнего.

    Вызывается хвостом обработчика выставления оценки — как и остальные хуки мессенджера/
    пушей, в try/except у вызывающего: сбой уведомления НИКОГДА не должен помешать
    преподавателю поставить балл.

    Память о прошлом уровне — `DropoutRiskNotice` (одна строка на студента). Без неё
    куратор получал бы одно и то же письмо после каждой двойки."""
    from ...models import DropoutRiskNotice
    risk = W.dropout_risk_for_student(db, stud.surname, stud.name, group)
    level = _RISK_ORDER.get(risk["level"], 0)
    row = db.get(DropoutRiskNotice, stud.id)
    was = _RISK_ORDER.get(row.level, 0) if row else 0

    #Состояние обновляем ВСЕГДА (в том числе вниз): выправился и снова просел — это
    #снова новость, а не «уже сообщали».
    if row is None:
        row = DropoutRiskNotice(id=stud.id)
        db.add(row)
    row.group_name = group
    row.level = risk["level"]
    row.score = risk["score"]
    row.updated_at = _now_iso()
    db.commit()

    if level < _RISK_NOTIFY_FROM or level <= was:
        return
    #Кураторы этой группы — те же, кого админ назначил в curated_groups. Отдельного
    #признака «куратор» в продукте нет, и заводить второй не нужно.
    curators = db.query(User).filter(User.role == "teacher", User.deleted == False).all()  # noqa: E712
    from ... import rustore_push
    for c in curators:
        if group in (c.curated_groups or []) and c.login:
            rustore_push.notify_dropout_risk(db, c.login, W.display_name(stud), group, risk)


@router.get("/curator/subjects")
def curator_group_subjects(group: str = Query(...), year: str = Query(""), semester: int = Query(0),
                           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Предметы курируемой группы за термин (ВСЕ предметы группы, не только «свои»).

    group — QUERY-параметр, а НЕ path: имена групп колледжа содержат слэш («К75/1»), и в
    пути encodeURIComponent даёт «%2F», который Starlette декодирует обратно в «/» и роут
    `{group}/subjects` перестаёт матчиться — эндпоинт молча 404-ил, и вкладка куратора
    показывала «нет предметов». В query слэш проходит как обычное значение."""
    _require("teacher", user)
    _curator_check(user, group)
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, year, semester)
    subs = {l.subject for l in W.group_lessons(db, group, year=ty, semester=ts) if l.subject}
    #⚠️ §3.5.5/3.6.1, ДВЕ находки одним корнем («отчёты/куратор показывают старые
    #предметы, новых вообще нет»). Список раньше был ЦЕЛИКОМ производным от Lesson
    #(предметы, у которых уже РЕАЛЬНО есть занятия) — импорт учебного плана/расписания
    #пишет ТОЛЬКО Group.subjects, ни одной Lesson-строки не создаёт, поэтому:
    #  1) свежедобавленный предмет БЕЗ единого занятия не показывался вовсе (§3.5.5,
    #     чинили досыпкой Group.subjects — но это было ОБЪЕДИНЕНИЕ, а не замена);
    #  2) предмет, УБРАННЫЙ из плана, но с занятиями в истории, продолжал висеть
    #     (обратная сторона того же перекоса, замечена только в 3.6.1).
    #Теперь для ТЕКУЩЕГО термина, когда план задан (group_plan_subjects — тот же
    #источник, что и у статистики студента/Вектора), он и есть ответ ЦЕЛИКОМ, а не
    #объединение с занятиями. Плана нет вовсе (группа заведена до импорта) — как раньше,
    #чистый список из занятий. АРХИВ (явно выбранный прошлый термин) НИКОГДА не трогаем
    #сегодняшним планом: тогда велись другие предметы, план мог с тех пор замениться.
    if (ty, ts) == W.current_term(cfg):
        plan = W.group_plan_subjects(db, group, cfg)
        if plan:
            subs = plan
    return {"group": group, "term": {"year": ty, "semester": ts}, "subjects": sorted(subs)}


@router.get("/curator/group-subject")
def curator_group_subject(group: str = Query(...), subject: str = Query(...),
                          year: str = Query(""), semester: int = Query(0),
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Студенты курируемой группы + оценки/средний по ЛЮБОМУ предмету группы (read-only).
    Ключевой скоуп безопасности: доступ по КУРИРУЕМОЙ ГРУППЕ, а не по предметам препода;
    запись недоступна (куратор не ставит оценки — только смотрит ведение журнала)."""
    _require("teacher", user)
    _curator_check(user, group)
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, year, semester)
    lessons = W.group_lessons(db, group, subject, year=ty, semester=ts)
    #Куратор смотрит ЧУЖОЙ предмет — шкала ведущего его преподавателя, не куратора.
    scale_map = W.lesson_scale_map(db, lessons)
    studs = W.students_in_group(db, group)
    rows = []
    for s in studs:
        recs = W.student_records(db, s.surname, s.name, group)
        grades = {l.id: recs.get(l.id, "") for l in lessons}
        rows.append({"surname": s.surname, "name": s.name,
                     "first_name": W.first_name(s), "patronymic": W.patronymic_of(s),
                     "grades": grades, "average": W.average(lessons, recs, cfg, scale=scale_map)})
    return {
        "group": group, "subject": subject, "term": {"year": ty, "semester": ts},
        "lessons": [{"id": l.id, "type": l.type, "number": l.number,
                     "topic": l.topic, "date": l.date} for l in lessons],
        "students": rows,
        #«Пройдено X из Y часов» — куратор смотрит ЧУЖОЙ журнал и раньше не видел плана
        #вовсе (живой баг: у препода в его собственном журнале часы есть, у куратора на
        #том же предмете/группе — нет). Тот же расчёт, что и в teacher_journal.
        "hours": W.hours_progress(db, group, subject, lessons, ty, ts),
    }


@router.get("/curator/zet-report")
def curator_zet_report(group: str = Query(...), year: str = Query(""), semester: int = Query(0),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Таблица перевода на курс по ЗЕТ (docs/done/PLAN-ZET.md §7.4) — «главная фича» отчёта
    куратора. group — QUERY (не path): имена групп содержат слэш («К75/1»), см. урок
    у /curator/subjects. Порог — ZetThreshold этой группы/термина, если куратор/админ
    его ещё не задал — eligible получают все (см. study_hours.group_zet_report)."""
    _require("teacher", user)
    _curator_check(user, group)
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, year, semester)
    threshold = db.get(ZetThreshold, zet_threshold_id(group, ty, ts))
    min_zet = threshold.min_zet if (threshold and not threshold.deleted) else None
    return {"group": group, "term": {"year": ty, "semester": ts}, "min_zet": min_zet,
            "students": W.group_zet_report(db, group, ty, ts, min_zet)}


@router.get("/admin/zet-thresholds")
def admin_get_zet_threshold(group: str = Query(...), year: str = Query(""),
                            semester: int = Query(0),
                            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Порог перевода группы на курс (docs/done/PLAN-ZET.md §7.2). group — QUERY (слэш в имени
    группы, тот же урок, что и везде)."""
    _admin_or_curator_check(user, group)
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, year, semester)
    row = db.get(ZetThreshold, zet_threshold_id(group, ty, ts))
    min_zet = row.min_zet if (row and not row.deleted) else None
    return {"group": group, "term": {"year": ty, "semester": ts}, "min_zet": min_zet}


@router.post("/admin/zet-thresholds")
def admin_set_zet_threshold(payload: dict = Body(...),
                            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Сохранить (или снять — min_zet=null) порог перевода: {group, year, semester, min_zet}."""
    group = (payload.get("group") or "").strip()
    if not group:
        raise HTTPException(status_code=400, detail="Нужна group")
    _admin_or_curator_check(user, group)
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, payload.get("year") or "", int(payload.get("semester") or 0))
    hid = zet_threshold_id(group, ty, ts)
    mz = payload.get("min_zet")
    row = db.get(ZetThreshold, hid)
    if mz is None or mz == "":
        if row is not None:
            row.deleted = True
            row.updated_at = _now_iso()
            db.commit()
        return {"ok": True, "min_zet": None, "term": {"year": ty, "semester": ts}}
    try:
        mz = float(mz)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="min_zet должен быть числом")
    if mz < 0:
        raise HTTPException(status_code=400, detail="min_zet не может быть отрицательным")
    if row is None:
        row = ZetThreshold(id=hid, group_name=group, year=ty, semester=ts)
        db.add(row)
    row.min_zet = mz
    row.updated_by = user.login
    row.updated_at = _now_iso()
    row.deleted = False
    db.commit()
    return {"ok": True, "min_zet": mz, "term": {"year": ty, "semester": ts}}


@router.post("/admin/groups/promote")
def admin_promote_group(payload: dict = Body(...), request: Request = None,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Перевод на следующий курс (docs/done/PLAN-ZET.md §7.4) — только явной кнопкой, только
    студентов с eligible=true (сервер САМ перепроверяет, клиентскому флагу не доверяет).
    group/year/semester/student_ids — В ТЕЛЕ (не в пути — слэш в имени группы)."""
    group = (payload.get("group") or "").strip()
    student_ids = payload.get("student_ids") or []
    if not group or not isinstance(student_ids, list) or not student_ids:
        raise HTTPException(status_code=400, detail="Нужны group и student_ids")
    _admin_or_curator_check(user, group)
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, payload.get("year") or "", int(payload.get("semester") or 0))
    threshold = db.get(ZetThreshold, zet_threshold_id(group, ty, ts))
    min_zet = threshold.min_zet if (threshold and not threshold.deleted) else None
    report = {r["student_id"]: r for r in W.group_zet_report(db, group, ty, ts, min_zet)}
    promoted, rejected = [], []
    for sid in student_ids:
        r = report.get(sid)
        if r is None:
            rejected.append({"student_id": sid, "reason": "не в группе"})
        elif not r["eligible"]:
            rejected.append({"student_id": sid, "reason": f"не хватает {r['missing_zet']} ЗЕТ"})
        else:
            promoted.append(r)
    if promoted:
        names = ", ".join(r["display_name"] for r in promoted)
        audit.log(db, request, actor=user.login, role=user.role, action="group.promote",
                  target=group, detail=f"переведено: {len(promoted)} ({names})")
        try:
            from ..messenger import _post_system_channel_message, _gtoken
            _post_system_channel_message(
                db, f"sys:announce:{_gtoken(group)}",
                f"Студенты переведены на следующий курс: {names}.")
        except Exception:
            pass
    return {"promoted": [{"student_id": r["student_id"], "display_name": r["display_name"]}
                         for r in promoted],
            "rejected": rejected}


@router.get("/curator/report")
def curator_report(groups: str = Query("all"), fmt: str = Query("xlsx"),
                   year: str = Query(""), semester: int = Query(0),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Сводный отчёт куратора по успеваемости (fmt=xlsx|docx).

    groups — «all» (все курируемые) ИЛИ список через запятую. КАЖДАЯ запрошенная группа
    проверяется на принадлежность к curated_groups: куратор не должен выгрузить чужую
    группу, подставив её в параметр. Пустой пересечённый список — 403 (нечего отдавать).

    В отчёте по каждой группе: список студентов, средние по предметам и общий, посещаемость
    (Н/Б/О) и число долгов — см. curator_report.collect_group."""
    from ... import curator_report as R
    _require("teacher", user)
    curated = list(user.curated_groups or [])
    if not curated:
        raise HTTPException(status_code=403, detail="Вы не куратор ни одной группы")

    if groups.strip().lower() == "all":
        chosen = curated
    else:
        asked = [g.strip() for g in groups.split(",") if g.strip()]
        #Строгий скоуп: оставляем только реально курируемые (порядок — как в curated).
        chosen = [g for g in curated if g in asked]
    if not chosen:
        raise HTTPException(status_code=403,
                            detail="Нет доступных для выгрузки групп из запрошенных")

    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, year, semester)
    data = [R.collect_group(db, g, ty, ts, cfg) for g in chosen]
    term = {"year": ty, "semester": ts}
    blob = R.build_docx(data, term) if fmt == "docx" else R.build_xlsx(data, term)

    name = "Успеваемость_" + ("все_группы" if len(chosen) > 1 else chosen[0])
    audit.log(db, actor=user.login, role="teacher", action="curator.report",
              target=", ".join(chosen), detail=f"{fmt}, семестр {ty}/{ts}")
    return _file_response(blob, name, fmt)


# ── Раздельное обучение (§ролей, 3.6.1) ──────────────────────────────────────────
# Поток: куратор ставит галочку «раздельное обучение» на предмете (здесь) → админ в
# редакторе часов группы получает право занять ВТОРОГО преподавателя (admin_read.py) →
# куратор расставляет студентов по подгруппам (subgroups ниже). Порядок именно такой —
# без назначенных преподавателей расставлять студентов по подгруппам было бы не за кого.
@router.post("/curator/subject-split")
def curator_set_subject_split(payload: dict = Body(...),
                              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Включить/выключить раздельное обучение по предмету группы: {group, subject,
    split: bool, year?, semester?}. Куратор ИЛИ админ (тот же порог, что у ЗЕТ-порога —
    решение по своей группе куратор принимает сам, гонять его к админу за каждой
    галочкой не нужно). Строку SubjectHours заводит, если её ещё не было (план мог
    быть пуст по этому предмету — часы админ выставит позже, отдельно)."""
    group = (payload.get("group") or "").strip()
    subject = (payload.get("subject") or "").strip()
    if not group or not subject:
        raise HTTPException(status_code=400, detail="Нужны group и subject")
    _admin_or_curator_check(user, group)
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, payload.get("year") or "", int(payload.get("semester") or 0))
    hid = subject_hours_id(group, subject, ty, ts)
    row = db.get(SubjectHours, hid)
    if row is None:
        row = SubjectHours(id=hid, group_name=group, subject=subject, year=ty, semester=ts)
        db.add(row)
    split = bool(payload.get("split"))
    row.split = split
    #Выключили раздельное обучение — второй преподаватель и роспись по подгруппам теряют
    #смысл: снимаем teacher_id_2 (иначе он молча продолжал бы «владеть» подгруппой 2 у
    #уже НЕ раздельного предмета). Роспись StudentSubgroup не трогаем — включат обратно,
    #она понадобится снова, а сама по себе на не-раздельный предмет не влияет никак
    #(Lesson.subgroup у него и так всегда 0 — фильтры её не читают).
    if not split:
        row.teacher_id_2 = ""
    row.deleted = False
    row.updated_at = _now_iso()
    db.commit()
    return {"ok": True, "group": group, "subject": subject, "split": split,
            "term": {"year": ty, "semester": ts}}


@router.get("/curator/subgroups")
def curator_get_subgroups(group: str = Query(...), subject: str = Query(...),
                          year: str = Query(""), semester: int = Query(0),
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Роспись студентов группы по подгруппам ЭТОГО раздельного предмета — для редактора
    куратора. Отдаёт ВСЕХ студентов группы (не только уже распределённых), чтобы было
    видно, кого ещё не отметили."""
    _admin_or_curator_check(user, group)
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, year, semester)
    row = W.subject_hours_row(db, group, subject, ty, ts)
    assigned = W.group_student_subgroups(db, group, subject, ty, ts)
    students = [{"student_id": s.id, "surname": s.surname, "name": s.name,
                "full_name": W.display_name(s), "subgroup": assigned.get(s.id)}
               for s in W.students_in_group(db, group)]
    return {"group": group, "subject": subject, "term": {"year": ty, "semester": ts},
            "split": bool(row and row.split),
            "teacher_name": W.display_name(db.get(User, row.teacher_id)) if (row and row.teacher_id and db.get(User, row.teacher_id)) else "",
            "teacher_name_2": W.display_name(db.get(User, row.teacher_id_2)) if (row and row.teacher_id_2 and db.get(User, row.teacher_id_2)) else "",
            "students": students}


@router.post("/curator/subgroups")
def curator_set_subgroups(payload: dict = Body(...),
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Сохранить роспись по подгруппам: {group, subject, assignments: {student_id: 1|2|null},
    year?, semester?}. null снимает распределение студента (снова «не назначен»)."""
    group = (payload.get("group") or "").strip()
    subject = (payload.get("subject") or "").strip()
    assignments = payload.get("assignments")
    if not group or not subject or not isinstance(assignments, dict):
        raise HTTPException(status_code=400, detail="Нужны group, subject и assignments")
    _admin_or_curator_check(user, group)
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, payload.get("year") or "", int(payload.get("semester") or 0))
    row = W.subject_hours_row(db, group, subject, ty, ts)
    if not row or not row.split:
        raise HTTPException(status_code=400, detail="Предмет не отмечен как раздельный")
    roster = {s.id for s in W.students_in_group(db, group)}
    now = _now_iso()
    for sid, sg in assignments.items():
        if sid not in roster:
            continue   #чужой/устаревший id — молча пропускаем, а не 400 на весь запрос
        hid = student_subgroup_id(group, subject, ty, ts, sid)
        srow = db.get(StudentSubgroup, hid)
        if sg is None or sg == "":
            if srow is not None:
                srow.deleted = True
                srow.updated_at = now
            continue
        try:
            sg = int(sg)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Подгруппа для {sid} должна быть 1 или 2")
        if sg not in (1, 2):
            raise HTTPException(status_code=400, detail="Подгруппа должна быть 1 или 2")
        if srow is None:
            srow = StudentSubgroup(id=hid, group_name=group, subject=subject, year=ty,
                                   semester=ts, student_id=sid)
            db.add(srow)
        srow.subgroup = sg
        srow.deleted = False
        srow.updated_at = now
    db.commit()
    return {"ok": True, "group": group, "subject": subject,
            "assigned": W.group_student_subgroups(db, group, subject, ty, ts)}
