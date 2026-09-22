"""Generic, replayable early-buyer identity and prior-only reuse evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from typing import Iterable, Mapping


ROLE_UNRESOLVED = "ROLE_UNRESOLVED"
APPARENT_EXTERNAL_BUYER = "APPARENT_EXTERNAL_BUYER"


def _canonical(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def classify_buyer_role(buyer: str, *, creator: str, operation_wallets=(), funders=(), controllers=(), intermediaries=(), prior_early_buyers=()) -> str:
    """Classify only established relationships; an unknown wallet is unresolved."""
    if buyer == creator:
        return "CREATOR"
    if buyer in set(operation_wallets):
        return "KNOWN_OPERATION_WALLET"
    if buyer in set(funders):
        return "KNOWN_FUNDER"
    if buyer in set(controllers):
        return "KNOWN_CONTROLLER"
    if buyer in set(intermediaries):
        return "KNOWN_INTERMEDIARY"
    if buyer in set(prior_early_buyers):
        return "PREVIOUSLY_SEEN_EARLY_BUYER"
    return ROLE_UNRESOLVED


def retain_early_buyer_sequence(*, mint: str, creator: str, bundle_boundary: Mapping[str, int], buys: Iterable[Mapping[str, object]], depth: int, provenance: Iterable[str]) -> dict:
    """Retain ordered external buys, rejecting ambiguous same-slot observations."""
    if depth < 1:
        raise ValueError("INVALID_EARLY_BUYER_DEPTH")
    boundary = (bundle_boundary["slot"], bundle_boundary["transaction_index"], bundle_boundary["instruction_index"])
    retained = []
    for buy in buys:
        required = ("signature", "slot", "transaction_index", "instruction_index", "block_time", "buyer_wallet")
        if any(buy.get(field) is None for field in required):
            raise ValueError("EARLY_BUYER_ORDER_OR_IDENTITY_UNRESOLVED")
        order = (buy["slot"], buy["transaction_index"], buy["instruction_index"])
        if order <= boundary or buy.get("launch_controlled") is True:
            continue
        row = dict(buy)
        row["mint"] = mint
        row["creator"] = creator
        row["ordering"] = {"slot": order[0], "transaction_index": order[1], "instruction_index": order[2]}
        row["elapsed_slots_from_bundle_boundary"] = order[0] - boundary[0]
        row["elapsed_transactions_from_bundle_boundary"] = None if order[0] != boundary[0] else order[1] - boundary[1]
        retained.append(row)
    retained.sort(key=lambda row: (row["slot"], row["transaction_index"], row["instruction_index"]))
    for position, row in enumerate(retained[:depth], 1):
        row["early_buy_position"] = position
    output = {"mint": mint, "creator": creator, "bundle_boundary": dict(bundle_boundary), "buyers": retained[:depth], "evidence_provenance": sorted(set(provenance))}
    output["sequence_id"] = _canonical(output)
    return output


def build_buyer_reuse_index(sequences: Iterable[Mapping[str, object]], *, repeated_threshold: int = 2, high_reuse_threshold: int = 5) -> dict:
    """Build retrospective and strictly-prior reuse without control inference."""
    rows = []
    for sequence in sequences:
        for buyer in sequence.get("buyers", []):
            row = dict(buyer)
            row.setdefault("mint", sequence["mint"]); row.setdefault("creator", sequence["creator"])
            rows.append(row)
    rows.sort(key=lambda row: (row["block_time"], row["slot"], row["transaction_index"], row["instruction_index"], row["mint"]))
    prior = defaultdict(list)
    output = []
    for row in rows:
        wallet = row["buyer_wallet"]
        before = prior[wallet]
        entry = {"buyer_wallet": wallet, "mint": row["mint"], "creator": row["creator"], "position": row["early_buy_position"], "block_time": row["block_time"], "prior_early_buyer_reuse": len(before), "prior_distinct_creators": len({item["creator"] for item in before})}
        output.append(entry)
        prior[wallet].append(entry)
    wallets = []
    for wallet, uses in sorted(prior.items()):
        launches = {use["mint"] for use in uses}; creators = {use["creator"] for use in uses}
        first = sum(use["position"] == 1 for use in uses)
        label = "SINGLE_LAUNCH_BUYER"
        if len(launches) >= high_reuse_threshold: label = "HIGH_REUSE_EARLY_BUYER"
        elif len(creators) > 1: label = "CROSS_CREATOR_EARLY_BUYER"
        elif len(launches) >= repeated_threshold: label = "REPEATED_EARLY_BUYER"
        wallets.append({"buyer_wallet": wallet, "distinct_launches": len(launches), "distinct_creators": len(creators), "first_buyer_count": first, "early_buyer_count": len(uses), "first_observed_at": min(use["block_time"] for use in uses), "last_observed_at": max(use["block_time"] for use in uses), "reuse_classification": label, "early_buyer_reuse_evidence": "OBSERVED", "control_proven": False})
    result = {"schema_version": "early_buyer_reuse.v1", "observations": output, "wallet_index": wallets, "invariants": {"MIGRATION_REQUIRED_FOR_EARLY_BUYER_ANALYSIS": "NO", "FIRST_BUY_PRICE_WITHOUT_BUYER_IDENTITY": "INCOMPLETE_WHERE_TRANSACTION_EVIDENCE_SUPPORTS_IDENTITY", "CONTROL_NOT_INFERRED_FROM_REUSE_ALONE": "PASS"}}
    result["index_digest"] = _canonical(result)
    return result


def reuse_matrix(index: Mapping[str, object]) -> dict:
    observations = index["observations"]; wallets = index["wallet_index"]
    return {"births_with_qualified_first_buyer": len({row["mint"] for row in observations if row["position"] == 1}), "unique_first_buyer_wallets": len({row["buyer_wallet"] for row in observations if row["position"] == 1}), "first_buyers_once": sum(row["first_buyer_count"] == 1 for row in wallets), "first_buyers_reused": sum(row["first_buyer_count"] > 1 for row in wallets), "first_buyers_cross_creator": sum(row["first_buyer_count"] > 0 and row["distinct_creators"] > 1 for row in wallets), "maximum_launches_by_one_first_buyer": max((row["first_buyer_count"] for row in wallets), default=0), "prior_seen_first_buyer_pct": 0 if not observations else 100 * sum(row["position"] == 1 and row["prior_early_buyer_reuse"] > 0 for row in observations) / sum(row["position"] == 1 for row in observations)}
