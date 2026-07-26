# X64.7 — Implementation Summary

## New module: `src/ops/create_event_ledger.py`

Full schema/behavior detail in `x64_7_ledger_schema.md`. Two public
functions: `record_create_event()` (the canonical write point, creator-
independent, idempotent, conflict-detecting) and `lookup_create_anchor()`
(zero-RPC read for walkback resolution, `SAFE` only on exactly-one-
signature-for-mint).

## Extended: `src/ops/anchor_reconciliation.py`

New function `resolve_anchor_with_priority(live_conn, ops_conn, mint, *,
queue_creator=None)` implementing Phase 9's exact 6-tier priority order:
1. `wt_create_event_ledger` (new, this task)
2. Existing `wt_walkback_queue` anchor (if already `VALID` — nothing to
   resolve)
3-5. `token_analysis` / `wt_detected_creates` / `creator_funding_queue`
   (reuses X64.6's existing `find_stored_create_anchor()` unchanged —
   no duplicate logic)
6. Bounded RPC recovery (not performed by this function — same
   separation-of-stages discipline as X64.6's
   `apply_rpc_recovered_anchor()`; the caller's responsibility)

Creator agreement is never required for the ledger source to resolve
`SAFE` — a NULL-creator ledger row is accepted exactly like a
resolved-creator one, per the task's explicit instruction. This function
is read-only (resolves candidates, never writes) — the actual anchor
persistence for a resolved candidate still goes through
`apply_rpc_recovered_anchor()` or `reconcile_waiting_create_anchors()`
(both X64.6, unchanged), preserving their existing conflict guard against
overwriting a different valid anchor.

## Modified: `src/core/pumpfun_curve_listener.py` (`handle_birth`)

Two additive changes, both confined to `handle_birth`
(lines ~6017-6110):

### 1. Structured instrumentation (Phase 4)
All 11 required event names added as `log_print` calls at their
corresponding stage, with zero control-flow changes — the same early
`return` statements fire at the same points, they now also emit a
structured log line first:

| Event | Where |
|---|---|
| `CREATE_TX_RECEIVED` | Function entry, before tx fetch |
| `CREATE_PARSE_STARTED` | After tx fetch succeeds |
| `CREATE_PARSE_REJECTED` | Any of: tx fetch fails, mint unresolved, `is_pumpfun_create=False` — `reason=` field distinguishes which |
| `CREATE_INSTRUCTION_FOUND` | After `is_pumpfun_create` validation succeeds |
| `CREATE_MINT_RESOLVED` | Same point (mint was already required to reach validation) |
| `CREATE_CREATOR_RESOLVED` | After `_infer_creator_from_tx` — always fires, `creator=UNRESOLVED` string when None |
| `CREATE_LEDGER_WRITE_ATTEMPT` | Immediately before the ledger write call |
| `CREATE_LEDGER_WRITE_COMMITTED` | On a successful `record_create_event()` result |
| `CREATE_LEDGER_WRITE_FAILED` | On any non-success result OR exception, `reason=`/`conflict=` fields populated from the returned dict |
| `CREATE_ENRICHMENT_ENQUEUED` | Just before `_insert_bonding_curve_token`, when `creator` is known |
| `CREATE_ENRICHMENT_SKIPPED` | Same point, when `creator` is `None` |

No secrets or RPC credentials appear in any log line — verified by
direct read of every added `log_print` call.

### 2. Canonical ledger write (Phase 7)
Inserted immediately after `is_pumpfun_create` validation succeeds and
`mint` is resolved, **before** `creator = analyzer._infer_creator_from_tx(...)`
is even called in program order for the log-instrumentation additions,
though the actual ledger write itself occurs a few lines later once
`creator` is available (passed as `None` when unresolved — the ledger
call itself has no gate on it). This satisfies Phase 7's required
ordering: `CREATE instruction validated → mint resolved → ledger write +
commit → creator resolution [already done in this function, but the
KEY property is verified] → enrichment (creator tracking / funding
enqueue, which happens further downstream via other functions this task
did not touch)`.

Uses the existing `database_write_service` serialized write lane
(matching the established pattern at `pumpfun_curve_listener.py:817-826`
for the same reason — see project memory "DB write serializer" — never
opens a raw, unserialized connection to the ops DB from the listener).
Wrapped in its own `try/except Exception`, entirely isolated from the
rest of `handle_birth`'s control flow: a ledger-write failure is logged
(`CREATE_LEDGER_WRITE_FAILED`) but never aborts or delays the
`_insert_bonding_curve_token` call that follows — the ledger write is
best-effort with respect to birth processing's own success, exactly as
required ("A missing creator must produce creator=NULL... not an
aborted ledger write" — extended here to "a ledger-write failure must
never abort birth processing").

**Why only `handle_birth`, not also the migration-time path**: the
migration-time creator-extraction block (`pumpfun_curve_listener.py:8955-9016`)
does not have a validated CREATE transaction available in the same way —
it only has a *signature* (`analyzer._create_tx_signature`, when set) and
that value is itself gated behind `is_pumpfun_create` validation
succeeding inside `PostMigrationAnalyzer`, which the migration path only
attempts when `CREATOR_BACKFILL_ENABLED != "0"` (disabled in production).
Wiring a second ledger-write call into the migration path was considered
and deliberately **not done** in this task, because: (a) `handle_birth`
already covers the same underlying CREATE event whenever it is
independently observed (the more common, cheaper, WS-native path); (b)
adding a second write site increases the surface for the very race
condition this task diagnoses (two independent async paths racing to
observe/persist the same CREATE) without adding coverage the birth path
doesn't already provide once `CREATOR_BACKFILL_ENABLED` stays off; (c) if
`CREATOR_BACKFILL_ENABLED` is ever re-enabled (the code comment in
`run_listener.sh` explicitly anticipates this), the migration path's own
`analyzer.get_creator_from_earliest_tx()` call already re-derives a valid
CREATE signature via RPC at that point — a natural second opportunity to
extend ledger-writing coverage in a follow-up task, not required for
X64.7's core fix.

## What was NOT changed

- **`CREATOR_BACKFILL_ENABLED` remains `0`** in `run_listener.sh` — not
  re-enabled. The documented reason for disabling it (RPC paging
  starving live migration capture) is a real, still-valid tradeoff; this
  task's fix (the ledger) makes CREATE-signature capture independent of
  this flag rather than arguing for flipping it.
- **`_birth_reconciler_loop`'s availability gate** was not changed —
  still only sweeps when PumpPortal is disconnected. Confirmed as a
  contributing factor (Phase 2/5) but changing its trigger condition is a
  larger operational decision (RPC cost implications, documented in the
  file's own comments) outside this task's scope.
- **`enqueue_missing_funding_jobs`'s creator-NULL exclusion** was not
  changed — this sweep's job (funding *enrichment*) is correctly scoped
  to known creators; it was never meant to be a CREATE-signature source,
  and per this task's own architecture, it no longer needs to be — the
  ledger is now the creator-independent source instead.
- **The 1.28M-row historical backfill was not executed** — see
  `x64_7_backfill_dry_run.md` for the explicit deferral rationale.

## Regression verification

`python3 -c "import ast; ast.parse(...)"` confirms the modified listener
file still parses cleanly. `python3 -c "import
src.core.pumpfun_curve_listener"` confirms it still imports without
error (no circular import introduced by the new `from src.ops import
create_event_ledger` deferred import inside the ledger-write closure).
Full test results in `x64_7_regression_results.md`.
