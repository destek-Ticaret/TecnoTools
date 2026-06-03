"""Algoritma endpoint'leri — admin paneli + bazı public uçlar.

Topluca tek router'da tutuluyor; ileride büyürse alt-router'lara bölünür.

Public:
  GET  /api/algorithms/products/{id}/recommendations  → ilgili ürünler
  POST /api/algorithms/recommendations/personal        → sepet/geçmişe göre
  GET  /api/algorithms/trending                        → trend ürünler
  POST /api/algorithms/shipping/quote                  → kargo hesabı
  GET  /api/algorithms/loyalty/{email}                 → puan/tier (email'le)

Admin:
  GET  /api/algorithms/forecast/stock         → stok forecast + ROP
  GET  /api/algorithms/inventory/abc          → ABC sınıflandırma + Pareto özet
  GET  /api/algorithms/customers/rfm          → RFM segmentleri
  GET  /api/algorithms/customers/clv          → CLV tablosu
  GET  /api/algorithms/customers/churn-risk   → churn riski olanlar
  GET  /api/algorithms/risk/orders            → risk skoru yüksek son siparişler
  GET  /api/algorithms/risk/order/{order_no}  → tek sipariş skoru
  GET  /api/algorithms/reports/heatmap        → satış heatmap
  GET  /api/algorithms/reports/top-products
  GET  /api/algorithms/reports/revenue-by-category
  GET  /api/algorithms/reports/conversion-funnel
  GET  /api/algorithms/reports/timeseries
  GET  /api/algorithms/reports/visitors
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_editor
from app.models import Order, PaymentStatus, Product, User
from app.services import analytics_reports, forecasting, loyalty, recommendations, risk, segmentation, shipping

router = APIRouter(prefix="/api/algorithms", tags=["algorithms"])


# ───────────────────────── ÖNERİ ─────────────────────────
class ProductLite(BaseModel):
    id: int
    name: str
    price: float
    icon: str | None = None
    image: str | None = None
    rating: float = 0
    score: float | None = None


async def _hydrate_products(db: AsyncSession, ids: list[int]) -> list[Product]:
    if not ids:
        return []
    rows = (
        await db.execute(select(Product).where(Product.id.in_(ids), Product.is_active == True))  # noqa: E712
    ).scalars().unique().all()
    by_id = {p.id: p for p in rows}
    return [by_id[i] for i in ids if i in by_id]


def _lite(p: Product, score: float | None = None) -> ProductLite:
    return ProductLite(
        id=p.id, name=p.name, price=float(p.price), icon=p.icon,
        image=(p.images or [None])[0] if p.images else None,
        rating=float(p.rating or 0), score=score,
    )


@router.get("/products/{product_id}/recommendations", response_model=list[ProductLite])
async def get_related_products(
    product_id: int, limit: int = Query(8, ge=1, le=24), db: AsyncSession = Depends(get_db)
):
    """Ürün detay sayfasında "ilgili ürünler". Co-occurrence + içerik fallback."""
    rel = await recommendations.related_products(db, product_id, limit=limit)
    ids = [pid for pid, _ in rel]
    if len(ids) < limit:
        fallback = await recommendations.content_similar(db, product_id, limit=limit - len(ids))
        ids.extend([i for i in fallback if i not in ids])
    products = await _hydrate_products(db, ids)
    score_map = dict(rel)
    return [_lite(p, score=score_map.get(p.id)) for p in products]


class PersonalRecRequest(BaseModel):
    viewed_pids: list[int] | None = None
    basket_pids: list[int] | None = None
    limit: int = 8


@router.post("/recommendations/personal", response_model=list[ProductLite])
async def personal_recommendations(payload: PersonalRecRequest, db: AsyncSession = Depends(get_db)):
    ids = await recommendations.recommend_for_session(
        db,
        viewed_pids=payload.viewed_pids,
        basket_pids=payload.basket_pids,
        limit=max(1, min(payload.limit, 24)),
    )
    products = await _hydrate_products(db, ids)
    return [_lite(p) for p in products]


@router.get("/trending", response_model=list[ProductLite])
async def get_trending(days: int = 14, limit: int = 12, db: AsyncSession = Depends(get_db)):
    rows = await recommendations.trending_products(db, days=days, limit=min(limit, 24))
    ids = [pid for pid, _ in rows]
    products = await _hydrate_products(db, ids)
    score_map = dict(rows)
    return [_lite(p, score=score_map.get(p.id)) for p in products]


# ───────────────────────── KARGO ─────────────────────────
class ShippingQuoteRequest(BaseModel):
    city: str | None = None
    subtotal: float = Field(ge=0)
    item_count: int = Field(default=1, ge=1)
    heavy_item_count: int = 0


@router.post("/shipping/quote")
async def shipping_quote(payload: ShippingQuoteRequest, db: AsyncSession = Depends(get_db)):
    from app.routers.settings import get_setting
    threshold = float(await get_setting(db, "shipping_free_threshold", "750") or "750")
    fee_override_raw = await get_setting(db, "shipping_fee_default", "")
    fee_override = float(fee_override_raw) if fee_override_raw else None
    result = shipping.calc_shipping(
        city=payload.city,
        subtotal=payload.subtotal,
        free_threshold=threshold,
        default_fee_override=fee_override,
        item_count=payload.item_count,
        heavy_item_count=payload.heavy_item_count,
    )
    result["upsell_message"] = shipping.free_shipping_message(result["remaining_for_free"])
    return result


# ───────────────────────── LOYALTY ─────────────────────────
@router.get("/loyalty/{email}")
async def loyalty_status(email: EmailStr, db: AsyncSession = Depends(get_db)):
    acc = await loyalty.loyalty_for_email(db, str(email))
    if not acc:
        return {
            "email": str(email), "points": 0, "points_value_try": 0,
            "lifetime_spend": 0, "annual_spend": 0, "tier": "Bronze",
            "next_tier": "Silver", "next_tier_remaining": 1000,
        }
    return acc.__dict__


class RedeemPreview(BaseModel):
    points: int = Field(ge=0)
    subtotal: float = Field(ge=0)


@router.post("/loyalty/redeem-preview")
async def loyalty_redeem_preview(payload: RedeemPreview):
    disc = loyalty.redeem_points_to_discount(payload.points, subtotal=payload.subtotal)
    return {"applied_discount_try": disc, "points_used": payload.points}


# ───────────────────────── FORECAST / ENVANTER ─────────────────────────
@router.get("/forecast/stock")
async def get_stock_forecast(
    days_window: int = Query(30, ge=7, le=180),
    lead_time_days: int = Query(7, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    return await forecasting.stock_forecast(db, days_window=days_window, lead_time_days=lead_time_days)


@router.get("/inventory/abc")
async def get_abc(
    days: int = Query(90, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    rows = await forecasting.abc_classification(db, days=days)
    return {"rows": rows, "summary": forecasting.pareto_summary(rows)}


# ───────────────────────── MÜŞTERİ SEGMENT ─────────────────────────
@router.get("/customers/rfm")
async def get_rfm(db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    stats = await segmentation.collect_customer_stats(db)
    rfm = segmentation.rfm_segments(stats)
    dist = segmentation.segment_distribution(rfm)
    return {"customers": rfm, "distribution": dist}


@router.get("/customers/clv")
async def get_clv(db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    stats = await segmentation.collect_customer_stats(db)
    return segmentation.clv_table(stats)


@router.get("/customers/churn-risk")
async def get_churn_risk(
    days_threshold: int = Query(120, ge=30, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    return await segmentation.churn_risk(db, days_threshold=days_threshold)


# ───────────────────────── RISK ─────────────────────────
@router.get("/risk/orders")
async def list_risky_orders(
    days: int = Query(7, ge=1, le=60),
    min_score: int = Query(40, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    orders = (
        await db.execute(
            select(Order).where(Order.created_at >= since).order_by(Order.created_at.desc()).limit(500)
        )
    ).scalars().unique().all()
    out = []
    for o in orders:
        res = await risk.evaluate_order_db(db, o.order_no)
        if res.get("score", 0) >= min_score:
            out.append({
                "order_no": o.order_no,
                "customer_email": o.customer_email,
                "total": float(o.total),
                "created_at": o.created_at,
                "payment_status": o.payment_status,
                **res,
            })
    out.sort(key=lambda r: r.get("score", 0), reverse=True)
    return out


@router.get("/risk/order/{order_no}")
async def risk_for_order(
    order_no: str, db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)
):
    res = await risk.evaluate_order_db(db, order_no)
    if res.get("error") == "not_found":
        raise HTTPException(404, "Sipariş bulunamadı")
    return res


# ───────────────────────── RAPORLAR ─────────────────────────
@router.get("/reports/heatmap")
async def report_heatmap(
    days: int = Query(30, ge=7, le=365),
    mode: str = Query("count", pattern="^(count|revenue)$"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    return await analytics_reports.sales_heatmap(db, days=days, mode=mode)


@router.get("/reports/top-products")
async def report_top_products(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    return await analytics_reports.top_products(db, days=days, limit=limit)


@router.get("/reports/revenue-by-category")
async def report_revenue_by_category(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    return await analytics_reports.revenue_by_category(db, days=days)


@router.get("/reports/conversion-funnel")
async def report_funnel(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    return await analytics_reports.conversion_funnel(db, days=days)


@router.get("/reports/timeseries")
async def report_timeseries(
    days: int = Query(30, ge=7, le=365),
    ma_window: int = Query(7, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    return await analytics_reports.sales_timeseries(db, days=days, ma_window=ma_window)


@router.get("/reports/visitors")
async def report_visitors(db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    return await analytics_reports.dau_mau(db)
