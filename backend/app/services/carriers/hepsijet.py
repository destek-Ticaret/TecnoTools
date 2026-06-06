"""Hepsijet adapter — webhook + mock. Gerçek API (REST) creds ile eklenecek."""

from __future__ import annotations

from app.config import get_settings
from app.services.carriers.common import GenericCarrierAdapter


class HepsijetAdapter(GenericCarrierAdapter):
    code = "hepsijet"
    display_name = "Hepsijet"
    signature_header = "x-hepsijet-signature"
    status_map = {}  # noqa: RUF012 — doküman gelince doldurulur (bkz. common._TEXT_HINTS)

    def _secret(self) -> str:
        return get_settings().hepsijet_webhook_secret
