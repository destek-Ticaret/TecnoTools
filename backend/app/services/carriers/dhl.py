"""DHL Lojistik kargo adapter'ı.

Entegrasyon: DHL Unified Shipment Tracking API v2
  GET https://api-eu.dhl.com/track/shipments?trackingNumber={no}
  Header: DHL-API-Key: {key}

Ortam değişkenleri (Railway Variables):
  DHL_API_KEY   — DHL Developer Portal'dan alınan API key
  DHL_WEBHOOK_SECRET — (opsiyonel) webhook imzası için HMAC secret

API key yoksa mock event döner (geliştirme / test).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.services.carriers.base import NormalizedEvent
from app.services.carriers.common import (
    GenericCarrierAdapter,
    classify,
    parse_dt,
)

log = logging.getLogger(__name__)

# DHL Unified Tracking API statü → internal event kodu
_STATUS_MAP: dict[str, str] = {
    # Üst-düzey status alanı
    "pre-transit": "created",
    "transit": "in_transit",
    "delivered": "delivered",
    "failure": "failed_attempt",
    "unknown": "in_transit",
    # Detay eventCode alanı
    "SHIPMENT_PICKED_UP": "picked_up",
    "SHIPMENT_PICKUP_COMPLETED": "picked_up",
    "TRANSIT": "in_transit",
    "IN_TRANSIT": "in_transit",
    "CUSTOMS_CLEARANCE": "in_transit",
    "OUT_FOR_DELIVERY": "out_for_delivery",
    "DELIVERED": "delivered",
    "DELIVERY_FAILURE": "failed_attempt",
    "DELIVERY_ATTEMPTED": "failed_attempt",
    "RETURNED_TO_SENDER": "returned",
    "SHIPMENT_CANCELLED": "cancelled",
    "CREATED": "created",
    "REGISTERED": "created",
}

_API_URL = "https://api-eu.dhl.com/track/shipments"


class DhlAdapter(GenericCarrierAdapter):
    code = "dhl"
    display_name = "DHL Lojistik"
    signature_header = "x-dhl-signature"
    status_map = _STATUS_MAP

    def _secret(self) -> str:
        return get_settings().dhl_webhook_secret or ""

    def is_configured(self) -> bool:
        return bool(get_settings().dhl_api_key)

    async def _fetch_real(self, tracking_no: str) -> list[NormalizedEvent]:
        """DHL Unified Tracking API'den event listesi çek."""
        api_key = get_settings().dhl_api_key
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _API_URL,
                params={"trackingNumber": tracking_no},
                headers={"DHL-API-Key": api_key},
            )
        if resp.status_code == 404:
            log.info("DHL: takip no bulunamadı — %s", tracking_no)
            return []
        resp.raise_for_status()
        data = resp.json()
        shipments: list[dict[str, Any]] = data.get("shipments", [])
        if not shipments:
            return []
        events: list[NormalizedEvent] = []
        for shipment in shipments:
            for ev in shipment.get("events", []):
                events.append(self._dhl_event_to_normalized(tracking_no, ev))
        # Tarihe göre sırala (eskiden yeniye)
        events.sort(key=lambda e: e.occurred_at)
        return events

    def _dhl_event_to_normalized(
        self, tracking_no: str, ev: dict[str, Any]
    ) -> NormalizedEvent:
        event_code = ev.get("eventCode", "")
        status_field = ev.get("status", "")
        description = ev.get("description") or ev.get("remark") or status_field
        location_obj = ev.get("location", {})
        address = location_obj.get("address", {}) if location_obj else {}
        location = (
            address.get("addressLocality")
            or address.get("cityName")
            or address.get("countryCode")
        )
        occurred_at = parse_dt(ev.get("timestamp"))
        # Önce eventCode, sonra üst-düzey status, son olarak metin sınıflandırıcı
        internal = (
            _STATUS_MAP.get(event_code)
            or _STATUS_MAP.get(status_field)
            or classify(event_code, description, _STATUS_MAP)
        )
        return NormalizedEvent(
            carrier=self.code,
            tracking_no=tracking_no,
            code=internal,
            occurred_at=occurred_at,
            raw_status=event_code or status_field or None,
            description=description,
            location=str(location) if location else None,
            raw_payload=ev,
        )
