"""
test_desktop_update.py — автообновление десктопа (3.5.6).

Проверяем ровно то, что на чужом компьютере сломается молча: сравнение версий,
выбор патча, отказ от битого файла и подмену .exe переименованием.

Сеть НЕ трогаем: `urlopen` подменяется. Настоящая закачка проверяется живым прогоном
после сборки, а тесты обязаны быть детерминированными.
"""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import desktop_update as DU


# ── Версии ────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("Release 3.5.6", (3, 5, 6)),
    ("3.5.6", (3, 5, 6)),
    ("v3.5.6-test", (3, 5, 6)),
    ("Release 3.6", (3, 6)),
    ("", ()),
    ("без цифр", ()),
])
def test_parse_version(text, expected):
    assert DU.parse_version(text) == expected


def test_is_newer_compares_numerically_not_as_text():
    """Главная ловушка строкового сравнения: «3.5.10» ДОЛЖНА быть новее «3.5.9»."""
    assert DU.is_newer("3.5.10", "3.5.9")
    assert not DU.is_newer("3.5.9", "3.5.10")


def test_is_newer_treats_missing_parts_as_zero():
    assert not DU.is_newer("3.6", "3.6.0")
    assert not DU.is_newer("3.6.0", "3.6")
    assert DU.is_newer("3.6.1", "3.6")


def test_unparseable_version_is_never_newer():
    """Неразборчивый манифест не имеет права запустить обновление: лучше остаться на
    рабочей сборке, чем поставить неизвестно что."""
    assert not DU.is_newer("мусор", "3.5.5")
    assert not DU.is_newer("3.5.6", "мусор")


def test_patch_name_is_single_source_of_truth():
    assert DU.patch_name("Release 3.5.5", "3.5.6") == "3.5.5-3.5.6.patch"


# ── Выбор патча ───────────────────────────────────────────────────────────────────
def _manifest(patches=None, version="3.5.6"):
    return {"version": version,
            "full": {"file": f"GradeBookAI-{version}.exe", "sha256": "f" * 64, "size": 10},
            "patches": patches if patches is not None else []}


def _patch(frm="3.5.5", to="3.5.6"):
    return {"from": frm, "to": to, "file": DU.patch_name(frm, to),
            "sha256": "a" * 64, "target_sha256": "f" * 64, "size": 5}


def test_pick_patch_matches_current_version():
    assert DU.pick_patch(_manifest([_patch()]), "Release 3.5.5")["file"] == "3.5.5-3.5.6.patch"


def test_pick_patch_ignores_patch_from_another_version():
    """Патч 3.5.4→3.5.6 не годится тому, кто сидит на 3.5.5 — иначе наложим на не тот
    файл и получим мусор вместо программы."""
    assert DU.pick_patch(_manifest([_patch("3.5.4", "3.5.6")]), "3.5.5") is None


def test_pick_patch_requires_checksums():
    """Патч без хешей не берём: применить его — записать на диск непроверяемые байты."""
    bad = _patch()
    bad.pop("target_sha256")
    assert DU.pick_patch(_manifest([bad]), "3.5.5") is None


def test_no_patch_when_already_latest():
    assert DU.pick_patch(_manifest([_patch()]), "3.5.6") is None
    assert not DU.has_update(_manifest(), "3.5.6")
    assert DU.has_update(_manifest(), "3.5.5")


