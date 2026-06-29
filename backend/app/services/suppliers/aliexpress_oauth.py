"""AliExpress OAuth2.0 (Server-side) — yetkilendirme + token saklama + otomatik yenileme.

Akış:
  1) authorize_url() → kullanıcı bu linkte AliExpress hesabını yetkilendirir
  2) AliExpress, redirect_uri'ye ?code=... ile döner; admin bu code'u exchange_code'a verir
  3) access_token + refresh_token oauth_tokens tablosuna (GİZLİ) kaydedilir
  4) get_valid_token() çağrıldığında süresi dolmak üzereyse refresh_token ile yenilenir

İmza: IOP sistem API'leri (path'li) → sign = HMAC-SHA256(secret, path + sorted(k+v)).
"""

from __future__ import annotations

import hashlib
import hmac
import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import OAuthToken

settings = get_settings()

REST = "https://api-sg.aliexpress.com/rest"
AUTHORIZE = "https://api-sg.aliexpress.com/oauth/authorize"
PROVIDER = "aliexpress"


def _redirect_uri() -> str:
    return settings.api_public_url or "https://api.tecnotools.org"


def _sign(path: str, params: dict[str, str], secret: str) -> str:
    base = path + "".join(f"{k}{params[k]}" for k in sorted(params))
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest().upper()


def authorize_url() -> str:
    return (
        f"{AUTHORIZE}?response_type=code&force_auth=true"
        f"&redirect_uri={_redirect_uri()}&client_id={settings.aliexpress_app_key}"
    )


async def _token_call(path: str, extra: dict[str, str]) -> dict:
    params = {
        "app_key": settings.aliexpress_app_key,
        "timestamp": str(int(time.time() * 1000)),
        "sign_method": "sha256",
    }
    params.update(extra)
    params["sign"] = _sign(path, params, settings.aliexpress_app_secret)
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(REST + path, data=params)
        r.raise_for_status()
        return r.json()


async def _store(db: AsyncSession, data: dict) -> dict:
    if "access_token" not in data:
        raise RuntimeError(f"Token alınamadı: {data}")
    expire_at = int(time.time()) + int(data.get("expires_in") or 0)
    row = (
        await db.execute(select(OAuthToken).where(OAuthToken.provider == PROVIDER))
    ).scalar_one_or_none()
    if not row:
        row = OAuthToken(provider=PROVIDER)
        db.add(row)
    row.access_token = data["access_token"]
    if data.get("refresh_token"):
        row.refresh_token = data["refresh_token"]
    row.expire_at = expire_at
    await db.commit()
    return data


async def exchange_code(db: AsyncSession, code: str) -> dict:
    """authorize sonrası gelen code'u access_token'a çevirir ve saklar."""
    data = await _token_call("/auth/token/security/create", {"code": code})
    if "error_response" in data:
        raise RuntimeError(f"AliExpress token hatası: {data['error_response']}")
    return await _store(db, data)


async def refresh(db: AsyncSession, refresh_token: str) -> dict:
    data = await _token_call("/auth/token/refresh", {"refresh_token": refresh_token})
    if "error_response" in data:
        raise RuntimeError(f"AliExpress refresh hatası: {data['error_response']}")
    return await _store(db, data)


async def get_valid_token(db: AsyncSession) -> str | None:
    """Geçerli access_token döndürür; bitimine <1 gün kalmışsa refresh_token ile yeniler.
    Env'de ALIEXPRESS_ACCESS_TOKEN varsa onu (manuel override) öne alır."""
    if settings.aliexpress_access_token:
        return settings.aliexpress_access_token
    row = (
        await db.execute(select(OAuthToken).where(OAuthToken.provider == PROVIDER))
    ).scalar_one_or_none()
    if not row or not row.access_token:
        return None
    if row.expire_at and time.time() > row.expire_at - 86400 and row.refresh_token:
        try:
            data = await refresh(db, row.refresh_token)
            return data["access_token"]
        except Exception:
            return row.access_token  # yenileme başarısızsa eldekini dene
    return row.access_token
