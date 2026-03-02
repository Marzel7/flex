"""
RPC Metrics API - FastAPI endpoints for metrics dashboard.

Provides HTTP endpoints:
- GET /metrics/rpc - Full metrics summary
- GET /metrics/rpc/summary - Quick summary only
- GET /metrics/rpc/sections - Per-section breakdown
- GET /metrics/rpc/methods - Top methods by credits
- POST /metrics/rpc/reset - Reset daily counters (admin only)
"""

from fastapi import FastAPI, HTTPException, Query
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
    """Initialize recorder on startup (with 1M credits/month plan example)"""
    # Adjust plan_monthly_credits to your actual plan
    initialize_recorder(plan_monthly_credits=1_000_000)


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
    return {
        "timestamp": datetime.now().isoformat(),
        "summary": recorder.get_summary(),
        "sections": recorder.get_section_stats(),
        "top_methods": recorder.get_top_methods(limit=10),
        "alerts": recorder.get_alerts(burn_rate_threshold=100.0),
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
async def record_metric(
    section: str = Query(...),
    provider: str = Query(...),
    method: str = Query(...),
    status_code: int = Query(...),
    latency_ms: float = Query(...),
    mode: str = Query("realtime"),
    retries: int = Query(0),
    bytes_in: int = Query(0),
    bytes_out: int = Query(0),
    error: Optional[str] = Query(None),
):
    """Record a single RPC metric (called by instrumented code in other processes)"""
    recorder = get_recorder()
    credits = recorder.record_request(
        section=section,
        provider=provider,
        method=method,
        status_code=status_code,
        latency_ms=latency_ms,
        mode=mode,
        retries=retries,
        bytes_in=bytes_in,
        bytes_out=bytes_out,
        error=error,
    )
    return {"credits": credits, "status": "recorded"}


@app.post("/metrics/rpc/reset")
async def metrics_reset(admin_token: Optional[str] = Query(None)):
    """Reset daily counters (requires admin_token for security)"""
    # In production, validate admin_token against environment or config
    if not admin_token or admin_token != "SECRET_ADMIN_TOKEN":
        raise HTTPException(status_code=403, detail="Unauthorized")

    recorder = get_recorder()
    recorder.reset_daily()
    return {"message": "Daily counters reset"}


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
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🚀 FLEX RPC Metrics Dashboard</h1>
            <p>Real-time Helius credit usage tracking by component</p>
        </header>

        <div id="content" class="loading">Loading metrics...</div>
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

            let html = "";

            // Summary cards
            html += `<div class="grid">
                <div class="card">
                    <h3>Total Credits Today</h3>
                    <div class="value">${formatNumber(summary.credits_today)}</div>
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
