"""X74.4 Evidence Integrity & Confidence Presentation.

Presentation-only classification of WATCHTOWER launch funding-mechanism
evidence quality. Reproduces the read-only analysis from the X74.3B audit
as a reusable, queryable module: for each confirmed WATCHTOWER launch,
determine whether its stored `funding_mechanism` value is genuinely
transaction-evidenced (OBSERVED), only partially supported (INFERRED), or
was silently filled in by one of several fallback defaults with zero
corroborating evidence (UNKNOWN).

This module changes nothing about attribution, reconciliation, promotion,
identity, discovery, walkback, or the registry. It reads existing tables
and returns a classification; it never writes.

Two independent silent-default sites were confirmed by X74.3B:
  - src/core/watchtower_registry_promotion.py:202 (WALKBACK-tier promotion)
    falls back to store.FUNDING_MECHANISM when the source
    wt_walkback_queue row has funding_mechanism=NULL.
  - src/core/watchtower_backfill.py's COALESCE(...,'WSOL_WRAP_CLOSE') does
    the same for BACKFILL-tier promotion.
  - wt_watchtower_launches.funding_mechanism itself has a column-level
    DEFAULT 'WSOL_WRAP_CLOSE', so even a caller that never sets the value
    explicitly (as watchtower_backfill.py's record_launch call does for
    MANUAL_ATTESTATION rows) ends up with the same masked value.

Because all three sites default to the identical string, the stored value
cannot be trusted at face value — it must be reconciled against the
persisted evidence it was supposedly derived from before being displayed.
"""
from __future__ import annotations

import sqlite3
from typing import Any


OBSERVED = "OBSERVED"
INFERRED = "INFERRED"
UNKNOWN = "UNKNOWN"

CONFIRMED_MECHANISMS = frozenset({"WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE", "PLAIN_XFER"})


def _row_exists(conn, sql: str, params: tuple) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def classify_launch_evidence(conn, launch: dict[str, Any]) -> dict[str, Any]:
    """Classify one wt_watchtower_launches row's funding-mechanism evidence.

    `conn` must be a connection to database/wt_ops_v2.db (row_factory=Row).
    `launch` is a dict of a wt_watchtower_launches row (mint, creator_wallet,
    treasury_wallet, subprov_wallet, funding_mechanism, wrap_close_signature,
    create_signature, confidence, ...).

    Returns {evidence_status, mechanism, evidence_source, wrap_wallet,
    session_wallet, funding_signature_present}. `mechanism` is the value to
    DISPLAY — None (not the stored default) when evidence_status is UNKNOWN,
    so callers never accidentally render an unevidenced mechanism string.
    """
    mint = launch.get("mint")
    creator = launch.get("creator_wallet")
    confidence = launch.get("confidence")
    stored_mechanism = launch.get("funding_mechanism")

    # 1. WALKBACK tier — the source queue row is the ground truth for
    #    whether a mechanism was actually captured during reconstruction.
    if confidence == "WALKBACK":
        queue_row = _row_exists(
            conn, "SELECT funding_mechanism, funder_sig FROM wt_walkback_queue WHERE mint=?", (mint,)
        )
        if queue_row and queue_row["funding_mechanism"] in CONFIRMED_MECHANISMS:
            return {
                "evidence_status": OBSERVED,
                "mechanism": queue_row["funding_mechanism"],
                "evidence_source": "wt_walkback_queue.funding_mechanism",
                "wrap_wallet": None,
                "session_wallet": None,
                "funding_signature_present": bool(queue_row["funder_sig"] or launch.get("wrap_close_signature")),
            }
        # queue funding_mechanism was NULL/unrecognised — this is exactly
        # the promotion-time default site. No mechanism was truly captured.
        return {
            "evidence_status": UNKNOWN,
            "mechanism": None,
            "evidence_source": "none (wt_walkback_queue.funding_mechanism was NULL; "
                                "stored value is an unverified fallback default)",
            "wrap_wallet": None,
            "session_wallet": None,
            "funding_signature_present": bool(launch.get("wrap_close_signature") or launch.get("create_signature")),
        }

    # 2. STRICT (live-detected) — corroborate against
    #    wt_candidate_websocket_watches, keyed by candidate_wallet (the
    #    live WS-detection path's own evidence, independent of the
    #    launch-registry row). A matching row's own funding_mechanism
    #    column also carries a DEFAULT, so a real signature/close
    #    destination is required as corroboration, not the column alone.
    watch = _row_exists(
        conn,
        "SELECT funding_mechanism, wrap_wallet, temp_wsol_account, close_destination, wrap_close_signature "
        "FROM wt_candidate_websocket_watches WHERE candidate_wallet=? "
        "AND wrap_close_signature IS NOT NULL",
        (creator,),
    ) if creator else None

    if watch and watch["funding_mechanism"] in CONFIRMED_MECHANISMS:
        return {
            "evidence_status": OBSERVED,
            "mechanism": watch["funding_mechanism"],
            "evidence_source": "wt_candidate_websocket_watches (live WS detection, signature-corroborated)",
            "wrap_wallet": watch["wrap_wallet"],
            "session_wallet": watch["temp_wsol_account"],
            "funding_signature_present": True,
        }

    # 3. wt_wrap_close_candidates — keyed by creator wallet (detection-time
    #    evidence, independent of the launch-registry row). Covers
    #    BACKFILL/MANUAL_ATTESTATION and any STRICT row not caught above.
    candidate = _row_exists(
        conn,
        "SELECT funding_mechanism, wrap_wallet, temp_wsol_account, close_destination, tx_signature "
        "FROM wt_wrap_close_candidates WHERE creator=?",
        (creator,),
    ) if creator else None

    if candidate and candidate["funding_mechanism"] in CONFIRMED_MECHANISMS:
        return {
            "evidence_status": OBSERVED,
            "mechanism": candidate["funding_mechanism"],
            "evidence_source": "wt_wrap_close_candidates (detection-time decode)",
            "wrap_wallet": candidate["wrap_wallet"],
            "session_wallet": candidate["temp_wsol_account"],
            "funding_signature_present": bool(candidate["tx_signature"] or launch.get("wrap_close_signature")),
        }

    # No corroborating detection-time row anywhere — the stored value,
    # whatever it is, has no independent evidence behind it.
    return {
        "evidence_status": UNKNOWN,
        "mechanism": None,
        "evidence_source": "none (no wt_candidate_websocket_watches or wt_wrap_close_candidates "
                            "row for this creator; stored value is an unverified fallback default)",
        "wrap_wallet": None,
        "session_wallet": None,
        "funding_signature_present": bool(launch.get("wrap_close_signature") or launch.get("create_signature")),
    }


