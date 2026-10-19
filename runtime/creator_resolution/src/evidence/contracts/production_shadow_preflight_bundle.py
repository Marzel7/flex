"""PSI0A-H immutable replay-verified preflight bundle and closure."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
from typing import Mapping, Tuple


BUNDLE_VERSION = "psi0a-h.v1"
AUTHORITY_CLASS = "NON_EXECUTING_PRODUCTION_SHADOW_PREFLIGHT_CLOSURE"
ENGINEERING_REVISION = "903ce9d13702339d3500c0498d4cc259a9e90934"
ABORT_ISOLATION_CONTRACT_DIGEST = "c9ff90b6cbfb332bc00691c87528abf5f6b6158b54072bfb48c60d9f920e5bab"
HEALTH_GATE_CONTRACT_DIGEST = "8c92231a76c9daad4305bd3859760bc6f1d1ef31249b255b471c213f1ce1c3bf"
RESOURCE_CEILING_CONTRACT_DIGEST = "f5eea8b9f8ba6b102f57e4ae59eb35eb8f0e23d3f8ac0f493f35671f8271f736"
PLAN_QUALIFICATION_DIGEST = "38d0605e77e1503e9d5e952d13a3e1501aacf6c84db7a3debc334bca8fc484ce"
CANONICAL_MANIFEST_DIGEST = "d956bc24c1cd160162acaaad5bc466a2dece78ea34fc1f5238bc80728d4283f5"
READ_BOUNDARY_DIGEST = "fdf11dc5e29c176d3724a4ccd1e3ff56584727512853bfb58a71fb3979c246f8"
CLOSURE_VERDICT = "PSI0A_PASS_PSI0B_MAY_BE_SEPARATELY_PROPOSED"
QUERY_IDS = (
    "creator_selected_cohort",
    "evidence_launch_facts",
    "main_selected_cohort",
    "ops_selected_cohort",
    "snapshot_selected_cohort",
)
DATA_FILES = {"run.json", "lineage.json", "preflight.json", "closure.json"}
EXACT_FILES = DATA_FILES | {"hashes.json"}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ProductionShadowPreflightBundleError(RuntimeError):
    """Named fail-closed PSI0A-H bundle violation."""


@dataclass(frozen=True)
class PreflightComponentSummary:
    component: str
    status: str
    identity_digest: str
    item_count: int
    conflict_count: int
    grants_extraction_authority: bool
    grants_activation_authority: bool
    summary_digest: str


@dataclass(frozen=True)
class ProductionShadowPreflightBundle:
    output_directory: Path
    closure_verdict: str
    bundle_digest: str
    file_digests: Mapping[str, str]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _digest_object(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_preflight_component_summary(
    *, component: str, status: str, identity_digest: str,
    item_count: int, conflict_count: int = 0,
    grants_extraction_authority: bool = False,
    grants_activation_authority: bool = False,
) -> PreflightComponentSummary:
    body = {
        "component": component,
        "status": status,
        "identity_digest": identity_digest,
        "item_count": item_count,
        "conflict_count": conflict_count,
        "grants_extraction_authority": grants_extraction_authority,
        "grants_activation_authority": grants_activation_authority,
    }
    summary = PreflightComponentSummary(**body, summary_digest=_digest_object(body))
    verify_preflight_component_summary(summary)
    return summary


def verify_preflight_component_summary(summary: PreflightComponentSummary) -> bool:
    body = asdict(summary)
    digest = body.pop("summary_digest")
    if digest != _digest_object(body):
        raise ProductionShadowPreflightBundleError("PSI0A_H_COMPONENT_REPLAY_MISMATCH")
    if not summary.component or summary.status != "PASS" or not _DIGEST.fullmatch(summary.identity_digest):
        raise ProductionShadowPreflightBundleError("PSI0A_H_COMPONENT_INVALID_OR_NOT_PASS")
    for value in (summary.item_count, summary.conflict_count):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProductionShadowPreflightBundleError("PSI0A_H_COMPONENT_COUNT_INVALID")
    if summary.conflict_count:
        raise ProductionShadowPreflightBundleError("PSI0A_H_COMPONENT_CONFLICT")
    if summary.grants_extraction_authority or summary.grants_activation_authority:
        raise ProductionShadowPreflightBundleError("PSI0A_H_COMPONENT_AUTHORITY_DRIFT")
    return True


def _expected_components() -> Mapping[str, Tuple[str, int]]:
    return {
        "capture_manifest": (CANONICAL_MANIFEST_DIGEST, 5),
        "read_boundary": (READ_BOUNDARY_DIGEST, 5),
        "query_plan_qualification": (PLAN_QUALIFICATION_DIGEST, 5),
        "resource_ceiling": (RESOURCE_CEILING_CONTRACT_DIGEST, 5),
        "health_gate": (HEALTH_GATE_CONTRACT_DIGEST, 3),
        "abort_isolation": (ABORT_ISOLATION_CONTRACT_DIGEST, 11),
    }


def _validated_components(
    components: Tuple[PreflightComponentSummary, ...],
) -> Tuple[PreflightComponentSummary, ...]:
    expected = _expected_components()
    mapped = {item.component: item for item in components}
    if len(mapped) != len(components) or set(mapped) != set(expected):
        raise ProductionShadowPreflightBundleError("PSI0A_H_COMPONENT_SET_MISMATCH")
    for name, (identity, count) in expected.items():
        item = mapped[name]
        verify_preflight_component_summary(item)
        if item.identity_digest != identity or item.item_count != count:
            raise ProductionShadowPreflightBundleError("PSI0A_H_COMPONENT_LINEAGE_MISMATCH")
    return tuple(mapped[name] for name in sorted(mapped))


def _documents(
    components: Tuple[PreflightComponentSummary, ...], *, run_id: str,
) -> Mapping[str, object]:
    ordered = _validated_components(components)
    lineage = {
        "engineering_revision": ENGINEERING_REVISION,
        "canonical_manifest_digest": CANONICAL_MANIFEST_DIGEST,
        "read_boundary_digest": READ_BOUNDARY_DIGEST,
        "plan_qualification_digest": PLAN_QUALIFICATION_DIGEST,
        "resource_ceiling_contract_digest": RESOURCE_CEILING_CONTRACT_DIGEST,
        "health_gate_contract_digest": HEALTH_GATE_CONTRACT_DIGEST,
        "abort_isolation_contract_digest": ABORT_ISOLATION_CONTRACT_DIGEST,
    }
    preflight = {
        "component_summaries": [asdict(item) for item in ordered],
        "query_ids": list(QUERY_IDS),
        "surface_count": 5,
        "stable_inclusive_rowid_only": True,
        "schema_and_path_compatibility": "PASS",
        "query_plan_compatibility": "PASS",
        "resource_ceiling_qualification": "PASS",
        "health_gate_qualification": "PASS",
        "abort_isolation_qualification": "PASS",
        "live_health_observation_performed": False,
        "canonical_extraction_performed": False,
        "production_rows_read": 0,
        "production_writes": 0,
        "provider_rpc_calls": 0,
    }
    closure = {
        "verdict": CLOSURE_VERDICT,
        "psi0b_execution_authorized": False,
        "production_integration_authorized": False,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
        "separate_psi0b_authorization_required": True,
    }
    run = {
        "bundle_version": BUNDLE_VERSION,
        "run_id": run_id,
        "engineering_revision": ENGINEERING_REVISION,
        "authority_class": AUTHORITY_CLASS,
        "closure_verdict": CLOSURE_VERDICT,
        "lineage_digest": _digest_object(lineage),
        "preflight_digest": _digest_object(preflight),
        "closure_digest": _digest_object(closure),
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
    }
    return {
        "run.json": run,
        "lineage.json": lineage,
        "preflight.json": preflight,
        "closure.json": closure,
    }


def write_production_shadow_preflight_bundle(
    components: Tuple[PreflightComponentSummary, ...], output_directory: Path, *, run_id: str,
) -> ProductionShadowPreflightBundle:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ProductionShadowPreflightBundleError("PSI0A_H_INVALID_RUN_ID")
    output = Path(output_directory)
    if output.exists():
        raise ProductionShadowPreflightBundleError("PSI0A_H_OUTPUT_ALREADY_EXISTS")
    documents = _documents(components, run_id=run_id)
    payload = {name: _canonical(value) for name, value in documents.items()}
    file_digests = {name: _digest_bytes(value) for name, value in payload.items()}
    bundle_digest = _digest_bytes(_canonical(file_digests))
    hashes = {
        "bundle_version": BUNDLE_VERSION,
        "files": file_digests,
        "bundle_digest": bundle_digest,
    }
    staging = output.with_name(f".{output.name}.{run_id}.tmp")
    if staging.exists():
        raise ProductionShadowPreflightBundleError("PSI0A_H_STAGING_ALREADY_EXISTS")
    try:
        staging.mkdir(parents=False)
        for name, value in payload.items():
            with (staging / name).open("xb") as handle:
                handle.write(value)
        with (staging / "hashes.json").open("xb") as handle:
            handle.write(_canonical(hashes))
        os.replace(staging, output)
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging)
        raise ProductionShadowPreflightBundleError("PSI0A_H_ATOMIC_WRITE_FAILED") from exc
    return ProductionShadowPreflightBundle(output, CLOSURE_VERDICT, bundle_digest, file_digests)


def verify_production_shadow_preflight_bundle(
    output_directory: Path,
) -> ProductionShadowPreflightBundle:
    output = Path(output_directory)
    if not output.is_dir():
        raise ProductionShadowPreflightBundleError("PSI0A_H_OUTPUT_NOT_FOUND")
    if {item.name for item in output.iterdir()} != EXACT_FILES:
        raise ProductionShadowPreflightBundleError("PSI0A_H_FILE_SET_MISMATCH")
    try:
        documents = {name: json.loads((output / name).read_text()) for name in DATA_FILES}
        hashes = json.loads((output / "hashes.json").read_text())
    except Exception as exc:
        raise ProductionShadowPreflightBundleError("PSI0A_H_INVALID_JSON") from exc
    if any((output / name).read_bytes() != _canonical(documents[name]) for name in DATA_FILES):
        raise ProductionShadowPreflightBundleError("PSI0A_H_NONCANONICAL_JSON")
    if (output / "hashes.json").read_bytes() != _canonical(hashes):
        raise ProductionShadowPreflightBundleError("PSI0A_H_NONCANONICAL_JSON")
    actual = {name: _digest_bytes((output / name).read_bytes()) for name in DATA_FILES}
    bundle_digest = _digest_bytes(_canonical(actual))
    if hashes != {"bundle_version": BUNDLE_VERSION, "files": actual, "bundle_digest": bundle_digest}:
        raise ProductionShadowPreflightBundleError("PSI0A_H_DIGEST_MISMATCH")
    run = documents["run.json"]
    lineage = documents["lineage.json"]
    preflight = documents["preflight.json"]
    closure = documents["closure.json"]
    required_run = {
        "bundle_version", "run_id", "engineering_revision", "authority_class",
        "closure_verdict", "lineage_digest", "preflight_digest", "closure_digest",
        "grants_extraction_authority", "grants_activation_authority",
    }
    if set(run) != required_run or run["bundle_version"] != BUNDLE_VERSION:
        raise ProductionShadowPreflightBundleError("PSI0A_H_RUN_SCHEMA_MISMATCH")
    if run["engineering_revision"] != ENGINEERING_REVISION or run["authority_class"] != AUTHORITY_CLASS:
        raise ProductionShadowPreflightBundleError("PSI0A_H_ENGINEERING_OR_AUTHORITY_DRIFT")
    if run["grants_extraction_authority"] or run["grants_activation_authority"]:
        raise ProductionShadowPreflightBundleError("PSI0A_H_AUTHORITY_DRIFT")
    if not _RUN_ID.fullmatch(run["run_id"]):
        raise ProductionShadowPreflightBundleError("PSI0A_H_INVALID_RUN_ID")
    expected_lineage = _documents(
        tuple(build_preflight_component_summary(
            component=name, status="PASS", identity_digest=identity, item_count=count,
        ) for name, (identity, count) in _expected_components().items()),
        run_id=run["run_id"],
    )
    if documents != expected_lineage:
        raise ProductionShadowPreflightBundleError("PSI0A_H_LINEAGE_OR_REPLAY_MISMATCH")
    if run["lineage_digest"] != _digest_object(lineage) or run["preflight_digest"] != _digest_object(preflight) or run["closure_digest"] != _digest_object(closure):
        raise ProductionShadowPreflightBundleError("PSI0A_H_DOCUMENT_REPLAY_MISMATCH")
    if closure["verdict"] != CLOSURE_VERDICT or closure["psi0b_execution_authorized"]:
        raise ProductionShadowPreflightBundleError("PSI0A_H_CLOSURE_AUTHORITY_DRIFT")
    return ProductionShadowPreflightBundle(output, CLOSURE_VERDICT, bundle_digest, actual)
