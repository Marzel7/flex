"""
Snapshot collectors for Helius CLI and FLEX internal metrics.
"""

import os
import json
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pathlib import Path

DB_PATH = "flex_complete_database.db"


def _connect(timeout: int = 30) -> sqlite3.Connection:
    """Create database connection with optimal settings."""
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_utc_iso() -> str:
    """Get current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class HeliusCliCollector:
    """Collect Helius usage via CLI: helius usage --json"""

    @staticmethod
    def _get_or_create_keypair_file() -> Optional[str]:
        """Create temp keypair file from HELIUS_WALLET_KEYPAIR env var."""
        keypair_env = os.getenv("HELIUS_WALLET_KEYPAIR")
        if not keypair_env:
            return None

        try:
            keypair_array = json.loads(keypair_env)
            temp_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, prefix="helius_keypair_"
            )
            json.dump(keypair_array, temp_file)
            temp_file.close()
            os.chmod(temp_file.name, 0o600)
            return temp_file.name
        except Exception as e:
            print(f"[HELIUS_CLI] ⚠️ Could not create keypair file: {e}", flush=True)
            return None

    @staticmethod
    def collect() -> Optional[Dict[str, Any]]:
        """Run helius usage --json and parse output."""
        keypair_file = HeliusCliCollector._get_or_create_keypair_file()
        if not keypair_file:
            print(
                "[HELIUS_CLI] ⚠️ No HELIUS_WALLET_KEYPAIR, skipping CLI collection",
                flush=True,
            )
            return None

        try:
            cmd = [
                "helius",
                "usage",
                "--json",
                "--keypair",
                keypair_file,
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, check=True
            )

            data = json.loads(result.stdout)

            # Extract nested creditsUsage if present
            credits_usage = data.get("creditsUsage", {})

            snapshot = {
                "ts_utc": get_utc_iso(),
                "billing_cycle_start_utc": data.get("billingCycleStart"),
                "billing_cycle_end_utc": data.get("billingCycleEnd"),
                "total_credits_used": data.get("creditsUsed"),
                "remaining_credits": data.get("creditsRemaining"),
                "rpc_usage": credits_usage.get("rpcUsage") or data.get("rpc_usage"),
                "api_usage": credits_usage.get("apiUsage") or data.get("api_usage"),
                "rpc_gpa_usage": credits_usage.get("rpcGpaUsage")
                or data.get("rpc_gpa_usage"),
                "webhook_usage": credits_usage.get("webhookUsage")
                or data.get("webhook_usage"),
                "prepaid_credits_used": credits_usage.get("prepaidCreditsUsed")
                or data.get("prepaid_credits_used"),
                "overage_credits_used": credits_usage.get("overageCreditsUsed")
                or data.get("overage_credits_used"),
                "overage_cost": credits_usage.get("overageCost")
                or data.get("overage_cost"),
                "raw_json": json.dumps(data),
            }

            # Cleanup temp file
            try:
                os.unlink(keypair_file)
            except:
                pass

            return snapshot

        except subprocess.TimeoutExpired:
            print("[HELIUS_CLI] ⚠️ Command timed out", flush=True)
            return None
        except subprocess.CalledProcessError as e:
            print(f"[HELIUS_CLI] ⚠️ CLI error: {e.stderr}", flush=True)
            return None
        except json.JSONDecodeError as e:
            print(f"[HELIUS_CLI] ⚠️ Failed to parse JSON: {e}", flush=True)
            return None
        except Exception as e:
            print(f"[HELIUS_CLI] ⚠️ Unexpected error: {e}", flush=True)
            return None

    @staticmethod
    def store_snapshot(snapshot: Dict[str, Any]) -> bool:
        """Store CLI snapshot in database."""
        try:
            conn = _connect()
            cursor = conn.cursor()

            # Update with both old column names (for compatibility) and new ones
            cursor.execute(
                """
                UPDATE helius_usage_snapshots
                SET ts_utc = ?, billing_cycle_start_utc = ?, billing_cycle_end_utc = ?,
                    total_credits_used = ?, remaining_credits = ?, rpc_usage = ?, api_usage = ?,
                    rpc_gpa_usage = ?, webhook_usage = ?, prepaid_credits_used = ?,
                    overage_credits_used = ?, overage_cost = ?, raw_json = ?
                WHERE timestamp = (SELECT MAX(timestamp) FROM helius_usage_snapshots)
                """,
                (
                    snapshot["ts_utc"],
                    snapshot.get("billing_cycle_start_utc"),
                    snapshot.get("billing_cycle_end_utc"),
                    snapshot.get("total_credits_used"),
                    snapshot.get("remaining_credits"),
                    snapshot.get("rpc_usage"),
                    snapshot.get("api_usage"),
                    snapshot.get("rpc_gpa_usage"),
                    snapshot.get("webhook_usage"),
                    snapshot.get("prepaid_credits_used"),
                    snapshot.get("overage_credits_used"),
                    snapshot.get("overage_cost"),
                    snapshot.get("raw_json"),
                ),
            )

            # If no rows updated, insert as new
            if cursor.rowcount == 0:
                cursor.execute(
                    """
                    INSERT INTO helius_usage_snapshots
                    (ts_utc, billing_cycle_start_utc, billing_cycle_end_utc,
                     total_credits_used, remaining_credits, rpc_usage, api_usage,
                     rpc_gpa_usage, webhook_usage, prepaid_credits_used,
                     overage_credits_used, overage_cost, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot["ts_utc"],
                        snapshot.get("billing_cycle_start_utc"),
                        snapshot.get("billing_cycle_end_utc"),
                        snapshot.get("total_credits_used"),
                        snapshot.get("remaining_credits"),
                        snapshot.get("rpc_usage"),
                        snapshot.get("api_usage"),
                        snapshot.get("rpc_gpa_usage"),
                        snapshot.get("webhook_usage"),
                        snapshot.get("prepaid_credits_used"),
                        snapshot.get("overage_credits_used"),
                        snapshot.get("overage_cost"),
                        snapshot.get("raw_json"),
                    ),
                )

            conn.commit()
            conn.close()
            print(
                f"[HELIUS_CLI] ✅ Stored snapshot: {snapshot['total_credits_used']} credits used",
                flush=True,
            )
            return True
        except Exception as e:
            print(f"[HELIUS_CLI] ⚠️ Failed to store snapshot: {e}", flush=True)
            return False


