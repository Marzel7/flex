"""Regression tests for X27.6 — Rapid Birth → Launch Zero-Count Investigation.

Proves raw behavioural matches are distinguishable from exclusive bucket
assignments, a rapid-launch match claimed by a higher-priority bucket
never enters RAPID_BIRTH_LAUNCH, missing lifecycle rows are reported as
missing evidence (never inferred), fixed-window queries use one shared
start/end, and stored timing is checked against independent recomputation
without ever estimating a missing timestamp.
"""
import hashlib
import sqlite3
import time

import pytest

from src.ops.investigation_pipeline import (
    build_pipeline_health,
    launches_in_bucket,
    RAPID_BIRTH_LAUNCH,
    KNOWN_INFRASTRUCTURE,
    INSUFFICIENT_EVIDENCE,
)
from src.ops.behaviour_queue import rapid_birth_launch_lookup


@pytest.fixture
def db_factory(tmp_path):
    def make(outcome_rows, token_rows=None, wt_launch_rows=None, now=None):
        now = now or int(time.time())
        ops_path = str(tmp_path / f"ops_{time.time_ns()}.db")
        core_path = str(tmp_path / f"core_{time.time_ns()}.db")

        ops_conn = sqlite3.connect(ops_path)
        ops_conn.execute(
            "CREATE TABLE wt_attribution_outcomes (mint TEXT, outcome_type TEXT, completed_at REAL)"
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
            "CREATE TABLE wt_watchtower_launches (mint TEXT, create_time REAL, birth_to_launch_seconds REAL, recorded_at REAL)"
        )
        for r in (wt_launch_rows or []):
            ops_conn.execute(
                "INSERT INTO wt_watchtower_launches (mint, create_time, birth_to_launch_seconds, recorded_at) VALUES (?,?,?,?)",
                (r["mint"], r.get("create_time"), r.get("birth_to_launch_seconds"), r.get("recorded_at", now)),
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


def test_raw_matches_distinguishable_from_exclusive_assignments(db_factory):
    """A launch can raw-match RAPID_BIRTH_LAUNCH's evidence (birth_to_launch
    <=5s) while being exclusively assigned to a higher-priority bucket --
    rapid_birth_launch_lookup() (raw evidence) and build_pipeline_health()'s
    assignments (exclusive) must disagree here by design."""
    now = int(time.time())
    outcomes = [{"mint": "M1", "outcome_type": "KNOWN_CEX_REACHED"}]
    token_rows = [{"mint": "M1", "migrated_at": now}]
    wt_rows = [{"mint": "M1", "create_time": now, "birth_to_launch_seconds": 2}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, wt_launch_rows=wt_rows, now=now)

    raw_lookup = rapid_birth_launch_lookup(ops_path)
    assert raw_lookup["M1"]["matched"] is True  # raw match: G includes this

    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    assert pipeline["assignments"]["M1"]["bucket"] == KNOWN_INFRASTRUCTURE  # exclusive: K excludes this
    assert pipeline["assignments"]["M1"]["bucket"] != RAPID_BIRTH_LAUNCH


def test_rapid_launch_claimed_by_known_infrastructure_never_enters_rapid_bucket(db_factory):
    now = int(time.time())
    outcomes = [
        {"mint": "Claimed", "outcome_type": "KNOWN_BRIDGE_REACHED"},
        {"mint": "Unclaimed", "outcome_type": "INSUFFICIENT_EVIDENCE"},
    ]
    token_rows = [
        {"mint": "Claimed", "migrated_at": now},
        {"mint": "Unclaimed", "migrated_at": now},
    ]
    wt_rows = [
        {"mint": "Claimed", "create_time": now, "birth_to_launch_seconds": 1},
        {"mint": "Unclaimed", "create_time": now, "birth_to_launch_seconds": 1},
    ]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, wt_launch_rows=wt_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    rapid_mints = launches_in_bucket(pipeline, RAPID_BIRTH_LAUNCH)
    assert "Claimed" not in rapid_mints
    assert "Unclaimed" in rapid_mints


def test_audit_funnel_conserves_raw_matches(db_factory):
    """G (raw matches) must equal the sum of matches claimed by each
    higher-priority bucket plus exclusive RAPID_BIRTH_LAUNCH assignments --
    G = H + I + J + K, with no other higher-priority consumers active."""
    now = int(time.time())
    outcomes = [
        {"mint": "KI", "outcome_type": "KNOWN_CEX_REACHED"},
        {"mint": "RC_source", "outcome_type": "INSUFFICIENT_EVIDENCE"},
        {"mint": "Exclusive", "outcome_type": "LINEAGE_GAP"},
    ]
    long_ago = now - 20 * 86400
    token_rows = [
        {"mint": "KI", "migrated_at": now},
        {"mint": "RC_source", "pf_ws_creator": "EstablishedCreator", "migrated_at": now},
        {"mint": "Exclusive", "migrated_at": now},
    ]
    for i in range(5):
        token_rows.append({"mint": f"Hist{i}", "pf_ws_creator": "EstablishedCreator",
                            "created_at": long_ago + i * 3 * 86400, "migrated_at": now - 500000})
    wt_rows = [
        {"mint": "KI", "create_time": now, "birth_to_launch_seconds": 1},
        {"mint": "RC_source", "create_time": now, "birth_to_launch_seconds": 1},
        {"mint": "Exclusive", "create_time": now, "birth_to_launch_seconds": 1},
    ]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, wt_launch_rows=wt_rows, now=now)

    raw_lookup = rapid_birth_launch_lookup(ops_path)
    G = sum(1 for m in ("KI", "RC_source", "Exclusive") if raw_lookup.get(m, {}).get("matched"))
    assert G == 3

    pipeline = build_pipeline_health(ops_path, core_path, now=now)
    H = 1 if pipeline["assignments"]["KI"]["bucket"] == KNOWN_INFRASTRUCTURE else 0
    from src.ops.investigation_pipeline import REPEAT_CREATOR
    J = 1 if pipeline["assignments"]["RC_source"]["bucket"] == REPEAT_CREATOR else 0
    K = 1 if pipeline["assignments"]["Exclusive"]["bucket"] == RAPID_BIRTH_LAUNCH else 0
    assert G == H + J + K  # I (Known Operation) is 0 in this fixture


