"""
Sprint X14 — Lifecycle Forecast Engine.

Answers: "Given everything we currently know, what is the most probable
next operational lifecycle transition?"

Architecture:
    Assessment (X13)         — synthesised intelligence
    BehaviourProfile (X10)   — timing facts (avg_launch_delay_s, avg_fanout_to_create_s)
    BehaviourChangeReport (X11) — drift state
    LifecycleSnapshot (X4)   — current state

The engine ORCHESTRATES these layers.  It never duplicates their DB queries
and never bypasses Assessment to reason from raw evidence.

Forecast scope: operational lifecycle transitions only.
    IDLE        → OBSERVING
    OBSERVING   → ARMED  |  OBSERVING (no change)
    ARMED       → ACTIVE
    ACTIVE      → COMPLETED
    COMPLETED   → ARCHIVED  (or IDLE if operator re-activates)

NOT in scope: price, market cap, profitability, token popularity, human intent.

DB-safety:
  — No writes during computation.
  — All DB access delegated to sub-engines (already constrained in X10–X13).
  — Fail open on any error.
  — No page-load computation: caller controls when forecasts are computed.

Transition Windows (expressed as human-readable ranges, never exact timestamps):
  FAST   → "1–10 minutes"
  SHORT  → "5–20 minutes"
  MEDIUM → "15–60 minutes"
  LONG   → "1–4 hours"
  VERY_LONG → "4–24 hours"
  UNKNOWN → "Unknown"
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from src.ops.lifecycle import (
    ARMED, ACTIVE, COMPLETED, IDLE, OBSERVING, ARCHIVED, STATES_VALID,
)
from src.ops.assessment_engine import ASSESSMENT_TYPES, OperatorAssessmentBundle

# ── Confidence ordering ───────────────────────────────────────────────────────

_CONF_ORDER = {"INSUFFICIENT": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

# ── Transition window constants ───────────────────────────────────────────────

WINDOW_FAST      = "1–10 minutes"
WINDOW_SHORT     = "5–20 minutes"
WINDOW_MEDIUM    = "15–60 minutes"
WINDOW_LONG      = "1–4 hours"
WINDOW_VERY_LONG = "4–24 hours"
WINDOW_UNKNOWN   = "Unknown"

# ── Forecast types (what transition is being forecast) ───────────────────────

FORECAST_TYPES = frozenset({
    "TRANSITION_LIKELY",    # confident next state predicted
    "TRANSITION_POSSIBLE",  # lower-confidence next state
    "STATE_STABLE",         # current state expected to persist
    "TRANSITION_BLOCKED",   # evidence argues against transition
    "INSUFFICIENT_EVIDENCE",
})


# ── Evidence items (reuse pattern from X13) ───────────────────────────────────

@dataclass
class ForecastEvidenceItem:
    label:  str
    detail: str
    source: str    # ASSESSMENT | BEHAVIOUR | CHANGE | LIFECYCLE | SIMILARITY | TIMING
    weight: float = 1.0

    def to_dict(self) -> dict:
        return {"label": self.label, "detail": self.detail,
                "source": self.source, "weight": self.weight}


# ── Forecast dataclass ────────────────────────────────────────────────────────

@dataclass
class LifecycleForecast:
    forecast_id:               str
    operator_id:               str
    forecast_type:             str
    current_state:             str
    predicted_next_state:      str | None
    forecast_reason:           str
    confidence:                str          # INSUFFICIENT | LOW | MEDIUM | HIGH
    supporting_assessments:    list[str]    # assessment_type values used
    supporting_evidence:       list[ForecastEvidenceItem] = field(default_factory=list)
    contradictory_evidence:    list[ForecastEvidenceItem] = field(default_factory=list)
    supporting_evidence_count: int = 0
    expected_window:           str = WINDOW_UNKNOWN
    generated_at:              int = field(default_factory=lambda: int(time.time()))
    expires_at:                int = field(default_factory=lambda: int(time.time()) + 1800)
    historical_observations:   int = 0
    historical_basis:          str = ""
    gap_notes:                 list[str] = field(default_factory=list)
    # Input fingerprints this forecast was derived from. Used purely for
    # invalidation (does the ground truth still match what produced this
    # forecast?) — never displayed as "precision".
    assessment_fingerprint:    str = ""
    behaviour_fingerprint:     str = ""
    change_fingerprint:        str = ""
    forecast_fingerprint:      str = ""

    def __post_init__(self) -> None:
        if self.forecast_type not in FORECAST_TYPES:
            raise ValueError(f"Unknown forecast_type: {self.forecast_type!r}")
        if self.predicted_next_state is not None:
            if self.predicted_next_state not in STATES_VALID:
                raise ValueError(f"Unknown lifecycle state: {self.predicted_next_state!r}")
        self.supporting_evidence_count = (
            len(self.supporting_evidence) + len(self.contradictory_evidence)
        )
        if not self.forecast_fingerprint:
            self.forecast_fingerprint = _compute_forecast_fingerprint(
                operator_id            = self.operator_id,
                current_state          = self.current_state,
                predicted_next_state   = self.predicted_next_state,
                confidence             = self.confidence,
                expected_window        = self.expected_window,
                assessment_fingerprint = self.assessment_fingerprint,
                behaviour_fingerprint  = self.behaviour_fingerprint,
                change_fingerprint     = self.change_fingerprint,
            )

    def is_stale_against(
        self,
        current_state:          str,
        assessment_fingerprint: str,
        behaviour_fingerprint:  str,
        change_fingerprint:     str,
        now:                    int | None = None,
    ) -> tuple[bool, str]:
        """
        Explicit invalidation check (never rely on TTL alone).

        Returns (stale, reason). A forecast is stale the instant any of its
        inputs no longer match reality — it is derived state, not a fact.
        """
        now = now if now is not None else int(time.time())
        if current_state != self.current_state:
            return True, "lifecycle_state_changed"
        if assessment_fingerprint != self.assessment_fingerprint:
            return True, "assessment_changed"
        if change_fingerprint != self.change_fingerprint:
            return True, "behaviour_change_changed"
        if behaviour_fingerprint != self.behaviour_fingerprint:
            return True, "behaviour_changed"
        if now >= self.expires_at:
            return True, "window_expired"
        return False, ""

    def to_dict(self) -> dict:
        return {
            "forecast_id":               self.forecast_id,
            "forecast_fingerprint":      self.forecast_fingerprint,
            "operator_id":               self.operator_id,
            "forecast_type":             self.forecast_type,
            "current_state":             self.current_state,
            "predicted_next_state":      self.predicted_next_state,
            "forecast_reason":           self.forecast_reason,
            "confidence":                self.confidence,
            "supporting_assessments":    self.supporting_assessments,
            "supporting_evidence":       [e.to_dict() for e in self.supporting_evidence],
            "contradictory_evidence":    [e.to_dict() for e in self.contradictory_evidence],
            "supporting_evidence_count": self.supporting_evidence_count,
            "expected_window":           self.expected_window,
            "generated_at":              self.generated_at,
            "expires_at":                self.expires_at,
            "historical_observations":   self.historical_observations,
            "historical_basis":          self.historical_basis,
            "gap_notes":                 self.gap_notes,
            "assessment_fingerprint":    self.assessment_fingerprint,
            "behaviour_fingerprint":     self.behaviour_fingerprint,
            "change_fingerprint":        self.change_fingerprint,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lower_conf(a: str, b: str) -> str:
    return a if _CONF_ORDER.get(a, 0) <= _CONF_ORDER.get(b, 0) else b


def _penalise(conf: str, n: int) -> str:
    order = ["INSUFFICIENT", "LOW", "MEDIUM", "HIGH"]
    idx   = order.index(conf) if conf in order else 0
    return order[max(0, idx - min(n, 2))]


def _fid(operator_id: str, ftype: str, ts: int) -> str:
    return f"{operator_id}:{ftype}:{ts}"


def _compute_forecast_fingerprint(
    operator_id:            str,
    current_state:          str,
    predicted_next_state:   str | None,
    confidence:             str,
    expected_window:        str,
    assessment_fingerprint: str,
    behaviour_fingerprint:  str,
    change_fingerprint:     str,
) -> str:
    """
    Same intelligence in -> same forecast_fingerprint out. No timestamps.
    This is what makes forecasts content-addressed derived state rather
    than identity-addressed rows: recomputing from unchanged inputs is a
    no-op, and any input change produces a new fingerprint.
    """
    payload = {
        "operator_id":            operator_id,
        "current_state":          current_state,
        "predicted_next_state":   predicted_next_state,
        "confidence":             confidence,
        "expected_window":        expected_window,
        "assessment_fingerprint": assessment_fingerprint,
        "behaviour_fingerprint":  behaviour_fingerprint,
        "change_fingerprint":     change_fingerprint,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _get_fact(profile, dim_key: str, fact_key: str):
    """Extract a BehaviourFact from a BehaviourProfile by keys."""
    for dim in profile.dimensions:
        if dim.key == dim_key:
            for f in dim.facts:
                if f.key == fact_key:
                    return f
    return None


def _timing_window(avg_seconds: float | None) -> str:
    """Map average timing in seconds to a human-readable window."""
    if avg_seconds is None:
        return WINDOW_UNKNOWN
    m = avg_seconds / 60.0
    if m < 10:
        return WINDOW_FAST
    if m < 20:
        return WINDOW_SHORT
    if m < 60:
        return WINDOW_MEDIUM
    if m < 240:
        return WINDOW_LONG
    return WINDOW_VERY_LONG


def _insufficient_forecast(operator_id: str, state: str, reason: str, ts: int) -> LifecycleForecast:
    return LifecycleForecast(
        forecast_id          = _fid(operator_id, "INSUFFICIENT_EVIDENCE", ts),
        operator_id          = operator_id,
        forecast_type        = "INSUFFICIENT_EVIDENCE",
        current_state        = state,
        predicted_next_state = None,
        forecast_reason      = reason,
        confidence           = "INSUFFICIENT",
        supporting_assessments = [],
        generated_at         = ts,
        expires_at           = ts + 1800,
    )


# ── Forecast Rules ────────────────────────────────────────────────────────────
# Each rule is a documented plain function.
# Rules are evaluated in priority order; first match wins (except INSUFFICIENT).

def _rule_insufficient(bundle: OperatorAssessmentBundle, profile, change_report,
                       state: str, ts: int) -> LifecycleForecast | None:
    """
    Rule: INSUFFICIENT_EVIDENCE
    Trigger: Assessment bundle is unavailable, OR primary assessment is INSUFFICIENT_EVIDENCE.
    Rationale: cannot forecast without assessment foundation.
    """
    if not bundle.available:
        return _insufficient_forecast(
            bundle.operator_id, state,
            "Assessment layer is unavailable; lifecycle forecast cannot be produced.", ts,
        )
    insuff = bundle.by_type("INSUFFICIENT_EVIDENCE")
    if insuff is not None and bundle.highest_confidence() and \
            bundle.highest_confidence().confidence == "INSUFFICIENT":
        return _insufficient_forecast(
            bundle.operator_id, state,
            f"Insufficient observations to assess this operator "
            f"({insuff.summary}).",
            ts,
        )
    return None


def _rule_idle_to_observing(
    bundle: OperatorAssessmentBundle,
    profile, change_report,
    state: str, ts: int,
) -> LifecycleForecast | None:
    """
    Rule: IDLE → OBSERVING
    Trigger:
      — current state is IDLE
      — assessment contains CAMPAIGN_EXPANSION or BEHAVIOUR_CHANGE
        (signals operator is becoming active again)
    Confidence: governed by assessment confidence.
    Window: UNKNOWN (activation timing highly variable).
    """
    if state != IDLE:
        return None

    activating_types = {"CAMPAIGN_EXPANSION", "BEHAVIOUR_CHANGE", "FUNDING_SHIFT"}
    matches = [
        a for a in bundle.assessments
        if a.assessment_type in activating_types
        and a.confidence in ("MEDIUM", "HIGH")
    ]
    if not matches:
        return None

    best  = max(matches, key=lambda a: _CONF_ORDER.get(a.confidence, 0))
    conf  = _lower_conf(best.confidence, "MEDIUM")  # cap at MEDIUM from IDLE
    supporting = [
        ForecastEvidenceItem(
            label  = a.headline,
            detail = f"Assessment: {a.assessment_type} ({a.confidence})",
            source = "ASSESSMENT",
            weight = _CONF_ORDER.get(a.confidence, 0),
        )
        for a in matches
    ]
    return LifecycleForecast(
        forecast_id           = _fid(bundle.operator_id, "TRANSITION_LIKELY", ts),
        operator_id           = bundle.operator_id,
        forecast_type         = "TRANSITION_LIKELY",
        current_state         = IDLE,
        predicted_next_state  = OBSERVING,
        forecast_reason       = (
            f"Activity signals detected ({best.assessment_type}). "
            f"Operator may be initiating a new operational phase."
        ),
        confidence            = conf,
        supporting_assessments = [a.assessment_type for a in matches],
        supporting_evidence   = supporting,
        expected_window       = WINDOW_VERY_LONG,
        historical_observations = profile.total_observations,
        generated_at          = ts,
        expires_at            = ts + 3600,
        gap_notes             = profile.evidence_gap_notes,
    )


def _rule_observing_to_armed(
    bundle: OperatorAssessmentBundle,
    profile, change_report,
    state: str, ts: int,
) -> LifecycleForecast | None:
    """
    Rule: OBSERVING → ARMED
    Trigger:
      — current state is OBSERVING
      — assessment is CAMPAIGN_EXPANSION (scale building)
      — funding dimension is STABLE or HIGH confidence (treasury loaded)
      — infrastructure dimension is STABLE (execution infra ready)
    Confidence: min(assessment_conf, behaviour_conf).
    Window: derived from historical avg_launch_delay_s if available.
    """
    if state != OBSERVING:
        return None

    expansion = bundle.by_type("CAMPAIGN_EXPANSION")
    if expansion is None or expansion.confidence not in ("MEDIUM", "HIGH"):
        return None

    # Check funding + infra are stable (look for contradictions in assessment)
    has_funding_contradiction = any(
        "funding" in e.label.lower() for e in expansion.contradictory_evidence
    )
    has_infra_contradiction = any(
        "infra" in e.label.lower() for e in expansion.contradictory_evidence
    )

    behaviour_conf = profile.overall_confidence
    base_conf      = _lower_conf(expansion.confidence, behaviour_conf)

    supporting: list[ForecastEvidenceItem] = [
        ForecastEvidenceItem(
            label  = expansion.headline,
            detail = f"Assessment: CAMPAIGN_EXPANSION ({expansion.confidence})",
            source = "ASSESSMENT",
            weight = 2.0,
        )
    ]
    contradictory: list[ForecastEvidenceItem] = []

    # Timing from behaviour profile
    delay_fact = _get_fact(profile, "launch", "avg_launch_delay_s")
    window     = WINDOW_UNKNOWN
    hist_obs   = 0
    hist_basis = ""
    if delay_fact and delay_fact.confidence in ("MEDIUM", "HIGH"):
        window     = _timing_window(delay_fact.raw)
        hist_obs   = delay_fact.observations
        hist_basis = (
            f"Based on {hist_obs} historical launches "
            f"with average launch delay {delay_fact.value}."
        )
        supporting.append(ForecastEvidenceItem(
            label  = f"Historical launch delay: {delay_fact.value}",
            detail = f"{hist_obs} observations, stability: {delay_fact.stability}",
            source = "TIMING",
            weight = 1.5,
        ))
    else:
        window = WINDOW_MEDIUM

    if has_funding_contradiction:
        contradictory.append(ForecastEvidenceItem(
            label  = "Funding pattern also changed",
            detail = "Funding shift may indicate reconfiguration rather than imminent activation.",
            source = "ASSESSMENT",
            weight = 0.8,
        ))
    if has_infra_contradiction:
        contradictory.append(ForecastEvidenceItem(
            label  = "Infrastructure also shifted",
            detail = "Infrastructure change may delay activation.",
            source = "ASSESSMENT",
            weight = 0.5,
        ))

    conf = _penalise(base_conf, len(contradictory))

    return LifecycleForecast(
        forecast_id            = _fid(bundle.operator_id, "TRANSITION_LIKELY", ts),
        operator_id            = bundle.operator_id,
        forecast_type          = "TRANSITION_LIKELY",
        current_state          = OBSERVING,
        predicted_next_state   = ARMED,
        forecast_reason        = (
            "Campaign expansion detected with stable funding. "
            "Operator is building toward an armed state."
        ),
        confidence             = conf,
        supporting_assessments = ["CAMPAIGN_EXPANSION"],
        supporting_evidence    = supporting,
        contradictory_evidence = contradictory,
        expected_window        = window,
        historical_observations = hist_obs,
        historical_basis       = hist_basis,
        generated_at           = ts,
        expires_at             = ts + 1800,
        gap_notes              = profile.evidence_gap_notes,
    )


def _rule_observing_stable(
    bundle: OperatorAssessmentBundle,
    profile, change_report,
    state: str, ts: int,
) -> LifecycleForecast | None:
    """
    Rule: OBSERVING → OBSERVING (remains)
    Trigger:
      — current state is OBSERVING
      — assessment is BASELINE_BEHAVIOUR or FUNDING_SHIFT (disruption without expansion)
      — no CAMPAIGN_EXPANSION present
    Confidence: LOW (stable states are less newsworthy).
    Rationale: operator is active but not building toward ARMED.
    """
    if state != OBSERVING:
        return None
    if bundle.by_type("CAMPAIGN_EXPANSION") is not None:
        return None   # expansion rule takes priority

    baseline  = bundle.by_type("BASELINE_BEHAVIOUR")
    fund_shift = bundle.by_type("FUNDING_SHIFT")
    if baseline is None and fund_shift is None:
        return None

    primary   = baseline or fund_shift
    conf      = _lower_conf(primary.confidence, "LOW")   # cap at LOW

    supporting = [
        ForecastEvidenceItem(
            label  = primary.headline,
            detail = f"Assessment: {primary.assessment_type} ({primary.confidence})",
            source = "ASSESSMENT",
            weight = 1.0,
        )
    ]
    contra = []
    if fund_shift:
        contra.append(ForecastEvidenceItem(
            label  = "Funding pattern changed",
            detail = "Funding shift may precede state change — monitoring advised.",
            source = "ASSESSMENT",
            weight = 0.5,
        ))

    return LifecycleForecast(
        forecast_id            = _fid(bundle.operator_id, "STATE_STABLE", ts),
        operator_id            = bundle.operator_id,
        forecast_type          = "STATE_STABLE",
        current_state          = OBSERVING,
        predicted_next_state   = OBSERVING,
        forecast_reason        = "No expansion signals detected. Operator expected to remain in observation phase.",
        confidence             = conf,
        supporting_assessments = [primary.assessment_type],
        supporting_evidence    = supporting,
        contradictory_evidence = contra,
        expected_window        = WINDOW_UNKNOWN,
        historical_observations = profile.total_observations,
        generated_at           = ts,
        expires_at             = ts + 3600,
        gap_notes              = profile.evidence_gap_notes,
    )


def _rule_armed_to_active(
    bundle: OperatorAssessmentBundle,
    profile, change_report,
    state: str, ts: int,
) -> LifecycleForecast | None:
    """
    Rule: ARMED → ACTIVE
    Trigger:
      — current state is ARMED
      — assessment is NOT INSUFFICIENT_EVIDENCE
    Confidence: assessment_conf ∩ behaviour_conf ∩ timing_stability.
    Window: derived from avg_fanout_to_create_s (the most proximate timing signal).
    Rationale: ARMED means high-confidence evidence of imminent activity.
               This is the highest-value forecast for analyst operations.
    """
    if state != ARMED:
        return None

    # Assessments that reinforce the armed state
    reinforcing_types = {
        "CAMPAIGN_EXPANSION", "BEHAVIOUR_CHANGE",
        "BASELINE_BEHAVIOUR",  # even stable operators fire when ARMED
        "RETURN_TO_BASELINE",
    }
    reinforcing = [
        a for a in bundle.assessments
        if a.assessment_type in reinforcing_types
        and a.confidence in ("LOW", "MEDIUM", "HIGH")
    ]

    # Assessments that limit confidence
    limiting_types = {"INFRASTRUCTURE_SHIFT", "FUNDING_SHIFT"}
    limiting = [
        a for a in bundle.assessments
        if a.assessment_type in limiting_types
    ]

    # Base confidence: assessment confidence, capped at HIGH
    if reinforcing:
        best_reinforce = max(reinforcing, key=lambda a: _CONF_ORDER.get(a.confidence, 0))
        base_conf      = best_reinforce.confidence
    else:
        # No reinforcing assessment but state is ARMED — still produce forecast
        base_conf = "MEDIUM"

    behaviour_conf = profile.overall_confidence
    base_conf      = _lower_conf(base_conf, behaviour_conf if behaviour_conf != "INSUFFICIENT" else "LOW")

    # Timing from fanout_to_create behaviour fact
    f2c_fact   = _get_fact(profile, "launch", "avg_fanout_to_create_s")
    delay_fact = _get_fact(profile, "launch", "avg_launch_delay_s")
    timing_fact = f2c_fact or delay_fact
    window     = WINDOW_SHORT   # default for ARMED
    hist_obs   = 0
    hist_basis = ""

    supporting: list[ForecastEvidenceItem] = []
    contradictory: list[ForecastEvidenceItem] = []

    if timing_fact and timing_fact.confidence in ("MEDIUM", "HIGH"):
        window     = _timing_window(timing_fact.raw)
        hist_obs   = timing_fact.observations
        hist_basis = (
            f"Based on {hist_obs} historical launches "
            f"with average timing {timing_fact.value} ({timing_fact.stability} stability)."
        )
        weight = 2.0 if timing_fact.stability == "STABLE" else 1.0
        supporting.append(ForecastEvidenceItem(
            label  = f"Historical timing: {timing_fact.value}",
            detail = f"{hist_obs} observations, stability: {timing_fact.stability}",
            source = "TIMING",
            weight = weight,
        ))
        if timing_fact.stability == "VARIABLE":
            contradictory.append(ForecastEvidenceItem(
                label  = "Timing is historically variable",
                detail = f"Coefficient of variation is HIGH — window estimate is approximate.",
                source = "TIMING",
                weight = 0.5,
            ))

    for a in reinforcing:
        supporting.append(ForecastEvidenceItem(
            label  = a.headline,
            detail = f"Assessment: {a.assessment_type} ({a.confidence})",
            source = "ASSESSMENT",
            weight = _CONF_ORDER.get(a.confidence, 0),
        ))

    for a in limiting:
        contradictory.append(ForecastEvidenceItem(
            label  = a.headline,
            detail = f"Assessment: {a.assessment_type} ({a.confidence}) — may delay transition.",
            source = "ASSESSMENT",
            weight = 0.8,
        ))

    conf = _penalise(base_conf, len(contradictory))

    return LifecycleForecast(
        forecast_id            = _fid(bundle.operator_id, "TRANSITION_LIKELY", ts),
        operator_id            = bundle.operator_id,
        forecast_type          = "TRANSITION_LIKELY",
        current_state          = ARMED,
        predicted_next_state   = ACTIVE,
        forecast_reason        = (
            "Operator is in ARMED state. High-confidence evidence indicates "
            "imminent operational activity."
        ),
        confidence             = conf,
        supporting_assessments = [a.assessment_type for a in reinforcing],
        supporting_evidence    = supporting,
        contradictory_evidence = contradictory,
        expected_window        = window,
        historical_observations = hist_obs,
        historical_basis       = hist_basis,
        generated_at           = ts,
        expires_at             = ts + 1800,
        gap_notes              = profile.evidence_gap_notes,
    )


def _rule_active_to_completed(
    bundle: OperatorAssessmentBundle,
    profile, change_report,
    state: str, ts: int,
) -> LifecycleForecast | None:
    """
    Rule: ACTIVE → COMPLETED
    Trigger: current state is ACTIVE.
    Confidence: capped at MEDIUM (we cannot know when the operation concludes).
    Window: avg_migration_timing_s if available (migration = operational completion).
    """
    if state != ACTIVE:
        return None

    migration_fact = _get_fact(profile, "launch", "avg_migration_timing_s")
    window   = WINDOW_LONG
    hist_obs = 0
    hist_basis = ""
    supporting: list[ForecastEvidenceItem] = []

    if migration_fact and migration_fact.confidence in ("MEDIUM", "HIGH"):
        window     = _timing_window(migration_fact.raw)
        hist_obs   = migration_fact.observations
        hist_basis = (
            f"Based on {hist_obs} historical launches "
            f"with average time to migration {migration_fact.value}."
        )
        supporting.append(ForecastEvidenceItem(
            label  = f"Historical time to migration: {migration_fact.value}",
            detail = f"{hist_obs} observations",
            source = "TIMING",
            weight = 1.5,
        ))

    # Assessment context
    for a in bundle.assessments:
        if a.assessment_type in ("CAMPAIGN_CONTRACTION", "RETURN_TO_BASELINE"):
            supporting.append(ForecastEvidenceItem(
                label  = a.headline,
                detail = f"Assessment: {a.assessment_type} ({a.confidence})",
                source = "ASSESSMENT",
                weight = 1.0,
            ))

    behaviour_conf = profile.overall_confidence
    conf = _lower_conf("MEDIUM", behaviour_conf if behaviour_conf != "INSUFFICIENT" else "LOW")

    return LifecycleForecast(
        forecast_id            = _fid(bundle.operator_id, "TRANSITION_POSSIBLE", ts),
        operator_id            = bundle.operator_id,
        forecast_type          = "TRANSITION_POSSIBLE",
        current_state          = ACTIVE,
        predicted_next_state   = COMPLETED,
        forecast_reason        = "Operator is ACTIVE. Transition to COMPLETED expected as operation concludes.",
        confidence             = conf,
        supporting_assessments = [],
        supporting_evidence    = supporting,
        expected_window        = window,
        historical_observations = hist_obs,
        historical_basis       = hist_basis,
        generated_at           = ts,
        expires_at             = ts + 3600,
        gap_notes              = profile.evidence_gap_notes,
    )


def _rule_completed_archived(
    bundle: OperatorAssessmentBundle,
    profile, change_report,
    state: str, ts: int,
) -> LifecycleForecast | None:
    """
    Rule: COMPLETED → ARCHIVED  (or IDLE if re-activation signals present)
    Trigger: current state is COMPLETED.
    If expansion signals: forecast IDLE (operator may re-activate).
    Otherwise: forecast ARCHIVED.
    """
    if state != COMPLETED:
        return None

    expansion  = bundle.by_type("CAMPAIGN_EXPANSION")
    change     = bundle.by_type("BEHAVIOUR_CHANGE")
    re_activate = expansion or change
    next_state  = IDLE if re_activate else ARCHIVED

    supporting: list[ForecastEvidenceItem] = []
    if re_activate:
        supporting.append(ForecastEvidenceItem(
            label  = (re_activate.headline),
            detail = f"Assessment: {re_activate.assessment_type} ({re_activate.confidence})",
            source = "ASSESSMENT",
            weight = 1.0,
        ))

    conf = "LOW"

    return LifecycleForecast(
        forecast_id            = _fid(bundle.operator_id, "TRANSITION_POSSIBLE", ts),
        operator_id            = bundle.operator_id,
        forecast_type          = "TRANSITION_POSSIBLE",
        current_state          = COMPLETED,
        predicted_next_state   = next_state,
        forecast_reason        = (
            "Operation is complete. "
            + ("Re-activation signals detected." if re_activate
               else "No re-activation signals; archival expected.")
        ),
        confidence             = conf,
        supporting_assessments = [re_activate.assessment_type] if re_activate else [],
        supporting_evidence    = supporting,
        expected_window        = WINDOW_UNKNOWN,
        historical_observations = profile.total_observations,
        generated_at           = ts,
        expires_at             = ts + 7200,
        gap_notes              = profile.evidence_gap_notes,
    )


def _rule_no_forecast(
    bundle: OperatorAssessmentBundle,
    profile, change_report,
    state: str, ts: int,
) -> LifecycleForecast:
    """
    Fallback: emit INSUFFICIENT_EVIDENCE when no other rule matched.
    """
    return _insufficient_forecast(
        bundle.operator_id, state,
        f"No applicable forecast rule for state {state!r}. "
        "Insufficient evidence or unrecognised lifecycle configuration.",
        ts,
    )


# ── Rule registry (ordered — first match wins) ────────────────────────────────

_RULES = [
    _rule_insufficient,
    _rule_idle_to_observing,
    _rule_observing_to_armed,
    _rule_observing_stable,
    _rule_armed_to_active,
    _rule_active_to_completed,
    _rule_completed_archived,
]


# ── Forecast Engine ───────────────────────────────────────────────────────────

class ForecastEngine:
    """
    Produces a LifecycleForecast by combining Assessment, Behaviour, and
    BehaviourChange outputs.

    The engine never opens a database connection.  All DB access is delegated
    to the injected sub-engines.  Fail-open: any exception returns a safe
    INSUFFICIENT_EVIDENCE forecast.

    Usage::

        engine   = ForecastEngine(assessment_engine, behaviour_engine, change_engine)
        forecast = engine.forecast(operator_id, current_lifecycle_state)
    """

    def __init__(
        self,
        assessment_engine,
        behaviour_engine,
        change_engine,
    ) -> None:
        self._assess = assessment_engine
        self._beh    = behaviour_engine
        self._chg    = change_engine

    def forecast(
        self,
        operator_id:        str,
        lifecycle_state:    str,
        treasury_wallets:   list[str] | None = None,
    ) -> LifecycleForecast:
        ts = int(time.time())
        try:
            return self._forecast_inner(operator_id, lifecycle_state, treasury_wallets, ts)
        except Exception as exc:
            return _insufficient_forecast(
                operator_id, lifecycle_state,
                f"Forecast generation failed: {type(exc).__name__}",
                ts,
            )

    def _forecast_inner(
        self,
        operator_id:      str,
        lifecycle_state:  str,
        treasury_wallets: list[str] | None,
        ts:               int,
    ) -> LifecycleForecast:
        # Validate state
        if lifecycle_state not in STATES_VALID:
            return _insufficient_forecast(
                operator_id, lifecycle_state,
                f"Unrecognised lifecycle state: {lifecycle_state!r}",
                ts,
            )

        # ── Layer 1: Assessment (consumes Behaviour + Change + Similarity) ────
        bundle  = self._assess.assess(operator_id, treasury_wallets)

        # ── Layer 2: Behaviour profile (for timing facts) ─────────────────────
        profile = self._beh.compute(operator_id, treasury_wallets)

        # ── Layer 3: Behaviour change (for drift context) ─────────────────────
        change  = self._chg.compare(operator_id, treasury_wallets)

        # ── Apply rules ───────────────────────────────────────────────────────
        result = None
        for rule in _RULES:
            result = rule(bundle, profile, change, lifecycle_state, ts)
            if result is not None:
                break
        if result is None:
            # Fallback (should never be reached)
            result = _rule_no_forecast(bundle, profile, change, lifecycle_state, ts)

        # Stamp input fingerprints and recompute the content-addressed
        # forecast_fingerprint from the actual rule output (fingerprints are
        # pure functions of the inputs, so rules don't need to know about them).
        result.assessment_fingerprint = bundle.fingerprint
        result.behaviour_fingerprint  = profile.fingerprint
        result.change_fingerprint     = change.fingerprint
        result.forecast_fingerprint   = _compute_forecast_fingerprint(
            operator_id            = result.operator_id,
            current_state          = result.current_state,
            predicted_next_state   = result.predicted_next_state,
            confidence             = result.confidence,
            expected_window        = result.expected_window,
            assessment_fingerprint = result.assessment_fingerprint,
            behaviour_fingerprint  = result.behaviour_fingerprint,
            change_fingerprint     = result.change_fingerprint,
        )
        return result

    @staticmethod
    def list_operator_ids(ops_db_path: str, limit: int = 50) -> list[str]:
        """
        Bounded, read-only lookup of recently-updated operator_ids.

        This is the single sanctioned place the Forecast layer touches a raw
        table (the `operators` roster itself, not intelligence data). Callers
        (routes, inbox adapters) must use this instead of opening their own
        sqlite3 connection, so the DB-access surface stays in one place.
        """
        import sqlite3
        try:
            with sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5) as conn:
                conn.execute("PRAGMA query_only=ON")
                rows = conn.execute(
                    "SELECT operator_id FROM operators ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []

    def platform_summary(self, operators: list[tuple[str, str]]) -> dict:
        """
        Platform-level forecast summary.

        operators: list of (operator_id, lifecycle_state) tuples.
        Bounded to 50 operators.
        """
        ts       = int(time.time())
        results  = []
        errors   = 0
        for op_id, state in operators[:50]:
            try:
                f = self.forecast(op_id, state)
                results.append(f)
            except Exception:
                errors += 1

        counts: dict[str, int] = {}
        for f in results:
            key = f"{f.current_state}→{f.predicted_next_state or '?'}"
            counts[key] = counts.get(key, 0) + 1

        armed_forecasts = [
            f for f in results
            if f.current_state == ARMED
            and f.predicted_next_state == ACTIVE
            and f.confidence in ("MEDIUM", "HIGH")
        ]

        return {
            "computed_at":          ts,
            "operators_assessed":   len(results),
            "operators_errored":    errors,
            "transition_counts":    counts,
            "armed_active_count":   len(armed_forecasts),
            "armed_active_forecasts": [f.to_dict() for f in armed_forecasts[:10]],
        }
