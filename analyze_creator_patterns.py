#!/usr/bin/env python3
"""
Analyze creator patterns and behavior on migrated tokens.

Examines:
- Creator's portfolio performance (across all their tokens)
- Peak timing patterns (how quickly tokens peak)
- Price volatility and stability
- Success rate (% of tokens that hit certain thresholds)
- Token lifespan and decay patterns
- Risk indicators (pump and dump signatures)
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import sys

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


def parse_timestamp(ts_str):
    """Parse timestamp string to datetime"""
    if not ts_str:
        return None

    try:
        # Try parsing ISO format
        if 'T' in str(ts_str):
            return datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
        else:
            # Try parsing as float (unix timestamp in milliseconds)
            ts_float = float(ts_str)
            if ts_float > 1e10:  # Likely milliseconds
                return datetime.fromtimestamp(ts_float / 1000)
            else:
                return datetime.fromtimestamp(ts_float)
    except:
        return None


def get_creator_portfolio(creator_address):
    """Get all tokens from a creator"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                base_mint,
                symbol,
                pumpfun_symbol,
                first_seen,
                current_price_usd,
                peak_percent_change,
                peak_price_usd,
                peak_time,
                buy_price_usd,
                buy_time,
                sell_price_usd,
                sell_time,
                trade_status,
                profit_loss_percent,
                pumpfun_image
            FROM pools
            WHERE pumpfun_creator = ?
            ORDER BY first_seen DESC
        ''', (creator_address,))

        tokens = cursor.fetchall()
        conn.close()
        return tokens

    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def calculate_time_diff(ts1, ts2):
    """Calculate time difference in minutes"""
    t1 = parse_timestamp(ts1)
    t2 = parse_timestamp(ts2)

    if not t1 or not t2:
        return None

    diff = abs(t2 - t1)
    minutes = diff.total_seconds() / 60
    return minutes


def format_duration(minutes):
    """Format minutes to human readable duration"""
    if minutes is None:
        return "—"

    if minutes < 60:
        return f"{int(minutes)}m"
    elif minutes < 1440:
        hours = minutes / 60
        return f"{hours:.1f}h"
    else:
        days = minutes / 1440
        return f"{days:.1f}d"


def analyze_creator_patterns(creator_address):
    """Analyze creator's token patterns"""
    tokens = get_creator_portfolio(creator_address)

    if not tokens:
        print(f"\nNo tokens found for creator: {creator_address}")
        return

    creator_short = f"{creator_address[:8]}...{creator_address[-4:]}"

    print(f"\n{'='*160}")
    print(f"CREATOR PATTERN ANALYSIS: {creator_short}")
    print(f"Full Address: {creator_address}")
    print(f"{'='*160}\n")

    # Portfolio statistics
    total_tokens = len(tokens)
    traded_tokens = sum(1 for t in tokens if t[8])  # buy_price_usd
    sold_tokens = sum(1 for t in tokens if t[11])  # sell_time
    tokens_with_peak = sum(1 for t in tokens if t[5] and t[5] > 0)  # peak_percent_change

    print("PORTFOLIO OVERVIEW")
    print(f"  Total tokens launched: {total_tokens}")
    print(f"  Tokens traded: {traded_tokens} ({traded_tokens/total_tokens*100:.1f}%)")
    print(f"  Tokens sold: {sold_tokens} ({sold_tokens/total_tokens*100:.1f}%)")
    print(f"  Tokens with peak data: {tokens_with_peak} ({tokens_with_peak/total_tokens*100:.1f}%)")
    print()

    # Peak performance statistics
    peaks = [t[5] for t in tokens if t[5] and t[5] > 0]
    if peaks:
        avg_peak = sum(peaks) / len(peaks)
        max_peak = max(peaks)
        min_peak = min(peaks)
        print("PEAK PERFORMANCE")
        print(f"  Average peak: +{avg_peak:.2f}%")
        print(f"  Highest peak: +{max_peak:.2f}%")
        print(f"  Lowest peak: +{min_peak:.2f}%")
        print()

    # Time to peak analysis
    times_to_peak = []
    for token in tokens:
        first_seen, peak_time = token[3], token[7]
        if first_seen and peak_time:
            minutes = calculate_time_diff(first_seen, peak_time)
            if minutes is not None and minutes >= 0:
                times_to_peak.append(minutes)

    if times_to_peak:
        avg_time = sum(times_to_peak) / len(times_to_peak)
        min_time = min(times_to_peak)
        max_time = max(times_to_peak)
        print("TIME TO PEAK")
        print(f"  Average: {format_duration(avg_time)}")
        print(f"  Fastest: {format_duration(min_time)}")
        print(f"  Slowest: {format_duration(max_time)}")
        print()

    # Profitability analysis
    profits = [t[13] for t in tokens if t[13] is not None and t[11]]  # profit_loss_percent where sold
    if profits:
        profitable = sum(1 for p in profits if p > 0)
        avg_profit = sum(profits) / len(profits) if profits else 0
        print("PROFITABILITY (Sold Tokens)")
        print(f"  Tokens sold: {len(profits)}")
        print(f"  Profitable: {profitable} ({profitable/len(profits)*100:.1f}%)")
        print(f"  Average return: {avg_profit:+.2f}%")
        print()

    # Display tokens table
    print("TOKEN DETAILS")
    print("-" * 160)

    data = []
    for token in tokens:
        (mint, symbol, pf_symbol, first_seen, curr_price, peak_pct, peak_price,
         peak_time, buy_price, buy_time, sell_price, sell_time, status, profit_pct, image) = token

        name = symbol or pf_symbol or mint[:8]

        # Peak info
        peak_str = f"+{peak_pct:.1f}%" if peak_pct and peak_pct > 0 else "—"

        # Time to peak
        time_to_peak = "—"
        if first_seen and peak_time:
            minutes = calculate_time_diff(first_seen, peak_time)
            if minutes is not None and minutes >= 0:
                time_to_peak = format_duration(minutes)

        # Profit/Loss
        profit_str = "—"
        if profit_pct is not None:
            if sell_time:  # Only show profit if sold
                profit_str = f"{profit_pct:+.1f}%"
            elif buy_time:  # Show unrealized if bought but not sold
                profit_str = f"{profit_pct:+.1f}% (unrealized)"

        # Status indicator
        status_icon = "⏳" if status == "waiting" else "📈" if status == "trading" else "✅" if status == "sold" else "❌"

        data.append([
            name,
            mint,
            peak_str,
            time_to_peak,
            f"${curr_price:.2e}" if curr_price else "—",
            profit_str,
            status_icon,
        ])

    if HAS_TABULATE:
        headers = ['Token', 'Mint', 'Peak %', 'Time to Peak', 'Current Price', 'Profit/Loss', 'Status']
        print(tabulate(data, headers=headers, tablefmt='grid'))
    else:
        print(f"{'Token':<12} | {'Mint':<44} | {'Peak %':<10} | {'Time to Peak':<12} | {'Current Price':<15} | {'P/L':<15} | Status")
        print("-" * 160)
        for row in data:
            print(f"{row[0]:<12} | {row[1]:<44} | {row[2]:<10} | {row[3]:<12} | {row[4]:<15} | {row[5]:<15} | {row[6]}")

    print()

    # Risk indicators
    print("RISK INDICATORS & PATTERNS")
    print("-" * 160)

    # Pump and dump signature: high peak but quick decline
    pump_dumps = []
    for token in tokens:
        peak_pct, peak_time, sell_time, sell_price, peak_price = token[5], token[7], token[11], token[10], token[6]

        if peak_pct and peak_pct > 100 and peak_time and sell_time and sell_price and peak_price:
            minutes_from_peak_to_sell = calculate_time_diff(peak_time, sell_time)
            if minutes_from_peak_to_sell and minutes_from_peak_to_sell < 120:  # Sold within 2 hours of peak
                decline_pct = ((peak_price - sell_price) / peak_price) * 100 if peak_price > 0 else 0
                if decline_pct > 50:  # Lost >50% from peak
                    pump_dumps.append({
                        'name': token[1] or token[2],
                        'peak': peak_pct,
                        'decline': decline_pct,
                        'time_to_sell': minutes_from_peak_to_sell
                    })

    if pump_dumps:
        print(f"\n⚠️  PUMP & DUMP SIGNATURE DETECTED ({len(pump_dumps)} tokens):")
        for pd in pump_dumps:
            print(f"    • {pd['name']}: +{pd['peak']:.1f}% → {pd['decline']:.1f}% decline in {format_duration(pd['time_to_sell'])}")
    else:
        print(f"\n✓ No pump & dump signatures detected")

    # Creator concentration: releasing tokens frequently
    token_dates = []
    for token in tokens:
        ts = parse_timestamp(token[3])
        if ts:
            token_dates.append(ts)

    if len(token_dates) > 1:
        token_dates.sort()
        time_between = []
        for i in range(1, len(token_dates)):
            diff = (token_dates[i] - token_dates[i-1]).total_seconds() / 3600  # hours
            time_between.append(diff)

        avg_interval = sum(time_between) / len(time_between) if time_between else 0
        print(f"\n📊 TOKEN RELEASE PATTERN:")
        print(f"    Average time between releases: {format_duration(avg_interval * 60)}")

        if avg_interval < 24:
            print(f"    ⚠️  Creator is releasing tokens rapidly (high frequency)")
        else:
            print(f"    ✓ Creator releases tokens at normal pace")

    print(f"\n{'='*160}\n")


if __name__ == '__main__':
    # Analyze the duplicate creator by default
    creator_to_analyze = "6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA"

    if len(sys.argv) > 1:
        creator_to_analyze = sys.argv[1]

    analyze_creator_patterns(creator_to_analyze)
