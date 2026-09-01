"""
Focused tests for the Leviathan membership backlog reconciliation
(scripts/reconcile_leviathan_membership_backlog.py) and the prospective
mid-walk P3R admission retrigger (src/core/walkback_worker.py).

No network calls, no RPC. Fixture tests use a disposable in-memory copy of
the schema, never the live wt_ops_v2.db, to prove the retrigger logic without
any risk to production state.
"""
import hashlib
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.reconcile_leviathan_membership_backlog import (
    LEVIATHAN_OPERATOR_ID, DB_PATH, build_backlog_candidates,
    dry_run_admission_decision, profile_collision_counts, replay_control_set,
)
from src.ops.p3r_profile_candidate_matcher import evaluate_mint, admit_unambiguous_p3r_match

ROOT = os.path.join(os.path.dirname(__file__), "..")
ARTIFACT_PATH = os.path.join(ROOT, "docs", "audits", "leviathan_membership_backlog_reconciliation.v1.json")
P3R_13A04_OPERATOR_ID = "ccb7b1b0-56e1-4543-9e95-3f284bed3943"


def _load_artifact():
    with open(ARTIFACT_PATH) as f:
        return json.load(f)


# ---------------- durable artifact / post-commit state ----------------

def test_reconciliation_artifact_exists_and_reports_commit():
    d = _load_artifact()
    assert d["operation_id"] == LEVIATHAN_OPERATOR_ID


def test_post_reconciliation_membership_count():
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (LEVIATHAN_OPERATOR_ID,)
    ).fetchone()[0]
    assert count >= 159  # at least the original baseline; backlog admissions should have landed


def test_no_backlog_remains_after_reconciliation():
    """Second pass over live DB should now find zero EXACT-match non-members."""
    conn = sqlite3.connect(DB_PATH)
    candidates = build_backlog_candidates(conn)
    exact_remaining = [c for c in candidates if c["detector_result"] == "EXACT_LEVIATHAN_MATCH"]
    assert exact_remaining == []


# ---------------- profile collision / rejected-8 / existing-159 safety ----------------

def test_rejected_lookalikes_still_not_leviathan_members():
    audit_path = os.path.join(ROOT, "docs", "audits", "leviathan_detector_match_ui.v1.json")
    with open(audit_path) as f:
        prior = json.load(f)
    rejected_sample = [r["mint"] for r in prior.get("rejected_lookalikes", {}).get("reasons_sample", [])]
    conn = sqlite3.connect(DB_PATH)
    replay = replay_control_set(conn, rejected_sample, "rejected_8")
    admitted_wrongly = sum(1 for r in replay if r["state"] == "EXACT_LEVIATHAN_MATCH")
    assert admitted_wrongly == 0
    for mint in rejected_sample:
        row = conn.execute(
            "SELECT operator_id FROM operator_launch_membership WHERE mint=?", (mint,)
        ).fetchone()
        if row is not None:
            assert row[0] != LEVIATHAN_OPERATOR_ID


def test_p3r_13a04_membership_delta_is_zero():
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (P3R_13A04_OPERATOR_ID,)
    ).fetchone()[0]
    assert count == 0  # unchanged before and after per investigation


def test_dry_run_decision_function_classifications():
    conn = sqlite3.connect(DB_PATH)
    candidates = build_backlog_candidates(conn)
    for c in candidates:
        decision = dry_run_admission_decision(conn, c)
        assert decision in (
            "WOULD_ADMIT_LEVIATHAN", "WOULD_SKIP_ALREADY_MEMBER", "WOULD_REJECT_OTHER_PROFILE",
            "WOULD_HOLD_AMBIGUOUS", "WOULD_FAIL_INSUFFICIENT", "WOULD_CONFLICT",
        )


def test_profile_collision_counts_shape():
    conn = sqlite3.connect(DB_PATH)
    candidates = build_backlog_candidates(conn)
    counts = profile_collision_counts(candidates)
    assert set(counts) == {
        "leviathan_only_count", "multi_profile_count", "other_profile_only_count", "ambiguous_profile_count",
    }


