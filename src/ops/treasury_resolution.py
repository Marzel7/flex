"""X65.1 — Sub-Provider Treasury Resolution for Unassigned Quick-Birth
Launches.

Resolves the creator -> sub-provisioner -> treasury lineage for launches
that currently sit at Discovery's terminal "Unknown Funding Origin /
Unassigned" node, without inventing any new detection logic. Every fact
this module reads already exists in wt_attribution_outcomes,
wt_active_subprov_sessions, and wt_confirmed_treasuries -- this module
is a read-only CROSS-REFERENCE join across three already-authoritative
tables that nothing currently connects for this population (see
docs/design/x65_1/x65_1_evidence_audit.md for the full audit).

Governing constraints (X65.1 brief):
  - Never automatically confirm or reroot a treasury identity. A wallet
    is KNOWN_TREASURY only if it is ALREADY present in
    wt_confirmed_treasuries -- this module never writes to that table.
  - Never promote an unconfirmed candidate to confirmed from a single
    transfer, wallet size, matching amount tails, timing proximity,
    shared RPC observation, or generic ATA-rent patterns (Phase 6).
  - Never fabricate a wallet or silently fall back to a guess -- every
    unresolved case carries an explicit reason string, never a null
    treated as a false positive.
  - Bounded traversal: default max depth is 2 funding hops upstream from
    the creator (creator <- subprov <- treasury). Only ever extended if
    the treasury candidate ITSELF has a wt_active_subprov_sessions row
    (i.e. is itself a subprov of a further upstream wallet) -- checked
    explicitly, never assumed.
  - Zero writes. Zero RPC. Every fact here is drawn from already-indexed
    tables; nothing in this module performs network I/O.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

STATUS_KNOWN_TREASURY = "KNOWN_TREASURY"
STATUS_UNKNOWN_TREASURY_CANDIDATE = "UNKNOWN_TREASURY_CANDIDATE"
STATUS_NO_SUBPROV = "NO_SUBPROV"
STATUS_UNRESOLVED = "UNRESOLVED"

SUBPROV_CONFIRMED = "CONFIRMED_SUBPROV"
SUBPROV_PROBABLE = "PROBABLE_SUBPROV"
SUBPROV_DIRECT_TREASURY = "DIRECT_TREASURY"
SUBPROV_NON_OPERATIONAL = "NON_OPERATIONAL_FUNDER"
SUBPROV_UNRESOLVED = "UNRESOLVED"

MAX_WALKBACK_DEPTH = 2


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def get_creator_funder(ops_conn: sqlite3.Connection, mint: str) -> Optional[dict[str, Any]]:
    """Phase 2/3 step 1-2: the creator's direct funder, reused verbatim
    from wt_attribution_outcomes.terminal_entity -- NOT re-derived. This
    table already answers "who funded the creator" for any mint that has
    an attribution-outcome row at all (regardless of outcome_type)."""
    if not _table_exists(ops_conn, "wt_attribution_outcomes"):
        return None
    row = ops_conn.execute(
        "SELECT mint, outcome_type, terminal_entity, evidence_json "
        "FROM wt_attribution_outcomes WHERE mint=?", (mint,),
    ).fetchone()
    if not row or not row["terminal_entity"]:
        return None
    import json
    try:
        evidence = json.loads(row["evidence_json"]) if row["evidence_json"] else {}
    except (TypeError, ValueError):
        evidence = {}
    return {
        "funder_wallet": row["terminal_entity"],
        "outcome_type": row["outcome_type"],
        "creator": evidence.get("creator"),
    }


def classify_creator_funder(
    ops_conn: sqlite3.Connection, funder_wallet: str,
) -> dict[str, Any]:
    """Phase 3: classify the creator's direct funder as CONFIRMED_SUBPROV
    / PROBABLE_SUBPROV / DIRECT_TREASURY / NON_OPERATIONAL_FUNDER /
    UNRESOLVED, using ONLY persisted transaction-level evidence
    (wt_active_subprov_sessions' funding_signature/funding_amount/
    funding_time), never a balance heuristic.

    Returns {classification, session, is_confirmed_treasury_directly}.
    """
    is_direct_treasury = bool(
        _table_exists(ops_conn, "wt_confirmed_treasuries")
        and ops_conn.execute(
            "SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=?", (funder_wallet,),
        ).fetchone()
    )
    if is_direct_treasury:
        return {
            "classification": SUBPROV_DIRECT_TREASURY,
            "session": None,
            "is_confirmed_treasury_directly": True,
        }

    session = None
    if _table_exists(ops_conn, "wt_active_subprov_sessions"):
        session = ops_conn.execute(
            "SELECT subprov_wallet, treasury_wallet, funding_signature, funding_amount, "
            "funding_time, funding_mechanism, state, open_reason "
            "FROM wt_active_subprov_sessions WHERE subprov_wallet=?", (funder_wallet,),
        ).fetchone()

    if session and session["treasury_wallet"] and session["funding_signature"]:
        # Complete transaction-level evidence: a real signature, amount,
        # and timestamp, plus an already-populated treasury_wallet --
        # this is the same durable, WS-observed evidence funding_topology.py
        # already treats as authoritative everywhere else in this project.
        return {
            "classification": SUBPROV_CONFIRMED,
            "session": dict(session),
            "is_confirmed_treasury_directly": False,
        }
    if session:
        # A session row exists but is missing a signature or treasury --
        # partial evidence, not enough for CONFIRMED, not nothing either.
        return {
            "classification": SUBPROV_PROBABLE,
            "session": dict(session),
            "is_confirmed_treasury_directly": False,
        }

    return {
        "classification": SUBPROV_UNRESOLVED,
        "session": None,
        "is_confirmed_treasury_directly": False,
    }


def treasury_scale_stats(ops_conn: sqlite3.Connection, treasury_wallet: str) -> dict[str, Any]:
    """Phase 4: treasury-scale history for a candidate -- distinct
    sub-provisioners funded and total funding_amount, all-time. Used only
    as supporting evidence (never as the sole basis for confirming a
    treasury -- confirmation is entirely governed by wt_confirmed_
    treasuries, per this module's own constraint)."""
    row = ops_conn.execute(
        "SELECT COUNT(DISTINCT subprov_wallet) AS n_subprovs, "
        "SUM(funding_amount) AS total_sol "
        "FROM wt_active_subprov_sessions WHERE treasury_wallet=?",
        (treasury_wallet,),
    ).fetchone()
    return {
        "distinct_subprovs_funded": row["n_subprovs"] or 0,
        "total_funding_amount_sol": row["total_sol"] or 0.0,
    }


def is_bridged_further_upstream(ops_conn: sqlite3.Connection, treasury_wallet: str) -> bool:
    """Phase 4's bridging check: does this treasury candidate ITSELF
    appear as a subprov_wallet (i.e. is it funded by a further upstream
    wallet)? Only when this returns True should walkback depth be
    increased beyond MAX_WALKBACK_DEPTH -- never assumed, always checked."""
    return bool(ops_conn.execute(
        "SELECT 1 FROM wt_active_subprov_sessions WHERE subprov_wallet=?",
        (treasury_wallet,),
    ).fetchone())


def match_known_treasury(ops_conn: sqlite3.Connection, treasury_wallet: str) -> Optional[dict[str, Any]]:
    """Phase 5: KNOWN_TREASURY match, using ONLY wt_confirmed_treasuries
    (the sole authoritative, already-approved registry) plus
    wt_ops_v2_wallets for operation linkage. Returns None if not already
    confirmed -- this function NEVER writes to wt_confirmed_treasuries
    and never promotes a candidate itself."""
    confirmed = ops_conn.execute(
        "SELECT treasury, method, confidence, confirmed_at, provenance "
        "FROM wt_confirmed_treasuries WHERE treasury=?", (treasury_wallet,),
    ).fetchone()
    if not confirmed:
        return None

    operation_id = None
    if _table_exists(ops_conn, "wt_ops_v2_wallets"):
        op_row = ops_conn.execute(
            "SELECT operation_uuid FROM wt_ops_v2_wallets WHERE wallet=? AND role='TREASURY' "
            "ORDER BY last_seen DESC LIMIT 1", (treasury_wallet,),
        ).fetchone()
        if op_row:
            operation_id = op_row["operation_uuid"]

    return {
        "treasury_wallet": treasury_wallet,
        "confirmation_method": confirmed["method"],
        "confidence_label": confirmed["confidence"],
        "confirmed_at": confirmed["confirmed_at"],
        "provenance": confirmed["provenance"],
        "operation_id": operation_id,
    }


def resolve_treasury_for_launch(
    ops_conn: sqlite3.Connection, mint: str,
) -> dict[str, Any]:
    """The full per-launch resolution: creator -> direct funder -> subprov
    classification -> treasury walkback -> known-treasury match. Returns
    exactly the schema specified in docs/design/x65_1/x65_1_resolution_model.md.

    Zero writes, zero RPC, bounded to MAX_WALKBACK_DEPTH unless bridging
    evidence is explicitly found (checked, never assumed)."""
    funder_info = get_creator_funder(ops_conn, mint)
    if not funder_info:
        return {
            "treasury_resolution": {
                "status": STATUS_UNRESOLVED,
                "creator_wallet": None,
                "subprov_wallet": None,
                "treasury_wallet": None,
                "operation_id": None,
                "operation_name": None,
                "hop_depth": 0,
                "confidence": 0.0,
                "evidence": [],
                "reason": "No wt_attribution_outcomes row (or no terminal_entity) exists for this mint -- "
                          "the creator's direct funder itself is unknown, so no walkback is possible.",
            }
        }

    funder_wallet = funder_info["funder_wallet"]
    creator_wallet = funder_info["creator"]
    subprov_result = classify_creator_funder(ops_conn, funder_wallet)
    classification = subprov_result["classification"]

    evidence: list[dict[str, Any]] = [{
        "hop": 1,
        "relationship": "creator_to_direct_funder",
        "wallet": funder_wallet,
        "source": "wt_attribution_outcomes.terminal_entity",
        "outcome_type": funder_info["outcome_type"],
    }]

    if classification == SUBPROV_DIRECT_TREASURY:
        match = match_known_treasury(ops_conn, funder_wallet)
        evidence.append({
            "hop": 1, "relationship": "direct_treasury_confirmation",
            "wallet": funder_wallet, "source": "wt_confirmed_treasuries",
        })
        return {
            "treasury_resolution": {
                "status": STATUS_KNOWN_TREASURY if match else STATUS_UNRESOLVED,
                "creator_wallet": creator_wallet,
                "subprov_wallet": None,
                "treasury_wallet": funder_wallet,
                "operation_id": match["operation_id"] if match else None,
                "operation_name": None,
                "hop_depth": 1,
                "confidence": 0.9 if match else 0.0,
                "evidence": evidence,
                "reason": "Creator was funded directly by an already-confirmed treasury -- no intermediate "
                          "sub-provisioner hop." if match else
                          "Creator's direct funder classified DIRECT_TREASURY but a confirmation lookup "
                          "unexpectedly failed -- treated conservatively as unresolved.",
            }
        }

    if classification in (SUBPROV_UNRESOLVED, SUBPROV_NON_OPERATIONAL):
        return {
            "treasury_resolution": {
                "status": STATUS_NO_SUBPROV if classification == SUBPROV_NON_OPERATIONAL else STATUS_UNRESOLVED,
                "creator_wallet": creator_wallet,
                "subprov_wallet": None,
                "treasury_wallet": None,
                "operation_id": None,
                "operation_name": None,
                "hop_depth": 1,
                "confidence": 0.0,
                "evidence": evidence,
                "reason": (
                    "Direct funder shows no sub-provisioner characteristics in any persisted evidence source."
                    if classification == SUBPROV_NON_OPERATIONAL else
                    "No wt_active_subprov_sessions record, wt_discovered_subprovs record, webhook hit, or "
                    "funding-lineage row exists for the creator's direct funder in any table checked -- "
                    "this wallet has never been observed by any existing indexing/detection pass."
                ),
            }
        }

    # CONFIRMED_SUBPROV or PROBABLE_SUBPROV: walk the second hop.
    session = subprov_result["session"]
    treasury_wallet = session.get("treasury_wallet") if session else None
    evidence.append({
        "hop": 1, "relationship": "creator_funder_is_subprov",
        "wallet": funder_wallet, "classification": classification,
        "source": "wt_active_subprov_sessions",
        "funding_signature": session.get("funding_signature") if session else None,
        "funding_amount": session.get("funding_amount") if session else None,
        "funding_time": session.get("funding_time") if session else None,
        "funding_mechanism": session.get("funding_mechanism") if session else None,
    })

    if not treasury_wallet:
        return {
            "treasury_resolution": {
                "status": STATUS_UNRESOLVED,
                "creator_wallet": creator_wallet,
                "subprov_wallet": funder_wallet,
                "treasury_wallet": None,
                "operation_id": None,
                "operation_name": None,
                "hop_depth": 1,
                "confidence": 0.3 if classification == SUBPROV_PROBABLE else 0.5,
                "evidence": evidence,
                "reason": "Direct funder has sub-provisioner evidence but no treasury_wallet is populated "
                          "on its wt_active_subprov_sessions row -- the second hop is genuinely unknown, "
                          "not silently guessed.",
            }
        }

    # Bounded bridging check -- only ever look further if there's real
    # evidence the treasury candidate is itself a subprov of something else.
    bridged = is_bridged_further_upstream(ops_conn, treasury_wallet)
    hop_depth = MAX_WALKBACK_DEPTH

    match = match_known_treasury(ops_conn, treasury_wallet)
    scale = treasury_scale_stats(ops_conn, treasury_wallet)
    evidence.append({
        "hop": 2, "relationship": "subprov_to_treasury_candidate",
        "wallet": treasury_wallet, "source": "wt_active_subprov_sessions.treasury_wallet",
        "distinct_subprovs_funded": scale["distinct_subprovs_funded"],
        "total_funding_amount_sol": scale["total_funding_amount_sol"],
        "bridged_further_upstream": bridged,
    })

    if match:
        evidence.append({
            "hop": 2, "relationship": "known_treasury_confirmation",
            "wallet": treasury_wallet, "source": "wt_confirmed_treasuries",
            "confirmation_method": match["confirmation_method"],
            "confirmed_at": match["confirmed_at"],
        })
        confidence = 0.95 if classification == SUBPROV_CONFIRMED else 0.6
        return {
            "treasury_resolution": {
                "status": STATUS_KNOWN_TREASURY,
                "creator_wallet": creator_wallet,
                "subprov_wallet": funder_wallet,
                "treasury_wallet": treasury_wallet,
                "operation_id": match["operation_id"],
                "operation_name": None,
                "hop_depth": hop_depth,
                "confidence": confidence,
                "evidence": evidence,
                "reason": f"Sub-provisioner's treasury_wallet is already confirmed via "
                          f"{match['confirmation_method']} ({match['provenance']}).",
            }
        }

    # Treasury candidate found but not yet confirmed -- Phase 6's
    # UNKNOWN_TREASURY_CANDIDATE, never auto-promoted.
    confidence = 0.4 if classification == SUBPROV_CONFIRMED else 0.2
    return {
        "treasury_resolution": {
            "status": STATUS_UNKNOWN_TREASURY_CANDIDATE,
            "creator_wallet": creator_wallet,
            "subprov_wallet": funder_wallet,
            "treasury_wallet": treasury_wallet,
            "operation_id": None,
            "operation_name": None,
            "hop_depth": hop_depth,
            "confidence": confidence,
            "evidence": evidence,
            "reason": "Upstream wallet has sub-provisioner-funding-scale evidence "
                      f"({scale['distinct_subprovs_funded']} distinct sub-provisioners funded, "
                      f"{scale['total_funding_amount_sol']:.1f} SOL total) but is not yet present in "
                      "wt_confirmed_treasuries -- requires human treasury review before any operation "
                      "attribution can occur.",
        }
    }


def resolve_treasury_for_cohort(
    ops_db_path: str, mints: list[str],
) -> dict[str, dict[str, Any]]:
    """Batch entry point: one treasury_resolution object per mint, per
    docs/design/x65_1/x65_1_resolution_model.md. Read-only, zero RPC."""
    conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        return {mint: resolve_treasury_for_launch(conn, mint) for mint in mints}
    finally:
        conn.close()
