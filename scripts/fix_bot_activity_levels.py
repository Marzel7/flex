#!/usr/bin/env python3
"""
Fix bot_activity_level for tokens that have bot_detection_flag but activity level is NONE.

This can happen if bot detection was run before the calculate_bot_activity_level function
was added, or if the database wasn't updated properly.
"""

import sqlite3
from pathlib import Path


def calculate_bot_activity_level(total_bot_transactions):
    """Categorize bot activity severity based on transaction count."""
    if total_bot_transactions == 0:
        return 'NONE'
    elif total_bot_transactions <= 5:
        return 'LOW'
    elif total_bot_transactions <= 20:
        return 'MEDIUM'
    else:
        return 'HIGH'


def fix_bot_activity_levels():
    """Fix bot_activity_level for all tokens with bot_detection_flag."""
    db_path = Path('pumpswap_tokens.db')

    if not db_path.exists():
        print("❌ Database not found!")
        return False

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()

    # Find all tokens with bot_detection_flag but NONE activity level
    cursor.execute('''
        SELECT base_mint, pumpfun_creator
        FROM pools
        WHERE bot_detection_flag IS NOT NULL
        AND bot_detection_flag != 'none'
        AND (bot_activity_level IS NULL OR bot_activity_level = 'NONE')
    ''')

    tokens_to_fix = cursor.fetchall()
    print(f"Found {len(tokens_to_fix)} tokens to fix\n")

    fixed_count = 0
    for token_mint, creator in tokens_to_fix:
        if not creator:
            continue

        # Get total bot transactions for this creator
        cursor.execute('''
            SELECT SUM(transaction_count)
            FROM creator_bot_usage
            WHERE creator_address = ?
        ''', (creator,))

        result = cursor.fetchone()
        total_tx = result[0] if result and result[0] else 0

        # If no transaction count found, this was likely detected via direct address match
        # In that case, use a low default (e.g., 3 for MEDIUM - conservative estimate)
        if total_tx == 0:
            # Direct KNOWN_BOTS match = detected with certainty, assign MEDIUM activity by default
            activity_level = 'MEDIUM'
        else:
            activity_level = calculate_bot_activity_level(total_tx)

        # Update the token
        cursor.execute('''
            UPDATE pools
            SET bot_activity_level = ?
            WHERE base_mint = ?
        ''', (activity_level, token_mint))

        fixed_count += 1
        if fixed_count % 10 == 0:
            print(f"  Fixed {fixed_count}/{len(tokens_to_fix)}: {activity_level} (tx count: {total_tx})")

    conn.commit()
    conn.close()

    print(f"\n✓ Fixed {fixed_count} tokens")

    # Verify fix
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT bot_activity_level, COUNT(*)
        FROM pools
        WHERE bot_detection_flag IS NOT NULL
        AND bot_detection_flag != 'none'
        GROUP BY bot_activity_level
    ''')

    print("\nNew bot activity distribution:")
    for level, count in cursor.fetchall():
        print(f"  {level:8} │ {count:4}")

    cursor.execute('''
        SELECT COUNT(*)
        FROM pools
        WHERE bot_detection_flag IS NOT NULL
        AND bot_detection_flag != 'none'
        AND bot_activity_level = 'NONE'
    ''')

    still_broken = cursor.fetchone()[0]
    if still_broken > 0:
        print(f"\n⚠ {still_broken} tokens still have NONE activity level")
    else:
        print(f"\n✓ All bot-detected tokens now have proper activity level")

    conn.close()
    return still_broken == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if fix_bot_activity_levels() else 1)
