"""Dinamik kargo hesabı — il → bölge → ücret tablosu.

Mevcut sistemde `shipping_fee_default` ve `shipping_free_threshold` tek sayı.
Bu modül daha gerçekçi bir alternatif sunar:
  - Türkiye coğrafi bölgeleri için ayrı taban ücret.
  - Tutar bazlı kademe: yüksek sepet → daha yüksek bedava-eşik garanti.
  - Hacim/ağırlık opsiyonel (her ürün için ortalama ağırlık varsayılır).
  - "Bedavadan ne kadar uzakta?" yardımcısı (frontend upsell mesajı için).

İl listesi `text_utils.normalize` ile karşılaştırılır.
"""
from __future__ import annotations

from app.services.text_utils import normalize

# Coğrafi bölge → standart kargo ücreti (₺) ve teslim süresi (gün)
ZONE_RATES: dict[str, dict] = {
    "MARMARA":            {"fee": 39.90, "days_min": 1, "days_max": 2},
    "EGE":                {"fee": 49.90, "days_min": 1, "days_max": 3},
    "AKDENIZ":            {"fee": 54.90, "days_min": 2, "days_max": 3},
    "IC_ANADOLU":         {"fee": 49.90, "days_min": 2, "days_max": 3},
    "KARADENIZ":          {"fee": 59.90, "days_min": 2, "days_max": 4},
    "DOGU_ANADOLU":       {"fee": 79.90, "days_min": 3, "days_max": 5},
    "GUNEYDOGU_ANADOLU":  {"fee": 69.90, "days_min": 3, "days_max": 5},
}

# İl → bölge. (Türkiye'nin 81 ili)
CITY_TO_ZONE: dict[str, str] = {
    # Marmara
    "istanbul": "MARMARA", "kocaeli": "MARMARA", "sakarya": "MARMARA",
    "yalova": "MARMARA", "bursa": "MARMARA", "balikesir": "MARMARA",
    "canakkale": "MARMARA", "edirne": "MARMARA", "kirklareli": "MARMARA",
    "tekirdag": "MARMARA", "bilecik": "MARMARA",
    # Ege
    "izmir": "EGE", "manisa": "EGE", "aydin": "EGE", "denizli": "EGE",
    "mugla": "EGE", "usak": "EGE", "kutahya": "EGE", "afyonkarahisar": "EGE",
    # Akdeniz
    "antalya": "AKDENIZ", "adana": "AKDENIZ", "mersin": "AKDENIZ",
    "isparta": "AKDENIZ", "burdur": "AKDENIZ", "hatay": "AKDENIZ",
    "osmaniye": "AKDENIZ", "kahramanmaras": "AKDENIZ",
    # İç Anadolu
    "ankara": "IC_ANADOLU", "konya": "IC_ANADOLU", "kayseri": "IC_ANADOLU",
    "eskisehir": "IC_ANADOLU", "sivas": "IC_ANADOLU", "yozgat": "IC_ANADOLU",
    "kirikkale": "IC_ANADOLU", "kirsehir": "IC_ANADOLU", "aksaray": "IC_ANADOLU",
    "nevsehir": "IC_ANADOLU", "nigde": "IC_ANADOLU", "karaman": "IC_ANADOLU",
    "cankiri": "IC_ANADOLU",
    # Karadeniz
    "samsun": "KARADENIZ", "trabzon": "KARADENIZ", "ordu": "KARADENIZ",
    "rize": "KARADENIZ", "giresun": "KARADENIZ", "artvin": "KARADENIZ",
    "bartin": "KARADENIZ", "bayburt": "KARADENIZ", "bolu": "KARADENIZ",
    "duzce": "KARADENIZ", "gumushane": "KARADENIZ", "karabuk": "KARADENIZ",
    "kastamonu": "KARADENIZ", "sinop": "KARADENIZ", "tokat": "KARADENIZ",
    "amasya": "KARADENIZ", "corum": "KARADENIZ", "zonguldak": "KARADENIZ",
    # Doğu Anadolu
    "erzurum": "DOGU_ANADOLU", "agri": "DOGU_ANADOLU", "ardahan": "DOGU_ANADOLU",
    "bingol": "DOGU_ANADOLU", "bitlis": "DOGU_ANADOLU", "elazig": "DOGU_ANADOLU",
    "erzincan": "DOGU_ANADOLU", "hakkari": "DOGU_ANADOLU", "igdir": "DOGU_ANADOLU",
    "kars": "DOGU_ANADOLU", "malatya": "DOGU_ANADOLU", "mus": "DOGU_ANADOLU",
    "tunceli": "DOGU_ANADOLU", "van": "DOGU_ANADOLU",
    # Güneydoğu Anadolu
    "gaziantep": "GUNEYDOGU_ANADOLU", "diyarbakir": "GUNEYDOGU_ANADOLU",
    "sanliurfa": "GUNEYDOGU_ANADOLU", "mardin": "GUNEYDOGU_ANADOLU",
    "siirt": "GUNEYDOGU_ANADOLU", "sirnak": "GUNEYDOGU_ANADOLU",
    "kilis": "GUNEYDOGU_ANADOLU", "batman": "GUNEYDOGU_ANADOLU",
    "adiyaman": "GUNEYDOGU_ANADOLU",
}


def zone_for_city(city: str | None) -> str:
    """İl adından bölge kodu. Bilinmeyen şehir → IC_ANADOLU (orta fiyat varsayım)."""
    if not city:
        return "IC_ANADOLU"
    key = normalize(city).replace(" ", "")
    return CITY_TO_ZONE.get(key, "IC_ANADOLU")


def calc_shipping(
    *,
    city: str | None,
    subtotal: float,
    free_threshold: float = 750.0,
    default_fee_override: float | None = None,
    item_count: int = 1,
    heavy_item_fee: float = 25.0,
    heavy_item_count: int = 0,
) -> dict:
    """Kargo ücretini ve tahmini teslim aralığını hesapla.

    - `free_threshold` üstü → ücretsiz.
    - `heavy_item_count` adet ağır ürün için ek bedel.
    - `default_fee_override` verilirse bölge ücreti yerine kullanılır.
    """
    zone = zone_for_city(city)
    zone_info = ZONE_RATES[zone]
    base = default_fee_override if default_fee_override is not None else zone_info["fee"]
    extra = heavy_item_fee * max(0, heavy_item_count)

    if subtotal >= free_threshold and subtotal > 0:
        fee = 0.0
        free = True
    else:
        fee = base + extra
        free = False

    return {
        "fee": round(fee, 2),
        "base_fee": round(base, 2),
        "extra_fee": round(extra, 2),
        "is_free": free,
        "free_threshold": free_threshold,
        "remaining_for_free": round(max(0.0, free_threshold - subtotal), 2),
        "zone": zone,
        "estimated_days_min": zone_info["days_min"],
        "estimated_days_max": zone_info["days_max"],
    }


def free_shipping_message(remaining: float) -> str | None:
    """Frontend için "X₺ daha ekle kargo bedava" mesajı (None → zaten bedava)."""
    if remaining <= 0:
        return None
    return f"🚚 {remaining:.2f}₺ daha ürün ekleyin, kargo ücretsiz!"
