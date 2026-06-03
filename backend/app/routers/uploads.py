"""Resim upload endpoint'i.

Storage backend pluggable (local / S3). Local'de FileResponse ile serve eder,
S3'te zaten public URL döner ve frontend doğrudan bucket'tan çeker.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.deps import require_editor
from app.models import User
from app.rate_limit import limiter
from app.services.storage import UPLOAD_DIR, get_storage

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


@router.get("/files/{filename}")
async def serve_image(filename: str):
    """Local storage için backend serve. S3 mode'da bu endpoint kullanılmaz."""
    if "/" in filename or ".." in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Geçersiz dosya adı")
    fpath = UPLOAD_DIR / filename
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Dosya bulunamadı")
    # nosniff: tarayıcı içeriği declared type dışında yorumlamasın (stored-XSS savunması)
    return FileResponse(fpath, headers={"X-Content-Type-Options": "nosniff"})
