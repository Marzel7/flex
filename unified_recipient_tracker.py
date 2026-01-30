#!/usr/bin/env python3
"""
Unified Creator Recipient Tracking System

Merges creator_outgoing_transfers and creator_tx_ledger into a single system
that tracks all recipient addresses and detects cross-creator linkages.

Key Features:
- Consolidates high-confidence recipients from creator_outgoing_transfers
- Adds continuous monitoring via creator_tx_ledger
- Implements cross-reference detection: when address X appears for creator A,
  checks if X is also linked to creators B, C, D (network detection)
- Provides network coordinator identification
"""

import sqlite3
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass

DB_PATH = "pumpswap_tokens.db"


@dataclass
class RecipientLink:
    """Represents a creator → recipient relationship"""
    creator_address: str
    recipient_address: str
    total_sol_sent: float
    transfer_count: int
    last_transfer_time: datetime
    confidence: str  # 'high' or 'medium'
    source: str  # 'explicit' (from creator_outgoing_transfers) or 'heuristic' (from creator_tx_ledger)
    is_cex: bool = False
    cex_exchange: Optional[str] = None
    is_suspicious: bool = False


@dataclass
class NetworkCoordinator:
    """Represents a potential coordinated actor across multiple creators"""
    address: str
    creator_count: int  # How many creators link to this address
    creators: List[str]  # List of creator addresses
    total_sol_moved: float
    network_confidence: str  # 'high', 'medium', 'low'
    is_cex: bool
    suspicious_flags: List[str]


