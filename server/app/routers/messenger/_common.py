"""
_common.py — общее ядро мессенджера: роутеры, реестр веб-сокетов, проверки прав
и сборка ответов. Всё, что нужно больше чем одному модулю пакета.

Пакет получился разрезом `routers/messenger.py` (3543 строки — самый большой файл
сервера) тем же приёмом и по той же причине, что `routers/web` в 3.6: один файл правили
сразу с нескольких сторон, и он стал главным источником конфликтов при слиянии.
Пути СНАРУЖИ не изменились — `from app.routers import messenger` работает как прежде,
`router` и `mod_router` те же объекты.
"""

#⚠️ Часть импортов ниже в самом `_common` не используется — они здесь как РЕ-ЭКСПОРТ:
#модули пакета берут их через `from ._common import *`. Для ruff это F401
#(«импортировано и не используется»), и для файла-ядра такое срабатывание ложное.
#Глушится в pyproject.toml (per-file-ignores) — там же, где такие же послабления
#для тестов, а не пометками в каждой строке импорта, которые пришлось бы поддерживать.


import asyncio
import re
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import (
    APIRouter, Body, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect,
)
from sqlalchemy import func
from sqlalchemy.orm import Session
from ... import audit, events, msg_limit
from ...db import get_db, SessionLocal
from ...deps import (get_current_user, require_admin, require_moderation,
                    MODERATION_ROLES)
from ...security import decode_token
from ...models import (
    Attachment,
    AuditEvent,
    AuthSession,
    Conversation, ConversationIgnore, ConversationInvite, ConversationParticipant,
    ConversationRole,
    CuratorReport, Group, Message, MessageHidden,
    BlockedUser, MessageReport, MessageReaction, MessageEdit, MessageTemplate,
    MutedUser, UserReport, SupportTicket, next_moderator_number,
    NotifyEvent, ParentLink, SubjectHours,
    UserStatus, User, UserNote, direct_conversation_id,
)
router = APIRouter(prefix="/web/messenger", tags=["messenger"])
#Кэш сводок: {(беседа, id последнего сообщения) → текст}. В ПАМЯТИ и намеренно: сводка
#дёшево пересоздаётся, переживать перезапуск ей незачем, а лишняя таблица на боевом VPS
#(1 ядро, 960 МБ) стоит дороже. Новое сообщение меняет ключ — устаревшее не отдастся.
_SUMMARY_CACHE = {}
#Модерация — админ И выделенный модератор (`require_moderation`), отдельный префикс.
#⚠️ Префикс исторический — `/web/admin/messenger`, хотя админ тут уже не единственный.
#Менять его нельзя: адрес зашит в клиенте и в уже опубликованном APK, а переименование
#ради красоты дало бы 404 там, где у людей работает модерация. Роль решает `Depends`,
#а не строка пути.
#Каждый просмотр чужой переписки пишется в аудит (152-ФЗ — см. MESSENGER-PLAN.md §3, §10).
mod_router = APIRouter(prefix="/web/admin/messenger", tags=["messenger-moderation"])


