"""
webdata.py — Общие выборки для веб-представлений (/web/*).

Читают серверную БД (SQLAlchemy) и формируют данные СТРОГО по роли: только то, что
пользователь вправе видеть, уже удобно для UI. В отличие от /sync/pull (отдаёт все
строки всех таблиц, включая хеши паролей) — эти выборки безопасно отдавать в браузер.

Расчёт среднего балла — через ЕДИНЫЙ модуль grading.py из корня репозитория
(инвариант §9: формулу НЕ дублируем). grading.py лёгкий (зависит только от typing),
поэтому импортируется на сервере без тяжёлых GUI-зависимостей.
"""
import os
import re
import sys

from sqlalchemy import or_

#grading.py лежит в корне репозитория рядом с server/. Разворачивание идёт из того
#же репо (см. server/DEPLOY.md: git clone <repo> && cd server), поэтому корень
#доступен. Добавляем его в sys.path и переиспользуем единый расчёт.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import grading  # noqa: E402
import study_hours  # noqa: E402  — общее с десктопом правило учебных часов
import dropout_risk  # noqa: E402  — общие с десктопом правила риска отчисления
from schedule.model import strip_subgroup_tag  # noqa: E402 — нормализация «…п/г»-меток ниже

from .models import (User, Lesson, Grade, ConfigKV, SubjectHours, Group,  # noqa: E402
                     subject_hours_id, StudentSubgroup, student_subgroup_id)


def load_config(db) -> dict:
    """Методика оценок (веса Н, учитывать ли пропуск/экзамены) из таблицы config.

    Терпимо к раскладке: строка со словарём вливается целиком, строка «ключ→значение»
    кладётся как есть. Отсутствующие ключи grading.avg_config добьёт дефолтами."""
    cfg = {}
    for row in db.query(ConfigKV).filter(ConfigKV.deleted == False):  # noqa: E712
        v = row.value
        if isinstance(v, dict):
            cfg.update(v)
        elif row.key:
            cfg[row.key] = v
    return cfg


_RETAKE_RE = re.compile(r"_retake(_\d+)?$")


def base_lesson_id(lesson_id: str) -> str:
    """Базовый id занятия без суффикса пересдачи: `<lid>_retake[_N]` → `<lid>`.
    Оценки пересдач хранятся отдельными строками с таким суффиксом в lesson_id."""
    return _RETAKE_RE.sub("", lesson_id or "")


def group_lesson_ids(db, group: str) -> set:
    """Множество БАЗОВЫХ id занятий группы (без надгробий) — для скоупинга оценок.

    Зачем: оценки хранятся по ключу surname|name|lesson_id БЕЗ группы, поэтому выборка
    по (surname, name) затягивает и строки ОДНОФАМИЛЬЦА-ТЁЗКИ из другой группы. Их
    занятия принадлежат другой группе, значит их lesson_id НЕ попадёт в этот набор —
    так мы отфильтровываем чужие оценки, не меняя формат ключа (совместимость синка)."""
    rows = db.query(Lesson.id).filter(
        Lesson.group_name == group, Lesson.deleted == False).all()  # noqa: E712
    return {r[0] for r in rows}


def student_records(db, surname: str, name: str, group: str | None = None,
                    allowed_lesson_ids: set | None = None) -> dict:
    """{lesson_id → оценка} для студента. Ключи пересдач (`<lid>_retake[_N]`) тоже
    приходят как отдельные строки grades — grading их учитывает по последней попытке.

    ЗАЩИТА ОТ ТЁЗОК. Если задана `group` (или готовый `allowed_lesson_ids`), оставляем
    только оценки, чьё занятие принадлежит этой группе — так студент/преподаватель
    никогда не видит оценок однофамильца из ДРУГОЙ группы. Легитимные оценки студента
    всегда стоят на занятиях его группы, поэтому фильтр ничего своего не теряет.
    Без параметров — прежнее поведение (полная выборка по имени)."""
    rows = db.query(Grade.lesson_id, Grade.grade).filter(
        Grade.student_f == surname, Grade.student_n == name,
        Grade.deleted == False).all()  # noqa: E712
    if group is None and allowed_lesson_ids is None:
        return {lid: g for lid, g in rows}
    allowed = allowed_lesson_ids if allowed_lesson_ids is not None else group_lesson_ids(db, group)
    return {lid: g for lid, g in rows if base_lesson_id(lid) in allowed}


def student_visible_records(db, surname: str, name: str, group: str | None = None,
                            cfg=None, today=None) -> dict:
    """Оценки студента ГЛАЗАМИ СТУДЕНТА — с учётом фазы учебного года.

    🔥 ЕДИНСТВЕННАЯ дверь для студенческих и родительских экранов (06.09.2026). Правило
    «после сессии видны только итоговые» читают витрина, журнал, статистика, ЗЕТ, долги,
    Вектор и ачивки — восемь мест. Разложить условие по ним значило бы восемь мест, где
    однажды забудут, а первое забытое здесь — чужой семестр в среднем балле, то есть ровно
    то, от чего правило и заведено (см. `grade_policy`).

    ⚠️ Преподаватель, куратор и администратор зовут ОБЫЧНЫЙ `student_records`: им нужна
    полная картина — вести журнал, разбирать долги, смотреть архив по семестрам. Сторож
    `test_grade_policy.py` следит, чтобы студенческие роутеры не ходили мимо этой функции.
    """
    from datetime import date
    from . import grade_policy
    cfg = cfg if cfg is not None else load_config(db)
    _ty, ts = current_term(cfg)
    ph = grade_policy.phase(today or date.today(), ts, cfg.get("grades_freeze_date"))
    return grade_policy.visible_records(student_records(db, surname, name, group), ph)


def grades_phase(db, cfg=None, today=None) -> str:
    """Фаза показа оценок для СТУДЕНТА — чтобы интерфейс мог объяснить, почему пусто.

    Молчаливо пустой журнал читается как поломка; подпись «идёт сессия, показаны итоговые»
    объясняет то же самое состояние и не заставляет никого искать несуществующий сбой.
    """
    from datetime import date
    from . import grade_policy
    cfg = cfg if cfg is not None else load_config(db)
    _ty, ts = current_term(cfg)
    return grade_policy.phase(today or date.today(), ts, cfg.get("grades_freeze_date"))


def current_term(cfg: dict) -> tuple:
    """Текущий учебный термин (год, семестр) из config, иначе — дефолт по дате.
    Год «YYYY/YYYY+1», семестр 1 (осень) | 2 (весна).

    🔥 ОВЕРРАЙД ИЗ БУДУЩЕГО ИГНОРИРУЕТСЯ, и это не перестраховка. Дважды — в 3.6.1 и
    снова 15.08.2026 — в `ConfigKV` оставался ручной ключ от кнопки «Перевод на курс»
    (`{'current_year': '2026/2027', 'current_semester': 1}` при живом августе 2026).
    Последствие каждый раз одно: от термина считается КУРС студента, импорт учебного
    плана записывает предметы следующего курса, и у группы оказываются предметы двух
    курсов сразу — у куратора одни, в админке другие. Оба раза чинилось руками в базе.

    Оверрайд НАЗАД (посмотреть закрытый семестр) — законный сценарий и работает как
    прежде. Оверрайд ВПЕРЁД означает, что продукт штампует новые данные периодом,
    который ещё не наступил, — законного применения у этого нет, а цена измерена.
    """
    from . import db as _db
    base = _db.default_term()
    y = (cfg.get("current_year") or "").strip()
    s = cfg.get("current_semester")
    if y and s:
        try:
            override = (y, int(s))
        except (TypeError, ValueError):
            return base
        if _term_key(override) > _term_key(base):
            #Раз в запуск: строка на каждый запрос превратила бы лог в шум, а молчать
            #нельзя — прошлые два раза оверрайд нашли только прямым разбором боевой базы.
            global _future_term_warned
            if not _future_term_warned:
                _future_term_warned = True
                import logging
                logging.getLogger("gradebook.webdata").warning(
                    "[термин] ручной оверрайд %s указывает в БУДУЩЕЕ относительно "
                    "календарного %s — игнорирую. Ключи current_year/current_semester в "
                    "таблице config стоит убрать: они уже дважды приводили к предметам "
                    "двух курсов сразу.", override, base)
            return base
        if _term_key(override) < _term_key(base):
            #🔥 ОБРАТНЫЙ СЛУЧАЙ, И ОН БЫЛ МОЛЧАЛИВЫМ (01.09.2026). Оверрайд назад —
            #законный сценарий (открыть закрытый семестр), поэтому он ПРИМЕНЯЕТСЯ и
            #здесь ничего не меняется. Но у него есть вторая, невидимая сторона: ключ
            #`current_year`, оставшийся с весны, 1 СЕНТЯБРЯ НЕ ДАЁТ УЧЕБНОМУ ГОДУ
            #НАЧАТЬСЯ. Продукт продолжает штамповать занятия прошлым семестром, курс
            #студентов не растёт, новые предметы ложатся в закрытый период — и понять
            #это можно только разбором боевой базы, ровно как оба прошлых раза с
            #оверрайдом вперёд. Разница лишь в направлении, цена та же.
            global _stale_term_warned
            if not _stale_term_warned:
                _stale_term_warned = True
                import logging
                logging.getLogger("gradebook.webdata").warning(
                    "[термин] ручной оверрайд %s ОТСТАЁТ от календарного %s — применяю "
                    "его (просмотр архива законен), но если это не намеренно, учебный "
                    "год у продукта ещё не начался: занятия штампуются прошлым "
                    "семестром. Проверьте ключи current_year/current_semester в config.",
                    override, base)
        return override
    return base


