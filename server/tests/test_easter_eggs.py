"""
test_easter_eggs.py — БРОСОК пасхалок: шанс, режим проверяющего, откуда берётся список.

Заведён 06.09.2026 вместе с просьбой Влада «сделай для аккаунта budrin шанс на ачивки
очень высоким, для проверки в новом дизайне». Проверяется НЕ то, что «функция есть», а
две вещи, на которых легко ошибиться и не заметить:
  • список проверяющих живёт в ОКРУЖЕНИИ, а не в исходнике (иначе логин уезжает в git,
    в .exe и в APK, и снимается только новым релизом);
  • высокий шанс — 90 %, а не гарантия: при 100 % пропадает сам факт «выпало», и
    проверить работу броска становится нечем.
"""
# ── Проверяющие: высокий шанс пасхалок для названных логинов (06.09.2026) ───────────
def test_lucky_logins_come_from_the_environment_not_from_the_code(monkeypatch):
    """🔒 Логин проверяющего НЕ вписан в исходник.

    Вписанный уезжает в git, в сборку .exe и в APK — то есть в руки любому, кто их
    откроет, — и убрать его можно было бы только новым релизом. Переменную снимают на бою
    одной строкой и рестартом.
    """
    from app import easter_eggs as E
    monkeypatch.delenv("GRADEBOOK_LUCKY_LOGINS", raising=False)
    assert E.is_lucky("budrin") is False, "список проверяющих зашит в код"
    monkeypatch.setenv("GRADEBOOK_LUCKY_LOGINS", "budrin, Vlad")
    assert E.is_lucky("budrin") and E.is_lucky("VLAD"), "регистр не должен решать"
    assert not E.is_lucky("ivanov"), "посторонний попал в проверяющие"


def test_lucky_raises_the_chance_but_not_to_certainty():
    """Доля 90 %, а не 100: при полной гарантии пропадает сам факт «выпало».

    Проверять, что бросок вообще работает, стало бы нечем — а это и есть предмет проверки.
    """
    from app import easter_eggs as E
    hits = sum(1 for _ in range(2000) if E._hit("doom_avatar", lucky=True))
    assert 1600 < hits < 1990, hits
    #Обычному человеку шанс не трогаем ВООБЩЕ.
    normal = sum(1 for _ in range(2000) if E._hit("doom_avatar", lucky=False))
    assert normal < hits / 2, (normal, hits)
