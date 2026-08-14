"""PSI0B-C dependency-injected fixture-only shadow execution runner."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from typing import Callable, Mapping, Tuple
from urllib.parse import quote

from .production_shadow_health_gate import HealthGateDecision, verify_health_gate_decision
from .production_shadow_query_plan import build_psi0a_d2a_rebound_contract
from .production_shadow_resource_ceiling import build_production_shadow_resource_ceiling_contract
from .production_shadow_run_preflight import ProductionShadowRunPreflight, verify_production_shadow_run_preflight


RUNNER_VERSION = "psi0b-c.v1"
AUTHORITY_CLASS = "FIXTURE_ONLY_QUERY_SHADOW_RUNNER"
ENGINEERING_REVISION = "d6f4cd98bb610bb6178fc2315bc6c88c08aa0587"
PSI0B_A_CONTRACT_DIGEST = "4804184b9a09c33daa07e6c810eded85e23df6de1add0e895e0d60935f25c1a4"
BOUND_PREFLIGHT_DIGEST = "35aa4d8e2f1519a60e6c3418a800476952e88bb1e44a4529c0ca7b9a90c960a0"
EB0_1P_BUNDLE_DIGEST = "2c07d41b9c243f8f0c8ca52e0c54c0b184f28a1ed855e98de2a975f936a688e5"
SELECTED_PROJECTION_DIGEST = "fd538d454cd10c7f7cd5ce5fa6fe251d2781efc42614ec10cff800a9348952ff"
BOUND_COHORT_DIGEST = "8f0a54838574e2e82f95030a5981a8b21b13629d493635719a0d23f5013bfbe3"
BOUND_FACT_FAMILY = "LaunchFact"
FILES = {"run.json", "accounting.json", "results.json", "hashes.json"}
DATA_FILES = FILES - {"hashes.json"}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ProductionShadowFixtureRunnerError(RuntimeError):
    """Named fail-closed PSI0B-C fixture runner violation."""


@dataclass(frozen=True)
class FixtureRunnerContract:
    runner_version: str
    engineering_revision: str
    psi0b_a_contract_digest: str
    bound_preflight_digest: str
    eb0_1p_bundle_digest: str
    selected_projection_digest: str
    bound_cohort_digest: str
    fact_family: str
    authority_class: str
    production_paths_allowed: bool
    retries_allowed: bool
    pagination_allowed: bool
    failover_allowed: bool
    widening_allowed: bool
    grants_production_execution_authority: bool
    grants_integration_authority: bool
    grants_activation_authority: bool
    contract_digest: str


@dataclass(frozen=True)
class FixtureShadowBundle:
    output_directory: Path
    bundle_digest: str
    file_digests: Mapping[str, str]
    total_rows: int


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _sha(value: bytes) -> str:
    return sha256(value).hexdigest()


def build_fixture_runner_contract() -> FixtureRunnerContract:
    body = {
        "runner_version": RUNNER_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0b_a_contract_digest": PSI0B_A_CONTRACT_DIGEST,
        "bound_preflight_digest": BOUND_PREFLIGHT_DIGEST,
        "eb0_1p_bundle_digest": EB0_1P_BUNDLE_DIGEST,
        "selected_projection_digest": SELECTED_PROJECTION_DIGEST,
        "bound_cohort_digest": BOUND_COHORT_DIGEST,
        "fact_family": BOUND_FACT_FAMILY,
        "authority_class": AUTHORITY_CLASS,
        "production_paths_allowed": False,
        "retries_allowed": False,
        "pagination_allowed": False,
        "failover_allowed": False,
        "widening_allowed": False,
        "grants_production_execution_authority": False,
        "grants_integration_authority": False,
        "grants_activation_authority": False,
    }
    return FixtureRunnerContract(**body, contract_digest=_digest(body))


def verify_fixture_runner_contract(contract: FixtureRunnerContract) -> bool:
    if contract != build_fixture_runner_contract():
        raise ProductionShadowFixtureRunnerError("PSI0B_C_CONTRACT_REPLAY_MISMATCH")
    if any((contract.production_paths_allowed, contract.retries_allowed,
            contract.pagination_allowed, contract.failover_allowed,
            contract.widening_allowed, contract.grants_production_execution_authority,
            contract.grants_integration_authority, contract.grants_activation_authority)):
        raise ProductionShadowFixtureRunnerError("PSI0B_C_AUTHORITY_DRIFT")
    return True


def _args(preflight: ProductionShadowRunPreflight, query_id: str) -> tuple:
    parameter = next(item for item in preflight.query_parameters if item.query_id == query_id)
    cohort_json = json.dumps(list(preflight.cohort.mints), separators=(",", ":"))
    if query_id == "evidence_launch_facts":
        return parameter.rowid_upper_inclusive, parameter.fact_family, parameter.row_limit
    return parameter.rowid_upper_inclusive, cohort_json, parameter.row_limit


def _normal(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    raise ProductionShadowFixtureRunnerError("PSI0B_C_UNSUPPORTED_SQLITE_VALUE")


def _execute_bound_shadow(
    preflight: ProductionShadowRunPreflight,
    source_paths: Mapping[str, Path],
    output_directory: Path,
    *,
    prestart_health: HealthGateDecision,
    active_health_check: Callable[[str], HealthGateDecision],
    fixture_root: Path | None,
    authority_class: str,
    fixture_only: bool,
    grants_production_execution_authority: bool,
    execution_authorization_digest: str | None,
    output_runner_version: str,
    clock: Callable[[], float] = time.monotonic,
    resource_probe: Callable[[], Tuple[int, int]] = lambda: (1, 0),
    lifecycle_event: Callable[[str, str], None] = lambda _query, _event: None,
    progress_steps: int = 1_000,
) -> FixtureShadowBundle:
    verify_production_shadow_run_preflight(preflight)
    verify_health_gate_decision(prestart_health)
    if prestart_health.phase != "PRESTART" or prestart_health.status != "PASS":
        raise ProductionShadowFixtureRunnerError("PSI0B_C_HEALTH_DO_NOT_START")
    output = Path(output_directory)
    if output.exists():
        raise ProductionShadowFixtureRunnerError("PSI0B_C_OUTPUT_NOT_NEW")
    expected_output_fingerprint = _digest({
        "resolved_output_directory": str(output.resolve()),
        "run_id": preflight.run_id,
    })
    if preflight.output_directory_fingerprint != expected_output_fingerprint:
        raise ProductionShadowFixtureRunnerError("PSI0B_C_OUTPUT_FINGERPRINT_MISMATCH")
    if isinstance(progress_steps, bool) or not isinstance(progress_steps, int) or progress_steps <= 0:
        raise ProductionShadowFixtureRunnerError("PSI0B_C_INVALID_PROGRESS_STEPS")
    if set(source_paths) != {"creator", "evidence", "main", "ops"}:
        raise ProductionShadowFixtureRunnerError("PSI0B_C_FIXTURE_SOURCE_SET_MISMATCH")
    resolved = {name: Path(path).resolve() for name, path in source_paths.items()}
    if fixture_root is not None:
        root = Path(fixture_root).resolve()
        if any(root != path.parent and root not in path.parents for path in resolved.values()):
            raise ProductionShadowFixtureRunnerError("PSI0B_C_NON_FIXTURE_PATH_REJECTED")
    if any(not path.is_file() for path in resolved.values()):
        raise ProductionShadowFixtureRunnerError("PSI0B_C_FIXTURE_SOURCE_MISSING")

    query_contract = build_psi0a_d2a_rebound_contract()
    ceilings = build_production_shadow_resource_ceiling_contract()
    ceiling_by_id = {item.query_id: item for item in ceilings.query_ceilings}
    started = clock()
    results = {}
    accounting = {}
    connections = 0
    total_bytes = total_rows = 0
    max_rss = max_temp = 0
    try:
        for template in query_contract.templates:
            active = active_health_check(template.query_id)
            verify_health_gate_decision(active)
            if active.phase != "ACTIVE" or active.status != "PASS":
                raise ProductionShadowFixtureRunnerError("PSI0B_C_ACTIVE_HEALTH_STOP")
            ceiling = ceiling_by_id[template.query_id]
            uri = f"file:{quote(str(resolved[template.database_id]), safe='/')}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=0.25, isolation_level=None)
            connections += 1
            connection.row_factory = sqlite3.Row
            query_started = clock()
            exceeded = False
            transaction_started = False

            def stop() -> int:
                nonlocal exceeded
                exceeded = clock() - query_started >= ceiling.maximum_query_seconds
                return int(exceeded)

            try:
                connection.execute("PRAGMA query_only=ON")
                if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
                    raise ProductionShadowFixtureRunnerError("PSI0B_C_QUERY_ONLY_NOT_ENFORCED")
                connection.execute("BEGIN")
                transaction_started = True
                lifecycle_event(template.query_id, "TRANSACTION_STARTED")
                connection.set_progress_handler(stop, progress_steps)
                lifecycle_event(template.query_id, "PROGRESS_HANDLER_INSTALLED")
                try:
                    fetched = connection.execute(template.sql, _args(preflight, template.query_id)).fetchall()
                except sqlite3.OperationalError as exc:
                    if exceeded:
                        raise ProductionShadowFixtureRunnerError("PSI0B_C_QUERY_DEADLINE_EXCEEDED") from exc
                    raise ProductionShadowFixtureRunnerError("PSI0B_C_SQLITE_QUERY_EXCEPTION") from exc
                rows = [{key: _normal(row[key]) for key in row.keys()} for row in fetched]
                payload_bytes = len(_canonical(rows))
                elapsed = max(clock() - query_started, 1e-9)
                if len(rows) > ceiling.maximum_rows:
                    raise ProductionShadowFixtureRunnerError("PSI0B_C_QUERY_ROW_CEILING_EXCEEDED")
                if payload_bytes > ceiling.maximum_canonical_bytes:
                    raise ProductionShadowFixtureRunnerError("PSI0B_C_QUERY_BYTE_CEILING_EXCEEDED")
                if elapsed > ceiling.maximum_query_seconds:
                    raise ProductionShadowFixtureRunnerError("PSI0B_C_QUERY_DEADLINE_EXCEEDED")
                if elapsed > ceiling.maximum_transaction_seconds:
                    raise ProductionShadowFixtureRunnerError("PSI0B_C_TRANSACTION_DEADLINE_EXCEEDED")
                results[template.query_id] = rows
                accounting[template.query_id] = {
                    "selected_rows": len(rows), "excluded_rows": 0,
                    "canonical_output_bytes": payload_bytes,
                    "query_seconds": elapsed, "transaction_seconds": elapsed,
                    "temporary_bytes": 0,
                }
                total_rows += len(rows); total_bytes += payload_bytes
            finally:
                try:
                    connection.set_progress_handler(None, 0)
                    lifecycle_event(template.query_id, "PROGRESS_HANDLER_REMOVED")
                finally:
                    if transaction_started:
                        try:
                            connection.rollback()
                        finally:
                            lifecycle_event(template.query_id, "ROLLBACK_ATTEMPTED")
                    connection.close()
                    lifecycle_event(template.query_id, "CONNECTION_CLOSED")
            rss, temporary = resource_probe()
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (rss, temporary)):
                raise ProductionShadowFixtureRunnerError("PSI0B_C_RESOURCE_PROBE_INVALID")
            max_rss = max(max_rss, rss); max_temp = max(max_temp, temporary)
            if max_rss > ceilings.maximum_process_rss_delta_bytes:
                raise ProductionShadowFixtureRunnerError("PSI0B_C_MEMORY_CEILING_EXCEEDED")
            if max_temp > ceilings.maximum_sqlite_temporary_bytes:
                raise ProductionShadowFixtureRunnerError("PSI0B_C_TEMPORARY_CEILING_EXCEEDED")
        wall = max(clock() - started, 1e-9)
        if total_rows > ceilings.maximum_total_rows or total_bytes > ceilings.maximum_total_canonical_bytes:
            raise ProductionShadowFixtureRunnerError("PSI0B_C_TOTAL_OUTPUT_CEILING_EXCEEDED")
        if wall > ceilings.maximum_wall_seconds or connections > ceilings.maximum_connections_opened:
            raise ProductionShadowFixtureRunnerError("PSI0B_C_RUN_RESOURCE_CEILING_EXCEEDED")
    except Exception:
        if output.exists():
            raise ProductionShadowFixtureRunnerError("PSI0B_C_PARTIAL_PUBLICATION")
        raise

    run = {
        "runner_version": output_runner_version, "run_id": preflight.run_id,
        "preflight_digest": preflight.preflight_digest,
        "authority_class": authority_class, "fixture_only": fixture_only,
        "execution_authorization_digest": execution_authorization_digest,
        "grants_production_execution_authority": grants_production_execution_authority,
        "grants_integration_authority": False, "grants_activation_authority": False,
    }
    accounting_document = {
        "queries": accounting, "total_rows": total_rows,
        "total_canonical_output_bytes": total_bytes, "connections_opened": connections,
        "maximum_concurrent_connections": 1, "process_rss_delta_bytes": max_rss,
        "sqlite_temporary_bytes": max_temp, "accounting_residual": 0,
    }
    documents = {"run.json": run, "accounting.json": accounting_document, "results.json": results}
    payloads = {name: _canonical(value) for name, value in documents.items()}
    digests = {name: _sha(value) for name, value in payloads.items()}
    bundle_digest = _sha(_canonical(digests))
    hashes = {"runner_version": output_runner_version, "files": digests, "bundle_digest": bundle_digest}
    staging = output.with_name(f".{output.name}.tmp")
    if staging.exists():
        raise ProductionShadowFixtureRunnerError("PSI0B_C_STAGING_EXISTS")
    try:
        staging.mkdir()
        for name, payload in payloads.items():
            with (staging / name).open("xb") as handle: handle.write(payload)
        with (staging / "hashes.json").open("xb") as handle: handle.write(_canonical(hashes))
        os.replace(staging, output)
    except Exception as exc:
        if staging.exists(): shutil.rmtree(staging)
        raise ProductionShadowFixtureRunnerError("PSI0B_C_ATOMIC_PUBLICATION_FAILED") from exc
    return FixtureShadowBundle(output, bundle_digest, digests, total_rows)


def execute_fixture_shadow(
    contract: FixtureRunnerContract,
    preflight: ProductionShadowRunPreflight,
    fixture_paths: Mapping[str, Path],
    output_directory: Path,
    *,
    prestart_health: HealthGateDecision,
    active_health_check: Callable[[str], HealthGateDecision],
    fixture_root: Path,
    clock: Callable[[], float] = time.monotonic,
    resource_probe: Callable[[], Tuple[int, int]] = lambda: (1, 0),
    lifecycle_event: Callable[[str, str], None] = lambda _query, _event: None,
    progress_steps: int = 1_000,
) -> FixtureShadowBundle:
    verify_fixture_runner_contract(contract)
    _execute_bound_shadow(
        preflight, fixture_paths, output_directory,
        prestart_health=prestart_health, active_health_check=active_health_check,
        fixture_root=fixture_root, authority_class=AUTHORITY_CLASS, fixture_only=True,
        grants_production_execution_authority=False,
        execution_authorization_digest=None, output_runner_version=RUNNER_VERSION,
        clock=clock, resource_probe=resource_probe, lifecycle_event=lifecycle_event,
        progress_steps=progress_steps,
    )
    return verify_fixture_shadow_bundle(output_directory)


def verify_fixture_shadow_bundle(output_directory: Path) -> FixtureShadowBundle:
    output = Path(output_directory)
    if not output.is_dir() or {item.name for item in output.iterdir()} != FILES:
        raise ProductionShadowFixtureRunnerError("PSI0B_C_BUNDLE_FILE_SET_MISMATCH")
    try:
        docs = {name: json.loads((output / name).read_text()) for name in DATA_FILES}
        hashes = json.loads((output / "hashes.json").read_text())
    except Exception as exc:
        raise ProductionShadowFixtureRunnerError("PSI0B_C_INVALID_JSON") from exc
    if any((output / name).read_bytes() != _canonical(docs[name]) for name in DATA_FILES) or (output / "hashes.json").read_bytes() != _canonical(hashes):
        raise ProductionShadowFixtureRunnerError("PSI0B_C_NONCANONICAL_JSON")
    actual = {name: _sha((output / name).read_bytes()) for name in DATA_FILES}
    bundle_digest = _sha(_canonical(actual))
    if hashes != {"runner_version": RUNNER_VERSION, "files": actual, "bundle_digest": bundle_digest}:
        raise ProductionShadowFixtureRunnerError("PSI0B_C_BUNDLE_DIGEST_MISMATCH")
    run = docs["run.json"]; accounting = docs["accounting.json"]; results = docs["results.json"]
    if run.get("authority_class") != AUTHORITY_CLASS or not run.get("fixture_only") or any((run.get("grants_production_execution_authority"), run.get("grants_integration_authority"), run.get("grants_activation_authority"))):
        raise ProductionShadowFixtureRunnerError("PSI0B_C_BUNDLE_AUTHORITY_DRIFT")
    if set(results) != set(build_psi0a_d2a_rebound_contract().templates[i].query_id for i in range(5)):
        raise ProductionShadowFixtureRunnerError("PSI0B_C_RESULT_QUERY_SET_MISMATCH")
    counted = sum(len(rows) for rows in results.values())
    if accounting.get("total_rows") != counted or accounting.get("accounting_residual") != 0:
        raise ProductionShadowFixtureRunnerError("PSI0B_C_ACCOUNTING_MISMATCH")
    return FixtureShadowBundle(output, bundle_digest, actual, counted)
