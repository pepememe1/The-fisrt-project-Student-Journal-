"""
test_schedule_categories.py — категории расписания (schedule/parser.py::CATEGORIES),
на СОХРАНЁННЫХ реальных страницах portal.esstu.ru (без сети).

Фикстуры (tests/fixtures/schedule_*):
  schedule_index_bakalavriat.htm / schedule_group_bakalavriat.htm — «Бакалавриат,
    специалитет», обычный (недельный) формат — читается СУЩЕСТВУЮЩИМ
    parse_group_page без единой правки (проверено живой разведкой).
  schedule_index_zo1.htm / schedule_group_zo1.htm — «Заочное 1», СЕССИОННЫЙ формат
    (заголовки дней — календарные даты, не Пн-Сб) — нужен parse_group_page_dated.

Числа в тестах на zo1 сверены вручную с сырым HTML (см. историю разведки):
группа «ЗУ-395-1», Пнд 06 апреля, 5-я пара — «Инженерная и компьютерная графика»
у «Прудова Л.Ю.», ауд. 732.
"""
import os

from schedule import parser as P
from schedule.model import Snapshot

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


# ── Реестр категорий ─────────────────────────────────────────────────────────

def test_categories_registry_has_four_entries_with_dated_flags():
    assert set(P.CATEGORIES) == {"college", "bakalavriat", "zo1", "zo2"}
    assert P.DEFAULT_CATEGORY == "college"
    assert P.CATEGORIES["college"]["dated"] is False
    assert P.CATEGORIES["bakalavriat"]["dated"] is False
    assert P.CATEGORIES["zo1"]["dated"] is True
    assert P.CATEGORIES["zo2"]["dated"] is True
    assert P.CATEGORIES["college"]["prefix"] == "К"
    assert P.CATEGORIES["bakalavriat"]["prefix"] is None


def test_old_constants_still_derive_from_registry():
    """COLLEGE_DIR/COLLEGE_INDEX/_COLLEGE_PREFIX — обратная совместимость для кода,
    который их ещё импортирует напрямую."""
    assert P.COLLEGE_DIR == P.CATEGORIES["college"]["dir"]
    assert P.COLLEGE_INDEX == P.category_index_url("college")
    assert P._COLLEGE_PREFIX == "К"


# ── list_category_groups / list_college_groups (совместимость) ──────────────

def test_list_category_groups_bakalavriat_takes_all_no_prefix_filter():
    html = _load("schedule_index_bakalavriat.htm")
    groups = P.list_category_groups(html, "bakalavriat")
    assert len(groups) > 100          # реально ~219 на живом снимке
    names = [n for n, _ in groups]
    assert "Б165" in names
    #никакой фильтрации по «К»/«Б» — это не колледж, там нет постороннего набора
    assert not any(n.startswith("К") for n in names)


# ── list_category_groups_with_course (3.5.5, курс = разведано вживую) ───────

def test_course_parsed_from_real_bakalavriat_index():
    """Реальный снимок portal.esstu.ru/bakalavriat: заголовок «1 курс».."6 курс»,
    группа сидит в столбце СВОЕГО курса (Б165 — курс 1, у неё есть занятия;
    подтверждено разведкой 3.5.5)."""
    html = _load("schedule_index_bakalavriat.htm")
    triples = P.list_category_groups_with_course(html, "bakalavriat")
    assert len(triples) > 100
    by_name = {name: course for name, _href, course in triples}
    assert by_name["Б165"] == 1
    assert by_name["Б164"] == 2
    assert by_name["Б173"] == 3
    assert by_name["Б112"] == 4
    #курс не фиксирован на 4 — на бакалавриате реально есть 5-й курс.
    assert 5 in by_name.values()


def test_course_parsing_matches_column_count_regardless_of_header_width():
    """Заголовок «N курс» может иметь любое число столбцов (разведано: 6 у всех
    четырёх категорий на живых данных) — курс всегда берётся из ПОЗИЦИИ ссылки,
    сверенной с текстом заголовка ТОЙ ЖЕ колонки, а не из фиксированного числа."""
    html = ('<table>'
            '<tr><td> 1 курс </td><td> 2 курс </td><td> 3 курс </td></tr>'
            '<tr><td><a href="1.htm">Г1</a></td>'
            '<td><a href="2.htm">Г2</a></td>'
            '<td><a href="3.htm">Г3</a></td></tr>'
            '</table>')
    triples = P.list_category_groups_with_course(html, "bakalavriat")
    by_name = {name: course for name, _href, course in triples}
    assert by_name == {"Г1": 1, "Г2": 2, "Г3": 3}


