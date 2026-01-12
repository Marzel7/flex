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
    """Get all analyzed post-migration tokens"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Try new schema first, then fall back to old schema
        try:
            cursor.execute("""
                SELECT
                    mint,
                    analyzed_at,
                    total_txs,
                    total_events,
                    rug_probability,
                    risk_level,
                    coverage
                FROM token_analysis
                ORDER BY analyzed_at DESC
            """)
            use_new_schema = True
        except sqlite3.OperationalError:
            # Fall back to old schema - map old columns to new ones
            cursor.execute("""
                SELECT
                    mint,
                    analyzed_at,
                    rug_probability,
                    risk_level,
                    events_parsed as total_events,
                    0 as total_txs,
                    COALESCE(pre_migration_coverage, 0) as coverage
                FROM token_analysis
                ORDER BY analyzed_at DESC
            """)
            use_new_schema = False

        tokens = []
        for row in cursor.fetchall():
            tokens.append({
                'mint': row['mint'],
                'analyzed_at': row['analyzed_at'],
                'rug_probability': row['rug_probability'] if row['rug_probability'] else 0,
                'risk_level': row['risk_level'],
                'total_txs': row['total_txs'],
                'total_events': row['total_events'],
                'coverage': row['coverage']
            })

        conn.close()
        return tokens
    except Exception as e:
        print(f"[DB] Error fetching analyzed tokens: {e}")
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
            color: #00d4ff;
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
            const highRisk = tokens.filter(t => t.risk_level?.includes('HIGH')).length;
            const mediumRisk = tokens.filter(t => t.risk_level?.includes('MEDIUM')).length;
            const lowRisk = tokens.filter(t => t.risk_level?.includes('LOW')).length;

            document.getElementById('total-migrations').textContent = tokens.length;
            document.getElementById('with-analysis').textContent = tokens.length;
            document.getElementById('high-risk').textContent = highRisk;
            document.getElementById('low-risk').textContent = lowRisk;
        }

        function buildTable(tokens) {
            const html = `
                <table class="tokens-table">
                    <thead>
                        <tr>
                            <th>Token Mint</th>
                            <th>Risk Level</th>
                            <th>Risk Score</th>
                            <th>Transactions</th>
                            <th>Events</th>
                            <th>Coverage</th>
                            <th>Analyzed</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${tokens.map(token => `
                            <tr>
                                <td class="mint"><a href="#" onclick="showTokenMetrics('${token.mint}'); return false;" class="mint-link" title="Click for metrics">${token.mint}</a></td>
                                <td>
                                    <span class="risk-score ${getRiskClass(token.risk_level)}">${token.risk_level}</span>
                                </td>
                                <td>
                                    ${token.rug_probability !== null && token.rug_probability !== undefined ? (token.rug_probability * 100).toFixed(1) + '%' : '—'}
                                </td>
                                <td>
                                    ${token.total_txs}
                                </td>
                                <td>
                                    ${token.total_events}
                                </td>
                                <td>
                                    ${token.coverage ? token.coverage.toFixed(1) + '%' : '—'}
                                </td>
                                <td>
                                    ${formatDate(token.analyzed_at)}
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;

            document.getElementById('tokens-container').innerHTML = html;

            // Load prices in background with delays to avoid network saturation
            tokens.forEach((token, index) => {
                setTimeout(() => {
                    loadPrice(token.mint);
                }, index * 50);  // Stagger requests 50ms apart
            });
        }

        async function loadPrice(mint) {
            try {
                const response = await fetch(`/api/token-price/${mint}`, {
                    signal: priceLoadController.signal
                });
                const data = await response.json();

                if (data.price !== null) {
                    const priceElement = document.getElementById(`price-${mint}`);
                    if (priceElement) {
                        priceElement.innerHTML = `$${data.price.toFixed(8)}`;
                    }
                }
            } catch (error) {
                if (error.name !== 'AbortError') {
                    console.error(`Error loading price for ${mint}:`, error);
                }
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

        // Abort controller for price loading - allows canceling requests when modal opens
        let priceLoadController = new AbortController();

        // Load tokens immediately and then every 5 seconds
        loadTokens();
        setInterval(loadTokens, 5000);

        // Metrics Modal Functions
        async function showTokenMetrics(mint) {
            // Cancel ongoing price fetches to free up bandwidth for modal
            priceLoadController.abort();
            priceLoadController = new AbortController();
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

                // Show notice if using post-migration data without pre-migration data
                if (data.metrics_source === 'post-migration' && !data.has_premigration_data) {
                    metricsGrid.innerHTML = `
                        <div style="grid-column: 1 / -1; padding: 15px; background: rgba(234, 179, 8, 0.1); border-left: 3px solid #eab308; border-radius: 8px; margin-bottom: 15px;">
                            <p style="color: #eab308; margin: 0; font-size: 13px;">
                                ⚠️ <strong>No pre-migration data</strong> - Using post-migration analysis only
                            </p>
                        </div>
                    `;
                }

                // Show notice if using post-migration data when both exist (most recent)
                if (data.metrics_source === 'post-migration' && data.has_premigration_data) {
                    metricsGrid.innerHTML = `
                        <div style="grid-column: 1 / -1; padding: 15px; background: rgba(100, 200, 255, 0.1); border-left: 3px solid #64c8ff; border-radius: 8px; margin-bottom: 15px;">
                            <p style="color: #64c8ff; margin: 0; font-size: 13px;">
                                ℹ️ <strong>Showing post-migration analysis</strong> (most recent metrics)
                            </p>
                        </div>
                    `;
                }

                // Build HTML string first, then set it once
                let metricsHTML = metricsGrid.innerHTML;
                Object.keys(metricLabels).forEach(key => {
                    const value = metrics[key] !== null && metrics[key] > 0 ? metrics[key].toFixed(4) : '—';
                    metricsHTML += `
                        <div class="metric">
                            <label>${metricLabels[key]}</label>
                            <span>${value}</span>
                        </div>
                    `;
                });

                // Add coverage metrics
                let preCoverage = '—';
                let postCoverage = '—';

                // Handle both nested object and flat coverage formats
                if (data.coverage !== null && data.coverage !== undefined) {
                    if (typeof data.coverage === 'object') {
                        preCoverage = data.coverage.pre_migration !== null ? (data.coverage.pre_migration).toFixed(1) : '—';
                        postCoverage = data.coverage.post_migration !== null ? (data.coverage.post_migration).toFixed(1) : '—';
                    } else {
                        // Flat coverage value (post-migration only)
                        postCoverage = data.coverage.toFixed(1);
                    }
                }

                metricsHTML += `
                    <div class="metric">
                        <label>Pre-Migration Coverage</label>
                        <span>${preCoverage}%</span>
                    </div>
                    <div class="metric">
                        <label>Post-Migration Coverage</label>
                        <span>${postCoverage}%</span>
                    </div>
                `;

                metricsGrid.innerHTML = metricsHTML;

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
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Try new schema first, then old schema
        try:
            cursor.execute("""
                SELECT
                    mint,
                    total_txs,
                    total_events,
                    mint_concentration,
                    unique_minters_ratio,
                    sell_suppression_ratio,
                    mint_velocity_sec,
                    buy_size_variance,
                    sell_volume_concentration,
                    rug_probability,
                    risk_level,
                    coverage
                FROM token_analysis
                WHERE mint = ?
            """, (token_mint,))
        except sqlite3.OperationalError:
            # Fall back to old schema
            cursor.execute("""
                SELECT
                    mint,
                    0 as total_txs,
                    events_parsed as total_events,
                    mint_concentration,
                    unique_minters_ratio,
                    sell_suppression_ratio,
                    mint_velocity_sec,
                    buy_size_variance,
                    sell_volume_concentration,
                    rug_probability,
                    risk_level,
                    COALESCE(pre_migration_coverage, 0) as coverage
                FROM token_analysis
                WHERE mint = ?
            """, (token_mint,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Token not found'}), 404

        # Format response compatible with UI expectations (pre/post migration comparison)
        response = jsonify({
            'mint': row['mint'],
            'total_txs': row['total_txs'],
            'total_events': row['total_events'],
            'metrics': {
                'mint_concentration': row['mint_concentration'] if row['mint_concentration'] else 0,
                'unique_minters_ratio': row['unique_minters_ratio'] if row['unique_minters_ratio'] else 0,
                'sell_suppression_ratio': row['sell_suppression_ratio'] if row['sell_suppression_ratio'] else 0,
                'mint_velocity_sec': row['mint_velocity_sec'] if row['mint_velocity_sec'] else 0,
                'buy_size_variance': row['buy_size_variance'] if row['buy_size_variance'] else 0,
                'sell_volume_concentration': row['sell_volume_concentration'] if row['sell_volume_concentration'] else 0
            },
            'risk': {
                'rug_probability': row['rug_probability'] if row['rug_probability'] else 0,
                'risk_level': row['risk_level'],
                # For UI compatibility (showing post-migration data)
                'pre_rug_probability': None,
                'pre_risk_level': None,
                'post_rug_probability': row['rug_probability'] if row['rug_probability'] else 0,
                'post_risk_level': row['risk_level'],
                'amm_rug_probability': None,
                'amm_risk_level': None
            },
            # For UI compatibility (flat coverage for post-migration only)
            'coverage': row['coverage'],
            # UI expects nested coverage object for pre/post comparison
            'metrics_source': 'post-migration',
            'has_premigration_data': False
        })
        return response
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =========================================================================
# MAIN
# =========================================================================

if __name__ == '__main__':
    print("[FLASK] Starting Migration Tracker UI...")
    print("[FLASK] Dashboard available at http://localhost:5002")
    print("[FLASK] Database: " + DB_PATH)
    app.run(host='0.0.0.0', port=5002, debug=False)
