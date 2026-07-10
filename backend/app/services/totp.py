"""RFC 6238 TOTP doğrulama — sadece doğrulama; secret üretimi frontend'de yapılır."""

import base64
import hashlib
import hmac
import struct
import time


def _b32_decode(secret: str) -> bytes:
    s = secret.replace(" ", "").upper()
    # Padding ekle
    pad = (-len(s)) % 8
    return base64.b32decode(s + "=" * pad)


def _hotp(key: bytes, counter: int, digits: int = 6) -> str:
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code_int = (
        ((h[offset] & 0x7F) << 24)
        | ((h[offset + 1] & 0xFF) << 16)
        | ((h[offset + 2] & 0xFF) << 8)
        | (h[offset + 3] & 0xFF)
    )
    return str(code_int % (10**digits)).zfill(digits)


def verify_totp(
    code: str,
    secret_b32: str,
    window: int = 1,
    step: int = 30,
    min_step: int | None = None,
) -> int | None:
    """Kod geçerliyse eşleşen HOTP step sayacını döner, geçersizse None.

    `min_step` verilirse, o step'e eşit ya da daha eski bir eşleşme reddedilir
    (anti-replay) — aksi hâlde bir kez görülen kod, ~90 saniyelik pencere
    içinde tekrar tekrar kullanılabilirdi."""
    if not code or not secret_b32:
        return None
    code = code.strip().replace(" ", "")
    try:
        key = _b32_decode(secret_b32)
    except Exception:
        return None
    now = int(time.time() // step)
    for w in range(-window, window + 1):
        counter = now + w
        if min_step is not None and counter <= min_step:
            continue
        if hmac.compare_digest(code, _hotp(key, counter)):
            return counter
    return None
