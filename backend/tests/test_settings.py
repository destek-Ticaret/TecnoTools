"""Site ayarları endpoint'leri."""


async def test_list_returns_defaults(client):
    r = await client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    # DEFAULTS anahtarları her zaman dolu döner
    assert body["shipping_free_threshold"] == "500"
    assert body["cod_enabled"] == "1"


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


async def test_store_iban_settings_roundtrip(auth_client):
    await auth_client.put(
        "/api/settings",
        json={"store_iban": "TR330006100519786457841326", "store_iban_holder": "Mert Çakır"},
    )
    body = (await auth_client.get("/api/settings")).json()
    assert body["store_iban"] == "TR330006100519786457841326"
    assert body["store_iban_holder"] == "Mert Çakır"


def test_order_confirmation_email_includes_iban_for_wire():
    """bank_info verilince e-posta IBAN kutusunu içerir; verilmeyince içermez."""
    from types import SimpleNamespace

    from app.services.email import render_template

    order = SimpleNamespace(
        customer_name="Ali",
        order_no="TT-2026-0042",
        total=250.0,
        items=[SimpleNamespace(name="Matkap", qty=1, price=250.0)],
        customer_address="Test Mah.",
        customer_city="İstanbul",
        customer_phone="0555",
    )
    bank = {"iban": "TR33 0006 1005 1978 6457 8413 26", "holder": "Mert Çakır TecnoTools"}
    html = render_template("order_confirmation.html", order=order, bank_info=bank)
    assert "TR33 0006 1005 1978 6457 8413 26" in html
    assert "Havale" in html

    html2 = render_template("order_confirmation.html", order=order, bank_info=None)
    assert "ödemeniz onaylandı" in html2
    assert "TR33 0006" not in html2
