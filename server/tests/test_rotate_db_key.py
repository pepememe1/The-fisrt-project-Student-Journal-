"""
Сторож инструмента ротации ключа базы (`server/rotate_db_key.py`).

🔥 У самого весомого пункта технического минимума НЕ БЫЛО НИ ОДНОГО ТЕСТА, и это
выяснилось только 05.09.2026, когда его впервые запустили вживую. Механизм написан ещё в
3.7.6, в CLAUDE.md он числился существующим — и там же честно стояло «ни разу не гонялся
на бою». Первый же живой запуск нашёл ДВА отказа, каждый сработал бы в день компрометации
ключа, то есть в худший из возможных моментов:

  • **флага `--check` не существовало.** Его называют режимом докстринг самого файла,
    CLAUDE.md и план безопасности; человек набирает документированную команду и получает
    `unrecognized arguments: --check`. Поведение было верным, неверным было ОБЕЩАНИЕ —
    а обещание, по которому действуют, ничем не лучше неверного кода;

  • **инструмент не находил боевую базу.** Он брал путь только из `GRADEBOOK_DB_URL` в
    `.env`, а в боевом `.env` этой переменной НЕТ вовсе (проверено на машине): сервер
    работает на умолчании из `app/config.py`. Падало это сообщением «Ротация поддержана
    только для SQLite/SQLCipher, а URL = ''» — то есть указывало не на ту причину.

⚠️ Крипту здесь замокать нельзя, и не нужно: сам механизм проверяется НА БОЮ командой
`python rotate_db_key.py --check` (проверено 05.09.2026 на настоящем ключе — снимок
открылся, 29 пользователей, перешифровался, старый ключ перестал открывать). Эти тесты
стерегут ОБВЯЗКУ: разбор `.env`, поиск файла базы, разбор аргументов и то, что ключи не
утекают в вывод. На Windows-разработке драйвера `sqlcipher3` нет by design, поэтому
модуль обязан импортироваться без него — это тоже проверяется.
"""
import io
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

import rotate_db_key as R  # noqa: E402


def test_module_imports_without_the_sqlcipher_driver():
    """Модуль обязан грузиться там, где драйвера нет.

    Иначе тесты обвязки были бы невозможны нигде, кроме боевой машины, — то есть их бы
    просто не было, что и произошло."""
    assert hasattr(R, "cmd_check") and hasattr(R, "cmd_apply")


# ── Разбор .env ─────────────────────────────────────────────────────────────────────
def _env_file(tmp_path, text):
    p = tmp_path / ".env"
    io.open(str(p), "w", encoding="utf-8").write(text)
    return str(p)


def test_read_env_strips_quotes_comments_and_cr(tmp_path):
    """Боевой `.env` писали разные люди и разные скрипты: кавычки, CRLF и комментарии
    там встречаются вперемешку. Ключ, прочитанный вместе с кавычкой, не откроет базу — а
    выглядеть это будет как испорченный файл."""
    path = _env_file(tmp_path, '# коммент\nA="один"\r\nB=\'два\'\nC=три\n\nD\n')
    env = R._read_env(path)
    assert env["A"] == "один"
    assert env["B"] == "два"
    assert env["C"] == "три"
    assert "D" not in env, "строка без '=' значением не является"
    assert "#" not in "".join(env)


# ── Поиск файла базы ────────────────────────────────────────────────────────────────
def test_db_path_falls_back_to_the_server_default_when_env_is_silent(tmp_path):
    """🔑 Главное свойство. В боевом `.env` переменной нет, и инструмент обязан взять
    то же умолчание, на котором работает сам сервер."""
    path = _env_file(tmp_path, "GRADEBOOK_DB_KEY=" + "a" * 64 + "\n")
    got = R._db_path_from_env(path, "")
    assert os.path.basename(got) == "gradebook_server.db"
    assert os.path.dirname(got) == os.path.dirname(os.path.abspath(path)), (
        "путь обязан считаться от каталога .env, а не от текущего каталога вызывающего: "
        "иначе запуск из другого места молча возьмёт несуществующий файл")


def test_explicit_url_still_wins(tmp_path):
    """Заданный явно адрес важнее умолчания — иначе переезд базы стал бы невозможен."""
    path = _env_file(tmp_path, "x=1\n")
    assert R._db_path_from_env(path, "sqlite:////srv/other.db") == "/srv/other.db"


def test_non_sqlite_url_is_refused():
    """Ротация умеет только SQLCipher. Отказ обязан быть явным, а не «получится как
    получится»: чужая СУБД молча не перешифруется."""
    with pytest.raises(SystemExit):
        R._db_path_from_env("/nowhere/.env", "postgresql://user@host/db")


