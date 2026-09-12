"""
auth.py — Авторизация: создание первого администратора и вход (JWT).

Offline-first: пользователей заводит админ в десктоп-проге, они синхронизируются
на сервер уже хешами паролей. Логин через API/сайт работает с теми же паролями.
"""
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import (ensure_device_allowed, get_current_user, is_web_client,
                    device_barrier_applies)
from ..models import User, AuthSession, set_user_password
from ..schemas import LoginIn, TokenOut, BootstrapIn, RefreshIn
from ..security import MIN_PASSWORD_LEN, verify_password, create_token_full, decode_token
from ..config import issue_ttl_min, session_ttl_min
from .. import throttle, events, audit, canary

router = APIRouter(prefix="/auth", tags=["auth"])


def _now() -> str:
    #UTC + смещение (+00:00) — единый формат меток с клиентом, чтобы LWW-сравнение
    #строк было корректным независимо от часового пояса.
    return datetime.now(timezone.utc).isoformat()


def client_kind(request: Request) -> str:
    """Каким клиентом пришёл запрос: 'android' | 'web' | '' (десктоп).

    Заголовок ставит сам клиент (`api/client.js`), подделать его тривиально — поэтому
    он НЕ даёт никаких прав, а только выбирает длину сессии при ВЫДАЧЕ токена. Дальше
    выбор фиксируется в auth_sessions.client и уже не зависит от заголовков."""
    if request is None:
        return ""
    v = (request.headers.get("x-client", "") or "").strip().lower()
    return v if v in ("android", "web") else ""


def _record_session(db: Session, jti: str, login: str, role: str, kind: str,
                    exp: int, request: Request, pair_jti: str = "", client: str = ""):
    """Сохраняет выданный токен в AuthSession (для отзыва/refresh/видимости сессий)."""
    dev = ip = ""
    try:
        if request is not None:
            dev = request.headers.get("X-Device-Id", "") or ""
            ip = throttle.client_ip(request)
    except Exception:
        pass
    db.add(AuthSession(jti=jti, login=login, role=role, kind=kind, device_id=dev,
                       ip=ip, issued_at=_now(), expires_at=int(exp), revoked=False,
                       pair_jti=pair_jti, client=client))


def _issue_token_pair(db: Session, user: User, request: Request) -> TokenOut:
    """Выдаёт пару (access + refresh), записывает обе сессии и коммитит.

    access — короткий (для запросов), refresh — длинный (тихое обновление). Связаны
    через pair_jti, чтобы logout/отзыв гасил оба разом.

    ⚠️ Длину сессии задаёт КЛИЕНТ (мобильное приложение — до ближайшего понедельника,
    сайт и десктоп — прежние 5 часов, см. config.issue_ttl_min и комментарий там же о
    том, почему это не одинаково для всех устройств). Записываем её в саму сессию,
    чтобы потолок на /auth/refresh считался от того же значения, а не от заголовка
    очередного запроса."""
    client = client_kind(request)
    #🔑 Срок сессии зависит от того, есть ли у человека второй фактор: с ним неделя на
    #сайте и месяц в приложении, без него — прежние пять часов и «до понедельника».
    #Спрашиваем ЗДЕСЬ, а не у вызывающих: пар токенов выдаётся из четырёх мест (первый
    #админ, обычный вход, второй шаг входа, вход по биометрии), и забытое место молча
    #выдало бы короткую сессию человеку, который её уже заслужил.
    from . import mfa as _mfa
    ttl = issue_ttl_min(client, mfa=_mfa.is_active(db, user.id))
    access, a_jti, a_exp = create_token_full(user.login, user.role, "access", ttl_min=ttl)
    refresh, r_jti, r_exp = create_token_full(user.login, user.role, "refresh", ttl_min=ttl)
    _record_session(db, a_jti, user.login, user.role, "access", a_exp, request, r_jti, client)
    _record_session(db, r_jti, user.login, user.role, "refresh", r_exp, request, a_jti, client)
    db.commit()
    name = user.full_name or f"{user.surname} {user.name}".strip()
    return TokenOut(access_token=access, refresh_token=refresh, role=user.role, name=name)


