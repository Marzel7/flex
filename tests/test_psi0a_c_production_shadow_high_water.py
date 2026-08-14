import sqlite3

import pytest

import src.evidence.contracts.production_shadow_high_water as high_water_module
from src.evidence.contracts.production_shadow_boundary import build_production_shadow_boundary
from src.evidence.contracts.production_shadow_high_water import (
    HighWaterSpec,
    ProductionShadowHighWaterError,
    capture_production_shadow_read_boundary,
    creator_tokens_cursor_only_high_water_spec,
    verify_production_shadow_read_boundary,
)
from src.evidence.contracts.production_shadow_schema_audit import RequiredRelation, audit_production_schema


def _inputs(tmp_path):
    path = tmp_path / "source.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE records(id INTEGER PRIMARY KEY,event_at INTEGER,payload TEXT)")
    connection.execute("CREATE INDEX idx_records_event ON records(event_at)")
    connection.executemany("INSERT INTO records VALUES (?,?,?)", [(1, 10, "a"), (2, 20, "b")])
    connection.commit(); connection.close()
    boundary = build_production_shadow_boundary(
        engineering_revision="c1f7513d",
        surfaces=({"database_id": "main", "relation_name": "records", "relation_type": "TABLE"},),
    )
    requirements = (RequiredRelation("main", "records", "TABLE", (("id", "INTEGER"), ("event_at", "INTEGER")), (("event_at",),)),)
    audit = audit_production_schema(boundary, {"main": path}, requirements)
    return path, boundary, audit


def test_high_water_is_stable_replayable_and_materializes_no_rows(tmp_path):
    path, boundary, audit = _inputs(tmp_path)
    result = capture_production_shadow_read_boundary(
        boundary, audit, {"main": path},
        (HighWaterSpec("main", "records", "rowid", "event_at"),),
        captured_at_utc_ns=123,
        max_query_seconds=1,
    )
    assert result.relations[0].cursor_upper_inclusive == 2
    assert result.relations[0].event_upper_inclusive == 20
    assert result.evidence_rows_materialized == 0
    assert verify_production_shadow_read_boundary(result)


def test_insert_after_capture_does_not_change_immutable_boundary(tmp_path):
    path, boundary, audit = _inputs(tmp_path)
    result = capture_production_shadow_read_boundary(
        boundary, audit, {"main": path},
        (HighWaterSpec("main", "records", "rowid", "event_at"),), captured_at_utc_ns=123,
        max_query_seconds=1,
    )
    connection = sqlite3.connect(path); connection.execute("INSERT INTO records VALUES (3,30,'c')"); connection.commit(); connection.close()
    assert result.relations[0].cursor_upper_inclusive == 2
    assert result.relations[0].event_upper_inclusive == 20


def test_unknown_surface_column_and_schema_gate_fail_closed(tmp_path):
    path, boundary, audit = _inputs(tmp_path)
    with pytest.raises(ProductionShadowHighWaterError, match="BOUNDARY_SPEC"):
        capture_production_shadow_read_boundary(
            boundary, audit, {"main": path}, (), captured_at_utc_ns=1, max_query_seconds=1
        )
    with pytest.raises(ProductionShadowHighWaterError, match="INVALID_BOUNDARY_COLUMN"):
        capture_production_shadow_read_boundary(
            boundary, audit, {"main": path},
            (HighWaterSpec("main", "records", "rowid;drop", None),), captured_at_utc_ns=1,
            max_query_seconds=1,
        )


def test_declared_integer_with_text_runtime_high_water_fails_named_and_closed(tmp_path):
    path = tmp_path / "source.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE records(id INTEGER PRIMARY KEY,event_at INTEGER)")
    connection.execute("CREATE INDEX idx_records_event ON records(event_at)")
    connection.execute("INSERT INTO records VALUES (1,'2026-04-21T15:43:38Z')")
    connection.commit(); connection.close()
    boundary = build_production_shadow_boundary(
        engineering_revision="c1f7513d",
        surfaces=({"database_id": "main", "relation_name": "records", "relation_type": "TABLE"},),
    )
    audit = audit_production_schema(
        boundary, {"main": path},
        (RequiredRelation("main", "records", "TABLE", (("id", "INTEGER"), ("event_at", "INTEGER")), (("event_at",),)),),
    )
    with pytest.raises(ProductionShadowHighWaterError, match="NON_INTEGER_HIGH_WATER"):
        capture_production_shadow_read_boundary(
            boundary, audit, {"main": path},
            (HighWaterSpec("main", "records", "rowid", "event_at"),), captured_at_utc_ns=1,
            max_query_seconds=1,
        )


def test_creator_tokens_uses_rowid_only_with_mixed_created_at_encodings(tmp_path):
    path = tmp_path / "creator.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE creator_tokens(creator_address TEXT,mint TEXT,created_at INTEGER NOT NULL)"
    )
    connection.execute("CREATE INDEX idx_creator_tokens_mint ON creator_tokens(mint)")
    connection.executemany(
        "INSERT INTO creator_tokens VALUES (?,?,?)",
        (
            ("creator-a", "mint-a", 1_700_000_000),
            ("creator-b", "mint-b", 1_700_000_000.5),
            ("creator-c", "mint-c", "2026-04-21T15:43:38Z"),
        ),
    )
    connection.commit()
    connection.close()
    boundary = build_production_shadow_boundary(
        engineering_revision="c1f7513d",
        surfaces=(
            {
                "database_id": "creator",
                "relation_name": "creator_tokens",
                "relation_type": "TABLE",
            },
        ),
    )
    audit = audit_production_schema(
        boundary,
        {"creator": path},
        (
            RequiredRelation(
                "creator",
                "creator_tokens",
                "TABLE",
                (("creator_address", "TEXT"), ("mint", "TEXT"), ("created_at", "INTEGER")),
                (("mint",),),
            ),
        ),
    )

    result = capture_production_shadow_read_boundary(
        boundary,
        audit,
        {"creator": path},
        (creator_tokens_cursor_only_high_water_spec(),),
        captured_at_utc_ns=123,
        max_query_seconds=1,
    )

    assert result.relations[0].cursor_column == "rowid"
    assert result.relations[0].cursor_upper_inclusive == 3
    assert result.relations[0].event_column is None
    assert result.relations[0].event_upper_inclusive is None
    assert result.evidence_rows_materialized == 0
    assert verify_production_shadow_read_boundary(result)


