"""PayTR iframe API entegrasyonu.

Üretim için: https://dev.paytr.com/iframe-api/iframe-api-1-adim
- Token üretimi: HMAC-SHA256 + Base64
- Notification (callback) doğrulaması: HMAC-SHA256 hash karşılaştırması

Bu modül sadece protokolü implement eder; gerçek HTTP çağrısı `payments` router'ında.
"""

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Iterable

import httpx

from app.config import get_settings

settings = get_settings()
PAYTR_TOKEN_URL = "https://www.paytr.com/odeme/api/get-token"


def _hmac_b64(key: str, message: str) -> str:
    digest = hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _encode_basket(basket: Iterable[tuple[str, float, int]]) -> str:
    """basket = [(name, unit_price_tl, qty), ...]"""
    arr = [[name, f"{price:.2f}", qty] for name, price, qty in basket]
    return base64.b64encode(json.dumps(arr, ensure_ascii=False).encode("utf-8")).decode("ascii")


def build_paytr_token(
    *,
    order_no: str,
    email: str,
    amount_kurus: int,
    user_name: str,
    user_phone: str,
    user_address: str,
    basket: Iterable[tuple[str, float, int]],
    user_ip: str = "0.0.0.0",
    currency: str = "TL",
    no_installment: int = 0,
    max_installment: int = 0,
) -> dict:
    """PayTR iframe için token üretir ve sözlük döner: {token, raw_response}."""
    if not (
        settings.paytr_merchant_id and settings.paytr_merchant_key and settings.paytr_merchant_salt
    ):
        # Test/dev modunda credential yoksa sahte bir token döndür
        return {"token": "DEV-" + secrets.token_urlsafe(20), "raw_response": {"status": "dev-mock"}}

    merchant_oid = order_no.replace("-", "")  # PayTR yalnız alfanumerik kabul eder
    basket_b64 = _encode_basket(basket)

    hash_str = (
        f"{settings.paytr_merchant_id}{user_ip}{merchant_oid}{email}{amount_kurus}"
        f"{basket_b64}{no_installment}{max_installment}{currency}{settings.paytr_test_mode}"
    )
    paytr_token = _hmac_b64(settings.paytr_merchant_key + settings.paytr_merchant_salt, hash_str)

    data = {
        "merchant_id": settings.paytr_merchant_id,
        "user_ip": user_ip,
        "merchant_oid": merchant_oid,
        "email": email,
        "payment_amount": amount_kurus,
        "paytr_token": paytr_token,
        "user_basket": basket_b64,
        "debug_on": 1 if settings.app_env != "production" else 0,
        "no_installment": no_installment,
        "max_installment": max_installment,
        "user_name": user_name,
        "user_address": user_address,
        "user_phone": user_phone,
        "merchant_ok_url": settings.paytr_ok_url,
        "merchant_fail_url": settings.paytr_fail_url,
        "timeout_limit": 30,
        "currency": currency,
        "test_mode": settings.paytr_test_mode,
    }
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(PAYTR_TOKEN_URL, data=data)
        body = resp.json()
    import logging; logging.getLogger("paytr").error("PayTR response: %s | sent data: %s", body, {k: v for k, v in data.items() if k != "paytr_token"})
    if body.get("status") != "success":
        raise RuntimeError(f"PayTR token üretilemedi: {body.get('reason', 'bilinmeyen')}")
    return {"token": body["token"], "raw_response": body, "merchant_oid": merchant_oid}


def verify_callback_hash(
    *, merchant_oid: str, status: str, total_amount: str, hash_value: str
) -> bool:
    """PayTR notification handler için imza doğrulaması."""
    if not (settings.paytr_merchant_key and settings.paytr_merchant_salt):
        return False
    msg = f"{merchant_oid}{settings.paytr_merchant_salt}{status}{total_amount}"
    expected = _hmac_b64(settings.paytr_merchant_key, msg)
    return hmac.compare_digest(expected, hash_value)
