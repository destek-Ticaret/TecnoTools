import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import current_user
from app.models import PasswordResetToken, RefreshToken, User
from app.rate_limit import limiter
from app.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenPair,
    TwoFADisableRequest,
    TwoFASetupRequest,
    TwoFAStatus,
    UserOut,
)
from app.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh,
    verify_password,
)
from app.services.email import send_password_reset

settings_cfg = get_settings()
RESET_TOKEN_TTL_MINUTES = 30

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _as_utc(dt: datetime) -> datetime:
    """SQLite naive datetimes → UTC-aware. Postgres timestamps zaten aware."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


@router.post("/login", response_model=TokenPair)
@limiter.limit("10/minute")
async def login(request: Request, payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == payload.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Kullanıcı adı veya şifre hatalı"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Hesabınız devre dışı bırakılmış"
        )

    # TOTP — sadece primary admin için zorunlu (eğer secret kayıtlıysa)
    if user.totp_secret:
        if not payload.totp_code:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="2FA kodu gerekli")
        from app.services.totp import verify_totp

        matched_step = verify_totp(payload.totp_code, user.totp_secret, min_step=user.totp_last_step)
        if matched_step is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="2FA kodu hatalı")
        user.totp_last_step = matched_step

    access = create_access_token(user.username, user.role)
    raw_refresh, refresh_hash, expiry = create_refresh_token()
    db.add(RefreshToken(user_id=user.id, token_hash=refresh_hash, expires_at=expiry))
    await db.commit()
    return TokenPair(access_token=access, refresh_token=raw_refresh)


@router.post("/refresh", response_model=TokenPair)
@limiter.limit("30/minute")
async def refresh(request: Request, payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    digest = hash_refresh(payload.refresh_token)
    now = datetime.now(UTC)
    # Atomik rotate: satır yalnızca HÂLÂ geçerliyse (revoked_at IS NULL) iptal edilir.
    # Aynı token'la eşzamanlı iki istek gelirse (çalıntı token + gerçek kullanıcı
    # yarışı), DB bu UPDATE'i yalnızca birine "kazandırır" — iki ayrı geçerli
    # oturumun türemesi (token forking) böylece imkânsız hâle gelir.
    rotated = (
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == digest, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
            .returning(RefreshToken.user_id, RefreshToken.expires_at)
        )
    ).first()

    if rotated is None:
        # Token yok YA DA daha önce rotate edilmiş (replay!). Rotate edilmiş bir
        # refresh token'ın tekrar sunulması çalınmış bir oturumun güçlü işaretidir
        # — kullanıcının tüm aktif token'larını iptal ederiz (mass revocation).
        existing = (
            await db.execute(select(RefreshToken).where(RefreshToken.token_hash == digest))
        ).scalar_one_or_none()
        if existing is not None and existing.revoked_at is not None:
            await db.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == existing.user_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token geçersiz"
        )

    user_id, expires_at = rotated
    if _as_utc(expires_at) < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token geçersiz"
        )

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Kullanıcı bulunamadı")

    raw_refresh, refresh_hash, expiry = create_refresh_token()
    db.add(RefreshToken(user_id=user.id, token_hash=refresh_hash, expires_at=expiry))
    await db.commit()
    return TokenPair(
        access_token=create_access_token(user.username, user.role), refresh_token=raw_refresh
    )


@router.post("/logout", status_code=204)
async def logout(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    digest = hash_refresh(payload.refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == digest))
    token_row = result.scalar_one_or_none()
    if token_row and not token_row.revoked_at:
        token_row.revoked_at = datetime.now(UTC)
        await db.commit()
    return None


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return user


@router.get("/2fa/status", response_model=TwoFAStatus)
async def twofa_status(user: User = Depends(current_user)):
    return TwoFAStatus(enabled=bool(user.totp_secret))


@router.post("/2fa/setup", status_code=200)
@limiter.limit("10/minute")
async def twofa_setup(
    request: Request,
    payload: TwoFASetupRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    from app.services.totp import verify_totp

    matched_step = verify_totp(payload.code, payload.secret)
    if matched_step is None:
        raise HTTPException(status_code=400, detail="Doğrulama kodu hatalı")
    user.totp_secret = payload.secret
    user.totp_last_step = matched_step
    await db.commit()
    return {"ok": True}


@router.post("/2fa/disable", status_code=200)
@limiter.limit("10/minute")
async def twofa_disable(
    request: Request,
    payload: TwoFADisableRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    if not user.totp_secret:
        raise HTTPException(status_code=400, detail="2FA zaten devre dışı")
    from app.services.totp import verify_totp

    matched_step = verify_totp(payload.code, user.totp_secret, min_step=user.totp_last_step)
    if matched_step is None:
        raise HTTPException(status_code=401, detail="2FA kodu hatalı")
    user.totp_secret = None
    user.totp_last_step = None
    await db.commit()
    return {"ok": True}


@router.post("/forgot-password", status_code=200)
@limiter.limit("3/minute")
async def forgot_password(
    request: Request, payload: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
):
    """Username veya email girilebilir. Kullanıcı bulunamazsa bile aynı yanıt döner
    (user enumeration koruması). Email kayıtlı değilse gönderim atlanır."""
    user = (
        await db.execute(
            select(User).where(
                (User.username == payload.username) | (User.email == payload.username)
            )
        )
    ).scalar_one_or_none()
    if user and user.email:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expiry = datetime.now(UTC) + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
        db.add(PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=expiry))
        await db.commit()
        reset_url = f"{settings_cfg.store_public_url.rstrip('/')}/admin.html#reset/{raw_token}"
        try:
            await send_password_reset(
                email=user.email,
                username=user.username,
                reset_url=reset_url,
                ttl_minutes=RESET_TOKEN_TTL_MINUTES,
            )
        except Exception:
            pass
    # Her durumda aynı yanıt
    return {
        "ok": True,
        "message": "Eğer kayıtlı bir e-posta bulduysak, şifre sıfırlama bağlantısı gönderildi.",
    }


@router.post("/reset-password", status_code=200)
@limiter.limit("10/minute")
async def reset_password_with_token(
    request: Request, payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)
):
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    row = (
        await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if not row or row.used_at or _as_utc(row.expires_at) < now:
        raise HTTPException(status_code=400, detail="Bağlantı geçersiz veya süresi dolmuş")
    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="Kullanıcı bulunamadı")
    user.password_hash = hash_password(payload.new_password)
    row.used_at = now
    # Aynı kullanıcı için diğer açık token'ları da iptal et
    await db.execute(
        update(PasswordResetToken)
        .where((PasswordResetToken.user_id == user.id) & (PasswordResetToken.used_at.is_(None)))
        .values(used_at=now)
    )
    # Güvenlik: tüm aktif refresh token'larını iptal et (çalınmış bir oturum
    # şifre sıfırlamadan sonra da geçerli kalmasın)
    await db.execute(
        update(RefreshToken)
        .where((RefreshToken.user_id == user.id) & (RefreshToken.revoked_at.is_(None)))
        .values(revoked_at=now)
    )
    await db.commit()
    return {"ok": True}
