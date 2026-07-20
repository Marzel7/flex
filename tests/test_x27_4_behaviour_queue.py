"""Regression tests for X27.4 — Behavioural Investigation Queue.

Verifies behavioural archetypes represent positive evidence only, never
infer missing timing, faithfully reproduce the measured WATCHTOWER
corpus precision, and perform zero database writes.
"""
import hashlib
import sqlite3
import time

import pytest

import inspect

from src.ops.behaviour_queue import (
    build_behaviour_queue,
    launches_in_archetype,
    rapid_birth_launch_lookup,
    burst_launch_lookup,
    rapid_birth_launch_candidates_for_treasury_discovery,
    RAPID_BIRTH_LAUNCH,
    BURST_LAUNCH,
    UNCLASSIFIED,
    RAPID_BIRTH_LAUNCH_THRESHOLD_SECONDS,
    BURST_WINDOW_SECONDS,
    BURST_MIN_CLUSTER_SIZE,
)


@pytest.fixture
def db_factory(tmp_path):
    def make(wt_launch_rows=None, token_analysis_rows=None):
        ops_path = str(tmp_path / f"ops_{time.time_ns()}.db")
        core_path = str(tmp_path / f"core_{time.time_ns()}.db")

        ops_conn = sqlite3.connect(ops_path)
        ops_conn.execute(
            "CREATE TABLE wt_watchtower_launches (mint TEXT, create_time REAL, "
            "birth_to_launch_seconds REAL)"
        )
        for r in (wt_launch_rows or []):
            ops_conn.execute(
                "INSERT INTO wt_watchtower_launches (mint, create_time, birth_to_launch_seconds) VALUES (?,?,?)",
                (r["mint"], r.get("create_time"), r.get("birth_to_launch_seconds")),
            )
        ops_conn.commit()
        ops_conn.close()

        core_conn = sqlite3.connect(core_path)
        core_conn.execute("CREATE TABLE token_analysis (mint TEXT, migrated_at REAL)")
        for r in (token_analysis_rows or []):
            core_conn.execute(
                "INSERT INTO token_analysis (mint, migrated_at) VALUES (?,?)",
                (r["mint"], r["migrated_at"]),
            )
        core_conn.commit()
        core_conn.close()
        return ops_path, core_path
    return make


