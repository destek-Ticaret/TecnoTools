"""SSE (Server-Sent Events) endpoint — public stream.

NOT: httpx `ASGITransport` (test `client` fixture'ı) yanıt gövdesinin
tamamını tamponlayıp akış bitene kadar Response döndürmez. SSE sonsuz akış
olduğu için `client.stream("GET", "/api/events")` sonsuza dek bloklar ve
`timeout` da uygulanmaz. Bu yüzden SSE'yi ASGI app'i doğrudan sürerek test
ediyoruz: `http.response.start` mesajındaki status + header'ları yakalayıp
uygulamayı iptal ediyoruz (gövdeyi hiç beklemeden)."""

import asyncio

from app.main import app


async def _open_sse(path: str, *, timeout: float = 5.0):
    """SSE endpoint'e ASGI üzerinden bağlan, response-start header'larını döndür.

    Gövdeyi (sonsuz stream) beklemez; ilk `http.response.start` mesajı
    geldiğinde status + header'ları yakalar ve app coroutine'ini iptal eder
    (generator'ın `finally` bloğu çalışıp `bus.unsubscribe` yapar)."""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "scheme": "http",
    }
    started = asyncio.Event()
    captured: dict = {}

    async def receive():
        # Açık bağlantıyı simüle et — istemci kapatana (cancel) kadar bekle.
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
            captured["headers"] = {k.decode().lower(): v.decode() for k, v in message["headers"]}
            started.set()
        # http.response.body mesajlarını yok say — stream'e girmeyeceğiz.

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(started.wait(), timeout=timeout)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    return captured["status"], captured["headers"]


async def test_sse_endpoint_returns_event_stream(client):
    """SSE bağlantısı text/event-stream döner, doğru cache header'ları ile."""
    status, headers = await _open_sse("/api/events")
    assert status == 200
    assert "text/event-stream" in headers.get("content-type", "")
    assert headers.get("cache-control") == "no-cache"
    assert headers.get("x-accel-buffering") == "no"


async def test_sse_no_auth_required(client):
    """Public endpoint, auth gerekmez."""
    status, _ = await _open_sse("/api/events")
    assert status == 200
