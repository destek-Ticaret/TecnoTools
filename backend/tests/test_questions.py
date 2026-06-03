"""Ürün soru-cevap (Q&A) testleri."""

from app.models import Product


async def _make_product(db_session, name="Matkap", price=100, stock=5):
    p = Product(name=name, sub="sub", price=price, stock=stock, is_active=True)
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_ask_question_pending(client, db_session):
    p = await _make_product(db_session)
    r = await client.post(
        f"/api/products/{p.id}/questions",
        json={"customer_name": "Ali", "question": "Garantisi var mı?"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_published"] is False
    assert body["answer"] is None


async def test_ask_question_honeypot(client, db_session):
    p = await _make_product(db_session)
    r = await client.post(
        f"/api/products/{p.id}/questions",
        json={"customer_name": "Bot", "question": "spam spam", "website": "x"},
    )
    assert r.status_code == 422  # max_length=0 schema reddi


async def test_ask_question_missing_product(client):
    r = await client.post(
        "/api/products/999999/questions",
        json={"customer_name": "Ali", "question": "Soru?"},
    )
    assert r.status_code == 404


async def test_public_list_only_published_answered(client, db_session, auth_client):
    p = await _make_product(db_session)
    # iki soru sor
    q1 = (
        await client.post(
            f"/api/products/{p.id}/questions",
            json={"customer_name": "Ali", "question": "Soru bir?"},
        )
    ).json()
    await client.post(
        f"/api/products/{p.id}/questions", json={"customer_name": "Ayse", "question": "Soru iki?"}
    )

    # public liste boş (hiçbiri yayınlı değil)
    pub = await client.get(f"/api/products/{p.id}/questions")
    assert pub.json() == []

    # admin: q1'i cevapla + yayınla
    upd = await auth_client.patch(
        f"/api/admin/questions/{q1['id']}",
        json={"answer": "Evet, 2 yıl garanti.", "is_published": True},
    )
    assert upd.status_code == 200
    assert upd.json()["answer"] == "Evet, 2 yıl garanti."

    pub2 = await client.get(f"/api/products/{p.id}/questions")
    body = pub2.json()
    assert len(body) == 1
    assert body[0]["id"] == q1["id"]
    assert body[0]["answer"] == "Evet, 2 yıl garanti."


async def test_published_without_answer_hidden(client, db_session, auth_client):
    """Yayınlı ama cevapsız soru public listede görünmez."""
    p = await _make_product(db_session)
    q = (
        await client.post(
            f"/api/products/{p.id}/questions",
            json={"customer_name": "Ali", "question": "Cevapsız?"},
        )
    ).json()
    await auth_client.patch(f"/api/admin/questions/{q['id']}", json={"is_published": True})
    pub = await client.get(f"/api/products/{p.id}/questions")
    assert pub.json() == []


async def test_admin_filters_and_delete(client, db_session, auth_client):
    p = await _make_product(db_session)
    q = (
        await client.post(
            f"/api/products/{p.id}/questions",
            json={"customer_name": "Ali", "question": "Filtre sorusu?"},
        )
    ).json()

    # answered=False filtresi → 1 sonuç
    unanswered = await auth_client.get("/api/admin/questions", params={"answered": "false"})
    assert unanswered.status_code == 200
    assert len(unanswered.json()) == 1

    # cevapla → answered=True 1, answered=False 0
    await auth_client.patch(f"/api/admin/questions/{q['id']}", json={"answer": "Cevap."})
    assert (
        len((await auth_client.get("/api/admin/questions", params={"answered": "true"})).json())
        == 1
    )
    assert (
        len((await auth_client.get("/api/admin/questions", params={"answered": "false"})).json())
        == 0
    )

    # sil
    d = await auth_client.delete(f"/api/admin/questions/{q['id']}")
    assert d.status_code == 204
    assert len((await auth_client.get("/api/admin/questions")).json()) == 0


async def test_admin_requires_auth(client):
    r = await client.get("/api/admin/questions")
    assert r.status_code == 401
