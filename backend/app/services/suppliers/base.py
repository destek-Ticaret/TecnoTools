"""Tedarikçi adapter ortak arayüzü ve normalize ürün modeli."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SupplierProduct:
    """Tedarikçiden çekilen, mağaza ürün şemasına eşlenebilir normalize ürün."""

    supplier: str  # "aliexpress" | "manual"
    supplier_product_id: str
    supplier_url: str
    title: str
    description: str | None
    supplier_price: float  # tedarikçideki alış fiyatı (mağaza para biriminde)
    currency: str
    images: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    stock: int = 0


class SupplierAdapter(abc.ABC):
    code: str = ""  # "aliexpress" | "mock"
    display_name: str = ""

    @abc.abstractmethod
    async def fetch_product(self, url_or_id: str) -> SupplierProduct:
        """Ürün linki veya tedarikçi ürün id'sinden ürün bilgisini çek."""

    def is_configured(self) -> bool:
        """Gerçek API çağrısı için credential'lar mevcut mu?"""
        return False
