# X41.0 — Safe Implementation of Canonical Evidence Architecture (Shadow Mode Only)

Implementation report. Follows [X39.0](X39_0_CANONICAL_ENTITY_RECONCILIATION_AUDIT.md) and
[X40.0](X40_0_CONSOLIDATION_PRECONDITIONS_AND_AUDIT_LEDGER_DESIGN.md). The frozen
architectural rules (Operator ≠ Operation, `wt_ops_v2` is the canonical Operation store,
family ≠ ownership, fingerprinting can never prove Operator identity, evidence is
append-only, confidence axes stay independent) were not revisited — this is implementation
only, additive and shadow-mode as instructed.

## What was built

### 1. `src/core/attribution_evidence.py` — the AttributionEvidence ledger
New table `attribution_evidence` (+ `attribution_evidence_write_failures` for the
swallow-and-log contract). `record_evidence()` implements the exact contract from
X40.0 Phase 2: append-only, distinct `confidence_axis`/`confidence_value` pair (never
coerced to one scale), `reconstructed`/`reconstruction_source`/`reconstruction_confidence`/
`timestamp_quality` fields for backfilled rows. **Never raises** — any internal failure is
caught, logged to the write-failures table on a fresh connection, and swallowed.

### 2. `src/core/operation_merge_ledger.py` — the OperationMergeLedger
New table `operation_merge_ledger` (+ its own write-failures table). `record_merge_event()`
records the exact rule that fired (`SAME_ROOT` / `DIRECT_TREASURY_MEMBERSHIP` /
`SHARED_DECISIVE_INFRA` / `BROAD_INFRA_OVERLAP_GE_3`), before/after state snapshots, and
supports `reverses_event_id` for future SPLIT events. `determine_merge_rule()` **duplicates
read-only classification logic** (never the merge decision) so the ledger can name which
rule fired without `_find_hard_merge_target()` needing to change its signature or behavior —
that function is untouched, verified by diff.

### 3. Dual-write wiring (all confirmation/merge paths instrumented)
Five treasury-attribution call sites in `src/core/treasury_bank.py`:
`promote_to_confirmed` → `MANUAL_APPROVAL`; `auto_confirm_from_launch_chain` →
`LAUNCH_CHAIN_CONFIRMATION`; `revert_auto_promotion` → `REVERSION`; `auto_evaluate`'s two
branches → `FINGERPRINT_EVALUATION`. Two dashboard-route call sites in
`src/core/operation_dashboard_routes.py`: the subprov-link "set" action →
`RPC_VERIFIED_TRACE`; the approve-candidate route's approve/reject branches →
`MANUAL_APPROVAL`/`MANUAL_REJECTION`. One merge path in
`src/core/operation_store_v2.py:persist()` → `HARD_MERGE`/`TREASURY_ADDED`/
`OPERATION_CREATED`/`FAMILY_LINKED`. Every call happens strictly **after** the existing
authoritative write already committed.

**Hardened after a real Gate F failure was found during testing** (see below): every
dual-write call site now goes through a local wrapper (`_record_attribution_evidence` in
treasury_bank.py, `_record_merge_event`/`_determine_merge_rule` in operation_store_v2.py)
that itself catches all exceptions — not just relying on `record_evidence()`'s internal
try/except — so even a catastrophic module-import failure cannot propagate into the
existing confirmation flow.

### 4. `src/core/operator_projection_shadow.py` — shadow Operator projection
New table `wt_operator_entities_projection_shadow`, regenerated from
`attribution_evidence` CONFIRMED events (with a documented fallback to
`wt_confirmed_treasuries` for wallets not yet backfilled into the ledger). **Never writes
to `operator_entities`.** `reconcile_against_live()` compares the two and writes a report
row to `wt_operator_projection_reconciliation_reports`, classifying every wallet as
identical / missing / unsupported / conflicting / duplicate / ambiguous.

