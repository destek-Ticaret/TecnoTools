"""Dropshipping tedarikçi adapterleri.

Aktif moda (settings.supplier_mode) göre uygun adapter'ı döndürür. Mock ile
geliştirme/test yapılır; AliExpress API anahtarları onaylanınca "aliexpress"
moduna geçilir.
"""

from app.config import get_settings
from app.services.suppliers.base import SupplierAdapter, SupplierProduct


def get_supplier() -> SupplierAdapter:
    settings = get_settings()
    if settings.supplier_mode == "aliexpress":
        from app.services.suppliers.aliexpress import AliExpressAdapter

        return AliExpressAdapter()
    from app.services.suppliers.mock import MockSupplierAdapter

    return MockSupplierAdapter()


__all__ = ["SupplierAdapter", "SupplierProduct", "get_supplier"]
