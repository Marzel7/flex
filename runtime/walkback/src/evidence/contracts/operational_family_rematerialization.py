"""PSI0F-F1 deterministic fixture-only EB0.4H rematerialization boundary."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from typing import Callable, Mapping, Sequence

from .operational_family_adapters import (
    ADAPTER_VERSION,
    SOURCE_SCHEMA_VERSION,
    adapt_normalized_operation_runtime,
)
from .operational_family_bundle import (
    OperationalFamilyBundle,
    verify_operational_family_bundle,
    write_operational_family_bundle,
)
from .operational_family_extractor import (
    EXTRACTOR_SCHEMA_VERSION,
    OperationalFamilyExtraction,
    extract_operational_families,
)
from .operational_family_nomination import CONTRACT_VERSION, NOMINATION_STATES


CONTRACT_VERSION_F1 = "psi0f-f1.v1"
SOURCE_SCHEMA_VERSION_F1 = "psi0f-f1.logical-source.v1"
ENGINEERING_REVISION = "4215f899b510f7d366c21702ce6cef63c6882878"
PSI0F_D_ADAPTER_DIGEST = "0d93b35b98c30d55d03ea8c8c787ba213442ab70b43499b989bfe8f2a18f990e"
PSI0F_B_CONTRACT_DIGEST = "042568c68b0eb86ef41bc65037568d65214d02d9d9753988adf88c86c14222ef"
PSI0E_H_CLOSURE_DIGEST = "ae0994f1f3647f53fbcd3cabf0c8e46efa26b504805fecb95e8d2851d9e98c16"
PSI0E_BUNDLE_DIGEST = "88c7de3156a4dc07b3c3b2461b4e1e37e85d5bd06d217d904472e4f4bc6f4d9c"
PROVENANCE_CLASS = "FROZEN_SYNTHETIC_IMMUTABLE_OPERATIONAL_FAMILY_LOGICAL_SOURCE"
AUTHORITY_KEYS = (
    "activation", "attribution", "deployment", "integration",
    "operator_identity", "policy", "ranking",
)
COMPONENTS = (
    "nomination_candidates", "normalized_operation_runtime", "operation_cohort",
)
COHORT_FIELDS = frozenset(("position", "operation_id"))
RUNTIME_FIELDS = frozenset((
    "schema_version", "identity_basis", "operation_id", "primary_role",
    "contract_id", "contract_version", "module_id", "module_version",
    "topology_revision_id", "behaviour_observation_id", "input_digest",
    "edge_features", "mechanism_features", "temporal_features",
    "quality_state", "completeness_state", "conflict_group_id",
))
MEMBERSHIP_FIELDS = frozenset(("group_id", "position", "operation_id", "nomination_state"))
VOCABULARY_FIELDS = frozenset(("roles", "edge", "mechanism", "temporal"))
_FORBIDDEN_SEMANTICS = ("operator", "owner", "identity", "attribution", "score", "rank", "policy", "profit", "cashflow")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class OperationalFamilyRematerializationError(RuntimeError):
    """Named fail-closed PSI0F-F1 boundary violation."""


@dataclass(frozen=True)
class OperationalFamilyRematerializationContract:
    contract_version: str
    source_schema_version: str
    engineering_revision: str
    psi0f_d_adapter_digest: str
    psi0f_b_contract_digest: str
    psi0e_h_closure_digest: str
    psi0e_bundle_digest: str
    eb0_4_contract_version: str
    eb0_4_adapter_version: str
    eb0_4_source_schema_version: str
    eb0_4_extractor_schema_version: str
    source_provenance_class: str
    component_names: tuple[str, ...]
    fixture_only: bool
    retries_allowed: bool
    topology_only_support_allowed: bool
    implicit_membership_allowed: bool
    retains_source_values_outside_boundary: bool
    authority: Mapping[str, bool]
    contract_digest: str


@dataclass(frozen=True)
class _VerifiedOperationalFamilySource:
    document: Mapping[str, object]
    source_digest: str


@dataclass(frozen=True)
class OperationalFamilyRematerialization:
    output_directory: Path
    source_digest: str
    extraction_result_digest: str
    bundle_digest: str
    file_digests: Mapping[str, str]
    contract_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(_canonical(value).rstrip(b"\n")).hexdigest()


def _fail(code: str) -> None:
    raise OperationalFamilyRematerializationError(f"PSI0F_F1_{code}")


def build_operational_family_rematerialization_contract() -> OperationalFamilyRematerializationContract:
    body = {
        "contract_version": CONTRACT_VERSION_F1,
        "source_schema_version": SOURCE_SCHEMA_VERSION_F1,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0f_d_adapter_digest": PSI0F_D_ADAPTER_DIGEST,
        "psi0f_b_contract_digest": PSI0F_B_CONTRACT_DIGEST,
        "psi0e_h_closure_digest": PSI0E_H_CLOSURE_DIGEST,
        "psi0e_bundle_digest": PSI0E_BUNDLE_DIGEST,
        "eb0_4_contract_version": CONTRACT_VERSION,
        "eb0_4_adapter_version": ADAPTER_VERSION,
        "eb0_4_source_schema_version": SOURCE_SCHEMA_VERSION,
        "eb0_4_extractor_schema_version": EXTRACTOR_SCHEMA_VERSION,
        "source_provenance_class": PROVENANCE_CLASS,
        "component_names": COMPONENTS,
        "fixture_only": True,
        "retries_allowed": False,
        "topology_only_support_allowed": False,
        "implicit_membership_allowed": False,
        "retains_source_values_outside_boundary": False,
        "authority": {key: False for key in AUTHORITY_KEYS},
    }
    serialized = {key: list(value) if isinstance(value, tuple) else value for key, value in body.items()}
    return OperationalFamilyRematerializationContract(**body, contract_digest=_digest(serialized))


def verify_operational_family_rematerialization_contract(
    contract: OperationalFamilyRematerializationContract,
) -> bool:
    if contract != build_operational_family_rematerialization_contract():
        _fail("CONTRACT_REPLAY_MISMATCH")
    if (not contract.fixture_only or contract.retries_allowed or
            contract.topology_only_support_allowed or contract.implicit_membership_allowed or
            contract.retains_source_values_outside_boundary or any(contract.authority.values())):
        _fail("AUTHORITY_DRIFT")
    return True


def _record(record: object, fields: frozenset[str], code: str) -> dict[str, object]:
    if not isinstance(record, Mapping) or frozenset(record) != fields:
        _fail(f"{code}_SCHEMA_DRIFT")
    return dict(record)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(code)
    return value.strip()


def _normalized_components(
    cohort: Sequence[object], runtime: Sequence[object], memberships: Sequence[object],
    vocabulary: Mapping[str, object],
) -> dict[str, list[dict[str, object]]]:
    if not isinstance(vocabulary, Mapping) or frozenset(vocabulary) != VOCABULARY_FIELDS:
        _fail("VOCABULARY_SCHEMA_DRIFT")
    normalized_vocabulary: dict[str, tuple[str, ...]] = {}
    for name in sorted(VOCABULARY_FIELDS):
        values = vocabulary[name]
        if not isinstance(values, (list, tuple)) or not values:
            _fail("INVALID_VOCABULARY")
        normalized = tuple(sorted(_text(value, "INVALID_DESCRIPTOR") for value in values))
        if len(normalized) != len(set(normalized)) or any(
            forbidden in value.lower() for value in normalized for forbidden in _FORBIDDEN_SEMANTICS
        ):
            _fail("INVALID_VOCABULARY")
        normalized_vocabulary[name] = normalized
    if not isinstance(cohort, (list, tuple)) or not isinstance(runtime, (list, tuple)) or not isinstance(memberships, (list, tuple)):
        _fail("COMPONENT_TYPE_INVALID")
    cohort_rows = [_record(item, COHORT_FIELDS, "COHORT") for item in cohort]
    if not cohort_rows:
        _fail("EMPTY_COHORT")
    cohort_rows.sort(key=lambda item: item["position"] if isinstance(item["position"], int) else -1)
    positions = [item["position"] for item in cohort_rows]
    operation_ids = [_text(item["operation_id"], "INVALID_OPERATION_ID") for item in cohort_rows]
    if positions != list(range(len(cohort_rows))) or len(operation_ids) != len(set(operation_ids)):
        _fail("INVALID_COHORT_ORDER")
    for item, operation_id in zip(cohort_rows, operation_ids):
        item["operation_id"] = operation_id

    runtime_rows = [_record(item, RUNTIME_FIELDS, "RUNTIME") for item in runtime]
    if not runtime_rows:
        _fail("EMPTY_RUNTIME")
    for item in runtime_rows:
        operation_id = _text(item["operation_id"], "INVALID_OPERATION_ID")
        item["operation_id"] = operation_id
        if operation_id not in set(operation_ids):
            _fail("ORPHAN_RUNTIME")
        if item["primary_role"] not in normalized_vocabulary["roles"]:
            _fail("UNKNOWN_ROLE")
        for field, vocabulary_name in (("edge_features", "edge"), ("mechanism_features", "mechanism"), ("temporal_features", "temporal")):
            values = item[field]
            if not isinstance(values, (list, tuple)) or any(value not in normalized_vocabulary[vocabulary_name] for value in values):
                _fail("UNKNOWN_DESCRIPTOR")
        try:
            facts = adapt_normalized_operation_runtime(item)
        except Exception as exc:
            raise OperationalFamilyRematerializationError("PSI0F_F1_RUNTIME_CONTRACT_REJECTED") from exc
        if not facts or all(not fact.mechanism_features and not fact.temporal_features for fact in facts):
            _fail("TOPOLOGY_ONLY_SUPPORT")
    runtime_rows.sort(key=lambda item: (str(item["operation_id"]), str(item["input_digest"])))
    runtime_keys = [(item["operation_id"], item["input_digest"]) for item in runtime_rows]
    if len(runtime_keys) != len(set(runtime_keys)):
        _fail("DUPLICATE_RUNTIME")

    membership_rows = [_record(item, MEMBERSHIP_FIELDS, "MEMBERSHIP") for item in memberships]
    if not membership_rows:
        _fail("EMPTY_MEMBERSHIP")
    membership_rows.sort(key=lambda item: (str(item["group_id"]), item["position"] if isinstance(item["position"], int) else -1, str(item["operation_id"])))
    groups: dict[str, list[dict[str, object]]] = {}
    for item in membership_rows:
        group_id = _text(item["group_id"], "INVALID_GROUP_ID")
        operation_id = _text(item["operation_id"], "INVALID_OPERATION_ID")
        state = item["nomination_state"]
        if operation_id not in set(operation_ids):
            _fail("ORPHAN_MEMBERSHIP")
        if state not in NOMINATION_STATES:
            _fail("INVALID_NOMINATION_STATE")
        item.update(group_id=group_id, operation_id=operation_id)
        groups.setdefault(group_id, []).append(item)
    for rows in groups.values():
        if ([item["position"] for item in rows] != list(range(len(rows))) or len(rows) < 2 or
                len({item["operation_id"] for item in rows}) != len(rows) or
                len({item["nomination_state"] for item in rows}) != 1):
            _fail("AMBIGUOUS_CANDIDATE_GROUP")
    return {
        "operation_cohort": cohort_rows,
        "normalized_operation_runtime": runtime_rows,
        "nomination_candidates": membership_rows,
    }


def build_immutable_operational_family_source(
    contract: OperationalFamilyRematerializationContract,
    *, cohort: Sequence[object], runtime: Sequence[object], memberships: Sequence[object],
    vocabulary: Mapping[str, object],
) -> bytes:
    verify_operational_family_rematerialization_contract(contract)
    canonical_vocabulary = {key: sorted(vocabulary[key]) for key in sorted(VOCABULARY_FIELDS)} if isinstance(vocabulary, Mapping) and frozenset(vocabulary) == VOCABULARY_FIELDS else vocabulary
    components = _normalized_components(cohort, runtime, memberships, canonical_vocabulary)
    component_digests = {key: _digest(components[key]) for key in COMPONENTS}
    body = {
        "schema_version": contract.source_schema_version,
        "contract_digest": contract.contract_digest,
        "engineering_revision": contract.engineering_revision,
        "provenance_class": contract.source_provenance_class,
        "fixture_only": True,
        "vocabulary": canonical_vocabulary,
        "components": components,
        "component_digests": component_digests,
        "accounting": {
            "cohort_count": len(components["operation_cohort"]),
            "runtime_count": len(components["normalized_operation_runtime"]),
            "membership_count": len(components["nomination_candidates"]),
            "candidate_group_count": len({item["group_id"] for item in components["nomination_candidates"]}),
        },
        "authority": {key: False for key in AUTHORITY_KEYS},
    }
    return _canonical({**body, "source_digest": _digest(body)})


def _verify_immutable_operational_family_source(
    contract: OperationalFamilyRematerializationContract, payload: bytes,
) -> _VerifiedOperationalFamilySource:
    verify_operational_family_rematerialization_contract(contract)
    if not isinstance(payload, bytes):
        _fail("SOURCE_BYTES_REQUIRED")
    try:
        document = json.loads(payload)
    except Exception as exc:
        raise OperationalFamilyRematerializationError("PSI0F_F1_SOURCE_INVALID_JSON") from exc
    if payload != _canonical(document):
        _fail("SOURCE_NONCANONICAL")
    expected = frozenset((
        "schema_version", "contract_digest", "engineering_revision", "provenance_class",
        "fixture_only", "vocabulary", "components", "component_digests", "accounting", "authority", "source_digest",
    ))
    if not isinstance(document, Mapping) or frozenset(document) != expected:
        _fail("SOURCE_SCHEMA_DRIFT")
    if (document["schema_version"] != contract.source_schema_version or
            document["contract_digest"] != contract.contract_digest or
            document["engineering_revision"] != contract.engineering_revision or
            document["provenance_class"] != contract.source_provenance_class or
            document["fixture_only"] is not True):
        _fail("SOURCE_LINEAGE_DRIFT")
    if document["authority"] != {key: False for key in AUTHORITY_KEYS}:
        _fail("AUTHORITY_DRIFT")
    components = document["components"]
    if not isinstance(components, Mapping) or frozenset(components) != frozenset(COMPONENTS):
        _fail("COMPONENT_SET_DRIFT")
    vocabulary = document["vocabulary"]
    if not isinstance(vocabulary, Mapping) or frozenset(vocabulary) != VOCABULARY_FIELDS:
        _fail("VOCABULARY_SCHEMA_DRIFT")
    canonical_vocabulary = {key: sorted(vocabulary[key]) if isinstance(vocabulary[key], (list, tuple)) else vocabulary[key] for key in sorted(VOCABULARY_FIELDS)}
    if vocabulary != canonical_vocabulary:
        _fail("VOCABULARY_ORDER_DRIFT")
    rebuilt = _normalized_components(
        components["operation_cohort"], components["normalized_operation_runtime"], components["nomination_candidates"], vocabulary,
    )
    if components != rebuilt:
        _fail("SOURCE_ORDER_DRIFT")
    component_digests = {key: _digest(rebuilt[key]) for key in COMPONENTS}
    if document["component_digests"] != component_digests:
        _fail("COMPONENT_DIGEST_DRIFT")
    accounting = {
        "cohort_count": len(rebuilt["operation_cohort"]),
        "runtime_count": len(rebuilt["normalized_operation_runtime"]),
        "membership_count": len(rebuilt["nomination_candidates"]),
        "candidate_group_count": len({item["group_id"] for item in rebuilt["nomination_candidates"]}),
    }
    if document["accounting"] != accounting:
        _fail("ACCOUNTING_DRIFT")
    body = {key: document[key] for key in document if key != "source_digest"}
    source_digest = _digest(body)
    if document["source_digest"] != source_digest or not _DIGEST.fullmatch(source_digest):
        _fail("SOURCE_DIGEST_DRIFT")
    return _VerifiedOperationalFamilySource(document=document, source_digest=source_digest)


def verify_immutable_operational_family_source(
    contract: OperationalFamilyRematerializationContract, payload: bytes,
) -> str:
    """Replay-verify caller bytes while returning only their immutable identity."""
    return _verify_immutable_operational_family_source(contract, payload).source_digest


def _write_fixture(source: _VerifiedOperationalFamilySource, path: Path) -> None:
    components = source.document["components"]
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            "CREATE TABLE operation_cohort(position INTEGER,operation_id TEXT);"
            "CREATE TABLE normalized_operation_runtime(schema_version TEXT,identity_basis TEXT,operation_id TEXT,primary_role TEXT,contract_id TEXT,contract_version TEXT,module_id TEXT,module_version TEXT,topology_revision_id TEXT,behaviour_observation_id TEXT,input_digest TEXT,edge_features_json TEXT,mechanism_features_json TEXT,temporal_features_json TEXT,quality_state TEXT,completeness_state TEXT,conflict_group_id TEXT);"
            "CREATE TABLE nomination_candidates(group_id TEXT,position INTEGER,operation_id TEXT,nomination_state TEXT);"
        )
        for item in components["operation_cohort"]:
            connection.execute("INSERT INTO operation_cohort VALUES (?,?)", (item["position"], item["operation_id"]))
        for item in components["normalized_operation_runtime"]:
            connection.execute(
                "INSERT INTO normalized_operation_runtime VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item["schema_version"], item["identity_basis"], item["operation_id"], item["primary_role"],
                    item["contract_id"], item["contract_version"], item["module_id"], item["module_version"],
                    item["topology_revision_id"], item["behaviour_observation_id"], item["input_digest"],
                    json.dumps(item["edge_features"], sort_keys=True, separators=(",", ":")),
                    json.dumps(item["mechanism_features"], sort_keys=True, separators=(",", ":")),
                    json.dumps(item["temporal_features"], sort_keys=True, separators=(",", ":")),
                    item["quality_state"], item["completeness_state"], item["conflict_group_id"],
                ),
            )
        for item in components["nomination_candidates"]:
            connection.execute("INSERT INTO nomination_candidates VALUES (?,?,?,?)", (item["group_id"], item["position"], item["operation_id"], item["nomination_state"]))
        connection.commit()
    finally:
        connection.close()


def _fsync_tree(path: Path, fsync: Callable[[int], None]) -> None:
    for item in sorted(path.iterdir(), key=lambda value: value.name):
        descriptor = os.open(item, os.O_RDONLY)
        try:
            fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(path, os.O_RDONLY)
    try:
        fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_absent(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        _fail("OUTPUT_NOT_NEW")
    os.rename(source, target)


def rematerialize_operational_family_bundle(
    contract: OperationalFamilyRematerializationContract,
    source_payload: bytes,
    output_directory: Path,
    *,
    run_id: str,
    engineering_revision: str,
    extractor: Callable[..., OperationalFamilyExtraction] = extract_operational_families,
    publisher: Callable[..., OperationalFamilyBundle] = write_operational_family_bundle,
    verifier: Callable[[Path], OperationalFamilyBundle] = verify_operational_family_bundle,
    fsync: Callable[[int], None] = os.fsync,
    rename: Callable[[Path, Path], None] = _rename_absent,
) -> OperationalFamilyRematerialization:
    verify_operational_family_rematerialization_contract(contract)
    source = _verify_immutable_operational_family_source(contract, source_payload)
    output = Path(output_directory)
    if (not output.is_absolute() or not output.parent.is_dir() or output.parent.is_symlink() or
            output.exists() or output.is_symlink()):
        _fail("OUTPUT_NOT_NEW")
    root = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    published = False
    rename_started = False
    staging_bundle = root / "bundle"
    try:
        fixture = root / "source.sqlite"
        _write_fixture(source, fixture)
        result = extractor(fixture)
        published_bundle = publisher(result, staging_bundle, run_id=run_id, engineering_revision=engineering_revision)
        verified_staging = verifier(staging_bundle)
        if published_bundle != verified_staging:
            _fail("STAGING_REPLAY_MISMATCH")
        _fsync_tree(staging_bundle, fsync)
        rename_started = True
        rename(staging_bundle, output)
        published = True
        parent_descriptor = os.open(output.parent, os.O_RDONLY)
        try:
            fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        verified_output = verifier(output)
        if verified_output.bundle_digest != published_bundle.bundle_digest or verified_output.file_digests != published_bundle.file_digests:
            _fail("POST_PUBLICATION_REPLAY_MISMATCH")
        return OperationalFamilyRematerialization(
            output_directory=output,
            source_digest=source.source_digest,
            extraction_result_digest=result.result_digest,
            bundle_digest=verified_output.bundle_digest,
            file_digests=verified_output.file_digests,
            contract_digest=contract.contract_digest,
        )
    except Exception:
        if (published or (rename_started and not staging_bundle.exists())) and output.is_dir() and not output.is_symlink():
            shutil.rmtree(output)
        raise
    finally:
        shutil.rmtree(root, ignore_errors=True)
