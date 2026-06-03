"""Auth akışları: login, refresh, logout, /me, forgot/reset password."""


async def test_login_success(client, seed_admin):
    resp = await client.post("/api/auth/login", json={
        "username": "testadmin", "password": "TestPass123!",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data and "refresh_token" in data
    assert data["token_type"] == "bearer"


async def test_login_wrong_password(client, seed_admin):
    resp = await client.post("/api/auth/login", json={
        "username": "testadmin", "password": "WrongPass!",
    })
    assert resp.status_code == 401
    assert "hatalı" in resp.json()["detail"].lower()


async def test_login_unknown_user(client, seed_admin):
    resp = await client.post("/api/auth/login", json={
        "username": "noone", "password": "Whatever123!",
    })
    assert resp.status_code == 401


async def test_me_requires_auth(client, seed_admin):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_me_returns_user(auth_client):
    resp = await auth_client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "testadmin"
    assert body["role"] == "admin"
    assert body["is_primary"] is True


async def test_refresh_token_rotation(client, seed_admin):
    login = await client.post("/api/auth/login", json={
        "username": "testadmin", "password": "TestPass123!",
    })
    refresh1 = login.json()["refresh_token"]
    r = await client.post("/api/auth/refresh", json={"refresh_token": refresh1})
    assert r.status_code == 200
    refresh2 = r.json()["refresh_token"]
    assert refresh2 != refresh1
    # Eski token artık iptal — yeniden kullanılamaz
    r2 = await client.post("/api/auth/refresh", json={"refresh_token": refresh1})
    assert r2.status_code == 401


async def test_logout_revokes_refresh(client, seed_admin):
    login = await client.post("/api/auth/login", json={
        "username": "testadmin", "password": "TestPass123!",
    })
    refresh = login.json()["refresh_token"]
    out = await client.post("/api/auth/logout", json={"refresh_token": refresh})
    assert out.status_code == 204
    # Logout sonrası refresh çalışmaz
    r = await client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401


async def test_forgot_password_unknown_user_returns_same(client, seed_admin):
    """User enumeration koruması: kullanıcı yoksa bile aynı yanıt."""
    r = await client.post("/api/auth/forgot-password", json={"username": "ghost"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_reset_password_with_invalid_token(client, seed_admin):
    r = await client.post("/api/auth/reset-password", json={
        "token": "invalid-token", "new_password": "NewPass123!",
    })
    assert r.status_code == 400