class InternalMetricsCollector:
    """Collect FLEX internal metrics via HTTP API."""

    @staticmethod
    def collect(api_url: str = "http://localhost:8001/metrics/rpc") -> Optional[
        Dict[str, Any]
    ]:
        """Fetch metrics from FLEX API."""
        try:
            import requests

            response = requests.get(api_url, timeout=10)
            response.raise_for_status()

            data = response.json()
            summary = data.get("summary", {})

            snapshot = {
                "ts_utc": get_utc_iso(),
                "credits_all_attempts": summary.get("credits_total"),
                "credits_success_only": summary.get("credits_success_only", 0),
                "requests_total": summary.get("requests_total"),
                "requests_429": summary.get("rate_limits_429"),
                "errors_total": summary.get("errors_total"),
                "burn_rate_per_minute": summary.get("credits_burn_rate_per_minute"),
                "raw_json": json.dumps(data),
            }

            return snapshot

        except ImportError:
            print("[INTERNAL] ⚠️ requests library not available", flush=True)
            return None
        except Exception as e:
            print(f"[INTERNAL] ⚠️ Failed to collect metrics: {e}", flush=True)
            return None

    @staticmethod
    def store_snapshot(snapshot: Dict[str, Any]) -> bool:
        """Store internal metrics snapshot in database."""
        try:
            conn = _connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT OR REPLACE INTO internal_usage_snapshots
                (ts_utc, credits_all_attempts, credits_success_only,
                 requests_total, requests_429, errors_total, burn_rate_per_minute, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot["ts_utc"],
                    snapshot.get("credits_all_attempts"),
                    snapshot.get("credits_success_only"),
                    snapshot.get("requests_total"),
                    snapshot.get("requests_429"),
                    snapshot.get("errors_total"),
                    snapshot.get("burn_rate_per_minute"),
                    snapshot.get("raw_json"),
                ),
            )

            conn.commit()
            conn.close()
            print(
                f"[INTERNAL] ✅ Stored snapshot: {snapshot['credits_all_attempts']} credits",
                flush=True,
            )
            return True
        except Exception as e:
            print(f"[INTERNAL] ⚠️ Failed to store snapshot: {e}", flush=True)
            return False


if __name__ == "__main__":
    # Test collectors
    print("[TEST] Collecting Helius CLI snapshot...")
    helius_snap = HeliusCliCollector.collect()
    if helius_snap:
        print(f"  Helius: {helius_snap['total_credits_used']} credits used")
        HeliusCliCollector.store_snapshot(helius_snap)

    print("[TEST] Collecting internal metrics snapshot...")
    internal_snap = InternalMetricsCollector.collect()
    if internal_snap:
        print(f"  Internal: {internal_snap['credits_all_attempts']} credits")
        InternalMetricsCollector.store_snapshot(internal_snap)