# ── Клиент: подмена .exe ──────────────────────────────────────────────────────────
@pytest.fixture()
def updater_env(tmp_path, monkeypatch):
    """Изолированная «установка»: свой каталог программы и свои данные."""
    monkeypatch.setenv("GRADEBOOK_APP_DIR", str(tmp_path))
    monkeypatch.setenv("GRADEBOOK_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(exist_ok=True)
    for mod in ("app_paths", "updater"):
        sys.modules.pop(mod, None)
    from data import updater
    import app_paths
    monkeypatch.setattr(app_paths, "data_dir", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(updater.app_paths, "data_dir", lambda: str(tmp_path / "data"))
    exe = tmp_path / updater.EXE_NAME
    exe.write_bytes(b"OLD-EXE")
    return updater, tmp_path, exe


def _stage(updater, tmp_path, payload=b"NEW-EXE", version="3.5.6"):
    """Кладёт «скачанное обновление» рядом с .exe, как это делает check_and_fetch."""
    new = tmp_path / (updater.EXE_NAME + updater.NEW_SUFFIX)
    new.write_bytes(payload)
    info = {"version": version, "path": str(new), "sha256": DU.sha256_bytes(payload)}
    (tmp_path / "data" / updater.PENDING_NAME).write_text(json.dumps(info), encoding="utf-8")
    return new


def test_apply_pending_replaces_exe_by_rename(updater_env):
    """Подмена именно ПЕРЕИМЕНОВАНИЕМ: Windows не даёт перезаписать запущенный .exe,
    но даёт его переименовать — на этом и держится вся установка."""
    updater, tmp_path, exe = updater_env
    _stage(updater, tmp_path)

    assert updater.apply_pending("3.5.5") is True
    assert exe.read_bytes() == b"NEW-EXE"
    #Старый файл сохранён рядом (пока процесс жив, удалить его нельзя).
    assert (tmp_path / (updater.EXE_NAME + updater.OLD_SUFFIX)).read_bytes() == b"OLD-EXE"
    assert not (tmp_path / "data" / updater.PENDING_NAME).exists()


def test_apply_pending_rejects_corrupted_download(updater_env):
    """Файл побился после скачивания (диск/антивирус) — ставить его нельзя."""
    updater, tmp_path, exe = updater_env
    new = _stage(updater, tmp_path)
    new.write_bytes(b"CORRUPTED")          #хеш в метке больше не сходится

    assert updater.apply_pending("3.5.5") is False
    assert exe.read_bytes() == b"OLD-EXE", "рабочая программа не должна пострадать"
    assert not new.exists(), "битый файл обязан быть убран, а не ждать следующего запуска"


def test_apply_pending_skips_when_already_up_to_date(updater_env):
    """Метка от старого обновления не должна «откатывать» уже обновлённую программу."""
    updater, tmp_path, exe = updater_env
    _stage(updater, tmp_path, version="3.5.5")

    assert updater.apply_pending("3.5.6") is False
    assert exe.read_bytes() == b"OLD-EXE"


def test_apply_pending_without_update_does_nothing(updater_env):
    updater, tmp_path, exe = updater_env
    assert updater.apply_pending("3.5.6") is False
    assert exe.read_bytes() == b"OLD-EXE"


def test_cleanup_old_removes_previous_exe(updater_env):
    updater, tmp_path, _exe = updater_env
    old = tmp_path / (updater.EXE_NAME + updater.OLD_SUFFIX)
    old.write_bytes(b"PREV")
    updater.cleanup_old()
    assert not old.exists()


# ── Интерактивный апдейтер: диалог да/нет, перезапуск ─────────────────────────────
class _FakeUser32:
    """Подмена ctypes.windll.user32 — реальный MessageBoxW НИКОГДА не должен всплывать
    в тестах (завис бы, ожидая клика человека)."""
    def __init__(self, answer=6):
        self.answer = answer
        self.calls = []

    def MessageBoxW(self, hwnd, message, title, flags):
        self.calls.append((message, title, flags))
        return self.answer


def test_ask_yes_no_true_on_idyes(updater_env, monkeypatch):
    updater, _tmp, _exe = updater_env
    fake = _FakeUser32(answer=6)                                # IDYES
    monkeypatch.setattr(updater.ctypes, "windll",
                        type("W", (), {"user32": fake})(), raising=False)
    #🔥 Без подмены платформы тест НЕ ПРОВЕРЯЕТ НИЧЕГО. `ask_yes_no` первой
    #строкой выходит по `sys.platform != "win32"`, то есть на Linux до
    #подменённого `windll` дело не доходит вовсе. Соседний тест (ответ «нет»)
    #из-за этого годами был ЗЕЛЁНЫМ ПО НЕВЕРНОЙ ПРИЧИНЕ: ждал False и получал
    #его от заслонки платформы, а не от диалога. Докстринг функции при этом
    #прямо утверждал обратное — «тесты подменяют ctypes.windll, а не
    #полагаются на эту ветку». Поправлено 29.08.2026 вместе с докстрингом.
    monkeypatch.setattr(updater.sys, "platform", "win32")
    assert updater.ask_yes_no("Заголовок", "Текст") is True
    assert fake.calls and fake.calls[0][1] == "Заголовок"


def test_ask_yes_no_false_on_idno(updater_env, monkeypatch):
    updater, _tmp, _exe = updater_env
    fake = _FakeUser32(answer=7)                                # IDNO
    monkeypatch.setattr(updater.ctypes, "windll",
                        type("W", (), {"user32": fake})(), raising=False)
    #🔥 Без подмены платформы тест НЕ ПРОВЕРЯЕТ НИЧЕГО. `ask_yes_no` первой
    #строкой выходит по `sys.platform != "win32"`, то есть на Linux до
    #подменённого `windll` дело не доходит вовсе. Соседний тест (ответ «нет»)
    #из-за этого годами был ЗЕЛЁНЫМ ПО НЕВЕРНОЙ ПРИЧИНЕ: ждал False и получал
    #его от заслонки платформы, а не от диалога. Докстринг функции при этом
    #прямо утверждал обратное — «тесты подменяют ctypes.windll, а не
    #полагаются на эту ветку». Поправлено 29.08.2026 вместе с докстрингом.
    monkeypatch.setattr(updater.sys, "platform", "win32")
    assert updater.ask_yes_no("Заголовок", "Текст") is False


def test_show_info_never_raises_if_dialog_fails(updater_env, monkeypatch):
    """Показ информационного окна — best-effort: сбой не должен ронять программу."""
    updater, _tmp, _exe = updater_env

    class Boom:
        def MessageBoxW(self, *a, **kw):
            raise OSError("нет доступа к рабочему столу")
    monkeypatch.setattr(updater.ctypes, "windll",
                        type("W", (), {"user32": Boom()})(), raising=False)
    updater.show_info("Заголовок", "Текст")                    # не должно бросить


def test_relaunch_noop_when_not_frozen(updater_env, monkeypatch):
    """В dev-запуске (не собранный .exe) перезапускать нечего."""
    updater, _tmp, _exe = updater_env
    called = []
    monkeypatch.setattr(updater.app_paths, "is_frozen", lambda: False)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: called.append(a))
    updater.relaunch()
    assert not called


def test_relaunch_spawns_new_process_when_frozen(updater_env, monkeypatch):
    updater, tmp_path, exe = updater_env
    calls = []
    monkeypatch.setattr(updater.app_paths, "is_frozen", lambda: True)
    monkeypatch.setattr("subprocess.Popen", lambda args, **kw: calls.append(args))
    updater.relaunch()
    assert calls == [[str(exe)]]


def test_check_and_prompt_no_update_never_asks(updater_env, monkeypatch):
    """Нет обновления — диалог вообще не должен показываться."""
    updater, _tmp, _exe = updater_env
    monkeypatch.setattr(updater, "fetch_manifest", lambda url, timeout=6: _manifest(version="3.5.5"))
    monkeypatch.setattr(updater, "ask_yes_no",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("не должен спрашивать")))
    assert updater.check_and_prompt("https://x", "3.5.5") is False