### 5. `scripts/backfill_attribution_evidence.py` — historical backfill
Implements exactly the X40.0 Phase 1 classification (Group A/B/C), verbatim — not
re-derived. `--apply`/dry-run modes; idempotent (`NOT EXISTS`-guarded on
`(subject_wallet, event_type, method)`); every row tagged `reconstructed=1` with an
explicit source and confidence tier (`HIGH` for the two full ledgers, `METHOD_ONLY` for
Group A, `PARTIAL`/`SUBSTANTIAL` for Groups B/C per the X40.0 distinction). Ran against the
live database: **695 rows backfilled**, matching X40.0's investigation exactly (8+4+7=19
gap-fill rows, plus 606 fingerprint-decision rows and 70 approval-audit rows). Re-running
confirmed idempotent — 0 new rows on second run.

## Validation gate results

| Gate | Result | Evidence |
|---|---|---|
| **A** — system behaves identically with new tables empty/broken | **PASS** | Forced `record_merge_event`/`determine_merge_rule` to raise unconditionally; `operation_store_v2.persist()` still produced the identical DISCOVER action and `wt_ops_v2` row, only logging the swallowed failure |
| **B** — every future confirmation produces exactly one AttributionEvidence event | **PASS** | Live test via `promote_to_confirmed` on a scratch DB copy produced exactly 1 `attribution_evidence` row (`event_type=MANUAL_APPROVAL`) |
| **C** — every future merge produces exactly one immutable merge event | **PASS by construction** — verified the write fires once per `persist()` call in the DISCOVER/MERGE/EXPAND branch structure; `operation_merge_ledger` is currently empty on the real DB because no new merge has fired since rollout (historical merge backfill was deliberately NOT attempted — see Known Limitations) |
| **D** — shadow Operator projection is deterministic and reproducible | **PASS** | Ran `operator_projection_shadow.run()` twice against the live DB; identical/missing/unsupported counts (58/7/2) matched exactly both times |
| **E** — historical reconstructed events are clearly marked | **PASS** | All 695 backfilled rows have non-null `reconstruction_source` and `reconstruction_confidence`; 0 rows found with either field missing |
| **F** — removing the new writers returns the system to original behaviour | **PASS (after a fix)** — see below | |

### Gate F: a real failure was found and fixed during this implementation

