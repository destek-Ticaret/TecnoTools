"""PayTR HMAC token üretimi + callback hash doğrulama."""

from app.services.paytr import build_paytr_token, verify_callback_hash


def test_dev_mock_token_when_creds_missing():
    """Credentials boşken DEV- prefix'li sahte token döner."""
    result = build_paytr_token(
        order_no="TT-2026-0001",
        email="x@x.com",
        amount_kurus=10000,
        user_name="Test User",
        user_phone="+905551234567",
        user_address="X",
        basket=[("Product A", 100.0, 1)],
    )
    assert result["token"].startswith("DEV-")


def test_callback_hash_rejects_invalid():
    """Geçersiz hash false döner — credentials boşken bile."""
    ok = verify_callback_hash(
        merchant_oid="TT20260001",
        status="success",
        total_amount="10000",
        hash_value="garbage",
    )
    assert ok is False


def test_callback_hash_with_credentials(monkeypatch):
    """Credential set edilince hash doğru hesaplanmalı."""
    import base64
    import hashlib
    import hmac

    from app.services import paytr as paytr_mod

    monkeypatch.setattr(paytr_mod.settings, "paytr_merchant_key", "TESTKEY")
    monkeypatch.setattr(paytr_mod.settings, "paytr_merchant_salt", "TESTSALT")

    msg = "TT20260001TESTSALTsuccess10000"
    expected = base64.b64encode(
        hmac.new(b"TESTKEY", msg.encode(), hashlib.sha256).digest()
    ).decode()
    assert (
        verify_callback_hash(
            merchant_oid="TT20260001",
            status="success",
            total_amount="10000",
            hash_value=expected,
        )
        is True
    )
