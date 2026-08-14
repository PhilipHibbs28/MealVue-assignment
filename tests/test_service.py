import asyncio
from datetime import datetime, timezone

import pytest

from telemetry_gateway.models import (
    BootRegistrationResult,
    DeviceState,
    IngestResult,
    TelemetryInput,
)
from telemetry_gateway.service import TelemetryService


def make_event(**overrides) -> TelemetryInput:
    values = {
        "deviceId": "device-01",
        "bootId": "boot-a",
        "sequence": 1,
        "deviceTime": "2026-08-12T09:00:00Z",
        "metric": "temperature",
        "value": 21.4,
    }
    values.update(overrides)
    return TelemetryInput.model_validate(values)


def make_state(**overrides) -> DeviceState:
    values = {
        "device_id": "device-01",
        "boot_id": "boot-a",
        "generation": 1,
        "sequence": 1,
        "device_time": "2026-08-12T09:00:00+00:00",
        "received_at": "2026-08-12T09:00:01+00:00",
        "metric": "temperature",
        "value": 21.4,
    }
    values.update(overrides)
    return DeviceState(**values)


class FakeRepository:
    def __init__(self, state: DeviceState, result: IngestResult | None = None) -> None:
        self.state = state
        self.result = result if result is not None else IngestResult(False, True, state)
        self.ingest_calls = 0
        self.raise_on_ingest: Exception | None = None

    def register_boot(self, _event):
        return BootRegistrationResult("device-01", "boot-a", 1, True)

    def ingest(self, _event, _received_at):
        self.ingest_calls += 1
        if self.raise_on_ingest is not None:
            raise self.raise_on_ingest
        return self.result

    def list_current_states(self):
        return []

    def list_events(self, _limit):
        return []

    def ping(self):
        return True


class RecordingPublisher:
    def __init__(self) -> None:
        self.states: list[DeviceState] = []

    async def publish(self, state: DeviceState) -> None:
        self.states.append(state)


def test_service_publishes_a_state_during_ingestion() -> None:
    event = make_event()
    state = make_state()
    repository = FakeRepository(state)
    publisher = RecordingPublisher()
    service = TelemetryService(
        repository,
        publisher,
        now=lambda: datetime(2026, 8, 12, 9, 0, 1, tzinfo=timezone.utc),
    )

    result = asyncio.run(service.ingest(event))

    assert result.current_changed is True
    assert publisher.states == [state]
    assert repository.ingest_calls == 1


def test_ingest_only_publishes_after_the_repository_call_returns() -> None:
    """Publication must follow the database write, not precede it.

    A publish-before-commit ordering would let a duplicate or stale event
    (which the repository call may still reject) already be on the wire.
    """
    event = make_event()
    state = make_state()
    repository = FakeRepository(state)
    calls: list[str] = []

    class OrderTrackingPublisher:
        async def publish(self, _state: DeviceState) -> None:
            calls.append("publish")

    original_ingest = repository.ingest

    def tracking_ingest(*args, **kwargs):
        calls.append("ingest")
        return original_ingest(*args, **kwargs)

    repository.ingest = tracking_ingest  # type: ignore[method-assign]
    service = TelemetryService(repository, OrderTrackingPublisher())

    asyncio.run(service.ingest(event))

    assert calls == ["ingest", "publish"]


def test_duplicate_event_does_not_publish() -> None:
    event = make_event()
    repository = FakeRepository(make_state(), result=IngestResult(duplicate=True, current_changed=False))
    publisher = RecordingPublisher()
    service = TelemetryService(repository, publisher)

    result = asyncio.run(service.ingest(event))

    assert result.duplicate is True
    assert publisher.states == []


def test_stale_event_that_does_not_change_current_state_does_not_publish() -> None:
    event = make_event()
    repository = FakeRepository(
        make_state(), result=IngestResult(duplicate=False, current_changed=False)
    )
    publisher = RecordingPublisher()
    service = TelemetryService(repository, publisher)

    result = asyncio.run(service.ingest(event))

    assert result.current_changed is False
    assert publisher.states == []


def test_failed_transaction_never_publishes() -> None:
    event = make_event()
    repository = FakeRepository(make_state())
    repository.raise_on_ingest = RuntimeError("database is locked")
    publisher = RecordingPublisher()
    service = TelemetryService(repository, publisher)

    with pytest.raises(RuntimeError):
        asyncio.run(service.ingest(event))

    assert publisher.states == []
