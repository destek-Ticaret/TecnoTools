"""Banner yönetimi — public listeleme + admin CRUD + reorder."""

from datetime import UTC


async def test_list_banners_public_ok(client):
    r = await client.get("/api/banners")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_list_banners_filters_inactive(client, auth_client):
    """Aktif olmayan banner public listede görünmez."""
    a = await auth_client.post(
        "/api/banners",
        json={
            "title": "Aktif",
            "image_url": "/a.jpg",
            "position": "hero",
        },
    )
    b = await auth_client.post(
        "/api/banners",
        json={
            "title": "Pasif",
            "image_url": "/b.jpg",
            "position": "hero",
            "is_active": False,
        },
    )

    r = await client.get("/api/banners", params={"position": "hero"})
    ids = [x["id"] for x in r.json()]
    assert a.json()["id"] in ids
    assert b.json()["id"] not in ids


async def test_list_banners_respects_date_window(client, auth_client):
    """Başlangıç tarihi gelecekte olan banner henüz gösterilmez."""
    from datetime import datetime, timedelta

    future = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    a = await auth_client.post(
        "/api/banners",
        json={
            "title": "Gelecek",
            "image_url": "/c.jpg",
            "position": "strip",
            "starts_at": future,
        },
    )
    r = await client.get("/api/banners", params={"position": "strip"})
    assert all(x["id"] != a.json()["id"] for x in r.json())


async def test_list_banners_excludes_expired(client, auth_client):
    from datetime import datetime, timedelta

    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    a = await auth_client.post(
        "/api/banners",
        json={
            "title": "Süresi Dolmuş",
            "image_url": "/d.jpg",
            "position": "popup",
            "ends_at": past,
        },
    )
    r = await client.get("/api/banners", params={"position": "popup"})
    assert all(x["id"] != a.json()["id"] for x in r.json())


async def test_create_banner_ok(auth_client):
    r = await auth_client.post(
        "/api/banners",
        json={
            "title": "Bahar",
            "subtitle": "Sezon indirimi",
            "image_url": "/hero.jpg",
            "link_url": "/campaign/bahar",
            "cta_text": "Keşfet",
            "position": "hero",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "Bahar"
    assert body["is_active"] is True


async def test_create_banner_invalid_position_400(auth_client):
    r = await auth_client.post(
        "/api/banners",
        json={
            "title": "X",
            "image_url": "/x.jpg",
            "position": "footer",  # izin verilenlerde yok
        },
    )
    assert r.status_code == 400
    assert "Geçersiz pozisyon" in r.json()["detail"]


async def test_create_banner_requires_permission(client):
    """Yetkisiz çağrı 401."""
    r = await client.post(
        "/api/banners", json={"title": "X", "image_url": "/x.jpg", "position": "hero"}
    )
    assert r.status_code == 401


async def test_update_banner_ok(auth_client):
    a = await auth_client.post(
        "/api/banners",
        json={
            "title": "Eski",
            "image_url": "/x.jpg",
            "position": "hero",
            "sort_order": 1,
        },
    )
    bid = a.json()["id"]
    r = await auth_client.put(
        f"/api/banners/{bid}",
        json={
            "title": "Yeni",
            "image_url": "/y.jpg",
            "position": "strip",
            "sort_order": 5,
        },
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Yeni"
    assert r.json()["position"] == "strip"


async def test_update_banner_404(auth_client):
    r = await auth_client.put(
        "/api/banners/99999",
        json={
            "title": "X",
            "image_url": "/x.jpg",
            "position": "hero",
        },
    )
    assert r.status_code == 404


async def test_admin_list_all_includes_inactive(auth_client, client):
    a = await auth_client.post(
        "/api/banners",
        json={
            "title": "Aktif",
            "image_url": "/a.jpg",
            "position": "hero",
            "is_active": True,
        },
    )
    b = await auth_client.post(
        "/api/banners",
        json={
            "title": "Pasif",
            "image_url": "/b.jpg",
            "position": "hero",
            "is_active": False,
        },
    )
    r = await auth_client.get("/api/banners/admin/all")
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()]
    assert a.json()["id"] in ids and b.json()["id"] in ids


async def test_reorder_banners(auth_client):
    ids = []
    for i in range(3):
        r = await auth_client.post(
            "/api/banners",
            json={
                "title": f"B{i}",
                "image_url": f"/{i}.jpg",
                "position": "hero",
            },
        )
        ids.append(r.json()["id"])

    # Yeni sıralama: ters çevir (Body embed=True → {"order": [...]})
    r = await auth_client.post("/api/banners/reorder", json={"order": [ids[2], ids[1], ids[0]]})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["count"] == 3

    # Sıralamayı doğrula
    listed = await auth_client.get("/api/banners/admin/all")
    hero_banners = [b for b in listed.json() if b["position"] == "hero"]
    so = [b["sort_order"] for b in hero_banners]
    assert so == sorted(so)


async def test_delete_banner_ok(auth_client):
    a = await auth_client.post(
        "/api/banners",
        json={
            "title": "Silinecek",
            "image_url": "/del.jpg",
            "position": "hero",
        },
    )
    bid = a.json()["id"]
    r = await auth_client.delete(f"/api/banners/{bid}")
    assert r.status_code == 204

    listed = await auth_client.get("/api/banners/admin/all")
    assert all(b["id"] != bid for b in listed.json())


async def test_delete_banner_404(auth_client):
    r = await auth_client.delete("/api/banners/99999")
    assert r.status_code == 404
