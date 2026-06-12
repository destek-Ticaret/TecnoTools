"""Satış raporları — admin dashboard'u için agregat sorgular.

İçerik:
  * sales_heatmap          → gün × saat ısı haritası (sipariş sayısı veya ciro)
  * top_products           → en çok ciro yapan N ürün (gün filtresi)
  * revenue_by_category    → kategori başına gelir
  * conversion_funnel      → page_view → add_to_cart → checkout → purchase
  * sales_timeseries       → günlük ciro + 7 gün hareketli ortalama
  * dau_mau                → günlük/aylık tekil ziyaretçi (ip_hash bazlı)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnalyticsEvent, Category, Order, OrderItem, PaymentStatus, Product


async def sales_heatmap(db: AsyncSession, days: int = 30, mode: str = "count") -> list[dict]:
    """Pazartesi=0..Pazar=6 × 0..23 saat matrisi.

    mode="count": sipariş sayısı | mode="revenue": ciro toplamı
    """
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(Order.created_at, Order.total).where(
                and_(
                    Order.created_at >= since,
                    Order.payment_status == PaymentStatus.SUCCESS.value,
                )
            )
        )
    ).all()
    matrix = [[0.0] * 24 for _ in range(7)]
    for created_at, total in rows:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        dow = created_at.weekday()
        hour = created_at.hour
        matrix[dow][hour] += float(total or 0) if mode == "revenue" else 1
    days_tr = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
    out: list[dict] = []
    for dow in range(7):
        for hour in range(24):
            out.append(
                {
                    "day": days_tr[dow],
                    "dow": dow,
                    "hour": hour,
                    "value": round(matrix[dow][hour], 2),
                }
            )
    return out


async def top_products(db: AsyncSession, days: int = 30, limit: int = 10) -> list[dict]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(
                OrderItem.product_id,
                func.max(OrderItem.name).label("name"),
                func.coalesce(func.sum(OrderItem.qty), 0).label("qty"),
                func.coalesce(func.sum(OrderItem.price * OrderItem.qty), 0).label("revenue"),
            )
            .join(Order, OrderItem.order_id == Order.id)
            .where(
                and_(
                    Order.created_at >= since,
                    Order.payment_status == PaymentStatus.SUCCESS.value,
                    OrderItem.product_id.isnot(None),
                )
            )
            .group_by(OrderItem.product_id)
            .order_by(func.sum(OrderItem.price * OrderItem.qty).desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "product_id": int(pid),
            "name": name,
            "qty": int(qty),
            "revenue": round(float(rev), 2),
        }
        for pid, name, qty, rev in rows
    ]


async def revenue_by_category(db: AsyncSession, days: int = 30) -> list[dict]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(
                Category.id,
                Category.name,
                func.coalesce(func.sum(OrderItem.price * OrderItem.qty), 0).label("revenue"),
                func.coalesce(func.sum(OrderItem.qty), 0).label("qty"),
            )
            .select_from(OrderItem)
            .join(Order, OrderItem.order_id == Order.id)
            .join(Product, Product.id == OrderItem.product_id)
            .join(Category, Category.id == Product.category_id, isouter=True)
            .where(
                and_(
                    Order.created_at >= since,
                    Order.payment_status == PaymentStatus.SUCCESS.value,
                )
            )
            .group_by(Category.id, Category.name)
            .order_by(func.sum(OrderItem.price * OrderItem.qty).desc())
        )
    ).all()
    out = []
    for cid, name, rev, qty in rows:
        out.append(
            {
                "category_id": int(cid) if cid else None,
                "category": name or "(Kategorisiz)",
                "revenue": round(float(rev or 0), 2),
                "qty": int(qty or 0),
            }
        )
    total = sum((float(r["revenue"]) for r in out), 0.0) or 1.0  # type: ignore[arg-type]
    for r in out:
        r["share_pct"] = round(100 * float(r["revenue"]) / total, 2)  # type: ignore[arg-type]
    return out


async def conversion_funnel(db: AsyncSession, days: int = 30) -> dict:
    """page_view → add_to_cart → checkout → purchase oranları.

    Self-hosted analytics olayları üzerinden çalışır. purchase olayı yoksa
    Order tablosundan başarılı siparişler sayılır (fallback).
    """
    since = datetime.now(UTC) - timedelta(days=days)
    counts = {"page_view": 0, "add_to_cart": 0, "checkout_started": 0, "purchase": 0}
    rows = (
        await db.execute(
            select(AnalyticsEvent.event, func.count())
            .where(AnalyticsEvent.created_at >= since)
            .group_by(AnalyticsEvent.event)
        )
    ).all()
    for ev, n in rows:
        if ev in counts:
            counts[ev] = int(n or 0)
    if counts["purchase"] == 0:
        # Fallback: gerçek başarılı sipariş sayısı
        n = (
            await db.execute(
                select(func.count(Order.id)).where(
                    and_(
                        Order.created_at >= since,
                        Order.payment_status == PaymentStatus.SUCCESS.value,
                    )
                )
            )
        ).scalar_one()
        counts["purchase"] = int(n or 0)

    def pct(num: int, den: int) -> float:
        return round(100 * num / den, 2) if den else 0.0

    return {
        "days": days,
        "stages": [
            {"name": "Sayfa görüntüleme", "count": counts["page_view"], "share_pct": 100.0},
            {
                "name": "Sepete ekleme",
                "count": counts["add_to_cart"],
                "share_pct": pct(counts["add_to_cart"], counts["page_view"]),
            },
            {
                "name": "Ödeme başlangıç",
                "count": counts["checkout_started"],
                "share_pct": pct(counts["checkout_started"], counts["page_view"]),
            },
            {
                "name": "Satın alma",
                "count": counts["purchase"],
                "share_pct": pct(counts["purchase"], counts["page_view"]),
            },
        ],
        "overall_conversion_pct": pct(counts["purchase"], counts["page_view"]),
    }


async def sales_timeseries(db: AsyncSession, days: int = 30, ma_window: int = 7) -> list[dict]:
    """Günlük ciro + hareketli ortalama (`ma_window` günlük SMA)."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(
                func.date(Order.created_at).label("d"),
                func.coalesce(func.sum(Order.total), 0).label("revenue"),
                func.count(Order.id).label("orders"),
            )
            .where(
                and_(
                    Order.created_at >= since,
                    Order.payment_status == PaymentStatus.SUCCESS.value,
                )
            )
            .group_by(func.date(Order.created_at))
            .order_by(func.date(Order.created_at))
        )
    ).all()
    # Eksik günleri 0 ile doldur
    series_map: dict[str, dict] = {}
    for d, rev, n in rows:
        key = str(d)
        series_map[key] = {"date": key, "revenue": float(rev), "orders": int(n)}
    today = datetime.now(UTC).date()
    full: list[dict] = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        full.append(series_map.get(day, {"date": day, "revenue": 0.0, "orders": 0}))
    # Hareketli ortalama
    window: list[float] = []
    for row in full:
        window.append(row["revenue"])
        if len(window) > ma_window:
            window.pop(0)
        row["revenue_ma"] = round(sum(window) / len(window), 2)
        row["revenue"] = round(row["revenue"], 2)
    return full


async def dau_mau(db: AsyncSession) -> dict:
    """Günlük (24h) ve aylık (30g) tekil ziyaretçi (ip_hash distinct)."""
    now = datetime.now(UTC)
    day_cut = now - timedelta(hours=24)
    month_cut = now - timedelta(days=30)
    dau = (
        await db.execute(
            select(func.count(func.distinct(AnalyticsEvent.ip_hash))).where(
                AnalyticsEvent.created_at >= day_cut
            )
        )
    ).scalar_one()
    mau = (
        await db.execute(
            select(func.count(func.distinct(AnalyticsEvent.ip_hash))).where(
                AnalyticsEvent.created_at >= month_cut
            )
        )
    ).scalar_one()
    return {
        "dau": int(dau or 0),
        "mau": int(mau or 0),
        "stickiness_pct": round(100 * (dau or 0) / (mau or 1), 2),
    }
