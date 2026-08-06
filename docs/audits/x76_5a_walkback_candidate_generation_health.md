# X76.5A — Walkback Candidate Generation Health Monitoring

## Objective

X76.5 restored live Treasury Review candidate generation and added a
stuck-write-lease self-kill guard to `walkback_worker`. Self-healing by
itself is not enough: Mission Control still couldn't distinguish healthy
progress from an idle-but-fine worker, a stuck lease, a repeated
self-kill loop, or candidate generation that has silently stopped. This
milestone adds explicit health monitoring for the complete Walkback →
Treasury Candidate path, and — because of how it was validated — ends up
fixing a real bug in X76.5's own self-kill logging along the way.

## Design

### Primary status: one clear state, six possibilities

`src/ops/walkback_candidate_health.py::_determine_status()` evaluates a
fixed precedence order so exactly one of `HEALTHY / IDLE / DEGRADED /
STALLED / RECOVERING / STOPPED` is ever returned:

1. **STOPPED** — Supervisor reports the process isn't running. Checked
   first: nothing else is meaningful if the worker is dead.
2. **RECOVERING** — a self-kill fired within the last
   `WALKBACK_RECOVERING_WINDOW_SECONDS` (default 300s) and hasn't yet
   been reconciled to healthy. Gives a fresh restart a grace window
   before judging it on steady-state criteria.
