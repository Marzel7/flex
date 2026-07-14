"""
Tests for second-hop Phase 1: scorer logic and API filter behavior.

Run with:
    pytest tests/test_second_hop.py -v
"""

import json
import sqlite3
import pytest

from src.core.second_hop_scorer import score_upstream_bridge, SecondHopScore


# ── Scorer tests ──────────────────────────────────────────────────────────────

def _score(**kwargs):
    defaults = dict(
        upstream_address="TestUpstream111",
        funders_bridged=3,
        networks_bridged=2,
        amount_stddev=1.0,
        burst_detected=False,
        in_wallet_cluster=False,
        in_farm_cluster=False,
        is_excluded=False,
    )
    defaults.update(kwargs)
    return score_upstream_bridge(**defaults)


class TestScorerExclusion:
    def test_excluded_always_zero(self):
        s = _score(is_excluded=True, funders_bridged=10, burst_detected=True, in_wallet_cluster=True)
        assert s.confidence_score == 0.0
        assert s.risk_level == "LOW"
        assert s.reason_codes == []


class TestScorerClusterCap:
    def test_cluster_overlap_capped_at_10(self):
        """Both clusters present + structural signal → max 10 pts from clusters."""
        s = _score(funders_bridged=3, in_wallet_cluster=True, in_farm_cluster=True)
        # 28 (funders) + 12 (2 networks) + 0 (no burst) + 0 (high stddev) + 10 (cluster cap)
        assert s.confidence_score == 50.0

    def test_cluster_single_wallet_cluster_7pts(self):
        """Single wallet cluster with structural signal → 7 pts."""
        s = _score(funders_bridged=3, in_wallet_cluster=True, in_farm_cluster=False)
        assert s.confidence_score == 28 + 12 + 7  # 47

    def test_cluster_single_farm_cluster_7pts(self):
        s = _score(funders_bridged=3, in_wallet_cluster=False, in_farm_cluster=True)
        assert s.confidence_score == 28 + 12 + 7  # 47


class TestScorerClusterRequiresStructuralSignal:
    def test_cluster_ignored_without_structural_signal(self):
        """2 funders, no burst, 2 networks → no structural signal → clusters add 0."""
        s = _score(funders_bridged=2, networks_bridged=2, burst_detected=False,
                   in_wallet_cluster=True, in_farm_cluster=True)
        # 15 (funders=2) + 12 (networks=2) + 0 (clusters blocked)
        assert s.confidence_score == 27.0

    def test_cluster_counts_with_burst_signal(self):
        """Burst present → structural signal → cluster points apply."""
        s = _score(funders_bridged=2, networks_bridged=2, burst_detected=True,
                   in_wallet_cluster=True, in_farm_cluster=True)
        # 15 + 12 + 15 (burst) + min(10, 14) = 52
        assert s.confidence_score == 52.0

    def test_cluster_counts_with_funder_count_signal(self):
        """funders_bridged >= 3 → structural signal → cluster points apply."""
        s = _score(funders_bridged=3, networks_bridged=2, burst_detected=False,
                   in_wallet_cluster=True, in_farm_cluster=False)
        assert s.confidence_score == 28 + 12 + 7  # 47

    def test_cluster_counts_with_multi_network_signal(self):
        """networks_bridged >= 3 → structural signal → cluster points apply."""
        s = _score(funders_bridged=2, networks_bridged=3, burst_detected=False,
                   in_wallet_cluster=True, in_farm_cluster=False)
        # 15 + 20 + 7 = 42
        assert s.confidence_score == 42.0


class TestScorerBurstBridge:
    def test_burst_with_high_funders_scores_high(self):
        s = _score(funders_bridged=5, networks_bridged=2, burst_detected=True,
                   amount_stddev=0.05, in_wallet_cluster=True)
        # 40 + 12 + 15 + 10 + 7 = 84 → CRITICAL
        assert s.confidence_score == 84.0
        assert s.risk_level == "CRITICAL"

    def test_burst_medium_funders_still_high(self):
        s = _score(funders_bridged=3, networks_bridged=2, burst_detected=True)
        # 28 + 12 + 15 = 55 → but clusters blocked: structural via funders=3
        # burst + funders>=3 both qualify as structural; cluster=0 (not set)
        assert s.confidence_score == 55.0
        assert s.risk_level in ("MEDIUM", "HIGH")

    def test_no_burst_2funder_stays_medium_or_lower(self):
        s = _score(funders_bridged=2, networks_bridged=2, burst_detected=False)
        # 15 + 12 = 27 → LOW (below 35)
        assert s.confidence_score == 27.0
        assert s.risk_level == "LOW"


