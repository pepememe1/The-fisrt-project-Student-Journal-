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


# ── ОБРАЩЕНИЯ: автоответчик, категории, тикеты ───────────────────────────────────────
#
# 🔑 ЗАЧЕМ АВТООТВЕТЧИК (12.09.2026, требование Влада). Человек пишет в модерацию «у меня
# проблема» — и до разбора нужен ещё один круг переписки, только чтобы выяснить, о чём
# речь. Список тем снимает этот круг: модератор открывает тикет, уже зная тему.
#
# 🔴 НО У АВТООТВЕТЧИКА ОБЯЗАНА БЫТЬ ДВЕРЬ НАРУЖУ. Робот, из которого нельзя выйти к
# человеку, — худшее, что бывает в поддержке: он превращает обращение в тупик ровно для
# тех случаев, которые в список не попали. Поэтому «Другое» и любая просьба позвать
# человека поднимают тикет СРОЧНЫМ и ставят его в самый верх очереди.
SUPPORT_CATEGORIES = [
    {"code": "harassment", "label": "Оскорбления или травля"},
    {"code": "spam", "label": "Спам или реклама"},
    {"code": "content", "label": "Неприемлемое содержимое"},
    {"code": "account", "label": "Доступ к аккаунту"},
    {"code": "mute", "label": "Не согласен с ограничением"},
    {"code": "other", "label": "Другое"},
    {"code": "human", "label": "Позвать человека"},
]
# Срочные по построению: наш список их случай не описывает, значит выбирать дальше нечего.
URGENT_CATEGORIES = {"other", "human"}
_CATEGORY_CODES = {c["code"] for c in SUPPORT_CATEGORIES}

# Фразы, по которым просьбу позвать человека узнаём в СВОБОДНОМ тексте. Список намеренно
# короткий и по основам: длинный превращается в угадывание, а цена ошибки здесь мала —
# срочный тикет просто раньше попадёт к модератору.
_HUMAN_STEMS = ("живой человек", "живого человека", "позов", "позва", "оператор",
                "не бот", "с человеком", "нужен человек")

# Через сколько суток молчания тикет, взятый в работу, закрывается сам.
# ⚠️ Проверяется ПРИ ЧТЕНИИ очереди, а не планировщиком: на одноядерном бою лишний поток
# дороже пользы, и это тот же приём, что у истёкшего мьюта и у напоминаний.
SUPPORT_AUTOCLOSE_DAYS = 14
# Сколько взятых в работу тикетов просматриваем за одно чтение очереди.
_AUTOCLOSE_SCAN_LIMIT = 200


def _ticket_open(db: Session, conv_id: str):
    """Действующий тикет беседы или None. Открытый и взятый в работу — оба «действующие»:
    пока модератор разбирает, второй тикет на то же обращение заводить нечего."""
    return (db.query(SupportTicket)
            .filter(SupportTicket.conversation_id == conv_id,
                    SupportTicket.status.in_(("open", "in_review")))
            .order_by(SupportTicket.id.desc()).first())


#Ответ на просьбу позвать человека — ОДНОЙ строкой: её пишут из двух мест (первое
#сообщение и эскалация внутри обращения), и две копии разошлись бы молча.
_HUMAN_ACK = ("Принято, зову человека. Обращение помечено срочным — "
              "модератор подключится к этому чату.")
#Подпись невыбранной темы. Пустая строка в очереди читается как поломка выдачи, а не как
#«человек ещё не ответил роботу».
NO_CATEGORY_LABEL = "Тема не выбрана"


def _category_label(code: str) -> str:
    if not code:
        return NO_CATEGORY_LABEL
    return next((c["label"] for c in SUPPORT_CATEGORIES if c["code"] == code), code)


def _autoresponder_text() -> str:
    lines = ["Здравствуйте! Это модерация. Чтобы разобрать быстрее, выберите тему обращения:"]
    lines += ["- " + c["label"] for c in SUPPORT_CATEGORIES]
    lines.append("Если ничего не подходит — выберите «Другое» или попросите позвать человека.")
    return "\n".join(lines)


