"""Checkout akışı + PayTR mock token + sipariş ID counter testi."""


async def test_checkout_creates_order_and_returns_token(auth_client, db_session):
    pr = await auth_client.post(
        "/api/products",
        json={
            "name": "Çekiç",
            "price": 100.0,
            "stock": 50,
        },
    )
    pid = pr.json()["id"]

    payload = {
        "items": [{"product_id": pid, "qty": 2}],
        "customer_name": "Ali Veli",
        "customer_email": "ali@example.com",
        "customer_phone": "+905551112233",
        "customer_city": "İstanbul",
        "customer_address": "Mahalle Sokak No:1 Daire:5",
    }
    r = await auth_client.post("/api/orders/checkout", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["order_no"].startswith("TT-")
    # PayTR credential'ı boş; dev mock token döner
    assert body["iframe_token"].startswith("DEV-")

    # Sipariş DB'de yazılı, payment_status=initiated
    orders = await auth_client.get("/api/orders")
    assert len(orders.json()) == 1
    o = orders.json()[0]
    assert o["payment_status"] == "initiated"
    assert o["total"] > 0
    # Stok henüz düşmedi (PayTR success callback'i bekleniyor)
    pub = await auth_client.get(f"/api/products/{pid}")
    assert pub.json()["effective_stock"] == 50


async def test_checkout_routes_eur_to_stripe(auth_client):
    """EUR (yurt dışı) → Stripe; TRY → PayTR yönlendirmesi."""
    pr = await auth_client.post("/api/products", json={"name": "Kulaklık", "price": 200.0, "stock": 10})
    pid = pr.json()["id"]
    base = {
        "items": [{"product_id": pid, "qty": 1}],
        "customer_name": "Jane Doe",
        "customer_email": "jane@example.com",
        "customer_phone": "+491701234567",
        "customer_address": "Hauptstrasse 1, Berlin",
    }
    eur = await auth_client.post("/api/orders/checkout", json={**base, "currency": "EUR"})
    assert eur.status_code == 200, eur.text
    assert eur.json()["provider"] == "stripe"

    tr = await auth_client.post("/api/orders/checkout", json={**base, "currency": "TRY"})
    assert tr.status_code == 200, tr.text
    assert tr.json()["provider"] == "paytr"


async def test_checkout_rejects_oversold(auth_client):
    pr = await auth_client.post(
        "/api/products",
        json={
            "name": "Az Stoklu",
            "price": 50,
            "stock": 1,
        },
    )
    pid = pr.json()["id"]
    r = await auth_client.post(
        "/api/orders/checkout",
        json={
            "items": [{"product_id": pid, "qty": 5}],
            "customer_name": "Test User",
            "customer_email": "t@t.com",
            "customer_phone": "+905551112233",
            "customer_city": "X",
            "customer_address": "Mahalle Sokak No:1 Daire:5",
        },
    )
    assert r.status_code == 409
    assert "stok" in r.json()["detail"].lower()


async def test_order_no_counter_monotonic(auth_client):
    pr = await auth_client.post("/api/products", json={"name": "P", "price": 10, "stock": 100})
    pid = pr.json()["id"]
    payload = {
        "items": [{"product_id": pid, "qty": 1}],
        "customer_name": "User",
        "customer_email": "u@u.com",
        "customer_phone": "+905551112233",
        "customer_city": "X",
        "customer_address": "Mahalle Sokak No:1 Daire:5",
    }
    nums = []
    for _ in range(3):
        r = await auth_client.post("/api/orders/checkout", json=payload)
        nums.append(r.json()["order_no"])
    # Sıralı ve unique
    assert len(set(nums)) == 3
    seqs = [int(n.split("-")[-1]) for n in nums]
    assert seqs == sorted(seqs)


async def test_admin_status_update(auth_client):
    pr = await auth_client.post("/api/products", json={"name": "P", "price": 10, "stock": 5})
    pid = pr.json()["id"]
    co = await auth_client.post(
        "/api/orders/checkout",
        json={
            "items": [{"product_id": pid, "qty": 1}],
            "customer_name": "User",
            "customer_email": "u@u.com",
            "customer_phone": "+905551112233",
            "customer_city": "X",
            "customer_address": "Mahalle Sokak No:1 Daire:5",
        },
    )
    order_no = co.json()["order_no"]

    r = await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "shipped"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "shipped"
    # Tracking otomatik atandı
    assert body["tracking_no"] and body["tracking_no"].startswith("YK")


# ── Stok otomatik düşme (sipariş onaylanınca) ─────────────────────────
async def _wire_order(auth_client, *, stock, qty):
    pr = await auth_client.post(
        "/api/products", json={"name": "Stoklu Ürün", "price": 100, "stock": stock}
    )
    pid = pr.json()["id"]
    co = await auth_client.post(
        "/api/orders/checkout",
        json={
            "items": [{"product_id": pid, "qty": qty}],
            "customer_name": "User",
            "customer_email": "u@u.com",
            "customer_phone": "+905551112233",
            "customer_city": "X",
            "customer_address": "Mahalle Sokak No:1 Daire:5",
            "payment_method": "wire",
        },
    )
    assert co.status_code == 200, co.text
    return pid, co.json()["order_no"]


async def _eff_stock(auth_client, pid):
    return (await auth_client.get(f"/api/products/{pid}")).json()["effective_stock"]


async def test_processing_deducts_stock_and_marks_paid(auth_client):
    pid, order_no = await _wire_order(auth_client, stock=5, qty=2)
    assert await _eff_stock(auth_client, pid) == 5  # havale: henüz düşmedi
    r = await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "processing"})
    assert r.status_code == 200
    assert r.json()["payment_status"] == "success"  # havale onayı = ödeme onayı
    assert await _eff_stock(auth_client, pid) == 3  # 2 düştü


