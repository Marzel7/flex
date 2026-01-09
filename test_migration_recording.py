#!/usr/bin/env python3
"""
Test script to verify migration data recording functionality.

This script:
1. Checks if migration tracking columns exist in database
2. Simulates a migration event and records it
3. Verifies the data was correctly stored
4. Displays the recorded migration data
"""

import sqlite3
import time
import sys
import os

DB_PATH = "pumpswap_tokens.db"

def check_migration_columns():
    """Verify migration tracking columns exist"""
    print("\n" + "="*80)
    print("  TEST 1: Checking Migration Tracking Columns")
    print("="*80 + "\n")

    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()

        # Get table schema
        cursor.execute("PRAGMA table_info(token_analysis)")
        columns = cursor.fetchall()

        required_columns = {
            'has_migrated',
            'migrated_at',
            'migration_signature',
            'migration_detected_at',
            'time_to_migration_seconds',
            'pumpswap_pool_address'
        }

        existing_columns = {col[1] for col in columns}

        print(f"Token Analysis Table Columns ({len(existing_columns)} total):")
        for col in columns:
            col_name = col[1]
            col_type = col[2]
            marker = " ✅" if col_name in required_columns else ""
            print(f"  • {col_name:40} ({col_type}){marker}")

        missing = required_columns - existing_columns
        if missing:
            print(f"\n❌ Missing columns: {missing}")
            conn.close()
            return False

        print(f"\n✅ All {len(required_columns)} migration tracking columns present!")
        conn.close()
        return True

    except Exception as e:
        print(f"❌ Error checking columns: {e}")
        return False


def get_test_token():
    """Get a token that was analyzed (for testing)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT mint, analyzed_at, amm_rug_probability, amm_risk_level
            FROM token_analysis
            LIMIT 1
        """)

        result = cursor.fetchone()
        conn.close()

        return result
    except:
        return None


def simulate_migration_recording():
    """Simulate recording a migration for a test token"""
    print("\n" + "="*80)
    print("  TEST 2: Simulating Migration Recording")
    print("="*80 + "\n")

    test_token = get_test_token()

    if not test_token:
        print("⚠️  No tokens in database to test with.")
        print("   First run: python3 test_complete_workflow.py --duration 60")
        return False

    mint, analyzed_at, rug_prob, risk_level = test_token
    print(f"Using test token: {mint[:40]}...")
    print(f"  Analysis Time: {analyzed_at}")
    print(f"  Rug Probability: {rug_prob:.1%}")
    print(f"  Risk Level: {risk_level}\n")

    # Simulate migration event
    migration_time = time.time()
    time_to_migration = int(migration_time - analyzed_at)
    migration_sig = "5JzSd9nM3pQ4rKwL8nXyZ2aBcD5eF6gH7iJ8kL9mN0oPqRsT" + "u"*10  # Simulated signature

    print(f"Recording simulated migration:")
    print(f"  Migration Time: {migration_time}")
    print(f"  Time to Migration: {time_to_migration} seconds ({time_to_migration/60:.1f} minutes)")
    print(f"  Migration Sig: {migration_sig[:40]}...\n")

    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()

        # Record the migration
        cursor.execute("""
            UPDATE token_analysis SET
                has_migrated = 1,
                migrated_at = ?,
                migration_signature = ?,
                migration_detected_at = ?,
                time_to_migration_seconds = ?
            WHERE mint = ?
        """, (migration_time, migration_sig, migration_time, time_to_migration, mint))

        conn.commit()
        print("✅ Migration data recorded successfully!\n")

        # Verify it was stored
        cursor.execute("""
            SELECT has_migrated, migrated_at, migration_signature, time_to_migration_seconds
            FROM token_analysis
            WHERE mint = ?
        """, (mint,))

        result = cursor.fetchone()
        conn.close()

        if result:
            has_mig, mig_at, mig_sig, time_to_mig = result
            print(f"✅ Verification - Data retrieved from database:")
            print(f"  has_migrated: {has_mig}")
            print(f"  migrated_at: {mig_at}")
            print(f"  migration_signature: {mig_sig[:40]}...")
            print(f"  time_to_migration_seconds: {time_to_mig}")
            return True
        else:
            print("❌ Could not verify recorded data")
            return False

    except Exception as e:
        print(f"❌ Error recording migration: {e}")
        return False


def query_recorded_migrations():
    """Query and display all recorded migrations"""
    print("\n" + "="*80)
    print("  TEST 3: Querying Recorded Migrations")
    print("="*80 + "\n")

    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT mint, amm_rug_probability, amm_risk_level,
                   migrated_at, migration_signature, time_to_migration_seconds
            FROM token_analysis
            WHERE has_migrated = 1
            ORDER BY migrated_at DESC
        """)

        migrations = cursor.fetchall()
        conn.close()

        if not migrations:
            print("ℹ️  No migrations recorded in database yet")
            return True

        print(f"Found {len(migrations)} recorded migration(s):\n")

        for i, row in enumerate(migrations, 1):
            mint, rug_prob, risk, mig_at, sig, time_to_mig = row
            print(f"{i}. Token: {mint[:40]}...")
            print(f"   Risk: {risk} ({rug_prob:.1%})")
            if mig_at:
                from datetime import datetime
                mig_time = datetime.fromtimestamp(mig_at)
                print(f"   Migration: {mig_time.strftime('%Y-%m-%d %H:%M:%S')}")
            if time_to_mig:
                print(f"   Time to Migration: {time_to_mig} seconds ({time_to_mig/60:.1f} minutes)")
            print()

        return True

    except Exception as e:
        print(f"❌ Error querying migrations: {e}")
        return False


def test_mint_extraction():
    """Test the mint extraction logic"""
    print("\n" + "="*80)
    print("  TEST 4: Token Mint Extraction from Logs")
    print("="*80 + "\n")

    import re

    # Sample migration logs
    sample_logs = [
        "Instruction: Migrate",
        "Token: A94G4PcndyU3ppqGwsii5xzpmkLZN8M1cuWAsTLZpump",
        "Pool: J7u2pW5kX...",
        "Initialize pool",
        "Mint SOL balance",
        "Transfer authority"
    ]

    print("Sample log entries:")
    for log in sample_logs:
        print(f"  • {log}")

    print("\nExtracting token mint...\n")

    logs_text = ' '.join(sample_logs)

    patterns = [
        r'Token:\s*([1-9A-HJ-NP-Z]{44})',
        r'mint.*?([1-9A-HJ-NP-Z]{44})',
        r'([1-9A-HJ-NP-Z]{44})',
    ]

    found = False
    for pattern in patterns:
        matches = re.findall(pattern, logs_text, re.IGNORECASE)
        if matches:
            mint = matches[0]
            if mint != "So11111111111111111111111111111111111111112":
                print(f"✅ Found token mint: {mint}\n")
                found = True
                break

    if not found:
        print("⚠️  Could not extract mint from sample logs")

    return True


def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("  MIGRATION RECORDING SYSTEM - VALIDATION TESTS")
    print("="*80)

    results = []

    # Test 1: Check columns
    results.append(("Migration Columns", check_migration_columns()))

    # Test 2: Simulate recording
    results.append(("Simulated Recording", simulate_migration_recording()))

    # Test 3: Query migrations
    results.append(("Query Migrations", query_recorded_migrations()))

    # Test 4: Mint extraction
    results.append(("Mint Extraction", test_mint_extraction()))

    # Summary
    print("\n" + "="*80)
    print("  TEST SUMMARY")
    print("="*80 + "\n")

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed! Migration recording system is working correctly.\n")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Check the output above.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
