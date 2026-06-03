"""Güvenlik sertleştirme testleri.

- Production'da zayıf/varsayılan secret ile başlatma engelleniyor mu?
- Devre dışı bırakılan admin'in mevcut token'ı reddediliyor mu?
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from app.config import Settings
from app.models import User


def test_production_rejects_default_secret():
    """app_env=production + varsayılan secret → ValidationError (fail-fast)."""
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            app_secret_key="dev-secret-change-me",
            initial_admin_password="StrongAdminPass!123",
        )


def test_production_rejects_short_secret():
    """32 karakterden kısa secret production'da reddedilir."""
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            app_secret_key="kisa",
            initial_admin_password="StrongAdminPass!123",
        )


def test_production_rejects_default_admin_password():
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            app_secret_key="x" * 40,
            initial_admin_password="ChangeMeOnFirstLogin!",
        )


def test_production_accepts_strong_config():
    s = Settings(
        app_env="production",
        app_secret_key="x" * 40,
        initial_admin_password="StrongAdminPass!123",
    )
    assert s.app_env == "production"


def test_development_allows_default_secret():
    """Geliştirme/test ortamında varsayılan secret kabul edilir (guard pasif)."""
    s = Settings(app_env="development", app_secret_key="dev-secret-change-me")
    assert s.app_env == "development"


async def test_deactivated_admin_token_rejected(auth_client, db_session):
    """Login sonrası kullanıcı devre dışı bırakılırsa mevcut token'ı 401 alır."""
    # Önce token çalışıyor
    ok = await auth_client.get("/api/auth/me")
    assert ok.status_code == 200
    # testadmin'i devre dışı bırak
    await db_session.execute(
        update(User).where(User.username == "testadmin").values(is_active=False)
    )
    await db_session.commit()
    # Aynı token artık reddedilmeli
    rejected = await auth_client.get("/api/auth/me")
    assert rejected.status_code == 401
