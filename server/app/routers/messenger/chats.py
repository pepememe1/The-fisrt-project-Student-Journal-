"""
chats.py — Жизненный цикл беседы: личный чат, «Избранное», список, закреп и архив,
создание группы и канала, участники, роли и права, игнор, карточка и переименование.

Часть пакета `routers/messenger` (разрез 3.7.7). Общий роутер, проверки прав и
сборка ответов — в `_common.py`; порядок регистрации маршрутов задаёт `__init__.py`.
"""
from ._common import *      # noqa: F401,F403 — роутеры, модели, хелперы


@router.post("/chats/direct/{user_id}")
def open_direct(user_id: str, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Открыть личный чат с пользователем (создать, если ещё нет). Идемпотентно — беседа
    ключуется детерминированным id пары, повторный вызов вернёт ту же."""
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="Нельзя написать самому себе")
    peer = db.query(User).filter(User.id == user_id, User.deleted == False).first()  # noqa: E712
    if peer is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    #Проверяем ЗДЕСЬ, а не только в каталоге: id собеседника можно подставить руками, и
    #фильтр в поиске сам по себе ничего не защищает (инвариант «UI-скрытие — не защита»).
    _guard_direct_allowed(db, user, peer)

    conv_id = _ensure_direct(db, user, peer)
    sm = _status_map(db, [peer.id])
    return {"conversation_id": conv_id, "kind": "direct",
            "peer": _safe_user(peer, _online_logins(), status=sm.get(peer.id))}


# ── Список бесед ─────────────────────────────────────────────────────────────────────
def _preview_out(msg, me_id: str, sender_name: str, att: dict = None) -> dict:
    """Сообщение для ПРЕВЬЮ в списке чатов — то же самое, но без цитаты.

    🔒 Цитату здесь убираем НАМЕРЕННО (нашёл Полковник 06.09.2026). Список чатов рисует
    только текст последнего сообщения (`utils/messagePreview.js`), то есть поле всё равно
    не показывается, — а путь мимо `_blank_quotes_of_deleted` был: гашение цитаты
    удалённого оригинала живёт в `_attach_rich_meta`, а превью его не зовёт. Значит
    процитированный кусок сообщения, удалённого «у всех», уезжал бы в ответ ручки.
    Убрать лишнее поле дешевле и надёжнее, чем не забыть подчистить его в третьем месте.
    """
    out = _msg_out(msg, me_id, sender_name, att)
    out["reply_quote"] = ""
    return out


@router.get("/chats")
def list_chats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Беседы текущего пользователя: собеседник/заголовок, последнее сообщение, непрочитанные.
    Закреплённые чаты — сверху, дальше по времени последнего сообщения (убыв.).

    ━━ ЗАПРОСЫ ИДУТ ПАЧКАМИ, А НЕ ПО ОДНОМУ НА БЕСЕДУ (09.09.2026) ━━
    Здесь был классический N+1, причём в САМОЙ ГОРЯЧЕЙ ручке продукта: список чатов
    опрашивается раз в 3.5 с при живом сокете и чаще без него. На каждую беседу
    отдельно спрашивались сама беседа, последнее сообщение, счётчик непрочитанного,
    скан отметок, автор последнего сообщения, вложение и собеседник личного чата.

    Замер (`server/bench_messenger.py`, профиль big — 3000 человек, 922 500 сообщений):
    у преподавателя с девятью беседами **59 запросов и 36.9 мс**. При этом сам SQLite
    тратил на них около 7 мс — остальные 30 мс уходили на обвязку: SQLAlchemy строит
    объект на каждую строку, и делает это 59 раз вместо одного. То есть дело было не в
    базе и не в языке, а в количестве работы.

    ⚠️ Сокращение работы — ПЕРВЫЙ шаг, и его нельзя пропустить в пользу «перепишем на
    другом языке»: быстрый язык, делающий ту же лишнюю работу, оставляет источник
    нагрузки на месте (замечание Ярослава от 09.09.2026, и оно верное).

    ⚠️ Границы поведения СОХРАНЕНЫ дословно, включая личные метки участника
    (`cleared_upto_id`, `cleared_at`, `last_read_at`) — они разные у разных бесед,
    поэтому в пакетных запросах приезжают ИЗ САМОЙ СТРОКИ УЧАСТНИКА через JOIN, а не
    подставляются одним значением на всех. Подставить одно — самый вероятный способ
    сломать это молча: у большинства людей метки совпадают, и расхождение всплыло бы
    только у того, кто чистил переписку.
    """
    parts = (db.query(ConversationParticipant)
             .filter(ConversationParticipant.user_id == user.id).all())
    if not parts:
        return {"chats": []}
    onl = _online_logins()
    by_conv = {p.conversation_id: p for p in parts}
    conv_ids = list(by_conv)

    # ── одна пачка: сами беседы ────────────────────────────────────────────────────
    convs = {c.id: c for c in db.query(Conversation)
             .filter(Conversation.id.in_(conv_ids)).all()}

    # ── последнее сообщение и счётчик: ТОЧЕЧНО, и это ЗАМЕР, а не небрежность ─────
    # 🔥 Здесь стоял «правильный» пакетный вариант — один `MAX(id) GROUP BY` и один
    # `COUNT(*) GROUP BY` с JOIN к строке участника вместо одиннадцати точечных запросов.
    # Он ОКАЗАЛСЯ ВДВОЕ МЕДЛЕННЕЕ (замер 09.09.2026: 5.0 + 5.6 мс против 1.2 + 3.9 мс), и
    # причина не в планировщике — планы у обоих индексные:
    #   • точечный «последнее сообщение» — это `ORDER BY id DESC LIMIT 1` по индексу
    #     (conversation_id, rowid): база идёт по индексу с конца и ОСТАНАВЛИВАЕТСЯ НА
    #     ПЕРВОЙ СТРОКЕ. Стоимость не зависит от размера беседы;
    #   • `MAX(id) ... GROUP BY` такого права не имеет: он обязан просмотреть ВСЮ группу
    #     и сложить результат во временное B-дерево (`USE TEMP B-TREE FOR GROUP BY`).
    #
    # ⚠️ Отсюда правило, которое стоит помнить: «N+1 всегда хуже одного запроса» — НЕ
    # правда. Точечный запрос, который упирается в индекс и берёт одну строку, дешевле
    # агрегата по всей группе, сколько бы раз его ни повторили. Дорого не число
    # запросов, а число ПРОСМОТРЕННЫХ СТРОК.
    last_by_conv = {}
    unread_by_conv = {}
    for p in parts:
        base = db.query(Message).filter(Message.conversation_id == p.conversation_id)
        if p.cleared_upto_id:
            base = base.filter(Message.id > p.cleared_upto_id)
        if p.cleared_at:                     #legacy-строки (очищены до появления id-границы)
            base = base.filter(Message.created_at > p.cleared_at)
        last = base.order_by(Message.id.desc()).first()
        if last is not None:
            last_by_conv[p.conversation_id] = last
        #Служебные события («вступил в беседу», «закреплено») в счётчик НЕ идут: это не
        #обращение к тебе, а отметка в ленте, а красный кружок заставляет открыть чат — в
        #канале на сотню читателей он не гас бы никогда.
        unread_by_conv[p.conversation_id] = (
            base.filter(Message.sender_id != user.id,
                        Message.kind != "system",
                        Message.deleted_at == "",
                        Message.created_at > (p.last_read_at or "")).count())
    last_rows = list(last_by_conv.values())

    # ── одна пачка: собеседники личных чатов ───────────────────────────────────────
    direct_ids = [cid for cid in conv_ids
                  if cid in convs and convs[cid].kind == "direct"]
    peer_of = {}
    if direct_ids:
        others = (db.query(ConversationParticipant)
                  .filter(ConversationParticipant.conversation_id.in_(direct_ids),
                          ConversationParticipant.user_id != user.id).all())
        peer_ids = {o.user_id for o in others}
        peers = {u.id: u for u in db.query(User).filter(User.id.in_(peer_ids)).all()} \
            if peer_ids else {}
        for o in others:
            #Первый найденный — как и раньше (в личном чате участников ровно двое).
            peer_of.setdefault(o.conversation_id, peers.get(o.user_id))

    # ── одна пачка: имена авторов, статусы и вложения ──────────────────────────────
    # ⚠️ Правило имени здесь СВОЁ и НЕ `_names_for` (возражение Полковника, 09.09.2026).
    # Тот подставляет `login` и даже `id`, когда ФИО пусто, — и это верно для ленты, где
    # подпись обязана быть хоть какой-то. В СПИСКЕ чатов правило другое и прежнее:
    # `full_name or name or ""`. Пустая строка означает «не подписывать автора вовсе», а
    # с `login` у человека, заведённого синком по одному логину (ФИО ещё не приехало),
    # строка превью из «текст» превратилась бы в «s1: текст». Мелочь, но это ровно тот
    # класс, ради которого и говорится «поведение сохранено дословно».
    sender_ids = {m.sender_id for m in last_rows
                  if m.sender_id and m.sender_id != "system"}
    senders = ({u.id: u for u in db.query(User).filter(User.id.in_(sender_ids)).all()}
               if sender_ids else {})
    statuses = _status_map(db, [p.id for p in peer_of.values() if p])
    amap = _att_map(db, last_rows)

    # ── отметки «@»: только там, где вообще есть непрочитанное ─────────────────────
    # ⚠️ Сужение осознанное и обосновано, а не «кажется, так быстрее»: отметка попадает
    # в `mentions` только у обычного сообщения от человека, а условия у неё те же, что у
    # счётчика непрочитанного, плюс «содержит @». Значит при `unread == 0` непрочитанной
    # отметки быть не может по построению. Прежний код сканировал ленту КАЖДОЙ беседы,
    # в том числе давно прочитанной, — а `LIKE '%@%'` индексом не ускоряется никогда.
    mention_by_conv = {}
    for cid in [c for c in conv_ids if unread_by_conv.get(c)]:
        p = by_conv[cid]
        for cand in (db.query(Message.id, Message.mentions)
                     .filter(Message.conversation_id == cid,
                             Message.sender_id != user.id,
                             Message.deleted_at == "",
                             Message.body.like("%@%"),
                             Message.created_at > (p.last_read_at or ""),
                             Message.id > (p.cleared_upto_id or 0))
                     .order_by(Message.id.asc()).limit(_MENTION_SCAN_LIMIT).all()):
            hit = next((x for x in (cand.mentions or []) if x.get("user_id") == user.id), None)
            if hit:
                mention_by_conv[cid] = (cand.id, bool(hit.get("loud")))
                break

    out = []
    for p in parts:
        conv = convs.get(p.conversation_id)
        if conv is None:
            continue
        last = last_by_conv.get(conv.id)
        #Чат удалён у меня и с тех пор ничего не приходило — не показываем его в списке.
        #Появится новое сообщение — вернётся сам (см. _unhide_participants).
        if p.hidden and last is None:
            continue
        #Имя автора последнего сообщения нужно списку чатов (в группе/канале строка
        #выглядит как «Иванов: текст» — без имени непонятно, кто написал). В личном чате
        #и у своих сообщений имя не нужно: клиент подписывает их «Вы».
        sender_name = ""
        if last is not None and conv.kind in ("group", "channel") and last.sender_id != user.id:
            su = senders.get(last.sender_id)
            sender_name = (SYSTEM_SENDER_NAME if last.sender_id == "system"
                           else ((su.full_name or su.name or "") if su else ""))
        mention_id, mention_loud = mention_by_conv.get(conv.id, (0, False))
        item = {
            "conversation_id": conv.id,
            "kind": conv.kind,
            "pinned": bool(p.pinned),
            "archived": bool(p.archived),
            "muted": bool(p.muted),
            "unread": unread_by_conv.get(conv.id, 0),
            "mention_message_id": mention_id,     #0 — меня не отмечали
            "mention_loud": mention_loud,
            "last_message": (_preview_out(last, user.id, sender_name,
                                          amap.get(getattr(last, "attachment_id", "") or ""))
                             if last else None),
            "last_at": (last.created_at if last else conv.created_at) or "",
            #Системный канал («Мои оценки», «Расписание · Группа», «Объявления») ведёт
            #Вектор, и в списке он показывается его лицом, а не кружком с инициалами.
            #⚠️ Отдаём ПРИЗНАК, а не заставляем клиент разбирать id: формат `sys:*:{группа}`
            #— серверная деталь, и полагаться на неё в браузере значит завести второй
            #источник правды, который однажды разойдётся с первым (у нас это уже было с
            #группой со слэшем в id).
            "is_system": bool(conv.is_system),
        }
        if conv.kind == "direct":
            peer = peer_of.get(conv.id)
            item["title"] = (peer.full_name or peer.name or peer.login) if peer else "Диалог"
            item["peer"] = _safe_user(peer, onl, status=statuses.get(peer.id)) if peer else None
        else:
            item["title"] = conv.title or ""
            #Аватарка группы/канала. У личного чата её нет намеренно: там лицо беседы —
            #сам собеседник, и он уже приходит в `peer`.
            item["avatar"] = conv.avatar or ""
        out.append(item)
    #Номер отчёта в строке списка («📊 Отчёт №3 по группе К75/1») — иначе там оказался бы
    #сырой id из тела сообщения.
    _attach_rich_meta(db, [x["last_message"] for x in out if x["last_message"]], user.id)
    #Сортировка: сначала закреплённые, потом по времени последней активности (новые выше).
    out.sort(key=lambda x: (not x["pinned"], _neg_key(x["last_at"])))
    return {"chats": out}


# ── Организация списка чатов: закреп/архив/избранное ─────────────────────────────────
# Дополнения из docs/MESSENGER-ADDON-PLAN-GPT*.md, отобранные как «действительно полезное
# и удобное» (без ИИ-упрощений учёбы — см. §5.4 CLAUDE.md). Личное состояние участника,
# как mute — на собеседника не влияет.
@router.post("/chats/{conv_id}/pin")
def pin_chat(conv_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Закрепить чат в списке у СЕБЯ (сортировка — см. list_chats)."""
    part = _require_participant(db, conv_id, user)
    part.pinned = True
    db.commit()
    return {"ok": True}


@router.delete("/chats/{conv_id}/pin")
def unpin_chat(conv_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    part = _require_participant(db, conv_id, user)
    part.pinned = False
    db.commit()
    return {"ok": True}


@router.post("/chats/{conv_id}/archive")
def archive_chat(conv_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """В архив — чат уходит из основного списка, но НЕ удаляется (в отличие от DELETE
    /chats/{conv_id}). В отличие от «удалить у себя» (hidden), новое сообщение чат из
    архива само не выводит — только явная расархивация, это и есть смысл архива."""
    part = _require_participant(db, conv_id, user)
    part.archived = True
    db.commit()
    return {"ok": True}


@router.delete("/chats/{conv_id}/archive")
def unarchive_chat(conv_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    part = _require_participant(db, conv_id, user)
    part.archived = False
    db.commit()
    return {"ok": True}


@router.post("/chats/saved")
def open_saved(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """«Избранное» (Saved Messages) — личный чат с самим собой: заметки, ссылки, код себе,
    без надобности заводить отдельную сущность заметок — переиспользуем ВСЮ инфраструктуру
    сообщений (Markdown, реакции, правка, пересылка). Один на пользователя, создаётся лениво
    при первом открытии. Закреплён по умолчанию — как в Telegram, всегда сверху списка."""
    return {"conversation_id": _ensure_saved(db, user)}


@router.delete("/chats/{conv_id}")
def delete_conversation(conv_id: str, clear_only: bool = Query(False),
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Удалить переписку У СЕБЯ: история до текущего момента больше не показывается, чат
    уходит из списка. У собеседника переписка остаётся — это личное действие, а не
    удаление чужих данных (иначе один человек стирал бы историю другому).

    clear_only=1 — «очистить историю»: сообщения скрываем, но чат остаётся в списке.
    Группу/канал при удалении ещё и покидаем — иначе беседа вернулась бы с первым же
    сообщением, хотя пользователь ушёл из неё осознанно."""
    p = _require_participant(db, conv_id, user)
    conv = _conversation(db, conv_id)
    #Границу ставим по НОМЕРУ последнего сообщения, а не по времени: сообщение, пришедшее
    #в тот же тик часов, что и очистка, иначе исчезло бы у пользователя навсегда.
    #cleared_at гасим — он остаётся только у строк, очищенных до появления этого поля.
    last = (db.query(Message).filter(Message.conversation_id == conv_id)
            .order_by(Message.id.desc()).first())
    p.cleared_upto_id = last.id if last else 0
    p.cleared_at = ""
    p.last_read_at = _now()                #«хвоста» непрочитанного после очистки нет
    #«Избранное» — не переписка, а личный блокнот: он один на пользователя и всегда есть в
    #списке (как в Telegram). Удалять его нечего и незачем — любое удаление сводим к очистке.
    if conv.kind == "saved":
        db.commit()
        return {"ok": True, "conversation_id": conv_id, "cleared": True}
    if clear_only:
        db.commit()
        return {"ok": True, "conversation_id": conv_id, "cleared": True}
    if conv.kind in ("group", "channel"):
        db.delete(p)                        #выйти из группы/канала = перестать получать
    else:
        p.hidden = True                     #личный чат/модерация — просто убрать из списка
    db.commit()
    return {"ok": True, "conversation_id": conv_id, "deleted": True}


@router.post("/chats/{conv_id}/mute")
def mute_conversation(conv_id: str, payload: dict = Body(default={}),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Замьютить/размьютить беседу У СЕБЯ (без пушей по ней; переписка продолжает работать).
    Это личное состояние участника, на других не влияет."""
    p = _require_participant(db, conv_id, user)
    p.muted = bool(payload.get("muted", True))
    db.commit()
    return {"ok": True, "conversation_id": conv_id, "muted": bool(p.muted)}


def _expand_class_groups(db: Session, user: User, names) -> list[str]:
    """Названия УЧЕБНЫХ групп -> id их студентов. Только СВОИ курируемые.

    ⚠️ ОДНА функция на создание беседы и на добавление в существующую. Раньше это был
    вложенный цикл внутри `create_group`, и когда добавление участников наконец обрело
    вызывающего (25.08.2026), логику пришлось бы написать второй раз — а вторая копия
    правила ДОСТУПА расходится с первой молча и в опасную сторону: «преподаватель
    массово добавил чужих студентов».

    ⚠️ Чужое имя молча пропускаем, а не отвечаем ошибкой: клиентскому списку не верим,
    но и подсказывать, какие группы существуют, тому, кто их не курирует, незачем.
    """
    curated = set(user.curated_groups or [])
    out: list[str] = []
    for gname in (names or []):
        if gname not in curated:
            continue
        students = (db.query(User)
                    .filter(User.role == "student", User.group_name == gname,
                            User.deleted == False).all())  # noqa: E712
        out.extend(s.id for s in students)
    return out


@router.post("/chats/group")
def create_group(payload: dict = Body(...), user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Создать группу: создатель — owner, выбранные — участники (member).

    `class_groups` (§12, режим куратора) — названия УЧЕБНЫХ групп (не путать с чат-
    группой): все студенты этих групп добавляются автоматически, вперемешку с
    индивидуально выбранными `member_ids`. Доступно ТОЛЬКО куратору и ТОЛЬКО для его
    СОБСТВЕННЫХ `curated_groups` — иначе любой преподаватель массово добавлял бы чужих
    студентов в свои чаты (роль/скоуп проверяются на сервере, а не на клиенте)."""
    _guard_can_create(db, user, "group")
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Нужно название группы")
    now = _now()
    conv_id = f"conv:{uuid4().hex}"
    db.add(Conversation(id=conv_id, kind="group", title=title[:120],
                        about=(payload.get("about") or "")[:500], owner_id=user.id, created_at=now))
    db.add(ConversationParticipant(conversation_id=conv_id, user_id=user.id,
                                   role="owner", joined_at=now))
    seen = {user.id}
    invited = 0
    member_ids = list(payload.get("member_ids") or [])
    member_ids.extend(_expand_class_groups(db, user, payload.get("class_groups")))
    for uid in member_ids:
        if uid in seen:
            continue
        u = db.query(User).filter(User.id == uid, User.deleted == False).first()  # noqa: E712
        if u is None:
            continue
        seen.add(uid)
        if _needs_invite(user, u):
            #Студент позвал преподавателя: участником он станет, только когда согласится.
            db.add(ConversationInvite(conversation_id=conv_id, user_id=uid,
                                      invited_by=user.id, created_at=now))
            invited += 1
            continue
        db.add(ConversationParticipant(conversation_id=conv_id, user_id=uid,
                                       role="member", joined_at=now))
    db.commit()
    if invited:
        _notify_invites(db, conv_id, title, user)
    return {"conversation_id": conv_id, "kind": "group", "title": title, "invited": invited}


@router.post("/chats/channel")
def create_channel(payload: dict = Body(...), user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Создать канал: создатель — owner, выбранные — writer (пишут); остальные вступают как reader."""
    #Канал — вещание, и студенту он закрыт: см. _CHANNEL_CREATOR_ROLES.
    _guard_can_create(db, user, "channel")
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Нужно название канала")
    now = _now()
    conv_id = f"conv:{uuid4().hex}"
    db.add(Conversation(id=conv_id, kind="channel", title=title[:120],
                        about=(payload.get("about") or "")[:500], owner_id=user.id,
                        is_public=bool(payload.get("is_public", True)), created_at=now))
    db.add(ConversationParticipant(conversation_id=conv_id, user_id=user.id,
                                   role="owner", joined_at=now))
    seen = {user.id}
    for uid in (payload.get("writer_ids") or []):
        if uid in seen or db.query(User).filter(User.id == uid, User.deleted == False).first() is None:  # noqa: E712
            continue
        db.add(ConversationParticipant(conversation_id=conv_id, user_id=uid,
                                       role="writer", joined_at=now))
        seen.add(uid)
    db.commit()
    return {"conversation_id": conv_id, "kind": "channel", "title": title}


@router.get("/channels")
def public_channels(q: str = Query(""), user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Каталог публичных каналов для вступления."""
    rows = (db.query(Conversation)
            .filter(Conversation.kind == "channel", Conversation.is_public == True).all())  # noqa: E712
    ql = (q or "").strip().lower()
    out = []
    for c in rows:
        if ql and ql not in (c.title or "").lower():
            continue
        subs = (db.query(ConversationParticipant)
                .filter(ConversationParticipant.conversation_id == c.id).count())
        out.append({"conversation_id": c.id, "title": c.title, "about": c.about,
                    "avatar": c.avatar or "",
                    "subscribers": subs, "joined": _participant(db, c.id, user.id) is not None})
    out.sort(key=lambda x: (x["title"] or "").lower())
    return {"channels": out}


#──────────────────────────────────────────────────────────────────────────────────────
#Заявки: студент позвал преподавателя в свою группу (см. _needs_invite в _common.py).
#Пока заявка не принята, участника НЕТ — то есть человек не в списке, не получает
#сообщений и не считается ни в одной существующей выборке. Это не флаг, который надо
#не забыть в двадцати запросах, а отсутствие строки.
#──────────────────────────────────────────────────────────────────────────────────────

@router.get("/invites")
def my_invites(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Мои непринятые приглашения в беседы."""
    rows = (db.query(ConversationInvite)
            .filter(ConversationInvite.user_id == user.id)
            .order_by(ConversationInvite.id.desc()).all())
    out = []
    for inv in rows:
        conv = (db.query(Conversation)
                .filter(Conversation.id == inv.conversation_id).first())
        if conv is None:
            #Беседу удалили, пока заявка висела — показывать нечего.
            continue
        by = db.query(User).filter(User.id == inv.invited_by).first()
        members = (db.query(ConversationParticipant)
                   .filter(ConversationParticipant.conversation_id == conv.id).count())
        out.append({
            "conversation_id": conv.id,
            "title": conv.title,
            "about": conv.about,
            "kind": conv.kind,
            "members": members,
            "invited_by": inv.invited_by,
            "invited_by_name": (by.full_name or by.name or by.login) if by else "",
            "created_at": inv.created_at,
        })
    return {"invites": out}


@router.post("/invites/{conv_id}/accept")
def accept_invite(conv_id: str, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Принять приглашение: заявка превращается в обычного участника.

    ⚠️ Роль — `member`, а не `admin`: преподаватель вошёл в ЧУЖУЮ беседу, которую завели
    студенты, и выдавать ему права над ней по факту должности значило бы отдать чужую
    группу первому приглашённому. Понадобятся права — их даёт владелец, как и всем."""
    inv = _invite(db, conv_id, user.id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Приглашение не найдено")
    conv = _conversation(db, conv_id)
    db.delete(inv)
    if _participant(db, conv_id, user.id) is None:
        db.add(ConversationParticipant(conversation_id=conv_id, user_id=user.id,
                                       role="member", joined_at=_now()))
    db.commit()
    name = user.full_name or user.name or user.login
    if conv.kind == "group":     #§D6: «вступил» — только для групп
        _system(db, conv_id, "user_joined", user.id, name)
    _broadcast(db, conv_id)
    return {"ok": True, "conversation_id": conv_id}


@router.post("/invites/{conv_id}/decline")
def decline_invite(conv_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Отклонить приглашение.

    ⚠️ Отказ объявляется в беседе. Тихий отказ выглядит для пригласивших так же, как
    «ещё не посмотрел», и они ждут молча — а потом зовут снова, потому что решают, что
    заявка не дошла. Строка нейтральна и не добавляет ничего, чего они не знают: кого
    звали, им известно."""
    inv = _invite(db, conv_id, user.id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Приглашение не найдено")
    conv = _conversation(db, conv_id)
    db.delete(inv)
    db.commit()
    name = user.full_name or user.name or user.login
    if conv.kind == "group":
        _system(db, conv_id, "invite_declined", user.id, name)
    _broadcast(db, conv_id)
    return {"ok": True}


@router.post("/chats/{conv_id}/join")
def join_chat(conv_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Присоединиться к публичному каналу (как reader) → канал появится в списке чатов."""
    conv = _conversation(db, conv_id)
    if conv.kind != "channel" or not conv.is_public:
        raise HTTPException(status_code=403, detail="К этой беседе нельзя присоединиться")
    if _participant(db, conv_id, user.id) is None:
        db.add(ConversationParticipant(conversation_id=conv_id, user_id=user.id,
                                       role="reader", joined_at=_now()))
        db.commit()
        #§D6: «вступил» — ТОЛЬКО для групп (см. add_members ниже). Этот эндпоинт вообще
        #только для каналов (проверка kind=="channel" выше), поэтому здесь их нет никогда.
        _broadcast(db, conv_id)
    return {"ok": True, "conversation_id": conv_id}


@router.post("/chats/{conv_id}/leave")
def leave_chat(conv_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Покинуть беседу (группу/канал)."""
    p = _participant(db, conv_id, user.id)
    if p is not None:
        conv = _conversation(db, conv_id)
        name = user.full_name or user.name or user.login
        db.delete(p)
        db.commit()
        if conv.kind == "group":       #§D6: «вышел» — только для групп, не для каналов
            _system(db, conv_id, "user_left", user.id, name)
        _broadcast(db, conv_id)
    return {"ok": True}


@router.post("/chats/{conv_id}/members")
def add_members(conv_id: str, payload: dict = Body(...),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Добавить участников (owner/admin). В канал добавляются как reader, в группу — member.

    ⚠️ У этой ручки ДО 25.08.2026 не было НИ ОДНОГО вызывающего: она работала, а через
    продукт добавить человека в беседу было нельзя вовсе — только правкой базы руками.
    Классическое «обещание без вызывающего»; нашлось сверкой контракта
    (`tools/graph_api_bridge.py`), где ручка всё время лежала в списке «сервер никто
    не зовёт».

    `class_groups` — то же, что при создании группы: названия УЧЕБНЫХ групп, только
    свои курируемые. Раскрывает общая `_expand_class_groups`, чтобы правило доступа не
    существовало в двух копиях.
    """
    _require_manager(db, conv_id, user)
    conv = _conversation(db, conv_id)
    role = "reader" if conv.kind == "channel" else "member"
    now = _now()
    added = 0
    joined_names = []
    wanted = list(payload.get("user_ids") or [])
    wanted.extend(_expand_class_groups(db, user, payload.get("class_groups")))
    invited = 0
    for uid in wanted:
        if _participant(db, conv_id, uid) is not None:
            continue
        u = db.query(User).filter(User.id == uid, User.deleted == False).first()  # noqa: E712
        if u is None:
            continue
        if _needs_invite(user, u):
            #То же правило, что при создании группы: студент сотрудника не записывает, а
            #зовёт. Держать его в двух местах нельзя — потому решение и вынесено в
            #_needs_invite, а не написано условием здесь.
            if _invite(db, conv_id, uid) is None:
                db.add(ConversationInvite(conversation_id=conv_id, user_id=uid,
                                          invited_by=user.id, created_at=now))
                invited += 1
            continue
        db.add(ConversationParticipant(conversation_id=conv_id, user_id=uid, role=role, joined_at=now))
        joined_names.append((uid, u.full_name or u.name or u.login))
        added += 1
    db.commit()
    if invited:
        _notify_invites(db, conv_id, conv.title or "", user)
    if conv.kind == "group":            #§D6: системные «вступил» — ТОЛЬКО для групп. Канал
        for uid, name in joined_names:  #может разом набрать сотню читателей (весь курс) —
            _system(db, conv_id, "user_joined", uid, name)   #лента не должна утонуть в этом.
    if added:
        _broadcast(db, conv_id)
    return {"added": added, "invited": invited}


@router.delete("/chats/{conv_id}/members/{uid}")
def remove_member(conv_id: str, uid: str, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Убрать участника (право kick — owner/admin по умолчанию, либо кастомная роль с этим
    правом); себя может убрать любой (=покинуть). Владельца не трогаем."""
    if uid != user.id:
        _require_permission(db, conv_id, user, "kick")
    else:
        _require_participant(db, conv_id, user)
    conv = _conversation(db, conv_id)
    p = _participant(db, conv_id, uid)
    if p is not None and p.role != "owner":
        u = db.query(User).filter(User.id == uid).first()
        name = (u.full_name or u.name or u.login) if u else uid
        db.delete(p)
        db.commit()
        if conv.kind == "group":        #§D6: «вышел» — только для групп
            _system(db, conv_id, "user_left", uid, name)
        _broadcast(db, conv_id)
    return {"ok": True}


@router.post("/chats/{conv_id}/members/{uid}/role")
def set_member_role(conv_id: str, uid: str, payload: dict = Body(...),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Назначить роль участнику — билдовую (`role`) ИЛИ кастомную (`custom_role_id`,
    взаимоисключающе). Право manage_roles — по умолчанию owner/admin, либо обладатель
    кастомной роли с этим правом (см. _permissions_for). Владельца не понижаем этим путём."""
    _require_permission(db, conv_id, user, "manage_roles")
    tp = _participant(db, conv_id, uid)
    if tp is None:
        raise HTTPException(status_code=404, detail="Участник не найден")
    if tp.role == "owner":
        raise HTTPException(status_code=400, detail="Нельзя изменить роль владельца")
    custom_role_id = payload.get("custom_role_id")
    if custom_role_id:
        cr = (db.query(ConversationRole)
              .filter(ConversationRole.id == custom_role_id,
                      ConversationRole.conversation_id == conv_id).first())
        if cr is None:
            raise HTTPException(status_code=404, detail="Роль не найдена")
        tp.custom_role_id = custom_role_id
    else:
        role = payload.get("role")
        if role not in ("admin", "member", "writer", "reader"):
            raise HTTPException(status_code=400, detail="Некорректная роль")
        tp.role = role
        tp.custom_role_id = None
    db.commit()
    return {"ok": True}


@router.get("/chats/{conv_id}/roles")
def list_roles(conv_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Кастомные роли беседы + шаблоны для быстрого создания (не хранятся, пока не
    сохранены — см. create_role)."""
    _require_participant(db, conv_id, user)
    rows = (db.query(ConversationRole)
            .filter(ConversationRole.conversation_id == conv_id)
            .order_by(ConversationRole.created_at).all())
    return {"roles": [{"id": r.id, "name": r.name, "permissions": r.permissions or []} for r in rows],
            "templates": _ROLE_TEMPLATES, "all_permissions": list(_ALL_PERMISSIONS)}


@router.post("/chats/{conv_id}/roles")
def create_role(conv_id: str, payload: dict = Body(...),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_permission(db, conv_id, user, "manage_roles")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Нужно название роли")
    perms = [p for p in (payload.get("permissions") or []) if p in _ALL_PERMISSIONS]
    role = ConversationRole(id=f"crole:{uuid4().hex}", conversation_id=conv_id,
                            name=name[:60], permissions=perms, created_at=_now())
    db.add(role)
    db.commit()
    return {"id": role.id, "name": role.name, "permissions": role.permissions}


@router.put("/chats/{conv_id}/roles/{role_id}")
def update_role(conv_id: str, role_id: str, payload: dict = Body(...),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_permission(db, conv_id, user, "manage_roles")
    role = (db.query(ConversationRole)
            .filter(ConversationRole.id == role_id, ConversationRole.conversation_id == conv_id).first())
    if role is None:
        raise HTTPException(status_code=404, detail="Роль не найдена")
    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Нужно название роли")
        role.name = name[:60]
    if "permissions" in payload:
        role.permissions = [p for p in (payload.get("permissions") or []) if p in _ALL_PERMISSIONS]
    db.commit()
    return {"id": role.id, "name": role.name, "permissions": role.permissions}


@router.delete("/chats/{conv_id}/roles/{role_id}")
def delete_role(conv_id: str, role_id: str, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Удалить кастомную роль — участники с ней откатываются на билдовую member."""
    _require_permission(db, conv_id, user, "manage_roles")
    role = (db.query(ConversationRole)
            .filter(ConversationRole.id == role_id, ConversationRole.conversation_id == conv_id).first())
    if role is None:
        raise HTTPException(status_code=404, detail="Роль не найдена")
    (db.query(ConversationParticipant)
     .filter(ConversationParticipant.conversation_id == conv_id,
             ConversationParticipant.custom_role_id == role_id)
     .update({"custom_role_id": None, "role": "member"}))
    db.delete(role)
    db.commit()
    return {"ok": True}


# ── Игнор участника (личное, см. модель ConversationIgnore) ────────────────────────────
@router.post("/chats/{conv_id}/ignore/{uid}")
def ignore_member(conv_id: str, uid: str, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    _require_participant(db, conv_id, user)
    exists = (db.query(ConversationIgnore)
              .filter(ConversationIgnore.conversation_id == conv_id,
                      ConversationIgnore.viewer_id == user.id,
                      ConversationIgnore.ignored_user_id == uid).first())
    if exists is None:
        db.add(ConversationIgnore(conversation_id=conv_id, viewer_id=user.id, ignored_user_id=uid))
        db.commit()
    return {"ok": True}


@router.delete("/chats/{conv_id}/ignore/{uid}")
def unignore_member(conv_id: str, uid: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    _require_participant(db, conv_id, user)
    (db.query(ConversationIgnore)
     .filter(ConversationIgnore.conversation_id == conv_id,
             ConversationIgnore.viewer_id == user.id,
             ConversationIgnore.ignored_user_id == uid)
     .delete())
    db.commit()
    return {"ok": True}


@router.get("/chats/{conv_id}")
def conversation_info(conv_id: str, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """Инфо о беседе (для шапки группы/канала): тип, название, участники и их роли, моя роль."""
    conv = _conversation(db, conv_id)
    part = _require_participant(db, conv_id, user)
    parts = (db.query(ConversationParticipant)
             .filter(ConversationParticipant.conversation_id == conv_id).all())
    urows = {u.id: u for u in db.query(User).filter(
        User.id.in_([p.user_id for p in parts]))} if parts else {}
    onl = _online_logins()
    sm = _status_map(db, [p.user_id for p in parts])
    #Кастомные роли участников — одним запросом, а не по одной на строку.
    crole_ids = {p.custom_role_id for p in parts if p.custom_role_id}
    croles = {r.id: r.name for r in (db.query(ConversationRole)
              .filter(ConversationRole.id.in_(crole_ids)).all())} if crole_ids else {}
    people = []
    for p in parts:
        u = urows.get(p.user_id)
        prefs = (u.prefs if u is not None and isinstance(u.prefs, dict) else {})
        st = sm.get(p.user_id, {})
        people.append({
            "user_id": p.user_id,
            "full_name": (u.full_name or u.name or u.login) if u else p.user_id,
            "role": p.role,                       #owner | admin | writer | member | reader
            "custom_role_id": p.custom_role_id or None,
            "custom_role_name": croles.get(p.custom_role_id, "") if p.custom_role_id else "",
            "silenced": bool(p.silenced),          #«/mute» модератора — не путать с muted
            "online": bool(u) and u.login in onl,
            #Карточка участника в панели «О беседе»: аватар и контекст (группа/предметы).
            "avatar": prefs.get("avatar", "") or "",
            "bio": prefs.get("bio", "") or "",
            "profile_color": prefs.get("profile_color", "") or "",
            "profile_banner": prefs.get("profile_banner", "") or "",   #гифка-баннер карточки
            "name_font": prefs.get("name_font", "") or "",   #§5.4 «стиль никнейма»
            "name_effect": prefs.get("name_effect", "") or "",   #3.7 — эффект и цвет имени
            "name_color": prefs.get("name_color", "") or "",
            "user_role": (u.role if u else ""),   #student | teacher | admin (роль в системе)
            "group_name": (u.group_name or "") if u else "",
            "subjects": (u.subjects or []) if (u is not None and u.role == "teacher") else [],
            #§D7: статус поверх presence (dnd/studying/away + текст у преподавателя).
            "status_kind": st.get("kind", "") or "",
            "status_text": st.get("custom_text", "") or "",
            #Галочки «отправлено/прочитано» в ЛС (клиент сравнивает created_at СВОИХ
            #сообщений с last_read_at СОБЕСЕДНИКА — без похода на сервер за каждым тиком).
            "last_read_at": p.last_read_at or "",
        })
    #Владелец — первым, дальше по роли и алфавиту: сразу видно, кто создал беседу.
    _ORDER = {"owner": 0, "admin": 1, "writer": 2, "member": 3, "reader": 4}
    people.sort(key=lambda x: (_ORDER.get(x["role"], 9), (x["full_name"] or "").lower()))
    my_ignored = [r[0] for r in (db.query(ConversationIgnore.ignored_user_id)
                                 .filter(ConversationIgnore.conversation_id == conv_id,
                                         ConversationIgnore.viewer_id == user.id).all())]
    return {"conversation_id": conv.id, "kind": conv.kind, "title": conv.title,
            "avatar": conv.avatar or "",
            "about": conv.about, "owner_id": conv.owner_id, "is_public": conv.is_public,
            "my_role": part.role, "participants": people, "subscribers": len(people),
            #Свой id клиент иначе не знает (в JWT/сторе только логин+роль) — нужен, чтобы
            #не рисовать кнопки «Выгнать»/«Игнорировать» на собственной строке участника.
            "my_user_id": user.id,
            #Права участника в ЭТОЙ беседе (§ролей) — веб решает по ним, показывать ли
            #«Выгнать»/«Выдать роль» и доступность /mute, /clear в слэш-автодополнении.
            "my_permissions": sorted(_permissions_for(db, part)),
            "my_ignored_user_ids": my_ignored,
            #§D5: метка прочтения ДО открытия чата — клиент строит по ней разделитель
            #«Новые сообщения» (снимаем ДО markReadActive, иначе она бы уже сдвинулась).
            "my_last_read_at": part.last_read_at or "",
            #Системные каналы (§D12/§12): клиенту нужно различать их тип — например,
            #показать команду /отчет только в канале отчётов куратора для группы.
            "is_system": bool(conv.is_system), "system_kind": conv.system_kind or ""}


@router.patch("/chats/{conv_id}")
def rename_conversation(conv_id: str, payload: dict = Body(...),
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Название, описание и АВАТАРКА группы/канала (owner/admin). Личный чат и беседу с
    модерацией не трогаем — у них нет своего лица: там его задаёт собеседник. Смена
    названия и картинки пишет системное сообщение (§D6).

    Проверяем ТИП беседы раньше роли: у личного чата участники всегда 'member' (не
    owner/admin), и без этого порядка запрос падал бы в 403 «недостаточно прав» вместо
    внятного 400 «эту беседу нельзя переименовать» — путало бы причину отказа.

    🔥 ОПИСАНИЕ СОХРАНЯЛОСЬ ТОЛЬКО ВМЕСТЕ С НАЗВАНИЕМ (найдено 02.09.2026 при добавлении
    аватарки). `about` лежал ВНУТРИ `if title != conv.title`, поэтому правка одного лишь
    описания молча не доезжала: запрос отвечал `{"ok": true}` и прежним текстом, клиент
    закрывал форму, а через минуту текст возвращался к старому. Отказ тихий и
    правдоподобный — ровно тот класс, который у нас ловится только чтением.

    ⚠️ Поля НЕОБЯЗАТЕЛЬНЫЕ и смотрим на ПРИСУТСТВИЕ ключа, а не на непустоту (тот же
    приём, что у дня рождения в §инвариантов): иначе форма, где меняют одну картинку,
    затирала бы описание, а явно очищенное описание не доезжало бы никогда."""
    conv = _conversation(db, conv_id)
    part = _require_participant(db, conv_id, user)
    if conv.kind not in ("group", "channel"):
        raise HTTPException(status_code=400, detail="Эту беседу нельзя переименовать")
    if part.role not in _MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    changed, events = False, []

    if "title" in payload:
        title = (payload.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Нужно название")
        title = title[:120]
        if title != conv.title:
            conv.title = title
            changed = True
            events.append(("title_changed", title))

    if "about" in payload:
        about = (payload.get("about") or "").strip()[:500]
        if about != (conv.about or ""):
            conv.about = about
            changed = True          #системного сообщения НЕТ: описание читают в карточке
                                    #беседы, и строка в ленте на каждую правку — шум

    if "avatar" in payload:
        #🔒 Проверяем ТОЙ ЖЕ функцией, что и аватарку профиля: картинка уедет в `<img src>`
        #ко ВСЕМ участникам, и чужая ссылка означала бы, что один человек включил слежку
        #за всей группой. Второй копии белого списка не заводим — она разошлась бы с
        #первой молча и в опасную сторону.
        from ..me import _sanitize_profile_media
        box = {"avatar": payload.get("avatar")}
        _sanitize_profile_media(box)
        if box["avatar"] != (conv.avatar or ""):
            conv.avatar = box["avatar"]
            changed = True
            #Событие БЕЗ самой картинки: тело системного сообщения хранится строкой и
            #уезжает в предпросмотр списка чатов — data:URL на сотню килобайт там не
            #место. Клиент возьмёт свежую картинку из карточки беседы.
            events.append(("avatar_changed", "1" if conv.avatar else ""))

    if changed:
        db.commit()
        for event, arg in events:
            _system(db, conv_id, event, arg)   #§D6
        _broadcast(db, conv_id)
    return {"ok": True, "title": conv.title, "about": conv.about, "avatar": conv.avatar or ""}
