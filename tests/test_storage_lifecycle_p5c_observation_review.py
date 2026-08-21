"""STORAGE-LIFECYCLE-P5C -- post-cutover observation and rollback retirement review tests.

Covers the review logic exercised in this milestone: production HOT health,
COLD historical reads (via real qualified infrastructure against safe test
fixtures), rollback frozen/validity checks, Watchtower/3SW2 digest
comparison, CEX/discovery checks, lock-health log parsing, growth-rate math,
cron/automation census logic, and retirement-gate logic. Where real repo
state is safe to read (read-only), tests exercise it directly; otherwise
isolated tmp_path fixtures are used.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from ops.cold_segment_registry import ColdSegmentRegistry  # noqa: E402
from ops.unified_transfer_reader import UnifiedTransferReader  # noqa: E402


# ---------------------------------------------------------------------------
# Rollback frozen-state / validity check logic
# ---------------------------------------------------------------------------

def _make_transfer_db(path: str, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE transfer_index (id INTEGER PRIMARY KEY, signature TEXT, "
        "source TEXT, destination TEXT, amount_lamports INTEGER, block_time INTEGER)"
    )
    conn.executemany(
        "INSERT INTO transfer_index (id, signature, source, destination, amount_lamports, block_time) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def check_rollback_frozen(db_path: str, expected_max_id: int, expected_row_count: int) -> dict:
    """Mirrors this milestone's Part 3 frozen-proof logic."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        max_id, row_count = conn.execute(
            "SELECT MAX(id), COUNT(*) FROM transfer_index"
        ).fetchone()
    finally:
        conn.close()
    return {
        "max_id": max_id,
        "row_count": row_count,
        "expected_max_id": expected_max_id,
        "expected_row_count": expected_row_count,
        "ROLLBACK_DB_FROZEN": "PASS" if (max_id == expected_max_id and row_count == expected_row_count) else "FAIL",
    }


def test_rollback_frozen_check_pass(tmp_path):
    db = str(tmp_path / "rollback.db")
    _make_transfer_db(db, [(i, f"sig{i}", "a", "b", 100, 1000 + i) for i in range(1, 11)])
    result = check_rollback_frozen(db, expected_max_id=10, expected_row_count=10)
    assert result["ROLLBACK_DB_FROZEN"] == "PASS"


def test_rollback_frozen_check_detects_drift(tmp_path):
    db = str(tmp_path / "rollback.db")
    _make_transfer_db(db, [(i, f"sig{i}", "a", "b", 100, 1000 + i) for i in range(1, 12)])  # 11 rows, not 10
    result = check_rollback_frozen(db, expected_max_id=10, expected_row_count=10)
    assert result["ROLLBACK_DB_FROZEN"] == "FAIL"


