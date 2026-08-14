# Engineering decisions

## Invariants identified

- Logical event identity is `(deviceId, bootId, sequence)`. Sequence numbers are only unique within one boot; a device restart resets its counter to 1.
- Current-state ordering for a `(deviceId, metric)` pair is `(generation, sequence)`, compared in that order. `deviceTime` is diagnostic only and must never affect ordering, since device clocks are untrusted.
- The database transaction is the source of truth. A realtime WebSocket message may only be sent after that transaction commits, and only when it actually changed current state.
- The raw `telemetry_events` audit table must retain one row per logical event forever, including events that never become current state (duplicates excepted).
- A slow or stalled WebSocket reader must not be allowed to block delivery to other clients or grow server memory without bound.
- The dashboard's WebSocket feed is a notification channel, not a data source; the `/api/devices` snapshot is authoritative and must be re-fetched on every connect, including reconnects.

## Incidents fixed

1. **Event identity collided across boots.** `telemetry_events` was uniqued on `(device_id, sequence)` instead of `(device_id, boot_id, sequence)`. After a device restart, its new boot's `sequence=1` collided with the previous boot's `sequence=1` row, so `INSERT OR IGNORE` silently discarded the first genuinely-new event of every new boot (returned as a false duplicate, current state never updated). Fixed with migration `002`, which rebuilds the table with the correct composite unique constraint. Verified against `simulator.py --chaos`, which restarts device boots and reuses low sequence numbers — the first event after a restart now correctly reports `currentChanged: true` with the new generation.

2. **Current-state ordering used `deviceTime` instead of `(generation, sequence)`.** The `ON CONFLICT` update on `current_state` compared `excluded.device_time > current_state.device_time`, directly violating the protocol ("Do not use `deviceTime` to decide current state"). A delayed or clock-skewed event could move current state backward, and a device with a lagging clock could be permanently blocked from updating state. Fixed the `WHERE` clause to compare `generation` first, then `sequence`.

3. **Realtime publish happened before, and independent of, the database transaction.** `TelemetryService.ingest` called a `preview_state()` helper and published to the WebSocket hub *before* calling `repository.ingest()`. This meant duplicates, stale/out-of-order events, and even failed transactions all produced a "successful" realtime update — directly violating the runtime contract. Fixed by reordering: `repository.ingest()` runs first, and the result is only published when it reports `current_changed` and carries a `state`. Removed `preview_state` entirely since it was a placeholder for the state used only for this incorrect pre-publish read.

4. **A slow WebSocket client could block every other client and grow memory unbounded.** `RealtimeHub.publish()` looped over clients and `await`ed `send_json` on each in turn — one stuck client stalled the whole fan-out, and there was no bound on how much could back up behind it. Rewrote `RealtimeHub` so each client has its own bounded `asyncio.Queue` served by a dedicated writer task. `publish()` only ever does a non-blocking `put_nowait`; a client whose queue fills (default bound: 100 pending messages) is dropped and its socket closed with code `1013`, without affecting delivery to any other client.

5. **The dashboard never refreshed its snapshot after a reconnect.** `app.js` fetched `/api/devices` once at page load and never again; since WebSocket delivery isn't guaranteed to be replayed, any state changes missed while disconnected (page backgrounded, network blip, server restart) were never recovered. Fixed by fetching the snapshot on every WebSocket `open` event, including reconnects, and removed the now-redundant standalone fetch before `connect()`.

## Design choices and trade-offs

- The event-identity fix required a schema migration (`002`) rather than editing migration `001` in place, so that databases that already applied `001` (any existing local `data/telemetry.db`) get repaired on next startup instead of silently keeping the broken constraint.
- Chose a per-client bounded queue + writer task over alternatives like `asyncio.wait_for` timeouts around each `send_json`: the queue approach fully decouples the publish path from any individual client's network speed, so `publish()` never awaits I/O on a socket at all. The trade-off is a small amount of added complexity (one task per connection) and a fixed memory ceiling per client (queue size × message size) rather than zero.
- Kept the slow-client threshold as a simple queue-length bound (100 messages) rather than a byte-size or time-based bound. This is simpler to reason about and sufficient for a single-metric, low-frequency telemetry feed; a higher-fan-out system might want a byte-based bound instead.
- Did not change the wire-visible API shape (`/api/telemetry`, `/api/boots`, `/api/devices`, `/api/events`, WebSocket message format) — all fixes are internal to how state is computed and published.

## Schema or API compatibility concerns

- Migration `002` rebuilds `telemetry_events` (SQLite requires a table rebuild to change a `UNIQUE` constraint). It preserves all existing rows, including `id` values, so `/api/events` ordering and any external references to event ids are unaffected. It runs automatically on startup via the existing migration runner; no manual data intervention is required.
- No response shape changed. `IngestResult.to_api()` and `DeviceState.to_api()` are unchanged; the WebSocket close code `1013` for evicted slow clients is a new behavior but is standard-compliant and only observable by a client that was already failing to keep up.

## Remaining risks or incomplete work

- The slow-client queue bound (100) and close code are not configurable via environment/CLI; a deployment with much higher fan-out or larger messages might want to tune this.
- No automated browser test covers the dashboard reconnect-refetch behavior (`app.js`); it was verified manually by inspecting the code path and confirming `/api/devices` is authoritative and idempotent to re-fetch. A future improvement would add a lightweight JS/e2e test if the project adopts a frontend test runner.
- `register_boot` and `ingest` still serialize through a single `RLock` around one SQLite connection (unchanged from the original design). This is adequate for the local, single-machine scope of this assignment but would not scale to concurrent writers across processes.
- Clock-skew handling is limited to "don't use it for ordering." There's no anomaly detection or alerting on devices reporting wildly incorrect `deviceTime`; it's stored as-is for diagnostic purposes, per the protocol.
