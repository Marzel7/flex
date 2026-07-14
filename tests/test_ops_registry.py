"""
Tests for the Operations OS Registry Loader and Capability Contracts.

Rules:
  - No Flask imports.
  - No database connections.
  - No HTTP calls.
  - Tests run entirely in memory or against temp directories.
"""

import textwrap
import pytest
from pathlib import Path

import yaml

# ── helpers ───────────────────────────────────────────────────────────────────

def _write_yaml(tmp_path: Path, filename: str, content: str) -> Path:
    """Write a YAML string to a temp file and return its path."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _registry_dir(tmp_path: Path, yaml_content: str, filename: str = "op.yaml") -> Path:
    """Write a single YAML file to a temp registry directory."""
    _write_yaml(tmp_path, filename, yaml_content)
    return tmp_path


# ── Minimal valid WATCHTOWER-like definition (reused by several tests) ────────

_VALID_WATCHTOWER = """\
    framework_schema_version: 1
    definition_version: 1
    operation_version: "wt-current"
    operation_id: watchtower
    display_name: WATCHTOWER
    status: INSTRUMENTED
    infrastructure_model: GRAPH
    graph:
      depth: 2
      cyclic: true
    vocabulary:
      origin_node: "Treasury"
      origin_node_plural: "Treasuries"
      intermediate_node: "Subprovisioner"
      terminal_node: "Creator"
      discovery_event: "Wrap-close"
      observable_event: "Token Create"
      detection_event: "Launch Detection"
    capabilities:
      health:
        state: SUPPORTED
        contract: health_v1
        provider: watchtower_health
    assurance_mapping:
      ground_truth_source: "wt_farm_launches"
      ground_truth_independent: true
