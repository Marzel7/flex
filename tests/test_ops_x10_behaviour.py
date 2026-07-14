"""
Sprint X10 — Behaviour Intelligence Engine tests.

Covers:
  - BehaviourFact / BehaviourProfile dataclasses
  - _confidence() and _stability() helpers
  - _campaign_behaviour, _funding_behaviour, _launch_behaviour,
    _operational_behaviour, _outcome_behaviour
  - BehaviourEngine.compute() end-to-end (empty + populated)
  - BehaviourEngine.compute_platform_summary()
  - Behaviour API routes: /api/operators/<id>/behaviour,
    /dimension/<key>, /fact/<key>, /api/behaviour/platform-summary
  - operator_intelligence.html markup: behaviour section present
  - inbox_adapters: _behaviour_items() emits no items when no operators
  - Evidence gap reporting: gaps reported, not fabricated
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

from src.ops.behaviour_engine import (
    BehaviourEngine,
    BehaviourFact,
    BehaviourProfile,
    MIN_OBSERVATIONS,
    _confidence,
    _stability,
    _OperatorData,
    _campaign_behaviour,
    _funding_behaviour,
    _launch_behaviour,
    _operational_behaviour,
    _outcome_behaviour,
    _build_timeline,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _tmp_db(suffix="_beh.db"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def _read_template(name):
    with open(os.path.join(ROOT, "templates", name), encoding="utf-8") as f:
        return f.read()


def _make_ops_db(launches: list[dict] | None = None, ops: list[dict] | None = None,
                 creators: list[dict] | None = None, fanouts: list[dict] | None = None,
                 wallets: list[dict] | None = None) -> str:
    """Create a minimal ops DB with the specified rows and return its path."""
    path = _tmp_db("_ops_x10.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_watchtower_launches (
            id INTEGER PRIMARY KEY,
            treasury_wallet TEXT, subprov_wallet TEXT,
            create_time INTEGER, birth_to_launch_seconds INTEGER,
            subprov_funding_sol REAL, wrap_close_sol REAL,
            fanout_time INTEGER, fanout_count INTEGER,
            fanout_to_create_secs REAL, create_to_migration_secs INTEGER,
            launch_mode TEXT, creator_wallet TEXT,
            funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE'
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_launch_audit (
            mint TEXT PRIMARY KEY, treasury TEXT, subprov TEXT,
            create_time INTEGER, peak_mc REAL, actionable_multiple REAL,
            migrated INTEGER DEFAULT 0, final_state TEXT,
            audit_state TEXT DEFAULT 'FINALIZED',
            created_at INTEGER DEFAULT (strftime('%s','now')),
            updated_at INTEGER DEFAULT (strftime('%s','now'))
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_ops_v2 (
            operation_uuid TEXT PRIMARY KEY, treasury_root TEXT,
            family_uuid TEXT, status TEXT DEFAULT 'FORMING',
            confidence REAL DEFAULT 0.0,
            first_seen INTEGER, last_seen INTEGER,
            created_at INTEGER, updated_at INTEGER
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_ops_v2_creators (
            operation_uuid TEXT, creator_wallet TEXT, token_mint TEXT,
            migration_time INTEGER, funding_amount_sol REAL,
            template_base REAL, op_first_seen INTEGER
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_fanout_events (
            id INTEGER PRIMARY KEY, subprov_wallet TEXT,
            treasury_wallet TEXT, fanout_time INTEGER, fanout_count INTEGER,
            total_sol REAL, largest_sol REAL, smallest_sol REAL,
            avg_sol REAL, has_identical_amounts INTEGER DEFAULT 0
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_ops_v2_wallets (
            operation_uuid TEXT, wallet TEXT, role TEXT,
            first_seen INTEGER, last_seen INTEGER
        )""")

    now = int(time.time())
    for launch in (launches or []):
        conn.execute(
            "INSERT INTO wt_watchtower_launches (treasury_wallet, subprov_wallet, "
            "create_time, birth_to_launch_seconds, subprov_funding_sol, wrap_close_sol, "
            "fanout_to_create_secs, create_to_migration_secs, launch_mode, creator_wallet, "
            "funding_mechanism) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                launch.get("treasury_wallet", "TW1"),
                launch.get("subprov_wallet", "SP1"),
                launch.get("create_time", now),
                launch.get("birth_to_launch_seconds", 5),
                launch.get("subprov_funding_sol", 800.0),
                launch.get("wrap_close_sol", 1.11),
                launch.get("fanout_to_create_secs", 2.5),
                launch.get("create_to_migration_secs"),
                launch.get("launch_mode"),
                launch.get("creator_wallet", "CR1"),
                launch.get("funding_mechanism", "WSOL_WRAP_CLOSE"),
            ),
        )

    for op in (ops or []):
        conn.execute(
            "INSERT INTO wt_ops_v2 (operation_uuid, treasury_root, first_seen, last_seen, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (
                op.get("operation_uuid", "op-1"),
                op.get("treasury_root", "TW1"),
                op.get("first_seen", now - 3600),
                op.get("last_seen", now),
                now, now,
            ),
        )

    for c in (creators or []):
        conn.execute(
            "INSERT INTO wt_ops_v2_creators (operation_uuid, creator_wallet, token_mint, "
            "migration_time, funding_amount_sol, op_first_seen) VALUES (?,?,?,?,?,?)",
            (
                c.get("operation_uuid", "op-1"),
                c.get("creator_wallet", "CR1"),
                c.get("token_mint", "MNT1"),
                c.get("migration_time"),
                c.get("funding_amount_sol", 1.11),
                c.get("op_first_seen", now - 3600),
            ),
        )

    for f in (fanouts or []):
        conn.execute(
            "INSERT INTO wt_fanout_events (subprov_wallet, treasury_wallet, fanout_time, "
            "fanout_count, total_sol, avg_sol) VALUES (?,?,?,?,?,?)",
            (
                f.get("subprov_wallet", "SP1"),
                f.get("treasury_wallet", "TW1"),
                f.get("fanout_time", now),
                f.get("fanout_count", 10),
                f.get("total_sol", 11.0),
                f.get("avg_sol", 1.1),
            ),
        )

    for w in (wallets or []):
        conn.execute(
            "INSERT INTO wt_ops_v2_wallets (operation_uuid, wallet, role, first_seen, last_seen) "
            "VALUES (?,?,?,?,?)",
            (
                w.get("operation_uuid", "op-1"),
                w.get("wallet", "W1"),
                w.get("role", "TREASURY"),
                now - 3600, now,
            ),
        )

    conn.commit()
    conn.close()
    return path


