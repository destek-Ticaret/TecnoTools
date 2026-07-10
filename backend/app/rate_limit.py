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
    # GÜVENLİK: Railway'in edge/CDN katmanı X-Forwarded-For'u yeniden yazar —
    # client'ın gönderdiği değeri değil, gerçek bağlantı IP'sini SOLA ekler;
    # bu yüzden ilk (en soldaki) değer güvenilirdir. X-Real-IP Railway'de CDN'in
    # kendi IP'sine sabitlenebildiğinden (tüm istekler aynı IP'ymiş gibi görünür
    # → rate limit fiilen tüm kullanıcılar için ortak bir havuza düşerdi) yalnızca
    # bundled nginx kurulumu (docker-compose.prod.yml, $remote_addr) için fallback
    # olarak kullanılır. (bkz. app/admin_ip_filter.py _client_ip — aynı mantık)
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return get_remote_address(request)


_is_test = os.environ.get("APP_ENV", "").lower() == "test"
limiter = Limiter(key_func=_key, default_limits=["200/minute"], enabled=not _is_test)
