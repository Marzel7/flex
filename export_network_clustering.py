#!/usr/bin/env python3
"""
Export comprehensive network clustering data to Excel.

Creates multiple sheets with:
1. Super-Cluster Overview
2. Root Operators & Funding Chains
3. Creator Memberships
4. Funder Network Details
5. Token Analysis Summary
6. Risk Assessment
"""

import sqlite3
import pandas as pd
from datetime import datetime
from infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS

DB_PATH = "pumpswap_tokens.db"
OUTPUT_FILE = f"network_clustering_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

def get_data():
    """Extract all network clustering data from database"""

    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    infra_and_cex = set(INFRASTRUCTURE_ACCOUNTS.keys()) | set(CEX_ACCOUNTS.keys())

    print("Extracting network clustering data...")

    # 1. SUPER-CLUSTER OVERVIEW
    print("  → Super-clusters")
    cursor.execute("""
        SELECT
            sc.super_cluster_id,
            sc.network_count,
            sc.creator_count,
            sc.root_addresses,
            sc.risk_level,
            COUNT(DISTINCT cscm.creator_address) as mapped_creators,
            COUNT(DISTINCT (
                SELECT DISTINCT mint FROM token_analysis
                WHERE earliest_tx_creator = cscm.creator_address
            )) as token_count,
            SUM(cf.amount_sol) as total_sol_funded
        FROM super_clusters sc
        LEFT JOIN creator_super_cluster_membership cscm ON sc.super_cluster_id = cscm.super_cluster_id
        LEFT JOIN creator_funders cf ON cscm.creator_address = cf.creator_address
        GROUP BY sc.super_cluster_id
        ORDER BY total_sol_funded DESC
    """)

    clusters = []
    for row in cursor.fetchall():
        root_ops = row['root_addresses'].split(',') if row['root_addresses'] else []
        clusters.append({
            'Cluster ID': row['super_cluster_id'],
            'Networks': row['network_count'],
            'Creators': row['creator_count'],
            'Mapped Creators': row['mapped_creators'],
            'Tokens': row['token_count'] or 0,
            'Root Operators': len(root_ops),
            'Total SOL': round(row['total_sol_funded'] or 0, 2),
            'Risk Level': row['risk_level'],
            'Root Op Addresses': row['root_addresses'] or 'None'
        })

    clusters_df = pd.DataFrame(clusters)

    # 2. ROOT OPERATORS & FUNDING CHAINS
    print("  → Root operators")
    cursor.execute("""
        SELECT
            cf.funder_address,
            COUNT(DISTINCT cf.creator_address) as creators_funded,
            COUNT(*) as transfer_count,
            SUM(cf.amount_sol) as total_sol,
            MIN(cf.first_detected_at) as first_transfer,
            GROUP_CONCAT(DISTINCT cscm.super_cluster_id) as clusters,
            CASE WHEN cf.funder_address IN (SELECT funder_address FROM funder_incoming_transfers) THEN 'Yes' ELSE 'No' END as has_incoming_data
        FROM creator_funders cf
        LEFT JOIN creator_super_cluster_membership cscm ON cf.creator_address = cscm.creator_address
        WHERE cf.funder_address NOT IN (SELECT key FROM (
            SELECT ? as key UNION ALL SELECT ? UNION ALL SELECT ? UNION ALL SELECT ?
        ))
        GROUP BY cf.funder_address
        HAVING COUNT(DISTINCT cf.creator_address) > 1
        ORDER BY total_sol DESC
        LIMIT 200
    """, tuple(list(infra_and_cex)[:4]))

    root_ops = []
    for row in cursor.fetchall():
        root_ops.append({
            'Root Operator': row['funder_address'],
            'Creators Funded': row['creators_funded'],
            'Transfers': row['transfer_count'],
            'Total SOL': round(row['total_sol'] or 0, 2),
            'First Activity': row['first_transfer'],
            'Active Clusters': len(set(row['clusters'].split(','))) if row['clusters'] else 0,
            'Incoming Data': row['has_incoming_data'],
            'Cluster IDs': row['clusters'] or 'None'
        })

    root_ops_df = pd.DataFrame(root_ops)

    # 3. CREATOR MEMBERSHIPS
    print("  → Creator memberships")
    infra_list = ','.join(['?' for _ in infra_and_cex])
    cursor.execute(f"""
        SELECT
            cscm.creator_address,
            cscm.super_cluster_id,
            COUNT(DISTINCT CASE WHEN cf.funder_address NOT IN ({infra_list}) THEN cf.funder_address END) as funder_count,
            SUM(CASE WHEN cf.funder_address NOT IN ({infra_list}) THEN cf.amount_sol ELSE 0 END) as total_sol_received,
            ta.mint,
            ta.risk_level,
            ta.rug_probability,
            ta.created_at
        FROM creator_super_cluster_membership cscm
        LEFT JOIN creator_funders cf ON cscm.creator_address = cf.creator_address
        LEFT JOIN token_analysis ta ON cscm.creator_address = ta.earliest_tx_creator
        GROUP BY cscm.creator_address, cscm.super_cluster_id
        ORDER BY ta.rug_probability DESC
        LIMIT 2000
    """, tuple(infra_and_cex) * 2)

    memberships = []
    for row in cursor.fetchall():
        memberships.append({
            'Creator': row['creator_address'],
            'Cluster': row['super_cluster_id'],
            'Funders': row['funder_count'] or 0,
            'SOL Received': round(row['total_sol_received'] or 0, 2),
            'Token Mint': row['mint'] or 'N/A',
            'Token Risk': row['risk_level'] or 'Pending',
            'Rug Probability': round(row['rug_probability'] or 0, 4) if row['rug_probability'] else 'N/A',
            'Created': row['created_at'] or 'N/A'
        })

    memberships_df = pd.DataFrame(memberships)

    # 4. FUNDER NETWORK ANALYSIS
    print("  → Funder networks")
    infra_list = ','.join(['?' for _ in infra_and_cex])
    cursor.execute(f"""
        SELECT
            fn.network_id,
            fn.network_name,
            COUNT(DISTINCT CASE WHEN fnm.funder_address NOT IN ({infra_list}) THEN fnm.funder_address END) as total_members,
            SUM(CASE WHEN cf.funder_address NOT IN ({infra_list}) THEN cf.amount_sol ELSE 0 END) as total_sol,
            COUNT(DISTINCT CASE WHEN cf.funder_address NOT IN ({infra_list}) THEN ta.mint END) as tokens_created,
            SUM(CASE WHEN ta.risk_level = 'HIGH' AND cf.funder_address NOT IN ({infra_list}) THEN 1 ELSE 0 END) as high_risk_tokens,
            GROUP_CONCAT(DISTINCT CASE WHEN fnm.funder_address NOT IN ({infra_list}) THEN fnm.funder_address END) as top_funders
        FROM funding_networks fn
        LEFT JOIN funding_network_members fnm ON fn.network_id = fnm.network_id
        LEFT JOIN creator_funders cf ON fnm.funder_address = cf.funder_address
        LEFT JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
        GROUP BY fn.network_id
        ORDER BY total_sol DESC
    """, tuple(infra_and_cex) * 5)

    networks = []
    for row in cursor.fetchall():
        networks.append({
            'Network ID': row['network_id'],
            'Network Name': row['network_name'] or 'Unknown',
            'Members': row['total_members'],
            'Total SOL': round(row['total_sol'] or 0, 2),
            'Tokens Created': row['tokens_created'] or 0,
            'High Risk Tokens': row['high_risk_tokens'] or 0,
            'Top Funders': row['top_funders'] or 'None'
        })

    networks_df = pd.DataFrame(networks)

    # 5. TOKEN ANALYSIS
    print("  → Token analysis")
    infra_list = ','.join(['?' for _ in infra_and_cex])
    cursor.execute(f"""
        SELECT
            ta.mint,
            ta.earliest_tx_creator,
            ta.created_at,
            ta.price_current,
            ta.market_cap_highest,
            ta.market_cap_highest_at,
            ta.risk_level,
            ta.rug_probability,
            cscm.super_cluster_id,
            COUNT(DISTINCT CASE WHEN cf.funder_address NOT IN ({infra_list}) THEN cf.funder_address END) as funder_count
        FROM token_analysis ta
        LEFT JOIN creator_super_cluster_membership cscm ON ta.earliest_tx_creator = cscm.creator_address
        LEFT JOIN creator_funders cf ON ta.earliest_tx_creator = cf.creator_address
        GROUP BY ta.mint
        ORDER BY ta.rug_probability DESC
    """, tuple(infra_and_cex))

    tokens = []
    for row in cursor.fetchall():
        creator = row['earliest_tx_creator']
        creator_short = creator[:16] + '...' if creator else 'Unknown'
        tokens.append({
            'Token': row['mint'],
            'Creator': creator_short,
            'Created': row['created_at'],
            'Current Price': round(row['price_current'] or 0, 8),
            'Peak Market Cap': round(row['market_cap_highest'] or 0, 2),
            'Peak At': row['market_cap_highest_at'] or 'N/A',
            'Risk Level': row['risk_level'],
            'Rug Probability': round(row['rug_probability'] or 0, 4),
            'Cluster': row['super_cluster_id'] or 'None',
            'Funders': row['funder_count'] or 0
        })

    tokens_df = pd.DataFrame(tokens)

    # 6. RISK SUMMARY
    print("  → Risk analysis")
    cursor.execute("""
        SELECT
            risk_level,
            COUNT(*) as token_count,
            ROUND(AVG(rug_probability), 4) as avg_rug_prob,
            ROUND(AVG(market_cap_highest), 2) as avg_peak_mc,
            SUM(CASE WHEN rug_probability > 0.7 THEN 1 ELSE 0 END) as critical_count
        FROM token_analysis
        GROUP BY risk_level
    """)

    risk_summary = []
    for row in cursor.fetchall():
        risk_summary.append({
            'Risk Level': row['risk_level'],
            'Token Count': row['token_count'],
            'Avg Rug Probability': row['avg_rug_prob'],
            'Avg Peak MC': round(row['avg_peak_mc'], 2),
            'Critical (>70%)': row['critical_count']
        })

    risk_df = pd.DataFrame(risk_summary)

    conn.close()

    return {
        'Super-Clusters': clusters_df,
        'Root Operators': root_ops_df,
        'Creator Memberships': memberships_df,
        'Funding Networks': networks_df,
        'Token Analysis': tokens_df,
        'Risk Summary': risk_df
    }

def write_excel(data_dict):
    """Write all dataframes to Excel with formatting"""

    print(f"\nWriting to {OUTPUT_FILE}...")

    with pd.ExcelWriter(OUTPUT_FILE, engine='openpyxl') as writer:
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

    print(f"✅ Export complete: {OUTPUT_FILE}\n")

    # Print summary
    print("=" * 70)
    print("EXPORT SUMMARY")
    print("=" * 70)
    for sheet_name, df in data_dict.items():
        print(f"  {sheet_name}: {len(df)} rows")
    print("=" * 70)

if __name__ == "__main__":
    data = get_data()
    write_excel(data)
