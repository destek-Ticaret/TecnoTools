"""E-arşiv fatura endpoint'leri.

Admin:
  POST   /api/invoices/orders/{order_no}/issue  — sipariş için fatura kes
  GET    /api/invoices                           — tüm faturalar
  GET    /api/invoices/{id}                      — tek fatura
  POST   /api/invoices/{id}/cancel               — fatura iptal (entegratör çağırır)
  GET    /api/invoices/{id}/pdf                  — fatura PDF (HTML olarak servis)

Müşteri:
  GET    /api/customer-auth/invoices             — kendi faturaları (customer_auth.py)
  GET    /api/invoices/public/{ettn}?email=...   — public PDF (email doğrulamalı)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import current_customer, require_editor
from app.models import (
    AuditLog,
    Customer,
    Invoice,
    InvoiceKind,
    InvoiceStatus,
    Order,
    User,
)
from app.rate_limit import limiter
from app.services.einvoice import InvoicePayload, get_provider, next_invoice_no
from app.services.email import send_email
from app.services.events import bus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/invoices", tags=["invoices"])


# ─────────────────────────────── Schemas ────────────────────────────────────


class InvoiceOut(BaseModel):
    id: int
    order_id: int
    order_no: str
    invoice_no: str
    ettn: str | None
    kind: str
    status: str
    customer_name: str
    customer_email: str
    tax_no: str | None
    tax_office: str | None
    company_title: str | None
    subtotal: float
    tax_rate: float
    tax_amount: float
    total: float
    items: list[dict]
    provider: str | None
    pdf_url: str | None
    error_message: str | None
    created_at: datetime
    sent_at: datetime | None
    cancelled_at: datetime | None

    class Config:
        from_attributes = True


class IssueIn(BaseModel):
    tax_rate: float = Field(default=20, ge=0, le=100)
    # Opsiyonel: müşteri tarafında girilmediyse buradan override
    tax_no: str | None = None
    tax_office: str | None = None
    company_title: str | None = None


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


# ─────────────────────────────── Helpers ────────────────────────────────────


def _serialize(inv: Invoice) -> dict[str, Any]:
    return {
        "id": inv.id,
        "order_id": inv.order_id,
        "order_no": inv.order_no,
        "invoice_no": inv.invoice_no,
        "ettn": inv.ettn,
        "kind": inv.kind,
        "status": inv.status,
        "customer_name": inv.customer_name,
        "customer_email": inv.customer_email,
        "customer_phone": inv.customer_phone,
        "customer_address": inv.customer_address,
        "tax_no": inv.tax_no,
        "tax_office": inv.tax_office,
        "company_title": inv.company_title,
        "subtotal": float(inv.subtotal),
        "tax_rate": float(inv.tax_rate),
        "tax_amount": float(inv.tax_amount),
        "total": float(inv.total),
        "items": inv.items or [],
        "provider": inv.provider,
        "pdf_url": inv.pdf_url,
        "error_message": inv.error_message,
        "created_at": inv.created_at,
        "sent_at": inv.sent_at,
        "cancelled_at": inv.cancelled_at,
    }


def _esc(s: Any) -> str:
    """HTML escape."""
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _tr_money(n: float) -> str:
    try:
        return f"₺{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return f"₺{n}"


# ──────────────────────────── Admin: issue ──────────────────────────────────


@router.post("/orders/{order_no}/issue", response_model=None, status_code=201)
async def issue_invoice(
    order_no: str,
    payload: IssueIn = Body(default=IssueIn()),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    """Sipariş için e-arşiv fatura kes. Halihazırda kesilmiş 'sent' fatura varsa
    409 döner; 'failed' veya iptal edilmiş varsa yeniden denenebilir."""
    o = (
        await db.execute(select(Order).where(Order.order_no == order_no))
    ).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    existing = (
        await db.execute(
            select(Invoice)
            .where(Invoice.order_id == o.id)
            .where(Invoice.status == InvoiceStatus.SENT.value)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Bu sipariş için zaten aktif fatura var: {existing.invoice_no}",
        )

    tax_rate = float(payload.tax_rate)
    tax_no = (payload.tax_no or o.tax_no or "").strip() or None
    tax_office = (payload.tax_office or o.tax_office or "").strip() or None
    company_title = (payload.company_title or o.company_title or "").strip() or None

    # Tutarlar — sipariş "total" KDV dahil. Subtotal'dan tax çıkar
    subtotal = float(o.subtotal)
    discount = float(o.discount or 0)
    tax_amount = float(o.tax or 0)
    total = float(o.total)

    items_snap: list[dict] = []
    for it in o.items:
        items_snap.append({
            "name": it.name,
            "qty": int(it.qty),
            "unit_price": float(it.price),
            "line_total": float(it.price) * int(it.qty),
            "tax_rate": tax_rate,
        })

    invoice_no = await next_invoice_no(db)
    inv = Invoice(
        order_id=o.id,
        order_no=o.order_no,
        invoice_no=invoice_no,
        kind=InvoiceKind.EARSIV.value,
        status=InvoiceStatus.PENDING.value,
        customer_name=o.customer_name,
        customer_email=o.customer_email,
        customer_phone=o.customer_phone,
        customer_address=o.customer_address,
        tax_no=tax_no,
        tax_office=tax_office,
        company_title=company_title,
        subtotal=Decimal(str(subtotal - discount)),
        tax_rate=Decimal(str(tax_rate)),
        tax_amount=Decimal(str(tax_amount)),
        total=Decimal(str(total)),
        items=items_snap,
    )
    db.add(inv)
    await db.flush()  # id gerek

    provider = get_provider()
    inv.provider = provider.name
    payload_obj = InvoicePayload(
        invoice_no=inv.invoice_no,
        customer_name=inv.customer_name,
        customer_email=inv.customer_email,
        customer_phone=inv.customer_phone,
        customer_address=inv.customer_address,
        tax_no=inv.tax_no,
        tax_office=inv.tax_office,
        company_title=inv.company_title,
        items=inv.items,
        subtotal=float(inv.subtotal),
        tax_rate=float(inv.tax_rate),
        tax_amount=float(inv.tax_amount),
        total=float(inv.total),
    )
    result = await provider.create(payload_obj)
    if result.ok:
        inv.status = InvoiceStatus.SENT.value
        inv.ettn = result.ettn
        inv.uuid = result.uuid
        inv.pdf_url = result.pdf_url  # mock'ta None; backend kendisi servis eder
        inv.provider_response = result.raw or None
        inv.sent_at = datetime.now(timezone.utc)
    else:
        inv.status = InvoiceStatus.FAILED.value
        inv.error_message = result.error or "Bilinmeyen entegratör hatası"
        inv.provider_response = result.raw or None

    db.add(AuditLog(
        actor=user.username, action="invoice-issue",
        message=f"Fatura {inv.invoice_no} ({inv.status}) — sipariş {o.order_no}",
    ))
    await db.commit()
    await db.refresh(inv)

    if inv.status == InvoiceStatus.SENT.value:
        # Müşteriye fatura email'i — fire-and-forget
        try:
            pdf_link = f"/api/invoices/public/{inv.ettn}?email={inv.customer_email}"
            html = f"""
            <h2>Faturanız hazır 🧾</h2>
            <p>Sayın {_esc(inv.customer_name)}, <strong>{_esc(o.order_no)}</strong> numaralı siparişinize ait e-arşiv faturanız düzenlendi.</p>
            <p><strong>Fatura No:</strong> {_esc(inv.invoice_no)}<br/>
               <strong>Toplam:</strong> {_tr_money(float(inv.total))}<br/>
               <strong>ETTN:</strong> <code>{_esc(inv.ettn)}</code></p>
            <p><a href="{pdf_link}" style="display:inline-block;background:#2563eb;color:#fff;padding:11px 22px;border-radius:9px;text-decoration:none;font-weight:600;">Faturayı Görüntüle</a></p>
            """
            await send_email(to=inv.customer_email, subject=f"🧾 Faturanız: {inv.invoice_no}", html=html)
        except Exception:
            logger.exception("Fatura email gönderim hatası")
        await bus.publish("invoice_issued", {"id": inv.id, "order_no": o.order_no})

    return _serialize(inv)


# ──────────────────────────── Admin: list / get ─────────────────────────────


@router.get("")
async def list_invoices(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    stmt = select(Invoice).order_by(Invoice.id.desc())
    if status:
        stmt = stmt.where(Invoice.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize(r) for r in rows]


@router.get("/{invoice_id}")
async def get_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    inv = (
        await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    ).scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Fatura bulunamadı")
    return _serialize(inv)


# ──────────────────────────── Admin: cancel ─────────────────────────────────


@router.post("/{invoice_id}/cancel")
async def cancel_invoice(
    invoice_id: int,
    payload: CancelIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
):
    inv = (
        await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    ).scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Fatura bulunamadı")
    if inv.status != InvoiceStatus.SENT.value:
        raise HTTPException(status_code=409, detail="Sadece 'sent' durumdaki faturalar iptal edilebilir")
    provider = get_provider()
    ok = await provider.cancel(inv.ettn or "", payload.reason)
    if not ok:
        raise HTTPException(status_code=502, detail="Entegratör iptali reddetti")
    inv.status = InvoiceStatus.CANCELLED.value
    inv.cancelled_at = datetime.now(timezone.utc)
    inv.error_message = f"İptal: {payload.reason}"
    db.add(AuditLog(
        actor=user.username, action="invoice-cancel",
        message=f"Fatura iptal {inv.invoice_no} — {payload.reason}",
    ))
    await db.commit()
    await bus.publish("invoice_cancelled", {"id": inv.id})
    return {"ok": True}


# ──────────────────────────── PDF (HTML) ─────────────────────────────────


def _render_invoice_html(inv: Invoice) -> str:
    """Fatura HTML şablonu — tarayıcı print ile PDF'e dökülür."""
    issued = inv.sent_at or inv.created_at
    issued_str = issued.strftime("%d.%m.%Y %H:%M") if issued else "—"
    is_corp = bool(inv.tax_no and (inv.company_title or len((inv.tax_no or "").strip()) == 10))
    fatura_tipi = "e-Arşiv Fatura" + (" (Kurumsal)" if is_corp else " (Bireysel)")
    items_html = "\n".join(
        f"""<tr>
              <td>{_esc(it.get('name'))}</td>
              <td style="text-align:center;">{int(it.get('qty', 1))}</td>
              <td style="text-align:right;">{_tr_money(float(it.get('unit_price', 0)))}</td>
              <td style="text-align:right;">{_tr_money(float(it.get('line_total', it.get('unit_price', 0) * it.get('qty', 1))))}</td>
            </tr>"""
        for it in (inv.items or [])
    )
    status_color = {
        InvoiceStatus.SENT.value: "#059669",
        InvoiceStatus.CANCELLED.value: "#dc2626",
        InvoiceStatus.FAILED.value: "#dc2626",
        InvoiceStatus.PENDING.value: "#b45309",
    }.get(inv.status, "#475569")
    status_label = {
        InvoiceStatus.SENT.value: "GEÇERLİ",
        InvoiceStatus.CANCELLED.value: "İPTAL EDİLDİ",
        InvoiceStatus.FAILED.value: "BAŞARISIZ",
        InvoiceStatus.PENDING.value: "TASLAK",
    }.get(inv.status, inv.status)
    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8" />
