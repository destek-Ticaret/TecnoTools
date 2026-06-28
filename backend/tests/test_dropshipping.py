"""Dropshipping sourcing testleri (mock tedarikçi modu)."""

import pytest


@pytest.mark.asyncio
async def test_preview_builds_draft_with_markup(auth_client):
    r = await auth_client.get(
        "/api/dropshipping/preview",
        params={"url": "https://www.aliexpress.com/item/1005006789012345.html"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "mock"
    draft = body["draft"]
    assert draft["supplier"] == "aliexpress"
    assert draft["supplier_product_id"] == "1005006789012345"
    # Satış fiyatı maliyetten yüksek olmalı (markup uygulandı)
    assert draft["price"] > draft["cost"] > 0
    assert draft["images"]


@pytest.mark.asyncio
async def test_preview_detects_1688_source(auth_client):
    r = await auth_client.get(
        "/api/dropshipping/preview",
        params={"url": "https://detail.1688.com/offer/987654321.html"},
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["supplier"] == "1688"
    assert draft["supplier_product_id"] == "987654321"
    assert draft["price"] > draft["cost"] > 0


@pytest.mark.asyncio
async def test_custom_markup_changes_price(auth_client):
    url = "https://www.aliexpress.com/item/1005006789012345.html"
    low = (
        await auth_client.get("/api/dropshipping/preview", params={"url": url, "markup": 1.5})
    ).json()
    high = (
        await auth_client.get("/api/dropshipping/preview", params={"url": url, "markup": 3.0})
    ).json()
    assert high["draft"]["price"] > low["draft"]["price"]


@pytest.mark.asyncio
async def test_import_creates_product_and_blocks_duplicate(auth_client):
    url = "https://www.aliexpress.com/item/1005006789099999.html"
    r = await auth_client.post("/api/dropshipping/import", json={"url": url})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] > 0
    assert body["draft"]["supplier"] == "aliexpress"

    # Aynı tedarikçi ürünü ikinci kez eklenememeli (409) — bu aynı zamanda
    # ürünün DB'ye kalıcı yazıldığını da kanıtlar.
    dup = await auth_client.post("/api/dropshipping/import", json={"url": url})
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_preview_requires_auth(client):
    r = await client.get("/api/dropshipping/preview", params={"url": "123456789"})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_sync_product(auth_client):
    url = "https://www.aliexpress.com/item/1005006700011111.html"
    pid = (await auth_client.post("/api/dropshipping/import", json={"url": url})).json()["id"]
    r = await auth_client.post(f"/api/dropshipping/products/{pid}/sync", json={"reprice": True})
    assert r.status_code == 200, r.text
    assert r.json()["product_id"] == pid
    assert "changes" in r.json()


@pytest.mark.asyncio
async def test_sync_non_supplier_product_rejected(auth_client):
    # Tedarikçiye bağlı olmayan normal ürün oluştur
    created = await auth_client.post(
        "/api/products", json={"name": "Yerli Ürün", "price": 100, "stock": 5}
    )
    assert created.status_code in (200, 201), created.text
    pid = created.json()["id"]
    r = await auth_client.post(f"/api/dropshipping/products/{pid}/sync", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_sync_all(auth_client):
    await auth_client.post(
        "/api/dropshipping/import",
        json={"url": "https://www.aliexpress.com/item/1005006700022222.html"},
    )
    r = await auth_client.post("/api/dropshipping/sync", json={"reprice": True})
    assert r.status_code == 200, r.text
    assert r.json()["synced"] >= 1


@pytest.mark.asyncio
async def test_fulfillment_404_for_missing_order(auth_client):
    r = await auth_client.get("/api/dropshipping/orders/999999/fulfillment")
    assert r.status_code == 404
