from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import (
    AuditLog,
    Order,
    OrderStatus,
    PaymentStatus,
)
from app.rate_limit import limiter
from app.services.currency import convert, get_rate
from app.services.email import send_admin_new_order, send_order_confirmation
from app.services.events import bus
from app.services.paytr import verify_callback_hash
from app.services.stock import deduct_stock_once
from app.services.stripe_gateway import verify_webhook_signature as verify_stripe_signature

_settings = get_settings()
# FX kuru checkout ile webhook arasında geçen sürede küçük ölçüde oynayabilir;
# bu toleransın üstündeki farklar tutar kurcalaması/tutarsızlığı olarak ele alınır.
_STRIPE_AMOUNT_TOLERANCE = 0.03

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.get("/installments")
@limiter.limit("60/minute")
async def installments(
    request: Request,
    bin_no: str = Query(alias="bin", description="Kart numarasının ilk 6-8 hanesi"),
    price: float = Query(gt=0, description="Sepet toplamı (TL)"),
):
    """Kart BIN + tutara göre taksit seçenekleri (public).

    iyzico kimliği tanımlıysa gerçek banka taksit planları; yoksa gerçekçi mock.
    Kart bilgisi SAKLANMAZ — sadece taksit önizlemesi için ilk haneler kullanılır."""
    digits = "".join(ch for ch in bin_no if ch.isdigit())[:8]
    if len(digits) < 6:
        raise HTTPException(status_code=400, detail="BIN en az 6 hane olmalı")
    from app.services.iyzico import get_installment_options

    return get_installment_options(digits, price)


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

    # merchant_oid = order_no'daki tireler kaldırılmış hâli: "TT-YYYY-NNNN" → "TTYYYYNNNN".
    # Yıl her zaman sabit 4 haneli olduğundan (bkz. _next_order_no), tarama yapmadan
    # doğrudan deterministik olarak geri kurulabilir — seq 4 haneyi aşsa bile (>9999
    # sipariş/yıl) belirsizlik oluşmaz, çünkü seq kısmı "yıldan sonraki her şey"dir.
    if not merchant_oid.startswith("TT") or len(merchant_oid) < 7:
        return Response(content="OK", media_type="text/plain")
    order_no = f"TT-{merchant_oid[2:6]}-{merchant_oid[6:]}"
    order = (
        await db.execute(select(Order).where(Order.order_no == order_no))
    ).scalar_one_or_none()
    if not order:
        # Sipariş yok veya zaten işlenmiş — yine de OK döneriz ki PayTR retry etmesin
        return Response(content="OK", media_type="text/plain")

    if order.payment_status == PaymentStatus.SUCCESS.value:
        return Response(content="OK", media_type="text/plain")

    if status == "success":
        # Savunma amaçlı tutar doğrulaması: PayTR'nin bildirdiği tutar (kuruş),
        # siparişin GÜNCEL toplamıyla eşleşmeli. Token oluşturulduktan sonra
        # sipariş tutarı değiştiyse (admin düzenlemesi, kupon vb.) callback'i
        # körü körüne onaylamayız — manuel incelemeye düşer.
        expected_kurus = round(float(order.total) * 100)
        try:
            reported_kurus = int(total_amount)
        except ValueError:
            reported_kurus = -1
        if reported_kurus != expected_kurus:
            db.add(
                AuditLog(
                    actor="paytr",
                    action="payment-amount-mismatch",
                    message=(
                        f"Tutar uyuşmazlığı: {order.order_no} — bildirilen {reported_kurus}, "
                        f"beklenen {expected_kurus} kuruş. Sipariş otomatik onaylanmadı."
                    ),
                )
            )
            await db.commit()
            return Response(content="OK", media_type="text/plain")
        order.payment_status = PaymentStatus.SUCCESS.value
        order.status = OrderStatus.PROCESSING.value
        order.payment_method = payment_type or "card"
        # Stoğu kalıcı düş (idempotent — admin önce 'Hazırlanıyor'a çekmiş olsa
        # bile ikinci kez düşmez)
        await deduct_stock_once(db, order)
        db.add(
            AuditLog(
                actor="paytr", action="payment-success", message=f"Ödeme başarılı: {order.order_no}"
            )
        )
        # Sipariş onay maili + admin bildirimi (fire-and-forget; SMTP yoksa konsola yazar)
        try:
            await send_order_confirmation(order)
        except Exception:
            pass
        try:
            from sqlalchemy import select as _select

            from app.models import User as _User
            admins = (await db.execute(_select(_User))).scalars().all()
            await send_admin_new_order(order, [u.email for u in admins if u.email])
        except Exception:
            pass
    else:
        order.payment_status = PaymentStatus.FAILED.value
        order.status = OrderStatus.CANCELLED.value
        db.add(
            AuditLog(
                actor="paytr",
                action="payment-failed",
                message=f"Ödeme başarısız: {order.order_no} ({failed_reason_code or '-'} {failed_reason_msg or ''})".strip(),
            )
        )

    await db.commit()
    if status == "success":
        # Ödeme onaylandı → otomatik e-arşiv fatura (best-effort, kendi session'ında).
        from app.routers.invoices import maybe_auto_issue_invoice

        await maybe_auto_issue_invoice(order.order_no, actor="paytr")
    return Response(content="OK", media_type="text/plain")


