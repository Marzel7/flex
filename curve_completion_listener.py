#!/usr/bin/env python3
"""
Pump.Fun Curve Completion Listener

Monitors pump.fun tokens for bonding curve completion and runs analysis
on curve completion to detect rug probability during AMM gap window.

Workflow:
1. Monitor token for curve completion signals
2. When curve_complete = True, run pre-migration analyzer
3. Score AMM gap window rug risk
4. Alert if high risk detected
"""

import time
import sys
from curve_completion_detector import CurveCompletionDetector
from pump_fun_pre_migration_analyzer import PumpFunPreMigrationAnalyzer


class CurveCompletionListener:
    """
    Listens for pump.fun curve completion and runs analyzer on completion.
    """

    def __init__(self, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        self.rpc_url = rpc_url
        self.monitoring = {}  # {token_mint: detector_instance}
        self.completed = {}   # {token_mint: analysis_result}

    def monitor_token(self, token_mint: str):
        """Start monitoring a token for curve completion"""
        if token_mint not in self.monitoring:
            detector = CurveCompletionDetector(token_mint, self.rpc_url)
            self.monitoring[token_mint] = {
                "detector": detector,
                "checks": 0,
                "started": time.time()
            }
            print(f"[LISTENER] 📊 Started monitoring {token_mint[:16]}...", flush=True)

    def check_completion(self, token_mint: str, run_analyzer: bool = True) -> bool:
        """
        Check if token curve is complete.

        Args:
            token_mint: Token to check
            run_analyzer: If True, run analyzer when completion detected

        Returns:
            True if curve is complete
        """
        if token_mint not in self.monitoring:
            self.monitor_token(token_mint)

        monitor = self.monitoring[token_mint]
        detector = monitor["detector"]
        monitor["checks"] += 1

        # Run detection check
        curve_complete = detector.detect(verbose=False)

        if curve_complete and token_mint not in self.completed:
            elapsed = time.time() - monitor["started"]
            print(f"\n[LISTENER] 🔴 CURVE COMPLETE: {token_mint}")
            print(f"[LISTENER] Detected after {elapsed:.1f} seconds ({monitor['checks']} checks)", flush=True)

            # Mark as completed
            self.completed[token_mint] = {
                "detector_result": detector.summary(),
                "completed_at": time.time(),
                "elapsed_seconds": elapsed
            }

            # Run analyzer if requested
            if run_analyzer:
                self._run_completion_analysis(token_mint)

        return curve_complete

    def _run_completion_analysis(self, token_mint: str):
        """Run pre-migration analyzer when curve completes"""
        print(f"\n[LISTENER] 🔍 Running completion analysis for {token_mint}...", flush=True)

        try:
            analyzer = PumpFunPreMigrationAnalyzer(token_mint, rpc_url=self.rpc_url)
            analyzer.fetch_curve_activity(limit=200)
            analyzer.print_summary()

            summary = analyzer.summary()
            amm_risk = summary.get("amm_rug_probability", 0.0)

            # Alert based on risk level
            if amm_risk >= 0.85:
                print(f"\n[LISTENER] ☠️  CRITICAL RUG WARNING: {token_mint}")
                print(f"[LISTENER] AMM Risk Score: {amm_risk} - Almost certain rug", flush=True)
            elif amm_risk >= 0.6:
                print(f"\n[LISTENER] 🔴 HIGH RUG RISK: {token_mint}")
                print(f"[LISTENER] AMM Risk Score: {amm_risk} - High probability rug", flush=True)
            elif amm_risk >= 0.3:
                print(f"\n[LISTENER] 🟡 MEDIUM RUG RISK: {token_mint}")
                print(f"[LISTENER] AMM Risk Score: {amm_risk} - Moderate rug risk", flush=True)
            else:
                print(f"\n[LISTENER] 🟢 LOW RUG RISK: {token_mint}")
                print(f"[LISTENER] AMM Risk Score: {amm_risk} - Relatively safe", flush=True)

            # Store analysis result
            self.completed[token_mint]["analysis"] = summary

        except Exception as e:
            print(f"[LISTENER] ❌ Analysis failed for {token_mint}: {e}", flush=True)

    def get_status(self, token_mint: str) -> dict:
        """Get current monitoring status for a token"""
        if token_mint not in self.monitoring:
            return {"status": "not_monitored"}

        monitor = self.monitoring[token_mint]
        is_complete = token_mint in self.completed

        return {
            "status": "complete" if is_complete else "monitoring",
            "checks_performed": monitor["checks"],
            "elapsed_seconds": time.time() - monitor["started"],
            "completion_data": self.completed.get(token_mint)
        }

    def print_summary(self):
        """Print summary of all monitored tokens"""
        print(f"\n{'='*70}")
        print(f"CURVE COMPLETION LISTENER SUMMARY")
        print(f"{'='*70}")
        print(f"Monitoring:  {len(self.monitoring)} tokens")
        print(f"Completed:   {len(self.completed)} tokens")

        if self.completed:
            print(f"\nCompleted tokens:")
            for token_mint, data in self.completed.items():
                amm_risk = data.get("analysis", {}).get("amm_rug_probability", "?")
                risk_level = data.get("analysis", {}).get("amm_risk_level", "?")
                elapsed = data.get("elapsed_seconds", 0)
                print(f"  {token_mint[:16]}... - AMM Risk: {amm_risk} {risk_level} (detected in {elapsed:.1f}s)")

        print(f"{'='*70}\n")


def main():
    """
    Example usage: Monitor a token for curve completion
    """
    if len(sys.argv) < 2:
        print("Usage: python3 curve_completion_listener.py <token_mint> [rpc_url]")
        print("\nExample:")
        print("  python3 curve_completion_listener.py 885yGwvQKEejqF8WCUc2WGJFn6AKSBSHmBUoN5iVpump")
        print("\nThis will:")
        print("  1. Monitor the token for curve completion signals")
        print("  2. When complete, run pre-migration analyzer")
        print("  3. Score AMM gap window rug risk")
        print("  4. Alert if high risk")
        sys.exit(1)

    token_mint = sys.argv[1]
    default_rpc = "https://mainnet.helius-rpc.com/?api-key=a132b19d-9b44-4c71-8e6f-d320d9f351c6"
    rpc_url = sys.argv[2] if len(sys.argv) > 2 else default_rpc

    listener = CurveCompletionListener(rpc_url)
    listener.monitor_token(token_mint)

    print(f"[LISTENER] Monitoring {token_mint} for curve completion...")
    print(f"[LISTENER] Press Ctrl+C to exit\n")

    check_count = 0
    try:
        while True:
            check_count += 1
            print(f"\n[LISTENER] Check #{check_count} at {time.strftime('%H:%M:%S')}")

            if listener.check_completion(token_mint, run_analyzer=True):
                print(f"[LISTENER] ✅ Curve completion detected and analyzed!")
                listener.print_summary()
                break

            # Check every 10 seconds
            print(f"[LISTENER] Next check in 10 seconds...")
            time.sleep(10)

    except KeyboardInterrupt:
        print(f"\n[LISTENER] Interrupted by user")
        listener.print_summary()


if __name__ == "__main__":
    main()
