"""Isolated, bounded SQLite retention for observed-minimum evidence.

This adapter deliberately accepts only records built by
``watchtower_observed_minimum_evidence``.  It is not connected to the monitor
store, a queue, a provider client, or lifecycle facts.  Each accepted record is
an immutable JSON value keyed by its deterministic evidence identity.
"""

from __future__ import annotations

import json
import sqlite3
import hashlib
import zlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.ops.watchtower_observed_minimum_evidence import _canonical


STORE_VERSION = "WATCHTOWER_OBSERVED_MINIMUM_STORE_V1"
MAX_RECORD_BYTES = 1_024
MAX_MINT_BYTES = 30_720
MAX_MANIFEST_BYTES = 100 * 1_024
MAX_EVIDENCE_FILE_BYTES = 1_000_000
BUSY_TIMEOUT_MS = 1_000

_REQUIRED = frozenset({
    "record_version", "mint", "entry_identity", "request_identity",
    "window_seconds", "minimum_status", "coverage_status",
    "candle_interval_seconds", "missing_bucket_timestamps",
    "invalid_bucket_timestamps", "provider_provenance", "evidence_identity",
})
_RAW_KEYS = frozenset({"candles", "raw", "raw_response", "provider_payload"})


class EvidenceStoreLimitError(ValueError):
    """A bounded evidence-store admission could not be made safely."""


class EvidenceIdentityConflictError(ValueError):
    """A deterministic identity already exists with different bytes."""


def _bytes(value: object) -> bytes:
    return _canonical(value).encode("utf-8")


def _validate_record(record: Mapping[str, Any]) -> tuple[str, str, bytes, bytes]:
    if _REQUIRED - set(record):
        raise ValueError("INCOMPLETE_OBSERVED_MINIMUM_EVIDENCE")
    if _RAW_KEYS & set(record):
        raise ValueError("RAW_EVIDENCE_FORBIDDEN")
    mint = record.get("mint")
    identity = record.get("evidence_identity")
    if not isinstance(mint, str) or not mint or not isinstance(identity, str) or not identity:
        raise ValueError("INVALID_EVIDENCE_IDENTITY")
    body = dict(record)
    body.pop("evidence_identity")
    if hashlib.sha256(_bytes(body)).hexdigest() != identity:
        raise ValueError("INVALID_EVIDENCE_IDENTITY_DIGEST")
    canonical = _bytes(dict(record))
    # SQLite keeps only the compact encoded form.  The expanded value is
    # returned through ``read`` and remains independently digest-verifiable.
    payload = zlib.compress(canonical, level=9)
    if len(payload) > MAX_RECORD_BYTES:
        raise EvidenceStoreLimitError("RECORD_BYTES_EXCEEDED")
    return mint, identity, canonical, payload


class ObservedMinimumEvidenceStore:
    """Small append-only store with explicit byte and lock bounds.

    ``initialize`` is the only setup operation and enables WAL for this
    isolated evidence database.  Every append uses one short ``BEGIN
    IMMEDIATE`` transaction with a one-second busy timeout.  The adapter never
    repairs, rewrites, or deletes prior evidence.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    def _file_sizes(self) -> int:
        return sum(candidate.stat().st_size for candidate in (
            self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"),
        ) if candidate.exists())

    def _assert_file_budget(self) -> None:
        if self._file_sizes() > MAX_EVIDENCE_FILE_BYTES:
            raise EvidenceStoreLimitError("EVIDENCE_FILE_BYTES_EXCEEDED")

    def initialize(self) -> None:
        """Create only this isolated schema; reject an over-budget path."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_file_budget()
        with sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1_000) as conn:
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS watchtower_observed_minimum_evidence ("
                "evidence_identity TEXT PRIMARY KEY, mint TEXT NOT NULL, "
                "record_version TEXT NOT NULL, payload_zlib BLOB NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_watchtower_observed_minimum_mint "
                "ON watchtower_observed_minimum_evidence(mint)"
            )
        self._assert_file_budget()

    def append(self, record: Mapping[str, Any]) -> bool:
        """Append one record, return false for an exact duplicate, fail on conflict."""
        mint, identity, canonical, payload = _validate_record(record)
        self.initialize()
        self._assert_file_budget()
        try:
            with sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1_000) as conn:
                conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT payload_zlib FROM watchtower_observed_minimum_evidence "
                    "WHERE evidence_identity=?", (identity,)
                ).fetchone()
                if existing:
                    if bytes(existing[0]) != payload:
                        raise EvidenceIdentityConflictError("EVIDENCE_IDENTITY_COLLISION")
                    conn.rollback()
                    return False
                mint_bytes = conn.execute(
                    "SELECT COALESCE(SUM(length(payload_zlib)), 0) "
                    "FROM watchtower_observed_minimum_evidence WHERE mint=?", (mint,)
                ).fetchone()[0]
                if int(mint_bytes) + len(payload) > MAX_MINT_BYTES:
                    raise EvidenceStoreLimitError("MINT_BYTES_EXCEEDED")
                conn.execute(
                    "INSERT INTO watchtower_observed_minimum_evidence "
                    "(evidence_identity,mint,record_version,payload_zlib) VALUES (?,?,?,?)",
                    (identity, mint, str(record["record_version"]), payload),
                )
                manifest_bytes = conn.execute(
                    "SELECT COALESCE(SUM(length(payload_zlib)), 0) "
                    "FROM watchtower_observed_minimum_evidence"
                ).fetchone()[0]
                if int(manifest_bytes) > MAX_MANIFEST_BYTES:
                    raise EvidenceStoreLimitError("MANIFEST_BYTES_EXCEEDED")
        except sqlite3.Error as exc:
            raise EvidenceStoreLimitError("EVIDENCE_STORE_UNAVAILABLE") from exc
        self._assert_file_budget()
        return True

    def append_all(self, records: Iterable[Mapping[str, Any]]) -> tuple[bool, ...]:
        """Use short independent transactions; a restart can safely resume."""
        return tuple(self.append(record) for record in records)

    def read(self, *, mint: str | None = None) -> tuple[dict[str, Any], ...]:
        """Read evidence through SQLite's URI read-only mode only."""
        if not self.path.exists():
            return ()
        uri = f"file:{self.path}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1_000) as conn:
                conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
                if mint is None:
                    rows = conn.execute(
                        "SELECT payload_zlib FROM watchtower_observed_minimum_evidence "
                        "ORDER BY evidence_identity"
                    )
                else:
                    rows = conn.execute(
                        "SELECT payload_zlib FROM watchtower_observed_minimum_evidence "
                        "WHERE mint=? ORDER BY evidence_identity", (mint,)
                    )
                return tuple(json.loads(zlib.decompress(bytes(row[0]))) for row in rows)
        except sqlite3.Error as exc:
            raise EvidenceStoreLimitError("EVIDENCE_STORE_UNAVAILABLE") from exc
