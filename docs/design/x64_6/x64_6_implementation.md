# X64.6 — Implementation Summary

## Code changes

### `src/ops/anchor_reconciliation.py` (extended, not replaced)

Three additions on top of the X64.5 module, all additive:

1. **`ensure_schema()` extended** — four new columns on
   `wt_anchor_reconciliation_log` (`source_row_id`, `recovery_method`,
   `rpc_credits_used`, `validation_result`), so a single log row is
   self-describing across both the zero-RPC (X64.5) and wider-source/
   bounded-RPC (X64.6) recovery paths without needing to cross-reference
   which module version wrote it.

2. **`find_stored_create_anchor(live_conn, ops_conn, mint, creator=None)`**
   — Phase 6's zero-RPC widened search. Checks `creator_funding_queue`,
   `token_analysis`, `wt_detected_creates` (all live DB) and
   `wt_watchtower_launches` (ops DB, presence-only — this table has no
   signature column, so a match there alone is never treated as a
   recoverable signature). Returns `confidence='SAFE'` only when exactly
   one distinct valid signature is found across every source;
   `confidence='CONFLICT'` if two sources disagree;
   `confidence='NONE'`/`conflict_reason='NO_STORED_CREATE_SIGNATURE'` if
   nothing is found anywhere. Performs zero RPC — pure SQL reads.

3. **`apply_rpc_recovered_anchor(ops_conn, *, mint, creator, signature,
   rpc_credits_used, recovery_method=...)`** — Phase 8's persistence
   repair. Deliberately kept as its own function, never merged into
   `reconcile_waiting_create_anchors()`, because the task's constraint is
   explicit: **anchor recovery and walkback execution must remain
   separate stages**, and the RPC search itself (Phase 7) must be a
   clearly separate stage from persistence too — this function performs
   **no RPC of its own**; it only validates (`valid_signature()`) and
   writes a signature that was already found by an external process.
   Idempotent (a row no longer `status='waiting'`/`path_state=
   WAITING_FOR_CREATE_ANCHOR` is not matched by the guarded UPDATE, and a
   duplicate log row is suppressed on replay via a pre-insert existence
   check). Never overwrites a different existing valid anchor — returns
   `conflict_existing_valid_anchor_differs` instead. Never touches
   `attempts`, `subprov`, `treasury`, or any attribution table.

### Standalone bounded-RPC recovery script (scratchpad, not committed to
the repo)

A standalone script performed Phase 7's actual RPC search — deliberately
**not** added to `src/`, since it is a one-time investigative tool
(paginate creator history, filter to a bounded time window, check for
pump.fun-program + target-mint co-occurrence, stop at first match),
consistent with this project's established pattern of keeping ad hoc RPC
investigation scripts out of the production module tree (per prior
session's `scripts/x55_exhaustive_history_audit.py`-style precedent, but
this one was small enough not to warrant a permanent `scripts/` entry).
Its logic — `find_create_tx()` — is fully documented in
`x64_6_rpc_recovery_dry_run.md` and could be promoted to a permanent
script in a follow-up task if this recovery shape is needed again
regularly; not done here to avoid maintaining an unused tool.

## What was NOT changed

- **`_enqueue_creator_funding_job()`** (`pumpfun_curve_listener.py`) —
  the actual upstream writer responsible for populating
  `creator_funding_queue` in the first place. Traced exhaustively (Phase
  4 of the master audit) across all 10 call sites and the `if not
  creator or not mint: return False` gate, but **not modified** by this
  task. The population data does not point to a single fixable bug in
  this function — see the master audit's Phase 4/6 root-cause discussion
  for why a targeted code fix isn't clearly indicated by the evidence
  gathered, and Phase 9's architectural recommendation for what should
  change instead.
- **No RPC was spent inside `anchor_reconciliation.py` itself** — all 114
  RPC credits were spent by the separate, standalone script, then handed
  to `apply_rpc_recovered_anchor()` as already-validated results. This
  physical separation is what makes the "no RPC in this module" tests
  (Phase 10, tests 13a/13b) meaningful rather than trivially true.

## Live production run

Executed against `database/wt_ops_v2.db`/`database/flex_complete_database.db`:

```
Phase 6 (zero-RPC widened search): 0 of 42 recoverable — confirmed
  exhaustively empty across all four widened sources.
Phase 7 (bounded RPC): 27 of 42 rows had a known creator and were
  searched; 13 recovered, 14 unresolved. 114 total RPC credits.
Phase 8 (persistence): all 13 recovered signatures applied via
  apply_rpc_recovered_anchor() — all succeeded, all verified idempotent
  on replay, all confirmed selectable by drain_batch's WHERE clause.
```

Verified directly: `wt_anchor_reconciliation_log` now has 13 rows with
`recovery_method='bounded_rpc_create_search'`, each carrying `mint`,
`creator`, `recovered_signature`, `original_state`, `recovery_source`,
`recovery_timestamp`, `rpc_credits_used`, `validation_result='VALID'` —
matching Phase 8's exact required audit-row shape.

## Phase 9 — Future capture hardening (design, not implemented)

The task's preferred architecture:
```
CREATE observed
  ↓
canonical CREATE-event record
  ↓
funding extraction may enrich later
  ↓
walkback anchor resolver reads canonical record
```

**Assessment: `creator_funding_queue` is confirmed to be the wrong
architectural dependency for CREATE anchoring**, for a reason directly
demonstrated by this task's own data: `creator_funding_queue.
create_tx_signature` is frequently NULL even on rows that DO exist for a
mint (confirmed in the master audit's Phase 2 — several of the 42 rows'
creators have other, successfully `status='complete'` funding-queue rows
with `create_tx_signature=NULL`). This table's own primary purpose is
funding **extraction** bookkeeping (retry counters, priority, funder
discovery status) — the CREATE signature is carried as an incidental
enrichment field, not its reason for existing. Making it the sole source
of CREATE truth means a walkback anchor's existence depends on funding
extraction's own success, which is a materially different, harder
problem (RPC-bound wallet-history walks) than "did this mint's CREATE
transaction happen" (a fact establishable at CREATE-observation time,
zero RPC, before any funding analysis begins).

**Recommended canonical source**: `token_analysis.create_tx_signature`
is architecturally closer to the right shape (per-mint, written at/near
CREATE-observation time by the live listener, independent of funding
extraction's own success/failure) — but this task's own data shows it is
*also* frequently NULL for the 42 blocked rows (confirmed: 0 of 42 had a
non-NULL `token_analysis.create_tx_signature`), meaning it currently has
the same underlying capture gap, not a different one. **The real fix is
upstream of both tables**: a dedicated, append-only CREATE-event ledger
written directly and unconditionally by the CREATE-instruction parser
itself (Failure Mode B in the master audit — "CREATE received but parser
rejected it" — and Failure Mode E — "database insert failed" — are the
two failure modes this task's evidence could not rule out without
listener-level tracing beyond this task's scope), with funding
extraction and creator resolution treated as later, independent
enrichment passes that read from — but never gate the existence of — that
ledger. This is a genuine code/architecture change beyond this task's
scope (the task's own Phase 9 instruction frames it as a design
recommendation, not a required implementation) — documented here as the
concrete recommendation, not implemented.
