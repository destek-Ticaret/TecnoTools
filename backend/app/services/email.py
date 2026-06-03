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
        logger.info("Body:\n%s", message.get_body(preferencelist=("plain",)).get_content() if message.get_body() else "")
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
        loop.create_task(send_email(to=to, subject=subject, html=html, text=text))
    except RuntimeError:
        # Event loop yoksa (testler için) senkron çalıştır
        asyncio.run(send_email(to=to, subject=subject, html=html, text=text))


def render_template(name: str, **context) -> str:
    tpl = _env.get_template(name)
    return tpl.render(**context, store_url=settings.store_public_url)


# ── Senaryolar ──
async def send_order_confirmation(order) -> None:
    html = render_template("order_confirmation.html", order=order)
    await send_email(to=order.customer_email, subject=f"Siparişiniz alındı · {order.order_no}", html=html)


async def send_order_status_update(order, old_status: str) -> None:
    html = render_template("order_status_update.html", order=order, old_status=old_status)
    label = {
        "pending": "alındı", "processing": "hazırlanıyor", "shipped": "kargoya verildi",
        "delivered": "teslim edildi", "cancelled": "iptal edildi",
    }.get(order.status, order.status)
    await send_email(
        to=order.customer_email,
        subject=f"Siparişiniz {label} · {order.order_no}",
        html=html,
    )


async def send_password_reset(*, email: str, username: str, reset_url: str, ttl_minutes: int = 30) -> None:
    html = render_template(
        "password_reset.html", username=username, reset_url=reset_url, ttl_minutes=ttl_minutes
    )
    await send_email(to=email, subject="Şifre sıfırlama talebi · TecnoTools", html=html)
