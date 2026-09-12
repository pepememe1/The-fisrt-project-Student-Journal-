"""
moderation.py — Модерация (`mod_router`, префикс /web/admin/messenger): жалобы на
сообщения и на профили, обращения, просмотр переписки по тикету, ответ, удаление
сообщения, мьют СО СРОКОМ, история наказаний, правка профиля по жалобе.

🔑 ДВЕРЬ ЗДЕСЬ — `require_moderation` (админ ИЛИ выделенный модератор), а НЕ
`require_admin`. Разница не косметическая: через `require_admin` проходит весь
административный доступ (оценки, группы, расписание, раздел «Сервер»), и добавить туда
роль модератора значило бы отдать ему журнал всего колледжа. Подробности — в докстринге
`deps.require_moderation`.
⚠️ Имя параметра `admin` в ручках оставлено намеренно: в аудит уходит `admin.role`, то
есть настоящая роль различается и пишется, а переименование тронуло бы каждую строку
журнала ради точности, которой там и так нет нужды.

Часть пакета `routers/messenger` (разрез 3.7.7). Общий роутер, проверки прав и
сборка ответов — в `_common.py`; порядок регистрации маршрутов задаёт `__init__.py`.
"""
from ._common import *      # noqa: F401,F403 — роутеры, модели, хелперы


