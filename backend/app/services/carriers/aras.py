"""Aras Kargo adapter — webhook + mock. Gerçek API (SOAP) creds ile eklenecek."""

from __future__ import annotations

from app.config import get_settings
from app.services.carriers.common import GenericCarrierAdapter


class ArasAdapter(GenericCarrierAdapter):
    code = "aras"
    display_name = "Aras Kargo"
    signature_header = "x-aras-signature"
    # Firmaya özel statü kodları doküman geldiğinde doldurulur; o zamana kadar
    # webhook'taki Türkçe metin sınıflandırıcı (common._TEXT_HINTS) kullanılır.
    status_map = {}  # noqa: RUF012

    def _secret(self) -> str:
        return get_settings().aras_webhook_secret
