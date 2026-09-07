"""
student.py — Кабинет студента: витрина, журнал, статистика, проактивные карточки, ЗЕТ.

Часть пакета `routers/web` (разрезан в 3.6: один файл на 4288 строк правили
62 коммита за полгода — он и был главным источником конфликтов при
одновременной работе). Общий роутер и хелперы — в `_common.py`; порядок
регистрации маршрутов задаёт `__init__.py`.
"""
from ._common import *      # noqa: F401,F403 — общий router, модели, хелперы


# СТУДЕНТ ──────────────────────────────────────────────────────────────────────
@router.get("/student/overview")
def student_overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Витрина студента: средний балл, свежие оценки, счётчик задолженностей."""
    _require("student", user)
    cfg = W.load_config(db)
    #Только предметы ДЕЙСТВУЮЩЕГО плана И только ТЕКУЩИЙ термин: занятия отменённых
    #дисциплин и занятия за прошлые курсы остаются в базе (история), но «Мои предметы» и
    #средний балл обязаны отражать текущий семестр — без term-фильтра предмет вроде
    #«Физическая культура» (есть в плане каждый семестр) тянул бы оценки за все курсы.
    #Плюс раздельное обучение (§ролей, 3.6.1): занятие чужой подгруппы студенту не видно.
    lessons = W.filter_lessons_by_student_subgroup(db, W.current_term_lessons(
        db, user.group_name, W.current_subject_lessons(
            db, user.group_name, W.group_lessons(db, user.group_name)), cfg), user.id)
    by_id = {l.id: l for l in lessons}
    records = W.student_visible_records(db, user.surname, user.name, user.group_name, cfg)
    scale_map = W.lesson_scale_map(db, lessons)

    #Свежие оценки — по серверной метке времени, только реальные занятия СВОЕЙ группы.
    #Скоуп по lesson_id группы нужен и здесь: иначе оценки тёзки из другой группы могли
    #бы вытеснить свои из окна limit(30) ещё до фильтра by_id ниже (защита от тёзок).
    own_ids = set(by_id.keys())
    recent_rows = db.query(Grade).filter(
        Grade.student_f == user.surname, Grade.student_n == user.name,
        Grade.deleted == False,
        Grade.lesson_id.in_(own_ids)).order_by(Grade.updated_at.desc()).limit(30).all()  # noqa: E712
    recent = []
    for g in recent_rows:
        l = by_id.get(g.lesson_id)
        if not l or not g.grade:
            continue
        recent.append({"subject": l.subject, "topic": l.topic, "date": l.date, "grade": g.grade})
        if len(recent) >= 6:
            break

    #Счётчик оценок за месяц — тоже строго по занятиям своей группы (без тёзок из чужих).
    #Считаем по БАЗОВОМУ lesson_id, чтобы не потерять свои пересдачи (<lid>_retake).
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    month_rows = db.query(Grade.lesson_id).filter(
        Grade.student_f == user.surname, Grade.student_n == user.name,
        Grade.deleted == False, Grade.updated_at >= cutoff).all()  # noqa: E712
    grades_month = sum(1 for (lid,) in month_rows if W.base_lesson_id(lid) in own_ids)

    # «Мои предметы» + счётчики + посещаемость (как на главной десктопа).
    from collections import OrderedDict
    subj_lessons = OrderedDict()
    for l in lessons:
        subj_lessons.setdefault(l.subject, []).append(l)
    #⚠️ Живой баг («на Главной 2 предмета, в Журнале 4, в Статистике весь список») —
    #список предметов Главной раньше был ЦЕЛИКОМ производным от `lessons` (какие
    #предметы у СТУДЕНТА уже реально есть занятия), а статистика (`per_subject_
    #with_plan`) досыпает предметы ДЕЙСТВУЮЩЕГО ПЛАНА без единого занятия — три экрана
    #по одному и тому же студенту отвечали на «сколько у меня предметов» ПО-РАЗНОМУ.
    #Досыпаем и здесь, тем же источником (`group_plan_subjects`) — предмет без единой
    #пары получает cnt=0, а не пропадает из списка.
    for subj in sorted(W.group_plan_subjects(db, user.group_name, cfg) - set(subj_lessons)):
        subj_lessons[subj] = []
    subjects = []
    grades_total = 0
    for subj, ls in subj_lessons.items():
        cnt = 0
        for l in ls:
            v = (records.get(l.id) or "").strip()
            if l.type in ("Практика", "Экзамен") and v:
                cnt += 1
        grades_total += cnt
        subjects.append({"subject": subj, "grades": cnt})
    #⚠️ ЗДЕСЬ БЫЛ ВТОРОЙ РАСЧЁТ ПОСЕЩАЕМОСТИ (убран 31.08.2026). Он считал свой процент
    #по ЛЕКЦИЯМ и только по ним: прогулявший все практики видел «посещаемость 100 %»
    #рядом с честным числом пропущенных часов на том же экране. В 3.7.6 этот же цикл уже
    #ловил Полковник — тогда из-за метки «О», — и его поправили, оставив вторым. Правда
    #теперь одна: `webdata.attendance_percent` считает из тех же часов, что и `absences`.
    attendance = W.attendance_percent(lessons, records)

    return {
        "name": W.display_name(user),
        "group": user.group_name,
        #Курс рядом с группой (живой отзыв 3.6). null — год поступления группы не задан;
        #клиент тогда просто не рисует подпись, а не пишет «1 курс» наугад.
        "course": W.group_course(db, user.group_name, cfg),
        "average": W.average(lessons, records, cfg, scale=scale_map),
        "grades_month": grades_month,
        "grades_total": grades_total,
        "subjects": subjects,
        "subjects_count": len(subjects),
        "attendance": attendance,
        "next_lesson": None,  # появится с интеграцией расписания
        "debts": len(W.debts(lessons, records, scale=scale_map)),
        "recent": recent,
    }


@router.get("/student/journal")
def student_journal(year: str = Query(""), semester: int = Query(0),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Журнал студента: занятия сгруппированы по предметам, у каждого — своя оценка.

    🔥 АРХИВ ПРОШЛЫХ СЕМЕСТРОВ СТУДЕНТУ ЗАКРЫТ (06.09.2026, требование Влада: «первого
    сентября оценки обнуляются, а по сути улетают в архив, чтобы админ мог смотреть
    программу за семестры с оценками, но студент не мог видеть свои старые оценки»).
    Здесь `year`+`semester` открывали прошлый период любому студенту.

    ⚠️ Причина не в секретности, а в арифметике, и названа она самим Владом: «если в
    прошлом семестре студент был двоечник, а сейчас отличник, чтобы он не был в среднем
    троечником, т.к. семестры разные». Средний, смешавший два семестра, не описывает НИ
    ОДИН из них и при этом выглядит настоящим.

    ⚠️ Параметры НЕ УДАЛЕНЫ и отказом не отвечают: старая ссылка/закладка/офлайн-копия
    просто получает ТЕКУЩИЙ семестр. Отказ здесь читался бы как поломка журнала, а
    подмена периода — это ровно то, что человеку и нужно показать.
    ⚠️ Ни одна оценка при этом не удаляется: администратор и выгрузки видят всё.
    """
    _require("student", user)
    cfg = W.load_config(db)
    year, semester = "", 0
    ty, ts = _resolve_term(cfg, year, semester)
    is_archive = False
    #Тот же скоуп, что у статистики: журнал текущего семестра показывает предметы
    #ДЕЙСТВУЮЩЕГО плана, архив — то, что реально велось тогда. Иначе список предметов в
    #журнале и в статистике расходится, и непонятно, какому из них верить.
    #Раздельное обучение (§ролей, 3.6.1): своя подгруппа — всегда, архив не исключение
    #(тогда занятие тоже принадлежало конкретной подгруппе, это факт истории).
    lessons = W.filter_lessons_by_student_subgroup(db, W.current_subject_lessons(
        db, user.group_name,
        W.group_lessons(db, user.group_name, year=ty, semester=ts), is_archive), user.id)
    records = W.student_visible_records(db, user.surname, user.name, user.group_name, cfg)
    scale_map = W.lesson_scale_map(db, lessons)

    from collections import OrderedDict
    buckets = OrderedDict()
    for l in lessons:
        buckets.setdefault(l.subject, []).append(l)
    #⚠️ Тот же живой баг, что и на Главной (см. student_overview): предмет ДЕЙСТВУЮЩЕГО
    #плана без единого занятия должен быть ВИДЕН («по нему пока пусто»), а не пропадать
    #из журнала молча — иначе три экрана снова расходятся в ответе на «сколько у меня
    #предметов». В архиве план мог с тех пор замениться — не трогаем.
    if not is_archive:
        for subj in sorted(W.group_plan_subjects(db, user.group_name, cfg) - set(buckets)):
            buckets[subj] = []

    subjects = []
    for subj, ls in buckets.items():
        items = []
        for l in ls:
            entry = {"id": l.id, "type": l.type, "number": l.number,
                     "topic": l.topic, "date": l.date, "grade": records.get(l.id, "")}
            if l.type == "Экзамен":
                entry["latest"] = W.grading.latest_exam_value(l.id, records)
            items.append(entry)
        subjects.append({"subject": subj, "lessons": items,
                         "average": W.average(ls, records, cfg, scale=scale_map),
                         #«Пройдено X из Y часов» по этому предмету (0 total — не задано).
                         "hours": W.hours_progress(db, user.group_name, subj, ls, ty, ts)})

    return {"group": user.group_name, "subjects": subjects, "term": {"year": ty, "semester": ts},
            "methodology": W.grading.methodology_text(cfg)}


