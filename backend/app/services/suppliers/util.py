"""Tedarikçi yardımcıları — URL'den kaynak tespiti ve ürün ID çıkarma."""

from __future__ import annotations

import re

SUPPLIERS = ("aliexpress", "1688")


def detect_supplier(url_or_id: str) -> str:
    """Linkin domaininden tedarikçiyi tespit eder. Belirsizse aliexpress varsayılır."""
    s = (url_or_id or "").lower()
    if "1688.com" in s:
        return "1688"
    return "aliexpress"


def extract_id(url_or_id: str) -> str:
    """Üründen sayısal ID çıkarır.

    AliExpress: .../item/1005006789012345.html
    1688:       https://detail.1688.com/offer/123456789.html
    """
    m = re.search(r"(\d{8,})", url_or_id or "")
    return m.group(1) if m else (url_or_id or "0").strip()
