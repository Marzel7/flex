# X77.0 — Database Write Contention & Throughput Audit

## Objective

X76 completed the platform's correctness programme (merge safety,
identity projection, treasury governance, walkback self-healing). The
remaining issue — recurring `SQLITE_BUSY`/lock contention between
`walkback_worker` and `ws_cascade` on `wt_ops_v2.db` — is explicitly
NOT a correctness problem (both paths route through the same
cross-process write lease, so no data race exists). This is a
throughput/contention audit: determine whether the observed contention
is expected, avoidable, or symptomatic of a real architectural
bottleneck, and implement only if evidence justifies it.

## Phase 1 — Writer inventory

12 Supervisor-managed processes total (`config/supervisor/supervisord.conf`).
Two independent, real serialization mechanisms exist for the SAME
databases and must both be understood together:

1. **`TrackedConnection`** (`src/utils/db_locking.py`) — an in-process
   `threading.Lock` (`_DB_WRITE_LOCK`) acquired lazily on the first write
   statement of a transaction, held until `commit()`/`close()`/`rollback()`.
   Per-process only.
2. **`DatabaseWriteService`** (`src/core/database_write_service.py`) — a
   per-database single worker-thread queue, backed by a **cross-process**
   `fcntl.flock` on `<db-path>.write.lock`. This file lock is what both
   `TrackedConnection` (via `_acquire_write_lane()` → `acquire_write_lease()`)
   AND `DatabaseWriteService` (via the same `acquire_write_lease()`) ultimately
   converge on — this is the actual cross-process safety boundary, and it
   is shared correctly. No data-race risk exists; the two mechanisms are
   two different client APIs onto the same underlying lock.

| # | Process | DB(s) written | Frequency | Connection lifetime | Write path |
|---|---|---|---|---|---|
| 1 | watchtower_api (gunicorn) | live DB | per HTTP request | per-request, closed after | `db_connect` (some inert bypass threads, gated off in prod via `FLEX_ENABLE_FLASK_BACKGROUND_WORKERS=0`) |
| 2 | watchtower_listener | live DB | varies; most sub-loops parked (`LISTENER_*_ENABLED=0`) | not deeply audited (12.1k-line file) | `db_connect`; birth reconciler explicitly disabled due to a documented "write-lane blocks event loop up to 60s" issue |
| 3 | watchtower_helius_monitor | live DB (`helius_usage_snapshots`) | disabled (`autostart=false`) | — | raw `sqlite3.connect` bypass — **inert** |
| 4 | operation_scheduler | ops DB | intake 900s, forward-monitor 180s, run-log per job | one `db_connect()` per call, closed in `finally` | `db_connect`, explicit comment: "short transaction, after the job, never during RPC" |
| 5 | intelligence_snapshot_scheduler | none (writes JSON files) | 300-1800s per window | N/A | N/A |
| 6 | **ws_cascade** | ops DB (heavy), live DB (narrow) | evented (WS-driven bursts) + 2s persist-loop + 30s expire-loop | **mixed** — see detail below | **both** `db_connect` (majority) and `DatabaseWriteService` (event log, webhook hits, ws-usage registration, schema init, heartbeat) |
| 7 | creator_resolution_worker | live DB | 5s (busy) / 60s (idle) | one `db_connect()`/call | `db_connect` |
| 8 | creator_funding_worker | live DB | 3s (busy) / 15s (idle) | one `db_connect()`/call | `db_connect` |
| 9 | **walkback_worker** | ops DB | every 45s, batch=8 | one `TrackedConnection` per ~45s cycle, several writes per cycle | `db_connect`; one deliberate narrow bypass in the X76.5A self-kill logging path only |
| 10 | dust_observatory_enricher | ops DB | every 120s | one `db_connect()`/call | `db_connect` |
| 11 | alert_evaluator | `wt_alerts.db` only (third DB, out of scope) | every 30s | per-call | raw `sqlite3.connect` bypass, but zero contention risk for the two audited DBs |
| 12 | webhook_worker | live DB | disabled (`autostart=false`) | one conn per loop iteration, spans multiple RPC-driven item processing calls | `db_connect` — **inert**, but flagged: if re-enabled, same conn-held-across-RPC shape as ws_cascade |