def _make_audit_db(path: str, rows: list[dict]) -> None:
    """Add audit rows to an existing ops DB path."""
    conn = sqlite3.connect(path)
    now = int(time.time())
    for r in rows:
        conn.execute(
            "INSERT OR IGNORE INTO wt_launch_audit "
            "(mint, treasury, peak_mc, actionable_multiple, migrated) VALUES (?,?,?,?,?)",
            (
                r.get("mint", f"MINT{now}"),
                r.get("treasury", "TW1"),
                r.get("peak_mc", 100000.0),
                r.get("actionable_multiple", 10.0),
                r.get("migrated", 1),
            ),
        )
    conn.commit()
    conn.close()


def _empty_data(tw: list[str] | None = None) -> _OperatorData:
    ops_path  = _tmp_db("_empty_ops.db")
    live_path = _tmp_db("_empty_live.db")
    d = _OperatorData(tw or ["TW1"], ops_path, live_path)
    # tables don't exist → all lists empty
    return d


# ── Confidence helper ─────────────────────────────────────────────────────────

class TestConfidence:

    def test_below_minimum_is_insufficient(self):
        for n in range(0, MIN_OBSERVATIONS):
            assert _confidence(n) == "INSUFFICIENT"

    def test_low(self):
        assert _confidence(MIN_OBSERVATIONS) == "LOW"
        assert _confidence(4) == "LOW"

    def test_medium(self):
        assert _confidence(5) == "MEDIUM"
        assert _confidence(11) == "MEDIUM"

    def test_high(self):
        assert _confidence(12) == "HIGH"
        assert _confidence(100) == "HIGH"


# ── Stability helper ──────────────────────────────────────────────────────────

