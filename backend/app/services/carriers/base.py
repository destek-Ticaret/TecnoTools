"""Kargo adapter ortak arayüzü ve normalize event modeli."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

EVENT_CODES = (
    "created",  # gönderi oluşturuldu / barkod basıldı
    "picked_up",  # şubeden teslim alındı
    "in_transit",  # transferde
    "out_for_delivery",  # dağıtıma çıktı
    "delivered",  # teslim edildi
    "failed_attempt",  # teslim denendi, başarısız
    "returned",  # iade
    "cancelled",  # iptal
)


@dataclass(frozen=True)
class NormalizedEvent:
    """Tüm firmalardan ortak şekle indirilmiş tek hareket."""

    carrier: str
    tracking_no: str
    code: str
    occurred_at: datetime
    raw_status: str | None = None
    description: str | None = None
    location: str | None = None
    raw_payload: dict[str, Any] | None = field(default=None)

    def __post_init__(self):
        if self.code not in EVENT_CODES:
            raise ValueError(f"Invalid event code: {self.code!r}")


class CarrierAdapter(abc.ABC):
    code: str = ""  # "aras" | "yurtici" | ...
    display_name: str = ""

    @abc.abstractmethod
    async def fetch(self, tracking_no: str) -> list[NormalizedEvent]:
        """Firma API'sinden ilgili gönderinin event listesini çek."""

    @abc.abstractmethod
    def parse_webhook(self, headers: dict[str, str], body: bytes) -> list[NormalizedEvent]:
        """Webhook gövdesini parse edip normalize event listesi üret."""

    def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        """Default: secret yoksa kabul, varsa HMAC-SHA256 doğrula. Override edilebilir."""
        return True

    def is_configured(self) -> bool:
        """Gerçek API çağrısı için credential'lar mevcut mu?"""
        return False
