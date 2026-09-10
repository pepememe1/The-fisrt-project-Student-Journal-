"""
activities.py — «Активности» в беседах (docs/done/PLAN-ACTIVITIES.md).

Совместное действие участников беседы: доска, викторина, соревнование, опрос, срез
понимания, тайм-бокс. Запускает преподаватель (или тот, кому владелец беседы выдал право
`activities`), участвуют все, кто в беседе.

Почему ОТДЕЛЬНЫЙ файл, а не внутри `messenger.py`: тот уже 3300 строк и был бы следующим
кандидатом на разрез — повторять историю `web.py` (4288 строк, 62 коммита за полгода,
вечные расхождения при параллельной работе двоих) незачем.

Почему префикс `/web/messenger/activities`: он уже входит в `_PROXY_PREFIXES`
(`desktop/local_api.py`), то есть ВНУТРИ программы активности работают без единой правки
десктопа. Мессенджер — онлайн-подсистема (§5.4 CLAUDE.md), активности тоже: ни одна из
их таблиц не в `SYNC_MODELS`.

Разделение транспорта: вниз (сервер → участники) идёт WebSocket тем же примитивом, что
рассылает `changed` в мессенджере; вверх (участник → сервер) — обычный HTTP. Ответ
студента это единичное действие, и REST даёт готовую проверку прав, аудит и внятные коды
ошибок, тогда как разбор команд внутри сокета всё это пришлось бы писать заново.
"""
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import activity_grading, activity_state, audit, msg_limit
from ..db import get_db
from ..deps import get_current_user
from ..models import (
    Activity, ActivityFeedback, ActivityResult, BoardArtifact,
    Conversation, ConversationParticipant, Message, MessageReport,
    QuizOption, QuizQuestion, QuizSet, User,
)

router = APIRouter(prefix="/web/messenger/activities", tags=["activities"])

#Категории. `contest` — синхронная надстройка над `quiz` (тот же конструктор и та же
#проверка ответов), поэтому отдельного набора вопросов у него нет.
KINDS = ("board", "quiz", "contest", "poll", "pulse", "timer")

#Соревнование допускает ТОЛЬКО типы с бесспорным баллом. В `order`/`match` спорна цена
#частично верного ответа, а на табло рядом с чужими баллами спор недопустим (§8.5 плана).
CONTEST_TYPES = ("single", "multi")

QUESTION_TYPES = ("single", "multi", "order", "match")
VISIBILITY = ("private", "college", "stock")

#Срез понимания: сколько плашка висит у студентов по умолчанию (хост может закрыть раньше).
PULSE_DEFAULT_S = 60
#Потолки: свободный текст отзыва и число вариантов опроса. Не косметика — оба поля
#приходят от клиента и попадают на экран преподавателю.
MAX_TEXT = 500
MAX_OPTIONS = 12
MAX_TAGS = 12
MAX_QUESTIONS = 100
#Штрихи доски прилетают пачками. Потолок на пачку — защита от одного запроса на мегабайты
#(поток штрихов и так режется клиентом до ~150 мс, см. §7 плана).
MAX_STROKES_PER_BATCH = 500
MAX_STROKES_TOTAL = 20000
#Потолок на ОДИН штрих. Числа штрихов мало: сам штрих — непрозрачный словарь от клиента,
#и без этого предела 20000 разрешённых штрихов могли весить сколько угодно (см.
#`_clean_strokes`). 4 КБ — шестикратный запас к настоящему куску линии за 150 мс.
MAX_STROKE_BYTES = 4096


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Доступ ───────────────────────────────────────────────────────────────────────────
def _conv(db: Session, conv_id: str) -> Conversation:
    c = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    return c


def _participant(db: Session, conv_id: str, user_id: str):
    return (db.query(ConversationParticipant)
            .filter(ConversationParticipant.conversation_id == conv_id,
                    ConversationParticipant.user_id == user_id).first())


def _require_participant(db: Session, conv_id: str, user: User) -> ConversationParticipant:
    _conv(db, conv_id)
    p = _participant(db, conv_id, user.id)
    if p is None:
        raise HTTPException(status_code=403, detail="Нет доступа к этой беседе")
    return p


def _require_can_run(db: Session, conv_id: str, user: User) -> ConversationParticipant:
    """Право запускать активности в ЭТОЙ беседе (§3 плана).

    Три условия, и каждое закрывает свой случай:
      • беседа общая (group/channel) — в личном чате, «Избранном» и чате модерации
        активности бессмысленны: там либо один собеседник, либо один человек;
      • право `activities` у роли в беседе ЛИБО системная роль teacher/admin —
        преподаватель ведёт занятие, а не администрирует чат, и просить владельца чата
        выдать ему роль ради этого странно;
      • человек не заглушен — ни глобально модерацией, ни в этой беседе (`/mute`).
    """
    from .messenger import _permissions_for, _guard_can_write
    conv = _conv(db, conv_id)
    if conv.kind not in ("group", "channel"):
        raise HTTPException(status_code=403,
                            detail="Активности бывают только в группах и каналах")
    part = _require_participant(db, conv_id, user)
    if part.silenced:
        raise HTTPException(status_code=403, detail="Вы заглушены в этой беседе")
    _guard_can_write(db, user)          #глобальный мьют модерации (403)
    if user.role in ("teacher", "admin"):
        return part
    if "activities" not in _permissions_for(db, part):
        raise HTTPException(status_code=403,
                            detail="Нет права запускать активности в этой беседе")
    return part


def _activity(db: Session, activity_id: str) -> Activity:
    a = db.query(Activity).filter(Activity.id == activity_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="Активность не найдена")
    return a


def _require_activity_participant(db: Session, activity_id: str, user: User) -> Activity:
    a = _activity(db, activity_id)
    _require_participant(db, a.conversation_id, user)
    return a


def _require_host(db: Session, activity_id: str, user: User) -> Activity:
    a = _require_activity_participant(db, activity_id, user)
    if a.host_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Управлять активностью может только ведущий")
    return a


def _require_running(a: Activity) -> Activity:
    if a.status != "running":
        raise HTTPException(status_code=400, detail="Активность уже завершена")
    return a


def _throttle(bucket: str, user: User) -> None:
    """Тормоз на действие участника (18.08.2026, адверсариальный разбор).

    До этого в активностях не было НИ ОДНОГО ограничения частоты, хотя у сообщений оно
    есть с 2.9.1 и заведено ровно по этой причине. Разница в том, что здесь дешёвый запрос
    УСИЛИВАЕТСЯ: `progress` рассылает кадр всем участникам беседы и делает три запроса в
    базу у ведущего, то есть в канале на сотню человек один POST превращается в сотню
    кадров. Права тут не спасают — усилитель доступен любому законному участнику.

    Отвечаем 429 с `Retry-After`: это не наказание, а просьба подождать, и клиент обязан
    уметь её прочитать. Пороги — в `msg_limit._BUCKETS`, подобраны от реального поведения
    клиента (штрихи идут кусками раз в 150 мс), а не от круглого числа."""
    wait = msg_limit.bucket_check(bucket, user.id)
    if wait:
        raise HTTPException(status_code=429, detail="Слишком часто, подождите немного",
                            headers={"Retry-After": str(wait)})


# ── Рассылка ─────────────────────────────────────────────────────────────────────────
def _emit(db: Session, conv_id: str, data: dict) -> None:
    """Кадр участникам беседы. Никогда не роняет запрос: сокет — ускоритель, а не
    транспорт правды (клиент всё равно умеет перечитать состояние по HTTP)."""
    try:
        from .messenger import ws_manager, _participant_ids
        ws_manager.emit_users(_participant_ids(db, conv_id), data)
    except Exception:
        pass


def _emit_to(user_ids: list, data: dict) -> None:
    """Кадр КОНКРЕТНЫМ людям (например, распределение голосов — только автору опроса).

    Отдельно от `_emit`: тот рассылает всем участникам беседы, а есть данные, которые
    в общей рассылке прочитали бы все. Так же не роняет запрос: сокет — ускоритель."""
    try:
        from .messenger import ws_manager
        ws_manager.emit_users(user_ids, data)
    except Exception:
        pass


def _emit_host_progress(db: Session, a: Activity) -> None:
    """Свежая шкала прохождения — ТОЛЬКО ведущему.

    🔥 Без этого мониторинг выглядел мёртвым, и это была настоящая поломка, а не мелочь.
    `progress` считается в проекции состояния (`_state_for`), то есть попадает клиенту
    ОДИН раз — при открытии активности. Кадры по сокету несут только изменившиеся поля
    (`walk_bump`, `submitted_count`, `answered_count`), а клиент их просто подмешивает —
    значит `progress` после открытия не обновлялся НИКОГДА: преподаватель смотрел на
    список, застывший в момент, когда ещё никто не начинал.

    Кадр адресный: чужой прогресс студентам не полагается. И со СВОИМ `seq` — клиент
    отбрасывает кадры с номером не больше текущего, поэтому повторить номер предыдущей
    рассылки нельзя, иначе шкала молча не приехала бы.
    """
    if a.kind not in ("quiz", "contest") or not a.host_id:
        return
    snap = activity_state.bump(a.id)
    if snap is None:
        return
    payload = dict(snap.get("payload") or {})
    answers = payload.get("answers") or {}
    _host_progress(db, a, payload, answers)
    _emit_to([a.host_id],
             {"type": "activity.state", "activity_id": a.id,
              "conversation_id": a.conversation_id, "seq": snap["seq"],
              "payload": {k: payload[k] for k in ("progress", "participants",
                                                  "total_questions") if k in payload}})


def _require_state(snap: dict | None) -> dict:
    """Снимок состояния активности — или внятный отказ вместо 500.

    `activity_state.patch/bump` отдают None, когда активности УЖЕ НЕТ в памяти процесса.
    Случаев два, и оба живые:
      • ведущий нажал «Завершить» МЕЖДУ чтением состояния и записью — обработчики
        FastAPI выполняются в пуле потоков, то есть параллельно по-настоящему, и
        проверки `if snap is None` сразу после `get()` для этого мало;
      • служба перезапускалась (состояние живёт в памяти ОДНОГО uvicorn — инвариант
        «один процесс»), то есть после каждого деплоя, пока идёт активность.
    Дальше по коду шло `snap["seq"]`, и участник получал 500 с трейсбеком вместо
    «активность завершена». Найдено проверкой типов (mypy, «dict | None is not
    indexable»), держит
    `tests/test_activities.py::test_vote_survives_activity_finished_between_read_and_write`.
    """
    if snap is None:
        raise HTTPException(status_code=400, detail="Активность уже завершена")
    return snap


def _emit_state(db: Session, a: Activity, snapshot: dict | None) -> None:
    if not snapshot:
        return
    _emit(db, a.conversation_id, {"type": "activity.state", "activity_id": a.id,
                                  "conversation_id": a.conversation_id,
                                  "seq": snapshot["seq"], "payload": snapshot["payload"]})


# ── Сериализация ─────────────────────────────────────────────────────────────────────
def _activity_out(a: Activity, me_id: str = "") -> dict:
    return {"id": a.id, "conversation_id": a.conversation_id, "kind": a.kind,
            "host_id": a.host_id, "is_host": bool(me_id) and a.host_id == me_id,
            "title": a.title or "", "params": a.params or {}, "status": a.status,
            "started_at": a.started_at or "", "finished_at": a.finished_at or "",
            "message_id": a.message_id or 0}


