#!/usr/bin/env python3
"""
Integrated Test: Pump.Fun Curve Listener + Pre-Migration Analyzer

Tests the complete workflow:
1. Starts the curve listener to detect and monitor tokens
2. When tokens are detected, analyzes them with pre-migration metrics
3. Stores analysis results for purchase strategy decisions
4. Queries and displays the stored analysis

This test can be run standalone or as part of main.py.

Usage:
  python3 test_curve_listener_integration.py [--duration <seconds>] [--query-only]

Examples:
  # Run for 60 seconds, detect and analyze tokens
  python3 test_curve_listener_integration.py --duration 60

  # Query previously analyzed tokens (no listening)
  python3 test_curve_listener_integration.py --query-only

  # Run until stopped (Ctrl+C)
  python3 test_curve_listener_integration.py
"""

import sys
import os
import asyncio
import time
import argparse
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pumpfun_curve_listener import PumpFunCurveListener
from query_token_analysis import TokenAnalysisQuery


class IntegratedTest:
    """Integration test for curve listener + analyzer"""

    def __init__(self):
        self.listener = PumpFunCurveListener()
        self.query = TokenAnalysisQuery()
        self.start_time = time.time()

    def print_header(self, title):
        """Print formatted header"""
        print(f"\n{'='*80}")
        print(f"  {title}")
        print(f"{'='*80}\n")

    def print_status(self):
        """Print current listener status"""
        elapsed = int(time.time() - self.start_time)
        print(f"\n[STATUS] Elapsed: {elapsed}s")
        print(f"[STATUS] Detected mints: {len(self.listener.seen_mints)}")
        print(f"[STATUS] Filtered mints: {len(self.listener.filtered_mints)}")
        print(f"[STATUS] Analyzed tokens: {len(self.listener.analyzed_tokens)}")

    async def run_listener(self, duration=None):
        """Run listener for specified duration"""
        self.print_header("🚀 STARTING CURVE LISTENER")
        print(f"[LISTENER] Monitoring pump.fun bonding curves...")
        print(f"[LISTENER] Market cap range: $50k - $80k USD")
        print(f"[LISTENER] Analysis: Pre-migration rug risk (14 metrics)")

        if duration:
            print(f"[LISTENER] Running for {duration} seconds...")
        else:
            print(f"[LISTENER] Running indefinitely (press Ctrl+C to stop)...")

        try:
            if duration:
                # Run for specified duration
                await asyncio.wait_for(
                    self.listener.listen(),
                    timeout=duration
                )
            else:
                # Run indefinitely
                await self.listener.listen()
        except asyncio.TimeoutError:
            print(f"\n[LISTENER] Duration {duration}s reached")
        except KeyboardInterrupt:
            print(f"\n[LISTENER] Interrupted by user")

    def query_results(self):
        """Query and display analysis results"""
        self.print_header("📊 QUERYING ANALYSIS RESULTS")

        # Get all analyzed tokens
        tokens = self.query.get_all_analysis()

        if not tokens:
            print("❌ No analyzed tokens found.")
            print("\nRun the listener first to detect and analyze tokens:")
            print("  python3 test_curve_listener_integration.py --duration 300")
            return

        print(f"✅ Found {len(tokens)} analyzed tokens\n")

        # Print table
        self.query.print_table(tokens)

        # Print detailed analysis for each token
        print(f"\n{'='*80}")
        print(f"  DETAILED ANALYSIS")
        print(f"{'='*80}")

        for i, token in enumerate(tokens, 1):
            print(f"\n[{i}/{len(tokens)}] Token Analysis:")
            self.query.print_token_summary(token)
            self.query.print_purchase_strategy(token)
            print()

        # Summary statistics
        self.print_analysis_summary(tokens)

    def print_analysis_summary(self, tokens):
        """Print summary statistics of analyzed tokens"""
        if not tokens:
            return

        self.print_header("📈 ANALYSIS SUMMARY")

        # Risk distribution
        low_risk = sum(1 for t in tokens if t['amm_rug_probability'] <= 0.25)
        medium_risk = sum(1 for t in tokens if 0.25 < t['amm_rug_probability'] <= 0.50)
        high_risk = sum(1 for t in tokens if 0.50 < t['amm_rug_probability'] <= 0.75)
        critical_risk = sum(1 for t in tokens if t['amm_rug_probability'] > 0.75)

        print(f"Risk Distribution:")
        print(f"  🟢 Low Risk (<=25%):     {low_risk} tokens")
        print(f"  🟡 Medium Risk (25-50%): {medium_risk} tokens")
        print(f"  🔴 High Risk (50-75%):   {high_risk} tokens")
        print(f"  ☠️  Critical (>75%):     {critical_risk} tokens")

        # Average metrics
        avg_rug_prob = sum(t['amm_rug_probability'] for t in tokens) / len(tokens)
        avg_mint_conc = sum(t['mint_concentration'] for t in tokens) / len(tokens)
        avg_unique = sum(t['unique_minters_ratio'] for t in tokens) / len(tokens)
        avg_events = sum(t['events_parsed'] for t in tokens) / len(tokens)

        print(f"\nAverage Metrics:")
        print(f"  • Rug Probability: {avg_rug_prob:.1%}")
        print(f"  • Mint Concentration: {avg_mint_conc:.3f}")
        print(f"  • Unique Minters: {avg_unique:.3f}")
        print(f"  • Events Analyzed: {avg_events:.0f}")

        # Safest and riskiest
        safest = min(tokens, key=lambda t: t['amm_rug_probability'])
        riskiest = max(tokens, key=lambda t: t['amm_rug_probability'])

        print(f"\nExtreme Cases:")
        print(f"  ✅ Safest: {safest['mint'][:20]}... ({safest['amm_rug_probability']:.1%} rug)")
        print(f"  ☠️  Riskiest: {riskiest['mint'][:20]}... ({riskiest['amm_rug_probability']:.1%} rug)")

    def run_query_only(self):
        """Run in query-only mode (no listening)"""
        self.print_header("📊 QUERY-ONLY MODE")
        self.query_results()

    async def run_with_periodic_query(self, duration=None):
        """Run listener with periodic query updates"""

        async def listener_task():
            await self.run_listener(duration)

        async def query_task():
            """Query results every 30 seconds"""
            while True:
                await asyncio.sleep(30)
                self.print_status()

        tasks = [
            asyncio.create_task(listener_task()),
            asyncio.create_task(query_task())
        ]

        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            print(f"\n[ERROR] {e}")
        finally:
            # Cancel remaining tasks
            for task in tasks:
                if not task.done():
                    task.cancel()

        # Show final results
        print()
        self.query_results()


async def main():
    parser = argparse.ArgumentParser(
        description="Integrated test: Curve Listener + Pre-Migration Analyzer"
    )
    parser.add_argument(
        "--duration",
        type=int,
        help="Run listener for specified seconds (default: indefinite)"
    )
    parser.add_argument(
        "--query-only",
        action="store_true",
        help="Query existing analysis only (no listening)"
    )

    args = parser.parse_args()

    test = IntegratedTest()

    try:
        if args.query_only:
            # Query-only mode
            test.run_query_only()
        else:
            # Run listener with periodic query updates
            await test.run_with_periodic_query(duration=args.duration)
    except KeyboardInterrupt:
        print(f"\n\n[TEST] Interrupted by user")
        test.query_results()
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
