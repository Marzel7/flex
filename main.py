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
from flask import Flask, jsonify, render_template_string, request
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
                'creator': row['earliest_tx_creator'] if row['earliest_tx_creator'] else None,
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

        /* Mint cell with embedded creator */
        .mint-with-creator {
            display: flex;
            flex-direction: column;
            gap: 4px;
            align-items: flex-start;
        }

        /* Creator address embedded under mint */
        .creator-address-embedded {
            font-family: 'Courier New', monospace;
            font-size: 10px;
            color: #6b7280;
            word-break: break-all;
            max-width: 250px;
            line-height: 1.3;
        }

        /* Creator tags container */
        .creator-tags {
            display: flex;
            gap: 5px;
            flex-wrap: wrap;
            max-width: 320px;
        }

        /* Base creator tag styling */
        .creator-tag {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: 600;
            white-space: nowrap;
        }

        /* Network size tag (purple) */
        .tag-network {
            background: rgba(139, 92, 246, 0.2);
            color: #a78bfa;
            border: 1px solid rgba(139, 92, 246, 0.3);
        }

        /* Funding tag (blue) */
        .tag-funding {
            background: rgba(59, 130, 246, 0.2);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.3);
        }

        /* Repeat launcher tag (orange) */
        .tag-repeat {
            background: rgba(249, 115, 22, 0.2);
            color: #fb923c;
            border: 1px solid rgba(249, 115, 22, 0.3);
        }

        /* Blocked tag (red) */
        .tag-blocked {
            background: rgba(239, 68, 68, 0.2);
            color: #ef4444;
            border: 1px solid rgba(239, 68, 68, 0.3);
            font-weight: 700;
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

        /* Creator stats grid */
        .creator-stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        .stat-box {
            background: rgba(0, 0, 0, 0.3);
            padding: 15px;
            border-radius: 8px;
            border-left: 3px solid #00d4ff;
            text-align: center;
        }

        .stat-box label {
            display: block;
            color: #a0a0a0;
            font-size: 12px;
            margin-bottom: 8px;
            text-transform: uppercase;
        }

        .stat-box span {
            display: block;
            color: #00d4ff;
            font-size: 18px;
            font-weight: bold;
        }

        /* Tokens launched table */
        .tokens-launched-container {
            max-height: 300px;
            overflow-y: auto;
            margin-bottom: 20px;
        }

        .tokens-launched-table {
            width: 100%;
            border-collapse: collapse;
        }

        .tokens-launched-table th {
            background: rgba(0, 0, 0, 0.3);
            padding: 10px;
            text-align: left;
            font-size: 12px;
            color: #a0a0a0;
            border-bottom: 1px solid rgba(0, 212, 255, 0.2);
        }

        .tokens-launched-table td {
            padding: 10px;
            font-size: 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        .tokens-launched-table tr:hover {
            background: rgba(0, 212, 255, 0.05);
        }

        /* Top funders table */
        .top-funders-container {
            max-height: 200px;
            overflow-y: auto;
            margin-bottom: 20px;
        }

        .top-funders-table {
            width: 100%;
            border-collapse: collapse;
        }

        .top-funders-table th {
            background: rgba(0, 0, 0, 0.3);
            padding: 10px;
            text-align: left;
            font-size: 12px;
            color: #a0a0a0;
            border-bottom: 1px solid rgba(0, 212, 255, 0.2);
        }

        .top-funders-table td {
            padding: 10px;
            font-size: 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        /* Cluster info */
        .cluster-info {
            background: rgba(0, 0, 0, 0.3);
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
        }

        .cluster-info p {
            margin: 5px 0;
            color: #e0e0e0;
            font-size: 14px;
        }

        /* CEX badge */
        .cex-badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 3px;
            background: rgba(34, 197, 94, 0.2);
            color: #4ade80;
            font-size: 10px;
            font-weight: 600;
            margin-left: 5px;
        }

        /* CREATE tx link */
        .create-tx-link {
            color: #00d4ff;
            text-decoration: none;
            font-family: monospace;
        }

        .create-tx-link:hover {
            text-decoration: underline;
        }

        .controls-panel {
            background: rgba(0, 20, 40, 0.8);
            border: 1px solid rgba(0, 212, 255, 0.3);
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
            display: flex;
            gap: 30px;
            align-items: center;
            flex-wrap: wrap;
        }

        .control-group {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .control-label {
            color: #a0a0a0;
            font-size: 14px;
            font-weight: 500;
        }

        .toggle-switch {
            position: relative;
            display: inline-block;
            width: 50px;
            height: 24px;
            background-color: #404050;
            border-radius: 12px;
            cursor: pointer;
            transition: background-color 0.3s;
            border: 1px solid rgba(0, 212, 255, 0.2);
        }

        .toggle-switch.active {
            background-color: #00d4ff;
        }

        .toggle-slider {
            position: absolute;
            top: 2px;
            left: 2px;
            width: 20px;
            height: 20px;
            background-color: white;
            border-radius: 50%;
            transition: left 0.3s;
        }

        .toggle-switch.active .toggle-slider {
            left: 28px;
        }

        .status-indicator {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: #ff4444;
            margin-left: 8px;
        }

        .status-indicator.active {
            background-color: #00ff00;
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
            <div class="stat-card">
                <div class="stat-label">Unique Creators</div>
                <div class="stat-value" id="unique-creators">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Repeat Launchers</div>
                <div class="stat-value" id="repeat-launchers">0</div>
            </div>
        </div>

        <div class="controls-panel">
            <div class="control-group">
                <span class="control-label">Token History Check</span>
                <div class="toggle-switch active" id="tokenHistoryToggle" onclick="toggleTokenHistory()">
                    <div class="toggle-slider"></div>
                </div>
                <span class="status-indicator active" id="tokenHistoryStatus"></span>
            </div>
            <div class="control-group">
                <span class="control-label">Creator Analysis</span>
                <div class="toggle-switch active" id="clusteringToggle" onclick="toggleClustering()">
                    <div class="toggle-slider"></div>
                </div>
                <span class="status-indicator active" id="clusteringStatus"></span>
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

    <!-- Creator Details Modal -->
    <div id="creatorModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeCreatorDetails()">&times;</span>
            <h2>Creator Details - <span id="modalCreator" style="font-family: monospace; font-size: 14px;"></span></h2>

            <!-- Creator Stats Summary -->
            <div class="creator-stats-grid">
                <div class="stat-box">
                    <label>Total Tokens</label>
                    <span id="creatorTotalTokens">—</span>
                </div>
                <div class="stat-box">
                    <label>Funding Received</label>
                    <span id="creatorTotalFunding">—</span>
                </div>
                <div class="stat-box">
                    <label>Funders</label>
                    <span id="creatorTotalFunders">—</span>
                </div>
                <div class="stat-box">
                    <label>Network Size</label>
                    <span id="creatorNetworkSize">—</span>
                </div>
                <div class="stat-box">
                    <label>Status</label>
                    <span id="creatorBlocklistStatus">—</span>
                </div>
            </div>

            <!-- Tokens Launched -->
            <h3>Tokens Launched</h3>
            <div class="tokens-launched-container">
                <table class="tokens-launched-table">
                    <thead>
                        <tr>
                            <th>Token Mint</th>
                            <th>Created</th>
                            <th>Risk</th>
                            <th>Market Cap</th>
                            <th>CREATE Tx</th>
                        </tr>
                    </thead>
                    <tbody id="tokensLaunchedBody">
                        <!-- Populated by JavaScript -->
                    </tbody>
                </table>
            </div>

            <!-- Top Funders -->
            <h3>Top Funders</h3>
            <div class="top-funders-container">
                <table class="top-funders-table">
                    <thead>
                        <tr>
                            <th>Funder Address</th>
                            <th>Amount (SOL)</th>
                            <th>Type</th>
                        </tr>
                    </thead>
                    <tbody id="topFundersBody">
                        <!-- Populated by JavaScript -->
                    </tbody>
                </table>
            </div>

            <!-- Wallet Cluster -->
            <h3>Wallet Network</h3>
            <div class="cluster-info" id="clusterInfo">
                <!-- Populated by JavaScript -->
            </div>
        </div>
    </div>

    <!-- Transaction Viewer Modal -->
    <div id="txViewerModal" class="modal">
        <div class="modal-content" style="max-width: 900px; max-height: 90vh; overflow-y: auto;">
            <span class="close" onclick="closeTxViewer()">&times;</span>
            <h2>Transaction Details - <span id="txViewerSig" style="font-family: monospace; font-size: 12px;"></span></h2>

            <div style="margin-bottom: 20px;">
                <a id="txSolscanLink" href="#" target="_blank" style="color: #00d4ff; text-decoration: none; margin-right: 15px;">
                    🔗 View on Solscan
                </a>
                <button onclick="copyToClipboard(document.getElementById('txViewerSig').textContent)" style="background: rgba(0, 212, 255, 0.2); color: #00d4ff; border: 1px solid #00d4ff; padding: 5px 12px; border-radius: 4px; cursor: pointer; font-family: monospace; font-size: 12px;">
                    📋 Copy Signature
                </button>
            </div>

            <h3>Account Keys (jsonParsed)</h3>
            <div style="background: rgba(0, 0, 0, 0.5); border: 1px solid rgba(0, 212, 255, 0.2); border-radius: 6px; padding: 15px; overflow-x: auto;">
                <pre id="txViewerAccountKeys" style="color: #e0e0e0; font-size: 11px; margin: 0; white-space: pre-wrap; word-wrap: break-word;"></pre>
            </div>

            <h3 style="margin-top: 20px;">Fee Payer (Creator)</h3>
            <div style="background: rgba(34, 197, 94, 0.1); border: 2px solid rgba(34, 197, 94, 0.3); border-radius: 6px; padding: 15px; margin-bottom: 20px;">
                <div style="font-family: monospace; font-size: 12px; color: #4ade80; word-break: break-all;">
                    <span id="txViewerFeePayer">—</span>
                </div>
                <div style="color: #a0a0a0; font-size: 11px; margin-top: 8px;">
                    ✓ Fee payer (always first signer at accountKeys[0]) = transaction creator
                </div>
            </div>
        </div>
    </div>

    <script>
        async function loadTokens() {
            try {
                const response = await fetch('/api/migrated-tokens');
                const data = await response.json();
                console.log('Loaded tokens:', data.tokens?.length || 0, 'tokens');
                console.log('Token data keys:', Object.keys(data));
                console.log('First token:', data.tokens?.[0]);

                if (!data.tokens || data.tokens.length === 0) {
                    console.error('No tokens in response!');
                    document.getElementById('tokens-container').innerHTML =
                        '<div class="no-data">No migrations recorded yet. Monitoring Pump.Fun...</div>';
                    return;
                }

                // Extract unique creator addresses
                const creators = [...new Set(data.tokens.map(t => t.creator).filter(c => c))];

                // Fetch all creator data in one batch call
                let creatorData = {};
                if (creators.length > 0) {
                    try {
                        const creatorResp = await fetch('/api/creators-batch', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({"creators": creators})
                        });
                        creatorData = await creatorResp.json();
                    } catch (e) {
                        console.error('Error loading creator data:', e);
                    }
                }

                // Enrich tokens with creator data
                const enrichedTokens = data.tokens.map(token => ({
                    ...token,
                    creatorData: creatorData[token.creator] || {}
                }));

                // Store tokens for sorting
                window.currentTokens = enrichedTokens;

                // Update stats
                updateStats({tokens: enrichedTokens});

                // Build table
                buildTable(enrichedTokens);
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

            // Calculate unique creators
            const uniqueCreators = new Set(tokens.map(t => t.creator).filter(c => c)).size;

            // Calculate repeat launchers
            const creatorTokenCounts = {};
            tokens.forEach(token => {
                if (token.creator) {
                    creatorTokenCounts[token.creator] = (creatorTokenCounts[token.creator] || 0) + 1;
                }
            });
            const repeatLaunchers = Object.values(creatorTokenCounts).filter(count => count > 1).length;

            document.getElementById('total-migrations').textContent = tokens.length;
            document.getElementById('with-analysis').textContent = tokens.length;
            document.getElementById('high-risk').textContent = highRisk;
            document.getElementById('low-risk').textContent = lowRisk;
            document.getElementById('unique-creators').textContent = uniqueCreators;
            document.getElementById('repeat-launchers').textContent = repeatLaunchers;
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
                            <th>Creator Tags</th>
                            <th onclick="sortBy('rug_indicator')" class="sortable ${sortConfig.column === 'rug_indicator' ? 'sorted-' + sortConfig.direction : ''}">Rug Flag</th>
                            <th onclick="sortBy('risk_level')" class="sortable ${sortConfig.column === 'risk_level' ? 'sorted-' + sortConfig.direction : ''}">Risk Level</th>
                            <th onclick="sortBy('rug_probability')" class="sortable ${sortConfig.column === 'rug_probability' ? 'sorted-' + sortConfig.direction : ''}">Risk Score</th>
                            <th onclick="sortBy('market_cap_current')" class="sortable ${sortConfig.column === 'market_cap_current' ? 'sorted-' + sortConfig.direction : ''}">Market Cap</th>
                            <th onclick="sortBy('market_cap_highest')" class="sortable ${sortConfig.column === 'market_cap_highest' ? 'sorted-' + sortConfig.direction : ''}">Peak MC</th>
                            <th onclick="sortBy('market_cap_highest_at')" class="sortable ${sortConfig.column === 'market_cap_highest_at' ? 'sorted-' + sortConfig.direction : ''}">Peak Timing</th>
                            <th onclick="sortBy('total_events')" class="sortable ${sortConfig.column === 'total_events' ? 'sorted-' + sortConfig.direction : ''}">Events</th>
                            <th onclick="sortBy('coverage')" class="sortable ${sortConfig.column === 'coverage' ? 'sorted-' + sortConfig.direction : ''}">Coverage</th>
                            <th onclick="sortBy('analyzed_at')" class="sortable ${sortConfig.column === 'analyzed_at' ? 'sorted-' + sortConfig.direction : ''}">Analyzed</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${sortedTokens.map(token => {
                            const creatorData = token.creatorData || {};
                            const tags = [];

                            // Network size tag (show if > 10 wallets)
                            if (creatorData.network_size > 10) {
                                const hop0 = creatorData.cluster_hops?.hop0 || 0;
                                const hop1 = creatorData.cluster_hops?.hop1 || 0;
                                tags.push(`<span class="creator-tag tag-network" title="Wallet cluster: ${hop0} hop-0, ${hop1} hop-1">${creatorData.network_size} wallets</span>`);
                            }

                            // Funding tag (show if > 10 SOL)
                            if (creatorData.inbound_sol > 10) {
                                const sources = creatorData.inbound_sources || 0;
                                tags.push(`<span class="creator-tag tag-funding" title="Pre-launch funding">${creatorData.inbound_sol.toFixed(1)} SOL from ${sources} source${sources > 1 ? 's' : ''}</span>`);
                            }

                            // Repeat launcher tag (show if > 1 token)
                            if (creatorData.token_count > 1) {
                                tags.push(`<span class="creator-tag tag-repeat" title="Repeat launcher">${creatorData.token_count} tokens</span>`);
                            }

                            // Blocked tag
                            if (creatorData.is_blocked || token.creator_is_blocked) {
                                tags.push('<span class="creator-tag tag-blocked" title="On blocklist">BLOCKED</span>');
                            }

                            const creatorShort = token.creator ? token.creator.substring(0, 8) + '...' : 'N/A';
                            const creatorTitle = token.creator || 'Unknown';
                            const creatorElement = token.creator
                                ? `<a href="#" onclick="showCreatorDetails('${token.creator}'); return false;" class="mint-link creator-address-link" title="Click for creator details">${creatorTitle}</a>`
                                : '<span style="color: #a0a0a0;">Unknown</span>';

                            return `
                                <tr>
                                    <td class="mint-with-creator">
                                        <a href="#" onclick="showTokenMetrics('${token.mint}'); return false;" class="mint-link" title="Click for metrics">${token.mint}</a>
                                        <div class="creator-address-embedded">${creatorElement}</div>
                                    </td>
                                    <td class="creator-tags">${tags.join(' ')}</td>
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
                                </tr>
                            `;
                        }).join('')}
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

        // Migration feature toggles
        let tokenHistoryEnabled = true;
        let clusteringEnabled = true;

        function toggleTokenHistory() {
            console.clear();
            tokenHistoryEnabled = !tokenHistoryEnabled;
            const toggle = document.getElementById('tokenHistoryToggle');
            const status = document.getElementById('tokenHistoryStatus');
            toggle.classList.toggle('active');
            status.classList.toggle('active');

            const state = tokenHistoryEnabled ? 'ENABLED' : 'DISABLED';
            console.log('🔧 [TOGGLE] Token History Check: ' + state);
            console.log('State: ' + state + ' | Value: ' + tokenHistoryEnabled);

            // Send to backend to enable/disable
            fetch('/api/migration-settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    token_history_check: tokenHistoryEnabled,
                    creator_history_check: clusteringEnabled
                })
            }).then(resp => resp.json()).then(data => {
                console.log('✅ [SETTINGS] Updated - Token History: ' + state);
                console.log('Response:', data);
            }).catch(e => console.error('❌ Error updating settings:', e));
        }

        function toggleClustering() {
            console.clear();
            clusteringEnabled = !clusteringEnabled;
            const toggle = document.getElementById('clusteringToggle');
            const status = document.getElementById('clusteringStatus');
            toggle.classList.toggle('active');
            status.classList.toggle('active');

            const state = clusteringEnabled ? 'ENABLED' : 'DISABLED';
            console.log('🔧 [TOGGLE] Creator Analysis: ' + state);
            console.log('State: ' + state + ' | Value: ' + clusteringEnabled);

            // Send to backend to enable/disable
            fetch('/api/migration-settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    token_history_check: tokenHistoryEnabled,
                    creator_history_check: clusteringEnabled
                })
            }).then(resp => resp.json()).then(data => {
                console.log('✅ [SETTINGS] Updated - Creator Analysis: ' + state);
                console.log('Response:', data);
            }).catch(e => console.error('❌ Error updating settings:', e));
        }

        // Initialize settings from backend on page load
        async function initializeSettings() {
            try {
                const resp = await fetch('/api/migration-settings');
                const settings = await resp.json();

                tokenHistoryEnabled = settings.token_history_check;
                clusteringEnabled = settings.creator_history_check;

                // Update toggle switch states
                const tokenHistoryToggle = document.getElementById('tokenHistoryToggle');
                const tokenHistoryStatus = document.getElementById('tokenHistoryStatus');
                const clusteringToggle = document.getElementById('clusteringToggle');
                const clusteringStatus = document.getElementById('clusteringStatus');

                if (!tokenHistoryEnabled) {
                    tokenHistoryToggle.classList.remove('active');
                    tokenHistoryStatus.classList.remove('active');
                }
                if (!clusteringEnabled) {
                    clusteringToggle.classList.remove('active');
                    clusteringStatus.classList.remove('active');
                }

                const historyState = tokenHistoryEnabled ? '✅ ON' : '❌ OFF';
                const analysisState = clusteringEnabled ? '✅ ON' : '❌ OFF';
                console.log('📋 [SETTINGS LOADED] Token History: ' + historyState + ' | Creator Analysis: ' + analysisState);
                console.log('Full settings:', settings);
            } catch (e) {
                console.error('❌ Error loading settings:', e);
            }
        }

        // Load tokens immediately and then every 10 seconds
        initializeSettings();
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

        async function showCreatorDetails(creatorAddress) {
            // Cancel ongoing price fetches to free up bandwidth for modal
            priceLoadController.abort();
            priceLoadController = new AbortController();

            const modal = document.getElementById('creatorModal');
            document.getElementById('modalCreator').textContent = creatorAddress;

            try {
                const response = await fetch(`/api/creator-details/${creatorAddress}`);
                const data = await response.json();

                if (data.error) {
                    alert('Creator details not found');
                    return;
                }

                // Populate creator stats
                document.getElementById('creatorTotalTokens').textContent = data.tokens.length;
                document.getElementById('creatorTotalFunding').textContent = (data.funding.total_sol !== null ? data.funding.total_sol.toFixed(2) : '0.00') + ' SOL';
                document.getElementById('creatorTotalFunders').textContent = data.funding.total_funders || '0';
                document.getElementById('creatorNetworkSize').textContent = (data.cluster.total_wallets || 0) + ' wallets';
                document.getElementById('creatorBlocklistStatus').textContent = data.is_blocked ? '🚫 BLOCKED' : '✅ Clean';
                document.getElementById('creatorBlocklistStatus').style.color = data.is_blocked ? '#ef4444' : '#4ade80';

                // Populate tokens launched table
                const tokensBody = document.getElementById('tokensLaunchedBody');
                if (data.tokens.length > 0) {
                    tokensBody.innerHTML = data.tokens.map(token => {
                        const createTxShort = token.create_tx_signature ? token.create_tx_signature.substring(0, 16) + '...' : 'N/A';
                        const createTxCell = token.create_tx_signature
                            ? `<a href="https://solscan.io/tx/${token.create_tx_signature}" target="_blank" class="create-tx-link" title="${token.create_tx_signature}">${createTxShort}</a>
                                <div style="display: inline-block; margin-left: 8px;">
                                    <button onclick="viewTransaction('${token.create_tx_signature}')" style="background: rgba(0, 212, 255, 0.2); color: #00d4ff; border: 1px solid #00d4ff; padding: 3px 10px; border-radius: 3px; cursor: pointer; font-size: 11px; font-family: monospace;">View Raw</button>
                                </div>`
                            : 'N/A';

                        return `
                            <tr>
                                <td><a href="#" onclick="showTokenMetrics('${token.mint}'); return false;" class="mint-link" title="${token.mint}">${token.mint.substring(0, 16)}...</a></td>
                                <td>${formatDate(token.created_at)}</td>
                                <td><span class="risk-score risk-${token.risk_level ? token.risk_level.toLowerCase() : 'medium'}">${token.risk_level || 'N/A'}</span></td>
                                <td>${formatMarketCap(token.market_cap_current)}</td>
                                <td>${createTxCell}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    tokensBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #a0a0a0;">No tokens launched yet</td></tr>';
                }

                // Populate top funders table
                const fundersBody = document.getElementById('topFundersBody');
                if (data.top_funders && data.top_funders.length > 0) {
                    fundersBody.innerHTML = data.top_funders.map(funder => {
                        const cexBadge = funder.is_cex ? `<span class="cex-badge">${funder.cex_exchange} ${funder.cex_type}</span>` : '';
                        return `
                            <tr>
                                <td title="${funder.funder_address}">${funder.funder_address.substring(0, 16)}...${cexBadge}</td>
                                <td>${funder.amount_sol.toFixed(2)} SOL</td>
                                <td>${funder.is_cex ? 'CEX' : 'Wallet'}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    fundersBody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: #a0a0a0;">No funding data available</td></tr>';
                }

                // Populate cluster info
                const clusterInfo = document.getElementById('clusterInfo');
                if (data.cluster.total_wallets > 0) {
                    clusterInfo.innerHTML = `
                        <p><strong>Total Network Wallets:</strong> ${data.cluster.total_wallets}</p>
                        <p><strong>Direct Connections (Hop 0):</strong> ${data.cluster.hop0}</p>
                        <p><strong>Secondary Connections (Hop 1):</strong> ${data.cluster.hop1}</p>
                        <p><strong>Tertiary Connections (Hop 2):</strong> ${data.cluster.hop2}</p>
                    `;
                } else {
                    clusterInfo.innerHTML = '<p style="color: #a0a0a0;">No wallet network data available</p>';
                }

                // Show modal
                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading creator details:', error);
                alert('Failed to load creator details');
            }
        }

        async function viewTransaction(signature) {
            // Validate signature
            if (!signature) {
                alert('No transaction signature provided');
                return;
            }

            // Show loading state
            document.getElementById('txViewerModal').style.display = 'block';
            document.getElementById('txViewerAccountKeys').textContent = 'Loading transaction...';
            document.getElementById('txViewerFeePayer').textContent = 'Fetching...';

            // Fetch transaction details with jsonParsed encoding
            const payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            };

            try {
                const response = await fetch('https://api.mainnet-beta.solana.com', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload),
                    timeout: 30000
                });
                const data = await response.json();

                // Check for RPC errors
                if (data.error) {
                    document.getElementById('txViewerAccountKeys').textContent = `Error: ${data.error.message || 'Unknown error'}`;
                    return;
                }

                if (!data.result) {
                    document.getElementById('txViewerAccountKeys').textContent = 'Transaction not found on blockchain';
                    return;
                }

                const tx = data.result;

                // Safely access nested properties
                if (!tx.transaction || !tx.transaction.message) {
                    document.getElementById('txViewerAccountKeys').textContent = 'Invalid transaction format';
                    return;
                }

                const message = tx.transaction.message;
                const accountKeys = message.accountKeys || [];

                // Display transaction details
                document.getElementById('txViewerSig').textContent = signature;
                document.getElementById('txSolscanLink').href = `https://solscan.io/tx/${signature}`;
                document.getElementById('txViewerAccountKeys').textContent = JSON.stringify(accountKeys, null, 2);

                // Extract and highlight fee payer (first account, must be signer)
                let feePayer = '—';
                let feePayerValid = false;
                if (accountKeys.length > 0) {
                    const firstKey = accountKeys[0];
                    if (typeof firstKey === 'string') {
                        feePayer = firstKey;
                        feePayerValid = true;
                    } else if (firstKey.pubkey) {
                        feePayer = firstKey.pubkey;
                        feePayerValid = firstKey.signer === true;
                    }
                }

                // Display fee payer with validation indicator
                const feePayerElement = document.getElementById('txViewerFeePayer');
                if (feePayerValid) {
                    feePayerElement.textContent = feePayer;
                    feePayerElement.parentElement.style.borderColor = 'rgba(34, 197, 94, 0.5)';
                } else {
                    feePayerElement.textContent = feePayer;
                    feePayerElement.parentElement.style.borderColor = 'rgba(239, 68, 68, 0.3)';
                }

            } catch (error) {
                console.error('Error fetching transaction:', error);
                document.getElementById('txViewerAccountKeys').textContent = `Error: ${error.message || 'Failed to fetch transaction'}`;
            }
        }

        function closeTxViewer() {
            document.getElementById('txViewerModal').style.display = 'none';
        }

        function copyToClipboard(text) {
            navigator.clipboard.writeText(text).then(() => {
                alert('Copied to clipboard!');
            }).catch(err => {
                console.error('Failed to copy:', err);
            });
        }

        function closeCreatorDetails() {
            document.getElementById('creatorModal').style.display = 'none';
        }

        // Close modal when clicking outside
        window.onclick = function(event) {
            const metricsModal = document.getElementById('metricsModal');
            const creatorModal = document.getElementById('creatorModal');
            const txViewerModal = document.getElementById('txViewerModal');

            if (event.target === metricsModal) {
                metricsModal.style.display = 'none';
            }
            if (event.target === creatorModal) {
                creatorModal.style.display = 'none';
            }
            if (event.target === txViewerModal) {
                txViewerModal.style.display = 'none';
            }
        }

        // Close modal when pressing Escape
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape') {
                closeTokenMetrics();
                closeCreatorDetails();
                closeTxViewer();
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
    response = jsonify({'tokens': tokens})
    # Disable caching to ensure fresh data
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


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


