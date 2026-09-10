# PLAN-ZET.md — Зачётные единицы (ЗЕТ) в GradeBookAI

> Этот документ — техническое задание для реализации. Читай вместе с `CLAUDE.md`.
> Реализуй поэтапно, каждый этап — отдельный коммит. Не трогай `grading.py` —
> ЗЕТ не влияют на средний балл. Расширяй `SubjectHours` (уже в SYNC_MODELS).

---

## 1. Что такое ЗЕТ

**ЗЕТ (зачётная единица трудоёмкости)** — стандартная единица нагрузки по
ФГОС СПО/ВО. Используется во всех колледжах и вузах РФ для учёта трудоёмкости
дисциплин и перевода студентов на следующий курс.

```
1 ЗЕТ = 36 академических часов
```

Трудоёмкость включает: аудиторные часы (лекции + практики) + самостоятельная
работа + экзамен/зачёт.

**Примеры:**
```
72 ч  →  2.0 ЗЕТ
54 ч  →  1.5 ЗЕТ
108 ч →  3.0 ЗЕТ
```

**Формула-подсказка** (не источник правды — учебный план утверждается отдельно):
```python
zet_hint = round(total_hours / 36, 1)
```

ЗЕТ задаёт администратор вручную. Автовычисление — только как подсказка в поле.

---

## 2. Когда ЗЕТ засчитываются студенту

**ТРИ состояния предмета** (вариант C, 26.08.2026 — купленный багом урок). До этого было
два («сдан»/«не сдан»), и предмет без экзамена засчитывал ВСЕ ЗЕТ по первой же
положительной оценке: «одна оценка, а пишет, будто весь семестр прошёл». Теперь между ними
есть «ожидается» — пока рубеж семестра по предмету не пройден, судить рано.

- **✅ passed** — предмет СДАН, ЗЕТ идут в зачёт (`earned`);
- **⏳ pending** — предмет ещё ИДЁТ, «ожидается»: ЗЕТ не засчитаны, но и не потеряны —
  показываются серым отдельно (`pending`), не входят в `earned`;
- **❌ failed** — предмет ЗАВЕРШЁН, но НЕ сдан.

**РУБЕЖ** (когда подводить итог по предмету), вычисляет `webdata.zet_summary_for_student`:
термин НЕ текущий (смотрят архив прошлого семестра), ИЛИ пройдены плановые часы предмета
(`study_hours.hours_done >= hours_total`). Экзамен — сам себе рубеж.

| Ситуация | Состояние |
|---|---|
| Экзамен сдан (оценка 5/4/3/Зачтено, в т.ч. по пересдаче) | ✅ passed |
| Экзамен провален (2/Не зачтено/Н) | ❌ failed |
| Экзамен в плане есть, оценки ещё нет | ⏳ pending (впереди, не провал) |
| Без экзамена, рубеж НЕ пройден (семестр идёт) | ⏳ pending |
| Без экзамена, рубеж пройден, средний >= 3.0 | ✅ passed |
| Без экзамена, рубеж пройден, средний < 3.0 | ❌ failed |
| ЗЕТ не задан администратором | — Не показывать нигде |

Расчёт — чистая `study_hours.subject_zet_state(...)` (един для студента/куратора/родителя,
десктоп берёт через локальный сервер). Обёртка `subject_zet_earned (УДАЛЕНА 31.08.2026, зови subject_zet_state)` (float|None) оставлена
для обратной совместимости. Держат `server/tests/test_zet.py` и
`tests/test_subject_hours_desktop.py` (обратный ход: одна «4» в идущем семестре → pending).

---

## 3. Модель данных

### 3.1 Расширить SubjectHours (уже в SYNC_MODELS)

```python
# server/app/models.py — SubjectHours
# Добавить одно поле — nullable, чтобы не сломать старые записи
class SubjectHours(Base):
    __tablename__ = 'subject_hours'
    id          = Column(String, primary_key=True)
    group_id    = Column(String, ForeignKey('groups.id'))
    subject     = Column(String)
    semester    = Column(Integer)
    total_hours = Column(Integer, default=0)   # уже есть
    zet         = Column(Float, nullable=True)  # НОВОЕ: None = не задан
    updated_at  = Column(DateTime, default=func.now(), onupdate=func.now())
```

### 3.2 Миграция БД

```sql
-- Безопасно: nullable, не ломает существующие строки
ALTER TABLE subject_hours ADD COLUMN zet REAL;
```

Выполнить: на сервере вручную, десктоп — автомиграция через `DBManager` при старте.

### 3.3 Новая таблица: ZetThreshold (порог перевода)

