"""Sipariş bazlı stok düşme / geri ekleme — idempotent (çift düşme/ekleme yok)
ve eşzamanlı çağrılara karşı satır kilidiyle korunur.

Kural: bir sipariş için "net satış" = sale hareketi sayısı - (cancel+return)
hareketi sayısı. `deduct_stock_once` net satış > 0 ise no-op (zaten düşülmüş),
`restore_stock_once` net satış <= 0 ise no-op (geri eklenecek bir şey yok).
Bu sayede iptal → tekrar onay döngüsünde stok doğru şekilde tekrar düşer
(basit "sale hareketi var mı" kontrolü bunu engelliyordu).

Eşzamanlılık: her çağrı önce ilgili Order satırını `FOR UPDATE` ile kilitler
(aynı sipariş için yarışan iki çağrı serileşir — webhook retry + admin onayı
çakışması çift düşmeye yol açamaz) ve hedef Product/ProductVariant satırlarını
da kilitleyerek farklı siparişlerin aynı ürünü eşzamanlı güncellemesinden
kaynaklanan "lost update" (kaybolan güncelleme) riskini kapatır. Postgres'te
tam etkilidir; SQLite bu kilidi desteklemediğinden (dev/test) no-op geçer.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.models import Order, Product, ProductVariant, StockMovement


async def _lock_order(db: AsyncSession, order: Order) -> None:
    """Aynı order_no için eşzamanlı deduct/restore çağrılarını serileştirir."""
    await db.execute(select(Order.id).where(Order.id == order.id).with_for_update())


async def _movement_count(db: AsyncSession, order_no: str, reasons: tuple[str, ...]) -> int:
    return (
        await db.execute(
            select(func.count(StockMovement.id)).where(
                StockMovement.order_no == order_no, StockMovement.reason.in_(reasons)
            )
        )
    ).scalar_one()


async def _load_targets(
    db: AsyncSession, order: Order
) -> tuple[dict[int, Product], dict[int, ProductVariant]]:
    """Sipariş kalemlerinin hedeflerini yükle: varyantlı kalem → ProductVariant,
    varyantsız kalem → Product. Satırlar FOR UPDATE ile kilitlenir (lost-update
    önleme); id sırasına göre seçilerek çapraz siparişler arasında deadlock
    riski azaltılır."""
    product_ids = sorted({it.product_id for it in order.items if it.product_id and not it.variant_id})
    variant_ids = sorted({it.variant_id for it in order.items if it.variant_id})
    pmap: dict[int, Product] = {}
    if product_ids:
        # NOT: Product.category ilişkisi lazy="joined" — varsayılan sorgu bir LEFT
        # OUTER JOIN üretir ve Postgres bunu FOR UPDATE ile REDDEDER ("FOR UPDATE
        # cannot be applied to the nullable side of an outer join"). category
        # burada kullanılmadığından noload ile bu join'i devre dışı bırakıyoruz.
        rows = (
            (
                await db.execute(
                    select(Product)
                    .where(Product.id.in_(product_ids))
                    .order_by(Product.id)
                    .options(noload(Product.category))
                    .with_for_update()
                )
            )
            .scalars()
            .unique()
            .all()
        )
        pmap = {p.id: p for p in rows}
    vmap: dict[int, ProductVariant] = {}
    if variant_ids:
        vrows = (
            (
                await db.execute(
                    select(ProductVariant)
                    .where(ProductVariant.id.in_(variant_ids))
                    .order_by(ProductVariant.id)
                    .with_for_update()
                )
            )
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
        tgt: Product | ProductVariant | None
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
    """Sipariş kalemleri için stoğu (varyantlıysa varyant, değilse ürün) düş —
    net satış (sale - cancel/return) zaten pozitifse no-op (çift düşme yok).
    İptal edilip tekrar onaylanan bir sipariş için doğru şekilde tekrar düşer.
    Returns: bu çağrıda fiilen düşüldüyse True."""
    await _lock_order(db, order)
    sold = await _movement_count(db, order.order_no, ("sale",))
    restored = await _movement_count(db, order.order_no, ("cancel", "return"))
    if sold > restored:
        return False
    return await _apply(db, order, delta_sign=-1, reason="sale")


async def restore_stock_once(db: AsyncSession, order: Order) -> bool:
    """İptal: daha önce düşülmüş (ve henüz geri eklenmemiş) stoğu geri ekle.
    Net satış <= 0 ise (hiç düşülmemiş ya da zaten geri eklenmiş) no-op.
    Returns: fiilen geri eklendiyse True."""
    await _lock_order(db, order)
    sold = await _movement_count(db, order.order_no, ("sale",))
    restored = await _movement_count(db, order.order_no, ("cancel", "return"))
    if sold <= restored:
        return False
    return await _apply(db, order, delta_sign=1, reason="cancel")