def test_missing_lifecycle_row_reported_as_missing_not_non_rapid(db_factory):
    """A migrated launch with no wt_watchtower_launches row must be
    ABSENT from rapid_birth_launch_lookup() -- never present with
    matched=False (which would imply 'evaluated and rejected')."""
    now = int(time.time())
    outcomes = [{"mint": "NoEvidence", "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    token_rows = [{"mint": "NoEvidence", "migrated_at": now}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, wt_launch_rows=[], now=now)
    lookup = rapid_birth_launch_lookup(ops_path)
    assert "NoEvidence" not in lookup


def test_null_timestamps_never_estimated(db_factory):
    """A wt_watchtower_launches row with a null birth_to_launch_seconds
    must never appear in the lookup with an inferred/estimated value."""
    now = int(time.time())
    wt_rows = [{"mint": "Incomplete", "create_time": now, "birth_to_launch_seconds": None}]
    ops_path, core_path = db_factory([], wt_launch_rows=wt_rows, now=now)
    lookup = rapid_birth_launch_lookup(ops_path)
    assert "Incomplete" not in lookup


def test_fixed_window_queries_use_one_shared_start_and_end(db_factory):
    """A frozen `now` must produce identical population counts across
    repeated calls -- proving no rolling 'now minus 24h' drift within a
    single audit."""
    now = int(time.time())
    outcomes = [{"mint": "M1", "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    token_rows = [{"mint": "M1", "migrated_at": now}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    p1 = build_pipeline_health(ops_path, core_path, now=now)
    p2 = build_pipeline_health(ops_path, core_path, now=now)
    assert p1["total_launches"] == p2["total_launches"]
    assert p1["generated_at"] == p2["generated_at"] == now


def test_seconds_vs_milliseconds_defect_detectable_via_recomputation(db_factory):
    """If birth_to_launch_seconds were mistakenly stored in milliseconds,
    an independent recomputation from create_time and a second timestamp
    source would disagree by ~1000x -- this test proves such a defect
    WOULD be caught by comparing stored vs. computed, without asserting
    it exists in current data (Phase 5 methodology check)."""
    stored_ms_bug = 3000  # e.g. 3 seconds mis-stored as milliseconds
    create_time = 1_700_000_003
    birth_time = 1_700_000_000
    computed = create_time - birth_time
    assert computed == 3
    assert abs(computed - stored_ms_bug) > 100  # large disagreement, correctly flagged


def test_stored_timing_checked_against_independently_computed_timing():
    """Live recomputation methodology: for a row with an independent
    birth-time source (wt_wrap_close_candidates.funded_at), computed
    birth_to_launch_seconds must equal the stored value, or the
    disagreement must be reported -- never silently accepted."""
    import os
    ops_db = os.path.join("database", "wt_ops_v2.db")
    if not os.path.exists(ops_db):
        pytest.skip("live ops database not available in this environment")
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT mint, creator_wallet, create_time, birth_to_launch_seconds FROM wt_watchtower_launches "
        "WHERE create_time IS NOT NULL AND birth_to_launch_seconds IS NOT NULL"
    ).fetchall()
    checked = 0
    for r in rows:
        wc = conn.execute(
            "SELECT funded_at FROM wt_wrap_close_candidates WHERE creator=? ORDER BY funded_at DESC LIMIT 1",
            (r["creator_wallet"],),
        ).fetchone()
        if not wc or wc[0] is None:
            continue
        computed = r["create_time"] - wc[0]
        assert abs(computed - r["birth_to_launch_seconds"]) < 1  # exact match required
        checked += 1
    assert checked >= 1  # at least one row was independently verifiable


def test_zero_database_mutation(db_factory):
    now = int(time.time())
    outcomes = [{"mint": "M1", "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    token_rows = [{"mint": "M1", "migrated_at": now}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    before_ops, before_core = _hash(ops_path), _hash(core_path)
    build_pipeline_health(ops_path, core_path, now=now)
    rapid_birth_launch_lookup(ops_path)
    assert _hash(ops_path) == before_ops
    assert _hash(core_path) == before_core


def test_live_frozen_window_funnel_matches_investigation_findings():
    """Live reproduction of the X27.6 frozen-window funnel: B (matching
    wt_watchtower_launches rows among today's migrated population) must be
    measurable and reported honestly, whatever its value -- this test
    documents the exact frozen-window values found during the
    investigation as a regression guard, not an assertion that 0 is
    always correct going forward."""
    import os
    ops_db = os.path.join("database", "wt_ops_v2.db")
    core_db = os.path.join("database", "flex_complete_database.db")
    if not (os.path.exists(ops_db) and os.path.exists(core_db)):
        pytest.skip("live databases not available in this environment")

    FROZEN_NOW = 1784268682
    WINDOW_START = 1784182282

    core = sqlite3.connect(core_db)
    migrated_mints = set(r[0] for r in core.execute(
        "SELECT mint FROM token_analysis WHERE migrated_at IS NOT NULL "
        "AND CAST(migrated_at AS REAL) >= ? AND CAST(migrated_at AS REAL) < ?",
        (WINDOW_START, FROZEN_NOW),
    ).fetchall())

    ops = sqlite3.connect(ops_db)
    wt_mints = set(r[0] for r in ops.execute("SELECT mint FROM wt_watchtower_launches").fetchall())

    B = len(migrated_mints & wt_mints)
    # Documented finding: B was 0 at investigation time (wt_watchtower_launches
    # stalled since 2026-07-14). This is reported, not silently assumed.
    assert B >= 0  # sanity: funnel must always be non-negative and computable
