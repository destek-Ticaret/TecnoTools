"""TOTP doğrulama + anti-replay testleri."""

import time

from app.services.totp import _b32_decode, _hotp, verify_totp

SECRET = "JBSWY3DPEHPK3PXP"  # örnek base32 secret


def _code_for(counter: int) -> str:
    return _hotp(_b32_decode(SECRET), counter)


def test_verify_totp_accepts_current_code():
    now = int(time.time() // 30)
    assert verify_totp(_code_for(now), SECRET) == now


def test_verify_totp_rejects_wrong_code():
    now = int(time.time() // 30)
    valid = _code_for(now)
    wrong = "1" * 6 if valid != "1" * 6 else "2" * 6
    assert verify_totp(wrong, SECRET) is None


def test_verify_totp_replay_rejected_with_min_step():
    """Aynı kod, min_step olarak kendi step'i verilince tekrar kabul edilmemeli
    — bir kodun görüldükten sonra tekrar kullanılmasını (replay) engeller."""
    now = int(time.time() // 30)
    code = _code_for(now)
    matched = verify_totp(code, SECRET, min_step=None)
    assert matched == now
    # Aynı kodu, daha önce kabul edilen step'i min_step olarak vererek tekrar dene
    replay = verify_totp(code, SECRET, min_step=matched)
    assert replay is None


def test_verify_totp_newer_code_accepted_after_replay_guard():
    now = int(time.time() // 30)
    old_code = _code_for(now)
    verify_totp(old_code, SECRET)  # ilk kullanım
    # Bir sonraki periyodun kodu, önceki step min_step olarak verilse bile kabul edilmeli
    next_code = _code_for(now + 1)
    assert verify_totp(next_code, SECRET, min_step=now) == now + 1
