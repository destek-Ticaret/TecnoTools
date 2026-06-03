"""In-memory pub-sub for SSE.

Single-instance kullanım için yeterli. Multi-instance deploy için Redis pub/sub'a
geçilmesi gerekir (RedisStreams veya redis-py pub/sub).
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class EventBus:
    """Asenkron kuyruk tabanlı pub/sub. Her abonenin kendi kuyruğu var."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def publish(self, event: str, data: dict | list | None = None) -> None:
        payload = json.dumps({"event": event, "data": data})
        # Snapshot — yayın esnasında set'i değiştirme
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("SSE queue full, dropping event for slow subscriber")


bus = EventBus()


async def event_stream(q: asyncio.Queue, heartbeat_interval: float = 25.0) -> AsyncIterator[str]:
    """SSE format'ında string yield eder. Heartbeat ile bağlantı canlı tutulur."""
    try:
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=heartbeat_interval)
                yield f"data: {payload}\n\n"
            except TimeoutError:
                # Proxy'ler boş bağlantıları kesmesin diye keep-alive
                yield ": heartbeat\n\n"
    except asyncio.CancelledError:
        raise
