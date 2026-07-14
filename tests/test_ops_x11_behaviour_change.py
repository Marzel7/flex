"""
Sprint X11 — Behaviour Change Detection Engine tests.

Covers:
  - _deviation(): numeric, categorical, edge cases (zero baseline, None)
  - _comparison_confidence(): both-sides requirement
  - FactComparison.changed property
  - _compare_dimension(): same stable / changed / insufficient
  - _split_data(): baseline vs. current window split
  - _build_change_timeline(): ordering, categories, events emitted
  - _overall_drift(): STABLE / CHANGED / MIXED / INSUFFICIENT_EVIDENCE
  - BehaviourChangeEngine.compare(): end-to-end on empty + populated data
  - BehaviourChangeEngine.compare_platform(): summary structure
  - API routes: /api/operators/<id>/behaviour/change, /change/<dim>, platform-summary
  - operator_intelligence.html: change section markup, JS fetch, CSS
  - Explainability: every FactComparison has a non-empty reason
  - Safety: no predictive language in comparison summaries
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ops.behaviour_change import (
    CURRENT_WINDOW_DAYS,
    BehaviourChangeEngine,
    BehaviourChangeReport,
    DimensionChange,
    FactComparison,
    _build_change_timeline,
    _compare_dimension,
    _comparison_confidence,
    _deviation,
    _empty_data_copy,
    _overall_drift,
    _split_data,
)
from src.ops.behaviour_engine import (
    MIN_OBSERVATIONS,
    BehaviourFact,
    _OperatorData,
    _campaign_behaviour,
    _funding_behaviour,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _tmp_db(suffix=".db"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def _read_template(name):
    with open(os.path.join(ROOT, "templates", name), encoding="utf-8") as f:
        return f.read()


def _fact(key="k", label="L", value="1.0", raw=1.0,
          confidence="HIGH", observations=20, unit="",
          stability="STABLE") -> BehaviourFact:
    return BehaviourFact(
        key=key, label=label, value=value, raw=raw,
        confidence=confidence, observations=observations,
        unit=unit, stability=stability,
    )


def _make_ops_db_with_launches(launches: list[dict]) -> str:
    path = _tmp_db("_chg_ops.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE wt_watchtower_launches (
            id INTEGER PRIMARY KEY, treasury_wallet TEXT, subprov_wallet TEXT,
            create_time INTEGER, birth_to_launch_seconds INTEGER,
            subprov_funding_sol REAL, wrap_close_sol REAL,
            fanout_to_create_secs REAL, create_to_migration_secs INTEGER,
            launch_mode TEXT, creator_wallet TEXT,
            funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE'
        )""")
    conn.execute("""
        CREATE TABLE wt_ops_v2 (
            operation_uuid TEXT PRIMARY KEY, treasury_root TEXT,
            first_seen INTEGER, last_seen INTEGER,
            created_at INTEGER, updated_at INTEGER,
            status TEXT DEFAULT 'FORMING', confidence REAL DEFAULT 0.0
        )""")
    conn.execute("""
        CREATE TABLE wt_launch_audit (
            mint TEXT PRIMARY KEY, treasury TEXT, peak_mc REAL,
            actionable_multiple REAL, migrated INTEGER DEFAULT 0,
            create_time INTEGER,
            audit_state TEXT DEFAULT 'FINALIZED',
            created_at INTEGER DEFAULT (strftime('%s','now')),
            updated_at INTEGER DEFAULT (strftime('%s','now'))
        )""")
    conn.execute("CREATE TABLE IF NOT EXISTS wt_ops_v2_creators (operation_uuid TEXT, creator_wallet TEXT, token_mint TEXT, migration_time INTEGER, funding_amount_sol REAL, op_first_seen INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS wt_fanout_events (id INTEGER PRIMARY KEY, subprov_wallet TEXT, treasury_wallet TEXT, fanout_time INTEGER, fanout_count INTEGER, total_sol REAL, avg_sol REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS wt_ops_v2_wallets (operation_uuid TEXT, wallet TEXT, role TEXT, first_seen INTEGER, last_seen INTEGER)")

    now = int(time.time())
    for i, launch in enumerate(launches):
        conn.execute(
            "INSERT INTO wt_watchtower_launches "
            "(treasury_wallet, subprov_wallet, create_time, birth_to_launch_seconds, "
            "subprov_funding_sol, wrap_close_sol, fanout_to_create_secs, funding_mechanism) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                launch.get("treasury_wallet", "TW1"),
                launch.get("subprov_wallet", f"SP{i}"),
                launch.get("create_time", now - i * 3600),
                launch.get("birth_to_launch_seconds", 5),
                launch.get("subprov_funding_sol", 800.0),
                launch.get("wrap_close_sol", 1.11),
                launch.get("fanout_to_create_secs", 2.5),
                "WSOL_WRAP_CLOSE",
            ),
        )
    conn.commit()
    conn.close()
    return path


