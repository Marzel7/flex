#!/usr/bin/env python3
"""One-shot, provider-free retirement of the legacy creator funding graph.

This migration intentionally preserves only audited, non-authoritative legacy
exceptions.  It never writes those rows into ``creator_funders``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_ID = "creator-funding-graph-retirement-v1"
EXCEPTION_STATUS = "INSUFFICIENT_EVIDENCE"
SOURCE_TABLE = "creator_funding_graph"

# Frozen from CREATOR_FUNDING_GRAPH_FINAL_RECONCILIATION_QUALIFIED. These are
# historical observations, not direct-funding facts and never become canonical.
LEGACY_EXCEPTIONS = (
    ("2DDyLChQ1rkfZt6QwzXUv76c45gYfHL6X1DVwgXNyMty", "DwdrYTtTWHfnfJBiN2RH6EgPbquDQLjZTfTwpykPEq1g", 1.306491154, "2026-08-10T19:04:24.490705"),
    ("HfdAzmhsRWgMxMtv29VrQFnbqsSv4wB74x8GYr9cde8j", "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk", 0.001626076, "2026-08-13T07:14:35.724566"),
    ("GU59xnit74AxBBBmdcE9gYGH4czCBHd6vsKEA5ewL5vw", "H58xoEgJQ2B91Tk3ncEMNHvzhnvDUJETrQB8U1JwD5f8", 1.099725295, "2026-08-16T20:14:18.267696"),
    ("4pNX5QNxcJ8eRjzfTWbux7pnfhMTg7sP2kzFVNBzutvZ", "5XEm5bqYyzxhRJdTq1sgm6KJtgVf4KiGmoSbvNRu8Ec1", 0.177267419, "2026-08-16T21:42:09.330816"),
    ("4pNX5QNxcJ8eRjzfTWbux7pnfhMTg7sP2kzFVNBzutvZ", "62ow8zXwo9BDjFiGf8piNkFi7B5JPEGdZjMarJ3GQCqQ", 0.149265224, "2026-08-16T21:42:09.330816"),
    ("4pNX5QNxcJ8eRjzfTWbux7pnfhMTg7sP2kzFVNBzutvZ", "A2q44ra6hemMaYz4rv8A4Z4RAMkW4XKstshgG9iruEJy", 0.091049173, "2026-08-16T21:42:09.330816"),
    ("4pNX5QNxcJ8eRjzfTWbux7pnfhMTg7sP2kzFVNBzutvZ", "HdHkCZZtQkGfPVocpvBU4nTrwmP5bN8rGQpYruPLZtd8", 0.745799218, "2026-08-16T21:42:09.330816"),
    ("VJNXG7n4b9zzWpXnjmUc9Bnmznm2QyqHqWBDpBrHY6G", "FQVin4Ma2xpsgK5w4APm6U3vQPYrfv7ZYvCWxUmCJmH7", 0.520246435, "2026-09-02T05:08:15.360144"),
    ("CNQdiPY6XAU5hRoq8bRKgPveW19QRyungstvWFSXGznD", "4R7UGnAz29Wuyn3BVniGMA4Rr82R2ggpZquYAukxzFki", 0.01, "2026-09-02T06:57:10.048436"),
    ("6u4EtXXewJtBpeGqnhfRKJUR3tV56nUTzDqduYus8hLB", "2uPFJbcK9X4w93C3uE9iJzb9vqENbPPM19ZpvH17KgQY", 492.999994999, "2026-09-02T09:01:07.629753"),
    ("B8dc6dVcvLyAZr1jFfaVCT4v8vWi6KBCjz5Z6LdQQc3i", "52wHK4NdRNsX3z42QoBSSi6FwfQ3iStWMEwQYeTf49cC", 199.905484904, "2026-09-02T22:58:32.091679"),
    ("2EAKVS4ZdmBZs26yrTcSYdXfShRGzXi2h5C9ZQUao8c3", "F5VQWYbueTDq5oyfyrZTbnWWxS2pCAhuK5KPM49h7ZUL", 1300.0, "2026-09-02T23:56:42.570228"),
)


def _exception_id(creator: str, funder: str) -> str:
    return hashlib.sha256(f"{SOURCE_TABLE}|{creator}|{funder}".encode()).hexdigest()


def _digest(rows: tuple[tuple[str, str, float, str], ...] = LEGACY_EXCEPTIONS) -> str:
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def retire(db_path: str | Path, *, now: str | None = None) -> dict[str, Any]:
    """Persist frozen exceptions and retire graph/view/index state atomically."""
    preserved_at = now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    conn = sqlite3.connect(str(db_path), timeout=60, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        graph_present = _has_table(conn, SOURCE_TABLE)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_funding_graph_legacy_exceptions (
                exception_id TEXT PRIMARY KEY,
                creator_address TEXT NOT NULL,
                funder_address TEXT NOT NULL,
                graph_inbound_sol REAL NOT NULL,
                graph_first_seen TEXT NOT NULL,
                graph_last_seen TEXT NOT NULL,
                graph_inbound_tx_count INTEGER NOT NULL,
                provenance TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status = 'INSUFFICIENT_EVIDENCE'),
                canonical_authority INTEGER NOT NULL DEFAULT 0 CHECK(canonical_authority = 0),
                migration_run_id TEXT NOT NULL,
                preserved_at TEXT NOT NULL,
                UNIQUE(creator_address, funder_address, provenance)
            )
            """
        )
        if graph_present:
            for creator, funder, amount, first_seen in LEGACY_EXCEPTIONS:
                row = conn.execute(
                    """
                    SELECT inbound_sol, first_seen, last_seen, inbound_tx_count
                    FROM creator_funding_graph
                    WHERE creator_address=? AND funder_address=?
                    """,
                    (creator, funder),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"frozen graph exception missing: {creator}/{funder}")
                inbound_sol, observed_first, observed_last, tx_count = row
                if abs(float(inbound_sol) - amount) > 1e-9 or observed_first != first_seen:
                    raise RuntimeError(f"frozen graph exception drift: {creator}/{funder}")
                canonical = conn.execute(
                    "SELECT 1 FROM creator_funders WHERE creator_address=? AND funder_address=?",
                    (creator, funder),
                ).fetchone()
                if canonical is not None:
                    raise RuntimeError(f"exception became canonical: {creator}/{funder}")
                conn.execute(
                    """
                    INSERT INTO creator_funding_graph_legacy_exceptions
                    (exception_id, creator_address, funder_address, graph_inbound_sol,
                     graph_first_seen, graph_last_seen, graph_inbound_tx_count, provenance,
                     status, canonical_authority, migration_run_id, preserved_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    ON CONFLICT(exception_id) DO NOTHING
                    """,
                    (_exception_id(creator, funder), creator, funder, inbound_sol,
                     observed_first, observed_last, tx_count, SOURCE_TABLE,
                     EXCEPTION_STATUS, RUN_ID, preserved_at),
                )
            saved = conn.execute(
                "SELECT COUNT(*) FROM creator_funding_graph_legacy_exceptions WHERE migration_run_id=?",
                (RUN_ID,),
            ).fetchone()[0]
            if saved != len(LEGACY_EXCEPTIONS):
                raise RuntimeError(f"expected {len(LEGACY_EXCEPTIONS)} exceptions, found {saved}")
            for view in (
                "v_creator_graph_stats_24h",
                "v_creator_graph_top_creators",
                "v_creator_graph_frequent_funders",
            ):
                conn.execute(f"DROP VIEW IF EXISTS {view}")
            conn.execute("DROP TABLE creator_funding_graph")
            outcome = "retired"
        else:
            saved = conn.execute(
                "SELECT COUNT(*) FROM creator_funding_graph_legacy_exceptions WHERE migration_run_id=?",
                (RUN_ID,),
            ).fetchone()[0]
            if saved != len(LEGACY_EXCEPTIONS):
                raise RuntimeError("retired graph lacks complete frozen exception set")
            outcome = "already_retired"
        conn.commit()
        return {
            "outcome": outcome,
            "exception_count": saved,
            "exception_digest": _digest(),
            "graph_present_after": _has_table(conn, SOURCE_TABLE),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="database/flex_complete_database.db")
    args = parser.parse_args()
    print(json.dumps(retire(args.db), sort_keys=True))


if __name__ == "__main__":
    main()
