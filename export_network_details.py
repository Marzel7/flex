#!/usr/bin/env python3
"""
Export detailed network data for a specific super-cluster to Excel.

Creates multiple sheets with:
1. Network Overview
2. Root Operators & Funding Chains
3. Creators & Their Tokens
4. Funding Sources (who funds the root operators)
5. Token Analysis
6. All Transfers (detailed funding relationships)
"""

import sqlite3
import pandas as pd
from datetime import datetime
import sys
from infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS

DB_PATH = "pumpswap_tokens.db"

def get_cluster_id():
    """Get cluster ID from command line or use default"""
    if len(sys.argv) > 1:
        return sys.argv[1]
    return "net_01086"  # Default

def get_data(cluster_id):
    """Extract all network data from database for a specific cluster"""

    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    infra_and_cex = set(INFRASTRUCTURE_ACCOUNTS.keys()) | set(CEX_ACCOUNTS.keys())

    print(f"Exporting network data for {cluster_id}...")

    # 1. CLUSTER OVERVIEW
    print("  → Cluster overview")
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
        print(f"Cluster {cluster_id} not found!")
        return None

    overview = [{
        'Cluster ID': cluster_row['super_cluster_id'],
        'Networks': cluster_row['network_count'],
        'Creators': cluster_row['creator_count'],
        'Root Operators': len(cluster_row['root_addresses'].split(',')) if cluster_row['root_addresses'] else 0,
        'Risk Level': cluster_row['risk_level'],
        'Export Date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }]
    overview_df = pd.DataFrame(overview)

    # 2. ROOT OPERATORS & FUNDING CHAINS
    print("  → Root operators")
    root_addresses_raw = cluster_row['root_addresses'].split(',') if cluster_row['root_addresses'] else []

    root_ops = []
    for addr in root_addresses_raw:
        cursor.execute("""
            SELECT
                cf.funder_address,
                COUNT(DISTINCT cf.creator_address) as creators_funded,
                COUNT(*) as transfer_count,
                SUM(cf.amount_sol) as total_sol,
                MIN(cf.first_detected_at) as first_transfer
            FROM creator_funders cf
            WHERE cf.funder_address = ?
            AND cf.creator_address IN (
                SELECT DISTINCT creator_address
                FROM creator_super_cluster_membership
                WHERE super_cluster_id = ?
            )
            GROUP BY cf.funder_address
        """, (addr, cluster_id))

        row = cursor.fetchone()
        if row:
            display_name = addr
            if addr in INFRASTRUCTURE_ACCOUNTS:
                display_name = f"{INFRASTRUCTURE_ACCOUNTS[addr]['name']} (INFRA)"
            elif addr in CEX_ACCOUNTS:
                display_name = f"{CEX_ACCOUNTS[addr]['name']} (CEX)"

            root_ops.append({
                'Root Operator': display_name,
                'Address': addr,
                'Creators Funded': row['creators_funded'],
                'Transfers': row['transfer_count'],
                'Total SOL': round(row['total_sol'] or 0, 2),
                'First Activity': row['first_transfer']
            })

    root_ops_df = pd.DataFrame(root_ops)

    # 3. CREATORS & THEIR TOKENS
    print("  → Creators and tokens")
    cursor.execute("""
        SELECT DISTINCT creator_address
        FROM creator_super_cluster_membership
        WHERE super_cluster_id = ?
        ORDER BY creator_address
    """, (cluster_id,))

    creators = [row['creator_address'] for row in cursor.fetchall()]

    creators_data = []
    for creator in creators:
        # Get creator's funders
        cursor.execute("""
            SELECT
                COUNT(DISTINCT funder_address) as funder_count,
                SUM(amount_sol) as total_sol
            FROM creator_funders
            WHERE creator_address = ?
        """, (creator,))

        funder_row = cursor.fetchone()

        # Get creator's tokens
        cursor.execute("""
            SELECT
                mint,
                created_at,
                risk_level,
                rug_probability,
                market_cap_highest
            FROM token_analysis
            WHERE earliest_tx_creator = ?
            ORDER BY created_at DESC
        """, (creator,))

        tokens = cursor.fetchall()

        if tokens:
            for token in tokens:
                creators_data.append({
                    'Creator': creator,
                    'Token': token['mint'],
                    'Created': token['created_at'],
                    'Funders': funder_row['funder_count'] or 0,
                    'Total SOL': round(funder_row['total_sol'] or 0, 2),
                    'Risk Level': token['risk_level'] or 'Pending',
                    'Rug Probability': round(token['rug_probability'] or 0, 4) if token['rug_probability'] else 'N/A',
                    'Peak Market Cap': round(token['market_cap_highest'] or 0, 2) if token['market_cap_highest'] else 'N/A'
                })
        else:
            creators_data.append({
                'Creator': creator,
                'Token': 'N/A',
                'Created': 'N/A',
                'Funders': funder_row['funder_count'] or 0,
                'Total SOL': round(funder_row['total_sol'] or 0, 2),
                'Risk Level': 'N/A',
                'Rug Probability': 'N/A',
                'Peak Market Cap': 'N/A'
            })

    creators_df = pd.DataFrame(creators_data)

    # 4. FUNDING SOURCES (who funds the root operators)
    print("  → Funding sources")
    funding_sources = []
    if root_addresses_raw:
        placeholders = ','.join(['?' for _ in root_addresses_raw])
        cursor.execute(f"""
            SELECT
                sender_address,
                funder_address,
                COUNT(*) as transfer_count,
                SUM(amount_sol) as total_sol
            FROM funder_incoming_transfers
            WHERE funder_address IN ({placeholders})
            GROUP BY sender_address, funder_address
            ORDER BY total_sol DESC
        """, root_addresses_raw)

        for row in cursor.fetchall():
            # Get display name for funder
            funder_display = row['funder_address']
            if row['funder_address'] in INFRASTRUCTURE_ACCOUNTS:
                funder_display = f"{INFRASTRUCTURE_ACCOUNTS[row['funder_address']]['name']} (INFRA)"
            elif row['funder_address'] in CEX_ACCOUNTS:
                funder_display = f"{CEX_ACCOUNTS[row['funder_address']]['name']} (CEX)"

            funding_sources.append({
                'Sender': row['sender_address'],
                'Funds To': funder_display,
                'Funder Address': row['funder_address'],
                'Transfers': row['transfer_count'],
                'Total SOL': round(row['total_sol'] or 0, 2)
            })

    funding_sources_df = pd.DataFrame(funding_sources) if funding_sources else pd.DataFrame()

    # 5. TOKEN ANALYSIS SUMMARY
    print("  → Token analysis")
    cursor.execute("""
        SELECT
            ta.mint,
            ta.earliest_tx_creator,
            ta.created_at,
            ta.risk_level,
            ta.rug_probability,
            ta.market_cap_highest,
            ta.price_current
        FROM token_analysis ta
        WHERE ta.earliest_tx_creator IN (
            SELECT DISTINCT creator_address
            FROM creator_super_cluster_membership
            WHERE super_cluster_id = ?
        )
        ORDER BY ta.rug_probability DESC
    """, (cluster_id,))

    tokens = []
    for row in cursor.fetchall():
        creator_short = row['earliest_tx_creator'][:16] + '...' if row['earliest_tx_creator'] else 'Unknown'
        tokens.append({
            'Token': row['mint'],
            'Creator': creator_short,
            'Created': row['created_at'],
            'Risk Level': row['risk_level'] or 'Pending',
            'Rug Probability': round(row['rug_probability'] or 0, 4) if row['rug_probability'] else 'N/A',
            'Peak Market Cap': round(row['market_cap_highest'] or 0, 2) if row['market_cap_highest'] else 'N/A',
            'Current Price': round(row['price_current'] or 0, 8) if row['price_current'] else 'N/A'
        })

    tokens_df = pd.DataFrame(tokens)

    # 6. ALL TRANSFERS (detailed funder-creator relationships)
    print("  → All transfers")
    placeholders = ','.join(['?' for _ in creators])
    cursor.execute(f"""
        SELECT
            cf.funder_address,
            cf.creator_address,
            cf.amount_sol,
            cf.first_detected_at,
            ta.mint
        FROM creator_funders cf
        LEFT JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
        WHERE cf.creator_address IN ({placeholders})
        ORDER BY cf.amount_sol DESC
    """, creators)

    transfers = []
    for row in cursor.fetchall():
        funder_display = row['funder_address']
        if row['funder_address'] in INFRASTRUCTURE_ACCOUNTS:
            funder_display = f"{INFRASTRUCTURE_ACCOUNTS[row['funder_address']]['name']} (INFRA)"
        elif row['funder_address'] in CEX_ACCOUNTS:
            funder_display = f"{CEX_ACCOUNTS[row['funder_address']]['name']} (CEX)"

        transfers.append({
            'Funder': funder_display,
            'Funder Address': row['funder_address'],
            'Creator': row['creator_address'],
            'Amount SOL': round(row['amount_sol'] or 0, 2),
            'First Detected': row['first_detected_at'],
            'Token': row['mint'] or 'N/A'
        })

    transfers_df = pd.DataFrame(transfers)

    conn.close()

    return {
        'Overview': overview_df,
        'Root Operators': root_ops_df,
        'Creators & Tokens': creators_df,
        'Funding Sources': funding_sources_df,
        'Token Analysis': tokens_df,
        'All Transfers': transfers_df
    }

def write_excel(cluster_id, data_dict):
    """Write all dataframes to Excel with formatting"""

    output_file = f"network_{cluster_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    print(f"\nWriting to {output_file}...")

    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        for sheet_name, df in data_dict.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)

            # Auto-adjust column widths
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width

    print(f"✅ Export complete: {output_file}\n")

    # Print summary
    print("=" * 70)
    print("EXPORT SUMMARY")
    print("=" * 70)
    for sheet_name, df in data_dict.items():
        print(f"  {sheet_name}: {len(df)} rows")
    print("=" * 70)

if __name__ == "__main__":
    cluster_id = get_cluster_id()
    data = get_data(cluster_id)
    if data:
        write_excel(cluster_id, data)