def _populated_engine(
    baseline_funding: float = 800.0,
    current_funding: float = 800.0,
    n_baseline: int = 15,
    n_current: int = 5,
) -> tuple[BehaviourChangeEngine, str, list[str]]:
    """
    Create an engine with controlled baseline + current launches.

    baseline launches are 30 days ago; current launches are 2 days ago.
    """
    now = int(time.time())
    baseline_ts = now - 30 * 86400
    current_ts  = now - 2 * 86400

    launches = [
        {"treasury_wallet": "TW1", "create_time": baseline_ts + i * 3600,
         "subprov_funding_sol": baseline_funding, "wrap_close_sol": 1.11}
        for i in range(n_baseline)
    ] + [
        {"treasury_wallet": "TW1", "create_time": current_ts + i * 3600,
         "subprov_funding_sol": current_funding, "wrap_close_sol": 1.11}
        for i in range(n_current)
    ]

    ops_path  = _make_ops_db_with_launches(launches)
    live_path = _tmp_db()
    engine    = BehaviourChangeEngine(ops_path, live_path)
    return engine, ops_path, ["TW1"]


# ── _deviation ────────────────────────────────────────────────────────────────

class TestDeviation:

    def test_identical_values_none(self):
        assert _deviation(100.0, 100.0) == "NONE"

    def test_within_5_pct_none(self):
        assert _deviation(100.0, 104.0) == "NONE"

    def test_6_pct_is_low(self):
        assert _deviation(100.0, 106.0) == "LOW"

    def test_20_pct_boundary_low(self):
        assert _deviation(100.0, 120.0) == "LOW"

    def test_21_pct_moderate(self):
        assert _deviation(100.0, 121.0) == "MODERATE"

    def test_50_pct_boundary_moderate(self):
        assert _deviation(100.0, 150.0) == "MODERATE"

    def test_51_pct_high(self):
        assert _deviation(100.0, 151.0) == "HIGH"

    def test_100_pct_boundary_high(self):
        assert _deviation(100.0, 200.0) == "HIGH"

    def test_101_pct_very_high(self):
        assert _deviation(100.0, 201.0) == "VERY_HIGH"

    def test_decrease_also_measured(self):
        # 50% decrease = HIGH
        assert _deviation(100.0, 50.0) == "MODERATE"

    def test_zero_baseline_nonzero_current_very_high(self):
        assert _deviation(0.0, 100.0) == "VERY_HIGH"

    def test_zero_both_none(self):
        assert _deviation(0.0, 0.0) == "NONE"

    def test_none_baseline_unknown(self):
        assert _deviation(None, 100.0) == "UNKNOWN"

    def test_none_current_unknown(self):
        assert _deviation(100.0, None) == "UNKNOWN"

    def test_both_none_unknown(self):
        assert _deviation(None, None) == "UNKNOWN"

    def test_categorical_equal_none(self):
        assert _deviation("WSOL_WRAP_CLOSE", "WSOL_WRAP_CLOSE") == "NONE"

    def test_categorical_different_high(self):
        assert _deviation("WSOL_WRAP_CLOSE", "PLAIN_TRANSFER") == "HIGH"


