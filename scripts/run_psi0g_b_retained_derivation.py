#!/usr/bin/env python3
"""Derive isolated EP3/EP4 payloads from PSI0G-A-bound retained stores."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts import EvidenceProvenance, EvidenceRecord, canonical_json_bytes
from src.evidence.discovery import DiscoveryEngine, DiscoverySnapshot, DiscoveryStore
from src.evidence.operation_contracts.formalization import ContractRegistryModel
from src.evidence.operation_contracts.input_windows import EvidenceInputWindow, PrimitiveInputWindow, RuntimeEvaluationSnapshot
from src.evidence.operation_contracts.loader import OperationContractLoader
from src.evidence.operation_contracts.registry import RuntimeRegistries
from src.evidence.operation_contracts.runtime import OperationRuntime
from src.evidence.operation_contracts.storage import OperationRuntimeStore
from src.evidence.operation_contracts.three_sw2_v1 import register_three_sw2_v1
from src.evidence.operation_contracts.watchtower_v1 import register_watchtower_v1
from src.evidence.primitives.contracts import ObservationWindow, PrimitiveObservation, PrimitiveType


ROOT = Path(__file__).resolve().parents[1]
GA_PATH = ROOT / "docs/audits/psi0g_a_real_source_eligibility_reconciliation.json"
RUN_ID = "psi0g-b-retained-derivation-20260817-01"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_identity(path: Path) -> dict[str, int]:
    value = path.stat()
    return {"device": value.st_dev, "inode": value.st_ino, "size_bytes": value.st_size,
            "mtime_ns": value.st_mtime_ns}


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_inputs_query_only(path: Path) -> tuple[tuple[PrimitiveObservation, ...], tuple[EvidenceRecord, ...]]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=0.25)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        references: dict[str, list[str]] = {}
        for primitive_id, evidence_id in connection.execute(
            "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY primitive_id,evidence_id"
        ):
            references.setdefault(primitive_id, []).append(evidence_id)
        rows = connection.execute(
            "SELECT * FROM primitive_observations ORDER BY primitive_id"
        ).fetchall()
        evidence_rows = connection.execute("""
            SELECT n.*,p.endpoint_method,p.request_parameters_digest,p.upstream_dependency,
                   p.acquisition_path,p.cache_source,p.dependency_group,p.parent_evidence_ids_json
            FROM normalized_evidence_records n
            JOIN (SELECT evidence_id,MIN(provider_request_id) provider_request_id
                  FROM normalized_evidence_provenance GROUP BY evidence_id) chosen
              ON chosen.evidence_id=n.evidence_id
            JOIN normalized_evidence_provenance p
              ON p.evidence_id=chosen.evidence_id
             AND p.provider_request_id=chosen.provider_request_id
            WHERE n.evidence_id IN (SELECT DISTINCT evidence_id FROM primitive_evidence_inputs)
            ORDER BY n.evidence_id
        """).fetchall()
    finally:
        connection.close()
    primitives = tuple(PrimitiveObservation(
        primitive_id=row["primitive_id"], primitive_type=row["primitive_type"],
        primitive_version=row["primitive_version"],
        evidence_ids=tuple(references.get(row["primitive_id"], ())),
        subjects=tuple(json.loads(row["subjects_json"])),
        parameters=json.loads(row["parameters_json"]),
        observation_window=ObservationWindow(row["window_start"], row["window_end"]),
        output_payload=json.loads(row["output_payload_json"]), output_digest=row["output_digest"],
        quality_state=row["quality_state"],
        missing_inputs=tuple(json.loads(row["missing_inputs_json"])),
        failure_state=row["failure_state"], generated_at=row["generated_at"],
    ) for row in rows)
    evidence = tuple(EvidenceRecord(
        evidence_id=row["evidence_id"], logical_fact_id=row["logical_fact_id"],
        fact_family=row["fact_family"], fact_schema_version=row["fact_schema_version"],
        chain=row["chain"], network=row["network"], natural_key=row["natural_key"],
        payload=json.loads(row["payload_json"]), payload_digest=row["payload_digest"],
        raw_artifact_digest=row["raw_artifact_digest"], observed_at=row["observed_at"],
        acquired_at=row["acquired_at"], source_id=row["source_id"],
        source_version=row["source_version"], provider=row["provider"],
        provider_request_id=row["provider_request_id"], parser_id=row["parser_id"],
        parser_version=row["parser_version"], replay_version=row["replay_version"],
        verification_state=row["verification_state"], provenance_quality=row["provenance_quality"],
        provenance=EvidenceProvenance(endpoint_method=row["endpoint_method"],
            request_parameters_digest=row["request_parameters_digest"],
            upstream_dependency=row["upstream_dependency"], acquisition_path=row["acquisition_path"],
            cache_source=row["cache_source"], dependency_group=row["dependency_group"],
            parent_evidence_ids=tuple(json.loads(row["parent_evidence_ids_json"]))),
        corrects_evidence_id=row["corrects_evidence_id"], created_at=row["created_at"],
    ) for row in evidence_rows)
    referenced = {ref for item in primitives for ref in item.evidence_ids}
    retained = {item.evidence_id for item in evidence}
    if referenced != retained:
        raise RuntimeError(f"PSI0G_B_EVIDENCE_PROVENANCE_INCOMPLETE:{len(referenced-retained)}")
    return primitives, evidence


def load_primitives_query_only(path: Path) -> tuple[PrimitiveObservation, ...]:
    return load_inputs_query_only(path)[0]


def runtime_registry(register: Callable[[Any], None]) -> tuple[RuntimeRegistries, ContractRegistryModel]:
    registries = RuntimeRegistries()
    register(registries)
    modules, detectors = registries.dependency_versions()
    registry = ContractRegistryModel(
        evidence_versions={"TransactionFact": ("1",), "LaunchFact": ("1",)},
        primitive_versions={kind.value: ("1",) for kind in PrimitiveType},
        behaviour_versions=modules, detector_versions=detectors,
        presentation_versions=registries.presentations.versions(),
    )
    return registries, registry


def derive_corpus(*, operation_key: str, source: Path, contract_path: Path,
                  register: Callable[[Any], None], runtime_path: Path,
                  discovery_path: Path) -> dict[str, Any]:
    before = source_identity(source)
    source_sha256 = sha256_file(source)
    primitives, evidence_records = load_inputs_query_only(source)
    after = source_identity(source)
    if before != after:
        raise RuntimeError(f"PSI0G_B_SOURCE_CHANGED_DURING_READ:{operation_key}")
    if not primitives:
        raise RuntimeError(f"PSI0G_B_EMPTY_PRIMITIVE_CORPUS:{operation_key}")

    subjects = sorted({subject for item in primitives for subject in item.subjects})
    starts = [item.observation_window.start for item in primitives if item.observation_window.start is not None]
    ends = [item.observation_window.end for item in primitives if item.observation_window.end is not None]
    generated_at = max(ends) if ends else 0
    primitive_watermark = hashlib.sha256(canonical_json_bytes(
        [item.primitive_id for item in primitives])).hexdigest()
    evidence_watermark = hashlib.sha256(canonical_json_bytes(
        [item.evidence_id for item in evidence_records])).hexdigest()
    evidence = EvidenceInputWindow.create(subjects=subjects, start=min(starts) if starts else None,
        end=max(ends) if ends else None, watermark=evidence_watermark,
        observations=evidence_records, maximum=len(evidence_records) or 1)
    primitive_window = PrimitiveInputWindow.create(subjects=subjects,
        start=min(starts) if starts else None, end=max(ends) if ends else None,
        watermark=primitive_watermark, observations=primitives, maximum=len(primitives))

    registries, registry = runtime_registry(register)
    contract = OperationContractLoader(registry).load_path(contract_path)
    store = OperationRuntimeStore(runtime_path)
    store.open()
    try:
        store.append_contract(contract, registered_at=generated_at)
        snapshot = RuntimeEvaluationSnapshot.create(contract=contract, subjects=subjects,
            observation_start=primitive_window.start, observation_end=primitive_window.end,
            evidence_window=evidence, primitive_window=primitive_window, generated_at=generated_at)
        result = OperationRuntime(contracts=registry, registries=registries, store=store).evaluate_snapshot(
            snapshot, current_candidate_state=None)
    finally:
        store.close()

    discovery_snapshot = DiscoverySnapshot.create(discovery_version="1.0.0",
        evidence_window=evidence, primitive_window=primitive_window,
        behaviour_observations=result.behaviours, topology_revisions=(result.topology,),
        runtime_snapshot_digests=(snapshot.input_digest,), generated_at=generated_at)
    candidates = DiscoveryEngine().discover(discovery_snapshot)
    discovery_store = DiscoveryStore(discovery_path)
    discovery_store.open()
    try:
        append = discovery_store.append(candidates)
        health = discovery_store.health()
    finally:
        discovery_store.close()

    descriptor = {
        "operation_key": operation_key, "contract_id": result.contract_id,
        "contract_version": result.contract_version, "contract_digest": contract["contract_digest"],
        "source": {"path": display_path(source), "identity": before,
                   "sha256": source_sha256, "access": "sqlite_uri_mode_ro_and_query_only"},
        "evidence_count": len(evidence_records), "evidence_ids_digest": evidence_watermark,
        "primitive_count": len(primitives), "primitive_ids_digest": primitive_watermark,
        "subject_count": len(subjects), "observation_start": primitive_window.start,
        "observation_end": primitive_window.end, "snapshot_digest": snapshot.input_digest,
        "behaviour_observation_ids": [item.observation_id for item in result.behaviours],
        "topology_revision_id": result.topology.revision_id,
        "detector_input_id": result.detector_input.input_id,
        "detector_result_id": result.detector_result.result_id,
        "runtime_persistence": dict(result.persistence),
        "discovery_snapshot_digest": discovery_snapshot.input_digest,
        "candidate_count": len(candidates),
        "candidate_ids_digest": hashlib.sha256(canonical_json_bytes(
            [item.candidate_id for item in candidates])).hexdigest(),
        "candidate_persistence": append, "discovery_health": health,
    }
    return descriptor


def canonical_write(path: Path, value: Any) -> None:
    path.write_bytes(canonical_json_bytes(value))


def run(output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"PSI0G_B_OUTPUT_EXISTS:{output}")
    ga = json.loads(GA_PATH.read_text(encoding="utf-8"))
    if ga.get("status") != "HOLD" or ga.get("milestone") != "PSI0G-A":
        raise ValueError("PSI0G_B_GA_BINDING_INVALID")
    bound = {item["path"]: item["identity"] for item in ga["eligible_sources"]}
    specs = (
        ("watchtower", ROOT / "database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db",
         ROOT / "src/evidence/operation_contracts/contracts/watchtower_v1.json", register_watchtower_v1),
        ("three_sw2", ROOT / "database/evidence_platform/three_sw2_shadow_ep3_2a/evidence.db",
         ROOT / "src/evidence/operation_contracts/contracts/three_sw2_v1.json", register_three_sw2_v1),
    )
    for _, source, _, _ in specs:
        key = str(source.relative_to(ROOT))
        if key not in bound or source_identity(source) != bound[key]:
            raise RuntimeError(f"PSI0G_B_SOURCE_IDENTITY_DRIFT:{key}")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        runtime_path, discovery_path = staging / "operation-runtime.db", staging / "discovery.db"
        operations = [derive_corpus(operation_key=key, source=source,
            contract_path=contract, register=register, runtime_path=runtime_path,
            discovery_path=discovery_path) for key, source, contract, register in specs]
        manifest = {
            "schema_version": "1.0.0", "milestone": "PSI0G-B", "run_id": RUN_ID,
            "status": "PASS", "authority": "NON_PRODUCTION_DERIVED_EVIDENCE_ONLY",
            "psi0g_a_artifact_sha256": sha256_file(GA_PATH), "operations": operations,
            "files": {}, "invariants": {"source_access": "QUERY_ONLY", "provider_rpc_calls": 0,
                "production_database_reads": 0, "candidate_dispositions": 0,
                "f13_exports": 0, "psi0f_publications": 0, "monitoring_activations": 0,
                "production_writes": 0, "identity_promotions": 0},
        }
        for path in (runtime_path, discovery_path):
            manifest["files"][path.name] = {"sha256": sha256_file(path),
                "size_bytes": path.stat().st_size}
        canonical_write(staging / "manifest.json", manifest)
        os.replace(staging, output)
        return manifest
    except BaseException:
        for child in staging.iterdir():
            child.unlink()
        staging.rmdir()
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
        default=ROOT / "docs/audits/psi0g_runs" / RUN_ID)
    args = parser.parse_args()
    result = run(args.output.resolve())
    print(json.dumps({"status": result["status"], "run_id": result["run_id"],
        "operations": [{"operation_key": item["operation_key"],
                         "candidate_count": item["candidate_count"]}
                       for item in result["operations"]]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