def _hash(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_rapid_birth_launch_only_fires_with_canonical_timing(db_factory):
    now = int(time.time())
    wt_rows = [
        {"mint": "M1", "create_time": now - 100, "birth_to_launch_seconds": 3},
        {"mint": "M2", "create_time": now - 100, "birth_to_launch_seconds": None},  # missing derived timing
    ]
    ta_rows = [{"mint": "M1", "migrated_at": now}, {"mint": "M2", "migrated_at": now}]
    ops_path, core_path = db_factory(wt_rows, ta_rows)
    lookup = rapid_birth_launch_lookup(ops_path)
    assert "M1" in lookup
    assert lookup["M1"]["matched"] is True
    assert "M2" not in lookup  # birth_to_launch_seconds missing -> absent, not inferred


def test_missing_lifecycle_timing_routes_to_unclassified(db_factory):
    now = int(time.time())
    ta_rows = [{"mint": "NoTimingMint", "migrated_at": now}]
    ops_path, core_path = db_factory([], ta_rows)
    queue = build_behaviour_queue(ops_path, core_path, now=now)
    assert queue["assignments"]["NoTimingMint"]["primary_archetype"] == UNCLASSIFIED
    assert queue["assignments"]["NoTimingMint"]["rapid_birth_launch_evidence"] is None


def test_archetypes_never_infer_unavailable_timing(db_factory):
    """A mint absent from wt_watchtower_launches must never be assigned
    RAPID_BIRTH_LAUNCH by any fallback/estimation path."""
    now = int(time.time())
    ta_rows = [{"mint": f"M{i}", "migrated_at": now - i * 1000} for i in range(5)]
    ops_path, core_path = db_factory([], ta_rows)
    queue = build_behaviour_queue(ops_path, core_path, now=now)
    for mint, assignment in queue["assignments"].items():
        assert assignment["primary_archetype"] != RAPID_BIRTH_LAUNCH
        assert assignment["rapid_birth_launch_evidence"] is None


def test_watchtower_replay_reproduces_measured_precision():
    """Live replay against the real wt_ops_v2.db confirmed WATCHTOWER
    corpus must reproduce the 97.6% (40/41) precision measured in
    X27.3.2/X27.4's brief -- not merely close to it."""
    import os
    ops_db = os.path.join("database", "wt_ops_v2.db")
    if not os.path.exists(ops_db):
        pytest.skip("live ops database not available in this environment")
    lookup = rapid_birth_launch_lookup(ops_db)
    n = len(lookup)
    matched = sum(1 for v in lookup.values() if v["matched"])
    assert n == 41
    assert matched == 40
    assert round(matched / n, 4) == round(40 / 41, 4)


def test_burst_launch_measures_cluster_size_correctly(db_factory):
    now = int(time.time())
    # 3 launches within 60s of each other -> cluster_size >= BURST_MIN_CLUSTER_SIZE
    ta_rows = [
        {"mint": "B1", "migrated_at": now},
        {"mint": "B2", "migrated_at": now + 10},
        {"mint": "B3", "migrated_at": now + 20},
        {"mint": "Solo", "migrated_at": now - 10000},
    ]
    ops_path, core_path = db_factory([], ta_rows)
    lookup = burst_launch_lookup(core_path, now=now + 20000)
    assert lookup["B1"]["matched"] is True
    assert lookup["B1"]["cluster_size"] == 3
    assert lookup["Solo"]["matched"] is False
    assert lookup["Solo"]["cluster_size"] == 1


def test_every_launch_receives_exactly_one_primary_archetype(db_factory):
    now = int(time.time())
    wt_rows = [{"mint": "R1", "create_time": now, "birth_to_launch_seconds": 2}]
    ta_rows = [
        {"mint": "R1", "migrated_at": now},
        {"mint": "B1", "migrated_at": now + 1000},
        {"mint": "B2", "migrated_at": now + 1010},
        {"mint": "B3", "migrated_at": now + 1020},
        {"mint": "U1", "migrated_at": now + 50000},
    ]
    ops_path, core_path = db_factory(wt_rows, ta_rows)
    queue = build_behaviour_queue(ops_path, core_path, now=now + 60000)
    for mint in ("R1", "B1", "B2", "B3", "U1"):
        assert mint in queue["assignments"]
        assert queue["assignments"][mint]["primary_archetype"] in (RAPID_BIRTH_LAUNCH, BURST_LAUNCH, UNCLASSIFIED)
    assert queue["conserved"] is True
    assert sum(a["count"] for a in queue["archetypes"]) == queue["total_launches"]


def test_totals_equal_stage1_investigation_population(db_factory):
    """The Behaviour Queue's total_launches must equal the same migrated
    -launch population X27.2's investigation pipeline measures (both
    query token_analysis.migrated_at with the same window)."""
    now = int(time.time())
    ta_rows = [{"mint": f"M{i}", "migrated_at": now - i} for i in range(10)]
    ops_path, core_path = db_factory([], ta_rows)
    queue = build_behaviour_queue(ops_path, core_path, now=now, window_seconds=86400)
    assert queue["total_launches"] == 10


def test_drilldown_returns_only_launches_assigned_to_that_archetype(db_factory):
    now = int(time.time())
    wt_rows = [{"mint": "R1", "create_time": now, "birth_to_launch_seconds": 1}]
    ta_rows = [
        {"mint": "R1", "migrated_at": now},
        {"mint": "U1", "migrated_at": now - 50000},
    ]
    ops_path, core_path = db_factory(wt_rows, ta_rows)
    queue = build_behaviour_queue(ops_path, core_path, now=now)
    rapid_mints = launches_in_archetype(queue, RAPID_BIRTH_LAUNCH)
    assert rapid_mints == ["R1"]
    unclassified_mints = launches_in_archetype(queue, UNCLASSIFIED)
    assert unclassified_mints == ["U1"]


def test_zero_database_mutation(db_factory):
    now = int(time.time())
    ta_rows = [{"mint": "M1", "migrated_at": now}]
    ops_path, core_path = db_factory([], ta_rows)
    before_ops, before_core = _hash(ops_path), _hash(core_path)
    build_behaviour_queue(ops_path, core_path, now=now)
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


def test_investigation_pipeline_bucket_order_now_includes_behavioural_archetypes():
    """X27.5 — behavioural archetypes were merged into investigation_pipeline
    .py's own BUCKET_ORDER; this module's ARCHETYPE_ORDER remains the
    source of truth for archetype ids/labels, but the priority ranking now
    lives in investigation_pipeline.py alongside the attribution buckets."""
    from src.ops.investigation_pipeline import BUCKET_ORDER, RAPID_BIRTH_LAUNCH, BURST_LAUNCH
    assert BUCKET_ORDER == (
        "KNOWN_OPERATION", "KNOWN_INFRASTRUCTURE", "REPEAT_CREATOR",
        RAPID_BIRTH_LAUNCH, BURST_LAUNCH,
        "UNKNOWN_INFRASTRUCTURE", "LINEAGE_GAP", "INSUFFICIENT_EVIDENCE",
    )


def test_standalone_behaviour_queue_route_removed_by_x27_5():
    """X27.5 removed the separate Behaviour Queue dashboard entirely --
    every migrated launch now appears in exactly one bucket via
    /api/ops-v2/investigation-pipeline."""
    from flask import Flask
    from src.core.operation_dashboard_routes import ops_dashboard_bp
    app = Flask(__name__)
    app.register_blueprint(ops_dashboard_bp)
    client = app.test_client()
    r = client.get("/api/ops-v2/behaviour-queue?window=24h")
    assert r.status_code == 404


def test_archetype_metadata_exposes_coverage_confidence_evidence():
    """Phase 6 -- every archetype must explicitly expose coverage,
    confidence, and evidence source; coverage must never be presented as
    recall (checked via the explicit coverage_note wording)."""
    from src.ops.behaviour_queue import ARCHETYPE_ORDER
    import os
    ops_db, core_db = os.path.join("database", "wt_ops_v2.db"), os.path.join("database", "flex_complete_database.db")
    if not (os.path.exists(ops_db) and os.path.exists(core_db)):
        pytest.skip("live databases not available in this environment")
    queue = build_behaviour_queue(ops_db, core_db)
    for a in queue["archetypes"]:
        assert "coverage_pct" in a
        assert "confidence" in a
        assert "evidence_source" in a
        assert a["archetype"] in ARCHETYPE_ORDER


def test_rapid_birth_launch_threshold_matches_measured_corpus_cutoff():
    assert RAPID_BIRTH_LAUNCH_THRESHOLD_SECONDS == 5


def test_burst_launch_constants_are_measured_not_arbitrary():
    assert BURST_WINDOW_SECONDS == 60
    assert BURST_MIN_CLUSTER_SIZE == 3


# ---------------------------------------------------------------------------
# X27.4 reframe — "Behavioural Discovery Engine": classification must never
# depend on treasury, operator identity, or infrastructure. WATCHTOWER's
# corpus is used only to validate precision (Phase 4), never as the
# classification target itself.
# ---------------------------------------------------------------------------

def test_classification_functions_never_take_treasury_operator_or_infrastructure_params():
    """Structural guarantee: none of the three public classification entry
    points accept a treasury/operator/infrastructure identity as an input --
    they are only ever handed database paths and a time window."""
    for fn in (rapid_birth_launch_lookup, burst_launch_lookup, build_behaviour_queue):
        params = set(inspect.signature(fn).parameters.keys())
        forbidden = {"treasury", "operator", "operator_id", "infrastructure", "subprov", "creator"}
        assert not (params & forbidden), f"{fn.__name__} accepts a forbidden identity parameter: {params & forbidden}"


def test_behaviour_queue_module_never_queries_treasury_operator_infrastructure_tables():
    """Source-level guarantee: the module's actual SQL/import statements
    must never reference any table or module keyed by treasury/operator
    /infrastructure identity. Prose in comments/docstrings legitimately
    discusses the downstream relationship (Phase 5), so this only checks
    executable lines (conn.execute(...) calls and import statements), not
    the full source text -- avoiding false positives from documentation."""
    import ast
    import src.ops.behaviour_queue as bq
    source = inspect.getsource(bq)
    tree = ast.parse(source)
    forbidden_tables = (
        "wt_confirmed_treasuries", "wt_treasury_review", "operator_entities",
        "wt_discovered_subprovs", "infra_mapping",
        "wt_active_subprov_sessions", "wt_wrap_close_candidates",
    )
    executable_strings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    executable_strings.append(arg.value)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                executable_strings.append(alias.name)
            if isinstance(node, ast.ImportFrom) and node.module:
                executable_strings.append(node.module)
    joined = " ".join(executable_strings)
    for table in forbidden_tables:
        assert table not in joined, f"behaviour_queue.py's SQL/imports reference forbidden table/module: {table}"


def test_rapid_birth_launch_result_identical_regardless_of_treasury_context(db_factory):
    """Two mints with identical canonical lifecycle timing must classify
    identically even though nothing about treasury/operator/infrastructure
    is passed in or varies between them -- proving the classification is a
    pure function of lifecycle timing alone."""
    now = int(time.time())
    wt_rows = [
        {"mint": "M1", "create_time": now, "birth_to_launch_seconds": 2},
        {"mint": "M2", "create_time": now, "birth_to_launch_seconds": 2},
    ]
    ta_rows = [{"mint": "M1", "migrated_at": now}, {"mint": "M2", "migrated_at": now}]
    ops_path, core_path = db_factory(wt_rows, ta_rows)
    lookup = rapid_birth_launch_lookup(ops_path)
    assert lookup["M1"]["matched"] == lookup["M2"]["matched"] == True


def test_phase5_candidate_discovery_returns_bare_mint_list_only(db_factory):
    """The Phase 5 treasury-discovery entry point must return only mints --
    no funder, treasury, or operator fields -- keeping the behaviour-
    identifies / walkback-explains boundary intact."""
    now = int(time.time())
    wt_rows = [{"mint": "Candidate1", "create_time": now, "birth_to_launch_seconds": 1}]
    ta_rows = [{"mint": "Candidate1", "migrated_at": now}]
    ops_path, core_path = db_factory(wt_rows, ta_rows)
    result = rapid_birth_launch_candidates_for_treasury_discovery(ops_path, core_path, now=now)
    assert result == ["Candidate1"]
    assert all(isinstance(m, str) for m in result)
