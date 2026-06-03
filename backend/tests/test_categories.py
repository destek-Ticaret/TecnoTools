"""Kategori CRUD."""


async def test_category_crud(auth_client):
    r = await auth_client.post("/api/categories", json={"name": "Elektrikli"})
    assert r.status_code == 201
    cid = r.json()["id"]

    r2 = await auth_client.get("/api/categories")
    assert any(c["id"] == cid for c in r2.json())

    # Aynı isim → çakışma
    dup = await auth_client.post("/api/categories", json={"name": "Elektrikli"})
    assert dup.status_code == 409

    # Sil
    d = await auth_client.delete(f"/api/categories/{cid}")
    assert d.status_code == 204


async def test_category_rename(auth_client):
    r = await auth_client.post("/api/categories", json={"name": "Eski Ad"})
    cid = r.json()["id"]
    upd = await auth_client.put(f"/api/categories/{cid}", json={"name": "Yeni Ad", "sort_order": 5})
    assert upd.status_code == 200
    body = upd.json()
    assert body["name"] == "Yeni Ad"
    assert body["sort_order"] == 5
    # GET sonrası da güncel
    listed = (await auth_client.get("/api/categories")).json()
    assert any(c["id"] == cid and c["name"] == "Yeni Ad" for c in listed)


async def test_category_rename_to_existing_name_conflict(auth_client):
    a = (await auth_client.post("/api/categories", json={"name": "Alfa"})).json()
    b = (await auth_client.post("/api/categories", json={"name": "Beta"})).json()
    # B'yi Alfa'ya değiştirmeye çalış → 409
    upd = await auth_client.put(f"/api/categories/{b['id']}", json={"name": "Alfa", "sort_order": 0})
    assert upd.status_code == 409


async def test_category_delete_returns_204(auth_client):
    """SQLite'da ON DELETE SET NULL davranışı PRAGMA gerektirir (PostgreSQL'de
    otomatik). Burada sadece 204 dönmesini doğrularız; FK davranışı backend'in
    DB constraint'ına bağlı.
    """
    cat = (await auth_client.post("/api/categories", json={"name": "Geçici"})).json()
    d = await auth_client.delete(f"/api/categories/{cat['id']}")
    assert d.status_code == 204
