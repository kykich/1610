"""Клиент для работы с GigaChat (облачный Сбер) через chat completions.
Требует файл gigakey.txt (base64 "client_id:client_secret") либо
учётные данные, введённые пользователем в интерфейсе (они действуют
только на время запроса — см. rtk_app.runtime_keys).
OAuth2 client_credentials; access-токен кешируется ПО УЧЁТНЫМ ДАННЫМ.
"""
import hashlib
import json
import threading
import time
import uuid
import urllib.parse
import urllib.request

from . import config, runtime_keys

__all__ = ["chat"]

# Кэш токенов: ключ — хэш учётных данных (сам ключ в памяти кэша не лежит).
_TOKENS = {}
_TOKENS_LOCK = threading.Lock()
# Сколько разных кредов держим одновременно (браузерные сессии + файл).
_MAX_TOKENS = 8


def _read_basic_auth(allow_session=True):
    """Читает базовую авторизацию (base64 client_id:client_secret).

    allow_session=True — сначала смотрим ключ, введённый пользователем
    в интерфейсе (действует только на время запроса); если его нет —
    читаем файл gigakey.txt.

    Комментарии (строки, начинающиеся с '#') и пустые строки игнорируются —
    ключом считается первая непустая рабочая строка. Так в файле-заглушке
    можно держать инструкцию # и рядом отдельной строкой настоящий ключ.
    """
    if allow_session:
        session_basic = runtime_keys.gigachat_basic()
        if session_basic:
            return session_basic
    try:
        with open(config.GC_KEY_FILE, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                return line
    except FileNotFoundError:
        raise FileNotFoundError(
            "GigaChat: нет учётных данных. Вставьте client_id:client_secret "
            "в блоке «Ключи облачных моделей» или создайте gigakey.txt")
    return ""


def _token_cache_key(basic):
    """Короткий хэш кредов — чтобы не хранить сам ключ в ключах словаря."""
    return hashlib.sha256(str(basic).encode("utf-8")).hexdigest()[:16]


def _post(url, headers, body):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_access_token(basic=None, allow_session=True):
    """OAuth-токен GigaChat (кэш — свой для каждой пары кредов)."""
    basic = str(basic or "").strip()
    if not basic:
        basic = _read_basic_auth(allow_session=allow_session)
    if not basic:
        raise RuntimeError(
            "OAuth GigaChat: пустые учётные данные — вставьте "
            "client_id:client_secret в блоке «Ключи облачных моделей» или "
            "проверьте gigakey.txt")
    cache_key = _token_cache_key(basic)
    now = time.time()
    with _TOKENS_LOCK:
        item = _TOKENS.get(cache_key)
        if item and now < item[1] - 30:
            return item[0]
    headers = {
        "Authorization": "Basic " + basic,
        "RqUID": str(uuid.uuid4()),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    body = urllib.parse.urlencode({"scope": config.GC_SCOPE}).encode("utf-8")
    data = _post(config.GC_TOKEN_URL, headers, body)
    token = data.get("access_token")
    if not token:
        raise RuntimeError("OAuth GigaChat: нет access_token")
    with _TOKENS_LOCK:
        # Ограничиваем кэш: сначала убираем истёкшие, затем самый старый.
        for key in [k for k, v in _TOKENS.items() if now >= v[1]]:
            _TOKENS.pop(key, None)
        while len(_TOKENS) >= _MAX_TOKENS:
            _TOKENS.pop(next(iter(_TOKENS)), None)
        _TOKENS[cache_key] = (token, now + config.GC_TOKEN_TTL)
    return token




def chat(messages, model=config.GC_MODEL, temperature=None, max_tokens=None,
         top_p=None, repetition_penalty=None, basic=None,
         allow_session=True):
    """Отправляет сообщения в GigaChat.

    Возвращает dict: content, prompt_tokens, completion_tokens.
    temperature — float или None; если задан — передаётся в запрос.
    max_tokens — int или None; если задан — ограничивает число токенов ответа.
    top_p — float или None; nucleus sampling (GigaChat поддерживает).
    repetition_penalty — float или None; штраф за повторы (1.0 — без штрафа).
    basic — явные учётные данные (для проверки ключа); None — сессионные
    (из интерфейса) либо файл gigakey.txt.
    allow_session=False — не использовать ключ из интерфейса (проверка).
    """
    token = _get_access_token(basic=basic, allow_session=allow_session)
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"model": model, "messages": messages, "stream": False}
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    if top_p is not None:
        payload["top_p"] = float(top_p)
    if repetition_penalty is not None:
        payload["repetition_penalty"] = float(repetition_penalty)
    data = _post(
        config.GC_API_BASE,
        headers,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    return {
        "content": content,
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
    }