async def test_stock_deduction_idempotent_across_transitions(auth_client):
    pid, order_no = await _wire_order(auth_client, stock=10, qty=3)
    for st in ("processing", "shipped", "delivered"):
        await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": st})
    assert await _eff_stock(auth_client, pid) == 7  # yalnız 1 kez (3) düştü


async def test_skip_to_shipped_also_deducts(auth_client):
    pid, order_no = await _wire_order(auth_client, stock=5, qty=1)
    await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "shipped"})
    assert await _eff_stock(auth_client, pid) == 4  # processing atlansa da düştü


async def test_cancel_restores_deducted_stock(auth_client):
    pid, order_no = await _wire_order(auth_client, stock=8, qty=2)
    await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "processing"})
    assert await _eff_stock(auth_client, pid) == 6
    await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "cancelled"})
    assert await _eff_stock(auth_client, pid) == 8  # geri eklendi


async def test_reconfirm_after_cancel_deducts_stock_again(auth_client):
    """İptal edilip stoğu geri eklenen bir sipariş tekrar onaylanırsa (processing),
    stok tekrar düşmeli — aksi halde aynı ürün ikinci kez satılabilir olurdu."""
    pid, order_no = await _wire_order(auth_client, stock=8, qty=2)
    await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "processing"})
    assert await _eff_stock(auth_client, pid) == 6
    await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "cancelled"})
    assert await _eff_stock(auth_client, pid) == 8
    await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "processing"})
    assert await _eff_stock(auth_client, pid) == 6


async def test_cancel_before_fulfill_keeps_stock(auth_client):
    pid, order_no = await _wire_order(auth_client, stock=4, qty=1)
    await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "cancelled"})
    assert await _eff_stock(auth_client, pid) == 4  # hiç düşmedi → geri ekleme de yok


async def test_max_per_order_enforced(auth_client):
    """max_per_order=1 olan üründen 2 adet alınamaz."""
    pr = await auth_client.post(
        "/api/products", json={"name": "Sınırlı Ürün", "price": 50, "stock": 10, "max_per_order": 1}
    )
    pid = pr.json()["id"]
    payload = {
        "items": [{"product_id": pid, "qty": 2}],
        "customer_name": "Ali Veli",
        "customer_email": "ali@example.com",
        "customer_phone": "+905551112233",
        "customer_address": "Mahalle Sokak No:1 Daire:5",
    }
    r = await auth_client.post("/api/orders/checkout", json=payload)
    assert r.status_code == 409, r.text
    # 1 adet kabul edilir
    payload["items"][0]["qty"] = 1
    ok = await auth_client.post("/api/orders/checkout", json=payload)
    assert ok.status_code == 200, ok.text
