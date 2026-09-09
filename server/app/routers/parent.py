"""
parent.py — роль «родитель»: привязка к студенту, журнал ребёнка, согласие студента.

Родитель — самая ограниченная роль в системе. Он видит ТОЛЬКО журнал своих детей и не
имеет доступа ни к спискам пользователей, ни к статистике группы, ни к чужим оценкам.
Поиск людей ему недоступен, и сам он невидим для студентов и преподавателей: в каталоге
собеседников (messenger.directory) роль `parent` открыта лишь куратору и администратору.

ГЛАВНОЕ ПРАВИЛО, которое нельзя ослаблять: доступ даёт не привязка, а СОГЛАСИЕ СТУДЕНТА.
Сотрудник колледжа только создаёт заявку (`pending`); пока студент не подтвердил её в
своём кабинете, `children()` не вернёт ребёнка, а журнал ответит 403. Для
несовершеннолетнего доступ законного представителя правомерен и без согласия, но в
колледже учатся и взрослые, а различать их по дате рождения значило бы хранить лишние
персональные данные ради проверки, которую согласие закрывает для всех сразу.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user, require_admin
from ..models import User, ParentLink, parent_link_id, set_user_password
from .. import webdata as W
from .. import audit

router = APIRouter(prefix="/web", tags=["parent"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_parent(user: User):
    if user.role != "parent":
        raise HTTPException(status_code=403, detail="Доступно только для роли «родитель»")


def _curator_groups(user: User) -> list:
    """Группы, которые пользователь курирует. Админ курирует всё (пустой список = все)."""
    return list(user.curated_groups or [])


def _may_manage_student(actor: User, student: User) -> bool:
    """Вправе ли сотрудник привязывать родителя к этому студенту.

    Админ — к любому. Куратор — только к студентам СВОИХ групп: привязка открывает доступ
    к персональным данным, и раздавать её по чужим группам куратор не должен."""
    if actor.role == "admin":
        return True
    if actor.role == "teacher" and student.group_name in _curator_groups(actor):
        return True
    return False


def _link_view(db: Session, link: ParentLink) -> dict:
    parent = db.get(User, link.parent_id)
    student = db.get(User, link.student_id)
    return {
        "id": link.id,
        "status": link.status,
        "created_at": link.created_at,
        "parent": {"id": link.parent_id,
                   "full_name": (parent.full_name or parent.name or "") if parent else "",
                   "login": parent.login if parent else ""},
        "student": {"id": link.student_id,
                    "full_name": (student.full_name or f"{student.surname} {student.name}").strip()
                    if student else "",
                    "group": student.group_name if student else ""},
    }


def active_children(db: Session, parent: User) -> list:
    """Студенты, доступ к журналу которых родителю ПОДТВЕРЖДЁН.

    Единая точка: и список детей, и проверка прав в журнале, и скоуп Вектора ходят сюда.
    Если бы каждый считал сам, рано или поздно один из них забыл бы про статус."""
    links = (db.query(ParentLink)
             .filter(ParentLink.parent_id == parent.id, ParentLink.status == "active")
             .all())
    out = []
    for link in links:
        student = db.get(User, link.student_id)
        if student is not None and not student.deleted:
            out.append(student)
    return out


# ── Кабинет родителя ────────────────────────────────────────────────────────────────
@router.get("/parent/children")
def parent_children(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Дети родителя (только подтверждённые) + сколько заявок ещё ждут согласия.

    Ожидающие показываем числом и без имён: пока согласия нет, даже «за кого я подал
    заявку» — это уже сведения о другом человеке."""
    _require_parent(user)
    pending = (db.query(ParentLink)
               .filter(ParentLink.parent_id == user.id, ParentLink.status == "pending")
               .count())
    return {"children": [{"id": s.id,
                          "full_name": (s.full_name or f"{s.surname} {s.name}").strip(),
                          "group": s.group_name} for s in active_children(db, user)],
            "pending": pending}


