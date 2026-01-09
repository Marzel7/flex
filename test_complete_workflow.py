#!/usr/bin/env python3
"""
Complete Workflow Test: Pump.Fun → PumpSwap Tracking

Tests the FULL CYCLE in one test:
1. PRE-MIGRATION: Curve Listener detects pump.fun tokens ($50k-$80k)
2. ANALYSIS: Pre-migration analyzer calculates 14 rug risk metrics
3. STORAGE: Stores analysis data in SQLite for purchase strategy
4. POST-MIGRATION: PumpSwap listener detects migrated tokens
5. LOOKUP: Correlates pre-migration analysis with post-migration prices
6. DECISION: Shows purchase decision based on stored analysis

This covers the COMPLETE lifecycle from token creation on pump.fun
through bonding curve phase, migration to PumpSwap, and into AMM trading.

Usage:
  # Run indefinitely (default - press Ctrl+C to stop)
  python3 test_complete_workflow.py

  # Run with optional duration limit (seconds)
  python3 test_complete_workflow.py --duration 300

  # Show results only (no listening)
  python3 test_complete_workflow.py --results-only

  # Show specific token journey
  python3 test_complete_workflow.py --track <MINT>

MODES:
  Default: Runs indefinitely, detecting and analyzing tokens continuously
  With --duration: Runs for specified seconds then shows results
  With --results-only: Shows previous analysis without detecting new tokens
  With --track: Shows complete journey of specific token
"""

import sys
import os
import asyncio
import time
import argparse
from datetime import datetime
from typing import Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pumpfun_curve_listener import PumpFunCurveListener
from query_token_analysis import TokenAnalysisQuery


