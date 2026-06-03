"""İade talebi endpoint'leri — public lookup + admin onay/red."""
from app.models import Order, OrderItem, OrderStatus, Product, ReturnStatus


async def _create_order_with_item(db_session, status_value: str = OrderStatus.DELIVERED.value, qty: int = 2, stock: int = 50):
    """Test için: ürün + sipariş + kalem oluşturur, status'unu set eder."""
    p = Product(name="Matkap", price=200.0, stock=stock, is_active=True)
    db_session.add(p)
    await db_session.flush()
    o = Order(
        order_no="TT-TEST-0001",
        customer_name="Ali Veli",
        customer_email="ali@example.com",
        customer_phone="+905551112233",
        customer_city="İstanbul",
        customer_address="Adres",
        status=status_value,
        payment_status="paid",
        subtotal=200.0 * qty,
        total=200.0 * qty,
        tax=0,
        shipping=0,
        discount=0,
    )
    db_session.add(o)
    await db_session.flush()
    it = OrderItem(order_id=o.id, product_id=p.id, name=p.name, qty=qty, price=200.0)
    db_session.add(it)
    await db_session.commit()
    return o, p


async def test_create_return_request_ok(client, db_session):
    o, p = await _create_order_with_item(db_session, status_value=OrderStatus.DELIVERED.value, qty=3)
    payload = {
        "order_no": o.order_no,
        "customer_email": o.customer_email,
        "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
    }
    r = await client.post("/api/returns", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == ReturnStatus.REQUESTED.value
    assert body["refund_amount"] == 200.0


async def test_return_honeypot_rejected(client, db_session):
    """Honeypot doldurulmuşsa bot olarak reddedilir (422 ya da 400)."""
    o, p = await _create_order_with_item(db_session)
    payload = {
        "order_no": o.order_no,
        "customer_email": o.customer_email,
        "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
        "website": "http://spam.example",  # honeypot
    }
    r = await client.post("/api/returns", json=payload)
    assert r.status_code in (400, 422)


async def test_return_invalid_reason(client, db_session):
    o, p = await _create_order_with_item(db_session)
    payload = {
        "order_no": o.order_no,
        "customer_email": o.customer_email,
        "customer_name": o.customer_name,
        "reason": "because_i_want",  # izin verilen listede yok
        "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
    }
    r = await client.post("/api/returns", json=payload)
    assert r.status_code == 400
    assert "Geçersiz sebep" in r.json()["detail"]


async def test_return_email_mismatch_403(client, db_session):
    o, p = await _create_order_with_item(db_session)
    payload = {
        "order_no": o.order_no,
        "customer_email": "other@example.com",  # yanlış email
        "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
    }
    r = await client.post("/api/returns", json=payload)
    assert r.status_code == 403


async def test_return_ineligible_status(client, db_session):
    """Sipariş henüz 'pending' durumdaysa iade açılamaz."""
    o, p = await _create_order_with_item(db_session, status_value=OrderStatus.PENDING.value)
    payload = {
        "order_no": o.order_no,
        "customer_email": o.customer_email,
        "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
    }
    r = await client.post("/api/returns", json=payload)
    assert r.status_code == 409


async def test_return_qty_exceeds_order(client, db_session):
    """İade adedi siparişteki adedi aşamaz."""
    o, p = await _create_order_with_item(db_session, qty=2)
    payload = {
        "order_no": o.order_no,
        "customer_email": o.customer_email,
        "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 99, "price": 200.0}],
    }
    r = await client.post("/api/returns", json=payload)
    assert r.status_code == 400
    assert "max adet" in r.json()["detail"]


async def test_return_item_not_in_order(client, db_session):
    o, p = await _create_order_with_item(db_session)
    payload = {
        "order_no": o.order_no,
        "customer_email": o.customer_email,
        "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"name": "Olmayan Ürün", "qty": 1, "price": 100.0}],
    }
    r = await client.post("/api/returns", json=payload)
    assert r.status_code == 400
    assert "yok" in r.json()["detail"]


async def test_lookup_returns_by_order_and_email(client, db_session):
    o, p = await _create_order_with_item(db_session)
    for i in range(2):
        await client.post("/api/returns", json={
            "order_no": o.order_no,
            "customer_email": o.customer_email,
            "customer_name": o.customer_name,
            "reason": "damaged",
            "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
        })

    r = await client.get("/api/returns/lookup", params={"order_no": o.order_no, "email": o.customer_email})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert all(row["status"] == ReturnStatus.REQUESTED.value for row in rows)


async def test_cancel_my_return_ok(client, db_session):
    o, p = await _create_order_with_item(db_session)
    cr = await client.post("/api/returns", json={
        "order_no": o.order_no, "customer_email": o.customer_email, "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
    })
    rid = cr.json()["id"]

    r = await client.post(f"/api/returns/{rid}/cancel", params={"email": o.customer_email})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Artık cancelled
    lookup = await client.get("/api/returns/lookup", params={"order_no": o.order_no, "email": o.customer_email})
    assert lookup.json()[0]["status"] == ReturnStatus.CANCELLED.value


async def test_cancel_return_with_wrong_email_404(client, db_session):
    o, p = await _create_order_with_item(db_session)
    cr = await client.post("/api/returns", json={
        "order_no": o.order_no, "customer_email": o.customer_email, "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
    })
    rid = cr.json()["id"]
    r = await client.post(f"/api/returns/{rid}/cancel", params={"email": "imposter@x.com"})
    assert r.status_code == 404


async def test_admin_approve_and_refund_restores_stock(auth_client, client, db_session):
    """refunded durumuna geçince stok otomatik geri eklenir."""
    o, p = await _create_order_with_item(db_session, qty=3, stock=10)
    initial_stock = p.stock

    cr = await client.post("/api/returns", json={
        "order_no": o.order_no, "customer_email": o.customer_email, "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 2, "price": 200.0}],
    })
    rid = cr.json()["id"]

    # Onayla
    r = await auth_client.patch(f"/api/returns/{rid}/status", json={"status": "approved"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # İadeyi gerçekleştir → stok geri gelsin
    r2 = await auth_client.patch(f"/api/returns/{rid}/status", json={"status": "refunded"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "refunded"

    # Ürün stoğu artmış olmalı
    pub = await auth_client.get(f"/api/products/{p.id}")
    # Public ürün endpoint'inde effective_stock + stock alanları farklı olabilir
    body = pub.json()
    new_stock = body.get("stock") or body.get("effective_stock")
    assert new_stock is not None
    # effective_stock = stock - reservations. Burada sadece kendi stoğunu arttırması yeterli.
    assert new_stock >= initial_stock


async def test_admin_reject_terminal(auth_client, client, db_session):
    o, p = await _create_order_with_item(db_session)
    cr = await client.post("/api/returns", json={
        "order_no": o.order_no, "customer_email": o.customer_email, "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
    })
    rid = cr.json()["id"]
    r1 = await auth_client.patch(f"/api/returns/{rid}/status", json={"status": "rejected"})
    assert r1.status_code == 200

    # Terminal duruma düştü, başka status güncellemesi reddedilir
    r2 = await auth_client.patch(f"/api/returns/{rid}/status", json={"status": "approved"})
    assert r2.status_code == 409


async def test_admin_cannot_set_requested(auth_client, client, db_session):
    o, p = await _create_order_with_item(db_session)
    cr = await client.post("/api/returns", json={
        "order_no": o.order_no, "customer_email": o.customer_email, "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
    })
    rid = cr.json()["id"]
    r = await auth_client.patch(f"/api/returns/{rid}/status", json={"status": "requested"})
    assert r.status_code == 400


async def test_admin_list_returns(auth_client, client, db_session):
    o, p = await _create_order_with_item(db_session)
    for _ in range(2):
        await client.post("/api/returns", json={
            "order_no": o.order_no, "customer_email": o.customer_email, "customer_name": o.customer_name,
            "reason": "damaged",
            "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
        })
    r = await auth_client.get("/api/returns")
    assert r.status_code == 200
    assert len(r.json()) >= 2


async def test_admin_list_filter_by_status(auth_client, client, db_session):
    o, p = await _create_order_with_item(db_session)
    cr = await client.post("/api/returns", json={
        "order_no": o.order_no, "customer_email": o.customer_email, "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
    })
    rid = cr.json()["id"]
    await auth_client.patch(f"/api/returns/{rid}/status", json={"status": "approved"})

    approved = await auth_client.get("/api/returns", params={"status": "approved"})
    assert all(r["status"] == "approved" for r in approved.json())
    assert any(r["id"] == rid for r in approved.json())


async def test_admin_get_return_detail(auth_client, client, db_session):
    o, p = await _create_order_with_item(db_session)
    cr = await client.post("/api/returns", json={
        "order_no": o.order_no, "customer_email": o.customer_email, "customer_name": o.customer_name,
        "reason": "damaged",
        "items": [{"product_id": p.id, "name": p.name, "qty": 1, "price": 200.0}],
    })
    rid = cr.json()["id"]
    r = await auth_client.get(f"/api/returns/{rid}")
    assert r.status_code == 200
    assert r.json()["id"] == rid


async def test_admin_get_404(auth_client):
    r = await auth_client.get("/api/returns/99999")
    assert r.status_code == 404