class UnifiedRecipientTracker:
    """Manages unified recipient tracking with cross-reference detection"""

    def __init__(self):
        self._ensure_db()

    def _ensure_db(self):
        """Create unified recipient tracking schema"""
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Unified recipient master table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_recipients_unified (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL,
                recipient_address TEXT NOT NULL,
                total_sol_sent REAL DEFAULT 0,
                transfer_count INTEGER DEFAULT 0,
                last_transfer_time TIMESTAMP,
                confidence TEXT DEFAULT 'medium',  -- 'high' or 'medium'
                source TEXT NOT NULL,              -- 'explicit' or 'heuristic'
                transaction_signatures TEXT,       -- JSON array of signatures
                is_cex BOOLEAN DEFAULT 0,
                cex_exchange TEXT,
                cex_type TEXT,
                is_suspicious BOOLEAN DEFAULT 0,
                suspicious_reasons TEXT,           -- JSON array
                network_flags TEXT,                -- JSON array (links to other creators)
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(creator_address, recipient_address),
                FOREIGN KEY(creator_address) REFERENCES creator_watch(creator_pubkey)
            )
        """)

        # Network coordinator detection table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS network_coordinators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coordinator_address TEXT NOT NULL UNIQUE,
                creator_count INTEGER,
                creators_linked TEXT,             -- JSON array
                total_sol_moved REAL,
                network_confidence TEXT,          -- 'high', 'medium', 'low'
                is_cex BOOLEAN DEFAULT 0,
                cex_exchange TEXT,
                suspicious_flags TEXT,            -- JSON array
                detection_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Cross-reference log (audit trail)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recipient_cross_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_address TEXT NOT NULL,
                creator_a TEXT NOT NULL,
                creator_b TEXT NOT NULL,
                shared_context TEXT,              -- How they're linked
                first_detected TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(recipient_address, creator_a, creator_b)
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recipient_creator ON creator_recipients_unified(creator_address)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recipient_address ON creator_recipients_unified(recipient_address)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_coordinator ON network_coordinators(coordinator_address)")

        conn.commit()
        conn.close()

    def merge_from_outgoing_transfers(self) -> Dict[str, int]:
        """
        Migrate high-confidence data from creator_outgoing_transfers.
        Returns stats on what was migrated.
        """
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        stats = {
            'total_merged': 0,
            'cex_detected': 0,
            'skipped': 0
        }

        try:
            # Get all records from creator_outgoing_transfers
            cursor.execute("""
                SELECT creator_address, recipient_address, amount_sol,
                       transaction_signature, recipient_type, is_suspicious
                FROM creator_outgoing_transfers
            """)
            rows = cursor.fetchall()

            for row in rows:
                creator = row['creator_address']
                recipient = row['recipient_address']
                amount = row['amount_sol']
                sig = row['transaction_signature']
                rec_type = row['recipient_type']
                is_suspicious = row['is_suspicious']

                # Determine CEX flag
                is_cex = 0
                cex_exchange = None
                if rec_type and 'cex' in rec_type.lower():
                    is_cex = 1
                    # Try to extract exchange name
                    if rec_type != 'cex_exchange':
                        cex_exchange = rec_type.replace('_', ' ').title()
                    stats['cex_detected'] += 1

                try:
                    # Upsert into unified table
                    cursor.execute("""
                        INSERT INTO creator_recipients_unified
                        (creator_address, recipient_address, total_sol_sent, transfer_count,
                         confidence, source, transaction_signatures, is_cex, cex_exchange,
                         is_suspicious)
                        VALUES (?, ?, ?, 1, 'high', 'explicit', ?, ?, ?, ?)
                        ON CONFLICT(creator_address, recipient_address) DO UPDATE SET
                            total_sol_sent = total_sol_sent + excluded.total_sol_sent,
                            transfer_count = transfer_count + excluded.transfer_count,
                            is_cex = MAX(is_cex, excluded.is_cex),
                            updated_at = CURRENT_TIMESTAMP
                    """, (creator, recipient, amount, json.dumps([sig]) if sig else "[]", is_cex, cex_exchange, is_suspicious))

                    stats['total_merged'] += 1

                except Exception as e:
                    print(f"[MERGE] Error merging {creator} → {recipient}: {e}")
                    stats['skipped'] += 1

            conn.commit()
            return stats

        finally:
            conn.close()

    def merge_from_tx_ledger(self) -> Dict[str, int]:
        """
        Merge outgoing transfers from creator_tx_ledger (continuous polling data).
        Only includes outgoing transfers (delta_sol_lamports < 0).
        Returns stats on what was merged.
        """
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        stats = {
            'total_merged': 0,
            'new_recipients': 0,
            'updated_existing': 0
        }

        try:
            # Get all outgoing transfers from creator_tx_ledger
            cursor.execute("""
                SELECT creator_pubkey, counterparty, ABS(SUM(delta_sol_lamports)) as total_sol,
                       COUNT(*) as transfer_count, MAX(blockTime) as last_time,
                       GROUP_CONCAT(signature, ',') as signatures
                FROM creator_tx_ledger
                WHERE delta_sol_lamports < 0 AND counterparty IS NOT NULL
                GROUP BY creator_pubkey, counterparty
            """)
            rows = cursor.fetchall()

            for row in rows:
                creator = row['creator_pubkey']
                recipient = row['counterparty']
                amount = row['total_sol'] / 1e9  # Convert lamports to SOL
                count = row['transfer_count']
                last_time = row['last_time']
                sigs = row['signatures'].split(',') if row['signatures'] else []

                try:
                    # Check if this recipient already exists (from explicit transfers)
                    cursor.execute("""
                        SELECT confidence FROM creator_recipients_unified
                        WHERE creator_address = ? AND recipient_address = ?
                    """, (creator, recipient))
                    existing = cursor.fetchone()

                    if existing:
                        # Update with heuristic data
                        cursor.execute("""
                            UPDATE creator_recipients_unified
                            SET transfer_count = transfer_count + ?,
                                total_sol_sent = total_sol_sent + ?,
                                last_transfer_time = MAX(last_transfer_time, ?),
                                transaction_signatures = json_insert(
                                    COALESCE(transaction_signatures, '[]'),
                                    '$[#]',
                                    json_each.value
                                ),
                                updated_at = CURRENT_TIMESTAMP
                            WHERE creator_address = ? AND recipient_address = ?
                        """, (count, amount, datetime.fromtimestamp(last_time), creator, recipient))
                        stats['updated_existing'] += 1
                    else:
                        # New recipient from heuristic data
                        cursor.execute("""
                            INSERT INTO creator_recipients_unified
                            (creator_address, recipient_address, total_sol_sent, transfer_count,
                             confidence, source, transaction_signatures, last_transfer_time)
                            VALUES (?, ?, ?, ?, 'medium', 'heuristic', ?, ?)
                        """, (creator, recipient, amount, count, json.dumps(sigs),
                             datetime.fromtimestamp(last_time)))
                        stats['new_recipients'] += 1

                    stats['total_merged'] += 1

                except Exception as e:
                    print(f"[MERGE] Error merging tx_ledger {creator} → {recipient}: {e}")

            conn.commit()
            return stats

        finally:
            conn.close()

    def detect_network_coordinators(self) -> List[NetworkCoordinator]:
        """
        Find addresses that appear as recipients for multiple creators.
        These are potential network coordinators or shared infrastructure.
        """
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        coordinators = []

        try:
            # Find recipients linked to 2+ creators
            cursor.execute("""
                SELECT recipient_address,
                       COUNT(DISTINCT creator_address) as unique_creators,
                       SUM(total_sol_sent) as total_sol,
                       MAX(CASE WHEN is_cex THEN 1 ELSE 0 END) as is_cex,
                       MAX(cex_exchange) as cex_exchange
                FROM creator_recipients_unified
                GROUP BY recipient_address
                HAVING unique_creators >= 2
                ORDER BY total_sol DESC
            """)
            rows = cursor.fetchall()

            for row in rows:
                address = row['recipient_address']
                creator_count = row['unique_creators']
                total_sol = row['total_sol'] or 0
                is_cex = bool(row['is_cex'])
                cex_exchange = row['cex_exchange']

                # Get list of creators linked to this recipient
                cursor.execute("""
                    SELECT DISTINCT creator_address FROM creator_recipients_unified
                    WHERE recipient_address = ?
                """, (address,))
                creators = [r[0] for r in cursor.fetchall()]

                # Determine confidence level
                if is_cex:
                    confidence = 'low'  # CEX addresses are expected to receive from many creators
                elif creator_count >= 5:
                    confidence = 'high'  # 5+ creators → suspicious
                elif creator_count >= 3:
                    confidence = 'high'  # 3+ creators → notable
                else:
                    confidence = 'medium'  # 2 creators → could be coincidence

                suspicious_flags = []
                if creator_count >= 5:
                    suspicious_flags.append(f"multiple_creator_links({creator_count})")
                if total_sol > 500:
                    suspicious_flags.append(f"high_volume({total_sol:.2f}SOL)")
                if is_cex and creator_count > 10:
                    suspicious_flags.append("cex_hub")

                coordinator = NetworkCoordinator(
                    address=address,
                    creator_count=creator_count,
                    creators=creators,
                    total_sol_moved=total_sol,
                    network_confidence=confidence,
                    is_cex=is_cex,
                    suspicious_flags=suspicious_flags
                )
                coordinators.append(coordinator)

                # Save to database
                self._save_coordinator(coordinator)

            return coordinators

        finally:
            conn.close()

    def _save_coordinator(self, coordinator: NetworkCoordinator):
        """Save or update coordinator in database"""
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO network_coordinators
                (coordinator_address, creator_count, creators_linked, total_sol_moved,
                 network_confidence, is_cex, cex_exchange, suspicious_flags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(coordinator_address) DO UPDATE SET
                    creator_count = excluded.creator_count,
                    creators_linked = excluded.creators_linked,
                    total_sol_moved = excluded.total_sol_moved,
                    network_confidence = excluded.network_confidence,
                    last_updated = CURRENT_TIMESTAMP
            """, (coordinator.address, coordinator.creator_count,
                  json.dumps(coordinator.creators), coordinator.total_sol_moved,
                  coordinator.network_confidence, coordinator.is_cex,
                  None, json.dumps(coordinator.suspicious_flags)))

            conn.commit()
        finally:
            conn.close()

    def log_cross_reference(self, recipient: str, creator_a: str, creator_b: str, context: str = "shared_recipient"):
        """Log that two creators share a recipient address"""
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO recipient_cross_references
                (recipient_address, creator_a, creator_b, shared_context)
                VALUES (?, ?, ?, ?)
                ON CONFLICT DO NOTHING
            """, (recipient, creator_a, creator_b, context))

            conn.commit()
        finally:
            conn.close()

    def get_recipient_links_for_creator(self, creator_address: str) -> List[RecipientLink]:
        """Get all recipient links for a creator"""
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        links = []

        try:
            cursor.execute("""
                SELECT recipient_address, total_sol_sent, transfer_count,
                       last_transfer_time, confidence, source, is_cex, cex_exchange, is_suspicious
                FROM creator_recipients_unified
                WHERE creator_address = ?
                ORDER BY total_sol_sent DESC
            """, (creator_address,))

            for row in cursor.fetchall():
                link = RecipientLink(
                    creator_address=creator_address,
                    recipient_address=row['recipient_address'],
                    total_sol_sent=row['total_sol_sent'],
                    transfer_count=row['transfer_count'],
                    last_transfer_time=datetime.fromisoformat(row['last_transfer_time']) if row['last_transfer_time'] else None,
                    confidence=row['confidence'],
                    source=row['source'],
                    is_cex=bool(row['is_cex']),
                    cex_exchange=row['cex_exchange'],
                    is_suspicious=bool(row['is_suspicious'])
                )
                links.append(link)

            return links

        finally:
            conn.close()

    def find_shared_recipients(self, creator_address: str) -> Dict[str, List[str]]:
        """
        Find which other creators share recipient addresses with the given creator.
        Returns dict: {recipient_address: [other_creators...]}
        """
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        shared = {}

        try:
            cursor.execute("""
                SELECT DISTINCT cur.recipient_address, cur.creator_address
                FROM creator_recipients_unified cur
                WHERE cur.recipient_address IN (
                    SELECT recipient_address
                    FROM creator_recipients_unified
                    WHERE creator_address = ?
                )
                AND cur.creator_address != ?
            """, (creator_address, creator_address))

            for row in cursor.fetchall():
                recipient, other_creator = row
                if recipient not in shared:
                    shared[recipient] = []
                shared[recipient].append(other_creator)

            return shared

        finally:
            conn.close()

    def get_network_coordinators(self, min_creators: int = 2) -> List[NetworkCoordinator]:
        """Get network coordinators with at least min_creators links"""
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        coordinators = []

        try:
            cursor.execute("""
                SELECT coordinator_address, creator_count, creators_linked,
                       total_sol_moved, network_confidence, is_cex, cex_exchange, suspicious_flags
                FROM network_coordinators
                WHERE creator_count >= ?
                ORDER BY creator_count DESC, total_sol_moved DESC
            """, (min_creators,))

            for row in cursor.fetchall():
                creators = json.loads(row['creators_linked']) if row['creators_linked'] else []
                flags = json.loads(row['suspicious_flags']) if row['suspicious_flags'] else []

                coordinator = NetworkCoordinator(
                    address=row['coordinator_address'],
                    creator_count=row['creator_count'],
                    creators=creators,
                    total_sol_moved=row['total_sol_moved'],
                    network_confidence=row['network_confidence'],
                    is_cex=bool(row['is_cex']),
                    suspicious_flags=flags
                )
                coordinators.append(coordinator)

            return coordinators

        finally:
            conn.close()

    def run_full_merge_and_analysis(self) -> Dict:
        """Execute complete merge and cross-reference detection"""
        print("[UNIFIED] Starting full merge and analysis...")

        # Phase 1: Merge explicit data
        print("[UNIFIED] Phase 1: Merging creator_outgoing_transfers...")
        out_stats = self.merge_from_outgoing_transfers()
        print(f"  ✓ Merged {out_stats['total_merged']} transfers ({out_stats['cex_detected']} CEX)")

        # Phase 2: Merge heuristic data
        print("[UNIFIED] Phase 2: Merging creator_tx_ledger...")
        tx_stats = self.merge_from_tx_ledger()
        print(f"  ✓ Merged {tx_stats['total_merged']} transfers "
              f"({tx_stats['new_recipients']} new recipients, "
              f"{tx_stats['updated_existing']} updated)")

        # Phase 3: Detect network coordinators
        print("[UNIFIED] Phase 3: Detecting network coordinators...")
        coordinators = self.detect_network_coordinators()
        print(f"  ✓ Found {len(coordinators)} potential coordinators")

        # Phase 4: Log cross-references
        print("[UNIFIED] Phase 4: Logging cross-references...")
        cross_refs = 0
        for coordinator in coordinators:
            if len(coordinator.creators) >= 2:
                for i, creator_a in enumerate(coordinator.creators):
                    for creator_b in coordinator.creators[i+1:]:
                        self.log_cross_reference(
                            coordinator.address,
                            creator_a,
                            creator_b,
                            context=f"{coordinator.creator_count}_creator_link"
                        )
                        cross_refs += 1

        print(f"  ✓ Logged {cross_refs} cross-references")

        return {
            'outgoing_transfers': out_stats,
            'tx_ledger': tx_stats,
            'coordinators': len(coordinators),
            'cross_references': cross_refs
        }


def main():
    """Run unified tracking setup and analysis"""
    tracker = UnifiedRecipientTracker()

    # Run full merge
    results = tracker.run_full_merge_and_analysis()

    print("\n" + "="*60)
    print("UNIFIED RECIPIENT TRACKING - COMPLETE")
    print("="*60)
    print(f"Outgoing Transfers Merged: {results['outgoing_transfers']['total_merged']}")
    print(f"TX Ledger Merged: {results['tx_ledger']['total_merged']}")
    print(f"Network Coordinators Detected: {results['coordinators']}")
    print(f"Cross-References Logged: {results['cross_references']}")

    # Show top coordinators
    print("\nTop Network Coordinators:")
    coordinators = tracker.get_network_coordinators(min_creators=2)
    for coord in coordinators[:5]:
        print(f"  {coord.address[:16]}... → {coord.creator_count} creators, "
              f"{coord.total_sol_moved:.2f} SOL, confidence: {coord.network_confidence}")
        if coord.suspicious_flags:
            print(f"    Flags: {', '.join(coord.suspicious_flags)}")


if __name__ == "__main__":
    main()
