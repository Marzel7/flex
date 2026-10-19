"""X65.44 — Promote Walkback-Confirmed WATCHTOWER Launches into the
Canonical Registry.

X65.41 established wt_watchtower_launches as the authoritative WATCHTOWER
launch registry (record_launch()'s own docstring: "Authoritative launch
record"). Historically only the live ws_cascade daemon (and a narrower
post-migration lineage-walk backfill, watchtower_backfill.py) wrote to it.
But walkback attribution (src/ops/attribution_outcome.py) can ALSO
independently establish canonical WATCHTOWER status by reconstructing
funding lineage and reaching the canonical WATCHTOWER operator
(outcome_type=CANONICAL_OPERATOR_REACHED, operator_id=WATCHTOWER_OPERATOR_
ID) -- and until this module, that confirmation was never promoted into
the registry. Production audit (2026-07-23, database/wt_ops_v2.db): 98
historical CANONICAL_OPERATOR_REACHED/WATCHTOWER outcomes existed, only 22
were in the registry, 76 were missing -- including 18 that completed
within the prior 24 hours.

Confirmation predicate (verified against every historical outcome_type/
operator_id pairing in production data -- every CANONICAL_OPERATOR_REACHED
row has operator_id=WATCHTOWER_OPERATOR_ID and every other outcome_type
has operator_id=NULL, i.e. this is already a 100%-clean, mutually
exclusive split with no observed counter-examples):

    outcome_type == "CANONICAL_OPERATOR_REACHED"
    AND operator_id == WATCHTOWER_OPERATOR_ID

Both conditions are checked explicitly (rather than relying on either
alone) as defense-in-depth against a future outcome_type/operator_id
pairing that doesn't hold today.

Field mapping (all from already-persisted evidence, never invented):
  creator          -- outcome.evidence["creator"], wt_walkback_queue.creator
  treasury_wallet   -- outcome.evidence["treasuries"][0] (single-treasury;
                       multi-treasury lineage is not this task's concern)
  subprov_wallet    -- outcome.evidence["subprovisioners"][0] if present,
                       else wt_walkback_queue.subprov
  create_signature  -- wt_walkback_queue.create_anchor_signature (may be
                       None -- see idempotency note below)
  create_time       -- wt_walkback_queue.create_anchor_block_time, else
                       token_analysis.created_at (X65.35b's own precedent
                       fallback order)
  wrap_close_sig    -- wt_walkback_queue.funder_sig (when funding_mechanism
                       is WSOL_WRAP_CLOSE -- the funder IS the wrap-close
                       wallet in that mechanism)
  wrap_close_sol    -- wt_walkback_queue.funder_amount_sol
  confidence        -- "WALKBACK" (new tier -- distinguishes from STRICT
                       live-detection and BACKFILL lineage-walk, per the
                       brief's provenance-preserving requirement)
  creator_extraction_method -- "WALKBACK_RECOVERED" (matches the exact
                       provenance label already used by detection_
                       reconciliation.py's classification for this case)

Idempotency: wt_watchtower_launches.UNIQUE(creator_wallet, create_signature)
does NOT protect against duplicate inserts when create_signature is NULL
-- SQLite treats every NULL as distinct under a UNIQUE constraint, so
INSERT OR IGNORE never conflicts on two NULL-signature rows for the same
mint (confirmed: watchtower_backfill.py's own record_launch(create_sig=
None, ...) calls have this exact latent gap). This module therefore checks
"is this mint already in the registry" EXPLICITLY before ever calling
record_launch(), rather than relying on the DB constraint alone.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from src.core import ws_cascade_store as store
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID

_LOG = logging.getLogger(__name__)

CONFIDENCE_TIER = "WALKBACK"
EXTRACTION_METHOD = "WALKBACK_RECOVERED"
MEMBERSHIP_SOURCE = "walkback_watchtower_confirmed_projection_v1"


def is_canonical_watchtower_outcome(outcome_type: str | None, operator_id: str | None) -> bool:
    """The exact, sole confirmation predicate for promotion. Both
    conditions checked explicitly -- see module docstring for the
    production-data verification that makes this a clean, mutually
    exclusive split today."""
    return outcome_type == "CANONICAL_OPERATOR_REACHED" and operator_id == WATCHTOWER_OPERATOR_ID


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _normalise_unix_seconds(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        try:
            import datetime
            s = str(value).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return int(datetime.datetime.fromisoformat(s).timestamp())
        except (TypeError, ValueError):
            return None


def _has_verified_queue_funding_route(conn: sqlite3.Connection, mint: str) -> bool:
    """Require persisted, transaction-derived route evidence for queue promotion.

    ``WATCHTOWER_CONFIRMED`` alone may originate from a legacy wallet
    association. A queue-confirmed mint is eligible only after the walkback
    has retained a complete treasury -> subprovisioner -> creator provisioning
    session and the queue retains the WSOL creator-funding signature.
    """
    if not (_table_exists(conn, "wt_walkback_queue") and _table_exists(conn, "wt_provisioning_sessions")):
        return False
    row = conn.execute(
        "SELECT q.creator,q.treasury,q.subprov,q.funder_sig,q.funding_mechanism,"
        "s.treasury AS session_treasury,s.subprov AS session_subprov,"
        "s.creator AS session_creator,s.treasury_to_subprov_mechanism,"
        "s.subprov_to_creator_mechanism "
        "FROM wt_walkback_queue q JOIN wt_provisioning_sessions s ON s.source_mint=q.mint "
        "WHERE q.mint=? AND q.intelligence_outcome='WATCHTOWER_CONFIRMED'",
        (mint,),
    ).fetchone()
    if not row:
        return False
    return bool(
        row["creator"] and row["treasury"] and row["subprov"] and row["funder_sig"]
        and row["funding_mechanism"] == "WSOL_WRAP_CLOSE"
        and row["creator"] == row["session_creator"]
        and row["treasury"] == row["session_treasury"]
        and row["subprov"] == row["session_subprov"]
        and row["subprov_to_creator_mechanism"] == "WSOL_WRAP_CLOSE"
    )


def promote_walkback_confirmed_watchtower(
    ops_conn: sqlite3.Connection,
    mint: str,
    *,
    outcome_type: str | None,
    operator_id: str | None,
    evidence: dict[str, Any] | None,
    completed_at: int | None = None,
    core_conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Idempotent, additive promotion of a walkback-confirmed WATCHTOWER
    mint into the canonical registry. Safe to call after EVERY
    materialize_outcome()/persist_outcome() commit, and safe to call
    repeatedly for the same mint (a reconciliation-sweep safety net) --
    it never creates a duplicate row and never overwrites an existing
    registry row's fields, regardless of how many times it runs.

    Returns {"action": "promoted"|"already_present"|"not_eligible"|"missing_evidence"|"failed",
             "mint": mint, "error": str|None}.

    Failure handling: this function catches its own write errors and
    returns action="failed" with the exception message rather than
    propagating -- a promotion failure must never roll back or interfere
    with the walkback commit that already succeeded (per the brief's
    explicit "additive, not atomic with the outcome write" requirement).
    The caller is responsible for logging/surfacing a "failed" result;
    this module only guarantees it is visible in the return value, never
    silently swallowed.
    """
    # The legacy attribution table records only terminal walkback taxonomy.
    # A newer queue confirmation may coexist with UNKNOWN_INFRASTRUCTURE, but
    # queue confirmation is never sufficient alone: it must have a complete,
    # persisted transaction-derived funding route.
    legacy_eligible = is_canonical_watchtower_outcome(outcome_type, operator_id)
    queue_route_eligible = not legacy_eligible and _has_verified_queue_funding_route(ops_conn, mint)
    if not legacy_eligible and not queue_route_eligible:
        return {"action": "not_eligible", "mint": mint, "error": None}

    try:
        existing = ops_conn.execute(
            "SELECT 1 FROM wt_watchtower_launches WHERE mint=?", (mint,)
        ).fetchone()
        if existing:
            return {"action": "already_present", "mint": mint, "error": None}

        evidence = evidence or {}
        creator = evidence.get("creator")
        treasuries = evidence.get("treasuries") or []
        subprovisioners = evidence.get("subprovisioners") or []
        treasury = treasuries[0] if treasuries else None
        subprov = subprovisioners[0] if subprovisioners else None

        create_sig = None
        create_time = None
        wrap_close_sig = None
        wrap_close_sol = None
        funding_mechanism = None
        if _table_exists(ops_conn, "wt_walkback_queue"):
            row = ops_conn.execute(
                "SELECT creator, subprov, treasury, create_anchor_signature, "
                "create_anchor_block_time, funder_sig, funder_amount_sol, funding_mechanism "
                "FROM wt_walkback_queue WHERE mint=?", (mint,),
            ).fetchone()
            if row:
                creator = creator or row["creator"]
                treasury = treasury or row["treasury"]
                subprov = subprov or row["subprov"]
                create_sig = row["create_anchor_signature"]
                create_time = _normalise_unix_seconds(row["create_anchor_block_time"])
                funding_mechanism = row["funding_mechanism"]
                if funding_mechanism == "WSOL_WRAP_CLOSE":
                    wrap_close_sig = row["funder_sig"]
                    wrap_close_sol = row["funder_amount_sol"]

        if create_time is None and core_conn is not None:
            core_row = core_conn.execute(
                "SELECT created_at FROM token_analysis WHERE mint=?", (mint,)
            ).fetchone()
            if core_row:
                create_time = _normalise_unix_seconds(core_row["created_at"])
        if create_time is None:
            create_time = _normalise_unix_seconds(completed_at)

        if not creator:
            # creator_wallet is NOT NULL on the registry -- without it we
            # cannot write a valid row at all. Never fabricate one.
            return {"action": "missing_evidence", "mint": mint, "error": "no creator available"}

        store.record_launch(
            ops_conn,
            mint=mint,
            creator=creator,
            create_sig=create_sig,
            create_time=create_time,
            treasury=treasury,
            subprov=subprov,
            wrap_close_sig=wrap_close_sig,
            birth_to_launch_s=None,
            confidence=CONFIDENCE_TIER,
            wrap_close_sol=wrap_close_sol,
            funding_mechanism=funding_mechanism or store.FUNDING_MECHANISM,
        )
        ops_conn.execute(
            "UPDATE wt_watchtower_launches SET creator_extraction_method=? "
            "WHERE mint=? AND creator_wallet=?",
            (EXTRACTION_METHOD, mint, creator),
        )
        ops_conn.commit()
        return {"action": "promoted", "mint": mint, "error": None}
    except Exception as exc:  # noqa: BLE001 -- must never propagate into the walkback commit path
        _LOG.error(
            "watchtower_registry_promotion failed mint=%s outcome_type=%s error=%s",
            mint, outcome_type, exc,
        )
        return {"action": "failed", "mint": mint, "error": str(exc)}


