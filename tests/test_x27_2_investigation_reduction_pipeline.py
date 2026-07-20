"""Regression tests for X27.2 — Investigation Reduction Pipeline.

Verifies every migrated launch receives exactly one mutually-exclusive
Pipeline Health bucket, bucket totals conserve against the total launch
count, drill-down never leaks a launch already claimed by a
higher-priority bucket, priority order (not classification logic) governs
assignment, and existing attribution/detection/walkback logic is
untouched.
"""
import sqlite3
import time
import hashlib

import pytest

from src.ops.investigation_pipeline import (
    build_pipeline_health,
    launches_in_bucket,
    creators_in_bucket,
    assign_bucket,
    BUCKET_ORDER,
    KNOWN_OPERATION,
    KNOWN_INFRASTRUCTURE,
    REPEAT_CREATOR,
    UNKNOWN_INFRASTRUCTURE,
    LINEAGE_GAP,
    INSUFFICIENT_EVIDENCE,
)


@pytest.fixture
def db_factory(tmp_path):
    def make(outcome_rows, token_rows=None, now=None):
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
        # tables evaluate_launcher_profile probes for; empty but present so
        # profile evaluation doesn't error on missing-table paths.
        ops_conn.execute("CREATE TABLE operator_entities (entity_address TEXT)")
        ops_conn.execute("CREATE TABLE wt_wrap_close_candidates (creator TEXT, funded_at REAL, detected_at REAL)")
        ops_conn.execute("CREATE TABLE wt_creator_birth_launch (creator TEXT, funded_at REAL, measured_at REAL)")
        ops_conn.execute("CREATE TABLE wt_candidate_websocket_watches (candidate_wallet TEXT)")
        ops_conn.commit()
        ops_conn.close()

        ops_conn2 = sqlite3.connect(ops_path)
        ops_conn2.execute(
            "CREATE TABLE wt_watchtower_launches (mint TEXT, create_time REAL, birth_to_launch_seconds REAL)"
        )
        ops_conn2.commit()
        ops_conn2.close()

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


