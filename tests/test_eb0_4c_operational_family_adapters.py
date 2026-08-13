import json
from pathlib import Path

import pytest

from src.evidence.contracts.operational_family_adapters import (
    OperationalFamilyAdapterError,
    adapt_normalized_operation_runtime,
)


FIXTURE = Path(__file__).parent / "fixtures" / "eb0_4c_normalized_operation_runtime.json"


def _record(): return json.loads(FIXTURE.read_text())


def test_exact_runtime_export_maps_deterministically_with_role_primary():
    first = adapt_normalized_operation_runtime(_record())
    second = adapt_normalized_operation_runtime(_record())
    assert first == second
    fact = first[0]
    assert fact.operation_id == "operation-alpha"
    assert fact.role == "PROVISIONING_OPERATION"
    assert fact.edge_features[0].startswith("SUBPROVIDER->")
    assert fact.mechanism_features == ("WSOL_WRAP_CLOSE",)
    assert fact.source.startswith("operation_runtime:watchtower_v1")


@pytest.mark.parametrize("field,value,match", [
    ("schema_version", "bad", "SCHEMA_VERSION_MISMATCH"),
    ("identity_basis", "WALLET_SUBJECT", "IDENTITY_BASIS_REJECTED"),
    ("operation_id", "", "INVALID_OPERATION_ID"),
])
def test_version_identity_and_required_bindings_fail_closed(field, value, match):
    record = _record(); record[field] = value
    with pytest.raises(OperationalFamilyAdapterError, match=match):
        adapt_normalized_operation_runtime(record)


@pytest.mark.parametrize("field", ["wallet", "subjects", "operator_identity", "confidence_score", "rank", "policy"])
def test_ambiguous_identity_scoring_and_policy_fields_are_rejected(field):
    record = _record(); record[field] = "forbidden"
    with pytest.raises(OperationalFamilyAdapterError, match="SCHEMA_DRIFT|FORBIDDEN_FIELD"):
        adapt_normalized_operation_runtime(record)


def test_generic_measured_values_and_topology_only_exports_fail_closed():
    record = _record(); record["measured_values"] = {"anything": "goes"}
    with pytest.raises(OperationalFamilyAdapterError, match="SCHEMA_DRIFT"):
        adapt_normalized_operation_runtime(record)
    record = _record(); record["mechanism_features"] = []; record["temporal_features"] = []
    with pytest.raises(OperationalFamilyAdapterError, match="CONTRACT_REJECTED"):
        adapt_normalized_operation_runtime(record)


def test_conflicts_remain_explicit_separate_facts():
    record = _record(); record["quality_state"] = "CONFLICTING"; record["conflict_group_id"] = "cg-1"
    fact = adapt_normalized_operation_runtime(record)[0]
    assert fact.quality_state == "CONFLICTING"
    assert fact.conflict_group_id == "cg-1"
