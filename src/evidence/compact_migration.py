"""Shadow-only compact provenance sidecar migration controls.

The canonical relation remains authoritative and intact.  This module models a
resumable build, canonical-transaction delta capture, guarded reader/writer
switch, and rollback without operating on production databases.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from src.evidence.compact_provenance import CompactProvenanceRepository


CAPTURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS compact_migration_delta(
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  primitive_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  UNIQUE(primitive_id,evidence_id)
);
CREATE TABLE IF NOT EXISTS compact_migration_control(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  reader_mode TEXT NOT NULL,
  writer_mode TEXT NOT NULL,
  sidecar_generation TEXT,
  authority_generation TEXT,
  switched_delta_sequence INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO compact_migration_control
VALUES(1,'CANONICAL','CANONICAL_WITH_DELTA',NULL,NULL,0);
CREATE TRIGGER IF NOT EXISTS compact_capture_after_insert
AFTER INSERT ON primitive_evidence_inputs BEGIN
  INSERT OR IGNORE INTO compact_migration_delta(primitive_id,evidence_id)
  VALUES(NEW.primitive_id,NEW.evidence_id);
END;
"""

SIDECAR_STATE = """
CREATE TABLE IF NOT EXISTS compact_migration_state(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  phase TEXT NOT NULL,
  source_high_water INTEGER NOT NULL,
  source_cursor INTEGER NOT NULL,
  applied_delta_sequence INTEGER NOT NULL,
  generation TEXT NOT NULL
);
"""


def relation_digest(pairs) -> str:
    digest = hashlib.sha256()
    for primitive_id, evidence_id in sorted(pairs):
        digest.update(primitive_id.encode()); digest.update(b"\0")
        digest.update(evidence_id.encode()); digest.update(b"\n")
    return digest.hexdigest()


