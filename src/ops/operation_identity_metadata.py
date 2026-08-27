"""Read-only identity, family, and infrastructure presentation metadata."""
from __future__ import annotations
from typing import Any

from src.ops.watchtower_fingerprint_observational_reader import watchtower_source_manifest

BYZC = "ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY"

IDENTITIES = {
    "Byzantine": {"human_name": "Byzantine", "family": None, "infrastructure": "STRONG_SHARED_ADDRESS_EVIDENCE", "common_root": "NOT_APPLICABLE", "coverage_note": "32/32 qualification cohort shared direct funder"},
    "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER": {"human_name": "Sentinel", "mechanism": "30 SOL 14.479K Ladder", "fingerprint_id": "30SOL-WSOL-LADDER-14479K-v1", "family": "30 SOL WSOL Ladder", "family_relation": "MECHANISM_FAMILY", "infrastructure": "NO_SHARED_INFRASTRUCTURE_PROVEN", "common_root": "NOT_PROVEN"},
    "P3R": {"human_name": "Leviathan", "mechanism": "100 SOL WSOL Close", "fingerprint_id": "100SOL-WSOL-CLOSE-v1", "family": None, "infrastructure": "RECURRING_MULTI_ADDRESS_INFRASTRUCTURE", "common_root": "NOT_PROVEN"},
    "P3R_13A04": {"human_name": "Harbinger", "mechanism": "30 SOL 5K Ladder", "fingerprint_id": "30SOL-5K-LADDER-v1", "family": "30 SOL WSOL Ladder", "family_relation": "MECHANISM_FAMILY", "infrastructure": "INSUFFICIENT_EVIDENCE", "common_root": "INSUFFICIENT_EVIDENCE"},
    "WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K": {"human_name": "1 SOL Provision Close", "family": None, "infrastructure": "SHARED_NON_EXCLUSIVE_INFRASTRUCTURE", "common_root": "NOT_PROVEN", "coverage_note": "39/44 frozen recurrent-infrastructure coverage; corroboration only"},
    "WATCHTOWER": {"human_name": "WATCHTOWER", "family": None, "infrastructure": "DYNAMIC_ROLE_INFRASTRUCTURE", "common_root": "NOT_PROVEN"},
}

def identity_metadata(conn: Any, display_name: str, membership_count: int) -> dict[str, Any] | None:
    value = IDENTITIES.get(display_name)
    if not value: return None
    result = dict(value); result["stable_display_name"] = display_name
    result["related_operations"] = [item["human_name"] for name, item in IDENTITIES.items() if name != display_name and item.get("family") == value.get("family")] if value.get("family") else []
    if display_name == "Byzantine":
        try:
            linked = conn.execute("SELECT COUNT(DISTINCT m.mint) FROM operator_launch_membership m JOIN wt_walkback_edge_candidates e ON e.mint=m.mint AND e.selection_status='SELECTED' WHERE m.operator_id=(SELECT operator_id FROM operators WHERE display_name='Byzantine' LIMIT 1) AND e.candidate_parent=?", (BYZC,)).fetchone()[0]
            result["coverage"] = f"{linked}/{membership_count}" if membership_count else "Not measured"
            if linked == membership_count and membership_count: result["coverage_note"] = f"100% current shared direct-funder coverage ({linked}/{membership_count})"
        except Exception: result["coverage"] = "32/32 qualification cohort"
    elif display_name == "WATCHTOWER":
        manifest = watchtower_source_manifest(conn); coverage = manifest["mutability_coverage"]
        result["coverage"] = f"{coverage['confirmed_treasuries']} treasuries; {coverage['confirmed_subproviders']} confirmed sub-providers"
        result["monitoring_strategy"] = "DYNAMIC_ROLE_DISCOVERY"
        result["mutation_resilience"] = manifest["mutation_resilience"]
    elif display_name == "P3R":
        try:
            count = conn.execute("SELECT COUNT(DISTINCT e.candidate_parent) FROM operator_launch_membership m JOIN wt_walkback_edge_candidates e ON e.mint=m.mint AND e.selection_status='SELECTED' WHERE m.operator_id=(SELECT operator_id FROM operators WHERE display_name='P3R' LIMIT 1) AND e.candidate_parent IS NOT NULL").fetchone()[0]
            result["coverage"] = f"{count} retained direct-funder addresses"
        except Exception: result["coverage"] = "Not measured"
    else: result.setdefault("coverage", result.get("coverage_note", "Not measured"))
    return result
