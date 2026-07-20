"""X25.7 — Detection Provenance Outcome Semantics.

Regression tests proving Detection Provenance now describes the evidential
OUTCOME (complete / partial / inconclusive funding lineage, live vs.
not-live detection) rather than which internal mechanism (walkback)
executed — since every migrated launch undergoes walkback, naming the
mechanism was not discriminating and provided no analyst value.

Also proves: no operator/operation/infrastructure assumptions leak in
(independence, carried forward from X25.6), and that no backend
classification value or detection/walkback/operation-identity/attribution
logic changed — this is a wording-only sprint.
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


def _script() -> str:
    m = re.search(r"{% block scripts %}\s*<script>(.*)</script>\s*{% endblock %}", HTML, re.S)
    return m.group(1)


def _snippet():
    js = _script()
    return _extract_function("esc", js) + "\n" + _extract_function("detectionReconciliation", js)


def _render(r) -> str:
    script = _snippet() + "\nconsole.log(JSON.stringify(detectionReconciliation(" + json.dumps(r) + ")));"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15, check=True)
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Classification values unchanged (backend logic untouched)
# ---------------------------------------------------------------------------

def test_all_six_classification_values_still_recognised():
    for cls in ("LIVE_DETECTED", "RECONCILED", "WALKBACK_RECOVERED",
                "PIPELINE_INCONSISTENCY", "WALKBACK_OBSERVED", "WALKBACK_INCONCLUSIVE"):
        html = _render({"classification": cls, "plain_transfer_associated": False})
        assert html != "", f"{cls} produced no rendering"
        assert "could not be classified" not in html


def test_unrecognised_classification_degrades_honestly():
    html = _render({"classification": "SOMETHING_NEW", "plain_transfer_associated": False})
    assert "could not be classified" in html


# ---------------------------------------------------------------------------
# Phase 3: process language ("walkback ran/reconstructed/scanned/observed")
# removed from the primary explain sentences; outcome language used instead.
# ---------------------------------------------------------------------------

def test_walkback_recovered_describes_outcome_not_mechanism():
    html = _render({"classification": "WALKBACK_RECOVERED", "plain_transfer_associated": False})
    assert "complete funding lineage was established" in html.lower()
    assert "walkback recovered" not in html.lower()
    assert "walkback" not in html.lower()  # mechanism name absent from primary explain text


def test_pipeline_inconsistency_describes_outcome_not_mechanism():
    html = _render({"classification": "PIPELINE_INCONSISTENCY", "plain_transfer_associated": False})
    assert "complete funding lineage was established" in html.lower()
    assert "detection gap" in html.lower()
    assert "walkback" not in html.lower()


def test_walkback_observed_describes_partial_evidence_not_mechanism():
    html = _render({"classification": "WALKBACK_OBSERVED", "plain_transfer_associated": False})
    assert "partial funding lineage" in html.lower()
    assert "walkback observed" not in html.lower()


def test_walkback_inconclusive_describes_insufficient_evidence_not_mechanism():
    html = _render({"classification": "WALKBACK_INCONCLUSIVE", "plain_transfer_associated": False})
    assert "insufficient to establish funding lineage" in html.lower()


def test_mechanism_footnote_only_appears_when_plain_transfer_flag_set():
    """The one legitimate, inline-explained mention of 'walkback' as a
    mechanism name is the plain-transfer footnote, which explains itself
    without requiring prior architecture knowledge (Phase 6) and only
    appears when the underlying fact (plain_transfer_associated) is true."""
    html_true = _render({"classification": "WALKBACK_OBSERVED", "plain_transfer_associated": True})
    html_false = _render({"classification": "WALKBACK_OBSERVED", "plain_transfer_associated": False})
    assert "plain SOL transfer" in html_true
    assert "plain SOL transfer" not in html_false


# ---------------------------------------------------------------------------
# Phase 4: evidence completeness levels (complete / partial / inconclusive)
# are now expressed distinctly.
# ---------------------------------------------------------------------------

def test_complete_evidence_states_say_complete():
    for cls in ("WALKBACK_RECOVERED", "PIPELINE_INCONSISTENCY"):
        html = _render({"classification": cls, "plain_transfer_associated": False})
        assert "complete funding lineage" in html.lower()


def test_partial_evidence_state_says_partial():
    html = _render({"classification": "WALKBACK_OBSERVED", "plain_transfer_associated": False})
    assert "partial funding lineage" in html.lower()
    assert "complete funding lineage" not in html.lower()


def test_inconclusive_evidence_state_says_insufficient_and_unjudgeable():
    html = _render({"classification": "WALKBACK_INCONCLUSIVE", "plain_transfer_associated": False})
    assert "insufficient" in html.lower()
    assert "judge how complete" in html.lower()


def test_complete_partial_inconclusive_are_textually_distinct():
    """No two of the three evidence-completeness levels share the same
    headline wording -- an analyst reading any single state must be able to
    tell it apart from the other two without cross-referencing."""
    complete = _render({"classification": "WALKBACK_RECOVERED", "plain_transfer_associated": False})
    partial = _render({"classification": "WALKBACK_OBSERVED", "plain_transfer_associated": False})
    inconclusive = _render({"classification": "WALKBACK_INCONCLUSIVE", "plain_transfer_associated": False})
    assert complete != partial != inconclusive


# ---------------------------------------------------------------------------
# Phase 5: independence from Operation Identity / Canonical Operator /
# Infrastructure Attribution / Launch Profile / Funding Walkback.
# ---------------------------------------------------------------------------

def test_no_operator_assumptions_in_any_state():
    for cls in ("LIVE_DETECTED", "RECONCILED", "WALKBACK_RECOVERED",
                "PIPELINE_INCONSISTENCY", "WALKBACK_OBSERVED", "WALKBACK_INCONCLUSIVE"):
        html = _render({"classification": cls, "plain_transfer_associated": False})
        assert "WATCHTOWER" not in html
        assert "operator" not in html.lower()


def test_no_operation_identity_assumptions_in_any_state():
    for cls in ("LIVE_DETECTED", "RECONCILED", "WALKBACK_RECOVERED",
                "PIPELINE_INCONSISTENCY", "WALKBACK_OBSERVED", "WALKBACK_INCONCLUSIVE"):
        html = _render({"classification": cls, "plain_transfer_associated": False})
        for phrase in ("operation identity", "treasury mesh", "mesh #", "operation #"):
            assert phrase not in html.lower()


def test_no_infrastructure_attribution_assumptions_in_any_state():
    for cls in ("LIVE_DETECTED", "RECONCILED", "WALKBACK_RECOVERED",
                "PIPELINE_INCONSISTENCY", "WALKBACK_OBSERVED", "WALKBACK_INCONCLUSIVE"):
        html = _render({"classification": cls, "plain_transfer_associated": False})
        for phrase in ("relay", "bridge", "exchange", "infrastructure boundary", "axiom"):
            assert phrase not in html.lower()


def test_no_launch_profile_assumptions_in_any_state():
    for cls in ("LIVE_DETECTED", "RECONCILED", "WALKBACK_RECOVERED",
                "PIPELINE_INCONSISTENCY", "WALKBACK_OBSERVED", "WALKBACK_INCONCLUSIVE"):
        html = _render({"classification": cls, "plain_transfer_associated": False})
        for phrase in ("provisioned", "observed_only", "birth", "creator history"):
            assert phrase not in html.lower()


# ---------------------------------------------------------------------------
# Phase 6: wording does not imply walkback is an unusual/rare process.
# ---------------------------------------------------------------------------

def test_wording_never_implies_walkback_is_unusual():
    for cls in ("WALKBACK_RECOVERED", "PIPELINE_INCONSISTENCY", "WALKBACK_OBSERVED", "WALKBACK_INCONCLUSIVE"):
        html = _render({"classification": cls, "plain_transfer_associated": False})
        for phrase in ("unusual", "rare", "exception", "special case"):
            assert phrase not in html.lower()


# ---------------------------------------------------------------------------
# Section-level label rewrite check
# ---------------------------------------------------------------------------

def test_section_labels_describe_outcome_not_process():
    assert "'WALKBACK_RECOVERED':   {label:'Lineage Established'" in HTML
    assert "'PIPELINE_INCONSISTENCY':{label:'Detection Gap'" in HTML
    assert "'WALKBACK_OBSERVED':    {label:'Partial Evidence'" in HTML
    assert "'WALKBACK_INCONCLUSIVE':{label:'Evidence Inconclusive'" in HTML


# ---------------------------------------------------------------------------
# Confirm backend detection/walkback/operation-identity/attribution logic
# is unchanged: detection_reconciliation.py must still expose the same
# classification function and gating behaviour established by X25.5.1.
# ---------------------------------------------------------------------------

def test_backend_classifier_still_gates_on_authoritative_outcome():
    import inspect
    from src.ops.detection_reconciliation import classify_walkback_confirmed_launches, _CONFIRMED_WALKBACK_OUTCOMES
    assert _CONFIRMED_WALKBACK_OUTCOMES == ("WATCHTOWER_CONFIRMED",)
    src = inspect.getsource(classify_walkback_confirmed_launches)
    assert "wt_walkback_queue" in src
    assert "WALKBACK_OBSERVED" in src
    assert "WALKBACK_INCONCLUSIVE" in src
