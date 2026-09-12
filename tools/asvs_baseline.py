# -*- coding: utf-8 -*-
"""
asvs_baseline.py — БАЗОВЫЙ УРОВЕНЬ БЕЗОПАСНОСТИ КАК ПРОВЕРЯЕМЫЙ ФАЙЛ, А НЕ КАК ТАБЛИЦА
В ДОКУМЕНТЕ.

━━ ЗАЧЕМ ━━
Требование заказчика (01.09.2026): «пройтись по OWASP ASVS 5.0 как по чек-листу» и
получить не «мы вроде всё сделали», а «вот 345 проверяемых требований, вот какие
применимы, вот доказательство каждого».

Ровно эту таблицу и просят на приёмке в вузе. Но таблица в markdown — это СНИМОК
ЗНАЧЕНИЯ, а у нас есть отдельный, дорого купленный урок про снимки: документ, который
описывает код, начинает врать в тот день, когда код меняют, и врёт молча. `CLAUDE.md`
из-за этого пришлось резать, «47 маркетинг-скиллов» оказались одним, а
`SECURITY-ARCHITECTURE.md` пришлось объявлять «описывает то, что есть» отдельной строкой,
потому что иначе никто бы не поверил.

Поэтому источник правды здесь — ДВА JSON-файла, а markdown только генерируется:

  docs/security/asvs-5.0.0-en.json   официальный стандарт (скачан с github.com/OWASP/ASVS,
                                     положен в репозиторий целиком: сторож не имеет права
                                     зависеть от сети, а на приёмке спросят, по какой
                                     именно редакции мы отчитываемся);
  docs/security/asvs-baseline.json   НАШИ вердикты по требованиям, с доказательствами.

Доказательство — это не фраза «сделано», а пара «файл + строка, которая обязана в нём
быть». Сторож `tests/test_asvs_baseline.py` открывает файл и ищет строку. Убрали защиту
из кода — сторож краснеет, и вердикт «выполнено» нельзя оставить по недосмотру.

⚠️ ЧЕГО ЭТОТ МЕХАНИЗМ НЕ ДЕЛАЕТ, и говорить об этом надо вслух. Он проверяет, что
названное доказательство НА МЕСТЕ, а не что оно ДОСТАТОЧНО. Строка `require_admin` в
файле не означает, что она стоит на нужной ручке. Это защита от протухания, а не замена
ревью — ровно как хеш-сумма не читает содержимое файла.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

#Каталог репозитория: файл лежит в tools/, значит корень — на уровень выше.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECURITY_DIR = os.path.join(ROOT, "docs", "security")
STANDARD_PATH = os.path.join(SECURITY_DIR, "asvs-5.0.0-en.json")
BASELINE_PATH = os.path.join(SECURITY_DIR, "asvs-baseline.json")

#Набор закрыт намеренно. «Частично» — отдельный статус, а не разновидность «сделано»:
#без него половинчатая защита закрывала бы пункт наравне с полной, и на приёмке мы
#предъявили бы галочку там, где её нет.
STATUSES = ("done", "partial", "todo", "n/a")

#Статусы, которые ОБЯЗАНЫ опираться на код. «todo» доказательств не требует (его ещё
#нет), «n/a» требует причину вместо доказательства.
NEEDS_EVIDENCE = ("done", "partial")


class BaselineError(Exception):
    """Файл базового уровня непригоден к разбору (не JSON, нет обязательных полей)."""


# ────────────────────────────── чтение ──────────────────────────────

def _read_json(path: str) -> Any:
    if not os.path.exists(path):
        raise BaselineError("нет файла %s" % path)
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:                                   # pragma: no cover - редкий путь
        raise BaselineError("не разбирается %s: %s" % (path, exc))


def standard_sha256(path: str = STANDARD_PATH) -> str:
    """Хеш файла стандарта.

    Нужен не для целостности при передаче, а чтобы СМЕНА РЕДАКЦИИ стандарта была
    заметна. Обновят ASVS до 5.1 — набор требований другой, а наши вердикты остались от
    прежнего; молча унаследовать их значит отчитаться по документу, которого никто не
    читал.

    🔥 ПЕРЕВОДЫ СТРОК НОРМАЛИЗУЮТСЯ, И ЭТО ПОЧИНКА НАСТОЯЩЕГО ДЕФЕКТА (12.09.2026).
    Хеш считался по СЫРЫМ байтам, а git на Windows выкладывает файл с CRLF: в репозитории
    149 407 байт с LF, на диске у Влада — 152 181 байт с CRLF. Содержимое посимвольно
    одно и то же, а хеш разный, и сторож объявлял «редакция стандарта СМЕНИЛАСЬ» на
    ровном месте. То есть проверка была КРАСНОЙ у одного человека и зелёной у другого —
    ровно тот случай, который в проекте уже стоил разбирательства дважды (`config.IS_PROD`
    в CI и состояние переводчика): **прогон не имеет права зависеть от того, чья это
    машина**. Та же грабля и та же починка, что в `tools/check_prod_caddyfile.sh`, где
    сырое сравнение хешей давало ложное расхождение 15 097 против 14 914 байт.

    ⚠️ Смысл проверки при этом НЕ ослаблен: другая редакция отличается ТЕКСТОМ требований,
    а не только концами строк. Объявленный `standard_sha256` менять не понадобилось — он
    и есть хеш LF-варианта, то есть содержимое всё это время совпадало.
    ⚠️ Красный сторож, который «всегда такой», перестают читать — и вместе с ним
    перестают читать настоящие расхождения вердиктов, ради которых он и заведён.
    """
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest()


def load_standard(path: str = STANDARD_PATH) -> Dict[str, Dict[str, Any]]:
    """Официальный ASVS → плоская карта {shortcode: {...}}.

    Уровень (`L`) в исходном файле — строка («1», «2», «3») и иногда пустая: требование
    без уровня в 5.0 встречается. Приводим к int или None, но НЕ выкидываем такие
    требования: «нет уровня» не значит «не считается».
    """
    doc = _read_json(path)
    out: Dict[str, Dict[str, Any]] = {}
    for chapter in doc.get("Requirements", []):
        ch_code = chapter.get("Shortcode", "")
        for section in chapter.get("Items", []):
            sec_code = section.get("Shortcode", "")
            sec_name = section.get("Name", "")
            for item in section.get("Items", []):
                code = item.get("Shortcode", "")
                if not code:
                    continue
                raw_level = str(item.get("L", "")).strip()
                out[code] = {
                    "id": code,
                    "chapter": ch_code,
                    "chapter_name": chapter.get("Name", "") or chapter.get("ShortName", ""),
                    "section": sec_code,
                    "section_name": sec_name,
                    "level": int(raw_level) if raw_level.isdigit() else None,
                    "text": item.get("Description", ""),
                }
    if not out:
        raise BaselineError("в %s не нашлось ни одного требования" % path)
    return out


def load_baseline(path: str = BASELINE_PATH) -> Dict[str, Any]:
    doc = _read_json(path)
    if not isinstance(doc, dict):
        raise BaselineError("%s: ожидался объект" % path)
    doc.setdefault("items", {})
    doc.setdefault("chapters", {})
    return doc


# ────────────────────────────── разрешение вердикта ──────────────────────────────

def verdict_for(code: str, req: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Вердикт по одному требованию: свой, унаследованный от главы, либо `todo`.

    Наследование от главы заведено не для краткости. У нас есть главы, неприменимые
    ЦЕЛИКОМ и по одной причине: OAuth/OIDC мы не реализуем вовсе (свои токены), WebRTC в
    продукте нет. Расписать 36 требований OAuth поимённо значило бы 36 раз повторить одну
    и ту же фразу — и один раз ошибиться в ней, а на приёмке разночтение внутри
    собственного документа выглядит хуже, чем отсутствие строки.

    ⚠️ Точечный вердикт ПЕРЕБИВАЕТ главу. Иначе неприменимость главы прятала бы
    требование, которое к нам всё-таки относится.
    """
    own = baseline.get("items", {}).get(code)
    if own:
        out = dict(own)
        out["source"] = "item"
        return out
    ch = baseline.get("chapters", {}).get(req["chapter"])
    if ch:
        out = dict(ch)
        out["source"] = "chapter"
        return out
    return {"status": "todo", "source": "default", "note": "", "evidence": [], "tests": []}


