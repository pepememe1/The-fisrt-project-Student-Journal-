"""
sync_runner.py — Фоновая синхронизация десктопа с сервером (через API).

Работает в отдельном потоке-демоне, чтобы не блокировать интерфейс.
Запускается после входа пользователя, ЕСЛИ задан адрес сервера
(app_settings.get_api_url). Нет сети/сервера — тихо ждёт и повторяет позже:
offline-first сохраняется, прога продолжает работать на локальном SQLite.

Авторизация к API — теми же логином/паролем, что ввёл пользователь. Токен живёт
в памяти на время сессии; при сбое — перелогин на следующем цикле.
"""
import threading
import time
from typing import TYPE_CHECKING

import log
from data import student_link
from data.app_settings import get_api_url

if TYPE_CHECKING:                      #только для проверки типов
    #Настоящий импорт СОЗНАТЕЛЬНО ленивый (внутри методов): sync_client тянет requests,
    #а модуль поднимается на пути запуска программы. Здесь он нужен лишь как аннотация.
    from sync.sync_client import SyncClient

_log = log.get("sync")


class SyncManager:
    def __init__(self, interval_sec: int = 30):
        #Аннотации у полей, которые НАЧИНАЮТСЯ с None и позже получают значение:
        #без них mypy выводит тип «None» из первого присваивания и дальше считает
        #ошибкой и `self._thread.start()`, и любое обращение к клиенту.
        self._thread: threading.Thread | None = None
        self._running = False
        self._client: "SyncClient | None" = None
        self._login = ""
        self._password = ""
        self._role = ""
        self._url = ""           #адрес сервера, под который создан текущий клиент
        self._interval = interval_sec
        self._on_synced = None   #колбэк после успешного цикла (для обновления UI)
        self._on_state = None    #колбэк смены онлайн/офлайн (для индикатора в шапке)
        self._wake = threading.Event()   #«будильник» для немедленного синка
        #🔥 СОСТОЯНИЕ ЗЕРКАЛА — ОТДЕЛЬНО ОТ СОСТОЯНИЯ СИНКА (07.09.2026).
        #Зеркало (`desktop/local_mirror.py`) наполняет ТУ базу, на которой работает
        #Vue-интерфейс, а обычный синк — ДРУГУЮ. Раньше результат `mirror_once` просто
        #выбрасывался: он возвращает {"ok": False, "error": ...}, а не бросает исключение,
        #поэтому `except` вокруг вызова его не видел. Итог — тихая деградация худшего
        #сорта: обмен со старой базой идёт, цикл ставит online=True, а человек смотрит на
        #устаревшие данные и не имеет НИ ОДНОГО признака этого. Причина была только в логе.
        #🔥 СИГНАЛ ОСТАНОВКИ — СВОЙ У КАЖДОГО ЗАПУСКА (07.09.2026).
        #Прежде цикл жил на общем поле `self._running`: stop() ставил его в False и
        #НЕ ЖДАЛ завершения потока, а следующий start() тут же возвращал его в True.
        #Старый поток к этому моменту спал в `_wake.wait(...)` — просыпался, видел снова
        #True и продолжал работать РЯДОМ с новым. Два цикла на одну базу и один сетевой
        #клиент: гонка за токеном, двойные push и пересоздание клиента под чужой ногой.
        #Общего изменяемого флага здесь недостаточно по построению — нужен объект,
        #принадлежащий КОНКРЕТНОМУ запуску, который старый поток не может «разостановить».
        self._stop_evt = None        #threading.Event текущего запуска
        self._mirror_error = ""
        self._mirror_ok_at = ""      #ISO-время последнего УСПЕШНОГО обновления копии
        #Сохранённый токен пробуем РОВНО один раз за сессию входа: если он протух,
        #дальше идём по паролю, а не крутим бесконечно негодный токен.
        self._saved_token_tried = False
        #Jitter перед входом по паролю — один раз за процесс (размазать «герд»).
        self._jitter_done = False
        #Устойчивость: считаем подряд идущие неудачи для ЭКСПОНЕНЦИАЛЬНОГО БЭКОФФА
        #(не долбить мёртвый сервер каждые 30 c) и держим онлайн/офлайн-состояние
        #для индикатора. None = ещё не знаем, True/False = определились.
        self._fail_count = 0
        self._online: bool | None = None
        self._last_error = ""
        #Причина последнего неудачного входа ('' — вход в порядке). Держим отдельно от
        #_last_error: сетевой сбой лечится сам, а отказ входа требует действия человека
        #(войти заново, попросить админа снять блокировку) — и различать их обязан UI.
        self._auth_error = ""
        #🔥 РЕКОНСИЛЯЦИЯ «СЕРВЕР = ИСТИНА» ОДИН РАЗ ЗА СЕССИЮ ВХОДА (инвариант §4.5).
        #Раньше её звал `main_window._restore_client_bg`, а `main_window.py` удалён вместе
        #с Qt-оболочкой — и вызывающий не остался НИ ОДИН: функция `sync_engine.reconcile`
        #продолжала существовать, покрываться тестами и не выполняться никогда. То есть
        #«осиротевшие» локальные записи (удалённые на сервере, пока этот ПК был офлайн)
        #перестали исчезать вовсе. Теперь крючок здесь: это единственное место, которое
        #переживает любую оболочку окна и уже знает, что сеть и токен в порядке.
        self._need_reconcile = False

    def trigger(self):
        """Разбудить синкер прямо сейчас (например, после сохранения данных),
        чтобы изменения ушли на сервер без ожидания интервала."""
        self._wake.set()

    def set_on_synced(self, cb):
        """Колбэк, вызываемый после успешной синхронизации. UI подключает сюда
        обновление текущего экрана (через потокобезопасный сигнал Qt)."""
        self._on_synced = cb

    def set_on_state(self, cb):
        """Колбэк смены онлайн/офлайн: cb(online: bool, error: str). Вызывается ТОЛЬКО
        при изменении состояния (не на каждом цикле). UI показывает индикатор."""
        self._on_state = cb

    def status(self) -> dict:
        """Текущее состояние синка для UI/диагностики.

        `auth_error`, `rejected` и `conflicts` вынесены отдельно от `error` НАМЕРЕННО:
        это четыре разных по смыслу состояния, и лечатся они по-разному.
          error     — сеть/сервер недоступны. Пройдёт само, делать ничего не надо.
          auth_error— вход не проходит (пароль, отозванная сессия, 429). Синка не будет,
                      пока человек не войдёт заново; само НЕ пройдёт.
          rejected  — сервер отверг часть правок (нет прав на предмет/группу). Они
                      остались только на этом ПК; нужен админ, чтобы вернуть назначение.
          conflicts — расхождения по оценкам, которые никто не разрешил: у нас одно
                      значение, на сервере другое. Само НЕ пройдёт и не пройдёт вообще —
                      экрана разбора сейчас нет (см. DBManager.list_conflicts), поэтому
                      число здесь единственное, что отличает «всё сошлось» от «две
                      разные правды живут рядом».
        Свести их в одну строку значило бы показать «нет связи» там, где связь есть."""
        from sync.sync_engine import last_rejected
        from data.core import DBManager
        return {"online": self._online, "fails": self._fail_count,
                "error": self._last_error, "auth_error": self._auth_error,
                "rejected": last_rejected(),
                "conflicts": DBManager.count_unresolved_conflicts(),
                #ПЯТОЕ состояние, и оно тоже отдельное: обмен может идти прекрасно, а
                #копия, из которой рисуется журнал, — стоять. Пустая строка = бед нет.
                "mirror_error": self._mirror_error,
                "mirror_ok_at": self._mirror_ok_at}

    def _set_online(self, online: bool, error: str = ""):
        """Обновить онлайн-состояние; колбэк дёргаем только при РЕАЛЬНОЙ смене."""
        self._last_error = error or ""
        if self._online is online:
            return
        self._online = online
        if self._on_state:
            try:
                self._on_state(online, error or "")
            except Exception:
                pass

    def current_auth(self):
        """(url, token) текущей сессии — чтобы админ-панель могла сама дёрнуть
        служебные эндпоинты /admin/*. Берём ЖИВОЙ токен синкера, иначе сохранённый
        для текущего логина. ('', '') — если адрес/вход не заданы."""
        url = get_api_url()
        token = ""
        #getattr с запасным значением тут был защитой от отсутствия поля, которого
        #у SyncClient не бывает (оно задаётся в __init__ всегда). `or ""` — потому
        #что токен может быть None: так `logout` гасит отозванный (см. sync_client).
        if self._client and self._client.token:
            token = self._client.token or ""
        elif self._login:
            from data import app_settings
            token = app_settings.get_saved_token(self._login)
        return url, (token or "")

    def fresh_auth(self):
        """(url, token) с ГАРАНТИРОВАННО живым токеном: если текущий протух, тихо
        обновляет его по refresh-токену (та же цепочка, что у фонового синка).

        Нужно встроенному веб-view мессенджера: JWT живёт жёстко 5 ч, и просроченный
        токен SPA просто отвергает — внутри десктопа показывалась ВЕБ-ФОРМА ВХОДА, хотя
        человек в программу уже вошёл. current_auth() отдаёт токен как есть и для этого
        не годится. Сеть недоступна — вернём что есть (решает вызывающий).

        ⚠️ (живой отзыв Влада) `allow_jitter=False` ОБЯЗАТЕЛЕН здесь. Эта функция висит
        синхронно ВНУТРИ HTTP-запроса живого человека (прокси мессенджера/`/me/prefs`,
        см. `desktop/local_api.py::install_remote_proxy`) — если токена ещё нет (холодный
        старт программы), `_ensure_auth` без этого флага могла ждать до
        `GRADEBOOK_LOGIN_JITTER_SEC` (по умолчанию 0-8 с СЛУЧАЙНО) ПЕРЕД входом по
        паролю: та задержка задумана для ФОНОВОГО цикла `_loop()` (размазать вход сотен
        ПК по 9:00), а не для интерактивного запроса, где человек прямо сейчас смотрит
        на пустой список чатов и ждёт. Фоновый `_loop()` свой джиттер не теряет — у него
        отдельный вызов `_ensure_auth(url)` с джиттером по умолчанию, флаг `_jitter_done`
        на процесс общий, но срабатывает только там, где джиттер реально запрашивали."""
        url = get_api_url()
        if not url:
            return "", ""
        try:
            from sync.sync_client import is_token_expired
            _, token = self.current_auth()
            if token and not is_token_expired(token):
                return url, token
            if self._ensure_auth(url, allow_jitter=False) and self._client and self._client.token:
                return url, self._client.token
        except Exception as e:
            _log.warning("обновление токена для веб-view не удалось: %s", e)
        return self.current_auth()

    def start(self, login: str, password: str, role: str):
        """Запустить фоновую синхронизацию для вошедшего пользователя.

        Креды запоминаем ВСЕГДА, даже если адрес сервера ещё не задан. Это важно
        для хост-ПК: администратор поднимает сервер уже ПОСЛЕ входа (адрес ведь
        появляется только после запуска сервера — иначе замкнутый круг). Раньше
        здесь стоял ранний `return` при пустом адресе: поток не стартовал, креды
        терялись, и когда сервер наконец поднимали — синхронизация так и не шла.
        Из-за этого админ не отдавал на сервер пользователей (преподаватели/студенты
        получали 401 при входе через API), а мониторинг не имел токена. Теперь поток
        стартует всегда и сам подхватывает адрес, как только он появится."""
        self._login, self._password, self._role = login, password, role
        #Новый вход — снова разрешаем попытку по сохранённому токену именно этого
        #пользователя (на одном ПК мог входить другой).
        self._saved_token_tried = False
        #Новая сессия входа — снова нужна сверка с сервером (см. поле в __init__).
        self._need_reconcile = True
        if self._running:
            return
        #Предыдущий поток мог ещё не выйти (stop() его не ждёт — см. ниже, почему).
        #Дожидаемся ЗДЕСЬ, а не в stop(): выход из аккаунта и закрытие программы не
        #должны стоять на сетевом таймауте, а вот запуск второго цикла поверх живого
        #первого недопустим совсем.
        self._join_previous()
        self._running = True
        self._stop_evt = threading.Event()
        self._wake.clear()
        self._thread = threading.Thread(target=self._loop, args=(self._stop_evt,),
                                        daemon=True)
        self._thread.start()

    def _join_previous(self, timeout: float = 5.0):
        """Дождаться завершения прежнего фонового потока (если он ещё жив).

        Таймаут намеренный и не «на всякий случай»: поток может стоять в сетевом вызове
        с чтением до 45 c, а держать вход человека всё это время нельзя. Не дождались —
        новый цикл всё равно безопасен: старый работает по СВОЕМУ `_stop_evt`, который
        уже взведён, и выйдет на ближайшей проверке, не тронув чужое состояние."""
        t = self._thread
        if t is None or not t.is_alive() or t is threading.current_thread():
            return
        t.join(timeout=timeout)
        if t.is_alive():
            _log.warning("прежний поток синка ещё не завершился — новый цикл стартует "
                         "рядом, старый выйдет по своему сигналу остановки")

    def stop(self):
        self._running = False
        #Взводим сигнал ИМЕННО ЭТОГО запуска. Даже если следующий start() вернёт
        #`self._running` в True раньше, чем старый поток проснётся, — его собственный
        #`_stop_evt` останется взведённым, и он выйдет. Это и есть разница между
        #«общий флаг» и «сигнал на запуск».
        if self._stop_evt is not None:
            self._stop_evt.set()
        self._client = None
        self._wake.set()   #будим цикл, чтобы он завершился сразу, а не через интервал

    def _apply_login_jitter(self):
        """Случайная задержка ПЕРЕД входом по паролю — один раз за процесс.

        Зачем: когда сотни ПК включают прогу в 9:00, все разом бьют в `/auth/login`,
        а PBKDF2 (200k) на сервере упирается в CPU. Случайный сдвиг 0..N секунд
        размазывает пик. Спим в фоновом потоке синка — UI не блокируется, локальные
        данные уже на экране (offline-first), задержка незаметна. N задаётся
        переменной GRADEBOOK_LOGIN_JITTER_SEC (по умолчанию 8 c; 0 — выключить)."""
        if self._jitter_done:
            return
        self._jitter_done = True
        import os
        try:
            max_s = float(os.environ.get("GRADEBOOK_LOGIN_JITTER_SEC", "8"))
        except ValueError:
            max_s = 8.0
        if max_s <= 0:
            return
        import random
        time.sleep(random.uniform(0, max_s))

    def _ensure_auth(self, url: str, allow_jitter: bool = True) -> bool:
        """Гарантирует наличие ГОДНОГО токена. Порядок (от дешёвого к дорогому):
          1) уже есть непросроченный access — берём его;
          2) сохранённый access (без дорогого PBKDF2), если он ещё жив;
          3) ТИХОЕ обновление refresh-токеном — без пароля (главное: не выкидываем
             пользователя на логин, когда короткий access протух, в т.ч. за офлайн);
          4) резерв — вход по паролю (+ bootstrap администратора при первом запуске).

        Просрочку access проверяем локально (is_token_expired, exp — абсолютная метка):
        офлайн-время учитывается, «заморозить» токен нельзя."""
        from sync.sync_client import SyncClient, is_token_expired
        from data import app_settings
        #🔥 КЛИЕНТ ОБЯЗАН СООТВЕТСТВОВАТЬ АДРЕСУ. Проверка смены адреса стояла только в
        #`_loop`, а `flush_now`/`fresh_auth` зовут `_ensure_auth` НАПРЯМУЮ из другого
        #потока — между сменой адреса и следующим витком цикла они видели старого клиента
        #с ещё живым токеном, получали True и отправляли данные НА ПРЕЖНИЙ СЕРВЕР.
        #Адрес меняется штатно: поддомен туннеля, переезд с ЛВС на боевой домен.
        if self._client is not None and self._client.base_url != SyncClient.normalize(url):
            self._client = None
            self._saved_token_tried = True   #сохранённый токен выдан ДРУГИМ сервером
        if self._client is None:
            self._client = SyncClient(url)
        #Подтянем сохранённый refresh-токен (для тихого обновления) — один раз.
        if not self._client.refresh_token and self._login:
            self._client.refresh_token = app_settings.get_saved_refresh_token(self._login)

        #1) есть живой access
        if self._client.token and not is_token_expired(self._client.token):
            return True

        #2) сохранённый access (ровно один раз за сессию), если он ещё не протух
        if not self._client.token and not self._saved_token_tried:
            self._saved_token_tried = True
            saved = app_settings.get_saved_token(self._login)
            if saved and not is_token_expired(saved):
                self._client.token = saved
                return True

        #3) тихое обновление refresh-токеном — без пароля
        tried_refresh = bool(self._client.refresh_token)
        if tried_refresh and self._try_refresh():
            return True

        #4) вход по паролю. ВАЖНО: если пароля нет (восстановленная сессия без
        #сохранённого токена — так синк и стартует при запуске по сохранённой сессии:
        #`start(login, "", role)`; раньше это делал `main_window`, теперь мост входа в
        #`desktop/local_api.py`),
        #НЕ ходим на сервер. Неудачный вход с пустым паролем каждый цикл накручивал бы
        #анти-брутфорс (429) и блокировал бы даже правильный вход. Тихо ждём, пока
        #пользователь войдёт заново с паролем (data_store передаст его в start()).
        if not (self._password or "").strip():
            #🔥 НО «пароля нет» — это ДВА разных состояния, и раньше они были склеены.
            #Здесь стоял голый `return False`, а вызывающий не считал это сбоем, потому
            #что «сети мы не касались». Для случая «токена нет вовсе» это верно. А вот
            #если refresh-токен БЫЛ и его отвергли — шагом выше мы уже сходили в сеть и
            #получили отказ. Это самый частый десктопный случай (запуск по сохранённой
            #сессии, токен отозван админом или истёк), и он давал ровно тот симптом,
            #ради которого счётчик и заводился: POST /auth/refresh каждые 30 c
            #бесконечно, без бэкоффа, при индикаторе «онлайн». Данные при этом не
            #терялись (метку не двигаем), но система врала человеку и долбила сервер.
            if tried_refresh:
                self._auth_error = ("сессия отозвана или истекла — нужен вход заново "
                                    "(тихое обновление токена отвергнуто сервером)")
            else:
                #Токена не было вовсе — сети не касались, это чистое ожидание входа.
                #Гасим причину ЯВНО: оставшись от прошлого цикла, она заставила бы
                #вызывающего вечно считать сбоем то, чего сейчас не произошло.
                self._auth_error = ""
            return False
        #Перед входом — jitter (размазать герд входов в 9:00). ⚠️ ТОЛЬКО в фоновом потоке:
        #flush_now() зовётся из _on_quit в ГЛАВНОМ потоке, и сон до 8 c там задерживал бы
        #закрытие программы (в логе это выглядело как «зависла на выходе»).
        self._client.token = None
        if allow_jitter:
            self._apply_login_jitter()
        try:
            self._client.login(self._login, self._password)
            self._save_tokens()
            return True
        except Exception as e:
            #🔥 ПРИЧИНУ ЗАПОМИНАЕМ. Раньше здесь стоял голый `return False`, и отказ входа
            #(неверный пароль, отозванная сессия, 429 анти-брутфорса) не оставлял НИ
            #СЛЕДА: `_loop` видел только False, не считал это сбоем — а значит не включал
            #бэкофф и не переключал индикатор. Программа молча ходила на `/auth/login`
            #каждые 30 c бесконечно, накручивая тот самый анти-брутфорс, и выглядела при
            #этом «онлайн». Диагностировать было нечем.
            self._auth_error = str(e)
            if self._role == "admin":
                try:
                    self._client.bootstrap_admin(self._login, self._password)
                    self._save_tokens()
                    self._auth_error = ""
                    return True
                except Exception as e2:
                    self._auth_error = f"{e} / bootstrap: {e2}"
            return False

    def _try_refresh(self) -> bool:
        """Пробует тихо обновить access по refresh-токену. True — получилось. False —
        refresh недействителен/отозван (тогда _ensure_auth уйдёт на вход по паролю)."""
        if not self._client or not self._client.refresh_token:
            return False
        try:
            self._client.refresh()
            self._save_tokens()
            return True
        except Exception as e:
            #Не молчим: отказ refresh — законный шаг вниз по цепочке (уйдём на пароль),
            #но у него две РАЗНЫЕ причины, и различать их можно только по следу — сеть
            #отпала (само пройдёт) или сессию отозвали/она истекла (нужен вход заново).
            _log.debug("тихое обновление токена не удалось, пробуем вход по паролю: %s", e)
            return False

    def _save_tokens(self):
        """Сохраняет access и refresh в зашифрованное локальное хранилище (DPAPI/Fernet)."""
        from data import app_settings
        app_settings.set_saved_token(self._login, self._client.token)
        if self._client.refresh_token:
            app_settings.set_saved_refresh_token(self._login, self._client.refresh_token)

    def _loop(self, stop_evt=None):
        from sync import sync_engine
        #Условие двойное и оба нужны: `_running` гасит цикл штатно, `stop_evt` —
        #персонально этот поток, даже если поле уже вернули в True новым запуском.
        while self._running and not (stop_evt is not None and stop_evt.is_set()):
            #Адрес сервера читаем КАЖДЫЙ цикл, а не один раз при старте: на хост-ПК он
            #появляется уже после входа (админ поднимает сервер), а у serveo поддомен
            #может смениться между запусками. Нет адреса — тихо ждём и проверяем снова.
            url = get_api_url()
            if not url:
                self._sleep_cycle()
                continue
            #Сменился адрес — старый клиент/токен относятся к другому серверу и больше
            #не годятся: пересоздаём клиента и идём по паролю (сохранённый токен чужой).
            if self._url and self._url != url:
                self._client = None
                self._saved_token_tried = True
            self._url = url
            try:
                if not self._ensure_auth(url):
                    #🔥 ОТКАЗ ВХОДА — ЭТО СБОЙ, а не «ничего не произошло». Раньше ветка
                    #была пустой: счётчик не рос, значит не включался бэкофф и мы били в
                    #`/auth/login` каждые 30 c бесконечно (прямая дорога в 429), а
                    #индикатор продолжал показывать прежнее состояние — человек видел
                    #«онлайн» при том, что не синхронизировалось НИЧЕГО.
                    #Пустой пароль (восстановленная сессия) — штатное ожидание, а не сбой:
                    #сети мы в этом случае не касались, ждать нечего и шуметь незачем.
                    #⚠️ НО только если не касались ДЕЙСТВИТЕЛЬНО. Прежняя формулировка
                    #была неверна: при живом refresh-токене `_ensure_auth` уже сходил в
                    #сеть и получил отказ, а мы считали это «ничего не произошло». Теперь
                    #такой случай приходит с заполненным `_auth_error` — и считается сбоем
                    #наравне с отказом по паролю: растёт счётчик, включается бэкофф,
                    #индикатор перестаёт врать «онлайн».
                    if (self._password or "").strip() or self._auth_error:
                        self._fail_count += 1
                        self._set_online(False, self._auth_error or "вход не выполнен")
                        if self._fail_count == 1:
                            _log.warning("вход на сервер не удался — синхронизации не "
                                         "будет, пока он не пройдёт: %s",
                                         self._auth_error or "причина неизвестна")
                    self._sleep_cycle()
                    continue
                self._auth_error = ""
                #Сверка уже заканчивается полным циклом обмена (см. `reconcile`), поэтому
                #обычный `sync_once` следом не нужен — он был бы вторым кругом подряд.
                if not self._reconcile_once():
                    sync_engine.sync_once(self._client)
                self._flush_pending_prefs()   #до-отправляем тему, если зависла
                #Доклеиваем оценкам неизменяемый id студента. Именно ЗДЕСЬ, после
                #pull: справочник студентов уже свежий, есть с чем сопоставлять.
                #Идемпотентно и дёшево (берутся только строки с пустым id), поэтому
                #флаг «уже сделано» не нужен — он соврал бы на свежей установке,
                #где справочник ещё не приехал и клеить было не с чем.
                student_link.backfill_quietly()
                #Зеркало для ОБЩЕГО Vue-интерфейса: тем же успешным циклом обновляем
                #локальную копию серверной базы (desktop/local_mirror.py). Именно здесь, а
                #не отдельным таймером: раз сеть только что была доступна и токен свеж,
                #второй раз это выяснять незачем. Сбой внутри проглатывается там же и
                #цикл не роняет; модуль опционален (server-пакета может не быть рядом).
                self._mirror_for_vue()
                #Успех: сбрасываем бэкофф и помечаем «онлайн».
                self._fail_count = 0
                self._set_online(True)
                if self._on_synced:
                    try:
                        self._on_synced()   #сигнал «данные обновились» в UI
                    except Exception as e:
                        #Колбэк — чужой код (обновление экрана). Его падение не имеет
                        #права ронять синк, но и исчезать бесследно не должно.
                        _log.debug("колбэк обновления UI упал: %s", e)
            except Exception as e:
                #Сеть/токен/сервер недоступны — не критично, повторим позже с БЭКОФФОМ.
                self._client = None   # сбросим, чтобы перелогиниться
                self._fail_count += 1
                self._set_online(False, str(e))
                #Транзиентный блип канала (попытки 1-2) — на DEBUG, чтобы НЕ пугать
                #пользователя в логе: клиент уже ретраит на уровне HTTP, а offline-first
                #держит данные локально. Шумим только когда сбой РЕАЛЬНО повторяется.
                if self._fail_count <= 2:
                    _log.debug("синк отложен (попытка %s): %s", self._fail_count, e)
                elif self._fail_count == 3:
                    _log.info("синк пока не проходит (попытка 3): %s", e)
                elif self._fail_count == 4:
                    _log.warning("сервер недоступен — режим редких повторов (бэкофф). "
                                 "Данные в безопасности локально, синк возобновится сам.")
            self._sleep_cycle()

    def _reconcile_once(self) -> bool:
        """Сверка «сервер = истина» — ОДИН раз за сессию входа, здесь и только здесь.

        Возвращает True, если сверка РЕАЛЬНО прошла (а значит полный цикл обмена уже
        сделан внутри неё и повторять его не надо).

        Зачем вообще: обычный дельта-pull приносит изменённые строки, но НЕ убирает
        локальные записи, которых на сервере уже нет. Пока ПК был офлайн, админ мог
        удалить студента или группу — и на этом ПК они остались бы навсегда, потому что
        удаление приходит надгробием, а надгробие приходит только тем, кто был на связи.
        Реконсиляция стирает синхронизируемый кэш и наливает его полным снимком.

        ⚠️ ХОСТ ОСВОБОЖДЁН. На хост-ПК локальные данные и есть источник правды (сервер
        поднят из этой же программы) — стереть их и «налить с сервера» значило бы стереть
        их и налить обратно, а при первом же сбое сети — просто стереть.

        ⚠️ Порядок обязателен и обеспечен самой `reconcile`: сначала push офлайн-правок,
        и лишь при его успехе — очистка. Провал пуша поднимает исключение, флаг НЕ
        снимаем — повторим следующим циклом. Флаг снимается только после удачи, иначе
        сверка шла бы каждые 30 c: это полный снимок базы, самый дорогой обмен из всех.
        """
        if not self._need_reconcile or not self._client:
            return False
        try:
            from data.app_settings import is_host
            if is_host():
                self._need_reconcile = False
                return False
        except Exception as e:      # noqa: BLE001
            #Не смогли выяснить, хост мы или клиент. Стирать кэш вслепую нельзя: на хосте
            #это уничтожило бы единственную копию. Пропускаем сверку — она не срочная.
            _log.warning("роль ПК (хост/клиент) не определилась (%s) — сверку с сервером "
                         "в этот раз пропускаю", e)
            self._need_reconcile = False
            return False
        from sync import sync_engine
        try:
            sync_engine.reconcile(self._client)
        except Exception as e:      # noqa: BLE001
            #Кэш при этом ЦЕЛ (reconcile стирает его только после удачного пуша).
            _log.warning("сверка с сервером не удалась (%s) — повторю следующим циклом", e)
            raise
        self._need_reconcile = False
        _log.info("сверка с сервером выполнена: локальный кэш соответствует серверу")
        return True

    def _sleep_cycle(self):
        """Ждём до следующего цикла ИЛИ «будильник» (trigger при изменении данных/запуске
        сервера). При серии неудач — ЭКСПОНЕНЦИАЛЬНЫЙ БЭКОФФ: пауза растёт 30→60→120→240→
        300 c (потолок 5 мин), чтобы не долбить мёртвый сервер. Успех обнуляет счётчик →
        снова быстрый интервал. Любой trigger (сохранение данных) будит немедленно."""
        delay = self._interval
        if self._fail_count > 0:
            delay = min(self._interval * (2 ** min(self._fail_count, 4)), 300)
        self._wake.wait(timeout=delay)
        #⚠️ Флаг общего «будильника» чистим ТОЛЬКО пока цикл действительно живой. Иначе
        #уходящий поток съел бы сигнал, предназначенный новому: тот проспал бы полный
        #интервал вместо немедленного синка.
        if self._running:
            self._wake.clear()

    def push_my_prefs(self, prefs: dict):
        """Отправить личные настройки (тему оформления) текущего пользователя на сервер.

        Строго self-scope (POST /me/prefs), в ОТДЕЛЬНОМ потоке — UI не блокируем.
        Если отправить сейчас нельзя (офлайн / нет токена / запрос упал) — кладём
        prefs в отложенные (app_settings.set_pending_prefs) и до-отправим при следующей
        удачной синхронизации (_flush_pending_prefs). Иначе выбранная тема осталась бы
        только локально и не уехала бы в БД (не «роумилась» на другие ПК)."""
        from data import app_settings
        url = get_api_url()
        token = ""
        #getattr с запасным значением тут был защитой от отсутствия поля, которого
        #у SyncClient не бывает (оно задаётся в __init__ всегда). `or ""` — потому
        #что токен может быть None: так `logout` гасит отозванный (см. sync_client).
        if self._client and self._client.token:
            token = self._client.token or ""
        elif self._login:
            token = app_settings.get_saved_token(self._login)
        if not url or not token:
            if self._login:
                app_settings.set_pending_prefs(self._login, prefs)
            return

        def _send():
            try:
                from sync.sync_client import SyncClient
                SyncClient(url, token).set_my_prefs(prefs)
                app_settings.clear_pending_prefs()
            except Exception as e:
                _log.warning("тема на сервер не ушла (отложу): %s", e)
                if self._login:
                    app_settings.set_pending_prefs(self._login, prefs)
        threading.Thread(target=_send, daemon=True).start()

    def _flush_pending_prefs(self):
        """Если с прошлого раза тема не доехала до сервера — до-отправляем её сейчас,
        пользуясь уже живым токеном текущего цикла. Зовётся после удачного sync_once."""
        if not self._login or self._client is None:
            return
        from data import app_settings
        prefs = app_settings.get_pending_prefs(self._login)
        if not prefs:
            return
        try:
            from sync.sync_client import SyncClient
            SyncClient(self._url, self._client.token).set_my_prefs(prefs)
            app_settings.clear_pending_prefs()
        except Exception as e:
            _log.warning("отложенная отправка темы не удалась: %s", e)

    def _mirror_for_vue(self):
        """Обновить локальную копию серверной базы — ту, на которой работает общий
        Vue-интерфейс (см. desktop/local_mirror.py, «один интерфейс» §11 CLAUDE.md).

        Полностью изолировано: любая ошибка тут не должна ронять обычный синк, ради
        которого цикл и существует. Модуль опционален — в окружении без серверного
        пакета рядом (`server/`) его просто нет, и это штатная ситуация, а не сбой."""
        try:
            from desktop import local_mirror
        except Exception as e:
            #Серверного пакета рядом нет — зеркала в этой сборке не существует вовсе.
            #Это НЕ беда копии, и записывать её в mirror_error нельзя: человек увидел бы
            #вечную жалобу там, где всё работает как задумано.
            _log.debug(f"[mirror] модуль недоступен: {e}")
            return
        try:
            res = local_mirror.mirror_once(client=self._client) or {}
        except Exception as e:
            #Исключение — тоже отказ зеркала, и молчать о нём нельзя ровно так же.
            self._mirror_error = str(e)
            _log.warning(f"[mirror] сорвалось: {e}")
            return
        #🔑 РЕЗУЛЬТАТ ЧИТАЕМ. Прежде он выбрасывался, и это был не недосмотр в одну
        #строку, а целый невидимый режим отказа: `mirror_once` про неудачу СООБЩАЕТ
        #(ok=False + error), а не бросает.
        if res.get("ok"):
            self._mirror_error = ""
            self._mirror_ok_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        else:
            self._mirror_error = str(res.get("error") or "копия не обновилась")
            _log.warning(f"[mirror] копия не обновилась: {self._mirror_error}")

    def flush_now(self):
        """Синхронный один цикл синхронизации ПРЯМО СЕЙЧАС — для выхода из аккаунта и
        закрытия проги. Пушит накопленное, если есть авторизация. Долго не блокирует:
        без пароля/токена _ensure_auth вернёт False сразу (фикс выше), а сетевые вызовы
        ограничены таймаутом клиента. Так данные не теряются при закрытии крестиком."""
        try:
            url = get_api_url()
            if not url:
                return
            self._url = url
            #На выходе jitter не применяем — иначе закрытие ждало бы случайную паузу.
            if self._ensure_auth(url, allow_jitter=False):
                #Быстрая проверка доступности (health, таймаут 3с): если сервер молчит —
                #НЕ вешаем закрытие программы полным push/pull (5+30с таймаут). Данные уже
                #в локальной БД (offline-first) и уедут при следующем запуске.
                if not self._client.health():
                    return
                from sync import sync_engine
                #wait=8: если фоновый цикл прямо сейчас в середине push (read-таймаут 45 c),
                #ждать его при закрытии программы нельзя — окно «зависло бы» на глазах.
                #Пропуск безопасен: тот цикл собирает дельту по водяному знаку, а не «свои»
                #правки, поэтому наши изменения уедут вместе с его пушем.
                sync_engine.sync_once(self._client, wait=8)
                self._flush_pending_prefs()
        except Exception as e:
            _log.warning("flush перед выходом не удался: %s", e)