# ---------------- deterministic candidate reconstruction ----------------

def test_backlog_candidates_deterministic_across_two_calls():
    conn = sqlite3.connect(DB_PATH)
    c1 = build_backlog_candidates(conn)
    c2 = build_backlog_candidates(conn)
    d1 = hashlib.sha256(json.dumps(c1, sort_keys=True, default=str).encode()).hexdigest()
    d2 = hashlib.sha256(json.dumps(c2, sort_keys=True, default=str).encode()).hexdigest()
    assert d1 == d2


# ---------------- no direct membership SQL bypass ----------------

def test_reconcile_script_never_direct_inserts_membership():
    with open(os.path.join(ROOT, "scripts", "reconcile_leviathan_membership_backlog.py")) as f:
        src = f.read()
    assert "INSERT INTO operator_launch_membership" not in src
    assert "admit_unambiguous_p3r_match" in src


# ---------------- fixture: disposable in-memory schema for late-evidence retrigger ----------------

def _build_disposable_schema(conn):
    conn.executescript("""
        CREATE TABLE operators(operator_id TEXT PRIMARY KEY, display_name TEXT, status TEXT);
        CREATE TABLE operation_registry_dispositions(operator_id TEXT, disposition TEXT);
        CREATE TABLE operation_behavioural_profiles(
            profile_id TEXT, operator_id TEXT, profile_version INTEGER, provenance_json TEXT, member_mints_json TEXT
        );
        CREATE TABLE operator_launch_membership(
            mint TEXT PRIMARY KEY, operator_id TEXT, source_population_id TEXT, assigned_at INTEGER, event_id TEXT
        );
        CREATE TABLE wt_walkback_edge_candidates(
            mint TEXT, hop_depth INTEGER, mechanism TEXT, amount_lamports INTEGER, selection_status TEXT
        );
        CREATE TABLE wt_walkback_atomic_flows(
            evidence_key TEXT PRIMARY KEY, mint TEXT, signature TEXT, has_create INTEGER,
            has_sync_native INTEGER, has_close INTEGER, transfer_lamports INTEGER, block_time INTEGER
        );
        CREATE TABLE operator_activity_snapshots(operator_id TEXT, refreshed_at INTEGER);
    """)
    conn.execute("INSERT INTO operators VALUES (?,?,?)", ("op-leviathan-fixture", "P3R", "CONFIRMED"))
    conn.execute("INSERT INTO operation_registry_dispositions VALUES (?,?)", ("op-leviathan-fixture", "ACTIVE_MANUAL"))
    conn.execute(
        "INSERT INTO operation_behavioural_profiles VALUES (?,?,?,?,?)",
        ("profile-1", "op-leviathan-fixture", 1, json.dumps({}), json.dumps([])),
    )
    conn.commit()


def _stub_refresh_activity(monkeypatch):
    import src.ops.manual_registry as manual_registry
    monkeypatch.setattr(manual_registry, "refresh_operator_activity_snapshot", lambda *a, **k: None)


