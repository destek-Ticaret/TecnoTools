"""MNG Kargo adapter — webhook + mock. Gerçek API (REST) creds ile eklenecek."""

from __future__ import annotations

from app.config import get_settings
from app.services.carriers.common import GenericCarrierAdapter


class MngAdapter(GenericCarrierAdapter):
    code = "mng"
    display_name = "MNG Kargo"
    signature_header = "x-mng-signature"
    status_map = {}  # noqa: RUF012 — doküman gelince doldurulur (bkz. common._TEXT_HINTS)

    def _secret(self) -> str:
        return get_settings().mng_webhook_secret