def _post_system(db: Session, conv_id: str, body: str):
    """Служебная реплика в беседу обращения от лица «Вектора» (SYSTEM_SENDER_ID).

    ⚠️ Не от лица модератора: пока тикет никто не взял, подписывать автоответ живым
    человеком значит обещать, что он уже читает, — а он ещё нет."""
    m = Message(conversation_id=conv_id, sender_id=SYSTEM_SENDER_ID,
                body=body[:_MAX_MSG_CHARS], created_at=_now())
    db.add(m)
    db.commit()
    _broadcast(db, conv_id)
    return m


def looks_like_human_request(body: str) -> bool:
    """Просит ли человек живого человека. Отдельной функцией — её зовут и хук отправки,
    и тест; повтор условия рядом с вызывающим разошёлся бы молча."""
    low = (body or "").lower()
    return any(s in low for s in _HUMAN_STEMS)


def open_support_ticket(db: Session, conv_id: str, user_id: str, category: str,
                        urgent: bool = False):
    """Завести обращение. Пустая тема — законное состояние «ещё не выбрана».

    ⚠️ Неизвестный код превращается в ПУСТУЮ тему, а не в «Другое»: «Другое» срочное по
    построению, и подстановка его вместо мусора от клиента подняла бы чужое обращение в
    самый верх очереди, ничего при этом не объяснив модератору."""
    code = category if category in _CATEGORY_CODES else ""
    now = _now()
    row = SupportTicket(conversation_id=conv_id, user_id=user_id, category=code,
                        urgent=bool(urgent or code in URGENT_CATEGORIES),
                        status="open", created_at=now, last_user_at=now)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def on_moderation_message(db: Session, conv, user, body: str) -> None:
    """Хук отправки сообщения В БЕСЕДУ ОБРАЩЕНИЙ. Зовётся ПОСЛЕ commit и обёрнут в
    try/except у вызывающего: сбой очереди не имеет права уронить уже отправленное
    сообщение (то же правило, что у системных каналов оценок и расписания).

    Что делает:
      - просьбу позвать человека превращает в СРОЧНЫЙ тикет сразу, без вопросов;
      - ЛЮБОЕ первое сообщение заводит тикет и один раз показывает список тем;
      - если тикет уже есть, двигает метку «человек написал».

    🔥 ТИКЕТ ЗАВОДИТСЯ ДАЖЕ БЕЗ ВЫБРАННОЙ ТЕМЫ, и это не мелочь. Пока он заводился только
    по нажатию кнопки, человек, написавший «у меня проблема» и не выбравший ничего,
    ПРОПАДАЛ из очереди целиком: сообщение отправлено, ошибок нет, тикета нет — ровно тот
    тихий отказ, против которого вся эта очередь и заведена. Тема — состояние обращения,
    а не условие его существования.

    ⚠️ Дверь наружу работает и ВНУТРИ открытого обращения: анкету прошли, а разговор зашёл
    в тупик — это и есть случай, ради которого дверь заведена. Иначе выбравший тему
    оказывался бы заперт в ней до конца переписки.
    """
    if getattr(conv, "kind", "") != "moderation":
        return
    wants_human = looks_like_human_request(body)
    existing = _ticket_open(db, conv.id)
    if existing is not None:
        existing.last_user_at = _now()
        escalated = wants_human and not existing.urgent
        if escalated:
            existing.urgent = True
            if not existing.category:
                existing.category = "human"
        db.commit()
        if escalated:
            _post_system(db, conv.id, _HUMAN_ACK)
        return
    if wants_human:
        open_support_ticket(db, conv.id, user.id, "human", urgent=True)
        _post_system(db, conv.id, _HUMAN_ACK)
        return
    # Автоответчик показываем ОДИН раз на обращение — повтор на каждое сообщение
    # превращает чат в переписку с роботом, из которой человек уходит, не дождавшись.
    # Признак «уже здоровались» — САМ ТИКЕТ, а не разбор прошлых сообщений: удалённая или
    # пересланная реплика сдвинула бы такой разбор, и робот поздоровался бы заново.
    open_support_ticket(db, conv.id, user.id, "")
    _post_system(db, conv.id, _autoresponder_text())


