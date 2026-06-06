"""Sürat Kargo adapter — webhook + mock. Gerçek API creds ile eklenecek."""

from __future__ import annotations

from app.config import get_settings
from app.services.carriers.common import GenericCarrierAdapter


class SuratAdapter(GenericCarrierAdapter):
    code = "surat"
    display_name = "Sürat Kargo"
    signature_header = "x-surat-signature"
    status_map = {}  # noqa: RUF012 — doküman gelince doldurulur (bkz. common._TEXT_HINTS)

    def _secret(self) -> str:
        return get_settings().surat_webhook_secret
