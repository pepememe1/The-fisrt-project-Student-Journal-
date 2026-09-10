"""
me.py — Личные настройки текущего пользователя (self-scope).

Зачем отдельный эндпоинт, а не общий /sync/push: обобщённый push ограничен по роли
(PUSH_SCOPE — ученик не пушит ничего, преподаватель только оценки/занятия). Чтобы
персональная тема оформления «роумилась» через БД, пользователю нужно уметь менять
СВОЮ строку — но строго свою и только поле prefs (пароль/роль/чужие записи не
трогаются). Это и делает POST /me/prefs: личность берётся из JWT (get_current_user),
поэтому подменить чужой профиль нельзя. Права ролей при этом не ослабляем.

Метку updated_at ставит сервер — как и в /sync/push, чтобы LWW не зависел от часов
клиента, и обновлённый prefs уехал другим устройствам этого же пользователя на pull.
"""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import retention as _retention
from .. import rustore_push
from ..config import OFFLINE_GRACE_MIN
from ..db import get_db
from ..deps import get_current_user
from ..models import NotifyEvent, PushToken, User

router = APIRouter(prefix="/me", tags=["me"])

#Личные настройки — это тема оформления и мелкие флаги. Ограничиваем размер, чтобы
#авторизованный пользователь не «раздул» свою строку произвольным JSON (защита БД).
#🔥 Было 16 КБ, и этого НЕ ХВАТАЛО на собственную аватарку. Замерено, а не прикинуто:
#AvatarCropper отдаёт 256×256 JPEG q0.85, у фотографии это ~14.5 КБ, в base64 внутри
#data:URL — столько же плюс треть. Вместе с «О себе», настройками уведомлений и
#избранными гифками сумма переваливала за 16 КБ, и сервер отвечал 413 — то есть человек
#с ФОТОГРАФИЕЙ на аватарке не мог её сохранить вовсе, а с однотонной картинкой (2 КБ)
#мог. Отсюда и «то работает, то нет» без всякой закономерности на глаз.
#64 КБ — с запасом на аватарку плюс всё остальное; строка prefs у большинства
#пользователей и близко к этому не подходит (гифка-аватарка вообще ~100 байт, это ссылка).
_MAX_PREFS_BYTES = 64 * 1024

#ПУБЛИЧНЫЕ поля профиля (их видят другие пользователи в карточке — см. messenger._safe_user),
#поэтому режем их на СЕРВЕРЕ, а не только в UI: клиента можно обойти запросом напрямую.
_MAX_BIO_CHARS = 400        #«О себе» — тот же лимит показывает счётчик в интерфейсе
_MAX_COLOR_ID = 32          #id пресета палитры ('violet', 'teal', …)
#Языки интерфейса. Коды те же, что в web/src/i18n/dictionaries.js.
_UI_LOCALES = ("ru", "en", "zh")

#Стиль никнейма (§5.4, «стиль никнейма» — Discord-style профиль) — id должен совпадать
#1:1 с web/src/config/nameFonts.js (там же — font-family на каждый id) и с @font-face в
#web/src/style.css. '' — «без стиля» (обычный шрифт интерфейса, а не отсутствие имени).
#Список ИМЕННО здесь (не в messenger.py) по тому же принципу, что и _UI_LOCALES: сюда
#приходит поле от авторизованного клиента при сохранении, чужой id означал бы CSS
#font-family на несуществующий шрифт — фолбэк браузера отработал бы тихо, но проверка
#на входе дешевле, чем гадать потом, откуда в БД мусор.
NAME_FONTS = (
    "", "unbounded", "comfortaa", "oswald", "ptserif", "ptmono", "yeseva",
    "caveat", "marckscript", "pacifico", "lobster",
    "russo", "tektur", "ruslan", "pixel",
    "glitch", "moonrocks", "bubbles", "wetpaint",
)

#ЭФФЕКТ имени (3.7, просьба Влада) — тот же принцип, что у NAME_FONTS: публичное поле,
#из которого клиент склеивает имя CSS-класса (.gb-nfx-<id> в web/src/style.css), поэтому
#произвольную строку сюда не пускаем. Список-близнец — web/src/config/nameEffects.js.
NAME_EFFECTS = ("", "solid", "gradient", "rainbow", "shine", "neon", "outline", "highlight")

