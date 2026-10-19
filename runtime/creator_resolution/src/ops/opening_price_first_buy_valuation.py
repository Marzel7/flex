"""Deterministic compact valuation reducer for a qualified first-buy amount result."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json

VERSION = "OPENING_PRICE_FIRST_BUY_VALUATION_V1_DECIMAL50_HALF_EVEN"


def _canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
def _digest(value): return hashlib.sha256(_canonical(value).encode()).hexdigest()
def _decimal(value):
    text = format(value, "f").rstrip("0").rstrip(".")
    return text or "0"


def reduce_first_buy_valuation(anchor, classified):
    """Reduce only classifier-qualified observed amounts; raw transaction is never accepted."""
    record = {"anchor_id": anchor.get("anchor_id"), "mint": anchor.get("mint"), "buyer": anchor.get("buyer"), "signature": anchor.get("signature"), "slot": anchor.get("slot"), "transaction_index": anchor.get("transaction_index"), "event_index": anchor.get("event_index"), "executed_token_amount_raw": None, "token_decimals": None, "executed_token_amount_normalized": None, "executed_quote_lamports": None, "quote_decimals": 9, "executed_quote_sol": None, "execution_price_type": "AVERAGE_EXECUTION_PRICE", "swap_semantics": classified.get("fact_states", {}).get("SWAP_SEMANTICS", "UNRESOLVED"), "protocol_fee_semantics": classified.get("fact_states", {}).get("PROTOCOL_FEE_SEMANTICS", "UNRESOLVED"), "source_transaction_digest": classified.get("source_digest"), "classifier_result_digest": classified.get("result_digest"), "valuation_method_version": VERSION, "price_readiness": "PRICE_INPUT_INSUFFICIENT", "market_cap_readiness": "MARKET_CAP_INSUFFICIENT_EVIDENCE", "primary_reason": None, "secondary_reasons": [], "protocol_fee_effect": "BLOCKING", "buy_math_interface": "COMPATIBLE_RAW_UNITS_PRESTATE_REQUIRED_DIFFERENT_PRICE_TYPE"}
    identity = (record["signature"], record["slot"], record["mint"], record["buyer"])
    if identity != (classified.get("signature"), classified.get("slot"), classified.get("mint"), classified.get("buyer")) or classified.get("error"):
        record["primary_reason"] = classified.get("error") or "ANCHOR_EVENT_MISMATCH"
    elif classified.get("fact_states", {}).get("EXECUTED_TOKEN_AMOUNT") not in ("QUALIFIED", "QUALIFIED_FROM_BALANCE_DELTAS"):
        record["primary_reason"] = "EXECUTED_TOKEN_AMOUNT_UNRESOLVED"
    elif classified.get("fact_states", {}).get("EXECUTED_QUOTE_AMOUNT") not in ("QUALIFIED", "QUALIFIED_FROM_BALANCE_DELTAS"):
        record["primary_reason"] = "EXECUTED_QUOTE_AMOUNT_UNRESOLVED"
    else:
        token, quote = classified.get("executed_token_amount") or {}, classified.get("executed_quote_amount") or {}
        raw, decimals, lamports = token.get("raw_base_units"), token.get("decimals"), quote.get("raw_lamports")
        if not isinstance(decimals, int): record["primary_reason"] = "TOKEN_DECIMALS_MISSING"
        elif not isinstance(raw, int) or raw <= 0: record["primary_reason"] = "EXECUTED_TOKEN_AMOUNT_INVALID"
        elif not isinstance(lamports, int) or lamports < 0: record["primary_reason"] = "EXECUTED_QUOTE_AMOUNT_INVALID"
        else:
            with localcontext() as context:
                context.prec = 50; context.rounding = ROUND_HALF_EVEN
                normalized_token = Decimal(raw) / (Decimal(10) ** decimals)
                normalized_quote = Decimal(lamports) / Decimal(1_000_000_000)
                price = normalized_quote / normalized_token
            record.update({"executed_token_amount_raw": raw, "token_decimals": decimals, "executed_token_amount_normalized": _decimal(normalized_token), "executed_quote_lamports": lamports, "executed_quote_sol": _decimal(normalized_quote), "average_execution_price_sol_per_token": _decimal(price), "price_readiness": "PRICE_QUALIFIED", "protocol_fee_effect": "NON_BLOCKING_FOR_EXECUTION_PRICE", "primary_reason": "EVENT_BOUND_OBSERVED_QUOTE_AND_TOKEN"})
            if record["protocol_fee_semantics"] == "UNRESOLVED": record["secondary_reasons"].append("PROTOCOL_FEE_DECOMPOSITION_UNRESOLVED_NON_BLOCKING_FOR_FROZEN_EVENT_BOUND_EXECUTION_PRICE")
    record["record_digest"] = _digest(record)
    return record
