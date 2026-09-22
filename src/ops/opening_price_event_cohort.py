"""Provider-free event-level opening cohort and compact Birdeye candle parsing."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Mapping

from .opening_price_semantic_reacquisition import price_and_valuation

VERSION = "OPENING_PRICE_EVENT_COHORT_V1"

def canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

def opening_events(artifact: Mapping[str, object]) -> list[dict]:
    mint, creator = artifact["mint"], artifact["create"]["creator"]
    rows = []
    for block in artifact["blocks"]:
        for tx in block["target_relevant_transactions"]:
            if tx["slot"] != artifact["create"]["slot"] or tx["transaction_index"] not in (289,290,291): continue
            for within_tx, event in enumerate(sorted((e for e in tx["trade_events"] if e["mint"] == mint and e["action_type"] == "BUY"), key=lambda e:e["event_log_index"]), 1):
                rows.append({"transaction_index":tx["transaction_index"],"event_ordinal_within_transaction":within_tx,"event_log_index":event["event_log_index"],"signature":tx["signature"],"success":tx["success"],"mint":mint,"buyer":event["buyer"],"creator_classification":"CREATOR_BUY" if event["buyer"] == creator else "NON_CREATOR_BUY","venue":"PUMPFUN_BONDING_CURVE","source_evidence_digest":block["source_digest"],"event":event,**price_and_valuation(event)})
    rows.sort(key=lambda row:(artifact["create"]["slot"],row["transaction_index"],row["event_log_index"]))
    for ordinal, row in enumerate(rows, 1): row["canonical_opening_ordinal"] = ordinal
    return rows

def compact_candles(payload: Mapping[str, object], *, mint: str, request: Mapping[str, object]) -> list[dict]:
    items = ((payload.get("data") or {}).get("items") or [])
    if not isinstance(items, list): raise ValueError("BIRDEYE_ITEMS_UNAVAILABLE")
    response_digest = sha256(canonical(payload)).hexdigest(); rows=[]
    for item in items:
        if not isinstance(item, Mapping): continue
        timestamp = item.get("unix_time", item.get("unixTime"))
        if not isinstance(timestamp, int) or item.get("type") != "1s": continue
        fields = {"open":item.get("mcap_open", item.get("o")),"high":item.get("mcap_high", item.get("h")),"low":item.get("mcap_low", item.get("l")),"close":item.get("mcap_close", item.get("c"))}
        if any(value is None for value in fields.values()): continue
        # Generic o/h/l/c keys do not themselves attest whether this endpoint
        # returned price or market-cap values.  Request intent is provenance,
        # not response semantics.
        chart_type = "MCAP" if all(key in item for key in ("mcap_open","mcap_high","mcap_low","mcap_close")) else "UNVERIFIED_GENERIC_OHLC"
        row={"mint":mint,"interval":"1s","timestamp":timestamp,**fields,"volume":item.get("v"),"currency":item.get("currency"),"chart_type":chart_type,"provider":"Birdeye","request_parameters":dict(request),"response_digest":response_digest}
        row["candle_record_digest"]=sha256(canonical(row)).hexdigest(); rows.append(row)
    return sorted(rows,key=lambda row:row["timestamp"])
