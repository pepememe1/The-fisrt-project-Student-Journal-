"""Обратный ход заслонки сети из `tests/conftest.py`.

🔥 Куплено настоящим дефектом 05.09.2026: `test_login_bridge.py` ходил на БОЕВОЙ сервер
живыми попытками входа с неверным паролем — кормил его анти-брутфорс, писал в его журнал
аудита и краснел после нескольких прогонов подряд при исправном коде.

⚠️ Заслонка без обратного хода бесполезна ровно так же, как любой наш сторож: молча
переставшая ловить проверка неотличима от герметичного прогона. Поэтому здесь проверяется
не «сеть выключена», а что попытка выйти наружу ПАДАЕТ, и что петля при этом жива —
без петли половина клиентских тестов (локальный сервер программы) не имела бы смысла.
"""
import socket

import pytest


def test_outbound_connection_is_refused():
    """Попытка уйти наружу обязана падать ГРОМКО и с объяснением."""
    with pytest.raises(RuntimeError) as e:
        socket.create_connection(("esstu-gradebook.ru", 443), timeout=5)
    assert "наружу" in str(e.value).lower(), str(e.value)


def test_outbound_is_refused_by_ip_too():
    """Адрес боевой машины числом — тот же запрет. Имя можно обойти, вписав IP."""
    with pytest.raises(RuntimeError):
        socket.create_connection(("194.226.120.74", 443), timeout=5)


def test_raw_socket_connect_is_guarded_too():
    """Заслонка стоит на ОБОИХ входах.

    `socket.create_connection` — не единственный путь: часть клиентов зовёт
    `sock.connect()` напрямую, и заслонка только на первом дала бы ложное спокойствие."""
    s = socket.socket()
    try:
        with pytest.raises(RuntimeError):
            s.connect(("example.com", 80))
    finally:
        s.close()


def test_connect_ex_is_guarded_too():
    """🔥 Третий вход, и он был ОТКРЫТ до 05.09.2026.

    `connect_ex` — не обёртка над `connect`, а отдельный метод: он соединяется и
    возвращает код ошибки вместо исключения. Заслонка, стоящая только на `connect` и
    `create_connection`, оставляла его проходным — то есть была дырой ровно того вида,
    против которого заведена. Нашлось проверкой собственной правки, а не чтением."""
    s = socket.socket()
    try:
        with pytest.raises(RuntimeError):
            s.connect_ex(("esstu-gradebook.ru", 443))
    finally:
        s.close()


def test_loopback_still_works():
    """Петля обязана остаться живой: на ней поднимается локальный сервер программы."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        with socket.create_connection(srv.getsockname(), timeout=5) as c:
            assert c
    finally:
        srv.close()


def test_the_hardcoded_production_url_is_not_what_tests_see():
    """Второй пояс: адрес сервера в прогоне заведомо не боевой.

    ⚠️ Проверяется через ПРОДУКТ (`app_settings.get_api_url`), а не чтением переменной
    окружения: важно не то, что переменная выставлена, а то, что продукт её слушает.
    Порядок источников (БД → env → зашитый дефолт) уже однажды менялся."""
    from data import app_settings
    url = app_settings.get_api_url()
    assert "esstu-gradebook.ru" not in url, (
        "клиентский прогон видит БОЕВОЙ адрес (%r) — значит любой сетевой вызов в тестах "
        "уйдёт на прод" % url)