@router.post("/bootstrap-admin", response_model=TokenOut)
def bootstrap_admin(body: BootstrapIn, request: Request, db: Session = Depends(get_db)):
    """Создаёт ПЕРВОГО администратора. Работает только если админа ещё нет —
    безопасно вызывать при первичной настройке сервера.

    Барьер устройства применяется и здесь: первичную настройку делает хост (его
    device_id пропускается), а случайный ПК из сети не сможет создать админа."""
    ensure_device_allowed(request, db)
    exists = db.query(User).filter(
        User.role == "admin", User.deleted == False  # noqa: E712
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="Администратор уже создан")
    if len(body.password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Пароль не короче {MIN_PASSWORD_LEN} символов")
    login = body.login.strip()
    #Детерминированный id (admin:<login>): когда десктоп позже пришлёт админа
    #через /sync, он обновит ЭТУ же строку, а не создаст дубликат.
    u = User(
        id=f"admin:{login}", role="admin", login=login,
        full_name=body.full_name, updated_at=_now(),
    )
    set_user_password(u, body.password)   #хеш и дата выдачи — одной функцией
    db.add(u)
    db.commit()
    return _issue_token_pair(db, u, request)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    """Вход по логину и паролю. Роль — внутри токена.

    Анти-брутфорс: до сверки пароля проверяем, не заблокирован ли этот источник за
    серию неверных попыток (см. throttle). Блокировка по паре (IP, логин) — чтобы
    атакующий не мог запереть вход настоящему пользователю. Неудача увеличивает
    счётчик, удачный вход его сбрасывает."""
    login_str = body.login.strip()
    ip = throttle.client_ip(request)
    web = is_web_client(request)

    #🍯 HONEYTOKEN. Этот логин может утечь ТОЛЬКО вместе с дампом базы или файлом
    #окружения: угадать его нельзя, а законный человек его не знает. Поэтому попытка
    #входа под ним — не подозрение, а ДОКАЗАТЕЛЬСТВО утечки (`app/canary.py`).
    #⚠️ Проверка стоит ДО поиска пользователя и до сверки пароля: строки в `users` для
    #него нет и быть не должно (приманка в таблице попала бы в списки, выгрузки и
    #средние баллы — см. запрет в шапке canary.py).
    #⚠️ Ответ ТОТ ЖЕ, что при обычной неудаче: сказать «это приманка» значит объяснить
    #атакующему, какие данные у него помечены.
    if canary.is_honeytoken(login_str):
        throttle.ban_ip(ip, canary.BAN_SECONDS)
        try:
            events.record("security", "honeytoken_used",
                          "попытка входа под учёткой-приманкой", ip=ip)
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    #Барьер устройства ДО сверки пароля: неодобренный ПК не должен входить (даже зная
    #верные креды), и не нужно дёргать дорогой PBKDF2 ради него. ДЕСКТОП (и любой не-веб
    #клиент) — жёсткий барьер, как прежде. Для ВЕБА барьер откладываем: студенту он не
    #нужен, а роль мы узнаем после поиска пользователя (ниже).
    if not web:
        ensure_device_allowed(request, db)

    left = throttle.seconds_until_unlocked(ip, login_str)
    if left > 0:
        #429 + Retry-After — стандартный сигнал «слишком много попыток, подожди».
        events.record("warn", "login_throttled",
                      f"вход заблокирован за перебор (ещё {left} с)", login_str, ip)
        raise HTTPException(
            status_code=429,
            detail=f"Слишком много неудачных попыток. Повторите через {left} с.",
            headers={"Retry-After": str(left)},
        )

    u = db.query(User).filter(
        User.login == login_str, User.deleted == False  # noqa: E712
    ).first()
    #⚠️ ЗДЕСЬ БЫЛА МЁРТВАЯ ВЕТКА, УДАЛЕНА 21.08.2026 при разборе безопасности мобилки:
    #    if web and u is not None and device_barrier_applies(request, u.role): ...
    #`device_barrier_applies` первой строкой отвечает False любому веб-клиенту, поэтому
    #условие было тождественно `web and not web` и не исполнялось НИ РАЗУ. Комментарий
    #над ним при этом обещал, что «персонал в вебе проходит подтверждение устройства», —
    #то есть код читался как действующая защита, которой нет. Это наш самый частый класс
    #дефекта (обещание без вызывающего), и опаснее всего он именно в таком виде: строка
    #есть, тесты зелёные, защиты нет.
    #Фактическая политика: барьер для веба и мобилки снят для ВСЕХ ролей (§11 CLAUDE.md,
    #docstring device_barrier_applies, тесты test_device_policy.py). Вернут ролевую —
    #править ОДНО место, `deps.device_barrier_applies`, и ветка тут не понадобится.

    #Сверка пароля — под слотом: гибридный хеш стоит 200k+200k итераций, и на одноядерном
    #VPS полсотни одновременных входов положили бы сайт всем (см. throttle.hash_slot).
    #Слот не получен — это НЕ неудачный вход: счётчики не трогаем, человек ни при чём.
    with throttle.hash_slot() as got_slot:
        if not got_slot:
            events.record("warn", "login_busy",
                          "вход отложен: сервер занят проверкой паролей", login_str, ip)
            raise HTTPException(
                status_code=503,
                detail="Сервер сейчас занят. Повторите через несколько секунд.",
                headers={"Retry-After": str(int(throttle.HASH_WAIT_S) or 1)},
            )
        password_ok = u is not None and verify_password(body.password, u.password_hash)

    if u is None:
        #Логина не существует: хешировать нечего, и без задержки ответ прилетел бы
        #МГНОВЕННО — в отличие от существующего логина, где считается 200k итераций.
        #Эта разница во времени сама по себе отвечает «есть такой аккаунт или нет», то
        #есть даёт собрать список живых логинов, не подобрав ни одного пароля. Плюс
        #перебор выдуманных логинов шёл бы даром и на полной скорости.
        time.sleep(throttle.UNKNOWN_LOGIN_DELAY_S)

    if not password_ok:
        #login_exists различает опечатку живого человека и перебор: счётчик ПО ВСЕМУ
        #адресу двигают только попытки против НЕсуществующих логинов. Иначе группа
        #студентов за одним VPN/NAT запирала бы вход сама себе (см. throttle.IP_MAX_FAILS).
        throttle.register_failure(ip, login_str, login_exists=u is not None)
        #След по ЛОГИНУ живёт час и не зависит от адреса — из него собирается признак
        #«к этому аккаунту подбирали пароль» (throttle.suspicion). Замок пары (IP,
        #логин) для этого не годится: он снимается через пять минут и всё забывает,
        #то есть подобравший пароль входит через шесть минут в полной тишине.
        if u is not None:
            throttle.register_login_attack(login_str, ip)
        events.record("warn", "login_failed", "неверный логин или пароль", login_str, ip)
        audit.log(db, request, actor=login_str, action="login.fail", level="warn")
        #Пасхалка Far Cry: седьмая неудача подряд у СТУДЕНТА. Решение принимает сервер и
        #сообщает заголовком — тела ответа не трогаем, чтобы не менять форму ошибки,
        #которую разбирает форма входа.
        #⚠️ Утечки здесь нет: заголовок видит ровно тот, кто сам семь раз промахнулся
        #мимо пароля, и ничего нового ему не сообщает — на седьмой попытке анти-брутфорс
        #и так запирает пару (IP, логин).
        headers = None
        try:
            from .. import easter_eggs
            if u is not None and u.role == "student"                     and easter_eggs.farcry_due(login_str, db, u.id):
                headers = {"X-Gb-Egg": "farcry_vaas_quote"}
        except Exception:      # noqa: BLE001 — пасхалка не имеет права мешать входу
            headers = None
        raise HTTPException(status_code=401, detail="Неверный логин или пароль",
                            headers=headers)

    throttle.register_success(ip, login_str)
    events.record("info", "login", f"вход выполнен (роль {u.role})", login_str, ip)
    audit.log(db, request, actor=login_str, role=u.role, action="login.ok")

    #🔒 УДАЧНЫЙ ВХОД СРАЗУ ПОСЛЕ СЕРИИ НЕУДАЧ — это и есть картина подобранного пароля.
    #Записываем ОТДЕЛЬНОЙ строкой с цифрами: обычный `login.ok` в общем потоке ничем не
    #выделяется, и при разборе инцидента такой вход не найти. Сам вход НЕ отменяем —
    #это лишь признак, а не доказательство, и запирать по нему живого человека, который
    #просто забыл раскладку, нельзя.
    #⚠️ Второй фактор здесь не «включается по признаку»: у кого он есть, тот и так его
    #сейчас пройдёт (ниже), а у кого нет — требовать нечего. Признак работает там, где
    #фактор МОЖНО спросить: продление сессии (см. /auth/refresh) и смена пароля по
    #ссылке (см. /auth/recover/confirm).
    sus = throttle.suspicion(login_str)
    if sus["suspicious"]:
        detail = (f"вход после {sus['fails']} неудачных попыток "
                  f"с {sus['ips']} адрес(ов) за последний час")
        events.record("warn", "login_after_attack", detail, login_str, ip)
        audit.log(db, request, actor=login_str, role=u.role,
                  action="login.ok.suspicious", level="warn", detail=detail)

    #Преподаватель вошёл — СРАЗУ запускаем сборку снимка расписания в фоне. Полный
    #снимок это ~68 запросов к порталу (десятки секунд), и раньше эта сборка стартовала
    #только когда препод уже открыл вкладку расписания — он смотрел на «строится…».
    #Теперь сборка идёт, пока он на дашборде, и к моменту открытия расписание готово.
    #warm() ничего не блокирует: лишь стартует фоновый поток, если снимок несвежий.
    if u.role == "teacher":
        try:
            from .. import schedule_web
            schedule_web.warm()
        except Exception:
            pass        #прогрев — удобство, а не условие входа; сбой не мешает логину

    #── ВТОРОЙ ФАКТОР ────────────────────────────────────────────────────────────
    #Пароль сошёлся, но фактор включён — токенов НЕ ВЫДАЁМ ВОВСЕ. Вместо них
    #короткий challenge, который годится ровно на один вызов /auth/mfa/verify.
    #
    #⚠️ Почему не «выдать токен с пометкой». Пометку пришлось бы проверять в каждой
    #из двух с лишним сотен ручек; первая забытая — это доступ к журналу по одному
    #паролю, причём молча. Здесь забыть негде: токена просто нет.
    from . import mfa as _mfa
    if _mfa.is_active(db, u.id):
        events.record("info", "mfa_challenge", "запрошен второй фактор", login_str, ip)
        #JSONResponse, а НЕ обычный dict: у этой ручки объявлен response_model=TokenOut,
        #и он молча выбросил бы поля mfa_required/challenge — клиент получил бы пустой
        #ответ и решил, что вход сломался. Возврат готового Response отключает
        #фильтрацию по модели; это документированный способ, а не обход.
        #`expires_in` нужен КЛИЕНТУ, чтобы показать обратный отсчёт. Без него
        #окончание срока выглядит как внезапный выброс на форму входа: человек не
        #знал, что у него было время, и не понимает, что произошло.
        #⚠️ Отдаём СЕКУНДЫ, а не метку времени: часы браузера расходятся с серверными,
        #и по абсолютной метке отсчёт врал бы ровно на эту разницу.
        return JSONResponse({"mfa_required": True,
                             "challenge": _mfa.make_challenge(u),
                             "expires_in": _mfa.CHALLENGE_TTL_MIN * 60})
    return _issue_token_pair(db, u, request)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, request: Request, db: Session = Depends(get_db)):
    """Тихое обновление сессии: меняем валидный refresh-токен на НОВЫЙ access.

    Сценарий 152-ФЗ/удобства: короткий access протух (например, за время офлайна), но
    refresh ещё жив — клиент в фоне дёргает этот эндпоинт и продолжает работу, НЕ
    выкидывая пользователя на экран логина. Барьер устройства проверяем и здесь."""
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("typ") != "refresh":
        raise HTTPException(status_code=401, detail="Недействительный refresh-токен")
    jti = payload.get("jti", "")
    sess = db.query(AuthSession).filter(AuthSession.jti == jti).first()
    if sess is None or sess.revoked:
        #refresh отозван (logout/блокировка админом) или неизвестен — обновлять нечего
        raise HTTPException(status_code=401, detail="Сессия завершена или отозвана")

    #Пользователя ищем ДО проверок срока: от того, включён ли у него второй фактор,
    #зависит сам потолок сессии (неделя/месяц против пяти часов).
    #⚠️ Признак берём ЗАНОВО, а не из записи сессии. Отключил второй фактор — его
    #сессии немедленно судятся по короткой политике и умирают досрочно; иначе
    #«включу на минуту, получу месяц, выключу» было бы штатным обходом потолка.
    login_str = payload.get("sub", "")
    u = db.query(User).filter(
        User.login == login_str, User.deleted == False  # noqa: E712
    ).first()
    if not u:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    from . import mfa as _mfa
    mfa_on = _mfa.is_active(db, u.id)
    #⚠️ АБСОЛЮТНЫЙ потолок сессии — от РЕАЛЬНОГО момента входа (sess.issued_at, дата в
    #БД, а не exp из JWT). Без этой проверки «жёсткие 5 часов» держались ТОЛЬКО тем,
    #что refresh не ротируется и его exp считался как iat+JWT_REFRESH_TTL_MIN — то есть
    #предположением, что JWT_REFRESH_TTL_MIN всегда равен JWT_TTL_MIN. Токен, выданный
    #ДО того, как это значение однажды сузили с 30 дней до 5 часов, продолжал бы
    #обновляться ещё месяц — ровно так и было поймано вживую (простое повторное
    #открытие сайта держало сессию намного дольше 5 часов). Теперь потолок не зависит
    #от текущего конфига и не может быть обойдён повторным изменением настройки.
    try:
        issued = datetime.fromisoformat(sess.issued_at)
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=timezone.utc)
        session_age_min = (datetime.now(timezone.utc) - issued).total_seconds() / 60
    except (TypeError, ValueError):
        session_age_min = 0      #метка повреждена/отсутствует — не блокируем по этой причине
    #Потолок берём ПО КЛИЕНТУ, записанному в самой сессии при входе (см. models.AuthSession
    #и config.session_ttl_min): у мобильного приложения он недельный, у сайта и десктопа —
    #прежние 5 часов. Читать «мобильный ли клиент» из ЗАГОЛОВКА прямо здесь нельзя: тогда
    #браузер, приславший X-Client: android, растянул бы уже выданную веб-сессию до недели,
    #то есть заголовок стал бы способом обойти потолок.
    if session_age_min > session_ttl_min(sess.client or "", mfa=mfa_on):
        sess.revoked = True
        db.commit()
        raise HTTPException(status_code=401, detail="Сессия истекла, нужен повторный вход")
    #ВТОРАЯ, независимая граница — КОНКРЕТНЫЙ срок, назначенный этой сессии при входе
    #(auth_sessions.expires_at). Проверка выше сравнивает возраст с потолком ПОЛИТИКИ и
    #поэтому не видит рубежа, который зависит от даты входа: мобильная сессия кончается
    #в ближайший понедельник, и в воскресенье её возраст (шесть дней) честно меньше
    #недельного потолка — то есть одной верхней проверки мало, понедельник наступил бы,
    #а сессия жила дальше. Ноль/пусто = запись старого формата, такие судим только по
    #возрасту (не выкидывать людей из-за нашей же миграции).
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if sess.expires_at and now_ts >= int(sess.expires_at):
        sess.revoked = True
        db.commit()
        raise HTTPException(status_code=401, detail="Сессия истекла, нужен повторный вход")
    #Барьер устройства применяем по той же политике, что и на входе: персонал и
    #десктоп — обязательно; веб-студенту не нужен (роль знаем из его же токена).
    if device_barrier_applies(request, u.role):
        ensure_device_allowed(request, db)

    #🔒 ПОДОЗРИТЕЛЬНАЯ АКТИВНОСТЬ ОТМЕНЯЕТ ТИХОЕ ПРОДЛЕНИЕ (03.09.2026, требование
    #Ярослава «код должен проситься при подозрительной активности»).
    #
    #Продление сессии — единственное действие, которое сейчас происходит БЕЗ участия
    #человека: клиент меняет refresh на новый access в фоне, и укравший refresh-токен
    #живёт на нём ровно столько же, сколько владелец. Если по этому логину только что
    #подбирали пароль, тихо продлевать нельзя — просим подтвердить, что это владелец.
    #
    #⚠️ ТОЛЬКО ТЕМ, У КОГО ФАКТОР ЕСТЬ. У остальных отказ не даёт НИЧЕГО: они просто
    #введут тот самый пароль, который и подбирают. Зато даёт постороннему готовый
    #способ выбивать человека из журнала — поспамил неверным паролем, и жертву каждые
    #пять часов выкидывает на форму входа. Признак поднимает кто угодно, поэтому
    #реакция обязана быть проходимой владельцем и бесполезной для атакующего.
    if throttle.is_suspicious(login_str) and mfa_on:
        sus = throttle.suspicion(login_str)
        detail = (f"продление сессии остановлено: {sus['fails']} неудачных попыток "
                  f"с {sus['ips']} адрес(ов) за час")
        events.record("warn", "refresh_step_up", detail, login_str,
                      throttle.client_ip(request))
        audit.log(db, request, actor=login_str, role=u.role,
                  action="session.step_up", level="warn", detail=detail)
        #401 — тот же код, что у истёкшей сессии, и клиент уже умеет его обрабатывать:
        #показывает форму входа. Заводить здесь свой поток «подтвердите код прямо
        #поверх страницы» значило бы новый экран ради редкого случая; повторный вход с
        #паролем И кодом даёт ту же гарантию и уже написан.
        #⚠️ Признак в заголовке — чтобы человек увидел ПРИЧИНУ. «Сессия истекла» в
        #середине рабочего дня выглядит как сбой продукта, а не как защита.
        raise HTTPException(
            status_code=401,
            detail=("К вашему аккаунту подбирали пароль. Войдите заново и подтвердите "
                    "вход кодом из приложения."),
            headers={"X-Gb-Reason": "reauth_required"})
    #Новый access, привязанный к ТОМУ ЖЕ refresh (refresh не ротируем — он живёт до
    #своего exp или явного отзыва). Прежний access этой пары гасим.
    if sess.pair_jti:
        old = db.query(AuthSession).filter(AuthSession.jti == sess.pair_jti).first()
        if old is not None:
            old.revoked = True
    #Длину нового access берём из САМОЙ СЕССИИ, а не из заголовка этого запроса, и не
    #из глобального дефолта: иначе у мобильной сессии access жил бы 5 часов при недельном
    #потолке и приложение всё равно ходило бы за refresh каждые пять часов — то есть
    #ровно то, от чего мы уходим. Клиент наследуется, а не перечитывается.
    #⚠️ И ОБРЕЗАЕМ его сроком самой сессии. Без обрезки выданный в воскресенье access жил
    #бы неделю — то есть пережил бы понедельничный рубеж: проверки выше срабатывают
    #только когда клиент придёт ЗА ОБНОВЛЕНИЕМ, а с ещё действующим access он не придёт
    #вовсе. Рубеж, который держится лишь при добровольном визите клиента, — не рубеж.
    ttl = session_ttl_min(sess.client or "", mfa=mfa_on)
    if sess.expires_at:
        ttl = max(1, min(ttl, int((int(sess.expires_at) - now_ts) // 60)))
    access, a_jti, a_exp = create_token_full(u.login, u.role, "access", ttl_min=ttl)
    _record_session(db, a_jti, u.login, u.role, "access", a_exp, request, jti,
                    sess.client or "")
    sess.pair_jti = a_jti
    db.commit()
    name = u.full_name or f"{u.surname} {u.name}".strip()
    #refresh возвращаем тот же — клиент продолжает им пользоваться.
    return TokenOut(access_token=access, refresh_token=body.refresh_token,
                    role=u.role, name=name)


@router.post("/logout")
def logout(request: Request, authorization: str = Header(None),
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Безопасный выход: сервер ОТЗЫВАЕТ текущий access и связанный refresh.

    Зачем серверу «забывать» токен: если злоумышленник успел скопировать токен из
    памяти ПК ДО нажатия «Выйти», без отзыва он пользовался бы им до конца срока. После
    logout сервер мгновенно перестаёт его принимать (чёрный список AuthSession)."""
    revoked = 0
    payload = decode_token((authorization or "").split(" ", 1)[-1].strip())
    jti = (payload or {}).get("jti", "")
    if jti:
        sess = db.query(AuthSession).filter(AuthSession.jti == jti).first()
        if sess is not None:
            sess.revoked = True
            revoked += 1
            if sess.pair_jti:
                pair = db.query(AuthSession).filter(AuthSession.jti == sess.pair_jti).first()
                if pair is not None and not pair.revoked:
                    pair.revoked = True
                    revoked += 1
        db.commit()
        #🔒 И РВЁМ ЖИВОЙ СОКЕТ. Без этого «Выйти» закрывало только новые подключения, а
        #уже открытое продолжало получать сигналы о переписке часами. Подробности —
        #в `messenger.ws_manager.kick_user`. Импорт ленивый: `messenger` тянет за собой
        #пол-приложения, и статический импорт отсюда завязал бы вход на мессенджер.
        _kick_sockets(user.id)
        events.record("info", "logout", "выход (токен отозван)", user.login,
                      throttle.client_ip(request))
        audit.log(db, request, actor=user.login, role=user.role, action="logout")
    return {"revoked": revoked}


def _kick_sockets(user_id: str):
    """Разорвать живые WebSocket-соединения пользователя. Никогда не бросает: отзыв
    сессии обязан состояться, даже если мессенджер по какой-то причине недоступен."""
    try:
        from .messenger import ws_manager
        ws_manager.kick_user(user_id or "")
    except Exception as e:      # noqa: BLE001
        events.record("warn", "ws_kick_failed",
                      f"сокет не разорван после отзыва сессии: {e}", "", "")


# ── Самостоятельная регистрация студентов и восстановление пароля ────────────────
import secrets                                                        # noqa: E402
import threading                                                      # noqa: E402
import uuid as _uuid                                                   # noqa: E402
from datetime import timedelta                                        # noqa: E402
from fastapi import Body                                              # noqa: E402
from ..models import (RegistrationRequest, Group, PasswordReset,      # noqa: E402
                      StudentInvite, User)
from ..config import SITE_URL, PASSWORD_RESET_TTL_MIN                 # noqa: E402
from .. import reg_utils, mailer, gost                                # noqa: E402


@router.post("/register")
def register(body: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Заявка студента на регистрацию (публичная, без токена). Проверяем ФИО, ГРУППУ
    (с учётом «сдвоенных» — К104/2 → К104/2,105.0), телефон и e-mail (только разрешённые
    домены). Аккаунт НЕ создаётся сразу — заявка ждёт одобрения администратора."""
    #Анти-DDoS заявками: 5 неверных попыток с одного IP → блок на 3 минуты.
    ip = throttle.client_ip(request) if request is not None else ""
    left = throttle.seconds_until_reg_unlocked(ip)
    if left:
        raise HTTPException(status_code=429,
                            detail=f"Слишком много попыток регистрации. Подождите {left // 60 + 1} мин.")

    def _fail(msg: str, code: int = 400):
        throttle.register_reg_failure(ip)          #каждая невалидная попытка приближает блок
        raise HTTPException(status_code=code, detail=msg)

    full_name = " ".join((body.get("full_name") or "").split())
    phone_in = body.get("phone") or ""
    email = (body.get("email") or "").strip().lower()
    group_in = (body.get("group") or "").strip()

    if not reg_utils.valid_full_name(full_name):
        _fail("Укажите ФИО полностью (минимум фамилия и имя)")
    phone = reg_utils.normalize_phone(phone_in)
    if not phone:
        _fail("Некорректный номер телефона")
    if not reg_utils.valid_email(email):
        _fail("Разрешены только почты @yandex.ru, @mail.ru, @esstu.ru")
    names = [g.name for g in db.query(Group).filter(Group.deleted == False).all()]  # noqa: E712
    group = reg_utils.resolve_group(group_in, names)
    if not group:
        _fail(f"Группа «{group_in}» не найдена. Формат: К‹число›/‹подгруппа›, например К104/2.")

    #не плодим дубликаты: уже есть студент с таким логином или заявка на рассмотрении
    if db.query(User).filter(User.login == email, User.deleted == False).first():  # noqa: E712
        _fail("Аккаунт с такой почтой уже существует", 409)
    if db.query(RegistrationRequest).filter(
            RegistrationRequest.email == email,
            RegistrationRequest.status == "pending").first():
        _fail("Заявка с такой почтой уже на рассмотрении", 409)

    db.add(RegistrationRequest(id=str(_uuid.uuid4()), full_name=full_name, group_name=group,
                               phone=gost.encrypt(phone),  # ПДн-телефон шифруем при хранении (ГОСТ)
                               email=email, status="pending", created_at=_now()))
    db.commit()
    throttle.register_reg_success(ip)              #удачная заявка сбрасывает счётчик
    try:
        events.record("info", "registration_request", f"{full_name} · {group} · {email}")
    except Exception:
        pass
    audit.log(db, request, actor=email, action="reg.request", target=group)
    return {"ok": True, "group": group}


# ── РЕГИСТРАЦИЯ ПО ПРИГЛАШЕНИЮ КУРАТОРА ───────────────────────────────────────────
# Отличие от `/register` выше принципиальное: там заявка ЖДЁТ одобрения администратора,
# здесь одобрением служит сама ссылка — её выдал куратор группы (`/web/admin/invites`).
# Поэтому аккаунт заводится сразу, и поэтому же у ссылки три ограничителя (срок, число
# мест, отзыв), которые проверяет ОБЩЕЕ с выдающей стороной правило
# `reg_utils.invite_blocked_reason`.

@router.get("/invite/{token}")
def invite_info(token: str, request: Request = None, db: Session = Depends(get_db)):
    """Публично: что за приглашение (для экрана регистрации — «вы вступаете в К-24»).

    ⚠️ Под тем же ограничителем попыток, что и регистрация: без него этот адрес — готовый
    оракул для перебора токенов, отвечающий «да/нет» без единой задержки.
    ⚠️ Группу отдаём ТОЛЬКО по действующему приглашению: имя учебной группы само по себе
    не секрет, но подтверждать существование токена перебором незачем."""
    ip = throttle.client_ip(request) if request is not None else ""
    left = throttle.seconds_until_reg_unlocked(ip)
    if left:
        raise HTTPException(status_code=429,
                            detail=f"Слишком много попыток. Подождите {left // 60 + 1} мин.")
    inv = db.get(StudentInvite, (token or "").strip())
    reason = reg_utils.invite_blocked_reason(inv, _now())
    if reason:
        throttle.register_reg_failure(ip)
        raise HTTPException(status_code=404, detail=reason)
    return {"ok": True, "group": inv.group_name, "note": inv.note or "",
            "expires_at": inv.expires_at}


@router.post("/register-invite")
def register_by_invite(body: dict = Body(...), request: Request = None,
                       db: Session = Depends(get_db)):
    """Регистрация студента по ссылке-приглашению: аккаунт создаётся СРАЗУ.

    Группу берём ИЗ ПРИГЛАШЕНИЯ, а не из тела запроса — иначе ссылка в группу К-24
    заводила бы студента в любую другую, и весь смысл ограничения пропал бы.
    """
    ip = throttle.client_ip(request) if request is not None else ""
    left = throttle.seconds_until_reg_unlocked(ip)
    if left:
        raise HTTPException(status_code=429,
                            detail=f"Слишком много попыток регистрации. Подождите {left // 60 + 1} мин.")

    def _fail(msg: str, code: int = 400):
        throttle.register_reg_failure(ip)
        raise HTTPException(status_code=code, detail=msg)

    inv = db.get(StudentInvite, (body.get("token") or "").strip())
    reason = reg_utils.invite_blocked_reason(inv, _now())
    if reason:
        _fail(reason, 404)

    full_name = " ".join((body.get("full_name") or "").split())
    email = (body.get("email") or "").strip().lower()
    phone = reg_utils.normalize_phone(body.get("phone") or "")
    if not reg_utils.valid_full_name(full_name):
        _fail("Укажите ФИО полностью (минимум фамилия и имя)")
    if not reg_utils.valid_email(email):
        _fail("Разрешены только почты @yandex.ru, @mail.ru, @esstu.ru")
    if not phone:
        _fail("Некорректный номер телефона")
    if db.query(User).filter(User.login == email, User.deleted == False).first():  # noqa: E712
        _fail("Аккаунт с такой почтой уже существует", 409)

    #Заведение студента — ТА ЖЕ функция, что у одобрения заявки администратором.
    #Второй копии быть не должно: в ней формат id, разбор ФИО и серверная метка.
    _row, pw = reg_utils.create_student_account(db, email, full_name, inv.group_name, _now())
    #Место расходуем ТОЛЬКО после успешного создания: отказ на дубликате почты не должен
    #съедать чужое место в приглашении.
    inv.uses = int(inv.uses or 0) + 1
    db.commit()
    throttle.register_reg_success(ip)
    audit.log(db, request, actor=email, action="reg.invite", target=inv.group_name)

    sent = mailer.send_email(
        email, "GradeBookAI — доступ к электронному журналу",
        f"Здравствуйте, {full_name}!\n\nВы зарегистрированы в электронном журнале.\n"
        f"Логин: {email}\nПароль: {pw}\nГруппа: {inv.group_name}\n\n"
        f"Войдите на {SITE_URL or 'https://esstu-gradebook.ru'}",
        html=mailer._brand_html("Доступ к журналу готов", [
            f"Здравствуйте, <b>{full_name}</b>! Вы зарегистрированы в электронном журнале.",
            f"Логин: <b>{email}</b>",
            f"Пароль: <b style='font-size:18px'>{pw}</b>",
            f"Группа: <b>{inv.group_name}</b>"]))
    #Пароль возвращаем ТОЛЬКО когда письмо не ушло: иначе он лёг бы в историю браузера и
    #в логи прокси у всех, кто регистрировался успешно. Без почты альтернативы нет —
    #человек иначе не узнает пароль вовсе.
    return {"ok": True, "login": email, "group": inv.group_name, "sent": sent,
            "password": None if sent else pw}


@router.post("/recover")
def recover(body: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Запрос восстановления пароля студента по e-mail (= логин).

    ⚠️ ПАРОЛЬ ЗДЕСЬ НЕ МЕНЯЕТСЯ — только заводится одноразовая ссылка и уходит письмо.
    Сама смена — в `/auth/recover/confirm`, по переходу по этой ссылке. (Прежний
    докстринг обещал «генерируем НОВЫЙ пароль и высылаем его» — так и было до 3.7.3, и
    это была дыра: знающий чужую почту выкидывал человека из журнала повторяемо.)

    Ответ ВСЕГДА одинаковый — и по телу, и по ВРЕМЕНИ (см. `_uniform_delay` ниже):
    существование почты не раскрывается ни тем, ни другим."""
    started = time.monotonic()
    ip = throttle.client_ip(request) if request is not None else ""
    left = throttle.seconds_until_reg_unlocked(ip)
    if left:
        raise HTTPException(status_code=429,
                            detail=f"Слишком много запросов. Подождите {left // 60 + 1} мин.")
    throttle.register_reg_failure(ip)   #каждый запрос на сброс приближает временный лимит
    email = (body.get("email") or "").strip().lower()
    #🔒 ОСТУДА ПО САМОЙ ПОЧТЕ. Лимит по IP тут не защищает: атакующий меняет адрес (с VPN
    #это автоматически), а страдает живой человек за общим адресом. Ключ — жертва: один
    #аккаунт сбрасывается не чаще раза в час, сколько бы адресов ни сменили. Без этого
    #эндпоинт был повторяемым DoS: каждый запрос менял пароль и отзывал ВСЕ сессии, то
    #есть выбивал студента из журнала, а знать надо было только его почту.
    #⚠️ Ответ и здесь ОДИНАКОВЫЙ (тихо выходим), иначе 429 подтверждал бы существование
    #почты — ровно то, что остальной эндпоинт старательно скрывает.
    if email and throttle.seconds_until_recover_allowed(email):
        _uniform_delay(started)
        return {"ok": True}
    u = db.query(User).filter(User.login == email, User.role == "student",
                              User.deleted == False).first()  # noqa: E712
    sent = False
    if u:
        throttle.register_recover(email)
        #🔒 ПАРОЛЬ ЗДЕСЬ БОЛЬШЕ НЕ МЕНЯЕТСЯ. Раньше менялся — и это была настоящая дыра:
        #кто угодно, зная почту студента, обнулял ему доступ и отзывал ВСЕ сессии, то есть
        #выкидывал человека из журнала посреди пары. Повторяемо и без единого следа для
        #самого студента. Теперь запрос лишь ЗАВОДИТ одноразовую ссылку; всё, что можно
        #сделать, не владея почтой, — прислать человеку письмо.
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        db.add(PasswordReset(
            token=token, login=email, created_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=PASSWORD_RESET_TTL_MIN)).isoformat(),
            used_at="", ip=ip))
        #Прежние невостребованные ссылки гасим: иначе каждый повторный запрос добавлял бы
        #ещё один действующий ключ, и их накапливалось бы столько, сколько писем ушло.
        (db.query(PasswordReset)
           .filter(PasswordReset.login == email, PasswordReset.used_at == "",
                   PasswordReset.token != token)
           .update({"used_at": now.isoformat()}))
        db.commit()
        sent = _send_reset_link(email, token)
    _ = sent  # ответ намеренно НЕ зависит от sent — не раскрываем существование аккаунта
    #В ЖУРНАЛ (внутренний, админ-only) писать факт можно: анти-энумерация касается только
    #ответа клиенту. Пишем, был ли сброс реально выполнен — для разбора инцидентов.
    audit.log(db, request, actor=email, action="password.recover",
              detail="ссылка выслана" if u else "аккаунт не найден")
    _uniform_delay(started)
    return {"ok": True}


#🔒 ОБЩИЙ БЮДЖЕТ ВРЕМЕНИ ОТВЕТА. Тянуть каждый выход к одной и той же длительности —
#единственный способ убрать оракул: считать «где какой sleep поставить» по веткам мы уже
#пробовали, и первая же новая ветка (остуда по почте) этот расчёт сломала — существующая
#почта отвечала за миллисекунды, несуществующая за 350 мс, разница в 70 раз.
_RECOVER_BUDGET_S = 0.40


def _uniform_delay(started: float):
    """Дотянуть ответ до общего бюджета, каким бы путём мы сюда ни пришли."""
    left = _RECOVER_BUDGET_S - (time.monotonic() - started)
    if left > 0:
        time.sleep(left)


def _send_reset_link(email: str, token: str) -> bool:
    """Отправить письмо со ссылкой — В ФОНОВОМ ПОТОКЕ.

    ⚠️ Почему не синхронно, как раньше. SMTP — это сотни миллисекунд, а то и секунды
    (`mailer.SMTP_SSL(..., timeout=25)`), и они возникают ТОЛЬКО когда аккаунт существует.
    Никакой общий бюджет времени такой разброс не покроет: ждать 25 секунд на КАЖДОМ
    запросе нельзя, а не ждать — значит вернуть тот самый оракул с другой стороны. Поэтому
    письмо уходит после ответа, а сам ответ не зависит от почты вовсе.

    ⚠️ И ТОЛЬКО ПО HTTPS. Ссылка несёт одноразовый ключ от аккаунта; отдать её в открытый
    канал — это подарить доступ любому, кто в середине. Та же заслонка и та же причина,
    что у автообновления десктопа (`data/updater._transport_ok`) и у виджета расписания.
    Сегодня на бою адрес резолвится верно, но он ВЫЧИСЛЯЕТСЯ из `ALLOWED_ORIGINS` — и
    достаточно поставить туда первым http-адрес, чтобы письма молча начали рассылать
    токены по открытому каналу.
    """
    if not SITE_URL.startswith("https://") and "localhost" not in SITE_URL:
        events.record("error", "reset_link_insecure",
                      f"ссылка восстановления НЕ отправлена: небезопасный адрес {SITE_URL}",
                      email, "")
        return False
    link = f"{SITE_URL}/reset-password?token={token}"
    text = (f"Здравствуйте!\n\nВы запросили восстановление доступа к электронному журналу.\n"
            f"Чтобы задать новый пароль, перейдите по ссылке (действует "
            f"{PASSWORD_RESET_TTL_MIN} мин.):\n\n{link}\n\n"
            f"Логин: {email}\n\nЕсли вы этого не запрашивали — просто не переходите по "
            f"ссылке. Пароль останется прежним, ничего делать не нужно.")
    html = mailer._brand_html("Восстановление доступа", [
        "Вы запросили восстановление доступа к электронному журналу.",
        f"<a href='{link}'>Задать новый пароль</a> "
        f"(ссылка действует {PASSWORD_RESET_TTL_MIN} мин.)",
        f"Логин: <b>{email}</b>",
        "Если вы этого не запрашивали — просто не переходите по ссылке. "
        "Пароль останется прежним."])

    def _worker():
        try:
            mailer.send_email(email, "GradeBookAI — восстановление доступа", text, html=html)
        except Exception as e:      # noqa: BLE001
            #Ответ клиенту уже ушёл, поднимать некуда — но след обязателен: «письмо не
            #пришло» иначе неотличимо от «человек не туда посмотрел».
            events.record("warn", "reset_mail_failed",
                          f"письмо восстановления не отправлено: {e}", email, "")

    threading.Thread(target=_worker, daemon=True).start()
    return True


@router.post("/recover/confirm")
def recover_confirm(body: dict = Body(...), request: Request = None,
                    db: Session = Depends(get_db)):
    """Завершение восстановления: смена пароля ПО ССЫЛКЕ из письма.

    Только здесь пароль реально меняется — значит право сменить его имеет ровно тот, кто
    владеет почтовым ящиком. Ссылка одноразовая и короткоживущая (см. модель PasswordReset).

    ⚠️ Ответ тут, в отличие от `/recover`, ОБЯЗАН быть внятным. Анти-энумерация здесь не
    применима и была бы вредной: токен не угадывается перебором (32 случайных байта), а
    человек, у которого ссылка просто протухла, должен понять, что делать, а не смотреть
    на «ок» при не сменившемся пароле."""
    token = (body.get("token") or "").strip()
    password = body.get("password") or ""
    if not token:
        raise HTTPException(status_code=400, detail="Ссылка неполная.")
    #Требование то же, что при создании администратора, и берётся ИЗ ОДНОГО МЕСТА
    #(`security.MIN_PASSWORD_LEN`). Строже здесь быть нельзя: человек и так пришёл сюда
    #потому, что не может войти, и отказ по правилу, которого нет больше нигде в продукте,
    #отправил бы его по кругу.
    if len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400,
                            detail=f"Пароль не короче {MIN_PASSWORD_LEN} символов")

    row = db.query(PasswordReset).filter(PasswordReset.token == token).first()
    now = datetime.now(timezone.utc)
    #Три причины отказа сводим в ОДИН текст намеренно: «такой ссылки нет» и «ссылка уже
    #использована» вместе рассказали бы, что чужой токен когда-то существовал.
    if row is None or row.used_at or not row.expires_at or row.expires_at < now.isoformat():
        audit.log(db, request, actor=(row.login if row else ""),
                  action="password.reset.reject", level="warn",
                  detail="ссылка недействительна или истекла")
        raise HTTPException(
            status_code=400,
            detail="Ссылка недействительна или истекла. Запросите восстановление заново.")

    u = db.query(User).filter(User.login == row.login, User.role == "student",
                              User.deleted == False).first()  # noqa: E712
    if u is None:
        #Аккаунт удалили между запросом и переходом. Ссылку гасим — она больше ни к чему
        #не ведёт, и оставлять её действующей на случай «а вдруг восстановят» нельзя.
        row.used_at = now.isoformat()
        db.commit()
        raise HTTPException(status_code=400, detail="Аккаунт недоступен.")

    #🔒 ВТОРОЙ ФАКТОР НА ВОССТАНОВЛЕНИИ ПАРОЛЯ (03.09.2026, требование Ярослава).
    #
    #До этой правки ссылка из письма была ЕДИНСТВЕННЫМ, что отделяло постороннего от
    #чужого аккаунта: получил доступ к почтовому ящику — сменил пароль — вошёл, и
    #второй фактор при этом не спрашивался НИ РАЗУ. То есть включённый второй фактор
    #обходился целиком через восстановление, а человек был уверен, что защищён.
    #
    #⚠️ Проверка ОДНА на весь продукт (`mfa.guard_action`), а не своя копия здесь: свой
    #ограничитель попыток и своё гашение использованного шага в каждом месте — это и
    #есть тот случай, когда однажды забудут погасить код.
    #⚠️ У кого фактора нет — проходит молча. Иначе восстановление пароля, то есть
    #последняя дверь для потерявшего доступ, потребовало бы того, чего у него нет.
    from . import mfa as _mfa
    _mfa.guard_action(db, u, str(body.get("code") or ""), request,
                      what="восстановление пароля")

    with throttle.hash_slot() as got_slot:
        #Тот же лимит одновременных хешей, что и на входе: смена пароля считает такой же
        #дорогой гибридный хеш, и без слота этот эндпоинт стал бы обходной дорогой к тому
        #же усилению нагрузки, от которого закрыт /auth/login.
        if not got_slot:
            raise HTTPException(status_code=503,
                                detail="Сервер сейчас занят. Повторите через несколько секунд.",
                                headers={"Retry-After": str(int(throttle.HASH_WAIT_S) or 1)})
        #⚠️ ЧЕРЕЗ `set_user_password`, а не `hash_password` руками. Её докстринг прямо
        #говорит: мест, где меняется пароль, семь, и новое восьмое обязано звать её —
        #иначе `password_set_at` не обновится, и админ увидит «пароль выдан полгода
        #назад» у человека, который сменил его пять минут как.
        set_user_password(u, password)
    u.updated_at = _now()
    row.used_at = now.isoformat()
    #Отзываем ВСЕ сессии: смена пароля — это в том числе реакция на «кажется, меня
    #взломали», и старый токен обязан перестать работать вместе со старым паролем.
    db.query(AuthSession).filter(AuthSession.login == row.login).update({"revoked": True})
    db.commit()
    #Смена пароля — типичная реакция на «кажется, меня взломали»: живой сокет чужой
    #вкладки обязан оборваться вместе с токеном, а не дожить до конца срока.
    _kick_sockets(u.id)
    audit.log(db, request, actor=row.login, action="password.reset.ok",
              detail="пароль изменён по ссылке из письма")
    #Пароль сменился — старые неудачные попытки по нему больше ничего не означают,
    #и держать признак «подбирают» значило бы требовать код при каждом продлении
    #сессии ещё час после того, как человек уже всё починил.
    throttle.clear_suspicion(row.login)
    return {"ok": True, "login": row.login}
