"""
desktop_update.py — АВТООБНОВЛЕНИЕ ДЕСКТОПА: общий модуль для клиента и сервера.

Лежит в КОРНЕ репозитория рядом с `grading.py`/`vector_nlu.py`/`study_hours.py` и по той
же причине: правила «какая версия новее», «как называется патч» и «чем проверяется
целостность» обязаны совпадать у того, кто патч СОБИРАЕТ (сервер/деплой-скрипт) и у того,
кто его ПРИМЕНЯЕТ (программа на компьютере преподавателя). Разъедутся — человек скачает
файл, который не подойдёт, и узнает об этом в момент запуска.

Модуль ЧИСТЫЙ (только stdlib): его тянет и десктоп, и FastAPI-сервер. Здесь НЕТ сети и
НЕТ файловых операций над установленной программой — только разбор версий, имена, хеши и
формат манифеста. Скачивание и замена .exe — в `data/updater.py` (клиент), сборка
патчей — в `tools/make_desktop_patch.py`.

── Формат манифеста (`downloads/updates/manifest.json` на сервере) ──────────────────
    {
      "version": "3.5.6",                       # последняя выпущенная версия
      "full": {"file": "GradeBookAI-3.5.6.exe", "sha256": "…", "size": 50395136},
      "patches": [
        {"from": "3.5.5", "to": "3.5.6", "file": "3.5.5-3.5.6.patch",
         "sha256": "…", "size": 2417152, "target_sha256": "…"}
      ]
    }
`target_sha256` — хеш .exe, который ДОЛЖЕН получиться после наложения патча. Без него
битый или подменённый патч выяснился бы только при запуске нерабочей программы.
"""
import hashlib
import re

#🔑 ЕДИНСТВЕННЫЙ ИСТОЧНИК СТРОКИ ВЕРСИИ ПРОДУКТА.
#Раньше она жила в ТРЁХ местах, и два из них отставали одновременно: `data/core.py`
#показывал 3.6.9 на ветке 3.7, а `routers/web/siteinfo.py` — 3.6.1, отстав на шесть
#релизов, причём прямо над ним стоял комментарий «правь руками при каждом релизе».
#Комментарий как средство синхронизации не сработал ни разу — отсюда общая константа.
#Дом ей именно здесь: модуль корневой, на одной stdlib, уже импортируется и десктопом,
#и сервером, и занимается ровно сравнением версий. `data/core.py` её РЕ-ЭКСПОРТИРУЕТ,
#поэтому ни один существующий импорт не сломался (тот же приём, что с `data/defaults.py`).
#⚠️ Держать синхронной с веткой релиза: по ней автообновление решает, ставить ли сборку.
APP_VERSION = "Release 3.9.3"

# Имя каталога с обновлениями внутри `downloads/` (его сервер и так раздаёт наружу).
UPDATES_DIR_NAME = "updates"
MANIFEST_NAME = "manifest.json"
# Читаем файл кусками: .exe весит ~48 МБ, а на боевом VPS 960 МБ памяти — целиком в RAM
# его тянуть незачем ни при выдаче, ни при проверке на слабой машине пользователя.
_CHUNK = 1 << 20

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)*)")


def parse_version(text: str) -> tuple:
    """«Release 3.5.6» / «3.5.6» / «v3.5.6-test» → (3, 5, 6). Мусор → ().

    Строка версии в продукте живёт в человекочитаемом виде — `APP_VERSION` НЕСКОЛЬКИМИ
    строками выше в ЭТОМ же файле («Release 3.7.3»); `data/core.py` её только
    ре-экспортирует. А сравнивать надо машинно: при обычном сравнении строк «3.5.10»
    оказывается СТАРШЕ «3.5.9», и обновление до неё не предложилось бы никогда.
    """
    m = _VERSION_RE.search(text or "")
    if not m:
        return ()
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    """Строго новее ли `candidate` относительно `current` (обе — любые формы записи).

    Неразобранная версия НИКОГДА не считается новее: лучше не обновиться и остаться на
    рабочей сборке, чем поставить неизвестно что по неразборчивому манифесту.
    """
    c, cur = parse_version(candidate), parse_version(current)
    if not c or not cur:
        return False
    #Дополняем нулями, чтобы «3.6» и «3.6.0» считались равными, а не разными.
    n = max(len(c), len(cur))
    return c + (0,) * (n - len(c)) > cur + (0,) * (n - len(cur))


def patch_name(from_version: str, to_version: str) -> str:
    """Каноничное имя файла патча. ОДНА точка правды для сборщика и клиента."""
    return f"{normalize(from_version)}-{normalize(to_version)}.patch"


def normalize(text: str) -> str:
    """«Release 3.5.6» → «3.5.6» (для имён файлов и сравнения в манифесте)."""
    v = parse_version(text)
    return ".".join(str(p) for p in v) if v else ""


def sha256_file(path) -> str:
    """SHA-256 файла в нижнем регистре (тот же вид, что кладёт сборщик патчей)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pick_patch(manifest: dict, current_version: str) -> dict | None:
    """Патч ИМЕННО с текущей версии на последнюю, либо None.

    Цепочки патчей (3.5.4→3.5.5→3.5.6) сознательно НЕ поддерживаем: каждый лишний шаг —
    ещё одна точка отказа на чужом компьютере, а выигрыш по трафику против одной полной
    закачки исчезает уже на втором звене. Нет прямого патча — клиент качает .exe целиком
    (см. `data/updater.py`), это всегда рабочий путь.
    """
    if not isinstance(manifest, dict):
        return None
    cur = normalize(current_version)
    latest = normalize(manifest.get("version") or "")
    if not cur or not latest or not is_newer(latest, cur):
        return None
    for p in manifest.get("patches") or []:
        if normalize(p.get("from") or "") == cur and normalize(p.get("to") or "") == latest:
            #Патч без контрольных сумм не берём: применить его — значит записать на диск
            #байты, которые нечем проверить.
            if p.get("sha256") and p.get("target_sha256") and p.get("file"):
                return p
    return None


def has_update(manifest: dict, current_version: str) -> bool:
    """Есть ли вообще что ставить (патчем или целиком)."""
    if not isinstance(manifest, dict):
        return False
    return is_newer(normalize(manifest.get("version") or ""), current_version)
