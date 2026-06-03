"""Test fixtures — SQLite memory DB, izole, hızlı.

Production'da PostgreSQL kullanılır; testler için aiosqlite yeterli.
JSONB tipi modeller `JSONType` ile SQLite'da JSON'a düşer."""

import os
import sys
from pathlib import Path

# Test env değişkenleri (settings'i ilk import'tan önce override)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-min-32-characters-long-please")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INITIAL_ADMIN_USERNAME", "testadmin")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "TestPass123!")
os.environ.setdefault("INITIAL_ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("SMTP_HOST", "")  # dev modu — email konsola
os.environ.setdefault("STORE_PUBLIC_URL", "http://localhost:5500")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:5500")
os.environ.setdefault("PAYTR_MERCHANT_ID", "")  # dev mock token kullanılacak
os.environ.setdefault("PAYTR_MERCHANT_KEY", "")
os.environ.setdefault("PAYTR_MERCHANT_SALT", "")

# Backend kökünü sys.path'e ekle
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.database as db_module
from app.database import Base
from app.main import app
from app.models import User, UserRole
from app.security import hash_password


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    """Her test için izole SQLite memory engine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    # FK CASCADE için PRAGMA'yı her bağlantıda aç (SQLite varsayılanı OFF)
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Modülün global'inde de override (app içindeki SessionLocal bu engine'i kullansın)
    db_module.engine = engine
    db_module.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine):
    async with db_module.SessionLocal() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def seed_admin(db_session):
    """Default admin kullanıcısı."""
    admin = User(
        username="testadmin",
        email="admin@test.local",
        password_hash=hash_password("TestPass123!"),
        role=UserRole.ADMIN.value,
        is_primary=True,
    )
    db_session.add(admin)
    await db_session.commit()
    return admin


@pytest_asyncio.fixture(scope="function")
async def client(db_engine):
    """ASGI test client — gerçek FastAPI app'i."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(scope="function")
async def auth_client(db_engine, seed_admin):
    """Login olmuş AsyncClient — Authorization header otomatik set.

    Test'ten gelen `client` parametresinden bağımsız olmalı, yoksa header
    mutation'ı `client`'a sızıp "auth gerekli" test'lerini yanıltabilir.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/auth/login",
            json={
                "username": "testadmin",
                "password": "TestPass123!",
            },
        )
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        token = resp.json()["access_token"]
        ac.headers["Authorization"] = f"Bearer {token}"
        yield ac
