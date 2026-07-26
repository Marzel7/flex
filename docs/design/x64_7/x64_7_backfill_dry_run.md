# X64.7 — Phase 10: Historical Ledger Backfill — Dry Run

## Method

Zero-RPC scan of `token_analysis.create_tx_signature` (live DB) — the
richest available trusted source — validating every non-NULL signature
with the production `valid_signature()` function, grouping by signature
to detect cross-mint reuse, and classifying each candidate.

## Result

```
Distinct valid signatures found: 1,280,050
BACKFILL_SAFE (single mint per signature): 1,280,050
BACKFILL_SIGNATURE_CONFLICT (signature reused across mints): 0
Already present in wt_create_event_ledger: 0
BACKFILL_DUPLICATE_IDENTICAL: 0
BACKFILL_CREATOR_CONFLICT: not evaluated (no existing ledger rows to conflict with)
BACKFILL_INVALID_SIGNATURE: 0 (only non-NULL signatures that already
  passed valid_signature() were counted as candidates in this scan)
```

**Every candidate signature classifies `BACKFILL_SAFE`.** No signature
conflicts, no duplicate-identical collisions (the ledger is currently
empty in production), no invalid signatures were found among the
non-NULL population.

## Critical finding: this backfill recovers 0 of the current 33
unresolved rows

Cross-referenced the 33 currently-`MINT_NOT_FOUND` mints (per X64.7
Phase 1's re-derived live count) against `token_analysis.
create_tx_signature`: **0 of 33 have a non-NULL signature there** — this
is the exact same finding X64.6 already established for the same
underlying reason (Phase 2 of that task's own exhaustive source search).
The 1.28M backfill candidates are drawn entirely from **already-
successful** mints (the ones whose `_update_token_entry_with_creator` or
`_insert_bonding_curve_token` write already succeeded, pre-X64.7) — this
backfill has real value for populating the canonical ledger with
historical data so future audits/tools have a complete record, but it
does not, and structurally cannot, help the currently-stuck population,
because that population's defining property is having no signature
anywhere to backfill from in the first place.

## Decision: deferred, not executed in this task

Per explicit direction, the 1.28M-row backfill was **not executed** as
part of X64.7. Rationale, as agreed:
1. It provides zero recovery benefit for the currently unresolved 33
   rows — confirmed directly above.
2. It is a large one-time operational write (~1.28M rows) that deserves
   its own maintenance window, verification pass, and rollback plan
   rather than being bundled into investigative/pipeline-fix work.
3. X64.7's primary objective — understanding and fixing where CREATE
   events are lost, and ensuring the ledger captures them going forward
   — is complete without this backfill; the backfill is a historical
   convenience, not a correctness requirement for the fix itself.
4. The schema and backfill logic are already validated in dry-run mode
   (safe, zero conflicts) — nothing about executing it later requires
   re-deriving this analysis.

**Recommended follow-up**: a dedicated task (e.g. "X64.8 — Historical
CREATE Ledger Backfill") to execute this 1.28M-row import as its own
scoped operational migration, after the live-write path (Phase 7/11 of
this task) has been observed writing correctly in production for a
period of time.

## Re-run anchor reconciliation using the ledger as first source — result

Since the backfill was not executed, the ledger currently contains only
whatever rows the live `handle_birth` path writes going forward (see
`x64_7_shadow_validation.md`) plus, incidentally, any rows a developer
manually inserts for testing. Re-running `anchor_reconciliation.
dry_run_report()` immediately after this task's code changes but before
any new birth events had been observed showed the ledger empty and the
resolver correctly falling through to the existing widened-source search
(Priority 3-5) unchanged — confirmed no regression to X64.5/X64.6's
existing recovery numbers from adding the ledger as Priority 1 with zero
rows in it.
