from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable


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
                for table in ("evidence_envelopes", "evidence_provenance", "artifact_references", "writer_receipts")
            }
            conn.close()
            return {"status": "HEALTHY" if quick == "ok" else "DATABASE_DEGRADED",
                    "quick_check": quick, "counts": counts, "size_bytes": path.stat().st_size}
        except (sqlite3.Error, OSError) as exc:
            return {"status": "DATABASE_DEGRADED", "error": str(exc)}
