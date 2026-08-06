"""X75.0 Discovery <-> Operations Convergence.

Re-projects EmergingOperatorService's existing, unchanged output into the
three buckets Discovery's converged workflow needs:

  Known Operations        -- confirmed operators (any operator, not only
                              WATCHTOWER), each with recent activity and any
                              investigation populations that resemble it.
  Potential Expansions    -- OPERATOR_CANDIDATE / REVIEW investigation
                              populations that match a KNOWN operation's own
                              declared defining evidence (see
                              src/ops/operation_matching_profile.py), scored
                              per-operation rather than assumed to be
                              WATCHTOWER-shaped.
  New Investigations      -- UNRESOLVED populations that do not resemble any
                              known operation closely enough to be proposed
                              as an expansion.

This module reads EmergingOperatorService's already-computed disposition
groups and applies matching-profile scoring on top; it performs no
detection, no RPC, no writes, and does not alter DispositionResolver's own
CONFIRMED_OPERATION/OPERATOR_CANDIDATE/REVIEW/INFRASTRUCTURE/UNRESOLVED
verdicts. Discovery still only PROPOSES here -- confirmation happens only
via Treasury Review -> Approve/Link -> OperatorIdentityGovernanceService,
unchanged by this module.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from src.ops.operation_matching_profile import (
    get_profile,
    score_population_against_profile,
)


def _treasury_review_signals(conn: sqlite3.Connection, family_wallets: frozenset[str]) -> dict[str, Any]:
    """Cross-reference this population's own wallets against
    wt_treasury_review -- the actual treasury-candidate pipeline, which
    already records real subprovisioner fan-out (distinct_subprovs) and
    walkback-confirmed lineage for wallets not yet in any operator's
    confirmed entity set. This is a genuine additional evidence source, not
    a heuristic string match: a population containing a wallet with
    real recorded subprovisioner fan-out IS structurally WATCHTOWER-shaped
    even before that wallet has ever been promoted anywhere."""
    if not family_wallets:
        return {"has_subprov_fanout": False, "has_walkback_evidence": False, "pending_count": 0}
    placeholders = ",".join("?" for _ in family_wallets)
    try:
        rows = conn.execute(
            f"SELECT distinct_subprovs, has_walkback_evidence, status FROM wt_treasury_review "
            f"WHERE treasury IN ({placeholders})",
            tuple(family_wallets),
        ).fetchall()
    except sqlite3.Error:
        return {"has_subprov_fanout": False, "has_walkback_evidence": False, "pending_count": 0}
    has_subprov_fanout = any((r["distinct_subprovs"] if hasattr(r, "keys") else r[0]) and
                              (r["distinct_subprovs"] if hasattr(r, "keys") else r[0]) > 1 for r in rows)
    has_walkback = any((r["has_walkback_evidence"] if hasattr(r, "keys") else r[1]) for r in rows)
    pending_count = sum(
        1 for r in rows
        if (r["status"] if hasattr(r, "keys") else r[2]) == "PENDING_REVIEW"
    )
    return {"has_subprov_fanout": has_subprov_fanout, "has_walkback_evidence": has_walkback,
            "pending_count": pending_count}


def _confirmed_recoveries(conn: sqlite3.Connection, operator_id: str) -> list[dict[str, Any]]:
    """Return only expansion events backed by current canonical membership.

    The immutable ledger can contain an event even when a failed/legacy caller
    never completed the asset projection. Such an orphan is audit history, not
    a confirmed recovery, and must not appear in Discovery or Mission Control.
    """
    try:
        rows = conn.execute(
            "SELECT event_type, payload_json, timestamp, evidence_revision, analyst FROM operator_identity_events "
            "WHERE operator_id=? AND event_type IN ('TREASURY_ADDED','IDENTITY_EXPANDED') "
            "ORDER BY timestamp DESC", (operator_id,),
        ).fetchall()
    except sqlite3.Error:
        return []
    result = []
    for row in rows:
        revision = row["evidence_revision"] if hasattr(row, "keys") else row[3]
        analyst = row["analyst"] if hasattr(row, "keys") else row[4]
        # Schema backfills establish the current asset projection; they do
        # not represent a newly recovered Operation in analyst time.
        if str(revision or "").startswith("backfill:") or str(analyst or "").startswith("system:"):
            continue
        try:
            payload = json.loads(row["payload_json"] if hasattr(row, "keys") else row[1])
        except (TypeError, ValueError):
            continue
        value = payload.get("asset_value")
        asset_type = payload.get("asset_type") or "TREASURY"
        if not value:
            continue
        try:
            active = conn.execute(
                "SELECT 1 FROM operator_identity_assets WHERE operator_id=? "
                "AND asset_type=? AND asset_value=? AND status='ACTIVE' LIMIT 1",
                (operator_id, asset_type, value),
            ).fetchone()
        except sqlite3.Error:
            active = None
        if active:
            # A Treasury Review expansion is confirmed only when the
            # workspace's immutable approval action also exists. This keeps
            # partial/test-leaked governance events out of recovery metrics.
            if str(revision or "").startswith("treasury-review:"):
                try:
                    approved = conn.execute(
                        "SELECT 1 FROM wt_treasury_review_actions WHERE treasury=? "
                        "AND action='APPROVE_TREASURY' LIMIT 1", (value,),
                    ).fetchone()
                except sqlite3.Error:
                    approved = None
                if not approved:
                    continue
            result.append({"timestamp": row["timestamp"] if hasattr(row, "keys") else row[2],
                           "asset_type": asset_type, "asset_value": value})
    return result


def _evidence_labels(values: frozenset[str]) -> list[str]:
    labels = {
        "TREASURY": "Operational treasury", "SUB_PROVISIONER": "Provisioning structure",
        "CLIENT": "Provisioning pattern", "CONFIRMED_TREASURY_CONTROL": "Treasury control",
        "RPC_CONFIRMED_LINEAGE": "Walkback lineage", "PERSISTENT_CONTROLLER_REUSE": "Known provisioning",
        "RETURN_TO_CONTROLLER": "Return to controller", "TREASURY_RELATIONSHIPS": "Funding behaviour",
        "PROVISIONING_LINEAGE": "Provisioning lineage", "PROVISIONING_SESSIONS": "Operational structure",
    }
    return [labels.get(value, value.replace("_", " ").title()) for value in sorted(values)]


def _family_entity_types(family: dict[str, Any], *, treasury_review_signals: dict[str, Any] | None = None) -> frozenset[str]:
    """Derived from the family's own dominant_topology description (its own
    stated evidence chain) plus populated wallet-role lists. Topology is
    checked first and constrains which role claims are made: a population
    whose topology explicitly says "Treasury -> client -> creator" (e.g.
    B48k) must not also claim SUB_PROVISIONER just because a treasuries
    list happens to be populated -- the topology string is the population's
    own description of what role each layer plays, and a shared treasury
    address does not imply a shared provisioning-chain structure."""
    topology = str(family.get("dominant_topology") or "").lower()
    # A topology description is only usable as a POSITIVE or NEGATIVE signal
    # when it actually describes a chain -- "evidence accumulation
    # incomplete" (or empty) means "we don't yet know the topology," not
    # "this population has no treasury/subprov/client role," so it must
    # fall back to the populated role lists rather than being read as an
    # implicit denial of every role.
    topology_known = bool(topology) and "incomplete" not in topology and "unknown" not in topology
    types: set[str] = set()
    if (topology_known and "treasury" in topology) or (not topology_known and (family.get("treasuries") or family.get("member_treasuries"))):
        types.add("TREASURY")
    signals = treasury_review_signals or {}
    if (topology_known and ("subprov" in topology or "provisioning" in topology)) \
            or (not topology_known and (family.get("subprovisioners") or family.get("sub_provisioners"))) \
            or signals.get("has_subprov_fanout"):
        types.add("SUB_PROVISIONER")
    if (topology_known and "client" in topology) or (not topology_known and (family.get("client_wallets") or family.get("provisioning_clients"))):
        types.add("CLIENT")
    return frozenset(types)


def _family_evidence_types(family: dict[str, Any], *, treasury_review_signals: dict[str, Any] | None = None) -> tuple[frozenset[str], frozenset[str]]:
    """Best-effort extraction of control/population evidence types. Prefers
    the family's own reconciliation metadata (already computed by
    DispositionResolver) when present; falls back to structural signals
    from the family's own populated fields (never evidence_sources table
    names, which only say where data came from)."""
    control: set[str] = set()
    population: set[str] = set()
    reconciliation = family.get("reconciliation") or {}
    for item in reconciliation.get("supporting_evidence") or []:
        etype = item.get("evidence_type") if isinstance(item, dict) else None
        if etype:
            control.add(etype)
            population.add(etype)

    if family.get("treasuries") or family.get("member_treasuries"):
        population.add("TREASURY_RELATIONSHIPS")
        control.add("CONFIRMED_TREASURY_CONTROL")
    if family.get("walkback_descendant_count") or "provisioning" in (family.get("dominant_topology") or "").lower():
        population.add("PROVISIONING_LINEAGE")
    if family.get("session_count"):
        population.add("PROVISIONING_SESSIONS")
    unique_creators = family.get("unique_creators")
    creator_count = len(unique_creators) if isinstance(unique_creators, list) else (unique_creators or 0)
    if creator_count and (family.get("launches") or 0) and creator_count < (family.get("launches") or 1):
        # more launches than distinct creators -> creator reuse across launches
        control.add("CREATOR_REUSE_CONTROL")
    if family.get("client_wallets") or family.get("provisioning_clients"):
        control.add("PERSISTENT_CONTROLLER_REUSE")
    signals = treasury_review_signals or {}
    if signals.get("has_walkback_evidence"):
        control.add("RPC_CONFIRMED_LINEAGE")
    if signals.get("has_subprov_fanout"):
        population.add("PROVISIONING_LINEAGE")
        population.add("PROVISIONING_SESSIONS")
    return frozenset(control), frozenset(population)


def _operator_entity_addresses(conn: sqlite3.Connection, operator_id: str) -> frozenset[str]:
    rows = conn.execute(
        "SELECT entity_address FROM operator_entities WHERE operator_id=?", (operator_id,)
    ).fetchall()
    return frozenset(r["entity_address"] if hasattr(r, "keys") else r[0] for r in rows)


def _rejected_treasuries(conn: sqlite3.Connection) -> frozenset[str]:
    """Treasuries a human analyst has already explicitly REJECTED via
    Treasury Review. A population containing one of these must never be
    re-proposed as a Potential Expansion of any operator -- that would
    silently re-litigate a human decision this module has no authority to
    override (Registry governs; Discovery proposes)."""
    try:
        rows = conn.execute(
            "SELECT treasury FROM wt_treasury_review WHERE status='REJECTED'"
        ).fetchall()
        return frozenset(r["treasury"] if hasattr(r, "keys") else r[0] for r in rows)
    except sqlite3.Error:
        return frozenset()


def _family_member_wallets(family: dict[str, Any]) -> frozenset[str]:
    wallets: set[str] = set()
    for key in ("member_wallets", "treasuries", "member_treasuries", "client_wallets", "provisioning_clients"):
        value = family.get(key)
        if isinstance(value, (list, tuple, set)):
            wallets.update(value)
    return frozenset(wallets)


def _known_operations(conn: sqlite3.Connection, list_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT operator_id, display_name, status, first_seen, last_seen, updated_at "
        "FROM operators WHERE status='CONFIRMED' ORDER BY display_name"
    ).fetchall()
    confirmed_cards = {
        str(c.get("family_id")): c for c in (list_payload.get("confirmed_operations_reconciled") or [])
    }
    result = []
    for row in rows:
        operator_id = row["operator_id"]
        profile = get_profile(operator_id, row["display_name"])
        card = confirmed_cards.get(operator_id)
        result.append({
            "operator_id": operator_id,
            "display_name": row["display_name"],
            "status": row["status"],
            "defining_evidence": {
                "entity_types": sorted(profile.defining_entity_types),
                "control_types": sorted(profile.defining_control_types),
                "population_types": sorted(profile.defining_population_types),
                "description": profile.description,
            },
            "recent_launches": card.get("launches") if card else None,
            "unique_creators": card.get("unique_creators") if card else None,
            "last_material_activity_at": card.get("last_material_activity_at") if card else row["last_seen"],
            "href": f"/intelligence/operator/{operator_id}",
        })
    return result


def _candidate_families(list_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Populations eligible to be scored as a Potential Expansion of a known
    Operation: OPERATOR_CANDIDATE (already close to confirmation) and
    UNRESOLVED (DispositionResolver has not yet found sufficient
    independently-provenanced identity evidence for a NEW operator, but the
    evidence it does carry may still resemble an EXISTING one). REVIEW and
    INFRASTRUCTURE populations are NOT re-scored here -- DispositionResolver
    already decided those dispositions for reasons unrelated to "which known
    operation does this resemble" (contradictory evidence / known shared
    infrastructure respectively), and this module must not second-guess
    that authoritative decision."""
    candidates = list(list_payload.get("operator_candidates_reconciled") or [])
    candidates += list(list_payload.get("active_investigations_reconciled") or [])
    return [family for family in candidates if family.get("analyst_lifecycle") != "DISMISSED"]


