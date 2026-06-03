"""Kargo entegrasyon endpoint'leri.

Public webhook:
  POST /api/shipping/webhook/{carrier}
    Headers: X-Aras-Signature / X-YK-Signature (HMAC-SHA256, hex)
    Body: firmaya özgü JSON/XML. Adapter parse eder, ShipmentEvent yazar,
    Order.status'ü ileri yönde günceller.

Admin/internal:
  GET  /api/shipping/track/{order_no}   — DB'deki event listesi + status.
  POST /api/shipping/sync/{order_no}    — Adapter.fetch() ile canlı poll, eksik
                                          event'leri uygular.
  POST /api/shipping/assign/{order_no}  — Sipariş için carrier + tracking_no set.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_editor
from app.models import AuditLog, Order, ShipmentEvent, User
from app.rate_limit import limiter
from app.services.carriers import CARRIER_CODES, apply_event, get_adapter

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/shipping", tags=["shipping"])


# ── Webhook ────────────────────────────────────────────────────────────
@router.post("/webhook/{carrier}", status_code=202)
@limiter.limit("120/minute")
async def carrier_webhook(carrier: str, request: Request, db: AsyncSession = Depends(get_db)):
    if carrier not in CARRIER_CODES:
        raise HTTPException(status_code=404, detail="Bilinmeyen kargo firması")
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    adapter = get_adapter(carrier)
    if not adapter.verify_signature(headers, body):
        log.warning("invalid signature on %s webhook (len=%d)", carrier, len(body))
        raise HTTPException(status_code=401, detail="İmza geçersiz")
    events = adapter.parse_webhook(headers, body)
    if not events:
        return {"accepted": 0, "applied": 0}

    applied = 0
    for ev in events:
        _, _, changed = await apply_event(db, ev, source="webhook")
        if changed:
            applied += 1
    await db.commit()
    return {"accepted": len(events), "applied": applied}


# ── Tracking listesi ───────────────────────────────────────────────────
@router.get("/track/{order_no}")
async def list_events(order_no: str, db: AsyncSession = Depends(get_db)):
    """Public — order_no biliniyorsa event listesi döner (PII içermez)."""
    order = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    rows = (
        await db.execute(
            select(ShipmentEvent)
            .where(ShipmentEvent.order_no == order_no)
            .order_by(ShipmentEvent.occurred_at.asc())
        )
    ).scalars().all()
    return {
        "order_no": order.order_no,
        "status": order.status,
        "carrier": order.carrier,
        "tracking_no": order.tracking_no,
        "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "last_sync_at": order.last_tracking_sync_at.isoformat() if order.last_tracking_sync_at else None,
        "events": [
            {
                "code": r.code,
                "raw_status": r.raw_status,
                "description": r.description,
                "location": r.location,
                "occurred_at": r.occurred_at.isoformat(),
                "source": r.source,
            }
            for r in rows
        ],
    }


# ── Canlı sync (admin) ─────────────────────────────────────────────────
@router.post("/sync/{order_no}")
async def sync_order(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    order = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    if not order.tracking_no:
        raise HTTPException(status_code=400, detail="Takip numarası yok")
    carrier = order.carrier or _guess_carrier(order.tracking_no)
    if not carrier:
        raise HTTPException(status_code=400, detail="Kargo firması belirlenemedi")
    adapter = get_adapter(carrier)
    events = await adapter.fetch(order.tracking_no)
    applied = 0
    for ev in events:
        _, _, changed = await apply_event(db, ev, order=order, source="poll")
        if changed:
            applied += 1
    db.add(AuditLog(actor=user.username, action="shipping-sync",
                    message=f"{order_no}: {carrier} {len(events)} event"))
    await db.commit()
    return {"fetched": len(events), "applied": applied, "carrier": carrier}


# ── Carrier + tracking atama (admin) ───────────────────────────────────
class AssignIn(BaseModel):
    carrier: str = Field(..., pattern=r"^ptt$")
    tracking_no: str = Field(..., min_length=4, max_length=64)


@router.post("/assign/{order_no}")
async def assign_carrier(
    order_no: str,
    payload: AssignIn = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    order = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    order.carrier = payload.carrier
    order.tracking_no = payload.tracking_no.strip()
    if not order.shipped_at:
        order.shipped_at = datetime.now(timezone.utc)
    db.add(AuditLog(actor=user.username, action="shipping-assign",
                    message=f"{order_no}: {payload.carrier} {payload.tracking_no}"))
    await db.commit()
    await db.refresh(order)
    return {"order_no": order.order_no, "carrier": order.carrier, "tracking_no": order.tracking_no}


def _guess_carrier(tracking_no: str) -> str | None:
    tn = (tracking_no or "").upper()
    if tn.startswith("PTT"):
        return "ptt"
    return None
