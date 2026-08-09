"""Compact-key provenance repository with external-ID contracts."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator


class CompactProvenanceRepository:
    """Keep compact keys private while exposing Primitive/Evidence identities."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def install(connection: sqlite3.Connection) -> None:
        connection.executescript("""
          CREATE TABLE IF NOT EXISTS primitive_identity(
            primitive_key INTEGER PRIMARY KEY, primitive_id TEXT NOT NULL UNIQUE);
          CREATE TABLE IF NOT EXISTS evidence_identity(
            evidence_key INTEGER PRIMARY KEY, evidence_id TEXT NOT NULL UNIQUE);
          CREATE TABLE IF NOT EXISTS compact_primitive_evidence_inputs(
            primitive_key INTEGER NOT NULL REFERENCES primitive_identity(primitive_key),
            evidence_key INTEGER NOT NULL REFERENCES evidence_identity(evidence_key),
            PRIMARY KEY(primitive_key,evidence_key)) WITHOUT ROWID;
          CREATE INDEX IF NOT EXISTS compact_inputs_by_evidence
            ON compact_primitive_evidence_inputs(evidence_key,primitive_key);
        """)

    def evidence_for_primitive(self, primitive_id: str) -> tuple[str, ...]:
        return tuple(row[0] for row in self.connection.execute("""
          SELECT e.evidence_id FROM primitive_identity p
          JOIN compact_primitive_evidence_inputs c USING(primitive_key)
          JOIN evidence_identity e USING(evidence_key)
          WHERE p.primitive_id=? ORDER BY e.evidence_id""", (primitive_id,)))

    def primitives_for_evidence(self, evidence_id: str) -> tuple[str, ...]:
        return tuple(row[0] for row in self.connection.execute("""
          SELECT p.primitive_id FROM evidence_identity e
          JOIN compact_primitive_evidence_inputs c USING(evidence_key)
          JOIN primitive_identity p USING(primitive_key)
          WHERE e.evidence_id=? ORDER BY p.primitive_id""", (evidence_id,)))

    def contains(self, primitive_id: str, evidence_id: str) -> bool:
        return self.connection.execute("""
          SELECT 1 FROM primitive_identity p
          JOIN compact_primitive_evidence_inputs c USING(primitive_key)
          JOIN evidence_identity e USING(evidence_key)
          WHERE p.primitive_id=? AND e.evidence_id=?""",
          (primitive_id, evidence_id)).fetchone() is not None

    def ordered_pairs(self) -> Iterator[tuple[str, str]]:
        # CROSS JOIN fixes the compact link table as the outer indexed scan.
        yield from self.connection.execute("""
          SELECT p.primitive_id,e.evidence_id
          FROM compact_primitive_evidence_inputs c
          CROSS JOIN primitive_identity p ON p.primitive_key=c.primitive_key
          CROSS JOIN evidence_identity e ON e.evidence_key=c.evidence_key
          ORDER BY c.primitive_key,c.evidence_key""")

    def append(self, pairs: Iterable[tuple[str, str]]) -> dict[str, int]:
        values = tuple(pairs)
        if not values:
            return {"inserted": 0, "duplicates": 0}
        connection = self.connection
        inserted = 0
        connection.execute("BEGIN IMMEDIATE")
        try:
            for primitive_id, evidence_id in values:
                connection.execute(
                    "INSERT OR IGNORE INTO primitive_identity(primitive_id) VALUES(?)",
                    (primitive_id,))
                connection.execute(
                    "INSERT OR IGNORE INTO evidence_identity(evidence_id) VALUES(?)",
                    (evidence_id,))
                cursor = connection.execute("""
                  INSERT OR IGNORE INTO compact_primitive_evidence_inputs(primitive_key,evidence_key)
                  SELECT p.primitive_key,e.evidence_key FROM primitive_identity p,evidence_identity e
                  WHERE p.primitive_id=? AND e.evidence_id=?""",
                  (primitive_id, evidence_id))
                inserted += cursor.rowcount
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return {"inserted": inserted, "duplicates": len(values) - inserted}

    def assert_integrity(self) -> None:
        missing = self.connection.execute("""
          SELECT COUNT(*) FROM compact_primitive_evidence_inputs c
          LEFT JOIN primitive_identity p USING(primitive_key)
          LEFT JOIN evidence_identity e USING(evidence_key)
          WHERE p.primitive_key IS NULL OR e.evidence_key IS NULL""").fetchone()[0]
        if missing:
            raise sqlite3.IntegrityError("compact provenance contains an unresolved identity")
