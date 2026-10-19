"""EB1.3H immutable status-aware bundles for EB1.3G extraction results."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from .evidence_fulfillment_planning_proposal import AUTHORITY
from .evidence_fulfillment_planning_proposal_extractor import (
    SCHEMA_VERSION as EXTRACTION_SCHEMA_VERSION,
    EvidenceFulfillmentPlanningProposalExtraction,
)

SCHEMA_VERSION = "eb1.3h.v1"
REVISION = re.compile(r"^[0-9a-f]{7,64}$")
BASE_FILES = {"run.json", "accounting.json", "hashes.json"}
PROJECTED_FILES = BASE_FILES | {"manifest.json", "corpus.json"}


class EvidenceFulfillmentPlanningProposalBundleError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceFulfillmentPlanningProposalBundle:
    output_directory: Path
    status: str
    bundle_digest: str
    file_digests: dict


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode() + b"\n"


def _digest_bytes(value):
    return sha256(value).hexdigest()


def _digest_object(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _result_body(run, accounting, manifest=None, corpus=None):
    body = {
        "schema_version": run["extraction_schema_version"],
        "status": run["status"],
        "input_fingerprint": run["input_fingerprint"],
        "bundle_digest": run["eb1_1h_bundle_digest"],
        "accounting": accounting,
    }
    if run["status"] == "PROJECTED":
        body.update(
            manifest_digest=manifest["manifest_digest"],
            corpus_digest=corpus["corpus_digest"],
        )
    body.update(
        authority_class=run["authority_class"],
        grants_planning_authority=run["grants_planning_authority"],
        grants_execution_authority=run["grants_execution_authority"],
    )
    return body


def write_evidence_fulfillment_planning_proposal_bundle(
    result: EvidenceFulfillmentPlanningProposalExtraction,
    output_directory: Path,
    *,
    run_id: str,
    engineering_revision: str,
):
    if not run_id or not REVISION.fullmatch(engineering_revision):
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_INVALID_RUN_METADATA")
    if result.status not in {"PROJECTED", "NO_ELIGIBLE_PROPOSALS"}:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_UNKNOWN_STATUS")
    if result.authority_class != AUTHORITY or result.grants_planning_authority or result.grants_execution_authority:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_AUTHORITY_MISMATCH")
    if result.status == "PROJECTED" and (result.manifest is None or result.corpus is None):
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_PROJECTED_OUTPUT_MISSING")
    if result.status == "NO_ELIGIBLE_PROPOSALS" and (result.manifest is not None or result.corpus is not None):
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_ZERO_STATUS_OUTPUT_PRESENT")
    output_directory = Path(output_directory)
    if output_directory.exists() and (
        not output_directory.is_dir() or any(output_directory.iterdir())
    ):
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_OUTPUT_NOT_EMPTY")
    output_directory.mkdir(exist_ok=True)
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "engineering_revision": engineering_revision,
        "extraction_schema_version": result.schema_version,
        "status": result.status,
        "result_digest": result.result_digest,
        "input_fingerprint": result.input_fingerprint,
        "eb1_1h_bundle_digest": result.eb1_1h_bundle_digest,
        "authority_class": result.authority_class,
        "grants_planning_authority": result.grants_planning_authority,
        "grants_execution_authority": result.grants_execution_authority,
    }
    payload = {
        "run.json": _canonical(run),
        "accounting.json": _canonical(asdict(result.accounting)),
    }
    if result.status == "PROJECTED":
        payload["manifest.json"] = _canonical(asdict(result.manifest))
        payload["corpus.json"] = _canonical(asdict(result.corpus))
    file_digests = {name: _digest_bytes(value) for name, value in payload.items()}
    bundle_digest = _digest_bytes(_canonical(file_digests))
    hashes = {
        "schema_version": SCHEMA_VERSION,
        "files": file_digests,
        "bundle_digest": bundle_digest,
    }
    for name, value in payload.items():
        with (output_directory / name).open("xb") as handle:
            handle.write(value)
    with (output_directory / "hashes.json").open("xb") as handle:
        handle.write(_canonical(hashes))
    return EvidenceFulfillmentPlanningProposalBundle(
        output_directory, result.status, bundle_digest, file_digests
    )


def verify_evidence_fulfillment_planning_proposal_bundle(output_directory: Path):
    output_directory = Path(output_directory)
    if not output_directory.is_dir():
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_OUTPUT_NOT_FOUND")
    try:
        run = json.loads((output_directory / "run.json").read_text())
    except Exception as exc:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_INVALID_JSON") from exc
    expected_files = PROJECTED_FILES if run.get("status") == "PROJECTED" else BASE_FILES
    if run.get("status") not in {"PROJECTED", "NO_ELIGIBLE_PROPOSALS"}:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_UNKNOWN_STATUS")
    if {item.name for item in output_directory.iterdir()} != expected_files:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_FILE_SET_MISMATCH")
    data_files = expected_files - {"hashes.json"}
    try:
        documents = {name: json.loads((output_directory / name).read_text()) for name in data_files}
        hashes = json.loads((output_directory / "hashes.json").read_text())
    except Exception as exc:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_INVALID_JSON") from exc
    if any((output_directory / name).read_bytes() != _canonical(documents[name]) for name in data_files):
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_NONCANONICAL_JSON")
    if (output_directory / "hashes.json").read_bytes() != _canonical(hashes):
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_NONCANONICAL_JSON")
    actual = {name: _digest_bytes((output_directory / name).read_bytes()) for name in data_files}
    bundle_digest = _digest_bytes(_canonical(actual))
    if hashes != {"schema_version": SCHEMA_VERSION, "files": actual, "bundle_digest": bundle_digest}:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_DIGEST_MISMATCH")
    run = documents["run.json"]
    accounting = documents["accounting.json"]
    required_run = {
        "schema_version", "run_id", "engineering_revision", "extraction_schema_version",
        "status", "result_digest", "input_fingerprint", "eb1_1h_bundle_digest",
        "authority_class", "grants_planning_authority", "grants_execution_authority",
    }
    if set(run) != required_run or run["schema_version"] != SCHEMA_VERSION:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_RUN_SCHEMA_MISMATCH")
    if run["extraction_schema_version"] != EXTRACTION_SCHEMA_VERSION:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_EXTRACTION_VERSION_MISMATCH")
    if run["authority_class"] != AUTHORITY or run["grants_planning_authority"] or run["grants_execution_authority"]:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_AUTHORITY_MISMATCH")
    required_accounting = {
        "input_requirement_count", "review_record_count", "proposal_input_count",
        "selected_proposal_count", "excluded_not_ready_count", "unknown_requirement_count",
        "conflict_count", "accounting_residual",
    }
    if set(accounting) != required_accounting or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in accounting.values()
    ):
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_ACCOUNTING_SCHEMA_MISMATCH")
    if accounting["proposal_input_count"] != (
        accounting["selected_proposal_count"]
        + accounting["excluded_not_ready_count"]
        + accounting["unknown_requirement_count"]
        + accounting["accounting_residual"]
    ):
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_ACCOUNTING_MISMATCH")
    if accounting["unknown_requirement_count"] or accounting["conflict_count"] or accounting["accounting_residual"]:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_FAILED_ACCOUNTING_SERIALIZED")
    manifest = documents.get("manifest.json")
    corpus = documents.get("corpus.json")
    if run["status"] == "PROJECTED":
        if accounting["selected_proposal_count"] <= 0:
            raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_PROJECTED_ACCOUNTING_MISMATCH")
        if manifest["proposal_count"] != accounting["selected_proposal_count"]:
            raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_MANIFEST_COUNT_MISMATCH")
        if corpus["proposal_count"] != accounting["selected_proposal_count"]:
            raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_CORPUS_COUNT_MISMATCH")
        if manifest["eb1_1h_bundle_digest"] != run["eb1_1h_bundle_digest"]:
            raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_LINEAGE_MISMATCH")
        if manifest["manifest_digest"] not in corpus["source_manifest_digests"]:
            raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_LINEAGE_MISMATCH")
        if manifest["manifest_digest"] != _digest_object(
            {key: value for key, value in manifest.items() if key != "manifest_digest"}
        ):
            raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_MANIFEST_REPLAY_MISMATCH")
        if corpus["corpus_digest"] != _digest_object(
            {key: value for key, value in corpus.items() if key != "corpus_digest"}
        ):
            raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_CORPUS_REPLAY_MISMATCH")
    elif accounting["selected_proposal_count"] != 0:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_ZERO_STATUS_ACCOUNTING_MISMATCH")
    expected_result_digest = _digest_object(_result_body(run, accounting, manifest, corpus))
    if run["result_digest"] != expected_result_digest:
        raise EvidenceFulfillmentPlanningProposalBundleError("EB1_3H_RESULT_REPLAY_MISMATCH")
    return EvidenceFulfillmentPlanningProposalBundle(
        output_directory, run["status"], bundle_digest, actual
    )
