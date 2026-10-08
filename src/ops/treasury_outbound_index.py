"""Compact, review-only known-treasury outbound index for DEV-019.

The index accepts only already-retained, balance-verified direct SOL facts.
It is intentionally isolated from the live Walkback database and returns a
funding connection, never operation membership or attribution assignment.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import sqlite3
from typing import Iterable, Mapping


MAX_INDEX_BYTES = 10_000_000
KNOWN_TREASURY_FUNDING_PATH_CONFIRMED = "KNOWN_TREASURY_FUNDING_PATH_CONFIRMED"
AMBIGUOUS_KNOWN_TREASURY_RECIPIENT = "AMBIGUOUS_KNOWN_TREASURY_RECIPIENT"
NO_KNOWN_TREASURY_RECIPIENT = "NO_KNOWN_TREASURY_RECIPIENT"
INVALID_LINEAGE = "INVALID_LINEAGE"

SCHEMA = """
CREATE TABLE IF NOT EXISTS wt_known_treasury_outbounds (
 treasury TEXT NOT NULL,
 operation_association TEXT,
 recipient TEXT NOT NULL,
 signature TEXT NOT NULL,
 slot INTEGER NOT NULL,
 transaction_index INTEGER,
 instruction_index INTEGER,
 lamports INTEGER NOT NULL CHECK(lamports > 0),
 provenance TEXT NOT NULL,
 PRIMARY KEY(treasury,signature,instruction_index)
);
CREATE INDEX IF NOT EXISTS ix_wt_known_treasury_outbounds_recipient
 ON wt_known_treasury_outbounds(recipient,slot,transaction_index,instruction_index);