def classify_all_launches(conn) -> list[dict[str, Any]]:
    """Classify every row in wt_watchtower_launches. Read-only."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM wt_watchtower_launches").fetchall()]
    results = []
    for row in rows:
        evidence = classify_launch_evidence(conn, row)
        results.append({**row, **evidence})
    return results


def evidence_integrity_summary(conn) -> dict[str, Any]:
    """Aggregate counts for the Investigation UI's Evidence Integrity panel
    and the WATCHTOWER profile's Funding Mechanisms panel."""
    classified = classify_all_launches(conn)
    total = len(classified)
    observed = sum(1 for c in classified if c["evidence_status"] == OBSERVED)
    unknown = sum(1 for c in classified if c["evidence_status"] == UNKNOWN)
    inferred = sum(1 for c in classified if c["evidence_status"] == INFERRED)
    by_mechanism: dict[str, int] = {}
    for c in classified:
        if c["evidence_status"] == OBSERVED and c["mechanism"]:
            by_mechanism[c["mechanism"]] = by_mechanism.get(c["mechanism"], 0) + 1
    return {
        "total_launches": total,
        "observed": observed,
        "inferred": inferred,
        "unknown": unknown,
        "by_mechanism": by_mechanism,
        "wsol_wrap_close": by_mechanism.get("WSOL_WRAP_CLOSE", 0),
        "seeded_account_close": by_mechanism.get("SEEDED_ACCOUNT_CLOSE", 0),
        "plain_xfer": by_mechanism.get("PLAIN_XFER", 0),
    }


def launch_detail(conn, mint: str) -> dict[str, Any] | None:
    """Full drill-down for one launch: Treasury, Subprovider, Provisioning
    Wallet, Creator, Funding Mechanism, Evidence Status, Funding Signature,
    Evidence Source — the exact field set X74.4 Phase 3 requires."""
    row = _row_exists(conn, "SELECT * FROM wt_watchtower_launches WHERE mint=?", (mint,))
    if not row:
        return None
    launch = dict(row)
    evidence = classify_launch_evidence(conn, launch)
    return {
        "mint": launch.get("mint"),
        "treasury": launch.get("treasury_wallet"),
        "subprovider": launch.get("subprov_wallet"),
        "provisioning_wallet": evidence["wrap_wallet"],
        "session_wallet": evidence["session_wallet"],
        "creator": launch.get("creator_wallet"),
        "funding_mechanism": evidence["mechanism"],
        "evidence_status": evidence["evidence_status"],
        "evidence_source": evidence["evidence_source"],
        "funding_signature": launch.get("wrap_close_signature") or launch.get("create_signature"),
        "funding_signature_present": evidence["funding_signature_present"],
        "wrap_wallet_recovered": bool(evidence["wrap_wallet"]),
        "session_recovered": bool(evidence["session_wallet"]),
        "create_time": launch.get("create_time"),
        "confidence": launch.get("confidence"),
    }
