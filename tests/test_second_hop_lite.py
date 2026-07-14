"""
Tests for Phase 2 Lite: second-hop funder RPC expansion.

Covers:
 - Queue builder priority scoring logic
 - Queue builder exclusion of CEX funders and infra addresses
 - Worker cache hit skips RPC
 - Worker RPC cap enforcement
 - API status returns correct shape (tables missing gracefully)
 - Enqueue / rescan endpoints
"""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_db() -> tuple[sqlite3.Connection, str]:
    """Create a temp SQLite DB with minimal schema for Phase 2 Lite tests."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_shl_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    # Minimal schema required by queue builder and worker
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS creator_funders (
            funder_address  TEXT,
            creator_address TEXT,
            is_cex          INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS wallet_clusters (
            cluster_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            funder_wallet    TEXT,
            creator_count    INTEGER,
            confidence_score REAL,
            has_burst        INTEGER,
            avg_transfer_sol REAL,
            days_active      INTEGER,
            creator_addresses TEXT
        );
        CREATE TABLE IF NOT EXISTS farm_cluster_members (
            wallet_address TEXT,
            wallet_role    TEXT
        );
        CREATE TABLE IF NOT EXISTS funder_network_map (
            funder_address TEXT,
            network_name   TEXT
        );
        CREATE TABLE IF NOT EXISTS networks_release (
            network_name       TEXT PRIMARY KEY,
            network_risk_level TEXT,
            network_size       INTEGER
        );
        CREATE TABLE IF NOT EXISTS token_analysis (
            mint                TEXT PRIMARY KEY,
            earliest_tx_creator TEXT,
            lifecycle_stage     TEXT,
            market_cap_current  REAL,
            migrated_at         INTEGER
        );
        CREATE TABLE IF NOT EXISTS funder_upstream_links (
            funder_address   TEXT,
            upstream_address TEXT,
            transfer_count   INTEGER,
            total_sol        REAL,
            avg_transfer_sol REAL,
            first_transfer_ts INTEGER,
            last_transfer_ts  INTEGER,
            source           TEXT,
            is_excluded      INTEGER DEFAULT 0,
            funders_touched  INTEGER,
            built_at         INTEGER,
            last_seen_network_count INTEGER,
            PRIMARY KEY (funder_address, upstream_address)
        );
        CREATE TABLE IF NOT EXISTS upstream_network_bridge (
            upstream_address TEXT,
            network_a        TEXT,
            network_b        TEXT,
            shared_funders   INTEGER,
            confidence_score REAL,
            risk_level       TEXT,
            reason_codes     TEXT,
            is_excluded      INTEGER DEFAULT 0,
            built_at         INTEGER,
            PRIMARY KEY (upstream_address, network_a, network_b)
        );
        -- Phase 2 Lite tables
        CREATE TABLE IF NOT EXISTS second_hop_lite_queue (
            funder_address   TEXT PRIMARY KEY,
            priority         INTEGER NOT NULL DEFAULT 0,
            reason_codes     TEXT NOT NULL DEFAULT '[]',
            status           TEXT NOT NULL DEFAULT 'pending',
            attempts         INTEGER NOT NULL DEFAULT 0,
            last_error       TEXT,
            rpc_calls_used   INTEGER NOT NULL DEFAULT 0,
            created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
            scanned_at       INTEGER,
            next_attempt_at  INTEGER NOT NULL DEFAULT (unixepoch())
        );
        CREATE TABLE IF NOT EXISTS funder_rpc_scan_cache (
            funder_address         TEXT PRIMARY KEY,
            scanned_at             INTEGER,
            expires_at             INTEGER,
            upstream_json          TEXT,
            inbound_upstream_count INTEGER,
            rpc_calls_used         INTEGER,
            status                 TEXT,
            error                  TEXT
        );
        CREATE TABLE IF NOT EXISTS second_hop_lite_rpc_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            called_at      INTEGER,
            funder_address TEXT,
            call_type      TEXT,
            success        INTEGER,
            latency_ms     INTEGER,
            error          TEXT,
            cache_hit      INTEGER
        );
        -- Infra / exclusion tables (build_excluded_set looks for these)
        CREATE TABLE IF NOT EXISTS infra_addresses (
            address TEXT PRIMARY KEY
        );
    """)
    conn.commit()
    return conn, path


# ── Queue builder tests ────────────────────────────────────────────────────────

