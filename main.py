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

                # Check if funding has been analyzed (has last_analyzed timestamps)
                cursor.execute("""
                    SELECT COUNT(*) as analyzed_count FROM creator_funders
                    WHERE creator_address = ? AND last_analyzed IS NOT NULL
                """, (row['earliest_tx_creator'],))
                analyzed_result = cursor.fetchone()
                funding_checked = analyzed_result[0] > 0 if analyzed_result else False

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
                'funding_checked': funding_checked
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
                <button class="action-button" onclick="toggleCEXView()" title="View CEX funders and activity" style="background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.5); margin-left: 8px;">CEX View</button>
                <button class="action-button" onclick="showMultiCreatorFunders()" title="Analyze funders supporting multiple creators" style="background: rgba(139, 92, 246, 0.2); color: #a78bfa; border: 1px solid rgba(139, 92, 246, 0.5); margin-left: 8px;">Coordinated Funders</button>
                <button class="action-button" onclick="openValidationModal()" title="Validate a transaction signature" style="background: rgba(59, 130, 246, 0.2); color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.5); margin-left: 8px;">Validate TX</button>
            </div>
            <div class="control-group" style="border-left: 1px solid rgba(239, 68, 68, 0.3); margin-left: 12px; padding-left: 12px;">
                <button class="action-button danger" onclick="emptyDatabase()" title="Clear all tokens, clustering, and address data">Empty DB</button>
                <button class="action-button danger" onclick="killFlask()" title="Stop Flask server on port 5002">Kill Port 5002</button>
            </div>
        </div>

        <div id="tokens-container">
            <div class="loading">Loading migrated tokens...</div>
        </div>

        <!-- Funding Network View - CRITICAL COORDINATION DETECTION -->
        <div id="funding-network-container" style="display: none;">
            <div style="padding: 20px;">
                <h2 style="color: #ef4444; margin-bottom: 20px;">🚨 Funding Network - Suspicious Coordination</h2>
                <p style="color: #fca5a5; margin-bottom: 20px; font-size: 14px;">
                    ⚠️ <strong>CRITICAL:</strong> These unknown addresses coordinate funding across multiple creators/funders.
                    This pattern indicates organized malicious behavior or bot networks.
                </p>

                <!-- Coordinated Address Network -->
                <div style="margin-bottom: 30px;">
                    <h3 style="color: #ff6b6b; margin-bottom: 15px;">Unknown Addresses Funding Multiple Creators</h3>
                    <div id="fundingNetworkContainer" style="overflow-x: auto;">
                        <table class="tokens-table" style="width: 100%;">
                            <thead>
                                <tr>
                                    <th>Address</th>
                                    <th>Creators Funded</th>
                                    <th>Total SOL</th>
                                    <th>Linked Funders</th>
                                    <th>Risk Level</th>
                                </tr>
                            </thead>
                            <tbody id="fundingNetworkBody">
                                <tr><td colspan="5" style="text-align: center; color: #a0a0a0;">Loading...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Shared Counterparties Between Funders -->
                <div style="margin-bottom: 30px;">
                    <h3 style="color: #ff6b6b; margin-bottom: 15px;">Shared Funding Sources (Hub Coordination)</h3>
                    <div id="sharedCounterpartiesContainer" style="background: rgba(239, 68, 68, 0.1); padding: 15px; border-radius: 8px; border-left: 4px solid #ef4444;">
                        <div id="sharedCounterpartiesBody" style="color: #fca5a5; font-family: monospace; font-size: 12px;">
                            <div class="loading">Analyzing coordination patterns...</div>
                        </div>
                    </div>
                </div>

                <!-- Statistics -->
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px;">
                    <div style="background: rgba(239, 68, 68, 0.1); padding: 15px; border-radius: 8px; border-left: 4px solid #ef4444;">
                        <div style="color: #a0a0a0; font-size: 12px; text-transform: uppercase; margin-bottom: 8px;">Suspicious Networks</div>
                        <div id="suspiciousNetworkCount" style="font-size: 24px; font-weight: bold; color: #ef4444;">0</div>
                    </div>
                    <div style="background: rgba(239, 68, 68, 0.1); padding: 15px; border-radius: 8px; border-left: 4px solid #ef4444;">
                        <div style="color: #a0a0a0; font-size: 12px; text-transform: uppercase; margin-bottom: 8px;">Hub Addresses</div>
                        <div id="hubAddressCount" style="font-size: 24px; font-weight: bold; color: #ef4444;">0</div>
                    </div>
                    <div style="background: rgba(239, 68, 68, 0.1); padding: 15px; border-radius: 8px; border-left: 4px solid #ef4444;">
                        <div style="color: #a0a0a0; font-size: 12px; text-transform: uppercase; margin-bottom: 8px;">Total SOL Tracked</div>
                        <div id="totalSolTracked" style="font-size: 24px; font-weight: bold; color: #ef4444;">0 SOL</div>
                    </div>
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

            <!-- View Funding Patterns Button -->
            <div style="margin: 20px 0; text-align: center;">
                <button onclick="showFundingNetwork3Tier(document.getElementById('modalCreator').textContent.split(' ')[0])" style="background: rgba(239, 68, 68, 0.2); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.5); padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: bold;">View Funding Patterns</button>
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
                            <th>Funder Name</th>
                            <th>Creators Funded</th>
                            <th>Total SOL</th>
                            <th>Funding Records</th>
                            <th>Activity Period</th>
                        </tr>
                    </thead>
                    <tbody id="multiCreatorFundersBody">
                        <tr><td colspan="5" style="text-align: center; color: #a0a0a0;">Loading...</td></tr>
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
                                columnTags.push('<span class="creator-tag tag-funding-checked" title="Creator funding accounts have been analyzed" style="border-color: #4ade80; color: #4ade80; background-color: rgba(74, 222, 128, 0.15);">✓ Funding Checked</span>');
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

                            // Get funder label - check if account_info has a name
                            let funderLabel = '';
                            if (funder.account_info && funder.account_info.name) {
                                funderLabel = funder.account_info.name;
                            }

                            return `
                                <tr>
                                    <td style="font-family: monospace; font-size: 12px; color: #ef4444;">
                                        <a href="#" onclick="showCreatorDetails('${funder.funder_address}'); return false;" title="Click to view details" style="color: #ef4444; text-decoration: none;">
                                            ${funder.funder_address}
                                        </a>
                                    </td>
                                    <td style="color: #fbbf24; font-weight: 600; font-size: 12px; white-space: nowrap;">${funderLabel}</td>
                                    <td><strong style="color: #ef4444;">${funder.creator_count}</strong></td>
                                    <td>${funder.total_sol_sent.toFixed(2)} SOL</td>
                                    <td>${funder.funding_record_count}</td>
                                    <td style="font-size: 11px;">${period}</td>
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
        }

        // Close modal when pressing Escape
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape') {
                closeTokenMetrics();
                closeCreatorDetails();
                closeMultiCreatorFunders();
                closeTxViewer();
                closeValidationModal();
                closeFundingNetwork3Tier();
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
                const response = await fetch(`/api/funding-network-3tier/${creatorAddress}`);
                const data = await response.json();

                if (data.error) {
                    document.getElementById('fn3tNetworkBody').innerHTML = '<div style="color: #ef4444;">Error loading network</div>';
                    return;
                }

                // Build 3-tier network visualization with relationship arrows
                let networkHTML = '<div style="font-family: monospace; font-size: 11px; line-height: 1.9;">';

                data.network_tiers.forEach((tier, tierIdx) => {
                    const funderAddr = tier.funder_address;
                    const funderType = tier.funder_type || 'unknown';
                    const funderLabel = tier.funder_label;
                    const totalToCreator = tier.total_to_creator.toFixed(4);
                    const senderCount = tier.sender_count || tier.senders.length;

                    // Funder type styling
                    let funderColor = '#4ade80';  // Default green for regular
                    let funderTypeLabel = '[Unknown]';
                    if (funderType === 'cex') {
                        funderColor = '#ef4444';  // Red for CEX
                        funderTypeLabel = '[CEX: ' + (funderLabel || 'Exchange') + ']';
                    } else if (funderType === 'infra') {
                        funderColor = '#f97316';  // Orange for INFRA
                        funderTypeLabel = '[INFRA: ' + (funderLabel || 'Infrastructure') + ']';
                    } else {
                        funderTypeLabel = '[Regular Wallet]';
                    }

                    networkHTML += `<div style="margin-bottom: 25px; padding: 12px; background: rgba(0,0,0,0.3); border-radius: 6px; border-left: 3px solid ${funderColor};">`;

                    // Funder header with type label
                    networkHTML += `<div style="color: ${funderColor}; font-weight: bold; margin-bottom: 4px;">`;
                    networkHTML += `🟢 FUNDER ${funderTypeLabel}</div>`;
                    networkHTML += `<div style="color: #00d4ff; margin-bottom: 8px; word-break: break-all; font-size: 10px;">`;
                    networkHTML += `${funderAddr}</div>`;
                    networkHTML += `<div style="color: #a0a0a0; font-size: 9px; margin-bottom: 8px;">`;
                    networkHTML += `Role: Receives SOL from ${senderCount} sender(s), distributes to Creator</div>`;

                    // Arrow down to senders
                    networkHTML += `<div style="color: #fbbf24; margin: 8px 0; text-align: center; font-weight: bold;">↓ Inbound from ${senderCount} sender(s)</div>`;

                    // Senders for this funder
                    if (tier.senders.length > 0) {
                        tier.senders.forEach((sender, senderIdx) => {
                            const senderType = sender.sender_type || 'unknown';
                            const senderColor = senderType === 'cex' ? '#ef4444' : senderType === 'infra' ? '#f97316' : '#fbbf24';
                            const senderTypeLabel = senderType === 'cex' ? '[CEX]' : senderType === 'infra' ? '[INFRA]' : '[Wallet]';
                            const senderAmount = sender.amount_to_funder.toFixed(4);

                            networkHTML += `<div style="margin-bottom: 10px; padding: 10px; background: rgba(0,0,0,0.5); border-radius: 4px; border-left: 2px solid ${senderColor};">`;
                            networkHTML += `<div style="color: ${senderColor}; font-weight: bold; margin-bottom: 4px;">`;
                            networkHTML += `🟡 SENDER ${senderTypeLabel}</div>`;
                            networkHTML += `<div style="color: #00d4ff; word-break: break-all; font-size: 10px; margin-bottom: 5px;">`;
                            networkHTML += `${sender.sender_address}</div>`;
                            networkHTML += `<div style="color: #a0a0a0; font-size: 9px; margin-bottom: 4px;">`;
                            networkHTML += `Role: Source of funds, sends to Funder</div>`;
                            networkHTML += `<div style="color: #fbbf24; font-size: 10px; font-weight: bold;">`;
                            networkHTML += `→ ${senderAmount} SOL → Funder</div>`;
                            networkHTML += `</div>`;
                        });
                    } else {
                        networkHTML += `<div style="color: #a0a0a0; font-size: 10px; padding: 8px;">No tracked senders</div>`;
                    }

                    // Arrow down to creator
                    networkHTML += `<div style="color: #4ade80; margin: 8px 0; text-align: center; font-weight: bold;">↓ Funds</div>`;
                    networkHTML += `<div style="color: #00d4ff; padding: 10px; background: rgba(0,212,255,0.1); border-radius: 4px; border-left: 2px solid #00d4ff;">`;
                    networkHTML += `<div style="color: #00d4ff; font-weight: bold; margin-bottom: 4px;">🔵 CREATOR</div>`;
                    networkHTML += `<div style="color: #00d4ff; word-break: break-all; font-size: 10px; margin-bottom: 6px;">`;
                    networkHTML += `${creatorAddress}</div>`;
                    networkHTML += `<div style="color: #a0a0a0; font-size: 9px; margin-bottom: 6px;">`;
                    networkHTML += `Role: Token creator, receives funds from Funders</div>`;
                    networkHTML += `<div style="color: #4ade80; font-size: 10px; font-weight: bold;">`;
                    networkHTML += `← ${totalToCreator} SOL from this Funder</div>`;

                    networkHTML += `</div>`;
                });

                networkHTML += '</div>';
                document.getElementById('fn3tNetworkBody').innerHTML = networkHTML;
                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading 3-tier network:', error);
                document.getElementById('fn3tNetworkBody').innerHTML = '<div style="color: #ef4444;">Error loading network data</div>';
            }
        }

        function closeFundingNetwork3Tier() {
            document.getElementById('fundingNetwork3TierModal').style.display = 'none';
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
            'tags': tags
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
                    sender_type
                FROM funder_incoming_transfers
                WHERE funder_address = ?
                GROUP BY sender_address, sender_type
                ORDER BY amount_to_funder DESC
            """, (funder_addr,))

            senders = cursor.fetchall()

            funder_info = {
                'funder_address': funder_addr,
                'funder_type': funder_type,
                'funder_label': funder_label,
                'total_to_creator': funder_total,
                'sender_count': len(senders),
                'senders': [
                    {
                        'sender_address': s['sender_address'],
                        'amount_to_funder': s['amount_to_funder'],
                        'sender_type': s['sender_type']
                    }
                    for s in senders
                ]
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


# =========================================================================
# MAIN
# =========================================================================

if __name__ == '__main__':
    print("[FLASK] Starting Migration Tracker UI...")
    print("[FLASK] Dashboard available at http://localhost:5002")
    print("[FLASK] Database: " + DB_PATH)
    app.run(host='0.0.0.0', port=5002, debug=False)
