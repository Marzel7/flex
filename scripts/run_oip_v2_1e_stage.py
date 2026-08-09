#!/usr/bin/env python3
"""Run the crash-safe OIP v2.1E staged 1,000-attempt expansion."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_oip_v2_1c_retry_failover import (  # noqa: E402
    PUBLIC_RPC, _clone_file, _configured_helius_url, _store_attempt_artifact,
    _write_json, run_downstream, storage_snapshot,
)
from src.acquisition.transaction import SharedTransactionAcquisition, acquisition_scope  # noqa: E402
from src.intelligence.bounded_retry_validation import (  # noqa: E402
    PHYSICAL_ATTEMPT_LIMIT, RETRYABLE_FAILURES, DurablePhysicalAttemptLedger,
    PhysicalAttemptBudget, classify_attempt, diagnostic_bytes,
)
from src.intelligence.migrated_coverage import census  # noqa: E402
from src.intelligence.staged_coverage_expansion import CoverageTarget, construct_manifest  # noqa: E402

EXPERIMENT_ID = "OIP_V2_1E"
FROZEN_SOURCE_ROWID = 1_615_500
CHECKPOINTS = {100, 250, 500, 750, 1000}
STORAGE_UPPER_BYTES = 2_780_000_000


def target_key(target: CoverageTarget) -> str:
    return f"{target.manifest_position}:{target.signature}"


def coverage_summary(rows) -> dict:
    from collections import Counter
    return {"total_migrated_launches": len(rows), "states": dict(Counter(row.state for row in rows)),
        "reasons": dict(Counter(row.reason for row in rows)),
        "actionable_missing_dependencies": sum((not row.creation_transaction_present) +
            (not row.migration_transaction_present) for row in rows if row.creation_signature and row.migration_signature)}


def light_storage(output: Path) -> dict:
    def size(path: Path) -> int:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.exists() else 0
    disk = shutil.disk_usage(output)
    database = output / "evidence.db"
    return {"evidence_db_bytes": database.stat().st_size,
        "wal_bytes": database.with_name(database.name + "-wal").stat().st_size
            if database.with_name(database.name + "-wal").exists() else 0,
        "attempt_artifact_bytes": size(output / "attempt_artifacts"),
        "attempt_telemetry_bytes": (output / "physical_attempts.jsonl").stat().st_size
            if (output / "physical_attempts.jsonl").exists() else 0,
        "artifact_store_bytes": size(output / "artifacts"), "free_disk_bytes": disk.free}


def prepare(args) -> tuple[list[CoverageTarget], dict]:
    rows = census(args.production_db, args.source / "evidence.db", max_source_rowid=FROZEN_SOURCE_ROWID)
    targets, manifest = construct_manifest(rows)
    manifest.update({"source_commit": "a16add94", "frozen_source_rowid": FROZEN_SOURCE_ROWID,
        "coverage_before": coverage_summary(rows), "provider": "helius_rpc",
        "fallback": "solana_public_rpc", "maximum_physical_attempts": 1000,
        "expected_storage_range_bytes": [1_670_000_000, STORAGE_UPPER_BYTES],
        "production_writes": 0, "identity_prioritization": False})
    if args.output.exists():
        existing = json.loads((args.output / "experiment_manifest.json").read_text())
        if existing != manifest:
            raise RuntimeError("existing v2.1E manifest differs from deterministic plan")
        return targets, manifest
    args.output.mkdir(parents=True)
    _clone_file(args.source / "evidence.db", args.output / "evidence.db")
    for directory in ("artifacts", "attempt_artifacts", "intake", "mirror_spool", "reports", "checkpoints"):
        (args.output / directory).mkdir()
    _write_json(args.output / "experiment_manifest.json", manifest)
    _write_json(args.output / "storage_before.json", storage_snapshot(args.output, args.output / "evidence.db"))
    _write_json(args.output / "stage_checkpoint.json", {"experiment_id": EXPERIMENT_ID,
        "prepared": True, "acquisition_complete": False, "downstream_complete": False,
        "physical_attempt_count": 0, "updated_at": time.time()})
    return targets, manifest


def checkpoint(args, budget, ledger, position: int) -> None:
    rows = ledger.rows(); successes = sum(row["result_class"] == "SUCCESS" for row in rows)
    payload = {"physical_attempt_count": budget.count, "manifest_cursor": position,
        "successes": successes, "failures": len(rows) - successes,
        "retries": sum(row["attempt_number_for_target"] > 1 and row["provider"] == "helius_rpc" for row in rows),
        "failovers": sum(row["provider"] != "helius_rpc" for row in rows),
        "storage": light_storage(args.output), "captured_at": time.time()}
    _write_json(args.output / f"checkpoints/attempt_{budget.count:04d}.json", payload)
    raw_growth = payload["storage"]["attempt_artifact_bytes"] + payload["storage"]["attempt_telemetry_bytes"]
    if budget.count and raw_growth / budget.count * 1000 > STORAGE_UPPER_BYTES:
        raise RuntimeError("projected physical growth exceeds v2.1D upper planning bound")


async def acquire(args, targets: list[CoverageTarget]) -> dict:
    ledger = DurablePhysicalAttemptLedger(args.output / "physical_attempts.jsonl")
    budget = PhysicalAttemptBudget(args.output / "experiment_checkpoint.json")
    if budget.in_flight:
        raise RuntimeError("in-flight reservation requires forensic recovery before resume")
    async with aiohttp.ClientSession() as session:
        client = SharedTransactionAcquisition(session)
        for target in targets:
            if budget.count >= PHYSICAL_ATTEMPT_LIMIT:
                break
            key = target_key(target)
            if key in budget.completed_target_keys:
                continue
            progress = budget.target_progress(key) or {}
            previous_class = progress.get("previous_class")
            previous_attempt_id = progress.get("previous_attempt_id")
            attempts_done = int(progress.get("attempts", 0))
            for attempt_number in range(attempts_done + 1, 3):
                if budget.count >= PHYSICAL_ATTEMPT_LIMIT:
                    break
                if attempt_number == 2 and previous_class not in RETRYABLE_FAILURES:
                    break
                provider, endpoint, url = "helius_rpc", "PRIMARY", args.helius_rpc_url
                retry_reason = failover_reason = None
                if attempt_number == 2 and previous_class in {"PROVIDER_UNAVAILABLE", "RPC_ERROR"}:
                    provider, endpoint, url = "solana_public_rpc", "EXISTING_FALLBACK", PUBLIC_RPC
                    failover_reason = previous_class
                elif attempt_number == 2:
                    retry_reason = previous_class
                    await asyncio.sleep(args.delay_seconds)
                attempt_id = str(uuid.uuid4()); started = time.time()
                context = {"target_key": key, "attempt_id": attempt_id, "target_signature": target.signature,
                    "launch_id": target.launch, "dependency_type": target.dependency_type,
                    "manifest_position": target.manifest_position, "policy_cohort": "STAGED_EXPANSION",
                    "attempt_number_for_target": attempt_number, "provider": provider,
                    "provider_endpoint_class": endpoint, "request_started_at": started,
                    "retry_reason": retry_reason, "failover_reason": failover_reason,
                    "previous_attempt_id": previous_attempt_id}
                physical_number = budget.reserve(context)
                payload = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [target.signature,
                    {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0}]}
                with acquisition_scope(purpose=target.dependency_type.lower(), launch=target.launch,
                                       correlation_id=f"{EXPERIMENT_ID}:{key}"):
                    response = await client.request_once(http_method="POST", url=url,
                        timeout_seconds=args.timeout_seconds, request_type="json_rpc", method="getTransaction",
                        json_payload=payload, retry_count=attempt_number - 1, acquisition_id=attempt_id)
                result_class, provider_error, rpc_code = classify_attempt(response)
                raw = diagnostic_bytes(response)
                digest, artifact_path = _store_attempt_artifact(args.output / "attempt_artifacts", raw)
                terminal = result_class == "SUCCESS" or result_class not in RETRYABLE_FAILURES or attempt_number == 2
                ledger.append({**context, "experiment_id": EXPERIMENT_ID,
                    "request_finished_at": time.time(), "latency_ms": round(response.latency_ms, 3),
                    "physical_attempt_number": physical_number, "result_class": result_class,
                    "http_status": response.status, "rpc_error_code": rpc_code, "provider_error": provider_error,
                    "transaction_found": result_class == "SUCCESS", "response_size_bytes": len(raw),
                    "terminal_for_target": terminal, "terminal_outcome": result_class if terminal else None,
                    "credits": 10 if provider == "helius_rpc" else None,
                    "raw_artifact_digest": digest, "raw_artifact_path": artifact_path})
                budget.record_target_attempt(key, attempt_number=attempt_number,
                                             result_class=result_class, attempt_id=attempt_id)
                previous_class, previous_attempt_id = result_class, attempt_id
                if physical_number in CHECKPOINTS or physical_number % 25 == 0:
                    checkpoint(args, budget, ledger, target.manifest_position)
                    print(json.dumps({"physical_attempts": physical_number, "successes": sum(
                        row["result_class"] == "SUCCESS" for row in ledger.rows())}), flush=True)
                if args.stop_after_attempts and physical_number >= args.stop_after_attempts:
                    return {"physical_attempt_count": budget.count,
                            "completed_targets": len(budget.completed_target_keys), "paused": True}
                if terminal:
                    break
            budget.complete_target(key)
    stage = json.loads((args.output / "stage_checkpoint.json").read_text())
    stage.update({"acquisition_complete": True, "physical_attempt_count": budget.count,
                  "acquisition_completed_at": time.time(), "updated_at": time.time()})
    _write_json(args.output / "stage_checkpoint.json", stage)
    return {"physical_attempt_count": budget.count, "completed_targets": len(budget.completed_target_keys)}


async def run(args):
    targets, manifest = prepare(args)
    if args.prepare_only:
        return {"prepared": True, "target_count": len(targets), "maximum_attempts": 1000}
    acquisition = await acquire(args, targets)
    if acquisition.get("paused"):
        return {"manifest": {key: value for key, value in manifest.items() if key != "targets"},
                "acquisition": acquisition, "downstream": "PAUSED_FOR_SAFETY_CHECKPOINT"}
    downstream = run_downstream(args)
    result = {"manifest": {key: value for key, value in manifest.items() if key != "targets"},
              "acquisition": acquisition, "downstream": downstream}
    _write_json(args.output / "runner_result.json", result)
    return result


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "database/evidence_platform/oip_v2_1c_retry_failover")
    parser.add_argument("--output", type=Path, default=ROOT / "database/evidence_platform/oip_v2_1e_stage_1000")
    parser.add_argument("--production-db", type=Path, default=ROOT / "database/flex_complete_database.db")
    parser.add_argument("--helius-rpc-url", default=_configured_helius_url())
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--stop-after-attempts", type=int, default=0)
    args = parser.parse_args()
    if not args.helius_rpc_url and not args.prepare_only:
        raise SystemExit("HELIUS_RPC_URL is required")
    if not args.helius_rpc_url:
        args.helius_rpc_url = "https://mainnet.helius-rpc.com/"
    result = asyncio.run(run(args)); print(json.dumps(result, indent=2, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
