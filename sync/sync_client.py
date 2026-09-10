"""
sync_client.py — Клиент синхронизации десктопа с бэкендом GradeBookAI.

Offline-first: программа ВСЕГДА работает на локальном SQLite. Этот модуль нужен
только для обмена с сервером, когда сеть доступна:
  • login()      — получить JWT по логину/паролю (те же, что вводит пользователь).
  • pull(since)   — забрать изменения с сервера (дельта по метке времени).
  • push(changes) — отправить накопленные офлайн изменения.
  • health()      — быстро проверить, доступен ли сервер.

Адрес API берётся из конфигурации (в боевой сборке — зашит/прописан один раз).
Любая сетевая ошибка НЕ критична: синхронизация просто откладывается, программа
продолжает работать офлайн. Поэтому методы кидают исключения, а вызывающий код
(фоновый синкер) ловит их и повторяет позже.
"""
import base64
import json
import os
import re
import time

import threading

import requests

import log

_log = log.get("sync")

#Таймауты — КОРТЕЖ (connect, read). connect чуть щедрее (РФ-VPS за Cloudflare/TLS не
#всегда соединяется за пару секунд), read короче для быстрых вызовов. Единичный блип
#гасит retry-адаптер ниже, поэтому жёстких «5 c» больше нет.
DEFAULT_TIMEOUT = (8, 15)

#Таймаут синка — connect как у всех, но read ДЛИННЫЙ: первый полный pull/push на
#медленном канале бывает крупным (иначе «Read timed out» в логе).
SYNC_TIMEOUT = (8, 45)

#Быстрая проверка доступности (health): короткая, но не впритык.
HEALTH_TIMEOUT = (5, 5)


