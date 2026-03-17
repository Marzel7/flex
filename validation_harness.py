#!/usr/bin/env python3
"""
Validation Harness for PumpSwap Discovery Pipeline

Validates discovery correctness, vault integrity, registration completeness,
and telemetry accuracy.
"""

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from typing import List, Dict, Optional
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv

load_dotenv()


@dataclass
class ValidationError:
    """A validation error."""
    field: str
    mint: str
    message: str


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


class DiscoveryValidator:
    """Validates discovery assertions."""

    @staticmethod
    def validate_all(db: Database) -> Dict:
        """Validate all pools."""
        errors = []
        violations_by_type = {}

        # Get all pools
        pools = db.query("""
            SELECT
                mint,
                pool_address,
                base_account,
                quote_account,
                pool_program,
                discovery_method,
                vault_validation_status
            FROM token_pool_accounts
            ORDER BY created_at DESC
        """)

        for pool in pools:
            mint = pool['mint']

            # Check 1: pool_address exists
            if not pool.get('pool_address'):
                violation = "pool_address NULL"
                errors.append(ValidationError('pool_address', mint, violation))
                violations_by_type[violation] = violations_by_type.get(violation, 0) + 1

            # Check 2: pool_address != base_account
            if pool.get('pool_address') == pool.get('base_account'):
                violation = "pool_address == base_account"
                errors.append(ValidationError('pool_address', mint, violation))
                violations_by_type[violation] = violations_by_type.get(violation, 0) + 1

            # Check 3: pool_address != quote_account
            if pool.get('pool_address') == pool.get('quote_account'):
                violation = "pool_address == quote_account"
                errors.append(ValidationError('pool_address', mint, violation))
                violations_by_type[violation] = violations_by_type.get(violation, 0) + 1

            # Check 4: base_account != quote_account
            if pool.get('base_account') == pool.get('quote_account'):
                violation = "base_account == quote_account"
                errors.append(ValidationError('base_account', mint, violation))
                violations_by_type[violation] = violations_by_type.get(violation, 0) + 1

            # Check 5: pool_program is valid
            if pool.get('pool_program') not in [
                'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
                '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',
                '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P',
            ]:
                violation = f"pool_program invalid: {pool.get('pool_program')}"
                errors.append(ValidationError('pool_program', mint, violation))
                violations_by_type[violation] = violations_by_type.get(violation, 0) + 1

            # Check 6: discovery_method is recorded
            if not pool.get('discovery_method') or pool.get('discovery_method') == 'unknown':
                violation = "discovery_method unknown or NULL"
                errors.append(ValidationError('discovery_method', mint, violation))
                violations_by_type[violation] = violations_by_type.get(violation, 0) + 1

        return {
            'total_pools': len(pools),
            'errors': [
                {
                    'mint': e.mint,
                    'field': e.field,
                    'message': e.message,
                }
                for e in errors
            ],
            'violations_by_type': violations_by_type,
            'passed': len(errors) == 0,
        }


class VaultValidator:
    """Validates vault integrity."""

    @staticmethod
    def validate_all(db: Database) -> Dict:
        """Validate all vaults."""
        issues = []

        # Check 1: Vaults that are zero addresses
        zero_vaults = db.query("""
            SELECT mint, base_account, quote_account
            FROM token_pool_accounts
            WHERE base_account = '11111111111111111111111111111111'
               OR quote_account = '11111111111111111111111111111111'
        """)

        for vault in zero_vaults:
            if vault['base_account'] == '11111111111111111111111111111111':
                issues.append({
                    'mint': vault['mint'],
                    'type': 'zero_address',
                    'field': 'base_account',
                    'value': vault['base_account'],
                })
            if vault['quote_account'] == '11111111111111111111111111111111':
                issues.append({
                    'mint': vault['mint'],
                    'type': 'zero_address',
                    'field': 'quote_account',
                    'value': vault['quote_account'],
                })

        # Check 2: Distribution by validation status
        status_dist = db.query("""
            SELECT vault_validation_status, COUNT(*) as count
            FROM token_pool_accounts
            GROUP BY vault_validation_status
        """)

        status_breakdown = {row['vault_validation_status']: row['count'] for row in status_dist}

        validated_count = status_breakdown.get('validated', 0)
        total_count = sum(status_breakdown.values())
        validation_rate = 100.0 * validated_count / total_count if total_count > 0 else 0

        return {
            'total_vaults': total_count,
            'validated': validated_count,
            'pending': status_breakdown.get('pending', 0),
            'validation_rate_pct': validation_rate,
            'zero_address_issues': len(issues),
            'issues': issues,
            'passed': len(issues) == 0 and validation_rate >= 95,
        }


