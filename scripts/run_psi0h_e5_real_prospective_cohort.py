#!/usr/bin/env python3
"""PSI0H-E5 bounded real prospective cohort runner.

Execution is single-shot and fail-closed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse
from src.evidence.contracts.psi0h_census_transaction_adapter import collect_census_transactions
from src.evidence.contracts.psi0h_e5_real_prospective_cohort import (
    MAX_MIGRATIONS,
    MAX_REQUESTS,
    E2_ADAPTER_SHA256,
    SCHEMA_VERSION,
    Psi0hE5PreflightError,
    build_e5_real_prospective_preflight,
    verify_e5_preflight,
)
from src.evidence.contracts.psi0h_real_cohort_execution import (
    Psi0hRealCohortExecutionError,
    SCHEMA_VERSION as REAL_COHORT_EXEC_SCHEMA_VERSION,
    build_real_cohort_authorization,
    execute_real_cohort_once,
    verify_real_cohort_authorization,
)
from src.evidence.contracts.psi0h_prospective_derivation import qualify_prospective_derivation
from src.evidence.contracts.psi0h_real_run_preflight import SCHEMA_VERSION as E3_SCHEMA_VERSION


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/audits/psi0h_e5_real_prospective_cohort_preflight.json"
E4_ARTIFACT = ROOT / "docs/audits/psi0h_e4_live_census_preflight.json"
E3_ARTIFACT = ROOT / "docs/audits/psi0h_e3_immutable_real_run_preflight_qualification.json"
RUN_ID = "psi0h-e5-real-prospective"
AUTHORIZATION_ID = "psi0h-e5-real-prospective-auth"
REAL_RUN_AUTH_ENV = "PSI0H_E5_REAL_RUN_AUTHORIZED"
REAL_RUN_AUTH_VALUE = "1"
REAL_PROVIDER_ENDPOINT_ENV = "HELIUS_RPC_URL"


class Psi0hE5ExecutionError(RuntimeError):
    pass


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest_file(path: Path) -> str:
    return _digest(_load_json(path))


def _read_append_rows(*, census_path: Path, start_offset: int) -> list[dict]:
    if not census_path.is_file():
        raise Psi0hE5ExecutionError("PSI0H_E5_CENSUS_MISSING")
    size = census_path.stat().st_size
    if size < start_offset:
        raise Psi0hE5ExecutionError("PSI0H_E5_CENSUS_HIGHWATER_DRIFT")
    with census_path.open("rb") as handle:
        handle.seek(start_offset)
        raw = handle.read()
    if not raw:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Psi0hE5ExecutionError("PSI0H_E5_CENSUS_INVALID_ENCODING") from exc
    events = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Psi0hE5ExecutionError("PSI0H_E5_CENSUS_PARSE_ERROR") from exc
        if not isinstance(row, dict):
            raise Psi0hE5ExecutionError("PSI0H_E5_CENSUS_ROW_INVALID")
        if row.get("event_type") != "MIGRATION":
            continue
        events.append(row)
    return events


def _read_windowed_rows(
    *, census_path: Path, start_offset: int, interval_start: int, interval_end: int,
) -> list[dict]:
    rows = []
    for row in _read_append_rows(census_path=census_path, start_offset=start_offset):
        event_id = row.get("event_id")
        event_type = row.get("event_type")
        signature = row.get("signature")
        mint = row.get("mint")
        receive_utc_ns = row.get("receive_utc_ns")
        if not all(
            isinstance(value, str) and value
            for value in (event_id, event_type, signature, mint)
        ):
            raise Psi0hE5ExecutionError("PSI0H_E5_CENSUS_ROW_INVALID")
        if event_type != "MIGRATION":
            continue
        if not isinstance(receive_utc_ns, int):
            raise Psi0hE5ExecutionError("PSI0H_E5_CENSUS_ROW_INVALID")
        event_time = row.get("event_time")
        if not isinstance(event_time, int):
            event_time = receive_utc_ns // 1_000_000_000
        if not (interval_start <= event_time <= interval_end):
            continue
        rows.append({
            "event_id": event_id,
            "event_type": event_type,
            "receive_utc_ns": receive_utc_ns,
            "signature": signature,
            "mint": mint,
        })
    rows.sort(key=lambda row: (row["receive_utc_ns"], row["signature"], row["mint"]))
    if len(rows) > MAX_MIGRATIONS:
        return rows[:MAX_MIGRATIONS]
    return rows


def _make_transport(endpoint: str) -> Callable[[str, str], AcquisitionResponse]:
    url = endpoint.strip()
    if not url:
        raise Psi0hE5ExecutionError("PSI0H_E5_PROVIDER_URL_MISSING")
    if any(char in url for char in [" ", "\n", "\t"]):
        raise Psi0hE5ExecutionError("PSI0H_E5_PROVIDER_URL_INVALID")

    def _transport(signature: str, mint: str) -> AcquisitionResponse:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        }
        raw_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            url=url,
            data=raw_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started_at = time.time()
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
                status = response.status
                headers = dict(response.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            headers = dict(exc.headers)
            metadata = AcquisitionMetadata(
                acquisition_id=str(uuid4()), correlation_id=str(uuid4()),
                purpose="psi0h-e5-real", creator=None, launch=mint,
                request_type="json_rpc", provider="solana-rpc", method="getTransaction",
                page_number=None, cursor=None, timestamp=started_at,
                cache_state="miss", retry_count=0,
            )
            return AcquisitionResponse(
                status=status, data=None, text=None, headers=headers, metadata=metadata,
                latency_ms=(time.time() - started_at) * 1000, raw_body=raw,
                artifact_representation="EXACT_PROVIDER_ARTIFACT", error=None,
            )
        except Exception as exc:  # pragma: no cover
            metadata = AcquisitionMetadata(
                acquisition_id=str(uuid4()), correlation_id=str(uuid4()),
                purpose="psi0h-e5-real", creator=None, launch=mint,
                request_type="json_rpc", provider="solana-rpc", method="getTransaction",
                page_number=None, cursor=None, timestamp=started_at,
                cache_state="miss", retry_count=0,
            )
            return AcquisitionResponse(
                status=None, data=None, text=None, headers={},
                metadata=metadata, latency_ms=(time.time() - started_at) * 1000,
                raw_body=None, artifact_representation="EXACT_PROVIDER_ARTIFACT", error=exc,
            )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            metadata = AcquisitionMetadata(
                acquisition_id=str(uuid4()), correlation_id=str(uuid4()),
                purpose="psi0h-e5-real", creator=None, launch=mint,
                request_type="json_rpc", provider="solana-rpc", method="getTransaction",
                page_number=None, cursor=None, timestamp=started_at,
                cache_state="miss", retry_count=0,
            )
            return AcquisitionResponse(
                status=status, data=None, text=None, headers=headers, metadata=metadata,
                latency_ms=(time.time() - started_at) * 1000, raw_body=raw,
                artifact_representation="EXACT_PROVIDER_ARTIFACT", error=exc,
            )
        metadata = AcquisitionMetadata(
            acquisition_id=str(uuid4()), correlation_id=str(uuid4()),
            purpose="psi0h-e5-real", creator=None, launch=mint,
            request_type="json_rpc", provider="solana-rpc", method="getTransaction",
            page_number=None, cursor=None, timestamp=started_at,
            cache_state="miss", retry_count=0,
        )
        return AcquisitionResponse(
            status=status, data=parsed, text=None, headers=headers, metadata=metadata,
            latency_ms=(time.time() - started_at) * 1000, raw_body=raw,
            artifact_representation="EXACT_PROVIDER_ARTIFACT", error=None,
        )

    return _transport


def _build_run_preflight(*, e4_artifact: Path, e3_artifact: Path) -> dict[str, Any]:
    e4_payload = _load_json(e4_artifact)
    e3_payload = _load_json(e3_artifact)
    if e4_payload.get("status") != "PASS" or e4_payload.get("preflight") is None:
        raise Psi0hE5ExecutionError("PSI0H_E5_E4_ARTIFACT_STATUS_INVALID")
    if e3_payload.get("status") != "PASS":
        raise Psi0hE5ExecutionError("PSI0H_E5_E3_ARTIFACT_STATUS_INVALID")

    root = Path(tempfile.mkdtemp(prefix="psi0h-e5-"))
    preflight = build_e5_real_prospective_preflight(
        run_id=RUN_ID,
        e4_artifact=e4_artifact,
        e4_preflight=e4_payload,
        e3_artifact=e3_artifact,
        e3_artifact_digest=_digest_file(e3_artifact),
        staging_directory=root / "staging",
        output_directory=root / "output",
        consumption_directory=root / "consumption",
    )
    verify_e5_preflight(preflight)

    result = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-E5",
        "status": "PASS",
        "run_id": RUN_ID,
        "e3_run_preflight_schema_version": E3_SCHEMA_VERSION,
        "artifact_contract": "src/evidence/contracts/psi0h_e5_real_prospective_cohort.py",
        "artifact_bound_e2_adapter_sha256": E2_ADAPTER_SHA256,
        "e3_artifact": str(e3_artifact),
        "e4_artifact": str(e4_artifact),
        "preflight": preflight.__dict__,
        "runtime_boundaries": {
            "max_migrations": MAX_MIGRATIONS,
            "max_provider_requests": MAX_REQUESTS,
            "requests_per_migration": 1,
            "retries_allowed": 0,
            "pagination_enabled": False,
            "failover_enabled": False,
        },
        "scope": {
            "source_read": False,
            "provider_access": False,
            "service_changes": False,
            "comparison": False,
            "monitoring": False,
            "activation": False,
        },
        "fixture_only": True,
    }
    return result


def _build_execution_authorization(preflight_obj: dict[str, Any]):
    preflight = preflight_obj["preflight"]
    authorization = build_real_cohort_authorization(
        authorization_id=AUTHORIZATION_ID,
        run_id=preflight["run_id"],
        source_id=preflight["source_id"],
        source_kind=preflight["source_kind"],
        interval_start=preflight["interval_start"],
        interval_end=preflight["interval_end"],
        cutoff=preflight["cutoff"],
        maximum_envelopes=preflight["maximum_migrations"],
        maximum_primitives=MAX_REQUESTS,
        maximum_provider_requests=preflight["maximum_provider_requests"],
        provider_access_allowed=True,
        service_changes_allowed=False,
        isolated_output_directory=preflight["output_directory"],
        collector_contract_digest=preflight["e2_adapter_digest"],
    )
    if authorization.maximum_provider_requests != preflight["maximum_provider_requests"]:
        raise Psi0hE5ExecutionError("PSI0H_E5_AUTHORIZATION_BINDING_DRIFT")
    if authorization.source_kind != preflight["source_kind"]:
        raise Psi0hE5ExecutionError("PSI0H_E5_AUTHORIZATION_BINDING_DRIFT")
    if authorization.run_id != preflight["run_id"]:
        raise Psi0hE5ExecutionError("PSI0H_E5_AUTHORIZATION_BINDING_DRIFT")
    if authorization.collector_contract_digest != preflight["e2_adapter_digest"]:
        raise Psi0hE5ExecutionError("PSI0H_E5_AUTHORIZATION_BINDING_DRIFT")
    if any((
        authorization.comparison_allowed,
        authorization.alerts_allowed,
        authorization.monitoring_allowed,
        authorization.activation_allowed,
    )):
        raise Psi0hE5ExecutionError("PSI0H_E5_AUTHORIZATION_SCOPE_EXPANDED")
    verify_real_cohort_authorization(authorization)
    return authorization


def _empty_execution_result(*, authorization, preflight: dict[str, Any]) -> dict[str, Any]:
    qualification = qualify_prospective_derivation(
        cutoff=authorization.cutoff,
        interval_start=authorization.interval_start,
        interval_end=authorization.interval_end,
        envelopes=(),
        evidence_rows=(),
        primitive_rows=(),
        maximum_primitives=authorization.maximum_primitives,
    )
    result = {
        "schema_version": REAL_COHORT_EXEC_SCHEMA_VERSION,
        "status": qualification["status"],
        "authorization_id": authorization.authorization_id,
        "run_id": authorization.run_id,
        "authorization_digest": authorization.authorization_digest,
        "source_id": authorization.source_id,
        "source_kind": authorization.source_kind,
        "provider_request_count": 0,
        "qualification": qualification,
        "comparison_performed": False,
        "alerts_emitted": 0,
        "monitoring_activated": False,
        "activation_authority": False,
        "source_identity": {
            "census_path": preflight["census_path"],
            "census_device": preflight["census_device"],
            "census_inode": preflight["census_inode"],
        },
        "census_snapshot": {
            "size_bytes": preflight["census_size_bytes"],
            "mtime_ns": preflight["census_mtime_ns"],
            "start_offset": preflight["census_start_offset"],
        },
    }
    result["artifact_digest"] = _digest(result)
    return result


def run(
    *, output: Path, e4_artifact: Path, e3_artifact: Path,
    execute: bool = False, provider_url: str | None = None,
) -> dict[str, Any]:
    e4_artifact = e4_artifact.resolve()
    e3_artifact = e3_artifact.resolve()
    if not e4_artifact.is_file() or not e3_artifact.is_file():
        raise Psi0hE5ExecutionError("PSI0H_E5_SOURCE_ARTIFACT_MISSING")

    result = _build_run_preflight(e4_artifact=e4_artifact, e3_artifact=e3_artifact)
    preflight = result["preflight"]

    if preflight["source_read_authorized"] or preflight["provider_access_authorized"]:
        raise Psi0hE5ExecutionError("PSI0H_E5_SOURCE_OR_PROVIDER_AUTH_MISMATCH")
    if preflight["service_changes_authorized"] or preflight["comparison_authorized"]:
        raise Psi0hE5ExecutionError("PSI0H_E5_NON_EXECUTION_SCOPE_PRESENT")
    if preflight["monitoring_authorized"] or preflight["activation_authorized"]:
        raise Psi0hE5ExecutionError("PSI0H_E5_NON_EXECUTION_SCOPE_PRESENT")
    if preflight["maximum_migrations"] != MAX_MIGRATIONS or preflight["maximum_provider_requests"] != MAX_REQUESTS:
        raise Psi0hE5ExecutionError("PSI0H_E5_BOUNDARY_MISMATCH")

    e4_payload = _load_json(e4_artifact)
    e4_preflight = e4_payload.get("preflight") or {}
    if e4_payload.get("preflight_digest") != preflight["e4_preflight_digest"]:
        raise Psi0hE5ExecutionError("PSI0H_E5_E4_PRELIGHT_BINDING_DRIFT")
    if preflight["census_path"] != str(e4_preflight.get("census_path", "")):
        raise Psi0hE5ExecutionError("PSI0H_E5_E4_BINDING_DRIFT")

    result["migration_window"] = {
        "start_utc": preflight["interval_start"],
        "end_utc": preflight["interval_end"],
        "cutoff_utc": preflight["cutoff"],
    }
    result["migrations_selected"] = 0
    result["provider_request_count"] = 0
    result["fixture_only"] = False
    result["execution_status"] = "READY_FOR_AUTHORIZATION"
    result["real_run_authorized"] = False

    if not execute:
        result["artifact_digest"] = _digest(result)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
        return result

    if os.environ.get(REAL_RUN_AUTH_ENV, "") != REAL_RUN_AUTH_VALUE:
        raise Psi0hE5ExecutionError("PSI0H_E5_REAL_RUN_NOT_AUTHORIZED")
    endpoint = (provider_url or os.environ.get(REAL_PROVIDER_ENDPOINT_ENV, "")).strip()
    if not endpoint:
        raise Psi0hE5ExecutionError("PSI0H_E5_PROVIDER_URL_MISSING")

    events = _read_windowed_rows(
        census_path=Path(preflight["census_path"]),
        start_offset=preflight["census_start_offset"],
        interval_start=preflight["interval_start"],
        interval_end=preflight["interval_end"],
    )
    if len(events) > preflight["maximum_migrations"]:
        raise Psi0hE5ExecutionError("PSI0H_E5_MIGRATION_CEILING_EXCEEDED")
    if len(events) == MAX_MIGRATIONS and preflight["maximum_migrations"] == MAX_MIGRATIONS:
        result["hard_stop_reason"] = "MIGRATION_CEILING"

    staging_dir = Path(preflight["staging_directory"])
    output_dir = Path(preflight["output_directory"])
    consumption_dir = Path(preflight["consumption_directory"])

    for directory in (staging_dir, output_dir, consumption_dir):
        if directory.exists():
            raise Psi0hE5ExecutionError("PSI0H_E5_DESTINATION_REUSED")
        parent = directory.parent
        if parent.exists() is False:
            raise Psi0hE5ExecutionError("PSI0H_E5_DESTINATION_PARENT_MISSING")

    consumption_dir.mkdir()

    authorization = _build_execution_authorization(result)
    transport = _make_transport(endpoint)

    def collector(_: Any) -> dict[str, Any]:
        result_rows = collect_census_transactions(
            events=events,
            interval_start=preflight["interval_start"],
            interval_end=preflight["interval_end"],
            staging_root=staging_dir,
            transport=transport,
        )
        if result_rows["provider_request_count"] > preflight["maximum_provider_requests"]:
            raise Psi0hE5ExecutionError("PSI0H_E5_PROVIDER_CEILING_EXCEEDED")
        return {
            "envelopes": result_rows["envelopes"],
            "evidence_rows": result_rows["evidence_rows"],
            "primitive_rows": result_rows["primitive_rows"],
            "provider_request_count": result_rows["provider_request_count"],
        }

    try:
        if not events:
            execution = _empty_execution_result(authorization=authorization, preflight=preflight)
        else:
            execution = execute_real_cohort_once(
                authorization=authorization,
                consumption_directory=consumption_dir,
                collector=collector,
            )
    except (RuntimeError, OSError) as exc:
        raise Psi0hE5ExecutionError(f"PSI0H_E5_REAL_RUN_STOPPED:{exc}") from exc

    if execution["provider_request_count"] > preflight["maximum_provider_requests"]:
        raise Psi0hE5ExecutionError("PSI0H_E5_PROVIDER_CEILING_EXCEEDED")
    if execution["provider_request_count"] != len(events):
        raise Psi0hE5ExecutionError("PSI0H_E5_REQUESTS_PER_MIGRATION_BREACH")

    result.update({
        "execution_status": "COMPLETED",
        "real_run_authorized": True,
        "provider_request_count": execution["provider_request_count"],
        "migration_count": len(events),
        "migrations_selected": len(events),
        "provider_url": endpoint,
        "source_id": preflight["source_id"],
        "source_kind": preflight["source_kind"],
        "execution": execution,
        "e4_preflight_digest": preflight["e4_preflight_digest"],
        "e3_artifact_digest": preflight["e3_artifact_digest"],
        "census_identity": {
            "device": preflight["census_device"],
            "inode": preflight["census_inode"],
            "size_bytes": preflight["census_size_bytes"],
            "mtime_ns": preflight["census_mtime_ns"],
        },
        "monitoring_performed": False,
        "alerts_emitted": 0,
        "comparison_performed": False,
        "activation_authority": False,
        "scope": {
            "source_read": False,
            "provider_access": True,
            "service_changes": False,
            "comparison": False,
            "monitoring": False,
            "activation": False,
        },
    })
    result["fixture_only"] = False
    result["artifact_digest"] = _digest(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--e4-artifact", type=Path, default=E4_ARTIFACT)
    parser.add_argument("--e3-artifact", type=Path, default=E3_ARTIFACT)
    parser.add_argument("--provider-url", type=str, default=None)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    payload = run(
        output=args.output,
        e4_artifact=args.e4_artifact,
        e3_artifact=args.e3_artifact,
        execute=args.execute,
        provider_url=args.provider_url,
    )
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Psi0hE5ExecutionError, Psi0hE5PreflightError,
            Psi0hRealCohortExecutionError, FileNotFoundError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc))
