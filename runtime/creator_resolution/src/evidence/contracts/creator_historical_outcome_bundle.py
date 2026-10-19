"""Immutable EB0.2H bundles for bounded EB0.2G extraction results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping, Tuple

from .creator_historical_outcome import CreatorHistoricalOutcomeFact
from .creator_historical_outcome_corpus import (
    CreatorHistoricalOutcomeCorpus,
    verify_creator_historical_outcome_corpora,
)
from .creator_historical_outcome_extractor import (
    EXTRACTOR_SCHEMA_VERSION,
    CreatorHistoricalOutcomeExtraction,
    OutcomePolicy,
)
from .creator_historical_outcome_manifest import (
    CreatorHistoricalOutcomeManifest,
    verify_creator_historical_outcome_manifest,
)


BUNDLE_SCHEMA_VERSION = "eb0.2h.v1"
_FILES = ("run.json", "accounting.json", "manifests.json", "corpora.json")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CreatorHistoricalOutcomeBundleError(RuntimeError):
    """Named fail-closed error for unsafe or non-replayable EB0.2H bundles."""


@dataclass(frozen=True)
class CreatorHistoricalOutcomeBundle:
    output_directory: Path
    bundle_digest: str
    file_digests: Mapping[str, str]


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8") + b"\n"


def _sha(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _result_body(
    *,
    selected_mints: object,
    qualified_mints: object,
    excluded_mints: object,
    policies: object,
    manifest_digests: object,
    corpus_digests: object,
    input_fingerprint: object,
) -> dict[str, object]:
    return {
        "schema_version": EXTRACTOR_SCHEMA_VERSION,
        "selected_mints": selected_mints,
        "qualified_mints": qualified_mints,
        "excluded_mints": excluded_mints,
        "policies": policies,
        "manifest_digests": manifest_digests,
        "corpus_digests": corpus_digests,
        "input_fingerprint": input_fingerprint,
    }


def _validate(
    result: CreatorHistoricalOutcomeExtraction,
    *,
    run_id: str,
    engineering_revision: str,
    policies: Tuple[OutcomePolicy, ...],
) -> list[dict[str, object]]:
    if not _RUN_ID.fullmatch(run_id):
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_INVALID_RUN_ID")
    if not _REVISION.fullmatch(engineering_revision):
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_INVALID_ENGINEERING_REVISION")
    if result.schema_version != EXTRACTOR_SCHEMA_VERSION:
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_EXTRACTION_SCHEMA_MISMATCH")
    if not _DIGEST.fullmatch(result.input_fingerprint) or not _DIGEST.fullmatch(result.result_digest):
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_INVALID_RESULT_DIGEST")
    policy_payload = [asdict(item) for item in policies]
    body = _result_body(
        selected_mints=result.selected_mints,
        qualified_mints=result.qualified_mints,
        excluded_mints=dict(sorted(result.excluded_mints.items())),
        policies=policy_payload,
        manifest_digests=[item.manifest_digest for item in result.manifests],
        corpus_digests=[item.corpus_digest for item in result.corpora],
        input_fingerprint=result.input_fingerprint,
    )
    if _sha(_json_bytes(body).rstrip(b"\n")) != result.result_digest:
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_POLICY_OR_RESULT_MISMATCH")
    return policy_payload


def _prepare_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_OUTPUT_NOT_EMPTY")
        return
    try:
        path.mkdir(mode=0o700)
    except OSError as exc:
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_OUTPUT_CREATE_FAILED") from exc


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_OVERWRITE_REJECTED") from exc


def write_creator_historical_outcome_bundle(
    result: CreatorHistoricalOutcomeExtraction,
    output_directory: Path,
    *,
    run_id: str,
    engineering_revision: str,
    policies: Tuple[OutcomePolicy, ...],
) -> CreatorHistoricalOutcomeBundle:
    policy_payload = _validate(
        result, run_id=run_id, engineering_revision=engineering_revision, policies=policies
    )
    output = Path(output_directory)
    _prepare_directory(output)
    run = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "extraction_schema_version": result.schema_version,
        "run_id": run_id,
        "engineering_revision": engineering_revision,
        "input_fingerprint": result.input_fingerprint,
        "extraction_result_digest": result.result_digest,
        "policies": policy_payload,
    }
    accounting = {
        "selected_mints": list(result.selected_mints),
        "qualified_mints": list(result.qualified_mints),
        "excluded_mints": dict(sorted(result.excluded_mints.items())),
        "policy_count": result.policy_count,
        "fact_count": result.fact_count,
        "eligible_denominator_count": result.eligible_denominator_count,
        "unknown_count": result.unknown_count,
        "conflicting_fact_count": result.conflicting_fact_count,
    }
    payloads = {
        "run.json": _json_bytes(run),
        "accounting.json": _json_bytes(accounting),
        "manifests.json": _json_bytes({"manifests": [item.to_dict() for item in result.manifests]}),
        "corpora.json": _json_bytes({"corpora": [item.to_dict() for item in result.corpora]}),
    }
    digests = {name: _sha(payloads[name]) for name in _FILES}
    bundle_digest = _sha(_json_bytes(digests))
    hashes = _json_bytes({
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "files": digests,
        "bundle_digest": bundle_digest,
    })
    for name in _FILES:
        _write_exclusive(output / name, payloads[name])
    _write_exclusive(output / "hashes.json", hashes)
    return CreatorHistoricalOutcomeBundle(output, bundle_digest, digests)


def _fact(value: Mapping[str, object]) -> CreatorHistoricalOutcomeFact:
    return CreatorHistoricalOutcomeFact(**value)


def _manifest(value: Mapping[str, object]) -> CreatorHistoricalOutcomeManifest:
    data = dict(value)
    data["facts"] = tuple(_fact(item) for item in data["facts"])
    return CreatorHistoricalOutcomeManifest(**data)


def _corpus(value: Mapping[str, object]) -> CreatorHistoricalOutcomeCorpus:
    data = dict(value)
    data["source_manifest_digests"] = tuple(data["source_manifest_digests"])
    data["facts"] = tuple(_fact(item) for item in data["facts"])
    return CreatorHistoricalOutcomeCorpus(**data)


def verify_creator_historical_outcome_bundle(
    output_directory: Path,
) -> CreatorHistoricalOutcomeBundle:
    output = Path(output_directory)
    expected = set(_FILES) | {"hashes.json"}
    if not output.is_dir() or {item.name for item in output.iterdir()} != expected:
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_BUNDLE_FILE_SET_MISMATCH")
    try:
        documents = {
            name: json.loads((output / name).read_text(encoding="utf-8"))
            for name in _FILES
        }
        hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_INVALID_JSON") from exc
    if hashes.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION:
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_BUNDLE_SCHEMA_MISMATCH")
    for name, document in documents.items():
        if (output / name).read_bytes() != _json_bytes(document):
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_NONCANONICAL_JSON")
    if (output / "hashes.json").read_bytes() != _json_bytes(hashes):
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_NONCANONICAL_JSON")
    recorded = hashes.get("files")
    if not isinstance(recorded, dict) or set(recorded) != set(_FILES):
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_HASH_FILE_SET_MISMATCH")
    actual = {name: _sha((output / name).read_bytes()) for name in _FILES}
    if recorded != actual:
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_FILE_DIGEST_MISMATCH")
    bundle_digest = _sha(_json_bytes(actual))
    if hashes.get("bundle_digest") != bundle_digest:
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_BUNDLE_DIGEST_MISMATCH")
    try:
        run = documents["run.json"]
        accounting = documents["accounting.json"]
        if run.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION:
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_BUNDLE_SCHEMA_MISMATCH")
        if run.get("extraction_schema_version") != EXTRACTOR_SCHEMA_VERSION:
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_EXTRACTION_SCHEMA_MISMATCH")
        if not _RUN_ID.fullmatch(run.get("run_id", "")):
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_INVALID_RUN_ID")
        if not _REVISION.fullmatch(run.get("engineering_revision", "")):
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_INVALID_ENGINEERING_REVISION")
        if not _DIGEST.fullmatch(run.get("input_fingerprint", "")) or not _DIGEST.fullmatch(
            run.get("extraction_result_digest", "")
        ):
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_INVALID_RESULT_DIGEST")
        if not isinstance(run.get("policies"), list):
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_INVALID_POLICIES")
        manifests = tuple(_manifest(item) for item in documents["manifests.json"]["manifests"])
        corpora = tuple(_corpus(item) for item in documents["corpora.json"]["corpora"])
        for manifest in manifests:
            verify_creator_historical_outcome_manifest(manifest, manifest.facts)
        if manifests:
            verify_creator_historical_outcome_corpora(corpora, manifests)
        elif corpora:
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_EMPTY_MANIFEST_CORPUS_MISMATCH")
        all_facts = [fact for manifest in manifests for fact in manifest.facts]
        selected = accounting.get("selected_mints")
        qualified = accounting.get("qualified_mints")
        excluded = accounting.get("excluded_mints")
        if (
            not isinstance(selected, list)
            or not isinstance(qualified, list)
            or not isinstance(excluded, dict)
            or len(set(selected)) != len(selected)
            or len(set(qualified)) != len(qualified)
            or set(qualified) & set(excluded)
            or set(qualified) | set(excluded) != set(selected)
            or {fact.mint for fact in all_facts} != set(qualified)
        ):
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_ACCOUNTING_MISMATCH")
        if accounting != {
            "selected_mints": selected,
            "qualified_mints": qualified,
            "excluded_mints": dict(sorted(excluded.items())),
            "policy_count": len(run["policies"]),
            "fact_count": len(all_facts),
            "eligible_denominator_count": sum(item.denominator_eligible for item in all_facts),
            "unknown_count": sum(item.outcome_state == "UNKNOWN" for item in all_facts),
            "conflicting_fact_count": sum(item.quality_state == "CONFLICTING" for item in all_facts),
        }:
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_ACCOUNTING_MISMATCH")
        result_body = _result_body(
            selected_mints=accounting["selected_mints"],
            qualified_mints=accounting["qualified_mints"],
            excluded_mints=accounting["excluded_mints"],
            policies=run["policies"],
            manifest_digests=[item.manifest_digest for item in manifests],
            corpus_digests=[item.corpus_digest for item in corpora],
            input_fingerprint=run["input_fingerprint"],
        )
        if _sha(_json_bytes(result_body).rstrip(b"\n")) != run["extraction_result_digest"]:
            raise CreatorHistoricalOutcomeBundleError("EB0_2H_RESULT_REPLAY_MISMATCH")
    except CreatorHistoricalOutcomeBundleError:
        raise
    except Exception as exc:
        raise CreatorHistoricalOutcomeBundleError("EB0_2H_CONTENT_REPLAY_FAILED") from exc
    return CreatorHistoricalOutcomeBundle(output, bundle_digest, actual)