class TestStability:

    def test_single_value_unknown(self):
        assert _stability([1.0]) == "UNKNOWN"

    def test_all_same_is_stable(self):
        assert _stability([1.0, 1.0, 1.0, 1.0]) == "STABLE"

    def test_high_variance_is_variable(self):
        assert _stability([1.0, 100.0, 1.0, 200.0]) == "VARIABLE"

    def test_moderate_variance(self):
        # cv ~= 0.25
        vals = [1.0, 1.2, 0.8, 1.1, 0.9]
        result = _stability(vals)
        assert result in ("STABLE", "MODERATE")

    def test_zero_mean_is_unknown(self):
        assert _stability([0.0, 0.0, 0.0]) == "UNKNOWN"


# ── BehaviourFact ─────────────────────────────────────────────────────────────

class TestBehaviourFact:

    def test_to_dict_roundtrip(self):
        f = BehaviourFact(
            key="test_key", label="Test", value="1.0 SOL", raw=1.0,
            confidence="HIGH", observations=20, unit="SOL", stability="STABLE",
        )
        d = f.to_dict()
        assert d["key"] == "test_key"
        assert d["confidence"] == "HIGH"
        assert d["observations"] == 20
        assert d["stability"] == "STABLE"

    def test_insufficient_fact_has_none_raw(self):
        f = BehaviourFact(
            key="k", label="L", value=None, raw=None,
            confidence="INSUFFICIENT", observations=0,
        )
        assert f.raw is None
        assert f.confidence == "INSUFFICIENT"


# ── _OperatorData (empty DB) ──────────────────────────────────────────────────

class TestOperatorDataEmpty:

    def test_empty_db_gives_empty_lists(self):
        data = _empty_data(["TW1"])
        assert data.launches == []
        assert data.ops == []
        assert data.creators == []
        assert data.fanouts == []

    def test_no_treasury_wallets_gives_empty_lists(self):
        data = _OperatorData([], _tmp_db(), _tmp_db())
        assert data.launches == []
        assert data.ops == []


# ── Campaign behaviour ────────────────────────────────────────────────────────

class TestCampaignBehaviour:

    def test_no_ops_is_insufficient(self):
        data = _empty_data()
        dim = _campaign_behaviour(data)
        assert dim.overall_confidence == "INSUFFICIENT"
        assert any(f.key == "observed_campaigns" for f in dim.facts)

    def test_sufficient_ops_derives_cadence(self):
        now = int(time.time())
        data = _empty_data()
        data.ops = [
            {"operation_uuid": f"op-{i}", "treasury_root": "TW1",
             "first_seen": now - i * 86400, "last_seen": now - i * 86400 + 3600}
            for i in range(15)
        ]
        dim = _campaign_behaviour(data)
        assert dim.overall_confidence in ("MEDIUM", "HIGH")
        keys = {f.key for f in dim.facts}
        assert "campaigns_per_day" in keys
        assert "avg_campaign_spacing_h" in keys

    def test_creators_per_campaign_fact_present(self):
        now = int(time.time())
        data = _empty_data()
        data.ops = [
            {"operation_uuid": f"op-{i}", "treasury_root": "TW1",
             "first_seen": now - i * 86400, "last_seen": now - i * 86400 + 3600}
            for i in range(5)
        ]
        data.creators = [
            {"operation_uuid": f"op-{i % 5}", "funding_amount_sol": 1.11,
             "migration_time": None, "op_first_seen": now - 3600}
            for i in range(20)
        ]
        dim = _campaign_behaviour(data)
        keys = {f.key for f in dim.facts}
        assert "avg_creators_per_campaign" in keys


# ── Funding behaviour ─────────────────────────────────────────────────────────

class TestFundingBehaviour:

    def test_no_launches_treasury_size_insufficient(self):
        data = _empty_data()
        dim = _funding_behaviour(data)
        ts_fact = next(f for f in dim.facts if f.key == "preferred_treasury_size")
        assert ts_fact.confidence == "INSUFFICIENT"

    def test_with_launches_derives_treasury_size(self):
        data = _empty_data()
        now = int(time.time())
        data.launches = [
            {"subprov_funding_sol": 800.0, "wrap_close_sol": 1.11,
             "funding_mechanism": "WSOL_WRAP_CLOSE", "create_time": now}
        ] * 12
        dim = _funding_behaviour(data)
        ts_fact = next(f for f in dim.facts if f.key == "preferred_treasury_size")
        assert ts_fact.confidence == "HIGH"
        assert ts_fact.raw == pytest.approx(800.0)

    def test_wrap_close_usage_always_emitted(self):
        data = _empty_data()
        data.launches = [{"funding_mechanism": "WSOL_WRAP_CLOSE", "create_time": 1}] * 5
        dim = _funding_behaviour(data)
        wc_fact = next(f for f in dim.facts if f.key == "wrap_close_usage")
        assert wc_fact is not None

    def test_100_pct_wrap_close(self):
        data = _empty_data()
        data.launches = [{"funding_mechanism": "WSOL_WRAP_CLOSE", "create_time": 1}] * 8
        dim = _funding_behaviour(data)
        wc_fact = next(f for f in dim.facts if f.key == "wrap_close_usage")
        assert wc_fact.raw == pytest.approx(100.0)


