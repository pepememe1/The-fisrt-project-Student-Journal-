"""
main.py — Точка входа бэкенда GradeBookAI (FastAPI).

Запуск (разработка):
    cd server
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Документация API после запуска: http://localhost:8000/docs
"""
import os
from contextlib import asynccontextmanager

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

#Логгер для того, что нельзя проглотить молча. Отказ записи сигнала безопасности —
#именно такой случай: он выглядит как «всё тихо», а значит «детектор не сработал».
log = logging.getLogger("gradebook.main")

from .db import init_db
from .config import ALLOWED_ORIGINS, assert_production_secrets
from .routers import auth, sync, me, admin, web, vector, messenger, parent
from .routers import activities as activities_router
from .routers import connect as connect_router
from .routers import webauthn_router
from .routers import mfa as mfa_router
from .routers import appupdate
from .routers import desktopupdate
from .routers import publicschedule
from .routers import serverinfo
from . import canary, events, throttle


@asynccontextmanager
async def lifespan(app: FastAPI):
    #🔒 Боевые секреты — ПЕРВЫМ делом, до создания таблиц и до приёма запросов. Сервер с
    #dev-ключом подписи не должен подняться вовсе (см. config.assert_production_secrets):
    #он выглядел бы совершенно исправным, раздавая при этом любому желающему право
    #выпустить себе админский токен.
    for _w in assert_production_secrets():
        events.record("warn", "server_config", _w)
        print(f"[config] ВНИМАНИЕ: {_w}")
    init_db()   # создаём таблицы при старте
    events.record("info", "server_start", "сервер запущен")
    #Прогреваем снимок расписания в фоне, чтобы преподаватель не ждал сборку при входе.
    try:
        from . import schedule_web
        schedule_web.warm()
    except Exception:
        pass
    yield


#На бою прячем интерактивную документацию и схему API (/docs, /redoc, /openapi.json):
#анониму они раскрывают всю поверхность атаки. Признак «боя» — суженный CORS
#(GRADEBOOK_ALLOWED_ORIGINS задан, т.е. не "*"); в dev по умолчанию "*" и доки нужны.
#Явно вернуть на бою — GRADEBOOK_ENABLE_DOCS=1.
_PROD = ALLOWED_ORIGINS != ["*"]
_DOCS_ON = os.environ.get("GRADEBOOK_ENABLE_DOCS", "").strip() == "1"
_docs_kw = ({} if (_DOCS_ON or not _PROD)
            else dict(docs_url=None, redoc_url=None, openapi_url=None))
app = FastAPI(title="GradeBookAI API", version="0.1.0", lifespan=lifespan, **_docs_kw)

#CORS: список разрешённых источников берётся из настроек (GRADEBOOK_ALLOWED_ORIGINS).
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


