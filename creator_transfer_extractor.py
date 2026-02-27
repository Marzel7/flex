#!/usr/bin/env python3
"""
Extract inter-creator transfers from blockchain

Identifies when creators send SOL to other addresses, particularly:
- To other creators
- To funding intermediaries
- Creating funding chains
"""

import sqlite3
import json
from typing import Dict, Set, List

DB_PATH = "flex_complete_database.db"

class CreatorTransferExtractor:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ensure_db()
    
    def _ensure_db(self):
        """Create tables for tracking creator transfers"""
        conn = sqlite3.connect(self.db_path, timeout=60)
        cursor = conn.cursor()
        
        # Table for tracking when creators send SOL
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_outgoing_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL,
                recipient_address TEXT NOT NULL,
                amount_sol REAL NOT NULL,
                transaction_signature TEXT UNIQUE,
                block_time INTEGER,
                recipient_is_creator BOOLEAN DEFAULT 0,
                recipient_is_funder BOOLEAN DEFAULT 0,
                is_funding_intermediary BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Table for tracking funding chains (creator → ... → funder → creator)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS funding_chains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_creator TEXT NOT NULL,
                chain_json TEXT NOT NULL,
                final_funder TEXT,
                final_creator TEXT,
                total_hops INTEGER,
                total_sol REAL,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_creator, final_creator, final_funder)
            )
        """)
        
        conn.commit()
        conn.close()
    
    def extract_creator_transfers_from_tx(self, creator: str, tx_signature: str):
        """
        Extract where a creator sent SOL in a transaction
        
        This requires parsing the transaction to find:
        1. What addresses received SOL from the creator
        2. How much SOL was sent
        3. Whether recipients are known creators/funders
        """
        try:
            # TODO: Implement transaction parsing via Helius API
            # For now, we need to:
            # 1. Get transaction details via Helius
            # 2. Parse token transfers and SOL transfers
            # 3. Identify recipient addresses
            # 4. Check if recipients are in our creator/funder lists
            # 5. Store in creator_outgoing_transfers
            pass
        except Exception as e:
            print(f"[CREATOR_TRANSFER] Error extracting transfers: {e}")
    
    def build_funding_chains(self):
        """
        Identify funding chains:
        Creator A → Intermediary → Funder → Creator B
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()
            
            # Find creators that sent to known funders
            cursor.execute("""
                SELECT DISTINCT 
                    cot.creator_address,
                    cot.recipient_address,
                    cot.amount_sol
                FROM creator_outgoing_transfers cot
                WHERE cot.recipient_address IN (
                    SELECT DISTINCT funder_address FROM creator_funders
                )
            """)
            
            direct_funder_chains = cursor.fetchall()
            
            # Find creators that sent to addresses that sent to funders
            # (2-hop chains)
            cursor.execute("""
                SELECT DISTINCT
                    cot1.creator_address,
                    cot1.recipient_address,
                    cot2.recipient_address,
                    cot2.amount_sol
                FROM creator_outgoing_transfers cot1
                JOIN funder_incoming_transfers fit ON cot1.recipient_address = fit.sender_address
                JOIN creator_outgoing_transfers cot2 ON fit.funder_address = cot2.creator_address
            """)
            
            multi_hop_chains = cursor.fetchall()
            
            print(f"[FUNDING_CHAINS] Found {len(direct_funder_chains)} direct creator→funder transfers")
            print(f"[FUNDING_CHAINS] Found {len(multi_hop_chains)} multi-hop chains")
            
            conn.close()
            return direct_funder_chains, multi_hop_chains
            
        except Exception as e:
            print(f"[FUNDING_CHAINS] Error building chains: {e}")
            return [], []

if __name__ == "__main__":
    extractor = CreatorTransferExtractor()
    extractor.build_funding_chains()
