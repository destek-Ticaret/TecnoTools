"""Server-Sent Events endpoint — storefront ve admin canlı bildirim için dinler."""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.events import bus, event_stream

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
async def stream():
    """Public SSE — tüm event'ler frontend'e geçer. Hassas veri yayınlanmaz."""
    q = await bus.subscribe()

    async def gen():
        try:
            async for chunk in event_stream(q):
                yield chunk
        finally:
            await bus.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx tampon kapatma
        },
    )
