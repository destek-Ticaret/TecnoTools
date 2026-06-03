"""Ürün arama uçları — autocomplete + fuzzy (typo toleransı)."""


from app.models import Product


async def _make(db_session, name, **kw):
    p = Product(
        name=name,
        sub=kw.get("sub"),
        price=kw.get("price", 100),
        stock=kw.get("stock", 5),
        is_active=kw.get("is_active", True),
        features=kw.get("features"),
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_autocomplete_prefix(client, db_session):
    await _make(db_session, "Akülü Matkap")
    await _make(db_session, "Akülü Vidalama")
    await _make(db_session, "Tornavida Seti")
    r = await client.get("/api/products/search/autocomplete", params={"q": "akü"})
    assert r.status_code == 200
    names = [x["name"] for x in r.json()]
    assert "Akülü Matkap" in names and "Akülü Vidalama" in names
    assert "Tornavida Seti" not in names


async def test_autocomplete_diacritic_insensitive(client, db_session):
    await _make(db_session, "Şarjlı Testere")
    # diakritiksiz sorgu da bulmalı
    r = await client.get("/api/products/search/autocomplete", params={"q": "sarj"})
    assert r.status_code == 200
    assert any(x["name"] == "Şarjlı Testere" for x in r.json())


async def test_autocomplete_excludes_inactive(client, db_session):
    await _make(db_session, "Gizli Ürün", is_active=False)
    r = await client.get("/api/products/search/autocomplete", params={"q": "gizli"})
    assert r.json() == []


async def test_fuzzy_typo_tolerance(client, db_session):
    await _make(db_session, "Matkap")
    # yazım hatası (harf yer değişimi): "matakp" → "matkap"
    r = await client.get("/api/products/search/fuzzy", params={"q": "matakp"})
    assert r.status_code == 200
    body = r.json()
    assert body and body[0]["name"] == "Matkap"
    assert "score" in body[0]


async def test_fuzzy_respects_min_score(client, db_session):
    await _make(db_session, "Çekiç")
    # alakasız sorgu yüksek eşikle boş dönmeli
    r = await client.get(
        "/api/products/search/fuzzy", params={"q": "buzdolabı", "min_score": "0.6"}
    )
    assert r.status_code == 200
    assert r.json() == []
