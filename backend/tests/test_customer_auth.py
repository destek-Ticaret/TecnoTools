"""Müşteri üyelik (customer-auth) akış testleri."""

REGISTER_PAYLOAD = {
    "email": "alice@example.com",
    "password": "AliceStrong1!",
    "name": "Alice Tester",
    "phone": "+905551112233",
    "city": "İstanbul",
    "address": "Mahalle Sokak No:1 Daire:5",
    "marketing_opt_in": True,
}


async def _register(client, **overrides):
    payload = {**REGISTER_PAYLOAD, **overrides}
    return await client.post("/api/customer-auth/register", json=payload)


async def test_register_creates_member(client):
    r = await _register(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert "access_token" in body and "refresh_token" in body
    assert body["customer"]["email"] == "alice@example.com"
    assert body["customer"]["is_verified"] is False
    assert body["customer"]["marketing_opt_in"] is True


async def test_register_rejects_duplicate(client):
    r1 = await _register(client)
    assert r1.status_code == 201
    r2 = await _register(client)
    assert r2.status_code == 409


async def test_register_honeypot_rejects(client):
    r = await _register(client, website="im-a-bot")
    # Pydantic schema max_length=0 validation katmanında reddeder → 422
    assert r.status_code == 422


async def test_register_password_too_short(client):
    r = await _register(client, password="short")
    assert r.status_code == 422


async def test_login_success(client):
    await _register(client)
    r = await client.post(
        "/api/customer-auth/login",
        json={
            "email": "alice@example.com",
            "password": "AliceStrong1!",
        },
    )
    assert r.status_code == 200
    assert "access_token" in r.json()


async def test_login_wrong_password(client):
    await _register(client)
    r = await client.post(
        "/api/customer-auth/login",
        json={
            "email": "alice@example.com",
            "password": "WrongPass1!",
        },
    )
    assert r.status_code == 401


async def test_login_unknown_email_same_message(client):
    """Enumeration koruması: bilinmeyen e-posta ve yanlış şifre aynı yanıt."""
    r = await client.post(
        "/api/customer-auth/login",
        json={
            "email": "ghost@example.com",
            "password": "Whatever1!",
        },
    )
    assert r.status_code == 401


async def test_me_requires_token(client):
    r = await client.get("/api/customer-auth/me")
    assert r.status_code == 401


async def test_me_returns_profile(client):
    reg = (await _register(client)).json()
    client.headers["Authorization"] = f"Bearer {reg['access_token']}"
    r = await client.get("/api/customer-auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert body["name"] == "Alice Tester"


async def test_admin_token_rejected_by_customer_endpoints(client, seed_admin):
    """Admin JWT'si müşteri endpoint'lerinde kabul edilmez (type=customer_access şart)."""
    login = await client.post(
        "/api/auth/login",
        json={
            "username": "testadmin",
            "password": "TestPass123!",
        },
    )
    admin_token = login.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {admin_token}"
    r = await client.get("/api/customer-auth/me")
    assert r.status_code == 401


async def test_profile_update(client):
    reg = (await _register(client)).json()
    client.headers["Authorization"] = f"Bearer {reg['access_token']}"
    r = await client.patch(
        "/api/customer-auth/me", json={"city": "Ankara", "marketing_opt_in": False}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["city"] == "Ankara"
    assert body["marketing_opt_in"] is False


async def test_change_password_revokes_refresh_tokens(client):
    reg = (await _register(client)).json()
    refresh = reg["refresh_token"]
    client.headers["Authorization"] = f"Bearer {reg['access_token']}"

    cp = await client.post(
        "/api/customer-auth/change-password",
        json={
            "current_password": "AliceStrong1!",
            "new_password": "NewPass1234!",
        },
    )
    assert cp.status_code == 200

    # Eski refresh artık geçersiz
    bad = await client.post("/api/customer-auth/refresh", json={"refresh_token": refresh})
    assert bad.status_code == 401

    # Eski parola → fail
    r1 = await client.post(
        "/api/customer-auth/login",
        json={
            "email": "alice@example.com",
            "password": "AliceStrong1!",
        },
    )
    assert r1.status_code == 401
    # Yeni parola → ok
    r2 = await client.post(
        "/api/customer-auth/login",
        json={
            "email": "alice@example.com",
            "password": "NewPass1234!",
        },
    )
    assert r2.status_code == 200


async def test_refresh_rotation(client):
    reg = (await _register(client)).json()
    r1 = await client.post(
        "/api/customer-auth/refresh", json={"refresh_token": reg["refresh_token"]}
    )
    assert r1.status_code == 200
    new_refresh = r1.json()["refresh_token"]
    assert new_refresh != reg["refresh_token"]
    # Eski token tekrar kullanılamaz
    r2 = await client.post(
        "/api/customer-auth/refresh", json={"refresh_token": reg["refresh_token"]}
    )
    assert r2.status_code == 401


async def test_refresh_replay_revokes_all_sessions(client):
    """Rotate edilmiş bir müşteri refresh token'ının tekrar sunulması, diğer TÜM
    aktif oturumların da iptal edilmesine yol açmalı (çalınmış oturum işareti)."""
    reg = (await _register(client)).json()
    login2 = await client.post(
        "/api/customer-auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )
    refresh_a = reg["refresh_token"]
    refresh_b = login2.json()["refresh_token"]

    r = await client.post("/api/customer-auth/refresh", json={"refresh_token": refresh_a})
    assert r.status_code == 200

    replay = await client.post("/api/customer-auth/refresh", json={"refresh_token": refresh_a})
    assert replay.status_code == 401

    still_b = await client.post("/api/customer-auth/refresh", json={"refresh_token": refresh_b})
    assert still_b.status_code == 401


async def test_logout_revokes_refresh(client):
    reg = (await _register(client)).json()
    out = await client.post(
        "/api/customer-auth/logout", json={"refresh_token": reg["refresh_token"]}
    )
    assert out.status_code == 204
    r = await client.post(
        "/api/customer-auth/refresh", json={"refresh_token": reg["refresh_token"]}
    )
    assert r.status_code == 401


async def test_forgot_password_same_response(client):
    """Bilinmeyen e-posta da olsa enumeration koruması için aynı yanıt."""
    r = await client.post("/api/customer-auth/forgot-password", json={"email": "noone@example.com"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_reset_password_with_invalid_token(client):
    r = await client.post(
        "/api/customer-auth/reset-password",
        json={
            "token": "bogus-token",
            "new_password": "WhateverNew1!",
        },
    )
    assert r.status_code == 400


async def test_my_orders_filters_by_ownership(client, auth_client):
    """Müşteri sadece kendi siparişlerini görür."""
    # Admin bir ürün ekler
    pr = await auth_client.post("/api/products", json={"name": "P", "price": 100, "stock": 10})
    pid = pr.json()["id"]

    # Müşteri kayıt + sipariş
    reg = (await _register(client)).json()
    client.headers["Authorization"] = f"Bearer {reg['access_token']}"
    co = await client.post(
        "/api/orders/checkout",
        json={
            "items": [{"product_id": pid, "qty": 1}],
            "customer_name": "Alice Tester",
            "customer_email": "alice@example.com",
            "customer_phone": "+905551112233",
            "customer_city": "İstanbul",
            "customer_address": "Mahalle Sokak No:1 Daire:5",
        },
    )
    assert co.status_code == 200, co.text
    my_order_no = co.json()["order_no"]

    # Başka bir email ile sipariş (auth gerekmez, public checkout)
    client.headers.pop("Authorization", None)
    other = await client.post(
        "/api/orders/checkout",
        json={
            "items": [{"product_id": pid, "qty": 1}],
            "customer_name": "Bob Other",
            "customer_email": "bob@example.com",
            "customer_phone": "+905559998877",
            "customer_city": "İzmir",
            "customer_address": "Cadde No:5 Daire:1",
        },
    )
    assert other.status_code == 200
    other_no = other.json()["order_no"]

    # Alice tekrar giriş yapıp /orders çağırır → sadece kendisininki
    client.headers["Authorization"] = f"Bearer {reg['access_token']}"
    mine = await client.get("/api/customer-auth/orders")
    assert mine.status_code == 200
    nos = [o["order_no"] for o in mine.json()]
    assert my_order_no in nos
    assert other_no not in nos

    # Alice başkasının siparişine erişemez
    forbidden = await client.get(f"/api/customer-auth/orders/{other_no}")
    assert forbidden.status_code == 404


async def test_pasif_kayit_uyelige_yukseltilir(client, auth_client):
    """Pasif kayıt (checkout'tan açılmış, password yok) register ile üyeliğe yükseltilir."""
    pr = await auth_client.post("/api/products", json={"name": "P", "price": 50, "stock": 5})
    pid = pr.json()["id"]
    # 1) Anonim checkout → Customer pasif kaydı oluşur
    co = await client.post(
        "/api/orders/checkout",
        json={
            "items": [{"product_id": pid, "qty": 1}],
            "customer_name": "Carol Pasif",
            "customer_email": "carol@example.com",
            "customer_phone": "+905557776655",
            "customer_city": "Bursa",
            "customer_address": "Adres satırı No:1 Daire:1",
        },
    )
    assert co.status_code == 200
    past_order_no = co.json()["order_no"]

    # 2) Aynı e-posta ile register → upgrade
    r = await _register(client, email="carol@example.com", name="Carol Pasif")
    assert r.status_code == 201
    tok = r.json()["access_token"]

    # 3) /orders eski siparişi de gösterir (email eşleşmesiyle)
    client.headers["Authorization"] = f"Bearer {tok}"
    mine = await client.get("/api/customer-auth/orders")
    assert mine.status_code == 200
    assert past_order_no in [o["order_no"] for o in mine.json()]


async def test_loyalty_endpoint_for_new_member_returns_bronze(client):
    reg = (await _register(client)).json()
    client.headers["Authorization"] = f"Bearer {reg['access_token']}"
    r = await client.get("/api/customer-auth/loyalty")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "Bronze"
    assert body["points"] == 0
