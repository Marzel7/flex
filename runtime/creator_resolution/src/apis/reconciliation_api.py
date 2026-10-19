"""
FastAPI endpoints for reconciliation system.
Integrate into your existing rpc_metrics_api.py or serve separately.

Usage in rpc_metrics_api.py:
    from src.apis.reconciliation_api import router as reconciliation_router
    app.include_router(reconciliation_router, prefix="/reconciliation", tags=["reconciliation"])
"""

from fastapi import APIRouter, Query
from typing import Optional, Dict, Any
from datetime import datetime

from reconciliation_collectors import HeliusCliCollector, InternalMetricsCollector
from src.utils.reconciliation_engine import ReconciliationEngine
from reconciliation_reporter import ReconciliationReporter

router = APIRouter()


@router.post("/collect")
async def collect_snapshots():
    """
    Manually trigger snapshot collection.
    POST /reconciliation/collect
    """
    helius_snap = HeliusCliCollector.collect()
    helius_stored = False
    if helius_snap:
        helius_stored = HeliusCliCollector.store_snapshot(helius_snap)

    internal_snap = InternalMetricsCollector.collect()
    internal_stored = False
    if internal_snap:
        internal_stored = InternalMetricsCollector.store_snapshot(internal_snap)

    return {
        "status": "ok",
        "helius_collected": bool(helius_snap),
        "helius_stored": helius_stored,
        "internal_collected": bool(internal_snap),
        "internal_stored": internal_stored,
    }


@router.post("/reconcile")
async def run_reconciliation():
    """
    Manually trigger reconciliation computation.
    POST /reconciliation/reconcile
    """
    result = ReconciliationEngine.reconcile_and_store()

    if result:
        return {"status": "ok", "result": result}
    else:
        return {"status": "error", "message": "Reconciliation failed"}


@router.get("/latest")
async def get_latest_reconciliation():
    """
    Get latest reconciliation result.
    GET /reconciliation/latest
    """
    result = ReconciliationEngine.get_latest_helius_snapshot()
    helius = ReconciliationEngine.get_latest_helius_snapshot()
    internal = ReconciliationEngine.get_latest_internal_snapshot()

    if not helius or not internal:
        return {"status": "error", "message": "Missing snapshots"}

    # Compute on-the-fly
    prev_helius = ReconciliationEngine.get_previous_helius_snapshot(
        helius["ts_utc"]
    )
    prev_internal = ReconciliationEngine.get_previous_internal_snapshot(
        internal["ts_utc"]
    )

    result = ReconciliationEngine.compute_reconciliation(
        helius, prev_helius, internal, prev_internal, window_seconds=300
    )

    return {"status": "ok", "latest": result}


@router.get("/daily")
async def get_daily_reconciliation(date: Optional[str] = Query(None)):
    """
    Get daily aggregation.
    GET /reconciliation/daily?date=2025-03-02
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    # Fetch all intervals for the day
    import sqlite3

    try:
        import os
        db_path = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), '../../database/flex_complete_database.db'))
        conn = sqlite3.connect(db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT *
            FROM usage_reconciliation
            WHERE DATE(ts_utc) = ?
            ORDER BY ts_utc ASC
            """,
            (date,),
        )

        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()

        if not rows:
            return {"status": "ok", "date": date, "intervals": []}

        # Aggregate
        total_cli_delta = sum(r["cli_delta"] or 0 for r in rows)
        total_internal_delta = sum(r["internal_delta"] or 0 for r in rows)
        total_diff = total_cli_delta - total_internal_delta
        total_diff_pct = (
            (total_diff / max(abs(total_cli_delta), 1)) * 100
            if total_cli_delta != 0
            else 0
        )
        num_breaks = sum(1 for r in rows if r["is_break"])

        return {
            "status": "ok",
            "date": date,
            "summary": {
                "samples": len(rows),
                "total_cli_delta": total_cli_delta,
                "total_internal_delta": total_internal_delta,
                "total_diff": total_diff,
                "total_diff_pct": total_diff_pct,
                "breaks": num_breaks,
            },
            "intervals": rows,
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/health")
async def get_health_check():
    """
    7-day health check summary.
    GET /reconciliation/health
    """
    import sqlite3

    try:
        import os
        db_path = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), '../../database/flex_complete_database.db'))
        conn = sqlite3.connect(db_path, timeout=15)
        conn.row_factory = sqlite3.Row
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
            return {"status": "ok", "health": "NO_DATA"}

        result = dict(row)

        # Determine health
        if result["total_intervals"] == 0:
            health = "NO_DATA"
        elif result["breaks"] and result["breaks"] > result["total_intervals"] * 0.1:
            health = "UNSTABLE"
        elif (
            result["significant_drift_count"]
            and result["significant_drift_count"] > result["total_intervals"] * 0.05
        ):
            health = "DEGRADED"
        elif (result["avg_diff_pct"] or 0) > 2:
            health = "WARNING"
        else:
            health = "HEALTHY"

        return {"status": "ok", "health": health, "details": result}

    except Exception as e:
        return {"status": "error", "message": str(e)}