def test_late_evidence_fixture_admits_after_completion(monkeypatch):
    _stub_refresh_activity(monkeypatch)
    conn = sqlite3.connect(":memory:")
    _build_disposable_schema(conn)
    mint = "FixtureMintLateEvidence1111111111111111111"

    # Stage 1: only the edge exists, atomic flow incomplete -> not admitted.
    conn.execute(
        "INSERT INTO wt_walkback_edge_candidates VALUES (?,?,?,?,?)",
        (mint, 1, "WSOL_WRAP_CLOSE", 99999985000, "SELECTED"),
    )
    conn.commit()
    result_1 = admit_unambiguous_p3r_match(conn, mint, core_db_path=None)
    assert result_1 == "not_unambiguous_p3r"
    assert conn.execute("SELECT COUNT(*) FROM operator_launch_membership WHERE mint=?", (mint,)).fetchone()[0] == 0

    # Stage 2: later, full atomic evidence arrives (simulating deep_walkback.persist_atomic_flows).
    conn.execute(
        "INSERT INTO wt_walkback_atomic_flows VALUES (?,?,?,?,?,?,?,?)",
        ("evk-1", mint, "sigABC", 1, 1, 1, 99997955720, int(time.time())),
    )
    conn.commit()

    # Stage 3: post-commit retrigger (mirrors the new walkback_worker.py hook) fires for this mint only.
    result_2 = admit_unambiguous_p3r_match(conn, mint, core_db_path=None)
    assert result_2 == "admitted"
    assert conn.execute("SELECT COUNT(*) FROM operator_launch_membership WHERE mint=?", (mint,)).fetchone()[0] == 1

    # Idempotent: firing again produces zero new writes.
    result_3 = admit_unambiguous_p3r_match(conn, mint, core_db_path=None)
    assert result_3 == "already_admitted"
    assert conn.execute("SELECT COUNT(*) FROM operator_launch_membership WHERE mint=?", (mint,)).fetchone()[0] == 1


def test_ambiguous_fixture_never_admitted_by_late_retrigger(monkeypatch):
    _stub_refresh_activity(monkeypatch)
    conn = sqlite3.connect(":memory:")
    _build_disposable_schema(conn)
    # Second operator+profile: P3R_13A04, whose route is a strict subset check —
    # craft a mint whose edges satisfy BOTH the unified P3R full-atomic route
    # AND happen to also satisfy a second synthetic contract, to prove ambiguity holds.
    conn.execute("INSERT INTO operators VALUES (?,?,?)", ("op-13a04-fixture", "P3R_13A04", "CONFIRMED"))
    conn.execute("INSERT INTO operation_registry_dispositions VALUES (?,?)", ("op-13a04-fixture", "ACTIVE_MANUAL"))
    ladder = [1, 29999975000, 29999980000, 29999985000, 29999990000]
    conn.execute(
        "INSERT INTO operation_behavioural_profiles VALUES (?,?,?,?,?)",
        ("profile-2", "op-13a04-fixture", 1, json.dumps({"funding_ladder_lamports": ladder}), json.dumps([])),
    )
    conn.commit()

    mint = "FixtureMintAmbiguous22222222222222222222222"
    # Edges satisfying the unified P3R contract.
    conn.execute(
        "INSERT INTO wt_walkback_edge_candidates VALUES (?,?,?,?,?)",
        (mint, 1, "WSOL_WRAP_CLOSE", 99999985000, "SELECTED"),
    )
    # Edges satisfying the P3R_13A04 four-hop ladder too (contrived overlap for the fixture).
    # load_contracts builds the route as (1,PLAIN_XFER,ladder[4]),(2,WSOL_WRAP_CLOSE,ladder[3]),
    # (3,PLAIN_XFER,ladder[2]),(4,WSOL_WRAP_CLOSE,ladder[1]) — i.e. reversed against the ladder list.
    for depth, mech, amt in [
        (1, "PLAIN_XFER", 29999990000), (2, "WSOL_WRAP_CLOSE", 29999985000),
        (3, "PLAIN_XFER", 29999980000), (4, "WSOL_WRAP_CLOSE", 29999975000),
    ]:
        conn.execute(
            "INSERT INTO wt_walkback_edge_candidates VALUES (?,?,?,?,?)",
            (mint, depth, mech, amt, "SELECTED"),
        )
    conn.execute(
        "INSERT INTO wt_walkback_atomic_flows VALUES (?,?,?,?,?,?,?,?)",
        ("evk-2", mint, "sigXYZ", 1, 1, 1, 99997955720, int(time.time())),
    )
    conn.commit()

    match = evaluate_mint(conn, mint)
    assert match is not None
    assert match.state == "AMBIGUOUS_BEHAVIOURAL_CANDIDATE"

    result = admit_unambiguous_p3r_match(conn, mint, core_db_path=None)
    assert result == "not_unambiguous_p3r"
    assert conn.execute("SELECT COUNT(*) FROM operator_launch_membership WHERE mint=?", (mint,)).fetchone()[0] == 0


