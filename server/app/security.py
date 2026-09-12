"""
security.py (бэкенд) — Хеширование паролей и JWT-токены.

Формат хеша и алгоритм ПОЛНОСТЬЮ СОВПАДАЮТ с десктопом (security.py):
    hybrid_sha512_gost512$<sha_iters>-<gost_iters>$<salt_hex>$<hash_hex>
Поэтому хеши, созданные в проге, проверяются на сервере и наоборот (offline-first:
пользователей заводит админ в десктопе, они синхронизируются на сервер уже хешами).

Двухэтапный хеш: PBKDF2-HMAC-SHA512 (этап 1, стойкость) → PBKDF2-Стрибог512 по
Р 50.1.111-2016 (этап 2, ГОСТ). ГОСТ-бэкенд авто: OpenSSL GOST-engine (ViPNet/
gost-engine — бой, C-скорость) → gostcrypto (pure-Python — dev). Подробности и
обоснование — в десктопном security.py и docs/STRIBOG_152FZ_PLAN.md.
"""
import hashlib
import hmac
import os
import secrets
import time

from jose import jwt, JWTError

from .config import JWT_SECRET, JWT_ALG, JWT_TTL_MIN, JWT_REFRESH_TTL_MIN

#🔑 МИНИМАЛЬНАЯ ДЛИНА ПАРОЛЯ — ОДНО ЧИСЛО НА ВЕСЬ ПРОДУКТ.
#До 12.09.2026 восьмёрка была вписана ТРЕМЯ отдельными литералами (заведение первого
#админа, смена пароля по письму, консольное заведение модератора). Снимок значения в
#нескольких местах расходится молча и всегда — у нас это уже стоило строки версии и
#счётчика тестов. А решение поднять требование (Argos-разбор Ярослава, §3.6: Argon2id и
#минимум 15 символов) обязано быть правкой ОДНОЙ строки, а не охотой по файлам.
#⚠️ Здесь только ДЛИНА. Любое усиление правила (словарь, классы символов) — тоже сюда, а
#не рядом с очередным вызывающим.
MIN_PASSWORD_LEN = 8

_SHA_ITERS = 200_000
_HYBRID_TAG = "hybrid_sha512_gost512"
_OPENSSL_GOST_NAMES = ("streebog512", "gost2012_512", "md_gost12_512")
_LEGACY_SUPPORTED = ("sha256", "sha512")   #старые одноэтапные хеши, которые ещё проверяем


def _openssl_gost_name():
    """Имя доступного ГОСТ-дайджеста в OpenSSL (движок/провайдер) или None.

    ВАЖНО: проверяем через hashlib.new(), а НЕ по algorithms_available. На OpenSSL 3.0
    (Ubuntu 24.04) дайджесты от gost-провайдера доступны для new()/pbkdf2_hmac, но в
    набор algorithms_available НЕ попадают (там только дайджесты дефолт-провайдера) —
    из-за этого прежняя проверка «n in algorithms_available» не находила ГОСТ, и хеш
    считался медленным pure-Python'ом даже при установленном gost-engine. Результат
    кэшируем: бэкенд не меняется в течение процесса."""
    global _gost_name_cache
    if _gost_name_cache is not False:
        return _gost_name_cache
    _gost_name_cache = None
    for n in _OPENSSL_GOST_NAMES:
        try:
            hashlib.new(n)
            _gost_name_cache = n
            break
        except Exception:
            continue
    return _gost_name_cache


_gost_name_cache = False   #False = ещё не определяли; None/строка — результат детекции


def _gost_pbkdf2(password: bytes, salt: bytes, iters: int) -> bytes:
    """PBKDF2-HMAC-Стрибог512 (Р 50.1.111-2016), 64 байта. OpenSSL GOST-engine →
    gostcrypto (fallback). Идентичен десктопному _gost_pbkdf2 → хеши совместимы."""
    name = _openssl_gost_name()
    if name:
        return hashlib.pbkdf2_hmac(name, password, salt, iters, dklen=64)
    try:
        from gostcrypto import gostpbkdf
    except ImportError as e:
        raise RuntimeError(
            "Нет ГОСТ-бэкенда: на бою нужен OpenSSL GOST-engine (ViPNet/gost-engine), "
            "на dev — пакет gostcrypto (pip install gostcrypto).") from e
    return gostpbkdf.new(password, salt=salt, counter=iters).derive(64)


def _gost_iters() -> int:
    """Итераций ГОСТ при создании хеша: GRADEBOOK_GOST_ITERS, иначе 2000 на быстром
    OpenSSL-бэкенде (бой) и мало на медленном pure-Python (dev)."""
    env = os.environ.get("GRADEBOOK_GOST_ITERS", "").strip()
    if env.isdigit():
        return max(1, int(env))
    return 2000 if _openssl_gost_name() else 2


def hash_password(password: str) -> str:
    if password is None:
        password = ""
    salt = secrets.token_bytes(16)
    gi = _gost_iters()
    inter = hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt, _SHA_ITERS)
    final = _gost_pbkdf2(inter, salt, gi)
    return f"{_HYBRID_TAG}${_SHA_ITERS}-{gi}${salt.hex()}${final.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    try:
        tag, iters_field, salt_hex, hash_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        pw = (password or "").encode("utf-8")
        if tag == _HYBRID_TAG:
            sha_iters, gost_iters = (int(x) for x in iters_field.split("-"))
            inter = hashlib.pbkdf2_hmac("sha512", pw, salt, sha_iters)
            dk = _gost_pbkdf2(inter, salt, gost_iters)
        elif tag.startswith("pbkdf2_"):
            hash_name = tag.split("_", 1)[1]
            if hash_name not in _LEGACY_SUPPORTED:
                return False
            dk = hashlib.pbkdf2_hmac(hash_name, pw, salt, int(iters_field))
        else:
            return False
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def new_jti() -> str:
    """Уникальный идентификатор токена (для чёрного списка/отзыва и видимости сессий)."""
    return secrets.token_urlsafe(16)


def create_token_full(subject: str, role: str, typ: str = "access",
                      ttl_min: int = None, jti: str = None) -> tuple:
    """Подписанный JWT с jti и типом. Возвращает (token, jti, exp_unix).

    typ: 'access' (короткий, для запросов) | 'refresh' (длинный, для тихого обновления).
    jti кладём в payload, чтобы сервер мог отозвать конкретный токен (см. AuthSession)."""
    now = int(time.time())
    if ttl_min is None:
        ttl_min = JWT_TTL_MIN if typ == "access" else JWT_REFRESH_TTL_MIN
    jti = jti or new_jti()
    exp = now + ttl_min * 60
    payload = {"sub": subject, "role": role, "typ": typ,
               "jti": jti, "iat": now, "exp": exp}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG), jti, exp


def decode_token(token: str) -> dict:
    """Возвращает payload или None, если токен невалиден/просрочен."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except JWTError:
        return None
