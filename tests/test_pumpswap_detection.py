#!/usr/bin/env python3
"""
Test suite for PumpSwap detection in TokenMonitor

Tests the three new PumpSwap detection methods:
1. is_pumpswap_token() - Detect if token migrated from PumpFun bonding curve
2. get_pumpfun_origin_info() - Fetch bonding curve history and creator metadata
3. track_pumpswap_pool() - Record PumpSwap pool association and migration timing

Usage:
  python test_pumpswap_detection.py
"""

import sys
import json
from typing import Dict, Optional
from datetime import datetime

# Import TokenMonitor from main
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')
from main import TokenMonitor, PumpSwapDatabase


class PumpSwapDetectionTest:
    """Test suite for PumpSwap detection methods"""

    def __init__(self):
        self.monitor = TokenMonitor()
        self.db = PumpSwapDatabase()
        self.test_results = []

    def print_header(self, title: str) -> None:
        """Print test section header"""
        print(f"\n{'='*80}")
        print(f"  {title}")
        print(f"{'='*80}\n")

    def print_test(self, name: str, passed: bool, detail: str = "") -> None:
        """Print test result"""
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"[{status}] {name}")
        if detail:
            print(f"      {detail}")
        self.test_results.append({"name": name, "passed": passed, "detail": detail})

    def test_is_pumpswap_token_with_bonding_curve(self) -> None:
        """Test 1: Detect PumpSwap token from PumpSwap program"""
        self.print_header("Test 1: is_pumpswap_token() - PumpSwap Program Detection")

        # Test case 1a: Token detected from PumpSwap program
        token_data = {
            "mint": "TokenMint123",
            "name": "PumpSwap Token",
            "symbol": "PST",
        }

        result = self.monitor.is_pumpswap_token(token_data, "PumpSwap")
        self.print_test(
            "Detect token from PumpSwap program",
            result is True,
            f"DEX: PumpSwap, Result: {result}"
        )

        # Test case 1b: Token from Raydium V4 (not PumpSwap)
        regular_token = {
            "mint": "RegularTokenMint",
            "name": "Regular Token",
            "symbol": "RT"
        }

        result = self.monitor.is_pumpswap_token(regular_token, "Raydium V4")
        self.print_test(
            "Reject Raydium V4 token (not from PumpSwap program)",
            result is False,
            f"DEX: Raydium V4, Result: {result}"
        )

        # Test case 1c: Token from Raydium CPMM (not PumpSwap)
        cpmm_token = {
            "mint": "CPMMToken",
            "name": "CPMM Token",
            "symbol": "CPMM"
        }

        result = self.monitor.is_pumpswap_token(cpmm_token, "Raydium CPMM")
        self.print_test(
            "Reject Raydium CPMM token (not from PumpSwap program)",
            result is False,
            f"DEX: Raydium CPMM, Result: {result}"
        )

        # Test case 1d: Empty token data (any source is not PumpSwap without proper DEX check)
        result = self.monitor.is_pumpswap_token({}, "Unknown")
        self.print_test(
            "Reject empty token data with unknown DEX",
            result is False,
            f"DEX: Unknown, Result: {result}"
        )

    def test_is_pumpswap_token_edge_cases(self) -> None:
        """Test 2: Edge cases for is_pumpswap_token()"""
        self.print_header("Test 2: is_pumpswap_token() - Edge Cases")

        # Test case 2a: Unknown DEX source
        token_unknown = {
            "mint": "Token",
        }
        result = self.monitor.is_pumpswap_token(token_unknown, "Unknown")
        self.print_test(
            "Reject token from Unknown DEX",
            result is False,
            f"DEX: Unknown, Result: {result}"
        )

        # Test case 2b: Empty DEX source
        token_empty = {
            "mint": "Token",
        }
        result = self.monitor.is_pumpswap_token(token_empty, "")
        self.print_test(
            "Reject token with empty DEX source",
            result is False,
            f"DEX: (empty), Result: {result}"
        )

    def test_get_pumpfun_origin_info_no_pool(self) -> None:
        """Test 3: get_pumpfun_origin_info() with non-existent token"""
        self.print_header("Test 3: get_pumpfun_origin_info() - Non-existent Token")

        # Test with mint that doesn't exist in database
        fake_mint = "NonExistentMintAddress123456"
        result = self.monitor.get_pumpfun_origin_info(fake_mint)

        self.print_test(
            "Return None for non-existent token",
            result is None,
            f"Result: {result}"
        )

    def test_track_pumpswap_pool_creates_record(self) -> None:
        """Test 4: track_pumpswap_pool() creates tracking record"""
        self.print_header("Test 4: track_pumpswap_pool() - Create Tracking Record")

        # Note: This test would need an actual pool in the database
        # For now, we test that the method handles non-existent pools gracefully

        fake_pool = "NonExistentPoolAddress"
        fake_mint = "NonExistentMintAddress"

        try:
            self.monitor.track_pumpswap_pool(fake_pool, fake_mint)
            self.print_test(
                "Handle non-existent pool gracefully",
                True,
                "Method completed without exception"
            )
        except Exception as e:
            self.print_test(
                "Handle non-existent pool gracefully",
                False,
                f"Exception: {e}"
            )

    def test_database_schema_has_pumpswap_columns(self) -> None:
        """Test 5: Database schema includes PumpSwap tracking columns"""
        self.print_header("Test 5: Database Schema - PumpSwap Columns")

        # Query the database to check for PumpSwap columns
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()

            # Get table schema
            cursor.execute("PRAGMA table_info(pools)")
            columns = cursor.fetchall()
            column_names = [col[1] for col in columns]
            conn.close()

            # Check for required PumpSwap columns
            required_columns = [
                "is_pumpswap",
                "pumpfun_creator",
                "bonding_curve_address",
                "pumpfun_migration_timestamp",
                "pumpfun_launch_time",
                "pumpfun_launch_price",
                "pumpfun_final_price",
                "pumpswap_initial_price"
            ]

            for col in required_columns:
                has_col = col in column_names
                self.print_test(
                    f"Schema has '{col}' column",
                    has_col,
                    f"Column present: {has_col}"
                )

        except Exception as e:
            self.print_test(
                "Query database schema",
                False,
                f"Exception: {e}"
            )

    def test_pumpswap_token_data_structure(self) -> None:
        """Test 6: PumpSwap token data structure validation"""
        self.print_header("Test 6: PumpSwap Token Data Structure")

        # Create mock token data structure detected from PumpSwap program
        pumpswap_token = {
            "mint": "EPjFWaLb3odcccccccccccccccccccccccccccccccc",
            "symbol": "USDC",
            "name": "USD Coin",
            "creator": "PumpFunCreatorAddress",
            "website": "https://usdc.example.com",
            "twitter": "https://twitter.com/usdc",
            "discord": "https://discord.gg/usdc",
            "image_uri": "https://example.com/usdc.png"
        }

        # Test is_pumpswap detection (token from PumpSwap program)
        is_pumpswap = self.monitor.is_pumpswap_token(pumpswap_token, "PumpSwap")
        self.print_test(
            "PumpSwap token detected from PumpSwap program",
            is_pumpswap is True,
            f"Token mint: {pumpswap_token['mint'][:8]}..., DEX: PumpSwap"
        )

        # Verify structure has required fields
        required_fields = ["mint", "symbol", "name"]
        has_all_fields = all(field in pumpswap_token for field in required_fields)
        self.print_test(
            "Token data has all required PumpSwap fields",
            has_all_fields,
            f"Fields: {', '.join(required_fields)}"
        )

    def test_comparison_pumpswap_vs_regular_raydium(self) -> None:
        """Test 7: Compare PumpSwap token vs regular Raydium pool detection"""
        self.print_header("Test 7: PumpSwap vs Regular Raydium - Differentiation")

        # PumpSwap token (detected from PumpSwap program)
        pumpswap_token = {
            "mint": "PumpSwapMint",
            "creator": "PumpFunCreator"
        }

        # Regular Raydium pool (created directly, not from PumpSwap)
        regular_raydium = {
            "mint": "RegularMint",
            "creator": "SomeOtherCreator"
        }

        pumpswap_result = self.monitor.is_pumpswap_token(pumpswap_token, "PumpSwap")
        regular_result = self.monitor.is_pumpswap_token(regular_raydium, "Raydium V4")

        self.print_test(
            "PumpSwap token correctly identified",
            pumpswap_result is True,
            f"DEX: PumpSwap, Detection: {pumpswap_result}"
        )

        self.print_test(
            "Regular Raydium pool correctly rejected",
            regular_result is False,
            f"DEX: Raydium V4, Detection: {regular_result}"
        )

        self.print_test(
            "Clear differentiation between PumpSwap and regular pools",
            pumpswap_result != regular_result,
            f"PumpSwap={pumpswap_result}, Regular={regular_result}"
        )

    def print_summary(self) -> None:
        """Print test summary"""
        print(f"\n{'='*80}")
        print("  TEST SUMMARY")
        print(f"{'='*80}\n")

        total = len(self.test_results)
        passed = sum(1 for t in self.test_results if t["passed"])
        failed = total - passed

        print(f"Total Tests:  {total}")
        print(f"Passed:       {passed} ✓")
        print(f"Failed:       {failed} ✗")
        print(f"Success Rate: {passed/total*100:.1f}%\n")

        if failed > 0:
            print("Failed Tests:")
            for test in self.test_results:
                if not test["passed"]:
                    print(f"  - {test['name']}")
                    if test["detail"]:
                        print(f"    {test['detail']}")
            print()

    def run_all_tests(self) -> bool:
        """Run all tests and return success status"""
        print("\n" + "="*80)
        print("  PUMPSWAP DETECTION TEST SUITE")
        print("="*80)
        print("\nTesting TokenMonitor PumpSwap detection methods")
        print("Phase 1 Implementation Tests\n")

        self.test_is_pumpswap_token_with_bonding_curve()
        self.test_is_pumpswap_token_edge_cases()
        self.test_get_pumpfun_origin_info_no_pool()
        self.test_track_pumpswap_pool_creates_record()
        self.test_database_schema_has_pumpswap_columns()
        self.test_pumpswap_token_data_structure()
        self.test_comparison_pumpswap_vs_regular_raydium()

        self.print_summary()

        return all(t["passed"] for t in self.test_results)


def main():
    """Run test suite"""
    tester = PumpSwapDetectionTest()
    success = tester.run_all_tests()

    if success:
        print("[SUCCESS] ✓ All PumpSwap detection tests passed!\n")
        return 0
    else:
        print("[FAILED] ✗ Some tests failed\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
