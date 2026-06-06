"""Birden çok kargo firması için ortak adapter altyapısı.

`GenericCarrierAdapter` webhook (event push) + mock fetch sağlar; alt sınıf
yalnızca `code`, `display_name`, `signature_header`, `status_map` ve `_secret()`
verir. Gerçek API polling'i (Aras/Yurtiçi SOAP, MNG REST...) kimlik bilgileri
geldiğinde alt sınıfın `_fetch_real()`'ine eklenir; o zamana kadar mock döner.

Webhook payload alan adları firmadan firmaya değiştiği için geniş bir alias
seti ile (TR + EN) tolere edilir. Statü kodu `status_map`'te bulunamazsa
firma-bağımsız Türkçe metin sınıflandırıcıya düşülür (çoğu TR kargo benzer
ifadeler kullanır: "Teslim edildi", "Dağıtımda", "Aktarma merkezinde"...).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from xml.etree import ElementTree as ET

from app.services.carriers.base import CarrierAdapter, NormalizedEvent

log = logging.getLogger(__name__)

# Türkçe statü metni → internal event kodu (firma-bağımsız yedek sınıflandırma).
_TEXT_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"teslim edil", re.I), "delivered"),
    (re.compile(r"dağıt|dagit", re.I), "out_for_delivery"),
    (re.compile(r"transfer|aktarma|yola çık|yola cik|sevk|hareket", re.I), "in_transit"),
    (re.compile(r"şube|sube|merkeze|kabul edil|teslim al|alındı|alindi", re.I), "picked_up"),
    (re.compile(r"iade", re.I), "returned"),
    (re.compile(r"iptal", re.I), "cancelled"),
    (re.compile(r"başarısız|basariz|bulunamadı|bulunamadi|adreste yok", re.I), "failed_attempt"),
    (re.compile(r"oluştur|olustur|barkod|kayıt|kayit", re.I), "created"),
]

# Webhook payload alan adları — geniş alias seti (firmadan firmaya değişir).
_TN_KEYS = (
    "barkod",
    "trackingNumber",
    "trackingNo",
    "trackingId",
    "kargoTakipNo",
    "takipNo",
    "cargoKey",
    "shipmentId",
    "referenceId",
    "documentId",
    "irsaliyeNo",
)
_CODE_KEYS = ("durumKodu", "statusCode", "kod", "eventCode", "code", "hareketKodu", "statusId")
_TEXT_KEYS = (
    "durum",
    "status",
    "aciklama",
    "açıklama",
    "description",
    "statusText",
    "eventDescription",
    "hareket",
    "movementText",
    "islem",
)
_DATE_KEYS = (
    "tarih",
    "eventDate",
    "date",
    "islemTarihi",
    "işlemTarihi",
    "occurredAt",
    "timestamp",
    "datetime",
    "hareketTarihi",
)
_LOC_KEYS = ("birim", "unit", "location", "sube", "şube", "branch", "city", "il", "merkez")

_XML_ROW_TAGS = ("event", "hareket", "movement", "shipmentmovement", "kargohareket")
_JSON_LIST_WRAPPERS = ("events", "data", "hareketler", "movements", "result", "items")


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """row içinde keys'ten ilk dolu değeri döndür (önce birebir, sonra case-insensitive)."""
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    low = {str(k).lower(): v for k, v in row.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, ""):
            return v
    return None


def parse_dt(value: Any) -> datetime:
    if not value:
        return datetime.now(UTC)
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text[:26], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return datetime.now(UTC)


def classify(status_code: Any, status_text: str | None, status_map: dict[str, str]) -> str:
    """Statü kodu → internal event; kod bulunamazsa Türkçe metinden çıkar."""
    if status_code is not None:
        key = str(status_code).strip()
        if key in status_map:
            return status_map[key]
    if status_text:
        for pat, code in _TEXT_HINTS:
            if pat.search(status_text):
                return code
    return "in_transit"


