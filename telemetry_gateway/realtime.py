from __future__ import annotations

import asyncio
from typing import Protocol

from fastapi import WebSocket

from telemetry_gateway.models import DeviceState

DEFAULT_QUEUE_SIZE = 100
SLOW_CLIENT_CLOSE_CODE = 1013  # "Try Again Later"


class StatePublisher(Protocol):
    async def publish(self, state: DeviceState) -> None: ...


class RealtimeHub:
    """Fans out state-change messages to connected dashboards.

    Each client gets its own bounded queue served by a dedicated writer
    task. publish() only ever enqueues (never awaits a socket write), so one
    client that cannot keep up cannot stall delivery to the others. A client
    whose queue fills is dropped instead of being allowed to buffer without
    limit.
    """

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._queues: dict[WebSocket, asyncio.Queue[dict]] = {}
        self._writers: dict[WebSocket, asyncio.Task[None]] = {}

    async def connect(self, client: WebSocket) -> None:
        await client.accept()
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._queue_size)
        self._queues[client] = queue
        self._writers[client] = asyncio.create_task(self._write_loop(client, queue))

    def disconnect(self, client: WebSocket) -> None:
        self._queues.pop(client, None)
        writer = self._writers.pop(client, None)
        if writer is not None:
            writer.cancel()

    async def publish(self, state: DeviceState) -> None:
        message = {"type": "device.state.changed", "data": state.to_api()}
        for client, queue in tuple(self._queues.items()):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self._drop_slow_client(client)

    def _drop_slow_client(self, client: WebSocket) -> None:
        self.disconnect(client)
        asyncio.create_task(client.close(code=SLOW_CLIENT_CLOSE_CODE))

    async def _write_loop(self, client: WebSocket, queue: asyncio.Queue[dict]) -> None:
        try:
            while True:
                message = await queue.get()
                await client.send_json(message)
        except Exception:
            self.disconnect(client)

    @property
    def size(self) -> int:
        return len(self._queues)