def test_check_and_prompt_decline_closes_without_downloading(updater_env, monkeypatch):
    """Отказ («нет») — программа должна закрыться, но НИЧЕГО не скачивать."""
    updater, _tmp, _exe = updater_env
    monkeypatch.setattr(updater, "fetch_manifest", lambda url, timeout=6: _manifest())
    monkeypatch.setattr(updater, "ask_yes_no", lambda *a, **kw: False)
    monkeypatch.setattr(updater, "check_and_fetch",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("не должен качать")))
    assert updater.check_and_prompt("https://x", "3.5.5") is True


def test_check_and_prompt_accept_downloads_applies_and_relaunches(updater_env, monkeypatch):
    """Согласие («да») — скачать, установить, перезапустить одним циклом."""
    updater, _tmp, _exe = updater_env
    relaunched = []
    monkeypatch.setattr(updater, "fetch_manifest", lambda url, timeout=6: _manifest())
    monkeypatch.setattr(updater, "ask_yes_no", lambda *a, **kw: True)
    monkeypatch.setattr(updater, "check_and_fetch",
                        lambda *a, **kw: {"version": "3.5.6", "path": "x", "sha256": "f" * 64})
    monkeypatch.setattr(updater, "apply_pending", lambda *a, **kw: True)
    monkeypatch.setattr(updater, "relaunch", lambda: relaunched.append(1))
    assert updater.check_and_prompt("https://x", "3.5.5") is True
    assert relaunched == [1]


