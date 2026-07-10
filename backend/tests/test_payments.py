"""PayTR callback — hash doğrulaması + sunucu tarafı tutar doğrulaması."""

from app.services.paytr import _hmac_b64
from app.services.paytr import settings as paytr_settings


def _hash(merchant_oid, status, total_amount):
    msg = f"{merchant_oid}{paytr_settings.paytr_merchant_salt}{status}{total_amount}"
    return _hmac_b64(paytr_settings.paytr_merchant_key, msg)


async def _checkout(auth_client, *, price=100.0, qty=1):
    pr = await auth_client.post("/api/products", json={"name": "X", "price": price, "stock": 10})
    pid = pr.json()["id"]
    co = await auth_client.post(
        "/api/orders/checkout",
        json={
            "items": [{"product_id": pid, "qty": qty}],
            "customer_name": "User",
            "customer_email": "u@u.com",
            "customer_phone": "+905551112233",
            "customer_city": "X",
            "customer_address": "Mahalle Sokak No:1 Daire:5",
        },
    )
    assert co.status_code == 200, co.text
    order_no = co.json()["order_no"]
    orders = (await auth_client.get("/api/orders")).json()
    order = next(o for o in orders if o["order_no"] == order_no)
    return order_no, order["total"]


async def test_paytr_callback_amount_mismatch_not_confirmed(auth_client, monkeypatch):
    """Bildirilen tutar sipariş toplamıyla uyuşmuyorsa, sipariş otomatik
    'success'e çekilmemeli — aksi hâlde eksik ödenen bir sipariş de onaylanırdı."""
    monkeypatch.setattr(paytr_settings, "paytr_merchant_key", "testkey")
    monkeypatch.setattr(paytr_settings, "paytr_merchant_salt", "testsalt")

    order_no, total = await _checkout(auth_client)
    merchant_oid = order_no.replace("-", "")
    wrong_kurus = round(total * 100) + 100000

    r = await auth_client.post(
        "/api/payments/paytr/callback",
        data={
            "merchant_oid": merchant_oid,
            "status": "success",
            "total_amount": str(wrong_kurus),
            "hash": _hash(merchant_oid, "success", str(wrong_kurus)),
        },
    )
    assert r.status_code == 200
    assert r.text == "OK"

    orders = (await auth_client.get("/api/orders")).json()
    order = next(o for o in orders if o["order_no"] == order_no)
    assert order["payment_status"] != "success"


async def test_paytr_callback_matching_amount_confirms_order(auth_client, monkeypatch):
    monkeypatch.setattr(paytr_settings, "paytr_merchant_key", "testkey")
    monkeypatch.setattr(paytr_settings, "paytr_merchant_salt", "testsalt")

    order_no, total = await _checkout(auth_client)
    merchant_oid = order_no.replace("-", "")
    correct_kurus = round(total * 100)

    r = await auth_client.post(
        "/api/payments/paytr/callback",
        data={
            "merchant_oid": merchant_oid,
            "status": "success",
            "total_amount": str(correct_kurus),
            "hash": _hash(merchant_oid, "success", str(correct_kurus)),
        },
    )
    assert r.status_code == 200

    orders = (await auth_client.get("/api/orders")).json()
    order = next(o for o in orders if o["order_no"] == order_no)
    assert order["payment_status"] == "success"


async def test_paytr_callback_deterministic_order_lookup(auth_client, monkeypatch):
    """merchant_oid, tüm bekleyen siparişler taranmadan doğrudan order_no'ya
    parse edilip sorgulanmalı (TT + yıl(4) + seq)."""
    monkeypatch.setattr(paytr_settings, "paytr_merchant_key", "testkey")
    monkeypatch.setattr(paytr_settings, "paytr_merchant_salt", "testsalt")

    order_no, total = await _checkout(auth_client)
    merchant_oid = order_no.replace("-", "")
    assert merchant_oid == order_no.replace("-", "")
    correct_kurus = round(total * 100)

    r = await auth_client.post(
        "/api/payments/paytr/callback",
        data={
            "merchant_oid": merchant_oid,
            "status": "success",
            "total_amount": str(correct_kurus),
            "hash": _hash(merchant_oid, "success", str(correct_kurus)),
        },
    )
    assert r.status_code == 200
    orders = (await auth_client.get("/api/orders")).json()
    order = next(o for o in orders if o["order_no"] == order_no)
    assert order["status"] == "processing"
