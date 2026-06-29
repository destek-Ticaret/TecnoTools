"""Dropshipping — tedarikçiden ürün kaynaklama + sipariş + senkron.

GET  /api/dropshipping/preview?url=...        → tedarikçi ürününü çek + markup'lı taslak (DB değişmez)
POST /api/dropshipping/import                 → taslağı mağaza ürünü olarak ekle
GET  /api/dropshipping/orders/{id}/fulfillment→ siparişin tedarikçi karşılama bilgisi (link+adres)
POST /api/dropshipping/products/{id}/sync     → tek ürünün fiyat/stok senkronu
POST /api/dropshipping/sync                   → tüm tedarikçi ürünlerini senkronla

Tedarikçi modu settings.supplier_mode ile seçilir (mock | aliexpress).
Yetki: products.import (toplu içe aktarma ile aynı izin).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.deps import require_permission
from app.models import AuditLog, Order, Product, StockMovement, User
from app.services.suppliers import get_supplier_for_url
from app.services.suppliers.aliexpress import AliExpressAdapter
from app.services.suppliers.aliexpress_oauth import authorize_url, exchange_code, get_valid_token
from app.services.suppliers.pricing import build_draft
from app.services.suppliers.sync import sync_all as _sync_all_products
from app.services.suppliers.sync import sync_one as _sync_product

settings = get_settings()

router = APIRouter(prefix="/api/dropshipping", tags=["dropshipping"])
_can_import = require_permission("products.import")


async def _supplier_for(url: str, db: AsyncSession):
    """URL'ye göre adapter; AliExpress ise DB'deki geçerli OAuth token'ı enjekte eder."""
    supplier = get_supplier_for_url(url)
    if isinstance(supplier, AliExpressAdapter):
        tok = await get_valid_token(db)
        if tok:
            supplier.access_token = tok
    return supplier


@router.get("/preview")
async def preview(
    url: str = Query(..., description="Tedarikçi ürün linki veya ID"),
    markup: float | None = Query(None, gt=0, description="Kâr çarpanı (boşsa varsayılan)"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_can_import),
):
    """Tedarikçi ürününü çekip satış fiyatı hesaplanmış taslağı döndürür (DB'ye yazmaz)."""
    supplier = await _supplier_for(url, db)
    try:
        sp = await supplier.fetch_product(url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Tedarikçi verisi alınamadı: {e}") from e
    draft = await build_draft(sp, markup)
    return {"mode": settings.supplier_mode, "draft": draft}


@router.get("/oauth/url")
async def oauth_url(_: User = Depends(_can_import)):
    """AliExpress yetkilendirme linkini döndürür. Admin bu linke gidip hesabı yetkilendirir."""
    return {"authorize_url": authorize_url(), "redirect_uri": settings.api_public_url or "https://api.tecnotools.org"}


@router.get("/oauth/exchange")
async def oauth_exchange(
    code: str = Query(..., description="authorize sonrası adres çubuğundaki code"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_can_import),
):
    """authorize sonrası gelen code'u access_token'a çevirip GİZLİ tabloya kaydeder."""
    try:
        data = await exchange_code(db, code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    db.add(AuditLog(actor=user.username, action="aliexpress-oauth", message="AliExpress token alındı"))
    await db.commit()
    return {
        "ok": True,
        "expires_in": data.get("expires_in"),
        "has_refresh": bool(data.get("refresh_token")),
        "note": "Token kaydedildi. Artık Tedarikçiden Ekle gerçek veriyle çalışır.",
    }


@router.get("/debug")
async def debug_raw(
    url: str = Query(..., description="Tedarikçi ürün linki veya ID"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_can_import),
):
    """AliExpress ham API yanıtını döndürür — yalnız alan adlarını doğrulamak için.
    Sadece live modda ve aliexpress kaynağı için çalışır."""
    supplier = await _supplier_for(url, db)
    if not hasattr(supplier, "fetch_raw"):
        return {"note": "Bu kaynak/mod için ham yanıt yok (mock veya 1688).", "mode": settings.supplier_mode}
    try:
        return {"mode": settings.supplier_mode, "raw": await supplier.fetch_raw(url)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


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
    supplier = await _supplier_for(url, db)
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
        market="intl",  # dropship ürünleri yurt dışı pazara
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


@router.get("/orders/{order_id}/fulfillment")
async def order_fulfillment(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_can_import),
):
    """Siparişteki tedarikçi (dropship) kalemlerini, tedarikçiye sipariş vermek için
    gereken bilgilerle döndürür: ürün linki, adet, birim maliyet + teslimat adresi.
    Yarı-otomatik akış: admin bu bilgilerle tedarikçide siparişi açar."""
    order = (
        await db.execute(
            select(Order).where(Order.id == order_id).options(selectinload(Order.items))
        )
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    # Kalemlerin ürünlerini topluca çek
    pids = [it.product_id for it in order.items if it.product_id]
    products = {}
    if pids:
        rows = (await db.execute(select(Product).where(Product.id.in_(pids)))).scalars().all()
        products = {p.id: p for p in rows}

    lines = []
    for it in order.items:
        p = products.get(it.product_id)
        if not p or not p.supplier:
            continue  # kendi stoğumuzdan; dropship değil
        lines.append(
            {
                "product_id": p.id,
                "name": p.name,
                "supplier": p.supplier,
                "supplier_url": p.supplier_url,
                "supplier_product_id": p.supplier_product_id,
                "qty": it.qty,
                "unit_cost": float(p.supplier_price) if p.supplier_price is not None else None,
            }
        )

    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "is_dropship": bool(lines),
        "shipping": {
            "name": order.customer_name,
            "phone": order.customer_phone,
            "city": order.customer_city,
            "address": order.customer_address,
        },
        "lines": lines,
    }


@router.post("/products/{product_id}/sync")
async def sync_product(
    product_id: int,
    reprice: bool = Body(True, embed=True),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_can_import),
):
    """Tek tedarikçi ürününün fiyat/stok bilgisini tedarikçiden tazeler."""
    product = (
        await db.execute(select(Product).where(Product.id == product_id))
    ).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    if not product.supplier:
        raise HTTPException(status_code=400, detail="Bu ürün bir tedarikçiye bağlı değil")
    try:
        result = await _sync_product(db, product, reprice)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Tedarikçi verisi alınamadı: {e}") from e
    db.add(
        AuditLog(
            actor=user.username,
            action="dropship-sync",
            message=f"Tedarikçi senkronu: {product.name} (#{product.id}) {result['changes']}",
        )
    )
    await db.commit()
    return result


@router.post("/sync")
async def sync_all(
    reprice: bool = Body(True, embed=True),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_can_import),
):
    """Tüm tedarikçi (dropship) ürünlerini senkronla. Tek tek hatalar atlanır."""
    out = await _sync_all_products(db, reprice)
    db.add(
        AuditLog(
            actor=user.username,
            action="dropship-sync-all",
            message=f"Toplu tedarikçi senkronu: {out['synced']} ürün, {len(out['errors'])} hata",
        )
    )
    await db.commit()
    return out