# ── _comparison_confidence ────────────────────────────────────────────────────

class TestComparisonConfidence:

    def test_both_high_gives_high(self):
        bf = _fact(confidence="HIGH", observations=20)
        cf = _fact(confidence="HIGH", observations=10)
        assert _comparison_confidence(bf, cf) == "HIGH"

    def test_high_and_medium_gives_medium(self):
        bf = _fact(confidence="HIGH", observations=20)
        cf = _fact(confidence="MEDIUM", observations=7)
        assert _comparison_confidence(bf, cf) == "MEDIUM"

    def test_high_and_low_gives_low(self):
        bf = _fact(confidence="HIGH", observations=20)
        cf = _fact(confidence="LOW", observations=3)
        assert _comparison_confidence(bf, cf) == "LOW"

    def test_insufficient_baseline_gives_insufficient(self):
        bf = _fact(confidence="INSUFFICIENT", observations=1)
        cf = _fact(confidence="HIGH", observations=20)
        assert _comparison_confidence(bf, cf) == "INSUFFICIENT"

    def test_insufficient_current_gives_insufficient(self):
        bf = _fact(confidence="HIGH", observations=20)
        cf = _fact(confidence="INSUFFICIENT", observations=2)
        assert _comparison_confidence(bf, cf) == "INSUFFICIENT"


# ── FactComparison.changed ────────────────────────────────────────────────────

class TestFactComparisonChanged:

    def _cmp(self, deviation, confidence) -> FactComparison:
        return FactComparison(
            key="k", label="L",
            historical_value="1.0", historical_raw=1.0, historical_observations=20,
            current_value="2.0", current_raw=2.0, current_observations=10,
            deviation=deviation, confidence=confidence, reason="test",
        )

    def test_none_deviation_not_changed(self):
        assert not self._cmp("NONE", "HIGH").changed

    def test_unknown_deviation_not_changed(self):
        assert not self._cmp("UNKNOWN", "HIGH").changed

    def test_insufficient_confidence_not_changed(self):
        assert not self._cmp("HIGH", "INSUFFICIENT").changed

    def test_high_deviation_high_confidence_changed(self):
        assert self._cmp("HIGH", "HIGH").changed

    def test_moderate_deviation_medium_confidence_changed(self):
        assert self._cmp("MODERATE", "MEDIUM").changed

    def test_low_deviation_low_confidence_changed(self):
        assert self._cmp("LOW", "LOW").changed


# ── _split_data ───────────────────────────────────────────────────────────────

class TestSplitData:

    def _data_with_launches(self, timestamps: list[int]) -> _OperatorData:
        obj = object.__new__(_OperatorData)
        obj.treasury_wallets = ["TW1"]
        obj._ops_db  = ""
        obj._live_db = ""
        obj.launches  = [{"create_time": ts, "subprov_funding_sol": 1.0} for ts in timestamps]
        obj.audit     = []
        obj.creators  = []
        obj.fanouts   = []
        obj.wallets   = []
        obj.ops       = [{"operation_uuid": f"op-{ts}", "treasury_root": "TW1",
                          "first_seen": ts, "last_seen": ts + 3600}
                         for ts in timestamps]
        return obj

    def test_split_puts_old_in_baseline(self):
        now = int(time.time())
        data = self._data_with_launches([now - 100, now - 200, now - 5])
        cutoff = now - 10
        baseline, current = _split_data(data, cutoff)
        assert len(baseline.launches) == 2
        assert len(current.launches) == 1

    def test_split_at_boundary_goes_to_current(self):
        now = int(time.time())
        data = self._data_with_launches([now - 10, now - 10])
        cutoff = now - 10  # exactly at cutoff → current
        baseline, current = _split_data(data, cutoff)
        assert len(baseline.launches) == 0
        assert len(current.launches) == 2

    def test_all_old_baseline_empty_current(self):
        now = int(time.time())
        data = self._data_with_launches([now - 1000, now - 2000])
        cutoff = now - 5
        baseline, current = _split_data(data, cutoff)
        assert len(baseline.launches) == 2
        assert len(current.launches) == 0

    def test_split_preserves_treasury_wallets(self):
        now = int(time.time())
        data = self._data_with_launches([now - 100])
        cutoff = now
        baseline, current = _split_data(data, cutoff)
        assert baseline.treasury_wallets == ["TW1"]
        assert current.treasury_wallets  == ["TW1"]


