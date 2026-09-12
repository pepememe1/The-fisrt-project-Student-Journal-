# -*- coding: utf-8 -*-
"""
test_asvs_baseline.py — 🔒 ОТЧЁТ О СООТВЕТСТВИИ ASVS НЕ ИМЕЕТ ПРАВА ПРОТУХНУТЬ МОЛЧА.

━━ ЗАЧЕМ ━━
`docs/security/asvs-baseline.json` — это то, что мы предъявим на приёмке в вузе: «вот 345
требований OWASP ASVS 5.0, вот применимые, вот доказательство каждого». Документ такого
рода опасен ровно тем же, чем был опасен разросшийся CLAUDE.md: он описывает код, а
живёт отдельно от кода, и в день расхождения продолжает уверенно утверждать прежнее.

У нас этот класс дефекта уже назван поимённо и не по одному разу: «47 маркетинг-скиллов»,
которых оказался один; «Playwright стоит с 18.08», когда его не было; ротация ключа базы,
месяц числившаяся отсутствующей при живом `server/rotate_db_key.py`. Разница в том, что
здесь ценой будет не потерянное время, а НЕВЕРНОЕ УТВЕРЖДЕНИЕ О БЕЗОПАСНОСТИ, отданное
покупателю письменно.

━━ ЧТО ПРОВЕРЯЕТСЯ ━━
Свойства, а не количество:
  • вердикт ссылается на СУЩЕСТВУЮЩЕЕ требование стандарта (выдуманный номер в отчёте о
    соответствии хуже отсутствующей строки);
  • «выполнено» опирается на живое доказательство И на тест;
  • доказательство — это файл, который есть, и строка, которая в нём ЕСТЬ СЕЙЧАС;
  • «неприменимо» и «частично» названы с причиной;
  • редакция стандарта не сменилась под уже вынесенными вердиктами.

⚠️ Числа «закрыто N требований» здесь СОЗНАТЕЛЬНО нет. Такой сторож краснеет на каждом
законном добавлении и подталкивает «просто обновить ожидание» — то есть ровно к тому, от
чего защищает. Мы это уже проходили на `test_proxied_prefixes_are_all_online_only`.

⚠️ Честная граница: сторож проверяет, что доказательство НА МЕСТЕ, а не что оно
ДОСТАТОЧНО. Строка `require_admin` в файле не означает, что она стоит на нужной ручке.
Это защита от протухания, а не замена ревью.
"""
import copy
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(ROOT, "tools", "asvs_baseline.py")


