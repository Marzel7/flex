"""The sole persistence boundary for canonical operator observations."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Iterable

from src.core.database_write_service import database_write_service, execute_script
from src.ops.operator_observation import OperatorObservation


OBSERVATION_DDL = """
CREATE TABLE IF NOT EXISTS operator_observations (
    observation_id   TEXT PRIMARY KEY,
    operator_id      TEXT NOT NULL REFERENCES operators(operator_id),
    observation_type TEXT NOT NULL,
    entity           TEXT,
    timestamp        INTEGER NOT NULL,
    source           TEXT NOT NULL,
    confidence       REAL NOT NULL,
    provenance       TEXT NOT NULL,
    metadata         TEXT NOT NULL,
    materialized_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_oo_operator_time
    ON operator_observations(operator_id, timestamp, observation_id);
CREATE INDEX IF NOT EXISTS ix_oo_operator_type
    ON operator_observations(operator_id, observation_type);

CREATE TABLE IF NOT EXISTS operator_observation_runs (
    operator_id       TEXT PRIMARY KEY REFERENCES operators(operator_id),
    status            TEXT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 0,
    started_at        INTEGER NOT NULL,
    completed_at      INTEGER,
    provider_counts   TEXT NOT NULL DEFAULT '{}',
    error             TEXT
);
"""


class ObservationStore:
    def __init__(self, db_path: str, *, write_service=None) -> None:
        self._path = db_path
        self._service = write_service or database_write_service
        self._database = f"operations:{os.path.realpath(db_path)}"
        self._service.register_database(self._database, db_path)

    def initialize_schema(self) -> None:
        self._service.submit(
            self._database, "operator-observation-schema",
            lambda conn: execute_script(conn, OBSERVATION_DDL),
        )

    def persist(self, operator_id: str, observations: Iterable[OperatorObservation],
                provider_counts: dict[str, int]) -> dict:
        rows = sorted(observations, key=lambda o: o.observation_id)
        started = int(time.time())

        def transaction(conn: sqlite3.Connection) -> dict:
            conn.execute(
                "INSERT INTO operator_observation_runs"
                "(operator_id,status,observation_count,started_at,completed_at,provider_counts,error) "
                "VALUES(?, 'MATERIALIZING', 0, ?, NULL, '{}', NULL) "
                "ON CONFLICT(operator_id) DO UPDATE SET status='MATERIALIZING',"
                "started_at=excluded.started_at,completed_at=NULL,error=NULL",
                (operator_id, started),
            )
            for observation in rows:
                conn.execute(
                    "INSERT INTO operator_observations"
                    "(observation_id,operator_id,observation_type,entity,timestamp,source,"
                    "confidence,provenance,metadata,materialized_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(observation_id) DO UPDATE SET "
                    "confidence=excluded.confidence,provenance=excluded.provenance,"
                    "metadata=excluded.metadata,materialized_at=excluded.materialized_at",
                    (observation.observation_id, observation.operator_id,
                     observation.observation_type, observation.entity,
                     observation.timestamp, observation.source, observation.confidence,
                     json.dumps(observation.provenance, sort_keys=True, default=str),
                    json.dumps(observation.metadata, sort_keys=True, default=str), started),
                )
            if rows:
                ids = [observation.observation_id for observation in rows]
                conn.execute(
                    f"DELETE FROM operator_observations WHERE operator_id=? AND "
                    f"observation_id NOT IN ({','.join('?' for _ in ids)})",
                    [operator_id, *ids],
                )
            else:
                conn.execute(
                    "DELETE FROM operator_observations WHERE operator_id=?", (operator_id,)
                )
            total = conn.execute(
                "SELECT COUNT(*) FROM operator_observations WHERE operator_id=?", (operator_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE operator_observation_runs SET status='READY',observation_count=?,"
                "completed_at=?,provider_counts=?,error=NULL WHERE operator_id=?",
                (total, int(time.time()), json.dumps(provider_counts, sort_keys=True), operator_id),
            )
            return {"operator_id": operator_id, "status": "READY",
                    "observation_count": total, "provider_counts": provider_counts}

        return self._service.submit(
            self._database, "operator-observation-materialize", transaction
        )

    def fetch(self, operator_id: str, observation_types: set[str] | None = None) -> list[OperatorObservation]:
        try:
            conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            try:
                sql = "SELECT * FROM operator_observations WHERE operator_id=?"
                params: list = [operator_id]
                if observation_types:
                    kinds = sorted(observation_types)
                    sql += f" AND observation_type IN ({','.join('?' for _ in kinds)})"
                    params.extend(kinds)
                sql += " ORDER BY timestamp,observation_id"
                rows = conn.execute(sql, params).fetchall()
                return [OperatorObservation(
                    observation_id=row["observation_id"], operator_id=row["operator_id"],
                    observation_type=row["observation_type"], entity=row["entity"],
                    timestamp=row["timestamp"], source=row["source"],
                    confidence=row["confidence"], provenance=json.loads(row["provenance"]),
                    metadata=json.loads(row["metadata"]),
                ) for row in rows]
            finally:
                conn.close()
        except (sqlite3.Error, OSError, json.JSONDecodeError):
            return []

    def status(self, operator_id: str) -> dict:
        try:
            conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            try:
                row = conn.execute(
                    "SELECT * FROM operator_observation_runs WHERE operator_id=?", (operator_id,)
                ).fetchone()
                if not row:
                    return {"operator_id": operator_id, "status": "IDENTITY_CONFIRMED",
                            "observation_count": 0, "provider_counts": {}}
                result = dict(row)
                result["provider_counts"] = json.loads(result["provider_counts"] or "{}")
                return result
            finally:
                conn.close()
        except (sqlite3.Error, OSError, json.JSONDecodeError):
            return {"operator_id": operator_id, "status": "IDENTITY_CONFIRMED",
                    "observation_count": 0, "provider_counts": {}}
