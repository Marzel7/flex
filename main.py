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
import threading
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

# Analysis result cache for background operations
app.funder_analysis_cache = {}

# =========================================================================
# DATABASE QUERIES
# =========================================================================

def get_migrated_tokens() -> List[Dict]:
    """Get all analyzed post-migration tokens"""
    try:
        from infra_mapping import CEX_ACCOUNTS
        
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Query post-migration analysis data - LIMIT to 25 most recent tokens for UI display
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
            LIMIT 25
        """)

        tokens = []
        for row in cursor.fetchall():
            # Get creator infrastructure tags if creator exists
            creator_infra_tags = []
            if row['earliest_tx_creator']:
                # Deduplicate tags across sources
                seen_tags = set()

                # Get tags from creator_tags (domain tags, infrastructure markers)
                cursor.execute("""
                    SELECT tag, description, amount_sol FROM creator_tags
                    WHERE creator_address = ?
                """, (row['earliest_tx_creator'],))
                for tag_row in cursor.fetchall():
                    tag_name = tag_row[0]
                    if tag_name not in seen_tags:
                        seen_tags.add(tag_name)
                        creator_infra_tags.append({'tag': tag_name, 'description': tag_row[1], 'amount_sol': tag_row[2]})

                # Also get service tags from creator_service_history (uses_jitotip, uses_meteora, etc.)
                cursor.execute("""
                    SELECT DISTINCT tag FROM creator_service_history
                    WHERE creator_address = ?
                """, (row['earliest_tx_creator'],))
                service_tags = cursor.fetchall()
                for service_tag_row in service_tags:
                    tag_name = service_tag_row[0]
                    if tag_name not in seen_tags:  # Skip if already added from creator_tags
                        seen_tags.add(tag_name)
                        # Create description for service tags
                        tag_desc = {
                            'uses_jitotip': 'Uses Jito tips on CREATE transaction',
                            'uses_jitotip_other': 'Uses Jito MEV tips on transactions',
                            'uses_meteora': 'Uses Meteora DLMM liquidity',
                            'uses_debridge': 'Uses deBridge cross-chain transfers',
                            'uses_axiom': 'Uses Axiom for verification'
                        }.get(tag_name, f'Uses {tag_name}')
                        creator_infra_tags.append({'tag': tag_name, 'description': tag_desc, 'amount_sol': None})

            # Get top funder for creator (to show CEX funding info)
            top_funder = None
            funding_checked = False
            if row['earliest_tx_creator']:
                cursor.execute("""
                    SELECT funder_address, cex_exchange, cex_type, amount_sol, is_cex
                    FROM creator_funders
                    WHERE creator_address = ?
                    ORDER BY amount_sol DESC
                    LIMIT 1
                """, (row['earliest_tx_creator'],))
                funder_row = cursor.fetchone()
                if funder_row:
                    funder_addr = funder_row[0]
                    is_cex = bool(funder_row[4])
                    cex_exchange = funder_row[1]
                    cex_type = funder_row[2]

                    # Check if funder is in live CEX_ACCOUNTS mapping
                    if not is_cex and funder_addr in CEX_ACCOUNTS:
                        is_cex = True
                        cex_info = CEX_ACCOUNTS[funder_addr]
                        cex_exchange = cex_info.get('exchange', cex_info.get('name', 'CEX'))
                        cex_type = cex_info.get('category', 'Wallet')

                    top_funder = {
                        'address': funder_addr,
                        'cex_exchange': cex_exchange,
                        'cex_type': cex_type,
                        'amount_sol': funder_row[3],
                        'is_cex': is_cex
                    }

                # Check if funding has been extracted (has funder records)
                cursor.execute("""
                    SELECT COUNT(*) as funding_count FROM creator_funders
                    WHERE creator_address = ?
                """, (row['earliest_tx_creator'],))
                funding_result = cursor.fetchone()
                funding_checked = funding_result[0] > 0 if funding_result else False

            # Get network information if token belongs to a network
            network_name = None
            network_id = None
            cursor.execute("""
                SELECT fn.network_id, fn.network_name
                FROM funding_networks fn
                INNER JOIN funding_network_shared_tokens fnst ON fn.network_id = fnst.network_id
                WHERE fnst.mint = ?
                LIMIT 1
            """, (row['mint'],))
            network_row = cursor.fetchone()
            if network_row:
                network_id = network_row[0]
                network_name = network_row[1]

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
                'creator_infra_tags': creator_infra_tags,
                'top_funder': top_funder,
                'funding_checked': funding_checked,
                'network_name': network_name,
                'network_id': network_id
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
            line-height: 1.4;
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 3px;
            flex-wrap: wrap;
        }

        /* Creator address link - clickable text, not a badge */
        .creator-address-link {
            display: inline !important;
            padding: 0 !important;
            background: none !important;
            border: none !important;
            color: #00d4ff !important;
            text-decoration: underline !important;
            cursor: pointer !important;
            font-family: 'Courier New', monospace !important;
            font-size: 10px !important;
        }

        .creator-address-link:hover {
            color: #0099ff;
            text-decoration-thickness: 2px;
        }

        /* Creator tags container - styles handled by inline styles on wrapper div */
        .creator-tags {
            /* Table cell - flex styles applied via wrapper div */
        }

        /* Base creator tag styling */
        .creator-tag {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: 600;
            white-space: nowrap;
            border: 1px solid rgba(0, 212, 255, 0.3);
            background: rgba(0, 212, 255, 0.15);
            color: #00d4ff;
        }

        /* Network size tag (cyan) */
        .tag-network {
            background: rgba(0, 212, 255, 0.15);
            color: #00d4ff;
            border: 1px solid rgba(0, 212, 255, 0.3);
        }

        /* Funding tag (cyan) */
        .tag-funding {
            background: rgba(0, 212, 255, 0.15);
            color: #00d4ff;
            border: 1px solid rgba(0, 212, 255, 0.3);
        }

        /* Repeat launcher tag (cyan) */
        .tag-repeat {
            background: rgba(0, 212, 255, 0.15);
            color: #00d4ff;
            border: 1px solid rgba(0, 212, 255, 0.3);
        }

        /* Blocked tag (cyan) */
        .tag-blocked {
            background: rgba(0, 212, 255, 0.15);
            color: #00d4ff;
            border: 1px solid rgba(239, 68, 68, 0.3);
            font-weight: 700;
        }

        /* Creator infrastructure tags container */
        .creator-infra-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
            margin: 0;
            align-items: center;
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
            color: #00d4ff !important;
            text-decoration: none;
            border-bottom: 1px dotted #00d4ff;
            transition: all 0.2s;
        }

        .mint-link.creator-address-link {
            text-decoration: underline !important;
            border-bottom: none !important;
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

        /* Super-cluster tab buttons */
        .sc-tab-button {
            padding: 10px 20px !important;
            background: none !important;
            border: none !important;
            color: #a0a0a0 !important;
            cursor: pointer !important;
            font-size: 14px !important;
            transition: color 0.2s ease !important;
        }

        .sc-tab-button:hover {
            color: #00d4ff !important;
        }

        .sc-tab-button.active {
            color: #00d4ff !important;
            border-bottom: 2px solid #00d4ff !important;
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

        /* CEX Funders Section */
        .cex-funders-container {
            max-height: 250px;
            overflow-y: auto;
            margin-bottom: 20px;
            padding: 15px;
            background: rgba(34, 197, 94, 0.05);
            border-left: 3px solid #4ade80;
            border-radius: 4px;
        }

        .cex-funders-table {
            width: 100%;
            border-collapse: collapse;
        }

        .cex-funders-table th {
            background: rgba(34, 197, 94, 0.15);
            padding: 10px;
            text-align: left;
            font-size: 12px;
            color: #4ade80;
            border-bottom: 2px solid rgba(34, 197, 94, 0.3);
            font-weight: 600;
        }

        .cex-funders-table td {
            padding: 10px;
            font-size: 12px;
            border-bottom: 1px solid rgba(34, 197, 94, 0.1);
            color: #e0e0e0;
        }

        .cex-funders-table tr:hover {
            background: rgba(34, 197, 94, 0.1);
        }

        /* Multi-creator funders styling */
        .multi-creator-container {
            background: rgba(239, 68, 68, 0.05);
            padding: 15px;
            border-radius: 8px;
            border: 1px solid rgba(239, 68, 68, 0.2);
        }

        .multi-creator-container .cex-funders-table th {
            background: rgba(239, 68, 68, 0.15);
            color: #fca5a5;
            border-bottom: 2px solid rgba(239, 68, 68, 0.3);
        }

        .multi-creator-container .cex-funders-table td {
            border-bottom: 1px solid rgba(239, 68, 68, 0.1);
        }

        .multi-creator-container .cex-funders-table tr:hover {
            background: rgba(239, 68, 68, 0.1);
        }

        .cex-exchange-name {
            color: #4ade80;
            font-weight: 600;
            display: flex;
            align-items: center;
        }

        .cex-exchange-name::before {
            content: '🏛️';
            margin-right: 5px;
        }

        /* Jito Tips Table */
        .jitotips-table {
            width: 100%;
            border-collapse: collapse;
        }

        .jitotips-table th {
            background: rgba(0, 0, 0, 0.3);
            padding: 10px;
            text-align: left;
            font-size: 12px;
            color: #a0a0a0;
            border-bottom: 1px solid rgba(0, 212, 255, 0.2);
        }

        .jitotips-table td {
            padding: 10px;
            font-size: 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        .jitotips-table tr:hover {
            background: rgba(0, 212, 255, 0.05);
        }

        /* Funder stats grid (multi-creator funders modal) */
        .funder-stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        /* Funders table */
        .funders-container {
            max-height: 400px;
            overflow-y: auto;
            margin-bottom: 20px;
            border: 1px solid rgba(139, 92, 246, 0.2);
            border-radius: 6px;
            padding: 10px;
        }

        .funders-table {
            width: 100%;
            border-collapse: collapse;
        }

        .funders-table th {
            background: rgba(139, 92, 246, 0.15);
            padding: 10px;
            text-align: left;
            font-size: 12px;
            color: #a78bfa;
            border-bottom: 2px solid rgba(139, 92, 246, 0.3);
            font-weight: 600;
        }

        .funders-table td {
            padding: 10px;
            font-size: 12px;
            border-bottom: 1px solid rgba(139, 92, 246, 0.1);
            color: #e0e0e0;
        }

        .funders-table tr:hover {
            background: rgba(139, 92, 246, 0.1);
        }

        .funders-table a {
            color: #a78bfa;
            text-decoration: none;
            cursor: pointer;
        }

        .funders-table a:hover {
            text-decoration: underline;
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

        /* Domain tags (SNS domains) */
        .domain-tag {
            display: inline-block;
            padding: 3px 7px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: 600;
            margin-right: 4px;
            background: rgba(59, 130, 246, 0.2);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.3);
            white-space: nowrap;
        }

        /* Other address tags */
        .address-tag {
            display: inline-block;
            padding: 3px 7px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: 600;
            margin-right: 4px;
            background: rgba(139, 92, 246, 0.2);
            color: #c4b5fd;
            border: 1px solid rgba(139, 92, 246, 0.3);
            white-space: nowrap;
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

        /* CEX View Styles */
        .cex-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        .cex-exchange-card {
            background: rgba(34, 197, 94, 0.05);
            border: 1px solid rgba(34, 197, 94, 0.3);
            border-left: 3px solid #4ade80;
            border-radius: 6px;
            padding: 15px;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .cex-exchange-card:hover {
            background: rgba(34, 197, 94, 0.1);
            border-left: 4px solid #4ade80;
            box-shadow: 0 0 10px rgba(34, 197, 94, 0.1);
        }

        .cex-exchange-card h4 {
            color: #4ade80;
            margin: 0 0 10px 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .cex-exchange-card .stat {
            display: flex;
            justify-content: space-between;
            margin: 8px 0;
            font-size: 13px;
            color: #e0e0e0;
        }

        .cex-exchange-card .stat-label {
            color: #a0a0a0;
        }

        .cex-exchange-card .stat-value {
            color: #00d4ff;
            font-weight: 600;
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
                <button class="action-button" id="tokensTabBtn" onclick="switchToTokensTab()" title="View tokens" style="background: rgba(99, 102, 241, 0.2); color: #6366f1; border: 1px solid rgba(99, 102, 241, 0.5); margin-left: 8px;">Tokens</button>
                <button class="action-button" id="networksTabBtn" onclick="switchToNetworksTab()" title="View funding networks" style="background: rgba(139, 92, 246, 0.2); color: #a78bfa; border: 1px solid rgba(139, 92, 246, 0.5); margin-left: 8px;">🔗 Networks</button>
                <button class="action-button" onclick="window.location.href = '/coordinated-funders'" title="Analyze funders supporting multiple creators" style="background: rgba(139, 92, 246, 0.2); color: #a78bfa; border: 1px solid rgba(139, 92, 246, 0.5); margin-left: 8px;">Coordinated Funders</button>
                <button class="action-button" onclick="openValidationModal()" title="Validate a transaction signature" style="background: rgba(59, 130, 246, 0.2); color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.5); margin-left: 8px;">Validate TX</button>
                <button id="funderExtractionBtn" class="action-button" onclick="toggleFunderExtraction()" title="Toggle funder transfer extraction (incoming/outgoing)" style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.5); margin-left: 8px;">Funder Extraction OFF</button>
            </div>
            <div class="control-group" style="border-left: 1px solid rgba(239, 68, 68, 0.3); margin-left: 12px; padding-left: 12px;">
                <button class="action-button danger" onclick="emptyDatabase()" title="Clear all tokens, clustering, and address data">Empty DB</button>
                <button class="action-button danger" onclick="killFlask()" title="Stop Flask server on port 5002">Kill Port 5002</button>
            </div>
        </div>

        <div id="tokens-container">
            <div class="loading">Loading migrated tokens...</div>
        </div>

        <!-- Funding Networks View -->
        <!-- Super-Clusters Networks View -->
        <div id="funding-network-container" style="display: none; padding: 20px;">
            <div style="margin-bottom: 30px;">
                <h2 style="color: #ef4444; margin-bottom: 10px;">🚨 Super-Clusters (Coordinated Networks)</h2>
                <p style="color: #a0a0a0; font-size: 14px; margin-bottom: 20px;">
                    Networks with ≥5 shared creators have been merged into super-clusters. These represent large coordinated funding operations identified through sophisticated network analysis.
                </p>

                <!-- Super-Cluster Statistics -->
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 30px;">
                    <div style="background: rgba(239, 68, 68, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #ef4444;">
                        <div style="color: #a0a0a0; font-size: 12px; text-transform: uppercase; margin-bottom: 8px;">Total Clusters</div>
                        <div style="font-size: 24px; font-weight: bold; color: #ef4444;" id="scTotalCount">—</div>
                    </div>
                    <div style="background: rgba(239, 68, 68, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #ef4444;">
                        <div style="color: #a0a0a0; font-size: 12px; text-transform: uppercase; margin-bottom: 8px;">Critical</div>
                        <div style="font-size: 24px; font-weight: bold; color: #ef4444;" id="scCriticalCount">—</div>
                    </div>
                    <div style="background: rgba(249, 115, 22, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #f97316;">
                        <div style="color: #a0a0a0; font-size: 12px; text-transform: uppercase; margin-bottom: 8px;">High Risk</div>
                        <div style="font-size: 24px; font-weight: bold; color: #f97316;" id="scHighCount">—</div>
                    </div>
                    <div style="background: rgba(251, 191, 36, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #fbbf24;">
                        <div style="color: #a0a0a0; font-size: 12px; text-transform: uppercase; margin-bottom: 8px;">Medium Risk</div>
                        <div style="font-size: 24px; font-weight: bold; color: #fbbf24;" id="scMediumCount">—</div>
                    </div>
                </div>

                <!-- Super-Clusters Grid -->
                <div id="super-clusters-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap: 20px;">
                    <div class="loading" style="grid-column: 1 / -1;">Loading super-clusters...</div>
                </div>
            </div>
        </div>

        <!-- CEX Funders View -->
        <div id="cex-container" style="display: none;">
            <div style="padding: 20px;">
                <h2 style="color: #4ade80; margin-bottom: 20px;">🏛️ CEX Funders Activity</h2>

                <!-- CEX Exchanges Summary -->
                <div style="margin-bottom: 30px;">
                    <h3 style="color: #00d4ff; margin-bottom: 15px;">Exchanges Funding Creators</h3>
                    <div id="cexExchangesContainer" class="cex-grid">
                        <div class="loading">Loading CEX exchanges...</div>
                    </div>
                </div>

                <!-- Top CEX Funder Wallets -->
                <div style="margin-bottom: 30px;">
                    <h3 style="color: #00d4ff; margin-bottom: 15px;">Top CEX Wallet Funders</h3>
                    <div id="topCexFundersContainer" style="overflow-x: auto;">
                        <table class="tokens-table" style="width: 100%;">
                            <thead>
                                <tr>
                                    <th>CEX Address</th>
                                    <th>Exchange</th>
                                    <th>Wallet Type</th>
                                    <th>Creators Funded</th>
                                    <th>Total SOL</th>
                                </tr>
                            </thead>
                            <tbody id="topCexFundersBody">
                                <tr><td colspan="5" style="text-align: center; color: #a0a0a0;">Loading...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- CEX-Funded Creators -->
                <div>
                    <h3 style="color: #00d4ff; margin-bottom: 15px;">Creators Funded by CEX</h3>
                    <div id="cexFundedCreatorsContainer" style="overflow-x: auto;">
                        <table class="tokens-table" style="width: 100%;">
                            <thead>
                                <tr>
                                    <th>Creator Address</th>
                                    <th>Exchanges Funding</th>
                                    <th>Total CEX Funding (SOL)</th>
                                    <th>Action</th>
                                </tr>
                            </thead>
                            <tbody id="cexFundedCreatorsBody">
                                <tr><td colspan="4" style="text-align: center; color: #a0a0a0;">Loading...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
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

            <!-- Analysis Buttons -->
            <div style="margin: 20px 0; text-align: center; display: flex; gap: 10px; justify-content: center; flex-wrap: wrap;">
                <button onclick="showFundingNetwork3Tier(document.getElementById('modalCreator').textContent.split(' ')[0])" style="background: rgba(239, 68, 68, 0.2); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.5); padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: bold;">View Funding Patterns</button>
                <button onclick="window.location.href = '/coordinated-funder-analysis/' + document.getElementById('modalCreator').textContent.split(' ')[0]" style="background: rgba(249, 115, 22, 0.2); color: #f97316; border: 1px solid rgba(249, 115, 22, 0.5); padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: bold;">Coordinated Network</button>
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

            <!-- Tokens Funded Section -->
            <div id="tokensFundedSection" style="display: none; margin-bottom: 20px;">
                <h3 style="color: #00d4ff;">💰 Tokens Funded (As Funder)</h3>
                <div class="tokens-funded-container">
                    <table class="tokens-launched-table">
                        <thead>
                            <tr>
                                <th>Token Mint</th>
                                <th>Creator</th>
                                <th>Funding Amount (SOL)</th>
                                <th>Created</th>
                                <th>Risk</th>
                            </tr>
                        </thead>
                        <tbody id="tokensFundedBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Jito Tips History Section -->
            <div id="jitotipsSection" style="display: none; margin-bottom: 20px;">
                <h3 style="color: #00d4ff;">💸 Jito Tips History</h3>
                <div class="jitotips-container">
                    <table class="jitotips-table">
                        <thead>
                            <tr>
                                <th>Token Mint</th>
                                <th>Tip Amount (SOL)</th>
                                <th>% of Cost</th>
                                <th>Type</th>
                                <th>Date</th>
                            </tr>
                        </thead>
                        <tbody id="jitotipsBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- CEX Funders Section -->
            <div id="cexFundersSection" style="display: none; margin-bottom: 20px;">
                <h3 style="color: #00d4ff;">🏛️ CEX Funders</h3>
                <div class="cex-funders-container">
                    <table class="cex-funders-table">
                        <thead>
                            <tr>
                                <th>CEX Funder</th>
                                <th>Address</th>
                                <th>Amount (SOL)</th>
                            </tr>
                        </thead>
                        <tbody id="cexFundersBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Multi-Creator Funders Section (Coordination Risk) -->
            <div id="multiCreatorFundersSection" style="display: none; margin-bottom: 20px;">
                <h3 style="color: #ef4444;">⚠️ Multi-Creator Funders (Coordination Risk)</h3>
                <div class="multi-creator-container">
                    <div id="multiCreatorRiskBanner" style="background: rgba(239, 68, 68, 0.1); border-left: 4px solid #ef4444; padding: 15px; margin-bottom: 15px; border-radius: 4px;">
                        <p style="color: #fca5a5; margin: 0; font-size: 13px;">
                            <strong>⚠️ Alert:</strong> This funder is also funding other token creators. This could indicate coordinated activity.
                        </p>
                    </div>
                    <table class="cex-funders-table" style="border-left: 4px solid #ef4444;">
                        <thead>
                            <tr>
                                <th>Funder Address</th>
                                <th>Creators Funded</th>
                                <th>Total SOL Sent</th>
                                <th>First Funding</th>
                                <th>Last Funding</th>
                            </tr>
                        </thead>
                        <tbody id="tokenMetricsMultiCreatorFundersBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Other Labeled Funders Section -->
            <div id="otherFundersSection" style="display: none; margin-bottom: 20px;">
                <h3 style="color: #00d4ff;">🏛️ Other Labeled Funders</h3>
                <div class="cex-funders-container">
                    <table class="cex-funders-table">
                        <thead>
                            <tr>
                                <th>Funder Name</th>
                                <th>Category</th>
                                <th>Amount (SOL)</th>
                            </tr>
                        </thead>
                        <tbody id="otherFundersBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- All Funders Section -->
            <div id="allFundersSection" style="margin-bottom: 20px;">
                <h3 style="color: #00d4ff;">💰 All Funders</h3>
                <div class="cex-funders-container">
                    <table class="cex-funders-table">
                        <thead>
                            <tr>
                                <th>Funder Address</th>
                                <th>Amount (SOL)</th>
                                <th>Type</th>
                            </tr>
                        </thead>
                        <tbody id="allFundersBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
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

    <!-- Multi-Creator Funders Modal -->
    <div id="multiCreatorFundersModal" class="modal">
        <div class="modal-content" style="max-width: 1000px; max-height: 90vh; overflow-y: auto;">
            <span class="close" onclick="closeMultiCreatorFunders()">&times;</span>
            <h2>🔗 Coordinated Funder Analysis</h2>

            <!-- Statistics Summary -->
            <div class="funder-stats-grid" id="funderStatsGrid">
                <div class="stat-box">
                    <label>Suspicious Multi-Creator</label>
                    <span id="suspiciousFundersCount">—</span>
                </div>
                <div class="stat-box">
                    <label>Safe (INFRA/CEX)</label>
                    <span id="safeFundersCount">—</span>
                </div>
                <div class="stat-box">
                    <label>Total Funders</label>
                    <span id="totalFundersCount">—</span>
                </div>
            </div>

            <!-- Suspicious Multi-Creator Funders Table -->
            <h3>⚠️ Suspicious Multi-Creator Funders</h3>
            <div class="funders-container">
                <table class="funders-table">
                    <thead>
                        <tr>
                            <th>Funder Address</th>
                            <th>Network</th>
                            <th>Creators Funded</th>
                            <th>Total SOL</th>
                            <th>Funding Records</th>
                            <th>Activity Period</th>
                        </tr>
                    </thead>
                    <tbody id="multiCreatorFundersBody">
                        <tr><td colspan="6" style="text-align: center; color: #a0a0a0;">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- Funder Details (Expandable) -->
            <div id="funderDetailsContainer" style="margin-top: 30px; display: none;">
                <h3>Funder Details</h3>
                <div id="funderDetailsList">
                    <!-- Populated by JavaScript -->
                </div>
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

    <!-- Transaction Validation Modal -->
    <div id="validationModal" class="modal">
        <div class="modal-content" style="max-width: 800px;">
            <span class="close" onclick="closeValidationModal()">&times;</span>
            <h2>🔍 Transaction Validation</h2>

            <div style="margin-bottom: 20px;">
                <label style="display: block; color: #a0a0a0; font-size: 12px; margin-bottom: 8px; text-transform: uppercase;">Transaction Signature</label>
                <input
                    type="text"
                    id="validationInput"
                    placeholder="Paste transaction signature (e.g., 2NcBKN1RV35onHE1fP7wmjfb8PWrmhBgvsvemPaoVt2DkcV5...)"
                    style="width: 100%; padding: 12px; background: rgba(0, 0, 0, 0.5); border: 1px solid rgba(0, 212, 255, 0.3); border-radius: 6px; color: #e0e0e0; font-family: monospace; font-size: 12px; box-sizing: border-box;"
                >
            </div>

            <div style="display: flex; gap: 10px; margin-bottom: 20px;">
                <button
                    onclick="validateTransaction()"
                    style="flex: 1; padding: 12px; background: rgba(59, 130, 246, 0.2); color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.5); border-radius: 6px; cursor: pointer; font-weight: bold; transition: all 0.2s;"
                    onmouseover="this.style.background='rgba(59, 130, 246, 0.4)'"
                    onmouseout="this.style.background='rgba(59, 130, 246, 0.2)'"
                >
                    ✅ Validate
                </button>
                <button
                    onclick="closeValidationModal()"
                    style="flex: 1; padding: 12px; background: rgba(100, 100, 100, 0.2); color: #a0a0a0; border: 1px solid rgba(100, 100, 100, 0.5); border-radius: 6px; cursor: pointer;"
                >
                    Cancel
                </button>
            </div>

            <div id="validationResults" style="display: none;">
                <div style="background: rgba(0, 212, 255, 0.05); border: 1px solid rgba(0, 212, 255, 0.2); border-radius: 6px; padding: 20px;">

                    <!-- Loading State -->
                    <div id="validationLoading" style="text-align: center; color: #00d4ff;">
                        <div style="font-size: 24px; margin-bottom: 10px;">⏳</div>
                        <div>Validating transaction...</div>
                    </div>

                    <!-- Results State -->
                    <div id="validationSuccess" style="display: none;">
                        <div style="color: #4ade80; font-weight: bold; margin-bottom: 15px;">✅ PUMP.FUN CREATE TRANSACTION CONFIRMED</div>

                        <div style="margin-bottom: 15px;">
                            <div style="color: #a0a0a0; font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Token Mint</div>
                            <div style="color: #00d4ff; font-family: monospace; font-size: 12px; word-break: break-all;" id="resultMint">—</div>
                        </div>

                        <div style="margin-bottom: 15px;">
                            <div style="color: #a0a0a0; font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Creator (Fee Payer)</div>
                            <div style="color: #4ade80; font-family: monospace; font-size: 12px; word-break: break-all;" id="resultCreator">—</div>
                        </div>

                        <div style="margin-bottom: 15px;">
                            <div style="color: #a0a0a0; font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Timestamp</div>
                            <div style="color: #e0e0e0; font-size: 12px;" id="resultTimestamp">—</div>
                        </div>

                        <div style="background: rgba(0, 0, 0, 0.3); padding: 12px; border-radius: 4px; border-left: 3px solid #00d4ff;">
                            <div style="color: #a0a0a0; font-size: 10px; text-transform: uppercase; margin-bottom: 8px;">Evidence</div>
                            <div id="resultEvidence" style="color: #e0e0e0; font-size: 11px; line-height: 1.6;">—</div>
                        </div>

                        <div style="margin-top: 15px;">
                            <a id="resultSolscanLink" href="#" target="_blank" style="color: #3b82f6; text-decoration: none; font-size: 12px;">
                                🔗 View on Solscan →
                            </a>
                        </div>
                    </div>

                    <!-- Error State -->
                    <div id="validationError" style="display: none; color: #ef4444;">
                        <div style="font-weight: bold; margin-bottom: 10px;">❌ Validation Failed</div>
                        <div id="errorMessage" style="font-size: 12px; color: #ff6b6b;">—</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- 3-Tier Funding Network Modal -->
    <div id="fundingNetwork3TierModal" class="modal">
        <div class="modal-content" style="max-width: 800px;">
            <span class="close" onclick="closeFundingNetwork3Tier()">&times;</span>
            <h2>Funding Patterns</h2>

            <!-- 3-Tier Network Visualization -->
            <div style="background: rgba(0, 0, 0, 0.2); border-radius: 8px; padding: 15px;">
                <div id="fn3tNetworkBody" style="font-family: monospace; font-size: 12px; line-height: 2; color: #e0e0e0; max-height: 500px; overflow-y: auto;">
                    <div style="color: #a0a0a0;">Loading network...</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Coordinated Funder Analysis Modal -->
    <div id="coordinatedFunderAnalysisModal" class="modal">
        <div class="modal-content" style="max-width: 900px;">
            <span class="close" onclick="closeCoordinatedFunderAnalysis()">&times;</span>
            <h2>Coordinated Funder Analysis</h2>

            <!-- Network Risk Summary -->
            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; margin-bottom: 20px;">
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid #ef4444;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 5px;">NETWORK RISK</div>
                    <div id="cfaRiskLevel" style="color: #ef4444; font-size: 18px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid #fbbf24;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 5px;">CONNECTED CREATORS</div>
                    <div id="cfaConnectedCount" style="color: #fbbf24; font-size: 18px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid #f97316;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 5px;">SHARED DESTINATIONS</div>
                    <div id="cfaSharedDests" style="color: #f97316; font-size: 18px; font-weight: bold;">—</div>
                </div>
            </div>

            <!-- Connected Creators List -->
            <h3 style="color: #e0e0e0; margin-top: 20px;">Connected Creators</h3>
            <div style="background: rgba(0, 0, 0, 0.2); border-radius: 8px; padding: 15px; max-height: 300px; overflow-y: auto;">
                <div id="cfaConnectedCreators" style="font-family: monospace; font-size: 12px; line-height: 1.8; color: #e0e0e0;">
                    <div style="color: #a0a0a0;">Loading...</div>
                </div>
            </div>

            <!-- Shared Destinations List -->
            <h3 style="color: #e0e0e0; margin-top: 20px;">Shared Destinations</h3>
            <div style="background: rgba(0, 0, 0, 0.2); border-radius: 8px; padding: 15px; max-height: 300px; overflow-y: auto;">
                <div id="cfaSharedDestinations" style="font-family: monospace; font-size: 12px; line-height: 1.8; color: #e0e0e0;">
                    <div style="color: #a0a0a0;">Loading...</div>
                </div>
            </div>

            <!-- Analysis Timestamp -->
            <div style="color: #a0a0a0; font-size: 10px; margin-top: 15px;">
                <div>Analyzed: <span id="cfaDetectedAt">—</span></div>
            </div>
        </div>
    </div>

    <!-- Funder Transfer Details Modal -->
    <div id="funderDetailsModal" class="modal">
        <div class="modal-content" style="max-width: 1000px;">
            <span class="close" onclick="closeFunderDetails()">&times;</span>
            <h2>Funder: <span id="fdFunderAddr" style="font-family: monospace; font-size: 14px;">—</span></h2>

            <!-- Summary Stats -->
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px;">
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid #fbbf24;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 5px;">CREATORS FUNDED</div>
                    <div id="fdCreatorCount" style="color: #fbbf24; font-size: 18px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid #4ade80;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 5px;">INCOMING SOL</div>
                    <div id="fdIncomingTotal" style="color: #4ade80; font-size: 18px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid #ef4444;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 5px;">OUTGOING SOL</div>
                    <div id="fdOutgoingTotal" style="color: #ef4444; font-size: 18px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid #f97316;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 5px;">NET FLOW</div>
                    <div id="fdNetFlow" style="color: #f97316; font-size: 18px; font-weight: bold;">—</div>
                </div>
            </div>

            <!-- Incoming Transfers -->
            <h3 style="color: #e0e0e0; margin-top: 20px;">Incoming Transfers (Senders)</h3>
            <div style="background: rgba(0, 0, 0, 0.2); border-radius: 8px; padding: 0; max-height: 350px; overflow-y: auto;">
                <table style="width: 100%; font-size: 12px; border-collapse: collapse;">
                    <thead style="position: sticky; top: 0; background: rgba(0, 0, 0, 0.4);">
                        <tr style="border-bottom: 1px solid rgba(0, 212, 255, 0.2);">
                            <th style="padding: 10px; text-align: left; color: #a0a0a0;">Sender Address</th>
                            <th style="padding: 10px; text-align: right; color: #a0a0a0;">SOL</th>
                            <th style="padding: 10px; text-align: center; color: #a0a0a0;">Txs</th>
                            <th style="padding: 10px; text-align: left; color: #a0a0a0;">Classification</th>
                        </tr>
                    </thead>
                    <tbody id="fdIncomingBody">
                        <tr><td colspan="4" style="padding: 20px; text-align: center; color: #a0a0a0;">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- Outgoing Transfers -->
            <h3 style="color: #e0e0e0; margin-top: 20px;">Outgoing Transfers (Recipients)</h3>
            <div style="background: rgba(0, 0, 0, 0.2); border-radius: 8px; padding: 0; max-height: 350px; overflow-y: auto;">
                <table style="width: 100%; font-size: 12px; border-collapse: collapse;">
                    <thead style="position: sticky; top: 0; background: rgba(0, 0, 0, 0.4);">
                        <tr style="border-bottom: 1px solid rgba(0, 212, 255, 0.2);">
                            <th style="padding: 10px; text-align: left; color: #a0a0a0;">Recipient Address</th>
                            <th style="padding: 10px; text-align: right; color: #a0a0a0;">SOL</th>
                            <th style="padding: 10px; text-align: center; color: #a0a0a0;">Txs</th>
                            <th style="padding: 10px; text-align: left; color: #a0a0a0;">Classification</th>
                        </tr>
                    </thead>
                    <tbody id="fdOutgoingBody">
                        <tr><td colspan="4" style="padding: 20px; text-align: center; color: #a0a0a0;">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Super-Cluster Details Modal -->
    <div id="superClusterModal" class="modal">
        <div class="modal-content" style="max-width: 1000px;">
            <span class="close" onclick="closeSuperCluster()">&times;</span>
            <h2>Super-Cluster Details - <span id="scModalId" style="font-size: 16px; color: #00d4ff;"></span></h2>

            <!-- Risk Badge -->
            <div style="display: inline-block; margin-bottom: 20px;">
                <span id="scRiskBadge" style="padding: 8px 16px; border-radius: 6px; font-weight: bold; font-size: 14px;">—</span>
            </div>

            <!-- Cluster Stats -->
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 25px;">
                <div style="background: rgba(59, 130, 246, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #3b82f6;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Networks</div>
                    <div id="scNetworkCount" style="color: #3b82f6; font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(245, 158, 11, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #f59e0b;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Creators</div>
                    <div id="scCreatorCount" style="color: #f59e0b; font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(59, 130, 246, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #3b82f6;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Tokens</div>
                    <div id="scTokenCount" style="color: #3b82f6; font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(74, 222, 128, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #4ade80;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Funders</div>
                    <div id="scFunderCount" style="color: #4ade80; font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(168, 85, 247, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #a855f7;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Total SOL</div>
                    <div id="scTotalSol" style="color: #a855f7; font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(168, 85, 247, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #a855f7;">
                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">CEX Funders</div>
                    <div id="scCexCount" style="color: #a855f7; font-size: 20px; font-weight: bold;">—</div>
                </div>
            </div>

            <!-- Root Operators & Relationship -->
            <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <h4 style="color: #a0a0a0; font-size: 12px; margin-bottom: 15px; text-transform: uppercase;">Root Operators & Cluster Relationship</h4>
                <div id="scRootAddresses" style="display: flex; flex-direction: column; gap: 12px;">
                    <!-- Populated by JS -->
                </div>
                <!-- Relationship Diagram -->
                <div id="scRelationshipDiagram" style="margin-top: 20px; padding-top: 20px; border-top: 1px solid rgba(99, 102, 241, 0.2);">
                    <!-- Populated by JS -->
                </div>
            </div>

            <!-- Tabs -->
            <div style="margin-bottom: 20px; border-bottom: 1px solid rgba(0, 212, 255, 0.2);">
                <button onclick="switchSuperClusterTab('creators')" class="sc-tab-button active" data-tab="creators">
                    Creators
                </button>
                <button onclick="switchSuperClusterTab('tokens')" class="sc-tab-button" data-tab="tokens">
                    Tokens
                </button>
            </div>

            <!-- Creators Tab -->
            <div id="scCreatorsTab" class="sc-tab-content" style="display: block; max-height: 400px; overflow-y: auto; margin-bottom: 20px;">
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="border-bottom: 1px solid rgba(0, 212, 255, 0.2);">
                            <th style="text-align: left; padding: 10px; color: #a0a0a0; font-size: 12px;">Creator Address</th>
                            <th style="text-align: left; padding: 10px; color: #a0a0a0; font-size: 12px;">Tokens</th>
                        </tr>
                    </thead>
                    <tbody id="scCreatorsList">
                        <tr><td colspan="2" style="padding: 20px; text-align: center; color: #a0a0a0;">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- Tokens Tab -->
            <div id="scTokensTab" class="sc-tab-content" style="display: none; max-height: 400px; overflow-y: auto; margin-bottom: 20px;">
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="border-bottom: 1px solid rgba(0, 212, 255, 0.2);">
                            <th style="text-align: left; padding: 10px; color: #a0a0a0; font-size: 12px;">Token Mint</th>
                            <th style="text-align: left; padding: 10px; color: #a0a0a0; font-size: 12px;">Creator</th>
                            <th style="text-align: right; padding: 10px; color: #a0a0a0; font-size: 12px;">Risk</th>
                            <th style="text-align: right; padding: 10px; color: #a0a0a0; font-size: 12px;">Peak MC</th>
                        </tr>
                    </thead>
                    <tbody id="scTokensList">
                        <tr><td colspan="4" style="padding: 20px; text-align: center; color: #a0a0a0;">Loading...</td></tr>
                    </tbody>
                </table>
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
                            <th></th>
                            <th onclick="sortBy('network_name')" class="sortable ${sortConfig.column === 'network_name' ? 'sorted-' + sortConfig.direction : ''}">Network</th>
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

                            // Funding checked tag
                            if (token.funding_checked) {
                                columnTags.push('<span class="creator-tag tag-funding-checked" title="Creator funding accounts have been analyzed" style="border-color: #4ade80; color: #4ade80; background-color: rgba(74, 222, 128, 0.15);">Funding</span>');
                            }

                            // CEX/Infrastructure funders - add to Creator Tags column
                            let funderLabels = [];
                            if (creatorData.funders && creatorData.funders.length > 0) {
                                for (let funder of creatorData.funders) {
                                    // Check if this funder is marked as CEX and has an exchange name
                                    if (funder.is_cex && (funder.display_name || funder.cex_exchange)) {
                                        // Use enriched display_name if available, otherwise fallback to cex_exchange
                                        const displayName = funder.display_name || funder.cex_exchange;
                                        // Only add if not already in our list
                                        const already = funderLabels.some(f => f.name === displayName);
                                        if (!already) {
                                            funderLabels.push({
                                                name: displayName,
                                                category: 'cex',
                                                description: `Funded by ${displayName}`
                                            });
                                        }
                                        // Only show first 2 CEX funders to avoid clutter
                                        if (funderLabels.length >= 2) break;
                                    }
                                }
                            }

                            // Add funder labels to columnTags (all cyan)
                            for (let label of funderLabels) {
                                const tagColor = '#00d4ff';
                                const bgColor = 'rgba(0, 212, 255, 0.15)';
                                columnTags.push(`<span class="creator-tag" style="border-color: ${tagColor}; color: ${tagColor}; background-color: ${bgColor};" title="${label.description}">${label.name}</span>`);
                            }

                            // Service tags (uses_axiom, uses_jitotip, uses_meteora, uses_debridge, etc.)
                            // Use creatorData.tags if available (from batch API), otherwise fall back to token.creator_infra_tags
                            const serviceTags = (creatorData.tags && creatorData.tags.length > 0) ? creatorData.tags : (token.creator_infra_tags || []);
                            if (serviceTags && serviceTags.length > 0) {
                                // Deduplicate and filter out address-like tags
                                const seenServiceTags = new Set();
                                for (let serviceTag of serviceTags) {
                                    const tagName = serviceTag.tag.toLowerCase();
                                    // Skip if already seen and skip address-like tags
                                    if (!seenServiceTags.has(tagName) && !serviceTag.tag.match(/^[1-9A-HJ-NP-Za-km-z]{30,}\.?$/)) {
                                        seenServiceTags.add(tagName);

                                        // All service tags use cyan color
                                        const tagColor = '#00d4ff';
                                        const bgColor = 'rgba(0, 212, 255, 0.15)';

                                        // Custom display names for service tags
                                        let displayName = serviceTag.tag.replace('uses_', '');
                                        if (serviceTag.tag === 'uses_jitotip') {
                                            displayName = 'JitoTip (CREATE)';
                                        } else if (serviceTag.tag === 'uses_jitotip_other') {
                                            displayName = 'JitoTip';
                                        }

                                        columnTags.push(`<span class="creator-tag" style="border-color: ${tagColor}; color: ${tagColor}; background-color: ${bgColor};" title="${serviceTag.description}">${displayName}</span>`);
                                    }
                                }
                            }

                            const creatorShort = token.creator ? token.creator.substring(0, 8) + '...' : 'N/A';
                            const creatorTitle = token.creator || 'Unknown';

                            // Get infrastructure tags for creator or funders
                            let infraTags = '';
                            let displayName = null;
                            let displayCategory = null;
                            let displayDescription = '';
                            let creatorIsLabeled = false;

                            // Check if creator itself is infrastructure or CEX - show account name only
                            if (token.creator && window.infraMapping) {
                                if (window.infraMapping.infrastructure && window.infraMapping.infrastructure[token.creator]) {
                                    const info = window.infraMapping.infrastructure[token.creator];
                                    displayName = info.name;
                                    displayCategory = info.category;
                                    displayDescription = info.description;
                                    creatorIsLabeled = true;
                                }
                                if (!displayName && window.infraMapping.cex && window.infraMapping.cex[token.creator]) {
                                    const info = window.infraMapping.cex[token.creator];
                                    displayName = info.name;
                                    displayCategory = info.category;
                                    displayDescription = info.description;
                                    creatorIsLabeled = true;
                                }
                            }

                            // infraTags is now only for showing creator as labeled (CEX/Infrastructure address)
                            // Funder labels are displayed in Creator Tags column instead

                            // Only show infraTags if creator itself is labeled as CEX/Infrastructure
                            if (displayName && displayCategory && !displayName.includes('1111111111') && displayName.length < 50 && !displayName.match(/^[1-9A-HJ-NP-Z]{32,}$/)) {
                                infraTags = `<div class="creator-infra-tags">
                                    <span class="infra-tag infra-${displayCategory}" title="${displayDescription}">${displayName}</span>
                                </div>`;
                            }

                            // Create creator element - ALWAYS show creator address (either label or address)
                            // The creator address is essential for accessing the creator modal
                            let creatorElement;
                            if (creatorIsLabeled && displayName && !displayName.match(/^[1-9A-HJ-NP-Z]{32,}$/)) {
                                // Creator itself is labeled (CEX/Infrastructure) - show label name with clickable link to details
                                creatorElement = `<a href="#" onclick="showCreatorDetails('${token.creator}'); return false;" class="mint-link creator-address-link" title="Creator: ${creatorTitle}">${displayName}</a>`;
                            } else {
                                // No direct label on creator - always show creator address so user can click to modal
                                // This is required even if there are tags or labeled funders
                                creatorElement = `<a href="#" onclick="showCreatorDetails('${token.creator}'); return false;" class="mint-link creator-address-link" title="Creator: ${creatorTitle}">${creatorShort}</a>`;
                            }

                            // Build creator infrastructure tags (deBridge, Meteora, Axiom) with deduplication
                            // NOTE: Service tags are now shown in the "Creator Tags" column, so skip them here to avoid duplication
                            let infraTagsHTML = '';
                            if (token.creator_infra_tags && token.creator_infra_tags.length > 0) {
                                // Skip displaying if the infrastructure service is already shown as a funder badge
                                // (e.g., don't show "uses_debridge" tag if deBridge is already displayed as a funder)
                                const skipIfFunder = displayName && displayName.toLowerCase().includes('debridge');

                                // Deduplicate tags (in case of duplicates in the array)
                                const seenTags = new Set();
                                const uniqueInfraTags = [];

                                for (let infraTag of token.creator_infra_tags) {
                                    const tagName = infraTag.tag.toLowerCase();
                                    if (!seenTags.has(tagName)) {
                                        seenTags.add(tagName);
                                        uniqueInfraTags.push(infraTag);
                                    }
                                }

                                for (let infraTag of uniqueInfraTags) {
                                    // Skip deBridge tag if deBridge is already shown as a funder
                                    if (infraTag.tag.includes('debridge') && skipIfFunder) continue;

                                    // SKIP if this looks like a Solana address (base58 characters, 30+ chars, may have period at end)
                                    // Solana base58 alphabet: [1-9A-HJ-NP-Za-km-z] (excludes 0, O, I, l)
                                    // Examples: "BmxK7bhPsfq4p4FTrXkUSKG26JeW2bT32DqqmkHD8rtJ" or "BmxK7bhPsfq4p4FTrXkUSKG26JeW2bT32DqqmkHD8rtJ."
                                    if (infraTag.tag.match(/^[1-9A-HJ-NP-Za-km-z]{30,}\.?$/)) {
                                        continue;  // Skip addresses, only show service names
                                    }

                                    // SKIP service tags (uses_axiom, uses_jitotip, uses_meteora, uses_debridge)
                                    // They are now displayed in the "Creator Tags" column to avoid duplication
                                    if (infraTag.tag.match(/^uses_/)) {
                                        continue;
                                    }

                                    let tagColor, bgColor;
                                    if (infraTag.tag.includes('debridge')) {
                                        tagColor = '#ff9500';
                                        bgColor = 'rgba(255, 149, 0, 0.15)';
                                    } else if (infraTag.tag.includes('meteora')) {
                                        tagColor = '#00d4ff';
                                        bgColor = 'rgba(0, 212, 255, 0.15)';
                                    } else if (infraTag.tag.includes('axiom')) {
                                        tagColor = '#9333ea';
                                        bgColor = 'rgba(147, 51, 234, 0.15)';
                                    } else if (infraTag.tag.includes('jito')) {
                                        tagColor = '#fbbf24';
                                        bgColor = 'rgba(251, 191, 36, 0.15)';
                                    } else {
                                        tagColor = '#4ade80';
                                        bgColor = 'rgba(74, 222, 128, 0.15)';
                                    }
                                    infraTagsHTML += `<span class="creator-tag" style="border-color: ${tagColor}; color: ${tagColor}; background-color: ${bgColor}; display: inline-block; margin-right: 5px;" title="${infraTag.description}">${infraTag.tag.replace('uses_', '')}</span>`;
                                }
                            }

                            // Append creator infrastructure tags to infraTags
                            if (infraTagsHTML) {
                                infraTags += `<div style="display: flex; flex-wrap: wrap; gap: 5px; margin-top: 3px;">${infraTagsHTML}</div>`;
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
                                    <td class="creator-tags"><div style="display: flex; flex-wrap: wrap; gap: 5px; align-items: center;">${columnTags.join('')}</div></td>
                                    <td class="network-name">
                                        ${token.network_name ? `<a href="#" onclick="switchTab('funding-networks'); showNetworkDetails(${token.network_id}); return false;" class="mint-link" style="font-size: 13px;" title="${token.network_name}">${token.network_name}</a>` : '—'}
                                    </td>
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

        // Toggle funder transfer extraction (incoming/outgoing)
        function toggleFunderExtraction() {
            const btn = document.getElementById('funderExtractionBtn');
            const isEnabled = btn.textContent.includes('ON');

            fetch('/api/funder-extraction-control', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: 'toggle'})
            }).then(resp => resp.json()).then(data => {
                if (data.extraction_enabled) {
                    btn.textContent = 'Funder Extraction ON';
                    btn.style.background = 'rgba(34, 197, 94, 0.2)';
                    btn.style.color = '#4ade80';
                    btn.style.borderColor = 'rgba(34, 197, 94, 0.5)';
                    console.log('✅ Funder transfer extraction ENABLED');
                } else {
                    btn.textContent = 'Funder Extraction OFF';
                    btn.style.background = 'rgba(245, 158, 11, 0.2)';
                    btn.style.color = '#fbbf24';
                    btn.style.borderColor = 'rgba(245, 158, 11, 0.5)';
                    console.log('✅ Funder transfer extraction DISABLED');
                }
            }).catch(e => {
                console.error('❌ Error toggling funder extraction:', e);
                alert('❌ Error toggling funder extraction');
            });
        }

        // Check funder extraction status on page load
        async function checkFunderExtractionStatus() {
            try {
                const resp = await fetch('/api/funder-extraction-control');
                const data = await resp.json();
                const btn = document.getElementById('funderExtractionBtn');

                if (data.extraction_enabled) {
                    btn.textContent = 'Funder Extraction ON';
                    btn.style.background = 'rgba(34, 197, 94, 0.2)';
                    btn.style.color = '#4ade80';
                    btn.style.borderColor = 'rgba(34, 197, 94, 0.5)';
                } else {
                    btn.textContent = 'Funder Extraction OFF';
                    btn.style.background = 'rgba(245, 158, 11, 0.2)';
                    btn.style.color = '#fbbf24';
                    btn.style.borderColor = 'rgba(245, 158, 11, 0.5)';
                }
            } catch (e) {
                console.error('Error checking funder extraction status:', e);
            }
        }

        // Toggle between token table and CEX view
        function toggleFundingNetworkView() {
            const tokensContainer = document.getElementById('tokens-container');
            const fundingNetworkContainer = document.getElementById('funding-network-container');

            if (fundingNetworkContainer.style.display === 'none') {
                // Switch to Funding Network view
                tokensContainer.style.display = 'none';
                fundingNetworkContainer.style.display = 'block';
                loadFundingNetworkData();
            } else {
                // Switch back to token view
                fundingNetworkContainer.style.display = 'none';
                tokensContainer.style.display = 'block';
            }
        }

        // Load and display suspicious funding network coordination
        async function loadFundingNetworkData() {
            try {
                const response = await fetch('/api/funding-network');
                const data = await response.json();

                if (data.error) {
                    document.getElementById('fundingNetworkBody').innerHTML = '<tr><td colspan="5" style="text-align: center; color: #ef4444;">Error: ' + data.error + '</td></tr>';
                    return;
                }

                // Update statistics
                document.getElementById('suspiciousNetworkCount').textContent = (data.networks || []).length;
                document.getElementById('hubAddressCount').textContent = (data.hub_addresses || []).length;
                document.getElementById('totalSolTracked').textContent = ((data.total_sol || 0).toFixed(2)) + ' SOL';

                // Populate shared counterparties
                let counterpartiesHTML = '';
                if (data.shared_counterparties && data.shared_counterparties.length > 0) {
                    counterpartiesHTML += '<div style="margin-bottom: 15px;"><strong style="color: #ff6b6b;">🔗 ' + data.shared_counterparties.length + ' Shared Funding Sources:</strong></div>';
                    for (let addr of data.shared_counterparties.slice(0, 10)) {
                        counterpartiesHTML += '<div style="margin: 8px 0; padding: 8px; background: rgba(239, 68, 68, 0.05); border-radius: 4px;">';
                        counterpartiesHTML += '<span style="font-family: monospace; color: #fca5a5;">' + addr + '</span>';
                        counterpartiesHTML += '</div>';
                    }
                    if (data.shared_counterparties.length > 10) {
                        counterpartiesHTML += '<div style="color: #fca5a5; margin-top: 10px;">... and ' + (data.shared_counterparties.length - 10) + ' more</div>';
                    }
                } else {
                    counterpartiesHTML += '<div style="color: #a0a0a0;">No shared funding sources detected yet. Analyze more funders to build network.</div>';
                }
                document.getElementById('sharedCounterpartiesBody').innerHTML = counterpartiesHTML;

                // Populate network table
                let html = '';
                if (data.networks && data.networks.length > 0) {
                    for (let network of data.networks) {
                        html += '<tr style="border-bottom: 1px solid rgba(239, 68, 68, 0.2);">';
                        html += '<td style="color: #fca5a5; font-family: monospace; font-size: 12px;">' + network.address.substring(0, 20) + '...</td>';
                        html += '<td style="color: #ff6b6b;"><strong>' + network.creator_count + '</strong></td>';
                        html += '<td style="color: #fca5a5;">' + (network.total_sol || 0).toFixed(2) + ' SOL</td>';
                        html += '<td style="color: #fca5a5;">' + (network.linked_funders || 0) + '</td>';
                        html += '<td><span style="background: rgba(239, 68, 68, 0.2); color: #ef4444; padding: 4px 8px; border-radius: 4px; font-size: 12px;">🚨 HIGH</span></td>';
                        html += '</tr>';
                    }
                } else {
                    html += '<tr><td colspan="5" style="text-align: center; color: #a0a0a0; padding: 20px;">No suspicious coordination networks detected. Analyze more funders.</td></tr>';
                }
                document.getElementById('fundingNetworkBody').innerHTML = html;

            } catch (error) {
                console.error('Error loading funding network data:', error);
                document.getElementById('fundingNetworkBody').innerHTML = '<tr><td colspan="5" style="text-align: center; color: #ef4444;">Failed to load data</td></tr>';
            }
        }

        function switchToTokensTab() {
            const tokensContainer = document.getElementById('tokens-container');
            const fundingNetworkContainer = document.getElementById('funding-network-container');
            const tokensTabBtn = document.getElementById('tokensTabBtn');
            const networksTabBtn = document.getElementById('networksTabBtn');

            tokensContainer.style.display = 'block';
            fundingNetworkContainer.style.display = 'none';

            tokensTabBtn.style.background = 'rgba(99, 102, 241, 0.3)';
            tokensTabBtn.style.color = '#818cf8';
            networksTabBtn.style.background = 'rgba(139, 92, 246, 0.2)';
            networksTabBtn.style.color = '#a78bfa';
        }

        function switchToNetworksTab() {
            const tokensContainer = document.getElementById('tokens-container');
            const fundingNetworkContainer = document.getElementById('funding-network-container');
            const tokensTabBtn = document.getElementById('tokensTabBtn');
            const networksTabBtn = document.getElementById('networksTabBtn');

            tokensContainer.style.display = 'none';
            fundingNetworkContainer.style.display = 'block';

            tokensTabBtn.style.background = 'rgba(99, 102, 241, 0.2)';
            tokensTabBtn.style.color = '#6366f1';
            networksTabBtn.style.background = 'rgba(139, 92, 246, 0.3)';
            networksTabBtn.style.color = '#c4b5fd';

            loadFundingNetworks();
        }

        function toggleCEXView() {
            const tokensContainer = document.getElementById('tokens-container');
            const cexContainer = document.getElementById('cex-container');

            if (cexContainer.style.display === 'none') {
                // Switch to CEX view
                tokensContainer.style.display = 'none';
                cexContainer.style.display = 'block';
                loadCEXData();
            } else {
                // Switch back to token view
                cexContainer.style.display = 'none';
                tokensContainer.style.display = 'block';
            }
        }

        // Load CEX data and populate the view
        async function loadCEXData() {
            try {
                const response = await fetch('/api/cex-funders');
                const data = await response.json();

                if (data.error) {
                    document.getElementById('cexExchangesContainer').innerHTML = '<p style="color: #ef4444;">Error loading CEX data: ' + data.error + '</p>';
                    return;
                }

                // Populate exchanges grid
                const exchangesContainer = document.getElementById('cexExchangesContainer');
                if (data.exchanges && data.exchanges.length > 0) {
                    exchangesContainer.innerHTML = data.exchanges.map(ex => `
                        <div class="cex-exchange-card">
                            <h4>🏛️ ${ex.cex_exchange}</h4>
                            <div class="stat">
                                <span class="stat-label">Creators Funded:</span>
                                <span class="stat-value">${ex.creator_count}</span>
                            </div>
                            <div class="stat">
                                <span class="stat-label">Wallets:</span>
                                <span class="stat-value">${ex.funder_count}</span>
                            </div>
                            <div class="stat">
                                <span class="stat-label">Total SOL:</span>
                                <span class="stat-value">${(ex.total_sol || 0).toFixed(2)}</span>
                            </div>
                        </div>
                    `).join('');
                } else {
                    exchangesContainer.innerHTML = '<p style="color: #a0a0a0;">No CEX funders found</p>';
                }

                // Populate top CEX funders table
                const cexFundersBody = document.getElementById('topCexFundersBody');
                if (data.top_cex_funders && data.top_cex_funders.length > 0) {
                    cexFundersBody.innerHTML = data.top_cex_funders.map(funder => {
                        // Use enriched display_name from API, fallback to database fields
                        const displayName = funder.display_name || `${funder.cex_exchange || 'Unknown'} ${funder.cex_type || 'Wallet'}`;
                        return `
                            <tr>
                                <td style="font-family: monospace; font-size: 12px;" title="${funder.funder_address}">${funder.funder_address.substring(0, 16)}...</td>
                                <td><span class="cex-exchange-name">${displayName}</span></td>
                                <td>${funder.creators_funded}</td>
                                <td style="text-align: right; color: #4ade80; font-weight: 600;">${(funder.total_sol || 0).toFixed(2)}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    cexFundersBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #a0a0a0;">No top CEX funders found</td></tr>';
                }

                // Populate CEX-funded creators table
                const creatorsBody = document.getElementById('cexFundedCreatorsBody');
                if (data.cex_funded_creators && data.cex_funded_creators.length > 0) {
                    creatorsBody.innerHTML = data.cex_funded_creators.map(creator => `
                        <tr>
                            <td style="font-family: monospace; font-size: 12px;" title="${creator.creator_address}">${creator.creator_address.substring(0, 16)}...</td>
                            <td style="text-align: center; color: #00d4ff; font-weight: 600;">${creator.exchanges_funding}</td>
                            <td style="text-align: right; color: #4ade80; font-weight: 600;">${(creator.total_cex_funding || 0).toFixed(2)}</td>
                            <td>
                                <a href="#" onclick="showCreatorDetails('${creator.creator_address}'); toggleCEXView(); return false;" style="color: #00d4ff; text-decoration: none;">View Creator →</a>
                            </td>
                        </tr>
                    `).join('');
                } else {
                    creatorsBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #a0a0a0;">No CEX-funded creators found</td></tr>';
                }

            } catch (error) {
                console.error('Error loading CEX data:', error);
                document.getElementById('cexExchangesContainer').innerHTML = '<p style="color: #ef4444;">Error loading CEX data</p>';
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
        checkFunderExtractionStatus();
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

            // Display creator address with domain if available
            let creatorDisplay = creatorAddress;
            // Will be updated after API response

            try {
                const response = await fetch(`/api/creator-details/${creatorAddress}`);
                const data = await response.json();

                if (data.error) {
                    alert('Creator details not found');
                    return;
                }

                // Display creator address with domain tag
                let creatorDisplay = creatorAddress;
                if (data.creator_address_tags && data.creator_address_tags.domain) {
                    const domains = data.creator_address_tags.domain;
                    creatorDisplay = `${creatorAddress.substring(0, 14)}... <span class="domain-tag" style="font-size: 11px; margin-left: 8px;">🌐 ${domains[0]}</span>`;
                }
                document.getElementById('modalCreator').innerHTML = creatorDisplay;

                // Populate creator stats
                document.getElementById('creatorTotalTokens').textContent = data.tokens.length;
                document.getElementById('creatorTotalFunding').textContent = (data.funding.total_sol !== null ? data.funding.total_sol.toFixed(2) : '0.00') + ' SOL';

                // Show CEX funders if any
                let fundersText = data.funding.total_funders || '0';
                if (data.funding.cex_funders > 0) {
                    fundersText = '🏛️ ' + data.funding.cex_funders + ' CEX + ' + ((data.funding.total_funders || 0) - data.funding.cex_funders) + ' other';
                }
                document.getElementById('creatorTotalFunders').textContent = fundersText;

                document.getElementById('creatorNetworkSize').textContent = (data.cluster.total_wallets || 0) + ' wallets';

                // Display creator tags (remove 'uses_' prefix for cleaner display, deduplicate, filter addresses)
                const tagsContainer = document.getElementById('creatorTagsContainer');
                if (data.tags && data.tags.length > 0) {
                    // Deduplicate tags and strip 'uses_' prefix for display
                    const seenTags = new Set();
                    const uniqueTags = [];

                    for (const t of data.tags) {
                        // SKIP if this looks like a Solana address (base58 characters, 30+ chars, may have period)
                        // Matches: standard addresses like "ABC123...", or "ABC123...." (with period for domain-like format)
                        if (t.tag.match(/^[1-9A-HJ-NP-Za-km-z]{30,}\.?$/)) {
                            continue;  // Skip addresses, only show service names
                        }

                        // Also skip if tag contains "interacted with" - these are auto-generated account tags
                        if (t.description && t.description.toLowerCase().includes('interacted with')) {
                            continue;
                        }

                        const displayTag = t.tag.replace('uses_', '').toLowerCase();
                        if (!seenTags.has(displayTag)) {
                            seenTags.add(displayTag);
                            uniqueTags.push({
                                display: displayTag,
                                original: t.tag,
                                description: t.description,
                                amount_sol: t.amount_sol
                            });
                        }
                    }

                    const tagsHTML = uniqueTags.map(t => {
                        // For jitotip, display amount prominently
                        let tagContent = t.display;
                        if (t.original === 'uses_jitotip' && t.amount_sol) {
                            tagContent = `${t.display} (${t.amount_sol.toFixed(6)} SOL)`;
                        } else if ((t.original === 'uses_meteora' || t.original === 'uses_axiom' || t.original === 'uses_debridge') && t.amount_sol) {
                            tagContent = `${t.display} (${t.amount_sol.toFixed(4)} SOL)`;
                        }
                        return `
                            <div class="creator-tag" title="${t.description}">
                                <span class="tag-label">${tagContent}</span>
                            </div>
                        `;
                    }).join('');
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

                // Separate CEX and non-CEX funders
                // Use enriched data from API: is_cex from infra_mapping lookup, not database flag
                const cexFunders = (data.top_funders || []).filter(f => f.is_cex);
                const nonCexFunders = (data.top_funders || []).filter(f => !f.is_cex);

                // Show/hide CEX funders section
                const cexSection = document.getElementById('cexFundersSection');
                if (cexFunders.length > 0) {
                    cexSection.style.display = 'block';
                    const cexBody = document.getElementById('cexFundersBody');
                    cexBody.innerHTML = cexFunders.map(funder => {
                        const amountStr = funder.amount_sol < 0.01
                            ? funder.amount_sol.toFixed(6)
                            : funder.amount_sol.toFixed(2);
                        // Use enriched display_name from infra_mapping, fall back to database fields
                        const displayName = funder.display_name || `${funder.cex_exchange || 'Unknown'} ${funder.cex_type || 'Hot Wallet'}`;
                        return `
                            <tr>
                                <td><span class="cex-exchange-name">${displayName}</span></td>
                                <td title="${funder.funder_address}" style="font-family: monospace;">${funder.funder_address.substring(0, 16)}...</td>
                                <td>${amountStr} SOL</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    cexSection.style.display = 'none';
                }

                // Check for multi-creator funders (coordination risk)
                try {
                    const multiCreatorResponse = await fetch('/api/multi-creator-funders');
                    const multiCreatorData = await multiCreatorResponse.json();

                    if (multiCreatorData.multi_creator_funders && multiCreatorData.multi_creator_funders.length > 0) {
                        // Check if any of this creator's funders are in the multi-creator list
                        const thisCreatorFunders = new Set(data.top_funders.map(f => f.funder_address));
                        const matchingMultiCreatorFunders = multiCreatorData.multi_creator_funders.filter(mf =>
                            thisCreatorFunders.has(mf.funder_address)
                        );

                        if (matchingMultiCreatorFunders.length > 0) {
                            const multiSection = document.getElementById('multiCreatorFundersSection');
                            // Filter out INFRA and CEX accounts - only show suspicious multi-creator funders
                            const suspiciousMatchers = matchingMultiCreatorFunders.filter(f => !f.is_infrastructure && !f.is_cex_account);

                            if (suspiciousMatchers.length > 0) {
                                multiSection.style.display = 'block';
                            } else {
                                multiSection.style.display = 'none';
                            }

                            const multiBody = document.getElementById('tokenMetricsMultiCreatorFundersBody');

                            multiBody.innerHTML = suspiciousMatchers.map(funder => {
                                const firstFundingDate = funder.first_funding_at ? new Date(funder.first_funding_at).toLocaleDateString() : 'N/A';
                                const lastFundingDate = funder.last_funding_at ? new Date(funder.last_funding_at).toLocaleDateString() : 'N/A';
                                const totalSol = funder.total_sol_sent ? funder.total_sol_sent.toFixed(2) : '0.00';

                                return `
                                    <tr>
                                        <td title="${funder.funder_address}" style="font-family: monospace;">
                                            <a href="https://solscan.io/address/${funder.funder_address}" target="_blank" style="color: #ef4444; text-decoration: none; cursor: pointer;">
                                                ${funder.funder_address.substring(0, 16)}...
                                            </a>
                                        </td>
                                        <td><strong>${funder.creator_count}</strong></td>
                                        <td>${totalSol} SOL</td>
                                        <td>${firstFundingDate}</td>
                                        <td>${lastFundingDate}</td>
                                    </tr>
                                `;
                            }).join('');
                        } else {
                            document.getElementById('multiCreatorFundersSection').style.display = 'none';
                        }
                    } else {
                        document.getElementById('multiCreatorFundersSection').style.display = 'none';
                    }
                } catch (error) {
                    console.error('Error loading multi-creator funders:', error);
                    document.getElementById('multiCreatorFundersSection').style.display = 'none';
                }

                // Show/hide other labeled funders section
                const otherSection = document.getElementById('otherFundersSection');
                const labeledNonCexFunders = nonCexFunders.filter(f => f.display_name);
                if (labeledNonCexFunders.length > 0) {
                    otherSection.style.display = 'block';
                    const otherBody = document.getElementById('otherFundersBody');
                    otherBody.innerHTML = labeledNonCexFunders.map(funder => {
                        const amountStr = funder.amount_sol < 0.01
                            ? funder.amount_sol.toFixed(6)
                            : funder.amount_sol.toFixed(2);
                        return `
                            <tr>
                                <td><span style="color: #4ade80; font-weight: 600;">${funder.display_name}</span></td>
                                <td>${funder.category || 'unknown'}</td>
                                <td>${amountStr} SOL</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    otherSection.style.display = 'none';
                }

                // Populate all funders table (complete list)
                const allFundersBody = document.getElementById('allFundersBody');
                if (data.top_funders && data.top_funders.length > 0) {
                    allFundersBody.innerHTML = data.top_funders.map(funder => {
                        const amountStr = funder.amount_sol < 0.01
                            ? funder.amount_sol.toFixed(6)
                            : funder.amount_sol.toFixed(2);

                        let funderType = 'Wallet';
                        if (funder.is_cex) {
                            // Use display_name from enriched data if available, otherwise fallback
                            if (funder.display_name) {
                                funderType = funder.display_name;
                            } else {
                                funderType = `${funder.cex_exchange || 'CEX'} ${funder.cex_type ? `(${funder.cex_type})` : ''}`.trim();
                            }
                        } else if (funder.display_name) {
                            funderType = funder.display_name;
                        }

                        return `
                            <tr>
                                <td title="${funder.funder_address}" style="font-family: monospace; color: #00d4ff;">${funder.funder_address.substring(0, 16)}...</td>
                                <td>${amountStr} SOL</td>
                                <td>${funderType}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    allFundersBody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: #a0a0a0;">No funders found</td></tr>';
                }

                // Load tokens funded (if this address is a funder)
                try {
                    const fundedResponse = await fetch(`/api/funder-tokens/${creatorAddress}`);
                    const fundedData = await fundedResponse.json();

                    if (fundedData.tokens_funded && fundedData.tokens_funded.length > 0) {
                        document.getElementById('tokensFundedSection').style.display = 'block';
                        const fundedBody = document.getElementById('tokensFundedBody');

                        fundedBody.innerHTML = fundedData.tokens_funded.map(token => {
                            return `
                                <tr>
                                    <td><a href="#" onclick="showTokenMetrics('${token.mint}'); return false;" class="mint-link" title="${token.mint}">${token.mint.substring(0, 16)}...</a></td>
                                    <td style="font-family: monospace; font-size: 11px;">
                                        <a href="#" onclick="showCreatorDetails('${token.creator_address}'); return false;" title="${token.creator_address}">${token.creator_address.substring(0, 16)}...</a>
                                    </td>
                                    <td>${token.funding_amount_sol.toFixed(2)} SOL</td>
                                    <td>${formatDateISO(token.created_at)}</td>
                                    <td><span class="risk-score risk-${token.risk_level ? token.risk_level.toLowerCase() : 'medium'}">${token.risk_level || 'N/A'}</span></td>
                                </tr>
                            `;
                        }).join('');
                    } else {
                        document.getElementById('tokensFundedSection').style.display = 'none';
                    }
                } catch (error) {
                    console.error('Error loading funded tokens:', error);
                    document.getElementById('tokensFundedSection').style.display = 'none';
                }

                // Populate top recipients table (where creator sent SOL)
                const recipientsBody = document.getElementById('topRecipientsBody');
                if (data.top_recipients && data.top_recipients.length > 0) {
                    // Show ALL recipients (not just labeled ones)
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

                        // Display label name if available, otherwise use address
                        const displayLabel = recipient.display_name || recipient.recipient_address.substring(0, 16) + '...';

                        return `
                            <tr class="${recipient.is_network_coordinator ? 'row-network-coordinator' : recipient.shared_with_creators ? 'row-shared-recipient' : ''}">
                                <td title="${recipient.recipient_address}" style="font-family: monospace; font-size: 12px;">
                                    ${displayLabel}
                                    ${networkIndicator ? `<div style="margin-top: 3px; font-size: 10px; color: #a0a0a0;">${networkTooltip}</div>` : ''}
                                </td>
                                <td>${recipientAmountStr} SOL</td>
                                <td>${networkIndicator || (recipient.is_infrastructure ? recipient.category : 'Wallet')}</td>
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

                // Fetch and populate Jito tips history
                try {
                    const historyResponse = await fetch(`/api/creator-service-history/${creatorAddress}`);
                    const historyData = await historyResponse.json();

                    const jitotipsSection = document.getElementById('jitotipsSection');
                    const jitotipsBody = document.getElementById('jitotipsBody');

                    if (historyData.history && historyData.history.length > 0) {
                        // Filter jitotip records (both CREATE and other txs)
                        const jitotips = historyData.history.filter(h => h.tag === 'uses_jitotip' || h.tag === 'uses_jitotip_other');

                        if (jitotips.length > 0) {
                            jitotipsSection.style.display = 'block';
                            jitotipsBody.innerHTML = jitotips.map(tip => {
                                const mintDisplay = tip.mint ? tip.mint.substring(0, 16) + '...' : 'N/A';
                                const mintTitle = tip.mint ? tip.mint : '';
                                const dateStr = tip.created_at ? new Date(tip.created_at).toLocaleDateString() : 'N/A';
                                const percentageStr = tip.tip_percentage ? tip.tip_percentage.toFixed(1) + '%' : 'N/A';
                                const txSig = tip.tx_signature || '';

                                // Show actual transaction type with styling and Solscan link
                                const txType = tip.tx_type || (tip.tag === 'uses_jitotip' ? 'Create' : 'Unknown');
                                const isCreate = tip.tag === 'uses_jitotip' || txType.toLowerCase() === 'create';
                                const typeColor = isCreate ? '#4ade80' : '#fbbf24';

                                let typeIndicator;
                                if (txSig) {
                                    typeIndicator = `<a href="https://solscan.io/tx/${txSig}" target="_blank" style="color: ${typeColor}; font-weight: 600; font-size: 10px; text-decoration: none; cursor: pointer;" title="View on Solscan: ${txSig}">${txType}</a>`;
                                } else {
                                    typeIndicator = `<span style="color: ${typeColor}; font-weight: 600; font-size: 10px;">${txType}</span>`;
                                }

                                return `
                                    <tr>
                                        <td title="${mintTitle}" style="font-family: monospace;">${mintDisplay}</td>
                                        <td>${tip.amount_sol ? tip.amount_sol.toFixed(6) : 'N/A'} SOL</td>
                                        <td>${percentageStr}</td>
                                        <td>${typeIndicator}</td>
                                        <td>${dateStr}</td>
                                    </tr>
                                `;
                            }).join('');
                        } else {
                            jitotipsSection.style.display = 'none';
                        }
                    } else {
                        jitotipsSection.style.display = 'none';
                    }
                } catch (error) {
                    console.error('Error loading Jito tips history:', error);
                    document.getElementById('jitotipsSection').style.display = 'none';
                }

                // Display super-cluster membership if available
                if (data.super_clusters && data.super_clusters.length > 0) {
                    const superClusterBanner = document.createElement('div');
                    superClusterBanner.style.cssText = 'background: rgba(239, 68, 68, 0.1); border-left: 4px solid #ef4444; padding: 15px; margin-bottom: 15px; border-radius: 4px; margin-top: 20px;';

                    let bannerHTML = '<div style="color: #ef4444; font-weight: bold; margin-bottom: 10px; font-size: 14px;">⚠️ COORDINATED NETWORK DETECTED</div>';
                    bannerHTML += '<div style="font-size: 12px; color: #e0e0e0;">';
                    bannerHTML += 'This creator belongs to funding coordination networks:';
                    bannerHTML += '<div style="margin-top: 8px;">';

                    for (const sc of data.super_clusters) {
                        let riskColor = '#6b7280';
                        let riskEmoji = '';

                        if (sc.risk_level === 'CRITICAL') {
                            riskColor = '#ef4444';
                            riskEmoji = '🚨';
                        } else if (sc.risk_level === 'HIGH') {
                            riskColor = '#f97316';
                            riskEmoji = '⚠️';
                        } else if (sc.risk_level === 'MEDIUM') {
                            riskColor = '#fbbf24';
                            riskEmoji = '⚡';
                        }

                        bannerHTML += `
                            <div style="margin: 6px 0;">
                                <a href="#" onclick="showSuperCluster('${sc.super_cluster_id}'); return false;"
                                   style="color: ${riskColor}; text-decoration: none; font-weight: 600; cursor: pointer;">
                                    ${riskEmoji} ${sc.super_cluster_id} (${sc.risk_level})
                                </a>
                                <span style="color: #a0a0a0; font-size: 11px; margin-left: 8px;">
                                    ${sc.creator_count} creators, ${sc.network_count} networks
                                </span>
                            </div>
                        `;
                    }

                    bannerHTML += '</div></div>';

                    // Inject the banner into the modal - look for the tags container
                    const tagsContainer = document.getElementById('creatorTagsContainer');
                    if (tagsContainer) {
                        superClusterBanner.innerHTML = bannerHTML;
                        tagsContainer.parentElement.insertBefore(superClusterBanner, tagsContainer.nextElementSibling);
                    }
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

        async function showMultiCreatorFunders() {
            // Cancel ongoing price fetches to free up bandwidth for modal
            priceLoadController.abort();
            priceLoadController = new AbortController();

            const modal = document.getElementById('multiCreatorFundersModal');

            try {
                const response = await fetch('/api/multi-creator-funders');
                const data = await response.json();

                if (data.error) {
                    alert('Failed to load multi-creator funder analysis');
                    return;
                }

                // Populate statistics
                const suspiciousCount = data.suspicious_only ? data.suspicious_only.length : 0;
                const safeCount = data.statistics.funding_multiple_creators - suspiciousCount;

                document.getElementById('suspiciousFundersCount').textContent = suspiciousCount;
                document.getElementById('suspiciousFundersCount').style.color = suspiciousCount > 0 ? '#ef4444' : '#4ade80';

                document.getElementById('safeFundersCount').textContent = safeCount;
                document.getElementById('safeFundersCount').style.color = '#4ade80';

                document.getElementById('totalFundersCount').textContent = data.statistics.total_funders;

                // Populate funders table - filter out INFRA/CEX accounts
                const fundersBody = document.getElementById('multiCreatorFundersBody');
                if (data.multi_creator_funders && data.multi_creator_funders.length > 0) {
                    // Filter out INFRA and CEX accounts - they are safe
                    const suspiciousFunders = data.multi_creator_funders.filter(f => !f.is_infrastructure && !f.is_cex_account);

                    if (suspiciousFunders.length > 0) {
                        fundersBody.innerHTML = suspiciousFunders.map((funder, idx) => {
                            const startDate = new Date(funder.first_funding_at).toLocaleDateString();
                            const endDate = new Date(funder.last_funding_at).toLocaleDateString();
                            const period = startDate === endDate ? startDate : `${startDate} - ${endDate}`;

                            // Build network display - show all networks this funder belongs to
                            let networkDisplay = '';
                            if (funder.networks && funder.networks.length > 0) {
                                const networkNames = funder.networks.map(n => {
                                    const typeBadge = n.network_type === 'single_funder' ? ' 🎯' : '';
                                    return n.network_name + typeBadge;
                                });
                                networkDisplay = networkNames.join(', ');
                            } else {
                                networkDisplay = '—';
                            }

                            return `
                                <tr style="cursor: pointer;" onclick="showFunderDetails('${funder.funder_address}')" title="Click to view funder details">
                                    <td style="font-family: monospace; font-size: 12px; color: #ef4444;">
                                        ${funder.funder_address}
                                    </td>
                                    <td style="color: #00d4ff; font-weight: 500; font-size: 12px; white-space: nowrap;">${networkDisplay}</td>
                                    <td><strong style="color: #ef4444;">${funder.creator_count}</strong></td>
                                    <td>${funder.total_sol_sent.toFixed(2)} SOL</td>
                                    <td>${funder.funding_record_count}</td>
                                    <td style="font-size: 11px;">${period}</td>
                                    <td onclick="event.stopPropagation();">
                                        <button onclick="analyzeFunderTransfers('${funder.funder_address}')" style="padding: 4px 8px; font-size: 11px; background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.5); border-radius: 3px; cursor: pointer; white-space: nowrap;">Analyze</button>
                                    </td>
                                </tr>
                            `;
                        }).join('');
                    } else {
                        fundersBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #4ade80;">✅ All multi-creator funders are known INFRA/CEX accounts (safe)</td></tr>';
                    }
                } else {
                    fundersBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #4ade80;">✅ No coordinated funders detected</td></tr>';
                }

                // Show modal
                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading multi-creator funders:', error);
                alert('Failed to load multi-creator funder analysis');
            }
        }

        function closeMultiCreatorFunders() {
            document.getElementById('multiCreatorFundersModal').style.display = 'none';
        }

        // Analyze funder transfers in background and show status
        async function analyzeFunderTransfers(funderAddress) {
            const btn = event.target;
            const originalText = btn.textContent;
            btn.textContent = 'Analyzing...';
            btn.disabled = true;
            btn.style.opacity = '0.5';

            try {
                const response = await fetch('/api/analyze-funder-transfers', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({funder_address: funderAddress})
                });

                const data = await response.json();

                if (data.status === 'queued') {
                    btn.textContent = 'Queued ✓';
                    btn.style.background = 'rgba(245, 158, 11, 0.2)';
                    btn.style.color = '#fbbf24';
                    btn.style.borderColor = 'rgba(245, 158, 11, 0.5)';

                    console.log(`✅ Analysis queued for: ${funderAddress}`);

                    // Poll for results
                    let pollCount = 0;
                    const pollInterval = setInterval(async () => {
                        pollCount++;

                        // Stop polling after 30 seconds
                        if (pollCount > 30) {
                            clearInterval(pollInterval);
                            btn.textContent = originalText;
                            btn.disabled = false;
                            btn.style.opacity = '1';
                            btn.style.background = 'rgba(34, 197, 94, 0.2)';
                            btn.style.color = '#4ade80';
                            btn.style.borderColor = 'rgba(34, 197, 94, 0.5)';
                            alert('⏱️ Analysis timed out. Try again later.');
                            return;
                        }

                        try {
                            const statusResponse = await fetch(`/api/funder-analysis-status/${funderAddress}`);
                            const statusData = await statusResponse.json();

                            if (statusData.status === 'completed') {
                                clearInterval(pollInterval);

                                // Show results (handle both naming conventions)
                                const incoming = statusData.result.incoming_found || statusData.result.incoming_count || 0;
                                const outgoing = statusData.result.outgoing_found || statusData.result.outgoing_count || 0;
                                const totalSol = (statusData.result.total_sol || 0).toFixed(2);

                                btn.textContent = `Done: ${incoming} IN / ${outgoing} OUT`;
                                btn.style.background = 'rgba(34, 197, 94, 0.3)';
                                btn.style.color = '#4ade80';
                                btn.style.borderColor = 'rgba(34, 197, 94, 0.7)';

                                alert(`✅ Analysis Complete\n\nIncoming: ${incoming}\nOutgoing: ${outgoing}\nTotal SOL: ${totalSol}`);

                                // Reset button after delay
                                setTimeout(() => {
                                    btn.textContent = originalText;
                                    btn.disabled = false;
                                    btn.style.opacity = '1';
                                    btn.style.background = 'rgba(34, 197, 94, 0.2)';
                                    btn.style.color = '#4ade80';
                                    btn.style.borderColor = 'rgba(34, 197, 94, 0.5)';
                                }, 3000);
                            }
                        } catch (e) {
                            console.error('Error checking analysis status:', e);
                        }
                    }, 1000);  // Poll every 1 second
                } else if (data.status === 'completed') {
                    const incoming = data.result.incoming_found || data.result.incoming_count || 0;
                    const outgoing = data.result.outgoing_found || data.result.outgoing_count || 0;
                    btn.textContent = `Done: ${incoming} IN / ${outgoing} OUT`;
                    btn.style.background = 'rgba(34, 197, 94, 0.3)';
                    btn.style.color = '#4ade80';
                    btn.style.borderColor = 'rgba(34, 197, 94, 0.7)';

                    // Show results
                    const msg = `✅ Analysis Complete\n\nIncoming: ${incoming}\nOutgoing: ${outgoing}\nTotal SOL: ${(data.result.total_sol || 0).toFixed(4)}`;
                    alert(msg);
                } else {
                    alert(`❌ Error: ${data.error || 'Unknown error'}`);
                    btn.textContent = originalText;
                    btn.disabled = false;
                    btn.style.opacity = '1';
                }
            } catch (error) {
                console.error('Error analyzing funder:', error);
                alert(`❌ Error analyzing funder: ${error.message}`);
                btn.textContent = originalText;
                btn.disabled = false;
                btn.style.opacity = '1';
            }
        }

        function closeCreatorDetails() {
            document.getElementById('creatorModal').style.display = 'none';
        }

        // Close modal when clicking outside
        window.onclick = function(event) {
            const metricsModal = document.getElementById('metricsModal');
            const creatorModal = document.getElementById('creatorModal');
            const txViewerModal = document.getElementById('txViewerModal');
            const multiCreatorFundersModal = document.getElementById('multiCreatorFundersModal');
            const validationModal = document.getElementById('validationModal');
            const fundingNetwork3TierModal = document.getElementById('fundingNetwork3TierModal');
            const coordinatedFunderAnalysisModal = document.getElementById('coordinatedFunderAnalysisModal');
            const funderDetailsModal = document.getElementById('funderDetailsModal');

            if (event.target === metricsModal) {
                metricsModal.style.display = 'none';
            }
            if (event.target === creatorModal) {
                creatorModal.style.display = 'none';
            }
            if (event.target === multiCreatorFundersModal) {
                multiCreatorFundersModal.style.display = 'none';
            }
            if (event.target === txViewerModal) {
                txViewerModal.style.display = 'none';
            }
            if (event.target === validationModal) {
                validationModal.style.display = 'none';
            }
            if (event.target === fundingNetwork3TierModal) {
                fundingNetwork3TierModal.style.display = 'none';
            }
            if (event.target === coordinatedFunderAnalysisModal) {
                coordinatedFunderAnalysisModal.style.display = 'none';
            }
            if (event.target === funderDetailsModal) {
                funderDetailsModal.style.display = 'none';
            }
        }

        // Close modal when pressing Escape
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape') {
                closeTokenMetrics();
                closeCreatorDetails();
                closeMultiCreatorFunders();
                closeCoordinatedFunderAnalysis();
                closeTxViewer();
                closeValidationModal();
                closeFundingNetwork3Tier();
                closeFunderDetails();
            }
        });

        // ===== TRANSACTION VALIDATION FUNCTIONS =====

        function openValidationModal() {
            document.getElementById('validationModal').style.display = 'block';
            document.getElementById('validationInput').focus();
            document.getElementById('validationInput').value = '';
            document.getElementById('validationResults').style.display = 'none';
        }

        function closeValidationModal() {
            document.getElementById('validationModal').style.display = 'none';
        }

        // 3-Tier Funding Network Functions
        function promptFundingNetwork3Tier() {
            const creatorAddress = prompt('Enter creator address to view funding network: ' + String.fromCharCode(10) + String.fromCharCode(10) + 'Example: HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp');
            if (creatorAddress && creatorAddress.trim().length > 0) {
                showFundingNetwork3Tier(creatorAddress.trim());
            }
        }

        async function showFundingNetwork3Tier(creatorAddress) {
            const modal = document.getElementById('fundingNetwork3TierModal');

            try {
                // Check extraction status first
                const statusResponse = await fetch(`/api/creator-funder-extraction-status/${creatorAddress}`);
                const statusData = await statusResponse.json();

                const response = await fetch(`/api/funding-network-3tier/${creatorAddress}`);
                const data = await response.json();

                if (data.error) {
                    document.getElementById('fn3tNetworkBody').innerHTML = '<div style="color: #ef4444;">Error loading network</div>';
                    return;
                }

                // Add extraction status indicator
                let statusIndicator = '';
                if (statusData.is_complete) {
                    statusIndicator = '<div style="color: #4ade80; font-weight: bold; margin-bottom: 15px; font-size: 13px;">✅ Funding complete</div>';
                } else if (statusData.status === 'pending') {
                    statusIndicator = '<div style="color: #fbbf24; font-weight: bold; margin-bottom: 15px; font-size: 13px;">⏳ Extraction in progress...</div>';
                }

                // Build 3-tier network visualization - concise version
                let networkHTML = '<div style="font-family: monospace; font-size: 12px; line-height: 2.2;">';

                data.network_tiers.forEach((tier, tierIdx) => {
                    const funderAddr = tier.funder_address;
                    const funderType = tier.funder_type || 'unknown';
                    const funderLabel = tier.funder_label;
                    const totalToCreator = tier.total_to_creator.toFixed(2);
                    const senderCount = tier.sender_count || tier.senders.length;

                    // Funder type styling
                    let funderColor = '#4ade80';  // Default green for regular
                    let funderTypeLabel = '';
                    if (funderType === 'cex') {
                        funderColor = '#ef4444';  // Red for CEX
                        funderTypeLabel = ' [CEX]';
                    } else if (funderType === 'infra') {
                        funderColor = '#f97316';  // Orange for INFRA
                        funderTypeLabel = ' [INFRA]';
                    }

                    networkHTML += `<div style="color: ${funderColor}; margin-bottom: 12px; font-family: monospace; font-size: 11px; word-break: break-all;">`;
                    networkHTML += `Funder: ${funderAddr}${funderTypeLabel}</div>`;

                    if (senderCount > 0) {
                        const knownCount = tier.known_sender_count || 0;
                        const unknownCount = senderCount - knownCount;
                        let senderSummary = `← ${senderCount} senders → ${totalToCreator} SOL`;
                        if (knownCount > 0) {
                            // Count risky vs trusted identified accounts
                            const riskyCount = tier.senders.filter(s => s.risk_level === 'high').length;
                            if (riskyCount > 0) {
                                senderSummary += ` <span style="color: #ef4444;">(${riskyCount} risky ⚠️)</span>`;
                            }
                            const trustedCount = tier.senders.filter(s => s.risk_level === 'neutral' || s.risk_level === 'low').length;
                            if (trustedCount > 0) {
                                senderSummary += ` <span style="color: #4ade80;">(${trustedCount} trusted ✓)</span>`;
                            }
                        }
                        networkHTML += `<div style="color: #fbbf24; margin-left: 20px; margin-bottom: 6px;">${senderSummary}</div>`;

                        // Always show known senders (prioritized), then unknowns up to 5 total
                        if (tier.senders.length > 0) {
                            // Separate known and unknown senders
                            const knownSenders = tier.senders.filter(s => s.is_known);
                            const unknownSenders = tier.senders.filter(s => !s.is_known);

                            // Show all known senders first, then unknown senders up to fill remaining space
                            const displayCount = Math.min(5, tier.senders.length);
                            const sendersToShow = knownSenders.concat(unknownSenders).slice(0, displayCount);

                            sendersToShow.forEach((sender) => {
                                const label = sender.label;
                                const riskLevel = sender.risk_level || 'unknown';

                                // Color code by risk level
                                let senderColor = '#fbbf24';  // unknown (yellow) - neutral
                                let badge = '';

                                if (riskLevel === 'high') {
                                    senderColor = '#ef4444';  // RED - suspicious (CEX hot wallets, risky accounts)
                                    badge = ' ⚠️';  // Warning emoji
                                } else if (riskLevel === 'neutral' || riskLevel === 'low') {
                                    senderColor = '#4ade80';  // GREEN - trusted infrastructure (Axiom, safe accounts)
                                    badge = ' ✓';  // Checkmark for trusted
                                } else if (riskLevel === 'medium') {
                                    senderColor = '#fbbf24';  // YELLOW - moderate risk
                                    badge = '';
                                }

                                const senderAmount = sender.amount_to_funder.toFixed(2);
                                const labelText = label ? ` [${label}]` : '';
                                networkHTML += `<div style="color: ${senderColor}; margin-left: 40px; font-size: 11px; font-family: monospace; word-break: break-all;">• ${sender.sender_address}${labelText}${badge} → ${senderAmount} SOL</div>`;
                            });

                            // Show remaining count if there are more
                            if (tier.senders.length > displayCount) {
                                networkHTML += `<div style="color: #a0a0a0; margin-left: 40px; font-size: 11px;">... and ${tier.senders.length - displayCount} more senders</div>`;
                            }
                        }
                    } else {
                        networkHTML += `<div style="color: #a0a0a0; margin-left: 20px; margin-bottom: 6px;">→ ${totalToCreator} SOL (no tracked sources)</div>`;
                    }

                    networkHTML += `</div>`;
                });

                networkHTML += '</div>';

                // If no funders with senders were found, show helpful message
                if (networkHTML === '<div style="font-family: monospace; font-size: 12px; line-height: 2.2;"></div>') {
                    if (data.total_funders > 0) {
                        networkHTML = `<div style="color: #fbbf24; padding: 20px; text-align: center;">
                            This creator has ${data.total_funders} funder(s) but no tracked pre-migration senders.<br>
                            <span style="color: #a0a0a0; font-size: 11px;">Funding source data extraction pending.</span>
                        </div>`;
                    } else {
                        networkHTML = '<div style="color: #a0a0a0; padding: 20px; text-align: center;">No funding data available for this creator.</div>';
                    }
                }

                // Prepend status indicator to the network HTML
                document.getElementById('fn3tNetworkBody').innerHTML = statusIndicator + networkHTML;
                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading 3-tier network:', error);
                document.getElementById('fn3tNetworkBody').innerHTML = '<div style="color: #ef4444;">Error loading network data</div>';
            }
        }

        function closeFundingNetwork3Tier() {
            document.getElementById('fundingNetwork3TierModal').style.display = 'none';
        }

        async function showCoordinatedFunderAnalysis(creatorAddress) {
            const modal = document.getElementById('coordinatedFunderAnalysisModal');

            try {
                const response = await fetch(`/api/coordinated-funder-analysis/${creatorAddress}`);
                const data = await response.json();

                if (response.status === 404) {
                    document.getElementById('cfaConnectedCreators').innerHTML =
                        '<div style="color: #fbbf24; text-align: center; padding: 20px;">Not yet analyzed. Run Coordinated Funder Analysis first.</div>';
                    document.getElementById('cfaSharedDestinations').innerHTML = '';
                    document.getElementById('cfaRiskLevel').textContent = 'PENDING';
                    document.getElementById('cfaRiskLevel').style.color = '#fbbf24';
                    document.getElementById('cfaConnectedCount').textContent = '0';
                    document.getElementById('cfaSharedDests').textContent = '0';
                    modal.style.display = 'block';
                    return;
                }

                if (data.error) {
                    alert('Error loading analysis: ' + data.error);
                    return;
                }

                // Display risk level with color coding
                let riskColor = '#4ade80';  // LOW - green
                if (data.network_risk_level === 'HIGH') riskColor = '#fbbf24';  // orange
                if (data.network_risk_level === 'CRITICAL') riskColor = '#ef4444';  // red

                document.getElementById('cfaRiskLevel').textContent = data.network_risk_level || 'UNKNOWN';
                document.getElementById('cfaRiskLevel').style.color = riskColor;
                document.getElementById('cfaConnectedCount').textContent = data.connected_creators_count || 0;
                document.getElementById('cfaSharedDests').textContent = data.shared_destinations_count || 0;

                // Display connected creators
                let creatorsList = '';
                if (data.connected_creators && data.connected_creators.length > 0) {
                    data.connected_creators.forEach((cc, idx) => {
                        const riskStyle = cc.risk_level === 'CRITICAL' ? 'color: #ef4444;' :
                                         cc.risk_level === 'HIGH' ? 'color: #fbbf24;' :
                                         'color: #4ade80;';
                        creatorsList += `
                            <div style="margin-bottom: 8px; ${riskStyle}">
                                ${idx + 1}. ${cc.creator_address.substring(0, 16)}...
                                <span style="font-size: 10px; color: #a0a0a0;">
                                    [${cc.risk_level}] Rug: ${(cc.rug_probability * 100).toFixed(0)}%
                                </span>
                            </div>
                        `;
                    });
                    if (data.connected_creators_count > 10) {
                        creatorsList += `<div style="color: #a0a0a0; font-size: 11px;">... and ${data.connected_creators_count - 10} more</div>`;
                    }
                } else {
                    creatorsList = '<div style="color: #a0a0a0;">No connected creators found</div>';
                }
                document.getElementById('cfaConnectedCreators').innerHTML = creatorsList;

                // Display shared destinations
                let destsList = '';
                if (data.shared_destinations && data.shared_destinations.length > 0) {
                    data.shared_destinations.slice(0, 20).forEach((dest, idx) => {
                        destsList += `<div style="margin-bottom: 6px; color: #fbbf24; font-size: 11px;">${idx + 1}. ${dest}</div>`;
                    });
                    if (data.shared_destinations_count > 20) {
                        destsList += `<div style="color: #a0a0a0; font-size: 11px;">... and ${data.shared_destinations_count - 20} more</div>`;
                    }
                } else {
                    destsList = '<div style="color: #a0a0a0;">No shared destinations found</div>';
                }
                document.getElementById('cfaSharedDestinations').innerHTML = destsList;

                // Display timestamp
                const detectedDate = new Date(data.detected_at).toLocaleString();
                document.getElementById('cfaDetectedAt').textContent = detectedDate;

                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading coordinated funder analysis:', error);
                alert('Failed to load analysis');
            }
        }

        function closeCoordinatedFunderAnalysis() {
            document.getElementById('coordinatedFunderAnalysisModal').style.display = 'none';
        }

        async function showFunderDetails(funderAddress) {
            const modal = document.getElementById('funderDetailsModal');
            document.getElementById('fdFunderAddr').textContent = funderAddress;

            try {
                const response = await fetch(`/api/funder-transfer-details/${funderAddress}`);
                const data = await response.json();

                if (data.error) {
                    alert('Error loading funder details: ' + data.error);
                    return;
                }

                // Update summary stats
                document.getElementById('fdCreatorCount').textContent = data.creators_funded;
                document.getElementById('fdIncomingTotal').textContent = data.incoming_transfers.total_sol.toFixed(2) + ' SOL';
                document.getElementById('fdOutgoingTotal').textContent = data.outgoing_transfers.total_sol.toFixed(2) + ' SOL';
                document.getElementById('fdNetFlow').textContent = data.net_flow.toFixed(2) + ' SOL';

                // Display incoming transfers
                let incomingHTML = '';
                if (data.incoming_transfers.senders && data.incoming_transfers.senders.length > 0) {
                    data.incoming_transfers.senders.forEach((sender) => {
                        const label = sender.label ? `[${sender.label}]` : `[${sender.category || 'Unknown'}]`;
                        const labelColor = sender.is_known ? '#4ade80' : '#fbbf24';
                        const badge = sender.is_known ? ' ✓' : '';
                        incomingHTML += `
                            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                                <td style="padding: 10px; color: #e0e0e0; font-family: monospace; font-size: 11px; word-break: break-all;">${sender.address.substring(0, 16)}...</td>
                                <td style="padding: 10px; text-align: right; color: #4ade80;">${sender.amount_sol.toFixed(2)}</td>
                                <td style="padding: 10px; text-align: center; color: #a0a0a0;">${sender.transaction_count}</td>
                                <td style="padding: 10px; color: ${labelColor};">${label}${badge}</td>
                            </tr>
                        `;
                    });
                } else {
                    incomingHTML = '<tr><td colspan="4" style="padding: 20px; text-align: center; color: #a0a0a0;">No incoming transfers recorded</td></tr>';
                }
                document.getElementById('fdIncomingBody').innerHTML = incomingHTML;

                // Display outgoing transfers
                let outgoingHTML = '';
                if (data.outgoing_transfers.recipients && data.outgoing_transfers.recipients.length > 0) {
                    data.outgoing_transfers.recipients.forEach((recipient) => {
                        const label = recipient.label ? `[${recipient.label}]` : `[${recipient.category || 'Unknown'}]`;
                        const labelColor = recipient.is_known ? '#4ade80' : '#fbbf24';
                        const badge = recipient.is_known ? ' ✓' : '';
                        outgoingHTML += `
                            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                                <td style="padding: 10px; color: #e0e0e0; font-family: monospace; font-size: 11px; word-break: break-all;">${recipient.address.substring(0, 16)}...</td>
                                <td style="padding: 10px; text-align: right; color: #ef4444;">${recipient.amount_sol.toFixed(2)}</td>
                                <td style="padding: 10px; text-align: center; color: #a0a0a0;">${recipient.transaction_count}</td>
                                <td style="padding: 10px; color: ${labelColor};">${label}${badge}</td>
                            </tr>
                        `;
                    });
                } else {
                    outgoingHTML = '<tr><td colspan="4" style="padding: 20px; text-align: center; color: #a0a0a0;">No outgoing transfers recorded</td></tr>';
                }
                document.getElementById('fdOutgoingBody').innerHTML = outgoingHTML;

                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading funder details:', error);
                alert('Failed to load funder details');
            }
        }

        function closeFunderDetails() {
            document.getElementById('funderDetailsModal').style.display = 'none';
        }

        async function validateTransaction() {
            const sig = document.getElementById('validationInput').value.trim();

            if (!sig) {
                alert('Please enter a transaction signature');
                return;
            }

            // Show loading state
            document.getElementById('validationResults').style.display = 'block';
            document.getElementById('validationLoading').style.display = 'block';
            document.getElementById('validationSuccess').style.display = 'none';
            document.getElementById('validationError').style.display = 'none';

            try {
                const response = await fetch('/api/validate-transaction', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ signature: sig })
                });

                const result = await response.json();

                // Hide loading
                document.getElementById('validationLoading').style.display = 'none';

                if (result.error) {
                    // Show error
                    document.getElementById('validationError').style.display = 'block';
                    document.getElementById('errorMessage').textContent = result.error;
                } else {
                    // Show success
                    document.getElementById('validationSuccess').style.display = 'block';

                    // Populate results
                    document.getElementById('resultMint').textContent = result.mint || '—';
                    document.getElementById('resultCreator').textContent = result.creator || '—';
                    document.getElementById('resultTimestamp').textContent = result.timestamp || '—';

                    // Build evidence list
                    const evidence = [];
                    if (result.has_system_create) evidence.push('✅ System.createAccount (5 instances)');
                    if (result.has_init_mint) evidence.push('✅ initializeMint2 instruction');
                    if (result.pump_program) evidence.push('✅ Pump.fun program involved');
                    if (result.confirmed) evidence.push('✅ Confirmed on-chain');
                    evidence.push(`✅ Instructions: ${result.instruction_count} top-level + ${result.inner_instruction_count} inner`);

                    document.getElementById('resultEvidence').innerHTML = evidence.join('<br>');

                    // Set Solscan link
                    document.getElementById('resultSolscanLink').href = `https://solscan.io/tx/${sig}`;
                }
            } catch (error) {
                console.error('Validation error:', error);
                document.getElementById('validationLoading').style.display = 'none';
                document.getElementById('validationError').style.display = 'block';
                document.getElementById('errorMessage').textContent = `Network error: ${error.message}`;
            }
        }

        // Allow Enter key to validate
        document.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && document.getElementById('validationModal').style.display === 'block') {
                validateTransaction();
            }
        });

        // Funding Networks Functions
        async function loadFundingNetworks() {
            // Load super-clusters directly (no separate funding networks)
            loadSuperClustersInNetworkView();
        }

        async function loadSuperClustersInNetworkView() {
            const gridEl = document.getElementById('super-clusters-grid');
            if (!gridEl) return;

            try {
                const response = await fetch('/api/super-clusters');
                const data = await response.json();

                if (data.error) {
                    gridEl.innerHTML = '<div style="grid-column: 1/-1; color: #ef4444;">Error loading super-clusters: ' + data.error + '</div>';
                    return;
                }

                // Update statistics
                const clusters = data.clusters || [];
                document.getElementById('scTotalCount').textContent = clusters.length;
                document.getElementById('scCriticalCount').textContent = clusters.filter(c => c.risk_level === 'CRITICAL').length;
                document.getElementById('scHighCount').textContent = clusters.filter(c => c.risk_level === 'HIGH').length;
                document.getElementById('scMediumCount').textContent = clusters.filter(c => c.risk_level === 'MEDIUM').length;

                let html = '';

                clusters.forEach(cluster => {
                    html += `
                        <div style="background: rgba(0, 0, 0, 0.3); border: 1px solid #6366f1; border-radius: 8px; padding: 25px; cursor: pointer; transition: all 0.3s;"
                             onclick="showSuperCluster('${cluster.id}')"
                             onmouseover="this.style.background='rgba(99, 102, 241, 0.15)'; this.style.boxShadow='0 0 15px rgba(99, 102, 241, 0.5)';"
                             onmouseout="this.style.background='rgba(0, 0, 0, 0.3)'; this.style.boxShadow='none';">
                            <div style="font-weight: bold; color: #e0e0e0; font-size: 16px; margin-bottom: 18px;">${cluster.id}</div>
                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; font-size: 12px;">
                                <div style="background: rgba(99, 102, 241, 0.1); padding: 12px; border-radius: 4px; border-left: 2px solid #6366f1;">
                                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 4px;">Creators</div>
                                    <div style="color: #6366f1; font-weight: bold; font-size: 18px;">${cluster.creator_count}</div>
                                </div>
                                <div style="background: rgba(99, 102, 241, 0.1); padding: 12px; border-radius: 4px; border-left: 2px solid #6366f1;">
                                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 4px;">Networks</div>
                                    <div style="color: #6366f1; font-weight: bold; font-size: 18px;">${cluster.network_count}</div>
                                </div>
                                <div style="background: rgba(99, 102, 241, 0.1); padding: 12px; border-radius: 4px; border-left: 2px solid #6366f1;">
                                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 4px;">Mapped</div>
                                    <div style="color: #6366f1; font-weight: bold; font-size: 18px;">${cluster.mapped_creators}</div>
                                </div>
                                <div style="background: rgba(99, 102, 241, 0.1); padding: 12px; border-radius: 4px; border-left: 2px solid #6366f1;">
                                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 4px;">Root Ops</div>
                                    <div style="color: #6366f1; font-weight: bold; font-size: 18px;">${cluster.root_addresses.length}</div>
                                </div>
                                <div style="background: rgba(99, 102, 241, 0.1); padding: 12px; border-radius: 4px; border-left: 2px solid #6366f1;">
                                    <div style="color: #a0a0a0; font-size: 11px; margin-bottom: 4px;">Risk</div>
                                    <div style="color: #6366f1; font-weight: bold; font-size: 18px;">${cluster.risk_level}</div>
                                </div>
                            </div>
                        </div>
                    `;
                });

                gridEl.innerHTML = html;
            } catch(e) {
                console.error('Error loading super-clusters:', e);
                gridEl.innerHTML = '<div style="grid-column: 1/-1; color: #ef4444;">Error: ' + e.message + '</div>';
            }
        }


        // Real-time polling for network updates
        let networkPollingInterval = null;
        let lastNetworkDataHash = null;

        function startNetworkPolling() {
            // Only poll if not already polling
            if (networkPollingInterval) return;

            console.log('[Networks] Starting real-time polling');

            networkPollingInterval = setInterval(async () => {
                try {
                    const response = await fetch('/api/funding-networks-list');
                    const data = await response.json();

                    if (!data.error && data.networks) {
                        // Create a hash of the current data to detect changes
                        const currentHash = JSON.stringify(data.networks);

                        if (currentHash !== lastNetworkDataHash) {
                            console.log('[Networks] Changes detected, refreshing display');
                            lastNetworkDataHash = currentHash;

                            // Refresh the grid if we're on the main networks view
                            const gridEl = document.getElementById('funding-networks-grid');

                            if (gridEl && !gridEl.innerHTML.includes('Network Details')) {
                                // We're on the main networks list, refresh it
                                await loadFundingNetworks();
                            }
                        }
                    }
                } catch(e) {
                    console.error('[Networks] Polling error:', e);
                }
            }, 10000); // Poll every 10 seconds
        }

        function stopNetworkPolling() {
            if (networkPollingInterval) {
                console.log('[Networks] Stopping polling');
                clearInterval(networkPollingInterval);
                networkPollingInterval = null;
                lastNetworkDataHash = null;
            }
        }

        // =====================================================================
        // SUPER-CLUSTER FUNCTIONS
        // =====================================================================

        async function showSuperCluster(clusterId) {
            const modal = document.getElementById('superClusterModal');
            document.getElementById('scModalId').textContent = clusterId;

            try {
                const response = await fetch(`/api/super-cluster/${clusterId}`);
                const data = await response.json();

                if (data.error) {
                    alert('Super-cluster details not found');
                    return;
                }

                // Set risk badge color
                const riskBadge = document.getElementById('scRiskBadge');
                let badgeColor = '#6b7280';
                let badgeText = data.risk_level;

                if (data.risk_level === 'CRITICAL') {
                    badgeColor = '#ef4444';
                    badgeText = '🚨 CRITICAL';
                } else if (data.risk_level === 'HIGH') {
                    badgeColor = '#f97316';
                    badgeText = '⚠️ HIGH';
                } else if (data.risk_level === 'MEDIUM') {
                    badgeColor = '#fbbf24';
                    badgeText = '⚡ MEDIUM';
                }

                riskBadge.textContent = badgeText;
                riskBadge.style.backgroundColor = badgeColor + '20';
                riskBadge.style.color = badgeColor;

                // Update stats
                document.getElementById('scNetworkCount').textContent = data.network_count;
                document.getElementById('scCreatorCount').textContent = data.creator_count;
                document.getElementById('scTokenCount').textContent = data.tokens.length;
                document.getElementById('scFunderCount').textContent = data.funder_stats.total_funders || 0;
                document.getElementById('scTotalSol').textContent = (data.funder_stats.total_sol || 0).toFixed(2) + ' SOL';
                document.getElementById('scCexCount').textContent = data.funder_stats.cex_funders || 0;

                // Update root operators with address flows
                const rootsContainer = document.getElementById('scRootAddresses');
                if (data.root_operator_flows && data.root_operator_flows.length > 0) {{
                    rootsContainer.innerHTML = data.root_operator_flows.map((flow, idx) => {{
                        let flowsHTML = '';
                        if (flow.example_flows && flow.example_flows.length > 0) {{
                            flow.example_flows.forEach((ex, flowIdx) => {{
                                flowsHTML += `<div style="font-family: monospace; font-size: 8px; color: #e0e0e0; padding: 6px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.05); line-height: 1.3;">
                                    <div style="color: #3b82f6;">${ex.sender.substring(0, 14)}...</div>
                                    <div style="color: #a0a0a0; margin-left: 8px; font-size: 7px;">↓ (to funder)</div>
                                    <div style="color: #6366f1;">${ex.funder.substring(0, 14)}...</div>
                                    <div style="color: #a0a0a0; margin-left: 8px; font-size: 7px;">↓ ${ex.sol_to_creator.toFixed(2)} SOL</div>
                                    <div style="color: #f59e0b;">${ex.creator.substring(0, 14)}...</div>
                                </div>`;
                            }});
                        }}
                        return `
                            <div style="background: rgba(99, 102, 241, 0.08); padding: 12px; border-radius: 6px; border-left: 3px solid #6366f1; margin-bottom: 12px;">
                                <div style="font-size: 10px; color: #a0a0a0; margin-bottom: 8px;">ROOT OPERATOR #${idx + 1}</div>
                                <div style="font-family: monospace; font-size: 11px; color: #6366f1; word-break: break-all; margin-bottom: 8px; padding: 6px; background: rgba(99, 102, 241, 0.1); border-radius: 4px;">${flow.root_operator}</div>
                                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; font-size: 10px;">
                                    <div>
                                        <div style="color: #a0a0a0;">CREATORS FUNDED</div>
                                        <div style="color: #f59e0b; font-weight: bold;">${flow.creators_funded}</div>
                                    </div>
                                    <div>
                                        <div style="color: #a0a0a0;">TOTAL SOL</div>
                                        <div style="color: #4ade80; font-weight: bold;">${flow.total_sol_sent.toFixed(2)}</div>
                                    </div>
                                </div>
                                <div style="font-size: 9px; color: #a0a0a0; margin-bottom: 6px;">EXAMPLE FLOWS: Sender → Funder → Creator</div>
                                <div style="background: rgba(0, 0, 0, 0.3); border-radius: 4px; padding: 6px; max-height: 120px; overflow-y: auto;">
                                    ${flowsHTML || '<div style="color: #a0a0a0; font-size: 9px;">No flows available</div>'}
                                </div>
                            </div>
                        `;
                    }}).join('');
                }} else {{
                    rootsContainer.innerHTML = data.root_addresses.map((addr, idx) => {{
                        return `
                            <div style="background: rgba(99, 102, 241, 0.05); padding: 10px; border-radius: 6px; border-left: 2px solid #6366f1;">
                                <div style="font-family: monospace; font-size: 11px; color: #6366f1; word-break: break-all; margin-bottom: 5px;">${addr}</div>
                                <div style="font-size: 11px; color: #a0a0a0;">Root Operator #${idx + 1}</div>
                            </div>
                        `;
                    }}).join('');
                }}

                // Build relationship diagram
                const relationshipDiv = document.getElementById('scRelationshipDiagram');
                const relationshipHTML = `
                    <div style="font-size: 12px; color: #a0a0a0; line-height: 1.8;">
                        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                            <div style="background: rgba(99, 102, 241, 0.1); padding: 8px 12px; border-radius: 4px; border-left: 2px solid #6366f1; color: #6366f1;">
                                <strong>${data.root_addresses.length}</strong> Root Operators
                            </div>
                            <div style="color: #888;">↓</div>
                            <div style="background: rgba(245, 158, 11, 0.1); padding: 8px 12px; border-radius: 4px; border-left: 2px solid #f59e0b; color: #f59e0b;">
                                <strong>${data.creator_count}</strong> Creators
                            </div>
                        </div>
                        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                            <div style="background: rgba(245, 158, 11, 0.1); padding: 8px 12px; border-radius: 4px; border-left: 2px solid #f59e0b; color: #f59e0b;">
                                <strong>${data.creator_count}</strong> Creators
                            </div>
                            <div style="color: #888;">↓</div>
                            <div style="background: rgba(59, 130, 246, 0.1); padding: 8px 12px; border-radius: 4px; border-left: 2px solid #3b82f6; color: #3b82f6;">
                                <strong>${data.tokens.length}</strong> Tokens
                            </div>
                        </div>
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <div style="background: rgba(59, 130, 246, 0.1); padding: 8px 12px; border-radius: 4px; border-left: 2px solid #3b82f6; color: #3b82f6;">
                                <strong>${data.tokens.length}</strong> Tokens
                            </div>
                            <div style="color: #888;">↓</div>
                            <div style="background: rgba(74, 222, 128, 0.1); padding: 8px 12px; border-radius: 4px; border-left: 2px solid #4ade80; color: #4ade80;">
                                <strong>${data.funder_stats.total_funders}</strong> Funders (${data.funder_stats.cex_funders} CEX)
                            </div>
                        </div>
                        <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid rgba(99, 102, 241, 0.2); color: #888; font-size: 11px;">
                            Total funding tracked: <strong style="color: #a855f7;">${data.funder_stats.total_sol.toFixed(2)} SOL</strong> across <strong style="color: #6366f1;">${data.network_count}</strong> networks
                        </div>
                    </div>
                `;
                relationshipDiv.innerHTML = relationshipHTML;

                // Populate creators tab
                const creatorsList = document.getElementById('scCreatorsList');
                if (data.creators.length > 0) {
                    creatorsList.innerHTML = data.creators.map(creator => {
                        const creatorTokens = data.tokens.filter(t => t.earliest_tx_creator === creator).length;
                        return `
                            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                                <td style="padding: 10px;">
                                    <a href="#" onclick="showCreatorDetails('${creator}'); return false;"
                                       style="color: #00d4ff; text-decoration: none; font-family: monospace; font-size: 11px; word-break: break-all;"
                                       title="${creator}">${creator}</a>
                                </td>
                                <td style="padding: 10px; color: #e0e0e0;">${creatorTokens}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    creatorsList.innerHTML = '<tr><td colspan="2" style="padding: 20px; text-align: center; color: #a0a0a0;">No creators found</td></tr>';
                }

                // Populate tokens tab
                const tokensList = document.getElementById('scTokensList');
                if (data.tokens.length > 0) {
                    tokensList.innerHTML = data.tokens.map(token => {
                        const riskColor = token.rug_probability > 0.7 ? '#ef4444' : token.rug_probability > 0.4 ? '#fbbf24' : '#10b981';
                        const riskPercent = (token.rug_probability * 100).toFixed(0);
                        const mcDisplay = token.market_cap_highest ? formatMarketCap(token.market_cap_highest) : '—';

                        return `
                            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                                <td style="padding: 10px;">
                                    <a href="#" onclick="showTokenMetrics('${token.mint}'); return false;"
                                       style="color: #00d4ff; text-decoration: none; font-family: monospace; font-size: 11px; word-break: break-all;"
                                       title="${token.mint}">${token.mint}</a>
                                </td>
                                <td style="padding: 10px; font-family: monospace; font-size: 11px; color: #a0a0a0; word-break: break-all;">${token.earliest_tx_creator}</td>
                                <td style="padding: 10px; text-align: right;">
                                    <span style="color: ${riskColor}; font-weight: bold;">${riskPercent}%</span>
                                </td>
                                <td style="padding: 10px; text-align: right; color: #e0e0e0;">${mcDisplay}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    tokensList.innerHTML = '<tr><td colspan="4" style="padding: 20px; text-align: center; color: #a0a0a0;">No tokens found</td></tr>';
                }

                // Reset to creators tab
                document.querySelectorAll('.sc-tab-button').forEach(btn => btn.classList.remove('active'));
                document.querySelectorAll('.sc-tab-content').forEach(tab => tab.style.display = 'none');
                document.querySelector('[data-tab="creators"]').classList.add('active');
                document.getElementById('scCreatorsTab').style.display = 'block';

                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading super-cluster details:', error);
                alert('Failed to load super-cluster details');
            }
        }

        function closeSuperCluster() {
            document.getElementById('superClusterModal').style.display = 'none';
        }

        function switchSuperClusterTab(tabName) {
            // Hide all tabs
            document.querySelectorAll('.sc-tab-content').forEach(tab => tab.style.display = 'none');
            document.querySelectorAll('.sc-tab-button').forEach(btn => btn.classList.remove('active'));

            // Show selected tab
            document.getElementById(`sc${tabName.charAt(0).toUpperCase() + tabName.slice(1)}Tab`).style.display = 'block';
            document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
        }

        async function loadSuperClusters() {
            try {
                const response = await fetch('/api/super-clusters');
                const data = await response.json();

                console.log(`Loaded ${data.total} super-clusters`);

                // Add super-cluster display to the UI if needed
                // For now, just log the data
                window.superClustersData = data;

            } catch (error) {
                console.error('Error loading super-clusters:', error);
            }
        }
    </script>
</body>
</html>
"""


