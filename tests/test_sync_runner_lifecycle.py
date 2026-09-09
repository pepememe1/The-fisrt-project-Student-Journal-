# -*- coding: utf-8 -*-
"""Жизненный цикл фонового синка и отчётность зеркала (находки 07.09.2026).

Два дефекта, оба тихие, оба найдены чтением цепочек, а не прогоном:

1) 🔥 ЗЕРКАЛО МОЛЧА НЕ ОБНОВЛЯЛОСЬ. `_mirror_for_vue` звал `local_mirror.mirror_once`
   и ВЫБРАСЫВАЛ результат. А тот про неудачу СООБЩАЕТ ({"ok": False, "error": ...}), а
   не бросает исключение, — поэтому `except` вокруг вызова его не видел никогда.
   Следствие: обмен со старой базой идёт, цикл ставит online=True, а Vue-интерфейс
   работает на устаревшей копии. Причина есть в логе, в состоянии синка её нет.

2) 🔥 БЫСТРЫЙ stop → start ОСТАВЛЯЛ ДВА ЦИКЛА. `stop()` сбрасывал общий `_running` и
   не ждал потока; `start()` возвращал флаг в True. Старый поток просыпался, видел
   True и работал рядом с новым — два цикла на одну базу и один сетевой клиент.

⚠️ Проверяем СВОЙСТВА, а не строки кода. У каждого теста есть обратный ход.
"""
import threading
import time

import pytest

from sync import sync_runner


class _Mgr(sync_runner.SyncManager):
    """Менеджер без сети: подменяем сам цикл, нас интересует только его ЖИЗНЬ."""

    def __init__(self):
        super().__init__()
        self.alive = set()          # какие запуски сейчас крутятся
        self.seen_max = 0           # максимум одновременно живых циклов

    def _loop(self, stop_evt=None):
        me = object()
        self.alive.add(me)
        try:
            while self._running and not (stop_evt is not None and stop_evt.is_set()):
                self.seen_max = max(self.seen_max, len(self.alive))
                time.sleep(0.005)
        finally:
            self.alive.discard(me)


def test_stop_then_start_never_leaves_two_loops():
    """Главное свойство: одновременно живой цикл ровно один."""
    m = _Mgr()
    for _ in range(12):
        m.start("u", "p", "teacher")
        time.sleep(0.01)
        m.stop()
    m.stop()
    time.sleep(0.2)
    assert m.seen_max <= 1, f"одновременно крутилось циклов: {m.seen_max}"
    assert not m.alive, "после stop() не осталось живых циклов"


def test_stop_signal_belongs_to_the_run_not_to_the_manager():
    """Обратный ход к дефекту: старый поток обязан выйти, ДАЖЕ ЕСЛИ общий флаг снова
    True. Именно этого не умел прежний код — там сигнала на запуск не было вовсе."""
    m = _Mgr()
    m.start("u", "p", "teacher")
    old_evt = m._stop_evt
    m.stop()
    assert old_evt.is_set(), "stop() обязан взвести сигнал ИМЕННО ЭТОГО запуска"
    m._running = True                      # имитируем гонку: новый start успел раньше
    assert old_evt.is_set(), "чужой запуск не имеет права разостановить прежний поток"
    m.stop()


def test_mirror_failure_is_recorded_not_swallowed():
    """`mirror_once` вернул ok=False — это обязано попасть в состояние синка."""
    m = sync_runner.SyncManager()
    fake = type("M", (), {"mirror_once": staticmethod(
        lambda client=None: {"ok": False, "error": "нет активной сессии с сервером"})})
    import sys
    sys.modules["desktop.local_mirror"] = fake
    try:
        m._mirror_for_vue()
    finally:
        sys.modules.pop("desktop.local_mirror", None)
    assert m._mirror_error == "нет активной сессии с сервером", (
        "отказ зеркала выброшен — ровно тот дефект, ради которого тест написан")


def test_mirror_success_clears_the_error_and_stamps_time():
    m = sync_runner.SyncManager()
    m._mirror_error = "прошлая беда"
    fake = type("M", (), {"mirror_once": staticmethod(
        lambda client=None: {"ok": True, "rows": 7})})
    import sys
    sys.modules["desktop.local_mirror"] = fake
    try:
        m._mirror_for_vue()
    finally:
        sys.modules.pop("desktop.local_mirror", None)
    assert m._mirror_error == "", "успех обязан снимать прежнюю жалобу"
    assert m._mirror_ok_at, "время последнего успеха обязано проставляться"


def test_missing_mirror_module_is_not_reported_as_a_failure():
    """Сборка без серверного пакета рядом — штатная. Вечная жалоба там, где всё
    работает как задумано, приучает не читать сигнал (правило проекта)."""
    m = sync_runner.SyncManager()
    import sys
    saved = sys.modules.pop("desktop.local_mirror", None)
    real = sys.modules.pop("desktop", None)
    sys.modules["desktop"] = type("D", (), {})()   # пакет без атрибута local_mirror
    try:
        m._mirror_for_vue()
    finally:
        sys.modules.pop("desktop", None)
        if real is not None:
            sys.modules["desktop"] = real
        if saved is not None:
            sys.modules["desktop.local_mirror"] = saved
    assert m._mirror_error == "", "отсутствие модуля — не беда копии"


def test_status_exposes_mirror_state_separately_from_sync_state():
    """Пятое состояние отдельно от четырёх прежних: обмен может идти прекрасно, а
    копия, из которой рисуется журнал, — стоять. Свести их значило бы показать
    «всё хорошо» там, где человек смотрит на устаревшие данные."""
    m = sync_runner.SyncManager()
    m._mirror_error = "копия не обновилась"
    st = m.status()
    assert "mirror_error" in st and "mirror_ok_at" in st, (
        "состояние зеркала не отдаётся наружу — узнать о нём будет неоткуда")
    assert st["mirror_error"] == "копия не обновилась"
    assert st["error"] != st["mirror_error"] or not st["error"], (
        "беда зеркала не имеет права подменять собой беду связи")
