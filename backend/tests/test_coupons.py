"""Kupon CRUD + validate endpoint."""
from datetime import datetime, timedelta, timezone


async def test_coupon_crud(auth_client):
    r = await auth_client.post("/api/coupons", json={
        "code": "TEST10", "type": "percent", "value": 10, "min_order": 100,
    })
    assert r.status_code == 201
    cid = r.json()["id"]

    # Validate public
    v = await auth_client.get("/api/coupons/validate/TEST10")
    assert v.status_code == 200
    assert v.json()["code"] == "TEST10"

    # Lower case normalize
    v2 = await auth_client.get("/api/coupons/validate/test10")
    assert v2.status_code == 200

    # Sil
    d = await auth_client.delete(f"/api/coupons/{cid}")
    assert d.status_code == 204
    v3 = await auth_client.get("/api/coupons/validate/TEST10")
    assert v3.status_code == 404


async def test_expired_coupon(auth_client):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    r = await auth_client.post("/api/coupons", json={
        "code": "OLD", "type": "fixed", "value": 50, "expires_at": past,
    })
    assert r.status_code == 201
    v = await auth_client.get("/api/coupons/validate/OLD")
    assert v.status_code == 410  # süresi geçti


async def test_coupon_max_uses_exhausted(auth_client, db_session):
    from app.models import Coupon
    c = Coupon(code="ONCE", type="fixed", value=20, min_order=0, max_uses=1, used_count=1, is_active=True)
    db_session.add(c)
    await db_session.commit()
    v = await auth_client.get("/api/coupons/validate/ONCE")
    assert v.status_code == 409
