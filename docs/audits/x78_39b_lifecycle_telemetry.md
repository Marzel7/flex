# X78.39B — Append-Only Creator Funding Lifecycle Telemetry

## Deployment result: partial observability, not qualification-ready

The deployed ledger gives every observed queue lifecycle row a deterministic
logical ID derived from `(creator_address, mint)`. Retried work keeps that ID,
so it cannot be counted as new organic demand. It is secondary and fail-open:
the authoritative queue commits first, then telemetry is attempted; a telemetry
failure is a measurement gap, not a queue failure.

The listener, Creator Resolution/backfill handoff, and Creator Funding worker
now emit creation, claim, completion, and retry events. The three relevant
services were restarted and recovered with fresh heartbeats.

This is intentionally **not** a clean capacity window. `FAILED`, `EXPIRED`,
`STALE_RECOVERED`, and `EXTRACTION_STARTED` still require instrumentation, and
the lazy ledger had not yet observed a new post-deployment transition at the
first verification. Therefore lifecycle coverage and completion/expiry
accounting are not reliable enough for X78.40.
