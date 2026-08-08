#!/usr/bin/env python3
"""Run the crash-safe OIP v2.1C bounded retry/failover experiment."""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.acquisition.transaction import (  # noqa: E402
    AcquisitionMetadata, AcquisitionResponse, SharedTransactionAcquisition,
    acquisition_scope,
)
from src.evidence.config import EvidenceConfig  # noqa: E402
from src.evidence.service import EvidencePlatform  # noqa: E402
from src.intelligence.bounded_retry_validation import (  # noqa: E402
    EXPERIMENT_ID, PHYSICAL_ATTEMPT_LIMIT, RETRYABLE_FAILURES,
    DurablePhysicalAttemptLedger, ExperimentTarget, PhysicalAttemptBudget,
    classify_attempt, construct_matched_cohorts, diagnostic_bytes, target_key,
)
from src.intelligence.migrated_coverage import census  # noqa: E402


FROZEN_SOURCE_ROWID = 1_615_500
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


def _configured_helius_url() -> str | None:
    if value := os.environ.get("HELIUS_RPC_URL"):
        return value
    supervisor = ROOT / "config/supervisor/supervisord.conf"
    if supervisor.exists():
        match = re.search(r'HELIUS_RPC_URL="([^"]+)"', supervisor.read_text())
        if match:
            return match.group(1)
    return None


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2, default=str)
        handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(temp, path)


