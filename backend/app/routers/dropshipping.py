"""Dropshipping — tedarikçiden ürün kaynaklama (sourcing).

GET  /api/dropshipping/preview?url=...   → tedarikçi ürününü çek + markup'lı taslak (DB değişmez)
POST /api/dropshipping/import            → taslağı mağaza ürünü olarak ekle

Tedarikçi modu settings.supplier_mode ile seçilir (mock | aliexpress).
Yetki: products.import (toplu içe aktarma ile aynı izin).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import require_permission
from app.models import AuditLog, Product, StockMovement, User
from app.services.suppliers import get_supplier
from app.services.suppliers.pricing import build_draft

settings = get_settings()

router = APIRouter(prefix="/api/dropshipping", tags=["dropshipping"])
_can_import = require_permission("products.import")


@router.get("/preview")
async def preview(
    url: str = Query(..., description="Tedarikçi ürün linki veya ID"),
    markup: float | None = Query(None, gt=0, description="Kâr çarpanı (boşsa varsayılan)"),
    _: User = Depends(_can_import),
):
    """Tedarikçi ürününü çekip satış fiyatı hesaplanmış taslağı döndürür (DB'ye yazmaz)."""
    supplier = get_supplier()
    try:
        sp = await supplier.fetch_product(url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Tedarikçi verisi alınamadı: {e}") from e
    draft = await build_draft(sp, markup)
    return {"mode": settings.supplier_mode, "draft": draft}


@router.post("/import")
async def import_one(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_can_import),
    url: str = Body(..., embed=True),
    markup: float | None = Body(None, embed=True),
    category_id: int | None = Body(None, embed=True),
    is_active: bool = Body(False, embed=True),  # varsayılan pasif: admin gözden geçirsin
):
    """Tedarikçi ürününü mağaza ürünü olarak ekler. Aynı supplier_product_id varsa
    çift kayıt engellenir (önce o ürünü güncellemen beklenir)."""
    supplier = get_supplier()
    try:
        sp = await supplier.fetch_product(url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Tedarikçi verisi alınamadı: {e}") from e

    existing = (
        await db.execute(
            select(Product).where(Product.supplier_product_id == sp.supplier_product_id)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Bu tedarikçi ürünü zaten ekli (ürün #{existing.id}: {existing.name})",
        )

    draft = await build_draft(sp, markup)
    product = Product(
        name=draft["name"],
        description=draft["description"],
        category_id=category_id,
        price=draft["price"],
        cost=draft["cost"],
        stock=draft["stock"],
        images=draft["images"] or None,
        features=draft["features"] or None,
        supplier=draft["supplier"],
        supplier_url=draft["supplier_url"],
        supplier_product_id=draft["supplier_product_id"],
        supplier_price=draft["supplier_price"],
        is_active=is_active,
    )
    db.add(product)
    await db.flush()
    if product.stock > 0:
        db.add(
            StockMovement(
                product_id=product.id,
                product_name=product.name,
                delta=product.stock,
                reason="dropship-import",
            )
        )
    db.add(
        AuditLog(
            actor=user.username,
            action="dropship-import",
            message=f"Tedarikçiden ürün eklendi: {product.name} (#{product.id}, {draft['supplier']})",
        )
    )
    await db.commit()
    await db.refresh(product)
    return {"id": product.id, "name": product.name, "price": float(product.price), "draft": draft}
