import sqlite3

from src.core import walkback_worker


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE wt_provisioning_sessions (source_mint TEXT PRIMARY KEY, treasury TEXT);"
        "CREATE TABLE wt_treasury_review (treasury TEXT PRIMARY KEY, distinct_subprovs INTEGER, "
        "distinct_creators INTEGER, detected_via TEXT, status TEXT);"
        "CREATE TABLE candidate_writes (value TEXT);"
        "INSERT INTO wt_provisioning_sessions VALUES ('M','T');"
        "INSERT INTO wt_treasury_review VALUES ('T',5,5,'walkback_hop2','PENDING_REVIEW');"
    )
    conn.commit()
    return conn


def test_runtime_topology_hook_runs_after_committed_boundary(monkeypatch):
    conn = _db()
    observed = []

    def fake_surface(candidate_conn, wallet, **_kwargs):
        observed.append((candidate_conn.in_transaction, wallet))
        return {"action": "not_qualified", "wallet": wallet}

    monkeypatch.setattr(
        "src.ops.treasury_topology_classifier.surface_runtime_topology_candidate",
        fake_surface,
    )
    result = walkback_worker._detect_topology_treasury_candidate_after_commit(conn, "M")
    assert result["action"] == "not_qualified"
    assert observed == [(False, "T")]


def test_runtime_topology_hook_failure_isolated_and_rolled_back(monkeypatch):
    conn = _db()

    def fail_after_write(candidate_conn, _wallet, **_kwargs):
        candidate_conn.execute("INSERT INTO candidate_writes VALUES ('partial')")
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.ops.treasury_topology_classifier.surface_runtime_topology_candidate",
        fail_after_write,
    )
    result = walkback_worker._detect_topology_treasury_candidate_after_commit(conn, "M")
    assert result["action"] == "failed"
    assert conn.in_transaction is False
    assert conn.execute("SELECT COUNT(*) FROM candidate_writes").fetchone()[0] == 0


def test_runtime_topology_hook_no_root_is_noop():
    conn = _db()
    result = walkback_worker._detect_topology_treasury_candidate_after_commit(conn, "OTHER")
    assert result == {"action": "no_observed_root", "mint": "OTHER"}
    assert conn.in_transaction is False


def test_runtime_topology_hook_skips_small_and_already_surfaced_cohorts(monkeypatch):
    conn = _db()
    calls = []
    monkeypatch.setattr(
        "src.ops.treasury_topology_classifier.surface_runtime_topology_candidate",
        lambda *_args, **_kwargs: calls.append(True),
    )
    conn.execute("UPDATE wt_treasury_review SET distinct_subprovs=4")
    conn.commit()
    assert walkback_worker._detect_topology_treasury_candidate_after_commit(
        conn, "M"
    )["action"] == "insufficient_cohort"
    conn.execute(
        "UPDATE wt_treasury_review SET distinct_subprovs=5,"
        "detected_via='topology_cohort_qualified'"
    )
    conn.commit()
    assert walkback_worker._detect_topology_treasury_candidate_after_commit(
        conn, "M"
    )["action"] == "already_surfaced"
    assert calls == []


def test_runtime_hook_is_operation_neutral_and_cannot_confirm_or_replay(monkeypatch):
    conn = _db()
    forbidden = []

    def fail_forbidden(*_args, **_kwargs):
        forbidden.append(True)
        raise AssertionError("runtime topology hook crossed the review-only boundary")

    monkeypatch.setattr(
        "src.ops.treasury_topology_classifier.confirm_topology_candidate",
        fail_forbidden,
    )
    monkeypatch.setattr(
        "src.ops.treasury_topology_classifier.replay_topology_candidate",
        fail_forbidden,
    )

    def surface_review(candidate_conn, wallet, **_kwargs):
        candidate_conn.execute("INSERT INTO candidate_writes VALUES (?)", (wallet,))
        return {"action": "inserted", "wallet": wallet, "distinct_mints": 38}

    monkeypatch.setattr(
        "src.ops.treasury_topology_classifier.surface_runtime_topology_candidate",
        surface_review,
    )
    result = walkback_worker._detect_topology_treasury_candidate_after_commit(conn, "M")

    assert result == {"action": "inserted", "wallet": "T", "distinct_mints": 38}
    assert forbidden == []
    assert conn.in_transaction is False
    assert conn.execute("SELECT value FROM candidate_writes").fetchone()[0] == "T"
