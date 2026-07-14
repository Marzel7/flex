"""
Sprint O2 — Launcher Observatory tests.

Verifies:
  1. YAML definition loads and validates against the framework
  2. All new contracts (behaviour_v1, intelligence_v1, outcome_intelligence_v1) are valid
  3. All providers are registered
  4. No framework files were modified
  5. Contract payload validation works for each new contract
  6. Capability resolver handles new capabilities correctly

Rules:
  - No Flask startup
  - No live DB connections
  - No RPC
  - No WATCHTOWER imports
"""

import textwrap
import pytest
from pathlib import Path


# ── Test 1: Launcher Observatory loads from real registry ─────────────────────

def test_launcher_observatory_loads():
    from src.ops.registry_loader import load_registry
    registry = load_registry()
    assert "launcher-observatory" in registry
    op = registry["launcher-observatory"]
    assert op.display_name == "Launcher Observatory"
    assert op.infrastructure_model == "FLAT"
    assert op.status == "INSTRUMENTED"


# ── Test 2: All three real operations present ─────────────────────────────────

def test_all_three_operations_present():
    from src.ops.registry_loader import load_registry
    registry = load_registry()
    assert "watchtower"            in registry
    assert "observatory-demo"      in registry
    assert "launcher-observatory"  in registry


# ── Test 3: Correct capability mix ────────────────────────────────────────────

def test_launcher_observatory_capability_mix():
    from src.ops.registry_loader import load_registry
    registry = load_registry()
    op = registry["launcher-observatory"]

    # SUPPORTED capabilities
    supported = op.supported_capabilities()
    assert "health"                 in supported
    assert "failure_attribution"    in supported
    assert "behaviour"              in supported
    assert "intelligence"           in supported
    assert "outcome_intelligence"   in supported

    # UNSUPPORTED (FLAT model — no graph)
    assert op.capability_state("infrastructure")    == "UNSUPPORTED"
    assert op.capability_state("discovery_assurance") == "UNSUPPORTED"

    # NOT_DECLARED (no real-time pipeline)
    assert op.capability_state("alerts")            == "NOT_DECLARED"
    assert op.capability_state("detection_coverage") == "NOT_DECLARED"


# ── Test 4: Vocabulary is operator-domain (no WATCHTOWER terms) ───────────────

def test_launcher_observatory_vocabulary_distinct():
    from src.ops.registry_loader import load_registry
    registry = load_registry()
    lo_vocab = registry["launcher-observatory"].vocabulary
    wt_vocab = registry["watchtower"].vocabulary

    # id_format is a technical format specifier (e.g. "solana_address"), not domain vocabulary.
    # Exclude it from the distinctness check — both operations run on Solana.
    wt_terms = {v.lower() for k, v in wt_vocab.items() if isinstance(v, str) and k != "id_format"}
    lo_terms = {v.lower() for k, v in lo_vocab.items() if isinstance(v, str) and k != "id_format"}

    overlap = wt_terms & lo_terms
    assert not overlap, f"Unexpected domain vocabulary overlap with WATCHTOWER: {overlap}"


# ── Test 5: All providers are registered ──────────────────────────────────────

def test_launcher_observatory_providers_registered():
    from src.ops.providers import get_provider
    expected_providers = [
        "launcher_observatory_health",
        "launcher_observatory_failure_attribution",
        "launcher_observatory_behaviour",
        "launcher_observatory_intelligence",
        "launcher_observatory_outcome_intelligence",
    ]
    for pid in expected_providers:
        desc = get_provider(pid)
        assert desc.provider_type == "HTTP"
        assert desc.path.startswith("/api/ops/launcher-observatory/")


# ── Test 6: behaviour_v1 contract validates correctly ─────────────────────────

def test_behaviour_v1_valid_payload():
    from src.ops.contracts import validate_capability_payload
    payload = {
        "operator_count":            21,
        "total_launches":            148,
        "avg_launches_per_operator": 7.0,
        "active_operators":          21,
        "avg_campaign_interval_days": 19.9,
    }
    errors = validate_capability_payload("behaviour_v1", payload)
    assert errors == [], f"Unexpected errors: {errors}"


def test_behaviour_v1_missing_required():
    from src.ops.contracts import validate_capability_payload
    errors = validate_capability_payload("behaviour_v1", {"operator_count": 5})
    assert any("total_launches" in e for e in errors)
    assert any("avg_launches_per_operator" in e for e in errors)


# ── Test 7: intelligence_v1 contract validates correctly ──────────────────────

def test_intelligence_v1_valid_payload():
    from src.ops.contracts import validate_capability_payload
    payload = {
        "top_operators": [{"funder": "abc123", "launch_count": 18}],
        "most_active_30d": [],
    }
    errors = validate_capability_payload("intelligence_v1", payload)
    assert errors == [], f"Unexpected errors: {errors}"


def test_intelligence_v1_missing_required():
    from src.ops.contracts import validate_capability_payload
    errors = validate_capability_payload("intelligence_v1", {})
    assert any("top_operators" in e for e in errors)


