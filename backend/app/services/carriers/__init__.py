"""Kargo firma adapter paketi.

Her adapter `CarrierAdapter` arayüzünü uygular:
  - fetch(tracking_no) → list[NormalizedEvent]  (canlı API poll)
  - parse_webhook(headers, body) → list[NormalizedEvent]
  - verify_signature(headers, body) → bool

Internal event kodları (carriers/base.py içinde EVENT_CODES):
  created, picked_up, in_transit, out_for_delivery, delivered,
  failed_attempt, returned, cancelled.

Order.status maplemesi `dispatch.apply_event()` içinde:
  picked_up | in_transit | out_for_delivery → shipped
  delivered → delivered
  returned | cancelled → cancelled
"""
from app.services.carriers.base import CarrierAdapter, NormalizedEvent, EVENT_CODES
from app.services.carriers.dispatch import get_adapter, apply_event, CARRIER_CODES

__all__ = [
    "CarrierAdapter",
    "NormalizedEvent",
    "EVENT_CODES",
    "get_adapter",
    "apply_event",
    "CARRIER_CODES",
]
