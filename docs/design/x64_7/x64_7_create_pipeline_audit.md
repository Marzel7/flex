# X64.7 — CREATE Persistence Pipeline Audit and Canonical Ledger Implementation

Master report. Companion documents: [x64_7_call_graph.md](x64_7_call_graph.md)
(Phase 2), [x64_7_unresolved_fixtures.csv](x64_7_unresolved_fixtures.csv)
(Phase 3), [x64_7_failure_classification.csv](x64_7_failure_classification.csv)
(Phase 5), [x64_7_ledger_schema.md](x64_7_ledger_schema.md) (Phase 6),
[x64_7_backfill_dry_run.md](x64_7_backfill_dry_run.md) (Phase 10),
[x64_7_implementation.md](x64_7_implementation.md) (Phases 7-9, 11),
[x64_7_shadow_validation.md](x64_7_shadow_validation.md) (Phase 12),
[x64_7_regression_results.md](x64_7_regression_results.md) (Phase 13),
[x64_7_conflicts.csv](x64_7_conflicts.csv) (empty — no conflicts
encountered).

## Phase 1 — Unresolved population snapshot

Re-derived live count via `anchor_reconciliation.dry_run_report()`:
**33 `MINT_NOT_FOUND` rows** at the time of this audit — down from
X64.6's 42-row baseline, since the 13 rows X64.6's bounded-RPC pass
recovered were confirmed still fully released (all 13 now show
`status='complete'`, walked by the ordinary worker since). Composition:
**19 `CREATOR_UNKNOWN`, 14 `CREATOR_KNOWN_RPC_UNRESOLVED`** — matching
X64.6's expected baseline of 19+14 exactly, confirming no drift in the
underlying failure-mode split even though the total count moved (new
rows entered at the same 19:14 ratio as the queue continued live).

No `DUPLICATE_BLOCKED_ROW`/`ALREADY_PROCESSED_ELSEWHERE`/
`STATE_INCONSISTENCY` rows found (same structural PK guarantee X64.6
established still holds). Full row-level detail in the raw population
export; the 9 curated fixtures (Phase 3) carry the fuller
per-mint evidence.

## Phase 2 — End-to-end CREATE call graph

Full detail in `x64_7_call_graph.md`. Traced via a dedicated Explore
agent pass, then independently spot-checked at every load-bearing claim
by direct file read. Confirms:
- `_update_token_entry_with_creator` (`pumpfun_curve_listener.py:7788`)
  is the sole function writing `bonding_curve_pda`/`create_tx_signature`
  at migration time, gated on `earliest_creator` being resolved
  (line 8996).
- `CREATOR_BACKFILL_ENABLED=0` in `run_listener.sh` (production
  default) disables the only fallback RPC walk that would resolve both
  creator and CREATE signature together when the fast-path lookup fails.
- Every `creator_funding_queue` write path requires a non-null creator;
  none require a non-null signature.
- Three genuinely silent (zero logging) early returns existed in
  `handle_birth` prior to this task's Phase 4 instrumentation.
- A structural, unresolvable race exists between `handle_migration` and
  `handle_birth` (independent concurrent async tasks, no ordering
  guarantee) — `COALESCE`-based writes prevent clobbering but not
  eventual completeness.

## Phase 3 — Canonical unresolved fixtures

Full detail in `x64_7_unresolved_fixtures.csv`. 3 creator-null, 3
creator-known-unresolved, 3 successful-control fixtures. **The earliest
observable divergence point, confirmed across all 9 fixtures with zero
exceptions**: `token_analysis.bonding_curve_pda` is `NULL` for all 6
failure fixtures and populated for all 3 control fixtures. Since
`bonding_curve_pda` and `create_tx_signature` are written together, in
the same `UPDATE` statement, by the same function
(`_update_token_entry_with_creator`), this single column's presence/
absence is a reliable, directly-observable proxy for "did the CREATE-
capture write path ever run for this mint" — and it never ran for any of
the 6 failure fixtures.

## Phase 4 — Instrumentation audit

Existing logs **could not** answer most of the required stage questions
before this task — confirmed by direct code read of `handle_birth`'s
three silent early returns. All 11 required event names implemented as
additive `log_print` calls (zero control-flow changes) in
`pumpfun_curve_listener.py`'s `handle_birth`. Full mapping in
`x64_7_implementation.md`.

## Phase 5 — Failure-mode determination