CREATE TABLE IF NOT EXISTS wt_known_treasury_intersections (
 lineage_id TEXT NOT NULL,
 treasury TEXT NOT NULL,
 outbound_signature TEXT NOT NULL,
 status TEXT NOT NULL,
 evidence_json TEXT NOT NULL,
 PRIMARY KEY(lineage_id,treasury,outbound_signature)
);
"""


@dataclass(frozen=True)
class LineageEdge:
    sender: str
    receiver: str
    signature: str
    slot: int
    transaction_index: int | None
    instruction_index: int | None
    lamports: int
    verified: bool = True
    route_semantics: str = "DIRECT"


def open_isolated_index(path: str) -> sqlite3.Connection:
    resolved = os.path.realpath(path)
    if not resolved.startswith("/private/tmp/"):
        raise ValueError("OUTBOUND_INDEX_MUST_BE_TEMPORARY")
    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _assert_bound(conn)
    return conn


def _assert_bound(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row[2] if row else ""
    if path and os.path.exists(path) and os.path.getsize(path) > MAX_INDEX_BYTES:
        raise ValueError("OUTBOUND_INDEX_SIZE_LIMIT_EXCEEDED")


def _compact(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > 16_384:
        raise ValueError("OUTBOUND_INTERSECTION_RECORD_TOO_LARGE")
    return encoded


def index_compact_outbound_fact(conn: sqlite3.Connection, *, fact: Mapping[str, object],
                                confirmed_treasuries: Iterable[str],
                                operation_associations: Mapping[str, str] | None = None) -> bool:
    """Index one compact retained fact only when its sender is confirmed."""
    confirmed = set(confirmed_treasuries)
    treasury = str(fact.get("sender") or "")
    recipient = str(fact.get("receiver") or "")
    signature = str(fact.get("signature") or "")
    slot, amount = fact.get("slot"), fact.get("lamports")
    if (treasury not in confirmed or not recipient or not signature or not isinstance(slot, int)
            or not isinstance(amount, int) or amount <= 0 or fact.get("route_semantics") != "DIRECT"
            or not bool(fact.get("balance_delta_verified"))):
        return False
    associations = dict(operation_associations or {})
    conn.execute(
        "INSERT OR IGNORE INTO wt_known_treasury_outbounds VALUES(?,?,?,?,?,?,?,?,?)",
        (treasury, associations.get(treasury), recipient, signature, slot,
         fact.get("transaction_index"), fact.get("instruction_index"), amount,
         str(fact.get("provenance") or "RETAINED_COMPACT_FACT")),
    )
    _assert_bound(conn)
    return True


def recipients(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT recipient,COUNT(DISTINCT treasury) AS treasury_count,COUNT(*) AS transfer_count "
        "FROM wt_known_treasury_outbounds GROUP BY recipient ORDER BY recipient"
    )]


def _ordered(edges: Iterable[LineageEdge]) -> list[LineageEdge] | None:
    chain = list(edges)
    if not chain:
        return None
    for index, edge in enumerate(chain):
        if (not edge.verified or edge.route_semantics != "DIRECT" or not edge.sender or not edge.receiver
                or not edge.signature or edge.slot < 0 or edge.lamports <= 0):
            return None
        if index and (chain[index - 1].receiver != edge.sender
                      or (edge.slot, edge.transaction_index or -1, edge.instruction_index or -1)
                      <= (chain[index - 1].slot, chain[index - 1].transaction_index or -1, chain[index - 1].instruction_index or -1)
                      or edge.lamports > chain[index - 1].lamports):
            return None
    return chain


def intersect_walkback_lineage(conn: sqlite3.Connection, *, lineage_id: str,
                               edges: Iterable[LineageEdge]) -> dict:
    """Intersect a forward-ordered Walkback chain with known treasury outbounds."""
    chain = _ordered(edges)
    if chain is None:
        return {"status": INVALID_LINEAGE, "canonical_writes": False, "operation_assignment": None}
    first = chain[0]
    rows = list(conn.execute(
        "SELECT * FROM wt_known_treasury_outbounds WHERE recipient=? "
        "AND (slot,COALESCE(transaction_index,-1),COALESCE(instruction_index,-1)) < (?,?,?) "
        "AND lamports>=? ORDER BY treasury,signature",
        (first.sender, first.slot, first.transaction_index or -1, first.instruction_index or -1, first.lamports),
    ))
    if not rows:
        return {"status": NO_KNOWN_TREASURY_RECIPIENT, "canonical_writes": False, "operation_assignment": None}
    treasury_set = {row["treasury"] for row in rows}
    if len(treasury_set) != 1:
        return {"status": AMBIGUOUS_KNOWN_TREASURY_RECIPIENT, "candidate_treasuries": sorted(treasury_set),
                "canonical_writes": False, "operation_assignment": None}
    outbound = dict(rows[0])
    evidence = {
        "treasury": outbound["treasury"], "operation_association": outbound["operation_association"],
        "outbound": outbound,
        "walkback_edges": [edge.__dict__ for edge in chain],
        "disposition": "TREASURY_FUNDING_CONNECTION_ONLY_REQUIRES_EXISTING_ATTRIBUTION_REVIEW",
    }
    conn.execute("INSERT OR IGNORE INTO wt_known_treasury_intersections VALUES(?,?,?,?,?)", (
        lineage_id, outbound["treasury"], outbound["signature"], KNOWN_TREASURY_FUNDING_PATH_CONFIRMED, _compact(evidence),
    ))
    _assert_bound(conn)
    return {"status": KNOWN_TREASURY_FUNDING_PATH_CONFIRMED, "treasury": outbound["treasury"],
            "operation_association": outbound["operation_association"], "evidence": evidence,
            "canonical_writes": False, "operation_assignment": None}


def walkback_attribution_read_interface(conn: sqlite3.Connection, *, lineage_id: str) -> list[dict]:
    """Compact read-only seam for an existing future attribution consumer."""
    return [dict(row) for row in conn.execute(
        "SELECT lineage_id,treasury,outbound_signature,status,evidence_json FROM wt_known_treasury_intersections WHERE lineage_id=? ORDER BY treasury,outbound_signature",
        (lineage_id,)
    )]
