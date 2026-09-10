"""
Сторож `app/hostcaps` — единственного места, отвечающего «что потянет эта машина».

━━ ПОЧЕМУ ЭТО НАДО СТЕРЕЧЬ ━━
От него зависит размер кеша боевой базы. Ошибка здесь ТИХАЯ в обе стороны: занизишь —
никто не заметит, кроме замедлившейся базы; завысишь на слабой машине — она уйдёт в
своп, и это будет выглядеть как «сервер стал тормозить», а не как правка настройки.

━━ ГЛАВНОЕ ТРЕБОВАНИЕ: ПЕРЕЕЗД НЕ ИМЕЕТ ПРАВА ПЕРЕДЕЛАТЬ ПРОВЕРЕННОЕ ━━
На нынешнем бою кеш — 4 МБ, и это правильное число, выведенное из размера базы и объёма
памяти. Заготовка под машину ВСГУТУ обязана оставить слабую машину В ТОЧНОСТИ такой, как
была: иначе «подготовка к переезду» молча меняет поведение прода, который никто не
переезжал.

⚠️ Все тесты подменяют железо, а не читают настоящее. Тест, зависящий от того, чья это
машина, у нас уже был дважды (`config.IS_PROD` и фикстура переводчика) — и оба раза
«зелено у одного, красно у другого» находилось не сразу.
"""
import pytest

from app import hostcaps

GB = 1024 ** 3


@pytest.fixture(autouse=True)
def _no_hardware_cache():
    """Сбрасываем кэш ответов о железе перед КАЖДЫМ тестом.

    ⚠️ Кэш глобален на процесс (иначе `nvidia-smi` запускался бы на каждое соединение с
    базой — см. `test_hardware_is_probed_once...`). Значит порядок тестов начал бы
    влиять на их результат: тест, подменяющий `subprocess`, получал бы ответ,
    посчитанный предыдущим. Такой тест зелен или красен в зависимости от того, что
    гоняли до него, — то есть проверяет не то, что написано.
    """
    hostcaps._gpu_cache = None
    hostcaps._cpu_cache = None
    hostcaps._tier_cache = None
    yield
    hostcaps._gpu_cache = None
    hostcaps._cpu_cache = None
    hostcaps._tier_cache = None


@pytest.fixture
def machine(monkeypatch):
    """Подменяет железо: memory/cpu_count/видеокарту и переменную окружения."""
    def _set(ram_gb, cpus, gpu_present=False, env=None):
        monkeypatch.setattr(hostcaps.hostinfo, "memory",
                            lambda: ({"total": int(ram_gb * GB),
                                      "available": int(ram_gb * GB * 0.7)}
                                     if ram_gb else {}))
        monkeypatch.setattr(hostcaps.hostinfo, "cpu_count", lambda: cpus)
        monkeypatch.setattr(hostcaps, "gpu",
                            lambda: {"present": gpu_present, "name": "test", "vram_mb": 0})
        if env is None:
            monkeypatch.delenv(hostcaps._ENV_TIER, raising=False)
        else:
            monkeypatch.setenv(hostcaps._ENV_TIER, env)
    return _set


# ─────────────────────────────────────────────────────────────────────────────────
# КЛАСС МАШИНЫ
# ─────────────────────────────────────────────────────────────────────────────────

def test_the_production_vps_is_recognised_as_weak(machine):
    """Нынешний бой: 960 МБ и одно ядро."""
    machine(0.94, 1)
    assert hostcaps.profile()["tier"] == "vps"
    assert not hostcaps.is_workstation()


