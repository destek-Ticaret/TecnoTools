from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# JSONB Postgres'te performanslı; SQLite'da bulunmaz. Test sırasında dialect'e göre düş.
JSONType = JSONB().with_variant(JSON(), "sqlite")


class UserRole(str, Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PaymentStatus(str, Enum):
    INITIATED = "initiated"
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    REFUNDED = "refunded"


class CouponType(str, Enum):
    PERCENT = "percent"
    FIXED = "fixed"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=UserRole.ADMIN.value)
    # Granüler yetki override'ı. NULL ise role-tabanlı varsayılan izinler geçerli.
    # Doluysa {"orders.write": true, "products.delete": false, ...} formatında
    # rol varsayılanlarını override eder. ADMIN her zaman tüm izinlere sahiptir.
    permissions: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recovery_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(8), nullable=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    old_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Alış maliyeti — kâr raporu ve marj-tabanlı otomatik fiyatlandırma için.
    cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[float] = mapped_column(Numeric(3, 2), default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    badge: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    features: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    images: Mapped[list | None] = mapped_column(JSONType, nullable=True)  # list of URLs
    video_url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # YouTube/Vimeo
    # Dropshipping — ürün bir tedarikçiden geliyorsa kaynak bilgisi. Boşsa
    # ürün kendi stoğumuzdandır. supplier_price = tedarikçideki alış fiyatı
    # (cost ile aynı olabilir; senkronda cost güncellenir).
    # Pazar hedefi: "tr" (sadece Türkiye), "intl" (sadece yurt dışı), "both" (her ikisi).
    # Vitrin ziyaretçinin diline göre filtreler. Dropship ürünleri varsayılan "intl".
    market: Mapped[str] = mapped_column(String(8), default="both", server_default="both")
    supplier: Mapped[str | None] = mapped_column(String(32), nullable=True)  # aliexpress | manual
    supplier_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    supplier_product_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    supplier_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    supplier_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    category: Mapped["Category | None"] = relationship(lazy="joined")
    variants: Mapped[list["ProductVariant"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductVariant.sort_order",
        lazy="selectin",
    )


class ProductVariant(Base):
    """Ürün varyantı (basit liste modeli). Her varyantın kendi stoğu vardır;
    `price` boşsa ürünün ana fiyatı kullanılır. `options` ileride Renk/Beden
    gibi eksenlere göre dropdown gruplaması için yapısal veri tutar."""

    __tablename__ = "product_variants"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))  # görünen ad, ör. "Kırmızı / 42"
    options: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # {"Renk":"Kırmızı"}
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)  # boşsa ürün fiyatı
    stock: Mapped[int] = mapped_column(Integer, default=0)
    image: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product: Mapped["Product"] = relationship(back_populates="variants")


class Customer(Base):
    """Müşteri kaydı.

    İki kullanım modu:
      1) Pasif kayıt — checkout sırasında otomatik açılır (password_hash NULL).
         Müşteri üyelik istemeden alışveriş yapabilir.
      2) Aktif üyelik — `/api/customer-auth/register` ile parola belirleyip
         giriş yapan müşteriler. password_hash dolu, is_verified email
         doğrulamasıyla True'ya geçer.
    """

    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Üyelik alanları — pasif kayıt için NULL kalabilir
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    verification_token_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    marketing_opt_in: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    # Snapshot - sipariş anındaki müşteri bilgisi (müşteri sonradan değişse de etkilenmez)
    customer_name: Mapped[str] = mapped_column(String(255))
    customer_email: Mapped[str] = mapped_column(String(255))
    customer_phone: Mapped[str] = mapped_column(String(32))
    customer_city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    customer_address: Mapped[str] = mapped_column(Text)

    subtotal: Mapped[float] = mapped_column(Numeric(12, 2))
    discount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    coupon_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Sadakat puanı harcaması — kupon indiriminden ayrı tutulur (denetlenebilirlik)
    loyalty_points_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    loyalty_discount: Mapped[float] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    tax: Mapped[float] = mapped_column(Numeric(12, 2))
    shipping: Mapped[float] = mapped_column(Numeric(12, 2))
    total: Mapped[float] = mapped_column(Numeric(12, 2))

    status: Mapped[str] = mapped_column(String(16), default=OrderStatus.PENDING.value, index=True)
    payment_status: Mapped[str] = mapped_column(
        String(16), default=PaymentStatus.INITIATED.value, index=True
    )
    payment_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payment_brand: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payment_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)

    tracking_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Kargo firma kodu: aras | yurtici | mng | ptt | hepsijet | surat. Boşsa
    # tracking_no prefix'inden tracking.carrier_for() ile tahmin edilir.
    carrier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_tracking_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_notes: Mapped[list | None] = mapped_column(JSONType, nullable=True)

    # E-arşiv fatura için opsiyonel — boşsa bireysel müşteri sayılır (TCKN
    # alanına müşteri ister TCKN girer; VKN girerse otomatik B2B/kurumsal olur)
    tax_no: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tax_office: Mapped[str | None] = mapped_column(String(128), nullable=True)
    company_title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True
    )
    variant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # snapshot
    # Snapshot
    name: Mapped[str] = mapped_column(String(255))
    icon: Mapped[str | None] = mapped_column(String(8), nullable=True)
    image: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    qty: Mapped[int] = mapped_column(Integer)

    order: Mapped[Order] = relationship(back_populates="items")


