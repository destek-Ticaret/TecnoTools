"""Ürün soru-cevap (Q&A) endpoint'leri.

Public:
  POST /api/products/{pid}/questions  — yeni soru (moderasyon + cevap bekler)
  GET  /api/products/{pid}/questions  — yayınlanmış + cevaplanmış sorular

Admin:
  GET    /api/admin/questions               — tüm sorular (filtre: ?published= ?answered=)
  PATCH  /api/admin/questions/{id}          — cevapla / yayınla
  DELETE /api/admin/questions/{id}          — sil
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_editor
from app.models import AuditLog, Product, ProductQuestion, User
from app.rate_limit import limiter
from app.services.events import bus

router = APIRouter(tags=["questions"])


class QuestionIn(BaseModel):
    customer_name: str = Field(min_length=2, max_length=255)
    customer_email: EmailStr | None = None
    question: str = Field(min_length=5, max_length=2000)
    # Honeypot — bot doldurursa reddet
    website: str | None = Field(default=None, max_length=0)


class QuestionOut(BaseModel):
    id: int
    product_id: int
    customer_name: str
    question: str
    answer: str | None
    is_published: bool
    created_at: object
    answered_at: object | None = None


def _to_out(q: ProductQuestion) -> QuestionOut:
    return QuestionOut(
        id=q.id, product_id=q.product_id, customer_name=q.customer_name,
        question=q.question, answer=q.answer, is_published=q.is_published,
        created_at=q.created_at, answered_at=q.answered_at,
    )


@router.post("/api/products/{product_id}/questions", response_model=QuestionOut, status_code=201)
@limiter.limit("5/minute")
async def create_question(
    request: Request, product_id: int, payload: QuestionIn = Body(...), db: AsyncSession = Depends(get_db)
):
    if payload.website:
        raise HTTPException(status_code=400, detail="invalid")
    product = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    q = ProductQuestion(
        product_id=product_id,
        customer_name=payload.customer_name,
        customer_email=payload.customer_email,
        question=payload.question,
        is_published=False,
    )
    db.add(q)
    db.add(AuditLog(actor="customer", action="question-create", message=f"Yeni soru: ürün #{product_id}"))
    await db.commit()
    await db.refresh(q)
    await bus.publish("question_created", {"id": q.id, "product_id": product_id})
    return _to_out(q)


@router.get("/api/products/{product_id}/questions", response_model=list[QuestionOut])
async def list_questions_public(product_id: int, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(ProductQuestion)
            .where(
                (ProductQuestion.product_id == product_id)
                & (ProductQuestion.is_published == True)  # noqa: E712
                & (ProductQuestion.answer.isnot(None))
            )
            .order_by(ProductQuestion.id.desc())
        )
    ).scalars().all()
    return [_to_out(q) for q in rows]


# ── Admin ──
@router.get("/api/admin/questions", response_model=list[QuestionOut])
async def list_questions_admin(
    published: bool | None = None,
    answered: bool | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    stmt = select(ProductQuestion).order_by(ProductQuestion.id.desc())
    if published is not None:
        stmt = stmt.where(ProductQuestion.is_published == published)
    if answered is True:
        stmt = stmt.where(ProductQuestion.answer.isnot(None))
    elif answered is False:
        stmt = stmt.where(ProductQuestion.answer.is_(None))
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_out(q) for q in rows]


class QuestionUpdate(BaseModel):
    answer: str | None = None
    is_published: bool | None = None


@router.patch("/api/admin/questions/{question_id}", response_model=QuestionOut)
async def update_question(
    question_id: int,
    payload: QuestionUpdate = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    q = (await db.execute(select(ProductQuestion).where(ProductQuestion.id == question_id))).scalar_one_or_none()
    if not q:
        raise HTTPException(status_code=404, detail="Soru bulunamadı")
    if payload.answer is not None:
        ans = payload.answer.strip()
        q.answer = ans or None
        q.answered_by = user.username if ans else None
        q.answered_at = datetime.now(timezone.utc) if ans else None
    if payload.is_published is not None:
        q.is_published = bool(payload.is_published)
    db.add(AuditLog(actor=user.username, action="question-update", message=f"Soru #{question_id} güncellendi (published={q.is_published})"))
    await db.commit()
    await db.refresh(q)
    return _to_out(q)


@router.delete("/api/admin/questions/{question_id}", status_code=204)
async def delete_question(
    question_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)
):
    q = (await db.execute(select(ProductQuestion).where(ProductQuestion.id == question_id))).scalar_one_or_none()
    if not q:
        raise HTTPException(status_code=404, detail="Soru bulunamadı")
    await db.delete(q)
    db.add(AuditLog(actor=user.username, action="question-delete", message=f"Soru silindi #{question_id}"))
    await db.commit()
    return None
