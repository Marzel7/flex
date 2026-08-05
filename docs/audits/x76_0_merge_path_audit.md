# X76.0 Phase 1 — Current Merge Path Audit

## Objective

Document every code path in this codebase that can cause two Operator
Identities (or their presentation-layer proxies, Investigation Population
family cards) to be treated as the same thing, and classify each as
**evidence-driven**, **implementation-driven**, or **projection-driven**.

## Merge path 1 — `OperatorIdentityGovernanceService.merge()`

- **File**: `src/ops/operator_identity_governance.py:502`
- **Entry point**: `POST /api/ops/operators/<operator_id>/identity/merge`
  (`src/ops/operator_routes.py:197`) — the ONLY caller found anywhere in
  the codebase (`grep -rn "\.merge(" src/`).
- **Evidence**: requires explicit `analyst`, `reason`, `evidence_revision`
  metadata (`_metadata()` raises `GovernanceError` if any are missing).
  No automatic invocation exists — a human must submit this request.
- **Merge trigger**: an explicit `destination_operator_id` +
  `source_operator_ids` list supplied by the caller. Nothing about wallet
  overlap, treasury overlap, or projection state triggers this
  automatically.
- **Wallet overlap / treasury overlap / review state**: not consulted at
  all — the caller has already decided the merge; this function only
  executes it (appends immutable `operator_identity_events`, marks source
  operators `MERGED`, reassigns `operator_entities`/
  `operator_identity_assets`/`operator_launch_membership` rows to the
  destination).
- **Projection dependence**: none. Operates directly on `operators` /
  `operator_identity_state` / `operator_entities` rows by ID.
- **Classification**: **EVIDENCE-DRIVEN** (in the sense that it is a
  deliberate human decision backed by recorded reasoning — the "evidence"
  is the analyst's own judgment, captured immutably). This is the correct,
  authoritative merge path per the task's Phase 7 requirement and is
  **not modified by X76.0**.

## Merge path 2 — `EmergingOperatorService._compose()` canonical-family absorption

- **File**: `src/ops/emerging_operator_service.py`, previously lines
  585–619 (pre-X76.0).
- **Entry point**: runs automatically inside `_compose()`, which is called
  by `list()` / `_list_uncached()` on every population-list computation —
  no human involvement, no explicit request to merge anything. This is a
  **presentation-layer** merge (it produces one combined family card for
  display), not a write to `operators`/`operator_identity_state` — but it
  does mutate the in-memory family dicts that Discovery, Emerging
  Operators, and the Operation Profile page all read as if they were
  ground truth, and it sets `promoted_to_operation_id` on the absorbed
  population, which **suppresses that population from
  analyst-facing `surface_families`** (line ~148, pre-existing) — i.e. it
  has real, if presentation-only, consequences.
- **Evidence (pre-X76.0)**: none beyond a boolean "does any wallet
  intersect."
- **Merge trigger (pre-X76.0)**: `canonical_entities.intersection(
  family.get("member_wallets") or ())` — `canonical_entities` was built
  from the canonical operator's `member_wallets` **and** `treasuries`
  fields, but the candidate population side was checked using
  `member_wallets` **only**, never `treasuries`. A single shared wallet
  recorded in the right field is sufficient to trigger absorption.
- **Wallet overlap**: yes — the ONLY signal used pre-X76.0.
- **Operator overlap**: not applicable (only one operator's canonical
  card is compared against each population at a time).
- **Treasury overlap**: only checked on the canonical side, not the
  candidate side — this asymmetry is exactly what let B48k/Dv34
  (X75.3A's example) escape absorption purely because its shared wallet
  (`EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5`) happened to be recorded
  under `treasuries` on both sides.
- **Review state**: not checked at all pre-X76.0 — a REJECTED wallet
  could theoretically have triggered an absorption if it had been the
  overlapping wallet and happened to sit in the checked field.
- **Projection dependence**: total — whether a merge occurred depended
  entirely on which dict key a wallet address happened to be stored
  under, not on any evidence about whether the two populations actually
  represent the same operator.
- **Classification (pre-X76.0)**: **PROJECTION-DRIVEN / IMPLEMENTATION-
  DRIVEN**. This is the defect X75.3A flagged and X76.0 fixes.

## Merge path 3 — anything else?

Searched for additional merge-shaped logic:

- `grep -rn "merge\|absorb" src/ops/*.py src/discovery/*.py src/core/*.py`
  — no other automatic identity-combination logic found. `operator_
  identity_governance.py`'s `split()` is the inverse operation (also
  human-invoked, also not modified). `treasury_bank.py` and
  `treasury_review_workspace.py` never combine two operator identities —
  they only ever attach a single treasury to a single operator via
  `OperatorIdentityGovernanceService.expand()` (additive, `INSERT OR
  IGNORE`, never merges two existing operators).
- `src/discovery/*.py` (X75.0/X75.2/X75.3A additions): confirmed via
  `tests/test_x76_0_canonical_merge_safety.py::TestDiscoveryNeverMerges`
  to contain zero write statements and zero `.merge()` calls — Discovery
  can propose (surface a Potential Expansion) but structurally cannot
  merge anything itself.

## Summary

| Path | File | Trigger | Classification | Modified by X76.0? |
|---|---|---|---|---|
| `OperatorIdentityGovernanceService.merge()` | `operator_identity_governance.py:502` | explicit human API call | Evidence-driven | No — already correct |
| `EmergingOperatorService._compose()` absorption | `emerging_operator_service.py` | wallet-set intersection (asymmetric) | Projection-driven (defect) | **Yes — gated behind `evaluate_merge()`** |
| Discovery (`src/discovery/*.py`) | — | — | N/A (no merge capability) | No — confirmed structurally incapable |

## Fix

See `src/ops/canonical_merge_contract.py` for the canonical merge contract
(Phase 2–4) and its application inside `_compose()` (Phase 3/4
implementation). The absorption trigger is replaced with
`evaluate_merge(canonical, candidate, rejected_wallets=...)`, which
requires: (a) wallet overlap computed symmetrically across every
wallet-role field on both sides, (b) at least two independent identity
signals (matching funding mechanism, matching topology, structural
depth), and (c) no REJECTED Treasury Review decision on any overlapping
wallet. A merge decision is never based on overlap alone, and the full
satisfied/unsatisfied criteria are attached to every evaluated candidate
population (`merge_evaluations`) for analyst/debug visibility (Phase 8).