def _question_out(q: QuizQuestion, options: list, with_key: bool, shuffle_seed: str = "") -> dict:
    """Вопрос для клиента. `with_key=False` — режим СТУДЕНТА.

    🔒 В режиме студента поля `is_correct` / `correct_position` / `match_key` не кладутся
    ВООБЩЕ (не пустыми — их нет), потому что «пустое поле» в JSON тоже сообщает, где
    ключ, а где нет. Это главный инвариант категории: получив ключ, любой студент
    прочитает его в инструментах разработчика до начала. Держит
    `test_student_never_receives_the_answer_key`.

    Порядок вариантов перемешивается у каждого своим `shuffle_seed` — дешёвая защита от
    «подсмотрел у соседа». Перемешивание ДЕТЕРМИНИРОВАНО (по seed), иначе повторный
    запрос вопросов после обрыва сети дал бы другой порядок, и уже выбранный вариант
    съехал бы на чужую строку."""
    opts = list(options)
    if shuffle_seed:
        #Свой детерминированный порядок: сортируем по хешу пары (seed, id варианта).
        #random.shuffle с seed зависел бы от версии Python — здесь порядок обязан быть
        #одинаковым при повторном запросе, а не «случайным каждый раз».
        opts.sort(key=lambda o: _stable_hash(f"{shuffle_seed}|{o.id}"))
    out_opts = []
    for o in opts:
        cell = {"id": o.id, "text": o.text or ""}
        if with_key:
            cell["is_correct"] = bool(o.is_correct)
            cell["match_key"] = o.match_key or ""
            cell["correct_position"] = int(o.correct_position or 0)
        out_opts.append(cell)
    out = {"id": q.id, "order_no": int(q.order_no or 0), "type": q.type or "single",
           "text": q.text or "", "points": int(q.points or 1), "options": out_opts}
    #Сопоставление: без списка правых половин студенту нечего выбирать — раньше он
    #ВПИСЫВАЛ пару руками, то есть попадал в ответ только дословным совпадением.
    #🔒 Сам список ключ НЕ раскрывает: он перемешан и не привязан к вариантам, поэтому
    #«какая половина к какой» по нему не восстановить — это ровно те же слова, что и так
    #написаны на экране, только в другом порядке.
    if (q.type or "single") == "match" and not with_key:
        pool = sorted({(o.match_key or "").strip() for o in options if (o.match_key or "").strip()},
                      key=lambda s: _stable_hash(f"{shuffle_seed}|pool|{s}"))
        out["match_pool"] = pool
    return out


def _stable_hash(s: str) -> str:
    """Устойчивый между запусками хеш строки. Встроенный hash() рандомизирован
    (PYTHONHASHSEED), и порядок вариантов менялся бы при каждом перезапуске сервера —
    для перемешивания это допустимо, а вот в пределах ОДНОЙ сессии студента нет."""
    #sha256, а не sha1: криптостойкость здесь не нужна (это перемешивание), но
    #объяснять каждому анализатору, почему слабый хеш «тут можно», дороже, чем
    #просто взять сильный. Смена алгоритма один раз изменит порядок вариантов —
    #на смысл не влияет, он и так у каждого свой.
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _quiz_out(q: QuizSet, questions_count: int = 0) -> dict:
    return {"id": q.id, "author_id": q.author_id, "title": q.title or "",
            "description": q.description or "", "tags": list(q.tags or []), "time_limit_s": int(q.time_limit_s or 0), "kind": q.kind or "quiz",
            "visibility": q.visibility or "private", "parent_id": q.parent_id or "",
            "created_at": q.created_at or "", "updated_at": q.updated_at or "",
            "questions_count": questions_count}


def _feedback_out(f: ActivityFeedback) -> dict:
    """Отзыв для ПРЕПОДАВАТЕЛЯ.

    🔑 `user_id` не кладём ни в одно поле — ни прямо, ни в виде «номера участника»,
    по которому его можно было бы вычислить сопоставлением с ростером. Автор в базе
    ЕСТЬ (иначе жалоба админу никуда не ведёт), но интерфейс преподавателя его не видит
    никогда — ни до жалобы, ни после. Держит `test_host_feedback_never_leaks_authors`."""
    return {"id": f.id, "score": int(f.score_1_10 or 0),
            "reason_code": f.reason_code or "", "text": f.custom_text or "",
            "created_at": f.created_at or ""}


# ── Запуск и завершение ──────────────────────────────────────────────────────────────
def _sweep_stale(db: Session) -> None:
    """Закрыть активности, брошенные хостом (вкладку закрыли и не вернулись), и те, что
    не пережили рестарт сервера.

    Без этого беседа получала бы вечный 409 «активность уже идёт»: строка в БД со
    статусом `running` живёт, а состояния в памяти нет — запустить новую нельзя вовсе,
    и починить это человек изнутри продукта не может."""
    ids = activity_state.stale_ids()
    if ids:
        for a in db.query(Activity).filter(Activity.id.in_(ids),
                                           Activity.status == "running").all():
            a.status = "finished"
            a.finished_at = _now()
        db.commit()
    _sweep_expired_timers(db)
    _sweep_expired_polls(db)
    _advance_expired_contests(db)


def _advance_expired_contests(db: Session) -> None:
    """Соревнование: время вопроса вышло — переходим к следующему САМИ, а после
    последнего завершаем активность.

    🔥 Прямое требование и одновременно починка тупика: на последнем вопросе «следующий»
    отвечал ошибкой «вопросы закончились», выбрать ответ было уже нельзя, а итоги
    появлялись, только если ведущий вручную нажимал «завершить». Теперь ход ведёт время,
    как в любой викторине-игре, а ведущий может обогнать его кнопкой.

    Двигает СЕРВЕР, а не клиент: у каждого свои часы, и переход по клиентскому таймеру
    у тридцати человек случился бы тридцать раз в разные моменты."""
    for a in db.query(Activity).filter(Activity.kind == "contest",
                                       Activity.status == "running").all():
        snap = activity_state.get(a.id)
        if snap is None:
            continue
        live = snap.get("payload") or {}
        idx = int(live.get("question_index", -1))
        ends = live.get("question_ends_at") or ""
        if idx < 0 or not ends or _seconds_left(ends) > 0:
            continue                       #ещё не стартовали или время не вышло
        quiz_id = (a.params or {}).get("quiz_id") or ""
        total = db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id).count()
        if idx + 1 < total:
            _contest_show(db, a, idx + 1, total)
        else:
            _finish_activity_row(db, a, save=False, expired=True)


def _contest_show(db: Session, a: Activity, idx: int, total: int) -> None:
    """Показать вопрос №idx ВСЕМ. Одна реализация на кнопку ведущего и на автопереход по
    времени — иначе ход соревнования зависел бы от того, кто его сдвинул."""
    import time
    limit_ms = int((a.params or {}).get("limit_ms") or 30000)
    snap = activity_state.patch(a.id, {
        "question_index": idx,
        "question_started_ms": int(time.monotonic() * 1000),
        "question_ends_at": _plus_seconds(_now(), limit_ms / 1000.0),
    })
    if snap is None:
        return
    _emit(db, a.conversation_id,
          {"type": "activity.state", "activity_id": a.id,
           "conversation_id": a.conversation_id, "seq": snap["seq"],
           "payload": {"question_index": idx, "total": total, "limit_ms": limit_ms,
                       "question_ends_at": snap["payload"]["question_ends_at"],
                       "answered_count": 0}})
    _emit_host_progress(db, a)


def _finish_activity_row(db: Session, a: Activity, save: bool = False,
                         expired: bool = False) -> dict:
    """Завершить активность и разослать итог. Общая для кнопки ведущего и для завершения
    ПО ВРЕМЕНИ: иначе у автозавершения не было бы ни итогов, ни события, и участники
    остались бы на экране вопроса навсегда.

    Итог считаем ДО снятия состояния из памяти: после `drop` считать уже нечего."""
    summary = _finalize(db, a, (activity_state.get(a.id) or {}).get("payload") or {}, save)
    if expired:
        summary = {**summary, "expired": True}
    a.status = "finished"
    a.finished_at = _now()
    db.commit()
    activity_state.drop(a.id)
    _emit(db, a.conversation_id,
          {"type": "activity.finished", "activity_id": a.id,
           "conversation_id": a.conversation_id,
           "finished_at": a.finished_at, "summary": summary})
    try:
        from .messenger import _broadcast
        _broadcast(db, a.conversation_id)
    except Exception:
        pass
    return summary


def _sweep_expired_polls(db: Session) -> None:
    """Опрос, у которого вышел срок, ЗАВЕРШАЕТСЯ САМ.

    🔥 Раньше подметались только таймеры и соревнования, и это стоило бы нам голосов.
    Снимок итогов (`final_counts`/`final_votes`) сохраняется ТОЛЬКО при завершении, а
    живые голоса лежат в памяти процесса. Опрос со сроком доходил до нуля и оставался
    `running` навсегда — значит при первом же рестарте сервера его закрывала общая
    уборка `_sweep_stale`, уже БЕЗ снимка, и в ленте навсегда оставалось «проголосовало:
    0» при полной группе проголосовавших.

    Второе следствие важно не меньше: пока опрос числится идущим, итог показывать
    нельзя (см. `_poll_cell`), то есть без этой уборки победитель не появлялся бы вовсе
    у опросов, которые никто не закрыл руками, — а это большинство."""
    for a in db.query(Activity).filter(Activity.kind == "poll",
                                       Activity.status == "running").all():
        snap = activity_state.get(a.id)
        if snap is None:
            continue
        ends = (snap.get("payload") or {}).get("ends_at") or ""
        #Без срока опрос живёт, пока автор не закроет, — это законный режим.
        if not ends or _seconds_left(ends) > 0:
            continue
        #save=True: та же ветка, что у кнопки «завершить», — снимок итогов обязателен.
        _finish_activity_row(db, a, save=True, expired=True)


def _sweep_expired_timers(db: Session) -> None:
    """Тайм-бокс, у которого вышло время, ЗАВЕРШАЕТСЯ САМ.

    🔥 Прямое требование: «если время таймера закончилось, он висеть не должен — именно
    ОТМЕНЯЕТСЯ, а не становится невидимым». Раньше отсчёт доходил до нуля, а активность
    оставалась `running` — она держала место (второй таймер запустить нельзя) и тихо
    висела в фоне. Завершаем ЗДЕСЬ, на сервере: клиент мог закрыть вкладку сразу после
    старта, и надеяться, что кто-то нажмёт «завершить», нельзя.

    Пауза не считается истечением: у остановленного таймера времени окончания нет, он
    ждёт человека."""
    for a in db.query(Activity).filter(Activity.kind == "timer",
                                       Activity.status == "running").all():
        snap = activity_state.get(a.id)
        if snap is None:
            continue
        live = snap.get("payload") or {}
        if live.get("paused"):
            continue
        ends = live.get("ends_at") or ""
        if not ends or _seconds_left(ends) > 0:
            continue
        a.status = "finished"
        a.finished_at = _now()
        db.commit()
        activity_state.drop(a.id)
        #Кадр всем: по нему клиент гасит полоску и показывает окно с сигналом.
        _emit(db, a.conversation_id,
              {"type": "activity.finished", "activity_id": a.id,
               "conversation_id": a.conversation_id, "finished_at": a.finished_at,
               "summary": {"expired": True, "duration_s": int(live.get("duration_s") or 0)}})


#Категории, которых в беседе может идти НЕСКОЛЬКО сразу. Опрос — сообщение в ленте, он
#никому не мешает и живёт своей жизнью; остальные занимают экран или ведут ход, и две
#одинаковых означали бы два источника правды «что сейчас идёт».
_MULTI_ALLOWED = ("poll",)


def _running_same_kind(db: Session, conv_id: str, kind: str) -> Activity | None:
    """Идущая активность ТОЙ ЖЕ категории (или None).

    ⚠️ Правило сменилось по живому отзыву: раньше беседа держала ОДНУ активность любого
    вида, и запустить таймер рядом с викториной было нельзя — а это ровно то, что делают
    на паре. Теперь ограничение точечное: два таймера или два среза одновременно
    по-прежнему бессмысленны (два отсчёта на экране, два одинаковых опроса понимания),
    а таймер рядом с доской — нормальная работа."""
    if kind in _MULTI_ALLOWED:
        return None
    for a in (db.query(Activity)
              .filter(Activity.conversation_id == conv_id, Activity.status == "running",
                      Activity.kind == kind)
              .order_by(Activity.started_at.desc()).all()):
        if activity_state.get(a.id) is not None:
            return a
        #След рестарта: строка «running» без состояния в памяти — закрываем на месте.
        a.status = "finished"
        a.finished_at = _now()
        db.commit()
    return None


def _running_in(db: Session, conv_id: str) -> Activity | None:
    """Активность, идущая в беседе ПРЯМО СЕЙЧАС (или None).

    Строка `running` без состояния в памяти — след рестарта сервера: недоигранное мы
    осознанно не восстанавливаем (§2 плана), поэтому такую строку закрываем на месте, а
    не отдаём как живую. Иначе участники увидели бы активность, которая не отвечает ни на
    одно действие."""
    a = (db.query(Activity)
         .filter(Activity.conversation_id == conv_id, Activity.status == "running")
         .order_by(Activity.started_at.desc()).first())
    if a is None:
        return None
    if activity_state.get(a.id) is None:
        a.status = "finished"
        a.finished_at = _now()
        db.commit()
        return None
    return a


