"""
RPC Metrics API - FastAPI endpoints for metrics dashboard.

Provides HTTP endpoints:
- GET /metrics/rpc - Full metrics summary
- GET /metrics/rpc/summary - Quick summary only
- GET /metrics/rpc/sections - Per-section breakdown
- GET /metrics/rpc/methods - Top methods by credits
- POST /metrics/rpc/reset - Reset daily counters (admin only)
"""

from fastapi import FastAPI, HTTPException, Query, Body, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime
import json
from typing import Optional
import threading
import time
import sqlite3

from rpc_metrics_recorder import (
    get_recorder,
    initialize_recorder,
    RPCMetricsRecorder,
)

# Global state for background comparison tests
comparison_results = {}
comparison_lock = threading.Lock()


# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(
    title="FLEX RPC Metrics",
    description="Monitor Helius credit usage by component section",
    version="1.0.0",
)


@app.on_event("startup")
async def startup_event():
    """Initialize recorder on startup (with 10M credits/month plan)"""
    # Using Helius tier: 10M credits/month
    initialize_recorder(plan_monthly_credits=10_000_000)


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}


@app.get("/metrics/rpc")
async def metrics_full():
    """
    Full metrics endpoint with summary, sections, top methods, and alerts.
    Returns everything needed for the dashboard.
    """
    recorder = get_recorder()

    # Try to get latest Helius snapshot for display
    helius_snapshot = None
    try:
        from helius_cli_monitor import get_latest_snapshot
        helius_snapshot = get_latest_snapshot()
    except:
        pass

    return {
        "timestamp": datetime.now().isoformat(),
        "summary": recorder.get_summary(),
        "sections": recorder.get_section_stats(),
        "top_methods": recorder.get_top_methods(limit=10),
        "alerts": recorder.get_alerts(burn_rate_threshold=100.0),
        "helius_snapshot": helius_snapshot,
    }


@app.get("/metrics/rpc/summary")
async def metrics_summary():
    """Quick summary only (low bandwidth)"""
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "summary": recorder.get_summary(),
    }


@app.get("/metrics/rpc/sections")
async def metrics_sections():
    """Per-section breakdown"""
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "sections": recorder.get_section_stats(),
    }


@app.get("/metrics/rpc/source-files")
async def metrics_source_files():
    """Per-source-file breakdown (which files/processes are making RPC calls)"""
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "source_files": recorder.get_source_file_stats(),
    }


@app.get("/metrics/rpc/methods")
async def metrics_methods(limit: int = Query(10, ge=1, le=50)):
    """Top methods by credits"""
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "top_methods": recorder.get_top_methods(limit=limit),
    }


@app.get("/metrics/rpc/alerts")
async def metrics_alerts(
    burn_rate_threshold: float = Query(100.0, ge=1.0, le=10000.0)
):
    """Get active alerts"""
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "alerts": recorder.get_alerts(burn_rate_threshold=burn_rate_threshold),
    }


@app.post("/metrics/rpc/record")
async def record_metric(data: dict):
    """Record a single RPC metric (called by instrumented code in other processes)"""
    recorder = get_recorder()
    credits = recorder.record_request(
        section=data.get("section", "unknown"),
        provider=data.get("provider", "unknown"),
        method=data.get("method", "unknown"),
        status_code=data.get("status_code", 0),
        latency_ms=data.get("latency_ms", 0),
        mode=data.get("mode", "realtime"),
        retries=data.get("retries", 0),
        bytes_in=data.get("bytes_in", 0),
        bytes_out=data.get("bytes_out", 0),
        source_file=data.get("source_file", "unknown"),
        error=data.get("error"),
    )
    return {"credits": credits, "status": "recorded"}