#🔒 Базовые заголовки безопасности НА УРОВНЕ ПРИЛОЖЕНИЯ. На бою их ставит Caddy (см.
#server/Caddyfile), но в ДЕСКТОПНОЙ сборке этот же `app` поднимается локально на
#127.0.0.1 БЕЗ Caddy (desktop/local_api.py) — и тогда единственный, кто может их
#проставить, это само приложение. Ставим два самых дешёвых и важных для WebView2:
#  • X-Content-Type-Options: nosniff — WebView2/браузер не «додумывает» тип ответа по
#    содержимому (иначе картинку с HTML внутри можно заставить исполниться как страницу);
#  • X-Frame-Options: DENY — нашу страницу нельзя вложить во фрейм (защита от кликджекинга).
#⚠️ На бою Caddy заменяет эти заголовки своими ТЕМИ ЖЕ значениями (директива `header`
#без `+` перезаписывает апстримовый) — двойного заголовка не будет. `setdefault` здесь
#на случай, если конкретный ответ уже проставил своё (напр. раздача вложений может
#ставить nosniff/attachment сама) — не перетираем осознанный выбор эндпоинта.
@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.middleware("http")
async def _canary(request: Request, call_next):
    """Приманки: пути, к которым никогда не обращается никто законный.

    Разбор и требования — `docs/PLAN-HONEYPOT.md`, список путей — `app/canary.py`.

    ⚠️ Именно MIDDLEWARE, а не маршруты. Маршруты пришлось бы согласовывать с
    `_is_api_path` и SPA-фолбэком (`/.env` иначе уехал бы в отдачу страницы), а проверка
    вхождения в множество стоит O(1) и от порядка регистрации маршрутов не зависит —
    а порядок в FastAPI значим и уже стоил нам дефекта.

    ⚠️ Ответ уходит МГНОВЕННО. Ни `sleep`, ни похода в сеть здесь нет и быть не может:
    боевая машина — одно ядро и один процесс, задержка в middleware останавливает журнал
    всему колледжу. «Загрузку» рисует CSS в браузере атакующего.
    """
    ip = throttle.client_ip(request)

    left = throttle.seconds_until_unbanned(ip)
    if left:
        #Забаненному — сухой отказ без объяснений: подсказывать, за что именно и на
        #сколько, значит помогать подобрать поведение, которое не банится.
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    if canary.is_canary(request.url.path):
        #🔑 БАНИТЬ И ЗАМЕЧАТЬ — РАЗНЫЕ РЕШЕНИЯ, И ПРИНИМАЮТСЯ ОНИ ОТДЕЛЬНО.
        #Раньше запись стояла ВНУТРИ `if ban_ip(...)`, и это делало детектор слепым к
        #самому вероятному нарушителю: доверенный адрес (а в белом списке у нас сеть
        #колледжа, один NAT) не банится — и не записывался вовсе. Студент со сканером в
        #аудитории проходил приманки, а журнал безопасности молчал. Нашёл Полковник.
        throttle.ban_ip(ip, canary.BAN_SECONDS)   #щадит доверенных — так и задумано
        if canary.should_record(ip):              #а вот запись не щадит никого
            #⚠️ ЧЕРЕЗ ПУЛ ПОТОКОВ. Запись в SQLite блокирующая, а обработчик объявлен
            #`async def` (иначе не получить `await call_next`): прямой вызов остановил
            #бы весь сервер, включая журнал и /health. Инвариант куплен дефектом в
            #`vector_stt` и держится `test_event_loop_not_blocked.py`.
            try:
                await run_in_threadpool(canary.record_hit, request.method,
                                        request.url.path, ip)
            except Exception:
                #Сигнал не должен ронять ответ. Но молчать нельзя: приманка — ДЕТЕКТОР,
                #и её незаписанное срабатывание это не «мелкая неудача», а отсутствие
                #того единственного, ради чего она заведена.
                log.exception("[canary] не удалось записать срабатывание приманки")
        resp = HTMLResponse(canary.DECOY_HTML, status_code=200)
        #«Мина» в заголовках: правдоподобный хеш служебной учётки в НАШЕЙ же схеме
        #(`hybrid_sha512_gost512`). Сканер скормит его Hashcat и будет жечь GPU
        #впустую. Мы при этом НИЧЕГО не считаем: хеш декоративный (случайные байты,
        #прообраза нет), число итераций растёт по источнику (Moving Target). Разбор —
        #`canary.mine_headers`. Ответ по-прежнему уходит мгновенно.
        for _k, _v in canary.mine_headers(ip).items():
            resp.headers[_k] = _v
        return resp

    return await call_next(request)


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception):
    """Любая НЕперехваченная ошибка попадает в админскую консоль и отдаётся клиенту
    как аккуратный 500 (без утечки стек-трейса наружу). Свои HTTPException (401/403/
    409/429 и т.п.) сюда не попадают — у них собственный обработчик FastAPI."""
    try:
        events.record("error", "server_error", f"{type(exc).__name__}: {exc}",
                      ip=throttle.client_ip(request))
    except Exception:
        pass
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


import os
from fastapi.responses import FileResponse


