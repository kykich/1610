"""Клиент для работы с DeepSeek API (chat completions).
Поддерживает дополнительные настройки генерации: temperature, max_tokens,
top_p и repetition_penalty.
"""
import json
import urllib.request

from . import config, runtime_keys

__all__ = ["chat"]


def chat(api_key, messages, model=None, temperature=None, max_tokens=None,
         top_p=None, repetition_penalty=None, allow_session=True):
    """Отправляет сообщения, возвращает dict: content, prompt_tokens, completion_tokens.

    allow_session=True — ключ, введённый пользователем в интерфейсе
    (он живёт только на время запроса), имеет приоритет над файловым.
    False — использовать только переданный api_key (проверка ключа).

    temperature — float или None; None означает «не передавать» (системное
    значение модели по умолчанию).
    max_tokens — int или None; None означает «не передавать» (лимит модели
    по умолчанию). Если задан — ограничивает число новых токенов ответа.
    top_p — float или None; nucleus sampling (1.0 — выключено).
    repetition_penalty — float или None; штраф за повторы (1.0 — без штрафа).
    """
    if not model:
        model = config.DS_MODELS[0]
    if allow_session:
        api_key = runtime_keys.deepseek_key(api_key)
    api_key = str(api_key or "").strip()
    if not api_key:
        raise ValueError(
            "не задан ключ DeepSeek: вставьте его в блоке «Ключи облачных "
            "моделей» (действует до перезагрузки страницы) или создайте "
            "apidpsk.txt")
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
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
    req = urllib.request.Request(
        config.DS_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    return {
        "content": content,
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
    }

