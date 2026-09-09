# -*- coding: utf-8 -*-
"""
test_agents_are_loadable.py — 🔒 СЕТЬ ГРАНЕЙ ОБЯЗАНА БЫТЬ ВЫЗЫВАЕМОЙ.

━━ ЗАЧЕМ (03.09.2026) ━━
Жалоба Ярослава: «преддеплойный и перед сборкой APK агент ты вообще не запускал уже
100 лет». Причина оказалась не в дисциплине: из ТРИНАДЦАТИ определений в `.claude/agents/`
Claude Code загружал РОВНО ОДНО. У остальных двенадцати описание начиналось с
`[ГРАНЬ/РОЛЬ]`, YAML читал открывающую скобку как начало списка, спотыкался о текст
после `]` и молча отбрасывал весь frontmatter — то есть агента целиком. Вызвать
`gb-deploy-check` было физически нечем с 13.08.2026, три недели.

Единственным работавшим оказался `gb-adversary` — у него описание случайно стояло в
кавычках.

━━ ПОЧЕМУ ЭТОГО НЕ ЗАМЕТИЛ ВАЛИДАТОР ━━
`.claude/validate-agents.py` существовал ровно для этого и отвечал «все определения
валидны». Он разбирал frontmatter ВРУЧНУЮ, через `split(":", 1)`, и для него строка
выглядела безупречно: ключ слева, значение справа. Проверка велась не тем способом,
каким читает потребитель, — и потому не проверяла ничего, только создавала уверенность.
Это ровно наш класс «зелёный тест рядом с дефектом», и он же — «обещание без
вызывающего»: сеть граней описана в CLAUDE.md §0.0 как обязательная, ROSTER.md её
перечисляет, а позвать нельзя ни одну.

━━ ПОЧЕМУ ТЕСТ, А НЕ «НЕ ЗАБЫВАТЬ ЗАПУСКАТЬ ВАЛИДАТОР» ━━
Валидатор и так был написан. Его не гоняли — иначе поймали бы в тот же день. Проверка,
которую надо ПОМНИТЬ запустить, отличается от отсутствующей только тем, что даёт ложное
спокойствие. Здесь она в общем прогоне и краснеет сама.
"""
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO / ".claude" / "agents"
VALIDATOR = REPO / ".claude" / "validate-agents.py"

# Не определения агентов, а документы для людей.
_NOT_AGENTS = {"ROSTER.md", "README.md"}

# ⚠️ 🔥 ПОЧЕМУ ЗДЕСЬ НЕ `importorskip` — НАЙДЕНО ПОЛКОВНИКОМ В ДЕНЬ НАПИСАНИЯ (04.09.2026).
#
# Сначала тут стоял `pytest.importorskip("yaml")` на уровне модуля. Он гасил ВЕСЬ файл,
# включая обе страховки от пустого каталога, а PyYAML не объявлен ни в одном
# requirements — корневой CI ставит `requirements.txt` и гоняет `pytest -q`. Итог:
# в CI и на машине Влада выполнялось **0 проверок из 42**, и прогон был ЗЕЛЁНЫМ.
#
# То есть сторож, заведённый против «зелёного теста рядом с дефектом», сам был ровно
# таким тестом — работал только на машине автора. Защита была написана и выключалась
# раньше, чем срабатывала.
#
# ПРАВИЛО: пропускать проверку можно по ОТСУТСТВИЮ ПРЕДМЕТА (граней тут нет — нечего
# проверять), но НЕ по отсутствию ИНСТРУМЕНТА (грани есть, а проверить нечем — это
# отказ, и он обязан быть громким).
#
# Каталог `.claude/` лежит вне git намеренно, поэтому в CI его нет физически — там скип
# честен. А если каталог есть, но PyYAML не поставлен, тест ПАДАЕТ и говорит, что делать.
pytestmark = pytest.mark.skipif(
    not AGENTS_DIR.is_dir(),
    reason="каталога .claude/agents/ нет (он вне git) — проверять нечего")

try:
    import yaml
except ImportError:                                      # pragma: no cover
    yaml = None


def _agent_files() -> list:
    if not AGENTS_DIR.is_dir():
        return []
    return sorted(p for p in AGENTS_DIR.glob("*.md") if p.name not in _NOT_AGENTS)


