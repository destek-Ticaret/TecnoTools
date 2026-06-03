from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin, require_editor
from app.models import AuditLog, NewsletterSubscriber, StockMovement, User
from app.rate_limit import limiter
from app.schemas import AuditOut, NewsletterIn
from app.services.events import bus
from app.services.gib import lookup as gib_lookup

router = APIRouter(prefix="/api", tags=["misc"])


@router.get("/tax/lookup")
@limiter.limit("30/minute")
async def tax_lookup(
    request: Request,
    value: str = Query(..., min_length=10, max_length=20),
    query_gib: bool = Query(default=True),
):
    """VKN/TCKN format doğrulama + (VKN için) GİB mükellef sorgu.

    Frontend: checkout'taki "Vergi No / TCKN" alanında debounce'lu çağrı.
    Yanıt `kind` alanı 'vkn'/'tckn' dönerse UI doğru fatura tipini seçer.
    Sorgu cache'lidir; agresif rate-limit ihtiyacı yok ama bot koruması için
    dakikada 30 sınırı uygulandı.
    """
    result = await gib_lookup(value, query_gib=query_gib)
    return asdict(result)


@router.post("/newsletter", status_code=201)
@limiter.limit("5/minute")
async def subscribe_newsletter(request: Request, payload: NewsletterIn, db: AsyncSession = Depends(get_db)):
    # Honeypot — bot doldurursa görmezden gel (200 dön; bot bilmesin)
    if payload.website:
        return {"ok": True, "already_subscribed": True}
    existing = (
        await db.execute(select(NewsletterSubscriber).where(NewsletterSubscriber.email == payload.email))
    ).scalar_one_or_none()
    if existing:
        return {"ok": True, "already_subscribed": True}
    db.add(NewsletterSubscriber(email=payload.email))
    await db.commit()
    return {"ok": True, "already_subscribed": False}


@router.get("/newsletter/subscribers")
async def list_subscribers(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    rows = (await db.execute(select(NewsletterSubscriber).order_by(NewsletterSubscriber.id.desc()))).scalars().all()
    return [{"id": r.id, "email": r.email, "created_at": r.created_at} for r in rows]


@router.delete("/newsletter/subscribers/{sub_id}", status_code=204)
async def delete_subscriber(sub_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    row = (await db.execute(select(NewsletterSubscriber).where(NewsletterSubscriber.id == sub_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Abone bulunamadı")
    await db.delete(row)
    await db.commit()
    return None


@router.get("/audit", response_model=list[AuditOut])
async def list_audit(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    return (await db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(500))).scalars().all()


@router.delete("/audit", status_code=204)
async def clear_audit(db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    await db.execute(delete(AuditLog))
    db.add(AuditLog(actor=user.username, action="audit-clear", message="Denetim kaydı temizlendi"))
    await db.commit()
    return None


@router.get("/stock-movements")
async def list_stock_movements(db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    rows = (await db.execute(select(StockMovement).order_by(StockMovement.id.desc()).limit(500))).scalars().all()
    return [
        {
            "id": r.id, "product_id": r.product_id, "product_name": r.product_name,
            "delta": r.delta, "reason": r.reason, "order_no": r.order_no, "created_at": r.created_at,
        }
        for r in rows
    ]


@router.delete("/stock-movements", status_code=204)
async def clear_stock_movements(db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    count = (await db.execute(select(StockMovement))).scalars().all()
    n = len(count)
    await db.execute(delete(StockMovement))
    db.add(AuditLog(actor=user.username, action="stock-clear", message=f"Stok hareketleri temizlendi ({n} kayıt)"))
    await db.commit()
    return None


@router.delete("/stock-movements/{movement_id}", status_code=204)
async def delete_stock_movement(movement_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    row = (await db.execute(select(StockMovement).where(StockMovement.id == movement_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Stok hareketi bulunamadı")
    pname = row.product_name
    delta = row.delta
    await db.delete(row)
    db.add(AuditLog(
        actor=user.username, action="stock-delete",
        message=f"Stok hareketi silindi: {pname} ({'+' if delta > 0 else ''}{delta})",
    ))
    await db.commit()
    return None


@router.get("/customers")
async def list_customers(db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    from app.models import Customer, Order
    rows = (await db.execute(select(Customer).order_by(Customer.id.desc()))).scalars().all()
    return [
        {
            "id": r.id, "email": r.email, "name": r.name, "phone": r.phone,
            "city": r.city, "address": r.address, "created_at": r.created_at,
            "is_verified": bool(r.is_verified),
            "is_active": bool(r.is_active),
            "has_account": bool(r.password_hash),
        }
        for r in rows
    ]


@router.delete("/customers/{customer_id}", status_code=204)
async def delete_customer(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Kayıtlı müşteriyi kalıcı olarak sil.

    Bağlı siparişler `customer_id` alanı NULL'a çekilerek korunur (ON DELETE
    SET NULL FK). Müşterinin chat oturumu, yorumlar ve refresh tokenları
    kaskad ile silinir.
    """
    from app.models import Customer
    cust = (
        await db.execute(select(Customer).where(Customer.id == customer_id))
    ).scalar_one_or_none()
    if not cust:
        raise HTTPException(status_code=404, detail="Müşteri bulunamadı")
    email = cust.email
    name = cust.name
    await db.delete(cust)
    db.add(AuditLog(
        actor=user.username, action="customer-delete",
        message=f"Müşteri silindi: {name} <{email}>",
    ))
    await db.commit()
    await bus.publish("customer_deleted", {"id": customer_id, "email": email})
    return None
