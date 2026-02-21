#!/usr/bin/env python3
"""
Backfill cluster information for existing tokens that don't have it yet.
This script updates token_analysis table with cluster information by checking
if each token's creator belongs to a cross-funding cluster.
"""

import sqlite3
import sys
from cluster_risk_checker import check_creator

DB_PATH = "pumpswap_tokens.db"

def backfill_clusters(limit=None):
    """Backfill cluster information for tokens without it"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get tokens without cluster info but with creator
        query = """
            SELECT mint, earliest_tx_creator 
            FROM token_analysis 
            WHERE cluster_id IS NULL AND earliest_tx_creator IS NOT NULL
            ORDER BY analyzed_at DESC
        """
        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        tokens = cursor.fetchall()
        
        total = len(tokens)
        print(f"[BACKFILL] Found {total} tokens needing cluster info")
        
        updated = 0
        with_cluster = 0
        
        for idx, token in enumerate(tokens, 1):
            mint = token['mint']
            creator = token['earliest_tx_creator']
            
            try:
                cluster_info = check_creator(creator)
                
                if cluster_info.get('in_cluster'):
                    cluster_id = cluster_info.get('cluster_id')
                    cluster_name = cluster_info.get('cluster_label', cluster_id)
                    cluster_multiplier = cluster_info.get('risk_multiplier', 1.0)
                    
                    cursor.execute("""
                        UPDATE token_analysis 
                        SET cluster_id = ?, cluster_name = ?, cluster_risk_multiplier = ?
                        WHERE mint = ?
                    """, (cluster_id, cluster_name, cluster_multiplier, mint))
                    
                    with_cluster += 1
                    print(f"[{idx}/{total}] ✅ {mint[:8]}... → {cluster_name} ({cluster_multiplier}x)")
                else:
                    cursor.execute("""
                        UPDATE token_analysis 
                        SET cluster_id = ?, cluster_name = ?, cluster_risk_multiplier = ?
                        WHERE mint = ?
                    """, (None, None, 1.0, mint))
                    print(f"[{idx}/{total}] ℹ {mint[:8]}... → no cluster")
                
                updated += 1
                
                if idx % 50 == 0:
                    conn.commit()
                    print(f"[BACKFILL] Committed {idx} tokens...")
                    
            except Exception as e:
                print(f"[ERROR] Failed to check {mint}: {e}")
        
        conn.commit()
        conn.close()
        
        print(f"\n[BACKFILL] ✅ Complete!")
        print(f"  Total updated: {updated}")
        print(f"  With cluster: {with_cluster}")
        print(f"  Without cluster: {updated - with_cluster}")
        
    except Exception as e:
        print(f"[ERROR] Backfill failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    backfill_clusters(limit)
