"""X65.21 — Provisioning Wallet: additive persistence of the proven,
previously-uncaptured intermediate wallet between a WATCHTOWER SubProvider and
Creator (X65.19: 42/42 resolvable cascade-confirmed launches show
SubProvider -> Wallet P -> Creator, atomic within one transaction, via either a
WSOL wrap-close or a seeded-account-close).

Purely additive, per X65.21's own constraint:
  - `wt_provisioning_edges` (edge_type IN TREASURY_TO_SUBPROV, SUBPROV_TO_CREATOR)
    is NOT modified -- no new edge_type value is added to it, no row it already
    holds is changed. Every existing reader of that table keeps working exactly
    as before.
  - A brand-new table, `wt_provisioning_wallets`, holds one row per launch
    (mint), since Wallet P is single-use per launch (X65.19). It records the
    wallet address plus the exact evidence used to recover it, and an explicit
    `reconstructed` flag so backfilled rows are always distinguishable from
    rows captured live.
  - A brand-new table, `wt_provisioning_wallet_edges`, holds the two new edge
    types (SUBPROV_TO_PROVISIONING, PROVISIONING_TO_CREATOR) using the exact
    same edge shape/upsert convention as wt_provisioning_edges, so any future
    reader can treat both tables uniformly if it chooses to -- but nothing
    reads from this table today except the new code this module adds.

This module writes no attribution, no operator identity, no confidence score,
and calls no RPC of its own except where explicitly invoked by the backfill
script (a separate, opt-in process) -- mirroring the read/write discipline
already established in provisioning_edges.py.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Optional

DDL = """
CREATE TABLE IF NOT EXISTS wt_provisioning_wallets (
    mint                     TEXT PRIMARY KEY,
    subprov_wallet           TEXT NOT NULL,
    creator_wallet           TEXT NOT NULL,
    provisioning_wallet      TEXT NOT NULL,
    mechanism                TEXT NOT NULL CHECK(mechanism IN ('WSOL_WRAP_CLOSE','SEEDED_ACCOUNT_CLOSE')),
    funding_signature        TEXT,
    recovery_method          TEXT NOT NULL CHECK(recovery_method IN (
                                 'PERSISTED_SIGNATURE_DECODE',
                                 'BOUNDED_SIGNATURE_LOOKUP',
                                 'LIVE_CAPTURE'
                             )),
    reconstructed            INTEGER NOT NULL DEFAULT 0 CHECK(reconstructed IN (0,1)),
    recorded_at              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wpw_subprov ON wt_provisioning_wallets(subprov_wallet);
CREATE INDEX IF NOT EXISTS ix_wpw_creator ON wt_provisioning_wallets(creator_wallet);
CREATE INDEX IF NOT EXISTS ix_wpw_provisioning ON wt_provisioning_wallets(provisioning_wallet);

-- Additive edge table, same shape/upsert convention as wt_provisioning_edges,
-- but a SEPARATE table so wt_provisioning_edges' own schema, CHECK
-- constraint, and every existing reader are left completely untouched.
CREATE TABLE IF NOT EXISTS wt_provisioning_wallet_edges (
    edge_id                 TEXT PRIMARY KEY,
    edge_type               TEXT NOT NULL CHECK(edge_type IN ('SUBPROV_TO_PROVISIONING','PROVISIONING_TO_CREATOR')),
    from_wallet              TEXT NOT NULL,
    to_wallet                TEXT NOT NULL,
    source_mint              TEXT NOT NULL,
    first_observed_by_flex   INTEGER NOT NULL,
    last_observed_by_flex    INTEGER NOT NULL,
    observation_count        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(edge_type, from_wallet, to_wallet)
);
CREATE INDEX IF NOT EXISTS ix_wpwe_from ON wt_provisioning_wallet_edges(from_wallet, edge_type);
CREATE INDEX IF NOT EXISTS ix_wpwe_to ON wt_provisioning_wallet_edges(to_wallet, edge_type);
"""

EDGE_SUBPROV_TO_PROVISIONING = "SUBPROV_TO_PROVISIONING"
EDGE_PROVISIONING_TO_CREATOR = "PROVISIONING_TO_CREATOR"

RECOVERY_PERSISTED_SIGNATURE_DECODE = "PERSISTED_SIGNATURE_DECODE"
RECOVERY_BOUNDED_SIGNATURE_LOOKUP = "BOUNDED_SIGNATURE_LOOKUP"
RECOVERY_LIVE_CAPTURE = "LIVE_CAPTURE"


def ensure_schema(conn) -> None:
    conn.executescript(DDL)


def _edge_id(edge_type: str, from_wallet: str, to_wallet: str) -> str:
    digest = hashlib.sha256(f"{edge_type}|{from_wallet}|{to_wallet}".encode()).hexdigest()
    return f"pwedge_{digest[:32]}"


def _upsert_wallet_edge(conn, *, edge_type: str, from_wallet: str, to_wallet: str,
                         source_mint: str, now: int) -> None:
    edge_id = _edge_id(edge_type, from_wallet, to_wallet)
    conn.execute(
        "INSERT INTO wt_provisioning_wallet_edges "
        "(edge_id, edge_type, from_wallet, to_wallet, source_mint, "
        " first_observed_by_flex, last_observed_by_flex, observation_count) "
        "VALUES (?,?,?,?,?,?,?,1) "
        "ON CONFLICT(edge_type, from_wallet, to_wallet) DO UPDATE SET "
        "  last_observed_by_flex = excluded.last_observed_by_flex, "
        "  observation_count = observation_count + 1, "
        "  source_mint = excluded.source_mint",
        (edge_id, edge_type, from_wallet, to_wallet, source_mint, now, now),
    )


def record_provisioning_wallet(
    conn,
    *,
    mint: str,
    subprov_wallet: str,
    creator_wallet: str,
    provisioning_wallet: str,
    mechanism: str,
    recovery_method: str,
    funding_signature: Optional[str] = None,
    reconstructed: bool = False,
) -> dict[str, Any]:
    """Record one launch's proven Provisioning Wallet (X65.19). Idempotent on
    `mint` -- calling this again for the same mint updates in place rather
    than duplicating (ON CONFLICT DO UPDATE), so the historical backfill can
    be re-run safely and live capture can co-exist with a prior backfilled row
    without creating a second record for the same launch.

    Writes no attribution, no confidence score, no operator identity -- pure
    structural fact capture, mirroring provisioning_edges.py's own discipline.
    """
    ensure_schema(conn)
    if not (subprov_wallet and creator_wallet and provisioning_wallet):
        return {"recorded": False, "reason": "missing_wallet"}
    if provisioning_wallet in (subprov_wallet, creator_wallet):
        # X65.19's own proof standard: Wallet P must be distinct from both.
        # Never record a row that would violate the proven shape.
        return {"recorded": False, "reason": "provisioning_wallet_not_distinct"}
    now = int(time.time())
    conn.execute(
        "INSERT INTO wt_provisioning_wallets "
        "(mint, subprov_wallet, creator_wallet, provisioning_wallet, mechanism, "
        " funding_signature, recovery_method, reconstructed, recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(mint) DO UPDATE SET "
        "  subprov_wallet = excluded.subprov_wallet, "
        "  creator_wallet = excluded.creator_wallet, "
        "  provisioning_wallet = excluded.provisioning_wallet, "
        "  mechanism = excluded.mechanism, "
        "  funding_signature = COALESCE(excluded.funding_signature, funding_signature), "
        "  recovery_method = excluded.recovery_method, "
        "  reconstructed = excluded.reconstructed",
        (mint, subprov_wallet, creator_wallet, provisioning_wallet, mechanism,
         funding_signature, recovery_method, 1 if reconstructed else 0, now),
    )
    _upsert_wallet_edge(conn, edge_type=EDGE_SUBPROV_TO_PROVISIONING,
                        from_wallet=subprov_wallet, to_wallet=provisioning_wallet,
                        source_mint=mint, now=now)
    _upsert_wallet_edge(conn, edge_type=EDGE_PROVISIONING_TO_CREATOR,
                        from_wallet=provisioning_wallet, to_wallet=creator_wallet,
                        source_mint=mint, now=now)
    return {"recorded": True}


def provisioning_wallet_for_edge(conn, *, subprov_wallet: str, creator_wallet: str) -> Optional[str]:
    """Read-only: the persisted Provisioning Wallet for a specific
    subprov->creator pair, if one has been recorded. Used by the lineage API
    to extend the existing Treasury->SubProvider->Creator chain additively --
    callers that don't invoke this function see no behavior change at all."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT provisioning_wallet FROM wt_provisioning_wallets "
        "WHERE subprov_wallet=? AND creator_wallet=? LIMIT 1",
        (subprov_wallet, creator_wallet),
    ).fetchone()
    return row[0] if row else None


def provisioning_wallet_for_mint(conn, mint: str) -> Optional[dict[str, Any]]:
    """Read-only: the full persisted Provisioning Wallet record for one mint,
    or None if not yet recovered."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT mint, subprov_wallet, creator_wallet, provisioning_wallet, mechanism, "
        "funding_signature, recovery_method, reconstructed, recorded_at "
        "FROM wt_provisioning_wallets WHERE mint=?", (mint,),
    ).fetchone()
    if not row:
        return None
    cols = ["mint", "subprov_wallet", "creator_wallet", "provisioning_wallet", "mechanism",
            "funding_signature", "recovery_method", "reconstructed", "recorded_at"]
    return dict(zip(cols, row))
