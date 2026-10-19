"""Build human-readable display labels for canonical network IDs."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DisplayNameCandidate:
    display_name: str
    reason: str
    source_address: str | None = None


class NetworkDisplayNameBuilder:
    """Assign UI-facing names while preserving stable network_name IDs."""

    HUB_REASON = "2H upstream hub bridges multiple networks"
    FUNDER_REASON = "dominant direct funder"

    def build(self, conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        self._ensure_schema(conn)

        networks = [
            row["network_name"] if isinstance(row, sqlite3.Row) else row[0]
            for row in conn.execute(
                "SELECT network_name FROM networks_release ORDER BY network_name"
            ).fetchall()
        ]

        candidates: dict[str, DisplayNameCandidate] = {}
        self._apply_hub_candidates(conn, networks, candidates)
        self._apply_funder_candidates(conn, networks, candidates)
        self._apply_wallet_cluster_candidates(conn, networks, candidates)
        self._apply_farm_cluster_candidates(conn, networks, candidates)

        result: dict[str, dict[str, Any]] = {}
        seen: Counter[str] = Counter()

        for network_name in networks:
            candidate = candidates.get(network_name) or DisplayNameCandidate(
                self._fallback_name(network_name),
                "canonical network id fallback",
                None,
            )

            base_name = candidate.display_name
            seen[base_name] += 1
            display_name = base_name if seen[base_name] == 1 else f"{base_name}-{seen[base_name]}"

            conn.execute(
                """
                UPDATE networks_release
                SET display_name = ?,
                    display_name_reason = ?,
                    display_name_source = ?
                WHERE network_name = ?
                """,
                (display_name, candidate.reason, candidate.source_address, network_name),
            )
            conn.execute(
                """
                INSERT INTO network_display_names
                    (network_name, display_name, reason, source_address, updated_at)
                VALUES (?, ?, ?, ?, strftime('%s','now'))
                ON CONFLICT(network_name) DO UPDATE SET
                    display_name = excluded.display_name,
                    reason = excluded.reason,
                    source_address = excluded.source_address,
                    updated_at = excluded.updated_at
                """,
                (network_name, display_name, candidate.reason, candidate.source_address),
            )
            result[network_name] = {
                "network_name": network_name,
                "display_name": display_name,
                "reason": candidate.reason,
                "source_address": candidate.source_address,
            }

        return result

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        self._ensure_column(conn, "networks_release", "display_name", "TEXT")
        self._ensure_column(conn, "networks_release", "display_name_reason", "TEXT")
        self._ensure_column(conn, "networks_release", "display_name_source", "TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS network_display_names (
                network_name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                reason TEXT,
                source_address TEXT,
                updated_at INTEGER DEFAULT (strftime('%s','now'))
            )
            """
        )

    def _apply_hub_candidates(
        self,
        conn: sqlite3.Connection,
        networks: list[str],
        candidates: dict[str, DisplayNameCandidate],
    ) -> None:
        if not self._table_exists(conn, "upstream_network_bridge"):
            return

        rows = conn.execute(
            """
            SELECT upstream_address, network_a, network_b, confidence_score
            FROM upstream_network_bridge
            WHERE confidence_score >= 55
              AND COALESCE(is_excluded, 0) = 0
            """
        ).fetchall()

        bridged_counts: defaultdict[str, set[str]] = defaultdict(set)
        for row in rows:
            bridged_counts[row["upstream_address"]].add(row["network_a"])
            bridged_counts[row["upstream_address"]].add(row["network_b"])

        choices: defaultdict[str, list[tuple[float, int, str]]] = defaultdict(list)
        network_set = set(networks)
        for row in rows:
            bridged_count = len(bridged_counts[row["upstream_address"]])
            for network_name in (row["network_a"], row["network_b"]):
                if network_name in network_set:
                    choices[network_name].append(
                        (float(row["confidence_score"] or 0), bridged_count, row["upstream_address"])
                    )

        for network_name, network_choices in choices.items():
            _, _, upstream = sorted(network_choices, key=lambda c: (-c[0], -c[1], c[2]))[0]
            candidates[network_name] = DisplayNameCandidate(
                f"HubCluster-{upstream[:8]}",
                self.HUB_REASON,
                upstream,
            )

    def _apply_funder_candidates(
        self,
        conn: sqlite3.Connection,
        networks: list[str],
        candidates: dict[str, DisplayNameCandidate],
    ) -> None:
        if not self._table_exists(conn, "funder_network_map"):
            return

        ranked = self._rank_network_funders(conn)
        for network_name in networks:
            if network_name in candidates or network_name not in ranked:
                continue
            creator_count, total_sol, funder = ranked[network_name][0]
            if creator_count <= 0 and total_sol <= 0:
                continue
            candidates[network_name] = DisplayNameCandidate(
                f"FunderCluster-{funder[:8]}",
                self.FUNDER_REASON,
                funder,
            )

    def _apply_wallet_cluster_candidates(
        self,
        conn: sqlite3.Connection,
        networks: list[str],
        candidates: dict[str, DisplayNameCandidate],
    ) -> None:
        if not (self._table_exists(conn, "funder_network_map") and self._table_exists(conn, "wallet_clusters")):
            return

        rows = conn.execute(
            """
            SELECT fnm.network_name, wc.cluster_id, wc.confidence_score, COALESCE(wc.creator_count, 0) AS creator_count
            FROM funder_network_map fnm
            JOIN wallet_clusters wc ON wc.funder_wallet = fnm.funder_address
            ORDER BY fnm.network_name, wc.confidence_score DESC, wc.creator_count DESC, wc.cluster_id ASC
            """
        ).fetchall()

        by_network: dict[str, sqlite3.Row] = {}
        for row in rows:
            by_network.setdefault(row["network_name"], row)

        for network_name in networks:
            if network_name in candidates or network_name not in by_network:
                continue
            row = by_network[network_name]
            candidates[network_name] = DisplayNameCandidate(
                f"WalletCluster-#{row['cluster_id']}",
                "associated wallet cluster",
                str(row["cluster_id"]),
            )

    def _apply_farm_cluster_candidates(
        self,
        conn: sqlite3.Connection,
        networks: list[str],
        candidates: dict[str, DisplayNameCandidate],
    ) -> None:
        if not self._table_exists(conn, "farm_cluster_members"):
            return

        rows: list[sqlite3.Row] = []
        if self._table_exists(conn, "network_membership"):
            rows.extend(
                conn.execute(
                    """
                    SELECT nm.network_name, fcm.cluster_id, COUNT(*) AS member_count
                    FROM network_membership nm
                    JOIN farm_cluster_members fcm ON fcm.wallet_address = nm.creator_address
                    GROUP BY nm.network_name, fcm.cluster_id
                    """
                ).fetchall()
            )

        if self._table_exists(conn, "funder_network_map"):
            rows.extend(
                conn.execute(
                    """
                    SELECT fnm.network_name, fcm.cluster_id, COUNT(*) AS member_count
                    FROM funder_network_map fnm
                    JOIN farm_cluster_members fcm ON fcm.wallet_address = fnm.funder_address
                    GROUP BY fnm.network_name, fcm.cluster_id
                    """
                ).fetchall()
            )

        best: dict[str, tuple[int, int]] = {}
        for row in rows:
            cluster_id = int(row["cluster_id"])
            member_count = int(row["member_count"] or 0)
            prev = best.get(row["network_name"])
            if prev is None or (member_count, -cluster_id) > (prev[1], -prev[0]):
                best[row["network_name"]] = (cluster_id, member_count)

        for network_name in networks:
            if network_name in candidates or network_name not in best:
                continue
            cluster_id, _ = best[network_name]
            candidates[network_name] = DisplayNameCandidate(
                f"FarmCluster-#{cluster_id}",
                "associated farm cluster",
                str(cluster_id),
            )

    def _rank_network_funders(self, conn: sqlite3.Connection) -> dict[str, list[tuple[int, float, str]]]:
        if not self._table_exists(conn, "network_membership"):
            rows = conn.execute(
                """
                SELECT network_name, funder_address, COALESCE(creator_count, 0) AS creator_count, 0 AS total_sol
                FROM funder_network_map
                """
            ).fetchall()
            ranked: defaultdict[str, list[tuple[int, float, str]]] = defaultdict(list)
            for row in rows:
                ranked[row["network_name"]].append(
                    (int(row["creator_count"] or 0), 0.0, row["funder_address"])
                )
            for network_name in list(ranked.keys()):
                ranked[network_name].sort(key=lambda item: (-item[0], item[2]))
            return dict(ranked)

        if not self._table_exists(conn, "creator_funders"):
            rows = conn.execute(
                """
                SELECT network_name, funder_address, COALESCE(creator_count, 0) AS creator_count, 0 AS total_sol
                FROM funder_network_map
                """
            ).fetchall()
            ranked: defaultdict[str, list[tuple[int, float, str]]] = defaultdict(list)
            for row in rows:
                ranked[row["network_name"]].append(
                    (int(row["creator_count"] or 0), 0.0, row["funder_address"])
                )
            for network_name in list(ranked.keys()):
                ranked[network_name].sort(key=lambda item: (-item[0], item[2]))
            return dict(ranked)

        cf_cols = self._columns(conn, "creator_funders")
        total_sol_expr = "COALESCE(SUM(cf.amount_sol), 0)" if "amount_sol" in cf_cols else "0"
        rows = conn.execute(
            f"""
            SELECT
                fnm.network_name,
                fnm.funder_address,
                COALESCE(fnm.creator_count, COUNT(DISTINCT nm.creator_address), 0) AS creator_count,
                {total_sol_expr} AS total_sol
            FROM funder_network_map fnm
            LEFT JOIN network_membership nm ON nm.network_name = fnm.network_name
            LEFT JOIN creator_funders cf
                ON cf.creator_address = nm.creator_address
               AND cf.funder_address = fnm.funder_address
            GROUP BY fnm.network_name, fnm.funder_address, fnm.creator_count
            """
        ).fetchall()

        ranked: defaultdict[str, list[tuple[int, float, str]]] = defaultdict(list)
        for row in rows:
            ranked[row["network_name"]].append(
                (
                    int(row["creator_count"] or 0),
                    float(row["total_sol"] or 0),
                    row["funder_address"],
                )
            )
        for network_name in list(ranked.keys()):
            ranked[network_name].sort(key=lambda item: (-item[0], -item[1], item[2]))
        return dict(ranked)

    @staticmethod
    def _fallback_name(network_name: str) -> str:
        match = re.match(r"^Network_(\d+)$", network_name or "")
        if match:
            return f"Network-{match.group(1)}"
        return (network_name or "Network").replace("_", "-")

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
        cols = NetworkDisplayNameBuilder._columns(conn, table)
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
