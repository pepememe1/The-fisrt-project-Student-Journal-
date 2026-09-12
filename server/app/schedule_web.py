"""
schedule_web.py — Серверная выдача расписания ВСГУТУ для веб-версии.

Переиспользует ДЕСКТОПНЫЙ парсер портала (schedule/parser.py, только stdlib+requests,
без Qt) — формулу разбора не дублируем. В отличие от десктопа (там каждый клиент сам
тянет портал), для сайта портал дёргает СЕРВЕР и кэширует снимок в памяти (TTL 3ч) —
чтобы не бомбить portal.esstu.ru на каждый заход браузера. Данные публичные, ПДн не
участвуют (152-ФЗ не задет).

Парсим по ОДНОЙ группе (быстро), а не весь колледж: браузеру нужна только его группа.
Любая сетевая ошибка/оффлайн → пустой снимок (эндпоинт отдаёт 200 с пустыми днями).

Категории (schedule/parser.py::CATEGORIES) — «колледж» была единственной изначально,
теперь портал читается ещё в трёх разделах (бакалавриат/заочное 1/заочное 2). Кэши
ниже ключуются по категории, дефолт везде — «колледж» (DEFAULT_CATEGORY), поэтому
любой существующий вызов без явной категории продолжает вести себя как раньше.
"""
import os
import sys
import time
import math
import threading
from datetime import date

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_lock = threading.Lock()
_TTL = 3 * 3600                      # как автo-обновление в десктопе (кэш старше 3ч → рефреш)
_index: dict = {}                    # category -> {"ts": float, "pairs": [(name, href)]}
_groups: dict = {}                   # category -> {name: {"ts": float, "data": dict}}


def _parser():
    """Ленивый импорт парсера (изолирует возможные сбои импорта от старта сервера)."""
    from schedule import parser  # noqa: E402  (schedule/__init__ импортит только math/datetime/model)
    return parser


def default_category() -> str:
    return _parser().DEFAULT_CATEGORY


def categories() -> list:
    """[{key, label, dated}] — реестр категорий для фронта (ОДИН источник правды,
    список меток не дублируется в JS). dated — сессионный формат (Заочное 1/2):
    дни это календарные даты, а не Пн-Сб, и число «недель» не всегда 2 — фронт
    должен рендерить сетку иначе (см. SchedulePage.vue)."""
    p = _parser()
    return [{"key": k, "label": v["label"], "dated": bool(v["dated"])}
           for k, v in p.CATEGORIES.items()]


def _academic_week_start(d: date) -> date:
    """Понедельник первой учебной недели полугодия — перенос `getReferenceDate()`
    портала. Полное объяснение правила (включая асимметрию «будни назад, выходные
    вперёд») — в докстринге `schedule/store.py::academic_week_start`."""
    ref_year = d.year - 1 if d.month <= 7 else d.year
    first_sep = date(ref_year, 9, 1)
    dow = (first_sep.weekday() + 1) % 7          # JS getDay(): Вс=0, Пн=1 … Сб=6
    if dow == 1:
        return date(ref_year, 9, 1)
    if 2 <= dow <= 5:
        return date(ref_year, 8, 31 - dow + 2)
    if dow == 6:
        return date(ref_year, 9, 3)
    return date(ref_year, 9, 2)


def current_week_parity(d: date | None = None) -> int:
    """1 (I неделя) / 2 (II неделя) — тот же расчёт, что в schedule/store.py.

    🔥 ИСПРАВЛЕНО (31.08.2026): считаем от начала УЧЕБНОГО года, как портал, а не
    «номер недели с 1 января». Прежняя формула разошлась с порталом ровно на неделю
    (у нас II, у портала I), и студент видел расписание чужой недели. Разбор, ссылка
    на исходник портала (`portal.esstu.ru/menu.htm`) и объяснение обеих веток — в
    докстринге `schedule/store.py::current_week_parity`.

    ⚠️ ЗДЕСЬ КОПИЯ, А НЕ ИМПОРТ, и это осознанно: `schedule/store.py` десктопный,
    тянет `log`, `data_store` и `core.DBManager`, а на сервере их нет — когда store
    однажды подтянулся сюда импортом, снимок расписания для сайта не собирался
    ВООБЩЕ (держит `tests/test_schedule_pkg_server_safe.py`). Согласованность копий
    держит контракт `docs/contracts/week-parity-cases.json` (Python ↔ Java-виджет).
    """
    d = d or date.today()
    ref = _academic_week_start(d)
    diff = (d - ref).days / 7
    if diff >= 0:
        return 1 if math.trunc(diff) % 2 == 0 else 2
    return 2 if math.trunc(diff + 1 / 7) % 2 == 0 else 1