def highlight_infra_in_funding(funders_list):
    """
    Add infrastructure/CEX/tag information to funders list.
    Enrich each funder with is_infrastructure, category, tags, display_name, and address_tags.
    """
    from infra_mapping import get_account_info, get_cex_info
    from address_tags import get_address_tags, get_domain_tag
    
    enriched_funders = []
    
    for funder in funders_list:
        funder_copy = funder.copy()
        funder_address = funder.get('funder_address')
        
        if not funder_address:
            funder_copy['is_infrastructure'] = False
            funder_copy['category'] = None
            funder_copy['tags'] = []
            funder_copy['display_name'] = None
            funder_copy['address_tags'] = {}
            enriched_funders.append(funder_copy)
            continue
        
        # Get address tags (domains, etc.)
        address_tags = get_address_tags(funder_address)
        
        # Check infrastructure first
        infra_info = get_account_info(funder_address)
        if infra_info:
            funder_copy['is_infrastructure'] = True
            funder_copy['category'] = infra_info.get('category')
            funder_copy['tags'] = infra_info.get('tags', [])
            funder_copy['display_name'] = infra_info.get('name')
            funder_copy['address_tags'] = address_tags
            enriched_funders.append(funder_copy)
            continue
        
        # Check CEX
        cex_info = get_cex_info(funder_address)
        if cex_info:
            funder_copy['is_infrastructure'] = False  # CEX is not infrastructure
            funder_copy['category'] = cex_info.get('category')
            funder_copy['tags'] = cex_info.get('tags', [])
            funder_copy['display_name'] = cex_info.get('name')
            funder_copy['address_tags'] = address_tags
            enriched_funders.append(funder_copy)
            continue
        
        # Neither infrastructure nor CEX
        funder_copy['is_infrastructure'] = False
        funder_copy['category'] = None
        funder_copy['tags'] = []
        funder_copy['display_name'] = None
        funder_copy['address_tags'] = address_tags
        enriched_funders.append(funder_copy)
    
    return enriched_funders