class CompactMigrationSidecar:
    def __init__(self, canonical_path: Path, sidecar_path: Path) -> None:
        self.canonical_path, self.sidecar_path = Path(canonical_path), Path(sidecar_path)
        self.canonical = sqlite3.connect(self.canonical_path)
        self.sidecar = sqlite3.connect(self.sidecar_path)
        self.canonical.execute("PRAGMA foreign_keys=ON")
        self.sidecar.execute("PRAGMA foreign_keys=ON")
        CompactProvenanceRepository.install(self.sidecar)
        self.sidecar.executescript(SIDECAR_STATE)
        self.repository = CompactProvenanceRepository(self.sidecar)

    def close(self) -> None:
        self.canonical.close(); self.sidecar.close()

    def begin(self, generation: str) -> dict[str, object]:
        self.canonical.executescript(CAPTURE_SCHEMA)
        high_water = int(self.canonical.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM primitive_evidence_inputs").fetchone()[0])
        self.sidecar.execute("""INSERT OR IGNORE INTO compact_migration_state
          VALUES(1,'BUILDING',?,0,0,?)""", (high_water, generation))
        self.sidecar.commit(); self.canonical.commit()
        return self.state()

    def state(self) -> dict[str, object]:
        row = self.sidecar.execute("SELECT * FROM compact_migration_state WHERE singleton=1").fetchone()
        if row is None: return {"phase": "NOT_STARTED"}
        return {"phase": row[1], "source_high_water": row[2], "source_cursor": row[3],
                "applied_delta_sequence": row[4], "generation": row[5]}

    def build_batch(self, batch_size: int) -> dict[str, int]:
        state = self.state()
        rows = self.canonical.execute("""SELECT rowid,primitive_id,evidence_id
          FROM primitive_evidence_inputs WHERE rowid>? AND rowid<=?
          ORDER BY rowid LIMIT ?""", (state["source_cursor"], state["source_high_water"], batch_size)).fetchall()
        result = self.repository.append((row[1], row[2]) for row in rows)
        cursor = rows[-1][0] if rows else state["source_cursor"]
        phase = "BASE_COMPLETE" if cursor >= state["source_high_water"] else "BUILDING"
        self.sidecar.execute("UPDATE compact_migration_state SET source_cursor=?,phase=? WHERE singleton=1",
                             (cursor, phase)); self.sidecar.commit()
        return {**result, "cursor": int(cursor), "rows": len(rows)}

    def apply_deltas(self, batch_size: int = 10_000) -> dict[str, int]:
        applied = int(self.state()["applied_delta_sequence"]); inserted = duplicates = rows = 0
        while True:
            batch = self.canonical.execute("""SELECT sequence,primitive_id,evidence_id
              FROM compact_migration_delta WHERE sequence>? ORDER BY sequence LIMIT ?""",
              (applied, batch_size)).fetchall()
            if not batch: break
            result = self.repository.append((row[1], row[2]) for row in batch)
            inserted += result["inserted"]; duplicates += result["duplicates"]; rows += len(batch)
            applied = batch[-1][0]
            self.sidecar.execute("""UPDATE compact_migration_state
              SET applied_delta_sequence=? WHERE singleton=1""", (applied,)); self.sidecar.commit()
        return {"rows": rows, "inserted": inserted, "duplicates": duplicates,
                "applied_delta_sequence": applied}

    def validate(self) -> dict[str, object]:
        canonical_pairs = tuple(self.canonical.execute(
            "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY 1,2"))
        compact_pairs = tuple(sorted(self.repository.ordered_pairs()))
        canonical_set, compact_set = set(canonical_pairs), set(compact_pairs)
        canonical_digest, compact_digest = relation_digest(canonical_pairs), relation_digest(compact_pairs)
        return {"canonical_count": len(canonical_pairs), "compact_count": len(compact_pairs),
                "canonical_digest": canonical_digest, "compact_digest": compact_digest,
                "canonical_minus_compact": len(canonical_set-compact_set),
                "compact_minus_canonical": len(compact_set-canonical_set),
                "exact": (len(canonical_pairs) == len(compact_pairs) and
                          canonical_digest == compact_digest and canonical_set == compact_set)}

    def cutover(self, *, writer_paused: bool, authority_generation: str) -> dict[str, object]:
        if not writer_paused: raise RuntimeError("writer pause required for cutover")
        state = self.state()
        if state["phase"] != "BASE_COMPLETE": raise RuntimeError("base build incomplete")
        self.apply_deltas(); validation = self.validate()
        if not validation["exact"]: raise RuntimeError("compact relation is not equivalent")
        # Canonical control changes only after the sidecar is committed and exact.
        self.canonical.execute("BEGIN IMMEDIATE")
        try:
            final_sequence = int(self.canonical.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM compact_migration_delta").fetchone()[0])
            if final_sequence != int(self.state()["applied_delta_sequence"]):
                raise RuntimeError("delta arrived during cutover")
            self.canonical.execute("""UPDATE compact_migration_control SET
              reader_mode='COMPACT',writer_mode='DUAL_WITH_CANONICAL_ROLLBACK',
              sidecar_generation=?,authority_generation=?,switched_delta_sequence=?
              WHERE singleton=1""", (state["generation"], authority_generation, final_sequence))
            self.canonical.commit()
        except BaseException:
            self.canonical.rollback(); raise
        self.sidecar.execute("UPDATE compact_migration_state SET phase='CUTOVER' WHERE singleton=1")
        self.sidecar.commit()
        return {"control": self.control(), "validation": validation}

    def rollback(self) -> dict[str, object]:
        self.canonical.execute("BEGIN IMMEDIATE")
        self.canonical.execute("""UPDATE compact_migration_control SET
          reader_mode='CANONICAL',writer_mode='CANONICAL_WITH_DELTA' WHERE singleton=1""")
        self.canonical.commit()
        self.sidecar.execute("UPDATE compact_migration_state SET phase='ROLLED_BACK' WHERE singleton=1")
        self.sidecar.commit(); return self.control()

    def control(self) -> dict[str, object]:
        row = self.canonical.execute("SELECT * FROM compact_migration_control WHERE singleton=1").fetchone()
        return {"reader_mode": row[1], "writer_mode": row[2], "sidecar_generation": row[3],
                "authority_generation": row[4], "switched_delta_sequence": row[5]}
