"""Admin panel — içerik & otomasyon özelliklerinin testleri.

Kapsam: granüler yetki, banner, ana sayfa düzeni, blog, CMS sayfa,
fiyat kuralları (preview/apply), toplu ürün içe aktarma.
"""

import io

import pytest
from openpyxl import Workbook


# ── Granüler yetki ──
@pytest.mark.asyncio
async def test_permission_catalog_and_enforcement(auth_client):
    r = await auth_client.get("/api/admin/users/permissions/catalog")
    assert r.status_code == 200
    assert "catalog" in r.json() and "role_defaults" in r.json()

    # Editor oluştur, content.blog iznini kapat
    r = await auth_client.post(
        "/api/admin/users", json={"username": "ed1", "password": "parola12345", "role": "editor"}
    )
    uid = r.json()["id"]
    r = await auth_client.put(
        f"/api/admin/users/{uid}/permissions", json={"permissions": {"content.blog": False}}
    )
    assert r.status_code == 200
    assert "content.blog" not in r.json()["effective"]

    # Editor olarak giriş yap — blog yazma 403, banner yazma 201
    lr = await auth_client.post(
        "/api/auth/login", json={"username": "ed1", "password": "parola12345"}
    )
    etok = lr.json()["access_token"]
    eh = {"Authorization": f"Bearer {etok}"}
    r = await auth_client.post("/api/blog", json={"title": "x", "body": "y"}, headers=eh)
    assert r.status_code == 403
    r = await auth_client.post("/api/banners", json={"image_url": "http://x/a.jpg"}, headers=eh)
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_admin_cannot_get_override(auth_client):
    # Admin rolüne override reddedilir
    r = await auth_client.get("/api/admin/users")
    admin_id = [u for u in r.json() if u["role"] == "admin"][0]["id"]
    r = await auth_client.put(
        f"/api/admin/users/{admin_id}/permissions", json={"permissions": {"orders.view": False}}
    )
    assert r.status_code == 400


# ── Banner ──
@pytest.mark.asyncio
async def test_banner_crud_and_public(auth_client):
    r = await auth_client.post(
        "/api/banners", json={"image_url": "http://x/a.jpg", "title": "Yaz", "position": "hero"}
    )
    assert r.status_code == 201
    bid = r.json()["id"]
    # Geçersiz pozisyon
    r = await auth_client.post(
        "/api/banners", json={"image_url": "http://x/b.jpg", "position": "xxx"}
    )
    assert r.status_code == 400
    # Public list
    r = await auth_client.get("/api/banners?position=hero")
    assert r.status_code == 200 and len(r.json()) == 1
    # Update + reorder + delete
    assert (
        await auth_client.put(
            f"/api/banners/{bid}", json={"image_url": "http://x/a2.jpg", "position": "strip"}
        )
    ).status_code == 200
    assert (
        await auth_client.post("/api/banners/reorder", json={"order": [bid]})
    ).status_code == 200
    assert (await auth_client.delete(f"/api/banners/{bid}")).status_code == 204


# ── Ana sayfa düzeni ──
@pytest.mark.asyncio
async def test_homepage_sections_reorder(auth_client):
    ids = []
    for kind in ("hero", "product_carousel", "blog"):
        r = await auth_client.post("/api/homepage", json={"kind": kind})
        assert r.status_code == 201
        ids.append(r.json()["id"])
    # Ters sırada reorder
    rev = list(reversed(ids))
    assert (await auth_client.post("/api/homepage/reorder", json={"order": rev})).status_code == 200
    r = await auth_client.get("/api/homepage")
    assert [s["id"] for s in r.json()] == rev
    # Geçersiz kind
    assert (await auth_client.post("/api/homepage", json={"kind": "bad"})).status_code == 400


# ── Blog ──
@pytest.mark.asyncio
async def test_blog_slug_and_views(auth_client):
    r = await auth_client.post(
        "/api/blog", json={"title": "İlk Yazı", "body": "<p>m</p>", "is_published": True}
    )
    assert r.status_code == 201
    slug = r.json()["slug"]
    assert slug == "ilk-yazi"
    # Aynı başlık → benzersiz slug
    r2 = await auth_client.post(
        "/api/blog", json={"title": "İlk Yazı", "body": "<p>m</p>", "is_published": True}
    )
    assert r2.json()["slug"] != slug
    # Public get artışı
    r = await auth_client.get(f"/api/blog/{slug}")
    assert r.status_code == 200 and r.json()["view_count"] == 1
    # Taslak public görünmez
    d = await auth_client.post(
        "/api/blog", json={"title": "Taslak", "body": "x", "is_published": False}
    )
    assert (await auth_client.get(f"/api/blog/{d.json()['slug']}")).status_code == 404


