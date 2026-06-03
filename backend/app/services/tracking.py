"""Sipariş takip servisi — timeline, ETA, kargo firması URL'si.

Adımların zamanı `audit_logs` üzerinden çıkarılır (order-status action'larında
"X → Y" mesajı bulunur). Audit yoksa `order.created_at` ve `order.updated_at`
fallback olarak kullanılır.

Kargo firması mapping'i basit prefix kuralları üzerinden çalışır
(`tracking_no` örnekleri):
  YK / 1Y / Y      → Yurtiçi Kargo
  MNG              → MNG Kargo
  ARAS             → Aras Kargo
  PTT              → PTT Kargo
  HEPSI            → Hepsijet
  TRX / SUR        → Sürat Kargo
Aksi halde "Kargo firması" jenerik etiketiyle döner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Order, OrderStatus, PaymentStatus, ReturnRequest, ShipmentEvent

# ── Kargo firması mapping'i ────────────────────────────────────────────────
_CARRIER_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^YK", re.I), "Yurtiçi Kargo", "https://gonderitakip.yurticikargo.com/?code={tn}"),
    (
        re.compile(r"^MNG", re.I),
        "MNG Kargo",
        "https://service.mngkargo.com.tr/api/cargotracking/{tn}",
    ),
    (
        re.compile(r"^ARAS", re.I),
        "Aras Kargo",
        "https://kargotakip.araskargo.com.tr/mainpage.aspx?code={tn}",
    ),
    (re.compile(r"^PTT", re.I), "PTT Kargo", "https://gonderitakip.ptt.gov.tr/Track/Verify?q={tn}"),
    (
        re.compile(r"^HEPSI|^HX", re.I),
        "Hepsijet",
        "https://www.hepsijet.com/gonderi-takibi?trackingId={tn}",
    ),
    (
        re.compile(r"^TRX|^SUR", re.I),
        "Sürat Kargo",
        "https://www.suratkargo.com.tr/KargoTakip/?kargotakipno={tn}",
    ),
]


_CARRIER_CODE_TO_DISPLAY: dict[str, tuple[str, str]] = {
    "aras": ("Aras Kargo", "https://kargotakip.araskargo.com.tr/mainpage.aspx?code={tn}"),
    "yurtici": ("Yurtiçi Kargo", "https://gonderitakip.yurticikargo.com/?code={tn}"),
    "mng": ("MNG Kargo", "https://service.mngkargo.com.tr/api/cargotracking/{tn}"),
    "ptt": ("PTT Kargo", "https://gonderitakip.ptt.gov.tr/Track/Verify?q={tn}"),
    "hepsijet": ("Hepsijet", "https://www.hepsijet.com/gonderi-takibi?trackingId={tn}"),
    "surat": ("Sürat Kargo", "https://www.suratkargo.com.tr/KargoTakip/?kargotakipno={tn}"),
}


def carrier_for(tracking_no: str | None, carrier_code: str | None = None) -> dict | None:
    """Tracking numarasından (veya verilen carrier kodundan) kargo firması bilgisi."""
    if not tracking_no:
        return None
    if carrier_code and carrier_code in _CARRIER_CODE_TO_DISPLAY:
        name, url_tpl = _CARRIER_CODE_TO_DISPLAY[carrier_code]
        return {
            "code": carrier_code,
            "name": name,
            "tracking_no": tracking_no,
            "tracking_url": url_tpl.format(tn=tracking_no),
        }
    for pat, name, url_tpl in _CARRIER_RULES:
        if pat.search(tracking_no):
            return {
                "code": None,
                "name": name,
                "tracking_no": tracking_no,
                "tracking_url": url_tpl.format(tn=tracking_no),
            }
    return {"code": None, "name": "Kargo firması", "tracking_no": tracking_no, "tracking_url": None}


# ── Adım modeli ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TrackingStep:
    code: str  # pending | paid | processing | shipped | delivered | cancelled | refunded
    label: str  # UI'de gösterilen Türkçe etiket
    description: str  # kısa açıklama
    at: datetime | None  # gerçekleşme zamanı (None → bekleniyor)
    is_done: bool
    is_active: bool  # şu an bu adımda mı

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "description": self.description,
            "at": self.at.isoformat() if self.at else None,
            "is_done": self.is_done,
            "is_active": self.is_active,
        }


_STEP_DEFS: list[tuple[str, str, str]] = [
    ("pending", "Sipariş Alındı", "Siparişiniz alındı, ödeme onayı bekleniyor."),
    ("paid", "Ödeme Onaylandı", "Ödemeniz başarıyla alındı."),
    ("processing", "Hazırlanıyor", "Siparişiniz depoda hazırlanıyor."),
    ("shipped", "Kargoya Verildi", "Siparişiniz kargo firmasına teslim edildi."),
    ("delivered", "Teslim Edildi", "Siparişiniz adresinize teslim edildi."),
]

_TERMINAL_STATUSES = {OrderStatus.CANCELLED.value, OrderStatus.DELIVERED.value}


# ── Audit'ten transition zamanları çıkar ───────────────────────────────────
_TRANSITION_RE = re.compile(r"([a-z_]+)\s*→\s*([a-z_]+)")


async def _status_transition_times(db: AsyncSession, order_no: str) -> dict[str, datetime]:
    """audit_logs'tan `{yeni_status: ne_zaman}` haritası çıkar."""
    rows = (
        (
            await db.execute(
                select(AuditLog)
                .where(
                    and_(AuditLog.action == "order-status", AuditLog.message.like(f"{order_no}%"))
                )
                .order_by(AuditLog.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, datetime] = {}
    for row in rows:
        m = _TRANSITION_RE.search(row.message or "")
        if not m:
            continue
        new_status = m.group(2)
        out[new_status] = row.created_at
    return out


# ── Timeline kurulumu ──────────────────────────────────────────────────────
def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def build_timeline(db: AsyncSession, order: Order) -> list[dict]:
    """Sipariş için kronolojik timeline döndür.

    İptal edilmişse 5 adım yerine: pending → cancelled gösterilir.
    """
    transitions = await _status_transition_times(db, order.order_no)
    created_at = _ensure_utc(order.created_at)
    updated_at = _ensure_utc(order.updated_at)

    if order.status == OrderStatus.CANCELLED.value:
        cancel_at = _ensure_utc(transitions.get("cancelled")) or updated_at
        return [
            TrackingStep(
                code="pending",
                label="Sipariş Alındı",
                description="Siparişiniz alındı.",
                at=created_at,
                is_done=True,
                is_active=False,
            ).as_dict(),
            TrackingStep(
                code="cancelled",
                label="İptal Edildi",
                description="Sipariş iptal edildi.",
                at=cancel_at,
                is_done=True,
                is_active=True,
            ).as_dict(),
        ]

    paid_at = (
        _ensure_utc(
            transitions.get("processing")
        )  # status processing'e geçtiyse ödeme onaylanmış sayılır
        or (updated_at if order.payment_status == PaymentStatus.SUCCESS.value else None)
    )

    # Her bir adım için "tamamlandı/aktif" durumunu çöz
    status_index = {s[0]: i for i, s in enumerate(_STEP_DEFS)}
    # mantıksal olarak "paid" pending'den sonra ödeme başarılı olduğunda aktif
    is_paid = order.payment_status == PaymentStatus.SUCCESS.value
    current_index = status_index.get(order.status, 0)
    if is_paid and current_index < 1:
        current_index = 1  # en azından paid step'ine yükselt

    raw_times: dict[str, datetime | None] = {
        "pending": created_at,
        "paid": _ensure_utc(paid_at),
        "processing": _ensure_utc(transitions.get("processing")),
        "shipped": _ensure_utc(transitions.get("shipped")),
        "delivered": _ensure_utc(transitions.get("delivered")),
    }

    timeline: list[dict] = []
    for i, (code, label, desc) in enumerate(_STEP_DEFS):
        is_done = i < current_index or (
            i == current_index and code in (order.status, "paid" and is_paid)
        )
        # Daha sade kural: i < current_index → done, i == current_index → active
        is_done = i < current_index
        is_active = i == current_index
        at = raw_times.get(code)
        timeline.append(
            TrackingStep(
                code=code,
                label=label,
                description=desc,
                at=at,
                is_done=is_done,
                is_active=is_active,
            ).as_dict()
        )
    return timeline


# ── ETA tahmini ────────────────────────────────────────────────────────────
def estimate_delivery(order: Order) -> dict | None:
    """`shipping.calc_shipping` ile aynı zone mantığını kullanarak ETA döner.

    Eğer sipariş zaten teslim edildiyse None.
    """
    from app.services.shipping import ZONE_RATES, zone_for_city

    if order.status == OrderStatus.DELIVERED.value:
        return None
    if order.status == OrderStatus.CANCELLED.value:
        return None

    zone = zone_for_city(order.customer_city or "")
    zone_info = ZONE_RATES[zone]
    base_dt = _ensure_utc(order.updated_at) or _ensure_utc(order.created_at) or datetime.now(UTC)

    # Kargoya verilmemişse "baseDt + 1 gün processing + zone min/max"
    if order.status in (OrderStatus.PENDING.value, OrderStatus.PROCESSING.value):
        prep_extra = 1
    else:
        prep_extra = 0
    min_days = zone_info["days_min"] + prep_extra
    max_days = zone_info["days_max"] + prep_extra
    eta_min = base_dt + timedelta(days=min_days)
    eta_max = base_dt + timedelta(days=max_days)
    return {
        "zone": zone,
        "min_date": eta_min.date().isoformat(),
        "max_date": eta_max.date().isoformat(),
        "min_days_from_now": max(0, (eta_min.date() - datetime.now(UTC).date()).days),
        "max_days_from_now": max(0, (eta_max.date() - datetime.now(UTC).date()).days),
    }


# ── Üst düzey tracking response builder ───────────────────────────────────
async def build_tracking_response(db: AsyncSession, order: Order) -> dict:
    """Tüm UI'ı besleyen kompakt tracking payload'ı."""
    timeline = await build_timeline(db, order)
    eta = estimate_delivery(order)
    carrier = carrier_for(order.tracking_no, getattr(order, "carrier", None))

    # Kargo firma event'leri (webhook/poll'dan gelmiş gerçek hareketler)
    shipment_rows = (
        (
            await db.execute(
                select(ShipmentEvent)
                .where(ShipmentEvent.order_no == order.order_no)
                .order_by(ShipmentEvent.occurred_at.asc())
            )
        )
        .scalars()
        .all()
    )
    shipment_events = [
        {
            "code": r.code,
            "raw_status": r.raw_status,
            "description": r.description,
            "location": r.location,
            "occurred_at": (_ensure_utc(r.occurred_at) or datetime.now(UTC)).isoformat(),
            "source": r.source,
        }
        for r in shipment_rows
    ]

    # Bu sipariş için aktif iade talebi var mı?
    return_row = (
        await db.execute(
            select(ReturnRequest)
            .where(ReturnRequest.order_no == order.order_no)
            .order_by(ReturnRequest.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return_summary = None
    if return_row:
        return_summary = {
            "id": return_row.id,
            "status": return_row.status,
            "reason": return_row.reason,
            "refund_amount": float(return_row.refund_amount or 0),
            "created_at": (_ensure_utc(return_row.created_at) or datetime.now(UTC)).isoformat(),
        }

    # İade hakkı (teslim edildiğinden bu yana 14 gün)
    can_return = False
    if order.status == OrderStatus.DELIVERED.value and not return_row:
        upd = _ensure_utc(order.updated_at) or _ensure_utc(order.created_at)
        if upd and (datetime.now(UTC) - upd).days <= 14:
            can_return = True

    return {
        "order_no": order.order_no,
        "status": order.status,
        "status_label": _status_label(order.status),
        "payment_status": order.payment_status,
        "total": float(order.total),
        "created_at": (_ensure_utc(order.created_at) or datetime.now(UTC)).isoformat(),
        "updated_at": (_ensure_utc(order.updated_at) or datetime.now(UTC)).isoformat(),
        "customer_name": order.customer_name,
        "customer_city": order.customer_city,
        "customer_address": order.customer_address,
        "timeline": timeline,
        "carrier": carrier,
        "shipment_events": shipment_events,
        "eta": eta,
        "items": [
            {
                "name": i.name,
                "qty": int(i.qty),
                "price": float(i.price),
                "icon": i.icon,
                "image": i.image,
                "product_id": i.product_id,
            }
            for i in (order.items or [])
        ],
        "return_request": return_summary,
        "can_request_return": can_return,
    }


def _status_label(status: str) -> str:
    return {
        OrderStatus.PENDING.value: "Beklemede",
        OrderStatus.PROCESSING.value: "Hazırlanıyor",
        OrderStatus.SHIPPED.value: "Kargoda",
        OrderStatus.DELIVERED.value: "Teslim edildi",
        OrderStatus.CANCELLED.value: "İptal",
    }.get(status, status)
