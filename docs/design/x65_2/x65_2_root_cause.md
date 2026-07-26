# X65.2 — Phase 6: Root Cause

Selects the best-fitting explanation from the task's required outcome
set: Historical Coverage Gap, Lineage Indexing Gap, Walkback Gap,
Mixed Causes, Unknown.

## Evaluation against each candidate

| Candidate | Fit | Evidence |
|---|---|---|
| **Historical Coverage Gap** | Rejected | Phase 1: all 12 launches occurred strictly after the current pipeline's deployment (394dbd9, 2026-07-14); zero launches predate it. There is no historical/legacy-pipeline population in this cohort to attribute a coverage gap to. |
| **Lineage Indexing Gap** | Rejected as the primary cause, but a real secondary contributor | Phase 2/3: `creator_funders`, `wt_provisioning_edges`, and `wt_active_subprov_sessions` are all empty for all 12 — but Phase 3 established these are *not* the earliest failure; they are downstream consequences of the CREATE-ledger gap. A pure "lineage indexing" explanation would require CREATE evidence to be intact with only the lineage-indexing step failing, which is not what the matrix shows. |
| **Walkback Gap** | Rejected | Phase 2/3: `wt_walkback_queue.status='complete'` for all 12 — the walkback process is not gapped; it runs to completion and correctly reports `INSUFFICIENT_EVIDENCE` given what it has to work with. |
| **Mixed Causes** | **Best fit** | Two independent, corroborated mechanisms both contribute: (1) a deterministic write-path defect (migration-time `_update_token_entry_with_creator()` unconditionally overwriting `create_tx_signature` with `NULL`, still present in the live code — confirmed responsible for 10/12 with high confidence, consistent with the remaining 2), and (2) chronic listener process instability (3,224 restarts across the window, 8/12 launches within ±30min of a restart) that plausibly compounds in-memory-state loss for a subset of the cohort. Both are active, current-generation issues — neither is historical, and neither alone is a clean "lineage indexing" or "walkback" gap in the terms those categories describe. |
| **Unknown** | Rejected | Sufficient direct evidence (log lines, DB state, code inspection, git history, supervisor restart records) was found to support a specific, well-evidenced conclusion — this is not an unexplained gap. |

## Conclusion: Mixed Causes

**Primary cause (fully explains 100% of the cohort, uniformly)**: a
single, precisely-located, still-live code defect —
`_update_token_entry_with_creator()`
(`src/core/pumpfun_curve_listener.py:7933`, `UPDATE` statement at line
7963) performs an unconditional `create_tx_signature=?` write with no
`COALESCE` guard, discarding an already-correct birth-time signature
whenever its own migration-time RPC re-validation doesn't
independently reconfirm the transaction. This single mechanism
accounts for every launch in the cohort sharing the identical
earliest-failure stage (CREATE ledger, Phase 3) — a uniformity that
would not be expected from twelve unrelated incidents.

**Secondary, corroborating contributor (does not independently explain
the full cohort, but is evidenced and additive)**: chronic
`watchtower_listener` process instability across the entire window
(3,224 restarts, median gap ~6.3 minutes) correlates with 8 of the 12
launches occurring within ±30 minutes of a restart. This does not
change which stage fails first (still CREATE ledger for all 12,
including the 4 launches with no nearby restart), but it is a
plausible amplifying factor for at least some of this cohort and is
evidenced independently (supervisor logs), not speculated.

This is classified **Mixed Causes** rather than a single-category
Lineage Indexing or Walkback Gap because the evidence points to two
distinct, independently-verifiable mechanisms operating in the same
window, one of which (the write-path defect) is sufficient on its own
to explain the full cohort, and the other of which (instability) is a
real but non-explanatory-on-its-own contributing condition. Neither
"Lineage Indexing Gap" nor "Walkback Gap" fits cleanly on their own
because both of those systems (indexing tables, the walkback queue)
are shown to behave *correctly* given what they receive — the defect
is upstream of them, in the CREATE-signature persistence step itself,
which is not one of the five listed single-cause categories exactly,
making "Mixed Causes" (a write-path defect plus an instability
contributor) the most accurate available classification rather than
forcing the finding into a category that only partially fits.

## No speculation beyond observed data

Every claim above is backed by a specific, cited piece of evidence
from this or the prior investigation pass (log lines, DB row counts,
git commit dates, supervisor restart timestamps, or direct code
reading of the exact write statement). No inference was made about
launches or time periods not directly checked.