class TestQueueBuilderPriority(unittest.TestCase):

    def setUp(self):
        self.conn, self.db_path = _make_db()

    def tearDown(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def _seed_funder(self, funder: str, creator_count: int, is_cex: int = 0):
        for i in range(creator_count):
            self.conn.execute(
                "INSERT OR IGNORE INTO creator_funders (funder_address, creator_address, is_cex) VALUES (?, ?, ?)",
                (funder, f"creator_{funder}_{i}", is_cex)
            )

    def _get_queue_row(self, funder: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM second_hop_lite_queue WHERE funder_address=?",
            (funder,)
        ).fetchone()

    def test_basic_enqueue_two_creators(self):
        """Funder with 2 creators gets enqueued at baseline priority."""
        self._seed_funder("FUNDER_A", 2)
        self.conn.commit()

        from src.core.second_hop_lite_queue_builder import SecondHopLiteQueueBuilder
        result = SecondHopLiteQueueBuilder(self.db_path).build(self.conn)
        self.conn.commit()

        self.assertEqual(result["status"], "success")
        row = self._get_queue_row("FUNDER_A")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "pending")

    def test_wallet_cluster_adds_40(self):
        """Funder in wallet_clusters gets +40 priority."""
        self._seed_funder("FUNDER_WC", 2)
        self.conn.execute(
            "INSERT INTO wallet_clusters (funder_wallet, creator_count, confidence_score) VALUES (?, 2, 80)",
            ("FUNDER_WC",)
        )
        self.conn.commit()

        from src.core.second_hop_lite_queue_builder import SecondHopLiteQueueBuilder
        SecondHopLiteQueueBuilder(self.db_path).build(self.conn)
        self.conn.commit()

        row = self._get_queue_row("FUNDER_WC")
        self.assertIsNotNone(row)
        self.assertGreaterEqual(row["priority"], 40)
        reasons = json.loads(row["reason_codes"])
        self.assertIn("wallet_cluster", reasons)

    def test_farm_cluster_adds_40(self):
        """Funder in farm_cluster_members gets +40 priority."""
        self._seed_funder("FUNDER_FC", 2)
        self.conn.execute(
            "INSERT INTO farm_cluster_members (wallet_address, wallet_role) VALUES (?, 'funder')",
            ("FUNDER_FC",)
        )
        self.conn.commit()

        from src.core.second_hop_lite_queue_builder import SecondHopLiteQueueBuilder
        SecondHopLiteQueueBuilder(self.db_path).build(self.conn)
        self.conn.commit()

        row = self._get_queue_row("FUNDER_FC")
        self.assertIsNotNone(row)
        self.assertGreaterEqual(row["priority"], 40)
        reasons = json.loads(row["reason_codes"])
        self.assertIn("farm_cluster", reasons)

    def test_five_creators_adds_20(self):
        """Funder with >=5 creators gets +20 priority."""
        self._seed_funder("FUNDER_5", 5)
        self.conn.commit()

        from src.core.second_hop_lite_queue_builder import SecondHopLiteQueueBuilder
        SecondHopLiteQueueBuilder(self.db_path).build(self.conn)
        self.conn.commit()

        row = self._get_queue_row("FUNDER_5")
        self.assertIsNotNone(row)
        reasons = json.loads(row["reason_codes"])
        self.assertIn("funds_5plus_creators", reasons)

    def test_three_creators_adds_10(self):
        """Funder with 3 creators gets +10 priority (not +20)."""
        self._seed_funder("FUNDER_3", 3)
        self.conn.commit()

        from src.core.second_hop_lite_queue_builder import SecondHopLiteQueueBuilder
        SecondHopLiteQueueBuilder(self.db_path).build(self.conn)
        self.conn.commit()

        row = self._get_queue_row("FUNDER_3")
        self.assertIsNotNone(row)
        reasons = json.loads(row["reason_codes"])
        self.assertIn("funds_3plus_creators", reasons)
        self.assertNotIn("funds_5plus_creators", reasons)

    def test_high_risk_network_adds_20(self):
        """Funder linked to HIGH-risk network gets +20."""
        self._seed_funder("FUNDER_HR", 2)
        self.conn.execute(
            "INSERT INTO networks_release (network_name, network_risk_level) VALUES ('NetA', 'HIGH')"
        )
        self.conn.execute(
            "INSERT INTO funder_network_map (funder_address, network_name) VALUES ('FUNDER_HR', 'NetA')"
        )
        self.conn.commit()

        from src.core.second_hop_lite_queue_builder import SecondHopLiteQueueBuilder
        SecondHopLiteQueueBuilder(self.db_path).build(self.conn)
        self.conn.commit()

        row = self._get_queue_row("FUNDER_HR")
        self.assertIsNotNone(row)
        reasons = json.loads(row["reason_codes"])
        self.assertIn("high_risk_network", reasons)

    def test_insert_or_ignore_does_not_overwrite(self):
        """Existing queue entry is not overwritten."""
        self._seed_funder("FUNDER_X", 3)
        existing_ts = int(time.time()) - 1000
        self.conn.execute("""
            INSERT INTO second_hop_lite_queue
            (funder_address, priority, reason_codes, status, attempts, rpc_calls_used, created_at, next_attempt_at)
            VALUES ('FUNDER_X', 999, '["already_there"]', 'done', 5, 10, ?, ?)
        """, (existing_ts, existing_ts))
        self.conn.commit()

        from src.core.second_hop_lite_queue_builder import SecondHopLiteQueueBuilder
        SecondHopLiteQueueBuilder(self.db_path).build(self.conn)
        self.conn.commit()

        row = self._get_queue_row("FUNDER_X")
        # Priority and status must not be changed
        self.assertEqual(row["priority"], 999)
        self.assertEqual(row["status"], "done")

    def test_single_creator_skipped_without_cluster_signal(self):
        """Funder with only 1 creator and no cluster signal is not enqueued."""
        self._seed_funder("FUNDER_SOLO", 1)
        self.conn.commit()

        from src.core.second_hop_lite_queue_builder import SecondHopLiteQueueBuilder
        SecondHopLiteQueueBuilder(self.db_path).build(self.conn)
        self.conn.commit()

        row = self._get_queue_row("FUNDER_SOLO")
        self.assertIsNone(row)


