"""Sipariş bazlı stok düşme / geri ekleme — idempotent (çift düşme/ekleme yok).

Tek kural: bir siparişe ait `reason="sale"` StockMovement varsa stok o sipariş
için zaten düşülmüştür. Tüm onay yolları (PayTR/Stripe callback, havale/kapıda
admin onayı) `deduct_stock_once`'tan geçer → ikinci çağrı no-op olur, hangi yol
önce gelirse gelsin çift düşme imkânsız.

İptal/iade `restore_stock_once` ile stoğu geri ekler (`reason="cancel"`), yine
idempotent: daha önce düşülmemişse veya zaten geri eklenmişse no-op.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, Product, StockMovement


async def _has_movement(db: AsyncSession, order_no: str, reasons: tuple[str, ...]) -> bool:
    row = (
        await db.execute(
            select(StockMovement.id)
            .where(StockMovement.order_no == order_no, StockMovement.reason.in_(reasons))
            .limit(1)
        )
    ).first()
    return row is not None


async def _products_for(db: AsyncSession, order: Order) -> dict[int, Product]:
    item_ids = [it.product_id for it in order.items if it.product_id]
    if not item_ids:
        return {}
    products = (
        (await db.execute(select(Product).where(Product.id.in_(item_ids)))).scalars().unique().all()
    )
    return {p.id: p for p in products}


async def deduct_stock_once(db: AsyncSession, order: Order) -> bool:
    """Sipariş kalemleri için stoğu BİR KEZ düş. Zaten düşülmüşse no-op.

    Returns: bu çağrıda fiilen düşüldüyse True.
    """
    if await _has_movement(db, order.order_no, ("sale",)):
        return False
    pmap = await _products_for(db, order)
    deducted = False
    for it in order.items:
        p = pmap.get(it.product_id) if it.product_id else None
        if not p:
            continue
        p.stock = max(0, (p.stock or 0) - it.qty)
        db.add(
            StockMovement(
                product_id=p.id,
                product_name=p.name,
                delta=-it.qty,
                reason="sale",
                order_no=order.order_no,
            )
        )
        deducted = True
    return deducted


async def restore_stock_once(db: AsyncSession, order: Order) -> bool:
    """İptal: daha önce düşülmüş stoğu BİR KEZ geri ekle. Düşülmemiş ya da zaten
    geri eklenmiş (cancel/return) ise no-op.

    Returns: bu çağrıda fiilen geri eklendiyse True.
    """
    if not await _has_movement(db, order.order_no, ("sale",)):
        return False
    if await _has_movement(db, order.order_no, ("cancel", "return")):
        return False
    pmap = await _products_for(db, order)
    restored = False
    for it in order.items:
        p = pmap.get(it.product_id) if it.product_id else None
        if not p:
            continue
        p.stock = (p.stock or 0) + it.qty
        db.add(
            StockMovement(
                product_id=p.id,
                product_name=p.name,
                delta=it.qty,
                reason="cancel",
                order_no=order.order_no,
            )
        )
        restored = True
    return restored
