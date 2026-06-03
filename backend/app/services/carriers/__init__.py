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

from app.services.carriers.base import EVENT_CODES, CarrierAdapter, NormalizedEvent
from app.services.carriers.dispatch import CARRIER_CODES, apply_event, get_adapter

__all__ = [
    "CARRIER_CODES",
    "EVENT_CODES",
    "CarrierAdapter",
    "NormalizedEvent",
    "apply_event",
    "get_adapter",
]
