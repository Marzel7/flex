# X65.2 — Phase 3: Funding Lineage Audit

Read-only trace of creator funding capture → walkback queue →
sub-provider discovery → treasury walkback → funding edge creation →
topology derivation, for the same 12 `UNRESOLVED` launches, using only
existing persisted evidence.

## Stage-by-stage evidence

| Stage | Table checked | Result for all 12 |
|---|---|---|
| Creator funding capture (direct funder → creator) | `creator_funders` | **0 rows** for every one of the 12 creator addresses |
| Walkback queue | `wt_walkback_queue` | **12/12 present, `status='complete'`, `walkback_class='FULL_WALKBACK'`**, each with a resolved `funder_wallet` matching `wt_attribution_outcomes.terminal_entity` exactly |
| Sub-provider discovery | `wt_active_subprov_sessions`, `wt_discovered_subprovs` | **0 rows** for any of the 12 `funder_wallet` values |
| Treasury walkback | `wt_confirmed_treasuries` (via `funder_wallet`) | **0 rows** (no candidate to check — no subprov to walk further from) |
| Funding edge creation | `wt_provisioning_edges` | **0 rows** referencing any of the 12 funder wallets |
| Topology derivation | `operational_intelligence.py` topology field | `UNKNOWN` for all 12 (matches Phase 1) |

## Key finding: the walkback queue itself completed successfully

All 12 mints have a `wt_walkback_queue` row with `status='complete']` —
the walkback process did not fail, error out, or get stuck. It ran to
completion, correctly determined the creator's direct funder, checked
that funder against every downstream lineage table available to it, and
correctly recorded `INSUFFICIENT_EVIDENCE` in `wt_attribution_outcomes`
because the funder itself has no further indexed lineage. This is the
walkback system behaving exactly as designed when handed a funder
wallet the rest of the system has never seen before — not a lineage-side
bug.

## The one CREATE-anchor recovery: `9Mn2t7yX2TmSSM...`

One of the 12 (`9Mn2t7yX2TmSSMEsQqDnFvcmNAGVCPhjevXpKfqgpump`) has
`wt_walkback_queue.path_state = 'CREATE_ANCHORED'`,
`create_anchor_audit_state = 'VALID'`, and a real
`create_anchor_signature`
(`2LCE1k1DZLYQx4YHmp9k4Q5pCTNQCBWsvChkVud4hqrf3ZjUJCwsByxpbLdJWdePLxLnpLsbhDp9PSxwsfJRcHCz`)
— meaning the walkback queue's own CREATE-anchor-recovery mechanism
(a Phase-6-relevant capability distinct from the live listener's
birth-time path) independently found and validated a CREATE signature
for this mint. That signature was never written back to
`token_analysis.create_tx_signature` (still NULL, per Phase 2) or to
`wt_create_event_ledger` (still 0 rows) — the two systems do not share
this evidence. The other 11 have no `create_anchor_signature` recovered
by the queue at all (`path_state`/`create_anchor_audit_state` empty),
consistent with Phase 2's finding that their CREATE evidence is
missing at the source, not merely un-cross-referenced.

## Classification (per the task's required categories)

| Category | Launches | Count |
|---|---|---|
| **NEVER_CAPTURED** (creator's direct funder → creator transfer itself was never captured into `creator_funders`, independent of whether the *funder's own* upstream lineage was ever findable) | All 12 | 12 |
| — sub-classification: **funder resolved via walkback despite `creator_funders` gap, but funder is itself un-indexed anywhere further upstream** | All 12 | 12 |
| — sub-classification: **one launch (`9Mn2t7yX...`) has a recovered-but-unpropagated CREATE anchor** | 9Mn2t7yX2TmSSM... | 1 |

All 12 land in **NEVER_CAPTURED** at the `creator_funders` stage
specifically — the realtime creator-funding extractor
(`realtime_creator_funding_extractor.py`, triggered per `docs/CLAUDE.md`
at `pumpfun_curve_listener.py:1728` on birth) never ran for these
creators, consistent with Phase 2's finding that the birth-time
`create_tx_signature` was never durably set (10 of 12) or the birth
event itself falls outside retained-log visibility (2 of 12) — in
either case, the trigger condition for the creator-funding extractor
(a successfully processed birth event) was not met.

This is not contradicted by the walkback queue's success: the queue's
`FULL_WALKBACK` path independently derives a `funder_wallet` through a
different, RPC-capable mechanism than the realtime extractor (per its
own `create_anchor_recovery` capability, seen in the one case above) —
so the *lineage-outcome* table (`wt_attribution_outcomes`) can be
fully populated even when the *realtime funding-capture* table
(`creator_funders`) is empty. These are two different, only loosely
coupled systems, and this decoupling is precisely why Discovery's UI
(which surfaces topology from the funding-edge/subprov tables, not from
`wt_attribution_outcomes` alone) shows these 12 as `UNKNOWN` topology
despite the walkback table showing "complete."

## No launch is CAPTURED_NOT_INDEXED, INDEXED_NOT_LINKED, or LINKED_NOT_CONSUMED

None of the 12 show any row in `creator_funders`,
`funder_incoming_transfers`, `wt_provisioning_edges`, or
`wt_active_subprov_sessions` for their funder wallets — so there is no
stage at which evidence was captured-but-unindexed, indexed-but-unlinked,
or linked-but-unconsumed. The gap is at the very first stage
(capture), not a downstream processing failure.
