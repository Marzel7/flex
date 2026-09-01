"""
Focused tests for the shared "Verified [Operation] Tokens" card pattern applied
to both Nexus (reference) and Leviathan (src/ops/operator_reader.py,
templates/operator_intelligence.html). Presentation is shared via the
verifiedTokenCard() JS helper; detector semantics remain fully operation-specific.

No network calls, no RPC, no writes to production tables.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ops.operator_reader import (
    _nexus_detector_projection, _leviathan_detector_projection,
    _NEXUS_OPERATOR_ID, _LEVIATHAN_OPERATOR_ID,
)

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "database", "wt_ops_v2.db")
P3R_13A04_OPERATOR_ID = "ccb7b1b0-56e1-4543-9e95-3f284bed3943"


def _conn():
    return sqlite3.connect(DB_PATH)


def _template_src():
    with open(os.path.join(ROOT, "templates", "operator_intelligence.html")) as f:
        return f.read()


# ---------------- shared presentation helper exists and is reused ----------------

def test_shared_verified_token_card_helper_exists():
    src = _template_src()
    assert "var verifiedTokenCard = function(title, exactCount, verifiedLabel, verifiedCount, description)" in src


def test_both_operations_call_the_shared_helper():
    src = _template_src()
    nexus_call_idx = src.find("verifiedTokenCard(\n      'Verified Nexus Tokens'")
    lev_call_idx = src.find("verifiedTokenCard(\n      'Verified Leviathan Tokens'")
    assert nexus_call_idx != -1
    assert lev_call_idx != -1


def test_no_independent_duplicate_card_markup():
    """The old literal <section class="oi-summary-card"><h2>Verified Nexus Tokens... markup
    should no longer be hand-duplicated inline; it must route through the shared helper."""
    src = _template_src()
    assert "'<section class=\"oi-summary-card\"><h2>Verified Nexus Tokens</h2>" not in src


# ---------------- Nexus regression: identical rendered semantics ----------------

def test_nexus_projection_unaffected_by_leviathan_addition():
    conn = _conn()
    projection = _nexus_detector_projection(conn)
    assert "counts" in projection
    assert "UNIQUE_MATCH" in projection["counts"]


def test_nexus_description_text_unchanged():
    src = _template_src()
    assert "Each displayed token meets the complete direct 10,000-lamport-to-associated-creator contract. Excluded comparison rows are not included in this Nexus view." in src


def test_nexus_card_title_unchanged():
    src = _template_src()
    assert "'Verified Nexus Tokens'" in src


# ---------------- Leviathan card semantics ----------------

def test_leviathan_card_title_present():
    src = _template_src()
    assert "'Verified Leviathan Tokens'" in src


def test_leviathan_description_operation_specific_not_generic_detector_id():
    src = _template_src()
    assert "Each displayed token meets the complete Leviathan WSOL wrap-close operation contract. Rejected and ambiguous comparison rows are not included in this Leviathan view." in src
    # must not expose the raw internal detector id as the primary explanatory text
    assert "P3R_UNIFIED_WSOL_WRAP_CLOSE_99_999985_SOL.v1" not in src


def test_leviathan_exact_and_verified_counts_derived_from_projection_fields():
    src = _template_src()
    idx = src.find("'Verified Leviathan Tokens', leviathanDetector.exact_count")
    assert idx != -1
    snippet = src[idx:idx + 200]
    assert "leviathanDetector.verified_total" in snippet
    # no hardcoded literal counts near this call
    assert "209" not in snippet


def test_no_hardcoded_91_near_nexus_card_call():
    src = _template_src()
    idx = src.find("'Verified Nexus Tokens', nexusDetector.counts.UNIQUE_MATCH")
    assert idx != -1
    snippet = src[idx:idx + 200]
    assert "91" not in snippet


# ---------------- counts derived from live current state ----------------

def test_leviathan_exact_verified_counts_from_live_state():
    conn = _conn()
    projection = _leviathan_detector_projection(conn)
    live_membership = conn.execute(
        "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (_LEVIATHAN_OPERATOR_ID,)
    ).fetchone()[0]
    assert projection["exact_count"] <= live_membership
    assert projection["verified_total"] == projection["exact_count"] or projection["verified_total"] == live_membership


def test_pending_excluded_from_exact_and_verified_counts():
    conn = _conn()
    projection = _leviathan_detector_projection(conn)
    pending_mints = {r["mint"] for r in projection["rows"] if r["raw_result"] == "PENDING_REPLAY"}
    exact_mints = {r["mint"] for r in projection["rows"] if r["raw_result"] == "EXACT"}
    assert pending_mints.isdisjoint(exact_mints)


def test_member_not_exact_never_counted_as_exact():
    conn = _conn()
    projection = _leviathan_detector_projection(conn)
    not_exact_mints = {r["mint"] for r in projection["rows"] if r["raw_result"] == "MEMBER_NOT_CURRENTLY_EXACT"}
    exact_mints = {r["mint"] for r in projection["rows"] if r["raw_result"] == "EXACT"}
    assert not_exact_mints.isdisjoint(exact_mints)
    if not_exact_mints:
        # exact_count must reflect only actual EXACT rows
        assert projection["exact_count"] == len(exact_mints)


# ---------------- rejected lookalikes / P3R_13A04 safety ----------------

def test_rejected_lookalikes_absent_from_verified_leviathan_population():
    audit_path = os.path.join(ROOT, "docs", "audits", "leviathan_detector_match_ui.v1.json")
    with open(audit_path) as f:
        prior = json.load(f)
    rejected = {r["mint"] for r in prior.get("rejected_lookalikes", {}).get("reasons_sample", [])}
    conn = _conn()
    projection = _leviathan_detector_projection(conn)
    row_mints = {r["mint"] for r in projection["rows"]}
    assert rejected.isdisjoint(row_mints)


def test_p3r_13a04_zero_rows_in_verified_leviathan():
    conn = _conn()
    p3r_13a04_mints = {r[0] for r in conn.execute(
        "SELECT mint FROM operator_launch_membership WHERE operator_id=?", (P3R_13A04_OPERATOR_ID,)
    ).fetchall()}
    projection = _leviathan_detector_projection(conn)
    row_mints = {r["mint"] for r in projection["rows"]}
    assert p3r_13a04_mints.isdisjoint(row_mints)


# ---------------- unified population, no stale sources ----------------

def test_no_stale_13_mint_profile_source_reintroduced():
    src = _template_src()
    # the old fallback that read the frozen behavioural_profile.member_mints for Leviathan markers
    assert "leviathanDetector ? '<span class=\"oi-detector-mark exact\"" not in src


def test_no_recently_admitted_subclass_reintroduced():
    src = _template_src()
    assert "renderPendingMint" not in src
    assert "pending observations (full replay not yet promoted)" not in src


# ---------------- old redundant micro-summary removed ----------------

def test_old_leviathan_micro_summary_line_removed():
    src = _template_src()
    assert "Detector · '+esc(leviathanDetector.exact_count)+' exact / '+esc(leviathanDetector.verified_total)+' verified" not in src


def test_new_pending_note_only_shown_when_nonzero():
    src = _template_src()
    idx = src.find("leviathanDetector.pending_count ? '<p")
    assert idx != -1


# ---------------- accessibility ----------------

def test_metrics_have_readable_text_labels_not_color_only():
    src = _template_src()
    # metric() helper renders a text label alongside the number; verify it's used, not just a colored dot
    assert "var metric = function(label, value)" in src


def test_marker_tooltips_present_for_accessibility():
    src = _template_src()
    assert "Verified exact Leviathan detector match" in src
    assert "data-tooltip=" in src and "aria-label=" in src
    # the mark span must carry both data-tooltip and aria-label, not color alone
    idx = src.find("EXACT:['exact','●','Verified exact Leviathan detector match']")
    assert idx != -1


# ---------------- other operations / safety ----------------

def test_other_operations_not_gated_into_shared_card():
    src = _template_src()
    # verifiedTokenCard must only be invoked for nexusDetector / leviathanDetector, no generic operation branch
    idx = src.find("var verifiedTokenCard")
    following = src[idx:idx + 4000]
    assert following.count("verifiedTokenCard(") <= 3  # definition + 2 call sites (Nexus, Leviathan)


def test_no_detector_membership_dispatch_writes_in_reader_or_template():
    reader_path = os.path.join(ROOT, "src", "ops", "operator_reader.py")
    with open(reader_path) as f:
        reader_src = f.read()
    for forbidden in ("INSERT INTO operator_launch_membership", "DELETE FROM operator_launch_membership",
                       "UPDATE operator_launch_membership"):
        assert forbidden not in reader_src
    import subprocess
    result = subprocess.run(
        ["git", "diff", "--stat", "src/ops/p3r_profile_candidate_matcher.py"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == ""
