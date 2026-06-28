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
