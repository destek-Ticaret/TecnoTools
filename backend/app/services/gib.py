"""GİB (Gelir İdaresi Başkanlığı) mükellef doğrulama.

İki kademe çalışır:

  1) Format/algoritma doğrulaması — TCKN (11 hane) ve VKN (10 hane) için
     resmi MERNIS/GIB checksum'ları. Network çağrısı gerektirmez, anında
     pozitif/negatif sonuç verir.

  2) Mükellef sorgu — VKN için GİB e-arşiv/e-fatura kapsamında olan
     mükellefleri listeler. Bağlantı yoksa (offline / GIB down) format
     doğrulamasıyla yetinilir; UI yine de devam edebilir.

Frontend kullanım: checkout VKN/TCKN alanında debounce'lu (450ms) çağrı.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass

import httpx

from app.services.cache import shared_cache

logger = logging.getLogger(__name__)

# Yalnız rakam — UI formdan boşluk/tire vb. gelmesi muhtemel
_DIGITS_RE = re.compile(r"\D+")


@dataclass(slots=True)
class TaxLookupResult:
    """Tek tip yanıt — frontend hem TCKN hem VKN için aynı şemayı kullanır."""

    kind: str  # "tckn" | "vkn" | "invalid"
    value: str
    valid_format: bool
    is_taxpayer: bool | None = None  # GİB sorgu sonucu; None = sorgulanmadı
    title: str | None = None  # ünvan (VKN için)
    tax_office: str | None = None  # vergi dairesi (VKN için)
    source: str | None = None  # "format" | "gib" | "cache"
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
    if sum(d[:10]) % 10 != d[10]:
        return False
    return True


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


# ── GİB internet vergi dairesi sorgu (best-effort) ─────────────────────────
_GIB_QUERY_URL = "https://sorgu.efatura.gov.tr/earsiv-services/esarsiv"
_GIB_TIMEOUT = 4.0
_GIB_TTL = 60 * 60 * 24  # 24 saat — mükellef listesi gün içi değişmez


async def _query_gib_taxpayer(vkn: str) -> TaxLookupResult:
    """GİB'in açık sorgu uç noktasından e-arşiv mükellef ünvanını çekmeye çalış.

    Resmi açık API yok; GİB sitesinin sorgu formu HTML döner. Burada
    `is_taxpayer`'ı sadece HTTP 2xx + içerikte VKN'nin geçmesi olarak
    yorumluyoruz; ünvan çıkarmaya çalışıyoruz.

    Production'da Foriba/Nilvera gibi entegratörünüzün "mükellef sorgu" API'sini
    kullanmak daha sağlıklı — `provider` ayarına göre buraya başka fonksiyon
    bağlanabilir.
    """
    cache_key = ("gib_taxpayer", vkn)
    cached = shared_cache.get(cache_key)
    if cached is not None:
        cached_result = TaxLookupResult(**cached)
        cached_result.source = "cache"
        return cached_result

    out = TaxLookupResult(kind="vkn", value=vkn, valid_format=True, source="gib")
    try:
        async with httpx.AsyncClient(timeout=_GIB_TIMEOUT, follow_redirects=True) as client:
            resp = await client.post(
                _GIB_QUERY_URL,
                data={"vkn": vkn},
                headers={"User-Agent": "Mozilla/5.0 (compatible; TecnoTools/1.0)"},
            )
        if 200 <= resp.status_code < 300:
            body = (resp.text or "")[:8000]
            if vkn in body:
                out.is_taxpayer = True
                # Çok kaba ünvan çıkarımı — production'da entegratör API'si tavsiye edilir
                m = re.search(
                    r"<td[^>]*>\s*([^<]{4,120}A\.Ş\.|[^<]{4,120}LTD\.\s*ŞTİ\.)\s*</td>",
                    body,
                    re.IGNORECASE,
                )
                if m:
                    out.title = m.group(1).strip()
            else:
                out.is_taxpayer = False
        else:
            out.error = f"gib_http_{resp.status_code}"
    except (TimeoutError, httpx.HTTPError) as e:
        out.error = f"gib_unreachable: {type(e).__name__}"
        logger.info("GİB sorgusu başarısız (%s): %s", vkn, e)

    # Hatayı da kısa süre cache'le ki ardışık aynı çağrılarda GİB'i bombalamayalım
    shared_cache.set(cache_key, asdict(out), ttl=_GIB_TTL if out.error is None else 60)
    return out


async def lookup(value: str, query_gib: bool = True) -> TaxLookupResult:
    """Public API — endpoint buradan çağırır.

    Args:
      value: kullanıcının girdiği VKN veya TCKN.
      query_gib: VKN için GİB sorgusu yapılsın mı? False ise sadece format.
    """
    s = _strip(value)
    kind = classify(s)
    if kind == "invalid":
        return TaxLookupResult(kind="invalid", value=s, valid_format=False, source="format")
    if kind == "tckn":
        # TCKN doğrulaması için MERNIS sorgusu kullanmıyoruz (ad-soyad-doğum yılı
        # zorunlu kılıyor + e-arşiv için zaten TCKN'nin doğru olması yeterli).
        return TaxLookupResult(kind="tckn", value=s, valid_format=True, source="format")
    # VKN
    if not query_gib:
        return TaxLookupResult(kind="vkn", value=s, valid_format=True, source="format")
    return await _query_gib_taxpayer(s)