```python
# server/app/models.py
class ZetThreshold(Base):
    """Минимальный порог ЗЕТ для перевода на следующий курс."""
    __tablename__ = 'zet_thresholds'
    id         = Column(String, primary_key=True,
                        default=lambda: f'zth:{uuid4()}')
    group_id   = Column(String, ForeignKey('groups.id'))
    semester   = Column(Integer)   # 1, 2, 3, 4 …
    min_zet    = Column(Float)     # напр. 27.0 — минимум для перевода
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    updated_by = Column(String)    # user_id администратора

# ZetThreshold НЕ в SYNC_MODELS — серверная политика, не офлайн-данные.
# Десктоп читает через /web/admin/zet-thresholds.
```

---

## 4. Бизнес-логика — расширить study_hours.py

Расширить `study_hours.py`. Не дублировать расчёт оценок мимо `grading.py`.

```python
# study_hours.py

ZET_HOURS = 36  # 1 ЗЕТ = 36 ч, константа ФГОС

def zet_hint(total_hours: int) -> float:
    """Подсказка-автовычисление. НЕ источник правды."""
    return round(total_hours / ZET_HOURS, 1) if total_hours else 0.0


def is_subject_passed(grades: list[str]) -> bool:
    """
    Предмет сдан, если последний экзамен/зачёт — оценка >= 3 или 'Зачтено'.
    grades — список оценок по предмету в хронологическом порядке.
    """
    if not grades:
        return False
    last = grades[-1]
    if last in ('5', '4', '3'):
        return True
    if 'зачтено' in last.lower() and 'не' not in last.lower():
        return True
    return False


def zet_earned_for_subject(student_id, group_id, subject, semester, db) -> float | None:
    """
    Возвращает ЗЕТ если студент СДАЛ предмет, иначе None.
    None также если ЗЕТ не задан администратором.
    """
    sh = db.query(SubjectHours).filter_by(
        group_id=group_id, subject=subject, semester=semester
    ).first()
    if not sh or sh.zet is None:
        return None

    exam_grades = get_exam_grades(student_id, subject, semester, db)
    if is_subject_passed(exam_grades):
        return sh.zet

    # Предмет без экзамена — проверить средний балл практик
    avg = get_subject_avg(student_id, subject, semester, db)
    if avg is not None and avg >= 3.0:
        return sh.zet

    return None


def zet_summary(student_id, group_id, semester, db) -> dict:
    """
    Итог по студенту за семестр.
    Возвращает: earned, total, pct, subjects[]
    """
    all_sh = db.query(SubjectHours).filter_by(
        group_id=group_id, semester=semester
    ).all()

    subjects = []
    earned = 0.0
    total  = 0.0

    for sh in all_sh:
        if sh.zet is None:
            continue
        total += sh.zet
        e = zet_earned_for_subject(student_id, group_id, sh.subject, semester, db)
        passed = e is not None
        if passed:
            earned += e
        subjects.append({
            'subject': sh.subject,
            'zet':     sh.zet,
            'earned':  e or 0.0,
            'passed':  passed,
        })

    return {
        'earned':   round(earned, 1),
        'total':    round(total, 1),
        'pct':      round(earned / total * 100, 1) if total else 0.0,
        'subjects': subjects,
    }


def group_zet_report(group_id, semester, db) -> list[dict]:
    """
    Отчёт по группе для кнопки перевода на следующий курс.
    Сортировка: сначала не набравшие порог, внутри — по earned (меньше выше).
    """
    threshold = db.query(ZetThreshold).filter_by(
        group_id=group_id, semester=semester
    ).first()
    min_zet = threshold.min_zet if threshold else None

    students = get_group_students(group_id, db)
    result   = []

    for student in students:
        s = zet_summary(student.id, group_id, semester, db)
        eligible = (min_zet is None) or (s['earned'] >= min_zet)
        result.append({
            'student_id':   student.id,
            'display_name': student.display_name,
            'earned':       s['earned'],
            'total':        s['total'],
            'pct':          s['pct'],
            'eligible':     eligible,
            'missing_zet':  round((min_zet or 0) - s['earned'], 1)
                            if not eligible else 0.0,
            'unsatisfied':  [x['subject'] for x in s['subjects']
                             if not x['passed']],
        })

    result.sort(key=lambda x: (x['eligible'], x['earned']))
    return result
```

---

## 5. API — новые эндпоинты

