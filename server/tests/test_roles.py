"""
test_roles.py — сторож списка ролей (11.09.2026).

━━ ЗАЧЕМ ━━
`User.role` — обычная строка, и база её НЕ ОГРАНИЧИВАЕТ. Значит человек, заведённый с
ролью «moderaor» (опечатка в одну букву), войдёт — и не увидит ничего: ни одна проверка
роли его не узнает, ни один раздел ему не откроется, и ни одного внятного отказа он не
получит. Отказ тихий и полный, а причина видна только тому, кто пойдёт смотреть строку
в базе.

⚠️ ПОЧЕМУ НЕ `Enum` В КОЛОНКЕ. Роли приезжают СИНКОМ с десктопов, в том числе со старых
сборок: строгий тип в базе означал бы, что первый же пришедший неизвестный вариант
роняет push целиком, а вместе с ним — всю пачку изменений. Мягкий список плюс сторож
ловит опечатку у нас, не ломая совместимость на бою.

⚠️ Обратный ход проверен: убрать "moderator" из `KNOWN_ROLES` — краснеет первый тест;
открыть `require_admin` для модератора — краснеет третий.
"""
import re
from pathlib import Path

from app import deps


APP = Path(__file__).resolve().parents[1] / "app"


def test_known_roles_covers_every_role_the_product_uses():
    """Каждая роль, встречающаяся в коде, обязана быть в списке известных.

    Ищем не «все строки подряд», а конкретную форму сравнения `role == "..."` и
    `role in (...)`: она и есть место, где роль что-то решает.
    """
    #🔥 СЛОВО `role` В ПРОДУКТЕ ОЗНАЧАЕТ ДВЕ РАЗНЫЕ ВЕЩИ, и первый же прогон этого
    #сторожа на них и споткнулся. `user.role` — роль ЧЕЛОВЕКА (admin/teacher/student…),
    #а `participant.role` — роль УЧАСТНИКА БЕСЕДЫ (owner/admin/writer/member/reader,
    #см. ConversationParticipant). Наборы пересекаются по слову «admin» и расходятся во
    #всём остальном; проверять их одним списком значит требовать, чтобы «owner» был
    #ролью пользователя, — то есть ловить несуществующую ошибку и приучать смотреть мимо.
    #Поэтому сверяем ТОЛЬКО обращения к человеку, по имени переменной.
    WHO = r"(?:user|u|admin|target|peer|other|actor|me|owner_user|reported|reporter)"
    used = set()
    pattern = re.compile(WHO + r'\.role\s*(?:==|!=)\s*"([a-z_]+)"')
    in_tuple = re.compile(WHO + r'\.role\s+(?:not\s+)?in\s*\(([^)]*)\)')
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        used.update(pattern.findall(text))
        for group in in_tuple.findall(text):
            used.update(re.findall(r'"([a-z_]+)"', group))
    assert used, "разбор не нашёл НИ ОДНОГО сравнения роли — проверка ослепла"
    unknown = used - set(deps.KNOWN_ROLES)
    assert not unknown, (
        "в коде сравнивают роль со значением, которого нет в deps.KNOWN_ROLES: %s. "
        "Либо это опечатка, либо новая роль — и тогда её надо объявить." % sorted(unknown))


def test_moderation_roles_are_a_subset_of_known_roles():
    """Список тех, кому открыта модерация, не имеет права содержать выдуманную роль."""
    assert set(deps.MODERATION_ROLES) <= set(deps.KNOWN_ROLES)
    assert "moderator" in deps.MODERATION_ROLES
    assert "admin" in deps.MODERATION_ROLES, "администратор остаётся модерацией"


def test_admin_door_stays_admin_only():
    """🔒 ГЛАВНОЕ: `require_admin` не знает про модератора.

    Это проверка ТЕКСТОМ, а не поведением, и намеренно: поведение закрыто в
    `test_moderation_role.py`, а здесь ловится попытка «просто добавить роль в список»,
    которая выглядит безобидной правкой одной строки и отдаёт модератору девяносто семь
    административных ручек разом.
    """
    src = (APP / "deps.py").read_text(encoding="utf-8")
    #⚠️ ТЕЛО ФУНКЦИИ РЕЖЕМ ПО ОТСТУПУ, а не «до следующего def».
    #Обе наивные версии этой проверки были неверны и обе краснели на исправном коде:
    #поиск следующего определения от начала строки находил саму функцию, а поправленный
    #вариант дотягивался до КОНСТАНТЫ `MODERATION_ROLES`, объявленной между двумя
    #дверями. Тело функции — это её заголовок и всё, что ниже с отступом; так и берём.
    lines = src[src.index("def require_admin("):].split("\n")
    body_lines = [lines[0]]
    for line in lines[1:]:
        if line and not line.startswith((" ", "\t")):
            break
        body_lines.append(line)
    body = "\n".join(body_lines)
    assert 'user.role != "admin"' in body, (
        "условие администратора изменено — проверьте, не получил ли модератор "
        "административный доступ целиком")
    assert "MODERATION_ROLES" not in body, (
        "в дверь администратора попал список модерации: это отдаёт модератору "
        "управление журналом всего колледжа")
