"""Müşteri segmentasyonu — RFM, CLV, kohort analizi.

RFM:
  - R (Recency): son sipariş tarihinden bugüne kadar geçen gün
  - F (Frequency): son 365 gün toplam başarılı sipariş sayısı
  - M (Monetary): son 365 gün toplam harcama (TRY)

Her boyut 1..5 quintile skor. Segment etiketi 3 skorun kombinasyonundan
(Champions, Loyal, Promising, At Risk, Hibernating, Lost ...).

CLV (basit model):
  CLV = AOV × satın alma sıklığı × beklenen yaşam (yıl)
  AOV (Average Order Value) = toplam ciro / sipariş sayısı
  Beklenen yaşam: satın alma aralığının tersine + sabit (default 2 yıl).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, Order, PaymentStatus


class CustomerStats(NamedTuple):
    customer_id: int
    email: str
    name: str
    recency_days: int
    frequency: int
    monetary: float
    first_order: datetime | None
    last_order: datetime | None


async def collect_customer_stats(db: AsyncSession, since_days: int = 365) -> list[CustomerStats]:
    """Tüm müşterilerin satın alma istatistikleri."""
    now = datetime.now(UTC)
    # Tek sorgu, customer üzerinden join.
    stmt = (
        select(
            Customer.id,
            Customer.email,
            Customer.name,
            func.count(Order.id).label("freq"),
            func.coalesce(func.sum(Order.total), 0).label("monetary"),
            func.min(Order.created_at).label("first_order"),
            func.max(Order.created_at).label("last_order"),
        )
        .join(Order, Order.customer_id == Customer.id)
        .where(Order.payment_status == PaymentStatus.SUCCESS.value)
        .group_by(Customer.id, Customer.email, Customer.name)
    )
    rows = (await db.execute(stmt)).all()
    out: list[CustomerStats] = []
    for cid, email, name, freq, monetary, first_o, last_o in rows:
        last_dt = (
            last_o.replace(tzinfo=UTC) if last_o and last_o.tzinfo is None else last_o
        )
        recency = int((now - last_dt).days) if last_dt else 9999
        out.append(
            CustomerStats(
                customer_id=int(cid),
                email=email or "",
                name=name or "",
                recency_days=recency,
                frequency=int(freq or 0),
                monetary=float(monetary or 0),
                first_order=first_o,
                last_order=last_o,
            )
        )
    return out


def _quintile_score(value: float, sorted_values: list[float], reverse: bool = False) -> int:
    """Quintile skor 1..5. reverse=True ise düşük değer = yüksek skor (Recency)."""
    if not sorted_values:
        return 3
    n = len(sorted_values)
    # value'nun sıralı listede konumu
    pos = 0
    for v in sorted_values:
        if v <= value:
            pos += 1
        else:
            break
    rank = pos / n  # 0..1
    if reverse:
        rank = 1 - rank
    if rank <= 0.20:
        return 1
    if rank <= 0.40:
        return 2
    if rank <= 0.60:
        return 3
    if rank <= 0.80:
        return 4
    return 5


def _segment_label(r: int, f: int, m: int) -> str:
    """3 skor → iş segmenti etiketi."""
    fm = (f + m) / 2.0
    if r >= 4 and fm >= 4:
        return "Champions"
    if r >= 3 and fm >= 4:
        return "Loyal"
    if r >= 4 and fm <= 2:
        return "New / Promising"
    if r >= 3 and f <= 2 and m >= 4:
        return "Big Spender"
    if r <= 2 and fm >= 3:
        return "At Risk"
    if r <= 2 and fm <= 2:
        return "Hibernating"
    if r == 1:
        return "Lost"
    return "Needs Attention"


def rfm_segments(stats: list[CustomerStats]) -> list[dict]:
    """Skorları hesapla + segment etiketleri ata."""
    if not stats:
        return []
    r_values = sorted(s.recency_days for s in stats)
    f_values = sorted(s.frequency for s in stats)
    m_values = sorted(s.monetary for s in stats)
    out = []
    for s in stats:
        r = _quintile_score(s.recency_days, r_values, reverse=True)
        f = _quintile_score(s.frequency, f_values)
        m = _quintile_score(s.monetary, m_values)
        out.append(
            {
                "customer_id": s.customer_id,
                "email": s.email,
                "name": s.name,
                "recency_days": s.recency_days,
                "frequency": s.frequency,
                "monetary": round(s.monetary, 2),
                "r_score": r,
                "f_score": f,
                "m_score": m,
                "rfm_code": f"{r}{f}{m}",
                "segment": _segment_label(r, f, m),
            }
        )
    return out


def segment_distribution(rfm_rows: list[dict]) -> list[dict]:
    """Segment başına sayım + toplam harcama özeti."""
    bucket: dict[str, dict] = {}
    for row in rfm_rows:
        seg = row["segment"]
        b = bucket.setdefault(seg, {"segment": seg, "count": 0, "revenue": 0.0})
        b["count"] += 1
        b["revenue"] += row["monetary"]
    out = list(bucket.values())
    for b in out:
        b["revenue"] = round(b["revenue"], 2)
    out.sort(key=lambda x: x["revenue"], reverse=True)
    return out


def customer_clv(s: CustomerStats, expected_lifetime_years: float | None = None) -> float:
    """Basit CLV = AOV × yıllık satın alma sıklığı × beklenen yaşam.

    Müşterinin geçmiş ortalaması varsa onu kullanır; yoksa makul varsayım.
    """
    if s.frequency <= 0 or not s.first_order or not s.last_order:
        return 0.0
    aov = s.monetary / s.frequency
    days_span = max(
        1,
        (
            (
                s.last_order.replace(tzinfo=UTC)
                if s.last_order and s.last_order.tzinfo is None
                else s.last_order
            )
            - (
                s.first_order.replace(tzinfo=UTC)
                if s.first_order and s.first_order.tzinfo is None
                else s.first_order
            )
        ).days,
    )
    annual_freq = s.frequency * 365.0 / days_span
    lifetime = expected_lifetime_years
    if lifetime is None:
        # Aktiflik + frekanstan kabaca tahmin
        if s.recency_days <= 90 and annual_freq >= 3:
            lifetime = 3.0
        elif s.recency_days <= 180:
            lifetime = 2.0
        else:
            lifetime = 1.0
    return round(aov * annual_freq * lifetime, 2)


def clv_table(stats: list[CustomerStats]) -> list[dict]:
    out = []
    for s in stats:
        out.append(
            {
                "customer_id": s.customer_id,
                "email": s.email,
                "name": s.name,
                "orders": s.frequency,
                "total_spend": round(s.monetary, 2),
                "aov": round(s.monetary / s.frequency, 2) if s.frequency else 0.0,
                "clv": customer_clv(s),
            }
        )
    out.sort(key=lambda x: x["clv"], reverse=True)
    return out


async def churn_risk(db: AsyncSession, days_threshold: int = 120) -> list[dict]:
    """Son `days_threshold` günden uzun süredir alışveriş yapmamış aktif müşteriler."""
    stats = await collect_customer_stats(db)
    rfm = rfm_segments(stats)
    return [r for r in rfm if r["recency_days"] >= days_threshold and r["frequency"] >= 2]
