#!/usr/bin/env python3
"""
Phase 3: Transfer Index Manager

Indexes all SOL transfers from parsed transactions into a local SQLite table.
Enables SQL-based funding analysis, eliminating 90-95% of historical RPC scanning.

This module provides:
1. Transfer parsing from raw transactions
2. Persistent storage in transfer_index table
3. Query builders for common analysis patterns
4. Monitoring and diagnostics

Usage:
    indexer = TransferIndexer(db_path)

    # Index transfers from a transaction
    transfers = indexer.index_transaction(transaction_dict)

    # Query transfers using builders
    funders = indexer.get_funders(creator_address)
    funded_creators = indexer.get_funded_creators(whale_address)
    shared_funders = indexer.find_clusters(creator_list)

Architecture:
    Raw Transaction
        ↓
    Transfer Parser (extract_transfers)
        ↓
    Validation (is_valid flag)
        ↓
    transfer_index Table (local SQLite)
        ↓
    SQL Queries (instant analytics)

Expected Impact:
    - Phase 1: 60% RPC reduction (cursors)
    - Phase 2: 30-35% additional reduction (caching)
    - Phase 3: 90-95% reduction of remaining calls (indexing)
    - Combined: 98%+ total RPC reduction

Storage:
    ~320 bytes per transfer (including indexes)
    1M transfers = ~320 MB
    10M transfers = ~3.2 GB
"""

import sqlite3
import json
import time
import logging
from typing import Optional, List, Dict, Set, Tuple
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Transfer:
    """Represents a SOL transfer on Solana."""
    signature: str
    source: str
    destination: str
    amount_lamports: int
    slot: int
    block_time: Optional[int]
    transfer_type: str = 'transfer'
    is_valid: bool = True

    @property
    def amount_sol(self) -> float:
        """Convert lamports to SOL."""
        return self.amount_lamports / 1e9


