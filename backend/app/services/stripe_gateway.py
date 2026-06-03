"""Stripe Checkout entegrasyonu.

PayTR'ye alternatif. Akış:
1. Backend Checkout Session yaratır (hosted UI URL'i döner)
2. Frontend kullanıcıyı bu URL'e yönlendirir
3. Stripe ödemeyi alır, sonra `success_url`'e yönlendirir
4. Stripe webhook ile `checkout.session.completed` event'i gelir → sipariş `payment_status=success`
"""

from collections.abc import Iterable

import stripe

from app.config import get_settings

settings = get_settings()
stripe.api_key = settings.stripe_secret_key or None


def create_checkout_session(
    *,
    order_no: str,
    customer_email: str,
    line_items: Iterable[tuple[str, float, int]],  # (name, unit_price_tl, qty)
    currency: str = "try",
    metadata: dict | None = None,
) -> dict:
    """Hosted Stripe Checkout session yaratır. Test modu için TEST API key kullanılır.
    Credentials boşsa dev mock URL döner."""
    if not settings.stripe_secret_key:
        return {
            "url": f"https://example.com/stripe-mock/{order_no}",
            "id": f"cs_dev_{order_no}",
            "mock": True,
        }

    line_data = [
        {
            "price_data": {
                "currency": currency,
                "product_data": {"name": name},
                "unit_amount": int(round(price * 100)),  # kuruş/cent
            },
            "quantity": qty,
        }
        for name, price, qty in line_items
    ]
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=line_data,
        customer_email=customer_email,
        success_url=settings.stripe_success_url,
        cancel_url=settings.stripe_cancel_url,
        client_reference_id=order_no,
        metadata={**(metadata or {}), "order_no": order_no},
    )
    return {"url": session.url, "id": session.id, "mock": False}


def verify_webhook_signature(payload: bytes, signature: str) -> dict | None:
    """Stripe webhook event objesini döndürür; imza geçersizse None."""
    if not settings.stripe_webhook_secret:
        return None
    try:
        return stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    except Exception:
        return None