Full detail in `x64_7_failure_classification.csv`. All 6 failure
fixtures classify **Mode E — "CREATE found, mint resolved, creator
unresolved"** for the 3 creator-known fixtures (HIGH confidence — direct
code-and-config evidence: `CREATOR_BACKFILL_ENABLED=0` blocking the sole
function that would have populated `bonding_curve_pda`/
`create_tx_signature`, even though the creator itself was independently
resolved via the birth-reconciler path). The 3 creator-null fixtures sit
at the **A/E boundary** (MODERATE confidence — `handle_birth` was never
observed to fire for these mints at all, evidenced by `bonding_curve_pda`
AND `first_pre_migration_signal_at` AND `migration_signal_source` all
being `NULL` simultaneously; distinguishing "transaction never received"
(mode A) from "received but the listener's own internal state doesn't
retain a birth-signal trace for mints later discovered only via price-
snapshot backfill" (a variant of mode E) would require log-level tracing
this session's read-only DB access could not perform).

**Direct answer to the task's two specific sub-questions**:
- **Does creator-resolution failure incorrectly prevent CREATE
  persistence itself, not just enrichment?** **Yes, confirmed.** Prior to
  this task, `_update_token_entry_with_creator` — the only function that
  wrote `create_tx_signature` at migration time — was entirely gated on
  `earliest_creator` being resolved. This is precisely the bug X64.7's
  ledger fixes: CREATE persistence is now creator-independent.
- **For creator-known unresolved rows, where does the common failure
  occur?** **During persistence** (mode E specifically) — not before
  parser invocation (the parser/birth-reconciler DID run, resolving a
  creator), not during parser branching (the CREATE instruction itself
  was never separately validated/persisted by that same path), and not
  during async handoff (no evidence of a lost message — the birth-signal
  timestamp is durably present in `token_analysis`, only the CREATE-
  signature-specific write never happened).

## Phase 6-9, 11 — Ledger, wiring, resolver priority, restart durability

Full detail in `x64_7_ledger_schema.md` and `x64_7_implementation.md`.
`wt_create_event_ledger` implemented exactly per the task's suggested
schema (signature PK, mint required, creator nullable, idempotent
upsert, first/last-seen tracking, no attribution fields) plus a
`wt_create_ledger_conflicts` audit table for the two hard-conflict cases.
Wired into `handle_birth` at the earliest point CREATE is validated,
using the existing serialized write-service lane, best-effort (never
blocks birth processing on a ledger-write failure). `resolve_anchor_with_
priority()` implements the exact 6-tier priority order, ledger first,
creator agreement never required. Restart durability verified by test
(fresh SQLite connection to the same on-disk file after a simulated
process restart; the underlying `sqlite3` `conn.commit()` inside
`record_create_event` is the actual durability mechanism — verified as
the real production write path, not assumed from SQLite's general
autocommit properties, since the write goes through
`database_write_service.submit()`, the same serialized/committed lane
every other durable write in this listener uses).

## Phase 10 — Historical backfill

Full detail in `x64_7_backfill_dry_run.md`. 1,280,050 signatures
eligible, all `BACKFILL_SAFE`, zero conflicts — **but confirmed to
recover 0 of the 33 currently-unresolved rows**, since none of them have
a stored signature anywhere to backfill from (same root finding X64.6
already established). Execution **deferred** to a dedicated follow-up
task, by explicit decision, since it provides no benefit to the
population this audit is actually trying to fix and is a large
operational write deserving its own maintenance window.

## Phase 12 — Live shadow validation

Full detail in `x64_7_shadow_validation.md`. **Deferred** — the live
listener process was already running and had not loaded this task's code
changes (no hot-reload); restarting it mid-investigation was judged an
operational deployment decision outside this audit's scope, not
performed. Procedure documented for the next planned deployment.

## Phase 13 — Regression tests

Full detail in `x64_7_regression_results.md`. 23 new tests (22 required
+ 1 split), all passing. Combined suite (140 tests across X64.5/X64.6/
X64.7/disposable-subprov-evidence) passes clean.

---

## Required summary

- **Initial unresolved rows**: 33
- **Creator-null rows**: 19
- **Creator-known unresolved rows**: 14
- **Earliest confirmed loss stage**: Persistence (Mode E) — the CREATE-
  signature write is coupled to, and blocked by, creator-resolution
  failure inside `_update_token_entry_with_creator`'s calling guard
  (`pumpfun_curve_listener.py:8996`), gated in turn by
  `CREATOR_BACKFILL_ENABLED=0`.
- **Rows classified per failure mode**: 6 fixtures directly classified
  (3× Mode E at HIGH confidence, 3× Mode A/E boundary at MODERATE
  confidence); by extrapolation (same `bonding_curve_pda=NULL` signature
  confirmed structurally tied to the same code path for all 33 via the
  Phase 1 population data), the same two-mode split applies to the full
  population, though only the 9 curated fixtures were individually,
  fully evidenced.
- **Canonical ledger rows backfilled**: 0 (deferred by decision — 1.28M
  eligible, dry-run validated, not executed)
- **Previously unresolved rows recovered from ledger**: 0 (the ledger is
  empty in production as of this task; it will begin recovering FUTURE
  unresolved rows once deployed and live traffic starts writing to it —
  see Phase 12's deferred shadow validation)
- **Conflicts**: 0
- **Remaining unresolved**: 33 (unchanged by this task — X64.7 fixes the
  pipeline for FUTURE CREATE events; it does not retroactively recover
  the current population, which has no stored signature anywhere to
  recover from, by any zero-RPC or ledger-based method)
- **Live CREATE detections**: not measured (Phase 12 deferred)
- **Committed ledger writes**: 0 in production (not yet deployed)
- **Migrations without ledger anchor**: not measured (Phase 12 deferred)

### What is the earliest stage at which the unresolved CREATE events
disappear?

**Persistence** — specifically, the calling guard around
`_update_token_entry_with_creator` at `pumpfun_curve_listener.py:8996`
(`if earliest_creator:`), which prevents the CREATE signature from ever
being written when creator resolution fails, even though the CREATE
transaction itself may have been (and for 14 of 33 rows, was) separately
and successfully observed via the birth path.

### Does creator resolution currently gate CREATE persistence?

**Yes, prior to this task — confirmed directly, not inferred.** This is
the single most important finding of this audit and the one X64.7's
ledger is designed to eliminate.

### Is the loss caused by one parser branch, listener source, or writer?

**One writer function's calling guard** (`_update_token_entry_with_
creator`'s gate), not one parser branch or listener source — the
underlying CREATE-observation mechanisms themselves (WS, webhook, birth
reconciler) are working correctly for 14 of 33 rows (creator WAS
resolved); the loss is specifically in what happens (or doesn't happen)
to the CREATE signature once creator resolution's own success/failure is
known.

### Can the listener prove that every validated CREATE is durably
committed?

**Not before this task — no**, three silent early returns in
`handle_birth` meant a validated-then-rejected CREATE left no trace.
**After this task's Phase 4 instrumentation, yes, provably** — every
exit branch in `handle_birth` now logs a structured event distinguishing
receipt, parse start, parse rejection (with reason), instruction found,
mint/creator resolution, and ledger write attempt/commit/failure. Full
provability requires the Phase 12 shadow-validation deployment (deferred)
to confirm in live production traffic, not just by code inspection.

### Does walkback now obtain its anchor independently of funding
extraction?

**Yes** — `resolve_anchor_with_priority()`'s first-priority source
(`wt_create_event_ledger`) has zero dependency on `creator_funding_queue`,
`token_analysis`'s creator fields, or any funding-extraction success —
verified by `find_stored_create_anchor`'s complete absence of any
`creator_funding_queue`/attribution-table reference in
`record_create_event`'s own source (test 9).

### Are creator-null launches safely persisted and recoverable?

**Yes** — `record_create_event(creator=None, ...)` succeeds
unconditionally (the only required field is `mint`), and
`lookup_create_anchor()` returns `confidence='SAFE'` for a creator-null
ledger row exactly as for a resolved-creator one — verified by tests 2
and 14.

### What residual cases still require RPC?

The 33 currently-unresolved rows themselves — since none of them have
any stored signature (in the ledger or any legacy source), recovering
them requires the same bounded-RPC approach X64.6 already implemented
and executed for its own 42-row population (13 of 42 recovered that way).
A fresh bounded-RPC pass against the current 33-row population, using the
same `apply_rpc_recovered_anchor()` persistence function (now also
capable of writing into the ledger via a small follow-up, not yet wired)
would be the direct next step — not performed in this task, since it was
out of X64.7's stated scope (pipeline fix, not another recovery round)
and would require the same temporary-key authorization discipline X64.6
established.

## Success criteria — assessed

- **"Every validated CREATE instruction with a resolved mint is durably
  written to a canonical ledger before enrichment begins"**: **true in
  code**, confirmed by test and direct read; **not yet true in
  production**, since the listener has not been restarted to load this
  change (Phase 12, deferred by decision).
- **"creator=NULL can no longer prevent CREATE persistence"**: **true**,
  both in the new ledger module (unconditionally) and in the calling
  code path (the ledger write in `handle_birth` has no creator gate,
  unlike the pre-existing `_update_token_entry_with_creator` call it
  sits alongside).
- **"migration observed but no canonical CREATE ledger row is either
  eliminated or surfaced immediately as an explicit, attributable
  pipeline failure"**: **the surfacing mechanism is implemented**
  (structured logging distinguishes every exit branch); **full
  elimination is not yet measured in production** (Phase 12 deferred);
  a dedicated alert on this specific condition (migration observed, no
  ledger row) was not implemented as a new alerting mechanism — flagged
  as a small, reasonable follow-up rather than claimed as done.
