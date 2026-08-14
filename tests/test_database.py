from telemetry_gateway.database import TelemetryStore
from telemetry_gateway.models import BootRegistrationInput, TelemetryInput


def telemetry(**overrides) -> TelemetryInput:
    values = {
        "deviceId": "device-01",
        "bootId": "boot-a",
        "sequence": 1,
        "deviceTime": "2026-08-12T09:00:00+00:00",
        "metric": "temperature",
        "value": 21.4,
    }
    values.update(overrides)
    return TelemetryInput.model_validate(values)


def test_registers_a_boot_idempotently() -> None:
    store = TelemetryStore(":memory:")
    try:
        event = BootRegistrationInput(deviceId="device-01", bootId="boot-a")

        first = store.register_boot(event)
        second = store.register_boot(event)

        assert first.to_api() == {
            "deviceId": "device-01",
            "bootId": "boot-a",
            "generation": 1,
            "created": True,
        }
        assert second.to_api() == {
            "deviceId": "device-01",
            "bootId": "boot-a",
            "generation": 1,
            "created": False,
        }
    finally:
        store.close()


def test_stores_a_basic_event_and_calculates_current_state() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))

        result = store.ingest(telemetry(), "2026-08-12T09:00:01+00:00")

        assert result.duplicate is False
        assert result.current_changed is True
        assert store.list_current_states()[0].to_api() == {
            "deviceId": "device-01",
            "bootId": "boot-a",
            "generation": 1,
            "sequence": 1,
            "deviceTime": "2026-08-12T09:00:00+00:00",
            "receivedAt": "2026-08-12T09:00:01+00:00",
            "metric": "temperature",
            "value": 21.4,
        }
        assert len(store.list_events(10)) == 1
    finally:
        store.close()


def test_repeated_event_from_same_boot_is_a_duplicate() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))
        store.ingest(telemetry(), "2026-08-12T09:00:01+00:00")

        duplicate = store.ingest(telemetry(), "2026-08-12T09:00:02+00:00")

        assert duplicate.to_api() == {
            "accepted": True,
            "duplicate": True,
            "currentChanged": False,
        }
        assert len(store.list_events(10)) == 1
    finally:
        store.close()


def test_a_new_boot_reusing_a_sequence_number_is_not_a_duplicate() -> None:
    """Event identity is (deviceId, bootId, sequence), not (deviceId, sequence).

    A device restart resets its local sequence counter to 1. That must not
    collide with sequence 1 from the device's previous boot.
    """
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))
        store.ingest(telemetry(bootId="boot-a", sequence=1), "2026-08-12T09:00:01+00:00")

        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-b"))
        result = store.ingest(
            telemetry(bootId="boot-b", sequence=1, value=30.0),
            "2026-08-12T09:05:00+00:00",
        )

        assert result.duplicate is False
        assert result.current_changed is True
        assert len(store.list_events(10)) == 2

        current = store.list_current_states()[0]
        assert current.boot_id == "boot-b"
        assert current.generation == 2
        assert current.value == 30.0
    finally:
        store.close()


def test_delayed_event_from_an_older_boot_does_not_move_state_backward() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-b"))

        store.ingest(
            telemetry(bootId="boot-b", sequence=1, value=25.0),
            "2026-08-12T09:05:00+00:00",
        )

        # A delayed event from the earlier boot arrives after the newer
        # boot has already reported. It must be recorded in raw history
        # but must not overwrite current state.
        delayed = store.ingest(
            telemetry(bootId="boot-a", sequence=99, value=1.0),
            "2026-08-12T09:06:00+00:00",
        )

        assert delayed.duplicate is False
        assert delayed.current_changed is False

        current = store.list_current_states()[0]
        assert current.boot_id == "boot-b"
        assert current.value == 25.0
        assert len(store.list_events(10)) == 2
    finally:
        store.close()


def test_lower_sequence_within_the_same_boot_does_not_move_state_backward() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))
        store.ingest(telemetry(sequence=5, value=25.0), "2026-08-12T09:05:00+00:00")

        reordered = store.ingest(
            telemetry(sequence=3, value=1.0), "2026-08-12T09:06:00+00:00"
        )

        assert reordered.current_changed is False
        assert store.list_current_states()[0].value == 25.0
    finally:
        store.close()


def test_current_state_ordering_ignores_device_time() -> None:
    """deviceTime is diagnostic metadata; ordering must use generation/sequence.

    A far-future or skewed device clock on an earlier (lower-sequence) event
    must not let it win, and a clock lagging behind an earlier event must
    not block a legitimately later (higher-sequence) one.
    """
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))

        store.ingest(
            telemetry(sequence=1, deviceTime="2026-08-12T09:00:00+00:00", value=20.0),
            "2026-08-12T09:00:01+00:00",
        )

        # sequence 2 has an earlier (skewed) deviceTime than sequence 1, but
        # it is still the newer logical event and must win.
        newer = store.ingest(
            telemetry(sequence=2, deviceTime="2026-08-12T08:00:00+00:00", value=21.0),
            "2026-08-12T09:00:02+00:00",
        )

        assert newer.current_changed is True
        assert store.list_current_states()[0].value == 21.0
    finally:
        store.close()
