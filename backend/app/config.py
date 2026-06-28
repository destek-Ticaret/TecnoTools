from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Production'da kullanılması yasak varsayılan/zayıf değerler.
_INSECURE_SECRETS = {"dev-secret-change-me", "", "change-me", "secret"}
_INSECURE_ADMIN_PASSWORDS = {"ChangeMeOnFirstLogin!", "", "admin", "password"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_secret_key: str = "dev-secret-change-me"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 30

    database_url: str = "postgresql+asyncpg://tecnotools:tecnotools@localhost:5432/tecnotools"

    cors_origins: str = "http://localhost:5500,http://127.0.0.1:5500"

    # Ödeme sağlayıcı seçimi: "paytr" | "stripe"
    payment_provider: str = "paytr"

    paytr_merchant_id: str = ""
    paytr_merchant_key: str = ""
    paytr_merchant_salt: str = ""
    paytr_test_mode: int = 1
    paytr_notification_url: str = "http://localhost:8000/api/payments/paytr/callback"
    paytr_ok_url: str = "http://localhost:5500/order-success.html"
    paytr_fail_url: str = "http://localhost:5500/order-fail.html"

    # Stripe
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_success_url: str = (
        "http://localhost:5500/order-success.html?session_id={CHECKOUT_SESSION_ID}"
    )
    stripe_cancel_url: str = "http://localhost:5500/order-fail.html"

    # iyzico — taksit gösterimi + (ileride) ödeme. Boşsa MOCK modu: gerçekçi
    # sahte taksit planları döner (canlı API çağrısı yapılmaz).
    iyzico_api_key: str = ""
    iyzico_secret_key: str = ""
    iyzico_base_url: str = "https://sandbox-api.iyzipay.com"  # canlı: https://api.iyzipay.com

    # Dropshipping / tedarikçi entegrasyonu.
    # supplier_mode: "mock" (sahte örnek veri) | "live" (gerçek API).
    # Kaynak (aliexpress | 1688) ürün linkinin domaininden otomatik tespit edilir.
    supplier_mode: str = "mock"
    aliexpress_app_key: str = ""
    aliexpress_app_secret: str = ""
    aliexpress_tracking_id: str = ""  # affiliate/dropship tracking id
    # 1688 / Alibaba Açık Platform (open.1688.com) erişimi
    onesixeighteight_app_key: str = ""
    onesixeighteight_app_secret: str = ""
    # Varsayılan kâr marjı çarpanı: satış fiyatı = tedarikçi fiyatı * markup.
    # 1.8 = %80 kâr. Ürün bazında override edilebilir.
    dropship_markup: float = 1.8
    # Satış fiyatını ".90" gibi psikolojik sona yuvarla (örn 179.90).
    dropship_price_ending: float = 0.90

    initial_admin_username: str = "TecnoTools"
    initial_admin_password: str = "ChangeMeOnFirstLogin!"
    initial_admin_email: str = "admin@tecnotools.local"

    # Storage backend: "local" veya "s3"
    storage_backend: str = "local"
    # Backend'in dışarıdan erişilen kökü (örn https://api.tecnotools.org).
    # S3 proxy modunda (S3_PUBLIC_BASE_URL boşken) upload URL'leri bununla üretilir —
    # r2.dev bazı ülkelerde (TR/BTK) engelli olduğundan görseller kendi domain'imizden servis edilir.
    api_public_url: str = ""
    s3_bucket: str = ""
    s3_region: str = "auto"
    s3_endpoint_url: str = (
        ""  # boş bırakılırsa AWS default; Cloudflare R2 / B2 / DO Spaces için doldur
    )
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_public_base_url: str = ""  # CDN/public URL prefix (örn https://cdn.tecnotools.com)

    # Resend (HTTPS e-posta API) — SMTP portları bloklu ortamlar (Railway vb.)
    # için. RESEND_API_KEY doluysa e-postalar Resend üzerinden gönderilir;
    # SMTP yok sayılır. Boşsa SMTP'ye, o da boşsa konsola düşülür.
    resend_api_key: str = ""

    # Veritabanı yedeği — günde 1 kez pg_dump → R2 (yalnız storage_backend=s3 iken).
    # Gün-bazlı dosya adı idempotent: aynı gün ikinci çalıştırma (2. worker/redeploy) atlanır.
    db_backup_enabled: bool = True
    db_backup_keep: int = 14  # tutulacak günlük yedek sayısı, eskiler silinir

    # SMTP / Email — boş bırakılırsa email'ler konsola yazılır (dev mode)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@tecnotools.local"
    smtp_from_name: str = "TecnoTools"
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    store_public_url: str = "http://localhost:5500"

    # Para birimleri — fiyatlar BASE_CURRENCY'de saklanır, diğerlerine çevrilerek gösterilir
    base_currency: str = "TRY"
    supported_currencies: str = "TRY,USD,EUR"
    exchange_rate_provider: str = "frankfurter"  # frankfurter.app (ücretsiz, ECB)
    exchange_rate_cache_seconds: int = 21600  # 6 saat

    @property
    def supported_currency_list(self) -> list[str]:
        return [c.strip().upper() for c in self.supported_currencies.split(",") if c.strip()]

    # Sentry — boşsa devre dışı
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1
    sentry_profiles_sample_rate: float = 0.0

    # Datadog APM — boşsa devre dışı
    dd_service: str = "tecnotools-api"
    dd_env: str = "development"
    dd_version: str = "1.0.0"
    dd_agent_host: str = ""
    dd_trace_agent_port: int = 8126

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ── Kargo firma entegrasyonu (PTT) ──────────────────────────────────────
    # Credential boşsa adapter mock moda düşer (deterministik 3 event).
    # PTT Kurumsal "Gönderi Takip" SOAP servisi.
    ptt_username: str = ""
    ptt_password: str = ""
    ptt_customer_code: str = ""
    ptt_wsdl_url: str = "https://kurumsalwebservis.ptt.gov.tr/gonderitakip.asmx?wsdl"
    ptt_webhook_secret: str = ""  # X-PTT-Signature: hex(hmac_sha256(secret, body))
    # Diğer firmalar — şimdilik webhook (event push) + mock fetch. Gerçek API
    # polling'i kimlik bilgileri geldiğinde her adapter'ın _fetch_real()'ine eklenir.
    # Webhook secret boşsa imza doğrulaması atlanır (firma push'u açık kabul edilir).
    # DHL Lojistik (aktif kargo firması)
    dhl_api_key: str = ""  # DHL Developer Portal → API key
    dhl_webhook_secret: str = ""  # (opsiyonel) X-DHL-Signature HMAC secret
    aras_webhook_secret: str = ""
    yurtici_webhook_secret: str = ""
    mng_webhook_secret: str = ""
    surat_webhook_secret: str = ""
    hepsijet_webhook_secret: str = ""
    # Gerçek API polling kimlikleri (boşsa ilgili adapter fetch()'i mock döner).
    # Aras: arascargoservice.asmx GetCargoTransaction(userName, password, code, integrationCode)
    aras_username: str = ""
    aras_password: str = ""
    aras_integration_code: str = ""  # opsiyonel müşteri entegrasyon referansı
    aras_tracking_url: str = "https://customerws.araskargo.com.tr/arascargoservice.asmx"
    # Yurtiçi: queryShipmentDetail(wsUserName, wsPassword, wsLanguage, keys, keyType, ...)
    yurtici_username: str = ""
    yurtici_password: str = ""
    yurtici_tracking_url: str = (
        "https://webservices.yurticikargo.com/KOPSWebServices/ShippingOrderDispatcherServices"
    )
    # Genel poll ayarları
    shipment_poll_interval_minutes: int = 60
    shipment_poll_max_age_days: int = 21

    # Admin IP whitelist — login + admin endpoint'leri sadece bu IP'lerden erişilebilir.
    # Boşsa kısıtlama yok. 127.0.0.1 ve ::1 her zaman izinlidir.
    admin_ip_whitelist: str = ""

    @property
    def admin_ip_list(self) -> list[str]:
        return [ip.strip() for ip in self.admin_ip_whitelist.split(",") if ip.strip()]

    @model_validator(mode="after")
    def _fix_database_url(self) -> "Settings":
        # Railway'in DATABASE_URL'si postgresql:// ile gelir; asyncpg postgresql+asyncpg:// ister.
        if self.database_url.startswith("postgresql://"):
            object.__setattr__(self, "database_url",
                               "postgresql+asyncpg://" + self.database_url[len("postgresql://"):])
        return self

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> "Settings":
        """Production'da zayıf/varsayılan secret ile başlamayı engelle (fail-fast).

        Aksi halde APP_SECRET_KEY set edilmezse bilinen varsayılan secret ile
        çalışılır → JWT taklidi (forgery) mümkün olurdu."""
        if self.app_env.lower() != "production":
            return self
        problems = []
        if self.app_secret_key in _INSECURE_SECRETS or len(self.app_secret_key) < 32:
            problems.append(
                "APP_SECRET_KEY zayıf/varsayılan (≥32 karakter rastgele bir değer gerekli)"
            )
        if self.initial_admin_password in _INSECURE_ADMIN_PASSWORDS:
            problems.append("INITIAL_ADMIN_PASSWORD varsayılan/zayıf")
        if problems:
            raise ValueError("Production yapılandırması güvensiz: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
