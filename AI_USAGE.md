# AI usage record

## Tools used

- Claude Code (Sonnet 5), used interactively for the full assignment: reading the protocol/runtime/API docs, locating the defects, implementing fixes, and writing tests.

## Important prompts or prompt summaries

- Initial prompt: asked for help completing the assignment. The assistant was directed to read `README.md`, `TASK.md`, and all three files under `docs/` before touching any code, per the assignment's own instructions.
- Follow-up direction: for each of the six required problem areas, find the specific line(s) of code that violate the documented contract, fix only that behavior, and add a focused test that would fail on the old code and passes on the fix.
- Asked the assistant to run the existing test suite before and after each change to catch regressions immediately, and to run the app against `simulator.py --chaos` to observe the actual failure/fix in a live run rather than trusting the diff alone.

## Generated output rejected or corrected

- The assistant's first draft of the reconnect-refetch fix in `app.js` included a three-line explanatory comment; trimmed to one line to match the project's "no multi-line comments" convention.
- No functional AI output was rejected outright in this session — each of the six fixes was verified against the specific doc requirement it corresponds to (see below) before being accepted. The main review discipline applied was checking every fix against `docs/protocol.md` and `docs/runtime-contract.md` line-by-line rather than accepting a plausible-looking diff.
- Reviewed independently: confirmed the `UNIQUE (device_id, sequence)` constraint was the actual root cause of the boot-restart bug (not just a hunch) by tracing `INSERT OR IGNORE` behavior against the chaos simulator's restart-with-`sequence=1` sequence in a live run before writing the migration.

## Verification performed

- Read `docs/protocol.md`, `docs/runtime-contract.md`, and `docs/api.md` and mapped each of the six required problem areas to a concrete contract statement before writing any fix.
- Ran `python -m compileall` and the full `pytest` suite after every change; grew the suite from 7 to 19 tests, with new/updated coverage for: cross-boot sequence-number reuse, delayed events from an older boot, out-of-order sequence within one boot, ordering ignoring `deviceTime`, publish-after-commit ordering, no-publish on duplicate/no-op/failed-transaction, and slow-client isolation/memory bound on the WebSocket hub.
- Started the real application (`python -m telemetry_gateway`) against a scratch SQLite database and drove it with `simulator.py --devices 3 --chaos` for ~30 seconds, then inspected `/api/devices` and `/api/events` responses directly to confirm: a restarted boot's first event now reports `currentChanged: true` with the correct new generation; delayed/duplicate events report `currentChanged: false`; and clock-skewed events (`deviceTime` far in the future/past) still update current state correctly because ordering no longer depends on `deviceTime`.
- Manually re-read the final diff for `telemetry_gateway/realtime.py`, `service.py`, `database.py`, `migrations.py`, and `static/app.js` against the runtime contract to confirm no unrelated behavior changed.

**Note for review:** this file was drafted by the same AI session that made the code changes. Per the assignment's own evaluation criteria ("ability to direct and verify AI-generated work"), you should personally re-read the diff and the tests before submitting, and adjust this file to reflect anything you changed, questioned, or verified yourself.