### Deep dive: `ws_cascade` (named in the report)

Tables written (ops DB): `wt_active_subprov_sessions`,
`wt_candidate_websocket_watches`, `wt_watchtower_launches`,
`wt_discovered_subprovs`, `wt_capital_reloads`,
`wt_capital_distributor_candidates`, `wt_cdc_outbound_events`,
`wt_subprov_topups`, `wt_subprov_evidence`, `wt_subprov_sig_retry`,
`wt_subprov_sig_cursor`, `wt_subprov_sig_dedupe_stats/summary`,
`wt_treasury_ws_usage`, `wt_subprov_account_ws_usage`,
`wt_pending_session_writes`, `wt_temp_provision_candidates`,
`wt_swarm_buys`, `wt_fanout_events`, `wt_token_lifecycle`,
`wt_webhook_hits`, `watchtower_events`.

**Confirmed connection-held-across-RPC pattern** in three hot-path
handlers (`_handle_treasury_tx`, `_handle_subprov_tx`, `_handle_cdc_tx`):
each opens `conn = self._ops()` (a `TrackedConnection`), then makes a
blocking RPC call (`_get_tx`/`_get_subprov_tx_fast_retry`, the latter's
own code comment citing 3200-3488ms typical, 8423ms max observed) with
`conn` still open, before any write. **Important refinement, verified
directly**: `TrackedConnection._acquire_write_lane()` only fires lazily
on the FIRST write statement — opening the connection and running
read-only lookups (`session_for_subprov`, `_confirmed_treasuries`) does
NOT acquire the cross-process write lease. So the RPC wait itself does
NOT hold the write lease in these three handlers; the lease is acquired
only once an actual write executes, which happens AFTER the RPC returns
and after further in-process decode/classification work
(`extract_close_destinations`, `_classify_recipient` on cache miss).
The connection handle stays open the whole time (relevant to WAL
pinning, see Phase 7 — measured clean, not currently an issue), but the
write-lease hold itself is shorter than the full RPC-to-close span.

## Phase 2 — Hot table analysis (live measurement)

| Table | Writes/1h | Writes/24h |
|---|---|---|
| `wt_walkback_queue` | 15 | 326 |
| `wt_treasury_review` | 1 | 38 |
| `wt_webhook_hits` | 0 (quiet window) | 163 (~7/hr average) |
| `wt_worker_heartbeat` | 2 | 2 (single-row upsert per worker, `ON CONFLICT DO UPDATE`) |
| `wt_watchtower_launches` | 0 | 0 (quiet window) |
| `operator_entities` | 0 | 0 (quiet window) |
| `wt_confirmed_treasuries` | 0 | 0 (quiet window) |

**Conclusion: none of these tables are "hot" in absolute terms.**
`wt_webhook_hits` (ws_cascade's own contended table) averages roughly 7
writes/hour. This rules out raw write volume as the cause — the
contention is not "too many writes," it's specific writes taking too
long relative to how often they collide.

## Phase 3 — SQLITE_BUSY analysis

Two distinct exception types observed in `walkback_worker`'s logs, and
they must be classified separately:

- **`NestedDatabaseWriteError` (449 occurrences)** — walkback_worker's
  OWN in-process thread-local write lease already held when a second
  write is attempted. This is the X76.5/X76.5A stuck-lease class,
  already addressed by that milestone's self-kill guard. **Not a
  cross-process contention signal** — this is a single-process
  self-nesting bug class, now self-healing.
- **`OperationalError: database is locked` / `SQLITE_BUSY` (483
  occurrences in walkback_worker, 19,203 in ws_cascade's own logs over
  its much longer uptime)** — genuine cross-process contention.
  Measured directly: `held_seconds` at time of failure consistently in
  a **narrow 31.1-32.6 second band** (mean 32.26s across 483 samples).
  This is not random — it is SQLite's own `busy_timeout=30000` (30s)
  expiring plus overhead, meaning: **whenever this fires, another
  process (per the `lease_held_by_this_thread_at_failure` /
  `current_writer` diagnostics, this is `walkback_worker` itself
  mid-multi-hop-RPC-walk, or occasionally `ws_cascade`'s own single-
  writer-thread queue backed up behind a slow prior item) has been
  holding the write lease for 30+ seconds continuously.**

Cross-referencing `ws_cascade.log`'s own failures: every sampled
`ws-cascade-hit` failure shows `current_writer.command: "ws-cascade-hit"`
with `process_pid` equal to `ws_cascade`'s own PID — i.e. **ws_cascade
is frequently blocking on its OWN prior write**, not on walkback_worker,
because `_event_writer_loop` is a single dedicated thread draining one
item at a time; if one item's `acquire_write_lease()` call is itself
waiting on walkback_worker's (or another process's) held lease, every
subsequent queued item inherits that wait serially.

