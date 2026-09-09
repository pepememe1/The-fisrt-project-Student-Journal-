# -*- coding: utf-8 -*-
"""
test_no_calendar_bound_tests.py — 🕒 ТЕСТ НЕ ИМЕЕТ ПРАВА ЗНАТЬ, КАКОЙ СЕЙЧАС УЧЕБНЫЙ ГОД.

━━ ЧЕМ КУПЛЕН ━━
В ночь на 1 сентября 2026 `db.default_term` переключился на «2026/2027», и
`test_overview_does_not_blend_a_recurring_subject_across_past_courses` покраснел БЕЗ
ЕДИНОЙ ПРАВКИ КОДА: он писал «сегодняшний» термин числом («2025/2026, семестр 2»).
Занятие перестало быть текущим, средний стал 0.0 вместо 5.0, и полчаса ушло на поиск
несуществующего дефекта в продукте.

Это наш давний класс — «сторож со снимком значения». Опасен он не тем, что краснеет, а
тем, КОГДА краснеет: ровно в первый учебный день, когда все заняты, и вперемешку с
настоящими падениями. А молчаливая половина той же болезни хуже: тест, написанный «на
будущий год», после наступления этого года начинает проверять совсем другой сценарий и
остаётся зелёным.

━━ ЧТО ПРОВЕРЯЕТСЯ ━━
Что ни один тестовый файл не содержит литерал ТЕКУЩЕГО учебного года — кроме тех, где это
осмысленно и объяснено ниже поимённо. Сторож сработает в тот день, когда кто-то снова
впишет «сегодняшний» год числом, а не возьмёт его из продукта (`db.default_term()` на
сервере, `data.terms.current_term()` на клиенте).

⚠️ Список исключений намеренно КРОШЕЧНЫЙ и с причинами. Растёт он — значит правило
перестают соблюдать, и это повод для разговора, а не для дописывания строки.
"""
import io
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#Файлы, где литерал текущего года ЗАКОНЕН, и почему именно.
ALLOWED = {
    #Проверяет САМУ функцию календаря по фиксированным датам: «сентябрь 2026 — это уже
    #2026/2027». Здесь литерал и есть предмет проверки, брать его из продукта значило бы
    #сверять продукт сам с собой.
    "tests/test_term_calendar.py",
    #Термин передаётся ЯВНО и в занятие, и в запрос: тест самодостаточен и не зависит от
    #того, какой год сегодня — совпадение с текущим случайно.
    "server/tests/test_curator.py",
    #Значение конфига здесь — ДАННЫЕ (проверяется, что синк не затирает config), сам год
    #на исход не влияет.
    "server/tests/test_sync_config_clobber.py",
    #Та же причина, что у test_term_calendar: проверяются ГРАНИЦЫ самой формулы по
    #ЯВНЫМ ФИКСИРОВАННЫМ датам — `term_for_date(datetime(2026, 9, 1))`. Такая проверка
    #не протухает никогда: 1 сентября 2026 останется началом 2026/2027 и через десять
    #лет. Совпадение литерала с сегодняшним годом здесь случайно и временно.
    "server/tests/test_course_rollover.py",
}

#Учебный год: «YYYY/YYYY+1».
YEAR_RE = re.compile(r"\b(20\d{2})/(20\d{2})\b")


def _code_only(line: str) -> str:
    """Строка без комментария. Год, названный в ПОЯСНЕНИИ («переключился на 2026/2027»),
    ничего не привязывает — наоборот, объясняет прошлую привязку. Считать его нарушением
    значило бы запретить объяснять собственные уроки, а этого мы не делаем."""
    cut = line.find("#")
    return line if cut < 0 else line[:cut]


def _prose_lines(src: str) -> set:
    """Номера строк, занятых ДОКСТРИНГАМИ (пояснениями), а не кодом.

    🔥 09.09.2026: сторож краснел на СОБСТВЕННОМ уроке. `_code_only` ниже срезает
    комментарий `#` — и правильно, его докстринг прямо это объясняет: «Год, названный в
    ПОЯСНЕНИИ, ничего не привязывает». Но докстринг комментарием не является, и
    `test_course_rollover.py` покраснел за фразу «со штампом 2026/2027·1» в описании
    боевого дефекта. То есть правило запрещало ровно то, что его же докстринг обещал
    разрешить, — и подталкивало не объяснять свои находки либо вписать целый файл в
    список исключений.

    ⚠️ Срезается ТОЛЬКО докстринг, а не всякая строка в кавычках. Это принципиально:
    самый частый вид самого дефекта — год ЗНАЧЕНИЕМ внутри проверки
    (`assert term == "2026/2027"`), и срезав все строковые литералы, сторож ослеп бы
    именно на нём. Признак докстринга — строковый литерал, СТОЯЩИЙ ОТДЕЛЬНЫМ
    ВЫРАЖЕНИЕМ: такая строка ничего не делает, она и есть текст для человека.
    """
    import tokenize
    out = set()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except Exception:
        return out                       #не разобрали — считаем, что пояснений нет
    #🔥 ВНУТРИ СКОБОК `NL` — НЕ НАЧАЛО ОПЕРАТОРА (нашёл Полковник в день написания).
    #Первая версия внесла `tokenize.NL` в начала без оговорки — и сторож ослеп на
    #МНОГОСТРОЧНОЙ проверке: когда список сравнения перенесён на следующую строку,
    #перенос ВНУТРИ скобки даёт токен `NL`, литерал оказывается «первым на строке» и
    #объявляется пояснением. То есть правило переставало ловить ровно тот дефект,
    #который его докстринг обещает ловить, стоило записать проверку в две строки — а
    #многострочный `assert` со снимком ответа у нас обычная форма. Живой регрессии не
    #было, но слепота копилась бы молча, и поймал её обратный ход, а не чтение.
    #Поэтому считаем глубину скобок: `NL` начинает оператор только на нулевой глубине.
    starters = {tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.ENCODING}
    prev = tokenize.NEWLINE
    depth = 0
    for tok in toks:
        starts = prev in starters or (prev == tokenize.NL and depth == 0)
        if tok.type == tokenize.STRING and starts:
            out.update(range(tok.start[0], tok.end[0] + 1))
        if tok.type == tokenize.OP:
            if tok.string in "([{":
                depth += 1
            elif tok.string in ")]}":
                depth = max(0, depth - 1)
        if tok.type != tokenize.COMMENT:
            prev = tok.type
    return out