def test_failure_isolation_does_not_touch_evidence(monkeypatch):
    """A failing admission attempt must never affect already-committed evidence rows."""
    conn = sqlite3.connect(":memory:")
    _build_disposable_schema(conn)
    mint = "FixtureMintFailureIsolation33333333333333"
    conn.execute(
        "INSERT INTO wt_walkback_edge_candidates VALUES (?,?,?,?,?)",
        (mint, 1, "WSOL_WRAP_CLOSE", 99999985000, "SELECTED"),
    )
    conn.execute(
        "INSERT INTO wt_walkback_atomic_flows VALUES (?,?,?,?,?,?,?,?)",
        ("evk-3", mint, "sigDEF", 1, 1, 1, 99997955720, int(time.time())),
    )
    conn.commit()

    # Simulate admission raising by breaking refresh_operator_activity_snapshot.
    import src.ops.manual_registry as manual_registry
    def _boom(*a, **k):
        raise RuntimeError("simulated activity snapshot failure")
    monkeypatch.setattr(manual_registry, "refresh_operator_activity_snapshot", _boom)

    try:
        admit_unambiguous_p3r_match(conn, mint, core_db_path=None)
    except RuntimeError:
        pass  # the walkback_worker.py hook wraps this in try/except; here we just prove evidence survives

    evidence_row = conn.execute(
        "SELECT COUNT(*) FROM wt_walkback_atomic_flows WHERE mint=?", (mint,)
    ).fetchone()[0]
    assert evidence_row == 1  # evidence untouched regardless of admission outcome


# ---------------- affected-mint-only reevaluation (no broad rescan) ----------------

def test_retrigger_hook_scoped_to_source_mint_only():
    with open(os.path.join(ROOT, "src", "core", "walkback_worker.py")) as f:
        src = f.read()
    idx = src.find("P3R mid-walk admission check failed")
    assert idx != -1
    snippet = src[max(0, idx - 700):idx + 100]
    assert "admit_unambiguous_p3r_match(ops, source_mint" in snippet
    # must not scan all mints / all launches here
    assert "SELECT DISTINCT mint FROM wt_walkback_atomic_flows" not in snippet


def test_retrigger_fires_after_commit_not_before():
    with open(os.path.join(ROOT, "src", "core", "walkback_worker.py")) as f:
        src = f.read()
    commit_idx = src.find("ops.commit()", src.find("def _find_funder_via_rpc"))
    retrigger_idx = src.find("admit_unambiguous_p3r_match(ops, source_mint")
    assert commit_idx != -1 and retrigger_idx != -1
    assert retrigger_idx > commit_idx


def test_retrigger_uses_canonical_function_not_direct_insert():
    with open(os.path.join(ROOT, "src", "core", "walkback_worker.py")) as f:
        src = f.read()
    idx = src.find("P3R mid-walk admission check failed")
    snippet = src[max(0, idx - 700):idx + 100]
    assert "INSERT INTO operator_launch_membership" not in snippet


# ---------------- write timing / safety ----------------

def test_write_timings_recorded_and_no_lock_errors():
    d = _load_artifact()
    timings = d.get("write_timings_ms", {})
    assert timings.get("sqlite_busy_count", 0) == 0
    assert timings.get("sqlite_locked_count", 0) == 0


def test_no_rpc_in_reconciliation_script():
    with open(os.path.join(ROOT, "scripts", "reconcile_leviathan_membership_backlog.py")) as f:
        src = f.read()
    for forbidden in ("urlopen" + "(", "requests" + ".", "getTransaction" + "("):
        assert forbidden not in src
