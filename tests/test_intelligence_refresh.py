"""
Tests for intelligence_refresh.py — watchlist, approval, budget gating.
All tests use in-memory SQLite. Zero RPC calls made.
"""

import json
import sqlite3
import time
import unittest

from src.core.intelligence_refresh import (
    IntelligenceRefreshCandidateBuilder,
    _score_creator,
    _score_funder,
    _get_budget_used,
    _increment_budget,
    _today_utc,
    approve_candidate,
    approve_top,
    ignore_candidate,
    get_budget_status,
    IGNORED_COOLDOWN_HOURS,
    MAX_CREATOR_REFRESH_SCANS_PER_DAY,
    MAX_FUNDER_SCANS_PER_DAY,
    MAX_RPC_CALLS_PER_DAY,
)


# ── Schema helpers ────────────────────────────────────────────────────────────

def _make_db() -> str:
    """Create an in-memory DB path (shared cache so multiple connections work)."""
    import tempfile, os
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.close()
    return f.name


def _seed_schema(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS intelligence_refresh_candidates (
            target_type TEXT NOT NULL,
            target_address TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            reason_codes TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'watchlist',
            rpc_allowed INTEGER NOT NULL DEFAULT 0,
            last_local_refresh_at INTEGER,
            last_rpc_scan_at INTEGER,
            next_eligible_scan_at INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY (target_type, target_address)
        );

        CREATE TABLE IF NOT EXISTS intelligence_refresh_rpc_budget (
            budget_date TEXT NOT NULL,
            budget_key TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (budget_date, budget_key)
        );

        CREATE TABLE IF NOT EXISTS token_analysis (
            mint TEXT PRIMARY KEY,
            earliest_tx_creator TEXT,
            analyzed_at TEXT,
            migrated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS creator_funders (
            creator_address TEXT,
            funder_address TEXT,
            amount_sol REAL DEFAULT 0,
            is_cex INTEGER DEFAULT 0,
            PRIMARY KEY (creator_address, funder_address)
        );

        CREATE TABLE IF NOT EXISTS creator_self_funding (
            creator_address TEXT PRIMARY KEY,
            is_self_funding INTEGER DEFAULT 0,
            self_funding_percentage REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS network_membership (
            creator_address TEXT,
            network_name TEXT,
            PRIMARY KEY (creator_address, network_name)
        );

        CREATE TABLE IF NOT EXISTS second_hop_lite_queue (
            funder_address TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',
            scanned_at INTEGER,
            priority INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS funder_upstream_links (
            funder_address TEXT,
            upstream_address TEXT,
            last_seen_network_count INTEGER DEFAULT 0,
            is_excluded INTEGER DEFAULT 0,
            PRIMARY KEY (funder_address, upstream_address)
        );

        CREATE TABLE IF NOT EXISTS wallet_clusters (
            funder_wallet TEXT PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS analyzer_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analyzer_name TEXT,
            started_at REAL,
            status TEXT DEFAULT 'running',
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
    """)
    conn.close()


def _insert_creator(db_path, creator, n_funders=0, n_tokens=0, n_migrated=0,
                    self_funding=False, in_network=False):
    conn = sqlite3.connect(db_path)
    for i in range(n_tokens):
        migrated_at = str(int(time.time())) if i < n_migrated else None
        conn.execute(
            "INSERT OR IGNORE INTO token_analysis (mint, earliest_tx_creator, migrated_at) VALUES (?,?,?)",
            (f"mint_{creator[:8]}_{i}", creator, migrated_at)
        )
    for i in range(n_funders):
        conn.execute(
            "INSERT OR IGNORE INTO creator_funders (creator_address, funder_address) VALUES (?,?)",
            (creator, f"funder_{creator[:8]}_{i}")
        )
    if self_funding:
        conn.execute(
            "INSERT OR REPLACE INTO creator_self_funding (creator_address, is_self_funding) VALUES (?,1)",
            (creator,)
        )
    if in_network:
        conn.execute(
            "INSERT OR IGNORE INTO network_membership (creator_address, network_name) VALUES (?,?)",
            (creator, "TestNetwork")
        )
    conn.commit()
    conn.close()


# ── Scoring unit tests ────────────────────────────────────────────────────────

class TestCreatorScoring(unittest.TestCase):

    def test_self_funding_adds_60(self):
        priority, reasons = _score_creator(
            self_funding=True, funder_count=10, single_creator_ratio=0.5,
            last_scan_age_days=1, migrated_count=0, token_count=5, no_network=False
        )
        self.assertIn("self_funding", reasons)
        self.assertGreaterEqual(priority, 60)

    def test_funder_count_tiers(self):
        p500, _ = _score_creator(False, 500, 0, 1, 0, 1, False)
        p100, _ = _score_creator(False, 100, 0, 1, 0, 1, False)
        p50,  _ = _score_creator(False, 50,  0, 1, 0, 1, False)
        self.assertGreater(p500, p100)
        self.assertGreater(p100, p50)

    def test_stale_scan_adds_priority(self):
        p30, r30 = _score_creator(False, 50, 0, 30, 0, 1, False)
        p7,  r7  = _score_creator(False, 50, 0, 7,  0, 1, False)
        p1,  _   = _score_creator(False, 50, 0, 1,  0, 1, False)
        self.assertIn("stale_30d", r30)
        self.assertIn("stale_7d", r7)
        self.assertGreater(p30, p7)
        self.assertGreater(p7, p1)

    def test_migrated_tokens_add_priority(self):
        p_mig, r = _score_creator(False, 50, 0, 1, 5, 1, False)
        p_none, _ = _score_creator(False, 50, 0, 1, 0, 1, False)
        self.assertIn("has_migrated_tokens", r)
        self.assertGreater(p_mig, p_none)

    def test_no_network_adds_10(self):
        p_no, r = _score_creator(False, 50, 0, 1, 0, 1, True)
        p_yes, _ = _score_creator(False, 50, 0, 1, 0, 1, False)
        self.assertIn("no_network_membership", r)
        self.assertEqual(p_no - p_yes, 10)


class TestFunderScoring(unittest.TestCase):

    def test_multi_creator_tiers(self):
        p10, r10 = _score_funder(10, False, False)
        p5,  r5  = _score_funder(5,  False, False)
        p2,  r2  = _score_funder(2,  False, False)
        self.assertGreater(p10, p5)
        self.assertGreater(p5, p2)
        self.assertIn("multi_creator_10+", r10)

    def test_wallet_cluster_adds_20(self):
        p_wc, _ = _score_funder(2, True, False)
        p_no, _ = _score_funder(2, False, False)
        self.assertEqual(p_wc - p_no, 20)

    def test_stale_cache_adds_10(self):
        p_s, _ = _score_funder(2, False, True)
        p_f, _ = _score_funder(2, False, False)
        self.assertEqual(p_s - p_f, 10)


# ── Watchlist / approval tests ─────────────────────────────────────────────────

class TestWatchlistBehaviour(unittest.TestCase):

    def setUp(self):
        self.db_path = _make_db()
        _seed_schema(self.db_path)

    def _get_row(self, target_type, address):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM intelligence_refresh_candidates WHERE target_type=? AND target_address=?",
            (target_type, address)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def test_high_risk_creator_gets_watchlisted(self):
        """Stale creator with many funders must appear in watchlist."""
        _insert_creator(self.db_path, "CREATOR_A", n_funders=200, n_tokens=5, n_migrated=1)
        builder = IntelligenceRefreshCandidateBuilder(self.db_path)
        # Patch analyzed_at to be stale
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE token_analysis SET analyzed_at='2025-01-01T00:00:00' WHERE earliest_tx_creator='CREATOR_A'"
        )
        conn.commit()
        conn.close()

        result = builder.run()
        self.assertGreater(result["creators_added"], 0)
        row = self._get_row("creator", "CREATOR_A")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "watchlist")
        self.assertEqual(row["rpc_allowed"], 0)

    def test_watchlist_does_not_trigger_rpc(self):
        """Watchlisting a candidate must never set rpc_allowed=1."""
        _insert_creator(self.db_path, "CREATOR_B", n_funders=100, n_tokens=2)
        IntelligenceRefreshCandidateBuilder(self.db_path).run()
        row = self._get_row("creator", "CREATOR_B")
        if row:
            self.assertEqual(row["rpc_allowed"], 0)

    def test_approve_enables_scan_eligibility(self):
        """After approve, status='approved' and rpc_allowed=1."""
        _insert_creator(self.db_path, "CREATOR_C", n_funders=100, n_tokens=2)
        IntelligenceRefreshCandidateBuilder(self.db_path).run()

        result = approve_candidate(self.db_path, "creator", "CREATOR_C")
        self.assertTrue(result["ok"])

        row = self._get_row("creator", "CREATOR_C")
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["rpc_allowed"], 1)

    def test_ignored_target_not_readded_within_cooldown(self):
        """Ignored candidate must not be re-added during the cooldown window."""
        _insert_creator(self.db_path, "CREATOR_D", n_funders=100, n_tokens=2)
        IntelligenceRefreshCandidateBuilder(self.db_path).run()
        ignore_candidate(self.db_path, "creator", "CREATOR_D")

        # Run builder again immediately
        IntelligenceRefreshCandidateBuilder(self.db_path).run()
        row = self._get_row("creator", "CREATOR_D")
        self.assertEqual(row["status"], "ignored")

    def test_approve_top_respects_limit(self):
        """approve-top must not approve more than `limit` candidates."""
        for i in range(10):
            _insert_creator(self.db_path, f"CREATOR_TOP_{i}", n_funders=100 + i)
        IntelligenceRefreshCandidateBuilder(self.db_path).run()

        result = approve_top(self.db_path, "creator", limit=3, min_priority=0)
        self.assertLessEqual(len(result["approved"]), 3)

    def test_approved_row_not_overwritten_by_builder(self):
        """Builder must not touch already-approved rows."""
        _insert_creator(self.db_path, "CREATOR_E", n_funders=100, n_tokens=2)
        IntelligenceRefreshCandidateBuilder(self.db_path).run()
        approve_candidate(self.db_path, "creator", "CREATOR_E")

        IntelligenceRefreshCandidateBuilder(self.db_path).run()
        row = self._get_row("creator", "CREATOR_E")
        self.assertEqual(row["status"], "approved")


# ── Budget tests ───────────────────────────────────────────────────────────────

class TestDailyBudget(unittest.TestCase):

    def setUp(self):
        self.db_path = _make_db()
        _seed_schema(self.db_path)

    def test_budget_starts_at_zero(self):
        status = get_budget_status(self.db_path)
        self.assertEqual(status["rpc_calls_today"], 0)
        self.assertFalse(status["budget_exhausted"])

    def test_budget_increments(self):
        conn = sqlite3.connect(self.db_path)
        _increment_budget(conn, "rpc_calls", 500)
        conn.commit()
        status = get_budget_status(self.db_path)
        self.assertEqual(status["rpc_calls_today"], 500)
        self.assertFalse(status["budget_exhausted"])
        conn.close()

    def test_budget_exhausted_flag(self):
        conn = sqlite3.connect(self.db_path)
        _increment_budget(conn, "rpc_calls", MAX_RPC_CALLS_PER_DAY)
        conn.commit()
        conn.close()
        status = get_budget_status(self.db_path)
        self.assertTrue(status["budget_exhausted"])
        self.assertEqual(status["rpc_budget_remaining"], 0)

    def test_budget_remaining_calculation(self):
        conn = sqlite3.connect(self.db_path)
        _increment_budget(conn, "rpc_calls", 300)
        conn.commit()
        conn.close()
        status = get_budget_status(self.db_path)
        self.assertEqual(status["rpc_budget_remaining"], MAX_RPC_CALLS_PER_DAY - 300)


if __name__ == "__main__":
    unittest.main()
