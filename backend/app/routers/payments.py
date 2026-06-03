from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AuditLog, Order, OrderItem, OrderStatus, PaymentStatus, Product, StockMovement
from app.services.email import send_order_confirmation
from app.services.events import bus
from app.services.paytr import verify_callback_hash
from app.services.stripe_gateway import verify_webhook_signature as verify_stripe_signature

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.post("/paytr/callback")
async def paytr_callback(
    merchant_oid: str = Form(...),
    status: str = Form(...),
    total_amount: str = Form(...),
    hash: str = Form(...),
    failed_reason_code: str | None = Form(None),
    failed_reason_msg: str | None = Form(None),
    payment_type: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """PayTR notification handler. Hash doğrulaması yapılır, sipariş güncellenir.
    Başarılı yanıtta düz `OK` döndürmek zorunludur, aksi takdirde PayTR yeniden dener."""
    if not verify_callback_hash(
        merchant_oid=merchant_oid, status=status, total_amount=total_amount, hash_value=hash
    ):
        # 200 dönmemek için 400 atıyoruz; PayTR retry yapar
        raise HTTPException(status_code=400, detail="hash mismatch")

    # merchant_oid'i tekrar order_no formatına çeviremeyiz (tire kaybolur), prefix eşleştirme yaparız
    # Pratikte order_no = "TT-YYYY-NNNN" → merchant_oid = "TTYYYYNNNN"
    # En güvenlisi: tüm aday siparişleri tarayıp eşleşeni bulmak yerine, order_no'da tire kaldırılarak match
    result = await db.execute(select(Order).where(Order.payment_status.in_([PaymentStatus.INITIATED.value, PaymentStatus.PENDING.value])))
    candidates = result.scalars().unique().all()
    order = next((o for o in candidates if o.order_no.replace("-", "") == merchant_oid), None)
    if not order:
        # Sipariş yok veya zaten işlenmiş — yine de OK döneriz ki PayTR retry etmesin
        return Response(content="OK", media_type="text/plain")

    if order.payment_status == PaymentStatus.SUCCESS.value:
        return Response(content="OK", media_type="text/plain")

    if status == "success":
        order.payment_status = PaymentStatus.SUCCESS.value
        order.status = OrderStatus.PROCESSING.value
        order.payment_method = payment_type or "card"
        # Stoğu kalıcı düş
        item_ids = [it.product_id for it in order.items if it.product_id]
        products = (await db.execute(select(Product).where(Product.id.in_(item_ids)))).scalars().unique().all()
        pmap = {p.id: p for p in products}
        for it in order.items:
            p = pmap.get(it.product_id) if it.product_id else None
            if p:
                p.stock = max(0, (p.stock or 0) - it.qty)
                db.add(StockMovement(
                    product_id=p.id, product_name=p.name, delta=-it.qty,
                    reason="sale", order_no=order.order_no,
                ))
        db.add(AuditLog(actor="paytr", action="payment-success", message=f"Ödeme başarılı: {order.order_no}"))
        # Sipariş onay maili (fire-and-forget; SMTP yoksa konsola yazar)
        try:
            await send_order_confirmation(order)
        except Exception:
            pass
    else:
        order.payment_status = PaymentStatus.FAILED.value
        order.status = OrderStatus.CANCELLED.value
        db.add(AuditLog(
            actor="paytr",
            action="payment-failed",
            message=f"Ödeme başarısız: {order.order_no} ({failed_reason_code or '-'} {failed_reason_msg or ''})".strip(),
        ))

    await db.commit()
    return Response(content="OK", media_type="text/plain")


async def _mark_order_paid(db: AsyncSession, order: Order, method: str) -> None:
    """Stoğu düş, status'u processing'e çek, onay maili gönder."""
    if order.payment_status == PaymentStatus.SUCCESS.value:
        return
    order.payment_status = PaymentStatus.SUCCESS.value
    order.status = OrderStatus.PROCESSING.value
    order.payment_method = method
    item_ids = [it.product_id for it in order.items if it.product_id]
    products = (await db.execute(select(Product).where(Product.id.in_(item_ids)))).scalars().unique().all()
    pmap = {p.id: p for p in products}
    for it in order.items:
        p = pmap.get(it.product_id) if it.product_id else None
        if p:
            p.stock = max(0, (p.stock or 0) - it.qty)
            db.add(StockMovement(
                product_id=p.id, product_name=p.name, delta=-it.qty,
                reason="sale", order_no=order.order_no,
            ))
    db.add(AuditLog(actor=method, action="payment-success", message=f"Ödeme başarılı: {order.order_no}"))
    await bus.publish("order_status_changed", {"order_no": order.order_no, "status": order.status})
    try:
        await send_order_confirmation(order)
    except Exception:
        pass


@router.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
):
    """Stripe webhook handler — checkout.session.completed event'inde sipariş ödendi sayılır."""
    raw = await request.body()
    event = verify_stripe_signature(raw, stripe_signature or "")
    if event is None:
        raise HTTPException(status_code=400, detail="invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        order_no = session.get("client_reference_id") or (session.get("metadata") or {}).get("order_no")
        if order_no:
            order = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
            if order:
                await _mark_order_paid(db, order, "stripe")
                await db.commit()
    elif event["type"] in ("checkout.session.expired", "payment_intent.payment_failed"):
        session = event["data"]["object"]
        order_no = session.get("client_reference_id") or (session.get("metadata") or {}).get("order_no")
        if order_no:
            order = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
            if order and order.payment_status != PaymentStatus.SUCCESS.value:
                order.payment_status = PaymentStatus.FAILED.value
                order.status = OrderStatus.CANCELLED.value
                db.add(AuditLog(actor="stripe", action="payment-failed", message=f"Ödeme başarısız: {order.order_no}"))
                await db.commit()
    return {"received": True}


@router.get("/order-status/{order_no}")
async def get_order_status(order_no: str, db: AsyncSession = Depends(get_db)):
    """Storefront, OK URL'sinden döndüğünde son durumu bu endpoint'le çeker."""
    result = await db.execute(select(Order).where(Order.order_no == order_no))
    o = result.scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return {
        "order_no": o.order_no,
        "status": o.status,
        "payment_status": o.payment_status,
        "total": float(o.total),
    }