def test_check_and_prompt_accept_but_download_fails_falls_back_to_normal_startup(updater_env, monkeypatch):
    """Согласились, но скачать/поставить не вышло — сообщаем и продолжаем как обычно
    (НЕ закрываем программу: сеть подвела — не повод не пустить человека в журнал)."""
    updater, _tmp, _exe = updater_env
    shown = []
    relaunched = []
    monkeypatch.setattr(updater, "fetch_manifest", lambda url, timeout=6: _manifest())
    monkeypatch.setattr(updater, "ask_yes_no", lambda *a, **kw: True)
    monkeypatch.setattr(updater, "check_and_fetch", lambda *a, **kw: {})
    monkeypatch.setattr(updater, "show_info", lambda *a, **kw: shown.append(a))
    monkeypatch.setattr(updater, "relaunch", lambda: relaunched.append(1))
    assert updater.check_and_prompt("https://x", "3.5.5") is False
    assert shown and not relaunched


def test_fetch_manifest_survives_offline(updater_env, monkeypatch):
    """Нет сети — это штатное состояние, а не ошибка: возвращаем пустой манифест."""
    updater, _tmp, _exe = updater_env

    def boom(*a, **kw):
        raise OSError("нет сети")
    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    assert updater.fetch_manifest("https://example.invalid") == {}


def test_fetch_manifest_empty_url_does_not_touch_network(updater_env, monkeypatch):
    """Пустой адрес сервера (офлайн-режим) — в сеть не ходим вовсе."""
    updater, _tmp, _exe = updater_env
    called = []
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **kw: called.append(1))
    assert updater.fetch_manifest("") == {}
    assert not called


# ── Дельта: собрать и наложить ────────────────────────────────────────────────────
def test_patch_roundtrip_reconstructs_new_build(tmp_path):
    """Патч, собранный сборщиком, обязан дать БАЙТ-В-БАЙТ новый файл.

    Это контракт между `tools/make_desktop_patch.py` и `data/updater.py`: разъедутся —
    человек получит .exe, который не запустится."""
    zstandard = pytest.importorskip("zstandard")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools"))
    import make_desktop_patch as MP

    #Похоже на реальность: две сборки, различающиеся куском в середине.
    old = tmp_path / "old.exe"
    new = tmp_path / "new.exe"
    body = bytes(range(256)) * 400
    old.write_bytes(body + b"VERSION-3.5.5" + body)
    new.write_bytes(body + b"VERSION-3.5.6" + body)
    patch = tmp_path / "p.patch"

    size = MP.build_patch(str(old), str(new), str(patch))
    assert size > 0

    #Накладываем тем же кодом, что и клиент.
    sys.modules.pop("updater", None)
    from data import updater
    out = tmp_path / "out.exe"
    assert updater._apply_patch(str(old), str(patch), str(out)) is True
    assert out.read_bytes() == new.read_bytes()


