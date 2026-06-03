from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import require_editor
from app.models import AuditLog, Product, Reservation, StockMovement, User
from app.schemas import ProductIn, ProductOut, ProductPublicOut
from app.services.currency import convert, get_rate, is_supported
from app.services.events import bus
from app.services.text_utils import fuzzy_score, normalize, slugify

_cfg = get_settings()

router = APIRouter(prefix="/api/products", tags=["products"])


async def _effective_stock_map(
    db: AsyncSession, product_ids: list[int], exclude_session: str | None
) -> dict[int, int]:
    """Verilen ürünler için 'kalan stok' (toplam - başkalarının rezervasyonu)."""
    if not product_ids:
        return {}
    now = datetime.now(UTC)
    q = select(Reservation.product_id, func.coalesce(func.sum(Reservation.qty), 0)).where(
        Reservation.product_id.in_(product_ids), Reservation.expires_at > now
    )
    if exclude_session:
        q = q.where(Reservation.session_id != exclude_session)
    q = q.group_by(Reservation.product_id)
    rows = (await db.execute(q)).all()
    return {pid: int(qty) for pid, qty in rows}


@router.get("", response_model=list[ProductPublicOut])
async def list_products_public(
    db: AsyncSession = Depends(get_db),
    session_id: str | None = Query(None),
    category_id: int | None = Query(None),
    q: str | None = Query(None),
    currency: str | None = Query(None, description="Görüntülenecek para birimi (varsayılan: TRY)"),
):
    stmt = select(Product).where(Product.is_active == True)  # noqa: E712
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    products = (await db.execute(stmt.order_by(Product.id.desc()))).scalars().unique().all()
    reserved = await _effective_stock_map(db, [p.id for p in products], session_id)

    # Kur uygula (BASE_CURRENCY → istenen)
    rate = 1.0
    target_cur = (currency or _cfg.base_currency).upper()
    if target_cur != _cfg.base_currency and is_supported(target_cur):
        rate = await get_rate(_cfg.base_currency, target_cur)

    out = []
    for p in products:
        eff = max(0, (p.stock or 0) - reserved.get(p.id, 0))
        price = convert(float(p.price), rate)
        old_price = convert(float(p.old_price), rate) if p.old_price is not None else None
        out.append(
            ProductPublicOut(
                id=p.id,
                name=p.name,
                sub=p.sub,
                description=p.description,
                icon=p.icon,
                category_id=p.category_id,
                category=p.category,
                price=price,
                old_price=old_price,
                effective_stock=eff,
                rating=float(p.rating or 0),
                review_count=p.review_count or 0,
                badge=p.badge,
                features=p.features,
                images=p.images,
            )
        )
    return out


