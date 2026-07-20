"""X25.5.1 — Correct Detection Reconciliation Membership Gating.

Fixes the X25.5-audited defect: classify_walkback_confirmed_launches()
previously treated wt_provisioning_sessions row existence as proof of
confirmed WATCHTOWER operation membership, when the table is deliberately
an operation-agnostic, append-only evidence table. 97.5% of real rows
belonged to mints whose walkback outcome was LINEAGE_GAP, not
WATCHTOWER_CONFIRMED, yet were rendered as "part of a WATCHTOWER-tracked
operation."

This suite proves the fix: membership is now gated on the mint's
authoritative wt_walkback_queue.intelligence_outcome, and non-confirmed
rows are downgraded to neutral WALKBACK_OBSERVED / WALKBACK_INCONCLUSIVE
states that never claim membership.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.ops.detection_reconciliation import classify_walkback_confirmed_launches

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/discovery.html").read_text()


@pytest.fixture
def ops_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE wt_provisioning_sessions (
            session_id TEXT, source_mint TEXT, treasury TEXT, subprov TEXT, creator TEXT,
            treasury_to_subprov_block_time INTEGER, subprov_to_creator_block_time INTEGER,
            creator_launch_time INTEGER,
            treasury_to_subprov_latency_seconds INTEGER, subprov_to_creator_latency_seconds INTEGER,
            creator_to_launch_latency_seconds INTEGER,
            treasury_to_subprov_mechanism TEXT, subprov_to_creator_mechanism TEXT,
            treasury_to_subprov_amount_sol REAL, subprov_to_creator_amount_sol REAL,
            recorded_at INTEGER
        );
        CREATE TABLE wt_provisioning_edges (
            edge_id TEXT PRIMARY KEY, edge_type TEXT, from_wallet TEXT, to_wallet TEXT,
            funding_mechanism TEXT, funding_block_time INTEGER, source_mint TEXT
        );
        CREATE TABLE wt_watchtower_launches (
            mint TEXT PRIMARY KEY, creator_wallet TEXT, treasury_wallet TEXT, subprov_wallet TEXT,
            create_time INTEGER, detection_source TEXT
        );
        CREATE TABLE wt_active_subprov_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, subprov_wallet TEXT, treasury_wallet TEXT,
            funding_time INTEGER, expires_at INTEGER, monitoring_state TEXT,
            funding_mechanism TEXT, detected_at INTEGER
        );
        CREATE TABLE wt_walkback_queue (
            mint TEXT PRIMARY KEY, intelligence_outcome TEXT
        );
        CREATE TABLE wt_attribution_outcomes (
            mint TEXT PRIMARY KEY, outcome_type TEXT, terminal_entity TEXT, terminal_entity_type TEXT
        );
    """)
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


def _insert(path, table, **cols):
    conn = sqlite3.connect(path)
    keys = ",".join(cols.keys())
    placeholders = ",".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", tuple(cols.values()))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Required test 1-3: known non-confirming outcomes never render membership
# ---------------------------------------------------------------------------

def test_lineage_gap_never_renders_watchtower_membership(ops_db):
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_GAP", treasury=None,
            subprov="S1", creator="C1", subprov_to_creator_mechanism="PLAIN_XFER",
            creator_launch_time=100, recorded_at=110)
    _insert(ops_db, "wt_walkback_queue", mint="MINT_GAP", intelligence_outcome="LINEAGE_GAP")
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_GAP")
    assert row["classification"] == "WALKBACK_OBSERVED"
    assert row["classification"] not in ("WALKBACK_RECOVERED", "PIPELINE_INCONSISTENCY")


def test_no_attribution_found_never_renders_watchtower_membership(ops_db):
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_NOATTR", treasury=None,
            subprov="S2", creator="C2", subprov_to_creator_mechanism="PLAIN_XFER",
            creator_launch_time=200, recorded_at=210)
    _insert(ops_db, "wt_walkback_queue", mint="MINT_NOATTR", intelligence_outcome="NO_ATTRIBUTION_FOUND")
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_NOATTR")
    assert row["classification"] == "WALKBACK_OBSERVED"