@app.post("/metrics/rpc/reset")
async def metrics_reset(request: dict = Body(None)):
    """Reset all RPC instrumentation metrics to 0"""
    # Reset is available for local/trusted access
    # In production, add authentication if exposed to untrusted networks
    try:
        recorder = get_recorder()
        recorder.reset_daily()
        recorder.reset_credits_today()
        return {"status": "success", "message": "All RPC metrics reset to 0 (requests, errors, sections, methods)"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reset failed: {str(e)}")


def run_comparison_background(test_id: str, duration_seconds: int):
    """Run comparison test in background thread
    
    Captures explicit Helius snapshots BEFORE and AFTER the test window
    to ensure accurate delta measurement.
    """
    DB_PATH = "flex_complete_database.db"

    def _connect():
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def get_latest_helius_snapshot():
        """Get the latest Helius snapshot from database"""
        conn = _connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT
                  credits_remaining,
                  credits_used,
                  credits_used_month,
                  prepaid_credits_used,
                  overage_credits_used,
                  overage_cost,
                  timestamp
                FROM helius_usage_snapshots
                ORDER BY timestamp DESC
                LIMIT 1
            """)
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    def trigger_helius_snapshot():
        """Trigger a fresh Helius CLI snapshot capture"""
        try:
            import subprocess
            subprocess.run(
                ["python", "helius_cli_monitor.py"],
                capture_output=True,
                timeout=15
            )
            print(f"[COMPARISON] Triggered Helius snapshot capture", flush=True)
        except Exception as e:
            print(f"[COMPARISON] Warning: Could not trigger Helius snapshot: {str(e)[:100]}", flush=True)

    try:
        # BEFORE TEST: Trigger fresh Helius snapshot
        print(f"[COMPARISON] Capturing BEFORE snapshot...", flush=True)
        trigger_helius_snapshot()
        time.sleep(2)  # Brief pause for snapshot to complete
        
        # Get initial state from RPC recorder (instrumented credits)
        recorder = get_recorder()
        summary = recorder.get_summary()
        local_start = summary.get('credits_instrumented_today', 0)
        print(f"[COMPARISON] Initial local baseline: {local_start} credits (instrumented)", flush=True)

        # Get BEFORE Helius snapshot (freshly captured)
        helius_snapshot_before = get_latest_helius_snapshot()
        helius_start = helius_snapshot_before.get('credits_used', 0) if helius_snapshot_before else 0
        helius_timestamp_before = helius_snapshot_before.get('timestamp', 'unknown') if helius_snapshot_before else 'unknown'
        print(f"[COMPARISON] BEFORE Helius snapshot: {helius_start} credits at {helius_timestamp_before}", flush=True)

        # Run comparison loop
        start_time = time.time()
        measurements = []
        local_delta = 0
        helius_delta = 0
        diff = 0
        diff_pct = 0
        status = "CLEAN"
        last_helius_value = helius_start

        while True:
            elapsed = int(time.time() - start_time)

            if elapsed > duration_seconds:
                print(f"[COMPARISON] Test duration reached ({elapsed}s), stopping", flush=True)
                break

            # Fetch current LOCAL metrics
            try:
                recorder = get_recorder()
                summary = recorder.get_summary()
                local_total = summary.get('credits_instrumented_today', 0)
            except:
                local_total = local_start

            # Fetch current HELIUS metrics from DB (updates every 30s from CLI monitor)
            try:
                helius_snapshot = get_latest_helius_snapshot()
                current_helius = helius_snapshot.get('credits_used', 0) if helius_snapshot else helius_start
                
                # Track if Helius updated
                if current_helius > last_helius_value:
                    print(f"[COMPARISON] @{elapsed}s: Helius updated to {current_helius} (was {last_helius_value})", flush=True)
                    last_helius_value = current_helius
                helius_end = current_helius
            except Exception as e:
                print(f"[COMPARISON] Error reading Helius snapshot: {str(e)}", flush=True)
                helius_end = helius_start

            # Calculate deltas (using latest known values)
            local_delta = max(0, local_total - local_start)
            helius_delta = max(0, helius_end - helius_start)

            # Calculate comparison
            diff = abs(local_delta - helius_delta)
            diff_pct = (diff / max(helius_delta, 1) * 100) if helius_delta > 0 else 0

            status = "CLEAN" if diff_pct <= 2 else "MINOR" if diff_pct <= 5 else "DRIFT"

            measurements.append({
                "elapsed": elapsed,
                "local_credits": local_delta,
                "helius_credits": helius_delta,
                "difference": diff,
                "difference_pct": round(diff_pct, 1) if helius_delta > 0 else 0,
                "status": status
            })

            # Update results immediately (for live polling)
            with comparison_lock:
                comparison_results[test_id] = {
                    "status": "running",
                    "duration": elapsed,
                    "updates": len(measurements),
                    "summary": {
                        "local_credits_used": local_delta,
                        "helius_credits_used": helius_delta,
                        "difference": diff,
                        "difference_pct": round(diff_pct, 1),
                        "status": status
                    },
                    "measurements": measurements[-5:],  # Last 5
                    "notes": ["Test running...", "Local: Per-request instrumentation", "Helius: Account-level billing (explicit snapshots)"]
                }

            time.sleep(5)  # Update every 5 seconds

        # AFTER TEST: Trigger fresh Helius snapshot
        print(f"[COMPARISON] Test complete, capturing AFTER snapshot...", flush=True)
        trigger_helius_snapshot()
        time.sleep(2)  # Brief pause for snapshot to complete

        # Get AFTER Helius snapshot (freshly captured)
        helius_snapshot_after = get_latest_helius_snapshot()
        helius_end_final = helius_snapshot_after.get('credits_used', 0) if helius_snapshot_after else helius_end
        helius_timestamp_after = helius_snapshot_after.get('timestamp', 'unknown') if helius_snapshot_after else 'unknown'
        print(f"[COMPARISON] AFTER Helius snapshot: {helius_end_final} credits at {helius_timestamp_after}", flush=True)

        # Recalculate final deltas using explicit before/after snapshots
        local_delta = max(0, local_total - local_start)
        helius_delta = max(0, helius_end_final - helius_start)
        diff = abs(local_delta - helius_delta)
        diff_pct = (diff / max(helius_delta, 1) * 100) if helius_delta > 0 else 0
        status = "CLEAN" if diff_pct <= 2 else "MINOR" if diff_pct <= 5 else "DRIFT"

        print(f"[COMPARISON] FINAL DELTA: Local={local_delta}, Helius={helius_delta}, Diff={diff} ({diff_pct:.1f}%)", flush=True)

        # Store final results
        with comparison_lock:
            comparison_results[test_id] = {
                "status": "complete",
                "duration": elapsed,
                "updates": len(measurements),
                "summary": {
                    "local_credits_used": local_delta,
                    "helius_credits_used": helius_delta,
                    "difference": diff,
                    "difference_pct": round(diff_pct, 1),
                    "status": status
                },
                "measurements": measurements[-5:],  # Last 5
                "snapshots": {
                    "before": {
                        "timestamp": helius_timestamp_before,
                        "credits_used": helius_start
                    },
                    "after": {
                        "timestamp": helius_timestamp_after,
                        "credits_used": helius_end_final
                    }
                },
                "notes": ["Test completed with explicit before/after snapshots", "Local: Per-request instrumentation", "Helius: Account-level billing (explicit snapshots)"]
            }
    except Exception as e:
        print(f"[COMPARISON] Error in background test: {str(e)}", flush=True)
        with comparison_lock:
            comparison_results[test_id] = {
                "status": "error",
                "error": str(e)
            }


@app.post("/metrics/rpc/comparison")
async def run_rpc_comparison(duration_seconds: int = Query(300), test_id: Optional[str] = Query(None)):
    """
    Start or check status of a 2-minute comparison test.

    If test_id is provided, returns status/results of that test.
    If test_id is not provided, starts a new test and returns the test_id for polling.

    Args:
        duration_seconds: How long to run the test (default 120 = 2 minutes)
        test_id: Test ID to check status (if None, starts new test)

    Returns:
        New test: {test_id, status: "running"}
        Status check: {status: "running"|"complete"|"error", ...results}
    """
    import uuid

    if test_id:
        # Check existing test status
        with comparison_lock:
            if test_id in comparison_results:
                return comparison_results[test_id]
            else:
                return {"status": "not_found"}

    # Start new background test
    new_test_id = str(uuid.uuid4())[:8]

    # Start background thread
    thread = threading.Thread(
        target=run_comparison_background,
        args=(new_test_id, duration_seconds),
        daemon=True
    )
    thread.start()

    with comparison_lock:
        comparison_results[new_test_id] = {"status": "running"}

    return {
        "test_id": new_test_id,
        "status": "running",
        "message": "Test started, poll with test_id to get results",
        "duration_seconds": duration_seconds
    }


@app.get("/metrics/rpc/scan-cost")
async def scan_cost_estimate():
    """
    Get estimated credits for a complete creator_outgoing_extractor scan cycle.

    Returns breakdown of:
    - RPC calls (getSignaturesForAddress)
    - Enhanced transaction parsing calls
    - Total estimated credits
    - Estimated duration with rate limiting
    """
    try:
        from creator_outgoing_extractor import calculate_scan_cost_estimate
        cost = calculate_scan_cost_estimate()
        return {
            "timestamp": datetime.now().isoformat(),
            "scan_cost_estimate": cost,
            "configuration": {
                "total_creators": 1000,
                "pages_per_creator": 2,
                "requests_per_second": 8.0,
                "concurrency": 3,
                "max_retries": 3,
            },
            "notes": [
                "Estimate based on MAX_PAGES_PER_CYCLE=2 per creator",
                "Rate limited to 8 req/sec (smooth, prevents 429s)",
                "Enhanced calls assume 25 sigs per page, 100 sigs per batch",
                "Actual cost may vary based on retry count and data volume",
                "One scan cycle runs every 12 hours",
                "Two cycles per day = covers all 1,453 creators",
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not calculate scan cost: {str(e)}")


@app.get("/metrics/helius")
async def helius_account_status():
    """
    Get Helius account status with full comparison

    Returns:
    - Account usage (credits used, remaining, budget)
    - Instrumented metrics (our RPC tracking)
    - Comparison and discrepancy analysis
    - Alerts if usage is high
    """
    try:
        from helius_account_monitor import get_account_status, get_alerts

        status = get_account_status()
        if not status:
            return {
                "status": "error",
                "message": "Could not fetch account status",
                "timestamp": datetime.now().isoformat(),
            }

        # Get alerts
        alerts = get_alerts()

        return {
            "timestamp": status["timestamp"],
            "helius_account": status["helius_account"],
            "instrumented_metrics": status["instrumented_metrics"],
            "discrepancy": status["discrepancy"],
            "alerts": alerts,
            "source": "config",  # Using rpc_metrics_config.py (manually synced)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not fetch Helius status: {str(e)}")


@app.post("/metrics/helius/capture")
async def capture_helius_snapshot():
    """
    Capture Helius usage snapshot via CLI tool.

    Requires:
    - helius-cli installed (npm install -g helius-cli)
    - helius login already run with your keypair

    Returns: Latest snapshot from CLI
    """
    try:
        from helius_cli_monitor import get_helius_usage_cli, record_usage_snapshot

        usage = get_helius_usage_cli()
        if not usage:
            raise HTTPException(
                status_code=503,
                detail="Could not capture usage from Helius CLI. Ensure helius-cli is installed and authenticated.",
            )

        record_usage_snapshot(usage)
        return {
            "status": "success",
            "message": "Helius usage snapshot captured",
            "snapshot": {
                "credits_remaining": usage.get("credits_remaining"),
                "credits_used": usage.get("credits_used"),
                "credits_used_month": usage.get("credits_used_month"),
                "prepaid_credits_used": usage.get("prepaid_credits_used"),
                "overage_credits_used": usage.get("overage_credits_used"),
                "overage_cost": usage.get("overage_cost"),
                "webhook_usage": usage.get("webhook_usage"),
                "api_usage": usage.get("api_usage"),
                "rpc_usage": usage.get("rpc_usage"),
                "rpc_gpa_usage": usage.get("rpc_gpa_usage"),
                "timestamp": usage.get("timestamp"),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Capture failed: {str(e)}")


@app.get("/metrics/helius/snapshots")
async def get_helius_snapshots(limit: int = Query(20, ge=1, le=100)):
    """
    Get recent Helius usage snapshots from database.

    Returns the last N snapshots in reverse chronological order.
    """
    try:
        from helius_cli_monitor import get_snapshot_history

        snapshots = get_snapshot_history(limit=limit)
        return {
            "status": "success",
            "count": len(snapshots),
            "snapshots": snapshots,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Could not fetch snapshots: {str(e)}"
        )


@app.get("/dashboard")
async def dashboard():
    """Serve minimal HTML dashboard"""
    return HTMLResponse(DASHBOARD_HTML)


# ============================================================================
# DASHBOARD HTML
# ============================================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FLEX RPC Metrics Dashboard</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1a1f3a 100%);
            color: #e2e8f0;
            padding: 20px;
            min-height: 100vh;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        header {
            margin-bottom: 30px;
            border-bottom: 2px solid #3b82f6;
            padding-bottom: 20px;
        }

        header h1 {
            font-size: 28px;
            color: #3b82f6;
            margin-bottom: 5px;
        }

        header p {
            color: #94a3b8;
            font-size: 14px;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }

        .card {
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 20px;
            backdrop-filter: blur(10px);
        }

        .card h3 {
            font-size: 12px;
            text-transform: uppercase;
            color: #64748b;
            margin-bottom: 10px;
            letter-spacing: 1px;
        }

        .card .value {
            font-size: 32px;
            font-weight: bold;
            color: #3b82f6;
            margin-bottom: 5px;
        }

        .card .unit {
            font-size: 12px;
            color: #94a3b8;
        }

        .card.alert {
            border-color: #f59e0b;
        }

        .card.alert .value {
            color: #f59e0b;
        }

        .card.critical {
            border-color: #ef4444;
        }

        .card.critical .value {
            color: #ef4444;
        }

        .section {
            margin-bottom: 30px;
        }

        .section h2 {
            font-size: 18px;
            color: #e2e8f0;
            margin-bottom: 15px;
            border-bottom: 1px solid #334155;
            padding-bottom: 10px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            background: rgba(30, 41, 59, 0.5);
            border: 1px solid #334155;
            border-radius: 8px;
            overflow: hidden;
        }

        thead {
            background: rgba(15, 23, 42, 0.8);
        }

        th {
            padding: 12px;
            text-align: left;
            font-size: 12px;
            text-transform: uppercase;
            color: #94a3b8;
            letter-spacing: 0.5px;
            border-bottom: 1px solid #334155;
        }

        td {
            padding: 12px;
            border-bottom: 1px solid #334155;
            font-size: 14px;
        }

        tr:hover {
            background: rgba(59, 130, 246, 0.1);
        }

        .method-cell {
            font-family: "Courier New", monospace;
            color: #60a5fa;
        }

        .alert-item {
            padding: 12px;
            margin: 10px 0;
            border-left: 4px solid #f59e0b;
            background: rgba(245, 158, 11, 0.1);
            border-radius: 4px;
        }

        .alert-item.critical {
            border-left-color: #ef4444;
            background: rgba(239, 68, 68, 0.1);
        }

        .alert-label {
            font-size: 11px;
            text-transform: uppercase;
            color: #f59e0b;
            margin-bottom: 5px;
        }

        .alert-item.critical .alert-label {
            color: #ef4444;
        }

        .refresh-status {
            text-align: center;
            margin-top: 30px;
            color: #64748b;
            font-size: 12px;
        }

        .loading {
            text-align: center;
            padding: 40px;
            color: #94a3b8;
        }

        .header-controls {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }

        .reset-btn {
            background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            transition: all 0.3s ease;
            box-shadow: 0 4px 6px rgba(239, 68, 68, 0.3);
        }

        .reset-btn:hover {
            background: linear-gradient(135deg, #dc2626 0%, #b91c1c 100%);
            box-shadow: 0 6px 12px rgba(239, 68, 68, 0.4);
            transform: translateY(-2px);
        }

        .reset-btn:active {
            transform: translateY(0);
        }

        .reset-btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }

        .reset-confirm {
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: rgba(30, 41, 59, 0.95);
            border: 2px solid #ef4444;
            border-radius: 12px;
            padding: 30px;
            z-index: 1000;
            max-width: 400px;
            box-shadow: 0 20px 25px rgba(0, 0, 0, 0.5);
            backdrop-filter: blur(10px);
        }

        .reset-confirm h3 {
            color: #ef4444;
            margin-bottom: 15px;
            font-size: 18px;
        }

        .reset-confirm p {
            color: #cbd5e1;
            margin-bottom: 20px;
            line-height: 1.5;
        }

        .reset-confirm-buttons {
            display: flex;
            gap: 10px;
            justify-content: flex-end;
        }

        .reset-confirm-btn {
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-size: 14px;
            transition: all 0.2s ease;
        }

        .reset-confirm-btn.confirm {
            background: #ef4444;
            color: white;
        }

        .reset-confirm-btn.confirm:hover {
            background: #dc2626;
        }

        .reset-confirm-btn.cancel {
            background: #334155;
            color: #e2e8f0;
        }

        .reset-confirm-btn.cancel:hover {
            background: #475569;
        }

        .reset-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.5);
            z-index: 999;
            display: none;
        }

        .reset-overlay.show {
            display: block;
        }

        .reset-status {
            padding: 12px 16px;
            border-radius: 6px;
            margin-bottom: 20px;
            display: none;
        }

        .reset-status.show {
            display: block;
        }

        .reset-status.success {
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid #10b981;
            color: #10b981;
        }

        .reset-status.error {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid #ef4444;
            color: #ef4444;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-controls">
                <div>
                    <h1>🚀 FLEX RPC Metrics Dashboard</h1>
                    <p>Real-time Helius credit usage tracking by component</p>
                </div>
                <div style="display: flex; gap: 10px;">
                    <button class="reset-btn" style="background: linear-gradient(135deg, #06b6d4 0%, #0891b2 100%); box-shadow: 0 4px 6px rgba(6, 182, 212, 0.3);" id="refreshBtn" onclick="refreshHelius()">💎 Refresh Helius</button>
                    <button class="reset-btn" style="background: linear-gradient(135deg, #a78bfa 0%, #9333ea 100%); box-shadow: 0 4px 6px rgba(167, 139, 250, 0.3);" onclick="showComparisonModal()">📊 Local vs Helius</button>
                    <button class="reset-btn" style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); box-shadow: 0 4px 6px rgba(16, 185, 129, 0.3);" onclick="runLiveComparison()">🔬 Run Live Test (5 min)</button>
                    <button class="reset-btn" onclick="showResetConfirm()">🔄 Reset Metrics</button>
                </div>
            </div>
        </header>

        <div id="resetStatus" class="reset-status"></div>

        <div id="content" class="loading">Loading metrics...</div>
    </div>

    <div id="resetOverlay" class="reset-overlay" onclick="hideResetConfirm()"></div>
    <div id="resetConfirm" class="reset-confirm" style="display: none;">
        <h3>⚠️ Reset Metrics?</h3>
        <p>This will reset all metrics to 0 except for Helius credit baseline information. This action cannot be undone.</p>
        <div class="reset-confirm-buttons">
            <button class="reset-confirm-btn cancel" onclick="hideResetConfirm()">Cancel</button>
            <button class="reset-confirm-btn confirm" onclick="confirmReset()">Reset All</button>
        </div>
    </div>

    <div id="comparisonOverlay" class="reset-overlay" onclick="hideComparisonModal()"></div>
    <div id="comparisonModal" class="reset-confirm" style="display: none; max-width: 900px; max-height: 80vh; overflow-y: auto;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
            <h3>📊 Local vs Helius Comparison</h3>
            <button style="background: none; border: none; color: #e2e8f0; font-size: 24px; cursor: pointer;" onclick="hideComparisonModal()">✕</button>
        </div>
        <div id="comparisonContent" style="color: #cbd5e1;">
            <p>Loading comparison data...</p>
        </div>
    </div>

    <div id="testResultsOverlay" class="reset-overlay" onclick="hideTestResults()"></div>
    <div id="testResultsModal" class="reset-confirm" style="display: none; max-width: 900px; max-height: 80vh; overflow-y: auto;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
            <h3>🔬 Live Test Results</h3>
            <button style="background: none; border: none; color: #e2e8f0; font-size: 24px; cursor: pointer;" onclick="hideTestResults()">✕</button>
        </div>
        <div id="testResultsContent" style="color: #cbd5e1;">
            <p>Running test...</p>
        </div>
    </div>

    <script>
        const API_URL = "/metrics/rpc";
        const REFRESH_INTERVAL = 5000; // 5 seconds

        async function fetchMetrics() {
            try {
                const response = await fetch(API_URL);
                const data = await response.json();

                // Also fetch source file stats
                const sourceResponse = await fetch("/metrics/rpc/source-files");
                const sourceData = await sourceResponse.json();
                data.source_files = sourceData.source_files;

                renderDashboard(data);
            } catch (error) {
                document.getElementById("content").innerHTML = `<p style="color: #ef4444;">Error loading metrics: ${error.message}</p>`;
            }
        }

        function formatNumber(num) {
            return new Intl.NumberFormat("en-US").format(Math.round(num));
        }

        function formatPercent(num) {
            return num.toFixed(1) + "%";
        }

        function renderDashboard(data) {
            const summary = data.summary;
            const sections = data.sections;
            const sourceFiles = data.source_files || {};
            const topMethods = data.top_methods;
            const alerts = data.alerts;
            const heliusSnapshot = data.helius_snapshot;

            let html = "";

            // Summary cards
            const creditsUsedSinceReset = summary.credits_instrumented_today;
            const creditsUsedAlert = creditsUsedSinceReset > 100000 ? 'alert' : '';

            html += `<div class="grid">
                <div class="card ${creditsUsedAlert}">
                    <h3>Credits Used (Since Reset)</h3>
                    <div class="value">${formatNumber(creditsUsedSinceReset)}</div>
                    <div class="unit">your local instrumentation</div>
                </div>
                <div class="card">
                    <h3>Daily Burn Rate</h3>
                    <div class="value">${summary.credits_burn_rate_per_minute.toFixed(2)}</div>
                    <div class="unit">credits/min</div>
                </div>
                <div class="card">
                    <h3>Monthly Estimate</h3>
                    <div class="value">${formatNumber(summary.credits_monthly_estimate)}</div>
                </div>
                ${summary.credits_monthly_remaining !== null ? `
                <div class="card ${summary.credits_monthly_remaining < summary.credits_monthly_estimate * 0.2 ? 'alert' : ''}">
                    <h3>Monthly Remaining</h3>
                    <div class="value">${formatNumber(summary.credits_monthly_remaining)}</div>
                </div>
                ` : ''}
                <div class="card">
                    <h3>Total Requests</h3>
                    <div class="value">${formatNumber(summary.requests_total)}</div>
                </div>
                <div class="card ${summary.errors_total > summary.requests_total * 0.05 ? 'alert' : ''}">
                    <h3>Errors</h3>
                    <div class="value">${formatNumber(summary.errors_total)}</div>
                </div>
            </div>`;

            // Helius breakdown
            if (heliusSnapshot) {
                html += `<div class="section">
                    <h2>💎 Helius Usage Breakdown</h2>
                    <div class="grid">
                        <div class="card" style="border: 1px solid #a78bfa;">
                            <h3>Today's Usage</h3>
                            <div class="value" style="color: #a78bfa;">${formatNumber(heliusSnapshot.prepaid_credits_used + (heliusSnapshot.overage_credits_used || 0))}</div>
                            <div class="unit">credits</div>
                        </div>
                        <div class="card" style="border: 1px solid #fbbf24;">
                            <h3>Remaining</h3>
                            <div class="value" style="color: #fbbf24;">${formatNumber(Math.max(0, 10000000 - (heliusSnapshot.prepaid_credits_used + (heliusSnapshot.overage_credits_used || 0))))}</div>
                            <div class="unit">of 10M monthly</div>
                        </div>
                        <div class="card" style="border: 1px solid #06b6d4;">
                            <h3>RPC Usage</h3>
                            <div class="value" style="color: #06b6d4;">${formatNumber(heliusSnapshot.rpc_usage || 0)}</div>
                            <div class="unit">RPC method calls</div>
                        </div>
                        <div class="card" style="border: 1px solid #06b6d4;">
                            <h3>RPC GPA Usage</h3>
                            <div class="value" style="color: #06b6d4;">${formatNumber(heliusSnapshot.rpc_gpa_usage || 0)}</div>
                            <div class="unit">GetProgramAccounts calls</div>
                        </div>
                        <div class="card" style="border: 1px solid #06b6d4;">
                            <h3>API Usage</h3>
                            <div class="value" style="color: #06b6d4;">${formatNumber(heliusSnapshot.api_usage || 0)}</div>
                            <div class="unit">REST API calls</div>
                        </div>
                        <div class="card" style="border: 1px solid #06b6d4;">
                            <h3>Webhook Usage</h3>
                            <div class="value" style="color: #06b6d4;">${formatNumber(heliusSnapshot.webhook_usage || 0)}</div>
                            <div class="unit">Webhook triggers</div>
                        </div>
                        <div class="card" style="border: 1px solid #10b981;">
                            <h3>Prepaid Used</h3>
                            <div class="value" style="color: #10b981;">${formatNumber(heliusSnapshot.prepaid_credits_used || 0)}</div>
                            <div class="unit">from prepaid tier</div>
                        </div>
                        <div class="card" style="border: 1px solid #ef4444;">
                            <h3>Overage Used</h3>
                            <div class="value" style="color: #ef4444;">${formatNumber(heliusSnapshot.overage_credits_used || 0)}</div>
                            <div class="unit">beyond prepaid (cost: $${(heliusSnapshot.overage_cost || 0).toFixed(2)})</div>
                        </div>
                    </div>
                </div>`;
            }

            // Alerts
            if (alerts.length > 0) {
                html += `<div class="section">
                    <h2>⚠️ Active Alerts</h2>`;
                alerts.forEach(alert => {
                    html += `
                        <div class="alert-item ${alert.level === 'critical' ? 'critical' : ''}">
                            <div class="alert-label">${alert.level.toUpperCase()}: ${alert.type}</div>
                            <div>${alert.message}</div>
                        </div>
                    `;
                });
                html += `</div>`;
            }

            // Per-section breakdown
            html += `<div class="section">
                <h2>📊 By Component Section</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Section</th>
                            <th>Credits</th>
                            <th>Requests</th>
                            <th>Errors</th>
                            <th>429s</th>
                            <th>Avg Latency (ms)</th>
                            <th>P95 Latency (ms)</th>
                        </tr>
                    </thead>
                    <tbody>`;

            Object.entries(sections).forEach(([section, stats]) => {
                html += `
                    <tr>
                        <td><strong>${section}</strong></td>
                        <td>${formatNumber(stats.credits)}</td>
                        <td>${formatNumber(stats.requests)}</td>
                        <td style="color: ${stats.errors > 0 ? '#f59e0b' : '#10b981'};">${formatNumber(stats.errors)}</td>
                        <td style="color: ${stats.rate_limits_429 > 0 ? '#ef4444' : '#10b981'};">${formatNumber(stats.rate_limits_429)}</td>
                        <td>${stats.avg_latency_ms.toFixed(1)}</td>
                        <td>${stats.p95_latency_ms.toFixed(1)}</td>
                    </tr>
                `;
            });

            html += `</tbody>
                </table>
            </div>`;

            // By source file/process
            html += `<div class="section">
                <h2>📁 By Source File/Process</h2>
                <table>
                    <thead>
                        <tr>
                            <th>File/Process</th>
                            <th>Credits</th>
                            <th>Requests</th>
                            <th>Errors</th>
                            <th>429s</th>
                            <th>Sections</th>
                            <th>Avg Latency (ms)</th>
                        </tr>
                    </thead>
                    <tbody>`;

            Object.entries(sourceFiles).forEach(([sourceFile, stats]) => {
                const sections = Object.entries(stats.sections)
                    .sort((a, b) => b[1] - a[1])
                    .map(([s, count]) => `${s} (${count})`)
                    .join(", ")
                    .substring(0, 50) + (Object.entries(stats.sections).length > 2 ? "..." : "");

                html += `
                    <tr>
                        <td><strong>${sourceFile}</strong></td>
                        <td>${formatNumber(stats.credits)}</td>
                        <td>${formatNumber(stats.requests)}</td>
                        <td style="color: ${stats.errors > 0 ? '#f59e0b' : '#10b981'};">${formatNumber(stats.errors)}</td>
                        <td style="color: ${stats.rate_limits_429 > 0 ? '#ef4444' : '#10b981'};">${formatNumber(stats.rate_limits_429)}</td>
                        <td>${sections}</td>
                        <td>${stats.avg_latency_ms.toFixed(1)}</td>
                    </tr>
                `;
            });

            html += `</tbody>
                </table>
            </div>`;

            // Top methods by credits
            html += `<div class="section">
                <h2>🔝 Top RPC Methods by Credits</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Method</th>
                            <th>Credits</th>
                            <th>Requests</th>
                            <th>Credits/Request</th>
                        </tr>
                    </thead>
                    <tbody>`;

            topMethods.forEach(method => {
                const creditsPerReq = (method.credits / method.requests).toFixed(1);
                html += `
                    <tr>
                        <td class="method-cell">${method.method}</td>
                        <td>${formatNumber(method.credits)}</td>
                        <td>${formatNumber(method.requests)}</td>
                        <td>${creditsPerReq}</td>
                    </tr>
                `;
            });

            html += `</tbody>
                </table>
            </div>`;

            // Refresh status
            html += `<div class="refresh-status">
                Last updated: ${new Date(summary.timestamp).toLocaleTimeString()}
                <br>Auto-refreshing every ${REFRESH_INTERVAL / 1000}s
            </div>`;

            document.getElementById("content").innerHTML = html;
        }

        function showResetConfirm() {
            document.getElementById("resetOverlay").classList.add("show");
            document.getElementById("resetConfirm").style.display = "block";
        }

        function hideResetConfirm() {
            document.getElementById("resetOverlay").classList.remove("show");
            document.getElementById("resetConfirm").style.display = "none";
        }

        async function confirmReset() {
            const resetBtn = document.querySelector(".reset-btn");
            const statusDiv = document.getElementById("resetStatus");

            resetBtn.disabled = true;
            hideResetConfirm();

            try {
                // Call the reset endpoint
                const response = await fetch("/metrics/rpc/reset", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({})
                });

                if (response.ok) {
                    const data = await response.json();
                    statusDiv.textContent = "✅ All RPC metrics reset to 0 (requests, errors, sections, methods). Helius account data preserved.";
                    statusDiv.classList.add("success", "show");

                    // Refresh dashboard after reset
                    setTimeout(() => {
                        fetchMetrics();
                        statusDiv.classList.remove("show");
                    }, 2000);
                } else {
                    statusDiv.textContent = "⚠️ Reset failed: " + (response.statusText || "Unknown error");
                    statusDiv.classList.add("error", "show");
                }
            } catch (error) {
                statusDiv.textContent = "❌ Error resetting metrics: " + error.message;
                statusDiv.classList.add("error", "show");
            } finally {
                resetBtn.disabled = false;
            }
        }

        async function refreshHelius() {
            const btn = document.getElementById("refreshBtn");
            const statusDiv = document.getElementById("resetStatus");

            btn.disabled = true;
            btn.textContent = "⏳ Syncing...";

            try {
                const response = await fetch("/metrics/helius/capture", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });

                if (response.ok) {
                    const data = await response.json();
                    statusDiv.textContent = "✅ Helius data synced! Remaining: " +
                        new Intl.NumberFormat("en-US").format(data.snapshot.credits_remaining) + " credits";
                    statusDiv.classList.add("success", "show");

                    // Refresh dashboard
                    setTimeout(() => {
                        fetchMetrics();
                        statusDiv.classList.remove("show");
                    }, 1500);
                } else {
                    statusDiv.textContent = "⚠️ Helius sync failed: " + (response.statusText || "Unknown error");
                    statusDiv.classList.add("error", "show");
                }
            } catch (error) {
                statusDiv.textContent = "❌ Error syncing Helius: " + error.message;
                statusDiv.classList.add("error", "show");
            } finally {
                btn.disabled = false;
                btn.textContent = "💎 Refresh Helius";
            }
        }

        async function runLiveComparison() {
            const btn = event.target;
            const statusDiv = document.getElementById("resetStatus");

            btn.disabled = true;
            btn.textContent = "⏳ Starting test...";

            // Show test results modal
            const overlay = document.getElementById("testResultsOverlay");
            const modal = document.getElementById("testResultsModal");
            overlay.classList.add("show");
            modal.style.display = "block";

            const contentDiv = document.getElementById("testResultsContent");

            try {
                // Step 1: Start the background test
                const startResponse = await fetch("/metrics/rpc/comparison", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });

                if (!startResponse.ok) {
                    throw new Error(`Failed to start test: ${startResponse.status}`);
                }

                const startData = await startResponse.json();
                const testId = startData.test_id;

                if (!testId) {
                    throw new Error("No test_id returned from server");
                }

                // Step 2: Poll for results with live updates
                let isComplete = false;
                let pollCount = 0;
                const maxPolls = 160; // ~320 seconds of polling with 2-second intervals (allows for 5min test + overhead)
                let allMeasurements = []; // Track all measurements for live display

                while (!isComplete && pollCount < maxPolls) {
                    pollCount++;

                    // Wait before polling
                    await new Promise(resolve => setTimeout(resolve, 2000));

                    // Poll for test status
                    const pollResponse = await fetch(`/metrics/rpc/comparison?test_id=${testId}`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" }
                    });

                    if (!pollResponse.ok) {
                        throw new Error(`Poll failed: ${pollResponse.status}`);
                    }

                    const pollData = await pollResponse.json();
                    const summary = pollData.summary || {};
                    const measurements = pollData.measurements || [];

                    // Keep track of all measurements
                    allMeasurements = measurements;

                    // Display live progress with current measurements
                    const elapsed = pollCount * 2;
                    const remaining = Math.max(0, 300 - elapsed);

                    let html = `
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;">
                            <div style="background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.5); border-radius: 8px; padding: 15px;">
                                <h4 style="color: #60a5fa; margin-bottom: 10px;">📊 Local Instrumentation</h4>
                                <div style="font-size: 28px; font-weight: bold; color: #60a5fa; margin-bottom: 5px;">
                                    ${formatNumber(summary.local_credits_used !== undefined ? summary.local_credits_used : 0)}
                                </div>
                                <div style="font-size: 12px; color: #cbd5e1;">
                                    Credits used in test period<br/>
                                    <span style="color: #94a3b8;">Per-request attribution</span>
                                </div>
                            </div>

                            <div style="background: rgba(167, 139, 250, 0.1); border: 1px solid rgba(167, 139, 250, 0.5); border-radius: 8px; padding: 15px;">
                                <h4 style="color: #d8b4fe; margin-bottom: 10px;">💎 Helius Billing</h4>
                                <div style="font-size: 28px; font-weight: bold; color: #d8b4fe; margin-bottom: 5px;">
                                    ${formatNumber(summary.helius_credits_used !== undefined ? summary.helius_credits_used : 0)}
                                </div>
                                <div style="font-size: 12px; color: #cbd5e1;">
                                    Credits used in test period<br/>
                                    <span style="color: #94a3b8;">Account-level billing</span>
                                </div>
                            </div>
                        </div>

                        <div style="background: rgba(100, 116, 139, 0.2); border-radius: 8px; padding: 15px; margin-bottom: 20px;">
                            <h4 style="color: #cbd5e1; margin-bottom: 10px;">📈 Test Results</h4>
                            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px;">
                                <div>
                                    <div style="color: #94a3b8; font-size: 12px; margin-bottom: 5px;">Difference</div>
                                    <div style="font-size: 20px; font-weight: bold; color: #60a5fa;">
                                        ${formatNumber(summary.difference !== undefined ? summary.difference : 0)}
                                    </div>
                                    <div style="font-size: 11px; color: #94a3b8;">credits</div>
                                </div>
                                <div>
                                    <div style="color: #94a3b8; font-size: 12px; margin-bottom: 5px;">Difference %</div>
                                    <div style="font-size: 20px; font-weight: bold; color: #60a5fa;">
                                        ${(summary.difference_pct !== undefined ? summary.difference_pct : 0).toFixed(1)}%
                                    </div>
                                </div>
                                <div>
                                    <div style="color: #94a3b8; font-size: 12px; margin-bottom: 5px;">Status</div>
                                    <div style="font-size: 20px; font-weight: bold; color: ${summary.status === 'CLEAN' ? '#10b981' : summary.status === 'MINOR' ? '#fbbf24' : '#ef4444'};">
                                        ${summary.status === 'CLEAN' ? '✅ Clean' : summary.status === 'MINOR' ? '⚠️ Minor' : '❌ Drift'}
                                    </div>
                                </div>
                            </div>
                        </div>

                        ${pollData.snapshots ? `
                        <div style="background: rgba(100, 116, 139, 0.15); border-radius: 8px; padding: 12px; margin-bottom: 20px; font-size: 11px; color: #cbd5e1;">
                            <h4 style="color: #cbd5e1; margin-bottom: 10px;">🔍 Helius Snapshot Points</h4>
                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px;">
                                <div style="background: rgba(34, 197, 94, 0.1); padding: 10px; border-radius: 6px; border-left: 3px solid #22c55e;">
                                    <div style="color: #22c55e; font-weight: bold;">BEFORE Test</div>
                                    <div style="color: #cbd5e1; font-size: 10px; margin-top: 3px;">
                                        ${formatNumber(pollData.snapshots.before.credits_used)} credits
                                    </div>
                                    <div style="color: #94a3b8; font-size: 9px; margin-top: 2px;">
                                        ${new Date(pollData.snapshots.before.timestamp).toLocaleTimeString()}
                                    </div>
                                </div>
                                <div style="background: rgba(34, 197, 94, 0.1); padding: 10px; border-radius: 6px; border-left: 3px solid #22c55e;">
                                    <div style="color: #22c55e; font-weight: bold;">AFTER Test</div>
                                    <div style="color: #cbd5e1; font-size: 10px; margin-top: 3px;">
                                        ${formatNumber(pollData.snapshots.after.credits_used)} credits
                                    </div>
                                    <div style="color: #94a3b8; font-size: 9px; margin-top: 2px;">
                                        ${new Date(pollData.snapshots.after.timestamp).toLocaleTimeString()}
                                    </div>
                                </div>
                            </div>
                        </div>
                        ` : ''}

                        <div style="background: rgba(100, 116, 139, 0.2); border-radius: 8px; padding: 15px; margin-bottom: 20px;">
                            <h4 style="color: #cbd5e1; margin-bottom: 10px;">📊 Live Measurements (${measurements.length} samples)
                            ${!pollData.status || pollData.status === 'running' ? ' <span style="color: #fbbf24;">⏳ Test running...</span>' : ''}</h4>
                            <div style="max-height: 300px; overflow-y: auto; font-size: 11px;">
                                ${measurements.map(m => `
                                    <div style="display: grid; grid-template-columns: 60px 100px 100px 70px; gap: 10px; padding: 8px 0; border-bottom: 1px solid rgba(100, 116, 139, 0.3);">
                                        <div><span style="color: #94a3b8;">+${m.elapsed}s</span></div>
                                        <div>Local: <span style="color: #60a5fa;">${formatNumber(m.local_credits)}</span></div>
                                        <div>Helius: <span style="color: #a78bfa;">${formatNumber(m.helius_credits)}</span></div>
                                        <div><span style="color: ${m.status === 'CLEAN' ? '#10b981' : m.status === 'MINOR' ? '#fbbf24' : '#ef4444'};">${m.status}</span></div>
                                    </div>
                                `).join('')}
                            </div>
                            <div style="margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(100, 116, 139, 0.3); color: #94a3b8; font-size: 10px;">
                                ${!pollData.status || pollData.status === 'running' ? `⏱️ ${remaining}s remaining` : ''}
                            </div>
                        </div>

                        <div style="background: rgba(100, 116, 139, 0.15); border-radius: 8px; padding: 12px; font-size: 11px; color: #cbd5e1;">
                            <strong>ℹ️ Interpretation:</strong><br/>
                            • Local = Per-request RPC instrumentation (more accurate)<br/>
                            • Helius = Account-level billing (includes all usage)<br/>
                            • Streaming subscriptions & webhooks are Helius-only (expected drift)
                        </div>
                    `;

                    document.getElementById("testResultsContent").innerHTML = html;

                    if (pollData.status === "complete") {
                        isComplete = true;
                        statusDiv.textContent = "✅ Test complete!";
                        statusDiv.classList.add("success", "show");
                    }
                }

                if (!isComplete) {
                    throw new Error("Test did not complete within timeout");
                }

            } catch (error) {
                contentDiv.innerHTML = '<p style="color: #ef4444;">❌ Error: ' + error.message + '</p>';
                statusDiv.textContent = "❌ Error: " + error.message;
                statusDiv.classList.add("error", "show");
            } finally {
                btn.disabled = false;
                btn.textContent = "🔬 Run Live Test (5 min)";
            }
        }

        function showComparisonModal() {
            const overlay = document.getElementById("comparisonOverlay");
            const modal = document.getElementById("comparisonModal");
            overlay.classList.add("show");
            modal.style.display = "block";
            loadComparisonData();
        }

        function hideComparisonModal() {
            const overlay = document.getElementById("comparisonOverlay");
            const modal = document.getElementById("comparisonModal");
            overlay.classList.remove("show");
            modal.style.display = "none";
        }

        function hideTestResults() {
            const overlay = document.getElementById("testResultsOverlay");
            const modal = document.getElementById("testResultsModal");
            overlay.classList.remove("show");
            modal.style.display = "none";
        }

        async function loadComparisonData() {
            try {
                const response = await fetch(API_URL);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const data = await response.json();

                // Safely extract data with defensive checks
                const summary = (data && data.summary) ? data.summary : {};
                const helius = (data && data.helius_snapshot) ? data.helius_snapshot : {};

                // Fallback chain for local credits
                const localCredits = summary.credits_instrumented_today !== undefined ? summary.credits_instrumented_today :
                                    (summary.credits_total !== undefined ? summary.credits_total : 0);
                const heliusCredits = helius.credits_used !== undefined ? helius.credits_used : 0;
                const prepaidCredits = helius.prepaid_credits_used !== undefined ? helius.prepaid_credits_used : 0;
                const overageCredits = helius.overage_credits_used !== undefined ? helius.overage_credits_used : 0;
                const totalHelius = heliusCredits;

                const diff = Math.abs(localCredits - totalHelius);
                const diffPercent = totalHelius > 0 ? (diff / totalHelius * 100).toFixed(1) : 0;

                let html = `
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;">
                        <div style="background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.5); border-radius: 8px; padding: 15px;">
                            <h4 style="color: #60a5fa; margin-bottom: 10px;">📊 Local Instrumentation</h4>
                            <div style="font-size: 28px; font-weight: bold; color: #60a5fa; margin-bottom: 5px;">
                                ${formatNumber(localCredits)}
                            </div>
                            <div style="font-size: 12px; color: #cbd5e1;">
                                Credits used since last reset<br/>
                                <span style="color: #94a3b8;">Per-request attribution</span>
                            </div>
                        </div>

                        <div style="background: rgba(167, 139, 250, 0.1); border: 1px solid rgba(167, 139, 250, 0.5); border-radius: 8px; padding: 15px;">
                            <h4 style="color: #d8b4fe; margin-bottom: 10px;">💎 Helius Billing</h4>
                            <div style="font-size: 28px; font-weight: bold; color: #d8b4fe; margin-bottom: 5px;">
                                ${formatNumber(totalHelius)}
                            </div>
                            <div style="font-size: 12px; color: #cbd5e1;">
                                Total credits used (prepaid + overage)<br/>
                                <span style="color: #94a3b8;">Account-level billing</span>
                            </div>
                        </div>
                    </div>

                    <div style="background: rgba(100, 116, 139, 0.2); border-radius: 8px; padding: 15px; margin-bottom: 20px;">
                        <h4 style="color: #cbd5e1; margin-bottom: 10px;">📈 Difference</h4>
                        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px;">
                            <div>
                                <div style="color: #94a3b8; font-size: 12px; margin-bottom: 5px;">Absolute Difference</div>
                                <div style="font-size: 20px; font-weight: bold; color: ${diff > 100 ? '#f97316' : '#10b981'};">
                                    ${formatNumber(Math.round(diff))}
                                </div>
                            </div>
                            <div>
                                <div style="color: #94a3b8; font-size: 12px; margin-bottom: 5px;">Relative Difference</div>
                                <div style="font-size: 20px; font-weight: bold; color: ${parseFloat(diffPercent) > 5 ? '#f97316' : '#10b981'};">
                                    ${diffPercent}%
                                </div>
                            </div>
                            <div>
                                <div style="color: #94a3b8; font-size: 12px; margin-bottom: 5px;">Status</div>
                                <div style="font-size: 20px; font-weight: bold; color: ${Math.abs(parseFloat(diffPercent)) <= 2 ? '#10b981' : Math.abs(parseFloat(diffPercent)) <= 5 ? '#fbbf24' : '#ef4444'};">
                                    ${Math.abs(parseFloat(diffPercent)) <= 2 ? '✅ Clean' : Math.abs(parseFloat(diffPercent)) <= 5 ? '⚠️ Minor' : '❌ Drift'}
                                </div>
                            </div>
                        </div>
                    </div>

                    <div style="background: rgba(100, 116, 139, 0.2); border-radius: 8px; padding: 15px;">
                        <h4 style="color: #cbd5e1; margin-bottom: 10px;">💰 Helius Breakdown</h4>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; font-size: 12px;">
                            <div>
                                <span style="color: #94a3b8;">Prepaid Used:</span>
                                <span style="color: #10b981; float: right; font-weight: bold;">${formatNumber(prepaidCredits)}</span>
                            </div>
                            <div>
                                <span style="color: #94a3b8;">Overage Used:</span>
                                <span style="color: #ef4444; float: right; font-weight: bold;">${formatNumber(overageCredits)}</span>
                            </div>
                            <div>
                                <span style="color: #94a3b8;">Overage Cost:</span>
                                <span style="color: #f59e0b; float: right; font-weight: bold;">$${(helius.overage_cost || 0).toFixed(2)}</span>
                            </div>
                            <div>
                                <span style="color: #94a3b8;">Remaining (10M):</span>
                                <span style="color: #60a5fa; float: right; font-weight: bold;">${formatNumber(helius.credits_remaining || 0)}</span>
                            </div>
                            <div style="grid-column: 1 / -1;">
                                <span style="color: #94a3b8;">RPC Usage:</span>
                                <span style="color: #06b6d4; float: right; font-weight: bold;">${formatNumber(helius.rpc_usage || 0)} calls</span>
                            </div>
                        </div>
                    </div>

                    <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid rgba(100, 116, 139, 0.3); font-size: 12px; color: #94a3b8;">
                        <strong>Note:</strong> Local credits are from per-request instrumentation. Helius shows project-level account usage. Small differences are normal due to:
                        <ul style="margin: 10px 0; padding-left: 20px;">
                            <li>Streaming/webhook usage (if tracked in Helius but not locally)</li>
                            <li>Uninstrumented RPC endpoints</li>
                            <li>Timing differences between resets and billing cycles</li>
                        </ul>
                    </div>
                `;

                document.getElementById("comparisonContent").innerHTML = html;
            } catch (error) {
                document.getElementById("comparisonContent").innerHTML =
                    `<p style="color: #ef4444;">Error loading comparison data: ${error.message}</p>`;
            }
        }

        // Initial fetch and auto-refresh
        fetchMetrics();
        setInterval(fetchMetrics, REFRESH_INTERVAL);
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