def test_patch_is_much_smaller_than_full_file(tmp_path):
    """Ради чего всё затевалось: дельта похожих сборок должна быть в разы меньше файла."""
    pytest.importorskip("zstandard")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools"))
    import make_desktop_patch as MP

    old = tmp_path / "old.exe"
    new = tmp_path / "new.exe"
    body = os.urandom(2 * 1024 * 1024)       #несжимаемое «тело» сборки
    old.write_bytes(body + b"A" * 1024)
    new.write_bytes(body + b"B" * 1024)
    patch = tmp_path / "p.patch"

    size = MP.build_patch(str(old), str(new), str(patch))
    assert 0 < size < os.path.getsize(new) * 0.2


# ── Защищённый канал (находка аудита 08.08.2026) ──────────────────────────────────
# Автообновление — единственное место продукта, где чужой байт становится
# исполняемым кодом. Тройная сверка SHA-256 от подмены не спасает: хеш едет ТЕМ ЖЕ
# каналом, что и файл. Значит проверять надо канал, а не только содержимое.
@pytest.mark.parametrize("url,allowed", [
    ("https://esstu-gradebook.ru", True),      # боевой адрес
    ("https://192.168.1.50:8443", True),       # свой сервер, но с TLS
    ("http://127.0.0.1:8000", True),           # петля: встать в середину негде
    ("http://localhost:8000", True),
    ("", True),                                # адреса нет вовсе — качать неоткуда
    ("http://194.226.120.74:8000", False),     # боевой IP открытым текстом
    ("http://192.168.1.50:8000", False),       # ровно то, что создаёт «ПК как сервер ЛВС»
    ("http://gradebook.local:8000", False),
])
def test_transport_guard_allows_only_secure_channels(updater_env, url, allowed):
    updater, _tmp, _exe = updater_env
    assert updater._transport_ok(url) is allowed


def test_manifest_over_plain_http_never_touches_network(updater_env, monkeypatch):
    """По небезопасному адресу запрос не делается ВООБЩЕ.

    Важно именно «не делается», а не «результат отбрасывается»: сам факт запроса уже
    сообщает тому, кто сидит в середине, что здесь есть клиент, готовый принять .exe.
    """
    updater, _tmp, _exe = updater_env
    def boom(*a, **kw):                       # pragma: no cover — не должно вызваться
        raise AssertionError("updater полез в сеть по незащищённому каналу")
    monkeypatch.setattr(updater, "_get", boom)
    assert updater.fetch_manifest("http://192.168.1.50:8000") == {}


def test_prompt_over_plain_http_does_not_offer_update(updater_env, monkeypatch):
    """Диалог «установить обновление?» по http не показывается, и программа живёт дальше.

    Возврат False здесь принципиален: True означает «закройся прямо сейчас», и человек
    остался бы без журнала из-за того, что администратор поднял сервер без TLS.
    """
    updater, _tmp, _exe = updater_env
    monkeypatch.setattr(updater, "_get", lambda *a, **kw: json.dumps(
        {"version": "9.9.9", "file": "GradeBookAI.exe", "sha256": "0" * 64}
    ).encode("utf-8"))
    asked = []
    monkeypatch.setattr(updater, "ask_yes_no", lambda *a: asked.append(a) or True)
    assert updater.check_and_prompt("http://192.168.1.50:8000", "3.6.9") is False
    assert not asked, "по незащищённому каналу обновление даже не предлагается"