# ────────────────────────────── проверки ──────────────────────────────

def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def check(standard: Optional[Dict[str, Dict[str, Any]]] = None,
          baseline: Optional[Dict[str, Any]] = None,
          root: str = ROOT) -> List[str]:
    """Все претензии к базовому уровню одним списком. Пустой список = возражений нет.

    Возвращаем СПИСОК СТРОК, а не бросаем на первой ошибке: человеку, правящему файл,
    нужен весь перечень за один прогон, а не по одной претензии за круг.

    ⚠️ Здесь СОЗНАТЕЛЬНО нет проверки «закрыто не менее N требований». Такой сторож —
    снимок значения: он краснеет на каждом законном добавлении требования в стандарт и
    подталкивает «просто обновить ожидание», то есть ровно к тому, от чего защищает. Мы
    уже наступали на это с `test_proxied_prefixes_are_all_online_only`. Проверяются
    СВОЙСТВА: вердикт ссылается на существующее требование, «выполнено» опирается на
    живое доказательство, «неприменимо» названо с причиной.
    """
    standard = standard if standard is not None else load_standard()
    baseline = baseline if baseline is not None else load_baseline()
    problems: List[str] = []

    # ── редакция стандарта ──
    declared = str(baseline.get("standard_sha256", "")).strip().lower()
    if not declared:
        problems.append("baseline: не объявлен standard_sha256 — по какой редакции ASVS отчёт?")
    else:
        actual = standard_sha256()
        if declared != actual:
            problems.append(
                "baseline: редакция стандарта СМЕНИЛАСЬ (объявлено %s…, на диске %s…). "
                "Вердикты относятся к прежнему тексту требований — перечитать, а не "
                "переписывать хеш" % (declared[:12], actual[:12]))

    chapters_in_standard = {req["chapter"] for req in standard.values()}

    # ── вердикты уровня главы ──
    for ch_code, ch in baseline.get("chapters", {}).items():
        if ch_code not in chapters_in_standard:
            problems.append("chapters.%s: такой главы в стандарте нет" % ch_code)
        problems.extend(_check_verdict("chapters.%s" % ch_code, ch, root))

    # ── точечные вердикты ──
    for code, item in baseline.get("items", {}).items():
        if code not in standard:
            problems.append(
                "items.%s: такого требования в ASVS 5.0.0 нет — выдуманный номер в "
                "отчёте о соответствии хуже отсутствующей строки" % code)
            continue
        problems.extend(_check_verdict("items.%s" % code, item, root))

    return problems


