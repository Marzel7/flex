"""X21E — behavioral rendering tests for the Operational Behaviour card.

Extracts the real operationalBehaviour()/opBehaviourTiming()/opBehaviourConsistency()
JS functions from templates/discovery.html and executes them under Node against
representative OperationalBehaviourService output shapes, so these tests fail if
the actual rendering logic regresses, not just if a string disappears from the
template.

Per the X21E sprint brief and the explicit user decision: no fabricated claims,
no percentages/probabilities, timing shown only when captured, and cards must
disappear gracefully when data is absent rather than rendering placeholders
that imply more knowledge than is actually persisted.

Skipped automatically if Node.js isn't available in the test environment.
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


def _build_snippet() -> str:
    m = re.search(r"{% block scripts %}\s*<script>(.*)</script>\s*{% endblock %}", HTML, re.S)
    js = m.group(1)
    pieces = []
    for fn in ("esc", "abbr", "href", "opBehaviourTiming", "opBehaviourConsistency", "operationalBehaviour"):
        pieces.append(_extract_function(fn, js))
    return "\n".join(pieces)


_SNIPPET = _build_snippet()


def _render(ob) -> str:
    script = _SNIPPET + "\nconsole.log(JSON.stringify(operationalBehaviour(" + json.dumps(ob) + ")));"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15, check=True)
    return json.loads(result.stdout)


RICH_OB = {
    "behaviour_summary": [
        "Creator funded after sub-provisioner (observed order, per persisted block times)",
        "Sub-provisioner funded creator via WSOL_WRAP_CLOSE",
        "Sub-provisioner has funded 1 creator (per wt_discovered_subprovs)",
        "Walkback completed successfully (provisioning session recorded)",
    ],
    "timing": {"available": True, "observations": [
        {"stage": "Treasury → Sub-Provisioner", "seconds": 58839},
        {"stage": "Sub-Provisioner → Creator", "seconds": 12},
    ]},
    "infrastructure_pattern": [
        {"label": "Sub-provisioner funded 1 creator", "source": "wt_discovered_subprovs.creator_count"},
        {"label": "Creator funding used WSOL wrap-close", "source": "wt_provisioning_edges.funding_mechanism"},
    ],
    "operational_consistency": [
        {"signal": "Infrastructure reuse", "status": "Not observed"},
        {"signal": "Creator funding structure (wrap-close)", "status": "Observed"},
        {"signal": "Repeated treasury", "status": "Not observed"},
        {"signal": "Full provisioning sequence recorded", "status": "Observed"},
        {"signal": "Observed timing", "status": "Observed"},
    ],
    "missing_evidence": [
        "Repeated treasury across multiple launches",
        "Provisioning hub reuse",
    ],
}

EMPTY_OB = None

PARTIAL_OB = {
    "behaviour_summary": [],
    "timing": {"available": False, "observations": []},
    "infrastructure_pattern": [],
    "operational_consistency": [
        {"signal": "Infrastructure reuse", "status": "Not yet available"},
        {"signal": "Creator funding structure (wrap-close)", "status": "Not yet available"},
        {"signal": "Repeated treasury", "status": "Not yet available"},
        {"signal": "Full provisioning sequence recorded", "status": "Not observed"},
        {"signal": "Observed timing", "status": "Not yet available"},
    ],
    "missing_evidence": [
        "Repeated treasury across multiple launches",
        "Repeated provisioning edges",
        "Observed timing history",
        "Multiple launches from this sub-provisioner",
        "Provisioning hub reuse",
    ],
}


def test_rich_case_renders_all_sections():
    html = _render(RICH_OB)
    assert "Behaviour summary" in html
    assert "Sub-provisioner funded creator via WSOL_WRAP_CLOSE" in html
    assert "Infrastructure pattern" in html
    assert "wt_discovered_subprovs.creator_count" in html
    assert "Timing observations" in html
    assert "58839" in html
    assert "Operational consistency" in html
    assert "Missing evidence" in html
    assert "Repeated treasury across multiple launches" in html


def test_card_absent_when_operational_behaviour_is_none():
    assert _render(EMPTY_OB) == ""


def test_timing_shows_not_yet_captured_when_unavailable():
    html = _render(PARTIAL_OB)
    assert "Not yet captured" in html
    assert "58839" not in html


def test_consistency_uses_tristate_never_a_percentage():
    html = _render(RICH_OB)
    assert re.search(r"\d+%", html) is None
    assert "Likely" not in html
    assert "Probable" not in html
    assert "Observed" in html
    assert "Not observed" in html


def test_partial_case_omits_empty_sections_but_keeps_consistency_and_missing_evidence():
    html = _render(PARTIAL_OB)
    assert "Behaviour summary" not in html
    assert "Infrastructure pattern" not in html
    assert "Operational consistency" in html
    assert "Missing evidence" in html


def test_no_fresh_wallet_or_composite_infrastructure_claims_ever_rendered():
    html = _render(RICH_OB)
    assert "fresh" not in html.lower()
    assert "Token-specific infrastructure" not in html


def test_card_absent_when_all_sections_empty_but_object_present():
    empty_but_present = {
        "behaviour_summary": [], "timing": {"available": False, "observations": []},
        "infrastructure_pattern": [], "operational_consistency": [], "missing_evidence": [],
    }
    assert _render(empty_but_present) == ""