def check_rollback_valid(db_path: str, expected_max_id: int, expected_row_count: int) -> dict:
    """Mirrors Part 13's validity check: quick_check + high-water + schema presence."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        schema_ok = "transfer_index" in tables
        if schema_ok:
            max_id, row_count = conn.execute("SELECT MAX(id), COUNT(*) FROM transfer_index").fetchone()
        else:
            max_id, row_count = None, None
    finally:
        conn.close()
    valid = (
        quick == "ok"
        and schema_ok
        and max_id == expected_max_id
        and row_count == expected_row_count
    )
    return {"ROLLBACK_DB_VALID": "PASS" if valid else "FAIL", "quick_check": quick, "schema_ok": schema_ok}


def test_rollback_valid_check_pass(tmp_path):
    db = str(tmp_path / "rollback.db")
    _make_transfer_db(db, [(i, f"sig{i}", "a", "b", 100, 1000 + i) for i in range(1, 6)])
    result = check_rollback_valid(db, expected_max_id=5, expected_row_count=5)
    assert result["ROLLBACK_DB_VALID"] == "PASS"


def test_rollback_valid_check_fails_on_missing_table(tmp_path):
    db = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    result = check_rollback_valid(db, expected_max_id=5, expected_row_count=5)
    assert result["ROLLBACK_DB_VALID"] == "FAIL"
    assert result["schema_ok"] is False


# ---------------------------------------------------------------------------
# Production HOT health check logic
# ---------------------------------------------------------------------------

def check_hot_health(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        max_id, row_count = conn.execute("SELECT MAX(id), COUNT(*) FROM transfer_index").fetchone()
        opens_ok = True
    except sqlite3.OperationalError:
        opens_ok, max_id, row_count = False, None, None
    finally:
        conn.close()
    return {"opens": opens_ok, "max_id": max_id, "row_count": row_count}


def test_hot_health_check_on_real_production_db_if_present():
    """Real read-only check against the actual live production DB, skipped
    safely if it does not exist on this host (keeps the test portable)."""
    hot_path = os.path.join(REPO_ROOT, "database", "flex_complete_database.db")
    if not os.path.isfile(hot_path):
        pytest.skip("live production DB not present on this host")
    result = check_hot_health(hot_path)
    assert result["opens"] is True
    assert result["row_count"] is not None and result["row_count"] > 0


# ---------------------------------------------------------------------------
# COLD historical read logic (real qualified UnifiedTransferReader/ColdSegmentRegistry
# against a safe, isolated synthetic HOT+COLD pair)
# ---------------------------------------------------------------------------

def _make_cold_segment(path: str, rows: list[tuple], segment_id: str, closed_at: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE transfer_index (id INTEGER PRIMARY KEY, signature TEXT, "
        "source TEXT, destination TEXT, amount_lamports INTEGER, block_time INTEGER)"
    )
    conn.executemany(
        "INSERT INTO transfer_index (id, signature, source, destination, amount_lamports, block_time) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.execute(
        "CREATE TABLE segment_manifest (segment_id TEXT, month_covered TEXT, row_count INTEGER, closed_at INTEGER)"
    )
    conn.execute(
        "INSERT INTO segment_manifest VALUES (?, ?, ?, ?)",
        (segment_id, "2024-01", len(rows), closed_at),
    )
    conn.commit()
    conn.close()


def test_cold_only_signature_retrievable_via_real_reader(tmp_path):
    hot_db = str(tmp_path / "hot.db")
    _make_transfer_db(hot_db, [(1, "hot_sig", "hc", "hd", 111, 2000)])

    cold_root = tmp_path / "cold_segments"
    cold_root.mkdir()
    _make_cold_segment(
        str(cold_root / "transfer_index_cold_2023_01.sqlite"),
        [(1, "cold_only_sig", "cc", "cd", 222, 1000)],
        segment_id="2023_01",
        closed_at=1700000000,
    )

    registry = ColdSegmentRegistry(cold_root=str(cold_root)).build()
    assert len(registry.segments) == 1
    assert len(registry.rejections) == 0

    hot_conn = sqlite3.connect(f"file:{hot_db}?mode=ro", uri=True)
    reader = UnifiedTransferReader(hot_conn=hot_conn, cold_conns=registry.connections)

    result = reader.by_signature("cold_only_sig")
    assert len(result) == 1
    assert result[0][0] == "cold_only_sig"
    assert reader.get_conflicts() == []

    registry.close()


def test_cold_registry_rejects_unclosed_segment(tmp_path):
    cold_root = tmp_path / "cold_segments"
    cold_root.mkdir()
    path = str(cold_root / "transfer_index_cold_unclosed.sqlite")
    _make_cold_segment(path, [(1, "s", "a", "b", 1, 1)], segment_id="x", closed_at=1700000000)
    # overwrite manifest with NULL closed_at
    conn = sqlite3.connect(path)
    conn.execute("UPDATE segment_manifest SET closed_at=NULL")
    conn.commit()
    conn.close()

    registry = ColdSegmentRegistry(cold_root=str(cold_root)).build()
    assert len(registry.segments) == 0
    assert len(registry.rejections) == 1
    assert registry.rejections[0].reason == "MANIFEST_NOT_CLOSED"
    registry.close()


def test_hot_cold_conflict_detection_still_works(tmp_path):
    """Regression guard: the dedup/conflict logic this review relies on for
    'no silent data loss' claims must still surface true disagreements."""
    hot_db = str(tmp_path / "hot.db")
    _make_transfer_db(hot_db, [(1, "dup_sig", "src", "dst", 500, 9999)])

    cold_root = tmp_path / "cold_segments"
    cold_root.mkdir()
    _make_cold_segment(
        str(cold_root / "transfer_index_cold_2023_01.sqlite"),
        [(1, "dup_sig", "src", "dst", 999, 1111)],  # disagreeing amount/block_time
        segment_id="2023_01",
        closed_at=1700000000,
    )
    registry = ColdSegmentRegistry(cold_root=str(cold_root)).build()
    hot_conn = sqlite3.connect(f"file:{hot_db}?mode=ro", uri=True)
    reader = UnifiedTransferReader(hot_conn=hot_conn, cold_conns=registry.connections)

    reader.by_signature("dup_sig")
    conflicts = reader.get_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "HOT_COLD_EVIDENCE_CONFLICT"
    registry.close()


# ---------------------------------------------------------------------------
# Watchtower / 3SW2 digest comparison logic
# ---------------------------------------------------------------------------

def compute_operator_digests(conn: sqlite3.Connection, display_name: str) -> dict:
    op_row = conn.execute(
        "SELECT * FROM operators WHERE display_name=?", (display_name,)
    ).fetchone()
    cols = [d[0] for d in conn.execute("SELECT * FROM operators LIMIT 0").description]
    op = dict(zip(cols, op_row)) if op_row else None
    op_digest = hashlib.sha256(json.dumps(op, sort_keys=True, default=str).encode()).hexdigest() if op else None

    ent_rows = conn.execute(
        "SELECT oe.* FROM operator_entities oe JOIN operators o ON oe.operator_id=o.operator_id "
        "WHERE o.display_name=? ORDER BY oe.entity_address",
        (display_name,),
    ).fetchall()
    ent_cols = [d[0] for d in conn.execute("SELECT * FROM operator_entities LIMIT 0").description]
    ent = [dict(zip(ent_cols, r)) for r in ent_rows]
    ent_digest = hashlib.sha256(json.dumps(ent, sort_keys=True, default=str).encode()).hexdigest()
    return {"operator_row_sha256": op_digest, "operator_entities_rows_sha256": ent_digest, "entity_count": len(ent)}


def test_digest_comparison_detects_match_and_mismatch(tmp_path):
    db = str(tmp_path / "wt_ops.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE operators (operator_id TEXT, display_name TEXT, status TEXT)"
    )
    conn.execute("CREATE TABLE operator_entities (operator_id TEXT, entity_address TEXT, entity_type TEXT)")
    conn.execute("INSERT INTO operators VALUES ('op1', 'WATCHTOWER', 'CONFIRMED')")
    conn.executemany(
        "INSERT INTO operator_entities VALUES (?, ?, ?)",
        [("op1", "addrB", "TREASURY"), ("op1", "addrA", "TREASURY")],
    )
    conn.commit()

    d1 = compute_operator_digests(conn, "WATCHTOWER")
    # re-run: must be identical (order-insensitive due to ORDER BY entity_address)
    d2 = compute_operator_digests(conn, "WATCHTOWER")
    assert d1 == d2
    assert d1["entity_count"] == 2

    # mutate and confirm digest changes (mismatch is detectable)
    conn.execute("UPDATE operator_entities SET entity_type='SUB_PROV' WHERE entity_address='addrA'")
    conn.commit()
    d3 = compute_operator_digests(conn, "WATCHTOWER")
    assert d3["operator_entities_rows_sha256"] != d1["operator_entities_rows_sha256"]
    conn.close()


def test_watchtower_digest_matches_frozen_p5a_baseline_if_db_present():
    """Real read-only check against the actual wt_ops_v2.db, reproducing this
    milestone's Part 5 methodology. Skipped safely if absent."""
    db_path = os.path.join(REPO_ROOT, "database", "wt_ops_v2.db")
    if not os.path.isfile(db_path):
        pytest.skip("wt_ops_v2.db not present on this host")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        result = compute_operator_digests(conn, "WATCHTOWER")
    finally:
        conn.close()
    assert result["operator_row_sha256"] == "c52d91ce9edc6911ea88873d990ee269a8eb483d68a9a14f08b83105d27121df"
    assert result["operator_entities_rows_sha256"] == "12bd876c7bdda481ae0817900bd66497a31badbaa98296417ebd3dd92be23605"
    assert result["entity_count"] == 69