def test_known_relay_reached_alone_never_renders_watchtower_membership(ops_db):
    """A KNOWN_RELAY_REACHED attribution outcome (e.g. Axiom) with no
    confirmed walkback outcome must not imply membership either."""
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_RELAY", treasury=None,
            subprov="S3", creator="C3", subprov_to_creator_mechanism="PLAIN_XFER",
            creator_launch_time=300, recorded_at=310)
    _insert(ops_db, "wt_walkback_queue", mint="MINT_RELAY", intelligence_outcome="LINEAGE_GAP")
    _insert(ops_db, "wt_attribution_outcomes", mint="MINT_RELAY", outcome_type="KNOWN_RELAY_REACHED",
            terminal_entity="AxiomRXZAq1J", terminal_entity_type="AUTOMATION")
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_RELAY")
    assert row["classification"] == "WALKBACK_OBSERVED"


def test_non_watchtower_outcome_never_renders_membership(ops_db):
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_NONWT", treasury="T9",
            subprov="S9", creator="C9", subprov_to_creator_mechanism="WSOL_WRAP_CLOSE",
            creator_launch_time=400, recorded_at=410)
    _insert(ops_db, "wt_walkback_queue", mint="MINT_NONWT", intelligence_outcome="NON_WATCHTOWER")
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_NONWT")
    assert row["classification"] == "WALKBACK_OBSERVED"


# ---------------------------------------------------------------------------
# Required test 4: a partial provisioning-session row alone is insufficient
# ---------------------------------------------------------------------------

def test_partial_provisioning_row_alone_is_insufficient(ops_db):
    """No wt_walkback_queue row at all — cannot determine membership from
    persisted data. Must be WALKBACK_INCONCLUSIVE, never a membership claim."""
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_PARTIAL", treasury=None,
            subprov="S4", creator="C4", subprov_to_creator_mechanism="PLAIN_XFER",
            creator_launch_time=500, recorded_at=510)
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_PARTIAL")
    assert row["classification"] == "WALKBACK_INCONCLUSIVE"
    assert row["classification"] not in ("WALKBACK_RECOVERED", "PIPELINE_INCONSISTENCY")


# ---------------------------------------------------------------------------
# Required test 5-6: genuine confirmation still renders correctly
# ---------------------------------------------------------------------------

def test_watchtower_confirmed_can_still_render_retrospective_recovery(ops_db):
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_CONFIRMED", treasury="T5",
            subprov="S5", creator="C5", subprov_to_creator_mechanism="WSOL_WRAP_CLOSE",
            creator_launch_time=600, recorded_at=610)
    _insert(ops_db, "wt_walkback_queue", mint="MINT_CONFIRMED", intelligence_outcome="WATCHTOWER_CONFIRMED")
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_CONFIRMED")
    assert row["classification"] == "WALKBACK_RECOVERED"


def test_confirmed_live_armed_miss_still_renders_pipeline_inconsistency(ops_db):
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_PIPEBUG", treasury="T6",
            subprov="S6", creator="C6", subprov_to_creator_mechanism="WSOL_WRAP_CLOSE",
            creator_launch_time=1784052892, recorded_at=1784052909)
    _insert(ops_db, "wt_walkback_queue", mint="MINT_PIPEBUG", intelligence_outcome="WATCHTOWER_CONFIRMED")
    _insert(ops_db, "wt_active_subprov_sessions", subprov_wallet="S6", treasury_wallet="T6",
            funding_time=1784051480, expires_at=1784053553, monitoring_state="LIVE_ARMED",
            funding_mechanism="PLAIN_TRANSFER", detected_at=1784051480)
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_PIPEBUG")
    assert row["classification"] == "PIPELINE_INCONSISTENCY"


# ---------------------------------------------------------------------------
# Required test 7: Canonical Operator / Operation Identity remain independent
# ---------------------------------------------------------------------------

