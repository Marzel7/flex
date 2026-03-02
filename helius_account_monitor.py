#!/usr/bin/env python3
"""
Helius Account Monitor

Real-time monitoring of Helius account credit usage, limits, and billing.

Features:
- Fetch account usage from Helius API
- Track daily/monthly consumption
- Alert on budget limits
- Compare actual vs instrumented metrics
- Export usage history
"""

import os
import requests
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import sqlite3

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()

# Fallback to primary API key if env not set
if not HELIUS_API_KEY:
    RPC_KEYS = [
        ("a132b19d-9b44-4c71-8e6f-d320d9f351c6", "GITHUB"),     # Primary (best quota)
        ("f084fae8-d111-4337-9960-2d9c5e02a726", "MARZEL"),     # Fallback 1
        ("0ae07551-32df-4d9d-af2a-1925fb7f561f", "JEZZA"),      # Fallback 2
        ("3b2917b8-9bed-4e2e-8c05-a74adbc34bb8", "NEW_KEY"),    # Fallback 3
    ]
    HELIUS_API_KEY = RPC_KEYS[0][0]

HELIUS_API_BASE = "https://api.helius.xyz"

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")


def _connect():
    """Create connection with optimal PRAGMA settings"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_history_table():
    """Create helius_account_history table for tracking"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS helius_account_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          credits_used INTEGER,
          credits_remaining INTEGER,
          monthly_budget INTEGER,
          api_calls INTEGER,
          estimated_daily_burn INTEGER,
          recorded_at TIMESTAMP
        )
        """)

        # Create index for fast queries
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_helius_hist_timestamp
        ON helius_account_history(timestamp)
        """)

        conn.commit()
    finally:
        conn.close()


def get_account_usage_from_dashboard() -> Optional[Dict]:
    """
    Try to get live usage from Helius dashboard (requires credentials)

    Returns usage dict or None if scraper not available
    """
    try:
        from helius_dashboard_scraper import scrape_helius_dashboard

        email = os.getenv("HELIUS_EMAIL", "")
        password = os.getenv("HELIUS_PASSWORD", "")

        if not email or not password:
            return None

        print("[HELIUS] 🌐 Fetching from dashboard...", flush=True)
        usage = scrape_helius_dashboard(email, password, headless=True)

        if usage:
            print("[HELIUS] ✅ Got live data from dashboard", flush=True)
            return usage
    except ImportError:
        # Selenium not installed
        pass
    except Exception as e:
        print(f"[HELIUS] ⚠️ Dashboard fetch failed: {str(e)[:100]}", flush=True)

    return None


def get_account_usage() -> Optional[Dict]:
    """
    Get account usage - tries dashboard first, falls back to config

    Returns:
    {
        "credits_used": int,
        "credits_remaining": int,
        "monthly_budget": int,
        "api_calls": int,
        "estimated_daily_burn": int,
        "timestamp": ISO datetime string,
        "source": "dashboard" or "config"
    }

    Order:
    1. Try Helius dashboard (if HELIUS_EMAIL + HELIUS_PASSWORD set)
    2. Fall back to PlanConfig.CURRENT_USAGE (manually synced)
    """
    # Try live dashboard first
    dashboard_usage = get_account_usage_from_dashboard()
    if dashboard_usage:
        # Add estimated daily burn from metrics
        try:
            from rpc_metrics_recorder import get_recorder
            recorder = get_recorder()
            summary = recorder.get_summary()
            burn_rate_per_min = summary.get("credits_burn_rate_per_minute", 0)
            dashboard_usage["estimated_daily_burn"] = int(burn_rate_per_min * 60 * 24)
        except:
            dashboard_usage["estimated_daily_burn"] = 0

        return dashboard_usage

    # Fall back to config
    try:
        from rpc_metrics_config import PlanConfig
        from rpc_metrics_recorder import get_recorder

        usage_config = PlanConfig.CURRENT_USAGE
        recorder = get_recorder()
        summary = recorder.get_summary()

        # Calculate estimated daily burn (from metrics)
        burn_rate_per_min = summary.get("credits_burn_rate_per_minute", 0)
        estimated_daily_burn = int(burn_rate_per_min * 60 * 24)

        credits_used = usage_config.get("credits_used_today", 0)
        credits_remaining = usage_config.get("credits_remaining", 0)
        monthly_budget = credits_used + credits_remaining

        usage = {
            "credits_used": credits_used,
            "credits_remaining": credits_remaining,
            "monthly_budget": monthly_budget,
            "api_calls": summary.get("requests_total", 0),
            "estimated_daily_burn": estimated_daily_burn,
            "timestamp": datetime.now().isoformat(),
            "source": "config",  # From rpc_metrics_config.py (manually synced)
        }

        return usage
    except Exception as e:
        print(f"[HELIUS] ❌ Config error: {str(e)[:100]}", flush=True)
        return None