def _load_index(category: str = "", force: bool = False):
    """[(имя, href, курс)] категории — курс разведан вживую (3.5.5, столбец
    таблицы индекса, см. schedule/parser.py::list_category_groups_with_course).
    Один кэш/один поход на портал на всех потребителей курса и без него —
    список_groups/_href_for просто игнорируют 3-й элемент кортежа."""
    category = category or default_category()
    with _lock:
        entry = _index.get(category, {"ts": 0.0, "pairs": []})
        fresh = entry["pairs"] and (time.time() - entry["ts"] < _TTL)
    if fresh and not force:
        return entry["pairs"]
    p = _parser()
    html = p.fetch_text(p.category_index_url(category))
    pairs = p.list_category_groups_with_course(html, category)
    with _lock:
        _index[category] = {"ts": time.time(), "pairs": pairs}
    return pairs


def list_groups(category: str = "") -> list:
    """Имена групп категории (для college — только «К»). Пустой список при оффлайне/ошибке."""
    category = category or default_category()
    try:
        return [name for name, _href, _course in _load_index(category)]
    except Exception:
        return []


def groups_by_course(category: str = "") -> dict:
    """{курс(int): [имена групп]} категории — для кнопок «Курс» в «Группах»/
    «Студентах»/«Расписании» (3.5.5). Курс НЕ фиксирован на 4 — реально
    встречается 5-6 на живых данных бакалавриата/заочного. Пусто при
    оффлайне/ошибке (тот же принцип деградации, что у list_groups)."""
    category = category or default_category()
    out: dict[int, list] = {}
    try:
        for name, _href, course in _load_index(category):
            out.setdefault(course, []).append(name)
    except Exception:
        return {}
    return out


def _index_expanded(category: str = "") -> list:
    """[(имя, курс)] — составные записи индекса РАЗВЁРНУТЫ в настоящие имена групп.

    🔥 ЗАЧЕМ. Портал кладёт две группы с общим расписанием в ОДНУ строку индекса:
    «К74/3,75.0», «К14,15.0», «К73/1,74.01» — таких записей 18 из 89, каждая пятая группа
    колледжа. `list_groups` отдаёт их как есть, и для СПИСКА ВЫБОРА это значит, что у
    К75.0 нет своей кнопки вовсе: её студент видит чужое имя и решает, что его группы
    здесь нет. Ровно та жалоба, с которой началась починка `_href_for` 04.09.2026, только
    с другой стороны — тогда чинили ссылку, а список остался прежним.

    ⚠️ Разбор НЕ СВОЙ: зовём `parser.expand_composite_group`, который держат 21 тест
    (`tests/test_schedule_composite_groups.py`). Вторая реализация того же правила
    разошлась бы с первой молча, и выбранная в списке группа перестала бы открываться.
    ⚠️ Сама строка «К74/3,75.0» из списка УБИРАЕТСЯ: группой она не является, и кнопка с
    таким именем — приглашение выбрать то, чего нет. Но только когда разбор реально дал
    части: у категорий без префикса (бакалавриат, заочное) запятая значит другое, и там
    запись остаётся как есть — выдуманное имя хуже неудобного (§«лучше не найти, чем
    найти не то»).
    ⚠️ Порядок сохраняем портальный, дубли убираем: одна и та же группа может значиться
    и отдельной строкой, и частью составной.
    ⚠️ **КАТЕГОРИЮ РЕЗОЛВИМ ЗДЕСЬ, А НЕ ПОЛАГАЕМСЯ НА ВЫЗЫВАЮЩЕГО.** `_load_index("")`
    внутри себя подставит колледж, а `expand_composite_group(name, "")` получит ПУСТОЙ
    префикс и не развернёт НИЧЕГО — то есть вызов с умолчанием молча перестал бы делать
    главное, ради чего функция и написана. Сегодня оба вызывающих передают категорию явно,
    и потому дефект спит; это ровно наш класс «первое забытое место».
    """
    category = category or default_category()
    p = _parser()
    out: list = []
    seen: set = set()
    for name, _href, course in _load_index(category):
        names = p.expand_composite_group(name, category)
        real = names[1:] if len(names) > 1 else [name]
        for r in real:
            if r not in seen:
                seen.add(r)
                out.append((r, course))
    return out


