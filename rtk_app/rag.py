"""RAG (Retrieval-Augmented Generation) поверх PDF и документов/кода проекта.

Назначение
----------
Дать агенту доступ к БАЗЕ ЗНАНИЙ: PDF-документы И текстовые файлы проекта
(README.md, папка docs/, исходники .py/.js/.css и т.п.) индексируются
(текст -> чанки -> эмбеддинги), а при запросе в чат выполняется ПОИСК по
индексу и найденный контекст ПОДМЕШИВАЕТСЯ в промпт модели (см.
docs/Final_task.md, задача 1: ассистент разработчика и команда /help).

Режим работы — A2 (см. переписку/план): при ВКЛЮЧЁННОМ RAG поиск выполняется
ВСЕГДА, найденный контекст добавляется в системный промпт. Модель не
«решает», искать ли — она просто получает готовый контекст. Это надёжнее
нативного tool-calling (у пользователя только модель эмбеддингов Ollama,
без chat-моделей с поддержкой tools).

Компоненты
----------
  * эмбеддинги  — ЛОКАЛЬНАЯ модель Ollama nomic-embed-text-v2-moe
                   (POST /api/embeddings, dim=768, контекст 512 токенов);
  * извлечение  — PDF: pypdf, ТОЛЬКО текстовый слой (графику игнорируем, OCR
                   не нужен: PDF сконвертированы из Word и имеют текстовый
                   слой); текстовые файлы: чтение UTF-8/cp1251 без страниц
                   (page=0, id вида README.md#chunk.3);
  * хранилище   — SQLite (session/rag_index.db): таблица chunks с текстом
                   и вектором (BLOB float32). JSON — только для настроек
                   и метаданных (session/rag.json).

Данный модуль НЕ зависит от session_store (низкоуровневый), но использует
конфигурацию из rtk_app.config. Потокобезопасность: каждая публичная
функция сама открывает короткую сессию SQLite; запись индекса защищена
блокировкой модуля (индексация выполняется редко и последовательно).
"""
import hashlib
import io
import json
import math
import os
import struct
import threading
import time
import urllib.request

from . import config

__all__ = [
    "RagStore",
    "embed_text",
    "embed_available",
    "format_sources",
    "format_citation_id",
    "tokenize_ru",
    "lexical_score",
    "hybrid_score",
    "extract_pdf_text",
    "extract_text_file",
    "extract_pdf_images",
]

# Блокировка на время (пере)индексации: две параллельные индексации одного
# и того же файла приводили бы к гонке за индекс. Чтение (поиск) не блокируем.
_index_lock = threading.Lock()

# Кэш активной конфигурации эмбеддингов (блок «Модель эмбеддингов»).
# Ключ включает mtime файла настроек и значения config.* — при смене движка
# или monkeypatch в тестах конфигурация перечитывается.
_embed_cfg_lock = threading.Lock()
_embed_cfg_cache = {"key": None, "cfg": None}

# Библиотека pypdf загружается лениво: без установленной библиотеки остальной
# функционал RAG (настройки, статус) должен работать, а индексация — выдать
# понятную ошибку.
_PDF_READER = None
_PDF_IMPORT_ERROR = None


def _load_pdf_reader():
    """Ленивая загрузка PdfReader из pypdf. None, если библиотеки нет."""
    global _PDF_READER, _PDF_IMPORT_ERROR
    if _PDF_READER is not None or _PDF_IMPORT_ERROR is not None:
        return _PDF_READER
    try:
        from pypdf import PdfReader  # noqa: WPS433 (импорт по требованию)
        _PDF_READER = PdfReader
    except Exception as exc:                     # noqa: BLE001
        _PDF_IMPORT_ERROR = str(exc)
        _PDF_READER = None
    return _PDF_READER


# ----------------------------------------------------------------------
# Эмбеддинги (Ollama)
# ----------------------------------------------------------------------
def _clean_text(text):
    """Очищает текст от некорректных Unicode-символов.

    PDF могут содержать бинарные данные, интерпретированные как текст —
    surrogate pairs, непереводимые символы и т.п. Ollama не может их
    обработать и возвращает HTTP 500. Заменяем на U+FFFD.
    """
    text = str(text or "")
    return text.encode("utf-8", errors="replace").decode("utf-8")


def _embed_config():
    """Активная конфигурация эмбеддингов (файл блока -> ENV-фолбэк).

    Кэшируется по mtime файла настроек и значениям config.*: при смене
    движка из UI или monkeypatch в тестах конфигурация перечитывается.
    """
    path = getattr(config, "EMBED_SETTINGS_FILE", "")
    try:
        mtime = (os.path.getmtime(path)
                 if path and os.path.isfile(path) else None)
    except OSError:
        mtime = None
    key = (path, mtime, config.RAG_EMBED_URL, config.RAG_EMBED_MODEL,
           config.RAG_EMBED_DIM, getattr(config, "RAG_EMBED_API", "ollama"),
           getattr(config, "RAG_EMBED_API_KEY", ""))
    with _embed_cfg_lock:
        if _embed_cfg_cache.get("key") == key:
            return dict(_embed_cfg_cache.get("cfg") or {})
    try:
        from . import ollama_server
        cfg = ollama_server.active_embed_config()
    except Exception:                            # noqa: BLE001
        cfg = {"backend": "ollama", "api": "ollama",
               "url": config.RAG_EMBED_URL, "model": config.RAG_EMBED_MODEL,
               "dim": config.RAG_EMBED_DIM,
               "api_key": getattr(config, "RAG_EMBED_API_KEY", "")}
    with _embed_cfg_lock:
        _embed_cfg_cache["key"] = key
        _embed_cfg_cache["cfg"] = dict(cfg)
    return cfg


def _alt_embed_endpoint(url):
    """Парный эндпоинт на случай 404/405 у выбранного протокола."""
    u = str(url or "").rstrip("/")
    pairs = (
        ("/api/embeddings", "/v1/embeddings"),
        ("/v1/embeddings", "/api/embeddings"),
        ("/api/embed", "/v1/embeddings"),
    )
    for a, b in pairs:
        if u.endswith(a):
            return u[:-len(a)] + b
    return ""


def _embed_payload(url, model, text):
    """Тело запроса под выбранный эндпоинт (Ollama или OpenAI-совместимый)."""
    u = str(url or "").rstrip("/")
    if u.endswith("/v1/embeddings"):
        return {"model": model, "input": text}
    if u.endswith("/api/embed"):
        return {"model": model, "input": [text]}
    return {"model": model, "prompt": text}


