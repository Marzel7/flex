"""Review-only Deep route leads; never canonical operation assignments.

Schema creation belongs to a separately gated migration. The writer functions
require an existing transaction and never commit, acquire a lease, or run DDL.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from src.ops.watchtower_deep_prospective import DEEP_OPERATOR_ID


SCHEMA = """
CREATE TABLE IF NOT EXISTS wt_deep_route_review_leads (
    mint TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state='REVIEW_CANDIDATE'),
    authority TEXT NOT NULL CHECK(authority='REVIEW_ONLY'),
    route_json TEXT NOT NULL,
    route_digest TEXT NOT NULL,
    first_observed_at INTEGER NOT NULL,
    last_observed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wdrrl_operator_observed
ON wt_deep_route_review_leads(operator_id,last_observed_at DESC);
"""


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
