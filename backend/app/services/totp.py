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


def verify_totp(code: str, secret_b32: str, window: int = 1, step: int = 30) -> bool:
    if not code or not secret_b32:
        return False
    code = code.strip().replace(" ", "")
    try:
        key = _b32_decode(secret_b32)
    except Exception:
        return False
    now = int(time.time() // step)
    return any(hmac.compare_digest(code, _hotp(key, now + w)) for w in range(-window, window + 1))
