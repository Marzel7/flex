#!/usr/bin/env python3
"""Isolated indexed Primitive authority store for OIP v2.2C.3 validation."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Sequence

from src.evidence.primitives.authority import FAMILY_CONTRACTS
from src.evidence.primitives.contracts import ObservationWindow, PrimitiveObservation


SCHEMA = """
CREATE TABLE IF NOT EXISTS authority_metadata(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS primitive_authority_events(
  event_id TEXT PRIMARY KEY,
  primitive_id TEXT NOT NULL,
  family TEXT NOT NULL,
  authority_group_id TEXT NOT NULL,
  authority_group_json TEXT NOT NULL,
  authority_state TEXT NOT NULL CHECK(authority_state IN
    ('AUTHORITATIVE','HISTORICAL_SNAPSHOT','LEGACY_VERSION')),
  current_primitive_id TEXT,
  reason TEXT NOT NULL,
  authority_contract_version TEXT NOT NULL,
  generator_semantic_version TEXT NOT NULL,
  transition_boundary INTEGER,
  recorded_at INTEGER NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS authority_events_by_primitive
  ON primitive_authority_events(primitive_id,event_id);
CREATE INDEX IF NOT EXISTS authority_events_by_state
  ON primitive_authority_events(authority_state,primitive_id);
CREATE INDEX IF NOT EXISTS authority_events_by_state_family
  ON primitive_authority_events(authority_state,family,primitive_id);
CREATE INDEX IF NOT EXISTS authority_events_by_group
  ON primitive_authority_events(authority_group_id,event_id);
CREATE TABLE IF NOT EXISTS current_primitive_authority(
  primitive_id TEXT PRIMARY KEY,
  family TEXT NOT NULL,
  authority_group_id TEXT NOT NULL,
  authority_group_json TEXT NOT NULL,
  authority_event_id TEXT NOT NULL UNIQUE
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS current_authority_by_family
  ON current_primitive_authority(family,primitive_id);
CREATE UNIQUE INDEX IF NOT EXISTS current_authority_one_per_group
  ON current_primitive_authority(authority_group_id);
CREATE VIEW IF NOT EXISTS indexed_current_primitive_authority AS
  SELECT primitive_id,family,authority_group_id,authority_group_json,event_id AS authority_event_id
  FROM primitive_authority_events WHERE authority_state='AUTHORITATIVE';
CREATE TABLE IF NOT EXISTS primitive_subject_index(
  subject TEXT NOT NULL,
  primitive_id TEXT NOT NULL,
  subject_order INTEGER NOT NULL,
  PRIMARY KEY(subject,primitive_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS primitive_subject_by_primitive
  ON primitive_subject_index(primitive_id,subject);
CREATE TABLE IF NOT EXISTS primitive_subject_cardinality(
  primitive_id TEXT PRIMARY KEY,
  subject_count INTEGER NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS subject_cardinality_by_count
  ON primitive_subject_cardinality(subject_count,primitive_id);
CREATE TABLE IF NOT EXISTS current_authority_subject(
  subject TEXT NOT NULL,
  primitive_id TEXT NOT NULL,
  PRIMARY KEY(subject,primitive_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS current_subject_by_primitive
  ON current_authority_subject(primitive_id,subject);
CREATE TRIGGER IF NOT EXISTS authority_events_no_update
BEFORE UPDATE ON primitive_authority_events BEGIN
  SELECT RAISE(ABORT,'immutable authority event');
END;
CREATE TRIGGER IF NOT EXISTS authority_events_no_delete
BEFORE DELETE ON primitive_authority_events BEGIN
  SELECT RAISE(ABORT,'immutable authority event');
END;
"""


def event_id(row: Sequence[object]) -> str:
    return hashlib.sha256(json.dumps(list(row), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class IndexedAuthorityStore:
    def __init__(self, path: Path, *, canonical: Path, compact: Path | None = None) -> None:
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        self.connection.execute("ATTACH DATABASE ? AS canonical", (str(canonical),))
        if compact is not None:
            self.connection.execute("ATTACH DATABASE ? AS compact", (str(compact),))

    def close(self) -> None:
        self.connection.close()

    def metadata(self, key: str, default: str | None = None) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM authority_metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_metadata(self, key: str, value: object) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO authority_metadata VALUES(?,?)", (key, str(value)))
        self.connection.commit()

    def import_projection(self, projection: Path) -> int:
        if self.metadata("authority_import_complete") == "1":
            self.refresh_current_projection()
            return self.connection.execute("SELECT COUNT(*) FROM primitive_authority_events").fetchone()[0]
        self.connection.execute("ATTACH DATABASE ? AS projection", (str(projection),))
        families = {row[0] for row in self.connection.execute("""
          SELECT DISTINCT p.primitive_type FROM projection.primitive_authority a
          JOIN canonical.primitive_observations p USING(primitive_id)""")}
        unknown = families - set(FAMILY_CONTRACTS)
        if unknown:
            self.connection.execute("DETACH DATABASE projection")
            raise ValueError(f"unregistered Primitive families: {sorted(unknown)}")
        expected = self.connection.execute(
            "SELECT COUNT(*) FROM projection.primitive_authority").fetchone()[0]
        existing = self.connection.execute(
            "SELECT COUNT(*) FROM primitive_authority_events").fetchone()[0]
        if existing != expected:
            high_water = (self.connection.execute(
                "SELECT MAX(primitive_id) FROM primitive_authority_events").fetchone()[0]
                if existing else None)
            missing_clause = "AND a.primitive_id>?" if high_water else ""
            rows = self.connection.execute(f"""
              SELECT a.primitive_id,p.primitive_type,a.authority_group_json,a.state,
                     COALESCE(a.superseded_by,a.primitive_id),a.reason,a.contract_version,
                     p.primitive_version,p.generated_at
              FROM projection.primitive_authority a
              JOIN canonical.primitive_observations p USING(primitive_id)
              WHERE 1=1 {missing_clause}
              ORDER BY a.primitive_id""", ((high_water,) if high_water else ()))
            batch = []
            for row in rows:
                body = tuple(row)
                group_id = hashlib.sha256(row[2].encode()).hexdigest()
                batch.append((event_id(body), row[0], row[1], group_id, row[2],
                              row[3], row[4], row[5], row[6], row[7], row[8], row[8]))
                if len(batch) == 10_000:
                    self.connection.executemany(
                        "INSERT OR IGNORE INTO primitive_authority_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                    self.connection.commit(); batch.clear()
            if batch:
                self.connection.executemany(
                    "INSERT OR IGNORE INTO primitive_authority_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", batch)
        self.refresh_current_projection()
        self.connection.execute("DETACH DATABASE projection")
        self.set_metadata("authority_import_complete", 1)
        return self.connection.execute("SELECT COUNT(*) FROM primitive_authority_events").fetchone()[0]

    def refresh_current_projection(self) -> int:
        self.connection.commit()
        return self.connection.execute(
            "SELECT COUNT(*) FROM indexed_current_primitive_authority").fetchone()[0]

    def build_subject_index(self, *, batch_size: int = 10_000) -> int:
        if self.metadata("subject_index_complete") == "1":
            return self.connection.execute(
                "SELECT COUNT(*) FROM primitive_subject_index").fetchone()[0]
        cursor = int(self.metadata("subject_index_cursor", "0") or 0)
        while True:
            rows = self.connection.execute("""
              SELECT rowid,primitive_id,subjects_json
              FROM canonical.primitive_observations WHERE rowid>?
              ORDER BY rowid LIMIT ?""", (cursor, batch_size)).fetchall()
            if not rows:
                break
            memberships = []
            cardinalities = []
            for row in rows:
                subjects = json.loads(row["subjects_json"])
                memberships.extend((subject, row["primitive_id"], order)
                                   for order, subject in enumerate(subjects))
                cardinalities.append((row["primitive_id"], len(subjects)))
            self.connection.executemany(
                "INSERT OR IGNORE INTO primitive_subject_index VALUES(?,?,?)", memberships)
            self.connection.executemany(
                "INSERT OR IGNORE INTO primitive_subject_cardinality VALUES(?,?)", cardinalities)
            cursor = rows[-1]["rowid"]
            self.connection.execute("INSERT OR REPLACE INTO authority_metadata VALUES('subject_index_cursor',?)",
                                    (str(cursor),))
            self.connection.commit()
        self.connection.execute("""INSERT OR IGNORE INTO current_authority_subject
          SELECT s.subject,s.primitive_id FROM primitive_subject_index s
          JOIN indexed_current_primitive_authority a USING(primitive_id)""")
        self.connection.commit()
        self.set_metadata("subject_index_complete", 1)
        return self.connection.execute("SELECT COUNT(*) FROM primitive_subject_index").fetchone()[0]

    def ids(self, mode: str = "CURRENT_AUTHORITATIVE", *, family: str | None = None,
            subjects: Sequence[str] = (), minimum_subjects: int = 0) -> tuple[str, ...]:
        if mode not in {"CURRENT_AUTHORITATIVE", "ALL_PERSISTED",
                        "HISTORICAL_SNAPSHOT", "LEGACY_VERSION"}:
            raise ValueError(f"unsupported authority mode: {mode}")
        parameters: list[object] = []
        if mode == "CURRENT_AUTHORITATIVE":
            source = "indexed_current_primitive_authority a"
            clauses = []
        elif mode == "ALL_PERSISTED":
            source = "canonical.primitive_observations a"
            clauses = []
        else:
            source = "primitive_authority_events a"
            clauses = ["a.authority_state=?"]
            parameters.append(mode)
        if family:
            clauses.append(("a.family=?" if mode != "ALL_PERSISTED" else "a.primitive_type=?"))
            parameters.append(family)
        if minimum_subjects:
            clauses.append("c.subject_count>=?"); parameters.append(minimum_subjects)
        joins = " JOIN primitive_subject_cardinality c USING(primitive_id)" if minimum_subjects else ""
        if subjects:
            self.connection.execute("DROP TABLE IF EXISTS temp.requested_subjects")
            self.connection.execute("CREATE TEMP TABLE requested_subjects(subject TEXT PRIMARY KEY) WITHOUT ROWID")
            self.connection.executemany("INSERT INTO requested_subjects VALUES(?)", ((x,) for x in set(subjects)))
            index_table = ("current_authority_subject" if mode == "CURRENT_AUTHORITATIVE"
                           else "primitive_subject_index")
            joins += f" JOIN {index_table} s USING(primitive_id) JOIN requested_subjects r USING(subject)"
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return tuple(row[0] for row in self.connection.execute(
            f"SELECT DISTINCT a.primitive_id FROM {source}{joins}{where} ORDER BY a.primitive_id", parameters))

    def load_primitives(self, mode: str, *, subjects: Sequence[str] = (),
                        minimum_subjects: int = 0, compact: bool = True) -> tuple[PrimitiveObservation, ...]:
        ids = self.ids(mode, subjects=subjects, minimum_subjects=minimum_subjects)
        self.connection.execute("DROP TABLE IF EXISTS temp.selected_primitives")
        self.connection.execute("CREATE TEMP TABLE selected_primitives(primitive_id TEXT PRIMARY KEY) WITHOUT ROWID")
        self.connection.executemany("INSERT INTO selected_primitives VALUES(?)", ((value,) for value in ids))
        refs: dict[str, list[str]] = {value: [] for value in ids}
        if compact:
            sql = """SELECT p.primitive_id,e.evidence_id FROM selected_primitives s
              JOIN compact.primitive_identity p USING(primitive_id)
              JOIN compact.compact_primitive_evidence_inputs i USING(primitive_key)
              JOIN compact.evidence_identity e USING(evidence_key)
              ORDER BY p.primitive_id,e.evidence_id"""
        else:
            sql = """SELECT i.primitive_id,i.evidence_id FROM selected_primitives s
              JOIN canonical.primitive_evidence_inputs i USING(primitive_id)
              ORDER BY i.primitive_id,i.evidence_id"""
        for primitive_id, evidence_id in self.connection.execute(sql):
            refs[primitive_id].append(evidence_id)
        rows = self.connection.execute("""SELECT p.* FROM selected_primitives s
          JOIN canonical.primitive_observations p USING(primitive_id) ORDER BY p.primitive_id""")
        return tuple(PrimitiveObservation(
            primitive_id=row["primitive_id"], primitive_type=row["primitive_type"],
            primitive_version=row["primitive_version"], evidence_ids=tuple(refs[row["primitive_id"]]),
            subjects=tuple(json.loads(row["subjects_json"])), parameters=json.loads(row["parameters_json"]),
            observation_window=ObservationWindow(row["window_start"], row["window_end"]),
            output_payload=json.loads(row["output_payload_json"]), output_digest=row["output_digest"],
            quality_state=row["quality_state"], missing_inputs=tuple(json.loads(row["missing_inputs_json"])),
            failure_state=row["failure_state"], generated_at=row["generated_at"]
        ) for row in rows)

    def explain(self, sql: str, parameters: Iterable[object] = ()) -> list[str]:
        return [row[3] for row in self.connection.execute("EXPLAIN QUERY PLAN " + sql, tuple(parameters))]

    def history(self, subject: str) -> tuple[sqlite3.Row, ...]:
        return tuple(self.connection.execute("""SELECT e.* FROM primitive_subject_index s
          JOIN primitive_authority_events e USING(primitive_id)
          WHERE s.subject=? ORDER BY e.transition_boundary,e.primitive_id""", (subject,)))

    def resolve_competing_events(self, authority_group_id: str) -> sqlite3.Row | None:
        return self.connection.execute("""SELECT * FROM primitive_authority_events
          WHERE authority_group_id=? AND authority_state='AUTHORITATIVE'
          ORDER BY transition_boundary DESC,event_id DESC LIMIT 1""",
          (authority_group_id,)).fetchone()

    def benchmark_ids(self, mode: str, **kwargs) -> dict[str, object]:
        started = time.perf_counter(); values = self.ids(mode, **kwargs)
        return {"count": len(values), "seconds": round(time.perf_counter()-started, 6)}
