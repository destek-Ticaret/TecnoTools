"""Yurtiçi Kargo adapter — webhook + gerçek SOAP fetch.

Gerçek API: KOPSWebServices/ShippingOrderDispatcherServices ·
queryShipmentDetail(wsUserName, wsPassword, wsLanguage, keys, keyType,
addHistoricalData, onlyTracking, jsonData) — canlı WSDL'den doğrulandı.
Kimlik (YURTICI_USERNAME/PASSWORD) yoksa fetch() mock döner.

UYARI: İstek WSDL'e göre doğru; başarı yanıtının alan adları gerçek hesapla
teyit edilmeli. addHistoricalData=true → hareket geçmişi. keyType=0 (gönderi
anahtarı/takip no) varsayıldı; hesap tipine göre 1 (irsaliye) olabilir.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.services.carriers.base import NormalizedEvent
from app.services.carriers.common import GenericCarrierAdapter

log = logging.getLogger(__name__)


class YurticiAdapter(GenericCarrierAdapter):
    code = "yurtici"
    display_name = "Yurtiçi Kargo"
    signature_header = "x-yurtici-signature"
    status_map = {}  # noqa: RUF012 — doküman gelince; şimdilik TR metin sınıflandırıcı

    def _secret(self) -> str:
        return get_settings().yurtici_webhook_secret

    def is_configured(self) -> bool:
        s = get_settings()
        return bool(s.yurtici_username and s.yurtici_password)

    async def _fetch_real(self, tracking_no: str) -> list[NormalizedEvent]:
        s = get_settings()
        ns = "http://yurticikargo.com.tr/ShippingOrderDispatcherServices"
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
            f' xmlns:ws="{ns}">'
            "<soap:Body><ws:queryShipmentDetail>"
            f"<wsUserName>{s.yurtici_username}</wsUserName>"
            f"<wsPassword>{s.yurtici_password}</wsPassword>"
            "<wsLanguage>TR</wsLanguage>"
            f"<keys>{tracking_no}</keys>"
            "<keyType>0</keyType>"
            "<addHistoricalData>true</addHistoricalData>"
            "<onlyTracking>false</onlyTracking>"
            "<jsonData>false</jsonData>"
            "</ws:queryShipmentDetail></soap:Body></soap:Envelope>"
        )
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                s.yurtici_tracking_url,
                content=envelope.encode("utf-8"),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": "",
                },
            )
            resp.raise_for_status()
        return self.soap_records_to_events(resp.text, tracking_no)
