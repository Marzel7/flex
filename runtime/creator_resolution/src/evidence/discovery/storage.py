"""Isolated append-only persistence for non-authoritative discovery candidates."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Sequence

from ..contracts import canonical_json_bytes
from .contracts import CandidateLifecycle, DiscoveryCandidate, TRANSITIONS


SCHEMA_PATH = Path(__file__).with_name("discovery_schema.sql")


class DiscoveryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.connection: sqlite3.Connection | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(SCHEMA_PATH.read_text())

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _conn(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Discovery store is not open")
        return self.connection

    def append(self, candidates: Sequence[DiscoveryCandidate]) -> dict[str, int]:
        inserted = duplicates = 0
        connection = self._conn()
        connection.execute("BEGIN IMMEDIATE")
        try:
            for candidate in candidates:
                value = candidate.to_dict()
                payload = canonical_json_bytes(value).decode().rstrip("\n")
                digest = hashlib.sha256(payload.encode()).hexdigest()
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO discovery_candidates VALUES(?,?,?,?,?,?,?)",
                    (candidate.candidate_id, candidate.discovery_version,
                     candidate.input_digest, candidate.lifecycle, payload, digest,
                     candidate.generated_at),
                )
                if cursor.rowcount:
                    inserted += 1
                    for reference_type, references in (
                        ("Evidence", candidate.supporting_evidence_ids),
                        ("Primitive", candidate.supporting_primitive_ids),
                        ("BehaviourObservation", candidate.supporting_behaviour_observation_ids),
                        ("TopologyRevision", candidate.supporting_topology_revision_ids),
                    ):
                        for reference in references:
                            connection.execute(
                                "INSERT INTO discovery_candidate_references VALUES(?,?,?)",
                                (candidate.candidate_id, reference_type, reference),
                            )
                else:
                    row = connection.execute(
                        "SELECT payload_digest FROM discovery_candidates WHERE candidate_id=?",
                        (candidate.candidate_id,),
                    ).fetchone()
                    if row is None or row[0] != digest:
                        raise sqlite3.IntegrityError("discovery candidate identity collision")
                    duplicates += 1
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return {"inserted": inserted, "duplicates": duplicates}

    def transition(self, candidate_id: str, *, from_state: CandidateLifecycle,
                   to_state: CandidateLifecycle, reason: str,
                   occurred_at: int) -> str:
        if to_state not in TRANSITIONS[from_state]:
            raise ValueError("invalid discovery lifecycle transition")
        if self._conn().execute(
            "SELECT 1 FROM discovery_candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone() is None:
            raise LookupError("unknown discovery candidate")
        current = self.current_state(candidate_id)
        if current is not from_state:
            raise ValueError("stale discovery lifecycle state")
        body = [candidate_id, from_state.value, to_state.value, reason, int(occurred_at)]
        event_id = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        payload_digest = hashlib.sha256(canonical_json_bytes(body[:-1])).hexdigest()
        self._conn().execute(
            "INSERT OR IGNORE INTO discovery_lifecycle_events VALUES(?,?,?,?,?,?,?)",
            (event_id, candidate_id, from_state.value, to_state.value, reason,
             int(occurred_at), payload_digest),
        )
        return event_id

    def current_state(self, candidate_id: str) -> CandidateLifecycle:
        event = self._conn().execute(
            "SELECT to_state FROM discovery_lifecycle_events WHERE candidate_id=? "
            "ORDER BY occurred_at DESC,event_id DESC LIMIT 1", (candidate_id,),
        ).fetchone()
        if event is not None:
            return CandidateLifecycle(event[0])
        row = self._conn().execute(
            "SELECT lifecycle FROM discovery_candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise LookupError("unknown discovery candidate")
        return CandidateLifecycle(row[0])

    def health(self) -> dict[str, object]:
        connection = self._conn()
        candidates = int(connection.execute(
            "SELECT COUNT(*) FROM discovery_candidates"
        ).fetchone()[0])
        events = int(connection.execute(
            "SELECT COUNT(*) FROM discovery_lifecycle_events"
        ).fetchone()[0])
        return {"status": "HEALTHY", "candidates": candidates,
                "lifecycle_events": events, "authoritative": False}
