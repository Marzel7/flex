# X64.7A — Canonical CREATE Commit Hardening (Phases 1-4, 8)

Implementation-only phase of X64.7A. Phases 5-7 (planned listener
restart + bounded live shadow observation) were explicitly deferred to a
separate deployment task — restarting the live production listener
mid-investigation was judged an operational decision requiring its own
maintenance window, not something to bundle into implementation work
(same reasoning X64.7's own Phase 12 deferral already established).

## Phase 1 — Corrected ordering

**Problem confirmed**: X64.7's ledger write in `handle_birth` was
sequenced *after* `creator = analyzer._infer_creator_from_tx(tx_data)` in
program order. While `create_event_ledger.record_create_event()` itself
was already creator-independent (accepts `creator=None`), an exception
raised *inside* `_infer_creator_from_tx` would have propagated before the
ledger write was ever reached — meaning a genuine parser/inference
failure could still silently cost the CREATE observation entirely, the
exact class of loss X64.7 was meant to close.

**Fix**: `handle_birth` now performs two writes:
1. Immediately after `CREATE_MINT_RESOLVED` (mint known, instruction
   validated) — a first ledger write with `creator=None`,
   `creator_resolution_state='PENDING'`, via the new
   `_write_create_ledger_durable()` helper.
2. After `_infer_creator_from_tx` returns (or the equivalent exception
   propagates — see below) — a second, idempotent write with the
   resolved creator (or `None` if genuinely unresolved) and
   `creator_resolution_state` set to `RESOLVED`/`UNRESOLVED`.

`record_create_event()` was extended with an explicit
`creator_resolution_state` parameter (previously auto-derived from
`creator` alone) specifically so `PENDING` (inference hasn't run yet) is
distinguishable from `UNRESOLVED` (inference ran and genuinely found
nothing) — this distinction did not exist in X64.7's schema.

**If `_infer_creator_from_tx` now raises**, the exception is caught by
`handle_birth`'s own outer `try/except` (unchanged from X64.7/before) —
but the PENDING ledger row from step 1 is already committed and
unaffected, since it happened on an earlier line, in an earlier,
already-completed database transaction. Verified by test
(`test_creator_inference_exception_after_pending_write_leaves_ledger_row_intact`).

## Phase 2 — Durable failed-write recovery

New table `wt_create_ledger_pending` (exact schema per the task's spec,
implemented in `src/ops/create_event_ledger.py`), plus two new functions:

- `persist_pending_write()` — called by `_write_create_ledger_durable()`
  (the listener-side helper) whenever a ledger write fails for a
  transient reason (not an invalid-input reason like a malformed
  signature or missing mint — those are never retryable, retrying them
  can never succeed). Idempotent on signature: a second failure updates
  `attempts`/`last_error`/`next_retry_at` rather than duplicating a row.
- `retry_pending_writes()` — zero-RPC, bounded-backoff
  (`30s → 120s → 600s → 1800s → 3600s`, 5 attempts max), lock-tolerant
  retry pass. Called from `walkback_worker.py`'s ordinary `run_loop()`
  cycle (same non-essential-maintenance pattern as the existing anchor
  reconciliation pass — a lock-contention error is logged and skipped,
  any other exception still propagates). A row that exhausts its retry
  budget stays in the table (never silently dropped) but is excluded
  from future selection — surfaced via the function's own `exhausted`
  count.

**Double-failure case** (both the ledger write AND the durable
pending-write persistence fail — e.g. the entire ops DB is unreachable):
`_write_create_ledger_durable()` catches this specifically and emits
`[CREATE_LEDGER_CRITICAL_FAILURE]`, the one case where the failure would
otherwise exist nowhere durable at all.

## Phase 3 — Migration coverage check

New table `wt_migration_ledger_coverage` and function
`_record_migration_coverage()` in `src/core/watchtower_attribution.py`,
called from `store_migration()` immediately after the existing
`enqueue_migration()` call, wrapped in its own `try/except` (never blocks
migration recording — verified by test
`test_store_migration_succeeds_even_if_coverage_check_raises`).

At migration time, queries `wt_create_event_ledger` by mint via the
existing `lookup_create_anchor()` and persists exactly one of:
`MIGRATION_CREATE_LEDGER_PRESENT` (ledger has a resolved-or-PENDING-state
single signature), `MIGRATION_CREATE_LEDGER_PENDING` (the ledger row
exists but `creator_resolution_state='PENDING'` — the CREATE observation
committed, creator inference hasn't finished/enriched yet),
`MIGRATION_CREATE_LEDGER_MISSING` (no ledger row for this mint at all —
the exact condition Phase 12/X64.7 flagged as needing an explicit,
queryable signal), or `MIGRATION_CREATE_LEDGER_CONFLICT` (the ledger
itself has ambiguous multi-signature evidence for this mint). The
`MISSING` case additionally sets `alert_emitted_at` and prints a
`[MIGRATION_CREATE_LEDGER_MISSING]` structured log line.

**A real, independently-caught bug**: the schema-creation helper for
this table originally used a process-wide "already ensured" boolean
flag, which incorrectly skipped table creation for a second, different
SQLite connection in the same process (e.g. two separate test fixtures,
or in principle two different DB files opened in sequence). Fixed to run
`CREATE TABLE IF NOT EXISTS` unconditionally on every call — cheap, and
correct per-connection rather than per-process. Caught by
`test_migration_with_pending_ledger_row_records_pending` and
`test_migration_without_ledger_row_records_missing` failing with
`no such table` on first implementation, before the fix.

## Phase 4 — Resolver integration, actually wired into production

**Confirmed gap**: `resolve_anchor_with_priority()` (X64.7's own Phase 9
function) existed but nothing in production called it — the function the
worker's `run_loop()` actually invokes each cycle,
`reconcile_waiting_create_anchors()`, still only called the older,
ledger-unaware `classify_stuck_row()`.

**Fix**: `reconcile_waiting_create_anchors()` now calls
`resolve_anchor_with_priority()` first for every stuck row; only when
its result is not `SAFE`-from-the-ledger does it fall back to the
pre-existing `classify_stuck_row()` widened-source search — preserving
every existing classification label (`RECOVERABLE_VALID_ANCHOR`,
`AMBIGUOUS_MULTIPLE_ROWS`, etc.) and all X64.5/X64.6 callers' behavior
unchanged for the case where the ledger has nothing. Verified by test
(`test_ordinary_reconcile_function_recovers_ledger_only_signature`) that
a row whose signature exists **only** in `wt_create_event_ledger** (not
`creator_funding_queue`, not `token_analysis`) is now recovered by this
exact production function — not just by the standalone helper directly.

## What was NOT changed in this phase

- Phases 5-7 (deployment, live shadow validation, production regression
  fixtures) — deferred, see `x64_7a_deployment_deferred.md`.
- `CREATOR_BACKFILL_ENABLED` remains `0` — unrelated to this phase's
  scope, unchanged since X64.7.
- No new alerting/paging integration for `MIGRATION_CREATE_LEDGER_MISSING`
  beyond the structured log line and durable table row — a dashboard/
  alert consuming this table is a reasonable small follow-up, not
  implemented here.
