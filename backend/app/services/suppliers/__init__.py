"""Dropshipping tedarikçi adapterleri.

Kaynak (aliexpress | 1688) URL'den tespit edilir. settings.supplier_mode ile
mock/gerçek seçilir: "mock" → sahte veri (geliştirme/test); "live" → gerçek API
(her kaynağın kendi adapter'ı). Geriye uyum: "aliexpress" değeri de "live" sayılır.
"""

from app.config import get_settings
from app.services.suppliers.base import SupplierAdapter, SupplierProduct
from app.services.suppliers.util import detect_supplier


def get_supplier(source: str = "aliexpress") -> SupplierAdapter:
    """Verilen kaynak için uygun adapter'ı döndürür."""
    settings = get_settings()
    live = settings.supplier_mode in ("live", "aliexpress", "1688")
    if live:
        if source == "1688":
            from app.services.suppliers.alibaba1688 import Alibaba1688Adapter

            return Alibaba1688Adapter()
        from app.services.suppliers.aliexpress import AliExpressAdapter

        return AliExpressAdapter()

    from app.services.suppliers.mock import MockSupplierAdapter

    return MockSupplierAdapter(source)


def get_supplier_for_url(url_or_id: str) -> SupplierAdapter:
    """URL'den kaynağı tespit edip uygun adapter'ı döndürür."""
    return get_supplier(detect_supplier(url_or_id))


__all__ = ["SupplierAdapter", "SupplierProduct", "get_supplier", "get_supplier_for_url"]
