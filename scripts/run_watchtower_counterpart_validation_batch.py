#!/usr/bin/env python3
"""Run the single frozen DEV-014 counterpart-validation batch (max ten RPC calls)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
RECONCILIATION = ROOT / "docs/audits/dev014_watchtower_creation_migration_reconciliation_20261009.v1.json"
CREATION = ROOT / "docs/audits/dev014_watchtower_creation_validation_pilot_20261009.v1.json"
MIGRATION = ROOT / "docs/audits/dev014_watchtower_migration_validation_pilot_20261009.v1.json"
OUTPUT = ROOT / "docs/audits/dev014_watchtower_counterpart_validation_batch_20261009.v1.json"
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
MAX_REQUESTS = 10
MAX_RECORD_BYTES = 1024
MAX_ARTIFACT_BYTES = 1_000_000


def encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def account_values(result: dict[str, Any]) -> set[str]:
    keys = ((result.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    return {item if isinstance(item, str) else item.get("pubkey") for item in keys if isinstance(item, (str, dict)) and (isinstance(item, str) or isinstance(item.get("pubkey"), str))}


def mint_present(result: dict[str, Any], mint: str) -> bool:
    if mint in account_values(result):
        return True
    meta = result.get("meta") or {}
    balances = list(meta.get("preTokenBalances") or []) + list(meta.get("postTokenBalances") or [])
    return any(isinstance(item, dict) and item.get("mint") == mint for item in balances)


def create_semantics(result: dict[str, Any]) -> tuple[bool, str | None]:
    if PUMPFUN_PROGRAM not in account_values(result):
        return False, "PUMPFUN_PROGRAM_ABSENT"
    text = " ".join(item for item in ((result.get("meta") or {}).get("logMessages") or []) if isinstance(item, str)).lower()
    if PUMPSWAP_PROGRAM.lower() in text or "instruction: migrate" in text or "migratebondingcurvecreator" in text:
        return False, "MIGRATION_EVENT_SUBSTITUTION_REJECTED"
    return (True, None) if "instruction: create" in text else (False, "PUMPFUN_CREATE_SEMANTICS_MISSING")


def migration_semantics(result: dict[str, Any]) -> tuple[bool, str | None]:
    text = " ".join(item for item in ((result.get("meta") or {}).get("logMessages") or []) if isinstance(item, str))
    lower = text.lower()
    if "Instruction: Migrate" not in text:
        return False, "PUMPFUN_MIGRATE_SEMANTICS_MISSING"
    if "Instruction: Buy" in text or "Instruction: Sell" in text or "MigrateBondingCurveCreator" in text:
        return False, "MIGRATION_EVENT_AMBIGUOUS"
    return (True, None) if any(value in lower for value in ("initialize", "create_pool", "createpool", "initializepool", PUMPSWAP_PROGRAM.lower())) else (False, "PUMPSWAP_SEMANTICS_MISSING")


def request_once(endpoint: str, signature: str) -> tuple[int | None, dict[str, Any] | None, str | None]:
    body = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "finalized"}]}
    try:
        request = Request(endpoint, data=encode(body), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode()), None
    except Exception as exc:  # one attempt only; retain no response content
        return None, None, type(exc).__name__


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit("COUNTERPART_OUTPUT_ALREADY_EXISTS")
    endpoint = os.environ.get("HELIUS_RPC_URL") or os.environ.get("HELIUS_ENDPOINT")
    if not endpoint or urlparse(endpoint).hostname != "mainnet.helius-rpc.com":
        raise SystemExit("AUTHORITATIVE_HELIUS_ENDPOINT_REQUIRED")
    reconciliation = json.loads(RECONCILIATION.read_text())
    allowlist = reconciliation["next_batch"]["allowlist"]
    if len(allowlist) != MAX_REQUESTS or len({item["candidate_signature"] for item in allowlist}) != MAX_REQUESTS or len({item["request_identity"] for item in allowlist}) != MAX_REQUESTS:
        raise SystemExit("FROZEN_ALLOWLIST_INVALID")
    creations = {item["mint"]: item for item in json.loads(CREATION.read_text())["records"]}
    migrations = {item["mint"]: item for item in json.loads(MIGRATION.read_text())["records"]}
    records = []
    for item in allowlist:  # sequential by design: one request per frozen signature
        mint, event_type, signature = item["mint"], item["missing_event_type"], item["candidate_signature"]
        counterpart = creations[mint] if event_type == "MIGRATION" else migrations[mint]
        existing_signature = counterpart["creation_signature"] if event_type == "MIGRATION" else counterpart["migration_signature"]
        existing_slot = counterpart["validated_creation_slot"] if event_type == "MIGRATION" else counterpart["validated_slot"]
        status, body, transport_error = request_once(endpoint, signature)
        record: dict[str, Any] = {
            "mint": mint, "request_identity": item["request_identity"], "provider": "HELIUS_MAINNET_RPC", "http_status": status,
            "creation_signature": existing_signature if event_type == "MIGRATION" else signature,
            "creation_slot": existing_slot if event_type == "MIGRATION" else None,
            "migration_signature": signature if event_type == "MIGRATION" else existing_signature,
            "migration_slot": None if event_type == "MIGRATION" else existing_slot,
            "counterpart_evidence_identity": counterpart["evidence_identity"], "new_event_type": event_type,
            "new_event_validated": False, "chronology_classification": "VALIDATION_FAILED", "missing_ordering_requirement": None,
            "conflict_reason": None,
        }
        result = body.get("result") if isinstance(body, dict) else None
        if transport_error:
            record["conflict_reason"] = f"TRANSPORT_{transport_error}"
        elif status != 200 or not isinstance(body, dict) or body.get("error") is not None or not isinstance(result, dict):
            record["conflict_reason"] = "RPC_RESULT_UNAVAILABLE"
        else:
            meta, slot = result.get("meta") or {}, result.get("slot")
            valid = meta.get("err") is None and isinstance(slot, int) and slot > 0 and mint_present(result, mint)
            semantic_ok, semantic_failure = migration_semantics(result) if event_type == "MIGRATION" else create_semantics(result)
            if not valid:
                record["conflict_reason"] = "TRANSACTION_OR_MINT_OR_SLOT_INVALID"
            elif not semantic_ok:
                record["conflict_reason"] = semantic_failure
            else:
                record["new_event_validated"] = True
                if event_type == "MIGRATION":
                    record["migration_slot"] = slot
                else:
                    record["creation_slot"] = slot
                if record["creation_signature"] == record["migration_signature"]:
                    record["chronology_classification"] = "EVENT_IDENTITY_CONFLICT"
                    record["conflict_reason"] = "CREATION_MIGRATION_SIGNATURE_NOT_DISTINCT"
                elif record["creation_slot"] < record["migration_slot"]:
                    record["chronology_classification"] = "CHRONOLOGY_QUALIFIED"
                elif record["creation_slot"] == record["migration_slot"]:
                    record["chronology_classification"] = "SAME_SLOT_ORDER_UNRESOLVED"
                    record["missing_ordering_requirement"] = "independent transaction-index or block-order evidence under a separately qualified rule"
                else:
                    record["chronology_classification"] = "EVENT_IDENTITY_CONFLICT"
                    record["conflict_reason"] = "CREATION_SLOT_NOT_STRICTLY_BEFORE_MIGRATION_SLOT"
        record["evidence_identity"] = digest({key: value for key, value in record.items() if key != "evidence_identity"})
        if len(encode(record)) > MAX_RECORD_BYTES:
            raise ValueError("record bound exceeded")
        records.append(record)
    same_slot_pair = next(pair for pair in reconciliation["pairs"] if pair.get("chronology") == "SAME_SLOT_ORDER_UNRESOLVED")
    artifact = {
        "artifact_type": "DEV014_WATCHTOWER_COUNTERPART_VALIDATION_BATCH", "version": 1, "provider": "HELIUS_MAINNET_RPC",
        "request_contract": {"method": "getTransaction", "max_requests": MAX_REQUESTS, "concurrency": 1, "retries": 0, "pagination": False, "fallback": False, "block_requests": 0},
        "source": {"reconciliation_sha256": hashlib.sha256(RECONCILIATION.read_bytes()).hexdigest()},
        "same_slot_assessment": {"mint": same_slot_pair["mint"], "creation_signature": same_slot_pair["creation_signature"], "migration_signature": same_slot_pair["migration_signature"], "slot": same_slot_pair["creation_slot"], "retained_ordering_source": False, "rule": "STRICT_SLOT_INEQUALITY_REQUIRED", "classification": "SAME_SLOT_ORDER_UNRESOLVED"},
        "records": records,
        "summary": {"request_count": len(records), "counterpart_validations": sum(record["new_event_validated"] for record in records), "both_events_qualified": sum(record["new_event_validated"] for record in records), "chronology_qualified": sum(record["chronology_classification"] == "CHRONOLOGY_QUALIFIED" for record in records), "same_slot_cases": sum(record["chronology_classification"] == "SAME_SLOT_ORDER_UNRESOLVED" for record in records) + 1, "event_identity_conflicts": sum(record["chronology_classification"] == "EVENT_IDENTITY_CONFLICT" for record in records)},
        "retention": {"raw_transaction_retained": False, "raw_block_retained": False, "aggregate_max_bytes": MAX_ARTIFACT_BYTES},
    }
    raw = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if len(raw.encode()) > MAX_ARTIFACT_BYTES:
        raise ValueError("aggregate bound exceeded")
    OUTPUT.write_text(raw)
    print(json.dumps({"output": str(OUTPUT), "bytes": len(raw.encode()), **artifact["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
