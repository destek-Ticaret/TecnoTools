"""Taksit (installment) bilgisi endpoint'i — mock mod (iyzico kimliği yok)."""


async def test_installments_returns_mock_plans(client):
    r = await client.get("/api/payments/installments", params={"bin": "454671", "price": 1000})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mock"] is True
    assert body["price"] == 1000.0
    counts = {o["count"]: o for o in body["options"]}
    # Tek çekim + bilinen taksit kademeleri
    assert 1 in counts and 6 in counts and 12 in counts
    # Tek çekimde komisyon yok, tutar = fiyat
    assert counts[1]["total_price"] == 1000.0
    assert counts[1]["installment_price"] == 1000.0
    # 6 taksitte per-taksit ≈ total / 6 (yuvarlama toleransı)
    o6 = counts[6]
    assert abs(o6["installment_price"] * 6 - o6["total_price"]) < 0.10
    assert o6["total_price"] >= 1000.0  # komisyonlu


async def test_installments_strips_nondigits_and_uses_bin(client):
    r = await client.get("/api/payments/installments", params={"bin": "4546 71 99", "price": 500})
    assert r.status_code == 200
    assert r.json()["bin"] == "45467199"  # boşluklar temizlendi, 8 haneye kırpıldı


async def test_installments_rejects_short_bin(client):
    r = await client.get("/api/payments/installments", params={"bin": "1234", "price": 1000})
    assert r.status_code == 400
    assert "BIN" in r.text


async def test_installments_rejects_nonpositive_price(client):
    r = await client.get("/api/payments/installments", params={"bin": "454671", "price": 0})
    assert r.status_code == 422  # Query gt=0
