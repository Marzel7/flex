import sqlite3

import pytest

import src.ops.watchtower_historical_backfill as backfill
from src.ops.watchtower_historical_backfill import (admit, begin_next_range, commit_to_existing_watchtower_terminal,
    complete_after_terminal_history, complete_range, job_state, mark_acquisition_complete, policy_c_observations, ranges)
from src.ops.strict_migration_window import MIGRATION_SECOND_FALLBACK_METHOD, PLUS1_ENTRY_METHOD
from src.ops.operator_lifecycle_projection import ensure_schema as ensure_history_schema


def _result(method):
    return {"mint": "fixture", "entry_timestamp": 1, "entry_mc_usd": 100.0,
            "entry_method": method, "entry_exactness": method,
            "candles": [{"timestamp": 900, "open": 100, "high": 300, "low": 100, "close": 250}, {"timestamp": 1800, "open": 250, "high": 280, "low": 30, "close": 30}]}


def test_terminal_adapter_preserves_fallback_and_is_idempotent(tmp_path):
    db = str(tmp_path / "terminal.sqlite")
    first = commit_to_existing_watchtower_terminal(db, _result(MIGRATION_SECOND_FALLBACK_METHOD), now=2000)
    second = commit_to_existing_watchtower_terminal(db, _result(MIGRATION_SECOND_FALLBACK_METHOD), now=2001)
    assert first["provider_calls"] == second["provider_calls"] == 0
    with sqlite3.connect(db) as conn:
        fact = conn.execute("SELECT entry_method,entry_exactness,final_proven_ath_mc,final_ath_multiple FROM operation_monitor_facts").fetchone()
        assert fact[:2] == (MIGRATION_SECOND_FALLBACK_METHOD, MIGRATION_SECOND_FALLBACK_METHOD)
        assert fact[2:] == (300.0, 3.0)
        assert conn.execute("SELECT count(*) FROM operation_monitor_observations").fetchone()[0] == 2


def test_terminal_adapter_keeps_plus_one_distinct(tmp_path):
    db = str(tmp_path / "plus1.sqlite")
    commit_to_existing_watchtower_terminal(db, _result(PLUS1_ENTRY_METHOD), now=2000)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT entry_method,entry_exactness FROM operation_monitor_facts").fetchone() == (PLUS1_ENTRY_METHOD, PLUS1_ENTRY_METHOD)


def test_job_closes_acquisition_before_provider_free_terminal_completion(tmp_path):
    db = str(tmp_path / "job.sqlite")
    boundary = {"mint": "fixture", "assignment_id": "a", "migration_signature": "s", "migration_slot": 1, "migration_timestamp": 1, "pumpswap_pool": "p"}
    job = admit(db, boundary, now=1)["job_id"]
    for ordinal in range(6):
        row = begin_next_range(db, job, now=ordinal + 2)
        complete_range(db, row["range_id"], {"ordinal": ordinal}, now=ordinal + 3)
    assert job_state(db, job) == "ACQUIRING"
    first = complete_after_terminal_history(db, job, _result(MIGRATION_SECOND_FALLBACK_METHOD), now=20)
    assert first["provider_calls"] == 0 and job_state(db, job) == "COMPLETED"
    assert complete_after_terminal_history(db, job, _result(MIGRATION_SECOND_FALLBACK_METHOD), now=21)["provider_calls"] == 0


def _completed_job(db, mint="fixture"):
    boundary = {"mint": mint, "assignment_id": "a", "migration_signature": "s", "migration_slot": 1, "migration_timestamp": 1, "pumpswap_pool": "p"}
    job = admit(db, boundary, now=1)["job_id"]
    for ordinal in range(6):
        row = begin_next_range(db, job, now=ordinal + 2)
        complete_range(db, row["range_id"], {"ordinal": ordinal}, now=ordinal + 3)
    return job


