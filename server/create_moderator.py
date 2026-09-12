"""
create_moderator.py — завести (или перевыдать пароль) учётной записи МОДЕРАТОРА.

🔑 ПОЧЕМУ ЭТО СКРИПТ, А НЕ СТРОКА В КОДЕ И НЕ РУЧКА В API.

• **Пароля в репозитории быть не может.** Репозиторий приватный, но уезжает покупателю
  вместе с историей, а пароль, однажды попавший в git, остаётся там навсегда — вычистить
  его можно только переписыванием истории по всем веткам (мы это уже делали ради чужого
  трейлера в коммитах, §0). Поэтому пароль приходит аргументом или запросом в консоли и
  нигде не сохраняется: в базу ложится только хеш.
• **Ручки в API нет и не будет.** Заведение человека с доступом к чужой переписке — то же
  по весу действие, что аварийный сброс второго фактора (`server/mfa_admin.py`), и граница
  у него та же (§16): выполняется С САМОЙ МАШИНЫ, а не из браузера. Роль можно обойти,
  отсутствующий маршрут — нельзя.

Запуск на боевой машине:

    cd /opt/gradebook/server && ../venv/bin/python create_moderator.py --login moderator

Пароль скрипт спросит сам (ввод не отображается). Неинтерактивно — `--password`, но тогда
он останется в истории команд оболочки: годится для разового заведения, после которого
историю чистят, и не годится для скрипта развёртывания.

⚠️ Повторный запуск с тем же логином НЕ создаёт второго человека — он меняет пароль
существующему. Иначе первая же опечатка в имени плодила бы «модератора-двойника», которого
никто не заметит, потому что списка модераторов на виду нет.
"""
import argparse
import getpass
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db import SessionLocal, init_db          # noqa: E402
from app.deps import KNOWN_ROLES                  # noqa: E402
from app.models import User, set_user_password    # noqa: E402
from app import audit                             # noqa: E402

#Минимум, ниже которого заводить нельзя. Это не «политика паролей» (её у продукта нет и
#выдумывать её здесь не место) — это защита от пустой строки и от «123», набранных в
#спешке для учётной записи, которой открыта чужая переписка.
MIN_PASSWORD_LEN = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description="Завести учётную запись модератора")
    ap.add_argument("--login", required=True, help="логин для входа, например moderator")
    ap.add_argument("--name", default="Модерация", help="отображаемое имя")
    ap.add_argument("--password", default="", help="пароль; без него будет запрошен в консоли")
    ap.add_argument("--by", default="", help="кто заводит — попадёт в журнал аудита")
    args = ap.parse_args()

    login = args.login.strip()
    if not login:
        print("Пустой логин.")
        return 2
    assert "moderator" in KNOWN_ROLES, "роль moderator не объявлена в deps.KNOWN_ROLES"

    password = args.password or getpass.getpass("Пароль модератора (не отображается): ")
    if len(password) < MIN_PASSWORD_LEN:
        print(f"Пароль короче {MIN_PASSWORD_LEN} символов — не заведено.")
        return 2
    if not args.password:
        #Подтверждение только для интерактивного ввода: опечатку в невидимом поле иначе
        #обнаружит сам модератор, когда не сможет войти, и звать будут нас.
        if password != getpass.getpass("Повторите пароль: "):
            print("Пароли не совпали — не заведено.")
            return 2

    init_db()
    db = SessionLocal()
    try:
        #id в формате `mod:{login}` — по тому же принципу, что `stud:`/`teach:` в синке
        #(§10 CLAUDE.md): по префиксу видно, кто это, без похода в колонку роли.
        uid = f"mod:{login}"
        row = db.query(User).filter(User.login == login, User.deleted == False).first()  # noqa: E712
        created = row is None
        if row is None:
            row = db.get(User, uid) or User(id=uid)
            db.add(row)
        elif row.role != "moderator":
            #Перевести чужую учётную запись в модераторы этим скриптом НЕЛЬЗЯ: под тем же
            #логином может жить преподаватель, и смена роли отобрала бы у него журнал,
            #оставив вместо этого доступ к чужой переписке. Такое решение принимается
            #человеком в админке, а не инструментом заведения.
            print(f"Логин «{login}» уже занят ролью «{row.role}». Выберите другой логин.")
            return 3
        row.role = "moderator"
        row.login = login
        row.full_name = args.name
        row.name = args.name
        row.deleted = False
        row.updated_at = _now()
        set_user_password(row, password)
        db.commit()
        audit.log(db, None, actor=(args.by or "cli"), role="admin",
                  action="user.moderator.create" if created else "user.moderator.password",
                  target=uid, detail=login)
        print(f"{'Заведён' if created else 'Обновлён пароль'}: {uid} (роль moderator, логин {login})")
        print("Пароль нигде не сохранён — передайте его человеку и не храните в переписке.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