@pytest.mark.parametrize("ram_gb,cpus", [(32, 8), (32, 16), (64, 16), (64, 32), (12, 8)])
def test_every_machine_in_the_target_range_is_recognised(machine, ram_gb, cpus):
    """Весь ЦЕЛЕВОЙ ДИАПАЗОН, а не одна машина.

    ⚠️ Требование Ярослава: «у заготовки не должно быть привязки к железу, мы пока
    оринтириуемся на Ryzen 7/9 / Xeon + 12/16 гб памяти + 32/64 DDR4/DDR5». Машина ещё
    не выбрана, поэтому проверяем КЛАСС: любая разумная конфигурация из этого диапазона
    обязана опознаться как мощная. Тест на одну конкретную сборку был бы снимком
    значения — тем самым, из-за которого у нас краснели сторожа на законных правках.
    """
    machine(ram_gb, cpus, gpu_present=True)
    prof = hostcaps.profile()
    assert prof["tier"] == "workstation"
    assert prof["gpu"]["present"] is True


def test_lots_of_memory_but_one_core_is_still_weak(machine):
    """Памяти много, ядро одно — считать такую машину мощной нельзя.

    На одном ядре счёт модели останавливает журнал всему колледжу: это не про объём
    памяти, а про то, что блокирующая работа некуда деться. Порог смотрит на ОБА
    признака именно поэтому.
    """
    machine(32, 1)
    assert hostcaps.profile()["tier"] == "vps"


def test_unknown_memory_falls_back_to_weak(machine):
    """Память не прочиталась (необычный контейнер) — считаем машину слабой.

    Ошибка в эту сторону означает «не включили лишнего», в обратную — «положили журнал».
    """
    machine(0, 8)
    prof = hostcaps.profile()
    assert prof["tier"] == "vps"
    assert "определить не удалось" in prof["why"]


@pytest.mark.parametrize("forced,expected", [("workstation", "workstation"),
                                             ("vps", "vps"),
                                             ("WORKSTATION", "workstation")])
def test_env_override_wins(machine, forced, expected):
    """Явное указание перебивает автоопределение — для машины, которую не предусмотрели."""
    machine(0.94, 1, env=forced)
    assert hostcaps.profile()["tier"] == expected


def test_garbage_in_env_does_not_break_detection(machine):
    """Мусор в переменной игнорируется, а не роняет сервер и не включает лишнее."""
    machine(32, 16, env="да-включи-всё")
    assert hostcaps.profile()["tier"] == "workstation"
    machine(0.94, 1, env="да-включи-всё")
    assert hostcaps.profile()["tier"] == "vps"


# ─────────────────────────────────────────────────────────────────────────────────
# РАЗМЕР КЕША БАЗЫ
# ─────────────────────────────────────────────────────────────────────────────────

def test_weak_machine_keeps_exactly_the_value_proven_in_production(machine):
    """🔒 ОБРАТНЫЙ ХОД ПО СМЫСЛУ: на слабой машине число обязано остаться прежним.

    4 МБ — не «поменьше на всякий случай», а выведенное значение: боевой файл базы около
    3 МБ, то есть кеш вмещает его целиком вместе с индексами, а лишнее ушло бы в своп.
    Если этот тест покраснел — значит подготовка к переезду задела прод, который никуда
    не переезжал.
    """
    machine(0.94, 1)
    assert hostcaps.sqlite_cache_kib() == 4_000


def test_workstation_gets_a_bigger_cache(machine):
    """На машине с 32 ГБ жаться под 960 МБ незачем."""
    machine(32, 16)
    assert hostcaps.sqlite_cache_kib() == 64_000


def test_cache_value_is_a_positive_integer_of_kilobytes(machine):
    """Значение уходит прямо в текст PRAGMA — дробь или строка там сломали бы запрос."""
    for ram, cpus in ((0.94, 1), (32, 16), (0, 4)):
        machine(ram, cpus)
        val = hostcaps.sqlite_cache_kib()
        assert isinstance(val, int) and val > 0


# ─────────────────────────────────────────────────────────────────────────────────
# ВИДЕОКАРТА
# ─────────────────────────────────────────────────────────────────────────────────

