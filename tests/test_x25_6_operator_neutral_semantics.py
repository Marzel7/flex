"""X25.6 — Operator-Neutral Discovery Semantics.

Regression tests proving:
- Detection Provenance never implies operator identity (no "WATCHTOWER"
  wording in any of its rendered states).
- Operation Identity never implies canonical operator.
- Infrastructure Attribution never implies operator.
- Walkback evidence (lead nodes, hops, generic timeline reason strings)
  never implies operator identity.
- Canonical Operator remains the ONLY place WATCHTOWER is asserted, and
  only when independently established via operator_name.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/discovery.html").read_text()

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")


def _extract_function(name: str, js: str) -> str:
    idx = js.index(f"function {name}(")
    depth = 0
    i = js.index("{", idx)
    while True:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
        if depth == 0:
            break
        i += 1
    return js[idx : i + 1]


def _extract_var(name: str, js: str) -> str:
    try:
        idx = js.index(f"var {name} ")
    except ValueError:
        idx = js.index(f"var {name}=")
    end = js.index(";\n", idx)
    return js[idx : end + 1]


def _script() -> str:
    m = re.search(r"{% block scripts %}\s*<script>(.*)</script>\s*{% endblock %}", HTML, re.S)
    return m.group(1)


def _run(expr: str, snippet: str) -> str:
    script = snippet + "\nconsole.log(JSON.stringify(" + expr + "));"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15, check=True)
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Detection Provenance never implies operator identity
# ---------------------------------------------------------------------------

def _reconciliation_snippet():
    js = _script()
    return "\n".join([_extract_function("esc", js), _extract_function("detectionReconciliation", js)])


def test_detection_provenance_walkback_recovered_never_names_watchtower():
    """X25.7 replaced 'confirmed operation lineage' process-adjacent wording
    with pure outcome wording ('complete funding lineage was established') —
    still operator-neutral, still no WATCHTOWER."""
    snippet = _reconciliation_snippet()
    html = _run("detectionReconciliation(" + json.dumps({
        "classification": "WALKBACK_RECOVERED", "plain_transfer_associated": False,
    }) + ")", snippet)
    assert "WATCHTOWER" not in html
    assert "complete funding lineage was established" in html.lower()


def test_detection_provenance_pipeline_inconsistency_never_names_watchtower():
    snippet = _reconciliation_snippet()
    html = _run("detectionReconciliation(" + json.dumps({
        "classification": "PIPELINE_INCONSISTENCY", "plain_transfer_associated": False,
    }) + ")", snippet)
    assert "WATCHTOWER" not in html
    assert "complete funding lineage was established" in html.lower()


def test_detection_provenance_walkback_observed_never_names_watchtower():
    snippet = _reconciliation_snippet()
    html = _run("detectionReconciliation(" + json.dumps({
        "classification": "WALKBACK_OBSERVED", "plain_transfer_associated": False,
    }) + ")", snippet)
    assert "WATCHTOWER" not in html
    assert "partial funding lineage" in html.lower()


def test_detection_provenance_walkback_inconclusive_never_names_watchtower():
    snippet = _reconciliation_snippet()
    html = _run("detectionReconciliation(" + json.dumps({
        "classification": "WALKBACK_INCONCLUSIVE", "plain_transfer_associated": False,
    }) + ")", snippet)
    assert "WATCHTOWER" not in html


def test_detection_provenance_reconciled_never_names_watchtower():
    snippet = _reconciliation_snippet()
    html = _run("detectionReconciliation(" + json.dumps({
        "classification": "RECONCILED", "live_detection_source": "RECONCILE",
        "plain_transfer_associated": False,
    }) + ")", snippet)
    assert "WATCHTOWER" not in html
    assert "wt_watchtower_launches" not in html


def test_detection_provenance_live_detected_never_names_watchtower():
    snippet = _reconciliation_snippet()
    html = _run("detectionReconciliation(" + json.dumps({
        "classification": "LIVE_DETECTED", "live_detection_source": "LIVE_STREAM",
        "plain_transfer_associated": False,
    }) + ")", snippet)
    assert "WATCHTOWER" not in html


# ---------------------------------------------------------------------------
# Operation Identity never implies canonical operator
# ---------------------------------------------------------------------------

def _operation_identity_snippet():
    js = _script()
    return "\n".join([_extract_function("esc", js), _extract_function("abbr", js),
                       _extract_function("operationIdentity", js)])


def test_operation_identity_card_never_names_watchtower():
    snippet = _operation_identity_snippet()
    oi = {"operation_id": "op_abc123", "display_name": "Operation ABC123",
          "identity_basis": "TREASURY_FUNDING_MESH", "confidence": "CONFIRMED",
          "treasury_count": 4, "launch_count": 22, "subject_treasury": "SOME_TREASURY",
          "member_treasuries": ["A", "B", "C", "D"]}
    html = _run("operationIdentity(" + json.dumps(oi) + ")", snippet)
    assert "WATCHTOWER" not in html


def test_operation_identity_single_treasury_never_names_watchtower():
    snippet = _operation_identity_snippet()
    oi = {"operation_id": "op_def456", "display_name": "Operation DEF456",
          "identity_basis": "TREASURY_FUNDING_MESH", "confidence": "CONFIRMED",
          "treasury_count": 1, "launch_count": 7, "subject_treasury": "SOLE_TREASURY",
          "member_treasuries": ["SOLE_TREASURY"]}
    html = _run("operationIdentity(" + json.dumps(oi) + ")", snippet)
    assert "WATCHTOWER" not in html


# ---------------------------------------------------------------------------
# Infrastructure Attribution never implies operator
# ---------------------------------------------------------------------------

def _outcome_snippet():
    js = _script()
    return "\n".join([
        _extract_function("esc", js), _extract_function("abbr", js),
        _extract_var("ANALYST_WORDING", js), _extract_var("DEFAULT_WORDING", js),
        _extract_var("ISTATE_CLASS", js), _extract_function("istateChip", js),
        _extract_function("knownFields", js), _extract_function("recordedLineage", js),
        _extract_function("outcome", js),
    ])


def test_infrastructure_outcomes_never_name_watchtower():
    snippet = _outcome_snippet()
    for outcome_type in ("KNOWN_RELAY_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_CEX_REACHED",
                          "UNKNOWN_INFRASTRUCTURE", "LINEAGE_GAP"):
        html = _run("outcome(" + json.dumps({
            "outcome_type": outcome_type, "confidence": "HIGH", "stop_reason": "x",
            "terminal_entity": "SomeAddr111111111111111111111111111111111",
            "terminal_entity_type": "AUTOMATION", "should_seed_emerging_operator": 0,
        }) + ")", snippet)
        assert "WATCHTOWER" not in html, f"{outcome_type} leaked WATCHTOWER"


# ---------------------------------------------------------------------------
# Walkback evidence never implies operator identity
# ---------------------------------------------------------------------------

def _walkback_snippet():
    js = _script()
    return "\n".join([
        _extract_var("ROLE_ICON", js), _extract_function("esc", js), _extract_function("abbr", js),
        _extract_function("href", js), _extract_function("walkbackLeadNodes", js),
        _extract_function("walkback", js),
    ])


def test_walkback_with_no_canonical_identity_never_names_watchtower():
    snippet = _walkback_snippet()
    html = _run(
        "walkback(" + json.dumps({"status": "PROVISIONAL", "hops": [], "stop_reason": "No historical evidence."})
        + ",null,null,null,null)", snippet,
    )
    assert "WATCHTOWER" not in html


def test_walkback_with_infrastructure_boundary_never_names_watchtower():
    snippet = _walkback_snippet()
    html = _run(
        "walkback(" + json.dumps({"status": "PROVISIONAL", "hops": [], "stop_reason": "Attribution boundary reached."})
        + ",null," + json.dumps({"terminal_entity": "AxiomRXZAq1J", "terminal_entity_type": "AUTOMATION"})
        + ",null,null)", snippet,
    )
    assert "WATCHTOWER" not in html
    assert "Infrastructure Boundary" in html


def test_walkback_only_names_watchtower_when_canonical_identity_genuinely_resolved():
    """The one legitimate exception: when canonicalIdentity is populated with
    operator_name='WATCHTOWER', the walkback endpoint correctly reflects it —
    this is earned by an independently-established fact, not asserted by the
    walkback mechanism itself."""
    snippet = _walkback_snippet()
    html = _run(
        "walkback(" + json.dumps({"status": "CONFIRMED", "hops": [], "stop_reason": "x"})
        + ",null,null," + json.dumps({"operator_name": "WATCHTOWER", "confidence": "HIGH", "operator_id": "op1"})
        + ",null)", snippet,
    )
    assert "WATCHTOWER" in html
    assert "Canonical operator reached" in html


# ---------------------------------------------------------------------------
# Canonical Operator is the only place WATCHTOWER is asserted
# ---------------------------------------------------------------------------

def _canonical_identity_snippet():
    js = _script()
    return "\n".join([_extract_function("esc", js), _extract_function("canonicalIdentity", js)])


def test_canonical_identity_absent_when_no_operator():
    snippet = _canonical_identity_snippet()
    assert _run("canonicalIdentity(null)", snippet) == ""


def test_canonical_identity_names_watchtower_only_when_operator_name_is_watchtower():
    snippet = _canonical_identity_snippet()
    html = _run("canonicalIdentity(" + json.dumps({
        "operator_name": "WATCHTOWER", "operator_id": "op1",
        "operator_href": "/x", "confidence": "HIGH", "identity_signals": [],
    }) + ")", snippet)
    assert "WATCHTOWER" in html


def test_canonical_identity_never_hardcodes_watchtower_for_other_operators():
    """Future-proofing: a different operator name renders faithfully, and the
    word WATCHTOWER never leaks in when a different operator is identified."""
    snippet = _canonical_identity_snippet()
    for name in ("PHANTOM", "ORBIT", "DELTA"):
        html = _run("canonicalIdentity(" + json.dumps({
            "operator_name": name, "operator_id": "op2",
            "operator_href": "/x", "confidence": "HIGH", "identity_signals": [],
        }) + ")", snippet)
        assert name in html
        assert "WATCHTOWER" not in html


# ---------------------------------------------------------------------------
# Backend: WATCHTOWER-specific wording removed from non-operator-gated paths
# ---------------------------------------------------------------------------

def test_attribution_reason_never_names_watchtower_for_bare_treasury_match():
    from src.discovery.service import DiscoveryService
    reason = DiscoveryService._attribution_reason({"matched_treasury": "SOME_TREASURY"})
    assert "WATCHTOWER" not in reason
    assert "confirmed treasury" in reason.lower()


def test_attribution_reason_never_names_watchtower_for_bare_subprov_match():
    from src.discovery.service import DiscoveryService
    reason = DiscoveryService._attribution_reason({"matched_subprov": "SOME_SUBPROV"})
    assert "WATCHTOWER" not in reason
