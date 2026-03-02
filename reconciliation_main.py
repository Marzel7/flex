#!/usr/bin/env python3
"""
Main orchestrator for reconciliation system.
Collects snapshots, runs reconciliation, and reports results.

Usage:
    # One-time reconciliation
    python reconciliation_main.py

    # Schedule (every 5 minutes)
    */5 * * * * cd /path/to/flex && python reconciliation_main.py

    # View latest results
    python reconciliation_main.py --latest

    # View daily report
    python reconciliation_main.py --daily 2025-03-02

    # Health check
    python reconciliation_main.py --health
"""

import sys
import argparse
from datetime import datetime, timezone

from reconciliation_schema import init_reconciliation_schema
from reconciliation_collectors import HeliusCliCollector, InternalMetricsCollector
from reconciliation_engine import ReconciliationEngine
from reconciliation_reporter import ReconciliationReporter


def main():
    parser = argparse.ArgumentParser(
        description="Reconciliation system for Helius CLI vs FLEX metrics"
    )
    parser.add_argument(
        "--init", action="store_true", help="Initialize schema (one-time)"
    )
    parser.add_argument(
        "--collect", action="store_true", help="Collect snapshots (default action)"
    )
    parser.add_argument(
        "--reconcile", action="store_true", help="Run reconciliation"
    )
    parser.add_argument(
        "--latest", action="store_true", help="Show latest reconciliation result"
    )
    parser.add_argument(
        "--daily", type=str, help="Show daily report for date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--health", action="store_true", help="Show health check (last 7 days)"
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default="http://localhost:8001/metrics/rpc",
        help="FLEX metrics API URL",
    )

    args = parser.parse_args()

    # Default: collect + reconcile
    if not any([args.init, args.latest, args.daily, args.health]):
        args.collect = True
        args.reconcile = True

    if args.init:
        print("[MAIN] Initializing reconciliation schema...")
        init_reconciliation_schema()

    if args.collect:
        print("[MAIN] Collecting snapshots...")

        # Collect Helius CLI
        print("[MAIN] Collecting Helius CLI snapshot...")
        helius_snap = HeliusCliCollector.collect()
        if helius_snap:
            HeliusCliCollector.store_snapshot(helius_snap)
        else:
            print("[MAIN] ⚠️ No Helius snapshot collected")

        # Collect internal metrics
        print("[MAIN] Collecting FLEX metrics snapshot...")
        internal_snap = InternalMetricsCollector.collect(api_url=args.api_url)
        if internal_snap:
            InternalMetricsCollector.store_snapshot(internal_snap)
        else:
            print("[MAIN] ⚠️ No internal snapshot collected")

    if args.reconcile:
        print("[MAIN] Running reconciliation...")
        result = ReconciliationEngine.reconcile_and_store()
        if not result:
            print("[MAIN] ⚠️ Reconciliation failed")

    if args.latest:
        print("[MAIN] Fetching latest reconciliation...")
        ReconciliationReporter.latest_reconciliation()

    if args.daily:
        print(f"[MAIN] Fetching daily report for {args.daily}...")
        ReconciliationReporter.daily_reconciliation(date_str=args.daily)

    if args.health:
        print("[MAIN] Running health check...")
        ReconciliationReporter.reconciliation_health()

    print("[MAIN] ✅ Done", flush=True)


if __name__ == "__main__":
    main()
