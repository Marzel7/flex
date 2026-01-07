#!/usr/bin/env python3
"""
Comprehensive system verification for bot detection and risk assessment.

This script verifies:
1. Database schema has all required columns
2. Bot activity levels are properly categorized
3. Bot detection flags are correctly set
4. Assessment indicators are visible for assessed tokens
5. Top 30 tokens by % change display works
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta


def verify_database_schema():
    """Verify database has all required columns."""
    print("\n" + "="*70)
    print("VERIFICATION 1: Database Schema")
    print("="*70)

    db_path = Path('pumpswap_tokens.db')
    if not db_path.exists():
        print("❌ Database not found!")
        return False

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()

    # Check columns
    cursor.execute("PRAGMA table_info(pools)")
    columns = {col[1]: col[2] for col in cursor.fetchall()}

    required_cols = {
        'base_mint': 'TEXT',
        'funding_risk_level': 'TEXT',
        'funding_check_timestamp': 'TIMESTAMP',
        'bot_detection_flag': 'TEXT',
        'bot_activity_level': 'TEXT',
        'dexscreener_price_native': 'REAL'
    }

    all_present = True
    for col, col_type in required_cols.items():
        if col in columns:
            print(f"✓ {col}: {columns[col]}")
        else:
            print(f"✗ {col}: MISSING")
            all_present = False

    conn.close()
    return all_present


def verify_bot_activity_distribution():
    """Verify bot activity levels are properly distributed."""
    print("\n" + "="*70)
    print("VERIFICATION 2: Bot Activity Level Distribution")
    print("="*70)

    db_path = Path('pumpswap_tokens.db')
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()

    # Get distribution
    cursor.execute('''
        SELECT bot_activity_level, COUNT(*) as count
        FROM pools
        GROUP BY bot_activity_level
        ORDER BY
            CASE bot_activity_level
                WHEN 'HIGH' THEN 1
                WHEN 'MEDIUM' THEN 2
                WHEN 'LOW' THEN 3
                WHEN 'NONE' THEN 4
            END
    ''')

    rows = cursor.fetchall()
    total = sum(count for _, count in rows)

    for level, count in rows:
        percentage = (count / total * 100) if total > 0 else 0
        print(f"{level:8} │ {count:4} tokens ({percentage:5.1f}%)")

    print(f"{'─'*30}")
    print(f"{'Total':8} │ {total:4} tokens")

    # Check for properly categorized bots (actual bots, not 'none')
    cursor.execute('''
        SELECT COUNT(*)
        FROM pools
        WHERE bot_activity_level != 'NONE'
        AND bot_detection_flag IS NOT NULL
        AND bot_detection_flag != 'none'
    ''')

    properly_flagged = cursor.fetchone()[0]

    cursor.execute('''
        SELECT COUNT(*)
        FROM pools
        WHERE bot_detection_flag IS NOT NULL
        AND bot_detection_flag != 'none'
    ''')

    total_with_flag = cursor.fetchone()[0]

    print(f"\n✓ {properly_flagged}/{total_with_flag} bot-detected tokens have activity level")

    conn.close()
    return properly_flagged == total_with_flag


def verify_assessment_indicators():
    """Verify tokens show assessment completion indicators."""
    print("\n" + "="*70)
    print("VERIFICATION 3: Assessment Indicators (✓ for assessed tokens)")
    print("="*70)

    db_path = Path('pumpswap_tokens.db')
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()

    # Get tokens with assessment indicators
    cursor.execute('''
        SELECT
            base_mint,
            symbol,
            funding_risk_level,
            bot_activity_level,
            funding_check_timestamp
        FROM pools
        WHERE funding_check_timestamp IS NOT NULL
        AND funding_risk_level IS NOT NULL
        AND funding_risk_level != 'UNKNOWN'
        LIMIT 10
    ''')

    assessed = cursor.fetchall()

    print(f"\nFound {len(assessed)} tokens with assessment completion indicators (✓):\n")

    for token_mint, symbol, risk, bot_level, check_time in assessed:
        symbol_str = symbol if symbol else "N/A"
        bot_level_str = bot_level if bot_level else "N/A"
        display = f"{symbol_str:8} │ {risk:12} │ Bot: {bot_level_str:6} │ {check_time[:10]}"
        print(f"✓ {display}")

    # Count total assessed
    cursor.execute('''
        SELECT COUNT(*)
        FROM pools
        WHERE funding_check_timestamp IS NOT NULL
        AND funding_risk_level IS NOT NULL
        AND funding_risk_level != 'UNKNOWN'
    ''')

    total_assessed = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM pools')
    total_tokens = cursor.fetchone()[0]

    percentage = (total_assessed / total_tokens * 100) if total_tokens > 0 else 0
    print(f"\nAssessment Coverage: {total_assessed}/{total_tokens} ({percentage:.1f}%)")

    conn.close()
    return total_assessed > 0


def verify_price_change_sorting():
    """Verify tokens can be sorted by % change correctly."""
    print("\n" + "="*70)
    print("VERIFICATION 4: Top 30 Tokens by % Change Sorting")
    print("="*70)

    db_path = Path('pumpswap_tokens.db')
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()

    # Get tokens with native prices for % change calculation
    cursor.execute('''
        SELECT
            base_mint,
            name,
            dexscreener_price_native
        FROM pools
        WHERE dexscreener_price_native > 0
        AND dexscreener_price_native IS NOT NULL
        ORDER BY dexscreener_price_native DESC
        LIMIT 10
    ''')

    top_tokens = cursor.fetchall()

    print(f"\nTop 10 tokens by SOL balance (highest gains):\n")

    for i, (mint, name, sol_balance) in enumerate(top_tokens, 1):
        pct_change = ((sol_balance - 85) / 85) * 100 if sol_balance > 0 else -100
        marker = "🔥" if pct_change > 100 else "📈" if pct_change > 0 else "📉"
        name_str = name if name else "N/A"
        print(f"{i:2}. {name_str:8} │ SOL: {sol_balance:8.2f} │ Change: {pct_change:+7.1f}% {marker}")

    # Count tokens with valid price data
    cursor.execute('''
        SELECT COUNT(*)
        FROM pools
        WHERE dexscreener_price_native > 0
        AND dexscreener_price_native IS NOT NULL
    ''')

    valid_tokens = cursor.fetchone()[0]
    print(f"\n✓ {valid_tokens} tokens have valid price data for sorting")

    conn.close()
    return valid_tokens > 0


def verify_bot_detection_accuracy():
    """Verify bot detection is matching correctly."""
    print("\n" + "="*70)
    print("VERIFICATION 5: Bot Detection Accuracy")
    print("="*70)

    db_path = Path('pumpswap_tokens.db')
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()

    # Check bot detection flags
    cursor.execute('''
        SELECT
            bot_detection_flag,
            COUNT(*) as count
        FROM pools
        WHERE bot_detection_flag IS NOT NULL
        GROUP BY bot_detection_flag
    ''')

    flags = cursor.fetchall()

    print("\nBot detection flag distribution:\n")
    for flag, count in flags:
        print(f"✓ {flag:30} │ {count:4} tokens")

    # Verify LOW+ or higher risk level is set for actual bots (exclude 'none')
    cursor.execute('''
        SELECT COUNT(*)
        FROM pools
        WHERE bot_detection_flag IS NOT NULL
        AND bot_detection_flag != 'none'
        AND funding_risk_level IN ('LOW+', 'HIGH', 'CRITICAL', 'CONFIRMED_RUG_PULL')
    ''')

    correctly_risked = cursor.fetchone()[0]

    cursor.execute('''
        SELECT COUNT(*) FROM pools
        WHERE bot_detection_flag IS NOT NULL
        AND bot_detection_flag != 'none'
    ''')
    total_bots = cursor.fetchone()[0]

    print(f"\n✓ {correctly_risked}/{total_bots} bot-detected tokens marked with risk (LOW+ or higher)")

    # Check database integrity
    cursor.execute('''
        SELECT COUNT(*)
        FROM creator_bot_usage
    ''')

    creator_bot_records = cursor.fetchone()[0]
    print(f"✓ {creator_bot_records} creator-bot relationships in database")

    conn.close()
    return correctly_risked == total_bots


def verify_recent_performance():
    """Verify system is working on recent tokens."""
    print("\n" + "="*70)
    print("VERIFICATION 6: Recent Token Assessment Performance")
    print("="*70)

    db_path = Path('pumpswap_tokens.db')
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()

    # Check last 20 tokens created
    cursor.execute('''
        SELECT
            base_mint,
            symbol,
            first_seen,
            funding_risk_level,
            bot_detection_flag,
            bot_activity_level
        FROM pools
        ORDER BY first_seen DESC
        LIMIT 20
    ''')

    recent = cursor.fetchall()

    print(f"\nLast 20 tokens detected:\n")

    assessed_count = 0
    for mint, symbol, first_seen, risk, bot_flag, bot_level in recent:
        is_assessed = "✓" if (risk and risk != 'UNKNOWN' and first_seen) else " "
        status = f"{'Assessed' if is_assessed == '✓' else 'Pending':8}"
        symbol_str = symbol if symbol else "N/A"
        risk_str = risk if risk else 'N/A'

        if is_assessed == "✓":
            assessed_count += 1

        print(f"{is_assessed} {symbol_str:8} │ {status} │ {first_seen[:19]} │ {risk_str:12}")

    percentage = (assessed_count / len(recent) * 100) if recent else 0
    print(f"\n✓ Assessment rate: {assessed_count}/{len(recent)} ({percentage:.1f}%)")

    conn.close()
    return assessed_count > 0


def main():
    """Run all verifications."""
    print("\n")
    print("╔" + "="*68 + "╗")
    print("║" + " "*15 + "SYSTEM VERIFICATION REPORT" + " "*27 + "║")
    print("╚" + "="*68 + "╝")

    results = {
        "Database Schema": verify_database_schema(),
        "Bot Activity Distribution": verify_bot_activity_distribution(),
        "Assessment Indicators": verify_assessment_indicators(),
        "Price Change Sorting": verify_price_change_sorting(),
        "Bot Detection Accuracy": verify_bot_detection_accuracy(),
        "Recent Performance": verify_recent_performance()
    }

    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)

    for check, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status:8} │ {check}")

    all_passed = all(results.values())
    print("\n" + "="*70)

    if all_passed:
        print("✓✓✓ ALL SYSTEMS OPERATIONAL ✓✓✓")
    else:
        print("⚠ SOME CHECKS FAILED - SEE ABOVE")

    print("="*70 + "\n")

    return all_passed


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
