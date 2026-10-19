"""
Sprint X13 — Intelligence Assessment Engine.

Synthesises existing intelligence layers into explainable assessments.

Architecture:
    BehaviourProfile         (X10)  — what the operator normally does
    BehaviourChangeReport    (X11)  — how they differ from their baseline
    SimilaritySnapshot       (X12)  — which operators behave similarly
    LifecycleSnapshot        (X4)   — where they are in the operational lifecycle

This module ORCHESTRATES those layers. It never duplicates their DB queries
or re-implements their logic. All reasoning happens in-memory over the
pre-computed outputs of those engines.

DB-safety rules (same as X12):
  — No writes during computation
  — One bulk read per engine (delegated to engine.compute())
  — Connection closed before reasoning starts (delegated)
  — Fail open on any error
  — No page-load computation (caller must manage refresh cadence)

Assessment types:
    BASELINE_BEHAVIOUR        Operator behaving within established norms.
    BEHAVIOUR_CHANGE          Meaningful change from historical baseline.
    CAMPAIGN_EXPANSION        Campaign size grown, funding/infra stable.
    CAMPAIGN_CONTRACTION      Campaign size shrunk, funding/infra stable.
    FUNDING_SHIFT             Funding pattern materially different.
    INFRASTRUCTURE_SHIFT      Operational infrastructure materially changed.
    SIMILARITY_OBSERVED       HIGH/VERY_HIGH behavioural similarity to another operator.
    RETURN_TO_BASELINE        Previous change reversed; drift returned to STABLE/NONE.
    INSUFFICIENT_EVIDENCE     Not enough observations to form any assessment.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# ── Confidence ordering ───────────────────────────────────────────────────────

_CONF_ORDER = {"INSUFFICIENT": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

ASSESSMENT_TYPES = frozenset({
    "BASELINE_BEHAVIOUR",
    "BEHAVIOUR_CHANGE",
    "CAMPAIGN_EXPANSION",
    "CAMPAIGN_CONTRACTION",
    "FUNDING_SHIFT",
    "INFRASTRUCTURE_SHIFT",
    "SIMILARITY_OBSERVED",
    "RETURN_TO_BASELINE",
    "INSUFFICIENT_EVIDENCE",
})

# Minimum dimension confidence to contribute to an assessment
_MIN_DIM_CONF = "LOW"
# Minimum overall confidence for the assessment to be non-INSUFFICIENT
_MIN_ASSESS_CONF = "LOW"

# Deviation thresholds that count as "meaningful"
_MEANINGFUL_DEVIATION = {"MODERATE", "HIGH", "VERY_HIGH"}
_STRONG_DEVIATION     = {"HIGH", "VERY_HIGH"}


# ── Evidence items ────────────────────────────────────────────────────────────

@dataclass
class EvidenceItem:
    label:    str
    detail:   str
    source:   str          # which layer produced this (BEHAVIOUR / CHANGE / SIMILARITY / LIFECYCLE)
    weight:   float = 1.0  # relative weight; used only to order display

    def to_dict(self) -> dict:
        return {
            "label":  self.label,
            "detail": self.detail,
            "source": self.source,
            "weight": self.weight,
        }


# ── Assessment dataclass ──────────────────────────────────────────────────────

@dataclass
class Assessment:
    assessment_id:        str          # <operator_id>:<type>:<generated_at>
    operator_id:          str
    assessment_type:      str
    headline:             str
    summary:              str
    confidence:           str          # INSUFFICIENT | LOW | MEDIUM | HIGH
    supporting_evidence:  list[EvidenceItem] = field(default_factory=list)
    contradictory_evidence: list[EvidenceItem] = field(default_factory=list)
    evidence_count:       int = 0
    generated_at:         int = field(default_factory=lambda: int(time.time()))
    expires_at:           int = field(default_factory=lambda: int(time.time()) + 3600)
    gap_notes:            list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.assessment_type not in ASSESSMENT_TYPES:
            raise ValueError(f"Unknown assessment type: {self.assessment_type!r}")
        self.evidence_count = len(self.supporting_evidence) + len(self.contradictory_evidence)

    def to_dict(self) -> dict:
        return {
            "assessment_id":          self.assessment_id,
            "operator_id":            self.operator_id,
            "assessment_type":        self.assessment_type,
            "headline":               self.headline,
            "summary":                self.summary,
            "confidence":             self.confidence,
            "supporting_evidence":    [e.to_dict() for e in self.supporting_evidence],
            "contradictory_evidence": [e.to_dict() for e in self.contradictory_evidence],
            "evidence_count":         self.evidence_count,
            "generated_at":           self.generated_at,
            "expires_at":             self.expires_at,
            "gap_notes":              self.gap_notes,
        }


@dataclass
class OperatorAssessmentBundle:
    """All assessments produced for one operator in a single pass."""
    operator_id:   str
    assessments:   list[Assessment]
    generated_at:  int = field(default_factory=lambda: int(time.time()))
    error:         str | None = None
    available:     bool = True

    def by_type(self, assessment_type: str) -> Assessment | None:
        for a in self.assessments:
            if a.assessment_type == assessment_type:
                return a
        return None

    def highest_confidence(self) -> Assessment | None:
        if not self.assessments:
            return None
        return max(
            self.assessments,
            key=lambda a: _CONF_ORDER.get(a.confidence, 0),
        )

    def to_dict(self) -> dict:
        return {
            "operator_id":  self.operator_id,
            "available":    self.available,
            "error":        self.error,
            "generated_at": self.generated_at,
            "count":        len(self.assessments),
            "assessments":  [a.to_dict() for a in self.assessments],
            "fingerprint":  self.fingerprint,
        }

    @property
    def fingerprint(self) -> str:
        """
        Deterministic content hash, excluding generated_at/expires_at/
        assessment_id (all timestamp-derived). Two bundles with identical
        assessment content produce the same fingerprint regardless of when
        each was computed — this is what lets downstream layers (Forecast)
        detect "assessment changed materially" without comparing timestamps.
        """
        import hashlib
        import json
        if not self.available:
            return "unavailable"
        payload = {
            "operator_id": self.operator_id,
            "assessments": [
                {
                    "assessment_type": a.assessment_type,
                    "headline":        a.headline,
                    "summary":         a.summary,
                    "confidence":      a.confidence,
                    "supporting_evidence":    [e.to_dict() for e in a.supporting_evidence],
                    "contradictory_evidence": [e.to_dict() for e in a.contradictory_evidence],
                }
                for a in sorted(self.assessments, key=lambda a: a.assessment_type)
            ],
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lower_conf(a: str, b: str) -> str:
    return a if _CONF_ORDER.get(a, 0) <= _CONF_ORDER.get(b, 0) else b


def _raise_conf(a: str, b: str) -> str:
    return a if _CONF_ORDER.get(a, 0) >= _CONF_ORDER.get(b, 0) else b


def _penalise_conf(conf: str, n_contradictions: int) -> str:
    """
    Each contradiction drops confidence by one band.
    Two or more contradictions drop by two bands.
    """
    order = ["INSUFFICIENT", "LOW", "MEDIUM", "HIGH"]
    idx = order.index(conf) if conf in order else 0
    drop = min(n_contradictions, 2)
    return order[max(0, idx - drop)]


def _assess_id(operator_id: str, atype: str, ts: int) -> str:
    return f"{operator_id}:{atype}:{ts}"


def _get_dim_fact(profile, dimension_key: str, fact_key: str):
    """
    Extract a BehaviourFact from a BehaviourProfile by dimension + fact key.
    Returns None if not found.
    """
    for dim in profile.dimensions:
        if dim.key == dimension_key:
            for fact in dim.facts:
                if fact.key == fact_key:
                    return fact
    return None


def _dim_confidence(profile, dimension_key: str) -> str:
    for dim in profile.dimensions:
        if dim.key == dimension_key:
            return dim.overall_confidence
    return "INSUFFICIENT"


def _dim_change(report, dimension_key: str):
    """Return the DimensionChange for a key, or None."""
    for dc in report.dimension_changes:
        if dc.key == dimension_key:
            return dc
    return None


def _fact_comparison(report, dimension_key: str, fact_key: str):
    dc = _dim_change(report, dimension_key)
    if dc is None:
        return None
    for fc in dc.comparisons:
        if fc.key == fact_key:
            return fc
    return None


# ── Reasoning rules ───────────────────────────────────────────────────────────
# Every rule is a plain function that accepts pre-computed intelligence objects
# and returns an Assessment or None. All logic is documented inline.

def _rule_insufficient_evidence(
    operator_id: str, profile, change_report, ts: int
) -> Assessment | None:
    """
    Rule: INSUFFICIENT_EVIDENCE
    Trigger: overall_confidence == INSUFFICIENT on the behaviour profile.
    Rationale: we cannot say anything meaningful without baseline confidence.
    """
    if profile.overall_confidence != "INSUFFICIENT":
        return None
    return Assessment(
        assessment_id   = _assess_id(operator_id, "INSUFFICIENT_EVIDENCE", ts),
        operator_id     = operator_id,
        assessment_type = "INSUFFICIENT_EVIDENCE",
        headline        = "Insufficient observations to assess this operator.",
        summary         = (
            f"Only {profile.total_observations} observation(s) recorded. "
            f"A minimum of 3 is required before any assessment can be produced."
        ),
        confidence      = "INSUFFICIENT",
        gap_notes       = profile.evidence_gap_notes,
        generated_at    = ts,
        expires_at      = ts + 3600,
    )


def _rule_baseline_behaviour(
    operator_id: str, profile, change_report, ts: int
) -> Assessment | None:
    """
    Rule: BASELINE_BEHAVIOUR
    Trigger: overall_drift is STABLE (or no drift available) AND profile
             overall_confidence >= LOW.
    Rationale: the operator is doing what it has historically done.
    """
    if profile.overall_confidence == "INSUFFICIENT":
        return None
    drift = getattr(change_report, "overall_drift", "STABLE")
    if drift not in ("STABLE", "INSUFFICIENT_EVIDENCE"):
        return None

    supporting: list[EvidenceItem] = []
    contradictory: list[EvidenceItem] = []

    for dim in profile.dimensions:
        if dim.overall_confidence in ("LOW", "MEDIUM", "HIGH"):
            supporting.append(EvidenceItem(
                label  = f"{dim.label} established",
                detail = f"Observed across {dim.overall_confidence.lower()} confidence observations.",
                source = "BEHAVIOUR",
                weight = _CONF_ORDER.get(dim.overall_confidence, 0),
            ))

    for dc in change_report.dimension_changes:
        if dc.drift == "CHANGED" and dc.overall_confidence in ("LOW", "MEDIUM", "HIGH"):
            contradictory.append(EvidenceItem(
                label  = f"{dc.label} shows minor variation",
                detail = dc.summary,
                source = "CHANGE",
                weight = 0.5,
            ))

    conf = profile.overall_confidence
    if drift == "INSUFFICIENT_EVIDENCE":
        conf = _lower_conf(conf, "MEDIUM")

    return Assessment(
        assessment_id          = _assess_id(operator_id, "BASELINE_BEHAVIOUR", ts),
        operator_id            = operator_id,
        assessment_type        = "BASELINE_BEHAVIOUR",
        headline               = "Operator behaviour is consistent with historical baseline.",
        summary                = (
            "All observed dimensions are within normal variation. "
            "No material departure from established behaviour patterns detected."
        ),
        confidence             = _penalise_conf(conf, len(contradictory)),
        supporting_evidence    = supporting,
        contradictory_evidence = contradictory,
        gap_notes              = profile.evidence_gap_notes,
        generated_at           = ts,
        expires_at             = ts + 3600,
    )


def _rule_return_to_baseline(
    operator_id: str, profile, change_report, ts: int
) -> Assessment | None:
    """
    Rule: RETURN_TO_BASELINE
    Trigger: overall_drift is STABLE but >=1 dimension previously showed CHANGED
             (captured in drift_summary as a STABLE dimension that has a 'was CHANGED' note).
    Practical proxy: overall_drift == STABLE and at least one dimension's summary
                     mentions 'no longer' or the dimension fact deviations are all NONE.
    Rationale: the operator returned to prior behaviour after a period of change.
    """
    if profile.overall_confidence == "INSUFFICIENT":
        return None
    drift = getattr(change_report, "overall_drift", None)
    if drift != "STABLE":
        return None

    # Inspect individual dimensions — if any fact deviation is NONE but the
    # historical window has high observations, that's a stability signal.
    stable_dims = [
        dc for dc in change_report.dimension_changes
        if dc.drift == "STABLE"
        and dc.overall_confidence in ("MEDIUM", "HIGH")
        and any(fc.deviation == "NONE" for fc in dc.comparisons)
    ]
    if len(stable_dims) < 2:
        return None  # nothing meaningful to say beyond BASELINE_BEHAVIOUR

    supporting = [
        EvidenceItem(
            label  = f"{dc.label} stable",
            detail = f"Deviation: NONE across {len(dc.comparisons)} measured facts.",
            source = "CHANGE",
            weight = 1.0,
        )
        for dc in stable_dims
    ]
    conf = _lower_conf(profile.overall_confidence, change_report.overall_confidence
                       if hasattr(change_report, "overall_confidence") else "MEDIUM")

    return Assessment(
        assessment_id       = _assess_id(operator_id, "RETURN_TO_BASELINE", ts),
        operator_id         = operator_id,
        assessment_type     = "RETURN_TO_BASELINE",
        headline            = "Operator behaviour has returned to historical norms.",
        summary             = (
            "Dimensions previously deviating are now consistent with the historical baseline. "
            "No ongoing departure detected."
        ),
        confidence          = conf,
        supporting_evidence = supporting,
        gap_notes           = profile.evidence_gap_notes,
        generated_at        = ts,
        expires_at          = ts + 3600,
    )


def _rule_campaign_expansion(
    operator_id: str, profile, change_report, ts: int
) -> Assessment | None:
    """
    Rule: CAMPAIGN_EXPANSION
    Trigger:
      — campaign dimension changed (MODERATE/HIGH/VERY_HIGH deviation)
      — direction: current campaign_size > baseline campaign_size
      — funding dimension STABLE or INSUFFICIENT
      — infrastructure (operational) dimension STABLE or INSUFFICIENT
    Rationale: scale grew without corresponding change in funding structure or
               operational infrastructure, suggesting deliberate expansion.
    """
    campaign_dc = _dim_change(change_report, "campaign")
    if campaign_dc is None or campaign_dc.drift != "CHANGED":
        return None
    if campaign_dc.overall_confidence not in ("LOW", "MEDIUM", "HIGH"):
        return None

    # Confirm campaign size increased (not just changed)
    size_fc = next(
        (fc for fc in campaign_dc.comparisons
         if fc.key in ("campaign_size", "launches_per_campaign", "total_launches")
         and fc.deviation in _MEANINGFUL_DEVIATION),
        None,
    )
    if size_fc is None:
        return None

    # Check direction: current > historical
    try:
        if float(size_fc.current_raw) <= float(size_fc.historical_raw):
            return None
    except (TypeError, ValueError):
        return None

    # Funding must be stable or unknown
    funding_dc = _dim_change(change_report, "funding")
    funding_stable = (
        funding_dc is None
        or funding_dc.drift in ("STABLE", "INSUFFICIENT_EVIDENCE")
    )
    infra_dc = _dim_change(change_report, "operational")
    infra_stable = (
        infra_dc is None
        or infra_dc.drift in ("STABLE", "INSUFFICIENT_EVIDENCE")
    )

    supporting: list[EvidenceItem] = [
        EvidenceItem(
            label  = "Campaign scale increased",
            detail = (
                f"{size_fc.label}: "
                f"{size_fc.historical_value} → {size_fc.current_value} "
                f"(deviation: {size_fc.deviation})"
            ),
            source = "CHANGE",
            weight = 2.0,
        )
    ]
    contradictory: list[EvidenceItem] = []

    if funding_stable:
        supporting.append(EvidenceItem(
            label  = "Funding pattern unchanged",
            detail = "Treasury provisioning structure shows no material deviation.",
            source = "CHANGE",
            weight = 1.0,
        ))
    else:
        contradictory.append(EvidenceItem(
            label  = "Funding pattern also changed",
            detail = (funding_dc.summary if funding_dc else ""),
            source = "CHANGE",
            weight = 0.8,
        ))

    if infra_stable:
        supporting.append(EvidenceItem(
            label  = "Infrastructure stable",
            detail = "Operational timing and sub-provisioner count unchanged.",
            source = "CHANGE",
            weight = 1.0,
        ))
    else:
        contradictory.append(EvidenceItem(
            label  = "Infrastructure also shifted",
            detail = (infra_dc.summary if infra_dc else ""),
            source = "CHANGE",
            weight = 0.8,
        ))

    base_conf = _lower_conf(campaign_dc.overall_confidence, size_fc.confidence)
    conf      = _penalise_conf(base_conf, len(contradictory))

    return Assessment(
        assessment_id          = _assess_id(operator_id, "CAMPAIGN_EXPANSION", ts),
        operator_id            = operator_id,
        assessment_type        = "CAMPAIGN_EXPANSION",
        headline               = "Operator is expanding campaign scale.",
        summary                = (
            f"Campaign size has increased "
            f"(from {size_fc.historical_value} to {size_fc.current_value}) "
            f"while funding and infrastructure remain stable."
        ),
        confidence             = conf,
        supporting_evidence    = supporting,
        contradictory_evidence = contradictory,
        gap_notes              = profile.evidence_gap_notes,
        generated_at           = ts,
        expires_at             = ts + 3600,
    )


def _rule_campaign_contraction(
    operator_id: str, profile, change_report, ts: int
) -> Assessment | None:
    """
    Rule: CAMPAIGN_CONTRACTION
    Trigger: same as EXPANSION but current campaign_size < baseline.
    """
    campaign_dc = _dim_change(change_report, "campaign")
    if campaign_dc is None or campaign_dc.drift != "CHANGED":
        return None
    if campaign_dc.overall_confidence not in ("LOW", "MEDIUM", "HIGH"):
        return None

    size_fc = next(
        (fc for fc in campaign_dc.comparisons
         if fc.key in ("campaign_size", "launches_per_campaign", "total_launches")
         and fc.deviation in _MEANINGFUL_DEVIATION),
        None,
    )
    if size_fc is None:
        return None

    try:
        if float(size_fc.current_raw) >= float(size_fc.historical_raw):
            return None
    except (TypeError, ValueError):
        return None

    funding_dc   = _dim_change(change_report, "funding")
    infra_dc     = _dim_change(change_report, "operational")
    funding_stable = not funding_dc or funding_dc.drift in ("STABLE", "INSUFFICIENT_EVIDENCE")
    infra_stable   = not infra_dc   or infra_dc.drift   in ("STABLE", "INSUFFICIENT_EVIDENCE")

    supporting: list[EvidenceItem] = [
        EvidenceItem(
            label  = "Campaign scale decreased",
            detail = (
                f"{size_fc.label}: "
                f"{size_fc.historical_value} → {size_fc.current_value} "
                f"(deviation: {size_fc.deviation})"
            ),
            source = "CHANGE",
            weight = 2.0,
        )
    ]
    contradictory: list[EvidenceItem] = []

    if funding_stable:
        supporting.append(EvidenceItem(
            label="Funding pattern unchanged", detail="", source="CHANGE", weight=1.0))
    else:
        contradictory.append(EvidenceItem(
            label="Funding pattern also changed",
            detail=funding_dc.summary if funding_dc else "",
            source="CHANGE", weight=0.8))

    if infra_stable:
        supporting.append(EvidenceItem(
            label="Infrastructure stable", detail="", source="CHANGE", weight=1.0))
    else:
        contradictory.append(EvidenceItem(
            label="Infrastructure also shifted",
            detail=infra_dc.summary if infra_dc else "",
            source="CHANGE", weight=0.8))

    base_conf = _lower_conf(campaign_dc.overall_confidence, size_fc.confidence)
    conf      = _penalise_conf(base_conf, len(contradictory))

    return Assessment(
        assessment_id          = _assess_id(operator_id, "CAMPAIGN_CONTRACTION", ts),
        operator_id            = operator_id,
        assessment_type        = "CAMPAIGN_CONTRACTION",
        headline               = "Operator campaign activity has decreased.",
        summary                = (
            f"Campaign size has decreased "
            f"(from {size_fc.historical_value} to {size_fc.current_value}) "
            f"while funding and infrastructure remain stable."
        ),
        confidence             = conf,
        supporting_evidence    = supporting,
        contradictory_evidence = contradictory,
        gap_notes              = profile.evidence_gap_notes,
        generated_at           = ts,
        expires_at             = ts + 3600,
    )


def _rule_funding_shift(
    operator_id: str, profile, change_report, ts: int
) -> Assessment | None:
    """
    Rule: FUNDING_SHIFT
    Trigger:
      — funding dimension changed (MODERATE+ deviation on preferred_treasury_size
        OR wrap_close_sol OR creator_funding_sol)
      — campaign dimension STABLE or INSUFFICIENT (isolates the shift)
    """
    funding_dc = _dim_change(change_report, "funding")
    if funding_dc is None or funding_dc.drift != "CHANGED":
        return None
    if funding_dc.overall_confidence not in ("LOW", "MEDIUM", "HIGH"):
        return None

    changed_facts = [
        fc for fc in funding_dc.comparisons
        if fc.deviation in _MEANINGFUL_DEVIATION
        and fc.confidence in ("LOW", "MEDIUM", "HIGH")
        and fc.key in ("preferred_treasury_size", "wrap_close_sol", "creator_funding_sol")
    ]
    if not changed_facts:
        return None

    campaign_dc = _dim_change(change_report, "campaign")
    campaign_stable = not campaign_dc or campaign_dc.drift in ("STABLE", "INSUFFICIENT_EVIDENCE")

    supporting = [
        EvidenceItem(
            label  = f"{fc.label} changed",
            detail = f"{fc.historical_value} → {fc.current_value} ({fc.deviation})",
            source = "CHANGE",
            weight = 2.0 if fc.deviation in _STRONG_DEVIATION else 1.0,
        )
        for fc in changed_facts
    ]
    contradictory: list[EvidenceItem] = []

    if campaign_stable:
        supporting.append(EvidenceItem(
            label  = "Campaign scale unchanged",
            detail = "Launch volume is within historical norms.",
            source = "CHANGE",
            weight = 0.5,
        ))
    else:
        contradictory.append(EvidenceItem(
            label  = "Campaign scale also changed",
            detail = campaign_dc.summary if campaign_dc else "",
            source = "CHANGE",
            weight = 0.8,
        ))

    base_conf = funding_dc.overall_confidence
    conf      = _penalise_conf(base_conf, len(contradictory))

    primary = changed_facts[0]
    return Assessment(
        assessment_id          = _assess_id(operator_id, "FUNDING_SHIFT", ts),
        operator_id            = operator_id,
        assessment_type        = "FUNDING_SHIFT",
        headline               = "Operator funding pattern has materially changed.",
        summary                = (
            f"Funding behaviour differs from the historical baseline. "
            f"{primary.label}: {primary.historical_value} → {primary.current_value}."
        ),
        confidence             = conf,
        supporting_evidence    = supporting,
        contradictory_evidence = contradictory,
        gap_notes              = profile.evidence_gap_notes,
        generated_at           = ts,
        expires_at             = ts + 3600,
    )


def _rule_infrastructure_shift(
    operator_id: str, profile, change_report, ts: int
) -> Assessment | None:
    """
    Rule: INFRASTRUCTURE_SHIFT
    Trigger: operational dimension changed (MODERATE+ deviation) on timing or
             sub-provisioner structure facts.
    """
    infra_dc = _dim_change(change_report, "operational")
    if infra_dc is None or infra_dc.drift != "CHANGED":
        return None
    if infra_dc.overall_confidence not in ("LOW", "MEDIUM", "HIGH"):
        return None

    changed_facts = [
        fc for fc in infra_dc.comparisons
        if fc.deviation in _MEANINGFUL_DEVIATION
        and fc.confidence in ("LOW", "MEDIUM", "HIGH")
    ]
    if not changed_facts:
        return None

    supporting = [
        EvidenceItem(
            label  = f"{fc.label} changed",
            detail = f"{fc.historical_value} → {fc.current_value} ({fc.deviation})",
            source = "CHANGE",
            weight = 1.5,
        )
        for fc in changed_facts
    ]
    campaign_dc = _dim_change(change_report, "campaign")
    contradictory: list[EvidenceItem] = []
    if campaign_dc and campaign_dc.drift == "CHANGED":
        contradictory.append(EvidenceItem(
            label  = "Campaign scale also changed",
            detail = campaign_dc.summary,
            source = "CHANGE",
            weight = 0.5,
        ))

    conf = _penalise_conf(infra_dc.overall_confidence, len(contradictory))
    return Assessment(
        assessment_id          = _assess_id(operator_id, "INFRASTRUCTURE_SHIFT", ts),
        operator_id            = operator_id,
        assessment_type        = "INFRASTRUCTURE_SHIFT",
        headline               = "Operator operational infrastructure has changed.",
        summary                = (
            "Operational timing or sub-provisioner structure differs "
            "from the historical baseline."
        ),
        confidence             = conf,
        supporting_evidence    = supporting,
        contradictory_evidence = contradictory,
        gap_notes              = profile.evidence_gap_notes,
        generated_at           = ts,
        expires_at             = ts + 3600,
    )


def _rule_behaviour_change(
    operator_id: str, profile, change_report, ts: int
) -> Assessment | None:
    """
    Rule: BEHAVIOUR_CHANGE
    Trigger: overall_drift is CHANGED or MIXED and no more specific rule
             (expansion/contraction/funding/infra) matched.
    This is the catch-all for meaningful change.
    """
    drift = getattr(change_report, "overall_drift", "STABLE")
    if drift not in ("CHANGED", "MIXED"):
        return None

    changed_dims = [
        dc for dc in change_report.dimension_changes
        if dc.drift == "CHANGED" and dc.overall_confidence in ("LOW", "MEDIUM", "HIGH")
    ]
    if not changed_dims:
        return None

    supporting = [
        EvidenceItem(
            label  = f"{dc.label} changed",
            detail = dc.summary,
            source = "CHANGE",
            weight = _CONF_ORDER.get(dc.overall_confidence, 0),
        )
        for dc in changed_dims
    ]
    stable_dims = [
        dc for dc in change_report.dimension_changes
        if dc.drift == "STABLE" and dc.overall_confidence in ("LOW", "MEDIUM", "HIGH")
    ]
    contradictory = [
        EvidenceItem(
            label  = f"{dc.label} unchanged",
            detail = "No departure from historical baseline on this dimension.",
            source = "CHANGE",
            weight = 0.5,
        )
        for dc in stable_dims
    ]
    base_conf = max(
        (dc.overall_confidence for dc in changed_dims),
        key=lambda c: _CONF_ORDER.get(c, 0),
        default="INSUFFICIENT",
    )
    conf = _penalise_conf(base_conf, len(contradictory))
    dim_labels = ", ".join(dc.label for dc in changed_dims)
    return Assessment(
        assessment_id          = _assess_id(operator_id, "BEHAVIOUR_CHANGE", ts),
        operator_id            = operator_id,
        assessment_type        = "BEHAVIOUR_CHANGE",
        headline               = "Operator behaviour has changed from historical baseline.",
        summary                = (
            f"Material change observed in: {dim_labels}. "
            f"Overall drift classification: {drift}."
        ),
        confidence             = conf,
        supporting_evidence    = supporting,
        contradictory_evidence = contradictory,
        gap_notes              = profile.evidence_gap_notes + change_report.evidence_gap_notes,
        generated_at           = ts,
        expires_at             = ts + 3600,
    )


def _rule_similarity_observed(
    operator_id: str, profile, similarity_results: list, ts: int
) -> Assessment | None:
    """
    Rule: SIMILARITY_OBSERVED
    Trigger: one or more HIGH/VERY_HIGH similarity results from X12 snapshot.
    Note: similarity is behavioural only. Does NOT imply common ownership.
    """
    high_results = [
        r for r in similarity_results
        if r.similarity_band in ("HIGH", "VERY_HIGH")
        and r.confidence in ("LOW", "MEDIUM", "HIGH")
    ]
    if not high_results:
        return None

    supporting = []
    for r in high_results[:3]:
        other = r.operator_b if r.operator_a == operator_id else r.operator_a
        supporting.append(EvidenceItem(
            label  = f"Behavioural similarity to {other}",
            detail = (
                f"Band: {r.similarity_band}, confidence: {r.confidence}. "
                + (r.reasons[0] if r.reasons else "")
            ),
            source = "SIMILARITY",
            weight = 2.0 if r.similarity_band == "VERY_HIGH" else 1.5,
        ))

    contradictory = []
    if len(similarity_results) > len(high_results):
        n_low = len(similarity_results) - len(high_results)
        contradictory.append(EvidenceItem(
            label  = f"{n_low} operator(s) show lower similarity",
            detail = "Not all operators behave similarly.",
            source = "SIMILARITY",
            weight = 0.3,
        ))

    best_band  = "VERY_HIGH" if any(r.similarity_band == "VERY_HIGH" for r in high_results) else "HIGH"
    sim_confs  = [r.confidence for r in high_results]
    best_conf  = max(sim_confs, key=lambda c: _CONF_ORDER.get(c, 0), default="INSUFFICIENT")
    conf       = _lower_conf(best_conf, profile.overall_confidence)
    conf       = _penalise_conf(conf, len(contradictory))

    return Assessment(
        assessment_id          = _assess_id(operator_id, "SIMILARITY_OBSERVED", ts),
        operator_id            = operator_id,
        assessment_type        = "SIMILARITY_OBSERVED",
        headline               = f"{best_band.replace('_', ' ').title()} behavioural similarity observed.",
        summary                = (
            f"{len(high_results)} operator(s) show {best_band.lower().replace('_', ' ')} "
            f"behavioural similarity. Similarity is based on behavioural patterns only "
            f"and does not indicate common ownership or control."
        ),
        confidence             = conf,
        supporting_evidence    = supporting,
        contradictory_evidence = contradictory,
        gap_notes              = profile.evidence_gap_notes,
        generated_at           = ts,
        expires_at             = ts + 3600,
    )


# ── Rule registry (ordered — more specific rules run first) ───────────────────

_BEHAVIOUR_RULES = [
    _rule_insufficient_evidence,
    _rule_campaign_expansion,
    _rule_campaign_contraction,
    _rule_funding_shift,
    _rule_infrastructure_shift,
    _rule_return_to_baseline,
    _rule_behaviour_change,
    _rule_baseline_behaviour,
]


# ── Assessment Engine ─────────────────────────────────────────────────────────

class AssessmentEngine:
    """
    Synthesises intelligence layers into explainable assessments.

    Inputs:    BehaviourEngine, BehaviourChangeEngine, OperatorSimilarityEngine
    No writes. No hot-path DB access. Fail-open.

    Usage::

        engine  = AssessmentEngine(behaviour_engine, change_engine, similarity_engine)
        bundle  = engine.assess(operator_id, treasury_wallets=[...])
        for a in bundle.assessments:
            print(a.headline, a.confidence)
    """

    def __init__(
        self,
        behaviour_engine,
        change_engine,
        similarity_engine,
    ) -> None:
        self._beh  = behaviour_engine
        self._chg  = change_engine
        self._sim  = similarity_engine

    def assess(
        self,
        operator_id: str,
        treasury_wallets: list[str] | None = None,
    ) -> OperatorAssessmentBundle:
        ts = int(time.time())
        try:
            return self._assess_inner(operator_id, treasury_wallets, ts)
        except Exception as exc:
            return OperatorAssessmentBundle(
                operator_id  = operator_id,
                assessments  = [],
                generated_at = ts,
                available    = False,
                error        = f"Assessment generation failed: {type(exc).__name__}",
            )

    def _assess_inner(
        self,
        operator_id: str,
        treasury_wallets: list[str] | None,
        ts: int,
    ) -> OperatorAssessmentBundle:
        # ── Layer 1: Behaviour ────────────────────────────────────────────────
        profile = self._beh.compute(operator_id, treasury_wallets)

        # ── Layer 2: Behaviour Change ─────────────────────────────────────────
        change_report = self._chg.compare(operator_id, treasury_wallets)

        # ── Layer 3: Similarity (read cached snapshot, no compute triggered) ──
        snap = self._sim.current_snapshot()
        sim_results = snap.for_operator(operator_id) if snap.available else []

        # ── Apply reasoning rules ─────────────────────────────────────────────
        assessments: list[Assessment] = []

        for rule in _BEHAVIOUR_RULES:
            a = rule(operator_id, profile, change_report, ts)
            if a is not None:
                assessments.append(a)
                # INSUFFICIENT_EVIDENCE blocks all further rules
                if a.assessment_type == "INSUFFICIENT_EVIDENCE":
                    break
                # Specific change assessments are mutually exclusive with BASELINE
                # (both may have been appended — deduplicate)

        # Deduplicate: keep only the first assessment of each type
        seen: set[str] = set()
        unique: list[Assessment] = []
        for a in assessments:
            if a.assessment_type not in seen:
                seen.add(a.assessment_type)
                unique.append(a)

        # Remove BASELINE_BEHAVIOUR if a more specific change rule fired
        change_types = {
            "CAMPAIGN_EXPANSION", "CAMPAIGN_CONTRACTION",
            "FUNDING_SHIFT", "INFRASTRUCTURE_SHIFT", "BEHAVIOUR_CHANGE",
        }
        if seen & change_types:
            unique = [a for a in unique if a.assessment_type != "BASELINE_BEHAVIOUR"]

        # ── Similarity rule (separate — doesn't interact with change rules) ───
        sim_a = _rule_similarity_observed(operator_id, profile, sim_results, ts)
        if sim_a is not None and "SIMILARITY_OBSERVED" not in seen:
            unique.append(sim_a)

        return OperatorAssessmentBundle(
            operator_id  = operator_id,
            assessments  = unique,
            generated_at = ts,
            available    = True,
            error        = None,
        )

    def platform_summary(self, operator_ids: list[str]) -> dict:
        """
        Platform-level assessment summary.

        Returns counts by type, highest-confidence assessments, and coverage.
        Does NOT trigger per-operator computation — callers must pass
        already-resolved operator IDs.
        """
        ts       = int(time.time())
        assessed = []
        errors   = 0
        for op_id in operator_ids[:50]:   # bounded workload
            bundle = self.assess(op_id)
            if bundle.available:
                assessed.append(bundle)
            else:
                errors += 1

        counts: dict[str, int] = {}
        for bundle in assessed:
            for a in bundle.assessments:
                counts[a.assessment_type] = counts.get(a.assessment_type, 0) + 1

        highest = sorted(
            [a for b in assessed for a in b.assessments],
            key=lambda a: (_CONF_ORDER.get(a.confidence, 0), a.generated_at),
            reverse=True,
        )[:10]

        return {
            "computed_at":         ts,
            "operators_assessed":  len(assessed),
            "operators_errored":   errors,
            "coverage_pct":        round(100 * len(assessed) / max(len(operator_ids), 1), 1),
            "counts_by_type":      counts,
            "highest_confidence":  [a.to_dict() for a in highest],
        }