def project_watchtower_confirmed_membership(
    ops_conn: sqlite3.Connection,
    mint: str,
    *,
    core_db_path: str | None = None,
    now: int | None = None,
    refresh_activity: bool = True,
) -> dict[str, Any]:
    """Project one strict queue-confirmed mint into WATCHTOWER membership.

    This is deliberately downstream of classification: the sole admission
    predicate is the persisted terminal ``WATCHTOWER_CONFIRMED`` queue outcome.
    Existing assignments are never moved, and the primary-keyed membership
    write makes repeated calls idempotent.
    """
    row = ops_conn.execute(
        "SELECT intelligence_outcome FROM wt_walkback_queue WHERE mint=?", (mint,)
    ).fetchone()
    if not row or row["intelligence_outcome"] != "WATCHTOWER_CONFIRMED":
        return {"action": "not_confirmed", "mint": mint}
    # Keep membership projection behind the same strict, transaction-derived
    # route gate as queue promotion. A terminal outcome on its own is not an
    # authorization to bypass the corrected false-positive exclusion.
    if not _has_verified_queue_funding_route(ops_conn, mint):
        return {"action": "missing_verified_route", "mint": mint}

    operator = ops_conn.execute(
        "SELECT operator_id FROM operators WHERE display_name='WATCHTOWER' "
        "AND status!='MERGED' LIMIT 1"
    ).fetchone()
    if not operator:
        return {"action": "missing_watchtower_operator", "mint": mint}
    operator_id = operator["operator_id"]

    existing = ops_conn.execute(
        "SELECT operator_id FROM operator_launch_membership WHERE mint=?", (mint,)
    ).fetchone()
    if existing:
        return {
            "action": "already_present" if existing["operator_id"] == operator_id else "existing_other_operator",
            "mint": mint,
            "operator_id": existing["operator_id"],
        }

    assigned_at = int(time.time()) if now is None else int(now)
    ops_conn.execute(
        "INSERT INTO operator_launch_membership"
        "(mint,operator_id,source_population_id,assigned_at,event_id) VALUES(?,?,?,?,NULL)",
        (mint, operator_id, MEMBERSHIP_SOURCE, assigned_at),
    )
    if refresh_activity:
        from src.ops.manual_registry import refresh_operator_activity_snapshot
        refresh_operator_activity_snapshot(
            ops_conn, operator_id, core_db_path=core_db_path, now=assigned_at,
        )
    return {"action": "projected", "mint": mint, "operator_id": operator_id}