def record_account_usage(usage: Dict):
    """Store account usage in database history"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            """
        INSERT INTO helius_account_history(
          credits_used,
          credits_remaining,
          monthly_budget,
          api_calls,
          estimated_daily_burn,
          recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                usage.get("credits_used", 0),
                usage.get("credits_remaining", 0),
                usage.get("monthly_budget", 0),
                usage.get("api_calls", 0),
                usage.get("estimated_daily_burn", 0),
                usage.get("timestamp"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_account_status() -> Optional[Dict]:
    """
    Get current account status with comparison to instrumented metrics

    Returns detailed status dict or None
    """
    usage = get_account_usage()
    if not usage:
        return None

    # Get instrumented metrics from recorder
    try:
        from rpc_metrics_recorder import get_recorder

        recorder = get_recorder()
        summary = recorder.get_summary()
    except Exception:
        summary = None

    # Build status
    status = {
        "timestamp": usage["timestamp"],
        "helius_account": {
            "credits_used": usage["credits_used"],
            "credits_remaining": usage["credits_remaining"],
            "monthly_budget": usage["monthly_budget"],
            "api_calls": usage["api_calls"],
            "estimated_daily_burn": usage["estimated_daily_burn"],
            "percent_used": round(
                (usage["credits_used"] / usage["monthly_budget"] * 100)
                if usage["monthly_budget"] > 0
                else 0,
                1,
            ),
        },
        "instrumented_metrics": None,
        "discrepancy": None,
    }

    if summary:
        status["instrumented_metrics"] = {
            "credits_today": summary.get("credits_instrumented_today", 0),
            "burn_rate_per_minute": summary.get("credits_burn_rate_per_minute", 0),
            "requests_total": summary.get("requests_total", 0),
            "errors_total": summary.get("errors_total", 0),
        }

        # Calculate discrepancy
        helius_used = usage["credits_used"]
        our_used = summary.get("credits_instrumented_today", 0)

        if helius_used > 0:
            discrepancy_pct = abs(helius_used - our_used) / helius_used * 100
        else:
            discrepancy_pct = 0

        status["discrepancy"] = {
            "helius_used": helius_used,
            "instrumented_used": our_used,
            "difference": helius_used - our_used,
            "percent_difference": round(discrepancy_pct, 1),
        }

    return status


def print_account_status():
    """Print formatted account status"""
    status = get_account_status()
    if not status:
        print("[HELIUS] ❌ Could not fetch account status", flush=True)
        return

    print("\n" + "=" * 80, flush=True)
    print("[HELIUS] 📊 ACCOUNT STATUS", flush=True)
    print("=" * 80, flush=True)

    # Helius account info
    helius = status["helius_account"]
    print(f"\n💰 Helius Account:", flush=True)
    print(f"   Credits Used:      {helius['credits_used']:>12,}", flush=True)
    print(f"   Credits Remaining: {helius['credits_remaining']:>12,}", flush=True)
    print(f"   Monthly Budget:    {helius['monthly_budget']:>12,}", flush=True)
    print(f"   Usage:             {helius['percent_used']:>12.1f}%", flush=True)
    print(f"   API Calls:         {helius['api_calls']:>12,}", flush=True)
    print(f"   Est. Daily Burn:   {helius['estimated_daily_burn']:>12,}", flush=True)

    # Instrumented metrics
    if status["instrumented_metrics"]:
        inst = status["instrumented_metrics"]
        print(f"\n📈 Our Instrumented Metrics:", flush=True)
        print(f"   Credits Today:     {inst['credits_today']:>12,}", flush=True)
        print(f"   Burn Rate:         {inst['burn_rate_per_minute']:>12.2f} cr/min", flush=True)
        print(f"   Requests:          {inst['requests_total']:>12,}", flush=True)
        print(f"   Errors:            {inst['errors_total']:>12,}", flush=True)

    # Discrepancy
    if status["discrepancy"]:
        disc = status["discrepancy"]
        print(f"\n🔍 Comparison:", flush=True)
        print(f"   Helius Used:       {disc['helius_used']:>12,}", flush=True)
        print(f"   Our Instrumented:  {disc['instrumented_used']:>12,}", flush=True)
        print(f"   Difference:        {disc['difference']:>12,}", flush=True)
        print(f"   % Difference:      {disc['percent_difference']:>12.1f}%", flush=True)

        if disc["percent_difference"] > 20:
            print(
                f"   ⚠️  WARNING: Large discrepancy ({disc['percent_difference']}%)",
                flush=True,
            )
            print(
                "      Check for uninstrumented RPC calls or estimation errors",
                flush=True,
            )

    print("\n" + "=" * 80 + "\n", flush=True)

    # Record in history
    record_account_usage(
        {
            "credits_used": helius["credits_used"],
            "credits_remaining": helius["credits_remaining"],
            "monthly_budget": helius["monthly_budget"],
            "api_calls": helius["api_calls"],
            "estimated_daily_burn": helius["estimated_daily_burn"],
            "timestamp": status["timestamp"],
        }
    )


def get_usage_history(hours: int = 24) -> List[Dict]:
    """Get usage history for last N hours"""
    cutoff = datetime.now() - timedelta(hours=hours)

    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            """
        SELECT
          timestamp,
          credits_used,
          credits_remaining,
          monthly_budget,
          api_calls,
          estimated_daily_burn
        FROM helius_account_history
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 100
        """,
            (cutoff.isoformat(),),
        )

        rows = cur.fetchall()
        return [
            {
                "timestamp": row["timestamp"],
                "credits_used": row["credits_used"],
                "credits_remaining": row["credits_remaining"],
                "monthly_budget": row["monthly_budget"],
                "api_calls": row["api_calls"],
                "estimated_daily_burn": row["estimated_daily_burn"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def export_usage_csv(filepath: str, hours: int = 24):
    """Export usage history to CSV"""
    import csv

    history = get_usage_history(hours=hours)
    if not history:
        print(f"[HELIUS] ℹ️ No history to export", flush=True)
        return

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "credits_used",
                "credits_remaining",
                "monthly_budget",
                "api_calls",
                "estimated_daily_burn",
            ],
        )
        writer.writeheader()
        writer.writerows(history)

    print(f"[HELIUS] ✅ Exported {len(history)} records to {filepath}", flush=True)


def get_alerts(usage: Optional[Dict] = None) -> List[str]:
    """
    Get list of alerts based on account status

    Returns list of alert messages
    """
    if not usage:
        usage = get_account_usage()
        if not usage:
            return []

    alerts = []

    # Budget alerts
    if usage["monthly_budget"] > 0:
        percent_used = (usage["credits_used"] / usage["monthly_budget"]) * 100

        if percent_used >= 95:
            alerts.append(f"🔴 CRITICAL: {percent_used:.1f}% of monthly budget used")
        elif percent_used >= 80:
            alerts.append(f"🟠 WARNING: {percent_used:.1f}% of monthly budget used")
        elif percent_used >= 50:
            alerts.append(f"🟡 INFO: {percent_used:.1f}% of monthly budget used")

    # Burn rate alerts
    if usage["estimated_daily_burn"] > 0:
        days_remaining = (
            usage["credits_remaining"] / usage["estimated_daily_burn"]
            if usage["estimated_daily_burn"] > 0
            else float("inf")
        )

        if days_remaining < 1:
            alerts.append(
                f"🔴 CRITICAL: Less than 1 day of credits remaining at current burn rate"
            )
        elif days_remaining < 7:
            alerts.append(
                f"🟠 WARNING: {days_remaining:.1f} days of credits remaining at current burn rate"
            )

    return alerts


if __name__ == "__main__":
    # Initialize
    ensure_history_table()

    # Fetch and display
    print_account_status()

    # Check alerts
    usage = get_account_usage()
    if usage:
        alerts = get_alerts(usage)
        if alerts:
            print("[HELIUS] 🚨 ALERTS:", flush=True)
            for alert in alerts:
                print(f"  {alert}", flush=True)
            print()

    # Export example
    print(
        "[HELIUS] 💾 To export usage history: python helius_account_monitor.py --export",
        flush=True,
    )
