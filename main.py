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
from infra_mapping import highlight_infra_in_funding

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
            # Get creator infrastructure tags if creator exists
            creator_infra_tags = []
            if row['earliest_tx_creator']:
                cursor.execute("""
                    SELECT tag, description FROM creator_tags
                    WHERE creator_address = ?
                """, (row['earliest_tx_creator'],))
                creator_infra_tags = [{'tag': t[0], 'description': t[1]} for t in cursor.fetchall()]

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
                'connected_malicious_count': row['connected_malicious_count'] if row['connected_malicious_count'] else 0,
                'creator_infra_tags': creator_infra_tags
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
    <title>Pump.Fun → PumpSwap Migration </title>
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
            display: flex;
            flex-direction: row;
            align-items: center;
            gap: 6px;
            flex-wrap: nowrap;
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

        /* Creator infrastructure tags container */
        .creator-infra-tags {
            display: inline-flex;
            flex-wrap: nowrap;
            gap: 0;
            margin: 0;
            white-space: nowrap;
        }

        /* Small infrastructure tag for creator address display */
        .creator-infra-tags .infra-tag {
            display: inline-block;
            padding: 2px 5px;
            border-radius: 2px;
            font-size: 9px;
            font-weight: 600;
            border: 1px solid;
        }

        .creator-infra-tags .tag {
            display: inline-block;
            padding: 1px 4px;
            border-radius: 2px;
            font-size: 8px;
            background: rgba(0, 0, 0, 0.3);
            color: #a0a0a0;
        }

        /* Category-specific colors for creator display */
        .creator-infra-tags .infra-automation {
            background: rgba(168, 85, 247, 0.2);
            color: #d8b4fe;
            border-color: rgba(168, 85, 247, 0.3);
        }

        .creator-infra-tags .infra-cex {
            background: rgba(34, 197, 94, 0.2);
            color: #4ade80;
            border-color: rgba(34, 197, 94, 0.3);
        }

        .creator-infra-tags .infra-system {
            background: rgba(107, 114, 128, 0.2);
            color: #d1d5db;
            border-color: rgba(107, 114, 128, 0.3);
        }

        .creator-infra-tags .infra-validator {
            background: rgba(59, 130, 246, 0.2);
            color: #60a5fa;
            border-color: rgba(59, 130, 246, 0.3);
        }

        .creator-infra-tags .infra-bridge {
            background: rgba(249, 115, 22, 0.2);
            color: #fb923c;
            border-color: rgba(249, 115, 22, 0.3);
        }

        .creator-infra-tags .infra-relay {
            background: rgba(249, 115, 22, 0.2);
            color: #fb923c;
            border-color: rgba(249, 115, 22, 0.3);
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

        /* Network indicator badges */
        .network-badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 12px;
            margin-left: 5px;
            font-weight: 600;
        }

        .network-badge.network-high {
            background: rgba(239, 68, 68, 0.2);
            color: #fca5a5;
            border: 1px solid rgba(239, 68, 68, 0.4);
            cursor: help;
        }

        .network-badge.network-medium {
            background: rgba(245, 158, 11, 0.2);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.4);
            cursor: help;
        }

        .network-badge.network-low {
            background: rgba(59, 130, 246, 0.2);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.4);
            cursor: help;
        }

        .shared-badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 12px;
            margin-left: 5px;
            background: rgba(139, 92, 246, 0.2);
            color: #c4b5fd;
            border: 1px solid rgba(139, 92, 246, 0.4);
            cursor: help;
        }

        /* Highlight rows with network connections */
        .row-network-coordinator {
            background: rgba(239, 68, 68, 0.05) !important;
            border-left: 2px solid rgba(239, 68, 68, 0.3) !important;
        }

        .row-shared-recipient {
            background: rgba(139, 92, 246, 0.05) !important;
            border-left: 2px solid rgba(139, 92, 246, 0.3) !important;
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

        #creatorTagsContainer {
            margin-bottom: 20px;
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }

        .creator-tag {
            background: rgba(34, 197, 94, 0.15);
            border: 1px solid rgba(34, 197, 94, 0.3);
            padding: 8px 12px;
            border-radius: 6px;
            cursor: help;
        }

        .tag-label {
            color: #4ade80;
            font-size: 13px;
            font-weight: 600;
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
            font-size: 12px;
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

        /* Source type badges */
        .original-sender-badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            background: rgba(34, 197, 94, 0.2);
            color: #4ade80;
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
        }

        .intermediary-badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            background: rgba(59, 130, 246, 0.2);
            color: #60a5fa;
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
        }

        /* Infrastructure tags */
        .infra-tag {
            display: inline-block;
            padding: 3px 7px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: 600;
            margin-right: 4px;
            white-space: nowrap;
        }

        .infra-automation {
            background: rgba(168, 85, 247, 0.2);
            color: #d8b4fe;
            border: 1px solid rgba(168, 85, 247, 0.3);
        }

        .infra-cex {
            background: rgba(34, 197, 94, 0.2);
            color: #4ade80;
            border: 1px solid rgba(34, 197, 94, 0.3);
        }

        .infra-system {
            background: rgba(107, 114, 128, 0.2);
            color: #d1d5db;
            border: 1px solid rgba(107, 114, 128, 0.3);
        }

        .infra-validator {
            background: rgba(59, 130, 246, 0.2);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.3);
        }

        .infra-bridge {
            background: rgba(249, 115, 22, 0.2);
            color: #fb923c;
            border: 1px solid rgba(249, 115, 22, 0.3);
        }

        .infra-relay {
            background: rgba(249, 115, 22, 0.2);
            color: #fb923c;
            border: 1px solid rgba(249, 115, 22, 0.3);
        }

        .tag {
            display: inline-block;
            padding: 2px 5px;
            border-radius: 2px;
            font-size: 9px;
            margin-right: 2px;
            background: rgba(0, 0, 0, 0.3);
            color: #a0a0a0;
        }

        .tag-infra {
            background: rgba(168, 85, 247, 0.15);
            color: #c084fc;
        }

        .tag-automation {
            background: rgba(168, 85, 247, 0.15);
            color: #c084fc;
        }

        .tag-oracle {
            background: rgba(34, 197, 94, 0.15);
            color: #6ee7b7;
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

        .action-button {
            padding: 8px 14px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            transition: all 0.3s;
            white-space: nowrap;
        }

        .action-button.danger {
            background-color: rgba(239, 68, 68, 0.2);
            color: #ef4444;
            border: 1px solid rgba(239, 68, 68, 0.4);
        }

        .action-button.danger:hover {
            background-color: rgba(239, 68, 68, 0.35);
            border-color: rgba(239, 68, 68, 0.7);
            box-shadow: 0 0 10px rgba(239, 68, 68, 0.2);
        }

        .action-button.danger:active {
            background-color: rgba(239, 68, 68, 0.5);
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
            <div class="control-group" style="border-left: 1px solid rgba(0, 212, 255, 0.3); margin-left: 12px; padding-left: 12px;">
                <span class="control-label">Token Launch</span>
                <div class="toggle-switch active" id="listenLaunchesToggle" onclick="toggleListenLaunches()">
                    <div class="toggle-slider"></div>
                </div>
                <span class="status-indicator active" id="listenLaunchesStatus"></span>
            </div>
            <div class="control-group" style="border-left: 1px solid rgba(139, 92, 246, 0.3); margin-left: 12px; padding-left: 12px;">
                <button id="pollingToggleBtn" class="action-button" onclick="togglePolling()" title="Toggle creator TX polling ON/OFF" style="background: rgba(76, 175, 80, 0.2); color: #4ade80; border: 1px solid rgba(76, 175, 80, 0.5);">▶️ Polling ON</button>
            </div>
            <div class="control-group" style="border-left: 1px solid rgba(239, 68, 68, 0.3); margin-left: 12px; padding-left: 12px;">
                <button class="action-button danger" onclick="emptyDatabase()" title="Clear all tokens, clustering, and address data">🗑️ Empty DB</button>
                <button class="action-button danger" onclick="killFlask()" title="Stop Flask server on port 5002">⏹️ Kill Port 5002</button>
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
            </div>

            <!-- Creator Tags -->
            <div id="creatorTagsContainer"></div>

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
                            <th>Tags</th>
                        </tr>
                    </thead>
                    <tbody id="topFundersBody">
                        <!-- Populated by JavaScript -->
                    </tbody>
                </table>
            </div>

            <!-- Top Recipients (Outgoing Transfers) -->
            <h3 style="margin-top: 20px;">Recipients (SOL Sent Out)</h3>
            <div class="top-recipients-container">
                <table class="top-funders-table">
                    <thead>
                        <tr>
                            <th>Recipient Address</th>
                            <th>Amount (SOL)</th>
                            <th>Type</th>
                        </tr>
                    </thead>
                    <tbody id="topRecipientsBody">
                        <!-- Populated by JavaScript -->
                    </tbody>
                </table>
            </div>

            <!-- Cross-Creator Network -->
            <h3 style="margin-top: 20px;">Cross-Creator Network Connections</h3>
            <div id="crossReferencesContainer" style="background: rgba(0, 0, 0, 0.3); border: 1px solid rgba(139, 92, 246, 0.3); border-radius: 6px; padding: 15px;">
                <!-- Populated by JavaScript -->
            </div>

            <!-- Wallet Cluster -->
            <h3 style="margin-top: 20px;">Wallet Network</h3>
            <div class="cluster-info" id="clusterInfo" style="background: rgba(0, 212, 255, 0.05); border: 1px solid rgba(0, 212, 255, 0.2); border-radius: 6px; padding: 15px;">
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

                // Load infrastructure mapping (separate infrastructure and CEX)
                window.infraMapping = { infrastructure: {}, cex: {} };
                try {
                    const infraResp = await fetch('/api/infrastructure-mapping');
                    if (infraResp.ok) {
                        window.infraMapping = await infraResp.json();
                    }
                } catch (e) {
                    console.log('Infrastructure mapping not available');
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

                            // Tags for the "Creator Tags" column (non-infrastructure)
                            const columnTags = [];

                            // Network size tag (show if > 10 wallets)
                            if (creatorData.network_size > 10) {
                                const hop0 = creatorData.cluster_hops?.hop0 || 0;
                                const hop1 = creatorData.cluster_hops?.hop1 || 0;
                                columnTags.push(`<span class="creator-tag tag-network" title="Wallet cluster: ${hop0} hop-0, ${hop1} hop-1">${creatorData.network_size} wallets</span>`);
                            }

                            // Funding tag (show if > 10 SOL)
                            if (creatorData.inbound_sol > 10) {
                                const sources = creatorData.inbound_sources || 0;
                                columnTags.push(`<span class="creator-tag tag-funding" title="Pre-launch funding">${creatorData.inbound_sol.toFixed(1)} SOL from ${sources} source${sources > 1 ? 's' : ''}</span>`);
                            }

                            // Repeat launcher tag (show if > 1 token)
                            if (creatorData.token_count > 1) {
                                columnTags.push(`<span class="creator-tag tag-repeat" title="Repeat launcher">${creatorData.token_count} tokens</span>`);
                            }

                            // Blocked tag
                            if (creatorData.is_blocked || token.creator_is_blocked) {
                                columnTags.push('<span class="creator-tag tag-blocked" title="On blocklist">BLOCKED</span>');
                            }

                            // Build infrastructure tags separately (for embedded display)
                            let infraTagsHTML = '';
                            if (token.creator_infra_tags && token.creator_infra_tags.length > 0) {
                                for (let infraTag of token.creator_infra_tags) {
                                    const tagColor = infraTag.tag.includes('debridge') ? '#ff9500' :
                                                   infraTag.tag.includes('meteora') ? '#00d4ff' :
                                                   infraTag.tag.includes('axiom') ? '#9333ea' : '#4ade80';
                                    infraTagsHTML += `<span class="creator-tag" style="border-color: ${tagColor}; color: ${tagColor}; display: inline-block; margin-right: 5px;" title="${infraTag.description}">${infraTag.tag.replace('uses_', '')}</span>`;
                                }
                            }

                            const creatorShort = token.creator ? token.creator.substring(0, 8) + '...' : 'N/A';
                            const creatorTitle = token.creator || 'Unknown';
                            const creatorElement = token.creator
                                ? `<a href="#" onclick="showCreatorDetails('${token.creator}'); return false;" class="mint-link creator-address-link" title="Click for creator details">${creatorTitle}</a>`
                                : '<span style="color: #a0a0a0;">Unknown</span>';

                            // Get infrastructure tags for creator or funders
                            let infraTags = '';
                            let displayName = null;
                            let displayCategory = null;
                            let displayDescription = '';

                            // Check if creator itself is infrastructure or CEX - show account name only
                            if (token.creator && window.infraMapping) {
                                if (window.infraMapping.infrastructure && window.infraMapping.infrastructure[token.creator]) {
                                    const info = window.infraMapping.infrastructure[token.creator];
                                    displayName = info.name;
                                    displayCategory = info.category;
                                    displayDescription = info.description;
                                }
                                if (!displayName && window.infraMapping.cex && window.infraMapping.cex[token.creator]) {
                                    const info = window.infraMapping.cex[token.creator];
                                    displayName = info.name;
                                    displayCategory = info.category;
                                    displayDescription = info.description;
                                }
                            }

                            // Check if any funders are infrastructure or CEX - show account name only
                            if (!displayName && token.creatorData && token.creatorData.funders) {
                                for (let funder of token.creatorData.funders) {
                                    // Check infrastructure funders first
                                    if (window.infraMapping && window.infraMapping.infrastructure && window.infraMapping.infrastructure[funder.address]) {
                                        const info = window.infraMapping.infrastructure[funder.address];
                                        displayName = info.name;
                                        displayCategory = info.category;
                                        displayDescription = info.description;
                                        break;
                                    }
                                    // Then check CEX funders
                                    if (window.infraMapping && window.infraMapping.cex && window.infraMapping.cex[funder.address]) {
                                        const info = window.infraMapping.cex[funder.address];
                                        displayName = info.name;
                                        displayCategory = info.category;
                                        displayDescription = info.description;
                                        break;
                                    }
                                }
                            }

                            // Render simple account name badge if we found a match
                            if (displayName && displayCategory) {
                                infraTags = `<div class="creator-infra-tags">
                                    <span class="infra-tag infra-${displayCategory}" title="${displayDescription}">${displayName}</span>
                                </div>`;
                            }

                            // Append creator infrastructure tags (deBridge, Meteora, Axiom) to infraTags
                            if (infraTagsHTML) {
                                infraTags += `<div style="margin-top: 5px;">${infraTagsHTML}</div>`;
                            }

                            return `
                                <tr>
                                    <td class="mint-with-creator">
                                        <a href="#" onclick="showTokenMetrics('${token.mint}'); return false;" class="mint-link" title="Click for metrics">${token.mint}</a>
                                        <div class="creator-address-embedded">
                                            ${creatorElement}
                                            ${infraTags}
                                        </div>
                                    </td>
                                    <td class="creator-tags">${columnTags.join('')}</td>
                                    <td class="rug-flag"></td>
                                    <td>
                                        <span class="risk-score ${getRiskClass(token.risk_level)}">${token.risk_level || '—'}</span>
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

        function formatDateISO(isoString) {
            if (!isoString) return '-';
            const date = new Date(isoString);
            if (isNaN(date.getTime())) return '-';
            return date.toLocaleString('en-US', { month: '2-digit', day: '2-digit', year: '2-digit' });
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

        // Listener feature toggles
        let listenLaunchesEnabled = true;

        function toggleListenLaunches() {
            listenLaunchesEnabled = !listenLaunchesEnabled;
            const toggle = document.getElementById('listenLaunchesToggle');
            const status = document.getElementById('listenLaunchesStatus');
            toggle.classList.toggle('active');
            status.classList.toggle('active');

            const state = listenLaunchesEnabled ? 'ENABLED' : 'DISABLED';
            console.log('🚀 [LISTENER] Token Launch: ' + state);

            // Send to backend
            fetch('/api/listener-settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    listen_to_launches: listenLaunchesEnabled
                })
            }).then(resp => resp.json()).then(data => {
                console.log('✅ [LISTENER] Updated - Launches: ' + state);
            }).catch(e => console.error('❌ Error updating listener settings:', e));
        }

        function emptyDatabase() {
            if (confirm('🚨 WARNING: This will permanently delete ALL tokens, clustering data, and address information. Are you sure?')) {
                fetch('/api/empty-database', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'}
                }).then(resp => resp.json()).then(data => {
                    console.log('✅ Database cleared:', data);
                    alert('✅ Database emptied successfully');
                    location.reload();
                }).catch(e => {
                    console.error('❌ Error clearing database:', e);
                    alert('❌ Error clearing database');
                });
            }
        }

        function killFlask() {
            if (confirm('⏹️ WARNING: This will stop the Flask server on port 5002. Page will become unresponsive. Continue?')) {
                fetch('/api/kill-server', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'}
                }).then(resp => {
                    console.log('✅ Kill signal sent to Flask');
                    alert('⏹️ Flask server stopped');
                }).catch(e => {
                    console.error('Server stopped (expected)', e);
                    alert('⏹️ Flask server stopped');
                });
            }
        }

        function togglePolling() {
            const btn = document.getElementById('pollingToggleBtn');
            const isEnabled = btn.textContent.includes('ON');

            fetch('/api/polling-control', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: 'toggle'})
            }).then(resp => resp.json()).then(data => {
                if (data.polling_enabled) {
                    btn.textContent = '▶️ Polling ON';
                    btn.style.background = 'rgba(76, 175, 80, 0.2)';
                    btn.style.color = '#4ade80';
                    btn.style.borderColor = 'rgba(76, 175, 80, 0.5)';
                    console.log('✅ Creator TX polling ENABLED');
                } else {
                    btn.textContent = '⏸️ Polling OFF';
                    btn.style.background = 'rgba(239, 68, 68, 0.2)';
                    btn.style.color = '#ef4444';
                    btn.style.borderColor = 'rgba(239, 68, 68, 0.5)';
                    console.log('✅ Creator TX polling DISABLED');
                }
            }).catch(e => {
                console.error('❌ Error toggling polling:', e);
                alert('❌ Error toggling polling');
            });
        }

        // Check polling status on page load
        async function checkPollingStatus() {
            try {
                const resp = await fetch('/api/polling-control');
                const data = await resp.json();
                const btn = document.getElementById('pollingToggleBtn');

                if (data.polling_enabled) {
                    btn.textContent = '▶️ Polling ON';
                    btn.style.background = 'rgba(76, 175, 80, 0.2)';
                    btn.style.color = '#4ade80';
                    btn.style.borderColor = 'rgba(76, 175, 80, 0.5)';
                } else {
                    btn.textContent = '⏸️ Polling OFF';
                    btn.style.background = 'rgba(239, 68, 68, 0.2)';
                    btn.style.color = '#ef4444';
                    btn.style.borderColor = 'rgba(239, 68, 68, 0.5)';
                }
            } catch (e) {
                console.error('Error checking polling status:', e);
            }
        }

        // Initialize settings from backend on page load
        async function initializeSettings() {
            try {
                // Load migration settings
                const respMig = await fetch('/api/migration-settings');
                const migSettings = await respMig.json();

                tokenHistoryEnabled = migSettings.token_history_check;
                clusteringEnabled = migSettings.creator_history_check;

                // Load listener settings
                const respListener = await fetch('/api/listener-settings');
                const listenerSettings = await respListener.json();

                listenLaunchesEnabled = listenerSettings.listen_to_launches;

                // Update migration toggle switch states
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

                // Update listener toggle switch states
                const listenLaunchesToggle = document.getElementById('listenLaunchesToggle');
                const listenLaunchesStatus = document.getElementById('listenLaunchesStatus');

                if (!listenLaunchesEnabled) {
                    listenLaunchesToggle.classList.remove('active');
                    listenLaunchesStatus.classList.remove('active');
                }

                const historyState = tokenHistoryEnabled ? '✅ ON' : '❌ OFF';
                const analysisState = clusteringEnabled ? '✅ ON' : '❌ OFF';
                const launchState = listenLaunchesEnabled ? '✅ ON' : '❌ OFF';
                console.log('📋 [SETTINGS LOADED] Migration - Token History: ' + historyState + ' | Creator Analysis: ' + analysisState);
                console.log('📋 [SETTINGS LOADED] Listener - Token Launch: ' + launchState);
            } catch (e) {
                console.error('❌ Error loading settings:', e);
            }
        }

        // Load tokens immediately and then every 10 seconds
        initializeSettings();
        checkPollingStatus();
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

                // Show CEX funders if any
                let fundersText = data.funding.total_funders || '0';
                if (data.funding.cex_funders > 0) {
                    fundersText = '🏦 ' + data.funding.cex_funders + ' CEX';
                }
                document.getElementById('creatorTotalFunders').textContent = fundersText;

                document.getElementById('creatorNetworkSize').textContent = (data.cluster.total_wallets || 0) + ' wallets';

                // Display creator tags
                const tagsContainer = document.getElementById('creatorTagsContainer');
                if (data.tags && data.tags.length > 0) {
                    const tagsHTML = data.tags.map(t => `
                        <div class="creator-tag" title="${t.description}">
                            <span class="tag-label">${t.tag}</span>
                        </div>
                    `).join('');
                    tagsContainer.innerHTML = tagsHTML;
                } else {
                    tagsContainer.innerHTML = '';
                }

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
                                <td>${formatDateISO(token.created_at)}</td>
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
                            const cexBadge = funder.is_cex ? `<span class="cex-badge">${funder.cex_exchange}</span>` : '';

                            // Source type badge
                            let sourceTypeBadge = '';
                            if (funder.source_type === 'intermediary') {
                                sourceTypeBadge = '<span class="intermediary-badge" title="Relay/intermediary account">ℹ️ Relay</span>';
                            }
                            // Original sender: leave blank (no badge)

                            // Infrastructure tags
                            let infraTags = '';
                            if (funder.is_infrastructure) {
                                const tags = funder.tags || [];
                                const categoryTag = `<span class="infra-tag infra-${funder.category}" title="${funder.description || ''}">${funder.category.toUpperCase()}</span>`;
                                const otherTags = tags.map(tag => `<span class="tag tag-${tag}">${tag}</span>`).join('');
                                infraTags = categoryTag + ' ' + otherTags;
                            }

                            // Format amount: show more decimals for small amounts
                            const amountStr = funder.amount_sol < 0.01
                                ? funder.amount_sol.toFixed(6)
                                : funder.amount_sol.toFixed(2);

                            return `
                                <tr>
                                    <td title="${funder.funder_address}" style="font-family: monospace; font-size: 12px;">${funder.funder_address.substring(0, 16)}...${cexBadge}</td>
                                    <td>${amountStr} SOL</td>
                                    <td>${sourceTypeBadge || (funder.is_cex ? 'CEX' : 'Wallet')}</td>
                                    <td>${infraTags || '—'}</td>
                                </tr>
                            `;
                        }).join('');
                } else {
                    fundersBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #a0a0a0;">No funding data available</td></tr>';
                }

                // Populate top recipients table (where creator sent SOL)
                const recipientsBody = document.getElementById('topRecipientsBody');
                if (data.top_recipients && data.top_recipients.length > 0) {
                    recipientsBody.innerHTML = data.top_recipients.map(recipient => {
                        // Check if this recipient is connected to other creators
                        let networkIndicator = '';
                        let networkTooltip = '';

                        if (recipient.is_network_coordinator) {
                            const info = recipient.coordinator_info;
                            networkIndicator = `<span class="network-badge network-${info.confidence}" title="Network Coordinator">🔗</span>`;
                            networkTooltip = `Linked to ${info.creator_count} creators | Confidence: ${info.confidence}`;
                        } else if (recipient.shared_with_creators) {
                            networkIndicator = `<span class="shared-badge" title="Shared with ${recipient.shared_creator_count} other creators">👥</span>`;
                            networkTooltip = `Also linked to: ${recipient.shared_with_creators.slice(0, 2).map(c => c.substring(0, 8) + '...').join(', ')}${recipient.shared_with_creators.length > 2 ? ' +' + (recipient.shared_with_creators.length - 2) + ' more' : ''}`;
                        }

                        // Format amount: show more decimals for small amounts
                        const recipientAmountStr = recipient.amount_sol < 0.01
                            ? recipient.amount_sol.toFixed(6)
                            : recipient.amount_sol.toFixed(2);

                        return `
                            <tr class="${recipient.is_network_coordinator ? 'row-network-coordinator' : recipient.shared_with_creators ? 'row-shared-recipient' : ''}">
                                <td title="${recipient.recipient_address}" style="font-family: monospace; font-size: 12px;">
                                    ${recipient.recipient_address}
                                    ${networkIndicator ? `<div style="margin-top: 3px; font-size: 10px; color: #a0a0a0;">${networkTooltip}</div>` : ''}
                                </td>
                                <td>${recipientAmountStr} SOL</td>
                                <td>${networkIndicator || 'Wallet'}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    recipientsBody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: #a0a0a0;">No outgoing transfers</td></tr>';
                }

                // Populate cross-creator references
                const crossRefsContainer = document.getElementById('crossReferencesContainer');
                if (data.cross_references && data.cross_references.length > 0) {
                    let crossRefsHTML = '';
                    for (const crossRef of data.cross_references) {
                        const creatorList = crossRef.other_creators
                            .slice(0, 3)
                            .map(c => `<span style="background: rgba(139, 92, 246, 0.2); padding: 2px 6px; border-radius: 3px; margin: 2px; display: inline-block; font-size: 10px; font-family: monospace;">${c.substring(0, 12)}...</span>`)
                            .join('');
                        const moreCreators = crossRef.other_creators.length > 3 ? `<span style="color: #a0a0a0; font-size: 10px;"> +${crossRef.other_creators.length - 3} more</span>` : '';

                        crossRefsHTML += `
                            <div style="margin-bottom: 12px; padding: 10px; background: rgba(139, 92, 246, 0.05); border-left: 3px solid rgba(139, 92, 246, 0.3); border-radius: 4px;">
                                <div style="font-family: monospace; font-size: 11px; color: #00d4ff; word-break: break-all; margin-bottom: 5px;">
                                    ${crossRef.recipient_address}
                                </div>
                                <div style="font-size: 11px; color: #c4b5fd;">
                                    <strong>⚠️ Also linked to ${crossRef.creator_count} other creator${crossRef.creator_count > 1 ? 's' : ''}:</strong>
                                </div>
                                <div style="margin-top: 5px;">
                                    ${creatorList} ${moreCreators}
                                </div>
                            </div>
                        `;
                    }
                    crossRefsContainer.innerHTML = crossRefsHTML;
                } else {
                    crossRefsContainer.innerHTML = '<p style="color: #a0a0a0; text-align: center; margin: 0;">No cross-creator connections detected ✓</p>';
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

            try {
                // Use backend endpoint to avoid CORS issues
                const response = await fetch(`/api/transaction/${signature}`, {
                    method: 'GET',
                    headers: {'Content-Type': 'application/json'}
                });
                const data = await response.json();

                // Check for API errors
                if (data.error) {
                    document.getElementById('txViewerAccountKeys').textContent = `Error: ${data.error}`;
                    return;
                }

                if (!data.account_keys) {
                    document.getElementById('txViewerAccountKeys').textContent = 'Transaction not found or no account keys';
                    return;
                }

                const accountKeys = data.account_keys;

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

        # 2. Get funding data - MERGED from both sources (funders + outgoing transfers from tx_ledger)
        # Pre-migration funders
        cursor.execute("""
            SELECT
                COUNT(DISTINCT funder_address) as funder_count,
                SUM(amount_sol) as total_sol,
                SUM(CASE WHEN is_cex = 1 THEN 1 ELSE 0 END) as cex_funder_count
            FROM creator_funders
            WHERE creator_address = ?
        """, (creator_address,))
        funders_row = cursor.fetchone()
        
        # Post-migration outgoing transfers from creator_receivers
        cursor.execute("""
            SELECT
                COUNT(DISTINCT receiver_address) as recipient_count,
                SUM(amount_sol) as total_sol_out
            FROM creator_receivers
            WHERE creator_address = ?
        """, (creator_address,))
        recipients_row = cursor.fetchone()

        # Combine both sources
        funder_count = (funders_row['funder_count'] or 0) if funders_row else 0
        funders_sol = (funders_row['total_sol'] or 0) if funders_row else 0
        recipient_count = (recipients_row['recipient_count'] or 0) if recipients_row else 0
        recipients_sol = (recipients_row['total_sol_out'] or 0) if recipients_row else 0
        
        funding = {
            'total_funders': funder_count,
            'total_sol_in': funders_sol,
            'total_recipients': recipient_count,
            'total_sol_out': recipients_sol,
            'total_accounts': funder_count + recipient_count,
            'total_sol': funders_sol + recipients_sol
        }

        # 3. Get top funders (with CEX info and source_type classification)
        cursor.execute("""
            SELECT
                funder_address,
                amount_sol,
                is_cex,
                cex_exchange,
                cex_type,
                COALESCE(source_type, 'original_sender') as source_type
            FROM creator_funders
            WHERE creator_address = ?
            ORDER BY amount_sol DESC
            LIMIT 5
        """, (creator_address,))
        top_funders = [dict(row) for row in cursor.fetchall()]

        # Add infrastructure highlighting to funders
        top_funders = highlight_infra_in_funding(top_funders)

        # 4. Get top recipients from creator_receivers (post-migration outgoing transfers)
        cursor.execute("""
            SELECT
                receiver_address as recipient_address,
                amount_sol,
                receiver_type,
                receiver_name
            FROM creator_receivers
            WHERE creator_address = ?
            ORDER BY amount_sol DESC
            LIMIT 10
        """, (creator_address,))
        top_recipients = [dict(row) for row in cursor.fetchall()]

        # Add infrastructure highlighting to recipients (use recipient_address field)
        for recipient in top_recipients:
            recipient_info = highlight_infra_in_funding([{"funder_address": recipient["recipient_address"], "amount_sol": recipient["amount_sol"]}])[0]
            recipient.update({
                "is_infrastructure": recipient_info["is_infrastructure"],
                "category": recipient_info["category"],
                "tags": recipient_info["tags"],
                "display_name": recipient_info["display_name"],
            })

        # 5. Get wallet cluster size (includes coordinated funders and recipients)
        cursor.execute("""
            SELECT COUNT(DISTINCT wallet_addr) as total_wallets,
                   SUM(CASE WHEN hop = 0 THEN 1 ELSE 0 END) as hop0_count,
                   SUM(CASE WHEN hop = 1 THEN 1 ELSE 0 END) as hop1_count,
                   SUM(CASE WHEN hop = 2 THEN 1 ELSE 0 END) as hop2_count
            FROM (
                SELECT wallet as wallet_addr, hop FROM wallet_cluster_nodes WHERE root_creator = ?
                UNION
                SELECT funder_address as wallet_addr, 0 as hop FROM creator_funders WHERE creator_address = ?
                UNION
                SELECT DISTINCT receiver_address as wallet_addr, 0 as hop FROM creator_receivers
                    WHERE creator_address = ?
            )
        """, (creator_address, creator_address, creator_address))
        cluster_row = cursor.fetchone()

        cluster = {
            'total_wallets': cluster_row['total_wallets'] or 0,
            'hop0': cluster_row['hop0_count'] or 0,
            'hop1': cluster_row['hop1_count'] or 0,
            'hop2': cluster_row['hop2_count'] or 0
        }

        # 6. Check blocklist status
        is_blocked = bool(tokens[0]['creator_is_blocked']) if tokens else False

        # 7. Get creator tags
        cursor.execute("""
            SELECT tag, description
            FROM creator_tags
            WHERE creator_address = ?
        """, (creator_address,))
        tags = [{'tag': row[0], 'description': row[1]} for row in cursor.fetchall()]

        # 8. Get cross-creator references (network detection)
        cross_refs = []
        try:
            from unified_recipient_tracker import UnifiedRecipientTracker
            tracker = UnifiedRecipientTracker()
            shared = tracker.find_shared_recipients(creator_address)
            for recipient, other_creators in shared.items():
                if other_creators:
                    cross_refs.append({
                        'recipient_address': recipient,
                        'other_creators': other_creators,
                        'creator_count': len(other_creators),
                        'connection_type': 'shared_recipient'
                    })
            cross_refs.sort(key=lambda x: x['creator_count'], reverse=True)
        except Exception as e:
            cross_refs = []

        # 9. Check if any recipients are network coordinators
        coordinator_flags = {}
        try:
            from unified_recipient_tracker import UnifiedRecipientTracker
            tracker = UnifiedRecipientTracker()
            coordinators = tracker.get_network_coordinators(min_creators=2)
            for coord in coordinators:
                if coord.address in [r.get('recipient_address') for r in top_recipients]:
                    coordinator_flags[coord.address] = {
                        'creator_count': coord.creator_count,
                        'confidence': coord.network_confidence,
                        'suspicious_flags': coord.suspicious_flags
                    }
        except Exception as e:
            coordinator_flags = {}

        conn.close()

        # Enhance top_recipients with cross-reference info
        for recipient in top_recipients:
            recipient_addr = recipient.get('recipient_address')
            if recipient_addr in coordinator_flags:
                recipient['is_network_coordinator'] = True
                recipient['coordinator_info'] = coordinator_flags[recipient_addr]
            else:
                recipient['is_network_coordinator'] = False
            for cross_ref in cross_refs:
                if cross_ref['recipient_address'] == recipient_addr:
                    recipient['shared_with_creators'] = cross_ref['other_creators']
                    recipient['shared_creator_count'] = cross_ref['creator_count']
                    break

        return jsonify({
            'creator_address': creator_address,
            'tokens': tokens,
            'funding': funding,
            'top_funders': top_funders,
            'top_recipients': top_recipients,
            'cross_references': cross_refs,
            'cluster': cluster,
            'is_blocked': is_blocked,
            'tags': tags
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- Creator SOL Watch Endpoints ---

@app.route('/api/creator-sol-stats/<creator_address>')
def api_creator_sol_stats(creator_address: str):
    """Get SOL in/out summary for a creator"""
    try:
        from creator_watch_manager import CreatorWatchManager

        # Create temporary manager instance to query stats
        helius_rpc = f"https://mainnet.helius-rpc.com/?api-key={os.getenv('HELIUS_API_KEY')}" if os.getenv("HELIUS_API_KEY") else "https://api.mainnet-beta.solana.com"
        manager = CreatorWatchManager(
            rpc_url=helius_rpc
        )

        stats = manager.get_creator_stats(creator_address)

        if not stats:
            return jsonify({'error': 'Creator not found in watch'}), 404

        return jsonify(stats)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/infrastructure-mapping')
def api_infrastructure_mapping():
    """Get infrastructure account mapping for UI highlighting (infrastructure + CEX separate)"""
    try:
        from infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS

        mapping = {
            "infrastructure": {},
            "cex": {}
        }

        # Infrastructure accounts
        for address, info in INFRASTRUCTURE_ACCOUNTS.items():
            mapping["infrastructure"][address] = {
                "name": info["name"],
                "category": info["category"],
                "description": info["description"],
                "tags": info.get("tags", []),
                "risk_level": info["risk_level"],
            }

        # CEX accounts
        for address, info in CEX_ACCOUNTS.items():
            mapping["cex"][address] = {
                "name": info["name"],
                "category": info["category"],
                "exchange": info.get("exchange"),
                "description": info["description"],
                "tags": info.get("tags", []),
                "risk_level": info["risk_level"],
            }

        return jsonify(mapping)

    except Exception as e:
        return jsonify({"infrastructure": {}, "cex": {}}), 200


@app.route('/api/creator-sol-ledger/<creator_address>')
def api_creator_sol_ledger(creator_address: str):
    """Get recent SOL transactions for a creator"""
    try:
        limit = request.args.get('limit', 50, type=int)

        from creator_watch_manager import CreatorWatchManager

        # Create temporary manager instance to query ledger
        helius_rpc = f"https://mainnet.helius-rpc.com/?api-key={os.getenv('HELIUS_API_KEY')}" if os.getenv("HELIUS_API_KEY") else "https://api.mainnet-beta.solana.com"
        manager = CreatorWatchManager(
            rpc_url=helius_rpc
        )

        ledger = manager.get_recent_ledger(creator_address, limit=limit)

        return jsonify({
            'creator_address': creator_address,
            'transactions': [
                {
                    'signature': tx['signature'],
                    'blockTime': tx['blockTime'],
                    'delta_sol': tx['delta_sol_lamports'] / 1e9 if tx['delta_sol_lamports'] else 0,
                    'fee_sol': tx['fee_lamports'] / 1e9 if tx['fee_lamports'] else 0,
                    'type': tx['tx_type'],
                    'counterparty': tx['counterparty']
                }
                for tx in ledger
            ]
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/transaction/<signature>')
def api_transaction(signature: str):
    """Fetch transaction details from Solana RPC"""
    try:
        import aiohttp
        import asyncio

        async def fetch_tx():
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post("https://api.mainnet-beta.solana.com", json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    data = await resp.json()
                    return data

        # Run async function
        data = asyncio.run(fetch_tx())

        # Check for RPC errors
        if data.get("error"):
            return jsonify({'error': f"RPC Error: {data['error'].get('message', 'Unknown error')}"}), 400

        if not data.get("result"):
            return jsonify({'error': 'Transaction not found'}), 404

        tx = data["result"]

        # Extract account keys
        try:
            message = tx.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            return jsonify({
                'signature': signature,
                'account_keys': account_keys,
                'success': True
            })
        except Exception as e:
            return jsonify({'error': f'Failed to parse transaction: {str(e)}'}), 400

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

        # Top funders per creator (for infrastructure tagging)
        cursor.execute(f"""
            SELECT
                creator_address,
                funder_address,
                amount_sol,
                is_cex,
                cex_exchange,
                cex_type
            FROM creator_funders
            WHERE creator_address IN ({placeholders})
            ORDER BY amount_sol DESC
        """, creator_addresses)
        funders_data = {}
        for row in cursor.fetchall():
            creator = row['creator_address']
            if creator not in funders_data:
                funders_data[creator] = []
            funders_data[creator].append({
                'address': row['funder_address'],
                'amount_sol': row['amount_sol'],
                'is_cex': bool(row['is_cex']),
                'cex_exchange': row['cex_exchange'],
                'cex_type': row['cex_type']
            })

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
                'is_blocked': blocked_data.get(creator, False),
                'funders': funders_data.get(creator, [])
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


@app.route('/api/listener-settings', methods=['GET', 'POST'])
def api_listener_settings():
    """Get or update listener settings (token launch listening)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if request.method == 'POST':
            data = request.json or {}

            # Update listen_to_launches setting
            if 'listen_to_launches' in data:
                old_val = None
                try:
                    cursor.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", ('listen_to_launches',))
                    row = cursor.fetchone()
                    if row:
                        old_val = row['setting_value'] == 'true'
                except:
                    pass

                new_val = 'true' if data['listen_to_launches'] else 'false'
                cursor.execute("""
                    INSERT OR REPLACE INTO listener_settings
                    (setting_key, setting_value, last_updated)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, ('listen_to_launches', new_val))

                if old_val is not None and old_val != data['listen_to_launches']:
                    status = '✅ ON' if data['listen_to_launches'] else '❌ OFF'
                    print(f"[LISTENER] TOGGLED - Token Launch: {status}", flush=True)

            conn.commit()

            # Get current settings
            cursor.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", ('listen_to_launches',))
            row = cursor.fetchone()
            listen_launches = row['setting_value'] == 'true' if row else True

            conn.close()
            return jsonify({'status': 'updated', 'listen_to_launches': listen_launches})

        else:  # GET
            cursor.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", ('listen_to_launches',))
            row = cursor.fetchone()
            listen_launches = row['setting_value'] == 'true' if row else True
            conn.close()
            return jsonify({'listen_to_launches': listen_launches})

    except Exception as e:
        print(f"[LISTENER_API] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


# --- Unified Recipient Tracking Endpoints ---

@app.route('/api/creator-recipients/<creator_address>')
def api_creator_recipients(creator_address: str):
    """Get all recipient links for a creator (unified tracking)"""
    try:
        from unified_recipient_tracker import UnifiedRecipientTracker

        tracker = UnifiedRecipientTracker()
        links = tracker.get_recipient_links_for_creator(creator_address)

        recipients = []
        for link in links:
            recipients.append({
                'recipient_address': link.recipient_address,
                'total_sol_sent': link.total_sol_sent,
                'transfer_count': link.transfer_count,
                'confidence': link.confidence,
                'source': link.source,
                'is_cex': link.is_cex,
                'cex_exchange': link.cex_exchange,
                'is_suspicious': link.is_suspicious
            })

        return jsonify({
            'creator_address': creator_address,
            'recipients': recipients,
            'total_recipients': len(recipients),
            'total_sol_sent': sum(r['total_sol_sent'] for r in recipients)
        })

    except ImportError:
        return jsonify({'error': 'Unified recipient tracker not available'}), 503
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-cross-references/<creator_address>')
def api_creator_cross_references(creator_address: str):
    """Find other creators that share recipient addresses with this creator"""
    try:
        from unified_recipient_tracker import UnifiedRecipientTracker

        tracker = UnifiedRecipientTracker()
        shared = tracker.find_shared_recipients(creator_address)

        shared_recipients = []
        for recipient, other_creators in shared.items():
            shared_recipients.append({
                'recipient_address': recipient,
                'other_creators': other_creators,
                'creator_count': len(other_creators)
            })

        # Sort by creator count (most suspicious first)
        shared_recipients.sort(key=lambda x: x['creator_count'], reverse=True)

        return jsonify({
            'creator_address': creator_address,
            'shared_recipients': shared_recipients,
            'total_shared': len(shared_recipients),
            'cross_creator_links': sum(r['creator_count'] for r in shared_recipients)
        })

    except ImportError:
        return jsonify({'error': 'Unified recipient tracker not available'}), 503
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/network-coordinators')
def api_network_coordinators():
    """Get network coordinators (addresses linked to multiple creators)"""
    try:
        from unified_recipient_tracker import UnifiedRecipientTracker

        min_creators = request.args.get('min_creators', 2, type=int)

        tracker = UnifiedRecipientTracker()
        coordinators = tracker.get_network_coordinators(min_creators=min_creators)

        result = []
        for coord in coordinators:
            result.append({
                'address': coord.address,
                'creator_count': coord.creator_count,
                'creators_linked': coord.creators,
                'total_sol_moved': coord.total_sol_moved,
                'network_confidence': coord.network_confidence,
                'is_cex': coord.is_cex,
                'suspicious_flags': coord.suspicious_flags
            })

        return jsonify({
            'coordinators': result,
            'total_coordinators': len(result),
            'min_creators_threshold': min_creators
        })

    except ImportError:
        return jsonify({'error': 'Unified recipient tracker not available'}), 503
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/unified-merge-status')
def api_unified_merge_status():
    """Check unified recipient tracking status and run merge if needed"""
    try:
        from unified_recipient_tracker import UnifiedRecipientTracker

        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Check if unified table has data
        cursor.execute("SELECT COUNT(*) FROM creator_recipients_unified")
        unified_count = cursor.fetchone()[0]

        # Check source tables
        cursor.execute("SELECT COUNT(*) FROM creator_outgoing_transfers")
        outgoing_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_tx_ledger")
        ledger_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM network_coordinators")
        coordinator_count = cursor.fetchone()[0]

        conn.close()

        status = {
            'unified_recipients': unified_count,
            'outgoing_transfers': outgoing_count,
            'tx_ledger_entries': ledger_count,
            'network_coordinators': coordinator_count,
            'merge_needed': unified_count == 0 and outgoing_count > 0
        }

        # If merge needed and requested, run it
        if status['merge_needed'] and request.args.get('run_merge') == 'true':
            tracker = UnifiedRecipientTracker()
            merge_results = tracker.run_full_merge_and_analysis()
            status['merge_results'] = merge_results
            status['merge_completed'] = True

        return jsonify(status)

    except ImportError:
        return jsonify({'error': 'Unified recipient tracker not available'}), 503
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/empty-database', methods=['POST'])
def api_empty_database():
    """Empty all tokens, clustering, and creator tracking data"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Get counts before deletion
        cursor.execute("SELECT COUNT(*) FROM token_analysis")
        token_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM wallet_cluster_nodes")
        cluster_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_tx_ledger")
        ledger_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_watch")
        watch_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_state")
        state_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_funders")
        funders_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_outgoing_transfers")
        outgoing_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_recipients_unified")
        unified_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM network_coordinators")
        coordinator_count = cursor.fetchone()[0]

        # Delete all data
        cursor.execute("DELETE FROM token_analysis")
        cursor.execute("DELETE FROM wallet_cluster_nodes")
        cursor.execute("DELETE FROM clustering_alerts")
        cursor.execute("DELETE FROM creator_tx_ledger")
        cursor.execute("DELETE FROM creator_state")
        cursor.execute("DELETE FROM creator_watch")
        cursor.execute("DELETE FROM creator_funders")
        cursor.execute("DELETE FROM creator_outgoing_transfers")
        cursor.execute("DELETE FROM creator_recipients_unified")
        cursor.execute("DELETE FROM network_coordinators")

        conn.commit()
        conn.close()

        return jsonify({
            'status': 'success',
            'deleted': {
                'tokens': token_count,
                'cluster_nodes': cluster_count,
                'clustering_alerts': cursor.rowcount,
                'creator_watch': watch_count,
                'creator_state': state_count,
                'creator_tx_ledger': ledger_count,
                'creator_funders': funders_count,
                'creator_outgoing_transfers': outgoing_count,
                'creator_recipients_unified': unified_count,
                'network_coordinators': coordinator_count,
                'total_items_deleted': token_count + cluster_count + watch_count + state_count + ledger_count + funders_count + outgoing_count + unified_count + coordinator_count
            }
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/api/polling-control', methods=['GET', 'POST'])
def api_polling_control():
    """Get or set creator TX polling status"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        
        # Ensure settings table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS polling_settings (
                setting_name TEXT PRIMARY KEY,
                setting_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        
        if request.method == 'GET':
            # Get current polling status
            cursor.execute("SELECT setting_value FROM polling_settings WHERE setting_name = 'polling_enabled'")
            row = cursor.fetchone()
            polling_enabled = row[0] == '1' if row else True
            
            conn.close()
            return jsonify({
                'status': 'enabled' if polling_enabled else 'paused',
                'polling_enabled': polling_enabled
            })
        
        elif request.method == 'POST':
            data = request.get_json()
            action = data.get('action')  # 'enable', 'disable', 'toggle'
            
            if action == 'toggle':
                # Get current state
                cursor.execute("SELECT setting_value FROM polling_settings WHERE setting_name = 'polling_enabled'")
                row = cursor.fetchone()
                current = row[0] == '1' if row else True
                new_value = '0' if current else '1'
            elif action == 'enable':
                new_value = '1'
            elif action == 'disable':
                new_value = '0'
            else:
                conn.close()
                return jsonify({'error': 'Invalid action'}), 400
            
            # Update setting
            cursor.execute("""
                INSERT OR REPLACE INTO polling_settings (setting_name, setting_value)
                VALUES ('polling_enabled', ?)
            """, (new_value,))
            conn.commit()
            conn.close()
            
            polling_enabled = new_value == '1'
            return jsonify({
                'status': 'success',
                'polling_enabled': polling_enabled,
                'action': action
            })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/kill-server', methods=['POST'])
def api_kill_server():
    """Kill the Flask server"""
    try:
        # Return success response first
        response = jsonify({'status': 'server_stopping'})

        # Then shutdown the server
        import os
        import signal
        os.kill(os.getpid(), signal.SIGTERM)

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
