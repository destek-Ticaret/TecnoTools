"""Çoklu para birimi servisi.

Ürün fiyatları BASE_CURRENCY (varsayılan TRY) ile saklanır. Public API endpoint'i
istemcinin sorgu parametresi (?currency=USD) ile döviz kuru uygular.

Kur sağlayıcı: frankfurter.app — Avrupa Merkez Bankası verisi, API key gerekmiyor,
ücretsiz, üretim kullanımına uygun.

Cache: in-memory dict, 6 saat TTL.
"""

import asyncio
import logging
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_RATE_CACHE: dict[str, tuple[float, float]] = {}  # base→{ "USD": (rate, fetched_at), ... }
_LOCK = asyncio.Lock()


async def _fetch_rates(base: str) -> dict:
    """Tüm desteklenen para birimleri için kur. Network hata → cache fallback."""
    url = f"https://api.frankfurter.app/latest?from={base}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    rates = data.get("rates") or {}
    rates[base] = 1.0  # kendisine 1
    return rates


async def get_rate(from_currency: str, to_currency: str) -> float:
    """from→to kur. Cache'te varsa kullan, yoksa fetch et."""
    from_currency = (from_currency or "").upper()
    to_currency = (to_currency or "").upper()
    if from_currency == to_currency:
        return 1.0

    now = time.time()
    key = from_currency
    async with _LOCK:
        cached = _RATE_CACHE.get(key)
        if cached and (now - cached[1]) < settings.exchange_rate_cache_seconds:
            rates_at = cached[0]
        else:
            try:
                rates = await _fetch_rates(from_currency)
                _RATE_CACHE[key] = (rates, now)
                rates_at = rates
            except Exception as e:
                logger.warning("Kur çekilemedi (%s→%s): %s", from_currency, to_currency, e)
                if cached:
                    rates_at = cached[0]
                else:
                    return 1.0  # fallback: dönüşüm yok

    return float(rates_at.get(to_currency, 1.0))


def convert(amount: float, rate: float) -> float:
    return round(amount * rate, 2)


def is_supported(currency: str) -> bool:
    return (currency or "").upper() in settings.supported_currency_list
