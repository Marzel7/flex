#!/usr/bin/env python3
"""
Pump.Fun → PumpSwap Migration Tracking UI

Displays tokens that have migrated from Pump.Fun to PumpSwap with:
- Risk scores (pre-migration analysis)
- Detection prices and times
- Migration prices
- Current live prices
- Time to migration
"""

import sqlite3
import json
import requests
from datetime import datetime
from flask import Flask, jsonify, render_template_string
from typing import Dict, List, Optional
import os
import time

# Database
DB_PATH = "pumpswap_tokens.db"

# Flask app
app = Flask(__name__)

# =========================================================================
# DATABASE QUERIES
# =========================================================================

def get_migrated_tokens() -> List[Dict]:
    """Get all tokens that have migrated with analysis data"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                mint,
                analyzed_at,
                has_migrated,
                migrated_at,
                rug_probability,
                risk_level,
                amm_rug_probability,
                amm_risk_level,
                time_to_migration_seconds,
                events_parsed,
                post_migration_rug_probability,
                post_migration_risk_level
            FROM token_analysis
            WHERE has_migrated = 1
            ORDER BY migrated_at DESC
        """)

        tokens = []
        for row in cursor.fetchall():
            tokens.append({
                'mint': row['mint'],
                'analyzed_at': row['analyzed_at'],
                'migrated_at': row['migrated_at'],
                'rug_probability': row['rug_probability'],
                'risk_level': row['risk_level'],
                'amm_rug_probability': row['amm_rug_probability'],
                'amm_risk_level': row['amm_risk_level'],
                'time_to_migration_seconds': row['time_to_migration_seconds'],
                'has_premigration_data': row['events_parsed'] > 0,
                'post_migration_rug_probability': row['post_migration_rug_probability'],
                'post_migration_risk_level': row['post_migration_risk_level']
            })

        conn.close()
        return tokens
    except Exception as e:
        print(f"[DB] Error fetching migrated tokens: {e}")
        return []


def get_token_price(token_mint: str) -> Optional[float]:
    """Fetch current price for a token from Jupiter API"""
    try:
        response = requests.get(
            f"https://api.jup.ag/price/v2?ids={token_mint}",
            timeout=10
        )
        data = response.json()

        if "data" in data and token_mint in data["data"]:
            return data["data"][token_mint].get("price", 0)
        return None
    except Exception as e:
        print(f"[PRICE] Error fetching price for {token_mint[:30]}: {e}")
        return None


