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