def _check_verdict(where: str, verdict: Dict[str, Any], root: str) -> List[str]:
    problems: List[str] = []
    status = verdict.get("status", "")
    if status not in STATUSES:
        problems.append("%s: статус %r не из набора %s" % (where, status, ", ".join(STATUSES)))
        return problems

    note = str(verdict.get("note", "") or "").strip()
    evidence = _as_list(verdict.get("evidence"))
    tests = _as_list(verdict.get("tests"))

    if status == "n/a" and not note:
        problems.append(
            "%s: «неприменимо» без причины. Без причины этот статус — способ закрыть "
            "требование молча" % where)

    if status in NEEDS_EVIDENCE and not evidence:
        problems.append("%s: статус %r без единого доказательства" % (where, status))

    if status == "done" and not tests:
        problems.append(
            "%s: «выполнено» без единого теста. Защита без сторожа неотличима от "
            "её отсутствия при следующей правке" % where)

    if status == "partial" and not note:
        problems.append("%s: «частично» без пояснения, ЧТО именно не закрыто" % where)

    # ── доказательства ──
    for idx, ev in enumerate(evidence):
        tag = "%s.evidence[%d]" % (where, idx)
        if not isinstance(ev, dict) or not ev.get("file"):
            problems.append("%s: доказательство без поля file" % tag)
            continue
        rel = ev["file"]
        full = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.exists(full):
            problems.append("%s: файла %s нет — доказательство протухло" % (tag, rel))
            continue
        needles = [str(n) for n in _as_list(ev.get("contains")) if str(n).strip()]
        if not needles:
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError as exc:                                 # pragma: no cover - редкий путь
            problems.append("%s: не читается %s (%s)" % (tag, rel, exc))
            continue
        for needle in needles:
            if needle not in body:
                problems.append(
                    "%s: в %s больше нет строки %r — защита уехала, а вердикт остался"
                    % (tag, rel, needle))

    # ── тесты ──
    for idx, t in enumerate(tests):
        rel = str(t).split("::")[0]                            # допускаем «файл::имя_теста»
        full = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.exists(full):
            problems.append("%s.tests[%d]: нет файла %s" % (where, idx, rel))

    return problems