def test_creator_tokens_qualified_spec_is_exact_and_non_authorizing():
    assert creator_tokens_cursor_only_high_water_spec() == HighWaterSpec(
        "creator", "creator_tokens", "rowid", None
    )


@pytest.mark.parametrize("invalid", [0, -1, True, None, "1"])
def test_query_deadline_must_be_explicit_positive_number(tmp_path, invalid):
    path, boundary, audit = _inputs(tmp_path)
    with pytest.raises(ProductionShadowHighWaterError, match="INVALID_QUERY_DEADLINE"):
        capture_production_shadow_read_boundary(
            boundary,
            audit,
            {"main": path},
            (HighWaterSpec("main", "records", "rowid", "event_at"),),
            captured_at_utc_ns=1,
            max_query_seconds=invalid,
        )


class _TrackingConnection:
    def __init__(self, connection, *, aggregate_failure=None):
        self.connection = connection
        self.aggregate_failure = aggregate_failure
        self.progress = None
        self.progress_calls = []
        self.statements = []
        self.closed = False

    def execute(self, sql):
        self.statements.append(sql)
        if sql.startswith("SELECT COALESCE(MAX") and self.aggregate_failure == "deadline":
            assert self.progress is not None
            while self.progress() == 0:
                pass
            raise sqlite3.OperationalError("interrupted")
        if sql.startswith("SELECT COALESCE(MAX") and self.aggregate_failure == "query":
            raise sqlite3.DatabaseError("fixture query failure")
        return self.connection.execute(sql)

    def set_progress_handler(self, callback, steps):
        self.progress = callback
        self.progress_calls.append((callback, steps))
        return self.connection.set_progress_handler(callback, steps)

    def close(self):
        self.closed = True
        return self.connection.close()


def _tracked_capture_inputs(tmp_path, monkeypatch, *, aggregate_failure=None):
    path, boundary, audit = _inputs(tmp_path)
    real_connect = high_water_module._connect
    tracked = []

    def connect(source_path):
        wrapper = _TrackingConnection(
            real_connect(source_path), aggregate_failure=aggregate_failure
        )
        tracked.append(wrapper)
        return wrapper

    monkeypatch.setattr(high_water_module, "_connect", connect)
    return path, boundary, audit, tracked


def _assert_cleaned_up(connection):
    assert "ROLLBACK" in connection.statements
    assert callable(connection.progress_calls[0][0])
    assert connection.progress_calls[-1] == (None, 0)
    assert connection.closed


def test_success_removes_progress_handler_rolls_back_and_closes(tmp_path, monkeypatch):
    path, boundary, audit, tracked = _tracked_capture_inputs(tmp_path, monkeypatch)
    capture_production_shadow_read_boundary(
        boundary,
        audit,
        {"main": path},
        (HighWaterSpec("main", "records", "rowid", "event_at"),),
        captured_at_utc_ns=1,
        max_query_seconds=1,
    )
    _assert_cleaned_up(tracked[0])


def test_deadline_is_named_and_cleans_up(tmp_path, monkeypatch):
    path, boundary, audit, tracked = _tracked_capture_inputs(
        tmp_path, monkeypatch, aggregate_failure="deadline"
    )
    readings = iter((0.0, 0.1, 0.6))
    with pytest.raises(ProductionShadowHighWaterError, match="QUERY_DEADLINE_EXCEEDED"):
        capture_production_shadow_read_boundary(
            boundary,
            audit,
            {"main": path},
            (HighWaterSpec("main", "records", "rowid", "event_at"),),
            captured_at_utc_ns=1,
            max_query_seconds=0.5,
            clock=lambda: next(readings),
        )
    _assert_cleaned_up(tracked[0])


def test_validation_failure_after_open_cleans_up(tmp_path, monkeypatch):
    path, boundary, audit, tracked = _tracked_capture_inputs(tmp_path, monkeypatch)
    with pytest.raises(ProductionShadowHighWaterError, match="INVALID_BOUNDARY_COLUMN"):
        capture_production_shadow_read_boundary(
            boundary,
            audit,
            {"main": path},
            (HighWaterSpec("main", "records", "rowid;invalid", None),),
            captured_at_utc_ns=1,
            max_query_seconds=1,
        )
    _assert_cleaned_up(tracked[0])


def test_query_exception_cleans_up_without_reclassification(tmp_path, monkeypatch):
    path, boundary, audit, tracked = _tracked_capture_inputs(
        tmp_path, monkeypatch, aggregate_failure="query"
    )
    with pytest.raises(sqlite3.DatabaseError, match="fixture query failure"):
        capture_production_shadow_read_boundary(
            boundary,
            audit,
            {"main": path},
            (HighWaterSpec("main", "records", "rowid", "event_at"),),
            captured_at_utc_ns=1,
            max_query_seconds=1,
        )
    _assert_cleaned_up(tracked[0])
