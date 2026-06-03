"""XLSX dışa aktarma endpoint'leri — admin."""
import io
import openpyxl


async def test_export_requires_permission(client, auth_client):
    for path in ["/orders.xlsx", "/products.xlsx", "/customers.xlsx", "/returns.xlsx"]:
        r1 = await client.get(f"/api/exports{path}")
        assert r1.status_code == 401, f"{path} 401 olmalı"
        r2 = await auth_client.get(f"/api/exports{path}")
        assert r2.status_code == 200, f"{path} 200 olmalı"


async def _parse_xlsx(content: bytes) -> tuple[list[str], list[list]]:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    headers = [str(c) if c is not None else "" for c in rows[0]]
    body = [[c for c in r] for r in rows[1:]]
    return headers, body


async def test_export_orders_xlsx(auth_client):
    r = await auth_client.get("/api/exports/orders.xlsx")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("content-type", "")
    headers, body = await _parse_xlsx(r.content)
    assert "Sipariş No" in headers
    assert "Toplam" in headers


async def test_export_orders_includes_created(auth_client, db_session):
    """DB'deki sipariş export'a yansır."""
    from app.models import Order, OrderStatus, PaymentStatus
    o = Order(
        order_no="TT-EXP-1", customer_name="Test", customer_email="t@t.com",
        customer_phone="+905551112233", customer_address="Adres",
        status=OrderStatus.PENDING.value, payment_status=PaymentStatus.SUCCESS.value,
        subtotal=100, total=100, tax=0, shipping=0, discount=0,
    )
    db_session.add(o)
    await db_session.commit()

    r = await auth_client.get("/api/exports/orders.xlsx")
    headers, body = await _parse_xlsx(r.content)
    assert any("TT-EXP-1" in (row[0] or "") for row in body)


async def test_export_products_xlsx(auth_client, db_session):
    from app.models import Product
    p = Product(name="Export Ürün", price=99.9, stock=5, is_active=True)
    db_session.add(p)
    await db_session.commit()

    r = await auth_client.get("/api/exports/products.xlsx")
    headers, body = await _parse_xlsx(r.content)
    assert "Ad" in headers
    assert "Fiyat" in headers
    assert any("Export Ürün" in (row[1] or "") for row in body)


async def test_export_products_includes_margin(auth_client, db_session):
    """cost varsa kâr marjı yüzdesi hesaplanır."""
    from app.models import Product
    p = Product(name="Kârlı", price=200.0, cost=100.0, stock=5, is_active=True)
    db_session.add(p)
    await db_session.commit()

    r = await auth_client.get("/api/exports/products.xlsx")
    headers, body = await _parse_xlsx(r.content)
    # body sırası: ID, Ad, Sub, Category, Fiyat, Eski Fiyat, Maliyet, Kâr Marjı %, Stok, Aktif, Puan, Yorum
    karli_row = next((row for row in body if row[1] == "Kârlı"), None)
    assert karli_row is not None
    # Kâr marjı = (200-100)/200 * 100 = 50.0
    assert karli_row[7] == 50.0


async def test_export_customers_xlsx(auth_client, db_session):
    from app.models import Customer
    from app.security import hash_password
    c = Customer(name="Export Müşteri", email="export@x.com", password_hash=hash_password("Pass1234!"), is_active=True)
    db_session.add(c)
    await db_session.commit()

    r = await auth_client.get("/api/exports/customers.xlsx")
    headers, body = await _parse_xlsx(r.content)
    assert "E-posta" in headers
    assert any("export@x.com" in (row[2] or "") for row in body)


async def test_export_returns_xlsx(auth_client, db_session):
    from app.models import Order, OrderItem, OrderStatus, PaymentStatus, Product, ReturnRequest, ReturnStatus
    p = Product(name="X", price=100.0, stock=10, is_active=True)
    db_session.add(p)
    await db_session.flush()
    o = Order(
        order_no="TT-RET-EXP", customer_name="X", customer_email="x@x.com",
        customer_phone="+905551112233", customer_address="Adres",
        status=OrderStatus.DELIVERED.value, payment_status=PaymentStatus.SUCCESS.value,
        subtotal=100, total=100, tax=0, shipping=0, discount=0,
    )
    db_session.add(o)
    await db_session.flush()
    db_session.add(OrderItem(order_id=o.id, product_id=p.id, name=p.name, qty=1, price=100))
    await db_session.commit()
    rr = ReturnRequest(
        order_id=o.id, order_no="TT-RET-EXP", customer_name="X", customer_email="x@x.com",
        reason="damaged", status=ReturnStatus.REQUESTED.value,
        items=[{"name": "X", "qty": 1, "price": 100}], refund_amount=100.0,
    )
    db_session.add(rr)
    await db_session.commit()

    r = await auth_client.get("/api/exports/returns.xlsx")
    headers, body = await _parse_xlsx(r.content)
    assert "İade #" in headers
    assert any("TT-RET-EXP" in (row[1] or "") for row in body)


async def test_export_empty_db(auth_client):
    """DB boşken de geçerli XLSX (sadece başlıklar) dönmeli."""
    r = await auth_client.get("/api/exports/orders.xlsx")
    assert r.status_code == 200
    headers, body = await _parse_xlsx(r.content)
    assert len(headers) > 0
    assert len(body) == 0