def _resolve_child(db: Session, user: User, student_id: str) -> User:
    """Ребёнок по id — с проверкой подтверждённой связи. 403 (а не 404) намеренно: сам
    факт существования студента с таким id посторонним знать незачем."""
    children = active_children(db, user)
    if not children:
        raise HTTPException(status_code=403, detail="Нет подтверждённого доступа к журналу")
    if not student_id:
        return children[0]          #один ребёнок — не заставляем выбирать
    for s in children:
        if s.id == student_id:
            return s
    raise HTTPException(status_code=403, detail="Нет доступа к журналу этого студента")


@router.get("/parent/journal")
def parent_journal(student_id: str = Query(""), year: str = Query(""), semester: int = Query(0),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Журнал ребёнка — ТОЛЬКО ЧТЕНИЕ. Формат совпадает с /web/student/journal, чтобы
    интерфейс переиспользовал ту же таблицу и цифры не разъехались между кабинетами."""
    _require_parent(user)
    child = _resolve_child(db, user, student_id)
    cfg = W.load_config(db)
    from .web import _resolve_term
    ty, ts = _resolve_term(cfg, year, semester)
    is_archive = bool((year or "").strip() and semester)
    #⚠️ Живой баг (найден у студента, здесь была ТА ЖЕ дыра, только эта функция её ещё
    #не получала вовсе): без current_subject_lessons предмет, УБРАННЫЙ из плана группы,
    #продолжал висеть в журнале родителя со старыми занятиями — тот же скоуп, что и у
    #student_journal (см. её докстринг), иначе «формат один в один» из докстрина этой
    #функции был бы неправдой.
    #Плюс раздельное обучение (§ролей, 3.6.1): чужая подгруппа ребёнка родителю не видна.
    lessons = W.filter_lessons_by_student_subgroup(db, W.current_subject_lessons(
        db, child.group_name,
        W.group_lessons(db, child.group_name, year=ty, semester=ts), is_archive), child.id)
    records = W.student_visible_records(db, child.surname, child.name, child.group_name)
    scale_map = W.lesson_scale_map(db, lessons)

    from collections import OrderedDict
    buckets = OrderedDict()
    for l in lessons:
        buckets.setdefault(l.subject, []).append(l)
    #Тот же живой баг, что и на Главной/Журнале студента: предмет ДЕЙСТВУЮЩЕГО плана
    #без единого занятия должен быть виден («по нему пока пусто»), не пропадать молча.
    if not is_archive:
        for subj in sorted(W.group_plan_subjects(db, child.group_name, cfg) - set(buckets)):
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
                         "hours": W.hours_progress(db, child.group_name, subj, ls, ty, ts)})

    return {"student": {"id": child.id,
                        "full_name": (child.full_name or f"{child.surname} {child.name}").strip(),
                        "group": child.group_name},
            "subjects": subjects, "term": {"year": ty, "semester": ts},
            "methodology": W.grading.methodology_text(cfg)}


@router.get("/parent/zet")
def parent_zet(student_id: str = Query(""), year: str = Query(""), semester: int = Query(0),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """ЗЕТ ребёнка — та же строка, что у студента (docs/PLAN-ZET.md §7.6), без таблицы
    группы и порога перевода (это дело куратора/администрации, не родителя)."""
    _require_parent(user)
    child = _resolve_child(db, user, student_id)
    cfg = W.load_config(db)
    from .web import _resolve_term
    ty, ts = _resolve_term(cfg, year, semester)
    return {"term": {"year": ty, "semester": ts},
            **W.zet_summary_for_student(db, child.surname, child.name, child.group_name, ty, ts)}


@router.get("/parent/stats")
def parent_stats(student_id: str = Query(""), year: str = Query(""), semester: int = Query(0),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Статистика ребёнка — формат ОДИН В ОДИН с /web/student/stats (3.6).

    Заведено ради сводки на вкладке «ИИ Помощник»: родитель обязан видеть ровно то же,
    что видит его студент. Одинаковый формат — не мелочь: интерфейс переиспользует ту же
    карточку, и как только формы ответов разъедутся, разъедутся и цифры в двух кабинетах.

    Риск отчисления сюда входит намеренно: родитель — второй человек после куратора,
    которому есть смысл узнать о нём заранее, и узнать он должен из журнала, а не из
    приказа."""
    _require_parent(user)
    child = _resolve_child(db, user, student_id)
    cfg = W.load_config(db)
    from .web import _resolve_term
    ty, ts = _resolve_term(cfg, year, semester)
    is_archive = bool((year or "").strip() and semester)
    #Скоуп ТОТ ЖЕ, что у студента: только предметы действующего плана (отменённые
    #дисциплины остаются в базе, но в текущую сводку не идут) — см. student_stats.
    #Плюс раздельное обучение (§ролей, 3.6.1): чужая подгруппа ребёнка не в счёт.
    lessons = W.filter_lessons_by_student_subgroup(db, W.current_subject_lessons(
        db, child.group_name,
        W.group_lessons(db, child.group_name, year=ty, semester=ts), is_archive), child.id)
    records = W.student_visible_records(db, child.surname, child.name, child.group_name)
    #Долги/пропуски — как у студента: занятия без штампа термина остаются (иначе реальные
    #долги «исчезают»), а занятия с ЧУЖИМ заданным термином (прошлый курс) — отсекаются
    #(current_term_lessons), иначе повторяющийся предмет тянул бы долги за все курсы.
    #В архиве — строго по выбранному термину.
    dl = lessons if is_archive else W.filter_lessons_by_student_subgroup(
        db, W.current_term_lessons(db, child.group_name, W.current_subject_lessons(
            db, child.group_name, W.group_lessons(db, child.group_name), is_archive), cfg), child.id)
    scale_map = W.lesson_scale_map(db, lessons if is_archive else lessons + dl)
    risk = W.dropout_risk_for_student(db, child.surname, child.name, child.group_name,
                                      cfg=cfg, lessons=dl)
    return {
        "student": {"id": child.id,
                    "full_name": (child.full_name or f"{child.surname} {child.name}").strip(),
                    "group": child.group_name},
        "term": {"year": ty, "semester": ts},
        "average": W.average(lessons, records, cfg, scale=scale_map),
        "per_subject": W.per_subject_with_plan(db, child.group_name, lessons, records,
                                               cfg, scale=scale_map, is_archive=is_archive),
        "absences": W.absences(dl, records),
        "debts": W.debts(dl, records, scale=scale_map),
        "risk": risk if risk["visible"] else None,
    }


@router.post("/parent/vector/ask")
def parent_vector_ask(payload: dict = Body(...),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """«Вектор» для родителя: отвечает по журналу ЕГО ребёнка и ничего больше.

    Факты собираем от лица самого студента — передаём в общий обработчик его же запись
    пользователя. Это намеренно: студенческий скоуп уже написан, протестирован и режет
    выборки по группе и ФИО. Своя ветка «как студент, но для родителя» рано или поздно
    отстала бы от него, и отстала бы именно в сторону утечки чужих данных.

    Тон при этом родительский (voice_role) — иначе Вектор говорил бы взрослому «твой
    средний балл», как будто учится он сам."""
    _require_parent(user)
    child = _resolve_child(db, user, (payload.get("student_id") or "").strip())
    question = (payload.get("message") or "").strip()
    from .web import answer_vector_question, user_ui_locale
    #Язык — РОДИТЕЛЯ (того, кто спрашивает и читает ответ), а не ребёнка: у `child` своя
    #независимая настройка интерфейса, которую сам родитель никогда не выбирал.
    result = answer_vector_question(question, child, db, voice_role="parent",
                                    locale=user_ui_locale(user))
    result["student_id"] = child.id
    return result


# ── Согласие студента ───────────────────────────────────────────────────────────────
@router.get("/student/parent-links")
def student_parent_links(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Заявки родителей на доступ к МОЕМУ журналу (и уже выданные доступы)."""
    if user.role != "student":
        raise HTTPException(status_code=403, detail="Доступно только для студента")
    links = (db.query(ParentLink)
             .filter(ParentLink.student_id == user.id)
             .order_by(ParentLink.created_at.desc()).all())
    return {"links": [_link_view(db, l) for l in links]}


@router.post("/student/parent-links/{link_id}/decide")
def student_decide_link(link_id: str, payload: dict = Body(...),
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Студент подтверждает или отзывает доступ родителя к своему журналу.

    Отзыв доступен и после подтверждения: согласие на обработку своих данных человек
    вправе отозвать в любой момент, и кнопка «передумал» должна быть у него, а не только
    у администратора."""
    if user.role != "student":
        raise HTTPException(status_code=403, detail="Доступно только для студента")
    link = db.get(ParentLink, link_id)
    #Чужую заявку не показываем и не трогаем — 404 одинаков для «нет» и «не твоя».
    if link is None or link.student_id != user.id:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    approve = bool(payload.get("approve"))
    link.status = "active" if approve else "revoked"
    link.decided_at = _now_iso()
    db.commit()
    audit.log(db, actor=user.login, role="student",
              action="parent.link.approve" if approve else "parent.link.revoke",
              target=link.parent_id, detail=f"связь {link.id}")
    return {"ok": True, "status": link.status}


# ── Управление привязками (админ и куратор) ─────────────────────────────────────────
@router.get("/staff/parent-links")
def staff_parent_links(group: str = Query(""),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Связи родителей по группе. Куратору — только его группы, админу — любые."""
    if user.role not in ("admin", "teacher"):
        raise HTTPException(status_code=403, detail="Доступно сотрудникам колледжа")
    groups = None if user.role == "admin" else set(_curator_groups(user))
    if groups is not None and not groups:
        raise HTTPException(status_code=403, detail="Вы не курируете ни одной группы")
    links = db.query(ParentLink).all()
    out = []
    for link in links:
        student = db.get(User, link.student_id)
        if student is None or student.deleted:
            continue
        if group and student.group_name != group:
            continue
        if groups is not None and student.group_name not in groups:
            continue
        out.append(_link_view(db, link))
    out.sort(key=lambda v: (v["student"]["full_name"], v["parent"]["full_name"]))
    return {"links": out}


@router.post("/staff/parent-links")
def staff_create_link(payload: dict = Body(...),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Привязать родителя к студенту. Создаётся в статусе `pending` — доступа пока нет."""
    if user.role not in ("admin", "teacher"):
        raise HTTPException(status_code=403, detail="Доступно сотрудникам колледжа")
    parent = db.get(User, (payload.get("parent_id") or "").strip())
    student = db.get(User, (payload.get("student_id") or "").strip())
    if parent is None or parent.deleted or parent.role != "parent":
        raise HTTPException(status_code=404, detail="Родитель не найден")
    if student is None or student.deleted or student.role != "student":
        raise HTTPException(status_code=404, detail="Студент не найден")
    if not _may_manage_student(user, student):
        raise HTTPException(status_code=403, detail="Эта группа вами не курируется")

    lid = parent_link_id(parent.id, student.id)
    link = db.get(ParentLink, lid)
    if link is None:
        link = ParentLink(id=lid, parent_id=parent.id, student_id=student.id)
        db.add(link)
    #Повторная привязка (в т.ч. после отзыва) снова требует согласия — молча воскрешать
    #отозванный доступ нельзя, иначе отзыв студента обходится парой кликов сотрудника.
    link.status = "pending"
    link.created_at = _now_iso()
    link.created_by = user.login
    link.decided_at = ""
    db.commit()
    audit.log(db, actor=user.login, role=user.role, action="parent.link.create",
              target=student.id, detail=f"родитель {parent.login}")
    return {"ok": True, "id": lid, "status": link.status}


@router.delete("/staff/parent-links/{link_id}")
def staff_delete_link(link_id: str,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Снять доступ родителя (например, по заявлению). Строку помечаем отозванной, а не
    удаляем: должно остаться видно, что доступ существовал и когда его сняли."""
    if user.role not in ("admin", "teacher"):
        raise HTTPException(status_code=403, detail="Доступно сотрудникам колледжа")
    link = db.get(ParentLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Связь не найдена")
    student = db.get(User, link.student_id)
    if student is None or not _may_manage_student(user, student):
        raise HTTPException(status_code=403, detail="Эта группа вами не курируется")
    link.status = "revoked"
    link.decided_at = _now_iso()
    db.commit()
    audit.log(db, actor=user.login, role=user.role, action="parent.link.revoke",
              target=link.student_id, detail=f"связь {link_id}")
    return {"ok": True}


# ── Справочник родителей (для админки и куратора) ───────────────────────────────────
@router.get("/staff/parents")
def staff_parents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Список родителей — виден ТОЛЬКО сотрудникам (админ, куратор)."""
    if user.role not in ("admin", "teacher"):
        raise HTTPException(status_code=403, detail="Доступно сотрудникам колледжа")
    if user.role == "teacher" and not _curator_groups(user):
        raise HTTPException(status_code=403, detail="Вы не курируете ни одной группы")
    rows = (db.query(User)
            .filter(User.role == "parent", User.deleted == False)  # noqa: E712
            .order_by(User.surname, User.name).all())
    return {"parents": [{"id": p.id, "login": p.login,
                         "full_name": (p.full_name or p.name or "").strip(),
                         #дата выдачи пароля — сам пароль показать нельзя (в базе хеш)
                         "password_set_at": p.password_set_at or ""} for p in rows]}


@router.post("/admin/parents")
def admin_create_parent(payload: dict = Body(...),
                        admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Завести родителя. Пароль хешируется тем же гибридом, что у всех остальных ролей."""
    import uuid
    login = (payload.get("login") or "").strip()
    surname = (payload.get("surname") or "").strip()
    name = (payload.get("name") or "").strip()
    password = payload.get("password") or ""
    if not (login and surname and password):
        raise HTTPException(status_code=400, detail="Нужны логин, фамилия и пароль")
    existing = (db.query(User)
                .filter(User.login == login, User.deleted == False).first())  # noqa: E712
    if existing is not None:
        raise HTTPException(status_code=409, detail="Пользователь с таким логином уже есть")
    uid = f"par:u:{uuid.uuid4()}"
    row = User(id=uid, role="parent", login=login,
               surname=surname, name=name,
               full_name=f"{surname} {name}".strip(),
               updated_at=_now_iso(), deleted=False)
    set_user_password(row, password)     #хеш и дата выдачи — одной функцией
    db.add(row)
    db.commit()
    audit.log(db, actor=admin.login, role="admin", action="parent.create", target=login)
    return {"ok": True, "id": uid, "login": login}


@router.put("/admin/parents/{parent_id}")
def admin_update_parent(parent_id: str, payload: dict = Body(...),
                        admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Правка родителя (id — ключ, как у student/teacher). Меняем ФИО/пароль — ЛОГИН
    не редактируем (в отличие от ФИО, логин участвует в проверке уникальности при
    создании, и смена задним числом рискует коллизией; отдельная форма для этого не
    заказывалась)."""
    row = db.get(User, parent_id)
    if row is None or row.deleted or row.role != "parent":
        raise HTTPException(status_code=404, detail="Родитель не найден")
    if "surname" in payload:
        row.surname = (payload.get("surname") or "").strip()
    if "name" in payload:
        row.name = (payload.get("name") or "").strip()
    row.full_name = f"{row.surname or ''} {row.name or ''}".strip()
    password = payload.get("password") or ""
    if password:
        set_user_password(row, password)
    row.updated_at = _now_iso()
    db.commit()
    audit.log(db, actor=admin.login, role="admin", action="parent.update", target=row.login)
    return {"ok": True, "id": parent_id}


@router.delete("/admin/parents/{parent_id}")
def admin_delete_parent(parent_id: str,
                        admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Мягкое удаление (deleted=1), как у student/teacher. Существующие ParentLink
    намеренно не трогаем — soft-delete в проекте нигде не каскадирует смежные данные."""
    row = db.get(User, parent_id)
    if row is None or row.deleted or row.role != "parent":
        raise HTTPException(status_code=404, detail="Родитель не найден")
    row.deleted = True
    row.updated_at = _now_iso()
    db.commit()
    audit.log(db, actor=admin.login, role="admin", action="parent.delete", target=row.login)
    return {"ok": True, "id": parent_id}
