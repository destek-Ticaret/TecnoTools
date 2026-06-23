from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import require_editor
from app.models import (
    AuditLog,
    Coupon,
    Customer,
    Order,
    OrderCounter,
    OrderItem,
    OrderStatus,
    PaymentStatus,
    Product,
    ProductVariant,
    Reservation,
    User,
)
from app.rate_limit import limiter
from app.schemas import (
    CheckoutRequest,
    OrderNoteIn,
    OrderOut,
    OrderPatch,
    OrderStatusUpdate,
    PaymentStartResponse,
)
from app.security import decode_customer_token
from app.services.email import (
    send_admin_new_order,
    send_order_confirmation,
    send_order_status_update,
)
from app.services.events import bus
from app.services.loyalty import (
    POINT_TO_CURRENCY_RATIO,
    loyalty_for_email,
    redeem_points_to_discount,
)
from app.services.order_signing import sign_order, verify_token
from app.services.paytr import build_paytr_token
from app.services.stock import deduct_stock_once
from app.services.stripe_gateway import create_checkout_session
from app.services.tracking import build_tracking_response

_pay_settings = get_settings()

router = APIRouter(prefix="/api/orders", tags=["orders"])

TAX_RATE = Decimal("0.20")  # KDV %20


async def _next_order_no(db: AsyncSession) -> str:
    year = datetime.now(UTC).year
    row = (
        await db.execute(select(OrderCounter).where(OrderCounter.year == year).with_for_update())
    ).scalar_one_or_none()
    if not row:
        row = OrderCounter(year=year, seq=0)
        db.add(row)
        await db.flush()
    row.seq += 1
    seq = row.seq
    return f"TT-{year}-{seq:04d}"


async def _calc_totals(
    db: AsyncSession,
    items_data: list[tuple[Product, "ProductVariant | None", int, float]],
    coupon: Coupon | None,
    loyalty_discount: Decimal = Decimal("0"),
) -> dict:
    from app.routers.settings import get_setting

    subtotal = sum((Decimal(str(unit_price)) * Decimal(qty) for _, _, qty, unit_price in items_data), Decimal("0"))
    discount = Decimal("0")
    if coupon and coupon.is_active:
        if coupon.type == "percent":
            discount = subtotal * (Decimal(str(coupon.value)) / Decimal("100"))
        else:
            discount = min(Decimal(str(coupon.value)), subtotal)
    taxable = max(Decimal("0"), subtotal - discount - loyalty_discount)
    tax = taxable * TAX_RATE
    threshold = Decimal(str(await get_setting(db, "shipping_free_threshold", "500")))
    fee = Decimal(str(await get_setting(db, "shipping_fee_default", "49.9")))
    shipping = fee if Decimal("0") < taxable < threshold else Decimal("0")
    total = taxable + tax + shipping
    return {
        "subtotal": float(subtotal),
        "discount": float(discount),
        "tax": float(tax),
        "shipping": float(shipping),
        "total": float(total),
    }