class ShipmentEvent(Base):
    """Kargo firma API/webhook'undan gelen tek tek hareket kaydı.

    `code` normalize edilmiş internal kod: created | picked_up | in_transit |
    out_for_delivery | delivered | failed_attempt | returned | cancelled.
    `raw_status` orijinal firma metnini saklar (debug için).
    """

    __tablename__ = "shipment_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), index=True)
    carrier: Mapped[str] = mapped_column(String(16), index=True)
    tracking_no: Mapped[str] = mapped_column(String(64), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    raw_status: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(16), default="webhook")  # webhook | poll | manual
    raw_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint(
            "carrier", "tracking_no", "code", "occurred_at", name="uq_shipment_event_dedupe"
        ),
    )


class Coupon(Base):
    __tablename__ = "coupons"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(16))  # percent | fixed
    value: Mapped[float] = mapped_column(Numeric(10, 2))
    min_order: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    product_name: Mapped[str] = mapped_column(String(255))
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(32))  # sale, manual, init, return, ...
    order_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Reservation(Base):
    """Sepete eklenen ama henüz ödenmemiş stok rezervasyonu. TTL'li."""

    __tablename__ = "reservations"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    qty: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        UniqueConstraint("session_id", "product_id", name="uq_reservation_session_product"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class NewsletterSubscriber(Base):
    __tablename__ = "newsletter_subscribers"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalyticsEvent(Base):
    """Self-hosted minimal analytics — sayfa görüntüleme, sepete ekleme, satın alma."""

    __tablename__ = "analytics_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    event: Mapped[str] = mapped_column(
        String(64), index=True
    )  # page_view, add_to_cart, purchase, ...
    path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    meta: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # privacy: IP'nin SHA-256'sının ilk 16 char'ı
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class NewsletterCampaign(Base):
    __tablename__ = "newsletter_campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(255))
    html_body: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64))
    total_recipients: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(16), default="draft"
    )  # draft, sending, completed, failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefreshToken(Base):
    """JWT refresh token kayıt — rotation ve revoke için."""

    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductReview(Base):
    """Müşteri ürün yorumu. Admin onayından geçer (is_approved)."""

    __tablename__ = "product_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    customer_name: Mapped[str] = mapped_column(String(255))
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rating: Mapped[int] = mapped_column(Integer)  # 1-5
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    order_no: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # doğrulanmış satın alma
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ProductQuestion(Base):
    """Ürün soru-cevap. Müşteri soru sorar, admin cevaplar; yayın moderasyondan geçer.

    Public listede yalnız `is_published=True` ve cevaplanmış sorular görünür.
    """

    __tablename__ = "product_questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    customer_name: Mapped[str] = mapped_column(String(255))
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StockNotification(Base):
    """Stok geldi bildirimi bekleme listesi."""

    __tablename__ = "stock_notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("product_id", "email", name="uq_stock_notif"),)


class SiteSetting(Base):
    """Site genel ayarları — admin'den runtime değiştirilebilir key-value."""

    __tablename__ = "site_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OrderCounter(Base):
    """Sipariş numarası counter — yıl bazlı tek satır."""

    __tablename__ = "order_counters"
    year: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, default=0)


class ReturnStatus(str, Enum):
    REQUESTED = "requested"  # müşteri talebi açtı
    APPROVED = "approved"  # admin onayladı, kargo bekleniyor
    REJECTED = "rejected"  # admin reddetti
    REFUNDED = "refunded"  # ürün iade alındı, para iade edildi
    CANCELLED = "cancelled"  # müşteri vazgeçti


