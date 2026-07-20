"""X24.1 Phase 5 — behavioral rendering tests for the detectionReconciliation()
card in Discovery.

Extracts the real detectionReconciliation() JS function from
templates/discovery.html and executes it under Node against representative
detection_reconciliation classifier output shapes, so these tests fail if the
actual rendering logic regresses, not just if a string disappears from the
template.

Per the sprint's explicit requirement: a WALKBACK_RECOVERED or
PIPELINE_INCONSISTENCY launch must say so explicitly ("recovered
retrospectively by walkback... live detection was not recorded") — never
implied to be a live catch. All copy is generated from real classifier fields;
nothing is fabricated.

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
    for fn in ("esc", "abbr", "href", "detectionReconciliation"):
        pieces.append(_extract_function(fn, js))
    return "\n".join(pieces)


_SNIPPET = _build_snippet()


def _render(r) -> str:
    script = _SNIPPET + "\nconsole.log(JSON.stringify(detectionReconciliation(" + json.dumps(r) + ")));"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15, check=True)
    return json.loads(result.stdout)


def test_absent_when_no_classification():
    assert _render(None) == ""


def test_live_detected_never_mentions_walkback_recovery():
    html = _render({"classification": "LIVE_DETECTED", "live_detection_source": "LIVE_STREAM",
                     "plain_transfer_associated": False})
    assert "Live Detected" in html
    assert "detected live" in html
    assert "recovered retrospectively" not in html


def test_reconciled_states_recovery_path_not_live_cascade():
    html = _render({"classification": "RECONCILED", "live_detection_source": "RECONCILE",
                     "plain_transfer_associated": False})
    assert "Reconciled" in html
    assert "not the live cascade" in html


def test_walkback_recovered_explicitly_states_no_live_detection():
    """X24.8 Phase 1: a walkback-only launch must state the evidence outcome
    and entry path as two separate facts, and never claim live detection.
    X25.7 replaced process-centric wording ('established by retrospective
    walkback') with pure outcome wording ('complete funding lineage was
    established... no live detection covered this launch')."""
    html = _render({"classification": "WALKBACK_RECOVERED", "live_detection_source": None,
                     "plain_transfer_associated": True})
    assert "complete funding lineage was established" in html.lower()
    assert "no live detection covered this launch" in html.lower()
    assert "Lineage Established" in html
    assert "WATCHTOWER attribution confirmed" not in html


def test_pipeline_inconsistency_flags_it_as_a_gap_not_expected_behaviour():
    html = _render({"classification": "PIPELINE_INCONSISTENCY", "live_detection_source": None,
                     "plain_transfer_associated": True})
    assert "Detection Gap" in html
    assert "detection gap" in html.lower()
    assert "complete funding lineage was established" in html.lower()
    assert "WATCHTOWER attribution confirmed" not in html


def test_plain_transfer_mechanism_note_only_shown_when_true():
    html_true = _render({"classification": "WALKBACK_RECOVERED", "plain_transfer_associated": True})
    html_false = _render({"classification": "WALKBACK_RECOVERED", "plain_transfer_associated": False})
    assert "plain SOL transfer" in html_true
    assert "plain SOL transfer" not in html_false


def test_no_percentage_or_probability_language():
    for cls in ("LIVE_DETECTED", "RECONCILED", "WALKBACK_RECOVERED", "PIPELINE_INCONSISTENCY"):
        html = _render({"classification": cls, "plain_transfer_associated": True})
        assert re.search(r"\d+%", html) is None
        assert "Likely" not in html
        assert "Probable" not in html


def test_unknown_classification_degrades_honestly():
    html = _render({"classification": "SOMETHING_NEW", "plain_transfer_associated": False})
    assert "could not be classified" in html