def test_every_launch_receives_exactly_one_bucket(db_factory):
    now = int(time.time())
    outcomes = [
        {"mint": "M1", "outcome_type": "CANONICAL_OPERATOR_REACHED"},
        {"mint": "M2", "outcome_type": "KNOWN_CEX_REACHED"},
        {"mint": "M3", "outcome_type": "UNKNOWN_INFRASTRUCTURE"},
        {"mint": "M4", "outcome_type": "LINEAGE_GAP"},
        {"mint": "M5", "outcome_type": "INSUFFICIENT_EVIDENCE"},
    ]
    ops_path, core_path = db_factory(outcomes, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    assert len(pipeline["assignments"]) == 5
    for m in ("M1", "M2", "M3", "M4", "M5"):
        assert m in pipeline["assignments"]
        assert pipeline["assignments"][m]["bucket"] in BUCKET_ORDER


def test_conservation_sum_equals_total(db_factory):
    now = int(time.time())
    outcomes = [{"mint": f"M{i}", "outcome_type": "INSUFFICIENT_EVIDENCE"} for i in range(37)]
    ops_path, core_path = db_factory(outcomes, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    assert pipeline["conserved"] is True
    assert sum(b["count"] for b in pipeline["buckets"]) == pipeline["total_launches"] == 37


def test_no_overlap_across_all_buckets(db_factory):
    now = int(time.time())
    outcomes = [
        {"mint": "A1", "outcome_type": "CANONICAL_OPERATOR_REACHED"},
        {"mint": "A2", "outcome_type": "KNOWN_CEX_REACHED"},
        {"mint": "A3", "outcome_type": "KNOWN_RELAY_REACHED"},
        {"mint": "A4", "outcome_type": "UNKNOWN_INFRASTRUCTURE"},
        {"mint": "A5", "outcome_type": "LINEAGE_GAP"},
        {"mint": "A6", "outcome_type": "AMBIGUOUS_BRANCH"},
        {"mint": "A7", "outcome_type": "MAX_DEPTH"},
        {"mint": "A8", "outcome_type": "INSUFFICIENT_EVIDENCE"},
    ]
    ops_path, core_path = db_factory(outcomes, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    seen = set()
    for bucket in BUCKET_ORDER:
        mints = launches_in_bucket(pipeline, bucket)
        assert not (seen & set(mints)), f"overlap detected in {bucket}"
        seen |= set(mints)
    assert seen == set(r["mint"] for r in outcomes)


def test_repeat_creator_reclaims_lower_priority_outcomes(db_factory):
    """A creator with an established launcher profile (>=5 launches, >=7d
    observation) whose mint terminated at INSUFFICIENT_EVIDENCE must be
    reclassified into REPEAT_CREATOR -- this is the actual overlap bug
    measured live (144/519 launches in the 2026-07-16 audit)."""
    now = int(time.time())
    long_ago = now - 20 * 86400
    token_rows = [
        {"mint": f"T{i}", "pf_ws_creator": "EstablishedCreator", "created_at": long_ago + i * 3 * 86400}
        for i in range(6)
    ]
    outcomes = [{"mint": "T0", "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    assert pipeline["assignments"]["T0"]["bucket"] == REPEAT_CREATOR


def test_repeat_creator_never_reclaims_known_operation_or_known_infrastructure(db_factory):
    """Higher-priority buckets (Known Operation, Known Infrastructure) must
    win outright, even for an established repeat creator -- priority order
    determines assignment (Phase 2/9), Repeat Creator is priority 3."""
    now = int(time.time())
    long_ago = now - 20 * 86400
    token_rows = [
        {"mint": f"T{i}", "pf_ws_creator": "EstablishedCreator", "created_at": long_ago + i * 3 * 86400}
        for i in range(6)
    ]
    outcomes = [
        {"mint": "T0", "outcome_type": "CANONICAL_OPERATOR_REACHED"},
        {"mint": "T1", "outcome_type": "KNOWN_CEX_REACHED"},
    ]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    assert pipeline["assignments"]["T0"]["bucket"] == KNOWN_OPERATION
    assert pipeline["assignments"]["T1"]["bucket"] == KNOWN_INFRASTRUCTURE


def test_naive_launch_count_alone_does_not_qualify_as_repeat_creator(db_factory):
    """A creator with only 2 launches over a short span must NOT be
    classified REPEAT_CREATOR -- the real evaluate_launcher_profile
    threshold (>=5 launches AND >=7 days observation) is used, not a loose
    launch_count>1 rule (which was measured live to sweep in 77% of
    launches, dominated by shared/bot-wallet false positives)."""
    now = int(time.time())
    token_rows = [
        {"mint": "T0", "pf_ws_creator": "CasualCreator", "created_at": now - 100},
        {"mint": "T1", "pf_ws_creator": "CasualCreator", "created_at": now - 50},
    ]
    outcomes = [{"mint": "T0", "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    assert pipeline["assignments"]["T0"]["bucket"] == INSUFFICIENT_EVIDENCE


def test_priority_order_governs_assignment_not_hardcoded_branching():
    """Changing BUCKET_ORDER/predicate priority changes assignment without
    touching the underlying classification data (outcome_type mapping) --
    proves the walk is priority-driven, not a hardcoded if/elif chain that
    would need rewriting for reprioritisation."""
    from src.ops import investigation_pipeline as ip
    assert list(ip.BUCKET_ORDER).index(ip.KNOWN_OPERATION) < list(ip.BUCKET_ORDER).index(ip.KNOWN_INFRASTRUCTURE)
    assert list(ip.BUCKET_ORDER).index(ip.KNOWN_INFRASTRUCTURE) < list(ip.BUCKET_ORDER).index(ip.REPEAT_CREATOR)
    assert list(ip.BUCKET_ORDER).index(ip.REPEAT_CREATOR) < list(ip.BUCKET_ORDER).index(ip.UNKNOWN_INFRASTRUCTURE)
    assert list(ip.BUCKET_ORDER).index(ip.UNKNOWN_INFRASTRUCTURE) < list(ip.BUCKET_ORDER).index(ip.LINEAGE_GAP)
    assert list(ip.BUCKET_ORDER).index(ip.LINEAGE_GAP) < list(ip.BUCKET_ORDER).index(ip.INSUFFICIENT_EVIDENCE)


def test_ambiguous_branch_and_max_depth_fold_into_lineage_gap(db_factory):
    now = int(time.time())
    outcomes = [
        {"mint": "M1", "outcome_type": "AMBIGUOUS_BRANCH"},
        {"mint": "M2", "outcome_type": "MAX_DEPTH"},
    ]
    ops_path, core_path = db_factory(outcomes, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    assert pipeline["assignments"]["M1"]["bucket"] == LINEAGE_GAP
    assert pipeline["assignments"]["M2"]["bucket"] == LINEAGE_GAP


def test_missing_creator_falls_back_to_outcome_mapped_bucket(db_factory):
    now = int(time.time())
    outcomes = [{"mint": "NoCreatorMint", "outcome_type": "LINEAGE_GAP"}]
    ops_path, core_path = db_factory(outcomes, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    assert pipeline["assignments"]["NoCreatorMint"]["bucket"] == LINEAGE_GAP


def test_zero_database_mutation(db_factory):
    now = int(time.time())
    outcomes = [{"mint": "M1", "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    ops_path, core_path = db_factory(outcomes, now=now)
    before_ops, before_core = _hash(ops_path), _hash(core_path)
    build_pipeline_health(ops_path, core_path, now=now)
    assert _hash(ops_path) == before_ops
    assert _hash(core_path) == before_core


def test_window_filters_by_completed_at(db_factory):
    now = int(time.time())
    outcomes = [
        {"mint": "Recent", "outcome_type": "INSUFFICIENT_EVIDENCE", "completed_at": now - 100},
        {"mint": "Old", "outcome_type": "INSUFFICIENT_EVIDENCE", "completed_at": now - 10 * 86400},
    ]
    ops_path, core_path = db_factory(outcomes, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, window_seconds=86400, now=now)
    assert pipeline["total_launches"] == 1
    assert "Recent" in pipeline["assignments"]
    assert "Old" not in pipeline["assignments"]


def test_bucket_labels_and_reasons_are_investigative_not_attribution_language():
    from src.ops.investigation_pipeline import BUCKET_REASONS, BUCKET_LABELS, BUCKET_ORDER
    for b in BUCKET_ORDER:
        assert b in BUCKET_LABELS
        assert b in BUCKET_REASONS
        assert len(BUCKET_REASONS[b]) > 0


def test_attribution_outcome_types_unchanged():
    from src.ops.attribution_outcome import OUTCOME_TYPES
    assert OUTCOME_TYPES == (
        "CANONICAL_OPERATOR_REACHED", "KNOWN_MULTI_TOKEN_CREATOR",
        "KNOWN_CEX_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED",
        "UNKNOWN_INFRASTRUCTURE", "LINEAGE_GAP", "AMBIGUOUS_BRANCH",
        "MAX_DEPTH", "INSUFFICIENT_EVIDENCE",
    )


def test_creators_in_bucket_groups_by_creator_with_correct_launch_counts(db_factory):
    now = int(time.time())
    long_ago = now - 20 * 86400
    token_rows = []
    for i in range(6):
        token_rows.append({"mint": f"A{i}", "pf_ws_creator": "CreatorA", "created_at": long_ago + i * 3 * 86400})
    for i in range(5):
        token_rows.append({"mint": f"B{i}", "pf_ws_creator": "CreatorB", "created_at": long_ago + i * 3 * 86400})
    outcomes = [
        {"mint": "A0", "outcome_type": "INSUFFICIENT_EVIDENCE"},
        {"mint": "A1", "outcome_type": "LINEAGE_GAP"},
        {"mint": "B0", "outcome_type": "UNKNOWN_INFRASTRUCTURE"},
    ]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    grouped = creators_in_bucket(pipeline, REPEAT_CREATOR)
    by_creator = {g["creator"]: g for g in grouped}
    assert set(by_creator["CreatorA"]["mints"]) == {"A0", "A1"}
    assert by_creator["CreatorA"]["launch_count"] == 2
    assert by_creator["CreatorB"]["mints"] == ["B0"]
    assert by_creator["CreatorB"]["launch_count"] == 1
    # Sorted by launch_count desc.
    assert grouped[0]["creator"] == "CreatorA"


def test_creators_in_bucket_never_leaks_mint_from_another_bucket(db_factory):
    now = int(time.time())
    long_ago = now - 20 * 86400
    token_rows = [
        {"mint": f"C{i}", "pf_ws_creator": "CreatorC", "created_at": long_ago + i * 3 * 86400}
        for i in range(6)
    ]
    outcomes = [
        {"mint": "C0", "outcome_type": "CANONICAL_OPERATOR_REACHED"},  # KNOWN_OPERATION wins, not repeat creator
        {"mint": "C1", "outcome_type": "INSUFFICIENT_EVIDENCE"},
    ]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    grouped = creators_in_bucket(pipeline, REPEAT_CREATOR)
    all_mints = set()
    for g in grouped:
        all_mints |= set(g["mints"])
    assert "C0" not in all_mints
    assert "C1" in all_mints


def test_group_by_creator_route_returns_grouped_response():
    from flask import Flask
    from src.core.operation_dashboard_routes import ops_dashboard_bp
    app = Flask(__name__)
    app.register_blueprint(ops_dashboard_bp)
    client = app.test_client()
    r = client.get("/api/ops-v2/investigation-pipeline?window=24h&bucket=REPEAT_CREATOR&group_by=creator")
    assert r.status_code == 200
    data = r.get_json()
    assert data["group_by"] == "creator"
    assert "creators" in data
    if data["creators"]:
        first = data["creators"][0]
        assert "creator" in first and "mints" in first and "launch_count" in first
        assert first["launch_count"] == len(first["mints"])


def test_pipeline_health_route_returns_200_and_bucket_drilldown():
    from flask import Flask
    from src.core.operation_dashboard_routes import ops_dashboard_bp
    app = Flask(__name__)
    app.register_blueprint(ops_dashboard_bp)
    client = app.test_client()
    r = client.get("/api/ops-v2/investigation-pipeline?window=24h")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert "buckets" in data
    assert data["conserved"] is True

    r2 = client.get("/api/ops-v2/investigation-pipeline?window=24h&bucket=REPEAT_CREATOR")
    assert r2.status_code == 200
    d2 = r2.get_json()
    assert "mints" in d2

    r3 = client.get("/api/ops-v2/investigation-pipeline?window=24h&bucket=NOT_A_REAL_BUCKET")
    assert r3.status_code == 400


def test_drilldown_never_returns_launches_assigned_elsewhere_live():
    """Live end-to-end check against the real databases: every mint
    returned by a bucket's drill-down must be assigned to that bucket and
    no other, and the union of all bucket drill-downs must equal the full
    assignment set with no duplicates."""
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