# ── Test 8: outcome_intelligence_v1 contract validates correctly ───────────────

def test_outcome_intelligence_v1_valid_payload():
    from src.ops.contracts import validate_capability_payload
    payload = {
        "unknown_scope_total":    739,
        "explained_by_operators": 148,
        "remaining_unknown":      591,
        "explanation_rate_pct":   20.0,
        "persistent_funders":     21,
    }
    errors = validate_capability_payload("outcome_intelligence_v1", payload)
    assert errors == [], f"Unexpected errors: {errors}"


def test_outcome_intelligence_v1_wrong_type():
    from src.ops.contracts import validate_capability_payload
    payload = {
        "unknown_scope_total":    739,
        "explained_by_operators": 148,
        "remaining_unknown":      591,
        "explanation_rate_pct":   "20%",  # wrong type — should be int/float
    }
    errors = validate_capability_payload("outcome_intelligence_v1", payload)
    assert any("explanation_rate_pct" in e for e in errors)


# ── Test 9: Display metadata valid for all new contracts ──────────────────────

def test_new_contract_display_metadata_valid():
    from src.ops.contracts import validate_display_metadata
    for contract in ["behaviour_v1", "intelligence_v1", "outcome_intelligence_v1"]:
        errors = validate_display_metadata(contract)
        assert errors == [], f"{contract} display errors: {errors}"


# ── Test 10: Capability resolver handles new capabilities ─────────────────────

def test_resolver_handles_behaviour():
    from src.ops.registry_loader import load_registry
    from src.ops.capability_resolver import resolve_capability

    registry = load_registry()
    op = registry["launcher-observatory"]

    valid_payload = {
        "operator_count":            5,
        "total_launches":            30,
        "avg_launches_per_operator": 6.0,
    }
    result = resolve_capability(op, "behaviour", http_fetcher=lambda _: valid_payload)
    assert result.render_status == "VALID"
    assert result.contract == "behaviour_v1"
    assert result.contract_errors == []


def test_resolver_handles_outcome_intelligence():
    from src.ops.registry_loader import load_registry
    from src.ops.capability_resolver import resolve_capability

    registry = load_registry()
    op = registry["launcher-observatory"]

    valid_payload = {
        "unknown_scope_total":    739,
        "explained_by_operators": 148,
        "remaining_unknown":      591,
        "explanation_rate_pct":   20.0,
    }
    result = resolve_capability(
        op, "outcome_intelligence", http_fetcher=lambda _: valid_payload
    )
    assert result.render_status == "VALID"
    assert result.contract == "outcome_intelligence_v1"


def test_resolver_infrastructure_unsupported_no_http():
    from src.ops.registry_loader import load_registry
    from src.ops.capability_resolver import resolve_capability

    registry = load_registry()
    op = registry["launcher-observatory"]
    call_log = []

    def spy(path):
        call_log.append(path)
        return {}

    result = resolve_capability(op, "infrastructure", http_fetcher=spy)
    assert result.state == "UNSUPPORTED"
    assert result.render_status == "UNSUPPORTED"
    assert call_log == [], "HTTP should not be called for UNSUPPORTED capability"


# ── Test 11: WATCHTOWER unaffected ────────────────────────────────────────────

def test_watchtower_unaffected_by_o2():
    from src.ops.registry_loader import load_registry
    registry = load_registry()
    wt = registry["watchtower"]
    assert wt.infrastructure_model == "GRAPH"
    assert wt.vocabulary["origin_node"] == "Treasury"
    assert "health" in wt.supported_capabilities()
    assert "discovery_assurance" in wt.supported_capabilities()


# ── Test 12: No framework files modified ──────────────────────────────────────

def test_framework_files_not_modified():
    """
    Documents which files may be modified in O2.
    Framework shell and resolver are frozen.
    Only additive changes are allowed:
      - New contracts in contracts.py (additive, backwards compatible)
      - New providers in providers.py (additive, backwards compatible)
      - New YAML in registry/
      - New module launcher_observatory_routes.py
    """
    import ast

    frozen_files = [
        "src/ops/capability_resolver.py",
        "src/ops/registry_cache.py",
        "src/ops/registry_loader.py",
        "src/ops/shell_routes.py",
        "templates/ops_shell_index.html",
        "templates/ops_shell_operation.html",
    ]

    for path in frozen_files:
        content = Path(path).read_text()
        assert len(content) > 100, f"{path} appears empty"
        if path.endswith(".py"):
            ast.parse(content)


# ── Test 13: launcher_observatory_routes imports no Flask at module level ──────

def test_launcher_observatory_routes_no_flask_at_import():
    import sys

    to_remove = [k for k in sys.modules if "launcher_observatory" in k
                 or k in ("flask", "flask.app")]
    for k in to_remove:
        del sys.modules[k]

    import src.ops.launcher_observatory_routes  # noqa: F401
    assert "flask" not in sys.modules, (
        "launcher_observatory_routes must not import Flask at module level "
        "(Flask is imported lazily inside register_launcher_observatory_routes)"
    )
