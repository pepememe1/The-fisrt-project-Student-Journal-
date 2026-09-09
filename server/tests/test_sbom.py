"""
Сторож состава программы (SBOM) — `tools/build_sbom.py`.

SBOM спросят на приёмке у ВСГУТУ и в заявке в реестр российского ПО (ПП РФ № 1236).
Документ, разошедшийся с тем, что реально объявлено, ХУЖЕ отсутствующего: отсутствие
это «не подготовили», а расхождение — «предоставили недостоверные сведения».

⚠️ Здесь проверяются СВОЙСТВА, а не снимок состава. Список из 46 строк, вписанный в
ожидание, краснел бы на каждой законной новой зависимости и подталкивал «просто обновить
число» — ровно та грабля, на которой мы уже стояли
(`test_proxied_prefixes_are_all_online_only`). Поэтому ни одного числа-ожидания тут нет.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#⚠️ Здесь стоял `pytest.importorskip("json")` — сторож, который не может сработать
#НИКОГДА: json это стандартная библиотека. Такая строка не проверяет ничего, но
#выглядит проверкой. Убрана 05.09.2026 при разборе слепых тестов.


def _sbom():
    from tools.build_sbom import build
    return build(with_installed=False)


def _names(doc):
    return {c["name"].lower() for c in doc["components"]}


def test_sbom_is_wellformed_cyclonedx():
    doc = _sbom()
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["specVersion"] == "1.5"
    assert doc["components"], "состав пуст — значит объявления не прочитались вовсе"
    for c in doc["components"]:
        assert c.get("name"), "компонент без имени"
        assert c.get("purl", "").startswith("pkg:"), "компонент без purl: %s" % c
        assert c.get("type") == "library"


def test_every_declared_dependency_reaches_the_sbom():
    """СВОЙСТВО: что объявлено — то и в составе. Пропуск здесь означает, что покупателю
    показали неполный перечень, а нашли бы это при сверке с боевой машиной."""
    from tools.build_sbom import _declared
    doc = _sbom()
    have = _names(doc)
    missing = sorted(k for k in _declared() if k not in have)
    assert not missing, "объявлено, но в SBOM не попало: %s" % missing


def test_the_heavy_ai_stack_is_declared_not_commented():
    """🔑 Заготовка под переезд обязана быть ОБЪЯВЛЕНИЕМ, а не запиской.

    `faster-whisper` и `argostranslate` полгода лежали ЗАКОММЕНТИРОВАННЫМИ с припиской
    «раскомментировать на машине, где есть память». Такую строку не поставит ни одна
    команда и не увидит ни один инструмент — в том числе этот. Их появление в SBOM и
    есть доказательство, что на машине ВСГУТУ они установятся сами.
    """
    have = _names(_sbom())
    for pkg in ("faster-whisper", "argostranslate"):
        assert pkg in have, (
            "%s снова не объявлен — значит на новой машине он не поставится, "
            "и перевод/распознавание речи молча не включатся" % pkg)


def test_a_commented_out_line_is_not_a_declaration():
    """Обратный ход разбора: комментарий НЕ считается зависимостью.

    Без этого сторож выше был бы бессмысленным — закомментированный пакет попадал бы
    в SBOM и выглядел бы установленным."""
    from tools.build_sbom import _parse_requirement
    assert _parse_requirement("# argostranslate>=1.9") is None
    assert _parse_requirement("   #faster-whisper>=1.0") is None
    assert _parse_requirement("") is None
    #А обычная строка — считается, иначе тест был бы зелёным при сломанном разборе.
    parsed = _parse_requirement("argostranslate>=1.9  # перевод")
    assert parsed and parsed[0] == "argostranslate" and ">=1.9" in parsed[1]


def test_banned_packages_never_appear_in_the_sbom():
    """Запрещённый по существу пакет не должен доехать до документа для покупателя.

    Если он вернётся в объявления, `test_requirements_complete` покраснеет первым — но
    и здесь тоже: SBOM это то, что уходит НАРУЖУ, и молчаливое упоминание Google
    Translate или драйвера PostgreSQL в нём стоит дороже, чем в коде."""
    from test_requirements_complete import BANNED
    have = _names(_sbom())
    for pkg in BANNED:
        assert pkg.lower() not in have, (
            "в SBOM попал запрещённый пакет %s — он уйдёт покупателю" % pkg)


def test_sbom_states_what_it_does_not_cover():
    """Документ обязан сам называть свою границу.

    Транзитивные зависимости здесь не разворачиваются, и умолчать об этом нельзя:
    читатель решит, что видит полный состав. Честная граница в документе — разница
    между «неполно, и это написано» и «недостоверно»."""
    doc = _sbom()
    props = {p["name"]: p["value"] for p in doc["metadata"]["properties"]}
    assert "gb:scope" in props
    assert "транзитивные" in props["gb:scope"].lower()
    assert "gb:foreign_dependency" in props, (
        "нет утверждения об отсутствии принудительной зависимости от иностранного ПО — "
        "а его спрашивают в заявке в реестр")
