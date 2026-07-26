# X64.9B1 — Durable Signature Redelivery Instrumentation — Execution Report

Implements durable measurement of the `wt_subprov_sig_retry` DONE-row
dedupe check discovered during X64.9B (which aborted a proposed purge
of DONE rows upon finding this exact check was unaudited). This task
instruments — but does not purge, does not change dedupe semantics,
and does not authorize any future retention decision on its own.

## Files changed

| File | Change |
|---|---|
| `src/core/ws_cascade_store.py` | Added two new idempotent table creations (`wt_subprov_sig_dedupe_stats`, `wt_subprov_sig_dedupe_summary`) + one index inside the existing `ensure_cascade_schema()`. Added three new module-level functions: `dedupe_age_bucket()`, `record_subprov_sig_duplicate()`, `record_subprov_sig_checked()`. No existing function or table modified. |
| `src/core/ws_cascade.py` | Widened the dedupe check's `SELECT` from `status` to `status, last_attempt_at` (still read-only, same WHERE clause). Added two new methods: `_record_subprov_sig_dedupe()` and `_record_subprov_sig_checked_only()`. Wired one call into the existing DONE-branch (before its unchanged `return []`) and one call after the non-duplicate path's connection close. No existing return value, control flow, or downstream call changed. |
| `tests/test_x64_9b1_dedupe_instrumentation.py` | New file, 37 tests (see below). |

## Schema added

```sql
CREATE TABLE IF NOT EXISTS wt_subprov_sig_dedupe_stats (
    subprov_wallet TEXT NOT NULL, age_bucket TEXT NOT NULL,
    duplicate_count INTEGER NOT NULL DEFAULT 0, max_duplicate_age_s INTEGER,
    first_observed_at INTEGER, last_observed_at INTEGER,
    source_ws INTEGER NOT NULL DEFAULT 0, source_catchup INTEGER NOT NULL DEFAULT 0,
    source_retry INTEGER NOT NULL DEFAULT 0, source_hot_burst INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subprov_wallet, age_bucket)
);
CREATE INDEX IF NOT EXISTS ix_subprov_sig_dedupe_stats_bucket ON wt_subprov_sig_dedupe_stats(age_bucket);

CREATE TABLE IF NOT EXISTS wt_subprov_sig_dedupe_summary (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total_checked INTEGER NOT NULL DEFAULT 0, total_duplicates INTEGER NOT NULL DEFAULT 0,
    max_duplicate_age_s INTEGER, first_duplicate_at INTEGER, last_duplicate_at INTEGER,
    updated_at INTEGER NOT NULL
);
```

Full design rationale: [x64_9b1_observability_design.md](x64_9b1_observability_design.md).
Constraint-by-constraint compliance: [x64_9b1_schema_safety.md](x64_9b1_schema_safety.md).
Path audit that motivated this design: [x64_9b1_path_audit.md](x64_9b1_path_audit.md).

## Tests added

37 tests in `tests/test_x64_9b1_dedupe_instrumentation.py`, covering
every scenario the task specified:

- A previously completed signature is still skipped identically to
  before instrumentation.
- Duplicate count is recorded, accumulates correctly per wallet/bucket,
  and separate buckets create separate rows.
- Duplicate age is calculated correctly (20 boundary-value cases across
  all 10 required buckets, plus a negative/defensive case).
- No duplicate RPC or downstream processing occurs — proven via an AST
  structural check that the DONE-branch contains only the metric call,
  the new recording call, and the unchanged `return []`.
- Observability write failure does not break deduplication — proven
  via an AST structural check that both new recording methods contain
  a non-reraising try/except.
- In-memory cache eviction does not bypass the durable check — proven
  via an AST check that `_process_subprov_sig_durable` never calls
  `_subprov_sig_seen()` as a gate (confirmed during the audit: that
  method exists in the file but is never called anywhere, so this
  property holds trivially and is now regression-tested).
- Process restart simulation still allows DB-backed duplicate
  detection — proven by simulating a fresh, empty in-memory set and
  confirming the DB-backed DONE check works independent of it.
- Schema initialization is idempotent — calling `ensure_cascade_schema`
  twice does not reset or duplicate data.
- No nested database write is introduced — proven via an AST check
  that both new recording methods open their own connection via
  `self._ops()` rather than accepting an external `conn` parameter.

All 37 pass; the full `ws_cascade`-related suite (including the
pre-existing `test_ws_cascade_connection_leak.py`) passes together (40
total), confirming no interference with prior regression coverage.

## Deployment result

- `ws_cascade` (the supervisord-managed process containing this code)
  was restarted at 2026-07-21T16:02:22Z (approx) to load the change —
  **only this process**; `watchtower_listener` and `walkback_worker`
  were confirmed untouched (uptimes unchanged across the restart).