@router.get("/moderation/categories")
def moderation_categories(user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """Список тем обращения — ОДИН источник правды для кнопок клиента.

    ⚠️ Вместе со списком отдаётся СОСТОЯНИЕ моего обращения (`current`). Без него клиент
    не знает, выбрана ли тема, и либо показывает кнопки тому, кто уже всё выбрал, либо
    прячет их у того, кто ещё нет, — а решать это по собственной памяти вкладки нельзя:
    человек заходит с телефона и с компьютера, и обращение у него одно.
    """
    row = _ticket_open(db, "mod:" + user.id)
    current = None
    if row is not None:
        current = {"id": row.id, "category": row.category or "",
                   "category_label": _category_label(row.category),
                   "urgent": bool(row.urgent), "status": row.status,
                   "claimed": bool(row.claimed_by)}
    return {"categories": SUPPORT_CATEGORIES, "urgent": sorted(URGENT_CATEGORIES),
            "current": current}


@router.post("/moderation/category")
def moderation_pick_category(payload: dict = Body(...),
                             user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)):
    """Человек выбрал тему обращения — заводим тикет.

    ⚠️ Код темы проверяется по ЗАКРЫТОМУ списку: иначе в очередь приезжает произвольная
    строка от клиента, и фильтр по теме перестаёт что-либо значить."""
    if user.role == "admin":
        raise HTTPException(status_code=403,
                            detail="Администратор отвечает на обращения, а не пишет в них")
    code = (payload.get("code") or "").strip()
    if code not in _CATEGORY_CODES:
        raise HTTPException(status_code=400, detail="Неизвестная тема обращения")
    conv_id = "mod:" + user.id
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if conv is None:
        raise HTTPException(status_code=404, detail="Чат с модерацией ещё не открыт")
    existing = _ticket_open(db, conv_id)
    if existing is not None:
        # Тему у уже открытого тикета меняем, а второй не заводим: у человека одно
        # обращение, и два тикета на него разошлись бы по разным модераторам.
        existing.category = code
        if code in URGENT_CATEGORIES:
            existing.urgent = True
        db.commit()
        row = existing
    else:
        row = open_support_ticket(db, conv_id, user.id, code)
    label = _category_label(code)
    tail = ("Обращение помечено срочным, модератор подключится к чату." if row.urgent
            else "Опишите, что случилось, — модератор ответит в этом чате.")
    _post_system(db, conv_id, "Тема обращения: «%s». %s" % (label, tail))
    return {"ok": True, "ticket_id": row.id, "urgent": bool(row.urgent),
            "category": row.category or "", "category_label": label}