@router.get("/student/stats")
def student_stats(year: str = Query(""), semester: int = Query(0),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Статистика студента: общий средний, по предметам, пропуски, задолженности.

    🔥 ТОЛЬКО ТЕКУЩИЙ СЕМЕСТР (06.09.2026), как и журнал. Здесь `year`+`semester`
    открывали архив, и именно там средний балл смешивал семестры — то, из-за чего правило
    и заведено: «если в прошлом семестре студент был двоечник, а сейчас отличник, чтобы он
    не был в среднем троечником, т.к. семестры разные».

    ⚠️ Параметры не удалены и отказом не отвечают: старая ссылка получает текущий период.
    Отказ читался бы как поломка статистики, а подмена периода показывает то, что нужно.
    ⚠️ ЗЕТ (`/student/zet`) НЕ ТРОГАЕМ намеренно: зачётные единицы копятся за всё обучение,
    и «архив» там — не смешение семестров, а сам смысл показателя.
    """
    _require("student", user)
    cfg = W.load_config(db)
    year, semester = "", 0
    ty, ts = _resolve_term(cfg, year, semester)
    is_archive = False
    #⚠️ Предметы, УБРАННЫЕ из учебного плана группы, в текущую статистику не идут: их
    #занятия и оценки остаются в базе (это история), но диаграмма «мои предметы» обязана
    #показывать то, что человек изучает СЕЙЧАС. В архиве фильтр не применяется — см.
    #webdata.current_subject_lessons.
    #+ раздельное обучение (§ролей, 3.6.1): чужая подгруппа студенту не видна ни здесь,
    #ни в архиве (тогда занятие тоже принадлежало конкретной подгруппе).
    lessons = W.filter_lessons_by_student_subgroup(db, W.current_subject_lessons(
        db, user.group_name,
        W.group_lessons(db, user.group_name, year=ty, semester=ts), is_archive), user.id)
    records = W.student_visible_records(db, user.surname, user.name, user.group_name, cfg)
    #Долги и пропуски в ДЕФОЛТНОМ виде считаем по занятиям БЕЗ штампа термина ТОЖЕ (как
    #overview): иначе легаси-занятия без year/semester (десктоп до штампа) выпадают из
    #фильтра текущего термина, и реальные долги/пропуски «исчезают». Занятия с ЧУЖИМ, но
    #ЗАДАННЫМ термином (прошлый курс) — исключаем (current_term_lessons), иначе предмет
    #вроде «Физической культуры» тянул бы долги/пропуски за все прошлые курсы группы.
    #В архиве — строго по выбранному термину, ничего лишнего не подмешиваем.
    dl = lessons if is_archive else W.filter_lessons_by_student_subgroup(
        db, W.current_term_lessons(db, user.group_name, W.current_subject_lessons(
            db, user.group_name, W.group_lessons(db, user.group_name), is_archive), cfg), user.id)
    scale_map = W.lesson_scale_map(db, lessons if is_archive else lessons + dl)
    #Предметы ПЛАНА, по которым занятий ещё нет вовсе, тоже показываем — иначе список
    #«мои предметы» в статистике короче, чем в журнале, и это выглядит как потеря данных.
    #Общая функция с кабинетом родителя: формат обязан совпадать (см. её докстринг).
    per_subject = W.per_subject_with_plan(db, user.group_name, lessons, records, cfg,
                                          scale=scale_map, is_archive=is_archive)
    return {
        "term": {"year": ty, "semester": ts},
        "average": W.average(lessons, records, cfg, scale=scale_map),
        "per_subject": per_subject,
        "absences": W.absences(dl, records),
        "debts": W.debts(dl, records, scale=scale_map),
    }


@router.get("/student/insights")
def student_insights(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Проактивные карточки Вектора для студента (порт vector/insights.py, личный
    скоуп): долги, ближайшие пересдачи, пропуски, средний. Все числа — из БД."""
    _require("student", user)
    cfg = W.load_config(db)
    #Карточки и риск отчисления считаем по ДЕЙСТВУЮЩЕМУ плану И текущему термину: долг
    #по отменённому предмету не должен пугать студента, а предмет вроде «Физической
    #культуры» (в плане каждый семестр) не должен тянуть оценки за прошлые курсы.
    #Плюс раздельное обучение (§ролей, 3.6.1): чужая подгруппа не в счёт.
    lessons = W.filter_lessons_by_student_subgroup(db, W.current_term_lessons(
        db, user.group_name, W.current_subject_lessons(
            db, user.group_name, W.group_lessons(db, user.group_name)), cfg), user.id)
    records = W.student_visible_records(db, user.surname, user.name, user.group_name, cfg)
    scale_map = W.lesson_scale_map(db, lessons)
    avg = W.average(lessons, records, cfg, scale=scale_map)
    cards = []

    d = W.debts(lessons, records, scale=scale_map)
    if d:
        cards.append({"severity": "warn", "icon": "⚠️", "title": "Незакрытые задолженности",
                      "detail": "; ".join(d[:3]) + ("…" if len(d) > 3 else "") + ".",
                      "action": "Уточните у преподавателя дату пересдачи"})

    #Назначенные пересдачи экзаменов, которые студент ещё не закрыл.
    for l in lessons:
        if l.type == "Экзамен" and l.retake_date:
            base = (records.get(l.id) or "").strip()
            retake = (records.get(l.id + "_retake") or "").strip()
            failed = base.startswith(("2", "Н")) or "Не зачтено" in base
            if failed and not retake:
                cards.append({"severity": "alert", "icon": "📅",
                              "title": f"Пересдача: {l.subject}",
                              "detail": f"Назначена на {l.retake_date} (экзамен №{l.number}).",
                              "action": "Подготовьтесь заранее"})

    a = W.absences(lessons, records)
    if a["всего"] >= 10:
        cards.append({"severity": "warn", "icon": "🕒", "title": "Много пропусков",
                      "detail": f"Всего {a['всего']} ч (Н: {a['Н']}, Б: {a['Б']}, О: {a['О']}).",
                      "action": "Не пропускайте ближайшие пары"})

    if avg >= 4.5:
        cards.append({"severity": "info", "icon": "🎉", "title": "Отличная успеваемость",
                      "detail": f"Средний балл {avg} — так держать!"})
    elif 0 < avg < 3:
        cards.append({"severity": "alert", "icon": "🚨", "title": "Средний ниже 3",
                      "detail": f"Сейчас {avg}. Есть риск задолженностей по итогам.",
                      "action": "Разберите сложные темы с Вектором"})

    #РИСК ОТЧИСЛЕНИЯ (3.6). Показываем ТОЛЬКО когда он реально есть (порог
    #dropout_risk.MIN_VISIBLE) — «риск 1 %» пугает без повода и обесценивает настоящий
    #сигнал. Карточка идёт ПЕРВОЙ: если она появилась, всё остальное на экране менее
    #важно. Обязательно называем предметы и что делать — индекс без объяснения человеку
    #бесполезен и воспринимается как приговор.
    risk = W.dropout_risk_for_student(db, user.surname, user.name, user.group_name,
                                      cfg=cfg, lessons=lessons, records=records)
    if risk["visible"]:
        subj = ", ".join(s["subject"] for s in risk["subjects"][:3])
        why = "; ".join(f["detail"] for f in risk["factors"][:2])
        cards.insert(0, {
            "severity": "alert" if risk["level"] in ("high", "critical") else "warn",
            "icon": "🎓",
            "title": f"{risk['level_label']} риск отчисления",
            "detail": (f"Из-за чего: {why}" + (f" Предметы: {subj}." if subj else "")),
            "action": risk["advice"],
        })

    if not cards:
        cards.append({"severity": "info", "icon": "✅", "title": "Всё в порядке",
                      "detail": "Долгов и тревожных сигналов нет — так держать!"})
    #risk отдаём отдельным полем (а не только карточкой): интерфейсу нужен уровень для
    #цвета/иконки, а разбирать его обратно из текста карточки — верный способ разъехаться.
    return {"cards": cards, "mood": _mood_by_avg(avg),
            "risk": risk if risk["visible"] else None}


@router.get("/student/zet")
def student_zet(year: str = Query(""), semester: int = Query(0),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """ЗЕТ студента за термин (docs/PLAN-ZET.md). Пусто — ни один предмет группы ещё
    не получил ЗЕТ от администратора (интерфейс тогда не показывает строку вовсе).
    min_zet — порог перевода группы (для «до перевода: X ЗЕТ» в дашборде), null — куратор/
    админ его ещё не задавал."""
    _require("student", user)
    cfg = W.load_config(db)
    ty, ts = _resolve_term(cfg, year, semester)
    threshold = db.get(ZetThreshold, zet_threshold_id(user.group_name, ty, ts))
    min_zet = threshold.min_zet if (threshold and not threshold.deleted) else None
    return {"term": {"year": ty, "semester": ts}, "min_zet": min_zet,
            **W.zet_summary_for_student(db, user.surname, user.name, user.group_name, ty, ts)}


@router.get("/teacher/insights")
def teacher_insights(group: str = Query(...),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Карточки по группе для преподавателя (порт vector/insights.compute_insights):
    средний группы, должники, зона риска, доля пропусков — по СВОИМ предметам."""
    _require("teacher", user)
    cfg = W.load_config(db)
    subjects = set(user.subjects or [])
    #Только ТЕКУЩИЙ термин: без него предмет, который препод вёл этой же группе в прошлом
    #курсе (напр. повторяющаяся «Физическая культура»), тянул бы средний/должников/риск
    #из истории, а не из того, что происходит сейчас.
    lessons = W.current_term_lessons(
        db, group, [l for l in W.group_lessons(db, group) if l.subject in subjects], cfg)
    studs = W.students_in_group(db, group)
    cards = []
    tscale = W.teacher_scale(user)

    vals, debtors, risky, absc_total = [], 0, 0, 0
    for s in studs:
        #не-студент: это ручка ПРЕПОДАВАТЕЛЯ (`/teacher/insights`), просто живёт в
        #этом файле. Ему нужна полная картина по группе — политика показа оценок
        #студенту к нему не относится (см. grade_policy).
        recs = W.student_records(db, s.surname, s.name, group)
        a = W.average(lessons, recs, cfg, scale=tscale)
        if a > 0:
            vals.append(a)
        if W.debts(lessons, recs, scale=tscale):
            debtors += 1
        if 0 < a < 3:
            risky += 1
        absc_total += W.absences(lessons, recs)["всего"]

    if vals:
        gavg = round(sum(vals) / len(vals), 2)
        level = "хороший уровень" if gavg >= 4 else ("средний уровень" if gavg >= 3
                                                     else "ниже нормы, стоит подтянуть")
        cards.append({"severity": "info" if gavg >= 3 else "warn", "icon": "📊",
                      "title": f"Средний балл группы {group}",
                      "detail": f"{gavg} — {level}."})
    if debtors:
        cards.append({"severity": "warn", "icon": "⚠️", "title": "Незакрытые задолженности",
                      "detail": f"Должников: {debtors}. Стоит назначить пересдачи.",
                      "action": "Проверьте журнал"})
    if risky:
        cards.append({"severity": "alert", "icon": "🚨", "title": "Студенты в зоне риска",
                      "detail": f"{risky} студ. со средним < 3.",
                      "action": "Связаться с куратором группы"})
    if studs and absc_total and round(absc_total / len(studs), 1) >= 5:
        cards.append({"severity": "warn", "icon": "🕒", "title": "Высокая доля пропусков",
                      "detail": f"В среднем {round(absc_total / len(studs), 1)} ч на студента."})
    if not cards:
        cards.append({"severity": "info", "icon": "✅", "title": "Всё спокойно",
                      "detail": f"По группе {group} тревожных сигналов нет."})
    return {"cards": cards}
