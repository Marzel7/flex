# X65.2 — Executive Summary: Missing CREATE Event & Funding Lineage Coverage Investigation

Read-only investigation into why the 12 `UNRESOLVED` launches from
X65.1's cohort (`QUICK_BIRTH_MIGRATION → FRESH_CREATOR → UNKNOWN
topology → UNASSIGNED`) have no persisted CREATE-event or
funding-lineage evidence. No attribution logic, treasury confirmation
rules, behaviour classification, or operation assignment was changed.
No recovery was performed. All facts measured live against production,
2026-07-21.

## Cohort reproduced exactly

19 total (7 `KNOWN_TREASURY`, 12 `UNRESOLVED`) — exact match to X65.1,
no drift.

## Root cause found: a genuine, single, precisely-located bug

`_update_token_entry_with_creator()`
(`src/core/pumpfun_curve_listener.py:7933`, `UPDATE` statement at line
7963) unconditionally overwrites `token_analysis.create_tx_signature`
during migration-time creator re-extraction, with no `COALESCE` guard —
unlike the birth-time write path (`_insert_bonding_curve_token()`,
line 5732), which correctly preserves existing values. When the
migration-time RPC re-validation of the CREATE transaction doesn't
independently reconfirm it (any RPC miss, rate limit, or shape
mismatch), the caller passes `create_tx_signature=None`, which this
`UPDATE` then writes straight over an already-correct, birth-time-
captured signature — destroying it. This is confirmed with high
confidence for 10 of 12 launches (direct log evidence: birth event
logged successfully, signature now NULL) and medium confidence for the
remaining 2 (same symptom, but the birth-time log evidence has already
rotated out of the retained log window).

## Not a detection miss

The CREATE event **was** observed for at least 10 of the 12 launches —
proven directly by the `[PUMPPORTAL] 🟢 Birth]` log line and by
`pf_ws_creator`/`earliest_tx_creator` being correctly populated for all
12. This is a **persistence** bug (a later write destroying a
correctly-captured earlier value), not a listener/WS coverage gap, not
a reconciliation-timing gap, and not an unsupported transaction shape.

## Scope-correcting finding: the bug is far broader than 12 launches

A live check found **87% of all migrated tokens in the last 7 days
(14,410 / 16,589)** have `create_tx_signature IS NULL` with the same
signature (creator populated, signature missing) — including **all 7**
of this cohort's already-*resolved* launches. What makes these
particular 12 launches visibly stuck is not the missing signature
itself (nearly universal) but that their funder wallets, independently,
have zero presence in any sub-provisioner/treasury lineage table
(confirmed via `wt_active_subprov_sessions`, `wt_provisioning_edges`,
`wt_discovered_subprovs` — all 0 rows for all 12). The walkback system
(`wt_walkback_queue`) ran to completion and correctly reported
`INSUFFICIENT_EVIDENCE` for all 12 — this is the lineage system working
as designed against genuinely un-indexed wallets, not a lineage-side
failure.

## One partial recovery already exists, unused

`9Mn2t7yX2TmSSMEsQqDnFvcmNAGVCPhjevXpKfqgpump`'s
`wt_walkback_queue` row already holds an independently-recovered,
audit-validated `create_anchor_signature` — proof the recovery
mechanism works — but it was never propagated to `token_analysis` or
`wt_create_event_ledger`. All 12 are classified `PARTIALLY_RECOVERABLE`
(the CREATE signature is very likely recoverable via a fresh mint-keyed
RPC lookup; the funding-lineage gap is a separate, independent
limitation that recovering the signature alone would not close). No
recovery was attempted in this task.

## Fix designed (not implemented)

Smallest change: add a `COALESCE` to the one destructive `UPDATE`
statement, matching the discipline already correctly used in the
birth-time write path. One-line SQL change, no schema change, no new
table, no new pipeline, no change to the existing strict on-chain
validation logic (which remains exactly as strict for writing *new*
values — it just can no longer erase a good existing one).

## Impact if the fix ships

- Prevents recurrence of this exact failure mode for all future
  migrations, not just this behaviour cohort — given the 87% system-
  wide null rate, the benefit extends broadly.
- Does **not** by itself increase `KNOWN_TREASURY` resolution counts —
  that requires separately expanding sub-provisioner/treasury detection
  coverage to reach these specific funder wallets (out of this task's
  scope).
- Does **not** retroactively fix the 12 launches already in this
  cohort — only a separate, explicit recovery action (not performed
  here) could do that.

## Deliverables

`docs/design/x65_2/` — `x65_2_missing_cohort.md`,
`x65_2_create_capture.md`, `x65_2_lineage_audit.md`,
`x65_2_pipeline_coverage.md`, `x65_2_root_causes.md`,
`x65_2_recoverability.md`, `x65_2_fix_design.md`, `x65_2_impact.md`,
this summary. No code was changed; no data was recovered or
attributed; all 12 launches remain exactly as `UNRESOLVED`/
`__UNASSIGNED__` as before this investigation began.