_future_term_warned = False
#Тот же приём, что у `_future_term_warned`: раз в запуск. Строка на каждый запрос
#превратила бы лог в шум, и её перестали бы читать — а именно чтением лога этот класс
#дефектов и ловится.
_stale_term_warned = False


def default_term_by_date() -> tuple:
    """Термин ПО КАЛЕНДАРЮ, без оглядки на ручной оверрайд. Нужен тем, кто проверяет сам
    оверрайд (`routers/web/terms.py`), — через `current_term` они получили бы уже
    отфильтрованное значение и сравнивали бы его само с собой."""
    from . import db as _db
    return _db.default_term()


def _term_key(term: tuple) -> tuple:
    """Сравнимый ключ термина: («2025/2026», 2) → (2025, 2). Мусор → (0, 0), то есть
    заведомо «старее» любого настоящего — неразборчивый оверрайд не должен побеждать."""
    try:
        return int(str(term[0]).split("/")[0]), int(term[1])
    except (TypeError, ValueError, IndexError):
        return 0, 0


def group_lessons(db, group: str, subject: str | None = None,
                  year: str | None = None, semester: int | None = None):
    """Занятия группы (опц. по предмету И учебному периоду), в порядке десктопа.

    Фильтр по термину (year+semester) — основа долгосрочного журнала: показываем
    занятия конкретного семестра. Без термина — все периоды (обратная совместимость)."""
    q = db.query(Lesson).filter(Lesson.group_name == group,
                                Lesson.deleted == False)  # noqa: E712
    if subject:
        q = q.filter(Lesson.subject == subject)
    if year:
        q = q.filter(Lesson.year == year)
    if semester:
        q = q.filter(Lesson.semester == int(semester))
    return q.order_by(Lesson.subject, Lesson.type, Lesson.number, Lesson.hour).all()


def group_subject_list(db, group: str) -> list:
    """Все предметы группы: справочник группы (Group.subjects) + предметы реальных занятий.

    ⚠️ Занятий одного достаточно НЕ БЫЛО: импорт учебного плана ВСГУТУ и импорт из
    расписания пишут только Group.subjects и не создают ни одной строки Lesson — предмет,
    по которому преподаватель ещё не открыл журнал, существует, но нигде не показывался.
    Тот же перекос уже чинили отдельно у куратора (curator_group_subjects) и в ЗЕТ
    (zet_summary_for_student); это ТРЕТЬЕ место, поэтому источник вынесен сюда — чтобы
    следующий потребитель брал готовое, а не заводил четвёртую копию правила.

    ⚠️ И обратный перекос (3.6): предмет, УБРАННЫЙ из плана, продолжал попадать сюда из
    старых занятий — Вектор перечислял студенту предметы, которых у него уже нет. Поэтому
    когда план задан, ОН и есть ответ; занятия досыпаются, только если плана нет вовсе
    (группы, заведённые до импорта учебного плана). Цена решения: предмет, по которому
    преподаватель завёл занятия, но админ не внёс его в группу, в список не попадёт —
    это видно и лечится добавлением предмета в группу, в отличие от бесшумно
    накапливающегося мусора из отменённых дисциплин.
    """
    plan = group_plan_subjects(db, group)
    if plan:
        return sorted(plan)
    return sorted({strip_subgroup_tag(l.subject) for l in group_lessons(db, group)
                   if l.subject and strip_subgroup_tag(l.subject)})


def per_subject_with_plan(db, group: str, lessons, records, cfg, scale=None,
                          is_archive: bool = False):
    """Средние по предметам + предметы ПЛАНА, по которым занятий ещё нет.

    Одна функция на студента и на родителя СОЗНАТЕЛЬНО: их сводки рисует одна и та же
    карточка, и стоит формату разойтись — в двух кабинетах будут разные списки предметов
    по одному и тому же студенту. Такое расхождение уже ловил тест
    `test_parent_stats_mirrors_student_stats`, поэтому правило вынесено сюда, а не
    скопировано в оба эндпоинта.

    В архиве план не досыпаем: тогда учились по другому набору предметов."""
    rows = per_subject_averages(lessons, records, cfg, scale=scale)
    if is_archive:
        return rows
    seen = {r["subject"] for r in rows}
    for subj in sorted(group_plan_subjects(db, group, cfg) - seen):
        rows.append({"subject": subj, "average": 0.0, "lessons": 0, "grades": 0})
    return rows


def portal_course(row) -> int:
    """Курс группы ПО ПОРТАЛУ ВСГУТУ (столбец индекса расписания) или None.

    Читает тот же TTL-кэш индекса, что и вкладка «Расписание» с её кнопками курса —
    второго похода на портал здесь не возникает, а значит и второй правды о курсе тоже.
    Портал недоступен, группы нет в индексе, категория неизвестна — None, и вызывающий
    честно падает на формулу.

    ⚠️ Именно `groups_by_course_CACHED`, а не `groups_by_course`: вторая при промахе
    кэша идёт в сеть с таймаутом 20 с, а эта функция стоит на пути ГЛАВНОЙ СТРАНИЦЫ
    кабинета — внутри десктопной программы в оффлайне такой запрос ждал бы портал на
    каждой загрузке. Кэш греется в фоне, курс доезжает со следующим запросом.

    ⚠️ Категорию берём у САМОЙ группы (`Group.category`), а не подставляем колледж
    всем подряд: у бакалавриата и заочного свои индексы, и группа из чужого индекса
    просто не нашлась бы — курс молча стал бы «неизвестен» у всех, кроме колледжа."""
    try:
        from . import schedule_web
        category = (getattr(row, "category", "") or "").strip() or schedule_web.default_category()
        for course, names in schedule_web.groups_by_course_cached(category).items():
            if row.name in names:
                c = int(course)
                return c if c >= 1 else None
    except Exception:
        return None
    return None


def group_course(db, group: str, cfg=None):
    """Курс группы (1..N) или None, если посчитать не из чего.

    ДВА источника, и порядок между ними — суть этой функции:
      1. **портал ВСГУТУ** (`portal_course`) — столбец индекса расписания. Авторитет;
      2. год поступления (`Group.enrollment_year`) + текущий термин, через общий
         `study_hours.course_and_semester`. Запасной путь, когда портал молчит.

    🔥 ПОЧЕМУ ПОРТАЛ ГЛАВНЕЕ, А НЕ НАША ДАТА. Одна и та же болезнь ловилась ТРИЖДЫ, и
    каждый раз лечилась сдвигом календарной границы термина: 1 июля → 25 августа →
    1 сентября (история — в докстринге `db.default_term`). Третий раз её принёс живой
    отзыв 27.08.2026: портал уже перевёл К74/1 на 3 курс, вкладка расписания показывала
    3, а профиль студента — по-прежнему 2, потому что наша граница ещё не наступила.
    Обещание «1 сентября не разойдётся с порталом НИКОГДА» оказалось неправдой — портал
    переключается когда переключается, и угадать эту дату календарём нельзя в принципе.

    Поэтому курс больше не УГАДЫВАЕТСЯ: если группа стоит в столбце «3 курс», студенты
    этой группы видят 3 курс в тот же момент, без нашей арифметики и без ожидания даты.

    ⚠️ Портал задаёт КУРС, но НЕ учебный термин. Термин (`current_term`) двигает оценки,
    часы, ЗЕТ и ключи `SubjectHours` — тянуть его за столбцом чужого сайта значило бы
    переписать журнал по внешнему сигналу. Здесь меняется ровно то, о чём спрашивали:
    число курса, которое видит человек.

    None — это честно «посчитать не из чего», а не «первый курс»: подставлять единицу
    значило бы приписать группе курс, которого никто не вводил."""
    row = db.query(Group).filter(Group.name == group,
                                 Group.deleted == False).first()  # noqa: E712
    if not row:
        return None
    from_portal = portal_course(row)
    if from_portal is not None:
        return from_portal
    if not row.enrollment_year:
        return None
    ty, ts = current_term(cfg if cfg is not None else load_config(db))
    try:
        course, _sem = study_hours.course_and_semester(int(row.enrollment_year), ty, ts)
    except (TypeError, ValueError):
        return None
    return course if course >= 1 else None