class TransferIndexer:
    """
    Manages transfer indexing for Phase 3 deployment.

    Stores all parsed SOL transfers in a local SQLite table,
    enabling SQL-based funding analysis instead of RPC scanning.
    """

    def __init__(self, db_path: str):
        """Initialize transfer indexer with database path."""
        self.db_path = db_path
        self._ensure_table()

    def _get_conn(self) -> Optional[sqlite3.Connection]:
        """Get database connection with WAL mode."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            conn.execute("PRAGMA busy_timeout = 60000")
            conn.execute("PRAGMA journal_mode = WAL")
            return conn
        except Exception as e:
            logger.error(f"[TRANSFER_INDEX] Failed to get connection: {e}")
            return None

    def _ensure_table(self) -> None:
        """Verify transfer_index table exists (created by migration)."""
        try:
            conn = self._get_conn()
            if conn is None:
                return

            cursor = conn.cursor()
            
            # Just verify table exists - schema created by migration
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='transfer_index'"
            )
            if not cursor.fetchone():
                logger.warning(
                    "[TRANSFER_INDEX] Table not found. Run Phase 3 migration: "
                    "sqlite3 flex_complete_database.db < database/migrations/phase3_transfer_index_migration.sql"
                )
            
            conn.close()

        except Exception as e:
            logger.error(f"[TRANSFER_INDEX] Failed to verify table: {e}")

    # ===== TRANSFER PARSING =====

    def extract_transfers(self, transaction: Dict) -> List[Transfer]:
        """
        Extract SOL transfers from a transaction.

        Parses system program transfers and returns list of Transfer objects.
        Returns empty list if no transfers found or parsing fails.
        """
        transfers = []

        try:
            signature = transaction.get('signature', '')
            slot = transaction.get('slot', 0)
            block_time = transaction.get('blockTime')

            if not signature:
                return transfers

            # Parse instructions for transfers
            instructions = transaction.get('instructions', [])

            for instruction in instructions:
                program = instruction.get('program', '')
                parsed = instruction.get('parsed', {})

                # Look for system program transfers
                if program == 'system' and parsed.get('type') == 'transfer':
                    try:
                        info = parsed.get('info', {})

                        transfer = Transfer(
                            signature=signature,
                            source=info.get('source', ''),
                            destination=info.get('destination', ''),
                            amount_lamports=int(info.get('lamports', 0)),
                            slot=slot,
                            block_time=block_time,
                            transfer_type='transfer',
                            is_valid=1
                        )

                        # Validate before adding
                        if self._validate_transfer(transfer):
                            transfers.append(transfer)

                    except Exception as e:
                        logger.warning(f"[TRANSFER_INDEX] Failed to parse instruction: {e}")
                        continue

            return transfers

        except Exception as e:
            logger.error(f"[TRANSFER_INDEX] Failed to extract transfers from {signature}: {e}")
            return []

    def _validate_transfer(self, transfer: Transfer) -> bool:
        """
        Validate transfer data.

        Checks:
        - Addresses are valid Solana addresses (~44 chars)
        - Amount is non-negative
        - Signature exists
        """
        try:
            # Check signature
            if not transfer.signature or len(transfer.signature) < 10:
                return False

            # Check addresses (Solana addresses ~44 chars)
            if not transfer.source or len(transfer.source) < 32:
                return False

            if not transfer.destination or len(transfer.destination) < 32:
                return False

            # Check amount
            if transfer.amount_lamports < 0:
                return False

            # Check slot
            if transfer.slot < 0:
                return False

            return True

        except Exception as e:
            logger.warning(f"[TRANSFER_INDEX] Validation failed: {e}")
            return False

    # ===== INDEXING =====

    def index_transaction(self, transaction: Dict) -> int:
        """
        Parse transaction and index all transfers.

        Returns count of transfers indexed.
        """
        try:
            transfers = self.extract_transfers(transaction)

            if not transfers:
                return 0

            indexed = 0
            for transfer in transfers:
                if self._index_transfer(transfer):
                    indexed += 1

            return indexed

        except Exception as e:
            logger.error(f"[TRANSFER_INDEX] Failed to index transaction: {e}")
            return 0

    def _index_transfer(self, transfer: Transfer) -> bool:
        """Store transfer in transfer_index table."""
        try:
            conn = self._get_conn()
            if conn is None:
                return False

            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR IGNORE INTO transfer_index
                (signature, source, destination, amount_lamports,
                 slot, block_time, is_valid, transfer_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                transfer.signature,
                transfer.source,
                transfer.destination,
                transfer.amount_lamports,
                transfer.slot,
                transfer.block_time,
                int(transfer.is_valid),
                transfer.transfer_type
            ))

            success = cursor.rowcount > 0
            conn.commit()
            conn.close()

            return success

        except Exception as e:
            logger.warning(f"[TRANSFER_INDEX] Failed to index transfer: {e}")
            return False

    # ===== QUERY BUILDERS =====

    def get_funders(self, destination: str, limit: int = 1000) -> List[str]:
        """
        Get all unique funders of an address.

        Returns list of source addresses that have transferred to destination.
        """
        try:
            conn = self._get_conn()
            if conn is None:
                return []

            cursor = conn.cursor()

            cursor.execute("""
                SELECT DISTINCT source
                FROM transfer_index
                WHERE destination = ?
                  AND is_valid = 1
                  AND amount_lamports > 0
                ORDER BY block_time DESC
                LIMIT ?
            """, (destination, limit))

            funders = [row[0] for row in cursor.fetchall()]
            conn.close()

            return funders

        except Exception as e:
            logger.error(f"[TRANSFER_INDEX] Failed to get funders: {e}")
            return []

    def get_funded_creators(
        self,
        source: str,
        limit: int = 1000,
        min_amount_sol: float = 0.0
    ) -> List[Tuple[str, int, float]]:
        """
        Get all addresses funded by a source address.

        Returns list of (destination, num_transfers, total_sol) tuples.
        """
        try:
            conn = self._get_conn()
            if conn is None:
                return []

            min_lamports = int(min_amount_sol * 1e9)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                  destination,
                  COUNT(*) as num_transfers,
                  SUM(amount_lamports) / 1e9 as total_sol
                FROM transfer_index
                WHERE source = ?
                  AND is_valid = 1
                  AND amount_lamports > ?
                GROUP BY destination
                ORDER BY total_sol DESC
                LIMIT ?
            """, (source, min_lamports, limit))

            results = [
                (row[0], int(row[1]), float(row[2]))
                for row in cursor.fetchall()
            ]
            conn.close()

            return results

        except Exception as e:
            logger.error(f"[TRANSFER_INDEX] Failed to get funded creators: {e}")
            return []

    def find_clusters(self, destination_addresses: List[str]) -> List[Dict]:
        """
        Find creators that share common funders (clustering).

        Returns list of clusters with creator pairs and shared funders.
        """
        try:
            conn = self._get_conn()
            if conn is None:
                return []

            if not destination_addresses:
                return []

            # Build placeholders
            placeholders = ','.join(['?' for _ in destination_addresses])

            cursor = conn.cursor()

            # Find pairs of creators sharing funders
            cursor.execute(f"""
                WITH creator_funders AS (
                  SELECT DISTINCT
                    destination as creator,
                    source as funder
                  FROM transfer_index
                  WHERE destination IN ({placeholders})
                    AND is_valid = 1
                )
                SELECT
                  a.creator as creator1,
                  b.creator as creator2,
                  a.funder,
                  COUNT(*) as shared_transfers
                FROM creator_funders a
                JOIN creator_funders b
                  ON a.funder = b.funder
                  AND a.creator < b.creator
                GROUP BY a.creator, b.creator, a.funder
                ORDER BY shared_transfers DESC
                LIMIT 1000
            """, destination_addresses)

            clusters = [
                {
                    'creator1': row[0],
                    'creator2': row[1],
                    'funder': row[2],
                    'shared_transfers': row[3]
                }
                for row in cursor.fetchall()
            ]
            conn.close()

            return clusters

        except Exception as e:
            logger.error(f"[TRANSFER_INDEX] Failed to find clusters: {e}")
            return []

    def get_funding_timeline(self, destination: str) -> List[Dict]:
        """
        Get funding timeline: when and how much funded by each funder.

        Returns list of (date, funder, num_transfers, total_sol) tuples.
        """
        try:
            conn = self._get_conn()
            if conn is None:
                return []

            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                  DATE(datetime(block_time, 'unixepoch')) as funding_date,
                  source as funder,
                  COUNT(*) as num_transfers,
                  SUM(amount_lamports) / 1e9 as total_sol
                FROM transfer_index
                WHERE destination = ?
                  AND is_valid = 1
                GROUP BY funding_date, funder
                ORDER BY funding_date DESC
            """, (destination,))

            timeline = [
                {
                    'date': row[0],
                    'funder': row[1],
                    'num_transfers': row[2],
                    'total_sol': float(row[3])
                }
                for row in cursor.fetchall()
            ]
            conn.close()

            return timeline

        except Exception as e:
            logger.error(f"[TRANSFER_INDEX] Failed to get funding timeline: {e}")
            return []

    def get_high_value_transfers(self, min_sol: float = 10.0, limit: int = 100) -> List[Dict]:
        """
        Get high-value transfers (whale activity).

        Returns list of transfers >= min_sol.
        """
        try:
            conn = self._get_conn()
            if conn is None:
                return []

            min_lamports = int(min_sol * 1e9)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                  block_time,
                  source,
                  destination,
                  amount_lamports / 1e9 as amount_sol,
                  signature
                FROM transfer_index
                WHERE amount_lamports >= ?
                  AND is_valid = 1
                ORDER BY block_time DESC
                LIMIT ?
            """, (min_lamports, limit))

            transfers = [
                {
                    'block_time': row[0],
                    'source': row[1],
                    'destination': row[2],
                    'amount_sol': float(row[3]),
                    'signature': row[4]
                }
                for row in cursor.fetchall()
            ]
            conn.close()

            return transfers

        except Exception as e:
            logger.error(f"[TRANSFER_INDEX] Failed to get high-value transfers: {e}")
            return []

    # ===== MONITORING & DIAGNOSTICS =====

    def get_stats(self) -> Dict:
        """Get cache statistics for monitoring."""
        try:
            conn = self._get_conn()
            if conn is None:
                return {'error': 'No database connection'}

            cursor = conn.cursor()

            # Total transfers
            cursor.execute("SELECT COUNT(*) FROM transfer_index")
            total_transfers = cursor.fetchone()[0] or 0

            # Valid transfers
            cursor.execute("SELECT COUNT(*) FROM transfer_index WHERE is_valid = 1")
            valid_transfers = cursor.fetchone()[0] or 0

            # Estimated storage
            cursor.execute("""
                SELECT SUM(LENGTH(signature) + LENGTH(source) + LENGTH(destination))
                FROM transfer_index
            """)
            approx_data_bytes = (cursor.fetchone()[0] or 0) + (total_transfers * 40)  # Overhead

            # Latest block time
            cursor.execute("SELECT MAX(block_time) FROM transfer_index")
            latest_block_time = cursor.fetchone()[0]

            # Oldest block time
            cursor.execute("SELECT MIN(block_time) FROM transfer_index")
            oldest_block_time = cursor.fetchone()[0]

            # Ingestion rate
            span_days = 0
            if oldest_block_time and latest_block_time:
                span_days = (latest_block_time - oldest_block_time) / 86400

            avg_per_day = total_transfers / max(span_days, 1)

            conn.close()

            return {
                'total_transfers': total_transfers,
                'valid_transfers': valid_transfers,
                'invalid_transfers': total_transfers - valid_transfers,
                'approx_size_mb': approx_data_bytes / (1024 * 1024),
                'latest_block_time': latest_block_time,
                'oldest_block_time': oldest_block_time,
                'span_days': span_days,
                'avg_transfers_per_day': avg_per_day,
            }

        except Exception as e:
            logger.error(f"[TRANSFER_INDEX] Failed to get stats: {e}")
            return {'error': str(e)}

    def cleanup_invalid_transfers(self) -> int:
        """Remove transfers marked as invalid. Returns count deleted."""
        try:
            conn = self._get_conn()
            if conn is None:
                return 0

            cursor = conn.cursor()

            cursor.execute("DELETE FROM transfer_index WHERE is_valid = 0")

            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            if deleted > 0:
                logger.info(f"[TRANSFER_INDEX] Cleaned up {deleted} invalid transfers")

            return deleted

        except Exception as e:
            logger.warning(f"[TRANSFER_INDEX] cleanup_invalid_transfers() failed: {e}")
            return 0

    def optimize_indexes(self) -> None:
        """Rebuild indexes to optimize query performance."""
        try:
            conn = self._get_conn()
            if conn is None:
                return

            cursor = conn.cursor()

            # Rebuild all indexes
            cursor.execute("REINDEX")

            conn.commit()
            conn.close()

            logger.info("[TRANSFER_INDEX] Indexes optimized")

        except Exception as e:
            logger.warning(f"[TRANSFER_INDEX] optimize_indexes() failed: {e}")


if __name__ == "__main__":
    # Example usage
    indexer = TransferIndexer('flex_complete_database.db')

    # Get stats
    stats = indexer.get_stats()
    print(f"Transfer Index Stats: {stats}")

    # Example query
    funders = indexer.get_funders('example_creator_address')
    print(f"Funders: {funders[:5]}")
