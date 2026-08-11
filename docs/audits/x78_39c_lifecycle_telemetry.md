# X78.39C — Lifecycle Telemetry Completion Gate

## Implemented lifecycle contract

The secondary, append-only ledger now records the mutation-adjacent lifecycle
events that exist in the current queue model:

`CREATED`, `CLAIMED`, `EXTRACTION_STARTED`, `RETRY`, `COMPLETED`, `FAILED`,
`EXPIRED`, and `STALE_RECOVERED`.

`ELIGIBLE` is not a stored transition in this queue: a pending/retry row becomes
eligible as wall-clock time passes `next_attempt_at`. It is consequently
represented by the subsequent `CLAIMED` event, rather than inventing a timer
writer or a duplicate state transition. `REQUEUED` is likewise not emitted:
the only actual requeue transitions are `RETRY` and `STALE_RECOVERED`, which
remain distinct.

Each event carries the stable logical obligation identity derived from the
creator/mint pair and the original source-derived work class. The select path
preserves `source` when the schema supplies it; compatibility fixtures that
predate that column remain explicitly `OTHER_PROVEN_SOURCE`, never guessed.

## Gap accounting and safety

Queue mutations commit before telemetry. If event persistence fails, a short,
best-effort append to `creator_funding_lifecycle_gaps` records the missing
measurement. If the database is unavailable for both writes, processing still
continues and the window cannot qualify as clean. This preserves the required
fail-open operational boundary without converting telemetry into a queue-write
dependency.

## Producer / consumer census

Producers are the PumpFun listener enqueue path and the Creator Resolution
handoff/backfill enqueue paths. The Creator Funding worker is the canonical
queue consumer and owns claims, extraction start, retry, terminal failure,
completion, stale recovery, and expiry. The legacy listener periodic queue
code remains excluded from live ownership by the existing sole-consumer worker
configuration; it was not re-enabled or altered by this work.

## Qualification status

Targeted queue regressions pass. This is still not a capacity qualification:
the updated worker must be restarted, then a representative 60-minute window
must show zero durable telemetry gaps and reconcile creations, claims,
terminal completions/failures/expiries and in-flight work by logical identity.
Until then X78.40 remains blocked.
