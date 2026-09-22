"""Offline, event-scoped amount classification for a Pump.fun first-buy envelope."""
from __future__ import annotations

import hashlib
import json
from .opening_price_instruction_normalization import InstructionNormalizationError, normalize_outer_instructions

PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
VERSION = "OPENING_PRICE_FIRST_BUY_AMOUNTS_V1"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _amount(raw, decimals):
    scale = 10 ** decimals
    return f"{raw // scale}.{raw % scale:0{decimals}d}" if decimals else str(raw)


def _observation(asset, raw_amount, account_from, account_to, outer, inner, semantic, binding):
    item = {"asset": asset, "raw_amount": raw_amount, "account_from": account_from, "account_to": account_to, "instruction_provenance": {"outer_instruction_index": outer, "inner_instruction_index": inner}, "semantic_class": semantic, "event_binding": binding}
    item["observation_id"] = _digest(item)[:20]
    return item


def _result(anchor, transaction, error=None):
    result = {"anchor_id": anchor.get("anchor_id") or _digest(anchor), "signature": anchor.get("signature"), "slot": anchor.get("slot"), "mint": anchor.get("mint"), "buyer": anchor.get("buyer"), "event_scope": None, "executed_token_amount": None, "executed_quote_amount": None, "excluded_network_fee": None, "excluded_rent": [], "excluded_unrelated_movements": [], "observations": [], "fact_states": {"EXECUTED_TOKEN_AMOUNT": "MISSING", "EXECUTED_QUOTE_AMOUNT": "MISSING", "SWAP_SEMANTICS": "UNRESOLVED", "PROTOCOL_FEE_SEMANTICS": "UNRESOLVED"}, "source_digest": _digest(transaction), "classifier_version": VERSION, "error": error}
    result["result_digest"] = _digest(result)
    return result