# ── Ключи ───────────────────────────────────────────────────────────────────────────
def test_only_64_hex_is_accepted_as_a_key():
    assert R._is_hex64("a" * 64)
    assert R._is_hex64("A" * 64)
    assert not R._is_hex64("a" * 63)
    assert not R._is_hex64("z" * 64)
    assert not R._is_hex64("")


def test_key_is_never_printed_in_full():
    """Вывод инструмента уходит в терминал администратора и в историю команд.

    Ключ от базы с ПДн студентов там оказаться не должен: `_pfx` обязан показывать
    ОПОЗНАВАТЕЛЬНЫЙ префикс, а не ключ."""
    key = "0123456789abcdef" * 4
    shown = R._pfx(key)
    assert key not in shown
    assert len(shown) < 12, shown
    assert shown.startswith("012345")
    assert R._pfx("") == "(пусто)"


# ── Аргументы ───────────────────────────────────────────────────────────────────────
def test_documented_check_flag_exists(tmp_path, capsys):
    """Обратный ход дефекта 05.09.2026, дословно: команда из документации обязана
    разбираться. Раньше здесь было `unrecognized arguments: --check`."""
    path = _env_file(tmp_path, "GRADEBOOK_DB_KEY=нет\n")
    argv = sys.argv
    sys.argv = ["rotate_db_key.py", "--check", "--env", path]
    try:
        with pytest.raises(SystemExit) as e:
            R.main()
    finally:
        sys.argv = argv
    #Дошли до проверки ключа — значит аргументы разобрались.
    assert "GRADEBOOK_DB_KEY" in str(e.value), e.value


def test_check_and_apply_together_are_refused(tmp_path):
    """«Проверить и сразу применить» — не режим, а недоразумение: первый ничего не
    меняет, второй меняет всё. Молча выбрать один из них значит однажды выбрать не тот."""
    path = _env_file(tmp_path, "GRADEBOOK_DB_KEY=" + "a" * 64 + "\n")
    argv = sys.argv
    sys.argv = ["rotate_db_key.py", "--check", "--apply", "--env", path]
    try:
        with pytest.raises(SystemExit) as e:
            R.main()
    finally:
        sys.argv = argv
    assert "взаимоисключающи" in str(e.value)


# ── Связь с продуктом ───────────────────────────────────────────────────────────────
def test_default_db_url_matches_the_server_default():
    """Умолчание продублировано литералом — значит обязано быть под сторожем.

    ⚠️ Две копии расходятся молча и всегда. Разойдись они здесь — инструмент ротации
    снова перестал бы находить боевую базу, и узнали бы мы об этом в день, когда ключ
    надо менять срочно."""
    import re
    src = io.open(os.path.join(SERVER, "app", "config.py"), encoding="utf-8").read()
    m = re.search(r'GRADEBOOK_DB_URL"\s*,\s*"([^"]+)"', src)
    assert m, "не нашли умолчание GRADEBOOK_DB_URL в app/config.py — разбор устарел"
    assert R._DEFAULT_DB_URL == m.group(1), (
        "умолчание адреса базы разошлось: в инструменте ротации %r, в сервере %r"
        % (R._DEFAULT_DB_URL, m.group(1)))


def test_apply_refuses_while_the_service_is_running():
    """Ротация по живой базе недопустима: её в этот момент пишет процесс.

    Проверяется СТРУКТУРНО — вызвать `cmd_apply` по-настоящему в тестах нельзя, а
    обещание в докстринге механизмом не является (наш записанный урок)."""
    import inspect
    src = inspect.getsource(R.cmd_apply)
    assert "_service_active" in src, "проверки остановленной службы нет"
    head = src.split("shutil.copy2")[0]
    assert "_service_active" in head, (
        "проверка службы обязана стоять ДО первых действий с файлами")


def test_apply_backs_up_both_the_database_and_the_env():
    """Ключ живёт в `.env`, данные — в базе. Копия одного без другого бесполезна:
    база без своего ключа неотличима от испорченной."""
    import inspect
    src = inspect.getsource(R.cmd_apply)
    assert src.count("shutil.copy2") >= 2
    assert "env_bak" in src and "db_bak" in src


def test_check_never_touches_the_live_file():
    """Проверка обязана работать на КОПИИ. Иначе «безопасный режим» перешифровал бы
    боевую базу одноразовым ключом — то есть уничтожил бы её."""
    import inspect
    src = inspect.getsource(R.cmd_check)
    assert "_rekey(snap" in src, "перешифровка должна идти по снимку"
    assert "_rekey(db" not in src
    assert "shutil.rmtree" in src, "временный каталог обязан убираться"
