"""Isolated append-only persistence for Operation runtime contracts and outputs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..contracts import canonical_json_bytes
from .formalization import contract_digest


SCHEMA_PATH = Path(__file__).with_name("runtime_schema.sql")
OUTPUT_TABLES = {
    "BehaviourObservation": ("behaviour_observations", "observation_id", "module_version"),
    "TopologyRevision": ("topology_revisions", "revision_id", "topology_version"),
    "DetectorInput": ("detector_inputs", "input_id", "detector_version"),
    "DetectorResult": ("detector_results", "result_id", "detector_version"),
    "LifecycleRecommendation": ("lifecycle_recommendations", "recommendation_id", "1"),
}


class OperationRuntimeStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.connection: sqlite3.Connection | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _conn(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Operation runtime store is not open")
        return self.connection

    def append_contract(self, contract: Mapping[str, Any], *, registered_at: int) -> bool:
        conn = self._conn()
        payload = canonical_json_bytes(dict(contract)).decode().rstrip("\n")
        digest = contract_digest(contract)
        cursor = conn.execute(
            "INSERT OR IGNORE INTO operation_contract_versions VALUES(?,?,?,?,?)",
            (contract["contract_id"], contract["contract_version"], digest, payload, registered_at),
        )
        if cursor.rowcount:
            return True
        existing = conn.execute(
            "SELECT contract_digest,payload_json FROM operation_contract_versions "
            "WHERE contract_id=? AND contract_version=?",
            (contract["contract_id"], contract["contract_version"]),
        ).fetchone()
        if existing is None or tuple(existing) != (digest, payload):
            raise sqlite3.IntegrityError("Operation Contract identity collision")
        return False

    def append_activation_event(self, *, contract_id: str, contract_version: str,
                                from_state: str | None, to_state: str, reason: str,
                                occurred_at: int) -> str:
        conn = self._conn()
        body = [contract_id, contract_version, from_state, to_state, reason, occurred_at]
        event_id = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        payload_digest = hashlib.sha256(canonical_json_bytes(body[:-1])).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO operation_contract_activation_events VALUES(?,?,?,?,?,?,?,?)",
            (event_id, contract_id, contract_version, from_state, to_state, reason,
             occurred_at, payload_digest),
        )
        return event_id

    def append_outputs(self, outputs: Iterable[Any]) -> dict[str, int]:
        conn = self._conn()
        inserted = duplicates = 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for output in outputs:
                value = output.to_dict()
                output_type = value["output_type"]
                table, id_key, producer_key = OUTPUT_TABLES[output_type]
                output_id = value[id_key]
                payload = canonical_json_bytes(value).decode().rstrip("\n")
                digest = hashlib.sha256(payload.encode()).hexdigest()
                producer_version = producer_key if producer_key == "1" else value[producer_key]
                generated_at = int(value.get("generated_at", 0))
                cursor = conn.execute(
                    f"INSERT OR IGNORE INTO {table} VALUES(?,?,?,?,?,?,?,?)",
                    (output_id, value["contract_id"], value["contract_version"],
                     producer_version, value["input_digest"], payload, digest, generated_at),
                )
                if cursor.rowcount:
                    inserted += 1
                    for ref_type, refs in self._references(value):
                        for ref in refs:
                            conn.execute(
                                "INSERT INTO operation_runtime_references VALUES(?,?,?,?)",
                                (output_type, output_id, ref_type, ref),
                            )
                else:
                    row = conn.execute(
                        f"SELECT payload_digest FROM {table} WHERE output_id=?", (output_id,)
                    ).fetchone()
                    if row is None or row[0] != digest:
                        raise sqlite3.IntegrityError("runtime output identity collision")
                    duplicates += 1
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return {"inserted": inserted, "duplicates": duplicates}

    @staticmethod
    def _references(value: Mapping[str, Any]) -> tuple[tuple[str, tuple[str, ...]], ...]:
        keys = ("evidence_refs", "primitive_refs", "supporting_evidence_ids",
                "contradictory_evidence_ids", "behaviour_observation_refs")
        result = [(key, tuple(value.get(key) or ())) for key in keys]
        for key in ("topology_revision_ref", "detector_result_ref"):
            if value.get(key):
                result.append((key, (str(value[key]),)))
        return tuple(result)

    def count(self, table: str) -> int:
        if table not in {item[0] for item in OUTPUT_TABLES.values()} | {
            "operation_contract_versions", "operation_contract_activation_events",
            "operation_runtime_references",
        }:
            raise ValueError("unsupported runtime table")
        return int(self._conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
