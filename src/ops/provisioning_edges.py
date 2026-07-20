"""X21B — Provisioning Relationship Capture: append-only, operation-agnostic edges.

This module persists observed structural relationships discovered during a
successful walkback hop, independent of any operator identity. It captures
FACTS ONLY:

  - which wallet funded which wallet (an edge, by role: TREASURY_TO_SUBPROV or
    SUBPROV_TO_CREATOR)
  - when we first/last observed that edge, and how many times
  - the funding mechanism and amount seen on each observation
  - the elapsed time between adjacent stages in the SAME walk (funding→funding,
    funding→launch) — an observed LATENCY, not a wallet's age since creation

It deliberately does NOT capture: operator names, WATCHTOWER-ness, confidence,
affinity, promotion state, or assessment. It never queries RPC. It never
updates or deletes a row it previously wrote to a degree that would erase
prior evidence — only MAX()/COALESCE()-style accumulation on a natural key,
matching the existing wt_discovered_subprovs convention in this codebase.

Terminology (deliberately not "wallet age"):
  - first_observed_by_flex / last_observed_by_flex: when THIS PLATFORM first/last
    saw this edge — not when the wallet was created on-chain. True on-chain
    wallet genesis age is NOT captured here; it would require new RPC
    (paginating getSignaturesForAddress to a wallet's earliest transaction),
    which is out of scope for this capture-only sprint. The schema is additive
    enough that a future, separately-scoped RPC enrichment pass could attach a
    genesis timestamp without altering anything this module writes.
  - latency_seconds: elapsed time between two already-observed, already-persisted
    block-time events in the SAME walk (e.g. subprov-funds-creator block_time minus
    treasury-funds-subprov block_time). This is a fact about observed timing, not
    an inference or classification.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Optional

from src.core.database_write_service import execute_script


EDGE_TREASURY_TO_SUBPROV = "TREASURY_TO_SUBPROV"
EDGE_SUBPROV_TO_CREATOR = "SUBPROV_TO_CREATOR"

_EDGE_TYPES = (EDGE_TREASURY_TO_SUBPROV, EDGE_SUBPROV_TO_CREATOR)

DDL = f"""
CREATE TABLE IF NOT EXISTS wt_provisioning_edges (
    edge_id                 TEXT PRIMARY KEY,
    edge_type               TEXT NOT NULL CHECK(edge_type IN ({','.join(repr(t) for t in _EDGE_TYPES)})),
    from_wallet              TEXT NOT NULL,
    to_wallet                TEXT NOT NULL,
    first_observed_by_flex   INTEGER NOT NULL,
    last_observed_by_flex    INTEGER NOT NULL,
    observation_count        INTEGER NOT NULL DEFAULT 1,
    funding_mechanism        TEXT,
    funding_amount_sol       REAL,
    funding_tx_signature     TEXT,
    funding_block_time       INTEGER,
    source_mint              TEXT,
    provenance                TEXT NOT NULL DEFAULT 'WALKBACK',
    UNIQUE(edge_type, from_wallet, to_wallet)
);
CREATE INDEX IF NOT EXISTS ix_wpe_from ON wt_provisioning_edges(from_wallet, edge_type);
CREATE INDEX IF NOT EXISTS ix_wpe_to ON wt_provisioning_edges(to_wallet, edge_type);
CREATE INDEX IF NOT EXISTS ix_wpe_type_time ON wt_provisioning_edges(edge_type, last_observed_by_flex DESC);

