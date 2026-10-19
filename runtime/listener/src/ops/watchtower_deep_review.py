"""Review-only Deep route leads; never canonical operation assignments.

Schema creation belongs to a separately gated migration. The writer functions
require an existing transaction and never commit, acquire a lease, or run DDL.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

from src.ops.watchtower_deep_prospective import (
    DEEP_OPERATOR_ID, DESTINATION_INDEX, DESTINATION_INDEX_DDL,
    assess_prospective_route,
)


REVIEW_TABLE_DDL = """CREATE TABLE IF NOT EXISTS wt_deep_route_review_leads (
    mint TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state='REVIEW_CANDIDATE'),
    authority TEXT NOT NULL CHECK(authority='REVIEW_ONLY'),
    route_json TEXT NOT NULL,
    route_digest TEXT NOT NULL,
    first_observed_at INTEGER NOT NULL,
    last_observed_at INTEGER NOT NULL
);"""
REVIEW_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_wdrrl_operator_observed "
    "ON wt_deep_route_review_leads(operator_id,last_observed_at DESC)"
)
SWEEP_INDEX = "ix_wbq_deep_review_page"
SWEEP_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_wbq_deep_review_page "
    "ON wt_walkback_queue(funder_block_time DESC,mint DESC) "
    "WHERE status='complete' AND intelligence_outcome='LINEAGE_GAP' "
    "AND funding_mechanism='WSOL_WRAP_CLOSE' "
    "AND funder_amount_sol>1.1120385 AND funder_amount_sol<1.1120395"
)
SCHEMA = REVIEW_TABLE_DDL + "\n" + REVIEW_INDEX_DDL + ";\n" + SWEEP_INDEX_DDL + ";"


def migrate_review_schema(conn: sqlite3.Connection) -> None:
    """DDL under the caller's shared writer lane; no implicit commit.

    This must never run from Walkback startup or a read path. A separately
    parity-gated one-shot migration owns the transaction and rollback.
    """
    conn.execute(DESTINATION_INDEX_DDL)
    conn.execute(SWEEP_INDEX_DDL)
    conn.execute(REVIEW_TABLE_DDL)
    conn.execute(REVIEW_INDEX_DDL)


def validate_review_schema(conn: sqlite3.Connection) -> bool:
    """Metadata-only check for the exact live read/write contract."""
    destination = [row[2] for row in conn.execute(f"PRAGMA index_info({DESTINATION_INDEX})")]
    lead_columns = {row[1] for row in conn.execute("PRAGMA table_info(wt_deep_route_review_leads)")}
    lead_index = [row[2] for row in conn.execute("PRAGMA index_info(ix_wdrrl_operator_observed)")]
    sweep_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (SWEEP_INDEX,),
    ).fetchone()
    return (destination == ["transfer_destination", "transfer_lamports"] and
            sweep_sql is not None and
            sweep_sql[0] == SWEEP_INDEX_DDL.replace("IF NOT EXISTS ", "") and
            lead_index == ["operator_id", "last_observed_at"] and
            {"mint", "operator_id", "state", "authority", "route_json", "route_digest",
             "first_observed_at", "last_observed_at"}.issubset(lead_columns))


def assess_review_readonly(
    db_path: str, mint: str, *, deadline_seconds: float = 0.25,
) -> dict:
    """Open and close a separate bounded read-only connection before publishing.

    A caller must never hold the application writer lease while invoking this.
    SQLite's progress handler interrupts an unexpectedly expensive statement;
    no production connection's progress handler or transaction is modified.
    """
    if deadline_seconds <= 0:
        raise ValueError("deadline_seconds must be positive")
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as read:
        if not validate_review_schema(read):
            return {"state": "DEFERRED", "reason": "review_schema_not_current",
                    "authority": "NONE"}
        deadline = time.monotonic() + deadline_seconds
        read.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        try:
            return assess_prospective_route(read, mint, require_index=True)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                return {"state": "DEFERRED", "reason": "read_deadline",
                        "authority": "NONE"}
            raise


def review_sweep_page(
    conn: sqlite3.Connection, *, since: int, cursor: tuple[int, str] | None = None,
    limit: int = 8,
) -> list[tuple[str, int]]:
    """One bounded page of unowned completed launches, newest first.

    The caller may reset the cursor after an empty page. Repeated pages allow
    cross-mint role evidence added later to be noticed without synthetic RPC.
    """
    bounded = max(0, min(int(limit), 32))
    before_time, before_mint = cursor if cursor else (2**63 - 1, "~")
    return [
        (mint, int(funded_at)) for mint, funded_at in conn.execute(
            "SELECT q.mint,q.funder_block_time FROM wt_walkback_queue q "
            "WHERE q.status='complete' AND q.intelligence_outcome='LINEAGE_GAP' "
            "AND q.funding_mechanism='WSOL_WRAP_CLOSE' "
            "AND q.funder_amount_sol>1.1120385 AND q.funder_amount_sol<1.1120395 "
            "AND q.funder_block_time>=? "
            "AND (q.funder_block_time<? OR (q.funder_block_time=? AND q.mint<?)) "
            "AND NOT EXISTS(SELECT 1 FROM operator_launch_membership m WHERE m.mint=q.mint) "
            "AND NOT EXISTS(SELECT 1 FROM wt_watchtower_launches w WHERE w.mint=q.mint) "
            "AND NOT EXISTS(SELECT 1 FROM wt_deep_route_review_leads l WHERE l.mint=q.mint) "
            "ORDER BY q.funder_block_time DESC,q.mint DESC LIMIT ?",
            (int(since), int(before_time), int(before_time), before_mint, bounded),
        )
    ]


def run_review_sweep_page(
    db_path: str, publish: Callable[[str, dict], object], *,
    cursor: tuple[int, str] | None = None, now: int | None = None,
    limit: int = 8, page_deadline_seconds: float = 0.25,
) -> dict:
    """Revisit one small page after Walkback commits; publishing is injected.

    Read-only connections are closed before the first publish callback. The
    caller owns the shared writer lane, commit, rollback and error isolation.
    An empty page resets the cursor for the next cycle.
    """
    timestamp = int(time.time()) if now is None else int(now)
    if page_deadline_seconds <= 0:
        raise ValueError("page_deadline_seconds must be positive")
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as read:
        if not validate_review_schema(read):
            return {"status": "SCHEMA_NOT_READY", "cursor": cursor,
                    "checked": 0, "published": 0}
        deadline = time.monotonic() + page_deadline_seconds
        read.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        try:
            page = review_sweep_page(read, since=timestamp - 7 * 86400,
                                     cursor=cursor, limit=limit)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                return {"status": "PAGE_DEFERRED", "cursor": cursor,
                        "checked": 0, "published": 0}
            raise
    if not page:
        return {"status": "COMPLETE_PASS", "cursor": None,
                "checked": 0, "published": 0}
    published = 0
    for mint, _funded_at in page:
        assessment = assess_review_readonly(db_path, mint)
        if assessment["state"] == "REVIEW_CANDIDATE":
            publish(mint, assessment)
            published += 1
    return {"status": "PAGE", "cursor": (page[-1][1], page[-1][0]),
            "checked": len(page), "published": published}


def persist_review_lead(
    conn: sqlite3.Connection, mint: str, assessment: dict, *, now: int | None = None,
) -> dict:
    """Persist one reviewed route shape without affecting an operation or treasury."""
    if (assessment.get("state") != "REVIEW_CANDIDATE" or
            assessment.get("authority") != "REVIEW_ONLY" or
            assessment.get("automatic_membership_allowed") is not False or
            assessment.get("capital_continuity") != "UNQUALIFIED"):
        raise ValueError("assessment is not a review-only route candidate")
    route = assessment.get("route")
    if not isinstance(route, dict) or not all(
        isinstance(route.get(key), str) and route[key]
        for key in ("pool", "coordinator", "distribution", "pool_signature", "upper_signature")
    ):
        raise ValueError("route evidence is incomplete")
    if conn.execute("SELECT 1 FROM operator_launch_membership WHERE mint=?", (mint,)).fetchone():
        return {"action": "existing_assignment"}
    if conn.execute("SELECT 1 FROM wt_watchtower_launches WHERE mint=?", (mint,)).fetchone():
        return {"action": "watchtower_ledger_present"}
    existing = conn.execute(
        "SELECT operator_id FROM wt_deep_route_review_leads WHERE mint=?", (mint,),
    ).fetchone()
    if existing and existing[0] != DEEP_OPERATOR_ID:
        raise ValueError("review lead belongs to another operator")
    payload = json.dumps(route, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    timestamp = int(time.time()) if now is None else int(now)
    conn.execute(
        "INSERT INTO wt_deep_route_review_leads "
        "(mint,operator_id,state,authority,route_json,route_digest,first_observed_at,last_observed_at) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(mint) DO UPDATE SET route_json=excluded.route_json,"
        "route_digest=excluded.route_digest,last_observed_at=excluded.last_observed_at "
        "WHERE wt_deep_route_review_leads.operator_id=excluded.operator_id",
        (mint, DEEP_OPERATOR_ID, "REVIEW_CANDIDATE", "REVIEW_ONLY", payload, digest,
         timestamp, timestamp),
    )
    return {"action": "review_lead_recorded", "route_digest": digest}


def fetch_review_leads(conn: sqlite3.Connection, *, limit: int = 500) -> list[dict]:
    """Read-only operator-specific UI/API projection, never membership."""
    bounded = max(0, min(int(limit), 1000))
    return [
        {"mint": mint, "operator_id": operator_id, "state": state,
         "authority": authority, "route": json.loads(route_json),
         "first_observed_at": first_seen, "last_observed_at": last_seen}
        for mint, operator_id, state, authority, route_json, first_seen, last_seen
        in conn.execute(
            "SELECT mint,operator_id,state,authority,route_json,first_observed_at,last_observed_at "
            "FROM wt_deep_route_review_leads l "
            "WHERE l.operator_id=? "
            "AND NOT EXISTS(SELECT 1 FROM operator_launch_membership m WHERE m.mint=l.mint) "
            "AND NOT EXISTS(SELECT 1 FROM wt_watchtower_launches w WHERE w.mint=l.mint) "
            "ORDER BY last_observed_at DESC,mint LIMIT ?", (DEEP_OPERATOR_ID, bounded),
        )
    ]
