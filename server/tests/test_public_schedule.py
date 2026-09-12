"""Расписание БЕЗ входа в аккаунт (`/public/schedule/*`).

Эндпоинт открытый — значит проверять надо не «работает ли», а ГРАНИЦУ: что он отдаёт
ровно расписание (публичное у первоисточника) и НИ ОДНОЙ строки из журнала. Соблазн
«тут же рядом отдать средний балл» появится обязательно, и держать его должен тест, а
не комментарий в файле.
"""
import pytest
from fastapi.testclient import TestClient

from app import schedule_web
from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


_SNAP = {
    "name": "К74/1", "href": "", "pair_times": [],
    "weeks": {"1": {"Пнд": [{"pair_no": 1, "time": "09:00-10:35", "kind": "лек",
                             "subject": "Математика", "teacher": "Иванов И.И.",
                             "room": "301", "raw": "", "extra": ""}]}},
}


def test_group_schedule_works_without_any_token(client, monkeypatch):
    """Главное свойство: токена нет, а расписание есть. Ради этого всё и делалось —
    виджету на рабочем столе токен взять неоткуда (JWT живёт максимум неделю)."""
    monkeypatch.setattr(schedule_web, "get_group", lambda g, c="": _SNAP)
    r = client.get("/public/schedule", params={"group": "К74/1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["group"] == "К74/1"
    assert body["schedule"]["weeks"]["1"]["Пнд"][0]["subject"] == "Математика"


def test_teacher_schedule_works_without_any_token(client, monkeypatch):
    class _Snap:
        def teachers(self):
            return ["Иванов И.И."]

    monkeypatch.setattr(schedule_web, "full_state", lambda c="": (_Snap(), False))
    monkeypatch.setattr(schedule_web, "teacher_weeks",
                        lambda snap, name: {"1": {"Пнд": []}} if name else None)
    r = client.get("/public/schedule/teacher", params={"name": "Иванов И.И."})
    assert r.status_code == 200
    assert r.json()["teacher"] == "Иванов И.И."


def test_no_group_is_honest_refusal_not_empty_schedule(client):
    """Без параметра `group` подставить «свою» неоткуда — токена нет. Отвечаем честным
    available=false, а не пустым расписанием, которое читалось бы как «пар нет»."""
    r = client.get("/public/schedule")
    assert r.status_code == 200
    assert r.json()["available"] is False
    assert r.json()["schedule"] is None


def test_week_parity_endpoint_returns_date_it_was_computed_for(client):
    """Дату возвращаем вместе с чётностью: без неё при расхождении часовых поясов не
    разобрать, чья «сегодня» имелась в виду."""
    r = client.get("/public/week")
    assert r.status_code == 200
    assert r.json()["week"] in (1, 2)
    assert len(r.json()["date"]) == 10


def test_public_router_exposes_only_schedule(client):
    """🔒 ГРАНИЦА. Под /public не должно быть ничего, кроме расписания и чётности недели.

    Проверяем не текст файла, а РЕАЛЬНО зарегистрированные маршруты приложения — иначе
    достаточно было бы дописать роут в другом файле с тем же префиксом, и тест бы этого
    не заметил."""
    paths = set()
    for r in app.routes:
        p = getattr(r, "path", "")
        if p.startswith("/public"):
            paths.add(p)
        #Подключённые роутеры в этой версии FastAPI лежат обёрткой без .path —
        #настоящие маршруты внутри original_router (тот же приём, что в §16).
        inner = getattr(r, "original_router", None)
        for ir in getattr(inner, "routes", []) or []:
            ip = getattr(ir, "path", "")
            if ip.startswith("/public"):
                paths.add(ip)
    #⚠️ Список ЗАКРЫТЫЙ, и краснеет он на каждом добавлении НАМЕРЕННО. Обычно сторож со
    #снимком значения — наша же грабля (он подталкивает «просто обновить ожидание»), но
    #здесь снимок и есть предмет проверки: всё, что живёт под /public, доступно БЕЗ
    #ТОКЕНА, и расширение этого множества обязано быть отдельным решением человека, а не
    #побочным следствием правки в соседнем файле.
    #Категории и список групп добавлены 12.09.2026: это оглавление ПУБЛИЧНОГО портала
    #(какие бывают категории и какие в них группы), а не данные вуза о людях.
    assert paths == {"/public/schedule", "/public/schedule/categories",
                     "/public/schedule/groups", "/public/schedule/teacher",
                     "/public/week"}, paths


@pytest.mark.parametrize("path,params", [
    ("/public/schedule", {"group": "К74/1"}),
    ("/public/schedule/teacher", {"name": "Иванов И.И."}),
    #Новые двери проходят ТУ ЖЕ проверку: граница принадлежит префиксу, а не отдельной
    #ручке, и первая же не внесённая сюда — это и есть «первое забытое место».
    ("/public/schedule/categories", {}),
    ("/public/schedule/groups", {"category": "college"}),
])
def test_public_answers_never_contain_journal_fields(client, monkeypatch, path, params):
    """🔒 В ответе не должно быть ни одного признака журнала: оценок, посещаемости,
    среднего балла, ЗЕТ, риска отчисления, списков студентов."""
    monkeypatch.setattr(schedule_web, "get_group", lambda g, c="": _SNAP)
    monkeypatch.setattr(schedule_web, "full_state", lambda c="": (None, False))
    body = client.get(path, params=params).text.lower()
    for forbidden in ("average", "средний", "grade", "оценк", "absenc", "пропуск",
                      "zet", "risk", "student_id", "долг"):
        assert forbidden not in body, f"{path}: в публичном ответе оказалось «{forbidden}»"


def test_rate_limit_answers_429_not_403(client, monkeypatch):
    """Превышение частоты — это 429, а не 403: клиенту надо понять, что дело во
    ВРЕМЕНИ и повторить позже, а не решить, что доступ закрыт навсегда."""
    from app.routers import publicschedule

    monkeypatch.setattr(publicschedule, "_too_many", lambda request: True)
    r = client.get("/public/schedule", params={"group": "К74/1"})
    assert r.status_code == 429


# ─────────────────────────────────────────────────────────────────────────────
# Страница и ручка по одному адресу (02.09.2026)
#
# 🔥 Дефект: страница «расписание без входа» была заведена на `/public/schedule` —
# адресе, который на сервере уже занят ЭТОЙ ручкой. Переход по ссылке внутри сайта
# работал (роутинг клиентский, до сервера не идёт), а прямой заход, F5 и присланная
# ссылка отдавали голый JSON. То есть страница была недостижима ровно в том случае,
# ради которого её и завели: человека выбросило из аккаунта, он открывает адрес.
# Освободить адрес нельзя — по нему ходит виджет из ОПУБЛИКОВАННОГО APK.
# ─────────────────────────────────────────────────────────────────────────────

_NAV = {"Sec-Fetch-Mode": "navigate"}


def test_person_opening_the_address_in_a_browser_gets_the_page_not_json(client):
    """Главное свойство задачи: человек, открывший адрес, попадает на страницу."""
    r = client.get("/public/schedule", headers=_NAV, follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/schedule"


def test_a_shared_link_keeps_the_group_it_was_shared_with(client):
    """Ссылку присылают со СВОЕЙ группой («вот расписание К74/1»). Потеряв её при
    переадресации, страница показала бы группу из памяти открывшего — то есть уверенно
    ответила бы не на тот вопрос."""
    r = client.get("/public/schedule", params={"group": "К74/1"},
                   headers=_NAV, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/schedule?group=%D0%9A74/1"


def test_the_android_widget_still_gets_json(client, monkeypatch):
    """ОБРАТНЫЙ ХОД, и он здесь важнее прямого: виджет живёт в уже опубликованном APK,
    и увести его на страницу значит сломать то, что работает, без возможности починить
    иначе как перезаливом в RuStore.

    ⚠️ Заголовки взяты НАСТОЯЩИЕ, как их шлёт `ScheduleWidgetRefresh.java`, вместе с
    дефолтным `Accept` Java-клиента — он начинается с `text/html`, и проверка «по
    Accept» приняла бы виджет за браузер. Ровно поэтому смотрим `Sec-Fetch-Mode`.
    """
    monkeypatch.setattr(schedule_web, "get_group", lambda g, c="": _SNAP)
    r = client.get("/public/schedule", params={"group": "К74/1"}, headers={
        "Accept": "text/html, image/gif, image/jpeg, *; q=.2, */*; q=.2",
        "X-Client": "android-widget",
    }, follow_redirects=False)
    assert r.status_code == 200, r.text
    assert r.json()["available"] is True


def test_the_spa_fetching_data_is_not_redirected(client, monkeypatch):
    """У fetch/XHR из самой страницы `Sec-Fetch-Mode` равен `cors`/`same-origin`.
    Переадресуй мы и его — страница получила бы HTML вместо данных и показала бы
    «не удалось получить расписание» на исправном сервере."""
    monkeypatch.setattr(schedule_web, "get_group", lambda g, c="": _SNAP)
    for mode in ("cors", "same-origin", "no-cors"):
        r = client.get("/public/schedule", params={"group": "К74/1"},
                       headers={"Sec-Fetch-Mode": mode}, follow_redirects=False)
        assert r.status_code == 200, mode
        assert r.json()["available"] is True, mode


def test_an_unknown_public_address_is_an_honest_404_not_a_page(client):
    """Пока «public» не стоял в списке API-префиксов, ЛЮБОЙ неизвестный `/public/*`
    отвечал страницей с кодом 200. Цена такой дыры не в вежливости ответа: тест,
    стучащийся в опечатанный адрес, зеленеет НЕ ДОЙДЯ до кода — этим уже дважды
    ловились проверки второго фактора (см. CLAUDE.md, 29.08.2026)."""
    r = client.get("/public/net-takogo-adresa")
    assert r.status_code == 404, r.text
    assert "text/html" not in r.headers.get("content-type", "")


def test_the_page_address_is_not_taken_by_any_api_route():
    """Страница и ручка не имеют права делить URL — кто выиграет, решает порядок
    подключения роутеров, а не замысел. Сторож смотрит на ПРОДУКТ: если однажды
    заведут `GET /schedule` на сервере, страница расписания молча исчезнет снова."""
    from app.main import app as real_app
    from app.routers.publicschedule import PAGE_URL

    from test_spa_fallback import _all_paths

    assert PAGE_URL not in set(_all_paths(real_app))


# ─────────────────────────────────────────────────────────────────────────────
# Выбор группы без входа (12.09.2026)
#
# 🔥 До этого страница спрашивала название группы ТЕКСТОМ и молча требовала знать его
# точно. Человек, вылетевший из аккаунта, как раз торопится — он открывает страницу
# посмотреть пары, а не вспоминать, пишется его группа «К74/1» или «К-74/1».
# ─────────────────────────────────────────────────────────────────────────────


def test_categories_are_available_without_any_token(client):
    """Кнопки категорий на публичной странице берутся отсюда. Форма ответа ДОСЛОВНО
    совпадает с кабинетной `/web/schedule/categories` — обе страницы разбирают её одним
    кодом, второй формат разошёлся бы с первым на первой же правке реестра."""
    r = client.get("/public/schedule/categories")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default"]
    assert isinstance(body["categories"], list) and body["categories"]
    for c in body["categories"]:
        assert set(c) == {"key", "label", "dated"}


def test_group_list_is_available_without_any_token(client, monkeypatch):
    #⚠️ Подменяем ЕДИНСТВЕННЫЙ источник — `_load_index`, а не производные функции. Подмена
    #производной оставляет живой путь к порталу: тест уходит в сеть, идёт секунды и
    #краснеет от чужого сбоя. Ровно это здесь и случилось в первый прогон.
    monkeypatch.setattr(schedule_web, "_load_index",
                        lambda c="", force=False: [("К74/1", "h", 3), ("К75.0", "h", 4)])
    r = client.get("/public/schedule/groups", params={"category": "college"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["groups"] == ["К74/1", "К75.0"]
    #Курс разведан по столбцу индекса портала и НЕ фиксирован на четырёх — по нему
    #страница рисует выбор курса.
    assert body["by_course"] == {"3": ["К74/1"], "4": ["К75.0"]}


def test_the_public_group_list_has_the_same_shape_as_the_one_behind_login(client, monkeypatch):
    """🔑 Ключи ответа обязаны совпасть с кабинетной ручкой: страницы разбирают их одним
    кодом. Расхождение здесь тихое — клиент прочитает отсутствующее поле и получит
    `undefined`, то есть пустой список вместо групп (ровно тот класс дефекта, из-за
    которого публичная страница не показывала расписание вовсе)."""
    from app.routers.web import schedule as web_schedule

    monkeypatch.setattr(schedule_web, "_load_index", lambda c="", force=False: [("К74/1", "h", 3)])
    public = set(client.get("/public/schedule/groups").json())
    behind_login = set(web_schedule.schedule_groups(category="college", user=None))
    assert public == behind_login, (public, behind_login)


def test_portal_silence_gives_an_empty_list_not_a_crash(client, monkeypatch):
    """Оффлайн и сбой портала — обычное состояние, а не авария: страница обязана суметь
    сказать «список недоступен», а не получить 500. Пустой список при этом НЕ значит
    «групп нет» — так его и объясняет страница."""
    def _boom(*a, **kw):
        raise RuntimeError("портал молчит")

    monkeypatch.setattr(schedule_web, "_load_index", _boom)
    r = client.get("/public/schedule/groups", params={"category": "college"})
    assert r.status_code == 200, r.text
    assert r.json()["groups"] == []
    assert r.json()["by_course"] == {}


@pytest.mark.parametrize("path", ["/public/schedule/categories", "/public/schedule/groups"])
def test_new_public_doors_are_rate_limited_too(client, monkeypatch, path):
    """Предел частоты — свойство ПРЕФИКСА, а не одной ручки. Забытая дверь даёт
    бесплатный способ гонять парсер портала в цикле."""
    from app.routers import publicschedule

    monkeypatch.setattr(publicschedule, "_too_many", lambda request: True)
    assert client.get(path).status_code == 429


def test_the_public_group_list_never_exposes_the_staff_directory(client, monkeypatch):
    """⚠️ Список ГРУПП — публичное оглавление портала; список ПРЕПОДАВАТЕЛЕЙ — уже нет.
    Кабинетная ручка расписания преподавателя отдаёт `teachers` для выпадающего списка,
    и соблазн сделать так же здесь появится обязательно."""
    monkeypatch.setattr(schedule_web, "_load_index", lambda c="", force=False: [("К74/1", "h", 3)])
    assert "teachers" not in client.get("/public/schedule/groups").json()

    class _Snap:
        def teachers(self):
            return ["Иванов И.И.", "Петрова А.А."]

    monkeypatch.setattr(schedule_web, "full_state", lambda c="": (_Snap(), False))
    monkeypatch.setattr(schedule_web, "teacher_weeks", lambda snap, name: {"1": {}})
    assert "teachers" not in client.get("/public/schedule/teacher",
                                        params={"name": "Иванов И.И."}).json()


# ─────────────────────────────────────────────────────────────────────────────
# Составные записи индекса в списке выбора (12.09.2026)
#
# 🔥 Портал кладёт две группы с общим расписанием в ОДНУ строку: «К74/3,75.0». На живом
# индексе таких 18 из 89 — каждая пятая группа колледжа. Ссылку для них починили
# 04.09.2026 (`_href_for`), а СПИСОК остался прежним: у К75.0 не было своей кнопки, её
# студент видел чужое имя и решал, что группы здесь нет.
# ─────────────────────────────────────────────────────────────────────────────

#Живые строки индекса (снято с портала 12.09.2026), а не выдуманные: правило целиком про
#то, как портал записывает РЕАЛЬНЫЕ группы.
_INDEX_LIVE = [
    ("К16", "h1", 1),
    ("К14,15.0", "h2", 2),
    ("К73/1,74.01", "h3", 3),
    ("К24/25.0", "h4", 4),
]


def test_composite_index_rows_become_separate_groups(client, monkeypatch):
    monkeypatch.setattr(schedule_web, "_load_index", lambda c="", force=False: _INDEX_LIVE)
    body = client.get("/public/schedule/groups", params={"category": "college"}).json()
    for want in ("К16", "К14", "К15.0", "К73/1", "К74.01"):
        assert want in body["groups"], f"{want} пропала из списка: {body['groups']}"


def test_the_composite_row_itself_never_becomes_a_button(client, monkeypatch):
    """«К74/3,75.0» — не группа, а строка индекса. Кнопка с таким именем предлагает
    выбрать то, чего не существует."""
    monkeypatch.setattr(schedule_web, "_load_index", lambda c="", force=False: _INDEX_LIVE)
    body = client.get("/public/schedule/groups", params={"category": "college"}).json()
    assert [g for g in body["groups"] if "," in g] == [], body["groups"]


def test_a_part_of_a_composite_row_keeps_the_course_of_that_row(client, monkeypatch):
    """Курс разведан по столбцу индекса, а строка у обеих групп одна — значит и курс
    общий. «Курс неизвестен» было бы хуже: группа выпала бы из выбора по курсу."""
    monkeypatch.setattr(schedule_web, "_load_index", lambda c="", force=False: _INDEX_LIVE)
    by_course = client.get("/public/schedule/groups",
                           params={"category": "college"}).json()["by_course"]
    assert "К15.0" in by_course["2"], by_course
    assert "К74.01" in by_course["3"], by_course


def test_every_offered_group_can_actually_be_opened(client, monkeypatch):
    """🔑 СВОЙСТВО, а не перечисление: КАЖДОЕ имя из списка обязано резолвиться в ссылку
    расписания. Список, предлагающий группу, которая не открывается, — худший исход:
    человек нажимает и получает «группа не найдена», то есть мы сами его обманули."""
    monkeypatch.setattr(schedule_web, "_load_index", lambda c="", force=False: _INDEX_LIVE)
    names = client.get("/public/schedule/groups",
                       params={"category": "college"}).json()["groups"]
    assert names
    for n in names:
        assert schedule_web._href_for(n, "college"), f"группа {n} предложена, но не открывается"


def test_categories_without_a_prefix_are_not_split(client, monkeypatch):
    """⚠️ У бакалавриата запятая значит ДРУГОЕ («316-1,2 ЭиЭ» — это 316-1 ЭиЭ и 316-2
    ЭиЭ, общий хвост специальности), и одного правила на обе категории нет. Разбирать их
    тем же приёмом значит СОЧИНЯТЬ имена: выдуманная группа ни с чем не совпадёт, но в
    выдаче появится и будет выглядеть настоящей. Лучше не найти, чем найти не то."""
    rows = [("545 исит,235", "h", 2)]
    monkeypatch.setattr(schedule_web, "_load_index", lambda c="", force=False: rows)
    body = client.get("/public/schedule/groups", params={"category": "bakalavriat"}).json()
    assert body["groups"] == ["545 исит,235"], body["groups"]


def test_the_dashboard_path_is_left_exactly_as_it_was(client, monkeypatch):
    """🔒 ГРАНИЦА ЗАДАЧИ. `list_groups`/`groups_by_course` кормят кабинет, админку и —
    через `groups_by_course_cached` — КУРС СТУДЕНТА на главной странице. Развернуть
    составные записи там значило бы молча изменить поведение путей, о которых эта задача
    не просила, причём на самой горячей странице продукта. Развёртка живёт в ОТДЕЛЬНЫХ
    функциях; этот тест краснеет, если их однажды «объединят для порядка»."""
    monkeypatch.setattr(schedule_web, "_load_index", lambda c="", force=False: _INDEX_LIVE)
    assert schedule_web.list_groups("college") == ["К16", "К14,15.0", "К73/1,74.01", "К24/25.0"]
    assert "К15.0" not in schedule_web.list_groups("college")
    #А в списке выбора — наоборот.
    assert "К15.0" in schedule_web.list_groups_expanded("college")


def test_portal_silence_still_gives_an_empty_expanded_list(client, monkeypatch):
    """Деградация у развёрнутых списков та же, что у обычных: пусто, а не исключение."""
    def _boom(*a, **kw):
        raise RuntimeError("портал молчит")

    monkeypatch.setattr(schedule_web, "_load_index", _boom)
    assert schedule_web.list_groups_expanded("college") == []
    assert schedule_web.groups_by_course_expanded("college") == {}