The first Gate F test (forcing `_record_attribution_evidence` to raise before it even
reached `record_evidence()`'s internal try/except) **failed**: `promote_to_confirmed`
propagated the exception to its caller, even though the underlying `wt_confirmed_treasuries`
write had already committed successfully. This meant a sufficiently catastrophic ledger-side
problem (e.g., a missing module at import time) could have surfaced as a visible error to
callers, even though production data integrity was never actually at risk.

**Fix applied**: every dual-write call site now goes through a local wrapper function that
catches all exceptions itself, rather than depending solely on `record_evidence()`'s
internal handling. Re-running the same test with the hardened wrapper: `promote_to_confirmed`
returned its normal `{"ok": True, ...}` result, with the simulated failure logged but fully
swallowed. This was verified for both the `treasury_bank.py` attribution path and the
`operation_store_v2.py` merge-ledger path (forcing a real `DISCOVER` action through
`persist()` with both merge-ledger functions raising unconditionally — the `wt_ops_v2` row
was still created correctly).

## A genuine production finding surfaced by the shadow reconciliation (not a bug in this implementation)

Running `reconcile_against_live()` against the real database found **2 wallets
(`EUe75Hf8Q5EqeqZKG5hSnA58GsFoKCRYVSxEdDNc6AcB`, `Fu2MupMNTV13tHUJ4NHLVJ5NCN8uGer2sdaRaDNa8o2x`)
with a `CONFIRMED` `LAUNCH_CHAIN` decision in `wt_treasury_fingerprint_decisions`
(`webhook_status='PENDING'`, never advanced to `WEBHOOKED` or `FAILED`) but no corresponding
row in `wt_confirmed_treasuries`, and no `REVERTED` decision explaining the absence.** This
was traced directly (not assumed): the decision-ledger rows exist with full evidence
(`subprov`, `creator`, chain reason), timestamped `1782575879`/`1782575882`, but
`wt_confirmed_treasuries` has never contained either wallet. This is either a lost write
(a crash or exception between the `wt_confirmed_treasuries` INSERT and the
`_log_decision()` call inside `auto_confirm_from_launch_chain`, which would mean the decision
log fired but the confirmed-treasuries write didn't — though the code shows the confirmed-
treasuries write happens first, which makes a partial write less likely) or an out-of-band
manual deletion that wasn't logged as a `REVERTED` decision. **This finding is reported here
as evidence the shadow reconciliation works as designed — it is not something this
implementation fixes**, since altering production treasury-confirmation state is explicitly
out of scope for a shadow-mode implementation task. Recommend a separate, human-reviewed
follow-up to determine which of the two explanations is correct before deciding whether to
re-confirm these wallets.

## Known limitations / explicitly deferred (not attempted in this pass)

- **Historical `operation_merge_ledger` backfill was not attempted.** X40.0 flagged this as
  "mechanically plausible but unattempted" — re-deriving which of the four deterministic
  merge rules fired for every existing multi-treasury `wt_ops_v2` row would require
  replaying `_find_hard_merge_target`'s logic against historical `wt_ops_v2_wallets`
  snapshots, which do not currently exist as a time series (only current state is stored).
  This is a real gap for Gate C's "explainability" goal at the historical level; going-forward
  merges are fully captured, but past merges remain unexplained by this session's work.
- **The shadow Operator projection does not implement the full X40.0 Phase 4 lifecycle**
  (PROPOSED/SUPPORTED/CONFIRMED/REJECTED/SPLIT/SUPERSEDED). It reuses today's single
  hardcoded `WATCHTOWER_OPERATOR_ID` constant deliberately, per the frozen architectural
  rule that this session must not redesign Operator semantics — implementing the full
  lifecycle state machine is future work, out of scope for "shadow mode only."
- **`operator_entities` itself was not modified in any way** — confirmed by re-reading the
  file diff; only new tables were created and only existing call sites were extended with
  after-the-fact dual-writes.
- The full test suite (`pytest tests/`, excluding 3 pre-existing collection errors unrelated
  to this change — missing `analyze_creator_wallet`/`main` top-level modules) completed with
  2 failures in `tests/test_x26_5_1_attribution_health_window_integrity.py`. **Verified
  pre-existing and unrelated**: re-ran the same file with `git stash` (this session's changes
  fully reverted) and got the identical 2 failures (HTML-content assertions against
  `discovery.html` unrelated to treasury/operation logic), then restored the stash and
  re-confirmed the same 2 failures persist unchanged. The scoped subset directly touching
  treasury/operation/scheduler code (76 tests) passed cleanly both before and after.

## Explicit non-goals honored

No existing table was deleted, renamed, or had its schema altered. `_find_hard_merge_target()`
was read but not modified — verified via `git diff` showing zero changes to that function's
body. Fingerprint thresholds (`STRICT_TRANSFER_PCT`, `STRICT_MIN_OUT_SOL`,
`STRICT_MIN_RECIPIENTS`, `STALE_DAYS`) are untouched. `auto_evaluate()`'s "never
auto-promote" behavior is untouched. All new tables use `CREATE TABLE IF NOT EXISTS` /
`CREATE INDEX IF NOT EXISTS`, idempotent by construction.

## Answer to the stated success criterion

The existing WATCHTOWER platform continues behaving identically — verified both by targeted
Gate F fault-injection tests (proving removal/failure of the new writers is a no-op on
existing behavior) and by the existing test suite passing unchanged. The observable
difference is exactly as specified: every future treasury attribution and Operation merge
decision now produces exactly one immutable evidence record (Gates B/C), historical
provenance has been backfilled and explicitly marked as reconstructed where applicable
(Gate E), and a shadow Operator projection can now be independently regenerated from
evidence and compared against the live table (Gate D) — which, on its very first run
against real production data, surfaced a genuine, previously-invisible discrepancy (the two
orphaned `LAUNCH_CHAIN` confirmations above) that the shadow architecture was specifically
designed to make explainable.
