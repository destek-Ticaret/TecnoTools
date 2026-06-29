from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class _ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── AUTH ──
class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: str | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(_ORMBase):
    id: int
    username: str
    role: str
    is_primary: bool
    is_active: bool
    permissions: dict[str, Any] | None = None


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr | None = None
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="editor")  # admin | editor | viewer


class AdminUserUpdate(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: str | None = None
    is_active: bool | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class TwoFASetupRequest(BaseModel):
    secret: str = Field(min_length=16, max_length=64)
    code: str = Field(min_length=6, max_length=8)


class TwoFADisableRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class TwoFAStatus(BaseModel):
    enabled: bool


class ReturnItemIn(BaseModel):
    product_id: int | None = None
    name: str
    qty: int = Field(ge=1)
    price: float = Field(ge=0)


class ReturnRequestIn(BaseModel):
    """Müşteri iade talebi — sipariş no + email + kalemler + sebep."""

    order_no: str
    customer_email: EmailStr
    reason: str = Field(min_length=2, max_length=64)
    note: str | None = Field(default=None, max_length=2000)
    items: list[ReturnItemIn] = Field(min_length=1)
    website: str | None = Field(default=None, max_length=0)  # honeypot


class ReturnRequestOut(_ORMBase):
    id: int
    order_no: str
    customer_name: str
    customer_email: str
    reason: str
    note: str | None
    items: list
    refund_amount: float
    status: str
    admin_note: str | None
    processed_by: str | None
    created_at: datetime
    processed_at: datetime | None


class ReturnStatusUpdate(BaseModel):
    status: str  # approved | rejected | refunded | cancelled
    admin_note: str | None = Field(default=None, max_length=2000)


class ForgotPasswordRequest(BaseModel):
    username: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


# ── CATEGORY ──
class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    sort_order: int = 0


class CategoryOut(_ORMBase):
    id: int
    name: str
    sort_order: int


# ── PRODUCT ──
class ProductVariantIn(BaseModel):
    id: int | None = None  # mevcut varyantı eşleştirmek için (update'te); yoksa yeni
    name: str = Field(min_length=1, max_length=255)
    options: dict | None = None  # {"Renk":"Kırmızı","Beden":"42"}
    sku: str | None = Field(default=None, max_length=64)
    barcode: str | None = Field(default=None, max_length=64)
    price: float | None = Field(default=None, ge=0)  # boşsa ürün fiyatı
    stock: int = Field(ge=0, default=0)
    image: str | None = None
    is_active: bool = True
    sort_order: int = 0


class ProductVariantOut(_ORMBase):
    id: int
    name: str
    options: dict | None
    sku: str | None
    barcode: str | None
    price: float | None
    stock: int
    image: str | None
    is_active: bool
    sort_order: int


class ProductVariantPublicOut(BaseModel):
    """Public — fiyat çözülmüş (boşsa ürün fiyatı), stok effective."""

    id: int
    name: str
    options: dict | None = None
    price: float
    effective_stock: int
    image: str | None = None


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    sub: str | None = None
    description: str | None = None
    icon: str | None = "📦"
    category_id: int | None = None
    price: float = Field(ge=0)
    old_price: float | None = None
    cost: float | None = Field(default=None, ge=0)
    stock: int = Field(ge=0, default=0)
    badge: dict | None = None
    features: list[str] | None = None
    images: list[str] | None = None
    video_url: str | None = None
    market: str = "both"  # tr | intl | both
    max_per_order: int | None = Field(default=None, ge=1)  # sipariş başına azami adet
    is_active: bool = True
    variants: list[ProductVariantIn] | None = None  # None = dokunma; [] = tümünü sil


class ProductOut(_ORMBase):
    id: int
    name: str
    sub: str | None
    description: str | None
    icon: str | None
    category_id: int | None
    category: CategoryOut | None = None
    price: float
    old_price: float | None
    cost: float | None = None
    stock: int
    rating: float
    review_count: int
    badge: dict | None
    features: list[str] | None
    images: list[str] | None
    video_url: str | None = None
    market: str = "both"
    max_per_order: int | None = None
    is_active: bool
    variants: list[ProductVariantOut] = []


class ProductPublicOut(_ORMBase):
    """Public — `effective_stock` ile birlikte; sensitive alanlar yok."""

    id: int
    name: str
    sub: str | None
    description: str | None
    icon: str | None
    category_id: int | None
    category: CategoryOut | None = None
    price: float
    old_price: float | None
    effective_stock: int
    rating: float
    review_count: int
    badge: dict | None
    features: list[str] | None
    images: list[str] | None
    video_url: str | None = None
    max_per_order: int | None = None
    variants: list[ProductVariantPublicOut] = []


# ── ORDER ──
class CartItemIn(BaseModel):
    product_id: int
    qty: int = Field(ge=1)
    variant_id: int | None = None


class CheckoutRequest(BaseModel):
    items: list[CartItemIn] = Field(min_length=1)
    customer_name: str = Field(min_length=3, max_length=255)
    customer_email: EmailStr
    customer_phone: str = Field(min_length=8, max_length=32)
    customer_city: str | None = None
    customer_address: str = Field(min_length=10)
    coupon_code: str | None = None
    note: str | None = None
    session_id: str | None = None  # rezervasyonu eşleştirmek için
    payment_method: str = Field(default="card")  # card | wire | cod (havale | kapıda)
    currency: str = Field(default="TRY")  # TRY → PayTR (TR), EUR/USD → Stripe (yurt dışı)
    # Sadakat puanı harcama — üye girişi (customer_access token) zorunlu;
    # sunucu bakiye + %20 sepet sınırına göre kırpar.
    use_loyalty_points: int = Field(default=0, ge=0)
    # E-arşiv fatura için opsiyonel — bireysel müşteri TCKN, kurumsal VKN
    tax_no: str | None = Field(default=None, max_length=16)
    tax_office: str | None = Field(default=None, max_length=128)
    company_title: str | None = Field(default=None, max_length=255)


class OrderItemOut(_ORMBase):
    id: int
    product_id: int | None
    variant_id: int | None = None
    variant_name: str | None = None
    name: str
    icon: str | None
    image: str | None
    price: float
    qty: int


class OrderOut(_ORMBase):
    id: int
    order_no: str
    customer_name: str
    customer_email: str
    customer_phone: str
    customer_city: str | None
    customer_address: str
    subtotal: float
    discount: float
    coupon_code: str | None
    loyalty_points_used: int = 0
    loyalty_discount: float = 0
    tax: float
    shipping: float
    total: float
    status: str
    payment_status: str
    payment_method: str | None
    payment_brand: str | None
    payment_last4: str | None
    tracking_no: str | None
    note: str | None
    admin_notes: list | None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemOut]


