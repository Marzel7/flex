"""WATCHTOWER observational reader for stable fingerprint-monitoring metadata.

This module is read-only by design: it reports retained WATCHTOWER state and
associated role-discovery counts without running detector logic or writing any
DB rows.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from src.core.watchtower_registry_promotion import _has_verified_queue_funding_route
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID

OPERATION_NAME = "WATCHTOWER"
FINGERPRINT_ID = "WSOL-ROUTE-STRICT-v1"
WATCHTOWER_MONITORING_STRATEGY = "DYNAMIC_ROLE_DISCOVERY"
NEAR_MATCH_MONITORING = "DISABLED"
MEMBERSHIP_WRITE_CAPABILITY = "NONE"
MUTATION_RESILIENCE = "TREASURY_SUBPROVIDER_CREATOR_ROTATION_SUPPORTED_WITH_DISCOVERY_DELAY"

STATE_CONFIRMED_VERIFIED_ROUTE = "CONFIRMED_VERIFIED_ROUTE"
STATE_CONFIRMED_OUTCOME_NOT_PROJECTABLE = "CONFIRMED_OUTCOME_NOT_PROJECTABLE"
STATE_PENDING_ROLE_DISCOVERY = "PENDING_ROLE_DISCOVERY"
STATE_UNOBSERVABLE = "UNOBSERVABLE"
UNIQUENESS_MEASURABLE = "MEASURABLE"
UNIQUENESS_NOT_MEASURED = "NOT_YET_MEASURED"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _scalar(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, args).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def read_watchtower_observational_state(conn: sqlite3.Connection, mint: str) -> str:
    """Return one of four persisted observation states for a mint.

    Order is intentionally conservative:
    - verified route evidence is the strongest signal
    - explicit outcomes are next
    - active queue jobs are pending role discovery
    - anything else is unobservable
    """
    # Canonical projection row is considered observed when it exists, even if the
    # queue has not been retained (strict pipeline path or older backfill).
    if _table_exists(conn, "wt_watchtower_launches"):
        row = conn.execute(
            "SELECT 1 FROM wt_watchtower_launches WHERE mint=? LIMIT 1",
            (mint,),
        ).fetchone()
        if row and _table_exists(conn, "wt_walkback_queue"):
            pass
        elif row:
            return STATE_CONFIRMED_VERIFIED_ROUTE

    queue_row = None
    if _table_exists(conn, "wt_walkback_queue"):
        queue_row = conn.execute(
            "SELECT status,intelligence_outcome FROM wt_walkback_queue WHERE mint=?",
            (mint,),
        ).fetchone()

    if queue_row:
        status = (queue_row["status"] or "").lower()
        outcome = queue_row["intelligence_outcome"]
        if outcome == "WATCHTOWER_CONFIRMED" and _has_verified_queue_funding_route(conn, mint):
            return STATE_CONFIRMED_VERIFIED_ROUTE
        if outcome == "WATCHTOWER_CONFIRMED":
            return STATE_CONFIRMED_OUTCOME_NOT_PROJECTABLE
        if outcome:
            return STATE_CONFIRMED_OUTCOME_NOT_PROJECTABLE
        if status in {"running", "pending", "waiting"}:
            return STATE_PENDING_ROLE_DISCOVERY
        return STATE_PENDING_ROLE_DISCOVERY

    return STATE_UNOBSERVABLE


def _confirmed_subproviders_count(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "wt_discovered_subprovs"):
        return 0
    if _table_exists(conn, "wt_confirmed_treasuries"):
        return _scalar(
            conn,
            "SELECT COUNT(*) FROM wt_discovered_subprovs WHERE treasury IN "
            "(SELECT treasury FROM wt_confirmed_treasuries)",
        )
    return _scalar(conn, "SELECT COUNT(*) FROM wt_discovered_subprovs WHERE treasury IS NOT NULL")


def _subprovider_candidate_count(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "wt_discovered_subprovs"):
        return 0
    return _scalar(conn, "SELECT COUNT(*) FROM wt_discovered_subprovs")


def _rejected_subprovider_count(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "wt_discovered_subprovs"):
        return 0
    return _scalar(
        conn,
        "SELECT COUNT(*) FROM wt_discovered_subprovs WHERE "
        "UPPER(state) LIKE 'REJECTED%' OR UPPER(state) LIKE 'REJECTED_%'",
    )


def watchtower_source_manifest(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build the minimal monitoring manifest payload for fingerprint consumers."""
    def _count_treasury_candidates() -> int:
        if _table_exists(conn, "wt_treasury_review"):
            return _scalar(conn, "SELECT COUNT(*) FROM wt_treasury_review")
        return 0

    def _count_confirmed_treasuries() -> int:
        if _table_exists(conn, "wt_confirmed_treasuries"):
            return _scalar(conn, "SELECT COUNT(*) FROM wt_confirmed_treasuries")
        return 0

    def _count_confirmed_membership() -> int:
        if not _table_exists(conn, "operator_launch_membership"):
            return 0
        return _scalar(
            conn,
            "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?",
            (WATCHTOWER_OPERATOR_ID,),
        )

    return {
        "operation": OPERATION_NAME,
        "operation_id": WATCHTOWER_OPERATOR_ID,
        "fingerprint_id": FINGERPRINT_ID,
        "monitoring_strategy": WATCHTOWER_MONITORING_STRATEGY,
        "near_match_monitoring": NEAR_MATCH_MONITORING,
        "mutation_resilience": MUTATION_RESILIENCE,
        "membership_write_capability": MEMBERSHIP_WRITE_CAPABILITY,
        "mutability_coverage": {
            "confirmed_treasuries": _count_confirmed_treasuries(),
            "treasury_candidates": _count_treasury_candidates(),
            "confirmed_subproviders": _confirmed_subproviders_count(conn),
            "subprovider_candidates": _subprovider_candidate_count(conn),
            "rejected_subproviders": _rejected_subprovider_count(conn),
            "confirmed_membership": _count_confirmed_membership(),
        },
        "monitoring_observation_source": "persisted WALKBACK outcome + walkback route evidence",
        "unique_state_source": "wt_watchtower_launches + wt_confirmed_treasuries + wt_treasury_review + wt_discovered_subprovs",
        "confirmed_route_method": {
            "requires_verified_queue_route": True,
            "route_state_check": "_has_verified_queue_funding_route",
            "source_tables": ["wt_walkback_queue", "wt_provisioning_sessions"],
        },
        "unresolved_lineage_support": True,
        "provenance": "CURRENTLY_PERSISTED_FACTS",
        "uniqueness_capability": UNIQUENESS_MEASURABLE if any(_count > 0 for _count in (
            _count_confirmed_treasuries(), _count_treasury_candidates(),
            _confirmed_subproviders_count(conn), _subprovider_candidate_count(conn),
        )) else UNIQUENESS_NOT_MEASURED,
    }


def watchtower_observational_summary_for_mint(conn: sqlite3.Connection, mint: str) -> dict[str, Any]:
    """Read-only per-mint projection for monitoring surfaces."""
    return {
        "mint": mint,
        "operation": OPERATION_NAME,
        "fingerprint_id": FINGERPRINT_ID,
        "monitoring_state": read_watchtower_observational_state(conn, mint),
        "monitoring_strategy": WATCHTOWER_MONITORING_STRATEGY,
        "near_match_monitoring": NEAR_MATCH_MONITORING,
        "membership_write_capability": MEMBERSHIP_WRITE_CAPABILITY,
        "mutation_resilience": MUTATION_RESILIENCE,
        "provenance": "wt_walkback_queue + wt_watchtower_launches + wt_confirmed_treasuries",
    }
