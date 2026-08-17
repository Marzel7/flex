"""PSI0F-F9 pure fixture-only retention bundle and F5 replay adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Mapping

from .operational_family_rematerialization import AUTHORITY_KEYS
from .operational_family_source_materialization import (
    MaterializedOperationalFamilySource,
    OperationalFamilySourceMaterializationError,
    build_operational_family_source_materialization_contract,
    materialize_fixture_operational_family_source,
)


CONTRACT_VERSION = "psi0f-f9.v1"
ENGINEERING_REVISION = "e87b4bf9f378a035b6f7e55c3f627e747af73949"
PSI0F_F8_DIGEST = "a799d62370a17d45c485d94f73dd153a1bf7786feee01e7d74eeb7160260f2de"
PSI0F_F5_DIGEST = "3b950893ef6accc2312817934814a4edf344c583a85c63ba6090bcad9a4d6af1"
DATA_FILES = (
    "contract.json", "cohort.json", "evaluations.json", "runtime.json",
    "candidates.json", "dispositions.json", "vocabulary.json", "accounting.json",
)
BUNDLE_FILES = DATA_FILES + ("hashes.json",)


class OperationalFamilyRetentionBundleError(RuntimeError):
    """Named fail-closed PSI0F-F9 contract violation."""


@dataclass(frozen=True)
class OperationalFamilyRetentionBundleContract:
    contract_version: str
    engineering_revision: str
    psi0f_f8_digest: str
    psi0f_f5_digest: str
    bundle_files: tuple[str, ...]
    fixture_only: bool
    performs_io: bool
    complete_f5_replay_inputs: bool
    lifecycle_grants_nomination_authority: bool
    authority: Mapping[str, bool]
    contract_digest: str


@dataclass(frozen=True)
class OperationalFamilyRetentionBundle:
    files: Mapping[str, bytes]
    bundle_digest: str
    source_digest: str
    contract_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(_canonical(value).rstrip(b"\n")).hexdigest()


def _fail(code: str) -> None:
    raise OperationalFamilyRetentionBundleError(f"PSI0F_F9_{code}")


def build_operational_family_retention_bundle_contract() -> OperationalFamilyRetentionBundleContract:
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0f_f8_digest": PSI0F_F8_DIGEST,
        "psi0f_f5_digest": PSI0F_F5_DIGEST,
        "bundle_files": BUNDLE_FILES,
        "fixture_only": True,
        "performs_io": False,
        "complete_f5_replay_inputs": True,
        "lifecycle_grants_nomination_authority": False,
        "authority": {key: False for key in AUTHORITY_KEYS},
    }
    return OperationalFamilyRetentionBundleContract(**body, contract_digest=_digest(body))


def verify_operational_family_retention_bundle_contract(
    contract: OperationalFamilyRetentionBundleContract,
) -> bool:
    if contract != build_operational_family_retention_bundle_contract():
        _fail("CONTRACT_REPLAY_MISMATCH")
    if (not contract.fixture_only or contract.performs_io or
            not contract.complete_f5_replay_inputs or
            contract.lifecycle_grants_nomination_authority or any(contract.authority.values())):
        _fail("AUTHORITY_DRIFT")
    return True


def _rows(value: object, name: str, sort_key: str) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(row, Mapping) for row in value):
        _fail(f"INVALID_{name.upper()}")
    rows = [dict(row) for row in value]
    try:
        return sorted(rows, key=lambda row: row[sort_key])
    except (KeyError, TypeError):
        _fail(f"INVALID_{name.upper()}")


def _normalized_inputs(*, cohort: object, evaluations: object, runtime: object,
                       candidates: object, dispositions: object,
                       vocabulary: object) -> dict[str, object]:
    cohort_rows = _rows(cohort, "cohort", "position")
    evaluation_rows = _rows(evaluations, "evaluations", "operation_id")
    for row in evaluation_rows:
        if isinstance(row.get("behaviour_observation_ids"), (list, tuple)):
            row["behaviour_observation_ids"] = sorted(row["behaviour_observation_ids"])
    runtime_rows = _rows(runtime, "runtime", "operation_id")
    runtime_rows.sort(key=lambda row: (row.get("operation_id"), row.get("input_digest")))
    for row in runtime_rows:
        for key in ("edge_features", "mechanism_features", "temporal_features"):
            if isinstance(row.get(key), (list, tuple)):
                row[key] = sorted(row[key])
    candidate_rows = _rows(candidates, "candidates", "candidate_id")
    for row in candidate_rows:
        for key in ("population", "supporting_evidence_ids", "supporting_primitive_ids",
                    "supporting_behaviour_observation_ids", "supporting_topology_revision_ids",
                    "missing_evidence", "contradictory_evidence"):
            if isinstance(row.get(key), (list, tuple)):
                row[key] = sorted(row[key])
    disposition_rows = _rows(dispositions, "dispositions", "review_id")
    for row in disposition_rows:
        if isinstance(row.get("operation_ids"), tuple):
            row["operation_ids"] = list(row["operation_ids"])
        if isinstance(row.get("authority"), Mapping):
            row["authority"] = dict(row["authority"])
    if not isinstance(vocabulary, Mapping):
        _fail("INVALID_VOCABULARY")
    vocabulary_row = dict(vocabulary)
    for key in ("roles", "edge", "mechanism", "temporal"):
        if isinstance(vocabulary_row.get(key), (list, tuple)):
            vocabulary_row[key] = sorted(vocabulary_row[key])
    return {
        "cohort": cohort_rows, "evaluations": evaluation_rows, "runtime": runtime_rows,
        "candidates": candidate_rows, "dispositions": disposition_rows,
        "vocabulary": vocabulary_row,
    }


def build_fixture_operational_family_retention_bundle(
    contract: OperationalFamilyRetentionBundleContract, *, cohort: object,
    evaluations: object, runtime: object, candidates: object,
    dispositions: object, vocabulary: object,
) -> OperationalFamilyRetentionBundle:
    verify_operational_family_retention_bundle_contract(contract)
    normalized = _normalized_inputs(
        cohort=cohort, evaluations=evaluations, runtime=runtime, candidates=candidates,
        dispositions=dispositions, vocabulary=vocabulary,
    )
    f5_contract = build_operational_family_source_materialization_contract()
    if f5_contract.contract_digest != contract.psi0f_f5_digest:
        _fail("F5_CONTRACT_DRIFT")
    try:
        source = materialize_fixture_operational_family_source(f5_contract, **normalized)
    except OperationalFamilySourceMaterializationError as exc:
        raise OperationalFamilyRetentionBundleError(f"PSI0F_F9_F5_SOURCE_REJECTED:{exc}") from exc
    documents = {
        "contract.json": {"schema_version": CONTRACT_VERSION, "contract": asdict(contract)},
        **{f"{name}.json": {"schema_version": f"{CONTRACT_VERSION}.{name}", "items": normalized[name]}
           for name in ("cohort", "evaluations", "runtime", "candidates", "dispositions")},
        "vocabulary.json": {"schema_version": f"{CONTRACT_VERSION}.vocabulary", "value": normalized["vocabulary"]},
        "accounting.json": {
            "schema_version": f"{CONTRACT_VERSION}.accounting",
            "operation_count": source.operation_count, "evaluation_count": len(normalized["evaluations"]),
            "runtime_count": source.runtime_count, "candidate_count": len(normalized["candidates"]),
            "disposition_count": len(normalized["dispositions"]),
            "group_count": source.candidate_group_count, "membership_count": source.membership_count,
            "nomination_states": {state: sum(row["nomination_state"] == state for row in normalized["dispositions"])
                                  for state in ("PROPOSED", "SUPPORTED")},
            "source_digest": source.source_digest,
        },
    }
    files = {name: _canonical(documents[name]) for name in DATA_FILES}
    file_hashes = {name: sha256(files[name]).hexdigest() for name in DATA_FILES}
    bundle_digest = _digest(file_hashes)
    files["hashes.json"] = _canonical({
        "schema_version": f"{CONTRACT_VERSION}.hashes", "files": file_hashes,
        "bundle_digest": bundle_digest,
    })
    return OperationalFamilyRetentionBundle(
        files=files, bundle_digest=bundle_digest, source_digest=source.source_digest,
        contract_digest=contract.contract_digest,
    )


def replay_fixture_operational_family_retention_bundle(
    contract: OperationalFamilyRetentionBundleContract, files: Mapping[str, bytes],
) -> MaterializedOperationalFamilySource:
    verify_operational_family_retention_bundle_contract(contract)
    if not isinstance(files, Mapping) or set(files) != set(BUNDLE_FILES):
        _fail("FILE_SET_MISMATCH")
    documents: dict[str, object] = {}
    for name in BUNDLE_FILES:
        payload = files[name]
        if not isinstance(payload, bytes):
            _fail("FILE_BYTES_INVALID")
        try:
            document = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            _fail("JSON_INVALID")
        if payload != _canonical(document):
            _fail("CANONICAL_BYTES_MISMATCH")
        documents[name] = document
    hashes = documents["hashes.json"]
    expected_hashes = {name: sha256(files[name]).hexdigest() for name in DATA_FILES}
    if (not isinstance(hashes, Mapping) or hashes.get("schema_version") != f"{CONTRACT_VERSION}.hashes" or
            hashes.get("files") != expected_hashes or hashes.get("bundle_digest") != _digest(expected_hashes)):
        _fail("HASH_REPLAY_MISMATCH")
    contract_doc = documents["contract.json"]
    expected_contract_doc = json.loads(_canonical({
        "schema_version": CONTRACT_VERSION, "contract": asdict(contract),
    }))
    if contract_doc != expected_contract_doc:
        _fail("CONTRACT_DOCUMENT_MISMATCH")
    def items(name: str) -> object:
        document = documents[f"{name}.json"]
        if not isinstance(document, Mapping) or set(document) != {"schema_version", "items"} or document["schema_version"] != f"{CONTRACT_VERSION}.{name}":
            _fail(f"{name.upper()}_DOCUMENT_MISMATCH")
        return document["items"]
    vocabulary_doc = documents["vocabulary.json"]
    if not isinstance(vocabulary_doc, Mapping) or set(vocabulary_doc) != {"schema_version", "value"} or vocabulary_doc["schema_version"] != f"{CONTRACT_VERSION}.vocabulary":
        _fail("VOCABULARY_DOCUMENT_MISMATCH")
    try:
        source = materialize_fixture_operational_family_source(
            build_operational_family_source_materialization_contract(), cohort=items("cohort"),
            evaluations=items("evaluations"), runtime=items("runtime"), candidates=items("candidates"),
            dispositions=items("dispositions"), vocabulary=vocabulary_doc["value"],
        )
    except OperationalFamilySourceMaterializationError as exc:
        raise OperationalFamilyRetentionBundleError(f"PSI0F_F9_F5_REPLAY_REJECTED:{exc}") from exc
    accounting = documents["accounting.json"]
    expected_accounting = {
        "schema_version": f"{CONTRACT_VERSION}.accounting",
        "operation_count": source.operation_count, "evaluation_count": len(items("evaluations")),
        "runtime_count": source.runtime_count, "candidate_count": len(items("candidates")),
        "disposition_count": len(items("dispositions")), "group_count": source.candidate_group_count,
        "membership_count": source.membership_count,
        "nomination_states": {state: sum(row["nomination_state"] == state for row in items("dispositions"))
                              for state in ("PROPOSED", "SUPPORTED")},
        "source_digest": source.source_digest,
    }
    if accounting != expected_accounting:
        _fail("ACCOUNTING_REPLAY_MISMATCH")
    return source
