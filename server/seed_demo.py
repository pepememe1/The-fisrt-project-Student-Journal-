"""
seed_demo.py — демо-данные для проверки веб-версии (идемпотентно).

Создаёт студента webtest / webtest1 с группой, предметом, парой практик и оценками,
чтобы на сайте сразу было что показать (средний балл, журнал, статистика). Пишет
НАПРЯМУЮ в серверную БД через модели — без HTTP и барьера устройства.

Запуск:  cd server && python seed_demo.py
Удалить: этого студента можно потом убрать (login=webtest) в десктоп-админке.
"""
from datetime import datetime, timezone

from app.db import SessionLocal, init_db, default_term
from app.models import (User, Group, Subject, Lesson, Grade, ParentLink, SubjectHours,
                        parent_link_id, subject_hours_id, set_user_password)

NOW = datetime.now(timezone.utc).isoformat()
GROUP = "К74/1"
SUBJECT = "Разработка программных модулей"
SURNAME, NAME = "Тестов", "Тест"


def upsert(db, model, pk_field, pk_value, **fields):
    row = db.get(model, pk_value)
    if row is None:
        row = model(**{pk_field: pk_value})
        db.add(row)
    for k, v in fields.items():
        setattr(row, k, v)
    row.updated_at = NOW
    return row


def main():
    init_db()
    db = SessionLocal()
    try:
        upsert(db, Group, "id", "g:webtest", name=GROUP, subjects=[SUBJECT])
        upsert(db, Subject, "id", "s:webtest", name=SUBJECT)
        student = upsert(db, User, "id", "stud:webtest", role="student", login="webtest",
                         surname=SURNAME, name=NAME, full_name=f"{SURNAME} {NAME}", group_name=GROUP)
        set_user_password(student, "webtest1")

        # Три роли нужны не только тестам API: без них ручной UX-приёмке пришлось бы
        # смотреть преподавателя и родителя через пустые или чужие кабинеты. Все записи
        # идемпотентны и живут только в локальной demo-БД.
        teacher = upsert(db, User, "id", "teach:webtest", role="teacher", login="teacherweb",
                         full_name="Семёнова Анна Петровна", surname="Семёнова", name="Анна Петровна",
                         subjects=[SUBJECT], curated_groups=[GROUP])
        set_user_password(teacher, "teacherweb1")
        parent = upsert(db, User, "id", "parent:webtest", role="parent", login="parentweb",
                        full_name="Тестова Марина Сергеевна", surname="Тестова", name="Марина Сергеевна")
        set_user_password(parent, "parentweb1")

        year, semester = default_term()
        hours = upsert(db, SubjectHours, "id", subject_hours_id(GROUP, SUBJECT, year, semester),
                       group_name=GROUP, subject=SUBJECT, year=year, semester=semester,
                       hours_total=72, teacher_id=teacher.id)
        hours.updated_at = NOW
        link_id = parent_link_id(parent.id, student.id)
        link = db.get(ParentLink, link_id)
        if link is None:
            link = ParentLink(id=link_id, parent_id=parent.id, student_id=student.id)
            db.add(link)
        link.status, link.created_at, link.created_by, link.decided_at = "active", NOW, "demo-seed", NOW

        lessons = [
            ("web-L1", "Практика", 1, "Переменные и типы", "05.09.2025", "5"),
            ("web-L2", "Практика", 2, "Условия и циклы", "12.09.2025", "4"),
            ("web-L3", "Практика", 3, "Функции", "19.09.2025", "5"),
            ("web-L4", "Лекция", 1, "Введение в модули", "26.09.2025", ""),
        ]
        for lid, ltype, num, topic, date, grade in lessons:
            upsert(db, Lesson, "id", lid, group_name=GROUP, subject=SUBJECT,
                   type=ltype, number=num, topic=topic, date=date)
            if grade:
                gid = f"{SURNAME}|{NAME}|{lid}"
                upsert(db, Grade, "id", gid, student_f=SURNAME, student_n=NAME,
                       lesson_id=lid, grade=grade)

        db.commit()
        print("Готово. Демо-студент создан:")
        print("  логин:  webtest")
        print("  пароль: webtest1")
        print(f"  группа: {GROUP}, предмет: {SUBJECT}, оценки: 5,4,5 (средний 4.67)")
        print("  преподаватель: teacherweb / teacherweb1")
        print("  родитель:      parentweb / parentweb1")
    finally:
        db.close()


if __name__ == "__main__":
    main()
