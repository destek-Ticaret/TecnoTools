"""Tedarikçi ürünleri için fiyat/stok senkron çekirdeği.

Hem admin endpoint'leri (routers/dropshipping.py) hem de zamanlayıcı
(services/scheduled.py) bu fonksiyonları kullanır.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, StockMovement
from app.services.suppliers import get_supplier
from app.services.suppliers.pricing import compute_sale_price

logger = logging.getLogger(__name__)


async def sync_one(db: AsyncSession, product: Product, reprice: bool) -> dict:
    """Tek ürünü tedarikçiden tazeler: maliyet/stok güncelle, istenirse yeniden fiyatla.

    Commit ETMEZ — çağıran tarafı commit eder.
    """
    supplier = get_supplier()
    sp = await supplier.fetch_product(product.supplier_url or product.supplier_product_id or "")
    sale, cost = await compute_sale_price(sp.supplier_price, sp.currency)

    changes: dict = {}
    if product.supplier_price is None or float(product.supplier_price) != cost:
        changes["cost"] = cost
        product.cost = cost
        product.supplier_price = cost
    old_stock = product.stock or 0
    if sp.stock != old_stock:
        changes["stock"] = sp.stock
        product.stock = sp.stock
        db.add(
            StockMovement(
                product_id=product.id,
                product_name=product.name,
                delta=sp.stock - old_stock,
                reason="dropship-sync",
            )
        )
    if reprice and float(product.price) != sale:
        changes["price"] = sale
        product.price = sale
    product.supplier_synced_at = datetime.now(UTC)
    return {"product_id": product.id, "name": product.name, "changes": changes}


async def sync_all(db: AsyncSession, reprice: bool) -> dict:
    """Tüm tedarikçi (dropship) ürünlerini senkronla. Tek tek hatalar atlanır.
    Commit ETMEZ — çağıran tarafı commit eder."""
    products = (
        (await db.execute(select(Product).where(Product.supplier.is_not(None)))).scalars().all()
    )
    results, errors = [], []
    for p in products:
        try:
            results.append(await sync_one(db, p, reprice))
        except Exception as e:
            logger.warning("dropship sync failed for product %s: %s", p.id, e)
            errors.append({"product_id": p.id, "error": str(e)})
    return {"synced": len(results), "errors": errors, "results": results}