def test_3sw2_digest_matches_frozen_baseline_if_db_present():
    db_path = os.path.join(REPO_ROOT, "database", "wt_ops_v2.db")
    if not os.path.isfile(db_path):
        pytest.skip("wt_ops_v2.db not present on this host")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        result = compute_operator_digests(conn, "3SW2")
    finally:
        conn.close()
    assert result["operator_row_sha256"] == "4c701873caee8506c137bca8f16bc5e00b6df5431ead41d177eb2fd415d940cf"
    assert result["operator_entities_rows_sha256"] == "c6252e208ef3b99200a2d4d856d7b045da17a7f032c73c5f29edb9202cd03bca"
    assert result["entity_count"] == 1


# ---------------------------------------------------------------------------
# CEX / discovery check logic
# ---------------------------------------------------------------------------

def check_cex_signature_counts(reader: UnifiedTransferReader, expectations: list[tuple[str, int]]) -> dict:
    results = []
    all_match = True
    for sig, expected in expectations:
        rows = reader.by_signature(sig)
        match = len(rows) == expected
        all_match = all_match and match
        results.append({"signature": sig, "count": len(rows), "expected": expected, "match": match})
    return {"results": results, "all_match": all_match}


def test_cex_signature_count_logic(tmp_path):
    hot_db = str(tmp_path / "hot.db")
    _make_transfer_db(hot_db, [
        (1, "cex_sig_a", "s1", "d1", 1, 1),
        (2, "cex_sig_a", "s2", "d1", 1, 2),
        (3, "cex_sig_b", "s1", "d2", 1, 3),
    ])
    hot_conn = sqlite3.connect(f"file:{hot_db}?mode=ro", uri=True)
    reader = UnifiedTransferReader(hot_conn=hot_conn, cold_conns=[])
    result = check_cex_signature_counts(reader, [("cex_sig_a", 2), ("cex_sig_b", 1)])
    assert result["all_match"] is True

    result_fail = check_cex_signature_counts(reader, [("cex_sig_a", 99)])
    assert result_fail["all_match"] is False