def reconcile_confirmed_watchtower_memberships(
    ops_conn: sqlite3.Connection,
    *,
    core_db_path: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Project only confirmed WATCHTOWER rows that have no membership.

    This bounded repair intentionally neither reclassifies historical evidence
    nor changes an assignment held by another operator.
    """
    rows = ops_conn.execute(
        "SELECT q.mint FROM wt_walkback_queue q "
        "LEFT JOIN operator_launch_membership m ON m.mint=q.mint "
        "WHERE q.intelligence_outcome='WATCHTOWER_CONFIRMED' AND m.mint IS NULL "
        "ORDER BY q.completed_at,q.mint"
    ).fetchall()
    projected: list[str] = []
    outcomes: dict[str, int] = {}
    timestamp = int(time.time()) if now is None else int(now)
    for row in rows:
        result = project_watchtower_confirmed_membership(
            ops_conn, row["mint"], core_db_path=core_db_path, now=timestamp,
            refresh_activity=False,
        )
        action = result["action"]
        outcomes[action] = outcomes.get(action, 0) + 1
        if action == "projected":
            projected.append(row["mint"])

    if projected:
        operator = ops_conn.execute(
            "SELECT operator_id FROM operators WHERE display_name='WATCHTOWER' "
            "AND status!='MERGED' LIMIT 1"
        ).fetchone()
        if operator:
            from src.ops.manual_registry import refresh_operator_activity_snapshot
            refresh_operator_activity_snapshot(
                ops_conn, operator["operator_id"], core_db_path=core_db_path, now=timestamp,
            )
    return {
        "eligible_missing_membership": len(rows),
        "projected": projected,
        "outcomes": outcomes,
    }


def remove_invalid_confirmed_membership_projection(
    ops_conn: sqlite3.Connection,
    *,
    core_db_path: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Undo only this projector's rows that fail the strict route gate."""
    rows = ops_conn.execute(
        "SELECT mint FROM operator_launch_membership WHERE source_population_id=? ORDER BY mint",
        (MEMBERSHIP_SOURCE,),
    ).fetchall()
    retained: list[str] = []
    removed: list[str] = []
    for row in rows:
        mint = row["mint"]
        if _has_verified_queue_funding_route(ops_conn, mint):
            retained.append(mint)
            continue
        ops_conn.execute(
            "DELETE FROM operator_launch_membership WHERE mint=? AND source_population_id=?",
            (mint, MEMBERSHIP_SOURCE),
        )
        removed.append(mint)
    if removed:
        operator = ops_conn.execute(
            "SELECT operator_id FROM operators WHERE display_name='WATCHTOWER' "
            "AND status!='MERGED' LIMIT 1"
        ).fetchone()
        if operator:
            from src.ops.manual_registry import refresh_operator_activity_snapshot
            refresh_operator_activity_snapshot(
                ops_conn, operator["operator_id"], core_db_path=core_db_path,
                now=int(time.time()) if now is None else int(now),
            )
    return {"examined": len(rows), "retained": retained, "removed": removed}


def reconcile_missing_promotions(
    ops_conn: sqlite3.Connection,
    *,
    core_conn: sqlite3.Connection | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Historical repair / safety-net sweep: scans EVERY wt_attribution_
    outcomes row for the canonical WATCHTOWER predicate (not just recent
    ones -- the brief explicitly warns against assuming a narrow window),
    and promotes any mint missing from the registry. Idempotent (reuses
    promote_walkback_confirmed_watchtower's own idempotency guard),
    supports dry_run (reports what WOULD be promoted without writing).

    Returns counts: eligible, already_present, inserted (or would_insert
    under dry_run), skipped, failed -- plus the list of failed mints for
    visibility (per the brief's "failure must not be silently lost").
    """
    rows = ops_conn.execute(
        "SELECT mint, outcome_type, operator_id, evidence_json, completed_at "
        "FROM wt_attribution_outcomes "
        "WHERE outcome_type='CANONICAL_OPERATOR_REACHED' AND operator_id=?",
        (WATCHTOWER_OPERATOR_ID,),
    ).fetchall()

    import json
    eligible = len(rows)
    already_present = 0
    inserted = 0
    skipped = 0
    failed: list[dict[str, Any]] = []

    for row in rows[: limit if limit is not None else len(rows)]:
        mint = row["mint"]
        already = ops_conn.execute(
            "SELECT 1 FROM wt_watchtower_launches WHERE mint=?", (mint,)
        ).fetchone()
        if already:
            already_present += 1
            continue

        if dry_run:
            inserted += 1  # "would insert"
            continue

        try:
            evidence = json.loads(row["evidence_json"] or "{}")
        except (TypeError, ValueError):
            evidence = {}

        result = promote_walkback_confirmed_watchtower(
            ops_conn, mint,
            outcome_type=row["outcome_type"], operator_id=row["operator_id"],
            evidence=evidence, completed_at=row["completed_at"], core_conn=core_conn,
        )
        if result["action"] == "promoted":
            inserted += 1
        elif result["action"] == "already_present":
            already_present += 1
        elif result["action"] == "failed":
            failed.append({"mint": mint, "error": result["error"]})
        else:
            skipped += 1

    return {
        "eligible": eligible,
        "already_present": already_present,
        "inserted": inserted,
        "skipped": skipped,
        "failed_count": len(failed),
        "failed": failed,
        "dry_run": dry_run,
    }
