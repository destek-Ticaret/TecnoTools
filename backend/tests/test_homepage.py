"""Ana sayfa bölümleri yönetimi."""


async def test_list_sections_public_ok(client):
    r = await client.get("/api/homepage")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_list_sections_filters_inactive(client, auth_client):
    a = await auth_client.post(
        "/api/homepage", json={"kind": "hero", "title": "A", "is_active": True}
    )
    b = await auth_client.post(
        "/api/homepage", json={"kind": "hero", "title": "B", "is_active": False}
    )
    r = await client.get("/api/homepage")
    ids = [x["id"] for x in r.json()]
    assert a.json()["id"] in ids
    assert b.json()["id"] not in ids


async def test_admin_list_includes_inactive(auth_client):
    a = await auth_client.post(
        "/api/homepage", json={"kind": "html", "title": "A", "is_active": True}
    )
    b = await auth_client.post(
        "/api/homepage", json={"kind": "html", "title": "B", "is_active": False}
    )
    r = await auth_client.get("/api/homepage/admin/all")
    ids = [x["id"] for x in r.json()]
    assert a.json()["id"] in ids
    assert b.json()["id"] in ids


async def test_create_section_ok(auth_client):
    r = await auth_client.post(
        "/api/homepage",
        json={
            "kind": "product_carousel",
            "title": "Yeni Gelenler",
            "config": {"category_id": 5, "limit": 12},
            "sort_order": 2,
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "product_carousel"
    assert body["config"] == {"category_id": 5, "limit": 12}


async def test_create_section_invalid_kind_400(auth_client):
    r = await auth_client.post("/api/homepage", json={"kind": "weird_block", "title": "X"})
    assert r.status_code == 400
    assert "Geçersiz bölüm türü" in r.json()["detail"]


async def test_create_section_requires_permission(client):
    r = await client.post("/api/homepage", json={"kind": "hero", "title": "X"})
    assert r.status_code == 401


async def test_update_section_ok(auth_client):
    a = await auth_client.post("/api/homepage", json={"kind": "hero", "title": "Eski"})
    sid = a.json()["id"]
    r = await auth_client.put(
        f"/api/homepage/{sid}",
        json={
            "kind": "html",
            "title": "Yeni",
            "config": {"html": "<h1>Merhaba</h1>"},
            "sort_order": 0,
        },
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Yeni"
    assert r.json()["kind"] == "html"


async def test_update_section_404(auth_client):
    r = await auth_client.put("/api/homepage/99999", json={"kind": "hero", "title": "X"})
    assert r.status_code == 404


async def test_reorder_sections(auth_client):
    ids = []
    for i in range(4):
        r = await auth_client.post("/api/homepage", json={"kind": "hero", "title": f"S{i}"})
        ids.append(r.json()["id"])
    # Yeni sıra: sondan başa (embed=True)
    new_order = list(reversed(ids))
    r = await auth_client.post("/api/homepage/reorder", json={"order": new_order})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["count"] == 4


async def test_delete_section_ok(auth_client):
    a = await auth_client.post("/api/homepage", json={"kind": "blog", "title": "Sil"})
    sid = a.json()["id"]
    r = await auth_client.delete(f"/api/homepage/{sid}")
    assert r.status_code == 204
    r2 = await auth_client.get("/api/homepage/admin/all")
    assert all(x["id"] != sid for x in r2.json())


async def test_delete_section_404(auth_client):
    r = await auth_client.delete("/api/homepage/99999")
    assert r.status_code == 404
