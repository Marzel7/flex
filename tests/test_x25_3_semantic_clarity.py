"""X25.3 — Discovery Semantic Clarity & Analyst Summary.

Behavioral tests proving:
- Funding Walkback heading and Relationship Confidence badge are separate.
- OBSERVED_ONLY explains why a reconstructed chain can still exist.
- The walkback terminal node distinguishes a genuine infrastructure boundary
  from an unresolved evidence gap, and the old generic "Endpoint" label is
  gone.
- Analyst Summary synthesizes only fields that actually exist, one sentence
  per field, and omits sentences whose backing field is absent.
- Existing sections (Detection Provenance, Attribution Outcome, Canonical
  Operator, relationship-chain entity labels) are unchanged in meaning.
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
# Phase 1 — Funding Walkback heading vs Relationship Confidence badge
# ---------------------------------------------------------------------------

def _walkback_snippet():
    js = _script()
    return "\n".join([
        _extract_var("ROLE_ICON", js),
        _extract_function("esc", js),
        _extract_function("abbr", js),
        _extract_function("href", js),
        _extract_function("walkbackLeadNodes", js),
        _extract_function("walkback", js),
    ])


def test_walkback_heading_no_longer_concatenates_status():
    assert "Funding Walkback · '+esc(w.status)" not in HTML
    assert "<span>Funding Walkback</span>" in HTML


def test_relationship_confidence_badge_present():
    snippet = _walkback_snippet()
    html = _run(
        "walkback(" + json.dumps({"status": "PROVISIONAL", "hops": [], "stop_reason": "x"})
        + ",null,null,null,null)", snippet,
    )
    assert "Relationship Confidence" in html
    assert "PROVISIONAL" in html
    assert "Funding Walkback" in html
    assert "Funding Walkback · PROVISIONAL" not in html


# ---------------------------------------------------------------------------
# Phase 2 — OBSERVED_ONLY explains retrospective reconstruction
# ---------------------------------------------------------------------------

def test_observed_only_reason_explains_retrospective_reconstruction():
    import sys
    sys.path.insert(0, str(ROOT))
    from src.discovery.service import DiscoveryService
    result = DiscoveryService._launch_profile(None)
    assert result["classification"] == "OBSERVED_ONLY"
    assert "reconstructed retrospectively" in result["reason"]
    assert "No verified provisioning session was recorded" in result["reason"]


# ---------------------------------------------------------------------------
# Phase 3 — Endpoint semantics: boundary vs unresolved gap, "Endpoint" gone
# ---------------------------------------------------------------------------

def test_generic_endpoint_label_removed():
    assert "<div class=\"vi-chain-role\">Endpoint</div>" not in HTML


def test_infrastructure_boundary_rendered_when_terminal_entity_present():
    snippet = _walkback_snippet()
    html = _run(
        "walkback("
        + json.dumps({"status": "PROVISIONAL", "hops": [], "stop_reason": "Attribution boundary reached."})
        + ",null,"
        + json.dumps({"terminal_entity": "AxiomRXZAq1J", "terminal_entity_type": "AUTOMATION"})
        + ",null,null)", snippet,
    )
    assert "Infrastructure Boundary" in html
    assert "AxiomRXZAq1J" in html
    assert "Endpoint" not in html


def test_walkback_stopped_rendered_when_no_terminal_entity():
    snippet = _walkback_snippet()
    html = _run(
        "walkback("
        + json.dumps({"status": "PROVISIONAL", "hops": [], "stop_reason": "No historical evidence."})
        + ",null,null,null,null)", snippet,
    )
    assert "Walkback Stopped" in html
    assert "Infrastructure Boundary" not in html
    assert "Endpoint" not in html


def test_canonical_operator_endpoint_unchanged():
    snippet = _walkback_snippet()
    html = _run(
        "walkback("
        + json.dumps({"status": "CONFIRMED", "hops": [], "stop_reason": "x"})
        + ",null,null,"
        + json.dumps({"operator_name": "WATCHTOWER", "confidence": "HIGH", "operator_id": "op1"})
        + ",null)", snippet,
    )
    assert "Canonical operator reached" in html
    assert "WATCHTOWER" in html


# ---------------------------------------------------------------------------
# Phase 4 — relationship chain entity labels unchanged (already correct)
# ---------------------------------------------------------------------------

def test_visual_chain_uses_real_entity_type_not_hardcoded_conclusion():
    assert "o.terminal_entity_type.toLowerCase()" in HTML
    assert "label:o.terminal_entity_type" in HTML


# ---------------------------------------------------------------------------
# Phase 5 — Analyst Summary: one sentence per existing field, none invented
# ---------------------------------------------------------------------------

def _summary_snippet():
    js = _script()
    return "\n".join([
        _extract_function("esc", js),
        _extract_function("typedLabel", js),
        _extract_function("analystSummary", js),
    ])


def test_summary_with_no_backing_fields_only_states_absence_of_operator():
    """canonical_identity being genuinely absent IS a real, existing fact
    (not fabricated) — the summary correctly still states it even when every
    other field is missing, rather than inventing profile/detection/outcome
    sentences it has no backing data for."""
    snippet = _summary_snippet()
    html = _run("analystSummary({})", snippet)
    assert "No canonical operator identified." in html
    for phrase in ("Verified provisioned launch", "Live detected", "Attribution", "reconstructed"):
        assert phrase not in html


def test_summary_provisioned_and_live_detected_and_relay_and_no_operator():
    snippet = _summary_snippet()
    d = {
        "launch_profile": {"classification": "PROVISIONED"},
        "detection_reconciliation": {"classification": "LIVE_DETECTED"},
        "attribution_outcome": {"outcome_type": "KNOWN_RELAY_REACHED",
                                 "terminal_entity": "AxiomRXZAq1J", "terminal_entity_type": "AUTOMATION"},
        "canonical_identity": None,
    }
    html = _run("analystSummary(" + json.dumps(d) + ")", snippet)
    assert "Verified provisioned launch." in html
    assert "Live detected." in html
    assert "automation infrastructure" in html.lower()
    assert "No canonical operator identified." in html
    assert "Launch Summary" in html


def test_summary_observed_only_and_walkback_recovered_and_canonical_operator():
    snippet = _summary_snippet()
    d = {
        "launch_profile": {"classification": "OBSERVED_ONLY"},
        "detection_reconciliation": {"classification": "WALKBACK_RECOVERED"},
        "attribution_outcome": {"outcome_type": "CANONICAL_OPERATOR_REACHED"},
        "canonical_identity": {"operator_name": "WATCHTOWER"},
    }
    html = _run("analystSummary(" + json.dumps(d) + ")", snippet)
    assert "reconstructed retrospectively" in html
    assert "Complete funding lineage established after the fact" in html
    assert "confirmed canonical operator" in html
    assert "Canonical operator: WATCHTOWER." in html


def test_summary_never_mentions_operation_identity_field_that_does_not_exist():
    """X25.0's Operation Identity is design-only; the summary must not
    fabricate an operation/mesh sentence since no such field exists yet."""
    snippet = _summary_snippet()
    d = {
        "launch_profile": {"classification": "PROVISIONED"},
        "detection_reconciliation": {"classification": "LIVE_DETECTED"},
        "attribution_outcome": {"outcome_type": "UNKNOWN_INFRASTRUCTURE"},
        "canonical_identity": None,
    }
    html = _run("analystSummary(" + json.dumps(d) + ")", snippet)
    for phrase in ("Mesh", "Operation", "operation"):
        assert phrase not in html


def test_summary_rendered_first_in_render_pipeline():
    assert "var summaryCard=analystSummary(d);" in HTML
    assert "innerHTML=identityHeader+summaryCard+infra" in HTML