async def _mark_order_paid(db: AsyncSession, order: Order, method: str) -> None:
    """Stoğu düş, status'u processing'e çek, onay maili gönder."""
    if order.payment_status == PaymentStatus.SUCCESS.value:
        return
    order.payment_status = PaymentStatus.SUCCESS.value
    order.status = OrderStatus.PROCESSING.value
    order.payment_method = method
    await deduct_stock_once(db, order)
    db.add(
        AuditLog(
            actor=method, action="payment-success", message=f"Ödeme başarılı: {order.order_no}"
        )
    )
    await bus.publish("order_status_changed", {"order_no": order.order_no, "status": order.status})
    try:
        await send_order_confirmation(order)
    except Exception:
        pass
    try:
        from sqlalchemy import select as _select

        from app.models import User as _User
        admins = (await db.execute(_select(_User))).scalars().all()
        admin_emails = [u.email for u in admins if u.email]
        await send_admin_new_order(order, admin_emails)
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

    if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session = event["data"]["object"]
        order_no = session.get("client_reference_id") or (session.get("metadata") or {}).get(
            "order_no"
        )
        # "completed" gecikmeli (async) ödeme yöntemlerinde payment_status=unpaid
        # iken de tetiklenebilir — para henüz kesinleşmemişken siparişi onaylamayız,
        # kesin sonuç async_payment_succeeded/failed event'iyle gelir.
        if order_no and session.get("payment_status") == "paid":
            order = (
                await db.execute(select(Order).where(Order.order_no == order_no))
            ).scalar_one_or_none()
            if order and order.payment_status != PaymentStatus.SUCCESS.value:
                # Savunma amaçlı tutar doğrulaması: Stripe'ın tahsil ettiği tutar,
                # siparişin GÜNCEL toplamının (session para birimine güncel kurla
                # çevrilmiş hâlinin) makul bir toleransı içinde olmalı. Checkout
                # session'ı oluşturulduktan sonra sipariş tutarı değiştiyse
                # (admin düzenlemesi, kupon vb.) körü körüne onaylamayız.
                session_cur = (session.get("currency") or "").upper()
                amount_total = session.get("amount_total")
                mismatch = True
                if session_cur and amount_total is not None:
                    try:
                        rate = await get_rate(_settings.base_currency, session_cur)
                        expected = convert(float(order.total), rate)
                        actual = float(amount_total) / 100
                        mismatch = abs(actual - expected) > expected * _STRIPE_AMOUNT_TOLERANCE
                    except Exception:
                        mismatch = True
                if mismatch:
                    db.add(
                        AuditLog(
                            actor="stripe",
                            action="payment-amount-mismatch",
                            message=(
                                f"Tutar uyuşmazlığı: {order.order_no} — Stripe {amount_total} "
                                f"{session_cur}, sipariş toplamı {order.total}. Otomatik onaylanmadı."
                            ),
                        )
                    )
                    await db.commit()
                else:
                    await _mark_order_paid(db, order, "stripe")
                    await db.commit()
                    from app.routers.invoices import maybe_auto_issue_invoice

                    await maybe_auto_issue_invoice(order.order_no, actor="stripe")
    elif event["type"] in (
        "checkout.session.expired",
        "checkout.session.async_payment_failed",
        "payment_intent.payment_failed",
    ):
        session = event["data"]["object"]
        order_no = session.get("client_reference_id") or (session.get("metadata") or {}).get(
            "order_no"
        )
        if order_no:
            order = (
                await db.execute(select(Order).where(Order.order_no == order_no))
            ).scalar_one_or_none()
            if order and order.payment_status != PaymentStatus.SUCCESS.value:
                order.payment_status = PaymentStatus.FAILED.value
                order.status = OrderStatus.CANCELLED.value
                db.add(
                    AuditLog(
                        actor="stripe",
                        action="payment-failed",
                        message=f"Ödeme başarısız: {order.order_no}",
                    )
                )
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
