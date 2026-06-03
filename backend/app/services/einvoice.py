"""E-arşiv fatura entegrasyon katmanı.

Tasarım: provider-bağımsız bir Protocol arayüzü; uygulamada hangi entegratörün
(Foriba, Nilvera, QNB Finansbank, Logo, Mikro, Sovos, vb.) kullanıldığı `EINVOICE_PROVIDER`
env değişkeniyle seçilir. Default `mock` — geliştirme/test için kalıcı ETTN
üretir, sahte PDF döner.

Gerçek entegratör eklemek için:
  1. Yeni bir class yaz (örn. ForibaProvider) — `EInvoiceProvider` protokolünü
     uygulasın.
  2. `_get_provider()` fonksiyonuna ekle.
  3. .env içine credential koy.

Bu sınır şimdi sadece e-arşiv (B2C). E-fatura (mükellef-mükellef) ileride
benzer biçimde eklenebilir; modelde `kind` alanı zaten var.
"""

from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class InvoicePayload:
    """Entegratöre gönderilecek standartlaştırılmış fatura."""

    invoice_no: str
    customer_name: str
    customer_email: str
    customer_phone: str
    customer_address: str
    tax_no: str | None  # TCKN (11 haneli) veya VKN (10 haneli)
    tax_office: str | None
    company_title: str | None  # boşsa bireysel
    items: list[dict]  # [{"name","qty","unit_price","tax_rate"}, ...]
    subtotal: float
    tax_rate: float
    tax_amount: float
    total: float


@dataclass
class InvoiceResult:
    """Entegratör yanıtının standart hale getirilmiş hali."""

    ok: bool
    ettn: str | None = None
    uuid: str | None = None
    pdf_url: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class EInvoiceProvider(Protocol):
    name: str

    async def create(self, payload: InvoicePayload) -> InvoiceResult: ...
    async def cancel(self, ettn: str, reason: str) -> bool: ...


# ──────────────────────────── Mock Provider ───────────────────────────────


class MockProvider:
    """Geliştirme/test için — ağa çıkmadan gerçek davranışı simüle eder."""

    name = "mock"

    async def create(self, payload: InvoicePayload) -> InvoiceResult:
        # Gerçek entegratörler gibi UUID + ETTN üret
        u = uuid.uuid4()
        ettn = str(u).upper()
        # PDF URL — backend kendi `/api/invoices/{id}/pdf` üzerinden servis eder
        logger.info(
            "📜 [MOCK-EARSIV] Fatura kesildi: %s (ETTN=%s, total=%.2f)",
            payload.invoice_no,
            ettn,
            payload.total,
        )
        return InvoiceResult(
            ok=True,
            ettn=ettn,
            uuid=str(u),
            pdf_url=None,  # backend kendi üretiyor
            raw={"mock": True, "issued_at": datetime.now(UTC).isoformat()},
        )

    async def cancel(self, ettn: str, reason: str) -> bool:
        logger.info("📜 [MOCK-EARSIV] Fatura iptal: %s — %s", ettn, reason)
        return True


# ──────────────────────────── Foriba örnek (iskelet) ──────────────────────


class ForibaProvider:
    """Foriba e-arşiv adapter'ı — iskelet.

    Gerçek API: https://efaturaservice.foriba.com/.../v1/invoices
    .env'de FORIBA_USERNAME / FORIBA_PASSWORD / FORIBA_SOURCE_ID gerek.
    """

    name = "foriba"

    def __init__(self) -> None:
        self.username = getattr(settings, "foriba_username", "") or ""
        self.password = getattr(settings, "foriba_password", "") or ""
        self.source_id = getattr(settings, "foriba_source_id", "") or ""
        self.endpoint = getattr(settings, "foriba_endpoint", "") or ""

    async def create(self, payload: InvoicePayload) -> InvoiceResult:
        if not (self.username and self.password and self.endpoint):
            return InvoiceResult(ok=False, error="Foriba credential'ları eksik")
        try:
            import httpx  # type: ignore
        except ImportError:
            return InvoiceResult(ok=False, error="httpx kurulu değil")
        auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        body = self._build_ubl_payload(payload)
        try:
            async with httpx.AsyncClient(timeout=30) as h:
                r = await h.post(
                    self.endpoint,
                    headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
                    json=body,
                )
            if r.status_code >= 300:
                return InvoiceResult(
                    ok=False,
                    error=f"Foriba HTTP {r.status_code}: {r.text[:200]}",
                    raw={"http": r.status_code},
                )
            data = r.json()
            return InvoiceResult(
                ok=True,
                ettn=data.get("uuid") or data.get("ettn"),
                uuid=data.get("uuid"),
                pdf_url=data.get("pdfUrl") or data.get("documentUrl"),
                raw=data,
            )
        except Exception as e:
            logger.exception("Foriba create_invoice error")
            return InvoiceResult(ok=False, error=f"Foriba çağrı hatası: {e}")

    async def cancel(self, ettn: str, reason: str) -> bool:
        # Gerçek API: PUT /invoices/{ettn}/cancel
        logger.warning("Foriba cancel placeholder — gerçek API çağrısı eklenmeli")
        return False

    def _build_ubl_payload(self, p: InvoicePayload) -> dict[str, Any]:
        """UBL 2.1 e-arşiv format taslağı — Foriba'nın beklediği şemada."""
        return {
            "documentTypeCode": "E_ARSIV",
            "customer": {
                "name": p.customer_name,
                "taxNumber": p.tax_no,
                "taxOffice": p.tax_office,
                "title": p.company_title,
                "address": p.customer_address,
                "email": p.customer_email,
                "phone": p.customer_phone,
            },
            "items": [
                {
                    "name": it["name"],
                    "quantity": it["qty"],
                    "unitPrice": it["unit_price"],
                    "taxRate": it.get("tax_rate", p.tax_rate),
                }
                for it in p.items
            ],
            "totals": {
                "subtotal": p.subtotal,
                "tax": p.tax_amount,
                "grandTotal": p.total,
            },
            "invoiceNumber": p.invoice_no,
        }


# ──────────────────────────── Provider seçimi ─────────────────────────────


_provider: EInvoiceProvider | None = None


def get_provider() -> EInvoiceProvider:
    """Aktif e-arşiv sağlayıcısını döner. Varsayılan: mock."""
    global _provider
    if _provider is not None:
        return _provider
    name = (getattr(settings, "einvoice_provider", "") or "mock").lower()
    if name == "foriba":
        _provider = ForibaProvider()
    else:
        _provider = MockProvider()
    logger.info("E-arşiv provider seçildi: %s", _provider.name)
    return _provider


# ──────────────────────────── Yardımcı: fatura no üret ────────────────────


from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.models import InvoiceCounter  # noqa: E402


async def next_invoice_no(db: AsyncSession, prefix: str = "TT-FAT") -> str:
    """Yıl bazlı tek satır sayaç. Format: TT-FAT-2026-000001."""
    year = datetime.now(UTC).year
    row = (
        await db.execute(select(InvoiceCounter).where(InvoiceCounter.year == year))
    ).scalar_one_or_none()
    if not row:
        row = InvoiceCounter(year=year, seq=0)
        db.add(row)
        await db.flush()
    row.seq += 1
    await db.flush()
    return f"{prefix}-{year}-{row.seq:06d}"