def group_plan_subjects(db, group: str, cfg=None) -> set:
    """ДЕЙСТВУЮЩИЙ учебный план группы — или пустое множество.

    Два источника, оба ведёт АДМИНИСТРАТОР, и оба означают «предмет в программе»:
      • `Group.subjects` — список предметов группы;
      • `SubjectHours` за ТЕКУЩИЙ термин — часы/ЗЕТ/назначение преподавателя.

    ⚠️ Второй источник обязателен, и вот почему. Занятие можно завести только по
    НАЗНАЧЕННОЙ паре (группа, предмет) — а назначение живёт именно в `SubjectHours`.
    Значит бывает предмет, который админ назначил и по которому уже идут занятия, но в
    `Group.subjects` внести забыл. Считать его «убранным из плана» и прятать оценки
    по нему — потеря данных на ровном месте; отличить же его от по-настоящему
    отменённого предмета по одним занятиям невозможно.

    Пусто значит «плана нет», а НЕ «предметов нет»: у групп, заведённых до импорта
    учебного плана, ни один из источников не заполняли. Отличать эти два случая
    обязательно — см. `current_subject_lessons`.

    ⚠️ Живой баг родителя: строки вида «…- 1 п/г»/«…- 2 п/г» — метка подгруппы у
    лабораторных, которую портал пишет ПРЯМО в название предмета (см. schedule/
    model.py). Импорт из расписания (`admin_bind_subjects`/`admin_import_schedule_
    category`) до фикса парсера клал их в `Group.subjects` как отдельные «предметы» —
    ни одного занятия/оценки под ними нет и не будет. Нормализуем (снимаем суффикс)
    ЗДЕСЬ, а не только на входе парсера: у уже поражённых групп (импорт был ДО фикса)
    `Group.subjects` остаётся как есть на диске, повторный импорт админом никто не
    гарантирует, а нормализация лечит всех сразу, включая прошлые импорты, без
    миграции данных. ⚠️ Именно НОРМАЛИЗАЦИЯ, а не отбрасывание — у части предметов
    (языки/лабораторные классы) на портале нет НИ ОДНОЙ немеченой строки вовсе, и
    отбрасывание убрало бы такой предмет из плана целиком (см. strip_subgroup_tag)."""
    row = db.query(Group).filter(Group.name == group,
                                 Group.deleted == False).first()  # noqa: E712
    subs = {strip_subgroup_tag(s) for s in (row.subjects if row else None) or []
            if s and strip_subgroup_tag(s)}
    ty, ts = current_term(cfg if cfg is not None else load_config(db))
    subs |= {strip_subgroup_tag(r.subject) for r in db.query(SubjectHours).filter(
        SubjectHours.group_name == group, SubjectHours.year == (ty or ""),
        SubjectHours.semester == int(ts or 0),
        SubjectHours.deleted == False).all()  # noqa: E712
             if r.subject and strip_subgroup_tag(r.subject)}
    return subs


def current_subject_lessons(db, group: str, lessons, is_archive: bool = False):
    """Отсеять занятия по предметам, УБРАННЫМ из учебного плана группы.

    ⚠️ Зачем. Удаление предмета из группы НЕ удаляет уже созданные занятия и оценки —
    и правильно делает: это история, стирать её нельзя. Но статистика, сводка на вкладке
    «ИИ Помощник» и ответы Вектора перечисляли предметы ПО ЗАНЯТИЯМ, поэтому исключённый
    из плана предмет продолжал висеть в диаграммах со своим средним баллом (живой отзыв
    3.6: «показывает старые предметы, а не актуальные»). Человек видит в списке то, чего
    у него в этом семестре нет вовсе.

    Правило:
      • план задан и смотрим ТЕКУЩИЙ термин → показываем только предметы плана;
      • план пуст (его просто не заполняли) → показываем всё, как раньше. Иначе у групп
        без импорта плана статистика опустела бы целиком — ошибка куда хуже исходной;
      • АРХИВ (явно выбранный прошлый семестр) НИКОГДА не фильтруем сегодняшним планом:
        тогда велись другие предметы, и прятать их — переписывать прошлое задним числом.
        Тот же принцип уже принят у куратора (`curator_group_subjects`).
    """
    if is_archive:
        return lessons
    plan = group_plan_subjects(db, group)
    if not plan:
        return lessons
    return [l for l in lessons if l.subject in plan]


def current_term_lessons(db, group: str, lessons, cfg=None):
    """Занятия ТОЛЬКО текущего термина — безопасно для среднего/долгов/пропусков/риска.

    ⚠️ Зачем ОТДЕЛЬНО от `current_subject_lessons` (тот фильтрует по ИМЕНИ предмета).
    `group_lessons(db, group)` без year/semester возвращает историю группы ЦЕЛИКОМ —
    предмет, который есть в плане КАЖДЫЙ семестр (например, «Физическая культура»),
    проходит фильтр по имени всегда, и без термина в «средний балл сейчас» и «текущие
    долги» подмешивались бы оценки за ВСЕ прошлые курсы группы — живой баг 3.6.1: «оценки
    по физре не должны быть циклом» при переходе на новый курс.

    Занятия БЕЗ штампа термина (легаси — заведены до появления `year`/`semester` у
    `Lesson`) ОСТАЮТСЯ как есть: отличить их «текущий» от «прошлый» нечем, а прятать по
    ним реальные долги хуже, чем один раз посчитать их текущими (тот же компромисс уже
    принят в `student_stats` для показателей по умолчанию)."""
    ty, ts = current_term(cfg if cfg is not None else load_config(db))
    return [l for l in lessons if not l.year or (l.year, int(l.semester or 0)) == (ty, ts)]


def list_terms(db) -> list:
    """Список учебных периodов, по которым есть занятия: [{year, semester}], новые сверху.
    Для селектора термина в журнале/статистике и для архива прошлых семестров."""
    rows = db.query(Lesson.year, Lesson.semester).filter(
        Lesson.deleted == False, Lesson.year != "").distinct().all()  # noqa: E712
    terms = sorted({(y, int(s or 0)) for y, s in rows if y},
                   key=lambda t: (t[0], t[1]), reverse=True)
    return [{"year": y, "semester": s} for y, s in terms]


def lesson_pairs(lessons):
    return [(l.id, l.type) for l in lessons]


def hours_done(lessons) -> int:
    """Пройденные академические часы — через общий с десктопом study_hours."""
    return study_hours.hours_done(lessons)


def hours_plan(db, group: str, subject: str, year: str, semester) -> int:
    """Плановые часы предмета для группы на семестр (0 — админ ещё не задавал)."""
    row = db.get(SubjectHours, subject_hours_id(group, subject, year, semester))
    if row is None or row.deleted:
        return 0
    return int(row.hours_total or 0)


def hours_progress(db, group: str, subject: str, lessons, year: str, semester) -> dict:
    """{done, total} — «пройдено X из Y часов» для шапки журнала.

    total=0 означает «часы не заданы»: клиент в этом случае просто не показывает строку,
    а не рисует «24 из 0». Придумывать план за администратора нельзя."""
    return {"done": hours_done(lessons),
            "total": hours_plan(db, group, subject, year, semester)}


