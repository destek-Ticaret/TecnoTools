"""SEO endpoint'leri: sitemap.xml, robots.txt, meta üretici, slug arama."""

from datetime import UTC, datetime
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Category, Product
from app.services.text_utils import slugify

router = APIRouter(tags=["seo"])
_cfg = get_settings()


@router.get("/sitemap.xml")
async def sitemap(db: AsyncSession = Depends(get_db)):
    """Aktif ürünleri + kategorileri + statik sayfaları içeren XML sitemap."""
    base = _cfg.store_public_url.rstrip("/")
    products = (
        await db.execute(
            select(Product.id, Product.name, Product.updated_at).where(Product.is_active == True)  # noqa: E712
        )
    ).all()
    categories = (await db.execute(select(Category))).scalars().all()

    now_iso = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    urls: list[str] = [
        f"<url><loc>{base}/</loc><lastmod>{now_iso}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>",
        f"<url><loc>{base}/legal/kvkk.html</loc><changefreq>yearly</changefreq><priority>0.3</priority></url>",
        f"<url><loc>{base}/legal/gizlilik.html</loc><changefreq>yearly</changefreq><priority>0.3</priority></url>",
        f"<url><loc>{base}/legal/mesafeli-satis.html</loc><changefreq>yearly</changefreq><priority>0.3</priority></url>",
    ]

    for cat in categories:
        slug = slugify(cat.name)
        urls.append(
            f"<url><loc>{escape(base)}/#category/{cat.id}/{slug}</loc>"
            f"<changefreq>weekly</changefreq><priority>0.7</priority></url>"
        )

    for pid, name, updated_at in products:
        lastmod = (
            (updated_at or datetime.now(UTC)).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        slug = slugify(name or f"urun-{pid}")
        urls.append(
            f"<url><loc>{escape(base)}/#product/{pid}/{slug}</loc>"
            f"<lastmod>{lastmod}</lastmod>"
            f"<changefreq>weekly</changefreq><priority>0.8</priority></url>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>"
    )
    return Response(content=body, media_type="application/xml")


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    base = _cfg.store_public_url.rstrip("/")
    return f"""User-agent: *
Disallow: /admin.html
Disallow: /api/

Sitemap: {base}/sitemap.xml
"""


@router.get("/api/seo/slug/{slug}")
async def resolve_slug(slug: str, db: AsyncSession = Depends(get_db)):
    """Slug → ürün id (URL'den slug ile gelen ziyaretçi için)."""
    normalized_slug = slugify(slug)
    rows = (
        await db.execute(select(Product.id, Product.name).where(Product.is_active == True))  # noqa: E712
    ).all()
    for pid, name in rows:
        if slugify(name or "") == normalized_slug:
            return {"product_id": int(pid), "name": name}
    raise HTTPException(404, "Slug eşleşmedi")


@router.get("/api/seo/meta/product/{product_id}")
async def product_meta(product_id: int, db: AsyncSession = Depends(get_db)):
    """SSR/SEO için JSON meta payload — title, description, OpenGraph, JSON-LD."""
    p = (await db.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if not p or not p.is_active:
        raise HTTPException(404, "Ürün bulunamadı")
    title = f"{p.name} | TecnoTools"
    desc_source = (p.description or p.sub or p.name or "").strip()
    desc = (desc_source[:155] + "…") if len(desc_source) > 156 else desc_source
    image = (p.images or [None])[0] if p.images else None
    base = _cfg.store_public_url.rstrip("/")
    url = f"{base}/#product/{p.id}/{slugify(p.name)}"
    structured = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": p.name,
        "description": desc,
        "image": image,
        "sku": f"TT-{p.id}",
        "offers": {
            "@type": "Offer",
            "url": url,
            "priceCurrency": _cfg.base_currency,
            "price": float(p.price),
            "availability": "https://schema.org/InStock"
            if (p.stock or 0) > 0
            else "https://schema.org/OutOfStock",
        },
        "aggregateRating": (
            {
                "@type": "AggregateRating",
                "ratingValue": float(p.rating or 0),
                "reviewCount": int(p.review_count or 0),
            }
            if (p.review_count or 0) > 0
            else None
        ),
    }
    if structured["aggregateRating"] is None:
        structured.pop("aggregateRating")
    return {
        "title": title,
        "description": desc,
        "canonical": url,
        "og": {
            "title": title,
            "description": desc,
            "type": "product",
            "image": image,
            "url": url,
        },
        "twitter": {
            "card": "summary_large_image",
            "title": title,
            "description": desc,
            "image": image,
        },
        "json_ld": structured,
    }
