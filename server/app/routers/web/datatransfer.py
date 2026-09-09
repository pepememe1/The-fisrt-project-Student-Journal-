"""
datatransfer.py — Выгрузка/загрузка справочников и резервные копии (админка).

Часть пакета `routers/web` (разрезан в 3.6: один файл на 4288 строк правили
62 коммита за полгода — он и был главным источником конфликтов при
одновременной работе). Общий роутер и хелперы — в `_common.py`; порядок
регистрации маршрутов задаёт `__init__.py`.
"""
from ._common import *      # noqa: F401,F403 — общий router, модели, хелперы
#Страница «Настройки ИИ» показывает, установлен ли движок распознавания на ХОСТЕ,
#и правит тот же набор ключей конфигурации, что и обработчик записи.
from .vector import _stt_installed      # noqa: F401
from .write import _AI_CFG_KEYS         # noqa: F401


# ── Выгрузка/загрузка данных и резервные копии (админка) ────────────────────────────
@router.get("/admin/data/datasets")
def admin_data_datasets(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Какие наборы данных есть и сколько в каждом записей — для галочек в интерфейсе.

    Считаем ЗДЕСЬ, а не на клиенте: администратор должен видеть объём до выгрузки («0
    родителей» объясняет, почему файл пустой, лучше, чем сам пустой файл)."""
    from ... import data_transfer as DT
    out = []
    for name, _f, model, label in DT.DATASETS:
        out.append({"name": name, "label": label,
                    "count": len(DT._query(db, name, model))})
    return {"datasets": out}


@router.get("/admin/data/export")
def admin_data_export(sets: str = "", request: Request = None,
                      _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """ZIP со всеми (или отмеченными) наборами данных — он же резервная копия.

    `sets` — имена через запятую; пусто = всё. Пароли в архив не попадают никогда
    (см. `data_transfer._SECRET_FIELDS`), поэтому выгрузка не является способом увести
    учётные записи. Действие аудируется: скачивание справочников с ПДн — событие, о
    котором должна остаться запись."""
    from ... import data_transfer as DT
    picked = [s.strip() for s in (sets or "").split(",") if s.strip()]
    from fastapi.responses import Response
    blob = DT.export_zip(db, picked or None)
    audit.log(db, request, actor=_admin.login, role="admin", action="data.export",
              detail=",".join(picked) if picked else "all")
    stamp = _now_iso()[:10]
    return Response(content=blob, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="gradebook-data-{stamp}.zip"'})


@router.post("/admin/data/inspect")
async def admin_data_inspect(file: UploadFile = File(...),
                             _admin: User = Depends(require_admin)):
    """Что лежит в присланном архиве — БЕЗ записи в базу.

    Обязательный шаг перед импортом: сначала показать состав, потом дать отметить, что
    вливать. Импорт «сразу при выборе файла» перезаписал бы боевые данные одним кликом."""
    from ... import data_transfer as DT
    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=400, detail="Пустой файл")
    try:
        return DT.inspect_zip(blob)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/data/import")
async def admin_data_import(file: UploadFile = File(...), sets: str = Form(""),
                            request: Request = None,
                            _admin: User = Depends(require_admin),
                            db: Session = Depends(get_db)):
    """Влить ОТМЕЧЕННЫЕ наборы из архива (слияние по id, без очистки существующего).

    `sets` обязателен: импорт без явного выбора — это «залить всё, что было в файле», а
    такого умолчания у операции, меняющей боевые данные, быть не должно."""
    from ... import data_transfer as DT
    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=400, detail="Пустой файл")
    picked = [s.strip() for s in (sets or "").split(",") if s.strip()]
    if not picked:
        raise HTTPException(status_code=400, detail="Не выбрано ни одного набора данных")
    try:
        result = DT.import_zip(db, blob, picked, _now_iso())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:      # noqa: BLE001 — битый архив не должен ронять сервер 500-кой
        raise HTTPException(status_code=400, detail=f"Не удалось прочитать архив: {e}")
    audit.log(db, request, actor=_admin.login, role="admin", action="data.import",
              detail=",".join(f"{k}={v}" for k, v in result["written"].items()))
    return {"ok": True, **result}


@router.get("/admin/ai-config")
def admin_get_ai_config(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Текущие настройки ИИ (провайдер, скоуп GigaChat, модель Ollama).

    🔒 КЛЮЧ GIGACHAT НАРУЖУ НЕ ОТДАЁТСЯ (06.09.2026, пункт P1 плана Ярослава: «секрет
    должен быть write-only: настроен / не настроен, но не доступен для повторного
    чтения»). Раньше значение возвращалось целиком, то есть любой, кто получил
    административную сессию — или снял ответ с экрана, — уносил рабочий ключ к платному
    внешнему сервису.

    ⚠️ Отдаём ПРИЗНАК `gigachat_configured`, а не пустую строку. Пустая строка означала бы
    «ключ не задан», и администратор, открыв настройки, честно решил бы, что его стёрли, —
    а следующим действием вписал бы новый поверх работающего.

    ⚠️ Заменить ключ по-прежнему можно: запись идёт своим путём (`POST /admin/ai-config`),
    и проверить новый ключ до сохранения тоже можно (`/admin/ai-config/test`). Закрыто
    ровно ЧТЕНИЕ — то единственное, ради чего секрет наружу и не нужен.
    """
    cfg = W.load_config(db)
    return {
        "vector_llm": cfg.get("vector_llm", "offline"),
        "gigachat_configured": bool((cfg.get("gigachat_credentials") or "").strip()),
        "gigachat_scope": cfg.get("gigachat_scope", "GIGACHAT_API_B2B"),
        "gigachat_model": cfg.get("gigachat_model", "GigaChat"),
        "local_model": cfg.get("local_model", "qwen2.5:3b"),
        "tts_enabled": cfg.get("tts_enabled", True),
        "tts_engine": cfg.get("tts_engine", "silero"),
        "stt_mode": cfg.get("stt_mode", "auto"),
        #Что реально стоит НА ЭТОМ хосте — чтобы админ выбирал, видя правду, а не
        #включал распознавание на машине, где движка нет.
        "stt_installed": _stt_installed(),
    }


@router.post("/admin/ai-config")
def admin_set_ai_config(payload: dict = Body(...), request: Request = None,
                        _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Сохранить настройки ИИ. Мержим В строку config (не затирая методику оценок и пр.),
    ставим серверную метку времени → синхронизируется на десктоп."""
    row = db.get(ConfigKV, "config")
    cur = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    for k in _AI_CFG_KEYS:
        if k in payload:
            cur[k] = payload[k]
    now = _now_iso()
    if row is None:
        db.add(ConfigKV(key="config", value=cur, updated_at=now, deleted=False))
    else:
        row.value = cur            #переприсваиваем dict — JSON-колонка увидит изменение
        row.updated_at = now
    db.commit()
    audit.log(db, request, actor=_admin.login, role="admin", action="ai.config")
    return {"ok": True}


@router.post("/admin/ai-config/test")
def admin_test_ai_config(payload: dict = Body(...),
                         _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Проверка провайдера: пробуем реально озвучить короткий факт. Ошибку НЕ глушим
    (в отличие от боевого voice), чтобы админ увидел причину. {ok, message}."""
    cfg = dict(W.load_config(db))
    for k in _AI_CFG_KEYS:      #проверяем с ПЕРЕДАННЫМИ (ещё не сохранёнными) значениями
        if k in payload:
            cfg[k] = payload[k]
    kind = (cfg.get("vector_llm") or "offline").strip()
    if kind == "offline":
        return {"ok": True, "message": "Оффлайн-режим: LLM не используется, ответы по фактам."}
    facts = "Средний балл — 4.5. Задолженностей нет."
    try:
        if kind == "gigachat":
            out = vector_llm._voice_gigachat(cfg, facts, "student", "как у меня дела")
        else:
            out = vector_llm._voice_ollama(cfg, facts, "student", "как у меня дела")
        return {"ok": True, "message": (out or "")[:200]}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}


# ПРИМЕЧАНИЕ: канонический CRUD занятий (create/update/delete) определён ВЫШЕ
# (teacher_create_lesson и др. ~строка 993) — с учебным периодом и защитой архива.
# Здесь раньше был дублирующий, устаревший набор тех же роутов (без термина и без
# read-only архива). FastAPI брал первый зарегистрированный, поэтому дубль был мёртвым
# кодом-миной (ruff F811) и удалён.
