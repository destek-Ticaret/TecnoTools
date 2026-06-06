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

from app.models import Order, Product, ProductVariant, StockMovement


async def _has_movement(db: AsyncSession, order_no: str, reasons: tuple[str, ...]) -> bool:
    row = (
        await db.execute(
            select(StockMovement.id)
            .where(StockMovement.order_no == order_no, StockMovement.reason.in_(reasons))
            .limit(1)
        )
    ).first()
    return row is not None


async def _load_targets(
    db: AsyncSession, order: Order
) -> tuple[dict[int, Product], dict[int, ProductVariant]]:
    """Sipariş kalemlerinin hedeflerini yükle: varyantlı kalem → ProductVariant,
    varyantsız kalem → Product."""
    product_ids = [it.product_id for it in order.items if it.product_id and not it.variant_id]
    variant_ids = [it.variant_id for it in order.items if it.variant_id]
    pmap: dict[int, Product] = {}
    if product_ids:
        rows = (
            (await db.execute(select(Product).where(Product.id.in_(product_ids))))
            .scalars()
            .unique()
            .all()
        )
        pmap = {p.id: p for p in rows}
    vmap: dict[int, ProductVariant] = {}
    if variant_ids:
        vrows = (
            (await db.execute(select(ProductVariant).where(ProductVariant.id.in_(variant_ids))))
            .scalars()
            .unique()
            .all()
        )
        vmap = {v.id: v for v in vrows}
    return pmap, vmap


async def _apply(db: AsyncSession, order: Order, *, delta_sign: int, reason: str) -> bool:
    """Her kalem için hedef stoğu delta_sign yönünde değiştir + StockMovement yaz.
    delta_sign=-1 düş (0'ın altına inmez), +1 geri ekle."""
    pmap, vmap = await _load_targets(db, order)
    changed = False
    for it in order.items:
        if it.variant_id:
            tgt = vmap.get(it.variant_id)
            name = f"{it.name} ({it.variant_name})" if it.variant_name else it.name
        else:
            tgt = pmap.get(it.product_id) if it.product_id else None
            name = it.name
        if tgt is None:
            continue
        if delta_sign < 0:
            tgt.stock = max(0, (tgt.stock or 0) - it.qty)
        else:
            tgt.stock = (tgt.stock or 0) + it.qty
        db.add(
            StockMovement(
                product_id=it.product_id,
                product_name=name,
                delta=delta_sign * it.qty,
                reason=reason,
                order_no=order.order_no,
            )
        )
        changed = True
    return changed


async def deduct_stock_once(db: AsyncSession, order: Order) -> bool:
    """Sipariş kalemleri için stoğu (varyantlıysa varyant, değilse ürün) BİR KEZ
    düş. Zaten düşülmüşse no-op. Returns: bu çağrıda fiilen düşüldüyse True."""
    if await _has_movement(db, order.order_no, ("sale",)):
        return False
    return await _apply(db, order, delta_sign=-1, reason="sale")


async def restore_stock_once(db: AsyncSession, order: Order) -> bool:
    """İptal: daha önce düşülmüş stoğu BİR KEZ geri ekle. Düşülmemiş ya da zaten
    geri eklenmiş (cancel/return) ise no-op. Returns: fiilen geri eklendiyse True."""
    if not await _has_movement(db, order.order_no, ("sale",)):
        return False
    if await _has_movement(db, order.order_no, ("cancel", "return")):
        return False
    return await _apply(db, order, delta_sign=1, reason="cancel")