```
# Студент — свой прогресс
GET  /web/student/zet?semester=3
→ { earned, total, pct, subjects: [{subject, zet, earned, passed}] }

# Куратор / администратор — отчёт по группе
GET  /web/admin/groups/{group_id}/zet-report?semester=3
→ [ {student_id, display_name, earned, total, pct,
     eligible, missing_zet, unsatisfied[]} ]

# Управление порогами перевода (только admin)
GET    /web/admin/zet-thresholds?group_id=&semester=
POST   /web/admin/zet-thresholds      {group_id, semester, min_zet}
PATCH  /web/admin/zet-thresholds/{id} {min_zet}
DELETE /web/admin/zet-thresholds/{id}

# PATCH учебных часов — уже есть, ДОБАВИТЬ поле zet
PATCH  /web/admin/subject-hours/{id}  {total_hours?, zet?}
# При total_hours без zet — вернуть zet_hint в ответе (подсказка, не сохраняем)

# Перевод на следующий курс (bulk)
POST   /web/admin/groups/{group_id}/promote
       {semester: int, student_ids: []}
# Проверяет eligible каждого, переводит только их.
# Пишет в audit_events. Шлёт системное сообщение в автоканал группы.
→ { promoted: [...], rejected: [{student_id, reason}] }
```

---

## 6. Вектор — новый intent

```python
# vector/intents.py — добавить

ZET_TRIGGERS = [
    'зет', 'зачётн', 'зачетн', 'кредит', 'трудоёмк',
    'трудоемк', 'нагрузк', 'сколько единиц', 'перевод на курс',
]

def handle_zet_balance(student_id, group_id, semester, db) -> dict:
    """
    Вопросы: «Сколько у меня ЗЕТ?», «Хватит ли для перевода?»,
    «Какие предметы ещё нужно сдать?»
    Возвращает факты — LLM только переформулирует (как всегда).
    """
    s = zet_summary(student_id, group_id, semester, db)
    threshold = db.query(ZetThreshold).filter_by(
        group_id=group_id, semester=semester
    ).first()
    return {
        'earned':      s['earned'],
        'total':       s['total'],
        'pct':         s['pct'],
        'min_zet':     threshold.min_zet if threshold else None,
        'eligible':    (s['earned'] >= threshold.min_zet)
                       if threshold else None,
        'unsatisfied': [x['subject'] for x in s['subjects']
                        if not x['passed'] and x['zet']],
    }
```

Добавить в `vector/knowledge.py`:
```python
ZET_DEFINITION = (
    "1 ЗЕТ (зачётная единица трудоёмкости) = 36 академических часов "
    "по ФГОС. Засчитывается студенту после сдачи дисциплины (оценка >= 3 "
    "или Зачтено). Используется для принятия решения о переводе на "
    "следующий курс."
)
```

---

## 7. UI — что добавить

### 7.1 Диалог учебных часов (админка) — десктоп + веб

В существующем диалоге `Группы → (часы)`:

```
Предмет           Часов   ЗЕТ      Подсказка
Числ. методы      [72]    [2.0]    ← 2.0 по формуле
Высш. математика  [108]   [3.0]    ← 3.0 по формуле
Физкультура       [72]    [   ]    ← пусто = не учитывается
```

- При изменении «Часов» → подставлять подсказку в «ЗЕТ» серым цветом
- Подсказка НЕ сохраняется пока пользователь не подтвердит
- ЗЕТ можно очистить → NULL → строка не показывается нигде

### 7.2 Порог перевода — карточка в настройках группы

```
Группа ИС-21 → Настройки → Порог ЗЕТ для перевода
  Семестр 3:  [27.0] ЗЕТ  (всего в семестре: 30.0 ЗЕТ)
  Семестр 4:  [27.0] ЗЕТ
  [Сохранить]
```

### 7.3 Дашборд студента

Показывать ТОЛЬКО если хотя бы один предмет имеет ЗЕТ:

```
Успеваемость:
  Средний балл:    3.9
  ЗЕТ за семестр: 12.0 / 30.0 (40%)
  ████░░░░░░░░    до перевода: 15.0 ЗЕТ

Предметы:
  Числ. методы    72 ч · 2.0 ЗЕТ  ✅ засчитаны
  Физика          72 ч · 2.0 ЗЕТ  ❌ не сдан
  Физкультура     72 ч · —        (ЗЕТ не задан)
```

Цвет прогресс-бара:
- 🟢 Зелёный: earned >= min_zet
- 🟡 Жёлтый: 80–99% от порога
- 🔴 Красный: < 80%

### 7.4 Отчёт куратора — таблица перевода (ГЛАВНАЯ ФИЧА)

Страница `/web/curator/zet-report` или вкладка в существующем отчёте:

```
Группа ИС-21 · Семестр 3 · Порог: 27.0 ЗЕТ

Статус      Студент          Набрано   Не хватает   Несданные предметы
─────────────────────────────────────────────────────────────────────
❌ Не готов  Иванов И.И.      18.0/30   9.0 ЗЕТ     Мат.анализ, Физика
❌ Не готов  Петрова М.С.     22.5/30   4.5 ЗЕТ     Физика
⚠️ Почти    Сидоров А.А.     25.5/30   1.5 ЗЕТ     Иностр. язык
✅ Готов     Козлова Т.В.     30.0/30   —           —
✅ Готов     Носков Р.П.      28.5/30   —           —

[Выбрать готовых]   [✅ Перевести выбранных на курс 2]
```

