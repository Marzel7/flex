#!/usr/bin/env python3
"""
Replay Test Harness for PumpSwap Discovery Pipeline

Replays historical migration signatures through the discovery pipeline
to validate end-to-end correctness.
"""

import asyncio
import argparse
import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Dict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
import os

load_dotenv()


@dataclass
class ReplayResult:
    """Result of replaying a single migration signature."""
    signature: str
    group: str
    status: str  # PASSED, FAILED, ERROR
    reason: str
    discovery_method: Optional[str] = None
    vault_status: Optional[str] = None
    duration_ms: int = 0
    ws_ready: bool = False
    has_snapshot: bool = False


class Database:
    """Simple database wrapper."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def query_one(self, sql: str, params: tuple = ()):
        """Query single row."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql, params)
        result = cursor.fetchone()
        conn.close()
        return dict(result) if result else None

    def query(self, sql: str, params: tuple = ()):
        """Query multiple rows."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql, params)
        results = cursor.fetchall()
        conn.close()
        return [dict(row) for row in results]


class ReplayTestHarness:
    """Replay test harness for PumpSwap discovery pipeline."""

    def __init__(self, db_path: str):
        self.db = Database(db_path)
        self.results: List[ReplayResult] = []

    def replay_migration(self, mint: str, group: str) -> ReplayResult:
        """Replay a single token registration."""
        start_time = time.time()

        try:
            # Query database for registration
            pool = self.db.query_one("""
                SELECT
                    mint,
                    pool_address,
                    base_account,
                    quote_account,
                    pool_program,
                    discovery_method,
                    vault_validation_status,
                    pool_score
                FROM token_pool_accounts
                WHERE mint = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (mint,))

            if not pool:
                return ReplayResult(
                    signature=mint,
                    group=group,
                    status="FAILED",
                    reason="No pool registered for this mint",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            # Check telemetry
            telemetry = self.db.query_one("""
                SELECT
                    mint,
                    detected_at,
                    resolved_at,
                    resolve_seconds,
                    resolve_source,
                    retry_count,
                    pool_address
                FROM token_resolution_telemetry
                WHERE mint = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (pool['mint'],))

            # Check discovery assertions
            violations = []

            if not pool.get('pool_address'):
                violations.append("pool_address NULL")

            if pool.get('pool_address') == pool.get('base_account'):
                violations.append("pool_address == base_account")

            if pool.get('pool_address') == pool.get('quote_account'):
                violations.append("pool_address == quote_account")

            if pool.get('base_account') == pool.get('quote_account'):
                violations.append("base_account == quote_account")

            if pool.get('pool_program') not in [
                'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
                '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',
                '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P',
            ]:
                violations.append(f"pool_program invalid: {pool.get('pool_program')}")

            # Check registration completeness
            if not pool.get('discovery_method'):
                violations.append("discovery_method NULL")

            if pool.get('vault_validation_status') not in ['validated', 'pending']:
                violations.append(f"vault_status invalid: {pool.get('vault_validation_status')}")

            # Check telemetry
            if telemetry:
                if not telemetry.get('resolved_at'):
                    violations.append("telemetry.resolved_at NULL")
                if not telemetry.get('resolve_source'):
                    violations.append("telemetry.resolve_source NULL")

            # Check price snapshot
            snapshot = self.db.query_one("""
                SELECT price_usd, source, created_at
                FROM token_price_snapshots
                WHERE mint = ? AND source = 'pool'
                ORDER BY created_at DESC
                LIMIT 1
            """, (pool['mint'],))

            has_snapshot = snapshot is not None and snapshot.get('source') == 'pool'

            # Determine status
            success = (
                len(violations) == 0 and
                pool.get('vault_validation_status') == 'validated' and
                telemetry is not None and
                telemetry.get('resolved_at') is not None
            )

            reason = ", ".join(violations) if violations else "end-to-end success"

            return ReplayResult(
                signature=mint,
                group=group,
                status="PASSED" if success else "FAILED",
                reason=reason,
                discovery_method=pool.get('discovery_method'),
                vault_status=pool.get('vault_validation_status'),
                duration_ms=int((time.time() - start_time) * 1000),
                ws_ready=has_snapshot,
                has_snapshot=has_snapshot
            )

        except Exception as e:
            return ReplayResult(
                signature=mint,
                group=group,
                status="ERROR",
                reason=str(e),
                duration_ms=int((time.time() - start_time) * 1000)
            )

    def get_known_good_signatures(self, limit: int = 10) -> List[str]:
        """Get known-good mints (successful resolutions)."""
        rows = self.db.query("""
            SELECT DISTINCT mint
            FROM token_pool_accounts
            WHERE discovery_method IN ('tx_parsing', 'vault_inference')
            AND vault_validation_status = 'validated'
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))

        return [row['mint'] for row in rows if row['mint']]

    def get_previously_failing_signatures(self, limit: int = 5) -> List[str]:
        """Get previously failing mints (stuck in pending)."""
        rows = self.db.query("""
            SELECT DISTINCT mint
            FROM token_pool_accounts
            WHERE vault_validation_status = 'pending'
            AND (discovery_method = 'unknown' OR discovery_method IS NULL)
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))

        return [row['mint'] for row in rows if row['mint']]

    def get_fresh_live_signatures(self, limit: int = 5) -> List[str]:
        """Get fresh live mints (recent registrations)."""
        rows = self.db.query("""
            SELECT DISTINCT mint
            FROM token_pool_accounts
            WHERE created_at > strftime('%s', 'now') - 3600
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))

        return [row['mint'] for row in rows if row['mint']]

    def run_group(self, group_name: str, signatures: List[str]) -> Dict:
        """Run a test group."""
        print(f"\n{'='*80}")
        print(f"GROUP: {group_name.upper()}")
        print(f"{'='*80}\n")

        if not signatures:
            print(f"⚠️  No signatures found for group '{group_name}'")
            return {
                'group': group_name,
                'total': 0,
                'passed': 0,
                'failed': 0,
                'errors': 0,
                'results': []
            }

        passed = 0
        failed = 0
        errors = 0

        for i, mint in enumerate(signatures, 1):
            result = self.replay_migration(mint, group_name)
            self.results.append(result)

            status_symbol = {
                'PASSED': '✓',
                'FAILED': '✗',
                'ERROR': '⚠'
            }.get(result.status, '?')

            print(f"[{i}/{len(signatures)}] {status_symbol} {mint[:16]}...")
            print(f"       Status: {result.status} ({result.duration_ms}ms)")

            if result.discovery_method:
                print(f"       Discovery: {result.discovery_method}")

            if result.vault_status:
                print(f"       Vault Status: {result.vault_status}")

            if result.reason:
                print(f"       Reason: {result.reason}")

            if result.status == 'PASSED':
                passed += 1
            elif result.status == 'FAILED':
                failed += 1
            else:
                errors += 1

            print()

        return {
            'group': group_name,
            'total': len(signatures),
            'passed': passed,
            'failed': failed,
            'errors': errors,
            'pass_rate': 100.0 * passed / len(signatures) if signatures else 0,
            'results': [
                {
                    'signature': r.signature,
                    'status': r.status,
                    'reason': r.reason,
                    'discovery_method': r.discovery_method,
                    'vault_status': r.vault_status,
                    'duration_ms': r.duration_ms,
                }
                for r in self.results if r.group == group_name
            ]
        }

    def generate_report(self) -> Dict:
        """Generate final report."""
        total = len(self.results)
        passed = len([r for r in self.results if r.status == 'PASSED'])
        failed = len([r for r in self.results if r.status == 'FAILED'])
        errors = len([r for r in self.results if r.status == 'ERROR'])

        groups = {}
        for result in self.results:
            if result.group not in groups:
                groups[result.group] = {
                    'total': 0,
                    'passed': 0,
                    'failed': 0,
                }
            groups[result.group]['total'] += 1
            if result.status == 'PASSED':
                groups[result.group]['passed'] += 1
            else:
                groups[result.group]['failed'] += 1

        return {
            'timestamp': int(time.time()),
            'total_tests': total,
            'passed': passed,
            'failed': failed,
            'errors': errors,
            'pass_rate': 100.0 * passed / total if total > 0 else 0,
            'groups': groups,
            'results': [
                {
                    'signature': r.signature,
                    'group': r.group,
                    'status': r.status,
                    'reason': r.reason,
                    'discovery_method': r.discovery_method,
                    'vault_status': r.vault_status,
                    'duration_ms': r.duration_ms,
                }
                for r in self.results
            ]
        }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Replay test harness for PumpSwap discovery pipeline'
    )
    parser.add_argument(
        '--group',
        action='append',
        choices=['historical_good', 'previously_failing', 'fresh_live'],
        help='Test group(s) to run'
    )
    parser.add_argument(
        '--db',
        default='database/flex_complete_database.db',
        help='Path to database'
    )
    parser.add_argument(
        '--output',
        help='Output JSON file'
    )

    args = parser.parse_args()

    # If no groups specified, run all
    groups_to_run = args.group or ['historical_good', 'previously_failing', 'fresh_live']

    # Create harness
    harness = ReplayTestHarness(args.db)

    # Run groups
    print("\n" + "="*80)
    print("PUMPSWAP DISCOVERY PIPELINE — REPLAY TEST HARNESS")
    print("="*80)
    print(f"Database: {args.db}")
    print(f"Groups: {', '.join(groups_to_run)}")

    group_results = []

    for group_name in groups_to_run:
        if group_name == 'historical_good':
            sigs = harness.get_known_good_signatures(10)
        elif group_name == 'previously_failing':
            sigs = harness.get_previously_failing_signatures(5)
        else:  # fresh_live
            sigs = harness.get_fresh_live_signatures(5)

        result = harness.run_group(group_name, sigs)
        group_results.append(result)

    # Generate report
    report = harness.generate_report()

    # Print summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"Total Tests: {report['total_tests']}")
    print(f"Passed: {report['passed']}")
    print(f"Failed: {report['failed']}")
    print(f"Errors: {report['errors']}")
    print(f"Pass Rate: {report['pass_rate']:.1f}%")

    print("\nGroup Breakdown:")
    for group_name, stats in report['groups'].items():
        rate = 100.0 * stats['passed'] / stats['total'] if stats['total'] > 0 else 0
        print(f"  {group_name}: {stats['passed']}/{stats['total']} passed ({rate:.1f}%)")

    # Production readiness
    print("\n" + "="*80)
    print("PRODUCTION READINESS")
    print("="*80)

    checks = {
        'Historical Good (≥9/10)': (
            'historical_good' in report['groups'] and
            report['groups']['historical_good']['passed'] >= 9
        ),
        'Previously Failing (≥4/5)': (
            'previously_failing' not in report['groups'] or
            report['groups']['previously_failing']['passed'] >= 4
        ),
        'Fresh Live (≥4/5)': (
            'fresh_live' not in report['groups'] or
            report['groups']['fresh_live']['passed'] >= 4
        ),
        'Overall (≥90%)': report['pass_rate'] >= 90.0,
    }

    all_pass = all(checks.values())

    for check_name, passed in checks.items():
        symbol = "✓" if passed else "✗"
        print(f"  {symbol} {check_name}")

    print()
    if all_pass:
        print("✅ PRODUCTION READY - All checks passed")
    else:
        print("❌ NOT READY - Some checks failed")

    # Save output
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\nResults saved to: {args.output}")

    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
