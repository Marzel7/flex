# X77.2 — Lossless ws_cascade Write Handling

## Objective

`ws_cascade`'s background event writer (`_event_writer_loop` in
`src/core/ws_cascade_store.py`) silently dropped `wt_webhook_hits` and
`watchtower_events` writes whenever the write failed for any reason — the
exception was logged and the item discarded. Acceptable for correctness (no
attribution/candidate-generation path reads these two tables), but a real
audit-trail loss. This milestone retains every event without weakening
throughput: retry only transient contention failures, never retry
constraint/schema/data failures, keep writes idempotent, expose
queued/retried/failed/dropped/succeeded, and never block the event loop.

## Design

### Where the loss actually happened

`_event_writer_loop` dequeues an item, calls `operations_write(...)` (which
routes through `database_write_service.submit` — the cross-process write
lease + a native `sqlite3.connect(..., timeout=10)`), and on any exception
just printed a log line. The item was already dequeued, so nothing survived
the failure. The realistic failure modes reaching this catch block are:

- `DatabaseWriteLockError` — raised by `database_write_service._execute` when
  the native connection's own 10s SQLite busy-timeout is exceeded (the write
  lease itself already serializes cross-process writers via `fcntl.flock`, so
  this is rare, but possible if a single transaction runs unusually long).
- `NestedDatabaseWriteError` — a same-thread reentrancy trip. Structurally
  shouldn't fire from this dedicated writer thread (it holds no other
  managed transaction), but is classified as transient anyway since a
  differently-scheduled retry cannot repeat the same reentrancy.
- Any other exception (constraint violation, schema mismatch, malformed
  payload) — a real bug, not contention. Retrying it would fail identically
  forever.

### The fix

New durable table `wt_pending_cascade_events` in `wt_ops_v2.db`, deliberately
modeled on the existing `wt_pending_session_writes` retry queue (same
`PENDING/WRITTEN/SUPERSEDED/FAILED` vocabulary, same 30s maintenance-loop
drain cadence) rather than inventing a new pattern:

```sql
CREATE TABLE wt_pending_cascade_events (
    id, kind, payload_json, dedupe_key,
    enqueued_at, retry_count, last_retry_at, last_error, state
)
```

- **`_is_transient_write_failure(exc)`** — the single classification choke
  point. Returns `True` only for `DatabaseWriteLockError`,
  `NestedDatabaseWriteError`, or a raw `sqlite3.OperationalError` containing
  "locked". Returns `False` for `IntegrityError`/`ProgrammingError` and, by
  explicit default, for any unrecognized exception class — unknown failures
  fail loud rather than being silently absorbed into an ever-growing retry
  queue.
- **`_write_cascade_item(c, item, wh_id)`** — extracted as the single write
  implementation, used by both the live-queue path and the retry-drain path,
  so a retried write is byte-identical to a first-attempt write (no
  parallel/divergent code paths to keep in sync).
- **`enqueue_pending_cascade_event`** — persists the failed item's original
  tuple (JSON-encoded) plus the error, only called when
  `_is_transient_write_failure` is `True`.
- **`drain_pending_cascade_events`** — retries up to 50 `PENDING` rows per
  cycle (oldest first), using the same `operations_write` path. On success:
  `state='WRITTEN'`. On a transient retry failure: `retry_count+1`, stays
  `PENDING`. On a **non**-transient retry failure: `state='FAILED'` —
  retrying is stopped rather than looping forever, since a permanent failure
  surfacing on retry means the original classification (or the data itself)
  was wrong.
- **Idempotency**: `'hit'` items have a natural key already enforced by
  `wt_webhook_hits`' own `UNIQUE(tx_signature, wallet_address)` index — reused
  as `dedupe_key` (`hit:{sig}:{treasury}`) on `wt_pending_cascade_events` too,
  so a `'hit'` can never double-enqueue even if the same failure is reported
  twice. `'event'` rows have no natural key (`watchtower_events` is an
  append-only log by design) — `dedupe_key` stays `NULL` there, and duplicates
  are simply distinct log entries, which is correct (two genuinely different
  events should both survive).
- **In-process counters** (`_event_writer_stats`, reset on restart, same
  convention as the pre-existing `_subprov_sig_metrics`): `succeeded`,
  `queued_for_retry`, `retried_ok`, `failed_permanent`, `dropped_queue_full`.
  `dropped_queue_full` is bumped in both `emit_event` and
  `record_treasury_hit`'s pre-existing `queue.Full` catch — the queue-full
  case is a genuine loss (the in-memory `_event_q` is bounded at 5000, not
  durable), so it's counted rather than silently passed.
- **`cascade_write_health_report()`** (new method on `WebSocketCascade` in
  `ws_cascade.py`) combines the in-process rate counters with the durable
  `wt_pending_cascade_events` state counts (`pending_cascade_event_counts`),
  so a freshly-restarted process still reports a true backlog even before it
  has retried anything itself. `status` is `DEGRADED` whenever a durable
  `PENDING` or `FAILED` backlog exists, `HEALTHY` otherwise.

### Event-loop safety

`_event_writer_loop` already runs on its own dedicated background thread
(`ws-cascade-event-writer`), started once via `_ensure_writer()` — this
predates X77.2. The new enqueue-on-failure path (`db_connect` +
`enqueue_pending_cascade_event`) executes entirely on that same background
thread, never on the asyncio event loop. `drain_pending_cascade_events` is
invoked from the maintenance loop via `await _ato_thread(...)` — the exact
same off-loop dispatch mechanism `_drain_pending_sessions` already uses —
so a slow retry round-trip under contention cannot stall WS processing.