def _load_lib():
    """Импорт по пути: tools/ не пакет, а заводить там __init__.py ради теста не стоит.

    Библиотеку берём ту же, которой пользуется генератор отчёта. Повторить её логику
    здесь было бы нашей классической ошибкой «тест повторяет формулу у себя»: он сверял
    бы копию с копией, и правка в продукте его бы не тронула.
    """
    spec = importlib.util.spec_from_file_location("asvs_baseline", _LIB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lib():
    assert os.path.exists(_LIB), "tools/asvs_baseline.py пропал — отчёт нечем проверять"
    return _load_lib()


@pytest.fixture(scope="module")
def standard(lib):
    return lib.load_standard()


@pytest.fixture(scope="module")
def baseline(lib):
    return lib.load_baseline()


# ────────────────────────────── прямой ход ──────────────────────────────

def test_standard_is_the_real_asvs_500(standard):
    """Вендоренный стандарт — настоящий ASVS 5.0.0, а не огрызок.

    Проверяем то, что объявлено самим OWASP: 17 глав и 345 требований. Это ЕДИНСТВЕННОЕ
    место, где число уместно — оно описывает ЧУЖОЙ неизменяемый документ фиксированной
    редакции, а не наш растущий продукт.
    """
    assert len(standard) == 345, (
        "в файле стандарта %d требований вместо 345 — подменена редакция или файл битый"
        % len(standard))
    chapters = {req["chapter"] for req in standard.values()}
    assert len(chapters) == 17, "глав %d вместо 17" % len(chapters)


def test_baseline_has_no_complaints(lib, standard, baseline):
    """Главная проверка: ни одной претензии к базовому уровню."""
    problems = lib.check(standard, baseline)
    assert not problems, "базовый уровень ASVS разошёлся с кодом:\n  " + "\n  ".join(problems)


def test_every_done_item_names_both_code_and_test(standard, baseline, lib):
    """«Выполнено» без теста — это обещание, а не защита.

    Дублирует часть `check`, и намеренно: если однажды кто-то ослабит правило в
    библиотеке, отдельный тест на самое дорогое из правил останется.
    """
    weak = []
    for code, req in standard.items():
        v = lib.verdict_for(code, req, baseline)
        if v.get("status") != "done":
            continue
        if not v.get("evidence") or not v.get("tests"):
            weak.append(code)
    assert not weak, "закрыты без доказательства или без теста: %s" % ", ".join(sorted(weak))


def test_open_items_are_not_hidden_behind_silence(standard, baseline, lib):
    """Не закрытые требования обязаны быть ВИДНЫ, а не выпасть из отчёта.

    Отсутствие записи трактуется как `todo` — это правило, а не случайность, и здесь оно
    закреплено. Иначе «в отчёте нет строки» однажды прочитают как «вопрос снят».
    """
    cov = lib.coverage(standard, baseline)
    assert cov["by_status"]["todo"] > 0, (
        "ни одного открытого требования — так не бывает; похоже, умолчание перестало "
        "быть `todo`, и отчёт теперь молча закрывает то, чего никто не делал")
    assert cov["in_scope_total"] > 0


def test_no_secrets_in_the_report(baseline):
    """В отчёт о безопасности не должны просочиться сами секреты.

    Файл уедет покупателю и в заявку. Ищем очевидное: приватные ключи, токены, строки
    подключения. Проверка грубая намеренно — она ловит «вставил для наглядности», а не
    изощрённое сокрытие.
    """
    import json
    body = json.dumps(baseline, ensure_ascii=False)
    for needle in ("BEGIN PRIVATE KEY", "BEGIN RSA", "BEGIN OPENSSH",
                   "postgresql://", "GRADEBOOK_DB_KEY=", "JWT_SECRET="):
        assert needle not in body, "в отчёт попал секрет: %s" % needle


# ────────────────────────────── обратный ход ──────────────────────────────
#
# Проверка, которая зелена и БЕЗ починки, неотличима от исправного кода и хуже
# отсутствия проверки. Правило куплено дорого: `pollingRespectsVisibility` пришлось
# чинить ЧЕТЫРЕ раза, и каждый раз ошибку ловил обратный ход, а не чтение.
#
# Каждый случай ниже — это реальный способ незаметно испортить отчёт.

def _mutants():
    def m_anchor_moved(b):
        b["items"]["V9.1.2"]["evidence"][0]["contains"] = "algorithms=[ЗАВЕДОМО-НЕТ]"

    def m_file_gone(b):
        b["items"]["V7.2.1"]["evidence"][0]["file"] = "server/app/net_takogo_faila.py"

    def m_invented_id(b):
        b["items"]["V6.99.99"] = {"status": "done",
                                  "evidence": [{"file": "server/app/deps.py"}],
                                  "tests": ["server/tests/test_auth.py"]}

    def m_done_without_test(b):
        b["items"]["V9.1.1"]["tests"] = []

    def m_done_without_evidence(b):
        b["items"]["V9.1.1"]["evidence"] = []

    def m_na_without_reason(b):
        b["chapters"]["V17"]["note"] = ""

    def m_partial_without_reason(b):
        b["items"]["V7.5.2"]["note"] = "   "

    def m_test_file_gone(b):
        b["items"]["V9.1.1"]["tests"] = ["server/tests/test_net_takogo_faila.py"]

    def m_standard_swapped(b):
        b["standard_sha256"] = "0" * 64

    def m_bogus_status(b):
        b["items"]["V9.1.1"]["status"] = "наверное"

    def m_bogus_chapter(b):
        b["chapters"]["V42"] = {"status": "n/a", "note": "выдуманная глава"}

    return [
        ("якорь доказательства уехал из файла", m_anchor_moved),
        ("файл доказательства удалён", m_file_gone),
        ("выдуманный номер требования", m_invented_id),
        ("«выполнено» без теста", m_done_without_test),
        ("«выполнено» без доказательства", m_done_without_evidence),
        ("«неприменимо» без причины", m_na_without_reason),
        ("«частично» без пояснения", m_partial_without_reason),
        ("названный тест не существует", m_test_file_gone),
        ("подменена редакция стандарта", m_standard_swapped),
        ("статус вне набора", m_bogus_status),
        ("глава вне стандарта", m_bogus_chapter),
    ]


@pytest.mark.parametrize("name,mutate", _mutants(), ids=[n for n, _ in _mutants()])
def test_checker_notices_a_broken_report(lib, standard, baseline, name, mutate):
    """Каждая порча отчёта обязана быть замечена."""
    spoiled = copy.deepcopy(baseline)
    mutate(spoiled)
    problems = lib.check(standard, spoiled)
    assert problems, "порча «%s» прошла незамеченной — сторож не работает" % name


def test_the_edition_hash_does_not_depend_on_line_endings(lib, tmp_path):
    """Прогон не имеет права зависеть от того, чья это машина (12.09.2026).

    Хеш редакции считался по СЫРЫМ байтам, а git на Windows выкладывает файл с CRLF: в
    репозитории 149 407 байт с LF, на диске 152 181 с CRLF. Содержимое посимвольно то же,
    а сторож объявлял «редакция стандарта СМЕНИЛАСЬ» — то есть был КРАСНЫМ у одного
    человека и зелёным у другого. Сигнал, который всегда красный, перестают читать, и
    вместе с ним перестают читать настоящие расхождения вердиктов, ради которых сторож
    и заведён.

    ⚠️ Байты собираются числами, без escape-последовательностей: тест про переводы строк,
    написанный через них, слишком легко испортить при переносе — и он тогда проверит не то.
    """
    LF, CR = bytes([10]), bytes([13])
    body = b'{"a": 1,' + LF + b' "b": 2}' + LF
    lf_file = tmp_path / "lf.json"
    crlf_file = tmp_path / "crlf.json"
    lf_file.write_bytes(body)
    crlf_file.write_bytes(body.replace(LF, CR + LF))
    assert lf_file.read_bytes() != crlf_file.read_bytes(), "файлы обязаны отличаться байтами"
    assert lib.standard_sha256(str(lf_file)) == lib.standard_sha256(str(crlf_file))


def test_the_edition_hash_still_notices_a_real_change(lib, tmp_path):
    """Обратный ход: нормализация не имеет права ослабить смысл проверки. Другая
    редакция отличается ТЕКСТОМ требований, и это обязано быть заметно."""
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_bytes(b'{"req": "one"}')
    b.write_bytes(b'{"req": "two"}')
    assert lib.standard_sha256(str(a)) != lib.standard_sha256(str(b))