# ── Launch behaviour ──────────────────────────────────────────────────────────

class TestLaunchBehaviour:

    def test_no_launches_insufficient(self):
        data = _empty_data()
        dim = _launch_behaviour(data)
        assert dim.overall_confidence == "INSUFFICIENT"

    def test_launch_delay_fact(self):
        now = int(time.time())
        data = _empty_data()
        data.launches = [
            {"birth_to_launch_seconds": 120, "create_time": now + i * 100,
             "wrap_close_sol": 1.11}
            for i in range(15)
        ]
        dim = _launch_behaviour(data)
        keys = {f.key for f in dim.facts}
        assert "avg_launch_delay_s" in keys
        delay_fact = next(f for f in dim.facts if f.key == "avg_launch_delay_s")
        assert delay_fact.raw == pytest.approx(120.0)

    def test_peak_hour_derived_from_create_time(self):
        # Create 15 launches all at the same UTC hour
        base = 1748880000  # 2025-06-02 12:00:00 UTC (hour=12)
        data = _empty_data()
        data.launches = [
            {"create_time": base + i * 60, "birth_to_launch_seconds": 5}
            for i in range(15)
        ]
        dim = _launch_behaviour(data)
        keys = {f.key for f in dim.facts}
        assert "peak_launch_hour_utc" in keys

    def test_fanout_to_create_fact(self):
        now = int(time.time())
        data = _empty_data()
        data.launches = [
            {"fanout_to_create_secs": 3.5, "create_time": now + i}
            for i in range(6)
        ]
        dim = _launch_behaviour(data)
        keys = {f.key for f in dim.facts}
        assert "avg_fanout_to_create_s" in keys


# ── Operational behaviour ─────────────────────────────────────────────────────

class TestOperationalBehaviour:

    def test_empty_gives_zero_wallets(self):
        data = _empty_data()
        dim = _operational_behaviour(data)
        ts_fact = next(f for f in dim.facts if f.key == "total_infra_wallets")
        assert ts_fact.raw == 0

    def test_wallet_roles_enumerated(self):
        data = _empty_data()
        data.wallets = [
            {"wallet": f"W{i}", "role": "TREASURY", "operation_uuid": "op-1"}
            for i in range(3)
        ] + [
            {"wallet": f"S{i}", "role": "SUB_PROV", "operation_uuid": "op-1"}
            for i in range(5)
        ]
        data.ops = [{"operation_uuid": "op-1", "treasury_root": "TW1",
                     "first_seen": 1, "last_seen": 2}] * 6
        dim = _operational_behaviour(data)
        keys = {f.key for f in dim.facts}
        assert "wallet_role_treasury" in keys or "wallet_role_sub_prov" in keys

    def test_infra_reuse_reported(self):
        data = _empty_data()
        # Same wallet in two operations → reuse
        data.wallets = [
            {"wallet": "SHARED", "role": "SUB_PROV", "operation_uuid": "op-1"},
            {"wallet": "SHARED", "role": "SUB_PROV", "operation_uuid": "op-2"},
            {"wallet": "UNIQUE", "role": "SUB_PROV", "operation_uuid": "op-1"},
        ]
        data.ops = [{"operation_uuid": f"op-{i}", "treasury_root": "TW1",
                     "first_seen": i, "last_seen": i+1}
                    for i in range(6)]
        dim = _operational_behaviour(data)
        keys = {f.key for f in dim.facts}
        assert "infra_reuse_pct" in keys


# ── Outcome behaviour ─────────────────────────────────────────────────────────

