"""
tts_service.py — серверный синтез речи (Text-to-Speech) для «Вектора»: озвучивает
его ответы на сайте, в мобильном приложении и для десктопа, когда есть интернет.

Движок — Silero TTS (v4_ru, офлайн-нейросеть, русский). Как и STT, синтез — способность
ХОСТА, где крутится сервер: на боевом ПК ВСГУТУ (i3 + RTX 3060) он практически мгновенный.
На слабом демо-VPS (1 CPU / 1 ГБ RAM) torch тяжёл, поэтому:
  • движок грузится ЛЕНИВО (первый запрос) и держится в памяти;
  • готовое аудио КЭШируется по (голос, текст) — ответы Вектора часто повторяются
    (приветствие, «чем помочь», howto), и повтор берётся из кэша за 0 вычислений;
  • длина текста ограничена — защита от разовой тяжёлой нагрузки.
Не поднялся torch / не скачалась модель → синтез отдаёт ok=False, эндпоинт честно
отвечает 503, а веб-клиент откатывается на встроенный speechSynthesis браузера.

Синтез идёт на ХОСТЕ (у сервера уже есть все данные), а звук играет на устройстве самого
запросившего — авторизованного пользователя; наружу, третьим лицам, ничего не уходит.
Текст ответа Вектора МОЖЕТ содержать фамилии (например, список должников) — они
проговариваются тому, кому эти данные и так доступны в журнале.

Два логических голоса продукта → спикеры Silero:
  male   = eugene — натуральнее и живее aidar (тот звучал «роботом»);
  female = baya.
"""
import io
import os
import re
import html
import wave
import hashlib
import threading
from collections import OrderedDict

#Логический голос продукта → спикер Silero v4_ru (в модели их больше, наружу даём два).
#  male = eugene — натуральнее и живее aidar (тот звучал «роботом»);
#  female = baya.
_SPEAKERS = {"male": "eugene", "female": "baya"}
_DEFAULT_VOICE = "male"

#Просодия (SSML) на голос. Темп — medium (fast «тараторил»), тон мужского чуть выше
#(«не грубый», как просил заказчик). Значения rate — ТОЛЬКО из набора Silero
#(x-slow|slow|medium|fast|x-fast), числа он не берёт.
_PROSODY = {
    "male":   {"rate": "medium", "pitch": "high"},
    "female": {"rate": "medium", "pitch": "medium"},
}

#Паузы (SSML <break>) на знаках препинания — иначе Silero проговаривает всё сплошным
#потоком, «не соблюдая точки». Длинная — конец предложения, короткая — запятая/двоеточие.
_BREAK_SENTENCE = '<break time="450ms"/>'
_BREAK_CLAUSE = '<break time="200ms"/>'

#8000/24000/48000 у Silero. 24 кГц — баланс «разборчиво / небольшой payload» (важно,
#канал VPS узкий): моно 16-бит 24 кГц ≈ 48 КБ на секунду речи.
_SAMPLE_RATE = 24000
_MAX_CHARS = 800                 #длиннее Вектор и не отвечает; отсекаем на всякий случай

#Откуда качаем модель Silero один раз (переопределяется env на проде/офлайне).
_MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"

_model = None
_load_lock = threading.Lock()    #сборку модели держим отдельно от кэша (может быть долгой)
_cache_lock = threading.Lock()

#LRU-кэш готового WAV: ключ = (спикер, sha1(текст)). Статичные ответы Вектора повторяются
#дословно — их синтез считается один раз за жизнь процесса.
_CACHE_MAX = 256
_cache = OrderedDict()


def voices() -> dict:
    """Голоса для UI: {ключ: человекочитаемое имя}. Порядок = порядок выбора в настройках."""
    return {"male": "Мужской", "female": "Женский"}


