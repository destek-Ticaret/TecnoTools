"""Stok geldi bildirimi (back-in-stock).

Public:
  POST /api/products/{pid}/notify-restock  — bekleme listesine kaydol

İç (PATCH /api/products/{id} ile stok 0'dan > 0'a çıktığında):
  scheduler veya product update endpoint'i tetikler, bekleyenlere email gönderir.

Bu modül endpoint + servisi içerir, products router'ı stok güncellemesinde
`notify_restocked(db, product_id)` çağırır.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Product, StockNotification
from app.rate_limit import limiter
from app.services.email import send_email

router = APIRouter(tags=["stock-notifications"])


class NotifyIn(BaseModel):
    email: EmailStr


@router.post("/api/products/{product_id}/notify-restock", status_code=201)
@limiter.limit("5/minute")
async def subscribe_restock(
    request: Request, product_id: int, payload: NotifyIn, db: AsyncSession = Depends(get_db)
):
    p = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    existing = (
        await db.execute(
            select(StockNotification).where(
                (StockNotification.product_id == product_id)
                & (StockNotification.email == payload.email)
                & (StockNotification.notified_at.is_(None))
            )
        )
    ).scalar_one_or_none()
    if existing:
        return {"ok": True, "already_subscribed": True}
    db.add(StockNotification(product_id=product_id, email=payload.email))
    await db.commit()
    return {"ok": True, "already_subscribed": False}


async def notify_restocked(db: AsyncSession, product_id: int) -> int:
    """Stok > 0 olduğunda bekleyen herkese email gönderir, kaç kişiye gittiğini döner."""
    p = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not p or (p.stock or 0) <= 0:
        return 0
    rows = (
        await db.execute(
            select(StockNotification).where(
                (StockNotification.product_id == product_id) & (StockNotification.notified_at.is_(None))
            )
        )
    ).scalars().all()
    if not rows:
        return 0
    html = f"""
    <h2>İyi haber! {p.name} tekrar stokta 🎉</h2>
    <p>Bekleme listesinde olduğunuz <strong>{p.name}</strong> ürünü artık stokta. Birinin daha alması ihtimaline karşı hemen sipariş vermenizi öneririz.</p>
    <a href="#" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 22px;border-radius:9px;text-decoration:none;font-weight:600;margin-top:14px;">Hemen Satın Al</a>
    """
    now = datetime.now(timezone.utc)
    sent = 0
    for r in rows:
        try:
            ok = await send_email(to=r.email, subject=f"📦 {p.name} stokta!", html=html)
            if ok:
                r.notified_at = now
                sent += 1
        except Exception:
            pass
    await db.commit()
    return sent