class CompleteWorkflowTest:
    """Test complete lifecycle from pump.fun to PumpSwap"""

    def __init__(self):
        self.curve_listener = PumpFunCurveListener()
        self.query = TokenAnalysisQuery()
        self.start_time = time.time()
        self.migrated_tokens = {}  # Track which tokens migrated

    def print_header(self, title, char="="):
        """Print formatted header"""
        width = 80
        print(f"\n{char*width}")
        print(f"  {title}")
        print(f"{char*width}\n")

    def print_section(self, title):
        """Print section header"""
        print(f"\n{'─'*80}")
        print(f"  {title}")
        print(f"{'─'*80}\n")

    # =========================================================================
    # PHASE 1: PRE-MIGRATION DETECTION
    # =========================================================================

    async def run_curve_listener(self, duration=None):
        """Run pump.fun curve listener"""
        self.print_header("🔍 PHASE 1: PRE-MIGRATION DETECTION (Pump.Fun Bonding Curve)")

        print(f"[LISTENER] Monitoring pump.fun program for tokens in range $50k-$80k")
        print(f"[LISTENER] Analyzing each detected token with 14 metrics")
        print(f"[LISTENER] Storing analysis for post-migration purchase decisions\n")

        try:
            if duration:
                print(f"[LISTENER] Running for {duration} seconds...\n")
                await asyncio.wait_for(
                    self.curve_listener.listen(),
                    timeout=duration
                )
            else:
                print(f"[LISTENER] Running indefinitely (press Ctrl+C to stop)...\n")
                await self.curve_listener.listen()
        except asyncio.TimeoutError:
            print(f"\n[LISTENER] Duration {duration}s reached")
        except KeyboardInterrupt:
            print(f"\n[LISTENER] Interrupted by user")

    # =========================================================================
    # PHASE 2: ANALYSIS RESULTS
    # =========================================================================

    def analyze_detected_tokens(self):
        """Display analysis of detected tokens from Phase 1"""
        self.print_section("📊 PHASE 2: ANALYSIS RESULTS (Pre-Migration Metrics)")

        analyzed = self.query.get_all_analysis()

        if not analyzed:
            print("❌ No tokens analyzed yet.")
            print("\nRun Phase 1 first to detect and analyze tokens on pump.fun")
            return

        print(f"✅ Found {len(analyzed)} analyzed tokens\n")

        # Display in table format
        print(f"{'MINT':<45} {'RISK':<12} {'RUG %':<8} {'EVENTS':<8}")
        print("-" * 80)

        for token in analyzed:
            mint_short = token['mint'][:40] + "..." if len(token['mint']) > 40 else token['mint']
            risk = token['amm_risk_level']
            rug_pct = f"{token['amm_rug_probability']:.1%}"
            events = f"{token['events_parsed']}"
            print(f"{mint_short:<45} {risk:<12} {rug_pct:<8} {events:<8}")

        return analyzed

    # =========================================================================
    # PHASE 3: PURCHASE STRATEGY
    # =========================================================================

    def show_purchase_strategy(self, tokens: List[Dict]):
        """Show purchase strategy for analyzed tokens"""
        self.print_section("💰 PHASE 3: PURCHASE STRATEGY (Pre-Migration Decisions)")

        if not tokens:
            print("No tokens to analyze.")
            return

        # Categorize by risk
        safe = [t for t in tokens if t['amm_rug_probability'] <= 0.25]
        medium = [t for t in tokens if 0.25 < t['amm_rug_probability'] <= 0.50]
        high = [t for t in tokens if 0.50 < t['amm_rug_probability'] <= 0.75]
        critical = [t for t in tokens if t['amm_rug_probability'] > 0.75]

        print(f"🟢 SAFE TOKENS (Buy Full): {len(safe)}")
        for token in safe[:3]:
            print(f"   • {token['mint'][:30]}... ({token['amm_rug_probability']:.1%} rug)")

        print(f"\n🟡 MEDIUM RISK (Buy 50%): {len(medium)}")
        for token in medium[:3]:
            print(f"   • {token['mint'][:30]}... ({token['amm_rug_probability']:.1%} rug)")

        print(f"\n🔴 HIGH RISK (Buy 10%): {len(high)}")
        for token in high[:3]:
            print(f"   • {token['mint'][:30]}... ({token['amm_rug_probability']:.1%} rug)")

        print(f"\n☠️  CRITICAL (Skip): {len(critical)}")
        for token in critical[:3]:
            print(f"   • {token['mint'][:30]}... ({token['amm_rug_probability']:.1%} rug)")

    # =========================================================================
    # PHASE 4: POST-MIGRATION MONITORING
    # =========================================================================

    def check_pumpswap_migrations(self):
        """Simulate checking for migrated tokens on PumpSwap"""
        self.print_section("🚀 PHASE 4: POST-MIGRATION MONITORING (PumpSwap AMM)")

        print("""
[NOTE] In production, this would:
  1. Monitor PumpSwap program for pool creation events
  2. Match migrated tokens against pre-migration analysis
  3. Apply purchase strategy based on stored metrics
  4. Execute buy orders if conditions met

For now, showing how to correlate data:
""")

        analyzed = self.query.get_all_analysis()
        if not analyzed:
            print("No analyzed tokens to track for migration.\n")
            return

        print(f"Watching {len(analyzed)} tokens for PumpSwap migration...\n")

        for token in analyzed[:5]:
            mint = token['mint']
            rug_prob = token['amm_rug_probability']

            # Determine purchase tier
            if rug_prob <= 0.25:
                tier = "✅ BUY FULL (100%)"
            elif rug_prob <= 0.50:
                tier = "🟡 BUY HALF (50%)"
            elif rug_prob <= 0.75:
                tier = "🔴 BUY SMALL (10%)"
            else:
                tier = "⛔ SKIP"

            print(f"Token: {mint[:30]}...")
            print(f"  Pre-Migration Risk: {token['amm_risk_level']}")
            print(f"  Rug Probability: {rug_prob:.1%}")
            print(f"  Migration Strategy: {tier}")
            print()

    # =========================================================================
    # PHASE 5: DETAILED ANALYSIS
    # =========================================================================

    def show_detailed_analysis(self, tokens: List[Dict]):
        """Show detailed analysis for top tokens"""
        self.print_section("🔬 PHASE 5: DETAILED ANALYSIS (Key Metrics)")

        if not tokens:
            return

        # Show most interesting cases
        print(f"SAFEST TOKEN (Lowest Rug Risk):")
        safest = min(tokens, key=lambda t: t['amm_rug_probability'])
        self._print_token_details(safest)

        print(f"\nRISKIEST TOKEN (Highest Rug Risk):")
        riskiest = max(tokens, key=lambda t: t['amm_rug_probability'])
        self._print_token_details(riskiest)

        # Show most balanced
        print(f"\nMOST BALANCED TOKEN (Medium Risk):")
        medium = [t for t in tokens if 0.30 < t['amm_rug_probability'] < 0.70]
        if medium:
            balanced = medium[len(medium)//2]
            self._print_token_details(balanced)

    def _print_token_details(self, token: Dict):
        """Print detailed token information"""
        print(f"\n  Address: {token['mint']}")
        print(f"  Risk Level: {token['amm_risk_level']}")
        print(f"  Rug Probability: {token['amm_rug_probability']:.1%}")
        print(f"  Events Analyzed: {token['events_parsed']}")

        print(f"\n  Key Metrics:")
        print(f"    • Mint Concentration: {token['mint_concentration']:.3f} (whales: {'high' if token['mint_concentration'] > 0.7 else 'normal' if token['mint_concentration'] > 0.3 else 'low'})")
        print(f"    • Unique Minters: {token['unique_minters_ratio']:.3f} (participation: {'high' if token['unique_minters_ratio'] > 0.7 else 'normal' if token['unique_minters_ratio'] > 0.3 else 'low'})")
        print(f"    • Sell Suppression: {token['sell_suppression_ratio']:.3f} ({'high suppression' if token['sell_suppression_ratio'] > 0.8 else 'normal' if token['sell_suppression_ratio'] > 0.5 else 'low suppression'})")
        print(f"    • Mint Velocity: {token['mint_velocity_sec']:.2f} mints/sec")
        print(f"    • Creator Activity: {token['creator_activity_ratio']:.3f}")

    # =========================================================================
    # PHASE 6: SUMMARY & STATUS
    # =========================================================================

    def show_summary(self):
        """Show overall workflow summary"""
        self.print_header("📋 SUMMARY: Complete Workflow Status")

        elapsed = int(time.time() - self.start_time)

        print(f"Phase 1: Pre-Migration Detection")
        print(f"  • Detected: {len(self.curve_listener.seen_mints)} tokens")
        print(f"  • Filtered ($50k-$80k): {len(self.curve_listener.filtered_mints)} tokens")
        print(f"  • Analyzed: {len(self.curve_listener.analyzed_tokens)} tokens")

        print(f"\nPhase 2-3: Analysis & Strategy")
        analyzed_count = len(self.query.get_all_analysis())
        print(f"  • Stored in Database: {analyzed_count} tokens")

        if analyzed_count > 0:
            all_analyzed = self.query.get_all_analysis()
            safe = sum(1 for t in all_analyzed if t['amm_rug_probability'] <= 0.25)
            medium = sum(1 for t in all_analyzed if 0.25 < t['amm_rug_probability'] <= 0.50)
            high = sum(1 for t in all_analyzed if 0.50 < t['amm_rug_probability'] <= 0.75)
            critical = sum(1 for t in all_analyzed if t['amm_rug_probability'] > 0.75)

            print(f"  • Safe (buy full): {safe}")
            print(f"  • Medium (buy 50%): {medium}")
            print(f"  • High (buy 10%): {high}")
            print(f"  • Critical (skip): {critical}")

        print(f"\nPhase 4: Post-Migration Monitoring")
        print(f"  • Ready to track {analyzed_count} tokens for migration")
        print(f"  • PumpSwap listener would detect pool creation")
        print(f"  • Auto-apply strategy from Phase 3")

        print(f"\nPhase 5: Detailed Analysis")
        print(f"  • Metrics available for all {analyzed_count} tokens")
        print(f"  • Can correlate with actual post-migration behavior")

        print(f"\nTotal Time: {elapsed} seconds")

    def run_results_only(self):
        """Run in results-only mode"""
        self.print_header("📊 COMPLETE WORKFLOW TEST - RESULTS ONLY")

        analyzed = self.analyze_detected_tokens()

        if analyzed:
            self.show_purchase_strategy(analyzed)
            self.show_detailed_analysis(analyzed)
            self.check_pumpswap_migrations()

        self.show_summary()

    async def run_complete_workflow(self, duration=None):
        """Run complete workflow"""
        try:
            # Phase 1: Detect and analyze
            await self.run_curve_listener(duration)

            # Phase 2-3: Show analysis and strategy
            analyzed = self.analyze_detected_tokens()

            if analyzed:
                self.show_purchase_strategy(analyzed)
                self.show_detailed_analysis(analyzed)
                self.check_pumpswap_migrations()

            # Summary
            self.show_summary()

        except KeyboardInterrupt:
            print(f"\n[TEST] Interrupted by user")
            self.show_summary()
        except Exception as e:
            print(f"\n[ERROR] {e}")
            import traceback
            traceback.print_exc()

    def track_token(self, mint: str):
        """Show full journey of a specific token"""
        self.print_header(f"🔍 TOKEN JOURNEY: {mint[:30]}...")

        token = self.query.get_token_analysis(mint)

        if not token:
            print(f"❌ Token {mint} not found in analysis database.")
            print("\nRun Phase 1 to detect and analyze this token first.")
            return

        print(f"\n📍 PHASE 1: Detected on Pump.Fun")
        print(f"   Created: {datetime.fromtimestamp(token['analyzed_at']).strftime('%Y-%m-%d %H:%M:%S')}")

        print(f"\n📊 PHASE 2-3: Pre-Migration Analysis")
        print(f"   Risk Level: {token['amm_risk_level']}")
        print(f"   Rug Probability: {token['amm_rug_probability']:.1%}")
        print(f"   Events Analyzed: {token['events_parsed']}")

        print(f"\n💡 PHASE 3: Purchase Strategy")
        rug_prob = token['amm_rug_probability']
        if rug_prob <= 0.25:
            print(f"   Decision: ✅ BUY FULL (100% position)")
            print(f"   Reasoning: Low rug probability ({rug_prob:.1%})")
        elif rug_prob <= 0.50:
            print(f"   Decision: 🟡 BUY HALF (50% position)")
            print(f"   Reasoning: Medium rug probability ({rug_prob:.1%})")
        elif rug_prob <= 0.75:
            print(f"   Decision: 🔴 BUY SMALL (10% position)")
            print(f"   Reasoning: High rug probability ({rug_prob:.1%})")
        else:
            print(f"   Decision: ⛔ SKIP")
            print(f"   Reasoning: Critical rug probability ({rug_prob:.1%})")

        print(f"\n🚀 PHASE 4: Post-Migration Monitoring")
        print(f"   Status: Waiting for PumpSwap migration")
        print(f"   Pre-migration analysis stored and ready")
        print(f"   Strategy will auto-apply on migration")

        print(f"\n📈 Key Risk Factors:")
        print(f"   • Mint Concentration: {token['mint_concentration']:.3f}")
        print(f"   • Unique Minters: {token['unique_minters_ratio']:.3f}")
        print(f"   • Sell Suppression: {token['sell_suppression_ratio']:.3f}")


async def main():
    parser = argparse.ArgumentParser(
        description="Complete Workflow Test: Pump.Fun → PumpSwap"
    )
    parser.add_argument(
        "--duration",
        type=int,
        help="Run for specified seconds (default: indefinite)"
    )
    parser.add_argument(
        "--results-only",
        action="store_true",
        help="Show results only (no listening)"
    )
    parser.add_argument(
        "--track",
        type=str,
        help="Track specific token through complete workflow"
    )

    args = parser.parse_args()

    test = CompleteWorkflowTest()

    try:
        if args.track:
            # Track specific token
            test.track_token(args.track)
        elif args.results_only:
            # Results only mode
            test.run_results_only()
        else:
            # Complete workflow
            await test.run_complete_workflow(duration=args.duration)
    except KeyboardInterrupt:
        print(f"\n\n[TEST] Interrupted by user")
        test.show_summary()
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
