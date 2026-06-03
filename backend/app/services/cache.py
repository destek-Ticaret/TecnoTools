"""In-memory TTL + LRU cache.

Kullanım:
    @ttl_cache(ttl_seconds=300, max_size=128)
    async def top_products(db, days: int) -> list: ...

Tek-instance deploy için yeterli. Multi-instance'da Redis'e geç.
Anahtarlamada SQLAlchemy Session gibi non-hashable objeleri otomatik dışla.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable

_DEFAULT_SENTINEL = object()


class TTLCache:
    """Thread-safe değil; asyncio tek-thread varsayımı altında çalışır."""

    def __init__(self, ttl_seconds: float = 300.0, max_size: int = 256) -> None:
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._store: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any, default: Any = None) -> Any:
        """Cache miss durumunda `default`'u olduğu gibi döndür.

        Sentinel ile miss'i ayırt etmek isteyen çağırıcılar `default`'a kendi
        sentinel'ını verebilir; biz None varsayılanını da geçerli bir miss
        cevabı sayarız.
        """
        item = self._store.get(key)
        if item is None:
            return default
        expires_at, value = item
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return default
        # LRU: en sona taşı
        self._store.move_to_end(key)
        return value

    def set(self, key: Any, value: Any, ttl: float | None = None) -> None:
        ttl_v = self.ttl if ttl is None else ttl
        self._store[key] = (time.monotonic() + ttl_v, value)
        self._store.move_to_end(key)
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()

    def invalidate(self, predicate: Callable[[Any], bool] | None = None) -> int:
        """Anahtarı predicate(True) olan girişleri kaldır. Predicate yoksa hepsini."""
        if predicate is None:
            n = len(self._store)
            self._store.clear()
            return n
        keys = [k for k in self._store if predicate(k)]
        for k in keys:
            self._store.pop(k, None)
        return len(keys)

    def __len__(self) -> int:
        return len(self._store)


def _hashable_args(args: tuple, kwargs: dict) -> tuple:
    """SQLAlchemy session vs. non-hashable argümanları atla.

    Cache key sadece primitive / hashable parametreleri içerir.
    """
    parts: list[Any] = []
    for a in args:
        try:
            hash(a)
            parts.append(a)
        except TypeError:
            parts.append(type(a).__name__)  # tip ismi yeterli ayırt edici
    for k in sorted(kwargs):
        try:
            hash(kwargs[k])
            parts.append((k, kwargs[k]))
        except TypeError:
            parts.append((k, type(kwargs[k]).__name__))
    return tuple(parts)


def ttl_cache(ttl_seconds: float = 300.0, max_size: int = 128):
    """Async + sync uyumlu TTL cache decorator."""
    cache = TTLCache(ttl_seconds=ttl_seconds, max_size=max_size)

    def decorator(fn: Callable):
        is_coro = asyncio.iscoroutinefunction(fn)

        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            key = (fn.__qualname__, _hashable_args(args, kwargs))
            hit = cache.get(key, _DEFAULT_SENTINEL)
            if hit is not _DEFAULT_SENTINEL:
                return hit
            value = await fn(*args, **kwargs)
            cache.set(key, value)
            return value

        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            key = (fn.__qualname__, _hashable_args(args, kwargs))
            hit = cache.get(key, _DEFAULT_SENTINEL)
            if hit is not _DEFAULT_SENTINEL:
                return hit
            value = fn(*args, **kwargs)
            cache.set(key, value)
            return value

        wrapper = async_wrapper if is_coro else sync_wrapper
        wrapper.cache = cache  # type: ignore[attr-defined]
        return wrapper

    return decorator


# Modüller arasında paylaşılan tek-instance cache (kur, popüler ürünler vs.)
shared_cache = TTLCache(ttl_seconds=300, max_size=512)