def test_canonical_operator_and_operation_identity_independent_of_gating_fix():
    """The gating fix only touches detection_reconciliation classification
    logic — canonicalIdentity() and operationIdentity() rendering in the
    frontend are untouched and remain gated on their own independent fields."""
    assert "if(canonicalIdentity && canonicalIdentity.operator_name){" in HTML
    assert "function operationIdentity(oi){" in HTML
    assert "if(!oi || !oi.operation_id)return '';" in HTML


# ---------------------------------------------------------------------------
# Required test 8: the real 278 lineage-gap rows no longer receive the claim
# ---------------------------------------------------------------------------

def test_lineage_gap_rows_never_classified_as_confirmed_membership_at_scale(ops_db):
    """Simulates the real-world shape found in X25.5's audit: many
    provisioning-session rows whose walkback outcome is LINEAGE_GAP. None of
    them may receive WALKBACK_RECOVERED/PIPELINE_INCONSISTENCY."""
    for i in range(50):
        mint = f"MINT_BULK_{i}"
        _insert(ops_db, "wt_provisioning_sessions", source_mint=mint, treasury=None,
                subprov=f"S{i}", creator=f"C{i}", subprov_to_creator_mechanism="PLAIN_XFER",
                creator_launch_time=1000 + i, recorded_at=1010 + i)
        _insert(ops_db, "wt_walkback_queue", mint=mint, intelligence_outcome="LINEAGE_GAP")
    result = classify_walkback_confirmed_launches(ops_db)
    bulk_rows = [r for r in result["rows"] if r["mint"].startswith("MINT_BULK_")]
    assert len(bulk_rows) == 50
    assert all(r["classification"] == "WALKBACK_OBSERVED" for r in bulk_rows)
    assert result["summary"].get("WALKBACK_RECOVERED", 0) == 0
    assert result["summary"].get("PIPELINE_INCONSISTENCY", 0) == 0
    assert result["summary"]["WALKBACK_OBSERVED"] == 50


# ---------------------------------------------------------------------------
# Frontend wording: WALKBACK_OBSERVED / WALKBACK_INCONCLUSIVE never claim membership
# ---------------------------------------------------------------------------

pytestmark_node = pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")


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


def _render(r) -> str:
    js = _script()
    snippet = _extract_function("esc", js) + "\n" + _extract_function("detectionReconciliation", js)
    script = snippet + "\nconsole.log(JSON.stringify(detectionReconciliation(" + json.dumps(r) + ")));"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15, check=True)
    return json.loads(result.stdout)


@pytestmark_node
def test_walkback_observed_never_says_watchtower_tracked_operation():
    """X25.7 relabelled this state 'Partial Evidence' (outcome-focused,
    since every migrated launch undergoes the same retrospective process —
    naming the process was not discriminating)."""
    html = _render({"classification": "WALKBACK_OBSERVED", "plain_transfer_associated": False})
    assert "WATCHTOWER-tracked operation" not in html
    assert "Partial Evidence" in html


@pytestmark_node
def test_walkback_inconclusive_never_says_watchtower_tracked_operation():
    html = _render({"classification": "WALKBACK_INCONCLUSIVE", "plain_transfer_associated": False})
    assert "WATCHTOWER-tracked operation" not in html
    assert "Evidence Inconclusive" in html


@pytestmark_node
def test_walkback_recovered_still_asserts_confirmed_operation_lineage():
    """The genuinely-confirmed case must be unaffected by this fix. X25.6
    replaced the operator-specific 'WATCHTOWER-tracked operation' wording
    with operator-neutral wording; X25.7 further replaced the
    process-centric framing with pure outcome wording ('complete funding
    lineage was established') — the underlying gating logic (this fix's
    actual subject) is what must be preserved throughout."""
    html = _render({"classification": "WALKBACK_RECOVERED", "plain_transfer_associated": False})
    assert "complete funding lineage was established" in html.lower()
    assert "WATCHTOWER" not in html


@pytestmark_node
def test_pipeline_inconsistency_still_asserts_confirmed_operation_lineage():
    html = _render({"classification": "PIPELINE_INCONSISTENCY", "plain_transfer_associated": False})
    assert "complete funding lineage was established" in html.lower()
    assert "WATCHTOWER" not in html
