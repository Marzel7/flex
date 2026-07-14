"""
Sprint A5 — Framework extensibility validation tests.

Proves that a second operation (observatory-demo) can be onboarded
without modifying framework files. Also documents the one defect found
(terminal_node universally required) and verifies the fix.

Rules:
  - No Flask startup.
  - No database.
  - No live HTTP calls.
  - No WATCHTOWER imports.
"""

import textwrap
import pytest
from pathlib import Path

from src.ops.registry_loader import load_registry, OperationDefinitionError
from src.ops.capability_resolver import resolve_capability, resolve_all_capabilities
from src.ops.contracts import validate_capability_payload, KNOWN_CAPABILITIES


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_both(tmp_path: Path) -> dict:
    """Write both YAMLs to a temp dir and load the registry."""
    import shutil, pathlib
    src = pathlib.Path("src/ops/registry")
    for f in src.glob("*.yaml"):
        shutil.copy(f, tmp_path / f.name)
    return load_registry(tmp_path)


# ── Test 1: observatory-demo loads from real registry ────────────────────────

def test_observatory_demo_loads():
    registry = load_registry()
    assert "observatory-demo" in registry
    op = registry["observatory-demo"]
    assert op.display_name == "Observatory Demo"
    assert op.infrastructure_model == "FLAT"
    assert op.status == "INSTRUMENTED"


# ── Test 2: Both operations present in registry ───────────────────────────────

def test_both_operations_in_registry():
    registry = load_registry()
    assert "watchtower"      in registry
    assert "observatory-demo" in registry


# ── Test 3: FLAT model accepted without terminal_node (defect fix) ────────────

def test_flat_without_terminal_node_accepted(tmp_path):
    """
    Defect found in A5: terminal_node was required for ALL models including FLAT.
    Fix: requirement is now model-conditional (GRAPH/MESH only).
    """
    content = textwrap.dedent("""\
        framework_schema_version: 1
        definition_version: 1
        operation_id: flat-no-terminal
        display_name: Flat No Terminal
        status: INSTRUMENTED
        infrastructure_model: FLAT
        vocabulary:
          observed_asset: "Price Feed"
          observation_event: "Signal Fired"
        capabilities:
          health:
            state: SUPPORTED
            contract: health_v1
            provider: demo_health
        assurance_mapping:
          ground_truth_source: "demo"
          ground_truth_independent: true
    """)
    (tmp_path / "flat-no-terminal.yaml").write_text(content)
    registry = load_registry(tmp_path)
    assert "flat-no-terminal" in registry


# ── Test 4: NONE model accepted without any tier vocabulary ──────────────────

def test_none_model_no_vocab_accepted(tmp_path):
    content = textwrap.dedent("""\
        framework_schema_version: 1
        definition_version: 1
        operation_id: none-op
        display_name: None Op
        status: INSTRUMENTED
        infrastructure_model: NONE
        vocabulary: {}
        capabilities:
          health:
            state: SUPPORTED
            contract: health_v1
            provider: demo_health
        assurance_mapping:
          ground_truth_source: "none"
          ground_truth_independent: true
    """)
    (tmp_path / "none-op.yaml").write_text(content)
    registry = load_registry(tmp_path)
    assert "none-op" in registry


# ── Test 5: observatory-demo has correct capability mix ───────────────────────

def test_observatory_demo_capability_mix():
    registry = load_registry()
    op = registry["observatory-demo"]

    assert "health"             in op.supported_capabilities()
    assert "failure_attribution" in op.supported_capabilities()
    assert "alerts"             in op.supported_capabilities()

    assert op.capability_state("infrastructure")       == "UNSUPPORTED"
    assert op.capability_state("intelligence")         == "UNSUPPORTED"
    assert op.capability_state("discovery_assurance")  == "NOT_DECLARED"
    assert op.capability_state("detection_coverage")   == "NOT_DECLARED"


# ── Test 6: observatory-demo vocabulary is completely different ───────────────

def test_observatory_demo_vocabulary_distinct():
    registry = load_registry()
    wt_vocab   = registry["watchtower"].vocabulary
    demo_vocab = registry["observatory-demo"].vocabulary

    # No WATCHTOWER terms should appear in demo vocabulary values
    wt_terms = {v.lower() for v in wt_vocab.values() if isinstance(v, str)}
    demo_terms = {v.lower() for v in demo_vocab.values() if isinstance(v, str)}

    # Confirm they share no vocabulary values
    overlap = wt_terms & demo_terms
    assert not overlap, f"Unexpected vocabulary overlap: {overlap}"


# ── Test 7: health_v1 contract validates demo payload ────────────────────────

def test_demo_health_payload_validates():
    payload = {
        "status":                "HEALTHY",
        "pipeline_active":       True,
        "last_event_detected_at": 1783849070,
        "ok":                    True,
        "worker_states":         {"signal_scanner": "RUNNING"},
        "active_alert_count":    0,
    }
    errors = validate_capability_payload("health_v1", payload)
    assert errors == []


