"""
RPC Metrics API - FastAPI endpoints for metrics dashboard.

Provides HTTP endpoints:
- GET /metrics/rpc - Full metrics summary
- GET /metrics/rpc/summary - Quick summary only
- GET /metrics/rpc/sections - Per-section breakdown
- GET /metrics/rpc/methods - Top methods by credits
- POST /metrics/rpc/reset - Reset daily counters (admin only)
"""

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime
import json
from typing import Optional

from rpc_metrics_recorder import (
    get_recorder,
    initialize_recorder,
    RPCMetricsRecorder,
)


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
