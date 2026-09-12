"""
safety.py — то, чем человек защищает СЕБЯ САМ: блокировка собеседника, жалоба на профиль,
своё текущее ограничение и «пометить беседу непрочитанной».

Часть пакета `routers/messenger` (разрез 3.7.7). Общий роутер, проверки прав и сборка
ответов — в `_common.py`; порядок регистрации маршрутов задаёт `__init__.py`.

🔑 ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ МОДУЛЬ, А НЕ ДОПИСКА В `users.py`. Здесь живут действия, у
которых ОДНО общее свойство: их совершает не модерация, а сам человек, и последствия у
них наступают немедленно и без чужого решения. Сложив их с каталогом и статусами, мы
получили бы файл, где рядом стоят «показать карточку» и «запретить писать», и правки в
них сталкивались бы в одном месте — ровно то, ради чего мессенджер и разрезали.

⚠️ ЧЕТЫРЕ ПОХОЖИХ МЕХАНИЗМА, И ПУТАТЬ ИХ НЕЛЬЗЯ — каждый решает свою задачу:
• `BlockedUser` (здесь) — ЗАПРЕТ ПИСАТЬ мне, проверяет СЕРВЕР, действует везде в личке;
• `ConversationIgnore` — «скрыть его сообщения у себя в этой беседе», прячет КЛИЕНТ,
  писать он по-прежнему может;
• `MessageHidden` — «удалить у себя» одно уже существующее сообщение;
• `MutedUser` — наказание МОДЕРАЦИЕЙ, действует на весь продукт и снимается по сроку.
"""
from ._common import *      # noqa: F401,F403 — роутеры, модели, хелперы


# ── Блокировка человек↔человек ───────────────────────────────────────────────────────
@router.get("/blocks")
def my_blocks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Кого я заблокировал. Нужен клиенту, чтобы показать в меню «Разблокировать» вместо
    «Заблокировать»: без этого списка кнопка каждый раз предлагала бы блокировать заново,
    а человек не понимал бы, сработало ли прошлое нажатие."""
    rows = db.query(BlockedUser).filter(BlockedUser.blocker_id == user.id).all()
    ids = [r.blocked_id for r in rows]
    onl = _online_logins()
    people = []
    for u in (db.query(User).filter(User.id.in_(ids)).all() if ids else []):
        people.append(_safe_user(u, onl))
    return {"blocked_ids": ids, "people": people}


@router.post("/users/{uid}/block")
def block_user(uid: str, payload: dict = Body(default={}),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Заблокировать/разблокировать человека. Односторонне и МОЛЧА.

    ⚠️ ЗАБЛОКИРОВАННОМУ НЕ СООБЩАЮТ, и это решение, а не недоработка. Уведомление
    превращает блокировку в способ объявить человеку «ты мне неприятен» — то есть в
    инструмент конфликта, а не защиты от него. Так же молчат Telegram и Discord. Отсюда и
    общий текст отказа при отправке (см. `_guard_not_blocked`): «сообщение не доставлено»,
    а не «вас заблокировали».

    ⚠️ БЛОКИРОВАТЬ МОДЕРАЦИЮ И АДМИНИСТРАЦИЮ НЕЛЬЗЯ. Иначе первый же нарушитель закрывал
    бы себе канал, по которому с ним разговаривают о нарушении, — и разбор жалобы упирался
    бы в то, что уведомить человека нечем.
    """
    if uid == user.id:
        raise HTTPException(status_code=400, detail="Нельзя заблокировать самого себя")
    target = db.query(User).filter(User.id == uid, User.deleted == False).first()  # noqa: E712
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.role in MODERATION_ROLES:
        raise HTTPException(status_code=400,
                            detail="Администрацию и модерацию заблокировать нельзя")
    blocked = bool(payload.get("blocked", True))
    row = (db.query(BlockedUser)
           .filter(BlockedUser.blocker_id == user.id, BlockedUser.blocked_id == uid).first())
    if blocked and row is None:
        db.add(BlockedUser(blocker_id=user.id, blocked_id=uid, created_at=_now()))
    elif not blocked and row is not None:
        db.delete(row)
    db.commit()
    return {"ok": True, "user_id": uid, "blocked": blocked}


# ── Жалоба на ПРОФИЛЬ (не на сообщение) ──────────────────────────────────────────────
#На что именно жалуются. Список закрытый: свободная строка отсюда уезжает модератору в
#интерфейс, и «поле» превратилось бы во второе описание жалобы.
_REPORT_FIELDS = {"profile", "name", "bio", "avatar", "banner"}


