"""Ürün CRUD ve public listeleme."""


async def test_public_list_empty(client):
    r = await client.get("/api/products")
    assert r.status_code == 200
    assert r.json() == []


async def test_create_product_requires_auth(client, seed_admin):
    r = await client.post(
        "/api/products",
        json={
            "name": "Akülü Matkap",
            "price": 999.0,
            "stock": 10,
        },
    )
    assert r.status_code == 401


async def test_full_product_lifecycle(auth_client):
    # Yarat
    r = await auth_client.post(
        "/api/products",
        json={
            "name": "Akülü Matkap",
            "sub": "18V Lityum",
            "description": "Güçlü ve hafif",
            "price": 1499.90,
            "stock": 25,
            "icon": "🔧",
        },
    )
    assert r.status_code == 201, r.text
    p = r.json()
    pid = p["id"]
    assert p["name"] == "Akülü Matkap"
    assert p["stock"] == 25
    assert p["is_active"] is True

    # Public listeyi gör
    pub = await auth_client.get("/api/products")
    assert pub.status_code == 200
    items = pub.json()
    assert len(items) == 1
    assert items[0]["effective_stock"] == 25

    # Güncelle
    r2 = await auth_client.put(
        f"/api/products/{pid}",
        json={
            "name": "Akülü Matkap",
            "price": 1299.90,
            "stock": 20,
            "icon": "🔧",
        },
    )
    assert r2.status_code == 200
    assert float(r2.json()["price"]) == 1299.90

    # Sil
    r3 = await auth_client.delete(f"/api/products/{pid}")
    assert r3.status_code == 204
    pub2 = await auth_client.get("/api/products")
    assert pub2.json() == []


async def test_inactive_product_hidden_from_public(auth_client):
    r = await auth_client.post(
        "/api/products",
        json={
            "name": "Stoksuz",
            "price": 100.0,
            "stock": 5,
            "is_active": False,
        },
    )
    assert r.status_code == 201
    pub = await auth_client.get("/api/products")
    assert pub.json() == []
    # Admin yine de görür
    adm = await auth_client.get("/api/products/admin/all")
    assert len(adm.json()) == 1


async def test_product_search(auth_client):
    await auth_client.post("/api/products", json={"name": "Matkap", "price": 500, "stock": 1})
    await auth_client.post("/api/products", json={"name": "Tornavida", "price": 100, "stock": 1})
    r = await auth_client.get("/api/products?q=Matkap")
    items = r.json()
    assert len(items) == 1
    assert items[0]["name"] == "Matkap"


async def test_product_video_url_roundtrip(auth_client):
    """video_url create'te kaydedilir, admin ve public detayda geri döner."""
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    r = await auth_client.post(
        "/api/products",
        json={"name": "Videolu Ürün", "price": 99, "stock": 5, "video_url": url},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["video_url"] == url

    # Public detayda da görünmeli
    pub = await auth_client.get(f"/api/products/{pid}")
    assert pub.status_code == 200
    assert pub.json()["video_url"] == url

    # Boşaltma: null gönderince temizlenmeli
    r2 = await auth_client.put(
        f"/api/products/{pid}",
        json={"name": "Videolu Ürün", "price": 99, "stock": 5, "video_url": None},
    )
    assert r2.status_code == 200
    assert r2.json()["video_url"] is None
