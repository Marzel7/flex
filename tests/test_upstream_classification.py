"""
Unit tests for upstream account classification in SecondHopLiteWorker.
Uses mock getAccountInfo responses — no real RPC calls made.
"""

import sqlite3
import unittest
from unittest.mock import patch

from src.core.second_hop_lite_worker import (
    SecondHopLiteWorker,
    SYSTEM_PROGRAM,
    SPL_TOKEN_PROGRAM,
    TOKEN_2022,
)


def _make_worker(db_path=":memory:") -> SecondHopLiteWorker:
    w = SecondHopLiteWorker(db_path)
    w._rpc_calls_used = 0
    w._account_checks_used = 0
    return w


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE upstream_account_classification (
            address TEXT PRIMARY KEY,
            owner_program TEXT,
            classification TEXT NOT NULL DEFAULT 'unknown',
            checked_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE shl_excluded_upstreams (
            address TEXT PRIMARY KEY,
            reason TEXT,
            added_at INTEGER DEFAULT (strftime('%s','now'))
        );
    """)
    return conn


def _account_info_resp(owner: str) -> dict:
    return {"result": {"value": {"owner": owner, "lamports": 1000000, "data": ["", "base64"]}}}


def _account_info_null() -> dict:
    # Account does not exist on-chain
    return {"result": {"value": None}}


class TestClassifyAccount(unittest.TestCase):

    def _classify(self, worker, conn, address, rpc_response):
        with patch.object(worker, "_rpc_post", return_value=rpc_response):
            return worker._classify_account(conn, address)

    def test_system_owned_is_wallet(self):
        w, conn = _make_worker(), _make_conn()
        result = self._classify(w, conn, "WalletAAA", _account_info_resp(SYSTEM_PROGRAM))
        self.assertEqual(result, "wallet")

    def test_pumpfun_owned_is_program_account(self):
        w, conn = _make_worker(), _make_conn()
        result = self._classify(w, conn, "BondingCurveAAA",
                                _account_info_resp("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"))
        self.assertEqual(result, "program_account")

    def test_pumpswap_owned_is_program_account(self):
        w, conn = _make_worker(), _make_conn()
        result = self._classify(w, conn, "PoolAAA",
                                _account_info_resp("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"))
        self.assertEqual(result, "program_account")

    def test_spl_token_account_classified(self):
        w, conn = _make_worker(), _make_conn()
        result = self._classify(w, conn, "TokenAccAAA", _account_info_resp(SPL_TOKEN_PROGRAM))
        self.assertEqual(result, "token_account")

    def test_token_2022_account_classified(self):
        w, conn = _make_worker(), _make_conn()
        result = self._classify(w, conn, "Token22AccAAA", _account_info_resp(TOKEN_2022))
        self.assertEqual(result, "token_account")

    def test_unknown_program_owner_excluded_as_program_account(self):
        w, conn = _make_worker(), _make_conn()
        result = self._classify(w, conn, "SomeProgAccAAA",
                                _account_info_resp("SomeRandomProgramXXXXXXXXXXXXXXXXXXXXXXXXXXX"))
        self.assertEqual(result, "program_account")

    def test_nonexistent_account_treated_as_wallet(self):
        w, conn = _make_worker(), _make_conn()
        result = self._classify(w, conn, "EmptyWalletAAA", _account_info_null())
        self.assertEqual(result, "wallet")

    def test_rpc_failure_returns_unknown(self):
        w, conn = _make_worker(), _make_conn()
        with patch.object(w, "_rpc_post", return_value=None):
            result = w._classify_account(conn, "AnyAddr")
        self.assertEqual(result, "unknown")

    def test_result_cached_no_second_rpc_call(self):
        w, conn = _make_worker(), _make_conn()
        # First call — hits RPC
        with patch.object(w, "_rpc_post", return_value=_account_info_resp(SYSTEM_PROGRAM)) as mock_rpc:
            w._classify_account(conn, "WalletBBB")
            self.assertEqual(mock_rpc.call_count, 1)
        # Second call — should use cache, no RPC
        with patch.object(w, "_rpc_post", return_value=_account_info_resp(SYSTEM_PROGRAM)) as mock_rpc:
            result = w._classify_account(conn, "WalletBBB")
            self.assertEqual(mock_rpc.call_count, 0)
        self.assertEqual(result, "wallet")

    def test_budget_exhausted_returns_unknown(self):
        from src.core.second_hop_lite_worker import MAX_ACCOUNT_INFO_CHECKS_PER_RUN
        w, conn = _make_worker(), _make_conn()
        w._account_checks_used = MAX_ACCOUNT_INFO_CHECKS_PER_RUN  # budget gone
        with patch.object(w, "_rpc_post", return_value=_account_info_resp(SYSTEM_PROGRAM)) as mock_rpc:
            result = w._classify_account(conn, "NewAddr")
            self.assertEqual(mock_rpc.call_count, 0)  # no RPC call made
        self.assertEqual(result, "unknown")

    def test_program_account_auto_added_to_blocklist(self):
        w, conn = _make_worker(), _make_conn()
        with patch.object(w, "_rpc_post",
                          return_value=_account_info_resp("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")):
            w._classify_account(conn, "BondingCurveBBB")
        row = conn.execute(
            "SELECT reason FROM shl_excluded_upstreams WHERE address='BondingCurveBBB'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(row[0].startswith("auto:"))


if __name__ == "__main__":
    unittest.main()