@app.route('/')
def index():
    """Serve the migration tracking dashboard"""
    # Return HTML directly without Jinja2 processing
    # The template contains ${{ }} which looks like Jinja2 escaping but is actually
    # incorrect - Jinja2 would interpret {{ }} as a print statement regardless of the $
    # Since there are no actual Jinja2 variables in the template, return it as-is
    return HTML_TEMPLATE


@app.route('/coordinated-funder-analysis/<creator_address>')
def coordinated_funder_analysis_view(creator_address: str):
    """Serve a full webview for coordinated funder analysis results"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get coordinated funder analysis data
        cursor.execute("""
            SELECT
                creator_address,
                connected_creators,
                shared_destinations,
                network_size,
                network_risk_level,
                detected_at,
                updated_at
            FROM creator_networks
            WHERE creator_address = ?
        """, (creator_address,))

        result = cursor.fetchone()

        if not result:
            conn.close()
            return f"""
            <html>
                <head>
                    <title>Coordinated Funder Analysis</title>
                    <style>
                        body {{ background: #0a0e27; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; margin: 0; padding: 20px; }}
                        .container {{ max-width: 1200px; margin: 0 auto; }}
                        h1 {{ color: #00d4ff; margin-bottom: 30px; }}
                        .not-analyzed {{ background: rgba(0, 0, 0, 0.3); padding: 30px; border-radius: 8px; text-align: center; border-left: 3px solid #fbbf24; }}
                        .back-link {{ margin-bottom: 20px; }}
                        .back-link a {{ color: #00d4ff; text-decoration: none; }}
                        .back-link a:hover {{ text-decoration: underline; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="back-link"><a href="/">← Back to Dashboard</a></div>
                        <h1>Coordinated Funder Analysis</h1>
                        <div class="not-analyzed">
                            <h2 style="color: #fbbf24;">Not Yet Analyzed</h2>
                            <p>Coordinated funder analysis has not been performed for this creator yet.</p>
                            <p style="color: #a0a0a0; font-size: 14px;">Run the coordinated funder analysis script to generate results.</p>
                        </div>
                    </div>
                </body>
            </html>
            """

        # Parse JSON data
        import json
        connected_creators = json.loads(result['connected_creators']) if result['connected_creators'] else []
        shared_destinations = json.loads(result['shared_destinations']) if result['shared_destinations'] else []

        # Get details about connected creators
        connected_creator_details = []
        for cc_addr in connected_creators[:20]:
            cursor.execute("""
                SELECT
                    earliest_tx_creator,
                    risk_level,
                    rug_probability,
                    market_cap_highest,
                    created_at
                FROM token_analysis
                WHERE earliest_tx_creator = ?
                LIMIT 1
            """, (cc_addr,))

            cc_info = cursor.fetchone()
            if cc_info:
                connected_creator_details.append({
                    'address': cc_addr,
                    'risk_level': cc_info['risk_level'],
                    'rug_probability': cc_info['rug_probability'],
                    'market_cap': cc_info['market_cap_highest']
                })

        conn.close()

        # Determine risk color
        risk_colors = {
            'CRITICAL': '#ef4444',
            'HIGH': '#fbbf24',
            'MEDIUM': '#f97316',
            'LOW': '#4ade80'
        }
        risk_color = risk_colors.get(result['network_risk_level'], '#a0a0a0')

        # Build connected creators HTML
        connected_html = ''
        for i, cc in enumerate(connected_creator_details[:20], 1):
            risk_color_cc = risk_colors.get(cc['risk_level'], '#a0a0a0')
            connected_html += f"""
            <div style="padding: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); font-size: 13px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="font-family: monospace; color: {risk_color_cc};">{i}. {cc['address']}</div>
                    <div style="display: flex; gap: 20px; color: #a0a0a0;">
                        <span style="color: {risk_color_cc};">{cc['risk_level']}</span>
                        <span>{(cc['rug_probability'] * 100):.0f}% rug</span>
                    </div>
                </div>
            </div>
            """

        # Build shared destinations HTML
        destinations_html = ''
        for i, dest in enumerate(shared_destinations[:30], 1):
            destinations_html += f"""
            <div style="padding: 10px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); font-family: monospace; font-size: 12px; color: #fbbf24;">
                {i}. {dest}
            </div>
            """

        html = f"""
        <html>
            <head>
                <title>Coordinated Funder Analysis - {creator_address[:16]}...</title>
                <style>
                    body {{
                        background: #0a0e27;
                        color: #e0e0e0;
                        font-family: 'Segoe UI', sans-serif;
                        margin: 0;
                        padding: 20px;
                    }}
                    .container {{
                        max-width: 1400px;
                        margin: 0 auto;
                    }}
                    h1 {{
                        color: #00d4ff;
                        margin-bottom: 10px;
                    }}
                    .creator-addr {{
                        font-family: monospace;
                        font-size: 12px;
                        color: #a0a0a0;
                        margin-bottom: 30px;
                    }}
                    .back-link {{
                        margin-bottom: 20px;
                    }}
                    .back-link a {{
                        color: #00d4ff;
                        text-decoration: none;
                    }}
                    .back-link a:hover {{
                        text-decoration: underline;
                    }}
                    .stats-grid {{
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                        gap: 15px;
                        margin-bottom: 30px;
                    }}
                    .stat-box {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 20px;
                        border-radius: 8px;
                        border-left: 3px solid {risk_color};
                    }}
                    .stat-label {{
                        color: #a0a0a0;
                        font-size: 11px;
                        text-transform: uppercase;
                        margin-bottom: 10px;
                    }}
                    .stat-value {{
                        font-size: 24px;
                        font-weight: bold;
                        color: {risk_color};
                    }}
                    .section {{
                        background: rgba(0, 0, 0, 0.2);
                        border-radius: 8px;
                        margin-bottom: 30px;
                        overflow: hidden;
                    }}
                    .section-title {{
                        background: rgba(0, 0, 0, 0.4);
                        padding: 15px;
                        border-bottom: 1px solid rgba(0, 212, 255, 0.2);
                        font-weight: 600;
                        color: #00d4ff;
                    }}
                    .section-content {{
                        max-height: 600px;
                        overflow-y: auto;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="back-link"><a href="/">← Back to Dashboard</a></div>
                    <h1>Coordinated Funder Analysis</h1>
                    <div class="creator-addr">Creator: {creator_address}</div>

                    <div class="stats-grid">
                        <div class="stat-box">
                            <div class="stat-label">Network Risk Level</div>
                            <div class="stat-value">{result['network_risk_level']}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Network Size</div>
                            <div class="stat-value">{result['network_size']}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Connected Creators</div>
                            <div class="stat-value">{len(connected_creators)}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Shared Destinations</div>
                            <div class="stat-value">{len(shared_destinations)}</div>
                        </div>
                    </div>

                    <div class="section">
                        <div class="section-title">Connected Creators ({len(connected_creator_details)} shown)</div>
                        <div class="section-content">
                            {connected_html if connected_html else '<div style="padding: 20px; color: #a0a0a0;">No connected creators found</div>'}
                        </div>
                    </div>

                    <div class="section">
                        <div class="section-title">Shared Destination Wallets ({len(shared_destinations)} total)</div>
                        <div class="section-content">
                            {destinations_html if destinations_html else '<div style="padding: 20px; color: #a0a0a0;">No shared destinations found</div>'}
                        </div>
                    </div>

                    <div style="color: #a0a0a0; font-size: 12px; margin-top: 30px;">
                        <p>Analysis performed: {result['detected_at']}</p>
                        <p>Last updated: {result['updated_at']}</p>
                    </div>
                </div>
            </body>
        </html>
        """
        return html

    except Exception as e:
        return f"<html><body style='background: #0a0e27; color: #e0e0e0;'><h1>Error</h1><p>{str(e)}</p></body></html>", 500


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
        from address_tags import get_address_tags
        
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
                "address_tags": recipient_info.get("address_tags", {}),
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

        # 7. Get creator tags (from creator_tags table)
        cursor.execute("""
            SELECT tag, description, amount_sol
            FROM creator_tags
            WHERE creator_address = ?
        """, (creator_address,))
        tags = [{'tag': row[0], 'description': row[1], 'amount_sol': row[2]} for row in cursor.fetchall()]

        # 8. Get creator's address tags (domains, etc. from address_tags table)
        creator_address_tags = get_address_tags(creator_address)

        # 9. Get cross-creator references (network detection)
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

        # 10. Check if any recipients are network coordinators
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

        # 11. Get super-cluster membership
        cursor.execute("""
            SELECT
                csm.super_cluster_id,
                sc.risk_level,
                sc.creator_count,
                sc.network_count
            FROM creator_super_cluster_membership csm
            INNER JOIN super_clusters sc ON csm.super_cluster_id = sc.super_cluster_id
            WHERE csm.creator_address = ?
            ORDER BY sc.creator_count DESC
        """, (creator_address,))

        super_clusters = [dict(row) for row in cursor.fetchall()]

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
            'creator_address_tags': creator_address_tags,
            'tokens': tokens,
            'funding': funding,
            'top_funders': top_funders,
            'top_recipients': top_recipients,
            'cross_references': cross_refs,
            'cluster': cluster,
            'is_blocked': is_blocked,
            'tags': tags,
            'super_clusters': super_clusters
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funder-tokens/<funder_address>')
def api_funder_tokens(funder_address: str):
    """Get tokens that a funder (account) has supported"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all tokens where this address was a funder
        cursor.execute("""
            SELECT DISTINCT
                ta.mint,
                ta.earliest_tx_creator as creator_address,
                ta.created_at,
                ta.risk_level,
                ta.market_cap_current,
                cf.amount_sol
            FROM creator_funders cf
            JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
            WHERE cf.funder_address = ?
            ORDER BY ta.created_at DESC
        """, (funder_address,))

        tokens = []
        for row in cursor.fetchall():
            tokens.append({
                'mint': row['mint'],
                'creator_address': row['creator_address'],
                'created_at': row['created_at'],
                'risk_level': row['risk_level'],
                'market_cap_current': row['market_cap_current'],
                'funding_amount_sol': row['amount_sol']
            })

        conn.close()
        return jsonify({
            'funder_address': funder_address,
            'tokens_funded': tokens,
            'total_tokens_funded': len(tokens)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cex-funders')
def api_cex_funders():
    """Get all CEX funders and their activity"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all CEX exchanges with statistics
        cursor.execute("""
            SELECT
                cex_exchange,
                COUNT(DISTINCT creator_address) as creator_count,
                COUNT(DISTINCT funder_address) as funder_count,
                SUM(amount_sol) as total_sol
            FROM creator_funders
            WHERE is_cex = 1
            GROUP BY cex_exchange
            ORDER BY total_sol DESC
        """)
        exchanges = [dict(row) for row in cursor.fetchall()]

        # Get top CEX funders across all exchanges
        cursor.execute("""
            SELECT
                funder_address,
                cex_exchange,
                cex_type,
                COUNT(DISTINCT creator_address) as creators_funded,
                SUM(amount_sol) as total_sol
            FROM creator_funders
            WHERE is_cex = 1
            GROUP BY funder_address, cex_exchange
            ORDER BY total_sol DESC
            LIMIT 50
        """)
        top_cex_funders = [dict(row) for row in cursor.fetchall()]

        # Get all creators funded by CEX
        cursor.execute("""
            SELECT
                creator_address,
                COUNT(DISTINCT cex_exchange) as exchanges_funding,
                SUM(amount_sol) as total_cex_funding
            FROM creator_funders
            WHERE is_cex = 1
            GROUP BY creator_address
            ORDER BY total_cex_funding DESC
            LIMIT 100
        """)
        cex_funded_creators = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            'exchanges': exchanges,
            'top_cex_funders': top_cex_funders,
            'cex_funded_creators': cex_funded_creators,
            'total_cex_funders': len(top_cex_funders),
            'total_cex_funded_creators': len(cex_funded_creators)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- Funding Network / Coordination Detection Endpoints ---

@app.route('/api/funding-network')
def api_funding_network():
    """Get suspicious funding coordination networks"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # REAL coordination indicator: Same sender funding multiple different creators
        # This query finds senders that appear in funder_incoming_transfers AND 
        # those funders in turn funded multiple creators
        cursor.execute("""
            SELECT sender_address,
                   COUNT(DISTINCT creator_address) as creator_count,
                   COUNT(DISTINCT funder_address) as funder_count,
                   COUNT(*) as transaction_count,
                   SUM(amount_sol) as total_sol
            FROM (
                SELECT fit.sender_address,
                       cf.creator_address,
                       fit.funder_address,
                       fit.amount_sol
                FROM funder_incoming_transfers fit
                JOIN creator_funders cf ON fit.funder_address = cf.funder_address
                GROUP BY fit.sender_address, cf.creator_address, fit.funder_address
            )
            GROUP BY sender_address
            HAVING creator_count >= 2
            ORDER BY creator_count DESC, funder_count DESC
        """)
        
        true_coordinators = [dict(row) for row in cursor.fetchall()]

        # Get funder analysis statistics
        cursor.execute("""
            SELECT COUNT(DISTINCT funder_address) as analyzed_funders,
                   COUNT(DISTINCT creator_address) as creators_with_funders,
                   SUM(total_inflows) as total_inflows,
                   SUM(total_outflows) as total_outflows
            FROM creator_funders
            WHERE last_analyzed IS NOT NULL
        """)
        stats_row = cursor.fetchone()
        stats = dict(stats_row) if stats_row else {}

        # Build networks - only true coordinators
        networks = []
        hub_addresses = set()

        for coordinator in true_coordinators:
            networks.append({
                'address': coordinator['sender_address'],
                'creator_count': coordinator['creator_count'],
                'total_sol': coordinator['total_sol'] or 0,
                'transactions': coordinator['transaction_count'],
                'funder_count': coordinator['funder_count'],
                'risk_type': 'multi_creator' if coordinator['creator_count'] >= 3 else 'dual_creator'
            })
            
            # Hub = coordinates 3+ creators
            if coordinator['creator_count'] >= 3:
                hub_addresses.add(coordinator['sender_address'])

        conn.close()

        return jsonify({
            'networks': networks,
            'hub_addresses': list(hub_addresses),
            'total_sol': stats.get('total_inflows', 0) or 0,
            'analyzed_funders': stats.get('analyzed_funders', 0),
            'creators_with_funders': stats.get('creators_with_funders', 0),
            'coordination_found': len(true_coordinators) > 0,
            'suspicious_coordinator_count': len(networks)
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


@app.route('/api/network-coordinators')
def api_network_coordinators():
    """Get all identified cross-funder coordinators"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all coordinators ordered by confidence and creator reach
        cursor.execute("""
            SELECT
                coordinator_address,
                creator_count,
                creators_linked,
                total_sol_moved,
                network_confidence,
                is_cex,
                cex_exchange,
                suspicious_flags,
                detection_timestamp,
                last_updated
            FROM network_coordinators
            ORDER BY
                CASE WHEN network_confidence = 'high' THEN 1
                     WHEN network_confidence = 'medium' THEN 2
                     ELSE 3 END,
                creator_count DESC
        """)

        coordinators = []
        for row in cursor.fetchall():
            creators = json.loads(row['creators_linked']) if row['creators_linked'] else []
            flags = json.loads(row['suspicious_flags']) if row['suspicious_flags'] else []

            coordinators.append({
                'address': row['coordinator_address'],
                'creator_count': row['creator_count'],
                'creators': creators,
                'total_sol': row['total_sol_moved'],
                'confidence': row['network_confidence'],
                'is_cex': bool(row['is_cex']),
                'cex_exchange': row['cex_exchange'],
                'flags': flags,
                'detected_at': row['detection_timestamp'],
                'updated_at': row['last_updated']
            })

        conn.close()

        return jsonify({
            'total': len(coordinators),
            'high_confidence': sum(1 for c in coordinators if c['confidence'] == 'high'),
            'medium_confidence': sum(1 for c in coordinators if c['confidence'] == 'medium'),
            'coordinators': coordinators
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funding-network-3tier/<creator_address>')
def api_funding_network_3tier(creator_address: str):
    """Get 3-tier funding network (Sender → Funder → Creator) for a specific creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Step 1: Find all funders funding this creator
        cursor.execute("""
            SELECT DISTINCT funder_address, SUM(amount_sol) as total_to_creator
            FROM creator_funders
            WHERE creator_address = ?
            GROUP BY funder_address
            ORDER BY total_to_creator DESC
        """, (creator_address,))

        funders = cursor.fetchall()

        # Step 2: For each funder, find all senders funding them
        network_3tier = []

        for funder_row in funders:
            funder_addr = funder_row['funder_address']
            funder_total = funder_row['total_to_creator']

            # Check funder type (CEX or INFRA)
            funder_type = 'unknown'
            funder_label = None
            try:
                from infra_mapping import get_cex_info
                cex_info = get_cex_info(funder_addr)
                if cex_info:
                    funder_type = 'cex'
                    funder_label = cex_info.get('name', 'Unknown CEX')
            except:
                pass

            # Get senders for this funder
            cursor.execute("""
                SELECT
                    sender_address,
                    SUM(amount_sol) as amount_to_funder,
                    sender_type,
                    is_cex,
                    cex_exchange,
                    cex_type
                FROM funder_incoming_transfers
                WHERE funder_address = ?
                GROUP BY sender_address, sender_type
                ORDER BY amount_to_funder DESC
            """, (funder_addr,))

            senders = cursor.fetchall()

            # Enrich senders with labels and sort known accounts first
            from infra_mapping import get_account_info, get_cex_info

            enriched_senders = []
            for s in senders:
                sender_addr = s['sender_address']
                sender_info = {
                    'sender_address': sender_addr,
                    'amount_to_funder': s['amount_to_funder'],
                    'sender_type': s['sender_type'],
                    'is_known': False,
                    'label': None,
                    'risk_level': 'unknown'  # NEW: track risk level
                }

                # Check if it's a CEX account
                if s['is_cex']:
                    sender_info['is_known'] = True
                    sender_info['label'] = s['cex_exchange'] or 'CEX'
                    sender_info['risk_level'] = 'high'  # CEX hot wallets are risky
                else:
                    # Check infrastructure mapping
                    infra_info = get_account_info(sender_addr)
                    if infra_info:
                        sender_info['is_known'] = True
                        sender_info['label'] = infra_info.get('name', 'Infrastructure')
                        sender_info['risk_level'] = infra_info.get('risk_level', 'neutral')  # NEW: get actual risk level
                    else:
                        # Check CEX info
                        cex_info = get_cex_info(sender_addr)
                        if cex_info:
                            sender_info['is_known'] = True
                            sender_info['label'] = cex_info.get('name', 'CEX')
                            sender_info['risk_level'] = cex_info.get('risk_level', 'high')

                enriched_senders.append(sender_info)

            # Sort: known accounts first (by amount), then unknown (by amount)
            known_senders = sorted([s for s in enriched_senders if s['is_known']],
                                 key=lambda x: x['amount_to_funder'], reverse=True)
            unknown_senders = sorted([s for s in enriched_senders if not s['is_known']],
                                    key=lambda x: x['amount_to_funder'], reverse=True)
            sorted_senders = known_senders + unknown_senders

            funder_info = {
                'funder_address': funder_addr,
                'funder_type': funder_type,
                'funder_label': funder_label,
                'total_to_creator': funder_total,
                'sender_count': len(sorted_senders),
                'known_sender_count': len(known_senders),
                'senders': sorted_senders
            }
            network_3tier.append(funder_info)

        # Step 3: Get creator info
        cursor.execute("""
            SELECT
                earliest_tx_creator,
                risk_level,
                rug_probability,
                market_cap_highest,
                created_at
            FROM token_analysis
            WHERE earliest_tx_creator = ?
            LIMIT 1
        """, (creator_address,))

        creator_info = cursor.fetchone()

        conn.close()

        return jsonify({
            'creator_address': creator_address,
            'creator_info': {
                'risk_level': creator_info['risk_level'] if creator_info else 'UNKNOWN',
                'rug_probability': creator_info['rug_probability'] if creator_info else 0,
                'market_cap_highest': creator_info['market_cap_highest'] if creator_info else 0,
                'created_at': creator_info['created_at'] if creator_info else None
            } if creator_info else None,
            'total_funders': len(funders),
            'total_senders': sum(len(f['senders']) for f in network_3tier),
            'network_tiers': network_3tier
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-funder-extraction-status/<creator_address>')
def api_creator_funder_extraction_status(creator_address: str):
    """Check if funder extraction is complete for a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Check if any funders for this creator have been analyzed
        cursor.execute("""
            SELECT
                COUNT(*) as total_funders,
                SUM(CASE WHEN last_analyzed IS NOT NULL THEN 1 ELSE 0 END) as analyzed_funders,
                MAX(last_analyzed) as last_analyzed_at
            FROM creator_funders
            WHERE creator_address = ?
        """, (creator_address,))

        result = cursor.fetchone()
        conn.close()

        if result['total_funders'] == 0:
            return jsonify({
                'creator_address': creator_address,
                'is_complete': False,
                'status': 'no_funders',
                'message': 'No funders found for this creator'
            })

        # Extraction is complete if all funders have been analyzed
        is_complete = result['analyzed_funders'] == result['total_funders'] and result['analyzed_funders'] > 0

        return jsonify({
            'creator_address': creator_address,
            'is_complete': is_complete,
            'status': 'complete' if is_complete else 'pending',
            'analyzed_funders': result['analyzed_funders'],
            'total_funders': result['total_funders'],
            'last_analyzed_at': result['last_analyzed_at']
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/coordinated-funder-analysis/<creator_address>')
def api_coordinated_funder_analysis(creator_address: str):
    """Get coordinated funder analysis results for a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Check creator_networks table for coordinated funding results
        cursor.execute("""
            SELECT
                creator_address,
                connected_creators,
                shared_destinations,
                network_size,
                network_risk_level,
                detected_at,
                updated_at
            FROM creator_networks
            WHERE creator_address = ?
        """, (creator_address,))

        result = cursor.fetchone()

        if not result:
            conn.close()
            return jsonify({
                'creator_address': creator_address,
                'status': 'not_analyzed',
                'message': 'Coordinated funder analysis not yet performed for this creator'
            }), 404

        # Parse JSON arrays
        import json
        connected_creators = json.loads(result['connected_creators']) if result['connected_creators'] else []
        shared_destinations = json.loads(result['shared_destinations']) if result['shared_destinations'] else []

        # Get more details about connected creators
        connected_creator_details = []
        for cc_addr in connected_creators[:10]:  # Limit to 10 for performance
            cursor.execute("""
                SELECT
                    earliest_tx_creator,
                    risk_level,
                    rug_probability,
                    market_cap_highest,
                    created_at
                FROM token_analysis
                WHERE earliest_tx_creator = ?
                LIMIT 1
            """, (cc_addr,))

            cc_info = cursor.fetchone()
            if cc_info:
                connected_creator_details.append({
                    'creator_address': cc_addr,
                    'risk_level': cc_info['risk_level'],
                    'rug_probability': cc_info['rug_probability'],
                    'market_cap_highest': cc_info['market_cap_highest'],
                    'created_at': cc_info['created_at']
                })

        conn.close()

        return jsonify({
            'creator_address': creator_address,
            'status': 'analyzed',
            'network_size': result['network_size'],
            'network_risk_level': result['network_risk_level'],
            'connected_creators_count': len(connected_creators),
            'shared_destinations_count': len(shared_destinations),
            'connected_creators': connected_creator_details,
            'shared_destinations': shared_destinations[:20],  # Limit to 20
            'detected_at': result['detected_at'],
            'updated_at': result['updated_at']
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
        from infra_mapping import CEX_ACCOUNTS, INFRASTRUCTURE_ACCOUNTS
        
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
            AND funder_address != creator_address
            ORDER BY amount_sol DESC
        """, creator_addresses)
        funders_data = {}
        for row in cursor.fetchall():
            creator = row['creator_address']
            if creator not in funders_data:
                funders_data[creator] = []

            funder_addr = row['funder_address']

            # Check if funder is in our known CEX or Infrastructure mappings
            is_cex = bool(row['is_cex'])
            cex_exchange = row['cex_exchange']
            cex_type = row['cex_type']
            display_name = None

            # Check live mapping for enriched display names (both new and existing CEX entries)
            if funder_addr in CEX_ACCOUNTS:
                is_cex = True
                cex_info = CEX_ACCOUNTS[funder_addr]
                # Use 'name' field for display (e.g., "Bybit Wallet 10"), fallback to exchange
                display_name = cex_info.get('name')
                cex_exchange = cex_info.get('exchange', cex_info.get('name', 'CEX'))
                cex_type = None  # Set to None since name field already includes type

            funders_data[creator].append({
                'address': funder_addr,
                'amount_sol': row['amount_sol'],
                'is_cex': is_cex,
                'cex_exchange': cex_exchange,
                'cex_type': cex_type,
                'display_name': display_name
            })

        # Creator service tags from creator_tags table (domain/infrastructure tags)
        cursor.execute(f"""
            SELECT creator_address, tag, description, amount_sol
            FROM creator_tags
            WHERE creator_address IN ({placeholders})
        """, creator_addresses)
        tags_data = {}
        for row in cursor.fetchall():
            creator = row['creator_address']
            if creator not in tags_data:
                tags_data[creator] = []
            tags_data[creator].append({
                'tag': row['tag'],
                'description': row['description'],
                'amount_sol': row['amount_sol']
            })

        # Creator service tags from creator_service_history (uses_jitotip, uses_meteora, etc.)
        cursor.execute(f"""
            SELECT DISTINCT creator_address, tag, 'Service tag' as description, NULL as amount_sol
            FROM creator_service_history
            WHERE creator_address IN ({placeholders})
        """, creator_addresses)
        for row in cursor.fetchall():
            creator = row['creator_address']
            if creator not in tags_data:
                tags_data[creator] = []

            # Build description for service tags
            tag_descriptions = {
                'uses_jitotip': 'Uses Jito tips on CREATE transaction',
                'uses_jitotip_other': 'Uses Jito MEV tips on transactions',
                'uses_meteora': 'Uses Meteora DLMM liquidity',
                'uses_debridge': 'Uses deBridge cross-chain transfers',
                'uses_axiom': 'Uses Axiom for verification'
            }

            tag_name = row['tag']
            description = tag_descriptions.get(tag_name, f'Uses {tag_name.replace("uses_", "")}')

            # Check if this tag already exists (avoid duplicates)
            if not any(t['tag'] == tag_name for t in tags_data[creator]):
                tags_data[creator].append({
                    'tag': tag_name,
                    'description': description,
                    'amount_sol': None
                })

        # Creator INFRA/CEX interactions (from creator_infra_interactions table)
        try:
            cursor.execute(f"""
                SELECT creator_address, account_type, account_name
                FROM creator_infra_interactions
                WHERE creator_address IN ({placeholders})
                GROUP BY creator_address, account_type, account_name
            """, creator_addresses)

            for row in cursor.fetchall():
                creator = row['creator_address']
                account_type = row['account_type']
                account_name = row['account_name']

                if creator not in tags_data:
                    tags_data[creator] = []

                # Create tag for INFRA/CEX interaction
                tag_key = f"uses_{account_name.lower().replace(' ', '_').replace('-', '_')}"
                description = f'Interacted with {account_name} ({account_type.upper()})'

                # Check if this tag already exists
                if not any(t['tag'] == tag_key for t in tags_data[creator]):
                    tags_data[creator].append({
                        'tag': tag_key,
                        'description': description,
                        'amount_sol': None
                    })
        except Exception as e:
            # Table may not exist yet, that's OK
            pass

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
                'funders': funders_data.get(creator, []),
                'tags': tags_data.get(creator, [])
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
        return jsonify({'error': str(e)}), 500@app.route('/api/creator-funding-history/<creator_address>')
def api_creator_funding_history(creator_address: str):
    """Get unified funding history for a creator (both incoming and outgoing transfers)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get incoming funders
        cursor.execute("""
            SELECT
                creator_address,
                funder_address as address,
                amount_sol,
                first_detected_at as timestamp,
                'incoming' as direction,
                is_cex,
                cex_exchange
            FROM creator_funders
            WHERE creator_address = ?
            ORDER BY first_detected_at DESC
        """, (creator_address,))

        incoming = [dict(row) for row in cursor.fetchall()]

        # Get outgoing recipients
        cursor.execute("""
            SELECT
                creator_address,
                recipient_address as address,
                SUM(amount_sol) as amount_sol,
                MAX(block_time) as block_timestamp,
                'outgoing' as direction,
                is_cex,
                cex_exchange
            FROM creator_outgoing_transfers
            WHERE creator_address = ?
            GROUP BY recipient_address
            ORDER BY MAX(block_time) DESC
        """, (creator_address,))

        outgoing = [dict(row) for row in cursor.fetchall()]

        # Combine and sort by timestamp
        all_transfers = incoming + outgoing
        all_transfers.sort(key=lambda x: x.get('timestamp') or x.get('block_timestamp') or '9999-12-31', reverse=True)

        conn.close()

        # Enrich transfers with infrastructure/CEX information
        enriched_transfers = []
        for transfer in all_transfers:
            transfer_copy = transfer.copy()
            address = transfer.get('address')

            if address:
                from infra_mapping import get_account_info, get_cex_info

                # Check infrastructure first
                infra_info = get_account_info(address)
                if infra_info:
                    transfer_copy['display_name'] = infra_info.get('name')
                    transfer_copy['category'] = infra_info.get('category')
                    transfer_copy['is_infrastructure'] = True
                else:
                    # Check CEX if not infrastructure
                    cex_info = get_cex_info(address)
                    if cex_info:
                        transfer_copy['display_name'] = cex_info.get('name')
                        transfer_copy['category'] = cex_info.get('category')
                        transfer_copy['is_infrastructure'] = False
                    else:
                        transfer_copy['display_name'] = None
                        transfer_copy['category'] = None
                        transfer_copy['is_infrastructure'] = False
            else:
                transfer_copy['display_name'] = None
                transfer_copy['category'] = None
                transfer_copy['is_infrastructure'] = False

            enriched_transfers.append(transfer_copy)

        return jsonify({
            'creator_address': creator_address,
            'transfers': enriched_transfers,
            'incoming_count': len(incoming),
            'outgoing_count': len(outgoing),
            'total_transfers': len(enriched_transfers),
            'total_incoming_sol': sum(t.get('amount_sol', 0) for t in incoming),
            'total_outgoing_sol': sum(t.get('amount_sol', 0) for t in outgoing)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/api/multi-creator-funders')
def api_multi_creator_funders():
    """Get funders that are funding multiple token creators (potential coordination risk)

    Shows all multi-creator funders with flags for infrastructure/CEX accounts.
    """
    try:
        from infra_mapping import get_account_info, get_cex_info, get_pumpfun_creator_info, get_suspicious_wallet_info

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get funders funding multiple creators
        cursor.execute("""
            SELECT
                funder_address,
                COUNT(DISTINCT creator_address) as creator_count,
                COUNT(*) as funding_record_count,
                SUM(amount_sol) as total_sol_sent,
                MIN(first_detected_at) as first_funding_at,
                MAX(first_detected_at) as last_funding_at,
                MAX(is_cex) as is_cex_flag
            FROM creator_funders
            GROUP BY funder_address
            HAVING COUNT(DISTINCT creator_address) > 1
            ORDER BY creator_count DESC, total_sol_sent DESC
        """)

        all_multi_funders = [dict(row) for row in cursor.fetchall()]

        # Classify and tag infrastructure/CEX accounts
        multi_funders = []
        suspicious_multi_funders = []

        for funder in all_multi_funders:
            funder_address = funder['funder_address']
            funder_data = dict(funder)
            funder_data['is_infrastructure'] = False
            funder_data['is_cex_account'] = False
            funder_data['account_info'] = None
            funder_data['networks'] = []

            # Check if already marked as CEX in database
            if funder['is_cex_flag']:
                funder_data['is_cex_account'] = True

            # Check if it's a known infrastructure account
            infra_info = get_account_info(funder_address)
            if infra_info:
                funder_data['is_infrastructure'] = True
                funder_data['account_info'] = infra_info

            # Check if it's a known CEX wallet
            cex_info = get_cex_info(funder_address)
            if cex_info:
                funder_data['is_cex_account'] = True
                funder_data['account_info'] = cex_info

            # Check if it's a PumpFun token creator (don't exclude from suspicious)
            pumpfun_info = get_pumpfun_creator_info(funder_address)
            if pumpfun_info and not funder_data['account_info']:
                funder_data['account_info'] = pumpfun_info

            # Check if it's a suspicious wallet type (don't exclude from suspicious)
            suspicious_wallet_info = get_suspicious_wallet_info(funder_address)
            if suspicious_wallet_info and not funder_data['account_info']:
                funder_data['account_info'] = suspicious_wallet_info

            # Get networks this funder belongs to (both shared-token and single-funder)
            cursor.execute("""
                SELECT
                    fn.network_id,
                    fn.network_name,
                    COALESCE(fn.network_type, 'shared') as network_type,
                    COUNT(DISTINCT fnt.mint) as token_count
                FROM funding_network_members fnm
                JOIN funding_networks fn ON fnm.network_id = fn.network_id
                LEFT JOIN funding_network_shared_tokens fnt ON fn.network_id = fnt.network_id
                WHERE fnm.funder_address = ?
                GROUP BY fn.network_id, fn.network_name, fn.network_type
                ORDER BY fn.network_id
            """, (funder_address,))

            networks = [dict(row) for row in cursor.fetchall()]
            funder_data['networks'] = networks

            # Add to appropriate list
            multi_funders.append(funder_data)

            # Suspicious = neither infrastructure nor CEX
            if not (funder_data['is_infrastructure'] or funder_data['is_cex_account']):
                suspicious_multi_funders.append(funder_data)

        # Get statistics
        cursor.execute("""
            SELECT
                COUNT(DISTINCT funder_address) as total_funders,
                COUNT(DISTINCT CASE WHEN (SELECT COUNT(DISTINCT creator_address) FROM creator_funders cf2 WHERE cf2.funder_address = creator_funders.funder_address) > 1 THEN funder_address END) as multi_creator_funders
            FROM creator_funders
        """)

        stats = dict(cursor.fetchone())

        conn.close()

        return jsonify({
            'multi_creator_funders': multi_funders,
            'suspicious_only': suspicious_multi_funders,
            'statistics': {
                'total_funders': stats['total_funders'],
                'funding_multiple_creators': len(multi_funders),
                'suspicious_funders': len(suspicious_multi_funders),
                'infra_funders': len([f for f in multi_funders if f['is_infrastructure']]),
                'cex_funders': len([f for f in multi_funders if f['is_cex_account']]),
                'funding_single_creator': stats['total_funders'] - len(multi_funders),
                'percentage_multi_creator': (len(multi_funders) / stats['total_funders'] * 100) if stats['total_funders'] > 0 else 0,
                'coordination_risk': 'HIGH' if len(suspicious_multi_funders) > 0 else 'MEDIUM' if len(multi_funders) > 0 else 'LOW'
            }
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/coordinated-funders')
def coordinated_funders_view():
    """Serve a full webview for coordinated funders analysis"""
    try:
        from infra_mapping import get_account_info, get_cex_info, get_pumpfun_creator_info, get_suspicious_wallet_info

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get funders funding multiple creators
        cursor.execute("""
            SELECT
                funder_address,
                COUNT(DISTINCT creator_address) as creator_count,
                COUNT(*) as funding_record_count,
                SUM(amount_sol) as total_sol_sent,
                MIN(first_detected_at) as first_funding_at,
                MAX(first_detected_at) as last_funding_at,
                MAX(is_cex) as is_cex_flag
            FROM creator_funders
            GROUP BY funder_address
            HAVING COUNT(DISTINCT creator_address) > 1
            ORDER BY creator_count DESC, total_sol_sent DESC
        """)

        all_multi_funders = [dict(row) for row in cursor.fetchall()]

        # Check which funders have been analyzed (have incoming/outgoing transfer data)
        cursor.execute("""
            SELECT DISTINCT funder_address FROM funder_incoming_transfers
            UNION
            SELECT DISTINCT funder_address FROM funder_outgoing_transfers
        """)
        analyzed_funders = set(row[0] for row in cursor.fetchall())

        # Get network information for each funder (can belong to multiple networks)
        funder_networks = {}  # funder_address -> list of networks
        network_id_to_name = {}

        if all_multi_funders:
            cursor.execute("""
                SELECT
                    fnm.funder_address,
                    fn.network_id,
                    COALESCE(fn.network_type, 'shared') as network_type
                FROM funding_network_members fnm
                INNER JOIN funding_networks fn ON fnm.network_id = fn.network_id
                WHERE fnm.funder_address IN ({})
                ORDER BY fn.network_id
            """.format(','.join('?' * len(all_multi_funders))), [f['funder_address'] for f in all_multi_funders])

            # Generate random names for networks using same logic as api_funding_networks_list
            import random
            random.seed(42)  # Consistent seed so names don't change

            adjectives = ['Shadow', 'Ghost', 'Phantom', 'Silent', 'Hidden', 'Dark', 'Swift', 'Rapid',
                         'Sleek', 'Sharp', 'Cunning', 'Sly', 'Stealthy', 'Crafty', 'Clever', 'Subtle',
                         'Veiled', 'Masked', 'Cloaked', 'Whispered', 'Covert', 'Secret', 'Mystic', 'Ancient']
            nouns = ['Circle', 'Ring', 'Syndicate', 'Cabal', 'Order', 'Society', 'Collective', 'Alliance',
                    'Coalition', 'Union', 'Cartel', 'Consortium', 'Federation', 'Network', 'Nexus', 'Web',
                    'Chain', 'Echo', 'Whisper', 'Shadow', 'Phantom', 'Specter', 'Entity', 'Force']

            rows = cursor.fetchall()
            for row in rows:
                network_id = row['network_id']
                network_type = row['network_type']

                # Generate name if not already generated
                if network_id not in network_id_to_name:
                    adj = random.choice(adjectives)
                    noun = random.choice(nouns)
                    network_id_to_name[network_id] = f"{adj} {noun}"

                network_name = network_id_to_name[network_id]
                funder_addr = row['funder_address']

                # Add to list of networks for this funder
                if funder_addr not in funder_networks:
                    funder_networks[funder_addr] = []

                type_badge = ' 🎯' if network_type == 'single_funder' else ''
                funder_networks[funder_addr].append({
                    'network_id': network_id,
                    'network_name': network_name + type_badge,
                    'network_type': network_type
                })

        # Mark analysis status and networks for each funder
        for funder in all_multi_funders:
            funder['is_analyzed'] = funder['funder_address'] in analyzed_funders
            network_list = funder_networks.get(funder['funder_address'], [])
            funder['networks'] = network_list

        # Classify and tag infrastructure/CEX accounts
        suspicious_funders = []
        safe_funders = []

        for funder in all_multi_funders:
            funder_address = funder['funder_address']
            funder_data = dict(funder)
            funder_data['is_infrastructure'] = False
            funder_data['is_cex_account'] = False
            funder_data['account_name'] = None

            # Check if already marked as CEX in database
            if funder['is_cex_flag']:
                funder_data['is_cex_account'] = True

            # Check if it's a known infrastructure account
            infra_info = get_account_info(funder_address)
            if infra_info:
                funder_data['is_infrastructure'] = True
                funder_data['account_name'] = infra_info.get('name')

            # Check if it's a known CEX wallet
            cex_info = get_cex_info(funder_address)
            if cex_info:
                funder_data['is_cex_account'] = True
                funder_data['account_name'] = cex_info.get('name')

            # Classify as suspicious or safe
            if funder_data['is_infrastructure'] or funder_data['is_cex_account']:
                safe_funders.append(funder_data)
            else:
                suspicious_funders.append(funder_data)

        # Get statistics
        cursor.execute("""
            SELECT
                COUNT(DISTINCT funder_address) as total_funders,
                COUNT(DISTINCT CASE WHEN (SELECT COUNT(DISTINCT creator_address) FROM creator_funders cf2 WHERE cf2.funder_address = creator_funders.funder_address) > 1 THEN funder_address END) as multi_creator_funders
            FROM creator_funders
        """)

        stats = dict(cursor.fetchone())
        conn.close()

        # Build suspicious funders table HTML
        suspicious_html = ''
        for funder in suspicious_funders:
            start_date = funder['first_funding_at'][:10] if funder['first_funding_at'] else 'N/A'
            end_date = funder['last_funding_at'][:10] if funder['last_funding_at'] else 'N/A'
            period = start_date if start_date == end_date else f"{start_date} - {end_date}"

            # Get all networks this funder belongs to
            networks = funder.get('networks', [])
            if networks:
                network_display = ', '.join([n['network_name'] for n in networks])
            else:
                network_display = '—'

            # Analysis status indicator
            analysis_badge = '✅ Analyzed' if funder['is_analyzed'] else '⏳ Pending'
            analysis_color = '#4ade80' if funder['is_analyzed'] else '#fbbf24'

            # Highlight duplicate creators (high priority) - red background for high creator counts
            row_highlight = 'background: rgba(255, 0, 0, 0.08);' if funder['creator_count'] > 3 else ''

            suspicious_html += f"""
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05); cursor: pointer; {row_highlight}" onclick="window.location.href = '/funder-details/{funder['funder_address']}'">
                <td style="padding: 12px; font-family: monospace; font-size: 12px; color: #ef4444; word-break: break-all;">{funder['funder_address']}</td>
                <td style="padding: 12px; color: #00d4ff; font-weight: 500; font-size: 12px;">{network_display}</td>
                <td style="padding: 12px; color: #ef4444; font-weight: bold;">{funder['creator_count']}</td>
                <td style="padding: 12px; color: #4ade80;">{funder['total_sol_sent']:.2f}</td>
                <td style="padding: 12px; color: #a0a0a0;">{funder['funding_record_count']}</td>
                <td style="padding: 12px; font-size: 11px; color: {analysis_color};">{analysis_badge}</td>
                <td style="padding: 12px; font-size: 11px; color: #a0a0a0;">{period}</td>
            </tr>
            """

        # Build safe funders table HTML
        safe_html = ''
        for funder in safe_funders:
            start_date = funder['first_funding_at'][:10] if funder['first_funding_at'] else 'N/A'
            end_date = funder['last_funding_at'][:10] if funder['last_funding_at'] else 'N/A'
            period = start_date if start_date == end_date else f"{start_date} - {end_date}"
            account_type = 'CEX' if funder['is_cex_account'] else 'INFRA'
            account_label = funder['account_name'] if funder['account_name'] else ''

            safe_html += f"""
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                <td style="padding: 12px; font-family: monospace; font-size: 12px; color: #a0a0a0;">{funder['funder_address']}</td>
                <td style="padding: 12px; color: #4ade80; font-weight: 600;">{account_label}</td>
                <td style="padding: 12px; text-align: center;"><span style="background: rgba(34, 197, 94, 0.2); color: #4ade80; padding: 3px 8px; border-radius: 3px; font-size: 11px;">{account_type}</span></td>
                <td style="padding: 12px; color: #a0a0a0; font-weight: bold;">{funder['creator_count']}</td>
                <td style="padding: 12px; color: #a0a0a0;">{funder['total_sol_sent']:.2f}</td>
            </tr>
            """

        html = f"""
        <html>
            <head>
                <title>Coordinated Funders Analysis</title>
                <style>
                    body {{
                        background: #0a0e27;
                        color: #e0e0e0;
                        font-family: 'Segoe UI', sans-serif;
                        margin: 0;
                        padding: 20px;
                    }}
                    .container {{
                        max-width: 1400px;
                        margin: 0 auto;
                    }}
                    h1 {{
                        color: #00d4ff;
                        margin-bottom: 10px;
                    }}
                    .subtitle {{
                        color: #a0a0a0;
                        margin-bottom: 30px;
                        font-size: 14px;
                    }}
                    .back-link {{
                        margin-bottom: 20px;
                    }}
                    .back-link a {{
                        color: #00d4ff;
                        text-decoration: none;
                    }}
                    .back-link a:hover {{
                        text-decoration: underline;
                    }}
                    .stats-grid {{
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                        gap: 15px;
                        margin-bottom: 30px;
                    }}
                    .stat-box {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 20px;
                        border-radius: 8px;
                        border-left: 3px solid;
                        text-align: center;
                    }}
                    .stat-box.suspicious {{
                        border-left-color: #ef4444;
                    }}
                    .stat-box.safe {{
                        border-left-color: #4ade80;
                    }}
                    .stat-label {{
                        color: #a0a0a0;
                        font-size: 11px;
                        text-transform: uppercase;
                        margin-bottom: 10px;
                    }}
                    .stat-value {{
                        font-size: 32px;
                        font-weight: bold;
                    }}
                    .stat-box.suspicious .stat-value {{
                        color: #ef4444;
                    }}
                    .stat-box.safe .stat-value {{
                        color: #4ade80;
                    }}
                    .section {{
                        background: rgba(0, 0, 0, 0.2);
                        border-radius: 8px;
                        margin-bottom: 30px;
                        overflow: hidden;
                    }}
                    .section-title {{
                        background: rgba(0, 0, 0, 0.4);
                        padding: 15px;
                        border-bottom: 1px solid rgba(0, 212, 255, 0.2);
                        font-weight: 600;
                        color: #00d4ff;
                    }}
                    .section-content {{
                        max-height: 800px;
                        overflow-y: auto;
                    }}
                    table {{
                        width: 100%;
                        border-collapse: collapse;
                        font-size: 13px;
                    }}
                    th {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 12px;
                        text-align: left;
                        color: #a0a0a0;
                        font-size: 11px;
                        border-bottom: 1px solid rgba(0, 212, 255, 0.2);
                    }}
                    tr:hover {{
                        background: rgba(0, 212, 255, 0.05);
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="back-link"><a href="/">← Back to Dashboard</a></div>
                    <h1>Coordinated Funders Analysis</h1>
                    <div class="subtitle">Funders supporting multiple token creators (potential coordination risk)</div>

                    <!-- Tab Navigation -->
                    <div style="display: flex; gap: 10px; margin-bottom: 20px; border-bottom: 1px solid rgba(0, 212, 255, 0.2); padding-bottom: 15px; flex-wrap: wrap;">
                        <button onclick="switchTab('funders')" id="tab-funders" style="background: rgba(0, 212, 255, 0.2); color: #00d4ff; border: 1px solid #00d4ff; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;">👥 Multi-Creator Funders</button>
                        <button onclick="switchTab('senders')" id="tab-senders" style="background: transparent; color: #a0a0a0; border: 1px solid #a0a0a0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;">📤 Duplicate Senders</button>
                        <button onclick="switchTab('tokens')" id="tab-tokens" style="background: transparent; color: #a0a0a0; border: 1px solid #a0a0a0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;">🪙 Coordinated Tokens</button>
                        <button onclick="switchTab('funder-networks')" id="tab-funder-networks" style="background: transparent; color: #a0a0a0; border: 1px solid #a0a0a0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;">🔗 Funder Networks</button>
                        <button onclick="switchTab('funding-networks')" id="tab-funding-networks" style="background: transparent; color: #a0a0a0; border: 1px solid #a0a0a0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;">🌐 Funding Networks</button>
                    </div>

                    <script>
                    function switchTab(tabName) {{
                        // Hide all tabs
                        document.getElementById('funders-tab').style.display = 'none';
                        document.getElementById('senders-tab').style.display = 'none';
                        document.getElementById('tokens-tab').style.display = 'none';
                        document.getElementById('funder-networks-tab').style.display = 'none';
                        document.getElementById('funding-networks-tab').style.display = 'none';

                        // Remove active state from all tabs
                        document.getElementById('tab-funders').style.background = 'transparent';
                        document.getElementById('tab-funders').style.color = '#a0a0a0';
                        document.getElementById('tab-funders').style.borderColor = '#a0a0a0';
                        document.getElementById('tab-senders').style.background = 'transparent';
                        document.getElementById('tab-senders').style.color = '#a0a0a0';
                        document.getElementById('tab-senders').style.borderColor = '#a0a0a0';
                        document.getElementById('tab-tokens').style.background = 'transparent';
                        document.getElementById('tab-tokens').style.color = '#a0a0a0';
                        document.getElementById('tab-tokens').style.borderColor = '#a0a0a0';
                        document.getElementById('tab-funder-networks').style.background = 'transparent';
                        document.getElementById('tab-funder-networks').style.color = '#a0a0a0';
                        document.getElementById('tab-funder-networks').style.borderColor = '#a0a0a0';
                        document.getElementById('tab-funding-networks').style.background = 'transparent';
                        document.getElementById('tab-funding-networks').style.color = '#a0a0a0';
                        document.getElementById('tab-funding-networks').style.borderColor = '#a0a0a0';

                        // Show selected tab
                        document.getElementById(tabName + '-tab').style.display = 'block';

                        // Set active state
                        if (tabName === 'funders') {{
                            document.getElementById('tab-funders').style.background = 'rgba(0, 212, 255, 0.2)';
                            document.getElementById('tab-funders').style.color = '#00d4ff';
                            document.getElementById('tab-funders').style.borderColor = '#00d4ff';
                        }} else if (tabName === 'senders') {{
                            document.getElementById('tab-senders').style.background = 'rgba(251, 191, 36, 0.2)';
                            document.getElementById('tab-senders').style.color = '#fbbf24';
                            document.getElementById('tab-senders').style.borderColor = '#fbbf24';
                            if (!document.getElementById('senders-content').innerHTML) {{
                                loadDuplicateSenders();
                            }}
                        }} else if (tabName === 'tokens') {{
                            document.getElementById('tab-tokens').style.background = 'rgba(34, 197, 94, 0.2)';
                            document.getElementById('tab-tokens').style.color = '#4ade80';
                            document.getElementById('tab-tokens').style.borderColor = '#4ade80';
                            if (!document.getElementById('tokens-content').innerHTML) {{
                                loadDuplicateTokens();
                            }}
                        }} else if (tabName === 'funder-networks') {{
                            document.getElementById('tab-funder-networks').style.background = 'rgba(59, 130, 246, 0.2)';
                            document.getElementById('tab-funder-networks').style.color = '#3b82f6';
                            document.getElementById('tab-funder-networks').style.borderColor = '#3b82f6';
                            if (!document.getElementById('funder-networks-content').innerHTML) {{
                                loadFunderNetworks();
                            }}
                        }} else if (tabName === 'funding-networks') {{
                            document.getElementById('tab-funding-networks').style.background = 'rgba(99, 102, 241, 0.2)';
                            document.getElementById('tab-funding-networks').style.color = '#6366f1';
                            document.getElementById('tab-funding-networks').style.borderColor = '#6366f1';
                            if (!document.getElementById('funding-networks-content').innerHTML) {{
                                loadFundingNetworks();
                            }}
                            startNetworkPolling();  // Start polling when networks tab is active
                        }} else {{
                            stopNetworkPolling();  // Stop polling when switching away from networks
                        }}
                    }}

                    async function loadDuplicateSenders() {{
                        const statusEl = document.getElementById('senders-status');
                        statusEl.textContent = '⟲ Loading duplicate senders...';

                        try {{
                            const response = await fetch('/api/duplicate-senders');
                            const data = await response.json();

                            if (data.error) {{
                                statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            // Build senders table HTML
                            let html = `
                                <div class="section">
                                    <div class="section-title">📤 Duplicate Senders - Sending to Multiple Funders (${{data.total_duplicate_senders}} total)</div>
                                    <div class="section-content">
                                        <table>
                                            <thead>
                                                <tr>
                                                    <th>Sender Address</th>
                                                    <th>Funders Sent To</th>
                                                    <th>Total Transfers</th>
                                                    <th>Total SOL</th>
                                                    <th>Related Tokens</th>
                                                    <th>Period</th>
                                                </tr>
                                            </thead>
                                            <tbody>`;

                            if (data.senders.length === 0) {{
                                html += '<tr><td colspan="6" style="padding: 20px; text-align: center; color: #a0a0a0;">No duplicate senders found</td></tr>';
                            }} else {{
                                data.senders.forEach(sender => {{
                                    const firstDate = new Date(sender.first_seen * 1000).toISOString().substring(0, 10);
                                    const lastDate = new Date(sender.last_seen * 1000).toISOString().substring(0, 10);
                                    const period = firstDate === lastDate ? firstDate : firstDate + ' - ' + lastDate;
                                    const rowHighlight = sender.funder_count > 10 ? 'background: rgba(251, 191, 36, 0.1);' : '';

                                    html += `
                                        <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05); ${{rowHighlight}}">
                                            <td style="padding: 12px; font-family: monospace; font-size: 11px; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                                <a href="#" onclick="showSenderTokens('${{sender.sender_address}}'); return false;" style="color: #fbbf24; text-decoration: none; cursor: pointer;" title="${{sender.sender_address}}">${{sender.sender_address}}</a>
                                            </td>
                                            <td style="padding: 12px; color: #ef4444; font-weight: bold;">${{sender.funder_count}}</td>
                                            <td style="padding: 12px; color: #a0a0a0;">${{sender.transfer_count}}</td>
                                            <td style="padding: 12px; color: #4ade80;">${{sender.total_sol.toFixed(2)}}</td>
                                            <td style="padding: 12px; color: #a0a0a0; font-weight: bold;">${{sender.related_token_count || 0}}</td>
                                            <td style="padding: 12px; font-size: 11px; color: #a0a0a0;">${{period}}</td>
                                        </tr>`;
                                }});
                            }}

                            html += `
                                            </tbody>
                                        </table>
                                    </div>
                                </div>`;

                            document.getElementById('senders-content').innerHTML = html;
                            statusEl.textContent = '✅ Loaded ' + data.total_duplicate_senders + ' duplicate senders';
                        }} catch(e) {{
                            statusEl.textContent = '❌ Error: ' + e.message;
                        }}
                    }}

                    async function loadDuplicateTokens() {{
                        const statusEl = document.getElementById('tokens-status');
                        statusEl.textContent = '⟲ Loading coordinated tokens...';

                        try {{
                            const response = await fetch('/api/duplicate-tokens');
                            const data = await response.json();

                            if (data.error) {{
                                statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            // Build tokens table HTML
                            let html = `
                                <div class="section">
                                    <div class="section-title">🪙 Coordinated Tokens - Funded by Multiple Senders (${{data.total_duplicate_tokens}} total)</div>
                                    <div class="section-content">
                                        <table>
                                            <thead>
                                                <tr>
                                                    <th>Token Mint</th>
                                                    <th>Creator</th>
                                                    <th>Created</th>
                                                    <th>Num Senders</th>
                                                    <th>Num Funders</th>
                                                    <th>Total SOL</th>
                                                    <th>Risk</th>
                                                    <th>Rug %</th>
                                                </tr>
                                            </thead>
                                            <tbody>`;

                            if (data.tokens.length === 0) {{
                                html += '<tr><td colspan="8" style="padding: 20px; text-align: center; color: #a0a0a0;">No coordinated tokens found</td></tr>';
                            }} else {{
                                data.tokens.forEach(token => {{
                                    const createdDate = new Date(token.created_at).toISOString().substring(0, 10);
                                    const riskColor = token.risk_level === 'HIGH' ? '#ef4444' : token.risk_level === 'MEDIUM' ? '#f59e0b' : '#4ade80';
                                    const senderHighlight = token.num_senders > 100 ? 'background: rgba(239, 68, 68, 0.15);' : token.num_senders > 50 ? 'background: rgba(245, 158, 11, 0.15);' : '';

                                    html += `
                                        <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05); ${{senderHighlight}}">
                                            <td style="padding: 12px; font-family: monospace; font-size: 10px; word-break: break-all; color: #4ade80;">
                                                <a href="https://solscan.io/token/${{token.mint}}" target="_blank" style="color: #4ade80; text-decoration: none;">${{token.mint}}</a>
                                            </td>
                                            <td style="padding: 12px; font-family: monospace; font-size: 10px; word-break: break-all; color: #a0a0a0;">${{token.creator}}</td>
                                            <td style="padding: 12px; font-size: 11px; color: #a0a0a0;">${{createdDate}}</td>
                                            <td style="padding: 12px; color: #ef4444; font-weight: bold; text-align: center;">${{token.num_senders}}</td>
                                            <td style="padding: 12px; color: #fbbf24; font-weight: bold; text-align: center;">${{token.num_funders}}</td>
                                            <td style="padding: 12px; color: #4ade80;">${{token.total_sol.toFixed(2)}}</td>
                                            <td style="padding: 12px; color: ${{riskColor}}; font-weight: bold;">${{token.risk_level || 'N/A'}}</td>
                                            <td style="padding: 12px; color: #f59e0b;">${{((token.rug_probability || 0) * 100).toFixed(1)}}%</td>
                                        </tr>`;
                                }});
                            }}

                            html += `
                                            </tbody>
                                        </table>
                                    </div>
                                </div>`;

                            document.getElementById('tokens-content').innerHTML = html;
                            statusEl.textContent = '✅ Loaded ' + data.total_duplicate_tokens + ' coordinated tokens';
                        }} catch(e) {{
                            statusEl.textContent = '❌ Error: ' + e.message;
                        }}
                    }}

                    async function loadFunderNetworks() {{
                        const statusEl = document.getElementById('funder-networks-status');
                        statusEl.textContent = '⟲ Loading funder networks...';

                        try {{
                            const response = await fetch('/api/funder-networks');
                            const data = await response.json();

                            if (data.error) {{
                                statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            // Build funders table HTML
                            let html = `
                                <div class="section">
                                    <div class="section-title">🔗 Funder Networks - All Funders with Network Info (${{data.total_funders}} total)</div>
                                    <div class="section-content">
                                        <table style="width: 100%; border-collapse: collapse;">
                                            <thead style="background: rgba(0, 0, 0, 0.3);">
                                                <tr>
                                                    <th style="padding: 12px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: #a0a0a0; font-size: 11px;">Funder Address</th>
                                                    <th style="padding: 12px; text-align: center; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: #a0a0a0; font-size: 11px;">Tokens</th>
                                                    <th style="padding: 12px; text-align: center; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: #a0a0a0; font-size: 11px;">Creators</th>
                                                    <th style="padding: 12px; text-align: center; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: #a0a0a0; font-size: 11px;">Senders</th>
                                                    <th style="padding: 12px; text-align: right; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: #a0a0a0; font-size: 11px;">SOL In/Out</th>
                                                    <th style="padding: 12px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: #a0a0a0; font-size: 11px;">Period</th>
                                                </tr>
                                            </thead>
                                            <tbody>`;

                            if (data.funders.length === 0) {{
                                html += '<tr><td colspan="6" style="padding: 20px; text-align: center; color: #a0a0a0;">No funder networks found</td></tr>';
                            }} else {{
                                data.funders.forEach(funder => {{
                                    const startDate = new Date(funder.earliest_funding * 1000).toISOString().substring(0, 10);
                                    const endDate = new Date(funder.latest_funding * 1000).toISOString().substring(0, 10);
                                    const period = startDate === endDate ? startDate : startDate + ' - ' + endDate;
                                    const networkHighlight = funder.tokens_funded > 10 ? 'background: rgba(59, 130, 246, 0.15);' : '';

                                    html += `
                                        <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05); ${{networkHighlight}}">
                                            <td style="padding: 12px; font-family: monospace; font-size: 10px; word-break: break-all; color: #3b82f6;">
                                                <a href="#" onclick="showFunderTokens('${{funder.funder_address}}'); return false;" style="color: #3b82f6; text-decoration: none; cursor: pointer;">${{funder.funder_address}}</a>
                                            </td>
                                            <td style="padding: 12px; text-align: center; color: #4ade80; font-weight: bold;">${{funder.tokens_funded}}</td>
                                            <td style="padding: 12px; text-align: center; color: #a0a0a0;">${{funder.creators_funded}}</td>
                                            <td style="padding: 12px; text-align: center; color: #fbbf24;">${{funder.num_senders || 0}}</td>
                                            <td style="padding: 12px; text-align: right; color: #f59e0b; font-size: 10px;">${{funder.total_sol_in ? funder.total_sol_in.toFixed(2) : '0'}} / ${{funder.total_sol_out.toFixed(2)}}</td>
                                            <td style="padding: 12px; font-size: 10px; color: #a0a0a0;">${{period}}</td>
                                        </tr>`;
                                }});
                            }}

                            html += `
                                            </tbody>
                                        </table>
                                    </div>
                                </div>`;

                            document.getElementById('funder-networks-content').innerHTML = html;
                            statusEl.textContent = '✅ Loaded ' + data.total_funders + ' funder networks';
                        }} catch(e) {{
                            statusEl.textContent = '❌ Error: ' + e.message;
                        }}
                    }}

                    async function showFunderTokens(funderAddress) {{
                        const modal = document.getElementById('funderTokensModal');
                        if (!modal) {{
                            alert('Click on a funder address to see its coordinated tokens');
                            return;
                        }}

                        document.getElementById('modalFunderAddress').textContent = funderAddress;
                        const statusEl = document.getElementById('funderTokensStatus');
                        statusEl.textContent = '⟲ Loading tokens...';

                        try {{
                            const response = await fetch(`/api/tokens-by-funder/${{funderAddress}}`);
                            const data = await response.json();

                            if (data.error) {{
                                statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            let html = `<table style="width: 100%; border-collapse: collapse;">
                                <thead style="background: rgba(0, 0, 0, 0.3);">
                                    <tr>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: #a0a0a0; font-size: 11px;">Token Mint</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: #a0a0a0; font-size: 11px;">Creator</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: #a0a0a0; font-size: 11px;">Created</th>
                                        <th style="padding: 10px; text-align: right; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: #a0a0a0; font-size: 11px;">SOL</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: #a0a0a0; font-size: 11px;">Senders</th>
                                    </tr>
                                </thead>
                                <tbody>`;

                            if (data.tokens.length === 0) {{
                                html += '<tr><td colspan="5" style="padding: 20px; text-align: center; color: #a0a0a0;">No tokens found</td></tr>';
                            }} else {{
                                data.tokens.forEach(token => {{
                                    const createdDate = new Date(token.created_at).toISOString().substring(0, 10);
                                    html += `
                                        <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                                            <td style="padding: 10px; font-family: monospace; font-size: 10px; color: #4ade80;">
                                                <a href="https://solscan.io/token/${{token.mint}}" target="_blank" style="color: #4ade80; text-decoration: none;">${{token.mint.substring(0, 20)}}...</a>
                                            </td>
                                            <td style="padding: 10px; font-family: monospace; font-size: 10px; color: #a0a0a0; word-break: break-all;">${{token.creator.substring(0, 20)}}...</td>
                                            <td style="padding: 10px; font-size: 10px; color: #a0a0a0;">${{createdDate}}</td>
                                            <td style="padding: 10px; color: #4ade80; font-weight: bold; text-align: right;">${{token.amount_sol ? token.amount_sol.toFixed(2) : '0'}}</td>
                                            <td style="padding: 10px; color: #fbbf24; font-weight: bold;">${{token.num_senders || 0}}</td>
                                        </tr>`;
                                }});
                            }}

                            html += '</tbody></table>';

                            const tokensContainer = document.getElementById('funderTokensContainer');
                            tokensContainer.innerHTML = html;
                            statusEl.textContent = `✅ Showing ${{data.total_tokens}} tokens funded by this funder`;
                            modal.style.display = 'block';

                        }} catch(error) {{
                            console.error('Error loading funder tokens:', error);
                            statusEl.textContent = '❌ Failed to load tokens';
                        }}
                    }}

                    async function loadFundingNetworks() {{
                        const gridEl = document.getElementById('funding-networks-grid');
                        const statusEl = document.getElementById('funding-networks-status');

                        if (!gridEl) {{
                            console.error('funding-networks-grid element not found');
                            return;
                        }}

                        if (statusEl) {{
                            statusEl.textContent = '⟲ Loading networks...';
                        }}

                        try {{
                            const response = await fetch('/api/funding-networks-list');
                            const data = await response.json();

                            if (data.error) {{
                                if (statusEl) statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            let html = `<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 15px;">`;

                            data.networks.forEach(network => {{
                                html += `
                                    <div style="background: rgba(0, 0, 0, 0.3); border: 1px solid #6366f1; border-radius: 8px; padding: 15px; cursor: pointer; transition: all 0.3s;"
                                         onclick="showNetworkDetails(${{network.network_id}})"
                                         onmouseover="this.style.background='rgba(99, 102, 241, 0.15)'; this.style.boxShadow='0 0 15px rgba(99, 102, 241, 0.5)';"
                                         onmouseout="this.style.background='rgba(0, 0, 0, 0.3)'; this.style.boxShadow='none';">
                                        <div style="font-weight: bold; color: #e0e0e0; font-size: 14px; margin-bottom: 12px;">${{network.name}}</div>
                                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 11px;">
                                            <div style="background: rgba(74, 222, 128, 0.1); padding: 8px; border-radius: 4px; border-left: 2px solid #4ade80;">
                                                <div style="color: #a0a0a0;">Funders</div>
                                                <div style="color: #4ade80; font-weight: bold;">${{network.funders}}</div>
                                            </div>
                                            <div style="background: rgba(59, 130, 246, 0.1); padding: 8px; border-radius: 4px; border-left: 2px solid #3b82f6;">
                                                <div style="color: #a0a0a0;">Senders</div>
                                                <div style="color: #3b82f6; font-weight: bold;">${{network.senders}}</div>
                                            </div>
                                            <div style="background: rgba(245, 158, 11, 0.1); padding: 8px; border-radius: 4px; border-left: 2px solid #f59e0b;">
                                                <div style="color: #a0a0a0;">Creators</div>
                                                <div style="color: #f59e0b; font-weight: bold;">${{network.creators}}</div>
                                            </div>
                                            <div style="background: rgba(59, 130, 246, 0.1); padding: 8px; border-radius: 4px; border-left: 2px solid #3b82f6;">
                                                <div style="color: #a0a0a0;">Tokens</div>
                                                <div style="color: #3b82f6; font-weight: bold;">${{network.tokens}}</div>
                                            </div>
                                            <div style="background: rgba(168, 85, 247, 0.1); padding: 8px; border-radius: 4px; border-left: 2px solid #a855f7;">
                                                <div style="color: #a0a0a0;">SOL</div>
                                                <div style="color: #a855f7; font-weight: bold;">${{network.total_sol.toFixed(0)}}</div>
                                            </div>
                                        </div>
                                    </div>`;
                            }});

                            html += `</div>`;

                            gridEl.innerHTML = html;
                            if (statusEl) statusEl.textContent = '✅ Loaded ' + data.total_networks + ' networks';
                        }} catch(e) {{
                            console.error('Error loading networks:', e);
                            if (statusEl) statusEl.textContent = '❌ Error: ' + e.message;
                        }}
                    }}

                    async function showNetworkDetails(networkId) {{
                        const gridEl = document.getElementById('funding-networks-grid');
                        const statusEl = document.getElementById('funding-networks-status');

                        if (!gridEl) {{
                            console.error('funding-networks-grid element not found');
                            return;
                        }}

                        if (statusEl) {{
                            statusEl.textContent = '⟲ Loading details...';
                        }}

                        try {{
                            const response = await fetch(`/api/funding-network-details/${{networkId}}`);
                            const data = await response.json();

                            if (data.error) {{
                                if (statusEl) statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            let html = `
                                <div style="margin-bottom: 20px;">
                                    <button onclick="loadFundingNetworks()" style="background: rgba(99, 102, 241, 0.2); color: #6366f1; border: 1px solid #6366f1; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 12px;">← Back to Networks</button>
                                </div>
                                <div style="background: rgba(99, 102, 241, 0.1); border-left: 3px solid #6366f1; border-radius: 6px; padding: 20px; margin-bottom: 20px;">
                                    <h2 style="color: #6366f1; margin: 0 0 15px 0;">${{data.network_name}} Details</h2>
                                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px;">
                                        <div>
                                            <div style="font-size: 12px; color: #a0a0a0; margin-bottom: 5px;">FUNDERS</div>
                                            <div style="font-size: 28px; font-weight: bold; color: #4ade80;">${{data.funders}}</div>
                                        </div>
                                        <div>
                                            <div style="font-size: 12px; color: #a0a0a0; margin-bottom: 5px;">SENDERS</div>
                                            <div style="font-size: 28px; font-weight: bold; color: #3b82f6;">${{data.senders}}</div>
                                        </div>
                                        <div>
                                            <div style="font-size: 12px; color: #a0a0a0; margin-bottom: 5px;">CREATORS</div>
                                            <div style="font-size: 28px; font-weight: bold; color: #f59e0b;">${{data.creators}}</div>
                                        </div>
                                        <div>
                                            <div style="font-size: 12px; color: #a0a0a0; margin-bottom: 5px;">TOKENS</div>
                                            <div style="font-size: 28px; font-weight: bold; color: #a855f7;">${{data.tokens}}</div>
                                        </div>
                                        <div>
                                            <div style="font-size: 12px; color: #a0a0a0; margin-bottom: 5px;">TOTAL SOL</div>
                                            <div style="font-size: 28px; font-weight: bold; color: #ec4899;">${{data.total_sol.toFixed(0)}}</div>
                                        </div>
                                    </div>
                                </div>

                                <div style="background: rgba(0, 0, 0, 0.2); border-radius: 6px; padding: 20px;">
                                    <h3 style="color: #e0e0e0; margin: 0 0 15px 0;">Tokens Coordinated</h3>
                                    <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 10px;">`;

                            data.token_list.forEach(token => {{
                                html += `
                                    <div style="background: rgba(99, 102, 241, 0.05); padding: 10px; border-radius: 4px; border-left: 2px solid #6366f1; font-family: monospace; font-size: 10px; word-break: break-all; color: #a0a0a0;">
                                        ${{token}}
                                    </div>`;
                            }});

                            html += `</div></div>`;

                            // Add Root Operator Flows section
                            if (data.root_operator_flows && data.root_operator_flows.length > 0) {{
                                html += `<div style="background: rgba(0, 0, 0, 0.2); border-radius: 6px; padding: 20px; margin-top: 20px;">
                                    <h3 style="color: #e0e0e0; margin: 0 0 15px 0;">Root Operators & Address Flows</h3>
                                    <div style="display: grid; grid-template-columns: 1fr; gap: 15px;">`;

                                data.root_operator_flows.forEach((flow, idx) => {{
                                    html += `<div style="background: rgba(99, 102, 241, 0.08); border-left: 3px solid #6366f1; border-radius: 6px; padding: 15px;">
                                        <div style="margin-bottom: 12px;">
                                            <div style="font-size: 11px; color: #a0a0a0; margin-bottom: 5px;">ROOT OPERATOR #${{idx + 1}}</div>
                                            <div style="font-family: monospace; font-size: 12px; color: #6366f1; word-break: break-all; padding: 8px; background: rgba(99, 102, 241, 0.1); border-radius: 4px;">${{flow.root_operator}}</div>
                                        </div>
                                        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 12px;">
                                            <div>
                                                <div style="font-size: 10px; color: #a0a0a0;">CREATORS FUNDED</div>
                                                <div style="font-size: 18px; font-weight: bold; color: #f59e0b;">${{flow.creators_funded}}</div>
                                            </div>
                                            <div>
                                                <div style="font-size: 10px; color: #a0a0a0;">TOTAL SOL</div>
                                                <div style="font-size: 18px; font-weight: bold; color: #4ade80;">${{flow.total_sol_sent.toFixed(2)}}</div>
                                            </div>
                                        </div>
                                        <div style="margin-bottom: 12px;">
                                            <div style="font-size: 11px; color: #a0a0a0; margin-bottom: 5px;">UPSTREAM SOURCES</div>
                                            <div style="background: rgba(0, 0, 0, 0.3); border-radius: 4px; padding: 8px; max-height: 80px; overflow-y: auto;">`;

                                    if (flow.upstream_sources.length > 0) {{
                                        flow.upstream_sources.forEach(source => {{
                                            html += `<div style="font-family: monospace; font-size: 10px; color: #3b82f6; word-break: break-all; padding: 4px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.05);">${{source.sender}}</div>`;
                                        }});
                                    }} else {{
                                        html += `<div style="color: #a0a0a0; font-size: 10px;">No upstream sources found</div>`;
                                    }}

                                    html += `</div></div>
                                        <div style="font-size: 11px; color: #a0a0a0; margin-bottom: 5px;">EXAMPLE ADDRESS FLOWS</div>
                                        <div style="background: rgba(0, 0, 0, 0.3); border-radius: 4px; padding: 8px; max-height: 100px; overflow-y: auto;">`;

                                    if (flow.example_flows && flow.example_flows.length > 0) {{
                                        flow.example_flows.forEach(ex => {{
                                            html += `<div style="font-family: monospace; font-size: 9px; color: #e0e0e0; padding: 4px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.05); line-height: 1.4;">
                                                <div style="color: #3b82f6;">${{ex.sender.substring(0, 12)}}...</div>
                                                <div style="color: #a0a0a0; margin-left: 10px;">↓ (to funder)</div>
                                                <div style="color: #6366f1;">${{ex.funder.substring(0, 12)}}...</div>
                                                <div style="color: #a0a0a0; margin-left: 10px;">↓ ${{{ex.sol_to_creator.toFixed(2)}}} SOL</div>
                                                <div style="color: #f59e0b;">${{ex.creator.substring(0, 12)}}...</div>
                                            </div>`;
                                        }});
                                    }} else {{
                                        html += `<div style="color: #a0a0a0; font-size: 10px;">No flows available</div>`;
                                    }}

                                    html += `</div>
                                        <div style="font-size: 11px; color: #a0a0a0; margin-bottom: 5px; margin-top: 12px;">TOKENS CREATED BY FUNDED CREATORS</div>
                                        <div style="background: rgba(0, 0, 0, 0.3); border-radius: 4px; padding: 8px; max-height: 120px; overflow-y: auto;">`;

                                    if (flow.downstream_creators.length > 0) {{
                                        flow.downstream_creators.forEach(creator => {{
                                            const riskColor = creator.risk_level === 'HIGH' ? '#ef4444' : creator.risk_level === 'MEDIUM' ? '#f59e0b' : '#4ade80';
                                            html += `
                                                <div style="padding: 6px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.05); display: flex; justify-content: space-between; align-items: center;">
                                                    <div style="font-family: monospace; font-size: 9px; color: #a0a0a0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${{creator.mint}}">${{creator.mint.substring(0, 16)}}...</div>
                                                    <div style="color: ${{riskColor}}; font-weight: bold; font-size: 10px; margin-left: 8px;">${{(creator.rug_probability * 100).toFixed(0)}}%</div>
                                                </div>`;
                                        }});
                                    }} else {{
                                        html += `<div style="color: #a0a0a0; font-size: 10px;">No tokens found</div>`;
                                    }}

                                    html += `</div>
                                    </div>`;
                                }});

                                html += `</div></div>`;
                            }}

                            gridEl.innerHTML = html;
                            if (statusEl) statusEl.textContent = '✅ Network details loaded';
                        }} catch(e) {{
                            console.error('Error loading network details:', e);
                            if (statusEl) statusEl.textContent = '❌ Error: ' + e.message;
                        }}
                    }}

                    function closeNetworkDetails() {{
                        loadFundingNetworks();
                    }}

                    async function showSenderTokens(senderAddress) {{
                        const modal = document.getElementById('senderTokensModal');
                        if (!modal) return;

                        document.getElementById('modalSenderAddress').textContent = senderAddress;
                        const statusEl = document.getElementById('senderTokensStatus');
                        statusEl.textContent = '⟲ Loading tokens...';

                        try {{
                            const response = await fetch(`/api/sender-tokens/${{senderAddress}}`);
                            const data = await response.json();

                            if (data.error) {{
                                statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            let html = `<table style="width: 100%; border-collapse: collapse;">
                                <thead style="background: rgba(0, 0, 0, 0.3);">
                                    <tr>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(0, 212, 255, 0.2); color: #a0a0a0; font-size: 12px;">Token Mint</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(0, 212, 255, 0.2); color: #a0a0a0; font-size: 12px;">Creator</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(0, 212, 255, 0.2); color: #a0a0a0; font-size: 12px;">Funding (SOL)</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(0, 212, 255, 0.2); color: #a0a0a0; font-size: 12px;">Risk</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(0, 212, 255, 0.2); color: #a0a0a0; font-size: 12px;">Rug %</th>
                                    </tr>
                                </thead>
                                <tbody>`;

                            if (data.tokens.length === 0) {{
                                html += '<tr><td colspan="5" style="padding: 20px; text-align: center; color: #a0a0a0;">No tokens found</td></tr>';
                            }} else {{
                                data.tokens.forEach(token => {{
                                    const riskColor = token.risk_level === 'HIGH' ? '#ef4444' : token.risk_level === 'MEDIUM' ? '#f59e0b' : '#4ade80';
                                    html += `
                                        <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                                            <td style="padding: 10px; font-family: monospace; font-size: 11px; color: #4ade80; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                                <a href="https://solscan.io/token/${{token.mint}}" target="_blank" style="color: #4ade80; text-decoration: none;" title="${{token.mint}}">${{token.mint}}</a>
                                            </td>
                                            <td style="padding: 10px; font-family: monospace; font-size: 11px; color: #a0a0a0; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${{token.creator_address}}">${{token.creator_address}}</td>
                                            <td style="padding: 10px; color: #4ade80; font-weight: bold;">${{token.total_funding_sol.toFixed(2)}}</td>
                                            <td style="padding: 10px; color: ${{riskColor}}; font-weight: bold;">${{token.risk_level || 'N/A'}}</td>
                                            <td style="padding: 10px; color: #f59e0b;">${{(token.rug_probability * 100).toFixed(1)}}%</td>
                                        </tr>`;
                                }});
                            }}

                            html += '</tbody></table>';

                            const tokensContainer = document.getElementById('senderTokensContainer');
                            tokensContainer.innerHTML = html;
                            statusEl.textContent = `✅ Showing ${{data.total_tokens}} tokens`;
                            modal.style.display = 'block';

                        }} catch(error) {{
                            console.error('Error loading sender tokens:', error);
                            statusEl.textContent = '❌ Failed to load tokens';
                        }}
                    }}

                    function closeSenderTokens() {{
                        const modal = document.getElementById('senderTokensModal');
                        if (modal) modal.style.display = 'none';
                    }}

                    function closeFunderTokens() {{
                        const modal = document.getElementById('funderTokensModal');
                        if (modal) modal.style.display = 'none';
                    }}

                    window.onclick = function(event) {{
                        const senderModal = document.getElementById('senderTokensModal');
                        const funderModal = document.getElementById('funderTokensModal');
                        if (event.target === senderModal) {{
                            senderModal.style.display = 'none';
                        }}
                        if (event.target === funderModal) {{
                            funderModal.style.display = 'none';
                        }}
                    }}
                    </script>

                    <!-- Funders Tab -->
                    <div id="funders-tab" style="display: block;">
                        <div style="margin-bottom: 20px;">
                            <button onclick="analyzeAllFunders()" style="background: rgba(76, 175, 80, 0.2); color: #4ade80; border: 1px solid #4ade80; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600;">🔍 Analyze All Funders</button>
                            <span id="analysis-status" style="margin-left: 15px; color: #a0a0a0;"></span>
                        </div>
                    </div>

                    <!-- Senders Tab -->
                    <div id="senders-tab" style="display: none;">
                        <div style="margin-bottom: 20px;">
                            <button onclick="loadDuplicateSenders()" style="background: rgba(251, 191, 36, 0.2); color: #fbbf24; border: 1px solid #fbbf24; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600;">⟲ Reload Senders Data</button>
                            <span id="senders-status" style="margin-left: 15px; color: #a0a0a0;"></span>
                        </div>
                        <div id="senders-content"></div>
                    </div>

                    <!-- Coordinated Tokens Tab -->
                    <div id="tokens-tab" style="display: none;">
                        <div style="margin-bottom: 20px;">
                            <button onclick="loadDuplicateTokens()" style="background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid #4ade80; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600;">⟲ Reload Tokens Data</button>
                            <span id="tokens-status" style="margin-left: 15px; color: #a0a0a0;"></span>
                        </div>
                        <div id="tokens-content"></div>
                    </div>

                    <!-- Funder Networks Tab -->
                    <div id="funder-networks-tab" style="display: none;">
                        <div style="margin-bottom: 20px;">
                            <button onclick="loadFunderNetworks()" style="background: rgba(59, 130, 246, 0.2); color: #3b82f6; border: 1px solid #3b82f6; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600;">⟲ Reload Funder Networks</button>
                            <span id="funder-networks-status" style="margin-left: 15px; color: #a0a0a0;"></span>
                        </div>
                        <div id="funder-networks-content"></div>
                    </div>

                    <!-- Funding Networks Tab (Token Overlap Clustering) -->
                    <div id="funding-networks-tab" style="display: none;">
                        <div style="margin-bottom: 20px;">
                            <button onclick="loadFundingNetworks()" style="background: rgba(99, 102, 241, 0.2); color: #6366f1; border: 1px solid #6366f1; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600;">⟲ Reload Funding Networks</button>
                            <span id="funding-networks-status" style="margin-left: 15px; color: #a0a0a0;"></span>
                        </div>
                        <div id="funding-networks-content"></div>
                    </div>

                    <script>
                    async function analyzeAllFunders() {{
                        const btn = event.target;
                        btn.disabled = true;
                        const statusEl = document.getElementById('analysis-status');
                        statusEl.textContent = 'Queuing analysis...';

                        try {{
                            const response = await fetch('/api/analyze-all-coordinated-funders', {{ method: 'POST' }});
                            const data = await response.json();
                            statusEl.innerHTML = `✅ ` + data.message + `<br/><small>Queued: ` + data.queued_for_analysis + ` | Already done: ` + data.already_analyzed + `</small>`;
                        }} catch(e) {{
                            statusEl.textContent = `Error: ` + e.message;
                        }} finally {{
                            btn.disabled = false;
                        }}
                    }}
                    </script>

                        <div class="stats-grid">
                            <div class="stat-box suspicious">
                                <div class="stat-label">Suspicious Multi-Creator</div>
                                <div class="stat-value">{len(suspicious_funders)}</div>
                            </div>
                            <div class="stat-box safe">
                                <div class="stat-label">Safe (INFRA/CEX)</div>
                                <div class="stat-value">{len(safe_funders)}</div>
                            </div>
                            <div class="stat-box suspicious">
                                <div class="stat-label">Total Funders</div>
                                <div class="stat-value">{stats['total_funders']}</div>
                            </div>
                        </div>

                        <div class="section">
                            <div class="section-title">⚠️ Suspicious Multi-Creator Funders ({len(suspicious_funders)} total) - Click row for details</div>
                            <div class="section-content">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>Funder Address</th>
                                            <th>Network</th>
                                            <th>Creators</th>
                                            <th>Total SOL</th>
                                            <th>Records</th>
                                            <th>Analysis Status</th>
                                            <th>Period</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {suspicious_html if suspicious_html else '<tr><td colspan="7" style="padding: 20px; text-align: center; color: #a0a0a0;">No suspicious funders found</td></tr>'}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <div class="section">
                            <div class="section-title">✅ Safe Multi-Creator Funders ({len(safe_funders)} total)</div>
                            <div class="section-content">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>Funder Address</th>
                                            <th>Account Name</th>
                                            <th>Type</th>
                                            <th>Creators Funded</th>
                                            <th>Total SOL</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {safe_html if safe_html else '<tr><td colspan="5" style="padding: 20px; text-align: center; color: #a0a0a0;">No safe funders found</td></tr>'}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>

                    <!-- Sender Tokens Modal -->
                    <div id="senderTokensModal" style="display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0, 0, 0, 0.7);">
                        <div style="background: #0a0e27; margin: 10% auto; padding: 20px; border: 1px solid #00d4ff; width: 90%; max-width: 1200px; max-height: 80vh; overflow-y: auto; border-radius: 8px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                                <h2 style="color: #00d4ff; margin: 0;">Tokens Funded by Sender</h2>
                                <span style="cursor: pointer; font-size: 28px; color: #a0a0a0;" onclick="closeSenderTokens()">&times;</span>
                            </div>
                            <p style="color: #a0a0a0; font-size: 12px; word-break: break-all; margin-bottom: 15px;"><strong>Sender:</strong> <span id="modalSenderAddress" style="font-family: monospace;"></span></p>
                            <div style="background: rgba(0, 0, 0, 0.3); padding: 10px; border-radius: 4px; margin-bottom: 15px; color: #a0a0a0;">
                                <span id="senderTokensStatus">Loading...</span>
                            </div>
                            <div id="senderTokensContainer" style="overflow-x: auto;"></div>
                        </div>
                    </div>

                    <!-- Funder Tokens Modal -->
                    <div id="funderTokensModal" style="display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0, 0, 0, 0.7);">
                        <div style="background: #0a0e27; margin: 10% auto; padding: 20px; border: 1px solid #3b82f6; width: 90%; max-width: 1200px; max-height: 80vh; overflow-y: auto; border-radius: 8px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                                <h2 style="color: #3b82f6; margin: 0;">Tokens Funded by Funder</h2>
                                <span style="cursor: pointer; font-size: 28px; color: #a0a0a0;" onclick="closeFunderTokens()">&times;</span>
                            </div>
                            <p style="color: #a0a0a0; font-size: 12px; word-break: break-all; margin-bottom: 15px;"><strong>Funder:</strong> <span id="modalFunderAddress" style="font-family: monospace;"></span></p>
                            <div style="background: rgba(0, 0, 0, 0.3); padding: 10px; border-radius: 4px; margin-bottom: 15px; color: #a0a0a0;">
                                <span id="funderTokensStatus">Loading...</span>
                            </div>
                            <div id="funderTokensContainer" style="overflow-x: auto;"></div>
                        </div>
                    </div>
                </div>
            </body>
        </html>
        """
        return html

    except Exception as e:
        return f"<html><body style='background: #0a0e27; color: #e0e0e0;'><h1>Error</h1><p>{str(e)}</p></body></html>", 500


@app.route('/funder-details/<funder_address>')
def funder_details_view(funder_address: str):
    """Serve a full webview for detailed funder analysis with transfer details"""
    try:
        from infra_mapping import get_account_info, get_cex_info
        import requests

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get funder info
        cursor.execute("""
            SELECT
                funder_address,
                COUNT(DISTINCT creator_address) as creator_count,
                COUNT(*) as funding_record_count,
                SUM(amount_sol) as total_sol_sent,
                MIN(first_detected_at) as first_funding_at,
                MAX(first_detected_at) as last_funding_at,
                MAX(is_cex) as is_cex_flag
            FROM creator_funders
            WHERE funder_address = ?
            GROUP BY funder_address
        """, (funder_address,))

        funder = dict(cursor.fetchone() or {})
        if not funder:
            conn.close()
            return f"<html><body style='background: #0a0e27; color: #e0e0e0;'><h1>Funder Not Found</h1><p>No funding data for {funder_address}</p><p><a href='/' style='color: #00d4ff;'>← Back to Dashboard</a></p></body></html>", 404

        # Get detailed transfers to creators
        cursor.execute("""
            SELECT
                creator_address,
                amount_sol,
                first_detected_at
            FROM creator_funders
            WHERE funder_address = ?
            ORDER BY amount_sol DESC
        """, (funder_address,))

        transfers = [dict(row) for row in cursor.fetchall()]

        # Get funder label/classification
        funder_label = None
        is_cex = False
        is_infra = False

        infra_info = get_account_info(funder_address)
        if infra_info:
            funder_label = infra_info.get('name')
            is_infra = True

        cex_info = get_cex_info(funder_address)
        if cex_info:
            funder_label = cex_info.get('name')
            is_cex = True

        conn.close()

        # Fetch transfer details (incoming/outgoing) from API
        incoming_transfers = []
        outgoing_transfers = []
        total_incoming = 0
        total_outgoing = 0

        try:
            transfer_response = requests.get(f'http://localhost:5002/api/funder-transfer-details/{funder_address}', timeout=5)
            if transfer_response.status_code == 200:
                transfer_data = transfer_response.json()

                # Parse incoming transfers
                incoming_obj = transfer_data.get('incoming_transfers', {})
                if isinstance(incoming_obj, dict):
                    incoming_senders = incoming_obj.get('senders', [])
                    total_incoming = incoming_obj.get('total_sol', 0)
                    incoming_transfers = incoming_senders
                else:
                    incoming_transfers = incoming_obj if isinstance(incoming_obj, list) else []

                # Parse outgoing transfers
                outgoing_obj = transfer_data.get('outgoing_transfers', {})
                if isinstance(outgoing_obj, dict):
                    outgoing_recipients = outgoing_obj.get('recipients', [])
                    total_outgoing = abs(outgoing_obj.get('total_sol', 0))
                    outgoing_transfers = outgoing_recipients
                else:
                    outgoing_transfers = outgoing_obj if isinstance(outgoing_obj, list) else []
        except Exception as e:
            print(f"[ERROR] Failed to fetch transfer details: {str(e)}")

        # Build creators funded table
        transfers_html = ''
        for transfer in transfers:
            transfers_html += f"""
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                <td style="padding: 12px; font-family: monospace; font-size: 12px; color: #00d4ff; cursor: pointer;" onclick="window.location.href = '/creator/{transfer['creator_address']}'"><u>{transfer['creator_address'][:16]}...{transfer['creator_address'][-4:]}</u></td>
                <td style="padding: 12px; color: #4ade80;">{transfer['amount_sol']:.2f} SOL</td>
                <td style="padding: 12px; color: #a0a0a0; font-size: 11px;">{transfer['first_detected_at'][:10] if transfer['first_detected_at'] else 'N/A'}</td>
            </tr>
            """

        # Build incoming transfers table
        incoming_html = ''
        for transfer in incoming_transfers[:20]:  # Limit to 20
            label = transfer.get('label', 'Unknown')
            category = transfer.get('category', '')
            badge = ''
            if category == 'CEX':
                badge = '<span style="background: rgba(239, 68, 68, 0.2); color: #ef4444; padding: 2px 6px; border-radius: 3px; font-size: 10px; margin-left: 8px;">🚨 CEX</span>'
            elif category == 'Infrastructure':
                badge = '<span style="background: rgba(76, 175, 80, 0.2); color: #4ade80; padding: 2px 6px; border-radius: 3px; font-size: 10px; margin-left: 8px;">✅ INFRA</span>'

            incoming_html += f"""
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                <td style="padding: 12px; font-family: monospace; font-size: 12px; color: #a0a0a0;">{transfer['address'][:20]}...</td>
                <td style="padding: 12px; color: #fbbf24;">{label}{badge}</td>
                <td style="padding: 12px; text-align: right; color: #4ade80;">{transfer['amount_sol']:.2f} SOL</td>
                <td style="padding: 12px; text-align: center; color: #a0a0a0;">{transfer['transaction_count']}</td>
            </tr>
            """

        # Build outgoing transfers table
        outgoing_html = ''
        for transfer in outgoing_transfers[:20]:  # Limit to 20
            label = transfer.get('label', 'Unknown')
            category = transfer.get('category', '')
            badge = ''
            if category == 'CEX':
                badge = '<span style="background: rgba(239, 68, 68, 0.2); color: #ef4444; padding: 2px 6px; border-radius: 3px; font-size: 10px; margin-left: 8px;">🚨 CEX</span>'
            elif category == 'Infrastructure':
                badge = '<span style="background: rgba(76, 175, 80, 0.2); color: #4ade80; padding: 2px 6px; border-radius: 3px; font-size: 10px; margin-left: 8px;">✅ INFRA</span>'

            outgoing_html += f"""
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                <td style="padding: 12px; font-family: monospace; font-size: 12px; color: #a0a0a0;">{transfer['address'][:20]}...</td>
                <td style="padding: 12px; color: #fbbf24;">{label}{badge}</td>
                <td style="padding: 12px; text-align: right; color: #4ade80;">{transfer['amount_sol']:.2f} SOL</td>
                <td style="padding: 12px; text-align: center; color: #a0a0a0;">{transfer['transaction_count']}</td>
            </tr>
            """

        classification = ''
        if is_cex:
            classification = '<span style="background: rgba(239, 68, 68, 0.2); color: #ef4444; padding: 4px 8px; border-radius: 3px; font-size: 11px; font-weight: 600;">🚨 CEX Hot Wallet</span>'
        elif is_infra:
            classification = '<span style="background: rgba(76, 175, 80, 0.2); color: #4ade80; padding: 4px 8px; border-radius: 3px; font-size: 11px; font-weight: 600;">✅ Infrastructure</span>'
        else:
            classification = '<span style="background: rgba(251, 191, 36, 0.2); color: #fbbf24; padding: 4px 8px; border-radius: 3px; font-size: 11px; font-weight: 600;">❓ Unknown</span>'

        html = f"""
        <html>
            <head>
                <title>Funder Details - {funder_address[:16]}...</title>
                <style>
                    body {{
                        background: #0a0e27;
                        color: #e0e0e0;
                        font-family: 'Segoe UI', sans-serif;
                        margin: 0;
                        padding: 20px;
                    }}
                    .container {{
                        max-width: 1200px;
                        margin: 0 auto;
                    }}
                    h1 {{
                        color: #00d4ff;
                        word-break: break-all;
                        margin: 0 0 10px 0;
                        font-size: 18px;
                    }}
                    h2 {{
                        color: #00d4ff;
                        font-size: 14px;
                        margin-top: 30px;
                        margin-bottom: 15px;
                    }}
                    .header {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 20px;
                        border-radius: 8px;
                        margin-bottom: 20px;
                    }}
                    .back-link {{
                        margin-bottom: 20px;
                    }}
                    .back-link a {{
                        color: #00d4ff;
                        text-decoration: none;
                    }}
                    .back-link a:hover {{
                        text-decoration: underline;
                    }}
                    .stats-grid {{
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                        gap: 12px;
                        margin: 20px 0;
                    }}
                    .stat-box {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 15px;
                        border-radius: 8px;
                        border-left: 3px solid #00d4ff;
                    }}
                    .stat-label {{
                        color: #a0a0a0;
                        font-size: 10px;
                        text-transform: uppercase;
                        margin-bottom: 8px;
                    }}
                    .stat-value {{
                        font-size: 20px;
                        font-weight: bold;
                        color: #00d4ff;
                    }}
                    .section {{
                        background: rgba(0, 0, 0, 0.2);
                        border-radius: 8px;
                        margin-bottom: 20px;
                        overflow: hidden;
                    }}
                    .section-title {{
                        background: rgba(0, 0, 0, 0.4);
                        padding: 12px 15px;
                        border-bottom: 1px solid rgba(0, 212, 255, 0.2);
                        font-weight: 600;
                        color: #00d4ff;
                        font-size: 13px;
                    }}
                    .section-content {{
                        max-height: 500px;
                        overflow-y: auto;
                    }}
                    table {{
                        width: 100%;
                        border-collapse: collapse;
                        font-size: 12px;
                    }}
                    th {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 10px;
                        text-align: left;
                        font-size: 10px;
                        color: #a0a0a0;
                        text-transform: uppercase;
                        border-bottom: 1px solid rgba(0, 212, 255, 0.2);
                        font-weight: 600;
                    }}
                    td {{
                        padding: 10px;
                    }}
                    tr:hover {{
                        background: rgba(0, 212, 255, 0.05);
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="back-link">
                        <a href="/coordinated-funders">← Back to Coordinated Funders</a>
                    </div>

                    <div class="header">
                        <h1>💰 {funder_label or 'Unknown Funder'}</h1>
                        <p style="margin: 10px 0 0 0; font-family: monospace; font-size: 12px; color: #a0a0a0; word-break: break-all;">{funder_address}</p>
                        <div style="margin-top: 10px;">
                            {classification}
                        </div>
                    </div>

                    <div class="stats-grid">
                        <div class="stat-box">
                            <div class="stat-label">Creators Funded</div>
                            <div class="stat-value">{funder['creator_count']}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Total SOL Out</div>
                            <div class="stat-value">{funder['total_sol_sent']:.2f}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Total SOL In</div>
                            <div class="stat-value">{total_incoming:.2f}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Funding Records</div>
                            <div class="stat-value">{funder['funding_record_count']}</div>
                        </div>
                    </div>

                    <h2>📤 Incoming Transfers (Who Funded This Account)</h2>
                    <div class="section">
                        <div class="section-title">✅ {len(incoming_transfers)} Sources | {total_incoming:.2f} SOL Total</div>
                        <div class="section-content">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Sender Address</th>
                                        <th>Source</th>
                                        <th>Amount</th>
                                        <th>Txs</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {incoming_html if incoming_html else '<tr><td colspan="4" style="padding: 20px; text-align: center; color: #a0a0a0;">No incoming transfers found (analyze to fetch)</td></tr>'}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <h2>📥 Outgoing Transfers (Where This Account Sent SOL)</h2>
                    <div class="section">
                        <div class="section-title">✅ {len(outgoing_transfers)} Recipients | {total_outgoing:.2f} SOL Total</div>
                        <div class="section-content">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Recipient Address</th>
                                        <th>Destination</th>
                                        <th>Amount</th>
                                        <th>Txs</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {outgoing_html if outgoing_html else '<tr><td colspan="4" style="padding: 20px; text-align: center; color: #a0a0a0;">No outgoing transfers found (analyze to fetch)</td></tr>'}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <div class="section">
                        <div class="section-title">🎯 {len(transfers)} Creators Funded</div>
                        <div class="section-content">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Creator Address</th>
                                        <th>Amount</th>
                                        <th>Date</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {transfers_html if transfers_html else '<tr><td colspan="3" style="padding: 20px; text-align: center; color: #a0a0a0;">No creators found</td></tr>'}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </body>
        </html>
        """
        return html

    except Exception as e:
        import traceback
        return f"<html><body style='background: #0a0e27; color: #e0e0e0;'><h1>Error</h1><p>{str(e)}</p><pre>{traceback.format_exc()}</pre></body></html>", 500


@app.route('/api/duplicate-senders')
def api_duplicate_senders():
    """Get senders that send to multiple funders (duplicate senders analysis)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get senders sending to multiple funders (excluding CEX/INFRA accounts)
        cursor.execute("""
            SELECT
                sender_address,
                COUNT(DISTINCT funder_address) as funder_count,
                COUNT(*) as transfer_count,
                SUM(amount_sol) as total_sol,
                MIN(block_time) as first_seen,
                MAX(block_time) as last_seen,
                MAX(sender_type) as sender_type
            FROM funder_incoming_transfers
            WHERE sender_address IS NOT NULL
            GROUP BY sender_address
            HAVING COUNT(DISTINCT funder_address) > 1
            ORDER BY funder_count DESC, total_sol DESC
        """)

        all_senders = [dict(row) for row in cursor.fetchall()]

        # Filter out CEX and INFRA accounts
        senders = [s for s in all_senders if s['sender_type'] and s['sender_type'].upper() not in ('CEX', 'INFRA')]

        # For each sender, count related tokens
        for sender in senders:
            cursor.execute("""
                SELECT COUNT(DISTINCT ta.mint) as token_count
                FROM creator_funders cf
                JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
                WHERE cf.funder_address IN (
                    SELECT DISTINCT funder_address
                    FROM funder_incoming_transfers
                    WHERE sender_address = ?
                )
            """, (sender['sender_address'],))

            result = cursor.fetchone()
            sender['related_token_count'] = result['token_count'] if result else 0

        # Sort by related token count (descending), then by funder count
        senders.sort(key=lambda x: (-x['related_token_count'], -x['funder_count']))

        conn.close()

        return jsonify({
            'senders': senders,
            'total_duplicate_senders': len(senders)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sender-tokens/<sender_address>')
def api_sender_tokens(sender_address: str):
    """Get all tokens funded by accounts that received from this sender"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all tokens funded by funders that received from this sender
        cursor.execute("""
            SELECT DISTINCT
                ta.mint,
                ta.created_at,
                ta.risk_level,
                ROUND(ta.rug_probability, 3) as rug_probability,
                ta.earliest_tx_creator
            FROM creator_funders cf
            JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
            WHERE cf.funder_address IN (
                SELECT DISTINCT funder_address
                FROM funder_incoming_transfers
                WHERE sender_address = ?
            )
            ORDER BY ta.created_at DESC
        """, (sender_address,))

        # Calculate funding for each token from the sender's funder network
        tokens_list = []
        for token_row in cursor.fetchall():
            token_mint = token_row['mint']
            creator_addr = token_row['earliest_tx_creator']

            # Get total funding for this token from this creator's funders that came from the sender
            cursor.execute("""
                SELECT
                    SUM(cf.amount_sol) as total_funding_sol,
                    COUNT(DISTINCT cf.funder_address) as num_funders
                FROM creator_funders cf
                WHERE cf.creator_address = ? AND cf.funder_address IN (
                    SELECT DISTINCT funder_address
                    FROM funder_incoming_transfers
                    WHERE sender_address = ?
                )
            """, (creator_addr, sender_address))

            funding_row = cursor.fetchone()

            tokens_list.append({
                'mint': token_row['mint'],
                'created_at': token_row['created_at'],
                'risk_level': token_row['risk_level'],
                'rug_probability': token_row['rug_probability'],
                'creator_address': creator_addr,
                'total_funding_sol': funding_row['total_funding_sol'] if funding_row else 0,
                'num_funders': funding_row['num_funders'] if funding_row else 0
            })

        conn.close()

        return jsonify({
            'sender_address': sender_address,
            'tokens': tokens_list,
            'total_tokens': len(tokens_list)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/duplicate-tokens')
def api_duplicate_tokens():
    """Get tokens funded by multiple senders (coordinated funding)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get tokens funded by multiple senders
        cursor.execute("""
            SELECT
                ta.mint,
                ta.earliest_tx_creator as creator,
                ta.created_at,
                ta.risk_level,
                ta.rug_probability,
                ta.market_cap_current,
                COUNT(DISTINCT fit.sender_address) as num_senders,
                COUNT(DISTINCT fit.funder_address) as num_funders,
                ROUND(SUM(fit.amount_sol), 2) as total_sol
            FROM token_analysis ta
            LEFT JOIN creator_funders cf ON cf.creator_address = ta.earliest_tx_creator
            LEFT JOIN funder_incoming_transfers fit ON fit.funder_address = cf.funder_address
            WHERE ta.earliest_tx_creator IS NOT NULL
              AND fit.sender_address IS NOT NULL
            GROUP BY ta.mint
            HAVING COUNT(DISTINCT fit.sender_address) > 1
            ORDER BY ta.created_at DESC, num_senders DESC
        """)

        tokens = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            'tokens': tokens,
            'total_duplicate_tokens': len(tokens)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funder-networks')
def api_funder_networks():
    """Get all funders with their network info (tokens, creators, senders)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all funders and their network stats (excluding CEX and INFRA accounts)
        cursor.execute("""
            SELECT
                cf.funder_address,
                COUNT(DISTINCT cf.creator_address) as creators_funded,
                COUNT(DISTINCT ta.mint) as tokens_funded,
                ROUND(SUM(cf.amount_sol), 2) as total_sol_out,
                COUNT(DISTINCT fit.sender_address) as num_senders,
                ROUND(SUM(fit.amount_sol), 2) as total_sol_in,
                MIN(CAST(strftime('%s', cf.first_detected_at) AS INTEGER)) as earliest_funding,
                MAX(CAST(strftime('%s', cf.first_detected_at) AS INTEGER)) as latest_funding,
                COALESCE(cf.is_cex, 0) as is_cex,
                COALESCE(cf.cex_exchange, '') as cex_exchange
            FROM creator_funders cf
            LEFT JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
            LEFT JOIN funder_incoming_transfers fit ON fit.funder_address = cf.funder_address
            WHERE COALESCE(cf.is_cex, 0) = 0
            AND cf.funder_address NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
            GROUP BY cf.funder_address
            HAVING COUNT(DISTINCT ta.mint) > 0
            ORDER BY tokens_funded DESC, total_sol_out DESC
        """)

        funders = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify({
            'funders': funders,
            'total_funders': len(funders)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funding-networks')
def api_funding_networks():
    """Get funding network clusters (groups of funders that fund overlapping tokens)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get all active networks with their stats
        cursor.execute("""
            SELECT
                fn.network_id,
                fn.network_name,
                fn.total_members,
                fn.total_tokens_funded,
                fn.total_creators_funded,
                ROUND(fn.total_sol, 2) as total_sol
            FROM funding_networks fn
            ORDER BY fn.total_sol DESC, fn.total_tokens_funded DESC
        """)

        networks = []
        for row in cursor.fetchall():
            members = []
            # Get detailed member info for each network
            cursor.execute("""
                SELECT
                    fnm.funder_address,
                    fnm.role,
                    fnm.shared_tokens_count,
                    fnm.tokens_unique_to_member,
                    ROUND(fnm.total_sol_out, 2) as total_sol_out,
                    (SELECT COUNT(DISTINCT cw.cex_address) FROM cex_wallets cw WHERE cw.cex_address = fnm.funder_address AND cw.is_active = 1) as is_cex,
                    (SELECT exchange_name FROM cex_wallets WHERE cex_address = fnm.funder_address AND is_active = 1 LIMIT 1) as exchange_name
                FROM funding_network_members fnm
                WHERE fnm.network_id = ?
                ORDER BY fnm.total_sol_out DESC
            """, (row['network_id'],))

            for member_row in cursor.fetchall():
                members.append({
                    'address': member_row['funder_address'],
                    'role': member_row['role'],
                    'shared_tokens': member_row['shared_tokens_count'],
                    'unique_tokens': member_row['tokens_unique_to_member'],
                    'total_sol': member_row['total_sol_out'],
                    'is_cex': member_row['is_cex'],
                    'exchange': member_row['exchange_name']
                })

            networks.append({
                'network_id': row['network_id'],
                'name': row['network_name'],
                'members': members,
                'member_count': row['total_members'],
                'total_tokens': row['total_tokens_funded'],
                'total_creators': row['total_creators_funded'],
                'total_sol': row['total_sol']
            })

        conn.close()
        return jsonify({
            'networks': networks,
            'total_networks': len(networks)
        })

    except Exception as e:
        print(f"[FUNDING_NETWORKS_API] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/funding-networks-list')
def api_funding_networks_list():
    """Get simplified list of all funding networks with random names and stats"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get all networks with basic stats including senders count
        # IMPORTANT: creators_count now shows ACTUAL creators who launched tokens in the network
        cursor.execute("""
            SELECT
                fn.network_id,
                fn.total_members as funders_count,
                COUNT(DISTINCT fnt.mint) as tokens_count,
                COUNT(DISTINCT ta.earliest_tx_creator) as creators_count,
                ROUND(fn.total_sol, 2) as total_sol,
                COUNT(DISTINCT fit.sender_address) as senders_count
            FROM funding_networks fn
            LEFT JOIN funding_network_members fnm ON fn.network_id = fnm.network_id
            LEFT JOIN funding_network_shared_tokens fnt ON fn.network_id = fnt.network_id
            LEFT JOIN token_analysis ta ON fnt.mint = ta.mint
            LEFT JOIN funder_incoming_transfers fit ON fit.funder_address = fnm.funder_address
            GROUP BY fn.network_id
            ORDER BY fn.total_members DESC
        """)

        networks = []
        # Random name parts for generating network names
        adjectives = ['Shadow', 'Ghost', 'Phantom', 'Silent', 'Hidden', 'Dark', 'Swift', 'Rapid', 
                     'Sleek', 'Sharp', 'Cunning', 'Sly', 'Stealthy', 'Crafty', 'Clever', 'Subtle',
                     'Veiled', 'Masked', 'Cloaked', 'Whispered', 'Covert', 'Secret', 'Mystic', 'Ancient']
        nouns = ['Circle', 'Ring', 'Syndicate', 'Cabal', 'Order', 'Society', 'Collective', 'Alliance',
                'Coalition', 'Union', 'Cartel', 'Consortium', 'Federation', 'Network', 'Nexus', 'Web',
                'Chain', 'Echo', 'Whisper', 'Shadow', 'Phantom', 'Specter', 'Entity', 'Force']
        
        import random
        random.seed(42)  # Consistent seed so names don't change on page reload

        for idx, row in enumerate(cursor.fetchall()):
            adj = random.choice(adjectives)
            noun = random.choice(nouns)
            random_name = f"{adj} {noun}"

            networks.append({
                'network_id': row['network_id'],
                'name': random_name,
                'funders': row['funders_count'],
                'tokens': row['tokens_count'],
                'creators': row['creators_count'],
                'senders': row['senders_count'] or 0,
                'total_sol': row['total_sol']
            })

        conn.close()
        return jsonify({
            'networks': networks,
            'total_networks': len(networks)
        })

    except Exception as e:
        print(f"[FUNDING_NETWORKS_LIST_API] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/funding-network-details/<int:network_id>')
def api_funding_network_details(network_id):
    """Get detailed stats for a specific network"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get network basic info with CORRECTED creator count
        # Only count creators who actually launched tokens in this network
        cursor.execute("""
            SELECT
                fn.network_id,
                fn.network_name,
                fn.total_members as funders_count,
                COUNT(DISTINCT fnt.mint) as tokens_count,
                COUNT(DISTINCT ta.earliest_tx_creator) as creators_count,
                ROUND(fn.total_sol, 2) as total_sol
            FROM funding_networks fn
            LEFT JOIN funding_network_shared_tokens fnt ON fn.network_id = fnt.network_id
            LEFT JOIN token_analysis ta ON fnt.mint = ta.mint
            WHERE fn.network_id = ?
            GROUP BY fn.network_id
        """, (network_id,))

        network_row = cursor.fetchone()
        if not network_row:
            return jsonify({'error': 'Network not found'}), 404

        # Count unique senders (original wallets that have inbound transfers to funders)
        # First try funder_incoming_transfers, fall back to 0 if empty
        cursor.execute("""
            SELECT COUNT(DISTINCT COALESCE(fit.sender_address, 0)) as senders_count
            FROM funder_incoming_transfers fit
            WHERE fit.funder_address IN (
                SELECT fnm.funder_address
                FROM funding_network_members fnm
                WHERE fnm.network_id = ?
            )
            AND fit.sender_address IS NOT NULL
        """, (network_id,))

        senders_row = cursor.fetchone()
        senders_count = senders_row['senders_count'] if senders_row and senders_row['senders_count'] else 0

        # Get the tokens this network coordinates
        cursor.execute("""
            SELECT DISTINCT mint
            FROM funding_network_shared_tokens
            WHERE network_id = ?
            ORDER BY mint
        """, (network_id,))

        tokens = [row['mint'] for row in cursor.fetchall()]

        # Get root operators (funders that fund multiple creators in this network)
        cursor.execute("""
            SELECT DISTINCT cf.funder_address
            FROM creator_funders cf
            WHERE cf.creator_address IN (
                SELECT DISTINCT ta.earliest_tx_creator
                FROM funding_network_shared_tokens fnt
                JOIN token_analysis ta ON fnt.mint = ta.mint
                WHERE fnt.network_id = ?
            )
            GROUP BY cf.funder_address
            HAVING COUNT(DISTINCT cf.creator_address) >= 2
            ORDER BY COUNT(DISTINCT cf.creator_address) DESC
        """, (network_id,))

        root_operators = [row['funder_address'] for row in cursor.fetchall()]

        # Build root operator flows
        root_operator_flows = []
        for root_op in root_operators:
            # Get creators funded by this root operator in this network
            cursor.execute("""
                SELECT DISTINCT cf.creator_address, cf.amount_sol
                FROM creator_funders cf
                WHERE cf.funder_address = ?
                AND cf.creator_address IN (
                    SELECT DISTINCT ta.earliest_tx_creator
                    FROM funding_network_shared_tokens fnt
                    JOIN token_analysis ta ON fnt.mint = ta.mint
                    WHERE fnt.network_id = ?
                )
                ORDER BY cf.amount_sol DESC
            """, (root_op, network_id))

            funded_creators = [{'creator': row['creator_address'], 'sol': float(row['amount_sol'])} for row in cursor.fetchall()]

            if not funded_creators:
                continue

            total_sol_to_creators = sum(c['sol'] for c in funded_creators)

            # Get upstream sources (senders to this root operator)
            cursor.execute("""
                SELECT DISTINCT sender_address, COUNT(*) as transfer_count
                FROM funder_incoming_transfers
                WHERE funder_address = ?
                GROUP BY sender_address
                ORDER BY transfer_count DESC
                LIMIT 5
            """, (root_op,))

            upstream_sources = [{'sender': row['sender_address'], 'transfers': row['transfer_count']} for row in cursor.fetchall()]

            # Build example address flows (sender >> root op >> creator)
            example_flows = []
            if upstream_sources and funded_creators:
                for sender_data in upstream_sources[:3]:  # First 3 senders
                    sender = sender_data['sender']
                    for creator_data in funded_creators[:2]:  # First 2 creators per sender
                        example_flows.append({
                            'sender': sender,
                            'funder': root_op,
                            'creator': creator_data['creator'],
                            'sol_to_creator': creator_data['sol']
                        })
                    if len(example_flows) >= 3:  # Limit to 3 total flows
                        break

            # Get downstream creators' token details
            creator_list = [c['creator'] for c in funded_creators[:10]]
            if creator_list:
                placeholders = ','.join('?' * len(creator_list))
                cursor.execute(f"""
                    SELECT
                        ta.mint,
                        ta.earliest_tx_creator,
                        ta.risk_level,
                        ta.rug_probability
                    FROM token_analysis ta
                    WHERE ta.earliest_tx_creator IN ({placeholders})
                    ORDER BY ta.rug_probability DESC
                    LIMIT 10
                """, creator_list)

                downstream_creators = [{
                    'mint': row['mint'],
                    'creator': row['earliest_tx_creator'],
                    'risk_level': row['risk_level'],
                    'rug_probability': float(row['rug_probability']) if row['rug_probability'] else 0
                } for row in cursor.fetchall()]
            else:
                downstream_creators = []

            root_operator_flows.append({
                'root_operator': root_op,
                'creators_funded': len(funded_creators),
                'total_sol_sent': total_sol_to_creators,
                'transfer_count': len(funded_creators),
                'upstream_sources': upstream_sources,
                'downstream_creators': downstream_creators,
                'example_flows': example_flows
            })

        conn.close()

        return jsonify({
            'network_id': network_row['network_id'],
            'network_name': network_row['network_name'],
            'funders': network_row['funders_count'],
            'senders': senders_count,
            'creators': network_row['creators_count'],
            'tokens': network_row['tokens_count'],
            'total_sol': network_row['total_sol'],
            'token_list': tokens,
            'root_operator_flows': root_operator_flows
        })

    except Exception as e:
        print(f"[NETWORK_DETAILS_API] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/build-funding-networks', methods=['POST'])
def api_build_funding_networks():
    """Build/rebuild funding network clusters from scratch"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()

        # Clear existing networks
        cursor.execute("DELETE FROM funding_network_shared_tokens")
        cursor.execute("DELETE FROM funding_network_members")
        cursor.execute("DELETE FROM funding_networks")
        conn.commit()

        # Get all funders with their funded tokens (excluding CEX/INFRA)
        cursor.execute("""
            SELECT DISTINCT cf.funder_address
            FROM creator_funders cf
            WHERE cf.funder_address NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
            AND COALESCE(cf.is_cex, 0) = 0
        """)

        all_funders = [row[0] for row in cursor.fetchall()]
        print(f"[NETWORKS] Found {len(all_funders)} non-CEX funders to cluster", flush=True)

        # Build a mapping of funder -> set of tokens they fund
        funder_to_tokens = {}
        cursor.execute("""
            SELECT DISTINCT cf.funder_address, ta.mint
            FROM creator_funders cf
            JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
            WHERE cf.funder_address NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
            AND COALESCE(cf.is_cex, 0) = 0
            AND ta.mint IS NOT NULL
        """)

        for funder, mint in cursor.fetchall():
            if funder not in funder_to_tokens:
                funder_to_tokens[funder] = set()
            funder_to_tokens[funder].add(mint)

        print(f"[NETWORKS] Built token map for {len(funder_to_tokens)} funders", flush=True)

        # Union-Find data structure for efficient clustering
        parent = {}
        
        def find(x):
            if x not in parent:
                parent[x] = x
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]
        
        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Pre-compute overlaps: For each pair of funders, if they share 2+ tokens, union them
        funder_list = list(funder_to_tokens.keys())
        print(f"[NETWORKS] Computing overlaps for {len(funder_list)} funders", flush=True)
        
        for i, funder1 in enumerate(funder_list):
            if i % 1000 == 0:
                print(f"[NETWORKS] Processed {i}/{len(funder_list)} funders for overlap...", flush=True)
            
            for funder2 in funder_list[i+1:]:
                overlap = len(funder_to_tokens[funder1] & funder_to_tokens[funder2])
                if overlap >= 2:  # Threshold: 2+ shared tokens
                    union(funder1, funder2)

        # Group funders by their root parent
        networks_dict = {}
        for funder in funder_list:
            root = find(funder)
            if root not in networks_dict:
                networks_dict[root] = []
            networks_dict[root].append(funder)

        # Only keep networks with 2+ members
        networks_dict = {k: v for k, v in networks_dict.items() if len(v) >= 2}
        print(f"[NETWORKS] Found {len(networks_dict)} networks with 2+ members", flush=True)

        # Insert networks into database
        network_id = 1
        for root, network_members in sorted(networks_dict.items(), key=lambda x: -len(x[1])):
            network_name = f"Network_{network_id}"
            cursor.execute("""
                INSERT INTO funding_networks (network_name, total_members)
                VALUES (?, ?)
            """, (network_name, len(network_members)))
            conn.commit()

            current_network_id = cursor.lastrowid

            # Add members to network
            for member_funder in network_members:
                member_tokens = funder_to_tokens.get(member_funder, set())

                # Count how many tokens this member shares with other network members
                shared_tokens = set()
                for other_member in network_members:
                    if other_member != member_funder:
                        shared_tokens.update(member_tokens & funder_to_tokens.get(other_member, set()))

                unique_tokens = len(member_tokens - shared_tokens)

                # Get total SOL from this funder
                cursor.execute("""
                    SELECT ROUND(SUM(amount_sol), 2) as total
                    FROM creator_funders
                    WHERE funder_address = ?
                """, (member_funder,))

                total_sol = cursor.fetchone()[0] or 0

                cursor.execute("""
                    INSERT INTO funding_network_members
                    (network_id, funder_address, shared_tokens_count, tokens_unique_to_member, total_sol_out)
                    VALUES (?, ?, ?, ?, ?)
                """, (current_network_id, member_funder, len(shared_tokens), unique_tokens, total_sol))
                conn.commit()

                # Add shared tokens to network
                for token in shared_tokens:
                    cursor.execute("""
                        INSERT OR IGNORE INTO funding_network_shared_tokens
                        (network_id, mint)
                        VALUES (?, ?)
                    """, (current_network_id, token))
                    conn.commit()

            # Update network totals
            cursor.execute("""
                SELECT
                    COUNT(DISTINCT funder_address) as member_count,
                    COUNT(DISTINCT mint) as token_count,
                    SUM(total_sol_out) as total_sol
                FROM funding_network_members fnm
                LEFT JOIN funding_network_shared_tokens fnst ON fnm.network_id = fnst.network_id
                WHERE fnm.network_id = ?
                GROUP BY fnm.network_id
            """, (current_network_id,))

            stats = cursor.fetchone()
            if stats:
                cursor.execute("""
                    UPDATE funding_networks
                    SET total_members = ?,
                        total_tokens_funded = ?,
                        total_sol = ?
                    WHERE network_id = ?
                """, (len(network_members), stats[1] or 0, stats[2] or 0, current_network_id))
                conn.commit()

            network_id += 1
            if network_id % 10 == 0:
                print(f"[NETWORKS] Inserted {network_id} networks...", flush=True)

        # Now build single-funder networks (one funder funding 2+ creators)
        print(f"[NETWORKS] Building single-funder networks...", flush=True)
        cursor.execute("""
            SELECT funder_address, COUNT(DISTINCT creator_address) as creator_count
            FROM creator_funders
            WHERE funder_address NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
            AND COALESCE(is_cex, 0) = 0
            GROUP BY funder_address
            HAVING creator_count >= 2
            ORDER BY creator_count DESC
        """)

        single_funder_networks = cursor.fetchall()
        single_funder_count = 0

        for row in single_funder_networks:
            funder_address = row['funder_address']
            creator_count = row['creator_count']

            # Create a single-funder network
            network_name = f"SingleFunder_{single_funder_count + 1}"
            cursor.execute("""
                INSERT INTO funding_networks (network_name, total_members, network_type)
                VALUES (?, ?, ?)
            """, (network_name, 1, 'single_funder'))
            conn.commit()

            current_network_id = cursor.lastrowid

            # Add the single funder as network member
            # Get all their tokens and total SOL
            cursor.execute("""
                SELECT
                    COUNT(DISTINCT ta.mint) as token_count,
                    ROUND(SUM(cf.amount_sol), 2) as total_sol
                FROM creator_funders cf
                JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
                WHERE cf.funder_address = ?
            """, (funder_address,))

            stats = cursor.fetchone()
            token_count = stats['token_count'] or 0
            total_sol = stats['total_sol'] or 0

            cursor.execute("""
                INSERT INTO funding_network_members
                (network_id, funder_address, shared_tokens_count, tokens_unique_to_member, total_sol_out)
                VALUES (?, ?, ?, ?, ?)
            """, (current_network_id, funder_address, token_count, token_count, total_sol))

            # Add all their funded tokens to the network
            cursor.execute("""
                SELECT DISTINCT ta.mint
                FROM creator_funders cf
                JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
                WHERE cf.funder_address = ?
            """, (funder_address,))

            tokens = [row['mint'] for row in cursor.fetchall()]
            for mint in tokens:
                cursor.execute("""
                    INSERT OR IGNORE INTO funding_network_shared_tokens
                    (network_id, mint)
                    VALUES (?, ?)
                """, (current_network_id, mint))

            # Update network stats
            cursor.execute("""
                UPDATE funding_networks
                SET total_tokens_funded = ?,
                    total_creators_funded = ?,
                    total_sol = ?
                WHERE network_id = ?
            """, (token_count, creator_count, total_sol, current_network_id))
            conn.commit()

            single_funder_count += 1
            if single_funder_count % 10 == 0:
                print(f"[NETWORKS] Inserted {single_funder_count} single-funder networks...", flush=True)

        conn.close()
        print(f"[NETWORKS] ✅ Network building complete: {network_id - 1} shared networks + {single_funder_count} single-funder networks created", flush=True)
        return jsonify({'status': 'Networks built successfully', 'networks_created': network_id - 1}), 201

    except Exception as e:
        print(f"[BUILD_NETWORKS_API] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


def update_networks_for_new_token(mint: str, creator: str):
    """
    Incrementally update existing networks when a new token is launched.
    Checks if the creator's funders/senders are in existing networks.
    If yes, adds the token to those networks without rebuilding.

    Returns: List of affected network IDs for UI refresh
    """
    affected_networks = []

    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()

        # Get all funders of this creator
        cursor.execute("""
            SELECT DISTINCT funder_address
            FROM creator_funders
            WHERE creator_address = ?
        """, (creator,))

        creator_funders = [row[0] for row in cursor.fetchall()]

        if not creator_funders:
            # No funders yet, nothing to add to networks
            conn.close()
            return affected_networks

        print(f"[NETWORK_UPDATE] Token {mint[:8]}... | Creator {creator[:8]}... has {len(creator_funders)} funders", flush=True)

        # Get all senders to these funders
        cursor.execute("""
            SELECT DISTINCT sender_address
            FROM funder_incoming_transfers
            WHERE funder_address IN ({})
        """.format(','.join('?' * len(creator_funders))), creator_funders)

        creator_senders = [row[0] for row in cursor.fetchall()]

        # Combine funders and senders
        all_addresses = set(creator_funders + creator_senders)
        print(f"[NETWORK_UPDATE] Total addresses (funders + senders): {len(all_addresses)}", flush=True)

        # Find which networks contain any of these addresses
        cursor.execute("""
            SELECT DISTINCT network_id
            FROM funding_network_members
            WHERE funder_address IN ({})
        """.format(','.join('?' * len(list(all_addresses)))), list(all_addresses))

        affected_networks = [row[0] for row in cursor.fetchall()]

        if not affected_networks:
            print(f"[NETWORK_UPDATE] No existing networks contain this creator's funders/senders", flush=True)
            conn.close()
            return affected_networks

        print(f"[NETWORK_UPDATE] Found {len(affected_networks)} affected networks", flush=True)

        # Add token to each affected network
        for network_id in affected_networks:
            cursor.execute("""
                INSERT OR IGNORE INTO funding_network_shared_tokens
                (network_id, mint)
                VALUES (?, ?)
            """, (network_id, mint))
            conn.commit()

        # Update network statistics for affected networks
        for network_id in affected_networks:
            cursor.execute("""
                SELECT
                    COUNT(DISTINCT mint) as token_count,
                    SUM(total_sol_out) as total_sol
                FROM funding_network_members fnm
                LEFT JOIN funding_network_shared_tokens fnst ON fnm.network_id = fnst.network_id
                WHERE fnm.network_id = ?
                GROUP BY fnm.network_id
            """, (network_id,))

            stats = cursor.fetchone()
            if stats:
                token_count, total_sol = stats
                cursor.execute("""
                    UPDATE funding_networks
                    SET total_tokens_funded = ?,
                        total_sol = ?
                    WHERE network_id = ?
                """, (token_count or 0, total_sol or 0, network_id))
                conn.commit()

        print(f"[NETWORK_UPDATE] ✅ Added token {mint[:8]}... to {len(affected_networks)} networks", flush=True)
        conn.close()

    except Exception as e:
        print(f"[NETWORK_UPDATE] Error updating networks: {e}", flush=True)

    return affected_networks


@app.route('/api/tokens-by-funder/<funder_address>')
def api_tokens_by_funder(funder_address: str):
    """Get all tokens funded by a specific funder address"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all tokens funded by this funder
        cursor.execute("""
            SELECT
                ta.mint,
                ta.earliest_tx_creator as creator,
                ta.created_at,
                ta.risk_level,
                ta.rug_probability,
                ta.market_cap_current,
                cf.amount_sol,
                COUNT(DISTINCT fit.sender_address) as num_senders
            FROM creator_funders cf
            JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
            LEFT JOIN funder_incoming_transfers fit ON fit.funder_address = cf.funder_address
            WHERE cf.funder_address = ?
            GROUP BY ta.mint
            ORDER BY ta.created_at DESC
        """, (funder_address,))

        tokens = [dict(row) for row in cursor.fetchall()]

        # Get funder info
        cursor.execute("""
            SELECT
                COUNT(DISTINCT creator_address) as creators_funded,
                ROUND(SUM(amount_sol), 2) as total_sol_out,
                MIN(created_at) as earliest_funding,
                MAX(created_at) as latest_funding
            FROM (
                SELECT DISTINCT creator_address, amount_sol, created_at FROM creator_funders WHERE funder_address = ?
            )
        """, (funder_address,))
        funder_info = dict(cursor.fetchone()) if cursor.fetchone() else {}

        # Get funder incoming transfers
        cursor.execute("""
            SELECT
                COUNT(DISTINCT sender_address) as senders,
                ROUND(SUM(amount_sol), 2) as total_sol_in
            FROM funder_incoming_transfers
            WHERE funder_address = ?
        """, (funder_address,))
        incoming_info = dict(cursor.fetchone()) if cursor.fetchone() else {}

        conn.close()

        return jsonify({
            'funder_address': funder_address,
            'tokens': tokens,
            'total_tokens': len(tokens),
            'funder_info': {
                **funder_info,
                'senders': incoming_info.get('senders', 0),
                'total_in': incoming_info.get('total_sol_in', 0)
            }
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/analyze-all-coordinated-funders', methods=['POST'])
def api_analyze_all_coordinated_funders():
    """Trigger transfer analysis for all funders funding multiple creators.

    This runs in background threads and returns immediately with status.
    Each funder's incoming/outgoing transfers are fetched and saved to DB.
    """
    try:
        from funder_incoming_extractor import extract_for_creator
        import threading

        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Get all funders funding multiple creators
        cursor.execute("""
            SELECT DISTINCT funder_address
            FROM creator_funders
            GROUP BY funder_address
            HAVING COUNT(DISTINCT creator_address) > 1
            ORDER BY SUM(amount_sol) DESC
        """)

        funder_addresses = [row[0] for row in cursor.fetchall()]
        conn.close()

        # Trigger background analysis for each funder
        analyzed_count = 0
        skipped_count = 0

        for funder_address in funder_addresses:
            try:
                # Check if already analyzed
                conn = sqlite3.connect(DB_PATH, timeout=5)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM funder_incoming_transfers WHERE funder_address = ? LIMIT 1", (funder_address,))
                if cursor.fetchone()[0] > 0:
                    skipped_count += 1
                    conn.close()
                    continue
                conn.close()

                # Run analysis in background thread (non-blocking)
                thread = threading.Thread(target=extract_for_creator, args=(funder_address,), daemon=True)
                thread.start()
                analyzed_count += 1

            except Exception as e:
                print(f"[ERROR] Failed to queue analysis for {funder_address}: {str(e)}")

        return jsonify({
            'status': 'queued',
            'total_funders': len(funder_addresses),
            'queued_for_analysis': analyzed_count,
            'already_analyzed': skipped_count,
            'message': f'Queued {analyzed_count} funders for transfer analysis'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/analyze-funder-transfers', methods=['POST'])
def api_analyze_funder_transfers():
    """Trigger funder transfer analysis (incoming/outgoing) for a funder address.

    First checks if data already exists in database, returns immediately if found.
    Only extracts from Helius if no data exists.
    """
    import sys

    try:
        data = request.get_json()
        funder_address = data.get('funder_address')

        if not funder_address:
            return jsonify({'error': 'No funder address provided'}), 400

        # Check if we already have data for this funder in the database
        print(f"[ANALYZE] Checking database for {funder_address[:16]}...", flush=True)
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            cursor = conn.cursor()

            # Count existing transfers
            cursor.execute("SELECT COUNT(*) as count FROM funder_incoming_transfers WHERE funder_address = ?", (funder_address,))
            incoming_count = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(*) as count FROM funder_outgoing_transfers WHERE funder_address = ?", (funder_address,))
            outgoing_count = cursor.fetchone()['count']

            # Get total SOL
            cursor.execute("SELECT SUM(amount_sol) as total FROM funder_incoming_transfers WHERE funder_address = ?", (funder_address,))
            incoming_total = cursor.fetchone()['total'] or 0.0

            cursor.execute("SELECT SUM(amount_sol) as total FROM funder_outgoing_transfers WHERE funder_address = ?", (funder_address,))
            outgoing_total = cursor.fetchone()['total'] or 0.0

            conn.close()

            # If we have data, return it immediately from cache
            if incoming_count > 0 or outgoing_count > 0:
                print(f"[ANALYZE] ✅ Found in DB: {incoming_count} IN, {outgoing_count} OUT", flush=True)
                result = {
                    'funder': funder_address,
                    'incoming_count': incoming_count,
                    'outgoing_count': outgoing_count,
                    'total_sol': incoming_total + outgoing_total,
                    'source': 'database_cache'
                }

                # Store in memory cache for quick retrieval
                app.funder_analysis_cache = app.funder_analysis_cache or {}
                app.funder_analysis_cache[funder_address] = {
                    'status': 'completed',
                    'result': result,
                    'timestamp': datetime.now().isoformat()
                }

                return jsonify({
                    'status': 'completed',
                    'funder_address': funder_address,
                    'result': result,
                    'message': 'Results from database cache (no re-analysis needed)'
                })
        except Exception as e:
            print(f"[ANALYZE] Database check error (will extract): {e}", flush=True)
            # Continue with extraction if database check fails

        # No data found, need to extract
        print(f"[ANALYZE] No data in DB, will extract from Helius", flush=True)

        # Import here to avoid circular imports
        from funder_helius_extractor import extract_transfers_for_funder
        import threading

        # Run extraction in background thread
        def run_extraction():
            try:
                print(f"[FUNDER_ANALYSIS] Starting extraction for {funder_address[:16]}...", flush=True)

                result = extract_transfers_for_funder(funder_address)

                # Store result in memory/cache for quick retrieval
                app.funder_analysis_cache = app.funder_analysis_cache or {}
                app.funder_analysis_cache[funder_address] = {
                    'status': 'completed',
                    'result': result,
                    'timestamp': datetime.now().isoformat()
                }
                print(f"[FUNDER_ANALYSIS] ✅ Completed: {result.get('incoming_count', 0)} IN, {result.get('outgoing_count', 0)} OUT", flush=True)
            except Exception as e:
                import traceback
                print(f"[FUNDER_ANALYSIS] ❌ Error: {e}", flush=True)
                print(traceback.format_exc(), flush=True)

        # Start background thread (non-daemon so it completes)
        thread = threading.Thread(target=run_extraction, daemon=False)
        thread.start()

        return jsonify({
            'status': 'queued',
            'funder_address': funder_address,
            'message': 'Analysis queued (extracting from Helius)'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funder-analysis-status/<funder_address>', methods=['GET'])
def api_funder_analysis_status(funder_address):
    """Check status of funder analysis"""
    try:
        if funder_address in app.funder_analysis_cache:
            cache_entry = app.funder_analysis_cache[funder_address]
            return jsonify(cache_entry)
        else:
            return jsonify({'status': 'pending', 'funder_address': funder_address})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funder-senders/<funder_address>', methods=['GET'])
def api_funder_senders(funder_address: str):
    """Get all senders (incoming transfers) to a funder with classification (known/unknown)

    Prioritizes known accounts (infrastructure, CEX, blocklisted) first.
    """
    try:
        from infra_mapping import get_account_info, get_cex_info

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all incoming transfers for this funder
        cursor.execute("""
            SELECT DISTINCT
                sender_address,
                SUM(amount_sol) as total_sol,
                sender_type,
                is_cex,
                cex_exchange,
                cex_type
            FROM funder_incoming_transfers
            WHERE funder_address = ?
            GROUP BY sender_address
            ORDER BY total_sol DESC
        """, (funder_address,))

        senders = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Classify senders as known/unknown
        known_senders = []
        unknown_senders = []

        for sender in senders:
            sender_addr = sender['sender_address']
            classification = {
                'address': sender_addr,
                'amount_sol': sender['total_sol'],
                'type': sender['sender_type'],
                'is_known': False,
                'label': None,
                'category': None
            }

            # Check if already marked as CEX in database
            if sender['is_cex']:
                classification['is_known'] = True
                classification['category'] = 'CEX'
                classification['label'] = sender['cex_exchange']

            # Check if infrastructure account
            if not classification['is_known']:
                infra_info = get_account_info(sender_addr)
                if infra_info:
                    classification['is_known'] = True
                    classification['category'] = 'Infrastructure'
                    classification['label'] = infra_info.get('name', 'Unknown INFRA')

            # Check if CEX wallet
            if not classification['is_known']:
                cex_info = get_cex_info(sender_addr)
                if cex_info:
                    classification['is_known'] = True
                    classification['category'] = 'CEX'
                    classification['label'] = cex_info.get('name', 'Unknown CEX')

            # Check in address_tags for any other known classification
            if not classification['is_known']:
                try:
                    conn2 = sqlite3.connect(DB_PATH, timeout=5)
                    cursor2 = conn2.cursor()
                    cursor2.execute("SELECT tag_type FROM address_tags WHERE address = ? LIMIT 1", (sender_addr,))
                    tag_result = cursor2.fetchone()
                    if tag_result:
                        classification['is_known'] = True
                        classification['category'] = tag_result[0]
                    conn2.close()
                except:
                    pass

            if classification['is_known']:
                known_senders.append(classification)
            else:
                unknown_senders.append(classification)

        # Return known senders first, then unknown
        all_senders = known_senders + unknown_senders

        return jsonify({
            'funder_address': funder_address,
            'total_senders': len(all_senders),
            'known_senders': len(known_senders),
            'unknown_senders': len(unknown_senders),
            'senders': all_senders
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funder-transfer-details/<funder_address>')
def api_funder_transfer_details(funder_address: str):
    """Get complete funder transfer details (IN and OUT) with summaries"""
    try:
        from infra_mapping import get_account_info, get_cex_info

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get funder info from creator_funders
        cursor.execute("""
            SELECT DISTINCT funder_address, COUNT(DISTINCT creator_address) as creator_count
            FROM creator_funders
            WHERE funder_address = ?
            GROUP BY funder_address
        """, (funder_address,))
        funder_info = cursor.fetchone()

        # Get incoming transfers (who funded this funder)
        cursor.execute("""
            SELECT
                sender_address,
                SUM(amount_sol) as total_amount,
                COUNT(*) as transaction_count,
                sender_type,
                is_cex,
                cex_exchange,
                cex_type,
                MIN(block_time) as first_transfer,
                MAX(block_time) as last_transfer
            FROM funder_incoming_transfers
            WHERE funder_address = ?
            GROUP BY sender_address
            ORDER BY total_amount DESC
        """, (funder_address,))
        incoming_transfers = [dict(row) for row in cursor.fetchall()]

        # Get outgoing transfers (where this funder sent SOL)
        cursor.execute("""
            SELECT
                recipient_address,
                SUM(amount_sol) as total_amount,
                COUNT(*) as transaction_count,
                recipient_type,
                is_cex,
                cex_exchange,
                cex_type,
                MIN(block_time) as first_transfer,
                MAX(block_time) as last_transfer
            FROM funder_outgoing_transfers
            WHERE funder_address = ?
            GROUP BY recipient_address
            ORDER BY total_amount DESC
        """, (funder_address,))
        outgoing_transfers = [dict(row) for row in cursor.fetchall()]

        conn.close()

        # Enrich incoming transfers with classification
        incoming_enriched = []
        total_incoming = 0
        for t in incoming_transfers:
            total_incoming += t['total_amount']
            addr = t['sender_address']
            classification = {'is_known': False, 'label': None, 'category': None}

            if t['is_cex']:
                classification = {'is_known': True, 'label': t['cex_exchange'], 'category': 'CEX'}
            else:
                infra = get_account_info(addr)
                if infra:
                    classification = {'is_known': True, 'label': infra.get('name'), 'category': 'Infrastructure'}
                else:
                    cex = get_cex_info(addr)
                    if cex:
                        classification = {'is_known': True, 'label': cex.get('name'), 'category': 'CEX'}

            incoming_enriched.append({
                'address': addr,
                'amount_sol': t['total_amount'],
                'transaction_count': t['transaction_count'],
                'type': t['sender_type'],
                **classification
            })

        # Enrich outgoing transfers with classification
        outgoing_enriched = []
        total_outgoing = 0
        for t in outgoing_transfers:
            total_outgoing += t['total_amount']
            addr = t['recipient_address']
            classification = {'is_known': False, 'label': None, 'category': None}

            if t['is_cex']:
                classification = {'is_known': True, 'label': t['cex_exchange'], 'category': 'CEX'}
            else:
                infra = get_account_info(addr)
                if infra:
                    classification = {'is_known': True, 'label': infra.get('name'), 'category': 'Infrastructure'}
                else:
                    cex = get_cex_info(addr)
                    if cex:
                        classification = {'is_known': True, 'label': cex.get('name'), 'category': 'CEX'}

            outgoing_enriched.append({
                'address': addr,
                'amount_sol': t['total_amount'],
                'transaction_count': t['transaction_count'],
                'type': t['recipient_type'],
                **classification
            })

        return jsonify({
            'funder_address': funder_address,
            'creators_funded': funder_info['creator_count'] if funder_info else 0,
            'incoming_transfers': {
                'total_senders': len(incoming_enriched),
                'total_sol': total_incoming,
                'known_senders': sum(1 for t in incoming_enriched if t['is_known']),
                'senders': incoming_enriched[:50]  # Limit to 50
            },
            'outgoing_transfers': {
                'total_recipients': len(outgoing_enriched),
                'total_sol': total_outgoing,
                'known_recipients': sum(1 for t in outgoing_enriched if t['is_known']),
                'recipients': outgoing_enriched[:50]  # Limit to 50
            },
            'net_flow': total_incoming - total_outgoing
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-service-history/<creator_address>')
def api_creator_service_history(creator_address: str):
    """Get full history of service usage (jitotip, meteora, debridge, axiom) for a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all service history records for this creator
        cursor.execute("""
            SELECT
                tag,
                amount_sol,
                tx_signature,
                mint,
                network_fee_sol,
                tip_percentage,
                tx_type,
                created_at
            FROM creator_service_history
            WHERE creator_address = ?
            ORDER BY created_at DESC
        """, (creator_address,))
        
        history = [dict(row) for row in cursor.fetchall()]

        # Calculate statistics per tag
        cursor.execute("""
            SELECT 
                tag,
                COUNT(*) as count,
                SUM(amount_sol) as total,
                AVG(amount_sol) as avg,
                MIN(amount_sol) as min,
                MAX(amount_sol) as max
            FROM creator_service_history
            WHERE creator_address = ?
            GROUP BY tag
        """, (creator_address,))
        
        stats = {}
        for row in cursor.fetchall():
            stats[row['tag']] = {
                'count': row['count'],
                'total_sol': row['total'],
                'avg_sol': row['avg'],
                'min_sol': row['min'],
                'max_sol': row['max']
            }

        conn.close()

        return jsonify({
            'creator_address': creator_address,
            'history': history,
            'statistics': stats,
            'total_records': len(history)
        })

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


@app.route('/api/funder-extraction-control', methods=['GET', 'POST'])
def api_funder_extraction_control():
    """Get or set funder transfer extraction status"""
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
            # Get current funder extraction status
            cursor.execute("SELECT setting_value FROM polling_settings WHERE setting_name = 'funder_extraction_enabled'")
            row = cursor.fetchone()
            extraction_enabled = row[0] == '1' if row else False

            conn.close()
            return jsonify({
                'status': 'enabled' if extraction_enabled else 'disabled',
                'extraction_enabled': extraction_enabled
            })

        elif request.method == 'POST':
            data = request.get_json()
            action = data.get('action')  # 'enable', 'disable', 'toggle'

            if action == 'toggle':
                # Get current state
                cursor.execute("SELECT setting_value FROM polling_settings WHERE setting_name = 'funder_extraction_enabled'")
                row = cursor.fetchone()
                current = row[0] == '1' if row else False
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
                VALUES ('funder_extraction_enabled', ?)
            """, (new_value,))
            conn.commit()
            conn.close()

            extraction_enabled = new_value == '1'
            return jsonify({
                'status': 'success',
                'extraction_enabled': extraction_enabled,
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


@app.route('/api/validate-transaction', methods=['POST'])
def api_validate_transaction():
    """Validate a Pump.Fun CREATE transaction"""
    try:
        data = request.json
        sig = data.get('signature', '').strip()

        if not sig:
            return jsonify({'error': 'No signature provided'}), 400

        # Fetch transaction from RPC
        import requests
        rpc_url = "https://api.mainnet-beta.solana.com"

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }

        response = requests.post(rpc_url, json=payload, timeout=10)
        result = response.json()

        if "result" not in result or not result["result"]:
            return jsonify({'error': 'Transaction not found on-chain'}), 404

        tx = result["result"]

        # Extract details
        message = tx.get("transaction", {}).get("message", {})
        account_keys = message.get("accountKeys", [])
        instructions = message.get("instructions", [])
        inner_instructions = tx.get("meta", {}).get("innerInstructions", [])

        # Get fee payer (first account)
        fee_payer = None
        if account_keys:
            first_key = account_keys[0]
            fee_payer = first_key.get("pubkey") if isinstance(first_key, dict) else first_key

        # Find mint and other details
        mint = None
        system_create_count = 0
        has_init_mint = False
        has_pump_program = False

        # Check top-level instructions
        for instr in instructions:
            program_id = instr.get("programId")
            parsed = instr.get("parsed", {})
            itype = (parsed.get("type") or "").lower()

            if program_id == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":
                has_pump_program = True

        # Check inner instructions
        for group in inner_instructions:
            if isinstance(group, dict) and "instructions" in group:
                for ii in group.get("instructions", []):
                    parsed = ii.get("parsed", {})
                    itype = (parsed.get("type") or "").lower()

                    if itype == "createaccount":
                        system_create_count += 1

                    if itype in ("initializemint", "initializemint2"):
                        has_init_mint = True
                        mint = parsed.get("info", {}).get("mint")

                    if ii.get("programId") == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":
                        has_pump_program = True

        # Determine if this is a CREATE
        is_create = system_create_count > 0 and has_init_mint

        if not is_create:
            return jsonify({'error': 'Not a Pump.Fun CREATE transaction (missing System.createAccount or initializeMint)'}), 400

        # Format timestamp
        from datetime import datetime
        block_time = tx.get("blockTime")
        timestamp = datetime.fromtimestamp(block_time).strftime('%Y-%m-%d %H:%M:%S UTC') if block_time else "Unknown"

        return jsonify({
            'signature': sig,
            'mint': mint or 'Unknown',
            'creator': fee_payer or 'Unknown',
            'timestamp': timestamp,
            'confirmed': tx.get("meta", {}).get("err") is None,
            'has_system_create': system_create_count > 0,
            'has_init_mint': has_init_mint,
            'pump_program': has_pump_program,
            'instruction_count': len(instructions),
            'inner_instruction_count': sum(
                len(g.get("instructions", [])) if isinstance(g, dict) else 1
                for g in inner_instructions
            )
        })

    except requests.exceptions.Timeout:
        return jsonify({'error': 'RPC timeout - transaction fetch took too long'}), 503
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Network error: {str(e)}'}), 503
    except Exception as e:
        return jsonify({'error': f'Validation error: {str(e)}'}), 500


@app.route('/api/super-clusters')
def api_super_clusters():
    """Get all super-clusters with their stats"""
    try:
        from infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS
        infra_and_cex = set(INFRASTRUCTURE_ACCOUNTS.keys()) | set(CEX_ACCOUNTS.keys())

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                sc.super_cluster_id,
                sc.network_count,
                sc.creator_count,
                sc.root_addresses,
                sc.risk_level,
                COUNT(DISTINCT csm.creator_address) as mapped_creators
            FROM super_clusters sc
            LEFT JOIN creator_super_cluster_membership csm ON sc.super_cluster_id = csm.super_cluster_id
            GROUP BY sc.super_cluster_id
            ORDER BY sc.creator_count DESC
        """)

        clusters = []
        for row in cursor.fetchall():
            # Filter out infrastructure and CEX accounts from root addresses
            root_addresses_raw = row['root_addresses'].split(',')
            root_addresses_filtered = [addr for addr in root_addresses_raw if addr not in infra_and_cex]

            clusters.append({
                'id': row['super_cluster_id'],
                'network_count': row['network_count'],
                'creator_count': row['creator_count'],
                'mapped_creators': row['mapped_creators'],
                'root_addresses': root_addresses_filtered,
                'risk_level': row['risk_level']
            })

        conn.close()
        return jsonify({'clusters': clusters, 'total': len(clusters)})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/super-cluster/<cluster_id>')
def api_super_cluster_details(cluster_id: str):
    """Get detailed information about a super-cluster with complete SOL flow chains"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get cluster info
        cursor.execute("""
            SELECT
                super_cluster_id,
                network_count,
                creator_count,
                root_addresses,
                risk_level
            FROM super_clusters
            WHERE super_cluster_id = ?
        """, (cluster_id,))

        cluster_row = cursor.fetchone()
        if not cluster_row:
            return jsonify({'error': 'Cluster not found'}), 404

        # Get all creators in this cluster
        cursor.execute("""
            SELECT DISTINCT creator_address
            FROM creator_super_cluster_membership
            WHERE super_cluster_id = ?
            ORDER BY creator_address
        """, (cluster_id,))

        creators = [row['creator_address'] for row in cursor.fetchall()]

        # Get tokens for these creators
        cursor.execute("""
            SELECT
                mint,
                earliest_tx_creator,
                created_at,
                rug_probability,
                risk_level,
                market_cap_highest,
                price_current
            FROM token_analysis
            WHERE earliest_tx_creator IN (
                SELECT DISTINCT creator_address
                FROM creator_super_cluster_membership
                WHERE super_cluster_id = ?
            )
            ORDER BY created_at DESC
        """, (cluster_id,))

        tokens = [dict(row) for row in cursor.fetchall()]

        # Get funder info
        from infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS
        infra_and_cex = set(INFRASTRUCTURE_ACCOUNTS.keys()) | set(CEX_ACCOUNTS.keys())

        cursor.execute("""
            SELECT
                COUNT(DISTINCT funder_address) as total_funders,
                SUM(amount_sol) as total_sol,
                SUM(CASE WHEN is_cex = 1 THEN 1 ELSE 0 END) as cex_funders
            FROM creator_funders
            WHERE creator_address IN (
                SELECT DISTINCT creator_address
                FROM creator_super_cluster_membership
                WHERE super_cluster_id = ?
            )
            AND funder_address NOT IN ({})
        """.format(','.join('?' * len(infra_and_cex))),
        (cluster_id,) + tuple(infra_and_cex))

        funder_row = cursor.fetchone()

        # Get network names
        cursor.execute("""
            SELECT DISTINCT
                fn.network_id,
                fn.network_name,
                fn.total_members,
                fn.total_sol
            FROM funding_networks fn
            WHERE fn.network_id IN (
                SELECT DISTINCT fnm.network_id
                FROM funding_network_members fnm
                WHERE fnm.funder_address IN (
                    SELECT DISTINCT funder_address
                    FROM creator_funders
                    WHERE creator_address IN (
                        SELECT DISTINCT creator_address
                        FROM creator_super_cluster_membership
                        WHERE super_cluster_id = ?
                    )
                )
            )
            ORDER BY fn.total_sol DESC
        """, (cluster_id,))

        networks = [dict(row) for row in cursor.fetchall()]

        # Identify root operators (funders with multiple creators)
        cursor.execute("""
            SELECT
                cf.funder_address,
                COUNT(DISTINCT cf.creator_address) as creators_funded,
                SUM(cf.amount_sol) as total_sol_sent,
                COUNT(*) as transfer_count,
                MIN(cf.first_detected_at) as first_transfer
            FROM creator_funders cf
            WHERE cf.creator_address IN (
                SELECT DISTINCT creator_address
                FROM creator_super_cluster_membership
                WHERE super_cluster_id = ?
            )
            AND cf.funder_address NOT IN ({})
            GROUP BY cf.funder_address
            HAVING COUNT(DISTINCT cf.creator_address) > 1
            ORDER BY total_sol_sent DESC
            LIMIT 10
        """.format(','.join('?' * len(infra_and_cex))),
        (cluster_id,) + tuple(infra_and_cex))

        root_operators_data = cursor.fetchall()

        # Build complete SOL flow chains for each root operator
        root_operator_flows = []
        for root_op in root_operators_data:
            root_op_addr = root_op['funder_address']

            # Get upstream funders for this root operator
            cursor.execute("""
                SELECT
                    funder_address,
                    amount_sol,
                    first_detected_at
                FROM creator_funders
                WHERE creator_address = ?
                ORDER BY amount_sol DESC
                LIMIT 5
            """, (root_op_addr,))

            upstream_sources = [dict(row) for row in cursor.fetchall()]

            # Get downstream creators funded by this root operator
            cursor.execute("""
                SELECT
                    cf.creator_address,
                    cf.amount_sol,
                    cf.first_detected_at,
                    ta.mint,
                    ta.rug_probability,
                    ta.risk_level
                FROM creator_funders cf
                LEFT JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
                WHERE cf.funder_address = ?
                AND cf.creator_address IN ({})
                ORDER BY cf.amount_sol DESC
            """.format(','.join('?' * len(creators))), (root_op_addr,) + tuple(creators))

            downstream_creators = [dict(row) for row in cursor.fetchall()]

            # Build example address flows (sender >> root op >> creator)
            example_flows = []
            if upstream_sources and downstream_creators:
                for sender_data in upstream_sources[:3]:  # First 3 senders
                    sender = sender_data['funder_address']
                    for creator_data in downstream_creators[:2]:  # First 2 creators per sender
                        example_flows.append({
                            'sender': sender,
                            'funder': root_op_addr,
                            'creator': creator_data['creator_address'],
                            'sol_to_creator': creator_data['amount_sol']
                        })
                    if len(example_flows) >= 3:  # Limit to 3 total flows
                        break

            root_operator_flows.append({
                'root_operator': root_op_addr,
                'creators_funded': root_op['creators_funded'],
                'total_sol_sent': root_op['total_sol_sent'],
                'transfer_count': root_op['transfer_count'],
                'first_transfer': root_op['first_transfer'],
                'upstream_sources': upstream_sources,
                'downstream_creators': downstream_creators,
                'example_flows': example_flows
            })

        conn.close()

        # Filter out infrastructure and CEX accounts from root addresses
        root_addresses_raw = cluster_row['root_addresses'].split(',') if cluster_row['root_addresses'] else []
        root_addresses_filtered = [addr for addr in root_addresses_raw if addr not in infra_and_cex]

        return jsonify({
            'id': cluster_row['super_cluster_id'],
            'network_count': cluster_row['network_count'],
            'creator_count': cluster_row['creator_count'],
            'mapped_creator_count': len(creators),
            'root_addresses': root_addresses_filtered,
            'risk_level': cluster_row['risk_level'],
            'creators': creators,
            'tokens': tokens,
            'funder_stats': {
                'total_funders': funder_row['total_funders'] if funder_row else 0,
                'total_sol': funder_row['total_sol'] if funder_row else 0,
                'cex_funders': funder_row['cex_funders'] if funder_row else 0
            },
            'networks': networks,
            'root_operator_flows': root_operator_flows
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-super-cluster/<creator_address>')
def api_creator_super_cluster(creator_address: str):
    """Get super-cluster membership for a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                csm.super_cluster_id,
                sc.risk_level,
                sc.creator_count,
                sc.network_count
            FROM creator_super_cluster_membership csm
            INNER JOIN super_clusters sc ON csm.super_cluster_id = sc.super_cluster_id
            WHERE csm.creator_address = ?
            ORDER BY sc.creator_count DESC
        """, (creator_address,))

        memberships = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            'creator_address': creator_address,
            'super_clusters': memberships,
            'total_clusters': len(memberships)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/super-cluster/<cluster_id>/sol-flow')
def api_super_cluster_sol_flow(cluster_id: str):
    """Get detailed SOL flow visualization for a super cluster (Root Op >> Funder >> Creator)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        from infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS
        infra_and_cex = set(INFRASTRUCTURE_ACCOUNTS.keys()) | set(CEX_ACCOUNTS.keys())

        # Get all creators in cluster
        cursor.execute("""
            SELECT creator_address FROM creator_super_cluster_membership
            WHERE super_cluster_id = ?
        """, (cluster_id,))
        cluster_creators = [row['creator_address'] for row in cursor.fetchall()]

        if not cluster_creators:
            return jsonify({'error': 'No creators found in cluster'}), 404

        # Identify root operators (funders that funded multiple creators in cluster)
        cursor.execute("""
            SELECT
                cf.funder_address,
                COUNT(DISTINCT cf.creator_address) as creators_funded,
                SUM(cf.amount_sol) as total_sol_sent
            FROM creator_funders cf
            WHERE cf.creator_address IN ({})
            AND cf.funder_address NOT IN ({})
            GROUP BY cf.funder_address
            ORDER BY total_sol_sent DESC
        """.format(
            ','.join('?' * len(cluster_creators)),
            ','.join('?' * len(infra_and_cex))
        ), tuple(cluster_creators) + tuple(infra_and_cex))

        root_operators = [dict(row) for row in cursor.fetchall()]

        # Get SOL flows: Root Op -> Funder -> Creator
        flows = []
        for op in root_operators[:10]:  # Top 10 root operators
            root_op = op['funder_address']

            # Find who funds this root operator
            cursor.execute("""
                SELECT
                    cf.funder_address,
                    cf.creator_address,
                    cf.amount_sol,
                    cf.first_detected_at
                FROM creator_funders cf
                WHERE cf.creator_address = ?
                AND cf.funder_address NOT IN ({})
                ORDER BY cf.amount_sol DESC
                LIMIT 5
            """.format(','.join('?' * len(infra_and_cex))),
            (root_op,) + tuple(infra_and_cex))

            upstream_funders = [dict(row) for row in cursor.fetchall()]

            # Find creators funded by this root operator
            cursor.execute("""
                SELECT
                    cf.creator_address,
                    cf.amount_sol,
                    cf.first_detected_at,
                    ta.mint,
                    ta.risk_level,
                    ta.rug_probability
                FROM creator_funders cf
                LEFT JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
                WHERE cf.funder_address = ?
                AND cf.creator_address IN ({})
                ORDER BY cf.amount_sol DESC
            """.format(','.join('?' * len(cluster_creators))),
            (root_op,) + tuple(cluster_creators))

            downstream_creators = [dict(row) for row in cursor.fetchall()]

            flows.append({
                'root_operator': root_op,
                'creators_funded': op['creators_funded'],
                'total_sol_sent': op['total_sol_sent'],
                'upstream_sources': upstream_funders,
                'downstream_creators': downstream_creators
            })

        # Get network names
        cursor.execute("""
            SELECT DISTINCT
                fn.network_id,
                fn.network_name,
                fn.total_sol
            FROM funding_networks fn
            WHERE fn.network_id IN (
                SELECT DISTINCT fnm.network_id
                FROM funding_network_members fnm
                WHERE fnm.funder_address IN ({})
            )
            ORDER BY fn.total_sol DESC
        """.format(','.join('?' * len([op['funder_address'] for op in root_operators]))),
        tuple([op['funder_address'] for op in root_operators]))

        networks = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            'cluster_id': cluster_id,
            'networks': networks,
            'sol_flows': flows,
            'total_root_operators': len(root_operators),
            'top_flows_shown': len(flows)
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