def test_crash_boundaries_never_reopen_completed_range_acquisition(tmp_path, monkeypatch):
    db = str(tmp_path / "crash.sqlite"); job = _completed_job(db)
    assert mark_acquisition_complete(db, job, now=10) == "TERMINAL_COMMIT_PENDING"
    assert job_state(db, job) == "TERMINAL_COMMIT_PENDING"  # crash before terminal call
    with pytest.raises(RuntimeError):
        monkeypatch.setattr(backfill, "commit_to_existing_watchtower_terminal", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("injected")))
        complete_after_terminal_history(db, job, _result(MIGRATION_SECOND_FALLBACK_METHOD), now=11)
    assert job_state(db, job) == "TERMINAL_COMMIT_PENDING"
    assert all(row["state"] == "COMPLETED" for row in ranges(db, job))
    monkeypatch.undo()
    commit_to_existing_watchtower_terminal(db, _result(MIGRATION_SECOND_FALLBACK_METHOD), now=12)  # crash after durable History
    assert complete_after_terminal_history(db, job, _result(MIGRATION_SECOND_FALLBACK_METHOD), now=13)["job_state"] == "COMPLETED"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM operation_monitor_facts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM operation_monitor_observations").fetchone()[0] == 2


def test_cook_provider_free_history_fixture_preserves_fallback_provenance(tmp_path):
    db = str(tmp_path / "cook.sqlite")
    mint = "HRinFbhZjrb2xoqSzxYCYKX7LqJ3H6pn42CURb2cpump"; entry = 1791269958; mc = 113464.45625213276
    job = _completed_job(db, mint)
    candles = [{"timestamp": entry + offset, "open": mc, "high": mc * (3 if offset == 1800 else 2), "low": mc * (.1 if offset == 86400 else 1), "close": mc * (.1 if offset == 86400 else 2)} for offset in range(900, 86401, 900)]
    assert len(policy_c_observations(candles, entry_timestamp=entry)) == 28
    result = {"mint": mint, "entry_timestamp": entry, "entry_mc_usd": mc, "entry_method": MIGRATION_SECOND_FALLBACK_METHOD, "entry_exactness": MIGRATION_SECOND_FALLBACK_METHOD, "candles": candles}
    out = complete_after_terminal_history(db, job, result, now=99)
    assert out["provider_calls"] == 0 and out["job_state"] == "COMPLETED"
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT entry_method,entry_exactness,final_proven_ath_mc,final_ath_multiple,drawdown_percent FROM operation_monitor_facts WHERE mint=?", (mint,)).fetchone()
        assert row[0:2] == (MIGRATION_SECOND_FALLBACK_METHOD, MIGRATION_SECOND_FALLBACK_METHOD)
        assert row[2] and row[3] and row[4] >= 85


def test_sparse_history_promotes_only_compatible_waiting_fact(tmp_path):
    db = str(tmp_path / "sparse.sqlite"); mint = "fixture"; job = _completed_job(db, mint)
    with sqlite3.connect(db) as conn:
        ensure_history_schema(conn)
        conn.execute("""INSERT INTO operation_monitor_facts(operation_id,mint,cohort_class,assignment_timestamp,assignment_provenance,entry_method,entry_status,entry_exactness,monitor_state,provider_call_count,candles_retained,provenance_digest,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("watchtower", mint, "PROSPECTIVE_MONITOR_COHORT", 2, "assignment-proof", "FIRST_FULL_POST_MIGRATION_SECOND_MC", "WAITING_FOR_ENTRY_REFERENCE", "UNQUALIFIED", "WAITING_FOR_ENTRY_REFERENCE", 0, 0, "waiting", 2, 2))
    candles = [{"timestamp": 900, "open": 100, "high": 300, "low": 100, "close": 250}, {"timestamp": 1800, "open": 250, "high": 280, "low": 30, "close": 30}]
    result = {"mint": mint, "entry_timestamp": 1, "entry_mc_usd": 100.0, "entry_method": MIGRATION_SECOND_FALLBACK_METHOD, "entry_exactness": MIGRATION_SECOND_FALLBACK_METHOD, "candles": candles}
    out = complete_after_terminal_history(db, job, result, now=20)
    assert out["provider_calls"] == 0 and out["coverage_class"] == "SPARSE" and job_state(db, job) == "COMPLETED"
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT assignment_timestamp,assignment_provenance,entry_status,monitor_state,final_ath_evidence,final_ath_resolution,evidence_status FROM operation_monitor_facts").fetchone()
        assert row == (2, "assignment-proof", "QUALIFIED", "PRICE_MONITOR_COMPLETE_COLLAPSED", "HISTORICAL_SPARSE_OBSERVED_15M_HIGH", "15m:SPARSE:OBSERVED_ONLY", "HISTORICAL_SPARSE_OBSERVED_ONLY")
        assert conn.execute("SELECT count(*) FROM operation_monitor_observations").fetchone()[0] == 2
