"""Deterministic, compact semantic candidate reduction for CREATE-to-B3 scans.

The scanner intentionally reduces an in-memory ``getBlock`` full/json response
before it can be checkpointed.  It is not an amount, price, or event decoder.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Mapping

from .opening_price_instruction_normalization import (
    InstructionNormalizationError,
    canonical_account_keys,
    normalize_outer_instructions,
)

VERSION = "OPENING_PRICE_SEMANTIC_BLOCK_SCANNER_V1"
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PAMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _block(payload: Mapping[str, object]) -> Mapping[str, object]:
    value = payload.get("result", payload)
    if not isinstance(value, Mapping):
        raise ValueError("SEMANTIC_BLOCK_RESULT_UNAVAILABLE")
    return value


def _signature(tx: Mapping[str, object]) -> str:
    signatures = (tx.get("transaction") or {}).get("signatures")
    if not isinstance(signatures, list) or not signatures or not isinstance(signatures[0], str):
        raise ValueError("SEMANTIC_BLOCK_SIGNATURE_UNAVAILABLE")
    return signatures[0]


def _fee_payer(tx: Mapping[str, object]) -> str | None:
    try:
        return canonical_account_keys(tx)[0]
    except (IndexError, InstructionNormalizationError):
        return None


def _logs(tx: Mapping[str, object]) -> list[str]:
    logs = (tx.get("meta") or {}).get("logMessages") or []
    return [item for item in logs if isinstance(item, str)]


def _mint_bound(tx: Mapping[str, object], mint: str, outer: list[dict]) -> bool:
    # Account vectors are authoritative for the target-mint relationship. Token
    # balance mint fields provide compatible corroboration for parsed responses.
    try:
        accounts = canonical_account_keys(tx)
    except InstructionNormalizationError:
        accounts = []
    if mint in accounts or any(mint in item["resolved_accounts"] for item in outer):
        return True
    meta = tx.get("meta") or {}
    for key in ("preTokenBalances", "postTokenBalances"):
        for balance in meta.get(key) or []:
            if isinstance(balance, Mapping) and balance.get("mint") == mint:
                return True
    return False


def _programs(outer: list[dict]) -> list[str]:
    return sorted({item["program_id"] for item in outer})


def _event(logs: list[str], value: str) -> bool:
    return any("Program log: Instruction: " + value in log for log in logs)


def _classify(tx: Mapping[str, object], *, mint: str, creator: str | None) -> dict | None:
    meta = tx.get("meta") or {}
    try:
        outer = normalize_outer_instructions(tx)
    except InstructionNormalizationError as exc:
        # A malformed transaction becomes relevant only if the target mint can
        # still be proven from parsed balances; otherwise it remains irrelevant.
        if any(isinstance(x, Mapping) and x.get("mint") == mint
               for key in ("preTokenBalances", "postTokenBalances")
               for x in meta.get(key) or []):
            return {"candidate_class": "UNSUPPORTED_RELEVANT_SHAPE", "program_id": None,
                    "buyer_hint": None, "event_hints": [], "reason": str(exc)}
        return None
    if not _mint_bound(tx, mint, outer):
        return None
    if meta.get("err") is not None:
        return None
    programs = _programs(outer)
    logs = _logs(tx)
    has_create = _event(logs, "Create")
    has_buy = _event(logs, "Buy")
    pump = PUMPFUN_PROGRAM in programs
    pamm = PAMM_PROGRAM in programs
    payer = _fee_payer(tx)
    if pump and has_create:
        kind, program, reason = "TARGET_CREATE_CANDIDATE", PUMPFUN_PROGRAM, "TARGET_MINT_PUMPFUN_CREATE"
    elif pump and has_buy:
        kind, program, reason = "TARGET_BUY_CANDIDATE_PUMPFUN", PUMPFUN_PROGRAM, "TARGET_MINT_PUMPFUN_BUY"
    elif pamm and has_buy:
        kind, program, reason = "TARGET_BUY_CANDIDATE_PAMM", PAMM_PROGRAM, "TARGET_MINT_PAMM_BUY"
    elif creator and creator in {payer, *[a for i in outer for a in i["resolved_accounts"]]}:
        kind, program, reason = "TARGET_CREATOR_ACTIVITY_CANDIDATE", None, "TARGET_MINT_CREATOR_ACTIVITY"
    elif pump or pamm:
        kind, program, reason = "UNSUPPORTED_RELEVANT_SHAPE", (PUMPFUN_PROGRAM if pump else PAMM_PROGRAM), "TARGET_MINT_PRIMARY_PROGRAM_WITHOUT_RECOGNIZED_EVENT"
    else:
        kind, program, reason = "UNSUPPORTED_RELEVANT_SHAPE", None, "TARGET_MINT_UNSUPPORTED_PROGRAM"
    hints = []
    if has_create:
        hints.append("CREATE_LOG")
    if has_buy:
        hints.append("BUY_LOG")
    return {"candidate_class": kind, "program_id": program, "buyer_hint": payer,
            "event_hints": hints, "reason": reason}


def reduce_block(payload: Mapping[str, object], *, slot: int, mint: str, creator: str | None = None,
                 response_bytes: int | None = None) -> dict:
    """Produce the only checkpointable representation of a semantic block."""
    block = _block(payload)
    txs = block.get("transactions")
    if not isinstance(txs, list):
        raise ValueError("SEMANTIC_BLOCK_TRANSACTIONS_UNAVAILABLE")
    candidates = []
    for index, tx in enumerate(txs):
        if not isinstance(tx, Mapping):
            raise ValueError("SEMANTIC_BLOCK_TRANSACTION_INVALID")
        result = _classify(tx, mint=mint, creator=creator)
        if result is None:
            continue
        candidates.append({"tx_index": index, "signature": _signature(tx), "candidate_class": result["candidate_class"],
                           "target_mint_match": True, "program_id": result["program_id"],
                           "buyer_hint": result["buyer_hint"], "event_hints": result["event_hints"], "reason": result["reason"]})
    record = {"scanner_version": VERSION, "slot": slot, "block_time": block.get("blockTime"),
              "source_digest": sha256(canonical(payload)).hexdigest(), "response_bytes": response_bytes,
              "transaction_count": len(txs), "candidate_transactions": candidates}
    record["compact_digest"] = sha256(canonical(record)).hexdigest()
    return record
