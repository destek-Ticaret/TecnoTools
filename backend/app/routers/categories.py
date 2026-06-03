from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_editor
from app.models import AuditLog, Category, User
from app.schemas import CategoryIn, CategoryOut
from app.services.events import bus

router = APIRouter(prefix="/api/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
async def list_categories(db: AsyncSession = Depends(get_db)):
    return (
        (await db.execute(select(Category).order_by(Category.sort_order, Category.name)))
        .scalars()
        .all()
    )


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)
):
    existing = (
        await db.execute(select(Category).where(Category.name == payload.name))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Kategori zaten mevcut")
    c = Category(**payload.model_dump())
    db.add(c)
    db.add(
        AuditLog(actor=user.username, action="category-add", message=f"Kategori eklendi: {c.name}")
    )
    await db.commit()
    await db.refresh(c)
    await bus.publish("category_created", {"id": c.id, "name": c.name})
    return c


@router.put("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: int,
    payload: CategoryIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    """Kategori yeniden adlandırma + sıra güncelleme.

    Yeni ad başka bir kategoride kullanılıyorsa 409 döner.
    """
    c = (await db.execute(select(Category).where(Category.id == category_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Kategori bulunamadı")
    if payload.name != c.name:
        clash = (
            await db.execute(select(Category).where(Category.name == payload.name))
        ).scalar_one_or_none()
        if clash and clash.id != category_id:
            raise HTTPException(status_code=409, detail="Bu adda bir kategori zaten var")
    old_name = c.name
    c.name = payload.name
    c.sort_order = payload.sort_order
    db.add(
        AuditLog(
            actor=user.username,
            action="category-edit",
            message=f"Kategori güncellendi: {old_name} → {c.name}",
        )
    )
    await db.commit()
    await db.refresh(c)
    await bus.publish("category_updated", {"id": c.id, "name": c.name})
    return c


@router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_editor)
):
    c = (await db.execute(select(Category).where(Category.id == category_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Kategori bulunamadı")
    name = c.name
    cid = c.id
    await db.delete(c)
    db.add(
        AuditLog(actor=user.username, action="category-delete", message=f"Kategori silindi: {name}")
    )
    await db.commit()
    await bus.publish("category_deleted", {"id": cid, "name": name})
    return None
