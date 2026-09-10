"""Вложения и вкладки панели беседы: файлы, медиа, «Избранное».

━━ ФАЙЛ ЧЕРЕЗ НАС НЕ ХОДИТ ━━
Порядок работы (docs/done/MESSENGER-ATTACHMENTS-PLAN.md, механизм Б):

    1. клиент  → POST /uploads/sign   {имя, размер, тип, беседа}
    2. сервер  → проверяет участие, роль, размер, тип → подписанная ссылка на ЗАГРУЗКУ
    3. клиент  → PUT файла НАПРЯМУЮ в хранилище (наш VPS не участвует)
    4. клиент  → POST /uploads/{id}/done  — «файл доехал»
    5. клиент  → обычная отправка сообщения с `attachment_id`
    6. чтение  → GET /attachments/{id}/url → проверка доступа → ссылка на СКАЧИВАНИЕ

⚠️ Шаг 4 обязателен и это не формальность: без него в базе копились бы записи о файлах,
которых в хранилище нет, и человек видел бы в списке файлы, которые не открываются.

⚠️ Каждая ссылка выдаётся ТОЛЬКО участнику беседы и живёт минуты. Ссылка утекает
пересылкой в чужой чат — это нормально для короткого срока и недопустимо для вечного.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import os

from fastapi import Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ... import storage
from ...models import Attachment, Conversation, Message, User
from ._common import (Depends as _D, _conversation, _now, _participant,  # noqa: F401
                      _require_participant, get_current_user, get_db, router)

#Что считаем «медиа», а что «файлом». Гифки и видео-ссылки — медиа, документы — файлы.
#⚠️ Разделение по СМЫСЛУ, а не по хранилищу: человек ищет «ту картинку» и «тот документ»
#в разных местах, и складывать их в одну вкладку значит не сделать ни одной.
_VIDEO_HOSTS = ("youtube.com", "youtu.be", "vk.com", "vkvideo.ru", "rutube.ru")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uploaded_last_day(db: Session, user_id: str) -> int:
    """Сколько байт этот человек занял вложениями за последние сутки.

    ⚠️ СЧИТАЕМ ВСЕ ЗАПИСИ, включая неподтверждённые (`ready=False`), и это осознанный
    размен. Подтверждение приходит от КЛИЕНТА: подписав загрузку и не подтвердив её,
    можно было бы залить сколько угодно, и ни один байт не попал бы в счёт. Считать надо
    то, что ограничивает диск в ХУДШЕМ случае, а не то, о чём клиент отчитался.
    Цена честная: несколько оборванных загрузок съедают часть суточного лимита до конца
    суток. При потолке в 200 МБ это заметно только тому, кто и так близок к границе.

    ⚠️ Окно скользящее (24 часа назад от «сейчас»), а не «с полуночи». С полуночью лимит
    обнуляется разом у всех, и в 00:01 можно занять двойную норму за пару минут.

    ⚠️ Метка `created_at` — строка ISO в UTC (так её пишет весь мессенджер), поэтому
    сравнение строковое и это корректно: ISO-8601 в UTC сортируется как текст.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    rows = (db.query(Attachment.size)
            .filter(Attachment.uploader_id == user_id,
                    Attachment.created_at >= since)
            .all())
    return sum(int(r[0] or 0) for r in rows)


def _att_out(a: Attachment) -> dict:
    return {"id": a.id, "name": a.name, "size": a.size, "mime": a.mime,
            "uploader_id": a.uploader_id, "created_at": a.created_at}


