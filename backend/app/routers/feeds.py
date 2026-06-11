"""Google Merchant / Meta Catalog ürün feed'i (RSS 2.0 + g: namespace).

GET /api/feeds/google-merchant.xml

Google Merchant Center ve Meta Commerce Manager bu URL'i zamanlanmış olarak
çeker; ürünler reklam kataloglarında fiyat/stok güncel kalır. Aynı format iki
platformda da geçerli. 1 saat in-memory cache (feed botları sık çeker).
"""

from __future__ import annotations

import re
import time
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Product

router = APIRouter(prefix="/api/feeds", tags=["feeds"])
settings = get_settings()

_CACHE_TTL_SEC = 3600
_cache: dict = {"xml": None, "at": 0.0}


def _abs_url(u: str | None) -> str:
    """Göreceli upload yolunu mutlak URL'e çevir (feed mutlak ister)."""
    if not u:
        return ""
    if u.startswith(("http://", "https://")):
        return u
    base = (settings.api_public_url or settings.store_public_url).rstrip("/")
    return f"{base}{u}" if u.startswith("/") else f"{base}/{u}"


def _plain_text(s: str | None) -> str:
    return re.sub(r"<[^>]+>", " ", s or "").strip()


@router.get("/google-merchant.xml")
async def google_merchant_feed(db: AsyncSession = Depends(get_db)):
    now = time.time()
    if _cache["xml"] and now - _cache["at"] < _CACHE_TTL_SEC:
        return Response(content=_cache["xml"], media_type="application/xml")

    store = settings.store_public_url.rstrip("/")
    rows = (
        (await db.execute(select(Product).where(Product.is_active.is_(True)))).scalars().all()
    )
    items: list[str] = []
    for p in rows:
        imgs = [u for u in (p.images or []) if u]
        image = _abs_url(imgs[0]) if imgs else ""
        if not image:
            continue  # Google görselsiz ürünü reddeder — feed'e koyma
        price = float(p.price)
        old = float(p.old_price) if p.old_price else None
        desc = _plain_text(p.description) or (p.sub or p.name)
        avail = "in_stock" if (p.stock or 0) > 0 else "out_of_stock"
        parts = [
            "<item>",
            f"<g:id>{p.id}</g:id>",
            f"<g:title>{escape((p.name or '')[:150])}</g:title>",
            f"<g:description>{escape(desc[:4900])}</g:description>",
            f"<g:link>{escape(f'{store}/product?id={p.id}')}</g:link>",
            f"<g:image_link>{escape(image)}</g:image_link>",
        ]
        for extra in imgs[1:6]:
            parts.append(f"<g:additional_image_link>{escape(_abs_url(extra))}</g:additional_image_link>")
        if old and old > price:
            parts.append(f"<g:price>{old:.2f} TRY</g:price>")
            parts.append(f"<g:sale_price>{price:.2f} TRY</g:sale_price>")
        else:
            parts.append(f"<g:price>{price:.2f} TRY</g:price>")
        parts += [
            f"<g:availability>{avail}</g:availability>",
            "<g:condition>new</g:condition>",
            "<g:brand>TecnoTools</g:brand>",
            # GTIN/MPN yok — Google bunun beyanını ister
            "<g:identifier_exists>false</g:identifier_exists>",
            "</item>",
        ]
        items.append("".join(parts))

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0"><channel>'
        "<title>TecnoTools</title>"
        f"<link>{escape(store)}</link>"
        "<description>TecnoTools — Profesyonel el aletleri ve ekipmanlar</description>"
        + "".join(items)
        + "</channel></rss>"
    )
    _cache["xml"] = xml
    _cache["at"] = now
    return Response(content=xml, media_type="application/xml")