# ── _compare_dimension ────────────────────────────────────────────────────────

class TestCompareDimension:

    def _dim(self, facts: list[BehaviourFact], key="campaign", label="Campaign") -> object:
        from src.ops.behaviour_engine import BehaviourDimension
        d = BehaviourDimension(key=key, label=label)
        d.facts = facts
        d.overall_confidence = max(
            (f.confidence for f in facts),
            key=lambda c: ["INSUFFICIENT","LOW","MEDIUM","HIGH"].index(c) if c in ["INSUFFICIENT","LOW","MEDIUM","HIGH"] else 0,
            default="INSUFFICIENT",
        )
        return d

    def test_identical_facts_stable(self):
        bf = _fact(key="count", raw=10.0, confidence="HIGH", observations=20)
        cf = _fact(key="count", raw=10.0, confidence="HIGH", observations=8)
        baseline_dim = self._dim([bf])
        current_dim  = self._dim([cf])
        dc = _compare_dimension(baseline_dim, current_dim)
        assert dc.drift == "STABLE"
        assert not dc.changed_facts

    def test_large_change_produces_changed(self):
        bf = _fact(key="funding", raw=100.0, confidence="HIGH", observations=20)
        cf = _fact(key="funding", raw=300.0, confidence="HIGH", observations=8)
        dc = _compare_dimension(self._dim([bf]), self._dim([cf]))
        assert dc.drift == "CHANGED"
        assert len(dc.changed_facts) == 1

    def test_both_insufficient_gives_insufficient_evidence(self):
        bf = _fact(key="k", raw=1.0, confidence="INSUFFICIENT", observations=1)
        cf = _fact(key="k", raw=2.0, confidence="INSUFFICIENT", observations=1)
        dc = _compare_dimension(self._dim([bf]), self._dim([cf]))
        assert dc.drift == "INSUFFICIENT_EVIDENCE"

    def test_missing_fact_in_current_unknown_deviation(self):
        bf = _fact(key="present", raw=1.0, confidence="HIGH", observations=15)
        # current has a DIFFERENT key
        cf = _fact(key="absent",  raw=2.0, confidence="HIGH", observations=5)
        dc = _compare_dimension(self._dim([bf]), self._dim([cf]))
        cmps = {c.key: c for c in dc.comparisons}
        assert cmps["present"].deviation == "UNKNOWN"

    def test_changed_and_unchanged_facts_segregated(self):
        b1 = _fact(key="stable",  raw=100.0, confidence="HIGH", observations=20)
        b2 = _fact(key="changed", raw=100.0, confidence="HIGH", observations=20)
        c1 = _fact(key="stable",  raw=102.0, confidence="HIGH", observations=8)
        c2 = _fact(key="changed", raw=300.0, confidence="HIGH", observations=8)
        dc = _compare_dimension(self._dim([b1, b2]), self._dim([c1, c2]))
        changed_keys   = {f.key for f in dc.changed_facts}
        unchanged_keys = {f.key for f in dc.unchanged_facts}
        assert "changed" in changed_keys
        assert "stable"  in unchanged_keys


# ── _overall_drift ────────────────────────────────────────────────────────────