# ── ОЧЕРЕДЬ ОБРАЩЕНИЙ ДЛЯ МОДЕРАЦИИ ──────────────────────────────────────────────────
def _autoclose_stale(db: Session) -> int:
    """«Умное» закрытие: взятый в работу тикет, где модератор ответил, а человек молчит
    дольше срока, закрывается сам.

    🔑 ПОЧЕМУ ЭТО НУЖНО. Тикет, который закрывает только человек, не закрывает НИКТО:
    разобранное обращение висит в очереди неделями, очередь растёт, и модератор перестаёт
    её просматривать. Та же болезнь, что у сигнала, который всегда красный, и ровно та
    причина, по которой у мьюта появился срок.

    ⚠️ Закрываем ТОЛЬКО те, где последним говорила модерация. Если последним написал
    человек — молчит как раз модерация, и автозакрытие спрятало бы её же долг.
    🔥 СРОК ОТСЧИТЫВАЕТСЯ ОТ ПОСЛЕДНЕГО ОТВЕТА МОДЕРАЦИИ, А НЕ ОТ ВЗЯТИЯ В РАБОТУ, и это
    куплено дефектом в обе стороны (нашёл Полковник). От взятия — значит тикет, взятый
    три недели назад, закрывался бы на первом же чтении очереди СРАЗУ после ответа
    модератора: человек получал бы «обращение закрыто» через минуту после ответа, не
    успев его прочитать. А парная проверка «человек писал позже взятия» гасила
    закрытие навсегда, потому что `claim` сам пишет в чат приветствие и человек
    отвечает на него — то есть механизм не срабатывал ровно там, ради чего написан.
    ⚠️ Проверяется ПРИ ЧТЕНИИ очереди, планировщика нет: на одноядерном бою лишний поток
    дороже пользы (тот же приём, что у истёкшего мьюта и у напоминаний). Отсюда и предел
    просмотра: взятых в работу тикетов немного, но чтение очереди — не место для полного
    обхода таблицы.
    ⚠️ Закрытие НЕ запирает человека: следующее его сообщение заводит новый тикет, и
    переписка остаётся на месте.
    """
    from datetime import datetime, timedelta, timezone
    edge = (datetime.now(timezone.utc) - timedelta(days=SUPPORT_AUTOCLOSE_DAYS)).isoformat()
    rows = (db.query(SupportTicket)
            .filter(SupportTicket.status == "in_review",
                    SupportTicket.claimed_at != "")
            .limit(_AUTOCLOSE_SCAN_LIMIT).all())
    closed = 0
    for r in rows:
        # Последнее слово за модерацией? Сравниваем метку человека с ПОСЛЕДНИМ ОТВЕТОМ,
        # и только с ним. Сравнение с моментом ВЗЯТИЯ здесь стояло и было дефектом
        # (нашёл Полковник): `claimed_at` после взятия не двигается, а `claim` сам пишет
        # в чат приветствие — значит человек почти всегда отвечает ПОЗЖЕ взятия, условие
        # становится истинным навсегда, и разобранный тикет висит вечно. То есть
        # автозакрытие не срабатывало ровно в том случае, ради которого написано.
        last_reply = (db.query(Message)
                      .filter(Message.conversation_id == r.conversation_id,
                              Message.sender_id != r.user_id,
                              Message.sender_id != SYSTEM_SENDER_ID,
                              Message.deleted_at == "")
                      .order_by(Message.id.desc()).first())
        if last_reply is None:
            continue          # модерация ещё не ответила — это ЕЁ долг, а не молчание человека
        said = last_reply.created_at or ""
        if (r.last_user_at or "") > said:
            continue          # человек написал после ответа и ждёт — очередь его не теряет
        if said >= edge:
            continue          # ответ свежий: молчание ещё ничего не значит
        r.status = "resolved"
        r.resolved_at = _now()
        r.resolved_by = "auto"
        r.resolution_note = "закрыт автоматически: %d суток без ответа" % SUPPORT_AUTOCLOSE_DAYS
        closed += 1
    if closed:
        db.commit()
    return closed


def _ticket_out(db: Session, r) -> dict:
    u = db.query(User).filter(User.id == r.user_id).first()
    label = _category_label(r.category)
    who = ""
    if r.claimed_by:
        m = db.query(User).filter(User.id == r.claimed_by).first()
        who = moderator_display_name(m) if m else r.claimed_by
    return {
        "id": r.id,
        "conversation_id": r.conversation_id,
        "user_id": r.user_id,
        "user_name": (u.full_name or u.name or u.login) if u else r.user_id,
        "category": r.category,
        "category_label": label,
        "urgent": bool(r.urgent),
        "status": r.status,
        "created_at": r.created_at or "",
        "claimed_by": who,
        "claimed_at": r.claimed_at or "",
        "resolved_at": r.resolved_at or "",
        "resolution_note": r.resolution_note or "",
        "last_user_at": r.last_user_at or "",
    }


@mod_router.get("/support")
def mod_support_queue(status: str = Query("open"), admin: User = Depends(require_moderation),
                      db: Session = Depends(get_db)):
    """Очередь обращений. СРОЧНЫЕ — В САМОМ ВЕРХУ.

    ⚠️ Порядок задаётся ЗДЕСЬ, на сервере, а не сортировкой на клиенте: очередь читают
    три разных экрана, и «срочное наверху» обязано означать одно и то же на всех.
    ⚠️ status='' — все; по умолчанию открытые и взятые в работу, то есть то, что требует
    внимания прямо сейчас.
    """
    _autoclose_stale(db)
    q = db.query(SupportTicket)
    if status == "open":
        q = q.filter(SupportTicket.status.in_(("open", "in_review")))
    elif status:
        q = q.filter(SupportTicket.status == status)
    rows = q.limit(500).all()
    out = [_ticket_out(db, r) for r in rows]
    # Срочные выше всех, дальше — кто дольше ждёт. `created_at` по возрастанию: обращение
    # недельной давности обязано быть выше сегодняшнего, иначе очередь обслуживает
    # последних пришедших, а первые не дожидаются вовсе.
    out.sort(key=lambda x: (0 if x["urgent"] else 1, x["created_at"] or ""))
    return {"tickets": out, "categories": SUPPORT_CATEGORIES}


