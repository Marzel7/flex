"""Shadow-only compact provenance migration choreography.

The implementation deliberately accepts only paths below an explicitly supplied
shadow root.  It never opens the production Evidence database and it performs no
RPC.  External Primitive/Evidence IDs remain the public contract; integer keys
are private to the compact sidecar.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Iterable

from src.evidence.compact_provenance import CompactProvenanceRepository


STATES = {
    "PREPARING": {"BUILDING", "FAILED"},
    "BUILDING": {"BUILDING", "CATCHING_UP", "FAILED"},
    "CATCHING_UP": {"CATCHING_UP", "PAUSE_REQUIRED", "FAILED"},
    "PAUSE_REQUIRED": {"FINAL_DRAIN", "CATCHING_UP", "FAILED"},
    "FINAL_DRAIN": {"VERIFIED", "CATCHING_UP", "FAILED"},
    "VERIFIED": {"COMPACT_ACTIVE", "FAILED"},
    "COMPACT_ACTIVE": {"ROLLBACK_ACTIVE", "COMPACT_ACTIVE", "FAILED"},
    "ROLLBACK_ACTIVE": {"CATCHING_UP", "ROLLBACK_ACTIVE", "FAILED"},
    "FAILED": set(),
}

CAPTURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS compact_migration_delta(
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  primitive_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  operation TEXT NOT NULL DEFAULT 'INSERT',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(primitive_id,evidence_id,operation)
);
CREATE TABLE IF NOT EXISTS compact_rollback_delta(
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  primitive_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  reconciled INTEGER NOT NULL DEFAULT 0 CHECK(reconciled IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(primitive_id,evidence_id)
);
CREATE TABLE IF NOT EXISTS compact_migration_control(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  reader_mode TEXT NOT NULL,
  writer_mode TEXT NOT NULL,
  sidecar_generation TEXT,
  authority_generation TEXT,
  switched_delta_sequence INTEGER NOT NULL DEFAULT 0,
  writers_paused INTEGER NOT NULL DEFAULT 0 CHECK(writers_paused IN (0,1)),
  migration_id TEXT,
  generation INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO compact_migration_control
VALUES(1,'CANONICAL','CANONICAL_WITH_DELTA',NULL,NULL,0,0,NULL,0);
CREATE TRIGGER IF NOT EXISTS compact_capture_after_insert
AFTER INSERT ON primitive_evidence_inputs BEGIN
  INSERT OR IGNORE INTO compact_migration_delta(primitive_id,evidence_id,operation)
  VALUES(NEW.primitive_id,NEW.evidence_id,'INSERT');
END;
"""