def mock_events(carrier: str, tracking_no: str) -> list[NormalizedEvent]:
    """Kimlik bilgisi yokken deterministik 3 event (dev/test)."""
    now = datetime.now(UTC)
    plan = (
        ("picked_up", now - timedelta(days=2), "Şubeden teslim alındı", "İstanbul Aktarma"),
        ("in_transit", now - timedelta(days=1), "Aktarma merkezinde", "Ankara Aktarma"),
        ("out_for_delivery", now - timedelta(hours=4), "Dağıtıma çıktı", "Ankara Çankaya"),
    )
    return [
        NormalizedEvent(
            carrier=carrier,
            tracking_no=tracking_no,
            code=code,
            occurred_at=at,
            raw_status=f"MOCK: {desc}",
            description=desc,
            location=loc,
        )
        for code, at, desc, loc in plan
    ]


class GenericCarrierAdapter(CarrierAdapter):
    """Webhook + mock tabanlı genel kargo adapter'ı.

    Alt sınıf override eder: `code`, `display_name`, `signature_header`,
    `status_map` (opsiyonel), `_secret()`. Gerçek API için `is_configured()` +
    `_fetch_real()` override edilir.
    """

    status_map: dict[str, str] = {}  # noqa: RUF012 — alt sınıf override eder
    signature_header: str = ""  # örn. "x-aras-signature"

    def _secret(self) -> str:
        """Webhook HMAC secret'ı (settings'ten). Boşsa imza atlanır."""
        return ""

    def is_configured(self) -> bool:
        # Gerçek polling API'si yok → fetch() mock döner. Creds gelince override.
        return False

    # ── Webhook ───────────────────────────────────────────────────────────
    def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        secret = self._secret()
        if not secret or not self.signature_header:
            return True
        sig = headers.get(self.signature_header.lower())
        if not sig:
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig.strip().lower(), expected.lower())

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> list[NormalizedEvent]:
        text = body.decode("utf-8", errors="replace").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return self._parse_xml(text)
        if isinstance(data, dict):
            for wrap in _JSON_LIST_WRAPPERS:
                inner = data.get(wrap)
                if isinstance(inner, list):
                    data = inner
                    break
            else:
                data = [data]
        if not isinstance(data, list):
            return []
        return [self._row_to_event(r) for r in data if isinstance(r, dict)]

    def _parse_xml(self, text: str) -> list[NormalizedEvent]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            log.warning("%s webhook xml parse failed: %s", self.code, e)
            return []
        events: list[NormalizedEvent] = []
        for el in root.iter():
            tag = el.tag.split("}")[-1].lower()
            if tag not in _XML_ROW_TAGS:
                continue
            row = {child.tag.split("}")[-1]: (child.text or "").strip() for child in el}
            if row:
                events.append(self._row_to_event(row))
        return events

    def _row_to_event(self, row: dict[str, Any]) -> NormalizedEvent:
        tn = _first(row, _TN_KEYS)
        code = _first(row, _CODE_KEYS)
        text_val = _first(row, _TEXT_KEYS)
        loc = _first(row, _LOC_KEYS)
        status_text = str(text_val) if text_val else None
        return NormalizedEvent(
            carrier=self.code,
            tracking_no=str(tn or ""),
            code=classify(code, status_text, self.status_map),
            occurred_at=parse_dt(_first(row, _DATE_KEYS)),
            raw_status=status_text or (str(code) if code is not None else None),
            description=status_text,
            location=str(loc) if loc else None,
            raw_payload=row,
        )

    # ── Polling ───────────────────────────────────────────────────────────
    async def fetch(self, tracking_no: str) -> list[NormalizedEvent]:
        if not self.is_configured():
            return mock_events(self.code, tracking_no)
        try:
            return await self._fetch_real(tracking_no)
        except Exception:
            log.exception("%s fetch failed for %s", self.code, tracking_no)
            return []

    async def _fetch_real(self, tracking_no: str) -> list[NormalizedEvent]:
        """Kimlik bilgileri geldiğinde firmaya özgü API burada uygulanır
        (Aras/Yurtiçi: SOAP, MNG: REST). Şimdilik mock'a düşer."""
        return mock_events(self.code, tracking_no)
