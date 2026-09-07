"""
vector.py — «Вектор»: ответы по фактам журнала, голосовой ввод, озвучка, локализация.

Часть пакета `routers/web` (разрезан в 3.6: один файл на 4288 строк правили
62 коммита за полгода — он и был главным источником конфликтов при
одновременной работе). Общий роутер и хелперы — в `_common.py`; порядок
регистрации маршрутов задаёт `__init__.py`.
"""
from ._common import *      # noqa: F401,F403 — общий router, модели, хелперы
#Расписание группы уже собрано в модуле расписания (мердж портала с правками админа) —
#Вектор отвечает на «что сегодня» ТЕМИ ЖЕ данными, что показывает страница.
from .schedule import _group_schedule      # noqa: F401
#Пул потоков для синхронного CPU-heavy кода (распознавание речи), см. vector_stt.
from starlette.concurrency import run_in_threadpool      # noqa: E402


# «ВЕКТОР» ─────────────────────────────────────────────────────────────────────────
#Интенты со СТАТИЧНЫМ текстом (приветствие/справка/благодарность/факты о ВСГУТУ). Их
#НЕЛЬЗЯ гонять через LLM: там нечего переформулировать — это готовая справка, а не данные.
#Модель видит в ней НАЗВАНИЯ метрик («средний балл», «должники», «зона риска») без цифр и
#дописывает шаблон с плейсхолдерами («Средний балл по группам: [укажите средние баллы]») —
#ровно та выдумка, которую продукт обещает не делать (§5). LLM озвучивает ТОЛЬКО ответы с
#РЕАЛЬНЫМИ числами.
#ВАЖНО: набор ДОЛЖЕН совпадать с десктопным guard'ом в vector/engine.py::ask — при
#портировании веба его забыли перенести, отсюда и был баг с плейсхолдерами на сайте.
#intent → LLM НЕ вызываем (нет цифр для переформулировки ИЛИ формат нельзя ломать):
#  hello/thanks/help/unknown — текст-справка без чисел (иначе модель дописывает
#    плейсхолдеры «[укажите средние баллы]» вместо фактов, см. §5);
#  about_* — статичные факты о заведении;
#  schedule — структурный список пар: LLM мог бы добавить/выкинуть занятие.
#  weather — реальные показания метеослужбы: модель, «озвучивая данные», охотно
#            меняет числа, а неверная температура в продукте, обещающем не врать,
#            дороже красивой формулировки.
#  homework — текст задания написал ПРЕПОДАВАТЕЛЬ, и человек должен прочитать его
#             дословно; перефразированная домашка это уже другая домашка.
#  server_state — одни числа (диск, память, размер базы): модель их округляет и меняет
#             единицы, а по этим цифрам принимают решения.
_NO_VOICE_INTENTS = {"hello", "thanks", "help", "unknown",
                     "about_vsgutu", "about_college", "schedule", "weather", "howto",
                     "homework", "server_state"}


def user_ui_locale(user: User) -> str:
    """Язык интерфейса ЭТОГО пользователя ('ru', если перевод выключен/не выбран).

    ⚠️ Вызывать на СВОЁМ `user` того, кто спрашивает, а не на чужой записи — в
    `parent.py` это родитель, а не ребёнок, чьими данными отвечает Вектор (см.
    `parent_vector_ask`): иначе Вектор заговорил бы на языке, который родитель не выбирал."""
    prefs = user.prefs or {}
    return (prefs.get("locale") or "ru") if prefs.get("locale_on") else "ru"


def answer_vector_question(question: str, user: User, db: Session, context: str = "",
                           voice_role: str = "", locale: str = "ru") -> dict:
    """Общая логика ответа Вектора (вынесена из `vector_ask`, чтобы её мог переиспользовать
    мессенджер — команда `/vector <вопрос>` в любом чате, см. `routers/messenger.py`).
    Тот же принцип, что в десктопе: цифры берутся из реальных данных (SQL) — модель их НЕ
    выдумывает. Фактический текст собирает `_vector_facts`, затем LLM-провайдер
    (GigaChat/Ollama/оффлайн — из СИНХРОНИЗИРОВАННОГО конфига админа) лишь ПЕРЕФОРМУЛИРУЕТ
    его в стиле Вектора. Роль вызывающего (`user.role`) скоупит данные, как и в дедике.

    `locale` — язык ИНТЕРФЕЙСА вызывающего (см. `user_ui_locale`), не факт данных: цифры
    и ФИО остаются теми же, меняется только язык вокруг них (§ролей, полный перевод)."""
    cfg = W.load_config(db)
    result = _vector_facts(question.lower(), user, db, cfg)
    intent = result.get("intent")
    #voice_role меняет ТОЛЬКО тон обращения, но не скоуп данных. Нужен кабинету родителя:
    #факты там собираются от лица РЕБЁНКА (иначе пришлось бы дублировать весь студенческий
    #скоуп и однажды разойтись с ним), а говорить «твой средний балл» родителю — странно.
    role = voice_role or user.role
    #Вопрос НЕ из пула (unknown) — свободный small-talk: пара фраз + мягкий возврат к учёбе,
    #без решения задач (см. vector_llm.free_chat). Данные журнала тут не выдумываем.
    if intent == "unknown":
        #context — обезличенные заметки из «Избранного» (пусто для обычного /web/vector/ask):
        #помогает понять «а это когда?», но цифры успеваемости из него брать запрещено.
        result["text"] = vector_llm.free_chat(cfg, question, role, context, locale)
    #Озвучка: числа уже посчитаны и верны, LLM их не трогает — только стиль (и, если выбран
    #другой язык интерфейса, ЯЗЫК ответа — persona получает инструкцию, см. vector_llm).
    #Оффлайн или ошибка провайдера → вернётся исходный фактический текст (сайт не ломается).
    #Приветствие/справку/благодарность/расписание НЕ озвучиваем (см. _NO_VOICE_INTENTS).
    elif intent not in _NO_VOICE_INTENTS and not result.get("no_voice"):
        #no_voice — ответ содержит фактический список (имена, причины), который LLM исказил
        #бы или отказался озвучивать. Отдаём как есть.
        result["text"] = vector_llm.voice(cfg, result.get("text", ""), role, question, locale)
    elif locale != "ru":
        #Не озвучиваемый (структурный/фактический) ответ всё равно должен быть на выбранном
        #языке. Без данных (hello/thanks/help/about_*/howto) — готовый ручной перевод;
        #с реальными данными (расписание/погода/домашка/сервер/списки) — перевод ГОТОВОГО
        #русского текста постфактум, факты при этом не пересчитываются.
        role_for_static = user.role if user.role in ("student", "teacher", "admin") else "student"
        if intent in _STATIC_TEXT_INTENTS:
            _localize_static(result, role_for_static, locale)
        else:
            _localize_dynamic(result, locale, cfg)
    result.pop("no_voice", None)   #внутренний флаг наружу не отдаём
    return result


