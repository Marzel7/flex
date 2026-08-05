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

    # source_mint is walkback observation context. It associates the creator
    # funding edge with the launch under investigation, but it does NOT mean
    # the mint participated in funding_tx_signature.
    launch_paths = [p for p in paths if p.get("type") == "SUBPROV_TO_CREATOR" and p.get("source_mint")]
    if launch_paths:
        grouped_launches: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for path in launch_paths:
            grouped_launches[str(path.get("to"))].append(path)
        edges.append({
            "from": "Creator", "to": "Launch Context", "relationship_type": "WALKBACK_SOURCE_MINT",
            "observation_count": len(launch_paths),
            "transaction_count": len({p.get("signature") for p in launch_paths if p.get("signature")}),
            "wallet_count": len(grouped_launches), "wallets": sorted(grouped_launches),
            "launches": sorted({p.get("source_mint") for p in launch_paths if p.get("source_mint")}),
            "transactions": sorted({p.get("signature") for p in launch_paths if p.get("signature")}),
        })

    ordered_roles = []
    for role in ("Treasury", "Operational Treasury", "Subprovider / Provisioning Wallet", "Provisioning Controller",
                 "Shared Infrastructure", "Provisioning Wallet", "Session Wallet", "Creator", "Launch Context"):
        if any(edge["from"] == role or edge["to"] == role for edge in edges):
            ordered_roles.append(role)
    if not ordered_roles:
        ordered_roles = [current_role]

    observations = []
    seen_observations = set()
    # An Operational Treasury row must begin at the highlighted treasury.
    # Join only persisted edges sharing the same launch context; never present
    # an unrelated downstream creator edge as though the treasury funded it.
    if current_role == "Operational Treasury":
        treasury_paths = [
            path for path in paths
            if path.get("type") == "TREASURY_TO_SUBPROV" and path.get("from") in anchors
        ]
        for treasury_path in treasury_paths:
            downstream = [
                path for path in launch_paths
                if path.get("from") == treasury_path.get("to")
                and path.get("source_mint") == treasury_path.get("source_mint")
            ]
            for creator_path in downstream:
                observations.append({
                    "controller": treasury_path.get("from"),
                    "intermediary": treasury_path.get("to"),
                    "creator": creator_path.get("to"),
                    "launch": creator_path.get("source_mint"),
                    "launch_label": creator_path.get("launch_label") or "Launch",
                    "mechanism": creator_path.get("mechanism"),
                    "observed_at": max(treasury_path.get("observed_at") or 0, creator_path.get("observed_at") or 0),
                    "transaction_at": creator_path.get("transaction_at"),
                    "transaction": creator_path.get("signature"),
                    "funding_hops": [
                        {"from": treasury_path.get("from"), "to": treasury_path.get("to"),
                         "mechanism": treasury_path.get("mechanism"), "transaction": treasury_path.get("signature"),
                         "transaction_at": treasury_path.get("transaction_at")},
                        {"from": creator_path.get("from"), "to": creator_path.get("to"),
                         "mechanism": creator_path.get("mechanism"), "transaction": creator_path.get("signature"),
                         "transaction_at": creator_path.get("transaction_at")},
                    ],
                })
        observations.sort(key=lambda item: item.get("transaction_at") or 0, reverse=True)
    else:
        display_paths = sorted(launch_paths, key=lambda item: item.get("observed_at") or 0, reverse=True)
        for path in display_paths:
            key = (path.get("from"), path.get("to"), path.get("source_mint"), path.get("mechanism"))
            if key in seen_observations:
                continue
            seen_observations.add(key)
            observations.append({
                "controller": path.get("from"), "creator": path.get("to"),
                "launch": path.get("source_mint"),
                "launch_label": path.get("launch_label") or "Launch",
                "mechanism": path.get("mechanism"), "observed_at": path.get("observed_at"),
                "transaction_at": path.get("transaction_at"),
                "transaction": path.get("signature"),
            })

    return {
        "current_role": current_role,
        "nodes": [{"role": role, "current": role == current_role} for role in ordered_roles],
        "edges": edges,
        "observation_count": sum(edge["observation_count"] for edge in edges),
        "relationship_count": len(edges),
        "observed_relationships": observations,
        "related_launch_count": len(observations),
        "evidence_backed": bool(edges),
        "source": "Recorded provisioning relationships and canonical launch membership",
    }
