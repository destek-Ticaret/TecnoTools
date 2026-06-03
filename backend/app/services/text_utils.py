"""Metin yardımcı fonksiyonları — Türkçe-bilinçli normalize, slug, fuzzy.

Bütün arama/öneri/SEO modülleri buradaki primitifleri kullanır.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# Türkçe → ASCII karakter eşlemesi (lower-case).
_TR_MAP = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ü": "u",
        "Ü": "u",
        "ö": "o",
        "Ö": "o",
        "ç": "c",
        "Ç": "c",
        "â": "a",
        "Â": "a",
        "î": "i",
        "Î": "i",
        "û": "u",
        "Û": "u",
    }
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MULTI_DASH = re.compile(r"-+")


def normalize(text: str) -> str:
    """Türkçe-bilinçli arama anahtarı: küçük harf + diakritiksiz + alfanumerik.

    "Şarj İstasyonu" → "sarj istasyonu"
    Aramada hem `q` hem de `name` bu fonksiyondan geçirilerek kıyaslanır.
    """
    if not text:
        return ""
    s = text.translate(_TR_MAP)
    # NFKD ayrıştırma ile geride kalan diakritikleri sök
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def slugify(text: str, max_length: int = 80) -> str:
    """SEO-friendly slug. "Şarj İstasyonu 3.0!" → "sarj-istasyonu-3-0"."""
    s = normalize(text)
    s = _NON_ALNUM.sub("-", s)
    s = _MULTI_DASH.sub("-", s).strip("-")
    if len(s) > max_length:
        # Boşluk kaybetmemek için son tireden kırp
        s = s[:max_length].rsplit("-", 1)[0] or s[:max_length]
    return s


def tokenize(text: str) -> list[str]:
    """Normalize edilmiş tokenler. 2+ karakter, alfanumerik."""
    if not text:
        return []
    return [t for t in re.findall(r"[a-z0-9]+", normalize(text)) if len(t) >= 2]


def trigrams(text: str) -> set[str]:
    """3-gram kümesi (Jaccard benzerliği için). Kelime sınırını korur."""
    s = f"  {normalize(text)}  "
    return {s[i : i + 3] for i in range(len(s) - 2)}


def jaccard(a: str, b: str) -> float:
    """0..1 arası trigram Jaccard benzerliği."""
    ta, tb = trigrams(a), trigrams(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def levenshtein(a: str, b: str, max_distance: int | None = None) -> int:
    """Düz dinamik programlama Levenshtein. max_distance ile erken çıkış.

    Tipik kelime kıyaslama için yeterli. Çok uzun metinde trigram tercih et.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    cur = [0] * (len(a) + 1)
    for j, cb in enumerate(b, 1):
        cur[0] = j
        row_min = cur[0]
        for i, ca in enumerate(a, 1):
            cost = 0 if ca == cb else 1
            cur[i] = min(
                cur[i - 1] + 1,  # insertion
                prev[i] + 1,  # deletion
                prev[i - 1] + cost,  # substitution
            )
            row_min = min(row_min, cur[i])
        if max_distance is not None and row_min > max_distance:
            return max_distance + 1
        prev, cur = cur, prev
    return prev[len(a)]


def fuzzy_score(query: str, candidate: str) -> float:
    """0..1 arası birleşik fuzzy skor.

    Strateji (büyükten küçüğe):
    1.0     — normalize eşit
    0.9..   — prefix / "kelime başlangıcı" eşleşme (uzunluk oranıyla)
    0.7..   — substring (içerme) — pozisyon penaltisi
    0..     — trigram Jaccard (geri kalan tüm durumlar)
    """
    nq = normalize(query)
    nc = normalize(candidate)
    if not nq or not nc:
        return 0.0
    if nq == nc:
        return 1.0
    if nc.startswith(nq):
        # "iphone" vs "iphone 15 pro" → daha kısa eşleşme oranı kadar bonus
        return 0.90 + 0.10 * (len(nq) / max(len(nc), 1))
    # kelime başına başlama bonusu
    for word in nc.split():
        if word.startswith(nq):
            return 0.85
    if nq in nc:
        pos = nc.find(nq)
        # Erken pozisyon daha değerli
        return 0.70 + 0.10 * (1 - pos / max(len(nc), 1))
    return jaccard(nq, nc)


def highlight_terms(text: str, query: str, mark: str = "mark") -> str:
    """Eşleşen kelimeleri <mark>...</mark> ile sar (UI vurgulaması için)."""
    nq = normalize(query)
    if not nq or not text:
        return text or ""
    parts = []
    nt = normalize(text)
    # Karakter pozisyonu eşlemesi: normalize uzunluğunu koruyabilir mi?
    # Güvenli olmak için orijinal metinde basit case-insensitive arama.
    for token in {t for t in tokenize(query) if len(t) >= 2}:
        # Türkçe-friendly: orijinal metinde de eşleşmesini istiyoruz, regex'i case-insensitive
        pattern = re.compile(re.escape(token), re.IGNORECASE)
        text = pattern.sub(lambda m: f"<{mark}>{m.group(0)}</{mark}>", text)
    return text


@lru_cache(maxsize=2048)
def cached_normalize(text: str) -> str:
    """`normalize` ile aynı, sık çağrılan arama anahtarları için cache'li."""
    return normalize(text)
