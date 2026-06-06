"""Aras Kargo adapter — webhook + gerçek SOAP fetch.

Gerçek API: arascargoservice.asmx · GetCargoTransaction(userName, password,
code, integrationCode) — operasyon/parametre adları canlı WSDL'den doğrulandı.
Kimlik (ARAS_USERNAME/PASSWORD) yoksa fetch() mock döner.

UYARI: İstek (request) WSDL'e göre doğru kurulur; ancak BAŞARI yanıtının alan
adları gerçek bir hesap + gerçek gönderi ile teyit edilmeli (kimlik olmadan
servis auth hatası döndürür). Yanıt, soap_records_to_events ile toleranslı
parse edilir.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.services.carriers.base import NormalizedEvent
from app.services.carriers.common import GenericCarrierAdapter

log = logging.getLogger(__name__)


class ArasAdapter(GenericCarrierAdapter):
    code = "aras"
    display_name = "Aras Kargo"
    signature_header = "x-aras-signature"
    status_map = {}  # noqa: RUF012 — doküman gelince; şimdilik TR metin sınıflandırıcı

    def _secret(self) -> str:
        return get_settings().aras_webhook_secret

    def is_configured(self) -> bool:
        s = get_settings()
        return bool(s.aras_username and s.aras_password)

    async def _fetch_real(self, tracking_no: str) -> list[NormalizedEvent]:
        s = get_settings()
        ns = "http://tempuri.org/"
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
            f' xmlns:tem="{ns}">'
            "<soap:Body><tem:GetCargoTransaction>"
            f"<tem:userName>{s.aras_username}</tem:userName>"
            f"<tem:password>{s.aras_password}</tem:password>"
            f"<tem:code>{tracking_no}</tem:code>"
            f"<tem:integrationCode>{s.aras_integration_code}</tem:integrationCode>"
            "</tem:GetCargoTransaction></soap:Body></soap:Envelope>"
        )
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                s.aras_tracking_url,
                content=envelope.encode("utf-8"),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": f"{ns}GetCargoTransaction",
                },
            )
            resp.raise_for_status()
        return self.soap_records_to_events(resp.text, tracking_no)
