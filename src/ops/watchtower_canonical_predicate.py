"""X67.18 -- Shared canonical WATCHTOWER eligibility predicate (X67.17 design,
implemented). A single, pure decision function consumed by BOTH promotion
paths (Path A: src.ops.provisioning_candidates_workflow.evaluate_candidate_
for_canonical_promotion; Path B: src.core.watchtower_registry_promotion.
promote_walkback_confirmed_watchtower), replacing their previously
divergent, independently-maintained predicates.

Architectural invariant: this module performs NO database reads, NO RPC
calls, and NO writes of any kind. It consumes only a CanonicalEvidenceInput
already assembled by the caller (see watchtower_canonical_adapters.py for
the two adapters that build that input from each path's own data source)
and returns a CanonicalEligibilityResult. Given the same input, it always
returns the same output (pure function, X67.17 Test Spec #17).

Five independent evidence dimensions (X67.17 S1): Identity, Session,
Creator Funding Mechanism, Evidence Quality, Conflicts. See the X67.17
design document (docs/design/X67_17_CANONICAL_EVIDENCE_MODEL.md) for the
full rationale -- this module is a direct, literal implementation of that
document's Sections 1-5, including the two explicit policy resolutions
made in this implementation (see _MECHANISM_CONFLICT_IS_FATAL and the
EXCHANGE_BOUNDARY evidence-tier requirement below).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Evidence input types (X67.17 S3)
# ---------------------------------------------------------------------------

IdentityTier = Literal["CERTAIN", "CONFIRMED", "MANUAL", "LOW"]
SubprovConfidence = Literal["PROVEN", "SESSION_OBSERVED", "UNPROVEN"]
LineageContinuity = Literal["EXCLUSIVE", "SHARED", "BROKEN"]

SessionState = Literal["ACTIVE", "EXPIRED", "REJECTED", "RECONSTRUCTED", "ABSENT"]
SessionTopology = Literal["DIRECT", "RELAY_ASSISTED", "UNKNOWN"]
RelayStatus = Literal["NONE", "EXTERNAL_UNCLASSIFIED", "EXTERNAL_EXCHANGE_PATTERN"]

MechanismValue = Literal[
    "WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE", "PLAIN_XFER", "UNVERIFIED", "CONFLICTING",
]
EvidenceTier = Literal[
    "RPC_VERIFIED", "MANUAL_CONFIRMATION", "WALKBACK_RECOVERED", "HISTORICAL_RECONSTRUCTION",
]

# Confidence ordering, highest to lowest (X67.17 S1d). Index = rank (0 best).
_EVIDENCE_TIER_RANK: dict[str, int] = {
    "RPC_VERIFIED": 0,
    "MANUAL_CONFIRMATION": 1,
    "WALKBACK_RECOVERED": 2,
    "HISTORICAL_RECONSTRUCTION": 3,
}

# Account-close mechanisms are self-verifying by instruction shape (X67.17 S2,
# S8) -- an atomic wrap-close/seeded-close instruction cannot hide an
# alternate funding path the way a generic transfer could. These mechanisms
# therefore accept HISTORICAL_RECONSTRUCTION/WALKBACK_RECOVERED evidence.
# PLAIN_XFER is a generic instruction with no such self-verifying shape, so
# it requires RPC_VERIFIED evidence specifically (X67.17 S2's explicit
# PLAIN_XFER-evidence-tier matrix row).
_SELF_VERIFYING_MECHANISMS = frozenset({"WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE"})

ConflictCode = Literal[
    "EXCHANGE_BOUNDARY", "MULTI_SOURCE_RELAY", "ROLE_COLLISION",
    "MECHANISM_CONFLICT", "LINEAGE_CONFLICT", "RELAY_UNCLASSIFIED",
    "SHARED_RELAY_SESSION_VOLUME", "MECHANISM_LABEL_VARIATION",
]

# Fatal vs reviewable vs informational, per X67.17 S1e.
_FATAL_CONFLICTS = frozenset({
    "EXCHANGE_BOUNDARY", "ROLE_COLLISION", "MECHANISM_CONFLICT", "LINEAGE_CONFLICT",
})
_REVIEWABLE_CONFLICTS = frozenset({"MULTI_SOURCE_RELAY"})
# X67.20 -- MECHANISM_LABEL_VARIATION added: the SAME on-chain transaction
# signature, cited by both the registry and an independent source, described
# with two different mechanism taxonomy labels (e.g. SEEDED_ACCOUNT_CLOSE
# vs WSOL_WRAP_CLOSE for one identical signature/sender/recipient/amount/
# timing). This is purely a vocabulary/decoder-taxonomy disagreement about
# one undisputed event, NOT a lineage-integrity question -- unlike
# MECHANISM_CONFLICT (which implies two DIFFERENT signatures/events), it
# must never gate the decision on its own. The predicate function
# (evaluate_watchtower_canonical_eligibility, UNCHANGED by this addition)
# already ignores any conflict code it doesn't explicitly special-case in
# its fatal/reviewable gates -- adding this code here only makes the
# distinction visible in CanonicalEligibilityResult.conflicts, without
# touching a single line of decision logic.
_INFORMATIONAL_CONFLICTS = frozenset({
    "RELAY_UNCLASSIFIED", "SHARED_RELAY_SESSION_VOLUME", "MECHANISM_LABEL_VARIATION",
})

# X67.17 S1e / S9's EXCHANGE_BOUNDARY note: an exchange-boundary conflict is
# only FATAL when it carries RPC-confirmed (structured) evidence -- a bare
# free-text closure-note claim (X67.14's "Binance 2" finding) is NOT
# sufficient to reject outright. Conflicts must self-report whether their
# evidence is RPC-confirmed via `rpc_confirmed`; unconfirmed
# EXCHANGE_BOUNDARY conflicts are downgraded to a review signal instead of
# a fatal one (see _classify_conflicts below).
#
# X67.17 S9's MECHANISM_CONFLICT tension: fatal only once a live re-decode
# has actually been attempted and still disagrees; an as-yet-unattempted
# re-decode is EVIDENCE_INSUFFICIENT, not an outright rejection. Conflicts
# must self-report `redecode_attempted` for this reason.


@dataclass(frozen=True)
class TreasuryConfirmationEvidence:
    confirmed: bool
    confidence_tier: Optional[IdentityTier] = None
    subprovider_confidence: Optional[SubprovConfidence] = None
    lineage_continuity: Optional[LineageContinuity] = None


@dataclass(frozen=True)
class SessionEvidence:
    exists: bool
    state: SessionState = "ABSENT"
    topology: SessionTopology = "UNKNOWN"
    relay_wallet: Optional[str] = None
    relay_status: RelayStatus = "NONE"


@dataclass(frozen=True)
class MechanismEvidence:
    value: MechanismValue
    evidence_tier: EvidenceTier


@dataclass(frozen=True)
class ConflictSignal:
    code: ConflictCode
    detail: str = ""
    # Only meaningful for EXCHANGE_BOUNDARY -- was this established via a
    # live/independently-checkable RPC or labelled-address source, rather
    # than a free-text note?
    rpc_confirmed: bool = False
    # Only meaningful for MECHANISM_CONFLICT -- has a live re-decode of the
    # raw instruction actually been attempted (and still disagreed)?
    redecode_attempted: bool = False


@dataclass(frozen=True)
class CanonicalEvidenceInput:
    mint: str
    treasury_wallet: Optional[str]
    subprov_wallet: Optional[str]
    creator_wallet: Optional[str]
    treasury_confirmation: TreasuryConfirmationEvidence
    session_evidence: SessionEvidence
    mechanism_evidence: MechanismEvidence
    conflict_evidence: list[ConflictSignal] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Decision output (X67.17 S4)
# ---------------------------------------------------------------------------

Decision = Literal["ACCEPTED", "REJECTED", "REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE"]


@dataclass(frozen=True)
class CanonicalEligibilityResult:
    eligible: bool
    decision: Decision
    identity_status: Literal["CONFIRMED", "UNCONFIRMED"]
    session_status: SessionState
    session_topology: SessionTopology
    creator_funding_mechanism: MechanismValue
    evidence_strength: Optional[EvidenceTier]
    conflicts: list[str]
    missing_evidence: list[str]
    review_required: bool
    decision_reason: str
    relay_wallet: Optional[str] = None
    relay_classification: Optional[RelayStatus] = None


# ---------------------------------------------------------------------------
# Reason codes (X67.17 S5)
# ---------------------------------------------------------------------------

REASON_CANONICAL_CONFIRMED = "CANONICAL_CONFIRMED"
REASON_IDENTITY_UNCONFIRMED = "IDENTITY_UNCONFIRMED"
REASON_CONFLICT_EXCHANGE_BOUNDARY = "CONFLICT_EXCHANGE_BOUNDARY"
REASON_CONFLICT_ROLE_COLLISION = "CONFLICT_ROLE_COLLISION"
REASON_CONFLICT_MECHANISM = "CONFLICT_MECHANISM"
REASON_CONFLICT_LINEAGE = "CONFLICT_LINEAGE"
REASON_CREATOR_FUNDING_UNSUPPORTED = "CREATOR_FUNDING_UNSUPPORTED"
REASON_EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
REASON_SESSION_INVALID = "SESSION_INVALID"
REASON_SESSION_RELAY_ASSISTED_UNCLASSIFIED = "SESSION_RELAY_ASSISTED_UNCLASSIFIED"
REASON_SESSION_RELAY_ASSISTED_EXCHANGE_SIGNATURE = "SESSION_RELAY_ASSISTED_EXCHANGE_SIGNATURE"
REASON_CONFLICT_MULTI_SOURCE_RELAY = "CONFLICT_MULTI_SOURCE_RELAY"
REASON_MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"

# Every reason code maps to exactly one decision (X67.17 S5's invariant) --
# enforced here as data so it is itself testable (X67.17 test spec item
# validating the reason-code catalogue).
REASON_TO_DECISION: dict[str, Decision] = {
    REASON_CANONICAL_CONFIRMED: "ACCEPTED",
    REASON_IDENTITY_UNCONFIRMED: "REJECTED",
    REASON_CONFLICT_EXCHANGE_BOUNDARY: "REJECTED",
    REASON_CONFLICT_ROLE_COLLISION: "REJECTED",
    REASON_CONFLICT_MECHANISM: "REJECTED",
    REASON_CONFLICT_LINEAGE: "REJECTED",
    REASON_CREATOR_FUNDING_UNSUPPORTED: "REJECTED",
    REASON_EVIDENCE_INSUFFICIENT: "INSUFFICIENT_EVIDENCE",
    REASON_SESSION_INVALID: "INSUFFICIENT_EVIDENCE",
    REASON_SESSION_RELAY_ASSISTED_UNCLASSIFIED: "REVIEW_REQUIRED",
    REASON_SESSION_RELAY_ASSISTED_EXCHANGE_SIGNATURE: "REVIEW_REQUIRED",
    REASON_CONFLICT_MULTI_SOURCE_RELAY: "REVIEW_REQUIRED",
    REASON_MANUAL_REVIEW_REQUIRED: "REVIEW_REQUIRED",
}


def _result(
    reason: str, evidence: CanonicalEvidenceInput, *,
    missing_evidence: Optional[list[str]] = None,
) -> CanonicalEligibilityResult:
    decision = REASON_TO_DECISION[reason]
    conflict_codes = [c.code for c in evidence.conflict_evidence]
    return CanonicalEligibilityResult(
        eligible=(decision == "ACCEPTED"),
        decision=decision,
        identity_status="CONFIRMED" if evidence.treasury_confirmation.confirmed else "UNCONFIRMED",
        session_status=evidence.session_evidence.state,
        session_topology=evidence.session_evidence.topology,
        creator_funding_mechanism=evidence.mechanism_evidence.value,
        evidence_strength=evidence.mechanism_evidence.evidence_tier,
        conflicts=conflict_codes,
        missing_evidence=missing_evidence or [],
        review_required=(decision == "REVIEW_REQUIRED"),
        decision_reason=reason,
        relay_wallet=evidence.session_evidence.relay_wallet,
        relay_classification=(
            evidence.session_evidence.relay_status
            if evidence.session_evidence.topology == "RELAY_ASSISTED" else None
        ),
    )


def evaluate_watchtower_canonical_eligibility(
    evidence: CanonicalEvidenceInput,
) -> CanonicalEligibilityResult:
    """The single shared canonical eligibility predicate (X67.17 S3). Pure
    function: no I/O, deterministic, same input always yields same output.

    Decision flow (X67.17 S3, implemented literally):
      1. Identity gate.
      2. Fatal conflict gate (role collision, lineage conflict, mechanism
         conflict-after-redecode, RPC-confirmed exchange boundary).
      3. Mechanism verification gate (value known + evidence tier sufficient
         for that mechanism -- see _SELF_VERIFYING_MECHANISMS).
      4. Session topology check (relay-assisted + exchange-pattern relay ->
         review, not reject).
      5. Reviewable-conflict gate (multi-source relay -> review).
      6. Accept.
    """
    # 1. Identity gate.
    if not evidence.treasury_confirmation.confirmed:
        return _result(REASON_IDENTITY_UNCONFIRMED, evidence)

    # 2. Fatal conflict gate. Role collision / lineage conflict are
    # unconditionally fatal. Exchange boundary is fatal ONLY when
    # rpc_confirmed=True (X67.17 S9's correction -- a bare closure note is
    # not sufficient). Mechanism conflict is fatal ONLY when
    # redecode_attempted=True and it still disagreed (X67.17 S9's
    # tension-resolution) -- an as-yet-unattempted conflict is instead
    # routed to EVIDENCE_INSUFFICIENT via the mechanism-evidence gate below,
    # since it means the mechanism cannot yet be trusted either way.
    for c in evidence.conflict_evidence:
        if c.code == "ROLE_COLLISION":
            return _result(REASON_CONFLICT_ROLE_COLLISION, evidence)
        if c.code == "LINEAGE_CONFLICT":
            return _result(REASON_CONFLICT_LINEAGE, evidence)
        if c.code == "EXCHANGE_BOUNDARY" and c.rpc_confirmed:
            return _result(REASON_CONFLICT_EXCHANGE_BOUNDARY, evidence)
        if c.code == "MECHANISM_CONFLICT" and c.redecode_attempted:
            return _result(REASON_CONFLICT_MECHANISM, evidence)

    # An exchange-boundary conflict that is NOT yet RPC-confirmed, or a
    # mechanism conflict whose redecode hasn't been attempted, still needs
    # to surface -- but as insufficient evidence, not a hard rejection.
    unconfirmed_exchange = any(
        c.code == "EXCHANGE_BOUNDARY" and not c.rpc_confirmed for c in evidence.conflict_evidence
    )
    unattempted_mechanism_conflict = any(
        c.code == "MECHANISM_CONFLICT" and not c.redecode_attempted for c in evidence.conflict_evidence
    )
    if unconfirmed_exchange or unattempted_mechanism_conflict:
        missing = []
        if unconfirmed_exchange:
            missing.append("rpc_confirmed_exchange_attribution")
        if unattempted_mechanism_conflict:
            missing.append("mechanism_redecode")
        return _result(REASON_EVIDENCE_INSUFFICIENT, evidence, missing_evidence=missing)

    # 3. Mechanism verification gate.
    mech = evidence.mechanism_evidence
    if mech.value == "CONFLICTING":
        # Should already have been caught by the conflict gate above if a
        # ConflictSignal was supplied; this is a defensive fallback for a
        # caller that set the mechanism value directly without a matching
        # conflict signal.
        return _result(REASON_CONFLICT_MECHANISM, evidence)
    if mech.value == "UNVERIFIED":
        return _result(REASON_EVIDENCE_INSUFFICIENT, evidence,
                        missing_evidence=["creator_funding_mechanism"])
    if mech.value not in ("WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE", "PLAIN_XFER"):
        return _result(REASON_CREATOR_FUNDING_UNSUPPORTED, evidence)

    required_tier_rank = (
        _EVIDENCE_TIER_RANK["HISTORICAL_RECONSTRUCTION"]
        if mech.value in _SELF_VERIFYING_MECHANISMS
        else _EVIDENCE_TIER_RANK["RPC_VERIFIED"]
    )
    if _EVIDENCE_TIER_RANK[mech.evidence_tier] > required_tier_rank:
        return _result(REASON_EVIDENCE_INSUFFICIENT, evidence,
                        missing_evidence=["stronger_evidence_tier_required"])

    # 4. Session topology check.
    sess = evidence.session_evidence
    if not sess.exists:
        return _result(REASON_SESSION_INVALID, evidence, missing_evidence=["session_evidence"])
    if sess.topology == "RELAY_ASSISTED":
        if sess.relay_status == "EXTERNAL_EXCHANGE_PATTERN":
            return _result(REASON_SESSION_RELAY_ASSISTED_EXCHANGE_SIGNATURE, evidence)
        if sess.relay_status == "EXTERNAL_UNCLASSIFIED":
            # Informational only per X67.17 S1e -- accept, but the caller
            # already gets full visibility via relay_wallet/relay_classification.
            pass

    # 5. Reviewable-conflict gate.
    if any(c.code == "MULTI_SOURCE_RELAY" for c in evidence.conflict_evidence):
        return _result(REASON_CONFLICT_MULTI_SOURCE_RELAY, evidence)

    # 6. Accept.
    return _result(REASON_CANONICAL_CONFIRMED, evidence)
