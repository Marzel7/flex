"""Address-independent, read-only Deep route discovery.

Discovery is deliberately REVIEW_ONLY. Historical transfer graph reachability
does not establish exclusive capital control of a fungible SOL balance, so this
module never writes canonical membership or calls a provider.
"""

from __future__ import annotations

import sqlite3


CREATOR_CLOSE_LAMPORTS = 1_112_039_000
DEEP_OPERATOR_ID = "bb255638-a493-551f-938c-8be7c9ea4f1e"
DESTINATION_INDEX = "ix_wwtr_destination_amount"
DESTINATION_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_wwtr_destination_amount "
    "ON wt_walkback_transaction_roles(transfer_destination,transfer_lamports)"
)
MAX_FUNDING_PARENTS = 32


def _funding_parents(
    conn: sqlite3.Connection, destination: str, minimum: int, before: int,
    *, require_index: bool = False,
) -> list[tuple[str, str, int, int]]:
    """Return transaction-role-confirmed transfers, deduplicated by signature."""
    index_clause = f" INDEXED BY {DESTINATION_INDEX}" if require_index else ""
    return [tuple(row) for row in conn.execute(
        "SELECT DISTINCT r.transfer_source,r.signature,r.transfer_lamports,e.block_time "
        f"FROM wt_walkback_transaction_roles r{index_clause} "
        "JOIN wt_walkback_edge_candidates e ON e.signature=r.signature "
        "AND e.candidate_parent=r.transfer_source "
        "AND e.wallet=r.transfer_destination "
        "WHERE r.transfer_destination=? AND r.transfer_lamports>=? "
        "AND e.block_time>0 AND e.block_time<=? "
        "ORDER BY e.block_time,r.signature LIMIT ?",
        (destination, minimum, before, MAX_FUNDING_PARENTS + 1),
    )]


