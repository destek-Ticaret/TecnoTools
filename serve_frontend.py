"""TecnoTools statik dosya sunucusu — admin.html için IP whitelist.

Public dosyalar (index.html, /js, /legal, vb.) herkese açık.
admin.html ve admin.* (Map vs) yalnızca ADMIN_IP_WHITELIST + localhost'tan açık.

Geliştirme modunda (.env içinde APP_ENV=development) LAN aralıkları
(10.0.0.0/8, 172.16-31.0.0/12, 192.168.0.0/16, 169.254/16) otomatik açılır
— telefondan veya iç ağdaki diğer cihazlardan test rahat olsun diye.

Kullanım:
    python serve_frontend.py [port]

Whitelist .env'den okunur (backend/.env içindeki ADMIN_IP_WHITELIST).
"""
import http.server
import ipaddress
import socketserver
import sys
from pathlib import Path

LOCAL_IPS = {"127.0.0.1", "::1", "localhost"}
ADMIN_PATHS = ("/admin.html", "/admin")

_DEV_LAN_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
]


def _read_env() -> dict[str, str]:
    env_file = Path(__file__).parent / "backend" / ".env"
    out: dict[str, str] = {}
    if not env_file.exists():
        return out
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_whitelist() -> set[str]:
    env = _read_env()
    allowed: set[str] = set(LOCAL_IPS)
    csv = env.get("ADMIN_IP_WHITELIST", "")
    for ip in csv.split(","):
        ip = ip.strip()
        if ip:
            allowed.add(ip)
    return allowed


def _is_dev_lan(ip_str: str) -> bool:
    if not ip_str:
        return False
    try:
        return any(ipaddress.ip_address(ip_str) in net for net in _DEV_LAN_NETWORKS)
    except ValueError:
        return False


WHITELIST = load_whitelist()
APP_ENV = _read_env().get("APP_ENV", "").lower()
DEV_LAN_OPEN = APP_ENV == "development"


class IPFilteredHandler(http.server.SimpleHTTPRequestHandler):
    def _is_admin_path(self) -> bool:
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        return any(path == p or path.startswith(p + ".") for p in ADMIN_PATHS)

    def _client_ip(self) -> str:
        fwd = self.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.client_address[0]

    def _check_admin_access(self) -> bool:
        if not self._is_admin_path():
            return True
        ip = self._client_ip()
        if ip in WHITELIST:
            return True
        if DEV_LAN_OPEN and _is_dev_lan(ip):
            return True
        self.send_error(403, "Forbidden", f"Admin paneline {ip} adresinden erisim engellendi.")
        return False

    # Cache-Control header'ları:
    # - HTML ve service worker: hiç cache'leme (her seferinde 200/304 ile taze).
    # - js/css/font/img: kısa TTL (60 sn) — geliştirme sırasında değişiklikler
    #   neredeyse anında düşer; SW de paralel olarak stale-while-revalidate yapar.
    def end_headers(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0].lower()
        if path.endswith(".html") or path in ("/", "/sw.js"):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        elif any(path.endswith(ext) for ext in (".js", ".css", ".webmanifest", ".json")):
            self.send_header("Cache-Control", "no-cache, max-age=0, must-revalidate")
        super().end_headers()

    def do_GET(self):
        if not self._check_admin_access():
            return
        super().do_GET()

    def do_HEAD(self):
        if not self._check_admin_access():
            return
        super().do_HEAD()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5500
    print(f"Serving on http://0.0.0.0:{port}")
    print(f"Admin whitelist: {sorted(WHITELIST)}")
    with socketserver.ThreadingTCPServer(("", port), IPFilteredHandler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
