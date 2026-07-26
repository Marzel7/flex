# X65.10 — Implement Candidate-Watch Topology Classification (Deliverables)

Implements X65.8's design. Live, deployed. All constraints honored:
Campaign classification, treasury resolution, attribution logic,
detection logic, walkback logic, and existing Discovery UI were not
modified.

## Implementation summary

Added a new, independent evidence function to `src/ops/funding_topology.py`
that reads `wt_candidate_websocket_watches` (the same table
`src/ops/campaign_classification.py` independently reads, via its own
separate query — no cross-module call) and consults it **before** the
existing `wt_provisioning_edges`-based Fan-Out/Linear rule, which
remains an unconditional fallback. No schema change, no new detection,
no RPC.

## Modified functions

| Function | File | Change |
|---|---|---|
| `_subprov_candidate_watch_counts()` | `src/ops/funding_topology.py` | **New.** Batched `{subprov_wallet: distinct candidate_wallet count}` reader over `wt_candidate_websocket_watches`. |
| `classify_topology_for_launch()` | `src/ops/funding_topology.py` | Added `candidate_watch_counts` parameter; inserted a new check at step 5 (before the existing `sibling_counts` check), falling through unchanged to the existing logic when no candidate-watch data exists for the subprov. |
| `build_topology_classification()` | `src/ops/funding_topology.py` | Calls `_subprov_candidate_watch_counts(ops_conn)` once per build (alongside the existing `_subprov_sibling_counts()` call) and threads the result through to every `classify_topology_for_launch()` call. |

## SQL query (new)

```sql
SELECT subprov_wallet, COUNT(DISTINCT candidate_wallet) AS n
FROM wt_candidate_websocket_watches
WHERE subprov_wallet IS NOT NULL
GROUP BY subprov_wallet
```

Batched — one query per `build_topology_classification()` call, not
per launch. Confirmed via `EXPLAIN QUERY PLAN` to use the existing
`ix_wc_subprov_time` index (`SEARCH ... USING INDEX
ix_wc_subprov_time (subprov_wallet>?)`), no new index required.

## Regression results

| Suite | Result |
|---|---|
| `test_x65_10_topology_candidate_watch.py` (new, 20 tests) | 20/20 pass |
| `test_x29_1_operational_topology_intelligence.py` (existing Topology) | 18/18 pass, unmodified |
| `test_x29_1_1_operational_topology_ui_migration.py` | 10/10 pass |
| `test_x29_1_2_swr_cache.py` | 9/9 pass |
| `test_x29_1_3_outcome_grouped_launches.py` | 20/20 pass |
| `test_x65_7_campaign_classification.py` (Campaign, untouched) | 35/35 pass |
| `test_x65_0_exclusive_behaviour.py` (Behaviour Cohort) | 11/11 pass |
| `test_x65_1_treasury_resolution.py` (Treasury) | 23/23 pass |
| `test_x64_8_creator_identity.py` (Creator Identity) | 14/14 pass |
| `test_x65_9_create_signature_preservation.py` | 7/7 pass |
| **Total** | **175/175 pass** |

New tests specifically cover: candidate-watch fan-out priority, the
provisioning-edge fallback path (unchanged), the walkback fallback path
(unchanged), Mesh/Multi-Level priority preserved, an explicit
independence guard (`funding_topology.py` contains no
`campaign_classification` import, no `campaign` field reference, and
`classify_topology_for_launch()`'s signature has no campaign-related
parameter), conservation, and an explicit "no forced WATCHTOWER →
FAN_OUT" test proving a single-recipient subprov stays `LINEAR`
regardless of any other context.

## Performance measurements

| Measurement | Value |
|---|---|
| New query wall-clock time (isolated) | 2.24s |
| `build_topology_classification()` before this change (baseline, stash-compared) | 7.02s |
| `build_topology_classification()` after this change (live) | 8.92s |
| Delta | +1.90s, consistent with the isolated query cost |
| Query plan | Uses existing `ix_wc_subprov_time` index — no table scan |
| Per-launch queries introduced | 0 (one batched query for the whole build) |
| New RPC calls | 0 |

This cost is paid once per Discovery cache refresh (the existing SWR
layer, X29.1.2), not once per page load — no measurable per-request
Discovery slowdown for end users.

## Conservation results

| Check | Before | After |
|---|---|---|
| `conserved` (Topology) | `True` | `True` |
| Total launches | 7,283 | 7,284 (natural population growth between measurements) |
| `MULTI_LEVEL_FAN_OUT + MESH + FAN_OUT + LINEAR + UNKNOWN` | = total | = total |

Live, post-deployment: `433 + 0 + 622 + 913 + 5316 = 7284` — exactly
matches `total_launches`. `campaign_conserved` also remains `True`
(288 + 6374 + 622 = 7284), confirming Campaign was genuinely untouched.

## Live verification

Replayed the 22 confirmed WATCHTOWER launches with a resolvable
`wt_attribution_outcomes` row (of 43 total; 21 fall outside the
365-day window, a pre-existing boundary unrelated to this task):

- **Before**: 0 of 22 correctly classified `FAN_OUT` by Topology (20
  `UNKNOWN`, 1 `LINEAR` in direct contradiction with 25 observed
  recipients, 1 correctly `UNKNOWN` with genuinely no evidence).
- **After**: **22 of 22 (100%)** now correctly classify `FAN_OUT`, each
  with `derived_from` explicitly showing
  `wt_candidate_websocket_watches_count=<n>` as the evidence source.

Per the task's explicit framing, this is **not** reported as "all
WATCHTOWER launches are now FAN_OUT" — live data for the broader
288-launch WATCHTOWER-campaign population (the full `window=all`
population, not just the 43 originally-confirmed cascade launches)
shows:

| Topology | Count (of 288 WATCHTOWER-campaign launches) |
|---|---|
| `MULTI_LEVEL_FAN_OUT` | 175 |
| `FAN_OUT` | 95 |
| `LINEAR` | 13 |
| `UNKNOWN` | 5 |

The 13 `LINEAR` and 5 `UNKNOWN` launches were individually inspected:
every one has a `derived_from` value showing genuine evidence
(`wt_candidate_websocket_watches_count=1`, or a legitimate fallback
such as `wt_provisioning_edges_sibling_count=1` /
`subprov_present_no_sibling_evidence` for subprovs the new evidence
source has no data on) — none are forced or defaulted. Topology
reached these results independently, using only its own observed
evidence, exactly as required.

Campaign's own output was independently re-checked post-deployment and
is byte-for-byte unaffected: `campaign_summary` counts and per-launch
`campaign`/`campaign_confidence`/`campaign_evidence` fields are
identical before and after this deployment (confirmed via the same
live API response fields).

## Deployment summary

- `src/ops/funding_topology.py` modified (additive only — one new
  function, one new parameter, one new decision branch).
- No changes to `src/ops/campaign_classification.py`,
  `src/ops/treasury_resolution.py`, any attribution/detection/walkback
  module, or `templates/discovery.html`.
- `watchtower_api` restarted to deploy; confirmed `RUNNING` and serving
  correctly via live API checks immediately after restart.
- `watchtower_listener` was **not** restarted (not required — this
  change is entirely in the read-side classifier, not the live
  detection/write path).
