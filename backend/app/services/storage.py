"""Pluggable storage backend.

Local: diske yazar, /api/uploads/files/{filename} ile sunar.
S3:    S3-compatible bucket'a yükler, public URL döner.

Üretimde S3'e geçiş için sadece STORAGE_BACKEND=s3 ve S3_* env'leri doldur.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings

settings = get_settings()

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


class StorageBackend(Protocol):
    async def save(self, data: bytes, ext: str, content_type: str) -> str:
        """Veriyi sakla, public erişim URL'sini döndür (göreceli veya tam)."""
        ...


class LocalStorage:
    async def save(self, data: bytes, ext: str, content_type: str) -> str:
        digest = hashlib.sha256(data).hexdigest()[:16]
        fname = f"{digest}{ext}"
        fpath = UPLOAD_DIR / fname
        if not fpath.exists():
            fpath.write_bytes(data)
        return f"/api/uploads/files/{fname}"


class S3Storage:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config as BotoConfig

        kwargs = {
            "service_name": "s3",
            "region_name": settings.s3_region or "auto",
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": settings.s3_secret_key,
            "config": BotoConfig(signature_version="s3v4"),
        }
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        self._client = boto3.client(**kwargs)
        self._bucket = settings.s3_bucket
        self._public_base = settings.s3_public_base_url.rstrip("/")

    async def save(self, data: bytes, ext: str, content_type: str) -> str:
        import asyncio

        digest = hashlib.sha256(data).hexdigest()[:16]
        key = f"uploads/{digest}{ext}"
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=2592000, immutable",
        )
        if self._public_base:
            return f"{self._public_base}/{key}"
        # Proxy modu: r2.dev bazı ülkelerde (TR/BTK) engelli — dosyayı kendi
        # API domain'imizden servis ederiz (serve_image R2'den çekip iletir).
        api_base = settings.api_public_url.rstrip("/")
        return f"{api_base}/api/uploads/files/{digest}{ext}"

    async def fetch(self, filename: str) -> bytes | None:
        """R2'den dosyayı oku (proxy servis için). Yoksa None."""
        import asyncio

        from botocore.exceptions import ClientError

        def _get() -> bytes | None:
            try:
                obj = self._client.get_object(Bucket=self._bucket, Key=f"uploads/{filename}")
                return obj["Body"].read()
            except ClientError:
                return None

        return await asyncio.to_thread(_get)


@lru_cache
def get_storage() -> StorageBackend:
    # lru_cache: boto3 client'ı her istekte yeniden kurmamak için tek instance.
    if settings.storage_backend == "s3":
        return S3Storage()
    return LocalStorage()