# =========================================================================
# FLASK ROUTES
# =========================================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pump.Fun → PumpSwap Migration Tracker</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1e1e2e 0%, #2d2d44 100%);
            color: #e0e0e0;
            padding: 20px;
            min-height: 100vh;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        .header {
            background: rgba(0, 0, 0, 0.3);
            padding: 30px;
            border-radius: 12px;
            margin-bottom: 30px;
            border-left: 4px solid #00d4ff;
        }

        .header h1 {
            font-size: 28px;
            margin-bottom: 10px;
        }

        .header p {
            color: #a0a0a0;
            font-size: 14px;
        }

        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }

        .stat-card {
            background: rgba(0, 0, 0, 0.3);
            padding: 20px;
            border-radius: 8px;
            border: 1px solid rgba(0, 212, 255, 0.2);
        }

        .stat-label {
            color: #a0a0a0;
            font-size: 12px;
            text-transform: uppercase;
            margin-bottom: 8px;
        }

        .stat-value {
            font-size: 28px;
            font-weight: bold;
            color: #00d4ff;
        }

        .tokens-table {
            width: 100%;
            border-collapse: collapse;
            background: rgba(0, 0, 0, 0.2);
            border-radius: 8px;
            overflow: hidden;
        }

        .tokens-table thead {
            background: rgba(0, 0, 0, 0.4);
            border-bottom: 2px solid rgba(0, 212, 255, 0.3);
        }

        .tokens-table th {
            padding: 15px;
            text-align: left;
            font-size: 12px;
            text-transform: uppercase;
            color: #00d4ff;
            font-weight: 600;
        }

        .tokens-table td {
            padding: 15px;
            border-bottom: 1px solid rgba(0, 212, 255, 0.1);
            font-size: 13px;
        }

        .tokens-table tbody tr:hover {
            background: rgba(0, 212, 255, 0.05);
        }

        .mint {
            font-family: 'Courier New', monospace;
            font-size: 11px;
            color: #00d4ff;
            max-width: 350px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .risk-score {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }

        .risk-low {
            background: rgba(34, 197, 94, 0.2);
            color: #22c55e;
        }

        .risk-medium {
            background: rgba(234, 179, 8, 0.2);
            color: #eab308;
        }

        .risk-high {
            background: rgba(239, 68, 68, 0.2);
            color: #ef4444;
        }

        .price-positive {
            color: #22c55e;
        }

        .price-negative {
            color: #ef4444;
        }

        .time-badge {
            background: rgba(0, 212, 255, 0.1);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            color: #00d4ff;
        }

        .loading {
            text-align: center;
            padding: 40px;
            color: #a0a0a0;
        }

        .no-data {
            text-align: center;
            padding: 40px;
            color: #a0a0a0;
            background: rgba(0, 0, 0, 0.2);
            border-radius: 8px;
            margin-top: 20px;
        }

        .refresh-info {
            color: #a0a0a0;
            font-size: 12px;
            margin-top: 20px;
            text-align: center;
        }

        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.6);
            animation: fadeIn 0.3s;
        }

        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        .modal-content {
            background-color: #1e1e2e;
            margin: 5% auto;
            padding: 30px;
            border: 1px solid rgba(0, 212, 255, 0.3);
            border-radius: 12px;
            width: 90%;
            max-width: 800px;
            max-height: 80vh;
            overflow-y: auto;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
        }

        .modal-content h2 {
            color: #00d4ff;
            margin-bottom: 20px;
            font-size: 20px;
        }

        .modal-content h3 {
            color: #00d4ff;
            margin-top: 20px;
            margin-bottom: 10px;
            font-size: 14px;
            text-transform: uppercase;
        }

        .close {
            color: #a0a0a0;
            float: right;
            font-size: 28px;
            font-weight: bold;
            cursor: pointer;
            line-height: 20px;
        }

        .close:hover,
        .close:focus {
            color: #00d4ff;
        }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        .metric {
            background: rgba(0, 0, 0, 0.3);
            padding: 15px;
            border-radius: 8px;
            border-left: 3px solid #00d4ff;
        }

        .metric label {
            display: block;
            color: #a0a0a0;
            font-size: 11px;
            text-transform: uppercase;
            margin-bottom: 8px;
        }

        .metric span {
            display: block;
            color: #00d4ff;
            font-size: 16px;
            font-weight: 600;
            font-family: 'Courier New', monospace;
        }

        .risk-section {
            background: rgba(0, 0, 0, 0.3);
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 15px;
        }

        .risk-section p {
            margin: 8px 0;
            color: #e0e0e0;
        }

        .risk-section label {
            color: #a0a0a0;
            font-size: 12px;
            text-transform: uppercase;
        }

        .risk-value {
            color: #00d4ff;
            font-weight: 600;
            margin-left: 10px;
        }

        .mint-link {
            cursor: pointer;
            color: #00d4ff;
            text-decoration: none;
            border-bottom: 1px dotted #00d4ff;
            transition: all 0.2s;
        }

        .mint-link:hover {
            text-decoration: none;
            opacity: 0.8;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Pump.Fun → PumpSwap Migration Tracker</h1>
            <p>Real-time monitoring of tokens that migrated from Pump.Fun bonding curve to PumpSwap AMM</p>
        </div>

        <div class="stats" id="stats">
            <div class="stat-card">
                <div class="stat-label">Total Migrations</div>
                <div class="stat-value" id="total-migrations">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">With Pre-Analysis</div>
                <div class="stat-value" id="with-analysis">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">High Risk</div>
                <div class="stat-value" id="high-risk">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Low Risk</div>
                <div class="stat-value" id="low-risk">0</div>
            </div>
        </div>

        <div id="tokens-container">
            <div class="loading">Loading migrated tokens...</div>
        </div>

        <div class="refresh-info">Auto-refreshing every 5 seconds</div>
    </div>

    <!-- Metrics Modal -->
    <div id="metricsModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeTokenMetrics()">&times;</span>
            <h2>Token Metrics - <span id="modalMint" style="font-family: monospace; font-size: 14px;"></span></h2>

            <h3>Risk Metrics</h3>
            <div class="metrics-grid" id="metricsGrid">
                <!-- Populated by JavaScript -->
            </div>

            <h3>Risk Scores</h3>
            <div class="risk-section" id="riskSection">
                <!-- Populated by JavaScript -->
            </div>

            <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid rgba(0, 212, 255, 0.2);">
                <p style="color: #a0a0a0; font-size: 12px;">
                    💡 <strong>Tip:</strong> Click "DexTools" link below to view live trading data
                </p>
                <a id="dextoolsLink" href="#" target="_blank" style="color: #00d4ff; margin-top: 10px; display: inline-block;">
                    → View on DexTools
                </a>
            </div>
        </div>
    </div>

    <script>
        async function loadTokens() {
            try {
                const response = await fetch('/api/migrated-tokens');
                const data = await response.json();

                if (!data.tokens || data.tokens.length === 0) {
                    document.getElementById('tokens-container').innerHTML =
                        '<div class="no-data">No migrations recorded yet. Monitoring Pump.Fun...</div>';
                    return;
                }

                // Update stats
                updateStats(data);

                // Build table
                buildTable(data.tokens);
            } catch (error) {
                console.error('Error loading tokens:', error);
                document.getElementById('tokens-container').innerHTML =
                    '<div class="no-data">Error loading data. Please refresh.</div>';
            }
        }

        function updateStats(data) {
            const tokens = data.tokens;
            const withAnalysis = tokens.filter(t => t.has_premigration_data).length;
            const highRisk = tokens.filter(t => t.risk_level?.includes('HIGH')).length;
            const lowRisk = tokens.filter(t => t.risk_level?.includes('LOW')).length;

            document.getElementById('total-migrations').textContent = tokens.length;
            document.getElementById('with-analysis').textContent = withAnalysis;
            document.getElementById('high-risk').textContent = highRisk;
            document.getElementById('low-risk').textContent = lowRisk;
        }

        function buildTable(tokens) {
            const html = `
                <table class="tokens-table">
                    <thead>
                        <tr>
                            <th>Token Mint</th>
                            <th>Risk Score</th>
                            <th>Analysis Data</th>
                            <th>Migrated</th>
                            <th>Time to Migration</th>
                            <th>Current Price</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${tokens.map(token => `
                            <tr>
                                <td class="mint"><a href="#" onclick="showTokenMetrics('${token.mint}'); return false;" class="mint-link" title="Click for metrics, Ctrl+Click for DexTools">${token.mint}</a></td>
                                <td>
                                    ${(() => {
                                        const preScore = token.has_premigration_data ? (token.rug_probability * 100).toFixed(1) + '%' : '—';
                                        const postScore = token.post_migration_rug_probability !== null ? (token.post_migration_rug_probability * 100).toFixed(1) + '%' : '—';
                                        const preClass = token.has_premigration_data ? getRiskClass(token.risk_level) : '';
                                        const postClass = token.post_migration_rug_probability !== null ? getRiskClass(token.post_migration_risk_level) : '';

                                        return `<span class="risk-score ${preClass}">${preScore}</span> / <span class="risk-score ${postClass}">${postScore}</span>`;
                                    })()}
                                </td>
                                <td>
                                    ${token.has_premigration_data ?
                                        '<span style="color: #00d4ff; font-size: 16px;">✓</span>' :
                                        '<span style="color: #a0a0a0; font-size: 16px;">✗</span>'
                                    }
                                </td>
                                <td>
                                    ${formatDate(token.migrated_at)}
                                </td>
                                <td>
                                    ${token.has_premigration_data && token.time_to_migration_seconds ?
                                        `<span class="time-badge">${formatTime(token.time_to_migration_seconds)}</span>` :
                                        '<span style="color: #a0a0a0;">—</span>'
                                    }
                                </td>
                                <td id="price-${token.mint}">
                                    <span style="color: #a0a0a0;">Loading...</span>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;

            document.getElementById('tokens-container').innerHTML = html;

            // Load prices
            tokens.forEach(token => {
                loadPrice(token.mint);
            });
        }

        async function loadPrice(mint) {
            try {
                const response = await fetch(`/api/token-price/${mint}`);
                const data = await response.json();

                if (data.price !== null) {
                    const priceElement = document.getElementById(`price-${mint}`);
                    priceElement.innerHTML = `$${data.price.toFixed(8)}`;
                }
            } catch (error) {
                console.error(`Error loading price for ${mint}:`, error);
            }
        }

        function getRiskClass(riskLevel) {
            if (!riskLevel) return 'risk-medium';
            if (riskLevel.includes('HIGH')) return 'risk-high';
            if (riskLevel.includes('LOW')) return 'risk-low';
            return 'risk-medium';
        }

        function formatDate(timestamp) {
            if (!timestamp) return '-';
            return new Date(timestamp * 1000).toLocaleString();
        }

        function formatTime(seconds) {
            if (!seconds) return '-';
            const minutes = Math.floor(seconds / 60);
            const secs = seconds % 60;
            return `${minutes}m ${secs}s`;
        }

        // Load tokens immediately and then every 5 seconds
        loadTokens();
        setInterval(loadTokens, 5000);

        // Metrics Modal Functions
        async function showTokenMetrics(mint) {
            const modal = document.getElementById('metricsModal');
            document.getElementById('modalMint').textContent = mint;

            try {
                const response = await fetch(`/api/token-metrics/${mint}`);
                const data = await response.json();

                if (data.error) {
                    alert('Token metrics not found');
                    return;
                }

                // Populate metrics grid
                const metricsGrid = document.getElementById('metricsGrid');
                const metrics = data.metrics;
                const metricLabels = {
                    'mint_concentration': 'Mint Concentration',
                    'unique_minters_ratio': 'Unique Minters',
                    'sell_suppression_ratio': 'Sell Suppression',
                    'mint_velocity_sec': 'Mint Velocity (per sec)',
                    'buy_size_variance': 'Buy Size Variance',
                    'sell_volume_concentration': 'Sell Volume Concentration',
                    'creator_activity_ratio': 'Creator Activity'
                };

                metricsGrid.innerHTML = '';

                // Show notice if using post-migration data
                if (data.metrics_source === 'post-migration') {
                    metricsGrid.innerHTML = `
                        <div style="grid-column: 1 / -1; padding: 15px; background: rgba(234, 179, 8, 0.1); border-left: 3px solid #eab308; border-radius: 8px; margin-bottom: 15px;">
                            <p style="color: #eab308; margin: 0; font-size: 13px;">
                                ⚠️ <strong>No pre-migration data</strong> - Using post-migration analysis only
                            </p>
                        </div>
                    `;
                }

                Object.keys(metricLabels).forEach(key => {
                    const value = metrics[key] !== null && metrics[key] > 0 ? metrics[key].toFixed(4) : '—';
                    metricsGrid.innerHTML += `
                        <div class="metric">
                            <label>${metricLabels[key]}</label>
                            <span>${value}</span>
                        </div>
                    `;
                });

                // Populate risk section
                const riskSection = document.getElementById('riskSection');
                const risk = data.risk;
                const preRug = risk.pre_rug_probability !== null ? (risk.pre_rug_probability * 100).toFixed(1) : '—';
                const postRug = risk.post_rug_probability !== null ? (risk.post_rug_probability * 100).toFixed(1) : '—';
                const ammRug = risk.amm_rug_probability !== null ? (risk.amm_rug_probability * 100).toFixed(1) : '—';

                riskSection.innerHTML = `
                    <p>
                        <label>Pre-Migration Rug Probability:</label>
                        <span class="risk-value">${preRug}%</span>
                        <span style="color: #a0a0a0; margin-left: 10px;">${risk.pre_risk_level || '—'}</span>
                    </p>
                    <p>
                        <label>AMM Pool Rug Probability:</label>
                        <span class="risk-value">${ammRug}%</span>
                        <span style="color: #a0a0a0; margin-left: 10px;">${risk.amm_risk_level || '—'}</span>
                    </p>
                    <p>
                        <label>Post-Migration Rug Probability:</label>
                        <span class="risk-value">${postRug}%</span>
                        <span style="color: #a0a0a0; margin-left: 10px;">${risk.post_risk_level || '—'}</span>
                    </p>
                    <p style="margin-top: 15px; color: #a0a0a0; font-size: 12px;">
                        ${data.has_premigration_data ? '✅ Pre-migration data available' : '⚠️ No pre-migration data (detected at migration time)'}
                    </p>
                `;

                // Set DexTools link
                document.getElementById('dextoolsLink').href = `https://www.dextools.io/app/solana/token/${mint}`;

                // Show modal
                modal.style.display = 'block';
            } catch (error) {
                console.error('Error loading metrics:', error);
                alert('Failed to load token metrics');
            }
        }

        function closeTokenMetrics() {
            document.getElementById('metricsModal').style.display = 'none';
        }

        // Close modal when clicking outside
        window.onclick = function(event) {
            const modal = document.getElementById('metricsModal');
            if (event.target === modal) {
                modal.style.display = 'none';
            }
        }

        // Close modal when pressing Escape
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape') {
                closeTokenMetrics();
            }
        });
    </script>
