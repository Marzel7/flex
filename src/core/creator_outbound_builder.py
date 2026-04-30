"""
CreatorOutboundBuilder — pure-DB classification of creator outgoing transfers.

Reads creator_outgoing_transfers and classifies each significant transfer into
creator_outbound_classifications with one of:

    return_to_funder         creator sends SOL back to one of their own funders
    shared_payout_wallet     recipient receives from ≥2 distinct creators
    creator_to_upstream_hub  recipient is a monitored upstream hub
    large_outbound           transfer > LARGE_OUTBOUND_SOL_THRESHOLD SOL

No RPC calls. Runs every analyzer cycle as a fast DB-only pass.
"""

import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LARGE_OUTBOUND_SOL_THRESHOLD = float(10.0)
SHARED_PAYOUT_MIN_CREATORS   = int(2)
MIN_CLASSIFY_SOL             = float(0.1)


def apply_migration(db_path: str) -> None:
    migration = (
        Path(__file__).resolve().parent.parent.parent
        / "database" / "migrations" / "add_creator_outbound.sql"
    )
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    for stmt in migration.read_text().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "already exists" not in str(e).lower():
                    logger.warning(f"[COB] Migration: {e}")
    conn.commit()
    conn.close()


class CreatorOutboundBuilder:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def run(self) -> dict:
        t0 = time.time()
        apply_migration(self.db_path)

        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            before = self._snapshot_classifications(conn)
            rows_written = self._classify_all(conn)
            conn.commit()
            self._emit_new_events(conn, before)
            conn.commit()
        finally:
            conn.close()

        duration = round(time.time() - t0, 2)
        logger.info(f"[COB] Done — classifications={rows_written} duration={duration}s")
        return {"status": "success", "classifications_written": rows_written, "duration_seconds": duration}

    # ── Snapshot + event emission ─────────────────────────────────────────────

    _EVENT_TYPES = {
        "return_to_funder":         "creator_returned_funds",
        "shared_payout_wallet":     "shared_payout_wallet_detected",
        "creator_to_upstream_hub":  "creator_linked_to_upstream_hub",
    }

    def _snapshot_classifications(self, conn: sqlite3.Connection) -> set:
        rows = conn.execute(
            "SELECT creator_address, recipient_address, relationship_type FROM creator_outbound_classifications"
        ).fetchall()
        return {(r[0], r[1], r[2]) for r in rows}

    def _emit_new_events(self, conn: sqlite3.Connection, before: set) -> None:
        after = self._snapshot_classifications(conn)
        new_rows = after - before
        if not new_rows:
            return
        for creator, recipient, rel_type in new_rows:
            event_type = self._EVENT_TYPES.get(rel_type)
            if not event_type:
                continue
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO intelligence_relationship_events
                        (event_type, source_type, source_address, target_type, target_address,
                         relationship_type, scan_source)
                    VALUES (?, 'creator', ?, 'address', ?, ?, 'outbound_builder')
                """, (event_type, creator, recipient, rel_type))
            except Exception:
                pass
        emitted = sum(1 for _, _, rt in new_rows if rt in self._EVENT_TYPES)
        if emitted:
            logger.info(f"[COB] Emitted {emitted} new relationship events")

    # ── Classification passes ──────────────────────────────────────────────────

    def _classify_all(self, conn: sqlite3.Connection) -> int:
        total = 0
        total += self._classify_return_to_funder(conn)
        total += self._classify_shared_payout_wallets(conn)
        total += self._classify_creator_to_hub(conn)
        total += self._classify_large_outbound(conn)
        return total

    def _upsert(self, conn: sqlite3.Connection, creator: str, recipient: str,
                rel_type: str, amount_sol: float, tx_count: int,
                first_seen: Optional[int], last_seen: Optional[int]) -> int:
        now = int(time.time())
        conn.execute("""
            INSERT INTO creator_outbound_classifications
                (creator_address, recipient_address, relationship_type,
                 amount_sol, tx_count, first_seen, last_seen, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(creator_address, recipient_address, relationship_type) DO UPDATE SET
                amount_sol  = excluded.amount_sol,
                tx_count    = excluded.tx_count,
                last_seen   = excluded.last_seen,
                updated_at  = excluded.updated_at
        """, (creator, recipient, rel_type, amount_sol, tx_count,
              first_seen, last_seen, now, now))
        return conn.execute("SELECT changes()").fetchone()[0]

    def _classify_return_to_funder(self, conn: sqlite3.Connection) -> int:
        """Creator sends SOL to a wallet that funded them."""
        rows = conn.execute("""
            SELECT
                cot.creator_address,
                cot.recipient_address,
                SUM(cot.amount_sol)     AS total_sol,
                COUNT(*)                AS tx_count,
                MIN(cot.block_time)     AS first_seen,
                MAX(cot.block_time)     AS last_seen
            FROM creator_outgoing_transfers cot
            JOIN creator_funders cf
                ON cf.creator_address = cot.creator_address
               AND cf.funder_address  = cot.recipient_address
            WHERE cot.is_cex = 0
              AND cot.amount_sol >= ?
            GROUP BY cot.creator_address, cot.recipient_address
        """, (MIN_CLASSIFY_SOL,)).fetchall()

        written = 0
        for r in rows:
            written += self._upsert(
                conn, r["creator_address"], r["recipient_address"],
                "return_to_funder",
                round(r["total_sol"], 6), r["tx_count"], r["first_seen"], r["last_seen"],
            )
        logger.info(f"[COB] return_to_funder: {written} rows")
        return written

    def _classify_shared_payout_wallets(self, conn: sqlite3.Connection) -> int:
        """Recipient receives SOL from ≥2 distinct creators."""
        # Identify shared recipients first
        shared_recipients = {
            row[0] for row in conn.execute("""
                SELECT recipient_address
                FROM creator_outgoing_transfers
                WHERE is_cex = 0 AND amount_sol >= ?
                GROUP BY recipient_address
                HAVING COUNT(DISTINCT creator_address) >= ?
            """, (MIN_CLASSIFY_SOL, SHARED_PAYOUT_MIN_CREATORS)).fetchall()
        }

        if not shared_recipients:
            return 0

        placeholders = ",".join("?" * len(shared_recipients))
        rows = conn.execute(f"""
            SELECT
                cot.creator_address,
                cot.recipient_address,
                SUM(cot.amount_sol)  AS total_sol,
                COUNT(*)             AS tx_count,
                MIN(cot.block_time)  AS first_seen,
                MAX(cot.block_time)  AS last_seen
            FROM creator_outgoing_transfers cot
            WHERE cot.is_cex = 0
              AND cot.amount_sol >= ?
              AND cot.recipient_address IN ({placeholders})
            GROUP BY cot.creator_address, cot.recipient_address
        """, (MIN_CLASSIFY_SOL, *shared_recipients)).fetchall()

        written = 0
        for r in rows:
            written += self._upsert(
                conn, r["creator_address"], r["recipient_address"],
                "shared_payout_wallet",
                round(r["total_sol"], 6), r["tx_count"], r["first_seen"], r["last_seen"],
            )
        logger.info(f"[COB] shared_payout_wallet: {written} rows")
        return written

    def _classify_creator_to_hub(self, conn: sqlite3.Connection) -> int:
        """Creator sends SOL directly to a monitored upstream hub."""
        rows = conn.execute("""
            SELECT
                cot.creator_address,
                cot.recipient_address,
                SUM(cot.amount_sol)  AS total_sol,
                COUNT(*)             AS tx_count,
                MIN(cot.block_time)  AS first_seen,
                MAX(cot.block_time)  AS last_seen
            FROM creator_outgoing_transfers cot
            JOIN monitored_upstream_hubs muh
                ON muh.upstream_address = cot.recipient_address
            WHERE cot.is_cex = 0
              AND cot.amount_sol >= ?
              AND muh.status = 'active'
            GROUP BY cot.creator_address, cot.recipient_address
        """, (MIN_CLASSIFY_SOL,)).fetchall()

        written = 0
        for r in rows:
            written += self._upsert(
                conn, r["creator_address"], r["recipient_address"],
                "creator_to_upstream_hub",
                round(r["total_sol"], 6), r["tx_count"], r["first_seen"], r["last_seen"],
            )
        logger.info(f"[COB] creator_to_upstream_hub: {written} rows")
        return written

    def _classify_large_outbound(self, conn: sqlite3.Connection) -> int:
        """Single transfers above the large-outbound threshold."""
        rows = conn.execute("""
            SELECT
                creator_address,
                recipient_address,
                SUM(amount_sol)   AS total_sol,
                COUNT(*)          AS tx_count,
                MIN(block_time)   AS first_seen,
                MAX(block_time)   AS last_seen
            FROM creator_outgoing_transfers
            WHERE is_cex = 0
              AND amount_sol >= ?
            GROUP BY creator_address, recipient_address
        """, (LARGE_OUTBOUND_SOL_THRESHOLD,)).fetchall()

        written = 0
        for r in rows:
            written += self._upsert(
                conn, r["creator_address"], r["recipient_address"],
                "large_outbound",
                round(r["total_sol"], 6), r["tx_count"], r["first_seen"], r["last_seen"],
            )
        logger.info(f"[COB] large_outbound: {written} rows")
        return written


def get_creator_outbound_summary(db_path: str, creator_address: str) -> dict:
    """Return outbound classification summary for one creator. Used by UI/API."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT relationship_type, COUNT(*) AS recipients, SUM(amount_sol) AS total_sol
            FROM creator_outbound_classifications
            WHERE creator_address = ?
            GROUP BY relationship_type
        """, (creator_address,)).fetchall()
        return {r["relationship_type"]: {"recipients": r["recipients"], "total_sol": round(r["total_sol"] or 0, 4)}
                for r in rows}
    finally:
        conn.close()