#ЦВЕТ имени — НЕ отдельная палитра, а те же id пресетов, что и `profile_color`
#(web/src/theme/palette.js::PRESETS — единственная палитра, оставшаяся в продукте;
#`ui/themes.py` удалён вместе с Qt). '' = «как цвет профиля»; проверяем только длину,
#как у profile_color, — держать здесь копию списка из 16 названий значило бы завести
#второй источник правды, который однажды разъедется с первым.


def _sanitize_public_profile(prefs: dict) -> None:
    """Обрезает публичные поля профиля на месте.

    Список цветов НЕ дублируем на сервере: он живёт в палитре клиента
    (web/src/theme/palette.js). Здесь ограничиваем только длину, а
    неизвестный id клиент сам отобразит стандартным акцентом — так нет второго
    источника правды, который мог бы разъехаться с первым."""
    if "bio" in prefs:
        bio = prefs.get("bio")
        prefs["bio"] = (bio if isinstance(bio, str) else "").strip()[:_MAX_BIO_CHARS]
    if "profile_color" in prefs:
        col = prefs.get("profile_color")
        prefs["profile_color"] = (col if isinstance(col, str) else "").strip()[:_MAX_COLOR_ID]


def _sanitize_name_font(prefs: dict) -> None:
    """Стиль никнейма — та же логика, что и у цвета плашки (_sanitize_public_profile):
    ПУБЛИЧНЫЕ поля (другие видят их в мессенджере и в карточке профиля, см.
    messenger._safe_user), поэтому проверяем на сервере, а не только в UI.

    Три поля разом, потому что они и настраиваются одним диалогом, и портятся одинаково:
    шрифт и эффект — строго из списка (клиент превращает их в font-family и в имя
    CSS-класса), цвет — id пресета палитры, для него, как и для profile_color, режем
    только длину (список цветов живёт у клиента, второй копии здесь не заводим)."""
    if "name_font" in prefs:
        val = prefs.get("name_font")
        prefs["name_font"] = val if val in NAME_FONTS else ""
    if "name_effect" in prefs:
        val = prefs.get("name_effect")
        prefs["name_effect"] = val if val in NAME_EFFECTS else ""
    if "name_color" in prefs:
        col = prefs.get("name_color")
        prefs["name_color"] = (col if isinstance(col, str) else "").strip()[:_MAX_COLOR_ID]


#Медиа профиля. Аватарка бывает ДВУХ видов, и это осознанно одно поле, а не два:
#обрезанная своя картинка (data:URL) ИЛИ гифка с CDN Klipy (ссылка). Всё, что рисует
#аватарку — от списка чатов до модерации, — просто подставляет значение в `<img src>`,
#и разделение на два поля заставило бы править каждое такое место.
#
#🔒 ТИПЫ ПЕРЕЧИСЛЕНЫ ПОИМЁННО, А НЕ `data:image/` ЦЕЛИКОМ (09.09.2026, замечание из
#внешнего разбора). Прежний префикс пропускал `data:image/svg+xml`, а SVG — это документ
#со скриптами внутри, а не растр.
#⚠️ Названо честно: живой кражи токена тут НЕ БЫЛО и разбор в этом месте ошибается.
#Браузер рисует SVG из `<img src>` в защищённом статическом режиме — ни скрипты, ни
#внешние ссылки внутри него не исполняются, а переход по `data:`-адресу верхним уровнем
#браузеры запрещают отдельно. То есть дыра держалась закрытой КОНТЕКСТОМ ОТРИСОВКИ, а не
#нашей проверкой. Это и есть причина сузить список: контекст задаёт клиент, и первый же
#`v-html`, `<object>` или `background-image` со значением этого поля превратил бы чужой
#SVG в исполняемый код — а помнить об этом придётся тому, кто через год будет править
#вёрстку карточки и никакой связи с проверкой на сервере не увидит.
#⚠️ Список ШИРЕ того, что делает продукт (`AvatarCropper.vue` отдаёт только JPEG), и это
#не забывчивость: проверка стоит на пути ЛЮБОЙ правки настроек, и значение подставляется
#в тот же словарь. Сузив её до одного JPEG, мы бы гасили в пустоту аватарки, лежащие в
#боевой базе с прежних версий, — человек менял бы тему и молча терял картинку.
_AVATAR_DATA_PREFIXES = (
    "data:image/jpeg;", "data:image/jpg;", "data:image/png;",
    "data:image/webp;", "data:image/gif;",
)


