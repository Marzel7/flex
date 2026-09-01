"""Read-only compact operation summary model.

Infrastructure and behavioural identity are deliberately independent: an
address can rotate without replacing an operation, and a behavioural variant
can be reviewed without creating one.  This module never performs RPC or
writes evidence.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


def _timestamp(value: str | int | None) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def build_operation_summary(operation: dict[str, Any], p3r_rows: list[dict[str, str]]) -> dict[str, Any]:
    """Build a bounded read model from already-retained evidence."""
    profile = operation.get("behavioural_profile") or {}
    metrics = (operation.get("activity_snapshot") or {}).get("metrics") or {}
    anchors: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in p3r_rows:
        if row.get("parent_first_funder_fee_payer"):
            grouped[row["parent_first_funder_fee_payer"]].append(row)
    for address, rows in grouped.items():
        observed = sorted(filter(None, (_timestamp(row.get("parent_first_funder_timestamp")) for row in rows)))
        anchors[address] = {
            "role": "INITIAL_FEE_PAYER", "address": address,
            "state": "ACTIVE", "qualification": "CONFIRMED_ANCHOR" if len(rows) >= 2 else "OBSERVED_ANCHOR",
            "observation_count": len(rows), "linked_launches": len({row.get("mint") for row in rows}),
            "first_observed": observed[0] if observed else None,
            "last_observed": observed[-1] if observed else None,
            "evidence": [{"signature": row.get("parent_first_funder_signature"), "source": row.get("parent_first_funder_intermediate_source")} for row in rows],
        }
    # WATCHTOWER and future operations use the identical anchor shape.
    if not anchors:
        for entity in operation.get("entities") or []:
            if entity.get("entity_type") == "TREASURY":
                address = entity.get("entity_address")
                if address:
                    anchors[address] = {"role": "TREASURY", "address": address, "state": "ACTIVE", "qualification": "CONFIRMED_ANCHOR", "observation_count": entity.get("evidence_count", 0), "linked_launches": entity.get("evidence_count", 0), "first_observed": None, "last_observed": None, "evidence": [{"source": "operator_entities"}]}
    ordered_anchors = sorted(anchors.values(), key=lambda anchor: (-anchor["observation_count"], anchor["address"]))
    representative = next((
        row for row in p3r_rows
        if ordered_anchors and row.get("parent_first_funder_fee_payer") == ordered_anchors[0]["address"]
    ), None)
    all_launches = sorted(p3r_rows, key=lambda row: _timestamp(row.get("parent_first_funder_timestamp")) or 0, reverse=True)
    retained_launches = operation.get("recent_launches") or []
    if retained_launches:
        by_mint = {row.get("mint"): row for row in all_launches if row.get("mint")}
        by_mint.update({row.get("mint"): row for row in retained_launches if row.get("mint")})
        all_launches = sorted(by_mint.values(), key=lambda row: _timestamp(row.get("parent_first_funder_timestamp")) or row.get("create_time") or 0, reverse=True)
    if not all_launches:
        all_launches = operation.get("recent_launches") or []
    if not all_launches and profile.get("member_mints"):
        history = {row["mint"]: row for row in operation.get("retained_funding_history") or []}
        all_launches = [{
            "mint": mint,
            "creator_wallet": (history.get(mint, {}).get("details") or {}).get("creator"),
            "subprov_wallet": history.get(mint, {}).get("terminal_entity"),
            "create_time": history.get(mint, {}).get("funding_block_time") or history.get(mint, {}).get("completed_at"),
            "funding_tx_signature": history.get(mint, {}).get("funding_tx_signature"),
            "funding_mechanism": history.get(mint, {}).get("funding_mechanism") or history.get(mint, {}).get("outcome_type"),
        } for mint in profile["member_mints"]]
    provenance = profile.get("provenance") or {}
    baseline = provenance.get("baseline_downstream") or provenance
    mechanism = next((row.get("mechanism") for row in p3r_rows if row.get("mechanism")), None)
    generic_mechanism = next((row.get("funding_mechanism") for row in all_launches if row.get("funding_mechanism")), None)
    amount_lamports = baseline.get("stored_amount_lamports")
    funding = None
    if amount_lamports is not None:
        funding = "%.6f SOL temporary WSOL close to creator" % (int(amount_lamports) / 1_000_000_000)
    return {
        "activity": {"state": (operation.get("activity_snapshot") or {}).get("activity_state") or operation.get("activity_status") or "ACTIVITY_UNKNOWN", "metrics": metrics, "last_observed": metrics.get("last_observed_launch_timestamp"), "time_since_last": metrics.get("time_since_last_observed_seconds")},
        "anchors": ordered_anchors,
        "primary_anchor": ordered_anchors[0] if ordered_anchors else None,
        "fingerprint": {"funding": funding or ("Temporary WSOL provision to creator" if p3r_rows else None), "route": "Initial fee payer → parent direct funder → creator" if p3r_rows else ("Treasury → sub-provider → creator" if any(row.get("treasury_wallet") or row.get("subprov_wallet") for row in all_launches) else None), "representative_path": {"funder": representative.get("parent_first_funder_fee_payer"), "parent": representative.get("stored_candidate_parent_direct_funder"), "creator": representative.get("creator_wallet"), "mint": representative.get("mint")} if representative else None, "mechanism": mechanism or baseline.get("mechanism") or generic_mechanism, "atomic_sequence": baseline.get("atomic_sequence") or [], "address_behaviour": "Creators, funders, and parents rotate" if p3r_rows else ("Treasury and sub-provider infrastructure recur across retained launches" if all_launches else None), "profile": "Baseline v%s" % profile.get("profile_version") if profile else ("Recorded operational baseline" if all_launches else None)},
        "recent_launches": [_launch_presentation(row) for row in all_launches[:3]],
        "all_launches": [_launch_presentation(row) for row in all_launches],
        "changes": [],
    }


def _launch_presentation(row: dict[str, Any]) -> dict[str, Any]:
    """Keep generic launch presentation compact while preserving optional roles."""
    return {
        "mint": row.get("mint"), "creator": row.get("creator_wallet"),
        "intermediary": row.get("stored_candidate_parent_direct_funder") or row.get("subprov_wallet"),
        "observed_at": _timestamp(row.get("parent_first_funder_timestamp")) or row.get("create_time"),
        "anchor": row.get("parent_first_funder_fee_payer") or row.get("treasury_wallet"),
        "signature": row.get("parent_first_funder_signature") or row.get("wrap_close_signature") or row.get("funding_tx_signature"),
        "selected_walkback_tx": row.get("selected_walkback_tx"),
        "upstream_funding": row.get("upstream_funding"),
        "creator_provisioning": row.get("creator_provisioning"),
        "session_chain": row.get("session_chain"),
        "mechanism": row.get("mechanism") or row.get("funding_mechanism"),
        "match": row.get("activity_observation_type", "BASELINE"),
    }