def build_convergence_view(conn: sqlite3.Connection, list_payload: dict[str, Any], *, min_score: float = 0.5) -> dict[str, Any]:
    """conn: read-only connection to database/wt_ops_v2.db.
    list_payload: the dict returned by EmergingOperatorService.list() (or
    ._list_uncached()) -- this module never calls that service itself, the
    caller supplies it, keeping this module a pure re-projection.

    Potential Expansions vs New Investigations is decided ONLY among
    OPERATOR_CANDIDATE/UNRESOLVED populations, scored against each known
    Operation's own declared matching profile. REVIEW and INFRASTRUCTURE
    populations pass through unchanged from DispositionResolver's own
    verdict -- this module never overrides them."""
    known_operations = _known_operations(conn, list_payload)
    profiles_by_operator = {op["operator_id"]: get_profile(op["operator_id"], op["display_name"]) for op in known_operations}
    rejected_treasuries = _rejected_treasuries(conn)

    potential_expansions: list[dict[str, Any]] = []
    new_investigations: list[dict[str, Any]] = []

    for family in _candidate_families(list_payload):
        family_wallets = _family_member_wallets(family)
        tr_signals = _treasury_review_signals(conn, family_wallets)
        entity_types = _family_entity_types(family, treasury_review_signals=tr_signals)
        control_types, population_types = _family_evidence_types(family, treasury_review_signals=tr_signals)
        if family_wallets & rejected_treasuries:
            # A human analyst already rejected at least one of this
            # population's own wallets as a treasury -- never re-propose
            # this population as an expansion candidate for anything.
            new_investigations.append({
                "family_id": family.get("family_id"),
                "family_name": family.get("family_name"),
                "launches": family.get("launches"),
                "unique_creators": family.get("unique_creators"),
                "disposition": (family.get("reconciliation") or {}).get("disposition") or "UNRESOLVED",
                "profile_href": family.get("profile_href"),
                "investigation_trigger": family.get("investigation_trigger"),
                "note": "Contains a treasury previously rejected in Treasury Review.",
            })
            continue
        best_match = None
        for operator_id, profile in profiles_by_operator.items():
            match = score_population_against_profile(
                profile,
                entity_types=entity_types,
                control_types=control_types,
                population_types=population_types,
            )
            if match.score >= min_score and (best_match is None or match.score > best_match.score):
                best_match = match
        if best_match:
            profile = profiles_by_operator[best_match.operator_id]
            matched_all = (best_match.matched_entity_types | best_match.matched_control_types |
                           best_match.matched_population_types)
            missing_all = ((profile.defining_entity_types - best_match.matched_entity_types) |
                           (profile.defining_control_types - best_match.matched_control_types) |
                           (profile.defining_population_types - best_match.matched_population_types))
            strength = "EXACT" if best_match.score == 1.0 else ("STRONG" if best_match.score >= 0.67 else "WEAK")
            potential_expansions.append({
                "family_id": family.get("family_id"),
                "family_name": family.get("family_name"),
                "launches": family.get("launches"),
                "unique_creators": family.get("unique_creators"),
                "matched_operator_id": best_match.operator_id,
                "matched_operator_display_name": best_match.display_name,
                "match_score": best_match.score,
                "match_reason": best_match.reason,
                "match_strength": strength,
                "recovered_because": _evidence_labels(matched_all),
                "missing_evidence": _evidence_labels(missing_all),
                "pending_treasury_reviews": tr_signals.get("pending_count", 0),
                "treasury_review_href": "/intelligence/treasury-review",
                "profile_href": family.get("profile_href"),
                "investigation_trigger": family.get("investigation_trigger"),
            })
        else:
            new_investigations.append({
                "family_id": family.get("family_id"),
                "family_name": family.get("family_name"),
                "launches": family.get("launches"),
                "unique_creators": family.get("unique_creators"),
                "disposition": (family.get("reconciliation") or {}).get("disposition") or "UNRESOLVED",
                "profile_href": family.get("profile_href"),
            })

    shared_infrastructure = [
        {
            "family_id": f.get("family_id"),
            "family_name": f.get("family_name"),
            "launches": f.get("launches"),
            "disposition": "INFRASTRUCTURE",
            "profile_href": f.get("profile_href"),
            "investigation_trigger": f.get("investigation_trigger"),
        }
        for f in (list_payload.get("infrastructure_alerts_reconciled") or [])
    ]

    review = [
        {
            "family_id": f.get("family_id"),
            "family_name": f.get("family_name"),
            "launches": f.get("launches"),
            "disposition": "REVIEW",
            "profile_href": f.get("profile_href"),
            "investigation_trigger": f.get("investigation_trigger"),
        }
        for f in (list_payload.get("review_cases_reconciled") or [])
    ]

    now = int(time.time())
    for operation in known_operations:
        matches = [item for item in potential_expansions
                   if item["matched_operator_id"] == operation["operator_id"]]
        recoveries = _confirmed_recoveries(conn, operation["operator_id"])
        operation["exact_topology_matches"] = sum(item["match_strength"] == "EXACT" for item in matches)
        operation["strong_behaviour_matches"] = sum(item["match_strength"] == "STRONG" for item in matches)
        operation["weak_matches"] = sum(item["match_strength"] == "WEAK" for item in matches)
        operation["pending_treasury_reviews"] = sum(item["pending_treasury_reviews"] for item in matches)
        operation["recently_expanded"] = sum((row["timestamp"] or 0) >= now - 7 * 86400 for row in recoveries)
        operation["recovered_today"] = sum((row["timestamp"] or 0) >= now - 86400 for row in recoveries)
        operation["last_confirmed_recovery_at"] = recoveries[0]["timestamp"] if recoveries else None

    return {
        "known_operations": known_operations,
        "potential_expansions": potential_expansions,
        "new_investigations": new_investigations,
        "review": review,
        "shared_infrastructure": shared_infrastructure,
        "dismissed_investigations": [
            {
                "family_id": f.get("family_id"), "family_name": f.get("family_name"),
                "launches": f.get("launches"), "disposition": "DISMISSED",
                "profile_href": f.get("profile_href"),
                "investigation_trigger": f.get("investigation_trigger"),
                "reason": (f.get("investigation_lifecycle") or {}).get("reason_label"),
                "reopen_recommended": bool((f.get("investigation_lifecycle") or {}).get("reopen_recommended")),
                "material_changes": (f.get("investigation_lifecycle") or {}).get("material_changes") or [],
            }
            for f in list_payload.get("dismissed_investigations") or []
        ],
        "investigation_lifecycle_summary": list_payload.get("investigation_lifecycle_summary") or {},
        "recovery_summary": {
            "recovered_today": sum(operation["recovered_today"] for operation in known_operations),
            "potential_recoveries": len(potential_expansions),
            "treasury_reviews_pending": sum(item["pending_treasury_reviews"] for item in potential_expansions),
            "new_operations": len(new_investigations) + len(review) + len(shared_infrastructure),
        },
        "read_only": True,
    }