@router.get("/search/fuzzy")
async def search_products_fuzzy(
    q: str = Query(..., min_length=1, max_length=80),
    limit: int = Query(20, ge=1, le=50),
    min_score: float = Query(0.15, ge=0, le=1),
    category_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Türkçe-bilinçli fuzzy arama. Skor sıralı sonuç döner.

    Strateji:
      1) Tüm aktif ürünleri (optional kategori) hızlı çek.
      2) `fuzzy_score(q, name + sub + features)` ile skorla.
      3) min_score üstünü, skor azalanı sıralı döndür.

    Büyük katalog (10k+) için ileride pg_trgm + GIN index'e taşınabilir.
    """
    stmt = select(Product).where(Product.is_active == True)  # noqa: E712
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    products = (await db.execute(stmt)).scalars().unique().all()

    nq = normalize(q)
    scored: list[tuple[float, Product]] = []
    for p in products:
        # Ad + alt başlık + (varsa) feature kelimeleri tek bir korpus
        haystack_parts = [p.name or "", p.sub or "", " ".join(p.features or [])]
        haystack = " ".join(part for part in haystack_parts if part)
        s = fuzzy_score(q, haystack)
        # Ürün ratingiyle hafif bias (üst eşitliklerde popülere öncelik)
        s += float(p.rating or 0) / 100.0
        if s >= min_score:
            scored.append((s, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    scored = scored[:limit]

    out = []
    for score, p in scored:
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "sub": p.sub,
                "icon": p.icon,
                "category_id": p.category_id,
                "price": float(p.price),
                "old_price": float(p.old_price) if p.old_price is not None else None,
                "stock": p.stock,
                "rating": float(p.rating or 0),
                "image": (p.images or [None])[0] if p.images else None,
                "slug": slugify(p.name),
                "score": round(score, 3),
            }
        )
    return out


@router.get("/search/autocomplete")
async def search_autocomplete(
    q: str = Query(..., min_length=1, max_length=40),
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """Hızlı autocomplete — prefix + substring (case/diakritik bağımsız)."""
    nq = normalize(q)
    if not nq:
        return []
    # Geniş alma; küçük katalog varsayımıyla bellekte filtre.
    rows = (
        await db.execute(
            select(Product.id, Product.name, Product.icon, Product.images)
            .where(
                Product.is_active == True  # noqa: E712
            )
            .limit(500)
        )
    ).all()
    matches: list[tuple[float, dict]] = []
    for pid, name, icon, images in rows:
        nn = normalize(name or "")
        if not nn:
            continue
        if nn.startswith(nq):
            score = 1.0
        elif nq in nn:
            score = 0.7
        else:
            # Kelime başlangıçları
            score = 0.0
            for w in nn.split():
                if w.startswith(nq):
                    score = 0.85
                    break
        if score >= 0.5:
            matches.append(
                (
                    score,
                    {
                        "id": int(pid),
                        "name": name,
                        "icon": icon,
                        "image": (images or [None])[0] if images else None,
                        "slug": slugify(name or ""),
                    },
                )
            )
    matches.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in matches[:limit]]


@router.get("/{product_id}", response_model=ProductPublicOut)
async def get_product_public(
    product_id: int, db: AsyncSession = Depends(get_db), session_id: str | None = Query(None)
):
    p = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not p or not p.is_active:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    reserved = await _effective_stock_map(db, [p.id], session_id)
    eff = max(0, (p.stock or 0) - reserved.get(p.id, 0))
    return ProductPublicOut(
        id=p.id,
        name=p.name,
        sub=p.sub,
        description=p.description,
        icon=p.icon,
        category_id=p.category_id,
        category=p.category,
        price=float(p.price),
        old_price=float(p.old_price) if p.old_price is not None else None,
        effective_stock=eff,
        rating=float(p.rating or 0),
        review_count=p.review_count or 0,
        badge=p.badge,
        features=p.features,
        images=p.images,
    )


# ── ADMIN ──
@router.get("/admin/all", response_model=list[ProductOut])
async def list_products_admin(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)
):
    products = (
        (await db.execute(select(Product).order_by(Product.id.desc()))).scalars().unique().all()
    )
    return products


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)
):
    p = Product(**payload.model_dump())
    db.add(p)
    await db.flush()
    if p.stock > 0:
        db.add(StockMovement(product_id=p.id, product_name=p.name, delta=p.stock, reason="init"))
    db.add(
        AuditLog(
            actor=user.username, action="product-add", message=f"Ürün eklendi: {p.name} (#{p.id})"
        )
    )
    await db.commit()
    await db.refresh(p)
    await bus.publish("product_created", {"id": p.id, "name": p.name})
    return p


@router.put("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: int,
    payload: ProductIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    p = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    old_stock = p.stock or 0
    old_price = float(p.price or 0)
    for k, v in payload.model_dump().items():
        setattr(p, k, v)
    new_stock = p.stock or 0
    new_price = float(p.price or 0)
    if new_stock != old_stock:
        db.add(
            StockMovement(
                product_id=p.id, product_name=p.name, delta=new_stock - old_stock, reason="manual"
            )
        )
    db.add(
        AuditLog(
            actor=user.username,
            action="product-edit",
            message=f"Ürün güncellendi: {p.name} (#{p.id})",
        )
    )
    await db.commit()
    await db.refresh(p)
    await bus.publish("product_updated", {"id": p.id, "name": p.name})
    # 0'dan > 0'a stoğa çıkış → bekleme listesine bildirim
    if old_stock <= 0 and new_stock > 0:
        try:
            from app.routers.stock_notifications import notify_restocked

            await notify_restocked(db, p.id)
        except Exception:
            pass
    # Fiyat düşüşü → favorileyenlere bildirim
    if new_price > 0 and new_price < old_price:
        try:
            from app.routers.wishlist import notify_price_drop

            await notify_price_drop(db, p.id, old_price)
        except Exception:
            pass
    return p


class BulkPriceUpdateIn(BaseModel):
    category_id: int | None = None  # belirtilmezse tüm aktif ürünler
    percent: float | None = None  # örn +10 (zam) veya -15 (indirim)
    fixed_delta: float | None = None  # +50 ₺ ekle / -20 ₺ düş
    only_in_stock: bool = False


@router.post("/bulk/price")
async def bulk_price_update(
    payload: BulkPriceUpdateIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    """Toplu fiyat zammı/indirimi. Yüzdesel veya sabit tutar.

    Örn: { "category_id": 3, "percent": 10 } → kategori 3'teki ürünlere %10 zam.
    """
    if payload.percent is None and payload.fixed_delta is None:
        raise HTTPException(status_code=400, detail="percent veya fixed_delta verilmeli")
    stmt = select(Product).where(Product.is_active == True)  # noqa: E712
    if payload.category_id:
        stmt = stmt.where(Product.category_id == payload.category_id)
    if payload.only_in_stock:
        stmt = stmt.where(Product.stock > 0)
    rows = (await db.execute(stmt)).scalars().unique().all()
    updated = 0
    for p in rows:
        old = float(p.price or 0)
        new = old
        if payload.percent is not None:
            new = old * (1 + payload.percent / 100.0)
        if payload.fixed_delta is not None:
            new = new + payload.fixed_delta
        new = max(0, round(new, 2))
        if new != old:
            p.price = new
            updated += 1
    db.add(
        AuditLog(
            actor=user.username,
            action="product-bulk-price",
            message=f"Toplu fiyat güncellemesi: {updated} ürün etkilendi"
            + (f", %{payload.percent}" if payload.percent else "")
            + (f", {payload.fixed_delta}₺" if payload.fixed_delta else ""),
        )
    )
    await db.commit()
    return {"ok": True, "updated": updated, "matched": len(rows)}


@router.delete("/{product_id}", status_code=204)
async def delete_product(
    product_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)
):
    p = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    name = p.name
    await db.execute(delete(Reservation).where(Reservation.product_id == product_id))
    await db.delete(p)
    db.add(
        AuditLog(
            actor=user.username,
            action="product-delete",
            message=f"Ürün silindi: {name} (#{product_id})",
        )
    )
    await db.commit()
    await bus.publish("product_deleted", {"id": product_id, "name": name})
    return None
