#!/usr/bin/env python3
"""
Hide tokens from the table if they have -75% or worse price decline.

Tokens remain in the database for risk assessment cross-reference and historical
tracking, but are hidden from the main UI table to reduce unnecessary API calls.

Usage:
    python hide_poor_performers.py

This will:
1. Calculate price change for each token
2. Identify tokens with -75% or worse decline
3. Mark them as hidden_from_table = 1
4. Report results
"""

import sqlite3
from pathlib import Path
from datetime import datetime


def hide_poor_performers():
    """Hide tokens with -75% or worse price decline"""

    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print("Error: Database not found")
        return False

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    c = conn.cursor()

    try:
        # Get all tokens with their price data
        c.execute('''
            SELECT
                base_mint,
                pumpfun_symbol,
                initial_price_usd,
                current_price_usd,
                funding_risk_level,
                hidden_from_table
            FROM pools
            WHERE initial_price_usd > 0 AND current_price_usd > 0
            ORDER BY first_seen DESC
        ''')

        tokens = c.fetchall()

        if not tokens:
            print("No tokens with price data found")
            conn.close()
            return False

        print(f"\nAnalyzing {len(tokens)} tokens for poor performance")
        print("=" * 100)

        to_hide = []

        for mint, symbol, initial_price, current_price, risk, hidden in tokens:
            if initial_price and initial_price > 0:
                price_change_pct = ((current_price - initial_price) / initial_price) * 100

                # Check if token qualifies for hiding (-75% or worse)
                if price_change_pct <= -75:
                    to_hide.append({
                        'mint': mint,
                        'symbol': symbol or mint[:8],
                        'change': price_change_pct,
                        'risk': risk,
                        'already_hidden': hidden
                    })

        if not to_hide:
            print("\n✅ No tokens qualify for hiding (all above -75% threshold)")
            print("\nPrice distribution:")

            # Show distribution
            c.execute('''
                SELECT
                    pumpfun_symbol,
                    ROUND(((current_price_usd - initial_price_usd) / initial_price_usd) * 100, 2) as change_pct
                FROM pools
                WHERE initial_price_usd > 0 AND current_price_usd > 0
                ORDER BY change_pct ASC
                LIMIT 20
            ''')

            for symbol, change in c.fetchall():
                status = "✓" if change > -75 else "⚠"
                print(f"  {status} {symbol:15} | {change:7.1f}%")

            conn.close()
            return True

        print(f"\nFound {len(to_hide)} token(s) with -75% or worse decline:")
        print("-" * 100)

        for token in to_hide:
            status = "already hidden" if token['already_hidden'] else "NEW"
            print(f"{status:15} | {token['symbol']:15} | {token['change']:7.1f}% | Risk: {token['risk']:8} | {token['mint'][:16]}...")

        # Update database to hide these tokens
        hidden_count = 0
        for token in to_hide:
            if not token['already_hidden']:
                c.execute('''
                    UPDATE pools
                    SET hidden_from_table = 1
                    WHERE base_mint = ?
                ''', (token['mint'],))
                hidden_count += 1

        conn.commit()

        print("-" * 100)
        print(f"\n✅ Hidden {hidden_count} new token(s) from table display")
        print(f"   (Already hidden: {len(to_hide) - hidden_count})")
        print(f"\n📌 Tokens remain in database for:")
        print(f"   • Risk assessment cross-reference")
        print(f"   • Historical price tracking")
        print(f"   • Coordination analysis")

        # Show current display status
        c.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN hidden_from_table = 0 THEN 1 ELSE 0 END) as visible,
                SUM(CASE WHEN hidden_from_table = 1 THEN 1 ELSE 0 END) as hidden
            FROM pools
        ''')

        total, visible, hidden = c.fetchone()
        hidden = hidden or 0

        print(f"\n📊 Table display status:")
        print(f"   Total tokens in database: {total}")
        print(f"   Visible in table:        {visible} ({visible*100//total}%)")
        print(f"   Hidden from table:       {hidden} ({hidden*100//total if total > 0 else 0}%)")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        conn.close()


if __name__ == '__main__':
    import sys
    success = hide_poor_performers()
    sys.exit(0 if success else 1)
