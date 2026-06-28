"""Gerçek AliExpress tedarikçi adapter (AliExpress Open Platform / Dropshipping API).

NOT: AliExpress API anahtarları (app_key/app_secret) henüz onaylanmadı. Bu sınıf,
anahtarlar gelince doldurulacak iskelettir. Onay sürecinden sonra:
  1) .env içine ALIEXPRESS_APP_KEY / ALIEXPRESS_APP_SECRET / ALIEXPRESS_TRACKING_ID yaz
  2) SUPPLIER_MODE=aliexpress yap
  3) Aşağıdaki imza (sign) ve yanıt-parse mantığını resmi API dökümanıyla doğrula

API: System Tool 'aliexpress.ds.product.get' (Dropshipping product detail).
Doküman: https://openservice.aliexpress.com
"""

from __future__ import annotations

import hashlib
import time

import httpx

from app.config import get_settings
from app.services.suppliers.base import SupplierAdapter, SupplierProduct
from app.services.suppliers.mock import _extract_id

settings = get_settings()

API_GATEWAY = "https://api-sg.aliexpress.com/sync"


def _sign(params: dict[str, str], secret: str) -> str:
    """AliExpress TOP imza (HMAC-SHA256, büyük harf hex). Dökümandan doğrula."""
    ordered = "".join(f"{k}{params[k]}" for k in sorted(params))
    import hmac

    return hmac.new(secret.encode(), ordered.encode(), hashlib.sha256).hexdigest().upper()


class AliExpressAdapter(SupplierAdapter):
    code = "aliexpress"
    display_name = "AliExpress"

    def is_configured(self) -> bool:
        return bool(settings.aliexpress_app_key and settings.aliexpress_app_secret)

    async def fetch_product(self, url_or_id: str) -> SupplierProduct:
        if not self.is_configured():
            raise RuntimeError(
                "AliExpress API anahtarları yok. .env'de ALIEXPRESS_APP_KEY/SECRET "
                "doldur ve SUPPLIER_MODE=aliexpress yap; ya da SUPPLIER_MODE=mock kullan."
            )
        pid = _extract_id(url_or_id)
        params = {
            "method": "aliexpress.ds.product.get",
            "app_key": settings.aliexpress_app_key,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
            "product_id": pid,
            "ship_to_country": "TR",
            "target_currency": "USD",
            "target_language": "tr",
        }
        params["sign"] = _sign(params, settings.aliexpress_app_secret)

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(API_GATEWAY, params=params)
            r.raise_for_status()
            data = r.json()

        # TODO: gerçek yanıt yapısına göre parse et. Aşağısı beklenen şemaya göre
        # yazılmış taslaktır; ilk gerçek çağrıda alan adlarını doğrula/güncelle.
        result = data.get("aliexpress_ds_product_get_response", {}).get("result", {})
        base = result.get("ae_item_base_info_dto", {})
        price_info = result.get("ae_item_sku_info_dtos", {})
        media = result.get("ae_multimedia_info_dto", {})

        images = [u for u in (media.get("image_urls", "") or "").split(";") if u.strip()]
        try:
            sku0 = price_info.get("ae_item_sku_info_d_t_o", [{}])[0]
            price = float(sku0.get("offer_sale_price") or sku0.get("sku_price") or 0)
        except (IndexError, TypeError, ValueError):
            price = 0.0

        return SupplierProduct(
            supplier="aliexpress",
            supplier_product_id=pid,
            supplier_url=url_or_id
            if str(url_or_id).startswith("http")
            else f"https://www.aliexpress.com/item/{pid}.html",
            title=base.get("subject", f"AliExpress {pid}"),
            description=base.get("detail"),
            supplier_price=round(price, 2),
            currency="USD",
            images=images,
            features=[],
            stock=int(base.get("sku_available_stock") or 0),
        )
