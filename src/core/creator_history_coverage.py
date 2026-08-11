"""Conservative durable observations for creator-history acquisition coverage.

This is deliberately *not* an incremental-acquisition cursor.  It records
what a particular extractor run durably represented, and is intentionally
allowed to understate coverage after any crash or provider ambiguity.  A
consumer must never use it to infer that absent funding was observed absent.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Optional


SCHEMA_VERSION = "x78.37-v1"
ACQUISITION_VERSION = "creator-funding-enhanced-history-v1"


@dataclass(frozen=True)
class HistoryPage:
    newest_signature: Optional[str]
    oldest_signature: Optional[str]
    newest_slot: Optional[int]
    oldest_slot: Optional[int]
    newest_timestamp: Optional[int]
    oldest_timestamp: Optional[int]
    result_count: int
    duplicate_count: int
    digest: str


def _page_value(page: Iterable[dict[str, Any]]) -> HistoryPage:
    rows = [row for row in page if isinstance(row, dict)]
    signatures = [str(row["signature"]) for row in rows if row.get("signature")]
    slots = [row.get("slot") for row in rows if isinstance(row.get("slot"), int)]
    timestamps = [row.get("timestamp") for row in rows if isinstance(row.get("timestamp"), int)]
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
    return HistoryPage(
        newest_signature=signatures[0] if signatures else None,
        oldest_signature=signatures[-1] if signatures else None,
        newest_slot=slots[0] if slots else None,
        oldest_slot=slots[-1] if slots else None,
        newest_timestamp=timestamps[0] if timestamps else None,
        oldest_timestamp=timestamps[-1] if timestamps else None,
        result_count=len(rows),
        duplicate_count=len(signatures) - len(set(signatures)),
        digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


class CreatorHistoryCoverageStore:
    """Append-only coverage observations with an explicitly conservative state."""

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_history_coverage_runs (
                run_id TEXT PRIMARY KEY,
                creator_address TEXT NOT NULL,
                provider TEXT NOT NULL,
                method TEXT NOT NULL,
                acquisition_version TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                semantic_version TEXT NOT NULL,
                mutable_head INTEGER NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                terminal_state TEXT NOT NULL DEFAULT 'UNKNOWN',
                terminal_reason TEXT,
                provider_exhausted INTEGER NOT NULL DEFAULT 0,
                contiguous_boundary_proven INTEGER NOT NULL DEFAULT 0,
                last_successful_continuation_signature TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_history_coverage_pages (
                run_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                request_before_signature TEXT,
                newest_signature TEXT,
                oldest_signature TEXT,
                newest_slot INTEGER,
                oldest_slot INTEGER,
                newest_timestamp INTEGER,
                oldest_timestamp INTEGER,
                result_count INTEGER NOT NULL,
                duplicate_count INTEGER NOT NULL,
                artifact_digest TEXT NOT NULL,
                durable_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (run_id, page_number),
                FOREIGN KEY (run_id) REFERENCES creator_history_coverage_runs(run_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_history_coverage_state (
                creator_address TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                state TEXT NOT NULL,
                reason TEXT NOT NULL,
                newest_signature TEXT,
                oldest_signature TEXT,
                newest_slot INTEGER,
                oldest_slot INTEGER,
                provider_exhausted INTEGER NOT NULL,
                contiguous_boundary_proven INTEGER NOT NULL,
                mutable_head INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creator_history_coverage_runs_creator "
            "ON creator_history_coverage_runs(creator_address, started_at)"
        )

    def begin_run(
        self,
        conn: sqlite3.Connection,
        creator: str,
        *,
        provider: str,
        method: str,
        parser_version: str = SCHEMA_VERSION,
        semantic_version: str = SCHEMA_VERSION,
        mutable_head: bool = True,
        run_id: Optional[str] = None,
    ) -> str:
        self.ensure_schema(conn)
        value = run_id or str(uuid.uuid4())
        conn.execute(
            """INSERT OR IGNORE INTO creator_history_coverage_runs
               (run_id, creator_address, provider, method, acquisition_version,
                parser_version, semantic_version, mutable_head)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (value, creator, provider, method, ACQUISITION_VERSION,
             parser_version, semantic_version, int(mutable_head)),
        )
        conn.commit()
        return value

    def record_durable_page(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        page_number: int,
        request_before_signature: Optional[str],
        page: Iterable[dict[str, Any]],
    ) -> HistoryPage:
        """Record only after the caller has committed the page's facts.

        This intentionally commits after fact persistence.  If this write or
        the process fails, facts may exist without coverage metadata, which is
        safe under-statement; the inverse ordering is prohibited.
        """
        value = _page_value(page)
        conn.execute(
            """INSERT OR IGNORE INTO creator_history_coverage_pages
               (run_id, page_number, request_before_signature, newest_signature,
                oldest_signature, newest_slot, oldest_slot, newest_timestamp,
                oldest_timestamp, result_count, duplicate_count, artifact_digest)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, page_number, request_before_signature, value.newest_signature,
             value.oldest_signature, value.newest_slot, value.oldest_slot,
             value.newest_timestamp, value.oldest_timestamp, value.result_count,
             value.duplicate_count, value.digest),
        )
        conn.execute(
            """UPDATE creator_history_coverage_runs
               SET last_successful_continuation_signature = ?
               WHERE run_id = ?""",
            (value.oldest_signature, run_id),
        )
        conn.commit()
        return value

    def finish_run(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        *,
        state: str,
        reason: str,
        provider_exhausted: bool = False,
        contiguous_boundary_proven: bool = False,
    ) -> None:
        if state not in {
            "COMPLETE_EXHAUSTED", "EXHAUSTED_UNVERIFIED_CONTIGUITY",
            "PARTIAL", "FAILED", "UNKNOWN",
        }:
            raise ValueError(f"unsupported coverage terminal state: {state}")
        if state == "COMPLETE_EXHAUSTED" and not (
            provider_exhausted and contiguous_boundary_proven
        ):
            raise ValueError("complete coverage requires exhaustion and a proven boundary")
        run = conn.execute(
            """SELECT creator_address, mutable_head FROM creator_history_coverage_runs
               WHERE run_id = ?""", (run_id,)
        ).fetchone()
        if run is None:
            raise ValueError(f"unknown coverage run {run_id}")
        boundary = conn.execute(
            """SELECT newest_signature, oldest_signature, newest_slot, oldest_slot
               FROM creator_history_coverage_pages WHERE run_id = ?
               ORDER BY page_number ASC LIMIT 1""", (run_id,)
        ).fetchone()
        oldest = conn.execute(
            """SELECT oldest_signature, oldest_slot FROM creator_history_coverage_pages
               WHERE run_id = ? ORDER BY page_number DESC LIMIT 1""", (run_id,)
        ).fetchone()
        newest_signature = boundary[0] if boundary else None
        newest_slot = boundary[2] if boundary else None
        oldest_signature = oldest[0] if oldest else None
        oldest_slot = oldest[1] if oldest else None
        conn.execute(
            """UPDATE creator_history_coverage_runs
               SET completed_at = CURRENT_TIMESTAMP, terminal_state = ?, terminal_reason = ?,
                   provider_exhausted = ?, contiguous_boundary_proven = ?
               WHERE run_id = ?""",
            (state, reason, int(provider_exhausted), int(contiguous_boundary_proven), run_id),
        )
        conn.execute(
            """INSERT INTO creator_history_coverage_state
               (creator_address, run_id, state, reason, newest_signature, oldest_signature,
                newest_slot, oldest_slot, provider_exhausted,
                contiguous_boundary_proven, mutable_head)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(creator_address) DO UPDATE SET
                 run_id=excluded.run_id, state=excluded.state, reason=excluded.reason,
                 newest_signature=excluded.newest_signature, oldest_signature=excluded.oldest_signature,
                 newest_slot=excluded.newest_slot, oldest_slot=excluded.oldest_slot,
                 provider_exhausted=excluded.provider_exhausted,
                 contiguous_boundary_proven=excluded.contiguous_boundary_proven,
                 mutable_head=excluded.mutable_head, updated_at=CURRENT_TIMESTAMP""",
            (run[0], run_id, state, reason, newest_signature, oldest_signature,
             newest_slot, oldest_slot, int(provider_exhausted),
             int(contiguous_boundary_proven), int(run[1])),
        )
        conn.commit()

