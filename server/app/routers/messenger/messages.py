"""
messages.py — Сами сообщения: чтение ленты, ветка ответов, поиск (обычный и по смыслу),
сводка, отправка, отметка прочтения, правка, реакции, удаление, закреп, пересылка,
жалоба.

Часть пакета `routers/messenger` (разрез 3.7.7). Общий роутер, проверки прав и
сборка ответов — в `_common.py`; порядок регистрации маршрутов задаёт `__init__.py`.
"""
from ._common import *      # noqa: F401,F403 — роутеры, модели, хелперы


def _hook_moderation(db: Session, conv_or_id, user: User, body: str) -> None:
    """Очередь обращений: автоответчик с темами и заведение тикета.

    🔑 ОДНА ДВЕРЬ НА ВСЕ ТОЧКИ, КОТОРЫЕ КЛАДУТ СООБЩЕНИЕ ЧЕЛОВЕКА В ЧАТ МОДЕРАЦИИ.
    Хук стоял прямо в `send_message`, и пересылка (`forward_messages`) проходила мимо:
    студент пересылал в ⚙ оскорбление и не писал ни слова — сообщение в чате есть, тикета
    НЕТ, очередь про обращение не знает. Это тот же класс, что `_guard_direct_write`:
    правило живёт в одной функции, и новая точка обязана позвать ЕЁ, а не повторить
    условие рядом.

    ⚠️ Best-effort ПОСЛЕ commit: сбой очереди модерации не имеет права уронить уже
    отправленное человеком сообщение (то же правило, что у системных каналов оценок и
    расписания).
    ⚠️ **`rollback` в `except` обязателен.** Внутри хука есть свои `commit`, и упавший
    коммит оставляет сессию в состоянии «нужен откат» — следующее же обращение к объекту
    сообщения в этой же ручке упало бы `PendingRollbackError`, то есть человек получил бы
    500 на сообщение, которое НА САМОМ ДЕЛЕ сохранено и разослано. Тихо проглотить
    исключение мало: сессию надо ещё и вернуть в рабочее состояние.

    ⚠️ Принимает и УЖЕ ЗАГРУЖЕННУЮ беседу, и её id. Отправка сообщения — самый горячий
    путь продукта, и лишний SELECT на каждое сообщение ради проверки `kind` здесь не
    нужен: вызывающий беседу уже держит.
    """
    conv = conv_or_id if not isinstance(conv_or_id, str) else _conversation(db, conv_or_id)
    if conv is None or getattr(conv, "kind", "") != "moderation":
        return
    try:
        from .moderation import on_moderation_message
        on_moderation_message(db, conv, user, body)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


