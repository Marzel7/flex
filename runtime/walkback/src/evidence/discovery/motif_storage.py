"""Append-only persistence for canonical operation motifs and occurrences."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Sequence

from ..contracts import canonical_json_bytes
from .motifs import OperationMotif


SCHEMA_PATH = Path(__file__).with_name("motif_schema.sql")


class MotifStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path); self.connection: sqlite3.Connection | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(SCHEMA_PATH.read_text())

    def close(self) -> None:
        if self.connection is not None: self.connection.close(); self.connection = None

    def _conn(self) -> sqlite3.Connection:
        if self.connection is None: raise RuntimeError("Motif store is not open")
        return self.connection

    def append(self, motifs: Sequence[OperationMotif]) -> dict[str, int]:
        inserted = duplicates = occurrences = 0; connection = self._conn()
        connection.execute("BEGIN IMMEDIATE")
        try:
            for motif in motifs:
                graph = canonical_json_bytes(motif.canonical_graph).decode().rstrip("\n")
                graph_digest = hashlib.sha256(graph.encode()).hexdigest()
                definition_digest = hashlib.sha256(canonical_json_bytes([
                    motif.motif_id, motif.canonicalization_version,
                    motif.replay_version, graph_digest,
                ])).hexdigest()
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO operation_motifs VALUES(?,?,?,?,?,?)",
                    (motif.motif_id, motif.canonicalization_version, motif.replay_version,
                     graph, graph_digest, definition_digest),
                )
                if cursor.rowcount: inserted += 1
                else:
                    row = connection.execute(
                        "SELECT definition_digest FROM operation_motifs WHERE motif_id=?",
                        (motif.motif_id,),
                    ).fetchone()
                    if row is None or row[0] != definition_digest:
                        raise sqlite3.IntegrityError("operation motif identity collision")
                    duplicates += 1
                for occurrence in motif.occurrences:
                    occurrence_payload = canonical_json_bytes(occurrence.to_dict()).decode().rstrip("\n")
                    occurrence_digest = hashlib.sha256(occurrence_payload.encode()).hexdigest()
                    occurrence_cursor = connection.execute(
                        "INSERT OR IGNORE INTO motif_occurrences VALUES(?,?,?,?,?)",
                        (occurrence.occurrence_id, motif.motif_id, occurrence.candidate_id,
                         occurrence_payload, occurrence_digest),
                    )
                    if occurrence_cursor.rowcount: occurrences += 1
                    else:
                        row = connection.execute(
                            "SELECT payload_digest FROM motif_occurrences WHERE occurrence_id=?",
                            (occurrence.occurrence_id,),
                        ).fetchone()
                        if row is None or row[0] != occurrence_digest:
                            raise sqlite3.IntegrityError("motif occurrence identity collision")
                for reference_type, values in (
                    ("Candidate", motif.supporting_candidate_ids),
                    ("Evidence", motif.supporting_evidence_ids),
                    ("Primitive", motif.supporting_primitive_ids),
                ):
                    for reference in values:
                        connection.execute("INSERT OR IGNORE INTO motif_references VALUES(?,?,?)",
                                           (motif.motif_id, reference_type, reference))
            connection.commit()
        except BaseException:
            connection.rollback(); raise
        return {"inserted": inserted, "duplicates": duplicates,
                "occurrences_inserted": occurrences}

    def health(self) -> dict[str, object]:
        connection = self._conn()
        motif_count = int(connection.execute("SELECT COUNT(*) FROM operation_motifs").fetchone()[0])
        candidate_count = int(connection.execute("SELECT COUNT(*) FROM motif_occurrences").fetchone()[0])
        largest = int(connection.execute(
            "SELECT COALESCE(MAX(value),0) FROM (SELECT COUNT(*) AS value "
            "FROM motif_occurrences GROUP BY motif_id)"
        ).fetchone()[0])
        singleton_count = int(connection.execute(
            "SELECT COUNT(*) FROM (SELECT motif_id FROM motif_occurrences "
            "GROUP BY motif_id HAVING COUNT(*)=1)"
        ).fetchone()[0])
        return {
            "status": "HEALTHY", "motif_count": motif_count,
            "candidate_count": candidate_count,
            "compression_ratio": candidate_count / motif_count if motif_count else 0.0,
            "largest_motif": largest,
            "singleton_rate": singleton_count / motif_count if motif_count else 0.0,
            "authoritative": False,
        }