3. **STALLED** — a write lease is held past the safe threshold (120s
   default) or the self-kill threshold (600s, same env var
   `WALKBACK_MAX_LEASE_STUCK_SECONDS` the worker's own guard uses), OR
   the heartbeat has gone stale (>180s) with no lease explanation.
4. **DEGRADED** — the worker IS progressing (current heartbeat, no
   stale lease) but showing real friction: nested-write-failure
   evidence, stalled running rows, pending work with zero completions
   in the last minute, or — the spec's "candidate-generation silence"
   scenario — walkback completed work in the last hour but nothing
   reached `wt_treasury_review`.
5. **IDLE vs HEALTHY** — both require a current heartbeat and no
   errors; the only distinction is whether there's pending/running work
   or recent completions. Per the spec's explicit instruction, a zero
   candidate count while walkback itself is idle is IDLE, never
   DEGRADED — "do not classify zero candidates as unhealthy when ...
   no eligible unknown treasury was found."

### Cross-process lease monitoring

`_lease_snapshot()` (from X76.5's `walkback_cycle_trace.py`) only sees
the CURRENT thread's lease — useless from a different process. Mission
Control (the Flask app) is a different process than `walkback_worker`,
so this milestone reads `database/wt_ops_v2.db.write.lock.owner`
directly — the on-disk lease record `database_write_service.py` already
writes on every lease acquisition, containing `command`,
`transaction_id`, `process_pid`, `acquired_at`. Absence of the file
means no write is currently in flight (healthy, not an error).

### Recovery-event log

New table `wt_walkback_recovery_events`
(`src/ops/walkback_recovery_log.py`), deliberately separate from
`wt_treasury_review_actions` (X76.2, analyst governance decisions) and
`operator_identity_events` (X76.1, identity events) — this records
WORKER lifecycle events, a different kind of thing entirely. Two event
kinds, matching the milestone's explicit incident-labelling requirement:

- `stale_lease_self_kill` — the worker's own guard fired
  (`record_self_kill`, called from `_check_stuck_lease` before
  `os._exit(1)`).
- `manual_external_termination` — recorded retroactively/explicitly for
  a termination NOT caused by the guard (a signal sent by tooling, an
  operator action, an OOM kill). A killed process can never log its own
  death, so this kind is always entered after the fact with real
  evidence, never guessed.

`mark_restarted`/`mark_healthy` close the loop: `run_loop()`'s startup
now checks whether the most recent recovery event for this worker has
no `restarted_at` yet — if so, THIS boot IS that restart, and the row is
reconciled with the boot time and an immediate healthy timestamp (the
worker being alive and reaching this line already proves the restart
succeeded).

### The X76.5 SIGABRT incident, correctly labelled

Backfilled via `scripts/maintenance/x76_5a_record_sigabrt_incident.py`
using exact timestamps from `supervisord.log` (not estimated): the
accidental `os.kill(pid, SIGABRT)` sent during X76.5's own investigation
occurred at `2026-08-06 00:05:36,404 UTC`, respawned as pid 59618 one
second later, entered RUNNING state at `00:05:42,648` (6 seconds to
healthy). Recorded with `event_kind='manual_external_termination'`,
explicitly NOT `stale_lease_self_kill` — the guard did not even exist in
the code running at that moment (it landed in the X76.5 commit at
00:16:01, ten minutes after this incident).

## Live proof-of-concept (found during this milestone's own validation)

While testing `build_walkback_candidate_health()` against live data, the
health check reported `STALLED` — and it was correct: `walkback_worker`
had genuinely stuck again (the same underlying, not-fully-isolated leak
class X76.5 targeted), with `held_seconds` climbing every cycle in the
worker's own trace log. Per the user's explicit instruction to let the
self-kill guard prove itself rather than intervene manually, the stall
was watched rather than restarted.

**The guard fired 4 times**, confirmed independently via two sources —
`walkback_worker.py`'s own `CRITICAL_STUCK_LEASE` log lines and
`supervisord.log`'s own exited/spawned/entered-RUNNING sequence:

| # | Held (s) | Exited (UTC) | Spawned | Entered RUNNING | Recovery time |
|---|---|---|---|---|---|
| 1 | 602 | 00:33:01.814 | 00:33:01.825 | 00:33:06.852 | ~5.0s |
| 2 | 602 | 00:45:16.767 | 00:45:16.831 | 00:45:22.453 | ~5.7s |
| 3 | 602 | 00:55:22.308 | 00:55:22.732 | 00:55:27.733 | ~5.4s |
| 4 | 637 | 01:07:28.414 | 01:07:28.427 | 01:07:33.463 | ~5.0s |

Every firing: guard detected the stuck lease, worker exited, Supervisor
respawned within ~10-500ms, and the fresh process reached RUNNING within
~5-6 seconds — every single time. Candidate generation resumed after
each restart (confirmed via `wt_treasury_review.detected_at` timestamps
continuing to advance after each recovery), and `wt_walkback_queue`
showed no anomalous duplicate rows afterward (crash-safe claiming via
`status='running'` + lease renewal was already part of the pre-existing
`drain_batch`/`_mark_running` design, untouched by this milestone).

### The bug this exposed and fixed

**All 4 real firings failed to log the event** — `record_self_kill()`
raised `NestedDatabaseWriteError` every time
(`logs/supervisor/walkback_worker.log`: "failed to persist self-kill
event (non-fatal): NestedDatabaseWriteError..."). Root cause: the
original code opened the logging connection via `db_connect()`, which
returns a `TrackedConnection` — but `_check_stuck_lease()` runs, by
definition, on a thread whose `_thread_write_lease` is ALREADY poisoned
by the very stuck lease it is reporting on. The tracked write immediately
self-nested against itself, the exact bug class this whole milestone
chain (X76.3/X76.5) exists to fix, now found in the fix's own
diagnostics code.

**Fixed** in the same commit: `walkback_worker.py` now uses the raw,
unpatched `sqlite3.connect` (`db_locking.py`'s own
`_sqlite3_connect_orig`) for this one write, bypassing
`TrackedConnection`'s lane entirely. Safe specifically here because the
process calls `os._exit(1)` immediately afterward — there is no future
cycle in this process for a dangling lease to poison.

The 4 missed events were backfilled from log data
(`scripts/maintenance/x76_5a_backfill_missed_self_kills.py`) with their
real `detected_at`/`restarted_at`/`healthy_at` timestamps and exact
`transaction_id`s preserved, idempotent on `lease_transaction_id`. (Note:
live backfill execution was repeatedly blocked by genuine, ongoing ops-DB
write contention during this session — `ws_cascade` and the recovering
`walkback_worker` both actively writing — so the backfill may still be
pending against the live database at commit time; the script itself is
idempotent and safe to re-run, and the 4 events' exact data is preserved
in this document and the script's own source regardless of live
persistence timing.)

## Display, warnings, and Mission Control integration

New route `GET /api/ops-v2/walkback-candidate-health`
(`operation_dashboard_routes.py`) returns the full composed payload.
`/system-health`'s existing Intelligence group
(`renderIntelligenceGroup` in `templates/system_health_dashboard.html`)
now fetches it and folds the status into that group's own
warning/critical aggregation — per the milestone's own instruction to
extend the existing Intelligence group rather than add a new top-level
one, and per its explicit acceptance criterion ("Mission Control cannot
show Healthy while candidate generation is stalled"): `STOPPED`/`STALLED`
escalate to critical, `DEGRADED`/`RECOVERING` to warning. The two
specific spec-required warning strings ("Walkback write lease is stale.
Candidate generation may be blocked." / "Worker recovered automatically
after a stale write lease.") are surfaced verbatim from the API's own
`warnings[]` array — never a generic database message.

A new subpanel shows: worker status pill, heartbeat age, candidates
generated (1h/24h), newest candidate age, pending/running walkback jobs,
average completion latency, self-kill counts (1h/24h); a collapsed
"Lease detail" expander (owner command, transaction ID, acquired-at,
age, both thresholds — never raw stack traces, per spec); and a
Recovery History table of the 5 most recent events, each labelled either
"Stale lease self-kill" or "Manual / external process termination" —
never conflated.

Discovery's own Candidate Generation column (X76.4) now links to Mission
Control's Intelligence group instead of only Treasury Review, closing
the Discovery ↔ Mission Control ↔ Treasury Review link triangle the
spec asks for; Mission Control's new subpanel links back to both
Treasury Review and Discovery.

## Named validation

All 6 scenarios from the milestone spec, verified directly against
`_determine_status()` in
`tests/test_x76_5a_walkback_candidate_health.py` (16 tests):

1. **Healthy progress** — current heartbeat, recent completions, no
   lease, candidate generated → `HEALTHY`.
2. **Healthy idle** — no pending jobs, no eligible candidates, current
   heartbeat → `IDLE`, not flagged as failure.
3. **Stale lease** — lease age past the safe threshold (even below the
   self-kill threshold) → `STALLED`, warning visible; lease under the
   safe threshold → `HEALTHY`.
4. **Self-kill recovery** — recent unreconciled self-kill →
   `RECOVERING`; full lifecycle (`record_self_kill` →
   `mark_restarted` → `mark_healthy`) verified end-to-end through the
   real persistence layer, event remains queryable in history after
   reconciliation.
5. **Worker stopped** — Supervisor reports not running → `STOPPED`,
   takes precedence over every other signal (even a stale lease or a
   recent self-kill flag).
6. **Candidate-generation silence** — walkback actively completing work
   but zero candidates generated → `DEGRADED` with an explicit
   "no candidate generated" reason; contrasted against the case where
   walkback itself isn't progressing either (correctly attributed to
   walkback's own stall, not double-counted as a separate issue).

Plus incident-labelling tests: manual termination is never recorded as
`stale_lease_self_kill`; the two kinds are independently countable;
`recent_events` returns newest-first, limited to 5.

## Regression

Confirmed empty diff on `disposition_resolver.py`,
`operation_attribution.py`, `evidence_reconciliation.py`,
`attribution_outcome.py`, `discovery/service.py`,
`discovery/operation_convergence.py`, `treasury_review_workspace.py`,
`operator_identity_governance.py` (only its pre-existing, unrelated
uncommitted `_transition()` block remains, as in every prior X76.x
milestone this session), `watchtower_alignment.py`. No changes to
attribution, reconciliation, resolver, promotion, operator identity, or
candidate-selection semantics.

Targeted regression, run individually (full-suite pollution documented
in every X76.x audit this session):
`test_x76_5a_walkback_candidate_health.py` (16/16, new),
`test_walkback_worker_startup_resilience.py` (10/10),
`test_database_write_service.py` (9/9),
`test_x76_2_treasury_review_audit_integrity.py` (19/19),
`test_x75_3a_structural_graph_integrity.py` (18/18),
`test_x26_2_1_attribution_gate_fix.py` (10/10),
`test_x75_3a_projection_consistency.py` (2/2),
`test_ops_x21b_walkback_integration.py` (5/5). 89/89 relevant tests pass.
