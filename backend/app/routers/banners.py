"""Banner / vitrin görseli yönetimi.

Public:  GET /api/banners?position=hero   → yayında olan banner'lar
Admin:   tam CRUD + sıralama (content.banners izni)
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_permission
from app.models import AuditLog, Banner, User
from app.services.events import bus

router = APIRouter(prefix="/api/banners", tags=["banners"])

VALID_POSITIONS = {"hero", "strip", "sidebar", "popup"}
_can_manage = require_permission("content.banners")


class BannerIn(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    image_url: str
    mobile_image_url: str | None = None
    link_url: str | None = None
    cta_text: str | None = None
    position: str = "hero"
    sort_order: int = 0
    is_active: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None


def _serialize(b: Banner) -> dict:
    return {
        "id": b.id,
        "title": b.title,
        "subtitle": b.subtitle,
        "image_url": b.image_url,
        "mobile_image_url": b.mobile_image_url,
        "link_url": b.link_url,
        "cta_text": b.cta_text,
        "position": b.position,
        "sort_order": b.sort_order,
        "is_active": b.is_active,
        "starts_at": b.starts_at,
        "ends_at": b.ends_at,
    }


@router.get("")
async def list_banners_public(
    position: str | None = Query(None), db: AsyncSession = Depends(get_db)
):
    """Yayında olan banner'lar — aktif + tarih penceresinde olanlar."""
    now = datetime.now(UTC)
    stmt = select(Banner).where(Banner.is_active == True)  # noqa: E712
    if position:
        stmt = stmt.where(Banner.position == position)
    rows = (await db.execute(stmt.order_by(Banner.sort_order, Banner.id))).scalars().all()
    out = []
    for b in rows:
        # SQLite naive datetime döndürür; karşılaştırma için tz ekle
        b_starts = (
            b.starts_at.replace(tzinfo=UTC)
            if b.starts_at and b.starts_at.tzinfo is None
            else b.starts_at
        )
        b_ends = (
            b.ends_at.replace(tzinfo=UTC)
            if b.ends_at and b.ends_at.tzinfo is None
            else b.ends_at
        )
        if b_starts and b_starts > now:
            continue
        if b_ends and b_ends < now:
            continue
        out.append(_serialize(b))
    return out


@router.get("/admin/all")
async def list_banners_admin(db: AsyncSession = Depends(get_db), _: User = Depends(_can_manage)):
    rows = (
        (await db.execute(select(Banner).order_by(Banner.position, Banner.sort_order, Banner.id)))
        .scalars()
        .all()
    )
    return [_serialize(b) for b in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_banner(
    payload: BannerIn, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)
):
    if payload.position not in VALID_POSITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Geçersiz pozisyon. İzin verilenler: {', '.join(VALID_POSITIONS)}",
        )
    b = Banner(**payload.model_dump())
    db.add(b)
    db.add(
        AuditLog(
            actor=user.username,
            action="banner-add",
            message=f"Banner eklendi: {payload.title or payload.image_url}",
        )
    )
    await db.commit()
    await db.refresh(b)
    await bus.publish("banner_changed", {"id": b.id})
    return _serialize(b)


@router.put("/{banner_id}")
async def update_banner(
    banner_id: int,
    payload: BannerIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_can_manage),
):
    b = (await db.execute(select(Banner).where(Banner.id == banner_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Banner bulunamadı")
    if payload.position not in VALID_POSITIONS:
        raise HTTPException(status_code=400, detail="Geçersiz pozisyon")
    for k, v in payload.model_dump().items():
        setattr(b, k, v)
    db.add(
        AuditLog(actor=user.username, action="banner-edit", message=f"Banner güncellendi (#{b.id})")
    )
    await db.commit()
    await db.refresh(b)
    await bus.publish("banner_changed", {"id": b.id})
    return _serialize(b)


@router.post("/reorder")
async def reorder_banners(
    order: list[int] = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_can_manage),
):
    """Verilen id sırasına göre sort_order'ı 0..n yeniden numaralandır."""
    rows = (await db.execute(select(Banner).where(Banner.id.in_(order)))).scalars().all()
    by_id = {b.id: b for b in rows}
    for idx, bid in enumerate(order):
        if bid in by_id:
            by_id[bid].sort_order = idx
    await db.commit()
    return {"ok": True, "count": len(by_id)}


@router.delete("/{banner_id}", status_code=204)
async def delete_banner(
    banner_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)
):
    b = (await db.execute(select(Banner).where(Banner.id == banner_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Banner bulunamadı")
    await db.delete(b)
    db.add(
        AuditLog(
            actor=user.username, action="banner-delete", message=f"Banner silindi (#{banner_id})"
        )
    )
    await db.commit()
    await bus.publish("banner_changed", {"id": banner_id})
    return None
