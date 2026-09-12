"""
messenger — мессенджер: беседы, сообщения, каналы, модерация, веб-сокет.

Был одним файлом на 3543 строки. Разрезан в 3.7.7 тем же приёмом, что `routers/web`
в 3.6: снаружи ничего не изменилось (`from app.routers import messenger`, `router`,
`mod_router` — те же объекты), внутри правки перестали сталкиваться в одном файле.

⚠️ ПОРЯДОК ИМПОРТА НИЖЕ — ЭТО ПОРЯДОК РЕГИСТРАЦИИ МАРШРУТОВ. Все модули дописывают
маршруты в ОДИН объект `router`, а FastAPI отдаёт запрос ПЕРВОМУ подошедшему. Сегодня
перехвата нет (проверено `test_messenger_routes.py`: буквальные пути и пути с
параметром либо не пересекаются, либо разведены методом), но менять порядок «просто
так» нельзя — именно на этом обожглись активности, где `GET /{activity_id}` начал
отвечать «не найдено» на `/quizzes`.
"""
from . import users        # noqa: F401  — /users, /status, /templates
from . import safety       # noqa: F401  — блокировка, жалоба на профиль, своё ограничение
from . import chats        # noqa: F401  — /chats/*, участники, роли
from . import messages     # noqa: F401  — сообщения, поиск, реакции, пересылка
from . import reminders    # noqa: F401  — напоминания о сообщении
from . import extras       # noqa: F401  — перевод и GIF
from . import attachments  # noqa: F401  — вложения, вкладки «Медиа»/«Файлы»/«Избранное»
from . import channels     # noqa: F401  — системные каналы и отчёты куратора
from . import moderation   # noqa: F401  — mod_router
from . import ws           # noqa: F401  — веб-сокет
from ._common import *     # noqa: F401,F403 — router, mod_router, ws_manager и хелперы