def test_missing_nvidia_smi_is_not_an_error(monkeypatch):
    """Нет `nvidia-smi` — «карта не найдена», а не падение.

    ⚠️ И это НЕ означает «карты нет»: на машине без драйверов она может стоять. Поэтому
    ответ этой функции влияет только на подсказку человеку, но никогда — на то,
    включится ли функция продукта.
    """
    monkeypatch.setattr(hostcaps.shutil, "which", lambda _name: None)
    assert hostcaps.gpu() == {"present": False, "name": "", "vram_mb": 0}


def test_broken_nvidia_smi_is_not_an_error(monkeypatch):
    """Утилита есть, но падает или отвечает мусором — тоже не повод ронять сервер."""
    monkeypatch.setattr(hostcaps.shutil, "which", lambda _name: "nvidia-smi")

    def _boom(*_a, **_kw):
        raise OSError("нет доступа")
    monkeypatch.setattr(hostcaps.subprocess, "run", _boom)
    assert hostcaps.gpu()["present"] is False


def test_gpu_is_parsed_from_real_output(monkeypatch):
    """Разбор ответа проверяем на ДОСЛОВНОЙ строке живой утилиты, а не на выдумке."""
    monkeypatch.setattr(hostcaps.shutil, "which", lambda _name: "nvidia-smi")

    class _Res:
        returncode = 0
        stdout = "NVIDIA GeForce RTX 4080, 16376\n"
    monkeypatch.setattr(hostcaps.subprocess, "run", lambda *a, **kw: _Res())
    card = hostcaps.gpu()
    assert card["present"] is True
    assert card["name"] == "NVIDIA GeForce RTX 4080"
    assert card["vram_mb"] == 16376


def test_hardware_is_probed_once_not_on_every_database_connection(monkeypatch):
    """🔥 ОПРОС ЖЕЛЕЗА НЕ ИМЕЕТ ПРАВА СТОЯТЬ НА ПУТИ ОТКРЫТИЯ СОЕДИНЕНИЯ.

    Дефект, пойманный самопроверкой 05.09.2026 ДО выкладки. `sqlite_cache_kib()`
    вызывается из `db._sqlite_pragmas`, то есть НА КАЖДОЕ НОВОЕ СОЕДИНЕНИЕ С БАЗОЙ.
    Через `profile()` он доходил до `gpu()`, а тот ЗАПУСКАЕТ ПОДПРОЦЕСС `nvidia-smi`
    с таймаутом 5 секунд.

    Цена на боевой машине: порождение процесса на каждое соединение — десятки
    миллисекунд в лучшем случае и пять секунд при зависшем драйвере, причём ровно
    тогда, когда пул поднимает соединение под живым запросом человека.

    ⚠️ Тесты класса машины были бы ЗЕЛЁНЫМИ рядом с этим дефектом: они проверяют,
    ЧТО отвечает `hostcaps`, а не СКОЛЬКО РАЗ он для этого лезет в систему.

    Обратный ход: убери кэш из `gpu()` (пусть каждый раз зовёт `_gpu_probe`) —
    тест краснеет на втором вызове.
    """
    from app import hostcaps
    calls = []

    def _fake_probe():
        calls.append(1)
        return {"present": True, "name": "NVIDIA Fake", "vram_mb": 8192}

    monkeypatch.setattr(hostcaps, "_gpu_cache", None)
    monkeypatch.setattr(hostcaps, "_gpu_probe", _fake_probe)

    #Столько раз, сколько соединений поднимет пул за рабочий день.
    for _ in range(20):
        hostcaps.sqlite_cache_kib()

    assert len(calls) == 1, (
        "железо опрашивается %d раз вместо одного — значит подпроцесс nvidia-smi "
        "запускается на каждое соединение с базой" % len(calls))


def test_cpu_model_is_also_probed_once(monkeypatch):
    """Та же проверка для модели процессора: она читает /proc на каждом обращении.

    Дешевле, чем подпроцесс, но `describe()` зовётся и в установщике, и в логах, а
    файловое чтение в цикле по соединениям — тот же класс ошибки."""
    from app import hostcaps
    calls = []
    monkeypatch.setattr(hostcaps, "_cpu_cache", None)
    monkeypatch.setattr(hostcaps, "_cpu_probe", lambda: calls.append(1) or "Fake CPU")

    for _ in range(10):
        hostcaps.cpu_model()

    assert len(calls) == 1, "модель процессора перечитывается на каждом обращении"