def test_course_falls_back_to_column_position_without_header():
    """Заголовок не нашёлся (сайт неожиданно поменялся) — не падаем, откатываемся
    на «номер колонки + 1», лучше приблизительный курс, чем исключение."""
    html = ('<table>'
            '<tr><td><a href="1.htm">Г1</a></td>'
            '<td><a href="2.htm">Г2</a></td></tr>'
            '</table>')
    triples = P.list_category_groups_with_course(html, "bakalavriat")
    by_name = {name: course for name, _href, course in triples}
    assert by_name == {"Г1": 1, "Г2": 2}


def test_empty_cells_are_skipped_not_counted_as_groups():
    """Пустые ячейки (курс есть, группы в нём нет — реальная картина на живых
    данных) не должны попадать в список ни именем, ни местом в счётчике."""
    html = ('<table>'
            '<tr><td> 1 курс </td><td> 2 курс </td></tr>'
            '<tr><td><a href="1.htm">Г1</a></td><td><a href="2.htm"></a></td></tr>'
            '</table>')
    triples = P.list_category_groups_with_course(html, "bakalavriat")
    assert [name for name, _href, _course in triples] == ["Г1"]


def test_list_category_groups_drops_course_for_old_callers():
    """list_category_groups (без курса) остаётся тонкой обёрткой — существующие
    потребители (schedule_web.py и др.) не видят разницы.

    ⚠️ ЗДЕСЬ БЫЛА ТАВТОЛОГИЯ (найдена 05.09.2026 при разборе слепых тестов). Тест
    писал у себя ВЫРАЖЕНИЕ, дословно совпадающее с телом продукта:

        new = [(name, href) for name, href, _course in list_category_groups_with_course(...)]
        assert old == new

    а `list_category_groups` ровно этим выражением и является. То есть сверялась копия
    с копией: любая ошибка внутри `list_category_groups_with_course` появлялась
    ОДИНАКОВО с обеих сторон и была невидима. Это первая из трёх записанных форм
    «сторожа, который не может покраснеть» (CLAUDE.md, 24.08.2026), и лечится она
    ровно так — проверкой СВОЙСТВА, а не равенства двух путей к одному источнику."""
    html = _load("schedule_index_bakalavriat.htm")
    old = P.list_category_groups(html, "bakalavriat")
    triples = P.list_category_groups_with_course(html, "bakalavriat")

    #1. Обёртка ничего не теряет и не добавляет.
    assert len(old) == len(triples) > 0, "на живом снимке индекса групп быть должно"
    #2. Форма именно пара, а не тройка: старый потребитель распаковывает в две
    #   переменные, и лишний элемент уронил бы его «too many values to unpack».
    assert all(isinstance(x, tuple) and len(x) == 2 for x in old), old[:3]
    #3. Значения настоящие: имя непустое, href ведёт на страницу группы.
    for name, href in old:
        assert name.strip(), "имя группы пустое"
        assert href.strip().endswith(".htm"), href
    #4. И только теперь — что курс отброшен, а первые два поля сохранены как есть.
    assert old == [(n, h) for n, h, _c in triples]


def test_list_college_groups_unchanged_behavior():
    """Старая функция — тонкая обёртка над list_category_groups(html, "college"),
    поведение (фильтр по «К», магистратура «М…» отброшена) не изменилось."""
    html = ('<table><tr>'
            '<td><a href="1.htm">К15/1</a></td>'
            '<td><a href="69.htm">М215</a></td>'
            '<td><a href="2.htm">К25</a></td>'
            '</tr></table>')
    names = [n for n, _ in P.list_college_groups(html)]
    assert names == [n for n, _ in P.list_category_groups(html, "college")]
    assert "К15/1" in names and "К25" in names
    assert "М215" not in names


# ── parse_group_page на bakalavriat (обычный формат, БЕЗ ПРАВОК) ─────────────