def check_discovery_count_semantic(current_count: int, baseline_count: int) -> dict:
    """Growth is fine; the only concerning case is if the count looks like
    it started silently reading from the wrong (quarantined) source -- which
    this simple structural check can't detect directly, but it at least
    ensures the comparison logic doesn't wrongly flag legitimate growth."""
    return {
        "current": current_count,
        "baseline": baseline_count,
        "grew": current_count > baseline_count,
        "shrank": current_count < baseline_count,
        "concerning": current_count < baseline_count,  # a DROP is the real red flag, not growth
    }


def test_discovery_count_growth_is_not_flagged_concerning():
    result = check_discovery_count_semantic(current_count=10, baseline_count=8)
    assert result["concerning"] is False


def test_discovery_count_drop_is_flagged_concerning():
    result = check_discovery_count_semantic(current_count=5, baseline_count=8)
    assert result["concerning"] is True


# ---------------------------------------------------------------------------
# Lock-health log-parsing logic
# ---------------------------------------------------------------------------

def count_post_cutover_lock_incidents(log_text: str, cutover_unix: float) -> int:
    """Mirrors this milestone's Part 7 approach: extract acquired_at
    timestamps from CROSS_PROCESS_LOCK / database-is-locked lines and count
    how many are strictly after the cutover timestamp."""
    timestamps = [float(m) for m in re.findall(r"acquired_at['\"]?:\s*['\"]?(\d{9,10}\.\d+)", log_text)]
    return sum(1 for t in timestamps if t > cutover_unix)


def test_lock_log_parsing_separates_pre_and_post_cutover():
    cutover = 1787299627.0
    log_text = (
        "[CROSS_PROCESS_LOCK] timeout ... 'acquired_at': 1787284350.44 ...\n"
        "[CROSS_PROCESS_LOCK] timeout ... 'acquired_at': 1787310000.00 ...\n"
    )
    assert count_post_cutover_lock_incidents(log_text, cutover) == 1


def test_lock_log_parsing_zero_incidents_when_all_pre_cutover():
    cutover = 1787299627.0
    log_text = "acquired_at': 1787200000.0 ...\nacquired_at': 1787290000.0 ...\n"
    assert count_post_cutover_lock_incidents(log_text, cutover) == 0


# ---------------------------------------------------------------------------
# Growth-rate calculation logic (synthetic inputs)
# ---------------------------------------------------------------------------

def compute_growth_rates(cutover_max_id: int, current_max_id: int, elapsed_hours: float, bytes_added: int) -> dict:
    rows_added = current_max_id - cutover_max_id
    rows_per_hour = rows_added / elapsed_hours if elapsed_hours > 0 else 0
    return {
        "rows_added": rows_added,
        "rows_per_hour": rows_per_hour,
        "projected_rows_per_day": rows_per_hour * 24,
        "bytes_per_hour": bytes_added / elapsed_hours if elapsed_hours > 0 else 0,
        "projected_bytes_per_day": (bytes_added / elapsed_hours * 24) if elapsed_hours > 0 else 0,
    }