def assess_prospective_route(
    conn: sqlite3.Connection, mint: str, *, diagnostic_ignore_ownership: bool = False,
    require_index: bool = False,
) -> dict:
    """Find a route-shaped review lead without using any known wallet address.

    ``diagnostic_ignore_ownership`` is for offline control replay only. It does
    not change the returned state's REVIEW_ONLY authority.
    """
    q = conn.execute(
        "SELECT creator,subprov,status,intelligence_outcome,funding_mechanism,"
        "funder_block_time FROM wt_walkback_queue WHERE mint=?", (mint,),
    ).fetchone()
    if not q:
        return {"state": "INSUFFICIENT", "reason": "missing_walkback", "authority": "NONE"}
    creator, subprov, status, outcome, mechanism, funded_at = q
    if not diagnostic_ignore_ownership:
        member = conn.execute(
            "SELECT operator_id FROM operator_launch_membership WHERE mint=?", (mint,),
        ).fetchone()
        if member:
            return {"state": "EXISTING_ASSIGNMENT", "reason": "canonical_owner_present",
                    "operator_id": member[0], "authority": "NONE"}
        if conn.execute("SELECT 1 FROM wt_watchtower_launches WHERE mint=?", (mint,)).fetchone():
            return {"state": "EXISTING_ASSIGNMENT", "reason": "watchtower_ledger_present",
                    "authority": "NONE"}
    if (status != "complete" or outcome not in {"LINEAGE_GAP", "WATCHTOWER_CONFIRMED"}
            or mechanism != "WSOL_WRAP_CLOSE" or not funded_at):
        return {"state": "INSUFFICIENT", "reason": "outside_complete_wrap_cohort",
                "authority": "NONE"}
    edges = conn.execute(
        "SELECT hop_depth,wallet,candidate_parent,signature,block_time,"
        "amount_lamports,mechanism,evidence_strength "
        "FROM wt_walkback_edge_candidates WHERE mint=? "
        "AND selection_status='SELECTED' AND hop_depth IN (1,2,3) "
        "ORDER BY hop_depth,signature", (mint,),
    ).fetchall()
    first = [row for row in edges if row[0] == 1]
    second = [row for row in edges if row[0] == 2]
    third = [row for row in edges if row[0] == 3]
    if len(first) != 1 or len(second) != 1 or len(third) > 1:
        return {"state": "INSUFFICIENT", "reason": "ambiguous_or_missing_selected_edges",
                "authority": "NONE"}
    one, two = first[0], second[0]
    distribution = two[2]
    if (not creator or not subprov or not distribution or
            len({creator, subprov, distribution}) != 3 or
            one[1] != creator or one[2] != subprov or two[1] != subprov or
            one[3] == two[3] or one[5] != CREATOR_CLOSE_LAMPORTS or
            one[6] != "WSOL_WRAP_CLOSE" or two[6] != "PLAIN_XFER" or
            one[7] != "TRANSACTION_DERIVED" or two[7] != "TRANSACTION_DERIVED" or
            two[5] is None or two[5] < 100_000_000_000 or
            not one[4] or not two[4] or
            not 0 < int(two[4]) <= int(one[4]) <= int(funded_at)):
        return {"state": "INSUFFICIENT", "reason": "invalid_selected_lower_route",
                "authority": "NONE"}

    if require_index and [row[2] for row in conn.execute(
        f"PRAGMA index_info({DESTINATION_INDEX})"
    )] != ["transfer_destination", "transfer_lamports"]:
        return {"state": "INSUFFICIENT", "reason": "missing_required_destination_index",
                "authority": "NONE"}

    routes = {}
    upper_parents = _funding_parents(
        conn, distribution, 100_000_000_000, int(two[4]), require_index=require_index,
    )
    if len(upper_parents) > MAX_FUNDING_PARENTS:
        return {"state": "AMBIGUOUS", "reason": "upper_parent_limit_exceeded",
                "authority": "NONE"}
    for coordinator, upper_sig, upper_amount, upper_time in upper_parents:
        if coordinator in {creator, subprov, distribution}:
            continue
        pool_parents = _funding_parents(
            conn, coordinator, 1_000_000_000_000, int(upper_time),
            require_index=require_index,
        )
        if len(pool_parents) > MAX_FUNDING_PARENTS:
            return {"state": "AMBIGUOUS", "reason": "pool_parent_limit_exceeded",
                    "authority": "NONE"}
        for pool, pool_sig, pool_amount, pool_time in pool_parents:
            if pool in {creator, subprov, distribution, coordinator}:
                continue
            routes[(pool, coordinator)] = {
                "pool": pool, "coordinator": coordinator,
                "distribution": distribution, "pool_signature": pool_sig,
                "upper_signature": upper_sig, "pool_time": pool_time,
                "upper_time": upper_time, "pool_lamports": pool_amount,
                "upper_lamports": upper_amount,
            }
    if not routes:
        return {"state": "INSUFFICIENT", "reason": "no_time_ordered_upper_route",
                "authority": "NONE"}
    if third:
        compatible = [r for r in routes.values() if
                      third[0][1] == distribution and
                      third[0][2] == r["coordinator"] and
                      third[0][7] == "TRANSACTION_DERIVED" and
                      third[0][4] and int(third[0][4]) <= int(two[4])]
        if not compatible:
            return {"state": "CONFLICT", "reason": "selected_upstream_disagrees",
                    "authority": "NONE"}
        routes = {(r["pool"], r["coordinator"]): r for r in compatible}
    if len(routes) != 1:
        return {"state": "AMBIGUOUS", "reason": "multiple_upper_routes",
                "authority": "NONE", "route_count": len(routes)}
    route = next(iter(routes.values()))
    return {
        "state": "REVIEW_CANDIDATE", "reason": "address_independent_route_shape",
        "authority": "REVIEW_ONLY", "route": route,
        "capital_continuity": "UNQUALIFIED",
        "automatic_membership_allowed": False,
    }