def list_groups_expanded(category: str = "") -> list:
    """Имена групп категории для СПИСКА ВЫБОРА (составные записи развёрнуты).

    ⚠️ Отдельно от `list_groups`, а не вместо неё. `list_groups` кормит кабинет, админку
    и `groups_by_course_cached` (через него — курс студента на ГЛАВНОЙ странице), и молча
    менять там состав списка значит трогать пути, о которых эта задача не просила.
    """
    category = category or default_category()
    try:
        return [name for name, _course in _index_expanded(category)]
    except Exception:
        return []


def groups_by_course_expanded(category: str = "") -> dict:
    """{курс: [группы]} для списка выбора — те же правила, что у `list_groups_expanded`.

    Часть составной записи наследует курс своей строки: он разведан по столбцу индекса, а
    строка у обеих групп одна. Это честнее, чем «курс неизвестен»: расписание у них тоже
    общее.
    """
    category = category or default_category()
    out: dict = {}
    try:
        for name, course in _index_expanded(category):
            out.setdefault(course, []).append(name)
    except Exception:
        return {}
    return out


_warming: set = set()                # категории, для которых прогрев уже идёт


def _warm_index_async(category: str) -> None:
    """Разогреть кэш индекса В ФОНЕ, не задерживая текущий запрос.

    ⚠️ Это НЕ планировщик: поток одноразовый и заводится только на промахе кэша, то есть
    не чаще раза в TTL (3 ч) на категорию. Флаг `_warming` не даёт запустить второй на
    те же данные — иначе тридцать одновременных заходов дали бы тридцать потоков и
    тридцать походов на портал за один и тот же файл.
    """
    with _lock:
        if category in _warming:
            return
        _warming.add(category)

    def _run():
        try:
            _load_index(category)
        except Exception:
            pass                     # оффлайн — обычное состояние, а не сбой
        finally:
            with _lock:
                _warming.discard(category)

    threading.Thread(target=_run, name=f"gb-warm-index-{category}", daemon=True).start()


def groups_by_course_cached(category: str = "") -> dict:
    """{курс: [группы]} ТОЛЬКО из уже прогретого кэша — портал здесь не дёргается НИКОГДА.

    🔥 Зачем отдельная функция, а не просто `groups_by_course`. От этого индекса теперь
    зависит КУРС в профиле студента (`webdata.group_course`), то есть обычная главная
    страница кабинета. `groups_by_course` при промахе кэша идёт в сеть с таймаутом 20 с —
    на боевом сервере это раз в три часа и незаметно, а внутри ДЕСКТОПНОЙ программы
    (тот же `server/app` на 127.0.0.1) кэш в оффлайне не наполняется никогда, и каждая
    загрузка главной ждала бы портал. Продукт offline-first — так нельзя.

    Промах кэша → пустой словарь СЕЙЧАС и прогрев в фоне: вызывающий честно падает на
    свой запасной расчёт, а следующий запрос уже получает настоящий курс.
    """
    category = category or default_category()
    with _lock:
        entry = _index.get(category, {"ts": 0.0, "pairs": []})
        fresh = entry["pairs"] and (time.time() - entry["ts"] < _TTL)
        pairs = list(entry["pairs"]) if fresh else []
    if not fresh:
        _warm_index_async(category)
        return {}
    out: dict[int, list] = {}
    for name, _href, course in pairs:
        out.setdefault(course, []).append(name)
    return out