#Глобальный менеджер на процесс.
_manager = SyncManager()


def start(login: str, password: str, role: str):
    _manager.start(login, password, role)


def stop():
    _manager.stop()


def set_on_synced(cb):
    _manager.set_on_synced(cb)


def set_on_state(cb):
    """Колбэк смены онлайн/офлайн: cb(online: bool, error: str). Для индикатора в шапке."""
    _manager.set_on_state(cb)


def trigger():
    """Немедленно разбудить синкер (после изменения данных)."""
    _manager.trigger()


def flush():
    """Синхронно до-отправить накопленные правки перед выходом из программы.

    Обёртка над методом менеджера `flush_now` — main.py на закрытии зовёт именно
    модульную `sync_runner.flush()`. Без авторизации/сервера выходит сразу, не вешая
    закрытие приложения."""
    _manager.flush_now()


def status() -> dict:
    """Состояние синка для интерфейса (обёртка над singleton — как `start`/`flush`).

    🔥 До появления этой обёртки `SyncManager.status()` не звал НИКТО, кроме теста:
    индикатор, ради которого метод писался, жил в Qt-оболочке и удалён вместе с ней.
    Получалось, что конфликты оценок и отвергнутые сервером правки «звучали» только
    строкой в `gradebook.log`, которую никто не открывает, а комментарии в коде при этом
    уверенно ссылались на несуществующего читателя.

    Настоящий потребитель — `desktop/local_api.py::install_sync_status`
    (`GET /desk/sync/status`), оттуда значения попадают в значок в боковой панели.
    """
    return _manager.status()