def is_token_expired(token: str, skew_sec: int = 30) -> bool:
    """True, если JWT просрочен (или не разобрался). Разбираем payload БЕЗ проверки
    подписи — это обычный base64url-JSON, а поле exp — абсолютная метка времени сервера.

    Зачем: не дёргать сеть заведомо мёртвым токеном и заранее (за skew_sec до exp)
    обновить его через refresh. Подпись здесь проверять не нужно — решение «идти в сеть
    или обновиться» не связано с доверием, а exp сервер всё равно перепроверит сам.
    Офлайн-время тоже учитывается: exp абсолютный, «заморозить» его нельзя."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)          #добить паддинг base64
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        return time.time() > (payload.get("exp", 0) - skew_sec)
    except Exception:
        return True


def _verify_setting():
    """Что передавать в requests как verify (проверку TLS-сертификата).

    По умолчанию True — сертификат проверяется (Caddy с публичным доменом даёт
    доверенный сертификат Let's Encrypt, ничего настраивать не нужно). Для
    ВНУТРЕННЕГО ЛВС с самоподписанным сертификатом укажите путь к доверенному CA в
    переменной GRADEBOOK_CA_BUNDLE — тогда https в ЛВС заработает без отключения
    проверки. Проверку TLS НИКОГДА не выключаем (verify=False открыл бы канал для
    подмены сервера)."""
    return os.environ.get("GRADEBOOK_CA_BUNDLE", "").strip() or True


def _prefer_ipv4(url: str) -> str:
    """localhost → 127.0.0.1 в адресе сервера.

    На чистом IPv4-окружении (типично для РФ) имя localhost резолвится сначала в
    IPv6 ::1; если сервер слушает только IPv4, запрос сперва виснет на таймауте
    ::1 и лишь потом падает на 127.0.0.1 — отсюда заметная задержка входа и
    синхронизации. Явный 127.0.0.1 убирает этот лишний круг."""
    return re.sub(r"^(https?://)localhost(?=[:/]|$)", r"\g<1>127.0.0.1", url)


class SyncClient:
    @staticmethod
    def normalize(base_url: str) -> str:
        """Адрес в том виде, в каком его хранит клиент.

        Отдельным методом, чтобы вызывающие могли СРАВНИТЬ свой адрес с `client.base_url`
        (сменился сервер — клиента надо пересоздать, см. `sync_runner._ensure_auth`), не
        повторяя у себя ни обрезку слэша, ни подмену localhost."""
        return _prefer_ipv4((base_url or "").rstrip("/"))

    #⚠️ Токены объявлены как `str | None`, а не `str`: пустое значение здесь бывает ДВУХ
    #видов и оба настоящие — «ещё не входили» и «вышли» (`logout` гасит токен в None,
    #чтобы переиспользованный на общем ПК клиент не слал отозванный). Прежняя запись
    #`token: str = None` была неявным Optional: аннотация обещала строку, а значением по
    #умолчанию стоял None — и любой читатель (и mypy) делал по ней неверный вывод.
    def __init__(self, base_url: str, token: str | None = None,
                 refresh_token: str | None = None):
        self.base_url = self.normalize(base_url)
        if not self.base_url:
            #Пустой адрес раньше давал ОТНОСИТЕЛЬНЫЙ запрос и невнятную ошибку глубоко
            #внутри requests. Отказ на месте создания понятнее на порядок.
            raise ValueError("SyncClient: не задан адрес сервера")
        self.token = token
        self.refresh_token = refresh_token or ""
        #verify (проверка TLS) и заголовки общие для всех запросов — держим в сессии.
        self._verify = _verify_setting()
        #🔥 СЕССИИ — ПО ПОТОКУ. `requests.Session` НЕ потокобезопасна: у неё общий пул
        #соединений, и одновременные запросы из разных потоков портят его состояние
        #(симптом — редкие необъяснимые обрывы, которые не воспроизводятся). А потоков у
        #нас реально несколько: фоновый цикл синка, прокси мессенджера внутри локального
        #сервера, отправка темы, закрытие программы. Замок сеанса в sync_engine защищает
        #только сам цикл, но не эти вызовы. Держим сессию в thread-local: у каждого потока
        #своя, keep-alive при этом сохраняется (потоки долгоживущие).
        self._local = threading.local()
        self._warn_if_insecure()

    def _sess(self, retry_post: bool):
        """Сессия ЭТОГО потока. `retry_post` — можно ли повторять POST (см. _build_session)."""
        key = "idem" if retry_post else "plain"
        got = getattr(self._local, key, None)
        if got is None:
            got = self._build_session(retry_post)
            setattr(self._local, key, got)
        return got

    @staticmethod
    def _build_session(retry_post: bool = False):
        """Сессия с АВТО-РЕТРАЯМИ на транзиентные сетевые сбои. Одиночный блип канала
        до РФ-VPS (обрыв соединения `RemoteDisconnected`, кратковременный отказ TCP,
        502/503/504 от Caddy/Cloudflare, пока бэкенд перезапускается) повторяется на
        уровне HTTP и НЕ доходит до синк-раннера как «сбой» — синхронизация проходит
        прозрачно, а лог не засоряется. Бэкофф между попытками: 0.6 → 1.2 → 2.4 c."""
        s = requests.Session()
        try:
            from urllib3.util.retry import Retry
            from requests.adapters import HTTPAdapter
            retry = Retry(
                total=3, connect=3, read=2, backoff_factor=0.6,
                status_forcelist=(502, 503, 504),
                #🔥 POST ЗДЕСЬ НЕ ПОВТОРЯЕМ. Раньше повторялся с оговоркой «наши POST
                #идемпотентны» — это верно ровно для трёх из них (push сверяет содержимое,
                #login/refresh безопасны), но НЕ для остальных: `create_event`,
                #`approve_registration`, `create_parent`, `create_parent_link` при
                #повторе создают ВТОРУЮ запись. Блип сети на такой отправке давал бы
                #дубль заявки или родителя — молча, потому что оба запроса «успешны».
                #Идемпотентные вызовы просят повтор явно: `_req(..., retry_post=True)`.
                allowed_methods=(frozenset(["GET", "PUT", "DELETE", "HEAD", "OPTIONS",
                                            "POST"]) if retry_post else
                                 frozenset(["GET", "PUT", "DELETE", "HEAD", "OPTIONS"])),
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
        except Exception as e:
            #Без ретраев работать можно, но знать об этом надо: одиночный блип канала
            #станет «сбоем синка» вместо прозрачного повтора.
            _log.warning("авто-ретраи HTTP недоступны (%s) — блипы сети пойдут как сбои", e)
        return s

    def _warn_if_insecure(self):
        """Предупреждаем, если адрес сервера — http к удалённому хосту: тогда ПДн
        (логины, пароли, оценки) пойдут по сети открытым текстом. Не блокируем —
        в ЛВС на этапе настройки это бывает временно нужно, но админ должен знать."""
        try:
            from data import app_settings
            if self.base_url and not app_settings.is_secure_transport(self.base_url):
                _log.warning("сервер задан по http:// к удалённому адресу — персональные "
                             "данные пойдут по сети В ОТКРЫТОМ виде. Для боевой работы "
                             "нужен https:// (server/DEPLOY.md, раздел про Caddy и TLS)")
        except Exception as e:
            _log.debug("проверка безопасности адреса не выполнена: %s", e)

    def _headers(self) -> dict:
        #ngrok-skip-browser-warning — чтобы бесплатные туннели (ngrok и пр.) не
        #подсовывали HTML-страницу-предупреждение вместо JSON ответа API.
        h = {"ngrok-skip-browser-warning": "true"}
        #X-Device-Id — идентификатор этого ПК для барьера подтверждения: сервер по
        #нему решает, одобрено ли устройство (см. server/app/connect.py). Шлём на КАЖДОМ
        #запросе, в т.ч. при входе — иначе неодобренный ПК не отличить от одобренного.
        try:
            from data import app_settings
            dev = app_settings.get_device_id()
            if dev:
                h["X-Device-Id"] = dev
        except Exception:
            pass
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _req(self, method: str, path: str, timeout=DEFAULT_TIMEOUT,
             retry_post: bool = False, **kwargs):
        """Единая точка сетевого вызова: общие заголовки, проверка TLS (verify) и
        авто-ретраи в одном месте — их нельзя случайно забыть.

        `retry_post=True` ставят ТОЛЬКО вызовы, безопасные к повтору: `push` (сервер
        делает upsert по ключу), `login`/`refresh` (повтор выдаёт новый токен, лишних
        сущностей не создаёт). Всё остальное, что создаёт записи, повторять нельзя —
        см. комментарий в `_build_session`."""
        return self._sess(retry_post).request(method, f"{self.base_url}{path}",
                                              headers=self._headers(), timeout=timeout,
                                              verify=self._verify, **kwargs)

    def health(self) -> bool:
        """True, если сервер отвечает. Не кидает исключений.

        ВАЖНО: health БЕЗ авто-ретраев (обычный requests, не self._session) — он должен
        падать БЫСТРО. Его зовут UI-блокирующие пути: вход (_online_bootstrap ставит
        WaitCursor) и закрытие приложения (flush_now). С ретраями (3× connect) оффлайн-
        вход/закрытие висли бы ~18 c. Ретраи нужны фоновому pull/push, а не этой пробе."""
        try:
            r = requests.get(f"{self.base_url}/health", headers=self._headers(),
                             timeout=HEALTH_TIMEOUT, verify=self._verify)
            return r.status_code == 200
        except Exception:
            return False

    def login(self, login: str, password: str) -> dict:
        """Возвращает {access_token, refresh_token, role, name} и запоминает оба токена."""
        r = self._req("POST", "/auth/login", retry_post=True,
                      json={"login": login, "password": password})
        r.raise_for_status()
        data = r.json()
        #Код 200 без токена — дефект контракта сервера. Молча остаться без авторизации
        #нельзя: дальше все запросы пошли бы анонимными и получали 401 без объяснения.
        if not data.get("access_token"):
            raise ValueError("сервер ответил успехом, но не выдал access_token")
        self.token = data["access_token"]
        self.refresh_token = data.get("refresh_token", "") or self.refresh_token
        return data

    def bootstrap_admin(self, login: str, password: str,
                        full_name: str = "Администратор") -> dict:
        """Создаёт первого администратора на сервере (только если его ещё нет)."""
        r = self._req("POST", "/auth/bootstrap-admin",
                      json={"login": login, "password": password,
                            "full_name": full_name})
        r.raise_for_status()
        data = r.json()
        #Код 200 без токена — дефект контракта сервера. Молча остаться без авторизации
        #нельзя: дальше все запросы пошли бы анонимными и получали 401 без объяснения.
        if not data.get("access_token"):
            raise ValueError("сервер ответил успехом, но не выдал access_token")
        self.token = data["access_token"]
        self.refresh_token = data.get("refresh_token", "") or self.refresh_token
        return data

    def refresh(self, refresh_token: str | None = None) -> dict:
        """Тихо обновляет access по refresh-токену (/auth/refresh). Обновляет self.token
        и возвращает данные {access_token, refresh_token, role, name}. Бросает HTTPError,
        если refresh недействителен/отозван — тогда вызывающий делает полный re-login."""
        rt = (refresh_token or self.refresh_token or "").strip()
        if not rt:
            raise ValueError("нет refresh-токена для обновления")
        r = self._req("POST", "/auth/refresh", json={"refresh_token": rt}, retry_post=True)
        r.raise_for_status()
        data = r.json()
        self.token = data.get("access_token") or self.token
        self.refresh_token = data.get("refresh_token", "") or self.refresh_token
        return data

    def logout(self) -> dict:
        """Безопасный выход: просит сервер ОТОЗВАТЬ текущий токен (чёрный список), чтобы
        украденный до выхода токен нельзя было использовать. Best-effort (ошибку глушим)."""
        out = {"revoked": 0}
        try:
            r = self._req("POST", "/auth/logout", timeout=5)
            if r.status_code == 200:
                out = r.json()
        except Exception as e:
            _log.debug("отзыв токена на сервере не прошёл (выход всё равно выполняем): %s", e)
        #🔥 ГАСИМ ТОКЕНЫ В ЛЮБОМ СЛУЧАЕ. Раньше они оставались в объекте, и если клиент
        #переиспользовали (смена пользователя на общем ПК колледжа — обычное дело), он
        #продолжал слать УЖЕ ОТОЗВАННЫЙ токен и получал 401 там, где вход был выполнен.
        self.token = None
        self.refresh_token = ""
        return out

    def pull(self, since: str = "") -> dict:
        """Изменения позже метки since. Возвращает {server_time, changes}.
        Долгий read-таймаут: первый полный pull может быть большим на медленном канале."""
        r = self._req("GET", "/sync/pull", params={"since": since}, timeout=SYNC_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def push(self, changes: dict) -> dict:
        """Отправляет изменения. changes = {users:[...], grades:[...], ...}.
        Долгий read-таймаут: первый пуш накопленного офлайн бывает объёмным."""
        r = self._req("POST", "/sync/push", json={"changes": changes},
                      timeout=SYNC_TIMEOUT, retry_post=True)
        r.raise_for_status()
        return r.json()

    def voice(self, facts_text: str, role: str = "student", question: str = "") -> str:
        """Озвучка готовых фактов LLM на СЕРВЕРЕ (токен провайдера ИИ живёт только там,
        не раздаётся на клиентские ПК — 152-ФЗ). Шлём уже посчитанный обезличенный текст,
        получаем переформулированный. Заголовки (в т.ч. X-Device-Id) ставит _req."""
        r = self._req("POST", "/vector/voice",
                      json={"facts": facts_text, "role": role, "question": question},
                      timeout=30)
        r.raise_for_status()
        return (r.json().get("text") or facts_text).strip()

    def tts(self, text: str, voice: str = "male") -> bytes:
        """Синтез речи на СЕРВЕРЕ (Silero) → WAV-байты. Десктоп-онлайн отдаёт нагрузку
        сюда (на боевом ПК ВСГУТУ — GPU). Оффлайн/ошибка → вызывающий синтезирует локально.
        Заголовки (авторизация, X-Device-Id) ставит _req."""
        r = self._req("POST", "/web/vector/tts",
                      json={"text": text, "voice": voice}, timeout=30)
        r.raise_for_status()
        return r.content

    def set_my_prefs(self, prefs: dict) -> dict:
        """Сохранить личные настройки текущего пользователя (self-scope /me/prefs).
        Меняет ТОЛЬКО свою строку — личность берётся из JWT на сервере."""
        r = self._req("POST", "/me/prefs", json={"prefs": prefs}, timeout=5)
        r.raise_for_status()
        return r.json()

    #Барьер подтверждения подключения (см. server/app/connect.py)
    #Эндпоинты /connect/{request,status,verify} — БЕЗ авторизации (новый ПК ещё не вошёл).
    def connect_request(self, device_id: str, hostname: str = "") -> dict:
        """Новый ПК просит доступ. Возвращает {status}."""
        r = self._req("POST", "/connect/request",
                      json={"device_id": device_id, "hostname": hostname}, timeout=8)
        r.raise_for_status()
        return r.json()

    def connect_status(self, device_id: str) -> str:
        """Опрос статуса запроса (pending|code_issued|approved|rejected|none)."""
        r = self._req("GET", "/connect/status",
                      params={"device_id": device_id}, timeout=8)
        r.raise_for_status()
        return (r.json() or {}).get("status", "none")

    def connect_verify(self, device_id: str, code: str) -> dict:
        """Ввод кода подтверждения. Бросает HTTPError при неверном/просроченном коде."""
        r = self._req("POST", "/connect/verify",
                      json={"device_id": device_id, "code": code}, timeout=8)
        r.raise_for_status()
        return r.json()

    #Действия администратора над запросами (на сервере — require_admin)
    def list_connect_requests(self) -> dict:
        """Активные запросы на подключение. {requests:[...], count}."""
        r = self._req("GET", "/connect/requests", timeout=5)
        r.raise_for_status()
        return r.json()

    def approve_device(self, device_id: str) -> dict:
        """Принять запрос — сервер вернёт 6-значный код для пользователя. {code}."""
        r = self._req("POST", "/connect/approve", json={"device_id": device_id}, timeout=5)
        r.raise_for_status()
        return r.json()

    def reject_device(self, device_id: str) -> dict:
        """Отклонить запрос. {ok}."""
        r = self._req("POST", "/connect/reject", json={"device_id": device_id}, timeout=5)
        r.raise_for_status()
        return r.json()

    #Заявки студентов на самостоятельную регистрацию (на сервере — require_admin).
    #Те же эндпоинты, что и веб-админка, — десктоп теперь видит и решает заявки 1:1.
    def list_registrations(self) -> dict:
        """Заявки на регистрацию, ждущие решения. {requests:[{id,full_name,group,phone,email,created_at}]}."""
        r = self._req("GET", "/web/admin/registrations", timeout=8)
        r.raise_for_status()
        return r.json()

    def approve_registration(self, req_id: str) -> dict:
        """Одобрить заявку: сервер заведёт студента и вышлет пароль на почту. {ok,sent,login,password}."""
        r = self._req("POST", "/web/admin/registrations/approve", json={"id": req_id}, timeout=15)
        r.raise_for_status()
        return r.json()

    def reject_registration(self, req_id: str, note: str = "") -> dict:
        """Отклонить заявку (с необязательной причиной). {ok}."""
        r = self._req("POST", "/web/admin/registrations/reject",
                      json={"id": req_id, "note": note or ""}, timeout=8)
        r.raise_for_status()
        return r.json()

    #Контактные данные с сервера (телефон, IP, последний вход) — для карточек студентов
    #и преподавателей в десктопе, как на сайте (require_admin).
    def admin_students(self, group: str = "") -> dict:
        """Студенты с сервера + контакты (login, phone, last_login, ip). {students:[...]}."""
        r = self._req("GET", "/web/admin/students", params={"group": group or ""}, timeout=8)
        r.raise_for_status()
        return r.json()

    def admin_teachers(self) -> dict:
        """Преподаватели с сервера + контакты (login, phone, last_login, ip). {teachers:[...]}."""
        r = self._req("GET", "/web/admin/teachers", timeout=8)
        r.raise_for_status()
        return r.json()

    #Роль «родитель» (§12) — ParentLink НЕ в SYNC_MODELS (онлайн-only, как мессенджер),
    #поэтому это прямые REST-вызовы, а не generic push. Управление доступно admin/куратору
    #(сервер сам ограничивает куратора его curated_groups — см. server/app/routers/parent.py).
    def create_parent(self, surname: str, name: str, login: str, password: str) -> dict:
        """Завести аккаунт родителя (admin-only на сервере). {ok,id,login}."""
        r = self._req("POST", "/web/admin/parents",
                      json={"surname": surname, "name": name, "login": login, "password": password},
                      timeout=10)
        r.raise_for_status()
        return r.json()

    def list_parents(self) -> dict:
        """Справочник аккаунтов-родителей. {parents:[{id,login,full_name}]}."""
        r = self._req("GET", "/web/staff/parents", timeout=8)
        r.raise_for_status()
        return r.json()

    def update_parent(self, parent_id: str, surname: str = "", name: str = "",
                      password: str = "") -> dict:
        """Правка ФИО/пароля родителя (логин не редактируется — см. серверный docstring
        admin_update_parent). Пустые строки — поле не меняем. {ok,id}."""
        payload = {}
        if surname:
            payload["surname"] = surname
        if name:
            payload["name"] = name
        if password:
            payload["password"] = password
        r = self._req("PUT", f"/web/admin/parents/{parent_id}", json=payload, timeout=10)
        r.raise_for_status()
        return r.json()

    def delete_parent(self, parent_id: str) -> dict:
        """Мягкое удаление аккаунта родителя. {ok,id}."""
        r = self._req("DELETE", f"/web/admin/parents/{parent_id}", timeout=8)
        r.raise_for_status()
        return r.json()

    def list_parent_links(self, group: str = "") -> dict:
        """Связи родитель↔студент (куратору — только его группы). {links:[...]}."""
        r = self._req("GET", "/web/staff/parent-links", params={"group": group or ""}, timeout=8)
        r.raise_for_status()
        return r.json()

    def create_parent_link(self, parent_id: str, student_id: str) -> dict:
        """Привязать родителя к студенту — создаётся в статусе pending, ждёт согласия
        студента. {ok,id,status}."""
        r = self._req("POST", "/web/staff/parent-links",
                      json={"parent_id": parent_id, "student_id": student_id}, timeout=8)
        r.raise_for_status()
        return r.json()

    def revoke_parent_link(self, link_id: str) -> dict:
        """Снять доступ родителя (мягкий отзыв, строка остаётся с status=revoked). {ok}."""
        r = self._req("DELETE", f"/web/staff/parent-links/{link_id}", timeout=8)
        r.raise_for_status()
        return r.json()

    def my_parent_links(self) -> dict:
        """(студент) Заявки родителей на доступ к МОЕМУ журналу + уже выданные. {links:[...]}."""
        r = self._req("GET", "/web/student/parent-links", timeout=8)
        r.raise_for_status()
        return r.json()

    def decide_parent_link(self, link_id: str, approve: bool) -> dict:
        """(студент) Подтвердить/отозвать доступ родителя к своему журналу. {ok,status}."""
        r = self._req("POST", f"/web/student/parent-links/{link_id}/decide",
                      json={"approve": bool(approve)}, timeout=8)
        r.raise_for_status()
        return r.json()

    #Учебные часы группы (план на семестр по предметам) — та же REST-пара, что уже
    #использует веб-редактор («Группы» → 🕐, web/src/pages/admin/AdminGroups.vue).
    #SubjectHours ХОДИТ в SYNC_MODELS (десктоп читает план локально после пулла), но
    #«пройдено X ч» считается сервером по ВСЕМ занятиям группы у ВСЕХ преподавателей —
    #у самого админа локально этих занятий нет, поэтому редактор идёт через REST, а не
    #через локальный store, как «Группы»/«Предметы».
    def group_hours(self, group: str, year: str = "", semester: int = 0) -> dict:
        """Предметы группы с плановыми и уже пройденными часами за семестр.
        {group, term:{year,semester}, subjects:[{subject,hours_total,hours_done}]}."""
        r = self._req("GET", "/web/admin/group-hours",
                      params={"group": group, "year": year or "", "semester": semester or 0},
                      timeout=8)
        r.raise_for_status()
        return r.json()

    def save_group_hours(self, group: str, hours: dict, teachers: dict | None = None,
                        zet: dict | None = None) -> dict:
        """Сохранить часы + назначение препода + ЗЕТ пачкой: {предмет: часов},
        {предмет: teacher_id|''} (§ролей препод↔предмет↔группа), {предмет: float|None}
        (docs/done/PLAN-ZET.md). {ok, saved, term}."""
        r = self._req("POST", "/web/admin/group-hours",
                      json={"group": group, "hours": hours, "teachers": teachers or {},
                            "zet": zet or {}},
                      timeout=10)
        r.raise_for_status()
        return r.json()

    def esstu_specialties(self, group: str = "") -> dict:
        """Справочник специальностей ВСГУТУ (parsers/esstu_parser.py) для диалога
        импорта учебного плана. {specialties:[...], suggested_code}. Внешний сайт —
        таймаут щедрее обычного."""
        r = self._req("GET", "/web/admin/esstu/specialties", params={"group": group},
                      timeout=20)
        r.raise_for_status()
        return r.json()

    def esstu_plan_years(self, specialty_code: str) -> dict:
        """Годы набора с реально опубликованным планом для специальности —
        {years: [...]}, наполняет выпадающий список вместо ручного ввода года."""
        r = self._req("GET", "/web/admin/esstu/plan-years",
                      params={"specialty_code": specialty_code}, timeout=20)
        r.raise_for_status()
        return r.json()

    def import_esstu(self, group: str, specialty_code: str, enrollment_year: int) -> dict:
        """Импорт специальности + учебного плана ВСГУТУ в группу — часы/ЗЕТ ИМЕННО
        текущего курса/семестра (считается от года поступления на сервере,
        study_hours.course_and_semester). {ok, course, semester, term, imported,
        unmapped, saved_hours}. Тянет и парсит PDF на сервере — таймаут щедрый."""
        r = self._req("POST", "/web/admin/groups/import-esstu",
                      json={"group": group, "specialty_code": specialty_code,
                            "enrollment_year": enrollment_year},
                      timeout=40)
        r.raise_for_status()
        return r.json()

    def schedule_groups(self, category: str = "") -> dict:
        """Имена групп категории расписания портала (schedule/parser.py::CATEGORIES)
        — {groups: [...]}. Для выпадающего списка «Импорт группы из категории»."""
        r = self._req("GET", "/web/schedule/groups", params={"category": category}, timeout=20)
        r.raise_for_status()
        return r.json()

    def import_schedule_category(self, category: str, group_name: str) -> dict:
        """Заводит группу-каталожную запись из НЕколледжевой категории расписания
        (Бакалавриат/Заочное 1/2) — {ok, name, category}. Сервер сверяет group_name с
        реальным списком портала для этой категории."""
        r = self._req("POST", "/web/admin/groups/import-schedule-category",
                      json={"category": category, "group_name": group_name}, timeout=20)
        r.raise_for_status()
        return r.json()

    def import_schedule_category_all(self, category: str) -> dict:
        """«Все» — массовый импорт ВСЕХ групп категории — {ok, building, imported,
        skipped, total}. Полный снимок строится на сервере лениво в фоне (десятки-сотни
        страниц портала, ~минута): пока не готов — ok=false, building=true, вызывающий
        подождёт и позовёт снова (тот же приём, что у остальных «полных снимков»)."""
        r = self._req("POST", "/web/admin/groups/import-schedule-category-all",
                      json={"category": category}, timeout=20)
        r.raise_for_status()
        return r.json()

    #Управление сессиями/токенами (на сервере — require_admin)
    def list_sessions(self, active: bool = True) -> dict:
        """Активные выданные токены (сессии): кто, роль, устройство, до когда. {sessions,count}."""
        r = self._req("GET", "/admin/sessions",
                      params={"active": "true" if active else "false"}, timeout=5)
        r.raise_for_status()
        return r.json()

    def revoke_session(self, jti: str = "", login: str = "") -> dict:
        """Отозвать сессию по jti (конкретный токен) ИЛИ по логину (все сессии юзера). {revoked}."""
        r = self._req("POST", "/admin/sessions/revoke",
                      json={"jti": jti or "", "login": login or ""}, timeout=5)
        r.raise_for_status()
        return r.json()

    #Админский мониторинг (доступ на сервере ограничен ролью admin — см. /admin/*)
    def get_online(self) -> dict:
        """Кто сейчас подключён к серверу. {online:[...], count, window_sec}."""
        r = self._req("GET", "/admin/online", timeout=5)
        r.raise_for_status()
        return r.json()

    def get_events(self, since: int = 0) -> dict:
        """Журнал событий сервера дельтой (since — последний полученный id).
        {events:[...], last_id}."""
        r = self._req("GET", "/admin/events", params={"since": since}, timeout=5)
        r.raise_for_status()
        return r.json()

    #Уведомления пользователя (вкладка «Уведомления» в профиле).
    #События серверные по своей природе (их порождает сервер при выставлении оценки и
    #правке расписания), поэтому в синк они НЕ входят и читаются по HTTP. Это чтение,
    #а не операция над журналом, так что offline-first (§1) не нарушается: нет связи —
    #показываем то, что успели загрузить, и честно говорим об отсутствии сети.
    def list_notifications(self, only_unread: bool = False, limit: int = 100) -> dict:
        """Письма пользователя. {items:[{id,kind,title,body,created_at,read_at}], unread}.

        По умолчанию просим ВСЕ: вкладка показывает и прочитанные, как почта. Сервер без
        параметров отдаёт только непрочитанные (так исторически ждёт мобильное
        приложение), поэтому filter передаём явно."""
        params: dict = {"limit": limit}          #значения разнотипные (число + строка)
        if not only_unread:
            params["filter"] = "all"
        r = self._req("GET", "/me/events", params=params, timeout=8)
        r.raise_for_status()
        return r.json()

    def mark_notification_read(self, event_id: str) -> dict:
        """Отметить одно письмо прочитанным."""
        r = self._req("POST", f"/me/events/{event_id}/read", timeout=8)
        r.raise_for_status()
        return r.json()

    def mark_all_notifications_read(self) -> dict:
        """«Прочитать все»."""
        r = self._req("POST", "/me/events/read-all", timeout=8)
        r.raise_for_status()
        return r.json()

    def create_event(self, title: str, body: str, groups: list | None = None) -> dict:
        """§12: мероприятие/событие (олимпиада, конкурс и т.п.) — заводит препод/админ,
        уходит уведомлением kind="event" выбранной аудитории. groups=[] — все группы
        (доступно ТОЛЬКО админу; сервер сам проверяет роль и скоуп групп куратора)."""
        r = self._req("POST", "/web/events",
                      json={"title": title, "body": body, "groups": groups or []}, timeout=8)
        r.raise_for_status()
        return r.json()