# ── Раздельное обучение (§ролей, 3.6.1) ──────────────────────────────────────────
def subject_hours_row(db, group: str, subject: str, year: str, semester):
    """Строка часов (или None) — общий лукап, чтобы не собирать subject_hours_id в
    каждом месте, где нужны split/teacher_id_2."""
    row = db.get(SubjectHours, subject_hours_id(group, subject, year, semester))
    return row if (row and not row.deleted) else None


def teacher_owned_subgroups(row, teacher_id: str) -> set:
    """Какие подгруппы ведёт преподаватель по строке часов раздельного предмета:
    {1}, {2}, {1,2} или пусто (не назначен вовсе). Пустой teacher_id_2 при split=True —
    единственный назначенный преподаватель ведёт ОБЕ подгруппы по очереди (маленький
    класс, см. CLAUDE.md) — тогда {1,2}, а не только {1}."""
    if row is None or not row.split or not teacher_id:
        return set()
    t1, t2 = row.teacher_id or "", row.teacher_id_2 or ""
    out = set()
    if teacher_id == t1:
        out.add(1)
        if not t2:
            out.add(2)
    if teacher_id == t2:
        out.add(2)
    return out


def student_subgroup(db, group: str, subject: str, year: str, semester, student_id: str):
    """Подгруппа (1/2) студента по ЭТОМУ предмету и термину — None, если куратор ещё не
    расставил (студент не входит ни в чью подгруппу, пока не назначен явно)."""
    row = db.get(StudentSubgroup, student_subgroup_id(group, subject, year, semester, student_id))
    return row.subgroup if (row and not row.deleted) else None


def group_student_subgroups(db, group: str, subject: str, year: str, semester) -> dict:
    """{student_id: 1|2} — вся роспись по подгруппам этого предмета/термина, одним запросом
    (для редактора куратора и для фильтрации ростера в журнале препода)."""
    rows = (db.query(StudentSubgroup)
            .filter(StudentSubgroup.group_name == group, StudentSubgroup.subject == subject,
                    StudentSubgroup.year == (year or ""), StudentSubgroup.semester == int(semester or 0),
                    StudentSubgroup.deleted == False).all())  # noqa: E712
    return {r.student_id: r.subgroup for r in rows}


def next_lesson_number(db, group: str, subject: str, ltype: str, subgroup: int = 0) -> int:
    """Следующий номер занятия этого типа — МАКСИМУМ+1, а не количество строк (лекция
    может лежать в базе двумя строками по академическим часам — сколькими именно,
    показывает study_hours.row_hours_map, предполагать это нельзя).
    При раздельном обучении (§ролей, 3.6.1) считается ОТДЕЛЬНО в пределах ТОЙ ЖЕ
    подгруппы — иначе у двух преподавателей одной группы независимые «Практика №1»
    слились бы в одну общую последовательность и сбивали счёт друг другу (живой
    запрос: «чтобы разные преподы могли не сбиться со счёта»)."""
    rows = db.query(Lesson.number).filter(
        Lesson.group_name == group, Lesson.subject == subject, Lesson.type == ltype,
        Lesson.subgroup == int(subgroup or 0), Lesson.deleted == False).all()  # noqa: E712
    return (max((r[0] or 0) for r in rows) + 1) if rows else 1


def filter_lessons_by_student_subgroup(db, lessons, student_id: str) -> list:
    """Оставляет занятия, которые видны СТУДЕНТУ: subgroup=0 (обычное занятие, либо
    «Совместно» на раздельном предмете) — всегда, иначе — только его собственная
    подгруппа по ИМЕННО ЭТОМУ предмету и термину (один студент может быть в разных
    подгруппах по разным предметам).

    Не split-предметы subgroup=0 у ВСЕХ занятий — фильтр их не трогает, поведение как
    раньше. Дёшево для типичного случая: ранний `continue` без похода в БД."""
    cache = {}
    out = []
    for l in lessons:
        if not l.subgroup:
            out.append(l)
            continue
        key = (l.group_name, l.subject, l.year, l.semester)
        if key not in cache:
            cache[key] = student_subgroup(db, l.group_name, l.subject, l.year, l.semester, student_id)
        if cache[key] == l.subgroup:
            out.append(l)
    return out


def teacher_scale(user) -> str:
    """Шкала ОДНОГО преподавателя (§ролей, 3.3.1) — для мест, где все lessons в списке
    заведомо ведёт ОН ЖЕ (журнал/статистика по своим назначениям): дешевле, чем
    lesson_scale_map, лишних запросов не требует."""
    sc = (user.prefs or {}).get("grading_scale") or grading.DEFAULT_SCALE
    return sc if sc in grading.SCALES else grading.DEFAULT_SCALE


def lesson_scale_map(db, lessons) -> dict:
    """{lesson_id: шкала} — какой шкалой (§ролей, 3.3.1) вводил оценку преподаватель,
    ведущий именно ЭТО занятие. Разрешение — через ТО ЖЕ назначение препод↔предмет↔
    группа, что и видимость групп (SubjectHours.teacher_id, см. teacher_assignments):
    занятие → (группа,предмет,термин) → назначенный преподаватель → его User.prefs
    ["grading_scale"]. Без назначения ИЛИ без выбора шкалы — DEFAULT_SCALE ("5"),
    то есть сегодняшнее поведение бит-в-бит. Нужен именно СЛОВАРЬ (не одна строка на
    все lessons), потому что список занятий часто смешивает НЕСКОЛЬКО предметов
    разом (общий средний студента/группы по всем предметам) — у каждого предмета
    может быть свой преподаватель со своей шкалой."""
    if not lessons:
        return {}
    pair_terms = {(l.group_name, l.subject, l.year or "", int(l.semester or 0))
                  for l in lessons}
    year_sem = {(y, s) for (_g, _sub, y, s) in pair_terms}
    hours_rows = []
    for y, s in year_sem:
        hours_rows.extend(db.query(SubjectHours).filter(
            SubjectHours.year == y, SubjectHours.semester == s,
            SubjectHours.deleted == False).all())  # noqa: E712
    teacher_by_pair = {}
    for r in hours_rows:
        if r.teacher_id:
            teacher_by_pair[(r.group_name, r.subject, r.year or "",
                             int(r.semester or 0))] = r.teacher_id
    teacher_ids = set(teacher_by_pair.values())
    scale_by_teacher = {}
    if teacher_ids:
        for u in db.query(User).filter(User.id.in_(teacher_ids)).all():
            sc = (u.prefs or {}).get("grading_scale") or grading.DEFAULT_SCALE
            scale_by_teacher[u.id] = sc if sc in grading.SCALES else grading.DEFAULT_SCALE
    out = {}
    for l in lessons:
        key = (l.group_name, l.subject, l.year or "", int(l.semester or 0))
        tid = teacher_by_pair.get(key)
        out[l.id] = scale_by_teacher.get(tid, grading.DEFAULT_SCALE)
    return out


def average(lessons, records, cfg, scale=None) -> float:
    """Средний балл — единый расчёт grading.practice_average. scale — {lesson_id:
    шкала} из lesson_scale_map (или None/строка — тогда как раньше, "5" для всех)."""
    return grading.practice_average(lesson_pairs(lessons), records, cfg,
                                    scale=scale if scale is not None else grading.DEFAULT_SCALE)


def graded_count(lessons, records) -> int:
    """Сколько РЕАЛЬНЫХ отметок стоит у студента по этим занятиям.

    Оценкой считаем непустое значение на оцениваемом занятии (практика/ДЗ/… —
    `grading.is_practice`, плюс экзамен). Буквы посещаемости («Н»/«Б»/«О») — это тоже
    непустое значение и тоже отметка в журнале: скрывать «Н» из счётчика значило бы
    показывать прогульщику «оценок 0» рядом с реально не аттестованным студентом.
    """
    n = 0
    for l in lessons:
        if grading.is_practice(l.type) or l.type == "Экзамен":
            if (records.get(l.id) or "").strip():
                n += 1
    return n


def per_subject_averages(lessons, records, cfg, scale=None):
    """Список {subject, average, lessons, grades} — средний по каждому предмету группы.

    `grades` (сколько отметок реально стоит) добавлен в 3.6 для сводки на вкладке
    «ИИ Помощник»: средний без числа оценок вводит в заблуждение — «5.0» по одной
    работе и «5.0» по двенадцати выглядят одинаково, а значат разное."""
    from collections import OrderedDict
    buckets = OrderedDict()
    for l in lessons:
        buckets.setdefault(l.subject, []).append(l)
    out = []
    for subj, ls in buckets.items():
        out.append({"subject": subj, "average": average(ls, records, cfg, scale=scale),
                    "lessons": len(ls), "grades": graded_count(ls, records)})
    return out


