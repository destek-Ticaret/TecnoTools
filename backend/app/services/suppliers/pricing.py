"""Tedarikçi fiyatından satış fiyatı hesaplama (markup + para çevrimi + yuvarlama)."""

from __future__ import annotations

import math

from app.config import get_settings
from app.services import currency
from app.services.suppliers.base import SupplierProduct

settings = get_settings()


def _round_ending(value: float) -> float:
    """Fiyatı .90 gibi psikolojik sona yuvarla (settings.dropship_price_ending)."""
    ending = settings.dropship_price_ending
    if ending <= 0:
        return round(value, 2)
    base = math.floor(value)
    candidate = base + ending
    if candidate < value:
        candidate += 1
    return round(candidate, 2)


async def compute_sale_price(
    supplier_price: float, currency_code: str, markup: float | None = None
) -> tuple[float, float]:
    """Tedarikçi fiyatını mağaza para birimine çevirip markup uygular.

    Dönen: (sale_price, cost_in_base_currency) — ikisi de BASE_CURRENCY'de.
    """
    markup = markup if markup is not None else settings.dropship_markup
    rate = await currency.get_rate(currency_code, settings.base_currency)
    cost = currency.convert(supplier_price, rate)
    sale = _round_ending(cost * markup)
    return sale, cost


async def build_draft(sp: SupplierProduct, markup: float | None = None) -> dict:
    """SupplierProduct'ı mağaza ürün şemasına (Product alanları) eşleyen taslak üretir."""
    sale, cost = await compute_sale_price(sp.supplier_price, sp.currency, markup)
    return {
        "name": sp.title,
        "description": sp.description,
        "price": sale,
        "cost": cost,
        "supplier_price": cost,  # base currency'de alış
        "stock": sp.stock,
        "images": sp.images,
        "features": sp.features,
        "supplier": sp.supplier,
        "supplier_url": sp.supplier_url,
        "supplier_product_id": sp.supplier_product_id,
        "currency": settings.base_currency,
        "markup": markup if markup is not None else settings.dropship_markup,
    }
