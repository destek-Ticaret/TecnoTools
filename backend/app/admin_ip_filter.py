"""Admin endpoint'leri için IP whitelist middleware.

`admin_ip_whitelist` boşsa hiçbir şey yapmaz. Doluysa /api/auth/* ve
/api/admin/* yollarına yalnızca whitelist + localhost'tan erişim verir.
Reverse proxy arkasındaysa X-Forwarded-For başlığının ilk değeri esas alınır.

Geliştirme modunda (APP_ENV=development) LAN aralıkları (RFC1918 + link-local)
otomatik whitelist'e dahil edilir — telefondan/diğer cihazlardan test rahat olsun.
Production'da bu davranış pasiftir; sadece explicit IP'ler kabul edilir.
"""
import ipaddress
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

LOCAL_IPS = {"127.0.0.1", "::1", "localhost"}
PROTECTED_PREFIXES = ("/api/auth/", "/api/admin/")

# Geliştirme ortamında otomatik kabul edilen LAN aralıkları
_DEV_LAN_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique-local
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]


def _client_ip(request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def _is_dev_lan(ip_str: str) -> bool:
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in _DEV_LAN_NETWORKS)


class AdminIPFilterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_ips: list[str]):
        super().__init__(app)
        self.allowed = set(allowed_ips) | LOCAL_IPS
        self.enabled = bool(allowed_ips)
        # APP_ENV development ise LAN otomatik açılır
        self.dev_lan_open = os.environ.get("APP_ENV", "").lower() == "development"

    async def dispatch(self, request, call_next):
        if not self.enabled:
            return await call_next(request)
        path = request.url.path
        if not any(path.startswith(p) for p in PROTECTED_PREFIXES):
            return await call_next(request)
        ip = _client_ip(request)
        if ip in self.allowed:
            return await call_next(request)
        if self.dev_lan_open and _is_dev_lan(ip):
            return await call_next(request)
        return JSONResponse(status_code=403, content={"detail": f"IP {ip} bu kaynağa erişemez."})