# ── CMS sayfa ──
@pytest.mark.asyncio
async def test_cms_pages(auth_client):
    r = await auth_client.post(
        "/api/pages", json={"title": "Hakkımızda", "body": "<p>biz</p>", "show_in_footer": True}
    )
    assert r.status_code == 201
    slug = r.json()["slug"]
    assert (await auth_client.get("/api/pages")).json()[0]["slug"] == slug
    assert (await auth_client.get(f"/api/pages/{slug}")).status_code == 200


# ── Fiyat kuralları ──
async def _make_product(auth_client, **kw):
    payload = {"name": "Ürün", "price": 100, "stock": 5}
    payload.update(kw)
    return (await auth_client.post("/api/products", json=payload)).json()


@pytest.mark.asyncio
async def test_pricing_rule_percent_and_idempotent_margin(auth_client):
    p = await _make_product(auth_client, name="P1", price=100, cost=60)
    # margin %50 → 90
    r = await auth_client.post(
        "/api/pricing-rules", json={"name": "marj", "strategy": "margin", "value": 50}
    )
    rid = r.json()["id"]
    prev = await auth_client.post(f"/api/pricing-rules/{rid}/preview")
    assert prev.json()["changed"] == 1
    ap = await auth_client.post(f"/api/pricing-rules/{rid}/apply")
    assert ap.json()["affected"] == 1
    cur = await auth_client.get(f"/api/products/{p['id']}")
    assert float(cur.json()["price"]) == 90.0
    # idempotent: tekrar uygula → 0
    assert (await auth_client.post(f"/api/pricing-rules/{rid}/apply")).json()["affected"] == 0


@pytest.mark.asyncio
async def test_pricing_rule_validation(auth_client):
    # category scope ama scope_id yok
    r = await auth_client.post(
        "/api/pricing-rules",
        json={"name": "x", "scope_type": "category", "strategy": "percent", "value": 5},
    )
    assert r.status_code == 400
    # geçersiz strateji
    r = await auth_client.post(
        "/api/pricing-rules", json={"name": "x", "strategy": "bad", "value": 5}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_pricing_min_max_guard(auth_client):
    p = await _make_product(auth_client, name="P2", price=100)
    # %50 indirim ama min 70 koruması
    r = await auth_client.post(
        "/api/pricing-rules",
        json={"name": "ind", "strategy": "percent", "value": -50, "min_price": 70},
    )
    rid = r.json()["id"]
    await auth_client.post(f"/api/pricing-rules/{rid}/apply")
    cur = await auth_client.get(f"/api/products/{p['id']}")
    assert float(cur.json()["price"]) == 70.0


# ── Toplu içe aktarma ──
def _xlsx_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["name", "price", "cost", "stock", "is_active"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_bulk_import_dry_run_and_commit(auth_client):
    data = _xlsx_bytes([["İçe Aktarılan", 150, 90, 12, "evet"], ["İkinci", 200, 120, 3, "evet"]])
    files = {
        "file": (
            "u.xlsx",
            data,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    # dry-run DB'yi değiştirmez
    r = await auth_client.post("/api/imports/products?dry_run=true", files=files)
    assert r.status_code == 200 and r.json()["created"] == 2
    assert len((await auth_client.get("/api/products/admin/all")).json()) == 0
    # gerçek
    files = {
        "file": (
            "u.xlsx",
            data,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    r = await auth_client.post("/api/imports/products", files=files)
    assert r.json()["created"] == 2
    names = {p["name"] for p in (await auth_client.get("/api/products/admin/all")).json()}
    assert {"İçe Aktarılan", "İkinci"} <= names


@pytest.mark.asyncio
async def test_bulk_import_updates_by_name(auth_client):
    await _make_product(auth_client, name="Mevcut", price=50)
    data = _xlsx_bytes([["Mevcut", 75, None, 9, "evet"]])
    files = {
        "file": (
            "u.xlsx",
            data,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    r = await auth_client.post("/api/imports/products", files=files)
    assert r.json()["updated"] == 1 and r.json()["created"] == 0
    prods = (await auth_client.get("/api/products/admin/all")).json()
    mevcut = [p for p in prods if p["name"] == "Mevcut"][0]
    assert float(mevcut["price"]) == 75.0