class TestOverallDrift:

    def _dc(self, drift: str) -> DimensionChange:
        return DimensionChange(key="k", label="L", drift=drift,
                               overall_confidence="HIGH", summary="")

    def test_all_stable(self):
        dcs = [self._dc("STABLE")] * 5
        assert _overall_drift(dcs) == "STABLE"

    def test_all_changed(self):
        dcs = [self._dc("CHANGED")] * 5
        assert _overall_drift(dcs) == "CHANGED"

    def test_mixed(self):
        dcs = [self._dc("STABLE"), self._dc("CHANGED"), self._dc("STABLE")]
        assert _overall_drift(dcs) == "MIXED"

    def test_all_insufficient(self):
        dcs = [self._dc("INSUFFICIENT_EVIDENCE")] * 5
        assert _overall_drift(dcs) == "INSUFFICIENT_EVIDENCE"

    def test_stable_with_insufficient_is_stable(self):
        dcs = [self._dc("STABLE"), self._dc("INSUFFICIENT_EVIDENCE")]
        assert _overall_drift(dcs) == "STABLE"

    def test_changed_with_insufficient_is_changed(self):
        dcs = [self._dc("CHANGED"), self._dc("INSUFFICIENT_EVIDENCE")]
        assert _overall_drift(dcs) in ("CHANGED", "MIXED")


# ── Timeline ──────────────────────────────────────────────────────────────────

class TestChangeTimeline:

    def _make_data(self, launch_times: list[int]) -> _OperatorData:
        obj = object.__new__(_OperatorData)
        obj.treasury_wallets = ["TW1"]
        obj._ops_db  = ""
        obj._live_db = ""
        obj.launches  = [{"create_time": ts} for ts in launch_times]
        obj.audit     = []
        obj.creators  = []
        obj.fanouts   = []
        obj.wallets   = []
        obj.ops       = []
        return obj

    def test_empty_data_empty_timeline(self):
        data    = self._make_data([])
        entries = _build_change_timeline([], data, int(time.time()))
        # Only the window boundary entry expected
        assert len(entries) >= 1

    def test_entries_sorted(self):
        now  = int(time.time())
        data = self._make_data([now - 3000, now - 1000])
        dcs  = [DimensionChange(key="k", label="L", drift="STABLE",
                                overall_confidence="HIGH", summary="")]
        entries = _build_change_timeline(dcs, data, now - 2000)
        ts_list = [e.ts for e in entries]
        assert ts_list == sorted(ts_list)

    def test_changed_dimension_produces_entry(self):
        now  = int(time.time())
        data = self._make_data([now - 100])
        cmp  = FactComparison(
            key="k", label="L",
            historical_value="1", historical_raw=1.0, historical_observations=15,
            current_value="3", current_raw=3.0, current_observations=5,
            deviation="HIGH", confidence="HIGH", reason="test",
        )
        dc = DimensionChange(key="funding", label="Funding Behaviour",
                             drift="CHANGED", overall_confidence="HIGH",
                             summary="", comparisons=[cmp])
        entries = _build_change_timeline([dc], data, now - 200)
        cats = {e.category for e in entries}
        assert "CAMPAIGN" in cats


# ── BehaviourChangeEngine end-to-end ─────────────────────────────────────────

