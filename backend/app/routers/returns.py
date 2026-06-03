"""Sipariş iade talebi endpoint'leri.

Public:
  POST /api/returns                    — müşteri talep açar
  GET  /api/returns/lookup?order_no=&email=  — müşteri kendi iadelerini görür
  POST /api/returns/{id}/cancel        — müşteri talebini geri çeker (sadece requested)

Admin:
  GET    /api/returns                  — tüm iade listesi
  GET    /api/returns/{id}             — detay
  PATCH  /api/returns/{id}/status      — approve / reject / mark_refunded

İş kuralları:
- Talep sadece kendi email'iyle sipariş veren kişi açabilir (email karşılaştırması)
- Sipariş statusü `delivered` veya `shipped` değilse iade açılamaz
- İade kalemleri orijinal sipariş kalemlerinden fazla olamaz
- "refunded" durumuna geçince stoğa otomatik geri eklenir + StockMovement(reason=return)
"""
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_editor
from app.models import (
    AuditLog,
    Order,
    OrderStatus,
    Product,
    ReturnRequest,
    ReturnStatus,
    StockMovement,
    User,
)
from app.rate_limit import limiter
from app.schemas import ReturnRequestIn, ReturnRequestOut, ReturnStatusUpdate
from app.services.email import render_template, send_email
from app.services.events import bus

router = APIRouter(prefix="/api/returns", tags=["returns"])

ELIGIBLE_ORDER_STATUSES = {OrderStatus.SHIPPED.value, OrderStatus.DELIVERED.value, OrderStatus.PROCESSING.value}
VALID_REASONS = {"damaged", "wrong_item", "not_needed", "defective", "size_issue", "other"}
TERMINAL_STATUSES = {ReturnStatus.REJECTED.value, ReturnStatus.REFUNDED.value, ReturnStatus.CANCELLED.value}


# ── Public ──
@router.post("", response_model=ReturnRequestOut, status_code=201)
@limiter.limit("5/minute")
async def create_return_request(
    request: Request, payload: ReturnRequestIn, db: AsyncSession = Depends(get_db)
):
    if payload.website:  # honeypot bot
        raise HTTPException(status_code=400, detail="invalid")
    if payload.reason not in VALID_REASONS:
        raise HTTPException(status_code=400, detail=f"Geçersiz sebep. İzin verilenler: {', '.join(VALID_REASONS)}")

    order = (await db.execute(select(Order).where(Order.order_no == payload.order_no))).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    # Email karşılaştırması (case-insensitive)
    if (order.customer_email or "").lower() != payload.customer_email.lower():
        raise HTTPException(status_code=403, detail="Sipariş bu e-posta ile eşleşmiyor")

    if order.status not in ELIGIBLE_ORDER_STATUSES:
        raise HTTPException(status_code=409, detail="Bu sipariş için iade açılamaz")

    # İade kalemleri: orijinal kalemlerden fazla olamaz
    order_item_map = {it.product_id: it for it in order.items if it.product_id}
    order_item_by_name = {it.name: it for it in order.items}
    refund_amount = Decimal("0")
    validated_items = []
    for ri in payload.items:
        original = None
        if ri.product_id and ri.product_id in order_item_map:
            original = order_item_map[ri.product_id]
        elif ri.name in order_item_by_name:
            original = order_item_by_name[ri.name]
        if not original:
            raise HTTPException(status_code=400, detail=f"Bu siparişte '{ri.name}' adlı ürün yok")
        if ri.qty > original.qty:
            raise HTTPException(
                status_code=400,
                detail=f"'{original.name}' için iade edebileceğiniz max adet: {original.qty}",
            )
        validated_items.append({
            "product_id": ri.product_id, "name": ri.name,
            "qty": ri.qty, "price": float(ri.price),
        })
        refund_amount += Decimal(str(ri.price)) * Decimal(ri.qty)

    rr = ReturnRequest(
        order_id=order.id,
        order_no=order.order_no,
        customer_name=order.customer_name,
        customer_email=order.customer_email,
        reason=payload.reason,
        note=payload.note,
        items=validated_items,
        refund_amount=float(refund_amount),
        status=ReturnStatus.REQUESTED.value,
    )
    db.add(rr)
    db.add(AuditLog(actor="customer", action="return-create",
                    message=f"İade talebi: {order.order_no} (₺{refund_amount:.2f})"))
    await db.commit()
    await db.refresh(rr)
    await bus.publish("return_created", {"id": rr.id, "order_no": rr.order_no, "amount": float(refund_amount)})

    # Müşteri bilgilendirme maili
    try:
        html = render_template("return_received.html", rr=rr)
        await send_email(to=rr.customer_email, subject=f"İade talebiniz alındı · {rr.order_no}", html=html)
    except Exception:
        pass
    return rr