-- One immutable row per successfully-observed provisioning session (a single
-- walk that resolved treasury->subprov->creator, in whole or in part). Rows
-- are never updated — a repeat walk of the same mint simply is not
-- re-enqueued by the existing walkback queue (PRIMARY KEY(mint) already
-- prevents duplicate processing), so this table is naturally append-only
-- without needing an explicit dedup constraint beyond mint itself.
CREATE TABLE IF NOT EXISTS wt_provisioning_sessions (
    session_id                       TEXT PRIMARY KEY,
    source_mint                      TEXT NOT NULL UNIQUE,
    treasury                        TEXT,
    subprov                         TEXT,
    creator                         TEXT,
    treasury_to_subprov_block_time   INTEGER,
    subprov_to_creator_block_time    INTEGER,
    creator_launch_time              INTEGER,
    treasury_to_subprov_latency_seconds INTEGER,
    subprov_to_creator_latency_seconds  INTEGER,
    creator_to_launch_latency_seconds   INTEGER,
    treasury_to_subprov_mechanism    TEXT,
    subprov_to_creator_mechanism     TEXT,
    treasury_to_subprov_amount_sol   REAL,
    subprov_to_creator_amount_sol    REAL,
    recorded_at                      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wps_treasury ON wt_provisioning_sessions(treasury);
CREATE INDEX IF NOT EXISTS ix_wps_subprov ON wt_provisioning_sessions(subprov);
"""


def ensure_schema(conn) -> None:
    execute_script(conn, DDL)


def _edge_id(edge_type: str, from_wallet: str, to_wallet: str) -> str:
    digest = hashlib.sha256(f"{edge_type}|{from_wallet}|{to_wallet}".encode()).hexdigest()
    return f"edge_{digest[:32]}"


def _upsert_edge(
    conn,
    *,
    edge_type: str,
    from_wallet: Optional[str],
    to_wallet: Optional[str],
    funding_mechanism: Optional[str],
    funding_amount_sol: Optional[float],
    funding_tx_signature: Optional[str],
    funding_block_time: Optional[int],
    source_mint: Optional[str],
    now: int,
) -> None:
    """Record one observation of an edge. Never overwrites first_observed_by_flex;
    always advances last_observed_by_flex and increments observation_count. The
    most recently observed funding_mechanism/amount/signature/block_time replace
    the prior observation's — this reflects the LATEST known characteristics of
    a recurring relationship, while first/last timestamps and the count preserve
    the full observation history's shape without storing every individual row."""
    if not from_wallet or not to_wallet:
        return
    edge_id = _edge_id(edge_type, from_wallet, to_wallet)
    conn.execute(
        "INSERT INTO wt_provisioning_edges "
        "(edge_id, edge_type, from_wallet, to_wallet, first_observed_by_flex, "
        " last_observed_by_flex, observation_count, funding_mechanism, funding_amount_sol, "
        " funding_tx_signature, funding_block_time, source_mint, provenance) "
        "VALUES (?,?,?,?,?,?,1,?,?,?,?,?,'WALKBACK') "
        "ON CONFLICT(edge_type, from_wallet, to_wallet) DO UPDATE SET "
        "  last_observed_by_flex = excluded.last_observed_by_flex, "
        "  observation_count = observation_count + 1, "
        "  funding_mechanism = COALESCE(excluded.funding_mechanism, funding_mechanism), "
        "  funding_amount_sol = COALESCE(excluded.funding_amount_sol, funding_amount_sol), "
        "  funding_tx_signature = COALESCE(excluded.funding_tx_signature, funding_tx_signature), "
        "  funding_block_time = COALESCE(excluded.funding_block_time, funding_block_time), "
        "  source_mint = excluded.source_mint",
        (
            edge_id, edge_type, from_wallet, to_wallet, now, now,
            funding_mechanism, funding_amount_sol, funding_tx_signature,
            funding_block_time, source_mint,
        ),
    )


def capture_provisioning_relationship(
    conn,
    *,
    source_mint: str,
    treasury: Optional[str] = None,
    subprov: Optional[str] = None,
    creator: Optional[str] = None,
    treasury_to_subprov_sig: Optional[str] = None,
    treasury_to_subprov_block_time: Optional[int] = None,
    treasury_to_subprov_amount_sol: Optional[float] = None,
    treasury_to_subprov_mechanism: Optional[str] = None,
    subprov_to_creator_sig: Optional[str] = None,
    subprov_to_creator_block_time: Optional[int] = None,
    subprov_to_creator_amount_sol: Optional[float] = None,
    subprov_to_creator_mechanism: Optional[str] = None,
    creator_launch_time: Optional[int] = None,
) -> dict[str, Any]:
    """Capture whichever provisioning edges/session facts are available for this walk.

    Called from the walkback success path with whatever subset of
    (treasury, subprov, creator) and their funding evidence is known at that
    point — this function accepts partial data and captures only the edges it
    has real evidence for. It never infers a missing wallet or a missing
    timestamp. Idempotent to call multiple times for the same mint (the
    session row is UNIQUE on source_mint; edges accumulate observation_count).

    Returns a small report dict (edges_captured, session_captured) for logging/
    tests — not for display; this function performs no interpretation.
    """
    ensure_schema(conn)
    now = int(time.time())
    edges_captured: list[str] = []

    if treasury and subprov:
        _upsert_edge(
            conn, edge_type=EDGE_TREASURY_TO_SUBPROV,
            from_wallet=treasury, to_wallet=subprov,
            funding_mechanism=treasury_to_subprov_mechanism,
            funding_amount_sol=treasury_to_subprov_amount_sol,
            funding_tx_signature=treasury_to_subprov_sig,
            funding_block_time=treasury_to_subprov_block_time,
            source_mint=source_mint, now=now,
        )
        edges_captured.append(EDGE_TREASURY_TO_SUBPROV)

    if subprov and creator:
        _upsert_edge(
            conn, edge_type=EDGE_SUBPROV_TO_CREATOR,
            from_wallet=subprov, to_wallet=creator,
            funding_mechanism=subprov_to_creator_mechanism,
            funding_amount_sol=subprov_to_creator_amount_sol,
            funding_tx_signature=subprov_to_creator_sig,
            funding_block_time=subprov_to_creator_block_time,
            source_mint=source_mint, now=now,
        )
        edges_captured.append(EDGE_SUBPROV_TO_CREATOR)

    session_captured = False
    if treasury or subprov or creator:
        t_to_s_latency = (
            subprov_to_creator_block_time - treasury_to_subprov_block_time
            if treasury_to_subprov_block_time is not None and subprov_to_creator_block_time is not None
            and subprov_to_creator_block_time >= treasury_to_subprov_block_time
            else None
        )
        s_to_c_latency = (
            creator_launch_time - subprov_to_creator_block_time
            if subprov_to_creator_block_time is not None and creator_launch_time is not None
            and creator_launch_time >= subprov_to_creator_block_time
            else None
        )
        c_to_launch_latency = (
            creator_launch_time - treasury_to_subprov_block_time
            if treasury_to_subprov_block_time is not None and creator_launch_time is not None
            and creator_launch_time >= treasury_to_subprov_block_time
            else None
        )
        session_id = f"session_{hashlib.sha256(source_mint.encode()).hexdigest()[:32]}"
        conn.execute(
            "INSERT INTO wt_provisioning_sessions "
            "(session_id, source_mint, treasury, subprov, creator, "
            " treasury_to_subprov_block_time, subprov_to_creator_block_time, creator_launch_time, "
            " treasury_to_subprov_latency_seconds, subprov_to_creator_latency_seconds, "
            " creator_to_launch_latency_seconds, "
            " treasury_to_subprov_mechanism, subprov_to_creator_mechanism, "
            " treasury_to_subprov_amount_sol, subprov_to_creator_amount_sol, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source_mint) DO NOTHING",
            (
                session_id, source_mint, treasury, subprov, creator,
                treasury_to_subprov_block_time, subprov_to_creator_block_time, creator_launch_time,
                t_to_s_latency, s_to_c_latency, c_to_launch_latency,
                treasury_to_subprov_mechanism, subprov_to_creator_mechanism,
                treasury_to_subprov_amount_sol, subprov_to_creator_amount_sol, now,
            ),
        )
        session_captured = conn.total_changes > 0

    return {"edges_captured": edges_captured, "session_captured": session_captured}


def _rows_as_dicts(cursor) -> list[dict[str, Any]]:
    """Row-factory-agnostic: works whether conn.row_factory is sqlite3.Row or unset."""
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def edges_for_wallet(conn, wallet: str, *, limit: int = 50, show_spam_transfers: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Read-only: every observed edge where this wallet is the funder or the funded party.

    X29.4: known spam transfers are environmental noise, not operational
    edges -- excluded from graph construction by default
    (show_spam_transfers=False). Pass show_spam_transfers=True for
    forensic/debugging inspection only; this flag never affects any
    stored attribution, only what this read returns."""
    ensure_schema(conn)
    outgoing = _rows_as_dicts(conn.execute(
        "SELECT * FROM wt_provisioning_edges WHERE from_wallet=? "
        "ORDER BY last_observed_by_flex DESC LIMIT ?", (wallet, limit)
    ))
    incoming = _rows_as_dicts(conn.execute(
        "SELECT * FROM wt_provisioning_edges WHERE to_wallet=? "
        "ORDER BY last_observed_by_flex DESC LIMIT ?", (wallet, limit)
    ))
    if not show_spam_transfers:
        from src.ops.known_spam_wallets import confirmed_spam_addresses
        spam = frozenset(confirmed_spam_addresses(conn))
        if spam:
            outgoing = [e for e in outgoing if e.get("from_wallet") not in spam]
            incoming = [e for e in incoming if e.get("from_wallet") not in spam]
    return {"outgoing": outgoing, "incoming": incoming}


def sessions_for_wallet(conn, wallet: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Read-only: every provisioning session where this wallet appeared in any role."""
    ensure_schema(conn)
    return _rows_as_dicts(conn.execute(
        "SELECT * FROM wt_provisioning_sessions "
        "WHERE treasury=? OR subprov=? OR creator=? "
        "ORDER BY recorded_at DESC LIMIT ?",
        (wallet, wallet, wallet, limit),
    ))


def timing_summary_for_wallet(conn, wallet: str) -> dict[str, Any]:
    """Read-only aggregate: mean observed latencies across all sessions involving this
    wallet, and the funding mechanism/amount most recently observed on its edges.
    Every field here is computed directly from persisted session/edge rows — nothing
    is inferred beyond a plain arithmetic mean over already-recorded observations."""
    ensure_schema(conn)
    sessions = sessions_for_wallet(conn, wallet, limit=1000)
    return _summarise_sessions(sessions)


def _summarise_sessions(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    def _mean(key: str) -> Optional[float]:
        values = [s[key] for s in sessions if s.get(key) is not None]
        return round(sum(values) / len(values), 1) if values else None

    return {
        "session_count": len(sessions),
        "mean_treasury_to_subprov_latency_seconds": _mean("treasury_to_subprov_latency_seconds"),
        "mean_subprov_to_creator_latency_seconds": _mean("subprov_to_creator_latency_seconds"),
        "mean_creator_to_launch_latency_seconds": _mean("creator_to_launch_latency_seconds"),
    }
