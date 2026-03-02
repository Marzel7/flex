"""
Delta-based reconciliation engine.
Compares Helius CLI totals with FLEX internal metrics over time intervals.
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any

DB_PATH = "flex_complete_database.db"


def _connect(timeout: int = 30) -> sqlite3.Connection:
    """Create database connection with optimal settings."""
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


class ReconciliationEngine:
    """Compute and store reconciliation between CLI and internal metrics."""

    @staticmethod
    def get_latest_helius_snapshot() -> Optional[Dict[str, Any]]:
        """Fetch latest Helius CLI snapshot."""
        try:
            conn = _connect()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM helius_usage_snapshots ORDER BY ts_utc DESC LIMIT 1"
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(row)
            return None
        except Exception as e:
            print(f"[RECONCILIATION] ⚠️ Error fetching latest Helius snapshot: {e}", flush=True)
            return None

    @staticmethod
    def get_latest_internal_snapshot() -> Optional[Dict[str, Any]]:
        """Fetch latest internal metrics snapshot."""
        try:
            conn = _connect()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM internal_usage_snapshots ORDER BY ts_utc DESC LIMIT 1"
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(row)
            return None
        except Exception as e:
            print(f"[RECONCILIATION] ⚠️ Error fetching latest internal snapshot: {e}", flush=True)
            return None

    @staticmethod
    def get_previous_helius_snapshot(
        before_ts: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch previous Helius snapshot before given timestamp."""
        try:
            conn = _connect()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM helius_usage_snapshots
                WHERE ts_utc < ?
                ORDER BY ts_utc DESC
                LIMIT 1
                """,
                (before_ts,),
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(row)
            return None
        except Exception as e:
            print(f"[RECONCILIATION] ⚠️ Error fetching previous Helius snapshot: {e}", flush=True)
            return None

    @staticmethod
    def get_previous_internal_snapshot(
        before_ts: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch previous internal snapshot before given timestamp."""
        try:
            conn = _connect()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM internal_usage_snapshots
                WHERE ts_utc < ?
                ORDER BY ts_utc DESC
                LIMIT 1
                """,
                (before_ts,),
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(row)
            return None
        except Exception as e:
            print(f"[RECONCILIATION] ⚠️ Error fetching previous internal snapshot: {e}", flush=True)
            return None

    @staticmethod
    def detect_break(
        prev_helius: Optional[Dict],
        curr_helius: Dict,
        prev_internal: Optional[Dict],
        curr_internal: Dict,
        window_seconds: int,
    ) -> Tuple[bool, str]:
        """
        Detect if this interval should be marked as a break.
        Returns (is_break, reason)
        """
        reasons = []

        # No previous snapshot = first interval
        if prev_helius is None or prev_internal is None:
            return False, "first_snapshot"

        # CLI total decreased (new billing cycle)
        if (
            curr_helius.get("total_credits_used") is not None
            and prev_helius.get("total_credits_used") is not None
            and curr_helius["total_credits_used"] < prev_helius["total_credits_used"]
        ):
            reasons.append("cli_reset")

        # Internal total decreased (restart/reset)
        if (
            curr_internal.get("credits_all_attempts") is not None
            and prev_internal.get("credits_all_attempts") is not None
            and curr_internal["credits_all_attempts"] < prev_internal["credits_all_attempts"]
        ):
            reasons.append("internal_reset")

        # Large time gap (more than 2x expected window)
        if window_seconds and window_seconds > 0:
            if window_seconds > window_seconds * 2:
                reasons.append("large_gap")

        if reasons:
            return True, ",".join(reasons)
        return False, ""

    @staticmethod
    def compute_reconciliation(
        curr_helius: Dict,
        prev_helius: Optional[Dict],
        curr_internal: Dict,
        prev_internal: Optional[Dict],
        window_seconds: int,
    ) -> Dict[str, Any]:
        """
        Compute delta-based reconciliation.
        Returns dict with cli_delta, internal_delta, diff, diff_pct, is_break, notes.
        """
        # Detect breaks
        is_break, break_reason = ReconciliationEngine.detect_break(
            prev_helius, curr_helius, prev_internal, curr_internal, window_seconds
        )

        cli_delta = None
        internal_delta = None
        delta_diff = None
        diff_pct = None
        notes = ""

        if not is_break:
            # Compute CLI delta
            if (
                prev_helius
                and curr_helius.get("total_credits_used") is not None
                and prev_helius.get("total_credits_used") is not None
            ):
                cli_delta = (
                    curr_helius["total_credits_used"]
                    - prev_helius["total_credits_used"]
                )
            else:
                cli_delta = 0 if curr_helius.get("total_credits_used") is not None else None

            # Compute internal delta
            if (
                prev_internal
                and curr_internal.get("credits_all_attempts") is not None
                and prev_internal.get("credits_all_attempts") is not None
            ):
                internal_delta = (
                    curr_internal["credits_all_attempts"]
                    - prev_internal["credits_all_attempts"]
                )
            else:
                internal_delta = (
                    0 if curr_internal.get("credits_all_attempts") is not None else None
                )

            # Compute difference
            if cli_delta is not None and internal_delta is not None:
                delta_diff = cli_delta - internal_delta
                max_delta = max(abs(cli_delta), 1)
                diff_pct = (delta_diff / max_delta) * 100

                # Classify
                if abs(diff_pct) <= 2:
                    notes = "clean"
                elif abs(diff_pct) <= 5:
                    notes = "minor_drift"
                else:
                    notes = "significant_drift"

        return {
            "ts_utc": curr_helius.get("ts_utc") or curr_internal.get("ts_utc"),
            "window_seconds": window_seconds,
            "cli_delta": cli_delta,
            "internal_delta": internal_delta,
            "delta_diff": delta_diff,
            "diff_pct": diff_pct,
            "is_break": 1 if is_break else 0,
            "notes": break_reason if is_break else notes,
        }

    @staticmethod
    def reconcile_and_store() -> Optional[Dict[str, Any]]:
        """
        Main reconciliation: compare latest snapshots and store result.
        """
        curr_helius = ReconciliationEngine.get_latest_helius_snapshot()
        curr_internal = ReconciliationEngine.get_latest_internal_snapshot()

        if not curr_helius or not curr_internal:
            print("[RECONCILIATION] ⚠️ Missing current snapshots", flush=True)
            return None

        # Get previous snapshots
        prev_helius = ReconciliationEngine.get_previous_helius_snapshot(
            curr_helius["ts_utc"]
        )
        prev_internal = ReconciliationEngine.get_previous_internal_snapshot(
            curr_internal["ts_utc"]
        )

        # Estimate window
        window_seconds = None
        if prev_internal and curr_internal.get("ts_utc"):
            try:
                prev_ts = datetime.fromisoformat(
                    prev_internal["ts_utc"].replace("Z", "+00:00")
                )
                curr_ts = datetime.fromisoformat(
                    curr_internal["ts_utc"].replace("Z", "+00:00")
                )
                window_seconds = int((curr_ts - prev_ts).total_seconds())
            except:
                window_seconds = None

        # Compute reconciliation
        result = ReconciliationEngine.compute_reconciliation(
            curr_helius, prev_helius, curr_internal, prev_internal, window_seconds
        )

        # Store result
        try:
            conn = _connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT OR REPLACE INTO usage_reconciliation
                (ts_utc, window_seconds, cli_delta, internal_delta,
                 delta_diff, diff_pct, is_break, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["ts_utc"],
                    result["window_seconds"],
                    result["cli_delta"],
                    result["internal_delta"],
                    result["delta_diff"],
                    result["diff_pct"],
                    result["is_break"],
                    result["notes"],
                ),
            )

            conn.commit()
            conn.close()

            print(
                f"[RECONCILIATION] ✅ Stored: cli_delta={result['cli_delta']}, "
                f"internal_delta={result['internal_delta']}, "
                f"diff_pct={result['diff_pct']:.1f}%, status={result['notes']}",
                flush=True,
            )

            return result

        except Exception as e:
            print(f"[RECONCILIATION] ⚠️ Failed to store result: {e}", flush=True)
            return None
