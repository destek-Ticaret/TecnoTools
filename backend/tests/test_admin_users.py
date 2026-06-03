"""Multi-admin: kullanıcı CRUD + sertleştirme kuralları."""


async def test_primary_user_cannot_be_deleted(auth_client):
    # seed_admin id=1, is_primary=True
    users = await auth_client.get("/api/admin/users")
    primary = next(u for u in users.json() if u["is_primary"])
    r = await auth_client.delete(f"/api/admin/users/{primary['id']}")
    assert r.status_code == 403


async def test_cannot_change_own_role(auth_client):
    users = await auth_client.get("/api/admin/users")
    primary = next(u for u in users.json() if u["is_primary"])
    r = await auth_client.put(f"/api/admin/users/{primary['id']}", json={"role": "viewer"})
    assert r.status_code == 403


async def test_create_editor_and_login(client, auth_client):
    r = await auth_client.post(
        "/api/admin/users",
        json={
            "username": "editor1",
            "password": "EditorPass1!",
            "role": "editor",
        },
    )
    assert r.status_code == 201
    # Login dene
    login = await client.post(
        "/api/auth/login",
        json={
            "username": "editor1",
            "password": "EditorPass1!",
        },
    )
    assert login.status_code == 200
    # editor: ürün oluşturabilir, ama yeni kullanıcı oluşturamaz (admin gerekli)
    editor_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {editor_token}"}
    pr = await client.post(
        "/api/products", json={"name": "E", "price": 1, "stock": 1}, headers=headers
    )
    assert pr.status_code == 201
    nu = await client.post(
        "/api/admin/users",
        json={
            "username": "x",
            "password": "Whatever123!",
            "role": "editor",
        },
        headers=headers,
    )
    assert nu.status_code == 403


async def test_duplicate_username_rejected(auth_client):
    r1 = await auth_client.post(
        "/api/admin/users",
        json={
            "username": "dupe",
            "password": "Pass1234!",
            "role": "editor",
        },
    )
    assert r1.status_code == 201
    r2 = await auth_client.post(
        "/api/admin/users",
        json={
            "username": "dupe",
            "password": "Pass1234!",
            "role": "editor",
        },
    )
    assert r2.status_code == 409
