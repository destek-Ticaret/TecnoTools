"""Site ayarları endpoint'leri — key/value tabanlı, admin yönetir.

Tanımlı anahtarlar (env'den override edilebilir):
- shipping_free_threshold: float  (örn 500.0 — bu tutarın üstünde ücretsiz)
- shipping_fee_default:    float  (örn 49.9)
- low_stock_threshold:     int    (örn 5 — bu altı uyarı)
- store_iban:              str
- store_iban_holder:       str
- cod_enabled:             "0"|"1"  (kapıda ödeme)
- wire_enabled:            "0"|"1"  (havale)
- auto_invoice_enabled:    "0"|"1"  (ödeme onaylanınca otomatik e-arşiv fatura)
"""

from fastapi import APIRouter, Body, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin
from app.models import SiteSetting, User
from app.services.events import bus

router = APIRouter(prefix="/api/settings", tags=["settings"])

DEFAULTS = {
    "shipping_free_threshold": "500",
    "shipping_fee_default": "49.9",
    "low_stock_threshold": "5",
    "store_iban": "",
    "store_iban_holder": "TecnoTools Ltd. Şti.",
    "cod_enabled": "1",
    "wire_enabled": "1",
    "auto_invoice_enabled": "1",
}


async def get_setting(db: AsyncSession, key: str, default: str | None = None) -> str | None:
    row = (await db.execute(select(SiteSetting).where(SiteSetting.key == key))).scalar_one_or_none()
    if row and row.value is not None:
        return row.value
    return default if default is not None else DEFAULTS.get(key)


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    row = (await db.execute(select(SiteSetting).where(SiteSetting.key == key))).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(SiteSetting(key=key, value=value))


@router.get("")
async def list_settings(db: AsyncSession = Depends(get_db)):
    """Public — frontend tüm ayarları okur. Hiçbir gizli veri yok."""
    rows = (await db.execute(select(SiteSetting))).scalars().all()
    merged = dict(DEFAULTS)
    for r in rows:
        if r.value is not None:
            merged[r.key] = r.value
    return merged


@router.put("")
async def update_settings(
    payload: dict = Body(...), db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
):
    """Toplu güncelleme — sadece DEFAULTS'ta tanımlı anahtarlar kabul edilir."""
    updated = []
    for k, v in (payload or {}).items():
        if k not in DEFAULTS:
            continue
        await set_setting(db, k, str(v))
        updated.append(k)
    await db.commit()
    if updated:
        await bus.publish("settings_updated", {"keys": updated})
    return {"ok": True, "updated": updated}
