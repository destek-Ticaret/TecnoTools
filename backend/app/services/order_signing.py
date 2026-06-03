"""Sipariş barkodu için RSA imzalama servisi.

Mimari:
  • İlk çağrıda backend/keys/ klasöründe RSA-2048 anahtar çifti oluşturulur.
  • Sipariş imzası: SHA-256 + RSA-PKCS1v15.
  • Token formatı (URL-safe base64): "<payload_b64>.<signature_b64>"
    Payload: {"order_no": str, "total": str, "issued_at": "ISO8601 UTC"}

Token QR koduyla taşınır; herhangi biri /api/orders/verify-barcode ile public
doğrulama yapabilir. Yalnız sipariş_no + total bilgisi gösterilir.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

_KEYS_DIR = Path(__file__).resolve().parent.parent.parent / "keys"
_PRIV_PATH = _KEYS_DIR / "order_signing_private.pem"
_PUB_PATH = _KEYS_DIR / "order_signing_public.pem"

_private_key: rsa.RSAPrivateKey | None = None
_public_key: rsa.RSAPublicKey | None = None


def _ensure_keys_loaded() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    global _private_key, _public_key
    if _private_key is not None and _public_key is not None:
        return _private_key, _public_key

    _KEYS_DIR.mkdir(parents=True, exist_ok=True)
    if _PRIV_PATH.exists() and _PUB_PATH.exists():
        with _PRIV_PATH.open("rb") as f:
            _private_key = serialization.load_pem_private_key(f.read(), password=None)
        with _PUB_PATH.open("rb") as f:
            _public_key = serialization.load_pem_public_key(f.read())
    else:
        _private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _public_key = _private_key.public_key()
        _PRIV_PATH.write_bytes(_private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        _PUB_PATH.write_bytes(_public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
    return _private_key, _public_key


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_order(order_no: str, total: float | str) -> dict[str, str]:
    """Order için imzalanmış token üretir. Dönen dict: token, public verify_path."""
    priv, _ = _ensure_keys_loaded()
    payload = {
        "order_no": str(order_no),
        "total": f"{float(total):.2f}",
        "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = priv.sign(payload_bytes, padding.PKCS1v15(), hashes.SHA256())
    token = _b64url_encode(payload_bytes) + "." + _b64url_encode(sig)
    return {"token": token, "payload": payload}


def verify_token(token: str) -> dict[str, Any] | None:
    """Token geçerliyse payload dict'i, değilse None döner."""
    _, pub = _ensure_keys_loaded()
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload_bytes = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error):
        return None
    try:
        pub.verify(sig, payload_bytes, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        return None
    try:
        return json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
