"""
Tests for relationship_events.py — snapshot/diff, dedup, rebuild safety.
All tests use temp SQLite files. Zero RPC calls made.
"""

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.relationship_events import (
    apply_migration,
    take_snapshot,
    diff_and_log,
    get_recent_events,
    rebuild_after_scan,
)


# ── Schema helpers ─────────────────────────────────────────────────────────────

def _make_db() -> str:
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.close()
    return f.name


def _seed_schema(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS funder_upstream_links (
            funder_address TEXT, upstream_address TEXT, is_excluded INTEGER DEFAULT 0,
            PRIMARY KEY (funder_address, upstream_address)
        );
        CREATE TABLE IF NOT EXISTS upstream_network_bridge (
            upstream_address TEXT, network_a TEXT, network_b TEXT, is_excluded INTEGER DEFAULT 0,
            confidence_score REAL DEFAULT 0, risk_level TEXT DEFAULT 'LOW', reason_codes TEXT,
            PRIMARY KEY (upstream_address, network_a, network_b)
        );
        CREATE TABLE IF NOT EXISTS creator_second_hop (
            creator_address TEXT, upstream_address TEXT, via_funder TEXT,
            confidence_score REAL DEFAULT 0, risk_level TEXT DEFAULT 'LOW', reason_codes TEXT,
            PRIMARY KEY (creator_address, upstream_address)
        );
        CREATE TABLE IF NOT EXISTS network_membership (
            creator_address TEXT, network_name TEXT,
            PRIMARY KEY (creator_address, network_name)
        );
        CREATE TABLE IF NOT EXISTS intelligence_refresh_candidates (
            target_type TEXT, target_address TEXT, status TEXT DEFAULT 'watchlist',
            priority INTEGER DEFAULT 0, reason_codes TEXT DEFAULT '[]',
            rpc_allowed INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            updated_at INTEGER DEFAULT (strftime('%s','now')),
            PRIMARY KEY (target_type, target_address)
        );
        CREATE TABLE IF NOT EXISTS analyzer_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analyzer_name TEXT, started_at REAL, status TEXT DEFAULT 'running',
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS token_analysis (
            mint TEXT PRIMARY KEY, earliest_tx_creator TEXT, migrated_at TEXT, analyzed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS creator_funders (
            creator_address TEXT, funder_address TEXT, is_cex INTEGER DEFAULT 0,
            PRIMARY KEY (creator_address, funder_address)
        );
        CREATE TABLE IF NOT EXISTS creator_self_funding (
            creator_address TEXT PRIMARY KEY, is_self_funding INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS second_hop_lite_queue (
            funder_address TEXT PRIMARY KEY, status TEXT DEFAULT 'pending', scanned_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS funder_rpc_scan_cache (
            funder_address TEXT PRIMARY KEY, scanned_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS second_hop_lite_rpc_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, funder_address TEXT, rpc_calls_used INTEGER,
            links_written INTEGER, scanned_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS listener_settings (
            setting_key TEXT PRIMARY KEY, setting_value TEXT
        );
        CREATE TABLE IF NOT EXISTS intelligence_refresh_rpc_budget (
            budget_date TEXT, budget_key TEXT, used INTEGER DEFAULT 0,
            PRIMARY KEY (budget_date, budget_key)
        );
    """)
    conn.close()
    apply_migration(db_path)


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestSnapshotAndDiff(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        _seed_schema(self.db)

    def _conn(self):
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def test_new_upstream_link_logs_event(self):
        before = take_snapshot(self.db)

        conn = self._conn()
        conn.execute("INSERT INTO funder_upstream_links (funder_address, upstream_address, is_excluded) VALUES ('FUNDER1','UPSTREAM1',0)")
        conn.commit()
        conn.close()

        result = diff_and_log(self.db, before, scan_source="test")
        self.assertTrue(result["ok"])
        self.assertEqual(result["funder_upstream_found"], 1)

        # Verify event in DB
        conn = self._conn()
        row = conn.execute("SELECT * FROM intelligence_relationship_events WHERE relationship_type='funder_upstream_found'").fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_new_second_hop_creator_logs_event(self):
        before = take_snapshot(self.db)

        conn = self._conn()
        conn.execute("INSERT INTO creator_second_hop (creator_address, upstream_address, via_funder, confidence_score, risk_level) VALUES ('CREATOR1','UPSTREAM1','FUNDER1',75,'HIGH')")
        conn.commit()
        conn.close()

        result = diff_and_log(self.db, before, scan_source="test")
        self.assertEqual(result["creator_second_hop_found"], 1)

    def test_network_bridge_logs_event(self):
        before = take_snapshot(self.db)

        conn = self._conn()
        conn.execute("INSERT INTO upstream_network_bridge (upstream_address, network_a, network_b, is_excluded) VALUES ('UP1','Net_A','Net_B',0)")
        conn.commit()
        conn.close()

        result = diff_and_log(self.db, before)
        self.assertEqual(result["upstream_network_bridge_found"], 1)

    def test_duplicate_events_not_repeated(self):
        """Running diff twice with same data should not insert duplicate events."""
        conn = self._conn()
        conn.execute("INSERT INTO funder_upstream_links (funder_address, upstream_address, is_excluded) VALUES ('FUNDER2','UPSTREAM2',0)")
        conn.commit()
        conn.close()

        before = take_snapshot(self.db)
        # Before already includes this row, so diff should find 0 new
        result = diff_and_log(self.db, before, scan_source="test")
        self.assertEqual(result["funder_upstream_found"], 0)

        # Insert a genuinely new row
        before2 = take_snapshot(self.db)
        conn = self._conn()
        conn.execute("INSERT INTO funder_upstream_links (funder_address, upstream_address, is_excluded) VALUES ('FUNDER3','UPSTREAM3',0)")
        conn.commit()
        conn.close()

        diff_and_log(self.db, before2, scan_source="test")
        diff_and_log(self.db, before2, scan_source="test")  # run twice

        conn = self._conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM intelligence_relationship_events WHERE source_address='FUNDER3'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_excluded_links_not_logged(self):
        before = take_snapshot(self.db)

        conn = self._conn()
        conn.execute("INSERT INTO funder_upstream_links (funder_address, upstream_address, is_excluded) VALUES ('FUNDER_EX','UPSTREAM_EX',1)")
        conn.commit()
        conn.close()

        result = diff_and_log(self.db, before, scan_source="test")
        self.assertEqual(result["funder_upstream_found"], 0)

    def test_empty_snapshot_skips_gracefully(self):
        result = diff_and_log(self.db, {}, scan_source="test")
        self.assertIn("skipped", result)

    def test_get_recent_events_returns_summary(self):
        # Seed one event
        _seed_schema(self.db)
        before = take_snapshot(self.db)
        conn = self._conn()
        conn.execute("INSERT INTO funder_upstream_links (funder_address, upstream_address, is_excluded) VALUES ('FA','UA',0)")
        conn.commit()
        conn.close()
        diff_and_log(self.db, before, scan_source="test")

        result = get_recent_events(self.db, limit=10, type_filter="all", since_hours=1)
        self.assertIn("summary", result)
        self.assertIn("events", result)
        self.assertIn("new_funder_links_today", result["summary"])

    def test_get_recent_events_type_filter(self):
        before = take_snapshot(self.db)
        conn = self._conn()
        conn.execute("INSERT INTO funder_upstream_links (funder_address, upstream_address, is_excluded) VALUES ('FB','UB',0)")
        conn.execute("INSERT INTO creator_second_hop (creator_address, upstream_address, via_funder) VALUES ('CB','UB','FB')")
        conn.commit()
        conn.close()
        diff_and_log(self.db, before, scan_source="test")

        result = get_recent_events(self.db, limit=10, type_filter="2h", since_hours=1)
        rel_types = {e["relationship_type"] for e in result["events"]}
        # Only 2H types should appear
        self.assertTrue(rel_types.issubset({'creator_second_hop_found', 'upstream_network_bridge_found'}))


class TestRebuildAfterScan(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        _seed_schema(self.db)

    def test_rebuild_failure_does_not_raise(self):
        """rebuild_after_scan must return a dict even if sub-builders fail."""
        with patch("src.core.relationship_events.SHL_REBUILD_AFTER_SCAN", True):
            with patch("src.core.second_hop_builder.SecondHopExpansionBuilder.build", side_effect=RuntimeError("boom")):
                result = rebuild_after_scan(self.db, scan_id="test_scan")
        # Must return a dict, not raise
        self.assertIsInstance(result, dict)
        self.assertIn("second_hop", result)

    def test_rebuild_disabled_returns_skipped(self):
        with patch("src.core.relationship_events.SHL_REBUILD_AFTER_SCAN", False):
            result = rebuild_after_scan(self.db)
        self.assertEqual(result.get("skipped"), True)

    def test_rebuild_logs_new_events(self):
        """If rebuild produces new data, events should be logged."""
        before = take_snapshot(self.db)

        # Manually insert a new link (simulating what a rebuild would produce)
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO funder_upstream_links (funder_address, upstream_address, is_excluded) VALUES ('FC','UC',0)")
        conn.commit()
        conn.close()

        result = diff_and_log(self.db, before, scan_source="test_rebuild", scan_id="scan_001")
        self.assertEqual(result["funder_upstream_found"], 1)

        # Check event has correct scan metadata
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT scan_source, scan_id FROM intelligence_relationship_events WHERE source_address='FC'").fetchone()
        conn.close()
        self.assertEqual(row[0], "test_rebuild")
        self.assertEqual(row[1], "scan_001")


if __name__ == "__main__":
    unittest.main()
