"""Behavioural matching and controlled automatic admission for reviewed P3R profiles."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class P3RProfileContract:
    operator_id: str
    display_name: str
    route: tuple[tuple[int, str, int], ...]
    require_full_atomic: bool = False


@dataclass(frozen=True)
class P3RCandidateMatch:
    mint: str
    matching_operator_ids: tuple[str, ...]
    matching_profiles: tuple[str, ...]
    state: str
    reason: str


def load_contracts(conn: sqlite3.Connection) -> tuple[P3RProfileContract, ...]:
    """Load the reviewed, address-independent contracts from active profiles."""
    rows = conn.execute(
        "SELECT o.operator_id, o.display_name, p.provenance_json "
        "FROM operators o JOIN operation_registry_dispositions d USING(operator_id) "
        "JOIN operation_behavioural_profiles p USING(operator_id) "
        "WHERE d.disposition='ACTIVE_MANUAL' AND o.status!='MERGED' "
        "AND (o.display_name='P3R' OR o.display_name LIKE 'P3R_%')"
    ).fetchall()
    contracts: list[P3RProfileContract] = []
    unified_p3r_operator_ids: set[str] = set()
    for operator_id, name, provenance_json in rows:
        provenance = json.loads(provenance_json)
        if name == "P3R_13A04":
            ladder = tuple(int(amount) for amount in provenance["funding_ladder_lamports"])
            contracts.append(P3RProfileContract(
                operator_id, name,
                ((1, "PLAIN_XFER", ladder[4]), (2, "WSOL_WRAP_CLOSE", ladder[3]),
                 (3, "PLAIN_XFER", ladder[2]), (4, "WSOL_WRAP_CLOSE", ladder[1])),
            ))
        elif name == "P3R" and operator_id not in unified_p3r_operator_ids:
            unified_p3r_operator_ids.add(operator_id)
            contracts.append(P3RProfileContract(
                operator_id, "P3R", ((1, "WSOL_WRAP_CLOSE", 99999985000),), True,
            ))
    return tuple(sorted(contracts, key=lambda contract: contract.display_name))


def _features_for_mint(conn: sqlite3.Connection, mint: str) -> tuple[set[tuple[int, str, int]], bool]:
    edges = {
        (int(depth), str(mechanism), int(amount))
        for depth, mechanism, amount in conn.execute(
            "SELECT hop_depth, mechanism, amount_lamports FROM wt_walkback_edge_candidates "
            "WHERE mint=? AND selection_status='SELECTED' AND amount_lamports IS NOT NULL",
            (mint,),
        )
    }
    full_atomic = bool(conn.execute(
        "SELECT 1 FROM wt_walkback_atomic_flows WHERE mint=? AND has_create=1 "
        "AND has_sync_native=1 AND has_close=1 AND transfer_lamports=99997955720 LIMIT 1",
        (mint,),
    ).fetchone())
    return edges, full_atomic


def evaluate_mint(conn: sqlite3.Connection, mint: str) -> P3RCandidateMatch | None:
    """Return a nomination-only candidate result, or ``None`` when unmatched."""
    edges, full_atomic = _features_for_mint(conn, mint)
    matching = [
        contract for contract in load_contracts(conn)
        if set(contract.route).issubset(edges)
        and (not contract.require_full_atomic or full_atomic)
    ]
    if not matching:
        return None
    matching = sorted(matching, key=lambda contract: contract.display_name)
    ambiguous = len(matching) > 1
    return P3RCandidateMatch(
        mint=mint,
        matching_operator_ids=tuple(contract.operator_id for contract in matching),
        matching_profiles=tuple(contract.display_name for contract in matching),
        state="AMBIGUOUS_BEHAVIOURAL_CANDIDATE" if ambiguous else "BEHAVIOURAL_CANDIDATE",
        reason=("Shared address-independent fingerprint; analyst disposition required."
                if ambiguous else "Reviewed address-independent fingerprint; analyst disposition required."),
    )


def admit_unambiguous_p3r_match(conn: sqlite3.Connection, mint: str, *, core_db_path: str | None = None) -> str:
    """Admit an exact, unambiguous reviewed P3R fingerprint.

    P3R is the unified AF500/EC1 operational identity. P3R_13A04 remains a
    separate exact-ladder identity. Existing conflicting assignments remain
    untouched.
    """
    match = evaluate_mint(conn, mint)
    if match is None or match.matching_profiles not in {("P3R",), ("P3R_13A04",)}:
        return "not_unambiguous_p3r"
    operator_id = match.matching_operator_ids[0]
    existing = conn.execute(
        "SELECT operator_id FROM operator_launch_membership WHERE mint=?", (mint,)
    ).fetchone()
    if existing and existing[0] != operator_id:
        return "existing_other_operator"
    # Preserve the former AF500/EC1 member sets as immutable historical profile
    # aliases. New unified matches live in authoritative launch membership rather
    # than being silently attributed to either legacy profile.
    if match.matching_profiles == ("P3R_13A04",):
        profile = conn.execute(
            "SELECT profile_id, member_mints_json FROM operation_behavioural_profiles "
            "WHERE operator_id=? ORDER BY profile_version DESC LIMIT 1", (operator_id,)
        ).fetchone()
        if profile is None:
            return "missing_profile"
        members = json.loads(profile[1])
        if mint not in members:
            members.append(mint)
            conn.execute(
                "UPDATE operation_behavioural_profiles SET member_mints_json=? WHERE profile_id=?",
                (json.dumps(members), profile[0]),
            )
    now = int(time.time())
    conn.execute(
        "INSERT INTO operator_launch_membership(mint,operator_id,source_population_id,assigned_at,event_id) "
        "VALUES (?,?,?,?,NULL) ON CONFLICT(mint) DO NOTHING",
        (mint, operator_id,
         "walkback_p3r_unified_matcher_v1" if match.matching_profiles == ("P3R",)
         else "walkback_p3r_13a04_matcher_v1", now),
    )
    from src.ops.manual_registry import refresh_operator_activity_snapshot
    refresh_operator_activity_snapshot(conn, operator_id, core_db_path=core_db_path, now=now)
    return "admitted" if not existing else "already_admitted"


def admit_unambiguous_13a04_match(conn: sqlite3.Connection, mint: str, *, core_db_path: str | None = None) -> str:
    """Compatibility wrapper retaining the former 13A04-only public gate."""
    match = evaluate_mint(conn, mint)
    if match is None or match.matching_profiles != ("P3R_13A04",):
        return "not_unambiguous_13a04"
    return admit_unambiguous_p3r_match(conn, mint, core_db_path=core_db_path)