def test_nothing_touches_the_system_on_every_database_connection(monkeypatch):
    """🔥 НА ПУТИ ОТКРЫТИЯ СОЕДИНЕНИЯ НЕ ДОЛЖНО БЫТЬ НИ ОДНОГО СИСТЕМНОГО ВЫЗОВА.

    Второй заход по тому же дефекту (нашёл Полковник, 05.09.2026). Подпроцесс
    `nvidia-smi` я убрал, но `sqlite_cache_kib()` продолжал строить ПОЛНЫЙ профиль:
    чтение `/proc/meminfo`, `statvfs` по корню, сборка словаря — на КАЖДОЕ новое
    соединение с базой, ради выбора между двумя константами.

    ⚠️ Прежний сторож этого не видел: он считал вызовы только `_gpu_probe`. Проверка,
    считающая ОДИН источник расхода, молча пропускает все остальные — поэтому здесь
    считаются ВСЕ обращения к системе, а не одно.

    Обратный ход: убери кэш из `is_workstation()` — счётчик станет 20 вместо 1.
    """
    from app import hostcaps, hostinfo
    calls = []
    monkeypatch.setattr(hostcaps, "_gpu_cache", None)
    monkeypatch.setattr(hostcaps, "_cpu_cache", None)
    monkeypatch.setattr(hostcaps, "_tier_cache", None)
    monkeypatch.setattr(hostcaps, "_gpu_probe",
                        lambda: {"present": False, "name": "", "vram_mb": 0})
    monkeypatch.setattr(hostinfo, "memory",
                        lambda: calls.append("mem") or {"total": 16 * GB,
                                                        "available": 8 * GB})
    monkeypatch.setattr(hostinfo, "disk",
                        lambda p="/": calls.append("disk") or {"total": 1, "used": 0,
                                                               "free": 1})

    for _ in range(20):
        hostcaps.sqlite_cache_kib()

    assert len(calls) <= 2, (
        "на каждое соединение с базой уходит %d обращений к системе (%s) — профиль "
        "железа считается заново вместо кэша" % (len(calls), calls[:6]))


# ─────────────────────────────────────────────────────────────────────────────────
# ОТОБРАЖЕНИЕ ФАЙЛА БАЗЫ В ПАМЯТЬ (mmap_size)
# ─────────────────────────────────────────────────────────────────────────────────

def test_weak_machine_gets_no_mmap_at_all(machine):
    """🔒 Тот же смысл, что у кеша: слабая машина получает РОВНО прежнее поведение.

    Ноль здесь — не «выключили функцию», а значение SQLite по умолчанию: соединение на
    боевом VPS настраивается в точности как до правки. Это важнее выигрыша: замеренный
    выигрыш составил 1–3 мс из 26, а отображённый в память файл в сотни мегабайт на
    машине с 960 МБ вытеснил бы страницы Caddy и Python, то есть увёл бы базу в своп.
    """
    machine(0.94, 1)
    assert hostcaps.sqlite_mmap_bytes() == 0


def test_workstation_gets_a_mmap_window(machine):
    """На машине с 32 ГБ окно в 256 МБ незаметно, а замер показал устойчивый знак."""
    machine(32, 16)
    assert hostcaps.sqlite_mmap_bytes() == 256 * 1024 * 1024


def test_mmap_value_is_a_whole_number_of_bytes(machine):
    """Значение уходит прямо в текст PRAGMA — дробь или строка сломали бы запрос."""
    for ram, cpus in ((0.94, 1), (32, 16), (0, 4)):
        machine(ram, cpus)
        v = hostcaps.sqlite_mmap_bytes()
        assert isinstance(v, int) and not isinstance(v, bool) and v >= 0
