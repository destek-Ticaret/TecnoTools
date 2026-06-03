"""Para birimi endpoint'leri."""
from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.services.currency import get_rate, is_supported

router = APIRouter(prefix="/api/currency", tags=["currency"])
settings = get_settings()


@router.get("")
async def list_currencies():
    """Desteklenen para birimleri + temel para birimi."""
    return {
        "base": settings.base_currency,
        "supported": settings.supported_currency_list,
    }


@router.get("/rate")
async def rate_endpoint(base: str = "", quote: str = ""):
    """`base` → `quote` kuru. Varsayılan: BASE_CURRENCY → quote."""
    src = (base or settings.base_currency).upper()
    dst = (quote or settings.base_currency).upper()
    if not is_supported(src) or not is_supported(dst):
        raise HTTPException(status_code=400, detail="Desteklenmeyen para birimi")
    r = await get_rate(src, dst)
    return {"base": src, "quote": dst, "rate": r}
