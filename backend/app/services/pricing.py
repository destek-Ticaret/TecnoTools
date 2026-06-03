"""Otomatik fiyatlandırma motoru.

Bir PricingRule'u eşleşen ürünlere uygular. Önce hedef fiyat hesaplanır,
sonra min/max koruma ve 2 ondalık yuvarlama yapılır. `compute_new_price`
saf fonksiyondur — önizleme (dry-run) ve gerçek uygulama aynı mantığı paylaşır.
"""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PricingRule, Product


def compute_new_price(rule: PricingRule, product: Product) -> float | None:
    """Kuralın ürüne uygulanmış yeni fiyatını döndür; uygulanamıyorsa None.

    None dönen durumlar: margin stratejisinde maliyet (cost) yoksa.
    """
    price = float(product.price or 0)
    value = float(rule.value or 0)
    strategy = rule.strategy

    if strategy == "percent":
        new = price * (1 + value / 100.0)
    elif strategy == "fixed":
        new = price + value
    elif strategy == "margin":
        if product.cost is None:
            return None
        new = float(product.cost) * (1 + value / 100.0)
    elif strategy == "round_99":
        # Bir üst tam sayının .99'una çek (örn 142.30 → 142.99, 142.99 → 142.99)
        whole = int(price)
        new = whole + 0.99
        if new < price:
            new = whole + 1 + 0.99
    else:
        return None

    # Taban / tavan koruma
    if rule.min_price is not None:
        new = max(new, float(rule.min_price))
    if rule.max_price is not None:
        new = min(new, float(rule.max_price))

    new = max(0.0, round(new, 2))
    return new


async def _matching_products(db: AsyncSession, rule: PricingRule) -> list[Product]:
    stmt = select(Product)
    if rule.scope_type == "category":
        stmt = stmt.where(Product.category_id == rule.scope_id)
    elif rule.scope_type == "product":
        stmt = stmt.where(Product.id == rule.scope_id)
    if rule.only_in_stock:
        stmt = stmt.where(Product.stock > 0)
    return list((await db.execute(stmt)).scalars().unique().all())


async def preview_rule(db: AsyncSession, rule: PricingRule, limit: int = 200) -> dict:
    """Kuralı uygulamadan etkisini hesapla (dry-run)."""
    products = await _matching_products(db, rule)
    changes = []
    skipped = 0
    for p in products:
        new = compute_new_price(rule, p)
        if new is None:
            skipped += 1
            continue
        old = float(p.price or 0)
        if new != old:
            changes.append(
                {
                    "product_id": p.id,
                    "name": p.name,
                    "old_price": old,
                    "new_price": new,
                    "delta": round(new - old, 2),
                }
            )
    return {
        "matched": len(products),
        "changed": len(changes),
        "skipped_no_cost": skipped,
        "items": changes[:limit],
    }


async def apply_rule(db: AsyncSession, rule: PricingRule, actor: str = "system") -> dict:
    """Kuralı kalıcı uygula. StockMovement değil — sadece fiyat günceller.

    AuditLog kaydı çağıran router tarafından eklenir. Commit de orada yapılır.
    """
    from datetime import datetime

    products = await _matching_products(db, rule)
    affected = 0
    for p in products:
        new = compute_new_price(rule, p)
        if new is None:
            continue
        if new != float(p.price or 0):
            p.price = new
            affected += 1
    rule.last_applied_at = datetime.now(UTC)
    rule.last_affected = affected
    return {"matched": len(products), "affected": affected}