# ── История и новые сообщения ────────────────────────────────────────────────────────
@router.get("/chats/{conv_id}/messages")
def messages(conv_id: str, before: int = Query(0), after: int = Query(0),
             limit: int = Query(_DEFAULT_PAGE),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Сообщения беседы. `before=<id>` — история вверх (старее указанного), `after=<id>` —
    новые (для опроса). Без параметров — последние `limit`. Скрытые «у себя» не отдаём;
    удалённые у всех — тумбстоуном. Всегда в хронологическом порядке (старые→новые)."""
    part = _require_participant(db, conv_id, user)
    limit = max(1, min(int(limit or _DEFAULT_PAGE), _MAX_PAGE))
    hidden = _hidden_ids(db, conv_id, user.id)

    q = db.query(Message).filter(Message.conversation_id == conv_id)
    if hidden:
        q = q.filter(~Message.id.in_(hidden))
    #«Удалённая у себя» переписка: всё, что было до очистки, пользователю не показываем.
    if part.cleared_upto_id:
        q = q.filter(Message.id > part.cleared_upto_id)
    if part.cleared_at:                      #legacy-строки (очищены до id-границы)
        q = q.filter(Message.created_at > part.cleared_at)

    if after:
        rows = q.filter(Message.id > after).order_by(Message.id.asc()).limit(limit).all()
    elif before:
        rows = (q.filter(Message.id < before)
                .order_by(Message.id.desc()).limit(limit).all())
        rows.reverse()                       #отдаём в хронологии
    else:
        rows = q.order_by(Message.id.desc()).limit(limit).all()
        rows.reverse()
    names = _names_for(db, [m.sender_id for m in rows])
    out = _msgs_out(db, rows, user.id, names)
    _attach_reactions(db, out, user.id)      #§D3: реакции пачкой
    _attach_reply_counts(db, out)            #Треды: бейдж «N ответов»
    _attach_rich_meta(db, out, user.id)               #кнопки: отчёт куратора, активность, доска
    return {"messages": out}


@router.get("/chats/{conv_id}/messages/thread/{message_id}")
def message_thread(conv_id: str, message_id: int,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Треды: ответы на КОНКРЕТНОЕ сообщение этой беседы (см. `_attach_reply_counts`).
    Своей сущности «ветки» не заводим — фильтр по уже существующему `reply_to_id`."""
    _require_participant(db, conv_id, user)
    hidden = _hidden_ids(db, conv_id, user.id)
    q = (db.query(Message)
         .filter(Message.conversation_id == conv_id, Message.reply_to_id == message_id))
    if hidden:
        q = q.filter(~Message.id.in_(hidden))
    rows = q.order_by(Message.id.asc()).all()
    names = _names_for(db, [m.sender_id for m in rows])
    out = _msgs_out(db, rows, user.id, names)
    _attach_reactions(db, out, user.id)
    _attach_rich_meta(db, out, user.id)               #иначе в ветке ответов вместо кнопки сырой id
    return {"messages": out}


@router.get("/chats/{conv_id}/messages/search")
def search_messages(conv_id: str, q: str = Query(""), limit: int = Query(50),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Поиск по тексту ВНУТРИ одной беседы. Регистронезависимое вхождение делаем в Python
    (та же причина, что и в directory(): SQLite без ICU не даёт регистронезависимый LIKE
    для кириллицы, а объём переписки учебного заведения в память умещается)."""
    part = _require_participant(db, conv_id, user)
    needle = (q or "").strip().lower()
    if not needle:
        return {"messages": []}
    hidden = _hidden_ids(db, conv_id, user.id)
    qq = (db.query(Message)
          .filter(Message.conversation_id == conv_id, Message.deleted_at == "",
                  Message.kind == "text"))
    if part.cleared_upto_id:
        qq = qq.filter(Message.id > part.cleared_upto_id)
    if part.cleared_at:                      #legacy-строки (очищены до id-границы)
        qq = qq.filter(Message.created_at > part.cleared_at)
    if hidden:
        qq = qq.filter(~Message.id.in_(hidden))
    rows = qq.order_by(Message.id.desc()).limit(2000).all()   #верхняя граница выборки
    limit = max(1, min(int(limit or 50), 100))
    matched = [m for m in rows if needle in (m.body or "").lower()][:limit]
    names = _names_for(db, [m.sender_id for m in matched])
    out = _msgs_out(db, matched, user.id, names)
    _attach_rich_meta(db, out, user.id)
    return {"messages": out}


@router.get("/chats/{conv_id}/messages/ai-search")
def ai_search_messages(conv_id: str, q: str = Query(""), limit: int = Query(30),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """§17: поиск по СМЫСЛУ внутри беседы.

    Как это устроено и почему именно так: модель получает ТОЛЬКО поисковый запрос и
    придумывает к нему словоформы и синонимы («домашка» → «дз», «задание», «задали»), а
    сопоставление с сообщениями делаем мы сами. Переписка модели не показывается вовсе.

    Настоящий семантический поиск (эмбеддинги + векторный индекс) потребовал бы
    проиндексировать всю переписку и держать индекс в памяти — на боевом одноядерном VPS
    с 960 МБ это не поедет, а отправлять чужие сообщения в облако ради поиска тем более
    нельзя. Расширение запроса даёт основную пользу без обеих этих цен.

    Модель недоступна → `expanded` пуст и результат совпадает с обычным поиском."""
    part = _require_participant(db, conv_id, user)
    needle = (q or "").strip().lower()
    if not needle:
        return {"messages": [], "expanded": []}

    from ...webdata import load_config
    from ... import messenger_ai
    extra = messenger_ai.expand_query(load_config(db), needle)

    rows = (_visible_messages_query(db, conv_id, part, user.id)
            .order_by(Message.id.desc()).limit(2000).all())
    scored = []
    for m in rows:
        score = messenger_ai.match_score(m.body, needle, extra)
        if score:
            scored.append((score, m))
    #Сортировка: сначала релевантность, при равной — свежие выше (id убывает).
    scored.sort(key=lambda p: (p[0], p[1].id), reverse=True)
    limit = max(1, min(int(limit or 30), 100))
    matched = [m for _s, m in scored[:limit]]
    names = _names_for(db, [m.sender_id for m in matched])
    out = _msgs_out(db, matched, user.id, names)
    _attach_rich_meta(db, out, user.id)
    return {"messages": out,
            "expanded": extra}


@router.get("/chats/{conv_id}/summary")
def chat_summary(conv_id: str, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """§18: краткая сводка переписки.

    Запускается ТОЛЬКО кнопкой. Автоматическая сводка при открытии чата означала бы запрос
    к модели на каждый вход в диалог — на одноядерном VPS это заметная нагрузка и лишние
    деньги за токены ради текста, который чаще всего никто не прочтёт.

    ФИО реальных людей маскируются ДО отправки (messenger_ai.mask_names): сообщения здесь
    модели действительно нужны, но персональные данные в облако уезжать не должны.

    Результат кэшируется по (беседа, id последнего сообщения) — пока в чат никто не
    написал, повторное нажатие отдаёт готовое и ничего не стоит."""
    part = _require_participant(db, conv_id, user)
    rows = (_visible_messages_query(db, conv_id, part, user.id)
            .order_by(Message.id.asc()).all())
    if len(rows) < 3:
        return {"summary": "", "reason": "too_short", "messages": len(rows)}

    #В ключ входит и МЕТКА последнего сообщения, не только его id. Причина не
    #теоретическая: id — автоинкремент внутри базы, и после восстановления сервера из
    #бэкапа они начинают выдаваться заново. Кэш в памяти процесса это переживёт и отдал бы
    #сводку ЧУЖОГО (уже несуществующего) разговора. Метка времени такой коллизии не даёт.
    cache_key = (conv_id, rows[-1].id, rows[-1].created_at)
    cached = _SUMMARY_CACHE.get(cache_key)
    if cached is not None:
        return {"summary": cached, "cached": True, "messages": len(rows)}

    from ...webdata import load_config
    from ... import messenger_ai
    names = _names_for(db, [m.sender_id for m in rows])
    transcript = messenger_ai.build_transcript(db, rows, names)
    summary = messenger_ai.summarize(load_config(db), transcript)
    if not summary:
        #Честно говорим, что не получилось, вместо выдуманного текста.
        return {"summary": "", "reason": "no_model", "messages": len(rows)}
    #Кэш маленький и в памяти: переживать перезапуск ему незачем, а место на VPS дорого.
    if len(_SUMMARY_CACHE) > 200:
        _SUMMARY_CACHE.clear()
    _SUMMARY_CACHE[cache_key] = summary
    return {"summary": summary, "cached": False, "messages": len(rows)}


@router.get("/messages/{mid}/read_by")
def message_read_by(mid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Кто прочитал сообщение — переиспользует УЖЕ существующий per-участнику
    `last_read_at` (без новой таблицы «строка на каждое прочтение каждым»): читатель —
    участник, чья метка прочтения НЕ РАНЬШЕ момента отправки этого сообщения."""
    m = db.query(Message).filter(Message.id == mid).first()
    if m is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    _require_participant(db, m.conversation_id, user)
    rows = (db.query(ConversationParticipant)
            .filter(ConversationParticipant.conversation_id == m.conversation_id,
                    ConversationParticipant.user_id != m.sender_id,
                    ConversationParticipant.last_read_at >= m.created_at).all())
    ids = [r.user_id for r in rows]
    if not ids:
        return {"users": []}
    #⚠️ (живой отзыв Влада, панель «Реакции» в контекстном меню) Точки «когда именно
    #прочитали ИМЕННО ЭТО сообщение» у нас нет и заводить её не будем (см. докстринг
    #выше — сознательно без новой таблицы «строка на каждое прочтение каждым»). Честная
    #замена — last_read_at участника: раз он ≥ момента отправки, значит человек дошёл
    #этим чтением как минимум досюда, и last_read_at — самая точная метка, какая у нас
    #есть. Как в Telegram — не «увидел именно эту секунду», а «где он сейчас в ленте».
    read_at_by_id = {r.user_id: (r.last_read_at or "") for r in rows}
    onl = _online_logins()
    users = db.query(User).filter(User.id.in_(ids)).all()
    return {"users": [dict(_safe_user(u, onl), last_read_at=read_at_by_id.get(u.id, ""))
                      for u in users]}


#Сколько символов выделения храним у цитаты. Цитата — указатель на кусок разговора, а не
#его копия: длинную клиент всё равно обрезает многоточием, а в базе она лежала бы вторым
#экземпляром чужого сообщения.
_MAX_QUOTE_CHARS = 300


def _clean_quote(raw, original: Message) -> str:
    """Проверить и подрезать выделенный фрагмент цитаты. Пусто — обычный ответ.

    🔒 ГЛАВНОЕ ЗДЕСЬ — ПРОВЕРКА ПРИНАДЛЕЖНОСТИ. Фрагмент обязан реально встречаться в
    теле оригинала. Без неё в цитату вписывались бы слова, которых собеседник не писал, и
    выглядели бы они как его собственные: подделка, заметная только тому, кто пойдёт
    сверять исходное сообщение — а к нему-то цитата и ведёт, то есть проверять её пошли
    бы по ссылке, которую поставил подделыватель.

    ⚠️ Сравниваем по НОРМАЛИЗОВАННЫМ пробелам: выделение мышью в браузере переносы строк
    внутри абзаца отдаёт пробелами, и точное вхождение не совпало бы у совершенно
    честного выделения через две строки.

    ⚠️ Цитата у СИСТЕМНОГО сообщения не имеет смысла (его тело — шаблон «событиеаргу-
    менты», человек такого не писал), поэтому там её просто нет.
    """
    text = (raw or "")
    if not isinstance(text, str) or not text.strip():
        return ""
    #🔥 ОРИГИНАЛ, УЖЕ УДАЛЁННЫЙ «У ВСЕХ», ЦИТИРОВАТЬ НЕЛЬЗЯ (нашёл Полковник 06.09.2026).
    #Тумбстоун тело НЕ стирает — оно остаётся в строке ради модерации, — поэтому проверка
    #принадлежности фрагмента проходила успешно, и цитата удалённого сообщения ложилась в
    #базу. Гашение при выдаче (`_blank_quotes_of_deleted`) её потом убирало из ленты, но
    #строка уже существовала и уезжала в пути, идущие мимо: превью последнего сообщения в
    #списке чатов и очередь модерации. Правило прежнее: не пускать, а не подчищать.
    if getattr(original, "deleted_at", ""):
        return ""
    if (getattr(original, "kind", "") or "text") == "system":
        return ""
    text = text.strip()[:_MAX_QUOTE_CHARS]
    def _flat(v: str) -> str:
        return " ".join((v or "").split())
    if _flat(text) not in _flat(original.body or ""):
        return ""
    return text


# ── Отправка ─────────────────────────────────────────────────────────────────────────
@router.post("/chats/{conv_id}/messages")
def send_message(conv_id: str, payload: dict = Body(...),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Отправить сообщение. Сервер ставит created_at (UTC). reply_to_id — необязательный
    ответ на сообщение этой же беседы. В канал пишут только авторы (writer/admin/owner)."""
    part = _require_participant(db, conv_id, user)
    conv = _conversation(db, conv_id)
    if conv.kind == "channel" and part.role not in _WRITER_ROLES:
        raise HTTPException(status_code=403, detail="В канал могут писать только авторы")
    if part.silenced:                        #«/mute»-заглушка модератора — не глобальный мьют
        raise HTTPException(status_code=403, detail="Вы заглушены в этой беседе")
    #Блокировка проверяется и ЗДЕСЬ, а не только при открытии лички: беседа могла быть
    #создана ДО блокировки и остаётся открытой у обоих. Проверка на входе в чат защитила
    #бы только новые пары — то есть не защитила бы никого из тех, кто уже переписывался.
    _guard_direct_write(db, conv, user)
    #`conv` передаётся НЕ для удобства: по виду беседы барьер решает, идёт ли речь
    #о чате с модерацией — единственном, который остаётся открытым под мьютом.
    _guard_can_write(db, user, conv)          #глобальный мьют (403) + анти-флуд (429)
    body = (payload.get("body") or "").strip()
    #⚠️ ФАЙЛ САМ ПО СЕБЕ — ЗАКОННОЕ СООБЩЕНИЕ. Гейт пустого текста стоял РАНЬШЕ разбора
    #вложения, поэтому «выбрал файл, ничего не написал, отправил» отвечало 400 — но уже
    #ПОСЛЕ того, как файл лёг в хранилище: объект оставался сиротой и оплаченным
    #трафиком, а человек видел ошибку на ровном месте. Найдено Полковником.
    if not body and not str(payload.get("attachment_id") or ""):
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    #GIF-пикер (Klipy, §messenger_gifs): тело — прямая ссылка на CDN, а не текст, набранный
    #человеком. Слэш-команды/упоминания/цензура ниже пропускаются целиком для этого вида —
    #прогонять URL через censor() рискованно: случайное совпадение с корнем матерного слова
    #испортило бы саму ссылку (censor меняет символы НА МЕСТЕ, длина та же).
    is_gif = (payload.get("kind") or "").strip() == "gif"
    if is_gif:
        from ... import gif_service
        if not gif_service.is_allowed_url(body):
            raise HTTPException(status_code=400, detail="Недопустимая ссылка на GIF")
    elif len(body) > _MAX_MSG_CHARS:
        body = body[:_MAX_MSG_CHARS]
    reply_to = int(payload.get("reply_to_id") or 0)
    reply_quote = ""
    if reply_to:
        ok = (db.query(Message)
              .filter(Message.id == reply_to, Message.conversation_id == conv_id).first())
        if ok is None:
            reply_to = 0                     #ответ на чужое/несуществующее — игнорируем связь
        else:
            reply_quote = _clean_quote(payload.get("reply_quote"), ok)

    #§D10: идемпотентность. Повторный POST с тем же client_nonce (ретрай при обрыве сети)
    #возвращает уже созданное сообщение, а не плодит дубль.
    nonce = str(payload.get("client_nonce") or "").strip()[:64]
    if nonce:
        #⚠️ И ПО ОТПРАВИТЕЛЮ ТОЖЕ. Метка уникальна только в пределах ОДНОГО клиента: она
        #его собственная, а не общая на беседу. Без sender_id участник, взявший чужую
        #метку из ленты, получал бы в ответ ЧУЖОЕ сообщение — и его собственный черновик
        #подменялся бы чужим текстом. Прав это не даёт и ПДн не раскрывает (вредит только
        #себе), но поведение бессмысленное, и стоило оно одной строки.
        dup = (db.query(Message)
               .filter(Message.conversation_id == conv_id,
                       Message.sender_id == user.id,
                       Message.client_nonce == nonce).first())
        if dup is not None:
            out = _msg_out(dup, user.id, user.full_name or user.name or user.login or "")
            _attach_rich_meta(db, [out], user.id)
            return out

    mentions = []
    if not is_gif:
        #§12: «/отчет [группа]» — команда, а не сообщение: разбираем ДО сохранения и отдаём
        #созданную кнопку отчёта. Иначе текст команды оставался бы в ленте мусором (особенно
        #заметным в чате родителей), а ошибка («не та группа», «нет прав») тонула молча.
        #⚠️ ПОСЛЕ проверки nonce: отчёт — это запись в БД, и повтор запроса (ретрай сети,
        #двойное нажатие) не должен плодить второй такой же. Тот же nonce уезжает и в
        #сообщение-кнопку, поэтому ретрай находит её выше и возвращает как есть.
        rep_msg = _handle_report_command(db, conv_id, body, user, nonce)
        if rep_msg is not None:
            out = _msg_out(rep_msg, user.id, user.full_name or user.name or user.login or "")
            _attach_rich_meta(db, [out], user.id)     #иначе клиент получит сырой id вместо кнопки
            return out

        #/mute, /clear — тоже команды, не сообщения: разбираем ДО сохранения текста (как
        #/отчет выше), иначе литеральная строка команды осела бы в ленте мусором.
        mute_msg = _handle_mute_command(db, conv_id, body, user, part)
        if mute_msg is not None:
            return _msg_out(mute_msg, user.id, "")
        clear_msg = _handle_clear_command(db, conv_id, body, user, part)
        if clear_msg is not None:
            return _msg_out(clear_msg, user.id, "")

        #§D8: упоминания — среди участников ЭТОЙ беседы (иначе @Фамилия постороннего человека
        #молча ни на что бы не сработала, но и не должна давать доступ к чужим данным).
        mentions = _parse_mentions(db, body, _participant_ids(db, conv_id))

        #Цензура мата — маскируем звёздочками (НЕ блокируем отправку, как автомодерация
        #Twitch/Discord). После всех slash-команд (иначе испортили бы их разбор) и ПОСЛЕ
        #упоминаний (плейсхолдер @Фамилия матом не считается), но ДО сохранения — что легло
        #в БД, то и видят все читатели постфактум. `body` переиспользуется ниже в
        #_handle_vector_command — так что и вопрос Вектору уходит уже очищенным.
        import profanity_filter
        body = profanity_filter.censor(body, mask=profanity_filter.MESSENGER_SAFE_MASK)

    #Вложение: пришло `attachment_id` — проверяем, что оно НАШЕ, ГОТОВО и из ЭТОЙ беседы.
    #⚠️ Все три проверки обязательны. Без первой чужим вложением можно подписаться, без
    #второй в ленту попадёт файл, которого в хранилище нет, без третьей — файл «переедет»
    #в беседу, участником которой отправитель может и не быть.
    att_id = str(payload.get("attachment_id") or "")
    kind = "gif" if is_gif else "text"
    if att_id:
        att = db.query(Attachment).filter(Attachment.id == att_id).first()
        if (att is None or not att.ready
                or att.uploader_id != user.id or att.conversation_id != conv_id):
            raise HTTPException(status_code=400, detail="Вложение недоступно")
        kind = "file"
        att.orphan_at = ""          #ссылка появилась — сиротой больше не считается

    m = Message(conversation_id=conv_id, sender_id=user.id, body=body,
                created_at=_now(), reply_to_id=reply_to, reply_quote=reply_quote,
                mentions=mentions,
                kind=kind, attachment_id=att_id,
                body_format="plain" if is_gif else "markdown", client_nonce=nonce)
    db.add(m)
    db.commit()
    db.refresh(m)
    #Отправитель прочитал свою же беседу вплоть до этого сообщения.
    _mark_read(db, conv_id, user.id, m.created_at)
    _unhide_participants(db, conv_id)        #«удалённый» чат возвращается с новым сообщением
    _broadcast(db, conv_id)                  #живой сигнал участникам (WS)
    silent_ids = {mm["user_id"] for mm in mentions if mm.get("silent")}
    _notify_recipients(db, conv, user, skip_ids=silent_ids)   #пуш офлайн — минус тихие упоминания
    #Громкая отметка (`/@!Фамилия`) — звук у получателя (его включает клиент по флагу
    #`loud` в mentions) + письмо в «Систему». Ставим ПОСЛЕ обычной рассылки: обычный пуш
    #о сообщении и «вас отметили» — разные события, и второе не должно съесть первое.
    _notify_loud_mentions(db, conv, user, mentions)
    if is_gif:
        #Best-effort, ПОСЛЕ commit — статистика показов Klipy не имеет права мешать уже
        #созданному сообщению, даже если их API прямо сейчас недоступен.
        from ... import gif_service
        gif_service.mark_shared((payload.get("gif_slug") or "").strip())
    else:
        _handle_vector_command(db, conv_id, body, user, reply_to=reply_to)
    #Очередь обращений: автоответчик с темами и заведение тикета.
    _hook_moderation(db, conv, user, body)
    #Вложение отдаём сразу: клиент рисует сообщение до ответа сервера, и без
    #метаданных карточка файла мигнула бы пустой.
    return _msg_out(m, user.id, user.full_name or user.name or user.login or "",
                    _att_map(db, [m]).get(att_id or ""))


@router.post("/chats/{conv_id}/read")
def mark_read(conv_id: str, payload: dict = Body(default={}),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Отметить беседу прочитанной до указанного сообщения (или до последнего)."""
    _require_participant(db, conv_id, user)
    upto = ""
    mid = int(payload.get("last_message_id") or 0)
    if mid:
        m = db.query(Message).filter(Message.id == mid,
                                     Message.conversation_id == conv_id).first()
        upto = m.created_at if m else ""
    if not upto:
        last = (db.query(Message).filter(Message.conversation_id == conv_id)
                .order_by(Message.id.desc()).first())
        upto = last.created_at if last else _now()
    _mark_read(db, conv_id, user.id, upto)
    return {"ok": True, "last_read_at": upto}


@router.patch("/messages/{mid}")
def edit_message(mid: int, payload: dict = Body(...),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Редактировать СВОЁ сообщение (ставит edited_at)."""
    m = _message_in_conv(db, mid)
    _require_participant(db, m.conversation_id, user)
    if m.sender_id != user.id:
        raise HTTPException(status_code=403, detail="Можно править только свои сообщения")
    if m.deleted_at:
        raise HTTPException(status_code=400, detail="Сообщение удалено")
    body = (payload.get("body") or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    #Цензура — ДО обрезки по длине (не после): если резать сначала, граница могла бы
    #попасть внутрь найденного слова и оставить необработанный обрубок незацензуренным
    #(см. тот же порядок в send_message).
    import profanity_filter
    body = profanity_filter.censor(body, mask=profanity_filter.MESSENGER_SAFE_MASK)
    #§D11: сохраняем текст ДО правки — модерация должна видеть оригинал (автор мог исправить
    #сообщение после жалобы). Пустых снимков не плодим.
    if (m.body or "") and m.body != body[:_MAX_MSG_CHARS]:
        db.add(MessageEdit(message_id=m.id, body_before=m.body, edited_at=_now()))
    m.body = body[:_MAX_MSG_CHARS]
    m.edited_at = _now()
    db.commit()
    db.refresh(m)
    _broadcast(db, m.conversation_id)
    return _msg_out(m, user.id)


@router.get("/messages/{mid}/history")
def message_history(mid: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """§D11: история редактирования сообщения (участникам беседы). Показывает версии ДО
    правок + текущий текст — чтобы было видно, что менялось."""
    m = _message_in_conv(db, mid)
    _require_participant(db, m.conversation_id, user)
    edits = (db.query(MessageEdit).filter(MessageEdit.message_id == mid)
             .order_by(MessageEdit.id.asc()).all())
    versions = [{"body": e.body_before, "at": e.edited_at} for e in edits]
    versions.append({"body": m.body or "", "at": m.edited_at or m.created_at, "current": True})
    return {"versions": versions}


# ── Реакции (§D3) ────────────────────────────────────────────────────────────────────
@router.post("/messages/{mid}/reactions")
def add_reaction(mid: int, payload: dict = Body(...),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Поставить реакцию-эмодзи (из белого списка). Идемпотентно: та же реакция от того же
    пользователя не дублируется."""
    emoji = str(payload.get("emoji") or "")
    if emoji not in _REACTIONS:
        raise HTTPException(status_code=400, detail="Недопустимая реакция")
    m = _message_in_conv(db, mid)
    _require_participant(db, m.conversation_id, user)
    #Реакция — не сообщение, но у собеседника она появляется под его же строкой, то есть
    #это способ дотянуться до человека, который этого не хочет. Блокировка обязана её
    #закрывать, иначе она дырява ровно в ту сторону, ради которой её ставят.
    _guard_direct_write(db, _conversation(db, m.conversation_id), user)
    if m.deleted_at:
        raise HTTPException(status_code=400, detail="Сообщение удалено")
    exists = (db.query(MessageReaction)
              .filter(MessageReaction.message_id == mid, MessageReaction.user_id == user.id,
                      MessageReaction.emoji == emoji).first())
    if exists is None:
        db.add(MessageReaction(message_id=mid, user_id=user.id, emoji=emoji, created_at=_now()))
        db.commit()
        _broadcast(db, m.conversation_id)
    return {"ok": True}


@router.delete("/messages/{mid}/reactions/{emoji}")
def remove_reaction(mid: int, emoji: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Снять СВОЮ реакцию."""
    m = _message_in_conv(db, mid)
    _require_participant(db, m.conversation_id, user)
    row = (db.query(MessageReaction)
           .filter(MessageReaction.message_id == mid, MessageReaction.user_id == user.id,
                   MessageReaction.emoji == emoji).first())
    if row is not None:
        db.delete(row)
        db.commit()
        _broadcast(db, m.conversation_id)
    return {"ok": True}


@router.get("/messages/{mid}/reactions")
def list_reactions(mid: int, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Кто какие реакции поставил (для попапа «кто поставил»)."""
    m = _message_in_conv(db, mid)
    _require_participant(db, m.conversation_id, user)
    rows = db.query(MessageReaction).filter(MessageReaction.message_id == mid).all()
    names = _names_for(db, [r.user_id for r in rows])
    out = {}
    for r in rows:
        out.setdefault(r.emoji, []).append(names.get(r.user_id, r.user_id))
    return {"reactions": [{"emoji": e, "users": u, "count": len(u)} for e, u in out.items()]}


@router.delete("/messages/{mid}")
def delete_message(mid: int, scope: str = Query("self"),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Удалить сообщение. scope=self — скрыть у себя (MessageHidden, у других остаётся);
    scope=all — тумбстоун у всех (автор, либо admin/owner в группе/канале)."""
    m = _message_in_conv(db, mid)
    part = _require_participant(db, m.conversation_id, user)
    if scope == "all":
        if not _can_delete_for_all(part, m, user.id):
            raise HTTPException(status_code=403, detail="Нельзя удалить это сообщение у всех")
        if not m.deleted_at:
            m.deleted_at = _now()
            m.pinned = False               #удалённое не остаётся закреплённым
            db.commit()
            _broadcast(db, m.conversation_id)
        return {"ok": True, "scope": "all", "id": mid}
    #scope=self — скрыть только у себя (идемпотентно).
    exists = (db.query(MessageHidden)
              .filter(MessageHidden.message_id == mid, MessageHidden.user_id == user.id).first())
    if exists is None:
        db.add(MessageHidden(message_id=mid, user_id=user.id))
        db.commit()
    return {"ok": True, "scope": "self", "id": mid}


@router.post("/messages/{mid}/pin")
def pin_message(mid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _message_in_conv(db, mid)
    part = _require_participant(db, m.conversation_id, user)
    conv = _conversation(db, m.conversation_id)
    if not _can_pin(part, conv):
        raise HTTPException(status_code=403, detail="Недостаточно прав для закрепления")
    #Закрепление меняет ОБЩЕЕ состояние беседы и добавляет системную строку — у
    #заблокировавшего это появляется на экране так же, как сообщение.
    _guard_direct_write(db, conv, user)
    if m.deleted_at:
        raise HTTPException(status_code=400, detail="Сообщение удалено")
    m.pinned = True
    m.pinned_at = _now()
    m.pinned_by = user.id
    db.commit()
    db.refresh(m)
    _system(db, m.conversation_id, "pin_added", str(mid))     #§D6
    _broadcast(db, m.conversation_id)
    return _msg_out(m, user.id)


@router.delete("/messages/{mid}/pin")
def unpin_message(mid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _message_in_conv(db, mid)
    part = _require_participant(db, m.conversation_id, user)
    conv = _conversation(db, m.conversation_id)
    if not _can_pin(part, conv):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    m.pinned = False
    db.commit()
    _system(db, m.conversation_id, "pin_removed", str(mid))   #§D6
    _broadcast(db, m.conversation_id)
    return {"ok": True, "id": mid}


@router.get("/chats/{conv_id}/pinned")
def pinned_messages(conv_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Закреплённые сообщения беседы (для плашки сверху)."""
    _require_participant(db, conv_id, user)
    hidden = _hidden_ids(db, conv_id, user.id)
    q = (db.query(Message)
         .filter(Message.conversation_id == conv_id, Message.pinned == True,  # noqa: E712
                 Message.deleted_at == ""))
    if hidden:
        q = q.filter(~Message.id.in_(hidden))
    rows = q.order_by(Message.id.desc()).all()
    #⚠️ Вложение отдаём и здесь. Оно терялось на трёх путях сразу (находка Полковника
    #25.08.2026): в ленте карточка файла была, а в закреплённых и в списке чатов —
    #нет. Расхождение между сериализациями ОДНОГО объекта замечают в последнюю
    #очередь: каждая по отдельности выглядит рабочей.
    out = _msgs_out(db, rows, user.id, {})
    _attach_rich_meta(db, out, user.id)               #закреплённая карточка активности — не сырой id
    return {"pinned": out}


@router.post("/messages/forward")
def forward_messages(payload: dict = Body(...),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Переслать сообщения в другие беседы. Пропускаем адресатов, где пользователь не
    участник, и источники, которых он не видит/удалённые. Копия несёт снимок источника."""
    mids = [int(x) for x in (payload.get("message_ids") or [])]
    targets = [str(x) for x in (payload.get("to_conversation_ids") or [])]
    if not mids or not targets:
        raise HTTPException(status_code=400, detail="Нужны message_ids и to_conversation_ids")
    _guard_can_write(db, user)               #пересылка — тоже создание сообщений
    made = 0
    for conv_id in targets:
        if _participant(db, conv_id, user.id) is None:
            continue                       #в чужую беседу переслать нельзя
        #🔒 Блокировку проверяем и здесь. Без этого запрет обходился пересылкой: участие в
        #беседе у заблокированного осталось, и «переслать» клало в ту же личку что угодно.
        #Отказ ОБЩИЙ, как и при отправке, — он не имеет права раскрывать блокировку.
        _guard_direct_write(db, _conversation(db, conv_id), user)
        for mid in mids:
            src = db.query(Message).filter(Message.id == mid).first()
            if src is None or src.deleted_at:
                continue
            if _participant(db, src.conversation_id, user.id) is None:
                continue                   #нельзя переслать то, что не видишь
            #§12: отчёт по группе — это оценки ВСЕЙ группы. Куда он поедет дальше, решает
            #куратор/администрация; родитель или студент, получивший кнопку, переслать её
            #уже не может (иначе успеваемость группы разошлась бы по чужим чатам).
            if (src.kind or "") == "report" and user.role not in ("teacher", "admin"):
                continue
            #🔒 Карточки активности и доски НЕ пересылаются вовсе. Они привязаны к своей
            #беседе: открыть их снаружи нельзя (403 в `_require_activity_participant`), то
            #есть в чужой ленте повисла бы кнопка, которая ни у кого не работает, — а
            #вместе с ней уехали бы заголовок и статус (заголовок пишет преподаватель, там
            #бывает тема контрольной). Возражение Полковника, 15.08.2026.
            if (src.kind or "") in ("activity", "board"):
                continue
            sender = db.query(User).filter(User.id == src.sender_id).first()
            #Источник пересылки — исходный автор оригинала (а не тот, кто раньше переслал).
            db.add(Message(
                conversation_id=conv_id, sender_id=user.id, body=src.body, created_at=_now(),
                #Тип и формат обязаны переехать вместе с телом: без них пересланная кнопка
                #отчёта превращалась в текстовое сообщение с сырым id («rpt:К75/1|3»).
                kind=(src.kind or "text"), body_format=(src.body_format or "markdown"),
                fwd_from_sender_id=(src.fwd_from_sender_id or src.sender_id),
                fwd_from_conv_id=(src.fwd_from_conv_id or src.conversation_id),
                fwd_from_created_at=(src.fwd_from_created_at or src.created_at),
                fwd_sender_name=(src.fwd_sender_name or (sender.full_name if sender else "")),
            ))
            made += 1
    db.commit()
    for conv_id in targets:
        _broadcast(db, conv_id)
        #🔥 ПЕРЕСЫЛКА В ЧАТ МОДЕРАЦИИ — ТОЖЕ ОБРАЩЕНИЕ (нашёл Полковник 12.09.2026).
        #Студент видит оскорбление, открывает ⚙ «Модерация», пересылает туда сообщение
        #(«вот, разберитесь») и НЕ пишет ни слова. Пока хук стоял только в `send_message`,
        #тикета не заводилось: сообщение в чате есть, в очереди обращения нет, метка
        #«человек написал» не двигается — ровно тот тихий отказ, против которого очередь
        #и заведена.
        _hook_moderation(db, conv_id, user, "")
    return {"forwarded": made}


@router.post("/reports")
def report_message(payload: dict = Body(...),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Пожаловаться на сообщение → создаётся тикет модерации со СНИМКОМ текста (контекст
    сохранится, даже если сообщение потом удалят). На своё сообщение жаловаться нельзя."""
    mid = int(payload.get("message_id") or 0)
    m = _message_in_conv(db, mid)
    _require_participant(db, m.conversation_id, user)   #жаловаться может только участник
    if m.sender_id == user.id:
        raise HTTPException(status_code=400, detail="Нельзя пожаловаться на своё сообщение")
    reason = payload.get("reason_code")
    reason = reason if reason in _REASONS else "other"
    desc = (payload.get("description") or "").strip()[:2000]
    rep = MessageReport(
        message_id=mid, conversation_id=m.conversation_id,
        message_snapshot=m.body or "", reporter_id=user.id, reported_user_id=m.sender_id,
        reason_code=reason, description=desc, created_at=_now(), status="open",
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    return {"ok": True, "report_id": rep.id}