def _href_for(name: str, category: str) -> str:
    """Ссылка на страницу расписания группы, включая СОСТАВНЫЕ записи индекса.

    🔥 Куплено жалобой Ярослава (04.09.2026). Портал кладёт ДВЕ группы с общим
    расписанием в ОДНУ строку — «К74/3,75.0», причём вторая часть записана без префикса
    «К». Здесь искалось ТОЧНОЕ совпадение имени, поэтому группа К75.0 не находилась
    вовсе: у её студентов расписания не было ни на сайте, ни в приложении, хотя на
    портале оно есть. Таких записей 18 из 89 — каждая пятая группа колледжа.

    ⚠️ Порядок значим: точное совпадение выигрывает ВСЕГДА. Иначе группа, чьё имя стоит
    в составной записи второй, могла бы перехватить ссылку у той, что значится
    отдельной строкой."""
    index = _load_index(category)
    for n, href, _course in index:
        if n == name:
            return href
    p = _parser()
    for n, href, _course in index:
        if "," not in n:
            continue                      # обычные записи уже проверены выше
        if name in p.expand_composite_group(n, category):
            return href
    return ""


def shared_schedule_with(name: str, category: str = "") -> list:
    """С какими группами у этой общее расписание (пусто, если своё).

    Нужно, чтобы подписать расписание честно. Без подписи студент К75.0 видит в
    заголовке «К74/3,75.0» и решает, что ему показали чужое, — ровно та жалоба, с
    которой всё началось."""
    category = category or default_category()
    try:
        index = _load_index(category)
    except Exception:
        return []
    p = _parser()
    for n, _href, _course in index:
        if "," not in n:
            continue
        names = p.expand_composite_group(n, category)
        if name in names and name != n:
            return [x for x in names[1:] if x != name]
    return []


#Расписание ПРЕПОДАВАТЕЛЯ: нужен ПОЛНЫЙ снимок (teacher_index строится инверсией всех
#групповых страниц категории — десятки-сотни GET). Поэтому ЛЕНИВО и в ФОНЕ: первый
#запрос запускает сборку потоком и сразу отвечает {building: true}; готовый снимок
#живёт _TTL. Ключ — категория (college — единственная с реальными аккаунтами
#преподавателей, остальные — просто просмотр «кто ведёт», без привязки к аккаунту).
_full: dict = {}                     # category -> {"ts", "snap", "building"}


def _full_entry(category: str) -> dict:
    return _full.setdefault(category, {"ts": 0.0, "snap": None, "building": False})


def _build_full_bg(category: str):
    try:
        p = _parser()
        snap = p.build_snapshot(category=category)
        with _lock:
            e = _full_entry(category)
            e["snap"] = snap
            e["ts"] = time.time()
    except Exception as e:
        print(f"[schedule_web] полный снимок ({category}) не собрался: {e}")
    finally:
        with _lock:
            _full_entry(category)["building"] = False


def full_state(category: str = ""):
    """(snapshot | None, building: bool). Устаревший/отсутствующий снимок запускает
    фоновую пересборку; пока она идёт, отдаём прежний снимок (если был)."""
    category = category or default_category()
    with _lock:
        e = _full_entry(category)
        snap = e["snap"]
        fresh = snap is not None and (time.time() - e["ts"] < _TTL)
        if not fresh and not e["building"]:
            e["building"] = True
            threading.Thread(target=_build_full_bg, args=(category,), daemon=True).start()
        return snap, e["building"]


