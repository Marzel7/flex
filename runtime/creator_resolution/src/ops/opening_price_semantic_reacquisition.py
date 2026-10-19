"""Compact, event-level reduction for a bounded Pump.fun opening probe.

This module consumes a transient ``getBlock`` response and returns only the
target-mint facts needed to qualify ordered opening buys.  It deliberately
does not persist a provider response or try to discover slots.
"""
from __future__ import annotations

import base64
from hashlib import sha256
import json
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Mapping

from .birth_anchored_opening_acquisition import (
    PUMP_DECIMALS, PUMP_TOTAL_SUPPLY_RAW, decode_trade_event,
)
from .opening_price_instruction_normalization import (
    InstructionNormalizationError, canonical_account_keys, normalize_outer_instructions,
)
from .pumpfun_opening_impulse import market_cap_sol

VERSION = "OPENING_PRICE_2BHJT_COMPACT_SEMANTIC_REACQUISITION_V1"
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _decimal(value: Decimal) -> str:
    text = format(value, "f").rstrip("0").rstrip(".")
    return text or "0"


def _inner_programs(tx: Mapping[str, object]) -> list[str]:
    keys = canonical_account_keys(tx)
    values: set[str] = set()
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        if not isinstance(group, Mapping):
            continue
        for item in group.get("instructions") or []:
            if not isinstance(item, Mapping):
                continue
            program = item.get("programId")
            if not isinstance(program, str):
                index = item.get("programIdIndex")
                if isinstance(index, int) and 0 <= index < len(keys):
                    program = keys[index]
            if isinstance(program, str):
                values.add(program)
    return sorted(values)


def _events(tx: Mapping[str, object], mint: str) -> list[dict]:
    result: list[dict] = []
    for log_index, line in enumerate((tx.get("meta") or {}).get("logMessages") or []):
        if not isinstance(line, str) or not line.startswith("Program data: "):
            continue
        try:
            decoded = decode_trade_event(base64.b64decode(line.split(": ", 1)[1]))
        except Exception:
            decoded = None
        if decoded and decoded["mint"] == mint:
            result.append({"event_log_index": log_index, **decoded})
    return result


def _token_deltas(tx: Mapping[str, object], mint: str) -> list[dict]:
    meta = tx.get("meta") or {}
    before = {item.get("accountIndex"): item for item in meta.get("preTokenBalances") or []
              if isinstance(item, Mapping) and item.get("mint") == mint}
    after = {item.get("accountIndex"): item for item in meta.get("postTokenBalances") or []
             if isinstance(item, Mapping) and item.get("mint") == mint}
    rows = []
    for index in sorted(set(before) | set(after), key=lambda x: -1 if x is None else x):
        left, right = before.get(index, {}), after.get(index, {})
        def amount(item):
            value = (item.get("uiTokenAmount") or {}).get("amount")
            return int(value) if isinstance(value, str) and value.isdigit() else 0
        rows.append({"account_index": index, "owner": right.get("owner", left.get("owner")),
                     "raw_delta": amount(right) - amount(left),
                     "decimals": (right.get("uiTokenAmount") or left.get("uiTokenAmount") or {}).get("decimals")})
    return rows


def _sol_deltas(tx: Mapping[str, object], accounts: list[str], relevant: set[str]) -> list[dict]:
    meta = tx.get("meta") or {}
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    rows = []
    for index, address in enumerate(accounts):
        if address in relevant and index < len(pre) and index < len(post):
            rows.append({"account": address, "lamports_delta": int(post[index]) - int(pre[index])})
    return rows


