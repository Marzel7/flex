"""Replayable first-actionable-buy qualification for any token birth.

This module is deliberately evidence-only: it selects an entry anchor from
already retained observations and never performs provider access or trades.
"""
from __future__ import annotations
import hashlib
import json
from collections import Counter
from typing import Iterable, Mapping


FIRST_ACTIONABLE_BUY_OPPORTUNITY = "FIRST_ACTIONABLE_BUY_OPPORTUNITY"
FIRST_OBSERVED_EXTERNAL_BUY = "FIRST_OBSERVED_EXTERNAL_BUY"
FIRST_ACTIONABLE_BUY_ORDER_UNRESOLVED = "FIRST_ACTIONABLE_BUY_ORDER_UNRESOLVED"
BUNDLED_INITIAL_PERFORMANCE_NOT_COUNTED_AS_TRADER_RETURN = "PASS"


def _canonical_id(value: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _required(record: Mapping[str, object], *keys: str) -> None:
    missing = [key for key in keys if record.get(key) is None]
    if missing:
        raise ValueError("MISSING_REQUIRED_EVIDENCE:" + ",".join(missing))


def qualify_first_actionable_buy(
    *,
    mint: str,
    create_slot: int,
    create_timestamp: int,
    observations: Iterable[Mapping[str, object]],
    provenance: Iterable[str],
) -> dict:
    """Return the earliest post-launch anchor without retrospective ordering.

    Each observation is a retained trade/state with ``kind`` (CREATE or BUY),
    ``slot``, ``transaction_index`` and ``instruction_index`` when ordering is
    known, plus ``launch_controlled``.  A post-bundle curve state is an
    actionable opportunity; otherwise the first ordered external buy is only
    an observed external buy.  Ambiguous same-slot candidates fail closed.
    """
    if not mint or not isinstance(create_slot, int) or not isinstance(create_timestamp, int):
        raise ValueError("INVALID_CREATE_ANCHOR")
    source = [dict(item) for item in observations]
    if not source:
        return _result(mint, create_slot, create_timestamp, "INSUFFICIENT_EVIDENCE", None, (), provenance)
    for item in source:
        _required(item, "kind", "slot")
        if item["kind"] not in {"CREATE", "BUY"}:
            raise ValueError("INVALID_OBSERVATION_KIND")

    launch_buys = [item for item in source if item["kind"] == "BUY" and item.get("launch_controlled") is True]
    external = [item for item in source if item["kind"] == "BUY" and item.get("launch_controlled") is False]
    if not external:
        status = "NO_SUBSEQUENT_TRADE" if launch_buys else "INSUFFICIENT_EVIDENCE"
        return _result(mint, create_slot, create_timestamp, status, None, launch_buys, provenance)

    # Exact ordering is mandatory for observations that could compete in one slot.
    def ordering(item: Mapping[str, object]):
        if not isinstance(item.get("transaction_index"), int) or not isinstance(item.get("instruction_index"), int):
            return None
        return (int(item["slot"]), int(item["transaction_index"]), int(item["instruction_index"]))

    launch_orders = [ordering(item) for item in launch_buys]
    external_orders = [ordering(item) for item in external]
    if any(value is None for value in launch_orders + external_orders):
        return _result(mint, create_slot, create_timestamp, FIRST_ACTIONABLE_BUY_ORDER_UNRESOLVED, None, launch_buys, provenance)
    boundary = max(launch_orders) if launch_orders else (create_slot, -1, -1)
    candidates = [(ordering(item), item) for item in external if ordering(item) > boundary]
    if not candidates:
        return _result(mint, create_slot, create_timestamp, "BUNDLE_BOUNDARY_UNRESOLVED", None, launch_buys, provenance)
    candidates.sort(key=lambda value: value[0])
    order, first_external = candidates[0]
    # A retained immediately-preceding state is needed to claim an executable opportunity.
    pre_state = first_external.get("curve_state_before")
    status = FIRST_ACTIONABLE_BUY_OPPORTUNITY if isinstance(pre_state, Mapping) else FIRST_OBSERVED_EXTERNAL_BUY
    return _result(mint, create_slot, create_timestamp, status, first_external, launch_buys, provenance, order)


def _result(mint, create_slot, create_timestamp, status, entry, bundled, provenance, order=None):
    entry_data = None
    if entry is not None:
        _required(entry, "slot", "signature", "timestamp", "price")
        entry_data = {
            "timestamp": entry["timestamp"], "slot": entry["slot"], "signature": entry["signature"],
            "ordering": {"transaction_index": order[1], "instruction_index": order[2]},
            "price": entry["price"], "market_cap": entry.get("market_cap"), "fdv": entry.get("fdv"),
            "supply_semantics": entry.get("supply_semantics"), "curve_state": entry.get("curve_state_before") if status == FIRST_ACTIONABLE_BUY_OPPORTUNITY else entry.get("curve_state_after"),
            "elapsed_time_from_create": entry["timestamp"] - create_timestamp,
            "elapsed_slots_from_create": entry["slot"] - create_slot,
        }
    bundled_evidence = [{key: item.get(key) for key in ("signature", "slot", "transaction_index", "instruction_index", "buyer", "signer", "fee_payer", "sol_amount", "token_amount", "curve_state_before", "curve_state_after", "relationship")} for item in bundled]
    body = {"mint": mint, "create": {"slot": create_slot, "timestamp": create_timestamp}, "classification": status, "entry": entry_data, "bundled_buys": bundled_evidence, "evidence_provenance": sorted(set(provenance)), "invariants": {"MIGRATION_REQUIRED_FOR_ENTRY_ANALYSIS": "NO", "BUNDLED_INITIAL_PERFORMANCE_NOT_COUNTED_AS_TRADER_RETURN": BUNDLED_INITIAL_PERFORMANCE_NOT_COUNTED_AS_TRADER_RETURN}}
    body["record_id"] = _canonical_id(body)
    return body


def entry_relative_performance(entry_price: float, observations: Mapping[str, float | None]) -> dict:
    """Calculate only requested entry-relative multiples; missing remains missing."""
    if not isinstance(entry_price, (int, float)) or entry_price <= 0:
        raise ValueError("INVALID_ENTRY_PRICE")
    horizon_map = {"5m_peak": "ENTRY_TO_5M_PEAK", "1h_peak": "ENTRY_TO_1H_PEAK", "6h_peak": "ENTRY_TO_6H_PEAK", "24h_peak": "ENTRY_TO_24H_PEAK", "migration": "ENTRY_TO_MIGRATION", "post_migration_peak": "ENTRY_TO_POST_MIGRATION_PEAK"}
    return {target: None if observations.get(source) is None else observations[source] / entry_price for source, target in horizon_map.items()}


def entry_coverage(records: Iterable[Mapping[str, object]]) -> dict:
    """Keep every birth in the denominator and classify one terminal status each."""
    values = [record.get("classification", "INSUFFICIENT_EVIDENCE") for record in records]
    counts = Counter(values)
    return {"total_births": len(values), "first_actionable_entry_qualified": counts[FIRST_ACTIONABLE_BUY_OPPORTUNITY], "first_observed_external_buy_only": counts[FIRST_OBSERVED_EXTERNAL_BUY], "bundle_boundary_unresolved": counts["BUNDLE_BOUNDARY_UNRESOLVED"] + counts[FIRST_ACTIONABLE_BUY_ORDER_UNRESOLVED], "no_subsequent_trade": counts["NO_SUBSEQUENT_TRADE"], "insufficient_evidence": counts["INSUFFICIENT_EVIDENCE"], "classification_counts": dict(sorted(counts.items()))}

def attach(qualification_id:str,mint:str,qualification_commit:int,observation:dict)->dict:
 if int(observation['availability_timestamp'])<int(qualification_commit):raise ValueError('PRE_QUALIFICATION_OBSERVATION')
 out={'qualification_id':qualification_id,'mint':mint,'qualification_commit_timestamp':int(qualification_commit),'observation_timestamp':int(observation['timestamp']),'availability_timestamp':int(observation['availability_timestamp']),'price':observation['price'],'units':observation['units'],'source':observation['source'],'evidence_grade':observation['evidence_grade']};out['id']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();return out
