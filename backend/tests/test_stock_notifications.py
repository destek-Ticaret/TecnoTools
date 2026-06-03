"""Stok geldi bildirimi (back-in-stock)."""
import pytest

from app.models import Product


async def _make_product(db_session, name="Matkap", price=100, stock=0):
    p = Product(name=name, sub="sub", price=price, stock=stock, is_active=True)
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_subscribe_restock(client, db_session):
    p = await _make_product(db_session, stock=0)
    r = await client.post(f"/api/products/{p.id}/notify-restock", json={"email": "x@example.com"})
    assert r.status_code == 201
    assert r.json()["already_subscribed"] is False


async def test_subscribe_idempotent(client, db_session):
    p = await _make_product(db_session, stock=0)
    await client.post(f"/api/products/{p.id}/notify-restock", json={"email": "x@example.com"})
    r2 = await client.post(f"/api/products/{p.id}/notify-restock", json={"email": "x@example.com"})
    assert r2.status_code == 201
    assert r2.json()["already_subscribed"] is True


async def test_subscribe_missing_product(client):
    r = await client.post("/api/products/999999/notify-restock", json={"email": "x@example.com"})
    assert r.status_code == 404


async def test_notify_restocked_sends(client, db_session, monkeypatch):
    from app.routers import stock_notifications as sn

    p = await _make_product(db_session, stock=0)
    await client.post(f"/api/products/{p.id}/notify-restock", json={"email": "a@example.com"})

    sent = []

    async def fake_send_email(to, subject, html):
        sent.append(to)
        return True

    monkeypatch.setattr(sn, "send_email", fake_send_email)

    # stok hâlâ 0 → kimseye gitmez
    assert await sn.notify_restocked(db_session, p.id) == 0

    # stoğa gir → bekleyene gönderilir
    p.stock = 3
    await db_session.commit()
    n = await sn.notify_restocked(db_session, p.id)
    assert n == 1 and sent == ["a@example.com"]

    # ikinci çağrı: zaten bildirildi → 0
    assert await sn.notify_restocked(db_session, p.id) == 0
