#!/usr/bin/env python3
"""
NetworkMembershipBuilder — derives network_membership from creator_funders.

Groups non-CEX, non-infra funders that funded >= min_creators creators,
assigns stable Network_N names (preserving existing names where possible),
and rebuilds network_membership atomically.

Also maintains funder_network_map for name stability across rebuilds.
"""

import sqlite3
from src.utils.db_locking import db_connect
import logging
import time
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class NetworkMembershipBuilder:
    def __init__(self, db_path: str, min_creators: int = 2, min_payout_creators: int = 3):
        self.db_path = db_path
        self.min_creators = min_creators
        self.min_payout_creators = min_payout_creators

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> dict:
        """
        Rebuild network_membership from creator_funders.

        Read phase uses a read-only connection so the listener can write concurrently.
        Write phase opens a separate connection and holds the lock only for the
        fast DELETE + INSERT swap — typically < 1 second.

        Returns:
            {
                'status': 'success'|'failed',
                'networks_built': int,
                'memberships_written': int,
                'duration_seconds': float,
            }
        """
        t0 = time.time()
        try:
            from src.utils.infra_mapping import build_excluded_set
            from src.core.database_write_service import PRIORITY_P2_BACKGROUND

            # ── READ PHASE (no write lock held) ──────────────────────────────
            rconn = db_connect(self.db_path, timeout=60)
            rconn.execute("PRAGMA journal_mode=WAL")
            rconn.execute("PRAGMA query_only=ON")
            rconn.row_factory = sqlite3.Row

            excluded = build_excluded_set(rconn)
            logger.info(f"[NetworkMembershipBuilder] Excluded set size: {len(excluded)}")

            rows = rconn.execute("""
                SELECT funder_address,
                       COUNT(DISTINCT creator_address) AS creator_count,
                       SUM(amount_sol)                 AS total_sol
                FROM creator_funders
                WHERE is_cex = 0
                GROUP BY funder_address
                HAVING creator_count >= ?
                ORDER BY creator_count DESC, total_sol DESC
            """, (self.min_payout_creators,)).fetchall()

            qualifying = [
                dict(r) for r in rows
                if r["funder_address"] not in excluded
            ]
            logger.info(
                f"[NetworkMembershipBuilder] Qualifying funders (after exclusion): {len(qualifying)}"
            )

            payout_rows = rconn.execute("""
                SELECT recipient_address AS funder_address,
                       COUNT(DISTINCT creator_address) AS creator_count,
                       SUM(amount_sol) AS total_sol
                FROM creator_outbound_classifications
                WHERE relationship_type='shared_payout_wallet'
                  AND recipient_address NOT IN (SELECT address FROM infra_wallets)
                GROUP BY recipient_address
                HAVING creator_count >= ?
                ORDER BY creator_count DESC, total_sol DESC
            """, (self.min_creators,)).fetchall()
            qualifying_payouts = [dict(r) for r in payout_rows if r["funder_address"] not in excluded]
            logger.info(
                f"[NetworkMembershipBuilder] Qualifying payout anchors (after exclusion): {len(qualifying_payouts)}"
            )

            anchors = qualifying + qualifying_payouts
            funder_to_name = self._assign_network_names(rconn, anchors)
            pairs = self._collect_memberships(rconn, qualifying, funder_to_name)
            pairs += self._collect_payout_memberships(rconn, qualifying_payouts, funder_to_name)
            funder_map_rows = self._collect_funder_map(anchors, funder_to_name)
            rconn.close()

            # ── WRITE PHASE (write lock held only for fast swap) ─────────────
            wconn = db_connect(self.db_path, timeout=60, priority=PRIORITY_P2_BACKGROUND)
            wconn.execute("PRAGMA journal_mode=WAL")
            wconn.execute("PRAGMA synchronous=NORMAL")
            wconn.execute("PRAGMA busy_timeout=30000")
            wconn.row_factory = sqlite3.Row

            self._ensure_funder_network_map(wconn)
            self._ensure_network_membership_schema(wconn)
            # X78.20 -- dropped inline sync_infra_wallets(wconn): the read
            # phase above already reads infra_wallets via build_excluded_set,
            # and infra_sync_scheduler (X78.14) keeps it fresh on its own
            # cadence -- re-syncing here only extended this write-phase hold.

            wconn.execute("DELETE FROM network_membership")
            wconn.executemany(
                "INSERT OR IGNORE INTO network_membership (network_name, creator_address, funder_address) VALUES (?, ?, ?)",
                pairs,
            )
            wconn.execute(
                "DELETE FROM funder_network_map WHERE funder_address IN (SELECT address FROM infra_wallets)"
            )
            wconn.executemany("""
                INSERT INTO funder_network_map (funder_address, network_name, creator_count, last_built_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(funder_address) DO UPDATE SET
                    network_name  = excluded.network_name,
                    creator_count = excluded.creator_count,
                    last_built_at = excluded.last_built_at
            """, funder_map_rows)
            wconn.commit()
            wconn.close()

            duration = time.time() - t0
            logger.info(
                f"[NetworkMembershipBuilder] Done — networks={len(funder_to_name)} "
                f"memberships={len(pairs)} duration={duration:.1f}s"
            )
            return {
                "status": "success",
                "networks_built": len(funder_to_name),
                "memberships_written": len(pairs),
                "duration_seconds": round(duration, 2),
            }

        except Exception as exc:
            duration = time.time() - t0
            logger.error(
                f"[NetworkMembershipBuilder] FAILED after {duration:.1f}s: {exc}",
                exc_info=True,
            )
            return {
                "status": "failed",
                "networks_built": 0,
                "memberships_written": 0,
                "duration_seconds": round(duration, 2),
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_funder_network_map(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS funder_network_map (
                funder_address TEXT PRIMARY KEY,
                network_name   TEXT NOT NULL,
                creator_count  INTEGER DEFAULT 0,
                last_built_at  TEXT
            )
        """)

    def _ensure_network_membership_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS network_membership (
                network_name TEXT,
                creator_address TEXT,
                funder_address TEXT,
                PRIMARY KEY (network_name, creator_address)
            )
        """)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(network_membership)").fetchall()}
        if "funder_address" not in cols:
            conn.execute("ALTER TABLE network_membership ADD COLUMN funder_address TEXT")

    def _assign_network_names(
        self, conn: sqlite3.Connection, qualifying: list[dict]
    ) -> dict[str, str]:
        """
        Return funder_address -> network_name mapping with stable names.

        Priority:
          1. Existing mapping in funder_network_map (persisted from a prior run).
          2. Back-derive from current network_membership + networks_release
             (for the very first run against manually-seeded data).
          3. Assign new Network_N for any funder not yet mapped.
        """
        funder_to_name: dict[str, str] = {}

        # --- Load existing funder_network_map (fastest, most reliable)
        for row in conn.execute(
            "SELECT funder_address, network_name FROM funder_network_map"
        ).fetchall():
            funder_to_name[row[0]] = row[1]

        # --- Back-derive from existing network_membership (first-run bootstrap)
        # For each network_name in networks_release, find the single funder that
        # funds the most members of that network.  We join network_membership →
        # creator_funders to find the dominant non-CEX funder per network.
        missing_funders = {
            r["funder_address"]
            for r in qualifying
            if r["funder_address"] not in funder_to_name
        }
        if missing_funders:
            logger.info(
                f"[NetworkMembershipBuilder] Back-deriving names for "
                f"{len(missing_funders)} unmapped funders from network_membership"
            )
            bootstrap = self._bootstrap_from_existing(conn)
            for funder, name in bootstrap.items():
                if funder in missing_funders and funder not in funder_to_name:
                    funder_to_name[funder] = name
                    missing_funders.discard(funder)

        # --- Assign new Network_N for remaining unmapped funders
        if missing_funders:
            next_n = self._next_network_number(conn, funder_to_name)
            for r in qualifying:
                fa = r["funder_address"]
                if fa in missing_funders:
                    funder_to_name[fa] = f"Network_{next_n}"
                    next_n += 1

        return funder_to_name

    def _bootstrap_from_existing(self, conn: sqlite3.Connection) -> dict[str, str]:
        """
        For each existing network in networks_release, find the dominant funder
        (most creator_funders rows among members of that network).
        Returns funder_address -> network_name mapping.
        """
        result: dict[str, str] = {}
        try:
            rows = conn.execute("""
                SELECT nm.network_name,
                       cf.funder_address,
                       COUNT(*) AS cnt
                FROM network_membership nm
                JOIN creator_funders cf
                  ON cf.creator_address = nm.creator_address
                WHERE cf.is_cex = 0
                  AND cf.funder_address NOT IN (SELECT address FROM infra_wallets)
                GROUP BY nm.network_name, cf.funder_address
                ORDER BY nm.network_name, cnt DESC
            """).fetchall()

            seen_networks: set[str] = set()
            seen_funders: set[str] = set()
            for row in rows:
                net = row[0]
                funder = row[1]
                if net not in seen_networks and funder not in seen_funders:
                    result[funder] = net
                    seen_networks.add(net)
                    seen_funders.add(funder)
        except Exception as exc:
            logger.warning(f"[NetworkMembershipBuilder] bootstrap failed: {exc}")
        return result

    def _next_network_number(
        self,
        conn: sqlite3.Connection,
        current_map: dict[str, str],
    ) -> int:
        """
        Return the next integer N such that Network_N is not already used
        in networks_release OR in the current_map being built.
        """
        used: set[int] = set()

        # From networks_release table
        try:
            for row in conn.execute("SELECT network_name FROM networks_release").fetchall():
                m = re.match(r"Network_(\d+)$", row[0])
                if m:
                    used.add(int(m.group(1)))
        except Exception:
            pass

        # From current mapping being built
        for name in current_map.values():
            m = re.match(r"Network_(\d+)$", name)
            if m:
                used.add(int(m.group(1)))

        n = 1
        while n in used:
            n += 1
        return n

    def _collect_memberships(
        self,
        conn: sqlite3.Connection,
        qualifying: list[dict],
        funder_to_name: dict[str, str],
    ) -> list[tuple[str, str, str]]:
        """Return (network_name, creator_address, funder_address) tuples — read only."""
        # Fetch all creator_funders rows for qualifying funders in one query
        if not qualifying:
            return []
        placeholders = ",".join("?" * len(qualifying))
        funder_addrs = [r["funder_address"] for r in qualifying]
        rows = conn.execute(f"""
            SELECT DISTINCT funder_address, creator_address
            FROM creator_funders
            WHERE funder_address IN ({placeholders}) AND is_cex = 0
        """, funder_addrs).fetchall()

        pairs: list[tuple[str, str, str]] = []
        for row in rows:
            fa, creator = row[0], row[1]
            network_name = funder_to_name.get(fa)
            if network_name:
                pairs.append((network_name, creator, fa))

        logger.info(f"[NetworkMembershipBuilder] Collected {len(pairs)} membership rows")
        return pairs

    def _collect_funder_map(
        self,
        qualifying: list[dict],
        funder_to_name: dict[str, str],
    ) -> list[tuple]:
        """Return rows for funder_network_map upsert — pure Python, no DB."""
        now = datetime.now(timezone.utc).isoformat()
        count_by_funder = {r["funder_address"]: r["creator_count"] for r in qualifying}
        return [
            (fa, name, count_by_funder.get(fa, 0), now)
            for fa, name in funder_to_name.items()
        ]

    def _collect_payout_memberships(
        self,
        conn: sqlite3.Connection,
        qualifying: list[dict],
        funder_to_name: dict[str, str],
    ) -> list[tuple[str, str, str]]:
        if not qualifying:
            return []
        placeholders = ",".join("?" * len(qualifying))
        payout_addrs = [r["funder_address"] for r in qualifying]
        rows = conn.execute(f"""
            SELECT DISTINCT recipient_address, creator_address
            FROM creator_outbound_classifications
            WHERE relationship_type='shared_payout_wallet'
              AND recipient_address IN ({placeholders})
        """, payout_addrs).fetchall()
        pairs = []
        for payout, creator in rows:
            name = funder_to_name.get(payout)
            if name:
                pairs.append((name, creator, payout))
        logger.info(f"[NetworkMembershipBuilder] Collected {len(pairs)} payout membership rows")
        return pairs


# ---------------------------------------------------------------------------
# Module-level helper — safe to import anywhere without instantiating the class
# ---------------------------------------------------------------------------

def assign_live_network_for_creator(db_path: str, creator_address: str) -> dict:
    """
    Best-effort provisional network assignment immediately after funding extraction.

    Returns:
        {
            'assigned':       bool,
            'network_name':   str | None,
            'funder_address': str | None,
            'provisional':    bool,          # True when name is Provisional_XXXXXXXX
            'creators_in_network': int,
        }

    Constraints:
        - Uses INSERT OR IGNORE so the canonical 30-min builder always wins on conflict.
        - Never deletes existing rows — read-only if no qualifying funder is found.
        - Skips CEX / infra addresses via build_excluded_set().
    """
    null_result = {
        "assigned": False,
        "network_name": None,
        "funder_address": None,
        "provisional": False,
        "creators_in_network": 0,
    }

    # X78.19 -- read phase on a plain read-only connection so this call never
    # holds the single cross-process write lane during exclusion-set lookup,
    # infra sync, or the funder/network SELECTs (previously the dominant hold
    # time for a call site that only needs the lane for one small INSERT OR
    # IGNORE at the end). The scheduled infra_sync_scheduler already keeps
    # infra_wallets fresh (X78.14) so this path reads it rather than
    # re-syncing inline on every creator.
    try:
        read_conn = db_connect(db_path, timeout=15, row_factory=sqlite3.Row, read_only=True)

        # --- 1. Find non-CEX funders of this creator who also fund >= 1 other creator
        #        (total distinct creators >= 2, counting the new one)
        rows = read_conn.execute("""
            SELECT cf.funder_address,
                   COUNT(DISTINCT cf.creator_address) AS creator_count
            FROM creator_funders cf
            WHERE cf.funder_address IN (
                SELECT funder_address
                FROM creator_funders
                WHERE creator_address = ? AND is_cex = 0
            )
              AND cf.is_cex = 0
              AND cf.funder_address NOT IN (SELECT address FROM infra_wallets)
            GROUP BY cf.funder_address
            HAVING creator_count >= 2
            ORDER BY creator_count DESC
        """, (creator_address,)).fetchall()

        # --- 2. Apply the exact exclusion contract to this tiny candidate
        # set. The old call built the global set first, including three
        # SELECT DISTINCT scans across token_analysis (~118s observed). This
        # candidate-bounded lookup preserves the same exclusions without a
        # global scan in the per-creator hot path.
        try:
            from src.utils.infra_mapping import build_excluded_set
            excluded = build_excluded_set(
                read_conn,
                candidate_addresses=[r["funder_address"] for r in rows],
                # The supervised infra_sync_scheduler materializes these
                # protocol accounts into infra_wallets. Re-scanning the source
                # token table here duplicates its ownership and cost.
                include_token_analysis=False,
            )
        except Exception:
            excluded = set()

        # Filter excluded addresses
        qualifying = [r for r in rows if r["funder_address"] not in excluded]

        if not qualifying:
            read_conn.close()
            return null_result

        # --- 3. Pick best funder (highest creator_count)
        best = qualifying[0]
        funder_addr = best["funder_address"]
        creator_count = best["creator_count"]

        # --- 4. Resolve network name
        #   Priority 1: existing funder_network_map entry
        fnm_row = read_conn.execute(
            "SELECT network_name FROM funder_network_map WHERE funder_address = ?",
            (funder_addr,),
        ).fetchone()

        network_name = None
        provisional = False

        if fnm_row:
            network_name = fnm_row["network_name"]
        else:
            # Priority 2: inherit from a creator already in network_membership
            #   that shares this funder
            nm_row = read_conn.execute("""
                SELECT nm.network_name
                FROM network_membership nm
                JOIN creator_funders cf
                  ON cf.creator_address = nm.creator_address
                WHERE cf.funder_address = ?
                  AND cf.is_cex = 0
                  AND cf.funder_address NOT IN (SELECT address FROM infra_wallets)
                LIMIT 1
            """, (funder_addr,)).fetchone()

            if nm_row:
                network_name = nm_row["network_name"]
            else:
                # Priority 3: provisional name
                network_name = f"Provisional_{funder_addr[:8]}"
                provisional = True

        read_conn.close()

        # --- 5. Acquire the write lane ONLY for the final small INSERT OR IGNORE
        # (canonical builder wins on conflict) -- this is the sole point that
        # needs write ownership, held for a single-statement transaction.
        # X78.20 -- P1/operational, not P2: this runs inline per completed
        # creator-funding job (creator_funding_worker), not on the periodic
        # background rebuild cadence, so it stays at the default priority
        # rather than deferring to P0 as aggressively as the heavy builders.
        write_conn = db_connect(db_path, timeout=15)
        write_conn.execute("PRAGMA busy_timeout=10000")
        write_conn.execute(
            """
            INSERT OR IGNORE INTO network_membership
                (network_name, creator_address, funder_address)
            VALUES (?, ?, ?)
            """,
            (network_name, creator_address, funder_addr),
        )
        write_conn.commit()
        write_conn.close()

        return {
            "assigned": True,
            "network_name": network_name,
            "funder_address": funder_addr,
            "provisional": provisional,
            "creators_in_network": creator_count,
        }

    except Exception as exc:
        logger.warning(f"[assign_live_network] failed for {creator_address}: {exc}")
        try:
            read_conn.close()
        except Exception:
            pass
        try:
            write_conn.close()
        except Exception:
            pass
        return null_result