def test_prompt_over_https_still_offers_update(updater_env, monkeypatch):
    """Обратная сторона: заслонка не должна отключить автообновление на боевом https.

    Без этой проверки «починка» могла бы просто выключить обновления всем и остаться
    незамеченной — тесты были бы зелёными, а продукт перестал бы обновляться.
    """
    updater, _tmp, _exe = updater_env
    monkeypatch.setattr(updater, "_get", lambda *a, **kw: json.dumps(
        {"version": "9.9.9", "file": "GradeBookAI.exe", "sha256": "0" * 64}
    ).encode("utf-8"))
    asked = []
    monkeypatch.setattr(updater, "ask_yes_no", lambda *a: asked.append(a) or False)
    assert updater.check_and_prompt("https://esstu-gradebook.ru", "3.6.9") is True
    assert asked, "по https обновление обязано предлагаться как раньше"


# ─────────────────────────────────────────────────────────────────────────────────
# ПОДПИСЬ ВЫПУСКА (Ed25519), 10.09.2026
#
# Закрывает пункт, честно висевший в README: контрольная сумма едет ТЕМ ЖЕ каналом, что
# и файл, — она ловит битую закачку и не ловит подмену.
# ─────────────────────────────────────────────────────────────────────────────────

def _keypair():
    """Пара ключей для опыта. Пропускаем, только если пакета нет ВООБЩЕ."""
    ed = pytest.importorskip(
        "cryptography.hazmat.primitives.asymmetric.ed25519",
        reason="cryptography объявлен обязательной зависимостью (§6); "
               "без него проверять подпись нечем")
    from cryptography.hazmat.primitives import serialization

    priv = ed.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()
    return priv, pub


def _sign(priv, version, sha):
    return priv.sign(DU.signing_payload(version, sha)).hex()


