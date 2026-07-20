"""Regression tests for X27.5 — Unify Behavioural Archetypes into the
Investigation Queue.

Verifies every migrated launch receives exactly one bucket across the
UNIFIED pipeline (attribution-derived buckets + the two behavioural
archetypes merged in from src/ops/behaviour_queue.py), behavioural
archetypes correctly consume launches from lower-priority buckets,
no overlap exists between any two buckets, and the standalone Behaviour
Queue API surface has been fully removed.
"""
import hashlib
import sqlite3
import time

import pytest

from src.ops.investigation_pipeline import (
    build_pipeline_health,
    launches_in_bucket,
    assign_bucket,
    BUCKET_ORDER,
    KNOWN_OPERATION,
    KNOWN_INFRASTRUCTURE,
    REPEAT_CREATOR,
    RAPID_BIRTH_LAUNCH,
    BURST_LAUNCH,
    UNKNOWN_INFRASTRUCTURE,
    LINEAGE_GAP,
    INSUFFICIENT_EVIDENCE,
)


@pytest.fixture
def db_factory(tmp_path):
    def make(outcome_rows, token_rows=None, wt_launch_rows=None, now=None):
        now = now or int(time.time())
        ops_path = str(tmp_path / f"ops_{time.time_ns()}.db")
        core_path = str(tmp_path / f"core_{time.time_ns()}.db")

        ops_conn = sqlite3.connect(ops_path)
        ops_conn.execute(
            "CREATE TABLE wt_attribution_outcomes ("
            "mint TEXT, outcome_type TEXT, completed_at REAL)"
        )
        for r in outcome_rows:
            ops_conn.execute(
                "INSERT INTO wt_attribution_outcomes (mint, outcome_type, completed_at) VALUES (?,?,?)",
                (r["mint"], r["outcome_type"], r.get("completed_at", now)),
            )
        ops_conn.execute("CREATE TABLE operator_entities (entity_address TEXT)")
        ops_conn.execute("CREATE TABLE wt_wrap_close_candidates (creator TEXT, funded_at REAL, detected_at REAL)")
        ops_conn.execute("CREATE TABLE wt_creator_birth_launch (creator TEXT, funded_at REAL, measured_at REAL)")
        ops_conn.execute("CREATE TABLE wt_candidate_websocket_watches (candidate_wallet TEXT)")
        ops_conn.execute(
            "CREATE TABLE wt_watchtower_launches (mint TEXT, create_time REAL, birth_to_launch_seconds REAL)"
        )
        for r in (wt_launch_rows or []):
            ops_conn.execute(
                "INSERT INTO wt_watchtower_launches (mint, create_time, birth_to_launch_seconds) VALUES (?,?,?)",
                (r["mint"], r.get("create_time"), r.get("birth_to_launch_seconds")),
            )
        ops_conn.commit()
        ops_conn.close()

        core_conn = sqlite3.connect(core_path)
        core_conn.execute(
            "CREATE TABLE token_analysis (mint TEXT, pf_ws_creator TEXT, earliest_tx_creator TEXT, "
            "created_at REAL, migrated_at REAL)"
        )
        for r in (token_rows or []):
            core_conn.execute(
                "INSERT INTO token_analysis (mint, pf_ws_creator, earliest_tx_creator, created_at, migrated_at) "
                "VALUES (?,?,?,?,?)",
                (r["mint"], r.get("pf_ws_creator"), r.get("earliest_tx_creator"), r.get("created_at"),
                 r.get("migrated_at", now)),
            )
        core_conn.commit()
        core_conn.close()
        return ops_path, core_path
    return make