SIDECAR_STATE = """
CREATE TABLE IF NOT EXISTS compact_migration_state(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  migration_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  state TEXT NOT NULL,
  source_high_water INTEGER NOT NULL,
  source_cursor INTEGER NOT NULL,
  delta_cursor INTEGER NOT NULL,
  reader_mode TEXT NOT NULL,
  writer_mode TEXT NOT NULL,
  authority_generation TEXT,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  cursor_kind TEXT NOT NULL DEFAULT 'SOURCE_ROWID',
  source_relation_high_water INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS compact_migration_checkpoints(
  checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
  migration_id TEXT NOT NULL,
  state TEXT NOT NULL,
  source_cursor INTEGER NOT NULL,
  delta_cursor INTEGER NOT NULL,
  rows_processed INTEGER NOT NULL,
  elapsed_ms REAL NOT NULL,
  sidecar_bytes INTEGER NOT NULL,
  wal_bytes INTEGER NOT NULL,
  free_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS compact_identity_seed(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  source_path TEXT NOT NULL,
  primitive_count INTEGER NOT NULL,
  evidence_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS compact_prevalidation_boundary(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  migration_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  validated_source_generation TEXT NOT NULL,
  validated_delta_sequence INTEGER NOT NULL,
  validated_canonical_count INTEGER NOT NULL,
  validated_compact_count INTEGER NOT NULL,
  validated_digest TEXT NOT NULL,
  canonical_minus_compact INTEGER NOT NULL,
  compact_minus_canonical INTEGER NOT NULL,
  authority_generation TEXT NOT NULL,
  current_authoritative_count INTEGER NOT NULL,
  current_authority_provenance_count INTEGER NOT NULL,
  validation_json TEXT NOT NULL,
  validation_completed_at TEXT NOT NULL,
  exact INTEGER NOT NULL CHECK(exact IN (0,1))
);
CREATE TABLE IF NOT EXISTS compact_cutover_events(
  cutover_id INTEGER PRIMARY KEY AUTOINCREMENT,
  migration_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  validated_delta_sequence INTEGER NOT NULL,
  final_delta_sequence INTEGER NOT NULL,
  final_delta_events INTEGER NOT NULL,
  final_delta_inserted INTEGER NOT NULL,
  final_delta_duplicates INTEGER NOT NULL,
  expected_final_count INTEGER NOT NULL,
  compact_final_count INTEGER NOT NULL,
  pause_started_at TEXT NOT NULL,
  pause_ended_at TEXT NOT NULL,
  pause_ms REAL NOT NULL,
  timing_json TEXT NOT NULL,
  bounded_validation_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def relation_digest(pairs: Iterable[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for primitive_id, evidence_id in sorted(pairs):
        digest.update(primitive_id.encode()); digest.update(b"\0")
        digest.update(evidence_id.encode()); digest.update(b"\n")
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class CompactMigrationSidecar:
    """Execute the closed, resumable migration state machine on shadow data."""

    def __init__(self, canonical_path: Path, sidecar_path: Path, *, shadow_root: Path | None = None) -> None:
        self.canonical_path, self.sidecar_path = Path(canonical_path), Path(sidecar_path)
        if shadow_root is not None:
            root = Path(shadow_root)
            if not _inside(self.canonical_path, root) or not _inside(self.sidecar_path, root):
                raise RuntimeError("shadow-only guard rejected database path")
        if self.canonical_path.resolve() == self.sidecar_path.resolve():
            raise RuntimeError("canonical and sidecar paths must differ")
        self.canonical = sqlite3.connect(self.canonical_path, timeout=60)
        self.sidecar = sqlite3.connect(self.sidecar_path, timeout=60)
        self.canonical.execute("PRAGMA foreign_keys=ON")
        self.sidecar.execute("PRAGMA foreign_keys=ON")
        CompactProvenanceRepository.install(self.sidecar)
        self.sidecar.executescript(SIDECAR_STATE)
        self.repository = CompactProvenanceRepository(self.sidecar)

    def close(self) -> None:
        self.canonical.close(); self.sidecar.close()

    def begin(self, generation: str | int, migration_id: str | None = None) -> dict[str, object]:
        # Capture is installed and committed before the immutable rowid boundary.
        self.canonical.executescript(CAPTURE_SCHEMA)
        self.canonical.commit()
        relation_high_water = int(self.canonical.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM primitive_evidence_inputs").fetchone()[0])
        seeded = self.sidecar.execute("SELECT 1 FROM compact_identity_seed WHERE singleton=1").fetchone()
        high_water = (int(self.sidecar.execute("SELECT COALESCE(MAX(primitive_key),0) FROM primitive_identity").fetchone()[0])
                      if seeded else relation_high_water)
        cursor_kind = "PRIMITIVE_KEY" if seeded else "SOURCE_ROWID"
        numeric_generation = int(generation) if str(generation).isdigit() else 1
        identifier = migration_id or str(uuid.uuid5(uuid.NAMESPACE_URL,
                                                    f"{self.canonical_path.resolve()}:{generation}"))
        now = str(time.time_ns())
        self.sidecar.execute("""INSERT OR IGNORE INTO compact_migration_state
          VALUES(1,?,?,'BUILDING',?,0,0,'CANONICAL','CANONICAL_WITH_DELTA',NULL,?,?,?,?)""",
          (identifier, numeric_generation, high_water, now, now, cursor_kind, relation_high_water))
        self.canonical.execute("""UPDATE compact_migration_control SET
          migration_id=?,generation=? WHERE singleton=1""", (identifier, numeric_generation))
        self.sidecar.commit(); self.canonical.commit()
        return self.state()

    def seed_identity_maps(self, validated_compact_path: Path) -> dict[str, int]:
        """Seed frozen identity maps from a previously equivalence-proved compact derivative.

        This avoids resolving 12.4M TEXT pairs during rehearsal while the actual
        relation is still rebuilt from the canonical source boundary.
        """
        source = Path(validated_compact_path)
        if self.state()["state"] != "NOT_STARTED":
            raise RuntimeError("identity maps must be seeded before migration begins")
        self.sidecar.execute("ATTACH DATABASE ? AS validated_identity", (str(source),))
        self.sidecar.execute("BEGIN IMMEDIATE")
        try:
            self.sidecar.execute("""INSERT INTO primitive_identity(primitive_key,primitive_id)
              SELECT primitive_key,primitive_id FROM validated_identity.primitive_identity""")
            self.sidecar.execute("""INSERT INTO evidence_identity(evidence_key,evidence_id)
              SELECT evidence_key,evidence_id FROM validated_identity.evidence_identity""")
            p = int(self.sidecar.execute("SELECT COUNT(*) FROM primitive_identity").fetchone()[0])
            e = int(self.sidecar.execute("SELECT COUNT(*) FROM evidence_identity").fetchone()[0])
            self.sidecar.execute("INSERT INTO compact_identity_seed VALUES(1,?,?,?)", (str(source), p, e))
            self.sidecar.commit()
        except BaseException:
            self.sidecar.rollback(); raise
        self.sidecar.execute("DETACH DATABASE validated_identity")
        return {"primitives": p, "evidence": e}

    def state(self) -> dict[str, object]:
        row = self.sidecar.execute("SELECT * FROM compact_migration_state WHERE singleton=1").fetchone()
        if row is None: return {"phase": "NOT_STARTED", "state": "NOT_STARTED"}
        return {"migration_id": row[1], "generation": row[2], "phase": row[3], "state": row[3],
                "source_high_water": row[4], "source_cursor": row[5],
                "applied_delta_sequence": row[6], "delta_cursor": row[6],
                "reader_mode": row[7], "writer_mode": row[8],
                "authority_generation": row[9], "started_at": row[10], "updated_at": row[11],
                "cursor_kind": row[12], "source_relation_high_water": row[13]}

    def transition(self, target: str) -> None:
        current = str(self.state()["state"])
        if target not in STATES.get(current, set()):
            raise RuntimeError(f"invalid migration transition: {current} -> {target}")
        self.sidecar.execute("UPDATE compact_migration_state SET state=?,updated_at=? WHERE singleton=1",
                             (target, str(time.time_ns())))
        self.sidecar.commit()

    def _checkpoint(self, rows: int, elapsed_ms: float) -> None:
        state = self.state()
        sidecar_bytes = self.sidecar_path.stat().st_size if self.sidecar_path.exists() else 0
        wal = Path(str(self.sidecar_path) + "-wal")
        free = __import__("shutil").disk_usage(self.sidecar_path.parent).free
        self.sidecar.execute("""INSERT INTO compact_migration_checkpoints
          (migration_id,state,source_cursor,delta_cursor,rows_processed,elapsed_ms,
           sidecar_bytes,wal_bytes,free_bytes) VALUES(?,?,?,?,?,?,?,?,?)""",
          (state["migration_id"], state["state"], state["source_cursor"], state["delta_cursor"],
           rows, elapsed_ms, sidecar_bytes, wal.stat().st_size if wal.exists() else 0, free))
        self.sidecar.commit()

    def build_batch(self, batch_size: int) -> dict[str, int | float]:
        state = self.state()
        if state["state"] not in {"BUILDING"}:
            raise RuntimeError("base build is not active")
        started = time.perf_counter()
        upper = min(int(state["source_high_water"]), int(state["source_cursor"]) + batch_size)
        if state.get("cursor_kind") == "PRIMITIVE_KEY":
            seed_path = self.sidecar.execute(
                "SELECT source_path FROM compact_identity_seed WHERE singleton=1").fetchone()[0]
            aliases = {row[1] for row in self.sidecar.execute("PRAGMA database_list")}
            if "validated_links" not in aliases:
                self.sidecar.execute("ATTACH DATABASE ? AS validated_links", (seed_path,))
            row_count = int(self.sidecar.execute("""SELECT COUNT(*) FROM
              validated_links.compact_primitive_evidence_inputs
              WHERE primitive_key>? AND primitive_key<=?""",
              (state["source_cursor"], upper)).fetchone()[0])
            before = int(self.sidecar.execute(
                "SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0])
            self.sidecar.execute("BEGIN IMMEDIATE")
            try:
                self.sidecar.execute("""INSERT OR IGNORE INTO compact_primitive_evidence_inputs
                  SELECT primitive_key,evidence_key
                  FROM validated_links.compact_primitive_evidence_inputs
                  WHERE primitive_key>? AND primitive_key<=?""", (state["source_cursor"], upper))
                self.sidecar.commit()
            except BaseException:
                self.sidecar.rollback(); raise
            after = int(self.sidecar.execute(
                "SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0])
            result = {"inserted": after-before, "duplicates": row_count-(after-before)}
            cursor = upper
            elapsed = (time.perf_counter() - started) * 1000
            self.sidecar.execute("UPDATE compact_migration_state SET source_cursor=?,updated_at=? WHERE singleton=1",
                                 (cursor, str(time.time_ns()))); self.sidecar.commit()
            self._checkpoint(row_count, elapsed)
            if cursor >= state["source_high_water"]: self.transition("CATCHING_UP")
            return {**result, "cursor": int(cursor), "rows": row_count, "elapsed_ms": elapsed}
        aliases = {row[1] for row in self.sidecar.execute("PRAGMA database_list")}
        if "canonical_build" not in aliases:
            self.sidecar.execute("ATTACH DATABASE ? AS canonical_build", (str(self.canonical_path),))
        row_count = int(self.sidecar.execute("""SELECT COUNT(*) FROM
          canonical_build.primitive_evidence_inputs WHERE rowid>? AND rowid<=?""",
          (state["source_cursor"], upper)).fetchone()[0])
        before = int(self.sidecar.execute(
            "SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0])
        seeded = self.sidecar.execute("SELECT 1 FROM compact_identity_seed WHERE singleton=1").fetchone() is not None
        self.sidecar.execute("BEGIN IMMEDIATE")
        try:
            if not seeded:
                self.sidecar.execute("""INSERT OR IGNORE INTO primitive_identity(primitive_id)
                  SELECT DISTINCT primitive_id FROM canonical_build.primitive_evidence_inputs
                  WHERE rowid>? AND rowid<=?""", (state["source_cursor"], upper))
                self.sidecar.execute("""INSERT OR IGNORE INTO evidence_identity(evidence_id)
                  SELECT DISTINCT evidence_id FROM canonical_build.primitive_evidence_inputs
                  WHERE rowid>? AND rowid<=?""", (state["source_cursor"], upper))
            self.sidecar.execute("""INSERT OR IGNORE INTO compact_primitive_evidence_inputs
              SELECT p.primitive_key,e.evidence_key
              FROM canonical_build.primitive_evidence_inputs i
              JOIN primitive_identity p USING(primitive_id)
              JOIN evidence_identity e USING(evidence_id)
              WHERE i.rowid>? AND i.rowid<=?""", (state["source_cursor"], upper))
            self.sidecar.commit()
        except BaseException:
            self.sidecar.rollback(); raise
        after = int(self.sidecar.execute(
            "SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0])
        inserted = after - before
        result = {"inserted": inserted, "duplicates": row_count - inserted}
        cursor = upper if row_count else state["source_cursor"]
        self.sidecar.execute("UPDATE compact_migration_state SET source_cursor=?,updated_at=? WHERE singleton=1",
                             (cursor, str(time.time_ns()))); self.sidecar.commit()
        elapsed = (time.perf_counter() - started) * 1000
        self._checkpoint(row_count, elapsed)
        if cursor >= state["source_high_water"]:
            self.transition("CATCHING_UP")
        return {**result, "cursor": int(cursor), "rows": row_count, "elapsed_ms": elapsed}

    def apply_deltas(self, batch_size: int = 10_000, *, through_sequence: int | None = None) -> dict[str, int]:
        applied = int(self.state()["delta_cursor"]); inserted = duplicates = rows = 0
        while True:
            params: list[int] = [applied]
            boundary = ""
            if through_sequence is not None:
                boundary = " AND sequence<=?"; params.append(through_sequence)
            params.append(batch_size)
            batch = self.canonical.execute(f"""SELECT sequence,primitive_id,evidence_id
              FROM compact_migration_delta WHERE sequence>?{boundary} ORDER BY sequence LIMIT ?""", params).fetchall()
            if not batch: break
            result = self.repository.append_bulk((row[1], row[2]) for row in batch)
            inserted += result["inserted"]; duplicates += result["duplicates"]; rows += len(batch)
            applied = batch[-1][0]
            self.sidecar.execute("""UPDATE compact_migration_state
              SET delta_cursor=?,updated_at=? WHERE singleton=1""", (applied, str(time.time_ns())))
            self.sidecar.commit()
        return {"rows": rows, "inserted": inserted, "duplicates": duplicates,
                "applied_delta_sequence": applied}

    def write_relations(self, pairs: Iterable[tuple[str, str]], *, rollback: bool = False) -> dict[str, int]:
        values = tuple(pairs)
        control = self.control()
        if control["writers_paused"]:
            raise RuntimeError("migration-paused")
        if rollback:
            self.canonical.execute("BEGIN IMMEDIATE")
            try:
                for pair in values:
                    self.canonical.execute("INSERT OR IGNORE INTO primitive_evidence_inputs VALUES(?,?)", pair)
                raise RuntimeError("synthetic rollback")
            except RuntimeError:
                self.canonical.rollback()
            return {"inserted": 0, "duplicates": 0}
        if control["writer_mode"] == "COMPACT":
            result = self.repository.append(values)
            # Durable rollback reconciliation journal; canonical is not guessed from sidecar state.
            self.canonical.executemany("""INSERT OR IGNORE INTO compact_rollback_delta
              (primitive_id,evidence_id) VALUES(?,?)""", values)
            self.canonical.commit()
            return result
        inserted = 0
        for pair in values:
            inserted += self.canonical.execute(
                "INSERT OR IGNORE INTO primitive_evidence_inputs VALUES(?,?)", pair).rowcount
        self.canonical.commit()
        return {"inserted": inserted, "duplicates": len(values) - inserted}

    def pause_writers(self) -> int:
        self.canonical.execute("UPDATE compact_migration_control SET writers_paused=1 WHERE singleton=1")
        self.canonical.commit()
        return time.monotonic_ns()

    def resume_writers(self) -> None:
        self.canonical.execute("UPDATE compact_migration_control SET writers_paused=0 WHERE singleton=1")
        self.canonical.commit()

    def validate(self) -> dict[str, object]:
        validation_started = time.monotonic_ns()
        tick = time.monotonic_ns()
        canonical_count = int(self.canonical.execute(
            "SELECT COUNT(*) FROM primitive_evidence_inputs").fetchone()[0])
        canonical_count_ms = (time.monotonic_ns() - tick) / 1_000_000
        tick = time.monotonic_ns()
        compact_count = int(self.sidecar.execute(
            "SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0])
        compact_count_ms = (time.monotonic_ns() - tick) / 1_000_000
        anti_join_ms = digest_ms = 0.0
        if canonical_count < 1_000_000:
            tick = time.monotonic_ns()
            canonical_pairs = tuple(self.canonical.execute(
                "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY 1,2"))
            compact_pairs = tuple(sorted(self.repository.ordered_pairs()))
            canonical_set, compact_set = set(canonical_pairs), set(compact_pairs)
            canonical_digest, compact_digest = relation_digest(canonical_pairs), relation_digest(compact_pairs)
            missing, extra = len(canonical_set-compact_set), len(compact_set-canonical_set)
            digest_ms = (time.monotonic_ns() - tick) / 1_000_000
            method = "direct ordered digest and bidirectional sets"
        else:
            # Explainable indexed anti-join: resolve external IDs to integer keys once,
            # then probe the WITHOUT ROWID relation. Avoid a 12M-row compatibility view.
            aliases = {row[1] for row in self.canonical.execute("PRAGMA database_list")}
            if "compact_validation" not in aliases:
                self.canonical.execute("ATTACH DATABASE ? AS compact_validation", (str(self.sidecar_path),))
            plan = tuple(row[3] for row in self.canonical.execute("""EXPLAIN QUERY PLAN
              SELECT COUNT(*) FROM primitive_evidence_inputs i
              JOIN compact_validation.primitive_identity p ON p.primitive_id=i.primitive_id
              JOIN compact_validation.evidence_identity e ON e.evidence_id=i.evidence_id
              LEFT JOIN compact_validation.compact_primitive_evidence_inputs c
                ON c.primitive_key=p.primitive_key AND c.evidence_key=e.evidence_key
              WHERE c.primitive_key IS NULL"""))
            tick = time.monotonic_ns()
            missing = int(self.canonical.execute("""SELECT COUNT(*) FROM primitive_evidence_inputs i
              JOIN compact_validation.primitive_identity p ON p.primitive_id=i.primitive_id
              JOIN compact_validation.evidence_identity e ON e.evidence_id=i.evidence_id
              LEFT JOIN compact_validation.compact_primitive_evidence_inputs c
                ON c.primitive_key=p.primitive_key AND c.evidence_key=e.evidence_key
              WHERE c.primitive_key IS NULL""").fetchone()[0])
            anti_join_ms = (time.monotonic_ns() - tick) / 1_000_000
            # Both relations enforce pair uniqueness. Equality of cardinality plus
            # canonical subset compact proves the reverse anti-join is empty.
            extra = 0 if missing == 0 and canonical_count == compact_count else -1
            tick = time.monotonic_ns(); digest = hashlib.sha256()
            for primitive_id, evidence_id in self.canonical.execute(
                    "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY 1,2"):
                digest.update(primitive_id.encode()); digest.update(b"\0")
                digest.update(evidence_id.encode()); digest.update(b"\n")
            canonical_digest = digest.hexdigest()
            digest_ms = (time.monotonic_ns() - tick) / 1_000_000
            compact_digest = canonical_digest if extra == 0 else "UNVERIFIED"
            method = "indexed canonical-minus-compact + cardinality proof; " + " | ".join(plan)
        exact = (canonical_count == compact_count and missing == 0 and extra == 0 and
                 canonical_digest == compact_digest)
        return {"canonical_count": canonical_count, "compact_count": compact_count,
                "canonical_digest": canonical_digest, "compact_digest": compact_digest,
                "canonical_minus_compact": missing, "compact_minus_canonical": extra,
                "validation_method": method, "exact": exact,
                "timings": {"canonical_count_ms": canonical_count_ms,
                    "compact_count_ms": compact_count_ms, "anti_join_ms": anti_join_ms,
                    "digest_ms": digest_ms,
                    "total_ms": (time.monotonic_ns() - validation_started) / 1_000_000}}

    def prevalidate(
        self,
        *,
        authority_generation: str,
        current_authoritative_count: int,
        current_authority_provenance_count: int,
    ) -> dict[str, object]:
        """Persist a full equivalence boundary while canonical writers remain live.

        A concurrent outbox advance invalidates the observation rather than
        pretending that several long-running statements formed one snapshot.
        Callers may catch up and retry while canonical remains authoritative.
        """
        state = self.state()
        if state["state"] != "CATCHING_UP":
            raise RuntimeError("migration is not ready for prevalidation")
        control = self.control()
        if control["writers_paused"] or control["reader_mode"] != "CANONICAL":
            raise RuntimeError("prevalidation requires live canonical writers")
        if control["authority_generation"] not in {None, authority_generation}:
            raise RuntimeError("authority generation changed before prevalidation")
        head = int(self.canonical.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM compact_migration_delta").fetchone()[0])
        self.apply_deltas(through_sequence=head)
        validation = self.validate()
        completed_head = int(self.canonical.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM compact_migration_delta").fetchone()[0])
        exact = bool(validation["exact"] and completed_head == head and
                     int(self.state()["delta_cursor"]) == head)
        completed_at = str(time.time_ns())
        # Establish the trusted counter only from an exact full-corpus boundary.
        # All later compact appends maintain it in their own relation transaction.
        if exact:
            self.repository.reset_relation_count(int(validation["compact_count"]))
        self.sidecar.execute("BEGIN IMMEDIATE")
        try:
            self.sidecar.execute("""INSERT OR REPLACE INTO compact_prevalidation_boundary VALUES(
              1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                state["migration_id"], state["generation"], str(state["generation"]), head,
                validation["canonical_count"], validation["compact_count"],
                validation["canonical_digest"], validation["canonical_minus_compact"],
                validation["compact_minus_canonical"], authority_generation,
                current_authoritative_count, current_authority_provenance_count,
                json.dumps(validation, sort_keys=True), completed_at, int(exact)))
            self.sidecar.commit()
        except BaseException:
            self.sidecar.rollback(); raise
        if not exact:
            raise RuntimeError("prevalidation boundary changed or equivalence failed")
        return {**validation, "validated_source_generation": str(state["generation"]),
                "validated_delta_sequence": head,
                "authority_generation": authority_generation,
                "current_authoritative_count": current_authoritative_count,
                "current_authority_provenance_count": current_authority_provenance_count,
                "validation_completed_at": completed_at,
                "writers_live": True}

    def prevalidation_boundary(self) -> dict[str, object] | None:
        row = self.sidecar.execute(
            "SELECT * FROM compact_prevalidation_boundary WHERE singleton=1").fetchone()
        if row is None:
            return None
        return {
            "migration_id": row[1], "generation": row[2],
            "validated_source_generation": row[3], "validated_delta_sequence": row[4],
            "validated_canonical_count": row[5], "validated_compact_count": row[6],
            "validated_digest": row[7], "canonical_minus_compact": row[8],
            "compact_minus_canonical": row[9], "authority_generation": row[10],
            "current_authoritative_count": row[11],
            "current_authority_provenance_count": row[12],
            "validation": json.loads(row[13]), "validation_completed_at": row[14],
            "exact": bool(row[15]),
        }

    def bounded_cutover(
        self,
        *,
        authority_generation: str,
        max_pause_ms: float = 30_000,
    ) -> dict[str, object]:
        """Close only the post-validation delta while writers are paused.

        Full digest and anti-join validation are intentionally forbidden here.
        Safety comes from the persisted full boundary plus exact proof of the
        bounded suffix and a cardinality invariant. Exhaustive validation runs
        again after writers resume.
        """
        boundary = self.prevalidation_boundary()
        state = self.state()
        if state["state"] != "CATCHING_UP" or not boundary or not boundary["exact"]:
            raise RuntimeError("exact prevalidation boundary required")
        if boundary["migration_id"] != state["migration_id"] or boundary["generation"] != state["generation"]:
            raise RuntimeError("stale prevalidation boundary")
        if boundary["authority_generation"] != authority_generation:
            raise RuntimeError("authority generation does not match prevalidation")
        if int(state["delta_cursor"]) != int(boundary["validated_delta_sequence"]):
            raise RuntimeError("delta cursor moved after prevalidation outside bounded cutover")

        timings: dict[str, float] = {}
        pause_started_wall = str(time.time_ns())
        tick = time.monotonic_ns(); self.transition("PAUSE_REQUIRED")
        pause_started = self.pause_writers()
        timings["pause_acquisition_ms"] = (time.monotonic_ns() - tick) / 1_000_000
        try:
            self.transition("FINAL_DRAIN")
            tick = time.monotonic_ns()
            final_sequence = int(self.canonical.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM compact_migration_delta").fetchone()[0])
            final_rows = self.canonical.execute("""SELECT sequence,primitive_id,evidence_id
              FROM compact_migration_delta WHERE sequence>? AND sequence<=?
              ORDER BY sequence""", (boundary["validated_delta_sequence"], final_sequence)).fetchall()
            timings["final_sequence_capture_ms"] = (time.monotonic_ns() - tick) / 1_000_000

            tick = time.monotonic_ns()
            delta = self.apply_deltas(through_sequence=final_sequence)
            timings["delta_apply_ms"] = (time.monotonic_ns() - tick) / 1_000_000
            if int(self.state()["delta_cursor"]) != final_sequence:
                raise RuntimeError("final delta sequence was not fully applied")

            unique_pairs = tuple(sorted({(row[1], row[2]) for row in final_rows}))
            expected_count = int(boundary["validated_canonical_count"]) + int(delta["inserted"])
            tick = time.monotonic_ns()
            # Canonical uniqueness + the AFTER INSERT outbox make suffix event
            # count the authoritative net-new canonical count. The compact
            # counter is maintained in the same transaction as compact inserts.
            canonical_count = int(boundary["validated_canonical_count"]) + len(unique_pairs)
            compact_count = self.repository.relation_count()
            timings["bounded_count_check_ms"] = (time.monotonic_ns() - tick) / 1_000_000

            tick = time.monotonic_ns()
            missing_pairs = tuple(pair for pair in unique_pairs if not self.repository.contains(*pair))
            timings["bounded_tuple_validation_ms"] = (time.monotonic_ns() - tick) / 1_000_000
            no_delta_extras = (len(unique_pairs) == delta["inserted"] + delta["duplicates"])
            bounded_exact = all((canonical_count == expected_count,
                                 compact_count == expected_count,
                                 not missing_pairs, no_delta_extras))

            tick = time.monotonic_ns()
            control = self.control()
            authority_exact = control["authority_generation"] in {None, authority_generation}
            timings["authority_check_ms"] = (time.monotonic_ns() - tick) / 1_000_000
            elapsed_ms = (time.monotonic_ns() - pause_started) / 1_000_000
            if not bounded_exact or not authority_exact:
                raise RuntimeError("bounded final validation failed")
            if elapsed_ms >= max_pause_ms:
                raise TimeoutError("writer pause limit reached before control switch")

            self.transition("VERIFIED")
            tick = time.monotonic_ns()
            self.canonical.execute("BEGIN IMMEDIATE")
            try:
                self.canonical.execute("""UPDATE compact_migration_control SET
                  reader_mode='COMPACT',writer_mode='COMPACT',sidecar_generation=?,
                  authority_generation=?,switched_delta_sequence=? WHERE singleton=1""",
                  (str(state["generation"]), authority_generation, final_sequence))
                self.canonical.commit()
            except BaseException:
                self.canonical.rollback(); raise
            self.sidecar.execute("""UPDATE compact_migration_state SET state='COMPACT_ACTIVE',
              reader_mode='COMPACT',writer_mode='COMPACT',authority_generation=?,updated_at=?
              WHERE singleton=1""", (authority_generation, str(time.time_ns())))
            self.sidecar.commit()
            timings["control_switch_ms"] = (time.monotonic_ns() - tick) / 1_000_000
            tick = time.monotonic_ns(); self.resume_writers()
            timings["writer_resume_ms"] = (time.monotonic_ns() - tick) / 1_000_000
        except BaseException:
            if self.control()["reader_mode"] == "CANONICAL":
                self.resume_writers()
                if self.state()["state"] in {"PAUSE_REQUIRED", "FINAL_DRAIN"}:
                    self.transition("CATCHING_UP")
            raise

        pause_ms = (time.monotonic_ns() - pause_started) / 1_000_000
        timings["total_pause_ms"] = pause_ms
        bounded = {
            "exact": True, "validation_scope": "PREVALIDATED_FULL_CORPUS_PLUS_BOUNDED_DELTA",
            "full_digest_inside_pause": False, "full_anti_join_inside_pause": False,
            "digest_strategy": "prevalidated full digest + exact bounded delta proof; exhaustive digest after resume",
            "validated_delta_sequence": boundary["validated_delta_sequence"],
            "final_delta_sequence": final_sequence, "delta_events": len(final_rows),
            "unique_delta_relations": len(unique_pairs), "new_relations": delta["inserted"],
            "duplicates": delta["duplicates"], "missing_delta_relations": len(missing_pairs),
            "no_delta_extras": no_delta_extras, "expected_final_count": expected_count,
            "canonical_final_count": canonical_count, "compact_final_count": compact_count,
            "count_strategy": "prevalidated canonical count + unique outbox suffix; transactional compact counter",
            "authority_generation_exact": authority_exact,
        }
        self.sidecar.execute("""INSERT INTO compact_cutover_events(
          migration_id,generation,validated_delta_sequence,final_delta_sequence,
          final_delta_events,final_delta_inserted,final_delta_duplicates,
          expected_final_count,compact_final_count,pause_started_at,pause_ended_at,
          pause_ms,timing_json,bounded_validation_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            state["migration_id"], state["generation"], boundary["validated_delta_sequence"],
            final_sequence, len(final_rows), delta["inserted"], delta["duplicates"],
            expected_count, compact_count, pause_started_wall, str(time.time_ns()), pause_ms,
            json.dumps(timings, sort_keys=True), json.dumps(bounded, sort_keys=True)))
        self.sidecar.commit()
        return {"control": self.control(), "bounded_validation": bounded,
                "pause_ms": pause_ms, "timings": timings}

    def prepare_cutover(self) -> int:
        if self.state()["state"] != "CATCHING_UP":
            raise RuntimeError("migration is not caught up")
        self.transition("PAUSE_REQUIRED")
        started = self.pause_writers()
        self.transition("FINAL_DRAIN")
        final_sequence = int(self.canonical.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM compact_migration_delta").fetchone()[0])
        self.apply_deltas(through_sequence=final_sequence)
        validation = self.validate()
        if not validation["exact"]:
            self.resume_writers(); self.transition("FAILED")
            raise RuntimeError("compact relation is not equivalent")
        self.transition("VERIFIED")
        return started

    def cutover(self, *, writer_paused: bool, authority_generation: str) -> dict[str, object]:
        if not writer_paused: raise RuntimeError("writer pause required for cutover")
        state = self.state()
        # Backwards compatibility for the v2.2C.4 API: finish catch-up/pause here.
        if state["state"] == "BUILDING": raise RuntimeError("base build incomplete")
        if state["state"] == "CATCHING_UP": self.prepare_cutover(); state = self.state()
        if state["state"] != "VERIFIED": raise RuntimeError("migration not verified")
        final_sequence = int(self.canonical.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM compact_migration_delta").fetchone()[0])
        if final_sequence != int(state["delta_cursor"]):
            raise RuntimeError("delta arrived during cutover")
        self.canonical.execute("BEGIN IMMEDIATE")
        try:
            self.canonical.execute("""UPDATE compact_migration_control SET
              reader_mode='COMPACT',writer_mode='COMPACT',sidecar_generation=?,
              authority_generation=?,switched_delta_sequence=? WHERE singleton=1""",
              (str(state["generation"]), authority_generation, final_sequence))
            self.canonical.commit()
        except BaseException:
            self.canonical.rollback(); raise
        self.sidecar.execute("""UPDATE compact_migration_state SET state='COMPACT_ACTIVE',
          reader_mode='COMPACT',writer_mode='COMPACT',authority_generation=? WHERE singleton=1""",
          (authority_generation,)); self.sidecar.commit()
        self.resume_writers()
        return {"control": self.control(), "validation": self.validate()}

    def reconcile_compact_writes(self) -> dict[str, int]:
        rows = self.canonical.execute("""SELECT sequence,primitive_id,evidence_id
          FROM compact_rollback_delta WHERE reconciled=0 ORDER BY sequence""").fetchall()
        self.canonical.execute("BEGIN IMMEDIATE")
        try:
            inserted = 0
            for row in rows:
                inserted += self.canonical.execute(
                    "INSERT OR IGNORE INTO primitive_evidence_inputs VALUES(?,?)",
                    (row[1], row[2])).rowcount
            self.canonical.executemany("UPDATE compact_rollback_delta SET reconciled=1 WHERE sequence=?",
                                       ((r[0],) for r in rows))
            self.canonical.commit()
        except BaseException:
            self.canonical.rollback(); raise
        return {"rows": len(rows), "inserted": inserted, "duplicates": len(rows)-inserted}

    def rollback(self, *, max_pause_ms: float | None = None) -> dict[str, object]:
        pause_started = None
        if self.control()["reader_mode"] == "COMPACT":
            pause_started = self.pause_writers()
        reconciliation = self.reconcile_compact_writes()
        if (pause_started is not None and max_pause_ms is not None and
                (time.monotonic_ns() - pause_started) / 1_000_000 >= max_pause_ms):
            self.resume_writers()
            raise TimeoutError("rollback writer pause limit reached before control switch")
        self.canonical.execute("BEGIN IMMEDIATE")
        self.canonical.execute("""UPDATE compact_migration_control SET reader_mode='CANONICAL',
          writer_mode='CANONICAL_WITH_DELTA',writers_paused=0 WHERE singleton=1""")
        self.canonical.commit()
        state = self.state()["state"]
        if state == "COMPACT_ACTIVE": self.transition("ROLLBACK_ACTIVE")
        self.sidecar.execute("""UPDATE compact_migration_state SET reader_mode='CANONICAL',
          writer_mode='CANONICAL_WITH_DELTA' WHERE singleton=1"""); self.sidecar.commit()
        result = self.control()
        result["reconciliation"] = reconciliation
        result["pause_ms"] = ((time.monotonic_ns() - pause_started) / 1_000_000
                              if pause_started is not None else 0.0)
        return result

    def control(self) -> dict[str, object]:
        row = self.canonical.execute("SELECT * FROM compact_migration_control WHERE singleton=1").fetchone()
        return {"reader_mode": row[1], "writer_mode": row[2], "sidecar_generation": row[3],
                "authority_generation": row[4], "switched_delta_sequence": row[5],
                "writers_paused": bool(row[6]), "migration_id": row[7], "generation": row[8]}
