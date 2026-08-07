from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from .contracts import EvidenceRecord, canonical_json_bytes
from .primitives.contracts import PrimitiveObservation


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class EvidenceDatabase:
    """Database operations used exclusively by the single Evidence writer."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self.connection: sqlite3.Connection | None = None

    def open_writer(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.connection.execute(
            "INSERT OR IGNORE INTO evidence_schema_metadata(schema_version,installed_at) VALUES(1,?)",
            (int(self.clock()),),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO evidence_schema_metadata(schema_version,installed_at) VALUES(2,?)",
            (int(self.clock()),),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO evidence_schema_metadata(schema_version,installed_at) VALUES(3,?)",
            (int(self.clock()),),
        )

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def append_batch(self, messages: list[dict[str, Any]]) -> dict[str, int]:
        if self.connection is None:
            raise RuntimeError("Evidence database writer is not open")
        inserted = duplicates = 0
        now = int(self.clock())
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            for message in messages:
                message_id = str(message["message_id"])
                envelope = message["envelope"]
                receipt = conn.execute(
                    "SELECT 1 FROM writer_receipts WHERE message_id=?", (message_id,)
                ).fetchone()
                if receipt:
                    duplicates += 1
                    continue
                values = (
                    str(envelope["envelope_id"]), int(envelope["observed_at"]),
                    int(envelope["acquired_at"]), str(envelope["source"]),
                    str(envelope["source_version"]), str(envelope["provider"]),
                    str(envelope["evidence_digest"]), str(envelope["replay_version"]),
                    str(envelope["parser_version"]), str(envelope["payload_type"]),
                    str(envelope["artifact"]["digest"]), now,
                )
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO evidence_envelopes("
                    "envelope_id,observed_at,acquired_at,source,source_version,provider,"
                    "evidence_digest,replay_version,parser_version,payload_type,artifact_digest,appended_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", values,
                )
                if cursor.rowcount:
                    provenance = envelope["provenance"]
                    conn.execute(
                        "INSERT INTO evidence_provenance("
                        "envelope_id,provider_request_id,rpc_verification_state,acquisition_method,source_metadata_json"
                        ") VALUES(?,?,?,?,?)",
                        (values[0], provenance.get("provider_request_id"),
                         str(provenance["rpc_verification_state"]),
                         str(provenance["acquisition_method"]),
                         json.dumps(provenance.get("source_metadata", {}), sort_keys=True,
                                    separators=(",", ":"))),
                    )
                    artifact = envelope["artifact"]
                    conn.execute(
                        "INSERT INTO artifact_references("
                        "envelope_id,artifact_digest,size_bytes,compressed_bytes,content_type,compression"
                        ") VALUES(?,?,?,?,?,?)",
                        (values[0], str(artifact["digest"]), int(artifact["size_bytes"]),
                         int(artifact["compressed_bytes"]), str(artifact["content_type"]),
                         str(artifact.get("compression", "gzip"))),
                    )
                    inserted += 1
                else:
                    existing = conn.execute(
                        "SELECT envelope_id FROM evidence_envelopes WHERE evidence_digest=?",
                        (str(envelope["evidence_digest"]),),
                    ).fetchone()
                    if not existing or existing[0] != values[0]:
                        raise sqlite3.IntegrityError("Evidence digest belongs to a different envelope")
                    duplicates += 1
                conn.execute(
                    "INSERT INTO writer_receipts(message_id,envelope_id,committed_at) VALUES(?,?,?)",
                    (message_id, values[0], now),
                )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return {"inserted": inserted, "duplicates": duplicates}

    def get_normalization_status(self, envelope_id: str, parser_id: str,
                                 parser_version: str,
                                 fact_schema_version: str) -> sqlite3.Row | None:
        if self.connection is None:
            raise RuntimeError("Evidence database writer is not open")
        return self.connection.execute(
            "SELECT * FROM normalization_status WHERE envelope_id=? AND parser_id=? "
            "AND parser_version=? AND fact_schema_version=?",
            (envelope_id, parser_id, parser_version, fact_schema_version),
        ).fetchone()

    def set_normalization_status(self, *, envelope_id: str, parser_id: str,
                                 parser_version: str, fact_schema_version: str,
                                 state: str, representation: str,
                                 error: str | None = None, fact_count: int = 0,
                                 increment_attempt: bool = False) -> None:
        if self.connection is None:
            raise RuntimeError("Evidence database writer is not open")
        now = int(self.clock())
        self.connection.execute(
            "INSERT INTO normalization_status("
            "envelope_id,parser_id,parser_version,fact_schema_version,state,attempts,error,"
            "artifact_representation,started_at,completed_at,fact_count) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(envelope_id,parser_id,parser_version,fact_schema_version) DO UPDATE SET "
            "state=excluded.state, attempts=normalization_status.attempts+?, error=excluded.error, "
            "artifact_representation=excluded.artifact_representation, "
            "started_at=CASE WHEN excluded.state='RUNNING' THEN excluded.started_at ELSE normalization_status.started_at END, "
            "completed_at=CASE WHEN excluded.state IN ('COMPLETE','FAILED','UNSUPPORTED') THEN excluded.completed_at ELSE NULL END, "
            "fact_count=excluded.fact_count",
            (envelope_id, parser_id, parser_version, fact_schema_version, state,
             1 if increment_attempt else 0, error, representation,
             now if state == "RUNNING" else None,
             now if state in {"COMPLETE", "FAILED", "UNSUPPORTED"} else None,
             int(fact_count), 1 if increment_attempt else 0),
        )

    def append_normalized_records(self, *, envelope_id: str, parser_id: str,
                                  parser_version: str, fact_schema_version: str,
                                  representation: str,
                                  records: list[EvidenceRecord]) -> dict[str, int]:
        if self.connection is None:
            raise RuntimeError("Evidence database writer is not open")
        conn = self.connection
        inserted = duplicates = 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for record in records:
                payload_json = canonical_json_bytes(dict(record.payload)).decode("utf-8").rstrip("\n")
                values = (
                    record.evidence_id, record.logical_fact_id, record.fact_family,
                    record.fact_schema_version, record.chain, record.network,
                    record.natural_key, payload_json, record.payload_digest,
                    record.raw_artifact_digest, record.observed_at, record.acquired_at,
                    record.source_id, record.source_version, record.provider,
                    record.provider_request_id, record.parser_id, record.parser_version,
                    record.replay_version, record.verification_state,
                    record.provenance_quality, record.corrects_evidence_id,
                    record.created_at,
                )
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO normalized_evidence_records("
                    "evidence_id,logical_fact_id,fact_family,fact_schema_version,chain,network,"
                    "natural_key,payload_json,payload_digest,raw_artifact_digest,observed_at,"
                    "acquired_at,source_id,source_version,provider,provider_request_id,parser_id,"
                    "parser_version,replay_version,verification_state,provenance_quality,"
                    "corrects_evidence_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    existing = conn.execute(
                        "SELECT payload_digest,raw_artifact_digest,parser_id,parser_version "
                        "FROM normalized_evidence_records WHERE evidence_id=?",
                        (record.evidence_id,),
                    ).fetchone()
                    expected = (record.payload_digest, record.raw_artifact_digest,
                                record.parser_id, record.parser_version)
                    if existing is None or tuple(existing) != expected:
                        raise sqlite3.IntegrityError(
                            "Evidence identity collision with non-identical observation"
                        )
                    duplicates += 1
                provenance = record.provenance
                conn.execute(
                    "INSERT OR IGNORE INTO normalized_evidence_provenance("
                    "evidence_id,provider_request_id,endpoint_method,request_parameters_digest,"
                    "upstream_dependency,acquisition_path,cache_source,dependency_group,"
                    "parent_evidence_ids_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    (record.evidence_id, record.provider_request_id or record.source_id,
                     provenance.endpoint_method, provenance.request_parameters_digest,
                     provenance.upstream_dependency, provenance.acquisition_path,
                     provenance.cache_source, provenance.dependency_group,
                     json.dumps(provenance.parent_evidence_ids, separators=(",", ":"))),
                )
            now = int(self.clock())
            conn.execute(
                "UPDATE normalization_status SET state='COMPLETE', error=NULL, completed_at=?, "
                "fact_count=? WHERE envelope_id=? AND parser_id=? AND parser_version=? "
                "AND fact_schema_version=?",
                (now, len(records), envelope_id, parser_id, parser_version,
                 fact_schema_version),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return {"inserted": inserted, "duplicates": duplicates}

    def load_normalized_records(self) -> list[sqlite3.Row]:
        if self.connection is None:
            raise RuntimeError("Evidence database writer is not open")
        return self.connection.execute(
            "SELECT * FROM normalized_evidence_records ORDER BY fact_family,logical_fact_id,evidence_id"
        ).fetchall()

    def append_primitives(self, observations: list[PrimitiveObservation]) -> dict[str, int]:
        if self.connection is None:
            raise RuntimeError("Evidence database writer is not open")
        inserted = duplicates = 0
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            for item in observations:
                values = (
                    item.primitive_id, item.primitive_type, item.primitive_version,
                    canonical_json_bytes(list(item.subjects)).decode().rstrip("\n"),
                    canonical_json_bytes(dict(item.parameters)).decode().rstrip("\n"),
                    item.observation_window.start, item.observation_window.end,
                    canonical_json_bytes(dict(item.output_payload)).decode().rstrip("\n"),
                    item.output_digest, item.quality_state,
                    canonical_json_bytes(list(item.missing_inputs)).decode().rstrip("\n"),
                    item.failure_state, item.generated_at,
                )
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO primitive_observations("
                    "primitive_id,primitive_type,primitive_version,subjects_json,parameters_json,"
                    "window_start,window_end,output_payload_json,output_digest,quality_state,"
                    "missing_inputs_json,failure_state,generated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                if cursor.rowcount:
                    for evidence_id_value in item.evidence_ids:
                        conn.execute(
                            "INSERT INTO primitive_evidence_inputs(primitive_id,evidence_id) VALUES(?,?)",
                            (item.primitive_id, evidence_id_value),
                        )
                    inserted += 1
                else:
                    existing = conn.execute(
                        "SELECT primitive_type,primitive_version,subjects_json,parameters_json,"
                        "window_start,window_end,output_payload_json,output_digest,quality_state,"
                        "missing_inputs_json,failure_state "
                        "FROM primitive_observations WHERE primitive_id=?",
                        (item.primitive_id,),
                    ).fetchone()
                    expected = (
                        item.primitive_type, item.primitive_version, values[3], values[4],
                        item.observation_window.start, item.observation_window.end,
                        values[7], item.output_digest, item.quality_state, values[10],
                        item.failure_state,
                    )
                    if existing is None or tuple(existing) != expected:
                        raise sqlite3.IntegrityError(
                            "Primitive identity collision with non-identical observation"
                        )
                    duplicates += 1
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return {"inserted": inserted, "duplicates": duplicates}

    @staticmethod
    def read_health(path: Path) -> dict[str, Any]:
        path = Path(path)
        if not path.exists():
            return {"status": "NOT_INITIALIZED"}
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
            conn.execute("PRAGMA query_only=ON")
            quick = conn.execute("PRAGMA quick_check(1)").fetchone()[0]
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("evidence_envelopes", "evidence_provenance", "artifact_references", "writer_receipts",
                              "normalized_evidence_records", "normalized_evidence_provenance", "normalization_status",
                              "primitive_observations", "primitive_evidence_inputs")
            }
            conn.close()
            return {"status": "HEALTHY" if quick == "ok" else "DATABASE_DEGRADED",
                    "quick_check": quick, "counts": counts, "size_bytes": path.stat().st_size}
        except (sqlite3.Error, OSError) as exc:
            return {"status": "DATABASE_DEGRADED", "error": str(exc)}
