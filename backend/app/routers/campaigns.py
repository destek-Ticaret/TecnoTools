"""Bülten kampanyaları — admin tarafından toplu HTML email gönderimi.

Senaryo:
1. Admin draft kampanya oluşturur (POST /api/newsletter/campaigns)
2. Gönderimi tetikler (POST /api/newsletter/campaigns/{id}/send)
3. Backend asyncio task ile abonelere throttled gönderir
4. Frontend status'u polling ile takip eder (GET /api/newsletter/campaigns/{id})
"""

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal, get_db
from app.deps import require_admin
from app.models import AuditLog, NewsletterCampaign, NewsletterSubscriber, User
from app.services.email import render_template, send_email

router = APIRouter(prefix="/api/newsletter/campaigns", tags=["newsletter-campaigns"])

THROTTLE_PER_SECOND = 5  # SMTP'yi boğmamak için saniyede 5 email


class CampaignIn(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    html_body: str = Field(min_length=10)


class CampaignOut(BaseModel):
    id: int
    subject: str
    status: str
    total_recipients: int
    sent_count: int
    failed_count: int
    created_at: datetime
    completed_at: datetime | None


def _to_out(c: NewsletterCampaign) -> CampaignOut:
    return CampaignOut(
        id=c.id,
        subject=c.subject,
        status=c.status,
        total_recipients=c.total_recipients,
        sent_count=c.sent_count,
        failed_count=c.failed_count,
        created_at=c.created_at,
        completed_at=c.completed_at,
    )


@router.get("")
async def list_campaigns(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    rows = (
        (
            await db.execute(
                select(NewsletterCampaign).order_by(NewsletterCampaign.id.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [_to_out(r) for r in rows]


@router.post("", response_model=CampaignOut, status_code=201)
async def create_campaign(
    payload: CampaignIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)
):
    c = NewsletterCampaign(
        subject=payload.subject,
        html_body=payload.html_body,
        created_by=user.username,
        status="draft",
    )
    db.add(c)
    db.add(
        AuditLog(
            actor=user.username, action="campaign-add", message=f"Kampanya oluşturuldu: {c.subject}"
        )
    )
    await db.commit()
    await db.refresh(c)
    return _to_out(c)


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(
    campaign_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
):
    c = (
        await db.execute(select(NewsletterCampaign).where(NewsletterCampaign.id == campaign_id))
    ).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    return _to_out(c)


async def _send_campaign_background(campaign_id: int) -> None:
    async with SessionLocal() as db:
        c = (
            await db.execute(select(NewsletterCampaign).where(NewsletterCampaign.id == campaign_id))
        ).scalar_one_or_none()
        if not c or c.status != "sending":
            return
        subs = (await db.execute(select(NewsletterSubscriber))).scalars().all()
        c.total_recipients = len(subs)
        await db.commit()

        for sub in subs:
            html = render_template("campaign.html", body=c.html_body)
            ok = await send_email(to=sub.email, subject=c.subject, html=html)
            if ok:
                c.sent_count += 1
            else:
                c.failed_count += 1
            await asyncio.sleep(1.0 / THROTTLE_PER_SECOND)
            if (c.sent_count + c.failed_count) % 10 == 0:
                await db.commit()

        c.status = "completed"
        c.completed_at = datetime.now(UTC)
        db.add(
            AuditLog(
                actor=c.created_by,
                action="campaign-sent",
                message=f"Kampanya gönderildi: {c.subject} ({c.sent_count}/{c.total_recipients})",
            )
        )
        await db.commit()


@router.post("/{campaign_id}/send", response_model=CampaignOut)
async def send_campaign(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)
):
    c = (
        await db.execute(select(NewsletterCampaign).where(NewsletterCampaign.id == campaign_id))
    ).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    if c.status not in ("draft", "failed"):
        raise HTTPException(status_code=409, detail=f"Kampanya zaten {c.status} durumunda")
    c.status = "sending"
    c.sent_count = 0
    c.failed_count = 0
    await db.commit()
    asyncio.create_task(_send_campaign_background(campaign_id))
    return _to_out(c)


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)
):
    c = (
        await db.execute(select(NewsletterCampaign).where(NewsletterCampaign.id == campaign_id))
    ).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    if c.status == "sending":
        raise HTTPException(status_code=409, detail="Gönderim devam ediyor; bekleyin")
    await db.delete(c)
    await db.commit()
    return None