#Категории, которые оставляют отметку в ленте беседы. Таймер и срез понимания её НЕ
#оставляют: они живут минуты, всплывают у всех сами и в историю возвращаться незачем —
#отметка о каждом запуске засоряла бы переписку. Опрос ведёт себя иначе и разбирается
#отдельно: он и есть сообщение в чате с кнопками (как в Telegram), а не ссылка на оверлей.
_LEAVES_TRACE_IN_FEED = ("quiz", "contest", "board", "poll")
#Опрос оставляет след ОСОБЫМ видом сообщения: он и ЕСТЬ сообщение с кнопками, как в
#Telegram, — голосуют прямо в ленте, не открывая ничего. Остальные категории оставляют
#карточку-ссылку на оверлей.
_KIND_OF_FEED_MESSAGE = {"poll": "poll"}


def _initial_payload(kind: str, params: dict) -> dict:
    """Начальное живое состояние по категории."""
    if kind == "timer":
        return {"ends_at": params.get("ends_at", ""), "paused": False,
                "duration_s": int(params.get("duration_s") or 0)}
    if kind == "poll":
        #votes — {user_id: индекс варианта}. Голос НЕ анонимен для сервера осознанно:
        #иначе ни накрутку не поймать, ни ошибочное нажатие не исправить. В интерфейсе
        #формулировка честная — «результаты видит только преподаватель».
        return {"votes": {}, "public_votes": bool(params.get("public_votes"))}
    if kind == "pulse":
        return {"open": True, "answered": []}
    if kind == "board":
        return {"sheet": params.get("sheet", "blank"), "strokes": [],
                "pen_user_id": "", "pen_history": []}
    if kind == "contest":
        return {"quiz_id": params.get("quiz_id", ""), "question_index": -1,
                "question_started_ms": 0, "limit_ms": int(params.get("limit_ms") or 30000),
                "answers": {}, "scores": {}}
    return {"quiz_id": params.get("quiz_id", "")}


def _validate_start(db: Session, kind: str, params: dict, user: User) -> dict:
    """Проверить параметры запуска и вернуть очищенные (клиентским не доверяем)."""
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="Неизвестная категория активности")
    p = dict(params or {})
    if kind == "timer":
        secs = int(p.get("duration_s") or 0)
        if secs <= 0 or secs > 6 * 60 * 60:
            raise HTTPException(status_code=400, detail="Укажите длительность от 1 секунды до 6 часов")
        return {"duration_s": secs}
    if kind == "poll":
        question = str(p.get("question") or "").strip()[:MAX_TEXT]
        options = [str(o).strip()[:MAX_TEXT] for o in (p.get("options") or []) if str(o).strip()]
        if not question:
            raise HTTPException(status_code=400, detail="Нужен текст вопроса")
        if len(options) < 2:
            raise HTTPException(status_code=400, detail="Нужно хотя бы два варианта")
        out = {"question": question, "options": options[:MAX_OPTIONS]}
        #Показывать ли итог всем ПОСЛЕ завершения. По умолчанию да: иначе победителя не
        #увидит никто, кроме автора, и подсветка теряет смысл. Обещание даётся ЗАРАНЕЕ —
        #подпись под опросом сообщает об этом до голосования, поэтому раскрытие в конце
        #не является изменением правил задним числом. Автор вправе выключить.
        out["reveal_results"] = bool(p.get("reveal_results", True))
        #Срок окончания — как в Telegram. По умолчанию СУТКИ: бессрочный опрос висел в
        #ленте вечно, и никто не знал, когда смотреть результат. Явный 0 по-прежнему
        #значит «без срока» — но это осознанный выбор автора, а не молчаливое умолчание.
        raw = p.get("duration_s")
        secs = int(raw) if raw is not None else 24 * 60 * 60
        if secs:
            out["duration_s"] = max(30, min(secs, 7 * 24 * 60 * 60))
        #Видно ли, КТО как проголосовал. По умолчанию нет: голос не анонимен для сервера
        #(иначе не поймать накрутку), но показывать его классу — отдельное решение,
        #и принимать его должен автор опроса осознанно.
        out["public_votes"] = bool(p.get("public_votes"))
        return out
    if kind == "pulse":
        secs = int(p.get("duration_s") or PULSE_DEFAULT_S)
        return {"duration_s": max(10, min(secs, 30 * 60))}
    if kind == "board":
        sheet = str(p.get("sheet") or "blank")
        if sheet not in ("blank", "grid", "lined"):
            sheet = "blank"
        out = {"sheet": sheet}
        #«Продолжить доску» — подхватываем штрихи сохранённого артефакта этой же беседы.
        src = str(p.get("continue_board_id") or "").strip()
        if src:
            out["continue_board_id"] = src
        return out
    #quiz | contest
    quiz_id = str(p.get("quiz_id") or "").strip()
    quiz = db.query(QuizSet).filter(QuizSet.id == quiz_id, QuizSet.deleted == False).first()  # noqa: E712
    if quiz is None:
        raise HTTPException(status_code=404, detail="Викторина не найдена")
    _require_quiz_readable(quiz, user)
    questions = db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id).all()
    if not questions:
        raise HTTPException(status_code=400, detail="В викторине нет ни одного вопроса")
    if kind == "contest":
        bad = [q for q in questions if (q.type or "single") not in CONTEST_TYPES]
        if bad:
            raise HTTPException(
                status_code=400,
                detail="В соревновании допустимы только вопросы с одним и несколькими "
                       "вариантами: в «расставить по порядку» и «сопоставить» спорна цена "
                       "частично верного ответа, а на общем табло спор недопустим")
        return {"quiz_id": quiz_id, "limit_ms": max(5000, min(int(p.get("limit_ms") or 30000), 300000))}
    return {"quiz_id": quiz_id}


