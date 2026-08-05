"""X76.0 Canonical Merge Safety & Identity Boundary Protection.

PHASE 1 AUDIT SUMMARY (see docs/audits/x76_0_merge_path_audit.md for the
full writeup):

Two merge-shaped mechanisms exist in this codebase:

1. `OperatorIdentityGovernanceService.merge()` (src/ops/operator_identity_
   governance.py:502) -- EVIDENCE-DRIVEN in the sense that it is a
   deliberate, human-invoked action (requires analyst/reason/evidence_
   revision metadata) reached ONLY through its own explicit HTTP endpoint
   (POST /api/ops/operators/<id>/identity/merge, operator_routes.py:197).
   No automated caller exists anywhere in the codebase. This is the
   correct, authoritative merge path per this task's Phase 7 requirement
   ("Operator Identity Governance may merge") and is NOT modified by this
   module -- it already satisfies the boundary the task asks for.

2. `EmergingOperatorService._compose()`'s canonical-family "absorption"
   block (src/ops/emerging_operator_service.py:585-619) -- IMPLEMENTATION-
   /PROJECTION-DRIVEN. It runs automatically on every population-list
   computation (no human involved), and its sole trigger is a non-empty
   set intersection between a canonical operator's `member_wallets` +
   `treasuries` fields and a candidate population's `member_wallets` field
   alone (never checking the candidate's own `treasuries` field). X75.3A
   proved this asymmetry is why B48k/Dv34 currently escapes absorption
   into WATCHTOWER: EFKV (their one shared wallet) happens to be recorded
   in `treasuries` on both sides, and the candidate-side check never looks
   there. A population whose shared wallet happened to be recorded in
   `member_wallets` instead would be absorbed on that ONE wallet alone --
   no evidence threshold, no minimum overlap, no review-state check. THIS
   is the mechanism this module makes safe.

This module defines ONE canonical merge contract and applies it as a gate
in front of mechanism #2's absorption trigger. It does not touch mechanism
#1 (already correct) and does not touch attribution, reconciliation, or
resolver logic -- it only decides whether an automatic ABSORPTION (a
presentation-layer merge of two family cards) is permitted to proceed.

CANONICAL MERGE CONTRACT

A merge (of any kind -- absorption or governance merge) must never be
triggered by any SINGLE one of the following alone:
  - a shared wallet
  - a shared treasury
  - shared infrastructure (CEX/relay/bridge membership)
  - a shared creator
  - projection/storage layout (which field a shared wallet happens to be
    recorded under)

A merge is permitted only when INDEPENDENT identity evidence -- evidence
that does not depend on which storage field a wallet is filed under --
establishes that two populations/identities represent the same
controlling operator. Concretely, this module requires ALL of:
  (a) wallet overlap computed across every wallet-role field on BOTH
      sides symmetrically (not just one side's member_wallets), so the
      overlap itself is never a coincidence of field naming; AND
  (b) at least TWO independent identity-class evidence signals (not just
      one), drawn from: matching funding mechanism, matching dominant
      topology / provisioning structure, review-history corroboration (a
      CONFIRMED, not REJECTED, treasury among the overlap), and
      structural depth (subprovisioner fan-out, walkback-confirmed
      lineage) -- mirroring the IDENTITY vs SUPPORTING evidence
      distinction already established in src/ops/operator_model.py's
      EVIDENCE_CATALOGUE, applied here to the absorption decision
      specifically; AND
  (c) no REJECTED treasury review decision exists for any overlapping
      wallet (a human already ruled on this wallet's identity -- an
      automatic mechanism must never override that).

This module performs no detection, no RPC, no writes, and does not alter
attribution_outcome.py, disposition_resolver.py, operation_attribution.py,
or evidence_reconciliation.py in any way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_WALLET_FIELDS = ("member_wallets", "treasuries", "member_treasuries", "client_wallets", "provisioning_clients")

_IDENTITY_SIGNAL_MIN_COUNT = 2


def _wallets_of(entity: dict[str, Any]) -> frozenset[str]:
    """Symmetric wallet extraction -- every wallet-role field, on whichever
    side it's called for. This is what closes the field-naming asymmetry:
    both the canonical side and the candidate side are read the same way."""
    wallets: set[str] = set()
    for field_name in _WALLET_FIELDS:
        value = entity.get(field_name)
        if isinstance(value, (list, tuple, set)):
            wallets.update(value)
    return frozenset(wallets)


@dataclass(frozen=True, slots=True)
class MergeCriterion:
    name: str
    satisfied: bool
    detail: str


@dataclass(frozen=True, slots=True)
class MergeDecision:
    allowed: bool
    canonical_id: str
    candidate_id: str
    overlapping_wallets: frozenset[str]
    satisfied_criteria: tuple[MergeCriterion, ...]
    unsatisfied_criteria: tuple[MergeCriterion, ...]
    reason: str

    def explain(self) -> dict[str, Any]:
        """PHASE 8 -- merge explainability. Returns a plain dict suitable
        for logging, an API response, or an analyst-facing debug view."""
        return {
            "allowed": self.allowed,
            "canonical_id": self.canonical_id,
            "candidate_id": self.candidate_id,
            "overlapping_wallets": sorted(self.overlapping_wallets),
            "satisfied_criteria": [
                {"name": c.name, "detail": c.detail} for c in self.satisfied_criteria
            ],
            "unsatisfied_criteria": [
                {"name": c.name, "detail": c.detail} for c in self.unsatisfied_criteria
            ],
            "reason": self.reason,
        }


def evaluate_merge(
    canonical: dict[str, Any],
    candidate: dict[str, Any],
    *,
    rejected_wallets: frozenset[str] = frozenset(),
) -> MergeDecision:
    """Evaluate whether `candidate` (an investigation population / family
    dict, EmergingOperatorService shape) may be absorbed into `canonical`
    (an operator's canonical family card, same shape). Pure function, no
    I/O -- `rejected_wallets` must be supplied by the caller (typically
    from wt_treasury_review, same source X75.0's own rejected-treasury
    exclusion uses).

    Returns a MergeDecision with `allowed=False` whenever fewer than 2
    independent identity signals are present, or any overlapping wallet
    has a REJECTED review decision -- regardless of how large the wallet
    overlap is. A merge decision is never based on overlap SIZE alone."""
    canonical_wallets = _wallets_of(canonical)
    candidate_wallets = _wallets_of(candidate)
    overlap = canonical_wallets & candidate_wallets

    canonical_id = str(canonical.get("family_id") or canonical.get("operator_id") or "unknown")
    candidate_id = str(candidate.get("family_id") or "unknown")

    if not overlap:
        return MergeDecision(
            allowed=False, canonical_id=canonical_id, candidate_id=candidate_id,
            overlapping_wallets=frozenset(), satisfied_criteria=(), unsatisfied_criteria=(
                MergeCriterion("wallet_overlap", False, "No overlapping wallet found on either side."),
            ),
            reason="No wallet overlap -- nothing to evaluate.",
        )

    # (c) REJECTED-review hard stop -- checked first, unconditional.
    rejected_overlap = overlap & rejected_wallets
    if rejected_overlap:
        criterion = MergeCriterion(
            "no_rejected_review", False,
            f"{len(rejected_overlap)} overlapping wallet(s) were REJECTED in Treasury Review: "
            f"{sorted(rejected_overlap)}",
        )
        return MergeDecision(
            allowed=False, canonical_id=canonical_id, candidate_id=candidate_id,
            overlapping_wallets=overlap, satisfied_criteria=(), unsatisfied_criteria=(criterion,),
            reason="A human analyst already rejected one of the overlapping wallets -- "
                   "an automatic mechanism must never override that decision.",
        )

    satisfied: list[MergeCriterion] = [
        MergeCriterion("wallet_overlap", True, f"{len(overlap)} overlapping wallet(s): {sorted(overlap)}"),
        MergeCriterion("no_rejected_review", True, "No overlapping wallet has a REJECTED review decision."),
    ]
    unsatisfied: list[MergeCriterion] = []

    # (b) Independent identity-class signals -- each is checked
    # independently of which field the overlap wallet was stored in.
    canonical_mechanisms = set(canonical.get("funding_mechanisms") or ())
    candidate_mechanisms = set(candidate.get("funding_mechanisms") or ())
    shared_mechanism = canonical_mechanisms & candidate_mechanisms
    if shared_mechanism:
        satisfied.append(MergeCriterion(
            "matching_funding_mechanism", True, f"Shared mechanism(s): {sorted(shared_mechanism)}",
        ))
    else:
        unsatisfied.append(MergeCriterion(
            "matching_funding_mechanism", False, "No shared funding mechanism recorded.",
        ))

    canonical_topology = str(canonical.get("dominant_topology") or "").strip().lower()
    candidate_topology = str(candidate.get("dominant_topology") or "").strip().lower()
    topology_known = bool(canonical_topology) and bool(candidate_topology) \
        and "incomplete" not in candidate_topology and "unknown" not in candidate_topology
    if topology_known and canonical_topology == candidate_topology:
        satisfied.append(MergeCriterion(
            "matching_topology", True, f"Both report topology: {candidate_topology!r}",
        ))
    else:
        unsatisfied.append(MergeCriterion(
            "matching_topology", False, "Dominant topology does not match or is not yet known.",
        ))

    candidate_subprov_fanout = bool(candidate.get("walkback_descendant_count")) \
        or bool(candidate.get("subprovisioners")) or bool(candidate.get("sub_provisioners"))
    if candidate_subprov_fanout:
        satisfied.append(MergeCriterion(
            "structural_depth", True, "Candidate has recorded subprovisioner fan-out / walkback lineage.",
        ))
    else:
        unsatisfied.append(MergeCriterion(
            "structural_depth", False, "No subprovisioner fan-out or walkback-confirmed lineage recorded.",
        ))

    identity_signal_count = sum(1 for c in satisfied if c.name not in ("wallet_overlap", "no_rejected_review"))
    identity_threshold_met = identity_signal_count >= _IDENTITY_SIGNAL_MIN_COUNT
    identity_criterion = MergeCriterion(
        "identity_signal_threshold", identity_threshold_met,
        f"{identity_signal_count} independent identity signal(s) present "
        f"(minimum required: {_IDENTITY_SIGNAL_MIN_COUNT}).",
    )
    (satisfied if identity_threshold_met else unsatisfied).append(identity_criterion)

    allowed = identity_threshold_met
    reason = (
        f"{identity_signal_count} independent identity signals satisfy the merge contract."
        if allowed else
        f"Only {identity_signal_count} independent identity signal(s) present "
        f"(minimum required: {_IDENTITY_SIGNAL_MIN_COUNT}) -- wallet overlap alone is never sufficient."
    )

    return MergeDecision(
        allowed=allowed, canonical_id=canonical_id, candidate_id=candidate_id,
        overlapping_wallets=overlap,
        satisfied_criteria=tuple(satisfied), unsatisfied_criteria=tuple(unsatisfied),
        reason=reason,
    )
