"""
Tests for the Operations OS Shell — capability resolution, contract validation,
provider resolution, and registry cache.

Rules:
  - No Flask startup.
  - No database connections.
  - No live HTTP calls (http_fetcher is always injected/mocked).
  - No WATCHTOWER imports.
"""

import textwrap
import pytest
from pathlib import Path
from unittest.mock import patch

from src.ops.registry_loader import load_registry, OperationDefinition
from src.ops.capability_resolver import resolve_capability, resolve_all_capabilities, CapabilityResult
from src.ops.contracts import validate_capability_payload, KNOWN_CAPABILITIES
from src.ops.providers import get_provider


# ── Fixtures ──────────────────────────────────────────────────────────────────

_MINIMAL_YAML = """\
    framework_schema_version: 1
    definition_version: 1
    operation_id: watchtower
    display_name: WATCHTOWER
    status: INSTRUMENTED
    infrastructure_model: GRAPH
    graph:
      depth: 2
      cyclic: true
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
      infrastructure:
        state: SUPPORTED
        contract: infrastructure_v1
        provider: watchtower_discovery
      discovery_assurance:
        state: SUPPORTED
        contract: discovery_assurance_v1
        provider: watchtower_discovery
      failure_attribution:
        state: SUPPORTED
        contract: failure_attribution_v1
        provider: watchtower_discovery
      alerts:
        state: SUPPORTED
        contract: alerts_v1
        provider: watchtower_alerts
      detection_coverage:
        state: NOT_DECLARED
      behaviour:
        state: UNSUPPORTED
        reason: "No behavioural fingerprinting implemented yet."
    assurance_mapping:
      ground_truth_source: "wt_farm_launches"
      ground_truth_independent: true
"""


def _load_op(tmp_path: Path, yaml: str = _MINIMAL_YAML) -> OperationDefinition:
    p = tmp_path / "watchtower.yaml"
    p.write_text(textwrap.dedent(yaml), encoding="utf-8")
    return load_registry(tmp_path)["watchtower"]


# ── Test 1: Operations index returns WATCHTOWER ───────────────────────────────

def test_registry_contains_watchtower():
    """Real registry must contain watchtower."""
    registry = load_registry()
    assert "watchtower" in registry


# ── Test 2: Operation page — definition loaded correctly ─────────────────────

def test_operation_definition_loads(tmp_path):
    op = _load_op(tmp_path)
    assert op.operation_id == "watchtower"
    assert op.display_name == "WATCHTOWER"
    assert op.status == "INSTRUMENTED"
    assert op.infrastructure_model == "GRAPH"


# ── Test 3: Unknown operation raises KeyError ─────────────────────────────────

def test_unknown_operation_raises(tmp_path):
    op = _load_op(tmp_path)
    registry = {"watchtower": op}
    assert "no-such-op" not in registry
    with pytest.raises(KeyError):
        _ = registry["no-such-op"]


# ── Test 4: Capability resolution — SUPPORTED with good payload ──────────────

def test_resolve_capability_supported_valid(tmp_path):
    op = _load_op(tmp_path)

    good_payload = {
        "status": "HEALTHY",
        "pipeline_active": True,
        "last_event_detected_at": 1783792275,
    }

    def fake_fetcher(_path):
        return good_payload

    result = resolve_capability(op, "health", http_fetcher=fake_fetcher)

    assert result.state == "SUPPORTED"
    assert result.render_status == "VALID"
    assert result.payload == good_payload
    assert result.contract_errors == []
    assert result.fetch_error is None


# ── Test 5: Contract validation success ──────────────────────────────────────

def test_contract_validation_success():
    errors = validate_capability_payload("health_v1", {
        "status": "HEALTHY",
        "pipeline_active": True,
        "last_event_detected_at": None,
    })
    assert errors == []


# ── Test 6: Contract validation failure ──────────────────────────────────────

def test_contract_validation_failure():
    errors = validate_capability_payload("health_v1", {
        "status": "HEALTHY",
        # pipeline_active missing
        "last_event_detected_at": "wrong-type",
    })
    assert any("pipeline_active" in e for e in errors)
    assert any("last_event_detected_at" in e for e in errors)


# ── Test 7: Provider resolution ───────────────────────────────────────────────

def test_provider_resolution():
    desc = get_provider("watchtower_health")
    assert desc.provider_type == "HTTP"
    assert desc.path.startswith("/api/")


# ── Test 8: Registry cache loads once ────────────────────────────────────────

def test_registry_cache_loads_once():
    from src.ops.registry_cache import get_registry, invalidate_cache

    invalidate_cache()
    r1 = get_registry()
    r2 = get_registry()
    assert r1 is r2  # same object — no re-parse


