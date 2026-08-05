"""X76.5 -- Restore Live Treasury Candidate Detection.

Validates:
1. walkback_worker's new stuck-lease self-kill guard fires correctly and
   never fires on a healthy/absent lease.
2. watchtower_recovery_diagnostics's new candidate_generation metrics
   compute correctly against an isolated database (not the live one).
3. add_walkback_hop2_lead (the canonical, already-existing detection
   contract this milestone reconnects) remains idempotent -- repeated
   calls for the same evidence never create duplicate rows and never
   double-count deduplicated signatures.
"""
import os
import sqlite3
import time

import pytest


class TestStuckLeaseSelfKillGuard:
    def test_no_lease_does_not_kill(self, monkeypatch):
        from src.core import walkback_worker

        killed = {"called": False}
        monkeypatch.setattr(os, "_exit", lambda code: killed.__setitem__("called", True))
        walkback_worker._check_stuck_lease()
        assert killed["called"] is False

    def test_fresh_lease_does_not_kill(self, monkeypatch):
        from src.core import walkback_worker
        from src.core.database_write_service import _thread_write_lease

        killed = {"called": False}
        monkeypatch.setattr(os, "_exit", lambda code: killed.__setitem__("called", True))
        _thread_write_lease.owner = {
            "database": "tracked", "database_selector": "tracked:test",
            "command": "test", "transaction_id": "tx-1",
            "acquired_at": time.time(),
        }
        try:
            walkback_worker._check_stuck_lease()
            assert killed["called"] is False
        finally:
            del _thread_write_lease.owner

    def test_stuck_lease_past_threshold_kills(self, monkeypatch):
        from src.core import walkback_worker
        from src.core.database_write_service import _thread_write_lease

        killed = {"called": False}
        monkeypatch.setattr(os, "_exit", lambda code: killed.__setitem__("called", True))
        monkeypatch.setattr(walkback_worker, "MAX_LEASE_STUCK_SECONDS", 60)
        _thread_write_lease.owner = {
            "database": "tracked", "database_selector": "tracked:test",
            "command": "walkback_worker.py:469 in _ops_conn", "transaction_id": "tx-stuck",
            "acquired_at": time.time() - 3600,  # 1 hour ago, well past the 60s threshold
        }
        try:
            walkback_worker._check_stuck_lease()
            assert killed["called"] is True
        finally:
            del _thread_write_lease.owner

    def test_lease_just_under_threshold_does_not_kill(self, monkeypatch):
        from src.core import walkback_worker
        from src.core.database_write_service import _thread_write_lease

        killed = {"called": False}
        monkeypatch.setattr(os, "_exit", lambda code: killed.__setitem__("called", True))
        monkeypatch.setattr(walkback_worker, "MAX_LEASE_STUCK_SECONDS", 600)
        _thread_write_lease.owner = {
            "database": "tracked", "database_selector": "tracked:test",
            "command": "walkback_worker.py:469 in _ops_conn", "transaction_id": "tx-almost",
            "acquired_at": time.time() - 500,  # under the 600s threshold
        }
        try:
            walkback_worker._check_stuck_lease()
            assert killed["called"] is False
        finally:
            del _thread_write_lease.owner