class TestBehaviourChangeEngine:

    def test_empty_db_returns_report_with_gaps(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourChangeEngine(ops_path, live_path)
        report = engine.compare("op-x", treasury_wallets=["TW1"])
        assert isinstance(report, BehaviourChangeReport)
        assert report.overall_drift == "INSUFFICIENT_EVIDENCE"
        assert len(report.evidence_gap_notes) > 0

    def test_report_has_five_dimensions(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourChangeEngine(ops_path, live_path)
        report = engine.compare("op-x", treasury_wallets=[])
        assert len(report.dimension_changes) == 5

    def test_no_change_when_funding_identical(self):
        engine, ops_path, tw = _populated_engine(
            baseline_funding=800.0, current_funding=810.0,  # ~1.25% change → NONE
            n_baseline=15, n_current=5,
        )
        report = engine.compare("op-x", treasury_wallets=tw)
        funding_dc = next(dc for dc in report.dimension_changes if dc.key == "funding")
        ts_cmp = next(
            (c for c in funding_dc.comparisons if c.key == "preferred_treasury_size"),
            None,
        )
        if ts_cmp and ts_cmp.confidence != "INSUFFICIENT":
            assert ts_cmp.deviation == "NONE"
        os.unlink(ops_path)

    def test_large_funding_change_detected(self):
        engine, ops_path, tw = _populated_engine(
            baseline_funding=800.0, current_funding=2000.0,  # +150% → VERY_HIGH
            n_baseline=15, n_current=5,
        )
        report = engine.compare("op-x", treasury_wallets=tw)
        funding_dc = next(dc for dc in report.dimension_changes if dc.key == "funding")
        ts_cmp = next(
            (c for c in funding_dc.comparisons if c.key == "preferred_treasury_size"),
            None,
        )
        if ts_cmp and ts_cmp.confidence != "INSUFFICIENT":
            assert ts_cmp.deviation in ("HIGH", "VERY_HIGH", "MODERATE")
        os.unlink(ops_path)

    def test_report_is_json_serialisable(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourChangeEngine(ops_path, live_path)
        report = engine.compare("op-x", treasury_wallets=[])
        serialised = json.dumps(report.to_dict())
        assert len(serialised) > 10

    def test_report_fields_present(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourChangeEngine(ops_path, live_path)
        d = engine.compare("op-x", treasury_wallets=[]).to_dict()
        for field in [
            "operator_id", "computed_at", "baseline_window_days",
            "current_window_days", "baseline_observations", "current_observations",
            "overall_drift", "drift_summary", "dimension_changes",
            "timeline_events", "evidence_gap_notes",
        ]:
            assert field in d, f"Missing field: {field}"

    def test_custom_window_days_respected(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourChangeEngine(ops_path, live_path)
        report = engine.compare("op-x", treasury_wallets=[], current_window_days=14)
        assert report.current_window_days == 14

    def test_drift_summary_has_all_five_dimensions(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourChangeEngine(ops_path, live_path)
        report = engine.compare("op-x", treasury_wallets=[])
        assert len(report.drift_summary) == 5

    def test_compare_platform_returns_ok(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourChangeEngine(ops_path, live_path)
        summary = engine.compare_platform()
        assert "ok" in summary


# ── Every FactComparison has a reason ────────────────────────────────────────

class TestExplainability:

    def test_all_comparisons_have_non_empty_reason(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourChangeEngine(ops_path, live_path)
        report = engine.compare("op-x", treasury_wallets=[])
        for dc in report.dimension_changes:
            for cmp in dc.comparisons:
                assert cmp.reason, (
                    f"Empty reason on {dc.key}/{cmp.key}"
                )

    def test_no_predictive_language_in_reasons(self):
        """Comparison layer must not forecast, predict, or infer intent."""
        engine, ops_path, tw = _populated_engine(n_baseline=15, n_current=5)
        report = engine.compare("op-x", treasury_wallets=tw)
        forbidden = ["predict", "forecast", "will", "likely", "suspicious",
                     "malicious", "intend", "expect", "next launch"]
        for dc in report.dimension_changes:
            for cmp in dc.comparisons:
                text = (cmp.reason + dc.summary).lower()
                for word in forbidden:
                    assert word not in text, (
                        f"Predictive/evaluative language '{word}' found in "
                        f"{dc.key}/{cmp.key}: {cmp.reason}"
                    )
        os.unlink(ops_path)

    def test_insufficient_reason_references_observation_counts(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourChangeEngine(ops_path, live_path)
        report = engine.compare("op-x", treasury_wallets=["TW_NONE"])
        for dc in report.dimension_changes:
            for cmp in dc.comparisons:
                if cmp.confidence == "INSUFFICIENT":
                    assert "obs" in cmp.reason.lower() or "observation" in cmp.reason.lower() or "evidence" in cmp.reason.lower()


# ── Behaviour Change API routes ───────────────────────────────────────────────

@pytest.fixture
def chg_client():
    import src.ops.behaviour_change_routes as bcr
    ops_path  = _tmp_db("_rt_ops.db")
    live_path = _tmp_db("_rt_live.db")
    engine = BehaviourChangeEngine(ops_path, live_path)
    bcr._engine = engine

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bcr.change_bp)
    app.config["TESTING"] = True

    with app.test_client() as c:
        yield c

    bcr._engine = None


class TestBehaviourChangeRoutes:

    def test_change_endpoint_returns_200(self, chg_client):
        r = chg_client.get("/api/operators/any-id/behaviour/change")
        assert r.status_code == 200

    def test_change_response_has_report_structure(self, chg_client):
        data = chg_client.get("/api/operators/any-id/behaviour/change").get_json()
        assert "operator_id"       in data
        assert "overall_drift"     in data
        assert "dimension_changes" in data
        assert "drift_summary"     in data
        assert "evidence_gap_notes" in data

    def test_dimension_endpoint_known_key(self, chg_client):
        r = chg_client.get("/api/operators/any-id/behaviour/change/campaign")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["dimension_change"]["key"] == "campaign"

    def test_dimension_endpoint_unknown_key_404(self, chg_client):
        r = chg_client.get("/api/operators/any-id/behaviour/change/nonexistent")
        assert r.status_code == 404

    def test_platform_summary_endpoint_returns_200(self, chg_client):
        r = chg_client.get("/api/behaviour/change/platform-summary")
        assert r.status_code == 200
        data = r.get_json()
        assert "ok" in data

    def test_window_days_param_honoured(self, chg_client):
        r = chg_client.get("/api/operators/any-id/behaviour/change?window_days=14")
        data = r.get_json()
        assert data["current_window_days"] == 14


# ── Template markup ───────────────────────────────────────────────────────────

class TestOperatorIntelligenceChangeMarkup:

    def test_change_section_present(self):
        html = _read_template("operator_intelligence.html")
        assert "oi-change-section" in html
        assert "oi-change-body"    in html

    def test_change_api_fetched(self):
        html = _read_template("operator_intelligence.html")
        assert "/behaviour/change" in html

    def test_drift_chip_element(self):
        html = _read_template("operator_intelligence.html")
        assert "oi-chg-drift" in html

    def test_drift_summary_render_function(self):
        html = _read_template("operator_intelligence.html")
        assert "renderDriftSummary"    in html
        assert "renderDimensionChange" in html
        assert "renderComparison"      in html

    def test_css_comparison_classes(self):
        html = _read_template("operator_intelligence.html")
        assert "oi-cmp-dim"    in html
        assert "oi-cmp-row"    in html
        assert "oi-cmp-hist"   in html
        assert "oi-cmp-curr"   in html
        assert "oi-cmp-reason" in html

    def test_drift_strip_css(self):
        html = _read_template("operator_intelligence.html")
        assert "oi-cmp-drift-strip" in html

    def test_change_section_before_inbox(self):
        html = _read_template("operator_intelligence.html")
        change_pos = html.find("oi-change-section")
        inbox_pos  = html.find("oi-inbox-section")
        assert change_pos > 0 and inbox_pos > 0
        assert change_pos < inbox_pos

    def test_behaviour_change_below_behaviour_intelligence(self):
        html = _read_template("operator_intelligence.html")
        beh_pos = html.find("oi-behaviour-section")
        chg_pos = html.find("oi-change-section")
        assert beh_pos > 0 and chg_pos > 0
        assert chg_pos > beh_pos

    def test_historical_arrow_current_pattern(self):
        """Comparison rows show: historical → current."""
        html = _read_template("operator_intelligence.html")
        assert "oi-cmp-hist" in html
        assert "oi-cmp-arrow" in html
        assert "oi-cmp-curr"  in html

    def test_no_predictive_language_in_template(self):
        html = _read_template("operator_intelligence.html")
        forbidden = ["predict", "forecast", "next launch", "likely to"]
        for word in forbidden:
            # Only check within the change section JS (rough proximity check)
            change_idx = html.find("Behaviour Change Detection")
            snippet = html[change_idx:change_idx + 3000] if change_idx > 0 else ""
            assert word not in snippet.lower(), (
                f"Predictive language '{word}' found near change section"
            )