def test_bakalavriat_group_page_parses_with_unmodified_parser():
    gs = P.parse_group_page(_load("schedule_group_bakalavriat.htm"), name="Б165")
    assert 1 in gs.weeks and 2 in gs.weeks
    assert any(l.teacher for w in gs.weeks.values() for ls in w.values() for l in ls), \
        "хотя бы один преподаватель должен распознаться (формат ФАМИЛИЯ капсом, как в колледже)"


# ── parse_group_page_dated на zo1 (сессионный формат) ────────────────────────

def test_dated_parser_finds_multiple_session_blocks():
    gs = P.parse_group_page_dated(_load("schedule_group_zo1.htm"), name="ЗУ-395-1")
    assert len(gs.weeks) >= 2          # реально 4 на живом снимке
    assert len(gs.pair_times) <= 8     # до 8 пар (не 6, как в обычном формате)


def test_dated_parser_day_keys_are_dates_not_weekdays():
    gs = P.parse_group_page_dated(_load("schedule_group_zo1.htm"), name="ЗУ-395-1")
    days = gs.weeks[1].keys()
    assert "Пнд, 06 апреля" in days
    assert "Пнд" not in days            # НЕ короткий день, как в обычном формате


def test_dated_parser_known_lesson_manually_verified():
    """Пнд 06 апреля, 5-я пара — сверено вручную с сырым HTML при разведке."""
    gs = P.parse_group_page_dated(_load("schedule_group_zo1.htm"), name="ЗУ-395-1")
    mon = gs.weeks[1]["Пнд, 06 апреля"]
    p5 = next(l for l in mon if l.pair_no == 5)
    assert p5.subject == "Инженерная и компьютерная графика"
    assert p5.teacher == "Прудова Л.Ю."     # НЕ капсом — регрессия на _TEACHER_RE_DATED
    assert p5.room == "732"


def test_dated_parser_ignores_double_underscore_artifact():
    """Слипшиеся ячейки на сессионных страницах иногда дают «_ _» вместо «_» —
    тоже пустая клетка, не текст занятия (regression на parse_cell)."""
    assert P.parse_cell("_ _") is None
    assert P.parse_cell("_  _") is None


def test_dated_parser_does_not_leak_into_regular_parse_cell():
    """Обычный parse_cell (дефолт _TEACHER_RE, капсом) НЕ должен матчить фамилию
    в обычном регистре — иначе была бы регрессия для колледжа/бакалавриата."""
    ls = P.parse_cell("лек.Инженерная и компьютерная графика Прудова Л.Ю. а.732")
    assert ls is not None
    assert ls.teacher == ""             # не капсом — дефолтный _TEACHER_RE не находит
    assert "Прудова" in ls.subject      # осталось в названии предмета как есть


# ── build_snapshot(category=...) — офлайн, категория параметризована ────────

def test_build_snapshot_dated_category_offline():
    index_html = _load("schedule_index_zo1.htm")
    group_html = _load("schedule_group_zo1.htm")

    def fake_fetch(url, timeout=20):
        return index_html if url.endswith("raspisan.htm") else group_html

    snap = P.build_snapshot(category="zo1", fetch=fake_fetch)
    assert "ЗУ-395-1" in snap.groups
    gs = snap.groups["ЗУ-395-1"]
    assert len(gs.weeks) >= 2

    #round-trip сериализации не теряет структуру (даты как day-ключи — тоже строки)
    restored = Snapshot.from_dict(snap.to_dict())
    assert restored.groups["ЗУ-395-1"].weeks.keys() == gs.weeks.keys()


def test_build_snapshot_default_still_means_college():
    """build_snapshot() без аргументов — 100% прежнее поведение (регрессия)."""
    index_html = '<table><tr><td><a href="1.htm">К15/1</a></td><td><a href="69.htm">М215</a></td></tr></table>'
    group_html = _load("group_K15_1.htm")   # существующая фикстура (test_schedule_parser.py)

    def fake_fetch(url, timeout=20):
        return index_html if url.endswith("raspisan.htm") else group_html

    snap = P.build_snapshot(fetch=fake_fetch)
    assert "К15/1" in snap.groups
    assert "М215" not in snap.groups