def _post_embed(url, payload, timeout, api_key=""):
    """POST JSON к серверу эмбеддингов; возвращает разобранный ответ."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_vector(data):
    """Достаёт вектор из ответа Ollama/OpenAI."""
    if not isinstance(data, dict):
        return []
    vec = data.get("embedding")
    if isinstance(vec, list) and vec:
        return vec
    embeddings = data.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        if isinstance(first, list) and first:
            return first
    items = data.get("data")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        vec = items[0].get("embedding")
        if isinstance(vec, list) and vec:
            return vec
    return []


def embed_text(text, model=None, timeout=None):
    """Считает вектор эмбеддинга через активный движок (Ollama/OpenAI API).

    Движок выбирается в блоке «Модель эмбеддингов» (Ollama на ПК/VPS).
    Поддерживаются /api/embeddings, /api/embed и OpenAI-совместимый
    /v1/embeddings. Возвращает список float либо бросает исключение при
    недоступности сервера/модели.
    """
    text = _clean_text(text or "").strip()
    if not text:
        raise ValueError("пустой текст для эмбеддинга")
    cfg = _embed_config()
    if str(cfg.get("backend") or "").lower() == "none":
        raise RuntimeError("эмбеддинги не запущены — запустите модель "
                           "эмбеддингов в блоке RAG")
    url = str(cfg.get("url") or "").strip()
    if not url:
        raise RuntimeError("адрес сервера эмбеддингов не задан")
    use_model = str(model or cfg.get("model")
                    or config.RAG_EMBED_MODEL).strip()
    timeout_val = timeout or config.RAG_EMBED_TIMEOUT
    api_key = str(cfg.get("api_key") or "")
    payload = _embed_payload(url, use_model, text)

    last_error = None
    for attempt in range(3):
        try:
            data = _post_embed(url, payload, timeout_val, api_key=api_key)
            vec = _extract_vector(data)
            if not vec:
                raise RuntimeError("сервер эмбеддингов не вернул вектор")
            return [float(x) for x in vec]
        except urllib.error.HTTPError as exc:
            last_error = exc
            # 404/405 — протокол не поддержан: пробуем парный эндпоинт.
            if exc.code in (404, 405):
                alt = _alt_embed_endpoint(url)
                if alt:
                    try:
                        data = _post_embed(
                            alt, _embed_payload(alt, use_model, text),
                            timeout_val, api_key=api_key)
                        vec = _extract_vector(data)
                        if vec:
                            return [float(x) for x in vec]
                    except Exception as alt_exc:       # noqa: BLE001
                        last_error = alt_exc
                raise exc
            if exc.code == 500 and attempt < 2:
                time.sleep(2)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("эмбеддинг не получен")


def embed_available(timeout=5):
    """Проверяет доступность модели эмбеддингов (без индексации).

    Возвращает dict {ok, model, dim?, backend, url, error?}. Используется
    кнопкой «Проверить» и статусом блока RAG.
    """
    cfg = _embed_config()
    info = {"backend": cfg.get("backend"), "url": cfg.get("url"),
            "model": cfg.get("model")}
    if str(cfg.get("backend") or "").lower() == "none":
        info.update({"ok": False, "error": "эмбеддинги не запущены"})
        return info
    try:
        vec = embed_text("проверка", timeout=timeout)
        info.update({"ok": True, "dim": len(vec), "error": None})
        return info
    except Exception as exc:                     # noqa: BLE001
        info.update({"ok": False, "error": str(exc)})
        return info



# ----------------------------------------------------------------------
# Векторы: упаковка/распаковка и косинусная близость
# ----------------------------------------------------------------------
def _pack_vector(vec):
    """Упаковывает список float в BLOB (float32, little-endian)."""
    return struct.pack("<%df" % len(vec), *vec)


def _unpack_vector(blob, dim):
    """Распаковывает BLOB в список float заданной размерности."""
    if not blob:
        return []
    count = len(blob) // 4
    if dim and count != dim:
        count = min(count, dim)
    return list(struct.unpack("<%df" % count, blob[:count * 4]))


def _under_root(path, root):
    """True, если путь лежит ВНУТРИ root (регистр не важен на Windows).

    Используется для ограничения поиска текущей папкой документов: в одном
    индексе могут остаться чанки других проектов (переиндексация не удаляет
    файлы, которых больше нет в папке), и они не должны попадать в ответ.
    """
    try:
        p = os.path.normcase(os.path.abspath(str(path or "")))
        r = os.path.normcase(os.path.abspath(str(root or "")))
    except (TypeError, ValueError, OSError):
        return True
    if not r:
        return True
    return p == r or p.startswith(r + os.sep)


def _cosine(a, b):
    """Косинусная близость двух векторов (списки float)."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        va = a[i]
        vb = b[i]
        dot += va * vb
        na += va * va
        nb += vb * vb
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ----------------------------------------------------------------------
# Лексический сигнал (гибридный поиск): ключевые слова запроса
# ----------------------------------------------------------------------
# Стоп-слова русского языка + общие служебные слова: не несут смысла для
# поиска и только «размывают» лексический скор.
_STOPWORDS = frozenset((
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а",
    "то", "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же",
    "вы", "за", "бы", "по", "только", "ее", "мне", "было", "вот", "от",
    "меня", "еще", "нет", "о", "из", "ему", "теперь", "когда", "даже",
    "ну", "вдруг", "ли", "если", "уже", "или", "ни", "быть", "был",
    "него", "до", "вас", "нибудь", "опять", "уж", "вам", "ведь", "там",
    "потом", "себя", "ничего", "ей", "может", "они", "тут", "где", "есть",
    "надо", "ней", "для", "мы", "тебя", "их", "чем", "была", "сам", "чтоб",
    "без", "будто", "чего", "раз", "тоже", "себе", "под", "будет", "ж",
    "тогда", "кто", "этот", "того", "потому", "этого", "какой", "совсем",
    "ним", "здесь", "этом", "один", "почти", "мой", "тем", "чтобы", "нее",
    "сейчас", "были", "куда", "зачем", "всех", "никогда", "можно", "при",
    "наконец", "два", "об", "другой", "хоть", "после", "над", "больше",
    "тот", "через", "эти", "нас", "про", "всего", "них", "какая", "много",
    "разве", "три", "эту", "моя", "впрочем", "хорошо", "свою", "этой",
    "перед", "иногда", "лучше", "чуть", "том", "нельзя", "такой", "им",
    "более", "всегда", "конечно", "всю", "между", "это", "будь",
))

# Минимальная длина токена (символы). Короче — отбрасываем (предлоги и т.п.).
_TOKEN_MIN = 3


def tokenize_ru(text):
    """Разбивает текст на значимые токены (лемма-подобно, без морфологии).

    Приводит к нижнему регистру, выкидывает всё кроме букв/цифр, делит по
    не-буквенным символам, отбрасывает стоп-слова и короткие токены.
    Русское слово «обрезается» до первых 5 символов — грубая, но рабочая
    нормализация словоформ (засечка/засечки/засечек -> «засеч»).
    """
    text = str(text or "").lower().replace("ё", "е")
    out = []
    buf = []
    for ch in text:
        if ch.isalpha() or ch.isdigit():
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    res = []
    for tok in out:
        if len(tok) < _TOKEN_MIN:
            continue
        if tok in _STOPWORDS:
            continue
        res.append(tok[:5])
    return res


def lexical_score(query_terms, text):
    """Лексическое совпадение терминов запроса с текстом (0..1).

    Считает долю УНИКАЛЬНЫХ терминов запроса, найденных в токенах текста
    (нормализованных так же, как запрос). 1.0 — все термины есть в тексте.
    Пустой запрос или текст -> 0.0.
    """
    if not query_terms:
        return 0.0
    doc_terms = set(tokenize_ru(text))
    if not doc_terms:
        return 0.0
    uniq = set(query_terms)
    if not uniq:
        return 0.0
    hit = sum(1 for t in uniq if t in doc_terms)
    return hit / float(len(uniq))


def hybrid_score(cosine, lexical, alpha=0.6):
    """Объединяет семантический и лексический скоры в один (0..1).

    alpha — вес косинусной части (0..1); вес лексики = 1 - alpha.
    Оба входа ожидаются в диапазоне 0..1 (косинус может быть отрицательным
    для противоположных векторов — «сжимаем» в 0).
    """
    try:
        c = float(cosine)
    except (TypeError, ValueError):
        c = 0.0
    try:
        lx = float(lexical)
    except (TypeError, ValueError):
        lx = 0.0
    if c < 0.0:
        c = 0.0
    if c > 1.0:
        c = 1.0
    if lx < 0.0:
        lx = 0.0
    if lx > 1.0:
        lx = 1.0
    try:
        a = float(alpha)
    except (TypeError, ValueError):
        a = 0.6
    if a < 0.0:
        a = 0.0
    if a > 1.0:
        a = 1.0
    return a * c + (1.0 - a) * lx


