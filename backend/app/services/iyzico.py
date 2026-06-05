"""iyzico entegrasyonu — Phase 1: taksit (installment) bilgisi.

Akış:
- Kart numarasının ilk 6-8 hanesi (BIN) + sepet tutarı verilir.
- iyzico kimliği TANIMLIYSA gerçek "Installment Info" API'sinden banka taksit
  planları çekilir (iyzipay SDK ile; kurulu değilse mock'a düşülür).
- Kimlik YOKSA gerçekçi MOCK taksit planları döner (geliştirme/test için).

Not: Gerçek ödeme (Checkout Form) Phase 2'de bu modüle eklenecek.
"""

import logging

from app.config import get_settings

try:  # SDK opsiyonel — yoksa mock modu çalışır
    import iyzipay  # type: ignore
except ImportError:  # pragma: no cover
    iyzipay = None

logger = logging.getLogger(__name__)
settings = get_settings()

# Mock taksit komisyon oranları (gerçekçi TR aralığı). Gerçek API bunları
# bankaya göre döndürür; mock'ta sabit kademe kullanıyoruz.
_MOCK_RATES = {1: 0.0, 2: 0.0, 3: 0.015, 6: 0.04, 9: 0.07, 12: 0.10}


def _has_credentials() -> bool:
    return bool(settings.iyzico_api_key and settings.iyzico_secret_key)


def _mock_installments(bin_number: str, price: float) -> dict:
    options = []
    for count, rate in _MOCK_RATES.items():
        total = round(price * (1 + rate), 2)
        per = round(total / count, 2)
        options.append(
            {
                "count": count,
                "total_price": total,
                "installment_price": per,
                "commission_rate": rate,
                "label": "Tek Çekim" if count == 1 else f"{count} Taksit",
            }
        )
    return {
        "bin": bin_number,
        "price": round(price, 2),
        "currency": "TRY",
        "bank_name": None,
        "card_family": None,
        "card_association": None,
        "force_3ds": False,
        "mock": True,
        "options": options,
    }


def _real_installments(bin_number: str, price: float) -> dict:
    """iyzico Installment Info API (iyzipay SDK). Hata → caller mock'a düşer."""
    import json

    opts = {
        "api_key": settings.iyzico_api_key,
        "secret_key": settings.iyzico_secret_key,
        "base_url": settings.iyzico_base_url,
    }
    req = {"locale": "tr", "price": f"{price:.2f}", "binNumber": bin_number}
    raw = iyzipay.InstallmentInfo().retrieve(req, opts)  # type: ignore[union-attr]
    data = json.loads(raw.read().decode("utf-8"))
    if data.get("status") != "success" or not data.get("installmentDetails"):
        raise RuntimeError(f"iyzico installment hatası: {data.get('errorMessage', 'bilinmeyen')}")
    detail = data["installmentDetails"][0]
    options = []
    for p in detail.get("installmentPrices", []):
        count = int(p["installmentNumber"])
        total = round(float(p["totalPrice"]), 2)
        per = round(float(p["installmentPrice"]), 2)
        options.append(
            {
                "count": count,
                "total_price": total,
                "installment_price": per,
                "commission_rate": round((total / price) - 1, 4) if price else 0.0,
                "label": "Tek Çekim" if count == 1 else f"{count} Taksit",
            }
        )
    options.sort(key=lambda o: o["count"])
    return {
        "bin": bin_number,
        "price": round(price, 2),
        "currency": "TRY",
        "bank_name": detail.get("bankName"),
        "card_family": detail.get("cardFamilyName"),
        "card_association": detail.get("cardAssociation"),
        "force_3ds": bool(detail.get("force3ds")),
        "mock": False,
        "options": options,
    }


def get_installment_options(bin_number: str, price: float) -> dict:
    """BIN + tutara göre taksit seçeneklerini döndür. Kimlik+SDK varsa gerçek
    API; yoksa veya hata olursa gerçekçi mock."""
    price = round(float(price), 2)
    if _has_credentials() and iyzipay is not None:
        try:
            return _real_installments(bin_number, price)
        except Exception:
            logger.exception("iyzico installment API başarısız, mock'a düşülüyor")
    return _mock_installments(bin_number, price)
