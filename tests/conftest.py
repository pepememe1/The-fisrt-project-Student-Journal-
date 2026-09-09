"""
conftest.py — Фикстуры для тестов КЛИЕНТА (логика без GUI: core, sync_engine, data_store).

Главное: подменяем папку данных на временную ДО импорта клиентских модулей. core.py
вычисляет путь к локальной базе на этапе импорта (app_paths.data_dir() читает
LOCALAPPDATA), поэтому переменную окружения задаём в самом верху, пока ни один
модуль приложения ещё не импортирован — тесты не трогают рабочую базу разработчика.

Fixture fresh_db даёт чистую базу на каждый тест: удаляем файл БД и пере-инициализируем
таблицы, плюс сбрасываем сессионный флаг дельта-синка.
"""
import os
import glob
import tempfile

#Временная папка данных — строго до импорта core/data_store/sync_engine.
_DATA = tempfile.mkdtemp(prefix="gbai_client_tests_")
os.environ["LOCALAPPDATA"] = _DATA
#И папку ПРОГРАММЫ тоже: часть путей считается ОТ неё (app_paths.safe_app_file).
#Историческая причина — subjects.json лежал рядом с main.py, и прогон тестов затирал
#рабочий список предметов разработчика. Файла и модуля больше нет (17.08.2026, источник
#правды — портал ВСГУТУ), но подмена папки остаётся правильной: она изолирует прогон от
#дерева репозитория целиком, а не от одного файла.
os.environ["GRADEBOOK_APP_DIR"] = _DATA

import pytest

from data.core import DBManager, LOCAL_DB


@pytest.fixture()
def fresh_db():
    """Чистая локальная база на каждый тест (файл БД + WAL/SHM удаляем, таблицы заново)."""
    for f in glob.glob(LOCAL_DB + "*"):     #.db, -wal, -shm
        try:
            os.remove(f)
        except OSError:
            pass
    DBManager.init()
    #Сбрасываем «первый полный pull сессии», чтобы тесты дельты не влияли друг на друга.
    from sync import sync_engine
    sync_engine.reset_session_state()
    yield


# ── ЗАПРЕТ СЕТИ НАРУЖУ ВО ВСЁМ КЛИЕНТСКОМ ПРОГОНЕ ───────────────────────────────────
# 🔥 Куплено настоящим дефектом (05.09.2026). `tests/test_login_bridge.py` ходил на
# БОЕВОЙ сервер живыми попытками входа с неверным паролем: `app_settings.get_api_url()`
# при отсутствии настройки отдаёт ЗАШИТЫЙ боевой домен, а мост входа честно шёл туда
# второй ступенью. Следствий три, и «мигающие тесты» — самое безобидное из них:
#   • прогон КОРМИЛ анти-брутфорс прода. После нескольких прогонов подряд бой отвечал
#     429, мост отдавал 503 «нет связи» вместо 401, и два теста краснели — при
#     совершенно исправном коде;
#   • каждый прогон писал в БОЕВОЙ журнал аудита неудачные входы несуществующего
#     человека, то есть замусоривал след безопасности, который читают при инциденте;
#   • результат прогона зависел от интернета и от того, сколько раз его гоняли сегодня.
#
# ⚠️ Ловить это литеральным поиском домена по текстам тестов БЕСПОЛЕЗНО: домена в тесте
# нет, он приезжает из настроек продукта. Поэтому проверяется не текст, а ПОВЕДЕНИЕ —
# соединение наружу физически не открывается.
#
# Петля разрешена: локальный сервер программы поднимается на 127.0.0.1, и половина
# клиентских тестов без него бессмысленна.
import socket as _socket

_ALLOWED_HOSTS = frozenset({
    "127.0.0.1", "::1", "localhost", "0.0.0.0", "", None,
})


def _host_of(address):
    if isinstance(address, (tuple, list)) and address:
        return address[0]
    return address


def _is_local(host) -> bool:
    h = str(host or "").strip().strip("[]").lower()
    return h in _ALLOWED_HOSTS or h.startswith("127.")


_real_create_connection = _socket.create_connection
_real_socket_connect = _socket.socket.connect
#⚠️ `connect_ex` — ОТДЕЛЬНЫЙ метод, а не обёртка над `connect`: он соединяется и
#возвращает код ошибки вместо исключения. Заслонка только на первых двух оставляла его
#открытым, то есть дырой ровно того вида, против которого заведена. Найдено проверкой
#собственной правки, а не чтением.
_real_socket_connect_ex = _socket.socket.connect_ex


def _blocked(host):
    return RuntimeError(
        "тест пытается открыть соединение НАРУЖУ (%r). Клиентский прогон обязан быть "
        "герметичным: он не должен зависеть от интернета и не имеет права трогать "
        "боевой сервер. Подмени сетевой вызов заглушкой в самом тесте." % (host,))


def _guarded_create_connection(address, *a, **kw):
    host = _host_of(address)
    if not _is_local(host):
        raise _blocked(host)
    return _real_create_connection(address, *a, **kw)


def _guarded_socket_connect(self, address, *a, **kw):
    host = _host_of(address)
    #У AF_UNIX и прочих не-IP адрес это путь/байты — их не трогаем.
    if isinstance(address, (tuple, list)) and not _is_local(host):
        raise _blocked(host)
    return _real_socket_connect(self, address, *a, **kw)


def _guarded_socket_connect_ex(self, address, *a, **kw):
    host = _host_of(address)
    if isinstance(address, (tuple, list)) and not _is_local(host):
        raise _blocked(host)
    return _real_socket_connect_ex(self, address, *a, **kw)


_socket.create_connection = _guarded_create_connection
_socket.socket.connect = _guarded_socket_connect
_socket.socket.connect_ex = _guarded_socket_connect_ex

#Второй пояс: даже если заслонка выше однажды перестанет ловить какой-то транспорт,
#адрес сервера в прогоне заведомо не боевой. Env читается ДО зашитого умолчания
#(`app_settings.get_api_url`), а порт заведомо закрыт — отказ будет быстрым.
os.environ.setdefault("GRADEBOOK_API_URL", "http://127.0.0.1:9")