# ────────────────────────────── сводка ──────────────────────────────

def coverage(standard: Optional[Dict[str, Dict[str, Any]]] = None,
             baseline: Optional[Dict[str, Any]] = None,
             target_level: Optional[int] = None) -> Dict[str, Any]:
    """Сводка покрытия. Считается, а не хранится: хранимое число устаревает молча."""
    standard = standard if standard is not None else load_standard()
    baseline = baseline if baseline is not None else load_baseline()
    if target_level is None:
        target_level = int(baseline.get("target_level", 2))

    by_status: Dict[str, int] = {s: 0 for s in STATUSES}
    by_chapter: Dict[str, Dict[str, int]] = {}
    in_scope_total = 0
    in_scope_done = 0

    for code, req in standard.items():
        v = verdict_for(code, req, baseline)
        st = v.get("status", "todo")
        if st not in by_status:
            by_status[st] = 0
        by_status[st] += 1
        ch = by_chapter.setdefault(req["chapter"], {s: 0 for s in STATUSES})
        ch[st] = ch.get(st, 0) + 1
        #«В охвате» = уровень требования не выше целевого И оно применимо к нам.
        #Требование без уровня считаем входящим: пропустить его молча дороже, чем
        #объяснить, почему оно не про нас.
        lvl = req.get("level")
        if st != "n/a" and (lvl is None or lvl <= target_level):
            in_scope_total += 1
            if st == "done":
                in_scope_done += 1

    return {
        "total": len(standard),
        "target_level": target_level,
        "by_status": by_status,
        "by_chapter": by_chapter,
        "in_scope_total": in_scope_total,
        "in_scope_done": in_scope_done,
        "in_scope_percent": round(100.0 * in_scope_done / in_scope_total, 1) if in_scope_total else 0.0,
    }


def chapter_titles(standard: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Tuple[str, str]]:
    """Главы в порядке стандарта: [(V1, «Encoding and Sanitization»), …]."""
    standard = standard if standard is not None else load_standard()
    seen: Dict[str, str] = {}
    for req in standard.values():
        seen.setdefault(req["chapter"], req["chapter_name"])
    return sorted(seen.items(), key=lambda kv: int(kv[0][1:]) if kv[0][1:].isdigit() else 0)


def iter_in_order(standard: Optional[Dict[str, Dict[str, Any]]] = None) -> Iterable[Dict[str, Any]]:
    """Требования в порядке стандарта (V1.1.1, V1.1.2, … V17.3.4), а не по алфавиту.

    Алфавит поставил бы V10 между V1 и V2, и читатель отчёта решил бы, что мы потеряли
    главы.
    """
    standard = standard if standard is not None else load_standard()

    def key(code: str) -> Tuple[int, ...]:
        parts = code.lstrip("V").split(".")
        return tuple(int(p) if p.isdigit() else 0 for p in parts)

    for code in sorted(standard, key=key):
        yield standard[code]
