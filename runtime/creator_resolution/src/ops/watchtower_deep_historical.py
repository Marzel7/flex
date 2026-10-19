"""Fail-closed, read-only qualification of the observed Deep funding branch.

This is a historical witness check, not a prospective rotating-wallet detector.
It never creates an operator, changes membership, or promotes a treasury.
"""

from __future__ import annotations

import sqlite3
import hashlib
import json
import time
import uuid


POOL = "6Muxk6cE42WH5m7aHK69sp9ooCVgShB4CuJ9MB21WQsZ"
COORDINATOR = "HHJRGmusxxRx4TgjoAaje6rqLi9SKthUy7WKd5iogTUY"
WINDOW_START = 1789344000  # 2026-09-14T00:00:00Z
WINDOW_END = 1790035200    # 2026-09-22T00:00:00Z
CREATOR_CLOSE_LAMPORTS = 1_112_039_000
OPERATOR_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "watchtower-deep-historical-route-v1"))
SOURCE_ID = "WATCHTOWER_DEEP_HISTORICAL_ROUTE_V1"
DETECTOR_VERSION = "WATCHTOWER_DEEP_OBSERVED_ROUTE_HISTORICAL_V1"


def _observed_transfers(
    conn: sqlite3.Connection, source: str, destination: str, minimum: int,
    latest_time: int,
) -> list[tuple[str, int, int]]:
    """Return distinct transaction-derived transfers, never duplicated mint roles."""
    return [
        (row[0], int(row[1]), int(row[2]))
        for row in conn.execute(
            "SELECT DISTINCT r.signature,r.transfer_lamports,e.block_time "
            "FROM wt_walkback_transaction_roles r "
            "JOIN wt_walkback_edge_candidates e ON e.signature=r.signature "
            "AND e.candidate_parent=r.transfer_source "
            "AND e.wallet=r.transfer_destination "
            "WHERE r.transfer_source=? AND r.transfer_destination=? "
            "AND r.transfer_lamports>=? AND e.block_time>0 "
            "AND e.block_time<=? ORDER BY e.block_time,r.signature",
            (source, destination, minimum, latest_time),
        )
    ]


def qualify_historical_mint(conn: sqlite3.Connection, mint: str) -> dict:
    """Require the selected lower route and a time-ordered, funded upper route.

    A conflicting selected hop-3 wins over a cross-mint upper-route witness.
    Existing canonical assignments and WATCHTOWER ledger entries always veto.
    """
    q = conn.execute(
        "SELECT creator,subprov,status,intelligence_outcome,funding_mechanism,"
        "funder_block_time FROM wt_walkback_queue WHERE mint=?", (mint,),
    ).fetchone()
    if q is None:
        return {"eligible": False, "reason": "missing_walkback"}
    creator, subprov, status, outcome, mechanism, funded_at = q
    if (status != "complete" or outcome != "LINEAGE_GAP" or
            mechanism != "WSOL_WRAP_CLOSE" or not funded_at or
            not WINDOW_START <= int(funded_at) < WINDOW_END):
        return {"eligible": False, "reason": "outside_historical_gap_cohort"}
    if conn.execute("SELECT 1 FROM operator_launch_membership WHERE mint=?", (mint,)).fetchone():
        return {"eligible": False, "reason": "existing_operator_assignment"}
    if conn.execute("SELECT 1 FROM wt_watchtower_launches WHERE mint=?", (mint,)).fetchone():
        return {"eligible": False, "reason": "existing_watchtower_launch"}

    edges = conn.execute(
        "SELECT hop_depth,wallet,candidate_parent,signature,block_time,"
        "amount_lamports,mechanism,evidence_strength "
        "FROM wt_walkback_edge_candidates WHERE mint=? "
        "AND selection_status='SELECTED' AND hop_depth IN (1,2,3) "
        "ORDER BY hop_depth,signature", (mint,),
    ).fetchall()
    first = [e for e in edges if e[0] == 1]
    second = [e for e in edges if e[0] == 2]
    third = [e for e in edges if e[0] == 3]
    if len(first) != 1 or len(second) != 1 or len(third) > 1:
        return {"eligible": False, "reason": "ambiguous_or_missing_selected_route"}
    one, two = first[0], second[0]
    distribution = two[2]
    if (not creator or not subprov or len({creator, subprov, distribution,
                                           COORDINATOR, POOL}) != 5 or
            one[1] != creator or one[2] != subprov or
            two[1] != subprov or one[3] == two[3] or
            one[5] != CREATOR_CLOSE_LAMPORTS or
            one[6] != "WSOL_WRAP_CLOSE" or two[6] != "PLAIN_XFER" or
            one[7] != "TRANSACTION_DERIVED" or
            two[7] != "TRANSACTION_DERIVED" or
            two[5] is None or two[5] < 100_000_000_000 or
            not two[4] or not one[4] or
            not 0 < int(two[4]) <= int(one[4]) <= int(funded_at)):
        return {"eligible": False, "reason": "invalid_selected_lower_route"}
    if third and (third[0][1] != distribution or third[0][2] != COORDINATOR or
                  third[0][7] != "TRANSACTION_DERIVED" or
                  not third[0][4] or int(third[0][4]) > int(two[4])):
        return {"eligible": False, "reason": "conflicting_selected_upstream"}

    upper = _observed_transfers(
        conn, COORDINATOR, distribution, 100_000_000_000, int(two[4]),
    )
    for upper_sig, upper_amount, upper_time in upper:
        pool = _observed_transfers(
            conn, POOL, COORDINATOR, 1_000_000_000_000, upper_time,
        )
        if pool:
            pool_sig, pool_amount, pool_time = pool[-1]
            return {
                "eligible": True, "reason": "historical_branch_route",
                "mint": mint, "distribution": distribution,
                "selected_signatures": [one[3], two[3]],
                "upper_signature": upper_sig, "upper_lamports": upper_amount,
                "upper_time": upper_time, "pool_signature": pool_sig,
                "pool_lamports": pool_amount, "pool_time": pool_time,
            }
    return {"eligible": False, "reason": "missing_time_ordered_upper_witness"}