def _sanitize_profile_media(prefs: dict) -> None:
    """Аватарка и баннер профиля — ПУБЛИЧНЫЕ поля, которые клиент подставляет в `<img
    src>` у ВСЕХ, кто открыл карточку. Значит источник картинки обязан проверяться на
    сервере, а не только в UI.

    До появления гифок поле `avatar` не проверялось вовсе: туда годилась любая строка.
    Пока это была наша же обрезанная картинка, вреда не было, но чужая ссылка означала
    бы, что каждый открывший профиль молча сходил на посторонний хост и отдал ему свой
    IP и User-Agent — то есть один человек включал бы слежку за всеми, кто на него
    посмотрел. На бою это ловил CSP Caddy (`img-src 'self' data: static.klipy.com`), но
    CSP — заслонка браузера, а не правило продукта: внутри программы страницу отдаёт
    ЛОКАЛЬНЫЙ сервер, у которого этого заголовка нет.

    Отсюда белый список: своя картинка РАСТРОВОГО типа (`_AVATAR_DATA_PREFIXES`, см.
    комментарий выше — SVG туда не входит) либо CDN Klipy — тот же
    `is_allowed_url`, что уже стоит на GIF-сообщениях и на избранном, второй копии
    правила не заводим. Баннеру data:URL не разрешён СПЕЦИАЛЬНО: своей картинки для
    него нет ни в одном интерфейсе, а полоса во всю ширину карточки съела бы лимит
    настроек целиком.

    Непрошедшее значение ГАСИМ в пустоту, а не режем: обрезанный до половины data:URL —
    это битая картинка, которая выглядит как поломка продукта, а пустое поле честно
    показывает буквы имени."""
    from .. import gif_service
    if "avatar" in prefs:
        val = prefs.get("avatar")
        val = val.strip() if isinstance(val, str) else ""
        ok = val.startswith(_AVATAR_DATA_PREFIXES) or gif_service.is_allowed_url(val)
        prefs["avatar"] = val if ok else ""
    if "profile_banner" in prefs:
        val = prefs.get("profile_banner")
        val = val.strip() if isinstance(val, str) else ""
        prefs["profile_banner"] = val if gif_service.is_allowed_url(val) else ""


def _sanitize_notify(prefs: dict) -> None:
    """Приводит `prefs.notify` к строгому виду: только известные категории, только «да/нет».

    Сюда приходит произвольный JSON от авторизованного клиента, а читает его СЕРВЕР при
    каждой отправке пуша (`rustore_push.category_enabled`). Мусор в этом поле — это не
    «некрасиво», а неверное решение о доставке: строка "false" истинна в Python, и
    выключенная в интерфейсе категория продолжала бы приходить.

    Незнакомые ключи выбрасываем: список категорий задаёт СЕРВЕР, иначе клиент мог бы
    насорить в поле, которое сам же не читает."""
    if "notify" not in prefs:
        return
    box = prefs.get("notify")
    if not isinstance(box, dict):
        prefs.pop("notify", None)
        return
    prefs["notify"] = {k: bool(v) for k, v in box.items()
                       if k in rustore_push.ALL_CATEGORIES}


def _sanitize_translate(prefs: dict) -> None:
    """Приводит `prefs.translate` к строгому виду: только известные языки и «да/нет».

    Это поле читает СЕРВЕР (коды языков уходят в промпт переводчика), поэтому мусор
    здесь — не косметика: незнакомый код языка попал бы прямо в инструкцию модели."""
    if "translate" not in prefs:
        return
    from .. import translate_service
    cleaned = translate_service.sanitize_prefs(prefs.get("translate"))
    if cleaned:
        prefs["translate"] = cleaned
    else:
        prefs.pop("translate", None)


