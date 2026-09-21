"""Offline startup-schema contract for the qualified Monitor service entrypoint."""

import sqlite3

import pytest

from src.ops.operation_monitor_service import validate_monitor_schema


def test_monitor_schema_validation_is_read_only(tmp_path):
    db = tmp_path / "ops.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE operation_monitor_facts (id INTEGER)")
        conn.execute("CREATE TABLE operation_monitor_observations (id INTEGER)")
    validate_monitor_schema(str(db))


def test_monitor_schema_validation_fails_closed(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    with pytest.raises(RuntimeError, match="MONITOR_SCHEMA_NOT_PROVISIONED"):
        validate_monitor_schema(str(db))
