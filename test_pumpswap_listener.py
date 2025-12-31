#!/usr/bin/env python3
"""
Continuous PumpSwap Listener Test

Runs the real-time WebSocket listener and price updater for PumpSwap tokens.
Detects new token migrations from PumpFun bonding curve to PumpSwap AMM
and continuously updates prices for all existing tokens.

This test demonstrates Phase 2 in action:
- Listens to WebSocket for new PumpSwap pool creation events (ONLY)
- Detects token migrations from Pump.fun bonding curve
- Extracts initial prices from SOL/Token balances in transaction metadata
- Continuously updates prices for existing tokens (every 30s-5min)
- Logs PumpSwap detections in real-time with detailed metadata

Usage:
  python test_pumpswap_listener.py

  The script will run continuously, logging:
  - All new PumpSwap pool creation events
  - PumpSwap tokens with 🚀 badge
  - Migration metadata (creator, bonding curve, timestamp)
  - Price extraction logs ([PUMPSWAP PRICE] prefix)
  - Price update cycles ([PRICE UPDATER] prefix)
  - Console output with [PUMPSWAP] and [BROADCAST] prefixes

Press Ctrl+C to stop the listener.
"""

import sys
import json
import signal
from datetime import datetime
from threading import Thread
from typing import Dict, List, Optional

sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')
from main import TokenMonitor, PumpSwapDatabase


class ContinuousPumpSwapListener:
    """Continuous listener for PumpSwap token migrations"""

    def __init__(self):
        self.monitor = TokenMonitor()
        self.db = PumpSwapDatabase()
        self.detected_tokens: List[Dict] = []
        self.pumpswap_tokens: List[Dict] = []
        self.start_time = datetime.now()
        self.is_running = True

    def print_header(self) -> None:
        """Print startup header"""
        print("\n" + "="*80)
        print("  PUMPSWAP LISTENER - New Launches & Price Updates")
        print("="*80)
        print(f"\nStarted: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("\nMonitoring:")
        print("  • New PumpSwap token launches (Pump.fun migrations)")
        print("  • Token price extraction and updates")
        print("\nWatch for:")
        print("  🚀 DETECTED - New token launch")
        print("  [PUMPSWAP PRICE] - Token price")
        print("\nPress Ctrl+C to stop\n")
        print("="*80 + "\n")

    def run_listener(self) -> None:
        """Run the WebSocket listener"""
        print("[LISTENER] Connecting to Solana WebSocket...\n")

        try:
            # Start the background WebSocket monitor
            self.monitor.start_background_monitor()

            # Also start price updater to continuously update existing tokens
            self.monitor.start_price_updater()

            # Keep the listener running
            import time
            while self.is_running:
                time.sleep(1)

        except KeyboardInterrupt:
            print("\n\n[LISTENER] Stopping WebSocket listener...")
            self.is_running = False

        except Exception as e:
            print(f"\n[LISTENER] Error: {e}")
            self.is_running = False

    def print_summary(self) -> None:
        """Print final summary of detections"""
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()

        print("\n" + "="*80)
        print("  LISTENER SUMMARY")
        print("="*80)

        print(f"\nSession Duration: {duration:.1f} seconds")
        print(f"Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Stopped: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")

        print(f"\nTotal Pools Detected: {len(self.detected_tokens)}")
        print(f"PumpSwap Tokens Found: {len(self.pumpswap_tokens)}")

        if self.pumpswap_tokens:
            print(f"\n{'='*80}")
            print("  DETECTED PUMPSWAP TOKENS")
            print(f"{'='*80}\n")
            print(f"Total PumpSwap tokens detected: {len(self.pumpswap_tokens)}")

        print("="*80)
        print("\nTo keep listening, run the script again.")
        print("To stop, press Ctrl+C.\n")

    def signal_handler(self, sig, frame):
        """Handle Ctrl+C gracefully"""
        print("\n\n[LISTENER] Caught interrupt signal, shutting down...")
        self.is_running = False
        self.monitor.is_running = False


def main():
    """Run continuous PumpSwap listener"""
    listener = ContinuousPumpSwapListener()

    # Set up signal handler for Ctrl+C
    signal.signal(signal.SIGINT, listener.signal_handler)

    # Print startup info
    listener.print_header()

    # Minimal setup info
    print("[SETUP] Monitoring PumpSwap for new token migrations and price updates\n")

    # Run listener
    try:
        listener.run_listener()
    finally:
        listener.print_summary()


if __name__ == "__main__":
    main()
