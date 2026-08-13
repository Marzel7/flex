"""EB0.4H immutable bundles for EB0.4G fixture extraction results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

from .operational_family_corpus import OperationalFamilyCorpus, verify_operational_family_corpora
from .operational_family_extractor import EXTRACTOR_SCHEMA_VERSION, OperationalFamilyExtraction
from .operational_family_manifest import OperationalFamilyManifest, verify_operational_family_manifest
from .operational_family_nomination import OperationBehaviourFact, OperationalFamilyNomination


BUNDLE_SCHEMA_VERSION = "eb0.4h.v1"
_FILES = ("run.json", "accounting.json", "manifests.json", "corpora.json")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class OperationalFamilyBundleError(RuntimeError):
    """Named fail-closed EB0.4H error."""


@dataclass(frozen=True)
class OperationalFamilyBundle:
    output_directory: Path
    bundle_digest: str
    file_digests: Mapping[str, str]


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _sha(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _result_body(result: OperationalFamilyExtraction) -> dict[str, object]:
    return {
        "schema_version": result.schema_version,
        "selected_operation_ids": result.selected_operation_ids,
        "qualified_operation_ids": result.qualified_operation_ids,
        "excluded_operations": dict(sorted(result.excluded_operations.items())),
        "candidate_group_count": result.candidate_group_count,
        "fact_count": result.fact_count,
        "nomination_count": result.nomination_count,
        "conflict_count": result.conflict_count,
        "manifests": [item.manifest_digest for item in result.manifests],
        "corpora": [item.corpus_digest for item in result.corpora],
        "input_fingerprint": result.input_fingerprint,
    }


def _prepare(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise OperationalFamilyBundleError("EB0_4H_OUTPUT_NOT_EMPTY")
    else:
        path.mkdir(mode=0o700)


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise OperationalFamilyBundleError("EB0_4H_OVERWRITE_REJECTED") from exc


def write_operational_family_bundle(result: OperationalFamilyExtraction, output_directory: Path, *, run_id: str, engineering_revision: str) -> OperationalFamilyBundle:
    if not _RUN_ID.fullmatch(run_id):
        raise OperationalFamilyBundleError("EB0_4H_INVALID_RUN_ID")
    if not _REVISION.fullmatch(engineering_revision):
        raise OperationalFamilyBundleError("EB0_4H_INVALID_ENGINEERING_REVISION")
    if result.schema_version != EXTRACTOR_SCHEMA_VERSION or not _DIGEST.fullmatch(result.input_fingerprint) or not _DIGEST.fullmatch(result.result_digest):
        raise OperationalFamilyBundleError("EB0_4H_INVALID_EXTRACTION")
    if _sha(_json(_result_body(result)).rstrip(b"\n")) != result.result_digest:
        raise OperationalFamilyBundleError("EB0_4H_RESULT_MISMATCH")
    output = Path(output_directory); _prepare(output)
    payloads = {
        "run.json": _json({"bundle_schema_version": BUNDLE_SCHEMA_VERSION, "extraction_schema_version": result.schema_version, "run_id": run_id, "engineering_revision": engineering_revision, "input_fingerprint": result.input_fingerprint, "extraction_result_digest": result.result_digest}),
        "accounting.json": _json({"selected_operation_ids": list(result.selected_operation_ids), "qualified_operation_ids": list(result.qualified_operation_ids), "excluded_operations": dict(sorted(result.excluded_operations.items())), "candidate_group_count": result.candidate_group_count, "fact_count": result.fact_count, "nomination_count": result.nomination_count, "conflict_count": result.conflict_count}),
        "manifests.json": _json({"manifests": [asdict(item) for item in result.manifests]}),
        "corpora.json": _json({"corpora": [asdict(item) for item in result.corpora]}),
    }
    digests = {name: _sha(payloads[name]) for name in _FILES}
    bundle_digest = _sha(_json(digests))
    for name in _FILES:
        _write_exclusive(output / name, payloads[name])
    _write_exclusive(output / "hashes.json", _json({"bundle_schema_version": BUNDLE_SCHEMA_VERSION, "files": digests, "bundle_digest": bundle_digest}))
    return OperationalFamilyBundle(output, bundle_digest, digests)


def _fact(value):
    data = dict(value)
    for field in ("edge_features", "mechanism_features", "temporal_features"):
        data[field] = tuple(data[field])
    return OperationBehaviourFact(**data)


def _nomination(value):
    data = dict(value)
    for field in ("member_operation_ids", "supporting_fact_ids", "shared_edge_features", "shared_mechanism_features", "shared_temporal_features", "supporting_sources", "conflict_group_ids"):
        data[field] = tuple(data[field])
    return OperationalFamilyNomination(**data)


def _manifest(value):
    data = dict(value); data["facts"] = tuple(_fact(x) for x in data["facts"]); data["nominations"] = tuple(_nomination(x) for x in data["nominations"])
    return OperationalFamilyManifest(**data)


def _corpus(value):
    data = dict(value); data["source_manifest_digests"] = tuple(data["source_manifest_digests"]); data["facts"] = tuple(_fact(x) for x in data["facts"]); data["nominations"] = tuple(_nomination(x) for x in data["nominations"])
    return OperationalFamilyCorpus(**data)


def verify_operational_family_bundle(output_directory: Path) -> OperationalFamilyBundle:
    output = Path(output_directory)
    if not output.is_dir() or {item.name for item in output.iterdir()} != set(_FILES) | {"hashes.json"}:
        raise OperationalFamilyBundleError("EB0_4H_BUNDLE_FILE_SET_MISMATCH")
    try:
        documents = {name: json.loads((output / name).read_text()) for name in _FILES}
        hashes = json.loads((output / "hashes.json").read_text())
    except Exception as exc:
        raise OperationalFamilyBundleError("EB0_4H_INVALID_JSON") from exc
    if any((output / name).read_bytes() != _json(documents[name]) for name in _FILES) or (output / "hashes.json").read_bytes() != _json(hashes):
        raise OperationalFamilyBundleError("EB0_4H_NONCANONICAL_JSON")
    recorded = hashes.get("files")
    actual = {name: _sha((output / name).read_bytes()) for name in _FILES}
    if hashes.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION or recorded != actual:
        raise OperationalFamilyBundleError("EB0_4H_FILE_DIGEST_MISMATCH")
    bundle_digest = _sha(_json(actual))
    if hashes.get("bundle_digest") != bundle_digest:
        raise OperationalFamilyBundleError("EB0_4H_BUNDLE_DIGEST_MISMATCH")
    try:
        run = documents["run.json"]; accounting = documents["accounting.json"]
        manifests = tuple(_manifest(x) for x in documents["manifests.json"]["manifests"])
        corpora = tuple(_corpus(x) for x in documents["corpora.json"]["corpora"])
        for manifest in manifests:
            verify_operational_family_manifest(manifest, manifest.facts, manifest.nominations)
        verify_operational_family_corpora(corpora, manifests)
        facts = {fact.fact_id: fact for manifest in manifests for fact in manifest.facts}
        selected = accounting["selected_operation_ids"]; qualified = accounting["qualified_operation_ids"]; excluded = accounting["excluded_operations"]
        if set(qualified) & set(excluded) or set(qualified) | set(excluded) != set(selected) or {fact.operation_id for fact in facts.values()} != set(qualified):
            raise OperationalFamilyBundleError("EB0_4H_ACCOUNTING_MISMATCH")
        expected_accounting = {"selected_operation_ids": selected, "qualified_operation_ids": qualified, "excluded_operations": dict(sorted(excluded.items())), "candidate_group_count": sum(m.nomination_count for m in manifests), "fact_count": len(facts), "nomination_count": sum(m.nomination_count for m in manifests), "conflict_count": sum(f.quality_state == "CONFLICTING" for f in facts.values())}
        if accounting != expected_accounting:
            raise OperationalFamilyBundleError("EB0_4H_ACCOUNTING_MISMATCH")
        body = {"schema_version": run["extraction_schema_version"], "selected_operation_ids": selected, "qualified_operation_ids": qualified, "excluded_operations": excluded, "candidate_group_count": accounting["candidate_group_count"], "fact_count": accounting["fact_count"], "nomination_count": accounting["nomination_count"], "conflict_count": accounting["conflict_count"], "manifests": [m.manifest_digest for m in manifests], "corpora": [c.corpus_digest for c in corpora], "input_fingerprint": run["input_fingerprint"]}
        if _sha(_json(body).rstrip(b"\n")) != run["extraction_result_digest"]:
            raise OperationalFamilyBundleError("EB0_4H_RESULT_REPLAY_MISMATCH")
    except OperationalFamilyBundleError:
        raise
    except Exception as exc:
        raise OperationalFamilyBundleError("EB0_4H_CONTENT_REPLAY_FAILED") from exc
    return OperationalFamilyBundle(output, bundle_digest, actual)
