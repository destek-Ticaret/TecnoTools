"""Blog / haber modülü — SEO içeriği için CMS.

Public:  GET /api/blog                 → yayınlanmış yazılar (özet)
         GET /api/blog/{slug}          → tek yazı (view_count artar)
Admin:   GET /api/blog/admin/all
         POST /api/blog, PUT /api/blog/{id}, DELETE /api/blog/{id}
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_permission
from app.models import AuditLog, BlogPost, User
from app.services.text_utils import slugify

router = APIRouter(prefix="/api/blog", tags=["blog"])
_can_manage = require_permission("content.blog")


class BlogIn(BaseModel):
    title: str
    slug: str | None = None
    excerpt: str | None = None
    body: str
    cover_image: str | None = None
    tags: list[str] | None = None
    is_published: bool = False
    meta_title: str | None = None
    meta_description: str | None = None


def _serialize(p: BlogPost, full: bool = False) -> dict:
    out = {
        "id": p.id, "slug": p.slug, "title": p.title, "excerpt": p.excerpt,
        "cover_image": p.cover_image, "tags": p.tags or [], "author": p.author,
        "is_published": p.is_published, "view_count": p.view_count,
        "published_at": p.published_at, "created_at": p.created_at,
    }
    if full:
        out.update({"body": p.body, "meta_title": p.meta_title, "meta_description": p.meta_description, "updated_at": p.updated_at})
    return out


async def _unique_slug(db: AsyncSession, base: str, exclude_id: int | None = None) -> str:
    """Slug çakışmasını -2, -3 ekiyle çöz."""
    base = base or "yazi"
    slug = base
    n = 1
    while True:
        row = (await db.execute(select(BlogPost).where(BlogPost.slug == slug))).scalar_one_or_none()
        if not row or row.id == exclude_id:
            return slug
        n += 1
        slug = f"{base}-{n}"


@router.get("")
async def list_blog_public(
    tag: str | None = Query(None), limit: int = Query(50, ge=1, le=100), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(
            select(BlogPost)
            .where(BlogPost.is_published == True)  # noqa: E712
            .order_by(BlogPost.published_at.desc().nullslast(), BlogPost.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    out = [_serialize(p) for p in rows]
    if tag:
        out = [p for p in out if tag in (p["tags"] or [])]
    return out


@router.get("/admin/all")
async def list_blog_admin(db: AsyncSession = Depends(get_db), _: User = Depends(_can_manage)):
    rows = (await db.execute(select(BlogPost).order_by(BlogPost.id.desc()))).scalars().all()
    return [_serialize(p, full=True) for p in rows]


@router.get("/{slug}")
async def get_blog_public(slug: str, db: AsyncSession = Depends(get_db)):
    p = (await db.execute(select(BlogPost).where(BlogPost.slug == slug))).scalar_one_or_none()
    if not p or not p.is_published:
        raise HTTPException(status_code=404, detail="Yazı bulunamadı")
    p.view_count = (p.view_count or 0) + 1
    # commit, ORM nesnesini expire eder; yanıtı commit'ten önce hazırla.
    data = _serialize(p, full=True)
    await db.commit()
    return data


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_blog(payload: BlogIn, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)):
    base = slugify(payload.slug or payload.title)
    slug = await _unique_slug(db, base)
    p = BlogPost(
        slug=slug, title=payload.title, excerpt=payload.excerpt, body=payload.body,
        cover_image=payload.cover_image, tags=payload.tags, author=user.username,
        is_published=payload.is_published, meta_title=payload.meta_title,
        meta_description=payload.meta_description,
        published_at=datetime.now(timezone.utc) if payload.is_published else None,
    )
    db.add(p)
    db.add(AuditLog(actor=user.username, action="blog-add", message=f"Blog yazısı eklendi: {payload.title}"))
    await db.commit()
    await db.refresh(p)
    return _serialize(p, full=True)


@router.put("/{post_id}")
async def update_blog(post_id: int, payload: BlogIn, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)):
    p = (await db.execute(select(BlogPost).where(BlogPost.id == post_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Yazı bulunamadı")
    was_published = p.is_published
    if payload.slug and slugify(payload.slug) != p.slug:
        p.slug = await _unique_slug(db, slugify(payload.slug), exclude_id=p.id)
    p.title = payload.title
    p.excerpt = payload.excerpt
    p.body = payload.body
    p.cover_image = payload.cover_image
    p.tags = payload.tags
    p.is_published = payload.is_published
    p.meta_title = payload.meta_title
    p.meta_description = payload.meta_description
    # İlk kez yayınlanıyorsa published_at damgala
    if payload.is_published and not was_published and not p.published_at:
        p.published_at = datetime.now(timezone.utc)
    db.add(AuditLog(actor=user.username, action="blog-edit", message=f"Blog yazısı güncellendi: {p.title} (#{p.id})"))
    await db.commit()
    await db.refresh(p)
    return _serialize(p, full=True)


@router.delete("/{post_id}", status_code=204)
async def delete_blog(post_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)):
    p = (await db.execute(select(BlogPost).where(BlogPost.id == post_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Yazı bulunamadı")
    title = p.title
    await db.delete(p)
    db.add(AuditLog(actor=user.username, action="blog-delete", message=f"Blog yazısı silindi: {title}"))
    await db.commit()
    return None
