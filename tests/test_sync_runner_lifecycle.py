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
import sys
import threading
import time

import pytest

import desktop
from sync import sync_runner


# ⚠️ ПОДМЕНЯТЬ НАДО АТРИБУТ ПАКЕТА, А НЕ ЗАПИСЬ В `sys.modules` (найдено полным
# прогоном 09.09.2026). Продукт делает `from desktop import local_mirror`, а эта форма
# сначала берёт АТРИБУТ уже импортированного пакета и лишь при его отсутствии смотрит в
# `sys.modules`. В одиночку файл проходил (пакет ещё не трогали, срабатывал запасной
# путь), а в полном прогоне любой предыдущий тест успевал импортировать зеркало — и
# подмена молча не действовала: звался НАСТОЯЩИЙ `mirror_once`, который без живой сессии
# честно отвечает отказом. Два теста краснели при полностью исправном коде.
#
# 🔑 Правило шире этого файла: подменяй то, что читает ПОТРЕБИТЕЛЬ, тем же способом,
# каким он это читает. Ровно тот же урок, что с `validate-agents.py`, разбиравшим YAML
# не так, как настоящий загрузчик.
def _put_mirror(monkeypatch, result):
    """Подставить зеркало, возвращающее `result`, — так, как его увидит продукт."""
    fake = type("M", (), {"mirror_once": staticmethod(lambda client=None: result)})
    monkeypatch.setattr(desktop, "local_mirror", fake, raising=False)
    monkeypatch.setitem(sys.modules, "desktop.local_mirror", fake)


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


def test_mirror_failure_is_recorded_not_swallowed(monkeypatch):
    """`mirror_once` вернул ok=False — это обязано попасть в состояние синка."""
    m = sync_runner.SyncManager()
    _put_mirror(monkeypatch, {"ok": False, "error": "нет активной сессии с сервером"})
    m._mirror_for_vue()
    assert m._mirror_error == "нет активной сессии с сервером", (
        "отказ зеркала выброшен — ровно тот дефект, ради которого тест написан")


def test_mirror_success_clears_the_error_and_stamps_time(monkeypatch):
    m = sync_runner.SyncManager()
    m._mirror_error = "прошлая беда"
    _put_mirror(monkeypatch, {"ok": True, "rows": 7})
    m._mirror_for_vue()
    assert m._mirror_error == "", "успех обязан снимать прежнюю жалобу"
    assert m._mirror_ok_at, "время последнего успеха обязано проставляться"


def test_missing_mirror_module_is_not_reported_as_a_failure(monkeypatch):
    """Сборка без серверного пакета рядом — штатная. Вечная жалоба там, где всё
    работает как задумано, приучает не читать сигнал (правило проекта)."""
    m = sync_runner.SyncManager()
    # Убираем ОБА пути, которыми `from desktop import local_mirror` может найти модуль:
    # атрибут пакета и запись в sys.modules. Уберёшь только второй — импорт удастся, и
    # тест проверит не тот случай (так и было до 09.09.2026).
    # ⚠️ Снятого атрибута МАЛО: `from desktop import local_mirror` тогда просто
    # импортирует подмодуль с диска заново, и мы проверим не тот случай. `None` в
    # sys.modules — штатный способ сказать импорту «этого модуля нет».
    monkeypatch.delattr(desktop, "local_mirror", raising=False)
    monkeypatch.setitem(sys.modules, "desktop.local_mirror", None)
    m._mirror_for_vue()
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