def _current_academic_year() -> str:
    """Текущий учебный год ИЗ ПРОДУКТА — тем же кодом, что решает это на бою."""
    import sys
    server = os.path.join(ROOT, "server")
    if server not in sys.path:
        sys.path.insert(0, server)
    from app.db import default_term
    return default_term()[0]


def _test_files():
    for base in ("tests", os.path.join("server", "tests")):
        folder = os.path.join(ROOT, base)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            rel = f"{base}/{name}".replace(os.sep, "/")
            yield rel, os.path.join(folder, name)


def test_no_test_hardcodes_the_current_academic_year():
    """🔥 СВОЙСТВО. Обратный ход: вписать текущий год в любой тест вне списка —
    краснеет с именем файла и строкой."""
    current = _current_academic_year()
    offenders = []
    for rel, path in _test_files():
        if rel in ALLOWED or rel.endswith("test_no_calendar_bound_tests.py"):
            continue
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        prose = _prose_lines(src)
        for n, line in enumerate(src.splitlines(), 1):
            if n not in prose and current in _code_only(line):
                offenders.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not offenders, (
        f"Тест привязан к ТЕКУЩЕМУ учебному году ({current}) — он протухнет 1 сентября "
        "и покраснеет в самый неудобный день, либо, что хуже, начнёт молча проверять "
        "другой сценарий. Термин берут из продукта: `db.default_term()` на сервере, "
        "`data.terms.current_term()` на клиенте.\n" + "\n".join(offenders))


def test_the_allow_list_has_no_dead_entries():
    """Исключение, переставшее быть нужным, — это разрешение на будущее нарушение.

    Файл переименовали или литерал из него убрали, а строка в списке осталась — и
    следующая привязка в этом файле пройдёт молча."""
    current = _current_academic_year()
    dead = []
    for rel in sorted(ALLOWED):
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(path):
            dead.append(f"{rel} — файла нет")
            continue
        with open(path, encoding="utf-8") as fh:
            if not any(current in _code_only(ln) for ln in fh):
                dead.append(f"{rel} — литерала {current} в нём больше нет")
    assert not dead, (
        "В списке исключений мёртвые записи; убрать их, иначе они разрешают будущие "
        "нарушения в этих файлах:\n" + "\n".join(dead))


@pytest.mark.parametrize("rel", sorted(ALLOWED))
def test_every_exception_is_still_a_test_file(rel):
    """Мелочь, но своя: список должен указывать на тесты, а не на что попало."""
    assert rel.endswith(".py") and "/test_" in rel, rel


def test_the_rule_still_catches_a_year_written_across_several_lines():
    """🔒 ОБРАТНЫЙ ХОД к `_prose_lines`, и он не формальность.

    Первая версия функции срезала литерал, стоящий первым на ПРОДОЛЖЕНИИ строки внутри
    скобок, — то есть переставала видеть год в многострочной проверке. Сторож при этом
    оставался зелёным, потому что живого нарушения такой формы в репозитории не было:
    слепота копилась бы молча и вылезла бы на первом же снимке ответа в скобках.

    Здесь проверяются ОБА исхода на одном куске кода: пояснение прощается, значение —
    нет, в том числе записанное в несколько строк.
    """
    src = (
        'def test_x():\n' 
        '    """Пояснение про 2026/2027 — объясняет прошлый урок."""\n' 
        '    assert term == "2026/2027"\n' 
        '    assert terms == [\n' 
        '        "2026/2027",\n' 
        '    ]\n'
    )
    prose = _prose_lines(src)
    caught = [n for n, line in enumerate(src.splitlines(), 1)
              if n not in prose and "2026/2027" in _code_only(line)]
    assert 2 not in caught, "докстринг снова считается привязкой — правило само себе врёт"
    assert caught == [3, 5], (
        "год ЗНАЧЕНИЕМ обязан ловиться в ЛЮБОЙ записи, включая многострочную; "
        f"поймано на строках {caught}")
