import asyncio

from telemetry_gateway.models import DeviceState
from telemetry_gateway.realtime import RealtimeHub


def make_state(value: float = 1.0) -> DeviceState:
    return DeviceState(
        device_id="device-01",
        boot_id="boot-a",
        generation=1,
        sequence=1,
        device_time="2026-08-12T09:00:00+00:00",
        received_at="2026-08-12T09:00:01+00:00",
        metric="temperature",
        value=value,
    )


class FakeWebSocket:
    def __init__(self, *, block_send: bool = False) -> None:
        self.accepted = False
        self.closed_code: int | None = None
        self.sent: list[dict] = []
        self._block_send = block_send
        self._release = asyncio.Event()

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        if self._block_send:
            await self._release.wait()
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code

    def release(self) -> None:
        self._release.set()


async def _drain() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_healthy_client_receives_published_state() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        client = FakeWebSocket()
        await hub.connect(client)

        await hub.publish(make_state())
        await _drain()

        assert client.accepted is True
        assert client.sent == [
            {"type": "device.state.changed", "data": make_state().to_api()}
        ]

    asyncio.run(scenario())


def test_slow_client_is_dropped_without_blocking_healthy_clients() -> None:
    async def scenario() -> None:
        hub = RealtimeHub(queue_size=2)
        slow = FakeWebSocket(block_send=True)
        fast = FakeWebSocket()

        await hub.connect(slow)
        await hub.connect(fast)

        # The slow client's writer stalls on its first send, so its queue
        # backs up and is dropped once it exceeds the bound. The fast
        # client must keep receiving every message regardless.
        for index in range(5):
            await hub.publish(make_state(value=float(index)))
            await _drain()

        assert len(fast.sent) == 5
        assert hub.size == 1
        assert slow.closed_code == 1013

    asyncio.run(scenario())


def test_disconnect_stops_further_delivery() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        client = FakeWebSocket()
        await hub.connect(client)
        hub.disconnect(client)

        await hub.publish(make_state())
        await _drain()

        assert client.sent == []
        assert hub.size == 0

    asyncio.run(scenario())


def test_disconnect_is_idempotent() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        client = FakeWebSocket()
        await hub.connect(client)

        hub.disconnect(client)
        hub.disconnect(client)

        assert hub.size == 0

    asyncio.run(scenario())
