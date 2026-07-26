# X65.5 — Phase 3: Define Inclusion Criteria

Determines which evidence contributes to bucket membership, and
classifies each candidate characteristic as Mandatory, Optional, or
Confidence-increasing. No single characteristic combination is
required to be complete — per the task's constraint, the classifier
must tolerate incomplete evidence, since (per X65.4) most of the
population never gets full candidate-watch coverage in the first
place.

## Candidate evidence, classified

| Evidence | Source (existing, read-only) | Classification | Rationale |
|---|---|---|---|
| Fresh creator | `creator_identity == FRESH_CREATOR` (`src/ops/creator_identity.py`) | **Mandatory** | The provisioning model (X65.4) is specifically about *newly funded* creators — a serial-deployer creator is a structurally different pattern this bucket should not claim |
| Wrap-close provisioning behaviour observed at all | `token_analysis`/`wt_watchtower_launches` — the creator was funded via the WSOL wrap→close mechanism (this project's own confirmed "WATCHTOWER wrap-close pattern") | **Mandatory** | This is the one irreducible structural signature that defines the mechanism itself — without it, there is no basis to call this WATCHTOWER-shaped at all, regardless of any other evidence |
| Single creator funded by the provisioning wallet | `wt_candidate_websocket_watches` — the specific wrap wallet that funded this creator has never been observed funding a second candidate | **Optional, confidence-increasing** | Confirmed true in 100% of checked cases (X65.4 Phase 3A) but data coverage is partial (only ~half of confirmed launches had a recorded `wrap_wallet`) — absence of data must not be treated as a failed check |
| Provisioning wallet not reused across subprovs | `wt_candidate_websocket_watches` — the wrap wallet has never been observed under a different `subprov_wallet` | **Optional, confidence-increasing** | Same reasoning as above — a real, validated characteristic (X65.4 Phase 3A), but coverage-limited, never gating |
| Observable SubProv fan-out (subprov produced >1 distinct wrap-close/candidate destination) | `wt_candidate_websocket_watches`, grouped by `subprov_wallet` | **Optional, confidence-increasing** | This is the single strongest confidence signal (X65.4: 88.4% of confirmed launches show it) but explicitly **not mandatory** — X65.4 Phase 3 found 1 of 43 confirmed launches with only 1 recorded recipient, and a further 3 with zero recorded data (table absent, not contradicting) purely due to coverage gaps, not because the launch wasn't really WATCHTOWER |
| Treasury lineage (any resolution status) | `src/ops/treasury_resolution.py` | **Optional, confidence-increasing (Treasury confidence tier, not gating)** | Per the task's explicit requirement — treasury status must never gate entry; it only refines the confidence tier shown (Phase 4) |
| Topology classification (`FAN_OUT`/`LINEAR`/`UNKNOWN`) | `src/ops/funding_topology.py` | **Optional, informational only** | Per X65.4, the existing topology field is known to be an incomplete/sometimes-incorrect description of the same fan-out this bucket independently checks via `wt_candidate_websocket_watches` — it is displayed (Phase 5) but never used as a bucket-membership signal, to avoid inheriting X65.4's identified gap into this new bucket's own logic |

## Why exactly two criteria are Mandatory

Membership requires only:
1. `creator_identity == FRESH_CREATOR`, and
2. direct evidence of the wrap-close provisioning mechanism (a
   `wrap_close_signature`/wrap-wallet-mediated funding event reaching
   this creator — the mechanism itself, not any measure of its scale).

This is deliberately the smallest possible mandatory set. Every other
characteristic (single-use, not-reused, fan-out breadth, treasury
resolution, topology label) is Optional/confidence-increasing — a
launch can be a member of the bucket with **zero** of them present
(e.g., a fresh creator funded via wrap-close whose subprov has no
`wt_candidate_websocket_watches` history at all, per X65.4 Phase 5's
finding that this is the common case for most of the Discovery
population) — it simply carries the lowest confidence tier rather than
being excluded.

## Confidence scoring (not a gate — a displayed tier)

| Confidence tier | Criteria met |
|---|---|
| **High** | Mandatory criteria + observable fan-out (>1 recipient) + single-use/not-reused provisioning wallet confirmed |
| **Medium** | Mandatory criteria + at least one optional confidence-increasing signal (fan-out observed, OR single-use confirmed, but not all) |
| **Baseline** | Mandatory criteria only — wrap-close + fresh creator confirmed, no further corroborating evidence available (most common case per X65.4 Phase 5's coverage-gap finding) |

Treasury confidence (Confirmed/Probable/New/Unknown, Phase 4) is
reported **alongside**, not blended into, this operational-confidence
tier — the two are orthogonal axes (how confident are we this is
WATCHTOWER-shaped, vs. how confident are we about which treasury it
traces to), and conflating them would reintroduce the exact
fragmentation-by-treasury-status problem this task exists to fix.
