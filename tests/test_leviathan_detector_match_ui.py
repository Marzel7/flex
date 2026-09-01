"""
Focused tests for the Leviathan-only detector-verification UI treatment
(docs/audits/leviathan_detector_match_ui.v1.json, scripts/generate_leviathan_detector_match_ui.py,
src/ops/operator_reader.py::_leviathan_detector_projection).

No network calls, no RPC, no writes to production tables.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.generate_leviathan_detector_match_ui import LEVIATHAN_OPERATOR_ID, DB_PATH
from src.ops.p3r_profile_candidate_matcher import evaluate_mint

ROOT = os.path.join(os.path.dirname(__file__), "..")
AUDIT_PATH = os.path.join(ROOT, "docs", "audits", "leviathan_detector_match_ui.v1.json")
NEXUS_OPERATOR_ID = "bd7d7479-1454-5d41-9f68-115550348f3e"


def _load_audit():
    with open(AUDIT_PATH) as f:
        return json.load(f)


# ---------------- identity ----------------

def test_leviathan_identity_resolved_correctly():
    d = _load_audit()
    assert d["identity"]["operator_id"] == LEVIATHAN_OPERATOR_ID
    assert d["identity"]["ui_alias"] == "Leviathan"
    assert d["identity"]["persisted_display_name"] == "P3R"


def test_leviathan_detector_is_not_nexus_detector():
    d = _load_audit()
    assert d["identity"]["detector_id"] != "DIRECT_10K_CREATOR_PROVISIONING"
    assert "P3R" in d["identity"]["detector_id"]


def test_leviathan_only_scope_flag():
    d = _load_audit()
    assert d["leviathan_only_scope"] is True


# ---------------- detector-result source, not membership-as-verification ----------------

def test_authoritative_result_source_is_replay_not_membership():
    d = _load_audit()
    src = d["authoritative_result_source"]
    assert "replay" in src.lower()
    assert "not membership" in src.lower() or "not membership-as-verification" in src.lower()


def test_verified_exact_mapping_matches_live_replay():
    """Recompute exact count independently via evaluate_mint and compare to the artifact."""
    conn = sqlite3.connect(DB_PATH)
    members = [r[0] for r in conn.execute(
        "SELECT mint FROM operator_launch_membership WHERE operator_id=?", (LEVIATHAN_OPERATOR_ID,)
    ).fetchall()]
    exact = 0
    for mint in members:
        match = evaluate_mint(conn, mint)
        if match and LEVIATHAN_OPERATOR_ID in match.matching_operator_ids and match.state != "AMBIGUOUS_BEHAVIOURAL_CANDIDATE":
            exact += 1
    d = _load_audit()
    assert exact == d["historical_population"]["exact_count"]
    assert len(members) == d["historical_population"]["historical_member_count"]


def test_pending_mapping_excludes_current_members():
    conn = sqlite3.connect(DB_PATH)
    members = {r[0] for r in conn.execute(
        "SELECT mint FROM operator_launch_membership WHERE operator_id=?", (LEVIATHAN_OPERATOR_ID,)
    ).fetchall()}
    d = _load_audit()
    pending = d["current_live_observations"]["pending_mints_sample"]
    assert members.isdisjoint(set(pending))


def test_rejected_lookalike_exclusion_from_leviathan_surface():
    """Rejected lookalikes must never appear in membership or pending sets."""
    conn = sqlite3.connect(DB_PATH)
    members = {r[0] for r in conn.execute(
        "SELECT mint FROM operator_launch_membership WHERE operator_id=?", (LEVIATHAN_OPERATOR_ID,)
    ).fetchall()}
    d = _load_audit()
    pending = set(d["current_live_observations"]["pending_mints_sample"])
    rejected_sample = {r["mint"] for r in d["rejected_lookalikes"]["reasons_sample"]}
    assert rejected_sample.isdisjoint(members)
    assert rejected_sample.isdisjoint(pending)
    for r in d["rejected_lookalikes"]["reasons_sample"]:
        match = evaluate_mint(conn, r["mint"])
        assert match is None or LEVIATHAN_OPERATOR_ID not in match.matching_operator_ids or match.state == "AMBIGUOUS_BEHAVIOURAL_CANDIDATE"


# ---------------- summary derivation ----------------

def test_summary_derived_not_hardcoded():
    """The displayed counts must trace to the historical_population/current_live_observations sections,
    not be independently hand-authored numbers."""
    d = _load_audit()
    hist = d["historical_population"]
    live = d["current_live_observations"]
    display = d["display_population"]
    assert display["display_verified_total"] == hist["exact_count"]
    assert display["display_pending_total"] == live["current_pending_replay"]


def test_display_population_excludes_rejected_lookalikes():
    d = _load_audit()
    assert d["display_population"]["excludes_rejected_lookalikes"] is True


# ---------------- UI semantics ----------------

def test_ui_semantics_markers_match_nexus_visual_language():
    d = _load_audit()
    ui = d["ui_semantics"]
    assert ui["verified_marker"] == "green ●"
    assert ui["pending_marker"] == "grey ·"
    assert ui["verified_tooltip"] == "Verified exact Leviathan detector match"
    assert ui["pending_tooltip"] == "Observed Leviathan candidate — full replay pending"
    assert ui["rejected_rows_displayed"] is False


# ---------------- template gating: Leviathan-only, Nexus/others unchanged ----------------

def test_template_gates_leviathan_detector_by_operator_id():
    with open(os.path.join(ROOT, "src", "ops", "operator_reader.py")) as f:
        src = f.read()
    assert f'operator_id == _LEVIATHAN_OPERATOR_ID' in src
    assert '_LEVIATHAN_OPERATOR_ID = "777211c3-211e-551b-9310-ff9301570627"' in src


def test_template_leviathan_marker_gated_by_leviathan_detector_variable():
    with open(os.path.join(ROOT, "templates", "operator_intelligence.html")) as f:
        src = f.read()
    assert "leviathanDetector" in src
    assert "Verified exact Leviathan detector match" in src
    assert "Observed Leviathan candidate — full replay pending" in src


def test_nexus_detector_projection_untouched():
    with open(os.path.join(ROOT, "src", "ops", "operator_reader.py")) as f:
        src = f.read()
    assert '_NEXUS_OPERATOR_ID = "bd7d7479-1454-5d41-9f68-115550348f3e"' in src
    assert "_nexus_detector_projection" in src


def test_no_hardcoded_leviathan_counts_in_template():
    """The template must reference leviathanDetector object fields, never literal 159/49 etc."""
    with open(os.path.join(ROOT, "templates", "operator_intelligence.html")) as f:
        src = f.read()
    idx = src.find("leviathanDetector.exact_count")
    assert idx != -1
    # ensure no literal historical counts hardcoded near the Leviathan block
    snippet = src[max(0, idx - 200):idx + 200]
    assert "159" not in snippet
    assert "49" not in snippet


# ---------------- safety ----------------

def test_no_detector_or_membership_mutation():
    d = _load_audit()
    safety = d["safety"]
    for key in ("detector_changes", "historical_membership_writes", "prospective_membership_writes",
                "dispatch_changes", "source_writes", "nexus_ui_delta", "other_operations_ui_delta"):
        assert safety[key] == 0, key


def test_rpc_requirement_zero():
    d = _load_audit()
    rpc = d["rpc_requirement"]
    assert rpc["rpc_calls_required"] == 0
    assert rpc["known_signature_rpc_count"] == 0
    assert rpc["signature_discovery_required_count"] == 0


def test_p3r_matcher_module_not_modified_by_this_task():
    """The detector implementation itself must not have been touched."""
    import subprocess
    result = subprocess.run(
        ["git", "diff", "--stat", "src/ops/p3r_profile_candidate_matcher.py"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == ""


def test_historical_replay_counts_consistent():
    d = _load_audit()
    hist = d["historical_population"]
    assert hist["exact_count"] + hist["no_match_count"] + hist["incomplete_count"] + hist["ambiguous_count"] == hist["total_leviathan_rows"]


def test_artifact_sha256_readable():
    import hashlib
    with open(AUDIT_PATH, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    assert len(digest) == 64