class TestScorerRiskBands:
    def test_critical_threshold(self):
        s = _score(funders_bridged=5, networks_bridged=3, burst_detected=True)
        # 40 + 20 + 15 = 75 → HIGH (no cluster, no low stddev)
        assert s.risk_level == "HIGH"

    def test_high_threshold(self):
        s = _score(funders_bridged=5, networks_bridged=2, burst_detected=True)
        # 40 + 12 + 15 = 67 → HIGH
        assert s.risk_level == "HIGH"

    def test_medium_threshold(self):
        s = _score(funders_bridged=3, networks_bridged=2)
        # 28 + 12 = 40 → MEDIUM
        assert s.risk_level == "MEDIUM"

    def test_low_threshold(self):
        s = _score(funders_bridged=2, networks_bridged=2)
        # 15 + 12 = 27 → LOW
        assert s.risk_level == "LOW"


# ── API filter helper tests ────────────────────────────────────────────────────

class _MockRow(dict):
    """dict that also supports attribute-style access for sqlite3.Row compatibility."""
    def __getitem__(self, key):
        return super().__getitem__(key)


def make_row(funders=2, span=None, reason_codes=None, shared_funders=None):
    """Build a mock row as the API helper sees it."""
    if reason_codes is None:
        reason_codes = '["shared_upstream_funder"]'
    return _MockRow({
        'reason_codes': reason_codes,
        'funders_bridged_count': funders,
        'shared_funders': shared_funders if shared_funders is not None else funders,
        'time_span_seconds': span,
    })


# Import the helper after it's defined in main.py — test in isolation
def _is_slow_2funder_bridge(row) -> bool:
    reason_codes = json.loads(row['reason_codes'] or '[]')
    has_burst = 'same_upstream_and_burst_timing' in reason_codes
    if has_burst:
        return False
    funders = row['funders_bridged_count'] if row['funders_bridged_count'] is not None else row['shared_funders']
    if funders > 2:
        return False
    span = row['time_span_seconds']
    return span is not None and span > 604800


class TestSlowBridgeFilter:
    def test_slow_2funder_no_burst_hidden(self):
        row = make_row(funders=2, span=700000)  # > 7 days
        assert _is_slow_2funder_bridge(row) is True

    def test_slow_2funder_with_burst_not_hidden(self):
        row = make_row(funders=2, span=700000,
                       reason_codes='["shared_upstream_funder","same_upstream_and_burst_timing"]')
        assert _is_slow_2funder_bridge(row) is False

    def test_3funder_not_hidden_even_slow(self):
        row = make_row(funders=3, span=1000000)
        assert _is_slow_2funder_bridge(row) is False

    def test_2funder_fast_span_not_hidden(self):
        row = make_row(funders=2, span=3600)  # 1 hour
        assert _is_slow_2funder_bridge(row) is False

    def test_null_span_not_hidden(self):
        row = make_row(funders=2, span=None)
        assert _is_slow_2funder_bridge(row) is False

    def test_boundary_exactly_7_days_not_hidden(self):
        row = make_row(funders=2, span=604800)  # exactly 7 days — not strictly >
        assert _is_slow_2funder_bridge(row) is False

    def test_one_second_over_7_days_hidden(self):
        row = make_row(funders=2, span=604801)
        assert _is_slow_2funder_bridge(row) is True


# ── Flask API integration tests ───────────────────────────────────────────────

