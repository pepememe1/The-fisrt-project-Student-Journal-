#!/usr/bin/env python3
"""rotate_db_key.py — БЕЗОПАСНАЯ ротация ключа SQLCipher (GRADEBOOK_DB_KEY).

Зачем: ключ шифрования базы за всё время ни разу не менялся. Компрометация .env, увольнение
человека, знавшего ключ, или просто гигиена (152-ФЗ, регламент смены ключей) требуют
возможности сменить ключ, не потеряв данные. Механизма не было вовсе — это самый весомый
оставшийся пункт технического минимума и самый опасный: ошибка = нечитаемая база.

Отсюда — предельная осторожность. Ротация НИКОГДА не идёт по живому файлу «на месте»:

  ──────────────────────────────────────────────────────────────────────────────────────
  РЕЖИМЫ
  ──────────────────────────────────────────────────────────────────────────────────────
  --check   (по умолчанию)  Проверка МЕХАНИЗМА, не трогающая ничего живого:
            снимает КОПИЮ базы, перешифровывает её СЛУЧАЙНЫМ одноразовым ключом, проверяет
            (integrity_check + есть пользователи + СТАРЫЙ ключ копию больше НЕ открывает),
            затем копию удаляет. Живую базу и .env не касается. Это и есть «тест» на
            реальном ключе/драйвере — крипту нельзя замокать, её надо прогнать по-честному.

  --apply   Настоящая ротация. ТРЕБУЕТ остановленного сервиса (иначе отказ). Порядок:
            1) резервная копия живой базы И .env (в /root/gb-backups);
            2) снимок → перешифровка снимка НОВЫМ ключом → проверка;
            3) только теперь атомная подмена живого файла + запись нового ключа в .env.
            Если что-то падает до шага 3 — живая база и .env НЕ изменены.

Ключи целиком не печатаются (только префикс для опознания) и в логи не уходят.
"""
import argparse
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone


def _read_env(env_path: str) -> dict:
    """Разбирает .env в словарь (KEY=VALUE), снимая кавычки и CR."""
    out = {}
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n").rstrip("\r")
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _db_path_from_url(url: str) -> str:
    """sqlite:///path → path. Для не-sqlite ротация этим инструментом не поддержана."""
    if not url.startswith("sqlite"):
        raise SystemExit(f"Ротация поддержана только для SQLite/SQLCipher, а URL = {url!r}")
    return url.split("sqlite:///", 1)[-1]


#🔥 БОЕВОЙ `.env` НЕ СОДЕРЖИТ `GRADEBOOK_DB_URL` ВОВСЕ (проверено на машине 05.09.2026).
#Сервер и не требует его: `app/config.py` имеет УМОЛЧАНИЕ, и на бою работает именно оно.
#А инструмент ротации читал только `.env` — то есть единственный механизм смены ключа
#не находил боевую базу и падал сообщением «Ротация поддержана только для
#SQLite/SQLCipher, а URL = ''», которое указывает совсем не на ту причину.
#⚠️ Отказ был бы обнаружен ровно в день компрометации ключа — то есть в худший момент.
#Литерал обязан совпадать с умолчанием `app/config.py`; расхождение держит
#`server/tests/test_rotate_db_key.py::test_default_db_url_matches_the_server_default`.
_DEFAULT_DB_URL = "sqlite:///./gradebook_server.db"


def _db_path_from_env(env_path: str, url: str) -> str:
    """Путь к файлу БД: из `.env`, а при его молчании — умолчание самого сервера.

    Умолчание сервера относительное (`./gradebook_server.db`) и считается от РАБОЧЕГО
    каталога службы, то есть от каталога рядом с `.env`. Раскрываем его именно так, а не
    от текущего каталога вызывающего: иначе запуск из другого места молча взял бы
    несуществующий файл.
    """
    if not (url or "").strip():
        base = os.path.dirname(os.path.abspath(env_path))
        return os.path.join(base, os.path.basename(_db_path_from_url(_DEFAULT_DB_URL)))
    return _db_path_from_url(url)


