"""Rate limit yapılandırması — slowapi tabanlı.

Bellek-içi storage kullanır (tek-instance yeterli). Multi-instance deploy için
Redis backend'e çevrilebilir (`storage_uri='redis://...'`).

Limitler IP başınadır. PayTR callback'i muaftır (sunucu-sunucu trafik).

Test modu (APP_ENV=test): Limiter `enabled=False` ile no-op'a düşer; testler
arasında slowapi global state'i tıkanmaz.
"""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address


def _key(request):
    # GÜVENLİK: X-Real-IP nginx tarafından $remote_addr'a sabitlenir (spoof edilemez).
    # X-Forwarded-For'un ilk değeri client-kontrollü olabileceğinden ona güvenmiyoruz
    # (yoksa saldırgan sahte IP ile rate-limit'i atlatabilirdi).
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return get_remote_address(request)


_is_test = os.environ.get("APP_ENV", "").lower() == "test"
limiter = Limiter(key_func=_key, default_limits=["200/minute"], enabled=not _is_test)
