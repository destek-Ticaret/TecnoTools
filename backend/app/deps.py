from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Customer, User, UserRole
from app.security import decode_access_token, decode_customer_token


async def current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Auth gerekli")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_access_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token geçersiz")
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token geçersiz")
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Kullanıcı bulunamadı")
    return user


def require_role(*allowed: UserRole):
    allowed_values = {r.value for r in allowed}

    async def _check(user: User = Depends(current_user)) -> User:
        if user.role not in allowed_values:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Yetkin yok")
        return user

    return _check


require_admin = require_role(UserRole.ADMIN)
require_editor = require_role(UserRole.ADMIN, UserRole.EDITOR)


def require_permission(permission: str):
    """Granüler yetki kontrolü. Rol varsayılanı + kullanıcı override'ına bakar.

    ADMIN her zaman geçer. Diğer roller için `app.permissions` çözümlemesi
    kullanılır; izin yoksa 403 döner.
    """
    from app.permissions import has_permission

    async def _check(user: User = Depends(current_user)) -> User:
        if not has_permission(user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Bu işlem için yetkin yok ({permission})",
            )
        return user

    return _check


async def current_customer(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> Customer:
    """Aktif müşteri üyelik token'ı doğrular, Customer döner.

    Admin token'larını kabul etmez (type='customer_access' zorunlu).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Giriş gerekli")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_customer_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token geçersiz")
    try:
        customer_id = int(payload.get("sub") or 0)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token geçersiz"
        ) from None
    if not customer_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token geçersiz")
    customer = (
        await db.execute(select(Customer).where(Customer.id == customer_id))
    ).scalar_one_or_none()
    if not customer or not customer.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Müşteri bulunamadı")
    if not customer.password_hash:
        # Pasif kayıt — üye olmamış, login yapamaz
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Üyelik aktif değil")
    return customer