def _frontmatter(path: pathlib.Path) -> str:
    """Сырой текст frontmatter между первой парой `---`."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{path.name}: файл не начинается с frontmatter"
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"{path.name}: frontmatter не закрыт второй строкой ---"
    return parts[1]


def test_the_yaml_parser_is_available():
    """Разборщик установлен. Без него проверить грани НЕЧЕМ, и молчать об этом нельзя.

    Ручной разбор строк (`split(":", 1)`) здесь не годится принципиально: именно им
    пользовался `.claude/validate-agents.py`, и именно поэтому он три недели отвечал
    «все определения валидны» при двенадцати незагружаемых агентах. Проверять надо ТЕМ
    ЖЕ разбором, каким читает потребитель."""
    assert yaml is not None, (
        "грани в .claude/agents/ есть, а PyYAML не установлен — проверить их нечем. "
        "Поставьте: pip install pyyaml (объявлен в requirements-dev.txt). "
        "Молча пропустить нельзя: без разбора битый агент выглядит целым, а Claude Code "
        "не загрузит его и не скажет об этом.")


def test_there_is_at_least_one_agent():
    """Пустой каталог — тоже отказ: §0.0 CLAUDE.md требует звать грани самому, и молча
    исчезнувший каталог выглядел бы как «звать некого»."""
    assert _agent_files(), (
        "в .claude/agents/ нет ни одного определения — сеть граней исчезла")


@pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
def test_frontmatter_parses_as_real_yaml(path):
    """🔥 ГЛАВНАЯ ПРОВЕРКА. Разбираем ТЕМ ЖЕ способом, каким читает Claude Code.

    Обратный ход проверен 03.09.2026: возвращаю описанию форму `description: [ГРАНЬ]`
    без кавычек — тест краснеет с той же диагностикой, что была у настоящего дефекта."""
    raw = _frontmatter(path)
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        pytest.fail(
            "%s: frontmatter не разбирается как YAML (%s).\n"
            "Claude Code молча НЕ ЗАГРУЗИТ этого агента — позвать его будет нечем, и "
            "узнаете вы об этом в тот момент, когда он понадобится.\n"
            "Частая причина: значение начинается с '[' — оберните его в двойные кавычки."
            % (path.name, str(exc).splitlines()[0]))
    assert isinstance(parsed, dict), (
        "%s: frontmatter разобрался в %s, а нужен словарь полей"
        % (path.name, type(parsed).__name__))


@pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
def test_required_fields_are_present(path):
    """Без `name` агента не позвать, без `description` его не выберут: описание — это
    единственное, по чему решают, подходит ли грань под задачу."""
    fm = yaml.safe_load(_frontmatter(path))
    for field in ("name", "description"):
        assert fm.get(field), "%s: нет обязательного поля '%s'" % (path.name, field)
    assert isinstance(fm["description"], str), (
        "%s: description разобрался как %s, а не строкой — почти всегда это значит, что "
        "он начинается со скобки и YAML прочитал его списком"
        % (path.name, type(fm["description"]).__name__))
    name = fm["name"]
    assert name == path.stem, (
        "%s: поле name='%s' не совпадает с именем файла. Звать агента будут по имени "
        "файла, и расхождение читается как «агента нет»" % (path.name, name))


@pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
def test_model_is_declared_and_cheap_by_default(path):
    """У каждой грани явно назван размер модели.

    ⚠️ Это про деньги, а не про аккуратность. Субагент стартует с ЧИСТЫМ контекстом, в
    который целиком грузится CLAUDE.md — а он у нас на 120 000 токенов. Три грани на
    opus, запущенные разом, стоят больше трети миллиона токенов ДО первой полезной
    строки; именно так 01–02.09.2026 три запущенные грани разом вернули 429.
    Без явного `model` грань наследует модель родителя, то есть самую дорогую."""
    fm = yaml.safe_load(_frontmatter(path))
    model = fm.get("model")
    assert model, (
        "%s: не задан model — грань унаследует модель родителя (самую дорогую), и цена "
        "параллельного запуска вырастет молча" % path.name)
    assert model in {"haiku", "sonnet", "opus", "fable", "inherit"}, (
        "%s: неизвестная модель '%s'" % (path.name, model))


def test_expensive_agents_stay_a_minority():
    """На opus держим только те грани, где цена оправдана СУТЬЮ работы: адверсариальная
    проверка утверждений и ревью. Остальное — разведка и механические сверки, они на
    haiku работают не хуже.

    Проверяется СВОЙСТВО (доля), а не поимённый список: список краснел бы на каждом
    законном добавлении и подталкивал «просто обновить ожидание» — грабля, уже описанная
    в CLAUDE.md."""
    models = {}
    for path in _agent_files():
        fm = yaml.safe_load(_frontmatter(path))
        models[path.stem] = fm.get("model")
    expensive = sorted(n for n, m in models.items() if m == "opus")
    total = len(models)
    assert len(expensive) * 3 <= total, (
        "на opus сидят %d граней из %d (%s). Субагент грузит CLAUDE.md целиком, поэтому "
        "цена параллельного запуска складывается из самых дорогих: держите opus на "
        "проверяющих гранях, разведку и сверки переводите на haiku."
        % (len(expensive), total, expensive))


def test_the_validator_itself_is_green():
    """Валидатор `.claude/validate-agents.py` проверяет больше, чем этот файл (неизвестные
    поля, дубли имён, инструменты, пропадающие в фоновом режиме). Он был написан давно и
    всё это время отвечал «все определения валидны» при двенадцати незагружаемых агентах —
    потому что его никто не запускал. Теперь запускает прогон."""
    if not VALIDATOR.exists():
        pytest.skip("validate-agents.py отсутствует")
    res = subprocess.run([sys.executable, "-X", "utf8", str(VALIDATOR)],
                         cwd=str(REPO), capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    assert res.returncode == 0, (
        "validate-agents.py вернул ошибку:\n%s\n%s"
        % (res.stdout or "", res.stderr or ""))


@pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
def test_effort_is_declared_explicitly(path):
    """У каждой грани явно назван уровень усилия.

    ⚠️ Это второй рычаг цены после `model`, и он работает ровно так же: **не заданный
    `effort` наследуется от СЕССИИ**, то есть от самого дорогого уровня, на котором
    сейчас работает Прайм. Пять граней из тринадцати (`gb-android`, `gb-fix`, `gb-i18n`,
    `gb-parity`, `gb-tests`) жили без него до 05.09.2026 — то есть механическая работа
    вроде «запусти прогон и перескажи вердикт» оплачивалась по верхнему тарифу, и
    заметить это было нечем: в отчёте грани уровень усилия не виден.

    Значение проверяется по списку самого валидатора, чтобы два места не разошлись."""
    fm = yaml.safe_load(_frontmatter(path))
    effort = fm.get("effort")
    assert effort, (
        "%s: не задан effort — грань унаследует уровень усилия сессии, то есть самый "
        "дорогой. Проставь явно: low для механической работы, medium для разбора, "
        "high только там, где нужен адверсариальный анализ." % path.name)
    allowed = _validator_constant("EFFORT")
    assert effort in allowed, (
        "%s: effort='%s' вне допустимых %s" % (path.name, effort, sorted(allowed)))


def _validator_constant(name: str) -> set:
    """Достаёт множество-константу из `.claude/validate-agents.py`.

    ⚠️ Берём из ЧУЖОГО файла, а не повторяем список у себя: тест, повторяющий формулу,
    сверяет копию с копией и переживает любое расхождение (записанный урок 24.08.2026)."""
    import ast
    src = VALIDATOR.read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return set(ast.literal_eval(node.value))
    raise AssertionError("в validate-agents.py нет константы %s — разбор устарел" % name)


def test_the_validator_constants_are_readable():
    """Обратный ход к разбору чужого файла: сломайся он — проверка выше стала бы
    зелёной по неверной причине, а не покраснела."""
    effort = _validator_constant("EFFORT")
    models = _validator_constant("MODELS")
    assert "low" in effort and "max" in effort, effort
    assert "haiku" in models and "opus" in models, models


def test_cheap_work_is_not_paid_at_the_top_rate():
    """СВОЙСТВО, а не поимённый список: большинство граней обязано быть дешёвым.

    Грань стартует холодной и целиком оплачивает вход (CLAUDE.md ~108 000 токенов).
    Если и модель, и усилие у большинства верхние — сеть граней перестаёт экономить и
    начинает стоить дороже, чем сделать работу самому. Именно это и произошло
    01–02.09.2026, когда три грани разом вернули 429."""
    top = []
    for path in _agent_files():
        fm = yaml.safe_load(_frontmatter(path))
        if fm.get("effort") in {"high", "xhigh", "max"}:
            top.append(path.stem)
    total = len(_agent_files())
    assert len(top) * 2 <= total, (
        "на верхнем усилии %d граней из %d (%s) — сеть перестала быть дешевле, чем "
        "сделать работу самому" % (len(top), total, sorted(top)))