**Кнопка «Перевести на следующий курс»:**
- Активна только для студентов с `eligible = true`
- При нажатии — диалог подтверждения со списком имён
- Вызывает `POST /web/admin/groups/{id}/promote`
- После успеха → системное сообщение в автоканал «Объявления · ИС-21»:
  «Студенты [список] переведены на 2-й курс»
- Записывается в `audit_events`

### 7.5 Переиспользуемый компонент ZetProgress.vue (веб)

```vue
<!-- Используется в дашборде студента, отчёте куратора, кабинете родителя -->
<ZetProgress
  :earned="12.0"
  :total="30.0"
  :min-zet="27.0"
  :subjects="subjects"
  :show-details="true"
/>
```

### 7.6 Кабинет родителя

Та же строка ЗЕТ что у студента, только своего ребёнка.
Таблицу группы и порог перевода родителю НЕ показывать.

---

## 8. Тесты

```
tests/test_zet.py               ← клиентские (если десктоп считает локально)
server/tests/test_zet.py        ← серверные

Что покрыть обязательно:
  zet_earned: предмет сдан       → float
  zet_earned: не сдан            → None
  zet_earned: ЗЕТ не задан       → None
  zet_earned: пересдача сдана    → ЗЕТ засчитаны
  zet_earned: оценка 2           → None
  zet_summary: сумма и pct корректны
  group_zet_report: сортировка (не готовые — сверху)
  group_zet_report: eligible только при earned >= min_zet
  POST /promote: отклоняет not-eligible
  POST /promote: принимает eligible
  POST /promote: пишет в audit_events
  PATCH /subject-hours: сохраняет zet
  PATCH /subject-hours: возвращает zet_hint при total_hours без zet
  GET /student/zet: 403 для чужого студента
  GET /zet-report: 403 для студента и родителя
  GET /zet-report: 200 для куратора и администратора
```

---

## 9. Порядок реализации

```
Этап 1 — Данные (1 день):
  ALTER TABLE subject_hours ADD COLUMN zet REAL
  Создать ZetThreshold (create_all в models.py)
  study_hours.py: zet_hint, is_subject_passed, zet_earned_for_subject,
                  zet_summary, group_zet_report
  Тесты серверные

Этап 2 — API (0.5 дня):
  PATCH /subject-hours — добавить поле zet
  GET /student/zet
  GET /admin/groups/{id}/zet-report
  CRUD /admin/zet-thresholds
  POST /admin/groups/{id}/promote

Этап 3 — Вектор (0.5 дня):
  handle_zet_balance в intents.py
  ZET_DEFINITION в knowledge.py
  ZET_TRIGGERS

Этап 4 — UI веб (1 день):
  Поле ЗЕТ в форме учебных часов + подсказка
  Карточка порога в настройках группы
  Строка ЗЕТ в дашборде студента
  ZetProgress.vue
  Страница /curator/zet-report с кнопкой перевода

Этап 5 — UI десктоп (0.5 дня):
  Поле ЗЕТ в диалоге SubjectHours (ui/admin_dashboard.py)
  Строка ЗЕТ в ui/student_dashboard.py
  Страница отчёта куратора (десктоп получает бесплатно через web-view)
```

---

## 10. Что НЕ делать

- ❌ Не трогать `grading.py` — ЗЕТ не влияют на средний балл
- ❌ Не автосохранять ЗЕТ из формулы — только подсказка, человек решает
- ❌ Не переводить студентов без явного действия — только кнопка
- ❌ Не добавлять `ZetThreshold` в SYNC_MODELS — серверная политика
- ❌ Не показывать строку ЗЕТ если поле NULL — избегаем «0 из 0»
- ❌ Не засчитывать ЗЕТ по незакрытому предмету — только после сдачи

---

## 11. Добавить в CLAUDE.md после реализации

```markdown
**ЗЕТ (зачётные единицы)** — поле `SubjectHours.zet` (nullable Float,
в SYNC_MODELS). 1 ЗЕТ = 36 ч по ФГОС. Задаёт ТОЛЬКО администратор
(тот же диалог что и учебные часы). Не задан → не показывается нигде.
Порог перевода — `ZetThreshold` (НЕ в SYNC_MODELS). Расчёт —
`study_hours.py`: zet_earned_for_subject(), zet_summary(),
group_zet_report(). Вектор — intents.handle_zet_balance().
Перевод на курс — POST /web/admin/groups/{id}/promote (только eligible,
пишет в audit_events, шлёт системное сообщение в автоканал группы).
НЕ влияет на grading.py и средний балл.
```
