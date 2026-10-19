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
          CREATE TABLE IF NOT EXISTS compact_relation_counter(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            relation_count INTEGER NOT NULL CHECK(relation_count>=0));
          INSERT OR IGNORE INTO compact_relation_counter VALUES(1,0);
        """)

    def relation_count(self) -> int:
        return int(self.connection.execute(
            "SELECT relation_count FROM compact_relation_counter WHERE singleton=1").fetchone()[0])

    def reset_relation_count(self, relation_count: int) -> None:
        self.connection.execute(
            "UPDATE compact_relation_counter SET relation_count=? WHERE singleton=1",
            (relation_count,))
        self.connection.commit()

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

    def evidence_for_primitives(self, primitive_ids: Iterable[str]) -> dict[str, tuple[str, ...]]:
        """Resolve a bounded Primitive population through integer keys first.

        This avoids the compatibility view's repeated external-ID resolution and
        global temporary ORDER BY. External Evidence ordering remains explicit.
        """
        values = tuple(sorted(set(primitive_ids)))
        result: dict[str, list[str]] = {value: [] for value in values}
        self.connection.execute("DROP TABLE IF EXISTS temp.requested_primitive_keys")
        self.connection.execute("""CREATE TEMP TABLE requested_primitive_keys(
          primitive_id TEXT PRIMARY KEY, primitive_key INTEGER UNIQUE) WITHOUT ROWID""")
        self.connection.executemany(
            "INSERT INTO requested_primitive_keys(primitive_id) VALUES(?)", ((value,) for value in values))
        # Resolve external IDs once, then drive the relation scan from compact keys.
        self.connection.execute("""UPDATE requested_primitive_keys SET primitive_key=(
          SELECT primitive_key FROM primitive_identity p
          WHERE p.primitive_id=requested_primitive_keys.primitive_id)""")
        for primitive_id, evidence_id in self.connection.execute("""
          SELECT s.primitive_id,e.evidence_id
          FROM requested_primitive_keys s
          CROSS JOIN compact_primitive_evidence_inputs c
            ON c.primitive_key=s.primitive_key
          JOIN evidence_identity e ON e.evidence_key=c.evidence_key"""):
            result[primitive_id].append(evidence_id)
        return {key: tuple(sorted(items)) for key, items in result.items()}

    def primitives_for_evidences(self, evidence_ids: Iterable[str]) -> dict[str, tuple[str, ...]]:
        values = tuple(sorted(set(evidence_ids)))
        result: dict[str, list[str]] = {value: [] for value in values}
        self.connection.execute("DROP TABLE IF EXISTS temp.requested_evidence_keys")
        self.connection.execute("""CREATE TEMP TABLE requested_evidence_keys(
          evidence_id TEXT PRIMARY KEY, evidence_key INTEGER UNIQUE) WITHOUT ROWID""")
        self.connection.executemany(
            "INSERT INTO requested_evidence_keys(evidence_id) VALUES(?)", ((value,) for value in values))
        self.connection.execute("""UPDATE requested_evidence_keys SET evidence_key=(
          SELECT evidence_key FROM evidence_identity e
          WHERE e.evidence_id=requested_evidence_keys.evidence_id)""")
        for evidence_id, primitive_id in self.connection.execute("""
          SELECT s.evidence_id,p.primitive_id
          FROM requested_evidence_keys s
          CROSS JOIN compact_primitive_evidence_inputs c
            ON c.evidence_key=s.evidence_key
          JOIN primitive_identity p ON p.primitive_key=c.primitive_key"""):
            result[evidence_id].append(primitive_id)
        return {key: tuple(sorted(items)) for key, items in result.items()}

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
            self.connection.execute("""UPDATE compact_relation_counter
              SET relation_count=relation_count+? WHERE singleton=1""", (inserted,))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return {"inserted": inserted, "duplicates": len(values) - inserted}

    def append_bulk(self, pairs: Iterable[tuple[str, str]]) -> dict[str, int]:
        """Append a migration-sized batch without per-relation SQL round trips."""
        values = tuple(pairs)
        if not values:
            return {"inserted": 0, "duplicates": 0}
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DROP TABLE IF EXISTS temp.compact_input_batch")
            connection.execute("""CREATE TEMP TABLE compact_input_batch(
              primitive_id TEXT NOT NULL,evidence_id TEXT NOT NULL,
              PRIMARY KEY(primitive_id,evidence_id)) WITHOUT ROWID""")
            connection.executemany("INSERT OR IGNORE INTO compact_input_batch VALUES(?,?)", values)
            unique_count = int(connection.execute(
                "SELECT COUNT(*) FROM compact_input_batch").fetchone()[0])
            connection.execute("""INSERT OR IGNORE INTO primitive_identity(primitive_id)
              SELECT DISTINCT primitive_id FROM compact_input_batch""")
            connection.execute("""INSERT OR IGNORE INTO evidence_identity(evidence_id)
              SELECT DISTINCT evidence_id FROM compact_input_batch""")
            relation_insert = connection.execute("""INSERT OR IGNORE INTO compact_primitive_evidence_inputs
              SELECT p.primitive_key,e.evidence_key FROM compact_input_batch b
              JOIN primitive_identity p USING(primitive_id)
              JOIN evidence_identity e USING(evidence_id)""")
            inserted = relation_insert.rowcount
            connection.execute("""UPDATE compact_relation_counter
              SET relation_count=relation_count+? WHERE singleton=1""", (inserted,))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return {"inserted": inserted, "duplicates": len(values) - inserted,
                "input_duplicates": len(values) - unique_count}

    def assert_integrity(self) -> None:
        missing = self.connection.execute("""
          SELECT COUNT(*) FROM compact_primitive_evidence_inputs c
          LEFT JOIN primitive_identity p USING(primitive_key)
          LEFT JOIN evidence_identity e USING(evidence_key)
          WHERE p.primitive_key IS NULL OR e.evidence_key IS NULL""").fetchone()[0]
        if missing:
            raise sqlite3.IntegrityError("compact provenance contains an unresolved identity")
