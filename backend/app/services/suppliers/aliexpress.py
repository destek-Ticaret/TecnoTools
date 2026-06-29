"""Gerçek AliExpress tedarikçi adapter (AliExpress Open Platform / Dropshipping API).

Kullanım (anahtarlar geldikten sonra):
  1) Railway env: ALIEXPRESS_APP_KEY / ALIEXPRESS_APP_SECRET / ALIEXPRESS_TRACKING_ID
  2) SUPPLIER_MODE=live
  3) İlk gerçek çağrıda yanıt alan adlarını doğrula (debug endpoint: /api/dropshipping/debug)

API: 'aliexpress.ds.product.get' (Dropshipping product detail).
Gateway: https://api-sg.aliexpress.com/sync · imza: HMAC-SHA256, büyük harf hex.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import httpx

from app.config import get_settings
from app.services.suppliers.base import SupplierAdapter, SupplierProduct
from app.services.suppliers.util import extract_id

settings = get_settings()

API_GATEWAY = "https://api-sg.aliexpress.com/sync"


def _sign(params: dict[str, str], secret: str) -> str:
    """TOP/IOP imza: parametreleri ada göre sırala, key+value birleştir, HMAC-SHA256."""
    ordered = "".join(f"{k}{params[k]}" for k in sorted(params))
    return hmac.new(secret.encode(), ordered.encode(), hashlib.sha256).hexdigest().upper()


class AliExpressAdapter(SupplierAdapter):
    code = "aliexpress"
    display_name = "AliExpress"

    def __init__(self) -> None:
        # Router/sync, DB'den geçerli OAuth token'ı buraya enjekte eder.
        # Yoksa env'deki manuel token'a düşer.
        self.access_token = settings.aliexpress_access_token or ""

    def is_configured(self) -> bool:
        return bool(settings.aliexpress_app_key and settings.aliexpress_app_secret)

    async def fetch_raw(self, url_or_id: str) -> dict:
        """Ham API yanıtını döndürür (alan adlarını doğrulamak / debug için)."""
        if not self.is_configured():
            raise RuntimeError(
                "AliExpress API anahtarları yok. Railway'de ALIEXPRESS_APP_KEY/SECRET "
                "doldur ve SUPPLIER_MODE=live yap; ya da SUPPLIER_MODE=mock kullan."
            )
        pid = extract_id(url_or_id)
        params = {
            "method": "aliexpress.ds.product.get",
            "app_key": settings.aliexpress_app_key,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
            "product_id": pid,
            "ship_to_country": "TR",
            "target_currency": "USD",
            "target_language": "EN",
        }
        # OAuth2 access_token (varsa) — ds.product.get yetki için gerekir
        if self.access_token:
            params["access_token"] = self.access_token
        params["sign"] = _sign(params, settings.aliexpress_app_secret)
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(API_GATEWAY, params=params)
            r.raise_for_status()
            return r.json()

    async def fetch_product(self, url_or_id: str) -> SupplierProduct:
        pid = extract_id(url_or_id)
        data = await self.fetch_raw(url_or_id)

        # API hata yanıtını yakala (yetki/imza/limit vb.)
        if "error_response" in data:
            err = data["error_response"]
            raise RuntimeError(
                f"AliExpress API hatası: {err.get('msg') or err.get('sub_msg') or err}"
            )

        # Yanıt sarmalı: aliexpress_ds_product_get_response > result
        resp = data.get("aliexpress_ds_product_get_response") or data.get("result") or data
        result = resp.get("result", resp) if isinstance(resp, dict) else {}
        base = result.get("ae_item_base_info_dto", {}) or {}
        sku_wrap = result.get("ae_item_sku_info_dtos", {}) or {}
        media = result.get("ae_multimedia_info_dto", {}) or {}

        images = [u for u in (media.get("image_urls", "") or "").split(";") if u.strip()]
        price = 0.0
        try:
            skus = sku_wrap.get("ae_item_sku_info_d_t_o") or []
            if skus:
                s0 = skus[0]
                price = float(s0.get("offer_sale_price") or s0.get("sku_price") or 0)
        except (IndexError, TypeError, ValueError):
            price = 0.0

        return SupplierProduct(
            supplier="aliexpress",
            supplier_product_id=pid,
            supplier_url=url_or_id
            if str(url_or_id).startswith("http")
            else f"https://www.aliexpress.com/item/{pid}.html",
            title=base.get("subject") or f"AliExpress {pid}",
            description=base.get("detail"),
            supplier_price=round(price, 2),
            currency="USD",
            images=images,
            features=[],
            stock=int(base.get("sku_available_stock") or base.get("product_count") or 0),
        )