@router.post("/vector/ask")
def vector_ask(payload: dict = Body(...),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Серверный «Вектор» — см. `answer_vector_question`. Студент видит только свои данные."""
    question = (payload.get("message") or "").strip()
    return answer_vector_question(question, user, db, locale=user_ui_locale(user))


# ── Голосовой ввод (Speech-to-Text) — ЗАГОТОВКА, распознаёт на ХОСТЕ ────────────────
def _stt_installed() -> bool:
    """Стоит ли движок распознавания на ЭТОМ хосте (faster-whisper)."""
    try:
        from ... import stt_service
        return bool(stt_service.is_available())
    except Exception:
        return False


#Режимы голосового ввода (настройка админа `stt_mode`):
#  auto    — Whisper, если он есть на хосте; иначе распознаёт сам браузер. По умолчанию:
#            у кого локально стоит — тот и получает Whisper, никого не заставляя качать.
#  server  — ТОЛЬКО Whisper на сервере. Это «задел на продажу»: на боевом VPS движка нет
#            (1 ядро, 960 МБ — модель туда не влезает), поэтому режим честно отвечает
#            «недоступно», пока у колледжа не появится машина с видеокартой.
#  browser — ТОЛЬКО встроенное распознавание браузера, даже если Whisper установлен.
_STT_MODES = ("auto", "server", "browser")


@router.get("/vector/stt/status")
def vector_stt_status(user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Каким движком распознавать речь и доступен ли он.

    Решение принимает СЕРВЕР, а не клиент: иначе десктоп и сайт разошлись бы в поведении,
    а админ не смог бы включить Whisper «для всех» одним переключателем.

    `engine`: 'whisper' — шлём аудио на `/web/vector/stt`; 'browser' — клиент распознаёт
    сам; '' — голосовой ввод недоступен, и тогда `reason` объясняет, почему именно."""
    installed = _stt_installed()
    mode = (W.load_config(db).get("stt_mode") or "auto").strip()
    if mode not in _STT_MODES:
        mode = "auto"
    if mode == "browser":
        return {"available": True, "mode": mode, "engine": "browser",
                "installed": installed, "reason": ""}
    if installed:
        return {"available": True, "mode": mode, "engine": "whisper",
                "installed": True, "reason": ""}
    if mode == "server":
        #Жёстко выбран Whisper, а его нет — молча подменять браузерным нельзя: админ
        #включил распознавание НА СЕРВЕРЕ осознанно (ПДн не должны уходить в облако
        #браузера), и тихая подмена нарушила бы именно это решение.
        return {"available": False, "mode": mode, "engine": "",
                "installed": False,
                "reason": "Распознавание на сервере включено, но движок на нём не установлен."}
    return {"available": True, "mode": mode, "engine": "browser",
            "installed": False, "reason": ""}


def _stt_context(db: Session, user: User) -> str:
    """Слова, которые модель должна ожидать: имя маскота, предметы, фамилии своих студентов.

    Это не «улучшение по вкусу»: без такой подсказки Whisper уверенно подменяет редкие
    имена похожими обычными словами, и распознанное выглядит правдоподобно — а значит
    ошибку легко не заметить. Ограничиваем список: слишком длинная подсказка размывает
    внимание модели и начинает мешать."""
    words = ["Вектор", "ВСГУТУ"]
    try:
        cfg = W.load_config(db)
        ty, ts = W.current_term(cfg)
        if user.role == "teacher":
            pairs = W.teacher_assignments(db, user.id, ty, ts)
            words += sorted({s for _g, s in pairs})[:12]
            for group in sorted({g for g, _s in pairs})[:4]:
                words += [st.surname for st in W.students_in_group(db, group)][:30]
        elif user.role == "student":
            #Раньше здесь стояла защита hasattr(W, "_group_subject_list") на функцию,
            #которой в webdata никогда не существовало — то есть ветка не срабатывала
            #НИ РАЗУ, и студент молча оставался без подсказки с названиями предметов.
            words += W.group_subject_list(db, user.group_name or "")[:12]
    except Exception:
        pass
    #Уникальные, порядок сохраняем: первым идёт то, что важнее узнать.
    seen, out = set(), []
    for w in words:
        w = (w or "").strip()
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return ", ".join(out[:60])


@router.post("/vector/voice/command")
def vector_voice_command(payload: dict = Body(...),
                         user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """Разобрать РАСПОЗНАННУЮ фразу преподавателя в НАМЕРЕНИЕ (оценки / занятие / вопрос).

    ⚠️ ЭТОТ ЭНДПОИНТ НИЧЕГО НЕ ПИШЕТ. Он только предлагает — запись выполняет уже
    существующий `POST /web/teacher/grade` после того, как преподаватель подтвердил
    список в диалоге. Отдельного «голосового» пути записи НЕТ намеренно: второй путь
    означал бы вторую проверку прав и второй формат ключа оценки, а именно из-за
    расползания таких копий ключи оценок когда-то собирались в семи местах.

    LLM в цепочке НЕ участвует: разбор детерминированный (общий модуль `voice_command.py`,
    тот же, что на десктопе). Модель ошибается молча, а оценка не тому студенту — худшее,
    что может сделать журнал.

    Роль-скоуп обычный: только преподаватель и только своя группа+предмет.
    """
    _require("teacher", user)
    text = (payload.get("text") or "").strip()
    group = (payload.get("group") or "").strip()
    subject = (payload.get("subject") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустая фраза")
    if not (group and subject):
        raise HTTPException(status_code=400, detail="Нужны группа и предмет")

    cfg = W.load_config(db)
    ty, ts = W.current_term(cfg)
    #Право на ЭТУ группу и ЭТОТ предмет — до любого разбора: подсказывать состав чужой
    #группы (а ростер уходит в подсказку распознавателя) мы не имеем права.
    _teacher_check_assignment(db, user, group, subject, ty, ts)

    students = W.students_in_group(db, group)
    roster = [(st.surname or "", st.name or "") for st in students]

    #Занятия ЗА СЕГОДНЯ по этому предмету — к ним привяжутся оценки. Дата в базе хранится
    #как ДД.ММ.ГГГГ (формат десктопа), поэтому сравниваем строкой в том же виде.
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y")
    todays = [{"id": l.id, "label": f"{l.type} №{l.number}"}
              for l in db.query(Lesson).filter(
                  Lesson.group_name == group, Lesson.subject == subject,
                  Lesson.date == today, Lesson.year == ty, Lesson.semester == ts,
                  Lesson.deleted == False).all()]      # noqa: E712

    import voice_command
    res = voice_command.parse_batch(text, roster, todays)
    return {
        "kind": res.kind,
        "is_question": bool(res.is_question),
        "heard": res.heard,
        "error": res.error,
        "warnings": list(res.warnings),
        "lesson_id": res.lesson_id,
        "lesson_label": res.lesson_label,
        #Плоский список правок — ровно в том виде, в котором клиент затем отправит их в
        #`/web/teacher/grade` (surname/name/grade). Ничего додумывать ему не придётся.
        "items": [{"surname": i.surname, "name": i.name, "who": i.who,
                   "action": i.action, "grade": i.value} for i in res.items],
        "lesson": ({"type": res.lesson.type, "topic": res.lesson.topic,
                    "number": res.lesson.number} if res.lesson else None),
    }


@router.post("/vector/stt")
async def vector_stt(file: UploadFile = File(...),
                     user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Принимает аудио (getUserMedia/MediaRecorder из браузера), распознаёт на ХОСТЕ и
    возвращает текст. Клиент дальше сам решает: вопрос → /web/vector/ask, а команду
    записи (для преподавателя) — через подтверждение (как в десктопе). Здесь только STT."""
    from ... import stt_service
    if not stt_service.is_available():
        raise HTTPException(status_code=503,
                            detail="Голосовой ввод не настроен на сервере.")
    #Уважаем выбор админа: при режиме «распознаёт браузер» серверный движок не работает,
    #даже если установлен. Иначе настройка была бы декоративной — устаревший клиент
    #продолжал бы грузить хост, хотя администратор это запретил.
    if (W.load_config(db).get("stt_mode") or "auto").strip() == "browser":
        raise HTTPException(status_code=503,
                            detail="Распознавание на сервере отключено администратором.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой аудиофайл.")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Аудио слишком большое (макс 10 МБ).")
    cfg = W.load_config(db)
    #🎯 ПОДСКАЗКА МОДЕЛИ — то, чего здесь не хватало, и из-за чего «Вектор» слышался как
    #«Виктор», а фамилии студентов превращались в похожие русские слова. Нативная версия
    #подсказку давала, серверная — нет, поэтому качество на вебе было заметно хуже при
    #той же модели. Whisper опирается на контекст: список ожидаемых слов резко поднимает
    #узнавание редких имён (бурятские ФИО — Самбуева, Гындынова, Баянжаргал).
    #Скоуп ролевой, как везде: студент подсказывает свои предметы, преподаватель — ещё и
    #фамилии своих студентов. Чужих ФИО в подсказку не попадает.
    #⚠️ РАСПОЗНАВАНИЕ УХОДИТ В ПОТОК, И ЭТО НЕ ОПТИМИЗАЦИЯ, А ИСПРАВЛЕНИЕ ОТКАЗА.
    #Эндпоинт объявлен `async def` (иначе не прочитать файл через `await file.read()`),
    #а `transcribe_bytes` — обычная синхронная функция, которая считает Whisper'ом
    #секунды, а на большой модели и десятки секунд. Прямой вызов замораживал ЕДИНСТВЕННЫЙ
    #цикл событий: на всё это время вставали и мессенджер, и веб-сокеты, и `/health` —
    #то есть один человек, надиктовавший оценку, останавливал сервер всему колледжу.
    #Соседние 150 ручек мессенджера объявлены обычным `def`, и FastAPI уводит их в пул
    #потоков сам; здесь этот путь закрыт формой эндпоинта, поэтому поток берём явно.
    res = await run_in_threadpool(
        stt_service.transcribe_bytes,
        data, filename=file.filename or "audio.webm",
        size=cfg.get("stt_model", "large-v3"), device=cfg.get("stt_device", "auto"),
        context=_stt_context(db, user))
    if not res["ok"]:
        raise HTTPException(status_code=500, detail=res["error"])
    return {"text": res["text"]}


# ── Голосовая ОЗВУЧКА (Text-to-Speech) — синтез на ХОСТЕ (Silero), см. tts_service ──
@router.get("/vector/tts/status")
def vector_tts_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Доступна ли озвучка: движок стоит на хосте И админ её не выключил. По этому флагу
    и списку голосов веб-клиент решает, показывать ли кнопку динамика и выбор голоса."""
    from ... import tts_service
    cfg = W.load_config(db)
    enabled = cfg.get("tts_enabled", True)
    return {"available": bool(enabled) and tts_service.is_available(),
            "voices": tts_service.voices()}


@router.post("/vector/tts")
def vector_tts(payload: dict = Body(...),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Синтезирует переданный текст (уже готовый ответ Вектора, без ПДн) в WAV. Голос
    (male/female) выбирает пользователь в настройках. Кэш — внутри tts_service."""
    from fastapi.responses import Response
    from ... import tts_service
    cfg = W.load_config(db)
    if not cfg.get("tts_enabled", True):
        raise HTTPException(status_code=503, detail="Озвучка отключена администратором.")
    text = (payload.get("text") or "").strip()
    voice = (payload.get("voice") or "male").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустой текст.")
    #Движок синтеза выбирается конфигом (silero сейчас; клон-голос на GPU — потом).
    res = tts_service.synthesize(text, voice, engine=cfg.get("tts_engine", "silero"))
    if not res["ok"]:
        #Движок не поднялся / нет модели — 503: клиент откатится на speechSynthesis.
        raise HTTPException(status_code=503, detail=res["error"])
    return Response(content=res["audio"], media_type=res["mime"],
                   headers={"Cache-Control": "no-store"})


#Слова-триггеры «что умеешь / помощь / кто ты» и приветствие — распознаём ПЕРВЫМИ,
#чтобы Вектор отвечал по делу, а не сваливался в статистику. Дефолт тоже = подсказка.
# Факты о заведении — источник правды, НЕ генерация (как vector/knowledge.py на десктопе).
#Факты о заведении. Держать СИНХРОННО с vector/knowledge.py (ANSWER_VSGUTU/ANSWER_COLLEGE)
#— это источник правды десктопа (§12). Импортировать оттуда напрямую нельзя: vector/__init__
#тянет десктопные engine/intents, и на сервере это падает. Поэтому — копия с пометкой,
#как и другие «обязаны совпадать» дубли (_NO_VOICE_INTENTS, grade_id). Раньше сервер нёс
#УРЕЗАННУЮ версию (без истории и масштаба) и расходился с десктопом.
_ABOUT_VSGUTU = (
    "ВСГУТУ — это Восточно-Сибирский государственный университет технологий и "
    "управления в Улан-Удэ (Республика Бурятия). Основан 19 июня 1962 года как "
    "Восточно-Сибирский технологический институт (ВСТИ); в 1994 году получил статус "
    "университета, а в 2011-м переименован во ВСГУТУ. Сегодня это один из крупнейших "
    "вузов Сибири и Дальнего Востока — свыше 15 тысяч студентов и более 1900 "
    "преподавателей и сотрудников. Основной адрес — ул. Ключевская, 40В."
)
_ABOUT_COLLEGE = (
    "Технологический колледж ВСГУТУ — структурное подразделение университета в "
    "Улан-Удэ, которое даёт среднее профессиональное образование (СПО) и готовит "
    "специалистов среднего звена. После колледжа можно продолжить учёбу в самом "
    "ВСГУТУ. Группы колледжа обозначаются буквой «К» (например, К75/1). Этот "
    "электронный журнал — внутренняя система колледжа: оценки, посещаемость, "
    "пересдачи и успеваемость по группам."
)

_HOWTO = (
    "Как всё устроено в журнале:\n"
    "• Средний балл считается по практикам и экзаменам (лекции в него не входят); "
    "пересдача заменяет прежнюю попытку — учитывается последняя.\n"
    "• Задолженность — несданная практика (2 или Н) либо незачтённый экзамен.\n"
    "• Посещаемость: Н — неявка, Б — по болезни, О — опоздание (был на занятии, в пропуски не идёт).\n"
    "• Зона риска — студенты со средним баллом ниже 3.\n"
    "Спроси про свои цифры словами — я беру их из реальных данных, а не выдумываю.")


_HELP_BY_ROLE = {
    "student": ("Я — Вектор 🐯, помогаю с учёбой по твоим РЕАЛЬНЫМ данным (цифры не выдумываю). "
                "Спроси словами: «мой средний балл», «сколько у меня оценок», «оценки по "
                "информатике», «есть ли долги», «сколько пропусков», «когда математика», "
                "«что завтра». Или жми кнопки под чатом."),
    "teacher": ("Я — Вектор 🐯 для преподавателя, считаю строго по журналу твоих групп. Умею: "
                "«средний по группам», «кто в зоне риска», «у кого долги», «список студентов», "
                "«пропуски у <фамилия>», «какие группы», «расписание группы». Спрашивай как удобно."),
    "admin":   ("Я — Вектор 🐯 для администратора. Умею сводку по колледжу: «сколько студентов», "
                "«сколько преподавателей», «какие группы», «список преподавателей», «сводка по "
                "группе <название>». Спрашивай словами или жми кнопки."),
}
_HELLO_PREFIX = {"student": "Привет!", "teacher": "Здравствуйте!", "admin": "Здравствуйте!"}
_THANKS_TEXT = "Всегда рад помочь! 🐯"
_SCHED_DAYS = ["Пнд", "Втр", "Срд", "Чтв", "Птн", "Сбт"]


# ── Полный перевод интерфейса: локализация ответов Вектора ─────────────────────────
# Интенты БЕЗ данных (hello/thanks/help/about_*/howto) переведены вручную и один раз —
# ни числа, ни ФИО в них не участвуют, поэтому это ровно тот случай, где ручной перевод
# надёжнее и мгновеннее LLM (см. dictionaries.js на фронте — тот же принцип). Всё
# ОСТАЛЬНОЕ, что не озвучивается (`_NO_VOICE_INTENTS`/`no_voice=True` — расписание,
# погода, домашка, состояние сервера, списки должников), — реальные данные, вторую
# ручную копию для них на 3 языка не пишем, а прогоняем ГОТОВЫЙ русский текст через
# LLM-перевод постфактум (`_localize_dynamic`) — числа/ФИО от этого не меняются, меняется
# только язык вокруг них. Нет LLM/сбой перевода → тихо остаётся русский текст.
_STATIC_TEXT_INTENTS = {"hello", "thanks", "help", "about_vsgutu", "about_college", "howto"}

_STATIC_TEXT = {
    "en": {
        "about_vsgutu": (
            "ESSTU is the East Siberia State University of Technology and Management in "
            "Ulan-Ude (Republic of Buryatia, Russia). Founded on June 19, 1962 as the East "
            "Siberian Technological Institute; it became a university in 1994 and was "
            "renamed ESSTU in 2011. Today it's one of the largest universities in Siberia "
            "and the Russian Far East — over 15,000 students and more than 1,900 faculty "
            "and staff. Main address: 40V Klyuchevskaya St."
        ),
        "about_college": (
            "The ESSTU Technological College is a structural unit of the university in "
            "Ulan-Ude that provides vocational secondary education and trains mid-level "
            "specialists. After college, you can continue studying at ESSTU itself. "
            "College groups are labeled with the letter «К» (e.g., К75/1). This "
            "gradebook is the college's internal system: grades, attendance, resits and "
            "performance by group."
        ),
        "howto": (
            "How the gradebook works:\n"
            "• The average is calculated from practicals and exams (lectures aren't "
            "counted); a retake replaces the previous attempt — only the latest one "
            "counts.\n"
            "• A debt is an unpassed practical (a 2 or an absence) or a failed exam.\n"
            "• Attendance: Н — absent, Б — sick leave, О — excused absence.\n"
            "• The risk zone is students with an average below 3.\n"
            "Ask about your numbers in plain words — I pull them from real data, I don't "
            "make them up."
        ),
        "help_by_role": {
            "student": ("I'm Vector 🐯, I help with your studies using your REAL data (I "
                        "never make numbers up). Ask in plain words: “my average”, "
                        "“how many grades do I have”, “grades in computer "
                        "science”, “do I have any debts”, “how many "
                        "absences”, “when is math”, “what's "
                        "tomorrow”. Or use the buttons under the chat."),
            "teacher": ("I'm Vector 🐯 for teachers — I count strictly from your groups' "
                        "gradebook. I can: “average by group”, “who's at "
                        "risk”, “who has debts”, “student list”, "
                        "“absences for <surname>”, “which groups”, "
                        "“group schedule”. Ask however's convenient."),
            "admin": ("I'm Vector 🐯 for administrators. I can summarize the college: "
                      "“how many students”, “how many teachers”, "
                      "“which groups”, “teacher list”, “summary "
                      "for group <name>”. Ask in words or use the buttons."),
        },
        "hello_prefix": {"student": "Hi!", "teacher": "Hello!", "admin": "Hello!"},
        "thanks": "Always happy to help! 🐯",
    },
    "zh": {
        "about_vsgutu": (
            "东西伯利亚国立技术大学（ESSTU）位于俄罗斯布里亚特共和国乌兰乌德市。始建于1962年6月19日，"
            "最初名为东西伯利亚技术学院；1994年升格为大学，2011年更名为ESSTU。如今它是西伯利亚和俄罗斯"
            "远东地区最大的高校之一——在校学生超过15000人，教职工超过1900人。主校区地址：Ключевская街40В号。"
        ),
        "about_college": (
            "ESSTU技术学院是该大学设在乌兰乌德的一个下属机构，提供中等职业教育，培养中级专业人才。"
            "从学院毕业后可以继续在ESSTU本部深造。学院班级以字母“К”标注（例如К75/1）。"
            "这个电子成绩册就是学院的内部系统：成绩、出勤、补考和各班级的学习情况。"
        ),
        "howto": (
            "成绩册的工作原理：\n"
            "• 平均分根据平时成绩和考试计算（不包括讲座）；补考会替换之前的成绩——只计最新一次。\n"
            "• 欠考是指未通过的平时作业（得2分或缺勤）或未通过的考试。\n"
            "• 出勤情况：Н——缺勤，Б——病假，О——请假。\n"
            "• 高危学生是指平均分低于3分的学生。\n"
            "用你自己的话问我关于你的数据——我从真实数据中获取，不会编造。"
        ),
        "help_by_role": {
            "student": ("我是维克托🐯，会根据你的真实数据帮你学习（绝不编造数字）。用自己的话问我："
                        "「我的平均分」「我有多少个成绩」「计算机课的成绩」「我有欠考吗」"
                        "「我缺勤了几次」「数学课什么时候」「明天有什么课」。也可以点击聊天下方的按钮。"),
            "teacher": ("我是维克托🐯，专为教师服务，严格按照你所带班级的成绩册计算。我可以回答："
                        "「各班平均分」「谁处于高危」「谁有欠考」「学生名单」「<姓氏>的缺勤情况」"
                        "「有哪些班级」「班级课表」。随意提问即可。"),
            "admin": ("我是维克托🐯，为管理员服务。我可以汇总学院信息：「学生总数」「教师总数」"
                      "「有哪些班级」「教师名单」「<班级名称>的汇总情况」。用文字提问或点击按钮均可。"),
        },
        "hello_prefix": {"student": "你好！", "teacher": "您好！", "admin": "您好！"},
        "thanks": "随时乐意帮忙！🐯",
    },
}


def _localize_static(result: dict, role_for_static: str, locale: str) -> None:
    """Подставляет готовый перевод для интентов без данных (см. `_STATIC_TEXT_INTENTS`)."""
    pack = _STATIC_TEXT.get(locale)
    if not pack:
        return
    intent = result.get("intent")
    if intent == "hello":
        result["text"] = f"{pack['hello_prefix'][role_for_static]} {pack['help_by_role'][role_for_static]}"
    elif intent == "help":
        result["text"] = pack["help_by_role"][role_for_static]
    elif intent == "thanks":
        result["text"] = pack["thanks"]
    elif intent in ("about_vsgutu", "about_college", "howto"):
        result["text"] = pack[intent]


def _localize_dynamic(result: dict, locale: str, cfg: dict) -> None:
    """Переводит ГОТОВЫЙ русский ответ с реальными данными (расписание/погода/домашка/
    состояние сервера/списки должников) через тот же LLM-примитив, что и переводчик
    сообщений (`vector_llm.complete`) — вторую ручную копию на 3 языка для десятков
    комбинаций данных не пишем. Числа/ФИО/markdown модель НЕ обязана менять — только язык.
    Нет провайдера или ошибка → молча остаётся русский текст (та же деградация, что и у
    переводчика сообщений при недоступной модели)."""
    text = result.get("text")
    if not text or locale not in _LANG_NAMES:
        return
    prompt = [
        {"role": "system", "content": (
            f"Переведи следующий текст на {_LANG_NAMES[locale]} язык. Verbatim: верни "
            "ТОЛЬКО перевод, без кавычек и пояснений. Сохрани числа, даты, имена, "
            "маркированные списки (•), переносы строк и эмодзи как есть.")},
        {"role": "user", "content": text},
    ]
    try:
        out = (vector_llm.complete(cfg, prompt, temperature=0.1) or "").strip()
    except Exception:
        out = ""
    if out:
        result["text"] = out


#Те же коды/названия языков, что у переводчика сообщений (translate_service.LANGUAGES) —
#не заводим вторую копию, иначе однажды разойдутся списки поддерживаемых языков.
_LANG_NAMES = {k: v for k, v in translate_service.LANGUAGES.items() if k != "ru"}


def _known_subjects(user: User, db: Session) -> list:
    """Предметы в области видимости пользователя (для распознавания «по информатике»).
    Студент — предметы занятий своей группы; преподаватель — свои; админ — все."""
    try:
        if user.role == "student":
            #Не только из занятий: предмет из импортированного учебного плана существует
            #в Group.subjects ещё до того, как по нему заведут первое занятие — иначе
            #«оценки по <предмету>» не распознавали бы сам предмет (см. group_subject_list).
            return W.group_subject_list(db, user.group_name or "")
        if user.role == "teacher":
            ty, ts = W.current_term(W.load_config(db))
            return sorted({s for _g, s in W.teacher_assignments(db, user.id, ty, ts)})
        rows = db.query(Subject.name).filter(Subject.deleted == False).all()  # noqa: E712
        return sorted({r[0] for r in rows if r[0]})
    except Exception:
        return []


def _known_surnames(user: User, db: Session) -> list:
    """Фамилии студентов в области видимости (для «пропуски у Иванова»). Студенту —
    пусто (только о себе); преподавателю — студенты его групп; админу — все студенты."""
    if user.role == "student":
        return []
    try:
        if user.role == "teacher":
            ty, ts = W.current_term(W.load_config(db))
            out = []
            for g in W.teacher_group_names(db, user.id, ty, ts):
                out += [s.surname for s in W.students_in_group(db, g)]
            return sorted(set(out))
        rows = db.query(User.surname).filter(User.role == "student",
                                             User.deleted == False).all()  # noqa: E712
        return sorted({r[0] for r in rows if r[0]})
    except Exception:
        return []


def _grade_breakdown(lessons, records, scale=None) -> dict:
    """Счёт оценок студента: всего практических оценок (2–5) и разбивка по баллам.
    scale — {lesson_id: шкала}/строка/None (§ролей, 3.3.1): сырое значение переводится
    в 5-балльное ПЕРЕД подсчётом, поэтому «пятёрка» на 100-балльной (90+) тоже считается."""
    counts = {"5": 0, "4": 0, "3": 0, "2": 0}
    ids = {l.id for l in lessons if l.type == "Практика"}
    scale_map = scale if isinstance(scale, dict) else None
    for lid, v in (records or {}).items():
        if lid not in ids or not v:
            continue
        lscale = scale_map.get(lid, W.grading.DEFAULT_SCALE) if scale_map is not None else (scale or W.grading.DEFAULT_SCALE)
        num = W.grading.to_five_point(v, lscale)
        key = str(int(num)) if num is not None else None
        if key in counts:
            counts[key] += 1
    counts["всего"] = sum(counts[k] for k in ("5", "4", "3", "2"))
    return counts


def _zet_facts(db, surname: str, name: str, group: str, cfg: dict) -> dict:
    """Вектор: «Сколько у меня ЗЕТ / хватит ли для перевода» (docs/PLAN-ZET.md §6).
    Факты — из того же расчёта, что и /web/student/zet; LLM (если подключена) только
    переформулирует, порог и цифры не выдумывает."""
    ty, ts = W.current_term(cfg)
    summ = W.zet_summary_for_student(db, surname, name, group, ty, ts)
    if not summ["subjects"]:
        return {"text": "ЗЕТ по твоим предметам пока не заданы администрацией.",
                "mood": "neutral", "intent": "zet", "facts": {}}
    threshold = db.get(ZetThreshold, zet_threshold_id(group, ty, ts))
    min_zet = threshold.min_zet if (threshold and not threshold.deleted) else None
    # «Не сдано» — только ПРОВАЛЕННЫЕ (failed). Идущие предметы (pending) — это «ожидается»,
    # а не «не сдано»: назвать идущий предмет несданным — ровно то заблуждение, из-за
    # которого завели вариант C (ЗЕТ «в процессе» до рубежа семестра).
    unsatisfied = [s["subject"] for s in summ["subjects"] if s.get("state") == "failed"]
    pending_val = summ.get("pending", 0.0)
    parts = [f"У тебя {summ['earned']} из {summ['total']} ЗЕТ за семестр ({summ['pct']}%)."]
    if pending_val:
        parts.append(f"Ещё {pending_val} ЗЕТ в предметах, которые ещё идут, — они "
                     f"засчитаются, когда семестр по ним завершится.")
    if min_zet is not None:
        if summ["earned"] >= min_zet:
            parts.append(f"Порог для перевода — {min_zet} ЗЕТ, он уже набран.")
        else:
            parts.append(f"Порог для перевода — {min_zet} ЗЕТ, не хватает "
                         f"{round(min_zet - summ['earned'], 1)}.")
    if unsatisfied:
        parts.append("Не сдано: " + ", ".join(unsatisfied) + ".")
    # Пока семестр идёт (есть «ожидающие» предметы), низкий баланс — не повод грустить.
    if min_zet is None or summ["earned"] >= min_zet:
        mood = "happy"
    elif summ["pct"] >= 80 or pending_val:
        mood = "neutral"
    else:
        mood = "sad"
    return {"text": " ".join(parts), "mood": mood, "intent": "zet",
            "facts": {"earned": summ["earned"], "total": summ["total"], "pct": summ["pct"],
                     "pending": pending_val, "min_zet": min_zet, "unsatisfied": unsatisfied}}


def _subj_match(detected: str, lesson_subject: str) -> bool:
    """Совпадает ли распознанный предмет с названием из расписания (разные написания).

    Строго: ПЕРВОЕ значимое слово должно совпасть по основе + не меньше половины значимых
    слов. Иначе «коммуник» из ИКТ ложно матчит «коммуникации» в «Иностр. язык в проф.
    коммуникации» — и Вектор показывал расписание чужого предмета."""
    a, b = vector_nlu.normalize(detected), vector_nlu.normalize(lesson_subject)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    aw = [w for w in a.split() if len(w) >= 5]
    bw = [w for w in b.split() if len(w) >= 5]
    if not aw or not bw:
        return False
    first = aw[0][:6]
    if not any(w.startswith(first) for w in bw):     # первое слово обязано совпасть
        return False
    hits = sum(1 for w in aw if any(x.startswith(w[:6]) for x in bw))
    return hits >= max(1, len(aw) // 2)


def _schedule_answer(db: Session, group: str, msg: str, day) -> tuple:
    """Ответ по расписанию группы (данные — серверный парсер портала schedule_web).

    ПРЕДМЕТ ищем по предметам САМОГО расписания (портал называет их иначе, чем журнал),
    а не по журнальному списку — иначе «иностранный язык» не находился, а ложный матч по
    общему слову показывал чужой предмет. Приоритет: назван предмет → когда он; назван
    день/сегодня/завтра → пары этого дня; иначе — сегодня."""
    if not group:
        return ("Не вижу твоей группы — расписание привязано к ней. Уточни у администратора.", {})
    data = _group_schedule(db, group)   #портал + админ-правки (overlay)
    if not data or not data.get("weeks"):
        return (f"Расписание группы {group} пока не загрузилось с портала — попробуй чуть позже.", {})
    weeks = data["weeks"]
    cur_week = str(schedule_web.current_week_parity() or 1)

    def fmt(l):
        s = l.get("subject") or l.get("raw") or "занятие"
        room = f", ауд. {l['room']}" if l.get("room") else ""
        return f"{l.get('pair_no', '?')} пара ({l.get('time', '')}) — {s}{room}"

    # Все предметы, реально присутствующие в расписании этой группы (для матча запроса).
    sched_subjects = sorted({(l.get("subject") or "").strip()
                             for wk in ("1", "2")
                             for dn in _SCHED_DAYS
                             for l in weeks.get(wk, {}).get(dn, [])
                             if (l.get("subject") or "").strip()})
    subject = vector_nlu.match_subject(msg, sched_subjects) if day == "" else ""

    # A) «когда <предмет>» — ищем предмет по обеим неделям
    if subject:
        found = []
        for wk in ("1", "2"):
            for di, dname in enumerate(_SCHED_DAYS):
                for l in weeks.get(wk, {}).get(dname, []):
                    if _subj_match(subject, l.get("subject", "")):
                        wl = "" if wk == cur_week else f" ({'II' if wk == '2' else 'I'} неделя)"
                        found.append(f"{dname}, {fmt(l)}{wl}")
        if not found:
            return (f"«{subject}» в расписании группы {group} не нашёл. Возможно, предмет "
                    "называется иначе — посмотри вкладку «Расписание».", {})
        return (f"Расписание «{subject}» (группа {group}):\n• " + "\n• ".join(found),
                {"subject": subject, "count": len(found)})

    # Спросили «когда <предмет>», но предмет в расписании не опознан и день не назван —
    # не подсовываем расписание на сегодня (это ввело бы в заблуждение), а честно уточняем.
    if day == "" and "когда" in vector_nlu.normalize(msg):
        return ("Не понял, какой предмет — назови точнее (как во вкладке «Расписание»), "
                "или спроси «что сегодня» / «что завтра». 🐯", {})

    # B) конкретный день / сегодня / завтра. Берём ДАТУ целевого дня и чётность ИМЕННО
    # ЕЁ, а не сегодняшнюю: спрошенный «понедельник» в субботу — это уже СЛЕДУЮЩАЯ неделя
    # (другая чётность). Раньше брали cur_week (сегодня) → выдавали пары не той недели.
    import datetime as _dt
    today = _dt.date.today()
    today_idx = today.weekday()                # 0=Пн..6=Вс
    if day == "today":
        target = today
    elif day == "tomorrow":
        target = today + _dt.timedelta(days=1)
    elif isinstance(day, int):
        target = today + _dt.timedelta(days=(day - today_idx) % 7)   # ближайший этот день
    else:
        target = today
    idx = target.weekday()
    if idx > 5:
        return (f"В этот день у группы {group} занятий нет — выходной. 🐯", {})
    week = str(schedule_web.current_week_parity(target) or 1)         # чётность ЦЕЛЕВОГО дня
    dname = _SCHED_DAYS[idx]
    label = {"today": "Сегодня", "tomorrow": "Завтра"}.get(day, dname)
    lessons_day = weeks.get(week, {}).get(dname, [])
    if not lessons_day:
        return (f"{label} ({dname}, {'II' if week == '2' else 'I'} неделя) у группы {group} "
                f"пар нет. 🐯", {"day": dname, "count": 0})
    body = "\n• ".join(fmt(l) for l in lessons_day)
    return (f"{label} ({dname}), {'II' if week == '2' else 'I'} неделя — группа {group}:\n• "
            + body, {"day": dname, "count": len(lessons_day)})


#Сколько домашних заданий показываем в ответе. Больше — это уже не ответ, а простыня:
#человек спрашивает «что задали», имея в виду ближайшее, а не всю историю за семестр.
_HOMEWORK_LIMIT = 5


def _homework_answer(lessons, records: dict, subject: str = "") -> dict:
    """Ответ про домашние задания. Общий для студента, преподавателя и родителя.

    ДЗ — обычный тип занятия (`Lesson.type == "ДЗ"`, §14), текст задания лежит в поле
    «тема». Раньше на этот вопрос отвечать было нечем: своего интента не существовало, и
    «что задали по физике» уходило в оценки — то есть Вектор уверенно отвечал НЕ НА ТОТ
    вопрос. Здесь же показываем и статус («сдано»/«не сдано»), потому что второй вопрос
    после «что задали» всегда «а я сдал?».

    `records` пуст → статус не показываем (преподаватель спрашивает про группу целиком,
    и «сдано» относилось бы непонятно к кому)."""
    hw = [l for l in lessons if (l.type or "").strip().upper() == "ДЗ"]
    if subject:
        hw = [l for l in hw if l.subject == subject]
    if not hw:
        where = f" по предмету «{subject}»" if subject else ""
        return {"text": f"Домашних заданий{where} пока нет. 🐯", "mood": "happy",
                "intent": "homework", "facts": {"count": 0}}

    #Свежие сверху: спрашивают про то, что задали недавно. Дата в формате ДД.ММ.ГГГГ,
    #поэтому сортируем по перевёрнутому виду, а не по строке как есть.
    def _key(lesson):
        parts = (lesson.date or "").split(".")
        return "".join(reversed(parts)) if len(parts) == 3 else ""

    hw.sort(key=_key, reverse=True)
    shown, lines = hw[:_HOMEWORK_LIMIT], []
    for lesson in shown:
        task = (lesson.topic or "").strip() or "без описания"
        head = f"{lesson.subject} №{lesson.number}" if lesson.number else lesson.subject
        when = f" от {lesson.date}" if lesson.date else ""
        mark = records.get(lesson.id) if records else None
        status = ""
        if records:
            #«Н» на домашней работе значит «не сдал», а не «не был» (§14) — так и пишем.
            status = " — не сдано" if mark == "Н" else (f" — оценка {mark}" if mark else " — ждёт сдачи")
        lines.append(f"{head}{when}: {task}{status}")
    tail = f" Показал последние {len(shown)} из {len(hw)}." if len(hw) > len(shown) else ""
    return {"text": "Домашние задания:\n• " + "\n• ".join(lines) + "." + tail,
            "mood": "neutral", "intent": "homework",
            #no_voice: список заданий содержит формулировки преподавателя, и LLM их
            #перепишет — а это ровно то, что человек должен прочитать дословно.
            "no_voice": True, "facts": {"count": len(hw)}}


def _human_bytes(n) -> str:
    """Байты человеку: «1.3 ГБ». Вектор говорит словами, а не числами со степенями."""
    if not n:
        return "—"
    units, value, i = ("Б", "КБ", "МБ", "ГБ", "ТБ"), float(n), 0
    while value >= 1024 and i < len(units) - 1:
        value, i = value / 1024, i + 1
    return f"{round(value)} {units[i]}" if value >= 10 or i == 0 else f"{value:.1f} {units[i]}"


def _server_state_facts(db: Session) -> dict:
    """Состояние машины для администратора: диск, память, база, резервные копии.

    Цифры берутся у самой машины (`hostinfo`) — тем же способом, что их показывает
    раздел «Сервер». Второго источника не заводим: разойдутся, и человек получит два
    разных ответа на один вопрос в зависимости от того, где спросил.

    ⚠️ ОТВЕТ НЕ ОЗВУЧИВАЕТСЯ (`no_voice`). Здесь одни числа, а LLM их «оживляет» —
    округляет, меняет единицы, добавляет несуществующее. Для успеваемости это правило
    у нас давно, и к состоянию сервера оно относится ровно так же."""
    from ... import hostinfo
    from ...routers.serverinfo import _db_file, _deploy_root

    info = hostinfo.summary(_deploy_root())
    disk, mem = info.get("disk") or {}, info.get("memory") or {}
    parts = []
    if disk.get("total"):
        used = round(disk["used"] / disk["total"] * 100)
        parts.append(f"диск занят на {used}% ({_human_bytes(disk['free'])} свободно)")
    if mem.get("total"):
        used = round(mem["used"] / mem["total"] * 100)
        parts.append(f"память — {used}% из {_human_bytes(mem['total'])}")
    days = int((info.get("uptime") or 0) // 86400)
    hours = int(((info.get("uptime") or 0) % 86400) // 3600)
    if days or hours:
        parts.append(f"работает {days} д {hours} ч")

    path, size, encrypted = _db_file(), 0, False
    try:
        from ...db import DB_KEY
        encrypted = bool(DB_KEY)
        if path and os.path.isfile(path):
            size = os.path.getsize(path)
    except Exception:      # noqa: BLE001 — сведения о базе не повод ронять ответ
        pass
    if size:
        parts.append(f"база — {_human_bytes(size)}"
                     + (", зашифрована" if encrypted else ", БЕЗ шифрования"))

    #Свежесть копий — первое, что админ хочет знать про сервер, и первое, о чём забывают.
    backup_dir = os.environ.get("GRADEBOOK_BACKUP_DIR") or "/root/gb-backups"
    latest = ""
    if os.path.isdir(backup_dir):
        items = hostinfo.listdir(backup_dir, backup_dir)
        if items:
            latest = max(i["mtime"] for i in items)
    parts.append(f"последняя резервная копия — {latest}" if latest
                 else "резервных копий НЕ НАЙДЕНО")

    #Тревожное состояние Вектор обязан назвать тревогой, а не спрятать в перечисление.
    alarm = (not latest) or (disk.get("total") and disk["used"] / disk["total"] > 0.9)
    return {"text": "Сервер: " + "; ".join(parts) + ".",
            "mood": "sad" if alarm else "neutral", "intent": "server_state",
            "no_voice": True,
            "facts": {"disk": disk, "memory": mem, "db_size": size,
                      "db_encrypted": encrypted, "latest_backup": latest}}


def _admin_risk_facts(db: Session, cfg: dict, intent: str) -> dict:
    """Кто в зоне риска / у кого долги — по ВСЕМУ колледжу.

    Считаем через те же `webdata`, что и кабинет преподавателя: своя формула здесь
    означала бы, что у админа и у преподавателя разные списки должников по одному и тому
    же студенту (инвариант §4.8 — расчёт живёт в одном месте)."""
    rows = (db.query(User).filter(User.role == "student", User.deleted == False)  # noqa: E712
            .all())
    lessons_by_group: dict = {}
    risky, debtors = [], []
    for stud in rows:
        group = stud.group_name or ""
        if not group:
            continue
        if group not in lessons_by_group:
            #Только ТЕКУЩИЙ термин — иначе повторяющийся предмет (напр. «Физическая
            #культура») тянул бы долги/средний из прошлых курсов этой же группы.
            lessons_by_group[group] = W.current_term_lessons(db, group, W.group_lessons(db, group), cfg)
        lessons = lessons_by_group[group]
        records = W.student_records(db, stud.surname, stud.name, group)
        if intent == "debtors":
            if W.debts(lessons, records, scale=W.lesson_scale_map(db, lessons)):
                debtors.append(f"{W.display_name(stud)} ({group})")
            continue
        avg = W.average(lessons, records, cfg, scale=W.lesson_scale_map(db, lessons))
        #Порог тот же, что красит дашборды (2.5) — свой не придумываем.
        if avg and avg < 2.5:
            risky.append(f"{W.display_name(stud)} ({group}) — {avg}")

    found = debtors if intent == "debtors" else risky
    what = "с задолженностями" if intent == "debtors" else "в зоне риска"
    if not found:
        return {"text": f"Студентов {what} нет — по всему колледжу чисто. 🐯",
                "mood": "happy", "intent": intent, "facts": {"count": 0}}
    #Список фамилий LLM переписывать не должен — это факты о людях.
    shown = found[:15]
    tail = f" И ещё {len(found) - len(shown)}." if len(found) > len(shown) else ""
    return {"text": f"Студентов {what}: {len(found)}. " + "; ".join(shown) + "." + tail,
            "mood": "sad", "intent": intent, "no_voice": True,
            "facts": {"count": len(found)}}


def _vector_facts(msg: str, user: User, db: Session, cfg: dict) -> dict:
    """Фактический ответ (text/mood/intent/facts) по роли из РЕАЛЬНЫХ данных.

    Единый разбор запроса — vector_nlu.classify (тот же лексикон, что у десктопа). intent
    ВСЕГДА в ответе: по нему клиент выбирает эмоцию/анимацию маскота. Числа — только из SQL."""
    role = user.role if user.role in ("student", "teacher", "admin") else "student"
    subjects = _known_subjects(user, db)
    surnames = _known_surnames(user, db)
    nlu = vector_nlu.classify(msg, surnames=surnames, subjects=subjects)
    intent, subject, surname, day = nlu["intent"], nlu["subject"], nlu["surname"], nlu["day"]
    help_text = _HELP_BY_ROLE[role]

    # Общие для всех ролей: приветствие / помощь / благодарность / факты о заведении.
    if intent == "hello":
        return {"text": f"{_HELLO_PREFIX[role]} {help_text}", "mood": "happy",
                "intent": "hello", "facts": {}}
    if intent in ("help", "unknown"):
        return {"text": help_text, "mood": "happy" if intent == "help" else "neutral",
                "intent": "help" if intent == "help" else "unknown", "facts": {}}
    if intent == "thanks":
        return {"text": "Всегда рад помочь! 🐯", "mood": "happy", "intent": "thanks", "facts": {}}
    if intent == "weather":
        #Погода — такой же факт, как средний балл: берём у метеослужбы, не сочиняем.
        #Нет связи — честное «не знаю» (weather.answer сам это учитывает).
        import weather as _w
        data = _w.current()
        #ВОПРОС передаём целиком: интент срабатывает на слово «погода», а спросить могли
        #про другой город. Без этого аргумента на «погода в Саратове» Вектор уверенно
        #называл температуру Улан-Удэ — неверный ответ с настоящей цифрой (нашёл Влад).
        return {"text": _w.answer(msg),
                "mood": "happy" if data else "neutral",
                "intent": "weather",
                "facts": {"weather": data or {}}}
    if intent == "about_vsgutu":
        return {"text": _ABOUT_VSGUTU, "mood": "neutral", "intent": "about_vsgutu", "facts": {}}
    if intent == "about_college":
        return {"text": _ABOUT_COLLEGE, "mood": "neutral", "intent": "about_college", "facts": {}}
    if intent == "howto":
        return {"text": _HOWTO, "mood": "happy", "intent": "howto", "facts": {}}

    # ── СТУДЕНТ: только свои данные (privacy-by-design) ──────────────────────────────
    if role == "student":
        group = user.group_name
        #Занятия по предметам, убранным из плана группы, не должны попадать ни в долги,
        #ни в средний, ни в «мои оценки» (current_subject_lessons) — и занятия за ПРОШЛЫЕ
        #курсы по повторяющимся предметам вроде «Физической культуры» тоже не должны
        #(current_term_lessons) — та же тройная фильтрация, что в student_overview, плюс
        #раздельное обучение (§ролей, 3.6.1): чужая подгруппа студенту не видна.
        lessons = W.filter_lessons_by_student_subgroup(db, W.current_term_lessons(
            db, group, W.current_subject_lessons(
                db, group, W.group_lessons(db, group)), cfg), user.id)
        #Вектор отвечает СТУДЕНТУ о нём самом — значит теми же правилами, что и его журнал:
        #иначе один и тот же вопрос давал бы разные ответы на двух экранах.
        records = W.student_visible_records(db, user.surname, user.name, group)
        scale_map = W.lesson_scale_map(db, lessons)

        if intent == "schedule":
            text, facts = _schedule_answer(db, group, msg, day)
            return {"text": text, "mood": "neutral", "intent": "schedule", "facts": facts}
        if intent == "groups":
            return {"text": f"Твоя группа — {group}." if group else "Группа за тобой не закреплена.",
                    "mood": "neutral", "intent": "groups", "facts": {"group": group}}
        if intent == "debtors":
            d = W.debts(lessons, records, scale=scale_map)
            text = "Задолженностей нет — так держать! 🐯" if not d else \
                "Есть задолженности: " + "; ".join(d) + "."
            return {"text": text, "mood": "happy" if not d else "sad",
                    "intent": "debtors", "facts": {"debts": len(d)}}
        if intent == "absences":
            a = W.absences(lessons, records)
            return {"text": f"Пропусков всего: {a['всего']} (Н: {a['Н']}, Б: {a['Б']}, О: {a['О']}).",
                    "mood": "neutral" if a["всего"] else "happy", "intent": "absences", "facts": a}
        if intent == "homework":
            return _homework_answer(lessons, records, subject)
        if intent == "grade_count":
            b = _grade_breakdown(lessons, records, scale=scale_map)
            if subject:
                subj_lessons = [l for l in lessons if l.subject == subject]
                bs = _grade_breakdown(subj_lessons, records, scale=scale_map)
                return {"text": f"Оценок по предмету «{subject}» — {bs['всего']} "
                                f"(5: {bs['5']}, 4: {bs['4']}, 3: {bs['3']}, 2: {bs['2']}).",
                        "mood": "neutral", "intent": "grade_count", "facts": bs}
            return {"text": f"Всего у тебя оценок — {b['всего']} "
                            f"(пятёрок: {b['5']}, четвёрок: {b['4']}, троек: {b['3']}, двоек: {b['2']}).",
                    "mood": "happy" if b["2"] == 0 else "neutral",
                    "intent": "grade_count", "facts": b}
        if intent == "subject_grades" and subject:
            per = [p for p in W.per_subject_averages(lessons, records, cfg, scale=scale_map)
                   if p["subject"] == subject]
            if not per:
                return {"text": f"По предмету «{subject}» у тебя пока нет занятий с оценками.",
                        "mood": "neutral", "intent": "subject_grades", "facts": {}}
            p = per[0]
            marks = [records.get(l.id) for l in lessons
                     if l.subject == subject and l.type == "Практика"
                     and W.grading.to_five_point(records.get(l.id), scale_map.get(l.id, W.grading.DEFAULT_SCALE)) is not None]
            avg_txt = f"средний {p['average']}" if p["average"] else "оценок ещё нет"
            body = (", ".join(marks)) if marks else "—"
            return {"text": f"По предмету «{subject}»: {avg_txt}. Оценки: {body}.",
                    "mood": _mood_by_avg(p["average"]), "intent": "subject_grades",
                    "facts": {"subject": subject, "average": p["average"]}}
        if intent == "subjects":
            #Полный список предметов группы — из справочника И занятий (group_subject_list),
            #а не только из занятий: предмет из импортированного учебного плана появляется
            #раньше, чем по нему заводят первое занятие, и до этой ветки Вектор о нём молчал.
            #no_voice — перечень НАЗВАНИЙ: LLM, «озвучивая данные», переставляет и выдумывает
            #предметы, а это ровно то, что продукт обещает не делать.
            names = W.group_subject_list(db, group)
            if not names:
                return {"text": "Предметы за твоей группой пока не закреплены — их заводит "
                                "администратор. 🐯",
                        "mood": "neutral", "intent": "subjects", "facts": {"subjects": 0},
                        "no_voice": True}
            graded = {p["subject"] for p in W.per_subject_averages(lessons, records, cfg,
                                                                   scale=scale_map)
                      if p["average"]}
            text = f"Твои предметы ({len(names)}): " + ", ".join(names) + "."
            no_marks = [n for n in names if n not in graded]
            if no_marks:
                text += " Пока без оценок: " + ", ".join(no_marks) + "."
            return {"text": text, "mood": "neutral", "intent": "subjects",
                    "facts": {"subjects": len(names), "without_grades": len(no_marks)},
                    "no_voice": True}
        if intent == "grades" or intent == "subject_grades":
            per = W.per_subject_averages(lessons, records, cfg, scale=scale_map)
            graded = [p for p in per if p["average"]]
            if not graded:
                return {"text": "Оценок по практикам пока нет — как появятся, покажу. 🐯",
                        "mood": "neutral", "intent": "grades", "facts": {}}
            body = "; ".join(f"{p['subject']} — {p['average']}" for p in graded)
            return {"text": f"Твой средний по предметам: {body}.", "mood": "neutral",
                    "intent": "grades", "facts": {"subjects": len(graded)}}
        if intent == "zet":
            return _zet_facts(db, user.surname, user.name, group, cfg)
        # average и всё остальное (at_risk/roster/teachers/group_stats недоступны студенту)
        avg = W.average(lessons, records, cfg, scale=scale_map)
        if intent == "server_state":
            #Состояние сервера — не учебные данные, и студенту их знать незачем. Молчать
            #нельзя: без явной ветки вопрос про диск проваливался бы в средний балл, то
            #есть Вектор отвечал бы уверенно и не на тот вопрос.
            return {"text": "Про сервер знает администратор — это не учебные данные. "
                            "Я могу показать твой средний балл, оценки, пропуски, долги, "
                            "домашние задания и расписание. 🐯",
                    "mood": "neutral", "intent": "help", "facts": {}}
        if intent in ("at_risk", "roster", "teachers", "group_stats"):
            return {"text": "Эти данные — для преподавателя. Я могу показать твой средний балл, "
                            "оценки, пропуски, долги, домашние задания и расписание. 🐯",
                    "mood": "neutral", "intent": "help", "facts": {}}
        return {"text": f"Твой средний балл — {avg}. " + W.grading.methodology_text(cfg),
                "mood": _mood_by_avg(avg), "intent": "average", "facts": {"average": avg}}

    # ── ПРЕПОДАВАТЕЛЬ: только свои группы/предметы ──────────────────────────────────
    if role == "teacher":
        ty0, ts0 = W.current_term(cfg)
        pairs = W.teacher_assignments(db, user.id, ty0, ts0)
        groups = sorted({g for g, _s in pairs})
        #Предметы ЭТОГО препода В ЭТОЙ КОНКРЕТНОЙ группе — не весь его набор предметов
        #(баг 3.3.1: раньше фильтровали занятия группы по ВСЕМ предметам препода, из-за
        #чего в чужой группе с совпавшим названием предмета тоже что-то находилось).
        subjects_by_group: dict = {}
        for g, s in pairs:
            subjects_by_group.setdefault(g, set()).add(s)
        if not groups:
            return {"text": "За вами пока нет групп с занятиями по вашим предметам. " + help_text,
                    "mood": "neutral", "intent": "help", "facts": {}}
        tscale = W.teacher_scale(user)

        if intent == "schedule":
            text, facts = _schedule_answer(db, groups[0], msg, day)
            return {"text": text, "mood": "neutral", "intent": "schedule", "facts": facts}
        if intent == "groups":
            return {"text": "Ваши группы: " + ", ".join(groups) + ".", "mood": "neutral",
                    "intent": "groups", "facts": {"groups": len(groups)}}
        if intent == "subjects":
            #Предметы ПРЕПОДАВАТЕЛЯ — из назначений (препод↔предмет↔группа), а не весь
            #каталог группы: чужие предметы той же группы к его нагрузке отношения не имеют.
            own = sorted({s for _g, s in pairs})
            body = "; ".join(f"{g} — " + ", ".join(sorted(subjects_by_group.get(g, ())))
                             for g in groups)
            return {"text": f"Ваши предметы ({len(own)}): " + ", ".join(own) + f". По группам: {body}.",
                    "mood": "neutral", "intent": "subjects",
                    "facts": {"subjects": len(own), "groups": len(groups)}, "no_voice": True}
        if intent == "roster":
            g = groups[0]
            names = [W.display_name(s) for s in W.students_in_group(db, g)]
            body = ", ".join(names) if names else "список пуст"
            #🔒 no_voice — ЗДЕСЬ ЕГО ЗАБЫЛИ, и это была настоящая утечка (найдено
            #сторожем-свойством 01.09.2026, до того — ничем). Ответ целиком состоит из
            #ФИО студентов группы, и без флага он уезжал в GigaChat вместе с ними. Ровно
            #тот класс дефекта, ради которого заведён `test_vector_never_voices_names.py`:
            #ничего не ломается, ответ выглядит правдоподобно, а имена людей уходят
            #внешнему сервису — заметить это можно только чтением этой строки.
            return {"text": f"Студенты группы {g} ({len(names)}): {body}.", "mood": "neutral",
                    "intent": "roster", "facts": {"group": g, "count": len(names)},
                    "no_voice": True}
        if intent == "homework":
            #Что ЗАДАЛ САМ преподаватель: занятия его групп по его же предметам.
            #Статуса сдачи нет — вопрос про группу целиком, и «сдано» относилось бы
            #непонятно к кому; за конкретным студентом идут в журнал.
            own = []
            for g in groups:
                allowed = subjects_by_group.get(g, set())
                own += W.current_term_lessons(
                    db, g, [l for l in W.group_lessons(db, g) if l.subject in allowed], cfg)
            return _homework_answer(own, {}, subject)
        if intent in ("absences", "debtors") and surname:
            # пропуски/долги конкретного студента (по фамилии) в группах преподавателя
            for g in groups:
                for s in W.students_in_group(db, g):
                    if s.surname == surname:
                        gl = W.current_term_lessons(db, g, [
                            l for l in W.group_lessons(db, g)
                            if l.subject in subjects_by_group.get(g, set())], cfg)
                        rec = W.student_records(db, s.surname, s.name, g)
                        if intent == "absences":
                            a = W.absences(gl, rec)
                            return {"text": f"{W.display_name(s)}: пропусков {a['всего']} "
                                            f"(Н: {a['Н']}, Б: {a['Б']}, О: {a['О']}).",
                                    "mood": "neutral", "intent": "absences", "facts": a}
                        d = W.debts(gl, rec, scale=tscale)
                        txt = (f"У {W.display_name(s)} задолженностей нет." if not d
                               else f"{W.display_name(s)}: " + "; ".join(d) + ".")
                        return {"text": txt, "mood": "happy" if not d else "sad",
                                "intent": "debtors", "facts": {"debts": len(d)}}
            return {"text": f"Студента «{surname}» в ваших группах не нашёл.", "mood": "neutral",
                    "intent": "help", "facts": {}}

        if intent == "subject_grades" and subject:
            #«Что у Петровой по математике», «как группа по информатике». Обработчика у
            #преподавателя не было ВОВСЕ: классификатор интент выдавал верно, а ветка
            #заканчивалась общей справкой «я умею вот это» — наш обычный класс дефекта
            #(интент есть, вызывающего нет), и со стороны он читается как «Вектор не
            #видит базу».
            own_groups = [g for g in groups if subject in subjects_by_group.get(g, set())]
            if not own_groups:
                return {"text": f"Предмет «{subject}» не в вашей нагрузке — по чужим "
                                "предметам данных не покажу. 🐯",
                        "mood": "neutral", "intent": "help", "facts": {}}
            if surname:
                for g in own_groups:
                    for st in W.students_in_group(db, g):
                        if st.surname != surname:
                            continue
                        gl = W.current_term_lessons(
                            db, g, [l for l in W.group_lessons(db, g)
                                    if l.subject == subject], cfg)
                        rec = W.student_records(db, st.surname, st.name, g)
                        a = W.average(gl, rec, cfg, scale=tscale)
                        ab = W.absences(gl, rec)
                        return {"text": f"{W.display_name(st)} ({g}) по предмету "
                                        f"«{subject}»: средний балл {a}, "
                                        f"пропусков {ab['всего']}.",
                                "mood": _mood_by_avg(a), "intent": "subject_grades",
                                "facts": {"student": W.display_name(st), "group": g,
                                          "subject": subject, "average": a,
                                          "absences": ab["всего"]}}
                return {"text": f"Студента «{surname}» в ваших группах по предмету "
                                f"«{subject}» не нашёл.",
                        "mood": "neutral", "intent": "help", "facts": {}}
            rows = []
            for g in own_groups:
                gl = W.current_term_lessons(
                    db, g, [l for l in W.group_lessons(db, g) if l.subject == subject], cfg)
                vals = []
                for st in W.students_in_group(db, g):
                    a = W.average(gl, W.student_records(db, st.surname, st.name, g),
                                  cfg, scale=tscale)
                    if a > 0:
                        vals.append(a)
                rows.append((g, round(sum(vals) / len(vals), 2) if vals else 0.0))
            body = "; ".join(f"{g}: {a}" for g, a in rows)
            return {"text": f"Средний по предмету «{subject}» — {body}.",
                    "mood": "neutral", "intent": "subject_grades",
                    "facts": {"subject": subject, "groups": len(rows)}}
        if intent == "teachers":
            #Состав преподавателей — служебный справочник, он и так открыт коллеге в
            #каталоге мессенджера. Раньше вопрос упирался в общую справку.
            rows = db.query(User).filter(User.role == "teacher", User.deleted == False).all()  # noqa: E712
            names = [W.display_name(t) for t in rows]
            body = ", ".join(names) if names else "список пуст"
            return {"text": f"Преподаватели колледжа ({len(names)}): {body}.",
                    "mood": "neutral", "intent": "teachers",
                    "facts": {"count": len(names)}, "no_voice": True}
        if intent in ("grade_count", "zet"):
            #Отвечаем ПРИЧИНОЙ, а не общей справкой: иначе преподаватель решит, что
            #Вектор не понял вопрос, и будет переспрашивать его же другими словами —
            #ровно это и происходило.
            what = ("Счёт оценок" if intent == "grade_count" else "Зачётные единицы")
            return {"text": f"{what} веду по студенту, а не по преподавателю. "
                            "Спросите с фамилией — например «сколько пропусков у Иванова». "
                            "Я также покажу ваши группы, средние баллы, долги и расписание. 🐯",
                    "mood": "neutral", "intent": "help", "facts": {}}

        # Агрегаты по группам (средний + зона риска) + ПОИМЁННЫЙ список отстающих.
        # Преподаватель видит своих студентов в журнале, поэтому назвать их долги — не
        # раскрытие ПДн, а прямой ответ на «у кого долги». Раньше отдавался только счётчик,
        # и Вектор честно не мог назвать фамилии.
        per, risky = [], []
        for g in groups:
            gl = W.current_term_lessons(db, g, [
                l for l in W.group_lessons(db, g)
                if l.subject in subjects_by_group.get(g, set())], cfg)
            vals = []
            for s in W.students_in_group(db, g):
                rec = W.student_records(db, s.surname, s.name, g)
                a = W.average(gl, rec, cfg, scale=tscale)
                dbts = W.debts(gl, rec, scale=tscale)
                if dbts or (0 < a < 3):
                    risky.append((W.display_name(s), g, a, dbts))
                if a > 0:
                    vals.append(a)
            per.append((g, round(sum(vals) / len(vals), 2) if vals else 0.0))
        if intent in ("at_risk", "debtors"):
            if not risky:
                return {"text": "Должников и отстающих сейчас нет — группы идут ровно.",
                        "mood": "happy", "intent": "at_risk",
                        "facts": {"at_risk": 0}, "no_voice": True}
            #no_voice: имена и причины отдаём ДОСЛОВНО, мимо LLM — иначе он их исказит
            #(серверная озвучка фамилии не обезличивает) или, как в жалобе, откажется
            #называть. Это фактический список, переформулировать нечего.
            lines = []
            for nm, g, a, dbts in risky:
                reason = "; ".join(dbts) if dbts else f"средний балл {a}"
                lines.append(f"• {nm} ({g}): {reason}")
            head = ("Задолженности есть у одного студента:" if len(risky) == 1
                    else f"Задолженности есть у {len(risky)} студентов:")
            return {"text": head + "\n" + "\n".join(lines),
                    "mood": "sad", "intent": "at_risk",
                    "facts": {"at_risk": len(risky)}, "no_voice": True}
        if intent in ("average", "group_stats", "grades"):
            body = "; ".join(f"{g}: {ga}" for g, ga in per)
            #len(risky), а не «risk»: такой переменной здесь нет и никогда не было —
            #вопрос преподавателя про средний балл/статистику падал с NameError в 500,
            #и Вектор выглядел «не видящим базу». Держит test_vector_teacher_group_stats.
            n_risky = len(risky)
            #Сколько СТУДЕНТОВ в группах преподавателя — прямой ответ на «сколько
            #студентов»: раньше на этот вопрос отдавались только средние по группам.
            n_students = sum(len(W.students_in_group(db, g)) for g in groups)
            return {"text": f"Средний по вашим группам — {body}. "
                            f"Всего студентов: {n_students}. В зоне риска: {n_risky}.",
                    "mood": "neutral", "intent": "group_stats",
                    "facts": {"groups": len(per), "students": n_students, "at_risk": n_risky}}
        if intent == "server_state":
            #Сервером занимается администратор. Отвечаем прямо, а не общей справкой:
            #иначе преподаватель решит, что Вектор просто не понял вопрос.
            return {"text": "Состояние сервера — к администратору, это не учебные данные. "
                            "Я могу показать ваши группы, оценки, долги, пропуски, "
                            "домашние задания и расписание. 🐯",
                    "mood": "neutral", "intent": "help", "facts": {}}
        return {"text": help_text, "mood": "neutral", "intent": "help", "facts": {}}

    # ── АДМИН: агрегаты по заведению, справочники и состояние сервера ──────────────
    #Вопросы, на которые у администратора ответа нет по существу. Раньше они молча
    #проваливались в счётчики: на «что задали» приходило «студентов — 47», то есть
    #уверенный ответ не на тот вопрос.
    if intent in ("homework", "zet", "subject_grades", "grade_count"):
        return {"text": "Это данные учебного процесса — их видят студент и его "
                        "преподаватель. Я могу показать справочники, сводку по колледжу "
                        "и состояние сервера. 🐯",
                "mood": "neutral", "intent": "help", "facts": {}}
    if intent == "server_state":
        return _server_state_facts(db)
    if intent in ("at_risk", "debtors"):
        #Общеколледжный срез: раньше эти вопросы у админа падали в счётчики, хотя
        #«кто в зоне риска» — ровно тот вопрос, ради которого админ и заходит.
        return _admin_risk_facts(db, cfg, intent)
    if intent == "teachers":
        rows = db.query(User).filter(User.role == "teacher", User.deleted == False).all()  # noqa: E712
        names = [W.display_name(t) for t in rows]
        body = ", ".join(names) if names else "список пуст"
        return {"text": f"Преподаватели колледжа ({len(names)}): {body}.", "mood": "neutral",
                "intent": "teachers", "facts": {"count": len(names)}}
    if intent == "groups":
        rows = db.query(Group).filter(Group.deleted == False).all()  # noqa: E712
        names = [g.name for g in rows]
        body = ", ".join(names) if names else "групп нет"
        return {"text": f"Группы колледжа ({len(names)}): {body}.", "mood": "neutral",
                "intent": "groups", "facts": {"count": len(names)}}
    if intent == "subjects":
        #Админу — каталог предметов целиком (это его справочник, ровно как «Группы» выше).
        rows = db.query(Subject).filter(Subject.deleted == False).all()  # noqa: E712
        names = sorted({s.name for s in rows if s.name})
        body = ", ".join(names) if names else "каталог пуст"
        return {"text": f"Предметы колледжа ({len(names)}): {body}.", "mood": "neutral",
                "intent": "subjects", "facts": {"count": len(names)}, "no_voice": True}
    n_students = db.query(User).filter(User.role == "student", User.deleted == False).count()  # noqa: E712
    n_teachers = db.query(User).filter(User.role == "teacher", User.deleted == False).count()  # noqa: E712
    n_parents = db.query(User).filter(User.role == "parent", User.deleted == False).count()  # noqa: E712
    n_groups = db.query(Group).filter(Group.deleted == False).count()  # noqa: E712
    n_subjects = db.query(Subject).filter(Subject.deleted == False).count()  # noqa: E712
    #Заявки на регистрацию — то, что ТРЕБУЕТ ДЕЙСТВИЯ, а не просто цифра в сводке.
    #Раньше о них можно было узнать, только зайдя на страницу заявок и вспомнив о ней.
    pending = 0
    try:
        pending = db.query(RegistrationRequest).filter(
            RegistrationRequest.status == "pending").count()
    except Exception:      # noqa: BLE001 — сводка не повод ронять ответ
        pending = 0
    text = (f"В системе: студентов — {n_students}, преподавателей — {n_teachers}, "
            f"родителей — {n_parents}, групп — {n_groups}, предметов — {n_subjects}.")
    if pending:
        text += f" ⚠️ Ждут одобрения заявки на регистрацию: {pending}."
    return {"text": text,
            "mood": "neutral" if not pending else "surprised", "intent": "group_stats",
            "facts": {"students": n_students, "teachers": n_teachers,
                      "parents": n_parents, "groups": n_groups,
                      "subjects": n_subjects, "pending_registrations": pending}}