@mod_router.post("/support/{tid}/claim")
def mod_support_claim(tid: int, request: Request = None,
                      admin: User = Depends(require_moderation), db: Session = Depends(get_db)):
    """Модератор берёт обращение и ПРЕДСТАВЛЯЕТСЯ в чате своим номером.

    🔑 Приветствие пишется автоматически и от лица модератора, потому что до этого
    момента человек разговаривал с роботом. Реплика «здравствуйте, я модератор №7» — это
    момент, когда обращение перестаёт быть анкетой и становится разговором; заставлять
    модератора набирать её руками значит получить чат, где он молча начинает с вопроса.

    ⚠️ Повторный вызов НЕ здоровается второй раз и не отбирает тикет у того, кто уже его
    взял: иначе два модератора по очереди представлялись бы одному человеку.
    """
    r = db.get(SupportTicket, tid)
    if r is None:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    if r.status == "resolved":
        raise HTTPException(status_code=409, detail="Обращение уже закрыто")
    if r.claimed_by and r.claimed_by != admin.id:
        other = db.query(User).filter(User.id == r.claimed_by).first()
        raise HTTPException(status_code=409, detail="Обращение уже ведёт %s"
                            % (moderator_display_name(other) if other else "другой модератор"))
    first_time = not r.claimed_by
    r.claimed_by = admin.id
    r.claimed_at = r.claimed_at or _now()
    r.status = "in_review"
    db.commit()
    if first_time:
        m = Message(conversation_id=r.conversation_id, sender_id=admin.id,
                    body="Здравствуйте, я %s. Чем могу помочь?" % moderator_display_name(admin),
                    created_at=_now())
        db.add(m)
        db.commit()
        _broadcast(db, r.conversation_id)
    audit.log(db, request, actor=admin.login, role=admin.role,
              action="support.claim", target=str(tid))
    return {"ok": True, "ticket": _ticket_out(db, r)}


@mod_router.post("/support/{tid}/resolve")
def mod_support_resolve(tid: int, payload: dict = Body(default={}), request: Request = None,
                        admin: User = Depends(require_moderation), db: Session = Depends(get_db)):
    """Закрыть обращение. Человеку в чат уходит понятная строка о том, что тикет закрыт.

    ⚠️ Молчаливое закрытие — худший вариант: человек продолжает ждать ответа в чате,
    который для модерации уже не существует. Это тот же урок, что «отказ заявки
    ОБЪЯВЛЯЕТСЯ в беседе».
    ⚠️ Переписку не трогаем и чат не закрываем: следующее сообщение заведёт новый тикет.
    """
    r = db.get(SupportTicket, tid)
    if r is None:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    if r.status == "resolved":
        return {"ok": True, "ticket": _ticket_out(db, r)}
    note = (payload.get("note") or "").strip()
    r.status = "resolved"
    r.resolved_at = _now()
    r.resolved_by = admin.id
    r.resolution_note = note[:500]
    db.commit()
    tail = (" Причина: %s" % note) if note else ""
    _post_system(db, r.conversation_id,
                 "Обращение закрыто модерацией.%s Если вопрос остался — напишите здесь, "
                 "и мы откроем новое." % tail)
    audit.log(db, request, actor=admin.login, role=admin.role,
              action="support.resolve", target=str(tid), detail=note)
    return {"ok": True, "ticket": _ticket_out(db, r)}


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
            .filter(Message.conversation_id == conv.id, Message.deleted_at == "")
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
                              Message.deleted_at == "")
                      .order_by(Message.id.desc()).first())
        q = db.query(Message).filter(Message.conversation_id == conv.id,
                                     Message.sender_id == owner,
                                     Message.deleted_at == "")
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
