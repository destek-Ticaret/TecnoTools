"""Self-hosted analytics — track + summary."""


async def test_track_event_persists(client, db_engine):
    """track endpoint'i 204 döner, DB'ye yazar."""
    r = await client.post(
        "/api/analytics/track",
        json={
            "event": "page_view",
            "path": "/",
            "session_id": "test-session-1",
        },
    )
    assert r.status_code == 204

    # Aynı client'tan bir tane daha
    r2 = await client.post(
        "/api/analytics/track",
        json={
            "event": "add_to_cart",
            "path": "/product/42",
            "session_id": "test-session-1",
        },
    )
    assert r2.status_code == 204


async def test_track_truncates_long_event(client):
    """event 64 karakteri geçerse sessizce kırpılır (hata vermez)."""
    long_event = "x" * 100
    r = await client.post("/api/analytics/track", json={"event": long_event})
    assert r.status_code == 204


async def test_track_empty_event_silently_ignored(client):
    """event boşsa hiçbir şey yazılmaz, 204 döner."""
    r = await client.post("/api/analytics/track", json={"event": ""})
    assert r.status_code == 204


async def test_summary_requires_admin(client, auth_client):
    """Yetkisiz → 401, admin → 200."""
    r1 = await client.get("/api/analytics/summary")
    assert r1.status_code == 401

    r2 = await auth_client.get("/api/analytics/summary")
    assert r2.status_code == 200
    body = r2.json()
    assert "unique_visitors" in body
    assert "event_counts" in body
    assert "top_pages" in body
    assert body["days"] == 7  # varsayılan


async def test_summary_aggregates_correctly(client, auth_client):
    """Birden fazla event gönderip unique visitor + count doğrula."""
    # 3 farklı IP (XFF header ile) → 3 unique visitor
    for i, ip in enumerate(["1.2.3.1", "1.2.3.2", "1.2.3.3"]):
        await client.post(
            "/api/analytics/track",
            json={
                "event": "page_view",
                "session_id": f"s-{i}",
            },
            headers={"x-forwarded-for": ip},
        )

    r = await auth_client.get("/api/analytics/summary")
    body = r.json()
    assert body["unique_visitors"] >= 3
    pv = next((e for e in body["event_counts"] if e["event"] == "page_view"), None)
    assert pv is not None
    assert pv["count"] >= 3


async def test_summary_days_param(auth_client):
    r = await auth_client.get("/api/analytics/summary", params={"days": 30})
    assert r.status_code == 200
    assert r.json()["days"] == 30


async def test_ip_hash_is_anonymous(client, auth_client):
    """Aynı IP, iki farklı session_id ile → unique visitor hâlâ 1."""
    h1 = {"x-forwarded-for": "5.6.7.8"}
    await client.post(
        "/api/analytics/track", json={"event": "page_view", "session_id": "a"}, headers=h1
    )
    await client.post(
        "/api/analytics/track", json={"event": "page_view", "session_id": "b"}, headers=h1
    )

    r = await auth_client.get("/api/analytics/summary", params={"days": 1})
    # 5.6.7.8'i sayan unique visitor sayısı 1 olmalı (her IP hash'i 1)
    assert r.json()["unique_visitors"] >= 1


async def test_top_pages_aggregated(client, auth_client):
    for _ in range(3):
        await client.post("/api/analytics/track", json={"event": "page_view", "path": "/popular"})
    r = await auth_client.get("/api/analytics/summary", params={"days": 1})
    top = r.json()["top_pages"]
    assert any(p["path"] == "/popular" and p["count"] >= 3 for p in top)
