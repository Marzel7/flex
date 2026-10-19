"""Minimal, injected-only historical PumpSwap first-buy binding contract.

This adapter deliberately does not decode a provider transaction or invent a
pool state.  It validates an already extracted compact record before passing
the exact inputs to the version-bound PumpSwap execution model.
"""
from __future__ import annotations

import hashlib
import json

from .pumpswap_authoritative_execution_model import PROGRAM_ID, PoolState, buy_exact_base_out

VERSION = "OPENING_PRICE_FIRST_BUY_PAMM_HISTORICAL_V1"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def extract_pamm_balance_deltas(anchor, diagnostic, pool_binding):
    """Compact event-bound vault deltas; intentionally leaves fee/state unknown."""
    result = {"venue": "PUMPSWAP_PAMM", "program_id": PROGRAM_ID, "pool_id": None,
              "executed_token_amount": None, "quote_to_pool_amount": None,
              "fee_config_identity": None, "fee_model": "UNRESOLVED", "pre_pool_state": None, "error": None}
    if pool_binding.get("status") != "POOL_IDENTITY_QUALIFIED_DIRECT":
        result["error"] = "POOL_IDENTITY_UNRESOLVED"
        return result
    outer = next((x for x in ((diagnostic.get("binding_structure") or {}).get("outer_programs") or [])
                  if x.get("outer_index") == anchor.get("event_index") and x.get("resolved_program_id") == PROGRAM_ID), None)
    if not outer or not {anchor.get("mint"), anchor.get("buyer"), pool_binding["pool"]}.issubset(set(outer.get("relevant_resolved_accounts") or [])):
        result["error"] = "PAMM_EVENT_BINDING_UNRESOLVED"
        return result
    # The retained SDK-layout qualification identifies account 19 as FeeConfig
    # for this 26-account Buy variant.  Identity alone is intentionally not a
    # fee schedule or a historical configuration decode.
    accounts = outer.get("relevant_resolved_accounts") or []
    if len(accounts) == 26:
        result["fee_config_identity"] = {"account": accounts[19], "status": "OBSERVED_LAYOUT_BOUND_PARAMETERS_UNRESOLVED"}
    token = diagnostic.get("token_candidates") or []
    base = pool_binding["base_vault"]
    buyer = anchor["buyer"]
    outgoing = [x for x in token if x.get("account_address") == base and x.get("mint") == anchor["mint"] and x.get("owner") == pool_binding["pool"] and int(x.get("raw_amount", 0)) < 0]
    received = [x for x in token if x.get("mint") == anchor["mint"] and x.get("owner") == buyer and int(x.get("raw_amount", 0)) > 0]
    if len(outgoing) == len(received) == 1 and -int(outgoing[0]["raw_amount"]) == int(received[0]["raw_amount"]) and outgoing[0].get("decimals") == received[0].get("decimals"):
        result["executed_token_amount"] = {"raw_base_units": int(received[0]["raw_amount"]), "decimals": received[0]["decimals"], "status": "QUALIFIED_FROM_POOL_VAULT_BALANCE_DELTAS"}
    quote = pool_binding["quote_vault"]
    credits = [x for x in token if x.get("account_address") == quote and x.get("mint") == "So11111111111111111111111111111111111111112" and x.get("owner") == pool_binding["pool"] and int(x.get("raw_amount", 0)) > 0]
    if len(credits) == 1:
        result["quote_to_pool_amount"] = {"raw_lamports": int(credits[0]["raw_amount"]), "status": "QUALIFIED_POOL_QUOTE_VAULT_CREDIT_NOT_TOTAL_TRADER_QUOTE"}
    result["pool_id"] = pool_binding["pool"]
    result["result_digest"] = _digest(result)
    return result


def bind_historical_pamm(anchor, evidence):
    """Fail closed unless one frozen successor has all exact pAMM inputs.

    Transaction amounts alone are adequate for average execution price.  The
    pre-state and versioned fee configuration are separately mandatory only
    when claiming consistency with the authoritative CPMM buy model.
    """
    result = {"anchor_id": anchor.get("anchor_id"), "venue": "PUMPSWAP_PAMM", "program_id": PROGRAM_ID,
              "adapter_version": VERSION, "status": "PAMM_PRICE_INPUT_PROVIDER_EVIDENCE_REQUIRED",
              "amounts": None, "model_consistency": "NOT_ATTEMPTED", "missing_facts": [], "error": None}
    identity = ("anchor_id", "mint", "buyer", "signature", "slot", "transaction_index", "event_index")
    if any(evidence.get(k) != anchor.get(k) for k in identity):
        result.update(status="PAMM_PRICE_INPUT_SEMANTIC_GAP", error="ANCHOR_EVENT_MISMATCH")
    elif evidence.get("program_id") != PROGRAM_ID or evidence.get("venue") != "PUMPSWAP_PAMM":
        result.update(status="PAMM_PRICE_INPUT_SEMANTIC_GAP", error="UNSUPPORTED_PROGRAM_ROUTE")
    elif evidence.get("outer_instruction_index") != anchor.get("event_index") or evidence.get("direction") != "BUY_QUOTE_TO_BASE":
        result.update(status="PAMM_PRICE_INPUT_SEMANTIC_GAP", error="PAMM_EVENT_BINDING_UNRESOLVED")
    else:
        required = ("pool_id", "executed_token_amount", "executed_quote_amount", "fee_model", "pool_version", "source_digests")
        missing = [k for k in required if not evidence.get(k)]
        token, quote = evidence.get("executed_token_amount") or {}, evidence.get("executed_quote_amount") or {}
        if not isinstance(token.get("raw_base_units"), int) or token.get("raw_base_units", 0) <= 0: missing.append("executed_token_amount.raw_base_units")
        if not isinstance(quote.get("raw_lamports"), int) or quote.get("raw_lamports", 0) <= 0: missing.append("executed_quote_amount.raw_lamports")
        if missing:
            result["missing_facts"] = sorted(set(missing))
        else:
            result["amounts"] = {"executed_token_amount": token, "executed_quote_amount": quote}
            result["status"] = "PAMM_PRICE_INPUT_OFFLINE_READY"
            state_data = evidence.get("pre_pool_state")
            if state_data:
                try:
                    model_quote = buy_exact_base_out(PoolState(**state_data), token["raw_base_units"], quote["raw_lamports"])
                    if model_quote.trader_quote_amount != quote["raw_lamports"]:
                        raise ValueError("MODEL_QUOTE_MISMATCH")
                    result["model_consistency"] = "QUALIFIED_PRE_STATE_AND_FEE_MODEL"
                except (TypeError, ValueError) as exc:
                    result.update(status="PAMM_PRICE_INPUT_SEMANTIC_GAP", error=str(exc), amounts=None)
            else:
                result["model_consistency"] = "NOT_REQUIRED_FOR_OBSERVED_AVERAGE_EXECUTION_PRICE"
    result["result_digest"] = _digest({k: v for k, v in result.items() if k != "result_digest"})
    return result