# ----------------------------------------------------------------------
# Извлечение текста из PDF (только текстовый слой)
# ----------------------------------------------------------------------
def extract_pdf_text(path, max_mb=None):
    """Извлекает ТЕКСТОВЫЙ СЛОЙ PDF. Картинки/графику игнорирует.

    Возвращает (pages, error):
      pages — список строк (по странице), может содержать пустые;
      error — строка с причиной, если файл пропущен, иначе None.
    PDF без текстового слоя (все страницы пустые) → error «PDF без
    текстового слоя», OCR НЕ применяется (по требованию задачи).
    """
    PdfReader = _load_pdf_reader()
    if PdfReader is None:
        return [], "нет библиотеки pypdf (%s)" % (_PDF_IMPORT_ERROR or "?")
    limit_mb = max_mb or config.RAG_MAX_PDF_MB
    try:
        size_mb = os.path.getsize(path) / (1024.0 * 1024.0)
    except OSError as exc:
        return [], "не удалось прочитать файл: %s" % exc
    if size_mb > limit_mb:
        return [], "файл больше %d МБ (%.1f МБ) — пропущен" % (limit_mb, size_mb)
    try:
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            try:
                raw = page.extract_text() or ""
                pages.append(_clean_text(raw))
            except Exception:                    # noqa: BLE001
                pages.append("")
    except Exception as exc:                     # noqa: BLE001
        return [], "ошибка чтения PDF: %s" % exc
    if not any(p.strip() for p in pages):
        return [], "PDF без текстового слоя"
    return pages, None


def extract_text_file(path, max_mb=None):
    """Читает ТЕКСТОВЫЙ файл проекта (README, docs/*.md, исходники).

    Возвращает (text, error):
      text  — содержимое файла (строка), либо "";
      error — причина пропуска (нет файла, слишком большой, не читается).

    Кодировка: UTF-8, при ошибке — cp1251 (типичные файлы Windows),
    затем latin-1 как последний резерв. Номеров страниц у текстовых
    файлов нет: в индекс они попадают с page=0.
    """
    limit_mb = max_mb or getattr(config, "RAG_DOC_MAX_MB", 2)
    try:
        size_mb = os.path.getsize(path) / (1024.0 * 1024.0)
    except OSError as exc:
        return "", "не удалось прочитать файл: %s" % exc
    if size_mb > limit_mb:
        return "", "файл больше %d МБ (%.1f МБ) — пропущен" % (
            limit_mb, size_mb)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return "", "не удалось прочитать файл: %s" % exc
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:                                        # pragma: no cover
        return "", "не удалось определить кодировку"
    text = _clean_text(text)
    if not text.strip():
        return "", "пустой файл"
    return text, None


def is_pdf(path):
    """True, если файл индексируется как PDF (текстовый слой + картинки)."""
    return str(path or "").lower().endswith(".pdf")


def doc_extensions():
    """Расширения индексируемых файлов (из конфига, с ведущей точкой)."""
    exts = getattr(config, "RAG_DOC_EXTS", None) or (".pdf",)
    out = []
    for e in exts:
        e = str(e or "").strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        out.append(e)
    return tuple(out)


# ----------------------------------------------------------------------
# Извлечение ВСТРОЕННЫХ изображений из PDF (pypdf page.images + Pillow)
# ----------------------------------------------------------------------
# Pillow загружается лениво: без неё текстовый RAG продолжает работать,
# а извлечение изображений просто ничего не вернёт.
_PILLOW_IMAGE = None
_PILLOW_IMPORT_ERROR = None


def _load_pillow_image():
    """Ленивая загрузка PIL.Image. None, если библиотеки нет."""
    global _PILLOW_IMAGE, _PILLOW_IMPORT_ERROR
    if _PILLOW_IMAGE is not None or _PILLOW_IMPORT_ERROR is not None:
        return _PILLOW_IMAGE
    try:
        from PIL import Image  # noqa: WPS433 (импорт по требованию)
        _PILLOW_IMAGE = Image
    except Exception as exc:                     # noqa: BLE001
        _PILLOW_IMPORT_ERROR = str(exc)
        _PILLOW_IMAGE = None
    return _PILLOW_IMAGE


def _is_significant_image(width, height):
    """Отсекает ЛОГОТИПЫ/ИКОНКИ/декоративные элементы по размеру.

    Картинка считается «значащей», если её меньшая сторона не ниже
    RAG_IMAGE_MIN_DIM пикселей. Это убирает мелкие вставки (иконки,
    значки, угловые логотипы), оставляя схемы/рисунки/фотографии.
    """
    try:
        w = int(width or 0)
        h = int(height or 0)
    except (TypeError, ValueError):
        return False
    if w <= 0 or h <= 0:
        return False
    min_dim = int(getattr(config, "RAG_IMAGE_MIN_DIM", 200) or 200)
    return min(w, h) >= min_dim


def extract_pdf_images(path, max_mb=None):
    """Извлекает ВСТРОЕННЫЕ изображения PDF (без рендера страниц).

    Использует pypdf (page.images) и Pillow для нормализации в PNG/JPEG.
    Возвращает (images, error): images — список словарей
    {page, data, ext, width, height, sha}, где:
      page   — номер страницы (1-based);
      data   — байты изображения в выбранном формате;
      ext    — расширение ("png"/"jpg");
      width/height — размеры в пикселях;
      sha    — SHA-256 содержимого (дедупликация одинаковых вставок).
    ЛОГОТИПЫ/ИКОНКИ (меньше RAG_IMAGE_MIN_DIM по меньшей стороне) и
    изображения крупнее RAG_IMAGE_MAX_MB отбрасываются. error — строка
    при невозможности извлечения (нет pypdf/Pillow, ошибка чтения),
    иначе None. Отсутствие картинок — НЕ ошибка (пустой список, None).
    """
    if not getattr(config, "RAG_IMAGES_ENABLED", True):
        return [], None
    PdfReader = _load_pdf_reader()
    if PdfReader is None:
        return [], "нет библиотеки pypdf (%s)" % (_PDF_IMPORT_ERROR or "?")
    Image = _load_pillow_image()
    if Image is None:
        return [], "нет библиотеки Pillow (%s)" % (
            _PILLOW_IMPORT_ERROR or "?")
    limit_mb = max_mb or config.RAG_MAX_PDF_MB
    try:
        size_mb = os.path.getsize(path) / (1024.0 * 1024.0)
    except OSError as exc:
        return [], "не удалось прочитать файл: %s" % exc
    if size_mb > limit_mb:
        return [], None
    fmt = str(getattr(config, "RAG_IMAGE_FORMAT", "png") or "png").lower()
    if fmt == "jpg":
        fmt = "jpeg"
    ext = "jpg" if fmt == "jpeg" else "png"
    max_bytes = int(getattr(config, "RAG_IMAGE_MAX_MB", 8) or 8) \
        * 1024 * 1024
    per_doc = int(getattr(config, "RAG_IMAGE_MAX_PER_DOC", 40) or 40)
    seen = set()
    out = []
    try:
        reader = PdfReader(path)
        for pi, page in enumerate(reader.pages):
            try:
                page_imgs = list(page.images)
            except Exception:                    # noqa: BLE001
                continue
            for img in page_imgs:
                if len(out) >= per_doc:
                    return out, None
                try:
                    pil = img.image
                except Exception:                # noqa: BLE001
                    continue
                try:
                    width, height = pil.size
                except Exception:                # noqa: BLE001
                    continue
                if not _is_significant_image(width, height):
                    continue
                buf = io.BytesIO()
                try:
                    if fmt == "jpeg":
                        if pil.mode not in ("RGB", "L"):
                            pil = pil.convert("RGB")
                        pil.save(buf, format="JPEG", quality=85)
                    else:
                        if pil.mode not in ("RGB", "RGBA", "L"):
                            pil = pil.convert("RGB")
                        pil.save(buf, format="PNG")
                except Exception:                # noqa: BLE001
                    continue
                data = buf.getvalue()
                if not data or len(data) > max_bytes:
                    continue
                sha = hashlib.sha256(data).hexdigest()
                if sha in seen:
                    continue
                seen.add(sha)
                out.append({"page": pi + 1, "data": data, "ext": ext,
                            "width": int(width), "height": int(height),
                            "sha": sha})
        return out, None
    except Exception as exc:                     # noqa: BLE001
        return [], "ошибка чтения изображений PDF: %s" % exc


