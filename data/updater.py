"""
updater.py — КЛИЕНТСКАЯ часть автообновления десктопа (скачать → проверить → подменить).

Правила формата (какая версия новее, как зовётся патч, чем считается хеш) живут в общем
корневом `desktop_update.py` — том же, что использует сборщик патчей и сервер. Здесь
только то, что бывает ТОЛЬКО на компьютере пользователя: сеть, диск и подмена .exe.

── Как это работает ──────────────────────────────────────────────────────────────────
1. `check_and_fetch()` (фоновый поток при старте) берёт `<сервер>/desktop/updates`,
   сравнивает с `APP_VERSION` и, если есть что ставить, качает ДЕЛЬТУ (обычно единицы
   МБ) либо, если дельты нет, .exe целиком.
2. Собранный новый .exe кладётся РЯДОМ как `GradeBookAI.new.exe` и проверяется по
   SHA-256 из манифеста. Не сошёлся — файл удаляется, обновление не состоится.
3. `apply_pending()` (в самом начале следующего запуска, ДО открытия окна) подменяет
   файлы и просит перезапуск.

⚠️ **Почему подмена именно переименованием.** Windows не даёт ПЕРЕЗАПИСАТЬ запущенный
.exe, но спокойно даёт его ПЕРЕИМЕНОВАТЬ. Поэтому: текущий → `.old`, новый → на место
текущего. Уборка `.old` — на следующем запуске (пока процесс жив, файл занят).

⚠️ **Обновление НИКОГДА не выполняется молча в момент работы.** Человек может вести
журнал; подмена файла под работающей программой — это как минимум неожиданный
перезапуск. Скачиваем в фоне, ставим на старте.

🔒 **ТОЛЬКО ЗАЩИЩЁННЫЙ КАНАЛ.** Обновление по http к удалённому хосту не делается
вообще — см. `_transport_ok()` ниже. Это единственное место в продукте, где чужой
байт становится исполняемым кодом на компьютере пользователя, поэтому здесь не
предупреждение, а отказ.
"""
import ctypes
import os
import sys
import json
import tempfile
import urllib.request
import urllib.error

import log
import app_paths
from data import app_settings          # is_secure_transport — единственная проверка канала в продукте
import desktop_update as DU

#Модуль log отдаёт логгер через get(), прямых info/warn у него нет.
_LOG = log.get("updater")

EXE_NAME = "GradeBookAI.exe"
NEW_SUFFIX = ".new.exe"
OLD_SUFFIX = ".old.exe"
#Метка рядом с новым файлом: какая версия скачана и какой у неё хеш. Без неё после
#перезапуска нечем отличить готовое к установке обновление от мусора, оставшегося от
#оборванной закачки.
PENDING_NAME = "pending_update.json"

_TIMEOUT = 30           #секунд на запрос: обновление не должно подвешивать запуск
_MAX_EXE_BYTES = 300 * 1024 * 1024   #потолок здравого смысла (.exe ~48 МБ)


def _exe_path() -> str:
    """Путь к САМОМУ .exe программы (не к временной распаковке onefile).

    ⚠️ `sys.executable` НЕ годится под Nuitka onefile (единственный релизный сборщик,
    §8.1 CLAUDE.md) — он указывает на временный bootstrap-интерпретатор в %TEMP%, а
    не на настоящий .exe (см. app_paths.py::_nuitka_onefile_dir, там же разбор живого
    бага «автообновление не работает»: апдейтер молча подменял файл не там). Берём
    ИМЯ файла из EXE_NAME и ПАПКУ из app_paths.app_dir() — она уже учитывает разницу
    PyInstaller/Nuitka сама, второй копии этой логики здесь не нужно."""
    return os.path.join(app_paths.app_dir(), EXE_NAME)


def _pending_path() -> str:
    return os.path.join(app_paths.data_dir(), PENDING_NAME)


def _new_exe_path() -> str:
    return _exe_path() + NEW_SUFFIX


def _old_exe_path() -> str:
    return _exe_path() + OLD_SUFFIX