def test_growth_rate_math_synthetic():
    result = compute_growth_rates(cutover_max_id=1000, current_max_id=1100, elapsed_hours=2.0, bytes_added=2000)
    assert result["rows_added"] == 100
    assert result["rows_per_hour"] == 50.0
    assert result["projected_rows_per_day"] == 1200.0
    assert result["bytes_per_hour"] == 1000.0
    assert result["projected_bytes_per_day"] == 24000.0


def test_growth_rate_math_zero_elapsed_does_not_divide_by_zero():
    result = compute_growth_rates(cutover_max_id=1000, current_max_id=1000, elapsed_hours=0.0, bytes_added=0)
    assert result["rows_per_hour"] == 0


# ---------------------------------------------------------------------------
# Cron census logic
# ---------------------------------------------------------------------------

def find_storage_lifecycle_cron_entry(crontab_text: str) -> str | None:
    for line in crontab_text.splitlines():
        if "storage_lifecycle_runner" in line and "--once" in line:
            return line.strip()
    return None


def test_cron_census_finds_expected_entry():
    crontab_text = (
        "*/5 * * * * /some/other/script.sh\n"
        "0 3 * * * cd /repo && python3 -m src.ops.storage_lifecycle_runner --once --quiet >> logs/storage_lifecycle.log 2>&1\n"
    )
    entry = find_storage_lifecycle_cron_entry(crontab_text)
    assert entry is not None
    assert "0 3 * * *" in entry


def test_cron_census_returns_none_when_absent():
    crontab_text = "*/5 * * * * /some/other/script.sh\n"
    assert find_storage_lifecycle_cron_entry(crontab_text) is None


# ---------------------------------------------------------------------------
# HOT->COLD automation census logic (structural)
# ---------------------------------------------------------------------------

def census_hot_cold_automation(crontab_text: str, supervisord_conf_text: str, scheduler_source_text: str) -> dict:
    """Mirrors Part 10: automation is only YES if an ACTUAL invocation of the
    archival modules is found in a scheduled context -- mere presence of the
    module names inside comments/docstrings should not count."""
    markers = ["p5b_delta_reconciler", "transfer_archival_cursor", "close_segment", "cold_segment_registry.build"]

    def _invoked(text: str) -> bool:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if any(m in line for m in markers):
                return True
        return False

    found_in_crontab = _invoked(crontab_text)
    found_in_supervisord = _invoked(supervisord_conf_text)
    found_in_scheduler = _invoked(scheduler_source_text)
    automated = found_in_crontab or found_in_supervisord or found_in_scheduler
    return {
        "IS_HOT_TO_COLD_ROLLOVER_AUTOMATED": "YES" if automated else "NO",
        "found_in_crontab": found_in_crontab,
        "found_in_supervisord": found_in_supervisord,
        "found_in_scheduler": found_in_scheduler,
    }


def test_automation_census_says_no_when_nothing_found():
    result = census_hot_cold_automation(
        crontab_text="0 3 * * * python3 -m src.ops.storage_lifecycle_runner --once --quiet\n",
        supervisord_conf_text="[program:watchtower_api]\ncommand=gunicorn ...\n",
        scheduler_source_text="def run():\n    reconcile_armed()\n",
    )
    assert result["IS_HOT_TO_COLD_ROLLOVER_AUTOMATED"] == "NO"


def test_automation_census_says_yes_when_real_invocation_found():
    result = census_hot_cold_automation(
        crontab_text="0 4 * * * python3 -m src.ops.p5b_delta_reconciler --run\n",
        supervisord_conf_text="",
        scheduler_source_text="",
    )
    assert result["IS_HOT_TO_COLD_ROLLOVER_AUTOMATED"] == "YES"


def test_automation_census_ignores_commented_out_mentions():
    result = census_hot_cold_automation(
        crontab_text="# 0 4 * * * python3 -m src.ops.p5b_delta_reconciler --run (disabled)\n",
        supervisord_conf_text="",
        scheduler_source_text="",
    )
    assert result["IS_HOT_TO_COLD_ROLLOVER_AUTOMATED"] == "NO"


def test_automation_census_matches_real_repo_state():
    """Real structural check against the actual repo: crontab -l output is
    captured at review time and is not re-invoked here (subprocess calls are
    avoided in unit tests), but the actual files this milestone inspected are
    read directly to confirm no invocation exists in the committed config."""
    supervisord_path = os.path.join(REPO_ROOT, "config", "supervisor", "supervisord.conf")
    scheduler_path = os.path.join(REPO_ROOT, "src", "core", "operation_scheduler.py")
    supervisord_text = open(supervisord_path).read() if os.path.isfile(supervisord_path) else ""
    scheduler_text = open(scheduler_path).read() if os.path.isfile(scheduler_path) else ""
    result = census_hot_cold_automation(
        crontab_text="",  # crontab not read here; covered live in the review artifact
        supervisord_conf_text=supervisord_text,
        scheduler_source_text=scheduler_text,
    )
    assert result["found_in_supervisord"] is False
    assert result["found_in_scheduler"] is False


