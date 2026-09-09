"""
bench_messenger.py — ЗАМЕР ГОРЯЧИХ ПУТЕЙ МЕССЕНДЖЕРА И ЖУРНАЛА.

━━ ЗАЧЕМ ━━
Требование Ярослава (09.09.2026): «1500 пользователей одновременно, целевое ~3000,
нужна максимальная оптимизация». До этого файла у нас НЕ БЫЛО НИ ОДНОЙ цифры о том,
сколько стоит один пользователь: все рассуждения о SQLite, о Go и о переезде опирались
на ощущения. Наш собственный записанный урок про Argos («замер, а не прикидка»: оценка
«влезет впритык» разошлась с правдой на порядок) относится к этому ровно так же.

━━ ЧТО ИМЕННО ЗАМЕРЯЕТСЯ ━━
Не выдуманный SQL, а НАСТОЯЩИЕ функции эндпоинтов (`list_chats`, `messages`,
`conversation_info`, …), вызванные напрямую с живой сессией. Причина та же, по которой
`validate-agents.py` ничего не проверял: инструмент, разбирающий задачу не так, как
настоящий потребитель, меряет не то. Здесь потребитель — сам роутер.

Считаем ДВЕ величины, и вторая важнее первой:
  • время ответа (медиана и худшие 5 %);
  • ЧИСЛО ЗАПРОСОВ К БАЗЕ на один ответ. Время зависит от машины и меняется вместе с
    ней, а «сто запросов на один список чатов» — свойство кода: оно одинаково плохо и
    на VPS, и на Ryzen 9, просто на втором это заметят позже.

━━ ЧЕГО ЭТОТ ЗАМЕР НЕ ДЕЛАЕТ (честная граница) ━━
  • это НЕ нагрузочный тест: параллельных клиентов здесь нет, меряется стоимость ОДНОГО
    ответа. Умножать на число пользователей можно только грубо — но именно эта грубая
    оценка и показывает, хватает ли одного ядра;
  • SQLCipher не включён (на машине разработки нет драйвера). Шифрование добавляет
    примерно 5–15 % к CPU и НЕ меняет число запросов, то есть вывод про N+1 от него не
    зависит;
  • данные синтетические. Распределение «сколько у кого бесед» взято правдоподобным
    (у преподавателя их заметно больше, чем у студента), но это модель, а не выгрузка
    боевой базы — её на машину разработчика класть нельзя (п. 5.2.4.1 политики ВСГУТУ).

━━ КАК ЗАПУСКАТЬ ━━
    cd server && python -X utf8 bench_messenger.py                # быстрый профиль
    cd server && python -X utf8 bench_messenger.py --scale big    # ближе к целевым 3000
    cd server && python -X utf8 bench_messenger.py --json out.json
"""
import argparse
import json
import os
import random
import statistics
import sys
import tempfile
import time

#Изолированная база — строго ДО импорта приложения: config.py и db.py читают окружение
#на этапе импорта и создают движок один раз (та же причина, что в tests/conftest.py).
_BENCH_DB = os.path.join(tempfile.gettempdir(), "gradebook_bench.db")
os.environ["GRADEBOOK_DB_URL"] = "sqlite:///" + _BENCH_DB.replace(os.sep, "/")
os.environ.setdefault("GRADEBOOK_JWT_SECRET", "bench-secret-not-for-production")
os.environ["GRADEBOOK_DB_KEY"] = ""
os.environ["GRADEBOOK_ALLOWED_ORIGINS"] = "*"
os.environ["GRADEBOOK_DATA_KEY"] = ""
os.environ["GRADEBOOK_INDEX_KEY"] = ""
os.environ["GRADEBOOK_DOMAIN"] = ""

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import event                                        # noqa: E402

from app.db import Base, SessionLocal, engine                       # noqa: E402
from app.models import (Conversation, ConversationParticipant,      # noqa: E402
                        Message, User)
from app.routers.messenger import chats as chats_router             # noqa: E402
from app.routers.messenger import messages as messages_router       # noqa: E402