**Classification: expected-but-avoidable contention, not pathological.**
No deadlocks, no data corruption, no permanent stalls (X76.5's self-kill
guard handles the one pathological sub-case — a lease that never
releases at all). The busy events are SQLite doing exactly what
`busy_timeout` is designed to do under real, if infrequent, overlap
between a fast, frequent writer (`ws_cascade`) and a slow, infrequent
writer (`walkback_worker`'s multi-hop RPC walks).

## Phase 4 — Lock timeline (reconstructed from measured data)

Representative sequence, reconstructed from `walkback_worker.log` and
`ws_cascade.log` timestamps around a single incident:

```
walkback_worker                              ws_cascade
────────────────                              ──────────
_process_row() begins FULL_WALKBACK walk
  hop1 RPC round-trip(s)                       (idle / other activity)
  _store_funder() → WRITE, lease ACQUIRED
  hop1 evidence RPC (_get_tx)          ←── lease held across this RPC
  _capture_provisioning_wallet() → WRITE (still same lease)
  hop2 RPC round-trip(s)               ←── lease held across this RPC
  ...                                          ws-cascade-hit item queued
                                               → acquire_write_lease() BLOCKS
                                                 (waits up to 30s busy_timeout)
  _mark_complete() → WRITE
  ops.commit() → lease RELEASED
                                               ws-cascade-hit acquires, writes, releases
                                               (if >30s elapsed: SQLITE_BUSY raised,
                                                item logged as failed, dropped —
                                                see Phase 6)
```

- **Average wait** (for the 483 measured `OperationalError` failures):
  effectively the full 30s `busy_timeout` window each time (since these
  are the ones that TIMED OUT, not the ones that succeeded after a short
  wait — successful short waits are not logged as failures and were not
  separately instrumented before this audit; see Phase 8 for the new
  instrumentation this finding justifies).
- **Maximum observed held-lease span**: 637s (one of the X76.5A stuck-
  lease incidents — a pathological outlier, not representative of normal
  contention, already handled by the self-kill guard).
- **Longest normal (non-pathological) held transaction**: not separately
  isolated from stuck-lease outliers in current logs — this is itself
  Phase 8's justification (see below): today, `walkback_worker`'s trace
  file can't distinguish "held 32s because of a genuinely slow multi-hop
  RPC walk" from "held 600s+ because of the stuck-lease bug." A future
  observability improvement (out of scope for this audit's Phase 1-9
  measurement work) would tag lease durations with a
  cause classification.
- **Contention frequency**: ~483 busy failures over the walkback_worker
  log's observed span (~9.8 hours, spanning multiple restarts and two
  deliberately-observed X76.5A stuck-lease incidents) — not a clean
  steady-state rate given the log includes those incidents, but order-
  of-magnitude "tens of busy events per hour" during active periods.

## Phase 5 — Transaction audit: RPC/network work inside write transactions

**Confirmed, directly, in `walkback_worker.py::_process_row()`
(FULL_WALKBACK branch, lines ~1080-1246):**

