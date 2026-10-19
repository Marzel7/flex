"""X67.21 -- Shared canonical-predicate integration layer.

Single orchestration point for both authoritative promotion paths (Path A:
candidate workflow, Path B: walkback promotion) to consult the shared,
pure predicate (watchtower_canonical_predicate.evaluate_watchtower_
canonical_eligibility) alongside their existing legacy decision logic,
under one central rollout-mode configuration
(WATCHTOWER_CANONICAL_PREDICATE_MODE).

Architectural invariants:
  - This module owns configuration reads, logging, telemetry persistence,
    and mode-aware decision routing. The predicate itself
    (watchtower_canonical_predicate.py) remains free of all of these --
    no I/O, no config reads, no logging, no exception swallowing.
  - Adapters (watchtower_canonical_adapters.py) remain free of mode logic
    and decision routing -- they only translate a path's own data source
    into CanonicalEvidenceInput.
  - This module NEVER writes to the canonical registry itself. It returns
    an authoritative decision; the CALLER (Path A's promote_eligible_
    candidate / Path B's _promote_if_canonical_watchtower) is responsible
    for invoking the existing, unchanged, idempotent registry writer
    (promote_walkback_confirmed_watchtower) when-and-only-when the
    authoritative decision is ACCEPTED.
  - SHADOW mode: legacy decision is authoritative; the predicate result is
    computed and recorded purely for comparison telemetry. A failure
    anywhere in evidence-gathering/predicate-evaluation/telemetry MUST NOT
    raise -- shadow mode is fully fail-open with respect to the live path.
  - ENFORCE mode: the predicate result is authoritative. A failure in
    evidence-gathering or predicate evaluation MUST fail closed (never
    promote) -- there is no silent fallback to legacy acceptance.
  - LEGACY mode: this module is a pure pass-through; the predicate is not
    required to run at all (though callers may still choose to call this
    module in LEGACY mode purely to preserve one call-site shape --
    see evaluate_canonical_decision's short-circuit below).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from src.ops.watchtower_canonical_predicate import (
    CanonicalEligibilityResult,
    CanonicalEvidenceInput,
    evaluate_watchtower_canonical_eligibility,
)

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Versioning (X67.21 requirement -- distinguishes future predicate/adapter
# refinements in historical telemetry analysis).
# ---------------------------------------------------------------------------
CANONICAL_PREDICATE_VERSION = "X67.20"
CANONICAL_ADAPTER_VERSION = "X67.20"

# ---------------------------------------------------------------------------
# Rollout mode configuration
# ---------------------------------------------------------------------------
CanonicalPredicateMode = Literal["shadow", "enforce", "legacy"]
_VALID_MODES = frozenset({"shadow", "enforce", "legacy"})
_ENV_VAR = "WATCHTOWER_CANONICAL_PREDICATE_MODE"
_DEFAULT_MODE: CanonicalPredicateMode = "shadow"


def get_canonical_predicate_mode() -> CanonicalPredicateMode:
    """Reads WATCHTOWER_CANONICAL_PREDICATE_MODE from the environment.
    Missing -> shadow (production-safe default). Unknown/invalid value ->
    legacy (fail SAFE, never fail toward enforcement), with a warning
    logged so a typo'd config value is visible rather than silently
    changing behaviour."""
    raw = os.environ.get(_ENV_VAR)
    if raw is None or raw.strip() == "":
        return _DEFAULT_MODE
    value = raw.strip().lower()
    if value not in _VALID_MODES:
        _LOG.warning(
            "watchtower_canonical_integration: invalid %s=%r, falling back to 'legacy' "
            "(never falls back to 'enforce')", _ENV_VAR, raw,
        )
        return "legacy"
    return value  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

CanonicalPath = Literal["path_a_candidate_workflow", "path_b_walkback"]

# Structured divergence vocabulary (X67.21 requirement -- never rely on
# free-text comparison messages for analysis).
DIVERGENCE_MATCH_ACCEPT = "MATCH_ACCEPT"
DIVERGENCE_MATCH_REVIEW = "MATCH_REVIEW"
DIVERGENCE_MATCH_REJECT = "MATCH_REJECT"
DIVERGENCE_LEGACY_ACCEPT_PREDICATE_REVIEW = "LEGACY_ACCEPT_PREDICATE_REVIEW"
DIVERGENCE_LEGACY_ACCEPT_PREDICATE_REJECT = "LEGACY_ACCEPT_PREDICATE_REJECT"
DIVERGENCE_LEGACY_REVIEW_PREDICATE_ACCEPT = "LEGACY_REVIEW_PREDICATE_ACCEPT"
DIVERGENCE_LEGACY_REVIEW_PREDICATE_REJECT = "LEGACY_REVIEW_PREDICATE_REJECT"
DIVERGENCE_LEGACY_REJECT_PREDICATE_ACCEPT = "LEGACY_REJECT_PREDICATE_ACCEPT"
DIVERGENCE_LEGACY_REJECT_PREDICATE_REVIEW = "LEGACY_REJECT_PREDICATE_REVIEW"
DIVERGENCE_PREDICATE_EVALUATION_ERROR = "PREDICATE_EVALUATION_ERROR"
DIVERGENCE_ADAPTER_EVIDENCE_ERROR = "ADAPTER_EVIDENCE_ERROR"
DIVERGENCE_LEGACY_DECISION_UNAVAILABLE = "LEGACY_DECISION_UNAVAILABLE"

# Legacy decisions are normalised to this 3-way vocabulary before comparison,
# matching the predicate's own ACCEPTED/REVIEW_REQUIRED/REJECTED shape.
LegacyDecision = Literal["ACCEPTED", "REVIEW_REQUIRED", "REJECTED"]

_MATCH_CODE = {
    "ACCEPTED": DIVERGENCE_MATCH_ACCEPT,
    "REVIEW_REQUIRED": DIVERGENCE_MATCH_REVIEW,
    "REJECTED": DIVERGENCE_MATCH_REJECT,
}
_DIVERGENCE_CODE = {
    ("ACCEPTED", "REVIEW_REQUIRED"): DIVERGENCE_LEGACY_ACCEPT_PREDICATE_REVIEW,
    ("ACCEPTED", "REJECTED"): DIVERGENCE_LEGACY_ACCEPT_PREDICATE_REJECT,
    ("REVIEW_REQUIRED", "ACCEPTED"): DIVERGENCE_LEGACY_REVIEW_PREDICATE_ACCEPT,
    ("REVIEW_REQUIRED", "REJECTED"): DIVERGENCE_LEGACY_REVIEW_PREDICATE_REJECT,
    ("REJECTED", "ACCEPTED"): DIVERGENCE_LEGACY_REJECT_PREDICATE_ACCEPT,
    ("REJECTED", "REVIEW_REQUIRED"): DIVERGENCE_LEGACY_REJECT_PREDICATE_REVIEW,
}


@dataclass(frozen=True)
class CanonicalIntegrationResult:
    mode: str
    path: str

    predicate_result: Optional[CanonicalEligibilityResult]
    legacy_decision: Optional[str]

    authoritative_decision: str
    authoritative_reason: str

    decisions_match: Optional[bool]
    divergence_code: Optional[str]

    predicate_error: Optional[str] = None
    # Additive beyond the task's minimum field list -- INSUFFICIENT_EVIDENCE
    # is the predicate's own third possible decision (X67.17); it is folded
    # into REVIEW_REQUIRED for the purpose of the 3-way legacy/predicate
    # comparison vocabulary above (both mean "do not promote, but do not
    # treat as a settled rejection either"), but the raw predicate decision
    # string is preserved here for anyone who needs the finer distinction.
    predicate_raw_decision: Optional[str] = None


def _normalise_predicate_decision(result: CanonicalEligibilityResult) -> LegacyDecision:
    if result.decision == "ACCEPTED":
        return "ACCEPTED"
    if result.decision == "REJECTED":
        return "REJECTED"
    # REVIEW_REQUIRED and INSUFFICIENT_EVIDENCE both mean "do not promote,
    # do not treat as terminal rejection" -- see X67.17 S3's explicit
    # design: these are a third outcome, never collapsed into rejection.
    return "REVIEW_REQUIRED"


def evaluate_canonical_decision(
    *,
    path: CanonicalPath,
    mint: str,
    build_evidence: Callable[[], CanonicalEvidenceInput],
    legacy_decision: Optional[LegacyDecision],
    legacy_reason: Optional[str],
    mode: Optional[CanonicalPredicateMode] = None,
) -> CanonicalIntegrationResult:
    """The single mode-aware integration entry point both paths call.

    `build_evidence` is a zero-arg callable (a closure over the caller's
    own connection/mint) rather than a pre-built CanonicalEvidenceInput, so
    that in LEGACY mode evidence-gathering can be skipped entirely (the
    predicate is "not required to run" per LEGACY mode's semantics) and so
    that adapter exceptions are caught HERE, at the one place responsible
    for mode-aware error handling, rather than requiring every caller to
    wrap its own adapter call in a try/except.
    """
    resolved_mode: CanonicalPredicateMode = mode if mode is not None else get_canonical_predicate_mode()

    if resolved_mode == "legacy":
        # Existing decision logic remains fully authoritative; the shared
        # predicate is not required to run at all.
        return CanonicalIntegrationResult(
            mode="legacy", path=path,
            predicate_result=None, legacy_decision=legacy_decision,
            authoritative_decision=legacy_decision or "REVIEW_REQUIRED",
            authoritative_reason=legacy_reason or "LEGACY_MODE_NO_PREDICATE_EVALUATION",
            decisions_match=None, divergence_code=None,
            predicate_error=None, predicate_raw_decision=None,
        )

    # SHADOW and ENFORCE both need the predicate evaluated.
    predicate_result: Optional[CanonicalEligibilityResult] = None
    predicate_error: Optional[str] = None
    try:
        evidence = build_evidence()
        predicate_result = evaluate_watchtower_canonical_eligibility(evidence)
    except Exception as exc:  # noqa: BLE001 -- must never propagate to the caller
        predicate_error = f"{type(exc).__name__}: {exc}"
        _LOG.error(
            "watchtower_canonical_integration: evidence/predicate evaluation failed "
            "path=%s mint=%s mode=%s error=%s", path, mint, resolved_mode, predicate_error,
        )

    if resolved_mode == "shadow":
        # Legacy remains fully authoritative regardless of predicate outcome
        # or predicate/adapter failure -- shadow mode NEVER influences the
        # live decision.
        divergence_code = DIVERGENCE_ADAPTER_EVIDENCE_ERROR if predicate_error else None
        decisions_match: Optional[bool] = None
        if predicate_result is not None and legacy_decision is not None:
            predicate_norm = _normalise_predicate_decision(predicate_result)
            decisions_match = (predicate_norm == legacy_decision)
            divergence_code = (
                _MATCH_CODE[legacy_decision] if decisions_match
                else _DIVERGENCE_CODE.get((legacy_decision, predicate_norm), "OTHER")
            )
        elif legacy_decision is None:
            divergence_code = DIVERGENCE_LEGACY_DECISION_UNAVAILABLE
        return CanonicalIntegrationResult(
            mode="shadow", path=path,
            predicate_result=predicate_result, legacy_decision=legacy_decision,
            authoritative_decision=legacy_decision or "REVIEW_REQUIRED",
            authoritative_reason=legacy_reason or "SHADOW_MODE_LEGACY_AUTHORITATIVE",
            decisions_match=decisions_match, divergence_code=divergence_code,
            predicate_error=predicate_error,
            predicate_raw_decision=predicate_result.decision if predicate_result else None,
        )

    # ENFORCE.
    if predicate_error is not None:
        # Fail closed: never promote, never silently fall back to legacy
        # acceptance. A safe holding state (REVIEW_REQUIRED-equivalent) is
        # the only acceptable outcome for an evaluation failure in
        # enforcement mode.
        return CanonicalIntegrationResult(
            mode="enforce", path=path,
            predicate_result=None, legacy_decision=legacy_decision,
            authoritative_decision="REVIEW_REQUIRED",
            authoritative_reason="PREDICATE_EVALUATION_ERROR",
            decisions_match=None if legacy_decision is None else False,
            divergence_code=DIVERGENCE_PREDICATE_EVALUATION_ERROR,
            predicate_error=predicate_error, predicate_raw_decision=None,
        )

    predicate_norm = _normalise_predicate_decision(predicate_result)
    decisions_match = (
        None if legacy_decision is None else (predicate_norm == legacy_decision)
    )
    divergence_code = (
        DIVERGENCE_LEGACY_DECISION_UNAVAILABLE if legacy_decision is None
        else (_MATCH_CODE[legacy_decision] if decisions_match
              else _DIVERGENCE_CODE.get((legacy_decision, predicate_norm), "OTHER"))
    )
    return CanonicalIntegrationResult(
        mode="enforce", path=path,
        predicate_result=predicate_result, legacy_decision=legacy_decision,
        authoritative_decision=predicate_norm,
        authoritative_reason=predicate_result.decision_reason,
        decisions_match=decisions_match, divergence_code=divergence_code,
        predicate_error=None, predicate_raw_decision=predicate_result.decision,
    )


# ---------------------------------------------------------------------------
# Telemetry persistence (append-only comparison ledger)
# ---------------------------------------------------------------------------

TELEMETRY_DDL = """
CREATE TABLE IF NOT EXISTS wt_canonical_predicate_comparisons (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at              INTEGER NOT NULL,
    path                    TEXT NOT NULL,
    mode                    TEXT NOT NULL,
    mint                    TEXT NOT NULL,
    source_record_id        TEXT,

    legacy_decision         TEXT,
    legacy_reason           TEXT,

    predicate_decision      TEXT,
    predicate_reason        TEXT,
    predicate_conflicts_json TEXT,

    authoritative_decision  TEXT NOT NULL,
    authoritative_reason    TEXT NOT NULL,

    decisions_match         INTEGER,
    divergence_code         TEXT,

    evidence_quality        TEXT,
    identity_status         TEXT,
    session_topology        TEXT,
    mechanism_verification  TEXT,

    predicate_version       TEXT,
    adapter_version         TEXT,
    error_text              TEXT
);
CREATE INDEX IF NOT EXISTS ix_wcpc_mint ON wt_canonical_predicate_comparisons(mint);
CREATE INDEX IF NOT EXISTS ix_wcpc_created_at ON wt_canonical_predicate_comparisons(created_at);
CREATE INDEX IF NOT EXISTS ix_wcpc_divergence ON wt_canonical_predicate_comparisons(divergence_code);
"""


def ensure_telemetry_schema(conn) -> None:
    conn.executescript(TELEMETRY_DDL)


def record_comparison_telemetry(
    conn, result: CanonicalIntegrationResult, *,
    mint: str, source_record_id: Optional[str] = None, now: Optional[int] = None,
) -> None:
    """Append-only telemetry write. MUST NEVER raise into the caller --
    per X67.21's explicit requirement, a telemetry failure must never
    block either the SHADOW or ENFORCE live path. Any error here is
    caught and logged, never propagated."""
    try:
        ensure_telemetry_schema(conn)
        pr = result.predicate_result
        conn.execute(
            "INSERT INTO wt_canonical_predicate_comparisons ("
            "created_at, path, mode, mint, source_record_id, "
            "legacy_decision, legacy_reason, "
            "predicate_decision, predicate_reason, predicate_conflicts_json, "
            "authoritative_decision, authoritative_reason, "
            "decisions_match, divergence_code, "
            "evidence_quality, identity_status, session_topology, mechanism_verification, "
            "predicate_version, adapter_version, error_text"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                now or int(time.time()), result.path, result.mode, mint, source_record_id,
                result.legacy_decision, None,
                result.predicate_raw_decision,
                pr.decision_reason if pr else None,
                json.dumps(pr.conflicts) if pr else None,
                result.authoritative_decision, result.authoritative_reason,
                None if result.decisions_match is None else int(result.decisions_match),
                result.divergence_code,
                pr.evidence_strength if pr else None,
                pr.identity_status if pr else None,
                pr.session_topology if pr else None,
                pr.creator_funding_mechanism if pr else None,
                CANONICAL_PREDICATE_VERSION, CANONICAL_ADAPTER_VERSION,
                result.predicate_error,
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 -- telemetry must never break the live path
        _LOG.error(
            "watchtower_canonical_integration: telemetry write failed mint=%s error=%s",
            mint, exc,
        )
