"""Canlı destek — müşteri ↔ admin chat.

Mimari:
  • Müşteri WebSocket: /api/ws/chat/customer?session_id=...
      - JWT şart değil; tarayıcı sessionStorage ID'siyle kimlik
      - Mesaj geldiğinde otomatik ChatSession upsert edilir
  • Admin WebSocket:    /api/ws/chat/admin?token=...
      - Tüm açık konuşmalardaki olayları alır; herhangi birine cevap yazabilir

Mesaj protokolü (JSON):
  Client → Server (customer):
    {"action":"identify","name":"...","email":"..."}   (opsiyonel)
    {"action":"send","body":"merhaba"}
    {"action":"mark_read"}                              (admin mesajlarını okudum)
  Client → Server (admin):
    {"action":"send","session_id":"sess_xx","body":"..."}
    {"action":"close","session_id":"sess_xx"}
    {"action":"mark_read","session_id":"sess_xx"}
  Server → Client (event):
    {"event":"chat_message","data":{...}}
    {"event":"chat_session","data":{...}}     (yeni/güncel oturum bilgisi)
    {"event":"chat_history","data":[...]}     (bağlantı açılınca müşteri için)

REST:
  GET  /api/chat/admin/sessions          — tüm konuşmalar (admin)
  GET  /api/chat/admin/sessions/{id}/messages
  POST /api/chat/admin/sessions/{id}/close
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal, get_db
from app.deps import current_user
from app.models import ChatMessage, ChatSession, ChatSessionStatus, Customer, User
from app.security import decode_access_token, decode_customer_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

MAX_MESSAGE_LEN = 2000
HISTORY_LIMIT = 100


# ─────────────────────────── Connection Manager ────────────────────────────


class ChatConnectionManager:
    """Müşteri ve admin WS bağlantılarını ayrı havuzlarda tutar.

    Müşteri bağlantıları session_id → set[WebSocket] (aynı anda birden çok sekme açık olabilir).
    Admin bağlantıları tek havuzda; her birine tüm olaylar yayınlanır.
    """

    def __init__(self) -> None:
        self._customers: dict[str, set[WebSocket]] = {}
        self._admins: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect_customer(self, ws: WebSocket, session_id: str) -> None:
        await ws.accept()
        async with self._lock:
            self._customers.setdefault(session_id, set()).add(ws)

    async def connect_admin(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._admins.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._admins.discard(ws)
            for sid, pool in list(self._customers.items()):
                pool.discard(ws)
                if not pool:
                    self._customers.pop(sid, None)

    async def _send(self, conns: Iterable[WebSocket], message: dict) -> None:
        payload = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        for ws in list(conns):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                self._admins.difference_update(dead)
                for pool in self._customers.values():
                    pool.difference_update(dead)

    async def send_to_customer(self, session_id: str, event: str, data: dict | list) -> None:
        conns = self._customers.get(session_id, set())
        if conns:
            await self._send(conns, {"event": event, "data": data})

    async def send_to_admins(self, event: str, data: dict | list) -> None:
        if self._admins:
            await self._send(self._admins, {"event": event, "data": data})


manager = ChatConnectionManager()


# ─────────────────────────────── Helpers ───────────────────────────────────


def _msg_to_dict(m: ChatMessage, session_id: str) -> dict:
    return {
        "id": m.id,
        "session_id": session_id,
        "sender": m.sender,
        "sender_name": m.sender_name,
        "body": m.body,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _session_to_dict(s: ChatSession) -> dict:
    return {
        "id": s.id,
        "session_id": s.session_id,
        "customer_id": s.customer_id,
        "customer_name": s.customer_name,
        "customer_email": s.customer_email,
        "status": s.status,
        "unread_admin": s.unread_admin,
        "unread_customer": s.unread_customer,
        "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


async def _get_or_create_session(
    db: AsyncSession,
    session_id: str,
    *,
    customer: Customer | None = None,
) -> ChatSession:
    sess = (
        await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
    ).scalar_one_or_none()
    if sess:
        # Müşteri girişli ise oturumda eksik alanları otomatik doldur
        if customer is not None:
            changed = False
            if sess.customer_id != customer.id:
                sess.customer_id = customer.id
                changed = True
            if not sess.customer_name and customer.name:
                sess.customer_name = customer.name
                changed = True
            if not sess.customer_email and customer.email:
                sess.customer_email = customer.email
                changed = True
            if changed:
                await db.commit()
                await db.refresh(sess)
        return sess
    sess = ChatSession(
        session_id=session_id,
        status=ChatSessionStatus.OPEN.value,
        customer_id=customer.id if customer else None,
        customer_name=customer.name if customer else None,
        customer_email=customer.email if customer else None,
    )
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return sess


# ─────────────────────────────── REST (admin) ──────────────────────────────


class CloseSessionOut(BaseModel):
    ok: bool


@router.get("/api/chat/admin/sessions")
async def admin_list_sessions(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    # SQLite NULLS LAST tam desteklemez; coalesce ile NULL'ı created_at'a düşür
    order_col = func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc()
    rows = (await db.execute(select(ChatSession).order_by(order_col))).scalars().all()
    return [_session_to_dict(s) for s in rows]


@router.get("/api/chat/admin/sessions/{session_pk}/messages")
async def admin_session_messages(
    session_pk: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    sess = (
        await db.execute(select(ChatSession).where(ChatSession.id == session_pk))
    ).scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Oturum bulunamadı")
    msgs = (
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_pk == session_pk)
                .order_by(ChatMessage.created_at.asc())
                .limit(HISTORY_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    # Admin tarafı bu konuşmayı okudu — unread_admin sıfırla
    if sess.unread_admin:
        sess.unread_admin = 0
        await db.commit()
        await manager.send_to_admins("chat_session", _session_to_dict(sess))
    return {
        "session": _session_to_dict(sess),
        "messages": [_msg_to_dict(m, sess.session_id) for m in msgs],
    }


@router.post("/api/chat/admin/sessions/{session_pk}/close", response_model=CloseSessionOut)
async def admin_close_session(
    session_pk: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    sess = (
        await db.execute(select(ChatSession).where(ChatSession.id == session_pk))
    ).scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Oturum bulunamadı")
    sess.status = ChatSessionStatus.CLOSED.value
    await db.commit()
    payload = _session_to_dict(sess)
    await manager.send_to_admins("chat_session", payload)
    await manager.send_to_customer(sess.session_id, "chat_session", payload)
    return CloseSessionOut(ok=True)


@router.delete("/api/chat/admin/messages/{message_id}", status_code=204)
async def admin_delete_message(
    message_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    msg = (
        await db.execute(select(ChatMessage).where(ChatMessage.id == message_id))
    ).scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Mesaj bulunamadı")
    sess = (
        await db.execute(select(ChatSession).where(ChatSession.id == msg.session_pk))
    ).scalar_one_or_none()
    session_id = sess.session_id if sess else None
    await db.delete(msg)
    await db.commit()
    if session_id:
        evt = {"id": message_id, "session_id": session_id}
        await manager.send_to_admins("chat_message_deleted", evt)
        await manager.send_to_customer(session_id, "chat_message_deleted", evt)
    return None


@router.delete("/api/chat/admin/sessions/{session_pk}", status_code=204)
async def admin_delete_session(
    session_pk: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    sess = (
        await db.execute(select(ChatSession).where(ChatSession.id == session_pk))
    ).scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Oturum bulunamadı")
    session_id = sess.session_id
    await db.delete(sess)  # CASCADE → mesajlar da silinir
    await db.commit()
    evt = {"id": session_pk, "session_id": session_id}
    await manager.send_to_admins("chat_session_deleted", evt)
    await manager.send_to_customer(session_id, "chat_session_deleted", evt)
    return None


# ─────────────────────────────── WebSocket: customer ───────────────────────


async def _resolve_customer_from_token(token: str) -> Customer | None:
    payload = decode_customer_token(token)
    if not payload:
        return None
    try:
        customer_id = int(payload.get("sub") or 0)
    except (TypeError, ValueError):
        return None
    if not customer_id:
        return None
    async with SessionLocal() as db:
        cust = (
            await db.execute(select(Customer).where(Customer.id == customer_id))
        ).scalar_one_or_none()
        if not cust or not cust.is_active or not cust.password_hash:
            return None
        return cust


@router.websocket("/api/ws/chat/customer")
async def chat_customer_ws(websocket: WebSocket, token: str = Query(...)):
    """Canlı destek müşteri WS — yalnızca üye girişi yapmış müşteriler.

    Token: müşteri customer_access JWT'si. Anonim ziyaretçi kabul edilmez.
    Aynı müşteri farklı cihazlardan bağlanırsa hep aynı `cust:{id}` oturumuna
    yazılır → tek bir kalıcı konuşma geçmişi.
    """
    customer = await _resolve_customer_from_token(token.strip())
    if not customer:
        await websocket.close(code=4401)
        return
    session_id = f"cust:{customer.id}"
    await manager.connect_customer(websocket, session_id)
    try:
        async with SessionLocal() as db:
            sess = await _get_or_create_session(db, session_id, customer=customer)
            msgs = (
                (
                    await db.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_pk == sess.id)
                        .order_by(ChatMessage.created_at.asc())
                        .limit(HISTORY_LIMIT)
                    )
                )
                .scalars()
                .all()
            )
            if sess.unread_customer:
                sess.unread_customer = 0
                await db.commit()
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "chat_session",
                        "data": _session_to_dict(sess),
                    },
                    default=str,
                )
            )
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "chat_history",
                        "data": [_msg_to_dict(m, session_id) for m in msgs],
                    },
                    default=str,
                )
            )

        while True:
            raw = await websocket.receive_text()
            if raw.strip() == "ping":
                await websocket.send_text("pong")
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            action = msg.get("action")
            if action == "send":
                body = (msg.get("body") or "").strip()
                if not body:
                    continue
                body = body[:MAX_MESSAGE_LEN]
                now = datetime.now(UTC)
                async with SessionLocal() as db:
                    sess = await _get_or_create_session(db, session_id, customer=customer)
                    sess.status = ChatSessionStatus.OPEN.value
                    sess.last_message_at = now
                    sess.unread_admin += 1
                    cm = ChatMessage(
                        session_pk=sess.id,
                        sender="customer",
                        sender_name=sess.customer_name or customer.name,
                        body=body,
                    )
                    db.add(cm)
                    await db.commit()
                    await db.refresh(cm)
                    msg_payload = _msg_to_dict(cm, session_id)
                    sess_payload = _session_to_dict(sess)
                await manager.send_to_customer(session_id, "chat_message", msg_payload)
                await manager.send_to_admins("chat_message", msg_payload)
                await manager.send_to_admins("chat_session", sess_payload)
            elif action == "mark_read":
                async with SessionLocal() as db:
                    sess = await _get_or_create_session(db, session_id, customer=customer)
                    if sess.unread_customer:
                        sess.unread_customer = 0
                        await db.commit()
                        await manager.send_to_admins("chat_session", _session_to_dict(sess))
            # 'identify' artık desteklenmiyor — kimlik JWT'den çıkıyor
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("chat customer ws error")
    finally:
        await manager.disconnect(websocket)


# ─────────────────────────────── WebSocket: admin ──────────────────────────


@router.websocket("/api/ws/chat/admin")
async def chat_admin_ws(websocket: WebSocket, token: str = Query(...)):
    payload = decode_access_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4401)
        return
    admin_name = payload.get("sub") or "Destek"
    await manager.connect_admin(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            if raw.strip() == "ping":
                await websocket.send_text("pong")
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            action = msg.get("action")
            target_session = (msg.get("session_id") or "").strip()
            if not target_session:
                continue

            if action == "send":
                body = (msg.get("body") or "").strip()
                if not body:
                    continue
                body = body[:MAX_MESSAGE_LEN]
                now = datetime.now(UTC)
                async with SessionLocal() as db:
                    sess = await _get_or_create_session(db, target_session)
                    sess.status = ChatSessionStatus.OPEN.value
                    sess.last_message_at = now
                    sess.unread_customer += 1
                    cm = ChatMessage(
                        session_pk=sess.id,
                        sender="admin",
                        sender_name=admin_name,
                        body=body,
                    )
                    db.add(cm)
                    await db.commit()
                    await db.refresh(cm)
                    msg_payload = _msg_to_dict(cm, target_session)
                    sess_payload = _session_to_dict(sess)
                await manager.send_to_customer(target_session, "chat_message", msg_payload)
                await manager.send_to_admins("chat_message", msg_payload)
                await manager.send_to_admins("chat_session", sess_payload)
            elif action == "close":
                async with SessionLocal() as db:
                    sess_or_none = (
                        await db.execute(
                            select(ChatSession).where(ChatSession.session_id == target_session)
                        )
                    ).scalar_one_or_none()
                    if not sess_or_none:
                        continue
                    sess_or_none.status = ChatSessionStatus.CLOSED.value
                    await db.commit()
                    payload_dict = _session_to_dict(sess_or_none)
                await manager.send_to_admins("chat_session", payload_dict)
                await manager.send_to_customer(target_session, "chat_session", payload_dict)
            elif action == "mark_read":
                async with SessionLocal() as db:
                    sess_or_none = (
                        await db.execute(
                            select(ChatSession).where(ChatSession.session_id == target_session)
                        )
                    ).scalar_one_or_none()
                    if not sess_or_none or not sess_or_none.unread_admin:
                        continue
                    sess_or_none.unread_admin = 0
                    await db.commit()
                    payload_dict = _session_to_dict(sess_or_none)
                await manager.send_to_admins("chat_session", payload_dict)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("chat admin ws error")
    finally:
        await manager.disconnect(websocket)
