"""Operation-agnostic compact early-trade record construction.

Provider transport belongs at the edge.  This module retains only the bounded
facts needed to replay a conservative entry qualification; it never fetches
blocks, walks wallets, or infers intra-transaction ordering.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "compact_early_trade_record.v1"
MAX_EARLY_TRADES = 10


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _pick(value: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if value.get(name) is not None:
            return value[name]
    return None


def normalize_trade(item: Mapping[str, Any]) -> dict[str, Any]:
    """Project one provider trade to immutable, evidence-relevant fields."""
    signers = _pick(item, "signers", "signer")
    if isinstance(signers, str):
        signers = [signers]
    if not isinstance(signers, list):
        signers = []
    return {
        "block_or_time": {"slot": _pick(item, "block_number", "blockNumber", "slot"),
                          "timestamp": _pick(item, "block_unix_time", "blockUnixTime", "unixTime", "timestamp")},
        "signature": _pick(item, "tx_hash", "txHash", "signature"),
        "buyer_or_signer": {"buyer": _pick(item, "owner", "owner_address", "ownerAddress", "wallet"), "signers": signers},
        "sol_spent": _pick(item, "quote_ui_amount", "quoteUiAmount", "quote_amount", "quoteAmount"),
        "tokens_received": _pick(item, "base_ui_amount", "baseUiAmount", "base_amount", "baseAmount"),
        "execution_price": _pick(item, "price", "price_usd", "priceUsd"),
        "source_or_venue": _pick(item, "source", "source_name", "sourceName"),
        "provider_order": _pick(item, "provider_order", "index", "order"),
        "provider_labels": _pick(item, "labels", "label", "tags"),
        "side": _pick(item, "tx_type", "txType", "side", "type"),
    }


def _ordered_early_buys(trades: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buys = [normalize_trade(item) for item in trades]
    buys = [item for item in buys if str(item.get("side") or "").lower() in {"buy", "swap"}]
    # A provider timestamp/slot is not instruction ordering.  Stable ordering
    # only makes the retained feed replayable; it never qualifies an entry.
    return sorted(buys, key=lambda item: (item["block_or_time"].get("timestamp") is None, item["block_or_time"].get("timestamp"), str(item.get("signature") or "")))[:MAX_EARLY_TRADES]


def build_compact_record(*, operation_id: str, birth: Mapping[str, Any], provider_trades: Iterable[Mapping[str, Any]],
                         trade_response_sha256: str, price_summary: Mapping[str, Any] | None,
                         price_response_sha256: str | None, acquired_at: str, parser_identity: str) -> dict[str, Any]:
    """Build a fail-closed mint record from bounded provider evidence."""
    mint = str(birth["mint"])
    early_buys = _ordered_early_buys(provider_trades)
    create_time = int(birth["birth_timestamp_unix"])
    create_slot = birth.get("create_slot")
    first = next((item for item in early_buys if item["buyer_or_signer"].get("buyer") != birth.get("creator")), None)
    record = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": operation_id,
        "mint": mint,
        "creator": birth.get("creator"),
        "launch_time_or_slot": {"timestamp": create_time, "slot": create_slot, "signature": birth.get("create_signature"),
                                  "platform": birth.get("launch_platform")},
        "early_buys": early_buys,
        "first_non_creator_buy": None if first is None else {**first, "confidence": "PROVIDER_ORDER_ONLY_UNQUALIFIED"},
        "first_actionable_buy": None,
        "first_actionable_buy_status": "INSUFFICIENT_EVIDENCE_PROVIDER_ORDER_AND_CURVE_PRESTATE_UNAVAILABLE",
        "early_buyer_addresses": sorted({str(x["buyer_or_signer"]["buyer"]) for x in early_buys if x["buyer_or_signer"].get("buyer")}),
        "price_summary": dict(price_summary or {}),
        "provider": {"name": "Birdeye", "query_bounds": {"trade_endpoint": "/defi/v3/token/txs", "max_early_buys": MAX_EARLY_TRADES},
                     "parser_version": parser_identity, "response_digest": {"trades": trade_response_sha256, "price": price_response_sha256},
                     "acquired_at": acquired_at},
    }
    record["record_sha256"] = canonical_sha256(record)
    return record