def _record(tx: Mapping[str, object], *, slot: int, transaction_index: int, mint: str, creator: str) -> dict | None:
    meta = tx.get("meta") or {}
    try:
        accounts = canonical_account_keys(tx)
        outer = normalize_outer_instructions(tx)
        outer_programs = sorted({row["program_id"] for row in outer})
        inner_programs = _inner_programs(tx)
    except InstructionNormalizationError:
        return None
    events = _events(tx, mint)
    mint_bound = mint in accounts or bool(events) or any(
        isinstance(row, Mapping) and row.get("mint") == mint
        for key in ("preTokenBalances", "postTokenBalances") for row in meta.get(key) or [])
    if not mint_bound:
        return None
    signature = ((tx.get("transaction") or {}).get("signatures") or [None])[0]
    if not isinstance(signature, str):
        return None
    fee_payer = accounts[0] if accounts else None
    signer_rows = (tx.get("transaction") or {}).get("message", {}).get("accountKeys") or []
    signers = [row.get("pubkey") for row in signer_rows if isinstance(row, Mapping) and row.get("signer") and isinstance(row.get("pubkey"), str)]
    if not signers and fee_payer:
        signers = [fee_payer]
    semantic = "TARGET_RELEVANT_NON_TRADE"
    instruction_indices = [row["outer_index"] for row in outer if row["program_id"] == PUMPFUN_PROGRAM]
    create_logs = [i for i, line in enumerate(meta.get("logMessages") or []) if line == "Program log: Instruction: Create"]
    buy_logs = [i for i, line in enumerate(meta.get("logMessages") or []) if line == "Program log: Instruction: Buy"]
    if PUMPFUN_PROGRAM in outer_programs and create_logs:
        semantic = "PUMPFUN_CREATE"
    if any(event["action_type"] == "BUY" for event in events):
        semantic = "PUMPFUN_BUY"
    elif any(event["action_type"] == "SELL" for event in events):
        semantic = "PUMPFUN_SELL"
    buyer = next((event["buyer"] for event in events if event["action_type"] == "BUY"), None)
    relevant = {address for address in (mint, creator, fee_payer, buyer) if isinstance(address, str)}
    target_accounts = [address for address in accounts if address in relevant]
    return {"signature": signature, "slot": slot, "transaction_index": transaction_index,
            "success": meta.get("err") is None, "outer_program_ids": outer_programs,
            "inner_program_ids": inner_programs, "target_relevant_accounts": target_accounts,
            "semantic_classification": semantic, "pumpfun_outer_instruction_indices": instruction_indices,
            "create_log_indices": create_logs, "buy_log_indices": buy_logs, "trade_events": events,
            "fee_payer": fee_payer, "signers": signers, "target_token_balance_deltas": _token_deltas(tx, mint),
            "relevant_sol_balance_deltas": _sol_deltas(tx, accounts, relevant),
            "creator_role": "CREATOR" if buyer == creator or fee_payer == creator else "NON_CREATOR"}


def reduce_block(payload: Mapping[str, object], *, slot: int, mint: str, creator: str, response_bytes: int) -> dict:
    block = payload.get("result", payload)
    if not isinstance(block, Mapping) or not isinstance(block.get("transactions"), list):
        raise ValueError("SEMANTIC_BLOCK_TRANSACTIONS_UNAVAILABLE")
    records = [row for index, tx in enumerate(block["transactions"])
               if isinstance(tx, Mapping) and (row := _record(tx, slot=slot, transaction_index=index, mint=mint, creator=creator))]
    compact = {"slot": slot, "block_time": block.get("blockTime"), "response_bytes": response_bytes,
               "source_digest": sha256(_canonical(payload)).hexdigest(), "target_relevant_transactions": records}
    compact["compact_digest"] = sha256(_canonical(compact)).hexdigest()
    return compact


def price_and_valuation(event: Mapping[str, object]) -> dict:
    raw_token, lamports = event.get("token_amount"), event.get("sol_amount")
    if not isinstance(raw_token, int) or raw_token <= 0 or not isinstance(lamports, int) or lamports < 0:
        return {"price_status": "PRICE_INPUT_INSUFFICIENT"}
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        token = Decimal(raw_token) / (Decimal(10) ** PUMP_DECIMALS)
        sol = Decimal(lamports) / Decimal(1_000_000_000)
        price = sol / token
    record = {"price_status": "PRICE_QUALIFIED", "token_decimals": PUMP_DECIMALS,
              "token_raw": raw_token, "token_normalized": _decimal(token), "quote_lamports": lamports,
              "quote_sol": _decimal(sol), "average_execution_price_sol_per_token": _decimal(price)}
    reserves = (event.get("post_virtual_sol_reserves"), event.get("post_virtual_token_reserves"))
    if all(isinstance(value, int) and value > 0 for value in reserves):
        value = market_cap_sol(virtual_sol_reserves=reserves[0], virtual_token_reserves=reserves[1],
                               supply_raw=PUMP_TOTAL_SUPPLY_RAW, decimals=PUMP_DECIMALS)
        record.update({"valuation_status": "PUMPFUN_CURVE_IMPLIED_MC_SOL_QUALIFIED",
                       "valuation_basis": {"supply_raw": PUMP_TOTAL_SUPPLY_RAW, "token_decimals": PUMP_DECIMALS,
                                           "source": "CREATE_MINTTO_AND_INITIALIZEMINT2_PLUS_POST_BUY_TRADE_EVENT_RESERVES"},
                       "post_buy_implied_market_cap_sol": _decimal(value)})
    else:
        record["valuation_status"] = "POST_BUY_RESERVES_UNAVAILABLE"
    return record
