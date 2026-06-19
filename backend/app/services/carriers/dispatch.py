"""Adapter seçimi + normalize event'i Order'a uygulama.

`apply_event()` idempotent: aynı (carrier, tracking_no, code, occurred_at)
çakışmalarında IntegrityError yer (uq_shipment_event_dedupe) ve sessizce
yutulur. Order.status sadece "ileri" yönde değişir (delivered'dan geriye gitmez).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Order, OrderStatus, ShipmentEvent
from app.services.carriers.base import CarrierAdapter, NormalizedEvent
from app.services.events import bus

log = logging.getLogger(__name__)

CARRIER_CODES = ("dhl",)

# Hangi event kodu Order.status'ü hangi değere çıkarmalı.
_EVENT_TO_STATUS: dict[str, str] = {
    "picked_up": OrderStatus.SHIPPED.value,
    "in_transit": OrderStatus.SHIPPED.value,
    "out_for_delivery": OrderStatus.SHIPPED.value,
    "delivered": OrderStatus.DELIVERED.value,
    "returned": OrderStatus.CANCELLED.value,
    "cancelled": OrderStatus.CANCELLED.value,
}

# Statü sıralaması (geri sayım engelle).
_STATUS_ORDER = {
    OrderStatus.PENDING.value: 0,
    OrderStatus.PROCESSING.value: 1,
    OrderStatus.SHIPPED.value: 2,
    OrderStatus.DELIVERED.value: 3,
    OrderStatus.CANCELLED.value: 3,
}


def get_adapter(carrier: str) -> CarrierAdapter:
    """Carrier kodundan adapter instance döndür."""
    from app.services.carriers.dhl import DhlAdapter

    registry: dict[str, type[CarrierAdapter]] = {
        "dhl": DhlAdapter,
    }
    cls = registry.get(carrier)
    if cls is None:
        raise ValueError(f"Unknown carrier: {carrier!r}")
    return cls()


async def apply_event(
    db: AsyncSession,
    event: NormalizedEvent,
    *,
    order: Order | None = None,
    source: str = "webhook",
) -> tuple[ShipmentEvent | None, Order | None, bool]:
    """Tek event'i DB'ye yaz + Order.status'ü güncelle.

    Returns: (kaydedilen event | None, güncellenen order | None, status_changed?)
    """
    # Order'ı bul — verilmemişse tracking_no üzerinden
    if order is None and event.tracking_no:
        order = (
            await db.execute(select(Order).where(Order.tracking_no == event.tracking_no))
        ).scalar_one_or_none()

    if order is None:
        log.info(
            "shipment event for unknown tracking_no=%s carrier=%s", event.tracking_no, event.carrier
        )
        return None, None, False

    # 1) Event satırını ekle (idempotent)
    row = ShipmentEvent(
        order_no=order.order_no,
        carrier=event.carrier,
        tracking_no=event.tracking_no,
        code=event.code,
        raw_status=event.raw_status,
        description=event.description,
        location=event.location,
        occurred_at=event.occurred_at
        if event.occurred_at.tzinfo
        else event.occurred_at.replace(tzinfo=UTC),
        source=source,
        raw_payload=event.raw_payload,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return None, order, False  # zaten var

    # 2) Order.status'ü hizala (ileri yönde + carrier alanını doldur)
    status_changed = False
    new_status = _EVENT_TO_STATUS.get(event.code)
    if not order.carrier:
        order.carrier = event.carrier
    if new_status and _STATUS_ORDER[new_status] > _STATUS_ORDER.get(order.status, 0):
        old = order.status
        order.status = new_status
        if new_status == OrderStatus.SHIPPED.value and not order.shipped_at:
            order.shipped_at = row.occurred_at
        if new_status == OrderStatus.DELIVERED.value and not order.delivered_at:
            order.delivered_at = row.occurred_at
        db.add(
            AuditLog(
                actor=f"carrier:{event.carrier}",
                action="order-status",
                message=f"{order.order_no}: {old} → {new_status}",
            )
        )
        status_changed = True

    order.last_tracking_sync_at = datetime.now(UTC)
    await db.flush()

    if status_changed:
        await bus.publish(
            "order_status_changed",
            {
                "order_no": order.order_no,
                "status": order.status,
                "carrier": event.carrier,
            },
        )
        # Müşteriye bilgilendirme maili
        try:
            from app.services.email import send_order_status_update

            await send_order_status_update(
                order, "shipped" if order.status == "delivered" else "processing"
            )
        except Exception:
            log.exception("send_order_status_update failed")

    return row, order, status_changed
