"""GİB (Gelir İdaresi Başkanlığı) mükellef doğrulama.

Yalnız format/algoritma doğrulaması yapılır — TCKN (11 hane) ve VKN (10 hane)
için resmi MERNIS/GİB checksum'ları. Network çağrısı gerektirmez, anında
pozitif/negatif sonuç verir.

NOT: GİB'in canlı "mükellef e-arşiv/e-fatura kapsamında mı" sorgusu için
resmi, ücretsiz, otomatize edilebilir bir açık API YOKTUR:
  - `sorgu.efatura.gov.tr` altında bir uç nokta tahmin edip kullanmayı
    denemiştik; o adres artık (veya hiç) mevcut değil (404 dönüyor).
  - GİB'in gerçek kamuya açık "e-Fatura Kayıtlı Kullanıcılar" sorgu sayfası
    (sorgu.efatura.gov.tr/kullanicilar/) CAPTCHA korumalıdır — sunucu
    tarafından otomatik sorgulanamaz.
  - Gerçek zamanlı mükellef sorgusu istenirse, yalnızca ücretli bir özel
    entegratörün (Nilvera, Foriba, Mikro vb.) API'si üzerinden yapılabilir;
    o zaman `_query_gib_taxpayer` bu entegratörün endpoint'ine bağlanacak
    şekilde yeniden yazılmalı.

Bu yüzden VKN için de (TCKN'de olduğu gibi) yalnızca format sonucu dönülür;
UI'da yanıltıcı "GİB sorgulanamadı" gibi mesajlar yerine net bir "geçerli"
onayı gösterilir.

Frontend kullanım: checkout VKN/TCKN alanında debounce'lu (450ms) çağrı.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Yalnız rakam — UI formdan boşluk/tire vb. gelmesi muhtemel
_DIGITS_RE = re.compile(r"\D+")


@dataclass(slots=True)
class TaxLookupResult:
    """Tek tip yanıt — frontend hem TCKN hem VKN için aynı şemayı kullanır."""

    kind: str  # "tckn" | "vkn" | "invalid"
    value: str
    valid_format: bool
    # Aşağıdaki üçü şu an her zaman None/"format" — canlı GİB mükellef sorgusu
    # yok (bkz. modül docstring'i). Alanlar, ileride bir entegratör API'si
    # bağlanırsa response şeması değişmesin diye korunuyor.
    is_taxpayer: bool | None = None
    title: str | None = None  # ünvan (VKN için)
    tax_office: str | None = None  # vergi dairesi (VKN için)
    source: str | None = None  # "format"
    error: str | None = None


def _strip(value: str) -> str:
    return _DIGITS_RE.sub("", value or "")


def validate_tckn(tckn: str) -> bool:
    """T.C. Kimlik No (11 hane) MERNIS algoritması.

    Kural:
      - 11 hane, ilki 0 olmayacak.
      - d10 = ((d1+d3+d5+d7+d9)*7 - (d2+d4+d6+d8)) mod 10
      - d11 = (d1+d2+...+d10) mod 10
    """
    s = _strip(tckn)
    if len(s) != 11 or s[0] == "0":
        return False
    try:
        d = [int(c) for c in s]
    except ValueError:
        return False
    odd_sum = d[0] + d[2] + d[4] + d[6] + d[8]
    even_sum = d[1] + d[3] + d[5] + d[7]
    if ((odd_sum * 7) - even_sum) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def validate_vkn(vkn: str) -> bool:
    """Vergi Kimlik No (10 hane) — GİB resmi algoritma.

    Adımlar (özet):
      v = (d_i + (10 - i)) % 10
      t = v * 2^(10-i) ; eğer v != 0 ve v == 9 ise t = 9
      son hane = (10 - (sum(t) mod 10)) mod 10
    """
    s = _strip(vkn)
    if len(s) != 10:
        return False
    try:
        d = [int(c) for c in s]
    except ValueError:
        return False
    total = 0
    for i in range(9):
        tmp = (d[i] + (9 - i)) % 10
        if tmp == 0:
            total += 0
        else:
            t = (tmp * pow(2, 9 - i)) % 9
            # pow mod 9: tmp != 0 ve t == 0 ise gerçek değer 9
            total += 9 if t == 0 else t
    check = (10 - (total % 10)) % 10
    return check == d[9]


def classify(value: str) -> str:
    s = _strip(value)
    if len(s) == 11:
        return "tckn" if validate_tckn(s) else "invalid"
    if len(s) == 10:
        return "vkn" if validate_vkn(s) else "invalid"
    return "invalid"


async def lookup(value: str, query_gib: bool = True) -> TaxLookupResult:
    """Public API — endpoint buradan çağırır.

    Args:
      value: kullanıcının girdiği VKN veya TCKN.
      query_gib: geriye dönük uyumluluk için tutulur; canlı GİB sorgusu
        olmadığından şu an davranışı değiştirmez (bkz. modül docstring'i).
    """
    s = _strip(value)
    kind = classify(s)
    if kind == "invalid":
        return TaxLookupResult(kind="invalid", value=s, valid_format=False, source="format")
    if kind == "tckn":
        # TCKN doğrulaması için MERNIS sorgusu kullanmıyoruz (ad-soyad-doğum yılı
        # zorunlu kılıyor + e-arşiv için zaten TCKN'nin doğru olması yeterli).
        return TaxLookupResult(kind="tckn", value=s, valid_format=True, source="format")
    # VKN — canlı GİB mükellef sorgusu yok (bkz. modül docstring'i); yalnızca
    # (doğrulanmış) format sonucu dönülür.
    return TaxLookupResult(kind="vkn", value=s, valid_format=True, source="format")
