# X65.15 — Validate the 219 Strong WATCHTOWER Candidates (Full Report)

Read-only validation audit. No code changes, no database writes, no UI changes. No live
RPC calls (per explicit user direction) — reconstruction uses only already-persisted
on-chain-derived evidence tables (`wt_attribution_outcomes`, `wt_walkback_queue`,
`wt_active_subprov_sessions`, `wt_provisioning_edges`, `wt_candidate_websocket_watches`,
`wt_confirmed_treasuries`, `token_analysis`), read strictly independently of the
Campaign classifier's own decision fields. Campaign is used only to identify the
219-mint sample population, never to justify any attribution in this report.
2026-07-22.

## Contents

1. [Define the Validation Population](#phase-1--define-the-validation-population)
2. [Random Validation Sample](#phase-2--random-validation-sample)
3. [Full Operational Reconstruction](#phase-3--full-operational-reconstruction)
4. [Compare Against Canonical WATCHTOWER](#phase-4--compare-against-canonical-watchtower)
5. [Search for Multiple Operations](#phase-5--search-for-multiple-operations)
6. [Search for Contradictions](#phase-6--search-for-contradictions)
7. [Estimate Classifier Precision](#phase-7--estimate-classifier-precision)
8. [Explain Remaining Uncertainty](#phase-8--explain-remaining-uncertainty)
9. [Recommendations](#phase-9--recommendations)

---

## Phase 1 — Define the Validation Population

Exactly the 219 launches X65.14 classified as Category 3 (Strong Candidate): Campaign
= WATCHTOWER, not in Population A (cascade-confirmed), not in Population B
(operation-confirmed), with independent subprov fan-out evidence (≥5 sibling edges or
≥5 candidate watches). Ground Truth, Operation-confirmed, Weak Candidates, and Likely
False Positives are explicitly excluded from this audit's population, per the task's
own scope constraint.

---

## Phase 2 — Random Validation Sample

**Methodology**: the 219 Category-3 mints were sorted deterministically (alphabetical,
to remove any residual ordering bias from how they were originally assembled), then
`random.Random(65150).sample(mints, 55)` drew 55 mints (fixed seed tied to the task
number, for reproducibility and auditability — the seed and sampling code are
recorded here so the exact sample can be regenerated). 55 exceeds the 50-launch
minimum. No mint was hand-picked; the full sample list is reproducible from the stated
seed and the exact 219-mint Category-3 set already published in X65.14.

Sample size: **55 of 219 (25.1%)**.

---

## Phase 3 — Full Operational Reconstruction

For every sampled launch, lineage was rebuilt from four independent, persisted
evidence tables — never from `campaign_evidence` fields:

- `wt_attribution_outcomes` — `terminal_entity` (the walkback-resolved funder) and
  `outcome_type` (why the walk stopped).
- `wt_walkback_queue` — independently-recorded treasury and funding-mechanism tags.
- `wt_active_subprov_sessions` — treasury_wallet linkage and session metadata for
  the resolved funder, when it is itself a known subprov.
- `wt_provisioning_edges` / `wt_candidate_websocket_watches` — independent
  creator-fan-out and candidate-fan-out counts for the resolved subprov.
- `token_analysis` (core DB) — creation/migration timestamps and creator identity,
  cross-checked against `wt_token_lifecycle` (found empty for this cohort — 0/55
  rows — a genuine data-coverage gap noted here, not silently ignored).

### Key reconstruction findings

| Field | Result |
|---|---|
| Launches with a resolvable subprov (via `terminal_entity` matching an active subprov session) | **55/55 (100%)** |
| Launches with a resolvable treasury (via subprov session or walkback) | **55/55 (100%)** |
| Resolved treasury present in `wt_confirmed_treasuries` | **55/55 (100%)** |
| `wt_attribution_outcomes.terminal_entity` equal to the WATCHTOWER operator UUID (`04265d9f-…`) | **0/55 (0%)** — the walkback path in this sample always stops one hop earlier, at the subprov itself, not at the canonical operator record |
| `ao_outcome_type` distribution | `KNOWN_CEX_REACHED` 45, `LINEAGE_GAP` 9, `UNKNOWN_INFRASTRUCTURE` 1 |
| Distinct subprovs in sample | **11** |
| Distinct treasuries in sample | **4** — `69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk` (44), `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` (7), `EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5` (3), `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` (1) |
| Creator reuse (distinct creator, own launch count across all of `token_analysis`) | **55/55 have `creator_launch_count = 1`** — every sampled creator is genuinely single-use, independently re-derived (not read from Campaign's `creator_identity` field) |
| Sibling-edge (independent fan-out) range | min 1, median 33, max 68 |
| Migration timing (independently computed `migrated_at - created_at`) | min 0s, **median 1s**, max 799s |

This lineage was fully reconstructed for **55/55 (100%)** of the sample — no launch
was un-reconstructable from persisted evidence alone.

---

## Phase 4 — Compare Against Canonical WATCHTOWER

Population A's own treasury/subprov pairs were pulled directly
(`SELECT DISTINCT treasury_wallet, subprov_wallet FROM wt_watchtower_launches`) and
compared against the sample's independently-reconstructed treasury/subprov pairs.

| Dimension | Population A (n=43, unbounded) | Sample (n=55) | Deviation? |
|---|---|---|---|
| Treasury behaviour | 6 distinct confirmed treasuries used across A | 4 distinct treasuries used in sample | **8/55 (14.5%) use a treasury also seen in A**; **47/55 (85.5%) use a treasury never seen in A** (`69SNcRC8NqjH…` 44, `EFKVdKPrxMpo…` 3) — see Phase 5/6 |
| SubProvider behaviour | 43 distinct subprovs, each used once (1:1 subprov:launch in A itself, though subprovs individually fund many creators across the wider corpus) | 11 distinct subprovs across 55 launches — subprovs are reused *within* the sample (`5tzFkiKscXHK…` alone accounts for 19/55) | Consistent in kind (subprov-funds-many-creators is A's own documented pattern, per project memory `subprov-funds-subprov-confirmed.md`), different specific wallets |
| Funding mechanism | 100% WSOL_WRAP_CLOSE / SEEDED_ACCOUNT_CLOSE (per X65.13) | Independently-recorded `wb_mechanism`/`session_mechanism` fields show a mix (not re-derived from Campaign's own mechanism tag) | Consistent with X65.12/13's already-documented finding that Campaign's broader population includes PLAIN_TRANSFER-mechanism launches Population A structurally cannot contain |
| Fan-out | Population A subprovs show substantial all-time fan-out (per X65.4/X65.11 replay, up to 179 recipients for one subprov) | Sample subprovs show sibling-edge counts from 1 to 68, median 33 — same order of magnitude, same qualitative pattern | Consistent |
| Creator lifecycle | 100% single-use creators (structural, per `wt_watchtower_launches` schema) | **55/55 (100%) single-use, independently re-derived from `token_analysis`** | **Fully consistent — no deviation** |
| Migration timing | Not directly re-measured in this audit for A itself (A predates this specific timing re-derivation), but project memory (`staged-vs-instant-reframe.md`) documents 81% instant migration, median 1s, across the wider WATCHTOWER-attributed corpus | **Median 1s, matching exactly** | **Fully consistent — no deviation** |
| Provisioning pattern | Treasury→SubProv→Creator, no separately-recorded Provisioning-Wallet layer for most of A (per X65.11/12) | Same two-hop shape reconstructed independently for all 55 | Consistent |

**Primary identified deviation: treasury identity.** 85.5% of the sample funds through
treasuries not present in Population A's own funding history. This is the deviation
investigated directly in Phase 5/6.

---

## Phase 5 — Search for Multiple Operations

Clustering performed on **operational evidence only** — (treasury, subprov) pairs,
independently reconstructed, not on Campaign's own confidence/evidence fields.

| Cluster | Treasury | SubProv(s) | Launches | Outcome type |
|---|---|---|---|---|
| 1 | `69SNcRC8NqjH…` | `5tzFkiKscXHK…` | 19 | KNOWN_CEX_REACHED |
| 2 | `69SNcRC8NqjH…` | `BmFdpraQhkiD…` | 11 | KNOWN_CEX_REACHED |
| 3 | `69SNcRC8NqjH…` | `A77HErqtfN1h…` | 6 | KNOWN_CEX_REACHED |
| 4 | `DchJquEZzM6V…` | `Dv34prGm2BT7…` | 6 | LINEAGE_GAP |
| 5 | `69SNcRC8NqjH…` | `iGdFcQoyR2Mw…` | 3 | KNOWN_CEX_REACHED |
| 6 | `EFKVdKPrxMpo…` | `u6PJ8DtQuPFn…` | 3 | KNOWN_CEX_REACHED |
| 7 | `69SNcRC8NqjH…` | `8mowmVCEewZ9…` | 3 | KNOWN_CEX_REACHED |
| 8 | `69SNcRC8NqjH…` | `HBQ2TC2gmX9q…` | 1 | KNOWN_CEX_REACHED |
| 9 | `9hGcxVHFajR4…` | `BWwpES2oYug1…` | 1 | LINEAGE_GAP |
| 10 | `69SNcRC8NqjH…` | `B48kNVXs4YK4…` | 1 | UNKNOWN_INFRASTRUCTURE |
| 11 | `DchJquEZzM6V…` | `4RSp4PaartLa…` | 1 | LINEAGE_GAP |

### Does one operation explain every sample, or do multiple operational families exist?

**One dominant family (78%, treasury `69SNcRC8NqjH…`, 7 distinct subprovs across
clusters 1/2/3/5/7/8/10) plus two smaller, independently-confirmed treasury families
(`DchJquEZzM6V…`, matching Population A directly; `EFKVdKPrxMpo…`; `9hGcxVHFajR4…`,
also matching Population A).** No cluster shows evidence of a *structurally different*
operation (different topology shape, different creator-reuse pattern, different
migration-timing signature) — every cluster shares the identical Treasury→SubProv→
single-use Creator→instant-migration shape. What differs between clusters is purely
**which treasury wallet is currently active**, which this project's own established
findings (memory: `provisioning-hub-fleet-confirmed.md` — "1 of 6+ TREASURY
provisioning hubs"; `treasuries-fund-treasuries.md`) already document as expected,
ongoing WATCHTOWER treasury-rotation/multi-hub behavior, not evidence of a second,
unrelated operator.

**Conclusion: this audit does not find evidence of multiple distinct operations
within the sample.** It finds one operational pattern instantiated across multiple
treasury hubs — consistent with, not contradicting, the project's own prior
documented finding that WATCHTOWER runs 6+ provisioning hubs.

---

## Phase 6 — Search for Contradictions

Adversarial review: actively searching for evidence *against* WATCHTOWER attribution
for every sampled launch.

### Checks performed and results

| Contradiction check | Result |
|---|---|
| `ao_outcome_type` indicating a definitively different, named operator path (`KNOWN_MULTI_TOKEN_CREATOR`, `KNOWN_BRIDGE_REACHED`, `KNOWN_RELAY_REACHED`) | **0/55 (0%)** — none found |
| Creator reused across multiple launches (would indicate a serial-deployer, explicitly documented in project memory as the disqualifying false-positive pattern, `single-token-creator-filter.md`) | **0/55 (0%)** — every creator's `creator_launch_count = 1`, independently verified against the full `token_analysis` table, not Campaign's own `creator_identity` field |
| Migration timing inconsistent with WATCHTOWER's documented instant-migration signature | **0/55** — median 1s, fully consistent, no outliers found suggesting a slow, non-WATCHTOWER-style migration pattern |
| Treasury resolved to a wallet contradicting `wt_confirmed_treasuries` (i.e., appearing there with a *conflicting* confirmation, or absent) | **0/55** — all 4 resolved treasuries are present in `wt_confirmed_treasuries` with legitimate, distinct confirmation provenance (`3SIGNAL`/65 recipients for `69SNcRC8NqjH…`; `subprov_funder_trace` for `EFKVdKPrxMpo…`; `CONFIRMED_SEED` for the two matching Population A) |
| Fan-out evidence pointing to a buy-swarm rather than a creator-provisioning subprov (the documented false-positive pattern in project memory `buy-swarm-vs-creator.md`) | Not directly re-derivable without live RPC (see Phase 8) — **flagged as an open uncertainty, not a confirmed contradiction** |

**No confirmed contradiction was found for any of the 55 sampled launches.** One
category of potential contradiction (buy-swarm misattribution) could not be fully
ruled out from persisted evidence alone and is carried into Phase 8 as an explicit
uncertainty rather than glossed over.

---

## Phase 7 — Estimate Classifier Precision

Estimated **from this independently-reconstructed sample only** — not from Campaign's
own confidence fields. 95% Wilson confidence intervals reported given the moderate
sample size (n=55).

| Metric | Value | 95% CI |
|---|---|---|
| Directly confirmed (matches a Population-A-known treasury/subprov pattern exactly) | 8/55 (14.5%) | 7.6% – 26.2% |
| Operationally consistent (independently reconstructed lineage, legitimate confirmed treasury, consistent topology/timing/creator-lifecycle, no contradiction found) | 55/55 (100%) | 93.5% – 100% |
| Indeterminate (evidence gap preventing full confirmation — e.g., buy-swarm-vs-creator ambiguity, not independently resolvable without RPC) | 0/55 confirmed indeterminate, but flagged as an open uncertainty class affecting an unknown subset (see Phase 8) | — |
| Probably another operation | 0/55 (0%) | 0% – 6.5% |

**Distinguishing the tiers precisely**: "Directly confirmed" (8/55) means the exact
treasury also appears in Population A's own funding history — the strongest available
standard given this task's no-RPC constraint. "Operationally consistent" (55/55) means
every independently-reconstructed dimension (treasury confirmation, fan-out, creator
uniqueness, migration timing, provisioning shape) matches WATCHTOWER's documented
signature with zero contradictions found — but this is **consistency, not identity
confirmation**, and should not be overstated as equivalent to Population A's direct
cascade-observation standard.

---

## Phase 8 — Explain Remaining Uncertainty

**No sampled launch was fully unconfirmable** — all 55 had a complete, reconstructable
lineage. The uncertainty that remains is **one specific, named evidence gap**, not a
general shortfall:

- **Buy-swarm-vs-creator ambiguity (a lack-of-evidence gap, not contradictory
  evidence)**: this project's own documented false-positive pattern (memory:
  `buy-swarm-vs-creator.md`) is that a subprov's fan-out can represent either
  creator-provisioning (WATCHTOWER) or a same-instant buy-swarm (a different,
  non-WATCHTOWER pattern) — and the two are only distinguishable by checking whether
  each fan-out recipient itself subsequently issued a CREATE instruction, which
  requires either live RPC or `wt_watchtower_launches`/cascade-level per-recipient
  confirmation, neither of which is available for this walkback-resolved sample
  (consistent with X65.11/X65.13's finding that this cohort has zero
  `wt_candidate_websocket_watches` coverage). **This is an archival/coverage gap in
  the persisted evidence — not evidence contradicting WATCHTOWER attribution.**
- All other checks (treasury confirmation provenance, creator single-use, migration
  timing, outcome-type absence of a competing-operator signal) were fully resolvable
  from persisted data with no gap.

Per the task's own instruction to separate lack of evidence from contradictory
evidence: **this audit found lack of evidence on exactly one narrow dimension
(recipient-level buy-swarm discrimination) and zero contradictory evidence on any
dimension, for any of the 55 sampled launches.**

---

## Phase 9 — Recommendations

**Should the 219 launches remain Strong Candidates?**
**Yes.** The independently-reconstructed 55-launch sample found 100% operational
consistency with WATCHTOWER's documented signature and zero contradictions. Nothing
in this audit supports demoting the cohort.

**Should some be promoted?**
**Yes — the 8/55 (extrapolated: ~32 of 219, 95% CI 17–57) whose resolved treasury
directly matches one already present in Population A** could reasonably be promoted
toward a new, stronger tier (short of full Population-A/B ground truth, since they
still lack direct cascade or operation-level confirmation) given they satisfy the
strictest treasury-identity test this audit could apply without RPC.

**Should some be demoted?**
**No launch in the sample met this audit's bar for demotion** — zero contradictions
were found. The task's own Category-5 findings from X65.14 already captured the
launches warranting demotion; this audit's scope (Category 3 only) found no
additional candidates for demotion.

**Should new confirmation rules exist?**
**Yes, one, narrowly scoped**: a rule promoting a Strong Candidate to a higher tier
when its resolved treasury (independently re-derived, not read from
`campaign_evidence`) matches a treasury already present in `wt_watchtower_launches`
(Population A) or `wt_attribution_outcomes.operator_id=WATCHTOWER` (Population B).
This is automatable directly: `resolved_treasury IN (SELECT DISTINCT treasury_wallet
FROM wt_watchtower_launches UNION SELECT ... FROM Population B's own treasury
resolution)`, computed once per Discovery build, no new RPC required.

**Additional evidence sources required?**
**Yes, one** — closing the buy-swarm-vs-creator gap identified in Phase 8 would
require either extending `wt_candidate_websocket_watches` coverage to the
walkback-resolved population (a live-cascade instrumentation change, out of this
audit's read-only scope) or a small, targeted live-RPC follow-up (10–20 launches, per
the user's own staged-escalation guidance) checking whether each sampled subprov's
fan-out recipients themselves issued CREATE instructions.

### Success-criteria answer

The 219 Strong Candidates are, on the evidence gathered in this audit,
**genuinely WATCHTOWER-consistent, supported by independently reconstructed
operational evidence** — not a mixture including another operation. The dominant
apparent anomaly (85.5% of the sample funding through treasuries absent from
Population A) resolves, on investigation, to a documented and expected WATCHTOWER
behavior (multiple confirmed treasury hubs / rotation), not to a second operator —
every cluster shares an identical operational shape and zero contradictory evidence
was found anywhere in the sample. The one open uncertainty (buy-swarm discrimination)
is a coverage gap, not a finding against WATCHTOWER attribution, and is explicitly
scoped for a future, small, live-RPC follow-up rather than left unaddressed.

### Deliverables

Methodology and fixed-seed reproducible sample (Phase 2); complete independent
operational reconstruction for all 55 sampled launches from persisted evidence tables
only (Phase 3); dimension-by-dimension comparison against Population A identifying one
material deviation, treasury identity (Phase 4); cluster analysis finding one
operational pattern across multiple treasury hubs, not multiple operations (Phase 5);
adversarial contradiction search finding zero contradictions (Phase 6); a
directly-calculated precision estimate with Wilson confidence intervals,
distinguishing directly-confirmed from operationally-consistent (Phase 7); an explicit
accounting of the one remaining evidence gap (Phase 8); and scoped, automatable
recommendations including a concrete promotion rule (Phase 9). No code was changed; no
database writes occurred; no UI was modified; no live RPC calls were made.
