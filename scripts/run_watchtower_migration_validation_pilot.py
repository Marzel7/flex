#!/usr/bin/env python3
"""One-shot, ten-request Watchtower migration validation pilot.

This runner deliberately derives compact evidence in memory and never persists
provider response bodies or transaction data.  It has no retry, pagination, or
fallback path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/audits/dev014_watchtower_migration_provenance_recovery_contract_20261009.v1.json"
OUTPUT = ROOT / "docs/audits/dev014_watchtower_migration_validation_pilot_20261009.v1.json"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def event_semantics(logs: object) -> bool:
    text = " ".join(str(item) for item in (logs or []))
    lowered = text.lower()
    return (
        "Instruction: Migrate" in text
        and "Instruction: Buy" not in text
        and "Instruction: Sell" not in text
        and "MigrateBondingCurveCreator" not in text
        and any(pattern in lowered for pattern in ("initialize", "create_pool", "createpool", "initializepool", PUMPSWAP_PROGRAM.lower()))
    )


def mint_present(result: dict, mint: str) -> bool:
    message = ((result.get("transaction") or {}).get("message") or {})
    keys = message.get("accountKeys") or []
    normalized = {str(key.get("pubkey")) if isinstance(key, dict) else str(key) for key in keys}
    if mint in normalized:
        return True
    meta = result.get("meta") or {}
    balances = list(meta.get("preTokenBalances") or []) + list(meta.get("postTokenBalances") or [])
    return any(str(item.get("mint")) == mint for item in balances if isinstance(item, dict))


def request_once(url: str, signature: str) -> tuple[int | None, dict | None, str | None]:
    body = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "finalized"}]}
    request = urllib.request.Request(url, data=json.dumps(body, separators=(",", ":")).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return int(response.status), json.loads(response.read()), None
    except Exception as exc:  # exactly one attempt; retain only the exception class
        return None, None, type(exc).__name__


def local_slot(connection: sqlite3.Connection, signature: str, mint: str) -> int | None:
    row = connection.execute("SELECT migration_slot FROM token_analysis WHERE mint=? AND migration_tx=?", (mint, signature)).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--rpc-url-env", default="HELIUS_RPC_URL")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("PILOT_OUTPUT_ALREADY_EXISTS")
    url = os.environ.get(args.rpc_url_env)
    if not url or urlparse(url).hostname != "mainnet.helius-rpc.com":
        raise SystemExit("ESTABLISHED_RPC_AUTHORITY_UNAVAILABLE")
    contract = json.loads(CONTRACT.read_text())
    allowlist = contract["pilot"]["allowlist"]
    if len(allowlist) != 10 or len({row["signature"] for row in allowlist}) != 10:
        raise SystemExit("FROZEN_ALLOWLIST_INVALID")
    connection = sqlite3.connect(f"file:{args.core_db.resolve()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    provider = urlparse(url).hostname
    records = []
    for ordinal, item in enumerate(allowlist, start=1):
        mint, signature = str(item["mint"]), str(item["signature"])
        request_identity = digest({"contract": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(), "ordinal": ordinal, "signature": signature, "method": "getTransaction"})
        http_status, response, transport_failure = request_once(url, signature)
        local = local_slot(connection, signature, mint)
        if response is None:
            record = {"mint": mint, "migration_signature": signature, "request_identity": request_identity,
                      "provider": provider, "http_status": http_status, "status": "TRANSPORT_FAILURE",
                      "failure": transport_failure, "local_slot": local}
        elif response.get("error") is not None or response.get("result") is None:
            record = {"mint": mint, "migration_signature": signature, "request_identity": request_identity,
                      "provider": provider, "http_status": http_status, "status": "RPC_RESULT_UNAVAILABLE",
                      "failure": "RPC_ERROR_OR_NULL_RESULT", "local_slot": local}
        else:
            result = response["result"]
            meta = result.get("meta") or {}
            slot = result.get("slot")
            valid_slot = isinstance(slot, int) and slot > 0
            bound = mint_present(result, mint)
            event = event_semantics(meta.get("logMessages"))
            success = meta.get("err") is None and valid_slot and bound and event
            record = {"mint": mint, "migration_signature": signature, "request_identity": request_identity,
                      "provider": provider, "http_status": http_status, "status": "MIGRATION_EVENT_VERIFIED" if success else "EVENT_VALIDATION_FAILED",
                      "validated_slot": slot if valid_slot else None, "transaction_success": meta.get("err") is None,
                      "mint_binding_verified": bound, "migration_event_verified": event,
                      "local_slot": local, "retained_slot_agreement": local is not None and local == slot,
                      "conflict": None if local is None or local == slot else "RETAINED_SLOT_DISAGREEMENT"}
        record["evidence_identity"] = digest({k: record.get(k) for k in ("mint", "migration_signature", "request_identity", "status", "validated_slot", "conflict")})
        if len(json.dumps(record, separators=(",", ":")).encode()) > 1024:
            raise SystemExit("SUCCESS_OR_FAILURE_RECORD_BOUND_EXCEEDED")
        records.append(record)
    artifact = {"schema_version": 1, "artifact_type": "DEV014_WATCHTOWER_MIGRATION_VALIDATION_PILOT",
                "contract_sha256": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(), "provider": provider,
                "request_count": len(records), "raw_transaction_retention": False, "provider_response_retention": False,
                "records": records,
                "summary": {"transaction_success_count": sum(r.get("transaction_success") is True for r in records),
                            "mint_binding_verified": sum(r.get("mint_binding_verified") is True for r in records),
                            "migration_event_verified": sum(r["status"] == "MIGRATION_EVENT_VERIFIED" for r in records),
                            "retained_slot_agreement": sum(r.get("retained_slot_agreement") is True for r in records),
                            "event_identity_conflicts": sum(r.get("conflict") is not None for r in records)}}
    rendered = json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n"
    if len(rendered.encode()) >= 1_000_000:
        raise SystemExit("PILOT_ARTIFACT_BOUND_EXCEEDED")
    args.output.write_text(rendered)


if __name__ == "__main__":
    main()
