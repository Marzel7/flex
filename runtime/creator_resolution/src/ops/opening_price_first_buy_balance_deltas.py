"""Fail-closed Pump.fun first-buy attribution from compact balance deltas.

This is intentionally narrower than the transaction classifier: it consumes a
previously bound Pump.fun Buy witness and compact diagnostic only.  It never
derives an amount from the payer's total lamport loss.
"""
from __future__ import annotations

import hashlib
import json

from .opening_price_first_buy_amounts import PUMP_FUN

VERSION = "OPENING_PRICE_FIRST_BUY_BALANCE_DELTAS_V1"


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_canon(value).encode()).hexdigest()


def _has_inner(items, *, parent, program, accounts):
    expected = set(accounts)
    return any(
        item.get("parent_outer_index") == parent
        and item.get("resolved_program_id") == program
        and expected.issubset(set(item.get("relevant_resolved_accounts") or []))
        for item in items
    )


def qualify_balance_deltas(anchor, binding_witness, diagnostic):
    """Return exact event-consistent amounts, or explicit unresolved states.

    The frozen Pump.fun Buy account layout provides the role bindings used here:
    curve authority at 3, curve token account at 4, buyer token account at 5,
    and buyer at 6.  A matching inner Token/System instruction is required in
    addition to those positions, so a merely negative counterparty delta fails.
    """
    result = {
        "anchor_id": anchor.get("anchor_id"), "signature": anchor.get("signature"),
        "slot": anchor.get("slot"), "buyer": anchor.get("buyer"), "mint": anchor.get("mint"),
        "classifier_version": VERSION,
        "source_digest": (diagnostic or {}).get("source_response_digest"),
        "venue": "PUMPFUN_BONDING_CURVE",
        "executed_token_amount": None, "executed_quote_amount": None,
        "fact_states": {
            "EXECUTED_TOKEN_AMOUNT": "UNRESOLVED",
            "EXECUTED_QUOTE_AMOUNT": "UNRESOLVED",
            "SWAP_SEMANTICS": "UNRESOLVED",
            "PROTOCOL_FEE_SEMANTICS": "UNRESOLVED_NON_BLOCKING_FOR_EXECUTION_PRICE",
        },
        "checks": {}, "error": None,
    }
    selected = (binding_witness or {}).get("selected_event") or {}
    accounts = selected.get("resolved_accounts") or []
    if (binding_witness or {}).get("binding_result") != "BOUND" or selected.get("program_id") != PUMP_FUN or len(accounts) < 7:
        result["error"] = "PUMPFUN_BOUND_EVENT_REQUIRED"
        result["result_digest"] = _digest(result)
        return result
    if accounts[2] != anchor.get("mint") or accounts[6] != anchor.get("buyer"):
        result["error"] = "ANCHOR_EVENT_MISMATCH"
        result["result_digest"] = _digest(result)
        return result

    curve, curve_token, buyer_token, buyer = accounts[3], accounts[4], accounts[5], accounts[6]
    inner = ((diagnostic or {}).get("binding_structure") or {}).get("inner_instruction_summaries") or []
    outer = selected.get("outer_index")
    token_role = _has_inner(inner, parent=outer, program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                            accounts=(curve_token, anchor.get("mint"), buyer_token, curve))
    system_role = _has_inner(inner, parent=outer, program="11111111111111111111111111111111", accounts=(buyer, curve))
    tokens = (diagnostic or {}).get("token_candidates") or []
    positives = [x for x in tokens if x.get("mint") == anchor.get("mint") and x.get("account_address") == buyer_token and x.get("owner") == buyer and int(x.get("raw_amount", 0)) > 0]
    negatives = [x for x in tokens if x.get("mint") == anchor.get("mint") and x.get("account_address") == curve_token and x.get("owner") == curve and int(x.get("raw_amount", 0)) < 0]
    result["checks"].update({"buyer_ownership_proven": len(positives) == 1, "curve_token_role_proven": token_role, "curve_quote_role_proven": system_role})
    if len(positives) == 1 and len(negatives) == 1 and token_role:
        plus, minus = positives[0], negatives[0]
        if plus.get("decimals") == minus.get("decimals") and int(plus["raw_amount"]) == -int(minus["raw_amount"]):
            # All target-mint deltas must be these two exact event-role accounts.
            scoped = [x for x in tokens if x.get("mint") == anchor.get("mint")]
            if len(scoped) == 2:
                result["executed_token_amount"] = {"raw_base_units": int(plus["raw_amount"]), "decimals": plus["decimals"], "source_account": curve_token, "destination_account": buyer_token, "provenance": "MATCHED_EVENT_CONSISTENT_BALANCE_DELTAS"}
                result["fact_states"]["EXECUTED_TOKEN_AMOUNT"] = "QUALIFIED_FROM_BALANCE_DELTAS"
    result["checks"]["exact_token_conservation"] = result["executed_token_amount"] is not None

    quotes = (diagnostic or {}).get("quote_candidates") or []
    curve_quotes = [x for x in quotes if x.get("source_account") == curve and int(x.get("raw_amount", 0)) > 0]
    if len(curve_quotes) == 1 and system_role:
        quote = curve_quotes[0]
        result["executed_quote_amount"] = {"raw_lamports": int(quote["raw_amount"]), "source_account": buyer, "destination_account": curve, "provenance": "MATCHED_EVENT_CONSISTENT_BALANCE_DELTAS"}
        result["fact_states"]["EXECUTED_QUOTE_AMOUNT"] = "QUALIFIED_FROM_BALANCE_DELTAS"
    if result["executed_token_amount"] and result["executed_quote_amount"]:
        result["fact_states"]["SWAP_SEMANTICS"] = "QUALIFIED_FROM_BOUND_PUMPFUN_BUY_AND_BALANCE_DELTAS"
    result["checks"].update({"unrelated_positive_deltas_excluded": bool(system_role), "network_fee_separate": (diagnostic or {}).get("meta_fee") is not None, "payer_delta_reconciliation_only": True})
    result["result_digest"] = _digest({k: v for k, v in result.items() if k != "result_digest"})
    return result
