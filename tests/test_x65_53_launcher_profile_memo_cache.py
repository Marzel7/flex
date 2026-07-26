"""X65.53 — Discovery Load Performance Optimisation.

Verifies the per-build memo cache added to assign_bucket()/build_pipeline_
health() eliminates duplicate evaluate_launcher_profile() calls when
multiple mints in the window share the same resolved creator, WITHOUT
changing the classification result, the underlying query shape, or cache
semantics anywhere else. The cache is scoped strictly to a single
build_pipeline_health() call — never persisted across requests/builds.
"""
import sqlite3
import time

import pytest

from src.ops.investigation_pipeline import build_pipeline_health, assign_bucket


@pytest.fixture
def db_factory(tmp_path):
    def make(outcome_rows, token_rows=None, now=None):
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
            "CREATE TABLE wt_watchtower_launches (mint TEXT, create_time REAL, birth_to_launch_seconds REAL)"
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


def test_shared_creator_across_mints_evaluates_launcher_profile_once(db_factory, monkeypatch):
    now = int(time.time())
    outcome_rows = [
        {"mint": "T1", "outcome_type": "LINEAGE_GAP", "completed_at": now},
        {"mint": "T2", "outcome_type": "LINEAGE_GAP", "completed_at": now},
        {"mint": "T3", "outcome_type": "LINEAGE_GAP", "completed_at": now},
    ]
    token_rows = [
        {"mint": "T1", "pf_ws_creator": "SHARED_CREATOR", "created_at": now - 100},
        {"mint": "T2", "pf_ws_creator": "SHARED_CREATOR", "created_at": now - 200},
        {"mint": "T3", "pf_ws_creator": "OTHER_CREATOR", "created_at": now - 300},
    ]
    ops_path, core_path = db_factory(outcome_rows, token_rows, now=now)

    calls = []

    def fake_evaluate(ops_conn, core_conn, creator, now=None):
        calls.append(creator)
        return {"established": False}

    monkeypatch.setattr(
        "src.ops.attribution_outcome.evaluate_launcher_profile", fake_evaluate
    )

    build_pipeline_health(ops_path, core_path, window_seconds=86400, now=now)

    # 3 mints resolve to 2 unique creators (SHARED_CREATOR used twice) --
    # the memo cache must collapse the two SHARED_CREATOR calls into one.
    assert calls.count("SHARED_CREATOR") == 1
    assert calls.count("OTHER_CREATOR") == 1
    assert len(calls) == 2


def test_memo_cache_does_not_change_classification_result(db_factory, monkeypatch):
    now = int(time.time())
    outcome_rows = [
        {"mint": "T1", "outcome_type": "LINEAGE_GAP", "completed_at": now},
        {"mint": "T2", "outcome_type": "LINEAGE_GAP", "completed_at": now},
    ]
    token_rows = [
        {"mint": "T1", "pf_ws_creator": "SHARED_CREATOR", "created_at": now - 100},
        {"mint": "T2", "pf_ws_creator": "SHARED_CREATOR", "created_at": now - 200},
    ]
    ops_path, core_path = db_factory(outcome_rows, token_rows, now=now)

    def fake_evaluate(ops_conn, core_conn, creator, now=None):
        return {"established": True}

    monkeypatch.setattr(
        "src.ops.attribution_outcome.evaluate_launcher_profile", fake_evaluate
    )

    pipeline = build_pipeline_health(ops_path, core_path, window_seconds=86400, now=now)

    from src.ops.investigation_pipeline import REPEAT_CREATOR
    assert pipeline["assignments"]["T1"]["bucket"] == REPEAT_CREATOR
    assert pipeline["assignments"]["T2"]["bucket"] == REPEAT_CREATOR


def test_profile_cache_is_scoped_per_build_not_shared_across_calls(db_factory, monkeypatch):
    now = int(time.time())
    outcome_rows = [{"mint": "T1", "outcome_type": "LINEAGE_GAP", "completed_at": now}]
    token_rows = [{"mint": "T1", "pf_ws_creator": "SHARED_CREATOR", "created_at": now - 100}]
    ops_path, core_path = db_factory(outcome_rows, token_rows, now=now)

    calls = []

    def fake_evaluate(ops_conn, core_conn, creator, now=None):
        calls.append(creator)
        return {"established": False}

    monkeypatch.setattr(
        "src.ops.attribution_outcome.evaluate_launcher_profile", fake_evaluate
    )

    build_pipeline_health(ops_path, core_path, window_seconds=86400, now=now)
    build_pipeline_health(ops_path, core_path, window_seconds=86400, now=now)

    # Two independent builds -- the memo cache must NOT persist across
    # them, so the same creator is re-evaluated once per build (2 total).
    assert len(calls) == 2


def test_assign_bucket_without_profile_cache_still_works(db_factory, monkeypatch):
    # Default (profile_cache=None) call sites elsewhere in the codebase
    # must behave exactly as before -- no cache, no crash.
    ops_conn = sqlite3.connect(":memory:")
    ops_conn.row_factory = sqlite3.Row
    core_conn = sqlite3.connect(":memory:")
    core_conn.row_factory = sqlite3.Row

    def fake_evaluate(ops_conn, core_conn, creator, now=None):
        return {"established": False}

    monkeypatch.setattr(
        "src.ops.attribution_outcome.evaluate_launcher_profile", fake_evaluate
    )

    result = assign_bucket(ops_conn, core_conn, "T1", "LINEAGE_GAP", creator="C1", now=int(time.time()))
    assert result["bucket"]