"""


# ── Test 1: Valid WATCHTOWER definition loads ─────────────────────────────────

def test_valid_watchtower_definition_loads(tmp_path):
    from src.ops.registry_loader import load_registry

    d = _registry_dir(tmp_path, _VALID_WATCHTOWER)
    registry = load_registry(d)

    assert "watchtower" in registry
    op = registry["watchtower"]
    assert op.operation_id == "watchtower"
    assert op.display_name == "WATCHTOWER"
    assert op.status == "INSTRUMENTED"
    assert op.infrastructure_model == "GRAPH"
    assert op.framework_schema_version == 1
    assert op.definition_version == 1
    assert op.operation_version == "wt-current"
    assert "health" in op.supported_capabilities()


# ── Test 2: Duplicate operation_id rejected ───────────────────────────────────

def test_duplicate_operation_id_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, RegistryLoadError

    _write_yaml(tmp_path, "wt1.yaml", _VALID_WATCHTOWER)
    _write_yaml(tmp_path, "wt2.yaml", _VALID_WATCHTOWER)  # same operation_id

    with pytest.raises(RegistryLoadError, match="Duplicate operation_id"):
        load_registry(tmp_path)


# ── Test 3: Missing operation_id rejected ─────────────────────────────────────

def test_missing_operation_id_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, OperationDefinitionError

    content = _VALID_WATCHTOWER.replace("operation_id: watchtower\n", "")
    d = _registry_dir(tmp_path, content)

    with pytest.raises(OperationDefinitionError, match="operation_id"):
        load_registry(d)


# ── Test 4: Unsupported framework_schema_version rejected ────────────────────

def test_unsupported_framework_schema_version_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, UnsupportedFrameworkSchemaError

    content = _VALID_WATCHTOWER.replace(
        "framework_schema_version: 1",
        "framework_schema_version: 999",
    )
    d = _registry_dir(tmp_path, content)

    with pytest.raises(UnsupportedFrameworkSchemaError, match="999"):
        load_registry(d)


# ── Test 5: Unknown capability contract rejected ──────────────────────────────

def test_unknown_capability_contract_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, UnknownCapabilityContractError

    content = _VALID_WATCHTOWER.replace("contract: health_v1", "contract: nonexistent_v99")
    d = _registry_dir(tmp_path, content)

    with pytest.raises(UnknownCapabilityContractError, match="nonexistent_v99"):
        load_registry(d)


# ── Test 6: Unknown provider rejected ────────────────────────────────────────

def test_unknown_provider_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, UnknownCapabilityProviderError

    content = _VALID_WATCHTOWER.replace(
        "provider: watchtower_health",
        "provider: no_such_provider",
    )
    d = _registry_dir(tmp_path, content)

    with pytest.raises(UnknownCapabilityProviderError, match="no_such_provider"):
        load_registry(d)


# ── Test 7: SUPPORTED capability without provider rejected ───────────────────

def test_supported_capability_without_provider_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, OperationDefinitionError

    content = _VALID_WATCHTOWER.replace("\n        provider: watchtower_health\n", "\n")
    d = _registry_dir(tmp_path, content)

    with pytest.raises(OperationDefinitionError, match="requires 'provider'"):
        load_registry(d)


# ── Test 8: NOT_DECLARED capability with provider rejected ───────────────────

def test_not_declared_capability_with_provider_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, OperationDefinitionError

    # Build a self-contained YAML that has a NOT_DECLARED cap carrying a provider
    content = """\
        framework_schema_version: 1
        definition_version: 1
        operation_id: watchtower
        display_name: WATCHTOWER
        status: INSTRUMENTED
        infrastructure_model: GRAPH
        graph:
          depth: 2
          cyclic: false
        vocabulary:
          origin_node: "Treasury"
          intermediate_node: "Subprovisioner"
          terminal_node: "Creator"
          discovery_event: "Wrap-close"
          observable_event: "Token Create"
          detection_event: "Launch Detection"
        capabilities:
          health:
            state: SUPPORTED
            contract: health_v1
            provider: watchtower_health
          behaviour:
            state: NOT_DECLARED
            provider: watchtower_health
        assurance_mapping:
          ground_truth_source: "wt_farm_launches"
          ground_truth_independent: true
    """
    d = _registry_dir(tmp_path, textwrap.dedent(content))

    with pytest.raises(OperationDefinitionError, match="NOT_DECLARED.*must not.*provider"):
        load_registry(d)


# ── Test 9: INSTRUMENTED operation without health SUPPORTED rejected ──────────

def test_instrumented_without_health_supported_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, OperationDefinitionError

    # Replace health with NOT_DECLARED
    content = _VALID_WATCHTOWER.replace(
        "      health:\n        state: SUPPORTED\n        contract: health_v1\n        provider: watchtower_health",
        "      health:\n        state: NOT_DECLARED",
    )
    d = _registry_dir(tmp_path, content)

    with pytest.raises(OperationDefinitionError, match="health.*SUPPORTED"):
        load_registry(d)


# ── Test 10: FLAT operation without intermediate vocabulary accepted ──────────

def test_flat_operation_without_intermediate_vocabulary_accepted(tmp_path):
    from src.ops.registry_loader import load_registry

    content = """\
        framework_schema_version: 1
        definition_version: 1
        operation_id: flat-op
        display_name: Flat Operation
        status: INSTRUMENTED
        infrastructure_model: FLAT
        vocabulary:
          terminal_node: "Operator Wallet"
        capabilities:
          health:
            state: SUPPORTED
            contract: health_v1
            provider: watchtower_health
        assurance_mapping:
          ground_truth_source: "flat_launches_table"
          ground_truth_independent: true
    """
    d = _registry_dir(tmp_path, textwrap.dedent(content))
    registry = load_registry(d)

    assert "flat-op" in registry
    assert registry["flat-op"].infrastructure_model == "FLAT"


# ── Test 11: GRAPH operation missing required vocabulary rejected ─────────────

def test_graph_operation_missing_vocabulary_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, OperationDefinitionError

    # Remove intermediate_node from vocabulary
    content = _VALID_WATCHTOWER.replace(
        '      intermediate_node: "Subprovisioner"\n', ""
    )
    d = _registry_dir(tmp_path, content)

    with pytest.raises(OperationDefinitionError, match="intermediate_node"):
        load_registry(d)


# ── Test 12: Capability payload validates against health_v1 ─────────────────

def test_valid_payload_passes_health_v1():
    from src.ops.contracts import validate_capability_payload

    payload = {
        "status": "HEALTHY",
        "pipeline_active": True,
        "last_event_detected_at": 1783792275,
        "ok": True,
    }
    errors = validate_capability_payload("health_v1", payload)
    assert errors == [], f"Expected no errors, got: {errors}"


# ── Test 13: Invalid payload fails with precise path/message ────────────────

def test_invalid_payload_fails_with_precise_message():
    from src.ops.contracts import validate_capability_payload

    # Missing 'pipeline_active', wrong type on 'last_event_detected_at'
    payload = {
        "status": "HEALTHY",
        # pipeline_active missing
        "last_event_detected_at": "not-a-number",  # wrong type
    }
    errors = validate_capability_payload("health_v1", payload)

    assert any("pipeline_active" in e for e in errors), (
        f"Expected error about 'pipeline_active', got: {errors}"
    )
    assert any("last_event_detected_at" in e for e in errors), (
        f"Expected error about 'last_event_detected_at', got: {errors}"
    )


# ── Test 14: Unknown contract returns error ───────────────────────────────────

def test_validate_payload_unknown_contract_returns_error():
    from src.ops.contracts import validate_capability_payload

    errors = validate_capability_payload("does_not_exist_v99", {"status": "HEALTHY"})
    assert len(errors) == 1
    assert "does_not_exist_v99" in errors[0]


# ── Test 15: Real WATCHTOWER YAML in src/ops/registry/ loads cleanly ─────────

def test_real_watchtower_yaml_loads():
    """Smoke test: the shipped watchtower.yaml must pass validation."""
    from src.ops.registry_loader import load_registry

    registry = load_registry()  # uses default registry dir
    assert "watchtower" in registry

    op = registry["watchtower"]
    assert op.status == "INSTRUMENTED"
    assert op.infrastructure_model == "GRAPH"
    assert "health" in op.supported_capabilities()
    assert op.assurance_mapping["ground_truth_independent"] is True


# ── Test 16: FLAT operation with intermediate_node vocabulary rejected ────────

def test_flat_operation_with_intermediate_vocab_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, OperationDefinitionError

    content = """\
        framework_schema_version: 1
        definition_version: 1
        operation_id: bad-flat
        display_name: Bad Flat Op
        status: INSTRUMENTED
        infrastructure_model: FLAT
        vocabulary:
          terminal_node: "Operator Wallet"
          intermediate_node: "Should Not Be Here"
        capabilities:
          health:
            state: SUPPORTED
            contract: health_v1
            provider: watchtower_health
        assurance_mapping:
          ground_truth_source: "table"
          ground_truth_independent: true
    """
    d = _registry_dir(tmp_path, textwrap.dedent(content))

    with pytest.raises(OperationDefinitionError, match="FLAT.*intermediate_node"):
        load_registry(d)


# ── Test 17: Missing ground_truth_independent rejected ───────────────────────

def test_missing_ground_truth_independent_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, OperationDefinitionError

    content = _VALID_WATCHTOWER.replace(
        "      ground_truth_independent: true\n", ""
    )
    d = _registry_dir(tmp_path, content)

    with pytest.raises(OperationDefinitionError, match="ground_truth_independent"):
        load_registry(d)


# ── Test 18: Invalid status value rejected ────────────────────────────────────

def test_invalid_status_rejected(tmp_path):
    from src.ops.registry_loader import load_registry, OperationDefinitionError

    content = _VALID_WATCHTOWER.replace("status: INSTRUMENTED", "status: RUNNING")
    d = _registry_dir(tmp_path, content)

    with pytest.raises(OperationDefinitionError, match="RUNNING"):
        load_registry(d)


# ── Test 19: Payload validates allowed_status_values ────────────────────────

def test_invalid_status_value_in_health_payload():
    from src.ops.contracts import validate_capability_payload

    payload = {
        "status": "RUNNING",          # not in allowed values
        "pipeline_active": True,
        "last_event_detected_at": None,
    }
    errors = validate_capability_payload("health_v1", payload)
    assert any("RUNNING" in e for e in errors), f"Expected status error, got: {errors}"


# ── Test 20: No Flask, DB, or network imports triggered ──────────────────────

def test_no_flask_or_db_imports():
    """Importing ops modules must not pull in Flask or database drivers."""
    import importlib
    import sys

    # Remove any cached ops modules and Flask to force a clean import check.
    # shell_routes legitimately imports Flask (it's a Blueprint), but that must
    # not be a *side-effect* of importing the registry-only modules. We clear
    # Flask from sys.modules so we can prove the registry modules don't re-import it.
    to_remove = [k for k in sys.modules if k.startswith("src.ops") or k in ("flask", "flask.app")]
    for key in to_remove:
        del sys.modules[key]

    import src.ops.contracts       # noqa: F401
    import src.ops.providers       # noqa: F401
    import src.ops.registry_loader # noqa: F401

    assert "flask" not in sys.modules, "Flask must not be imported by ops framework modules."
    assert "sqlalchemy" not in sys.modules, "SQLAlchemy must not be imported."
    # sqlite3 is stdlib and acceptable only if imported transitively; check it's not from us
    # (yaml and pathlib are fine)