@router.get("/lookup")
async def lookup_returns(
    order_no: str = Query(...), email: str = Query(...), db: AsyncSession = Depends(get_db)
):
    """Müşteri kendi siparişine bağlı iade taleplerini görür."""
    rows = (
        await db.execute(
            select(ReturnRequest).where(
                and_(
                    ReturnRequest.order_no == order_no,
                    ReturnRequest.customer_email.ilike(email),
                )
            ).order_by(ReturnRequest.id.desc())
        )
    ).scalars().all()
    return [
        {
            "id": r.id, "status": r.status, "reason": r.reason, "items": r.items,
            "refund_amount": float(r.refund_amount), "created_at": r.created_at,
            "processed_at": r.processed_at, "admin_note": r.admin_note,
        }
        for r in rows
    ]


@router.post("/{return_id}/cancel")
async def cancel_my_return(
    return_id: int, email: str = Query(...), db: AsyncSession = Depends(get_db)
):
    """Müşteri talebini geri çeker — sadece REQUESTED durumundakileri."""
    r = (await db.execute(select(ReturnRequest).where(ReturnRequest.id == return_id))).scalar_one_or_none()
    if not r or r.customer_email.lower() != email.lower():
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    if r.status != ReturnStatus.REQUESTED.value:
        raise HTTPException(status_code=409, detail="Sadece beklemedeki iadeler iptal edilebilir")
    r.status = ReturnStatus.CANCELLED.value
    r.processed_at = datetime.now(timezone.utc)
    db.add(AuditLog(actor="customer", action="return-cancel", message=f"İade iptal edildi: {r.order_no} (#{r.id})"))
    await db.commit()
    return {"ok": True}


# ── Admin ──
@router.get("", response_model=list[ReturnRequestOut])
async def list_returns(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
    status_filter: str | None = Query(None, alias="status"),
):
    stmt = select(ReturnRequest).order_by(ReturnRequest.id.desc())
    if status_filter:
        stmt = stmt.where(ReturnRequest.status == status_filter)
    rows = (await db.execute(stmt)).scalars().all()
    return rows


@router.get("/{return_id}", response_model=ReturnRequestOut)
async def get_return(return_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    r = (await db.execute(select(ReturnRequest).where(ReturnRequest.id == return_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    return r


@router.patch("/{return_id}/status", response_model=ReturnRequestOut)
async def update_return_status(
    return_id: int, payload: ReturnStatusUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)
):
    r = (await db.execute(select(ReturnRequest).where(ReturnRequest.id == return_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="İade bulunamadı")
    if r.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"İade zaten {r.status} durumunda")

    new_status = payload.status
    valid = {s.value for s in ReturnStatus}
    if new_status not in valid:
        raise HTTPException(status_code=400, detail="Geçersiz durum")
    if new_status == ReturnStatus.REQUESTED.value:
        raise HTTPException(status_code=400, detail="requested'a geri dönülemez")

    old = r.status
    r.status = new_status
    r.processed_by = user.username
    r.processed_at = datetime.now(timezone.utc)
    if payload.admin_note is not None:
        r.admin_note = payload.admin_note

    # Onaylandığında veya iade edildiğinde stok geri ekle (sadece bir kez)
    if new_status == ReturnStatus.REFUNDED.value and old != ReturnStatus.REFUNDED.value:
        product_ids = [it.get("product_id") for it in (r.items or []) if it.get("product_id")]
        if product_ids:
            products = (await db.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().unique().all()
            pmap = {p.id: p for p in products}
            for it in r.items or []:
                pid = it.get("product_id")
                qty = int(it.get("qty") or 0)
                p = pmap.get(pid) if pid else None
                if p and qty > 0:
                    p.stock = (p.stock or 0) + qty
                    db.add(StockMovement(
                        product_id=p.id, product_name=p.name, delta=qty,
                        reason="return", order_no=r.order_no,
                    ))

    db.add(AuditLog(
        actor=user.username, action=f"return-{new_status}",
        message=f"İade {new_status}: {r.order_no} (#{r.id})",
    ))
    await db.commit()
    await db.refresh(r)
    await bus.publish("return_status_changed", {"id": r.id, "status": r.status})

    # Müşteri bildirim maili
    try:
        html = render_template("return_processed.html", rr=r)
        labels = {
            "approved": "onaylandı", "rejected": "reddedildi",
            "refunded": "tamamlandı", "cancelled": "iptal edildi",
        }
        await send_email(
            to=r.customer_email,
            subject=f"İade talebiniz {labels.get(new_status, new_status)} · {r.order_no}",
            html=html,
        )
    except Exception:
        pass

    return r
