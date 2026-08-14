from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from telemetry_gateway.database import TelemetryRepository
from telemetry_gateway.models import (
    BootRegistrationInput,
    BootRegistrationResult,
    IngestResult,
    TelemetryInput,
)
from telemetry_gateway.realtime import StatePublisher


class TelemetryService:
    def __init__(
        self,
        repository: TelemetryRepository,
        publisher: StatePublisher,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._now = now or (lambda: datetime.now(timezone.utc))

    def register_boot(self, event: BootRegistrationInput) -> BootRegistrationResult:
        return self._repository.register_boot(event)

    async def ingest(self, event: TelemetryInput) -> IngestResult:
        received_at = self._now().astimezone(timezone.utc).isoformat()
        result = self._repository.ingest(event, received_at)
        if result.current_changed and result.state is not None:
            await self._publisher.publish(result.state)
        return result