@router.post("/checkout", response_model=PaymentStartResponse)
@limiter.limit("10/minute")
async def checkout(request: Request, payload: CheckoutRequest, db: AsyncSession = Depends(get_db)):
    """Sipariş oluşturur (status=pending, payment_status=initiated), PayTR token döner."""
    product_ids = [it.product_id for it in payload.items]
    products = (
        (await db.execute(select(Product).where(Product.id.in_(product_ids))))
        .scalars()
        .unique()
        .all()
    )
    pmap = {p.id: p for p in products}
    # İstenen varyantları yükle
    variant_ids = [it.variant_id for it in payload.items if it.variant_id]
    vmap: dict[int, ProductVariant] = {}
    if variant_ids:
        vrows = (
            (await db.execute(select(ProductVariant).where(ProductVariant.id.in_(variant_ids))))
            .scalars()
            .unique()
            .all()
        )
        vmap = {v.id: v for v in vrows}

    # (ürün, varyant|None, adet, birim_fiyat) — varyant fiyatı boşsa ürün fiyatı
    items_data: list[tuple[Product, ProductVariant | None, int, float]] = []
    for it in payload.items:
        p = pmap.get(it.product_id)
        if not p or not p.is_active:
            raise HTTPException(status_code=400, detail=f"Ürün bulunamadı: #{it.product_id}")
        active_variants = [v for v in p.variants if v.is_active]
        if active_variants:
            # Varyantlı ürün → seçim zorunlu, ürüne ait + aktif olmalı
            if not it.variant_id:
                raise HTTPException(status_code=400, detail=f"Varyant seçilmeli: {p.name}")
            v = vmap.get(it.variant_id)
            if not v or v.product_id != p.id or not v.is_active:
                raise HTTPException(status_code=400, detail=f"Geçersiz varyant: {p.name}")
            if (v.stock or 0) < it.qty:
                raise HTTPException(status_code=409, detail=f"Stok yetersiz: {p.name} ({v.name})")
            unit_price = float(v.price) if v.price is not None else float(p.price)
            items_data.append((p, v, it.qty, unit_price))
        else:
            if it.variant_id:
                raise HTTPException(status_code=400, detail=f"Bu ürünün varyantı yok: {p.name}")
            if (p.stock or 0) < it.qty:
                raise HTTPException(status_code=409, detail=f"Stok yetersiz: {p.name}")
            items_data.append((p, None, it.qty, float(p.price)))

    coupon = None
    if payload.coupon_code:
        coupon = (
            await db.execute(select(Coupon).where(Coupon.code == payload.coupon_code.upper()))
        ).scalar_one_or_none()
        if coupon and coupon.expires_at:
            exp = (
                coupon.expires_at
                if coupon.expires_at.tzinfo
                else coupon.expires_at.replace(tzinfo=UTC)
            )
            if exp < datetime.now(UTC):
                coupon = None
        if coupon and coupon.max_uses is not None and (coupon.used_count or 0) >= coupon.max_uses:
            coupon = None

    totals = await _calc_totals(db, items_data, coupon)
    if coupon and totals["subtotal"] < float(coupon.min_order or 0):
        raise HTTPException(status_code=400, detail="Kupon için minimum sepet tutarı sağlanmadı")

    # ── Sadakat puanı harcama ──
    # Üye girişi zorunlu; puanlar yalnız token'daki e-postayla verilen siparişte
    # kullanılabilir. Sunucu bakiye + %20 sepet sınırına göre kırpar.
    loyalty_points_used = 0
    loyalty_discount = Decimal("0")
    if payload.use_loyalty_points > 0:
        auth_header = request.headers.get("authorization") or ""
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
        tok = decode_customer_token(token) if token else None
        if not tok:
            raise HTTPException(status_code=401, detail="Puan kullanmak için üye girişi gerekli")
        if (tok.get("email") or "").lower() != payload.customer_email.lower():
            raise HTTPException(
                status_code=403,
                detail="Puanlar yalnızca kendi e-postanızla verilen siparişlerde kullanılabilir",
            )
        acct = await loyalty_for_email(db, payload.customer_email)
        available = acct.points if acct else 0
        pts_requested = min(payload.use_loyalty_points, available)
        taxable_before = max(0.0, totals["subtotal"] - totals["discount"])
        try_amount = redeem_points_to_discount(pts_requested, subtotal=taxable_before)
        if try_amount > 0:
            loyalty_points_used = int(round(try_amount / POINT_TO_CURRENCY_RATIO))
            loyalty_discount = Decimal(str(try_amount))
            totals = await _calc_totals(db, items_data, coupon, loyalty_discount)

    # Müşteri kaydı (upsert)
    cust = (
        await db.execute(select(Customer).where(Customer.email == payload.customer_email))
    ).scalar_one_or_none()
    if not cust:
        cust = Customer(
            email=payload.customer_email,
            name=payload.customer_name,
            phone=payload.customer_phone,
            city=payload.customer_city,
            address=payload.customer_address,
        )
        db.add(cust)
        await db.flush()
    else:
        cust.name, cust.phone, cust.city, cust.address = (
            payload.customer_name,
            payload.customer_phone,
            payload.customer_city,
            payload.customer_address,
        )

    order_no = await _next_order_no(db)
    order = Order(
        order_no=order_no,
        customer_id=cust.id,
        customer_name=payload.customer_name,
        customer_email=payload.customer_email,
        customer_phone=payload.customer_phone,
        customer_city=payload.customer_city,
        customer_address=payload.customer_address,
        subtotal=totals["subtotal"],
        discount=totals["discount"],
        coupon_code=coupon.code if coupon else None,
        loyalty_points_used=loyalty_points_used,
        loyalty_discount=float(loyalty_discount),
        tax=totals["tax"],
        shipping=totals["shipping"],
        total=totals["total"],
        status=OrderStatus.PENDING.value,
        payment_status=PaymentStatus.INITIATED.value,
        note=payload.note,
        admin_notes=[],
        tax_no=((payload.tax_no or None) and payload.tax_no.strip()) or None,
        tax_office=((payload.tax_office or None) and payload.tax_office.strip()) or None,
        company_title=((payload.company_title or None) and payload.company_title.strip()) or None,
    )
    db.add(order)
    await db.flush()

    for p, v, qty, unit_price in items_data:
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=p.id,
                variant_id=v.id if v else None,
                variant_name=v.name if v else None,
                name=p.name,
                icon=p.icon,
                image=(v.image if v and v.image else (p.images or [None])[0]),
                price=unit_price,
                qty=qty,
            )
        )

    # Rezervasyonu temizle (artık siparişte)
    if payload.session_id:
        await db.execute(delete(Reservation).where(Reservation.session_id == payload.session_id))

    db.add(
        AuditLog(
            actor="customer", action="order-create", message=f"Sipariş oluşturuldu: {order_no}"
        )
    )
    await db.commit()
    await db.refresh(order)
    await bus.publish("order_created", {"order_no": order_no, "total": float(order.total)})

    # Havale veya kapıda ödeme: gateway iframe akışı yok, sipariş "pending" kalır
    if payload.payment_method in ("wire", "cod"):
        from app.routers.settings import get_setting

        if payload.payment_method == "wire" and (await get_setting(db, "wire_enabled", "1")) != "1":
            raise HTTPException(status_code=400, detail="Havale ödeme kapalı")
        if payload.payment_method == "cod" and (await get_setting(db, "cod_enabled", "1")) != "1":
            raise HTTPException(status_code=400, detail="Kapıda ödeme kapalı")
        order.payment_method = payload.payment_method
        order.payment_status = "pending"  # bankaya gelince admin manual onaylayacak
        # COD: ürün kargoya verilir, ödeme kapıda; stok hemen düş
        # Havale (wire): admin ödemeyi görünce "processing" yapar → stok o an düşer
        if payload.payment_method == "cod":
            await deduct_stock_once(db, order)
        await db.commit()
        await db.refresh(order)
        # Onay maili + admin bildirimi (fire-and-forget; SMTP yoksa konsola yazar)
        # Havale siparişinde IBAN bilgilerini maile ekle
        bank_info = None
        if payload.payment_method == "wire":
            iban = (await get_setting(db, "store_iban", "") or "").replace(" ", "").upper()
            if iban:
                bank_info = {
                    "iban": " ".join(iban[i : i + 4] for i in range(0, len(iban), 4)),
                    "holder": await get_setting(db, "store_iban_holder", "") or "",
                }
        try:
            await send_order_confirmation(order, bank_info)
        except Exception:
            pass
        try:
            admins = (await db.execute(select(User))).scalars().all()
            admin_emails = [u.email for u in admins if u.email]
            await send_admin_new_order(order, admin_emails)
        except Exception:
            pass
        return PaymentStartResponse(
            order_no=order.order_no,
            iframe_token="",
            iframe_url=f"#order/{order.order_no}",  # frontend bunu görünce başarı modal'ı açar
            provider=payload.payment_method,
        )

    # Ödeme sağlayıcısı .env'den seçilir
    if _pay_settings.payment_provider == "stripe":
        sess = create_checkout_session(
            order_no=order.order_no,
            customer_email=order.customer_email,
            line_items=[
                (f"{p.name} ({v.name})" if v else p.name, unit_price, qty)
                for p, v, qty, unit_price in items_data
            ],
        )
        order.payment_method = "stripe"
        await db.commit()
        return PaymentStartResponse(
            order_no=order.order_no,
            iframe_token=sess["id"],
            iframe_url=sess["url"],
            provider="stripe",
        )
    paytr = build_paytr_token(
        order_no=order.order_no,
        email=order.customer_email,
        amount_kurus=int(round(order.total * 100)),
        user_name=order.customer_name,
        user_phone=order.customer_phone,
        user_address=f"{order.customer_address}, {order.customer_city or ''}",
        basket=[
            (f"{p.name} ({v.name})" if v else p.name, unit_price, qty)
            for p, v, qty, unit_price in items_data
        ],
    )
    return PaymentStartResponse(
        order_no=order.order_no,
        iframe_token=paytr["token"],
        iframe_url=f"https://www.paytr.com/odeme/guvenli/{paytr['token']}",
        provider="paytr",
    )