# ── Test 9: Vocabulary rendering — shell uses vocab not hardcoded terms ───────

def test_vocabulary_rendered_from_definition(tmp_path):
    op = _load_op(tmp_path)
    vocab = op.vocabulary
    # Generic labels map to WATCHTOWER-specific terms
    assert vocab["origin_node"] == "Treasury"
    assert vocab["intermediate_node"] == "Subprovisioner"
    assert vocab["terminal_node"] == "Creator"
    assert vocab["discovery_event"] == "Wrap-close"
    # The framework itself never hardcodes these — they come only from vocab
    # (the test proves vocab is accessible at the OperationDefinition level)


# ── Test 10: NOT_DECLARED renders without provider call ──────────────────────

def test_not_declared_no_provider_call(tmp_path):
    op = _load_op(tmp_path)
    call_log = []

    def spy_fetcher(path):
        call_log.append(path)
        return {}

    result = resolve_capability(op, "detection_coverage", http_fetcher=spy_fetcher)

    assert result.state == "NOT_DECLARED"
    assert result.render_status == "NOT_DECLARED"
    assert result.payload is None
    assert call_log == []  # no HTTP call made


# ── Test 11: UNSUPPORTED renders without provider call ───────────────────────

def test_unsupported_no_provider_call(tmp_path):
    op = _load_op(tmp_path)
    call_log = []

    def spy_fetcher(path):
        call_log.append(path)
        return {}

    result = resolve_capability(op, "behaviour", http_fetcher=spy_fetcher)

    assert result.state == "UNSUPPORTED"
    assert result.render_status == "UNSUPPORTED"
    assert call_log == []


# ── Test 12: INVALID_CONTRACT render_status when payload violates contract ────

def test_resolve_invalid_contract(tmp_path):
    op = _load_op(tmp_path)

    def bad_fetcher(_path):
        # Missing required fields
        return {"something": "else"}

    result = resolve_capability(op, "health", http_fetcher=bad_fetcher)

    assert result.render_status == "INVALID_CONTRACT"
    assert len(result.contract_errors) > 0


# ── Test 13: FETCH_ERROR render_status when fetcher raises ───────────────────

def test_resolve_fetch_error(tmp_path):
    op = _load_op(tmp_path)

    def broken_fetcher(_path):
        raise ConnectionRefusedError("Connection refused")

    result = resolve_capability(op, "health", http_fetcher=broken_fetcher)

    assert result.render_status == "FETCH_ERROR"
    assert result.fetch_error is not None
    assert "Connection refused" in result.fetch_error


# ── Test 14: resolve_all_capabilities covers all KNOWN_CAPABILITIES ──────────

def test_resolve_all_covers_known_capabilities(tmp_path):
    op = _load_op(tmp_path)

    def noop_fetcher(_path):
        return {
            "status": "HEALTHY",
            "pipeline_active": True,
            "last_event_detected_at": None,
            "origin_node_count": 57,
            "terminal_node_count": 100,
            "total_observed_outcomes": 1359,
            "attributed_outcomes": 619,
            "unattributed_outcomes": 739,
            "attribution_rate_pct": 45.5,
            "failure_breakdown": {"DISC_ROOT_UNKNOWN": 739},
            "active_count": 0,
            "active": [],
        }

    results = resolve_all_capabilities(op, http_fetcher=noop_fetcher)

    for cap_name in KNOWN_CAPABILITIES:
        assert cap_name in results, f"Missing capability: {cap_name}"
        assert isinstance(results[cap_name], CapabilityResult)


# ── Test 15: No Flask imports in shell/registry modules ──────────────────────

def test_no_flask_in_registry_modules():
    """Importing registry, contracts, providers, capability_resolver,
    and registry_cache must not trigger Flask."""
    import sys

    ops_keys = [k for k in sys.modules if k.startswith("src.ops")]
    for k in ops_keys:
        del sys.modules[k]

    import src.ops.contracts
    import src.ops.providers
    import src.ops.registry_loader
    import src.ops.capability_resolver
    import src.ops.registry_cache

    assert "flask" not in sys.modules


# ── Test 16: Shell routes import does pull Flask but via Blueprint only ───────

def test_shell_routes_uses_blueprint():
    """shell_routes.py imports Flask Blueprint — confirm it does so correctly
    and does not call load_registry() at import time."""
    from src.ops.registry_cache import invalidate_cache
    invalidate_cache()

    # Import the module — this must not trigger a registry load
    import importlib
    import sys
    sys.modules.pop("src.ops.shell_routes", None)

    # We can't easily verify "no load_registry called" without instrumentation,
    # but we can verify the import succeeds and the Blueprint is registered.
    from src.ops.shell_routes import ops_shell_bp
    assert ops_shell_bp.name == "ops_shell"
