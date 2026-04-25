"""
C2CEdgeBuilder — builds creator_c2c_edges from live creator_outgoing_transfers.

Direct C2C definition:
    creator_outgoing_transfers.creator_address → recipient_address
    where recipient_address is a known creator (appears in creator_funders
    or token_analysis.earliest_tx_creator) and is not the sender itself
    and is not a CEX/infra wallet.

Shared-destination (two creators both sending to the same third wallet) is
explicitly NOT included here — that is a different signal handled elsewhere.
"""

import sqlite3
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS creator_c2c_edges (
    source_creator  TEXT NOT NULL,
    dest_creator    TEXT NOT NULL,
    total_sol       REAL NOT NULL DEFAULT 0,
    transfer_count  INTEGER NOT NULL DEFAULT 0,
    first_seen      INTEGER,
    last_seen       INTEGER,
    confidence      REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (source_creator, dest_creator)
)
"""

_CREATE_IDX_SOURCE = "CREATE INDEX IF NOT EXISTS idx_c2c_source ON creator_c2c_edges(source_creator)"
_CREATE_IDX_DEST   = "CREATE INDEX IF NOT EXISTS idx_c2c_dest   ON creator_c2c_edges(dest_creator)"

# Confidence formula:
#   transfer_count contributes up to 0.6 (saturates at 5 transfers)
#   total_sol contributes up to 0.4 (saturates at 50 SOL)
# A single 1-SOL transfer → ~0.22. Multiple large transfers → approaches 1.0.
def _confidence(transfer_count: int, total_sol: float) -> float:
    count_component = min(transfer_count / 5.0, 1.0) * 0.6
    sol_component   = min(total_sol / 50.0, 1.0) * 0.4
    return round(count_component + sol_component, 4)


class C2CEdgeBuilder:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def build(self) -> dict:
        started_at = time.time()
        conn = self._get_conn()
        try:
            conn.execute(_CREATE_TABLE)
            conn.execute(_CREATE_IDX_SOURCE)
            conn.execute(_CREATE_IDX_DEST)
            conn.commit()

            # Build known-creator set once (union of both sources)
            logger.info("[C2C] Building known-creator set")
            cur = conn.execute("""
                SELECT creator_address FROM creator_funders
                UNION
                SELECT earliest_tx_creator FROM token_analysis
                WHERE earliest_tx_creator IS NOT NULL
            """)
            known_creators = {r[0] for r in cur.fetchall()}
            logger.info(f"[C2C] Known creators: {len(known_creators)}")

            # Load excluded CEX/infra set
            try:
                from src.utils.infra_mapping import build_excluded_set
                excluded = build_excluded_set(conn)
            except Exception as e:
                logger.warning(f"[C2C] Could not load excluded set: {e} — using empty set")
                excluded = set()

            # Derive edges: aggregate by (source, dest) from outgoing transfers
            logger.info("[C2C] Querying creator_outgoing_transfers")
            cur = conn.execute("""
                SELECT creator_address, recipient_address,
                       SUM(amount_sol) as total_sol,
                       COUNT(*) as transfer_count,
                       MIN(block_time) as first_seen,
                       MAX(block_time) as last_seen
                FROM creator_outgoing_transfers
                WHERE is_cex = 0
                  AND recipient_address != creator_address
                GROUP BY creator_address, recipient_address
            """)
            rows = cur.fetchall()

            edges = []
            for source, dest, total_sol, tx_count, first_seen, last_seen in rows:
                if dest not in known_creators:
                    continue
                if source in excluded or dest in excluded:
                    continue
                conf = _confidence(tx_count, total_sol or 0)
                edges.append((source, dest, round(total_sol or 0, 6),
                               tx_count, first_seen, last_seen, conf))

            logger.info(f"[C2C] {len(edges)} direct C2C edges qualified")

            # Rebuild in a single transaction
            conn.execute("DELETE FROM creator_c2c_edges")
            conn.executemany("""
                INSERT INTO creator_c2c_edges
                    (source_creator, dest_creator, total_sol, transfer_count,
                     first_seen, last_seen, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, edges)
            conn.commit()

            logger.info(f"[C2C] Wrote {len(edges)} edges to creator_c2c_edges")
            return {
                'status': 'success',
                'edges_written': len(edges),
                'known_creators': len(known_creators),
                'duration_seconds': round(time.time() - started_at, 2),
            }

        except Exception as e:
            logger.error(f"[C2C] Build failed: {e}", exc_info=True)
            return {
                'status': 'failed',
                'edges_written': 0,
                'error': str(e),
                'duration_seconds': round(time.time() - started_at, 2),
            }
        finally:
            conn.close()
