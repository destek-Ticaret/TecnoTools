"""Sahte tedarikçi adapter — gerçek API onayı gelene kadar geliştirme/test için.

Verilen URL/ID'den deterministik örnek bir ürün üretir; gerçek ağ çağrısı yapmaz.
AliExpress ve 1688 kaynaklarını destekler (kaynak URL'den tespit edilir).
"""

from __future__ import annotations

from app.services.suppliers.base import SupplierAdapter, SupplierProduct
from app.services.suppliers.util import detect_supplier, extract_id

# Geriye dönük uyumluluk: bazı modüller _extract_id'yi buradan import ediyordu.
_extract_id = extract_id

# Kaynağa göre para birimi: AliExpress USD, 1688 yuan (CNY)
_CURRENCY = {"aliexpress": "USD", "1688": "CNY"}
_ITEM_URL = {
    "aliexpress": "https://www.aliexpress.com/item/{pid}.html",
    "1688": "https://detail.1688.com/offer/{pid}.html",
}


class MockSupplierAdapter(SupplierAdapter):
    display_name = "Tedarikçi (mock)"

    def __init__(self, source: str = "aliexpress") -> None:
        self.code = source

    async def fetch_product(self, url_or_id: str) -> SupplierProduct:
        source = detect_supplier(url_or_id) if str(url_or_id).startswith("http") else self.code
        pid = extract_id(url_or_id)
        currency = _CURRENCY.get(source, "USD")
        # ID'den deterministik sahte fiyat
        base = 3 + (int(pid[-3:]) % 50) if pid.isdigit() else 9.99
        price = base * (7 if currency == "CNY" else 1)  # CNY fiyatlar daha yüksek görünür
        url = url_or_id if str(url_or_id).startswith("http") else _ITEM_URL[source].format(pid=pid)
        return SupplierProduct(
            supplier=source,
            supplier_product_id=pid,
            supplier_url=url,
            title=f"Örnek {source} Ürünü #{pid}",
            description=(
                "Bu, mock tedarikçi adapter'ından gelen örnek bir üründür. "
                "Gerçek API onayı gelince gerçek ürün verisiyle değişecek."
            ),
            supplier_price=round(float(price), 2),
            currency=currency,
            images=[
                f"https://picsum.photos/seed/{pid}a/600",
                f"https://picsum.photos/seed/{pid}b/600",
            ],
            features=["Hızlı kargo", "Tedarikçi garantili", "Mock özellik"],
            stock=100,
        )
