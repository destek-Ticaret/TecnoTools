"""Sunucu-taraflı favoriler (wishlist) testleri."""
import pytest

from app.models import Customer, Product
from app.security import hash_password


REGISTER_PAYLOAD = {
    "email": "wish@example.com",
    "password": "WishStrong1!",
    "name": "Wish Tester",
    "phone": "+905551112233",
    "city": "İstanbul",
    "address": "Mahalle Sokak No:1 Daire:5",
    "marketing_opt_in": False,
}


async def _member_token(client):
    r = await client.post("/api/customer-auth/register", json=REGISTER_PAYLOAD)
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


async def _make_product(db_session, name="Matkap", price=100, stock=5):
    p = Product(name=name, sub="sub", price=price, stock=stock, is_active=True)
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_wishlist_requires_auth(client):
    r = await client.get("/api/wishlist")
    assert r.status_code == 401


async def test_add_list_remove(client, db_session):
    token = await _member_token(client)
    h = {"Authorization": f"Bearer {token}"}
    p = await _make_product(db_session)

    add = await client.post(f"/api/wishlist/{p.id}", headers=h)
    assert add.status_code == 201
    assert add.json()["already"] is False

    # idempotent
    add2 = await client.post(f"/api/wishlist/{p.id}", headers=h)
    assert add2.status_code == 201
    assert add2.json()["already"] is True

    lst = await client.get("/api/wishlist", headers=h)
    assert lst.status_code == 200
    body = lst.json()
    assert len(body) == 1 and body[0]["id"] == p.id

    rem = await client.delete(f"/api/wishlist/{p.id}", headers=h)
    assert rem.status_code == 200
    lst2 = await client.get("/api/wishlist", headers=h)
    assert lst2.json() == []


async def test_add_missing_product_404(client):
    token = await _member_token(client)
    h = {"Authorization": f"Bearer {token}"}
    r = await client.post("/api/wishlist/999999", headers=h)
    assert r.status_code == 404


async def test_merge_imports_local_favorites(client, db_session):
    token = await _member_token(client)
    h = {"Authorization": f"Bearer {token}"}
    p1 = await _make_product(db_session, name="A")
    p2 = await _make_product(db_session, name="B")

    # p1 zaten sunucuda
    await client.post(f"/api/wishlist/{p1.id}", headers=h)

    # localStorage'da p1 + p2 + olmayan id var → birleştir
    merged = await client.post(
        "/api/wishlist/merge",
        json={"product_ids": [p1.id, p2.id, 999999]},
        headers=h,
    )
    assert merged.status_code == 200
    ids = {x["id"] for x in merged.json()}
    assert ids == {p1.id, p2.id}


async def test_price_drop_notifies_wishlisters(client, db_session, monkeypatch):
    """Ürün fiyatı düşünce favorileyenlere notify_price_drop çağrılır."""
    from app.routers import wishlist as wl

    token = await _member_token(client)
    h = {"Authorization": f"Bearer {token}"}
    p = await _make_product(db_session, name="İndirimlik", price=200, stock=3)
    await client.post(f"/api/wishlist/{p.id}", headers=h)

    sent_to = []

    async def fake_send_email(to, subject, html):
        sent_to.append(to)
        return True

    monkeypatch.setattr(wl, "send_email", fake_send_email)

    n = await wl.notify_price_drop(db_session, p.id, old_price=200)
    # Fiyat hâlâ 200 (düşmedi) → 0
    assert n == 0

    # Fiyatı düşür, tekrar dene
    p.price = 150
    await db_session.commit()
    n2 = await wl.notify_price_drop(db_session, p.id, old_price=200)
    assert n2 == 1
    assert sent_to == ["wish@example.com"]