def debts(lessons, records, scale=None):
    """Причины задолженности (порт vector/intents._is_debt). scale — {lesson_id: шкала}
    из lesson_scale_map, для распознавания «завалено» в шкале ведущего преподавателя."""
    reasons = []
    scale_map = scale if isinstance(scale, dict) else None
    for l in lessons:
        if grading.is_practice(l.type):
            v = records.get(l.id)
            lscale = (scale_map.get(l.id, grading.DEFAULT_SCALE) if scale_map is not None
                     else (scale or grading.DEFAULT_SCALE))
            if v == "Н" or (v and grading.is_failed_scaled(v, lscale)):
                #ДЗ — тоже долг: «Н» на домашней работе значит «не сдал», а не «не был».
                what = "ДЗ" if l.type == "ДЗ" else "практика"
                ending = "о" if l.type == "ДЗ" else "а"
                reasons.append(f"{what} №{l.number} не сдан{ending} ({v})")
        elif l.type == "Экзамен":
            v = grading.latest_exam_value(l.id, records)
            if grading.is_failed(v):   #единый источник fail-логики (см. grading.is_failed)
                reasons.append(f"экзамен №{l.number} не зачтён")
    return reasons


def absences(lessons, records):
    """Пропущенные АКАДЕМИЧЕСКИЕ ЧАСЫ по меткам: {"Н", "Б", "О", "всего"}.

    ⚠️ ДЗ здесь НЕ учитывается, хотя в средний балл идёт наравне с практикой: «Н» на
    домашней работе означает «не сдал», а не «не был на занятии». Записать это в пропуски
    значило бы наказать студента посещаемостью за несданную домашку.

    🔥 «О» — ОПОЗДАЛ, и в сумму пропусков НЕ входит (3.7.6). До этого метка называлась
    «уважительная причина» и складывалась с «Н» и «Б», хотя преподаватели ставили её
    именно за опоздание. Цена ошибки была не косметической: опоздавший СТУДЕНТ БЫЛ НА
    ЗАНЯТИИ, а стоило это ему столько же, сколько прогул — те же часы в отчёте куратора,
    тот же вклад в индекс риска отчисления (`attendance_hours` ниже питает именно его),
    тот же «пропустил N часов» в глазах родителя.
    Счётчик при этом ОСТАЁТСЯ отдельным ключом: систематические опоздания — реальный
    сигнал для куратора, терять его нельзя.

    🔥 ПРАКТИКА УЧИТЫВАЛАСЬ ОДНОЙ МЕТКОЙ ИЗ ТРЁХ (починено 31.08.2026). Ветка выглядела
    так: `elif l.type == "Практика" and v == "Н"`. То есть «Б» на практике не попадала в
    пропуски ВООБЩЕ (проболевший все практики семестра показан родителю и куратору как
    «пропусков нет»), «О» не попадала в свой счётчик, а «Н» стоила один «час» вместо
    двух. Вторая половина того же дефекта — единица измерения: считались СТРОКИ, а
    называлось это часами, и практика (одна строка на пару) весила вдвое меньше лекции
    (две строки на ту же пару). Теперь перевод строки в часы делает `study_hours.row_hours_map`
    — тот самый единый источник, мимо которого часы считать запрещено (§4.15).

    ⚠️ Метка на НЕчасоносном занятии (ДЗ, экзамен) не учитывается по построению — такой
    строки нет в карте `row_hours_map`, и отдельного условия для ДЗ больше не нужно.

    ⚠️ **Часы на строку СЧИТАЮТСЯ, а не предполагаются.** Первая версия этой починки
    брала константу «строка лекции = 1 час, практики = 2» — по комментариям, которые
    повторяются по всему проекту. Комментарии описывали Qt-журнал, удалённый 13.08.2026:
    живой `POST /web/teacher/lesson` кладёт занятие ОДНОЙ строкой любого типа, значит в
    боевой базе лежит СМЕСЬ, и константа врала бы вдвое на половине лекций. Раскладку
    пары по строкам определяет `study_hours.row_hours_map` по фактическим данным.
    Держит `tests/test_attendance_marks.py` и `tests/test_study_hours.py`."""
    per_row = study_hours.row_hours_map(lessons)
    res = {"Н": 0.0, "Б": 0.0, "О": 0.0}
    for l in lessons:
        hours = per_row.get(l.id)
        if not hours:
            continue
        v = records.get(l.id)
        if v in res:
            res[v] += hours
    #Наружу — целые часы: пара делится между своими строками нацело в обеих живых
    #раскладках (одна строка → 2, две → по 1), дробь возможна лишь на битых данных
    #(три строки у пары), и показывать «1.33 ч» человеку незачем.
    res = {k: int(round(v)) for k, v in res.items()}
    res["всего"] = res["Н"] + res["Б"]
    return res


def failed_exam_subjects(lessons, records) -> list:
    """Предметы с НЕСДАННЫМ экзаменом/зачётом — по ПОСЛЕДНЕЙ попытке.

    Пересдачи учитывает `grading.latest_exam_value` (единственный источник этой логики,
    инвариант §8): студент, завaливший экзамен и закрывший его пересдачей, задолженности
    не имеет, и попасть в риск не должен."""
    out = []
    for l in lessons:
        if l.type == "Экзамен":
            v = grading.latest_exam_value(l.id, records)
            if grading.is_failed(v) and l.subject not in out:
                out.append(l.subject)
    return out


def silent_subjects(lessons, records) -> list:
    """Предметы, где занятия ШЛИ, но нет ни одной отметки.

    ⚠️ Считаем только по ОЦЕНИВАЕМЫМ занятиям (практика/ДЗ/экзамен): предмет из одних
    лекций отметок не предполагает вовсе, и записывать его в «студент пропал» — ложное
    срабатывание."""
    from collections import OrderedDict
    buckets = OrderedDict()
    for l in lessons:
        if grading.is_practice(l.type) or l.type == "Экзамен":
            buckets.setdefault(l.subject, []).append(l)
    return [subj for subj, ls in buckets.items() if graded_count(ls, records) == 0]


def attendance_hours(lessons, records) -> tuple:
    """(пропущено, всего, из них неуважительных) в АКАДЕМИЧЕСКИХ ЧАСАХ.

    ⚠️ Прежде единицей была СТРОКА занятия, а называлась она часом — известная
    нестыковка, которую тут сознательно не чинили, «чтобы риск сходился с цифрой
    пропусков рядом». Довод был верен, а вывод из него — нет: сходиться надо было
    ПОЧИНИВ ОБЕ, что и сделано 31.08.2026 (см. `absences`).

    ⚠️ `total` считается ТОЙ ЖЕ картой строк, что и пропущенное. Свойство `missed <= total`
    обязано держаться всегда: доля `absence_hours / total_hours` питает индекс риска
    отчисления, и уехавшая за 100 % доля необъяснима ни человеку, ни нам.

    ⚠️ **С `study_hours.hours_done` этот `total` НЕ обязан совпадать, и раньше здесь было
    написано обратное** (поправлено 31.08.2026 после возражения Полковника). `hours_done`
    ключует пары БЕЗ предмета — её зовут по ОДНОМУ предмету, для подписи «Пройдено X из Y ч»
    против плана `SubjectHours`. Сюда же приходят занятия студента по ВСЕМ предметам сразу,
    и там `hours_done` схлопнула бы «Лекцию №1» математики с «Лекцией №1» физики. Это разные
    величины с разным Y, и считать одну через другую нельзя."""
    a = absences(lessons, records)
    total = int(round(sum(study_hours.row_hours_map(lessons).values())))
    return a["всего"], total, a["Н"]


def attendance_percent(lessons, records) -> int:
    """Посещаемость в процентах — ЕДИНСТВЕННЫЙ расчёт этой величины в продукте.

    🔥 Их было два, и второй жил своим циклом в `routers/web/student.py`, считая ТОЛЬКО
    ЛЕКЦИИ. Практика в процент не входила вовсе: прогулявший все практики видел
    «посещаемость 100 %» рядом с честным числом пропущенных часов — две правды на одном
    экране, ровно тот же класс дефекта, что уже ловил здесь Полковник в 3.7.6 с меткой
    «О». Лечится не третьей поправкой, а тем, что расчёт остался один.

    Занятий нет — 100 %, а не 0: «нечего пропускать» и «пропустил всё» это разные
    события, и второе нельзя показывать первому курсу первого сентября."""
    missed, total, _ = attendance_hours(lessons, records)
    if not total:
        return 100
    return round(100 * (total - missed) / total)


