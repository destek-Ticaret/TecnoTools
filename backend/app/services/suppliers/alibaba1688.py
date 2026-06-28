"""Gerçek 1688.com tedarikçi adapter (Alibaba Açık Platform / 1688 API).

NOT: 1688 API erişimi henüz alınmadı. 1688 doğrudan açık bir dropshipping API'si
sunmaz; erişim genelde Alibaba Open Platform (open.1688.com) onayı veya yetkili bir
aracı (CD/sourcing agent) API'si üzerinden olur. Bu sınıf, erişim sağlanınca
doldurulacak iskelettir.

Erişim gelince:
  1) .env içine ONESIXEIGHTEIGHT_APP_KEY / _APP_SECRET yaz
  2) SUPPLIER_MODE=live yap (mock dışına çık)
  3) Aşağıdaki imza ve yanıt-parse mantığını resmi dökümana göre doğrula
"""

from __future__ import annotations

from app.config import get_settings
from app.services.suppliers.base import SupplierAdapter, SupplierProduct
from app.services.suppliers.util import extract_id

settings = get_settings()


class Alibaba1688Adapter(SupplierAdapter):
    code = "1688"
    display_name = "1688.com"

    def is_configured(self) -> bool:
        return bool(settings.onesixeighteight_app_key and settings.onesixeighteight_app_secret)

    async def fetch_product(self, url_or_id: str) -> SupplierProduct:
        if not self.is_configured():
            raise RuntimeError(
                "1688 API erişimi yok. .env'de ONESIXEIGHTEIGHT_APP_KEY/SECRET doldur "
                "veya SUPPLIER_MODE=mock kullan."
            )
        # TODO: open.1688.com ürün detay API çağrısı + imza + yanıt parse.
        pid = extract_id(url_or_id)
        raise NotImplementedError(
            f"1688 canlı API entegrasyonu henüz tamamlanmadı (ürün {pid})."
        )
