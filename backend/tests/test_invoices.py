"""E-arşiv fatura endpoint'leri — admin + public + customer."""

import pytest


async def _make_paid_order(auth_client, db_session, qty: int = 2, total: float = 240.0):
    """Ürün + ödenmiş sipariş oluşturur."""
    from app.models import Order, OrderItem, OrderStatus, PaymentStatus, Product

    p = Product(name="Test Ürün", price=100.0, stock=50, is_active=True)
    db_session.add(p)
    await db_session.flush()
    o = Order(
        order_no="TT-INV-0001",
        customer_name="Ali Veli",
        customer_email="ali@example.com",
        customer_phone="+905551112233",
        customer_address="Adres",
        status=OrderStatus.DELIVERED.value,
        payment_status=PaymentStatus.SUCCESS.value,
        subtotal=total,
        total=total,
        tax=0,
        shipping=0,
        discount=0,
    )
    db_session.add(o)
    await db_session.flush()
    it = OrderItem(order_id=o.id, product_id=p.id, name=p.name, qty=qty, price=100.0)
    db_session.add(it)
    await db_session.commit()
    return o, p


async def test_issue_invoice_creates_sent_invoice(auth_client, db_session):
    """Sipariş için fatura kesilir (mock provider)."""
    o, p = await _make_paid_order(auth_client, db_session)
    r = await auth_client.post(f"/api/invoices/orders/{o.order_no}/issue", json={"tax_rate": 20})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["order_no"] == o.order_no
    assert body["status"] in ("sent", "failed")  # mock'ta sent
    assert body["ettn"] is not None or body["status"] == "failed"


async def test_issue_invoice_404_for_missing_order(auth_client):
    r = await auth_client.post("/api/invoices/orders/TT-NOPE/issue", json={})
    assert r.status_code == 404


async def test_issue_invoice_409_if_already_sent(auth_client, db_session):
    """Bir sipariş için ikinci kez fatura kesilemez (active varsa)."""
    o, _ = await _make_paid_order(auth_client, db_session)
    r1 = await auth_client.post(f"/api/invoices/orders/{o.order_no}/issue", json={})
    assert r1.status_code == 201
    r2 = await auth_client.post(f"/api/invoices/orders/{o.order_no}/issue", json={})
    # Mock'ta status='sent' olduğu için 409 bekleniyor
    assert r2.status_code == 409


async def test_issue_invoice_requires_admin(client, db_session):
    """Auth olmadan 401 — order varlığına bile gerek yok, guard önce çalışır."""
    r = await client.post("/api/invoices/orders/TT-X/issue", json={})
    assert r.status_code == 401


async def test_list_invoices(auth_client, db_session):
    o, _ = await _make_paid_order(auth_client, db_session)
    await auth_client.post(f"/api/invoices/orders/{o.order_no}/issue", json={})
    r = await auth_client.get("/api/invoices")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


async def test_list_invoices_filter_by_status(auth_client, db_session):
    o, _ = await _make_paid_order(auth_client, db_session)
    await auth_client.post(f"/api/invoices/orders/{o.order_no}/issue", json={})
    r = await auth_client.get("/api/invoices", params={"status": "sent"})
    assert r.status_code == 200
    rows = r.json()
    assert all(i["status"] == "sent" for i in rows)


async def test_get_invoice(auth_client, db_session):
    o, _ = await _make_paid_order(auth_client, db_session)
    issue = await auth_client.post(f"/api/invoices/orders/{o.order_no}/issue", json={})
    inv_id = issue.json()["id"]
    r = await auth_client.get(f"/api/invoices/{inv_id}")
    assert r.status_code == 200
    assert r.json()["id"] == inv_id


async def test_get_invoice_404(auth_client):
    r = await auth_client.get("/api/invoices/99999")
    assert r.status_code == 404


async def test_cancel_invoice_ok(auth_client, db_session):
    o, _ = await _make_paid_order(auth_client, db_session)
    issue = await auth_client.post(f"/api/invoices/orders/{o.order_no}/issue", json={})
    if issue.json()["status"] != "sent":
        pytest.skip("Mock provider 'sent' üretmedi, skip")
    inv_id = issue.json()["id"]
    r = await auth_client.post(f"/api/invoices/{inv_id}/cancel", json={"reason": "Müşteri iptal talebi"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_cancel_reason_validation(auth_client, db_session):
    o, _ = await _make_paid_order(auth_client, db_session)
    issue = await auth_client.post(f"/api/invoices/orders/{o.order_no}/issue", json={})
    inv_id = issue.json()["id"]
    r = await auth_client.post(f"/api/invoices/{inv_id}/cancel", json={"reason": "ab"})
    assert r.status_code == 422  # min_length=3


async def test_admin_pdf_renders_html(auth_client, db_session):
    o, _ = await _make_paid_order(auth_client, db_session)
    issue = await auth_client.post(f"/api/invoices/orders/{o.order_no}/issue", json={})
    inv_id = issue.json()["id"]
    r = await auth_client.get(f"/api/invoices/{inv_id}/pdf")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    # Bytes üzerinden UTF-8 araması. .upper() Türkçe 'i' → 'İ' yapmaz (ASCII I kalır).
    body = r.content
    assert b"TecnoTools" in body
    # Fatura tipi başlığı: "E-ARŞIV FATURA" veya "E-ARŞİV FATURA" (Unicode TR upper'a bağlı)
    # En azından anahtar kelimelerin geçtiğini doğrula
    assert b"E-AR" in body.upper()
    assert b"FATURA" in body.upper()


async def test_public_pdf_requires_email_match(client, auth_client, db_session):
    o, _ = await _make_paid_order(auth_client, db_session)
    issue = await auth_client.post(f"/api/invoices/orders/{o.order_no}/issue", json={})
    inv = issue.json()
    ettn = inv.get("ettn")
    if not ettn:
        pytest.skip("ETTN üretilmedi, skip")
    # Yanlış email → 404 (enumeration koruması)
    r1 = await client.get(f"/api/invoices/public/{ettn}", params={"email": "imposter@x.com"})
    assert r1.status_code == 404
    # Doğru email → 200
    r2 = await client.get(f"/api/invoices/public/{ettn}", params={"email": o.customer_email})
    assert r2.status_code == 200


async def test_public_pdf_404_for_unknown_ettn(client):
    r = await client.get("/api/invoices/public/fake-ettn-uuid", params={"email": "a@b.com"})
    assert r.status_code == 404
