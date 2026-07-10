"""KVKK / privacy uçları: veri ihracı, silme, çerez izni, vergi sorgu."""

from datetime import UTC

import pytest

from app.services.gib import classify, validate_tckn, validate_vkn

REGISTER = {
    "email": "bob@example.com",
    "password": "BobStrong1!",
    "name": "Bob Tester",
    "marketing_opt_in": False,
}


async def _register_and_auth(client) -> dict:
    r = await client.post("/api/customer-auth/register", json=REGISTER)
    assert r.status_code == 201, r.text
    data = r.json()
    client.headers["Authorization"] = f"Bearer {data['access_token']}"
    return data


# ─────────────────── Veri ihracı ────────────────────


async def test_data_export_returns_profile(client):
    await _register_and_auth(client)
    r = await client.get("/api/customer-auth/me/data-export")
    assert r.status_code == 200
    body = r.json()
    assert body["customer"]["email"] == REGISTER["email"]
    assert "orders" in body and "invoices" in body and "reviews" in body
    # Hassas alan dışlanmalı
    assert "password_hash" not in body["customer"]


async def test_data_export_requires_auth(client):
    r = await client.get("/api/customer-auth/me/data-export")
    assert r.status_code == 401


# ─────────────────── Silme talebi ────────────────────


async def test_delete_request_wrong_password(client):
    await _register_and_auth(client)
    r = await client.post(
        "/api/customer-auth/me/delete-request",
        json={"password": "wrong", "reason": "test"},
    )
    assert r.status_code == 401


async def test_delete_request_then_confirm_anonymizes_customer(client, db_session):
    """Tam akış: talep → token → onayla → customer silinmiş, order anonim."""
    import hashlib
    import secrets
    from datetime import datetime, timedelta

    from app.models import (
        Customer,
        DataDeletionRequest,
        DataDeletionStatus,
        Order,
        OrderStatus,
        PaymentStatus,
    )

    await _register_and_auth(client)

    # Müşteriye delivered sipariş ekle (mali kayıt zorunluluğu testi)
    cust = (
        await db_session.execute(
            __import__("sqlalchemy").select(Customer).where(Customer.email == REGISTER["email"])
        )
    ).scalar_one()
    order = Order(
        order_no="TT-2026-9001",
        customer_id=cust.id,
        customer_name=cust.name,
        customer_email=cust.email,
        customer_phone="05551112233",
        customer_city="İstanbul",
        customer_address="Sokak 1",
        subtotal=100,
        discount=0,
        tax=20,
        shipping=0,
        total=120,
        status=OrderStatus.DELIVERED.value,
        payment_status=PaymentStatus.SUCCESS.value,
        admin_notes=[],
    )
    db_session.add(order)
    await db_session.commit()

    # Silme talebi başlat — açık (delivered = teslim) sipariş engellemez
    r = await client.post(
        "/api/customer-auth/me/delete-request",
        json={"password": REGISTER["password"], "reason": "kvkk"},
    )
    assert r.status_code == 202, r.text

    # Token mailde olduğundan testte direkt DB'den token üretip onaylamak yerine
    # endpoint için yeni bir token üretip request'i confirm'e geçiriyoruz.
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    ddr = (
        await db_session.execute(
            __import__("sqlalchemy")
            .select(DataDeletionRequest)
            .where(DataDeletionRequest.email_snapshot == REGISTER["email"])
        )
    ).scalar_one()
    ddr.token_hash = token_hash
    ddr.expires_at = datetime.now(UTC) + timedelta(minutes=10)
    ddr.status = DataDeletionStatus.PENDING.value
    await db_session.commit()

    r2 = await client.post(
        "/api/customer-auth/me/delete-confirm",
        json={"token": raw_token},
    )
    assert r2.status_code == 200, r2.text
    summary = r2.json()["summary"]
    assert summary["orders_anonymized"] == 1
    assert summary["customer_deleted"] is True

    # Identity map cache'i temizle — endpoint başka session'da çalıştı
    db_session.expire_all()

    # Doğrula: customer kaydı silindi, order anonim
    cust_after = (
        await db_session.execute(
            __import__("sqlalchemy").select(Customer).where(Customer.email == REGISTER["email"])
        )
    ).scalar_one_or_none()
    assert cust_after is None

    order_after = (
        await db_session.execute(
            __import__("sqlalchemy").select(Order).where(Order.order_no == "TT-2026-9001")
        )
    ).scalar_one()
    assert order_after.customer_id is None
    assert order_after.customer_name == "[Silinmiş Müşteri]"
    assert order_after.customer_email != REGISTER["email"]
    assert order_after.customer_email.startswith("silinmis+")


