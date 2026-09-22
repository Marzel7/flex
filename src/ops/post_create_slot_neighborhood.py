"""Generic bounded, deduplicated getBlock acquisition and replay contract."""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Callable, Iterable, Mapping

SCHEMA_VERSION = "post_create_slot_neighborhood.v1"
MINT_STATES = {"NOT_STARTED","ANCHORING","ACQUIRING_SLOTS","DECODING","READY","OBSERVED_ONLY","BUNDLE_UNRESOLVED","SAME_SLOT_UNRESOLVED","NO_POST_TRADE","BOUND_EXHAUSTED","PROVIDER_FAILED","EVIDENCE_MISSING"}
SLOT_STATES = {"NOT_REQUESTED","FETCHED","CACHED","NULL_SKIPPED","PROVIDER_FAILED"}

def canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"))
def digest(value): return sha256(canonical(value).encode()).hexdigest()

@dataclass(frozen=True)
class Subject:
    mint: str
    create_signature: str
    create_slot: int
    creator: str
    birth_time: int | None = None

def slot_plan(subjects: Iterable[Subject], *, forward_slots: int = 8) -> dict:
    if forward_slots != 8: raise ValueError("FROZEN_FORWARD_SLOT_BOUND_IS_8")
    values = list(subjects)
    if any(not item.create_signature or item.create_slot < 0 for item in values): raise ValueError("EXACT_CREATE_ANCHOR_REQUIRED")
    slots = sorted({slot for item in values for slot in range(item.create_slot, item.create_slot + forward_slots + 1)})
    return {"schema_version": SCHEMA_VERSION, "method":"getBlock", "request_configuration":{"transactionDetails":"full","rewards":False,"maxSupportedTransactionVersion":0,"commitment":"finalized"}, "forward_slots":forward_slots, "blocks_per_mint_max":forward_slots+1, "subjects":len(values), "naive_calls":len(values)*(forward_slots+1), "unique_slots":slots, "unique_slot_request_count":len(slots), "deduplication_savings":len(values)*(forward_slots+1)-len(slots), "slot_set_digest":digest(slots), "address_history_calls":0, "retry_failover":False}

def get_block_request(slot: int, configuration: Mapping[str, object] | None = None) -> dict:
    cfg = {"transactionDetails":"full","rewards":False,"maxSupportedTransactionVersion":0,"commitment":"finalized"}
    if configuration: cfg.update(configuration)
    return {"jsonrpc":"2.0","id":slot,"method":"getBlock","params":[slot,cfg]}

def raw_identity(provider: str, slot: int, configuration: Mapping[str, object], raw: bytes) -> dict:
    value = {"provider":provider,"method":"getBlock","slot":slot,"request_configuration":dict(configuration),"raw_sha256":sha256(raw).hexdigest()}
    value["raw_artifact_id"] = digest(value); return value

def retain_block(cache: dict, *, provider: str, slot: int, configuration: Mapping[str, object], raw: bytes | None) -> dict:
    key = (provider, slot, canonical(configuration))
    if key in cache: return {"state":"CACHED","raw":cache[key]}
    if raw is None:
        cache[key] = {"state":"NULL_SKIPPED","slot":slot}; return cache[key]
    cache[key] = {"state":"FETCHED", **raw_identity(provider,slot,configuration,raw)}
    return cache[key]

def ordered_relevant_transactions(block: Mapping[str, object], *, mint: str, decoder: Callable[[Mapping[str, object], int, str], Iterable[Mapping[str, object]]]) -> list[dict]:
    """Preserve provider block-array order and explicit instruction indices only."""
    transactions = block.get("transactions")
    if not isinstance(transactions, list): raise ValueError("BLOCK_TRANSACTION_ORDER_UNAVAILABLE")
    out=[]
    for tx_index, tx in enumerate(transactions):
        text=canonical(tx)
        if mint not in text: continue
        for action in decoder(tx, tx_index, mint):
            row=dict(action)
            if not isinstance(row.get("instruction_index"), int): raise ValueError("INSTRUCTION_ORDER_UNAVAILABLE")
            row["transaction_index"] = tx_index
            row["slot"] = block.get("parentSlot", block.get("slot"))
            out.append(row)
    return sorted(out,key=lambda row:(row["transaction_index"],row["instruction_index"],row.get("inner_instruction_index",-1)))

def validate_anchor(actions: Iterable[Mapping[str, object]], signature: str) -> None:
    if not any(item.get("signature") == signature and item.get("action_type") == "CREATE" for item in actions): raise ValueError("CREATE_ANCHOR_NOT_FOUND")

def checkpoint_slot(state: dict, slot: int, raw_record: Mapping[str, object]) -> None:
    if raw_record.get("state") not in {"FETCHED","CACHED","NULL_SKIPPED","PROVIDER_FAILED"}: raise ValueError("RAW_BEFORE_CHECKPOINT")
    state.setdefault("slots", {})[str(slot)] = dict(raw_record)