# ── Чат с модерацией (сторона пользователя, кнопка ⚙) ────────────────────────────────
@router.get("/moderation")
def moderation_chat(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Личная беседа пользователя с командой модерации (создаётся при первом обращении).
    Пользователь пишет как обычно (он участник); отвечает модерация через админ-эндпоинт."""
    #Админ САМ и есть модерация — обращаться ему некуда (отвечает через /web/admin/messenger).
    #Иначе появлялся бы бессмысленный чат «админ пишет сам себе в поддержку».
    if user.role == "admin":
        raise HTTPException(status_code=403, detail="Администратор отвечает на обращения, а не пишет в них")
    conv_id = f"mod:{user.id}"
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if conv is None:
        now = _now()
        conv = Conversation(id=conv_id, kind="moderation", title="Модерация", created_at=now)
        db.add(conv)
        db.add(ConversationParticipant(conversation_id=conv_id, user_id=user.id,
                                       role="member", joined_at=now))
        db.commit()
    return {"conversation_id": conv_id, "kind": "moderation"}


@mod_router.get("/reports")
def mod_reports(status: str = Query("open"), admin: User = Depends(require_moderation),
                db: Session = Depends(get_db)):
    """Очередь жалоб (тикетов). status='' — все, иначе фильтр по статусу."""
    q = db.query(MessageReport)
    if status:
        q = q.filter(MessageReport.status == status)
    rows = q.order_by(MessageReport.id.desc()).limit(300).all()
    return {"reports": [_report_out(db, r) for r in rows]}


@mod_router.post("/reports/{rid}/resolve")
def mod_resolve(rid: int, payload: dict = Body(...), request: Request = None,
                admin: User = Depends(require_moderation), db: Session = Depends(get_db)):
    """Обработать тикет: сменить статус + заметка. Пишется в аудит."""
    r = db.query(MessageReport).filter(MessageReport.id == rid).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    status = payload.get("status")
    if status not in _REPORT_STATUSES:
        raise HTTPException(status_code=400, detail="Некорректный статус")
    r.status = status
    r.handled_by = admin.login
    r.handled_at = _now()
    r.resolution_note = (payload.get("resolution_note") or "")[:1000]
    db.commit()
    audit.log(db, request, actor=admin.login, role=admin.role,
              action="msg.report.resolve", target=str(rid), detail=status)
    return {"ok": True}


@mod_router.get("/conversations")
def mod_conversations(q: str = Query(""), kind: str = Query(""),
                      admin: User = Depends(require_moderation), db: Session = Depends(get_db)):
    """Список бесед для модерации (поиск по участникам, фильтр по типу).

    🔥 ОБРАЩЕНИЯ ПОКАЗЫВАЮТ ПОСЛЕДНЕЕ СООБЩЕНИЕ И ЧИСЛО НОВЫХ. Раньше вкладка отдавала
    только названия, то есть человек, написавший в поддержку, был неотличим от того, кто
    обратился месяц назад и получил ответ: чтобы узнать, есть ли новое, приходилось
    открывать беседы по очереди. Это наш записанный урок «заявка без уведомления
    бесполезна» в чистом виде — очередь, по которой надо ходить руками, перестают
    просматривать.

    ⚠️ «Новые» считаются от последнего ОТВЕТА МОДЕРАЦИИ, а не от чьей-то метки прочтения.
    Модераторов несколько, метка `last_read_at` у каждого своя, и «непрочитано» по ней
    означало бы «не читал лично я» — то есть одно и то же обращение висело бы новым у
    троих, пока каждый не откроет. Здесь вопрос другой и общий для всех: ответили ли
    человеку после того, как он написал.
    """
    query = db.query(Conversation)
    if kind:
        query = query.filter(Conversation.kind == kind)
    convs = query.order_by(Conversation.created_at.desc()).limit(300).all()
    ql = (q or "").strip().lower()
    onl = _online_logins()
    out = []
    for c in convs:
        parts = (db.query(ConversationParticipant)
                 .filter(ConversationParticipant.conversation_id == c.id).all())
        names, people = [], []
        mset = _muted_set(db, [p.user_id for p in parts])
        for p in parts:
            u = db.query(User).filter(User.id == p.user_id).first()
            if u:
                names.append(u.full_name or u.login)
                #карточка: аватар, ФИО, роль, группа/предметы + состояние глобального мьюта
                people.append(_safe_user(u, onl, u.id in mset))
        if ql and not any(ql in n.lower() for n in names):
            continue
        item = {"conversation_id": c.id, "kind": c.kind,
                "title": c.title or " · ".join(names), "participants": names,
                "people": people}
        item.update(_inbox_state(db, c))
        out.append(item)
    #Свежие сверху: у очереди обращений порядок «кто последним написал» — единственный
    #осмысленный. `created_at` беседы отвечал на другой вопрос («когда её завели»), и
    #обращение месячной давности с новым сообщением уезжало в конец списка.
    out.sort(key=lambda x: x.get("last_at") or "", reverse=True)
    return {"conversations": out}


def _inbox_state(db: Session, conv) -> dict:
    """Последнее сообщение беседы и сколько человек написал после ответа модерации.

    ⚠️ Ответом модерации считается сообщение от того, кто НЕ является хозяином обращения:
    сверять роль автора нельзя — в `mod:{id}` пишет и админ, и модератор, а роль у них
    разная; сверять список модераторов значило бы держать его второй копией здесь."""
    last = (db.query(Message)
            .filter(Message.conversation_id == conv.id, Message.deleted_at.is_(None))
            .order_by(Message.id.desc()).first())
    if last is None:
        return {"last_at": conv.created_at or "", "last_body": "", "last_from": "", "fresh": 0}
    owner = ""
    if conv.kind == "moderation":
        #Хозяин обращения — единственный его участник (модерация участником не является,
        #она отвечает через свой эндпоинт). Отсюда и определение «кто спрашивает».
        p = (db.query(ConversationParticipant)
             .filter(ConversationParticipant.conversation_id == conv.id).first())
        owner = p.user_id if p else ""
    fresh = 0
    if owner:
        last_reply = (db.query(Message)
                      .filter(Message.conversation_id == conv.id,
                              Message.sender_id != owner,
                              Message.deleted_at.is_(None))
                      .order_by(Message.id.desc()).first())
        q = db.query(Message).filter(Message.conversation_id == conv.id,
                                     Message.sender_id == owner,
                                     Message.deleted_at.is_(None))
        if last_reply is not None:
            q = q.filter(Message.id > last_reply.id)
        fresh = q.count()
    return {"last_at": last.created_at or "", "last_body": (last.body or "")[:200],
            "last_from": last.sender_id or "", "fresh": fresh}


@mod_router.get("/conversations/{conv_id}/messages")
def mod_conversation_messages(conv_id: str, report_id: int = Query(0), request: Request = None,
                              admin: User = Depends(require_moderation), db: Session = Depends(get_db)):
    """Прочитать ЛЮБУЮ беседу (модерация). Каждый вызов пишется в аудит (152-ФЗ).

    §правка: `report_id` — необязательный, передаёт фронт ТОЛЬКО во вкладке «Жалобы»
    (просмотр из тикета, см. AdminMessenger.vue::openConversation); вкладка «Обращения»
    его не знает и продолжает работать как раньше. Если тикет уже закрыт (resolved/
    dismissed/expired) — доступ к переписке ПО ЭТОМУ ТИКЕТУ закрывается: свободный
    просмотр чужой переписки без активного расследования — риск сам по себе (живой
    отзыв). Проверка СЕРВЕРНАЯ, а не только дизейбл кнопки во фронте — иначе прямой
    запрос к этому же URL с браузерным дебагом обходил бы «закрытую» кнопку."""
    _conversation(db, conv_id)
    if report_id:
        #Правило «живой тикет» одно на два действия — просмотр и наказание. Раньше оно
        #было расписано здесь, а мьют не проверял его вовсе; теперь проверка общая
        #(`_require_live_ticket`), и разойтись двум копиям больше нечем.
        rep = _require_live_ticket(db, report_id)
        if rep.conversation_id != conv_id:
            raise HTTPException(status_code=404, detail="Тикет не найден")
    rows = (db.query(Message).filter(Message.conversation_id == conv_id)
            .order_by(Message.id.asc()).all())
    #ФИО автора — иначе в переписке с 2+ участниками (жалоба, групповой чат) не видно,
    #кто что написал (тот же _names_for, что уже используют обычные списки сообщений).
    names = _names_for(db, [m.sender_id for m in rows])
    #§правка: модерация должна видеть ПОЛНУЮ картину — текст удалённых сообщений и всю
    #цепочку правок. Обычный _msg_out() их НАМЕРЕННО прячет (для всех остальных
    #читателей это правильно), здесь — противоположный, осознанный контракт: доступ и
    #так журналируется аудитом ниже, а подотчётность важнее приватности при активной
    #проверке жалобы (см. комментарий у edit_message: «модерация должна видеть оригинал»).
    ids = [m.id for m in rows]
    edits_by_msg: dict[int, list] = {}
    if ids:
        for e in (db.query(MessageEdit).filter(MessageEdit.message_id.in_(ids))
                  .order_by(MessageEdit.id.asc()).all()):
            edits_by_msg.setdefault(e.message_id, []).append({"body": e.body_before, "at": e.edited_at})
    out = []
    for m in rows:
        d = _msg_out(m, sender_name=names.get(m.sender_id, ""))
        if m.deleted_at:
            d["body"] = m.body or ""
        versions = edits_by_msg.get(m.id)
        if versions:
            d["edit_versions"] = versions + [{"body": m.body or "", "at": m.edited_at or m.created_at}]
        out.append(d)
    _attach_rich_meta(db, out, admin.id)               #админ читает ту же ленту, что и участники
    audit.log(db, request, actor=admin.login, role=admin.role,
              action="msg.moderation.view", target=conv_id)
    return {"messages": out}


@mod_router.post("/conversations/{conv_id}/reply")
def mod_reply(conv_id: str, payload: dict = Body(...), request: Request = None,
              admin: User = Depends(require_moderation), db: Session = Depends(get_db)):
    """Ответ модерации в ЧАТ ОБРАЩЕНИЙ пользователя (kind='moderation'). Отправитель — админ;
    проверка участия НЕ применяется (это и есть право модерации). Пишется в аудит.

    ⚠️ Ограничено kind='moderation': раньше эндпоинт позволял админу вписать сообщение в
    ЛЮБУЮ беседу, включая приватный 1-на-1 чужих людей, от своего имени — это выходит за
    рамки «ответа модерации». Модерация читает любую переписку (mod_conversation_messages,
    с аудитом), но писать может только в официальный чат обращений."""
    conv = _conversation(db, conv_id)
    if conv.kind != "moderation":
        raise HTTPException(
            status_code=403,
            detail="Ответ модерации доступен только в чате обращений пользователя.")
    body = (payload.get("body") or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    import profanity_filter
    body = profanity_filter.censor(body, mask=profanity_filter.MESSENGER_SAFE_MASK)
    m = Message(conversation_id=conv_id, sender_id=admin.id, body=body[:_MAX_MSG_CHARS],
                created_at=_now())
    db.add(m)
    db.commit()
    db.refresh(m)
    _broadcast(db, conv_id)
    audit.log(db, request, actor=admin.login, role=admin.role,
              action="msg.moderation.reply", target=conv_id)
    return _msg_out(m, admin.id)


@mod_router.delete("/messages/{mid}")
def mod_delete_message(mid: int, request: Request = None,
                       admin: User = Depends(require_moderation), db: Session = Depends(get_db)):
    """Удалить ЛЮБОЕ сообщение у всех (модерация). Обычный DELETE /messages/{id} требует
    участия в беседе и авторства — админ в чужой переписке не участник, поэтому для
    модерации отдельный эндпоинт. Сообщение становится тумбстоуном (текст стирается,
    факт остаётся), закрепление снимается. Пишется в аудит (152-ФЗ, подотчётность)."""
    m = _message_in_conv(db, mid)
    if not m.deleted_at:
        m.deleted_at = _now()
        m.pinned = False
        db.commit()
        _broadcast(db, m.conversation_id)
    audit.log(db, request, actor=admin.login, role=admin.role,
              action="msg.moderation.delete", target=str(mid), detail=m.conversation_id)
    return {"ok": True, "id": mid, "deleted": True}


@mod_router.post("/users/{uid}/mute")
def mod_mute_user(uid: str, payload: dict = Body(default={}), request: Request = None,
                  admin: User = Depends(require_moderation), db: Session = Depends(get_db)):
    """Мьют/размьют пользователя модерацией — ТЕПЕРЬ СО СРОКОМ.

    ПОЧЕМУ БЕССРОЧНЫЙ МЬЮТ БОЛЬШЕ НЕ ВЫДАЁТСЯ. Наказание «пока не снимут» снимать
    некому: о замьюченном не вспоминают — он просто перестаёт писать, и продукт считает
    это нормой. Та же болезнь, что у сигнала, который всегда красный. Срок обязателен;
    дни, часы и минуты складываются (hours=3 — три часа; days=1, hours=12 — полтора суток).
    ⚠️ Уже наложенные бессрочные мьюты НЕ трогаются (см. миграцию
    `_ensure_muted_user_term_columns`): амнистия выкладкой была бы решением, которого
    никто не принимал.

    КОГО НЕЛЬЗЯ: администратора, другого модератора и САМОГО СЕБЯ. Первые два — «модераторы
    не глушат друг друга», иначе спор двоих решает тот, кто быстрее нажал. Третье выглядит
    безобидно, но замьютивший себя модератор не сможет ответить в обращении.

    ТИКЕТ. Мьют ИЗ жалобы (`report_id`) требует ЖИВОГО тикета — то же правило, что у
    просмотра переписки: закрытый тикет означает, что расследование окончено. Проверка
    стоит здесь, а не только в виде погашенной кнопки: кнопку обходит прямой запрос.
    """
    target = db.query(User).filter(User.id == uid, User.deleted == False).first()  # noqa: E712
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя замьютить самого себя")
    if target.role in MODERATION_ROLES:
        raise HTTPException(status_code=400,
                            detail="Нельзя замьютить администратора или модератора")

    report_id = int(payload.get("report_id") or 0)
    if report_id:
        _require_live_ticket(db, report_id)

    muted = bool(payload.get("muted", True))
    row = db.query(MutedUser).filter(MutedUser.user_id == uid).first()

    if not muted:
        if row is not None:
            db.delete(row)
            db.commit()
        audit.log(db, request, actor=admin.login, role=admin.role,
                  action="msg.moderation.unmute", target=uid)
        return {"ok": True, "user_id": uid, "muted": False}

    minutes = _mute_minutes(payload)
    until = _iso_plus_minutes(minutes)
    reason = (payload.get("reason") or "").strip()[:300]
    if row is None:
        row = MutedUser(user_id=uid)
        db.add(row)
    row.muted_by = admin.login
    row.muted_by_role = admin.role
    row.muted_at = _now()
    row.muted_until = until
    row.reason = reason
    db.commit()
    #В журнал уходит СРОК и ПРИЧИНА, а не только факт: без них через неделю не ответить
    #ни человеку («за что»), ни проверяющему («на каком основании»).
    audit.log(db, request, actor=admin.login, role=admin.role,
              action="msg.moderation.mute", target=uid,
              detail="до %s; %d мин; причина: %s" % (until, minutes, reason or "не указана"))
    return {"ok": True, "user_id": uid, "muted": True, "muted_until": until, "reason": reason}


#Готовые сроки (часы) — те же, что предлагает интерфейс. Список ЗДЕСЬ, потому что границы
#всё равно проверяет сервер: кнопки обходятся, а правило у них одно.
MUTE_PRESET_HOURS = (1, 3, 5, 12, 24)
#Потолок — год. Не «на всякий случай»: без него опечатка в поле «дней» (9999) выдаёт
#пожизненный мьют, ради отмены которого срок и заводился.
MUTE_MAX_MINUTES = 365 * 24 * 60


def _mute_minutes(payload: dict) -> int:
    """Длительность мьюта в минутах. Дни/часы/минуты СКЛАДЫВАЮТСЯ — интерфейсу не нужно
    приводить «полтора суток» к одной единице, а нам не нужно гадать, в какой единице
    пришло число."""
    try:
        days = int(payload.get("days") or 0)
        hours = int(payload.get("hours") or 0)
        minutes = int(payload.get("minutes") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Срок мьюта задан не числом")
    total = days * 24 * 60 + hours * 60 + minutes
    if total <= 0:
        raise HTTPException(status_code=400, detail="Укажите срок мьюта")
    if total > MUTE_MAX_MINUTES:
        raise HTTPException(status_code=400, detail="Срок мьюта больше года")
    return total


def _iso_plus_minutes(minutes: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _require_live_ticket(db: Session, report_id: int):
    """Тикет существует и ещё расследуется. ОДНА проверка на два потребителя (просмотр
    переписки и наказание): две копии одного правила разошлись бы молча, и разойтись они
    могли бы только в сторону «наказать по закрытому тикету»."""
    rep = db.query(MessageReport).filter(MessageReport.id == report_id).first()
    if rep is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    if rep.status not in ("open", "in_review"):
        raise HTTPException(status_code=403,
                            detail="Тикет закрыт — действия по нему недоступны")
    return rep


@mod_router.get("/users/{uid}/history")
def mod_user_history(uid: str, admin: User = Depends(require_moderation),
                     db: Session = Depends(get_db)):
    """История наказаний и жалоб по человеку: первый это раз или пятый.

    ЧИТАЕТСЯ ИЗ ЖУРНАЛА АУДИТА, А НЕ ИЗ СВОЕЙ ТАБЛИЦЫ, и причина не в экономии: строка в
    `muted_users` живёт ровно до истечения срока и удаляется, то есть таблица наказаний
    по построению помнит только действующие. Аудит же только-на-добавление и с цепочкой
    хешей — именно он отвечает на вопрос «что с этим человеком делали раньше». Вторая
    таблица рядом стала бы второй правдой, притом более слабой.
    ⚠️ Отдаём `audit_since`: записей до включения цепочки в журнале нет по ДРУГОЙ причине,
    и выдавать их отсутствие за «нарушений не было» нельзя — интерфейс не должен врать
    молчанием.
    """
    rows = (db.query(AuditEvent)
            .filter(AuditEvent.target == uid,
                    AuditEvent.action.in_(("msg.moderation.mute", "msg.moderation.unmute",
                                           "msg.moderation.delete", "msg.report.resolve",
                                           "msg.moderation.profile")))
            .order_by(AuditEvent.id.desc()).limit(100).all())
    reports = (db.query(MessageReport).filter(MessageReport.reported_user_id == uid)
               .order_by(MessageReport.id.desc()).limit(50).all())
    ureports = (db.query(UserReport).filter(UserReport.reported_user_id == uid)
                .order_by(UserReport.id.desc()).limit(50).all())
    row = _mute_row(db, uid)
    oldest = db.query(AuditEvent).order_by(AuditEvent.id.asc()).first()
    return {
        "user_id": uid,
        "muted": row is not None,
        "muted_until": (row.muted_until if row else ""),
        "mute_reason": (row.reason if row else ""),
        "actions": [{"at": a.ts, "action": a.action, "by": a.actor,
                     "role": a.role, "detail": a.detail} for a in rows],
        "reports_on_messages": len(reports),
        "reports_on_profile": len(ureports),
        "audit_since": (oldest.ts if oldest else ""),
    }


# ── Жалобы на ПРОФИЛИ: своя очередь ──────────────────────────────────────────────────
@mod_router.get("/user-reports")
def mod_user_reports(status: str = Query("open"),
                     admin: User = Depends(require_moderation), db: Session = Depends(get_db)):
    """Очередь жалоб на профили. status='' — все, иначе фильтр по статусу.

    ⚠️ ОТДЕЛЬНАЯ ОЧЕРЕДЬ, А НЕ ФИЛЬТР В ОБЩЕЙ. У жалобы на сообщение разбор начинается с
    переписки («что он написал»), у жалобы на профиль — с самого профиля («как он себя
    назвал»), и действия разные: там удалить сообщение, здесь стереть поле. Сложив их в
    один список, мы заставили бы модератора на каждой строке вспоминать, какие кнопки
    сейчас осмысленны, — а первая забытая означала бы удаление постороннего текста
    (ровно этим уже обернулась общая очередь с отзывами о срезах понимания)."""
    q = db.query(UserReport)
    if status:
        q = q.filter(UserReport.status == status)
    rows = q.order_by(UserReport.id.desc()).limit(300).all()
    onl = _online_logins()
    out = []
    for r in rows:
        reported = db.query(User).filter(User.id == r.reported_user_id).first()
        reporter = db.query(User).filter(User.id == r.reporter_id).first()
        mset = _muted_set(db, [r.reported_user_id])
        out.append({
            "id": r.id, "field": r.field, "snapshot": r.snapshot,
            "reason_code": r.reason_code, "description": r.description,
            "created_at": r.created_at, "status": r.status,
            "handled_by": r.handled_by, "resolution_note": r.resolution_note,
            "reported": _safe_user(reported, onl, r.reported_user_id in mset) if reported else None,
            "reporter": _safe_user(reporter, onl) if reporter else None,
        })
    return {"reports": out}


@mod_router.post("/user-reports/{rid}/resolve")
def mod_resolve_user_report(rid: int, payload: dict = Body(...), request: Request = None,
                            admin: User = Depends(require_moderation),
                            db: Session = Depends(get_db)):
    """Обработать жалобу на профиль: статус + заметка. Пишется в аудит."""
    r = db.query(UserReport).filter(UserReport.id == rid).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    status = payload.get("status")
    if status not in _REPORT_STATUSES:
        raise HTTPException(status_code=400, detail="Некорректный статус")
    r.status = status
    r.handled_by = admin.login
    r.handled_at = _now()
    r.resolution_note = (payload.get("resolution_note") or "")[:1000]
    db.commit()
    audit.log(db, request, actor=admin.login, role=admin.role,
              action="msg.userreport.resolve", target=str(rid), detail=status)
    return {"ok": True}


# ── Люди, попавшие в поле зрения модерации ───────────────────────────────────────────
@mod_router.get("/users")
def mod_users(q: str = Query(""), admin: User = Depends(require_moderation),
              db: Session = Depends(get_db)):
    """Список людей, НА КОТОРЫХ ЕСТЬ ЖАЛОБА (или действующий мьют). Не каталог колледжа.

    🔒 ЭТО ГРАНИЦА, А НЕ УДОБСТВО, и поставлена она по прямому требованию: «просмотр
    списка пользователей с возможностью редактирования их профиля — только если на юзера
    отправят жалобу, без этого никак». Полный список людей модератору не нужен ни для
    одной его задачи, а выдать его значило бы отдать роли, заведённой разбирать
    конфликты, справочник всего колледжа с лицами и группами. Поиск здесь работает ВНУТРИ
    этого множества и найти постороннего не может по построению — то же правило, что
    §16: границу проводит отсутствие данных, а не проверка на экране.

    ⚠️ Мьют без жалобы тоже включён в выборку: наказанный человек обязан оставаться
    видимым тому, кто наказание снимает. Иначе снять мьют, выданный по тикету, который
    потом закрыли, было бы не с кого."""
    ids = set()
    for (uid,) in db.query(MessageReport.reported_user_id).distinct().all():
        if uid:
            ids.add(uid)
    for (uid,) in db.query(UserReport.reported_user_id).distinct().all():
        if uid:
            ids.add(uid)
    for (uid,) in db.query(MutedUser.user_id).all():
        if uid:
            ids.add(uid)
    if not ids:
        return {"users": []}
    rows = db.query(User).filter(User.id.in_(ids), User.deleted == False).all()  # noqa: E712
    ql = (q or "").strip().lower()
    onl = _online_logins()
    mset = _muted_set(db, [u.id for u in rows])
    out = []
    for u in rows:
        name = (u.full_name or u.login or "").lower()
        if ql and ql not in name:
            continue
        d = _safe_user(u, onl, u.id in mset)
        #Сколько жалоб — главный ответ на «первый раз или пятый». Считаем ОБЕ очереди:
        #человек, на профиль которого жаловались трижды, но на сообщения ни разу, для
        #модератора не «чистый».
        d["reports_open"] = (
            db.query(MessageReport).filter(MessageReport.reported_user_id == u.id,
                                           MessageReport.status.in_(("open", "in_review"))).count()
            + db.query(UserReport).filter(UserReport.reported_user_id == u.id,
                                          UserReport.status.in_(("open", "in_review"))).count())
        d["reports_total"] = (
            db.query(MessageReport).filter(MessageReport.reported_user_id == u.id).count()
            + db.query(UserReport).filter(UserReport.reported_user_id == u.id).count())
        row = _mute_row(db, u.id)
        d["muted_until"] = (row.muted_until if row else "")
        d["mute_reason"] = (row.reason if row else "")
        out.append(d)
    out.sort(key=lambda x: (-x["reports_open"], -x["reports_total"]))
    return {"users": out}


#Поля профиля, которые модерация вправе ПОЧИСТИТЬ. Список закрытый и умышленно короткий:
#сюда входит только то, что человек написал о себе сам и что видят все остальные.
_MOD_EDITABLE_FIELDS = ("bio", "avatar", "profile_banner", "name_effect", "name_color",
                        "name_font", "profile_color")


@mod_router.post("/users/{uid}/profile")
def mod_edit_profile(uid: str, payload: dict = Body(...), request: Request = None,
                     admin: User = Depends(require_moderation), db: Session = Depends(get_db)):
    """Почистить публичные поля профиля — ТОЛЬКО при живой жалобе на этого человека.

    🔒 БЕЗ ЖАЛОБЫ — ОТКАЗ. Правка чужого профиля «просто так» это не модерация, а доступ
    к чужому аккаунту; повод обязан существовать до действия, а не придумываться после.
    Живой жалобой считается открытый тикет любой из двух очередей — на сообщение или на
    профиль.

    🔑 ПОЛЯ ТОЛЬКО ЧИСТЯТСЯ, А НЕ ЗАДАЮТСЯ ПРОИЗВОЛЬНО. Значение приходит, но приводится
    к пустоте: разрешить модератору ВПИСАТЬ человеку «о себе» значило бы дать возможность
    сказать что-то от его имени — а под текстом стоит его лицо и его фамилия. Снять
    оскорбительное можно; написать за него нельзя.
    ⚠️ ФИО не трогается вовсе: это учётные данные из журнала, они приезжают синком и
    правятся администратором в своём разделе. Модератор, переименовавший студента, сломал
    бы ключ, по которому у того считаются оценки (§10).
    """
    target = db.query(User).filter(User.id == uid, User.deleted == False).first()  # noqa: E712
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    live = (db.query(MessageReport)
            .filter(MessageReport.reported_user_id == uid,
                    MessageReport.status.in_(("open", "in_review"))).first()
            or db.query(UserReport)
            .filter(UserReport.reported_user_id == uid,
                    UserReport.status.in_(("open", "in_review"))).first())
    if live is None:
        raise HTTPException(status_code=403,
                            detail="Правка профиля возможна только по открытой жалобе")
    fields = [f for f in (payload.get("clear") or []) if f in _MOD_EDITABLE_FIELDS]
    if not fields:
        raise HTTPException(status_code=400, detail="Не указано, какие поля очистить")
    prefs = dict(target.prefs if isinstance(target.prefs, dict) else {})
    for f in fields:
        prefs[f] = ""
    target.prefs = prefs
    target.updated_at = _now()
    db.commit()
    audit.log(db, request, actor=admin.login, role=admin.role,
              action="msg.moderation.profile", target=uid, detail=", ".join(fields))
    return {"ok": True, "cleared": fields}
