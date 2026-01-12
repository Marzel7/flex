#!/usr/bin/env python3
"""
Display the absolute top tokens by peak % change with all details.

This shows ALL tokens in the database sorted by peak performance,
not just recently active ones.
"""

import sqlite3
from pathlib import Path
from datetime import datetime


def show_top_peaks(limit=30):
    """Show top tokens by peak % change."""
    db_path = Path('pumpswap_tokens.db')

    if not db_path.exists():
        print("❌ Database not found!")
        return False

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()

    # Get top tokens by peak % change with all details
    cursor.execute('''
        SELECT
            base_mint,
            name,
            symbol,
            peak_percent_change,
            peak_price_usd,
            dexscreener_price_native,
            dexscreener_price_usd,
            funding_risk_level,
            bot_activity_level,
            first_seen,
            peak_time
        FROM pools
        WHERE peak_percent_change IS NOT NULL
        ORDER BY peak_percent_change DESC
        LIMIT ?
    ''', (limit,))

    rows = cursor.fetchall()

    print("\n" + "="*120)
    print(f"{'TOP ' + str(limit) + ' TOKENS BY PEAK % CHANGE':^120}")
    print("="*120)
    print()

    for i, row in enumerate(rows, 1):
        mint, name, symbol, peak_pct, peak_usd, sol_bal, price_usd, risk, bot_level, first_seen, peak_time = row

        # Format display info
        name_str = name if name else "UNKNOWN"
        symbol_str = symbol if symbol else "N/A"
        risk_str = risk if risk else "UNKNOWN"
        bot_str = bot_level if bot_level else "NONE"

        print(f"{i:2}. PEAK: {peak_pct:8.2f}% │ {name_str[:20]:20} │ {symbol_str:8}")
        print(f"    Mint: {mint}")
        print(f"    Risk: {risk_str:15} │ Bots: {bot_str:8} │ Detected: {first_seen[:10] if first_seen else 'N/A'}")
        print(f"    Peak Price: ${peak_usd if peak_usd else 'N/A':>12} │ Current: ${price_usd if price_usd else 'N/A':>12}")
        print(f"    Peak Time: {peak_time[:19] if peak_time else 'N/A'}")
        print()

    conn.close()

    # Summary statistics
    print("="*120)
    cursor = conn = sqlite3.connect(str(db_path), check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute('SELECT AVG(peak_percent_change), MIN(peak_percent_change), MAX(peak_percent_change) FROM pools WHERE peak_percent_change IS NOT NULL')
    avg_peak, min_peak, max_peak = cursor.fetchone()

    print(f"Peak Performance Statistics:")
    print(f"  Highest Peak: {max_peak:.2f}%")
    print(f"  Lowest Peak: {min_peak:.2f}%")
    print(f"  Average Peak: {avg_peak:.2f}%")

    cursor.execute('''
        SELECT funding_risk_level, COUNT(*), AVG(peak_percent_change)
        FROM pools
        WHERE peak_percent_change IS NOT NULL
        GROUP BY funding_risk_level
        ORDER BY AVG(peak_percent_change) DESC
    ''')

    print(f"\nPeak Performance by Risk Level:")
    for risk, count, avg in cursor.fetchall():
        risk_str = risk if risk else "UNKNOWN"
        print(f"  {risk_str:15}: {count:3} tokens, Avg Peak: {avg:8.2f}%")

    conn.close()
    return True


if __name__ == "__main__":
    import sys
    sys.exit(0 if show_top_peaks(30) else 1)