@router.post("/start")
def start_activity(payload: dict = Body(...), request: Request = None,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Запустить активность в беседе. Одна на беседу одновременно (иначе — 409).

    Вторая параллельная — это гонка за экран студента и два источника правды «что сейчас
    идёт», поэтому отказ, а не «запустим рядом». В ответе 409 называем текущую: без этого
    человек видит отказ и не понимает, чей он."""
    conv_id = str(payload.get("conversation_id") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    _require_can_run(db, conv_id, user)
    _sweep_stale(db)
    running = _running_same_kind(db, conv_id, kind)
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail=f"В беседе уже идёт «{running.title or running.kind}» этой же категории. "
                   f"Сначала завершите её.")
    params = _validate_start(db, kind, payload.get("params") or {}, user)
    now = _now()
    a = Activity(id=f"act:{uuid4().hex}", conversation_id=conv_id, kind=kind,
                 host_id=user.id, title=str(payload.get("title") or "").strip()[:120],
                 params=params, status="running", started_at=now)
    db.add(a)
    db.commit()
    db.refresh(a)

    live = _initial_payload(kind, params)
    if kind in ("quiz", "contest"):
        #⚠️ Скобки обязательны: `Column == x or ""` Python считает как `(Column == x) or ""`,
        #а `__bool__` у сравнения SQLAlchemy отдаёт False — в фильтр уезжала пустая строка
        #и падало «Textual SQL expression '' should be explicitly declared as text()».
        _qid = params.get("quiz_id") or ""
        live["total_questions"] = (db.query(QuizQuestion)
                                   .filter(QuizQuestion.quiz_id == _qid).count())
    if kind == "poll" and params.get("duration_s"):
        live["ends_at"] = _plus_seconds(now, int(params["duration_s"]))
    if kind == "timer":
        #Время окончания считает СЕРВЕР и присылает один раз; тик отсчитывает клиент.
        #Тикать по сокету каждую секунду всем участникам — самая дорогая реализация самой
        #дешёвой функции, а расхождение в полсекунды у таймера пары ничего не значит.
        live["ends_at"] = _plus_seconds(now, params["duration_s"])
    if kind == "board" and params.get("continue_board_id"):
        src = (db.query(BoardArtifact)
               .filter(BoardArtifact.id == params["continue_board_id"],
                       BoardArtifact.conversation_id == conv_id).first())
        #Чужую доску не подхватываем: артефакт ищется В ЭТОЙ беседе, иначе id из другой
        #группы дал бы её содержимое людям, которые к ней отношения не имеют.
        if src is not None:
            #🔥 Санируем ТОЖЕ (найдено разбором Полковника 18.08.2026). Артефакт мог быть
            #сохранён ДО появления проверки размера, то есть содержать штрихи любого объёма
            #и в любом числе. Без этой строки потолок памяти на доску (20000 x 4 КБ) не
            #действовал вовсе: старую доску достаточно было «продолжить». Хуже того,
            #`board_snapshot` сложил бы эти же несанированные штрихи в НОВЫЙ артефакт —
            #и дофиксовые данные размножались бы вперёд, уже после починки.
            live["strokes"] = _clean_strokes(list(src.strokes or []), limit=MAX_STROKES_TOTAL)
            live["sheet"] = src.sheet or live["sheet"]
    activity_state.start(a.id, kind, live)

    #Отметка в ленте. Тело сообщения — просто id, а объект сервер подмешивает при выдаче
    #(`_attach_activity_meta`), потому что статус активности меняется ПОСЛЕ отправки, а
    #переотправлять сообщение незачем.
    #
    #⚠️ СЛЕД В ЛЕНТЕ ОСТАВЛЯЮТ НЕ ВСЕ КАТЕГОРИИ. Таймер и срез понимания — это оверлеи
    #на несколько минут: они всплывают у всех сами, заходить в них из истории незачем, а
    #отметка о каждом запуске превращала бы ленту в мусор (на паре их бывает несколько).
    #Остаются те, к которым РЕАЛЬНО возвращаются: викторина, соревнование, доска.
    if kind in _LEAVES_TRACE_IN_FEED:
        msg = Message(conversation_id=conv_id, sender_id=user.id, body=a.id,
                      created_at=now, kind=_KIND_OF_FEED_MESSAGE.get(kind, "activity"),
                      body_format="plain")
        db.add(msg)
        db.commit()
        db.refresh(msg)
        a.message_id = msg.id
        db.commit()

    audit.log(db, request, actor=user.login, role=user.role, action="activity.start",
              target=a.id, detail=f"{kind} в {conv_id}")
    _emit(db, conv_id, {"type": "activity.started", "activity_id": a.id,
                        "conversation_id": conv_id, "kind": kind,
                        "host_id": user.id, "title": a.title or ""})
    from .messenger import _broadcast
    _broadcast(db, conv_id)      #в ленте появилось сообщение-карточка
    out = _activity_out(a, user.id)
    out["state"] = activity_state.get(a.id)
    return out


def _plus_seconds(iso: str, secs: int) -> str:
    from datetime import timedelta
    return (datetime.fromisoformat(iso) + timedelta(seconds=secs)).isoformat()


@router.post("/{activity_id}/finish")
def finish_activity(activity_id: str, payload: dict = Body(default={}),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Завершить активность. Для доски `{"save": true}` сохраняет её артефактом в ленту.

    Итог фиксируем ДО снятия состояния из памяти: после `activity_state.drop` считать уже
    нечего, и порядок здесь не косметический."""
    a = _require_host(db, activity_id, user)
    if a.status == "finished":
        return _activity_out(a, user.id)          #повтор — не ошибка (двойное нажатие)
    #`finished_at` в событии обязателен: по нему клиент гасит таймер на карточке в ленте.
    #Метку времени в этом продукте всегда ставит сервер (§4.3), иначе у двух человек в
    #одном чате активность «шла» разное время.
    summary = _finish_activity_row(db, a, save=bool(payload.get("save")))
    out = _activity_out(a, user.id)
    out["summary"] = summary
    return out


def _finalize(db: Session, a: Activity, payload: dict, save: bool) -> dict:
    """Что остаётся от активности после завершения (и что показать в карточке итога)."""
    if a.kind == "board":
        if not save:
            return {"saved": False}
        art = BoardArtifact(id=f"board:{uuid4().hex}", conversation_id=a.conversation_id,
                            activity_id=a.id, author_id=a.host_id,
                            sheet=payload.get("sheet") or "blank",
                            strokes=list(payload.get("strokes") or []),
                            title=a.title or "", created_at=_now())
        db.add(art)
        db.commit()
        msg = Message(conversation_id=a.conversation_id, sender_id=a.host_id, body=art.id,
                      created_at=_now(), kind="board", body_format="plain")
        db.add(msg)
        db.commit()
        return {"saved": True, "board_id": art.id}
    if a.kind == "poll":
        votes = payload.get("votes") or {}
        options = (a.params or {}).get("options") or []
        counts = [0] * len(options)
        for idx in votes.values():
            if isinstance(idx, int) and 0 <= idx < len(counts):
                counts[idx] += 1
        #🔥 СОХРАНЯЕМ ИТОГ В params. Живое состояние после завершения гасится, а опрос —
        #это СООБЩЕНИЕ, которое остаётся в ленте навсегда: без снимка оно показывало бы
        #«проголосовало: 0» и пустые полосы, то есть завершённый опрос выглядел бы так,
        #будто в нём никто не участвовал. Отдельной таблицы ради двух чисел не заводим.
        params = dict(a.params or {})
        params["final_counts"] = counts
        params["final_total"] = len(votes)
        params["final_votes"] = {k: int(v) for k, v in votes.items() if isinstance(v, int)}
        a.params = params
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(a, "params")
        db.commit()
        return {"votes_total": len(votes), "counts": counts}
    if a.kind == "pulse":
        rows = db.query(ActivityFeedback).filter(ActivityFeedback.activity_id == a.id).all()
        scores = [int(r.score_1_10 or 0) for r in rows if r.score_1_10]
        return {"answers": len(rows),
                "average": round(sum(scores) / len(scores), 2) if scores else 0}
    if a.kind in ("quiz", "contest"):
        if a.kind == "contest":
            _persist_contest_results(db, a, payload)
        rows = db.query(ActivityResult).filter(ActivityResult.activity_id == a.id).all()
        return {"participants": len(rows),
                "average": round(sum(float(r.score or 0) for r in rows) / len(rows), 2)
                if rows else 0}
    return {}


# ── Чтение состояния ─────────────────────────────────────────────────────────────────
@router.get("/running")
def running_activities(conversation_id: str = Query(...),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """ВСЕ активности, идущие в беседе. Их теперь может быть несколько разных категорий:
    таймер рядом с доской — нормальная работа на паре, и клиенту нужен весь список, чтобы
    показать полоску таймера независимо от того, какая активность открыта на экране."""
    _require_participant(db, conversation_id, user)
    _sweep_stale(db)
    out = []
    for a in (db.query(Activity)
              .filter(Activity.conversation_id == conversation_id, Activity.status == "running")
              .order_by(Activity.started_at.asc()).all()):
        if activity_state.get(a.id) is None:
            #След рестарта — закрываем, живой её показывать нельзя (она не отвечает).
            a.status = "finished"
            a.finished_at = _now()
            db.commit()
            continue
        cell = _activity_out(a, user.id)
        cell["state"] = _state_for(db, a, user)
        out.append(cell)
    return {"activities": out}


@router.get("/current")
def current_activity(conversation_id: str = Query(...),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Что идёт в беседе сейчас — чтобы войти в уже начатую (человек открыл вкладку позже)."""
    _require_participant(db, conversation_id, user)
    _sweep_stale(db)
    a = _running_in(db, conversation_id)
    if a is None:
        return {"activity": None}
    out = _activity_out(a, user.id)
    out["state"] = _state_for(db, a, user)
    return {"activity": out}


@router.get("/boards")
def list_boards(conversation_id: str = Query(...),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Сохранённые доски беседы — для «продолжить доску» при запуске новой."""
    _require_participant(db, conversation_id, user)
    rows = (db.query(BoardArtifact)
            .filter(BoardArtifact.conversation_id == conversation_id)
            .order_by(BoardArtifact.created_at.desc()).limit(50).all())
    return {"boards": [{"id": b.id, "title": b.title or "", "sheet": b.sheet or "blank",
                        "created_at": b.created_at or "",
                        "strokes_count": len(b.strokes or [])} for b in rows]}


@router.get("/boards/{board_id}")
def get_board(board_id: str, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    b = db.query(BoardArtifact).filter(BoardArtifact.id == board_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="Доска не найдена")
    _require_participant(db, b.conversation_id, user)
    return {"id": b.id, "title": b.title or "", "sheet": b.sheet or "blank",
            "strokes": list(b.strokes or []), "created_at": b.created_at or ""}


def _host_progress(db: Session, a: Activity, payload: dict, answers: dict | None = None) -> None:
    """Шкала прогресса по КАЖДОМУ участнику — для ведущего.

    ⚠️ Отдельной функцией и вызовом ПОСЛЕ цепочки `elif`, а не веткой внутри неё: ветка
    соревнования стоит выше и перехватывала бы её, и преподаватель, запустивший
    соревнование, снова видел бы задание вместо хода. Ровно на это и была жалоба.

    В список идёт ВЕСЬ ростер беседы, а не только закончившие: иначе «пусто» неотличимо
    от «все закончили мгновенно», и преподаватель не понимает, ждать ему или собирать.
    """
    done = db.query(ActivityResult).filter(ActivityResult.activity_id == a.id).all()
    by_user = {r.user_id: r for r in done}
    walk = dict(payload.get("walk") or {})
    #В соревновании прогресс известен серверу и без сообщений клиента: ответы лежат по
    #вопросам. Считаем оттуда, иначе шкала у ведущего стояла бы на нуле всю игру.
    if a.kind == "contest":
        #⚠️ Ответы приходят ПАРАМЕТРОМ: ветка соревнования выше по коду выбрасывает их из
        #состояния (`payload.pop("answers")`), чтобы не отдать чужие ответы клиенту, и
        #`payload.get("answers")` здесь уже пуст — шкала стояла бы на нуле всю игру.
        for slot in (answers or {}).values():
            for uid in (slot or {}):
                walk[uid] = int(walk.get(uid) or 0) + 1
    roster = [uid for uid in _participants_of(db, a.conversation_id) if uid != a.host_id]
    names = {}
    if roster:
        names = {u.id: (u.full_name or u.name or u.login or u.id)
                 for u in db.query(User).filter(User.id.in_(roster)).all()}
    total_q = int(payload.get("total_questions") or 0)
    rows = []
    for uid in roster:
        r = by_user.get(uid)
        rows.append({
            "user_id": uid, "name": names.get(uid, uid),
            #Сколько заданий человек прошёл. Закончил — засчитываем все.
            "walked": int(r.total_count or 0) if r is not None else int(walk.get(uid) or 0),
            "done": r is not None,
            "correct_count": int(r.correct_count or 0) if r is not None else 0,
            "total_count": int(r.total_count or 0) if r is not None else total_q,
            "score": float(r.score or 0) if r is not None else 0.0,
        })
    rows.sort(key=lambda x: (-x["walked"], x["name"]))
    payload["progress"] = rows
    payload["participants"] = len(roster)
    payload["total_questions"] = total_q


def _state_for(db: Session, a: Activity, user: User) -> dict | None:
    """Живое состояние ДЛЯ ЭТОГО зрителя.

    🔒 Обрезка по роли делается здесь, в одном месте, а не в каждом эндпоинте: голоса
    опроса и ответы соревнования — это чужие данные, и «забыть обрезать» в одном из пяти
    мест означало бы отдать их целиком. Хосту тоже отдаём не всё: распределение опроса он
    получает отдельным эндпоинтом, а карту «кто как проголосовал» не получает никто."""
    snap = activity_state.get(a.id)
    if snap is None:
        return None
    payload = dict(snap.get("payload") or {})
    is_host = a.host_id == user.id or user.role == "admin"
    if a.kind == "poll":
        votes = payload.pop("votes", {}) or {}
        payload["voted_count"] = len(votes)
        payload["my_choice"] = votes.get(user.id, None)
        #Студенту показываем «проголосовало N из M»: число ничего не раскрывает и снимает
        #вопрос «дошло ли вообще». Само распределение — только создателю, и не здесь.
        payload["participants"] = len(_participants_of(db, a.conversation_id))
    elif a.kind == "contest":
        contest_answers = payload.pop("answers", {}) or {}
        answers = contest_answers
        scores = payload.pop("scores", {}) or {}
        payload["my_score"] = float(scores.get(user.id, 0) or 0)
        cur = str(payload.get("question_index", -1))
        payload["answered"] = user.id in (answers.get(cur) or {})
        payload["answered_count"] = len(answers.get(cur) or {})
        #Табло видят ВСЕ, как в Kahoot: соревнование ради него и затевается, а места
        #в конце всё равно объявляются вслух. Скрывать промежуточные баллы значило бы
        #убрать из игры единственное, что заставляет тянуться к следующему вопросу.
        payload["board"] = _contest_board(db, scores)
    elif a.kind == "pulse":
        answered = payload.pop("answered", []) or []
        payload["answered_count"] = len(answered)
        payload["mine_done"] = user.id in answered
        #Сколько ВСЕГО человек в беседе. Без этого у преподавателя стояло «ответили 1 из
        #0» — доля не считалась, и понять, можно ли делать выводы, было нельзя.
        payload["participants"] = len([uid for uid in _participants_of(db, a.conversation_id)
                                       if uid != a.host_id])
    if a.kind in ("quiz", "contest") and is_host:
        _host_progress(db, a, payload, locals().get("contest_answers"))
    payload.pop("walk", None)
    return {"seq": snap["seq"], "payload": payload}


def _participants_of(db: Session, conv_id: str) -> list:
    return [p.user_id for p in db.query(ConversationParticipant)
            .filter(ConversationParticipant.conversation_id == conv_id).all()]


# ⚠️ `GET /{activity_id}` — ловушка порядка маршрутов, поэтому он объявлен В САМОМ КОНЦЕ
# файла, а не здесь. FastAPI сопоставляет маршруты В ПОРЯДКЕ РЕГИСТРАЦИИ, и параметр пути
# без ограничений съедает любой односегментный GET, объявленный ПОЗЖЕ: `/quizzes` и
# `/journal` уходили бы в него и отвечали «Активность не найдена» — при исправном коде и
# зелёных тестах на сами эти эндпоинты. Держит `test_single_segment_routes_are_not_shadowed`.


# ── Тайм-бокс ────────────────────────────────────────────────────────────────────────
@router.post("/{activity_id}/timer")
def control_timer(activity_id: str, payload: dict = Body(...),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Остановить / продолжить / продлить тайм-бокс. Только ведущий."""
    a = _require_running(_require_host(db, activity_id, user))
    if a.kind != "timer":
        raise HTTPException(status_code=400, detail="Это не тайм-бокс")
    snap = activity_state.get(a.id)
    if snap is None:
        raise HTTPException(status_code=400, detail="Активность уже завершена")
    cur = snap["payload"]
    action = str(payload.get("action") or "")
    if action == "pause":
        left = _seconds_left(cur.get("ends_at") or "")
        changes = {"paused": True, "left_s": left}
    elif action == "resume":
        changes = {"paused": False,
                   "ends_at": _plus_seconds(_now(), int(cur.get("left_s") or 0))}
    elif action == "extend":
        add = max(1, min(int(payload.get("seconds") or 60), 3600))
        base = _now() if cur.get("paused") else (cur.get("ends_at") or _now())
        changes = ({"left_s": int(cur.get("left_s") or 0) + add} if cur.get("paused")
                   else {"ends_at": _plus_seconds(base, add)})
    else:
        raise HTTPException(status_code=400, detail="Неизвестное действие")
    #Тот же None, что и у голосования, но исход был хуже: `_emit_state` на None молча
    #выходит, и ведущий получал 200 `{"ok": true, "state": null}` — «Продлить»
    #отвечало успехом, ничего не продлив. Тихий ложный успех опаснее пятисотки.
    snap = _require_state(activity_state.patch(a.id, changes))
    _emit_state(db, a, snap)
    return {"ok": True, "state": snap}


def _seconds_left(ends_at: str) -> int:
    if not ends_at:
        return 0
    try:
        delta = datetime.fromisoformat(ends_at) - datetime.now(timezone.utc)
    except ValueError:
        return 0
    return max(0, int(delta.total_seconds()))


# ── Опрос ────────────────────────────────────────────────────────────────────────────
@router.post("/{activity_id}/vote")
def vote(activity_id: str, payload: dict = Body(...),
         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Проголосовать. Повторный голос ЗАМЕНЯЕТ прежний — человек имеет право передумать
    и, что важнее, исправить промах пальцем по соседнему варианту."""
    _throttle("answer", user)
    a = _require_running(_require_activity_participant(db, activity_id, user))
    if a.kind != "poll":
        raise HTTPException(status_code=400, detail="Это не опрос")
    options = (a.params or {}).get("options") or []
    choice = payload.get("choice")
    if not isinstance(choice, int) or not (0 <= choice < len(options)):
        raise HTTPException(status_code=400, detail="Нет такого варианта")
    snap = activity_state.get(a.id)
    if snap is None:
        raise HTTPException(status_code=400, detail="Опрос уже завершён")
    votes = dict(snap["payload"].get("votes") or {})
    votes[user.id] = choice
    snap = _require_state(activity_state.patch(a.id, {"votes": votes}))
    #Итог участника пишем и в БД: журнал беседы (§9) обязан пережить перезапуск, а один
    #голос — это одна запись на человека, а не поток (профиль нагрузки тот же, что у
    #`submit` викторины, который план прямо разрешает).
    _upsert_result(db, a.id, user.id, score=0.0, correct=0, total=0,
                   answers={"choice": choice})
    #Распределение считаем ЗДЕСЬ: оно нужно и рассылке ниже, и ответу на сам запрос.
    tally = [sum(1 for v in votes.values() if int(v) == i) for i in range(len(options))]
    public = bool((a.params or {}).get("public_votes"))
    #Наружу уходит СЧЁТЧИК, не карта голосов: распределение видит только создатель.
    _emit(db, a.conversation_id,
          {"type": "activity.state", "activity_id": a.id,
           "conversation_id": a.conversation_id, "seq": snap["seq"],
           "payload": {"voted_count": len(votes),
                       #Открытые голоса — распределение всем сразу; иначе оно уходит
                       #ОТДЕЛЬНЫМ кадром только автору (ниже): в общей рассылке ему
                       #места нет, там его прочитали бы все.
                       **({"tally": tally} if public else {})}})
    if not public and a.host_id:
        _emit_to([a.host_id],
                 {"type": "activity.state", "activity_id": a.id,
                  "conversation_id": a.conversation_id, "seq": snap["seq"],
                  "payload": {"voted_count": len(votes), "tally": tally}})
    #Автору опроса (и всем, если он включил открытые голоса) сразу возвращаем
    #распределение: иначе после своего клика он видел бы только «проголосовало N»,
    #а ради распределения ему пришлось бы перезаходить в беседу.
    out = {"ok": True, "my_choice": choice, "voted_count": len(votes)}
    if public or user.id == a.host_id:
        out["tally"] = tally
    return out


@router.get("/{activity_id}/poll-results")
def poll_results(activity_id: str, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Распределение голосов — ТОЛЬКО создателю опроса (и админу).

    Не «пока идёт»: и после завершения тоже. Опрос обещает студенту, что распределение
    видит только преподаватель, и обещание не может истекать по времени."""
    a = _require_activity_participant(db, activity_id, user)
    if a.kind != "poll":
        raise HTTPException(status_code=400, detail="Это не опрос")
    if a.host_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Результаты опроса видит только его создатель")
    options = (a.params or {}).get("options") or []
    counts = [0] * len(options)
    rows = db.query(ActivityResult).filter(ActivityResult.activity_id == activity_id).all()
    for r in rows:
        idx = (r.answers or {}).get("choice")
        if isinstance(idx, int) and 0 <= idx < len(counts):
            counts[idx] += 1
    total = sum(counts)
    return {"question": (a.params or {}).get("question", ""), "options": options,
            "counts": counts, "total": total,
            "participants": len(_participants_of(db, a.conversation_id))}


# ── Срез понимания ───────────────────────────────────────────────────────────────────
@router.post("/{activity_id}/feedback")
def send_feedback(activity_id: str, payload: dict = Body(...),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Отправить срез понимания. Повторная отправка заменяет прежнюю."""
    _throttle("answer", user)
    a = _require_running(_require_activity_participant(db, activity_id, user))
    if a.kind != "pulse":
        raise HTTPException(status_code=400, detail="Это не срез понимания")
    score = int(payload.get("score") or 0)
    if not (1 <= score <= 10):
        raise HTTPException(status_code=400, detail="Оценка понимания — от 1 до 10")
    row = (db.query(ActivityFeedback)
           .filter(ActivityFeedback.activity_id == activity_id,
                   ActivityFeedback.user_id == user.id).first())
    if row is None:
        row = ActivityFeedback(activity_id=activity_id, user_id=user.id)
        db.add(row)
    row.score_1_10 = score
    row.reason_code = str(payload.get("reason_code") or "")[:64]
    #Свободный текст — от человека, значит цензурим тем же фильтром, что и сообщения:
    #отзыв попадает на экран преподавателю, и «анонимность» не повод для брани.
    import profanity_filter
    text = str(payload.get("text") or "").strip()[:MAX_TEXT]
    row.custom_text = profanity_filter.censor(text, mask=profanity_filter.MESSENGER_SAFE_MASK)
    row.created_at = _now()
    db.commit()
    snap = activity_state.get(a.id)
    if snap is not None:
        answered = list(snap["payload"].get("answered") or [])
        if user.id not in answered:
            answered.append(user.id)
        snap = _require_state(activity_state.patch(a.id, {"answered": answered}))
        #Наружу — только СЧЁТЧИК. Список ответивших сам по себе выдал бы автора отзыва
        #тому, кто следит за экраном: «ответил Петров — значит вот эта строка его».
        _emit(db, a.conversation_id,
              {"type": "activity.state", "activity_id": a.id,
               "conversation_id": a.conversation_id, "seq": snap["seq"],
               "payload": {"answered_count": len(answered)}})
    return {"ok": True}


@router.get("/{activity_id}/feedback")
def feedback_summary(activity_id: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Сводка среза — ведущему. БЕЗ авторов (см. `_feedback_out`)."""
    a = _require_host(db, activity_id, user)
    if a.kind != "pulse":
        raise HTTPException(status_code=400, detail="Это не срез понимания")
    rows = (db.query(ActivityFeedback)
            .filter(ActivityFeedback.activity_id == activity_id)
            .order_by(ActivityFeedback.id.asc()).all())
    scores = [int(r.score_1_10 or 0) for r in rows if r.score_1_10]
    by_reason: dict[str, int] = {}
    for r in rows:
        if r.reason_code:
            by_reason[r.reason_code] = by_reason.get(r.reason_code, 0) + 1
    return {"answers": len(rows),
            "average": round(sum(scores) / len(scores), 2) if scores else 0,
            "histogram": [sum(1 for s in scores if s == n) for n in range(1, 11)],
            "by_reason": by_reason,
            "items": [_feedback_out(r) for r in rows],
            "participants": len(_participants_of(db, a.conversation_id))}


@router.post("/feedback/{feedback_id}/report")
def report_feedback(feedback_id: int, payload: dict = Body(...), request: Request = None,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Пожаловаться АДМИНУ на отзыв среза понимания.

    Тикет ложится в ту же очередь `MessageReport`, где разбирается всё остальное, — с
    `target_kind="activity_feedback"`. Админ видит и автора, и текст и решает сам, что с
    этим делать; преподаватель автора не узнаёт ни на каком шаге, включая этот."""
    row = db.query(ActivityFeedback).filter(ActivityFeedback.id == feedback_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Отзыв не найден")
    a = _require_host(db, row.activity_id, user)
    rep = MessageReport(message_id=row.id, target_kind="activity_feedback",
                        conversation_id=a.conversation_id,
                        message_snapshot=row.custom_text or "",
                        reporter_id=user.id, reported_user_id=row.user_id,
                        reason_code=str(payload.get("reason_code") or "other")[:32],
                        description=str(payload.get("description") or "")[:MAX_TEXT],
                        created_at=_now(), status="open")
    db.add(rep)
    db.commit()
    db.refresh(rep)
    audit.log(db, request, actor=user.login, role=user.role,
              action="activity.feedback.report", target=str(row.id))
    return {"ok": True, "report_id": rep.id}


# ── Библиотека викторин ──────────────────────────────────────────────────────────────
def _require_teacher(user: User) -> None:
    if user.role not in ("teacher", "admin"):
        raise HTTPException(status_code=403, detail="Библиотека викторин — для преподавателей")


def _require_quiz_readable(quiz: QuizSet, user: User) -> None:
    if user.role == "admin":
        return
    if quiz.author_id == user.id:
        return
    if (quiz.visibility or "private") in ("college", "stock") and user.role == "teacher":
        return
    raise HTTPException(status_code=403, detail="Эта викторина недоступна")


def _norm_tag(t: str) -> str:
    """Нормализация тега: регистр и пробелы.

    Закрытый справочник кто-то один раз заполнит неудачно, а полностью свободные теги
    разъедутся на «матем» / «математика» / «Математика» — и поиск по тегу перестанет
    находить половину. Нормализация — середина: вводить можно что угодно, но одинаковое
    по смыслу становится одинаковым по строке."""
    return re.sub(r"\s+", " ", str(t or "")).strip().lower()[:40]


def _clean_tags(tags) -> list:
    out, seen = [], set()
    for t in (tags or []):
        n = _norm_tag(t)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out[:MAX_TAGS]


@router.get("/quizzes")
def list_quizzes(q: str = Query(default=""), tag: str = Query(default=""),
                 scope: str = Query(default="mine"), kind: str = Query(default=""),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Библиотека: `scope` = mine | college | stock, `kind` = quiz | contest.

    ⚠️ Библиотеки РАЗДЕЛЬНЫЕ. У категорий разные допустимые типы заданий (в соревновании
    только выбор — там нужен бесспорный балл) и разный смысл, а общий список заставлял
    преподавателя гадать, что откуда запускается. Без `kind` отдаём всё — так ведут себя
    старые сборки клиента, и ломать их незачем."""
    _require_teacher(user)
    query = db.query(QuizSet).filter(QuizSet.deleted == False)          # noqa: E712
    if kind in ("quiz", "contest"):
        #Наборы, заведённые до появления поля, считаем обычными викторинами.
        query = query.filter(QuizSet.kind == kind) if kind == "contest" else query.filter(
            or_(QuizSet.kind == "quiz", QuizSet.kind.is_(None), QuizSet.kind == ""))
    if scope == "mine":
        query = query.filter(QuizSet.author_id == user.id)
    elif scope == "stock":
        query = query.filter(QuizSet.visibility == "stock")
    else:
        query = query.filter(QuizSet.visibility.in_(("college", "stock")))
    rows = query.order_by(QuizSet.updated_at.desc()).limit(500).all()
    needle = _norm_tag(q)
    if needle:
        #⚠️ Поиск по подстроке — В PYTHON, а не через LIKE: SQLite без ICU не умеет
        #регистронезависимый LIKE для кириллицы (тот же приём уже дважды применён —
        #каталог мессенджера и поиск по чату; третьей копии правила не заводим).
        rows = [r for r in rows
                if needle in (r.title or "").lower()
                or needle in (r.description or "").lower()
                or any(needle in t for t in (r.tags or []))]
    tag_needle = _norm_tag(tag)
    if tag_needle:
        rows = [r for r in rows if tag_needle in (r.tags or [])]
    counts = _question_counts(db, [r.id for r in rows])
    return {"quizzes": [_quiz_out(r, counts.get(r.id, 0)) for r in rows],
            "tags": sorted({t for r in rows for t in (r.tags or [])})}


def _question_counts(db: Session, quiz_ids: list) -> dict:
    if not quiz_ids:
        return {}
    from sqlalchemy import func
    rows = (db.query(QuizQuestion.quiz_id, func.count(QuizQuestion.id))
            .filter(QuizQuestion.quiz_id.in_(quiz_ids))
            .group_by(QuizQuestion.quiz_id).all())
    return {qid: cnt for qid, cnt in rows}


@router.get("/quizzes/{quiz_id}")
def get_quiz(quiz_id: str, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Викторина С КЛЮЧОМ — для конструктора. Только преподавателю, которому она доступна."""
    _require_teacher(user)
    quiz = db.query(QuizSet).filter(QuizSet.id == quiz_id, QuizSet.deleted == False).first()  # noqa: E712
    if quiz is None:
        raise HTTPException(status_code=404, detail="Викторина не найдена")
    _require_quiz_readable(quiz, user)
    questions = (db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id)
                 .order_by(QuizQuestion.order_no.asc()).all())
    opts = _options_by_question(db, [x.id for x in questions])
    out = _quiz_out(quiz, len(questions))
    out["questions"] = [_question_out(x, opts.get(x.id, []), with_key=True) for x in questions]
    out["can_edit"] = (quiz.author_id == user.id)
    return out


def _options_by_question(db: Session, qids: list) -> dict:
    if not qids:
        return {}
    rows = (db.query(QuizOption).filter(QuizOption.question_id.in_(qids))
            .order_by(QuizOption.order_no.asc()).all())
    out: dict[str, list] = {}
    for o in rows:
        out.setdefault(o.question_id, []).append(o)
    return out


@router.get("/quizzes/{quiz_id}/similar")
def similar_quizzes(quiz_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Похожие по тегам + цепочка копий.

    ⚠️ Цепочка отдаётся, ТОЛЬКО если в ней больше одного автора: ветки внутри одного
    преподавателя — это его кухня (черновик, вариант для другой группы), а не история
    заимствований, и показывать её всем незачем. Сворачивание делает СЕРВЕР, а не UI —
    иначе «скрытая» цепочка всё равно уезжала бы клиенту и читалась в ответе."""
    _require_teacher(user)
    quiz = db.query(QuizSet).filter(QuizSet.id == quiz_id).first()
    if quiz is None:
        raise HTTPException(status_code=404, detail="Викторина не найдена")
    _require_quiz_readable(quiz, user)
    tags = set(quiz.tags or [])
    pool = (db.query(QuizSet)
            .filter(QuizSet.deleted == False, QuizSet.id != quiz_id,          # noqa: E712
                    QuizSet.visibility.in_(("college", "stock"))).limit(500).all())
    scored = []
    for r in pool:
        common = tags & set(r.tags or [])
        if common:
            scored.append((len(common), r))
    scored.sort(key=lambda p: -p[0])
    chain = _copy_chain(db, quiz)
    authors = {c.author_id for c in chain}
    return {"similar": [_quiz_out(r) for _, r in scored[:10]],
            "chain": [_quiz_out(c) for c in chain] if len(authors) > 1 else []}


def _copy_chain(db: Session, quiz: QuizSet) -> list:
    """Цепочка от корня до этой викторины (оригинал → копия → копия)."""
    chain, seen, cur = [], set(), quiz
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        chain.append(cur)
        if not cur.parent_id:
            break
        cur = db.query(QuizSet).filter(QuizSet.id == cur.parent_id).first()
    chain.reverse()
    return chain


def _save_questions(db: Session, quiz_id: str, questions: list) -> None:
    """Переписать вопросы викторины целиком (конструктор присылает готовый набор)."""
    old = db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id).all()
    old_ids = [x.id for x in old]
    if old_ids:
        db.query(QuizOption).filter(QuizOption.question_id.in_(old_ids)).delete(
            synchronize_session=False)
        db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id).delete(
            synchronize_session=False)
    for i, q in enumerate((questions or [])[:MAX_QUESTIONS]):
        qtype = str(q.get("type") or "single")
        if qtype not in QUESTION_TYPES:
            qtype = "single"
        qid = f"qq:{uuid4().hex}"
        db.add(QuizQuestion(id=qid, quiz_id=quiz_id, order_no=i, type=qtype,
                            text=str(q.get("text") or "").strip()[:MAX_TEXT],
                            points=max(1, min(int(q.get("points") or 1), 100))))
        for j, o in enumerate((q.get("options") or [])[:MAX_OPTIONS]):
            db.add(QuizOption(id=f"qo:{uuid4().hex}", question_id=qid, order_no=j,
                              text=str(o.get("text") or "").strip()[:MAX_TEXT],
                              is_correct=bool(o.get("is_correct")),
                              match_key=str(o.get("match_key") or "")[:MAX_TEXT],
                              correct_position=int(o.get("correct_position") or 0)))
    db.commit()


@router.post("/quizzes")
def create_quiz(payload: dict = Body(...), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _require_teacher(user)
    title = str(payload.get("title") or "").strip()[:120]
    if not title:
        raise HTTPException(status_code=400, detail="Нужно название викторины")
    vis = str(payload.get("visibility") or "private")
    if vis not in VISIBILITY:
        vis = "private"
    if vis == "stock" and user.role != "admin":
        #«Стартовые» — общий фонд продукта, а не чья-то личная полка: их состав определяет
        #администратор, иначе первый же преподаватель наполнил бы витрину своими черновиками.
        vis = "college"
    now = _now()
    quiz = QuizSet(id=f"quiz:{uuid4().hex}", author_id=user.id, title=title,
                   description=str(payload.get("description") or "").strip()[:MAX_TEXT],
                   tags=_clean_tags(payload.get("tags")),
            time_limit_s=max(0, min(int(payload.get("time_limit_s") or 0), 6 * 60 * 60)),
            kind=("contest" if str(payload.get("kind") or "") == "contest" else "quiz"), visibility=vis,
                   created_at=now, updated_at=now)
    db.add(quiz)
    db.commit()
    _save_questions(db, quiz.id, payload.get("questions") or [])
    return _quiz_out(quiz, len(payload.get("questions") or []))


@router.put("/quizzes/{quiz_id}")
def update_quiz(quiz_id: str, payload: dict = Body(...),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Править можно ТОЛЬКО свою. Чужую — копировать себе (`/copy`), иначе один
    преподаватель менял бы викторину под ногами у другого, который её прямо сейчас ведёт."""
    _require_teacher(user)
    quiz = db.query(QuizSet).filter(QuizSet.id == quiz_id, QuizSet.deleted == False).first()  # noqa: E712
    if quiz is None:
        raise HTTPException(status_code=404, detail="Викторина не найдена")
    if quiz.author_id != user.id:
        raise HTTPException(status_code=403,
                            detail="Это чужая викторина — скопируйте её себе, чтобы править")
    if "title" in payload:
        quiz.title = str(payload.get("title") or "").strip()[:120] or quiz.title
    if "description" in payload:
        quiz.description = str(payload.get("description") or "").strip()[:MAX_TEXT]
    if "tags" in payload:
        quiz.tags = _clean_tags(payload.get("tags"))
    if "kind" in payload:
        #Перенос набора между библиотеками. Нужен потому, что наборы, созданные ДО
        #разделения, все помечены как обычные викторины: без переноса преподаватель
        #увидел бы в соревновании пустой список и не понял, куда делись его тесты.
        #⚠️ В соревнование переносим только если ВСЕ задания ему подходят (там нужен
        #бесспорный балл) — иначе набор попал бы в библиотеку, из которой не запускается.
        want = "contest" if str(payload.get("kind") or "") == "contest" else "quiz"
        if want == "contest":
            bad = [q for q in db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz.id).all()
                   if (q.type or "single") not in CONTEST_TYPES]
            if bad:
                raise HTTPException(
                    status_code=400,
                    detail="В соревновании бывают только задания с выбором ответа. "
                           "Уберите задания на порядок и сопоставление.")
        quiz.kind = want
    if "time_limit_s" in payload:
        #Ограничение на весь тест. 0 — без ограничения (так было всегда). Потолок шесть
        #часов — тот же, что у тайм-бокса: пары длиннее не бывает, а опечатка в поле
        #иначе завела бы счётчик на годы.
        quiz.time_limit_s = max(0, min(int(payload.get("time_limit_s") or 0), 6 * 60 * 60))
    if "visibility" in payload:
        vis = str(payload.get("visibility") or "private")
        if vis in VISIBILITY and not (vis == "stock" and user.role != "admin"):
            quiz.visibility = vis
    quiz.updated_at = _now()
    db.commit()
    if "questions" in payload:
        _save_questions(db, quiz_id, payload.get("questions") or [])
    count = _question_counts(db, [quiz_id]).get(quiz_id, 0)
    return _quiz_out(quiz, count)


@router.post("/quizzes/{quiz_id}/copy")
def copy_quiz(quiz_id: str, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Копия себе. `parent_id` = оригинал — так строится цепочка происхождения."""
    _require_teacher(user)
    src = db.query(QuizSet).filter(QuizSet.id == quiz_id, QuizSet.deleted == False).first()  # noqa: E712
    if src is None:
        raise HTTPException(status_code=404, detail="Викторина не найдена")
    _require_quiz_readable(src, user)
    now = _now()
    dst = QuizSet(id=f"quiz:{uuid4().hex}", author_id=user.id, title=(src.title or "")[:120],
                  description=src.description or "", tags=list(src.tags or []),
                  visibility="private", parent_id=src.id, created_at=now, updated_at=now)
    db.add(dst)
    db.commit()
    questions = (db.query(QuizQuestion).filter(QuizQuestion.quiz_id == src.id)
                 .order_by(QuizQuestion.order_no.asc()).all())
    opts = _options_by_question(db, [x.id for x in questions])
    for q in questions:
        qid = f"qq:{uuid4().hex}"
        db.add(QuizQuestion(id=qid, quiz_id=dst.id, order_no=q.order_no, type=q.type,
                            text=q.text, points=q.points))
        for o in opts.get(q.id, []):
            db.add(QuizOption(id=f"qo:{uuid4().hex}", question_id=qid, order_no=o.order_no,
                              text=o.text, is_correct=o.is_correct, match_key=o.match_key,
                              correct_position=o.correct_position))
    db.commit()
    return _quiz_out(dst, len(questions))


@router.delete("/quizzes/{quiz_id}")
def delete_quiz(quiz_id: str, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Мягкое удаление: на викторину ссылаются уже проведённые активности, и физическое
    удаление осиротило бы их разбор."""
    _require_teacher(user)
    quiz = db.query(QuizSet).filter(QuizSet.id == quiz_id).first()
    if quiz is None:
        raise HTTPException(status_code=404, detail="Викторина не найдена")
    if quiz.author_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Удалить может только автор")
    quiz.deleted = True
    quiz.updated_at = _now()
    db.commit()
    return {"ok": True}


# ── Прохождение ──────────────────────────────────────────────────────────────────────
@router.get("/{activity_id}/questions")
def activity_questions(activity_id: str, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Вопросы активности — БЕЗ КЛЮЧА для всех, включая ведущего.

    Ведущему ключ здесь тоже не нужен: он смотрит свою викторину в конструкторе
    (`GET /quizzes/{id}`), а этот эндпоинт открыт КАЖДОМУ участнику беседы — то есть
    любая ветка «а хосту отдадим с ключом» превращается в вопрос «а точно ли этот
    участник хост» ровно там, где ошибиться дороже всего."""
    a = _require_activity_participant(db, activity_id, user)
    if a.kind not in ("quiz", "contest"):
        raise HTTPException(status_code=400, detail="У этой активности нет вопросов")
    quiz_id = (a.params or {}).get("quiz_id") or ""
    questions = (db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id)
                 .order_by(QuizQuestion.order_no.asc()).all())
    opts = _options_by_question(db, [x.id for x in questions])
    if a.kind == "contest":
        #В соревновании вопрос показывается ПО КОМАНДЕ хоста: отдать все сразу значит
        #отдать и те, что ещё не задавали, — и человек ответит на них заранее.
        snap = activity_state.get(a.id) or {"payload": {}}
        idx = int(snap["payload"].get("question_index", -1))
        if idx < 0 or idx >= len(questions):
            return {"questions": [], "index": idx, "total": len(questions)}
        q = questions[idx]
        return {"questions": [_question_out(q, opts.get(q.id, []), False, user.id)],
                "index": idx, "total": len(questions),
                "limit_ms": int((a.params or {}).get("limit_ms") or 30000)}
    quiz = db.query(QuizSet).filter(QuizSet.id == quiz_id).first()
    return {"questions": [_question_out(x, opts.get(x.id, []), False, user.id)
                          for x in questions],
            "index": 0, "total": len(questions),
            #Счётчик идёт от ОТКРЫТИЯ теста, а не от старта активности: человек мог зайти
            #в беседу через десять минут после запуска, и общий отсчёт съел бы его время.
            "time_limit_s": int((quiz.time_limit_s or 0) if quiz else 0)}


def _upsert_result(db: Session, activity_id: str, user_id: str, score: float,
                   correct: int, total: int, answers: dict, duration_ms: int = 0) -> ActivityResult:
    """Итог участника — ОДНА строка на (активность, человек).

    Повторная отправка заменяет, а не добавляет: дрогнувшая сеть и второе нажатие не
    должны сдваивать результат (§5.3 плана)."""
    row = (db.query(ActivityResult)
           .filter(ActivityResult.activity_id == activity_id,
                   ActivityResult.user_id == user_id).first())
    if row is None:
        row = ActivityResult(activity_id=activity_id, user_id=user_id)
        db.add(row)
    row.score = float(score)
    row.correct_count = int(correct)
    row.total_count = int(total)
    row.answers = answers or {}
    row.finished_at = _now()
    row.duration_ms = int(duration_ms or 0)
    db.commit()
    return row


def _quiz_questions(db: Session, a):
    """Вопросы викторины активности в порядке показа."""
    quiz_id = (a.params or {}).get("quiz_id") or ""
    return (db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id)
            .order_by(QuizQuestion.order_no.asc()).all())


def _review(questions, opts, answers: dict, per_question: list) -> dict:
    """Разбор для экрана итогов: что спрашивали, что человек выбрал, что было верно.

    🔒 Отдавать ключ здесь БЕЗОПАСНО ровно потому, что пересдать нельзя: результат
    считается ОДИН раз, повторная отправка возвращает сохранённый (см. докстринг
    `submit_quiz` про оракул). Убери ту идемпотентность — и этот разбор мгновенно
    станет способом узнать ответы и отправить заново.
    """
    ok_by_q = {r.get("question_id"): r for r in (per_question or [])}
    out = []
    for q in questions:
        chosen = answers.get(q.id)
        chosen_ids = ([chosen] if isinstance(chosen, str)
                      else list(chosen) if isinstance(chosen, list)
                      else list(chosen.values()) if isinstance(chosen, dict) else [])
        out.append({
            "id": q.id, "text": q.text, "type": q.type,
            "points": float(q.points or 1),
            "correct": bool((ok_by_q.get(q.id) or {}).get("correct")),
            "earned": float((ok_by_q.get(q.id) or {}).get("points") or 0),
            "options": [{"id": o.id, "text": o.text,
                         "is_correct": bool(o.is_correct),
                         "chosen": o.id in chosen_ids}
                        for o in (opts.get(q.id) or [])],
        })
    return {"review": out, "max_score": round(sum(float(q.points or 1) for q in questions), 2)}


@router.post("/{activity_id}/progress")
def report_progress(activity_id: str, payload: dict = Body(...),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Сколько заданий человек уже прошёл — для шкалы у ведущего.

    ⚠️ Это НЕ отход от «одного запроса на участника». Тяжёлым был не запрос, а ПРОВЕРКА:
    grading, запись результата, коммит. Здесь только число в памяти процесса и рассылка
    ведущему — ни одной записи в базу. Без этого шкала прогресса невозможна в принципе:
    асинхронная викторина отправляет ответы одним запросом В КОНЦЕ, и до него сервер не
    знает о человеке ничего, кроме того, что он открыл вопросы.
    """
    _throttle("progress", user)
    a = _require_running(_require_activity_participant(db, activity_id, user))
    if a.kind not in ("quiz", "contest"):
        raise HTTPException(status_code=400, detail="У этой активности нет заданий")
    snap = activity_state.get(a.id)
    if snap is None:
        raise HTTPException(status_code=400, detail="Активность уже завершена")
    done = max(0, int(payload.get("answered") or 0))
    walk = dict(snap["payload"].get("walk") or {})
    #Назад не двигаем: человек мог вернуться к предыдущему вопросу, но пройденного это
    #не отменяет, а прыгающая назад шкала у ведущего читается как сбой.
    walk[user.id] = max(done, int(walk.get(user.id) or 0))
    snap = _require_state(activity_state.patch(a.id, {"walk": walk}))
    _emit(db, a.conversation_id,
          {"type": "activity.state", "activity_id": a.id,
           "conversation_id": a.conversation_id, "seq": snap["seq"],
           "payload": {"walk_bump": user.id}})
    _emit_host_progress(db, a)
    return {"ok": True}


@router.post("/{activity_id}/submit")
def submit_quiz(activity_id: str, payload: dict = Body(...),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Отправить ВСЕ ответы асинхронной викторины одним запросом.

    Один запрос на участника вместо одного на вопрос: 30 вместо 600 на группу в 30
    человек. И сеть, моргнувшая посреди прохождения, ничего не теряет — человек шёл
    локально, отправка одна.

    🔒 ПРОВЕРЯЕТСЯ ОДИН РАЗ. Повторная отправка возвращает УЖЕ СОХРАНЁННЫЙ результат и
    ничего не пересчитывает. Это не про дубли строк (их не было и раньше) — это про
    оракул: ответ содержит `per_question` с картой «где верно, где нет», и если пускать
    вторую отправку, любой студент отправляет пустой набор, читает из ответа список
    неверных, исправляет их и отправляет снова. Для вопросов с четырьмя вариантами это
    ≤4 запроса до ста баллов, причём инструменты разработчика не нужны вовсе — оракул
    отдаёт штатный API, а разбор ошибок печатает сам интерфейс. Ключ на сервере при этом
    формально не покидал его ни разу.
    ⚠️ Ровно поэтому в соревновании повтор отвергается 409 (`contest_answer`), а здесь —
    идемпотентный возврат: сетевой ретрай шлёт ТЕ ЖЕ ответы и обязан получить свой
    результат, а не ошибку. Найдено разбором Полковника, 15.08.2026.
    """
    _throttle("answer", user)
    a = _require_running(_require_activity_participant(db, activity_id, user))
    if a.kind != "quiz":
        raise HTTPException(status_code=400,
                            detail="Соревнование отвечает по одному вопросу, а не пачкой")
    done = (db.query(ActivityResult)
            .filter(ActivityResult.activity_id == a.id,
                    ActivityResult.user_id == user.id).first())
    if done is not None:
        return {"score": float(done.score or 0), "correct_count": int(done.correct_count or 0),
                "total_count": int(done.total_count or 0),
                "per_question": (done.answers or {}).get("_per_question", []),
                "already_submitted": True,
                **_review(_quiz_questions(db, a), _options_by_question(
                    db, [x.id for x in _quiz_questions(db, a)]),
                    {k: v for k, v in (done.answers or {}).items() if k != "_per_question"},
                    (done.answers or {}).get("_per_question", []))}
    quiz_id = (a.params or {}).get("quiz_id") or ""
    questions = (db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id)
                 .order_by(QuizQuestion.order_no.asc()).all())
    opts = _options_by_question(db, [x.id for x in questions])
    answers = payload.get("answers") or {}
    if not isinstance(answers, dict):
        raise HTTPException(status_code=400, detail="Ответы должны быть словарём")
    graded = activity_grading.grade(questions, opts, answers)
    #Разбор храним вместе с ответами: повторный запрос обязан вернуть ТО ЖЕ САМОЕ, не
    #пересчитывая (см. про оракул в докстринге выше), а пересчёт по сохранённым ответам
    #дал бы другой результат, если преподаватель успел поправить викторину.
    stored = dict(answers)
    stored["_per_question"] = graded["per_question"]
    _upsert_result(db, a.id, user.id, graded["score"], graded["correct_count"],
                   graded["total_count"], stored,
                   duration_ms=int(payload.get("duration_ms") or 0))
    snap = activity_state.bump(a.id)
    if snap is not None:
        done = db.query(ActivityResult).filter(ActivityResult.activity_id == a.id).count()
        _emit(db, a.conversation_id,
              {"type": "activity.state", "activity_id": a.id,
               "conversation_id": a.conversation_id, "seq": snap["seq"],
               "payload": {"submitted_count": done}})
        _emit_host_progress(db, a)
    graded.update(_review(questions, opts, answers, graded["per_question"]))
    return graded


# ── Соревнование ─────────────────────────────────────────────────────────────────────
@router.post("/{activity_id}/next")
def contest_next(activity_id: str, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Показать следующий вопрос ВСЕМ разом. Только ведущий."""
    a = _require_running(_require_host(db, activity_id, user))
    if a.kind != "contest":
        raise HTTPException(status_code=400, detail="Это не соревнование")
    snap = activity_state.get(a.id)
    if snap is None:
        raise HTTPException(status_code=400, detail="Соревнование уже завершено")
    quiz_id = (a.params or {}).get("quiz_id") or ""
    total = db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id).count()
    idx = int(snap["payload"].get("question_index", -1)) + 1
    if idx >= total:
        raise HTTPException(status_code=400, detail="Вопросы закончились — завершите соревнование")
    #Ручной сдвиг ведущего идёт ТЕМ ЖЕ путём, что и автопереход по времени: отсчёт
    #начинается заново, счётчик ответивших обнуляется.
    _contest_show(db, a, idx, total)
    return {"ok": True, "index": idx, "total": total}


@router.post("/{activity_id}/answer")
def contest_answer(activity_id: str, payload: dict = Body(...),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Ответ на ТЕКУЩИЙ вопрос соревнования. Один раз на вопрос.

    Повтор отвергаем (в отличие от опроса, где «передумал» — норма): здесь баллы зависят
    от скорости, и второй ответ был бы способом сначала выбрать наугад, а потом исправиться
    без потери времени."""
    _throttle("answer", user)
    a = _require_running(_require_activity_participant(db, activity_id, user))
    if a.kind != "contest":
        raise HTTPException(status_code=400, detail="Это не соревнование")
    snap = activity_state.get(a.id)
    if snap is None:
        raise HTTPException(status_code=400, detail="Соревнование уже завершено")
    idx = int(snap["payload"].get("question_index", -1))
    if idx < 0:
        raise HTTPException(status_code=400, detail="Вопрос ещё не показан")
    quiz_id = (a.params or {}).get("quiz_id") or ""
    questions = (db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id)
                 .order_by(QuizQuestion.order_no.asc()).all())
    if idx >= len(questions):
        raise HTTPException(status_code=400, detail="Вопрос ещё не показан")
    q = questions[idx]
    answers = dict(snap["payload"].get("answers") or {})
    slot = dict(answers.get(str(idx)) or {})
    if user.id in slot:
        raise HTTPException(status_code=409, detail="Вы уже ответили на этот вопрос")
    opts = _options_by_question(db, [q.id]).get(q.id, [])
    ok = activity_grading.check_answer(q, opts, payload.get("answer"))
    import time
    elapsed = int(time.monotonic() * 1000) - int(snap["payload"].get("question_started_ms") or 0)
    limit_ms = int((a.params or {}).get("limit_ms") or 30000)
    gain = activity_grading.speed_score(q.points or 1, elapsed, limit_ms) if ok else 0.0
    slot[user.id] = {"correct": ok, "gain": gain}
    answers[str(idx)] = slot
    scores = dict(snap["payload"].get("scores") or {})
    scores[user.id] = round(float(scores.get(user.id, 0) or 0) + gain, 2)
    snap = _require_state(activity_state.patch(a.id, {"answers": answers, "scores": scores}))
    _emit(db, a.conversation_id,
          {"type": "activity.state", "activity_id": a.id,
           "conversation_id": a.conversation_id, "seq": snap["seq"],
           #🔥 Табло — В КАДРЕ. Оно считается в проекции состояния, то есть попадает
           #клиенту только при открытии активности: кнопка «Лидерборд» во время игры
           #показывала пустой список у всех, кто не перезаходил. В соревновании баллы
           #публичны по построению (пьедестал объявляют вслух), поэтому шлём всем.
           "payload": {"answered_count": len(slot), "question_index": idx,
                       "board": _contest_board(db, scores)}})
    _emit_host_progress(db, a)
    return {"ok": True, "correct": ok, "gain": gain, "my_score": scores[user.id]}


def _contest_board(db: Session, scores: dict) -> list:
    """Табло: имена и баллы, по убыванию."""
    if not scores:
        return []
    rows = db.query(User).filter(User.id.in_(list(scores.keys()))).all()
    names = {u.id: (u.full_name or u.name or u.login or u.id) for u in rows}
    board = [{"user_id": uid, "name": names.get(uid, uid), "score": float(sc or 0)}
             for uid, sc in scores.items()]
    board.sort(key=lambda r: -r["score"])
    for i, row in enumerate(board, start=1):
        row["place"] = i
    return board


def _persist_contest_results(db: Session, a: Activity, payload: dict) -> None:
    """Перенести баллы соревнования из памяти в БД при завершении.

    До этого момента их там нет намеренно (§2 плана: каждый ответ — не транзакция), но
    журнал беседы обязан пережить перезапуск, поэтому итог фиксируем один раз, здесь."""
    scores = payload.get("scores") or {}
    answers = payload.get("answers") or {}
    total = len({k for k in answers})
    #🔥 Участники берутся из ОТВЕТОВ, а не только из баллов. Раньше цикл шёл по `scores`,
    #а туда человек попадает, лишь когда что-то заработал: ответивший на всё неправильно
    #не получал строки результата ВООБЩЕ и пропадал из таблицы — со стороны это выглядело
    #как «соревнование не считает результаты». Ноль баллов — тоже результат, и его надо
    #показать: именно он говорит преподавателю, с кем разбирать тему.
    participants = set(scores) | {uid for slot in answers.values() for uid in (slot or {})}
    for uid in participants:
        correct = sum(1 for slot in answers.values()
                      if (slot.get(uid) or {}).get("correct"))
        #«Выполнено» = человек ОТВЕТИЛ, пусть и неверно. Не выбрал вариант вовсе —
        #задание не выполнено и в счётчик не идёт (прямое требование): иначе молчание
        #выглядело бы как участие.
        answered = sum(1 for slot in answers.values() if uid in (slot or {}))
        _upsert_result(db, a.id, uid, float(scores.get(uid) or 0), correct, total,
                       {"answered_count": answered})


@router.get("/{activity_id}/results")
def activity_results(activity_id: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Таблица результатов. Ведущему — все, участнику — только своя строка."""
    a = _require_activity_participant(db, activity_id, user)
    is_host = a.host_id == user.id or user.role == "admin"
    query = db.query(ActivityResult).filter(ActivityResult.activity_id == activity_id)
    #🏆 В СОРЕВНОВАНИИ таблицу видят ВСЕ, и только после завершения: пьедестал и есть
    #смысл категории, а места всё равно объявляются вслух. В обычной викторине чужие
    #результаты по-прежнему не показываем — там это личный результат, а не игра.
    if not is_host and not (a.kind == "contest" and a.status == "finished"):
        query = query.filter(ActivityResult.user_id == user.id)
    rows = query.all()
    names = {}
    if rows:
        users = db.query(User).filter(User.id.in_([r.user_id for r in rows])).all()
        names = {u.id: (u.full_name or u.name or u.login or u.id) for u in users}
    items = [{"user_id": r.user_id, "name": names.get(r.user_id, r.user_id),
              "score": float(r.score or 0), "correct_count": int(r.correct_count or 0),
              "total_count": int(r.total_count or 0),
              #«Выполнено» — сколько заданий человек РЕШАЛ (неверный ответ тоже
              #выполнен). У викторины отдельного счётчика нет: там отправляют всё разом,
              #поэтому выполнено ровно столько, сколько заданий в наборе.
              "answered_count": int((r.answers or {}).get("answered_count",
                                                          r.total_count or 0) or 0),
              "finished_at": r.finished_at or "",
              "duration_ms": int(r.duration_ms or 0)} for r in rows]
    items.sort(key=lambda r: (-r["score"], r["duration_ms"] or 10 ** 12))
    for i, row in enumerate(items, start=1):
        row["place"] = i
    return {"results": items, "is_host": is_host,
            "participants": len(_participants_of(db, a.conversation_id))}


# ── Доска ────────────────────────────────────────────────────────────────────────────
def _may_draw(db: Session, a: Activity, user: User, payload: dict) -> bool:
    return a.host_id == user.id or payload.get("pen_user_id") == user.id


def _clean_strokes(batch: list, limit: int = MAX_STROKES_PER_BATCH) -> list:
    """Отсеять штрихи, которые не могут быть настоящими (18.08.2026).

    ⚠️ Раньше ограничено было ТОЛЬКО ЧИСЛО штрихов, а размер каждого — ничем. Штрих это
    непрозрачный для сервера словарь от клиента (он и должен таким быть — формат объектов
    доски расширялся уже дважды), и в него помещалось сколько угодно: 500 штрихов в пачке
    по мегабайту каждый упирались только в общий предел тела запроса Caddy (4 МБ), а
    состояние доски держит до 20000 штрихов В ПАМЯТИ процесса, на машине с 960 МБ.
    Получалась не дыра в правах, а способ занять память законным участником с пером.

    ⚠️ `limit` — не украшение. Через эту же дверь проходит НЕ ТОЛЬКО пачка от клиента, но и
    «продолжить доску» (`start_activity`), где штрихов законно тысячи: предел пачки в 500
    молча обрезал бы восстановленную доску до первых пятисот, то есть стёр бы человеку
    работу под видом защиты. Пачке — MAX_STROKES_PER_BATCH, целой доске — MAX_STROKES_TOTAL.

    Режем ПОШТУЧНО, а не всю пачку: одна битая точка не должна стирать честно нарисованную
    линию рядом. 4 КБ — с шестикратным запасом к реальности (кусок за 150 мс это ~18 точек,
    ~300 байт). Потолок памяти на доску становится определённым: 20000 × 4 КБ = 80 МБ, и
    вместе с тормозом частоты выше набрать их быстро уже нельзя.
    """
    import json
    out = []
    for s in batch[:limit]:
        if not isinstance(s, (dict, list)):
            continue
        try:
            if len(json.dumps(s, ensure_ascii=False)) <= MAX_STROKE_BYTES:
                out.append(s)
        except (TypeError, ValueError):
            continue          #несериализуемое в состояние не кладём — оно оттуда и уедет в WS
    return out


@router.post("/{activity_id}/strokes")
def add_strokes(activity_id: str, payload: dict = Body(...),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Пачка штрихов. Пишет ведущий либо тот, кому он выдал перо."""
    _throttle("stroke", user)
    a = _require_running(_require_activity_participant(db, activity_id, user))
    if a.kind != "board":
        raise HTTPException(status_code=400, detail="Это не доска")
    snap = activity_state.get(a.id)
    if snap is None:
        raise HTTPException(status_code=400, detail="Доска уже закрыта")
    if not _may_draw(db, a, user, snap["payload"]):
        raise HTTPException(status_code=403, detail="Сейчас рисует другой участник")
    batch = payload.get("strokes")
    if not isinstance(batch, list):
        raise HTTPException(status_code=400, detail="Ожидается список штрихов")
    batch = _clean_strokes(batch)
    strokes = list(snap["payload"].get("strokes") or [])
    if payload.get("clear"):
        strokes = []
    strokes.extend(batch)
    #Потолок — защита от бесконечно растущего состояния в памяти на длинной паре.
    #Режем СТАРЫЕ: свежее человеку нужнее, а «доска целиком» и так уезжает в артефакт.
    if len(strokes) > MAX_STROKES_TOTAL:
        strokes = strokes[-MAX_STROKES_TOTAL:]
    snap = _require_state(activity_state.patch(a.id, {"strokes": strokes}))
    _emit(db, a.conversation_id,
          {"type": "activity.state", "activity_id": a.id,
           "conversation_id": a.conversation_id, "seq": snap["seq"],
           "payload": {"strokes_added": batch,
                       "cleared": bool(payload.get("clear"))}})
    return {"ok": True, "total": len(strokes)}


@router.post("/{activity_id}/pen")
def give_pen(activity_id: str, payload: dict = Body(default={}),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Выдать/снять перо. `{"random": true}` — жребий по ростеру беседы.

    Жребий С ПАМЯТЬЮ (`pen_history`): без неё генератор регулярно выдаёт одного и того же
    дважды подряд, и это читается группой как «преподаватель к нему придирается»."""
    a = _require_running(_require_host(db, activity_id, user))
    if a.kind != "board":
        raise HTTPException(status_code=400, detail="Это не доска")
    snap = activity_state.get(a.id)
    if snap is None:
        raise HTTPException(status_code=400, detail="Доска уже закрыта")
    history = list(snap["payload"].get("pen_history") or [])
    if payload.get("random"):
        import random
        pool = [uid for uid in _participants_of(db, a.conversation_id) if uid != a.host_id]
        if not pool:
            raise HTTPException(status_code=400, detail="В беседе больше никого нет")
        fresh = [uid for uid in pool if uid not in history] or pool
        if len(fresh) > 1 and history:
            fresh = [uid for uid in fresh if uid != history[-1]] or fresh
        target = random.choice(fresh)
        history.append(target)
        if len(history) >= len(pool):
            history = [target]           #круг пройден — начинаем новый
    else:
        target = str(payload.get("user_id") or "")
        if target and _participant(db, a.conversation_id, target) is None:
            raise HTTPException(status_code=404, detail="Этот человек не в беседе")
    snap = _require_state(activity_state.patch(a.id, {"pen_user_id": target, "pen_history": history}))
    name = ""
    if target:
        u = db.query(User).filter(User.id == target).first()
        name = (u.full_name or u.name or u.login or "") if u else ""
    _emit(db, a.conversation_id,
          {"type": "activity.state", "activity_id": a.id,
           "conversation_id": a.conversation_id, "seq": snap["seq"],
           "payload": {"pen_user_id": target, "pen_user_name": name}})
    return {"ok": True, "pen_user_id": target, "pen_user_name": name}


@router.post("/{activity_id}/snapshot")
def board_snapshot(activity_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Снимок доски в чат БЕЗ завершения: на доске много написано, и это не должно
    пропасть только потому, что пара ещё идёт."""
    a = _require_running(_require_host(db, activity_id, user))
    if a.kind != "board":
        raise HTTPException(status_code=400, detail="Это не доска")
    snap = activity_state.get(a.id)
    if snap is None:
        raise HTTPException(status_code=400, detail="Доска уже закрыта")
    art = BoardArtifact(id=f"board:{uuid4().hex}", conversation_id=a.conversation_id,
                        activity_id=a.id, author_id=user.id,
                        sheet=snap["payload"].get("sheet") or "blank",
                        strokes=list(snap["payload"].get("strokes") or []),
                        title=a.title or "", created_at=_now())
    db.add(art)
    db.commit()
    msg = Message(conversation_id=a.conversation_id, sender_id=user.id, body=art.id,
                  created_at=_now(), kind="board", body_format="plain")
    db.add(msg)
    db.commit()
    from .messenger import _broadcast
    _broadcast(db, a.conversation_id)
    return {"ok": True, "board_id": art.id}


# ── Журнал беседы ────────────────────────────────────────────────────────────────────
@router.get("/journal")
def journal(conversation_id: str = Query(...), user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    """Журнал активностей беседы (§9 плана).

    Замена тому, чего сделать нельзя: автоматически выставить оценку в учебный журнал не
    получится — беседа не связана с учебной группой. Собственный журнал беседы честен: он
    про то, что происходило именно здесь.

    Кто что видит: ведущий и обладатели права `activities` — таблицу целиком, участник —
    только свои строки."""
    from .messenger import _permissions_for
    part = _require_participant(db, conversation_id, user)
    full = (user.role in ("teacher", "admin")
            or "activities" in _permissions_for(db, part))
    acts = (db.query(Activity).filter(Activity.conversation_id == conversation_id)
            .order_by(Activity.started_at.desc()).limit(200).all())
    ids = [a.id for a in acts]
    results = (db.query(ActivityResult).filter(ActivityResult.activity_id.in_(ids)).all()
               if ids else [])
    by_act: dict[str, list] = {}
    for r in results:
        by_act.setdefault(r.activity_id, []).append(r)
    hosts = {}
    if acts:
        rows = db.query(User).filter(User.id.in_([a.host_id for a in acts])).all()
        hosts = {u.id: (u.full_name or u.name or u.login or u.id) for u in rows}
    quiz_titles = _quiz_titles(db, acts)
    items = []
    for a in acts:
        rows = by_act.get(a.id, [])
        mine = next((r for r in rows if r.user_id == user.id), None)
        if not full and mine is None:
            continue          #участник видит только те активности, где реально участвовал
        cell = {"id": a.id, "kind": a.kind, "title": a.title or quiz_titles.get(a.id, ""),
                "host_name": hosts.get(a.host_id, ""), "status": a.status,
                "started_at": a.started_at or "", "finished_at": a.finished_at or "",
                "participants": len(rows),
                "my_score": float(mine.score or 0) if mine else None,
                "my_correct": int(mine.correct_count or 0) if mine else None,
                "my_total": int(mine.total_count or 0) if mine else None}
        if full:
            cell["average"] = (round(sum(float(r.score or 0) for r in rows) / len(rows), 2)
                               if rows else 0)
        items.append(cell)
    return {"items": items, "full": full}


def _quiz_titles(db: Session, acts: list) -> dict:
    """Название викторины для строк журнала (у активности своего заголовка может не быть)."""
    ids = {(a.params or {}).get("quiz_id") for a in acts if a.kind in ("quiz", "contest")}
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = db.query(QuizSet).filter(QuizSet.id.in_(list(ids))).all()
    by_quiz = {q.id: (q.title or "") for q in rows}
    return {a.id: by_quiz.get((a.params or {}).get("quiz_id"), "") for a in acts}


# ── Состояние одной активности ───────────────────────────────────────────────────────
# ⚠️ ЭТОТ МАРШРУТ ОБЯЗАН БЫТЬ ПОСЛЕДНИМ В ФАЙЛЕ, и это не стилистика. `{activity_id}` —
# параметр пути без ограничений: он подходит под ЛЮБОЙ односегментный GET, и FastAPI
# отдаёт запрос ПЕРВОМУ подошедшему маршруту в порядке регистрации. Объяви его выше — и
# `/quizzes`, `/journal`, `/boards` начнут отвечать «Активность не найдена», хотя их
# собственные обработчики целы, а их тесты зелены (они-то зовут функцию напрямую через
# TestClient по тому же пути… и получают чужой обработчик). Новый односегментный GET
# добавлять ТОЛЬКО выше этой черты.
@router.get("/{activity_id}")
def get_activity(activity_id: str, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    a = _require_activity_participant(db, activity_id, user)
    out = _activity_out(a, user.id)
    out["state"] = _state_for(db, a, user)
    return out
