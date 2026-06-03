"""Sadakat puanı + tier hesabı (stateless).

Bütün hesaplar Order tablosu üzerinden anında yapılır; ayrı tablo gerekmez.
Frontend'in kullanması için bir özet endpoint'i `/api/algorithms/loyalty/{email}`.

Kurallar (tier eşiği config'lenebilir):
  * Her başarılı siparişin `total - tax - shipping - discount` (yani net subtotal)
    tutarının ₺1'ı = 1 puan (yuvarlanır).
  * 30 gün önceki puanlar geçerliliğini korur (tüm puanlar 12 ay geçerli).
  * Tier eşikleri (12 aylık ciro ile):
        Bronze:   <  1.000 ₺
        Silver:   <  5.000 ₺
        Gold:     < 15.000 ₺
        Platinum: ≥ 15.000 ₺
  * 100 puan = 10₺ kupon (default kur).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, PaymentStatus

POINT_PER_TRY = 1.0
POINT_VALIDITY_DAYS = 365
POINT_TO_CURRENCY_RATIO = 0.10  # 100 puan = 10₺

TIER_THRESHOLDS = [
    ("Platinum", 15000),
    ("Gold", 5000),
    ("Silver", 1000),
    ("Bronze", 0),
]


@dataclass(frozen=True)
class LoyaltyAccount:
    email: str
    points: int
    points_value_try: float
    lifetime_spend: float
    annual_spend: float
    tier: str
    next_tier: str | None
    next_tier_remaining: float


def _tier_for(annual_spend: float) -> tuple[str, str | None, float]:
    """(tier, next_tier, kalan_tutar). Platinum için next None."""
    sorted_tiers = sorted(TIER_THRESHOLDS, key=lambda t: t[1])  # küçükten büyüğe
    current = sorted_tiers[0][0]
    next_t: str | None = None
    next_remaining = 0.0
    for name, threshold in sorted_tiers:
        if annual_spend >= threshold:
            current = name
        else:
            next_t = name
            next_remaining = round(threshold - annual_spend, 2)
            break
    return current, next_t, next_remaining


async def loyalty_for_email(db: AsyncSession, email: str) -> LoyaltyAccount | None:
    """Email bazlı puan + tier özeti. Sipariş yoksa None döner."""
    rows = (
        await db.execute(
            select(Order.subtotal, Order.discount, Order.created_at).where(
                and_(
                    Order.customer_email == email,
                    Order.payment_status == PaymentStatus.SUCCESS.value,
                )
            )
        )
    ).all()
    if not rows:
        return None
    now = datetime.now(UTC)
    validity_cutoff = now - timedelta(days=POINT_VALIDITY_DAYS)
    annual_cutoff = now - timedelta(days=365)
    points = 0
    lifetime = 0.0
    annual = 0.0
    for sub, disc, created_at in rows:
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        net = max(0.0, float(sub or 0) - float(disc or 0))
        lifetime += net
        if created_at and created_at >= annual_cutoff:
            annual += net
        if created_at and created_at >= validity_cutoff:
            points += int(round(net * POINT_PER_TRY))
    tier, next_tier, remaining = _tier_for(annual)
    return LoyaltyAccount(
        email=email,
        points=points,
        points_value_try=round(points * POINT_TO_CURRENCY_RATIO, 2),
        lifetime_spend=round(lifetime, 2),
        annual_spend=round(annual, 2),
        tier=tier,
        next_tier=next_tier,
        next_tier_remaining=remaining,
    )


def points_for_order_total(*, subtotal: float, discount: float) -> int:
    """Bir sipariş için kazanılacak puanı hesapla (preview)."""
    net = max(0.0, subtotal - discount)
    return int(round(net * POINT_PER_TRY))


def redeem_points_to_discount(
    points: int, max_discount_pct: float = 0.20, subtotal: float = 0.0
) -> float:
    """`points` puanın TRY karşılığı — sepete uygulanabilecek max indirim.

    Güvenlik: en fazla sepet ara toplamının %max_discount_pct kadarı uygulanır.
    """
    if points <= 0:
        return 0.0
    raw = points * POINT_TO_CURRENCY_RATIO
    if subtotal > 0:
        raw = min(raw, subtotal * max_discount_pct)
    return round(raw, 2)
