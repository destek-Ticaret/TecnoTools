"""Otomatik fiyatlandırma kuralları — CRUD + preview + apply."""


async def test_list_rules_requires_permission(client, auth_client):
    assert (await client.get("/api/pricing-rules")).status_code == 401
    r = await auth_client.get("/api/pricing-rules")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_create_rule_ok(auth_client):
    r = await auth_client.post(
        "/api/pricing-rules",
        json={
            "name": "Tüm ürünlerde %10 indirim",
            "scope_type": "all",
            "strategy": "percent",
            "value": -10,
        },
    )
    assert r.status_code == 201
    assert r.json()["name"] == "Tüm ürünlerde %10 indirim"


async def test_create_rule_invalid_scope_400(auth_client):
    r = await auth_client.post(
        "/api/pricing-rules",
        json={
            "name": "X",
            "scope_type": "brand",  # geçersiz
        },
    )
    assert r.status_code == 400
    assert "Geçersiz kapsam" in r.json()["detail"]


async def test_create_rule_invalid_strategy_400(auth_client):
    r = await auth_client.post(
        "/api/pricing-rules",
        json={
            "name": "X",
            "scope_type": "all",
            "strategy": "magic",  # geçersiz
        },
    )
    assert r.status_code == 400
    assert "Geçersiz strateji" in r.json()["detail"]


async def test_category_scope_requires_scope_id(auth_client):
    r = await auth_client.post(
        "/api/pricing-rules",
        json={
            "name": "X",
            "scope_type": "category",
            "scope_id": None,
            "strategy": "percent",
            "value": 5,
        },
    )
    assert r.status_code == 400
    assert "scope_id" in r.json()["detail"]


async def test_create_rule_validates_name_length(auth_client):
    r = await auth_client.post(
        "/api/pricing-rules",
        json={"name": "", "scope_type": "all", "strategy": "percent", "value": 5},
    )
    assert r.status_code == 422  # pydantic min_length=1


async def test_update_rule_ok(auth_client):
    cr = await auth_client.post(
        "/api/pricing-rules",
        json={
            "name": "Eski",
            "scope_type": "all",
            "strategy": "percent",
            "value": 5,
        },
    )
    rid = cr.json()["id"]
    r = await auth_client.put(
        f"/api/pricing-rules/{rid}",
        json={
            "name": "Yeni",
            "scope_type": "all",
            "strategy": "fixed",
            "value": 99.9,
            "priority": 5,
        },
    )
    assert r.status_code == 200
    assert r.json()["strategy"] == "fixed"
    assert r.json()["priority"] == 5


async def test_update_rule_404(auth_client):
    r = await auth_client.put(
        "/api/pricing-rules/99999",
        json={
            "name": "X",
            "scope_type": "all",
            "strategy": "percent",
            "value": 0,
        },
    )
    assert r.status_code == 404


async def test_preview_rule_returns_count(auth_client, db_session):
    from app.models import Product

    for i in range(3):
        db_session.add(Product(name=f"Ürün {i}", price=100.0, stock=10, is_active=True))
    await db_session.commit()

    cr = await auth_client.post(
        "/api/pricing-rules",
        json={
            "name": "Tümünde %20 indirim",
            "scope_type": "all",
            "strategy": "percent",
            "value": -20,
            "priority": 10,
        },
    )
    rid = cr.json()["id"]

    r = await auth_client.post(f"/api/pricing-rules/{rid}/preview")
    assert r.status_code == 200
    body = r.json()
    assert "affected" in body or "preview" in body or "items" in body  # service contract'ı


async def test_apply_rule_changes_prices(auth_client, db_session):
    from app.models import Product

    p = Product(name="Pahalı Matkap", price=200.0, stock=5, is_active=True)
    db_session.add(p)
    await db_session.commit()

    cr = await auth_client.post(
        "/api/pricing-rules",
        json={
            "name": "Tümünde %25 indirim",
            "scope_type": "all",
            "strategy": "percent",
            "value": -25,
            "priority": 1,
            "is_active": True,
        },
    )
    rid = cr.json()["id"]
    r = await auth_client.post(f"/api/pricing-rules/{rid}/apply")
    assert r.status_code == 200

    # Ürünün fiyatı güncellendi mi?
    pub = await auth_client.get(f"/api/products/{p.id}")
    assert pub.json()["price"] < 200.0  # 200 * 0.75 = 150


async def test_preview_404(auth_client):
    r = await auth_client.post("/api/pricing-rules/99999/preview")
    assert r.status_code == 404


async def test_apply_404(auth_client):
    r = await auth_client.post("/api/pricing-rules/99999/apply")
    assert r.status_code == 404


async def test_delete_rule_ok(auth_client):
    cr = await auth_client.post(
        "/api/pricing-rules",
        json={
            "name": "Silinecek",
            "scope_type": "all",
            "strategy": "percent",
            "value": 0,
        },
    )
    rid = cr.json()["id"]
    r = await auth_client.delete(f"/api/pricing-rules/{rid}")
    assert r.status_code == 204
    listed = await auth_client.get("/api/pricing-rules")
    assert all(rule["id"] != rid for rule in listed.json())


async def test_delete_rule_404(auth_client):
    r = await auth_client.delete("/api/pricing-rules/99999")
    assert r.status_code == 404
