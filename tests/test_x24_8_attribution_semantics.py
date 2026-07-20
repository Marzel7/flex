"""X24.8 — Discovery Attribution Semantics Cleanup: behavioral tests proving
the four Discovery concepts (Detection Provenance, Relationship Chain,
Operator Attribution, Infrastructure Attribution) stay verbally independent.

Extracts the real JS functions/vars from templates/discovery.html and executes
them under Node against representative data shapes, so these tests fail if the
actual rendering logic regresses, not just if a string disappears.

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


def _build_snippet() -> str:
    js = _script()
    pieces = []
    for fn in ("esc", "abbr", "href", "ago"):
        pieces.append(_extract_function(fn, js))
    pieces.append(_extract_var("ANALYST_WORDING", js))
    pieces.append(_extract_var("DEFAULT_WORDING", js))
    pieces.append(_extract_var("ISTATE_CLASS", js))
    for fn in ("istateChip", "knownFields", "recordedLineage", "outcome",
               "canonicalIdentity", "detectionReconciliation"):
        pieces.append(_extract_function(fn, js))
    return "\n".join(pieces)


_SNIPPET = _build_snippet()


def _run(expr: str) -> str:
    script = _SNIPPET + "\nconsole.log(JSON.stringify(" + expr + "));"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15, check=True)
    return json.loads(result.stdout)


def _outcome(o: dict) -> str:
    return _run("outcome(" + json.dumps(o) + ")")


def _detection(r: dict) -> str:
    return _run("detectionReconciliation(" + json.dumps(r) + ")")


def _canonical(c: dict) -> str:
    return _run("canonicalIdentity(" + json.dumps(c) + ")")


# ---------------------------------------------------------------------------
# Phase 5 requirement: KNOWN_RELAY_REACHED with no canonical operator must
# never render "WATCHTOWER attribution confirmed" (or any WATCHTOWER mention).
# ---------------------------------------------------------------------------

def test_known_relay_reached_never_mentions_watchtower():
    html = _outcome({
        "outcome_type": "KNOWN_RELAY_REACHED", "confidence": "HIGH",
        "stop_reason": "Attribution boundary reached. Known infrastructure boundary: Axiom.",
        "terminal_entity": "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk",
        "terminal_entity_type": "AUTOMATION",
        "should_seed_emerging_operator": 0,
    })
    assert "WATCHTOWER" not in html
    assert "attribution confirmed" not in html.lower()
    assert "Known Infrastructure" in html or "Infrastructure" in html


def test_known_cex_and_bridge_reached_never_mention_watchtower():
    for outcome_type, name in (("KNOWN_CEX_REACHED", "Binance"), ("KNOWN_BRIDGE_REACHED", "Wormhole")):
        html = _outcome({
            "outcome_type": outcome_type, "confidence": "HIGH",
            "stop_reason": "Attribution boundary reached.",
            "terminal_entity": "SomeAddr111111111111111111111111111111111",
            "terminal_entity_type": "CEX" if "CEX" in outcome_type else "BRIDGE",
            "should_seed_emerging_operator": 0,
        })
        assert "WATCHTOWER" not in html


def test_canonical_operator_reached_outcome_does_not_claim_infrastructure_boundary():
    """The one outcome type that IS operator attribution must not borrow
    infrastructure-boundary language ("known relay/bridge/CEX")."""
    html = _outcome({
        "outcome_type": "CANONICAL_OPERATOR_REACHED", "confidence": "HIGH",
        "stop_reason": "Reached canonical operator.",
        "terminal_entity": None, "terminal_entity_type": "OPERATOR",
        "should_seed_emerging_operator": 0,
    })
    for phrase in ("known relay", "known bridge", "known exchange", "known cex"):
        assert phrase not in html.lower()


# ---------------------------------------------------------------------------
# Canonical operator card must only render when operator_name exists.
# ---------------------------------------------------------------------------

def test_canonical_identity_absent_when_no_operator():
    assert _canonical(None) == ""


def test_canonical_identity_never_mentions_infrastructure_or_relay():
    html = _canonical({"operator_name": "WATCHTOWER Prime", "operator_id": "op_1",
                        "operator_href": "/intelligence/operators/op_1", "confidence": "HIGH",
                        "identity_signals": ["vanity prefix match"]})
    assert "WATCHTOWER Prime" in html
    assert "relay" not in html.lower()
    assert "bridge" not in html.lower()
    assert "exchange" not in html.lower()
    assert "known infrastructure" not in html.lower()


# ---------------------------------------------------------------------------
# Detection Provenance: new wording, new section label, no operator-identity
# or infrastructure-boundary claims.
# ---------------------------------------------------------------------------

def test_detection_provenance_section_label_is_not_generic():
    html = _detection({"classification": "LIVE_DETECTED", "live_detection_source": "LIVE_STREAM",
                        "plain_transfer_associated": False})
    assert "Detection Provenance" in html
    assert "How was this obtained" not in html


def test_detection_provenance_never_says_watchtower_attribution_confirmed():
    for cls in ("LIVE_DETECTED", "RECONCILED", "WALKBACK_RECOVERED", "PIPELINE_INCONSISTENCY"):
        html = _detection({"classification": cls, "live_detection_source": "LIVE_STREAM",
                            "plain_transfer_associated": False})
        assert "WATCHTOWER attribution confirmed" not in html


def test_detection_provenance_never_mentions_infrastructure_boundary_names():
    for cls in ("LIVE_DETECTED", "RECONCILED", "WALKBACK_RECOVERED", "PIPELINE_INCONSISTENCY"):
        html = _detection({"classification": cls, "plain_transfer_associated": False})
        for phrase in ("known relay", "known bridge", "known exchange", "axiom"):
            assert phrase not in html.lower()


def test_walkback_recovered_states_operation_membership_and_entry_path_separately():
    """X25.6 replaced 'part of a WATCHTOWER-tracked operation' with
    operator-neutral wording; X25.7 further replaced the process-centric
    framing with pure outcome wording ('complete funding lineage was
    established... no live detection'). The evidence-completeness fact and
    the live-vs-not-live fact remain two separate sentences, without naming
    a specific operator or mechanism."""
    html = _detection({"classification": "WALKBACK_RECOVERED", "live_detection_source": None,
                        "plain_transfer_associated": True})
    assert "complete funding lineage was established" in html.lower()
    assert "no live detection covered this launch" in html.lower()
    assert "WATCHTOWER" not in html


def test_pipeline_inconsistency_states_operation_membership_and_gap_separately():
    html = _detection({"classification": "PIPELINE_INCONSISTENCY", "live_detection_source": None,
                        "plain_transfer_associated": True})
    assert "complete funding lineage was established" in html.lower()
    assert "detection gap" in html.lower()
    assert "WATCHTOWER" not in html


# ---------------------------------------------------------------------------
# Infrastructure attribution remains independent of operator/detection wording.
# ---------------------------------------------------------------------------

def test_infrastructure_outcomes_never_mention_detection_provenance_language():
    for outcome_type in ("KNOWN_RELAY_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_CEX_REACHED"):
        html = _outcome({
            "outcome_type": outcome_type, "confidence": "HIGH",
            "stop_reason": "Attribution boundary reached.",
            "terminal_entity": "SomeAddr111111111111111111111111111111111",
            "terminal_entity_type": "AUTOMATION",
            "should_seed_emerging_operator": 0,
        })
        for phrase in ("live detected", "reconciled", "walkback recovered", "pipeline inconsistency"):
            assert phrase not in html.lower()


# ---------------------------------------------------------------------------
# Regression guard: all existing outcome types still render a title (wording-
# only change, no outcome type silently dropped).
# ---------------------------------------------------------------------------

def test_all_known_outcome_types_still_render_a_title():
    for outcome_type in (
        "CANONICAL_OPERATOR_REACHED", "KNOWN_MULTI_TOKEN_CREATOR", "KNOWN_CEX_REACHED",
        "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED", "UNKNOWN_INFRASTRUCTURE",
        "LINEAGE_GAP", "AMBIGUOUS_BRANCH", "MAX_DEPTH", "INSUFFICIENT_EVIDENCE",
    ):
        html = _outcome({
            "outcome_type": outcome_type, "confidence": "HIGH", "stop_reason": "x",
            "terminal_entity": None, "terminal_entity_type": "X",
            "should_seed_emerging_operator": 0,
        })
        assert "dw-outcome-title" in html