def dropout_risk_for_student(db, surname: str, name: str, group: str,
                             cfg=None, lessons=None, records=None,
                             zet_earned=None, zet_min=None,
                             student_id: str = "") -> dict:
    """Индекс риска отчисления студента — сбор ФАКТОВ + общий модуль `dropout_risk`.

    Сами правила и веса живут в корневом `dropout_risk.py` (общем с десктопом); здесь
    только выборка цифр из БД. lessons/records можно передать готовыми — в списке
    группы они уже посчитаны, и второй поход в базу на каждого студента был бы
    заметен даже на одноядерном VPS.

    `student_id` — необязателен и нужен только ради подгруппы (см. ниже). Вызывающие,
    которые идут циклом по группе, объект студента уже держат в руках и передают его id
    даром; остальные позволяют найти его по ФИО одним запросом.

    ⚠️ Динамику (`trend_delta`) сознательно НЕ передаём: единственная временная метка
    у оценки — `updated_at`, которую двигает ЛЮБАЯ правка, в том числе техническая.
    «Средний упал» по такой метке — это выдуманная цифра, а Вектор и всё вокруг него
    держатся на правиле «не выдумывать». Появится честная история оценок — фактор в
    модуле уже готов и включится одной строкой.
    """
    if cfg is None:
        cfg = load_config(db)
    if lessons is None:
        #Без явного списка — берём ТЕКУЩИЙ термин (не всю историю группы), иначе риск
        #считался бы по оценкам за прошлые курсы для предметов вроде «Физической
        #культуры», которые есть в плане каждый семестр.
        lessons = current_term_lessons(db, group, group_lessons(db, group), cfg)
    #🔥 РАЗДЕЛЬНОЕ ОБУЧЕНИЕ: занятия ЧУЖОЙ подгруппы обязаны уйти ЗДЕСЬ, а не у
    #вызывающего (31.08.2026, второе возражение Полковника). Из шести вызывающих фильтр
    #применял ОДИН — студенческий; куратор, преподаватель и родитель передавали занятия
    #ВСЕЙ группы. Пока подгруппы не было в ключе пары, это скрывалось: у «Практики №1»
    #обеих подгрупп получалась одна пара. Теперь пар две, знаменатель `total_hours`
    #удваивается, и доля пропусков в индексе риска выходит вдвое заниженной — тем же
    #числом, что и до починки, только с другой стороны дроби.
    #⚠️ Фильтр стоит ПОСЛЕ получения списка и работает независимо от того, передали его
    #снаружи или собрали здесь: повторная фильтрация уже отфильтрованного безвредна, а
    #«пусть фильтрует вызывающий» — это шесть мест, где однажды забудут.
    sid = student_id or (getattr(student_by_name(db, surname, name, group), "id", "") or "")
    if sid:
        lessons = filter_lessons_by_student_subgroup(db, lessons, sid)
    if records is None:
        records = student_records(db, surname, name, group)
    scale_map = lesson_scale_map(db, lessons)
    missed, total, unexcused = attendance_hours(lessons, records)
    per_subj = {r["subject"]: r["average"]
                for r in per_subject_averages(lessons, records, cfg, scale=scale_map)}
    return dropout_risk.assess(
        average=average(lessons, records, cfg, scale=scale_map),
        subject_averages=per_subj,
        debts=len(debts(lessons, records, scale=scale_map)),
        failed_exams=failed_exam_subjects(lessons, records),
        absence_hours=missed, total_hours=total, unexcused_hours=unexcused,
        silent_subjects=silent_subjects(lessons, records),
        zet_earned=zet_earned, zet_min=zet_min,
        has_activity=bool(lessons),
    )


def group_risk_report(db, group: str, cfg=None, subjects=None) -> list:
    """Риск по ВСЕМ студентам группы — для преподавателя и куратора.

    subjects — ограничить занятия этим набором предметов (преподаватель судит только по
    СВОИМ: чужой предмет он всё равно не видит, и учитывать его в «риске по моему
    списку» было бы враньём). None — вся группа целиком (взгляд куратора).

    Занятия группы читаются ОДИН раз на всю группу, а не на каждого студента."""
    if cfg is None:
        cfg = load_config(db)
    #ТЕКУЩИЙ термин — та же причина, что в dropout_risk_for_student: повторяющийся
    #предмет (напр. «Физическая культура») иначе тянул бы риск из прошлых курсов группы.
    lessons = current_term_lessons(db, group, group_lessons(db, group), cfg)
    if subjects is not None:
        lessons = [l for l in lessons if l.subject in subjects]
    out = []
    for s in students_in_group(db, group):
        recs = student_records(db, s.surname, s.name, group)
        risk = dropout_risk_for_student(db, s.surname, s.name, group,
                                        cfg=cfg, lessons=lessons, records=recs)
        out.append({"surname": s.surname, "name": s.name,
                    "full_name": display_name(s), "student_id": s.id, "risk": risk})
    return out


def zet_summary_for_student(db, surname: str, name: str, group: str, year: str, semester) -> dict:
    """Сводка ЗЕТ студента за термин (docs/done/PLAN-ZET.md) — {earned,total,pct,subjects[]}.
    Собирает занятия/оценки/шкалы преподавателей и сводит через ЧИСТЫЕ функции
    study_hours (та же логика для студента/куратора/родителя, один расчёт)."""
    lessons = group_lessons(db, group, year=year, semester=semester)
    records = student_records(db, surname, name, group)
    scale_map = lesson_scale_map(db, lessons)
    hrows = {r.subject: r for r in db.query(SubjectHours).filter(
        SubjectHours.group_name == group, SubjectHours.year == (year or ""),
        SubjectHours.semester == int(semester or 0), SubjectHours.deleted == False).all()}  # noqa: E712
    #РУБЕЖ СЕМЕСТРА (вариант C, docs/done/PLAN-ZET.md §2). Термин закрыт целиком, если он НЕ
    #текущий (смотрят архив прошлого семестра). Внутри текущего рубеж по предмету проходит,
    #когда пройдены его плановые часы (см. term_over на предмет ниже). Без этого предмет без
    #экзамена засчитывал бы все ЗЕТ по первой же положительной оценке (баг Влада).
    term_is_current = ((year or ""), int(semester or 0)) == current_term(load_config(db))
    by_subject = {}
    for l in lessons:
        by_subject.setdefault(l.subject, []).append(l)
    #⚠️ §3.5.5: перечислять ОБЯЗАНЫ hrows (реальный учебный план — SubjectHours с заданным
    #ЗЕТ), а НЕ by_subject (что уже есть в журнале). Раньше перебирали by_subject — предмет,
    #по которому препод ещё не завёл ни одного занятия в этом термине, выпадал не только
    #из «сдано», но и из ЗНАМЕНАТЕЛЯ («всего ЗЕТ») целиком: группа с 5 предметами по
    #плану, но занятиями заведёнными только по одному, показывала «всего 2.9» вместо
    #суммы всех пяти. Пустой список занятий — это ЧЕСТНО «не сдан» (`subject_zet_state`
    #на []), а не повод скрыть предмет из суммы.
    rows = []
    for subj, row in hrows.items():
        zet = row.zet
        if zet is None:
            continue
        ls = by_subject.get(subj, [])
        scale = scale_map.get(ls[0].id, grading.DEFAULT_SCALE) if ls else grading.DEFAULT_SCALE
        #Рубеж по предмету: архивный термин ИЛИ пройдены плановые часы (0 = план не задан,
        #тогда единственный рубеж — экзамен или закрытие термина).
        hours_total = int(row.hours_total or 0)
        term_over = (not term_is_current) or (hours_total > 0 and study_hours.hours_done(ls) >= hours_total)
        state = study_hours.subject_zet_state(ls, records, zet, term_over=term_over, scale=scale)
        rows.append({"subject": subj, "zet": zet, "state": state})
    return study_hours.zet_summary(rows)