def _get(url: str, timeout: int = _TIMEOUT) -> bytes:
    #SAST B310: схему проверяет `_transport_ok()` ДО вызова (строка 142) —
    #http и файловые схемы отвергаются там же, где объяснено почему.
    req = urllib.request.Request(url, headers={"User-Agent": "GradeBookAI-updater/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
        return r.read()


def _transport_ok(base_url: str) -> bool:
    """Можно ли вообще качать обновление с этого адреса.

    🔒 Находка аудита безопасности (08.08.2026). Обновление берётся ЛЮБОЙ схемой, и
    тройная сверка SHA-256 от подмены не спасает: хеш приезжает ПО ТОМУ ЖЕ каналу, что
    и файл. То есть он защищает от битой закачки, а не от того, кто эту закачку
    подменяет — такой подсунет свой .exe вместе со своим же хешем, и всё сойдётся.

    Сценарий не гипотетический. Адрес сервера человек задаёт РУКАМИ в настройках, и
    `http://<адрес>:8000` там оказывается легко — так был настроен целый колледж, пока у
    нас существовала кнопка «этот ПК как сервер для ЛВС» (удалена 15.08.2026 вместе с
    `server_control.py`). Любой, кто способен встать в середину — подменённая точка Wi-Fi,
    ARP- или DNS-спуфинг в учебной сети — отдаёт свой файл, программа сверяет его с его же
    хешем, ставит подменой и САМА перезапускается. Это удалённое выполнение кода на всех
    машинах разом.
    ⚠️ Удаление той кнопки заслонку НЕ отменяет: поле адреса осталось, а с ним и дверь.

    Поэтому здесь ОТКАЗ, а не предупреждение (в отличие от `SyncClient`, где по http
    течёт содержимое, но чужой код не исполняется). Правило то же, что у дев-ключа
    подписи JWT в §аудита: тихо работающую дыру не чинят — о ней не знают.

    ⚠️ ЦЕНА ЭТОГО РЕШЕНИЯ НАЗВАНА ЯВНО: колледж, раздающий программу с ЛВС-сервера по
    http, перестаёт получать автообновления совсем — до тех пор, пока у того сервера не
    появится https. Это осознанный размен: не обновиться хуже, чем обновиться, но
    получить чужой код — несопоставимо хуже обоих.

    Петля (`127.0.0.1`, `localhost`) остаётся разрешённой: встать в середину внутри
    одной машины невозможно, а это режим разработки и локального сервера программы.
    """
    if app_settings.is_secure_transport(base_url):
        return True
    _LOG.error(
        "[update] обновление ОТКЛОНЕНО: адрес %r не защищён (нужен https либо петля). "
        "Проверка хеша тут не помогает — он приезжает тем же каналом, что и файл.",
        base_url,
    )
    return False


def fetch_manifest(base_url: str, timeout: int = _TIMEOUT) -> dict:
    """Манифест обновлений или {} (нет сети/нет обновлений — это НЕ ошибка).

    ⚠️ `timeout` короче полного `_TIMEOUT` вызывается из ИНТЕРАКТИВНОЙ проверки при
    старте (`check_and_prompt`, ниже) — манифест это несколько байт JSON, а без него
    окно программы вообще не открывается, поэтому 30 секунд ожидания при плохой сети
    выглядели бы зависанием на старте.

    🔒 Заслонка небезопасного канала стоит ИМЕННО ЗДЕСЬ, а не в двух вызывающих:
    через манифест проходят ОБА пути (и интерактивный диалог, и фоновая закачка), и
    без манифеста ни один из них не начнёт качать файл. Одна проверка вместо двух,
    которые однажды разъедутся."""
    base = (base_url or "").rstrip("/")
    if not base:
        return {}
    if not _transport_ok(base):
        return {}
    try:
        return json.loads(_get(f"{base}/desktop/updates", timeout=timeout).decode("utf-8"))
    except Exception as e:                                   # noqa: BLE001
        _LOG.info(f"[update] манифест недоступен: {e}")
        return {}


def _download(url: str, dest: str, expect_sha: str) -> bool:
    """Качает во ВРЕМЕННЫЙ файл рядом и переименовывает только после проверки хеша.

    Скачивание прямо в целевой файл оставило бы после обрыва связи полуфайл, который
    выглядит как готовое обновление."""
    tmp = dest + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GradeBookAI-updater/1.0"})
        #SAST B310: см. `_transport_ok()` — качаем только по проверенной схеме.
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r, open(tmp, "wb") as f:  # nosec B310
            total = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_EXE_BYTES:
                    raise ValueError("файл обновления неправдоподобно велик")
                f.write(chunk)
        got = DU.sha256_file(tmp)
        if expect_sha and got != expect_sha:
            _LOG.warning(f"[update] хеш не сошёлся: ждали {expect_sha[:12]}…, получили {got[:12]}…")
            return False
        os.replace(tmp, dest)
        return True
    except Exception as e:                                   # noqa: BLE001
        _LOG.warning(f"[update] не удалось скачать {url}: {e}")
        return False
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _apply_patch(old_exe: str, patch_file: str, out_exe: str) -> bool:
    """Накладывает дельту на текущий .exe (zstd, старый файл как словарь).

    zstandard импортируется ЛЕНИВО и только здесь: без патча (полная закачка) он не
    нужен вовсе, и сборка без него остаётся рабочей — просто обновления пойдут целиком.
    """
    try:
        import zstandard
    except ImportError:
        _LOG.info("[update] zstandard недоступен — дельту не применить, качаем целиком")
        return False
    try:
        with open(old_exe, "rb") as f:
            base = f.read()
        with open(patch_file, "rb") as f:
            patch = f.read()
        dctx = zstandard.ZstdDecompressor(dict_data=zstandard.ZstdCompressionDict(base))
        data = dctx.decompress(patch, max_output_size=_MAX_EXE_BYTES)
        with open(out_exe, "wb") as f:
            f.write(data)
        return True
    except Exception as e:                                   # noqa: BLE001
        _LOG.warning(f"[update] патч не наложился: {e}")
        return False


def check_and_fetch(base_url: str, current_version: str) -> dict:
    """Проверить, скачать и подготовить обновление. Возвращает {} либо {version, path}.

    Ничего не подменяет — только кладёт готовый .exe рядом и пишет метку. Установка —
    `apply_pending()` на следующем запуске.
    """
    man = fetch_manifest(base_url)
    if not DU.has_update(man, current_version):
        return {}
    latest = DU.normalize(man.get("version") or "")
    base = (base_url or "").rstrip("/")
    new_exe = _new_exe_path()
    #Уже скачано это же обновление — второй раз не тянем (частый случай: человек
    #закрыл программу до перезапуска).
    done = read_pending()
    if done.get("version") == latest and os.path.isfile(new_exe):
        return done

    patch = DU.pick_patch(man, current_version)
    ok = False
    #Откуда в итоге взялся файл — от этого зависит, ЧЬЮ подпись проверять ниже.
    #Одного флага `ok` мало: он остаётся True и после отката на полную закачку.
    ok_from_patch = False
    if patch:
        tmp_patch = os.path.join(tempfile.gettempdir(), patch["file"])
        if _download(f"{base}/downloads/{DU.UPDATES_DIR_NAME}/{patch['file']}",
                     tmp_patch, patch.get("sha256", "")):
            ok = _apply_patch(_exe_path(), tmp_patch, new_exe)
            if ok and DU.sha256_file(new_exe) != patch.get("target_sha256"):
                #Патч лёг «успешно», но результат не тот — чаще всего это значит, что
                #обновляемся не с той сборки, для которой патч собран.
                _LOG.warning("[update] результат патча не совпал с ожидаемым — качаю целиком")
                os.remove(new_exe)
                ok = False
            ok_from_patch = ok
            try:
                os.remove(tmp_patch)
            except OSError:
                pass

    if not ok:
        full = man.get("full") or {}
        if not full.get("file"):
            return {}
        ok = _download(f"{base}/downloads/{DU.UPDATES_DIR_NAME}/{full['file']}",
                       new_exe, full.get("sha256", ""))
    if not ok:
        return {}

    #🔒 ПОДПИСЬ ВЫПУСКА. Хеш к этому моменту уже сошёлся, но он ехал ТЕМ ЖЕ каналом,
    #что и файл: подменивший .exe перепишет и хеш в манифесте. Подпись сделана нашим
    #закрытым ключом, которого у подменившего нет.
    #⚠️ Берём её из ТОЙ ЖЕ записи манифеста, по которой качали: у полной закачки и у
    #патча подписи разные, а перепутать их — значит проверить не то.
    got_sha = DU.sha256_file(new_exe)
    sig = ((patch or {}).get("sig") if patch and ok_from_patch
           else (man.get("full") or {}).get("sig")) or ""
    if not DU.release_signature_ok(latest, got_sha, sig):
        #Отказ ГРОМКИЙ и файл убираем: оставленный на диске неподписанный .exe дождался
        #бы следующего запуска и был бы поставлен уже без проверки.
        _LOG.warning("[update] подпись выпуска не сошлась — обновление ОТКЛОНЕНО")
        try:
            os.remove(new_exe)
        except OSError:
            pass
        return {}

    info = {"version": latest, "path": new_exe, "sha256": got_sha, "sig": sig}
    _write_pending(info)
    _LOG.info(f"[update] обновление {latest} готово, установится при следующем запуске")
    return info


def read_pending() -> dict:
    try:
        with open(_pending_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:                                        # noqa: BLE001
        return {}


def _write_pending(info: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_pending_path()), exist_ok=True)
        with open(_pending_path(), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
    except OSError as e:
        _LOG.warning(f"[update] не удалось записать метку обновления: {e}")


def clear_pending() -> None:
    for p in (_pending_path(), _new_exe_path()):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def cleanup_old() -> None:
    """Убирает `.old.exe` от прошлой установки. Занят — молча оставляем до следующего раза."""
    old = _old_exe_path()
    if os.path.isfile(old):
        try:
            os.remove(old)
        except OSError:
            pass


def apply_pending(current_version: str) -> bool:
    """Установить скачанное обновление. True — файл подменён, нужен перезапуск.

    Зовётся В САМОМ НАЧАЛЕ запуска, до открытия окна: подменять .exe под работающей
    программой нельзя, а на старте процесс ещё не начал ничего показывать человеку.
    """
    cleanup_old()
    info = read_pending()
    new_exe = _new_exe_path()
    if not info or not os.path.isfile(new_exe):
        return False
    #Уже стоит эта версия (или новее) — метка протухла, чистим.
    if not DU.is_newer(info.get("version") or "", current_version):
        clear_pending()
        return False
    #Файл мог быть повреждён после скачивания (диск, антивирус) — проверяем ещё раз
    #ПЕРЕД подменой: это последняя точка, где отказ ничего не стоит.
    if info.get("sha256") and DU.sha256_file(new_exe) != info["sha256"]:
        _LOG.warning("[update] скачанный файл повреждён — обновление отменено")
        clear_pending()
        return False
    #🔒 Подпись проверяется ВТОРОЙ раз, ровно как хеш, и по той же причине: между
    #скачиванием и запуском проходит время, а файл всё это время лежит на диске рядом
    #с программой. Первая проверка защищает канал, эта — уже лежащий файл.
    #⚠️ Проверка идёт ЗДЕСЬ, а не только при скачивании: метку `pending` мог оставить
    #и прежний выпуск программы, который подписи ещё не знал.
    if not DU.release_signature_ok(info.get("version") or "", info.get("sha256") or "",
                                   info.get("sig") or ""):
        _LOG.warning("[update] подпись выпуска не сошлась — установка отменена")
        clear_pending()
        return False

    cur, old = _exe_path(), _old_exe_path()
    try:
        if os.path.isfile(cur):
            os.replace(cur, old)          #переименовать запущенный .exe Windows разрешает
        os.replace(new_exe, cur)
    except OSError as e:
        _LOG.warning(f"[update] не удалось подменить файл программы: {e}")
        #Откатываемся, чтобы не остаться вообще без .exe.
        try:
            if not os.path.isfile(cur) and os.path.isfile(old):
                os.replace(old, cur)
        except OSError:
            pass
        return False
    try:
        os.remove(_pending_path())
    except OSError:
        pass
    _LOG.info(f"[update] установлена версия {info.get('version')}")
    return True


#── Интерактивная проверка при старте (диалог да/нет) ────────────────────────────────
#⚠️ Раньше проверка была ТИХОЙ фоновой (start_background_check — качала про запас,
#подмена ставилась молча на СЛЕДУЮЩЕМ запуске). Человек видел, что программа вдруг
#закрывается, без единого объяснения (--windows-console-mode=disable — консоли нет,
#печать в неё никто не увидит) — это и читалось как «автообновление не работает»
#наравне с самим багом is_frozen()/Nuitka (см. CLAUDE.md). Теперь: проверяем СИНХРОННО
#до открытия окна, спрашиваем явным диалогом, качаем только после согласия и сразу же
#перезапускаемся — без «запустите программу ещё раз».

def ask_yes_no(title: str, message: str) -> bool:
    """Нативный Windows-диалог да/нет. Работает БЕЗ готового окна/WebView2/Qt — на
    этом этапе запуска их ещё нет, а тянуть Qt ради одного диалога означало бы то же
    самое ~150 МБ, от которых отказались переходом на WebView2 (см. §11 CLAUDE.md).

    На не-Windows (только dev-запуск тестов на другой ОС, релиз — Windows-only, DPAPI/
    WebView2/SQLCipher) — молча отвечает «нет», чтобы автотесты не поймали реальный
    диалог.

    ⚠️ Здесь стояло «тесты самой функции подменяют ctypes.windll, а не полагаются на
    эту ветку» — и это было НЕПРАВДОЙ: заслонка ниже срабатывает ПЕРВОЙ, до всякого
    windll. Из-за неё на Linux тест «ответ НЕТ» был зелёным, ни разу не дойдя до
    диалога, а тест «ответ ДА» падал — и его падение списывали на среду. Тесты теперь
    подменяют И платформу; см. tests/test_desktop_update.py."""
    if sys.platform != "win32":
        _LOG.info(f"[update] {title}: {message} (не Windows — диалог недоступен)")
        return False
    MB_YESNO, MB_ICONQUESTION, MB_TOPMOST = 0x4, 0x20, 0x40000
    IDYES = 6
    res = ctypes.windll.user32.MessageBoxW(0, message, title,
                                           MB_YESNO | MB_ICONQUESTION | MB_TOPMOST)
    return res == IDYES


def show_info(title: str, message: str) -> None:
    """Информационное окно (например, «не удалось скачать»). Best-effort — сбой показа
    не должен ронять программу, сообщение в любом случае уходит и в лог."""
    _LOG.info(f"[update] {title}: {message}")
    if sys.platform != "win32":
        return
    try:
        MB_ICONINFORMATION, MB_TOPMOST = 0x40, 0x40000
        ctypes.windll.user32.MessageBoxW(0, message, title, MB_ICONINFORMATION | MB_TOPMOST)
    except Exception:                                          # noqa: BLE001
        pass


def relaunch() -> None:
    """Запускает СВЕЖУЮ копию программы (только что обновлённый .exe) отдельным
    процессом. Не завершает текущий процесс сама — это решает вызывающий код (main.py),
    симметрично остальным функциям модуля.

    Только для собранной версии: в dev (`python main.py`) `_exe_path()` не указывает
    ни на что реально исполняемое, перезапускать нечего."""
    if not app_paths.is_frozen():
        return
    try:
        import subprocess
        subprocess.Popen([_exe_path()], cwd=app_paths.app_dir(),
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    except Exception as e:                                     # noqa: BLE001
        _LOG.warning(f"[update] не удалось перезапустить программу: {e}")


def check_and_prompt(base_url: str, current_version: str) -> bool:
    """Весь интерактивный цикл автообновления ОДНИМ вызовом: манифест → диалог →
    закачка → установка → перезапуск. Вынесено сюда (а не в main.py), чтобы
    тестировать без реального диалога/сети/подмены файлов — ровно так же, как
    остальные функции модуля.

    Возвращает True, если программа должна завершиться ПРЯМО СЕЙЧАС: либо человек
    согласился и уже запущена новая версия отдельным процессом, либо отказался — по
    прямому решению тогда программа просто закрывается, а не остаётся работать на
    старой версии молча."""
    man = fetch_manifest(base_url, timeout=6)
    if not DU.has_update(man, current_version):
        return False
    latest = DU.normalize(man.get("version") or "") or "новая версия"
    if not ask_yes_no("Обновление GradeBookAI",
                      f"Доступна версия {latest}.\n\nУстановить сейчас? Программа "
                      "скачает обновление и перезапустится."):
        return True
    info = check_and_fetch(base_url, current_version)
    if not info or not apply_pending(current_version):
        show_info("Обновление GradeBookAI",
                  "Не удалось установить обновление. Программа откроется как обычно.")
        return False
    relaunch()
    return True
