"""X65.69 — Reclassify historical CEX/infrastructure Provisioning Candidates
into Ecosystem Intelligence.

Background (X65.66/X65.67/X65.68): a subset of Provisioning Candidate
Subprovider attributions are independently verified as known exchange /
infrastructure wallets (src.utils.infra_mapping.is_known_account), not
genuine WATCHTOWER operational infrastructure. X65.67 established the
Treasury->Exchange transfer itself is legitimate, ordinary operator
behaviour; X65.68 stops NEW instances of this entering the live candidate
pipeline. This module handles the remaining historical population already
present before that fix landed.

This module is READ-ONLY with respect to every canonical/attribution table
(wt_confirmed_treasuries, wt_ops_v2, wt_ops_v2_wallets, wt_watchtower_launches,
attribution_evidence, wt_attribution_outcomes, wt_active_subprov_sessions,
wt_discovered_subprovs) -- it never inserts, updates, or deletes a row in any
of them. Its only write target is the new, additive
wt_ecosystem_exchange_interactions table, which is populated purely by
COPYING already-computed evidence. Nothing is deleted anywhere -- the
"reclassification" is exclusively a presentation-layer exclusion (a mint
whose Provisioning Candidate subprov is_known_account() is filtered out of
the candidate query and instead appears in Ecosystem Intelligence) plus an
additive evidence copy, never a mutation of the source evidence.

Selection query mirrors src.ops.campaign_classification._wrap_close_evidence_by_mint's
own walkback-evidence source exactly (the SAME candidate population the
Discovery UI's Provisioning Candidate table already computes from), restricted to:
  - mint NOT in wt_watchtower_launches (never touches a confirmed launch)
  - the mint's evidence_json.subprovisioners[0] IS a session-confirmed subprov
    (wt_active_subprov_sessions membership -- exactly campaign_classification.py's
    own "not a bare mention" rule, no heuristic invented here)
  - that subprov IS known_account() (the one and only registry check; no
    second registry, no inference)
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

from src.utils.infra_mapping import is_known_account, get_account_info, get_cex_info, get_custom_info

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OPS_DB_PATH = os.environ.get("OPS_V2_DB_PATH", os.path.join(_REPO_ROOT, "database", "wt_ops_v2.db"))

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wt_ecosystem_exchange_interactions (
    mint                    TEXT PRIMARY KEY,
    treasury_wallet         TEXT,
    exchange_wallet         TEXT NOT NULL,
    exchange_name           TEXT,
    exchange_category       TEXT,
    creator_wallet          TEXT,
    funding_mechanism       TEXT,
    funding_signature       TEXT,
    funding_amount          REAL,
    funding_time            INTEGER,
    walkback_confidence     TEXT,
    walkback_completed_at   INTEGER,
    walkback_evidence_json  TEXT,
    discovery_source        TEXT,
    reclassified_at         INTEGER NOT NULL,
    reclassification_reason TEXT NOT NULL
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA_SQL)
    conn.commit()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def find_historical_cex_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Read-only. Returns one row per candidate mint whose Provisioning
    Candidate subprov attribution is an independently-verified known
    exchange/infrastructure wallet. Never touches wt_watchtower_launches,
    wt_confirmed_treasuries, wt_ops_v2, wt_ops_v2_wallets, or
    attribution_evidence -- reads wt_attribution_outcomes and
    wt_active_subprov_sessions only, exactly mirroring
    campaign_classification._wrap_close_evidence_by_mint's own evidence
    source and its own "session-confirmed, not a bare mention" rule.
    """
    if not _table_exists(conn, "wt_attribution_outcomes"):
        return []

    confirmed_mints: set[str] = set()
    if _table_exists(conn, "wt_watchtower_launches"):
        confirmed_mints = {
            r[0] for r in conn.execute("SELECT mint FROM wt_watchtower_launches")
        }

    candidate_subprov_by_mint: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT mint, evidence_json, confidence, completed_at FROM wt_attribution_outcomes"
    ):
        mint = row["mint"]
        if mint in confirmed_mints:
            continue
        try:
            ev = json.loads(row["evidence_json"] or "{}")
        except (TypeError, ValueError):
            continue
        subprovs = ev.get("subprovisioners") or []
        if not subprovs:
            continue
        candidate_subprov_by_mint[mint] = {
            "subprov_wallet": subprovs[0],
            "treasuries": ev.get("treasuries") or [],
            "creator": ev.get("creator"),
            "funder_wallet": ev.get("funder_wallet"),
            "boundary": ev.get("boundary") or {},
            "walkback_confidence": row["confidence"],
            "walkback_completed_at": row["completed_at"],
            "evidence_json": row["evidence_json"],
        }

    if not candidate_subprov_by_mint:
        return []

    distinct_candidates = list({v["subprov_wallet"] for v in candidate_subprov_by_mint.values()})
    sessioned_subprovs: set[str] = set()
    if _table_exists(conn, "wt_active_subprov_sessions"):
        placeholders = ",".join("?" for _ in distinct_candidates)
        sessioned_subprovs = {
            r[0] for r in conn.execute(
                f"SELECT DISTINCT subprov_wallet FROM wt_active_subprov_sessions "
                f"WHERE subprov_wallet IN ({placeholders})", distinct_candidates,
            )
        }

    # One session row per subprov (earliest funding_time) for
    # signature/amount/timestamp/treasury/mechanism/discovery_source detail.
    session_by_subprov: dict[str, sqlite3.Row] = {}
    if sessioned_subprovs and _table_exists(conn, "wt_active_subprov_sessions"):
        placeholders = ",".join("?" for _ in sessioned_subprovs)
        for row in conn.execute(
            f"SELECT subprov_wallet, treasury_wallet, funding_signature, funding_amount, "
            f"funding_time, funding_mechanism, open_reason FROM wt_active_subprov_sessions "
            f"WHERE subprov_wallet IN ({placeholders}) ORDER BY funding_time ASC",
            list(sessioned_subprovs),
        ):
            session_by_subprov.setdefault(row["subprov_wallet"], row)

    results: list[dict[str, Any]] = []
    for mint, info in candidate_subprov_by_mint.items():
        subprov = info["subprov_wallet"]
        if subprov not in sessioned_subprovs:
            continue
        if not is_known_account(subprov):
            continue
        session = session_by_subprov.get(subprov)
        acct_info = get_account_info(subprov) or get_cex_info(subprov) or get_custom_info(subprov) or {}
        results.append({
            "mint": mint,
            "treasury_wallet": (session["treasury_wallet"] if session else None)
                or (info["treasuries"][0] if info["treasuries"] else None),
            "exchange_wallet": subprov,
            "exchange_name": acct_info.get("exchange") or acct_info.get("name"),
            "exchange_category": acct_info.get("category"),
            "creator_wallet": info["creator"],
            "funding_mechanism": session["funding_mechanism"] if session else None,
            "funding_signature": session["funding_signature"] if session else None,
            "funding_amount": session["funding_amount"] if session else None,
            "funding_time": session["funding_time"] if session else None,
            "walkback_confidence": info["walkback_confidence"],
            "walkback_completed_at": info["walkback_completed_at"],
            "walkback_evidence_json": info["evidence_json"],
            "discovery_source": (session["open_reason"] if session else None),
        })
    return results