1. `_store_funder(ops, ...)` (line 1108) — a plain `ops.execute(UPDATE...)`
   with **no accompanying commit**.
2. Immediately followed by `_get_tx(sig1)` (line 1119) — a **blocking RPC
   call** — while the write from step 1 has already implicitly acquired
   the write lease (lazily, on that `UPDATE`).
3. More writes (`_store_close_destination_evidence`,
   `_capture_provisioning_wallet`) on the same connection, same
   transaction, same held lease.
4. `_find_with_evidence` for hop2 (line 1168) — **more RPC calls**
   (signature pagination + transaction fetches, bounded by
   `SIG_PAGE_COUNT=3` pages and `TX_FETCH_LIMIT=5` tx fetches per hop).
5. More writes (`_capture_provisioning_facts`).
6. Only at `_mark_complete()` and the final `ops.commit()` at the end of
   `_process_row` (line ~740, confirmed in the X76.5A investigation) does
   the transaction actually close and the write lease release.

**This is the single clearest, most actionable finding of this audit**:
a `FULL_WALKBACK` row can hold walkback_worker's write lease across two
full hop-resolution RPC cycles — each potentially several seconds
(`RPC_TIMEOUT=8s` cap per call, multiple calls per hop) — meaning a
single row can legitimately hold the cross-process write lease for
10-40+ seconds, matching the measured 31-32.5s `held_seconds` band in
Phase 3 almost exactly.

**Does this include anything not requiring DB ownership?** Yes — every
RPC call (steps 2 and 4) and all of the in-between parsing/decoding.
None of this requires the write lease to be held; it is held only
because the code writes partial evidence (`_store_funder`, evidence
capture) BEFORE the hop2 RPC round-trip completes, rather than
collecting all evidence first and writing once at the end.

**Per this milestone's explicit instruction: do not change behaviour
yet.** This finding is reported as an opportunity, not implemented,
pending the Phase 10 recommendation decision below.

