"""Öneri motoru — item-item co-occurrence + içerik bazlı benzerlik + trend.

Hızlı ve bağımlılığı olmayan bir yaklaşım:
- Co-occurrence: birlikte siparişe giren ürünleri sayar (Jaccard / cosine).
- Trend: son N gün satışları üzerinde exponential-decay ağırlığı.
- İçerik: aynı kategori + fiyat bandı (orta % 20 sapma).

Hesaplar online yapılır; çok büyük katalog için offline batch + cache mantığına
geçirilmelidir. Sonuçlar shared_cache'te 10 dk tutulur.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, OrderItem, PaymentStatus, Product
from app.services.cache import ttl_cache


async def _basket_index(db: AsyncSession, days: int = 180) -> dict[int, set[int]]:
    """Sipariş başına ürün setlerini döndür. Sadece ödemesi başarılı siparişler."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(OrderItem.order_id, OrderItem.product_id)
            .join(Order, OrderItem.order_id == Order.id)
            .where(
                and_(
                    Order.created_at >= since,
                    Order.payment_status == PaymentStatus.SUCCESS.value,
                    OrderItem.product_id.isnot(None),
                )
            )
        )
    ).all()
    baskets: dict[int, set[int]] = defaultdict(set)
    for oid, pid in rows:
        baskets[oid].add(int(pid))
    return baskets


@ttl_cache(ttl_seconds=600, max_size=8)
async def _cooccurrence_table(db: AsyncSession, days: int = 180) -> dict[int, Counter]:
    """{product_id: Counter({other_pid: birlikte sayısı})} — yön bağımsız."""
    baskets = await _basket_index(db, days=days)
    table: dict[int, Counter] = defaultdict(Counter)
    pop: Counter = Counter()
    for items in baskets.values():
        for pid in items:
            pop[pid] += 1
        items_l = list(items)
        for i in range(len(items_l)):
            for j in range(i + 1, len(items_l)):
                a, b = items_l[i], items_l[j]
                table[a][b] += 1
                table[b][a] += 1
    # popülarite haritasını __pop__ özel anahtarında sakla (cosine için gerekli)
    table["__pop__"] = pop  # type: ignore[index]
    return table


def _cosine(co_count: int, pop_a: int, pop_b: int) -> float:
    denom = math.sqrt(pop_a * pop_b)
    return co_count / denom if denom else 0.0


async def related_products(
    db: AsyncSession, product_id: int, limit: int = 8, days: int = 180
) -> list[tuple[int, float]]:
    """`product_id` için (other_id, skor) listesi — skor azalan."""
    table = await _cooccurrence_table(db, days=days)
    pop: Counter = table.get("__pop__", Counter())
    co = table.get(product_id, Counter())
    pop_a = pop.get(product_id, 0)
    if not co or pop_a == 0:
        return []
    scored = [
        (other, _cosine(cnt, pop_a, pop.get(other, 0)))
        for other, cnt in co.items()
        if other != product_id
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


async def frequently_bought_together(
    db: AsyncSession, basket_pids: list[int], limit: int = 5, days: int = 180
) -> list[tuple[int, float]]:
    """Sepet için "şunlar da sık birlikte alınıyor" — agregat skor."""
    if not basket_pids:
        return []
    table = await _cooccurrence_table(db, days=days)
    pop: Counter = table.get("__pop__", Counter())
    in_basket = set(basket_pids)
    aggregate: Counter = Counter()
    for pid in basket_pids:
        co = table.get(pid, Counter())
        pop_a = pop.get(pid, 0) or 1
        for other, cnt in co.items():
            if other in in_basket:
                continue
            aggregate[other] += _cosine(cnt, pop_a, pop.get(other, 0))
    return aggregate.most_common(limit)


async def content_similar(
    db: AsyncSession, product_id: int, limit: int = 8, price_band_pct: float = 0.25
) -> list[int]:
    """İçerik-bazlı benzerlik: aynı kategori + fiyat bandı (±%price_band_pct).

    Co-occurrence verisi yoksa veya cold-start için fallback.
    """
    target = (
        await db.execute(select(Product).where(Product.id == product_id))
    ).scalar_one_or_none()
    if not target:
        return []
    lo = float(target.price) * (1 - price_band_pct)
    hi = float(target.price) * (1 + price_band_pct)
    stmt = (
        select(Product.id)
        .where(
            Product.id != product_id,
            Product.is_active == True,  # noqa: E712
            Product.category_id == target.category_id,
            Product.price >= lo,
            Product.price <= hi,
        )
        .order_by(Product.rating.desc(), Product.review_count.desc())
        .limit(limit)
    )
    return [int(r[0]) for r in (await db.execute(stmt)).all()]


@ttl_cache(ttl_seconds=300, max_size=8)
async def trending_products(
    db: AsyncSession, days: int = 14, half_life_days: float = 5.0, limit: int = 12
) -> list[tuple[int, float]]:
    """Zaman-ağırlıklı popülerlik. Yarı-ömür `half_life_days` (5 gün varsayılan).

    skor = sum_per_sale( qty * 0.5 ^ (yaş / half_life) )
    """
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(OrderItem.product_id, OrderItem.qty, Order.created_at)
            .join(Order, OrderItem.order_id == Order.id)
            .where(
                and_(
                    Order.created_at >= since,
                    Order.payment_status == PaymentStatus.SUCCESS.value,
                    OrderItem.product_id.isnot(None),
                )
            )
        )
    ).all()
    now = datetime.now(UTC)
    lam = math.log(2) / max(half_life_days, 0.1)
    scores: Counter = Counter()
    for pid, qty, created_at in rows:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        age_days = (now - created_at).total_seconds() / 86400.0
        weight = math.exp(-lam * age_days)
        scores[int(pid)] += float(qty) * weight
    return scores.most_common(limit)


async def recommend_for_session(
    db: AsyncSession,
    viewed_pids: list[int] | None = None,
    basket_pids: list[int] | None = None,
    limit: int = 8,
) -> list[int]:
    """Karma strateji: önce sepet co-occurrence, sonra view içerik, sonra trend.

    Tek bir id listesi döndürür (UI'da "Sana Özel" şeridi için).
    """
    out: list[int] = []
    seen: set[int] = set(viewed_pids or []) | set(basket_pids or [])

    if basket_pids:
        for pid, _ in await frequently_bought_together(db, basket_pids, limit=limit):
            if pid not in seen:
                out.append(pid)
                seen.add(pid)

    if len(out) < limit and viewed_pids:
        for vp in viewed_pids[-3:]:  # son 3 görüntülenen
            sim = await content_similar(db, vp, limit=limit)
            for pid in sim:
                if pid not in seen:
                    out.append(pid)
                    seen.add(pid)
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break

    if len(out) < limit:
        for pid, _ in await trending_products(db, limit=limit):
            if pid not in seen:
                out.append(pid)
                seen.add(pid)
            if len(out) >= limit:
                break

    return out[:limit]