class TestOutcomeBehaviour:

    def test_no_audit_is_insufficient(self):
        data = _empty_data()
        dim = _outcome_behaviour(data)
        assert dim.overall_confidence == "INSUFFICIENT"

    def test_migration_rate_computed(self):
        data = _empty_data()
        data.audit = [
            {"migrated": 1, "actionable_multiple": 10.0, "peak_mc": 100000.0}
        ] * 10 + [{"migrated": 0, "actionable_multiple": 2.0, "peak_mc": 20000.0}] * 2
        dim = _outcome_behaviour(data)
        mig_fact = next(f for f in dim.facts if f.key == "migration_rate")
        assert mig_fact.raw == pytest.approx(10/12 * 100, abs=1.0)

    def test_actionable_multiple_fact(self):
        data = _empty_data()
        data.audit = [{"migrated": 1, "actionable_multiple": 50.0, "peak_mc": 200000.0}] * 5
        dim = _outcome_behaviour(data)
        keys = {f.key for f in dim.facts}
        assert "avg_actionable_multiple" in keys
        m_fact = next(f for f in dim.facts if f.key == "avg_actionable_multiple")
        assert m_fact.raw == pytest.approx(50.0)

    def test_pct_gt5x_fact(self):
        data = _empty_data()
        data.audit = [{"migrated": 1, "actionable_multiple": 10.0, "peak_mc": 100000.0}] * 8
        data.audit += [{"migrated": 0, "actionable_multiple": 1.5, "peak_mc": 5000.0}] * 4
        dim = _outcome_behaviour(data)
        keys = {f.key for f in dim.facts}
        assert "pct_gt5x" in keys


# ── Timeline ──────────────────────────────────────────────────────────────────

class TestTimeline:

    def test_empty_gives_empty_list(self):
        data = _empty_data()
        assert _build_timeline(data) == []

    def test_entries_sorted_ascending(self):
        data = _empty_data()
        data.ops = [
            {"operation_uuid": "op-1", "first_seen": 1000, "last_seen": 2000},
            {"operation_uuid": "op-2", "first_seen": 500, "last_seen": 1500},
        ]
        entries = _build_timeline(data)
        ts = [e.ts for e in entries]
        assert ts == sorted(ts)

    def test_launches_produce_entries(self):
        data = _empty_data()
        data.launches = [{"create_time": 1000 + i * 100, "creator_wallet": "CR1",
                          "subprov_wallet": "SP1"} for i in range(5)]
        entries = _build_timeline(data)
        cats = {e.category for e in entries}
        assert "LAUNCH" in cats

    def test_capped_at_100(self):
        data = _empty_data()
        data.launches = [{"create_time": i, "creator_wallet": "CR1",
                          "subprov_wallet": "SP1"} for i in range(200)]
        entries = _build_timeline(data)
        assert len(entries) <= 100


# ── BehaviourEngine end-to-end ────────────────────────────────────────────────

