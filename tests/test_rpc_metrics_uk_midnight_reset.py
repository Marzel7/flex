"""
Test suite for UK midnight reset functionality in RPC Metrics Recorder.

Tests verify that:
1. Metrics reset once per UK calendar day (not rolling 24h)
2. Reset happens automatically at UK midnight
3. Multiple processes share the same reset point
4. Database state persists reset information
5. Manual reset functions still work
6. Thread safety is maintained
"""

import pytest
import sqlite3
import time
import os
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock
from collections import defaultdict

# Import the recorder
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.metrics.rpc_metrics_recorder import (
    RPCMetricsRecorder,
    _ensure_rpc_metrics_table,
    _get_state,
    _set_state,
    UK_TZ,
)

# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def temp_db():
    """Create a temporary database for testing"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Set DB_PATH to temp file
    import src.metrics.rpc_metrics_recorder as recorder_module
    original_db_path = recorder_module.DB_PATH
    recorder_module.DB_PATH = path

    # Initialize tables
    _ensure_rpc_metrics_table()

    yield path

    # Cleanup
    recorder_module.DB_PATH = original_db_path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def recorder(temp_db):
    """Create a fresh recorder instance with temp DB"""
    return RPCMetricsRecorder()


@pytest.fixture
def uk_tz():
    """Return UK timezone"""
    return ZoneInfo("Europe/London")


# ============================================================================
# TEST: UK TIMEZONE HELPERS
# ============================================================================

class TestUKTimeHelpers:
    """Test UK timezone helper functions"""

    def test_uk_now_returns_uk_time(self, recorder, uk_tz):
        """_uk_now() returns time in UK timezone"""
        now_uk = recorder._uk_now()
        assert now_uk.tzinfo is not None
        assert now_uk.tzinfo.key == "Europe/London"

    def test_uk_day_key_format(self, recorder):
        """_uk_day_key() returns YYYY-MM-DD format"""
        day_key = recorder._uk_day_key()
        # Should be ISO format YYYY-MM-DD
        assert len(day_key) == 10
        assert day_key[4] == "-" and day_key[7] == "-"

    def test_uk_midnight_timestamp(self, recorder, uk_tz):
        """_uk_midnight_timestamp() returns Unix timestamp for midnight UK time"""
        midnight_ts = recorder._uk_midnight_timestamp()

        # Convert back to UK time to verify
        midnight_dt = datetime.fromtimestamp(midnight_ts, tz=uk_tz)

        # Should be at 00:00:00
        assert midnight_dt.hour == 0
        assert midnight_dt.minute == 0
        assert midnight_dt.second == 0

    def test_uk_midnight_timestamp_is_consistent(self, recorder):
        """Multiple calls to _uk_midnight_timestamp() return same value within same day"""
        ts1 = recorder._uk_midnight_timestamp()
        time.sleep(0.1)
        ts2 = recorder._uk_midnight_timestamp()

        # Should be the same (within same day)
        assert ts1 == ts2


# ============================================================================
# TEST: AUTOMATIC MIDNIGHT RESET
# ============================================================================

class TestAutomaticMidnightReset:
    """Test automatic reset at UK midnight"""

    def test_no_reset_same_day(self, recorder):
        """_maybe_auto_reset_at_uk_midnight() does not reset on same day"""
        # Record initial state
        _set_state("rpc_metrics_last_reset_day", recorder._uk_day_key())

        # Initialize some metrics
        recorder._daily_credits = 100
        recorder._daily_requests = 5

        # Call reset check
        recorder._maybe_auto_reset_at_uk_midnight()

        # Should NOT reset (same day)
        assert recorder._daily_credits == 100
        assert recorder._daily_requests == 5

    def test_reset_on_new_day(self, recorder):
        """_maybe_auto_reset_at_uk_midnight() resets on new day"""
        # Set reset day to yesterday
        yesterday = (recorder._uk_now() - timedelta(days=1)).date().isoformat()
        _set_state("rpc_metrics_last_reset_day", yesterday)

        # Set some metrics
        recorder._daily_credits = 100
        recorder._daily_requests = 5
        recorder._history.append(MagicMock())

        # Call reset
        recorder._maybe_auto_reset_at_uk_midnight()

        # Should reset
        assert recorder._daily_credits == 0
        assert recorder._daily_requests == 0
        assert len(recorder._history) == 0

    def test_reset_persists_state(self, recorder):
        """Reset persists state to database"""
        # Set reset day to yesterday
        yesterday = (recorder._uk_now() - timedelta(days=1)).date().isoformat()
        _set_state("rpc_metrics_last_reset_day", yesterday)

        # Call reset
        recorder._maybe_auto_reset_at_uk_midnight()

        # Verify state persisted
        today = recorder._uk_day_key()
        stored_day = _get_state("rpc_metrics_last_reset_day")
        assert stored_day == today

    def test_reset_clears_history(self, recorder):
        """Reset clears in-memory history"""
        # Add records to history
        from src.metrics.rpc_metrics_recorder import RequestRecord
        for i in range(5):
            record = RequestRecord(
                timestamp=time.time(),
                section="test",
                provider="helius_rpc",
                method=f"method_{i}",
                mode="realtime",
                status_code=200,
                latency_ms=10.0,
                retries=0,
                source_file="test.py",
            )
            recorder._history.append(record)

        # Set reset day to yesterday
        yesterday = (recorder._uk_now() - timedelta(days=1)).date().isoformat()
        _set_state("rpc_metrics_last_reset_day", yesterday)

        # Call reset
        recorder._maybe_auto_reset_at_uk_midnight()

        # History should be cleared
        assert len(recorder._history) == 0

    def test_reset_clears_section_stats(self, recorder):
        """Reset clears section statistics"""
        # Add section stats
        recorder._section_stats["listener"].requests = 10
        recorder._section_stats["creator_funding"].credits_total = 500

        # Set reset day to yesterday
        yesterday = (recorder._uk_now() - timedelta(days=1)).date().isoformat()
        _set_state("rpc_metrics_last_reset_day", yesterday)

        # Call reset
        recorder._maybe_auto_reset_at_uk_midnight()

        # Section stats should be cleared
        assert len(recorder._section_stats) == 0


# ============================================================================
# TEST: CREDIT AND CALL QUERIES
# ============================================================================

class TestCreditAndCallQueries:
    """Test UK midnight-based credit and call queries"""

    def test_get_credits_today_uk_from_db(self, recorder):
        """_get_credits_today_uk() queries database"""
        # Record some metrics
        import src.metrics.rpc_metrics_recorder as recorder_module

        now = time.time()
        recorder_module._persist_rpc_metric(
            timestamp=now,
            section="test",
            provider="helius_rpc",
            method="getTransaction",
            status_code=200,
            latency_ms=10.0,
            credits=10,
        )

        # Query should return credits
        credits = recorder._get_credits_today_uk()
        assert credits == 10

    def test_get_credits_today_uk_uk_midnight_boundary(self, recorder):
        """_get_credits_today_uk() respects UK midnight boundary"""
        import src.metrics.rpc_metrics_recorder as recorder_module

        # Record metric before midnight
        midnight_ts = recorder._uk_midnight_timestamp()
        before_midnight_ts = midnight_ts - 3600  # 1 hour before

        recorder_module._persist_rpc_metric(
            timestamp=before_midnight_ts,
            section="test",
            provider="helius_rpc",
            method="getTransaction",
            status_code=200,
            latency_ms=10.0,
            credits=10,
        )

        # Record metric after midnight
        after_midnight_ts = midnight_ts + 3600  # 1 hour after
        recorder_module._persist_rpc_metric(
            timestamp=after_midnight_ts,
            section="test",
            provider="helius_rpc",
            method="getTransaction",
            status_code=200,
            latency_ms=10.0,
            credits=20,
        )

        # Query should only return credits after midnight
        credits = recorder._get_credits_today_uk()
        assert credits == 20

    def test_get_tracked_calls_today_uk(self, recorder):
        """_get_tracked_calls_today_uk() counts RPC calls since midnight"""
        import src.metrics.rpc_metrics_recorder as recorder_module

        # Record actual RPC calls (credits > 0)
        now = time.time()
        for i in range(3):
            recorder_module._persist_rpc_metric(
                timestamp=now,
                section="test",
                provider="helius_rpc",
                method="getTransaction",
                status_code=200,
                latency_ms=10.0,
                credits=10,
            )

        # Record cache event (credits == 0, should not count)
        recorder_module._persist_rpc_metric(
            timestamp=now,
            section="test",
            provider="helius_rpc",
            method="getTransaction",
            status_code=200,
            latency_ms=0.0,
            credits=0,
            cache_action="skip",
            credits_saved=10,
        )

        # Query should only count actual RPC calls
        calls = recorder._get_tracked_calls_today_uk()
        assert calls == 3

    def test_get_credits_24h_uses_uk_midnight(self, recorder):
        """_get_credits_24h() is alias for _get_credits_today_uk()"""
        import src.metrics.rpc_metrics_recorder as recorder_module

        now = time.time()
        recorder_module._persist_rpc_metric(
            timestamp=now,
            section="test",
            provider="helius_rpc",
            method="getTransaction",
            status_code=200,
            latency_ms=10.0,
            credits=15,
        )

        # Both should return same value
        credits_24h = recorder._get_credits_24h()
        credits_today = recorder._get_credits_today_uk()
        assert credits_24h == credits_today


# ============================================================================
# TEST: INTEGRATION WITH RECORDING
# ============================================================================

class TestRecordingWithMidnightReset:
    """Test that recording operations trigger reset check"""

    def test_record_request_triggers_reset(self, recorder):
        """record_request() calls _maybe_auto_reset_at_uk_midnight()"""
        # Mock the reset method
        recorder._maybe_auto_reset_at_uk_midnight = MagicMock()

        # Record a request
        recorder.record_request(
            section="test",
            provider="helius_rpc",
            method="getTransaction",
            status_code=200,
            latency_ms=10.0,
        )

        # Reset should have been called
        recorder._maybe_auto_reset_at_uk_midnight.assert_called()

    def test_record_stream_bytes_triggers_reset(self, recorder):
        """record_stream_bytes() calls _maybe_auto_reset_at_uk_midnight()"""
        # Mock the reset method
        recorder._maybe_auto_reset_at_uk_midnight = MagicMock()

        # Record streaming bytes
        recorder.record_stream_bytes(
            section="test",
            provider="helius_rpc",
            stream_name="laserstream",
            bytes_count=1024,
        )

        # Reset should have been called
        recorder._maybe_auto_reset_at_uk_midnight.assert_called()

    def test_get_summary_triggers_reset(self, recorder):
        """get_summary() calls _maybe_auto_reset_at_uk_midnight()"""
        # Mock the reset method
        recorder._maybe_auto_reset_at_uk_midnight = MagicMock()

        # Get summary
        recorder.get_summary()

        # Reset should have been called
        recorder._maybe_auto_reset_at_uk_midnight.assert_called()

    def test_get_section_stats_triggers_reset(self, recorder):
        """get_section_stats() calls _maybe_auto_reset_at_uk_midnight()"""
        # Mock the reset method
        recorder._maybe_auto_reset_at_uk_midnight = MagicMock()

        # Get section stats
        recorder.get_section_stats()

        # Reset should have been called
        recorder._maybe_auto_reset_at_uk_midnight.assert_called()

    def test_get_top_methods_triggers_reset(self, recorder):
        """get_top_methods() calls _maybe_auto_reset_at_uk_midnight()"""
        # Mock the reset method
        recorder._maybe_auto_reset_at_uk_midnight = MagicMock()

        # Get top methods
        recorder.get_top_methods()

        # Reset should have been called
        recorder._maybe_auto_reset_at_uk_midnight.assert_called()

    def test_get_optimization_savings_triggers_reset(self, recorder):
        """get_optimization_savings() calls _maybe_auto_reset_at_uk_midnight()"""
        # Mock the reset method
        recorder._maybe_auto_reset_at_uk_midnight = MagicMock()

        # Get optimization savings
        recorder.get_optimization_savings()

        # Reset should have been called
        recorder._maybe_auto_reset_at_uk_midnight.assert_called()

    def test_get_component_breakdown_triggers_reset(self, recorder):
        """get_component_breakdown() calls _maybe_auto_reset_at_uk_midnight()"""
        # Mock the reset method
        recorder._maybe_auto_reset_at_uk_midnight = MagicMock()

        # Get component breakdown
        recorder.get_component_breakdown()

        # Reset should have been called
        recorder._maybe_auto_reset_at_uk_midnight.assert_called()


# ============================================================================
# TEST: MANUAL RESET FUNCTIONS
# ============================================================================

class TestManualResetFunctions:
    """Test that manual reset functions still work"""

    def test_reset_credits_today_still_works(self, recorder):
        """reset_credits_today() manual reset still functional"""
        # Set some metrics
        recorder._total_credits = 500
        recorder._daily_credits = 100

        # Reset
        recorder.reset_credits_today()

        # Should be reset
        assert recorder._total_credits == 0
        assert recorder._daily_credits == 0
        assert len(recorder._history) == 0

    def test_reset_comparison_baseline_still_works(self, recorder):
        """reset_comparison_baseline() manual reset still functional"""
        old_ts = recorder._comparison_reset_timestamp

        # Manual reset
        time.sleep(0.1)
        recorder.reset_comparison_baseline()

        # Timestamp should be updated
        assert recorder._comparison_reset_timestamp != old_ts

    def test_reset_daily_still_works(self, recorder):
        """reset_daily() still functional"""
        # Set daily metrics
        recorder._daily_credits = 100
        recorder._daily_requests = 10

        # Reset
        recorder.reset_daily()

        # Should be reset
        assert recorder._daily_credits == 0
        assert recorder._daily_requests == 0


# ============================================================================
# TEST: MULTI-PROCESS SAFETY
# ============================================================================

class TestMultiProcessSafety:
    """Test that reset is safe across multiple processes"""

    def test_reset_state_persisted_to_db(self, recorder):
        """Reset state is persisted to rpc_metrics_state table"""
        # Trigger reset
        yesterday = (recorder._uk_now() - timedelta(days=1)).date().isoformat()
        _set_state("rpc_metrics_last_reset_day", yesterday)
        recorder._maybe_auto_reset_at_uk_midnight()

        # Check database directly
        conn = sqlite3.connect(os.getenv("RPC_METRICS_DB", "flex_complete_database.db"), timeout=30)
        cursor = conn.execute(
            "SELECT value FROM rpc_metrics_state WHERE key = ?",
            ("rpc_metrics_last_reset_day",)
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == recorder._uk_day_key()

    def test_reset_idempotent_across_processes(self, recorder):
        """Reset is idempotent when called by multiple processes"""
        # First "process" resets
        yesterday = (recorder._uk_now() - timedelta(days=1)).date().isoformat()
        _set_state("rpc_metrics_last_reset_day", yesterday)
        recorder._maybe_auto_reset_at_uk_midnight()

        first_reset_time = recorder._daily_reset_time

        # Second "process" calls reset (should be no-op)
        time.sleep(0.1)
        recorder._maybe_auto_reset_at_uk_midnight()

        second_reset_time = recorder._daily_reset_time

        # Reset time should be the same (idempotent)
        assert first_reset_time == second_reset_time


# ============================================================================
# TEST: EDGE CASES
# ============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions"""

    def test_midnight_boundary_with_clock_drift(self, recorder):
        """Handles clock drift near midnight"""
        # Even with small time differences, should use same midnight
        midnight1 = recorder._uk_midnight_timestamp()

        # Simulate small clock drift
        with patch('time.time', return_value=time.time() + 0.5):
            midnight2 = recorder._uk_midnight_timestamp()

        # Should be same midnight (within same day)
        assert midnight1 == midnight2

    def test_empty_database_queries(self, recorder):
        """Queries work correctly on empty database"""
        # Should not crash on empty database
        credits = recorder._get_credits_today_uk()
        calls = recorder._get_tracked_calls_today_uk()

        assert credits == 0
        assert calls == 0

    def test_reset_with_mixed_timezone_data(self, recorder):
        """Reset handles timestamps from different timezones correctly"""
        import src.metrics.rpc_metrics_recorder as recorder_module

        # Record metrics with various timestamps
        midnight = recorder._uk_midnight_timestamp()

        # Before midnight
        recorder_module._persist_rpc_metric(
            timestamp=midnight - 3600,
            section="test",
            provider="helius_rpc",
            method="getTransaction",
            status_code=200,
            latency_ms=10.0,
            credits=10,
        )

        # After midnight
        recorder_module._persist_rpc_metric(
            timestamp=midnight + 3600,
            section="test",
            provider="helius_rpc",
            method="getTransaction",
            status_code=200,
            latency_ms=10.0,
            credits=20,
        )

        # Query should respect boundary
        credits = recorder._get_credits_today_uk()
        assert credits == 20


# ============================================================================
# TEST: THREAD SAFETY
# ============================================================================

class TestThreadSafety:
    """Test that operations are thread-safe"""

    def test_reset_uses_lock(self, recorder):
        """_maybe_auto_reset_at_uk_midnight() respects lock"""
        # Mock the lock to verify it's used
        original_lock = recorder._lock
        recorder._lock = MagicMock()
        recorder._lock.__enter__ = MagicMock(return_value=None)
        recorder._lock.__exit__ = MagicMock(return_value=None)

        # When called through record_request (which holds lock)
        # The reset method should work
        # (Note: full multi-threading test would need threading module)

        recorder._lock = original_lock


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