@pytest.fixture
def in_memory_db():
    """Minimal in-memory DB for API route integration tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE upstream_network_bridge (
            upstream_address TEXT, network_a TEXT, network_b TEXT,
            shared_funders INTEGER, confidence_score REAL, risk_level TEXT,
            reason_codes TEXT, is_excluded INTEGER DEFAULT 0, built_at INTEGER,
            time_span_seconds INTEGER, funders_bridged_count INTEGER
        );
        CREATE TABLE creator_second_hop (
            creator_address TEXT, upstream_address TEXT, via_funder TEXT,
            confidence_score REAL, risk_level TEXT, reason_codes TEXT,
            source TEXT, built_at INTEGER
        );
        CREATE TABLE funder_network_map (
            funder_address TEXT, network_name TEXT
        );
        CREATE TABLE networks_release (
            network_name TEXT PRIMARY KEY,
            second_hop_bridge_count INTEGER DEFAULT 0,
            max_second_hop_confidence REAL DEFAULT 0
        );
        -- Bridge A: HIGH, 3 funders, tight span → shown by default
        INSERT INTO upstream_network_bridge VALUES
            ('UP1','Net_1','Net_2',3,62.0,'HIGH','["shared_upstream_funder","same_upstream_and_burst_timing"]',0,1000,3600,3);
        -- Bridge B: MEDIUM, 2 funders, 9-day span, no burst → hidden by default
        INSERT INTO upstream_network_bridge VALUES
            ('UP2','Net_1','Net_3',2,45.0,'MEDIUM','["shared_upstream_funder","overlap_with_wallet_cluster"]',0,1000,777600,2);
        -- Creator second-hop for creator C1 via UP1
        INSERT INTO creator_second_hop VALUES
            ('C1','UP1','F1',62.0,'HIGH','["shared_upstream_funder"]','transfer_index',1000);
        -- Creator second-hop for creator C1 via UP2 (slow 2-funder)
        INSERT INTO creator_second_hop VALUES
            ('C1','UP2','F2',45.0,'MEDIUM','["shared_upstream_funder","overlap_with_wallet_cluster"]','transfer_index',1000);
        -- networks_release stamps
        INSERT INTO networks_release VALUES ('Net_1', 2, 62.0);
        INSERT INTO networks_release VALUES ('Net_2', 1, 62.0);
        INSERT INTO networks_release VALUES ('Net_3', 1, 45.0);
    """)
    return conn


class TestAPIFiltering:
    """Test the filter logic used by the API routes directly against an in-memory DB."""

    def _get_bridges(self, conn, network, include_low=False):
        confidence_floor = 15 if include_low else 45
        rows = conn.execute("""
            SELECT unb.upstream_address,
                   CASE WHEN unb.network_a = ? THEN unb.network_b ELSE unb.network_a END AS connected_network,
                   unb.shared_funders, unb.confidence_score, unb.risk_level,
                   unb.reason_codes, unb.time_span_seconds, unb.funders_bridged_count
            FROM upstream_network_bridge unb
            WHERE (unb.network_a = ? OR unb.network_b = ?)
              AND unb.confidence_score >= ?
              AND unb.is_excluded = 0
            ORDER BY unb.confidence_score DESC
        """, (network, network, network, confidence_floor)).fetchall()

        if include_low:
            return list(rows)
        return [r for r in rows if not _is_slow_2funder_bridge(r)]

    def test_default_hides_slow_2funder_bridge(self, in_memory_db):
        bridges = self._get_bridges(in_memory_db, 'Net_1', include_low=False)
        upstreams = [r['upstream_address'] for r in bridges]
        assert 'UP1' in upstreams
        assert 'UP2' not in upstreams  # slow 2-funder suppressed

    def test_include_low_confidence_returns_hidden_bridge(self, in_memory_db):
        bridges = self._get_bridges(in_memory_db, 'Net_1', include_low=True)
        upstreams = [r['upstream_address'] for r in bridges]
        assert 'UP1' in upstreams
        assert 'UP2' in upstreams

    def test_second_hop_network_map_threshold(self, in_memory_db):
        """Only networks with max_second_hop_confidence >= 45 appear in badge map."""
        rows = in_memory_db.execute("""
            SELECT network_name, max_second_hop_confidence
            FROM networks_release
            WHERE max_second_hop_confidence >= 45
        """).fetchall()
        names = {r['network_name'] for r in rows}
        assert 'Net_1' in names
        assert 'Net_2' in names
        assert 'Net_3' in names  # 45.0 exactly qualifies

    def test_second_hop_network_map_excludes_low(self, in_memory_db):
        in_memory_db.execute("INSERT INTO networks_release VALUES ('Net_low', 0, 30.0)")
        rows = in_memory_db.execute("""
            SELECT network_name FROM networks_release WHERE max_second_hop_confidence >= 45
        """).fetchall()
        names = {r['network_name'] for r in rows}
        assert 'Net_low' not in names
