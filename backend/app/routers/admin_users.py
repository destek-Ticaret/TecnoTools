"""Multi-admin kullanıcı yönetimi.

Sadece `admin` role'üne sahip kullanıcılar erişebilir.
- Birincil kullanıcı (`is_primary=True`) silinemez ve role'ü değiştirilemez.
- Kullanıcı kendi role'ünü değiştiremez (kilitlenmeyi önlemek için).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from app.database import get_db
from app.deps import current_user, require_admin
from app.models import AuditLog, User, UserRole
from app.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_CATALOG,
    ROLE_DEFAULTS,
    effective_permissions,
)
from app.schemas import AdminUserCreate, AdminUserUpdate, ChangePasswordRequest, UserOut
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])

VALID_ROLES = {r.value for r in UserRole}


class PermissionUpdate(BaseModel):
    # {"products.delete": false, "content.blog": true} — rol varsayılanını override eder
    permissions: dict[str, bool]


@router.get("", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    rows = (await db.execute(select(User).order_by(User.is_primary.desc(), User.username))).scalars().all()
    return rows


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: AdminUserCreate, db: AsyncSession = Depends(get_db), me: User = Depends(require_admin)
):
    if payload.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Geçersiz rol. İzin verilenler: {', '.join(VALID_ROLES)}")
    existing = (await db.execute(select(User).where(User.username == payload.username))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Bu kullanıcı adı zaten mevcut")
    new_user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_primary=False,
    )
    db.add(new_user)
    db.add(AuditLog(actor=me.username, action="user-add", message=f"Kullanıcı eklendi: {payload.username} ({payload.role})"))
    await db.commit()
    await db.refresh(new_user)
    return new_user


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int, payload: AdminUserUpdate, db: AsyncSession = Depends(get_db), me: User = Depends(require_admin)
):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")

    if payload.role is not None:
        if target.is_primary:
            raise HTTPException(status_code=403, detail="Birincil kullanıcının rolü değiştirilemez")
        if target.id == me.id:
            raise HTTPException(status_code=403, detail="Kendi rolünüzü değiştiremezsiniz")
        if payload.role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail="Geçersiz rol")
        target.role = payload.role

    if payload.password:
        target.password_hash = hash_password(payload.password)

    if payload.is_active is not None and payload.is_active != target.is_active:
        if target.is_primary:
            raise HTTPException(status_code=403, detail="Birincil kullanıcı devre dışı bırakılamaz")
        if target.id == me.id:
            raise HTTPException(status_code=403, detail="Kendinizi devre dışı bırakamazsınız")
        target.is_active = payload.is_active
        action = "user-enable" if payload.is_active else "user-disable"
        db.add(AuditLog(actor=me.username, action=action, message=f"{target.username} {'etkinleştirildi' if payload.is_active else 'devre dışı bırakıldı'}"))

    db.add(AuditLog(actor=me.username, action="user-edit", message=f"Kullanıcı güncellendi: {target.username}"))
    await db.commit()
    await db.refresh(target)
    return target


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db), me: User = Depends(require_admin)):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    if target.is_primary:
        raise HTTPException(status_code=403, detail="Birincil kullanıcı silinemez")
    if target.id == me.id:
        raise HTTPException(status_code=403, detail="Kendinizi silemezsiniz")
    name = target.username
    await db.delete(target)
    db.add(AuditLog(actor=me.username, action="user-delete", message=f"Kullanıcı silindi: {name}"))
    await db.commit()
    return None


@router.get("/permissions/catalog")
async def permission_catalog(_: User = Depends(require_admin)):
    """İzin kataloğu + rol varsayılanları — UI yetki matrisini bununla çizer."""
    return {
        "catalog": PERMISSION_CATALOG,
        "role_defaults": {role: sorted(perms) for role, perms in ROLE_DEFAULTS.items()},
    }


@router.get("/me/permissions")
async def my_permissions(me: User = Depends(current_user)):
    """Giriş yapan kullanıcının etkin izinleri — UI menü/buton görünürlüğü için."""
    return {"role": me.role, "permissions": sorted(effective_permissions(me))}


@router.get("/{user_id}/permissions")
async def get_user_permissions(
    user_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    return {
        "user_id": target.id,
        "role": target.role,
        "override": target.permissions or {},
        "effective": sorted(effective_permissions(target)),
    }


@router.put("/{user_id}/permissions")
async def set_user_permissions(
    user_id: int, payload: PermissionUpdate, db: AsyncSession = Depends(get_db), me: User = Depends(require_admin)
):
    """Kullanıcının granüler izin override'ını ayarlar.

    ADMIN rolündeki kullanıcılarda override etkisizdir (her zaman tüm izinler).
    Bilinmeyen izin anahtarları yok sayılır.
    """
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    if target.role == UserRole.ADMIN.value:
        raise HTTPException(status_code=400, detail="Admin rolü tüm izinlere sahiptir; override gereksiz")
    clean = {k: bool(v) for k, v in payload.permissions.items() if k in ALL_PERMISSIONS}
    target.permissions = clean or None
    db.add(AuditLog(actor=me.username, action="user-permissions", message=f"{target.username} izinleri güncellendi ({len(clean)} override)"))
    await db.commit()
    return {"ok": True, "override": clean, "effective": sorted(effective_permissions(target))}


@router.post("/me/change-password", status_code=200)
async def change_my_password(
    payload: ChangePasswordRequest, db: AsyncSession = Depends(get_db), me: User = Depends(require_admin)
):
    """Giriş yapan kullanıcı kendi şifresini değiştirir (mevcut şifre doğrulanır)."""
    if not verify_password(payload.current_password, me.password_hash):
        raise HTTPException(status_code=401, detail="Mevcut şifre hatalı")
    me.password_hash = hash_password(payload.new_password)
    db.add(AuditLog(actor=me.username, action="password-change", message="Şifre değiştirildi"))
    await db.commit()
    return {"ok": True}