def _sanitize_locale(prefs: dict) -> None:
    """Язык интерфейса: только известные коды, флаг перевода — строго «да/нет».

    Значение читает КЛИЕНТ, но приходит оно от него же, поэтому чистим на входе: чужой
    код языка означал бы интерфейс, у которого нет словаря, — то есть пустые подписи.
    Список языков держит фронт (`web/src/i18n/dictionaries.js`), здесь только его коды:
    заводить второй список на сервере значит однажды их разъехать."""
    if "locale" in prefs:
        code = str(prefs.get("locale") or "").strip().lower()
        if code in _UI_LOCALES:
            prefs["locale"] = code
        else:
            prefs.pop("locale", None)
    if "locale_on" in prefs:
        prefs["locale_on"] = bool(prefs.get("locale_on"))


def _sanitize_gif_favorites(prefs: dict) -> None:
    """Приводит `prefs.gif_favorites` к строгому виду: только записи, чьи ссылки реально
    ведут на CDN Klipy (та же проверка, что при отправке GIF-сообщения) — иначе в
    список «Избранного» можно было бы протащить произвольный URL. Отдельного счётчика
    штук нет: общий лимит размера prefs (_MAX_PREFS_BYTES) и так режет список."""
    if "gif_favorites" not in prefs:
        return
    from .. import gif_service
    raw = prefs.get("gif_favorites")
    cleaned = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            thumb = str(item.get("thumb_url") or "")
            if not gif_service.is_allowed_url(url) or not gif_service.is_allowed_url(thumb):
                continue
            try:
                width, height = int(item.get("width") or 0), int(item.get("height") or 0)
            except (TypeError, ValueError):
                width, height = 0, 0
            cleaned.append({
                "id": item.get("id"), "slug": str(item.get("slug") or "")[:200],
                "title": str(item.get("title") or "")[:200],
                "thumb_url": thumb, "url": url, "width": width, "height": height,
            })
    if cleaned:
        prefs["gif_favorites"] = cleaned
    else:
        prefs.pop("gif_favorites", None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/prefs")
def get_prefs(user: User = Depends(get_current_user)):
    """Текущие личные настройки вошедшего пользователя.

    ⚠️ Отдаём ЕЩЁ И собственный id, хотя это не настройка. Причина: клиент своего id не
    знает вовсе — в JWT и в «визитке» после входа лежат только логин, роль и ФИО. Пока
    это было незаметно, потому что почти везде id и не нужен: сервер сам понимает, кто
    спрашивает, по токену. Но эндпоинты, устроенные как «действие ПРО человека», берут
    цель ПУТЁМ (`/web/messenger/users/{id}/note`), и для действия про самого себя
    подставить туда было нечего — личная заметка на своей же карточке профиля молча не
    сохранялась (запрос вообще не уходил). Здесь это дешевле всего: страница профиля и
    так дёргает prefs при открытии, а значит правка чинит и уже выданные сессии — не
    требуя от всего колледжа перезайти, как потребовала бы добавка id в ответ входа.

    Здесь же приезжает `offline_grace_min` — сколько минут приложение вправе работать,
    ни разу не дозвонившись до сервера. Это не личная настройка, а ПОЛИТИКА, и её
    место на сервере: иначе цифра жила бы только в собранном APK, и поменять её без
    перевыпуска приложения было бы нельзя. Клиент проверяет окно сам (сервера в этот
    момент по определению нет), но правило получает отсюда."""
    #День рождения («ДД.ММ») отдаём ЗДЕСЬ, а не в prefs: его задаёт АДМИН, а prefs —
    #это то, что человек настраивает сам. Клиенту он нужен на своей же карточке, а
    #страница профиля и так дёргает этот запрос при открытии.
    return {"prefs": user.prefs or {}, "user_id": user.id,
            "birthday": user.birthday or "",
            "offline_grace_min": OFFLINE_GRACE_MIN}


@router.post("/prefs")
def set_prefs(payload: dict = Body(...), user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Сливает переданные ключи в prefs ТЕКУЩЕГО пользователя (только своя строка).

    Принимаем либо {"prefs": {...}}, либо сразу плоский словарь — обе формы удобны
    клиенту. Существующие ключи prefs сохраняются, новые перекрывают одноимённые."""
    incoming = payload.get("prefs") if isinstance(payload.get("prefs"), dict) else payload
    if not isinstance(incoming, dict):
        incoming = {}
    merged = dict(user.prefs or {})
    merged.update(incoming)
    _sanitize_public_profile(merged)     #«О себе» и цвет плашки видны другим — режем здесь
    _sanitize_profile_media(merged)      #аватарка и баннер: своя картинка либо CDN Klipy
    _sanitize_name_font(merged)          #шрифт/эффект/цвет имени — видны другим, только из списка
    _sanitize_notify(merged)             #категории уведомлений читает сервер — приводим к «да/нет»
    _sanitize_translate(merged)          #коды языков уходят в промпт — только известные
    _sanitize_locale(merged)             #язык интерфейса — только тот, для которого есть словарь
    _sanitize_gif_favorites(merged)      #только реальные ссылки на CDN Klipy
    #Лимит на размер настроек: не даём авторизованному пользователю раздувать БД.
    try:
        size = len(json.dumps(merged, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректные настройки") from None
    if size > _MAX_PREFS_BYTES:
        raise HTTPException(status_code=413, detail="Слишком большой объём настроек")
    user.prefs = merged
    #JSON-столбец меняем «по месту» — подсказываем ORM, что поле грязное, иначе
    #переприсваивание того же объекта может не попасть в UPDATE.
    flag_modified(user, "prefs")
    user.updated_at = _now()
    db.commit()
    return {"ok": True, "prefs": merged, "updated_at": user.updated_at}


#Пуш-уведомления (RuStore Push)
#Токен привязан к КОНКРЕТНОМУ телефону, поэтому живёт в отдельной таблице и НЕ входит
#в синхронизацию: на другие устройства пользователя ему ехать незачем.

@router.post("/push-token")
def register_push_token(payload: dict = Body(...),
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Приложение сообщает свой токен устройства (при запуске и при его обновлении).

    Вызывается КАЖДЫЙ запуск, а не только при первом получении токена: так поле
    last_seen остаётся свежим, и уборка не выбросит живое устройство. RuStore умеет
    менять токен молча — поэтому запись ключуется самим токеном, а не пользователем."""
    token = (payload.get("token") or "").strip()
    if not token or len(token) > 1024:
        raise HTTPException(status_code=400, detail="Нужен непустой token")
    platform = (payload.get("platform") or "android").strip()[:32]
    now = datetime.now(timezone.utc).isoformat()

    row = db.get(PushToken, token)
    if row is None:
        row = PushToken(token=token, created_at=now)
        db.add(row)
    #Логин перезаписываем ВСЕГДА: на одном телефоне мог войти другой пользователь, и
    #слать ему чужие уведомления недопустимо.
    row.login = user.login or ""
    row.platform = platform
    row.last_seen = now
    row.fail_count = 0
    db.commit()
    return {"ok": True}


@router.delete("/push-token")
def delete_push_token(payload: dict = Body(default={}),
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """Отписка при выходе из аккаунта. Без неё следующий владелец телефона (или тот же
    человек под другим логином) продолжал бы получать чужие уведомления."""
    token = (payload.get("token") or "").strip()
    q = db.query(PushToken).filter(PushToken.login == (user.login or ""))
    if token:
        q = q.filter(PushToken.token == token)
    removed = q.delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "removed": removed}


#Вкладка «Уведомления» — список писем, чтение, отметки о прочтении.
#
#⚠️ ПОРЯДОК ОБЪЯВЛЕНИЯ МАРШРУТОВ ЗДЕСЬ ЗНАЧИМ. Статический "/events/unread-count" обязан
#идти РАНЬШЕ шаблонного "/events/{event_id}": FastAPI сопоставляет маршруты сверху вниз,
#и при обратном порядке запрос за счётчиком попал бы в обработчик события с
#event_id="unread-count" и вернул 404 вместо числа.

@router.get("/events/unread-count")
def unread_events_count(user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Только число непрочитанных — для значка в интерфейсе.

    Отдельный дешёвый COUNT: ради цифры в углу экрана незачем выгружать список событий
    при каждом открытии страницы."""
    n = (db.query(NotifyEvent)
         .filter(NotifyEvent.login == (user.login or ""), NotifyEvent.read_at == "")
         .count())
    return {"unread": n}


@router.post("/events/read-all")
def mark_all_events_read(user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """«Прочитать все». Фильтр по login обязателен: без него UPDATE прошёлся бы по
    чужим строкам."""
    now = datetime.now(timezone.utc).isoformat()
    n = (db.query(NotifyEvent)
         .filter(NotifyEvent.login == (user.login or ""), NotifyEvent.read_at == "")
         .update({NotifyEvent.read_at: now}, synchronize_session=False))
    db.commit()
    return {"ok": True, "updated": n}


@router.post("/events/{event_id}/read")
def mark_event_read(event_id: str,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Отметить одно событие прочитанным (клик по письму в списке).

    Чужое событие — 404, а не 403, по той же причине, что и в get_notify_event: 403
    подтвердил бы факт существования события."""
    row = db.get(NotifyEvent, event_id)
    if row is None or row.login != (user.login or ""):
        raise HTTPException(status_code=404, detail="Событие не найдено")
    if not row.read_at:
        row.read_at = datetime.now(timezone.utc).isoformat()
        db.commit()
    return {"ok": True, "read_at": row.read_at}


@router.get("/events/{event_id}")
def get_notify_event(event_id: str,
                     user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Куда открыть экран по нажатому уведомлению + текст письма.

    Проверка владельца обязательна: id события уезжает в пуш и оседает на телефоне,
    поэтому его нельзя считать секретом. На чужой id отвечаем 404, а НЕ 403 — 403
    подтвердил бы, что такое событие существует, и превратил бы перебор id в способ
    узнать чужую активность."""
    row = db.get(NotifyEvent, event_id)
    if row is None or row.login != (user.login or ""):
        raise HTTPException(status_code=404, detail="Событие не найдено")
    if not row.read_at:
        row.read_at = datetime.now(timezone.utc).isoformat()
        db.commit()
    #Поля title/body/payload добавлены позже: у событий, накопленных до этого, они
    #пустые — клиент в таком случае показывает текст по kind, как делал раньше.
    return {"id": row.id, "kind": row.kind, "subject": row.subject,
            "lesson_id": row.lesson_id, "created_at": row.created_at,
            "title": row.title or "", "body": row.body or "",
            "payload": row.payload or {}, "read_at": row.read_at or ""}


def _fire_due_reminders(db: Session, user: User) -> int:
    """Превратить наступившие напоминания в обычные события. Возвращает их число.

    Полностью в try/except: напоминания — вспомогательная фича, и её сбой не имеет права
    лишить человека уведомлений об оценках, ради которых он сюда и пришёл."""
    try:
        from datetime import datetime, timezone
        from ..models import Reminder
        now = datetime.now(timezone.utc).isoformat()
        due = (db.query(Reminder)
               .filter(Reminder.login == (user.login or ""),
                       Reminder.fired_at == "",
                       Reminder.remind_at <= now)
               .all())
        if not due:
            return 0

        # 🔥 ЗВУК БЕСЕДЫ ГАСИТ И НАПОМИНАНИЯ (25.08.2026, просьба Влада: «кнопка звука,
        # которая может полностью выключать уведомления от таймеров»). До этого мьют
        # глушил пуши о сообщениях и громкие отметки, а напоминание из ТОЙ ЖЕ беседы
        # всё равно звонило: оно материализуется по ЛОГИНУ и про беседу не спрашивало.
        # Человек, выключивший звук у чата, справедливо считал это поломкой.
        #
        # ⚠️ Гасим ПУШ, но не само срабатывание: письмо во вкладке «Уведомления»
        # остаётся. Это история, а не звонок, — то же правило, что у категорий
        # уведомлений (§11). Напоминание, поставленное человеком САМОМУ СЕБЕ, стирать
        # молча нельзя: он его планировал.
        from ..models import ConversationParticipant
        muted_convs = {p.conversation_id for p in db.query(ConversationParticipant)
                       .filter(ConversationParticipant.user_id == (user.id or ""),
                               ConversationParticipant.muted == True).all()}  # noqa: E712

        from .. import rustore_push
        for r in due:
            #Пуш шлём тем же путём, что и остальные события: человек мог поставить
            #напоминание неделю назад и приложение с тех пор не открывать.
            if r.conversation_id not in muted_convs:
                rustore_push.notify_reminder(db, user.login, r.text, r.conversation_id)
            r.fired_at = now
        db.commit()
        return len(due)
    except Exception as e:      # noqa: BLE001
        print(f"[reminders] не удалось обработать напоминания: {e}")
        return 0


#§правка: жалоба (тикет модерации), которую 10 часов никто не тронул (статус так и
#остался 'open' — даже не «в работу»), закрывается САМА. Свободный доступ к чужой
#переписке БЕЗ активного расследования — риск сам по себе (см. mod_conversation_
#messages ниже: доступ закрывается сразу, как только жалоба перестаёт быть
#открытой) — бессрочно висящая жалоба продлевала бы этот риск бессрочно же.
_REPORT_EXPIRY_HOURS = 10


def _expire_stale_reports(db: Session) -> int:
    """Тот же приём, что и напоминания ниже — планировщик ради одной проверки не
    заводим, один индексный запрос (`status`+`created_at` уже проиндексированы в
    MessageReport) почти бесплатен, и делать её логично там, где ЛЮБОЙ активный
    пользователь (не только автор жалобы) и так пришёл за уведомлениями — иначе
    просрочка обнаружилась бы только если админ откроет модерацию сам.
    Полностью в try/except: сбой не должен ронять чтение уведомлений."""
    try:
        from ..models import MessageReport
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=_REPORT_EXPIRY_HOURS)).isoformat()
        stale = (db.query(MessageReport)
                 .filter(MessageReport.status == "open",
                         MessageReport.created_at <= cutoff).all())
        if not stale:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        for r in stale:
            r.status = "expired"
            r.handled_at = now
            reporter = db.query(User).filter(User.id == r.reporter_id).first()
            if reporter and reporter.login:
                rustore_push.notify_report_expired(db, reporter.login, r.id)
        db.commit()
        return len(stale)
    except Exception as e:      # noqa: BLE001
        print(f"[reports] не удалось обработать просроченные жалобы: {e}")
        return 0


@router.get("/events")
def list_events(filter_: str = Query("unread", alias="filter"),
                limit: int = Query(50, ge=1, le=100),
                offset: int = Query(0, ge=0),
                user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Список событий пользователя.

    ⚠️ ПО УМОЛЧАНИЮ отдаёт ТОЛЬКО НЕПРОЧИТАННЫЕ — поведение менять нельзя. Этот
    эндпоинт уже вызывают установленные Android-приложения (см. web/src/services/push.js
    и нативную часть), а у пользователей на телефонах старый бандл. Полный список для
    вкладки «Уведомления» запрашивается явно: ?filter=all."""
    #Напоминания (§D19) срабатывают ЗДЕСЬ, а не по расписанию: планировщик ради одной
    #фичи означал бы вечный фоновый поток на одноядерном VPS. Проверка — один запрос по
    #индексу (login + remind_at), и делать её логично ровно там, где человек всё равно
    #пришёл за уведомлениями.
    _fire_due_reminders(db, user)
    _expire_stale_reports(db)
    #Политика хранения (152-ФЗ) — тем же приёмом и по той же причине: не чаще раза в
    #сутки на процесс, проверка метки в памяти без единого запроса к базе. Подробности,
    #сроки и главное — ПОЧЕМУ окно уборки надгробий такое длинное — в app/retention.py.
    _retention.maybe_run(db)
    q = db.query(NotifyEvent).filter(NotifyEvent.login == (user.login or ""))
    if filter_ != "all":
        q = q.filter(NotifyEvent.read_at == "")
    rows = (q.order_by(NotifyEvent.created_at.desc())
            .offset(offset).limit(limit).all())
    #Непрочитанные считаем отдельно: при filter=all длина списка о них ничего не говорит,
    #а значку нужно честное число.
    unread = (db.query(NotifyEvent)
              .filter(NotifyEvent.login == (user.login or ""), NotifyEvent.read_at == "")
              .count())
    return {"count": len(rows), "unread": unread,
            "items": [{"id": r.id, "kind": r.kind, "subject": r.subject,
                       "created_at": r.created_at,
                       "title": r.title or "", "body": r.body or "",
                       "read_at": r.read_at or ""} for r in rows]}
