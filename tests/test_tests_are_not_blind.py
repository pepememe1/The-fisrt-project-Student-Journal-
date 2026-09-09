"""
Сторож НАД СТОРОЖАМИ: ищет тесты, которые не могут покраснеть.

Инвариант проекта — «сторож, не проверенный откатом, скорее всего не работает» — куплен
дорого и повторялся много раз. Здесь закрыты те его формы, которые видны МАШИНЕ; формы,
видимые только чтением (тест повторяет формулу у себя, допуск шире поломки, тест не знает
про измерение), этой проверкой НЕ ловятся, и притворяться иначе нельзя.

Что проверяется:

1. **Голое сравнение вместо `assert`.** `x == 7.7` отдельной строкой вычисляется,
   результат выбрасывается, проверка не проверяет ничего. Ловилось у нас 24.08.2026.

2. **`assert True` и родня.** Тавтология зелена всегда.

3. **Тест без единой проверки.** Есть законная форма «функция не бросает — и всё», и она
   действительно проверка: исключение красит тест. Поэтому не запрет, а СПИСОК с
   причиной у каждого пункта — исключение без записанной причины это не исключение, а
   забытый случай (урок 02.09.2026, префикс «public/» прикрывал настоящую дыру).

4. 🔥 **Пропуск целого файла по отсутствию ОБЪЯВЛЕННОГО пакета.** Это один и тот же
   дефект дважды: `importorskip("yaml")` гасил 42 проверки граней из 42 в CI
   (04.09.2026, нашёл Полковник) и `importorskip("pdfplumber")` гасил 17 проверок
   разбора учебных планов (05.09.2026). Оба раза прогон оставался ЗЕЛЁНЫМ.
   ⚠️ Правило: пропускать можно по отсутствию ПРЕДМЕТА проверки, но не по отсутствию
   ИНСТРУМЕНТА, который мы сами объявили зависимостью. Нет объявленного пакета — это
   поломка окружения, и она обязана быть громкой.
"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIRS = ("tests", os.path.join("server", "tests"))

#Вызовы, которые сами по себе являются проверкой.
_CHECK_CALLS = {"fail", "raises", "warns", "approx", "xfail", "exit", "skip"}

#Тесты, у которых проверка — САМ ФАКТ, что вызов не бросил исключение. У каждого причина.
_NO_ASSERT_ALLOWED = {
    "server/tests/test_audit_silence.py::test_audit_failure_never_breaks_the_action":
        "инвариант «аудит не роняет операцию»: проверка в том, что вызов не бросает",
    "server/tests/test_db_migrations.py::test_ensure_notify_event_columns_is_idempotent":
        "миграция гоняется на КАЖДОМ старте; повтор не должен падать «duplicate column»",
    "server/tests/test_messenger.py::test_ws_connect_with_subprotocol":
        "сокет обязан ОТКРЫТЬСЯ; отказ приходит исключением WebSocketDisconnect",
    "server/tests/test_caddyfile.py::test_production_config_holds":
        "параметризован: проверку делает переданная функция _check_*(conf) внутри",
}


def _iter_test_files():
    for d in TEST_DIRS:
        base = os.path.join(ROOT, d)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x != "__pycache__"]
            for fn in sorted(filenames):
                if fn.startswith("test_") and fn.endswith(".py"):
                    path = os.path.join(dirpath, fn)
                    yield os.path.relpath(path, ROOT).replace("\\", "/"), path


def _tree(path):
    return ast.parse(io.open(path, encoding="utf-8").read())


def _has_check(node) -> bool:
    for n in ast.walk(node):
        if isinstance(n, (ast.Assert, ast.Raise)):
            return True
        if isinstance(n, ast.Call):
            name = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if name in _CHECK_CALLS or (name or "").startswith("assert"):
                return True
    return False


def _is_tautology(test) -> bool:
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return isinstance(test.operand, ast.Constant) and not test.operand.value
    return False


def _test_funcs(tree):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_"):
            yield n


# ── 1. Голое сравнение ──────────────────────────────────────────────────────────────
def _bare_comparisons(tree):
    out = []
    for fn in _test_funcs(tree):
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Expr) and isinstance(sub.value, ast.Compare):
                out.append((sub.lineno, fn.name))
    return out


def test_no_bare_comparison_instead_of_assert():
    bad = []
    for rel, path in _iter_test_files():
        for lineno, name in _bare_comparisons(_tree(path)):
            bad.append("%s:%d  %s" % (rel, lineno, name))
    assert not bad, ("сравнение отдельной строкой ничего не проверяет — нужен assert:\n  "
                     + "\n  ".join(bad))


# ── 2. Тавтология ───────────────────────────────────────────────────────────────────
def test_no_tautological_assert():
    bad = []
    for rel, path in _iter_test_files():
        for fn in _test_funcs(_tree(path)):
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Assert) and _is_tautology(sub.test):
                    bad.append("%s:%d  %s" % (rel, sub.lineno, fn.name))
    assert not bad, "assert-тавтология зелена всегда:\n  " + "\n  ".join(bad)


# ── 3. Тест без проверки ────────────────────────────────────────────────────────────
def test_every_test_checks_something():
    bad = []
    for rel, path in _iter_test_files():
        for fn in _test_funcs(_tree(path)):
            if _has_check(fn):
                continue
            key = "%s::%s" % (rel, fn.name)
            if key in _NO_ASSERT_ALLOWED:
                continue
            bad.append("%s:%d" % (key, fn.lineno))
    assert not bad, (
        "тест не проверяет НИЧЕГО и зелен всегда:\n  " + "\n  ".join(bad) +
        "\nЕсли проверка — это «вызов не бросает», внеси его в _NO_ASSERT_ALLOWED "
        "С ПРИЧИНОЙ.")


def test_the_allowlist_has_no_dead_entries():
    """В списке исключений не должно быть записей про несуществующие тесты.

    Мёртвая запись — это разрешение, выданное неизвестно чему: под её именем однажды
    заведут новый тест, и он окажется освобождён от проверки молча."""
    alive = set()
    for rel, path in _iter_test_files():
        for fn in _test_funcs(_tree(path)):
            alive.add("%s::%s" % (rel, fn.name))
    dead = sorted(k for k in _NO_ASSERT_ALLOWED if k not in alive)
    assert not dead, "в _NO_ASSERT_ALLOWED записи про несуществующие тесты: %s" % dead


# ── 4. Пропуск файла по отсутствию ОБЪЯВЛЕННОГО пакета ──────────────────────────────
def _declared_packages() -> set:
    """Имена пакетов из ОБЪЯВЛЕНИЙ (закомментированная строка объявлением не считается)."""
    names = set()
    files = [os.path.join(ROOT, "server", "requirements.txt"),
             os.path.join(ROOT, "requirements.txt"),
             os.path.join(ROOT, "server", "requirements-ai.txt"),
             os.path.join(ROOT, "pyproject.toml")]
    for path in files:
        if not os.path.isfile(path):
            continue
        for line in io.open(path, encoding="utf-8"):
            line = line.strip().strip(",").strip('"').strip("'")
            if not line or line.startswith("#"):
                continue
            for sep in ("==", ">=", "<=", "~=", ">", "<", "[", ";"):
                line = line.split(sep)[0]
            line = line.strip()
            if line and all(c.isalnum() or c in "-_." for c in line):
                names.add(line.lower().replace("-", "_"))
    return names


def _module_level_importorskip(tree):
    out = []
    for n in tree.body:
        call = None
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
            call = n.value
        elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            call = n.value
        if call is None or getattr(call.func, "attr", "") != "importorskip":
            continue
        if call.args and isinstance(call.args[0], ast.Constant):
            out.append((n.lineno, str(call.args[0].value)))
    return out


def test_no_test_file_switches_itself_off_by_a_declared_package():
    """🔑 Главное свойство файла. Дважды купленный урок, оба раза прогон был зелёным."""
    declared = _declared_packages()
    bad = []
    for rel, path in _iter_test_files():
        for lineno, pkg in _module_level_importorskip(_tree(path)):
            key = pkg.split(".")[0].lower().replace("-", "_")
            if key in declared:
                bad.append("%s:%d  importorskip(%r) — пакет ОБЪЯВЛЕН зависимостью"
                           % (rel, lineno, pkg))
    assert not bad, (
        "файл гасит себя целиком по отсутствию объявленного пакета:\n  " +
        "\n  ".join(bad) +
        "\nПропуск честен по отсутствию ПРЕДМЕТА проверки, а не ИНСТРУМЕНТА. "
        "Нет объявленной зависимости — это отказ, и он обязан быть громким.")


def test_the_declared_list_is_not_empty():
    """Обратный ход к разбору объявлений: пустой список сделал бы проверку выше
    зелёной навсегда, ничего не проверяя."""
    declared = _declared_packages()
    assert len(declared) > 20, "разбор объявлений сломан: нашлось %d" % len(declared)
    for must in ("fastapi", "sqlalchemy", "pdfplumber"):
        assert must in declared, "%s пропал из разбора объявлений" % must


# ── ОБРАТНЫЙ ХОД САМОГО СТОРОЖА ─────────────────────────────────────────────────────
_BAD_SOURCE = "\n".join([
    "import pytest",
    "pytest.importorskip('fastapi')",
    "",
    "",
    "def test_bare():",
    "    value = 2 + 2",
    "    value == 5",
    "",
    "",
    "def test_tauto():",
    "    assert True",
    "",
    "",
    "def test_nothing():",
    "    helper = 1",
    "",
])


def test_the_scanner_would_catch_all_four_shapes():
    """⚠️ Без этого сторож неотличим от сломанного: опечатка в разборе — и он зелен
    навсегда. Ровно так четыре раза подряд зеленел `pollingRespectsVisibility` при
    сломанном продукте."""
    tree = ast.parse(_BAD_SOURCE)
    assert _bare_comparisons(tree), "голое сравнение не распознано"
    tautologies = [s for fn in _test_funcs(tree) for s in ast.walk(fn)
                   if isinstance(s, ast.Assert) and _is_tautology(s.test)]
    assert tautologies, "assert True не распознан"
    empty = [fn.name for fn in _test_funcs(tree) if not _has_check(fn)]
    assert "test_nothing" in empty, "тест без проверки не распознан"
    skips = _module_level_importorskip(tree)
    assert skips and skips[0][1] == "fastapi", "модульный importorskip не распознан"


def test_the_scanner_does_not_flag_healthy_tests():
    """И обратное: на здоровом коде сторож обязан МОЛЧАТЬ, иначе его отключат."""
    tree = ast.parse("def test_ok():\n    assert 2 + 2 == 4\n")
    assert not _bare_comparisons(tree)
    assert all(_has_check(fn) for fn in _test_funcs(tree))
    assert not _module_level_importorskip(tree)
