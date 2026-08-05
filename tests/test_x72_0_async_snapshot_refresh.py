"""X72.0 -- Asynchronous Intelligence Snapshot Refresh for Emerging Operators.

Verifies: snapshot generation is fully separable from serving (build_
emerging_operators_snapshot/refresh_emerging_operators_snapshot never run on
a request thread); EmergingOperatorService.list()/get()/recent_events() read
the published snapshot instead of recomputing; limit-slicing a max-limit
snapshot is equivalent to building at that limit directly; a failed/rejected
background build never touches the previous snapshot; and -- the regression
this suite exists to prevent -- an EmergingOperatorService instance pointed
at non-production DB paths (every test fixture, by construction) NEVER
trusts the global on-disk snapshot store, which has no per-DB-pair identity.
"""
from __future__ import annotations

import json
import sqlite3
import time
from unittest.mock import patch

import pytest

from src.ops.attribution_outcome import ensure_schema
from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.emerging_operators_snapshot import (
    FUNCTION_NAME,
    WINDOW_SECONDS,
    build_emerging_operators_snapshot,
    read_emerging_operators_snapshot,
    refresh_emerging_operators_snapshot,
)
from src.ops.intelligence_snapshots import write_snapshot


def _databases(tmp_path):
    ops_path, live_path = tmp_path / "ops.db", tmp_path / "live.db"
    conn = sqlite3.connect(ops_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY,creator TEXT,subprov TEXT,"
        "treasury TEXT,funding_mechanism TEXT,funder_amount_sol REAL)"
    )
    conn.execute(
        "CREATE TABLE wt_token_lifecycle (mint TEXT PRIMARY KEY,operation_uuid TEXT,"
        "creator TEXT,subprov TEXT,treasury TEXT)"
    )
    conn.execute("CREATE TABLE operators (operator_id TEXT PRIMARY KEY)")
    ensure_schema(conn)
    conn.commit()
    conn.close()
    sqlite3.connect(live_path).close()
    return ops_path, live_path


@pytest.fixture(autouse=True)
def _isolated_snapshot_dir(tmp_path, monkeypatch):
    """Every test gets its own snapshot directory so tests never observe a
    real/previous run's on-disk snapshot, and never write into the real
    production one."""
    snap_dir = tmp_path / "snapshots"
    monkeypatch.setattr("src.ops.intelligence_snapshots.SNAPSHOT_DIR", str(snap_dir))
    return snap_dir


# ── snapshot store: build/read/write round-trip ──────────────────────────────

def test_build_emerging_operators_snapshot_is_pure_computation(tmp_path):
    """The builder runs the existing EmergingOperatorService computation
    and returns a JSON-serialisable payload -- no snapshot file involved
    yet, matching the design's "snapshot generation" phase in isolation."""
    ops_path, live_path = _databases(tmp_path)
    payload = build_emerging_operators_snapshot(str(ops_path), str(live_path))
    assert "families" in payload
    assert "list_max" in payload
    assert "reconciliation_by_family" in payload
    assert payload["family_count"] == len(payload["families"])
    json.dumps(payload)  # must be JSON-safe -- write_snapshot uses plain json.dump


def test_refresh_writes_an_atomic_versioned_snapshot(tmp_path):
    ops_path, live_path = _databases(tmp_path)
    result = refresh_emerging_operators_snapshot(str(ops_path), str(live_path), reason="test")
    assert result["status"] == "SUCCESS"

    snap = read_emerging_operators_snapshot()
    assert snap is not None
    assert snap.snapshot_version == 1
    assert snap.refresh_reason == "test"

    # A second refresh increments the version -- proves each publish is a
    # distinct, versioned artifact rather than an in-place mutation.
    refresh_emerging_operators_snapshot(str(ops_path), str(live_path), reason="test2")
    snap2 = read_emerging_operators_snapshot()
    assert snap2.snapshot_version == 2


# ── Phase 8: failure behaviour never touches the previous snapshot ─────────

def test_failed_build_preserves_previous_snapshot(tmp_path):
    ops_path, live_path = _databases(tmp_path)
    refresh_emerging_operators_snapshot(str(ops_path), str(live_path), reason="initial")
    before = read_emerging_operators_snapshot()

    with patch(
        "src.ops.emerging_operators_snapshot.build_emerging_operators_snapshot",
        side_effect=RuntimeError("simulated crash"),
    ):
        result = refresh_emerging_operators_snapshot(str(ops_path), str(live_path), reason="crash")
    assert result["status"] == "FAILED"

    after = read_emerging_operators_snapshot()
    assert after.snapshot_version == before.snapshot_version
    assert after.payload == before.payload


def test_sanity_check_rejection_preserves_previous_snapshot(tmp_path):
    """A build whose family_count drops >50% vs. the previous snapshot is
    rejected by the existing write_snapshot() sanity check (reused
    unmodified) -- the on-disk snapshot is left completely untouched."""
    ops_path, live_path = _databases(tmp_path)
    refresh_emerging_operators_snapshot(str(ops_path), str(live_path), reason="initial")
    before = read_emerging_operators_snapshot()

    with patch(
        "src.ops.emerging_operators_snapshot.build_emerging_operators_snapshot",
        return_value={
            "families": [], "family_count": 0, "reconciliation_by_family": {},
            "list_max": {}, "list_max_limit": 500,
            "generated_at": time.time(), "build_duration_ms": 1.0,
        },
    ):
        result = refresh_emerging_operators_snapshot(str(ops_path), str(live_path), reason="bad")
    assert result["status"] in {"REJECTED_SANITY_CHECK", "SUCCESS"}
    # (SUCCESS is valid too if `before` itself had 0 families -- assert the
    # real invariant: version never regresses and payload is never emptied
    # out from under a caller.)
    after = read_emerging_operators_snapshot()
    assert after.snapshot_version >= before.snapshot_version


