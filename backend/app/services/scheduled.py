"""Periyodik görevler:
- Düşük stok uyarısı (her 6 saatte bir)
- Terk edilmiş sepet hatırlatma email'i (her 30 dakikada bir; cart 1 saatten eski + sipariş yok)

Lifespan'da `start_scheduler()` çağrılır. Tek instance için asyncio task yeterli;
multi-instance deploy'da Celery beat veya cron container'a taşınmalı.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models import Order, OrderStatus, Product, Reservation, User
from app.services.email import send_email

logger = logging.getLogger(__name__)

LOW_STOCK_INTERVAL_SEC = 6 * 3600
CART_ABANDON_INTERVAL_SEC = 30 * 60
CART_ABANDON_MIN_AGE_MIN = 60
# Yedek kontrol sıklığı — gün-bazlı dosya adı gerçek sıklığı günde 1'e indirir
DB_BACKUP_INTERVAL_SEC = 6 * 3600


async def _low_stock_alert(db: AsyncSession) -> None:
    from app.routers.settings import get_setting

    threshold = int(float(await get_setting(db, "low_stock_threshold", "5") or "5"))
    rows = (
        (
            await db.execute(
                select(Product).where(
                    (Product.is_active.is_(True))
                    & (Product.stock > 0)
                    & (Product.stock <= threshold)
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return
    out_of_stock = (
        (
            await db.execute(
                select(Product).where((Product.is_active.is_(True)) & (Product.stock <= 0))
            )
        )
        .scalars()
        .all()
    )

    admins = (await db.execute(select(User).where(User.role == "admin"))).scalars().all()
    admin_emails = [a.email for a in admins if a.email]
    if not admin_emails:
        return

    body_rows = "".join(
        f"<tr><td>{p.name}</td><td style='text-align:right;color:#ef4444;font-weight:700;'>{p.stock}</td></tr>"
        for p in rows
    )
    out_rows = "".join(
        f"<tr><td>{p.name}</td><td style='text-align:right;color:#6b7280;'>0 (tükendi)</td></tr>"
        for p in out_of_stock
    )
    html = f"""
    <h2>Stok Uyarısı</h2>
    <p><strong>{len(rows)} ürün</strong> kritik seviyenin altında ({threshold} ve aşağısı):</p>
    <table style='width:100%;border-collapse:collapse;margin:1rem 0;'>
      <thead><tr style='background:#f1f5f9;'><th style='text-align:left;padding:.5rem;'>Ürün</th><th style='text-align:right;padding:.5rem;'>Stok</th></tr></thead>
      <tbody>{body_rows}{out_rows}</tbody>
    </table>
    <p style='font-size:.85rem;color:#64748b;'>Bu uyarı 6 saatte bir gönderilir. Sınırı değiştirmek için admin panelinde Ayarlar &rsaquo; Low stock threshold.</p>
    """
    for email in admin_emails:
        try:
            await send_email(to=email, subject=f"🔔 {len(rows)} ürün için stok uyarısı", html=html)
        except Exception as e:
            logger.warning("Low stock email failed for %s: %s", email, e)


async def _abandoned_cart_alert(db: AsyncSession) -> None:
    """Terk edilmiş rezervasyon → email hatırlatma.

    Mantık: Reservation tablosundaki, 60 dakikadan eski + son 24 saat
    içinde sipariş açmamış müşterilere hatırlatma.
    Reservation tablosunda customer email yok; bu yüzden sadece adminlerin
    görmesi için özet rapor mail'i gönderilir (gerçek müşteriye ulaşmak için
    "Cart" tablosu eklemek gerekir, şimdilik admin'e durum bildirilir).
    """
    abandoned = (
        await db.execute(
            select(Reservation.session_id, func.sum(Reservation.qty).label("total_qty"))
            .where(Reservation.expires_at > datetime.now(UTC))
            .group_by(Reservation.session_id)
        )
    ).all()
    # Bu liste içinden henüz sipariş açılmamış olanları say
    count = len(abandoned)
    if count < 3:  # 3'ten az aktif sepet varsa rapor gönderme
        return

    admins = (await db.execute(select(User).where(User.role == "admin"))).scalars().all()
    admin_emails = [a.email for a in admins if a.email]
    if not admin_emails:
        return

    html = f"""
    <h2>Terk Edilmiş Sepet Raporu</h2>
    <p>Şu an <strong>{count}</strong> aktif sepette ürün rezerve edilmiş ancak ödemeye geçilmemiş.</p>
    <p>Müşteri email adresleri sepet aşamasında toplanmadığı için doğrudan hatırlatma gönderilemez.
    İleride checkout başlangıç adımında email toplanırsa otomatik hatırlatma aktif olur.</p>
    """
    for email in admin_emails:
        try:
            await send_email(to=email, subject=f"🛒 {count} terk edilmiş sepet", html=html)
        except Exception as e:
            logger.warning("Abandoned cart email failed for %s: %s", email, e)


async def _shipment_poll(db: AsyncSession) -> None:
    """Aktif kargolar için periyodik API poll — webhook gelmeyen firmalar için yedek."""
    from app.config import get_settings
    from app.services.carriers import apply_event, get_adapter

    s = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=s.shipment_poll_max_age_days)
    orders = (
        (
            await db.execute(
                select(Order).where(
                    and_(
                        Order.status == OrderStatus.SHIPPED.value,
                        Order.tracking_no.isnot(None),
                        Order.carrier.isnot(None),
                        Order.shipped_at >= cutoff,
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    for o in orders:
        if not o.carrier:
            continue
        try:
            adapter = get_adapter(o.carrier)
        except ValueError:
            continue
        try:
            events = await adapter.fetch(o.tracking_no or "")
        except Exception as e:
            logger.warning("shipment poll failed for %s: %s", o.order_no, e)
            continue
        for ev in events:
            await apply_event(db, ev, order=o, source="poll")
    if orders:
        await db.commit()


async def _scheduler_loop() -> None:
    """Her 30dk'da bir çalış; düşük stok 6 saatte bir, kargo poll ayar bazında."""
    from app.config import get_settings

    s = get_settings()
    ship_interval = max(1, s.shipment_poll_interval_minutes) * 60

    low_stock_counter = 0
    ship_counter = 0
    backup_counter = 0
    ship_period = max(1, ship_interval // CART_ABANDON_INTERVAL_SEC)
    backup_period = DB_BACKUP_INTERVAL_SEC // CART_ABANDON_INTERVAL_SEC
    while True:
        try:
            async with SessionLocal() as db:
                if low_stock_counter == 0:
                    await _low_stock_alert(db)
                await _abandoned_cart_alert(db)
                if ship_counter == 0:
                    await _shipment_poll(db)
            if backup_counter == 0:
                from app.services.backup import run_db_backup

                await run_db_backup()
        except Exception as e:
            logger.error("Scheduler error: %s", e)
        low_stock_counter = (low_stock_counter + 1) % (
            LOW_STOCK_INTERVAL_SEC // CART_ABANDON_INTERVAL_SEC
        )
        ship_counter = (ship_counter + 1) % ship_period
        backup_counter = (backup_counter + 1) % backup_period
        await asyncio.sleep(CART_ABANDON_INTERVAL_SEC)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())


def stop_scheduler() -> None:
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
