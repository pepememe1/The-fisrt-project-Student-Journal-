"""
terms.py — Учебный период: список термина/архива и перевод на следующий семестр.

Часть пакета `routers/web` (разрезан в 3.6: один файл на 4288 строк правили
62 коммита за полгода — он и был главным источником конфликтов при
одновременной работе). Общий роутер и хелперы — в `_common.py`; порядок
регистрации маршрутов задаёт `__init__.py`.
"""
from ._common import *      # noqa: F401,F403 — общий router, модели, хелперы


# УЧЕБНЫЙ ПЕРИОД (год/семестр) ───────────────────────────────────────────────────
@router.get("/terms")
def terms(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Доступные учебные периоды (для селектора/архива) + текущий термин."""
    cfg = W.load_config(db)
    cy, cs = W.current_term(cfg)
    return {"current": {"year": cy, "semester": cs}, "terms": W.list_terms(db)}


@router.get("/admin/term/grades-freeze")
def admin_get_grades_freeze(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Дата, после которой студенту видны только ИТОГОВЫЕ оценки. Пусто — правило выключено.

    Требование Влада (06.09.2026): «время доходит до лета — оценки останавливаются и видны
    только итоговые (выставим кастомную дату, когда по сути зачёты и экзамены уже сданы)».

    ⚠️ Отдаём и ФАЗУ, в которой продукт находится прямо сейчас. Настройка, по которой
    нельзя проверить, сработала ли она, проверяется единственным способом — дождаться лета;
    это не проверка, а надежда.
    """
    cfg = W.load_config(db)
    return {"date": cfg.get("grades_freeze_date") or "",
            "phase": W.grades_phase(db, cfg),
            "term": dict(zip(("year", "semester"), W.current_term(cfg)))}


@router.post("/admin/term/grades-freeze")
def admin_set_grades_freeze(payload: dict = Body(...), request: Request = None,
                            _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Задать дату заморозки («ДД.ММ») или снять её пустой строкой.

    ⚠️ Год НЕ хранится: дата повторяется каждый учебный год, и записанный год пришлось бы
    править вручную каждый июнь — тот же класс, из-за которого курс группы хранится годом
    поступления, а не числом курса.

    ⚠️ Неразобранную строку ОТКЛОНЯЕМ, а не сохраняем. Молча принятый мусор означал бы
    выключённое правило при заполненном поле — админ считал бы, что настроил, а оценки
    продолжали бы показываться всё лето.
    """
    from ... import grade_policy
    raw = (payload.get("date") or "").strip()
    if raw and grade_policy.parse_freeze(raw) is None:
        raise HTTPException(status_code=400,
                            detail="Дата задаётся как «ДД.ММ», например «30.06»")
    row = db.get(ConfigKV, "config")
    cur = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    cur["grades_freeze_date"] = raw
    now = _now_iso()
    if row is None:
        db.add(ConfigKV(key="config", value=cur, updated_at=now, deleted=False))
    else:
        row.value = cur
        row.updated_at = now
    db.commit()
    audit.log(db, request, actor=_admin.login, role="admin", action="term.grades_freeze",
              detail=raw or "снята")
    return {"ok": True, "date": raw, "phase": W.grades_phase(db)}


@router.post("/admin/term/rollover")
def admin_term_rollover(payload: dict = Body(default={}), request: Request = None,
                        _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Перевод учебного периода вручную.

    Прошлые занятия остаются в своём периоде и становятся read-only (архив, см.
    `_ensure_current_term`). Ростер/группы НЕ трогаем. Термин хранится в config.

    🔥 ВПЕРЁД ДВИГАТЬ НЕЛЬЗЯ, и это главное в этой функции. Дважды — в 3.6.1 и снова
    15.08.2026 — оставленный ею ключ уводил продукт на год вперёд календаря, а от термина
    считается КУРС студента: импорт учебного плана записывал предметы СЛЕДУЮЩЕГО курса, и
    у группы оказывались предметы двух курсов сразу (у куратора одни, в админке другие).
    Оба раза чинилось руками в боевой базе.

    Двигать вперёд не нужно вовсе: 1 сентября календарь переключается сам
    (`db.default_term`). Поэтому попытка уйти в будущее — 400 с объяснением, а не тихая
    запись. Назад (открыть закрытый семестр) по-прежнему можно — это законный сценарий.
    Вторая заслонка стоит в `webdata.current_term`: она игнорирует будущий оверрайд, если
    он уже лежит в базе с прошлых раз."""
    cfg = W.load_config(db)
    cy, cs = W.current_term(cfg)
    ny = (payload.get("year") or "").strip()
    ns = payload.get("semester")
    if ny and ns:
        new_year, new_sem = ny, int(ns)
    elif cs == 1:
        new_year, new_sem = cy, 2                 #осень → весна того же года
    else:
        try:                                      #весна → осень следующего года
            a, b = cy.split("/")
            new_year = f"{int(a) + 1}/{int(b) + 1}"
        except Exception:
            new_year = cy
        new_sem = 1
    #Заслонка: уйти в термин, который календарь ещё не прошёл, нельзя (см. докстринг).
    if W._term_key((new_year, new_sem)) > W._term_key(W.default_term_by_date()):
        raise HTTPException(
            status_code=400,
            detail=(f"Период {new_year}·{new_sem} ещё не наступил по календарю "
                    f"(сейчас {'·'.join(map(str, W.default_term_by_date()))}). Вперёд "
                    f"учебный период переключается сам 1 сентября — ручной сдвиг уже "
                    f"дважды приводил к предметам двух курсов сразу."))
    row = db.get(ConfigKV, "config")
    cur = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    cur["current_year"] = new_year
    cur["current_semester"] = new_sem
    now = _now_iso()
    if row is None:
        db.add(ConfigKV(key="config", value=cur, updated_at=now, deleted=False))
    else:
        row.value = cur
        row.updated_at = now
    db.commit()
    audit.log(db, request, actor=_admin.login, role="admin", action="term.rollover",
              detail=f"{cy}·{cs} → {new_year}·{new_sem}")
    #§12: отчёты куратора «вечны» (кнопки не удаляются), но после rollover'а старого
    #термина архивируются — новый /отчет уже не берёт значения из term'а, который
    #только что закрылся. Сбой этой пометки не должен ломать сам rollover.
    try:
        from ...models import CuratorReport
        (db.query(CuratorReport)
         .filter(CuratorReport.year == cy, CuratorReport.semester == cs,
                 CuratorReport.archived == False)  # noqa: E712
         .update({"archived": True}, synchronize_session=False))
        db.commit()
    except Exception as e:
        print(f"[curator_reports] не удалось архивировать отчёты term {cy}·{cs}: {e}")
    #Свидетельство о курсе снимаем ДО того, как год сменится в глазах остальных: без
    #«где группа стояла раньше» вычислить «не перешла на следующий курс» не из чего
    #(см. `group_archive`). Сбой этой пометки не должен ронять сам перевод периода —
    #то же правило, что у отчётов куратора выше.
    try:
        from ... import group_archive as GA
        for grp in db.query(Group).filter(Group.deleted == False,  # noqa: E712
                                          Group.archived == False).all():  # noqa: E712
            GA.witness(db, grp, W.group_course(db, grp.name))
        db.commit()
    except Exception as e:
        print(f"[group_archive] не удалось запомнить курсы групп: {e}")
    return {"ok": True, "previous": {"year": cy, "semester": cs},
            "current": {"year": new_year, "semester": new_sem}}