@router.get("/track")
@limiter.limit("20/minute")
async def track_order_public(
    request: Request,
    order_no: str,
    email: str,
    db: AsyncSession = Depends(get_db),
):
    """Public sipariş takibi — üye olmayanlar için.

    Doğrulama: `order_no` ve `email` birlikte eşleşmeli. E-posta sipariş
    anındaki müşteri e-postasıyla karşılaştırılır (kayıt sırasındakine değil).
    Bulamazsa 404 — sipariş varlığını sızdırmamak için 401/403 yerine.
    """
    o = (
        await db.execute(
            select(Order).where(
                (Order.order_no == order_no.strip())
                & (Order.customer_email == email.strip().lower())
            )
        )
    ).scalar_one_or_none()
    # Email büyük/küçük harfle farklı saklanmış olabilir; ikinci sorgu lowercase olmadan
    if not o:
        o = (
            await db.execute(
                select(Order).where(
                    (Order.order_no == order_no.strip()) & (Order.customer_email == email.strip())
                )
            )
        ).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return await build_tracking_response(db, o)


@router.get("/verify-barcode")
@limiter.limit("30/minute")
async def verify_barcode_public(request: Request, t: str):
    """RSA imzalı sipariş barkodu token'ını public olarak doğrular.

    Token QR koduyla gelir. İmza geçerliyse minimum bilgi döner (order_no + total).
    Token kendi içinde verisini taşıdığı için DB sorgusu yapılmaz.
    """
    payload = verify_token(t.strip())
    if not payload:
        return {"valid": False}
    return {
        "valid": True,
        "order_no": payload.get("order_no"),
        "total": payload.get("total"),
        "issued_at": payload.get("issued_at"),
    }


