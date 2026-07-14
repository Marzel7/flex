"""X21D.1 — behavioral rendering tests for the Funding Walkback chain.

Extracts the real walkback()/walkbackLeadNodes() JS functions from
templates/discovery.html and executes them under Node against representative
Discovery-response shapes (no fabrication, real function code — not a
reimplementation), so these tests fail if the actual rendering logic
regresses, not just if a string disappears from the template.

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
    role_icon_line = re.search(r"var ROLE_ICON=\{[^}]*\};", js).group(0)
    pieces = [role_icon_line]
    for fn in ("esc", "abbr", "href", "walkbackLeadNodes", "walkback"):
        pieces.append(_extract_function(fn, js))
    return "\n".join(pieces)


_SNIPPET = _build_snippet()


def _render(w: dict, subject: dict, attribution_outcome, canonical_identity) -> str:
    script = (
        _SNIPPET
        + "\nconsole.log(JSON.stringify(walkback("
        + json.dumps(w) + "," + json.dumps(subject) + ","
        + json.dumps(attribution_outcome) + "," + json.dumps(canonical_identity)
        + ")));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=15, check=True
    )
    return json.loads(result.stdout)


CONFIRMED_TOKEN = {"id": "2Yy9egf9fnJCeDPrunjgCJseSH2axagQ8mUmdMWipump", "type": "token"}
CONFIRMED_WALKBACK = {
    "hops": [
        {"address": "3ue1agUnXhPRvZMs5JgKAdbNbXj2z7xVZYUhdvT4E1ZD", "role": "SUB_PROVISIONER", "confidence": "MEDIUM"},
        {"address": "9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4", "role": "TREASURY", "confidence": "HIGH"},
    ],
    "status": "CONFIRMED",
    "stop_reason": "Attribution complete. Canonical operator: WATCHTOWER.",
}
CREATOR_ADDR = "8PcwdR1n796ujCNMBQAiCZQp7SGD72dk5Riw3d7BsPL8"
CANONICAL_IDENTITY = {
    "operator_id": "04265d9f-6eb2-568c-a49e-9253091a4dbb",
    "operator_name": "WATCHTOWER",
    "confidence": "HIGH",
    "operator_href": "/intelligence/operator/04265d9f-6eb2-568c-a49e-9253091a4dbb",
}


def test_confirmed_token_with_persisted_creator_full_chain():
    html = _render(
        CONFIRMED_WALKBACK, CONFIRMED_TOKEN,
        {"evidence": {"creator": CREATOR_ADDR}}, CANONICAL_IDENTITY,
    )
    # token -> creator -> sub-provisioner -> treasury -> WATCHTOWER, in order.
    assert html.index("WATCHTOKEN") < html.index(">Creator<")
    assert html.index(">Creator<") < html.index("SUB_PROVISIONER")
    assert html.index("SUB_PROVISIONER") < html.index("TREASURY")
    assert html.index("TREASURY") < html.index("WATCHTOWER")
    assert "Canonical operator reached" in html
    assert CONFIRMED_TOKEN["id"] in html
    assert CREATOR_ADDR in html


def test_confirmed_token_without_persisted_creator():
    html = _render(
        CONFIRMED_WALKBACK, CONFIRMED_TOKEN,
        {"evidence": {"creator": None}}, CANONICAL_IDENTITY,
    )
    assert "WATCHTOKEN" in html
    assert ">Creator<" not in html
    assert html.index("WATCHTOKEN") < html.index("SUB_PROVISIONER")
    assert html.index("SUB_PROVISIONER") < html.index("TREASURY")
    assert html.index("TREASURY") < html.index("WATCHTOWER")


def test_unresolved_token_shows_only_persisted_nodes():
    walkback = {
        "hops": [{"address": "DWYT3zxsLdSWKjXs59rCMBLrZmhu9azFooyLv6avB5sE", "role": "SUB_PROVISIONER", "confidence": "MEDIUM"}],
        "status": "PROVISIONAL",
        "stop_reason": "Walkback stopped at a lineage gap. Retry only when new evidence arrives.",
    }
    html = _render(
        walkback, {"id": "G8rtCCXx89ZQdwsaGLXSuW3qHSPZvk4XhujQMFpepump", "type": "token"},
        {"evidence": {"creator": None}}, None,
    )
    assert "WATCHTOKEN" in html
    assert "SUB_PROVISIONER" in html
    assert "TREASURY" not in html
    assert ">Creator<" not in html
    assert "WATCHTOWER" not in html
    # No fabricated operator label; typed endpoint wording preserved.
    assert "Endpoint" in html
    assert "PROVISIONAL" in html


def test_treasury_search_does_not_prepend_unrelated_token():
    walkback = {
        "hops": [{"address": "SOME_SUBPROV", "role": "SUB_PROVISIONER", "confidence": "MEDIUM"}],
        "status": "PROVISIONAL", "stop_reason": "x",
    }
    html = _render(
        walkback, {"id": "SOME_TREASURY_ADDR", "type": "treasury"},
        {"evidence": {"creator": "SOME_CREATOR"}}, None,
    )
    assert "WATCHTOKEN" not in html
    assert ">Creator<" in html
    assert "SOME_CREATOR" in html


def test_creator_search_begins_with_creator_not_duplicated():
    walkback = {
        "hops": [{"address": "SOME_SUBPROV", "role": "SUB_PROVISIONER", "confidence": "MEDIUM"}],
        "status": "PROVISIONAL", "stop_reason": "x",
    }
    html = _render(
        walkback, {"id": "SOME_CREATOR_ADDR", "type": "creator"},
        {"evidence": {"creator": "SOME_CREATOR_ADDR"}}, None,
    )
    assert "WATCHTOKEN" not in html
    # A single node renders the address 3x (href, title attribute, visible text) —
    # confirm exactly ONE Creator node exists, not a duplicated pair.
    assert html.count('class="vi-chain-role">Creator<') == 1
    assert html.index(">Creator<") < html.index("SUB_PROVISIONER")


def test_no_fabricated_nodes_or_placeholder_addresses():
    walkback = {"hops": [], "status": "NO_ATTRIBUTION_FOUND", "stop_reason": "No funding evidence recorded."}
    html = _render(
        walkback, {"id": "SOME_TOKEN", "type": "token"},
        {"evidence": {"creator": None}}, None,
    )
    assert "unknown" not in html.lower()
    assert "placeholder" not in html.lower()
    assert ">Creator<" not in html
    assert "SUB_PROVISIONER" not in html
    assert "TREASURY" not in html


def test_existing_status_and_confidence_values_unchanged():
    html = _render(
        CONFIRMED_WALKBACK, CONFIRMED_TOKEN,
        {"evidence": {"creator": CREATOR_ADDR}}, CANONICAL_IDENTITY,
    )
    assert "Confidence MEDIUM" in html  # sub-provisioner hop, unchanged
    assert "Confidence HIGH" in html    # treasury hop, unchanged
    assert "Confidence HIGH" in html.split("WATCHTOWER")[-1]  # operator confidence shown too


def test_generic_confirmed_endpoint_only_replaced_when_operator_resolution_exists():
    # Same CONFIRMED walkback, but no canonical_identity resolved yet.
    html_no_identity = _render(
        CONFIRMED_WALKBACK, CONFIRMED_TOKEN,
        {"evidence": {"creator": CREATOR_ADDR}}, None,
    )
    assert "Canonical operator reached" not in html_no_identity
    assert "Endpoint" in html_no_identity
    assert "CONFIRMED" in html_no_identity

    html_with_identity = _render(
        CONFIRMED_WALKBACK, CONFIRMED_TOKEN,
        {"evidence": {"creator": CREATOR_ADDR}}, CANONICAL_IDENTITY,
    )
    assert "Canonical operator reached" in html_with_identity
