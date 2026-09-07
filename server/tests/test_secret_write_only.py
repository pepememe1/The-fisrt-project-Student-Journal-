"""
test_secret_write_only.py — СЕКРЕТ, КОТОРЫЙ НЕЛЬЗЯ ПРОЧИТАТЬ ОБРАТНО.

Пункт P1 из плана Ярослава (06.09.2026): «GigaChat credential возвращается администратору
через /web/admin/ai-config… Секрет должен быть write-only: „настроен / не настроен", но не
доступен для повторного чтения».

Ключ к платному внешнему сервису уходил наружу целиком: его уносил любой, кто получил
административную сессию — или просто снял ответ с экрана. Чтение секрета не нужно ни для
чего: заменить ключ можно записью, проверить — отдельной ручкой.

⚠️ ОБРАТНЫЙ ХОД проверен: вернуть в ответ `"gigachat_credentials": cfg.get(...)` — краснеет
`test_the_key_never_comes_back_out`.
"""
from app.db import SessionLocal
from app.models import ConfigKV
from conftest import make_admin


def _set_key(value: str):
    db = SessionLocal()
    try:
        row = db.get(ConfigKV, "config")
        cur = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
        cur["gigachat_credentials"] = value
        if row is None:
            db.add(ConfigKV(key="config", value=cur,
                            updated_at="2026-09-06T00:00:00+00:00", deleted=False))
        else:
            row.value = cur
        db.commit()
    finally:
        db.close()


def _get_key() -> str:
    db = SessionLocal()
    try:
        row = db.get(ConfigKV, "config")
        return (dict(row.value).get("gigachat_credentials") or "") if row is not None else ""
    finally:
        db.close()


def test_the_key_never_comes_back_out(client):
    """🔒 Значение ключа не появляется в ответе НИ В КАКОМ виде.

    Проверяем не отсутствие конкретного поля, а отсутствие САМОГО СЕКРЕТА в теле ответа:
    поле можно переименовать, а секрет — утечь под другим именем.
    """
    admin = make_admin(client)
    secret = "СЕКРЕТНЫЙ-КЛЮЧ-GIGACHAT-12345"
    _set_key(secret)
    r = client.get("/web/admin/ai-config", headers=admin)
    assert r.status_code == 200, r.text
    assert secret not in r.text, "ключ GigaChat ушёл наружу"
    assert "gigachat_credentials" not in r.json(), "поле со значением ключа осталось в ответе"


def test_the_admin_still_sees_WHETHER_the_key_is_set(client):
    """Признак «настроен» обязателен.

    Без него пустое поле читается как «ключ стёрли», и администратор впишет новый поверх
    работающего — то есть защита секрета сломала бы настройку.
    """
    admin = make_admin(client)
    _set_key("")
    assert client.get("/web/admin/ai-config", headers=admin).json()["gigachat_configured"] is False
    _set_key("что-то")
    assert client.get("/web/admin/ai-config", headers=admin).json()["gigachat_configured"] is True


def test_saving_without_the_field_keeps_the_existing_key(client):
    """🔥 Пустое поле означает «НЕ МЕНЯТЬ», а не «стереть».

    Сервер ключ не отдаёт, значит поле у администратора всегда пустое. Если бы сохранение
    записывало пустоту, простое нажатие «Сохранить» на странице настроек уносило бы рабочий
    ключ, ничего не трогая, — и заметили бы это только когда Вектор перестал отвечать.
    """
    admin = make_admin(client)
    _set_key("рабочий-ключ")
    r = client.post("/web/admin/ai-config",
                    json={"vector_llm": "gigachat"}, headers=admin)
    assert r.status_code == 200, r.text
    assert _get_key() == "рабочий-ключ", "ключ стёрли сохранением без поля"


def test_the_key_can_still_be_replaced(client):
    """Закрыто ровно ЧТЕНИЕ. Замена обязана работать — иначе ключ нельзя было бы сменить."""
    admin = make_admin(client)
    _set_key("старый")
    r = client.post("/web/admin/ai-config",
                    json={"gigachat_credentials": "новый"}, headers=admin)
    assert r.status_code == 200, r.text
    assert _get_key() == "новый"