<title>{_esc(inv.invoice_no)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Inter', Arial, sans-serif; color: #0f172a; padding: 28px 32px; background: #fff; font-size: 12.5px; line-height: 1.5; }}
  .doc {{ max-width: 800px; margin: 0 auto; }}
  .head {{ display: flex; justify-content: space-between; align-items: flex-start; padding-bottom: 16px; border-bottom: 3px solid #0f172a; margin-bottom: 22px; }}
  .brand h1 {{ font-size: 24px; font-weight: 800; letter-spacing: -.02em; }}
  .brand h1 span {{ color: #2563eb; }}
  .brand p {{ color: #64748b; font-size: 10.5px; margin-top: 4px; line-height: 1.45; }}
  .head-right {{ text-align: right; }}
  .head-right h2 {{ font-size: 18px; font-weight: 800; color: #0f172a; }}
  .head-right .sub {{ font-size: 11px; color: #475569; margin-top: 3px; }}
  .head-right .stamp {{ display: inline-block; margin-top: 6px; padding: 4px 12px; background: rgba(5,150,105,.12); color: {status_color}; border: 1px solid {status_color}; border-radius: 4px; font-size: 11px; font-weight: 800; letter-spacing: .04em; }}
  .ettn {{ font-family: monospace; font-size: 10px; color: #64748b; margin-top: 6px; word-break: break-all; }}
  section {{ margin-bottom: 18px; }}
  section h3 {{ font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em; color: #64748b; margin-bottom: 8px; }}
  .parties {{ display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }}
  .party {{ padding: 12px 14px; border: 1px solid #e2e8f0; border-radius: 8px; background: #f8fafc; }}
  .party .pname {{ font-weight: 700; font-size: 13.5px; margin-bottom: 3px; }}
  .party p {{ font-size: 11.5px; color: #334155; line-height: 1.5; }}
  .party .tx {{ margin-top: 4px; font-size: 11px; color: #475569; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; margin-top: 4px; }}
  table th {{ text-align: left; padding: 8px 10px; background: #1e293b; color: #fff; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; font-weight: 700; }}
  table td {{ padding: 8px 10px; border-bottom: 1px solid #e2e8f0; }}
  table tr:nth-child(even) td {{ background: #fafbfc; }}
  .totals {{ margin-top: 10px; margin-left: auto; width: 320px; }}
  .totals .row {{ display: flex; justify-content: space-between; padding: 4px 0; font-size: 12.5px; font-variant-numeric: tabular-nums; }}
  .totals .row.grand {{ font-size: 16px; font-weight: 800; border-top: 2px solid #0f172a; padding-top: 10px; margin-top: 8px; color: #0f172a; }}
  .foot {{ margin-top: 24px; padding-top: 14px; border-top: 1px solid #e2e8f0; font-size: 10.5px; color: #94a3b8; }}
  @media print {{ body {{ padding: 0; }} @page {{ size: A4; margin: 14mm; }} .no-print {{ display: none; }} }}
  .actions {{ text-align: right; margin-bottom: 14px; }}
  .actions button {{ background: #2563eb; color: #fff; border: none; border-radius: 8px; padding: 8px 18px; font-weight: 600; cursor: pointer; font-family: inherit; font-size: 13px; }}
</style></head><body>
<div class="actions no-print"><button onclick="window.print()">PDF olarak indir / Yazdır</button></div>
<div class="doc">
  <div class="head">
    <div class="brand">
      <h1>TecnoTools <span>Ltd. Şti.</span></h1>
      <p>Yenidoğan Mah., Sancaktepe / İstanbul<br/>VKN: 1234567890 · destek@tecnotools.org</p>
    </div>
    <div class="head-right">
      <h2>{fatura_tipi.upper()}</h2>
      <div class="sub">Fatura No: <strong>{_esc(inv.invoice_no)}</strong></div>
      <div class="sub">Tarih: {issued_str}</div>
      <div class="stamp">{status_label}</div>
      {f'<div class="ettn">ETTN: {_esc(inv.ettn)}</div>' if inv.ettn else ''}
    </div>
  </div>

  <section>
    <h3>Taraflar</h3>
    <div class="parties">
      <div class="party">
        <div class="pname">Satıcı</div>
        <p>TecnoTools Ltd. Şti.<br/>Yenidoğan Mah., Sancaktepe / İstanbul<br/>destek@tecnotools.org</p>
        <div class="tx">VKN: 1234567890 · Vergi Dairesi: Sancaktepe</div>
      </div>
      <div class="party">
        <div class="pname">{_esc(inv.company_title or inv.customer_name)}</div>
        <p>{_esc(inv.customer_address)}<br/>{_esc(inv.customer_email)} · {_esc(inv.customer_phone)}</p>
        {f'<div class="tx">{"VKN" if is_corp else "TCKN"}: {_esc(inv.tax_no)} · {_esc(inv.tax_office or "")}</div>' if inv.tax_no else '<div class="tx" style="color:#94a3b8;">Bireysel (vergi no girilmedi)</div>'}
      </div>
    </div>
  </section>

  <section>
    <h3>Mal/Hizmet Kalemleri</h3>
    <table>
      <thead><tr><th>Açıklama</th><th style="text-align:center;width:80px;">Adet</th><th style="text-align:right;width:120px;">Birim</th><th style="text-align:right;width:140px;">Tutar</th></tr></thead>
      <tbody>{items_html}</tbody>
    </table>
  </section>

  <section>
    <div class="totals">
      <div class="row"><span>Ara Toplam (KDV Hariç)</span><span>{_tr_money(float(inv.subtotal))}</span></div>
      <div class="row"><span>KDV (%{float(inv.tax_rate):.0f})</span><span>{_tr_money(float(inv.tax_amount))}</span></div>
      <div class="row grand"><span>Genel Toplam</span><span>{_tr_money(float(inv.total))}</span></div>
    </div>
  </section>

  <div class="foot">
    <p>Bu e-arşiv fatura, 397 sıra no'lu Vergi Usul Kanunu Genel Tebliği kapsamında düzenlenmiştir.
    Belgenin elektronik nüshasını <strong>destek@tecnotools.org</strong> adresinden talep edebilirsiniz.
    {("Sağlayıcı: " + _esc(inv.provider).upper()) if inv.provider else ""}</p>
  </div>
</div>
</body></html>"""


@router.get("/{invoice_id}/pdf", response_class=HTMLResponse)
async def invoice_pdf_admin(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
):
    inv = (
        await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    ).scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Fatura bulunamadı")
    # Bytes olarak gönder → response header'ından bağımsız UTF-8 garantisi
    return HTMLResponse(_render_invoice_html(inv).encode("utf-8"), media_type="text/html; charset=utf-8")


@router.get("/public/{ettn}", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def invoice_pdf_public(
    request: Request,
    ettn: str,
    email: str,
    db: AsyncSession = Depends(get_db),
):
    """Public erişim — müşteri email'i ile eşleşmeli (enumeration koruma için
    404)."""
    inv = (
        await db.execute(
            select(Invoice)
            .where(Invoice.ettn == ettn)
            .where(Invoice.customer_email == email.strip())
        )
    ).scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Fatura bulunamadı")
    return HTMLResponse(_render_invoice_html(inv).encode("utf-8"), media_type="text/html; charset=utf-8")


# ──────────────────────── Customer: kendi faturaları ─────────────────────


@router.get("/my/list")
async def my_invoices(
    customer: Customer = Depends(current_customer),
    db: AsyncSession = Depends(get_db),
):
    """Login olmuş müşterinin tüm faturaları."""
    rows = (
        await db.execute(
            select(Invoice)
            .where(Invoice.customer_email == customer.email)
            .order_by(Invoice.id.desc())
        )
    ).scalars().all()
    return [_serialize(r) for r in rows]
