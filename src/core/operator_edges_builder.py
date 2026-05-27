import sqlite3
import time


class OperatorEdgesBuilder:
    """Build creator-to-creator operator edges from strong downstream evidence."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def build(self) -> dict:
        t0 = time.time()
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS operator_creator_edges (
                    creator_a TEXT NOT NULL,
                    creator_b TEXT NOT NULL,
                    operator_anchor TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (creator_a, creator_b, operator_anchor, edge_type)
                )
            """)
            conn.execute("DELETE FROM operator_creator_edges WHERE edge_type='shared_payout_wallet'")
            rows = conn.execute("""
                SELECT a.creator_address, b.creator_address, a.recipient_address,
                       MIN(1.0, 0.5 + (COUNT(*) * 0.1)) AS confidence
                FROM creator_outbound_classifications a
                JOIN creator_outbound_classifications b
                  ON a.recipient_address=b.recipient_address
                 AND a.creator_address < b.creator_address
                WHERE a.relationship_type='shared_payout_wallet'
                  AND b.relationship_type='shared_payout_wallet'
                  AND a.recipient_address NOT IN (SELECT address FROM infra_wallets)
                GROUP BY a.creator_address,b.creator_address,a.recipient_address
            """).fetchall()
            now = int(time.time())
            conn.executemany("""
                INSERT OR IGNORE INTO operator_creator_edges
                    (creator_a,creator_b,operator_anchor,edge_type,confidence,created_at)
                VALUES (?,?,?,?,?,?)
            """, [(a,b,anchor,'shared_payout_wallet',conf,now) for a,b,anchor,conf in rows])
            conn.commit()
            return {
                "status": "success",
                "edges_written": len(rows),
                "duration_seconds": round(time.time()-t0, 2),
            }
        finally:
            conn.close()