def _clone_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["cp", "-c", str(source), str(target)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.copystat(source, target)
    except (OSError, subprocess.CalledProcessError):
        shutil.copy2(source, target)


def _dir_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.exists() else 0


def _db_metrics(path: Path) -> dict:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        counts = {name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in (
            "artifact_references", "normalized_evidence_records", "primitive_observations",
            "primitive_evidence_inputs",
        )}
        payloads = {
            "evidence_payload_bytes": int(conn.execute(
                "SELECT COALESCE(SUM(LENGTH(payload_json)),0) FROM normalized_evidence_records"
            ).fetchone()[0]),
            "primitive_payload_bytes": int(conn.execute(
                "SELECT COALESCE(SUM(LENGTH(subjects_json)+LENGTH(parameters_json)+"
                "LENGTH(output_payload_json)+LENGTH(missing_inputs_json)),0) "
                "FROM primitive_observations"
            ).fetchone()[0]),
        }
    return {"allocated_bytes": page_count * page_size, "file_bytes": path.stat().st_size,
            "counts": counts, **payloads}


def storage_snapshot(output: Path, db_path: Path) -> dict:
    return {
        "captured_at": time.time(), "evidence_db": _db_metrics(db_path),
        "artifact_store_bytes": _dir_size(output / "artifacts"),
        "attempt_artifact_bytes": _dir_size(output / "attempt_artifacts"),
        "attempt_telemetry_bytes": (output / "physical_attempts.jsonl").stat().st_size
            if (output / "physical_attempts.jsonl").exists() else 0,
        "replay_report_bytes": _dir_size(output / "reports"),
    }


def prepare(args) -> tuple[list[ExperimentTarget], dict]:
    failures = json.loads(args.failure_census.read_text())["failures"]
    coverage = census(args.production_db, args.source / "evidence.db",
                      max_source_rowid=FROZEN_SOURCE_ROWID)
    coverage_by_mint = {row.mint: row for row in coverage}
    targets, manifest = construct_matched_cohorts(failures, coverage_by_mint)
    manifest.update({
        "milestone": "OIP v2.1C", "source_commit": "f2864898",
        "source_failure_count": len(failures), "source_affected_launches": len({x["launch"] for x in failures}),
        "primary_provider": "helius_rpc", "failover_provider": "solana_public_rpc",
        "delayed_retry_seconds": args.delay_seconds,
        "retryable_failure_classes": sorted(RETRYABLE_FAILURES),
        "transaction_not_found_retryable": False,
        "production_interaction": False,
    })
    if args.output.exists():
        existing = json.loads((args.output / "experiment_manifest.json").read_text())
        if existing != manifest:
            raise RuntimeError("existing experiment manifest does not match deterministic plan")
        return targets, manifest
    args.output.mkdir(parents=True)
    _write_json(args.output / "experiment_manifest.json", manifest)
    _clone_file(args.source / "evidence.db", args.output / "evidence.db")
    for name in ("artifacts", "intake", "mirror_spool", "attempt_artifacts", "reports"):
        (args.output / name).mkdir(parents=True, exist_ok=True)
    _write_json(args.output / "storage_before.json", storage_snapshot(args.output, args.output / "evidence.db"))
    _write_json(args.output / "stage_checkpoint.json", {
        "experiment_id": EXPERIMENT_ID, "prepared": True, "acquisition_complete": False,
        "downstream_complete": False, "updated_at": time.time(),
    })
    return targets, manifest


def _store_attempt_artifact(root: Path, payload: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(payload).hexdigest()
    target = root / digest[:2] / digest[2:4] / f"{digest}.bin.gz"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        with gzip.open(temporary, "wb", compresslevel=6) as handle:
            handle.write(payload)
        os.replace(temporary, target)
    return digest, str(target.relative_to(root.parent))


def _raw_from_artifact(output: Path, relative: str) -> bytes:
    with gzip.open(output / relative, "rb") as handle:
        return handle.read()


async def execute_acquisition(args, targets: list[ExperimentTarget]) -> dict:
    ledger = DurablePhysicalAttemptLedger(args.output / "physical_attempts.jsonl")
    budget = PhysicalAttemptBudget(args.output / "experiment_checkpoint.json")
    rows_by_number = {int(row["physical_attempt_number"]): row for row in ledger.rows()}
    in_flight = budget.in_flight
    if in_flight:
        number = int(in_flight["physical_attempt_number"])
        if number not in rows_by_number:
            recovered_row = {
                **in_flight, "experiment_id": EXPERIMENT_ID,
                "attempt_id": in_flight.get("attempt_id"), "request_finished_at": time.time(),
                "latency_ms": None, "result_class": "TRANSPORT_ERROR",
                "http_status": None, "rpc_error_code": None,
                "provider_error": "PROCESS_INTERRUPTED_AFTER_ATTEMPT_RESERVATION",
                "transaction_found": False, "response_size_bytes": 0,
                "raw_artifact_digest": None, "raw_artifact_path": None,
                "terminal_for_target": in_flight.get("policy_cohort") == "NO_RETRY",
                "terminal_outcome": ("INTERRUPTED_IN_FLIGHT"
                                     if in_flight.get("policy_cohort") == "NO_RETRY" else None),
                "credits": 10 if in_flight.get("provider") == "helius_rpc" else None,
            }
            ledger.append(recovered_row)
        else:
            recovered_row = rows_by_number[number]
        budget.record_target_attempt(
            in_flight["target_key"],
            attempt_number=int(in_flight["attempt_number_for_target"]),
            result_class=recovered_row["result_class"],
            attempt_id=recovered_row["attempt_id"],
        )

    async with aiohttp.ClientSession() as session:
        client = SharedTransactionAcquisition(session)
        for target in targets:
            key = target_key(target)
            if key in budget.completed_target_keys:
                continue
            progress = budget.target_progress(key) or {}
            previous_attempt_id = progress.get("previous_attempt_id")
            previous_class = progress.get("previous_class")
            max_attempts = 1 if target.policy_cohort == "NO_RETRY" else 2
            attempts_done = int(progress.get("attempts", 0))
            if attempts_done and (previous_class == "SUCCESS"
                                  or previous_class not in RETRYABLE_FAILURES
                                  or attempts_done >= max_attempts):
                budget.complete_target(key)
                continue
            for attempt_number in range(attempts_done + 1, max_attempts + 1):
                if attempt_number == 2 and previous_class not in RETRYABLE_FAILURES:
                    break
                provider = "helius_rpc"
                endpoint_class = "PRIMARY"
                url = args.helius_rpc_url
                retry_reason = None; failover_reason = None
                delay_started = None; delay_ms = 0.0
                if attempt_number == 2 and target.policy_cohort == "DELAYED_RETRY":
                    retry_reason = previous_class
                    delay_started = time.perf_counter()
                    await asyncio.sleep(args.delay_seconds)
                    delay_ms = (time.perf_counter() - delay_started) * 1000
                elif attempt_number == 2 and target.policy_cohort == "EXISTING_FAILOVER":
                    provider = "solana_public_rpc"; endpoint_class = "EXISTING_FALLBACK"
                    url = PUBLIC_RPC; failover_reason = previous_class
                attempt_id = str(uuid.uuid4())
                started_at = time.time()
                context = {
                    "target_key": key, "attempt_id": attempt_id,
                    "target_signature": target.signature, "launch_id": target.launch,
                    "dependency_type": target.dependency_type, "policy_cohort": target.policy_cohort,
                    "attempt_number_for_target": attempt_number, "provider": provider,
                    "provider_endpoint_class": endpoint_class, "request_started_at": started_at,
                    "retry_reason": retry_reason, "failover_reason": failover_reason,
                    "previous_attempt_id": previous_attempt_id,
                }
                physical_number = budget.reserve(context)
                payload = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [
                    target.signature,
                    {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0},
                ]}
                with acquisition_scope(purpose=target.dependency_type.lower(), launch=target.launch,
                                       correlation_id=f"{EXPERIMENT_ID}:{key}"):
                    response = await client.request_once(
                        http_method="POST", url=url, timeout_seconds=args.timeout_seconds,
                        request_type="json_rpc", method="getTransaction", json_payload=payload,
                        retry_count=attempt_number - 1, acquisition_id=attempt_id,
                    )
                finished_at = time.time()
                result_class, provider_error, rpc_code = classify_attempt(response)
                raw = diagnostic_bytes(response)
                digest, artifact_path = _store_attempt_artifact(args.output / "attempt_artifacts", raw)
                terminal = result_class == "SUCCESS" or attempt_number == max_attempts or result_class not in RETRYABLE_FAILURES
                terminal_outcome = ("SUCCESS" if result_class == "SUCCESS" else
                                    "RETRY_EXHAUSTED" if terminal and target.policy_cohort == "DELAYED_RETRY" and attempt_number == 2 else
                                    "FAILOVER_EXHAUSTED" if terminal and target.policy_cohort == "EXISTING_FAILOVER" and attempt_number == 2 else
                                    result_class if terminal else None)
                ledger.append({
                    "experiment_id": EXPERIMENT_ID, "attempt_id": attempt_id,
                    "target_signature": target.signature, "launch_id": target.launch,
                    "dependency_type": target.dependency_type, "provider": provider,
                    "provider_endpoint_class": endpoint_class,
                    "attempt_number_for_target": attempt_number,
                    "policy_cohort": target.policy_cohort,
                    "request_started_at": started_at, "request_finished_at": finished_at,
                    "latency_ms": round(response.latency_ms, 3),
                    "physical_attempt_number": physical_number, "result_class": result_class,
                    "http_status": response.status, "rpc_error_code": rpc_code,
                    "provider_error": provider_error, "transaction_found": result_class == "SUCCESS",
                    "response_size_bytes": len(raw), "retry_reason": retry_reason,
                    "failover_reason": failover_reason, "previous_attempt_id": previous_attempt_id,
                    "terminal_for_target": terminal, "terminal_outcome": terminal_outcome,
                    "credits": 10 if provider == "helius_rpc" else None,
                    "retry_delay_ms": round(delay_ms, 3),
                    "raw_artifact_digest": digest, "raw_artifact_path": artifact_path,
                })
                budget.record_target_attempt(
                    key, attempt_number=attempt_number,
                    result_class=result_class, attempt_id=attempt_id,
                )
                if physical_number % 25 == 0:
                    print(json.dumps({
                        "physical_attempts": physical_number,
                        "hard_limit": PHYSICAL_ATTEMPT_LIMIT,
                        "completed_targets": len(budget.completed_target_keys),
                    }), flush=True)
                previous_attempt_id = attempt_id; previous_class = result_class
                if terminal:
                    break
            budget.complete_target(key)

    stage = json.loads((args.output / "stage_checkpoint.json").read_text())
    stage.update({"acquisition_complete": True, "physical_attempt_count": budget.count,
                  "acquisition_completed_at": time.time(), "updated_at": time.time()})
    _write_json(args.output / "stage_checkpoint.json", stage)
    return {"physical_attempt_count": budget.count, "completed_targets": len(budget.completed_target_keys)}


def _response_from_ledger(output: Path, row: dict) -> AcquisitionResponse:
    raw = _raw_from_artifact(output, row["raw_artifact_path"])
    data = json.loads(raw) if raw else None
    metadata = AcquisitionMetadata(
        acquisition_id=row["attempt_id"], correlation_id=f"{EXPERIMENT_ID}:{row['target_key'] if 'target_key' in row else row['policy_cohort']+':'+row['target_signature']}",
        purpose=row["dependency_type"].lower(), creator=None, launch=row["launch_id"],
        request_type="json_rpc", provider=row["provider"], method="getTransaction",
        page_number=None, cursor=None, timestamp=row["request_started_at"], cache_state="none",
        retry_count=int(row["attempt_number_for_target"]) - 1,
    )
    return AcquisitionResponse(
        status=row["http_status"], data=data, text=None, headers={}, metadata=metadata,
        latency_ms=float(row["latency_ms"] or 0), raw_body=raw,
        artifact_representation="EXACT_PROVIDER_ARTIFACT",
    )


def run_downstream(args) -> dict:
    checkpoint = json.loads((args.output / "stage_checkpoint.json").read_text())
    if checkpoint.get("downstream_complete"):
        return json.loads((args.output / "stage_telemetry.json").read_text())
    successes = [row for row in DurablePhysicalAttemptLedger(
        args.output / "physical_attempts.jsonl").rows() if row["result_class"] == "SUCCESS"]
    config = EvidenceConfig(
        platform_enabled=True, writer_enabled=True, queue_enabled=True,
        artifact_store_enabled=True, health_enabled=True, mirror_enabled=True,
        normalization_enabled=True, primitive_engine_enabled=True,
        database_path=args.output / "evidence.db", queue_path=args.output / "intake",
        artifact_path=args.output / "artifacts", mirror_spool_path=args.output / "mirror_spool",
        writer_batch_size=100, queue_max_messages=2_000, mirror_buffer_size=1_000,
    )
    platform = EvidencePlatform(config)
    platform.writer.primitive_engine = None
    platform.writer.start()
    timings = {"successful_acquisitions": len(successes)}
    try:
        mirror_started = time.perf_counter()
        for row in successes:
            response = _response_from_ledger(args.output, row)
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [
                row["target_signature"],
                {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0},
            ]}
            url = args.helius_rpc_url if row["provider"] == "helius_rpc" else PUBLIC_RPC
            if not platform.mirror.publish_nowait(response, http_method="POST", url=url, request_payload=payload):
                raise RuntimeError("mirror rejected a recovered acquisition")
        if not platform.mirror.drain(timeout=300):
            raise RuntimeError("mirror did not drain")
        timings["mirror_seconds"] = round(time.perf_counter() - mirror_started, 6)

        normalization_started = time.perf_counter()
        claimed = inserted = duplicates = failed = 0
        while True:
            batch = platform.writer.run_once()
            claimed += int(batch.get("claimed", 0)); inserted += int(batch.get("inserted", 0))
            duplicates += int(batch.get("duplicates", 0))
            failed += int(batch.get("failed", 0))
            if batch.get("claimed", 0) == 0:
                break
        timings["normalization_seconds"] = round(time.perf_counter() - normalization_started, 6)
        timings["normalization"] = {"claimed": claimed, "inserted_envelopes": inserted,
                                    "duplicate_envelopes": duplicates, "failed": failed}

        primitive_started = time.perf_counter()
        first = platform.primitive_engine.run_once()
        timings["primitive_first_seconds"] = round(time.perf_counter() - primitive_started, 6)
        primitive_started = time.perf_counter()
        second = platform.primitive_engine.run_once()
        timings["primitive_second_seconds"] = round(time.perf_counter() - primitive_started, 6)
        timings["primitive_first"] = first; timings["primitive_second"] = second
    finally:
        platform.writer.stop(); platform.mirror.stop()
    _write_json(args.output / "stage_telemetry.json", timings)
    checkpoint.update({"downstream_complete": True, "downstream_completed_at": time.time(),
                       "updated_at": time.time()})
    _write_json(args.output / "stage_checkpoint.json", checkpoint)
    _write_json(args.output / "storage_after_core.json", storage_snapshot(args.output, args.output / "evidence.db"))
    return timings


async def run(args) -> dict:
    targets, manifest = prepare(args)
    if args.prepare_only:
        return {"prepared": True, "manifest": manifest, "physical_attempts": 0}
    acquisition = await execute_acquisition(args, targets)
    downstream = run_downstream(args)
    result = {"prepared": True, "acquisition": acquisition, "downstream": downstream}
    _write_json(args.output / "runner_result.json", result)
    return result


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path,
        default=ROOT / "database/evidence_platform/oip_v2_1a_pilot")
    parser.add_argument("--output", type=Path,
        default=ROOT / "database/evidence_platform/oip_v2_1c_retry_failover")
    parser.add_argument("--production-db", type=Path,
        default=ROOT / "database/flex_complete_database.db")
    parser.add_argument("--failure-census", type=Path,
        default=ROOT / "docs/evidence_platform/oip_v2_1b_provider_failure_census.json")
    parser.add_argument("--helius-rpc-url", default=_configured_helius_url())
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if not args.helius_rpc_url and not args.prepare_only:
        raise SystemExit("HELIUS_RPC_URL is required")
    if not args.helius_rpc_url:
        args.helius_rpc_url = "https://mainnet.helius-rpc.com/"
    result = asyncio.run(run(args))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
