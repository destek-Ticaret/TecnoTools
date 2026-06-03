"""Otomatik fiyatlandırma kuralları — admin (pricing.manage izni).

GET    /api/pricing-rules
POST   /api/pricing-rules
PUT    /api/pricing-rules/{id}
DELETE /api/pricing-rules/{id}
POST   /api/pricing-rules/{id}/preview   → dry-run, etkiyi göster
POST   /api/pricing-rules/{id}/apply     → fiyatları kalıcı uygula
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_permission
from app.models import AuditLog, PricingRule, User
from app.services import pricing
from app.services.events import bus

router = APIRouter(prefix="/api/pricing-rules", tags=["pricing-rules"])
_can_manage = require_permission("pricing.manage")

VALID_SCOPES = {"all", "category", "product"}
VALID_STRATEGIES = {"percent", "fixed", "margin", "round_99"}


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scope_type: str = "all"
    scope_id: int | None = None
    strategy: str = "percent"
    value: float = 0
    min_price: float | None = None
    max_price: float | None = None
    only_in_stock: bool = False
    priority: int = 0
    is_active: bool = True


def _serialize(r: PricingRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "scope_type": r.scope_type,
        "scope_id": r.scope_id,
        "strategy": r.strategy,
        "value": float(r.value or 0),
        "min_price": float(r.min_price) if r.min_price is not None else None,
        "max_price": float(r.max_price) if r.max_price is not None else None,
        "only_in_stock": r.only_in_stock,
        "priority": r.priority,
        "is_active": r.is_active,
        "last_applied_at": r.last_applied_at,
        "last_affected": r.last_affected,
    }


def _validate(payload: RuleIn) -> None:
    if payload.scope_type not in VALID_SCOPES:
        raise HTTPException(
            status_code=400, detail=f"Geçersiz kapsam. İzin verilenler: {', '.join(VALID_SCOPES)}"
        )
    if payload.strategy not in VALID_STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"Geçersiz strateji. İzin verilenler: {', '.join(VALID_STRATEGIES)}",
        )
    if payload.scope_type in ("category", "product") and not payload.scope_id:
        raise HTTPException(status_code=400, detail="Bu kapsam için scope_id zorunlu")


async def _get(db: AsyncSession, rule_id: int) -> PricingRule:
    r = (
        await db.execute(select(PricingRule).where(PricingRule.id == rule_id))
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Kural bulunamadı")
    return r


@router.get("")
async def list_rules(db: AsyncSession = Depends(get_db), _: User = Depends(_can_manage)):
    rows = (
        (await db.execute(select(PricingRule).order_by(PricingRule.priority, PricingRule.id)))
        .scalars()
        .all()
    )
    return [_serialize(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: RuleIn, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)
):
    _validate(payload)
    r = PricingRule(**payload.model_dump())
    db.add(r)
    db.add(
        AuditLog(
            actor=user.username,
            action="pricing-rule-add",
            message=f"Fiyat kuralı eklendi: {payload.name}",
        )
    )
    await db.commit()
    await db.refresh(r)
    return _serialize(r)


@router.put("/{rule_id}")
async def update_rule(
    rule_id: int,
    payload: RuleIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_can_manage),
):
    _validate(payload)
    r = await _get(db, rule_id)
    for k, v in payload.model_dump().items():
        setattr(r, k, v)
    db.add(
        AuditLog(
            actor=user.username,
            action="pricing-rule-edit",
            message=f"Fiyat kuralı güncellendi: {r.name} (#{r.id})",
        )
    )
    await db.commit()
    await db.refresh(r)
    return _serialize(r)


@router.post("/{rule_id}/preview")
async def preview(rule_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(_can_manage)):
    r = await _get(db, rule_id)
    return await pricing.preview_rule(db, r)


@router.post("/{rule_id}/apply")
async def apply(
    rule_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)
):
    r = await _get(db, rule_id)
    result = await pricing.apply_rule(db, r, actor=user.username)
    db.add(
        AuditLog(
            actor=user.username,
            action="pricing-rule-apply",
            message=f"Fiyat kuralı uygulandı: {r.name} → {result['affected']} ürün güncellendi",
        )
    )
    await db.commit()
    await bus.publish("prices_updated", {"rule_id": r.id, "affected": result["affected"]})
    return result


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(_can_manage)
):
    r = await _get(db, rule_id)
    name = r.name
    await db.delete(r)
    db.add(
        AuditLog(
            actor=user.username,
            action="pricing-rule-delete",
            message=f"Fiyat kuralı silindi: {name}",
        )
    )
    await db.commit()
    return None
