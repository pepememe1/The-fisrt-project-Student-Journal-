"""
test_done_docs.py — КАТАЛОГ «СДЕЛАНО» ОБЯЗАН ГОВОРИТЬ ПРАВДУ.

`docs/done/` заведён 10.09.2026, чтобы на вопрос «это уже реализовано или ещё нет»
отвечал состав папки, а не память. Но папка с названием «сделано» — это ровно тот вид
документа, который в нашем проекте уже трижды начинал врать: «Playwright стоит с 18.08»
(не стоял), «47 маркетинг-скиллов» (был один), «на бою PostgreSQL» (не было никогда).
Каждый раз намерение записывали как факт, и по факту потом строили планы.

Поэтому у каждого перенесённого плана в `docs/done/README.md` названо ДОКАЗАТЕЛЬСТВО —
файл продукта, без которого плана бы не существовало, — и этот прогон проверяет, что
файл на месте. Удалят `canary.py`, а `PLAN-HONEYPOT.md` оставят в «сделано» — покраснеет.

⚠️ ЧЕСТНАЯ ГРАНИЦА, и её надо назвать вслух. Проверяется НАЛИЧИЕ файла, а не то, что
он делает. Это сторож против «предмет плана удалили, а документ остался», а НЕ
доказательство, что план выполнен целиком: последнее машиной не проверяется вовсе и
держится на разборе человеком. Делать вид, что прогон закрывает и это, нельзя — иначе
сторож сам станет источником ложной уверенности, против которой заведён.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DONE = os.path.join(ROOT, "docs", "done")
README = os.path.join(DONE, "README.md")

#Пути в README пишутся прозой вперемешку с текстом, поэтому доказательства перечислены
#здесь отдельным списком: разбор прозы регулярками — ровно тот приём, из-за которого
#`validate-agents.py` три недели «проверял» и не проверял ничего.
EVIDENCE = {
    "PLAN-2.10.md": ["server/app/routers/web/curator.py"],
    "PLAN-3.1.md": ["server/app/routers/parent.py"],
    "PLAN-ACTIVITIES.md": ["server/app/routers/activities.py",
                           "server/app/activity_state.py",
                           "web/src/utils/wheelGeometry.js"],
    "PLAN-COURSE-ROLLOVER.md": ["server/app/course_rollover.py"],
    "PLAN-EASTER-EGGS.md": ["server/app/easter_eggs.py",
                            "web/src/config/achievements.js"],
    "PLAN-HONEYPOT.md": ["server/app/canary.py", "server/app/throttle.py"],
    "PLAN-PACKAGING.md": ["build_nuitka.sh"],
    "PLAN-ZET.md": ["study_hours.py"],
    "MESSENGER-PLAN.md": ["server/app/routers/messenger/_common.py"],
    "MESSENGER-PLAN-DISCORD-ADDONS.md": ["server/app/routers/messenger/messages.py"],
    "MESSENGER-ATTACHMENTS-PLAN.md": ["server/app/storage.py",
                                      "server/app/routers/messenger/attachments.py"],
    "PERF-MESSENGER.md": ["web/src/utils/livePolling.js"],
    "TTS-PLAN.md": ["server/app/tts_service.py", "web/src/stores/tts.js"],
    "PENTEST-3.7.8.md": ["server/app/storage.py"],
}


def _done_plans():
    """Планы, лежащие в docs/done (README сам планом не является)."""
    if not os.path.isdir(DONE):
        return []
    return sorted(f for f in os.listdir(DONE)
                  if f.endswith(".md") and f != "README.md")


@pytest.mark.parametrize("plan", _done_plans())
def test_every_done_plan_still_has_its_subject_in_the_product(plan):
    """У плана в «сделано» обязан существовать названный файл продукта."""
    assert plan in EVIDENCE, (
        "план %s лежит в docs/done, но доказательства для него не названо. "
        "Либо впишите файл продукта в EVIDENCE и в README, либо верните план в docs/ — "
        "«сделано» без доказательства это просто утверждение" % plan)
    for rel in EVIDENCE[plan]:
        assert os.path.exists(os.path.join(ROOT, rel)), (
            "%s числится сделанным, но названного доказательства нет: %s. "
            "Либо предмет плана удалили (тогда документ не «сделано»), либо файл "
            "переименовали и запись устарела" % (plan, rel))


def test_the_evidence_list_has_no_dead_entries():
    """И обратная половина: в EVIDENCE нет записей о планах, которых там уже нет.

    Список без мёртвых записей — правило проекта. Мёртвая строка выглядит как знание,
    а на деле описывает файл, который кто-то давно вернул в работу.
    """
    dead = sorted(set(EVIDENCE) - set(_done_plans()))
    assert not dead, ("EVIDENCE называет планы, которых в docs/done нет: %s" % dead)


def test_readme_mentions_every_plan_that_lies_there():
    """Каталог и его оглавление не имеют права разойтись.

    Разойдутся они молча: файл переносят одной командой, а таблицу в README дописать
    забывают — и «сделано» перестаёт быть списком, становясь просто папкой.
    """
    assert os.path.exists(README), "у docs/done нет README — каталог без оглавления"
    with open(README, encoding="utf-8") as f:
        text = f.read()
    missing = [p for p in _done_plans() if p not in text]
    assert not missing, ("в docs/done лежат планы, не названные в его README: %s"
                         % missing)


def test_no_stale_path_to_a_moved_plan_is_left_in_the_code():
    """🔒 ОБРАТНЫЙ ХОД переноса: старый путь `docs/<план>.md` не должен остаться нигде.

    Путь в докстринге — такая же поверхность отказа, как код: ссылка на прежнее место
    отправит следующего читателя искать несуществующий файл и решить, что план потеряли.
    Правило записано в CLAUDE.md («комментарий и докстринг — тоже поверхность отказа») и
    уже трижды покупалось настоящими ошибками.

    🔥 И этот сторож поймал настоящее в первый же прогон: массовая правка ссылок прошла
    только по файлам ПОД GIT, а `.claude/agents/gb-extract.md` и `.codex/CODEX.md` в git
    не лежат — там пути остались старыми и никто бы этого не заметил.
    ⚠️ Пример прежнего пути здесь НЕ приводится дословно намеренно: та же массовая
    замена переписала бы его вместе с настоящими ссылками, и объяснение превратилось бы
    в собственную противоположность. Это уже случилось при написании файла.
    """
    skip_dirs = ("node_modules", "graphify-out", ".git", "dist", "__pycache__",
                 "docs\\done", "docs/done")
    exts = (".py", ".js", ".mjs", ".vue", ".sh", ".md", ".txt", ".yml")
    #🔥 СНИМКИ ПРОШЛОГО ПРОПУСКАЕМ, и это не послабление сторожу.
    #`CLAUDE.md` и `docs/HISTORY.md` вне git и сливаются руками, поэтому рядом с ними
    #живут датированные копии: предок для следующего трёхстороннего слияния и «как было
    #до». Такая копия ОБЯЗАНА содержать прежние пути — она описывает состояние на свою
    #дату, и «починить» её значит подделать снимок, по которому потом сверяются.
    #Требовать от них актуальности — то же, что требовать её от git-истории.
    #⚠️ Узнаём их по ИМЕНИ (`.bak-`/`.base-` + дата), а не по расширению: обычный `.md`
    #в репозитории по-прежнему проверяется весь.
    snapshots = (".bak-", ".base-")
    bad = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if not any(s in os.path.join(base, d)
                                              for s in skip_dirs)]
        if any(s in base for s in skip_dirs):
            continue
        for fn in files:
            if not fn.endswith(exts):
                continue
            if any(s in fn for s in snapshots):
                continue                       #датированный снимок — см. объяснение выше
            path = os.path.join(base, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                continue
            for plan in _done_plans():
                #Ищем ровно «docs/<план>», не предварённое «done/».
                for m in re.finditer(r"docs/" + re.escape(plan), text):
                    if not text[max(0, m.start() - 5):m.start()].endswith("done/"):
                        bad.append("%s -> docs/%s" % (os.path.relpath(path, ROOT), plan))
                        break
    assert not bad, ("остались ссылки на прежнее место перенесённых планов:\n  "
                     + "\n  ".join(sorted(set(bad))))
