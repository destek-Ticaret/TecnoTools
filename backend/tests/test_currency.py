"""Para birimi endpoint'leri — ağ gerektirmeyen senaryolar."""


async def test_list_currencies(client):
    r = await client.get("/api/currency")
    assert r.status_code == 200
    body = r.json()
    assert body["base"] == "TRY"
    assert "TRY" in body["supported"]


async def test_rate_same_currency_is_one(client):
    # Aynı para birimi → ağ çağrısı yok, 1.0
    r = await client.get("/api/currency/rate", params={"base": "TRY", "quote": "TRY"})
    assert r.status_code == 200
    assert r.json()["rate"] == 1.0


async def test_rate_unsupported_400(client):
    r = await client.get("/api/currency/rate", params={"base": "TRY", "quote": "XYZ"})
    assert r.status_code == 400


async def test_rate_defaults_to_base(client):
    # base boş → BASE_CURRENCY; quote boş → BASE_CURRENCY; ikisi de TRY → 1.0
    r = await client.get("/api/currency/rate")
    assert r.status_code == 200
    body = r.json()
    assert body["base"] == "TRY" and body["quote"] == "TRY" and body["rate"] == 1.0
