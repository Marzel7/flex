"""Bounded, provider-agnostic decoding for a birth-anchored Pump.fun opening.

This module has no database, listener, membership, or UI dependency.  Callers
must retain raw provider responses before passing them here.
"""
from __future__ import annotations

import base64
from typing import Any, Mapping

from src.ops.pumpfun_curve_birth_recovery import decode_pumpfun_create

TRADE_EVENT_DISCRIMINATOR = bytes.fromhex("bddb7fd34ee661ee")
PUMP_TOTAL_SUPPLY_RAW = 1_000_000_000_000_000
PUMP_DECIMALS = 6


def _b58(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = int.from_bytes(data, "big")
    encoded = ""
    while number:
        number, rem = divmod(number, 58)
        encoded = alphabet[rem] + encoded
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + (encoded or "")


def _u64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 8], "little")


def decode_trade_event(payload: bytes) -> dict[str, Any] | None:
    """Decode the stable TradeEvent prefix needed by ``opening_impulse``.

    The prefix is the locally retained official-IDL schema: discriminator,
    mint, sol/token amounts, buy flag, user, timestamp and four reserve fields.
    Later version-specific fields are intentionally ignored.
    """
    if len(payload) < 129 or payload[:8] != TRADE_EVENT_DISCRIMINATOR:
        return None
    return {
        "mint": _b58(payload[8:40]), "sol_amount": _u64(payload, 40),
        "token_amount": _u64(payload, 48), "action_type": "BUY" if payload[56] else "SELL",
        "buyer": _b58(payload[57:89]), "timestamp": int.from_bytes(payload[89:97], "little", signed=True),
        "post_virtual_sol_reserves": _u64(payload, 97),
        "post_virtual_token_reserves": _u64(payload, 105),
        "post_real_sol_reserves": _u64(payload, 113),
        "post_real_token_reserves": _u64(payload, 121),
    }


def creation_context(transaction: Mapping[str, Any], *, mint: str) -> dict[str, Any] | None:
    decoded = decode_pumpfun_create(transaction)
    if decoded is None or decoded[1] != mint or not isinstance(transaction.get("slot"), int):
        return None
    kind, _, creator, curve = decoded
    return {"create_type": kind, "creator": creator, "bonding_curve": curve,
            "creation_slot": transaction["slot"]}


def _actions_from_item(item: Mapping[str, Any], *, mint: str, slot: int, transaction_index: int | None) -> list[dict[str, Any]]:
    if (item.get("meta") or {}).get("err") is not None:
        return []
    message = (item.get("transaction") or {}).get("message") or {}
    signatures = message.get("signatures") or []
    result=[]
    for action_index, line in enumerate((item.get("meta") or {}).get("logMessages") or []):
        if not isinstance(line,str) or not line.startswith("Program data: "): continue
        try: event=decode_trade_event(base64.b64decode(line.split(": ",1)[1]))
        except Exception: event=None
        if event and event["mint"]==mint:
            event.update({"slot":slot,"transaction_index":transaction_index,"action_index":action_index,"signature":signatures[0] if signatures else None}); result.append(event)
    return result

def actions_from_transaction(transaction: Mapping[str, Any], *, mint: str, slot: int) -> list[dict[str, Any]]:
    """Single getTransaction envelope; transaction index is intentionally None."""
    return _actions_from_item(transaction.get("result",transaction),mint=mint,slot=slot,transaction_index=None)

def actions_from_block(block: Mapping[str, Any], *, mint: str, slot: int) -> list[dict[str, Any]]:
    """Extract only successful target TradeEvents, preserving tx/log order."""
    result = block.get("result", block)
    transactions = result.get("transactions", []) if isinstance(result, Mapping) else []
    actions: list[dict[str, Any]] = []
    for tx_index, item in enumerate(transactions):
        if isinstance(item,Mapping): actions.extend(_actions_from_item(item,mint=mint,slot=slot,transaction_index=tx_index))
    return actions