def _is_hex64(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", s or ""))


def _pfx(key: str) -> str:
    """Короткий префикс ключа для человекочитаемого лога (НЕ раскрывает ключ)."""
    return (key[:6] + "…") if key else "(пусто)"


def _connect(path: str, key: str):
    """SQLCipher-соединение с заданным ключом (тот же способ, что server/app/db.py)."""
    import sqlcipher3
    conn = sqlcipher3.connect(path)
    conn.execute("PRAGMA key = \"x'%s'\"" % key)
    return conn


def _opens_ok(path: str, key: str) -> tuple:
    """(ок, число_пользователей|причина). Ключ верный → integrity_check 'ok' и читается users."""
    try:
        c = _connect(path, key)
        integ = c.execute("PRAGMA integrity_check").fetchone()[0]
        if integ != "ok":
            c.close()
            return False, f"integrity_check={integ!r}"
        n = c.execute("SELECT count(*) FROM users").fetchone()[0]
        c.close()
        return True, n
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _snapshot(src: str, old_key: str, dst: str) -> None:
    """Консистентный зашифрованный снимок (VACUUM INTO) — как в автобэкапе."""
    c = _connect(src, old_key)
    c.execute("VACUUM INTO ?", (dst,))
    c.close()


def _rekey(path: str, old_key: str, new_key: str) -> None:
    """Перешифровать файл на месте: открыть старым ключом, PRAGMA rekey на новый."""
    c = _connect(path, old_key)
    c.execute("PRAGMA rekey = \"x'%s'\"" % new_key)
    c.close()


def _verify_rotation(path: str, old_key: str, new_key: str) -> int:
    """Полная проверка перешифрованного файла. Возвращает число пользователей.
    Падает (SystemExit), если новый ключ не открывает ИЛИ старый ещё открывает."""
    ok_new, info_new = _opens_ok(path, new_key)
    if not ok_new:
        raise SystemExit(f"ПРОВАЛ: новый ключ не открывает копию ({info_new})")
    if not isinstance(info_new, int) or info_new < 1:
        raise SystemExit(f"ПРОВАЛ: в перешифрованной копии нет пользователей ({info_new})")
    #Старый ключ ОБЯЗАН перестать открывать — иначе rekey не сработал, и мы бы оставили
    #базу читаемой прежним (возможно, скомпрометированным) ключом.
    ok_old, _ = _opens_ok(path, old_key)
    if ok_old:
        raise SystemExit("ПРОВАЛ: СТАРЫЙ ключ всё ещё открывает копию — rekey не подействовал")
    return info_new


def _service_active(name: str) -> bool:
    try:
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def cmd_check(db: str, old_key: str) -> None:
    """Проверить механизм, не трогая живое."""
    tmpdir = tempfile.mkdtemp(prefix="gbrotate_")
    snap = os.path.join(tmpdir, "snap.db")
    try:
        throwaway = secrets.token_hex(32)
        print(f"[check] снимок живой базы (ключ {_pfx(old_key)}) → {snap}")
        _snapshot(db, old_key, snap)
        ok, info = _opens_ok(snap, old_key)
        if not ok:
            raise SystemExit(f"снимок не открылся старым ключом: {info}")
        print(f"[check] снимок открыт старым ключом, пользователей: {info}")
        print(f"[check] перешифровка снимка на одноразовый ключ {_pfx(throwaway)}")
        _rekey(snap, old_key, throwaway)
        n = _verify_rotation(snap, old_key, throwaway)
        print(f"[check] OK: новый ключ открывает ({n} польз.), старый — больше нет.")
        print("[check] МЕХАНИЗМ РАБОТАЕТ. Живая база и .env не тронуты.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def cmd_apply(db: str, env_path: str, old_key: str, new_key: str, service: str) -> None:
    """Настоящая ротация. Требует остановленного сервиса."""
    if _service_active(service):
        raise SystemExit(
            f"ОТКАЗ: сервис {service} активен. Останови его перед ротацией:\n"
            f"    systemctl stop {service}\n"
            "Ротация по живой базе (её пишет процесс) недопустима.")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bdir = "/root/gb-backups"
    os.makedirs(bdir, exist_ok=True)

    # 1) резервная копия живой базы и .env (на случай отката)
    db_bak = os.path.join(bdir, f"pre-rekey_{ts}.db")
    env_bak = os.path.join(bdir, f"pre-rekey_{ts}.env")
    shutil.copy2(db, db_bak); os.chmod(db_bak, 0o600)
    shutil.copy2(env_path, env_bak); os.chmod(env_bak, 0o600)
    print(f"[apply] бэкап: {db_bak}, {env_bak}")

    # 2) снимок → перешифровка снимка → проверка (живой файл ещё не тронут)
    tmpdir = tempfile.mkdtemp(prefix="gbrotate_")
    snap = os.path.join(tmpdir, "snap.db")
    try:
        _snapshot(db, old_key, snap)
        _rekey(snap, old_key, new_key)
        n = _verify_rotation(snap, old_key, new_key)
        print(f"[apply] перешифрованный снимок проверен: {n} польз., новый ключ {_pfx(new_key)}")

        # 3) атомная подмена живого файла + запись ключа в .env
        os.chmod(snap, 0o600)
        # WAL/SHM старой базы больше не нужны — новый файл самодостаточен.
        for ext in ("-wal", "-shm"):
            try:
                os.remove(db + ext)
            except OSError:
                pass
        shutil.move(snap, db)          # на той же ФС — атомно
        os.chmod(db, 0o600)
        _rewrite_env_key(env_path, new_key)
        print(f"[apply] ГОТОВО. Ключ в .env заменён. Запусти сервис: systemctl start {service}")
        print(f"[apply] Откат (если что): восстановить {db_bak} и {env_bak}, затем start.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _rewrite_env_key(env_path: str, new_key: str) -> None:
    """Заменить строку GRADEBOOK_DB_KEY=... на новый ключ, остальное не трогая."""
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    found = False
    for i, line in enumerate(lines):
        if re.match(r"\s*GRADEBOOK_DB_KEY\s*=", line):
            lines[i] = f"GRADEBOOK_DB_KEY={new_key}\n"
            found = True
            break
    if not found:
        lines.append(f"GRADEBOOK_DB_KEY={new_key}\n")
    tmp = env_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.chmod(tmp, 0o600)
    os.replace(tmp, env_path)          # атомно


def main() -> None:
    ap = argparse.ArgumentParser(description="Безопасная ротация ключа SQLCipher.")
    ap.add_argument("--env", default="/root/gb-deploy/server/.env",
                    help="путь к .env с GRADEBOOK_DB_KEY и GRADEBOOK_DB_URL")
    ap.add_argument("--db", default="", help="путь к файлу БД (по умолчанию из GRADEBOOK_DB_URL)")
    #🔥 ФЛАГА `--check` НЕ СУЩЕСТВОВАЛО, ХОТЯ ЕГО ОБЕЩАЛИ ТРИ ДОКУМЕНТА (05.09.2026).
    #Докстринг этого файла, CLAUDE.md и план безопасности называют «--check» РЕЖИМОМ, и
    #по ним человек набирает именно его — а получает `unrecognized arguments: --check` в
    #тот единственный день, когда ротация понадобилась. Поведение было верным (проверка
    #и так шла по умолчанию), неверным было ОБЕЩАНИЕ. Флаг заведён явным и ничего не
    #меняет: он лишь делает документированную команду рабочей.
    ap.add_argument("--check", action="store_true",
                    help="только проверка механизма на КОПИИ базы (режим по умолчанию)")
    ap.add_argument("--apply", action="store_true",
                    help="настоящая ротация (иначе только проверка, ничего не меняющая)")
    ap.add_argument("--new-key", default="",
                    help="новый ключ (64 hex). Пусто при --apply → сгенерировать случайный")
    ap.add_argument("--service", default="gradebook", help="имя systemd-сервиса для проверки")
    args = ap.parse_args()

    if args.check and args.apply:
        raise SystemExit("--check и --apply взаимоисключающи: первый ничего не меняет, "
                         "второй меняет всё. Выбери одно.")

    env = _read_env(args.env)
    old_key = env.get("GRADEBOOK_DB_KEY", "")
    if not _is_hex64(old_key):
        raise SystemExit("GRADEBOOK_DB_KEY в .env отсутствует или не 64 hex — ротация невозможна.")
    db = args.db or _db_path_from_env(args.env, env.get("GRADEBOOK_DB_URL", ""))
    if not db or not os.path.exists(db):
        raise SystemExit(
            f"Файл БД не найден: {db!r}.\n"
            "Задай путь явно: --db /root/gb-deploy/server/gradebook_server.db")

    try:
        import sqlcipher3  # noqa: F401
    except ImportError:
        #from None: причина здесь и есть сообщение, а трейсбек ImportError только шумит.
        raise SystemExit("Нет драйвера sqlcipher3 — ротация возможна только там, где он установлен "
                         "(боевой venv). На Windows-dev его нет by design.") from None

    if not args.apply:
        cmd_check(db, old_key)
        return

    new_key = args.new_key or secrets.token_hex(32)
    if not _is_hex64(new_key):
        raise SystemExit("--new-key должен быть ровно 64 hex-символа (32 байта).")
    if new_key == old_key:
        raise SystemExit("Новый ключ совпадает со старым — это не ротация.")
    cmd_apply(db, args.env, old_key, new_key, args.service)


if __name__ == "__main__":
    main()
