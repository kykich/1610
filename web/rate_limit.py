# -*- coding: utf-8 -*-
"""Простой потокобезопасный rate limit по IP (token bucket).

Реализует §7.4 ТЗ (docs/vps-llm-service.md): не более ``RATE_LIMIT_PER_MIN``
запросов в минуту с одного IP (по умолчанию 10). IP берётся из
``X-Forwarded-For`` (за nginx) или из сокета — этим занимается вызывающий
HTTP-слой и передаёт сюда готовый ключ (IP).

Токен-бакет: у каждого IP есть ``capacity`` токенов, replenish —
``capacity`` токенов за 60 секунд. Один запрос «стоит» 1 токен. Если токенов
нет — запрос отклоняется (``429`` + ``Retry-After``).
"""
import threading
import time

from rtk_app import config


class _Bucket:
    __slots__ = ("tokens", "updated")

    def __init__(self, capacity):
        self.tokens = float(capacity)
        self.updated = time.time()


class RateLimiter:
    """Лимитер по произвольному ключу (обычно IP)."""

    def __init__(self, per_minute=None, now=None):
        self.per_minute = int(per_minute or getattr(config, "RATE_LIMIT_PER_MIN", 10))
        self._window = 60.0
        self._now = now or time.time
        self._lock = threading.Lock()
        self._buckets = {}
        self._last_sweep = 0.0

    def _replenish(self, b, now):
        rate = self.per_minute / self._window      # токенов в секунду
        elapsed = now - b.updated
        if elapsed > 0:
            b.tokens = min(self.per_minute, b.tokens + elapsed * rate)
            b.updated = now

    def check(self, key):
        """Возвращает (allowed: bool, retry_after: int seconds).

        allowed=True — запрос разрешён (1 токен списан).
        allowed=False — лимит исчерпан; retry_after — через сколько секунд
        снова будет токен (для заголовка Retry-After).
        """
        if self.per_minute <= 0:
            return True, 0
        now = self._now()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = _Bucket(self.per_minute)
                self._buckets[key] = b
            self._replenish(b, now)
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                allowed, retry = True, 0
            else:
                # Секунд до накопления одного токена.
                rate = self.per_minute / self._window
                need = (1.0 - b.tokens) / rate if rate > 0 else self._window
                allowed, retry = False, max(1, int(need + 0.999))
            # Редкая очистка «остывших» ключей (раз в минуту).
            if now - self._last_sweep > 60:
                self._sweep(now)
                self._last_sweep = now
            return allowed, retry

    def _sweep(self, now):
        """Удаляет ключи, которые полностью восстановились и не используются."""
        for key in list(self._buckets.keys()):
            b = self._buckets[key]
            if now - b.updated > self._window and b.tokens >= self.per_minute - 0.001:
                del self._buckets[key]


_LIMITER = None
_LIMITER_LOCK = threading.Lock()


def get_limiter():
    global _LIMITER
    if _LIMITER is None:
        with _LIMITER_LOCK:
            if _LIMITER is None:
                _LIMITER = RateLimiter()
    return _LIMITER