def test_a_properly_signed_release_is_accepted(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setattr(DU, "UPDATE_PUBLIC_KEYS", (pub,))
    sha = "a" * 64
    assert DU.release_signature_ok("3.9.4", sha, _sign(priv, "3.9.4", sha))


def test_a_substituted_file_fails_even_with_a_real_signature(monkeypatch):
    """Главный случай, ради которого всё затевалось.

    Подменивший .exe перепишет и хеш в манифесте — и старая проверка сверила бы подделку
    саму с собой. Подпись относится к КОНКРЕТНОМУ хешу, поэтому чужой файл её не проходит.
    """
    priv, pub = _keypair()
    monkeypatch.setattr(DU, "UPDATE_PUBLIC_KEYS", (pub,))
    good = _sign(priv, "3.9.4", "a" * 64)
    assert not DU.release_signature_ok("3.9.4", "b" * 64, good), (
        "подпись от одного файла подошла к другому — она не привязана к хешу")


def test_signature_of_an_older_release_does_not_unlock_a_newer_manifest(monkeypatch):
    """🔥 ОТКАТ НА СТАРУЮ ВЕРСИЮ — отдельная атака, и версия входит в подпись ради неё.

    Без версии в подписываемом наша ЖЕ настоящая подпись от выпуска 3.8.0 годилась бы
    для манифеста, объявляющего 3.9.4: получивший канал откатил бы парк на прежнюю,
    уязвимую сборку, и каждая проверка при этом сошлась бы.
    """
    priv, pub = _keypair()
    monkeypatch.setattr(DU, "UPDATE_PUBLIC_KEYS", (pub,))
    sha = "c" * 64
    old_sig = _sign(priv, "3.8.0", sha)
    assert DU.release_signature_ok("3.8.0", sha, old_sig)          #для своей — годна
    assert not DU.release_signature_ok("3.9.4", sha, old_sig), (
        "подпись прежнего выпуска подошла к новому — откат проходит с настоящей подписью")


def test_a_foreign_key_is_rejected(monkeypatch):
    """Подпись, сделанная не нашим ключом, не проходит."""
    priv_theirs, _ = _keypair()
    _, pub_ours = _keypair()
    monkeypatch.setattr(DU, "UPDATE_PUBLIC_KEYS", (pub_ours,))
    sha = "d" * 64
    assert not DU.release_signature_ok("3.9.4", sha, _sign(priv_theirs, "3.9.4", sha))


def test_key_rotation_keeps_both_keys_working(monkeypatch):
    """Смена ключа не имеет права разорвать обновления.

    С единственным ключом смена означала бы, что все, кто ещё не обновился, теряют
    обновления НАВСЕГДА — чинить пришлось бы руками у каждого. Поэтому ключей список.
    """
    old_priv, old_pub = _keypair()
    new_priv, new_pub = _keypair()
    monkeypatch.setattr(DU, "UPDATE_PUBLIC_KEYS", (new_pub, old_pub))
    sha = "e" * 64
    assert DU.release_signature_ok("3.9.4", sha, _sign(new_priv, "3.9.4", sha))
    assert DU.release_signature_ok("3.9.4", sha, _sign(old_priv, "3.9.4", sha))


def test_garbage_in_the_signature_field_is_rejected(monkeypatch):
    """Мусор вместо подписи — отказ, а не исключение на чужом компьютере."""
    _, pub = _keypair()
    monkeypatch.setattr(DU, "UPDATE_PUBLIC_KEYS", (pub,))
    for junk in ("", "нет", "zz" * 64, "ab", None, "0" * 127):
        assert not DU.release_signature_ok("3.9.4", "f" * 64, junk), junk


def test_while_no_key_is_configured_behaviour_is_exactly_as_before(monkeypatch):
    """🔴 НАЗВАННАЯ ГРАНИЦА, а не забытый случай.

    Пока открытый ключ не заведён, продукт НЕ ЗАЩИЩЁН от подмены обновления — и этот
    тест существует, чтобы факт нельзя было потерять из виду. Строгая проверка при
    пустом списке означала бы, что первая же собранная сборка перестала обновляться у
    всех разом; вписать ключ может только тот, у кого есть закрытая половина.
    """
    monkeypatch.setattr(DU, "UPDATE_PUBLIC_KEYS", ())
    assert not DU.signature_required()
    assert DU.release_signature_ok("3.9.4", "a" * 64, "")          #ставим как раньше
    #Но САМА проверка при этом честно отвечает «не проверено», а не «проверено и ок»:
    #иначе пустой список выглядел бы как успешная проверка.
    assert not DU.verify_release_signature("3.9.4", "a" * 64, "0" * 128)


def test_signing_is_wired_into_publishing_once_a_key_exists():
    """🔥 «ОБЕЩАНИЕ БЕЗ ВЫЗЫВАЮЩЕГО» — В ВЫКЛАДКЕ, И ОНО ДОЖИЛО ДО 12.09.2026.

    `tools/sign_release.py` написан, его поведение под тестами, а
    `tools/publish_desktop_update.sh` НЕ ЗОВЁТ ЕГО НИ РАЗУ: ни шага подписи, ни ворот
    `--verify`. При этом CLAUDE.md уверенно писал, что подпись стала шагом выкладки, —
    и по этой записи я отказался выкладывать готовую сборку, сославшись на ворота,
    которых не существует. Документ соврал, а проверить было нечем: сторожа не было.

    🔑 ПОЧЕМУ СТОРОЖ УСЛОВНЫЙ, А НЕ БЕЗУСЛОВНЫЙ. Пока `UPDATE_PUBLIC_KEYS` пуст, подпись
    не требуется (`release_signature_ok` пропускает всё), и выкладка без неё — законное
    прежнее поведение. Опасность появляется РОВНО в момент заведения ключа: клиенты новых
    сборок начнут требовать подпись, а выкладка продолжит класть неподписанный манифест —
    он зальётся успешно, сервер отдаст его с кодом 200, шаг «версия обновилась» пройдёт,
    а программа у людей молча перестанет обновляться. Узнали бы через недели.

    ⚠️ Поэтому проверка краснеет ровно тогда, когда опасность становится настоящей, и
    молчит, пока её нет. Безусловный вариант краснел бы сегодня на исправном продукте и
    подталкивал бы «просто обновить ожидание» — наша записанная грабля.

    ⚠️ Вторая половина — про ВОРОТА. Подписать и не проверить результат значит получить
    «подпись сделана» там, где она не сходится: файл подписан ДРУГИМ ключом, обрезан,
    записан не в то поле. Сверять «есть ли поле sig» бессмысленно — подпись чужим ключом
    тоже поле; проверять надо ТОЙ ЖЕ функцией, какой проверяет клиент.
    """
    import pathlib

    publish = (pathlib.Path(__file__).resolve().parents[1]
               / "tools" / "publish_desktop_update.sh").read_text(encoding="utf-8")
    calls_signer = "sign_release.py" in publish
    if DU.UPDATE_PUBLIC_KEYS:
        assert calls_signer, (
            "ключ подписи ЗАВЕДЁН (UPDATE_PUBLIC_KEYS непуст), а publish_desktop_update.sh "
            "не зовёт tools/sign_release.py: манифест уедет неподписанным, зальётся "
            "успешно и молча лишит обновлений всех, у кого сборка с этим ключом")
    #Подписывает — обязан и проверить, причём той же дверью, что клиент.
    if calls_signer:
        assert "release_signature_ok" in publish or "--verify" in publish, (
            "выкладка подписывает манифест и не проверяет результат: подпись чужим или "
            "испорченным ключом уедет на сервер как настоящая")


def test_the_public_key_list_itself_is_sane():
    """🔑 СТОРОЖ НАД САМИМ КЛЮЧОМ, которого не было (заведён 12.09.2026).

    CLAUDE.md утверждал, что «под сторожем и сам ключ: 64 hex, без дублей». Проверено —
    такого теста не существовало: все проверки подписи подменяют `UPDATE_PUBLIC_KEYS`
    через `monkeypatch`, то есть НАСТОЯЩИЙ список не смотрел никто.

    ⚠️ Цена опечатки здесь максимальная и молчаливая: неверный открытый ключ не ломает
    ни сборку, ни выкладку — он ломает ПРОВЕРКУ на компьютере человека, и обновления
    перестают ставиться у всех разом, а выглядит это как «что-то с интернетом».
    ⚠️ Дубль ключа — не опечатка, а признак незавершённой ротации: старый оставили, новый
    дописали дважды. Сам по себе он безвреден, но означает, что список правили вслепую.
    """
    keys = list(DU.UPDATE_PUBLIC_KEYS)
    for k in keys:
        assert isinstance(k, str), f"ключ не строка: {type(k).__name__}"
        assert re.fullmatch(r"[0-9a-fA-F]{64}", k.strip()), (
            f"открытый ключ Ed25519 — ровно 64 hex-символа, а тут {len(k.strip())}: "
            f"{k.strip()[:12]}…")
    assert len(keys) == len({k.strip().lower() for k in keys}), (
        "в UPDATE_PUBLIC_KEYS есть дубли — список правили вслепую")


def test_the_payload_is_computed_in_one_place():
    """🔒 Подписывающий и проверяющий обязаны считать байты ОДНОЙ функцией.

    Две копии формата разойдутся молча, и выглядеть это будет как «подпись не сходится»,
    то есть как попытка подмены, а не как наша опечатка. Проверяем, что инструмент
    подписи не завёл свою версию.
    """
    import inspect
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "tools" / "sign_release.py"
    text = src.read_text(encoding="utf-8")
    assert "DU.signing_payload(" in text, (
        "tools/sign_release.py считает подписываемые байты сам — заведётся вторая копия "
        "формата, и она разойдётся с проверкой")
    assert "_SIG_DOMAIN" not in text, (
        "приставка области продублирована в инструменте вместо использования общей")
    assert inspect.isfunction(DU.signing_payload)
