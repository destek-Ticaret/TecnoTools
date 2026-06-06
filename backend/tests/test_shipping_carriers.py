"""PTT kargo adapter ve webhook akış testleri.

Kapsam:
  - PTT webhook parse (JSON + XML) + signature doğrulama
  - apply_event: idempotent insert, ileri-yön statü geçişi, Order alanlarının
    (carrier, shipped_at, delivered_at) doldurulması
  - POST /api/shipping/webhook/ptt end-to-end
  - POST /api/shipping/sync (admin) ve assign
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.config import get_settings
from app.models import Order, OrderStatus, PaymentStatus
from app.services.carriers import apply_event
from app.services.carriers.base import NormalizedEvent
from app.services.carriers.dispatch import _EVENT_TO_STATUS
from app.services.carriers.ptt import PttAdapter
from app.services.carriers.ptt import _classify as ptt_classify


async def _make_order(db_session, *, tracking_no="PTT123456", carrier=None, status="processing"):
    o = Order(
        order_no="TT-2026-0001",
        customer_name="Ahmet Yılmaz",
        customer_email="a@a.com",
        customer_phone="+905551112233",
        customer_city="İstanbul",
        customer_address="Yenidoğan Mah.",
        subtotal=Decimal("100"),
        discount=Decimal("0"),
        tax=Decimal("20"),
        shipping=Decimal("40"),
        total=Decimal("160"),
        status=status,
        payment_status=PaymentStatus.SUCCESS.value,
        tracking_no=tracking_no,
        carrier=carrier,
    )
    db_session.add(o)
    await db_session.commit()
    await db_session.refresh(o)
    return o


# ── Adapter unit tests ────────────────────────────────────────────────
def test_ptt_classify_known_codes():
    assert ptt_classify("50", None) == "delivered"
    assert ptt_classify("30", None) == "in_transit"
    assert ptt_classify("99", None) == "cancelled"


def test_ptt_classify_text_fallback():
    assert ptt_classify(None, "Teslim Edildi") == "delivered"
    assert ptt_classify(None, "Dağıtıma çıktı") == "out_for_delivery"
    assert ptt_classify("UNKNOWN", "rastgele bir şey") == "in_transit"


def test_ptt_parse_webhook_json():
    a = PttAdapter()
    payload = json.dumps(
        [
            {
                "barkod": "PTT123",
                "durumKodu": "50",
                "durum": "Teslim Edildi",
                "tarih": "2026-05-27T14:32:11",
                "birim": "İstanbul Merkez",
            },
            {
                "barkod": "PTT123",
                "durumKodu": "30",
                "durum": "Aktarmada",
                "tarih": "2026-05-26T09:00:00",
            },
        ]
    ).encode()
    events = a.parse_webhook({}, payload)
    assert len(events) == 2
    assert {e.code for e in events} == {"delivered", "in_transit"}
    assert events[0].tracking_no == "PTT123"
    assert events[0].location == "İstanbul Merkez"


def test_ptt_parse_webhook_xml():
    a = PttAdapter()
    xml = (
        b"<events><hareket><barkod>PTT999</barkod>"
        b"<durumKodu>30</durumKodu><durum>Aktarma</durum>"
        b"<tarih>2026-05-26 09:00:00</tarih></hareket></events>"
    )
    out = a.parse_webhook({}, xml)
    assert len(out) == 1 and out[0].code == "in_transit" and out[0].tracking_no == "PTT999"


def test_ptt_signature_verification():
    s = get_settings()
    s.ptt_webhook_secret = "topsecret"
    try:
        a = PttAdapter()
        body = b'[{"barkod":"X"}]'
        sig = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
        assert a.verify_signature({"x-ptt-signature": sig}, body) is True
        assert a.verify_signature({"x-ptt-signature": "deadbeef"}, body) is False
        assert a.verify_signature({}, body) is False
    finally:
        s.ptt_webhook_secret = ""


# ── apply_event: idempotent + ileri yön ───────────────────────────────
@pytest.mark.asyncio
async def test_apply_event_advances_status_and_is_idempotent(db_session):
    order = await _make_order(db_session)
    ev = NormalizedEvent(
        carrier="ptt",
        tracking_no=order.tracking_no,
        code="in_transit",
        occurred_at=datetime(2026, 5, 27, 10, 0, tzinfo=UTC),
        raw_status="Transferde",
        description="Aktarma",
        location="Ankara",
    )
    row, o, changed = await apply_event(db_session, ev)
    assert row is not None and changed is True
    assert o.status == OrderStatus.SHIPPED.value
    assert o.carrier == "ptt"
    assert o.shipped_at is not None

    row2, _, changed2 = await apply_event(db_session, ev)
    assert row2 is None
    assert changed2 is False


@pytest.mark.asyncio
async def test_apply_event_delivered_sets_delivered_at(db_session):
    order = await _make_order(db_session, status="shipped", carrier="ptt")
    ev = NormalizedEvent(
        carrier="ptt",
        tracking_no=order.tracking_no,
        code="delivered",
        occurred_at=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
    )
    _, o, changed = await apply_event(db_session, ev)
    assert changed is True
    assert o.status == OrderStatus.DELIVERED.value
    assert o.delivered_at is not None


@pytest.mark.asyncio
async def test_apply_event_does_not_regress_status(db_session):
    order = await _make_order(db_session, status="delivered", carrier="ptt")
    ev = NormalizedEvent(
        carrier="ptt",
        tracking_no=order.tracking_no,
        code="in_transit",
        occurred_at=datetime(2026, 5, 27, 10, 0, tzinfo=UTC),
    )
    _, o, changed = await apply_event(db_session, ev)
    assert changed is False
    assert o.status == OrderStatus.DELIVERED.value


def test_event_to_status_mapping_complete():
    for code in (
        "picked_up",
        "in_transit",
        "out_for_delivery",
        "delivered",
        "returned",
        "cancelled",
    ):
        assert code in _EVENT_TO_STATUS


# ── End-to-end webhook ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ptt_webhook_endpoint_applies_event(auth_client, db_session):
    await _make_order(db_session, tracking_no="PTTE2E1", carrier="ptt", status="processing")
    body = json.dumps(
        [
            {
                "barkod": "PTTE2E1",
                "durumKodu": "50",
                "durum": "Teslim Edildi",
                "tarih": "2026-05-28T11:00:00",
                "birim": "Çankaya Merkez",
            }
        ]
    ).encode()

    resp = await auth_client.post(
        "/api/shipping/webhook/ptt", content=body, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["accepted"] == 1
    assert data["applied"] == 1

    await db_session.commit()
    o = (
        await db_session.execute(
            __import__("sqlalchemy").select(Order).where(Order.order_no == "TT-2026-0001")
        )
    ).scalar_one()
    await db_session.refresh(o)
    assert o.status == OrderStatus.DELIVERED.value
    assert o.delivered_at is not None


@pytest.mark.asyncio
async def test_webhook_unknown_carrier_404(auth_client):
    resp = await auth_client.post("/api/shipping/webhook/fedex", content=b"{}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_webhook_invalid_signature_401(auth_client, db_session):
    s = get_settings()
    s.ptt_webhook_secret = "rotated-secret"
    try:
        resp = await auth_client.post(
            "/api/shipping/webhook/ptt",
            content=b'[{"barkod":"X"}]',
            headers={"x-ptt-signature": "wrong"},
        )
        assert resp.status_code == 401
    finally:
        s.ptt_webhook_secret = ""


# ── Sync + assign admin endpoints ─────────────────────────────────────
@pytest.mark.asyncio
async def test_assign_then_sync_uses_mock_events(auth_client, db_session):
    await _make_order(db_session, tracking_no="OLD", carrier=None, status="processing")
    r = await auth_client.post(
        "/api/shipping/assign/TT-2026-0001", json={"carrier": "ptt", "tracking_no": "PTTNEW1"}
    )
    assert r.status_code == 200
    assert r.json()["carrier"] == "ptt"

    r2 = await auth_client.post("/api/shipping/sync/TT-2026-0001")
    assert r2.status_code == 200
    body = r2.json()
    assert body["fetched"] == 3
    assert body["carrier"] == "ptt"

    r3 = await auth_client.get("/api/shipping/track/TT-2026-0001")
    assert r3.status_code == 200
    j = r3.json()
    assert j["carrier"] == "ptt"
    assert len(j["events"]) >= 3


@pytest.mark.asyncio
async def test_assign_rejects_unknown_carrier(auth_client, db_session):
    await _make_order(db_session, tracking_no="OLD2", carrier=None, status="processing")
    r = await auth_client.post(
        "/api/shipping/assign/TT-2026-0001", json={"carrier": "fedex", "tracking_no": "X1234"}
    )
    assert r.status_code == 422


# ── Çoklu firma (Aras/Yurtiçi/MNG/Sürat/Hepsijet) ─────────────────────
from app.services.carriers import CARRIER_CODES, get_adapter
from app.services.carriers.aras import ArasAdapter
from app.services.carriers.common import classify
from app.services.carriers.yurtici import YurticiAdapter


def test_all_carrier_codes_resolve_to_adapter():
    for code in CARRIER_CODES:
        adapter = get_adapter(code)
        assert adapter.code == code
        assert adapter.display_name


def test_real_api_is_configured_toggles_with_credentials():
    # Kimlik yokken adapter mock moddadır (is_configured False); set edilince True.
    s = get_settings()
    assert get_adapter("aras").is_configured() is False
    assert get_adapter("yurtici").is_configured() is False
    s.aras_username, s.aras_password = "u", "p"
    s.yurtici_username, s.yurtici_password = "u", "p"
    try:
        assert get_adapter("aras").is_configured() is True
        assert get_adapter("yurtici").is_configured() is True
    finally:
        s.aras_username = s.aras_password = ""
        s.yurtici_username = s.yurtici_password = ""


def test_generic_classify_text_fallback():
    # status_map boş → Türkçe metinden çıkarım
    assert classify(None, "Teslim Edildi", {}) == "delivered"
    assert classify(None, "Dağıtıma çıktı", {}) == "out_for_delivery"
    assert classify(None, "Aktarma merkezinde", {}) == "in_transit"
    assert classify(None, "Gönderi iade edildi", {}) == "returned"
    assert classify("X", "anlamsız", {}) == "in_transit"


def test_generic_parse_webhook_alias_fields():
    # Aras farklı alan adları (trackingNumber/status/eventDate/city) ile gelir
    a = ArasAdapter()
    payload = json.dumps(
        [
            {
                "trackingNumber": "ARAS123",
                "status": "Teslim Edildi",
                "eventDate": "2026-05-28 10:00:00",
                "city": "İzmir",
            }
        ]
    ).encode()
    events = a.parse_webhook({}, payload)
    assert len(events) == 1
    e = events[0]
    assert e.carrier == "aras"
    assert e.tracking_no == "ARAS123"
    assert e.code == "delivered"
    assert e.location == "İzmir"


def test_generic_parse_webhook_wrapped_and_xml():
    # {"events":[...]} sarmalı + XML <movements><movement>
    y = YurticiAdapter()
    wrapped = json.dumps({"events": [{"barkod": "YK9", "durum": "Dağıtımda"}]}).encode()
    out = y.parse_webhook({}, wrapped)
    assert len(out) == 1 and out[0].code == "out_for_delivery" and out[0].tracking_no == "YK9"

    xml = (
        b"<movements><movement><trackingNo>YK10</trackingNo>"
        b"<status>Aktarma merkezinde</status>"
        b"<date>2026-05-26 09:00:00</date></movement></movements>"
    )
    xout = y.parse_webhook({}, xml)
    assert len(xout) == 1 and xout[0].code == "in_transit" and xout[0].tracking_no == "YK10"


def test_generic_signature_verification():
    s = get_settings()
    s.aras_webhook_secret = "aras-secret"
    try:
        a = ArasAdapter()
        body = b'[{"trackingNumber":"X"}]'
        sig = hmac.new(b"aras-secret", body, hashlib.sha256).hexdigest()
        assert a.verify_signature({"x-aras-signature": sig}, body) is True
        assert a.verify_signature({"x-aras-signature": "deadbeef"}, body) is False
        assert a.verify_signature({}, body) is False
    finally:
        s.aras_webhook_secret = ""


@pytest.mark.asyncio
async def test_aras_webhook_endpoint_applies_event(auth_client, db_session):
    await _make_order(db_session, tracking_no="ARASE2E", carrier="aras", status="processing")
    body = json.dumps(
        [
            {
                "trackingNumber": "ARASE2E",
                "status": "Teslim Edildi",
                "eventDate": "2026-05-28 11:00:00",
            }
        ]
    ).encode()
    resp = await auth_client.post(
        "/api/shipping/webhook/aras", content=body, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["applied"] == 1

    await db_session.commit()
    import sqlalchemy

    o = (
        await db_session.execute(sqlalchemy.select(Order).where(Order.order_no == "TT-2026-0001"))
    ).scalar_one()
    await db_session.refresh(o)
    assert o.status == OrderStatus.DELIVERED.value


@pytest.mark.asyncio
async def test_assign_and_sync_new_carrier(auth_client, db_session):
    await _make_order(db_session, tracking_no="OLD3", carrier=None, status="processing")
    r = await auth_client.post(
        "/api/shipping/assign/TT-2026-0001", json={"carrier": "yurtici", "tracking_no": "YK123456"}
    )
    assert r.status_code == 200
    assert r.json()["carrier"] == "yurtici"

    r2 = await auth_client.post("/api/shipping/sync/TT-2026-0001")
    assert r2.status_code == 200
    assert r2.json()["fetched"] == 3  # mock
    assert r2.json()["carrier"] == "yurtici"
