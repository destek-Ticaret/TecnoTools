"""CMS statik sayfalar — admin'in oluşturduğu içerik sayfaları (hakkımızda, SSS...).

Public:  GET /api/pages              → footer'da gösterilecek yayınlı sayfalar (özet)
         GET /api/pages/{slug}       → tek sayfa
Admin:   GET /api/pages/admin/all + CRUD  (content.pages izni)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_permission
from app.models import AuditLog, CmsPage, User
from app.services.text_utils import slugify

router = APIRouter(prefix="/api/pages", tags=["pages"])
_can_manage = require_permission("content.pages")


class PageIn(BaseModel):
    title: str
    slug: str | None = None
    body: str
    is_published: bool = True
    show_in_footer: bool = False
    sort_order: int = 0
    meta_title: str | None = None
    meta_description: str | None = None


def _serialize(p: CmsPage, full: bool = False) -> dict:
    out = {
        "id": p.id,
        "slug": p.slug,
        "title": p.title,
        "is_published": p.is_published,
        "show_in_footer": p.show_in_footer,
        "sort_order": p.sort_order,
    }
    if full:
        out.update(
            {
                "body": p.body,
                "meta_title": p.meta_title,
                "meta_description": p.meta_description,
                "updated_at": p.updated_at,
            }
        )
    return out


async def _unique_slug(db: AsyncSession, base: str, exclude_id: int | None = None) -> str:
    base = base or "sayfa"
    slug, n = base, 1
    while True:
        row = (await db.execute(select(CmsPage).where(CmsPage.slug == slug))).scalar_one_or_none()
        if not row or row.id == exclude_id:
            return slug
        n += 1
        slug = f"{base}-{n}"


@router.get("")
async def list_pages_public(db: AsyncSession = Depends(get_db)):
    """Footer menüsü için yayınlı sayfalar."""
    rows = (
        (
            await db.execute(
                select(CmsPage)
                .where(CmsPage.is_published == True, CmsPage.show_in_footer == True)  # noqa: E712
                .order_by(CmsPage.sort_order, CmsPage.id)
            )
        )
        .scalars()
        .all()
    )
    return [_serialize(p) for p in rows]


@router.get("/admin/all")
async def list_pages_admin(db: AsyncSession = Depends(get_db), _: User = Depends(_can_manage)):
    rows = (
        (await db.execute(select(CmsPage).order_by(CmsPage.sort_order, CmsPage.id))).scalars().all()
    )
    return [_serialize(p, full=True) for p in rows]


@router.get("/{slug}")
async def get_page_public(slug: str, db: AsyncSession = Depends(get_db)):
    p = (await db.execute(select(CmsPage).where(CmsPage.slug == slug))).scalar_one_or_none()
    if not p or not p.is_published:
        raise HTTPException(status_code=404, detail="Sayfa bulunamadı")
    return _serialize(p, full=True)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_page(
    payload: PageIn, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)
):
    slug = await _unique_slug(db, slugify(payload.slug or payload.title))
    p = CmsPage(
        slug=slug,
        title=payload.title,
        body=payload.body,
        is_published=payload.is_published,
        show_in_footer=payload.show_in_footer,
        sort_order=payload.sort_order,
        meta_title=payload.meta_title,
        meta_description=payload.meta_description,
    )
    db.add(p)
    db.add(
        AuditLog(actor=user.username, action="page-add", message=f"Sayfa eklendi: {payload.title}")
    )
    await db.commit()
    await db.refresh(p)
    return _serialize(p, full=True)


@router.put("/{page_id}")
async def update_page(
    page_id: int,
    payload: PageIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_can_manage),
):
    p = (await db.execute(select(CmsPage).where(CmsPage.id == page_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Sayfa bulunamadı")
    if payload.slug and slugify(payload.slug) != p.slug:
        p.slug = await _unique_slug(db, slugify(payload.slug), exclude_id=p.id)
    p.title = payload.title
    p.body = payload.body
    p.is_published = payload.is_published
    p.show_in_footer = payload.show_in_footer
    p.sort_order = payload.sort_order
    p.meta_title = payload.meta_title
    p.meta_description = payload.meta_description
    db.add(
        AuditLog(
            actor=user.username,
            action="page-edit",
            message=f"Sayfa güncellendi: {p.title} (#{p.id})",
        )
    )
    await db.commit()
    await db.refresh(p)
    return _serialize(p, full=True)


@router.delete("/{page_id}", status_code=204)
async def delete_page(
    page_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)
):
    p = (await db.execute(select(CmsPage).where(CmsPage.id == page_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Sayfa bulunamadı")
    title = p.title
    await db.delete(p)
    db.add(AuditLog(actor=user.username, action="page-delete", message=f"Sayfa silindi: {title}"))
    await db.commit()
    return None