class ReturnRequest(Base):
    """Sipariş iade talebi.

    items JSONB: [{ "product_id": int, "name": str, "qty": int, "price": float }, ...]
    Müşteri sadece kendi sipariş email'iyle eşleşen siparişlere iade açabilir.
    """

    __tablename__ = "return_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    order_no: Mapped[str] = mapped_column(String(32), index=True)  # snapshot
    customer_email: Mapped[str] = mapped_column(String(255), index=True)
    customer_name: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(
        String(64)
    )  # damaged, wrong_item, not_needed, defective, other
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    items: Mapped[list] = mapped_column(JSONType)  # iade edilecek kalemler
    refund_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(
        String(16), default=ReturnStatus.REQUESTED.value, index=True
    )
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordResetToken(Base):
    """Şifre sıfırlama: token plaintext olarak email link'inde, SHA-256 hash DB'de."""

    __tablename__ = "password_reset_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CustomerRefreshToken(Base):
    """Müşteri JWT refresh token — admin RefreshToken'dan ayrı, rotation'lı."""

    __tablename__ = "customer_refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CustomerPasswordResetToken(Base):
    """Müşteri şifre sıfırlama — admin token'larından ayrı tutulur."""

    __tablename__ = "customer_password_reset_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvoiceStatus(str, Enum):
    PENDING = "pending"  # Entegratöre yollanmadı (taslak)
    SENT = "sent"  # Entegratör kabul etti, ETTN üretildi
    FAILED = "failed"  # Entegratör reddetti
    CANCELLED = "cancelled"  # Sonradan iptal edildi


class InvoiceKind(str, Enum):
    EARSIV = "earsiv"  # Bireysel (e-arşiv)
    EFATURA = "efatura"  # Kurumsal mükellef (e-fatura) — gelecekte


class Invoice(Base):
    """E-arşiv / e-fatura kaydı.

    Entegratöre (Foriba, Nilvera, QNB, Logo, vb.) gönderilen her fatura için
    UUID + ETTN üretilir; PDF entegratörden bir public URL ile döner ya da
    backend'in kendi storage'ına kopyalanır.

    items: snapshot JSON list — fatura iptali/yeniden kesim sonrası bile
    fatura içeriği değişmesin.
    """

    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    order_no: Mapped[str] = mapped_column(String(32), index=True)  # snapshot
    invoice_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    ettn: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default=InvoiceKind.EARSIV.value)
    status: Mapped[str] = mapped_column(String(16), default=InvoiceStatus.PENDING.value, index=True)
    # Müşteri snapshot
    customer_name: Mapped[str] = mapped_column(String(255))
    customer_email: Mapped[str] = mapped_column(String(255))
    customer_phone: Mapped[str] = mapped_column(String(32))
    customer_address: Mapped[str] = mapped_column(Text)
    tax_no: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tax_office: Mapped[str | None] = mapped_column(String(128), nullable=True)
    company_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Tutarlar
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2))
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=20)  # KDV %
    tax_amount: Mapped[float] = mapped_column(Numeric(12, 2))
    total: Mapped[float] = mapped_column(Numeric(12, 2))
    # Snapshot kalemler
    items: Mapped[list] = mapped_column(JSONType)
    # Entegratör yanıtı (debug için raw response)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_response: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InvoiceCounter(Base):
    """Yıl bazlı fatura numarası counter — TT-FAT-2026-000123."""

    __tablename__ = "invoice_counters"
    year: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, default=0)


class ChatSessionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class ChatSession(Base):
    """Canlı destek konuşması.

    session_id browser-side sessionStorage'tan gelir (rezervasyonlar ile aynı kimlik).
    customer_id login olmuş müşteri için doldurulur; ziyaretçi için NULL kalır.
    """

    __tablename__ = "chat_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=ChatSessionStatus.OPEN.value, index=True
    )
    unread_admin: Mapped[int] = mapped_column(Integer, default=0)
    unread_customer: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ChatMessage(Base):
    """Tek mesaj — sender 'customer' veya 'admin'."""

    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_pk: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    sender: Mapped[str] = mapped_column(String(16))  # 'customer' | 'admin'
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class DataDeletionStatus(str, Enum):
    """KVKK 11. madde silme/unutulma hakkı talep durumu."""

    PENDING = "pending"  # token mailde, müşteri henüz onaylamadı
    CONFIRMED = "confirmed"  # müşteri linki tıkladı, kuyrukta
    COMPLETED = "completed"  # işlem tamamlandı (anonimleştirme + silme)
    CANCELLED = "cancelled"  # müşteri vazgeçti veya admin reddetti