class OrderStatusUpdate(BaseModel):
    status: str


class OrderNoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class OrderPatch(BaseModel):
    """Kısmi sipariş güncelleme — tracking, note, vs."""

    tracking_no: str | None = None
    note: str | None = None


# ── COUPON ──
class CouponIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    type: str  # percent | fixed
    value: float = Field(ge=0)
    min_order: float = 0
    max_uses: int | None = None
    expires_at: datetime | None = None
    is_active: bool = True


class CouponOut(_ORMBase):
    id: int
    code: str
    type: str
    value: float
    min_order: float
    max_uses: int | None
    used_count: int
    expires_at: datetime | None
    is_active: bool


# ── PAYMENT ──
class PaymentStartResponse(BaseModel):
    """Ödeme akışı için frontend'in kullanacağı bilgiler.
    - paytr: iframe_url içerideki iframe'de açılır
    - stripe: redirect_url'e tam sayfa yönlendirme yapılır
    """

    order_no: str
    iframe_token: str
    iframe_url: str
    provider: str = "paytr"


# ── NEWSLETTER ──
class NewsletterIn(BaseModel):
    email: EmailStr
    website: str | None = Field(default=None, max_length=0)  # honeypot


# ── AUDIT ──
class AuditOut(_ORMBase):
    id: int
    actor: str
    action: str
    message: str
    meta: dict | None
    created_at: datetime
