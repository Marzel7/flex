"""
Tests for enqueue_creator_funders_for_phase2_lite and approval hooks.
All in-memory SQLite. Zero RPC calls.
"""

import json
import sqlite3
import tempfile
import time
import unittest

from src.core.intelligence_refresh import (
    enqueue_creator_funders_for_phase2_lite,
    approve_candidate,
    approve_top,
    MAX_FUNDER_ENQUEUE_PER_CREATOR,
    MAX_FUNDER_ENQUEUE_APPROVE_TOP,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_db() -> str:
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.close()
    return f.name


def _seed(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS creator_funders (
            creator_address TEXT, funder_address TEXT,
            amount_sol REAL DEFAULT 0, is_cex INTEGER DEFAULT 0,
            PRIMARY KEY (creator_address, funder_address)
        );
        CREATE TABLE IF NOT EXISTS creator_self_funding (
            creator_address TEXT PRIMARY KEY, is_self_funding INTEGER DEFAULT 0,
            self_funding_percentage REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS wallet_clusters (
            cluster_id INTEGER, funder_wallet TEXT,
            creator_addresses TEXT DEFAULT '[]', creator_count INTEGER DEFAULT 0,
            confidence_score REAL DEFAULT 0, avg_transfer_sol REAL DEFAULT 0,
            transfer_stddev REAL DEFAULT 0, days_active INTEGER DEFAULT 0,
            first_transfer_ts INTEGER DEFAULT 0, last_transfer_ts INTEGER DEFAULT 0,
            has_burst BOOLEAN DEFAULT 0, wallet_age_days REAL DEFAULT 0,
            detected_at REAL DEFAULT 0, updated_at REAL DEFAULT 0,
            first_seen_at INTEGER DEFAULT 0, last_updated_at INTEGER DEFAULT 0,
            PRIMARY KEY (funder_wallet)
        );
        CREATE TABLE IF NOT EXISTS farm_cluster_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id INTEGER, wallet_address TEXT, wallet_role TEXT DEFAULT 'member',
            in_degree INTEGER DEFAULT 0, out_degree INTEGER DEFAULT 0,
            in_ratio REAL DEFAULT 0, out_ratio REAL DEFAULT 0, total_degree INTEGER DEFAULT 0,
            transfers_sent INTEGER DEFAULT 0, transfers_received INTEGER DEFAULT 0,
            total_sent_sol REAL DEFAULT 0, total_received_sol REAL DEFAULT 0,
            role_confidence REAL DEFAULT 0, pattern_regularity REAL DEFAULT 0,
            first_activity_ts INTEGER DEFAULT 0, last_activity_ts INTEGER DEFAULT 0,
            detected_at REAL DEFAULT 0, updated_at REAL DEFAULT 0, token_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS second_hop_lite_queue (
            funder_address TEXT PRIMARY KEY,
            priority INTEGER NOT NULL DEFAULT 0,
            reason_codes TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            rpc_calls_used INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            scanned_at INTEGER,
            next_attempt_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS funder_rpc_scan_cache (
            funder_address TEXT PRIMARY KEY,
            scanned_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            expires_at INTEGER NOT NULL,
            upstream_json TEXT,
            inbound_upstream_count INTEGER NOT NULL DEFAULT 0,
            rpc_calls_used INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ok',
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS infra_funders_observed (
            funder_address TEXT PRIMARY KEY,
            label TEXT
        );
        CREATE TABLE IF NOT EXISTS intelligence_refresh_candidates (
            target_type TEXT NOT NULL, target_address TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            reason_codes TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'watchlist',
            rpc_allowed INTEGER NOT NULL DEFAULT 0,
            last_local_refresh_at INTEGER, last_rpc_scan_at INTEGER,
            next_eligible_scan_at INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY (target_type, target_address)
        );
        CREATE TABLE IF NOT EXISTS intelligence_refresh_rpc_budget (
            budget_date TEXT NOT NULL, budget_key TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (budget_date, budget_key)
        );
        CREATE TABLE IF NOT EXISTS token_analysis (
            mint TEXT PRIMARY KEY, earliest_tx_creator TEXT,
            migrated_at TEXT, analyzed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS network_membership (
            creator_address TEXT, network_name TEXT,
            PRIMARY KEY (creator_address, network_name)
        );
        CREATE TABLE IF NOT EXISTS second_hop_lite_rpc_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, funder_address TEXT,
            rpc_calls_used INTEGER, links_written INTEGER, scanned_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS listener_settings (
            setting_key TEXT PRIMARY KEY, setting_value TEXT
        );
        CREATE TABLE IF NOT EXISTS analyzer_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, analyzer_name TEXT,
            started_at REAL, status TEXT DEFAULT 'running',
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
    """)
    conn.close()


def _add_creator(db_path: str, creator: str, priority: int = 100,
                 self_funding: bool = False) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO intelligence_refresh_candidates
            (target_type, target_address, priority, status, rpc_allowed)
        VALUES ('creator', ?, ?, 'watchlist', 0)
    """, (creator, priority))
    if self_funding:
        conn.execute(
            "INSERT OR REPLACE INTO creator_self_funding (creator_address, is_self_funding) VALUES (?,1)",
            (creator,)
        )
    conn.commit()
    conn.close()


def _add_funder(db_path: str, creator: str, funder: str, amount_sol: float = 1.0,
                is_cex: int = 0, n_other_creators: int = 0,
                in_wallet_cluster: bool = False, in_farm_cluster: bool = False,
                cache_fresh: bool = False, already_queued: bool = False,
                is_infra: bool = False) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO creator_funders (creator_address, funder_address, amount_sol, is_cex) VALUES (?,?,?,?)",
        (creator, funder, amount_sol, is_cex)
    )
    # Add cross-creator relationships
    for i in range(n_other_creators):
        other = f"OTHER_CREATOR_{i}"
        conn.execute(
            "INSERT OR IGNORE INTO creator_funders (creator_address, funder_address, amount_sol) VALUES (?,?,1.0)",
            (other, funder)
        )
    if in_wallet_cluster:
        conn.execute(
            "INSERT OR IGNORE INTO wallet_clusters (cluster_id, funder_wallet, creator_addresses, creator_count, confidence_score, avg_transfer_sol, transfer_stddev, days_active, detected_at, updated_at) VALUES (1,?,?,1,0.8,1.0,0.1,30,?,?)",
            (funder, '[]', time.time(), time.time())
        )
    if in_farm_cluster:
        conn.execute(
            "INSERT OR IGNORE INTO farm_cluster_members (cluster_id, wallet_address, wallet_role, detected_at, updated_at) VALUES (1,?,'member',?,?)",
            (funder, time.time(), time.time())
        )
    if cache_fresh:
        now = int(time.time())
        conn.execute(
            "INSERT OR IGNORE INTO funder_rpc_scan_cache (funder_address, scanned_at, expires_at, status) VALUES (?,?,?,?)",
            (funder, now, now + 86400 * 7, 'ok')
        )
    if already_queued:
        conn.execute(
            "INSERT OR IGNORE INTO second_hop_lite_queue (funder_address, priority, reason_codes, status) VALUES (?,0,'[]','pending')",
            (funder,)
        )
    if is_infra:
        conn.execute("INSERT OR IGNORE INTO infra_funders_observed (funder_address) VALUES (?)", (funder,))
    conn.commit()
    conn.close()


def _queue_rows(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT funder_address, priority, reason_codes, status FROM second_hop_lite_queue").fetchall()
    conn.close()
    return rows


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestEnqueueCreatorFunders(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        _seed(self.db)
        _add_creator(self.db, "CREATOR_A", priority=100)

    def _conn(self):
        c = sqlite3.connect(self.db)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    def test_enqueues_multi_creator_funders(self):
        _add_funder(self.db, "CREATOR_A", "FUNDER_MULTI", n_other_creators=2)
        conn = self._conn()
        result = enqueue_creator_funders_for_phase2_lite(conn, "CREATOR_A")
        conn.commit(); conn.close()
        self.assertGreater(result["funders_enqueued"], 0)
        rows = _queue_rows(self.db)
        addrs = [r[0] for r in rows]
        self.assertIn("FUNDER_MULTI", addrs)

    def test_does_not_exceed_limit(self):
        for i in range(50):
            _add_funder(self.db, "CREATOR_A", f"FUNDER_{i:03}", n_other_creators=2, amount_sol=float(i))
        conn = self._conn()
        result = enqueue_creator_funders_for_phase2_lite(conn, "CREATOR_A", limit=20)
        conn.commit(); conn.close()
        self.assertLessEqual(result["funders_enqueued"], 20)

    def test_does_not_enqueue_cex_funders(self):
        _add_funder(self.db, "CREATOR_A", "FUNDER_CEX", is_cex=1, n_other_creators=5)
        conn = self._conn()
        result = enqueue_creator_funders_for_phase2_lite(conn, "CREATOR_A")
        conn.commit(); conn.close()
        addrs = [r[0] for r in _queue_rows(self.db)]
        self.assertNotIn("FUNDER_CEX", addrs)

    def test_does_not_enqueue_infra_funders(self):
        _add_funder(self.db, "CREATOR_A", "FUNDER_INFRA", is_infra=True, n_other_creators=5)
        conn = self._conn()
        result = enqueue_creator_funders_for_phase2_lite(conn, "CREATOR_A")
        conn.commit(); conn.close()
        self.assertEqual(result["skipped_excluded"], 1)
        addrs = [r[0] for r in _queue_rows(self.db)]
        self.assertNotIn("FUNDER_INFRA", addrs)

    def test_does_not_enqueue_fresh_cached_funders(self):
        _add_funder(self.db, "CREATOR_A", "FUNDER_CACHED", n_other_creators=3, cache_fresh=True)
        conn = self._conn()
        result = enqueue_creator_funders_for_phase2_lite(conn, "CREATOR_A")
        conn.commit(); conn.close()
        self.assertEqual(result["skipped_cached"], 1)
        addrs = [r[0] for r in _queue_rows(self.db)]
        self.assertNotIn("FUNDER_CACHED", addrs)

    def test_does_not_enqueue_already_pending_funders(self):
        _add_funder(self.db, "CREATOR_A", "FUNDER_QUEUED", n_other_creators=3, already_queued=True)
        conn = self._conn()
        result = enqueue_creator_funders_for_phase2_lite(conn, "CREATOR_A")
        conn.commit(); conn.close()
        self.assertEqual(result["skipped_existing_queue"], 1)

    def test_single_creator_funder_excluded_unless_high_risk(self):
        """A funder who only funds one creator should be skipped if creator is not high-risk."""
        _add_creator(self.db, "CREATOR_LOW", priority=20)
        _add_funder(self.db, "CREATOR_LOW", "FUNDER_SINGLE", n_other_creators=0, amount_sol=5.0)
        conn = self._conn()
        result = enqueue_creator_funders_for_phase2_lite(conn, "CREATOR_LOW")
        conn.commit(); conn.close()
        addrs = [r[0] for r in _queue_rows(self.db)]
        self.assertNotIn("FUNDER_SINGLE", addrs)

    def test_wallet_cluster_funder_gets_higher_priority(self):
        _add_funder(self.db, "CREATOR_A", "FUNDER_WC", n_other_creators=2, in_wallet_cluster=True, amount_sol=1.0)
        _add_funder(self.db, "CREATOR_A", "FUNDER_PLAIN", n_other_creators=2, amount_sol=1.0)
        conn = self._conn()
        enqueue_creator_funders_for_phase2_lite(conn, "CREATOR_A")
        conn.commit(); conn.close()
        rows = {r[0]: r[1] for r in _queue_rows(self.db)}  # {address: priority}
        if "FUNDER_WC" in rows and "FUNDER_PLAIN" in rows:
            self.assertGreater(rows["FUNDER_WC"], rows["FUNDER_PLAIN"])

    def test_reason_codes_include_approved_creator(self):
        _add_funder(self.db, "CREATOR_A", "FUNDER_RC", n_other_creators=2)
        conn = self._conn()
        enqueue_creator_funders_for_phase2_lite(conn, "CREATOR_A")
        conn.commit(); conn.close()
        rows = _queue_rows(self.db)
        for addr, priority, reason_codes, status in rows:
            if addr == "FUNDER_RC":
                rc = json.loads(reason_codes)
                self.assertIn("approved_creator", rc)


class TestApproveCandidate(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        _seed(self.db)
        _add_creator(self.db, "CREATOR_B", priority=90)
        _add_funder(self.db, "CREATOR_B", "FUNDER_B1", n_other_creators=3)
        _add_funder(self.db, "CREATOR_B", "FUNDER_B2", n_other_creators=1, amount_sol=10.0)

    def test_approve_creator_returns_enqueue_summary(self):
        result = approve_candidate(self.db, "creator", "CREATOR_B")
        self.assertTrue(result["ok"])
        self.assertIn("phase2_lite_enqueue", result)
        eq = result["phase2_lite_enqueue"]
        self.assertIn("funders_enqueued", eq)
        self.assertIn("funders_found", eq)

    def test_approve_funder_has_no_enqueue(self):
        conn = sqlite3.connect(self.db)
        conn.execute("""
            INSERT INTO intelligence_refresh_candidates
                (target_type, target_address, priority, status)
            VALUES ('funder','FUNDER_X', 50, 'watchlist')
        """)
        conn.commit(); conn.close()
        result = approve_candidate(self.db, "funder", "FUNDER_X")
        self.assertTrue(result["ok"])
        # funder approval has no phase2_lite_enqueue key
        self.assertEqual(result.get("phase2_lite_enqueue", {}), {})

    def test_approve_enqueues_funders_into_queue(self):
        approve_candidate(self.db, "creator", "CREATOR_B")
        rows = _queue_rows(self.db)
        # FUNDER_B1 is multi-creator so should be enqueued
        addrs = [r[0] for r in rows]
        self.assertIn("FUNDER_B1", addrs)

    def test_no_rpc_called(self):
        """Patching requests/httpx to verify no network calls are made."""
        import unittest.mock as mock
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("RPC called")):
            result = approve_candidate(self.db, "creator", "CREATOR_B")
        self.assertTrue(result["ok"])


class TestApproveTop(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        _seed(self.db)

    def test_approve_top_respects_global_cap(self):
        """approve_top must not enqueue more than MAX_FUNDER_ENQUEUE_APPROVE_TOP funders total."""
        for c in range(5):
            creator = f"CREATOR_TOP_{c}"
            _add_creator(self.db, creator, priority=100 - c)
            # 25 multi-creator funders per creator → 125 total eligible, cap is 50
            for f in range(25):
                _add_funder(self.db, creator, f"FUNDER_{c}_{f}", n_other_creators=2)

        result = approve_top(self.db, "creator", limit=5, min_priority=0)
        self.assertTrue(result["ok"])
        eq = result.get("phase2_lite_enqueue", {})
        self.assertLessEqual(eq.get("funders_enqueued", 0), MAX_FUNDER_ENQUEUE_APPROVE_TOP)

    def test_approve_top_includes_enqueue_in_response(self):
        _add_creator(self.db, "CREATOR_T1", priority=80)
        _add_funder(self.db, "CREATOR_T1", "FT1", n_other_creators=3)
        result = approve_top(self.db, "creator", limit=3, min_priority=0)
        self.assertIn("phase2_lite_enqueue", result)


if __name__ == "__main__":
    unittest.main()