def group_zet_report(db, group: str, year: str, semester, min_zet) -> list:
    """Отчёт группы для кнопки перевода на курс (docs/done/PLAN-ZET.md §7.4) — по каждому
    студенту сводит zet_summary_for_student, дальше решает study_hours.group_zet_report."""
    students = []
    for s in students_in_group(db, group):
        students.append({"student_id": s.id, "display_name": display_name(s),
                         "summary": zet_summary_for_student(db, s.surname, s.name, group,
                                                            year, semester)})
    return study_hours.group_zet_report(students, min_zet)


def student_by_name(db, surname: str, name: str, group: str):
    """Студент группы по ФИО — или None. Нужен там, где вызывающий знает человека только
    по имени (риск отчисления, ведомости, отчёт куратора).

    ⚠️ ТЁЗКИ. При двух однофамильцах с одним именем в ОДНОЙ группе отдаём None, а не
    «первого попавшегося»: единственное, ради чего эту функцию зовут, — подгруппа
    студента, и ошибиться человеком тут значит показать куратору чужую посещаемость.
    Без результата вызывающий просто не фильтрует подгруппу — поведение как раньше."""
    rows = db.query(User).filter(
        User.role == "student", User.group_name == group,
        User.surname == surname, User.name == name,
        User.deleted == False).all()  # noqa: E712
    return rows[0] if len(rows) == 1 else None


def students_in_group(db, group: str):
    """Студенты группы — это пользователи с ролью student и этой group_name."""
    return db.query(User).filter(
        User.role == "student", User.group_name == group,
        User.deleted == False).order_by(User.surname, User.name).all()  # noqa: E712


def teacher_assignments(db, teacher_id: str, year: str, semester,
                        allow_fallback: bool = True) -> list:
    """Пары (группа, предмет), ЯВНО назначенные преподавателю на этот термин —
    ЕДИНЫЙ источник правды «какие группы видит препод» (см. models.SubjectHours.
    teacher_id). Заменяет старую teacher_groups()/«предмет числится у препода» —
    та отдавала ЛЮБУЮ группу, где предмет вообще упоминался, даже группам, которых
    препод в глаза не видел (баг, найденный в 3.3.1: препод с 5 предметами видел
    все группы этих предметов, а не только свои).

    Без назначения на семестр (админ ещё не расставил) — пустой список: это НЕ
    «видно всё», а «видно ничего, пока не назначили» — осознанно строже старого
    поведения, ошибка в другую сторону (спрятать своё) тут безопаснее."""
    if not teacher_id:
        return []
    #teacher_id_2 — второй преподаватель раздельного обучения (§ролей, 3.6.1, подгруппа
    #2). Без него препод, назначенный ТОЛЬКО во второй слот, не проходил бы вообще
    #никуда: журнал/сводка/офлайн-синк отвечали бы «эта группа вам не назначена».
    rows = (db.query(SubjectHours)
            .filter(or_(SubjectHours.teacher_id == teacher_id,
                       SubjectHours.teacher_id_2 == teacher_id),
                    SubjectHours.year == (year or ""),
                    SubjectHours.semester == int(semester or 0),
                    SubjectHours.deleted == False).all())  # noqa: E712
    pairs = sorted({(r.group_name, r.subject) for r in rows if r.group_name and r.subject})
    if pairs or not allow_fallback:
        return _drop_subjects_outside_current_plan(db, pairs, year, semester)
    #⚠️ НОЛЬ назначений — это НЕ «админ назначил пусто», и разница стоила боевого простоя.
    #30.07.2026 нагрузка обнулилась сразу у ВСЕХ преподавателей (проверено на живых
    #аккаунтах Saha/ddxd/Dixm/Artur/Golubev): в базе 13 строк SubjectHours, teacher_id
    #пуст во всех — до появления редактора назначений заполнять его было нечем. Пустой
    #журнал получили и сайт, и десктоп (он ходит в тот же API), и офлайн-копия: строгий
    #скоуп заодно перестал привозить группы в /sync/pull.
    #Решение тимлида: до расстановки назначений работаем по ПРЕЖНЕМУ правилу. Замысел
    #точного скоупа цел — появилось хоть одно назначение, и работают ТОЛЬКО они (return
    #выше), поэтому «препод видит чужие группы» не возвращается. Это мост на время ввода
    #данных, а не режим: снимается удалением этой ветки, когда назначения расставлены.
    #
    #🔥 НО У МОСТА НЕ БЫЛО ПЕРИОДА, И ЭТО ДЕРЖАЛО ПРЕПОДАВАТЕЛЯ НА ПРОШЛОМ КУРСЕ
    #(05.09.2026). `_assignments_fallback` собирает пары из `Lesson` БЕЗ фильтра по
    #термину, поэтому после смены курса преподаватель продолжал видеть прошлогодние
    #группы как действующие — «сейчас преподы не открепляются» из жалобы Влада. Сузить
    #мост глобально до текущего термина нельзя: сразу после перевода текущих занятий нет
    #НИ У КОГО, и все разом ушли бы во вторую ветку фолбэка — «все группы, где числится
    #мой предмет», то есть весь колледж. Поэтому мост выключается ПОГРУППНО и только
    #там, где администратор уже принял решение (см. course_rollover.advance).
    return _drop_subjects_outside_current_plan(
        db, _drop_groups_with_explicit_assignments(
            db, _assignments_fallback(db, teacher_id), year, semester),
        year, semester)


def _drop_groups_with_explicit_assignments(db, pairs: list, year: str, semester) -> list:
    """Убрать из моста группы, переведённые на новый курс.

    У такой группы «нет строки с моим id» означает «мне тут не назначено», а не «данные
    ещё не ввели», — и мост, придуманный против пустого журнала у всех сразу, здесь
    работает против своей же цели: возвращает преподавателю прошлогоднюю нагрузку.

    ⚠️ Спрашиваем ОДИН раз на группу: функция стоит на пути каждого запроса кабинета
    преподавателя, а групп в паре может быть десяток.
    """
    if not pairs:
        return pairs
    from . import course_rollover as CR
    cache = {}
    out = []
    for group, subject in pairs:
        if group not in cache:
            cache[group] = CR.assignments_must_be_explicit(db, group, year, semester)
        if not cache[group]:
            out.append((group, subject))
    return out


def _drop_subjects_outside_current_plan(db, pairs: list, year: str, semester) -> list:
    """Пара (группа, предмет) может пережить сам предмет: `Group.subjects` при импорте
    учебного плана/расписания ЗАМЕНЯЮТ (не объединяют, см. admin_import_esstu), а
    старая строка `SubjectHours.teacher_id` или старое занятие — остаются. Препод
    продолжал бы видеть в выборе предмет, которого у группы больше нет (живой баг:
    «предмет убрали у группы, а журнал у препода остался»).

    Фильтруем ТОЛЬКО для ТЕКУЩЕГО термина — архивный просмотр прошлого семестра НЕ
    обязан зависеть от сегодняшнего плана: тогда велись другие предметы, и прятать их
    задним числом было бы переписыванием истории (тот же принцип, что уже применён в
    `current_subject_lessons`). Группа без плана вовсе (пусто) не фильтруется — иначе
    у групп, куда план ещё не завозили, пропали бы вообще все назначения."""
    if not pairs or (year, int(semester or 0)) != current_term(load_config(db)):
        return pairs
    cache = {}
    out = []
    for g, s in pairs:
        if g not in cache:
            cache[g] = group_plan_subjects(db, g)
        plan = cache[g]
        if not plan or s in plan:
            out.append((g, s))
    return out