def classify_first_buy_transaction(anchor, transaction):
    """Classify only exact event-bound movements; never infer quote from payer delta."""
    try:
        message, meta = transaction["transaction"]["message"], transaction["meta"]
        keys = message["accountKeys"]
        if len(meta["preBalances"]) != len(keys) or len(meta["postBalances"]) != len(keys):
            return _result(anchor, transaction, "BALANCE_ARRAY_LENGTH_MISMATCH")
        for balance in meta.get("preTokenBalances", []) + meta.get("postTokenBalances", []):
            if not 0 <= balance["accountIndex"] < len(keys):
                return _result(anchor, transaction, "TOKEN_BALANCE_ACCOUNT_INDEX_OUT_OF_RANGE")
        signature = transaction["transaction"]["signatures"][0]
        if signature != anchor.get("signature") or transaction.get("slot") != anchor.get("slot"):
            return _result(anchor, transaction, "ANCHOR_EVENT_MISMATCH")
        buyer = anchor.get("buyer")
        normalized=normalize_outer_instructions(transaction);by_index={x["outer_index"]:x for x in normalized}
        pump_indices = [x for x in normalized if x["program_id"] == PUMP_FUN]
        selected = next((x for x in pump_indices if anchor.get("mint") in x["resolved_accounts"] and buyer in x["resolved_accounts"]), None)
        pump_index = selected["outer_index"] if selected else None
        if pump_index is None or "Instruction: Buy" not in "\n".join(meta.get("logMessages", [])):
            return _result(anchor, transaction, "ANCHOR_EVENT_MISMATCH")
        result = _result(anchor, transaction)
        outer = selected
        result["event_scope"] = {"outer_instruction_index": pump_index, "program_id": PUMP_FUN, "accounts": outer["resolved_accounts"], "inner_instruction_indices": []}
        curve = outer["resolved_accounts"][3]
        target_mint = anchor["mint"]
        event_groups = {group["index"]: group["instructions"] for group in meta.get("innerInstructions", [])}
        token = None
        for group_index, instructions in sorted(event_groups.items()):
            is_event = group_index == pump_index
            if is_event: result["event_scope"]["inner_instruction_indices"] = list(range(len(instructions)))
            for inner_index, instruction in enumerate(instructions):
                parsed = instruction.get("parsed") or {}; info = parsed.get("info") or {}; kind = parsed.get("type")
                if kind == "transferChecked" and info.get("mint") == target_mint:
                    amount = int(info["tokenAmount"]["amount"]); decimals = int(info["tokenAmount"]["decimals"])
                    if is_event and info.get("destination") in outer["resolved_accounts"] and not token:
                        obs = _observation(target_mint, amount, info.get("source"), info.get("destination"), group_index, inner_index, "EXECUTED_TOKEN_AMOUNT_QUALIFIED", "TARGET_BUY_EVENT")
                        result["observations"].append(obs); token = {"raw_base_units": amount, "decimals": decimals, "normalized_amount": _amount(amount, decimals), "source_account": info.get("source"), "destination_account": info.get("destination"), "provenance": obs["observation_id"]}
                    else:
                        obs = _observation(target_mint, amount, info.get("source"), info.get("destination"), group_index, inner_index, "UNRELATED_TRANSFER", "OUTSIDE_TARGET_BUY_EVENT")
                        result["observations"].append(obs); result["excluded_unrelated_movements"].append(obs)
                elif kind == "transfer":
                    amount = int(info["lamports"])
                    if is_event and info.get("destination") == curve:
                        obs = _observation("SOL", amount, info.get("source"), info.get("destination"), group_index, inner_index, "EXECUTED_QUOTE_AMOUNT_QUALIFIED", "TARGET_BUY_EVENT")
                        result["observations"].append(obs); result["executed_quote_amount"] = {"raw_lamports": amount, "source_account": info.get("source"), "destination_account": info.get("destination"), "provenance": obs["observation_id"]}
                    elif group_index == 2 and by_index.get(2,{}).get("program_id") == "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL":
                        obs = _observation("SOL", amount, info.get("source"), info.get("destination"), group_index, inner_index, "RENT_OR_ACCOUNT_CREATION", "ATA_CONTEXT")
                        result["observations"].append(obs); result["excluded_rent"].append(obs)
                    elif is_event:
                        obs = _observation("SOL", amount, info.get("source"), info.get("destination"), group_index, inner_index, "EVENT_BOUND_CORROBORATING_AMOUNT", "TARGET_BUY_EVENT")
                        result["observations"].append(obs)
                    else:
                        obs = _observation("SOL", amount, info.get("source"), info.get("destination"), group_index, inner_index, "UNRELATED_TRANSFER", "OUTSIDE_TARGET_BUY_EVENT")
                        result["observations"].append(obs); result["excluded_unrelated_movements"].append(obs)
        fee = int(meta["fee"])
        fee_obs = _observation("SOL", fee, buyer, None, None, None, "NETWORK_FEE", "TRANSACTION_META")
        result["observations"].append(fee_obs); result["excluded_network_fee"] = {"raw_lamports": fee, "provenance": fee_obs["observation_id"]}
        if token:
            result["executed_token_amount"] = token; result["fact_states"]["EXECUTED_TOKEN_AMOUNT"] = "QUALIFIED"
        else: result["fact_states"]["EXECUTED_TOKEN_AMOUNT"] = "UNRESOLVED"
        if result["executed_quote_amount"]:
            result["fact_states"]["EXECUTED_QUOTE_AMOUNT"] = "QUALIFIED"
        else: result["fact_states"]["EXECUTED_QUOTE_AMOUNT"] = "UNRESOLVED"
        result["fact_states"]["SWAP_SEMANTICS"] = "QUALIFIED" if token else "UNRESOLVED"
        result["observations"].sort(key=lambda x: (x["instruction_provenance"]["outer_instruction_index"] is None, x["instruction_provenance"]["outer_instruction_index"] or -1, x["instruction_provenance"]["inner_instruction_index"] or -1, x["observation_id"]))
        result["result_digest"] = _digest({k: v for k, v in result.items() if k != "result_digest"})
        return result
    except (KeyError, IndexError, TypeError, ValueError, InstructionNormalizationError):
        return _result(anchor, transaction, "MALFORMED_TRANSACTION")
