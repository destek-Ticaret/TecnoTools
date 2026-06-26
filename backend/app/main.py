import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.admin_ip_filter import AdminIPFilterMiddleware
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import User, UserRole
from app.rate_limit import limiter
from app.routers import (
    admin_users,
    algorithms,
    analytics,
    auth,
    banners,
    blog,
    campaigns,
    categories,
    chat,
    coupons,
    currency,
    customer_auth,
    events,
    exports,
    feeds,
    homepage,
    imports,
    invoices,
    misc,
    orders,
    pages,
    payments,
    pricing_rules,
    privacy,
    products,
    questions,
    reservations,
    returns,
    reviews,
    seo,
    shipping,
    stock_notifications,
    uploads,
    wishlist,
    ws,
)
from app.routers import (
    settings as settings_router,
)
from app.security import hash_password

settings = get_settings()

# Geliştirme modunda app.* logger'larını konsola bas — özellikle dev e-postaları
# (verify-email, parola sıfırlama) için tüketilebilir log lazım.
if settings.app_env != "production" and not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

# ── Datadog APM (varsa) — auto-instrument fastapi, sqlalchemy, httpx, asyncpg ──
if settings.dd_agent_host:
    import os as _os

    _os.environ.setdefault("DD_SERVICE", settings.dd_service)
    _os.environ.setdefault("DD_ENV", settings.dd_env)
    _os.environ.setdefault("DD_VERSION", settings.dd_version)
    _os.environ.setdefault("DD_AGENT_HOST", settings.dd_agent_host)
    _os.environ.setdefault("DD_TRACE_AGENT_PORT", str(settings.dd_trace_agent_port))
    try:
        from ddtrace import patch_all

        patch_all()
    except Exception:
        pass

# ── Sentry init — DSN varsa hata + performans izleme aktif ──
if settings.sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        integrations=[FastApiIntegration(transaction_style="endpoint"), SqlalchemyIntegration()],
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        # PII korumalı — kullanıcı adı, IP gibi alanları otomatik göndermez
        send_default_pii=False,
        attach_stacktrace=True,
        # Tahmin edilebilir 404'leri ve rate-limit 429'ları filtrele
        before_send=lambda event, hint: (
            None if event.get("transaction") in ("/api/health", "/api/events") else event
        ),
    )


async def _seed_initial_admin():
    """İlk admin kullanıcısı veritabanında yoksa oluştur."""
    async with SessionLocal() as db:
        existing = (
            await db.execute(select(User).where(User.username == settings.initial_admin_username))
        ).scalar_one_or_none()
        if existing:
            return
        admin = User(
            username=settings.initial_admin_username,
            email=settings.initial_admin_email,
            password_hash=hash_password(settings.initial_admin_password),
            role=UserRole.ADMIN.value,
            is_primary=True,
        )
        db.add(admin)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Üretimde tablolar Alembic ile yönetilir. Geliştirme/test rahatlığı için
    # production dışında create_all çalıştırılır (idempotent).
    if settings.app_env != "production":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    await _seed_initial_admin()
    # Periyodik görevler (düşük stok, terk sepet)
    from app.services.scheduled import start_scheduler, stop_scheduler

    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title="TecnoTools API", version="1.0.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# Auth: Bearer token (Authorization header) kullanılıyor — cookie yok.
# allow_credentials=True + allow_headers=["*"] Starlette'de çakışır;
# Bearer token için credentials=False yeterli.
_cors_origins = settings.cors_origin_list
if settings.app_env == "development":
    _cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(AdminIPFilterMiddleware, allowed_ips=settings.admin_ip_list)

app.include_router(auth.router)
app.include_router(customer_auth.router)
app.include_router(products.router)
app.include_router(categories.router)
app.include_router(orders.router)
app.include_router(coupons.router)
app.include_router(payments.router)
app.include_router(reservations.router)
app.include_router(returns.router)
app.include_router(uploads.router)
app.include_router(admin_users.router)
app.include_router(events.router)
app.include_router(ws.router)
app.include_router(campaigns.router)
app.include_router(analytics.router)
app.include_router(currency.router)
app.include_router(settings_router.router)
app.include_router(seo.router)
app.include_router(reviews.router)
app.include_router(stock_notifications.router)
app.include_router(exports.router)
app.include_router(feeds.router)
app.include_router(algorithms.router)
app.include_router(misc.router)
app.include_router(chat.router)
app.include_router(invoices.router)
app.include_router(privacy.router)
app.include_router(shipping.router)
app.include_router(wishlist.router)
app.include_router(questions.router)
app.include_router(banners.router)
app.include_router(homepage.router)
app.include_router(blog.router)
app.include_router(pages.router)
app.include_router(pricing_rules.router)
app.include_router(imports.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "env": settings.app_env}


@app.get("/api/debug/paytr")
async def debug_paytr():
    import base64, hashlib, hmac as hmac_mod, json as json_mod, urllib.parse, urllib.request, urllib.error
    mid = settings.paytr_merchant_id
    key = settings.paytr_merchant_key
    salt = settings.paytr_merchant_salt

    basket = [["Test", "1.00", 1]]
    basket_b64 = base64.b64encode(json_mod.dumps(basket, separators=(",", ":")).encode()).decode()
    user_ip = "1.2.3.4"
    merchant_oid = "DEBUGTEST001"
    email = "debug@tecnotools.org"
    amount = 100

    hash_str = mid + user_ip + merchant_oid + email + str(amount) + basket_b64 + "0" + "0" + "TL" + str(settings.paytr_test_mode)
    hmac_key = (key + salt).encode()
    token = base64.b64encode(hmac_mod.new(hmac_key, hash_str.encode(), hashlib.sha256).digest()).decode()

    data = {
        "merchant_id": mid, "user_ip": user_ip, "merchant_oid": merchant_oid,
        "email": email, "payment_amount": str(amount), "paytr_token": token,
        "user_basket": basket_b64, "debug_on": "1", "no_installment": "0", "max_installment": "0",
        "user_name": "Debug Test", "user_address": "Istanbul",
        "user_phone": "05001234567",
        "merchant_ok_url": settings.paytr_ok_url,
        "merchant_fail_url": settings.paytr_fail_url,
        "timeout_limit": "30", "currency": "TL", "test_mode": str(settings.paytr_test_mode),
    }
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        "https://www.paytr.com/odeme/api/get-token", data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode()
        status = 200
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        status = e.code

    return {
        "paytr_status": status,
        "paytr_body": body,
        "hash_str_preview": hash_str[:60],
        "token_preview": token[:20],
    }