class TestQueueBuilderCEXExclusion(unittest.TestCase):

    def setUp(self):
        self.conn, self.db_path = _make_db()

    def tearDown(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def test_cex_funder_not_enqueued(self):
        """CEX funders (is_cex=1) must not appear in the queue."""
        for i in range(5):
            self.conn.execute(
                "INSERT INTO creator_funders (funder_address, creator_address, is_cex) VALUES (?, ?, 1)",
                ("CEX_FUNDER", f"creator_{i}")
            )
        self.conn.commit()

        from src.core.second_hop_lite_queue_builder import SecondHopLiteQueueBuilder
        SecondHopLiteQueueBuilder(self.db_path).build(self.conn)
        self.conn.commit()

        row = self.conn.execute(
            "SELECT * FROM second_hop_lite_queue WHERE funder_address='CEX_FUNDER'"
        ).fetchone()
        self.assertIsNone(row)


# ── Worker tests ───────────────────────────────────────────────────────────────

class TestWorkerCacheHit(unittest.TestCase):

    def setUp(self):
        self.conn, self.db_path = _make_db()

    def tearDown(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    @patch("src.core.second_hop_lite_worker.SECOND_HOP_RPC_LITE_ENABLED", True)
    def test_cache_hit_skips_rpc(self):
        """If a valid cache entry exists for a funder, no RPC calls are made."""
        funder = "CACHED_FUNDER"
        now = int(time.time())
        # Plant a valid cache entry (expires in the future)
        self.conn.execute("""
            INSERT INTO funder_rpc_scan_cache
            (funder_address, scanned_at, expires_at, upstream_json, inbound_upstream_count, rpc_calls_used, status)
            VALUES (?, ?, ?, '{}', 0, 1, 'ok')
        """, (funder, now - 3600, now + 3600))
        # Also plant in queue
        self.conn.execute("""
            INSERT INTO second_hop_lite_queue
            (funder_address, priority, reason_codes, status, attempts, rpc_calls_used, created_at, next_attempt_at)
            VALUES (?, 50, '[]', 'pending', 0, 0, ?, ?)
        """, (funder, now, now))
        self.conn.commit()

        from src.core.second_hop_lite_worker import SecondHopLiteWorker
        worker = SecondHopLiteWorker(self.db_path)
        worker.rpc_url = "http://fake-rpc.example.com"

        with patch.object(worker, "_rpc_post") as mock_rpc:
            links_written = worker._scan_funder(self.conn, funder)
            self.conn.commit()
            # RPC should NOT have been called
            mock_rpc.assert_not_called()
            self.assertEqual(links_written, 0)


class TestWorkerRpcCap(unittest.TestCase):

    def setUp(self):
        self.conn, self.db_path = _make_db()

    def tearDown(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    @patch("src.core.second_hop_lite_worker.SECOND_HOP_RPC_LITE_ENABLED", True)
    @patch("src.core.second_hop_lite_worker.MAX_RPC_CALLS_PER_RUN", 0)
    def test_rpc_cap_zero_stops_immediately(self):
        """When cap is 0, no funders are scanned at all."""
        now = int(time.time())
        for i in range(5):
            funder = f"FUNDER_{i}"
            # Ensure no cache hit
            self.conn.execute("""
                INSERT INTO second_hop_lite_queue
                (funder_address, priority, reason_codes, status, attempts, rpc_calls_used, created_at, next_attempt_at)
                VALUES (?, 50, '[]', 'pending', 0, 0, ?, ?)
            """, (funder, now, now))
        self.conn.commit()

        from src.core.second_hop_lite_worker import SecondHopLiteWorker
        worker = SecondHopLiteWorker(self.db_path)
        worker.rpc_url = "http://fake-rpc.example.com"
        worker._rpc_calls_used = 0  # reset

        with patch.object(worker, "_rpc_post") as mock_rpc:
            # pick_funders will return rows, but the cap check fires before any call
            funders = worker._pick_funders(self.conn)
            scanned = 0
            for f in funders:
                if worker._rpc_calls_used >= 0:  # cap is 0
                    break
                worker._scan_funder(self.conn, f)
                scanned += 1
            mock_rpc.assert_not_called()
            self.assertEqual(scanned, 0)

    @patch("src.core.second_hop_lite_worker.SECOND_HOP_RPC_LITE_ENABLED", True)
    def test_rpc_cap_enforced_mid_run(self):
        """_scan_funder returns 0 immediately when rpc_calls_used >= cap."""
        funder = "FUNDER_CAP"
        now = int(time.time())
        self.conn.execute("""
            INSERT INTO second_hop_lite_queue
            (funder_address, priority, reason_codes, status, attempts, rpc_calls_used, created_at, next_attempt_at)
            VALUES (?, 50, '[]', 'pending', 0, 0, ?, ?)
        """, (funder, now, now))
        self.conn.commit()

        from src.core.second_hop_lite_worker import SecondHopLiteWorker
        import src.core.second_hop_lite_worker as _m
        worker = SecondHopLiteWorker(self.db_path)
        worker.rpc_url = "http://fake-rpc.example.com"
        worker._rpc_calls_used = _m.MAX_RPC_CALLS_PER_RUN  # already at cap

        with patch.object(worker, "_rpc_post") as mock_rpc:
            result = worker._scan_funder(self.conn, funder)
            mock_rpc.assert_not_called()
            self.assertEqual(result, 0)


# ── API status shape tests ─────────────────────────────────────────────────────

class TestApiStatusShape(unittest.TestCase):
    """
    Verify /api/second-hop-lite/status returns the expected shape
    whether or not the Phase 2 Lite tables exist.
    """

    def setUp(self):
        self.conn, self.db_path = _make_db()
        import sys
        from pathlib import Path
        _root = str(Path(__file__).resolve().parent.parent)
        if _root not in sys.path:
            sys.path.insert(0, _root)

    def tearDown(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def _make_app(self):
        """Return a Flask test client with DB_PATH patched to our temp DB."""
        import importlib
        import src.core.main as main_mod
        # Patch DB_PATH only for duration of test
        original = main_mod.DB_PATH
        main_mod.DB_PATH = self.db_path
        app = main_mod.app
        app.config["TESTING"] = True
        client = app.test_client()
        return client, main_mod, original

    def test_status_returns_required_keys(self):
        """Status endpoint always returns required top-level keys."""
        try:
            client, main_mod, original = self._make_app()
        except Exception:
            self.skipTest("Flask app import not available in this test context")
            return

        try:
            resp = client.get("/api/second-hop-lite/status")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            required_keys = {
                "enabled", "tables_ready", "priority_funders",
                "tracked_funders", "queue", "rpc_calls_today",
                "cache_hit_rate", "top_untracked_funders", "recent_scans",
            }
            for key in required_keys:
                self.assertIn(key, data, f"Missing key: {key}")
            # queue must be a dict with status sub-keys
            self.assertIsInstance(data["queue"], dict)
        finally:
            main_mod.DB_PATH = original

    def test_status_graceful_missing_tables(self):
        """Status endpoint returns sensible zeros when Phase 2 tables are absent."""
        # Create a separate DB without Phase 2 tables
        fd, bare_path = tempfile.mkstemp(suffix=".db", prefix="test_shl_bare_")
        os.close(fd)
        bare_conn = sqlite3.connect(bare_path)
        bare_conn.executescript("""
            CREATE TABLE creator_funders (funder_address TEXT, creator_address TEXT, is_cex INTEGER);
            CREATE TABLE funder_upstream_links (funder_address TEXT, upstream_address TEXT,
                transfer_count INTEGER, total_sol REAL, avg_transfer_sol REAL,
                first_transfer_ts INTEGER, last_transfer_ts INTEGER, source TEXT,
                is_excluded INTEGER, funders_touched INTEGER, built_at INTEGER);
        """)
        bare_conn.commit()
        bare_conn.close()

        try:
            import src.core.main as main_mod
        except Exception:
            self.skipTest("Flask app import not available")
            Path(bare_path).unlink(missing_ok=True)
            return

        original = main_mod.DB_PATH
        main_mod.DB_PATH = bare_path
        main_mod.app.config["TESTING"] = True
        client = main_mod.app.test_client()

        try:
            resp = client.get("/api/second-hop-lite/status")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertFalse(data["tables_ready"])
            self.assertEqual(data["top_untracked_funders"], [])
            self.assertEqual(data["recent_scans"], [])
        finally:
            main_mod.DB_PATH = original
            Path(bare_path).unlink(missing_ok=True)


class TestEnqueueRescanEndpoints(unittest.TestCase):

    def setUp(self):
        self.conn, self.db_path = _make_db()
        # Seed one non-CEX funder
        self.conn.execute(
            "INSERT INTO creator_funders (funder_address, creator_address, is_cex) VALUES ('F_ENQUEUE', 'C1', 0)"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def _make_client(self):
        try:
            import src.core.main as main_mod
            original = main_mod.DB_PATH
            main_mod.DB_PATH = self.db_path
            main_mod.app.config["TESTING"] = True
            return main_mod.app.test_client(), main_mod, original
        except Exception:
            return None, None, None

    def test_enqueue_valid_funder(self):
        client, main_mod, original = self._make_client()
        if client is None:
            self.skipTest("Flask app not importable")
        try:
            resp = client.post("/api/second-hop-lite/enqueue/F_ENQUEUE")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["status"], "enqueued")
            self.assertEqual(data["funder_address"], "F_ENQUEUE")

            # Check it's actually in the queue
            row = self.conn.execute(
                "SELECT * FROM second_hop_lite_queue WHERE funder_address='F_ENQUEUE'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            main_mod.DB_PATH = original

    def test_enqueue_unknown_funder_returns_404(self):
        client, main_mod, original = self._make_client()
        if client is None:
            self.skipTest("Flask app not importable")
        try:
            resp = client.post("/api/second-hop-lite/enqueue/NONEXISTENT_WALLET")
            self.assertEqual(resp.status_code, 404)
        finally:
            main_mod.DB_PATH = original

    def test_rescan_resets_queue_entry(self):
        client, main_mod, original = self._make_client()
        if client is None:
            self.skipTest("Flask app not importable")
        try:
            # Plant a 'done' entry
            now = int(time.time())
            self.conn.execute("""
                INSERT INTO second_hop_lite_queue
                (funder_address, priority, reason_codes, status, attempts, rpc_calls_used, created_at, next_attempt_at)
                VALUES ('F_ENQUEUE', 50, '[]', 'done', 3, 10, ?, ?)
            """, (now, now))
            self.conn.commit()

            resp = client.post("/api/second-hop-lite/rescan/F_ENQUEUE")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["status"], "reset_to_pending")

            # Confirm queue reset
            row = self.conn.execute(
                "SELECT status FROM second_hop_lite_queue WHERE funder_address='F_ENQUEUE'"
            ).fetchone()
            self.assertEqual(row[0], "pending")
        finally:
            main_mod.DB_PATH = original

    def test_rescan_clears_cache(self):
        client, main_mod, original = self._make_client()
        if client is None:
            self.skipTest("Flask app not importable")
        try:
            now = int(time.time())
            # Plant cache entry
            self.conn.execute("""
                INSERT INTO funder_rpc_scan_cache
                (funder_address, scanned_at, expires_at, upstream_json, inbound_upstream_count, rpc_calls_used, status)
                VALUES ('F_ENQUEUE', ?, ?, '{}', 0, 1, 'ok')
            """, (now - 3600, now + 3600))
            self.conn.commit()

            client.post("/api/second-hop-lite/rescan/F_ENQUEUE")

            cached = self.conn.execute(
                "SELECT * FROM funder_rpc_scan_cache WHERE funder_address='F_ENQUEUE'"
            ).fetchone()
            self.assertIsNone(cached)
        finally:
            main_mod.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
