"""Ürün varyantları — Faz 1 (backend temel): CRUD upsert + varyant-farkında stok.

Kapsam:
  - Varyantla ürün oluşturma; admin çıktısında ham fiyat (boş=None)
  - Public çıktı: effective_stock = aktif varyant stoklarının toplamı, fiyat
    çözülmüş (boşsa ürün fiyatı)
  - Update upsert: id ile güncelle, id'siz ekle, payload'da olmayanı sil
  - variants=None gönderilirse mevcut varyantlara dokunulmaz
  - Varyantsız ürün eski davranışını korur
"""


async def _create_with_variants(auth_client, product_stock=0):
    return await auth_client.post(
        "/api/products",
        json={
            "name": "Tişört",
            "price": 100,
            "stock": product_stock,
            "variants": [
                {"name": "Kırmızı / S", "options": {"Renk": "Kırmızı", "Beden": "S"}, "stock": 3},
                {"name": "Kırmızı / M", "options": {"Beden": "M"}, "stock": 5, "price": 120},
                {"name": "Mavi / L", "stock": 0},
            ],
        },
    )


async def test_create_product_with_variants(auth_client):
    r = await _create_with_variants(auth_client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["variants"]) == 3
    by_name = {v["name"]: v for v in body["variants"]}
    assert by_name["Kırmızı / M"]["price"] == 120
    assert by_name["Kırmızı / S"]["price"] is None  # admin: ham; boş=ürün fiyatına düşer
    assert by_name["Kırmızı / S"]["options"] == {"Renk": "Kırmızı", "Beden": "S"}


async def test_public_effective_stock_sums_variants(auth_client):
    r = await _create_with_variants(auth_client, product_stock=999)
    pid = r.json()["id"]
    j = (await auth_client.get(f"/api/products/{pid}")).json()
    # varyantlı ürün → effective = aktif varyant stok toplamı (3+5+0); ürün stoğu (999) yoksayılır
    assert j["effective_stock"] == 8
    pv = {v["name"]: v for v in j["variants"]}
    assert pv["Kırmızı / S"]["price"] == 100  # ürün fiyatına düştü
    assert pv["Kırmızı / M"]["price"] == 120  # kendi fiyatı
    assert pv["Kırmızı / S"]["effective_stock"] == 3


async def test_update_variants_upsert_and_delete(auth_client):
    r = await _create_with_variants(auth_client)
    pid = r.json()["id"]
    s_id = next(v["id"] for v in r.json()["variants"] if v["name"] == "Kırmızı / S")
    upd = await auth_client.put(
        f"/api/products/{pid}",
        json={
            "name": "Tişört",
            "price": 100,
            "stock": 0,
            "variants": [
                {"id": s_id, "name": "Kırmızı / S", "stock": 10},  # id ile güncelle
                {"name": "Yeşil / XL", "stock": 2},  # id'siz → yeni
            ],
        },
    )
    assert upd.status_code == 200, upd.text
    vs = {v["name"]: v for v in upd.json()["variants"]}
    assert set(vs) == {"Kırmızı / S", "Yeşil / XL"}  # M ve L silindi
    assert vs["Kırmızı / S"]["id"] == s_id  # id korundu (sipariş referansları sağlam)
    assert vs["Kırmızı / S"]["stock"] == 10


async def test_update_variants_none_leaves_untouched(auth_client):
    r = await _create_with_variants(auth_client)
    pid = r.json()["id"]
    upd = await auth_client.put(
        f"/api/products/{pid}", json={"name": "Tişört X", "price": 110, "stock": 0}
    )
    assert upd.status_code == 200
    assert len(upd.json()["variants"]) == 3  # variants gönderilmedi → dokunulmadı


async def test_product_without_variants_unchanged(auth_client):
    r = await auth_client.post("/api/products", json={"name": "Tekil", "price": 50, "stock": 7})
    pid = r.json()["id"]
    j = (await auth_client.get(f"/api/products/{pid}")).json()
    assert j["variants"] == []
    assert j["effective_stock"] == 7  # ürün stoğu (varyant yok)


# ── Faz 2: checkout + stok varyant bazlı ──────────────────────────────
async def _checkout(auth_client, items):
    return await auth_client.post(
        "/api/orders/checkout",
        json={
            "items": items,
            "customer_name": "Ali Veli",
            "customer_email": "a@a.com",
            "customer_phone": "+905551112233",
            "customer_city": "X",
            "customer_address": "Mahalle Sokak No:1 Daire:5",
            "payment_method": "wire",
        },
    )


def _vid(create_resp, name):
    return next(v["id"] for v in create_resp.json()["variants"] if v["name"] == name)


async def test_checkout_requires_variant_for_variant_product(auth_client):
    r = await _create_with_variants(auth_client)
    co = await _checkout(auth_client, [{"product_id": r.json()["id"], "qty": 1}])
    assert co.status_code == 400
    assert "varyant" in co.json()["detail"].lower()


async def test_checkout_rejects_invalid_variant(auth_client):
    r = await _create_with_variants(auth_client)
    co = await _checkout(
        auth_client, [{"product_id": r.json()["id"], "qty": 1, "variant_id": 999999}]
    )
    assert co.status_code == 400


async def test_checkout_variant_stock_insufficient(auth_client):
    r = await _create_with_variants(auth_client)
    pid = r.json()["id"]
    co = await _checkout(
        auth_client, [{"product_id": pid, "qty": 5, "variant_id": _vid(r, "Kırmızı / S")}]
    )  # S stoğu 3
    assert co.status_code == 409


async def test_checkout_variant_deducts_variant_stock_on_confirm(auth_client):
    r = await _create_with_variants(auth_client)
    pid = r.json()["id"]
    co = await _checkout(
        auth_client, [{"product_id": pid, "qty": 2, "variant_id": _vid(r, "Kırmızı / M")}]
    )
    assert co.status_code == 200, co.text
    order_no = co.json()["order_no"]
    await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "processing"})
    pv = {v["name"]: v for v in (await auth_client.get(f"/api/products/{pid}")).json()["variants"]}
    assert pv["Kırmızı / M"]["effective_stock"] == 3  # 5 - 2 (varyanttan düştü)
    assert pv["Kırmızı / S"]["effective_stock"] == 3  # dokunulmadı


async def test_checkout_uses_variant_price(auth_client):
    r = await _create_with_variants(auth_client)
    pid = r.json()["id"]
    co = await _checkout(
        auth_client, [{"product_id": pid, "qty": 1, "variant_id": _vid(r, "Kırmızı / M")}]
    )  # M fiyatı 120 (ürün fiyatı 100)
    order_no = co.json()["order_no"]
    orders = (await auth_client.get("/api/orders")).json()
    o = next(x for x in orders if x["order_no"] == order_no)
    assert o["subtotal"] == 120


async def test_cancel_restores_variant_stock(auth_client):
    r = await _create_with_variants(auth_client)
    pid = r.json()["id"]
    co = await _checkout(
        auth_client, [{"product_id": pid, "qty": 2, "variant_id": _vid(r, "Kırmızı / M")}]
    )
    order_no = co.json()["order_no"]
    await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "processing"})
    await auth_client.patch(f"/api/orders/{order_no}/status", json={"status": "cancelled"})
    pv = {v["name"]: v for v in (await auth_client.get(f"/api/products/{pid}")).json()["variants"]}
    assert pv["Kırmızı / M"]["effective_stock"] == 5  # geri eklendi
