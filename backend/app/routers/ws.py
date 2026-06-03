"""WebSocket altyapısı — çift yönlü bağlantı.

SSE tek yönlü (server→client). WebSocket admin'in storefront kullanıcılarına push
bildirim gönderebilmesi için çift yönlü.

Connection manager:
- Genel kanal (broadcast): tüm bağlı istemcilere
- Authenticated kanal: sadece admin paneline (JWT ile)

NOT: Multi-instance deploy'da Redis pub/sub gerekir; şu an single-instance.
"""

import asyncio
import json
import logging
from collections.abc import Iterable

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.security import decode_access_token
from app.services.events import bus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ws", tags=["ws"])


class ConnectionManager:
    def __init__(self) -> None:
        self._public: set[WebSocket] = set()
        self._admin: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, *, is_admin: bool) -> None:
        await ws.accept()
        async with self._lock:
            (self._admin if is_admin else self._public).add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._public.discard(ws)
            self._admin.discard(ws)

    async def _broadcast(self, conns: Iterable[WebSocket], message: dict) -> None:
        payload = json.dumps(message)
        dead: list[WebSocket] = []
        for ws in list(conns):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._public.discard(ws)
                    self._admin.discard(ws)

    async def broadcast_all(self, event: str, data: dict | list | None = None) -> None:
        msg = {"event": event, "data": data}
        await self._broadcast(self._public | self._admin, msg)

    async def broadcast_admin(self, event: str, data: dict | list | None = None) -> None:
        await self._broadcast(self._admin, {"event": event, "data": data})

    async def broadcast_public(self, event: str, data: dict | list | None = None) -> None:
        await self._broadcast(self._public, {"event": event, "data": data})


manager = ConnectionManager()


async def _relay_bus_to_manager() -> None:
    """EventBus'tan gelen mesajları WebSocket istemcilerine yayınla."""
    q = await bus.subscribe()
    try:
        while True:
            raw = await q.get()
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            event = payload.get("event")
            data = payload.get("data")
            # Sadece public alanı ilgilendirenler dışına filtre koymak istersek burada
            if event in ("order_created", "order_status_changed"):
                await manager.broadcast_admin(event, data)
            else:
                await manager.broadcast_all(event, data)
    finally:
        await bus.unsubscribe(q)


_relay_task: asyncio.Task | None = None


def ensure_relay_started() -> None:
    global _relay_task
    if _relay_task is None or _relay_task.done():
        _relay_task = asyncio.create_task(_relay_bus_to_manager())


@router.websocket("/public")
async def public_ws(websocket: WebSocket):
    """Public WS — herkes bağlanabilir; sadece ürün ve genel event'ler alır."""
    ensure_relay_started()
    await manager.connect(websocket, is_admin=False)
    try:
        # İstemciden ping/pong veya komut beklemiyoruz; sadece bağlantıyı tutuyoruz
        while True:
            msg = await websocket.receive_text()
            # Echo destekli — istemci kendi pong'unu gönderebilir
            if msg.strip() == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await manager.disconnect(websocket)


@router.websocket("/admin")
async def admin_ws(websocket: WebSocket, token: str = Query(...)):
    """Admin WS — JWT access token query string'de. Sadece authenticated."""
    ensure_relay_started()
    payload = decode_access_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4401)
        return
    await manager.connect(websocket, is_admin=True)
    try:
        while True:
            data = await websocket.receive_text()
            if data.strip() == "ping":
                await websocket.send_text("pong")
                continue
            # Admin → frontend kullanıcılarına push (örn: site genelinde duyuru)
            try:
                msg = json.loads(data)
                if msg.get("action") == "broadcast" and msg.get("event"):
                    await manager.broadcast_public(msg["event"], msg.get("data"))
            except Exception:
                pass
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