class RegistrationValidator:
    """Validates registration completeness."""

    @staticmethod
    def validate_all(db: Database) -> Dict:
        """Validate registration completeness."""
        completeness = db.query_one("""
            SELECT
                COUNT(*) as total,
                COUNT(pool_address) as has_pool_address,
                COUNT(base_account) as has_base_account,
                COUNT(quote_account) as has_quote_account,
                COUNT(pool_program) as has_pool_program,
                COUNT(discovery_method) as has_discovery_method,
                COUNT(vault_validation_status) as has_vault_status,
                COUNT(pool_score) as has_pool_score,
                ROUND(100.0 * COUNT(pool_address) / COUNT(*), 1) as pool_address_pct,
                ROUND(100.0 * COUNT(base_account) / COUNT(*), 1) as base_account_pct,
                ROUND(100.0 * COUNT(quote_account) / COUNT(*), 1) as quote_account_pct,
                ROUND(100.0 * COUNT(CASE WHEN discovery_method NOT IN ('unknown', NULL)
                    THEN 1 END) / COUNT(*), 1) as known_discovery_pct
            FROM token_pool_accounts
        """)

        # Minimum thresholds
        thresholds = {
            'pool_address_pct': 99,
            'base_account_pct': 99,
            'quote_account_pct': 99,
            'known_discovery_pct': 85,
        }

        passed = all(
            completeness.get(key, 0) >= threshold
            for key, threshold in thresholds.items()
        )

        return {
            'total': completeness['total'],
            'completeness': {
                'pool_address_pct': completeness['pool_address_pct'],
                'base_account_pct': completeness['base_account_pct'],
                'quote_account_pct': completeness['quote_account_pct'],
                'discovery_method_pct': completeness['known_discovery_pct'],
                'pool_score_pct': 100.0 * completeness['has_pool_score'] / completeness['total'],
            },
            'thresholds': thresholds,
            'passed': passed,
        }