def current_login() -> str:
    """Логин ТЕКУЩЕЙ сессии ('' — вход не выполнялся).

    Самый надёжный источник: он известен с момента входа и не зависит ни от живого
    токена (офлайн его нет), ни от записей на диске (при выходе они стираются).

    Зовёт его страница-передатчик сессии `desktop/local_api.py::install_desktop_bootstrap`
    (`_session_login()`): она выпускает сессию для локального сервера по логину, и на
    пустом логине человек видел бы форму входа внутри уже открытой программы. Прежняя
    ссылка на `ui/vue_shell.py` устарела — того модуля нет с удаления Qt."""
    return getattr(_manager, "_login", "") or ""


def current_auth():
    """(url, token) текущей сессии для админских запросов из UI ('', '' — нет входа)."""
    return _manager.current_auth()


def fresh_auth():
    """(url, token) с живым токеном: протухший обновляется по refresh. Для веб-view."""
    return _manager.fresh_auth()


def push_my_prefs(prefs: dict):
    """Отправить личные настройки (тему оформления) текущего пользователя на сервер.

    Делегируем менеджеру: он знает логин и при неудаче отложит отправку, чтобы тема
    не потерялась для БД и «роуминга». Запрос строго self-scope (/me/prefs)."""
    _manager.push_my_prefs(prefs)
