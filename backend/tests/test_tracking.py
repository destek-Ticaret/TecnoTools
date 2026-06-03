"""Sipariş takip endpoint'leri ve servis testleri.

Kapsam:
  - carrier_for: tracking_no prefix → kargo firması mapping
  - GET /api/orders/track: public, order_no + email doğrulaması
  - GET /api/customer-auth/orders/{n}/tracking: üye için zenginleştirilmiş
  - Başkasının siparişine erişememe
"""


from app.services.tracking import carrier_for


# ── carrier_for birim testleri ────────────────────────────────────────────
def test_carrier_yurtici():
    c = carrier_for("YK12345")
    assert c["name"] == "Yurtiçi Kargo"
    assert "yurticikargo.com" in c["tracking_url"]
    assert "YK12345" in c["tracking_url"]


def test_carrier_mng():
    c = carrier_for("MNG-9999")
    assert c["name"] == "MNG Kargo"
    assert c["tracking_url"]


def test_carrier_aras():
    c = carrier_for("ARAS-123")
    assert c["name"] == "Aras Kargo"


def test_carrier_unknown_returns_generic():
    c = carrier_for("XX-999")
    assert c["name"] == "Kargo firması"
    assert c["tracking_url"] is None


def test_carrier_none_returns_none():
    assert carrier_for(None) is None
    assert carrier_for("") is None


# ── Public /api/orders/track ──────────────────────────────────────────────
async def _create_order_via_checkout(
    client, auth_client, email="trackuser@example.com", name="Track User"
):
    pr = await auth_client.post("/api/products", json={"name": "Ürün", "price": 200, "stock": 5})
    pid = pr.json()["id"]
    co = await client.post(
        "/api/orders/checkout",
        json={
            "items": [{"product_id": pid, "qty": 1}],
            "customer_name": name,
            "customer_email": email,
            "customer_phone": "+905551234567",
            "customer_city": "İstanbul",
            "customer_address": "Mahalle, Sokak No:1 Daire:5",
        },
    )
    assert co.status_code == 200, co.text
    return co.json()["order_no"]


async def test_public_track_with_correct_email(client, auth_client):
    order_no = await _create_order_via_checkout(client, auth_client)
    r = await client.get(f"/api/orders/track?order_no={order_no}&email=trackuser@example.com")
    assert r.status_code == 200
    data = r.json()
    assert data["order_no"] == order_no
    assert "timeline" in data and len(data["timeline"]) >= 2
    # Bekleyen sipariş: tüm adımlar tamamlanmamış olmalı
    assert any(s["is_active"] for s in data["timeline"])
    # ETA hesaplanmalı (henüz teslim edilmemiş)
    assert data["eta"] is not None
    assert "min_date" in data["eta"]


async def test_public_track_wrong_email_404(client, auth_client):
    order_no = await _create_order_via_checkout(client, auth_client)
    r = await client.get(f"/api/orders/track?order_no={order_no}&email=wrong@example.com")
    assert r.status_code == 404


async def test_public_track_unknown_order_404(client):
    r = await client.get("/api/orders/track?order_no=TT-9999-9999&email=anything@example.com")
    assert r.status_code == 404


async def test_track_after_status_change_has_carrier(client, auth_client):
    order_no = await _create_order_via_checkout(client, auth_client)
    # Admin: sipariş durumunu shipped'a çek → tracking_no otomatik atanır (YK...)
    r = await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "shipped"})
    assert r.status_code == 200
    track = await client.get(f"/api/orders/track?order_no={order_no}&email=trackuser@example.com")
    data = track.json()
    assert data["carrier"] is not None
    assert data["carrier"]["name"] == "Yurtiçi Kargo"
    assert data["carrier"]["tracking_url"]


async def test_cancelled_timeline_has_two_steps(client, auth_client):
    order_no = await _create_order_via_checkout(client, auth_client)
    await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "cancelled"})
    track = await client.get(f"/api/orders/track?order_no={order_no}&email=trackuser@example.com")
    data = track.json()
    codes = [s["code"] for s in data["timeline"]]
    assert codes == ["pending", "cancelled"]
    assert data["eta"] is None  # iptal edilmişse ETA yok


# ── Üye tracking endpoint'i ───────────────────────────────────────────────
async def test_member_tracking_owns_order(client, auth_client):
    # Üye kayıt
    reg = await client.post(
        "/api/customer-auth/register",
        json={
            "email": "alice@example.com",
            "password": "AliceStrong1!",
            "name": "Alice Tester",
            "marketing_opt_in": False,
        },
    )
    tok = reg.json()["access_token"]

    # Alice email'iyle sipariş aç (anonim checkout — sahiplik email ile eşleşir)
    order_no = await _create_order_via_checkout(
        client, auth_client, email="alice@example.com", name="Alice Tester"
    )

    client.headers["Authorization"] = f"Bearer {tok}"
    r = await client.get(f"/api/customer-auth/orders/{order_no}/tracking")
    assert r.status_code == 200
    body = r.json()
    assert body["order_no"] == order_no
    assert body["timeline"]
    assert body["items"]


async def test_member_cannot_track_other_customers_order(client, auth_client):
    # Bob üye olur
    reg = await client.post(
        "/api/customer-auth/register",
        json={
            "email": "bob@example.com",
            "password": "BobStrong12!",
            "name": "Bob Tester",
            "marketing_opt_in": False,
        },
    )
    tok = reg.json()["access_token"]

    # Başka biri sipariş açar
    order_no = await _create_order_via_checkout(
        client, auth_client, email="someone@example.com", name="Some One"
    )

    client.headers["Authorization"] = f"Bearer {tok}"
    r = await client.get(f"/api/customer-auth/orders/{order_no}/tracking")
    assert r.status_code == 404


async def test_anonymous_cannot_use_member_tracking(client, auth_client):
    order_no = await _create_order_via_checkout(client, auth_client)
    r = await client.get(f"/api/customer-auth/orders/{order_no}/tracking")
    assert r.status_code == 401


# ── ETA mantığı ────────────────────────────────────────────────────────────
async def test_eta_zone_for_marmara(client, auth_client):
    order_no = await _create_order_via_checkout(client, auth_client)
    r = await client.get(f"/api/orders/track?order_no={order_no}&email=trackuser@example.com")
    data = r.json()
    assert data["eta"]["zone"] == "MARMARA"
    # MARMARA min 1, max 2 + 1 gün prep
    assert data["eta"]["min_days_from_now"] >= 1