class TelemetryValidator:
    """Validates telemetry accuracy."""

    @staticmethod
    def validate_all(db: Database) -> Dict:
        """Validate telemetry data."""
        telemetry = db.query_one("""
            SELECT
                COUNT(*) as total_detected,
                COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) as resolved,
                COUNT(CASE WHEN resolved_at IS NULL THEN 1 END) as unresolved,
                ROUND(100.0 * COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) /
                    COUNT(*), 1) as resolution_rate_pct,
                ROUND(MIN(resolve_seconds), 2) as min_resolve_s,
                ROUND(AVG(resolve_seconds), 2) as avg_resolve_s,
                ROUND(MAX(resolve_seconds), 2) as max_resolve_s
            FROM token_resolution_telemetry
        """)

        if not telemetry or telemetry['total_detected'] == 0:
            return {
                'total_detected': 0,
                'resolved': 0,
                'unresolved': 0,
                'resolution_rate_pct': 0,
                'unresolved_after_60s': 0,
                'latency': {'min_s': 0, 'avg_s': 0, 'max_s': 0},
                'resolve_source_distribution': {},
                'errors': ['No telemetry data found'],
                'passed': False,
            }

        # Source distribution
        source_dist = db.query("""
            SELECT resolve_source, COUNT(*) as count
            FROM token_resolution_telemetry
            WHERE resolved_at IS NOT NULL
            GROUP BY resolve_source
            ORDER BY count DESC
        """)

        source_breakdown = {row['resolve_source']: row['count'] for row in source_dist}

        # Resolve source percentages
        resolved_count = telemetry['resolved']
        source_pct = {
            src: 100.0 * count / resolved_count if resolved_count > 0 else 0
            for src, count in source_breakdown.items()
        }

        # Unresolved after 60s
        unresolved_60s = db.query_one("""
            SELECT COUNT(*) as count
            FROM token_resolution_telemetry
            WHERE resolved_at IS NULL
              AND detected_at < strftime('%s', 'now') - 60
        """)

        unresolved_count = unresolved_60s['count'] if unresolved_60s else 0

        return {
            'total_detected': telemetry['total_detected'],
            'resolved': telemetry['resolved'],
            'unresolved': telemetry['unresolved'],
            'resolution_rate_pct': telemetry['resolution_rate_pct'],
            'unresolved_after_60s': unresolved_count,
            'latency': {
                'min_s': telemetry['min_resolve_s'],
                'avg_s': telemetry['avg_resolve_s'],
                'max_s': telemetry['max_resolve_s'],
            },
            'resolve_source_distribution': source_pct,
            'passed': (
                telemetry['resolution_rate_pct'] >= 95 and
                unresolved_count == 0
            ),
        }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Validation harness for PumpSwap discovery pipeline'
    )
    parser.add_argument(
        '--check',
        action='append',
        choices=['discovery', 'vault', 'registration', 'telemetry', 'all'],
        help='Validation checks to run'
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

    # If no checks specified, run all
    checks_to_run = args.check or ['all']
    if 'all' in checks_to_run:
        checks_to_run = ['discovery', 'vault', 'registration', 'telemetry']

    # Create database
    db = Database(args.db)

    print("\n" + "="*80)
    print("PUMPSWAP DISCOVERY PIPELINE — VALIDATION HARNESS")
    print("="*80)
    print(f"Database: {args.db}")
    print(f"Checks: {', '.join(checks_to_run)}")
    print()

    results = {}
    all_passed = True

    # Run checks
    if 'discovery' in checks_to_run:
        print("[1/4] Running discovery validation...")
        result = DiscoveryValidator.validate_all(db)
        results['discovery'] = result
        all_passed = all_passed and result['passed']

        print(f"  Total pools: {result['total_pools']}")
        print(f"  Violations: {len(result['errors'])}")

        if result['violations_by_type']:
            print("  Violations by type:")
            for violation_type, count in result['violations_by_type'].items():
                print(f"    - {violation_type}: {count}")

        print(f"  Status: {'✓ PASS' if result['passed'] else '✗ FAIL'}")
        print()

    if 'vault' in checks_to_run:
        print("[2/4] Running vault validation...")
        result = VaultValidator.validate_all(db)
        results['vault'] = result
        all_passed = all_passed and result['passed']

        print(f"  Total vaults: {result['total_vaults']}")
        print(f"  Validated: {result['validated']} ({result['validation_rate_pct']:.1f}%)")
        print(f"  Pending: {result['pending']}")
        print(f"  Zero address issues: {result['zero_address_issues']}")
        print(f"  Status: {'✓ PASS' if result['passed'] else '✗ FAIL'}")
        print()

    if 'registration' in checks_to_run:
        print("[3/4] Running registration validation...")
        result = RegistrationValidator.validate_all(db)
        results['registration'] = result
        all_passed = all_passed and result['passed']

        print(f"  Total registrations: {result['total']}")
        print(f"  Completeness:")
        for field, pct in result['completeness'].items():
            print(f"    - {field}: {pct:.1f}%")
        print(f"  Status: {'✓ PASS' if result['passed'] else '✗ FAIL'}")
        print()

    if 'telemetry' in checks_to_run:
        print("[4/4] Running telemetry validation...")
        result = TelemetryValidator.validate_all(db)
        results['telemetry'] = result
        all_passed = all_passed and result['passed']

        print(f"  Total detected: {result['total_detected']}")
        print(f"  Resolved: {result['resolved']}")
        print(f"  Resolution rate: {result['resolution_rate_pct']:.1f}%")
        print(f"  Unresolved (>60s): {result['unresolved_after_60s']}")
        print(f"  Latency (avg): {result['latency']['avg_s']:.2f}s")
        print(f"  Status: {'✓ PASS' if result['passed'] else '✗ FAIL'}")
        print()

    # Summary
    print("="*80)
    print("SUMMARY")
    print("="*80)

    check_results = []
    for check_name in checks_to_run:
        if check_name in results:
            passed = results[check_name]['passed']
            symbol = '✓' if passed else '✗'
            check_results.append(f"{symbol} {check_name.capitalize()}")

    for check in check_results:
        print(f"  {check}")

    print()
    if all_passed:
        print("✅ ALL VALIDATIONS PASSED")
    else:
        print("❌ SOME VALIDATIONS FAILED")

    # Save output
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {args.output}")

    return 0 if all_passed else 1


if __name__ == '__main__':
    sys.exit(main())
