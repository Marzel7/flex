"""DEV-013 provider-bounded FIRST_AVAILABLE_MC evidence reducer.

This module is intentionally dormant: callers inject one Helius transaction
reader and one Birdeye trade reader.  It neither opens databases nor performs
network I/O.  The result is compact, deterministic and fail-closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


CREATE_SIGNATURE_SOURCE = "canonical_create_ledger"
WINDOW_SECONDS = 30
MAX_HELIUS_CALLS = 1
MAX_BIRDEYE_CALLS = 1


@dataclass(frozen=True)
class FirstAvailableResult:
    status: str
    evidence: Mapping[str, Any]
    helius_calls: int
    birdeye_calls: int


def _value(value: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if value.get(name) is not None:
            return value[name]
    return None


def normalize_trade(raw: Mapping[str, Any], provider_index: int) -> dict[str, Any] | None:
    """Normalize only the compact fields required by the frozen contract."""
    slot = _value(raw, "slot", "block_slot")
    price = _value(raw, "execution_price_usd", "price_usd", "price")
    if not isinstance(slot, int) or isinstance(slot, bool) or not isinstance(price, (int, float)) or price <= 0:
        return None
    instruction = _value(raw, "instruction_index", "instructionIndex", "index")
    order = _value(raw, "transaction_order", "transaction_index", "tx_index", "order")
    timestamp = _value(raw, "timestamp", "block_time", "time")
    # Missing ordering fields sort after known fields; provider order is the last tie-breaker.
    return {
        "signature": _value(raw, "signature", "tx_hash", "txHash"),
        "slot": slot,
        "transaction_order": order if isinstance(order, int) else None,
        "instruction_index": instruction if isinstance(instruction, int) else None,
        "timestamp": timestamp if isinstance(timestamp, (int, float)) else None,
        "owner": _value(raw, "owner", "wallet", "user"),
        "side": _value(raw, "side", "trade_side"),
        "venue": _value(raw, "venue", "source", "platform"),
        "execution_price_usd": float(price),
        "provider_order": provider_index,
    }


def _order_key(trade: Mapping[str, Any]) -> tuple[Any, ...]:
    unknown = 2**63 - 1
    return (
        trade["slot"],
        trade["transaction_order"] if trade["transaction_order"] is not None else unknown,
        trade["instruction_index"] if trade["instruction_index"] is not None else unknown,
        trade["timestamp"] if trade["timestamp"] is not None else float("inf"),
        trade["provider_order"],
    )


def collect_first_available(
    *,
    mint: str,
    canonical_create_signature: str,
    qualified_supply: int | float | None,
    helius_get_transaction: Callable[[str], Mapping[str, Any] | None],
    birdeye_get_trades: Callable[[str, int, int], Sequence[Mapping[str, Any]] | None],
) -> FirstAvailableResult:
    """Run exactly one bounded acquisition from injected transports, without retries."""
    if not mint or not canonical_create_signature:
        return FirstAvailableResult("INSUFFICIENT_CREATE_EVIDENCE", {}, 0, 0)
    if not isinstance(qualified_supply, (int, float)) or qualified_supply <= 0:
        return FirstAvailableResult("INSUFFICIENT_SUPPLY_EVIDENCE", {}, 0, 0)
    tx = helius_get_transaction(canonical_create_signature)
    if not isinstance(tx, Mapping):
        return FirstAvailableResult("INSUFFICIENT_CREATE_EVIDENCE", {}, 1, 0)
    create_slot = _value(tx, "slot")
    create_time = _value(tx, "blockTime", "block_time")
    if not isinstance(create_slot, int) or not isinstance(create_time, int):
        return FirstAvailableResult("INSUFFICIENT_CREATE_EVIDENCE", {}, 1, 0)
    raw_trades = birdeye_get_trades(mint, create_time - 1, create_time + WINDOW_SECONDS)
    if raw_trades is None:
        return FirstAvailableResult("INSUFFICIENT_TRADE_EVIDENCE", {}, 1, 1)
    normalized = [t for index, raw in enumerate(raw_trades) if isinstance(raw, Mapping)
                  if (t := normalize_trade(raw, index)) is not None]
    normalized.sort(key=_order_key)
    classes = {"PRE_CREATE_SLOT": 0, "CREATE_SLOT": 0, "OPEN_SLOT": 0}
    for trade in normalized:
        classes["PRE_CREATE_SLOT" if trade["slot"] < create_slot else
                "CREATE_SLOT" if trade["slot"] == create_slot else "OPEN_SLOT"] += 1
    first = next((trade for trade in normalized if trade["slot"] > create_slot), None)
    base = {"mint": mint, "canonical_create_signature": canonical_create_signature,
            "canonical_create_source": CREATE_SIGNATURE_SOURCE, "create_slot": create_slot,
            "canonical_create_time": create_time, "qualified_token_supply": qualified_supply,
            "request": {"endpoint": "/defi/v3/token/txs", "after_time": create_time - 1,
                        "before_time": create_time + WINDOW_SECONDS},
            "slot_trade_counts": classes}
    if first is None:
        return FirstAvailableResult("NO_FIRST_AVAILABLE_TRADE_IN_WINDOW", base, 1, 1)
    compact = {key: first[key] for key in ("signature", "slot", "timestamp", "instruction_index", "owner", "side", "venue", "execution_price_usd")}
    base.update({"first_available": compact, "first_available_slot_offset": first["slot"] - create_slot,
                 "first_available_mc_usd": first["execution_price_usd"] * qualified_supply})
    return FirstAvailableResult("QUALIFIED", base, 1, 1)
