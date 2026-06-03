"""Blog yazıları — public + admin CRUD."""


async def test_list_blog_public_ok(client):
    r = await client.get("/api/blog")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_list_blog_filters_drafts(client, auth_client):
    pub = await auth_client.post(
        "/api/blog",
        json={
            "title": "Yayınlanan Yazı",
            "body": "İçerik...",
            "is_published": True,
        },
    )
    draft = await auth_client.post(
        "/api/blog",
        json={
            "title": "Taslak Yazı",
            "body": "İçerik...",
            "is_published": False,
        },
    )
    listed = await client.get("/api/blog")
    ids = [p["id"] for p in listed.json()]
    assert pub.json()["id"] in ids
    assert draft.json()["id"] not in ids


async def test_list_blog_tag_filter(client, auth_client):
    await auth_client.post(
        "/api/blog",
        json={
            "title": "Matkap Rehberi",
            "body": "...",
            "is_published": True,
            "tags": ["matkap", "rehber"],
        },
    )
    await auth_client.post(
        "/api/blog",
        json={
            "title": "Tornavida İpuçları",
            "body": "...",
            "is_published": True,
            "tags": ["tornavida"],
        },
    )
    filtered = await client.get("/api/blog", params={"tag": "matkap"})
    titles = [p["title"] for p in filtered.json()]
    assert "Matkap Rehberi" in titles
    assert "Tornavida İpuçları" not in titles


async def test_list_blog_limit(client, auth_client):
    for i in range(5):
        await auth_client.post(
            "/api/blog", json={"title": f"Yazı {i}", "body": "...", "is_published": True}
        )
    r = await client.get("/api/blog", params={"limit": 2})
    assert len(r.json()) == 2


async def test_get_blog_increments_view_count(client, auth_client):
    cr = await auth_client.post(
        "/api/blog", json={"title": "Sayım Testi", "body": "...", "is_published": True}
    )
    slug = cr.json()["slug"]
    r1 = await client.get(f"/api/blog/{slug}")
    r2 = await client.get(f"/api/blog/{slug}")
    assert r1.json()["view_count"] == 1
    assert r2.json()["view_count"] == 2


async def test_get_blog_404_for_draft(client, auth_client):
    cr = await auth_client.post(
        "/api/blog", json={"title": "Taslak", "body": "...", "is_published": False}
    )
    r = await client.get(f"/api/blog/{cr.json()['slug']}")
    assert r.status_code == 404


async def test_get_blog_404_for_nonexistent(client):
    r = await client.get("/api/blog/yok-boyle-yazi")
    assert r.status_code == 404


async def test_create_blog_auto_slug(auth_client):
    r = await auth_client.post(
        "/api/blog",
        json={
            "title": "Profesyonel Matkap Seçimi 2026",
            "body": "İçerik...",
        },
    )
    assert r.status_code == 201
    slug = r.json()["slug"]
    assert "matkap" in slug.lower() or "profesyonel" in slug.lower()


async def test_create_blog_unique_slug(auth_client):
    a = await auth_client.post("/api/blog", json={"title": "Aynı Başlık", "body": "..."})
    b = await auth_client.post("/api/blog", json={"title": "Aynı Başlık", "body": "..."})
    assert a.json()["slug"] != b.json()["slug"]


async def test_create_blog_sets_published_at(auth_client):
    r = await auth_client.post(
        "/api/blog",
        json={
            "title": "Yayınlanan",
            "body": "...",
            "is_published": True,
        },
    )
    assert r.json()["published_at"] is not None

    r2 = await auth_client.post(
        "/api/blog",
        json={
            "title": "Taslak",
            "body": "...",
            "is_published": False,
        },
    )
    assert r2.json()["published_at"] is None


async def test_create_blog_requires_permission(client):
    r = await client.post("/api/blog", json={"title": "X", "body": "..."})
    assert r.status_code == 401


async def test_update_blog_ok(auth_client):
    cr = await auth_client.post("/api/blog", json={"title": "Eski", "body": "..."})
    pid = cr.json()["id"]
    r = await auth_client.put(
        f"/api/blog/{pid}",
        json={
            "title": "Yeni Başlık",
            "body": "Yeni içerik",
            "tags": ["güncel"],
            "is_published": False,
        },
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Yeni Başlık"
    assert r.json()["tags"] == ["güncel"]


async def test_update_blog_first_publish_stamps_date(auth_client):
    cr = await auth_client.post(
        "/api/blog", json={"title": "Taslak", "body": "...", "is_published": False}
    )
    assert cr.json()["published_at"] is None
    pid = cr.json()["id"]

    r = await auth_client.put(
        f"/api/blog/{pid}",
        json={
            "title": "Taslak",
            "body": "...",
            "is_published": True,
        },
    )
    assert r.json()["published_at"] is not None


async def test_update_blog_404(auth_client):
    r = await auth_client.put("/api/blog/99999", json={"title": "X", "body": "..."})
    assert r.status_code == 404


async def test_admin_list_includes_drafts(auth_client):
    pub = await auth_client.post(
        "/api/blog", json={"title": "Pub", "body": "...", "is_published": True}
    )
    draft = await auth_client.post(
        "/api/blog", json={"title": "Draft", "body": "...", "is_published": False}
    )
    r = await auth_client.get("/api/blog/admin/all")
    ids = [p["id"] for p in r.json()]
    assert pub.json()["id"] in ids
    assert draft.json()["id"] in ids


async def test_delete_blog_ok(auth_client):
    cr = await auth_client.post("/api/blog", json={"title": "Silinecek", "body": "..."})
    pid = cr.json()["id"]
    r = await auth_client.delete(f"/api/blog/{pid}")
    assert r.status_code == 204
    listed = await auth_client.get("/api/blog/admin/all")
    assert all(p["id"] != pid for p in listed.json())


async def test_delete_blog_404(auth_client):
    r = await auth_client.delete("/api/blog/99999")
    assert r.status_code == 404
