#!/usr/bin/env python3
"""Run the bounded DEV-014 Pump.fun creation-validation research pilot.

This is intentionally a one-shot, compact-evidence tool.  It obtains at most
ten explicitly frozen ``getTransaction`` responses and never writes raw RPC
payloads, lifecycle state, a queue, or a database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
COVERAGE_PATH = ROOT / "docs/audits/dev014_watchtower_provenance_coverage_20261009.v1.json"
DEFAULT_OUTPUT = ROOT / "docs/audits/dev014_watchtower_creation_validation_pilot_20261009.v1.json"
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
MAX_REQUESTS = 10
MAX_SUCCESS_RECORD_BYTES = 1024
MAX_FAILURE_RECORD_BYTES = 512
MAX_ARTIFACT_BYTES = 1_000_000


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def account_values(message: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for item in message.get("accountKeys", []) or []:
        if isinstance(item, str):
            values.add(item)
        elif isinstance(item, dict) and isinstance(item.get("pubkey"), str):
            values.add(item["pubkey"])
    return values


def mint_in_transaction(transaction: dict[str, Any], mint: str) -> bool:
    message = (transaction.get("transaction") or {}).get("message") or {}
    if mint in account_values(message):
        return True
    meta = transaction.get("meta") or {}
    balances = (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or [])
    return any(isinstance(item, dict) and item.get("mint") == mint for item in balances)


def create_semantics(transaction: dict[str, Any]) -> tuple[bool, str | None]:
    message = (transaction.get("transaction") or {}).get("message") or {}
    accounts = account_values(message)
    if PUMPFUN_PROGRAM not in accounts:
        return False, "PUMPFUN_PROGRAM_ABSENT"
    logs = (transaction.get("meta") or {}).get("logMessages") or []
    text = " ".join(item for item in logs if isinstance(item, str)).lower()
    if PUMPSWAP_PROGRAM.lower() in text or "instruction: migrate" in text or "migratebondingcurvecreator" in text:
        return False, "MIGRATION_EVENT_SUBSTITUTION_REJECTED"
    if "instruction: create" not in text:
        return False, "PUMPFUN_CREATE_SEMANTICS_MISSING"
    return True, None


def request_once(endpoint: str, signature: str) -> tuple[int | None, Any | None, str | None]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "finalized"},
        ],
    }
    try:
        request = Request(endpoint, data=canonical_bytes(payload), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            status = response.status
            body = json.loads(response.read().decode("utf-8"))
        return status, body, None
    except Exception as exc:  # bounded diagnostic only; no response content retained
        return None, None, type(exc).__name__


def select_allowlist(coverage: dict[str, Any]) -> list[dict[str, Any]]:
    records = coverage.get("records") or []
    candidates = []
    for record in records:
        creation = record.get("creation") or {}
        signature = creation.get("signature")
        timestamp = creation.get("timestamp")
        if (
            record.get("current_membership") != "CURRENT_CONFIRMED"
            or creation.get("class") == "CREATION_QUALIFIED"
            or not isinstance(signature, str)
            or not signature
            or not isinstance(timestamp, int)
            or timestamp <= 0
        ):
            continue
        candidates.append({"mint": record.get("mint"), "signature": signature, "timestamp": timestamp})
    if len(candidates) != 533:
        raise ValueError(f"expected 533 retained creation-signature candidates, found {len(candidates)}")
    candidates.sort(key=lambda item: (-item["timestamp"], item["mint"]))
    selected = candidates[:MAX_REQUESTS]
    if len({item["mint"] for item in selected}) != MAX_REQUESTS or len({item["signature"] for item in selected}) != MAX_REQUESTS:
        raise ValueError("selected creation allowlist has duplicate mint or signature")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("PILOT_OUTPUT_ALREADY_EXISTS")
    endpoint = os.environ.get("HELIUS_RPC_URL") or os.environ.get("HELIUS_ENDPOINT")
    if not endpoint or urlparse(endpoint).hostname != "mainnet.helius-rpc.com":
        raise SystemExit("AUTHORITATIVE_HELIUS_ENDPOINT_REQUIRED")
    coverage = json.loads(COVERAGE_PATH.read_text())
    allowlist = select_allowlist(coverage)
    coverage_hash = hashlib.sha256(COVERAGE_PATH.read_bytes()).hexdigest()
    frozen = []
    for ordinal, item in enumerate(allowlist, start=1):
        request_identity = digest({"contract": "DEV014_CREATION_VALIDATION_V1", "coverage_sha256": coverage_hash, "ordinal": ordinal, **item})
        frozen.append({**item, "ordinal": ordinal, "request_identity": request_identity})

    records = []
    for item in frozen:  # deliberately sequential: concurrency one, one request per signature
        status, body, transport_error = request_once(endpoint, item["signature"])
        result = body.get("result") if isinstance(body, dict) else None
        record: dict[str, Any] = {
            "mint": item["mint"],
            "creation_signature": item["signature"],
            "request_identity": item["request_identity"],
            "provider": "HELIUS_MAINNET_RPC",
            "http_status": status,
            "transaction_success": False,
            "mint_binding_verified": False,
            "pumpfun_create_verified": False,
            "validated_creation_slot": None,
            "qualification_status": "UNQUALIFIED",
            "conflict_reason": None,
        }
        if transport_error:
            record["conflict_reason"] = f"TRANSPORT_{transport_error}"
        elif status != 200 or not isinstance(body, dict):
            record["conflict_reason"] = "RPC_HTTP_OR_SHAPE_FAILURE"
        elif body.get("error") is not None or not isinstance(result, dict):
            record["conflict_reason"] = "RPC_RESULT_UNAVAILABLE"
        else:
            meta = result.get("meta") or {}
            slot = result.get("slot")
            successful = meta.get("err") is None
            mint_bound = mint_in_transaction(result, item["mint"])
            create_ok, semantic_error = create_semantics(result)
            record.update({
                "transaction_success": successful,
                "mint_binding_verified": mint_bound,
                "pumpfun_create_verified": create_ok,
                "validated_creation_slot": slot if isinstance(slot, int) and slot > 0 else None,
            })
            if not successful:
                record["conflict_reason"] = "TRANSACTION_META_ERROR"
            elif record["validated_creation_slot"] is None:
                record["conflict_reason"] = "POSITIVE_SLOT_REQUIRED"
            elif not mint_bound:
                record["conflict_reason"] = "MINT_BINDING_UNVERIFIED"
            elif not create_ok:
                record["conflict_reason"] = semantic_error
            else:
                record["qualification_status"] = "PUMPFUN_CREATE_EVENT_VERIFIED"
        record["evidence_identity"] = digest({key: value for key, value in record.items() if key != "evidence_identity"})
        limit = MAX_SUCCESS_RECORD_BYTES if record["qualification_status"] == "PUMPFUN_CREATE_EVENT_VERIFIED" else MAX_FAILURE_RECORD_BYTES
        if len(canonical_bytes(record)) > limit:
            raise ValueError("compact record bound exceeded")
        records.append(record)

    artifact = {
        "artifact_type": "DEV014_WATCHTOWER_CREATION_VALIDATION_PILOT",
        "version": 1,
        "provider": "HELIUS_MAINNET_RPC",
        "request_contract": {"method": "getTransaction", "max_requests": MAX_REQUESTS, "concurrency": 1, "retries": 0, "pagination": False, "fallback": False},
        "source": {"coverage_path": str(COVERAGE_PATH.relative_to(ROOT)), "coverage_sha256": coverage_hash},
        "selection": {"rule": "recent_creation_timestamp_desc_then_mint_asc", "candidate_count": 533, "frozen_allowlist": frozen},
        "records": records,
        "summary": {
            "request_count": len(records),
            "transaction_success_count": sum(record["transaction_success"] for record in records),
            "mint_binding_verified_count": sum(record["mint_binding_verified"] for record in records),
            "pumpfun_create_verified_count": sum(record["qualification_status"] == "PUMPFUN_CREATE_EVENT_VERIFIED" for record in records),
        },
        "retention": {"raw_transaction_retained": False, "success_record_max_bytes": MAX_SUCCESS_RECORD_BYTES, "failure_record_max_bytes": MAX_FAILURE_RECORD_BYTES, "aggregate_max_bytes": MAX_ARTIFACT_BYTES},
    }
    raw = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if len(raw.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise ValueError("aggregate artifact bound exceeded")
    args.output.write_text(raw)
    print(json.dumps({"output": str(args.output), "bytes": len(raw.encode("utf-8")), **artifact["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
