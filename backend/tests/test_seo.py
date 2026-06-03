"""SEO endpoint'leri — sitemap, robots, slug, product meta."""


async def test_sitemap_returns_xml(client):
    """sitemap.xml geçerli XML, ana sayfa + legal URL'ler içerir."""
    r = await client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "xml" in r.headers.get("content-type", "")
    body = r.text
    assert "<?xml" in body
    assert "<urlset" in body
    assert "http://localhost:5500/" in body  # base URL


async def test_sitemap_includes_active_products(client, auth_client, db_session):
    """Aktif ürünler sitemap'te listelenir, pasif olanlar değil."""
    from app.models import Product
    from sqlalchemy import select
    p1 = await auth_client.post("/api/products", json={"name": "Aktif Ürün", "price": 10, "stock": 5})
    pid1 = p1.json()["id"]
    p2 = await auth_client.post("/api/products", json={"name": "Pasif Ürün", "price": 10, "stock": 5})
    pid2 = p2.json()["id"]
    # p2'yi deaktif et (PUT şeması kabul etmiyor; doğrudan DB)
    prod2 = (await db_session.execute(select(Product).where(Product.id == pid2))).scalar_one()
    prod2.is_active = False
    await db_session.commit()

    r = await client.get("/sitemap.xml")
    body = r.text
    assert f"/#product/{pid1}" in body
    assert f"/#product/{pid2}" not in body


async def test_robots_txt(client):
    r = await client.get("/robots.txt")
    assert r.status_code == 200
    assert "User-agent" in r.text
    assert "Disallow: /admin.html" in r.text
    assert "Sitemap:" in r.text


async def test_resolve_slug_ok(client, auth_client):
    p = await auth_client.post("/api/products", json={"name": "Bosch Matkap", "price": 250, "stock": 3})
    pid = p.json()["id"]
    r = await client.get(f"/api/seo/slug/bosch-matkap")
    assert r.status_code == 200
    body = r.json()
    assert body["product_id"] == pid
    assert body["name"] == "Bosch Matkap"


async def test_resolve_slug_404(client):
    r = await client.get("/api/seo/slug/yok-boyle-bisey")
    assert r.status_code == 404


async def test_resolve_slug_ignores_inactive(client, auth_client, db_session):
    p = await auth_client.post("/api/products", json={"name": "Gizli Ürün", "price": 1, "stock": 1})
    pid = p.json()["id"]
    # PUT şeması is_active'i kabul etmiyor; db üzerinden deaktif et
    from app.models import Product
    from sqlalchemy import select
    prod = (await db_session.execute(select(Product).where(Product.id == pid))).scalar_one()
    prod.is_active = False
    await db_session.commit()

    r = await client.get("/api/seo/slug/gizli-urun")
    assert r.status_code == 404


async def test_product_meta_ok(client, auth_client):
    p = await auth_client.post("/api/products", json={
        "name": "Stanley Çekiç", "price": 89.9, "stock": 12, "description": "Ahşap saplı, 16oz"
    })
    pid = p.json()["id"]
    r = await client.get(f"/api/seo/meta/product/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Stanley Çekiç | TecnoTools"
    assert "canonical" in body
    assert body["og"]["type"] == "product"
    assert body["json_ld"]["@type"] == "Product"
    assert body["json_ld"]["offers"]["price"] == 89.9


async def test_product_meta_out_of_stock(client, auth_client):
    p = await auth_client.post("/api/products", json={"name": "Tükenen", "price": 10, "stock": 0})
    pid = p.json()["id"]
    r = await client.get(f"/api/seo/meta/product/{pid}")
    assert r.status_code == 200
    assert "OutOfStock" in r.json()["json_ld"]["offers"]["availability"]


async def test_product_meta_404(client):
    r = await client.get("/api/seo/meta/product/99999")
    assert r.status_code == 404


async def test_product_meta_inactive_404(client, auth_client, db_session):
    p = await auth_client.post("/api/products", json={"name": "A", "price": 1, "stock": 1})
    pid = p.json()["id"]
    from app.models import Product
    from sqlalchemy import select
    prod = (await db_session.execute(select(Product).where(Product.id == pid))).scalar_one()
    prod.is_active = False
    await db_session.commit()
    r = await client.get(f"/api/seo/meta/product/{pid}")
    assert r.status_code == 404
