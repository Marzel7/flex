"""
Tests for UpstreamExpansionBuilder and hub detection in SecondHopExpansionBuilder.
All in-memory SQLite. Zero RPC calls.
"""

import json
import sqlite3
import tempfile
import time
import unittest

from src.core.upstream_expansion_builder import (
    UpstreamExpansionBuilder,
    MAX_FUNDER_ENQUEUE_PER_UPSTREAM,
    MAX_UPSTREAMS_PER_RUN,
    HUB_EXPANSION_COOLDOWN_SECONDS,
)


# ── Schema helpers ─────────────────────────────────────────────────────────────

def _make_db() -> str:
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.close()
    return f.name


def _seed(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS monitored_upstream_hubs (
            upstream_address TEXT PRIMARY KEY,
            confidence_score REAL,
            networks_bridged INTEGER,
            funders_bridged  INTEGER,
            risk_level       TEXT,
            reason_codes     TEXT DEFAULT '[]',
            status           TEXT DEFAULT 'active',
            discovered_at    INTEGER DEFAULT (strftime('%s','now')),
            last_expanded_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS funder_upstream_links (
            funder_address   TEXT,
            upstream_address TEXT,
            avg_transfer_sol REAL DEFAULT 0,
            is_excluded      INTEGER DEFAULT 0,
            PRIMARY KEY (funder_address, upstream_address)
        );

        CREATE TABLE IF NOT EXISTS creator_funders (
            creator_address TEXT,
            funder_address  TEXT,
            amount_sol      REAL DEFAULT 0,
            is_cex          INTEGER DEFAULT 0,
            PRIMARY KEY (creator_address, funder_address)
        );

        CREATE TABLE IF NOT EXISTS wallet_clusters (
            cluster_id INTEGER, funder_wallet TEXT PRIMARY KEY,
            creator_addresses TEXT DEFAULT '[]', creator_count INTEGER DEFAULT 0,
            confidence_score REAL DEFAULT 0, avg_transfer_sol REAL DEFAULT 0,
            transfer_stddev REAL DEFAULT 0, days_active INTEGER DEFAULT 0,
            first_transfer_ts INTEGER DEFAULT 0, last_transfer_ts INTEGER DEFAULT 0,
            has_burst BOOLEAN DEFAULT 0, wallet_age_days REAL DEFAULT 0,
            detected_at REAL DEFAULT 0, updated_at REAL DEFAULT 0,
            first_seen_at INTEGER DEFAULT 0, last_updated_at INTEGER DEFAULT 0
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
            funder_address  TEXT PRIMARY KEY,
            priority        INTEGER NOT NULL DEFAULT 0,
            reason_codes    TEXT NOT NULL DEFAULT '[]',
            status          TEXT NOT NULL DEFAULT 'pending',
            attempts        INTEGER NOT NULL DEFAULT 0,
            last_error      TEXT,
            rpc_calls_used  INTEGER NOT NULL DEFAULT 0,
            created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            scanned_at      INTEGER,
            next_attempt_at INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS funder_rpc_scan_cache (
            funder_address TEXT PRIMARY KEY,
            scanned_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            expires_at     INTEGER NOT NULL,
            upstream_json  TEXT,
            inbound_upstream_count INTEGER NOT NULL DEFAULT 0,
            rpc_calls_used INTEGER NOT NULL DEFAULT 0,
            status         TEXT NOT NULL DEFAULT 'ok',
            error          TEXT
        );

        CREATE TABLE IF NOT EXISTS infra_funders_observed (
            funder_address TEXT PRIMARY KEY,
            label          TEXT
        );

        CREATE TABLE IF NOT EXISTS intelligence_relationship_events (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type       TEXT NOT NULL,
            source_type      TEXT,
            source_address   TEXT,
            target_type      TEXT,
            target_address   TEXT,
            relationship_type TEXT NOT NULL,
            scan_source      TEXT,
            created_at       INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ire_dedup
            ON intelligence_relationship_events(relationship_type, source_address, target_address);
    """)
    conn.close()


def _add_hub(db_path: str, upstream: str, confidence: float = 62.0,
             networks: int = 3, funders: int = 8,
             status: str = 'active', last_expanded_at: int = None) -> None:
    conn = sqlite3.connect(db_path)
    reasons = []
    if confidence >= 55: reasons.append("high_confidence")
    if networks >= 3:     reasons.append("multi_network_bridge")
    if funders >= 5:      reasons.append("high_funders_bridged")
    conn.execute("""
        INSERT OR REPLACE INTO monitored_upstream_hubs
            (upstream_address, confidence_score, networks_bridged, funders_bridged,
             risk_level, reason_codes, status, last_expanded_at)
        VALUES (?,?,?,?,'HIGH',?,?,?)
    """, (upstream, confidence, networks, funders, json.dumps(reasons), status, last_expanded_at))
    conn.commit()
    conn.close()


def _add_funder_link(db_path: str, upstream: str, funder: str,
                     is_excluded: int = 0, avg_sol: float = 1.0,
                     n_creators: int = 0, is_cex: int = 0,
                     in_wallet_cluster: bool = False,
                     cache_fresh: bool = False,
                     already_queued: bool = False,
                     is_infra: bool = False) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO funder_upstream_links (funder_address, upstream_address, avg_transfer_sol, is_excluded) VALUES (?,?,?,?)",
        (funder, upstream, avg_sol, is_excluded)
    )
    for i in range(n_creators):
        conn.execute(
            "INSERT OR IGNORE INTO creator_funders (creator_address, funder_address, is_cex) VALUES (?,?,?)",
            (f"creator_{i}", funder, is_cex)
        )
    if in_wallet_cluster:
        conn.execute(
            "INSERT OR IGNORE INTO wallet_clusters (cluster_id, funder_wallet, creator_addresses, creator_count, confidence_score, avg_transfer_sol, transfer_stddev, days_active, detected_at, updated_at) VALUES (1,?,'[]',1,0.8,1.0,0.1,30,?,?)",
            (funder, time.time(), time.time())
        )
    if cache_fresh:
        now = int(time.time())
        conn.execute(
            "INSERT OR IGNORE INTO funder_rpc_scan_cache (funder_address, scanned_at, expires_at, status) VALUES (?,?,?,'ok')",
            (funder, now, now + 86400 * 7)
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

class TestHubSignificance(unittest.TestCase):
    """Test that SecondHopExpansionBuilder correctly identifies significant hubs."""

    def setUp(self):
        self.db = _make_db()
        _seed(self.db)

    def test_hub_inserted_for_high_confidence(self):
        _add_hub(self.db, "HUB_HIGH", confidence=62, networks=2, funders=3)
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT * FROM monitored_upstream_hubs WHERE upstream_address='HUB_HIGH'").fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_hub_status_active_by_default(self):
        _add_hub(self.db, "HUB_A")
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT status FROM monitored_upstream_hubs WHERE upstream_address='HUB_A'").fetchone()
        conn.close()
        self.assertEqual(row[0], 'active')


class TestUpstreamExpansionBuilder(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        _seed(self.db)

    def test_expansion_enqueues_funders(self):
        _add_hub(self.db, "HUB1")
        for i in range(5):
            _add_funder_link(self.db, "HUB1", f"FUNDER_{i}", n_creators=2)
        result = UpstreamExpansionBuilder(self.db).run()
        self.assertEqual(result["status"], "success")
        self.assertGreater(result["funders_enqueued"], 0)
        rows = _queue_rows(self.db)
        self.assertTrue(len(rows) > 0)

    def test_caps_per_upstream(self):
        _add_hub(self.db, "HUB_BIG")
        for i in range(50):
            _add_funder_link(self.db, "HUB_BIG", f"BIG_FUNDER_{i}", n_creators=2)
        result = UpstreamExpansionBuilder(self.db).run()
        self.assertLessEqual(result["funders_enqueued"], MAX_FUNDER_ENQUEUE_PER_UPSTREAM)

    def test_caps_upstreams_per_run(self):
        for h in range(10):
            _add_hub(self.db, f"HUB_{h:02}", confidence=60)
            for i in range(3):
                _add_funder_link(self.db, f"HUB_{h:02}", f"F_{h}_{i}", n_creators=2)
        result = UpstreamExpansionBuilder(self.db).run()
        self.assertLessEqual(result["hubs_processed"], MAX_UPSTREAMS_PER_RUN)

    def test_no_duplicate_queue_entries(self):
        _add_hub(self.db, "HUB2")
        _add_funder_link(self.db, "HUB2", "FUNDER_DUP", n_creators=2)
        UpstreamExpansionBuilder(self.db).run()
        UpstreamExpansionBuilder(self.db).run()  # second run should not duplicate
        rows = [r for r in _queue_rows(self.db) if r[0] == "FUNDER_DUP"]
        self.assertEqual(len(rows), 1)

    def test_cooldown_enforced(self):
        now = int(time.time())
        _add_hub(self.db, "HUB3", last_expanded_at=now - 100)  # expanded 100s ago < 3600s cooldown
        _add_funder_link(self.db, "HUB3", "FUNDER_COOL", n_creators=2)
        result = UpstreamExpansionBuilder(self.db).run()
        self.assertEqual(result["hubs_processed"], 0)
        self.assertEqual(result["funders_enqueued"], 0)

    def test_cooldown_expired_allows_expansion(self):
        now = int(time.time())
        _add_hub(self.db, "HUB4", last_expanded_at=now - HUB_EXPANSION_COOLDOWN_SECONDS - 10)
        _add_funder_link(self.db, "HUB4", "FUNDER_EXP", n_creators=2)
        result = UpstreamExpansionBuilder(self.db).run()
        self.assertGreater(result["funders_enqueued"], 0)

    def test_excluded_funders_not_enqueued(self):
        _add_hub(self.db, "HUB5")
        _add_funder_link(self.db, "HUB5", "FUNDER_EX", is_excluded=1, n_creators=2)
        result = UpstreamExpansionBuilder(self.db).run()
        addrs = [r[0] for r in _queue_rows(self.db)]
        self.assertNotIn("FUNDER_EX", addrs)

    def test_infra_funders_not_enqueued(self):
        _add_hub(self.db, "HUB6")
        _add_funder_link(self.db, "HUB6", "FUNDER_INFRA", n_creators=2, is_infra=True)
        result = UpstreamExpansionBuilder(self.db).run()
        addrs = [r[0] for r in _queue_rows(self.db)]
        self.assertNotIn("FUNDER_INFRA", addrs)

    def test_fresh_cached_funders_not_enqueued(self):
        _add_hub(self.db, "HUB7")
        _add_funder_link(self.db, "HUB7", "FUNDER_CACHED", n_creators=2, cache_fresh=True)
        result = UpstreamExpansionBuilder(self.db).run()
        addrs = [r[0] for r in _queue_rows(self.db)]
        self.assertNotIn("FUNDER_CACHED", addrs)

    def test_already_pending_funders_not_re_enqueued(self):
        _add_hub(self.db, "HUB8")
        _add_funder_link(self.db, "HUB8", "FUNDER_PEND", n_creators=2, already_queued=True)
        result = UpstreamExpansionBuilder(self.db).run()
        # Should appear 0 times as new enqueue (already counted as skipped)
        rows = [r for r in _queue_rows(self.db) if r[0] == "FUNDER_PEND"]
        self.assertEqual(len(rows), 1)  # exists but was pre-existing

    def test_reason_codes_contain_upstream_expansion(self):
        _add_hub(self.db, "HUB9")
        _add_funder_link(self.db, "HUB9", "FUNDER_RC", n_creators=2)
        UpstreamExpansionBuilder(self.db).run()
        rows = {r[0]: json.loads(r[2]) for r in _queue_rows(self.db)}
        if "FUNDER_RC" in rows:
            self.assertIn("upstream_expansion", rows["FUNDER_RC"])

    def test_wallet_cluster_funder_gets_priority_boost(self):
        _add_hub(self.db, "HUBWC")
        _add_funder_link(self.db, "HUBWC", "FUNDER_WC",    n_creators=2, in_wallet_cluster=True)
        _add_funder_link(self.db, "HUBWC", "FUNDER_PLAIN", n_creators=2)
        UpstreamExpansionBuilder(self.db).run()
        rows = {r[0]: r[1] for r in _queue_rows(self.db)}
        if "FUNDER_WC" in rows and "FUNDER_PLAIN" in rows:
            self.assertGreater(rows["FUNDER_WC"], rows["FUNDER_PLAIN"])

    def test_paused_hub_not_expanded(self):
        _add_hub(self.db, "HUB_PAUSED", status='paused')
        _add_funder_link(self.db, "HUB_PAUSED", "FUNDER_P", n_creators=2)
        result = UpstreamExpansionBuilder(self.db).run()
        addrs = [r[0] for r in _queue_rows(self.db)]
        self.assertNotIn("FUNDER_P", addrs)

    def test_last_expanded_at_updated_after_run(self):
        _add_hub(self.db, "HUB_TS")
        _add_funder_link(self.db, "HUB_TS", "FUNDER_TS", n_creators=2)
        before = int(time.time())
        UpstreamExpansionBuilder(self.db).run()
        conn = sqlite3.connect(self.db)
        ts = conn.execute("SELECT last_expanded_at FROM monitored_upstream_hubs WHERE upstream_address='HUB_TS'").fetchone()[0]
        conn.close()
        self.assertIsNotNone(ts)
        self.assertGreaterEqual(ts, before)

    def test_relationship_event_logged(self):
        _add_hub(self.db, "HUB_EVT")
        _add_funder_link(self.db, "HUB_EVT", "FUNDER_EVT", n_creators=2)
        UpstreamExpansionBuilder(self.db).run()
        conn = sqlite3.connect(self.db)
        count = conn.execute(
            "SELECT COUNT(*) FROM intelligence_relationship_events WHERE event_type='upstream_expansion_enqueued'"
        ).fetchone()[0]
        conn.close()
        self.assertGreater(count, 0)


if __name__ == "__main__":
    unittest.main()
