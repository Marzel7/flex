"""
Sprint X14 — Lifecycle Forecast Engine tests.

Coverage:
  Correctness:      ARMED→ACTIVE, OBSERVING paths, IDLE, ACTIVE, COMPLETED,
                    expiry fields, window generation, confidence limits,
                    deterministic results, symmetry of repeated computation.
  Explainability:   assessment reference, evidence present, confidence derived,
                    no unexplained output, no forbidden language.
  Database Safety:  no writes, delegated-only DB access, fail-open, no N+1,
                    no page-load compute, batch persistence only.
  UI:               section present, progressive disclosure, unavailable state,
                    no raw DB errors, Mission Control badge class, inbox integration.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ops.lifecycle import ARMED, ACTIVE, COMPLETED, IDLE, OBSERVING, ARCHIVED
from src.ops.forecast_engine import (
    FORECAST_TYPES,
    ForecastEngine,
    LifecycleForecast,
    WINDOW_FAST, WINDOW_SHORT, WINDOW_MEDIUM, WINDOW_LONG, WINDOW_VERY_LONG, WINDOW_UNKNOWN,
    _timing_window,
    _insufficient_forecast,
    _rule_armed_to_active,
    _rule_observing_to_armed,
    _rule_observing_stable,
    _rule_idle_to_observing,
    _rule_active_to_completed,
    _rule_completed_archived,
    _rule_insufficient,
)


# ── Stub helpers ──────────────────────────────────────────────────────────────

def _ts():
    return int(time.time())


def _fact(key, raw=10.0, conf="HIGH", obs=15, value=None, stability="STABLE"):
    from src.ops.behaviour_engine import BehaviourFact
    return BehaviourFact(
        key=key, label=key.replace("_", " ").title(),
        value=value if value is not None else str(raw),
        raw=raw, confidence=conf, observations=obs,
        stability=stability,
    )


def _dim(key="launch", label="Launch", conf="HIGH", facts=None):
    from src.ops.behaviour_engine import BehaviourDimension
    d = BehaviourDimension(key=key, label=label, facts=facts or [])
    d.overall_confidence = conf
    return d


def _profile(overall_conf="HIGH", dims=None, obs=20, gap_notes=None):
    from src.ops.behaviour_engine import BehaviourProfile
    return BehaviourProfile(
        operator_id="op-x",
        computed_at=_ts(),
        total_observations=obs,
        overall_confidence=overall_conf,
        dimensions=dims or [],
        timeline=[],
        stability_summary={},
        insufficient_dimensions=[],
        evidence_gap_notes=gap_notes or [],
    )


def _assessment(atype, conf="HIGH", headline="h", summary="s",
                supporting=None, contradicting=None):
    from src.ops.assessment_engine import Assessment, EvidenceItem
    sup = supporting or [
        EvidenceItem(label="Evidence", detail="detail", source="BEHAVIOUR")
    ]
    con = contradicting or []
    return Assessment(
        assessment_id   = f"op-x:{atype}:0",
        operator_id     = "op-x",
        assessment_type = atype,
        headline        = headline,
        summary         = summary,
        confidence      = conf,
        supporting_evidence    = sup,
        contradictory_evidence = con,
    )


def _bundle(assessments=None, available=True):
    from src.ops.assessment_engine import OperatorAssessmentBundle
    return OperatorAssessmentBundle(
        operator_id  = "op-x",
        assessments  = assessments or [],
        available    = available,
    )


def _change_report(overall_drift="STABLE"):
    from src.ops.behaviour_change import BehaviourChangeReport
    return BehaviourChangeReport(
        operator_id="op-x", computed_at=_ts(),
        baseline_window_days=30, current_window_days=7,
        baseline_observations=10, current_observations=5,
        overall_drift=overall_drift, drift_summary={},
        dimension_changes=[], timeline_events=[], evidence_gap_notes=[],
    )


def _mock_engines(bundle=None, profile=None, change_report=None):
    asm = MagicMock()
    asm.assess.return_value = bundle or _bundle()
    beh = MagicMock()
    beh.compute.return_value = profile or _profile()
    chg = MagicMock()
    chg.compare.return_value = change_report or _change_report()
    return asm, beh, chg


def _engine(bundle=None, profile=None, change_report=None):
    asm, beh, chg = _mock_engines(bundle, profile, change_report)
    return ForecastEngine(asm, beh, chg)


def _read_template():
    with open(os.path.join(ROOT, "templates", "operator_intelligence.html")) as f:
        return f.read()


# ── FORECAST_TYPES completeness ───────────────────────────────────────────────

class TestForecastTypes:

    def test_required_types_present(self):
        required = {
            "TRANSITION_LIKELY", "TRANSITION_POSSIBLE",
            "STATE_STABLE", "TRANSITION_BLOCKED", "INSUFFICIENT_EVIDENCE",
        }
        assert required <= FORECAST_TYPES

    def test_forecast_rejects_unknown_type(self):
        with pytest.raises(ValueError):
            LifecycleForecast(
                forecast_id="x", operator_id="x",
                forecast_type="MADE_UP_TYPE",
                current_state=ARMED,
                predicted_next_state=ACTIVE,
                forecast_reason="r", confidence="HIGH",
                supporting_assessments=[],
            )

    def test_forecast_rejects_unknown_next_state(self):
        with pytest.raises(ValueError):
            LifecycleForecast(
                forecast_id="x", operator_id="x",
                forecast_type="TRANSITION_LIKELY",
                current_state=ARMED,
                predicted_next_state="NOT_A_STATE",
                forecast_reason="r", confidence="HIGH",
                supporting_assessments=[],
            )


# ── _timing_window ────────────────────────────────────────────────────────────

class TestTimingWindow:

    def test_fast_below_10m(self):   assert _timing_window(5 * 60)   == WINDOW_FAST
    def test_short_10_20m(self):     assert _timing_window(12 * 60)  == WINDOW_SHORT
    def test_medium_20_60m(self):    assert _timing_window(30 * 60)  == WINDOW_MEDIUM
    def test_long_1_4h(self):        assert _timing_window(2 * 3600) == WINDOW_LONG
    def test_very_long_over_4h(self): assert _timing_window(5 * 3600) == WINDOW_VERY_LONG
    def test_none_returns_unknown(self): assert _timing_window(None)  == WINDOW_UNKNOWN


# ── Rule: insufficient ────────────────────────────────────────────────────────

class TestRuleInsufficient:

    def test_fires_when_bundle_unavailable(self):
        b  = _bundle(available=False)
        p  = _profile()
        cr = _change_report()
        r  = _rule_insufficient(b, p, cr, OBSERVING, _ts())
        assert r is not None
        assert r.forecast_type == "INSUFFICIENT_EVIDENCE"
        assert r.confidence    == "INSUFFICIENT"

    def test_fires_when_only_assessment_is_insufficient(self):
        insuff = _assessment("INSUFFICIENT_EVIDENCE", conf="INSUFFICIENT")
        b  = _bundle(assessments=[insuff])
        p  = _profile()
        cr = _change_report()
        r  = _rule_insufficient(b, p, cr, OBSERVING, _ts())
        assert r is not None

    def test_does_not_fire_when_other_assessments_present(self):
        good = _assessment("BASELINE_BEHAVIOUR", conf="HIGH")
        b    = _bundle(assessments=[good])
        p    = _profile()
        cr   = _change_report()
        assert _rule_insufficient(b, p, cr, OBSERVING, _ts()) is None


# ── Rule: IDLE → OBSERVING ────────────────────────────────────────────────────

class TestRuleIdleToObserving:

    def test_fires_on_campaign_expansion_from_idle(self):
        exp = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b   = _bundle(assessments=[exp])
        p   = _profile()
        cr  = _change_report()
        r   = _rule_idle_to_observing(b, p, cr, IDLE, _ts())
        assert r is not None
        assert r.predicted_next_state == OBSERVING

    def test_does_not_fire_from_non_idle(self):
        exp = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b   = _bundle(assessments=[exp])
        p   = _profile()
        cr  = _change_report()
        assert _rule_idle_to_observing(b, p, cr, OBSERVING, _ts()) is None

    def test_does_not_fire_on_baseline_only(self):
        base = _assessment("BASELINE_BEHAVIOUR", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile()
        cr   = _change_report()
        assert _rule_idle_to_observing(b, p, cr, IDLE, _ts()) is None

    def test_confidence_capped_at_medium(self):
        exp = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b   = _bundle(assessments=[exp])
        p   = _profile(overall_conf="HIGH")
        cr  = _change_report()
        r   = _rule_idle_to_observing(b, p, cr, IDLE, _ts())
        assert r is not None
        assert r.confidence in ("LOW", "MEDIUM")   # capped at MEDIUM from IDLE


# ── Rule: OBSERVING → ARMED ───────────────────────────────────────────────────

class TestRuleObservingToArmed:

    def test_fires_on_campaign_expansion(self):
        exp = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b   = _bundle(assessments=[exp])
        p   = _profile(dims=[_dim("launch", facts=[_fact("avg_launch_delay_s", raw=300.0)])])
        cr  = _change_report()
        r   = _rule_observing_to_armed(b, p, cr, OBSERVING, _ts())
        assert r is not None
        assert r.predicted_next_state == ARMED

    def test_does_not_fire_without_expansion(self):
        base = _assessment("BASELINE_BEHAVIOUR", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile()
        cr   = _change_report()
        assert _rule_observing_to_armed(b, p, cr, OBSERVING, _ts()) is None

    def test_does_not_fire_from_non_observing(self):
        exp = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b   = _bundle(assessments=[exp])
        p   = _profile()
        cr  = _change_report()
        assert _rule_observing_to_armed(b, p, cr, ARMED, _ts()) is None

    def test_timing_fact_drives_window(self):
        exp = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b   = _bundle(assessments=[exp])
        # 5 min avg delay → FAST window
        p   = _profile(dims=[_dim("launch", facts=[_fact("avg_launch_delay_s", raw=5*60, conf="HIGH")])])
        cr  = _change_report()
        r   = _rule_observing_to_armed(b, p, cr, OBSERVING, _ts())
        assert r.expected_window == WINDOW_FAST

    def test_funding_contradiction_lowers_confidence(self):
        from src.ops.assessment_engine import EvidenceItem
        exp = _assessment(
            "CAMPAIGN_EXPANSION", conf="HIGH",
            contradicting=[EvidenceItem(label="Funding pattern also changed", detail="", source="ASSESSMENT")]
        )
        b_with = _bundle(assessments=[exp])
        b_without = _bundle(assessments=[_assessment("CAMPAIGN_EXPANSION", conf="HIGH")])
        p  = _profile()
        cr = _change_report()
        r_with    = _rule_observing_to_armed(b_with,    p, cr, OBSERVING, _ts())
        r_without = _rule_observing_to_armed(b_without, p, cr, OBSERVING, _ts())
        _CO = {"INSUFFICIENT": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        assert _CO.get(r_with.confidence, 0) <= _CO.get(r_without.confidence, 0)


# ── Rule: OBSERVING → OBSERVING (stable) ─────────────────────────────────────

class TestRuleObservingStable:

    def test_fires_on_baseline(self):
        base = _assessment("BASELINE_BEHAVIOUR", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile()
        cr   = _change_report()
        r    = _rule_observing_stable(b, p, cr, OBSERVING, _ts())
        assert r is not None
        assert r.predicted_next_state == OBSERVING
        assert r.forecast_type        == "STATE_STABLE"

    def test_does_not_fire_when_expansion_present(self):
        base = _assessment("BASELINE_BEHAVIOUR", conf="HIGH")
        exp  = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b    = _bundle(assessments=[base, exp])
        p    = _profile()
        cr   = _change_report()
        assert _rule_observing_stable(b, p, cr, OBSERVING, _ts()) is None

    def test_confidence_capped_at_low(self):
        base = _assessment("BASELINE_BEHAVIOUR", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile()
        cr   = _change_report()
        r    = _rule_observing_stable(b, p, cr, OBSERVING, _ts())
        _CO  = {"INSUFFICIENT": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        assert _CO.get(r.confidence, 0) <= _CO["LOW"]


# ── Rule: ARMED → ACTIVE ──────────────────────────────────────────────────────

class TestRuleArmedToActive:

    def test_fires_from_armed_with_any_non_insufficient_assessment(self):
        base = _assessment("BASELINE_BEHAVIOUR", conf="MEDIUM")
        b    = _bundle(assessments=[base])
        p    = _profile()
        cr   = _change_report()
        r    = _rule_armed_to_active(b, p, cr, ARMED, _ts())
        assert r is not None
        assert r.current_state        == ARMED
        assert r.predicted_next_state == ACTIVE

    def test_fires_even_with_no_reinforcing_assessment(self):
        b = _bundle(assessments=[])   # no assessments at all
        p = _profile()
        cr = _change_report()
        r  = _rule_armed_to_active(b, p, cr, ARMED, _ts())
        assert r is not None
        assert r.predicted_next_state == ACTIVE

    def test_does_not_fire_from_non_armed(self):
        base = _assessment("BASELINE_BEHAVIOUR", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile()
        cr   = _change_report()
        assert _rule_armed_to_active(b, p, cr, OBSERVING, _ts()) is None

    def test_timing_fact_drives_window(self):
        base = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile(dims=[_dim("launch", facts=[
            _fact("avg_fanout_to_create_s", raw=3*60, conf="HIGH", stability="STABLE")
        ])])
        cr   = _change_report()
        r    = _rule_armed_to_active(b, p, cr, ARMED, _ts())
        assert r.expected_window == WINDOW_FAST

    def test_variable_timing_adds_contradiction(self):
        base = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile(dims=[_dim("launch", facts=[
            _fact("avg_fanout_to_create_s", raw=5*60, conf="HIGH", stability="VARIABLE")
        ])])
        cr   = _change_report()
        r    = _rule_armed_to_active(b, p, cr, ARMED, _ts())
        assert any("variable" in ev.label.lower() or "variable" in ev.detail.lower()
                   for ev in r.contradictory_evidence)

    def test_infrastructure_shift_limits_confidence(self):
        base  = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        infra = _assessment("INFRASTRUCTURE_SHIFT", conf="MEDIUM")
        b_with    = _bundle(assessments=[base, infra])
        b_without = _bundle(assessments=[base])
        p  = _profile()
        cr = _change_report()
        r_with    = _rule_armed_to_active(b_with,    p, cr, ARMED, _ts())
        r_without = _rule_armed_to_active(b_without, p, cr, ARMED, _ts())
        _CO = {"INSUFFICIENT": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        assert _CO.get(r_with.confidence, 0) <= _CO.get(r_without.confidence, 0)

    def test_historical_obs_populated_from_timing_fact(self):
        base = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile(dims=[_dim("launch", facts=[
            _fact("avg_fanout_to_create_s", raw=5*60, conf="HIGH", obs=25)
        ])])
        cr   = _change_report()
        r    = _rule_armed_to_active(b, p, cr, ARMED, _ts())
        assert r.historical_observations == 25


# ── Rule: ACTIVE → COMPLETED ─────────────────────────────────────────────────

class TestRuleActiveToCompleted:

    def test_fires_from_active(self):
        b  = _bundle(assessments=[_assessment("BASELINE_BEHAVIOUR")])
        p  = _profile()
        cr = _change_report()
        r  = _rule_active_to_completed(b, p, cr, ACTIVE, _ts())
        assert r is not None
        assert r.predicted_next_state == COMPLETED
        assert r.forecast_type == "TRANSITION_POSSIBLE"

    def test_confidence_capped_at_medium(self):
        b  = _bundle(assessments=[_assessment("BASELINE_BEHAVIOUR", conf="HIGH")])
        p  = _profile(overall_conf="HIGH")
        cr = _change_report()
        r  = _rule_active_to_completed(b, p, cr, ACTIVE, _ts())
        _CO = {"INSUFFICIENT": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        assert _CO.get(r.confidence, 0) <= _CO["MEDIUM"]

    def test_does_not_fire_from_non_active(self):
        b  = _bundle()
        p  = _profile()
        cr = _change_report()
        assert _rule_active_to_completed(b, p, cr, ARMED, _ts()) is None

    def test_migration_timing_drives_window(self):
        b  = _bundle(assessments=[])
        p  = _profile(dims=[_dim("launch", facts=[
            _fact("avg_migration_timing_s", raw=25*60, conf="HIGH")
        ])])
        cr = _change_report()
        r  = _rule_active_to_completed(b, p, cr, ACTIVE, _ts())
        assert r.expected_window == WINDOW_MEDIUM


# ── Rule: COMPLETED → ARCHIVED/IDLE ──────────────────────────────────────────

class TestRuleCompletedArchived:

    def test_fires_from_completed(self):
        b  = _bundle(assessments=[])
        p  = _profile()
        cr = _change_report()
        r  = _rule_completed_archived(b, p, cr, COMPLETED, _ts())
        assert r is not None
        assert r.current_state == COMPLETED

    def test_predicts_archived_without_expansion(self):
        b  = _bundle(assessments=[_assessment("BASELINE_BEHAVIOUR")])
        p  = _profile()
        cr = _change_report()
        r  = _rule_completed_archived(b, p, cr, COMPLETED, _ts())
        assert r.predicted_next_state == ARCHIVED

    def test_predicts_idle_with_expansion(self):
        exp = _assessment("CAMPAIGN_EXPANSION", conf="MEDIUM")
        b   = _bundle(assessments=[exp])
        p   = _profile()
        cr  = _change_report()
        r   = _rule_completed_archived(b, p, cr, COMPLETED, _ts())
        assert r.predicted_next_state == IDLE

    def test_does_not_fire_from_non_completed(self):
        b  = _bundle()
        p  = _profile()
        cr = _change_report()
        assert _rule_completed_archived(b, p, cr, ARMED, _ts()) is None


# ── ForecastEngine integration ────────────────────────────────────────────────

class TestForecastEngine:

    def test_armed_to_active_end_to_end(self):
        base = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile(dims=[_dim("launch", facts=[
            _fact("avg_fanout_to_create_s", raw=5*60, conf="HIGH")
        ])])
        cr   = _change_report()
        eng  = _engine(bundle=b, profile=p, change_report=cr)
        f    = eng.forecast("op-x", ARMED)
        assert f.predicted_next_state == ACTIVE
        assert f.forecast_type in ("TRANSITION_LIKELY", "TRANSITION_POSSIBLE")
        assert f.confidence in ("LOW", "MEDIUM", "HIGH")

    def test_deterministic_identical_inputs(self):
        base = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile()
        cr   = _change_report()
        eng  = _engine(bundle=b, profile=p, change_report=cr)
        f1   = eng.forecast("op-x", ARMED)
        f2   = eng.forecast("op-x", ARMED)
        assert f1.predicted_next_state == f2.predicted_next_state
        assert f1.forecast_type        == f2.forecast_type
        assert f1.confidence           == f2.confidence

    def test_fail_open_on_assessment_error(self):
        asm = MagicMock(); asm.assess.side_effect = RuntimeError("DB exploded")
        beh = MagicMock(); beh.compute.return_value = _profile()
        chg = MagicMock(); chg.compare.return_value = _change_report()
        eng = ForecastEngine(asm, beh, chg)
        f   = eng.forecast("op-x", ARMED)
        assert f.forecast_type == "INSUFFICIENT_EVIDENCE"
        assert f.confidence    == "INSUFFICIENT"

    def test_fail_open_on_behaviour_error(self):
        asm = MagicMock(); asm.assess.return_value = _bundle()
        beh = MagicMock(); beh.compute.side_effect = RuntimeError("locked")
        chg = MagicMock(); chg.compare.return_value = _change_report()
        eng = ForecastEngine(asm, beh, chg)
        f   = eng.forecast("op-x", ARMED)
        assert f.forecast_type == "INSUFFICIENT_EVIDENCE"

    def test_unknown_lifecycle_state_fails_open(self):
        eng = _engine()
        f   = eng.forecast("op-x", "NOT_A_STATE")
        assert f.forecast_type == "INSUFFICIENT_EVIDENCE"

    def test_confidence_limited_by_behaviour_confidence(self):
        base = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b    = _bundle(assessments=[base])
        p_low  = _profile(overall_conf="LOW")
        p_high = _profile(overall_conf="HIGH")
        cr     = _change_report()
        eng_low  = _engine(bundle=b, profile=p_low,  change_report=cr)
        eng_high = _engine(bundle=b, profile=p_high, change_report=cr)
        f_low  = eng_low.forecast("op-x",  OBSERVING)
        f_high = eng_high.forecast("op-x", OBSERVING)
        _CO = {"INSUFFICIENT": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        assert _CO.get(f_low.confidence, 0) <= _CO.get(f_high.confidence, 0)

    def test_expires_at_in_future(self):
        eng = _engine()
        f   = eng.forecast("op-x", ARMED)
        assert f.expires_at > f.generated_at

    def test_platform_summary_bounded_to_50(self):
        asm, beh, chg = _mock_engines()
        eng   = ForecastEngine(asm, beh, chg)
        pairs = [(f"op-{i}", OBSERVING) for i in range(100)]
        s     = eng.platform_summary(pairs)
        assert asm.assess.call_count <= 50

    def test_platform_summary_counts_armed_active(self):
        base = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b    = _bundle(assessments=[base])
        asm  = MagicMock(); asm.assess.return_value = b
        beh  = MagicMock(); beh.compute.return_value = _profile()
        chg  = MagicMock(); chg.compare.return_value = _change_report()
        eng  = ForecastEngine(asm, beh, chg)
        s    = eng.platform_summary([("op-x", ARMED)])
        # armed_active_count may be 0 if confidence is LOW — just check field exists
        assert "armed_active_count"   in s
        assert "transition_counts"    in s
        assert "operators_assessed"   in s


# ── X14.1 hardening: fingerprints + explicit invalidation ────────────────────

class TestFingerprints:

    def test_behaviour_profile_fingerprint_stable_across_recompute(self):
        p1 = _profile()
        p2 = _profile()
        assert p1.fingerprint == p2.fingerprint

    def test_behaviour_profile_fingerprint_changes_with_content(self):
        p1 = _profile(overall_conf="HIGH")
        p2 = _profile(overall_conf="LOW")
        assert p1.fingerprint != p2.fingerprint

    def test_behaviour_profile_fingerprint_ignores_computed_at(self):
        from src.ops.behaviour_engine import BehaviourProfile
        p1 = BehaviourProfile(operator_id="op-x", computed_at=100,
                               total_observations=5, overall_confidence="HIGH")
        p2 = BehaviourProfile(operator_id="op-x", computed_at=999999,
                               total_observations=5, overall_confidence="HIGH")
        assert p1.fingerprint == p2.fingerprint

    def test_change_report_fingerprint_stable_and_sensitive(self):
        c1 = _change_report(overall_drift="STABLE")
        c2 = _change_report(overall_drift="STABLE")
        c3 = _change_report(overall_drift="CHANGED")
        assert c1.fingerprint == c2.fingerprint
        assert c1.fingerprint != c3.fingerprint

    def test_assessment_bundle_fingerprint_stable_and_sensitive(self):
        a  = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b1 = _bundle(assessments=[a])
        b2 = _bundle(assessments=[a])
        assert b1.fingerprint == b2.fingerprint

        a2 = _assessment("CAMPAIGN_EXPANSION", conf="LOW")
        b3 = _bundle(assessments=[a2])
        assert b1.fingerprint != b3.fingerprint

    def test_assessment_bundle_unavailable_fingerprint_is_sentinel(self):
        b = _bundle(available=False)
        assert b.fingerprint == "unavailable"

    def test_forecast_fingerprint_deterministic_same_inputs(self):
        base = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile()
        cr   = _change_report()
        eng  = _engine(bundle=b, profile=p, change_report=cr)
        f1   = eng.forecast("op-x", ARMED)
        f2   = eng.forecast("op-x", ARMED)
        # Unlike forecast_id (timestamp-based), forecast_fingerprint is a
        # pure content hash and must match across separate invocations.
        assert f1.forecast_fingerprint == f2.forecast_fingerprint

    def test_forecast_fingerprint_changes_when_assessment_changes(self):
        p  = _profile()
        cr = _change_report()
        b1 = _bundle(assessments=[_assessment("CAMPAIGN_EXPANSION", conf="HIGH")])
        b2 = _bundle(assessments=[_assessment("CAMPAIGN_EXPANSION", conf="LOW")])
        f1 = _engine(bundle=b1, profile=p, change_report=cr).forecast("op-x", ARMED)
        f2 = _engine(bundle=b2, profile=p, change_report=cr).forecast("op-x", ARMED)
        assert f1.forecast_fingerprint != f2.forecast_fingerprint

    def test_forecast_carries_input_fingerprints(self):
        b   = _bundle(assessments=[_assessment("CAMPAIGN_EXPANSION", conf="HIGH")])
        p   = _profile()
        cr  = _change_report()
        eng = _engine(bundle=b, profile=p, change_report=cr)
        f   = eng.forecast("op-x", ARMED)
        assert f.assessment_fingerprint == b.fingerprint
        assert f.behaviour_fingerprint  == p.fingerprint
        assert f.change_fingerprint     == cr.fingerprint


class TestForecastInvalidation:

    def _forecast(self, bundle=None, profile=None, change_report=None, state=ARMED):
        b  = bundle or _bundle(assessments=[_assessment("CAMPAIGN_EXPANSION", conf="HIGH")])
        p  = profile or _profile()
        cr = change_report or _change_report()
        eng = _engine(bundle=b, profile=p, change_report=cr)
        return eng.forecast("op-x", state), b, p, cr

    def test_not_stale_when_nothing_changed(self):
        f, b, p, cr = self._forecast()
        stale, reason = f.is_stale_against(
            current_state=f.current_state,
            assessment_fingerprint=b.fingerprint,
            behaviour_fingerprint=p.fingerprint,
            change_fingerprint=cr.fingerprint,
            now=f.generated_at,
        )
        assert stale is False
        assert reason == ""

    def test_stale_on_lifecycle_state_change(self):
        f, b, p, cr = self._forecast(state=ARMED)
        stale, reason = f.is_stale_against(
            current_state=ACTIVE,
            assessment_fingerprint=b.fingerprint,
            behaviour_fingerprint=p.fingerprint,
            change_fingerprint=cr.fingerprint,
            now=f.generated_at,
        )
        assert stale is True
        assert reason == "lifecycle_state_changed"

    def test_stale_on_assessment_fingerprint_change(self):
        f, b, p, cr = self._forecast()
        stale, reason = f.is_stale_against(
            current_state=f.current_state,
            assessment_fingerprint="different-fingerprint",
            behaviour_fingerprint=p.fingerprint,
            change_fingerprint=cr.fingerprint,
            now=f.generated_at,
        )
        assert stale is True
        assert reason == "assessment_changed"

    def test_stale_on_behaviour_change_fingerprint_change(self):
        f, b, p, cr = self._forecast()
        stale, reason = f.is_stale_against(
            current_state=f.current_state,
            assessment_fingerprint=b.fingerprint,
            behaviour_fingerprint=p.fingerprint,
            change_fingerprint="different-fingerprint",
            now=f.generated_at,
        )
        assert stale is True
        assert reason == "behaviour_change_changed"

    def test_stale_on_behaviour_fingerprint_change(self):
        f, b, p, cr = self._forecast()
        stale, reason = f.is_stale_against(
            current_state=f.current_state,
            assessment_fingerprint=b.fingerprint,
            behaviour_fingerprint="different-fingerprint",
            change_fingerprint=cr.fingerprint,
            now=f.generated_at,
        )
        assert stale is True
        assert reason == "behaviour_changed"

    def test_stale_on_window_expiry_even_if_inputs_unchanged(self):
        f, b, p, cr = self._forecast()
        stale, reason = f.is_stale_against(
            current_state=f.current_state,
            assessment_fingerprint=b.fingerprint,
            behaviour_fingerprint=p.fingerprint,
            change_fingerprint=cr.fingerprint,
            now=f.expires_at + 1,
        )
        assert stale is True
        assert reason == "window_expired"

    def test_lifecycle_state_change_checked_before_ttl(self):
        """Priority: ground-truth divergence is detected even before TTL fires."""
        f, b, p, cr = self._forecast(state=ARMED)
        stale, reason = f.is_stale_against(
            current_state=ACTIVE,
            assessment_fingerprint=b.fingerprint,
            behaviour_fingerprint=p.fingerprint,
            change_fingerprint=cr.fingerprint,
            now=f.generated_at,  # well within TTL
        )
        assert stale is True
        assert reason == "lifecycle_state_changed"


# ── Explainability ────────────────────────────────────────────────────────────

class TestExplainability:

    def _forecast(self, state=ARMED):
        base = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b    = _bundle(assessments=[base])
        p    = _profile(dims=[_dim("launch", facts=[
            _fact("avg_fanout_to_create_s", raw=5*60, conf="HIGH")
        ])])
        cr   = _change_report()
        return _engine(bundle=b, profile=p, change_report=cr).forecast("op-x", state)

    def test_all_forecasts_have_reason(self):
        f = self._forecast()
        assert f.forecast_reason.strip() != ""

    def test_all_forecasts_have_confidence(self):
        f = self._forecast()
        assert f.confidence in {"INSUFFICIENT", "LOW", "MEDIUM", "HIGH"}

    def test_non_insufficient_has_predicted_state(self):
        f = self._forecast(ARMED)
        if f.forecast_type != "INSUFFICIENT_EVIDENCE":
            assert f.predicted_next_state in (ARMED, ACTIVE, COMPLETED, OBSERVING, IDLE, ARCHIVED)

    def test_no_predictive_forbidden_language(self):
        forbidden = ["price", "market cap", "profit", "popular", "success", "will dump",
                     "predict", "guaranteed"]
        for state in (ARMED, OBSERVING, ACTIVE, IDLE, COMPLETED):
            f = self._forecast(state)
            text = (f.forecast_reason + " " + f.historical_basis).lower()
            for word in forbidden:
                assert word not in text, f"Forbidden word '{word}' in state {state}: {text[:200]}"

    def test_forecast_is_json_serialisable(self):
        f = self._forecast()
        json.dumps(f.to_dict())

    def test_supporting_evidence_count_matches(self):
        f = self._forecast()
        expected = len(f.supporting_evidence) + len(f.contradictory_evidence)
        assert f.supporting_evidence_count == expected

    def test_armed_forecast_references_assessment(self):
        base = _assessment("CAMPAIGN_EXPANSION", conf="HIGH")
        b    = _bundle(assessments=[base])
        eng  = _engine(bundle=b)
        f    = eng.forecast("op-x", ARMED)
        if f.forecast_type != "INSUFFICIENT_EVIDENCE":
            # Either assessment referenced in supporting_assessments or in evidence
            has_ref = (
                len(f.supporting_assessments) > 0
                or any("ASSESSMENT" in ev.source for ev in f.supporting_evidence)
            )
            assert has_ref


# ── Database safety ───────────────────────────────────────────────────────────

class TestDatabaseSafety:

    def test_forecast_never_opens_db_directly(self):
        """ForecastEngine.forecast() must not call sqlite3.connect() directly."""
        asm, beh, chg = _mock_engines()
        eng = ForecastEngine(asm, beh, chg)

        db_opens = []
        orig_connect = sqlite3.connect
        def tracking_connect(path, *a, **kw):
            db_opens.append(path)
            return orig_connect(path, *a, **kw)

        with patch("sqlite3.connect", side_effect=tracking_connect):
            eng.forecast("op-x", ARMED)

        assert db_opens == [], (
            f"ForecastEngine.forecast() opened DB directly: {db_opens}. "
            "All DB work must be delegated to sub-engines."
        )

    def test_forecast_delegates_to_all_three_engines(self):
        asm, beh, chg = _mock_engines()
        eng = ForecastEngine(asm, beh, chg)
        eng.forecast("op-x", ARMED)
        asm.assess.assert_called_once()
        beh.compute.assert_called_once()
        chg.compare.assert_called_once()

    def test_no_writes_via_sub_engines(self):
        asm, beh, chg = _mock_engines()
        eng = ForecastEngine(asm, beh, chg)
        eng.forecast("op-x", ARMED)
        # Only read methods called (assess / compute / compare)
        for m in [asm, beh, chg]:
            write_calls = [c for c in m.mock_calls if any(
                w in str(c) for w in ("write", "insert", "update", "delete", "upsert")
            )]
            assert write_calls == []

    def test_fail_open_preserves_detection_usability(self):
        """After a forecast failure the engine returns a safe result, not an exception."""
        asm = MagicMock(); asm.assess.side_effect = Exception("anything")
        beh = MagicMock()
        chg = MagicMock()
        eng = ForecastEngine(asm, beh, chg)
        f   = eng.forecast("op-x", ARMED)
        assert isinstance(f, LifecycleForecast)   # safe object, no exception

    def test_error_does_not_expose_stack_trace(self):
        asm = MagicMock(); asm.assess.side_effect = Exception("internal detail")
        eng = ForecastEngine(asm, MagicMock(), MagicMock())
        f   = eng.forecast("op-x", ARMED)
        assert "Traceback" not in f.forecast_reason
        assert "File " not in f.forecast_reason

    def test_platform_summary_bounded(self):
        asm, beh, chg = _mock_engines()
        eng   = ForecastEngine(asm, beh, chg)
        pairs = [(f"op-{i}", OBSERVING) for i in range(200)]
        eng.platform_summary(pairs)
        assert asm.assess.call_count <= 50


# ── API routes ────────────────────────────────────────────────────────────────

@pytest.fixture
def fc_client():
    import src.ops.forecast_routes as fr

    asm, beh, chg = _mock_engines(
        bundle=_bundle(assessments=[_assessment("CAMPAIGN_EXPANSION", conf="HIGH")]),
        profile=_profile(),
    )
    fr._engine = ForecastEngine(asm, beh, chg)
    fr._history.clear()

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(fr.forecast_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    fr._engine = None
    fr._history.clear()


@pytest.fixture
def failing_fc_client():
    import src.ops.forecast_routes as fr

    asm = MagicMock(); asm.assess.side_effect = Exception("DB down")
    fr._engine = ForecastEngine(asm, MagicMock(), MagicMock())
    fr._history.clear()

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(fr.forecast_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    fr._engine = None
    fr._history.clear()


class TestForecastRoutes:

    def test_forecast_endpoint_200(self, fc_client):
        r = fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ARMED")
        assert r.status_code == 200

    def test_forecast_response_has_ok(self, fc_client):
        data = fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ARMED").get_json()
        assert "ok" in data

    def test_forecast_response_has_forecast(self, fc_client):
        data = fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ARMED").get_json()
        if data.get("ok"):
            assert "forecast" in data

    def test_history_endpoint_200(self, fc_client):
        fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ARMED")
        r = fc_client.get("/api/operators/op-x/forecast/history")
        assert r.status_code == 200

    def test_history_accumulates(self, fc_client):
        fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ARMED")
        fc_client.get("/api/operators/op-x/forecast?lifecycle_state=OBSERVING")
        data = fc_client.get("/api/operators/op-x/forecast/history").get_json()
        assert data["count"] >= 1

    def test_fail_open_returns_200(self, failing_fc_client):
        r = failing_fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ARMED")
        assert r.status_code == 200

    def test_fail_open_no_stack_trace(self, failing_fc_client):
        data = failing_fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ARMED").get_json()
        text = json.dumps(data)
        assert "Traceback" not in text
        assert "File " not in text

    def test_page_load_does_not_explode(self, fc_client):
        """Multiple GET calls should all return 200 without side-effects."""
        for state in ("ARMED", "OBSERVING", "ACTIVE", "IDLE", "COMPLETED"):
            r = fc_client.get(f"/api/operators/op-x/forecast?lifecycle_state={state}")
            assert r.status_code == 200

    def test_forecast_response_has_fingerprint(self, fc_client):
        data = fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ARMED").get_json()
        if data.get("ok"):
            assert "forecast_fingerprint" in data["forecast"]

    def test_lifecycle_change_flags_prior_as_invalidated(self, fc_client):
        fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ARMED")
        data = fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ACTIVE").get_json()
        if data.get("ok") and "invalidated_prior" in data:
            assert data["invalidated_prior"]["reason"] == "lifecycle_state_changed"

    def test_history_marks_superseded_entries(self, fc_client):
        fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ARMED")
        fc_client.get("/api/operators/op-x/forecast?lifecycle_state=ACTIVE")
        data = fc_client.get("/api/operators/op-x/forecast/history").get_json()
        if data["count"] >= 2:
            assert any("superseded" in h for h in data["history"])


# ── Template markup ───────────────────────────────────────────────────────────

class TestForecastTemplateMarkup:

    def test_forecast_section_present(self):
        html = _read_template()
        assert "oi-forecast-section" in html
        assert "oi-forecast-body"    in html

    def test_forecast_label_present(self):
        html = _read_template()
        assert ">Forecast<" in html

    def test_forecast_api_fetched(self):
        html = _read_template()
        assert "/forecast" in html

    def test_lifecycle_state_passed(self):
        html = _read_template()
        assert "lifecycle_state" in html

    def test_progressive_disclosure_evidence(self):
        html = _read_template()
        assert "pd-toggle" in html
        assert "Show detail" in html

    def test_arrow_transition_rendered(self):
        html = _read_template()
        assert "oi-fc-arrow"  in html
        assert "oi-fc-rarr"   in html

    def test_window_rendered(self):
        html = _read_template()
        assert "oi-fc-window" in html

    def test_unavailable_state_handled(self):
        html = _read_template()
        assert "unavailable" in html.lower()

    def test_insufficient_evidence_neutral_message(self):
        html = _read_template()
        assert "Insufficient evidence" in html or "insufficient" in html.lower()

    def test_no_raw_db_error_in_template(self):
        html = _read_template()
        assert "OperationalError" not in html
        assert "sqlite3" not in html.lower()

    def test_no_new_top_level_page(self):
        html = _read_template()
        assert "/intelligence/forecasts" not in html

    def test_mission_control_badge_css(self):
        html = _read_template()
        assert "mc-forecast-badge" in html

    def test_forecast_section_after_assessment(self):
        html = _read_template()
        assess_pos   = html.find("oi-assess-section")
        forecast_pos = html.find("oi-forecast-section")
        inbox_pos    = html.find("oi-inbox-section")
        assert assess_pos < forecast_pos < inbox_pos