def warm(category: str = ""):
    """Прогрев снимка расписания при СТАРТЕ сервера: запускаем фоновую сборку заранее,
    чтобы к первому входу преподавателя его расписание по ФИО было уже готово (иначе
    первый заход ждёт десятки секунд/до пары минут сборки полного снимка — на боевых
    данных бакалавриата это ~70с). Ничего не блокирует — full_state() лишь стартует
    фоновый поток на категорию.

    ⚠️ §3.5.5, живой баг: раньше грели ТОЛЬКО колледж (аргумент по умолчанию не
    передавался ни с одного из двух вызовов — старт сервера и вход препода), и первый
    же заход в «Расписание преподавателя» для бакалавриата/заочного стартовал
    холодную сборку РОВНО в момент, когда человек уже смотрит на пустой список — со
    стороны неотличимо от «преподов там вообще нет». Явная категория — греем только
    её (используется по колледжу отдельно, чтобы не тормозить самый частый путь входа
    четырьмя параллельными сборками); БЕЗ категории — теперь греем ВСЕ разом."""
    try:
        if category:
            full_state(category)
        else:
            from schedule import parser as p
            for key in p.CATEGORIES:
                full_state(key)
    except Exception as e:
        print(f"[schedule_web] прогрев не удался: {e}")


def match_teacher(full_name: str, names: list) -> str:
    """Ищет преподавателя портала по ФИО пользователя. На портале — «Иванов И.И.»,
    в аккаунте — «Иванов Иван Иванович»: сравниваем фамилию + инициалы."""
    fn = (full_name or "").strip().lower()
    if not fn:
        return ""
    parts = fn.split()
    surname = parts[0]
    inits = "".join(p[0] for p in parts[1:3] if p)
    for t in names:
        tp = (t or "").lower().replace(".", " ").split()
        if not tp or tp[0] != surname:
            continue
        tinits = "".join(p[0] for p in tp[1:3] if p)
        if not inits or not tinits or tinits == inits[:len(tinits)]:
            return t
    return ""


def teacher_weeks(snap, name: str) -> dict | None:
    """Недели преподавателя из teacher_index в JSON-виде: {week: {day: [пары]}};
    в каждую пару добавляем group — преподавателю важно, у кого он ведёт."""
    weeks = (snap.teacher_index or {}).get(name)
    if not weeks:
        return None
    out = {}
    for w, days in weeks.items():
        out[str(w)] = {}
        for d, entries in days.items():
            #Сортируем пары дня ПО НОМЕРУ. teacher_index строится инверсией страниц групп,
            #поэтому пары одного дня приходят в произвольном порядке (у разных групп) — без
            #сортировки в расписании препода 5-я пара оказывалась выше 4-й.
            ordered = sorted(entries, key=lambda e: getattr(e["lesson"], "pair_no", 0) or 0)
            out[str(w)][d] = [dict(e["lesson"].to_dict(), group=e["group"])
                              for e in ordered]
    return out


def invalidate_all() -> None:
    """Сбросить ВЕСЬ кэш портала (все категории): индекс групп, снимки групп и полный
    снимок.

    Нужен кнопке «Взять с ВСГУТУ» — форс-обновление основы, поверх которой лежат правки
    администратора. Полный снимок помечаем протухшим (ts=0), но НЕ дёргаем сборку здесь:
    её запустит следующий full_state() в фоне (на одноядерном VPS блокировать нельзя)."""
    with _lock:
        _index.clear()
        _groups.clear()
        _full.clear()


def get_group(name: str, category: str = "", force: bool = False) -> dict | None:
    """Снимок расписания одной группы категории (dict как GroupSchedule.to_dict) или None.

    force=True — игнорировать кэш и сходить на портал заново (кнопка «Взять с ВСГУТУ»
    для одной группы). Обычные заходы кэш используют (портал не дёргается на каждый
    просмотр)."""
    category = category or default_category()
    name = (name or "").strip()
    if not name:
        return None
    if not force:
        with _lock:
            c = _groups.get(category, {}).get(name)
            if c and time.time() - c["ts"] < _TTL:
                return c["data"]
    try:
        if force:
            _load_index(category, force=True)   #индекс групп мог устареть — обновляем и его
        href = _href_for(name, category)
        if not href:
            return None
        p = _parser()
        dated = p.CATEGORIES[category]["dated"]
        page_parser = p.parse_group_page_dated if dated else p.parse_group_page
        html = p.fetch_text(p.category_group_url(category, href))
        data = page_parser(html, name=name, href=href).to_dict()
        with _lock:
            _groups.setdefault(category, {})[name] = {"ts": time.time(), "data": data}
        return data
    except Exception:
        return None
