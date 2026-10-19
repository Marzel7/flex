"""EB0.4C exact frozen normalized-runtime adapters into EB0.4A."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Mapping, Tuple

from .operational_family_nomination import (
    OperationBehaviourFact,
    OperationalFamilyNominationError,
    project_operation_behaviour_facts,
)


ADAPTER_VERSION = "eb0.4c.v1"
SOURCE_SCHEMA_VERSION = "eb0.4c.normalized-runtime.v1"
_FIELDS = frozenset({
    "schema_version", "identity_basis", "operation_id", "primary_role",
    "contract_id", "contract_version", "module_id", "module_version",
    "topology_revision_id", "behaviour_observation_id", "input_digest",
    "edge_features", "mechanism_features", "temporal_features",
    "quality_state", "completeness_state", "conflict_group_id",
})
_FORBIDDEN = ("wallet", "subject", "operator", "owner", "identity_confidence", "score", "rank", "policy")


class OperationalFamilyAdapterError(ValueError):
    """Named fail-closed EB0.4C adapter error."""


def _fail(code: str) -> None:
    raise OperationalFamilyAdapterError(f"EB0_4C_{code}")


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        _fail(f"INVALID_{field.upper()}")
    return value.strip()


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(payload.encode()).hexdigest()


def adapt_normalized_operation_runtime(record: Mapping[str, object]) -> Tuple[OperationBehaviourFact, ...]:
    if not isinstance(record, Mapping) or frozenset(record) != _FIELDS:
        _fail("SCHEMA_DRIFT")
    if any(any(term in str(key).lower() for term in _FORBIDDEN) for key in record):
        _fail("FORBIDDEN_FIELD")
    if record.get("schema_version") != SOURCE_SCHEMA_VERSION:
        _fail("SCHEMA_VERSION_MISMATCH")
    if record.get("identity_basis") != "PLATFORM_OPERATION_ID":
        _fail("IDENTITY_BASIS_REJECTED")
    operation_id = _text(record, "operation_id")
    role = _text(record, "primary_role")
    contract_id = _text(record, "contract_id")
    contract_version = _text(record, "contract_version")
    module_id = _text(record, "module_id")
    module_version = _text(record, "module_version")
    topology_id = _text(record, "topology_revision_id")
    behaviour_id = _text(record, "behaviour_observation_id")
    input_digest = _text(record, "input_digest")
    source_body = {
        "adapter_version": ADAPTER_VERSION,
        "schema_version": SOURCE_SCHEMA_VERSION,
        "operation_id": operation_id,
        "contract_id": contract_id,
        "contract_version": contract_version,
        "module_id": module_id,
        "module_version": module_version,
        "topology_revision_id": topology_id,
        "behaviour_observation_id": behaviour_id,
        "input_digest": input_digest,
    }
    projected = {
        "operation_id": operation_id,
        "role": role,
        "edge_features": record["edge_features"],
        "mechanism_features": record["mechanism_features"],
        "temporal_features": record["temporal_features"],
        "source": f"operation_runtime:{contract_id}:{module_id}",
        "source_version": f"{contract_version}:{module_version}:{ADAPTER_VERSION}",
        "source_record_digest": _digest(source_body),
        "quality_state": record["quality_state"],
        "completeness_state": record["completeness_state"],
        "conflict_group_id": record["conflict_group_id"],
    }
    try:
        return project_operation_behaviour_facts([projected])
    except OperationalFamilyNominationError as exc:
        raise OperationalFamilyAdapterError(f"EB0_4C_CONTRACT_REJECTED:{exc}") from exc
