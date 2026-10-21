# -*- coding: utf-8 -*-
"""Сессионные ключи облачных моделей (только память процесса).

Ключи вводятся пользователем в интерфейсе и приходят в теле запроса
(``POST /api/ask`` -> поле ``keys``). Они действуют ТОЛЬКО на время
обработки этого запроса в текущем потоке:

  * ничего не пишется на диск — ни ``session/*.json``, ни ``apidpsk.txt``
    / ``gigakey.txt``;
  * после ответа сервер вызывает :func:`clear` (см. ``web/server.py``);
  * перезагрузка страницы, «новый разговор» и закрытие окна стирают
    ключи на клиенте (они живут в памяти вкладки), поэтому на сервер они
    больше не приходят.

Приоритет: ключ из интерфейса > ключ из файла. Файлы ``apidpsk.txt`` и
``gigakey.txt`` остаются резервным способом (VPS, CLI, GitHub Actions).

Модуль не зависит от HTTP-слоя и ничего не логирует: значения ключей не
должны попадать ни в журналы, ни в ответы API.
"""
import base64
import re
import threading

__all__ = ["MAX_KEY_CHARS", "normalize_deepseek", "normalize_gigachat",
           "mask", "set_keys", "clear", "deepseek_key", "gigachat_basic",
           "current"]

# Верхняя граница на вставляемое значение (защита от мусора в теле запроса).
MAX_KEY_CHARS = 4096

# base64-алфавит (GigaChat-креды хранятся именно в этом виде).
_B64_RE = re.compile(r"^[A-Za-z0-9+/=]+$")

# Ключи ТЕКУЩЕГО потока (каждый запрос ThreadingHTTPServer — свой поток).
_thread = threading.local()


def _first_value_line(text):
    """Первая значимая строка вставки (комментарии '#' пропускаются)."""
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def normalize_deepseek(text):
    """Приводит вставленный ключ DeepSeek к одной строке ``sk-…``.

    Пустая строка -> ``""`` (значит «ключ не задан, использовать файл»).
    Слишком длинное значение -> ValueError.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    if "\n" in s or "\r" in s:
        s = _first_value_line(s)
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    # Вставка из буфера иногда содержит пробелы/переносы внутри — убираем.
    s = "".join(s.split())
    if len(s) > MAX_KEY_CHARS:
        raise ValueError("ключ DeepSeek слишком длинный")
    return s


def normalize_gigachat(text):
    """Возвращает base64-строку ``client_id:client_secret`` для GigaChat.

    Принимает готовую base64 (как в файле ``gigakey.txt``) либо пару
    ``client_id:client_secret`` — тогда кодирует сам. Пустая строка -> ``""``.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    if "\n" in s or "\r" in s:
        s = _first_value_line(s)
    # Частая вставка вместе с заголовком: "Authorization: Basic <base64>".
    if ":" in s and s.lower().startswith("authorization:"):
        s = s.split(":", 1)[1].strip()
    if s.lower().startswith("basic "):
        s = s[6:].strip()
    if len(s) > MAX_KEY_CHARS:
        raise ValueError("учётные данные GigaChat слишком длинные")
    # base64 не содержит ':' — значит передана сама пара id:secret.
    if ":" in s:
        s = base64.b64encode(s.encode("utf-8")).decode("ascii")
    s = "".join(s.split())
    if s and not _B64_RE.match(s):
        raise ValueError(
            "GigaChat: нужна base64-строка client_id:client_secret")
    return s


def mask(value):
    """Маска для показа в интерфейсе: ``sk-…abcd`` (без полного ключа)."""
    s = str(value or "")
    if not s:
        return ""
    tail_len = 4 if len(s) > 8 else 2
    head = "sk-…" if s.startswith("sk-") else "…"
    return head + s[-tail_len:]


def set_keys(deepseek=None, gigachat=None):
    """Запоминает ключи для ТЕКУЩЕГО потока (на время запроса).

    Возвращает нормализованные значения (для тестов/диагностики).
    ValueError — если значение не похоже на ключ.
    """
    ds = normalize_deepseek(deepseek)
    gc = normalize_gigachat(gigachat)
    _thread.deepseek = ds
    _thread.gigachat = gc
    return {"deepseek": ds, "gigachat": gc}


def clear():
    """Стирает ключи текущего потока (вызывается после ответа)."""
    _thread.deepseek = ""
    _thread.gigachat = ""


def deepseek_key(fallback=""):
    """Ключ DeepSeek для текущего запроса (иначе fallback — файл/пусто)."""
    return getattr(_thread, "deepseek", "") or fallback


def gigachat_basic(fallback=""):
    """GigaChat-креды текущего запроса (иначе fallback — файл)."""
    return getattr(_thread, "gigachat", "") or fallback


def current():
    """Текущие ключи потока (диагностика/тесты; на диск не пишется)."""
    return {"deepseek": getattr(_thread, "deepseek", ""),
            "gigachat": getattr(_thread, "gigachat", "")}