- Startup succeeded cleanly: new PID (14162, from prior 51958), normal
  startup log sequence (`starting`, wallet-profile load, CDC
  rehydration, resync complete), zero tracebacks in the post-restart
  window.
- Both new tables (`wt_subprov_sig_dedupe_stats`,
  `wt_subprov_sig_dedupe_summary`) confirmed created on first startup.
- Observed for ~4 minutes post-restart: `total_checked` grew
  179 → 212, `updated_at` advancing, process remained `RUNNING`
  throughout, zero new tracebacks, zero `NestedDatabaseWriteError`,
  zero `X64.9B1`-tagged errors.
- `sig_stage_timing` log lines confirmed `dedupe=0.4ms` — no
  measurable latency regression from the widened SELECT.
- `wt_subprov_sig_retry` continued growing naturally during the
  observation window (2,313,026 → 2,313,094 DONE rows), confirming
  normal signature processing was unaffected.

## Baseline counters (at end of this task's observation window)

| Metric | Value |
|---|---|
| `total_checked` | 212 (and growing) |
| `total_duplicates` | 0 |
| `max_duplicate_age_s` | NULL (no duplicates observed yet) |

## Initial duplicate findings

**Zero duplicates observed in this task's short (~4-minute)
post-deployment window.** This is consistent with, but does not yet
independently confirm or refute, the pre-existing code comment's
offline sample (0/48). No conclusion should be drawn from this window
alone — see the Measurement Contract (Phase 7) for why a much larger
sample (500,000+ signatures checked, spanning 14+ days and multiple
restart/replay cycles) is required before any retention-cutoff
decision.

## Runtime impact

- **Latency**: negligible — the widened SELECT adds one additional
  column to an already-indexed, already-executing point lookup; the
  new recording calls are fully outside the critical path (fired after
  the dedupe-critical connection is closed, or immediately before the
  unchanged `return []` on the duplicate path).
- **Connection overhead**: each duplicate observation or
  non-duplicate-path check now opens one additional short-lived
  connection via `self._ops()`. Given duplicates are (so far, and per
  historical sampling) very rare, this overhead is dominated by the
  non-duplicate path's `_record_subprov_sig_checked_only()` call — one
  extra connection open/close per signature processed. This was a
  deliberate design tradeoff (see Phase 3/schema-safety doc) to
  guarantee zero nested-write-lane risk; if connection overhead proves
  material at higher sustained signature volume than observed in this
  task's short window, a future optimization could batch multiple
  `total_checked` increments into a single periodic write instead of
  one-per-signature — **not done here**, since correctness and
  safety were prioritized over micro-optimization for this
  measurement-focused task, and no negative impact was observed in
  this deployment's observation window.
- **Storage growth**: bounded by design (see observability doc) —
  `wt_subprov_sig_dedupe_stats` grows only with distinct
  (wallet, bucket) pairs that actually produce a duplicate;
  `wt_subprov_sig_dedupe_summary` is permanently exactly 1 row.

## Known limitations

- The observation window completed as part of this task (~4 minutes)
  is far short of the Measurement Contract's required minimum (14
  days, 500,000+ signatures) — this task delivers the instrumentation
  and confirms it is live and correctly functioning, not the completed
  measurement itself.
- `_subprov_sig_seen()` (the bounded in-memory cache) was found, during
  Phase 1's audit, to be dead code — defined but never called anywhere
  in the file. This task did not remove it (out of scope — this task's
  constraints explicitly prohibit "replace the DB check with the
  in-memory cache," and removing unrelated dead code is a separate,
  unauthorized change) but flags it here as a follow-up worth a
  separate, explicitly-scoped cleanup task.
- Per-source duplicate counts use four fixed columns
  (`source_ws`/`source_catchup`/`source_retry`/`source_hot_burst`) —
  if a fifth source is ever added to `_process_subprov_sig_durable`'s
  callers in the future, duplicates from that source would still be
  correctly counted in `duplicate_count` but would not increment any
  per-source column (silently, not an error — see
  `test_unrecognized_source_does_not_crash_and_omits_source_increment`).
  A future maintainer adding a new source should also add a
  corresponding column and entry in `_DEDUPE_SOURCE_COLUMNS`.

## Exact criteria for beginning the future retention analysis

Per [x64_9b1_measurement_contract.md](x64_9b1_measurement_contract.md):
**both** of the following must hold simultaneously before a retention
cutoff may be proposed:
1. At least 14 days of instrumentation uptime, spanning multiple
   `ws_cascade` restarts and at least one naturally-occurring
   CATCHUP-sourced (replay) event.
2. At least 500,000 signatures checked (`total_checked` in
   `wt_subprov_sig_dedupe_summary`).

Until both conditions are met, no purge of `wt_subprov_sig_retry` DONE
rows should be proposed or executed — this remains the explicit
successor task to this one, not authorized by this report.