**`ws_cascade`'s three RPC-during-open-connection handlers** (Phase 1)
do NOT show the same defect at the write-lease level (the lease isn't
acquired until after the RPC, per the Phase 1 refinement) — they hold
an open SQLite handle across the RPC, which is a much weaker form of
resource retention (relevant to connection-count/WAL-reader accounting,
not to blocking other writers' lease acquisition).

## Phase 6 — Retry behaviour

- **SQLite-level**: `busy_timeout=30000` (30s) is set on every
  `db_connect()`-opened connection (`db_locking.py:528`) — this is the
  only retry/wait mechanism operating at the SQL statement level.
- **Application-level, walkback_worker**: `_is_lock_error()` exists but
  is only used to decide whether a STARTUP maintenance failure should be
  swallowed vs. re-raised (`recover_stalled_running_jobs`,
  `finalize_exhausted_pending`) — it does **not** wrap the main
  per-row write path in any explicit retry loop. A busy/lock failure
  during `_process_row()` propagates to the outer `except Exception`
  in `run_loop()`, the cycle is abandoned, and the row (still marked
  `status='running'` with an active lease) is picked up again by the
  NEXT cycle's `recover_stalled_running_jobs`/`drain_batch` claim logic
  45s later.
- **Application-level, ws_cascade**: the failing `ws-cascade-hit`
  writes are logged (`"[WS_CASCADE] ops-db write failed hit: ..."`) and
  **dropped** — `_event_writer_loop`'s `except Exception as e: print(...)`
  has no retry or re-queue; a `wt_webhook_hits`/`watchtower_events` row
  that fails to write during contention is permanently lost (not
  requeued, not retried). Given `wt_webhook_hits` is a webhook-hit audit
  trail (used for treasury/subprov WS activity confirmation, not primary
  attribution), this data loss is low-severity but real, and distinct
  from a "healthy" retry pattern — it is closer to **insufficient**:
  there is no retry at all for this specific writer.
- **`db_retry()`** (`db_locking.py:808-836`, exponential backoff wrapper)
  exists in the codebase but is **not used** by either `walkback_worker`
  or `ws_cascade`'s contended paths.

**Classification: retries are insufficient for `ws_cascade`'s event/hit
writer** (silent data loss on contention, no backoff, no requeue) and
**absent-by-design for `walkback_worker`** (relies on the next natural
loop cycle + existing crash-safe row-claiming, which is a reasonable,
if coarse-grained, substitute for a tight retry loop given the 45s
cadence).

## Phase 7 — WAL behaviour

Measured directly, live: `wt_ops_v2.db-wal` = 4.1 MB (healthy, well
under any concerning threshold); `PRAGMA wal_checkpoint(PASSIVE)`
returned `(busy=0, log_pages=146, checkpointed_pages=146)` in 14ms — a
full, non-blocked checkpoint with zero busy readers at the moment of
measurement.

**Conclusion: WAL/checkpoint behaviour is healthy and is NOT a
contributing factor** to the observed `SQLITE_BUSY` contention. The
existing WAL watchdog (`db_locking.py`'s `_wal_watchdog_loop`, 32MB
threshold, TRUNCATE checkpoint) and connection reaper are both already
in place and functioning; no changes indicated here.

## Phase 8 — Mission Control

**Not implemented in this milestone.** Justification: the audit's own
Phase 3/4 findings show the existing per-process telemetry
(`TrackedConnection`'s `serializer_metrics()`, `DatabaseWriteService`'s
`self._telemetry`) is real and detailed, but **fragmented across
processes with no cross-process aggregation** — `walkback_worker` and
`ws_cascade` each accumulate their own in-memory counters with no shared
sink (unlike the live-DB listener, which already snapshots
`serializer_metrics()` to `logs/db_serializer_metrics.json` every 15s
for exactly this reason). Building a correct Mission Control contention
panel requires this instrumentation gap to be closed first (each
long-running ops-DB writer snapshotting its own telemetry to a shared
location), which is itself a real, evidence-justified follow-on — but
implementing it now, without first fixing Phase 5's actual root cause
(RPC calls inside a held write lease), would produce a dashboard that
faithfully displays a still-avoidable problem rather than a genuinely
irreducible one. Per the milestone's own instruction ("implement only
if justified by evidence... otherwise produce a complete audit"), this
phase is intentionally deferred to a follow-on milestone once Phase 10's
recommendation is acted on, rather than built against a target the
evidence says is about to move.

## Phase 9 — Sustained load validation

Given Phase 8 was deferred, full "Mission Control reports contention
live" validation was not applicable. Instead, sustained live observation
was already performed as a side effect of this session's own X76.5/
X76.5A work: `walkback_worker`, `ws_cascade`, and `creator_funding_worker`
were all simultaneously active for multiple hours during this session,
including through several real contention episodes (documented in
X76.5A's own audit) and the busy-event data analyzed in Phases 2-4
above. Observed under that sustained real load:

- Busy events: hundreds over multi-hour windows (Phase 3).
- Queue throughput: `wt_walkback_queue` continued draining at its
  normal ~15/hour rate even during contention windows (Phase 2) — no
  observed permanent stall outside the already-addressed stuck-lease
  case.
- Candidate generation: continued advancing throughout (confirmed via
  X76.5A's own `wt_treasury_review.detected_at` monitoring).
- Worker stability: `walkback_worker`'s self-kill guard fired and
  recovered cleanly multiple times (X76.5A); `ws_cascade` never crashed
  or required intervention despite thousands of logged busy events —
  it degrades to dropped audit-trail rows (Phase 6), not process
  failure.
- No regression to attribution, reconciliation, resolver, or governance
  behaviour was observed or is implicated by any finding in this audit.

## Phase 10 — Recommendation

**C — Transaction boundary improvement.**

Evidence supports neither "no changes" (A) — Phase 5 identified a
concrete, RPC-inside-write-transaction pattern that measurably explains
the 30-32s busy-timeout band — nor an architecture change (E) — the
existing two-mechanism (TrackedConnection + DatabaseWriteService)
design is sound, both converge correctly on the same cross-process file
lock, and no data-race or correctness risk exists anywhere in this
audit. This is not a "not enough capacity" or "wrong design" problem; it
is a "one function holds a lock longer than its own work requires"
problem, which is exactly what recommendation C is for.

**Specific, evidence-backed opportunity (not implemented in this
commit, per the milestone's explicit "implement only if justified by
evidence" / Phase 5's explicit "do not change behaviour yet"
instruction — recommendation only):**

`walkback_worker.py::_process_row()`'s FULL_WALKBACK branch should
collect all hop1/hop2 RPC evidence FIRST (into plain in-memory values,
no DB writes), then perform exactly one write transaction at the end
using that collected evidence — mirroring the canonical shape this
codebase has already used successfully elsewhere (X76.3's fix for the
creator-funding extractor: "concurrent RPC/read collection → pure
result payloads → single supervised persistence stage"). This would
shrink the write-lease hold time from "up to two full RPC hop cycles"
to "one batch of INSERT/UPDATE statements," which is the direct,
measured cause of the 30-32s busy-timeout collisions in Phase 3/4.

A secondary, smaller opportunity: `ws_cascade`'s `_event_writer_loop`
(Phase 6) currently drops `wt_webhook_hits`/`watchtower_events` rows
silently on a busy-timeout failure with no retry or requeue — adding a
requeue-on-failure (or using the existing `db_retry()` helper already
in `db_locking.py`) would close a real, if low-severity, audit-trail
data-loss gap, independent of the transaction-boundary fix above.

Both are deferred to a follow-on implementation milestone, to keep this
audit's own scope honest to its stated objective ("determine... implement
only if justified") rather than bundling a correctness-neutral, evidence-
backed refactor into what was scoped as a measurement pass.

## Deliverables summary

1. Writer inventory — Phase 1 (12 processes, 2 serialization mechanisms).
2. Hot-table report — Phase 2 (none of the audited tables are high-volume
   in absolute terms; contention is duration-driven, not volume-driven).
3. SQLITE_BUSY analysis — Phase 3 (483 `OperationalError` + 449
   `NestedDatabaseWriteError` in walkback_worker's logs, 19,203 in
   ws_cascade's; classified as expected-but-avoidable, not pathological).
4. Lock timeline — Phase 4 (reconstructed sequence; 31-32.5s measured
   held-lease band matches SQLite's own 30s busy_timeout almost exactly).
5. Transaction audit — Phase 5 (root cause identified: RPC calls
   interleaved with un-committed writes in walkback_worker's
   FULL_WALKBACK hop resolution).
6. WAL audit — Phase 7 (healthy; not a contributing factor).
7. Mission Control metrics — Phase 8 (deferred; instrumentation gap
   identified and explained, not built against a moving target).
8. Recommendation — Phase 10: **C, transaction boundary improvement**,
   specific fix identified, not implemented in this commit.
9. Commit hash — see below.

## Acceptance criteria

- ✓ All 12 writers identified (Phase 1).
- ✓ Hot tables identified and measured (Phase 2) — none found to be
  volume-hot; contention is duration-driven.
- ✓ SQLITE_BUSY classified (Phase 3) — expected-but-avoidable, not
  pathological; the one pathological sub-case (stuck lease) already
  has a self-healing fix from X76.5.
- ✓ Lock durations measured (Phase 4) — 31-32.5s band, root cause traced
  to Phase 5's finding.
- ✓ Retry behaviour understood (Phase 6) — SQLite busy_timeout is the
  only mechanism in walkback_worker's path (adequate given the 45s loop
  cadence); ws_cascade's event writer has NO retry (a real, separate,
  low-severity gap).
- ✓ Mission Control reporting deferred with explicit justification
  (Phase 8) rather than built prematurely.
- ✓ No integrity guarantees weakened — nothing in this audit touched
  `TrackedConnection`, `DatabaseWriteService`, or either process's code.
- ✓ No attribution, reconciliation, resolver, or governance changes —
  this commit is documentation only.
