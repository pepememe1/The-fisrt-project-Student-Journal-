"""Объектное хранилище для вложений мессенджера (S3-совместимое).

━━ ГЛАВНОЕ ПРАВИЛО: ФАЙЛ ЧЕРЕЗ НАШ СЕРВЕР НЕ ПРОХОДИТ ━━
Ни при загрузке, ни при скачивании. Браузер работает с хранилищем НАПРЯМУЮ по временной
подписанной ссылке, а мы храним только метаданные — имя, размер, тип, кто и в какой
беседе. Это не оптимизация, а условие работоспособности: на боевой машине ОДНО ядро и
ОДИН процесс uvicorn, который раздаёт журнал. Прогони через него пару лекций — и лягут
оценки с расписанием, а не мессенджер (замеры и разбор — docs/done/MESSENGER-ATTACHMENTS-PLAN.md).

━━ ПОЧЕМУ БЕЗ boto3 ━━
Подпись AWS SigV4 — это несколько вызовов hmac/sha256, здесь она занимает полсотни строк.
boto3 тянет botocore и десятки мегабайт в образ ради одной операции «подпиши ссылку».
На машине с 960 МБ ОЗУ и на сборке .exe это заметно, а выгоды нет никакой.

━━ ГДЕ ХРАНИМ (152-ФЗ) ━━
Только РФ: Yandex Object Storage, VK Cloud, Selectel, Timeweb — все S3-совместимы,
поэтому код от провайдера не зависит и переезд стоит только новых ключей.
⚠️ Cloudflare R2 / AWS нельзя: данные учащихся обязаны оставаться в России.

⚠️ Пока ключи не заданы, `configured()` отдаёт False, и ручки честно отвечают «хранилище
не настроено». Это НЕ заглушка «потом доделаем»: молча принять файл и потерять его —
хуже отказа, потому что человек будет уверен, что отправил.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import shutil
from datetime import datetime, timezone
from urllib.parse import quote

# ━━ СПОСОБ ХРАНЕНИЯ ВЫБИРАЕТСЯ САМ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# 🔥 Требование Влада (25.08.2026): «когда переедем, хранилище сразу будет большое —
# сделай, чтобы при переезде лишних настроек не делать». Поэтому решает не человек, а
# сама машина:
#
#   1. заданы ключи S3 → работаем через объектное хранилище (файл идёт мимо нас);
#   2. иначе смотрим СВОБОДНОЕ МЕСТО. Хватает — храним у себя на диске;
#   3. не хватает — вложения честно выключены (503), как сейчас на боевом VPS.
#
# ⚠️ Порог по свободному месту, а не тумблер, и это принципиально. Тумблер придётся
# вспомнить и переключить — а забудут ровно в день переезда, и «файлы почему-то не
# работают» будут искать в коде. Место машина знает про себя сама.
#
# ⚠️ Честная граница этого приёма: свободный диск — ПРОКСИ для «машина потянет», а не
# доказательство. Второй половиной возражения было одно ядро (файлы идут через тот же
# процесс, что раздаёт журнал), и её порогом по диску не измерить. Поэтому есть ручной
# перекрыватель `GRADEBOOK_FILES_MODE` (off | local | s3 | auto): если окажется, что
# машина большая по диску и слабая по процессору, режим ставится явно, а не правкой кода.
MODE = os.environ.get("GRADEBOOK_FILES_MODE", "auto").strip().lower()

#Куда складывать при локальном хранении. Рядом с развёртыванием, а не в /tmp: /tmp
#вычищается перезагрузкой, и переписка молча лишилась бы вложений.
LOCAL_DIR = os.environ.get(
    "GRADEBOOK_FILES_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "attachments"),
)

#Сколько свободного места считать достаточным. 20 ГБ — не «много», а порог, ниже
#которого хранение файлов начинает угрожать САМОМУ ЖУРНАЛУ: забитый диск роняет не
#мессенджер, а весь сайт и API. На боевом VPS свободно 2.9 ГБ, то есть там режим
#останется выключенным сам собой — ровно как и задумано.
LOCAL_MIN_FREE_BYTES = int(os.environ.get("GRADEBOOK_FILES_MIN_FREE_GB", "20")) * 1024 ** 3

#Настройки S3 — из окружения, как всё остальное (см. config.py).
ENDPOINT = os.environ.get("GRADEBOOK_S3_ENDPOINT", "").strip().rstrip("/")
REGION = os.environ.get("GRADEBOOK_S3_REGION", "ru-central1").strip()
BUCKET = os.environ.get("GRADEBOOK_S3_BUCKET", "").strip()
from . import secrets_source
ACCESS_KEY = secrets_source.get("GRADEBOOK_S3_KEY")
SECRET_KEY = secrets_source.get("GRADEBOOK_S3_SECRET")

#Потолок размера. 25 МБ — это учебный документ, презентация или скан, но не видео:
#видео живёт ссылкой на видеохостинг (механизм А в плане), и так и должно остаться.
MAX_SIZE = int(os.environ.get("GRADEBOOK_S3_MAX_MB", "25")) * 1024 * 1024

#━━ СУТОЧНЫЙ ПОТОЛОК НА ЧЕЛОВЕКА ━━
#🔥 Находка пентеста 3.7.8, пункт 2: потолок на ОДИН файл есть, а на пользователя за
#сутки — нет. Значит один аккаунт кладёт сорок файлов по 25 МБ за вечер и занимает
#хранилище целиком; остальным приходит честный отказ «места нет», и выглядит это как
#поломка сервера, а не как чьё-то злоупотребление.
#⚠️ В отчёте пункт был отложен с пометкой «модуль ещё не подключён» — и это перестало
#быть правдой 25.08.2026, когда вложения заработали. Отложенная находка не отменяется
#тем, что код переписали: `uploads.py` исчез, дыра переехала в `storage.py`.
#
#200 МБ в сутки: двадцать пять учебных документов по 8 МБ. Для человека это заведомо
#больше, чем нужно за день, для скрипта — заведомо мало, чтобы забить диск.
MAX_USER_DAY_BYTES = int(os.environ.get("GRADEBOOK_FILES_USER_DAY_MB", "200")) * 1024 * 1024

#Сколько живёт подписанная ссылка. Загрузка — дольше (большой файл на слабом канале),
#скачивание — короче: ссылка утекает пересылкой в чужой чат, и час чужого доступа
#заметно хуже пятнадцати минут.
UPLOAD_TTL_S = 900
DOWNLOAD_TTL_S = 900

#⚠️ БЕЛЫЙ список типов, а не чёрный. Чёрный всегда неполон, и первым же пропущенным
#типом окажется исполняемый. Картинки и видео сюда НЕ входят намеренно (см. выше).
ALLOWED_MIME = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/rtf": ".rtf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/json": ".json",
    "application/zip": ".zip",
}


def s3_ready() -> bool:
    return bool(ENDPOINT and BUCKET and ACCESS_KEY and SECRET_KEY)


def free_bytes(path: str = "") -> int:
    """Свободное место там, где будем хранить. 0 — узнать не удалось."""
    try:
        target = path or LOCAL_DIR
        probe = target if os.path.isdir(target) else os.path.dirname(target) or "."
        return shutil.disk_usage(probe).free
    except Exception:
        return 0


def local_ready() -> bool:
    """Хватает ли места, чтобы хранить вложения у себя.

    ⚠️ Каталог создаём ЛЕНИВО и только когда собираемся им пользоваться: пустая папка
    на боевой машине, где режим всё равно выключен, — лишний повод решить, что файлы
    работают.
    """
    return free_bytes() >= LOCAL_MIN_FREE_BYTES


def mode() -> str:
    """`s3` | `local` | `off`. Решает машина, а не человек (см. шапку файла)."""
    if MODE in ("off", "local", "s3"):
        #Явно заданный режим уважаем, но не притворяемся, что он работает: без ключей
        #S3 и без места на диске отвечать «готово» значит принять файл и потерять его.
        if MODE == "s3":
            return "s3" if s3_ready() else "off"
        if MODE == "local":
            return "local" if free_bytes() > 0 else "off"
        return "off"
    if s3_ready():
        return "s3"
    return "local" if local_ready() else "off"


def configured() -> bool:
    """Готово ли хранилище хоть каким-нибудь способом."""
    return mode() != "off"


def mime_ok(mime: str) -> bool:
    return (mime or "").split(";")[0].strip().lower() in ALLOWED_MIME


def _sign_key(secret: str, date: str, region: str, service: str) -> bytes:
    k = ("AWS4" + secret).encode()
    for part in (date, region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    return k


def presign(method: str, key: str, expires: int, *, content_type: str = "",
            extra: dict = None) -> str:
    """Временная ссылка на объект: подпись SigV4 в query-параметрах.

    ⚠️ `UNSIGNED-PAYLOAD` намеренно: тело подписывать нельзя — при загрузке браузер шлёт
    файл, которого у нас нет и быть не должно. Целостность обеспечивает TLS, а доступ —
    сама подпись и её срок.

    ⚠️ Ключ объекта кодируем с `safe="/"`: слэши в пути обязаны остаться слэшами, иначе
    хранилище увидит один объект со странным именем вместо папки.
    """
    if not s3_ready():
        raise RuntimeError("объектное хранилище не настроено")

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    host = ENDPOINT.split("://", 1)[-1]
    scope = f"{date}/{REGION}/s3/aws4_request"

    canon_uri = f"/{BUCKET}/{quote(key, safe='/')}"

    #🔥 ТИП СОДЕРЖИМОГО ВХОДИТ В ПОДПИСЬ (находка Полковника 25.08.2026). Раньше
    #`content_type` принимался и НИГДЕ не использовался: подпись связывала только хост,
    #поэтому по ссылке, выданной под «конспект.txt, text/plain», можно было залить
    #исполняемый файл любого размера — проверки жили только в нашей декларации, а не в
    #самом разрешении. Теперь заголовок подписан, и хранилище отвергнет запрос с другим
    #типом: клиент ОБЯЗАН прислать ровно тот `Content-Type`, под который подписано.
    #⚠️ Размер так связать нельзя: `content-length-range` есть только у POST-policy, а мы
    #грузим PUT'ом. Поэтому размер проверяется ПОСЛЕ загрузки (`head_object` +
    #`confirm_upload`): не сошёлся — объект удаляется и готовым не считается.
    signed = {"host": host}
    if content_type:
        signed["content-type"] = content_type
    signed_names = ";".join(sorted(signed))

    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{ACCESS_KEY}/{scope}",
        "X-Amz-Date": stamp,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": signed_names,
    }
    params.update(extra or {})
    canon_qs = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}"
                        for k, v in sorted(params.items()))
    canon_headers = "".join(f"{k}:{signed[k]}\n" for k in sorted(signed))
    canon_req = "\n".join([
        method.upper(), canon_uri, canon_qs,
        canon_headers, signed_names, "UNSIGNED-PAYLOAD",
    ])
    to_sign = "\n".join([
        "AWS4-HMAC-SHA256", stamp, scope,
        hashlib.sha256(canon_req.encode()).hexdigest(),
    ])
    signature = hmac.new(_sign_key(SECRET_KEY, date, REGION, "s3"),
                         to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{ENDPOINT}{canon_uri}?{canon_qs}&X-Amz-Signature={signature}"


def upload_url(key: str, content_type: str) -> str:
    return presign("PUT", key, UPLOAD_TTL_S, content_type=content_type)


def download_url(key: str, name: str = "", mime: str = "") -> str:
    """Ссылка на скачивание. Имя файла подставляем ЗАГОЛОВКОМ ответа.

    🔥 Докстринг `object_key` обещал это с самого начала, а кода не было (находка
    Полковника 25.08.2026): браузер сохранял объект под ключом `att:<hex>` — без имени и
    без расширения. Предпросмотр это скрывал, потому что тянет байты сам, так что
    заметили бы только на живом хранилище.

    ⚠️ `response-content-disposition` — подписанный query-параметр, поэтому подменить его
    в уже выданной ссылке нельзя. Имя кодируем по RFC 5987 (`filename*`): в наших именах
    кириллица, а сырой заголовок её не переживёт.
    """
    extra = {}
    if name:
        safe = quote(name, safe="")
        extra["response-content-disposition"] = f"attachment; filename*=UTF-8''{safe}"
    if mime:
        extra["response-content-type"] = mime
    return presign("GET", key, DOWNLOAD_TTL_S, extra=extra)


def head_object(key: str) -> dict:
    """Настоящие размер и тип объекта в хранилище.

    ⚠️ Нужен потому, что подписанный PUT не умеет ограничивать РАЗМЕР: `content-length-
    range` есть только у POST-policy. Значит единственная честная проверка — посмотреть,
    что реально легло, и отвергнуть несовпадение (см. `confirm_upload`).
    """
    if not s3_ready():
        return {}
    import urllib.error
    import urllib.request
    req = urllib.request.Request(presign("HEAD", key, 300), method="HEAD")
    try:
        #SAST B310: ссылку подписали мы сами (`presign`) из GRADEBOOK_S3_ENDPOINT.
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            return {"size": int(resp.headers.get("Content-Length") or 0),
                    "mime": (resp.headers.get("Content-Type") or "").split(";")[0].strip()}
    except urllib.error.HTTPError:
        return {}
    except Exception:
        return {}


# ━━ ЛОКАЛЬНОЕ ХРАНЕНИЕ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# Тот же порядок работы, что и с объектным хранилищем: клиент получает ССЫЛКУ и сам
# кладёт по ней файл. Разница только в том, что ссылка ведёт к нам. Так клиентский код
# один на оба способа, и при переезде на большую машину менять в браузере нечего.
#
# ⚠️ Ссылка подписана и живёт минуты — ровно как у S3. Без подписи это была бы дыра:
# `PUT /uploads/local/att:<id>` угадывается, а «он же знает id» защитой не является.


def _local_secret() -> bytes:
    """Ключ подписи локальных ссылок — тот же секрет, что у JWT.

    ⚠️ Отдельный ключ пришлось бы отдельно заводить, отдельно хранить и отдельно
    ротировать; ещё один секрет в `.env` — ещё одно место, где однажды окажется
    значение по умолчанию.
    """
    from .config import JWT_SECRET
    return str(JWT_SECRET).encode()


def local_path(att_id: str) -> str:
    """Файл на диске. Имя — ТОЛЬКО id: имя от человека в путь не попадает никогда."""
    safe = "".join(c for c in att_id if c.isalnum() or c in "-_")[:80]
    return os.path.join(LOCAL_DIR, safe)


def local_token(att_id: str, action: str, ttl: int) -> str:
    """Подпись «этому файлу, на это действие, до этого времени»."""
    exp = int(datetime.now(timezone.utc).timestamp()) + ttl
    msg = f"{att_id}|{action}|{exp}".encode()
    sig = hmac.new(_local_secret(), msg, hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{sig}"


def local_token_ok(att_id: str, action: str, token: str) -> bool:
    """Проверка подписи. Срок и действие входят в неё, поэтому подменить нечего."""
    try:
        exp_s, sig = str(token or "").split(".", 1)
        exp = int(exp_s)
    except Exception:
        return False
    if exp < int(datetime.now(timezone.utc).timestamp()):
        return False
    msg = f"{att_id}|{action}|{exp}".encode()
    want = hmac.new(_local_secret(), msg, hashlib.sha256).hexdigest()[:32]
    #⚠️ Сравнение постоянного времени: обычное `==` по строке подписи утекает её побайтно.
    return hmac.compare_digest(want, sig)


def local_ensure_dir() -> None:
    os.makedirs(LOCAL_DIR, exist_ok=True)


def local_stat(att_id: str) -> dict:
    """Размер и наличие файла на диске — аналог `head_object` для локального способа."""
    try:
        return {"size": os.path.getsize(local_path(att_id))}
    except OSError:
        return {}


def local_delete(att_id: str) -> bool:
    try:
        os.remove(local_path(att_id))
        return True
    except FileNotFoundError:
        return True                      #уже нет — задача выполнена
    except OSError:
        return False


def remove(att_id: str, storage_key: str) -> bool:
    """Удалить объект тем способом, каким он хранится. Одна дверь для уборки."""
    return local_delete(att_id) if mode() == "local" else delete_object(storage_key)


def object_key(conv_id: str, att_id: str) -> str:
    """Путь объекта. Беседа в пути — чтобы уборку и разбор инцидента можно было сделать
    по префиксу, не поднимая базу.

    ⚠️ Имя файла в ключ НЕ кладём: оно приходит от человека, содержит что угодно вплоть
    до `../`, и путь в хранилище — последнее место, где стоит доверять такому вводу.
    Настоящее имя живёт в базе и подставляется заголовком при скачивании.
    """
    safe_conv = "".join(c for c in conv_id if c.isalnum() or c in "-_:")[:64]
    return f"messenger/{safe_conv}/{att_id}"


def delete_object(key: str) -> bool:
    """Удалить объект. Зовётся уборкой по сроку, а не удалением сообщения.

    ⚠️ Почему не сразу: сообщения удаляются ТУМБСТОУНОМ ради модерации — жалоба обязана
    показать оригинал, и автор не должен заметать следы, удалив сообщение после жалобы.
    Файл — часть сообщения, значит правило то же. Физически стираем позже, когда ссылок
    на вложение не осталось и срок разбора вышел.
    """
    if not s3_ready():
        return False
    import urllib.error
    import urllib.request
    req = urllib.request.Request(presign("DELETE", key, 300), method="DELETE")
    try:
        #SAST B310: ссылка подписана нами же (`presign`).
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        return e.code == 404          #уже нет — считаем, что задача выполнена
    except Exception:
        return False