@router.post("/uploads/sign")
def sign_upload(payload: dict = Body(...), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Подписать ЗАГРУЗКУ файла. Проверки здесь, а не в браузере.

    ⚠️ Размер и тип проверяем на сервере, хотя клиент их уже видел: клиентская проверка
    существует ради вежливого сообщения, а не ради безопасности — обойти её можно
    curl'ом за секунду.
    """
    if not storage.configured():
        #Честный отказ вместо молчаливой потери: человек уверен, что отправил файл.
        raise HTTPException(status_code=503, detail="Хранилище файлов не настроено")

    conv_id = str(payload.get("conversation_id") or "")
    _require_participant(db, conv_id, user)

    name = (str(payload.get("name") or "")).strip()[:200]
    size = int(payload.get("size") or 0)
    mime = (str(payload.get("mime") or "")).strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Нужно имя файла")
    if size <= 0 or size > storage.MAX_SIZE:
        raise HTTPException(status_code=413,
                            detail=f"Файл больше {storage.MAX_SIZE // (1024 * 1024)} МБ")
    if not storage.mime_ok(mime):
        raise HTTPException(status_code=415, detail="Такой тип файла не поддерживается")

    #━━ СУТОЧНЫЙ ПОТОЛОК НА ЧЕЛОВЕКА ━━
    #Находка пентеста 3.7.8 (п. 2): потолок на ОДИН файл был, а на пользователя за сутки —
    #нет. Один аккаунт занимал хранилище целиком за вечер, и остальные получали «места
    #нет» — отказ, который выглядит как поломка сервера, а не как чьё-то злоупотребление.
    used = _uploaded_last_day(db, user.id)
    if used + size > storage.MAX_USER_DAY_BYTES:
        left = max(0, storage.MAX_USER_DAY_BYTES - used)
        raise HTTPException(
            status_code=429,
            detail=("Сегодня уже загружено %d МБ из %d. Осталось %d МБ — "
                    "попробуйте завтра или отправьте файл меньше."
                    % (used // (1024 * 1024),
                       storage.MAX_USER_DAY_BYTES // (1024 * 1024),
                       left // (1024 * 1024))))

    att_id = f"att:{uuid4().hex}"
    key = storage.object_key(conv_id, att_id)
    db.add(Attachment(id=att_id, conversation_id=conv_id, uploader_id=user.id,
                      name=name, size=size, mime=mime, storage_key=key,
                      created_at=_iso(), ready=False))
    db.commit()

    #⚠️ Ответ РАЗНЫЙ по форме, и клиент ветвится по `form_field`. Причина не в лени:
    #в объектное хранилище файл кладётся сырым PUT (иначе подпись не сойдётся), а к нам
    #— обычной формой, чтобы обработчик остался ОБЫЧНЫМ `def`. FastAPI уводит такие в
    #пул потоков, и запись файла на диск не встанет поперёк цикла событий (инвариант:
    #блокирующий вызов в `async def` останавливает весь сервер).
    if storage.mode() == "local":
        storage.local_ensure_dir()
        token = storage.local_token(att_id, "put", storage.UPLOAD_TTL_S)
        return {"attachment_id": att_id, "method": "POST", "form_field": "file",
                "url": f"/web/messenger/uploads/local/{att_id}?t={token}"}
    return {"attachment_id": att_id, "url": storage.upload_url(key, mime),
            "method": "PUT", "headers": {"Content-Type": mime}}


@router.post("/uploads/local/{att_id}")
def upload_local(att_id: str, t: str = Query(""), file: UploadFile = File(...),
                 db: Session = Depends(get_db)):
    """Приём файла при локальном хранении.

    ⚠️ Обычный `def`, а не `async def`: запись файла на диск блокирующая, и в
    асинхронной ручке она встала бы поперёк цикла событий — то есть подвесила бы
    журнал, расписание и `/health` на всё время загрузки (инвариант в CLAUDE.md,
    куплен настоящим дефектом с Whisper). Синхронные ручки FastAPI уводит в пул потоков.

    ⚠️ Токена достаточно, обычная авторизация здесь НЕ нужна и была бы вредна: ссылку
    выдал `sign_upload` уже после проверки участия и роли, а требовать заголовок с
    токеном от загрузки файла значит запретить будущие способы загрузки (форма, фоновая
    докачка). Подпись привязана к id файла, действию и сроку.
    """
    if not storage.local_token_ok(att_id, "put", t):
        raise HTTPException(status_code=403, detail="Ссылка недействительна")
    a = db.query(Attachment).filter(Attachment.id == att_id).first()
    if a is None or a.ready:
        #Повторная загрузка в уже подтверждённое вложение — способ подменить файл
        #под чужим сообщением. Один id — одна загрузка.
        raise HTTPException(status_code=404, detail="Вложение не найдено")

    storage.local_ensure_dir()
    written = 0
    limit = storage.MAX_SIZE
    with open(storage.local_path(att_id), "wb") as out:
        while True:
            chunk = file.file.read(1024 * 256)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                #⚠️ Режем ПО ХОДУ, а не проверяем в конце: иначе пятигигабайтный файл
                #сначала ляжет на диск и только потом будет отвергнут.
                out.close()
                storage.local_delete(att_id)
                raise HTTPException(status_code=413, detail="Файл слишком большой")
            out.write(chunk)
    return {"ok": True, "size": written}


@router.get("/uploads/local/{att_id}")
def download_local(att_id: str, t: str = Query(""), db: Session = Depends(get_db)):
    """Отдача файла при локальном хранении. Доступ — по подписи, как и у загрузки."""
    if not storage.local_token_ok(att_id, "get", t):
        raise HTTPException(status_code=403, detail="Ссылка недействительна")
    a = db.query(Attachment).filter(Attachment.id == att_id).first()
    if a is None or not a.ready:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    path = storage.local_path(att_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Файл не найден")
    #Имя подставляем заголовком: на диске файл лежит под id, а человек ждёт своё имя.
    return FileResponse(path, media_type=a.mime or "application/octet-stream",
                        filename=a.name or att_id)


@router.post("/uploads/{att_id}/done")
def confirm_upload(att_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """«Файл доехал» — и сервер ПРОВЕРЯЕТ, что доехал именно тот файл.

    🔥 Одной декларации мало (находка Полковника 25.08.2026). Подписанный PUT не умеет
    ограничивать РАЗМЕР — `content-length-range` есть только у POST-policy. Значит по
    ссылке, выданной под «конспект.txt, 1 КБ», можно было положить пятигигабайтный
    исполняемый файл: наши 413/415 проверяли ЗАЯВЛЕННОЕ, а не то, что легло.

    Тип теперь связан подписью (`storage.presign` подписывает `content-type`), а размер
    сверяем здесь по факту. Не сошлось — объект УДАЛЯЕМ и готовым не считаем: оставить
    его значит оплачивать чужое хранилище и держать в бакете то, чего мы не разрешали.

    ⚠️ Хранилище может не ответить на HEAD (сеть, права). Тогда доверяем заявленному:
    отказать из-за сетевой заминки — сломать отправку на ровном месте. Разница с
    прежним поведением в том, что ЛОЖЬ теперь ловится, а раньше не ловилась никогда.
    """
    a = db.query(Attachment).filter(Attachment.id == att_id).first()
    if a is None or a.uploader_id != user.id:
        raise HTTPException(status_code=404, detail="Вложение не найдено")

    #Факт проверяем тем способом, каким хранили: у объектного хранилища HEAD,
    #у локального — размер файла на диске. Одна проверка на оба пути.
    real = (storage.local_stat(att_id) if storage.mode() == "local"
            else storage.head_object(a.storage_key))
    if real:
        bad_size = real.get("size", 0) > storage.MAX_SIZE
        bad_mime = bool(real.get("mime")) and not storage.mime_ok(real["mime"])
        if bad_size or bad_mime:
            storage.remove(att_id, a.storage_key)
            db.delete(a)
            db.commit()
            raise HTTPException(status_code=413 if bad_size else 415,
                                detail="Файл не соответствует заявленному")
        #Размер записываем НАСТОЯЩИЙ: в списке файлов человек должен видеть правду.
        if real.get("size"):
            a.size = real["size"]

    a.ready = True
    db.commit()
    return {"ok": True, "attachment": _att_out(a)}


@router.get("/attachments/{att_id}/url")
def attachment_url(att_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Ссылка на СКАЧИВАНИЕ — только участнику той беседы, куда файл отправлен."""
    a = db.query(Attachment).filter(Attachment.id == att_id).first()
    if a is None or not a.ready:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    _require_participant(db, a.conversation_id, user)
    if not storage.configured():
        raise HTTPException(status_code=503, detail="Хранилище файлов не настроено")
    if storage.mode() == "local":
        token = storage.local_token(att_id, "get", storage.DOWNLOAD_TTL_S)
        return {"url": f"/web/messenger/uploads/local/{att_id}?t={token}",
                "attachment": _att_out(a)}
    #Имя и тип уходят в ссылку подписанными: браузер сохранит файл под настоящим
    #именем, а не под ключом `att:<hex>` без расширения.
    return {"url": storage.download_url(a.storage_key, a.name, a.mime),
            "attachment": _att_out(a)}


@router.get("/chats/{conv_id}/files")
def chat_files(conv_id: str, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Вкладка «Файлы»: документы беседы.

    ⚠️ Тумбстоуны отбрасываем: удалённое сообщение не должно возвращаться списком файлов
    — иначе «удалил у всех» перестанет что-либо значить.
    """
    _require_participant(db, conv_id, user)
    rows = (db.query(Message, Attachment)
            .join(Attachment, Attachment.id == Message.attachment_id)
            .filter(Message.conversation_id == conv_id,
                    Message.deleted_at == "",
                    Attachment.ready == True)                     # noqa: E712
            .order_by(Message.created_at.desc()).limit(300).all())
    return {"files": [{**_att_out(a), "message_id": m.id, "sent_at": m.created_at}
                      for m, a in rows]}


@router.get("/chats/{conv_id}/media")
def chat_media(conv_id: str, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Вкладка «Медиа»: гифки и видео-ссылки.

    ⚠️ Видео у нас живёт ССЫЛКОЙ на видеохостинг, а не файлом (механизм А плана) —
    поэтому «медиа» собирается из тела сообщений, а не из вложений. Складывать это в
    одну вкладку с документами нельзя: человек ищет «ту картинку» и «тот документ» в
    разных местах.
    """
    _require_participant(db, conv_id, user)
    rows = (db.query(Message)
            .filter(Message.conversation_id == conv_id, Message.deleted_at == "")
            .order_by(Message.created_at.desc()).limit(500).all())
    out = []
    for m in rows:
        if m.kind == "gif":
            out.append({"message_id": m.id, "kind": "gif", "url": m.body,
                        "sent_at": m.created_at, "sender_id": m.sender_id})
            continue
        body = m.body or ""
        if any(h in body for h in _VIDEO_HOSTS) and "http" in body:
            out.append({"message_id": m.id, "kind": "video", "url": body,
                        "sent_at": m.created_at, "sender_id": m.sender_id})
    return {"media": out[:200]}


@router.get("/chats/{conv_id}/saved")
def chat_saved(conv_id: str, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Вкладка «Избранное»: что ИЗ ЭТОЙ беседы человек унёс к себе в «Избранное».

    ⚠️ Смотрим на `fwd_from_conv_id` пересланных сообщений в СОБСТВЕННОМ чате-избранном.
    Никакой новой сущности заводить не пришлось: пересылка и так помнит источник.

    ⚠️ Ответ строго ЛИЧНЫЙ. «Избранное» — чат с самим собой, и показать его содержимое
    другим участникам беседы значило бы раскрыть, что человек счёл важным.
    """
    _require_participant(db, conv_id, user)
    saved = (db.query(Conversation)
             .filter(Conversation.kind == "saved", Conversation.owner_id == user.id)
             .first())
    if saved is None:
        return {"saved": []}
    rows = (db.query(Message)
            .filter(Message.conversation_id == saved.id,
                    Message.fwd_from_conv_id == conv_id,
                    Message.deleted_at == "")
            .order_by(Message.created_at.desc()).limit(200).all())
    #Карточка автора (аватарка, полное имя, цвет плашки, роль) — ОДНИМ запросом на всю
    #вкладку, а не по строке на сообщение: сохранённых бывает две сотни, и N+1 здесь
    #упёрся бы в единственное ядро боевой машины ровно при открытии панели.
    #⚠️ `fwd_sender_name` остаётся ЗАПАСНЫМ именем и не выбрасывается: автор мог быть
    #удалён после пересылки, а снимок имени в сообщении переживает удаление аккаунта.
    ids = {m.fwd_from_sender_id for m in rows if m.fwd_from_sender_id}
    authors = {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    out = []
    for m in rows:
        u = authors.get(m.fwd_from_sender_id)
        prefs = (u.prefs if u is not None and isinstance(u.prefs, dict) else {})
        out.append({
            "message_id": m.id, "body": m.body, "kind": m.kind,
            #ДВЕ разных метки, и путать их нельзя: `sent_at` — когда автор написал
            #сообщение в беседе (её и показываем, о ней спрашивают «время отправки»),
            #`saved_at` — когда читатель унёс его к себе. У старых строк снимка времени
            #оригинала нет вовсе (поле завели позже) — тогда честно падаем на вторую.
            "sent_at": m.fwd_from_created_at or m.created_at,
            "saved_at": m.created_at,
            "from_id": m.fwd_from_sender_id or "",
            "from_name": (u.full_name or u.name or "") if u is not None else (m.fwd_sender_name or ""),
            "from_role": (u.role if u is not None else ""),
            "from_avatar": prefs.get("avatar", "") or "",
            "from_color": prefs.get("profile_color", "") or "",
        })
    return {"saved": out}


@router.get("/uploads/limits")
def upload_limits(user: User = Depends(get_current_user)):
    """Что клиенту показывать до выбора файла: потолок и типы.

    ⚠️ Отдаём с сервера, а не хардкодим в браузере: потолок настраивается переменной
    окружения, и вторая копия числа разошлась бы с первой ровно в тот день, когда его
    поменяют.
    """
    return {"configured": storage.configured(),
            #Способ полезен не для красоты: по нему видно, почему файлы не работают —
            #«места мало» и «ключи не заданы» лечатся по-разному.
            "mode": storage.mode(),
            "free_bytes": storage.free_bytes(),
            "max_size": storage.MAX_SIZE,
            "mime": sorted(storage.ALLOWED_MIME),
            "ext": sorted(set(storage.ALLOWED_MIME.values()))}
