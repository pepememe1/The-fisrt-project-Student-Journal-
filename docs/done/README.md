# Закрытые планы

Сюда переезжает план, **предмет которого живёт в продукте**. Смысл каталога — чтобы на
вопрос «это уже сделано или ещё нет» отвечал состав папки, а не память.

⚠️ **Правило приёма, и оно не формальность: у каждой строки названо ДОКАЗАТЕЛЬСТВО —
файл продукта, без которого плана бы не существовало.** «Кажется, мы это делали» —
недостаточное основание: в этом проекте уже трижды случалось, что намерение записывали
как факт (Playwright «стоит с 18.08», «47 маркетинг-скиллов», «на бою PostgreSQL»), и по
такой записи потом уверенно строили планы.

⚠️ **Доказательства проверяются прогоном** — `server/tests/test_done_docs.py`. Удалят
названный файл, а документ оставят здесь — тест покраснеет. Без этого папка «сделано»
через полгода начнёт врать ровно так же, как врал бы устаревший CLAUDE.md.

⚠️ **Перенос сюда НЕ означает «внутри всё до последней строки закрыто».** Где остался
хвост — он назван прямо в таблице. Умолчать про хвост значило бы получить документ,
который выглядит закрытым и не является им, — а это хуже, чем оставить план в работе.

| план | что от него в продукте (доказательство) | остаток |
|---|---|---|
| `PLAN-2.10.md` | `server/app/routers/web/curator.py`, `User.curated_groups` | ФАЗА 6 (переход на FSD) отложена по решению — см. §«FSD отложен» в самом файле |
| `PLAN-3.1.md` | `SubjectHours` в `models.py`, роль `parent` в `routers/parent.py`, ДЗ как тип занятия | — |
| `PLAN-ACTIVITIES.md` | `server/app/routers/activities.py`, `activity_state.py`, колесо в `web/src/utils/wheelGeometry.js` | — |
| `PLAN-COURSE-ROLLOVER.md` | `server/app/course_rollover.py` + автозапуск в `main.lifespan` | — |
| `PLAN-EASTER-EGGS.md` | `server/app/easter_eggs.py`, `web/src/config/achievements.js` | — |
| `PLAN-HONEYPOT.md` | `server/app/canary.py` + middleware в `main.py` + баны в `throttle.py` | белый список `GRADEBOOK_TRUST_IPS` на бою — заглушка (не дефект плана, а незаданная настройка) |
| `PLAN-PACKAGING.md` | `build_nuitka.sh`, сборка onefile | — |
| `PLAN-ZET.md` | `study_hours.subject_zet_state`, `webdata.zet_summary_for_student` | граница «рубеж семестра» выбрана по варианту C и ждёт подтверждения заказчика |
| `MESSENGER-PLAN.md` | пакет `server/app/routers/messenger/` | — |
| `MESSENGER-PLAN-DISCORD-ADDONS.md` | D1–D12: реакции, упоминания, треды, статусы, системные каналы | — |
| `MESSENGER-ATTACHMENTS-PLAN.md` | `server/app/storage.py`, `routers/messenger/attachments.py` | — |
| `PERF-MESSENGER.md` | `web/src/utils/livePolling.js` (опрос 30 с при живом сокете) | — |
| `TTS-PLAN.md` | `server/app/tts_service.py`, `web/src/stores/tts.js` | клон своего голоса — задел на машину с видеокартой |
| `PENTEST-3.7.8.md` | оба отложенных пункта закрыты: `storage.MAX_USER_DAY_BYTES`, `downloads` в `_API_PREFIXES` | — |

---

## Что СОЗНАТЕЛЬНО осталось в `docs/` и почему

Каталог отвечает на вопрос «сделано ли», поэтому сюда не попадает три разных сорта
документов — и путать их между собой нельзя:

**1. Планы с незакрытым предметом.**

| документ | почему остаётся |
|---|---|
| `PLAN-EXTERNAL-GIFS.md` | в шапке прямо: «ОТЛОЖЕНО, не начато». Он про Tenor/Giphy, а НЕ про Klipy — Klipy сделан, но это другой план |
| `PLAN-MOBILE-OFFLINE.md` | предмета в коде нет |
| `PLAN-TG-MINIAPP.md` | мини-приложения Telegram в продукте нет вовсе |
| `PLAN-AI-SERVER.md` | ждёт железа ВСГУТУ, не кода |
| `PLAN-PROD-2026-09.md`, `PLAN-YAROSLAV-2026-09.md`, `PLAN-SALE-AND-MIGRATION.txt`, `PLAN-HARDENING.txt` | живые бэклоги |
| `security/PLAN-SECURITY-2.0.md` | часть пунктов невыполнима без offline-ключа и WORM-хранилища |
| `TECH-DEBT-PLAN.md`, `NOT-DONE-3.8.6.md`, `QUEUE.md`, `PERF-SCALE-2026.md` | по построению списки незакрытого |
| `MESSENGER-ADDON-PLAN-GPT.md`, `MESSENGER-ADDON-PLAN-GPT-SMART.md` | отбор сделан частично и осознанно (см. §5.4 CLAUDE.md) |
| `ULYANA-CAMPUS-BROWSER-SPEC.md` | работа идёт прямо сейчас |

**2. Живые документы, у которых нет состояния «сделано».** Их закрывать нечем:
`INCIDENT-RESPONSE.md`, `DATA-RETENTION.md`, `SUBPROCESSORS.md`,
`security/ASVS-BASELINE.md`, `SECURITY-ARCHITECTURE.md`. Регламент реагирования не
бывает «выполнен» — он бывает актуальным или устаревшим.

**3. Исследования и материалы для продажи** — они описывают мир, а не нашу работу:
`COMPETITOR-MMIS.md`, `MARKET-ANALOGUES-2026.md`, `SYNC-RESEARCH-2026.md`,
`PRICING-AND-PITCH.md`, `CHRONOLOGY-EVIDENCE.md`.
