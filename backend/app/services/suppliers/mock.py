"""Sahte tedarikçi adapter — AliExpress API onayı gelene kadar geliştirme/test için.

Verilen URL/ID'den deterministik örnek bir ürün üretir; gerçek ağ çağrısı yapmaz.
"""

from __future__ import annotations

import re

from app.services.suppliers.base import SupplierAdapter, SupplierProduct


def _extract_id(url_or_id: str) -> str:
    # AliExpress linki: .../item/1005006789012345.html
    m = re.search(r"(\d{8,})", url_or_id or "")
    return m.group(1) if m else (url_or_id or "0").strip()


class MockSupplierAdapter(SupplierAdapter):
    code = "aliexpress"  # mock'ta da aynı kaynak kodu kullanılır (kayıt tutarlılığı)
    display_name = "AliExpress (mock)"

    async def fetch_product(self, url_or_id: str) -> SupplierProduct:
        pid = _extract_id(url_or_id)
        # ID'den deterministik sahte fiyat (3.00–53.00 USD aralığı)
        price_usd = 3 + (int(pid[-3:]) % 50) if pid.isdigit() else 9.99
        url = (
            url_or_id
            if str(url_or_id).startswith("http")
            else f"https://www.aliexpress.com/item/{pid}.html"
        )
        return SupplierProduct(
            supplier="aliexpress",
            supplier_product_id=pid,
            supplier_url=url,
            title=f"Örnek Tedarikçi Ürünü #{pid}",
            description=(
                "Bu, mock tedarikçi adapter'ından gelen örnek bir üründür. "
                "AliExpress API onayı gelince gerçek ürün verisiyle değişecek."
            ),
            supplier_price=round(float(price_usd), 2),
            currency="USD",
            images=[
                f"https://picsum.photos/seed/{pid}a/600",
                f"https://picsum.photos/seed/{pid}b/600",
            ],
            features=["Hızlı kargo", "Tedarikçi garantili", "Mock özellik"],
            stock=100,
        )