def _find_web_dist() -> str:
    """Папка собранного САЙТА (dist), которую сервер отдаёт с ТОГО ЖЕ адреса, что и API.
    Ищем: переменная окружения → bundled server/webdist → рядом лежащий репозиторий
    веб-версии (dev-раскладка). Нет папки — сервер работает как чистый API (визитка на «/»)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../server
    root_dir = os.path.dirname(here)                                    # корень десктоп-репо
    parent = os.path.dirname(root_dir)                                  # уровень выше (напр. GB_2_7)
    #⚠️ Явно заданный GRADEBOOK_WEB_DIST — ОКОНЧАТЕЛЬНЫЙ ответ, даже если по нему ничего
    #нет. Тихо подставить вместо него какую-то другую найденную папку значит показать
    #человеку не тот сайт, который он указал, и не сказать об этом ни слова — тот же
    #принцип, что у режима распознавания речи «server» (честный отказ вместо подмены).
    forced = os.environ.get("GRADEBOOK_WEB_DIST", "").strip()
    if forced:
        return (os.path.realpath(forced)
                if os.path.isfile(os.path.join(forced, "index.html")) else "")
    candidates = [
        os.path.join(here, "webdist"),
        os.path.join(root_dir, "web", "dist"),                     # монорепо (Pre-Release-2.8+)
        os.path.join(parent, "GradeBookAI-Web-Edition", "dist"),   # старое раздельное расположение
        os.path.join(root_dir, "GradeBookAI-Web-Edition", "dist"),
    ]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "index.html")):
            return os.path.realpath(c)
    return ""


WEB_DIST = _find_web_dist()


#index.html НАМЕРЕННО без длинного кэша (в отличие от /assets/* ниже — те кэшируются
#агрессивно, потому что их имя меняется вместе с содержимым, content-hash). Сам
#index.html никогда не меняет имя — без Cache-Control браузер вправе héuristически
#счесть его свежим и не перепроверить у сервера даже после `F5`, и человек молча
#продолжает работать на СТАРОМ JS-бандле после деплоя, пока не сделает жёсткий
#reload вручную. `no-cache` — не «не кэшировать», а «кэшировать, но ВСЕГДА
#перепроверять по ETag» (дешёвый 304, а не полная перекачка).
_INDEX_HEADERS = {"Cache-Control": "no-cache"}


@app.get("/", tags=["service"], include_in_schema=False)
def root():
    #Есть собранный сайт → отдаём его (адрес сервера = адрес сайта). Нет — короткая
    #«визитка» API, чтобы при заходе в браузере было видно «сервер жив».
    if WEB_DIST:
        return FileResponse(os.path.join(WEB_DIST, "index.html"), headers=_INDEX_HEADERS)
    return JSONResponse({"service": "GradeBookAI API", "status": "ok", "docs": "/docs"})


@app.get("/health", tags=["service"])
def health():
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(sync.router)
app.include_router(me.router)
app.include_router(admin.router)
app.include_router(vector.router)
app.include_router(connect_router.router)
app.include_router(webauthn_router.router)
#Второй фактор входа. ⚠️ Идёт МИМО require_admin намеренно: иначе
#администратор без фактора не смог бы его завести — замок без двери.
app.include_router(mfa_router.router)
app.include_router(web.router)
app.include_router(parent.router)          # кабинет родителя + согласие студента (/web/parent/*)
app.include_router(messenger.router)       # мессенджер (/web/messenger/*) — до SPA-катч-олла
# Активности в беседах (/web/messenger/activities/*). ⚠️ ПОСЛЕ messenger.router: у того
# есть `/chats/{conv_id}`-подобные маршруты, но ни одного, который перехватил бы наш
# префикс, — а вот наоборот порядок важен для будущих правок. Отдельный роутер, а не
# часть messenger.py: тот уже 3300 строк (см. docs/PLAN-ACTIVITIES.md §4).
app.include_router(activities_router.router)
app.include_router(messenger.mod_router)   # модерация мессенджера (/web/admin/messenger/*)
#Расписание БЕЗ входа (/public/schedule/*) — для виджета на рабочем столе Android:
#токена у него быть не может (JWT живёт 5 часов), а данные и так публичны у портала.
#Никаких данных журнала здесь нет и быть не должно — см. предупреждение в самом файле.
app.include_router(publicschedule.router)
app.include_router(appupdate.router)   # OTA-обновления приложения (до SPA-катч-олла)
app.include_router(desktopupdate.router)   # автообновление десктопа (манифест; файлы — /downloads)
#Раздел «Сервер» — ТОЛЬКО ПРОСМОТР. Управление (SSH, команды, перенос) живёт в
#desktop/server_admin.py и подключается лишь к локальному серверу программы: на боевой
#машине этого кода нет вовсе, поэтому дырка в веб-админке не даёт оболочку на VPS.
app.include_router(serverinfo.router)


#Раздача файлов для скачивания (десктоп-клиент GradeBookAI.exe). Файлы кладём в папку
#downloads рядом с сервером (переопределяется GRADEBOOK_DOWNLOADS). Регистрируем ДО
#SPA-заглушки, чтобы /downloads/* и /desktop-info не перехватывались катч-оллом Vue.
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOADS_DIR = os.environ.get(
    "GRADEBOOK_DOWNLOADS", os.path.join(os.path.dirname(_SERVER_DIR), "downloads"))
DESKTOP_EXE = "GradeBookAI.exe"


@app.get("/desktop-info", tags=["service"])
def desktop_info():
    """Доступен ли десктоп-клиент и его размер — для кнопки скачивания на сайте.
    Пока файла нет — {available: false}, сайт покажет секцию как «готовится»."""
    path = os.path.join(DOWNLOADS_DIR, DESKTOP_EXE)
    if os.path.isfile(path):
        return {"available": True, "url": f"/downloads/{DESKTOP_EXE}",
                "size_mb": round(os.path.getsize(path) / 1048576, 1)}
    return {"available": False}


def _serve_download(relpath: str):
    """Отдать файл СТРОГО изнутри downloads (общая проверка для обоих роутов ниже)."""
    target = os.path.realpath(os.path.join(DOWNLOADS_DIR, relpath))
    #защита от path traversal: отдаём только файлы СТРОГО ВНУТРИ downloads. Сравниваем с
    #разделителем на конце (root + os.sep), иначе соседний каталог-префикс (downloads_x)
    #прошёл бы голый startswith.
    _root = os.path.realpath(DOWNLOADS_DIR)
    if (target == _root or target.startswith(_root + os.sep)) and os.path.isfile(target):
        return FileResponse(target, filename=os.path.basename(target),
                            media_type="application/octet-stream")
    return JSONResponse(status_code=404, content={"detail": "Файл не найден"})


@app.get("/downloads/updates/{fname}", include_in_schema=False)
def download_update_file(fname: str):
    """Файлы автообновления десктопа: полный .exe и дельта-патчи (см. desktop_update.py).

    ⚠️ Отдельный роут нужен потому, что `/downloads/{fname}` НЕ матчит подкаталог:
    у строкового параметра пути слэш не захватывается, и `updates/3.5.5-3.5.6.patch`
    просто не нашёлся бы — обновления «молча не качались бы». Проверка та же самая."""
    return _serve_download(os.path.join("updates", fname))


@app.get("/downloads/{fname}", include_in_schema=False)
def download_file(fname: str):
    return _serve_download(fname)


#Префиксы, за которыми живёт API. Всё, что начинается с них и не совпало ни с
#одним маршрутом, обязано получить честный 404, а не страницу сайта.
#⚠️ Список ДЕРЖАТЬ В СОГЛАСИИ с подключёнными роутерами; сторож
#`test_api_paths_never_fall_into_the_spa` проверяет, что ни один существующий
#маршрут не остался снаружи этого списка — то есть забыть префикс нельзя молча.
#🔥 «admin» и «connect» — ДВУСЕГМЕНТНЫЕ, и это куплено дефектом на бою (31.08.2026).
#Односегментные, они съедали СТРАНИЦЫ САЙТА с теми же именами: у SPA есть маршруты
#`/admin` (вся админка, тридцать разделов) и `/connect` (экран подключения при первом
#запуске). Заглушка отвечала на них честным JSON-404 — то есть приложение не грузилось
#вовсе, и до проверки роли дело не доходило. Внутри сайта переходы работали (роутинг
#клиентский, до сервера не идёт), а закладка, присланная ссылка и F5 в любом разделе
#админки давали `{"detail":"Неизвестный адрес API"}` — ВСЕМ, включая самого админа.
#Под этими именами у API заняты только вторые сегменты (см. список ниже), голые `/admin`
#и `/connect` не занимает ни один маршрут, а пересечений с адресами страниц нет.
#⚠️ Цена размена названа честно: опечатка под общим сегментом (напр. `/admin/sessionz`)
#теперь уедет в SPA и получит 200 со страницей. Отличить её от адреса страницы сервер
#не может по построению — сегмент общий. Для НАСТОЯЩИХ ручек точность сохранена, а
#забыть новый префикс молча не даст `test_no_api_route_lives_outside_the_prefix_list`.
#⚠️ «public» СТОИТ В СПИСКЕ, и это решение, а не формальность (02.09.2026). Весь
#`/public/*` принадлежит API — по нему ходит виджет из опубликованного APK, и сменить
#там адрес нечем. Пока префикса тут не было, неизвестный `/public/что-угодно` отвечал
#СТРАНИЦЕЙ с кодом 200: ровно та дыра, из-за которой тест, стучащийся в опечатанный
#адрес, зеленеет не дойдя до кода. Сторож `test_no_api_route_lives_outside_the_prefix_list`
#её не видел — в нём стояло исключение «public/» без объяснения причины; исключение снято.
#Следствие, которое надо знать: страница расписания без входа живёт на `/schedule`, и
#класть её обратно под `/public/` нельзя — этот адрес больше не отдаёт страниц.
#⚠️ «downloads» ДОБАВЛЕН 04.09.2026 — закрыт пункт 3 пентеста 3.7.8. Роуты
#`/downloads/{fname}` и `/downloads/updates/{fname}` не матчат путь глубже своего
#сегмента (у строкового параметра слэш не захватывается), поэтому `/downloads/a/b`
#проваливался в SPA-заглушку и получал 200 СО СТРАНИЦЕЙ. Утечки там не было — сама
#раздача проверяет `realpath` и отдаёт честный 404, — но 200 на несуществующий файл
#это тот же дефект, из-за которого тест, стучащийся в опечатанный адрес, зеленеет не
#дойдя до кода. Весь `/downloads/*` принадлежит серверу, страниц под ним нет.
_API_PREFIXES = (
    "auth", "me", "web", "sync", "app", "desktop", "vector", "public", "downloads",
    "health", "docs", "openapi.json", "redoc",
    "admin/online", "admin/events", "admin/sessions",
    "connect/request", "connect/requests", "connect/status",
    "connect/verify", "connect/approve", "connect/reject",
)


def _is_api_path(path: str) -> bool:
    """Начинается ли путь с API-префикса.

    ⚠️ Сравниваем СЕГМЕНТАМИ, а не подстрокой. С «me/» не совпадал бы сам «/me»
    (поймано сторожем), а с голой подстрокой «app» под API уехал бы, например,
    «/application-form» — то есть законный адрес сайта начал бы отвечать 404.
    """
    p = path.strip("/")
    return any(p == pref or p.startswith(pref + "/") for pref in _API_PREFIXES)

#САЙТ (SPA): отдаём собранный фронтенд с ТОГО ЖЕ адреса, что и API. Монтируем ПОСЛЕ
#всех API-роутеров, поэтому /auth, /web, /docs и т.п. имеют приоритет. Неизвестные
#НЕ-API пути возвращают index.html (клиентский роутинг Vue), существующие файлы
#(assets/, mascot/, favicon) — как есть. Нет dist — блок не подключается.
if WEB_DIST:
    from fastapi.staticfiles import StaticFiles

    _assets_dir = os.path.join(WEB_DIST, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        #🔥 API-ПУТИ СЮДА НЕ ПОПАДАЮТ. Заглушка обязана ловить ТОЛЬКО адреса
        #клиентского роутинга Vue. Неизвестный адрес под API-префиксом — это
        #опечатка или удалённый эндпоинт, и отвечать на него страницей нельзя:
        #  • клиент получает 200 и HTML там, где ждёт JSON, и ломается разбором
        #    вместо понятного 404. Ровно этим дефектом канал «Расписание · Группа»
        #    для группы со слэшем не открывался (см. CLAUDE.md, 3.8);
        #  • ТЕСТ, стучащийся в опечатанный адрес, получает 200 и ЗЕЛЕНЕЕТ. Поймано
        #    на себе 29.08.2026 дважды за час: проверки второго фактора ходили в
        #    «/me» и «/web/admin/users», которых не существует, и обе «проходили».
        #    Зелёный тест, не дошедший до кода, — худший вид сторожа.
        if _is_api_path(full_path):
            raise HTTPException(status_code=404, detail="Неизвестный адрес API")
        target = os.path.realpath(os.path.join(WEB_DIST, full_path))
        #защита от path traversal: отдаём файл только СТРОГО ВНУТРИ dist (root + os.sep),
        #иначе неизвестный путь → отдаём index.html (клиентский роутинг Vue).
        if (target == WEB_DIST or target.startswith(WEB_DIST + os.sep)) and os.path.isfile(target):
            return FileResponse(target)
        return FileResponse(os.path.join(WEB_DIST, "index.html"), headers=_INDEX_HEADERS)

else:
    #⚠️ САЙТ НЕ СОБРАН. Раньше этот случай не обрабатывался вовсе, и программа,
    #запущенная ИЗ ИСХОДНИКОВ без `npm run build`, открывала окно на /login и показывала
    #человеку голое `{"detail":"Not Found"}` — ни причины, ни что делать (поймано на
    #машине Влада, 3.6). В .exe этого не бывает: dist бандлится внутрь. А вот запуск из
    #исходников в свежеклонированной папке — обычное дело, и молчаливый 404 в такой
    #ситуации выглядит как «программа сломана», хотя не хватает одной команды сборки.
    _NO_DIST_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>GradeBookAI — интерфейс не собран</title>
<style>body{background:#0f1b22;color:#e8eef1;font:16px/1.6 "Segoe UI",sans-serif;
margin:0;display:grid;place-items:center;height:100vh}
.b{max-width:680px;padding:32px}h1{font-size:22px;margin:0 0 12px}
code{background:#1b2b34;padding:2px 8px;border-radius:6px;display:inline-block}
p{color:#9fb3bd}</style></head><body><div class="b">
<h1>Интерфейс ещё не собран</h1>
<p>Сервер работает, но собранного сайта рядом нет — показывать нечего.
Это не поломка: при запуске <b>из исходников</b> фронтенд нужно собрать один раз.</p>
<p><code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code></p>
<p>После сборки перезапустите программу. В готовом <code>.exe</code> этот шаг не нужен —
интерфейс уже внутри.</p>
<p>Проверить, что сервер жив: <code>/health</code></p>
</div></body></html>"""

    @app.get("/{full_path:path}", include_in_schema=False)
    def no_dist_notice(full_path: str):
        from fastapi.responses import HTMLResponse
        #503, а не 404: сервер поднят и исправен, отсутствует лишь собранный интерфейс.
        return HTMLResponse(_NO_DIST_HTML, status_code=503)
