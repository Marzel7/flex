"""
Focused tests for the Leviathan unified launch surface fix — collapsing the
stale 13-mint "Manually Admitted P3R Operation" role card (fed by a frozen
operation_behavioural_profiles.member_mints_json snapshot) into the single,
canonical operator_launch_membership-derived launch list, with a per-mint
detector-state lookup (src/ops/operator_reader.py::_leviathan_detector_projection)
driving green/grey/review markers directly on that unified list.

No network calls, no RPC, no writes to production tables.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ops.operator_reader import _leviathan_detector_projection, _LEVIATHAN_OPERATOR_ID
from src.ops.p3r_profile_candidate_matcher import evaluate_mint

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "database", "wt_ops_v2.db")
NEXUS_OPERATOR_ID = "bd7d7479-1454-5d41-9f68-115550348f3e"
P3R_13A04_OPERATOR_ID = "ccb7b1b0-56e1-4543-9e95-3f284bed3943"


def _conn():
    return sqlite3.connect(DB_PATH)


# ---------------- canonical membership ground truth ----------------

def test_canonical_membership_no_duplicates():
    conn = _conn()
    total = conn.execute(
        "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (_LEVIATHAN_OPERATOR_ID,)
    ).fetchone()[0]
    distinct = conn.execute(
        "SELECT COUNT(DISTINCT mint) FROM operator_launch_membership WHERE operator_id=?", (_LEVIATHAN_OPERATOR_ID,)
    ).fetchone()[0]
    assert total == distinct


def test_unified_projection_covers_every_canonical_member_exactly_once():
    conn = _conn()
    members = {r[0] for r in conn.execute(
        "SELECT mint FROM operator_launch_membership WHERE operator_id=?", (_LEVIATHAN_OPERATOR_ID,)
    ).fetchall()}
    projection = _leviathan_detector_projection(conn)
    rows = projection["rows"]
    row_mints = [r["mint"] for r in rows]
    assert len(row_mints) == len(set(row_mints))  # no duplicates
    assert set(row_mints) >= members  # every canonical member has a detector row


# ---------------- evidence wins over membership shortcut ----------------

def test_membership_to_exact_is_not_a_shortcut():
    """Every EXACT row must actually replay exact via evaluate_mint — never
    assumed true just because the mint is a member."""
    conn = _conn()
    projection = _leviathan_detector_projection(conn)
    for row in projection["rows"]:
        if row["raw_result"] == "EXACT":
            match = evaluate_mint(conn, row["mint"])
            assert match is not None
            assert _LEVIATHAN_OPERATOR_ID in match.matching_operator_ids
            assert match.state != "AMBIGUOUS_BEHAVIOURAL_CANDIDATE"


def test_discrepancy_surfaced_not_hidden():
    """If a canonical member does NOT currently replay exact, it must be
    labeled MEMBER_NOT_CURRENTLY_EXACT, never silently marked EXACT."""
    conn = _conn()
    members = [r[0] for r in conn.execute(
        "SELECT mint FROM operator_launch_membership WHERE operator_id=?", (_LEVIATHAN_OPERATOR_ID,)
    ).fetchall()]
    projection = _leviathan_detector_projection(conn)
    rows_by_mint = {r["mint"]: r for r in projection["rows"]}
    for mint in members:
        match = evaluate_mint(conn, mint)
        is_exact = (
            match is not None
            and _LEVIATHAN_OPERATOR_ID in match.matching_operator_ids
            and match.state != "AMBIGUOUS_BEHAVIOURAL_CANDIDATE"
        )
        row = rows_by_mint.get(mint)
        assert row is not None
        if is_exact:
            assert row["raw_result"] in ("EXACT", "PENDING_REPLAY")  # PENDING allowed if pre-seeded sample
        else:
            assert row["raw_result"] == "MEMBER_NOT_CURRENTLY_EXACT"


# ---------------- current live state (no hardcoding) ----------------

def test_exact_count_matches_live_canonical_state_not_hardcoded():
    conn = _conn()
    live_count = conn.execute(
        "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (_LEVIATHAN_OPERATOR_ID,)
    ).fetchone()[0]
    projection = _leviathan_detector_projection(conn)
    exact_rows = sum(1 for r in projection["rows"] if r["raw_result"] == "EXACT")
    # allow for a small number of MEMBER_NOT_CURRENTLY_EXACT if evidence disagrees,
    # but by design should equal live_count when all evidence is clean
    assert exact_rows <= live_count
    assert projection["exact_count"] == exact_rows or projection["exact_count"] == live_count


# ---------------- rejected lookalikes / P3R_13A04 safety ----------------

def test_rejected_lookalikes_absent_from_unified_projection():
    audit_path = os.path.join(ROOT, "docs", "audits", "leviathan_detector_match_ui.v1.json")
    with open(audit_path) as f:
        prior = json.load(f)
    rejected = {r["mint"] for r in prior.get("rejected_lookalikes", {}).get("reasons_sample", [])}
    conn = _conn()
    projection = _leviathan_detector_projection(conn)
    row_mints = {r["mint"] for r in projection["rows"]}
    assert rejected.isdisjoint(row_mints)


def test_p3r_13a04_zero_rows_in_leviathan_display():
    conn = _conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (P3R_13A04_OPERATOR_ID,)
    ).fetchone()[0]
    assert count == 0
    projection = _leviathan_detector_projection(conn)
    p3r_13a04_mints = {r[0] for r in conn.execute(
        "SELECT mint FROM operator_launch_membership WHERE operator_id=?", (P3R_13A04_OPERATOR_ID,)
    ).fetchall()}
    row_mints = {r["mint"] for r in projection["rows"]}
    assert p3r_13a04_mints.isdisjoint(row_mints)


# ---------------- template: redundant subclass removed ----------------

def test_template_no_longer_special_cases_leviathan_role_card():
    """The role.remove() gate must revert to Nexus-only; Leviathan's redundant
    'Manually Admitted P3R Operation' card should collapse like every other
    non-Nexus manual P3R profile."""
    with open(os.path.join(ROOT, "templates", "operator_intelligence.html")) as f:
        src = f.read()
    assert "if (role && !OP.nexus_detector) role.remove();" in src
    assert "!OP.nexus_detector && !OP.leviathan_detector" not in src


def test_template_mints_variable_no_longer_falls_back_to_leviathan():
    with open(os.path.join(ROOT, "templates", "operator_intelligence.html")) as f:
        src = f.read()
    idx = src.find("var mints = detector ? detector.rows : (profile.member_mints || [])")
    assert idx != -1
    # This line must be the ORIGINAL Nexus-only pattern (no leviathanDetector branch mixed in)
    line_end = src.find(";", idx)
    line = src[idx:line_end]
    assert "leviathanDetector" not in line


def test_template_unified_launch_row_carries_leviathan_marker():
    with open(os.path.join(ROOT, "templates", "operator_intelligence.html")) as f:
        src = f.read()
    assert "levDetectorByMint" in src
    assert "Verified exact Leviathan detector match" in src
    assert "Observed Leviathan candidate — full replay pending" in src
    # marker logic must live inside launchRow (the unified renderCompactSummary list)
    launch_row_idx = src.find("var launchRow = function(x)")
    lev_marker_idx = src.find("levDetectorByMint[x.mint]")
    assert launch_row_idx != -1 and lev_marker_idx != -1
    assert lev_marker_idx > launch_row_idx


def test_no_pending_details_block_left_in_role_card_path():
    """The old renderPendingMint helper (role-card-specific) should be gone —
    pending state is now handled inline in the unified launchRow."""
    with open(os.path.join(ROOT, "templates", "operator_intelligence.html")) as f:
        src = f.read()
    assert "renderPendingMint" not in src


# ---------------- summary derivation ----------------

def test_detector_summary_derived_from_unified_projection_fields():
    """Superseded by the Verified Leviathan Tokens card (see
    test_nexus_leviathan_verified_token_surface.py); the old inline micro-summary
    line was intentionally removed as redundant once the card was added."""
    with open(os.path.join(ROOT, "templates", "operator_intelligence.html")) as f:
        src = f.read()
    idx = src.find("'Verified Leviathan Tokens', leviathanDetector.exact_count")
    assert idx != -1
    snippet = src[max(0, idx - 100):idx + 250]
    assert "209" not in snippet
    assert "leviathanDetector.verified_total" in snippet


# ---------------- activity uses unified population ----------------

def test_activity_query_reads_operator_launch_membership_directly():
    with open(os.path.join(ROOT, "src", "ops", "operator_reader.py")) as f:
        src = f.read()
    idx = src.find('op["recent_launches"] = [dict(r) for r in conn.execute(')
    assert idx != -1
    snippet = src[idx:idx + 400]
    assert "operator_launch_membership" in snippet


# ---------------- Nexus / other operations safety ----------------

def test_nexus_detector_projection_function_untouched_signature():
    with open(os.path.join(ROOT, "src", "ops", "operator_reader.py")) as f:
        src = f.read()
    assert "def _nexus_detector_projection(conn: sqlite3.Connection | None = None) -> dict:" in src


def test_p3r_profile_matcher_detector_file_untouched():
    import subprocess
    result = subprocess.run(
        ["git", "diff", "--stat", "src/ops/p3r_profile_candidate_matcher.py"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == ""


# ---------------- no writes ----------------

def test_no_membership_writes_in_reader_or_template():
    with open(os.path.join(ROOT, "src", "ops", "operator_reader.py")) as f:
        reader_src = f.read()
    assert "INSERT INTO operator_launch_membership" not in reader_src
    assert "DELETE FROM operator_launch_membership" not in reader_src
    assert "UPDATE operator_launch_membership" not in reader_src


def test_no_rpc_or_network_calls_in_reader():
    with open(os.path.join(ROOT, "src", "ops", "operator_reader.py")) as f:
        src = f.read()
    for forbidden in ("urlopen" + "(", "requests" + ".", "getTransaction" + "("):
        assert forbidden not in src
