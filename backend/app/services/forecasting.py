"""Stok tahmini, ABC sınıflandırma, Pareto analizi.

Algoritmalar:
  * velocity_per_day: son N gün satışlarının basit hareketli ortalaması (SMA)
                       + opsiyonel exponential smoothing (EMA, alpha=0.3).
  * days_to_stockout: mevcut stok / velocity. velocity == 0 → ∞ (None).
  * reorder_point (ROP) = lead_time_days * velocity + safety_stock
        safety_stock = z * stddev * sqrt(lead_time_days)   (z=1.65, %95)
  * abc_classification: gelire göre A (%80), B (%15), C (%5) — kümülatif Pareto.

Hiçbir 3rd-party ML kullanılmaz; sadece istatistik primitifleri.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, OrderItem, PaymentStatus, Product

DEFAULT_LEAD_TIME_DAYS = 7
DEFAULT_SERVICE_LEVEL_Z = 1.65  # %95


async def _daily_sales(db: AsyncSession, days: int) -> dict[int, dict[str, int]]:
    """{product_id: {YYYY-MM-DD: qty}} son `days` gün için."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            select(OrderItem.product_id, OrderItem.qty, Order.created_at)
            .join(Order, OrderItem.order_id == Order.id)
            .where(
                and_(
                    Order.created_at >= since,
                    Order.payment_status == PaymentStatus.SUCCESS.value,
                    OrderItem.product_id.isnot(None),
                )
            )
        )
    ).all()
    out: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for pid, qty, created_at in rows:
        day = created_at.strftime("%Y-%m-%d")
        out[int(pid)][day] += int(qty)
    return out


def _sma(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _ema(values: list[float], alpha: float = 0.3) -> float:
    if not values:
        return 0.0
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _series_for(product_id: int, days: int, daily: dict[int, dict[str, int]]) -> list[float]:
    """0-pad'li günlük seri (eksik gün = 0 satış)."""
    today = datetime.now(timezone.utc).date()
    series: list[float] = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        series.append(float(daily.get(product_id, {}).get(day, 0)))
    return series


async def stock_forecast(
    db: AsyncSession,
    days_window: int = 30,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    service_z: float = DEFAULT_SERVICE_LEVEL_Z,
) -> list[dict]:
    """Her ürün için forecast satırı döndür.

    Sıralama: kritik olanlar başta (days_to_stockout < lead_time).
    """
    products = (
        await db.execute(select(Product).where(Product.is_active == True))  # noqa: E712
    ).scalars().unique().all()
    daily = await _daily_sales(db, days_window)
    out: list[dict] = []
    for p in products:
        series = _series_for(p.id, days_window, daily)
        sma = _sma(series)
        ema = _ema(series)
        velocity = max(sma, ema)  # iki tahminin daha yüksek olanı (güvenli taraf)
        sigma = _stddev(series)
        safety_stock = service_z * sigma * math.sqrt(max(lead_time_days, 1))
        rop = velocity * lead_time_days + safety_stock
        stock = int(p.stock or 0)
        if velocity > 0:
            dto = stock / velocity
        else:
            dto = None
        below_rop = stock < rop
        out.append({
            "product_id": p.id,
            "name": p.name,
            "stock": stock,
            "velocity_per_day": round(velocity, 3),
            "sma": round(sma, 3),
            "ema": round(ema, 3),
            "stddev": round(sigma, 3),
            "reorder_point": round(rop, 2),
            "safety_stock": round(safety_stock, 2),
            "days_to_stockout": round(dto, 1) if dto is not None else None,
            "below_rop": bool(below_rop),
            "lead_time_days": lead_time_days,
            "suggested_purchase_qty": max(0, math.ceil(rop - stock + velocity * lead_time_days))
            if below_rop else 0,
        })
    out.sort(key=lambda r: (
        0 if r["below_rop"] else 1,
        r["days_to_stockout"] if r["days_to_stockout"] is not None else 1e9,
    ))
    return out


async def abc_classification(db: AsyncSession, days: int = 90) -> list[dict]:
    """Ürünleri gelire göre A/B/C sınıflarına ayır (kümülatif Pareto).

    A: ilk %80 ciro
    B: sonraki %15 (yani kümülatif %80..%95)
    C: kalan %5
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            select(
                OrderItem.product_id,
                func.coalesce(func.sum(OrderItem.price * OrderItem.qty), 0).label("revenue"),
                func.coalesce(func.sum(OrderItem.qty), 0).label("qty"),
                func.max(OrderItem.name).label("name"),
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
        )
    ).all()
    if not rows:
        return []
    data = [
        {"product_id": int(pid), "name": name, "revenue": float(rev), "qty": int(qty)}
        for pid, rev, qty, name in rows
    ]
    data.sort(key=lambda r: r["revenue"], reverse=True)
    total = sum(d["revenue"] for d in data) or 1.0
    cum = 0.0
    for i, d in enumerate(data):
        cum += d["revenue"]
        share = cum / total
        d["cum_share"] = round(share, 4)
        if share <= 0.80:
            d["class"] = "A"
        elif share <= 0.95:
            d["class"] = "B"
        else:
            d["class"] = "C"
        d["rank"] = i + 1
        d["revenue_pct"] = round(100 * d["revenue"] / total, 2)
    return data


def pareto_summary(abc_rows: list[dict]) -> dict:
    """ABC tablosundan sınıf bazlı özet (sayı, ciro, % pay)."""
    if not abc_rows:
        return {"A": {}, "B": {}, "C": {}}
    total = sum(r["revenue"] for r in abc_rows) or 1.0
    out: dict[str, dict] = {}
    for cls in ("A", "B", "C"):
        sub = [r for r in abc_rows if r["class"] == cls]
        out[cls] = {
            "count": len(sub),
            "revenue": round(sum(r["revenue"] for r in sub), 2),
            "share": round(100 * sum(r["revenue"] for r in sub) / total, 2),
            "product_ids": [r["product_id"] for r in sub],
        }
    return out


def linear_forecast(series: list[float], horizon_days: int = 14) -> list[float]:
    """Basit linear regression ile gelecek tahmini (least squares).

    series: günlük satış. Dönüş: horizon_days kadar tahmin satışı (>=0).
    """
    n = len(series)
    if n < 2:
        return [series[-1] if series else 0.0] * horizon_days
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(series) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, series))
    den = sum((x - mean_x) ** 2 for x in xs) or 1.0
    slope = num / den
    intercept = mean_y - slope * mean_x
    return [max(0.0, slope * (n + i) + intercept) for i in range(horizon_days)]
