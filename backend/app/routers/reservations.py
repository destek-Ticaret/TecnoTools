from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Reservation

router = APIRouter(prefix="/api/reservations", tags=["reservations"])

RESERVATION_TTL_MIN = 20


@router.post("/sync")
async def sync_reservations(
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Frontend cart'ın güncel hâlini gönderir; bu session'ın tüm rezervasyonları bununla değiştirilir.
    body = { session_id: str, items: [{ product_id: int, qty: int }, ...] }"""
    session_id = (body.get("session_id") or "").strip()
    items = body.get("items") or []
    if not session_id:
        return {"ok": False, "detail": "session_id gerekli"}

    # Önce expired olanları temizle
    now = datetime.now(timezone.utc)
    await db.execute(delete(Reservation).where(Reservation.expires_at <= now))

    # Bu session'a ait kayıtları sil
    await db.execute(delete(Reservation).where(Reservation.session_id == session_id))

    expiry = now + timedelta(minutes=RESERVATION_TTL_MIN)
    rows = []
    for it in items:
        pid = int(it.get("product_id") or 0)
        qty = int(it.get("qty") or 0)
        if pid > 0 and qty > 0:
            rows.append({"session_id": session_id, "product_id": pid, "qty": qty, "expires_at": expiry})
    if rows:
        await db.execute(pg_insert(Reservation).values(rows))
    await db.commit()
    return {"ok": True, "expires_at": expiry.isoformat()}


@router.post("/release")
async def release_reservations(
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Sipariş tamamlanmasa bile elle serbest bırakmak için."""
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        return {"ok": False}
    await db.execute(delete(Reservation).where(Reservation.session_id == session_id))
    await db.commit()
    return {"ok": True}
