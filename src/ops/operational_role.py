"""Evidence-backed operational-role projection for analyst UI surfaces."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


ROLE_LABELS = {
    "TREASURY_TO_SUBPROV": ("Treasury", "Subprovider / Provisioning Wallet"),
    "SUBPROV_TO_CREATOR": ("Subprovider / Provisioning Wallet", "Creator"),
}


def derive_operational_role(family: dict[str, Any], infrastructure: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project a compact lifecycle using persisted edges and membership only.

    No absent hop is inferred: every returned edge cites its persisted
    relationship type (or the canonical population-membership projection).
    """
    infrastructure = infrastructure or {}
    paths = [p for p in infrastructure.get("funding_paths") or [] if p.get("from") and p.get("to")]
    members = set(family.get("member_wallets") or [])
    anchors = members | {x for x in (family.get("family_anchor"), family.get("terminal_entity")) if x}
    disposition = str((family.get("reconciliation") or {}).get("disposition") or
                      (family.get("presentation") or {}).get("disposition") or "UNRESOLVED")
    treasuries = set(family.get("treasuries") or infrastructure.get("treasuries") or [])
    clients = set(family.get("client_wallets") or infrastructure.get("persistent_clients") or [])

    if disposition == "CONFIRMED_OPERATION":
        current_role = "Confirmed Operator"
    elif disposition == "INFRASTRUCTURE":
        current_role = "Shared Infrastructure"
    elif len(members) > 1 and len(treasuries) > 1:
        current_role = "Shared Infrastructure"
    elif anchors & treasuries:
        current_role = "Operational Treasury"
    elif anchors & clients or any(p.get("from") in anchors and p.get("type") == "SUBPROV_TO_CREATOR" for p in paths):
        current_role = "Provisioning Controller"
    elif family.get("session_count"):
        current_role = "Session Wallet"
    else:
        current_role = "Unknown"

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        edge_type = str(path.get("type") or "")
        labels = ROLE_LABELS.get(edge_type)
        if not labels:
            continue
        left, right = labels
        if current_role == "Provisioning Controller" and path.get("from") in anchors:
            left = "Provisioning Controller"
        if current_role == "Provisioning Controller" and path.get("to") in anchors:
            right = "Provisioning Controller"
        if current_role == "Shared Infrastructure" and (path.get("from") in anchors or path.get("to") in anchors):
            left = "Shared Infrastructure" if path.get("from") in anchors else left
            right = "Shared Infrastructure" if path.get("to") in anchors else right
        elif current_role == "Operational Treasury" and path.get("from") in anchors:
            left = "Operational Treasury"
        grouped[(left, right, edge_type)].append(path)

    edges = []
    for (left, right, edge_type), observations in grouped.items():
        signatures = {p.get("signature") for p in observations if p.get("signature")}
        edges.append({
            "from": left, "to": right, "relationship_type": edge_type,
            "observation_count": len(observations), "transaction_count": len(signatures),
            "wallet_count": len({p.get("from") for p in observations} | {p.get("to") for p in observations}),
            "wallets": sorted({p.get("from") for p in observations} | {p.get("to") for p in observations}),
            "transactions": sorted(signatures),
        })

    # A launch hop is supported only by persisted source_mint edge provenance.
    launch_paths = [p for p in paths if p.get("type") == "SUBPROV_TO_CREATOR" and p.get("source_mint")]
    if launch_paths:
        grouped_launches: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for path in launch_paths:
            grouped_launches[str(path.get("to"))].append(path)
        edges.append({
            "from": "Creator", "to": "Launch", "relationship_type": "EDGE_SOURCE_MINT",
            "observation_count": len(launch_paths),
            "transaction_count": len({p.get("signature") for p in launch_paths if p.get("signature")}),
            "wallet_count": len(grouped_launches), "wallets": sorted(grouped_launches),
            "launches": sorted({p.get("source_mint") for p in launch_paths if p.get("source_mint")}),
            "transactions": sorted({p.get("signature") for p in launch_paths if p.get("signature")}),
        })

    ordered_roles = []
    for role in ("Treasury", "Operational Treasury", "Subprovider / Provisioning Wallet", "Provisioning Controller",
                 "Shared Infrastructure", "Provisioning Wallet", "Session Wallet", "Creator", "Launch"):
        if any(edge["from"] == role or edge["to"] == role for edge in edges):
            ordered_roles.append(role)
    if not ordered_roles:
        ordered_roles = [current_role]

    return {
        "current_role": current_role,
        "nodes": [{"role": role, "current": role == current_role} for role in ordered_roles],
        "edges": edges,
        "observation_count": sum(edge["observation_count"] for edge in edges),
        "relationship_count": len(edges),
        "evidence_backed": bool(edges),
        "source": "Recorded provisioning relationships and canonical launch membership",
    }
