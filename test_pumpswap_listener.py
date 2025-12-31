#!/usr/bin/env python3
"""
Continuous PumpSwap Listener Test

Runs the real-time WebSocket listener and logs all detected PumpSwap tokens
as they migrate from PumpFun bonding curve to PumpSwap AMM.

This test demonstrates Phase 2 in action:
- Listens to WebSocket for new PumpSwap pool creation events (ONLY)
- Detects token migrations from Pump.fun bonding curve
- Extracts prices from SOL/Token balances in transaction metadata
- Logs PumpSwap detections in real-time with detailed metadata

Usage:
  python test_pumpswap_listener.py

  The script will run continuously, logging:
  - All new PumpSwap pool creation events
  - PumpSwap tokens with 🚀 badge
  - Migration metadata (creator, bonding curve, timestamp)
  - Price extraction logs ([PUMPSWAP PRICE] prefix)
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
        print("  PUMPSWAP CONTINUOUS LISTENER - Phase 2 Real-Time Detection")
        print("="*80)
        print(f"\nStarted at: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("\nListening for:")
        print("  ✓ PumpSwap program pool creation events (ONLY)")
        print("  ✓ PumpFun → PumpSwap token migrations")
        print("  ✓ Price extraction and metadata tracking")
        print("\nOutput will show:")
        print("  [WEBSOCKET]  - Pool creation event detected")
        print("  [PUMPSWAP]   - PumpSwap token identified")
        print("  [BROADCAST]  - Token marked for UI display")
        print("\nPress Ctrl+C to stop listening...\n")
        print("="*80 + "\n")

    def run_listener(self) -> None:
        """Run the WebSocket listener"""
        print("[LISTENER] Starting WebSocket monitoring...")
        print("[LISTENER] Connecting to Solana WebSocket...\n")

        try:
            # Start the background WebSocket monitor
            self.monitor.start_background_monitor()

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

            for i, token in enumerate(self.pumpswap_tokens, 1):
                print(f"{i}. {token.get('name', 'Unknown')} ({token.get('symbol', 'N/A')})")
                print(f"   Mint: {token.get('mint', 'N/A')[:16]}...")
                print(f"   Pool: {token.get('pool', 'N/A')[:16]}...")
                print(f"   Creator: {token.get('creator', 'Unknown')}")
                print(f"   Detected: {token.get('timestamp', 'N/A')}")
                print()

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

    # Explain what's happening
    print("[SETUP] Configuration:")
    print(f"  RPC Endpoint: {listener.monitor.rpc_http_url.split('?')[0]}...")
    print(f"  Database: pumpswap_tokens.db")
    print(f"  Monitoring PumpSwap Program: {listener.monitor.PUMPSWAP_PROGRAM[:16]}...")
    print()

    print("[SETUP] The WebSocket listener will:")
    print("  1. Connect to Solana WebSocket RPC")
    print("  2. Subscribe to PumpSwap program events (ONLY)")
    print("  3. Detect new pool creation transactions from PumpSwap")
    print("  4. Parse pool data from transaction logs")
    print("  5. Extract SOL/Token balances for price calculation")
    print("  6. Extract and store migration metadata")
    print("  7. Broadcast with 🚀 PumpSwap badge")
    print()

    print("[SETUP] Look for these indicators:")
    print("  🚀 DETECTED - PumpSwap token found")
    print("  [PUMPSWAP] - Metadata logging")
    print("  [BROADCAST] - UI broadcast notification")
    print()

    print("[SETUP] NOTE: Detection takes 3-8 seconds after on-chain confirmation")
    print("[SETUP] because the WebSocket must receive, parse, and verify the transaction")
    print()

    # Run listener
    try:
        listener.run_listener()
    finally:
        listener.print_summary()


if __name__ == "__main__":
    main()
