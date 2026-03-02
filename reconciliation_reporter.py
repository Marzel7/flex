"""
CLI reporting for reconciliation results.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

DB_PATH = "flex_complete_database.db"


def _format_table(headers: List[str], rows: List[List]) -> str:
    """Simple table formatter without external dependencies."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_row = "|" + "|".join(
        f" {headers[i]:<{col_widths[i]}} " for i in range(len(headers))
    ) + "|"

    result = sep + "\n" + header_row + "\n" + sep + "\n"

    for row in rows:
        data_row = "|" + "|".join(
            f" {str(row[i]):<{col_widths[i]}} " for i in range(len(row))
        ) + "|"
        result += data_row + "\n"

    result += sep
    return result


def _connect(timeout: int = 30) -> sqlite3.Connection:
    """Create database connection with optimal settings."""
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


class ReconciliationReporter:
    """Generate reports from reconciliation data."""

    @staticmethod
    def latest_reconciliation() -> Optional[Dict[str, Any]]:
        """Get and display latest reconciliation result."""
        try:
            conn = _connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT * FROM usage_reconciliation
                ORDER BY ts_utc DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            conn.close()

            if not row:
                print("No reconciliation data available")
                return None

            result = dict(row)

            # Pretty print
            print("\n" + "=" * 80)
            print("LATEST RECONCILIATION")
            print("=" * 80)
            print(f"Timestamp:        {result['ts_utc']}")
            print(f"Window:           {result['window_seconds']}s")
            print(f"CLI Delta:        {result['cli_delta']:,} credits")
            print(f"Internal Delta:   {result['internal_delta']:,} credits")
            print(f"Difference:       {result['delta_diff']:,} credits")
            print(f"Diff %:           {result['diff_pct']:.2f}%" if result['diff_pct'] else "Diff %:           N/A")
            print(f"Break:            {'YES' if result['is_break'] else 'NO'}")
            print(f"Status:           {result['notes']}")
            print("=" * 80 + "\n")

            return result

        except Exception as e:
            print(f"Error fetching latest reconciliation: {e}")
            return None

    @staticmethod
    def daily_reconciliation(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get daily aggregation for a specific date (UTC).
        Format: YYYY-MM-DD
        If no date, use today.
        """
        if not date_str:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            conn = _connect()
            cursor = conn.cursor()

            # Get all reconciliation entries for the date
            cursor.execute(
                """
                SELECT *
                FROM usage_reconciliation
                WHERE DATE(ts_utc) = ?
                ORDER BY ts_utc ASC
                """,
                (date_str,),
            )

            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()

            if not rows:
                print(f"No reconciliation data for {date_str}")
                return []

            # Aggregate
            total_cli_delta = sum(r["cli_delta"] or 0 for r in rows)
            total_internal_delta = sum(r["internal_delta"] or 0 for r in rows)
            total_diff = total_cli_delta - total_internal_delta
            total_diff_pct = (total_diff / max(abs(total_cli_delta), 1)) * 100
            num_breaks = sum(1 for r in rows if r["is_break"])

            print("\n" + "=" * 80)
            print(f"DAILY RECONCILIATION SUMMARY - {date_str} (UTC)")
            print("=" * 80)
            print(f"Samples:          {len(rows)}")
            print(f"Total CLI Delta:  {total_cli_delta:,} credits")
            print(f"Total Int Delta:  {total_internal_delta:,} credits")
            print(f"Total Diff:       {total_diff:,} credits")
            print(f"Total Diff %:     {total_diff_pct:.2f}%")
            print(f"Breaks:           {num_breaks}")
            print("-" * 80)

            # Table of all intervals
            table_data = [
                [
                    r["ts_utc"],
                    r["window_seconds"] or "?",
                    r["cli_delta"] or "?",
                    r["internal_delta"] or "?",
                    f"{r['diff_pct']:.1f}%" if r["diff_pct"] is not None else "?",
                    "BREAK" if r["is_break"] else r["notes"],
                ]
                for r in rows
            ]

            headers = ["Timestamp", "Window(s)", "CLI Δ", "Int Δ", "Diff%", "Status"]
            print(_format_table(headers, table_data))
            print("=" * 80 + "\n")

            return rows

        except Exception as e:
            print(f"Error fetching daily reconciliation: {e}")
            return []

    @staticmethod
    def reconciliation_health() -> Dict[str, Any]:
        """
        Overall health check: summarize last 7 days.
        """
        try:
            conn = _connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    COUNT(*) as total_intervals,
                    SUM(CASE WHEN is_break=0 THEN 1 ELSE 0 END) as clean_intervals,
                    SUM(CASE WHEN is_break=1 THEN 1 ELSE 0 END) as breaks,
                    AVG(CASE WHEN is_break=0 THEN diff_pct ELSE NULL END) as avg_diff_pct,
                    MAX(CASE WHEN is_break=0 THEN ABS(diff_pct) ELSE NULL END) as max_diff_pct,
                    SUM(CASE WHEN notes='clean' THEN 1 ELSE 0 END) as clean_count,
                    SUM(CASE WHEN notes='minor_drift' THEN 1 ELSE 0 END) as minor_drift_count,
                    SUM(CASE WHEN notes='significant_drift' THEN 1 ELSE 0 END) as significant_drift_count
                FROM usage_reconciliation
                WHERE ts_utc >= datetime('now', '-7 days')
                """
            )

            row = cursor.fetchone()
            conn.close()

            if not row:
                print("No reconciliation data in last 7 days")
                return {}

            result = dict(row)

            # Determine health
            if result["total_intervals"] == 0:
                health = "NO_DATA"
            elif result["breaks"] > result["total_intervals"] * 0.1:
                health = "UNSTABLE"
            elif result["significant_drift_count"] and result["significant_drift_count"] > result["total_intervals"] * 0.05:
                health = "DEGRADED"
            elif (result["avg_diff_pct"] or 0) > 2:
                health = "WARNING"
            else:
                health = "HEALTHY"

            print("\n" + "=" * 80)
            print("HEALTH CHECK - Last 7 Days (UTC)")
            print("=" * 80)
            print(f"Status:           {health}")
            print(f"Total Intervals:  {result['total_intervals']}")
            print(f"Clean:            {result['clean_intervals']}")
            print(f"Breaks:           {result['breaks']}")
            print(f"Avg Diff %:       {result['avg_diff_pct']:.2f}%" if result['avg_diff_pct'] else "Avg Diff %:       N/A")
            print(f"Max Diff %:       {result['max_diff_pct']:.2f}%" if result['max_diff_pct'] else "Max Diff %:       N/A")
            print(f"Clean:            {result['clean_count']}")
            print(f"Minor Drift:      {result['minor_drift_count']}")
            print(f"Significant Drift:{result['significant_drift_count']}")
            print("=" * 80 + "\n")

            return {**result, "health": health}

        except Exception as e:
            print(f"Error computing health check: {e}")
            return {}
