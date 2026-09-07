"""
achievements.py — ачивки за пасхалки: свои, витрина в профиле, чужая витрина.

Часть пакета `routers/web` (разрез 3.6). Общий роутер и проверки — в `_common.py`.

━━ ЧТО ЗДЕСЬ ЕСТЬ И ЧЕГО НЕТ ━━
Есть чтение своего списка, переключение «показывать в профиле» и чтение ЧУЖОЙ витрины.
Ручки «выдай мне ачивку» НЕТ и быть не должно: ачивку открывает только серверная
логика самой пасхалки (`easter_eggs.unlock`). Дай её фронту — и весь список
накрутят одним curl'ом, а вместе с ним обесценится и витрина в профиле.

━━ ЧТО ВИДНО ЧУЖОМУ ━━
Только то, что человек сам отметил галочкой. Полный список своих ачивок наружу не
уходит: он показывает, чего у человека НЕТ, а это уже про его поведение в продукте.
"""
from ._common import *      # noqa: F401,F403 — общий router, модели, хелперы

from ... import easter_eggs


@router.get("/achievements")
def my_achievements(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Мои ачивки: что открыто и что из этого показано в профиле.

    Названия и значки НЕ отдаём — они статика и живут на клиенте
    (`web/src/config/achievements.js`), где переводятся вместе с интерфейсом.
    Сервер оперирует только идентификаторами."""
    rows = (db.query(UserAchievement)
              .filter(UserAchievement.user_id == user.id)
              .order_by(UserAchievement.unlocked_at.asc())
              .all())
    return {"unlocked": [{"id": r.achievement_id,
                          "unlocked_at": r.unlocked_at or "",
                          "showcase": bool(r.showcase)} for r in rows],
            "known": sorted(easter_eggs.ACHIEVEMENT_IDS)}


@router.post("/achievements/showcase")
def set_showcase(payload: dict = Body(...),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Отметить, какие ачивки показывать другим. Приходит ПОЛНЫЙ список отмеченных.

    ⚠️ Отметить можно только СВОЮ открытую ачивку. Присланные чужие или ещё не
    открытые id молча отбрасываются, а не создают строку: витрина — публичное поле,
    и через неё нельзя показать то, чего человек не получал."""
    want = payload.get("ids")
    want = {str(x) for x in want} if isinstance(want, list) else set()
    rows = db.query(UserAchievement).filter(UserAchievement.user_id == user.id).all()
    for r in rows:
        r.showcase = r.achievement_id in want
    db.commit()
    return {"ok": True, "showcase": [r.achievement_id for r in rows if r.showcase]}


@router.post("/easter-eggs/roll")
def roll_eggs(payload: dict = Body(...),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Бросок шанса. Возвращает id сработавшей пасхалки или null.

    ⚠️ Бросок ЗДЕСЬ, а не в браузере: `Math.random()` правится инструментами
    разработчика за секунду, и 1/500 перестала бы быть редкостью. Заодно бросок общий
    для всех устройств человека — иначе телефон и ПК давали бы два шанса на одно событие.

    Принимает либо `egg` (одна), либо `eggs` (список — тогда берётся ПЕРВАЯ сработавшая
    и на этом перебор кончается: две пасхалки на одной загрузке перестают читаться как
    редкая находка)."""
    if user.role != "student":
        #Пасхалки видит только студент (решение Влада). Преподавателю и админу не
        #бросаем вовсе — иначе кулдаун тратился бы на того, кому и показывать не будем.
        return {"egg": None}
    many = payload.get("eggs")
    if isinstance(many, list) and many:
        return {"egg": easter_eggs.roll_one_of([str(x) for x in many], user.id, db)}
    egg = str(payload.get("egg") or "")
    return {"egg": egg if egg and easter_eggs.roll(egg, user.id, db) else None}


@router.post("/easter-eggs/claim")
def claim_achievement(payload: dict = Body(...),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Закрыть находку: пасхалка доиграна до конца, выдаём ачивку.

    ⚠️ ПОЧЕМУ ЭТО НЕ «ручка выдай мне ачивку». Ачивка выдаётся, ТОЛЬКО если у человека
    есть свежий след срабатывания именно этой пасхалки (`EasterEggLog`, последний час).
    То есть накрутить список curl'ом нельзя: сначала нужно, чтобы сервер сам бросил
    шанс и он сошёлся. Клиент решает лишь МОМЕНТ — доиграл человек сцену или закрыл её.

    Час, а не пять минут: доска Делтарун или ночная смена FNAF идут минутами, и жёсткое
    окно отобрало бы ачивку у того, кто досмотрел до конца."""
    egg = str(payload.get("egg") or "")
    aid = str(payload.get("achievement") or "")
    if not egg or not aid:
        raise HTTPException(status_code=400, detail="Нужны egg и achievement")
    if easter_eggs.ACHIEVEMENTS.get(aid) != egg:
        #Пара «пасхалка → ачивка» зафиксирована на сервере: чужую ачивку за свою
        #пасхалку не получить, даже подобрав оба идентификатора.
        raise HTTPException(status_code=400, detail="Эта ачивка не от этой пасхалки")
    if not easter_eggs.was_triggered_recently(user.id, egg, db):
        raise HTTPException(status_code=400, detail="Пасхалка не срабатывала")
    fresh = easter_eggs.unlock(user.id, aid, db)
    return {"ok": True, "unlocked": fresh}

@router.get("/easter-eggs/on-login")
def egg_on_login(
    scene: bool = True,
    user: User = Depends(get_current_user),
    jti: str = Depends(current_jti),
    db: Session = Depends(get_db),
):
    """Что показать сразу после входа: не больше одной пасхалки за раз.

    ⚠️ Один запрос вместо пяти бросков подряд — и дело не в экономии. Условия («сейчас
    ночь», «до этого было три неудачных попытки», «сегодня день рождения») обязаны
    считаться на сервере: в браузере любое из них подделывается строкой в консоли, а
    шанс без честного условия ничего не стоит.

    ⚠️ `avatar` — ОТДЕЛЬНОЕ поле, а не ещё один вариант `egg`, и разделение
    принципиально. Полноэкранная сцена и метка на аватарке друг другу не мешают: одна
    перекрывает экран на секунды, вторая висит тихо. Сложи их в один слот — и любая
    выпавшая сцена лишала бы человека метки, хотя показать можно было и то и другое.
    Внутри же пары DOOM/Detroit выбор строго один: обе рисуются на одной аватарке.

    ⚠️ `scene=0` — «бросок сцены уже был, дай только метку аватарки» (24.08.2026).
    Нужно для перезагрузки страницы: F5 не должна давать новый шанс на редкую сцену, но
    метка обязана вернуться — она принадлежит человеку и дню, а не вкладке. Пока эти две
    величины ехали одним неделимым ответом, клиент после F5 не спрашивал ничего, и
    украшение пропадало до конца сессии.

    ⚠️ Метка НЕ бросается и клиентом не запоминается: `pick_avatar_egg` вычисляет её
    детерминированно по ВХОДУ (`jti` токена) и сама обновляет след. Поэтому спрашивать
    её можно сколько угодно раз — в пределах одного входа ответ один и тот же, `claim`
    всегда находит свежий след, а выход и новый вход дают новую метку."""
    return {
        "egg": easter_eggs.pick_on_login(user, db) if scene else None,
        "avatar": easter_eggs.pick_avatar_egg(user, db, session_key=jti),
    }


@router.get("/easter-eggs/journal")
def eggs_for_journal(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Что показать в журнале оценок: счётчик стиля и/или разовая находка.

    ⚠️ Возвращаем ДВЕ величины, а не одну, потому что у них разная природа. Счётчик
    ULTRAKILL детерминирован (его видит каждый отличник каждый раз — это не находка,
    а плашка-похвала), а кубик Isaac и внутренний голос — редкие броски. Сложи их в
    одно поле, и у отличника не выпадал бы больше НИ ОДИН редкий сюрприз: постоянная
    плашка занимала бы единственный слот навсегда.

    ⚠️ «Отличник» считается ЗДЕСЬ, а не в браузере: клиент передал бы любой средний.
    Считаем тем же `W.average`, что и страница статистики, — второй методики среднего
    в продукте быть не должно (инвариант §8)."""
    if user.role != "student":
        return {"ultrakill": False, "egg": None}

    cfg = W.load_config(db)
    #Тот же путь отбора занятий, что у `student_stats`: действующий план, текущий
    #термин, своя подгруппа. Иначе средний тут и средний на соседнем экране разъедутся.
    lessons = W.filter_lessons_by_student_subgroup(db, W.current_term_lessons(
        db, user.group_name, W.current_subject_lessons(
            db, user.group_name, W.group_lessons(db, user.group_name)), cfg), user.id)
    records = W.student_visible_records(db, user.surname, user.name, user.group_name)
    avg = W.average(lessons, records, cfg, scale=W.lesson_scale_map(db, lessons))

    #⚠️ Теперь это УСЛОВИЕ + БРОСОК, а не чистое условие (просьба Влада 23.08.2026):
    #отличник видел плашку при каждой загрузке журнала, и она перестала быть находкой.
    #Порядок важен: сперва дешёвая проверка среднего, и только потом бросок — иначе
    #шанс тратился бы на тех, кому плашка всё равно не полагается.
    #След срабатывания и кулдаун берёт на себя сам `roll`, отдельный `mark_triggered`
    #здесь больше не нужен и был бы вторым, лишним следом.
    ultra = avg >= easter_eggs.ULTRAKILL_MIN_AVG and easter_eggs.roll("ultrakill_rank", user.id, db)
    egg = easter_eggs.roll_one_of(["binding_of_isaac_d6", "disco_elysium_voice"], user.id, db)
    return {"ultrakill": ultra, "average": avg, "egg": egg}
