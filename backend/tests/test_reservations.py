"""Stok rezervasyon TTL + effective_stock hesabı."""


async def test_reservation_reduces_effective_stock_for_others(auth_client):
    pr = await auth_client.post(
        "/api/products",
        json={
            "name": "Limited",
            "price": 50,
            "stock": 10,
        },
    )
    pid = pr.json()["id"]

    # Bir session 3 adet rezerve etsin
    sync = await auth_client.post(
        "/api/reservations/sync",
        json={
            "session_id": "sess_other",
            "items": [{"product_id": pid, "qty": 3}],
        },
    )
    assert sync.status_code == 200

    # Farklı bir session için effective_stock 10-3=7 olmalı
    r = await auth_client.get(f"/api/products/{pid}?session_id=sess_me")
    assert r.json()["effective_stock"] == 7

    # Aynı session_id ile sorgulayan kendi rezervasyonunu hariç tutar → 10
    r2 = await auth_client.get(f"/api/products/{pid}?session_id=sess_other")
    assert r2.json()["effective_stock"] == 10


async def test_reservation_release(auth_client):
    pr = await auth_client.post(
        "/api/products",
        json={
            "name": "P",
            "price": 1,
            "stock": 5,
        },
    )
    pid = pr.json()["id"]
    await auth_client.post(
        "/api/reservations/sync",
        json={
            "session_id": "s1",
            "items": [{"product_id": pid, "qty": 2}],
        },
    )
    r1 = await auth_client.get(f"/api/products/{pid}?session_id=other")
    assert r1.json()["effective_stock"] == 3

    await auth_client.post("/api/reservations/release", json={"session_id": "s1"})
    r2 = await auth_client.get(f"/api/products/{pid}?session_id=other")
    assert r2.json()["effective_stock"] == 5