class DataDeletionRequest(Base):
    """KVKK silme/unutulma hakkı talep kaydı.

    Akış:
      1) Müşteri /me/delete-request çağırır → token üretilir, e-postaya gönderilir.
      2) Müşteri e-postadaki linki açar → /me/delete-confirm token'ı tüketir.
      3) services/privacy.run_deletion() çalışır:
         - aktif siparişi olmayan müşteri tamamen silinir,
         - varsa siparişler "[Silinmiş Müşteri]" olarak anonimleştirilir,
         - newsletter, yorum, chat, refresh/reset token, stok bildirimi temizlenir.
      4) status=completed, audit log kaydı bırakılır.

    Snapshot email/name: müşteri zaten silinmişse bile talep izlenebilir kalsın.
    """

    __tablename__ = "data_deletion_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email_snapshot: Mapped[str] = mapped_column(String(255), index=True)
    name_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_hash: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True, nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=DataDeletionStatus.PENDING.value, index=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(
        JSONType, nullable=True
    )  # anonymize_count, deletion_count vb.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConsentLog(Base):
    """KVKK çerez/pazarlama izni denetim kaydı.

    Frontend granüler izin merkezini her güncellediğinde bir satır eklenir.
    Anonim ziyaretçi → session_id, üye → customer_id de dolar.
    IP gizliliği: SHA-256'nın ilk 16 char'ı (analytics ile aynı yöntem).
    """

    __tablename__ = "consent_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    categories: Mapped[dict] = mapped_column(
        JSONType
    )  # {"essential": true, "preference": true, "analytics": false, "marketing": false}
    policy_version: Mapped[str] = mapped_column(String(16), default="1.0")
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class WishlistItem(Base):
    """Müşteri favorisi — cihazlar arası senkron için sunucuda tutulur.

    Üye olmayan ziyaretçilerin favorileri frontend'de localStorage'da kalır;
    müşteri giriş yapınca `POST /api/wishlist/merge` ile sunucuya taşınır.
    """

    __tablename__ = "wishlist_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("customer_id", "product_id", name="uq_wishlist_customer_product"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Admin panel — içerik & vitrin yönetimi (banner, blog, statik sayfa, vitrin)
# ─────────────────────────────────────────────────────────────────────────────


class Banner(Base):
    """Vitrin görseli — ana sayfa hero slider, şerit ve yan banner alanları.

    position: hero | strip | sidebar | popup. Aynı pozisyondaki banner'lar
    sort_order'a göre sıralanır. starts_at/ends_at ile zamanlı yayın yapılır
    (boşsa süresiz). Public endpoint yalnız aktif + tarih penceresindekileri döner.
    """

    __tablename__ = "banners"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subtitle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_url: Mapped[str] = mapped_column(String(512))
    mobile_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    link_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cta_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    position: Mapped[str] = mapped_column(String(16), default="hero", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class HomepageSection(Base):
    """Ana sayfa düzeni — sürükle-bırak ile sıralanan bölümler.

    kind: hero | banner_strip | category_grid | product_carousel | blog | html
    config JSON bölüme özel ayarları tutar; ör. product_carousel için
    {"source": "trending", "limit": 8} veya category_grid için {"category_ids": [..]}.
    Frontend ana sayfayı bu sıralı, aktif bölümlere göre render eder.
    """

    __tablename__ = "homepage_sections"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BlogPost(Base):
    """Blog / haber yazısı — SEO içeriği için CMS modülü.

    slug benzersizdir, public URL'de kullanılır. body HTML'dir (admin editör).
    is_published False ise yalnız admin görür. tags JSON list[str].
    """

    __tablename__ = "blog_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    cover_image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tags: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    author: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    meta_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta_description: Mapped[str | None] = mapped_column(String(320), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CmsPage(Base):
    """Admin'in oluşturduğu statik içerik sayfası (hakkımızda, SSS, kargo vb.).

    legal/ klasöründeki sabit HTML'lerden farklı: runtime düzenlenebilir.
    """

    __tablename__ = "cms_pages"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    show_in_footer: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    meta_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta_description: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PricingRule(Base):
    """Otomatik fiyatlandırma kuralı.

    scope_type: all | category | product   (scope_id buna göre kategori/ürün id'si)
    strategy:
      - percent      : mevcut fiyata value% uygula (+zam / -indirim)
      - fixed        : mevcut fiyata value ₺ ekle/çıkar
      - margin       : maliyet (cost) üstüne value% kâr marjı → fiyat = cost*(1+value/100)
      - round_99     : fiyatı x.99'a yuvarla (value yok sayılır)
    min_price/max_price taban-tavan koruma sağlar.
    priority düşükten yükseğe uygulanır; yüksek priority son sözü söyler.
    Kurallar admin panelden "önizle" (dry-run) ve "uygula" ile çalıştırılır.
    Not: percent/fixed mevcut fiyata göredir; tekrar uygulanırsa birikir
    (önizleme bunu gösterir). margin/round_99 idempotenttir.
    """

    __tablename__ = "pricing_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    scope_type: Mapped[str] = mapped_column(String(16), default="all")
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy: Mapped[str] = mapped_column(String(16), default="percent")
    value: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    min_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)  # taban koruma
    max_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)  # tavan koruma
    only_in_stock: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_affected: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