def historical_plan(conn: sqlite3.Connection) -> dict:
    """Read-only exact cohort and controls; no treasury or membership mutation."""
    rows = conn.execute(
        "SELECT mint FROM wt_walkback_queue WHERE funding_mechanism='WSOL_WRAP_CLOSE' "
        "AND funder_block_time>=? AND funder_block_time<? "
        "AND ABS(funder_amount_sol-1.112039)<0.0000005 ORDER BY mint",
        (WINDOW_START, WINDOW_END),
    ).fetchall()
    decisions = {row[0]: qualify_historical_mint(conn, row[0]) for row in rows}
    accepted = sorted(mint for mint, result in decisions.items() if result["eligible"])
    digest = hashlib.sha256("\n".join(accepted).encode()).hexdigest()
    return {"candidate_count": len(rows), "accepted": accepted,
            "accepted_count": len(accepted), "accepted_digest": digest,
            "decisions": decisions}


def commit_historical_operation(
    conn: sqlite3.Connection, *, expected_mints: list[str],
    expected_digest: str, expected_watchtower_count: int,
    now: int | None = None,
) -> dict:
    """One DWS-owned transaction; caller must already hold the writer lane.

    This routine deliberately has no commit/rollback call. The owner of the
    shared writer lane commits or rolls back the complete registration.
    """
    if len(expected_mints) != len(set(expected_mints)) or expected_mints != sorted(expected_mints):
        raise ValueError("invalid expected mint set")
    if hashlib.sha256("\n".join(expected_mints).encode()).hexdigest() != expected_digest:
        raise ValueError("historical cohort digest mismatch")
    if len(expected_mints) != 26:
        raise ValueError("historical cohort count changed")
    wt_count = conn.execute(
        "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?",
        ("04265d9f-6eb2-568c-a49e-9253091a4dbb",),
    ).fetchone()[0]
    if wt_count != expected_watchtower_count:
        raise ValueError("WATCHTOWER membership baseline changed")
    existing = conn.execute(
        "SELECT operator_id,display_name FROM operators WHERE operator_id=? "
        "OR display_name='WATCHTOWER_DEEP'", (OPERATOR_ID,),
    ).fetchall()
    if existing:
        if len(existing) != 1 or tuple(existing[0]) != (OPERATOR_ID, "WATCHTOWER_DEEP"):
            raise ValueError("WATCHTOWER_DEEP operator identity conflict")
        members = sorted(row[0] for row in conn.execute(
            "SELECT mint FROM operator_launch_membership WHERE operator_id=?", (OPERATOR_ID,),
        ))
        if members == expected_mints:
            return {"action": "already_registered", "operator_id": OPERATOR_ID,
                    "members": len(members)}
        raise ValueError("WATCHTOWER_DEEP has a partial or divergent registration")
    # Recheck every admitted mint while holding the writer lane. An intervening
    # WATCHTOWER admission or selected-path change must abort the whole unit.
    evidence = [qualify_historical_mint(conn, mint) for mint in expected_mints]
    if not all(item["eligible"] for item in evidence):
        raise ValueError("historical mint changed or conflicts with current ownership")
    timestamp = int(time.time()) if now is None else int(now)
    observed = [conn.execute(
        "SELECT funder_block_time FROM wt_walkback_queue WHERE mint=?", (mint,),
    ).fetchone()[0] for mint in expected_mints]
    evidence_digest = hashlib.sha256(json.dumps(
        evidence, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    summary = (
        "WT-linked Deep funding branch; 26 route-qualified historical launches. "
        "Rotating-wallet automatic detection is not qualified."
    )
    conn.execute(
        "INSERT INTO operators(operator_id,status,confidence,first_seen,last_seen,"
        "summary,review_state,display_name,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (OPERATOR_ID, "CONFIRMED", "HIGH", min(observed), max(observed),
         summary, "REVIEWED", "WATCHTOWER_DEEP", timestamp, timestamp),
    )
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{SOURCE_ID}:{expected_digest}"))
    evidence_package = json.dumps({
        "source": SOURCE_ID, "detector_version": DETECTOR_VERSION,
        "accepted_digest": expected_digest, "evidence_digest": evidence_digest,
        "accepted_count": len(expected_mints), "automation": "OFF",
    }, sort_keys=True)
    conn.execute(
        "INSERT INTO operator_identity_events(event_id,operator_id,event_type,"
        "timestamp,analyst,evidence_revision,reason,payload_json) VALUES(?,?,?,?,?,?,?,?)",
        (event_id, OPERATOR_ID, "CONFIRMED", timestamp,
         "user_authorized_20260921", DETECTOR_VERSION,
         "Distinct historical Deep funding route; no prospective admission", evidence_package),
    )
    conn.execute(
        "INSERT INTO operator_identity_state(operator_id,identity_status,activity_status,"
        "source_population_id,source_population_revision,confirmation_evidence_package,"
        "disposition_at_confirmation,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (OPERATOR_ID, "CONFIRMED", "ACTIVE", SOURCE_ID, DETECTOR_VERSION,
         evidence_package, "ACTIVE_MANUAL", timestamp),
    )
    conn.execute(
        "INSERT INTO operation_registry_dispositions(operator_id,disposition,manual_reviewer,"
        "reason,source_candidate_id,updated_at) VALUES(?,?,?,?,?,?)",
        (OPERATOR_ID, "ACTIVE_MANUAL", "user_authorized_20260921",
         "Historical route-qualified Deep branch; automatic detector OFF",
         SOURCE_ID, timestamp),
    )
    conn.execute(
        "INSERT INTO operation_qualification_contracts(contract_id,operator_id,"
        "qualification_category,automation_eligibility,detector_version,parent_mechanism,"
        "source_candidate_id,benchmark_json,contract_json,evidence_lineage_json,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid5(uuid.NAMESPACE_URL, f"{SOURCE_ID}:contract")), OPERATOR_ID,
         "CONFIRMED", "REVIEW_ONLY", DETECTOR_VERSION, "WSOL_WRAP_CLOSE",
         SOURCE_ID, json.dumps({"historical_accepted": len(expected_mints),
                                "watchtower_controls": expected_watchtower_count}, sort_keys=True),
         evidence_package, json.dumps({"evidence_digest": evidence_digest}, sort_keys=True),
         timestamp),
    )
    for mint in expected_mints:
        conn.execute(
            "INSERT INTO operator_launch_membership(mint,operator_id,source_population_id,"
            "assigned_at,event_id) VALUES(?,?,?,?,?)",
            (mint, OPERATOR_ID, SOURCE_ID, timestamp, event_id),
        )
        conn.execute(
            "INSERT INTO operator_launch_assignment_history(assignment_id,mint,from_operator_id,"
            "to_operator_id,timestamp,analyst,evidence_revision,reason,event_id) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid5(uuid.NAMESPACE_URL, f"{SOURCE_ID}:{mint}:assignment")),
             mint, None, OPERATOR_ID, timestamp, "user_authorized_20260921",
             DETECTOR_VERSION, "Time-ordered observed Deep route", event_id),
        )
    from src.ops.manual_registry import metrics
    activity = metrics(observed, now=timestamp)
    activity["source_provenance"] = {
        "contract": "route-qualified historical Deep primary members",
        "accepted_digest": expected_digest,
    }
    conn.execute(
        "INSERT INTO operation_activity_snapshots(snapshot_id,operator_id,observed_at,"
        "timestamp_semantics,metrics_json,activity_state) VALUES(?,?,?,?,?,?)",
        (str(uuid.uuid4()), OPERATOR_ID, timestamp,
         "Historical Walkback funded-at timestamps; prospective detection OFF",
         json.dumps(activity, sort_keys=True), activity["activity_state"]),
    )
    final_wt_count = conn.execute(
        "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?",
        ("04265d9f-6eb2-568c-a49e-9253091a4dbb",),
    ).fetchone()[0]
    if final_wt_count != expected_watchtower_count:
        raise ValueError("WATCHTOWER membership changed during registration")
    return {"action": "registered", "operator_id": OPERATOR_ID,
            "members": len(expected_mints), "accepted_digest": expected_digest}
