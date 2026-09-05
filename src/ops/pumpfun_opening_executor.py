"""Durable, bounded acquisition for a Pump.fun opening replay.

The executor has no database, listener, or membership side effects. Providers
and immutable storage are injected, keeping network work outside SQLite work.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping

from src.ops.birth_anchored_opening_acquisition import (
    PUMP_DECIMALS, PUMP_TOTAL_SUPPLY_RAW, actions_from_block,
    actions_from_transaction, creation_context,
)
from src.ops.pumpfun_opening_impulse import market_cap_sol, reconstruct_opening_impulse

MAX_BLOCKS = 8
TIMING = "SLOT_BOUNDED_FIRST_1S_APPROXIMATION"


def _raw(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _put(store: Any, payload: Any, metadata: dict[str, Any]) -> str:
    reference = store.put(_raw(payload), metadata=metadata)
    store.verify(reference.digest)
    return reference.digest


def _order_key(action: Mapping[str, Any]) -> tuple[int, int, int]:
    """A direct getTransaction has no fabricated block transaction index."""
    transaction_index = action.get("transaction_index")
    return (int(action["slot"]), -1 if transaction_index is None else int(transaction_index),
            int(action["action_index"]))


def execute_birth_anchored_opening_analysis(
    *, operation_id: str, mint: str, rich_birth: Mapping[str, Any], helius: Any,
    alchemy: Any | None, artifact_store: Any,
) -> dict[str, Any]:
    """Run the frozen bounded acquisition contract using injected clients.

    ``creation_context`` may declare ``requires_archived_prestate`` only where
    retained creation evidence proves continuity cannot otherwise be established.
    That permits one bounded Alchemy account lookup.
    """
    calls = {"helius_get_transaction": 0, "helius_get_block": 0,
             "alchemy_get_account_info": 0}
    artifacts: list[str] = []

    def insufficient(reason: str) -> dict[str, Any]:
        return {"status": "INSUFFICIENT_EVIDENCE_BOUND_EXHAUSTED", "reason": reason,
                "calls": calls, "artifacts": artifacts}

    def retain(provider: str, method: str, request: dict[str, Any], response: Any) -> str:
        raw_digest = _put(
            artifact_store, response,
            {"kind": "provider_raw_response", "provider": provider, "method": method,
             "operation_id": operation_id, "mint": mint, "request": request},
        )
        observation = {
            "schema_version": 1, "provider": provider, "network": "solana-mainnet",
            "request_method": method, "safe_request_parameters": request,
            "operation_id": operation_id, "mint": mint,
            "birth_signature": rich_birth.get("signature"), "received_at": int(time.time()),
            "response_artifact_digest": raw_digest,
        }
        observation_digest = _put(
            artifact_store, observation,
            {"kind": "provider_observation", "provider": provider,
             "response_artifact_digest": raw_digest},
        )
        artifacts.extend((raw_digest, observation_digest))
        return raw_digest

    signature = str(rich_birth.get("signature") or "")
    if not signature:
        return {"status": "INSUFFICIENT_EVIDENCE", "reason": "MISSING_BIRTH_SIGNATURE",
                "calls": calls, "artifacts": artifacts}

    calls["helius_get_transaction"] += 1
    transaction = helius.get_transaction(signature)
    retain("helius", "getTransaction", {"signature": signature}, transaction)
    context = creation_context(transaction.get("result", transaction), mint=mint)
    if not context:
        return insufficient("INVALID_CREATION")

    archived_prestate = None
    if context.get("requires_archived_prestate") or rich_birth.get("requires_archived_prestate"):
        if alchemy is None:
            return insufficient("MISSING_ARCHIVE_PROVIDER")
        calls["alchemy_get_account_info"] += 1
        archived_prestate = alchemy.get_account_info(context["bonding_curve"], context["creation_slot"])
        retain("alchemy", "getAccountInfo", {
            "account": context["bonding_curve"], "slot": context["creation_slot"],
        }, archived_prestate)
        if not isinstance(archived_prestate, Mapping) or not archived_prestate.get("result"):
            return insufficient("ARCHIVE_PRESTATE_UNAVAILABLE")

    creation_slot = int(context["creation_slot"])
    actions = actions_from_transaction(transaction, mint=mint, slot=creation_slot)
    start_slot = creation_slot if actions else creation_slot + 1
    independent: Mapping[str, Any] | None = None
    for slot in range(start_slot, start_slot + MAX_BLOCKS):
        if slot != creation_slot or not actions:
            calls["helius_get_block"] += 1
            block = helius.get_block(slot)
            # Retention completes before decoding this block or requesting N+1.
            retain("helius", "getBlock", {"slot": slot}, block)
            actions.extend(actions_from_block(block, mint=mint, slot=slot))
        actions.sort(key=_order_key)
        independent = next((action for action in actions if action["action_type"] == "BUY"
                            and action["buyer"] != context["creator"]), None)
        # Fixed first-second slot horizon; later requests cannot alter its metrics.
        if independent is not None and slot >= int(independent["slot"]) + 3:
            break

    if not actions:
        return insufficient("NO_OPENING_ACTIONS")
    opening = actions_from_transaction(transaction, mint=mint, slot=creation_slot)
    if not opening:
        opening = [action for action in actions if int(action["slot"]) == start_slot]
    if not opening:
        return insufficient("NO_OPENING_SLOT_ACTIONS")
    replay = reconstruct_opening_impulse(
        mint=mint, creation_signature=signature, creation_slot=creation_slot,
        creation_time=None, creator=context["creator"], bonding_curve=context["bonding_curve"],
        associated_bonding_curve=None, supply_raw=PUMP_TOTAL_SUPPLY_RAW,
        decimals=PUMP_DECIMALS, opening_slot=int(opening[0]["slot"]), actions=opening,
        archived_prebuy_state=archived_prestate,
    )
    if independent is None:
        return insufficient("NO_INDEPENDENT_BUY")
    horizon = int(independent["slot"]) + 3
    window = [action for action in actions if int(independent["slot"]) <= int(action["slot"]) <= horizon]
    market_caps = [market_cap_sol(
        virtual_sol_reserves=action["post_virtual_sol_reserves"],
        virtual_token_reserves=action["post_virtual_token_reserves"],
        supply_raw=PUMP_TOTAL_SUPPLY_RAW, decimals=PUMP_DECIMALS,
    ) for action in window]
    result = {
        "status": "QUALIFIED", "operation_id": operation_id, "mint": mint,
        "first_independent_buy_mc": str(replay.first_independent_buy_mc_sol),
        "opening_slot_peak_mc": str(replay.opening_slot_peak_mc_sol),
        "opening_slot_end_mc": str(replay.opening_slot_end_mc_sol),
        "first_1s_start_slot": int(independent["slot"]), "first_1s_end_slot": int(window[-1]["slot"]),
        "first_1s_timing_qualification": TIMING, "first_1s_trade_count": len(market_caps),
        "first_1s_peak_mc": str(max(market_caps)), "first_1s_low_mc": str(min(market_caps)),
        "first_1s_end_mc": str(market_caps[-1]),
        "first_1s_multiple": str(max(market_caps) / replay.first_independent_buy_mc_sol),
        "calls": calls, "artifacts": artifacts,
        "early_stop_used": calls["helius_get_block"] < MAX_BLOCKS,
    }
    result["logical_id"] = hashlib.sha256(_raw(
        {key: value for key, value in result.items() if key not in {"artifacts", "calls"}}
    )).hexdigest()
    # A qualified result is never returned before its immutable result artifact.
    result["result_artifact"] = _put(artifact_store, result, {"kind": "opening_result"})
    return result