### Ordering

Not preserved across a retry, by design: `drain_pending_cascade_events`
processes `PENDING` rows oldest-`enqueued_at`-first, but a row that fails
transiently and re-queues can end up written after a later item that
succeeded on its first attempt. `watchtower_events`/`wt_webhook_hits` are
both timestamp-carrying append-only logs (not state machines), so
write-order does not affect correctness — every consumer of these tables
already reads by `created_at`/`block_time`, not insertion order.

## Validation

New file `tests/test_x77_2_lossless_cascade_write_handling.py`, 14 tests, all
passing:

1. **Classification** (5 tests) — `DatabaseWriteLockError`,
   `NestedDatabaseWriteError`, and a raw locked `OperationalError` classify
   transient; `IntegrityError` and an arbitrary `ValueError` classify
   non-transient.
2. **Retry success** (2 tests) — a simulated first-attempt lock failure is
   enqueued, then `drain_pending_cascade_events` retries it successfully;
   verified for both `'event'` (→ `watchtower_events`) and `'hit'`
   (→ `wt_webhook_hits`, verifying wallet/sig/amount/direction columns land
   correctly).
3. **Non-transient never retried** (2 tests) — confirms the classifier itself
   rejects `IntegrityError`; confirms a row that fails non-transiently
   *during* a retry attempt is marked `FAILED` (stops retrying) rather than
   staying `PENDING` forever.
4. **Idempotency** (2 tests) — a `'hit'` item enqueued twice (simulating two
   failed attempts) produces exactly one row (`dedupe_key` collision); two
   distinct `'event'` items both persist (no false dedupe on the
   no-natural-key case).
5. **Counters** (3 tests) — a transient failure bumps `queued_for_retry`; a
   non-transient failure bumps `failed_permanent` and, critically, never
   enqueues; `pending_cascade_event_counts` reflects durable state correctly.

## Regression

Targeted run, 186/186 passing:
`test_x77_2_lossless_cascade_write_handling.py` (14/14, new),
`test_ws_cascade_connection_leak.py` (3/3),
`test_database_write_service.py` (9/9),
`test_cdc_phase1.py` (9/9),
`test_x24_1_detection_path.py` (3/3),
`test_x24_2_1_sweep_concurrency.py` (11/11),
`test_x24_3_rpc_deadline.py` (23/23),
`test_x24_2_restart_durability.py` (3/3),
`test_x24_2_2_sig_write_merge.py` (6/6),
`test_x24_2_fair_sweep_scheduler.py` (9/9),
`test_x26_3_subprov_infrastructure_exclusion.py` (17/17),
`test_x28_0_decouple_creator_watch_lifetime.py` (12/12),
`test_x65_44_watchtower_registry_promotion.py` (21/21),
`test_x64_9b1_dedupe_instrumentation.py` (37/37),
`test_x65_41_canonical_registry_definition.py` (3/3),
`test_x65_44_walkback_worker_promotion_hook.py` (6/6).

No changes to `emit_event`/`record_treasury_hit`'s public signatures, no
changes to `wt_webhook_hits`/`watchtower_events` schemas, no changes to any
session/candidate/subprov classification logic — this milestone touches only
the failure path of the existing background writer.

## Acceptance

- ✅ Every event retained (transient failures durably queued, not dropped).
- ✅ Throughput unweakened (writer loop's happy path is unchanged; retry work
  happens off the event loop, on the existing dedicated writer thread /
  maintenance-loop `_ato_thread` dispatch).
- ✅ Only transient `SQLITE_BUSY`-class failures retry; constraint/schema/data
  failures never retry (`_is_transient_write_failure`, tested both ways).
- ✅ Bounded retries (50/cycle, every 30s — same cadence as the pre-existing
  session-write retry queue).
- ✅ Ordering: not required for append-only, timestamp-carrying event logs;
  explicitly not attempted (documented above).
- ✅ Idempotent (`'hit'` dedupe on natural key; `'event'` rows are
  legitimately distinct log entries).
- ✅ `queued`/`retried`/`failed`/`dropped`/`succeeded` exposed via
  `event_writer_stats()` (in-process) and `cascade_write_health_report()`
  (combined in-process + durable, ready for X77.3's Mission Control
  integration).
- ✅ No event-loop blocking (writer thread + `_ato_thread` dispatch, both
  pre-existing off-loop mechanisms, reused rather than reinvented).
- ✅ Validated under simulated contention (transient-failure-then-retry
  tests) — a live soak under real concurrent load is X77.3's scope.

## Files changed

- `src/core/ws_cascade_store.py` — `wt_pending_cascade_events` schema;
  `_is_transient_write_failure`, `_item_dedupe_key`,
  `enqueue_pending_cascade_event`, `_write_cascade_item`,
  `drain_pending_cascade_events`, `pending_cascade_event_counts`,
  `event_writer_stats`; `_event_writer_loop` and `record_treasury_hit`
  updated to classify-and-queue instead of drop.
- `src/core/ws_cascade.py` — `_drain_pending_cascade_events` (mirrors
  `_drain_pending_sessions`), wired into the maintenance loop at the same 30s
  cadence; `cascade_write_health_report()`.
- `tests/test_x77_2_lossless_cascade_write_handling.py` — new, 14 tests.

[x77_2_lossless_ws_cascade_write_handling.md](docs/audits/x77_2_lossless_ws_cascade_write_handling.md)