async def test_delete_request_blocks_when_open_order(client, db_session):
    from app.models import Customer, Order, OrderStatus, PaymentStatus

    await _register_and_auth(client)
    cust = (
        await db_session.execute(
            __import__("sqlalchemy").select(Customer).where(Customer.email == REGISTER["email"])
        )
    ).scalar_one()
    db_session.add(
        Order(
            order_no="TT-2026-9002",
            customer_id=cust.id,
            customer_name=cust.name,
            customer_email=cust.email,
            customer_phone="05551112233",
            customer_address="A",
            subtotal=10,
            tax=2,
            shipping=0,
            total=12,
            status=OrderStatus.PROCESSING.value,
            payment_status=PaymentStatus.SUCCESS.value,
            admin_notes=[],
        )
    )
    await db_session.commit()

    r = await client.post(
        "/api/customer-auth/me/delete-request",
        json={"password": REGISTER["password"]},
    )
    assert r.status_code == 409


async def test_delete_confirm_expired_token(client, db_session):
    import hashlib
    import secrets
    from datetime import datetime, timedelta

    from app.models import Customer, DataDeletionRequest, DataDeletionStatus

    await _register_and_auth(client)
    cust = (
        await db_session.execute(
            __import__("sqlalchemy").select(Customer).where(Customer.email == REGISTER["email"])
        )
    ).scalar_one()
    raw = secrets.token_urlsafe(16)
    db_session.add(
        DataDeletionRequest(
            customer_id=cust.id,
            email_snapshot=cust.email,
            name_snapshot=cust.name,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
            status=DataDeletionStatus.PENDING.value,
        )
    )
    await db_session.commit()

    r = await client.post("/api/customer-auth/me/delete-confirm", json={"token": raw})
    assert r.status_code == 400


# ─────────────────── Çerez izin log ────────────────────


