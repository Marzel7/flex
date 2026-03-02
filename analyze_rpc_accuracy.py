#!/usr/bin/env python3
"""
RPC Metrics Accuracy Analysis

Compares instrumented RPC calls vs actual Helius account usage.
Helps identify:
- Inflated call counts (retries counted multiple times)
- Inaccurate credit schedule
- Test/mock modes not consuming real credits
- Uninstrumented calls

Usage:
    python analyze_rpc_accuracy.py              # Full analysis
    python analyze_rpc_accuracy.py --section creator_outgoing_scan
    python analyze_rpc_accuracy.py --compare    # Side-by-side comparison
"""

import os
import sys
import sqlite3
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")


def _connect():
    """Create database connection"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def get_rpc_metrics_summary() -> Optional[Dict]:
    """Get metrics summary from RPC metrics API"""
    try:
        resp = requests.get("http://localhost:8001/metrics/rpc/summary", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None


def get_rpc_sections() -> Optional[Dict]:
    """Get metrics by section from RPC metrics API"""
    try:
        resp = requests.get("http://localhost:8001/metrics/rpc/sections", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None


def get_helius_latest_usage() -> Optional[Dict]:
    """Get latest Helius usage from local database"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("""
        SELECT timestamp, credits_remaining, credits_used, credits_used_month
        FROM helius_usage_snapshots
        ORDER BY timestamp DESC
        LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            return dict(row)
    finally:
        conn.close()
    return None


def analyze_section(section_name: str, sections_data: Dict):
    """Deep dive into a single section"""
    if "sections" not in sections_data or section_name not in sections_data["sections"]:
        print(f"❌ Section '{section_name}' not found in metrics")
        return

    section = sections_data["sections"][section_name]

    print(f"\n" + "=" * 80)
    print(f"SECTION: {section_name}")
    print("=" * 80)

    requests_count = section.get("requests", 0)
    credits_total = section.get("credits_total", 0)

    print(f"Total Requests:     {requests_count:>10,}")
    print(f"Total Credits:      {credits_total:>10,}")

    if requests_count > 0:
        avg_cost = credits_total / requests_count
        print(f"Avg Cost/Request:   {avg_cost:>10.2f} credits")

    # Show by method
    if "credits_by_method" in section and section["credits_by_method"]:
        print(f"\nBreakdown by Method:")
        print("-" * 80)
        for method, credits in sorted(section["credits_by_method"].items(), key=lambda x: -x[1]):
            print(f"  {method:40} {credits:>10,} credits")

    # Show by provider
    if "credits_by_provider" in section and section["credits_by_provider"]:
        print(f"\nBreakdown by Provider:")
        print("-" * 80)
        for provider, credits in sorted(section["credits_by_provider"].items(), key=lambda x: -x[1]):
            print(f"  {provider:40} {credits:>10,} credits")


def main():
    """Main analysis"""
    print("\n" + "=" * 80)
    print("RPC METRICS ACCURACY ANALYSIS")
    print("=" * 80)

    # Get data
    summary = get_rpc_metrics_summary()
    sections = get_rpc_sections()
    helius = get_helius_latest_usage()

    if not summary:
        print("❌ RPC Metrics API not available at http://localhost:8001")
        print("   Make sure the metrics server is running: python rpc_metrics_api.py")
        sys.exit(1)

    # Instrumented metrics
    print("\n📊 INSTRUMENTED METRICS (Your Code)")
    print("-" * 80)
    print(f"Total Requests:     {summary.get('total_requests', 0):>10,}")
    print(f"Total Credits:      {summary.get('total_credits_instrumented', 0):>10,}")
    print(f"Daily Credits:      {summary.get('daily_credits_instrumented', 0):>10,}")
    print(f"Total Errors:       {summary.get('total_errors', 0):>10,}")
    print(f"Total 429s:         {summary.get('total_429s', 0):>10,}")

    # Actual usage
    if helius:
        print("\n💳 HELIUS ACCOUNT (Actual Usage)")
        print("-" * 80)
        print(f"Remaining:          {helius.get('credits_remaining', 0):>10,} credits")
        print(f"Used Today/Period:  {helius.get('credits_used', 0):>10,} credits")
        print(f"Used This Month:    {helius.get('credits_used_month', 0):>10,} credits")
        print(f"Last Updated:       {helius.get('timestamp', 'Unknown'):>10}")
    else:
        print("\n⚠️  No Helius usage data found")
        print("   Update with: python helius_usage_cli.py update REMAINING USED MONTH")

    # Comparison
    if helius and summary:
        print("\n🔍 RECONCILIATION")
        print("-" * 80)
        instrumented = summary.get("daily_credits_instrumented", 0)
        actual = helius.get("credits_used", 0)

        if instrumented == actual:
            print(f"✅ PERFECT MATCH: {instrumented} credits")
            print("   All RPC calls are properly accounted for!")
        else:
            diff = actual - instrumented
            ratio = actual / instrumented if instrumented > 0 else 0
            print(f"❌ DISCREPANCY FOUND")
            print(f"   Instrumented: {instrumented:,} credits")
            print(f"   Actual:       {actual:,} credits")
            print(f"   Difference:   {diff:+,} credits ({ratio:.1%} of instrumented)")

            if ratio > 2:
                print(f"\n   💡 Analysis:")
                print(f"   - Actual usage is {ratio:.1f}x higher than instrumented")
                print(f"   - Possible causes:")
                print(f"     1. Retries are inflating request counts")
                print(f"     2. Some RPC calls are not being instrumented")
                print(f"     3. Credit costs in CREDIT_SCHEDULE are too low")
            elif ratio < 0.5:
                print(f"\n   💡 Analysis:")
                print(f"   - Instrumented is {1/ratio:.1f}x higher than actual")
                print(f"   - Possible causes:")
                print(f"     1. Calls are in test/mock mode (not executing)")
                print(f"     2. Credit schedule costs are too high")
                print(f"     3. Some recorded calls failed (don't consume credits)")

    # Show sections if requested
    if "--section" in sys.argv:
        idx = sys.argv.index("--section")
        if idx + 1 < len(sys.argv):
            section_name = sys.argv[idx + 1]
            if sections:
                analyze_section(section_name, sections)
    elif sections and "--all-sections" in sys.argv:
        if "sections" in sections:
            for section_name in sorted(sections["sections"].keys()):
                analyze_section(section_name, sections)
    else:
        # Show major sections by credit usage
        if sections and "sections" in sections:
            print("\n📈 TOP SECTIONS BY CREDITS")
            print("-" * 80)
            sorted_sections = sorted(
                sections["sections"].items(),
                key=lambda x: x[1].get("credits_total", 0),
                reverse=True
            )
            for section_name, section_data in sorted_sections[:10]:
                credits = section_data.get("credits_total", 0)
                requests = section_data.get("requests", 0)
                avg = credits / requests if requests > 0 else 0
                print(f"  {section_name:40} {credits:>10,} credits ({requests:>6,} reqs, avg {avg:.2f})")

    print("\n" + "=" * 80)
    print("💡 Next Steps:")
    print("  1. Check Helius dashboard for actual usage")
    print("  2. Run: python helius_usage_cli.py dashboard")
    print("  3. Update snapshots: python helius_usage_cli.py update REMAINING USED MONTH")
    print("  4. Re-run this analysis to reconcile")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