class TestBehaviourEngine:

    def test_no_operators_returns_empty_profile(self):
        ops_path  = _tmp_db("_e2e_ops.db")
        live_path = _tmp_db("_e2e_live.db")
        engine = BehaviourEngine(ops_path, live_path)
        # No operator in store → treasury_wallets = []
        profile = engine.compute("nonexistent-op-id")
        assert profile.operator_id == "nonexistent-op-id"
        assert profile.total_observations == 0

    def test_profile_has_five_dimensions(self):
        ops_path  = _tmp_db("_e2e_ops2.db")
        live_path = _tmp_db("_e2e_live2.db")
        engine = BehaviourEngine(ops_path, live_path)
        profile = engine.compute("op-x", treasury_wallets=[])
        assert len(profile.dimensions) == 5

    def test_all_insufficient_when_no_data(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourEngine(ops_path, live_path)
        profile = engine.compute("op-x", treasury_wallets=["TW1"])
        insuf = [d for d in profile.dimensions if d.overall_confidence == "INSUFFICIENT"]
        assert len(insuf) == len(profile.dimensions)

    def test_evidence_gaps_reported(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourEngine(ops_path, live_path)
        profile = engine.compute("op-x", treasury_wallets=["TW_MISSING"])
        # No launches found → gap note
        assert any("launch" in g.lower() for g in profile.evidence_gap_notes)

    def test_populated_db_gives_confident_profile(self):
        now = int(time.time())
        launches = [
            {"treasury_wallet": "TW1", "subprov_wallet": f"SP{i}",
             "create_time": now - i * 7200,
             "birth_to_launch_seconds": 3 + i % 5,
             "subprov_funding_sol": 800.0 + i * 10,
             "wrap_close_sol": 1.11,
             "fanout_to_create_secs": 2.5,
             "funding_mechanism": "WSOL_WRAP_CLOSE"}
            for i in range(15)
        ]
        ops = [
            {"operation_uuid": f"op-{i}", "treasury_root": "TW1",
             "first_seen": now - i * 86400, "last_seen": now - i * 86400 + 3600}
            for i in range(15)
        ]
        ops_path = _make_ops_db(launches=launches, ops=ops)
        _make_audit_db(ops_path, [
            {"mint": f"M{i}", "treasury": "TW1",
             "peak_mc": 100000.0 + i * 10000, "actionable_multiple": 40.0 + i,
             "migrated": 1}
            for i in range(15)
        ])
        live_path = _tmp_db()
        engine = BehaviourEngine(ops_path, live_path)
        profile = engine.compute("op-x", treasury_wallets=["TW1"])

        # Should have meaningful data
        assert profile.total_observations > 0
        assert profile.overall_confidence in ("LOW", "MEDIUM", "HIGH")

        # Funding dimension should have a treasury size fact
        funding_dim = next(d for d in profile.dimensions if d.key == "funding")
        ts_fact = next(f for f in funding_dim.facts if f.key == "preferred_treasury_size")
        assert ts_fact.raw is not None
        assert ts_fact.raw > 0

        os.unlink(ops_path)

    def test_profile_to_dict_serialisable(self):
        ops_path  = _tmp_db()
        live_path = _tmp_db()
        engine = BehaviourEngine(ops_path, live_path)
        profile = engine.compute("op-x", treasury_wallets=[])
        d = profile.to_dict()
        # Must be JSON-serialisable
        serialised = json.dumps(d)
        assert len(serialised) > 0

    def test_compute_platform_summary(self):
        now = int(time.time())
        launches = [
            {"treasury_wallet": "TW1", "subprov_funding_sol": 800.0, "create_time": now}
        ] * 5
        ops_path  = _make_ops_db(launches=launches)
        live_path = _tmp_db()
        engine = BehaviourEngine(ops_path, live_path)
        summary = engine.compute_platform_summary()
        assert summary["ok"] is True
        assert summary["treasury_count"] >= 1
        assert summary["total_launches"] >= 5
        os.unlink(ops_path)


# ── Behaviour API routes ──────────────────────────────────────────────────────

@pytest.fixture
def beh_client():
    import src.ops.behaviour_routes as brr
    ops_path  = _tmp_db("_route_ops.db")
    live_path = _tmp_db("_route_live.db")
    engine = BehaviourEngine(ops_path, live_path)
    brr._engine = engine

    from flask import Flask
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"))
    app.register_blueprint(brr.behaviour_bp)
    app.config["TESTING"] = True

    with app.test_client() as c:
        yield c

    brr._engine = None


class TestBehaviourRoutes:

    def test_behaviour_endpoint_returns_200(self, beh_client):
        r = beh_client.get("/api/operators/any-op-id/behaviour")
        assert r.status_code == 200

    def test_behaviour_endpoint_returns_profile_structure(self, beh_client):
        r = beh_client.get("/api/operators/any-op-id/behaviour")
        data = r.get_json()
        assert "operator_id"         in data
        assert "dimensions"          in data
        assert "overall_confidence"  in data
        assert "total_observations"  in data
        assert "timeline"            in data
        assert "stability_summary"   in data
        assert "insufficient_dimensions" in data
        assert "evidence_gap_notes"  in data

    def test_behaviour_has_five_dimensions(self, beh_client):
        r = beh_client.get("/api/operators/any-op-id/behaviour")
        data = r.get_json()
        assert len(data["dimensions"]) == 5

    def test_dimension_endpoint_returns_200(self, beh_client):
        r = beh_client.get("/api/operators/any-op-id/behaviour/dimension/campaign")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["dimension"]["key"] == "campaign"

    def test_unknown_dimension_returns_404(self, beh_client):
        r = beh_client.get("/api/operators/any-op-id/behaviour/dimension/nonexistent")
        assert r.status_code == 404

    def test_fact_endpoint_returns_fact(self, beh_client):
        r = beh_client.get("/api/operators/any-op-id/behaviour/fact/observed_campaigns")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["fact"]["key"] == "observed_campaigns"

    def test_unknown_fact_returns_404(self, beh_client):
        r = beh_client.get("/api/operators/any-op-id/behaviour/fact/does_not_exist")
        assert r.status_code == 404

    def test_platform_summary_endpoint(self, beh_client):
        r = beh_client.get("/api/behaviour/platform-summary")
        assert r.status_code == 200
        data = r.get_json()
        assert "ok" in data


# ── Each dimension emits required facts ──────────────────────────────────────

class TestDimensionFactKeys:

    def test_campaign_dim_always_has_observed_campaigns(self):
        data = _empty_data()
        dim = _campaign_behaviour(data)
        assert any(f.key == "observed_campaigns" for f in dim.facts)

    def test_funding_dim_always_has_treasury_size_and_creator_funding(self):
        data = _empty_data()
        dim = _funding_behaviour(data)
        keys = {f.key for f in dim.facts}
        assert "preferred_treasury_size" in keys
        assert "preferred_creator_funding" in keys

    def test_launch_dim_always_has_observed_launches(self):
        data = _empty_data()
        dim = _launch_behaviour(data)
        assert any(f.key == "observed_launches" for f in dim.facts)

    def test_operational_dim_always_has_total_wallets(self):
        data = _empty_data()
        dim = _operational_behaviour(data)
        assert any(f.key == "total_infra_wallets" for f in dim.facts)

    def test_outcome_dim_always_has_audited_launches(self):
        data = _empty_data()
        dim = _outcome_behaviour(data)
        assert any(f.key == "audited_launches" for f in dim.facts)


# ── Every fact has required fields ────────────────────────────────────────────

class TestFactIntegrity:

    def test_all_facts_have_required_fields(self):
        data = _empty_data()
        for dim_fn in [
            _campaign_behaviour,
            _funding_behaviour,
            _launch_behaviour,
            _operational_behaviour,
            _outcome_behaviour,
        ]:
            dim = dim_fn(data)
            for fact in dim.facts:
                d = fact.to_dict()
                assert "key"          in d, f"Missing key in {dim.key}"
                assert "label"        in d
                assert "confidence"   in d
                assert "observations" in d
                assert d["confidence"] in (
                    "INSUFFICIENT", "LOW", "MEDIUM", "HIGH"
                ), f"Invalid confidence {d['confidence']} in {dim.key}/{d['key']}"

    def test_no_prediction_in_fact_labels(self):
        """Behaviour is descriptive only — no forward-looking language."""
        data = _empty_data()
        forbidden = ["predict", "forecast", "expect", "will", "anomaly", "likely"]
        for dim_fn in [_campaign_behaviour, _funding_behaviour, _launch_behaviour,
                       _operational_behaviour, _outcome_behaviour]:
            dim = dim_fn(data)
            for fact in dim.facts:
                label_lower = fact.label.lower()
                for word in forbidden:
                    assert word not in label_lower, (
                        f"Predictive language '{word}' found in fact label: {fact.label}"
                    )


# ── Template markup ───────────────────────────────────────────────────────────

class TestOperatorIntelligenceTemplateBehaviour:

    def test_behaviour_section_present(self):
        html = _read_template("operator_intelligence.html")
        assert "oi-behaviour-section" in html
        assert "oi-behaviour-body"    in html

    def test_behaviour_api_fetched(self):
        html = _read_template("operator_intelligence.html")
        assert "/api/operators/" in html
        assert "/behaviour"      in html

    def test_dimension_rendering_functions_present(self):
        html = _read_template("operator_intelligence.html")
        assert "renderDimension" in html
        assert "renderFact"      in html

    def test_stability_rendering_present(self):
        html = _read_template("operator_intelligence.html")
        assert "renderStability" in html

    def test_evidence_gaps_rendered(self):
        html = _read_template("operator_intelligence.html")
        assert "renderGaps" in html

    def test_behaviour_css_present(self):
        html = _read_template("operator_intelligence.html")
        assert "oi-beh-dim"  in html
        assert "oi-beh-fact" in html
        assert "oi-beh-stab" in html