def migrate_historical_cex_candidates(ops_db_path: str = OPS_DB_PATH) -> dict[str, Any]:
    """Writes ONLY to wt_ecosystem_exchange_interactions (idempotent upsert
    on mint). Does not touch any other table. Returns a before/after report."""
    conn = sqlite3.connect(ops_db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        before_count = conn.execute(
            "SELECT COUNT(*) FROM wt_ecosystem_exchange_interactions"
        ).fetchone()[0]

        candidates = find_historical_cex_candidates(conn)
        now = int(time.time())
        conn.execute("BEGIN IMMEDIATE")
        for c in candidates:
            conn.execute(
                """INSERT INTO wt_ecosystem_exchange_interactions
                     (mint, treasury_wallet, exchange_wallet, exchange_name, exchange_category,
                      creator_wallet, funding_mechanism, funding_signature, funding_amount,
                      funding_time, walkback_confidence, walkback_completed_at,
                      walkback_evidence_json, discovery_source, reclassified_at,
                      reclassification_reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(mint) DO UPDATE SET
                     treasury_wallet=excluded.treasury_wallet,
                     exchange_wallet=excluded.exchange_wallet,
                     exchange_name=excluded.exchange_name,
                     exchange_category=excluded.exchange_category,
                     creator_wallet=excluded.creator_wallet,
                     funding_mechanism=excluded.funding_mechanism,
                     funding_signature=excluded.funding_signature,
                     funding_amount=excluded.funding_amount,
                     funding_time=excluded.funding_time,
                     walkback_confidence=excluded.walkback_confidence,
                     walkback_completed_at=excluded.walkback_completed_at,
                     walkback_evidence_json=excluded.walkback_evidence_json,
                     discovery_source=excluded.discovery_source""",
                (c["mint"], c["treasury_wallet"], c["exchange_wallet"], c["exchange_name"],
                 c["exchange_category"], c["creator_wallet"], c["funding_mechanism"],
                 c["funding_signature"], c["funding_amount"], c["funding_time"],
                 c["walkback_confidence"], c["walkback_completed_at"],
                 c["walkback_evidence_json"], c["discovery_source"], now,
                 "KNOWN_INFRASTRUCTURE_REGISTRY_MATCH"),
            )
        conn.commit()

        after_count = conn.execute(
            "SELECT COUNT(*) FROM wt_ecosystem_exchange_interactions"
        ).fetchone()[0]
        return {
            "candidates_found": len(candidates),
            "before_count": before_count,
            "after_count": after_count,
            "newly_added": after_count - before_count,
        }
    finally:
        conn.close()


def list_ecosystem_exchange_interactions(
    ops_db_path: str = OPS_DB_PATH, *, window_seconds: int | None = None
) -> list[dict[str, Any]]:
    """Read-only accessor for the API route.

    X67.33 -- window_seconds filters on funding_time (the treasury->exchange
    funding event already displayed as "Observed"/"Funding Observed" in the
    UI), NOT reclassified_at (migration/copy time) or any other timestamp.
    None/absent/<=0 means no filter (the "All" view) -- the WHERE predicate
    is omitted entirely rather than using an artificial huge interval, so
    rows with funding_time=NULL are never silently excluded from "All."
    """
    conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "wt_ecosystem_exchange_interactions"):
            return []
        if window_seconds is None or window_seconds <= 0:
            rows = conn.execute(
                "SELECT * FROM wt_ecosystem_exchange_interactions ORDER BY funding_time DESC"
            ).fetchall()
        else:
            cutoff = int(time.time()) - window_seconds
            rows = conn.execute(
                "SELECT * FROM wt_ecosystem_exchange_interactions "
                "WHERE funding_time >= ? ORDER BY funding_time DESC",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
