"""CMS statik sayfalar — public footer menüsü + admin CRUD + slug benzersizliği."""


async def test_list_pages_public_ok(client):
    r = await client.get("/api/pages")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_list_pages_footer_filter(client, auth_client):
    """Yayında + footer'da gösterilecek sayfalar görünür, diğerleri görünmez."""
    await auth_client.post("/api/pages", json={
        "title": "Hakkımızda", "body": "Biz...", "is_published": True, "show_in_footer": True, "sort_order": 1,
    })
    await auth_client.post("/api/pages", json={
        "title": "Gizli Taslak", "body": "...", "is_published": False, "show_in_footer": True,
    })
    await auth_client.post("/api/pages", json={
        "title": "Yayında ama footer'da yok", "body": "...", "is_published": True, "show_in_footer": False,
    })

    listed = (await client.get("/api/pages")).json()
    titles = [p["title"] for p in listed]
    assert "Hakkımızda" in titles
    assert "Gizli Taslak" not in titles
    assert "Yayında ama footer'da yok" not in titles


async def test_get_page_by_slug_ok(client, auth_client):
    cr = await auth_client.post("/api/pages", json={
        "title": "İade Politikası", "body": "İade 14 gün içinde...", "is_published": True,
    })
    slug = cr.json()["slug"]
    r = await client.get(f"/api/pages/{slug}")
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "İade Politikası"
    assert "İade" in body["body"]


async def test_get_page_404_for_unpublished(client, auth_client):
    cr = await auth_client.post("/api/pages", json={
        "title": "Taslak", "body": "...", "is_published": False,
    })
    # Yayında değilse public 404 döner
    r = await client.get(f"/api/pages/{cr.json()['slug']}")
    assert r.status_code == 404


async def test_get_page_404_for_nonexistent(client):
    r = await client.get("/api/pages/yok-boyle-sayfa")
    assert r.status_code == 404


async def test_create_page_auto_slug(auth_client):
    cr = await auth_client.post("/api/pages", json={"title": "Sıkça Sorulan Sorular", "body": "..."})
    assert cr.status_code == 201
    slug = cr.json()["slug"]
    assert slug  # otomatik üretildi


async def test_create_page_unique_slug(auth_client):
    """Aynı başlık → otomatik -2, -3 ... sonekleri."""
    a = await auth_client.post("/api/pages", json={"title": "Garanti Şartları", "body": "..."})
    b = await auth_client.post("/api/pages", json={"title": "Garanti Şartları", "body": "..."})
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["slug"] != b.json()["slug"]
    assert b.json()["slug"].startswith(a.json()["slug"])


async def test_create_page_requires_permission(client):
    r = await client.post("/api/pages", json={"title": "X", "body": "..."})
    assert r.status_code == 401


async def test_update_page_ok(auth_client):
    cr = await auth_client.post("/api/pages", json={"title": "Eski", "body": "..."})
    pid = cr.json()["id"]
    r = await auth_client.put(f"/api/pages/{pid}", json={
        "title": "Yeni", "body": "Yeni içerik", "is_published": True, "show_in_footer": True,
    })
    assert r.status_code == 200
    assert r.json()["title"] == "Yeni"


async def test_update_page_404(auth_client):
    r = await auth_client.put("/api/pages/99999", json={"title": "X", "body": "..."})
    assert r.status_code == 404


async def test_admin_list_includes_drafts(auth_client):
    pub = await auth_client.post("/api/pages", json={"title": "Pub", "body": "...", "is_published": True, "show_in_footer": True})
    draft = await auth_client.post("/api/pages", json={"title": "Draft", "body": "...", "is_published": False})
    r = await auth_client.get("/api/pages/admin/all")
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()]
    assert pub.json()["id"] in ids
    assert draft.json()["id"] in ids


async def test_delete_page_ok(auth_client):
    cr = await auth_client.post("/api/pages", json={"title": "Silinecek", "body": "..."})
    pid = cr.json()["id"]
    r = await auth_client.delete(f"/api/pages/{pid}")
    assert r.status_code == 204

    r2 = await auth_client.get("/api/pages/admin/all")
    assert all(x["id"] != pid for x in r2.json())


async def test_delete_page_404(auth_client):
    r = await auth_client.delete("/api/pages/99999")
    assert r.status_code == 404