def _assignments_fallback(db, teacher_id: str) -> list:
    """Пары (группа, предмет) по прежнему правилу — для преподавателя БЕЗ назначений.

    Два источника, как было до перехода на явные назначения: группы его РЕАЛЬНЫХ занятий
    и группы, где его предмет числится в справочнике. Второй нужен новичку — у него ещё
    нет ни одного занятия, и без этого он не смог бы создать первое."""
    from .models import Group
    user = db.get(User, teacher_id)
    subjects = {s for s in ((user.subjects if user else None) or []) if s}
    if not subjects:
        return []
    #Сначала — группы, где у него РЕАЛЬНО есть занятия. Это и есть его нагрузка.
    pairs = set()
    for g_name, subj in (db.query(Lesson.group_name, Lesson.subject)
                         .filter(Lesson.subject.in_(subjects),
                                 Lesson.deleted == False).distinct().all()):  # noqa: E712
        if g_name and subj:
            pairs.add((g_name, subj))
    if pairs:
        return sorted(pairs)
    #⚠️ Справочник групп — ТОЛЬКО если занятий нет ни одного, и вот почему.
    #«Предмет числится у группы» — очень слабый признак: предметы вроде «Компьютерные
    #сети» стоят у десятков групп колледжа, и преподаватель получал в выборе весь
    #колледж вместо своих трёх групп (замечено на бою 30.07.2026). Пользоваться таким
    #списком нельзя — своё в нём не найти.
    #Но и убирать его целиком нельзя: у НОВОГО преподавателя занятий ещё нет, и без этой
    #ветки он не смог бы создать первое. Поэтому она — последняя, а не равноправная.
    for g in db.query(Group).filter(Group.deleted == False).all():  # noqa: E712
        for subj in subjects & set(g.subjects or []):
            pairs.add((g.name, subj))
    return sorted(pairs)


def teacher_group_names(db, teacher_id: str, year: str, semester) -> list:
    """Только имена групп из teacher_assignments — для мест, которым предмет не нужен
    (аудитория уведомлений, список групп для создания чата и т.п.)."""
    return sorted({g for g, _s in teacher_assignments(db, teacher_id, year, semester)})


def _teacher_subject_rows(db, teacher, ty: str, ts, cfg,
                          only_subject: str = "", only_group: str = ""):
    """Общая выборка для диаграмм вкладки «ИИ Помощник» у преподавателя (3.6.1): по
    каждой его паре (группа, предмет) — строка `{"average": …}` на каждого студента
    группы, средний ИМЕННО по этому предмету (а не общий средний студента по всем
    дисциплинам, как у куратора — иначе чужой предмет того же студента красил бы
    категорию, к которой этот преподаватель отношения не имеет). Отдаёт список
    `(group, subject, rows)` — вызывающий сам решает, как их сгруппировать/агрегировать.
    Шкала — СВОЯ шкала преподавателя (`teacher_scale`), как и в /teacher/stats и
    /teacher/summary: все эти занятия его собственные, лукап по каждому занятию отдельно
    (как у куратора, где занятия могут быть НЕСКОЛЬКИХ преподавателей) тут не нужен.
    `only_subject`/`only_group` — сузить одной парой (для drill-down по конкретному
    предмету/группе), пустая строка — без фильтра."""
    scale = teacher_scale(teacher)
    out = []
    for group, subject in teacher_assignments(db, teacher.id, ty, ts):
        if only_subject and subject != only_subject:
            continue
        if only_group and group != only_group:
            continue
        lessons = group_lessons(db, group, subject=subject, year=ty, semester=ts)
        if not lessons:
            continue
        rows = []
        for s in students_in_group(db, group):
            recs = student_records(db, s.surname, s.name, group)
            rows.append({"average": average(lessons, recs, cfg, scale=scale)})
        out.append((group, subject, rows))
    return out


def _avg_of(rows) -> float:
    vals = [r["average"] for r in rows if r["average"]]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def teacher_subjects_overview(db, teacher, ty: str, ts) -> dict:
    """Сводка для верхнего уровня диаграмм: ОБЩАЯ категоризация (по всем парам
    группа-предмет сразу) + по КАЖДОМУ предмету (агрегат по всем группам, где он
    ведётся). Категории/пороги — curator_report.categorize, ТЕ ЖЕ, что красят
    дашборды преподавателя/куратора (единый набор порогов на весь продукт)."""
    from . import curator_report as CR
    cfg = load_config(db)
    triples = _teacher_subject_rows(db, teacher, ty, ts, cfg)
    all_rows = []
    by_subject: dict[str, list] = {}
    for _group, subject, rows in triples:
        all_rows.extend(rows)
        by_subject.setdefault(subject, []).extend(rows)
    subjects = [{"subject": subj, "students": len(rows), "avg": _avg_of(rows),
                "categories": CR.categorize(rows)}
               for subj, rows in sorted(by_subject.items())]
    return {"overall": {"students": len(all_rows), "avg": _avg_of(all_rows),
                        "categories": CR.categorize(all_rows)},
            "subjects": subjects}


def teacher_subject_groups(db, teacher, ty: str, ts, subject: str) -> dict:
    """Второй уровень (drill-down по предмету): та же категоризация, но ОДНА строка
    на каждую группу, где преподаватель ведёт этот предмет — для сравнения групп
    между собой."""
    from . import curator_report as CR
    cfg = load_config(db)
    triples = _teacher_subject_rows(db, teacher, ty, ts, cfg, only_subject=subject)
    groups = [{"group": group, "students": len(rows), "avg": _avg_of(rows),
              "categories": CR.categorize(rows)}
             for group, _subject, rows in triples]
    return {"subject": subject, "groups": groups}


def curator_summary_groups(db, user, ty: str, ts) -> list:
    """Курируемые группы для вкладки «ИИ Помощник» (3.6.1) — та же форма ответа, что
    у `/teacher/summary`'s `groups[]` (group/students/average/at_risk/subjects[]), чтобы
    TeacherOverview.vue рисовал их ОДНИМ и тем же шаблоном, просто вторым разделом.

    ⚠️ Отличие от teacher_summary принципиальное: там предметы группы — те, что
    НАЗНАЧЕНЫ преподавателю (teacher_assignments), здесь — ВЕСЬ ДЕЙСТВУЮЩИЙ учебный
    план группы (group_plan_subjects), включая предметы ДРУГИХ преподавателей — куратор
    должен видеть картину группы целиком, а не только свою нагрузку. Живой отзыв:
    «курируемые группы со всеми предметами» вообще не показывались — панель знала
    только про explicit-назначенные преподавателю пары."""
    cfg = load_config(db)
    out = []
    for group in sorted(set(user.curated_groups or [])):
        subjects = sorted(group_plan_subjects(db, group, cfg))
        if not subjects:
            continue
        lessons = current_subject_lessons(db, group, group_lessons(db, group, year=ty, semester=ts), False)
        studs = students_in_group(db, group)
        recs = {s.id: student_records(db, s.surname, s.name, group) for s in studs}
        #Куратор видит ЧУЖИЕ предметы — своей единой шкалы у него нет, лукап по каждому
        #занятию (как у отчёта/Вектора), а не teacher_scale одного человека.
        scale_map = lesson_scale_map(db, lessons)
        subj_rows = []
        for subject in subjects:
            sl = [l for l in lessons if l.subject == subject]
            vals = [v for v in (average(sl, recs[s.id], cfg, scale=scale_map) for s in studs) if v > 0]
            subj_rows.append({"subject": subject,
                              "average": round(sum(vals) / len(vals), 2) if vals else 0.0,
                              "graded_students": len(vals), "lessons": len(sl)})
        all_vals = [v for v in (average(lessons, recs[s.id], cfg, scale=scale_map) for s in studs) if v > 0]
        at_risk = 0
        for s in studs:
            r = dropout_risk_for_student(db, s.surname, s.name, group,
                                         cfg=cfg, lessons=lessons, records=recs[s.id])
            if r["visible"] and r["level"] in ("medium", "high", "critical"):
                at_risk += 1
        out.append({"group": group, "students": len(studs),
                    "average": round(sum(all_vals) / len(all_vals), 2) if all_vals else 0.0,
                    "at_risk": at_risk, "subjects": subj_rows})
    return out


def display_name(user) -> str:
    return user.full_name or f"{user.surname} {user.name}".strip()


def first_name(user) -> str:
    """Имя БЕЗ отчества (первое слово name) — для раздельного показа/ввода.
    name хранит «Имя Отчество» и остаётся ключом оценок, поэтому имя берём как
    первое слово, а отчество отдаём отдельным полем (см. patronymic_of)."""
    n = (getattr(user, "name", "") or "").strip()
    return n.split(" ", 1)[0] if " " in n else n


def patronymic_of(user) -> str:
    """Отчество: из отдельного поля users.patronymic, а если оно пусто (напр. строка
    пришла со старого десктопа без этого поля) — из хвоста name. Так отчество всегда
    показывается раздельно, независимо от источника строки."""
    p = (getattr(user, "patronymic", "") or "").strip()
    if p:
        return p
    n = (getattr(user, "name", "") or "").strip()
    return n.split(" ", 1)[1].strip() if " " in n else ""
