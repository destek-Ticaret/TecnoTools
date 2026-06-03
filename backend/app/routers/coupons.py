from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_editor
from app.models import AuditLog, Coupon, User
from app.rate_limit import limiter
from app.schemas import CouponIn, CouponOut
from app.services.events import bus

router = APIRouter(prefix="/api/coupons", tags=["coupons"])


@router.get("", response_model=list[CouponOut])
async def list_coupons(db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)):
    return (await db.execute(select(Coupon).order_by(Coupon.id.desc()))).scalars().all()


@router.get("/validate/{code}")
@limiter.limit("20/minute")
async def validate_coupon(request: Request, code: str, db: AsyncSession = Depends(get_db)):
    """Public — checkout'tan önce kupon doğrulama."""
    c = (await db.execute(select(Coupon).where(Coupon.code == code.upper()))).scalar_one_or_none()
    if not c or not c.is_active:
        raise HTTPException(status_code=404, detail="Kupon geçersiz")
    if c.expires_at:
        # SQLite naive datetime → UTC-aware. Postgres zaten aware döner.
        exp = c.expires_at if c.expires_at.tzinfo else c.expires_at.replace(tzinfo=UTC)
        if exp < datetime.now(UTC):
            raise HTTPException(status_code=410, detail="Kuponun süresi dolmuş")
    if c.max_uses is not None and c.used_count >= c.max_uses:
        raise HTTPException(status_code=409, detail="Kupon kullanım limiti dolu")
    return {
        "code": c.code,
        "type": c.type,
        "value": float(c.value),
        "min_order": float(c.min_order),
        "expires_at": c.expires_at,
    }


@router.post("", response_model=CouponOut, status_code=status.HTTP_201_CREATED)
async def create_coupon(
    payload: CouponIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)
):
    payload_dict = payload.model_dump()
    payload_dict["code"] = payload_dict["code"].upper()
    c = Coupon(**payload_dict)
    db.add(c)
    db.add(AuditLog(actor=user.username, action="coupon-add", message=f"Kupon eklendi: {c.code}"))
    await db.commit()
    await db.refresh(c)
    await bus.publish("coupon_created", {"id": c.id, "code": c.code})
    return c


@router.put("/{coupon_id}", response_model=CouponOut)
async def update_coupon(
    coupon_id: int,
    payload: CouponIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    c = (await db.execute(select(Coupon).where(Coupon.id == coupon_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Kupon bulunamadı")
    for k, v in payload.model_dump().items():
        setattr(c, k, v.upper() if k == "code" else v)
    db.add(
        AuditLog(actor=user.username, action="coupon-edit", message=f"Kupon güncellendi: {c.code}")
    )
    await db.commit()
    await db.refresh(c)
    await bus.publish("coupon_updated", {"id": c.id, "code": c.code})
    return c


@router.delete("/{coupon_id}", status_code=204)
async def delete_coupon(
    coupon_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)
):
    c = (await db.execute(select(Coupon).where(Coupon.id == coupon_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Kupon bulunamadı")
    code = c.code
    cid = c.id
    await db.delete(c)
    db.add(AuditLog(actor=user.username, action="coupon-delete", message=f"Kupon silindi: {code}"))
    await db.commit()
    await bus.publish("coupon_deleted", {"id": cid, "code": code})
    return None
