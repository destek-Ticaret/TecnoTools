"""KVKK uçları (silme talep yönetimi + çerez izin kaydı).

Public:
  POST /api/privacy/consent           — çerez tercih log kaydı

Admin:
  GET    /api/privacy/deletion-requests           — talepleri listele
  GET    /api/privacy/deletion-requests/{id}      — detay
  POST   /api/privacy/deletion-requests/{id}/cancel  — talep iptal
  POST   /api/privacy/deletion-requests/{id}/run     — admin tetikli icra
                                                     (mailli onay yerine)
  GET    /api/privacy/consent-logs                — son izin kayıtları
"""

import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin
from app.models import (
    AuditLog,
    ConsentLog,
    DataDeletionRequest,
    DataDeletionStatus,
    User,
)
from app.rate_limit import limiter
from app.services.privacy import run_deletion

router = APIRouter(prefix="/api/privacy", tags=["privacy"])

ALLOWED_CATEGORIES = {"essential", "preference", "analytics", "marketing"}


class ConsentIn(BaseModel):
    """Granüler çerez izni — frontend "Çerez tercihleri" modalı buradan POST eder."""

    session_id: str = Field(min_length=4, max_length=64)
    categories: dict[str, bool]
    policy_version: str = Field(default="1.0", max_length=16)


class ConsentLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int | None
    session_id: str | None
    categories: dict
    policy_version: str
    created_at: datetime


class DeletionRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int | None
    email_snapshot: str
    name_snapshot: str | None
    status: str
    reason: str | None
    result: dict | None
    error_message: str | None
    created_at: datetime
    confirmed_at: datetime | None
    completed_at: datetime | None


def _hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


# ── Public: çerez izin kaydı ────────────────────────────────────────────────
@router.post("/consent", status_code=201)
@limiter.limit("30/hour")
async def log_consent(
    request: Request,
    payload: ConsentIn,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None),
    user_agent: str | None = Header(None),
):
    """Çerez tercihini kayda al — KVKK açık rıza ispatı için.

    Kategori sözlüğü beklenen anahtarlar: essential, preference, analytics, marketing.
    Bilinmeyen anahtarlar yok sayılır; essential her zaman true olmalı.
    Customer login varsa otomatik bağlanır (admin token'ları hesaba katılmaz).
    """
    cats = {k: bool(v) for k, v in payload.categories.items() if k in ALLOWED_CATEGORIES}
    if not cats.get("essential", False):
        # Zorunlu çerezler kapalı kabul edilemez — sitenin çalışmasına engel
        cats["essential"] = True

    customer_id: int | None = None
    if authorization and authorization.lower().startswith("bearer "):
        # Müşteri token'ı varsa bağla; admin token'ları için sessizce geç.
        from app.security import decode_customer_token

        try:
            tok = decode_customer_token(authorization.split(" ", 1)[1].strip())
            if tok and tok.get("sub"):
                customer_id = int(tok["sub"])
        except Exception:
            customer_id = None

    client_ip = request.client.host if request.client else None
    db.add(
        ConsentLog(
            customer_id=customer_id,
            session_id=payload.session_id,
            categories=cats,
            policy_version=payload.policy_version,
            user_agent=(user_agent or "")[:255] or None,
            ip_hash=_hash_ip(client_ip),
        )
    )
    await db.commit()
    return {"ok": True, "categories": cats}


# ── Admin: silme talepleri ──────────────────────────────────────────────────
@router.get("/deletion-requests", response_model=list[DeletionRequestOut])
async def list_deletion_requests(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
    status: str | None = None,
):
    q = select(DataDeletionRequest).order_by(DataDeletionRequest.id.desc())
    if status:
        q = q.where(DataDeletionRequest.status == status)
    rows = (await db.execute(q.limit(500))).scalars().all()
    return rows


@router.get("/deletion-requests/{req_id}", response_model=DeletionRequestOut)
async def get_deletion_request(
    req_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    row = (
        await db.execute(select(DataDeletionRequest).where(DataDeletionRequest.id == req_id))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Talep bulunamadı")
    return row


@router.post("/deletion-requests/{req_id}/cancel", response_model=DeletionRequestOut)
async def cancel_deletion(
    req_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    row = (
        await db.execute(select(DataDeletionRequest).where(DataDeletionRequest.id == req_id))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Talep bulunamadı")
    if row.status == DataDeletionStatus.COMPLETED.value:
        raise HTTPException(status_code=400, detail="Tamamlanmış talep iptal edilemez")
    row.status = DataDeletionStatus.CANCELLED.value
    row.token_hash = None
    db.add(
        AuditLog(
            actor=user.username,
            action="data-deletion-cancel",
            message=f"Silme talebi iptal: #{req_id}",
        )
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/deletion-requests/{req_id}/run", response_model=DeletionRequestOut)
async def run_deletion_admin(
    req_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Admin tetikli silme. Müşteri mailden onay vermediği durumda
    (örn. KVKK Kurumu yazısı) admin elle çalıştırabilir.
    """
    row = (
        await db.execute(select(DataDeletionRequest).where(DataDeletionRequest.id == req_id))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Talep bulunamadı")
    if row.status == DataDeletionStatus.COMPLETED.value:
        raise HTTPException(status_code=400, detail="Zaten tamamlanmış")
    row.status = DataDeletionStatus.CONFIRMED.value
    row.confirmed_at = datetime.now(UTC)
    row.token_hash = None
    await db.commit()
    db.add(
        AuditLog(
            actor=user.username,
            action="data-deletion-admin-run",
            message=f"Silme admin'ce başlatıldı: #{req_id}",
        )
    )
    await db.commit()
    try:
        await run_deletion(db, row)
    except Exception as e:
        row.error_message = str(e)[:500]
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e))
    await db.refresh(row)
    return row


# ── Admin: consent log görüntüleme ──────────────────────────────────────────
@router.get("/consent-logs", response_model=list[ConsentLogOut])
async def list_consent_logs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
    customer_id: int | None = None,
    session_id: str | None = None,
):
    q = select(ConsentLog).order_by(ConsentLog.id.desc())
    if customer_id:
        q = q.where(ConsentLog.customer_id == customer_id)
    if session_id:
        q = q.where(ConsentLog.session_id == session_id)
    return (await db.execute(q.limit(500))).scalars().all()