</body>
</html>
"""


@app.route('/')
def index():
    """Serve the migration tracking dashboard"""
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/migrated-tokens')
def api_migrated_tokens():
    """Get all migrated tokens with analysis data"""
    tokens = get_migrated_tokens()
    return jsonify({'tokens': tokens})


@app.route('/api/token-price/<token_mint>')
def api_token_price(token_mint: str):
    """Get current price for a specific token"""
    price = get_token_price(token_mint)
    return jsonify({'mint': token_mint, 'price': price})


@app.route('/api/token-metrics/<token_mint>')
def api_token_metrics(token_mint: str):
    """Get detailed risk metrics for a specific token"""
    start = time.time()
    try:
        t1 = time.time()
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        t2 = time.time()
        cursor.execute("""
            SELECT
                mint,
                mint_concentration,
                unique_minters_ratio,
                sell_suppression_ratio,
                mint_velocity_sec,
                buy_size_variance,
                sell_volume_concentration,
                creator_activity_ratio,
                rug_probability,
                risk_level,
                amm_rug_probability,
                amm_risk_level,
                post_migration_rug_probability,
                post_migration_risk_level,
                post_migration_mint_concentration,
                post_migration_unique_minters_ratio,
                post_migration_sell_suppression_ratio,
                post_migration_mint_velocity_sec,
                post_migration_buy_size_variance,
                post_migration_sell_volume_concentration,
                post_migration_creator_activity_ratio,
                events_parsed
            FROM token_analysis
            WHERE mint = ?
        """, (token_mint,))

        t3 = time.time()
        row = cursor.fetchone()
        conn.close()
        t4 = time.time()

        print(f"[METRICS] Connect: {(t2-t1)*1000:.1f}ms, Query: {(t3-t2)*1000:.1f}ms, Fetch: {(t4-t3)*1000:.1f}ms")

        if not row:
            return jsonify({'error': 'Token not found'}), 404

        has_pre = row['events_parsed'] > 0
        has_post = row['post_migration_mint_concentration'] is not None

        # Use post-migration metrics if available (most recent analysis), otherwise pre-migration, then zeros
        metrics_to_use = {
            'mint_concentration': row['post_migration_mint_concentration'] if has_post else (row['mint_concentration'] if has_pre else 0),
            'unique_minters_ratio': row['post_migration_unique_minters_ratio'] if has_post else (row['unique_minters_ratio'] if has_pre else 0),
            'sell_suppression_ratio': row['post_migration_sell_suppression_ratio'] if has_post else (row['sell_suppression_ratio'] if has_pre else 0),
            'mint_velocity_sec': row['post_migration_mint_velocity_sec'] if has_post else (row['mint_velocity_sec'] if has_pre else 0),
            'buy_size_variance': row['post_migration_buy_size_variance'] if has_post else (row['buy_size_variance'] if has_pre else 0),
            'sell_volume_concentration': row['post_migration_sell_volume_concentration'] if has_post else (row['sell_volume_concentration'] if has_pre else 0),
            'creator_activity_ratio': row['post_migration_creator_activity_ratio'] if has_post else (row['creator_activity_ratio'] if has_pre else 0)
        }

        elapsed = time.time() - start
        print(f"[METRICS] Total time: {elapsed*1000:.1f}ms")

        return jsonify({
            'mint': row['mint'],
            'has_premigration_data': has_pre,
            'has_postmigration_metrics': has_post,
            'metrics_source': 'post-migration' if has_post else ('pre-migration' if has_pre else 'none'),
            'metrics': metrics_to_use,
            'risk': {
                'pre_rug_probability': row['rug_probability'],
                'pre_risk_level': row['risk_level'],
                'post_rug_probability': row['post_migration_rug_probability'],
                'post_risk_level': row['post_migration_risk_level'],
                'amm_rug_probability': row['amm_rug_probability'],
                'amm_risk_level': row['amm_risk_level']
            }
        })
    except Exception as e:
        print(f"[API] Error fetching metrics for {token_mint}: {e}")
        return jsonify({'error': str(e)}), 500


# =========================================================================
# MAIN
# =========================================================================

if __name__ == '__main__':
    print("[FLASK] Starting Migration Tracker UI...")
    print("[FLASK] Dashboard available at http://localhost:5002")
    print("[FLASK] Database: " + DB_PATH)
    app.run(host='0.0.0.0', port=5002, debug=False)