# ── профили нагрузки ────────────────────────────────────────────────────────────────
# «small» — сегодняшний колледж; «big» — целевые 3000 человек с накопленной за годы
# перепиской. Второй профиль и есть ответ на вопрос «а что будет, когда вырастем».
PROFILES = {
    "small": dict(users=300, groups=20, msgs_per_conv=400),
    "mid": dict(users=1500, groups=60, msgs_per_conv=1200),
    "big": dict(users=3000, groups=120, msgs_per_conv=3000),
}


class QueryCounter:
    """Считает запросы к базе за замеряемый участок. Вешается на движок один раз."""

    def __init__(self, eng):
        self.n = 0
        self.on = False
        self.slow = []
        event.listen(eng, "before_cursor_execute", self._before)

    def _before(self, conn, cursor, statement, params, ctx, many):
        if self.on:
            self.n += 1
            self.slow.append(statement.split("\n")[0][:110])

    def start(self):
        self.n, self.slow, self.on = 0, [], True

    def stop(self):
        self.on = False
        return self.n


def seed(profile: str, seed_value: int = 20260909) -> dict:
    """Наполнить базу правдоподобными данными. Возвращает, кого потом замерять."""
    rnd = random.Random(seed_value)
    cfg = PROFILES[profile]
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    t0 = time.perf_counter()
    users = []
    #Роли в пропорции настоящего колледжа: подавляющее большинство — студенты.
    for i in range(cfg["users"]):
        role = "student" if i % 12 else ("teacher" if i % 24 else "admin")
        u = User(id=f"u{i}", login=f"user{i}", name=f"Имя{i}", full_name=f"Фамилия{i} Имя{i}",
                 role=role, group_name=f"К{i % cfg['groups']}", password_hash="x")
        users.append(u)
        db.add(u)
    db.commit()

    convs = []
    #Групповые беседы: учебная группа целиком. Это самый тяжёлый для нас случай —
    #участников много, сообщений много, и список чатов их все и обходит.
    for g in range(cfg["groups"]):
        c = Conversation(id=f"g{g}", kind="group", title=f"Группа К{g}",
                         owner_id="u0", created_at="2026-01-01T00:00:00Z")
        db.add(c)
        convs.append(c)
    #Личные чаты: у каждого несколько собеседников.
    for i in range(cfg["users"] // 2):
        a, b = f"u{i}", f"u{(i * 7 + 3) % cfg['users']}"
        if a == b:
            continue
        c = Conversation(id=f"d{i}", kind="direct", created_at="2026-01-01T00:00:00Z")
        db.add(c)
        convs.append(c)
        db.add(ConversationParticipant(conversation_id=c.id, user_id=a, role="member"))
        db.add(ConversationParticipant(conversation_id=c.id, user_id=b, role="member"))
    db.commit()

    for u in users:
        g = int(u.group_name[1:])
        db.add(ConversationParticipant(conversation_id=f"g{g}", user_id=u.id, role="member",
                                       last_read_at="2026-05-01T00:00:00Z"))
        #Преподаватель сидит ещё в нескольких группах — он и есть тяжёлый пользователь.
        if u.role in ("teacher", "admin"):
            for extra in range(1, min(9, cfg["groups"])):
                db.add(ConversationParticipant(conversation_id=f"g{(g + extra) % cfg['groups']}",
                                               user_id=u.id, role="admin",
                                               last_read_at="2026-05-01T00:00:00Z"))
    db.commit()

    #Сообщения. Даты растут, чтобы часть оказалась «непрочитанной» (позже last_read_at).
    mid = 0
    rows = []
    for c in convs:
        n = cfg["msgs_per_conv"] if c.kind == "group" else cfg["msgs_per_conv"] // 8
        members = [p.user_id for p in db.query(ConversationParticipant)
                   .filter(ConversationParticipant.conversation_id == c.id).limit(30).all()]
        if not members:
            continue
        for k in range(n):
            mid += 1
            #Каждое двадцатое сообщение содержит отметку «@» — так и в жизни.
            body = (f"@Фамилия{rnd.randrange(cfg['users'])} посмотри"
                    if k % 20 == 0 else f"сообщение {k} в беседе {c.id}")
            month = 1 + (k * 11) // max(1, n)
            rows.append(dict(id=mid, conversation_id=c.id, sender_id=rnd.choice(members),
                             body=body, kind="text",
                             created_at=f"2026-{month:02d}-01T10:00:00Z", deleted_at="",
                             mentions=[], reply_to_id=0, pinned=False,
                             attachment_id="", client_nonce=""))
        if len(rows) >= 20000:
            db.bulk_insert_mappings(Message, rows)
            db.commit()
            rows = []
    if rows:
        db.bulk_insert_mappings(Message, rows)
        db.commit()

    total_msgs = db.query(Message).count()
    #Кого мерим: студент (одна группа + личные) и преподаватель (девять групп).
    student = db.query(User).filter(User.role == "student").first()
    teacher = db.query(User).filter(User.role == "teacher").first()
    #Беседу для замера берём ту, где преподаватель ДЕЙСТВИТЕЛЬНО состоит: жёстко
    #вписанный «g0» совпадал с его группой только на маленьком профиле, а на большом
    #замер падал 403-м — то есть литерал молча зависел от размера набора.
    heavy_conv = "g" + teacher.group_name[1:]
    db.close()
    return dict(profile=profile, seconds=round(time.perf_counter() - t0, 1),
                users=cfg["users"], convs=len(convs), messages=total_msgs,
                student_id=student.id, teacher_id=teacher.id, heavy_conv=heavy_conv)


def measure(name, fn, counter, repeat):
    """Один замер: время и число запросов к базе."""
    times, queries = [], 0
    fn()                                        # прогрев: первый вызов греет кеш страниц
    for _ in range(repeat):
        counter.start()
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
        queries = counter.stop()
    times.sort()
    return dict(name=name, ms_p50=round(statistics.median(times), 1),
                ms_p95=round(times[min(len(times) - 1, int(len(times) * 0.95))], 1),
                queries=queries)


def run(profile: str, repeat: int) -> dict:
    info = seed(profile)
    counter = QueryCounter(engine)
    db = SessionLocal()
    student = db.query(User).filter(User.id == info["student_id"]).first()
    teacher = db.query(User).filter(User.id == info["teacher_id"]).first()

    cases = [
        ("список чатов, студент", lambda: chats_router.list_chats(user=student, db=db)),
        ("список чатов, преподаватель", lambda: chats_router.list_chats(user=teacher, db=db)),
        ("лента беседы (открытие)",
         lambda: messages_router.messages(conv_id=info["heavy_conv"], before=0, after=0,
                                          limit=50, user=teacher, db=db)),
        ("догон ленты (?after=)",
         lambda: messages_router.messages(conv_id=info["heavy_conv"], before=0,
                                          after=max(1, info["messages"] - 5),
                                          limit=50, user=teacher, db=db)),
        ("карточка беседы",
         lambda: chats_router.conversation_info(conv_id=info["heavy_conv"],
                                                user=teacher, db=db)),
    ]
    results = [measure(n, f, counter, repeat) for n, f in cases]
    db.close()
    return dict(info=info, results=results)


def main():
    ap = argparse.ArgumentParser(description="Замер горячих путей мессенджера")
    ap.add_argument("--scale", choices=list(PROFILES), default="small")
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    out = run(args.scale, args.repeat)
    i = out["info"]
    print(f"\nПрофиль «{i['profile']}»: {i['users']} человек, {i['convs']} бесед, "
          f"{i['messages']} сообщений (наполнение {i['seconds']} с)\n")
    print(f"{'путь':<32}{'медиана, мс':>14}{'худшие 5%, мс':>16}{'запросов к БД':>16}")
    print("-" * 78)
    for r in out["results"]:
        print(f"{r['name']:<32}{r['ms_p50']:>14}{r['ms_p95']:>16}{r['queries']:>16}")
    print()
    #Грубая, но честная прикидка потолка: сколько таких ответов успеет одно ядро.
    worst = max(out["results"], key=lambda r: r["ms_p50"])
    if worst["ms_p50"] > 0:
        print(f"Одно ядро выдаёт примерно {int(1000 / worst['ms_p50'])} ответов «{worst['name']}» "
              f"в секунду. При опросе раз в 3.5 с это потолок около "
              f"{int(1000 / worst['ms_p50'] * 3.5)} человек НА ЯДРО — без всего остального,\n"
              f"что сервер делает в то же время.")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\nПодробности: {args.json}")


if __name__ == "__main__":
    main()
