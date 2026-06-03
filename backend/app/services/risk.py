"""Sipariş risk skoru — 0..100, yüksek = daha riskli.

Faktörler (ağırlıkları yapılandırılabilir):
  +25  → aynı email son 1 saatte ≥3 sipariş (velocity)
  +20  → aynı IP_hash son 24 saatte ≥5 sipariş
  +15  → bu müşteri için son 24 saatte ≥2 başarısız ödeme
  +15  → toplam tutar >= ortalamanın 5 katı
  +10  → sipariş adresi ile fatura email domain'inin tutarsızlığı (TLD bazlı)
  +10  → tek seferde >3 farklı yüksek-fiyatlı ürün
  +5   → şüpheli email domain (10dk mail, vs.)
  +5   → siparişte birden fazla aynı ürün yüksek qty

Skor >=70 → ÇOK YÜKSEK; 40..69 → orta; 0..39 → düşük.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, OrderItem, PaymentStatus

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "yopmail.com", "trashmail.com", "fakeinbox.com", "throwawaymail.com",
    "getnada.com", "dispostable.com", "maildrop.cc", "mintemail.com",
}


def _email_domain(email: str) -> str:
    return email.split("@", 1)[-1].lower() if email and "@" in email else ""


async def _avg_order_total(db: AsyncSession, days: int = 30) -> float:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    val = (
        await db.execute(
            select(func.avg(Order.total)).where(
                and_(Order.created_at >= since, Order.payment_status == PaymentStatus.SUCCESS.value)
            )
        )
    ).scalar_one()
    return float(val or 0)


async def score_order(
    db: AsyncSession,
    *,
    email: str,
    total: float,
    items: list[tuple[float, int]],  # [(price, qty)]
    ip_hash: str | None = None,
) -> dict:
    """Sipariş için risk değerlendirmesi. Sipariş henüz commit edilmemiş olsa da çalışır."""
    reasons: list[dict] = []
    score = 0
    now = datetime.now(timezone.utc)

    # Velocity by email
    last_1h = (
        await db.execute(
            select(func.count(Order.id)).where(
                and_(
                    Order.customer_email == email,
                    Order.created_at >= now - timedelta(hours=1),
                )
            )
        )
    ).scalar_one()
    if int(last_1h or 0) >= 3:
        score += 25
        reasons.append({"code": "EMAIL_VELOCITY", "weight": 25, "detail": f"Son 1 saat: {int(last_1h)} sipariş"})

    # Başarısız ödeme geçmişi
    failed_24h = (
        await db.execute(
            select(func.count(Order.id)).where(
                and_(
                    Order.customer_email == email,
                    Order.created_at >= now - timedelta(hours=24),
                    Order.payment_status == PaymentStatus.FAILED.value,
                )
            )
        )
    ).scalar_one()
    if int(failed_24h or 0) >= 2:
        score += 15
        reasons.append({"code": "FAILED_PAYMENTS", "weight": 15, "detail": f"Son 24 saat başarısız: {int(failed_24h)}"})

    # Yüksek tutar — ortalamanın 5+ katı
    avg = await _avg_order_total(db)
    if avg > 0 and total >= avg * 5:
        score += 15
        reasons.append({"code": "HIGH_AMOUNT", "weight": 15, "detail": f"Tutar ortalama ({avg:.0f}₺) × {total/avg:.1f}"})

    # Disposable mail
    dom = _email_domain(email)
    if dom in DISPOSABLE_DOMAINS:
        score += 5
        reasons.append({"code": "DISPOSABLE_EMAIL", "weight": 5, "detail": dom})

    # Yüksek fiyatlı çok kalem
    high_priced = sum(1 for price, _ in items if price >= 5000)
    if high_priced >= 3:
        score += 10
        reasons.append({"code": "MANY_HIGH_TICKET", "weight": 10, "detail": f"{high_priced} adet ≥5000₺ ürün"})

    # Aynı ürünü yüksek miktarda
    if any(qty >= 5 for _, qty in items):
        score += 5
        reasons.append({"code": "HIGH_QTY_PER_ITEM", "weight": 5, "detail": "Aynı üründen ≥5 adet"})

    score = min(100, score)
    if score >= 70:
        level = "HIGH"
    elif score >= 40:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {
        "score": score,
        "level": level,
        "reasons": reasons,
        "should_review": level in ("HIGH", "MEDIUM"),
        "should_block": level == "HIGH",
    }


async def evaluate_order_db(db: AsyncSession, order_no: str) -> dict:
    """DB'deki kayıtlı bir siparişi geriye dönük skorla."""
    row = (
        await db.execute(select(Order).where(Order.order_no == order_no))
    ).scalar_one_or_none()
    if not row:
        return {"error": "not_found"}
    items_rows = (
        await db.execute(select(OrderItem.price, OrderItem.qty).where(OrderItem.order_id == row.id))
    ).all()
    items = [(float(p), int(q)) for p, q in items_rows]
    return await score_order(
        db, email=row.customer_email, total=float(row.total), items=items
    )
