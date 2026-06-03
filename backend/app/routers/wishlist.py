"""Müşteri favorileri (wishlist) — cihazlar arası senkron.

Tümü müşteri üyelik token'ı (`current_customer`) ister. Üye olmayan ziyaretçinin
favorileri frontend'de localStorage'da kalır; giriş yapınca `merge` ile taşınır.

Endpoint'ler:
  GET    /api/wishlist            — favori ürünlerin tam listesi
  POST   /api/wishlist/{pid}      — favoriye ekle (idempotent)
  DELETE /api/wishlist/{pid}      — favoriden çıkar
  POST   /api/wishlist/merge      — localStorage favorilerini topluca taşı

Fiyat düşüşü bildirimi için `notify_price_drop(db, product_id, old_price)`
products router'ından çağrılır.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import current_customer
from app.models import Customer, Product, WishlistItem
from app.schemas import ProductOut
from app.services.email import send_email

router = APIRouter(prefix="/api/wishlist", tags=["wishlist"])


class MergeIn(BaseModel):
    product_ids: list[int] = []


async def _wishlist_products(db: AsyncSession, customer_id: int) -> list[Product]:
    rows = (
        await db.execute(
            select(Product)
            .join(WishlistItem, WishlistItem.product_id == Product.id)
            .where(WishlistItem.customer_id == customer_id)
            .where(Product.is_active.is_(True))
            .order_by(WishlistItem.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


@router.get("", response_model=list[ProductOut])
async def list_wishlist(
    db: AsyncSession = Depends(get_db), customer: Customer = Depends(current_customer)
):
    return await _wishlist_products(db, customer.id)


@router.post("/merge", response_model=list[ProductOut])
async def merge_wishlist(
    payload: MergeIn,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    """localStorage'daki favori product_id listesini sunucuya ekler (idempotent),
    güncel birleşik listeyi döner.

    NOT: Bu statik route, `/{product_id}` parametrik route'undan ÖNCE tanımlı
    olmalı; aksi halde "merge" bir product_id sanılır.
    """
    ids = {int(i) for i in payload.product_ids if isinstance(i, (int, float)) or str(i).isdigit()}
    if ids:
        # Sadece var olan ürünler
        valid = set(
            (await db.execute(select(Product.id).where(Product.id.in_(ids)))).scalars().all()
        )
        existing = set(
            (
                await db.execute(
                    select(WishlistItem.product_id).where(
                        WishlistItem.customer_id == customer.id
                    )
                )
            ).scalars().all()
        )
        for pid in valid - existing:
            db.add(WishlistItem(customer_id=customer.id, product_id=pid))
        if valid - existing:
            await db.commit()
    return await _wishlist_products(db, customer.id)


@router.post("/{product_id}", status_code=201)
async def add_wishlist(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    p = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    existing = (
        await db.execute(
            select(WishlistItem).where(
                (WishlistItem.customer_id == customer.id)
                & (WishlistItem.product_id == product_id)
            )
        )
    ).scalar_one_or_none()
    if existing:
        return {"ok": True, "already": True}
    db.add(WishlistItem(customer_id=customer.id, product_id=product_id))
    await db.commit()
    return {"ok": True, "already": False}


@router.delete("/{product_id}")
async def remove_wishlist(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    await db.execute(
        delete(WishlistItem).where(
            (WishlistItem.customer_id == customer.id)
            & (WishlistItem.product_id == product_id)
        )
    )
    await db.commit()
    return {"ok": True}


async def notify_price_drop(db: AsyncSession, product_id: int, old_price: float) -> int:
    """Ürünün fiyatı düştüğünde favorileyen müşterilere e-posta gönderir.

    Kaç müşteriye gönderildiğini döner. Hata toleranslı (tek tek try/except).
    """
    p = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not p or old_price is None:
        return 0
    new_price = float(p.price or 0)
    if new_price <= 0 or new_price >= float(old_price):
        return 0
    emails = (
        await db.execute(
            select(Customer.email)
            .join(WishlistItem, WishlistItem.customer_id == Customer.id)
            .where(WishlistItem.product_id == product_id)
            .where(Customer.is_active.is_(True))
        )
    ).scalars().all()
    if not emails:
        return 0
    pct = round((1 - new_price / float(old_price)) * 100)
    html = f"""
    <h2>Favorin indirimde! 🎉</h2>
    <p>Favori listendeki <strong>{p.name}</strong> ürününün fiyatı düştü.</p>
    <p style="font-size:1.1rem;">
      <span style="text-decoration:line-through;color:#888;">{float(old_price):.2f} ₺</span>
      &nbsp;→&nbsp;
      <strong style="color:#16a34a;">{new_price:.2f} ₺</strong>
      &nbsp;<span style="background:#16a34a;color:#fff;padding:2px 8px;border-radius:6px;font-size:.85rem;">%{pct} indirim</span>
    </p>
    <a href="#" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 22px;border-radius:9px;text-decoration:none;font-weight:600;margin-top:14px;">Ürüne Git</a>
    """
    sent = 0
    for email in set(emails):
        try:
            if await send_email(to=email, subject=f"💸 {p.name} indirimde!", html=html):
                sent += 1
        except Exception:
            pass
    return sent
