"""KVKK 11. madde — silme/unutulma + veri ihracı.

İki temel akış:

  • collect_customer_data(db, customer):
      Müşteriye dair tüm verileri tek JSON'a derler — taşınabilirlik hakkı için.

  • run_deletion(db, request):
      DataDeletionRequest "confirmed" iken çağrılır. Sırayla:
        1) Müşteriye bağlı siparişleri anonimleştirir
           (snapshot alanlar "[Silinmiş Müşteri]" olur, customer_id NULL).
           Faturalar mali kayıt — silinmez, sadece e-posta/telefon maskelenir.
        2) Refresh / reset token, chat session, stok bildirimi, yorum, newsletter
           aboneliği gibi tamamen kişisel kayıtları siler.
        3) Müşteri kaydını siler.

   Çıktı `result` dict'i her tablodan kaç satır etkilendiğini içerir; audit log
   için bu data_deletion_requests.result alanına yazılır.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditLog,
    ChatMessage,
    ChatSession,
    ConsentLog,
    Customer,
    CustomerPasswordResetToken,
    CustomerRefreshToken,
    DataDeletionRequest,
    DataDeletionStatus,
    Invoice,
    NewsletterSubscriber,
    Order,
    OrderStatus,
    PaymentStatus,
    ProductReview,
    ReturnRequest,
    StockNotification,
)

logger = logging.getLogger(__name__)

ANON_NAME = "[Silinmiş Müşteri]"
ANON_PHONE = "00000000000"
ANON_ADDRESS = "[Veri silindi]"
# E-posta yerine deterministik hash kullan — aynı silinmiş müşterinin tüm
# kayıtları birbirine bağlı kalır, ama kişisel veriyle eşleştirilemez.
_ANON_EMAIL_SALT = "tecnotools-anon-v1"


# Mali kayıt zorunluluğundan ötürü tutulması gereken sipariş durumları.
# Bunlar anonimleştirilir (snapshot maskelenir), tamamen silinmez.
_RETAIN_ORDER_STATUSES = {
    PaymentStatus.SUCCESS.value,
    PaymentStatus.REFUNDED.value,
    PaymentStatus.PENDING.value,  # havale/cod onayı beklenen — fatura kesilmiş olabilir
}


def _anon_email(original: str) -> str:
    digest = hashlib.sha256((_ANON_EMAIL_SALT + (original or "")).encode("utf-8")).hexdigest()[:16]
    return f"silinmis+{digest}@anonim.local"


async def collect_customer_data(db: AsyncSession, customer: Customer) -> dict[str, Any]:
    """Müşteri verisinin taşınabilirlik (data portability) çıktısı.

    Şifre hash'i, refresh token'lar gibi güvenlik bilgileri çıktıya konmaz.
    """
    orders = (
        await db.execute(
            select(Order).where(
                (Order.customer_id == customer.id) | (Order.customer_email == customer.email)
            )
        )
    ).scalars().unique().all()
    reviews = (
        await db.execute(select(ProductReview).where(ProductReview.customer_email == customer.email))
    ).scalars().all()
    invoices = (
        await db.execute(select(Invoice).where(Invoice.customer_email == customer.email))
    ).scalars().all()
    returns = (
        await db.execute(select(ReturnRequest).where(ReturnRequest.customer_email == customer.email))
    ).scalars().all()
    stock_notifs = (
        await db.execute(select(StockNotification).where(StockNotification.email == customer.email))
    ).scalars().all()
    newsletter = (
        await db.execute(select(NewsletterSubscriber).where(NewsletterSubscriber.email == customer.email))
    ).scalars().all()
    chat_sessions = (
        await db.execute(select(ChatSession).where(ChatSession.customer_id == customer.id))
    ).scalars().all()

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "format_version": "1.0",
        "customer": {
            "id": customer.id,
            "email": customer.email,
            "name": customer.name,
            "phone": customer.phone,
            "city": customer.city,
            "address": customer.address,
            "is_verified": customer.is_verified,
            "marketing_opt_in": customer.marketing_opt_in,
            "last_login_at": customer.last_login_at.isoformat() if customer.last_login_at else None,
            "created_at": customer.created_at.isoformat() if customer.created_at else None,
        },
        "orders": [
            {
                "order_no": o.order_no,
                "status": o.status,
                "payment_status": o.payment_status,
                "subtotal": float(o.subtotal),
                "discount": float(o.discount or 0),
                "tax": float(o.tax),
                "shipping": float(o.shipping),
                "total": float(o.total),
                "note": o.note,
                "tracking_no": o.tracking_no,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ],
        "invoices": [
            {
                "invoice_no": inv.invoice_no,
                "order_no": inv.order_no,
                "ettn": inv.ettn,
                "kind": inv.kind,
                "status": inv.status,
                "total": float(inv.total),
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
            }
            for inv in invoices
        ],
        "returns": [
            {
                "id": r.id,
                "order_no": r.order_no,
                "status": r.status,
                "reason": r.reason,
                "refund_amount": float(r.refund_amount or 0),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in returns
        ],
        "reviews": [
            {
                "product_id": r.product_id,
                "rating": r.rating,
                "title": r.title,
                "body": r.body,
                "is_approved": r.is_approved,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reviews
        ],
        "stock_notifications": [
            {"product_id": s.product_id, "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in stock_notifs
        ],
        "newsletter_subscriptions": [
            {"email": n.email, "created_at": n.created_at.isoformat() if n.created_at else None}
            for n in newsletter
        ],
        "chat_sessions": [
            {
                "session_id": cs.session_id,
                "status": cs.status,
                "created_at": cs.created_at.isoformat() if cs.created_at else None,
            }
            for cs in chat_sessions
        ],
    }


async def _anonymize_orders(db: AsyncSession, customer: Customer) -> int:
    """Mali kayıt zorunluluğuna takılan siparişleri snapshot bazlı maskele.

    Order.customer_id zaten ondelete=SET NULL — silinince otomatik NULL'a düşer.
    Ama snapshot alanlar (customer_name/email/phone/address) hâlâ kişisel veri
    içerir; onları burada anonimleştiriyoruz.
    """
    rows = (
        await db.execute(
            select(Order).where(
                (Order.customer_id == customer.id) | (Order.customer_email == customer.email)
            )
        )
    ).scalars().unique().all()
    affected = 0
    anon_email = _anon_email(customer.email)
    for o in rows:
        # Mali kayıt değilse tamamen silinebilirdi, ama tutarlılık için hepsini
        # anonimleştiriyoruz — KVKK saklama süresi sona erince ayrı cron siler.
        o.customer_id = None
        o.customer_name = ANON_NAME
        o.customer_email = anon_email
        o.customer_phone = ANON_PHONE
        o.customer_address = ANON_ADDRESS
        o.customer_city = None
        # admin not'ları kişisel veri içerebilir — temizle
        o.admin_notes = None
        affected += 1
    return affected


async def _anonymize_invoices(db: AsyncSession, customer: Customer) -> int:
    """E-arşiv faturaları VUK uyarınca 5 yıl saklanır — silinmez, maskelenir."""
    rows = (
        await db.execute(select(Invoice).where(Invoice.customer_email == customer.email))
    ).scalars().all()
    affected = 0
    anon_email = _anon_email(customer.email)
    for inv in rows:
        inv.customer_name = ANON_NAME
        inv.customer_email = anon_email
        inv.customer_phone = ANON_PHONE
        inv.customer_address = ANON_ADDRESS
        affected += 1
    return affected


async def _anonymize_returns(db: AsyncSession, customer: Customer) -> int:
    rows = (
        await db.execute(select(ReturnRequest).where(ReturnRequest.customer_email == customer.email))
    ).scalars().all()
    anon_email = _anon_email(customer.email)
    for r in rows:
        r.customer_name = ANON_NAME
        r.customer_email = anon_email
        r.note = None
    return len(rows)


async def _anonymize_reviews(db: AsyncSession, customer: Customer) -> int:
    """Yorumlar gönüllü içeriktir; KVKK silme talebinde tamamen siliyoruz.

    İçerik mağaza için değerli — anonim göstermek isterseniz onun yerine
    customer_name="Anonim" yapıp body'i tutabilirsiniz. Burada katı silme.
    """
    res = await db.execute(
        delete(ProductReview).where(ProductReview.customer_email == customer.email)
    )
    return res.rowcount or 0


async def _delete_chat(db: AsyncSession, customer: Customer) -> int:
    """Chat oturumlarını + mesajlarını sil (cascade)."""
    sessions = (
        await db.execute(select(ChatSession.id).where(ChatSession.customer_id == customer.id))
    ).scalars().all()
    deleted = 0
    if sessions:
        # Önce mesajları sil (FK ondelete=CASCADE zaten ama hassas içerikte
        # ayrı silmek log'da net görünür).
        res_msg = await db.execute(
            delete(ChatMessage).where(ChatMessage.session_pk.in_(sessions))
        )
        deleted += res_msg.rowcount or 0
        res_sess = await db.execute(delete(ChatSession).where(ChatSession.id.in_(sessions)))
        deleted += res_sess.rowcount or 0
    return deleted


async def _delete_misc(db: AsyncSession, customer: Customer) -> dict[str, int]:
    """Tutmaya değmeyen kişisel kayıtlar — tamamen sil."""
    out: dict[str, int] = {}

    r = await db.execute(
        delete(StockNotification).where(StockNotification.email == customer.email)
    )
    out["stock_notifications"] = r.rowcount or 0

    r = await db.execute(
        delete(NewsletterSubscriber).where(NewsletterSubscriber.email == customer.email)
    )
    out["newsletter"] = r.rowcount or 0

    r = await db.execute(
        delete(CustomerRefreshToken).where(CustomerRefreshToken.customer_id == customer.id)
    )
    out["refresh_tokens"] = r.rowcount or 0

    r = await db.execute(
        delete(CustomerPasswordResetToken).where(
            CustomerPasswordResetToken.customer_id == customer.id
        )
    )
    out["reset_tokens"] = r.rowcount or 0

    # Consent log'ları — customer_id ile bağlı olanları SET NULL yap;
    # session_id bazlılar zaten kişiyi tanımlamaz.
    await db.execute(
        update(ConsentLog)
        .where(ConsentLog.customer_id == customer.id)
        .values(customer_id=None)
    )
    return out


async def run_deletion(db: AsyncSession, request: DataDeletionRequest) -> dict[str, Any]:
    """KVKK silme talebini icra eder. confirmed → completed."""
    if request.status != DataDeletionStatus.CONFIRMED.value:
        raise ValueError(f"Talep durumu uygun değil: {request.status}")

    customer = None
    if request.customer_id:
        customer = (
            await db.execute(select(Customer).where(Customer.id == request.customer_id))
        ).scalar_one_or_none()
    if not customer:
        # Snapshot email'le ara — müşteri eski talepte silinmiş olabilir
        customer = (
            await db.execute(select(Customer).where(Customer.email == request.email_snapshot))
        ).scalar_one_or_none()

    if not customer:
        # Zaten silinmiş — başka bağlı kayıtlar varsa email bazlı temizle
        anon_email = _anon_email(request.email_snapshot)
        await db.execute(
            update(Order)
            .where(Order.customer_email == request.email_snapshot)
            .values(
                customer_id=None,
                customer_name=ANON_NAME,
                customer_email=anon_email,
                customer_phone=ANON_PHONE,
                customer_address=ANON_ADDRESS,
                customer_city=None,
                admin_notes=None,
            )
        )
        request.status = DataDeletionStatus.COMPLETED.value
        request.completed_at = datetime.now(timezone.utc)
        request.result = {"note": "customer_already_deleted"}
        await db.commit()
        return request.result

    summary: dict[str, Any] = {}
    summary["orders_anonymized"] = await _anonymize_orders(db, customer)
    summary["invoices_anonymized"] = await _anonymize_invoices(db, customer)
    summary["returns_anonymized"] = await _anonymize_returns(db, customer)
    summary["reviews_deleted"] = await _anonymize_reviews(db, customer)
    summary["chat_records_deleted"] = await _delete_chat(db, customer)
    summary.update(await _delete_misc(db, customer))

    # Audit log — kim sildi izi bırak (kişisel veri içermez)
    db.add(
        AuditLog(
            actor="customer",
            action="data-deletion",
            message=f"KVKK silme tamamlandı: customer#{customer.id}",
            meta={"summary": summary, "request_id": request.id},
        )
    )

    customer_id = customer.id
    await db.delete(customer)
    summary["customer_deleted"] = True

    request.status = DataDeletionStatus.COMPLETED.value
    request.completed_at = datetime.now(timezone.utc)
    request.customer_id = None  # SET NULL referansı netleşsin
    request.result = summary
    await db.commit()
    logger.info("KVKK silme tamamlandı: customer#%s → %s", customer_id, summary)
    return summary