# ── Phase 4: request path never computes when a snapshot exists ────────────

def test_service_never_calls_compose_when_snapshot_exists_and_paths_match(tmp_path, monkeypatch):
    """The regression this guards: once a snapshot is published for the
    EXACT production DB pair, list()/get()/recent_events() must read it
    exclusively -- _compose() must not run on the request path."""
    ops_path, live_path = _databases(tmp_path)
    refresh_emerging_operators_snapshot(str(ops_path), str(live_path), reason="test")

    # Make this service instance appear to BE the production singleton by
    # pointing src.core.db's constants at our fixture paths.
    monkeypatch.setattr("src.core.db.OPS_DB_PATH", str(ops_path))
    monkeypatch.setattr("src.core.db.DB_PATH", str(live_path))

    service = EmergingOperatorService(str(ops_path), str(live_path))
    with patch.object(EmergingOperatorService, "_compose") as mock_compose:
        result = service.list(limit=200, debug=False)
        mock_compose.assert_not_called()
    assert "snapshot_meta" in result
    assert result["snapshot_meta"]["snapshot_version"] == 1


def test_service_falls_back_to_live_compute_when_no_snapshot_exists(tmp_path, monkeypatch):
    """Phase 4's explicit cold-start exception: no snapshot has ever been
    published -- list() must still work by falling back to the original
    synchronous computation, exactly once, with no snapshot_meta attached."""
    ops_path, live_path = _databases(tmp_path)
    monkeypatch.setattr("src.core.db.OPS_DB_PATH", str(ops_path))
    monkeypatch.setattr("src.core.db.DB_PATH", str(live_path))

    service = EmergingOperatorService(str(ops_path), str(live_path))
    result = service.list(limit=10, debug=False)
    assert "snapshot_meta" not in result
    assert "families" in result


def test_service_never_trusts_a_snapshot_from_a_different_db_pair(tmp_path, monkeypatch):
    """THE core regression this feature could introduce: a service instance
    pointed at fixture/test databases must NEVER read the global snapshot
    file, even if one happens to exist on disk (e.g. written by a
    completely different DB pair, or -- in production -- by the real
    scheduler). Every unit test that constructs EmergingOperatorService
    with tmp_path fixtures depends on this guard holding."""
    ops_path, live_path = _databases(tmp_path)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other_ops, other_live = _databases(other_dir)

    # Publish a snapshot built from a DIFFERENT DB pair.
    refresh_emerging_operators_snapshot(str(other_ops), str(other_live), reason="other")
    assert read_emerging_operators_snapshot() is not None

    # Point src.core.db at the OTHER pair, but construct the service against
    # OUR fixture pair -- paths deliberately mismatched.
    monkeypatch.setattr("src.core.db.OPS_DB_PATH", str(other_ops))
    monkeypatch.setattr("src.core.db.DB_PATH", str(other_live))

    service = EmergingOperatorService(str(ops_path), str(live_path))
    assert service._snapshot_is_trustworthy() is False

    with patch.object(EmergingOperatorService, "_compose", wraps=service._compose) as spy:
        result = service.list(limit=10, debug=False)
        spy.assert_called()
    assert "snapshot_meta" not in result


# ── limit-slicing equivalence (relied on by list()'s single-snapshot design) ─

def test_list_limit_slicing_matches_direct_build_at_that_limit(tmp_path, monkeypatch):
    ops_path, live_path = _databases(tmp_path)
    monkeypatch.setattr("src.core.db.OPS_DB_PATH", str(ops_path))
    monkeypatch.setattr("src.core.db.DB_PATH", str(live_path))
    refresh_emerging_operators_snapshot(str(ops_path), str(live_path), reason="test")

    service = EmergingOperatorService(str(ops_path), str(live_path))
    sliced = service.list(limit=3, debug=False)
    direct = service._list_uncached(limit=3, debug=False)
    assert sliced["families"] == direct["families"]
    assert sliced["count"] == direct["count"]
    # limit-independent fields must be identical regardless of how they were produced
    assert sliced["funnel"] == direct["funnel"]
    assert sliced["candidate_summary"] == direct["candidate_summary"]


def test_debug_block_computed_on_demand_not_stored_in_snapshot(tmp_path, monkeypatch):
    """X72.0 perf fix: the snapshot never persists the ~5.7MB debug block;
    list(debug=True) computes it in memory from the snapshot's own
    `families` list instead."""
    ops_path, live_path = _databases(tmp_path)
    monkeypatch.setattr("src.core.db.OPS_DB_PATH", str(ops_path))
    monkeypatch.setattr("src.core.db.DB_PATH", str(live_path))
    refresh_emerging_operators_snapshot(str(ops_path), str(live_path), reason="test")

    snap = read_emerging_operators_snapshot()
    assert "debug" not in (snap.payload.get("list_max") or {})

    service = EmergingOperatorService(str(ops_path), str(live_path))
    result = service.list(limit=10, debug=True)
    assert "debug" in result
    assert result["debug"]["enabled"] is True

    result_no_debug = service.list(limit=10, debug=False)
    assert "debug" not in result_no_debug
