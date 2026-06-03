"""Sağlık kontrolü + temel endpoint mevcudiyeti."""


async def test_health_ok(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


async def test_openapi_schema_loads(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    # Kritik endpoint'lerin schema'da olması
    paths = schema["paths"]
    assert "/api/auth/login" in paths
    assert "/api/products" in paths
    assert "/api/orders/checkout" in paths
    assert "/api/payments/paytr/callback" in paths