@app.route('/api/creator-details/<creator_address>')
def api_creator_details(creator_address: str):
    """Get detailed information about a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # 1. Get all tokens launched by this creator
        cursor.execute("""
            SELECT
                mint,
                created_at,
                bonding_curve_pda,
                create_tx_signature,
                rug_probability,
                risk_level,
                market_cap_current,
                market_cap_highest,
                creator_is_blocked
            FROM token_analysis
            WHERE earliest_tx_creator = ?
            ORDER BY created_at DESC
        """, (creator_address,))
        tokens = [dict(row) for row in cursor.fetchall()]

        # 2. Get funding data (total inbound SOL, funder count, CEX funders)
        cursor.execute("""
            SELECT
                COUNT(DISTINCT funder_address) as funder_count,
                SUM(amount_sol) as total_sol,
                SUM(CASE WHEN is_cex = 1 THEN 1 ELSE 0 END) as cex_funder_count
            FROM creator_funders
            WHERE creator_address = ?
        """, (creator_address,))
        funding_row = cursor.fetchone()
        funding = {
            'total_funders': funding_row['funder_count'] if funding_row else 0,
            'total_sol': funding_row['total_sol'] if funding_row else 0,
            'cex_funders': funding_row['cex_funder_count'] if funding_row else 0
        }

        # 3. Get top funders (with CEX info)
        cursor.execute("""
            SELECT
                funder_address,
                amount_sol,
                is_cex,
                cex_exchange,
                cex_type
            FROM creator_funders
            WHERE creator_address = ?
            ORDER BY amount_sol DESC
            LIMIT 5
        """, (creator_address,))
        top_funders = [dict(row) for row in cursor.fetchall()]

        # 4. Get wallet cluster size
        cursor.execute("""
            SELECT
                COUNT(*) as total_wallets,
                SUM(CASE WHEN hop = 0 THEN 1 ELSE 0 END) as hop0_count,
                SUM(CASE WHEN hop = 1 THEN 1 ELSE 0 END) as hop1_count,
                SUM(CASE WHEN hop = 2 THEN 1 ELSE 0 END) as hop2_count
            FROM wallet_cluster_nodes
            WHERE root_creator = ?
        """, (creator_address,))
        cluster_row = cursor.fetchone()
        cluster = {
            'total_wallets': cluster_row['total_wallets'] if cluster_row else 0,
            'hop0': cluster_row['hop0_count'] if cluster_row else 0,
            'hop1': cluster_row['hop1_count'] if cluster_row else 0,
            'hop2': cluster_row['hop2_count'] if cluster_row else 0
        }

        # 5. Check blocklist status
        is_blocked = bool(tokens[0]['creator_is_blocked']) if tokens else False

        conn.close()

        return jsonify({
            'creator_address': creator_address,
            'tokens': tokens,
            'funding': funding,
            'top_funders': top_funders,
            'cluster': cluster,
            'is_blocked': is_blocked
        })

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


@app.route('/api/creators-batch', methods=['POST'])
def api_creators_batch():
    """Get creator enrichment data for multiple creators in one batch call"""
    creator_addresses = request.json.get('creators', []) if request.json else []
    if not creator_addresses:
        return jsonify({})

    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Token counts per creator
        placeholders = ','.join('?' * len(creator_addresses))
        cursor.execute(f"""
            SELECT earliest_tx_creator, COUNT(*) as token_count
            FROM token_analysis
            WHERE earliest_tx_creator IN ({placeholders})
            GROUP BY earliest_tx_creator
        """, creator_addresses)
        token_counts = {row['earliest_tx_creator']: row['token_count'] for row in cursor.fetchall()}

        # Funding data per creator (INBOUND only)
        cursor.execute(f"""
            SELECT
                creator_address,
                COUNT(*) as sources,
                SUM(amount_sol) as total_sol
            FROM creator_sol_flows
            WHERE creator_address IN ({placeholders}) AND flow_type = 'INBOUND'
            GROUP BY creator_address
        """, creator_addresses)
        funding_data = {}
        for row in cursor.fetchall():
            funding_data[row['creator_address']] = {
                'sources': row['sources'],
                'sol': row['total_sol'] if row['total_sol'] else 0
            }

        # Wallet cluster size per creator
        cursor.execute(f"""
            SELECT
                root_creator,
                COUNT(*) as total_wallets,
                SUM(CASE WHEN hop = 0 THEN 1 ELSE 0 END) as hop0,
                SUM(CASE WHEN hop = 1 THEN 1 ELSE 0 END) as hop1
            FROM wallet_cluster_nodes
            WHERE root_creator IN ({placeholders})
            GROUP BY root_creator
        """, creator_addresses)
        cluster_data = {}
        for row in cursor.fetchall():
            cluster_data[row['root_creator']] = {
                'size': row['total_wallets'],
                'hop0': row['hop0'],
                'hop1': row['hop1']
            }

        # Blocklist status
        cursor.execute(f"""
            SELECT DISTINCT earliest_tx_creator, creator_is_blocked
            FROM token_analysis
            WHERE earliest_tx_creator IN ({placeholders})
        """, creator_addresses)
        blocked_data = {row['earliest_tx_creator']: bool(row['creator_is_blocked']) for row in cursor.fetchall()}

        conn.close()

        # Build response
        result = {}
        for creator in creator_addresses:
            result[creator] = {
                'token_count': token_counts.get(creator, 0),
                'inbound_sources': funding_data.get(creator, {}).get('sources', 0),
                'inbound_sol': funding_data.get(creator, {}).get('sol', 0),
                'network_size': cluster_data.get(creator, {}).get('size', 0),
                'cluster_hops': {
                    'hop0': cluster_data.get(creator, {}).get('hop0', 0),
                    'hop1': cluster_data.get(creator, {}).get('hop1', 0)
                },
                'is_blocked': blocked_data.get(creator, False)
            }

        return jsonify(result)
    except Exception as e:
        print(f"[API] Error in creators-batch: {e}")
        return jsonify({})


# Migration settings (stored in file for persistence)
SETTINGS_FILE = "migration_settings.json"

def load_migration_settings():
    """Load migration settings from file"""
    import os
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except:
            pass
    # Default settings
    return {
        'token_history_check': True,
        'creator_history_check': True
    }

def save_migration_settings(settings):
    """Save migration settings to file"""
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f)
    except Exception as e:
        print(f"[SETTINGS] Error saving settings: {e}")

migration_settings = load_migration_settings()


def get_migration_setting(key: str, default=True) -> bool:
    """Get a migration setting value (for use by listener)"""
    global migration_settings
    # Reload from file to get latest changes
    migration_settings = load_migration_settings()
    return migration_settings.get(key, default)


@app.route('/api/migration-settings', methods=['POST', 'GET'])
def api_migration_settings():
    """Get or update migration feature settings"""
    global migration_settings

    if request.method == 'POST':
        data = request.json or {}
        old_settings = migration_settings.copy()

        # Track which settings changed
        changes = []

        if 'token_history_check' in data:
            old_val = old_settings.get('token_history_check', True)
            new_val = bool(data['token_history_check'])
            migration_settings['token_history_check'] = new_val
            if old_val != new_val:
                changes.append(f"Token History: {('✅ ON' if old_val else '❌ OFF')} → {('✅ ON' if new_val else '❌ OFF')}")

        if 'creator_history_check' in data:
            old_val = old_settings.get('creator_history_check', True)
            new_val = bool(data['creator_history_check'])
            migration_settings['creator_history_check'] = new_val
            if old_val != new_val:
                changes.append(f"Creator Analysis: {('✅ ON' if old_val else '❌ OFF')} → {('✅ ON' if new_val else '❌ OFF')}")

        # Persist to file
        save_migration_settings(migration_settings)

        # Log detailed state changes
        if changes:
            for change in changes:
                print(f"[SETTINGS] TOGGLED - {change}", flush=True)

        history_state = '✅ ON' if migration_settings['token_history_check'] else '❌ OFF'
        analysis_state = '✅ ON' if migration_settings['creator_history_check'] else '❌ OFF'
        print(f"[SETTINGS] Current State - Token History: {history_state} | Creator Analysis: {analysis_state}", flush=True)

        return jsonify({
            'status': 'updated',
            'settings': migration_settings
        })

    # GET - return current settings
    history_state = '✅ ON' if migration_settings['token_history_check'] else '❌ OFF'
    analysis_state = '✅ ON' if migration_settings['creator_history_check'] else '❌ OFF'
    print(f"[SETTINGS] Retrieved - Token History: {history_state} | Creator Analysis: {analysis_state}", flush=True)
    return jsonify(migration_settings)


@app.route('/api/cex-wallets', methods=['GET', 'POST', 'DELETE'])
def api_cex_wallets():
    """Manage CEX wallet mappings
    
    GET: List all known CEX wallets
    POST: Add a new CEX wallet
    DELETE: Remove a CEX wallet
    """
    try:
        if request.method == 'GET':
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Get all active CEX wallets
            cursor.execute("""
                SELECT cex_address, exchange_name, wallet_type, confidence_level, discovered_date, discovery_source, notes
                FROM cex_wallets
                WHERE is_active = 1
                ORDER BY exchange_name, wallet_type
            """)
            
            wallets = []
            for row in cursor.fetchall():
                wallets.append({
                    'address': row['cex_address'],
                    'exchange': row['exchange_name'],
                    'type': row['wallet_type'],
                    'confidence': row['confidence_level'],
                    'discovered': row['discovered_date'],
                    'source': row['discovery_source'],
                    'notes': row['notes']
                })
            
            conn.close()
            return jsonify({'wallets': wallets, 'total': len(wallets)})
        
        elif request.method == 'POST':
            data = request.json or {}
            
            # Validate required fields
            required = ['address', 'exchange', 'type']
            if not all(data.get(field) for field in required):
                return jsonify({'error': f'Missing required fields: {required}'}), 400
            
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO cex_wallets
                    (cex_address, exchange_name, wallet_type, confidence_level, discovery_source, notes, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (
                    data['address'],
                    data['exchange'],
                    data['type'],
                    data.get('confidence', 95),
                    data.get('source', 'Manual'),
                    data.get('notes', '')
                ))
                conn.commit()
                print(f"[CEX] ✅ Added {data['exchange']} wallet: {data['address'][:16]}...", flush=True)
                return jsonify({'status': 'added', 'address': data['address']}), 201
            finally:
                conn.close()
        
        elif request.method == 'DELETE':
            data = request.json or {}
            address = data.get('address')
            
            if not address:
                return jsonify({'error': 'Missing address parameter'}), 400
            
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            
            try:
                cursor.execute("UPDATE cex_wallets SET is_active = 0 WHERE cex_address = ?", (address,))
                conn.commit()
                
                if cursor.rowcount > 0:
                    print(f"[CEX] ✅ Deactivated wallet: {address[:16]}...", flush=True)
                    return jsonify({'status': 'deleted', 'address': address})
                else:
                    return jsonify({'error': 'Wallet not found'}), 404
            finally:
                conn.close()
    
    except Exception as e:
        print(f"[CEX_API] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


# =========================================================================
# MAIN
# =========================================================================

if __name__ == '__main__':
    print("[FLASK] Starting Migration Tracker UI...")
    print("[FLASK] Dashboard available at http://localhost:5002")
    print("[FLASK] Database: " + DB_PATH)
    app.run(host='0.0.0.0', port=5002, debug=False)