# ── WebSocket: живые события (Фаза 7) ────────────────────────────────────────────────
# Транспорт-энхансер поверх опроса: сервер шлёт участникам ЛЁГКИЙ сигнал «в беседе что-то
# изменилось», клиент по нему сразу подтягивает свежее (не парсит каждое событие — так
# надёжнее и опрос остаётся страховкой). Реестр соединений — в памяти процесса (1 воркер;
# при масштабировании на процессы понадобится общий брокер, напр. Redis pub/sub — §5 плана).
class _WSManager:
    def __init__(self):
        self._by_user: dict[str, set] = {}
        self._loop = None

    def bind_loop(self):
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    async def connect(self, uid: str, ws: WebSocket, subprotocol: str = None):
        #Если клиент авторизовался через Sec-WebSocket-Protocol, браузер требует, чтобы
        #сервер ЭХОМ вернул один из предложенных сабпротоколов — иначе рукопожатие рвётся.
        await ws.accept(subprotocol=subprotocol)
        self._by_user.setdefault(uid, set()).add(ws)

    def disconnect(self, uid: str, ws: WebSocket):
        s = self._by_user.get(uid)
        if s:
            s.discard(ws)
            if not s:
                self._by_user.pop(uid, None)

    async def send_users(self, uids, data: dict):
        for uid in uids:
            for ws in list(self._by_user.get(uid, ())):
                try:
                    await ws.send_json(data)
                except Exception:
                    pass

    def emit_users(self, uids, data: dict):
        """Вызывается из СИНХРОННЫХ REST-обработчиков (пул потоков) — планируем корутину в
        цикл событий приложения. Нет цикла (никто не подключён по WS) → просто ничего."""
        loop = self._loop
        if loop is None or loop.is_closed():   #нет живого цикла (никто по WS / цикл закрыт)
            return
        try:
            asyncio.run_coroutine_threadsafe(self.send_users(list(uids), data), loop)
        except Exception:
            pass

    async def close_user(self, uid: str, code: int = 4001):
        """Разорвать ВСЕ живые сокеты пользователя."""
        for ws in list(self._by_user.get(uid, ())):
            try:
                await ws.close(code=code)
            except Exception:
                #Сокет мог умереть сам (клиент закрыл вкладку) — это штатный исход
                #гонки, а не сбой. Важно, что из реестра он всё равно уйдёт ниже.
                pass
        self._by_user.pop(uid, None)

    def kick_user(self, uid: str):
        """🔒 Отзыв сессии обязан РВАТЬ уже открытый сокет, а не только закрывать вход.

        Проверка отзыва стоит на ПОДКЛЮЧЕНИИ, и этого мало: сокет живёт часами. После
        «Выйти» или блокировки админом украденный токен продолжал получать карту
        активности бесед (в каких чатах идёт переписка) и слать «печатает…» — ровно до
        того момента, когда клиент сам отвалится. Тексты не утекали (их отдаёт HTTP, а
        он отзыв проверяет), но чёрный список jti заводился именно ради того, чтобы
        доступ закрывался МГНОВЕННО, а не «когда-нибудь».

        Зовётся из СИНХРОННЫХ обработчиков (logout, отзыв сессий админом) — отсюда тот
        же приём с планированием корутины в цикл, что у `emit_users`."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.close_user(uid), loop)
        except Exception:
            pass


ws_manager = _WSManager()


def _participant_ids(db: Session, conv_id: str) -> list:
    return [p.user_id for p in db.query(ConversationParticipant)
            .filter(ConversationParticipant.conversation_id == conv_id).all()]


def _broadcast(db: Session, conv_id: str, kind: str = "changed"):
    """Лёгкий сигнал участникам беседы: «подтяни свежее». Никогда не роняет запрос."""
    try:
        ws_manager.emit_users(_participant_ids(db, conv_id),
                              {"type": kind, "conversation_id": conv_id})
    except Exception:
        pass


def _notify_recipients(db: Session, conv: Conversation, sender: User, skip_ids: set = None):
    """Пуш о новом сообщении получателям, которых НЕТ в приложении (по presence). Через
    RuStore Push, как уведомления об оценке. Контент НЕ уходит третьей стороне (§12 плана):
    заголовок — имя отправителя/название беседы, тело — нейтральное «Новое сообщение».
    `skip_ids` — §D8: тихо упомянутые (@!Фамилия) не получают пуш по ЭТОМУ сообщению
    (только бейдж при следующем открытии). Best-effort: сбой доставки не роняет отправку."""
    try:
        from ... import rustore_push
        online = _online_logins()
        ids = [i for i in _participant_ids(db, conv.id) if i != sender.id and i not in (skip_ids or ())]
        if not ids:
            return
        #Получатели, замьютившие ЭТУ беседу, пушей не получают (их выбор — тишина).
        muted_here = {p.user_id for p in db.query(ConversationParticipant)
                      .filter(ConversationParticipant.conversation_id == conv.id,
                              ConversationParticipant.muted == True).all()}  # noqa: E712
        ids = [i for i in ids if i not in muted_here]
        if not ids:
            return
        recips = db.query(User).filter(User.id.in_(ids)).all()
        sender_name = sender.full_name or sender.name or sender.login or "Сообщение"
        title = (conv.title if conv.kind in ("group", "channel") and conv.title else sender_name)
        body = f"{sender_name}: новое сообщение" if conv.kind in ("group", "channel") else "Новое сообщение"
        for u in recips:
            if u.login in online:
                continue                     #активному в приложении пуш не нужен
            rustore_push.notify_login(db, u.login, title, body,
                                      {"type": "message", "conversation_id": conv.id})
    except Exception:
        pass


def _notify_loud_mentions(db: Session, conv: Conversation, sender: User, mentions: list) -> None:
    """ГРОМКАЯ отметка (`/@!Фамилия`): письмо во вкладку «Уведомления» → «Система» + пуш.

    Почему письмо, а не только значок в чате: смысл громкого пинга — «увидь это, даже
    если сейчас не в мессенджере». Значок «@» в списке чатов увидит лишь тот, кто и так
    открыл вкладку сообщений, а `NotifyEvent` доезжает до всех платформ разом (веб,
    десктоп, телефон) уже существующим механизмом уведомлений.

    Текст письма собирается ЗДЕСЬ, на сервере, как и остальные уведомления (см. модель
    NotifyEvent): тон и формулировка не должны разъезжаться по платформам, а история
    обязана остаться правдивой, даже если шаблон потом поправят.

    ⚠️ Мьют беседы уважаем: замьютивший её просил тишины, и «громкость» отметки — не
    повод это обойти. Best-effort: сбой уведомления не роняет уже отправленное сообщение.
    """
    loud_ids = {m["user_id"] for m in mentions if m.get("loud")}
    if not loud_ids:
        return
    try:
        muted_here = {p.user_id for p in db.query(ConversationParticipant)
                      .filter(ConversationParticipant.conversation_id == conv.id,
                              ConversationParticipant.muted == True).all()}  # noqa: E712
        loud_ids -= muted_here
        loud_ids.discard(sender.id)          #отметить самого себя — не событие
        if not loud_ids:
            return
        sender_name = sender.full_name or sender.name or sender.login or "Собеседник"
        where = (conv.title if conv.kind in ("group", "channel") and conv.title
                 else f"переписке с {sender_name}")
        title = "Вас отметили"
        body = f"{sender_name} отметил(а) вас в чате «{where}»."
        online = _online_logins()
        for u in db.query(User).filter(User.id.in_(loud_ids)).all():
            if not u.login:
                continue
            db.add(NotifyEvent(id=str(uuid4()), login=u.login, kind="mention",
                               subject="", lesson_id="", created_at=_now(), read_at="",
                               title=title, body=body,
                               payload={"conversation_id": conv.id}))
            if u.login not in online:
                from ... import rustore_push
                rustore_push.notify_login(db, u.login, title, body,
                                          {"type": "message", "conversation_id": conv.id})
        db.commit()
    except Exception:
        db.rollback()


#Сколько непрочитанных сообщений со «@» просматриваем в поисках отметки (см. list_chats).
#Отметка ищется САМАЯ РАННЯЯ, поэтому лимит режет хвост очень старых непрочитанных — а
#там значок «@» уже не помогает: чат в таком состоянии открывают целиком, а не по кнопке.
_MENTION_SCAN_LIMIT = 200
_MAX_MSG_CHARS = 4000          #лимит длины сообщения (защита БД от «простыней»/спама)
_DEFAULT_PAGE = 50            #сколько сообщений отдаём за один запрос истории
_MAX_PAGE = 100
_MAX_NOTE_CHARS = 300           #личная заметка о человеке (UserNote) — короче «О себе»,


                                #это памятка себе, а не публичный текст


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Хелперы ──────────────────────────────────────────────────────────────────────────
def _online_logins() -> set:
    """Логины, активные прямо сейчас (по in-memory presence, окно ~90 c). Клиенты
    мессенджера опрашивают сервер каждые несколько секунд → get_current_user отмечает их
    активными, поэтому этот сигнал отражает «кто сейчас в приложении»."""
    try:
        return {r["login"] for r in events.online()}
    except Exception:
        return set()


def _safe_user(u: User, online_logins: set = None, muted: bool = False, status: dict = None) -> dict:
    """Безопасные поля пользователя для карточки/каталога (НИЧЕГО, что помогает входу в
    чужой аккаунт — см. MESSENGER-PLAN.md §9: без логина, почты, телефона, хэша, device-id).
    У студента — группа; у преподавателя — предметы, которые ведёт. online — по presence.
    `muted` (глобальный мьют модерацией) заполняем ТОЛЬКО в админ-контексте — рядовым
    пользователям состояние мьюта чужого аккаунта в каталоге ни к чему.
    `status` — §D7: {kind, custom_text} НАКЛАДКА поверх presence (dnd/studying/away);
    kind='' значит статуса нет — клиент показывает просто online/не в сети."""
    prefs = u.prefs if isinstance(u.prefs, dict) else {}
    st = status or {}
    d = {
        "id": u.id,
        "full_name": u.full_name or u.name or u.login or "",
        "role": u.role,
        "group_name": u.group_name or "",
        #День рождения («ДД.ММ», без года) — ПУБЛИЧНОЕ поле карточки: его видно и в
        #своём профиле, и в чужом. Год мы не храним вовсе, поэтому возраст отсюда не
        #вычисляется — показываем ровно то, что нужно, чтобы поздравить.
        "birthday": u.birthday or "",
        "online": bool(online_logins) and (u.login in online_logins),
        #Аватарка (обрезанная 256×256 data:URL из prefs) — её видят ВСЕ (карточка, список,
        #модерация). Это публичное «лицо» аккаунта, не чувствительное поле.
        "avatar": prefs.get("avatar", "") or "",
        #Публичная часть профиля, которую человек настраивает сам (см. routers/me.py):
        #«О себе» и цвет плашки (id пресета палитры — клиент сопоставит его с цветом).
        "bio": prefs.get("bio", "") or "",
        "profile_color": prefs.get("profile_color", "") or "",
        #Баннер карточки — гифка с CDN Klipy вместо однотонной плашки цвета. Пусто —
        #рисуем плашку по profile_color, как и раньше (баннер НЕ заменяет цвет: цвет
        #по-прежнему красит имя и подложку аватарки, гифка ложится только на полосу).
        "profile_banner": prefs.get("profile_banner", "") or "",
        #Стиль никнейма (§5.4) — тоже публичный, тем же путём, что цвет плашки: id из
        #фиксированного списка (routers/me.py::NAME_FONTS), клиент сам сопоставит его со
        #шрифтом. Пусто — уже проверено на входе (me.py::_sanitize_name_font), но берём
        #через or "" ещё раз: строки в БД могли остаться от ДО того, как поле завели.
        "name_font": prefs.get("name_font", "") or "",
        #Эффект и цвет имени (3.7) — та же публичная тройка, что и шрифт: без них у
        #собеседника имя рисовалось бы выбранным шрифтом, но без выбранного вида.
        "name_effect": prefs.get("name_effect", "") or "",
        "name_color": prefs.get("name_color", "") or "",
        "muted": bool(muted),
        "status_kind": st.get("kind", "") or "",
        "status_text": st.get("custom_text", "") or "",
    }
    if u.role == "teacher":
        d["subjects"] = u.subjects or []
    return d


_STATUS_KINDS = {"", "dnd", "studying", "away"}


def _status_map(db: Session, user_ids) -> dict:
    """§D7: карта id→{kind, custom_text} для набора пользователей (пачкой, один запрос)."""
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    rows = db.query(UserStatus).filter(UserStatus.user_id.in_(ids)).all()
    return {r.user_id: {"kind": r.kind, "custom_text": r.custom_text} for r in rows}


def _mute_row(db: Session, user_id: str):
    """Действующий мьют пользователя или None. ЕДИНСТВЕННАЯ точка, где решается вопрос
    «наказан ли он прямо сейчас», — и ровно поэтому срок проверяется здесь.

    🔥 ИСТЁКШИЙ МЬЮТ СНИМАЕТСЯ ЗДЕСЬ ЖЕ, СТРОКОЙ ИЗ БАЗЫ, а не «считается снятым».
    Оставить строку и просто не учитывать её — значит завести второе состояние: человек
    писать может, а в списке модерации он «замьючен». Разошлись бы они молча, и модератор
    снимал бы мьют, которого нет. Тот же приём, что у напоминаний (`_fire_due_reminders`):
    срабатывание по сроку проверяется ПОПУТНО, при обращении, а не фоновым планировщиком —
    на одноядерном бою лишний поток дороже пользы, а узнать о снятии мьюта можно только
    попыткой написать, то есть ровно в этот момент.

    ⚠️ Пустой `muted_until` — БЕССРОЧНО. Все мьюты, наложенные до появления срока, именно
    такие (миграция даёт им ""), и трактовать пустоту как «истёк» значило бы амнистировать
    их выкладкой."""
    row = db.query(MutedUser).filter(MutedUser.user_id == user_id).first()
    if row is None:
        return None
    if _mute_expired(row):
        db.delete(row)
        db.commit()
        return None
    return row


def _mute_expired(row) -> bool:
    """Вышел ли срок. Сравнение СТРОК ISO UTC — они сортируются лексикографически, и
    разбор `datetime` тут не нужен; обе метки ставит один и тот же `_now()`."""
    until = (getattr(row, "muted_until", "") or "").strip()
    return bool(until) and until <= _now()


def _is_muted(db: Session, user_id: str) -> bool:
    """Замьючен ли пользователь глобально (модерацией). Один индексный поиск по PK."""
    return _mute_row(db, user_id) is not None


def _muted_set(db: Session, user_ids) -> set:
    """Множество замьюченных из набора id — чтобы не бить БД по одному в списках модерации.

    ⚠️ Истёкшие строки здесь ТОЖЕ убираются: список модерации обязан показывать то же
    состояние, что и барьер записи. Разойдись они — и модератор увидел бы «замьючен» у
    человека, который уже пишет."""
    ids = {i for i in user_ids if i}
    if not ids:
        return set()
    rows = db.query(MutedUser).filter(MutedUser.user_id.in_(ids)).all()
    live, dead = set(), []
    for r in rows:
        if _mute_expired(r):
            dead.append(r)
        else:
            live.add(r.user_id)
    for r in dead:
        db.delete(r)
    if dead:
        db.commit()
    return live


def _create_flood_ticket(db: Session, user: User) -> None:
    """§D2: систематический флуд (3+ нарушений всплеска за 10 минут) — автотикет модерации,
    без ручного создания. reporter_id='system' — отличает автообнаружение от жалобы человека."""
    db.add(MessageReport(
        message_id=0, conversation_id="",
        message_snapshot="Автоматически: систематическая частая отправка сообщений.",
        reporter_id="system", reported_user_id=user.id,
        reason_code="flood", description="", created_at=_now(), status="open",
    ))
    db.commit()


def _mute_refusal(row) -> dict:
    """Машиночитаемый отказ при мьюте: до каких пор и за что.

    🔥 РАНЬШЕ ОТКАЗ БЫЛ ГЛУХИМ — «вы ограничены модерацией и временно не можете отправлять
    сообщения». Человек не знал ни срока, ни причины, и единственным его действием было
    написать в поддержку «почему не отправляется»: то есть наказание само генерировало
    обращения, которые разбирает та же модерация. Срок и причина здесь не украшение, а
    способ НЕ получить это обращение.

    ⚠️ Словарь, а не строка: клиенту нужно посчитать оставшееся время и показать его
    живым отсчётом, а разбирать для этого русский текст — та же беда, что читать число
    из подписи. Поле `message` оставлено для тех мест, где ответ показывают как есть."""
    until = (getattr(row, "muted_until", "") or "").strip()
    reason = (getattr(row, "reason", "") or "").strip()
    tail = " до " + until if until else " бессрочно"
    return {
        "message": "Вы ограничены модерацией и не можете отправлять сообщения" + tail + ".",
        "muted": True,
        "muted_until": until,                #"" = бессрочно
        "reason": reason,
        "appeal_hint": "Обжаловать можно в чате с модерацией — он остаётся открытым.",
    }


def _guard_can_write(db: Session, user: User, conv=None) -> None:
    """Единый барьер записи в мессенджер: глобальный мьют модерацией (403) → маскот-кулдаун
    (429 с эскалацией, §D2) → жёсткий анти-флуд (429, задел на скрипт, игнорирующий UI).
    Зовётся из всех точек, создающих сообщения (отправка, пересылка).

    🔥 ЧАТ С МОДЕРАЦИЕЙ ИЗ-ПОД МЬЮТА ПИШЕТСЯ ВСЕГДА, и это не поблажка. Мьют глушил ВСЁ,
    включая беседу `mod:{user.id}`, — то есть человек, которого наказали, физически не мог
    спросить «за что» и обжаловать. Наказание без возможности возразить — не модерация, а
    тупик: он пойдёт искать обходной путь (второй аккаунт, жалоба преподавателю), и первым
    об этом узнает не модератор. Анти-флуд на этот чат ДЕЙСТВУЕТ как обычно — иначе
    обжалование стало бы каналом, которым можно завалить сервер.

    ⚠️ Проверяется ВИД беседы, а не её id: строка `mod:{id}` собирается в одном месте, но
    сверять здесь её формат значило бы завести второе место, знающее этот формат.
    """
    if conv is not None and getattr(conv, "kind", "") == "moderation":
        pass                                 #обжалование — см. объяснение выше
    else:
        row = _mute_row(db, user.id)
        if row is not None:
            raise HTTPException(status_code=403, detail=_mute_refusal(row))
    mascot_wait, violations = msg_limit.mascot_check(user.id)
    if mascot_wait:
        if violations == 3:                    #ровно на переходе к «систематическому»
            _create_flood_ticket(db, user)
        raise HTTPException(
            status_code=429,
            detail={"message": "Не отправляйте сообщения так часто.",
                    "cooldown_seconds": mascot_wait, "mascot": True},
            headers={"Retry-After": str(mascot_wait)})
    wait = msg_limit.check(user.id)
    if wait:
        raise HTTPException(
            status_code=429,
            detail="Не отправляйте сообщения так часто. Подождите немного.",
            headers={"Retry-After": str(wait)})


#Каналы создают ТОЛЬКО преподаватели и админ. Канал — вещание: один пишет, сотня читает,
#и завести такой рупор студенту нельзя (исходное требование заказчика про спам в силе).
_CHANNEL_CREATOR_ROLES = ("teacher", "admin")
#ГРУППЫ студентам открыты (решение Ярослава 28.08.2026): «разрешить студентам делать
#группы между собой». Группа симметрична — в ней все пишут друг другу, собрать в неё
#можно только тех, кого человек и так может найти в каталоге, а войти в чужую группу
#по своей воле нельзя (публичны только каналы, см. join_chat). Родитель сюда не попадает:
#каталог ему студентов не показывает, а _guard_direct_allowed держит его отдельно.
_GROUP_CREATOR_ROLES = ("student", "teacher", "admin")

#Кого студент не может добавить в свою группу молча. Преподаватель и админ — это рабочее
#время и имя человека в переписке, на которую он не соглашался; их зовут ЗАЯВКОЙ.
_STAFF_ROLES = ("teacher", "admin")


def _needs_invite(actor: User, target: User) -> bool:
    """Правило одной строкой: студент не записывает сотрудника в беседу, а приглашает.

    Обратное направление (преподаватель добавляет студентов) осталось прямым — это его
    учебная группа и его полномочия. Симметрию здесь заводить не надо: она превратила бы
    каждый рабочий чат в очередь согласований."""
    return actor.role == "student" and target.role in _STAFF_ROLES


def _guard_can_create(db: Session, user: User, kind: str = "group") -> None:
    allowed = _GROUP_CREATOR_ROLES if kind == "group" else _CHANNEL_CREATOR_ROLES
    if user.role not in allowed:
        raise HTTPException(
            status_code=403,
            detail=("Каналы могут создавать только преподаватели." if kind == "channel"
                    else "Вам недоступно создание бесед."))
    if _is_muted(db, user.id):
        raise HTTPException(
            status_code=403, detail="Вы ограничены модерацией и не можете создавать беседы.")


def _invite(db: Session, conv_id: str, uid: str):
    """Непринятая заявка этому человеку в эту беседу (None — заявки нет)."""
    return (db.query(ConversationInvite)
            .filter(ConversationInvite.conversation_id == conv_id,
                    ConversationInvite.user_id == uid).first())


def _notify_invites(db: Session, conv_id: str, title: str, actor: User) -> None:
    """Письмо во вкладку «Уведомления» + пуш всем, у кого висит заявка в эту беседу.

    ⚠️ Приглашение БЕЗ уведомления бесполезно: преподаватель не открывает раздел чатов
    по расписанию, и заявка провисела бы, пока про неё не напомнят голосом. Ровно так
    уже отказывали «обещания без вызывающего» — код есть, а до человека не доходит.
    ⚠️ Сбой уведомления НЕ должен ронять создание беседы, поэтому вся функция под
    try/except: группа уже создана, а заявка видна и в разделе чатов.
    """
    try:
        rows = (db.query(ConversationInvite)
                .filter(ConversationInvite.conversation_id == conv_id).all())
        if not rows:
            return
        who = actor.full_name or actor.name or actor.login
        online = _online_logins()
        for inv in rows:
            u = db.query(User).filter(User.id == inv.user_id).first()
            if u is None or not u.login:
                continue
            #В НАШЕМ письме имена есть — оно едет по TLS в нашу же базу. Наружу, в пуш,
            #уходит только нейтральный текст, который собирает rustore_push (§3.7.6).
            db.add(NotifyEvent(
                id=str(uuid4()), login=u.login, kind="chat_invite", subject="",
                lesson_id="", created_at=_now(), read_at="",
                title="Приглашение в беседу",
                body=f"{who} приглашает вас в «{title}».",
                payload={"conversation_id": conv_id}))
            if u.login not in online:
                from ... import rustore_push
                rustore_push.notify_login(db, u.login, "", "",
                                          {"type": "chat_invite"})
        db.commit()
    except Exception:
        db.rollback()


def _parent_group_names(db: Session, parent: User) -> set:
    """Группы, к которым родитель причастен через ПОДТВЕРЖДЁННЫХ детей.

    Именно они определяют, с кем родителю вообще можно переписываться: с родителями той же
    группы и с её куратором. Группы непривязанных или неподтверждённых детей сюда не
    попадают — доступ даёт согласие студента, а не заявка сотрудника."""
    from ..parent import active_children
    return {s.group_name for s in active_children(db, parent) if s.group_name}


def _blocked_between(db: Session, a_id: str, b_id: str) -> bool:
    """Есть ли между двумя людьми блокировка — В ЛЮБУЮ СТОРОНУ.

    🔑 ИМЕННО В ЛЮБУЮ, и это не перестраховка. Блокировка одностороння по смыслу («я не
    хочу, чтобы он мне писал»), но если проверять только направление «он→я», то
    заблокировавший сохранит право писать заблокированному — то есть получит канал, в
    котором ответить ему нельзя. Односторонняя переписка без возможности возразить хуже
    и для того, и для другого; поэтому запрет симметричен, а несимметрично лишь то, КТО
    может его снять.
    """
    return db.query(BlockedUser).filter(
        ((BlockedUser.blocker_id == a_id) & (BlockedUser.blocked_id == b_id))
        | ((BlockedUser.blocker_id == b_id) & (BlockedUser.blocked_id == a_id))
    ).first() is not None


def _guard_not_blocked(db: Session, user: User, peer_id: str) -> None:
    """Отказать, если между людьми есть блокировка.

    ⚠️ ТЕКСТ ОТКАЗА ОБЩИЙ И НЕ НАЗЫВАЕТ ПРИЧИНУ. «Вас заблокировали» превратило бы
    блокировку в способ сообщить человеку, что он неприятен, — то есть в инструмент
    конфликта вместо защиты от него (то же решение у Telegram и Discord). Цена размена
    названа честно: отправитель видит неинформативный отказ и может решить, что сломался
    сервер. Это меньшее зло, и лечится оно текстом «сообщение не доставлено», а не
    признанием.
    """
    if peer_id and _blocked_between(db, user.id, peer_id):
        raise HTTPException(status_code=403, detail="Сообщение не доставлено.")


def _guard_direct_write(db: Session, conv, user: User) -> None:
    """Можно ли ПИСАТЬ в эту беседу с точки зрения блокировок.

    🔑 ОДНА ФУНКЦИЯ НА ВСЕ ТОЧКИ СОЗДАНИЯ СООБЩЕНИЙ. Их сейчас две — обычная отправка и
    пересылка, — и блокировку сначала получила только первая: переслать заблокировавшему
    можно было что угодно, то есть запрет не значил ничего. Правило, расписанное у одного
    потребителя из двух, — это наш класс «первое забытое место»; здесь забыть нечего, пока
    новая точка зовёт эту функцию, а не повторяет условие рядом.

    ⚠️ Только ЛИЧНЫЕ беседы. В группе и канале человек пишет всем сразу, и запрет означал
    бы, что один участник вычёркивает другого из учебной беседы (для этого есть модерация,
    а для «не хочу видеть» — `ConversationIgnore`).
    """
    if getattr(conv, "kind", "") != "direct":
        return
    others = (db.query(ConversationParticipant)
              .filter(ConversationParticipant.conversation_id == conv.id,
                      ConversationParticipant.user_id != user.id).all())
    for other in others:
        _guard_not_blocked(db, user, other.user_id)


def _guard_direct_allowed(db: Session, user: User, peer: User) -> None:
    """Кому вообще можно написать лично. Ограничение существует только вокруг РОДИТЕЛЕЙ.

    Родитель — не участник учебного процесса, а внешний человек с доступом к данным своего
    ребёнка. Пускать его в переписку со всем колледжем нельзя: это ни студентам, ни
    преподавателям не нужно, а поводов для конфликтов даёт много. Поэтому родителю открыты
    только два направления: родители той же группы и куратор этой группы. Симметрично: и
    написать родителю может только тот, кому родитель мог бы написать сам.

    ⚠️ Здесь же проверяется БЛОКИРОВКА — она про ту же дверь («можно ли нам переписываться»),
    и заводить для неё вторую проверку рядом значило бы однажды забыть одну из двух."""
    _guard_not_blocked(db, user, peer.id)
    if "parent" not in (user.role, peer.role):
        return
    parent, other = (user, peer) if user.role == "parent" else (peer, user)
    groups = _parent_group_names(db, parent)
    if not groups:
        raise HTTPException(
            status_code=403,
            detail="Переписка станет доступна, когда студент подтвердит доступ к журналу.")
    if other.role == "parent":
        #Двум родителям нужна ОБЩАЯ группа (их дети учатся вместе).
        if groups & _parent_group_names(db, other):
            return
        raise HTTPException(status_code=403,
                            detail="Писать можно только родителям своей группы.")
    if other.role == "teacher" and groups & set(other.curated_groups or []):
        return          #куратор группы ребёнка
    if other.role == "admin":
        return          #обращение в администрацию колледжа закрывать не за чем
    raise HTTPException(status_code=403,
                        detail="Родителю доступна переписка только с куратором и "
                               "родителями своей группы.")


def _participant(db: Session, conv_id: str, user_id: str):
    """Участие пользователя в беседе (или None). Это же — проверка доступа к беседе."""
    return (db.query(ConversationParticipant)
            .filter(ConversationParticipant.conversation_id == conv_id,
                    ConversationParticipant.user_id == user_id)
            .first())


def _require_participant(db: Session, conv_id: str, user: User) -> ConversationParticipant:
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if conv is None:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    p = _participant(db, conv_id, user.id)
    if p is None:
        raise HTTPException(status_code=403, detail="Нет доступа к этой беседе")
    return p


def _hidden_ids(db: Session, conv_id: str, user_id: str) -> set:
    """id сообщений этой беседы, скрытых у пользователя («удалить у себя»)."""
    rows = (db.query(MessageHidden.message_id)
            .join(Message, Message.id == MessageHidden.message_id)
            .filter(Message.conversation_id == conv_id, MessageHidden.user_id == user_id)
            .all())
    return {r[0] for r in rows}


def _names_for(db: Session, sender_ids) -> dict:
    """Карта id→ФИО для набора отправителей (в группах/каналах показываем автора).
    §D12: синтетический "system" (автопосты в системные каналы) — всегда «Вектор»."""
    ids = {s for s in sender_ids if s}
    if not ids:
        return {}
    out = {}
    if "system" in ids:
        out["system"] = SYSTEM_SENDER_NAME
        ids = ids - {"system"}
    if ids:
        rows = db.query(User).filter(User.id.in_(ids)).all()
        out.update({u.id: moderator_display_name(u) for u in rows})
    return out


def moderator_display_name(u) -> str:
    """Как человека подписывают в переписке.

    🔢 МОДЕРАТОР ВИДЕН НОМЕРОМ, А НЕ ФАМИЛИЕЙ (12.09.2026, требование Влада). Он
    разбирает конфликты, и получивший ограничение не должен уносить из чата фамилию
    того, кто его выдал: иначе разговор продолжится в коридоре, а не в тикете. Кто стоит
    за номером, видит ТОЛЬКО администратор — на своей странице модераторов.

    ⚠️ Правило живёт ОДНОЙ функцией и зовётся из `_names_for`, через которую проходит
    вся подпись сообщений. Повтори мы условие у каждого потребителя (лента, список
    чатов, «кто прочитал», поиск, модерация) — первое забытое место показало бы фамилию,
    и заметить это можно было бы только глазами, в чужой переписке.
    ⚠️ Номер пустой (0) — подписываем как обычно: человек мог получить роль синком со
    старой сборки, и «Модератор №0» выглядел бы поломкой.
    """
    if getattr(u, "role", "") == "moderator" and getattr(u, "mod_number", 0):
        return "Модератор №%d" % int(u.mod_number)
    return u.full_name or u.name or u.login or u.id


#Номер живёт в `models.next_moderator_number`: его зовут админка, консольный скрипт и
#эта подпись — три места, и вторая копия разошлась бы молча.


#Отправитель служебных постов (ответы Вектора, системные каналы). Не настоящий User —
#строка-заглушка, поэтому её не найти в справочнике; клиент подписывает такие сообщения
#как «Вектор» (SYSTEM_SENDER_NAME ниже).
SYSTEM_SENDER_ID = "system"
#§D8 + «пинги»: три формы отметки, они же — то, что подставляет автодополнение по «/@».
#  @Фамилия      — обычная отметка: подсветка + пуш офлайн-получателю (как было);
#  /@Фамилия     — ТИХАЯ: только подсветка и значок «@» в списке чатов, без пуша;
#  /@!Фамилия    — ГРОМКАЯ: звуковой сигнал у получателя + письмо во вкладку «Система»
#  /!@Фамилия      («Вас отметили в чате …») + пуш. Обе перестановки «!» принимаются:
#                  вспомнить порядок двух подряд идущих символов невозможно, а ошибиться
#                  в громкости отметки — неприятно (либо звонит зря, либо молчит нужное).
#Историческая форма @!Фамилия (тихая, без слэша) сохранена — она уже в переписках.
_MENTION_RE = re.compile(r"(/)?(?:@(!?)|(!)@)([A-Za-zА-Яа-яЁё]+)")


def _parse_mentions(db: Session, body: str, participant_ids) -> list:
    """§D8: находит отметки среди УЧАСТНИКОВ беседы (формы — см. _MENTION_RE выше).

    Сопоставление — по ПЕРВОМУ слову ФИО (фамилии), регистронезависимо, без разбора
    склонений: осознанно просто, как и остальной MVP мессенджера (см. MESSENGER-PLAN
    §D8 — точный морфологический разбор там же не предполагался).

    Возвращает [{user_id, silent, loud}]: `silent` глушит пуш (как раньше), `loud`
    дополнительно даёт звук и письмо в «Систему». Одного поля мало — «громко» это не
    «не тихо»: обычная @Фамилия остаётся посередине (пуш есть, звонка нет)."""
    ids = [i for i in participant_ids if i]
    if not ids or "@" not in body:
        return []
    rows = db.query(User).filter(User.id.in_(ids)).all()
    surnames = {}
    for u in rows:
        first = (u.full_name or u.name or "").split(" ", 1)[0].strip().lower()
        if first:
            surnames.setdefault(first, u.id)
    out, seen = [], set()
    for m in _MENTION_RE.finditer(body):
        slash, bang_after, bang_before, name = m.group(1), m.group(2), m.group(3), m.group(4)
        uid = surnames.get(name.lower())
        if not uid or uid in seen:
            continue
        seen.add(uid)
        bang = bool(bang_after or bang_before)
        #«!» без слэша — историческая ТИХАЯ форма (@!Фамилия), её смысл не меняем: в
        #переписках уже есть такие сообщения, и переворачивать их задним числом нельзя.
        loud = bool(slash) and bang
        silent = (bang and not slash) or (bool(slash) and not bang)
        out.append({"user_id": uid, "silent": silent, "loud": loud})
    return out


def _att_map(db, messages) -> dict:
    """Метаданные вложений для пачки сообщений: ОДИН запрос на всю ленту.

    ⚠️ Не подгружаем вложение внутри `_msg_out`: он вызывается на каждое сообщение, и
    запрос оттуда дал бы классический N+1 — сотня обращений к базе на одну открытую
    ленту. На боевой машине с одним ядром это заметно сразу.
    """
    ids = [m.attachment_id for m in messages if getattr(m, "attachment_id", "")]
    if not ids:
        return {}
    rows = db.query(Attachment).filter(Attachment.id.in_(set(ids))).all()
    return {a.id: {"id": a.id, "name": a.name, "size": a.size, "mime": a.mime}
            for a in rows if a.ready}


def _msgs_out(db, rows, me_id: str, names: dict) -> list:
    """Сериализовать пачку сообщений вместе с вложениями."""
    amap = _att_map(db, rows)
    return [_msg_out(m, me_id, names.get(m.sender_id, ""),
                     amap.get(getattr(m, "attachment_id", "") or ""))
            for m in rows]


def _msg_out(m: Message, me_id: str = "", sender_name: str = "", att: dict = None) -> dict:
    """Сериализация сообщения для клиента. Удалённое-у-всех отдаём тумбстоуном (без текста).
    `mine` вычисляет сервер (клиент своего id не знает — в JWT/сторе только логин+роль).
    `sender_name` нужен в группах/каналах (в личном чате имя не показываем)."""
    deleted = bool(m.deleted_at)
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "sender_id": m.sender_id,
        "sender_name": sender_name,
        "mine": bool(me_id) and m.sender_id == me_id,
        "kind": getattr(m, "kind", "") or "text",          #§D6: text | system
        "body": "" if deleted else (m.body or ""),
        "body_format": getattr(m, "body_format", "") or "markdown",  #§D1
        "created_at": m.created_at,
        "edited_at": "" if deleted else (m.edited_at or ""),
        "deleted": deleted,
        "reply_to_id": m.reply_to_id or None,
        #«Ответить с цитатой»: выделенный кусок оригинала (снимок). Пусто — обычный ответ,
        #клиент показывает начало исходного сообщения, как и раньше.
        #⚠️ У тумбстоуна цитаты нет: «удалил у всех» обязано убирать и текст, который
        #процитировали ИЗ этого сообщения, иначе удаление обходится ответом на самого себя.
        "reply_quote": "" if deleted else (getattr(m, "reply_quote", "") or ""),
        #Вложение — метаданные, БЕЗ ссылки. Ссылка выдаётся отдельной ручкой, живёт
        #минуты и только участнику: положи её сюда — и она уедет пересылкой в чужой чат
        #вместе с текстом сообщения, а срок у неё был бы вечный.
        #⚠️ У тумбстоуна вложения нет: «удалил у всех» обязано убирать и файл из виду.
        "attachment": None if deleted else att,
        #Метка отправителя (§D10). Клиент рисует своё сообщение СРАЗУ, не дожидаясь
        #ответа сервера, и ему нужно опознать в ленте именно свой черновик: по WS то же
        #сообщение может прийти РАНЬШЕ ответа на POST, и без метки в беседе появились бы
        #две одинаковые строки.
        #⚠️ ОТДАЁМ ТОЛЬКО СВОЮ метку. Чужая получателю не нужна ни для чего, а раздавать
        #её всем участникам — значит без надобности вынести наружу поле, которое до сих
        #пор жило только в базе. Парная защита стоит на приёме (дедуп ищет по паре
        #«отправитель + метка», см. messages.py).
        "client_nonce": ((getattr(m, "client_nonce", "") or "")
                         if (bool(me_id) and m.sender_id == me_id) else ""),
        "pinned": bool(m.pinned) and not deleted,
        #Шапка «Переслано от …» (снимок имени источника):
        "forwarded_from": (m.fwd_sender_name or "") if (m.fwd_from_sender_id and not deleted) else None,
        "mentions": [] if deleted else (getattr(m, "mentions", None) or []),   #§D8
        "reactions": [],   #§D3: заполняется в списке сообщений (_attach_reactions)
        "reply_count": 0,  #Треды: заполняется в списке сообщений (_attach_reply_counts)
        #§12: kind="report" — метаданные кнопки «Отчёт №N» (заполняется _attach_report_meta).
        "report": None,
        #kind="activity"/"board" — карточка активности и сохранённая доска
        #(PLAN-ACTIVITIES §10). Тот же приём, что у отчёта: в теле сообщения лежит только
        #id, а объект подмешивается при выдаче — статус активности меняется ПОСЛЕ отправки
        #(идёт → завершена), и переотправлять ради этого сообщение незачем.
        "activity": None,
        "board": None,
    }


def _attach_reactions(db: Session, msgs: list, me_id: str) -> None:
    """§D3: догрузить реакции пачкой для списка сериализованных сообщений (по id) и
    сгруппировать: [{emoji, count, mine}]. Один запрос на всю страницу."""
    ids = [m["id"] for m in msgs]
    if not ids:
        return
    rows = db.query(MessageReaction).filter(MessageReaction.message_id.in_(ids)).all()
    by_msg = {}
    for r in rows:
        grp = by_msg.setdefault(r.message_id, {})
        cell = grp.setdefault(r.emoji, {"emoji": r.emoji, "count": 0, "mine": False})
        cell["count"] += 1
        if r.user_id == me_id:
            cell["mine"] = True
    for m in msgs:
        m["reactions"] = list(by_msg.get(m["id"], {}).values())


def _attach_reply_counts(db: Session, msgs: list) -> None:
    """Треды (docs/MESSENGER-ADDON-PLAN-GPT-SMART.md §3.3): число ответов на сообщение —
    для бейджа «N ответов». Своей сущности треда не заводим: ветка = сообщения этой же
    беседы с reply_to_id == id родителя (переиспользуем уже существующее поле)."""
    ids = [m["id"] for m in msgs]
    if not ids:
        return
    rows = (db.query(Message.reply_to_id, func.count(Message.id))
            .filter(Message.reply_to_id.in_(ids), Message.deleted_at == "")
            .group_by(Message.reply_to_id).all())
    counts = {rid: cnt for rid, cnt in rows}
    for m in msgs:
        m["reply_count"] = counts.get(m["id"], 0)


def _attach_report_meta(db: Session, msgs: list) -> None:
    """§12: у сообщений kind="report" тело — просто report_id; номер/архивность кнопки
    «Отчёт №N» досчитываем пачкой (тело сообщения НЕ меняем — statuses «архивный» может
    смениться ПОСЛЕ отправки, а сообщение переотправлять незачем)."""
    ids = [m["body"] for m in msgs if m["kind"] == "report" and m["body"]]
    if not ids:
        return
    rows = db.query(CuratorReport).filter(CuratorReport.id.in_(ids)).all()
    by_id = {r.id: r for r in rows}
    for m in msgs:
        if m["kind"] != "report":
            continue
        r = by_id.get(m["body"])
        if r is not None:
            m["report"] = {"id": r.id, "seq": r.seq, "group": r.group_name,
                           #Дата границы — на кнопке: два отчёта подряд иначе неразличимы.
                           "cutoff_date": r.cutoff_date or "",
                           "archived": _report_archived(r, db)}


def _report_archived(rep, db) -> bool:
    """Архивна ли кнопка отчёта куратора.

    🔥 Считаем по КАЛЕНДАРЮ, а не только по сохранённому флагу. Флаг ставил ровно один
    код — перевод термина вперёд (`/admin/term/rollover`), а его сдвиг в будущее теперь
    запрещён: он дважды уводил боевой сервер на год вперёд, и у группы оказывались
    предметы двух курсов сразу. Если оставить только флаг, отчёты перестали бы
    архивироваться вовсе и «Отчёт №3» за прошлый семестр выглядел бы действующим.

    Сохранённый флаг уважаем (его могли поставить раньше) — он лишь дополняется
    честным сравнением периода отчёта с текущим."""
    if bool(getattr(rep, "archived", False)):
        return True
    #⚠️ `webdata` в этом модуле импортируется ВНУТРИ функций, а не на уровне файла —
    #модульного `W` тут нет, и обращение к нему дало бы NameError уже в рантайме, при
    #полностью зелёной компиляции (эта грабля в проекте уже стоила отдельного разбора).
    from ... import webdata as W
    try:
        cy, cs = W.current_term(W.load_config(db))
        return (int(str(rep.year).split("/")[0]), int(rep.semester)) <                (int(str(cy).split("/")[0]), int(cs))
    except (TypeError, ValueError, AttributeError, IndexError):
        return False


def _poll_cell(a, viewer_id: str, host_name: str = "") -> dict:
    """Опрос ДЛЯ ЛЕНТЫ: голосуют прямо в сообщении, как в Telegram.

    🔒 Распределение голосов кладём ТОЛЬКО создателю либо когда автор явно включил
    открытые голоса. Прочим — свой выбор и общее число проголосовавших: число ничего не
    раскрывает и снимает вопрос «дошло ли», а чужой голос без согласия автора опроса
    показывать нельзя."""
    from ...routers import activities as _act
    snap = _act.activity_state.get(a.id)
    live = (snap or {}).get("payload") or {}
    params = a.params or {}
    #Идёт — берём живые голоса; завершён — снимок, сохранённый при завершении. Без
    #снимка завершённый опрос показывал бы «проголосовало: 0», хотя голосовали все.
    votes = live.get("votes") if snap is not None else (params.get("final_votes") or {})
    votes = votes or {}
    opts = list(params.get("options") or [])
    public = bool(params.get("public_votes"))
    mine = votes.get(viewer_id)
    cell = {"question": params.get("question") or "", "options": opts,
            "my_choice": mine if mine is not None else None,
            "voted_count": len(votes), "ends_at": live.get("ends_at") or "",
            "public_votes": public,
            #Кто спросил. В ленте опрос выглядит сообщением «от Вектора» (его постит
            #система), и без подписи было непонятно, чей это вопрос — старосты, куратора
            #или преподавателя. От этого зависит, отвечать ли на него вообще.
            "host_name": host_name}
    #Кому видно распределение. Три двери, и каждая открыта заранее объявленным правилом:
    #  • автору — всегда (он и спрашивал);
    #  • всем сразу — если автор включил открытые голоса;
    #  • всем ПОСЛЕ завершения — если автор не запретил раскрытие итога.
    #⚠️ Третья дверь проверяет `status`, а не «время вышло»: истёкший, но не закрытый
    #опрос итог НЕ раскрывает. Закрывает такие `_sweep_expired_polls`, и до этого момента
    #голоса ещё могут меняться — раскрыть их раньше значило бы подсветить лидера в живом
    #опросе, а это тянет за собой голоса остальных.
    revealed = a.status != "running" and bool(params.get("reveal_results", True))
    if public or viewer_id == a.host_id or revealed:
        cell["tally"] = [sum(1 for v in votes.values() if int(v) == i) for i in range(len(opts))]
    cell["reveal_results"] = bool(params.get("reveal_results", True))
    return cell


def _attach_activity_meta(db: Session, msgs: list, viewer_id: str = "") -> None:
    """kind="activity" — карточка-кнопка активности; kind="board" — сохранённая доска.

    Как и у отчёта, в теле сообщения лежит ТОЛЬКО id: статус активности меняется после
    отправки (идёт → завершена), а переотправлять сообщение ради этого незачем — клиент
    гасит кнопку по `status`."""
    from ...models import Activity, BoardArtifact
    #Опрос — тоже активность, просто рисуется в ленте кнопками, а не ссылкой.
    act_ids = [m["body"] for m in msgs if m["kind"] in ("activity", "poll") and m["body"]]
    board_ids = [m["body"] for m in msgs if m["kind"] == "board" and m["body"]]
    acts = ({a.id: a for a in db.query(Activity).filter(Activity.id.in_(act_ids)).all()}
            if act_ids else {})
    #ФИО авторов опросов — ОДНИМ запросом на всю страницу, а не по строке на сообщение:
    #в ленте опросов бывает много, и N+1 здесь превратился бы в десятки запросов ради
    #одной подписи.
    host_names = _names_for(db, [a.host_id for a in acts.values() if a.kind == "poll"])
    boards = ({b.id: b for b in db.query(BoardArtifact)
               .filter(BoardArtifact.id.in_(board_ids)).all()} if board_ids else {})
    #⚠️ Сверяем беседу: объект догружаем ТОЛЬКО там, где он и живёт. Без этой проверки
    #пересланная (или вручную созданная с чужим id) карточка отдавала бы заголовок и
    #статус активности людям, которые к её беседе отношения не имеют. Пересылку таких
    #сообщений мы запретили отдельно, но одного запрета мало: правило «чужое не
    #показываем» должно держаться и там, где строка уже как-то оказалась в ленте.
    for m in msgs:
        if m["kind"] in ("activity", "poll"):
            a = acts.get(m["body"])
            if a is not None and a.conversation_id == m.get("conversation_id"):
                m["activity"] = {"id": a.id, "kind": a.kind, "title": a.title or "",
                                 "status": a.status, "started_at": a.started_at or "",
                                 "finished_at": a.finished_at or ""}
                if a.kind == "poll":
                    m["activity"].update(
                        _poll_cell(a, viewer_id, host_names.get(a.host_id, "")))
        elif m["kind"] == "board":
            b = boards.get(m["body"])
            if b is not None and b.conversation_id == m.get("conversation_id"):
                m["board"] = {"id": b.id, "title": b.title or "", "sheet": b.sheet or "blank",
                              "strokes_count": len(b.strokes or [])}


def _attach_rich_meta(db: Session, msgs: list, viewer_id: str = "") -> None:
    """ЕДИНАЯ точка догрузки всего, что у сообщения лежит в теле одним лишь id.

    ⚠️ Заведена намеренно вместо перечисления вызовов по местам. Раньше
    `_attach_report_meta` звался из ПЯТИ мест, и добавление второго такого вида
    (активности) означало не забыть дописать рядом ещё пять строк. Забыть одну — значит
    отдать клиенту сырой `act:9f3…` вместо кнопки, причём именно в том месте, куда
    заглядывают реже всего (превью последнего сообщения в списке чатов). Тот же приём и
    та же причина, что у обёртки `_safe()` в сборе дельты синка: правило живёт в ОДНОМ
    месте, и новый вид сообщения нельзя забыть подключить."""
    if not msgs:
        return
    _attach_report_meta(db, msgs)
    _attach_activity_meta(db, msgs, viewer_id)
    _blank_quotes_of_deleted(db, msgs)


def _blank_quotes_of_deleted(db: Session, msgs: list) -> None:
    """Цитата УСТУПАЕТ удалению оригинала — иначе «удалить у всех» обходится цитатой.

    🔒 Снимок цитаты сделан ради ПРАВОК: отредактированный оригинал не должен менять то,
    на что человек отвечал. Но удаление — другое событие: автор убрал сказанное у всех, и
    если процитированный кусок продолжает висеть в чужом ответе, гарантия удаления
    ничего не значит. Проверять это на КЛИЕНТЕ мало: оригинал может быть старше
    подгруженной страницы (лента отдаёт по 50), и тогда клиенту просто нечего сверять.

    Один запрос на страницу, и только по тем сообщениям, где цитата реально есть.
    """
    ids = {m.get("reply_to_id") for m in msgs if m.get("reply_quote") and m.get("reply_to_id")}
    if not ids:
        return
    gone = {row.id for row in db.query(Message.id)
            .filter(Message.id.in_(ids), Message.deleted_at != "").all()}
    if not gone:
        return
    for m in msgs:
        if m.get("reply_to_id") in gone:
            m["reply_quote"] = ""


#§D3: белый список эмодзи-реакций (как в плане). Ничего сверх — предсказуемо и безопасно.
_REACTIONS = {"👍", "✅", "❤️", "😂", "👀", "🔥", "💯", "❓", "📌"}
#§D6: разделитель полей системного события — НЕ двоеточие: id участника сам содержит ':'
#(формат stud:{login}/teach:{login}), и простой split(':') на клиенте расклеивал бы его на
#куски (баг был найден при добавлении Qt-рендера и относится и к вебу тоже). \x1f (Unit
#Separator) — непечатный символ, набрать с клавиатуры невозможно, коллизий не бывает.
_SYS_SEP = "\x1f"


def _system(db: Session, conv_id: str, event: str, *args: str) -> Message:
    """§D6: вставить СИСТЕМНОЕ сообщение в ленту (вступил/вышел/закрепил/…). Тело —
    'событие\\x1fаргумент\\x1f...', клиент рендерит по-человечески. Не шлёт пуш, но
    триггерит WS-обновление (см. вызывающий код — broadcast делает он сам).
    Возвращает созданную строку — нужна командам (/mute), которые отдают её клиенту
    как результат отправки, а не только «побочным эффектом»."""
    body = _SYS_SEP.join((event, *args))
    m = Message(conversation_id=conv_id, sender_id="system", body=body,
               created_at=_now(), kind="system")
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _peer_of_direct(db: Session, conv_id: str, me_id: str):
    """Второй участник личного чата (для заголовка/карточки). None, если не найден."""
    other = (db.query(ConversationParticipant)
             .filter(ConversationParticipant.conversation_id == conv_id,
                     ConversationParticipant.user_id != me_id)
             .first())
    if other is None:
        return None
    return db.query(User).filter(User.id == other.user_id).first()


# ── Каталог пользователей и поиск по ФИО ─────────────────────────────────────────────
_PAGE_USERS = 30


def _may_list_parent(db: Session, viewer: User, parent: User) -> bool:
    """Показывать ли этого родителя в каталоге у данного пользователя."""
    if viewer.role == "admin":
        return True
    groups = _parent_group_names(db, parent)
    if viewer.role == "teacher":
        return bool(groups & set(viewer.curated_groups or []))
    if viewer.role == "parent":
        return bool(groups & _parent_group_names(db, viewer))
    return False


# ── Шаблоны быстрых ответов преподавателя ────────────────────────────────────────────
# docs/MESSENGER-ADDON-PLAN-GPT.md «Шаблоны сообщений преподавателя»: часто используемые
# фразы одним кликом («Работа принята», «Исправьте ошибки»). Личный набор, НЕ AI —
# преподаватель сам пишет текст один раз и переиспользует. Лимит — защита от «простыней».
_MAX_TEMPLATES = 20


# ── Открыть/создать личный чат ───────────────────────────────────────────────────────
def _ensure_direct(db: Session, user: User, peer: User) -> str:
    """id личного чата пары (создать, если его ещё нет). Идемпотентно: id детерминирован.
    ⚠️ Границы переписки (_guard_direct_allowed) проверяет ВЫЗЫВАЮЩИЙ — тут только беседа."""
    conv_id = direct_conversation_id(user.id, peer.id)
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if conv is None:
        now = _now()
        db.add(Conversation(id=conv_id, kind="direct", created_at=now))
        db.add(ConversationParticipant(conversation_id=conv_id, user_id=user.id,
                                       role="member", joined_at=now))
        db.add(ConversationParticipant(conversation_id=conv_id, user_id=peer.id,
                                       role="member", joined_at=now))
        db.commit()
    return conv_id


def _neg_key(iso: str):
    """Ключ сортировки «по убыванию времени» для строковых ISO-меток (позже → выше)."""
    return "" if not iso else "".join(chr(255 - b) for b in iso.encode("utf-8"))


def _saved_conv_id(user_id: str) -> str:
    return f"saved:{user_id}"


def _ensure_saved(db: Session, user: User) -> str:
    """id «Избранного» пользователя (создать лениво при первом обращении)."""
    conv_id = _saved_conv_id(user.id)
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if conv is None:
        now = _now()
        db.add(Conversation(id=conv_id, kind="saved", title="Избранное",
                            owner_id=user.id, created_at=now))
        db.add(ConversationParticipant(conversation_id=conv_id, user_id=user.id,
                                       role="owner", joined_at=now, pinned=True))
        db.commit()
    return conv_id


def _visible_messages_query(db: Session, conv_id: str, part, user_id: str):
    """Базовая выборка ВИДИМЫХ пользователю сообщений беседы (границы очистки и скрытия).

    Вынесено из search_messages, чтобы умный поиск и сводка не собирали те же условия
    заново: забытая граница `cleared_upto_id` означала бы показ переписки, которую человек
    у себя удалил."""
    hidden = _hidden_ids(db, conv_id, user_id)
    qq = (db.query(Message)
          .filter(Message.conversation_id == conv_id, Message.deleted_at == "",
                  Message.kind == "text"))
    if part.cleared_upto_id:
        qq = qq.filter(Message.id > part.cleared_upto_id)
    if part.cleared_at:                      #legacy-строки (очищены до id-границы)
        qq = qq.filter(Message.created_at > part.cleared_at)
    if hidden:
        qq = qq.filter(~Message.id.in_(hidden))
    return qq


# ── §19. Напоминания из сообщений ───────────────────────────────────────────────────
# Разбор даты ДЕТЕРМИНИРОВАННЫЙ (reminder_parse.py в корне репо), без модели — несмотря на
# название фичи в плане. Причина та же, что у записи оценок голосом: «завтра в 15:00»
# регулярка разбирает надёжнее, чем LLM, а модель ошибается МОЛЧА — напоминание, съехавшее
# на день, хуже ненайденного, потому что на него уже положились. Плюс это приватность:
# текст личной переписки никуда не уезжает.

def _reminder_out(r) -> dict:
    return {"id": r.id, "conversation_id": r.conversation_id, "message_id": r.message_id,
            "text": r.text, "remind_at": r.remind_at, "fired_at": r.fired_at or ""}


_VECTOR_CMD_RE = re.compile(r"^/vector(?:@\w+)?\s*(.*)$", re.IGNORECASE | re.DOTALL)
#Сколько последних заметок отдаём Вектору как контекст и сколько символов максимум.
#Ограничение не косметическое: длинный контекст — это и деньги за токены, и риск, что
#модель начнёт отвечать по заметке вместо журнала.
_CTX_MESSAGES = 20
_CTX_CHARS = 1500


def _mask_names(db: Session, text: str) -> str:
    """Заменяет ФИО реальных пользователей на «Студент»/«Преподаватель».

    ⚠️ ОБЯЗАТЕЛЬНО перед отправкой в облачную модель: заметки — свободный текст, и в них
    легко попадают фамилии одногруппников. Продукт публично обещает, что ПДн в облако не
    уходят (README, §6), и заметки — не исключение. Маскируем от длинных совпадений к
    коротким, иначе «Иванов» съел бы «Иванов Иван» и остаток имени утёк бы."""
    rows = db.query(User).filter(User.deleted == False).all()          # noqa: E712
    pairs = []
    for u in rows:
        label = "Преподаватель" if u.role == "teacher" else "Студент"
        for name in (u.full_name, u.surname, u.name):
            if name and len(name.strip()) >= 3:
                pairs.append((name.strip(), label))
    for name, label in sorted(pairs, key=lambda p: -len(p[0])):
        if name.lower() in text.lower():
            text = re.sub(re.escape(name), label, text, flags=re.IGNORECASE)
    return text


def _saved_context(db: Session, conv_id: str, user: User) -> str:
    """Последние заметки «Избранного» — контекст для команды /vector.

    Зачем: без него вопрос «а когда это?» или «что я писал про экзамен» опирается только на
    формулировку самого вопроса. Берём ТОЛЬКО этот личный чат (он виден одному человеку),
    свежие сообщения, без удалённых, и обезличиваем перед отправкой в модель."""
    part = _participant(db, conv_id, user.id)
    q = db.query(Message).filter(Message.conversation_id == conv_id,
                                 Message.deleted_at == "")
    if part is not None and part.cleared_upto_id:
        q = q.filter(Message.id > part.cleared_upto_id)      #очищенное не воскрешаем
    rows = q.order_by(Message.id.desc()).limit(_CTX_MESSAGES).all()
    rows.reverse()
    lines = []
    for m in rows:
        body = (m.body or "").strip()
        #Сами команды в контекст не тянем — это шум, а не заметка.
        if not body or _VECTOR_CMD_RE.match(body):
            continue
        who = "Вектор" if m.sender_id == "system" else "Я"
        lines.append(f"{who}: {body}")
    text = "\n".join(lines)[-_CTX_CHARS:]
    return _mask_names(db, text) if text else ""


def _is_vector_message(db: Session, conv_id: str, message_id: int) -> bool:
    """Является ли сообщение ответом Вектора в ЭТОЙ беседе (отправитель — 'system')."""
    if not message_id:
        return False
    row = (db.query(Message)
           .filter(Message.id == message_id, Message.conversation_id == conv_id).first())
    return row is not None and row.sender_id == SYSTEM_SENDER_ID


def _handle_vector_command(db: Session, conv_id: str, body: str, user: User,
                           reply_to: int = 0) -> None:
    """`docs/MESSENGER-ADDON-PLAN-GPT.md`: «AI-поиск по смыслу» — реализован не как отдельная
    embedding-инфраструктура (её негде держать на 1-ядерном VPS), а как переиспользование УЖЕ
    существующего анти-галлюцинационного Вектора (`web.py::answer_vector_question`, тот же
    код, что у `/web/vector/ask`). try/except: сбой ИИ-ответа не должен мешать самой
    отправке сообщения — она уже прошла и закоммичена выше.

    ⚠️ ТОЛЬКО в «Избранном» (личный чат с собой). Раньше команда работала в ЛЮБОМ чате, и
    ответ Вектора публиковался всем участникам: в общей беседе это и шум, и утечка контекста
    вопроса, а роль-скоуп ответа считается по СПРОСИВШЕМУ — соседи по чату увидели бы
    выборку, к которой сами доступа не имеют. Личный чат снимает оба вопроса сразу.

    ДВА способа спросить, оба только в «Избранном»:
      • `/vector <вопрос>` — как раньше, начало разговора;
      • ОТВЕТ (reply) на реплику Вектора — продолжение цепочки БЕЗ повторного префикса.
    Второй способ добавлен потому, что диалог из нескольких уточнений требовал писать
    «/vector» на каждой строке, хотя адресат из ответа и так однозначен. Отвечать на
    СВОЁ сообщение при этом ничего не запускает: адресат там не Вектор, и обычная
    цитата в личных заметках не должна внезапно уходить в модель."""
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if conv is None or conv.kind != "saved":
        return
    match = _VECTOR_CMD_RE.match(body.strip())
    if match:
        question = match.group(1).strip()
    elif _is_vector_message(db, conv_id, reply_to):
        question = body.strip()          #ответ на реплику Вектора — весь текст и есть вопрос
    else:
        return
    if not question:
        return
    try:
        from ..web import answer_vector_question, user_ui_locale
        answer = answer_vector_question(question, user, db,
                                        context=_saved_context(db, conv_id, user),
                                        locale=user_ui_locale(user))
        text = (answer.get("text") or "").strip()
        if text:
            _post_system_channel_message(db, conv_id, text)
    except Exception:
        pass


# ── Прочтение ────────────────────────────────────────────────────────────────────────
def _mark_read(db: Session, conv_id: str, user_id: str, upto_iso: str) -> None:
    p = _participant(db, conv_id, user_id)
    if p is not None and upto_iso and upto_iso > (p.last_read_at or ""):
        p.last_read_at = upto_iso
        db.commit()
        #Живой сигнал собеседнику: галочка «отправлено→прочитано» в ЛС должна смениться,
        #пока оба ещё в чате, а не только при следующем входе (см. ChatThread.vue).
        _broadcast(db, conv_id)


def _unhide_participants(db: Session, conv_id: str) -> None:
    """Новая активность возвращает беседу тем, кто «удалил» её у себя: снимаем флаг hidden.
    Метку cleared_at НЕ трогаем — старая история для них так и остаётся скрытой."""
    (db.query(ConversationParticipant)
     .filter(ConversationParticipant.conversation_id == conv_id,
             ConversationParticipant.hidden == True)                      # noqa: E712
     .update({"hidden": False}, synchronize_session=False))
    db.commit()


# ── Действия над сообщением (см. MESSENGER-PLAN.md §6) ────────────────────────────────
_MANAGER_ROLES = ("owner", "admin")
_WRITER_ROLES = ("owner", "admin", "writer")
# ── Роли и права внутри беседы (кастомные роли групп/каналов) ─────────────────────────
# Фиксированный небольшой набор прав — ровно то, что просили (кик, выдача ролей, две
# модераторские команды), без раздувания в гранулярную матрицу.
#`activities` — запуск активностей в беседе (docs/done/PLAN-ACTIVITIES.md §3). Системная роль
#teacher/admin даёт его и БЕЗ роли беседы (проверка — в routers/activities._require_can_run):
#преподаватель ведёт занятие, а не администрирует чат, и просить владельца чата выдать
#ему роль ради этого странно. Здесь право нужно, чтобы владелец мог выдать его старосте.
_ALL_PERMISSIONS = ("kick", "manage_roles", "cmd_mute", "cmd_clear", "activities")
#Дефолт для БИЛДОВЫХ ролей, когда участнику не назначена кастомная ConversationRole —
#эквивалент того, что раньше жёстко проверял _MANAGER_ROLES (owner/admin), чтобы уже
#существующие беседы без единой кастомной роли продолжали работать ровно как раньше.
_DEFAULT_ROLE_PERMISSIONS = {"owner": set(_ALL_PERMISSIONS), "admin": set(_ALL_PERMISSIONS)}


def _permissions_for(db: Session, part: ConversationParticipant) -> set:
    """Права участника В ЭТОЙ беседе. owner — всегда полный набор (создателя не разжаловать
    этим путём). Иначе — кастомная роль, если назначена; иначе — дефолт по билдовой role."""
    if part.role == "owner":
        return set(_ALL_PERMISSIONS)
    if part.custom_role_id:
        cr = db.query(ConversationRole).filter(ConversationRole.id == part.custom_role_id).first()
        if cr is not None:
            return set(cr.permissions or [])
    return set(_DEFAULT_ROLE_PERMISSIONS.get(part.role, ()))


def _conversation(db: Session, conv_id: str) -> Conversation:
    c = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    return c


def _message_in_conv(db: Session, mid: int) -> Message:
    m = db.query(Message).filter(Message.id == mid).first()
    if m is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    return m


def _can_delete_for_all(part: ConversationParticipant, m: Message, user_id: str) -> bool:
    """Удалить у всех: автор всегда; в группе/канале — ещё owner/admin (модерируют чужое)."""
    return m.sender_id == user_id or part.role in _MANAGER_ROLES


def _can_pin(part: ConversationParticipant, conv: Conversation) -> bool:
    """Закреплять: в личном чате — оба; в группе — owner/admin; в канале — писатели."""
    if conv.kind == "direct":
        return True
    if conv.kind == "channel":
        return part.role in _WRITER_ROLES
    return part.role in _MANAGER_ROLES


# ── Жалоба = тикет модерации (см. MESSENGER-PLAN.md §6.7, §10) ────────────────────────
_REASONS = {"spam", "harassment", "threats", "fraud", "illegal", "flood", "other"}


# ── Группы и каналы (см. MESSENGER-PLAN.md §5) ───────────────────────────────────────
def _require_manager(db: Session, conv_id: str, user: User) -> ConversationParticipant:
    p = _require_participant(db, conv_id, user)
    if p.role not in _MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return p


def _require_permission(db: Session, conv_id: str, user: User, permission: str) -> ConversationParticipant:
    """Как _require_manager, но по гранулярному праву (kick/manage_roles/cmd_mute/
    cmd_clear) — учитывает кастомную роль участника, а не только билдовую owner/admin."""
    p = _require_participant(db, conv_id, user)
    if permission not in _permissions_for(db, p):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return p


# ── Кастомные роли беседы (§ролей) ─────────────────────────────────────────────────────
_ROLE_TEMPLATES = [
    {"name": "Студент", "permissions": []},
    {"name": "Староста", "permissions": ["kick", "cmd_mute", "cmd_clear"]},
    {"name": "Преподаватель", "permissions": ["kick", "manage_roles", "cmd_mute", "cmd_clear"]},
]
# ── §D12: автоматические системные каналы (оценки/объявления/расписание) ─────────────
# Превращают мессенджер из «просто чата» в хаб колледжа: авто-канал появляется у студента
# сам, без ручного создания. Технически это ОБЫЧНЫЙ kind='channel' (переиспользует всю
# инфраструктуру — закрепление/реакции/пересылка), просто отмечен is_system=True и создан
# от лица "system". Публичные точки входа вызываются ИЗВНЕ (routers/web.py — оценки,
# рассылка расписания), ОБЯЗАНЫ быть обёрнуты в try/except на СТОРОНЕ ВЫЗЫВАЮЩЕГО КОДА —
# сбой мессенджера НИКОГДА не должен ронять выставление оценки/рассылку расписания.
SYSTEM_SENDER_NAME = "Вектор"    #посты подписаны уже знакомым маскотом, а не безликой «системой»


def _ensure_system_channel(db: Session, conv_id: str, title: str, about: str,
                           reader_ids, writer_ids=()) -> Conversation:
    """Найти/создать системный канал. Не идёт через create_channel (тот проверяет роль
    вызывающего и требует явного owner-пользователя) — здесь owner условный ("system")."""
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if conv is not None:
        return conv
    now = _now()
    kind_tag = conv_id.split(":", 2)[1] if conv_id.count(":") >= 2 else ""
    conv = Conversation(id=conv_id, kind="channel", title=title[:120], about=about[:500],
                        owner_id="system", is_public=False, created_at=now,
                        is_system=True, system_kind=kind_tag)
    db.add(conv)
    writer_set = set(writer_ids)
    for uid in writer_set:
        db.add(ConversationParticipant(conversation_id=conv_id, user_id=uid, role="writer", joined_at=now))
    for uid in reader_ids:
        if uid in writer_set:
            continue
        db.add(ConversationParticipant(conversation_id=conv_id, user_id=uid, role="reader", joined_at=now))
    db.commit()
    return conv


def _post_system_channel_message(db: Session, conv_id: str, body: str) -> None:
    """Опубликовать пост от лица «Вектора» + пуш офлайн-читателям (не замьютившим канал)."""
    m = Message(conversation_id=conv_id, sender_id="system", body=body,
               created_at=_now(), kind="text", body_format="markdown")
    db.add(m)
    db.commit()
    _broadcast(db, conv_id)
    try:
        from ... import rustore_push
        online = _online_logins()
        parts = db.query(ConversationParticipant).filter(
            ConversationParticipant.conversation_id == conv_id).all()
        ids = [p.user_id for p in parts if not p.muted]
        if ids:
            for u in db.query(User).filter(User.id.in_(ids)).all():
                if u.login and u.login not in online:
                    #🔒 ТЕКСТ СООБЩЕНИЯ В ПУШ НЕ КЛАДЁМ. Здесь стояло `body[:120]` — и это
                    #было ЕДИНСТВЕННОЕ место во всём мессенджере, нарушавшее собственное
                    #правило продукта «содержимое не уходит третьей стороне» (см. шапку
                    #rustore_push.py и `_notify_recipients`, где давно уходит нейтральное
                    #«Новое сообщение»). Через системные каналы ходят объявления куратора и
                    #ответы Вектора на вопросы студента — то есть первые 120 символов
                    #учебных данных уезжали в инфраструктуру RuStore и оседали в её логах
                    #и в шторке уведомлений на заблокированном экране.
                    #Ради чего терпеть: ни ради чего. Человек всё равно открывает чат.
                    rustore_push.notify_login(db, u.login, SYSTEM_SENDER_NAME,
                                              "Новое сообщение",
                                              {"type": "message", "conversation_id": conv_id})
    except Exception:
        pass


def notify_grade_posted(db: Session, student_id: str, teacher_name: str, subject: str, grade: str) -> None:
    """§D12(1): «Мои оценки» — личный read-only канал студента. Публичная точка входа для
    routers/web.py (POST /web/teacher/grade); ВЫЗЫВАЮЩИЙ КОД оборачивает в try/except."""
    if not student_id:
        return
    conv_id = f"sys:grades:{student_id}"
    _ensure_system_channel(db, conv_id, "Мои оценки",
                          "Автоматические уведомления о ваших оценках.", reader_ids=[student_id])
    _post_system_channel_message(
        db, conv_id, f"**{teacher_name}** поставил(а) вам **{grade}** по {subject}")


def ensure_group_schedule_channel(db: Session, group_name: str, student_ids) -> str:
    """§D12(3): «Расписание · Группа» — read-only канал, публикует schedule.publish (админ,
    web.py). Возвращает id канала (для _post_system_channel_message вызывающим кодом).

    ⚠️ Имя группы идёт через `_gtoken` (см. его докстринг): у групп колледжа оно вида
    «К74/1», и слэш в id разваливает адрес — GET проваливается в SPA-фолбэк. Объявления,
    отчёты и замены починили ещё в 3.5, а ЭТОТ канал пропустили: тест мессенджера ставит
    группу «К-24», без слэша, поэтому оставался зелёным рядом с дефектом."""
    conv_id = f"sys:schedule:{_gtoken(group_name)}"
    _ensure_system_channel(db, conv_id, f"Расписание · {group_name}",
                          "Автоматические изменения расписания вашей группы.",
                          reader_ids=student_ids)
    return conv_id


def notify_substitution(db: Session, group_name: str, text: str) -> None:
    """§D12(4): «Замены · Группа» — read-only канал точечных правок расписания.

    Почему отдельно от «Расписание · Группа», хотя источник данных общий: тот канал
    рассылается ОДНИМ постом на публикацию («расписание изменилось — проверьте»), а замена
    — это конкретная пара, которую нужно увидеть до выхода из дома. Смешав их, мы бы либо
    спамили общий канал каждой правкой ячейки, либо утопили замену в общем «что-то
    поменялось». Публичная точка входа для routers/web.py; ВЫЗЫВАЮЩИЙ оборачивает
    в try/except — сбой мессенджера не должен ронять правку расписания."""
    group_name = (group_name or "").strip()
    if not group_name or not text:
        return
    students = [u.id for u in db.query(User).filter(
        User.role == "student", User.group_name == group_name,
        User.deleted == False).all()]  # noqa: E712
    if not students:
        return
    conv_id = f"sys:substitute:{_gtoken(group_name)}"
    _ensure_system_channel(db, conv_id, f"Замены · {group_name}",
                           "Точечные изменения пар: переносы, замены, отмены.",
                           reader_ids=students)
    _post_system_channel_message(db, conv_id, text)


# ── §12: отчёты куратора для родителей («Отчёты · Группа») ──────────────────────────────
def _gtoken(group: str) -> str:
    """Имя группы → безопасный кусок ИДЕНТИФИКАТОРА беседы/отчёта.

    ⚠️ Слэш в id — это сломанный адрес, а не косметика. Группы колледжа называются
    «К74/1», и id вида «sys:announce:К74/1» в URL приезжает как %2F; Starlette
    раскодирует его ОБРАТНО в «/» ещё до подбора роута, путь распадается на лишний
    сегмент и не совпадает ни с одним эндпоинтом мессенджера — GET проваливался в
    SPA-фолбэк (клиент получал HTML вместо JSON и показывал пустоту), а POST отвечал
    405. Из-за этого у групп со слэшем не открывались отчёты и не работали объявления,
    хотя на «ИС-21» всё было исправно.

    «~» выбран потому, что он unreserved в RFC 3986 (в URL не кодируется вовсе) и в
    названиях групп не встречается. Обратное преобразование — _gname."""
    return (group or "").replace("/", "~")


def _gname(token: str) -> str:
    """Обратно к имени группы (см. _gtoken)."""
    return (token or "").replace("~", "/")


def _active_parent_ids_for_group(db: Session, group_name: str) -> list:
    """«Группа с родителями» — есть хотя бы одна АКТИВНАЯ (подтверждённая студентом)
    связь родитель→студент этой группы. Пересчитываем каждый раз (не кэшируем): группа
    может потерять последнего родителя (студент отозвал согласие) или обрести первого."""
    student_ids = [u.id for u in db.query(User).filter(
        User.role == "student", User.group_name == group_name,
        User.deleted == False).all()]  # noqa: E712
    if not student_ids:
        return []
    rows = (db.query(ParentLink.parent_id)
            .filter(ParentLink.student_id.in_(student_ids), ParentLink.status == "active")
            .distinct().all())
    return [r[0] for r in rows]


#Аргумент необязателен: «/отчет», «/отчёт К75/1», «/отчет "К75/1"».
_REPORT_CMD_RE = re.compile(r"^/отч[её]т\b\s*(.*)$", re.IGNORECASE | re.DOTALL)


def _report_groups_for(db: Session, user: User) -> list:
    """Группы, по которым этот человек вправе выпускать отчёт: куратору — его группы,
    администрации — любые. Список нужен ещё и для внятной ошибки («ваши группы: …»)."""
    if user.role == "admin":
        return [g.name for g in db.query(Group)
                .filter(Group.deleted == False).order_by(Group.name).all()]  # noqa: E712
    if user.role == "teacher":
        return list(user.curated_groups or [])
    return []


def _resolve_report_group(arg: str, conv: Conversation, allowed: list) -> str:
    """Какая группа имеется в виду: аргумент команды → канал отчётов → единственная
    курируемая группа. Угадывать при неоднозначности нельзя — отчёт не по той группе
    это выдача чужих оценок, поэтому в спорном случае просим уточнить."""
    arg = (arg or "").strip().strip('«»"\'').strip()
    if arg:
        hit = [g for g in allowed if g.strip().lower() == arg.lower()]
        if not hit:
            raise HTTPException(
                status_code=400,
                detail=f"Группа «{arg}» не найдена. Доступны: {', '.join(allowed) or '—'}")
        return hit[0]
    if conv is not None and conv.system_kind == "curator_reports":
        return _gname(conv.id.split(":", 2)[-1])
    if len(allowed) == 1:
        return allowed[0]
    raise HTTPException(
        status_code=400,
        detail=f"Укажите группу: /отчет {allowed[0] if allowed else 'К75/1'}. "
               f"Доступны: {', '.join(allowed) or '—'}")


def _create_report(db: Session, group_name: str, user: User, conv_ids: list,
                   nonce: str = "") -> Message:
    """Создать отчёт по группе и положить кнопку «Отчёт №N» в каждую из бесед.

    Хранится СНИМОК ГРАНИЦЫ (термин + дата включительно), а не готовые цифры: они
    пересчитываются живьём при каждом открытии (curator_report.collect_group) в пределах
    этой границы. Кнопка одна и та же во всех беседах — id отчёта общий, поэтому
    пересылка не плодит копий данных и не расходится с оригиналом.
    Возвращает сообщение в ПЕРВОЙ беседе (её открывает клиент после создания)."""
    from ... import webdata as W
    cfg = W.load_config(db)
    year, semester = W.current_term(cfg)
    seq = (db.query(func.count(CuratorReport.id))
           .filter(CuratorReport.group_name == group_name).scalar() or 0) + 1
    rid = f"rpt:{_gtoken(group_name)}|{seq}"
    now = _now()
    db.add(CuratorReport(id=rid, group_name=group_name, seq=seq, year=year,
                         semester=semester, cutoff_date=_iso_to_ddmmyyyy(now),
                         created_by=user.id, created_at=now, conversation_id=conv_ids[0]))
    db.commit()
    first = None
    for cid in conv_ids:
        m = Message(conversation_id=cid, sender_id=user.id, body=rid,
                    created_at=_now(), kind="report", body_format="plain",
                    #nonce ставим только ПЕРВОМУ сообщению: он уникален на беседу, и
                    #повтор запроса найдёт именно его (см. проверку в send_message).
                    client_nonce=(nonce if first is None else ""))
        db.add(m)
        db.commit()
        db.refresh(m)
        if first is None:
            first = m
            rep = db.query(CuratorReport).filter(CuratorReport.id == rid).first()
            if rep is not None:
                rep.message_id = m.id
                db.commit()
        _unhide_participants(db, cid)      #«удалённый» чат возвращается с новым сообщением
        _broadcast(db, cid)
    return first


def _handle_report_command(db: Session, conv_id: str, body: str, user: User,
                           nonce: str = ""):
    """`/отчет [группа]` — команда куратора, а не обычное сообщение.

    Работает в ЛЮБОЙ беседе, где автор может писать: в канале «Отчёты · Группа» (группа
    берётся из канала), в чате родителей или в личной переписке — тогда группу называют
    прямо в команде («/отчет К75/1»), и отчёт публикуется СРАЗУ сюда же.

    Ошибки поднимаем HTTP 400/403, а не глотаем: раньше команда молча ничего не делала —
    ни отчёта, ни объяснения, и это выглядело как «отчёты не работают».
    Возвращает созданное сообщение-кнопку (его и отдаёт /messages вместо текста команды)."""
    mt = _REPORT_CMD_RE.match(body.strip())
    if not mt:
        return None
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    allowed = _report_groups_for(db, user)
    if not allowed:
        raise HTTPException(status_code=403,
                            detail="Отчёт по группе выпускает её куратор или администрация")
    group_name = _resolve_report_group(mt.group(1), conv, allowed)
    #В КАНАЛЕ отчётов аудитория — родители, и без единой подтверждённой связи публиковать
    #там нечего. В обычном чате это ограничение не действует: адресата выбрал сам куратор.
    if conv is not None and conv.system_kind == "curator_reports" \
            and not _active_parent_ids_for_group(db, group_name):
        raise HTTPException(
            status_code=400,
            detail="У группы нет ни одного подтверждённого родителя — отчёт для родителей "
                   "публиковать некому")
    return _create_report(db, group_name, user, [conv_id], nonce)


#ℹ️ `/активность` УДАЛЕНА 17.08.2026 (решение Влада). Команда была ЕДИНСТВЕННОЙ дверью к
#активностям — оттуда и её появление здесь, и сторож достижимости в
#`web/tests/slashCommandsReachable.test.mjs`. С 3.7.4 дверь другая и лучше: кнопка в шапке
#беседы зовёт лаунчер напрямую, не спрашивая сервер. Два входа в одно место, из которых
#один надо помнить наизусть, — это не запас прочности, а лишняя поверхность: команду
#пришлось бы переводить, объяснять и проверять правами дважды.
#⚠️ Проверка права осталась там, где ей и место, — в `POST /activities/start`
#(`_require_can_run`): она НЕ была побочным эффектом команды.


_MUTE_CMD_RE = re.compile(r'^/mute\s+"?@?([^"\s]+)"?\s*$', re.IGNORECASE)
_CLEAR_CMD_RE = re.compile(r'^/clear\s+"?(\d+)"?\s*$', re.IGNORECASE)
_CLEAR_MAX_N = 100


def _resolve_participant_by_name(db: Session, conv_id: str, token: str):
    """Находит участника беседы по первому слову ФИО (та же простая эвристика, что у
    @упоминаний, см. _parse_mentions) — токен без ведущего @/кавычек."""
    token = (token or "").lstrip("@").strip().lower()
    if not token:
        return None
    ids = _participant_ids(db, conv_id)
    if not ids:
        return None
    for u in db.query(User).filter(User.id.in_(ids)).all():
        first = (u.full_name or u.name or "").split(" ", 1)[0].strip().lower()
        if first == token:
            return u
    return None


def _handle_mute_command(db: Session, conv_id: str, body: str, user: User,
                         part: ConversationParticipant):
    """`/mute "@username"` — переключатель: заглушает/снова разрешает цели писать В ЭТУ
    беседу (НЕ путать с личным `muted` — «я не хочу пуши»). Право cmd_mute — owner/admin
    по умолчанию, либо обладатель кастомной роли с этим правом."""
    mt = _MUTE_CMD_RE.match(body.strip())
    if not mt:
        return None
    if "cmd_mute" not in _permissions_for(db, part):
        raise HTTPException(status_code=403, detail="Нет права /mute в этой беседе")
    target = _resolve_participant_by_name(db, conv_id, mt.group(1))
    if target is None:
        raise HTTPException(status_code=404, detail="Участник не найден в этой беседе")
    tp = _participant(db, conv_id, target.id)
    if tp is None:
        raise HTTPException(status_code=404, detail="Участник не найден в этой беседе")
    if tp.role == "owner":
        raise HTTPException(status_code=400, detail="Нельзя заглушить владельца беседы")
    tp.silenced = not tp.silenced
    db.commit()
    name = target.full_name or target.name or target.login
    sysmsg = _system(db, conv_id, "muted" if tp.silenced else "unmuted", target.id, name)
    _broadcast(db, conv_id)
    return sysmsg


def _handle_clear_command(db: Session, conv_id: str, body: str, user: User,
                          part: ConversationParticipant):
    """`/clear "N"` — томбстоунит последние N сообщений беседы (тот же механизм, что
    модераторское удаление, не hard-delete). Право cmd_clear, лимит _CLEAR_MAX_N от
    случайного /clear 999999."""
    mt = _CLEAR_CMD_RE.match(body.strip())
    if not mt:
        return None
    if "cmd_clear" not in _permissions_for(db, part):
        raise HTTPException(status_code=403, detail="Нет права /clear в этой беседе")
    n = min(int(mt.group(1)), _CLEAR_MAX_N)
    if n <= 0:
        raise HTTPException(status_code=400, detail="Укажите количество сообщений больше нуля")
    rows = (db.query(Message)
            .filter(Message.conversation_id == conv_id, Message.deleted_at == "")
            .order_by(Message.id.desc()).limit(n).all())
    now = _now()
    for row in rows:
        row.deleted_at = now
        row.pinned = False
    db.commit()
    sysmsg = _system(db, conv_id, "cleared", str(len(rows)))
    _broadcast(db, conv_id)
    return sysmsg


def _require_report_access(db: Session, rep: CuratorReport, user: User) -> None:
    """Доступ к отчёту = участие в ЛЮБОЙ беседе, где лежит его кнопка.

    Привязка к ОДНОЙ исходной беседе делала пересылку бессмысленной: у адресата в личке
    кнопка была, а по нажатию приходило 403. Отчёт при этом не «публичный» — чтобы кнопка
    попала в беседу, туда её должен положить или переслать тот, у кого доступ уже есть."""
    conv_ids = {r[0] for r in db.query(Message.conversation_id)
                .filter(Message.kind == "report", Message.body == rep.id,
                        Message.deleted_at == "").all()}
    if rep.conversation_id:
        conv_ids.add(rep.conversation_id)
    for cid in conv_ids:
        if _participant(db, cid, user.id) is not None:
            return
    raise HTTPException(status_code=403, detail="Нет доступа к этому отчёту")


def _iso_to_ddmmyyyy(iso: str) -> str:
    """"2026-07-27T10:00:00+00:00" → "27.07.2026" (формат дат занятий, см. curator_report.py)."""
    try:
        y, m, d = iso[:10].split("-")
        return f"{d}.{m}.{y}"
    except Exception:
        return ""


# ── Модерация (админ) — очередь тикетов, просмотр бесед (с аудитом), ответ ────────────
def _report_out(db: Session, r: MessageReport) -> dict:
    reporter = db.query(User).filter(User.id == r.reporter_id).first()
    reported = db.query(User).filter(User.id == r.reported_user_id).first()
    onl = _online_logins()
    mset = _muted_set(db, [r.reporter_id, r.reported_user_id])
    return {
        "id": r.id, "message_id": r.message_id, "conversation_id": r.conversation_id,
        #⚠️ На ЧТО жалоба. Обязано уезжать клиенту: у тикета на отзыв среза понимания
        #(target_kind="activity_feedback") `message_id` — это id строки activity_feedback,
        #а НЕ сообщения. Без этого поля админка предложила бы на таком тикете «удалить
        #сообщение», и удалён был бы ПОСТОРОННИЙ текст с совпавшим номером — молча и
        #необратимо для читателей. Держит `test_feedback_report_is_marked_as_not_a_message`.
        "target_kind": getattr(r, "target_kind", "") or "message",
        "message_snapshot": r.message_snapshot, "reason_code": r.reason_code,
        "description": r.description, "created_at": r.created_at, "status": r.status,
        "reporter_name": (reporter.full_name if reporter else r.reporter_id),
        "reported_name": (reported.full_name if reported else r.reported_user_id),
        #Карточки участников (аватар, ФИО, роль, группа/предметы, состояние мьюта) — админ
        #в жалобе видит, КТО пожаловался и НА КОГО, с лицом, контекстом и кнопкой мьюта.
        "reporter": _safe_user(reporter, onl, r.reporter_id in mset) if reporter else None,
        "reported": _safe_user(reported, onl, r.reported_user_id in mset) if reported else None,
        "handled_by": r.handled_by, "resolution_note": r.resolution_note,
    }


_REPORT_STATUSES = {"open", "in_review", "resolved", "dismissed"}


# ── WebSocket-эндпоинт ───────────────────────────────────────────────────────────────
def _ws_token(ws: WebSocket) -> tuple:
    """Достаёт JWT для WS ТОЛЬКО из Sec-WebSocket-Protocol (клиент шлёт ['bearer', <jwt>]):
    так токен НЕ попадает в URL и, значит, в access-логи прокси. Раньше был фолбэк на
    ?token= в query — но он клал JWT в JSON-лог Caddy (`uri` там НЕ редактируется, в
    отличие от заголовков Authorization/Cookie), а НИ ОДИН живой клиент им не пользуется:
    веб/десктоп/мобилка ходят сабпротоколом (web/src/stores/messenger.js). Фолбэк убран —
    клиент без валидного сабпротокола просто не поднимет сокет и останется на опросе (он и
    так страховка, §мессенджера). Возвращает (token, subprotocol_echo): если авторизация
    прошла через сабпротокол, его надо вернуть эхом в accept()."""
    raw = ws.headers.get("sec-websocket-protocol", "")
    if raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        #ожидаем ["bearer", "<jwt>"]; JWT состоит из tchar (base64url + точки) — валидный токен
        if len(parts) >= 2 and parts[0] == "bearer":
            return parts[1], "bearer"
    return "", None


#⚠️ __all__ СЧИТАЕТСЯ АВТОМАТИЧЕСКИ, и это не лень. У соседнего пакета
#`routers/web/_common.py` список имён задан руками, и там уже ловили NameError в
#рантайме при совершенно зелёной компиляции: имя добавили в блок импорта и забыли
#дописать в `__all__`. Здесь забыть нечего — список берётся из самого модуля.
#Почти все имена мессенджера начинаются с подчёркивания, а такие `import *` без
#явного `__all__` не переносит вовсе, поэтому список обязателен.
__all__ = sorted(k for k in list(globals()) if not k.startswith("__"))
