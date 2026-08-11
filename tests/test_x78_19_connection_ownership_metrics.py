"""Focused X78.19 ownership and second-hop diagnostics contracts."""

from __future__ import annotations

import json
import sqlite3

from src.utils.db_locking import (
    db_connect,
    get_open_connection_summary,
    record_connection_snapshot,
)


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_connection_lifecycle_has_stable_identity_and_close(tmp_path, monkeypatch):
    diagnostics = tmp_path / "connections.jsonl"
    database = tmp_path / "connections.db"
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(diagnostics))

    connection = db_connect(str(database))
    connection_id = connection._db_connection_id
    summary = get_open_connection_summary()
    assert any(row["connection_id"] == connection_id for row in summary["oldest"])
    connection.close()

    events = [event for event in _events(diagnostics) if event.get("connection_id") == connection_id]
    assert [event["event"] for event in events] == ["open", "close"]
    assert events[0]["mode"] == "read_write"
    assert events[0]["pid"] == events[1]["pid"]
    assert events[1]["age_ms"] >= 0
    assert not any(
        row["connection_id"] == connection_id
        for row in get_open_connection_summary()["oldest"]
    )


def test_fd_snapshot_correlates_os_and_registry_counts(tmp_path, monkeypatch):
    diagnostics = tmp_path / "connections.jsonl"
    database = tmp_path / "connections.db"
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(diagnostics))
    connection = db_connect(str(database), read_only=False)
    snapshot = record_connection_snapshot(primary_fd_count=3, extra={"source": "test"})
    connection.close()

    assert snapshot["registry_count"] >= 1
    assert snapshot["registry_fd_delta"] == 3 - snapshot["registry_count"]
    stored = next(event for event in _events(diagnostics) if event["event"] == "snapshot")
    assert stored["primary_fd_count"] == 3
    assert stored["extra"] == {"source": "test"}
    assert "age_buckets" in stored
    assert stored["connection_ids"]


def test_connection_context_exception_still_closes(tmp_path, monkeypatch):
    diagnostics = tmp_path / "connections.jsonl"
    database = tmp_path / "connections.db"
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(diagnostics))
    connection_id = None
    try:
        with db_connect(str(database)) as connection:
            connection_id = connection._db_connection_id
            raise RuntimeError("cancelled-work analogue")
    except RuntimeError:
        pass
    events = [event for event in _events(diagnostics) if event.get("connection_id") == connection_id]
    assert [event["event"] for event in events] == ["open", "close"]
    assert events[-1]["transaction_open"] is False


def test_connection_close_remains_idempotent_with_diagnostics(tmp_path, monkeypatch):
    diagnostics = tmp_path / "connections.jsonl"
    database = tmp_path / "connections.db"
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(diagnostics))
    connection = db_connect(str(database))
    connection.close()
    connection.close()


def test_second_hop_build_emits_phase_and_publication_metrics(tmp_path, monkeypatch):
    from src.core.second_hop_builder import SecondHopExpansionBuilder
    from tests.test_x78_18_second_hop_isolation import _seed

    database = tmp_path / "second-hop.db"
    diagnostics = tmp_path / "second-hop.jsonl"
    _seed(database)
    monkeypatch.setenv("SECOND_HOP_BUILD_METRICS_PATH", str(diagnostics))

    result = SecondHopExpansionBuilder(str(database)).build()
    assert result["status"] == "success"
    event = _events(diagnostics)[-1]
    assert event["status"] == "success"
    assert event["build_id"]
    assert event["source_rows"]["transfer_index"] == 2
    assert event["read_snapshot_write_lane_owned"] is False
    assert event["materialize_seconds"] >= 0
    assert event["publication_write_lane_wait_seconds"] >= 0
    assert event["publication_write_lane_hold_seconds"] >= 0
    assert event["output_rows"]["funder_upstream_links"] == 2


def test_second_hop_metrics_failure_is_fail_open(tmp_path, monkeypatch):
    from src.core.second_hop_builder import SecondHopExpansionBuilder
    from tests.test_x78_18_second_hop_isolation import _seed

    database = tmp_path / "second-hop.db"
    _seed(database)
    monkeypatch.setenv("SECOND_HOP_BUILD_METRICS_PATH", "/dev/null/not-a-file")
    assert SecondHopExpansionBuilder(str(database)).build()["status"] == "success"