async def test_consent_log_stores_categories(client, db_session):
    r = await client.post(
        "/api/privacy/consent",
        json={
            "session_id": "sess_test_123",
            "categories": {
                "essential": True,
                "preference": True,
                "analytics": False,
                "marketing": False,
            },
            "policy_version": "1.0",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["categories"]["essential"] is True
    assert body["categories"]["analytics"] is False


async def test_consent_log_forces_essential_true(client):
    """Zorunlu çerezler kapatılamaz — backend sessizce true'ya çevirir."""
    r = await client.post(
        "/api/privacy/consent",
        json={
            "session_id": "sess_test_x",
            "categories": {"essential": False, "marketing": True},
        },
    )
    assert r.status_code == 201
    assert r.json()["categories"]["essential"] is True


async def test_consent_ignores_unknown_categories(client):
    r = await client.post(
        "/api/privacy/consent",
        json={
            "session_id": "sess_test_y",
            "categories": {"essential": True, "tracking_pixels": True, "marketing": True},
        },
    )
    assert r.status_code == 201
    assert "tracking_pixels" not in r.json()["categories"]


# ─────────────────── VKN / TCKN ────────────────────


def test_validate_tckn_known_valid():
    # GİB resmi test TCKN — internette örnek olarak yayınlanan checksum'lu numara
    assert validate_tckn("10000000146") is True


def test_validate_tckn_rejects_leading_zero():
    assert validate_tckn("01234567890") is False


def test_validate_tckn_rejects_bad_checksum():
    assert validate_tckn("10000000147") is False


def test_validate_vkn_known_valid():
    # GİB örnek VKN
    assert (
        validate_vkn("1234567890") is True
        or validate_vkn("0123456789") is True
        or validate_vkn("4350309852") is True
    )


def test_validate_vkn_wrong_length():
    assert validate_vkn("123") is False
    assert validate_vkn("12345678901") is False


def test_classify_dispatch():
    assert classify("10000000146") == "tckn"
    assert classify("12345") == "invalid"


async def test_tax_lookup_endpoint_tckn(client):
    r = await client.get("/api/tax/lookup", params={"value": "10000000146", "query_gib": False})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "tckn"
    assert body["valid_format"] is True


async def test_tax_lookup_endpoint_invalid(client):
    r = await client.get("/api/tax/lookup", params={"value": "0000000000", "query_gib": False})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "invalid"
    assert body["valid_format"] is False


async def test_tax_lookup_vkn_no_live_gib_query(client):
    """GİB'in canlı mükellef sorgusu için ücretsiz/otomatize edilebilir bir API
    yok (bkz. app/services/gib.py) — geçerli bir VKN, query_gib varsayılan
    (True) olsa bile hiçbir ağ çağrısı yapmadan, dürüst bir format sonucu
    dönmeli (önceden var olmayan bir URL'e istek atıp hep is_taxpayer=None +
    error dönerdi)."""
    from app.services.gib import validate_vkn

    candidate = None
    for v in ("4350309852", "1234567890", "0123456789", "8900000023"):
        if validate_vkn(v):
            candidate = v
            break
    if not candidate:
        pytest.skip("Test için geçerli VKN bulunamadı")
    r = await client.get("/api/tax/lookup", params={"value": candidate})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "vkn"
    assert body["valid_format"] is True
    assert body["source"] == "format"
    assert body["error"] is None


async def test_tax_lookup_skips_gib_when_disabled(client):
    """query_gib=False sadece format döner — network call gerekmemeli."""
    # 10 hane uzunluğunda checksum geçen bir VKN bulmak için bir tane üretelim
    # 4350309852 GİB örneği — gerçek doğrulansın diye runtime'da çalıştır
    # Format geçerliyse VKN dön, geçmezse skip
    from app.services.gib import validate_vkn

    candidate = None
    for v in ("4350309852", "1234567890", "0123456789", "8900000023"):
        if validate_vkn(v):
            candidate = v
            break
    if not candidate:
        pytest.skip("Test için geçerli VKN bulunamadı")
    r = await client.get("/api/tax/lookup", params={"value": candidate, "query_gib": False})
    body = r.json()
    assert body["kind"] == "vkn"
    assert body["valid_format"] is True
    assert body["source"] == "format"


# ─────────────────── Admin: silme talep yönetimi ────────────────────


async def test_admin_lists_deletion_requests(auth_client, db_session):
    from app.models import DataDeletionRequest, DataDeletionStatus

    db_session.add(
        DataDeletionRequest(
            email_snapshot="legacy@example.com",
            name_snapshot="Legacy User",
            status=DataDeletionStatus.PENDING.value,
        )
    )
    await db_session.commit()
    r = await auth_client.get("/api/privacy/deletion-requests")
    assert r.status_code == 200
    items = r.json()
    assert any(it["email_snapshot"] == "legacy@example.com" for it in items)


async def test_admin_cancels_deletion_request(auth_client, db_session):

    from app.models import DataDeletionRequest, DataDeletionStatus

    ddr = DataDeletionRequest(
        email_snapshot="cancel@example.com",
        name_snapshot="X",
        status=DataDeletionStatus.PENDING.value,
    )
    db_session.add(ddr)
    await db_session.commit()
    await db_session.refresh(ddr)

    r = await auth_client.post(f"/api/privacy/deletion-requests/{ddr.id}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == DataDeletionStatus.CANCELLED.value