def is_available(engine: str = "silero") -> bool:
    """Установлен ли движок синтеза НА ХОСТЕ. Пока и Silero, и будущий клон-голос на GPU
    зависят от torch+numpy, поэтому проверка общая. По этому флагу веб решает, показывать
    ли озвучку. Неизвестный движок — недоступен."""
    if engine not in _RENDERERS:
        return False
    try:
        import numpy  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def _model_path() -> str:
    """Путь к файлу модели Silero. Кладём в кэш-каталог (env переопределяет для прода)."""
    root = os.environ.get("GRADEBOOK_TTS_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "gradebook-tts")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "v4_ru.pt")


def _ensure_model_file(path: str) -> None:
    """Скачивает модель один раз, если её ещё нет. Пишем во временный файл и атомарно
    переименовываем — прерванная закачка не оставит битый .pt."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    import urllib.request
    tmp = path + ".part"
    #SAST B310: _MODEL_URL — константа модуля (модель Silero).
    urllib.request.urlretrieve(_MODEL_URL, tmp)  # nosec B310
    os.replace(tmp, path)


def _load():
    """Ленивая загрузка модели Silero (torch.package). Держим синглтоном под локом —
    на одноядерном VPS второй параллельной сборки быть не должно."""
    global _model
    with _load_lock:
        if _model is not None:
            return _model
        import torch
        path = _model_path()
        _ensure_model_file(path)
        model = torch.package.PackageImporter(path).load_pickle("tts_models", "model")
        try:
            #Отдаём все ядра синтезу (на VPS их одно, на проде — четыре).
            torch.set_num_threads(max(1, os.cpu_count() or 1))
        except Exception:
            pass
        model.to("cpu")
        _model = model
        return _model


def _ssml(text: str, voice: str) -> str:
    """Оборачивает текст в SSML с просодией голоса и паузами на знаках препинания.

    Порядок важен: СНАЧАЛА экранируем текст (<, >, & в самих словах), ПОТОМ вставляем
    реальные теги <break> — иначе они бы тоже экранировались. Переносы строк схлопываем."""
    pr = _PROSODY.get(voice, _PROSODY[_DEFAULT_VOICE])
    safe = html.escape(re.sub(r"\s+", " ", text).strip(), quote=False)
    #Пауза ПОСЛЕ завершающего знака (перед пробелом), чтобы точка «звучала».
    safe = re.sub(r"([.!?…])(\s)", r"\1" + _BREAK_SENTENCE + r"\2", safe)
    safe = re.sub(r"([,;:—])(\s)", r"\1" + _BREAK_CLAUSE + r"\2", safe)
    return f'<speak><prosody rate="{pr["rate"]}" pitch="{pr["pitch"]}">{safe}</prosody></speak>'


def _to_wav(audio, sample_rate: int) -> bytes:
    """torch float32 [-1, 1] → WAV (int16, моно) в памяти. Через stdlib `wave` — без
    ffmpeg/soundfile, чтобы не тянуть на VPS лишних зависимостей."""
    import numpy as np
    a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
    a = np.clip(a.astype("float32"), -1.0, 1.0)
    pcm = (a * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _render_silero(text: str, voice: str) -> bytes:
    """Silero → WAV-байты. Основной путь — SSML с просодией/паузами, откат — обычный текст."""
    speaker = _SPEAKERS.get(voice, _SPEAKERS[_DEFAULT_VOICE])
    model = _load()
    try:
        audio = model.apply_tts(ssml_text=_ssml(text, voice), speaker=speaker,
                                sample_rate=_SAMPLE_RATE)
    except Exception:
        #SSML не принят (редкий символ/разметка) — синтез без просодии, лишь бы озвучить.
        audio = model.apply_tts(text=text, speaker=speaker, sample_rate=_SAMPLE_RATE,
                                put_accent=True, put_yo=True)
    return _to_wav(audio, _SAMPLE_RATE)


#Реестр движков синтеза. Сейчас один — Silero (CPU-демо на VPS). На боевом GPU-сервере
#сюда добавится КЛОН своего голоса (см. docs/done/TTS-PLAN.md) с той же сигнатурой
#(text, voice) -> wav-байты — synthesize/кэш/эндпоинт при этом менять не придётся.
_RENDERERS = {"silero": _render_silero}
_DEFAULT_ENGINE = "silero"


def synthesize(text: str, voice: str = _DEFAULT_VOICE,
               engine: str = _DEFAULT_ENGINE) -> dict:
    """Синтез речи → {"ok", "audio"(WAV bytes), "mime", "error"}.

    Движок выбирается конфигом (`tts_engine`); неизвестный — откат на дефолт. Сначала
    пробуем кэш (мгновенно, без движка). Любой сбой возвращает ok=False с причиной
    (эндпоинт превратит его в 503, сайт не сломается)."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "audio": b"", "mime": "", "error": "Пустой текст."}
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]
    if engine not in _RENDERERS:
        engine = _DEFAULT_ENGINE
    speaker = _SPEAKERS.get(voice, _SPEAKERS[_DEFAULT_VOICE])
    #Ключ кэша включает движок и голос — разные движки/голоса дают разное аудио.
    key = (engine, speaker, hashlib.sha256(text.encode("utf-8")).hexdigest())

    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)       #освежаем позицию в LRU
            return {"ok": True, "audio": hit, "mime": "audio/wav", "error": ""}

    if not is_available(engine):
        return {"ok": False, "audio": b"", "mime": "",
                "error": "Синтез речи не установлен на сервере."}
    try:
        wav = _RENDERERS[engine](text, voice)
    except Exception as e:
        return {"ok": False, "audio": b"", "mime": "", "error": f"Ошибка синтеза: {e}"}

    with _cache_lock:
        _cache[key] = wav
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)     #вытесняем самый старый
    return {"ok": True, "audio": wav, "mime": "audio/wav", "error": ""}