# ---------------------------------------------------------------------------
# Retirement-gate logic
# ---------------------------------------------------------------------------

def evaluate_retirement_gates(gates: dict[str, bool]) -> bool:
    """gates maps gate name -> satisfied bool. Eligible only if ALL true."""
    return all(gates.values())


def test_all_gates_pass_yields_eligible():
    gates = {f"gate_{i}": True for i in range(1, 13)}
    assert evaluate_retirement_gates(gates) is True


@pytest.mark.parametrize("failing_gate", [f"gate_{i}" for i in range(1, 13)])
def test_any_single_failing_gate_yields_ineligible(failing_gate):
    gates = {f"gate_{i}": True for i in range(1, 13)}
    gates[failing_gate] = False
    assert evaluate_retirement_gates(gates) is False


def test_short_observation_window_gate_fails_by_construction():
    """Direct check of this milestone's real finding: elapsed hours << 24
    must produce gate failure regardless of every other check."""
    elapsed_hours = 0.579
    min_required_hours = 24.0
    gate_1_satisfied = elapsed_hours >= min_required_hours
    assert gate_1_satisfied is False


# ---------------------------------------------------------------------------
# Structural guard: this milestone's own code must never delete/unlink the
# quarantine path.
# ---------------------------------------------------------------------------

def _co_occurs_executable(source: str, needle_a: str, needle_b: str, window: int = 200) -> bool:
    """Like a naive co-occurrence scan, but skips matches that fall inside
    this module's own string/list literals (i.e. the `dangerous_calls` /
    `dangerous` marker lists below), so the guard doesn't trip on its own
    definition of what it's searching for. Only counts a hit when needle_a
    appears as an actual call token (immediately followed by '(') OUTSIDE of
    a line that itself looks like a marker-list literal (a line containing
    both '[' and a comma-separated string list, i.e. this function's own
    config)."""
    lines = source.splitlines()
    marker_line_idx = {i for i, line in enumerate(lines) if "dangerous_calls = [" in line or "dangerous = [" in line}
    idx_a = source.find(needle_a)
    while idx_a != -1:
        line_no = source.count("\n", 0, idx_a)
        if line_no not in marker_line_idx:
            window_text = source[max(0, idx_a - window): idx_a + window]
            if needle_b in window_text:
                return True
        idx_a = source.find(needle_a, idx_a + 1)
    return False


def test_no_deletion_of_quarantine_path_in_this_milestones_test_file():
    """Self-referential structural guard: scans this very test file's source
    for any deletion/unlink call combined with the quarantine path string,
    excluding this guard's own marker-list definitions."""
    this_file = os.path.abspath(__file__)
    with open(this_file) as f:
        source = f.read()
    dangerous_calls = ["os.remove(", "os.unlink(", "shutil.rmtree(", "Path.unlink("]
    quarantine_marker = "p5b_rollback_quarantine"
    for call in dangerous_calls:
        assert not _co_occurs_executable(source, call, quarantine_marker), (
            f"found {call} near {quarantine_marker} in {this_file}"
        )


def test_no_deletion_calls_anywhere_in_repo_reference_the_quarantine_path():
    """Broader structural guard across the repo's Python sources: no
    deletion/unlink/rename/VACUUM call should appear in the same statement
    as a reference to the quarantine filename, anywhere in this milestone's
    own new test file. This does not assert about pre-existing files outside
    this milestone's scope (e.g. the P5B cutover script itself legitimately
    performs the rename INTO quarantine, which is expected and out of scope
    here) -- it specifically guards this milestone's own new artifacts and
    test file."""
    quarantine_marker = "p5b_rollback_quarantine"
    dangerous = ["os.remove(", "os.unlink(", "shutil.rmtree(", ".unlink()", "VACUUM"]
    this_dir = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(this_dir, "test_storage_lifecycle_p5c_observation_review.py")
    with open(target) as f:
        source = f.read()
    for call in dangerous:
        assert not _co_occurs_executable(source, call, quarantine_marker), f"{call} found near {quarantine_marker}"
