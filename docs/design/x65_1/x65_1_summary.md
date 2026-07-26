# X65.1 — Executive Summary: Sub-Provider Treasury Resolution for Unassigned Quick-Birth Launches

Resolved the creator → sub-provisioner → treasury lineage for the
Discovery cohort at `QUICK_BIRTH_MIGRATION → FRESH_CREATOR → UNKNOWN
topology → UNKNOWN funding → UNASSIGNED`, without any new detection
logic, any automatic treasury confirmation, or any change to Behaviour
Cohort/Creator Identity classification. All facts measured live against
the production database, 2026-07-21.

## Original cohort count

**19 launches** — reproduced exactly (not just approximately) via
`canonical_behaviour=QUICK_BIRTH_MIGRATION` + `creator_identity=FRESH_CREATOR`
+ `topology=UNKNOWN` + `operation_id=None`, confirming no UI/API
filtering discrepancy existed.

## Number of direct creator funders resolved

**19 / 19 (100%)** — but this was already true before this task began:
every launch already had a `wt_attribution_outcomes` row with a
`terminal_entity` (the creator's direct funder), persisted by the
existing walkback process. This task's real contribution starts one
hop further upstream.

## Number of sub-providers identified

**7** classified `CONFIRMED_SUBPROV` (complete `wt_active_subprov_sessions`
evidence: a real funding signature, amount, timestamp, and populated
treasury). **0** `PROBABLE_SUBPROV`. **12** `UNRESOLVED` — these 12
funder wallets have zero presence in any funding-lineage table checked
(also zero persisted CREATE signature for their launches anywhere,
including the X64.7 canonical CREATE-event ledger), indicating a
genuine coverage gap in prior indexing rather than an inconclusive
prior check.

## Number of upstream treasury wallets identified

**3 distinct treasury wallets** across the 7 resolved launches:
`DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` (3 launches),
`9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` (3 launches),
`Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` (1 launch).

## Known treasuries matched

**All 3** were already present and confirmed in `wt_confirmed_treasuries`
(confirmed 2026-06-11, 2026-06-14, and 2026-07-21 respectively — all
predating this task) — zero new treasury confirmations were performed
or needed. All 3 are already linked to a confirmed (non-WATCHTOWER)
operation in `wt_ops_v2_wallets`.

## Unknown treasury candidates discovered

**0** for this specific 19-launch cohort. Every upstream candidate
found was already confirmed; every launch with no candidate at all
(the 12 `UNRESOLVED`) had zero evidence to produce a candidate from.
The `UNKNOWN_TREASURY_CANDIDATE` classification path is fully
implemented and tested but was not exercised by this particular
cohort's data.

## New confirmed operation assignments

**0 new confirmations** — this task never writes to
`wt_confirmed_treasuries` or `wt_ops_v2_wallets`. What changed is that
**7 launches now surface their already-confirmed operation** via a
cross-reference join that previously wasn't being checked (the actual
root cause identified in Phase 2: `funding_topology.py`'s existing
lineage derivation never checks whether a
`wt_attribution_outcomes.terminal_entity` is itself a
`wt_active_subprov_sessions.subprov_wallet`).

## Remaining unassigned launches

**12 of 19** remain `UNRESOLVED` and correctly stay `__UNASSIGNED__` in
Discovery's existing Operation Attribution stage — no launch was
force-assigned or guessed into an operation.

## Unresolved reasons

All 12 share the same underlying reason: the creator's direct funder
wallet (`terminal_entity`) has zero rows in
`wt_active_subprov_sessions`, `wt_discovered_subprovs`,
`wt_webhook_hits`, `funder_incoming_transfers`, `creator_receivers`,
`sol_transfers`, `transfer_index`, or `wt_provisioning_edges` — combined
with the fact that none of the 19 cohort launches have a persisted
CREATE signature anywhere (`token_analysis.create_tx_signature` is NULL
for all 19; `wt_create_event_ledger` has zero rows for all 19), this
strongly suggests these are launches that fell through a gap in prior
funding-lineage/CREATE-event indexing, not launches the system
deliberately examined and gave up on.

## Performance impact

New API endpoint (`GET /api/ops-v2/treasury-resolution`) measured at
**19ms** for a single-mint request — a bounded (≤200 mints/request),
synchronous, database-read-only endpoint with zero RPC and zero
network I/O. No change to any existing endpoint's performance
(X65.0's SWR-cached `/api/ops-v2/operational-intelligence` is
untouched). 23 new tests run in 0.14s.

## Whether any candidates require human treasury review

**No new candidates require review from this specific 19-launch
cohort** (zero `UNKNOWN_TREASURY_CANDIDATE` results). However, one
existing discrepancy is surfaced for human attention, not silently
resolved: this project's own persistent operating memory ("Hello
program operator linkage") independently established, via a separate
on-chain evidence path, that all 3 treasury wallets reached by this
cohort's resolved launches share a downstream Hello-service payment
recipient — evidence they belong to the same real-world operator. Yet
`wt_ops_v2_wallets` currently links them to **3 distinct operation
UUIDs**. This task does not merge, reroot, or otherwise act on this
discrepancy (per the explicit "do not automatically confirm or reroot
treasury identities" constraint, read as extending to operation
records) — it is flagged here as a candidate for human review, separate
from and in addition to the primary treasury-resolution work this task
performed.

## Success criteria — final status

| Criterion | Status |
|---|---|
| Every launch in the cohort receives an explicit treasury-resolution result | ✅ 19/19, verified live |
| The creator → sub-provider → treasury lineage is preserved | ✅ full evidence path retained per launch |
| Confirmed treasuries are distinguished from unknown candidates | ✅ `KNOWN_TREASURY` requires an existing `wt_confirmed_treasuries` row; nothing auto-promotes |
| Unknown wallets are not promoted automatically | ✅ zero write statements anywhere in the new module |
| Known operation attribution occurs only through existing confirmed treasury relationships | ✅ every `operation_id` traced back through a confirmed treasury match |
| The ~19 launches resolve into measurable groups (Known Treasury / Unknown Treasury Candidate / No Sub-Provider / Unresolved) | ✅ 7 Known Treasury, 0 Unknown Candidate, 0 No Sub-Provider, 12 Unresolved |
| The Discovery interface exposes the treasury address and evidence path instead of stopping at generic Unknown/Unassigned labels | ✅ new Treasury Resolution panel, live-tested API, evidence retained per row |

## Deliverables produced

`docs/design/x65_1/` — `x65_1_cohort_reproduction.md`,
`x65_1_evidence_audit.md`, `x65_1_creator_subprov_resolution.md`,
`x65_1_treasury_walkback.md`, `x65_1_known_treasury_matches.md`,
`x65_1_unknown_treasury_candidates.md`, `x65_1_resolution_model.md`,
`x65_1_ui_validation.md`, `x65_1_cohort_results.md`,
`x65_1_regression_and_safety.md`, this summary.

`src/ops/treasury_resolution.py` (new module, 23 tests in
`tests/test_x65_1_treasury_resolution.py`), a new API route
(`src/core/operation_dashboard_routes.py`), and a new Discovery UI panel
(`templates/discovery.html`) — deployed live (`watchtower_api`
restarted, pid 64992).
