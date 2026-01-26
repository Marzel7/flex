#!/usr/bin/env python3
"""
Pump.Fun → PumpSwap Migration Tracking UI

Displays tokens that have migrated from Pump.Fun to PumpSwap with:
- Post-migration risk scores
- Token analysis metrics
- Detection times
- Current live prices
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

        # Query post-migration analysis data
        cursor.execute("""
            SELECT
                mint,
                analyzed_at,
                created_at,
                events_parsed,
                rug_probability,
                risk_level,
                post_migration_coverage,
                price_current,
                price_highest,
                market_cap_current,
                market_cap_highest,
                market_cap_highest_at,
                rug_indicator,
                creator_address,
                creator_reputation,
                token_creator,
                earliest_tx_creator,
                creator_is_blocked,
                network_risk,
                connected_malicious_count
            FROM token_analysis
            ORDER BY analyzed_at DESC
        """)

        tokens = []
        for row in cursor.fetchall():
            tokens.append({
                'mint': row['mint'],
                'analyzed_at': row['analyzed_at'],
                'created_at': row['created_at'],
                'rug_probability': row['rug_probability'] if row['rug_probability'] else 0,
                'risk_level': row['risk_level'],
                'total_txs': 0,  # Not used in new schema
                'total_events': row['events_parsed'] if row['events_parsed'] else 0,
                'coverage': row['post_migration_coverage'] if row['post_migration_coverage'] else 0,
                'price_current': row['price_current'] if row['price_current'] else None,
                'price_highest': row['price_highest'] if row['price_highest'] else None,
                'market_cap_current': row['market_cap_current'] if row['market_cap_current'] else None,
                'market_cap_highest': row['market_cap_highest'] if row['market_cap_highest'] else None,
                'market_cap_highest_at': row['market_cap_highest_at'] if row['market_cap_highest_at'] else None,
                'rug_indicator': row['rug_indicator'],
                'creator_address': row['creator_address'] if row['creator_address'] else None,
                'creator_reputation': row['creator_reputation'] if row['creator_reputation'] else None,
                'token_creator': row['token_creator'] if row['token_creator'] else None,
                'earliest_tx_creator': row['earliest_tx_creator'] if row['earliest_tx_creator'] else None,
                'creator_is_blocked': bool(row['creator_is_blocked']) if row['creator_is_blocked'] else False,
                'network_risk': bool(row['network_risk']) if row['network_risk'] else False,
                'connected_malicious_count': row['connected_malicious_count'] if row['connected_malicious_count'] else 0
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

        .tokens-table th.sortable {
            cursor: pointer;
            user-select: none;
            position: relative;
            transition: background-color 0.2s;
        }

        .tokens-table th.sortable:hover {
            background: rgba(0, 212, 255, 0.1);
        }

        .tokens-table th.sorted-asc::after {
            content: ' ↑';
            font-size: 12px;
            margin-left: 5px;
        }

        .tokens-table th.sorted-desc::after {
            content: ' ↓';
            font-size: 12px;
            margin-left: 5px;
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
            font-size: 10px;
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

        .rug-badge {
            display: inline-block;
            background: rgba(239, 68, 68, 0.25);
            color: #ff6b6b;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            border: 1px solid rgba(239, 68, 68, 0.5);
        }

        .safe-badge {
            display: inline-block;
            background: rgba(34, 197, 94, 0.15);
            color: #22c55e;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }

        .creator-pump_fun_official {
            display: inline-block;
            background: rgba(59, 130, 246, 0.15);
            color: #3b82f6;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            border: 1px solid rgba(59, 130, 246, 0.5);
        }

        .creator-malicious {
            display: inline-block;
            background: rgba(239, 68, 68, 0.25);
            color: #ff6b6b;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            border: 1px solid rgba(239, 68, 68, 0.5);
        }

        .creator-unknown {
            display: inline-block;
            background: rgba(156, 163, 175, 0.15);
            color: #9ca3af;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }

        .creator-blocked {
            display: inline-block;
            background: rgba(239, 68, 68, 0.3);
            color: #dc2626;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            border: 1px solid rgba(239, 68, 68, 0.7);
            animation: pulse 2s infinite;
        }

        .network-risk {
            display: inline-block;
            background: rgba(249, 115, 22, 0.3);
            color: #ea580c;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            border: 1px solid rgba(249, 115, 22, 0.7);
            margin-right: 4px;
        }

        @keyframes pulse {
            0%, 100% {
                opacity: 1;
            }
            50% {
                opacity: 0.8;
            }
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
                console.log('Loaded tokens:', data.tokens?.length || 0, 'tokens');

                if (!data.tokens || data.tokens.length === 0) {
                    document.getElementById('tokens-container').innerHTML =
                        '<div class="no-data">No migrations recorded yet. Monitoring Pump.Fun...</div>';
                    return;
                }

                // Store tokens for sorting
                window.currentTokens = data.tokens;

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

        let sortConfig = {
            column: 'market_cap_highest',
            direction: 'desc'
        };

        function buildTable(tokens) {
            // Sort tokens based on current sort config
            const sortedTokens = [...tokens].sort((a, b) => {
                const aVal = a[sortConfig.column];
                const bVal = b[sortConfig.column];

                // Handle null/undefined values
                if (aVal == null && bVal == null) return 0;
                if (aVal == null) return sortConfig.direction === 'asc' ? 1 : -1;
                if (bVal == null) return sortConfig.direction === 'asc' ? -1 : 1;

                // Numeric comparison
                if (typeof aVal === 'number' && typeof bVal === 'number') {
                    return sortConfig.direction === 'asc' ? aVal - bVal : bVal - aVal;
                }

                // String comparison
                return sortConfig.direction === 'asc'
                    ? String(aVal).localeCompare(String(bVal))
                    : String(bVal).localeCompare(String(aVal));
            });

            const html = `
                <table class="tokens-table">
                    <thead>
                        <tr>
                            <th onclick="sortBy('mint')" class="sortable ${sortConfig.column === 'mint' ? 'sorted-' + sortConfig.direction : ''}">Token Mint</th>
                            <th onclick="sortBy('rug_indicator')" class="sortable ${sortConfig.column === 'rug_indicator' ? 'sorted-' + sortConfig.direction : ''}">Rug Flag</th>
                            <th onclick="sortBy('risk_level')" class="sortable ${sortConfig.column === 'risk_level' ? 'sorted-' + sortConfig.direction : ''}">Risk Level</th>
                            <th onclick="sortBy('rug_probability')" class="sortable ${sortConfig.column === 'rug_probability' ? 'sorted-' + sortConfig.direction : ''}">Risk Score</th>
                            <th onclick="sortBy('market_cap_current')" class="sortable ${sortConfig.column === 'market_cap_current' ? 'sorted-' + sortConfig.direction : ''}">Market Cap</th>
                            <th onclick="sortBy('market_cap_highest')" class="sortable ${sortConfig.column === 'market_cap_highest' ? 'sorted-' + sortConfig.direction : ''}">Peak MC</th>
                            <th onclick="sortBy('market_cap_highest_at')" class="sortable ${sortConfig.column === 'market_cap_highest_at' ? 'sorted-' + sortConfig.direction : ''}">Peak Timing</th>
                            <th onclick="sortBy('total_events')" class="sortable ${sortConfig.column === 'total_events' ? 'sorted-' + sortConfig.direction : ''}">Events</th>
                            <th onclick="sortBy('coverage')" class="sortable ${sortConfig.column === 'coverage' ? 'sorted-' + sortConfig.direction : ''}">Coverage</th>
                            <th onclick="sortBy('analyzed_at')" class="sortable ${sortConfig.column === 'analyzed_at' ? 'sorted-' + sortConfig.direction : ''}">Analyzed</th>
                            <th onclick="sortBy('creator_reputation')" class="sortable ${sortConfig.column === 'creator_reputation' ? 'sorted-' + sortConfig.direction : ''}">Creator</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${sortedTokens.map(token => `
                            <tr>
                                <td class="mint"><a href="#" onclick="showTokenMetrics('${token.mint}'); return false;" class="mint-link" title="Click for metrics">${token.mint}</a></td>
                                <td>
                                    ${token.rug_indicator === 'quick_peak_low_mc' ? '<span class="rug-badge">🚨 RUG</span>' : '<span class="safe-badge">✓ Safe</span>'}
                                </td>
                                <td>
                                    <span class="risk-score ${getRiskClass(token.risk_level)}">${token.risk_level}</span>
                                </td>
                                <td>
                                    ${token.rug_probability !== null && token.rug_probability !== undefined ? (token.rug_probability * 100).toFixed(1) + '%' : '—'}
                                </td>
                                <td>
                                    ${token.market_cap_current ? '$' + formatMarketCap(token.market_cap_current) : '—'}
                                </td>
                                <td>
                                    ${token.market_cap_highest ? '$' + formatMarketCap(token.market_cap_highest) : '—'}
                                </td>
                                <td>
                                    ${token.market_cap_highest_at ? getTimeToPeak(token.created_at, token.market_cap_highest_at) : '—'}
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
                                <td>
                                    ${token.network_risk ? '<span class="network-risk" title="🔗 Connected to ' + token.connected_malicious_count + ' malicious creator(s)">🔗 NETWORK (' + token.connected_malicious_count + ')</span> ' : ''}${token.creator_is_blocked ? '<span class="creator-blocked" title="⚠️ Known malicious actor">🚨 BLOCKED</span> ' : ''}${token.creator_reputation ? '<span class="creator-' + token.creator_reputation.toLowerCase() + '" title="Creator: ' + (token.token_creator || 'unknown') + '">' + token.creator_reputation + '</span>' : ''}
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

        function sortBy(column) {
            // If clicking same column, toggle direction
            if (sortConfig.column === column) {
                sortConfig.direction = sortConfig.direction === 'asc' ? 'desc' : 'asc';
            } else {
                sortConfig.column = column;
                sortConfig.direction = 'desc';  // Default to descending for new column
            }
            // Rebuild table with current tokens
            const tokens = window.currentTokens || [];
            buildTable(tokens);
        }

        function getRiskClass(riskLevel) {
            if (!riskLevel) return 'risk-medium';
            if (riskLevel.includes('HIGH')) return 'risk-high';
            if (riskLevel.includes('LOW')) return 'risk-low';
            return 'risk-medium';
        }

        function formatDate(timestamp) {
            if (!timestamp) return '-';
            const date = new Date(timestamp * 1000);
            return date.toLocaleString('en-US', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
        }

        function formatTime(seconds) {
            if (!seconds) return '-';
            const minutes = Math.floor(seconds / 60);
            const secs = seconds % 60;
            return `${minutes}m ${secs}s`;
        }

        function formatMarketCap(value) {
            if (!value) return '-';
            if (value >= 1000000) {
                return (value / 1000000).toFixed(1) + 'M';
            } else if (value >= 1000) {
                return (value / 1000).toFixed(1) + 'K';
            }
            return value.toFixed(0);
        }

        function getTimeToPeak(migrationTime, peakTime) {
            if (!migrationTime || !peakTime) return '—';
            try {
                const migration = new Date(migrationTime);
                const peak = new Date(peakTime);
                const diffSeconds = (peak - migration) / 1000;

                if (diffSeconds < 0) return '—';
                if (diffSeconds < 60) return `${Math.floor(diffSeconds)}s`;
                if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m`;
                const hours = diffSeconds / 3600;
                if (hours < 24) return `${hours.toFixed(1)}h`;
                return `${(hours / 24).toFixed(1)}d`;
            } catch (e) {
                return '—';
            }
        }

        // Abort controller for price loading - allows canceling requests when modal opens
        let priceLoadController = new AbortController();

        // Load tokens immediately and then every 10 seconds
        loadTokens();
        setInterval(loadTokens, 10000);

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

                // Build HTML string first, then set it once
                let metricsHTML = metricsGrid.innerHTML;
                Object.keys(metricLabels).forEach(key => {
                    const value = metrics[key] !== null && metrics[key] !== undefined ? metrics[key].toFixed(4) : '—';
                    metricsHTML += `
                        <div class="metric">
                            <label>${metricLabels[key]}</label>
                            <span>${value}</span>
                        </div>
                    `;
                });

                // Add coverage metric
                let coverage = '—';
                if (data.coverage !== null && data.coverage !== undefined) {
                    coverage = data.coverage.toFixed(1);
                }

                metricsHTML += `
                    <div class="metric">
                        <label>Analysis Coverage</label>
                        <span>${coverage}%</span>
                    </div>
                `;

                // Add market cap metrics
                let marketCapCurrent = '—';
                let marketCapHighest = '—';
                if (data.market_cap && data.market_cap.current) {
                    marketCapCurrent = '$' + formatMarketCap(data.market_cap.current);
                }
                if (data.market_cap && data.market_cap.highest) {
                    marketCapHighest = '$' + formatMarketCap(data.market_cap.highest);
                }

                metricsHTML += `
                    <div class="metric">
                        <label>Market Cap</label>
                        <span>${marketCapCurrent}</span>
                    </div>
                    <div class="metric">
                        <label>Peak MC</label>
                        <span>${marketCapHighest}</span>
                    </div>
                `;

                metricsGrid.innerHTML = metricsHTML;

                // Populate risk section
                const riskSection = document.getElementById('riskSection');
                const risk = data.risk;
                const rugProbability = (risk.rug_probability * 100).toFixed(1);

                riskSection.innerHTML = `
                    <p>
                        <label>Rug Probability:</label>
                        <span class="risk-value">${rugProbability}%</span>
                        <span style="color: #a0a0a0; margin-left: 10px;">${risk.risk_level || '—'}</span>
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

        # Query post-migration analysis data
        cursor.execute("""
            SELECT
                mint,
                events_parsed as total_events,
                post_migration_mint_concentration,
                post_migration_unique_minters_ratio,
                post_migration_sell_suppression_ratio,
                post_migration_mint_velocity_sec,
                post_migration_buy_size_variance,
                post_migration_sell_volume_concentration,
                post_migration_creator_activity_ratio,
                rug_probability,
                risk_level,
                post_migration_coverage as coverage,
                price_current,
                price_highest,
                market_cap_current,
                market_cap_highest
            FROM token_analysis
            WHERE mint = ?
        """, (token_mint,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Token not found'}), 404

        # Format response for post-migration analysis only
        response = jsonify({
            'mint': row['mint'],
            'total_txs': 0,
            'total_events': row['total_events'] if row['total_events'] else 0,
            'metrics': {
                'mint_concentration': row['post_migration_mint_concentration'] if row['post_migration_mint_concentration'] else 0,
                'unique_minters_ratio': row['post_migration_unique_minters_ratio'] if row['post_migration_unique_minters_ratio'] else 0,
                'sell_suppression_ratio': row['post_migration_sell_suppression_ratio'] if row['post_migration_sell_suppression_ratio'] else 0,
                'mint_velocity_sec': row['post_migration_mint_velocity_sec'] if row['post_migration_mint_velocity_sec'] else 0,
                'buy_size_variance': row['post_migration_buy_size_variance'] if row['post_migration_buy_size_variance'] else 0,
                'sell_volume_concentration': row['post_migration_sell_volume_concentration'] if row['post_migration_sell_volume_concentration'] else 0,
                'creator_activity_ratio': row['post_migration_creator_activity_ratio'] if row['post_migration_creator_activity_ratio'] else 0
            },
            'risk': {
                'rug_probability': row['rug_probability'] if row['rug_probability'] else 0,
                'risk_level': row['risk_level']
            },
            'price': {
                'current': row['price_current'] if row['price_current'] else 0,
                'highest': row['price_highest'] if row['price_highest'] else 0
            },
            'market_cap': {
                'current': row['market_cap_current'] if row['market_cap_current'] else 0,
                'highest': row['market_cap_highest'] if row['market_cap_highest'] else 0
            },
            'coverage': row['coverage'] if row['coverage'] else 0
        })
        return response
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-cluster/<creator_address>')
def api_creator_cluster(creator_address: str):
    """Get wallet cluster data for a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get cluster size and hop breakdown
        cursor.execute("""
            SELECT
                COUNT(*) as total_wallets,
                SUM(CASE WHEN hop = 0 THEN 1 ELSE 0 END) as hop0_count,
                SUM(CASE WHEN hop = 1 THEN 1 ELSE 0 END) as hop1_count,
                SUM(CASE WHEN hop = 2 THEN 1 ELSE 0 END) as hop2_count,
                AVG(confidence) as avg_confidence
            FROM wallet_cluster_nodes
            WHERE root_creator = ?
        """, (creator_address,))

        cluster_row = cursor.fetchone()

        # Get token count for this creator
        cursor.execute("""
            SELECT COUNT(*) as token_count
            FROM token_analysis
            WHERE earliest_tx_creator = ?
        """, (creator_address,))

        token_row = cursor.fetchone()
        conn.close()

        return jsonify({
            'creator': creator_address,
            'cluster_size': cluster_row['total_wallets'] if cluster_row and cluster_row['total_wallets'] else 0,
            'hop0': cluster_row['hop0_count'] if cluster_row and cluster_row['hop0_count'] else 0,
            'hop1': cluster_row['hop1_count'] if cluster_row and cluster_row['hop1_count'] else 0,
            'hop2': cluster_row['hop2_count'] if cluster_row and cluster_row['hop2_count'] else 0,
            'token_count': token_row['token_count'] if token_row else 0,
            'avg_confidence': round(cluster_row['avg_confidence'], 2) if cluster_row and cluster_row['avg_confidence'] else 0
        })
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
