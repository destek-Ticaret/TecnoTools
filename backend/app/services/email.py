"""Email gönderim servisi — async SMTP, HTML + plaintext fallback.

SMTP_HOST boşsa email'ler konsola yazılır (geliştirme modu).
Gönderim fire-and-forget olarak çalışır — endpoint cevabını bloklamaz.
"""

import asyncio
import logging
from email.message import EmailMessage
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Fire-and-forget task'lara güçlü referans tut — yoksa GC task'ı yarıda toplayabilir.
_bg_tasks: set[asyncio.Task] = set()

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    enable_async=False,
)


def _strip_html(html: str) -> str:
    """Minimal HTML → düz metin dönüşümü (plaintext fallback için)."""
    import re

    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _send_smtp(message: EmailMessage) -> None:
    if not settings.smtp_host:
        # Dev modu: konsola yaz
        logger.info("📧 [DEV-EMAIL] To: %s | Subject: %s", message["To"], message["Subject"])
        logger.info(
            "Body:\n%s",
            message.get_body(preferencelist=("plain",)).get_content() if message.get_body() else "",
        )
        return

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        start_tls=settings.smtp_use_tls and not settings.smtp_use_ssl,
        use_tls=settings.smtp_use_ssl,
        timeout=15,
    )


async def send_email(*, to: str, subject: str, html: str, text: str | None = None) -> bool:
    """Tek email gönderir. Başarı/başarısızlık döner; istisna fırlatmaz."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = to
    msg.set_content(text or _strip_html(html))
    msg.add_alternative(html, subtype="html")
    try:
        await _send_smtp(msg)
        return True
    except Exception as e:
        logger.error("Email gönderim hatası (%s): %s", to, e)
        return False


def send_email_background(*, to: str, subject: str, html: str, text: str | None = None) -> None:
    """Fire-and-forget — endpoint'i bloklamadan email kuyruğuna at."""
    try:
        loop = asyncio.get_running_loop()
        _task = loop.create_task(send_email(to=to, subject=subject, html=html, text=text))
        _bg_tasks.add(_task)
        _task.add_done_callback(_bg_tasks.discard)
    except RuntimeError:
        # Event loop yoksa (testler için) senkron çalıştır
        asyncio.run(send_email(to=to, subject=subject, html=html, text=text))


def render_template(template_name: str, **context) -> str:
    # NOT: parametre adı bilinçli olarak `template_name` — `name` olsaydı
    # render_template("x.html", name=...) çağrısında "name" çakışırdı (TypeError).
    tpl = _env.get_template(template_name)
    return tpl.render(**context, store_url=settings.store_public_url)


# ── Senaryolar ──
async def send_order_confirmation(order) -> None:
    html = render_template("order_confirmation.html", order=order)
    await send_email(
        to=order.customer_email, subject=f"Siparişiniz alındı · {order.order_no}", html=html
    )


async def send_order_status_update(order, old_status: str) -> None:
    html = render_template("order_status_update.html", order=order, old_status=old_status)
    label = {
        "pending": "alındı",
        "processing": "hazırlanıyor",
        "shipped": "kargoya verildi",
        "delivered": "teslim edildi",
        "cancelled": "iptal edildi",
    }.get(order.status, order.status)
    await send_email(
        to=order.customer_email,
        subject=f"Siparişiniz {label} · {order.order_no}",
        html=html,
    )


async def send_admin_new_order(order, admin_emails: list[str]) -> None:
    """Admin'lere yeni sipariş bildirimi (fire-and-forget ile çağrılır)."""
    if not admin_emails:
        return
    items_rows = "".join(
        f"<tr><td style='padding:6px 0;border-bottom:1px solid #f1f5f9'>{it.name}"
        f"{'(' + it.variant_name + ')' if it.variant_name else ''}</td>"
        f"<td align='center' style='padding:6px 0;border-bottom:1px solid #f1f5f9'>{it.qty}</td>"
        f"<td align='right' style='padding:6px 0;border-bottom:1px solid #f1f5f9'>₺{it.price * it.qty:.2f}</td></tr>"
        for it in order.items
    )
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1a1f2e;">
      <h2 style="margin:0 0 12px;font-size:20px;">🛒 Yeni Sipariş: {order.order_no}</h2>
      <p style="margin:0 0 16px;color:#475569;">
        <strong>{order.customer_name}</strong> — {order.customer_email}<br/>
        Tutar: <strong>₺{order.total:.2f}</strong> &nbsp;|&nbsp;
        Ödeme: <strong>{order.payment_method or '—'}</strong>
      </p>
      <table width="100%" cellspacing="0" cellpadding="0" border="0"
             style="margin-bottom:16px;font-size:13px;">
        <thead>
          <tr>
            <th align="left" style="font-size:11px;color:#64748b;padding:6px 0;
                border-bottom:1px solid #e2e8f0;">Ürün</th>
            <th align="center" style="font-size:11px;color:#64748b;padding:6px 0;
                border-bottom:1px solid #e2e8f0;">Adet</th>
            <th align="right" style="font-size:11px;color:#64748b;padding:6px 0;
                border-bottom:1px solid #e2e8f0;">Tutar</th>
          </tr>
        </thead>
        <tbody>{items_rows}</tbody>
      </table>
      <a href="{settings.store_public_url.rstrip('/')}/admin.html"
         style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;
                padding:10px 20px;border-radius:8px;font-weight:600;font-size:13px;">
        Admin Paneli →
      </a>
    </div>"""
    for email in admin_emails:
        await send_email(
            to=email,
            subject=f"[TecnoTools] Yeni sipariş: {order.order_no} · ₺{order.total:.2f}",
            html=html,
        )


async def send_password_reset(
    *, email: str, username: str, reset_url: str, ttl_minutes: int = 30
) -> None:
    html = render_template(
        "password_reset.html", username=username, reset_url=reset_url, ttl_minutes=ttl_minutes
    )
    await send_email(to=email, subject="Şifre sıfırlama talebi · TecnoTools", html=html)