@pytest.fixture()
def ops_db(tmp_path):
    """Isolated ops-schema database -- not the live 2.9GB database. Only the
    tables this module's queries touch."""
    path = str(tmp_path / "x76_5_ops.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE wt_treasury_review (
            treasury TEXT PRIMARY KEY, transfer_pct INTEGER, out_sol REAL,
            recipients INTEGER, micro_pings INTEGER, detected_via TEXT,
            status TEXT DEFAULT 'PENDING_REVIEW', reviewed_by TEXT,
            detected_at INTEGER, reviewed_at INTEGER,
            subprov_wallet TEXT, creator_wallet TEXT, token_mint TEXT,
            distinct_subprovs INTEGER, distinct_creators INTEGER,
            evidence_sigs TEXT, evidence_subprovs TEXT, evidence_creators TEXT,
            evidence_mints TEXT, has_walkback_evidence INTEGER,
            first_walkback_at INTEGER, last_walkback_at INTEGER
        );
        CREATE TABLE wt_confirmed_treasuries (
            treasury TEXT PRIMARY KEY, confirmed_at INTEGER
        );
        CREATE TABLE wt_discovered_subprovs (
            subprov TEXT PRIMARY KEY, treasury TEXT
        );
    """)
    conn.commit()
    conn.close()
    yield path


class TestCandidateGenerationMetrics:
    def test_empty_table_reports_zero_and_stalled(self, ops_db):
        from src.ops.watchtower_recovery_diagnostics import _candidate_generation_metrics

        conn = sqlite3.connect(ops_db)
        conn.row_factory = sqlite3.Row
        now = int(time.time())
        m = _candidate_generation_metrics(conn, now=now)
        conn.close()
        assert m["generated_last_hour"] == 0
        assert m["generated_last_day"] == 0
        assert m["pending_review"] == 0
        assert m["newest_candidate_at"] is None
        assert m["stalled"] is True

    def test_fresh_candidate_is_not_stalled(self, ops_db):
        from src.ops.watchtower_recovery_diagnostics import _candidate_generation_metrics

        now = int(time.time())
        conn = sqlite3.connect(ops_db)
        conn.execute(
            "INSERT INTO wt_treasury_review (treasury, detected_via, status, detected_at) "
            "VALUES (?,?,?,?)",
            ("TestTreasury1111111111111111111111111111", "walkback_hop2", "PENDING_REVIEW", now - 60),
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        m = _candidate_generation_metrics(conn, now=now)
        conn.close()
        assert m["generated_last_hour"] == 1
        assert m["generated_last_day"] == 1
        assert m["pending_review"] == 1
        assert m["stalled"] is False
        assert m["newest_candidate_age_secs"] == 60

    def test_only_old_candidates_reports_stalled(self, ops_db):
        from src.ops.watchtower_recovery_diagnostics import _candidate_generation_metrics

        now = int(time.time())
        conn = sqlite3.connect(ops_db)
        conn.execute(
            "INSERT INTO wt_treasury_review (treasury, detected_via, status, detected_at) "
            "VALUES (?,?,?,?)",
            ("TestTreasuryOld222222222222222222222222", "walkback_hop2", "PENDING_REVIEW", now - 100 * 86400),
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        m = _candidate_generation_metrics(conn, now=now)
        conn.close()
        assert m["generated_last_hour"] == 0
        assert m["generated_last_day"] == 0
        assert m["pending_review"] == 1
        assert m["stalled"] is True
        assert m["oldest_pending_age_secs"] == 100 * 86400


@pytest.fixture()
def treasury_bank_conn(tmp_path):
    """Isolated database for treasury_bank.add_walkback_hop2_lead idempotency
    tests -- exercises the REAL function, not a re-implementation."""
    path = str(tmp_path / "x76_5_treasury_bank.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    from src.core import treasury_bank
    treasury_bank.initialize_schema(conn)
    yield conn
    conn.close()


class TestWalkbackHop2LeadIdempotency:
    """Exercises the ALREADY-EXISTING (X76.5 reconnects, does not rewrite)
    canonical candidate-creation contract in treasury_bank.py directly."""

    def test_first_call_inserts(self, treasury_bank_conn):
        from src.core import treasury_bank

        disp = treasury_bank.add_walkback_hop2_lead(
            treasury_bank_conn,
            "UpstreamWallet11111111111111111111111111",
            subprov_wallet="SubprovWallet111111111111111111111111",
            creator_wallet="CreatorWallet111111111111111111111111",
            token_mint="Mint1111111111111111111111111111111111",
            funding_sig="Sig1111111111111111111111111111111111",
            funding_amount_sol=10.0,
            funding_mechanism="WRAP_CLOSE",
        )
        assert disp == "inserted"
        row = treasury_bank_conn.execute(
            "SELECT distinct_subprovs, distinct_creators, out_sol FROM wt_treasury_review WHERE treasury=?",
            ("UpstreamWallet11111111111111111111111111",),
        ).fetchone()
        assert row["distinct_subprovs"] == 1
        assert row["distinct_creators"] == 1
        assert row["out_sol"] == 10.0

    def test_repeated_same_signature_does_not_double_count(self, treasury_bank_conn):
        """Idempotency (Phase 4): re-running walkback for the same evidence
        must not inflate out_sol or create a duplicate row."""
        from src.core import treasury_bank

        upstream = "UpstreamWallet22222222222222222222222222"
        kwargs = dict(
            subprov_wallet="SubprovWallet222222222222222222222222",
            creator_wallet="CreatorWallet222222222222222222222222",
            token_mint="Mint2222222222222222222222222222222222",
            funding_sig="Sig2222222222222222222222222222222222",
            funding_amount_sol=25.0,
            funding_mechanism="WRAP_CLOSE",
        )
        first = treasury_bank.add_walkback_hop2_lead(treasury_bank_conn, upstream, **kwargs)
        second = treasury_bank.add_walkback_hop2_lead(treasury_bank_conn, upstream, **kwargs)
        third = treasury_bank.add_walkback_hop2_lead(treasury_bank_conn, upstream, **kwargs)

        assert first == "inserted"
        assert second == "updated"
        assert third == "updated"

        rows = treasury_bank_conn.execute(
            "SELECT COUNT(*) FROM wt_treasury_review WHERE treasury=?", (upstream,)
        ).fetchone()[0]
        assert rows == 1  # no duplicate rows

        out_sol = treasury_bank_conn.execute(
            "SELECT out_sol FROM wt_treasury_review WHERE treasury=?", (upstream,)
        ).fetchone()[0]
        assert out_sol == 25.0  # NOT 75.0 -- the same sig must only count once

    def test_new_distinct_signature_does_accumulate(self, treasury_bank_conn):
        """A genuinely NEW signature (different funding event) for the same
        upstream treasury SHOULD accumulate evidence -- idempotency means
        "no duplicate re-counting of the same evidence," not "never update.\""""
        from src.core import treasury_bank

        upstream = "UpstreamWallet33333333333333333333333333"
        treasury_bank.add_walkback_hop2_lead(
            treasury_bank_conn, upstream,
            subprov_wallet="SubprovA333333333333333333333333333333",
            creator_wallet="CreatorA333333333333333333333333333333",
            token_mint="MintA33333333333333333333333333333333333",
            funding_sig="SigA3333333333333333333333333333333333",
            funding_amount_sol=5.0,
        )
        treasury_bank.add_walkback_hop2_lead(
            treasury_bank_conn, upstream,
            subprov_wallet="SubprovB333333333333333333333333333333",
            creator_wallet="CreatorB333333333333333333333333333333",
            token_mint="MintB33333333333333333333333333333333333",
            funding_sig="SigB3333333333333333333333333333333333",
            funding_amount_sol=7.0,
        )
        row = treasury_bank_conn.execute(
            "SELECT distinct_subprovs, distinct_creators, out_sol FROM wt_treasury_review WHERE treasury=?",
            (upstream,),
        ).fetchone()
        assert row["distinct_subprovs"] == 2
        assert row["distinct_creators"] == 2
        assert row["out_sol"] == 12.0

    def test_confirmed_treasury_is_never_re_added_as_candidate(self, treasury_bank_conn):
        """Never after human review (spec Phase 2 constraint): a wallet
        already promoted to wt_confirmed_treasuries must never be
        re-surfaced as a review candidate."""
        from src.core import treasury_bank

        confirmed = "ConfirmedTreasury4444444444444444444444"
        treasury_bank_conn.execute(
            "INSERT INTO wt_confirmed_treasuries (treasury, confirmed_at) VALUES (?,?)",
            (confirmed, int(time.time())),
        )
        treasury_bank_conn.commit()

        disp = treasury_bank.add_walkback_hop2_lead(
            treasury_bank_conn, confirmed,
            subprov_wallet="Subprov4444444444444444444444444444444",
            creator_wallet="Creator4444444444444444444444444444444",
            funding_sig="Sig44444444444444444444444444444444444",
            funding_amount_sol=1.0,
        )
        assert disp == "skipped:confirmed_treasury"
        row = treasury_bank_conn.execute(
            "SELECT 1 FROM wt_treasury_review WHERE treasury=?", (confirmed,)
        ).fetchone()
        assert row is None

    def test_known_subprov_is_never_added_as_treasury_candidate(self, treasury_bank_conn):
        """A wallet with recorded wrap-close fan-out is a SUBPROV, not a
        treasury (the fingerprint discriminator this milestone's Phase 3
        contract must not bypass)."""
        from src.core import treasury_bank

        # wt_discovered_subprovs is owned by operation_scheduler.py, not
        # treasury_bank.py's own initialize_schema -- add_walkback_hop2_lead's
        # own check for it is wrapped in try/except (silently no-ops if the
        # table doesn't exist yet), so this test creates it explicitly to
        # exercise the real skip path.
        treasury_bank_conn.execute(
            "CREATE TABLE wt_discovered_subprovs (subprov TEXT PRIMARY KEY, treasury TEXT)"
        )
        known_subprov = "KnownSubprov5555555555555555555555555555"
        treasury_bank_conn.execute(
            "INSERT INTO wt_discovered_subprovs (subprov, treasury) VALUES (?,NULL)",
            (known_subprov,),
        )
        treasury_bank_conn.commit()

        disp = treasury_bank.add_walkback_hop2_lead(
            treasury_bank_conn, known_subprov,
            subprov_wallet="SubprovX5555555555555555555555555555555",
            creator_wallet="CreatorX5555555555555555555555555555555",
            funding_sig="SigX555555555555555555555555555555555555",
            funding_amount_sol=1.0,
        )
        assert disp == "skipped:known_subprov"

    def test_self_rooted_upstream_equal_to_creator_is_rejected(self, treasury_bank_conn):
        from src.core import treasury_bank

        wallet = "SelfRooted666666666666666666666666666666"
        disp = treasury_bank.add_walkback_hop2_lead(
            treasury_bank_conn, wallet,
            subprov_wallet="Subprov6666666666666666666666666666666",
            creator_wallet=wallet,  # same as upstream -- self-rooted noise
            funding_sig="Sig6666666666666666666666666666666666666",
            funding_amount_sol=1.0,
        )
        assert disp == "skipped:self_rooted"
