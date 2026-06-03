"""Self-hosted minimal analytics.

İlke: Hiçbir kişisel veri saklanmaz. IP hash'lenir, kişi izi takip edilmez,
sadece toplu sayımlar yapılır.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin
from app.models import AnalyticsEvent, User

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

_DAILY_SALT_SEED = "tt-analytics-daily-salt"


def _ip_hash(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    ip = xff.split(",")[0].strip() or (request.client.host if request.client else "0.0.0.0")
    today = datetime.now(UTC).strftime("%Y%m%d")
    salt = f"{_DAILY_SALT_SEED}:{today}"
    return hashlib.sha256(f"{ip}:{salt}".encode()).hexdigest()[:16]


@router.post("/track", status_code=204)
async def track(request: Request, payload: dict = Body(...), db: AsyncSession = Depends(get_db)):
    event = (payload.get("event") or "").strip()[:64]
    if not event:
        return None
    db.add(
        AnalyticsEvent(
            event=event,
            path=(payload.get("path") or "")[:255] or None,
            referrer=(payload.get("referrer") or "")[:255] or None,
            session_id=(payload.get("session_id") or "")[:64] or None,
            meta=payload.get("meta") or None,
            user_agent=request.headers.get("user-agent", "")[:255] or None,
            ip_hash=_ip_hash(request),
        )
    )
    await db.commit()
    return None


@router.get("/summary")
async def summary(
    days: int = 7, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
):
    since = datetime.now(UTC) - timedelta(days=days)
    type_counts = (
        await db.execute(
            select(AnalyticsEvent.event, func.count())
            .where(AnalyticsEvent.created_at >= since)
            .group_by(AnalyticsEvent.event)
        )
    ).all()
    unique_visitors = (
        await db.execute(
            select(func.count(func.distinct(AnalyticsEvent.ip_hash))).where(
                AnalyticsEvent.created_at >= since
            )
        )
    ).scalar_one()
    top_pages = (
        await db.execute(
            select(AnalyticsEvent.path, func.count())
            .where((AnalyticsEvent.event == "page_view") & (AnalyticsEvent.created_at >= since))
            .group_by(AnalyticsEvent.path)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()
    return {
        "days": days,
        "unique_visitors": int(unique_visitors or 0),
        "event_counts": [{"event": e, "count": int(n)} for e, n in type_counts],
        "top_pages": [{"path": p or "/", "count": int(n)} for p, n in top_pages],
    }
