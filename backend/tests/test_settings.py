"""Site ayarları endpoint'leri."""


async def test_list_returns_defaults(client):
    r = await client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    # DEFAULTS anahtarları her zaman dolu döner
    assert body["shipping_free_threshold"] == "500"
    assert body["wire_enabled"] == "1"


async def test_update_requires_admin(client):
    r = await client.put("/api/settings", json={"shipping_fee_default": "0"})
    assert r.status_code == 401


async def test_update_only_known_keys(auth_client):
    r = await auth_client.put(
        "/api/settings",
        json={"shipping_fee_default": "29.9", "bilinmeyen_key": "x"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "shipping_fee_default" in body["updated"]
    assert "bilinmeyen_key" not in body["updated"]

    # Kalıcı oldu mu?
    after = (await auth_client.get("/api/settings")).json()
    assert after["shipping_fee_default"] == "29.9"


async def test_update_persists_and_overrides_default(auth_client):
    await auth_client.put("/api/settings", json={"low_stock_threshold": "2"})
    body = (await auth_client.get("/api/settings")).json()
    assert body["low_stock_threshold"] == "2"