@router.get("/{order_no}/signed-barcode")
async def get_signed_barcode(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    """Sipariş için RSA imzalı barkod token'ı üretir (admin)."""
    o = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    signed = sign_order(order_no=o.order_no, total=o.total)
    return signed


@router.get("", response_model=list[OrderOut])
async def list_orders(db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    orders = (
        (await db.execute(select(Order).order_by(Order.created_at.desc()))).scalars().unique().all()
    )
    return orders


@router.get("/{order_no}", response_model=OrderOut)
async def get_order(
    order_no: str, db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)
):
    o = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return o


@router.patch("/{order_no}/status", response_model=OrderOut)
async def update_status(
    order_no: str,
    payload: OrderStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    o = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    allowed = {s.value for s in OrderStatus}
    if payload.status not in allowed:
        raise HTTPException(status_code=400, detail="Geçersiz durum")
    old = o.status
    o.status = payload.status
    if (
        payload.status in (OrderStatus.SHIPPED.value, OrderStatus.DELIVERED.value)
        and not o.tracking_no
    ):
        from secrets import token_hex

        o.tracking_no = "YK" + token_hex(5).upper()
    db.add(
        AuditLog(
            actor=user.username,
            action="order-status",
            message=f"{order_no}: {old} → {payload.status}",
        )
    )
    # Stok: sipariş ilk kez 'onaylı' bir duruma (hazırlanıyor/kargoda/teslim)
    # girince stoğu düş (idempotent — kart zaten ödeme callback'inde düşmüştür,
    # ikinci kez düşmez). Havale/Kapıda için ödeme onayını da işaretle.
    # 'İptal'e geçişte düşülmüş stok geri eklenir.
    _fulfill = (
        OrderStatus.PROCESSING.value,
        OrderStatus.SHIPPED.value,
        OrderStatus.DELIVERED.value,
    )
    if payload.status in _fulfill and old not in _fulfill:
        from app.services.stock import deduct_stock_once

        if await deduct_stock_once(db, o) and o.payment_status != PaymentStatus.SUCCESS.value:
            o.payment_status = PaymentStatus.SUCCESS.value
    elif payload.status == OrderStatus.CANCELLED.value and old != OrderStatus.CANCELLED.value:
        from app.services.stock import restore_stock_once

        await restore_stock_once(db, o)
    await db.commit()
    await db.refresh(o)
    # Müşteriye durum bildirim maili (initiated/pending dışı bir değişiklikse)
    if old != payload.status and payload.status in (
        "processing",
        "shipped",
        "delivered",
        "cancelled",
    ):
        try:
            await send_order_status_update(o, old)
        except Exception:
            pass
    await bus.publish("order_status_changed", {"order_no": order_no, "status": payload.status})
    # Havale/Kapıda siparişlerde admin 'hazırlanıyor'a çekince = ödeme onayı →
    # otomatik e-arşiv fatura (idempotent; kart siparişi zaten callback'te kesilir).
    if old != OrderStatus.PROCESSING.value and payload.status == OrderStatus.PROCESSING.value:
        from app.routers.invoices import maybe_auto_issue_invoice

        await maybe_auto_issue_invoice(order_no, actor=user.username)
    return o


@router.delete("/{order_no}", status_code=204)
async def delete_order(
    order_no: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)
):
    o = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    customer_email = o.customer_email
    await db.delete(o)
    db.add(
        AuditLog(actor=user.username, action="order-delete", message=f"Sipariş silindi: {order_no}")
    )
    await db.commit()
    # Müşterinin storefront sekmesi açıksa "Siparişlerim" anında güncellenir
    await bus.publish("order_deleted", {"order_no": order_no, "customer_email": customer_email})
    return None


@router.patch("/{order_no}", response_model=OrderOut)
async def patch_order(
    order_no: str,
    payload: OrderPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    o = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    fields = payload.model_dump(exclude_unset=True)
    for k, v in fields.items():
        setattr(o, k, v)
    db.add(
        AuditLog(
            actor=user.username, action="order-edit", message=f"Sipariş güncellendi: {order_no}"
        )
    )
    await db.commit()
    await db.refresh(o)
    return o


@router.post("/{order_no}/notes", response_model=OrderOut)
async def add_admin_note(
    order_no: str,
    payload: OrderNoteIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    o = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    notes = list(o.admin_notes or [])
    notes.insert(
        0, {"at": datetime.now(UTC).isoformat(), "text": payload.text, "by": user.username}
    )
    o.admin_notes = notes
    db.add(AuditLog(actor=user.username, action="order-note", message=f"{order_no}: not eklendi"))
    await db.commit()
    await db.refresh(o)
    return o


@router.delete("/{order_no}/notes/{note_idx}", response_model=OrderOut)
async def delete_admin_note(
    order_no: str,
    note_idx: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    o = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    notes = list(o.admin_notes or [])
    if note_idx < 0 or note_idx >= len(notes):
        raise HTTPException(status_code=404, detail="Not bulunamadı")
    notes.pop(note_idx)
    o.admin_notes = notes
    await db.commit()
    await db.refresh(o)
    return o