def _hash(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_bucket_order_includes_behavioural_archetypes_at_correct_priority():
    assert list(BUCKET_ORDER).index(KNOWN_OPERATION) < list(BUCKET_ORDER).index(KNOWN_INFRASTRUCTURE)
    assert list(BUCKET_ORDER).index(KNOWN_INFRASTRUCTURE) < list(BUCKET_ORDER).index(REPEAT_CREATOR)
    assert list(BUCKET_ORDER).index(REPEAT_CREATOR) < list(BUCKET_ORDER).index(RAPID_BIRTH_LAUNCH)
    assert list(BUCKET_ORDER).index(RAPID_BIRTH_LAUNCH) < list(BUCKET_ORDER).index(BURST_LAUNCH)
    assert list(BUCKET_ORDER).index(BURST_LAUNCH) < list(BUCKET_ORDER).index(UNKNOWN_INFRASTRUCTURE)
    assert list(BUCKET_ORDER).index(UNKNOWN_INFRASTRUCTURE) < list(BUCKET_ORDER).index(LINEAGE_GAP)
    assert list(BUCKET_ORDER).index(LINEAGE_GAP) < list(BUCKET_ORDER).index(INSUFFICIENT_EVIDENCE)


def test_every_launch_receives_exactly_one_bucket_with_behaviour_merged(db_factory):
    now = int(time.time())
    long_ago = now - 20 * 86400
    token_rows = [{"mint": f"T{i}", "pf_ws_creator": "EstablishedCreator", "created_at": long_ago + i * 3 * 86400, "migrated_at": now} for i in range(6)]
    token_rows.append({"mint": "RapidMint", "migrated_at": now})
    token_rows.append({"mint": "BurstA", "migrated_at": now})
    token_rows.append({"mint": "BurstB", "migrated_at": now + 10})
    token_rows.append({"mint": "BurstC", "migrated_at": now + 20})
    token_rows.append({"mint": "Solo", "migrated_at": now - 100000})

    outcomes = [
        {"mint": "T0", "outcome_type": "CANONICAL_OPERATOR_REACHED"},
        {"mint": "T1", "outcome_type": "KNOWN_CEX_REACHED"},
        {"mint": "RapidMint", "outcome_type": "INSUFFICIENT_EVIDENCE"},
        {"mint": "BurstA", "outcome_type": "INSUFFICIENT_EVIDENCE"},
        {"mint": "BurstB", "outcome_type": "LINEAGE_GAP"},
        {"mint": "BurstC", "outcome_type": "UNKNOWN_INFRASTRUCTURE"},
        {"mint": "Solo", "outcome_type": "INSUFFICIENT_EVIDENCE"},
    ]
    wt_rows = [{"mint": "RapidMint", "create_time": now, "birth_to_launch_seconds": 2}]

    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, wt_launch_rows=wt_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now + 20000)

    assert pipeline["conserved"] is True
    assert sum(b["count"] for b in pipeline["buckets"]) == pipeline["total_launches"] == len(outcomes)
    for mint in ("T0", "T1", "RapidMint", "BurstA", "BurstB", "BurstC", "Solo"):
        assert mint in pipeline["assignments"]