@router.post("/user-reports")
def report_user(payload: dict = Body(...),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Пожаловаться на ПРОФИЛЬ человека → тикет в отдельную очередь модерации.

    ⚠️ СНИМОК ОБЯЗАТЕЛЕН по той же причине, что у жалобы на сообщение: пока тикет ждёт
    разбора, человек успевает переписать «о себе» или сменить аватарку, и модератор увидит
    невинный профиль. Снимок делает СЕРВЕР из текущих `prefs`, а не принимает от клиента:
    присланный снимок — это текст, который пишет жалующийся, то есть готовый способ
    приписать чужому профилю чего там не было.
    ⚠️ На свой профиль жаловаться нельзя — тот же гейт, что у сообщений.
    """
    uid = str(payload.get("user_id") or "")
    if uid == user.id:
        raise HTTPException(status_code=400, detail="Нельзя пожаловаться на свой профиль")
    target = db.query(User).filter(User.id == uid, User.deleted == False).first()  # noqa: E712
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    reason = payload.get("reason_code")
    reason = reason if reason in _REASONS else "other"
    field = payload.get("field")
    field = field if field in _REPORT_FIELDS else "profile"
    desc = (payload.get("description") or "").strip()[:2000]
    prefs = target.prefs if isinstance(target.prefs, dict) else {}
    #Снимок — ровно то поле, на которое жалуются. Для аватарки и баннера это ссылка или
    #data:URL: показывать модератору саму картинку важнее, чем экономить строку.
    snapshot = {
        "name": target.full_name or target.name or "",
        "bio": prefs.get("bio", "") or "",
        "avatar": prefs.get("avatar", "") or "",
        "banner": prefs.get("profile_banner", "") or "",
    }.get(field, "") or (target.full_name or "")
    rep = UserReport(
        reported_user_id=uid, reporter_id=user.id, reason_code=reason, description=desc,
        field=field, snapshot=_snapshot_fit(snapshot), created_at=_now(), status="open",
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    return {"ok": True, "report_id": rep.id}


#Потолок снимка. Аватарка и баннер приезжают как `data:`-строка (обрезанный 256×256
#JPEG), и она заметно длиннее текста «о себе».
_SNAPSHOT_LIMIT = 400_000


def _snapshot_fit(value: str) -> str:
    """Снимок поля, который не станет ВРАНЬЁМ от обрезки.

    🔥 ОБЫЧНОЕ `value[:N]` ЗДЕСЬ НЕЛЬЗЯ. Для текста обрезка честна — видно, что он
    длинный. Для картинки `data:`-строка, урезанная посередине, превращается в БИТОЕ
    изображение: модератор видит сломанный квадрат и решает, что испортился снимок, а не
    что картинка не поместилась. Поэтому слишком большое значение заменяется ПУСТОТОЙ —
    «снимка нет» честнее, чем «снимок испорчен».
    """
    value = value or ""
    return value if len(value) <= _SNAPSHOT_LIMIT else ""


# ── Своё ограничение ─────────────────────────────────────────────────────────────────
@router.get("/my-restriction")
def my_restriction(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Ограничен ли Я, до каких пор и за что.

    🔥 БЕЗ ЭТОГО ЧЕЛОВЕК УЗНАВАЛ О МЬЮТЕ ТОЛЬКО ПО ОТКАЗУ ПРИ ОТПРАВКЕ — то есть написав
    сообщение и потеряв его. Причём отказ был глухим («вы ограничены модерацией»), и
    единственным осмысленным следующим действием было обращение в поддержку, которое
    разбирает та же модерация. Наказание не должно само генерировать себе работу.

    ⚠️ Ручка отдаёт СВОЁ состояние и ничего больше: чужой мьют — дело модерации, и
    показывать его в каталоге рядовому пользователю незачем.
    ⚠️ Истёкший мьют здесь же и снимается — она ходит через тот же `_mute_row`, что и
    барьер записи. Две разные проверки срока разошлись бы, и человек видел бы «ограничение
    снято» при работающем запрете (или наоборот).
    """
    row = _mute_row(db, user.id)
    if row is None:
        return {"muted": False}
    return {
        "muted": True,
        "muted_until": row.muted_until or "",     #"" = бессрочно (наложен до появления срока)
        "reason": row.reason or "",
        "appeal_hint": "Обжаловать можно в чате с модерацией — он остаётся открытым.",
    }
