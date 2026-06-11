"""Resim upload endpoint'i.

Storage backend pluggable (local / S3). Local'de FileResponse ile serve eder,
S3'te zaten public URL döner ve frontend doğrudan bucket'tan çeker.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from app.config import get_settings
from app.deps import require_editor
from app.models import User
from app.rate_limit import limiter
from app.services.storage import UPLOAD_DIR, get_storage

settings = get_settings()

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

MAX_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}
EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _detect_image_type(raw: bytes) -> str | None:
    """Dosya içeriğinin gerçek (magic-byte) türünü tespit et.

    Declared Content-Type başlığı client-kontrollü ve spoof'lanabilir; bu yüzden
    içeriğin gerçekten izin verilen bir resim formatı olduğunu byte imzasından
    doğruluyoruz (stored-XSS / polyglot dosya savunması)."""
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:4] == b"GIF8":  # GIF87a / GIF89a
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


@router.post("/images")
@limiter.limit("30/minute")
async def upload_image(
    request: Request, file: UploadFile = File(...), user: User = Depends(require_editor)
):
    if file.content_type not in ALLOWED:
        raise HTTPException(status_code=400, detail="Desteklenmeyen dosya türü")
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status_code=413, detail=f"Dosya {MAX_BYTES // (1024 * 1024)} MB sınırını aşıyor"
        )
    # İçeriği byte imzasından doğrula — declared Content-Type'a güvenme (spoof'lanabilir).
    actual_type = _detect_image_type(raw)
    if actual_type not in ALLOWED:
        raise HTTPException(status_code=400, detail="Dosya içeriği geçerli bir resim değil")
    # Uzantıyı tespit edilen (güvenilir) türden seç, declared header'dan değil.
    ext = EXT_BY_TYPE[actual_type]
    url = await get_storage().save(raw, ext, actual_type)
    return {"url": url, "size": len(raw)}


CONTENT_TYPE_BY_EXT = {v: k for k, v in EXT_BY_TYPE.items()}
# Dosya adları içerik hash'i (sha256) olduğundan içerik hiç değişmez → immutable cache.
_IMG_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "public, max-age=2592000, immutable",
}


@router.get("/files/{filename}")
async def serve_image(filename: str):
    """Yüklenen görselleri servis et.

    Local mode: diskten. S3 proxy modunda: önce disk önbelleği, yoksa R2'den
    çekilir ve diske önbelleklenir (r2.dev TR'de engelli olduğundan görseller
    bu endpoint üzerinden, kendi domain'imizden sunulur).
    """
    if "/" in filename or ".." in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Geçersiz dosya adı")
    fpath = UPLOAD_DIR / filename
    if fpath.exists() and fpath.is_file():
        # nosniff: tarayıcı içeriği declared type dışında yorumlamasın (stored-XSS savunması)
        return FileResponse(fpath, headers=_IMG_HEADERS)
    if settings.storage_backend == "s3":
        storage = get_storage()
        data = await storage.fetch(filename) if hasattr(storage, "fetch") else None
        if data:
            try:
                fpath.write_bytes(data)  # geçici disk önbelleği (deploy'da silinse de R2'de kalıcı)
            except OSError:
                pass
            media_type = CONTENT_TYPE_BY_EXT.get(fpath.suffix.lower(), "application/octet-stream")
            return Response(content=data, media_type=media_type, headers=_IMG_HEADERS)
    raise HTTPException(status_code=404, detail="Dosya bulunamadı")
