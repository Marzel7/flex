# X78.39A — Class-Separated Creator Funding Queue Telemetry

## Result: producer census complete; capacity telemetry not yet reliable

The durable logical obligation identity is `(creator_address, mint)`. Retry and
stale-running recovery retain that identity, so they must never count as new
organic work.

However, `source` is mutable and the queue retains only current state. It is
not an append-only event log: a point-in-time table cannot reconstruct interval
creation, claim, completion, retry, reactivation and expiry accounting by
class. Current source labels also include listener, resolution, recovery and
backfill variants that require a fixed primary-class mapping at write time.

No telemetry schema was deployed because an incomplete event write would make
capacity accounting less reliable, not more reliable. X78.40 remains blocked
until atomic, fail-open class-event instrumentation is implemented and a
representative interval is collected.
