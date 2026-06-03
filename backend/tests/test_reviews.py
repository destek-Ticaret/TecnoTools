"""Ürün yorumları — public oluşturma + moderasyon + rating yeniden hesabı."""

from app.models import Product


async def _make_product(db_session, name="Matkap", price=100, stock=5):
    p = Product(name=name, sub="sub", price=price, stock=stock, is_active=True)
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


def _review(**kw):
    base = {
        "customer_name": "Ali Veli",
        "customer_email": "ali@example.com",
        "rating": 5,
        "title": "Harika",
        "body": "Gerçekten çok memnun kaldım.",
    }
    base.update(kw)
    return base


async def test_create_review_pending(client, db_session):
    p = await _make_product(db_session)
    r = await client.post(f"/api/products/{p.id}/reviews", json=_review())
    assert r.status_code == 201, r.text
    assert r.json()["is_approved"] is False


async def test_create_review_honeypot(client, db_session):
    p = await _make_product(db_session)
    r = await client.post(f"/api/products/{p.id}/reviews", json=_review(website="bot"))
    assert r.status_code == 422  # max_length=0 schema reddi


async def test_create_review_invalid_rating(client, db_session):
    p = await _make_product(db_session)
    r = await client.post(f"/api/products/{p.id}/reviews", json=_review(rating=9))
    assert r.status_code == 422


async def test_public_list_only_approved(client, db_session, auth_client):
    p = await _make_product(db_session)
    rv = (await client.post(f"/api/products/{p.id}/reviews", json=_review())).json()
    # public boş (onaysız)
    assert (await client.get(f"/api/products/{p.id}/reviews")).json() == []
    # admin onayla → rating yeniden hesaplanır
    upd = await auth_client.patch(f"/api/admin/reviews/{rv['id']}", json={"is_approved": True})
    assert upd.status_code == 200
    pub = (await client.get(f"/api/products/{p.id}/reviews")).json()
    assert len(pub) == 1 and pub[0]["id"] == rv["id"]
    # ürün rating güncellendi
    prod = (await client.get(f"/api/products/{p.id}")).json()
    assert prod["rating"] == 5.0 and prod["review_count"] == 1


async def test_admin_delete_recalcs(client, db_session, auth_client):
    p = await _make_product(db_session)
    rv = (await client.post(f"/api/products/{p.id}/reviews", json=_review(rating=4))).json()
    await auth_client.patch(f"/api/admin/reviews/{rv['id']}", json={"is_approved": True})
    # sil → review_count 0'a düşmeli
    d = await auth_client.delete(f"/api/admin/reviews/{rv['id']}")
    assert d.status_code == 204
    prod = (await client.get(f"/api/products/{p.id}")).json()
    assert prod["review_count"] == 0


async def test_admin_list_requires_auth(client):
    assert (await client.get("/api/admin/reviews")).status_code == 401
