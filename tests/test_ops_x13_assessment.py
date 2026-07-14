"""
Sprint X13 — Intelligence Assessment Engine tests.

Coverage:
  Reasoning:    determinism, contradictions lower confidence, insufficient evidence,
                type matches rule set, specific rules fire correctly.
  Explainability: all assessments have supporting evidence, confidence is derived,
                  no hidden conclusions.
  Database safety: no writes, fail-open, no page-load compute, no N+1.
  UI:           template markup present, progressive disclosure, unavailable state.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ops.assessment_engine import (
    ASSESSMENT_TYPES,
    Assessment,
    AssessmentEngine,
    EvidenceItem,
    OperatorAssessmentBundle,
    _CONF_ORDER,
    _lower_conf,
    _penalise_conf,
    _raise_conf,
    _rule_baseline_behaviour,
    _rule_behaviour_change,
    _rule_campaign_contraction,
    _rule_campaign_expansion,
    _rule_funding_shift,
    _rule_infrastructure_shift,
    _rule_insufficient_evidence,
    _rule_return_to_baseline,
    _rule_similarity_observed,
)


# ── Lightweight stub helpers ──────────────────────────────────────────────────

def _fact(key, raw=10.0, conf="HIGH", obs=15, value=None):
    from src.ops.behaviour_engine import BehaviourFact
    return BehaviourFact(
        key=key, label=key.replace("_", " ").title(),
        value=value if value is not None else str(raw),
        raw=raw, confidence=conf, observations=obs,
    )


def _dim(key="campaign", label="Campaign", conf="HIGH", facts=None):
    from src.ops.behaviour_engine import BehaviourDimension
    d = BehaviourDimension(key=key, label=label, facts=facts or [])
    d.overall_confidence = conf
    return d


def _profile(overall_conf="HIGH", dims=None, obs=20, gap_notes=None):
    from src.ops.behaviour_engine import BehaviourProfile
    return BehaviourProfile(
        operator_id="op-x",
        computed_at=int(time.time()),
        total_observations=obs,
        overall_confidence=overall_conf,
        dimensions=dims or [],
        timeline=[],
        stability_summary={},
        insufficient_dimensions=[],
        evidence_gap_notes=gap_notes or [],
    )


def _fc(key, hist_raw, cur_raw, deviation, conf="MEDIUM", hist_obs=10, cur_obs=10):
    from src.ops.behaviour_change import FactComparison
    return FactComparison(
        key=key, label=key.replace("_", " ").title(),
        historical_value=str(hist_raw), historical_raw=hist_raw, historical_observations=hist_obs,
        current_value=str(cur_raw),    current_raw=cur_raw,    current_observations=cur_obs,
        deviation=deviation, confidence=conf,
        reason=f"{key} changed from {hist_raw} to {cur_raw}",
    )


def _dc(key="campaign", label="Campaign", drift="STABLE", conf="HIGH", comparisons=None):
    from src.ops.behaviour_change import DimensionChange
    return DimensionChange(
        key=key, label=label, drift=drift,
        overall_confidence=conf, summary=f"{label} {drift}",
        comparisons=comparisons or [],
    )


def _change_report(overall_drift="STABLE", dim_changes=None, obs_baseline=10, obs_current=5, gap_notes=None):
    from src.ops.behaviour_change import BehaviourChangeReport
    return BehaviourChangeReport(
        operator_id="op-x",
        computed_at=int(time.time()),
        baseline_window_days=30,
        current_window_days=7,
        baseline_observations=obs_baseline,
        current_observations=obs_current,
        overall_drift=overall_drift,
        drift_summary={},
        dimension_changes=dim_changes or [],
        timeline_events=[],
        evidence_gap_notes=gap_notes or [],
    )


@dataclass
class _SimResult:
    operator_a: str
    operator_b: str
    similarity_band: str
    confidence: str
    reasons: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)


def _mock_engines(profile=None, change_report=None, sim_results=None):
    beh = MagicMock()
    beh.compute.return_value = profile or _profile()
    chg = MagicMock()
    chg.compare.return_value = change_report or _change_report()
    sim = MagicMock()
    snap = MagicMock()
    snap.available = True
    snap.for_operator.return_value = sim_results or []
    sim.current_snapshot.return_value = snap
    return beh, chg, sim


def _engine(profile=None, change_report=None, sim_results=None):
    beh, chg, sim = _mock_engines(profile, change_report, sim_results)
    return AssessmentEngine(beh, chg, sim)


def _ts():
    return int(time.time())


def _read_template():
    with open(os.path.join(ROOT, "templates", "operator_intelligence.html")) as f:
        return f.read()


# ── ASSESSMENT_TYPES completeness ─────────────────────────────────────────────

class TestAssessmentTypes:

    def test_all_required_types_present(self):
        required = {
            "BASELINE_BEHAVIOUR", "BEHAVIOUR_CHANGE", "CAMPAIGN_EXPANSION",
            "CAMPAIGN_CONTRACTION", "FUNDING_SHIFT", "INFRASTRUCTURE_SHIFT",
            "SIMILARITY_OBSERVED", "RETURN_TO_BASELINE", "INSUFFICIENT_EVIDENCE",
        }
        assert required <= ASSESSMENT_TYPES

    def test_assessment_rejects_unknown_type(self):
        with pytest.raises(ValueError):
            Assessment(
                assessment_id="x:UNKNOWN:0", operator_id="x",
                assessment_type="UNKNOWN_TYPE",
                headline="h", summary="s", confidence="HIGH",
            )


# ── Helper functions ──────────────────────────────────────────────────────────

class TestHelpers:

    def test_lower_conf_picks_lesser(self):
        assert _lower_conf("HIGH", "MEDIUM") == "MEDIUM"
        assert _lower_conf("LOW", "HIGH") == "LOW"
        assert _lower_conf("HIGH", "HIGH") == "HIGH"

    def test_raise_conf_picks_higher(self):
        assert _raise_conf("HIGH", "MEDIUM") == "HIGH"

    def test_penalise_conf_one_contradiction(self):
        assert _penalise_conf("HIGH", 1) == "MEDIUM"

    def test_penalise_conf_two_contradictions(self):
        assert _penalise_conf("HIGH", 2) == "LOW"

    def test_penalise_conf_clamps_at_insufficient(self):
        assert _penalise_conf("LOW", 3) == "INSUFFICIENT"

    def test_penalise_conf_zero_contradictions(self):
        assert _penalise_conf("MEDIUM", 0) == "MEDIUM"


# ── Reasoning rules ───────────────────────────────────────────────────────────

class TestRuleInsufficientEvidence:

    def test_fires_when_overall_insufficient(self):
        p  = _profile(overall_conf="INSUFFICIENT", obs=1)
        cr = _change_report()
        a  = _rule_insufficient_evidence("op-x", p, cr, _ts())
        assert a is not None
        assert a.assessment_type == "INSUFFICIENT_EVIDENCE"
        assert a.confidence == "INSUFFICIENT"

    def test_does_not_fire_when_sufficient(self):
        p  = _profile(overall_conf="MEDIUM", obs=10)
        cr = _change_report()
        assert _rule_insufficient_evidence("op-x", p, cr, _ts()) is None

    def test_headline_does_not_contain_forbidden_words(self):
        p = _profile(overall_conf="INSUFFICIENT", obs=1)
        a = _rule_insufficient_evidence("op-x", p, _change_report(), _ts())
        for word in ("predict", "forecast", "will", "expect", "anomaly"):
            assert word not in a.headline.lower()


class TestRuleBaselineBehaviour:

    def test_fires_when_drift_stable(self):
        p  = _profile(overall_conf="HIGH", dims=[_dim()])
        cr = _change_report(overall_drift="STABLE", dim_changes=[_dc(drift="STABLE")])
        a  = _rule_baseline_behaviour("op-x", p, cr, _ts())
        assert a is not None
        assert a.assessment_type == "BASELINE_BEHAVIOUR"

    def test_does_not_fire_when_changed(self):
        p  = _profile(overall_conf="HIGH")
        cr = _change_report(overall_drift="CHANGED")
        assert _rule_baseline_behaviour("op-x", p, cr, _ts()) is None

    def test_supporting_evidence_present(self):
        p  = _profile(overall_conf="HIGH", dims=[_dim("campaign"), _dim("funding")])
        cr = _change_report(overall_drift="STABLE")
        a  = _rule_baseline_behaviour("op-x", p, cr, _ts())
        assert len(a.supporting_evidence) >= 1

    def test_changed_sub_dimension_becomes_contradictory(self):
        p  = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        cr = _change_report(
            overall_drift="STABLE",
            dim_changes=[_dc("campaign", drift="CHANGED", conf="MEDIUM")],
        )
        a  = _rule_baseline_behaviour("op-x", p, cr, _ts())
        assert any(
            "variation" in ev.label.lower() or "changed" in ev.label.lower()
            for ev in a.contradictory_evidence
        )


class TestRuleCampaignExpansion:

    def _make(self, cur, hist, funding_drift="STABLE", infra_drift="STABLE", camp_conf="HIGH"):
        fc   = _fc("campaign_size", hist, cur, "HIGH" if cur > hist * 1.5 else "MODERATE")
        dc   = _dc("campaign", drift="CHANGED", conf=camp_conf, comparisons=[fc])
        f_dc = _dc("funding", drift=funding_drift)
        i_dc = _dc("operational", drift=infra_drift)
        cr   = _change_report(overall_drift="CHANGED", dim_changes=[dc, f_dc, i_dc])
        p    = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        return p, cr

    def test_fires_when_size_increased(self):
        p, cr = self._make(cur=15, hist=5)
        a = _rule_campaign_expansion("op-x", p, cr, _ts())
        assert a is not None
        assert a.assessment_type == "CAMPAIGN_EXPANSION"

    def test_does_not_fire_when_size_decreased(self):
        p, cr = self._make(cur=3, hist=15)
        assert _rule_campaign_expansion("op-x", p, cr, _ts()) is None

    def test_does_not_fire_without_campaign_change(self):
        cr = _change_report(overall_drift="STABLE")
        p  = _profile()
        assert _rule_campaign_expansion("op-x", p, cr, _ts()) is None

    def test_stable_funding_in_supporting(self):
        p, cr = self._make(cur=20, hist=5, funding_drift="STABLE")
        a = _rule_campaign_expansion("op-x", p, cr, _ts())
        assert any("funding" in ev.label.lower() for ev in a.supporting_evidence)

    def test_changed_funding_becomes_contradictory(self):
        p, cr = self._make(cur=20, hist=5, funding_drift="CHANGED")
        a = _rule_campaign_expansion("op-x", p, cr, _ts())
        assert a is not None
        assert any("funding" in ev.label.lower() for ev in a.contradictory_evidence)

    def test_contradictions_lower_confidence(self):
        p1, cr1 = self._make(cur=20, hist=5, funding_drift="STABLE")
        p2, cr2 = self._make(cur=20, hist=5, funding_drift="CHANGED", infra_drift="CHANGED")
        a1 = _rule_campaign_expansion("op-x", p1, cr1, _ts())
        a2 = _rule_campaign_expansion("op-x", p2, cr2, _ts())
        assert _CONF_ORDER.get(a1.confidence, 0) >= _CONF_ORDER.get(a2.confidence, 0)


class TestRuleCampaignContraction:

    def test_fires_when_size_decreased(self):
        fc  = _fc("campaign_size", 20, 3, "HIGH")
        dc  = _dc("campaign", drift="CHANGED", conf="HIGH", comparisons=[fc])
        cr  = _change_report(overall_drift="CHANGED", dim_changes=[dc])
        p   = _profile(overall_conf="HIGH")
        a   = _rule_campaign_contraction("op-x", p, cr, _ts())
        assert a is not None
        assert a.assessment_type == "CAMPAIGN_CONTRACTION"

    def test_does_not_fire_when_increased(self):
        fc  = _fc("campaign_size", 3, 20, "HIGH")
        dc  = _dc("campaign", drift="CHANGED", comparisons=[fc])
        cr  = _change_report(dim_changes=[dc])
        assert _rule_campaign_contraction("op-x", _profile(), cr, _ts()) is None


class TestRuleFundingShift:

    def test_fires_on_treasury_size_change(self):
        fc  = _fc("preferred_treasury_size", 800.0, 300.0, "HIGH")
        dc  = _dc("funding", drift="CHANGED", conf="HIGH", comparisons=[fc])
        cr  = _change_report(overall_drift="CHANGED", dim_changes=[dc])
        a   = _rule_funding_shift("op-x", _profile(), cr, _ts())
        assert a is not None
        assert a.assessment_type == "FUNDING_SHIFT"

    def test_does_not_fire_without_funding_change(self):
        cr = _change_report(overall_drift="STABLE")
        assert _rule_funding_shift("op-x", _profile(), cr, _ts()) is None

    def test_does_not_fire_on_peripheral_fact(self):
        fc  = _fc("some_unrelated_key", 1.0, 2.0, "HIGH")
        dc  = _dc("funding", drift="CHANGED", conf="HIGH", comparisons=[fc])
        cr  = _change_report(dim_changes=[dc])
        assert _rule_funding_shift("op-x", _profile(), cr, _ts()) is None


class TestRuleInfrastructureShift:

    def test_fires_on_operational_change(self):
        fc  = _fc("active_hours_span", 6.0, 22.0, "HIGH")
        dc  = _dc("operational", drift="CHANGED", conf="HIGH", comparisons=[fc])
        cr  = _change_report(overall_drift="CHANGED", dim_changes=[dc])
        a   = _rule_infrastructure_shift("op-x", _profile(), cr, _ts())
        assert a is not None
        assert a.assessment_type == "INFRASTRUCTURE_SHIFT"

    def test_does_not_fire_when_stable(self):
        dc  = _dc("operational", drift="STABLE", conf="HIGH")
        cr  = _change_report(dim_changes=[dc])
        assert _rule_infrastructure_shift("op-x", _profile(), cr, _ts()) is None


class TestRuleBehaviourChange:

    def test_fires_on_changed_drift(self):
        dc  = _dc("campaign", drift="CHANGED", conf="MEDIUM")
        cr  = _change_report(overall_drift="CHANGED", dim_changes=[dc])
        a   = _rule_behaviour_change("op-x", _profile(overall_conf="MEDIUM"), cr, _ts())
        assert a is not None
        assert a.assessment_type == "BEHAVIOUR_CHANGE"

    def test_fires_on_mixed_drift(self):
        dc1 = _dc("campaign", drift="CHANGED", conf="MEDIUM")
        dc2 = _dc("funding",  drift="STABLE",  conf="HIGH")
        cr  = _change_report(overall_drift="MIXED", dim_changes=[dc1, dc2])
        a   = _rule_behaviour_change("op-x", _profile(), cr, _ts())
        assert a is not None

    def test_does_not_fire_when_stable(self):
        cr = _change_report(overall_drift="STABLE")
        assert _rule_behaviour_change("op-x", _profile(), cr, _ts()) is None

    def test_stable_dims_become_contradictory(self):
        dc1 = _dc("campaign", drift="CHANGED", conf="MEDIUM")
        dc2 = _dc("funding",  drift="STABLE",  conf="HIGH")
        cr  = _change_report(overall_drift="CHANGED", dim_changes=[dc1, dc2])
        a   = _rule_behaviour_change("op-x", _profile(), cr, _ts())
        assert len(a.contradictory_evidence) >= 1


class TestRuleSimilarityObserved:

    def test_fires_on_high_similarity(self):
        sr = _SimResult("op-x", "op-y", "HIGH", "HIGH", reasons=["Similar treasury size"])
        p  = _profile(overall_conf="HIGH")
        a  = _rule_similarity_observed("op-x", p, [sr], _ts())
        assert a is not None
        assert a.assessment_type == "SIMILARITY_OBSERVED"

    def test_does_not_fire_on_low_similarity(self):
        sr = _SimResult("op-x", "op-y", "LOW", "MEDIUM")
        assert _rule_similarity_observed("op-x", _profile(), [sr], _ts()) is None

    def test_does_not_fire_on_insufficient_confidence(self):
        sr = _SimResult("op-x", "op-y", "HIGH", "INSUFFICIENT")
        assert _rule_similarity_observed("op-x", _profile(), [sr], _ts()) is None

    def test_no_ownership_language(self):
        sr = _SimResult("op-x", "op-y", "VERY_HIGH", "HIGH", reasons=["Same wrap-close amount"])
        a  = _rule_similarity_observed("op-x", _profile(), [sr], _ts())
        assert a is not None
        text = a.headline + a.summary + " ".join(e.label for e in a.supporting_evidence)
        for word in ("same operator", "controlled", "merged", "linked operator"):
            assert word not in text.lower()

    def test_headline_mentions_band(self):
        sr = _SimResult("op-x", "op-y", "VERY_HIGH", "HIGH")
        a  = _rule_similarity_observed("op-x", _profile(), [sr], _ts())
        assert "very high" in a.headline.lower()


# ── AssessmentEngine integration ──────────────────────────────────────────────

class TestAssessmentEngine:

    def test_assess_returns_bundle(self):
        eng = _engine()
        b   = eng.assess("op-x")
        assert isinstance(b, OperatorAssessmentBundle)
        assert b.available is True

    def test_deterministic_identical_inputs(self):
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        cr  = _change_report(overall_drift="STABLE")
        eng = _engine(profile=p, change_report=cr)
        b1  = eng.assess("op-x")
        b2  = eng.assess("op-x")
        types1 = sorted(a.assessment_type for a in b1.assessments)
        types2 = sorted(a.assessment_type for a in b2.assessments)
        assert types1 == types2

    def test_insufficient_evidence_blocks_other_rules(self):
        p   = _profile(overall_conf="INSUFFICIENT", obs=1)
        cr  = _change_report(overall_drift="CHANGED")
        eng = _engine(profile=p, change_report=cr)
        b   = eng.assess("op-x")
        assert len(b.assessments) == 1
        assert b.assessments[0].assessment_type == "INSUFFICIENT_EVIDENCE"

    def test_campaign_expansion_excludes_baseline(self):
        fc  = _fc("campaign_size", 5, 25, "HIGH")
        dc  = _dc("campaign", drift="CHANGED", conf="HIGH", comparisons=[fc])
        cr  = _change_report(overall_drift="CHANGED", dim_changes=[dc])
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        eng = _engine(profile=p, change_report=cr)
        b   = eng.assess("op-x")
        types = {a.assessment_type for a in b.assessments}
        assert "CAMPAIGN_EXPANSION" in types
        assert "BASELINE_BEHAVIOUR" not in types

    def test_similarity_added_alongside_change_assessment(self):
        fc  = _fc("campaign_size", 5, 25, "HIGH")
        dc  = _dc("campaign", drift="CHANGED", conf="HIGH", comparisons=[fc])
        cr  = _change_report(overall_drift="CHANGED", dim_changes=[dc])
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        sr  = _SimResult("op-x", "op-y", "HIGH", "HIGH", reasons=["Similar treasury"])
        eng = _engine(profile=p, change_report=cr, sim_results=[sr])
        b   = eng.assess("op-x")
        types = {a.assessment_type for a in b.assessments}
        assert "SIMILARITY_OBSERVED" in types

    def test_no_duplicate_types_in_bundle(self):
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign"), _dim("funding")])
        cr  = _change_report(overall_drift="STABLE")
        eng = _engine(profile=p, change_report=cr)
        b   = eng.assess("op-x")
        types = [a.assessment_type for a in b.assessments]
        assert len(types) == len(set(types))

    def test_fail_open_on_engine_error(self):
        beh = MagicMock()
        beh.compute.side_effect = RuntimeError("DB exploded")
        chg = MagicMock()
        sim = MagicMock()
        eng = AssessmentEngine(beh, chg, sim)
        b   = eng.assess("op-x")
        assert b.available is False
        assert b.error is not None
        assert "RuntimeError" in b.error or "DB exploded" in b.error or len(b.error) > 0

    def test_error_does_not_expose_stack_trace(self):
        beh = MagicMock()
        beh.compute.side_effect = Exception("internal")
        chg = MagicMock()
        sim = MagicMock()
        eng = AssessmentEngine(beh, chg, sim)
        b   = eng.assess("op-x")
        assert b.error is not None
        assert "Traceback" not in b.error
        assert "File " not in b.error


# ── Explainability ────────────────────────────────────────────────────────────

class TestExplainability:

    def _non_insufficient_assessments(self, bundle):
        return [a for a in bundle.assessments if a.assessment_type != "INSUFFICIENT_EVIDENCE"]

    def test_all_assessments_have_headline(self):
        fc  = _fc("campaign_size", 5, 20, "HIGH")
        dc  = _dc("campaign", drift="CHANGED", conf="HIGH", comparisons=[fc])
        cr  = _change_report(overall_drift="CHANGED", dim_changes=[dc])
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        eng = _engine(profile=p, change_report=cr)
        for a in eng.assess("op-x").assessments:
            assert a.headline.strip() != ""

    def test_all_assessments_have_summary(self):
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        cr  = _change_report(overall_drift="STABLE")
        eng = _engine(profile=p, change_report=cr)
        for a in eng.assess("op-x").assessments:
            assert a.summary.strip() != ""

    def test_non_insufficient_have_supporting_evidence(self):
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        cr  = _change_report(overall_drift="STABLE", dim_changes=[_dc(drift="STABLE")])
        eng = _engine(profile=p, change_report=cr)
        for a in self._non_insufficient_assessments(eng.assess("op-x")):
            assert len(a.supporting_evidence) >= 1, f"{a.assessment_type} has no supporting evidence"

    def test_confidence_is_valid_band(self):
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        cr  = _change_report(overall_drift="STABLE")
        eng = _engine(profile=p, change_report=cr)
        valid = {"INSUFFICIENT", "LOW", "MEDIUM", "HIGH"}
        for a in eng.assess("op-x").assessments:
            assert a.confidence in valid

    def test_evidence_count_matches_lists(self):
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        cr  = _change_report(overall_drift="STABLE", dim_changes=[_dc(drift="STABLE")])
        eng = _engine(profile=p, change_report=cr)
        for a in eng.assess("op-x").assessments:
            expected = len(a.supporting_evidence) + len(a.contradictory_evidence)
            assert a.evidence_count == expected

    def test_assessment_is_json_serialisable(self):
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        cr  = _change_report(overall_drift="STABLE")
        eng = _engine(profile=p, change_report=cr)
        b   = eng.assess("op-x")
        json.dumps(b.to_dict())

    def test_no_predictive_language_in_any_text(self):
        forbidden = ["predict", "forecast", "will launch", "next launch", "expect", "likely to"]
        fc  = _fc("campaign_size", 5, 20, "HIGH")
        dc  = _dc("campaign", drift="CHANGED", conf="HIGH", comparisons=[fc])
        cr  = _change_report(overall_drift="CHANGED", dim_changes=[dc])
        sr  = _SimResult("op-x", "op-y", "HIGH", "HIGH", reasons=["Similar funding"])
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        eng = _engine(profile=p, change_report=cr, sim_results=[sr])
        b   = eng.assess("op-x")
        for a in b.assessments:
            full_text = (
                a.headline + " " + a.summary + " "
                + " ".join(e.label + " " + e.detail for e in a.supporting_evidence)
                + " ".join(e.label + " " + e.detail for e in a.contradictory_evidence)
            ).lower()
            for word in forbidden:
                assert word not in full_text, f"Forbidden word '{word}' in {a.assessment_type}: {full_text[:200]}"

    def test_no_ml_inference_language(self):
        forbidden = ["model", "neural", "classify", "score", "rank", "probability"]
        p   = _profile(overall_conf="HIGH", dims=[_dim("campaign")])
        cr  = _change_report(overall_drift="STABLE")
        eng = _engine(profile=p, change_report=cr)
        for a in eng.assess("op-x").assessments:
            full_text = (a.headline + " " + a.summary).lower()
            for word in forbidden:
                assert word not in full_text


# ── Database safety ───────────────────────────────────────────────────────────

class TestDatabaseSafety:

    def test_assess_does_not_open_db_directly(self):
        """AssessmentEngine.assess() must not call sqlite3.connect() directly.
        All DB access is delegated to the sub-engines."""
        beh, chg, sim = _mock_engines()
        eng = AssessmentEngine(beh, chg, sim)

        db_opens = []
        orig_connect = sqlite3.connect
        def tracking_connect(path, *a, **kw):
            db_opens.append(path)
            return orig_connect(path, *a, **kw)

        with patch("sqlite3.connect", side_effect=tracking_connect):
            eng.assess("op-x")

        assert db_opens == [], (
            f"AssessmentEngine.assess() opened DB directly: {db_opens}. "
            "All DB work must be delegated to sub-engines."
        )

    def test_assess_delegates_to_sub_engines(self):
        beh, chg, sim = _mock_engines()
        eng = AssessmentEngine(beh, chg, sim)
        eng.assess("op-x")
        beh.compute.assert_called_once()
        chg.compare.assert_called_once()
        sim.current_snapshot.assert_called_once()

    def test_no_writes_via_sub_engines(self):
        """Verify sub-engine calls are read-only (compute/compare/current_snapshot)."""
        beh, chg, sim = _mock_engines()
        eng = AssessmentEngine(beh, chg, sim)
        eng.assess("op-x")
        # No mutating calls on sub-engines
        assert not beh.mock_calls or all(
            "compute" in str(c) or "__" in str(c) for c in beh.mock_calls
        )

    def test_fail_open_when_behaviour_engine_fails(self):
        beh = MagicMock(); beh.compute.side_effect = Exception("DB locked")
        chg = MagicMock(); sim = MagicMock()
        eng = AssessmentEngine(beh, chg, sim)
        b   = eng.assess("op-x")
        assert b.available is False
        assert b.error is not None

    def test_fail_open_when_change_engine_fails(self):
        beh = MagicMock(); beh.compute.return_value = _profile()
        chg = MagicMock(); chg.compare.side_effect = RuntimeError("DB busy")
        sim = MagicMock()
        eng = AssessmentEngine(beh, chg, sim)
        b   = eng.assess("op-x")
        assert b.available is False

    def test_similarity_snapshot_unavailable_does_not_fail(self):
        beh, chg, sim = _mock_engines()
        snap = MagicMock(); snap.available = False
        sim.current_snapshot.return_value = snap
        eng = AssessmentEngine(beh, chg, sim)
        b   = eng.assess("op-x")
        assert b.available is True  # should still produce other assessments

    def test_platform_summary_bounded_to_50(self):
        beh, chg, sim = _mock_engines()
        eng = AssessmentEngine(beh, chg, sim)
        op_ids = [f"op-{i}" for i in range(100)]
        summary = eng.platform_summary(op_ids)
        # Should cap at 50 — compute called at most 50 times
        assert beh.compute.call_count <= 50


# ── API routes ────────────────────────────────────────────────────────────────

@pytest.fixture
def assess_client():
    import src.ops.assessment_routes as ar

    beh, chg, sim = _mock_engines(
        profile=_profile(
            overall_conf="HIGH",
            dims=[_dim("campaign"), _dim("funding")],
        ),
        change_report=_change_report(overall_drift="STABLE"),
    )
    ar._engine = AssessmentEngine(beh, chg, sim)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(ar.assessment_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    ar._engine = None


@pytest.fixture
def failing_client():
    import src.ops.assessment_routes as ar

    beh = MagicMock(); beh.compute.side_effect = Exception("DB down")
    chg = MagicMock(); sim = MagicMock()
    ar._engine = AssessmentEngine(beh, chg, sim)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(ar.assessment_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    ar._engine = None


class TestAssessmentRoutes:

    def test_assessment_endpoint_200(self, assess_client):
        r = assess_client.get("/api/operators/op-x/assessment")
        assert r.status_code == 200

    def test_assessment_response_has_ok(self, assess_client):
        data = assess_client.get("/api/operators/op-x/assessment").get_json()
        assert "ok" in data

    def test_assessment_response_has_assessments(self, assess_client):
        data = assess_client.get("/api/operators/op-x/assessment").get_json()
        if data.get("ok"):
            assert "assessments" in data

    def test_by_type_unknown_returns_400(self, assess_client):
        r = assess_client.get("/api/operators/op-x/assessment/UNKNOWN_TYPE")
        assert r.status_code == 400

    def test_by_type_known_returns_200(self, assess_client):
        r = assess_client.get("/api/operators/op-x/assessment/BASELINE_BEHAVIOUR")
        assert r.status_code == 200

    def test_fail_open_returns_200_not_500(self, failing_client):
        r = failing_client.get("/api/operators/op-x/assessment")
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("ok") is False or data.get("unavailable") is True

    def test_no_stack_trace_in_error_response(self, failing_client):
        data = failing_client.get("/api/operators/op-x/assessment").get_json()
        text = json.dumps(data)
        assert "Traceback" not in text
        assert "File " not in text


# ── Template markup ───────────────────────────────────────────────────────────

class TestAssessmentTemplateMarkup:

    def test_assessment_section_present(self):
        html = _read_template()
        assert "oi-assess-section" in html
        assert "oi-assess-body"    in html

    def test_assessment_label_present(self):
        html = _read_template()
        assert "Assessment" in html

    def test_assessment_api_fetched(self):
        html = _read_template()
        assert "/assessment" in html

    def test_progressive_disclosure_evidence(self):
        html = _read_template()
        assert "pd-toggle"   in html
        assert "pd-body"     in html
        assert "Show evidence" in html

    def test_supporting_evidence_rendered(self):
        html = _read_template()
        assert "Supporting Evidence" in html or "supporting_evidence" in html

    def test_contradictory_evidence_rendered(self):
        html = _read_template()
        assert "Contradictory Evidence" in html or "contradictory_evidence" in html

    def test_confidence_badge_present(self):
        html = _read_template()
        assert "oi-ass-meta" in html
        assert "ip-badge" in html

    def test_unavailable_state_handled(self):
        html = _read_template()
        assert "unavailable" in html.lower()

    def test_no_raw_db_error_in_template(self):
        html = _read_template()
        assert "OperationalError" not in html
        assert "sqlite3" not in html.lower()

    def test_no_new_top_level_page(self):
        html = _read_template()
        assert "/intelligence/assessments" not in html

    def test_gap_notes_rendered(self):
        html = _read_template()
        assert "gap_notes" in html or "Evidence gaps" in html

    def test_assessment_section_between_change_and_inbox(self):
        html = _read_template()
        change_pos = html.find("oi-change-section")
        assess_pos = html.find("oi-assess-section")
        inbox_pos  = html.find("oi-inbox-section")
        assert change_pos < assess_pos < inbox_pos


# ── OperatorAssessmentBundle ──────────────────────────────────────────────────

class TestBundle:

    def test_by_type_returns_correct(self):
        a = Assessment(
            assessment_id="x:BASELINE_BEHAVIOUR:1",
            operator_id="x", assessment_type="BASELINE_BEHAVIOUR",
            headline="h", summary="s", confidence="HIGH",
        )
        b = OperatorAssessmentBundle(operator_id="x", assessments=[a])
        assert b.by_type("BASELINE_BEHAVIOUR") is a
        assert b.by_type("FUNDING_SHIFT") is None

    def test_highest_confidence_correct(self):
        a_low = Assessment(
            assessment_id="x:BEHAVIOUR_CHANGE:1",
            operator_id="x", assessment_type="BEHAVIOUR_CHANGE",
            headline="h", summary="s", confidence="LOW",
        )
        a_high = Assessment(
            assessment_id="x:BASELINE_BEHAVIOUR:1",
            operator_id="x", assessment_type="BASELINE_BEHAVIOUR",
            headline="h", summary="s", confidence="HIGH",
        )
        b = OperatorAssessmentBundle(operator_id="x", assessments=[a_low, a_high])
        assert b.highest_confidence() is a_high

    def test_to_dict_serialisable(self):
        b = OperatorAssessmentBundle(operator_id="x", assessments=[], available=True)
        json.dumps(b.to_dict())