def _doc_hash(path):
    """Хэш документа для имени подпапки изображений (по пути)."""
    key = os.path.abspath(str(path or "")).encode("utf-8", "replace")
    return hashlib.sha256(key).hexdigest()[:16]


# ----------------------------------------------------------------------
# Нарезка текста на чанки
# ----------------------------------------------------------------------
def chunk_text(text, size=None, overlap=None):
    """Режет текст на чанки с перекрытием (для индексации).

    Размер чанка заведомо меньше контекста модели эмбеддингов (512 токенов):
    RAG_CHUNK_CHARS символов укладываются в окно с запасом для русского.
    Перекрытие сохраняет смысл на границах чанков.
    """
    text = str(text or "")
    size = int(size or config.RAG_CHUNK_CHARS)
    overlap = int(overlap if overlap is not None else config.RAG_CHUNK_OVERLAP)
    if size <= 0:
        size = config.RAG_CHUNK_CHARS
    if overlap < 0:
        overlap = 0
    if overlap >= size:
        overlap = max(0, size // 4)
    chunks = []
    n = len(text)
    start = 0
    while start < n:
        end = start + size
        piece = text[start:end]
        if piece.strip():
            chunks.append(piece)
        if end >= n:
            break
        start = end - overlap
    return chunks


# ----------------------------------------------------------------------
# Хранилище индекса (SQLite)
# ----------------------------------------------------------------------
class RagStore(object):
    """Индекс чанков и векторов в SQLite + настройки RAG в JSON.

    Схема БД:
      chunks(id INTEGER PK, file TEXT, page INTEGER, chunk INTEGER,
             text TEXT, vec BLOB)
      files(path TEXT PK, mtime REAL, size INTEGER, chunks INTEGER,
            indexed REAL, error TEXT)
    Векторы — BLOB float32 (dim = config.RAG_EMBED_DIM). Поиск — косинусный
    перебор (десятки тысяч чанков: приемлемо, без внешних ANN-библиотек).
    """

    def __init__(self, index_file=None, settings_file=None):
        self.index_file = index_file or config.RAG_INDEX_FILE
        self.settings_file = settings_file or config.RAG_SETTINGS_FILE
        self._local = threading.local()
        self._ensure_dir()

    # ---- низкоуровневый доступ к SQLite ----
    def _ensure_dir(self):
        for p in (self.index_file, self.settings_file):
            d = os.path.dirname(p)
            if d and not os.path.isdir(d):
                try:
                    os.makedirs(d, exist_ok=True)
                except OSError:
                    pass

    def _conn(self):
        """Соединение SQLite для текущего потока (ленивое)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            import sqlite3
            conn = sqlite3.connect(self.index_file, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            self._init_schema(conn)
            self._local.conn = conn
        return conn

    @staticmethod
    def _init_schema(conn):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "file TEXT NOT NULL,"
            "page INTEGER NOT NULL DEFAULT 0,"
            "chunk INTEGER NOT NULL DEFAULT 0,"
            "text TEXT NOT NULL,"
            "vec BLOB)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS files ("
            "path TEXT PRIMARY KEY,"
            "mtime REAL,"
            "size INTEGER,"
            "chunks INTEGER,"
            "indexed REAL,"
            "error TEXT)"
        )
        # Изображения страниц (встроенные картинки PDF) — для вставки
        # «фрагмента документа» в карточку ответа. rel_path — путь к файлу
        # изображения ОТНОСИТЕЛЬНО RAG_IMAGES_DIR (для безопасной отдачи).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS images ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "file TEXT NOT NULL,"
            "page INTEGER NOT NULL DEFAULT 0,"
            "sha TEXT NOT NULL,"
            "rel_path TEXT NOT NULL,"
            "width INTEGER NOT NULL DEFAULT 0,"
            "height INTEGER NOT NULL DEFAULT 0,"
            "size INTEGER NOT NULL DEFAULT 0,"
            "ext TEXT NOT NULL DEFAULT 'png')"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_file ON images(file)")
        # Метаданные индекса: какой моделью/размерностью он собран.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta ("
            "key TEXT PRIMARY KEY,"
            "value TEXT)")
        conn.commit()

    # ---- метаданные индекса ----
    @staticmethod
    def _meta_set(conn, key, value):
        """Записывает метаданные индекса (key/value)."""
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     (str(key), "" if value is None else str(value)))

    @staticmethod
    def _meta_get(conn, key):
        """Читает метаданные индекса (None, если нет)."""
        try:
            row = conn.execute("SELECT value FROM meta WHERE key=?",
                               (str(key),)).fetchone()
            return row[0] if row else None
        except Exception:                          # noqa: BLE001
            return None

    def _index_dim(self):
        """Размерность векторов ДЕЙСТВУЮЩЕГО индекса (meta -> активная)."""
        try:
            value = self._meta_get(self._conn(), "embed_dim")
            if value:
                return max(1, int(value))
        except (TypeError, ValueError):
            pass
        return int(_embed_config().get("dim") or config.RAG_EMBED_DIM)

    # ---- настройки (JSON) ----
    def load_settings(self):
        """Читает настройки RAG из JSON. Гарантирует все ключи."""
        defaults = {
            "enabled": bool(config.RAG_ENABLED),
            "docs_dir": config.RAG_DOCS_DIR,
            "top_k": int(config.RAG_TOP_K),
            "embed_model": config.RAG_EMBED_MODEL,
            "chunk_chars": int(config.RAG_CHUNK_CHARS),
            # Улучшенный поиск (2-й этап): фильтрация/реранкинг + rewrite.
            "rewrite": bool(config.RAG_REWRITE),
            "hybrid": bool(config.RAG_HYBRID),
            "hybrid_alpha": float(config.RAG_HYBRID_ALPHA),
            "pool_mult": int(config.RAG_POOL_MULT),
            "min_score": float(config.RAG_MIN_SCORE),
            "rerank": str(config.RAG_RERANK),
            "updated": None,
        }
        data = {}
        try:
            with open(self.settings_file, "r", encoding="utf-8") as fh:
                data = json.load(fh) or {}
        except Exception:                        # noqa: BLE001
            data = {}
        if not isinstance(data, dict):
            data = {}
        out = dict(defaults)
        if "enabled" in data:
            out["enabled"] = bool(data.get("enabled"))
        if isinstance(data.get("docs_dir"), str) and data["docs_dir"].strip():
            out["docs_dir"] = data["docs_dir"].strip()
        try:
            k = int(data.get("top_k"))
            out["top_k"] = max(1, min(20, k))
        except (TypeError, ValueError):
            pass
        # Улучшенный поиск: булевы/числовые настройки 2-го этапа.
        if "rewrite" in data:
            out["rewrite"] = bool(data.get("rewrite"))
        if "hybrid" in data:
            out["hybrid"] = bool(data.get("hybrid"))
        try:
            a = float(data.get("hybrid_alpha"))
            out["hybrid_alpha"] = max(0.0, min(1.0, a))
        except (TypeError, ValueError):
            pass
        try:
            pm = int(data.get("pool_mult"))
            out["pool_mult"] = max(1, min(50, pm))
        except (TypeError, ValueError):
            pass
        try:
            ms = float(data.get("min_score"))
            out["min_score"] = max(0.0, min(1.0, ms))
        except (TypeError, ValueError):
            pass
        if isinstance(data.get("rerank"), str) and data["rerank"] in ("none", "llm"):
            out["rerank"] = data["rerank"]
        if data.get("updated"):
            out["updated"] = data["updated"]
        return out

    def save_settings(self, settings):
        """Сохраняет настройки RAG в JSON (atomic-запись)."""
        try:
            with open(self.settings_file, "w", encoding="utf-8") as fh:
                json.dump(settings, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def update_settings(self, enabled=None, docs_dir=None, top_k=None,
                        rewrite=None, hybrid=None, hybrid_alpha=None,
                        pool_mult=None, min_score=None, rerank=None):
        """Точечно меняет настройки (None — не трогать). Возвращает снимок."""
        s = self.load_settings()
        if enabled is not None:
            s["enabled"] = bool(enabled)
        if docs_dir is not None and str(docs_dir).strip():
            s["docs_dir"] = str(docs_dir).strip()
        if top_k is not None:
            try:
                s["top_k"] = max(1, min(20, int(top_k)))
            except (TypeError, ValueError):
                pass
        if rewrite is not None:
            s["rewrite"] = bool(rewrite)
        if hybrid is not None:
            s["hybrid"] = bool(hybrid)
        if hybrid_alpha is not None:
            try:
                s["hybrid_alpha"] = max(0.0, min(1.0, float(hybrid_alpha)))
            except (TypeError, ValueError):
                pass
        if pool_mult is not None:
            try:
                s["pool_mult"] = max(1, min(50, int(pool_mult)))
            except (TypeError, ValueError):
                pass
        if min_score is not None:
            try:
                s["min_score"] = max(0.0, min(1.0, float(min_score)))
            except (TypeError, ValueError):
                pass
        if rerank is not None and str(rerank) in ("none", "llm"):
            s["rerank"] = str(rerank)
        # ВАЖНО: модель эмбеддингов больше НЕ берётся из config — она хранится
        # в отдельном файле блока «Модель эмбеддингов» (см. ollama_server).
        # Иначе смена движка затиралась бы при каждом изменении настроек RAG.
        s["chunk_chars"] = int(config.RAG_CHUNK_CHARS)
        self.save_settings(s)
        return s

    # ---- (пере)индексация ----
    def _doc_files(self, docs_dir):
        """Список индексируемых файлов в папке (рекурсивно), отсортированный.

        Берутся PDF и текстовые файлы/код из config.RAG_DOC_EXTS.
        Служебные папки (config.RAG_DOC_SKIP_DIRS: .git, session,
        __pycache__, node_modules и т.п.) и любые скрытые папки пропускаются.
        """
        out = []
        if not docs_dir or not os.path.isdir(docs_dir):
            return out
        exts = doc_extensions()
        skip = set(str(d).lower()
                   for d in getattr(config, "RAG_DOC_SKIP_DIRS", ()))
        skip_files = set(str(f).lower()
                         for f in getattr(config, "RAG_DOC_SKIP_FILES", ()))
        for root, dirs, names in os.walk(docs_dir):
            dirs[:] = sorted(d for d in dirs
                             if d.lower() not in skip and not d.startswith("."))
            for name in sorted(names):
                if name.lower() in skip_files:
                    continue
                if name.lower().endswith(exts):
                    out.append(os.path.join(root, name))
        out.sort()
        return out

    def _pdf_files(self, docs_dir):
        """Список только PDF-файлов (совместимость; см. _doc_files)."""
        return [p for p in self._doc_files(docs_dir) if is_pdf(p)]

    def index_docs(self, docs_dir=None, verbose=True):
        """Полная (пере)индексация папки с документами, кодом и PDF.

        Перебирает файлы из config.RAG_DOC_EXTS (PDF — текстовый слой и
        картинки; .md/.txt/.py/.js/... — как текст), для неизменённых
        (mtime+size) — пропускает, для остальных: текст -> чанки ->
        эмбеддинги -> SQLite. Возвращает отчёт {ok, files, chunks, skipped,
        errors}.
        """
        if not _index_lock.acquire(blocking=False):
            return {"ok": False, "error": "индексация уже выполняется"}
        try:
            settings = self.load_settings()
            docs_dir = docs_dir or settings.get("docs_dir") or config.RAG_DOCS_DIR
            if not os.path.isdir(docs_dir):
                return {"ok": False,
                        "error": "папка не найдена: %s" % docs_dir}
            conn = self._conn()
            files = self._doc_files(docs_dir)
            embed_cfg = _embed_config()
            current_model = str(embed_cfg.get("model") or "")
            current_dim = int(embed_cfg.get("dim")
                              or config.RAG_EMBED_DIM)
            # Если индекс собран ДРУГОЙ моделью/размерностью — переиндексируем
            # всё принудительно (mtime/size не помогут: текст тот же, а векторы
            # несовместимы).
            meta_model = (self._meta_get(conn, "embed_model") or "").strip()
            meta_dim_raw = self._meta_get(conn, "embed_dim")
            force_reindex = bool(
                meta_model and current_model
                and meta_model.split(":", 1)[0]
                != current_model.split(":", 1)[0])
            if meta_dim_raw:
                try:
                    force_reindex = (force_reindex
                                     or int(meta_dim_raw) != current_dim)
                except (TypeError, ValueError):
                    pass
            report = {"ok": True, "docs_dir": docs_dir, "files": 0,
                      "chunks": 0, "skipped": 0, "unchanged": 0,
                      "images": 0, "errors": [],
                      "embed_model": embed_cfg.get("model"),
                      "embed_backend": embed_cfg.get("backend"),
                      "embed_dim": current_dim,
                      "forced_reindex": force_reindex}
            if not files:
                return report
            vec_dim = 0
            rewritten = 0
            for path in files:
                try:
                    mtime = os.path.getmtime(path)
                    size = os.path.getsize(path)
                except OSError:
                    report["skipped"] += 1
                    continue
                row = conn.execute(
                    "SELECT mtime, size, chunks FROM files WHERE path=?",
                    (path,)).fetchone()
                if row and not force_reindex \
                        and abs(row[0] - mtime) < 1e-6 and row[1] == size \
                        and not row[2] is None:
                    report["unchanged"] += 1
                    report["chunks"] += int(row[2] or 0)
                    report["files"] += 1
                    # Текст неизменен, но изображения могли появиться
                    # ПОСЛЕ первой индексации (функция добавлена позже)
                    # или прежняя индексация прошла без извлечения.
                    # Дозаполняем таблицу images, если для файла пусто.
                    # Картинки есть только у PDF: текстовые файлы пропускаем.
                    if is_pdf(path):
                        try:
                            have = conn.execute(
                                "SELECT COUNT(*) FROM images WHERE file=?",
                                (path,)).fetchone()
                            if not (have and int(have[0] or 0)):
                                report["images"] += self._store_images(conn,
                                                                       path)
                        except Exception as exc:             # noqa: BLE001
                            report["errors"].append(
                                "%s: изображения: %s"
                                % (os.path.basename(path), exc))
                    continue
                pdf = is_pdf(path)
                if pdf:
                    pages, err = extract_pdf_text(path)
                    first_page = 1
                else:
                    text, err = extract_text_file(path)
                    pages = [text] if text else []
                    first_page = 0
                if err:
                    # Помечаем файл с ошибкой, старые чанки удаляем.
                    report["errors"].append("%s: %s"
                                            % (os.path.basename(path), err))
                    self._replace_file(conn, path, mtime, size, [], 0, err)
                    # Изображения могли извлечься даже без текста (PDF).
                    if pdf:
                        self._store_images(conn, path)
                    report["skipped"] += 1
                    continue
                chunks = self._file_chunks(pages, first_page=first_page)
                if not chunks:
                    report["errors"].append(
                        "%s: нет текста" % os.path.basename(path))
                    self._replace_file(conn, path, mtime, size, [], 0,
                                       "нет текста")
                    if pdf:
                        self._store_images(conn, path)
                    report["skipped"] += 1
                    continue
                vectors = []
                embed_failed = None
                for ci, ch in enumerate(chunks):
                    try:
                        vectors.append(embed_text(ch["text"]))
                        if ci > 0 and ci % 10 == 0:
                            time.sleep(1)
                    except Exception as exc:     # noqa: BLE001
                        embed_failed = str(exc)
                        break
                if embed_failed:
                    report["ok"] = False
                    report["errors"].append(
                        "%s: эмбеддинги недоступны (%s)"
                        % (os.path.basename(path), embed_failed))
                    break
                self._replace_file(conn, path, mtime, size, chunks, vectors,
                                   None)
                rewritten += 1
                if vectors and not vec_dim:
                    vec_dim = len(vectors[0])
                if pdf:
                    report["images"] += self._store_images(conn, path)
                report["files"] += 1
                report["chunks"] += len(chunks)
                if verbose:
                    print("[RAG] проиндексирован %s: %d чанков"
                          % (os.path.basename(path), len(chunks)), flush=True)
            settings["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.save_settings(settings)
            # Фиксируем модель/размерность ТОЛЬКО если реально перезаписали
            # векторы: иначе при смене модели и неизменных файлах метаданные
            # соврали бы, что индекс пересобран.
            if report.get("ok") and rewritten:
                dim = int(vec_dim or report.get("embed_dim")
                          or config.RAG_EMBED_DIM)
                report["embed_dim"] = dim
                self._meta_set(conn, "embed_model",
                               report.get("embed_model") or "")
                self._meta_set(conn, "embed_dim", dim)
                self._meta_set(conn, "embed_backend",
                               report.get("embed_backend") or "")
                self._meta_set(conn, "indexed_at", settings["updated"])
                conn.commit()
            return report
        finally:
            _index_lock.release()

    @staticmethod
    def _file_chunks(pages, first_page=1):
        """Превращает страницы документа в список чанков.

        pages      — список текстов (для PDF — по страницам, для текстовых
                     файлов — один элемент со всем содержимым);
        first_page — номер первой страницы: 1 для PDF, 0 для текстовых
                     файлов (у них страниц нет, id будет без «стр.N»).
        """
        out = []
        for pi, page_text in enumerate(pages):
            for ci, piece in enumerate(chunk_text(page_text)):
                out.append({"page": pi + first_page,
                            "chunk": ci + 1, "text": piece})
        return out

    @staticmethod
    def _replace_file(conn, path, mtime, size, chunks, vectors, error):
        """Заменяет все чанки файла и его запись в files (в транзакции)."""
        conn.execute("DELETE FROM chunks WHERE file=?", (path,))
        if chunks and vectors and len(chunks) == len(vectors):
            for ch, vec in zip(chunks, vectors):
                conn.execute(
                    "INSERT INTO chunks(file, page, chunk, text, vec) "
                    "VALUES (?,?,?,?,?)",
                    (path, ch["page"], ch["chunk"], ch["text"],
                     _pack_vector(vec)))
        conn.execute(
            "INSERT OR REPLACE INTO files(path, mtime, size, chunks, indexed, "
            "error) VALUES (?,?,?,?,?,?)",
            (path, mtime, size, len(chunks) if not error else 0,
             time.time(), error))
        conn.commit()

    # ---- изображения документов ----
    def _images_dir(self):
        d = getattr(config, "RAG_IMAGES_DIR", None)
        return d or os.path.join(config.SESSION_DIR, "rag_images")

    def _store_images(self, conn, path):
        """Извлекает и сохраняет изображения файла. Идемпотентно.

        Возвращает число сохранённых изображений (0 при отключённом
        режиме или ошибке). Файлы складываются в <RAG_IMAGES_DIR>/
        <doc_hash>/, в таблицу images пишутся относительные пути.
        Старые записи файла удаляются перед вставкой новых.
        """
        conn.execute("DELETE FROM images WHERE file=?", (path,))
        if not getattr(config, "RAG_IMAGES_ENABLED", True):
            conn.commit()
            return 0
        try:
            images, _err = extract_pdf_images(path)
        except Exception:                        # noqa: BLE001
            images = []
        if not images:
            conn.commit()
            return 0
        doc_dir = _doc_hash(path)
        base_dir = os.path.join(self._images_dir(), doc_dir)
        saved = 0
        try:
            os.makedirs(base_dir, exist_ok=True)
        except OSError:
            conn.commit()
            return 0
        for im in images:
            name = "%s.%s" % (im["sha"][:24], im["ext"])
            rel = os.path.join(doc_dir, name)
            full = os.path.join(self._images_dir(), rel)
            try:
                if not os.path.isfile(full):
                    with open(full, "wb") as fh:
                        fh.write(im["data"])
            except OSError:
                continue
            conn.execute(
                "INSERT INTO images(file, page, sha, rel_path, width, "
                "height, size, ext) VALUES (?,?,?,?,?,?,?,?)",
                (path, im["page"], im["sha"], rel.replace(os.sep, "/"),
                 im["width"], im["height"], len(im["data"]), im["ext"]))
            saved += 1
        conn.commit()
        return saved

    def images_for(self, file, page=None):
        """Изображения документа (опционально — конкретной страницы).

        file — имя PDF (basename) как в hits/источниках, либо полный
        путь. Возвращает список {page, url, width, height}, где url —
        относительный URL для отдачи сервером (/images/<rel_path>).
        """
        base = os.path.basename(str(file or "").strip())
        if not base:
            return []
        conn = self._conn()
                # file в индексе — полный путь; сопоставляем по basename.
        sql = ("SELECT page, rel_path, width, height FROM images "
               "WHERE (file=? OR file LIKE ? OR file LIKE ?)")
        params = [base, "%" + os.sep + base, "%/" + base]
        if page is not None:
            try:
                pg = int(page)
            except (TypeError, ValueError):
                pg = None
            if pg:
                sql += " AND page=?"
                params.append(pg)
        sql += " ORDER BY page, id"
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        except Exception:                        # noqa: BLE001
            return []
        out = []
        for pg, rel, w, h in rows:
            if not rel:
                continue
            out.append({"page": int(pg or 0),
                        "url": "/images/%s" % str(rel).lstrip("/"),
                        "width": int(w or 0), "height": int(h or 0)})
        return out

    def image_abs_path(self, rel):
        """Абсолютный путь к файлу изображения (или None).

        Защита от выхода за пределы RAG_IMAGES_DIR (path traversal).
        """
        rel = str(rel or "").strip().lstrip("/").replace("/", os.sep)
        if not rel:
            return None
        root = os.path.realpath(self._images_dir())
        full = os.path.realpath(os.path.join(root, os.path.normpath(rel)))
        if not (full.startswith(root + os.sep) or full == root):
            return None
        if not os.path.isfile(full):
            return None
        return full

    def fragments(self, hits, max_frags=None):
        """Фрагменты документа для карточки ответа: текст + картинки.

        По каждому найденному фрагменту (hit из search/build_context)
        возвращает dict:
            {text, file, doc, page, chunk, score,
             images: [{page, url, width, height}, …]}
        images — изображения СООТВЕТСТВУЮЩЕЙ страницы документа (если
        при индексации из PDF извлекались картинки; иначе []) — это и
        есть «фрагмент документа: текст + картинка». Порядок — как в
        hits (по релевантности). Ограничение max_frags — защита от
        перегрузки карточки.
        """
        limit = int(getattr(config, "RAG_FRAGMENTS_MAX", 0) or 0)
        if max_frags:
            limit = int(max_frags)
        out = []
        for h in hits or []:
            if not isinstance(h, dict):
                continue
            full = str(h.get("file") or "").strip()
            base = os.path.basename(full)
            try:
                page = int(h.get("page") or 0)
            except (TypeError, ValueError):
                page = 0
            try:
                chunk = int(h.get("chunk") or 0)
            except (TypeError, ValueError):
                chunk = 0
            try:
                score = float(h.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            text = str(h.get("text") or "")
            images = self.images_for(base, page=page) if base else []
            out.append({
                "text": text,
                "file": base,
                "doc": os.path.splitext(base)[0],
                "page": page,
                "chunk": chunk,
                "score": score,
                "images": images,
            })
            if limit and len(out) >= limit:
                break
        return out

    # ---- очистка ----
    def clear(self):
        """Удаляет все чанки, файлы и метаданные индекса."""
        conn = self._conn()
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM files")
        try:
            conn.execute("DELETE FROM images")
        except Exception:                        # noqa: BLE001
            pass
        try:
            conn.execute("DELETE FROM meta")
        except Exception:                        # noqa: BLE001
            pass
        conn.commit()
        # Удаляем файлы изображений (вся папка хранилища).
        try:
            import shutil
            root = self._images_dir()
            if root and os.path.isdir(root):
                shutil.rmtree(root, ignore_errors=True)
        except Exception:                        # noqa: BLE001
            pass
        s = self.load_settings()
        s["updated"] = None
        self.save_settings(s)
        return {"ok": True}

    # ---- поиск ----
    def _score_rows(self, rows, queries, settings, pool):
        """Считает гибридные скоры чанков по НАБОРУ запросов.

        rows    — строки индекса (file, page, chunk, text, vec);
        queries — список формулировок запроса (векторизуются отдельно);
        pool    — сколько лучших вернуть ДО порога/реранка.

        Для каждого чанка берёт ЛУЧШИЙ скор по всем формулировкам
        (max), гибридизуя косинус с лексикой, если включён гибридный режим.
        Возвращает список словарей hit, отсортированных по убыванию score.
        """
        hybrid = bool(settings.get("hybrid", config.RAG_HYBRID))
        alpha = float(settings.get("hybrid_alpha", config.RAG_HYBRID_ALPHA))
        # Термы запроса для лексики — объединяем все формулировки.
        q_terms = []
        for q in queries:
            q_terms.extend(tokenize_ru(q))
        # Векторизуем запросы (каждый отдельно).
        q_vecs = []
        for q in queries:
            try:
                q_vecs.append(embed_text(q))
            except Exception:                    # noqa: BLE001
                continue
        if not q_vecs:
            return []
        scored = []
        index_dim = self._index_dim()
        for file, page, chunk, text, vec in rows:
            v = _unpack_vector(vec, index_dim)
            if not v:
                continue
            cos = max((_cosine(qv, v) for qv in q_vecs), default=0.0)
            if hybrid:
                lex = lexical_score(q_terms, text)
                score = hybrid_score(cos, lex, alpha)
            else:
                lex = 0.0
                score = max(0.0, cos)
            scored.append({"file": file, "page": page, "chunk": chunk,
                           "text": text, "score": score, "cosine": cos,
                           "lexical": lex})
        scored.sort(key=lambda h: h["score"], reverse=True)
        for h in scored:
            h["score"] = round(h["score"], 4)
            h["cosine"] = round(h["cosine"], 4)
            h["lexical"] = round(h["lexical"], 4)
        return scored[:max(1, pool)]

    def _embed_mismatch(self):
        """Сверяет модель/размерность индекса с активным движком.

        Возвращает {mismatch, index_model, index_dim, current_model,
        current_dim}. Индекс без метаданных (старый) считается совместимым.
        """
        cfg = _embed_config()
        try:
            conn = self._conn()
            idx_model = (self._meta_get(conn, "embed_model") or "").strip()
            raw_dim = self._meta_get(conn, "embed_dim")
            idx_dim = int(raw_dim) if raw_dim else 0
        except Exception:                        # noqa: BLE001
            idx_model, idx_dim = "", 0
        cur_model = str(cfg.get("model") or "")
        cur_dim = int(cfg.get("dim") or 0)
        # Тег можно опустить: nomic-embed-text-v2-moe == ...:latest.
        def _base(name):
            return str(name or "").split(":", 1)[0]
        mismatch = (bool(idx_model and cur_model
                         and _base(idx_model) != _base(cur_model))
                    or bool(idx_dim and cur_dim and idx_dim != cur_dim))
        return {"mismatch": bool(mismatch), "index_model": idx_model,
                "index_dim": idx_dim, "current_model": cur_model,
                "current_dim": cur_dim}

    def search(self, query, top_k=None, docs_dir=None):
        """Ищет top-k фрагментов к запросу с гибридным скором.

        Выполняет ПЕРВЫЙ этап (векторный + лексический поиск, пул кандидатов),
        затем ВТОРОЙ этап: фильтрация по порогу min_score и возврат top-k.
        Возвращает список {file, page, chunk, text, score, cosine, lexical}.
        Пустой запрос/индекс → []. Ошибки эмбеддингов пробрасываются.
        """
        return self.search_multi([query], top_k=top_k, docs_dir=docs_dir)

    def search_multi(self, queries, top_k=None, docs_dir=None):
        """Ищет по НАБОРУ формулировок (query rewrite + гибрид + порог).

        queries — список строк (оригинал + переформулировки от LLM). Пустые — отбрасываются.
        Скор чанка — лучший по всем формулировкам. Кандидаты ниже
        min_score отбрасываются, остальные сортируются и режутся по top_k.

        docs_dir — ОГРАНИЧЕНИЕ ОБЛАСТИ ПОИСКА: если задан, учитываются только
        чанки файлов внутри этой папки. Без него ищем по всему индексу.
        """
        qs = [str(q).strip() for q in (queries or []) if str(q).strip()]
        if not qs:
            return []
        settings = self.load_settings()
        k = int(top_k or settings.get("top_k") or config.RAG_TOP_K)
        pool_mult = int(settings.get("pool_mult", config.RAG_POOL_MULT))
        min_score = float(settings.get("min_score", config.RAG_MIN_SCORE))
        conn = self._conn()
        rows = conn.execute(
            "SELECT file, page, chunk, text, vec FROM chunks").fetchall()
        if docs_dir and str(docs_dir).strip():
            rows = [r for r in rows if _under_root(r[0], docs_dir)]
        if not rows:
            return []
        mismatch = self._embed_mismatch()
        if mismatch.get("mismatch"):
            raise RuntimeError(
                "индекс собран другой моделью эмбеддингов (%s, dim %s), "
                "сейчас активна %s (dim %s) — переиндексируйте базу"
                % (mismatch.get("index_model") or "?",
                   mismatch.get("index_dim") or "?",
                   mismatch.get("current_model") or "?",
                   mismatch.get("current_dim") or "?"))
        pool = max(1, k * max(1, pool_mult))
        hits = self._score_rows(rows, qs, settings, pool)
        if min_score > 0.0:
            hits = [h for h in hits if h["score"] >= min_score]
        return hits[:max(1, k)]

    def rerank(self, query, hits, top_k=None, rerank_fn=None, model=None):
        """ВТОРОЙ этап после поиска: пересортировка кандидатов.

        rerank_fn — callback без LLM в самом модуле: (запрос, список hit)
        → список пересортированных hit с вероятностями (см. агент.rag_rerank).
        Если rerank_fn не задан или вернул пустое — возвращает hits как есть. При наличии
        скора от реранкера он пишется в поле "rerank".
        """
        hits = list(hits or [])
        if not hits or not callable(rerank_fn):
            return hits[:max(1, int(top_k or len(hits)))]
        try:
            ranked = rerank_fn(str(query or ""), hits)
        except Exception:                        # noqa: BLE001
            return hits[:max(1, int(top_k or len(hits)))]
        if not ranked:
            return hits[:max(1, int(top_k or len(hits)))]
        return list(ranked)[:max(1, int(top_k or len(ranked)))]

    def build_context(self, query, top_k=None, docs_dir=None, queries=None,
                      rerank_fn=None):
        """Формирует текстовый блок контекста RAG для подмешивания в промпт.

        Возвращает (context_text, hits): context_text — готовый блок с
        указанием источника (файл, страница) или "" если ничего не найдено;
        hits — список найденных фрагментов (для трассировки/UI).
        Ограничивается RAG_CTX_CAP символами.

        queries   — набор формулировок (оригинал + query rewrite); если None — [query];
        rerank_fn — callback СТОРОГО этапа (реранкер) силами агента:
                    (query, hits) -> hits с полем "rerank"/пересортированные.
        """
        if queries:
            hits = self.search_multi(queries, top_k=top_k, docs_dir=docs_dir)
        else:
            hits = self.search(query, top_k=top_k, docs_dir=docs_dir)
        if hits and callable(rerank_fn):
            hits = self.rerank(query, hits, top_k=top_k, rerank_fn=rerank_fn)
        if not hits:
            return "", []
        parts = []
        used = 0
        cap = int(config.RAG_CTX_CAP)
        for h in hits:
            cid = format_citation_id(h["file"], h.get("page"),
                                     h.get("chunk"))
            head = "[фрагмент: %s, id=%s]" % (
                os.path.basename(h["file"]), cid)
            body = h["text"].strip()
            piece = head + "\n" + body
            if used + len(piece) > cap and parts:
                break
            parts.append(piece)
            used += len(piece) + 2
        return "\n\n".join(parts), hits

    # ---- статус ----
    def status(self):
        """Снимок состояния RAG для интерфейса.

        {enabled, docs_dir, top_k, embed_model, files, chunks, updated,
         available(есть ли индекс), last_error}
        """
        settings = self.load_settings()
        conn = self._conn()
        embed_cfg = _embed_config()
        mismatch = self._embed_mismatch()
        try:
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        except Exception:                        # noqa: BLE001
            chunks = 0
        try:
            files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        except Exception:                        # noqa: BLE001
            files = 0
        try:
            images = conn.execute(
                "SELECT COUNT(*) FROM images").fetchone()[0]
        except Exception:                        # noqa: BLE001
            images = 0
        try:
            errs = conn.execute(
                "SELECT path, error FROM files WHERE error IS NOT NULL "
                "AND error<>'' LIMIT 20").fetchall()
        except Exception:                        # noqa: BLE001
            errs = []
        return {
            "enabled": bool(settings.get("enabled")),
            "docs_dir": settings.get("docs_dir"),
            "top_k": settings.get("top_k"),
            "embed_model": embed_cfg.get("model"),
            "embed_dim": int(embed_cfg.get("dim")
                             or config.RAG_EMBED_DIM),
            "embed_backend": embed_cfg.get("backend"),
            "embed_url": embed_cfg.get("url"),
            "embed_source": embed_cfg.get("source"),
            # Модель/размерность, которыми РЕАЛЬНО собран индекс.
            "index_embed_model": mismatch.get("index_model"),
            "index_embed_dim": mismatch.get("index_dim") or None,
            "embed_mismatch": bool(mismatch.get("mismatch")),
            # Улучшенный поиск (показываем в UI).
            "rewrite": bool(settings.get("rewrite")),
            "hybrid": bool(settings.get("hybrid")),
            "hybrid_alpha": settings.get("hybrid_alpha"),
            "pool_mult": settings.get("pool_mult"),
            "min_score": settings.get("min_score"),
            "rerank": settings.get("rerank"),
            "files": int(files),
            "chunks": int(chunks),
            "images": int(images),
            "images_enabled": bool(getattr(config, "RAG_IMAGES_ENABLED",
                                            True)),
            "updated": settings.get("updated"),
            "available": int(chunks) > 0,
            "errors": [{"file": os.path.basename(p), "error": e}
                       for p, e in errs],
        }


# ----------------------------------------------------------------------
# Источники и идентификаторы ссылок (для карточки ответа)
# ----------------------------------------------------------------------
def format_citation_id(file, page, chunk):
    """Собирает КОРОТКИЙ идентификатор фрагмента вида section/chunk_id.

    Формат: «<документ>#стр.<P>/chunk.<C>» — используется и в промпте (модель
    обязана ссылаться на источник в скобках), и в блоке «Источники». Так
    пользователь понимает, из какого раздела/фрагмента взята цитата.
    Без номера страницы/чанка часть опускается. Пустой файл → "".
    """
    base = os.path.basename(str(file or "").strip())
    if not base:
        return ""
    parts = []
    try:
        p = int(page or 0)
    except (TypeError, ValueError):
        p = 0
    try:
        c = int(chunk or 0)
    except (TypeError, ValueError):
        c = 0
    if p > 0:
        parts.append("стр.%d" % p)
    if c > 0:
        parts.append("chunk.%d" % c)
    if not parts:
        return base
    return base + "#" + "/".join(parts)


def format_sources(hits, docs_dir=None):
    """Собирает УНИКАЛЬНЫЕ источники из результатов поиска RAG.

    На вход — список фрагментов (hits) из RagStore.search/build_context,
    каждый вида {"file", "page", "chunk", "score", ...}. Возвращает список
    словарей {file, doc, path, pages, citations}, где:
      * file  — имя файла (без пути) — для показа и совместимости;
      * doc   — имя файла без расширения (для показа);
      * path  — путь ОТНОСИТЕЛЬНО docs_dir (например, "docs/REPORT.md" или
                "rtk_app/rag.py"); по нему сервер отдаёт документ из /docs/.
                Если docs_dir не задан или файл вне него — равен file;
      * pages — отсортированный список страниц (у текстовых файлов пустой);
      * citations — идентификаторы фрагментов вида <файл>#стр.P/chunk.C.

    Один и тот же файл показывается один раз, а страницы объединяются.
    Порядок — по максимальной релевантности (лучший score сверху).
    """
    order = []          # порядок вывода файлов (по лучшему score)
    by_file = {}        # file -> {"doc", "path", "pages", "best", "citations"}
    root = str(docs_dir or "").strip()
    for h in hits or []:
        if not isinstance(h, dict):
            continue
        full = str(h.get("file") or "").strip()
        if not full:
            continue
        base = os.path.basename(full)
        rel = base
        if root:
            try:
                cand = os.path.relpath(full, root)
                cand = cand.replace(os.sep, "/")
                if not cand.startswith(".."):
                    rel = cand
            except (ValueError, OSError):
                rel = base
        try:
            page = int(h.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        try:
            chunk = int(h.get("chunk") or 0)
        except (TypeError, ValueError):
            chunk = 0
        try:
            score = float(h.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        item = by_file.get(base)
        if item is None:
            item = {"doc": os.path.splitext(base)[0], "path": rel,
                    "pages": set(), "best": score, "citations": []}
            by_file[base] = item
            order.append(base)
        if page > 0:
            item["pages"].add(page)
        # Идентификатор section/chunk_id этого фрагмента (для ссылок).
        cid = format_citation_id(base, page, chunk)
        if cid and cid not in item["citations"]:
            item["citations"].append(cid)
        if score > item["best"]:
            item["best"] = score
    # Сортируем файлы по убыванию релевантности (лучший фрагмент сверху).
    order.sort(key=lambda b: by_file[b]["best"], reverse=True)
    out = []
    for base in order:
        item = by_file[base]
        out.append({
            "file": base,
            "doc": item["doc"],
            "path": item["path"],
            "pages": sorted(item["pages"]),
            "citations": sorted(item["citations"]),
        })
    return out
