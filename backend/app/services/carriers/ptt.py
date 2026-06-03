"""PTT Kargo entegrasyonu.

API: PTT Kurumsal "Gönderi Takip" SOAP servisi (kurumsalwebservis.ptt.gov.tr).
Webhook: PTT kurumsal müşterilerine event push opsiyonu sağlıyor; payload JSON
veya XML olabiliyor. Burada JSON kabul edip XML için yedek parser.

Beklenen webhook payload örnegi:
  {"barkod":"PTT123","durumKodu":"50","durum":"Teslim Edildi",
   "tarih":"2026-05-28T14:32:11","birim":"İstanbul Merkez"}

İmza: X-PTT-Signature: hex(hmac_sha256(secret, body))

Mock modu: credential yoksa `fetch()` deterministik 3 event döner (test/dev için).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from app.config import get_settings
from app.services.carriers.base import CarrierAdapter, NormalizedEvent

log = logging.getLogger(__name__)

# PTT durum kodu → internal
_STATUS_MAP: dict[str, str] = {
    "10": "created",
    "15": "created",
    "20": "picked_up",
    "30": "in_transit",
    "35": "in_transit",
    "40": "out_for_delivery",
    "50": "delivered",
    "55": "delivered",
    "60": "failed_attempt",
    "70": "returned",
    "99": "cancelled",
}

_TEXT_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"teslim edil", re.I), "delivered"),
    (re.compile(r"dağıtı|dagiti", re.I), "out_for_delivery"),
    (re.compile(r"transfer|merkez|aktarma", re.I), "in_transit"),
    (re.compile(r"kabul edil|teslim alın|alindi", re.I), "picked_up"),
    (re.compile(r"iade", re.I), "returned"),
    (re.compile(r"iptal", re.I), "cancelled"),
    (re.compile(r"başarısız|basariz|bulunamadı", re.I), "failed_attempt"),
    (re.compile(r"oluştur|barkod|kayıt", re.I), "created"),
]


def _classify(status_code: str | None, status_text: str | None) -> str:
    if status_code and str(status_code).strip() in _STATUS_MAP:
        return _STATUS_MAP[str(status_code).strip()]
    if status_text:
        for pat, code in _TEXT_HINTS:
            if pat.search(status_text):
                return code
    return "in_transit"


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


class PttAdapter(CarrierAdapter):
    code = "ptt"
    display_name = "PTT Kargo"

    def __init__(self) -> None:
        s = get_settings()
        self.username = s.ptt_username
        self.password = s.ptt_password
        self.customer_code = s.ptt_customer_code
        self.wsdl_url = s.ptt_wsdl_url
        self.secret = s.ptt_webhook_secret

    def is_configured(self) -> bool:
        return bool(self.username and self.password)

    # ── Webhook ────────────────────────────────────────────────────────
    def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        if not self.secret:
            return True
        sig = headers.get("x-ptt-signature") or headers.get("X-PTT-Signature")
        if not sig:
            return False
        expected = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig.strip().lower(), expected.lower())

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> list[NormalizedEvent]:
        text = body.decode("utf-8", errors="replace").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                data = [data]
            return [self._row_to_event(row) for row in data if isinstance(row, dict)]
        except json.JSONDecodeError:
            return self._parse_xml(text)

    def _parse_xml(self, text: str) -> list[NormalizedEvent]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            log.warning("ptt webhook xml parse failed: %s", e)
            return []
        events: list[NormalizedEvent] = []
        for el in root.iter():
            tag = el.tag.split("}")[-1].lower()
            if tag not in ("event", "hareket", "gonderitakip", "movement"):
                continue
            row = {child.tag.split("}")[-1]: (child.text or "").strip() for child in el}
            if row:
                events.append(self._row_to_event(row))
        return events

    def _row_to_event(self, row: dict[str, Any]) -> NormalizedEvent:
        tn = str(row.get("barkod") or row.get("trackingNumber") or row.get("trackingNo") or "")
        status_code = row.get("durumKodu") or row.get("statusCode") or row.get("kod")
        status_text = row.get("durum") or row.get("status") or row.get("aciklama") or row.get("description")
        return NormalizedEvent(
            carrier=self.code,
            tracking_no=tn,
            code=_classify(str(status_code) if status_code is not None else None, status_text),
            occurred_at=_parse_dt(row.get("tarih") or row.get("eventDate") or row.get("date")),
            raw_status=status_text or (str(status_code) if status_code else None),
            description=row.get("aciklama") or row.get("description") or status_text,
            location=row.get("birim") or row.get("unit") or row.get("location"),
            raw_payload=row,
        )

    # ── Polling ────────────────────────────────────────────────────────
    async def fetch(self, tracking_no: str) -> list[NormalizedEvent]:
        if not self.is_configured():
            return _mock_events(self.code, tracking_no)

        envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:tem="http://tempuri.org/">
  <soap:Body>
    <tem:GonderiTakip>
      <tem:kullaniciAdi>{self.username}</tem:kullaniciAdi>
      <tem:sifre>{self.password}</tem:sifre>
      <tem:musteriKodu>{self.customer_code}</tem:musteriKodu>
      <tem:barkod>{tracking_no}</tem:barkod>
    </tem:GonderiTakip>
  </soap:Body>
</soap:Envelope>"""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    self.wsdl_url.replace("?wsdl", ""),
                    content=envelope.encode("utf-8"),
                    headers={
                        "Content-Type": "text/xml; charset=utf-8",
                        "SOAPAction": "http://tempuri.org/GonderiTakip",
                    },
                )
                resp.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("ptt fetch failed for %s: %s", tracking_no, e)
            return []
        return self._parse_xml(resp.text)


def _mock_events(carrier: str, tracking_no: str) -> list[NormalizedEvent]:
    now = datetime.now(timezone.utc)
    return [
        NormalizedEvent(
            carrier=carrier, tracking_no=tracking_no, code="picked_up",
            occurred_at=now - timedelta(days=2),
            raw_status="MOCK: kabul edildi", description="Şubeden teslim alındı",
            location="İstanbul Merkez",
        ),
        NormalizedEvent(
            carrier=carrier, tracking_no=tracking_no, code="in_transit",
            occurred_at=now - timedelta(days=1),
            raw_status="MOCK: transfer", description="Aktarma merkezinde",
            location="Ankara Merkez",
        ),
        NormalizedEvent(
            carrier=carrier, tracking_no=tracking_no, code="out_for_delivery",
            occurred_at=now - timedelta(hours=4),
            raw_status="MOCK: dağıtımda", description="Dağıtım için yola çıktı",
            location="Ankara Çankaya",
        ),
    ]
