"""Gece veritabanı yedeği — pg_dump (custom format) → R2/S3.

Zamanlayıcı periyodik çağırır; gün-bazlı dosya adı sayesinde idempotenttir:
bugünün yedeği bucket'ta zaten varsa (ikinci gunicorn worker'ı, redeploy vb.)
hiçbir şey yapılmaz. Saklama: son `db_backup_keep` yedek tutulur, eskiler silinir.

Geri yükleme (felaket günü):
    pg_restore --clean --no-owner --dbname="postgresql://..." tecnotools-YYYYMMDD.dump
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_PREFIX = "backups/"


def _boto_client():
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
    return boto3.client(**kwargs)


def _exists(client, key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=settings.s3_bucket, Key=key)
        return True
    except ClientError:
        return False


def _prune(client, keep: int) -> None:
    """En yeni `keep` yedek kalsın, gerisini sil (adlar tarihli → sort yeterli)."""
    if keep <= 0:
        return
    resp = client.list_objects_v2(Bucket=settings.s3_bucket, Prefix=_PREFIX)
    keys = sorted(o["Key"] for o in resp.get("Contents", []))
    for key in keys[:-keep]:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
        logger.info("Eski DB yedeği silindi: %s", key)


async def run_db_backup() -> bool:
    """Bugünün yedeği yoksa pg_dump alıp R2'ye yükler. True = yeni yedek alındı."""
    if not settings.db_backup_enabled or settings.storage_backend != "s3":
        return False
    key = f"{_PREFIX}tecnotools-{datetime.now(UTC):%Y%m%d}.dump"
    client = await asyncio.to_thread(_boto_client)
    if await asyncio.to_thread(_exists, client, key):
        return False

    # pg_dump asyncpg driver'ını bilmez — düz postgresql:// DSN ister.
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    tmp = Path(tempfile.gettempdir()) / Path(key).name
    try:
        proc = await asyncio.create_subprocess_exec(
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--dbname={dsn}",
            f"--file={tmp}",
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.error("pg_dump bulunamadı — Dockerfile'da postgresql-client kurulu olmalı")
        return False
    _, err = await proc.communicate()
    try:
        if proc.returncode != 0:
            logger.error(
                "pg_dump başarısız (rc=%s): %s",
                proc.returncode,
                err.decode(errors="replace")[-500:],
            )
            return False
        await asyncio.to_thread(
            client.upload_file,
            str(tmp),
            settings.s3_bucket,
            key,
            ExtraArgs={"ContentType": "application/octet-stream"},
        )
        logger.info("DB yedeği yüklendi: %s (%d bayt)", key, tmp.stat().st_size)
        await asyncio.to_thread(_prune, client, settings.db_backup_keep)
        return True
    finally:
        tmp.unlink(missing_ok=True)