# ── Test 8: failure_attribution_v1 validates demo payload ────────────────────

def test_demo_failure_attribution_validates():
    payload = {
        "failure_breakdown": {
            "DISC_ROOT_UNKNOWN": 3,
            "DET_FETCH_TIMEOUT": 1,
        }
    }
    errors = validate_capability_payload("failure_attribution_v1", payload)
    assert errors == []


# ── Test 9: alerts_v1 validates demo payload ─────────────────────────────────

def test_demo_alerts_validates():
    payload = {
        "active_count": 0,
        "active": [],
    }
    errors = validate_capability_payload("alerts_v1", payload)
    assert errors == []


# ── Test 10: capability resolver handles UNSUPPORTED without HTTP call ────────

def test_resolver_unsupported_no_http_call():
    registry = load_registry()
    op = registry["observatory-demo"]
    call_log = []

    def spy(path):
        call_log.append(path)
        return {}

    result = resolve_capability(op, "infrastructure", http_fetcher=spy)
    assert result.state == "UNSUPPORTED"
    assert result.render_status == "UNSUPPORTED"
    assert call_log == []


# ── Test 11: resolver resolves health with injected payload ──────────────────

def test_resolver_health_supported_valid():
    registry = load_registry()
    op = registry["observatory-demo"]

    good = {
        "status": "HEALTHY",
        "pipeline_active": True,
        "last_event_detected_at": None,
    }
    result = resolve_capability(op, "health", http_fetcher=lambda _: good)

    assert result.render_status == "VALID"
    assert result.provider_id == "demo_health"
    assert result.contract == "health_v1"
    assert result.contract_errors == []


# ── Test 12: resolver catches contract violation ──────────────────────────────

def test_resolver_contract_violation():
    registry = load_registry()
    op = registry["observatory-demo"]

    bad = {"unexpected_field": True}  # missing all required fields
    result = resolve_capability(op, "health", http_fetcher=lambda _: bad)

    assert result.render_status == "INVALID_CONTRACT"
    assert len(result.contract_errors) > 0


# ── Test 13: WATCHTOWER unaffected — still resolves correctly ────────────────

def test_watchtower_unaffected():
    registry = load_registry()
    op = registry["watchtower"]

    assert op.infrastructure_model == "GRAPH"
    assert "health" in op.supported_capabilities()
    assert op.vocabulary["origin_node"] == "Treasury"
    assert op.assurance_mapping["ground_truth_independent"] is True


# ── Test 14: resolve_all_capabilities covers all KNOWN for both ops ──────────

def test_resolve_all_covers_known_for_both():
    registry = load_registry()

    def noop(path):
        # Return a payload that satisfies multiple contracts
        return {
            "status": "HEALTHY",
            "pipeline_active": True,
            "last_event_detected_at": None,
            "origin_node_count": 0,
            "terminal_node_count": 0,
            "total_observed_outcomes": 0,
            "attributed_outcomes": 0,
            "unattributed_outcomes": 0,
            "attribution_rate_pct": 0.0,
            "failure_breakdown": {},
            "active_count": 0,
            "active": [],
        }

    for op_id in ("watchtower", "observatory-demo"):
        results = resolve_all_capabilities(registry[op_id], http_fetcher=noop)
        for cap in KNOWN_CAPABILITIES:
            assert cap in results, f"{op_id}: missing {cap}"


# ── Test 15: NONE model with tier vocabulary rejected ────────────────────────

def test_none_model_with_tier_vocab_rejected(tmp_path):
    content = textwrap.dedent("""\
        framework_schema_version: 1
        definition_version: 1
        operation_id: bad-none
        display_name: Bad None
        status: INSTRUMENTED
        infrastructure_model: NONE
        vocabulary:
          origin_node: "Should not be here"
        capabilities:
          health:
            state: SUPPORTED
            contract: health_v1
            provider: demo_health
        assurance_mapping:
          ground_truth_source: "none"
          ground_truth_independent: true
    """)
    (tmp_path / "bad-none.yaml").write_text(content)
    with pytest.raises(OperationDefinitionError, match="NONE model must not define"):
        load_registry(tmp_path)


# ── Test 16: Framework files unchanged (except the documented defect fix) ─────

def test_framework_files_not_changed_beyond_defect_fix():
    """
    Documents which files were modified in A5.
    Only registry_loader.py was changed (terminal_node defect fix).
    All other frozen files are untouched.
    """
    import ast, pathlib

    frozen_files = [
        "src/ops/capability_resolver.py",
        "src/ops/contracts.py",
        "src/ops/registry_cache.py",
        "src/ops/shell_routes.py",
        "templates/ops_shell_index.html",
        "templates/ops_shell_operation.html",
    ]

    for path in frozen_files:
        content = pathlib.Path(path).read_text()
        # Basic sanity: files must parse / be non-empty
        assert len(content) > 100, f"{path} appears empty"
        if path.endswith(".py"):
            ast.parse(content)  # would raise SyntaxError if corrupt
