"""STORAGE-LIFECYCLE-P5B-R1 (resumed): writer-classification and zero-writer
multi-signal-check logic tests. All tests use isolated tmp_path fixtures and
in-memory/temp sqlite databases. Never touches real production databases,
never calls supervisorctl, never starts/stops a real process.

Covers the two pieces of discrete logic exercised by this R1 milestone:
1. Writer classification -- the terminal-state taxonomy (ACTIVE_WRITER,
   CONDITIONALLY_ARMABLE_WRITER, MANUAL_ONLY_NOT_RUNNING, DEAD_CODE_NOT_WIRED,
   NOT_A_WRITER, UNKNOWN) and the rule that a census is only complete when
   zero candidates remain UNKNOWN.
2. The zero-writer multi-signal check -- reimplemented here as small pure
   functions matching the live session's method (max-id stability polling,
   lsof write-handle detection, process-tree PPID verification for the
   Gunicorn master/worker pooling reasoning), so the core logic is
   unit-testable without touching any real database or process.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. Writer classification taxonomy
# ---------------------------------------------------------------------------

VALID_CLASSIFICATIONS = {
    "ACTIVE_WRITER",
    "CONDITIONALLY_ARMABLE_WRITER",
    "MANUAL_ONLY_NOT_RUNNING",
    "DEAD_CODE_NOT_WIRED",
    "NOT_A_WRITER",
    "UNKNOWN",
}


def classify_candidate(*, has_write_statement: bool, has_live_runtime_owner: bool,
                        gated_off: bool = False, toggle_gated: bool = False) -> str:
    """Mirrors the classification decision tree applied by hand during the
    live R1 census: a candidate with a write statement to transfer_index is
    only a real writer if it has a proven live runtime owner (a running
    supervisord program, cron entry, or import chain reachable from one)."""
    if not has_write_statement:
        return "NOT_A_WRITER"
    if not has_live_runtime_owner:
        return "DEAD_CODE_NOT_WIRED"
    if gated_off:
        return "MANUAL_ONLY_NOT_RUNNING"
    if toggle_gated:
        return "CONDITIONALLY_ARMABLE_WRITER"
    return "ACTIVE_WRITER"


def census_is_complete(classifications: list[str]) -> bool:
    """The census gate: UNKNOWN_WRITER_COUNT must be exactly 0."""
    return all(c in VALID_CLASSIFICATIONS for c in classifications) and \
        classifications.count("UNKNOWN") == 0


class TestWriterClassification:
    def test_dead_code_no_runtime_owner(self):
        # e.g. src/ops/transfer_archival_cursor.py -- write statement present,
        # but no supervisord program/cron/import chain reaches it.
        assert classify_candidate(has_write_statement=True, has_live_runtime_owner=False) == \
            "DEAD_CODE_NOT_WIRED"

    def test_active_writer(self):
        # e.g. watchtower_listener -- write statement + live, unconditional owner.
        assert classify_candidate(has_write_statement=True, has_live_runtime_owner=True) == \
            "ACTIVE_WRITER"

    def test_conditionally_armable_writer(self):
        # e.g. watchtower_api -- live owner, but the write path is gated by a
        # live-flippable DB toggle (auto_extract_funders), so it must remain
        # inside the writer boundary regardless of current toggle state.
        assert classify_candidate(has_write_statement=True, has_live_runtime_owner=True,
                                   toggle_gated=True) == "CONDITIONALLY_ARMABLE_WRITER"

    def test_manual_only_not_running(self):
        # e.g. pumpfun_curve_listener's in-process creator-funding loop,
        # gated off via LISTENER_CREATOR_FUNDING_QUEUE_ENABLED=0.
        assert classify_candidate(has_write_statement=True, has_live_runtime_owner=True,
                                   gated_off=True) == "MANUAL_ONLY_NOT_RUNNING"

    def test_not_a_writer(self):
        assert classify_candidate(has_write_statement=False, has_live_runtime_owner=True) == \
            "NOT_A_WRITER"

    def test_census_complete_requires_zero_unknowns(self):
        assert census_is_complete(["ACTIVE_WRITER", "DEAD_CODE_NOT_WIRED"]) is True
        assert census_is_complete(["ACTIVE_WRITER", "UNKNOWN"]) is False

    def test_census_real_p5b_r1_result(self):
        # The actual terminal classification set reached in this session's
        # live census (docs/audits/storage_lifecycle_p5b_r1_complete_writer_census.json).
        result = [
            "ACTIVE_WRITER",              # watchtower_listener
            "ACTIVE_WRITER",              # creator_funding_worker
            "CONDITIONALLY_ARMABLE_WRITER",  # watchtower_api
            "DEAD_CODE_NOT_WIRED",        # transfer_archival_cursor.py
            "DEAD_CODE_NOT_WIRED",        # transfer_cold_store.py
            "DEAD_CODE_NOT_WIRED",        # storage_cleanup.py
            "MANUAL_ONLY_NOT_RUNNING",    # listener in-process loop (gated)
            "MANUAL_ONLY_NOT_RUNNING",    # run_creator_funding_queue_once.py
        ]
        assert census_is_complete(result) is True
        assert result.count("ACTIVE_WRITER") == 2
        assert result.count("CONDITIONALLY_ARMABLE_WRITER") == 1


# ---------------------------------------------------------------------------
# 2. Zero-writer multi-signal check -- pure-logic reimplementation
# ---------------------------------------------------------------------------

def max_id_is_stable(samples: list[int]) -> bool:
    """Signal 6 from the live session: MAX(id) polled repeatedly must not
    advance across the zero-writer window."""
    return len(samples) > 0 and len(set(samples)) == 1


def has_write_capable_handle(lsof_fd_modes: list[str]) -> bool:
    """Signal 3: any FD mode containing 'w' (write) or 'u' (read+write)
    indicates a live writer handle; pure-read 'r' handles are expected and
    harmless (e.g. intelligence_snapshot_scheduler, creator_resolution_worker)."""
    return any(("w" in mode or "u" in mode) for mode in lsof_fd_modes)


def gunicorn_worker_belongs_to_master(worker_ppid: int, tracked_master_pid: int) -> bool:
    """Signal used for the Part 2 process-tree-awareness reasoning: a
    Gunicorn worker is part of the SAME service-level quiesce unit as the
    supervisord-tracked master iff its PPID equals the tracked master pid.
    This is exactly how pid 36501/37966 were verified against master
    35289/37965 in the live session, and how the prior 'pid 34082 anomaly'
    would be re-checked by a future session (pid no longer exists, but the
    check itself is this function)."""
    return worker_ppid == tracked_master_pid


def zero_writer_proof(*, supervisor_all_stopped: bool, process_census_clean: bool,
                       write_handles: list[str], max_id_samples: list[int]) -> bool:
    return (
        supervisor_all_stopped
        and process_census_clean
        and not has_write_capable_handle(write_handles)
        and max_id_is_stable(max_id_samples)
    )


class TestZeroWriterMultiSignalCheck:
    def test_max_id_stable_across_flat_samples(self):
        assert max_id_is_stable([6726543] * 8) is True

    def test_max_id_not_stable_if_it_advances(self):
        assert max_id_is_stable([6726543, 6726543, 6726544]) is False

    def test_max_id_stable_requires_at_least_one_sample(self):
        assert max_id_is_stable([]) is False

    def test_read_only_handles_do_not_fail_the_check(self):
        # Mirrors the live session's lsof result: two 'r'-mode handles from
        # non-writer services present during a genuinely zero-writer window.
        assert has_write_capable_handle(["r", "r"]) is False

    def test_write_handle_detected(self):
        assert has_write_capable_handle(["r", "w"]) is True
        assert has_write_capable_handle(["u"]) is True

    def test_gunicorn_worker_ppid_matches_tracked_master(self):
        # Live session: worker pid 36501 had PPID 35289 (pre-test master);
        # worker pid 37966 had PPID 37965 (post-restart master).
        assert gunicorn_worker_belongs_to_master(worker_ppid=35289, tracked_master_pid=35289) is True
        assert gunicorn_worker_belongs_to_master(worker_ppid=37965, tracked_master_pid=37965) is True

    def test_gunicorn_worker_ppid_mismatch_would_flag_orphan(self):
        # This is the exact check that resolves the prior session's
        # "pid 34082" anomaly class: an orphan worker's PPID would NOT match
        # the currently tracked master.
        assert gunicorn_worker_belongs_to_master(99999, 35289) is False

    def test_full_zero_writer_proof_passes_on_live_session_values(self):
        assert zero_writer_proof(
            supervisor_all_stopped=True,
            process_census_clean=True,
            write_handles=["r", "r"],
            max_id_samples=[6726543] * 8,
        ) is True

    def test_full_zero_writer_proof_fails_if_any_signal_fails(self):
        assert zero_writer_proof(
            supervisor_all_stopped=True,
            process_census_clean=True,
            write_handles=["r", "w"],  # a write handle survived
            max_id_samples=[6726543] * 8,
        ) is False
        assert zero_writer_proof(
            supervisor_all_stopped=True,
            process_census_clean=True,
            write_handles=["r"],
            max_id_samples=[6726543, 6726544],  # id advanced -- unaccounted writer
        ) is False


# ---------------------------------------------------------------------------
# 3. creator_funding_queue checkpoint resume logic (DB-backed, tmp_path only)
# ---------------------------------------------------------------------------

@pytest.fixture()
def queue_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "queue_test.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE creator_funding_queue ("
        "id INTEGER PRIMARY KEY, creator_address TEXT, status TEXT)"
    )
    rows = [
        (1, "creatorA", "complete"),
        (2, "creatorB", "pending"),
        (3, "creatorC", "retry"),
        (4, "creatorD", "running"),
    ]
    conn.executemany(
        "INSERT INTO creator_funding_queue (id, creator_address, status) VALUES (?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def select_resumable_rows(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT id, status FROM creator_funding_queue WHERE status IN ('pending','retry')"
        ).fetchall()
    finally:
        conn.close()


class TestCreatorFundingWorkerCheckpointResume:
    def test_resume_selects_only_pending_and_retry(self, queue_db: Path):
        rows = select_resumable_rows(queue_db)
        statuses = {status for _id, status in rows}
        assert statuses == {"pending", "retry"}
        assert len(rows) == 2

    def test_a_stop_start_cycle_does_not_lose_or_duplicate_terminal_rows(self, queue_db: Path):
        # Simulates: worker stops (no-op on DB), worker restarts, re-queries.
        before = select_resumable_rows(queue_db)
        # "stop" -- nothing touches the DB.
        # "start" -- re-query.
        after = select_resumable_rows(queue_db)
        assert before == after  # idempotent across a stop/start with no processing in between

        conn = sqlite3.connect(queue_db)
        complete_count = conn.execute(
            "SELECT COUNT(*) FROM creator_funding_queue WHERE status='complete'"
        ).fetchone()[0]
        conn.close()
        assert complete_count == 1  # unaffected by the stop/start cycle
