"""
RPC Metrics API v2 - Enhanced FastAPI endpoints with monitoring improvements

Provides REST endpoints for:
- Success vs attempted credit tracking
- Retry diagnostics
- 429 rate limit diagnostics with attempt numbers
- Section taxonomy validation
- Source file attribution improvements
- Optional reconciliation with Helius dashboard
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from datetime import datetime
from typing import Optional
import json
import os

from rpc_metrics_recorder_v2 import (
    get_recorder,
    initialize_recorder,
    RPCMetricsRecorder,
)

# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(
    title="FLEX RPC Metrics v2",
    description="Enhanced monitoring: success vs attempts, retries, 429 diagnostics",
    version="2.0.0",
)


@app.on_event("startup")
async def startup_event():
    """Initialize recorder on startup"""
    plan_monthly_credits = int(os.getenv("PLAN_MONTHLY_CREDITS", "1000000"))
    expected_helius_credits = int(os.getenv("EXPECTED_HELIUS_CREDITS_TODAY", "0"))

    initialize_recorder(plan_monthly_credits=plan_monthly_credits)

    if expected_helius_credits > 0:
        print(f"[METRICS] Expected Helius credits today: {expected_helius_credits}")


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok", "version": "2.0"}


# ============================================================================
# CORE METRICS ENDPOINTS
# ============================================================================

@app.get("/metrics/rpc")
async def metrics_full():
    """
    Full metrics endpoint with v2 enhancements:
    - credits_success_only (credits from status_code==200)
    - credits_all_attempts (credits from all requests including retries)
    - retry diagnostics per section
    - 429 diagnostics with attempt numbers
    """
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "summary": recorder.get_summary(),
        "sections": recorder.get_section_stats(),
        "top_methods": recorder.get_top_methods(limit=10),
        "rate_limit_diagnostics": recorder.get_rate_limit_diagnostics(),
        "retry_diagnostics": recorder.get_retry_diagnostics(),
        "alerts": recorder.get_alerts(burn_rate_threshold=100.0),
    }


@app.get("/metrics/rpc/summary")
async def metrics_summary():
    """
    Quick summary with v2 fields:
    - credits_success_only
    - credits_all_attempts
    - requests_success / requests_failed / requests_429
    - retries_total
    """
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "summary": recorder.get_summary(),
    }


@app.get("/metrics/rpc/sections")
async def metrics_sections():
    """
    Per-section breakdown with v2 enhancements:
    - credits_success_only vs credits_all_attempts per section
    - retries_total per section
    - avg_retries_per_request
    """
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "sections": recorder.get_section_stats(),
    }


@app.get("/metrics/rpc/methods")
async def metrics_methods(limit: int = Query(10, ge=1, le=50)):
    """
    Top methods by credits with v2 breakdown:
    - credits_success (estimated from success ratio)
    - credits_all_attempts (actual total)
    - requests
    - retries_total
    - avg_retries per request
    """
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "top_methods": recorder.get_top_methods(limit=limit),
    }


@app.get("/metrics/rpc/source-files")
async def metrics_source_files():
    """
    Per-source-file breakdown with v2 enhancements:
    - credits_success_only vs credits_all_attempts per source file
    - requests_success / requests_failed / requests_429
    - retries_total and avg_retries_per_request
    """
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "source_files": recorder.get_source_file_stats(),
    }


@app.get("/metrics/rpc/rate-limits")
async def metrics_rate_limits():
    """
    Comprehensive 429 rate limit diagnostics:
    - total_429_count
    - last_5min_429_count
    - 429_by_section
    - 429_by_method
    - 429_by_source_file
    - avg_retry_after_ms (from Retry-After headers)
    - attempts_by_attempt_number (which attempt in retry chain hit 429)
    """
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "rate_limit_diagnostics": recorder.get_rate_limit_diagnostics(),
    }


@app.get("/metrics/rpc/retries")
async def metrics_retries():
    """
    Retry diagnostics per section:
    - total_requests
    - total_retries
    - avg_retries_per_request
    - requests_with_retries (count of requests that had retries > 0)
    - retries_by_method
    """
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "retry_diagnostics": recorder.get_retry_diagnostics(),
    }


@app.get("/metrics/rpc/alerts")
async def metrics_alerts(burn_rate_threshold: float = Query(100.0, ge=1.0, le=10000.0)):
    """
    Get active alerts:
    - high_burn_rate (if credits/min exceeds threshold)
    - high_rate_limit (if 429s in last 5 min > 10)
    - high_error_rate (if error % > 5%)
    """
    recorder = get_recorder()
    return {
        "timestamp": datetime.now().isoformat(),
        "alerts": recorder.get_alerts(burn_rate_threshold=burn_rate_threshold),
    }


# ============================================================================
# RECORD ENDPOINT (for multi-process support)
# ============================================================================

@app.post("/metrics/rpc/record")
async def record_metric(data: dict):
    """
    Record a single RPC metric (called by instrumented code in other processes).

    Expected JSON body:
    {
        "section": "listener",
        "provider": "helius_rpc",
        "method": "getAccountInfo",
        "status_code": 200,
        "latency_ms": 45.3,
        "mode": "realtime",
        "retries": 0,
        "source_file": "pumpfun_curve_listener",
        "bytes_in": 0,
        "bytes_out": 0,
        "error": null,
        "attempt_number": 1,
        "retry_after_ms": null
    }
    """
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
        attempt_number=data.get("attempt_number", 1),
        retry_after_ms=data.get("retry_after_ms"),
    )
    return {"credits": credits, "status": "recorded"}


# ============================================================================
# ADMIN ENDPOINTS
# ============================================================================

@app.post("/metrics/rpc/reset")
async def metrics_reset(admin_token: Optional[str] = Query(None)):
    """Reset daily counters (requires admin_token for security)"""
    if not admin_token or admin_token != os.getenv("METRICS_ADMIN_TOKEN", "SECRET_ADMIN_TOKEN"):
        raise HTTPException(status_code=403, detail="Unauthorized")

    recorder = get_recorder()
    recorder.reset_daily()
    return {"message": "Daily counters reset", "timestamp": datetime.now().isoformat()}


# ============================================================================
# RECONCILIATION ENDPOINT
# ============================================================================

@app.get("/metrics/rpc/reconciliation")
async def metrics_reconciliation(helius_credits_today: int = Query(0)):
    """
    Reconciliation mode: compare FLEX metrics with Helius dashboard.

    This is for monitoring only - NO automatic adjustments.

    Args:
        helius_credits_today: Number from Helius dashboard to compare against

    Returns:
        FLEX metrics, Helius credits, and difference
    """
    recorder = get_recorder()
    summary = recorder.get_summary()
    flex_credits = summary["credits_all_attempts"]

    reconciliation = {
        "timestamp": datetime.now().isoformat(),
        "flex_credits_all_attempts": flex_credits,
        "flex_credits_success_only": summary["credits_success_only"],
        "helius_credits_today": helius_credits_today,
        "difference": helius_credits_today - flex_credits,
        "difference_percent": round((helius_credits_today - flex_credits) / helius_credits_today * 100, 2) if helius_credits_today > 0 else 0,
        "note": "Difference may be due to: (1) Uninstrumented endpoints, (2) Streaming costs not yet tracked, (3) Time zone differences in 'today' definition",
    }

    return reconciliation


# ============================================================================
# EXPORT ENDPOINTS
# ============================================================================

@app.get("/metrics/rpc/export")
async def metrics_export():
    """Export full metrics as JSON for external systems"""
    recorder = get_recorder()
    return JSONResponse(json.loads(recorder.export_json()))


# ============================================================================
# DASHBOARD
# ============================================================================

@app.get("/dashboard")
async def dashboard():
    """Serve enhanced HTML dashboard with v2 sections"""
    return HTMLResponse(DASHBOARD_HTML_V2)


# ============================================================================
# DASHBOARD HTML (v2)
# ============================================================================

DASHBOARD_HTML_V2 = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FLEX RPC Metrics Dashboard v2</title>
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
            max-width: 1600px;
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
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }

        .card {
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 15px;
            backdrop-filter: blur(10px);
        }

        .card h3 {
            font-size: 11px;
            text-transform: uppercase;
            color: #64748b;
            margin-bottom: 8px;
            letter-spacing: 1px;
        }

        .card .value {
            font-size: 28px;
            font-weight: bold;
            color: #3b82f6;
            margin-bottom: 3px;
        }

        .card .unit {
            font-size: 11px;
            color: #94a3b8;
        }

        .card.success { border-color: #10b981; }
        .card.success .value { color: #10b981; }

        .card.failed { border-color: #ef4444; }
        .card.failed .value { color: #ef4444; }

        .card.warning { border-color: #f59e0b; }
        .card.warning .value { color: #f59e0b; }

        .section {
            margin-bottom: 30px;
        }

        .section h2 {
            font-size: 16px;
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
            font-size: 13px;
        }

        thead {
            background: rgba(15, 23, 42, 0.8);
        }

        th {
            padding: 12px;
            text-align: left;
            font-size: 11px;
            text-transform: uppercase;
            color: #94a3b8;
            letter-spacing: 0.5px;
            border-bottom: 1px solid #334155;
            font-weight: 600;
        }

        td {
            padding: 10px 12px;
            border-bottom: 1px solid #334155;
        }

        tr:hover {
            background: rgba(59, 130, 246, 0.1);
        }

        .method-cell {
            font-family: "Courier New", monospace;
            color: #60a5fa;
            font-size: 12px;
        }

        .alert-item {
            padding: 12px;
            margin: 10px 0;
            border-left: 4px solid #f59e0b;
            background: rgba(245, 158, 11, 0.1);
            border-radius: 4px;
            font-size: 13px;
        }

        .alert-item.critical {
            border-left-color: #ef4444;
            background: rgba(239, 68, 68, 0.1);
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

        .split-value {
            display: flex;
            justify-content: space-between;
            gap: 10px;
            align-items: center;
        }

        .split-value .label {
            font-size: 10px;
            color: #94a3b8;
        }

        .split-value .val {
            font-weight: bold;
            color: #3b82f6;
            flex: 1;
            text-align: right;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🚀 FLEX RPC Metrics Dashboard v2</h1>
            <p>Enhanced monitoring: Success vs Attempts | Retries | 429 Diagnostics</p>
        </header>

        <div id="content" class="loading">Loading metrics...</div>
    </div>

    <script>
        const API_URL = "/metrics/rpc";
        const REFRESH_INTERVAL = 5000;

        async function fetchMetrics() {
            try {
                const response = await fetch(API_URL);
                const data = await response.json();

                // Also fetch source files and rate limits
                const sourceResponse = await fetch("/metrics/rpc/source-files");
                const sourceData = await sourceResponse.json();
                data.source_files = sourceData.source_files;

                const rateLimitResponse = await fetch("/metrics/rpc/rate-limits");
                const rateLimitData = await rateLimitResponse.json();
                data.rate_limit_diagnostics = rateLimitData.rate_limit_diagnostics;

                renderDashboard(data);
            } catch (error) {
                document.getElementById("content").innerHTML = `<p style="color: #ef4444;">Error loading metrics: ${error.message}</p>`;
            }
        }

        function formatNumber(num) {
            return new Intl.NumberFormat("en-US").format(Math.round(num));
        }

        function renderDashboard(data) {
            const summary = data.summary;
            const sections = data.sections;
            const topMethods = data.top_methods;
            const sourceFiles = data.source_files || {};
            const rateLimitDiags = data.rate_limit_diagnostics || {};
            const alerts = data.alerts;

            let html = "";

            // Summary cards - v2 layout with success vs attempts split
            html += `<div class="grid">
                <div class="card">
                    <h3>Success Credits</h3>
                    <div class="value">${formatNumber(summary.credits_success_only)}</div>
                    <div class="unit">from successful requests</div>
                </div>
                <div class="card">
                    <h3>All Attempts Credits</h3>
                    <div class="value">${formatNumber(summary.credits_all_attempts)}</div>
                    <div class="unit">including retries</div>
                </div>
                <div class="card success">
                    <h3>Successful Requests</h3>
                    <div class="value">${formatNumber(summary.requests_success)}</div>
                </div>
                <div class="card failed">
                    <h3>Failed Requests</h3>
                    <div class="value">${formatNumber(summary.requests_failed)}</div>
                </div>
                <div class="card warning">
                    <h3>Rate Limits (429)</h3>
                    <div class="value">${formatNumber(summary.rate_limits_429_total)}</div>
                    <div class="unit">last 5min: ${summary.rate_limits_429_last_5min}</div>
                </div>
                <div class="card">
                    <h3>Avg Retries/Request</h3>
                    <div class="value">${summary.avg_retries_per_request.toFixed(2)}</div>
                </div>
            </div>`;

            // Alerts
            if (alerts.length > 0) {
                html += `<div class="section">
                    <h2>⚠️ Active Alerts</h2>`;
                alerts.forEach(alert => {
                    html += `
                        <div class="alert-item ${alert.level === 'critical' ? 'critical' : ''}">
                            <strong>${alert.type.toUpperCase()}:</strong> ${alert.message}
                        </div>
                    `;
                });
                html += `</div>`;
            }

            // Per-section breakdown - v2 with success vs attempts
            html += `<div class="section">
                <h2>📊 By Component Section (Success vs Attempts)</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Section</th>
                            <th>Success Cr</th>
                            <th>All Cr</th>
                            <th>Success Req</th>
                            <th>Failed Req</th>
                            <th>429s</th>
                            <th>Avg Retries</th>
                            <th>Avg Latency</th>
                        </tr>
                    </thead>
                    <tbody>`;

            Object.entries(sections).forEach(([section, stats]) => {
                html += `
                    <tr>
                        <td><strong>${section}</strong></td>
                        <td>${formatNumber(stats.credits_success_only)}</td>
                        <td>${formatNumber(stats.credits_all_attempts)}</td>
                        <td style="color: #10b981;">${formatNumber(stats.requests_success)}</td>
                        <td style="color: #ef4444;">${formatNumber(stats.requests_failed)}</td>
                        <td style="color: ${stats.requests_429 > 0 ? '#f59e0b' : '#10b981'};">${formatNumber(stats.requests_429)}</td>
                        <td>${stats.avg_retries_per_request.toFixed(2)}</td>
                        <td>${stats.avg_latency_ms.toFixed(1)}ms</td>
                    </tr>
                `;
            });

            html += `</tbody></table></div>`;

            // Source files - v2 with success vs attempts
            html += `<div class="section">
                <h2>📁 By Source File/Process (Success vs Attempts)</h2>
                <table>
                    <thead>
                        <tr>
                            <th>File/Process</th>
                            <th>Success Cr</th>
                            <th>All Cr</th>
                            <th>Total Req</th>
                            <th>Success Req</th>
                            <th>Failed Req</th>
                            <th>429s</th>
                            <th>Avg Retries</th>
                        </tr>
                    </thead>
                    <tbody>`;

            Object.entries(sourceFiles).forEach(([source, stats]) => {
                html += `
                    <tr>
                        <td><strong>${source}</strong></td>
                        <td>${formatNumber(stats.credits_success_only)}</td>
                        <td>${formatNumber(stats.credits_all_attempts)}</td>
                        <td>${formatNumber(stats.requests)}</td>
                        <td style="color: #10b981;">${formatNumber(stats.requests_success)}</td>
                        <td style="color: #ef4444;">${formatNumber(stats.requests_failed)}</td>
                        <td style="color: ${stats.requests_429 > 0 ? '#f59e0b' : '#10b981'};">${formatNumber(stats.requests_429)}</td>
                        <td>${stats.avg_retries_per_request.toFixed(2)}</td>
                    </tr>
                `;
            });

            html += `</tbody></table></div>`;

            // 429 Rate Limit Diagnostics
            if (Object.keys(rateLimitDiags).length > 0) {
                html += `<div class="section">
                    <h2>🚨 Rate Limit (429) Diagnostics</h2>
                    <table>
                        <thead>
                            <tr>
                                <th>Metric</th>
                                <th>Value</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td><strong>Total 429 Count</strong></td>
                                <td>${rateLimitDiags.total_429_count}</td>
                            </tr>
                            <tr>
                                <td><strong>Last 5 Minutes</strong></td>
                                <td>${rateLimitDiags.last_5min_429_count}</td>
                            </tr>
                            <tr>
                                <td><strong>Avg Retry-After (ms)</strong></td>
                                <td>${rateLimitDiags.avg_retry_after_ms.toFixed(1)}</td>
                            </tr>
                        </tbody>
                    </table>

                    <h3 style="margin-top: 15px; margin-bottom: 10px; color: #94a3b8; font-size: 12px;">By Section:</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Section</th>
                                <th>429 Count</th>
                            </tr>
                        </thead>
                        <tbody>`;

                Object.entries(rateLimitDiags.by_section || {}).forEach(([section, count]) => {
                    html += `
                        <tr>
                            <td>${section}</td>
                            <td>${count}</td>
                        </tr>
                    `;
                });

                html += `</tbody></table></div>`;
            }

            // Top methods
            html += `<div class="section">
                <h2>🔝 Top RPC Methods (Success vs Attempts)</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Method</th>
                            <th>Success Cr</th>
                            <th>All Cr</th>
                            <th>Requests</th>
                            <th>Retries</th>
                            <th>Avg Retries</th>
                        </tr>
                    </thead>
                    <tbody>`;

            topMethods.forEach(method => {
                html += `
                    <tr>
                        <td class="method-cell">${method.method}</td>
                        <td>${formatNumber(method.credits_success)}</td>
                        <td>${formatNumber(method.credits_all_attempts)}</td>
                        <td>${formatNumber(method.requests)}</td>
                        <td>${method.retries_total}</td>
                        <td>${method.avg_retries.toFixed(2)}</td>
                    </tr>
                `;
            });

            html += `</tbody></table></div>`;

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
