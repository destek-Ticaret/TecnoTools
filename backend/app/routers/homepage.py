"""Ana sayfa düzeni — sürükle-bırak ile sıralanan bölümler.

Public:  GET  /api/homepage          → aktif bölümler (sıralı)
Admin:   GET  /api/homepage/admin/all
         POST /api/homepage          (bölüm ekle)
         PUT  /api/homepage/{id}
         POST /api/homepage/reorder   (sürükle-bırak sıralama)
         DELETE /api/homepage/{id}
"""
from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_permission
from app.models import AuditLog, HomepageSection, User
from app.services.events import bus

router = APIRouter(prefix="/api/homepage", tags=["homepage"])

VALID_KINDS = {"hero", "banner_strip", "category_grid", "product_carousel", "blog", "html"}
_can_manage = require_permission("content.homepage")


class SectionIn(BaseModel):
    kind: str
    title: str | None = None
    config: dict | None = None
    sort_order: int = 0
    is_active: bool = True


def _serialize(s: HomepageSection) -> dict:
    return {
        "id": s.id, "kind": s.kind, "title": s.title, "config": s.config or {},
        "sort_order": s.sort_order, "is_active": s.is_active,
    }


@router.get("")
async def list_sections_public(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(HomepageSection)
            .where(HomepageSection.is_active == True)  # noqa: E712
            .order_by(HomepageSection.sort_order, HomepageSection.id)
        )
    ).scalars().all()
    return [_serialize(s) for s in rows]


@router.get("/admin/all")
async def list_sections_admin(db: AsyncSession = Depends(get_db), _: User = Depends(_can_manage)):
    rows = (await db.execute(select(HomepageSection).order_by(HomepageSection.sort_order, HomepageSection.id))).scalars().all()
    return [_serialize(s) for s in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_section(payload: SectionIn, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)):
    if payload.kind not in VALID_KINDS:
        raise HTTPException(status_code=400, detail=f"Geçersiz bölüm türü. İzin verilenler: {', '.join(sorted(VALID_KINDS))}")
    s = HomepageSection(**payload.model_dump())
    db.add(s)
    db.add(AuditLog(actor=user.username, action="homepage-add", message=f"Ana sayfa bölümü eklendi: {payload.kind}"))
    await db.commit()
    await db.refresh(s)
    await bus.publish("homepage_changed", {"id": s.id})
    return _serialize(s)


@router.put("/{section_id}")
async def update_section(section_id: int, payload: SectionIn, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)):
    s = (await db.execute(select(HomepageSection).where(HomepageSection.id == section_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Bölüm bulunamadı")
    if payload.kind not in VALID_KINDS:
        raise HTTPException(status_code=400, detail="Geçersiz bölüm türü")
    for k, v in payload.model_dump().items():
        setattr(s, k, v)
    db.add(AuditLog(actor=user.username, action="homepage-edit", message=f"Ana sayfa bölümü güncellendi (#{s.id})"))
    await db.commit()
    await db.refresh(s)
    await bus.publish("homepage_changed", {"id": s.id})
    return _serialize(s)


@router.post("/reorder")
async def reorder_sections(
    order: list[int] = Body(..., embed=True), db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)
):
    """Sürükle-bırak sonrası id sırasını kaydet — sort_order 0..n."""
    rows = (await db.execute(select(HomepageSection).where(HomepageSection.id.in_(order)))).scalars().all()
    by_id = {s.id: s for s in rows}
    for idx, sid in enumerate(order):
        if sid in by_id:
            by_id[sid].sort_order = idx
    db.add(AuditLog(actor=user.username, action="homepage-reorder", message=f"Ana sayfa düzeni yeniden sıralandı ({len(by_id)} bölüm)"))
    await db.commit()
    await bus.publish("homepage_changed", {"reorder": True})
    return {"ok": True, "count": len(by_id)}


@router.delete("/{section_id}", status_code=204)
async def delete_section(section_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)):
    s = (await db.execute(select(HomepageSection).where(HomepageSection.id == section_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Bölüm bulunamadı")
    await db.delete(s)
    db.add(AuditLog(actor=user.username, action="homepage-delete", message=f"Ana sayfa bölümü silindi (#{section_id})"))
    await db.commit()
    await bus.publish("homepage_changed", {"id": section_id})
    return None
