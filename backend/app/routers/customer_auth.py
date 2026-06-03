"""Müşteri üyelik (auth) router'ı.

Admin kullanıcılardan tamamen ayrı bir kimlik sistemi:
  * Token type=`customer_access`
  * Tablo: `customers` (password_hash artık opsiyonel — pasif kayıt + üyelik).
  * Refresh token tablosu: `customer_refresh_tokens` (rotation'lı).
  * Şifre sıfırlama: `customer_password_reset_tokens` (email + plaintext token).
  * Email doğrulama: `verification_token_hash` — kayıt sırasında üretilip mail
    yoluyla gönderilir; doğrulama endpoint'i `is_verified=True` yapar.

Endpoint'ler (`/api/customer-auth/*`):
  POST /register             — yeni üyelik (varolan pasif kaydı upgrade edebilir)
  POST /login                — JWT access + refresh
  POST /refresh              — refresh rotation
  POST /logout               — refresh revoke
  GET  /me                   — kendi profil
  PATCH /me                  — profil güncelleme (ad/tel/şehir/adres/marketing)
  POST /change-password      — eski parola + yeni parola
  POST /forgot-password      — email'e reset link (enumeration korumalı)
  POST /reset-password       — reset token + yeni parola
  POST /verify-email         — email doğrulama token'ı tüket
  GET  /orders               — müşterinin kendi siparişleri
  GET  /orders/{order_no}    — tek sipariş detayı (yalnız sahibine)
  GET  /loyalty              — kendi puan/tier durumu
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import current_customer
from app.models import (
    AuditLog,
    Customer,
    CustomerPasswordResetToken,
    CustomerRefreshToken,
    DataDeletionRequest,
    DataDeletionStatus,
    Order,
)
from app.rate_limit import limiter
from app.schemas import OrderOut
from app.security import (
    create_customer_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh,
    verify_password,
)
from app.services.email import render_template, send_email, send_password_reset
from app.services.loyalty import loyalty_for_email
from app.services.privacy import collect_customer_data, run_deletion
from app.services.tracking import build_tracking_response

router = APIRouter(prefix="/api/customer-auth", tags=["customer-auth"])
settings_cfg = get_settings()
RESET_TOKEN_TTL_MINUTES = 30
DELETION_TOKEN_TTL_MINUTES = 60


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Schemas ────────────────────────────────────────────────────────────────
class CustomerRegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=2, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=2000)
    marketing_opt_in: bool = False
    # Honeypot — bot doldurursa reddet
    website: str | None = Field(default=None, max_length=0)


class CustomerLoginIn(BaseModel):
    email: EmailStr
    password: str


class CustomerTokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    customer: "CustomerOut"


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    phone: str | None
    city: str | None
    address: str | None
    is_verified: bool
    marketing_opt_in: bool
    created_at: datetime


CustomerTokenPair.model_rebuild()


class CustomerProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=2000)
    marketing_opt_in: bool | None = None


class CustomerChangePassword(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class CustomerForgotPassword(BaseModel):
    email: EmailStr


class CustomerResetPassword(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class CustomerVerifyEmail(BaseModel):
    token: str


class RefreshIn(BaseModel):
    refresh_token: str


class DataDeleteRequestIn(BaseModel):
    """KVKK silme talebi başlatma — mevcut parola + opsiyonel sebep."""
    password: str
    reason: str | None = Field(default=None, max_length=2000)


class DataDeleteConfirmIn(BaseModel):
    token: str


# ── Helpers ────────────────────────────────────────────────────────────────
async def _issue_tokens(db: AsyncSession, customer: Customer) -> CustomerTokenPair:
    access = create_customer_access_token(customer.id, customer.email)
    raw_refresh, refresh_hash, expiry = create_refresh_token()
    db.add(CustomerRefreshToken(customer_id=customer.id, token_hash=refresh_hash, expires_at=expiry))
    customer.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    return CustomerTokenPair(
        access_token=access,
        refresh_token=raw_refresh,
        customer=CustomerOut.model_validate(customer),
    )


async def _send_verification_email(customer: Customer, raw_token: str) -> None:
    """Email doğrulama linki gönder (SMTP yoksa dev modunda konsola düşer)."""
    base = settings_cfg.store_public_url.rstrip("/")
    verify_url = f"{base}/#verify-email/{raw_token}"
    try:
        html = render_template(
            "email_verify.html",
            name=customer.name,
            verify_url=verify_url,
        ) if _template_exists("email_verify.html") else (
            f"<p>Merhaba {customer.name},</p>"
            f"<p>Üyeliğinizi tamamlamak için aşağıdaki bağlantıya tıklayın:</p>"
            f'<p><a href="{verify_url}">{verify_url}</a></p>'
            f"<p>Bağlantı 24 saat geçerlidir.</p>"
        )
        await send_email(to=customer.email, subject="E-posta doğrulama · TecnoTools", html=html)
    except Exception:
        pass


def _template_exists(name: str) -> bool:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "templates" / "email" / name).is_file()


# ── Endpoints ──────────────────────────────────────────────────────────────
@router.post("/register", response_model=CustomerTokenPair, status_code=201)
@limiter.limit("5/minute")
async def register(request: Request, payload: CustomerRegisterIn, db: AsyncSession = Depends(get_db)):
    """Yeni müşteri üyeliği.

    Mevcut **pasif** Customer kaydı (checkout'tan otomatik açılmış, password_hash
    NULL) varsa, parolayı set ederek üyeliğe yükseltir. Aksi halde yeni kayıt açar.
    """
    if payload.website:  # honeypot
        raise HTTPException(status_code=400, detail="invalid")

    email_norm = payload.email.strip().lower()
    existing = (
        await db.execute(select(Customer).where(Customer.email == email_norm))
    ).scalar_one_or_none()
    if existing and existing.password_hash:
        # Zaten üye → 409 (frontend "giriş yap" yönlendirmesi yapar)
        raise HTTPException(status_code=409, detail="Bu e-posta zaten kayıtlı")

    raw_verify_token = secrets.token_urlsafe(32)
    verify_hash = _hash_token(raw_verify_token)

    if existing:
        # Pasif kayıt → üyeliğe yükselt
        existing.password_hash = hash_password(payload.password)
        existing.name = payload.name
        existing.phone = payload.phone or existing.phone
        existing.city = payload.city or existing.city
        existing.address = payload.address or existing.address
        existing.marketing_opt_in = payload.marketing_opt_in
        existing.verification_token_hash = verify_hash
        existing.is_active = True
        customer = existing
    else:
        customer = Customer(
            email=email_norm,
            name=payload.name,
            phone=payload.phone,
            city=payload.city,
            address=payload.address,
            password_hash=hash_password(payload.password),
            marketing_opt_in=payload.marketing_opt_in,
            verification_token_hash=verify_hash,
            is_active=True,
            is_verified=False,
        )
        db.add(customer)
        await db.flush()

    await db.commit()
    await db.refresh(customer)
    await _send_verification_email(customer, raw_verify_token)
    # Token üret + dön (kullanıcı doğrulamayı sonra yapabilir, ama hemen giriş yapmış olur)
    return await _issue_tokens(db, customer)


@router.post("/login", response_model=CustomerTokenPair)
@limiter.limit("10/minute")
async def login(request: Request, payload: CustomerLoginIn, db: AsyncSession = Depends(get_db)):
    customer = (
        await db.execute(select(Customer).where(Customer.email == payload.email.strip().lower()))
    ).scalar_one_or_none()
    # User enumeration koruması: hata mesajları aynı.
    invalid = HTTPException(status_code=401, detail="E-posta veya şifre hatalı")
    if not customer or not customer.password_hash:
        raise invalid
    if not verify_password(payload.password, customer.password_hash):
        raise invalid
    if not customer.is_active:
        raise HTTPException(status_code=403, detail="Hesabınız devre dışı bırakılmış")
    return await _issue_tokens(db, customer)


@router.post("/refresh", response_model=CustomerTokenPair)
@limiter.limit("30/minute")
async def refresh(request: Request, payload: RefreshIn, db: AsyncSession = Depends(get_db)):
    digest = hash_refresh(payload.refresh_token)
    row = (
        await db.execute(select(CustomerRefreshToken).where(CustomerRefreshToken.token_hash == digest))
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if not row or row.revoked_at or _as_utc(row.expires_at) < now:
        raise HTTPException(status_code=401, detail="Refresh token geçersiz")
    customer = (await db.execute(select(Customer).where(Customer.id == row.customer_id))).scalar_one_or_none()
    if not customer or not customer.is_active:
        raise HTTPException(status_code=401, detail="Müşteri bulunamadı")
    # Rotate
    row.revoked_at = now
    return await _issue_tokens(db, customer)


@router.post("/logout", status_code=204)
async def logout(payload: RefreshIn, db: AsyncSession = Depends(get_db)):
    digest = hash_refresh(payload.refresh_token)
    row = (
        await db.execute(select(CustomerRefreshToken).where(CustomerRefreshToken.token_hash == digest))
    ).scalar_one_or_none()
    if row and not row.revoked_at:
        row.revoked_at = datetime.now(timezone.utc)
        await db.commit()
    return None


@router.get("/me", response_model=CustomerOut)
async def me(customer: Customer = Depends(current_customer)):
    return customer


@router.patch("/me", response_model=CustomerOut)
async def update_me(
    payload: CustomerProfileUpdate,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(customer, k, v)
    await db.commit()
    await db.refresh(customer)
    return customer


@router.post("/change-password", status_code=200)
async def change_password(
    payload: CustomerChangePassword,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    if not customer.password_hash or not verify_password(payload.current_password, customer.password_hash):
        raise HTTPException(status_code=401, detail="Mevcut şifre hatalı")
    customer.password_hash = hash_password(payload.new_password)
    # Güvenlik: tüm aktif refresh token'larını iptal et (başka cihazlar çıkış yapar)
    await db.execute(
        CustomerRefreshToken.__table__.update()
        .where(
            (CustomerRefreshToken.customer_id == customer.id)
            & (CustomerRefreshToken.revoked_at.is_(None))
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"ok": True}


@router.post("/forgot-password", status_code=200)
@limiter.limit("3/minute")
async def forgot_password(
    request: Request, payload: CustomerForgotPassword, db: AsyncSession = Depends(get_db)
):
    """Email kayıtlıysa reset link gönder. Enumeration koruması: her durumda aynı yanıt."""
    customer = (
        await db.execute(select(Customer).where(Customer.email == payload.email.strip().lower()))
    ).scalar_one_or_none()
    if customer and customer.password_hash:  # sadece gerçek üyeler için
        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        expiry = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
        db.add(
            CustomerPasswordResetToken(
                customer_id=customer.id, token_hash=token_hash, expires_at=expiry
            )
        )
        await db.commit()
        reset_url = (
            f"{settings_cfg.store_public_url.rstrip('/')}/#reset-password/{raw_token}"
        )
        try:
            await send_password_reset(
                email=customer.email,
                username=customer.name,
                reset_url=reset_url,
                ttl_minutes=RESET_TOKEN_TTL_MINUTES,
            )
        except Exception:
            pass
    return {"ok": True, "message": "Eğer kayıtlı bir e-posta bulduysak, şifre sıfırlama bağlantısı gönderildi."}


@router.post("/reset-password", status_code=200)
@limiter.limit("10/minute")
async def reset_password(
    request: Request, payload: CustomerResetPassword, db: AsyncSession = Depends(get_db)
):
    token_hash = _hash_token(payload.token)
    row = (
        await db.execute(
            select(CustomerPasswordResetToken).where(CustomerPasswordResetToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if not row or row.used_at or _as_utc(row.expires_at) < now:
        raise HTTPException(status_code=400, detail="Bağlantı geçersiz veya süresi dolmuş")
    customer = (
        await db.execute(select(Customer).where(Customer.id == row.customer_id))
    ).scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=400, detail="Müşteri bulunamadı")
    customer.password_hash = hash_password(payload.new_password)
    row.used_at = now
    # Aynı müşterinin diğer açık reset token'larını da iptal et
    await db.execute(
        CustomerPasswordResetToken.__table__.update()
        .where(
            (CustomerPasswordResetToken.customer_id == customer.id)
            & (CustomerPasswordResetToken.used_at.is_(None))
        )
        .values(used_at=now)
    )
    # Tüm refresh token'larını iptal et (güvenlik)
    await db.execute(
        CustomerRefreshToken.__table__.update()
        .where(
            (CustomerRefreshToken.customer_id == customer.id)
            & (CustomerRefreshToken.revoked_at.is_(None))
        )
        .values(revoked_at=now)
    )
    await db.commit()
    return {"ok": True}


@router.post("/verify-email", status_code=200)
async def verify_email(payload: CustomerVerifyEmail, db: AsyncSession = Depends(get_db)):
    token_hash = _hash_token(payload.token)
    customer = (
        await db.execute(select(Customer).where(Customer.verification_token_hash == token_hash))
    ).scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=400, detail="Doğrulama bağlantısı geçersiz")
    customer.is_verified = True
    customer.verification_token_hash = None
    await db.commit()
    return {"ok": True, "is_verified": True}


@router.post("/resend-verification", status_code=200)
@limiter.limit("3/minute")
async def resend_verification(
    request: Request,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    if customer.is_verified:
        return {"ok": True, "message": "Zaten doğrulanmış"}
    raw_token = secrets.token_urlsafe(32)
    customer.verification_token_hash = _hash_token(raw_token)
    await db.commit()
    await _send_verification_email(customer, raw_token)
    return {"ok": True, "message": "Doğrulama e-postası tekrar gönderildi"}


# ── Müşteri-özel sipariş ve sadakat görünümleri ─────────────────────────────
@router.get("/orders", response_model=list[OrderOut])
async def list_my_orders(
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    """Müşterinin kendi siparişleri. Email eşleşmesiyle (customer_id NULL'a düşmüş
    eski siparişleri de yakalamak için)."""
    rows = (
        await db.execute(
            select(Order)
            .where(
                (Order.customer_id == customer.id) | (Order.customer_email == customer.email)
            )
            .order_by(Order.created_at.desc())
        )
    ).scalars().unique().all()
    return rows


@router.get("/orders/{order_no}", response_model=OrderOut)
async def get_my_order(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    o = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    # Sahiplik kontrolü: ya FK eşleşmesi, ya da email eşleşmesi
    if not (o.customer_id == customer.id or o.customer_email == customer.email):
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return o


@router.get("/orders/{order_no}/tracking")
async def get_my_order_tracking(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    """Üye için detaylı takip — timeline, ETA, kargo linki, iade durumu."""
    o = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not o or not (o.customer_id == customer.id or o.customer_email == customer.email):
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return await build_tracking_response(db, o)


@router.get("/loyalty")
async def my_loyalty(
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    acc = await loyalty_for_email(db, customer.email)
    if not acc:
        return {
            "email": customer.email,
            "points": 0,
            "points_value_try": 0,
            "lifetime_spend": 0,
            "annual_spend": 0,
            "tier": "Bronze",
            "next_tier": "Silver",
            "next_tier_remaining": 1000,
        }
    return acc.__dict__


# ── KVKK 11. madde: veri ihracı + silme/unutulma hakkı ──────────────────────


@router.get("/me/data-export")
@limiter.limit("3/hour")
async def export_my_data(
    request: Request,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    """KVKK m.11(d) — taşınabilirlik. Tüm kişisel veriyi JSON olarak indirir.

    Şifre hash'i ve refresh token gibi güvenlik içerikleri çıktıya konmaz.
    Rate limit: kullanıcı başına saatte 3 — abuse koruması.
    """
    data = await collect_customer_data(db, customer)
    db.add(
        AuditLog(
            actor=f"customer#{customer.id}",
            action="data-export",
            message=f"Veri ihracı: {customer.email}",
        )
    )
    await db.commit()
    return data


@router.post("/me/delete-request", status_code=202)
@limiter.limit("3/hour")
async def request_account_deletion(
    request: Request,
    payload: DataDeleteRequestIn,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(current_customer),
):
    """KVKK m.11(e) — silme/unutulma talebi başlatır.

    İki aşamalı:
      1) Bu endpoint parola ister, mail'e onay linki gönderir (rastgele token).
      2) Müşteri linki açar → /me/delete-confirm işi yürütür.

    Açık aktif (ödenmemiş veya kargo bekleyen) sipariş varsa engeller —
    mali süreç tamamlanmadan silme yapılamaz. Müşteri önce siparişi iptal
    etmeli / teslim almalı.
    """
    if not customer.password_hash or not verify_password(payload.password, customer.password_hash):
        raise HTTPException(status_code=401, detail="Mevcut şifre hatalı")

    # Aktif (henüz tamamlanmamış) sipariş kontrolü
    open_orders = (
        await db.execute(
            select(Order).where(
                ((Order.customer_id == customer.id) | (Order.customer_email == customer.email))
                & (Order.status.in_(("pending", "processing", "shipped")))
            )
        )
    ).scalars().unique().all()
    if open_orders:
        raise HTTPException(
            status_code=409,
            detail=(
                "Aktif siparişiniz var. Silme talebi için tüm siparişlerin "
                "teslim/iptal durumuna geçmesi gerekir."
            ),
        )

    raw_token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(minutes=DELETION_TOKEN_TTL_MINUTES)
    ddr = DataDeletionRequest(
        customer_id=customer.id,
        email_snapshot=customer.email,
        name_snapshot=customer.name,
        token_hash=_hash_token(raw_token),
        expires_at=expiry,
        status=DataDeletionStatus.PENDING.value,
        reason=payload.reason,
    )
    db.add(ddr)
    db.add(
        AuditLog(
            actor=f"customer#{customer.id}",
            action="data-deletion-request",
            message=f"Silme talebi açıldı: {customer.email}",
        )
    )
    await db.commit()

    confirm_url = (
        f"{settings_cfg.store_public_url.rstrip('/')}/#delete-account/{raw_token}"
    )
    html = (
        f"<p>Merhaba {customer.name},</p>"
        f"<p>TecnoTools hesabınızı ve kişisel verilerinizi silme talebiniz "
        f"alınmıştır. Talebi onaylamak için aşağıdaki bağlantıya tıklayın:</p>"
        f'<p><a href="{confirm_url}">{confirm_url}</a></p>'
        f"<p>Bu bağlantı {DELETION_TOKEN_TTL_MINUTES} dakika geçerlidir.</p>"
        f"<p><strong>Onayladıktan sonra:</strong> hesabınız ve kişisel verileriniz "
        f"silinir; geçmiş siparişleriniz mali kayıt zorunluluğu nedeniyle "
        f"anonim olarak saklanır.</p>"
        f"<p>Bu talebi siz başlatmadıysanız bu e-postayı yok sayın; bağlantıya "
        f"tıklanmadığı sürece hiçbir veri silinmez.</p>"
    )
    try:
        await send_email(
            to=customer.email,
            subject="Hesap silme talebiniz · TecnoTools",
            html=html,
        )
    except Exception:
        pass
    return {"ok": True, "message": "Onay bağlantısı e-posta adresinize gönderildi."}


@router.post("/me/delete-confirm", status_code=200)
@limiter.limit("10/hour")
async def confirm_account_deletion(
    request: Request,
    payload: DataDeleteConfirmIn,
    db: AsyncSession = Depends(get_db),
):
    """Mail'deki onay linki — bu çağrıdan sonra veriler geri dönülmez biçimde silinir.

    Auth zorunlu değil: müşteri zaten token sahibi olduğunu kanıtlıyor.
    Tamamlandıktan sonra çağıran istemcide oturum kapatılmalı (token zaten siler
    refresh kayıtlarını).
    """
    token_hash = _hash_token(payload.token)
    ddr = (
        await db.execute(
            select(DataDeletionRequest).where(DataDeletionRequest.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if not ddr:
        raise HTTPException(status_code=400, detail="Bağlantı geçersiz")
    if ddr.status not in (DataDeletionStatus.PENDING.value,):
        raise HTTPException(status_code=400, detail="Bu talep zaten işlenmiş")
    if ddr.expires_at and _as_utc(ddr.expires_at) < now:
        raise HTTPException(status_code=400, detail="Bağlantının süresi dolmuş")

    ddr.status = DataDeletionStatus.CONFIRMED.value
    ddr.confirmed_at = now
    ddr.token_hash = None  # tek kullanımlık
    await db.commit()

    try:
        summary = await run_deletion(db, ddr)
    except Exception as e:
        ddr.status = DataDeletionStatus.PENDING.value  # admin manuel müdahale için
        ddr.error_message = str(e)[:500]
        await db.commit()
        raise HTTPException(status_code=500, detail="Silme işlemi başarısız, destek ekibi bilgilendirildi")

    return {"ok": True, "summary": summary}
