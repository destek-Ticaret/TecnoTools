"""Otomatik e-arşiv fatura — ödeme onaylanınca otomatik kesim."""

from sqlalchemy import select

from app.models import Invoice, Order, OrderItem, PaymentStatus, Product


async def _make_order(db_session, order_no="TT-AUTO-1", status="pending"):
    """Ürün + sipariş (ödenmiş) oluşturur, Order döner."""
    p = Product(name="Oto Ürün", price=100.0, stock=50, is_active=True)
    db_session.add(p)
    await db_session.flush()
    o = Order(
        order_no=order_no,
        customer_name="Veli",
        customer_email="veli@example.com",
        customer_phone="+905551110000",
        customer_address="Adres",
        status=status,
        payment_status=PaymentStatus.SUCCESS.value,
        subtotal=200.0,
        total=200.0,
        tax=0,
        shipping=0,
        discount=0,
    )
    db_session.add(o)
    await db_session.flush()
    db_session.add(OrderItem(order_id=o.id, product_id=p.id, name=p.name, qty=2, price=100.0))
    await db_session.commit()
    return o


async def _invoices_for(db_session, order_id):
    rows = (
        (await db_session.execute(select(Invoice).where(Invoice.order_id == order_id)))
        .scalars()
        .all()
    )
    return rows


async def test_auto_issue_creates_invoice(db_session):
    from app.routers.invoices import maybe_auto_issue_invoice

    o = await _make_order(db_session, order_no="TT-AUTO-1")
    await maybe_auto_issue_invoice(o.order_no, actor="test")
    rows = await _invoices_for(db_session, o.id)
    assert len(rows) == 1
    assert rows[0].status in ("sent", "failed")  # mock provider → sent
    assert rows[0].order_no == o.order_no


async def test_auto_issue_is_idempotent(db_session):
    from app.routers.invoices import maybe_auto_issue_invoice

    o = await _make_order(db_session, order_no="TT-AUTO-2")
    await maybe_auto_issue_invoice(o.order_no)
    await maybe_auto_issue_invoice(o.order_no)  # ikinci çağrı atlanmalı (aktif fatura var)
    rows = await _invoices_for(db_session, o.id)
    assert len(rows) == 1


async def test_auto_issue_disabled_by_setting(db_session):
    from app.routers.invoices import maybe_auto_issue_invoice
    from app.routers.settings import set_setting

    await set_setting(db_session, "auto_invoice_enabled", "0")
    await db_session.commit()
    o = await _make_order(db_session, order_no="TT-AUTO-3")
    await maybe_auto_issue_invoice(o.order_no)
    rows = await _invoices_for(db_session, o.id)
    assert len(rows) == 0


async def test_status_to_processing_triggers_invoice(auth_client, db_session):
    """PATCH status→processing (havale ödeme onayı) otomatik fatura keser."""
    o = await _make_order(db_session, order_no="TT-AUTO-4", status="pending")
    r = await auth_client.patch(f"/api/orders/{o.order_no}/status", json={"status": "processing"})
    assert r.status_code == 200, r.text
    rows = await _invoices_for(db_session, o.id)
    assert len(rows) == 1
