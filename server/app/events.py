"""In-process event bus feeding the per-job SSE streams."""
from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, job_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(job_id, []).append(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue) -> None:
        queues = self._subscribers.get(job_id, [])
        if queue in queues:
            queues.remove(queue)
        if not queues:
            self._subscribers.pop(job_id, None)

    def publish(self, job_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(job_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # slow consumer; drop rather than block
                pass
