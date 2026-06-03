"""Ürün yorumları endpoint'leri.

Public:
  POST /api/products/{pid}/reviews  — yeni yorum (admin onayı bekler)
  GET  /api/products/{pid}/reviews  — sadece onaylı yorumlar

Admin:
  GET    /api/admin/reviews           — tüm yorumlar (filtre: ?approved=)
  PATCH  /api/admin/reviews/{id}      — onaylı/onaysız yap
  DELETE /api/admin/reviews/{id}     — sil

Onaylanan yorum, ürünün rating ortalamasını ve review_count'ünü günceller.
"""
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_editor
from app.models import AuditLog, Product, ProductReview, User
from app.rate_limit import limiter
from app.services.events import bus

router = APIRouter(tags=["reviews"])


class ReviewIn(BaseModel):
    customer_name: str = Field(min_length=2, max_length=255)
    customer_email: EmailStr | None = None
    rating: int = Field(ge=1, le=5)
    title: str | None = Field(default=None, max_length=255)
    body: str = Field(min_length=10, max_length=4000)
    order_no: str | None = None
    # Honeypot — bot doldurursa reddet
    website: str | None = Field(default=None, max_length=0)


class ReviewOut(BaseModel):
    id: int
    product_id: int
    customer_name: str
    rating: int
    title: str | None
    body: str
    is_approved: bool
    created_at: object
    verified_purchase: bool = False


async def _recalc_product_rating(db: AsyncSession, product_id: int) -> None:
    stats = (
        await db.execute(
            select(func.avg(ProductReview.rating), func.count())
            .where((ProductReview.product_id == product_id) & (ProductReview.is_approved == True))  # noqa: E712
        )
    ).one()
    avg = float(stats[0] or 0)
    cnt = int(stats[1] or 0)
    p = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if p:
        p.rating = round(avg, 2)
        p.review_count = cnt


@router.post("/api/products/{product_id}/reviews", response_model=ReviewOut, status_code=201)
@limiter.limit("5/minute")
async def create_review(
    request: Request, product_id: int, payload: ReviewIn = Body(...), db: AsyncSession = Depends(get_db)
):
    # Honeypot
    if payload.website:
        raise HTTPException(status_code=400, detail="invalid")
    product = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    r = ProductReview(
        product_id=product_id,
        customer_name=payload.customer_name,
        customer_email=payload.customer_email,
        rating=payload.rating,
        title=payload.title,
        body=payload.body,
        order_no=payload.order_no,
        is_approved=False,
    )
    db.add(r)
    db.add(AuditLog(actor="customer", action="review-create", message=f"Yeni yorum: ürün #{product_id} ({payload.rating}★)"))
    await db.commit()
    await db.refresh(r)
    return ReviewOut(
        id=r.id, product_id=r.product_id, customer_name=r.customer_name, rating=r.rating,
        title=r.title, body=r.body, is_approved=r.is_approved, created_at=r.created_at,
        verified_purchase=bool(r.order_no),
    )


@router.get("/api/products/{product_id}/reviews", response_model=list[ReviewOut])
async def list_reviews_public(product_id: int, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(ProductReview)
            .where((ProductReview.product_id == product_id) & (ProductReview.is_approved == True))  # noqa: E712
            .order_by(ProductReview.id.desc())
        )
    ).scalars().all()
    return [
        ReviewOut(
            id=r.id, product_id=r.product_id, customer_name=r.customer_name, rating=r.rating,
            title=r.title, body=r.body, is_approved=r.is_approved, created_at=r.created_at,
            verified_purchase=bool(r.order_no),
        )
        for r in rows
    ]


# ── Admin ──
@router.get("/api/admin/reviews", response_model=list[ReviewOut])
async def list_reviews_admin(
    approved: bool | None = None, db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)
):
    stmt = select(ProductReview).order_by(ProductReview.id.desc())
    if approved is not None:
        stmt = stmt.where(ProductReview.is_approved == approved)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        ReviewOut(
            id=r.id, product_id=r.product_id, customer_name=r.customer_name, rating=r.rating,
            title=r.title, body=r.body, is_approved=r.is_approved, created_at=r.created_at,
            verified_purchase=bool(r.order_no),
        )
        for r in rows
    ]


@router.patch("/api/admin/reviews/{review_id}")
async def update_review(
    review_id: int, payload: dict = Body(...), db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)
):
    r = (await db.execute(select(ProductReview).where(ProductReview.id == review_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Yorum bulunamadı")
    was_approved = r.is_approved
    if "is_approved" in payload:
        r.is_approved = bool(payload["is_approved"])
    await db.flush()
    await _recalc_product_rating(db, r.product_id)
    db.add(AuditLog(actor=user.username, action="review-update", message=f"Yorum #{review_id} → approved={r.is_approved}"))
    await db.commit()
    if r.is_approved != was_approved:
        await bus.publish(
            "review_approved" if r.is_approved else "review_unapproved",
            {"id": r.id, "product_id": r.product_id, "rating": r.rating},
        )
    return {"ok": True, "is_approved": r.is_approved}


@router.delete("/api/admin/reviews/{review_id}", status_code=204)
async def delete_review(review_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)):
    r = (await db.execute(select(ProductReview).where(ProductReview.id == review_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Yorum bulunamadı")
    pid = r.product_id
    was_approved = r.is_approved
    await db.delete(r)
    await db.flush()
    await _recalc_product_rating(db, pid)
    db.add(AuditLog(actor=user.username, action="review-delete", message=f"Yorum silindi #{review_id}"))
    await db.commit()
    if was_approved:
        await bus.publish("review_deleted", {"id": review_id, "product_id": pid})
    return None
