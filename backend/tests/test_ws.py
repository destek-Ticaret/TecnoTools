"""WebSocket smoke testleri — auth bariyerlerinin çalıştığını doğrular.

Detaylı çift-yönlü WS akışı integration testi gerektirir (bkz.
`test_chat_e2e.py`, canlı sunucuya karşı). Burada sadece yetkisiz
bağlantıların reddedildiğini doğruluyoruz.

NOT: httpx `AsyncClient` (diğer testlerdeki `client` fixture'ı) WebSocket
DESTEKLEMEZ — `websocket_connect` metodu yoktur ve `httpx` içinde
`WebSocketUpgradeError` diye bir şey de yoktur (o `httpx_ws` paketine aittir).
In-process WS testi için Starlette'in senkron `TestClient`'ı kullanılır.

Beklenen close kodları (`/api/ws/chat/*`):
  • token query parametresi HİÇ yoksa → 1008 (FastAPI zorunlu-query
    doğrulaması bağlantıyı handler'a girmeden kapatır)
  • token var ama GEÇERSİZSE → 4401 (handler'ın açık `close(code=4401)`'i)
"""
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


@pytest.fixture
def ws_client():
    # `with` kullanmıyoruz: lifespan (scheduler vb.) başlatmaya gerek yok,
    # reddedilen bağlantılar DB'ye dokunmadan kapanıyor.
    return TestClient(app)


def test_customer_ws_rejects_missing_token(ws_client):
    """token query'si yok → 1008 (zorunlu parametre doğrulaması)."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect("/api/ws/chat/customer"):
            pass
    assert exc_info.value.code == 1008


def test_customer_ws_rejects_invalid_token(ws_client):
    """Geçersiz token → 4401."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect("/api/ws/chat/customer?token=invalid_garbage"):
            pass
    assert exc_info.value.code == 4401


def test_admin_ws_rejects_missing_token(ws_client):
    """Admin WS token query'si yok → 1008."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect("/api/ws/chat/admin"):
            pass
    assert exc_info.value.code == 1008


def test_admin_ws_rejects_invalid_token(ws_client):
    """Admin WS geçersiz token → 4401."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect("/api/ws/chat/admin?token=bogus"):
            pass
    assert exc_info.value.code == 4401