def test_rapid_birth_launch_consumes_from_lower_priority_bucket(db_factory):
    now = int(time.time())
    outcomes = [{"mint": "RapidMint", "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    token_rows = [{"mint": "RapidMint", "migrated_at": now}]
    wt_rows = [{"mint": "RapidMint", "create_time": now, "birth_to_launch_seconds": 3}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, wt_launch_rows=wt_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    assert pipeline["assignments"]["RapidMint"]["bucket"] == RAPID_BIRTH_LAUNCH


def test_burst_launch_consumes_from_lower_priority_bucket(db_factory):
    now = int(time.time())
    outcomes = [
        {"mint": "B1", "outcome_type": "LINEAGE_GAP"},
        {"mint": "B2", "outcome_type": "UNKNOWN_INFRASTRUCTURE"},
        {"mint": "B3", "outcome_type": "INSUFFICIENT_EVIDENCE"},
    ]
    token_rows = [
        {"mint": "B1", "migrated_at": now},
        {"mint": "B2", "migrated_at": now + 10},
        {"mint": "B3", "migrated_at": now + 20},
    ]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now + 100)
    for mint in ("B1", "B2", "B3"):
        assert pipeline["assignments"][mint]["bucket"] == BURST_LAUNCH


def test_known_infrastructure_and_repeat_creator_still_win_over_behaviour(db_factory):
    """Priority order means Known Infrastructure/Repeat Creator must claim
    a launch even if it ALSO exhibits a behavioural fingerprint -- this is
    the exact double-counting scenario X27.5 fixes (measured live: Burst
    Launches previously appeared in all five investigation buckets)."""
    now = int(time.time())
    long_ago = now - 20 * 86400
    outcomes = [
        {"mint": "T0", "outcome_type": "KNOWN_CEX_REACHED"},
        {"mint": "T1", "outcome_type": "INSUFFICIENT_EVIDENCE"},  # repeat creator via established profile
    ]
    token_rows = [
        {"mint": "T0", "migrated_at": now},
        {"mint": "T1", "pf_ws_creator": "EstablishedCreator", "migrated_at": now + 5},
    ]
    # Give EstablishedCreator enough history to qualify (>=5 launches, >=7d span)
    for i in range(5):
        token_rows.append({"mint": f"Hist{i}", "pf_ws_creator": "EstablishedCreator", "created_at": long_ago + i * 3 * 86400, "migrated_at": now - 500000})
    wt_rows = [{"mint": "T0", "create_time": now, "birth_to_launch_seconds": 1},
               {"mint": "T1", "create_time": now, "birth_to_launch_seconds": 1}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, wt_launch_rows=wt_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now + 10)
    assert pipeline["assignments"]["T0"]["bucket"] == KNOWN_INFRASTRUCTURE
    assert pipeline["assignments"]["T1"]["bucket"] == REPEAT_CREATOR


def test_no_overlap_across_all_buckets_including_behavioural(db_factory):
    now = int(time.time())
    outcomes = [
        {"mint": "A1", "outcome_type": "CANONICAL_OPERATOR_REACHED"},
        {"mint": "A2", "outcome_type": "KNOWN_CEX_REACHED"},
        {"mint": "A3", "outcome_type": "INSUFFICIENT_EVIDENCE"},  # rapid birth
        {"mint": "A4", "outcome_type": "LINEAGE_GAP"},  # burst
        {"mint": "A5", "outcome_type": "UNKNOWN_INFRASTRUCTURE"},
        {"mint": "A6", "outcome_type": "LINEAGE_GAP"},
        {"mint": "A7", "outcome_type": "AMBIGUOUS_BRANCH"},
        {"mint": "A8", "outcome_type": "INSUFFICIENT_EVIDENCE"},
    ]
    token_rows = [
        {"mint": "A1", "migrated_at": now},
        {"mint": "A2", "migrated_at": now},
        {"mint": "A3", "migrated_at": now},
        {"mint": "A4", "migrated_at": now + 1000},
        {"mint": "A5", "migrated_at": now + 1005},
        {"mint": "A6", "migrated_at": now + 1010},
        {"mint": "A7", "migrated_at": now - 500000},
        {"mint": "A8", "migrated_at": now - 500010},
    ]
    wt_rows = [{"mint": "A3", "create_time": now, "birth_to_launch_seconds": 2}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, wt_launch_rows=wt_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now + 2000)
    seen = set()
    for bucket in BUCKET_ORDER:
        mints = launches_in_bucket(pipeline, bucket)
        assert not (seen & set(mints)), f"overlap detected in {bucket}"
        seen |= set(mints)
    assert seen == set(r["mint"] for r in outcomes)


def test_drilldown_returns_only_launches_assigned_to_that_bucket_for_behavioural(db_factory):
    now = int(time.time())
    outcomes = [
        {"mint": "RapidMint", "outcome_type": "INSUFFICIENT_EVIDENCE"},
        {"mint": "Other", "outcome_type": "INSUFFICIENT_EVIDENCE"},
    ]
    token_rows = [
        {"mint": "RapidMint", "migrated_at": now},
        {"mint": "Other", "migrated_at": now - 500000},
    ]
    wt_rows = [{"mint": "RapidMint", "create_time": now, "birth_to_launch_seconds": 1}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, wt_launch_rows=wt_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    rapid_mints = launches_in_bucket(pipeline, RAPID_BIRTH_LAUNCH)
    assert rapid_mints == ["RapidMint"]
    insufficient_mints = launches_in_bucket(pipeline, INSUFFICIENT_EVIDENCE)
    assert insufficient_mints == ["Other"]


def test_classification_is_deterministic(db_factory):
    now = int(time.time())
    outcomes = [{"mint": "M1", "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    token_rows = [{"mint": "M1", "migrated_at": now}]
    wt_rows = [{"mint": "M1", "create_time": now, "birth_to_launch_seconds": 2}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, wt_launch_rows=wt_rows, now=now)
    p1 = build_pipeline_health(ops_path, core_path, now=now)
    p2 = build_pipeline_health(ops_path, core_path, now=now)
    assert p1["assignments"]["M1"]["bucket"] == p2["assignments"]["M1"]["bucket"] == RAPID_BIRTH_LAUNCH


def test_zero_database_mutation(db_factory):
    now = int(time.time())
    outcomes = [{"mint": "M1", "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    token_rows = [{"mint": "M1", "migrated_at": now}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    before_ops, before_core = _hash(ops_path), _hash(core_path)
    build_pipeline_health(ops_path, core_path, now=now)
    assert _hash(ops_path) == before_ops
    assert _hash(core_path) == before_core


def test_attribution_outcome_types_unchanged():
    from src.ops.attribution_outcome import OUTCOME_TYPES
    assert OUTCOME_TYPES == (
        "CANONICAL_OPERATOR_REACHED", "KNOWN_MULTI_TOKEN_CREATOR",
        "KNOWN_CEX_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED",
        "UNKNOWN_INFRASTRUCTURE", "LINEAGE_GAP", "AMBIGUOUS_BRANCH",
        "MAX_DEPTH", "INSUFFICIENT_EVIDENCE",
    )


def test_behaviour_queue_module_unchanged_lookups_still_importable():
    """src/ops/behaviour_queue.py's lookups remain the single source of
    truth for behavioural evidence -- investigation_pipeline.py imports
    them rather than reimplementing detection logic."""
    from src.ops.behaviour_queue import rapid_birth_launch_lookup, burst_launch_lookup
    assert callable(rapid_birth_launch_lookup)
    assert callable(burst_launch_lookup)


def test_standalone_behaviour_queue_route_removed():
    from flask import Flask
    from src.core.operation_dashboard_routes import ops_dashboard_bp
    app = Flask(__name__)
    app.register_blueprint(ops_dashboard_bp)
    client = app.test_client()
    r = client.get("/api/ops-v2/behaviour-queue")
    assert r.status_code == 404


def test_investigation_pipeline_route_returns_200_with_behavioural_buckets():
    from flask import Flask
    from src.core.operation_dashboard_routes import ops_dashboard_bp
    app = Flask(__name__)
    app.register_blueprint(ops_dashboard_bp)
    client = app.test_client()
    r = client.get("/api/ops-v2/investigation-pipeline?window=24h")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["conserved"] is True
    bucket_ids = {b["bucket"] for b in data["buckets"]}
    assert RAPID_BIRTH_LAUNCH in bucket_ids
    assert BURST_LAUNCH in bucket_ids

    r2 = client.get("/api/ops-v2/investigation-pipeline?window=24h&bucket=BURST_LAUNCH")
    assert r2.status_code == 200
    assert "mints" in r2.get_json()


def test_html_no_longer_references_behaviour_queue():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "templates/discovery.html").read_text()
    assert "behaviourQueuePanel" not in html
    assert "filteredArchetype" not in html
    assert "/api/ops-v2/behaviour-queue" not in html
    assert "Rapid Birth" in html  # still surfaced via BUCKET_LABELS in the unified panel


def test_live_overlap_matrix_shows_zero_cross_bucket_leakage():
    """Live end-to-end check: every mint in RAPID_BIRTH_LAUNCH or
    BURST_LAUNCH must not simultaneously appear in any other bucket,
    against the real databases."""
    import os
    ops_db = os.path.join("database", "wt_ops_v2.db")
    core_db = os.path.join("database", "flex_complete_database.db")
    if not (os.path.exists(ops_db) and os.path.exists(core_db)):
        pytest.skip("live databases not available in this environment")
    pipeline = build_pipeline_health(ops_db, core_db)
    seen = set()
    for bucket in BUCKET_ORDER:
        mints = launches_in_bucket(pipeline, bucket)
        for m in mints:
            assert pipeline["assignments"][m]["bucket"] == bucket
        assert not (seen & set(mints))
        seen |= set(mints)
    assert seen == set(pipeline["assignments"].keys())
    assert pipeline["conserved"] is True
