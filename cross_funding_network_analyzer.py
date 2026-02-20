#!/usr/bin/env python3
"""
Optimized Cross-Funding Cluster Analyzer (Solana) — PATCHED

Patches applied (requested + correctness/perf):
1) SYSTEM is filtered everywhere (including _load_recipient_times, burst metrics inputs)
2) Funder clustering is no longer O(N^2) across ALL funders:
   - It only clusters funders that fund >=2 creators (the only ones that can form co-funding networks anyway)
3) _load_creator_to_funders now ACCUMULATES amounts per (creator,funder) (instead of overwriting)
4) funder_networks now stores a real cluster_id, so:
   - “unique clusters” is correct
   - “total SOL across clusters” can be computed without double-counting
5) CEX downweighting is kept (not removed) and applied in scoring as “weak evidence” per funder:
   - shared funder weight = 1.0 (non-CEX) or 0.3 (CEX), counted once per shared funder

DB-driven. No RPC calls.
"""

import sqlite3
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict, deque


DB_PATH = os.getenv("DB_PATH", "pumpswap_tokens.db")

# -----------------------------
# Tuning knobs
# -----------------------------
MIN_CREATORS_FOR_RECIPIENT_HUB = 2

# Funder cluster similarity thresholds
MIN_OVERLAP_CREATORS = 2
MIN_JACCARD = 0.25

# Creator cluster edge threshold
MIN_SHARED_DESTS_FOR_CREATOR_EDGE = 1

# Risk scoring weights
WEIGHT_CREATOR_COUNT = 2.0
WEIGHT_SHARED_RECIPIENT_HUB = 2.5
WEIGHT_SHARED_FUNDER = 2.0
WEIGHT_SHARED_DESTINATION = 2.0
WEIGHT_HIGH_VOLUME = 1.5
WEIGHT_BURST = 1.5

BOOST_VOLUME_SOL = 500.0
BURST_WINDOWS = [3600, 6 * 3600, 24 * 3600]

# Address filtering
IGNORE_ADDRESSES = {"SYSTEM"}

# CEX downweighting (kept, not removed)
CEX_FUNDER_MULTIPLIER = 0.3


# =========================================================================
# DATA CLASSES
# =========================================================================

@dataclass(frozen=True)
class NetworkCoordinator:
    address: str
    creator_count: int
    creators: List[str]
    total_sol_moved: float
    network_confidence: str  # 'high', 'medium', 'low'
    is_cex: bool
    suspicious_flags: List[str]


@dataclass(frozen=True)
class FunderCluster:
    cluster_id: str
    funders: Set[str]
    creators_served: Set[str]
    total_volume_sol: float
    edges: List[Tuple[str, str, float, int]]  # (funderA, funderB, jaccard, overlap)


@dataclass(frozen=True)
class CreatorCluster:
    cluster_id: str
    creators: Set[str]
    shared_destinations: Set[str]
    edges: List[Tuple[str, str, int]]  # (creatorA, creatorB, shared_dest_count)


@dataclass(frozen=True)
class UnifiedClusterReport:
    target_creator: str
    creators: Set[str]
    funders: Set[str]
    recipients: Set[str]
    destinations: Set[str]
    score: float
    risk_level: str  # 'CRITICAL', 'HIGH', 'MEDIUM', 'CLEAN'
    reasons: List[str]
    burst_metrics: Dict[str, Dict[str, int]]


# =========================================================================
# DB HELPERS
# =========================================================================

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _connect(db_path: str = DB_PATH, timeout: int = 60) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,))
    return cur.fetchone() is not None


def _get_columns(conn: sqlite3.Connection, table: str) -> Set[str]:
    cur = conn.cursor()
    try:
        cur.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}
    except Exception:
        return set()


def _pick_first_existing(columns: Set[str], candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in columns:
            return c
    return None


def _safe_json_dumps(obj) -> str:
    try:
        return json.dumps(obj)
    except Exception:
        return "[]"


def _as_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        try:
            if "T" in s:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(s.replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None


# =========================================================================
# UNION-FIND
# =========================================================================

class UnionFind:
    def __init__(self):
        self.parent: Dict[str, str] = {}
        self.rank: Dict[str, int] = {}

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
            return x
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
        elif self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            self.rank[ra] += 1

    def groups(self) -> Dict[str, Set[str]]:
        out: Dict[str, Set[str]] = defaultdict(set)
        for x in list(self.parent.keys()):
            out[self.find(x)].add(x)
        return out


# =========================================================================
# ANALYZER
# =========================================================================

class CrossFundingClusterAnalyzer:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.creators_set: Set[str] = set()
        self._ensure_db()
        self._load_creators()

    # -----------------------------
    # DB init / schema
    # -----------------------------

    def _ensure_db(self) -> None:
        conn = _connect(self.db_path, timeout=5)
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS creator_recipients_unified (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL,
                recipient_address TEXT NOT NULL,
                total_sol_sent REAL DEFAULT 0,
                transfer_count INTEGER DEFAULT 0,
                last_transfer_time TIMESTAMP,
                confidence TEXT DEFAULT 'medium',
                source TEXT NOT NULL,
                transaction_signatures TEXT,
                is_cex BOOLEAN DEFAULT 0,
                cex_exchange TEXT,
                cex_type TEXT,
                is_suspicious BOOLEAN DEFAULT 0,
                suspicious_reasons TEXT,
                network_flags TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(creator_address, recipient_address)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS network_coordinators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coordinator_address TEXT NOT NULL UNIQUE,
                creator_count INTEGER,
                creators_linked TEXT,
                total_sol_moved REAL,
                network_confidence TEXT,
                is_cex BOOLEAN DEFAULT 0,
                cex_exchange TEXT,
                suspicious_flags TEXT,
                detection_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS recipient_cross_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_address TEXT NOT NULL,
                creator_a TEXT NOT NULL,
                creator_b TEXT NOT NULL,
                shared_context TEXT,
                first_detected TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(recipient_address, creator_a, creator_b)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS creator_networks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL UNIQUE,
                connected_creators TEXT,
                shared_destinations TEXT,
                network_size INTEGER,
                network_risk_level TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # NOTE: cluster_id added here
        cur.execute("""
            CREATE TABLE IF NOT EXISTS funder_networks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                primary_funder TEXT NOT NULL UNIQUE,
                cluster_id TEXT,
                connected_funders TEXT,
                transfer_chain TEXT,
                creators_served TEXT,
                network_size INTEGER,
                total_volume_sol REAL,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Backfill schema if older DB exists without cluster_id
        try:
            cols = _get_columns(conn, "funder_networks")
            if "cluster_id" not in cols:
                cur.execute("ALTER TABLE funder_networks ADD COLUMN cluster_id TEXT;")
        except Exception:
            pass

        cur.execute("""
            CREATE TABLE IF NOT EXISTS unified_creator_clusters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_creator TEXT NOT NULL UNIQUE,
                cluster_creators TEXT,
                cluster_funders TEXT,
                cluster_recipients TEXT,
                cluster_destinations TEXT,
                score REAL,
                risk_level TEXT,
                reasons TEXT,
                burst_metrics TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_recipient_creator ON creator_recipients_unified(creator_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_recipient_address ON creator_recipients_unified(recipient_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_coordinator ON network_coordinators(coordinator_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_funder_cluster_id ON funder_networks(cluster_id)")

        conn.commit()
        conn.close()

    def _load_creators(self) -> None:
        try:
            conn = _connect(self.db_path, timeout=60)
            cur = conn.cursor()
            if not _table_exists(conn, "token_analysis"):
                self.creators_set = set()
                conn.close()
                print("[ANALYZER] token_analysis table not found; creators_set empty")
                return

            cur.execute("""
                SELECT DISTINCT earliest_tx_creator
                FROM token_analysis
                WHERE earliest_tx_creator IS NOT NULL
            """)
            self.creators_set = {row[0] for row in cur.fetchall() if row and row[0]}
            conn.close()
            print(f"[ANALYZER] Loaded {len(self.creators_set)} unique creators")
        except Exception as e:
            print(f"[ERROR] Loading creators: {e}")

    # =========================================================================
    # METHOD 1: RECIPIENT HUB COORDINATORS
    # =========================================================================

    def detect_network_coordinators(self) -> List[NetworkCoordinator]:
        conn = _connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        coordinators: List[NetworkCoordinator] = []

        if not _table_exists(conn, "creator_recipients_unified"):
            conn.close()
            print("[ANALYZER] creator_recipients_unified not found; skipping coordinators")
            return coordinators

        try:
            cur.execute("""
                SELECT recipient_address,
                       COUNT(DISTINCT creator_address) as unique_creators,
                       SUM(total_sol_sent) as total_sol,
                       MAX(CASE WHEN is_cex THEN 1 ELSE 0 END) as is_cex,
                       MAX(cex_exchange) as cex_exchange
                FROM creator_recipients_unified
                GROUP BY recipient_address
                HAVING unique_creators >= ?
                ORDER BY total_sol DESC
            """, (MIN_CREATORS_FOR_RECIPIENT_HUB,))
            rows = cur.fetchall()

            for row in rows:
                address = row["recipient_address"]
                if address in IGNORE_ADDRESSES:
                    continue

                creator_count = int(row["unique_creors"] if "unique_creors" in row.keys() else row["unique_creators"] or 0)
                total_sol = float(row["total_sol"] or 0.0)
                is_cex = bool(row["is_cex"])
                cex_exchange = row["cex_exchange"]

                cur.execute("""
                    SELECT DISTINCT creator_address
                    FROM creator_recipients_unified
                    WHERE recipient_address = ?
                """, (address,))
                creators = [r[0] for r in cur.fetchall()]

                if is_cex:
                    confidence = "low"
                elif creator_count >= 3:
                    confidence = "high"
                else:
                    confidence = "medium"

                flags: List[str] = []
                if creator_count >= 5:
                    flags.append(f"multiple_creator_links({creator_count})")
                if total_sol >= BOOST_VOLUME_SOL:
                    flags.append(f"high_volume({total_sol:.2f}SOL)")
                if is_cex and creator_count >= 10:
                    flags.append("cex_hub")

                coordinator = NetworkCoordinator(
                    address=address,
                    creator_count=creator_count,
                    creators=creators,
                    total_sol_moved=total_sol,
                    network_confidence=confidence,
                    is_cex=is_cex,
                    suspicious_flags=flags,
                )
                coordinators.append(coordinator)
                self._save_coordinator(conn, coordinator, cex_exchange=cex_exchange)
                self._write_recipient_crossrefs(conn, address, creators)

            conn.commit()
            return coordinators
        finally:
            conn.close()

    def _save_coordinator(self, conn: sqlite3.Connection, coordinator: NetworkCoordinator, cex_exchange: Optional[str]) -> None:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO network_coordinators
            (coordinator_address, creator_count, creators_linked, total_sol_moved,
             network_confidence, is_cex, cex_exchange, suspicious_flags, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            coordinator.address,
            coordinator.creator_count,
            _safe_json_dumps(coordinator.creators),
            coordinator.total_sol_moved,
            coordinator.network_confidence,
            1 if coordinator.is_cex else 0,
            cex_exchange,
            _safe_json_dumps(coordinator.suspicious_flags),
        ))

    def _write_recipient_crossrefs(self, conn: sqlite3.Connection, recipient: str, creators: List[str]) -> None:
        if len(creators) < 2:
            return
        cur = conn.cursor()
        creators_sorted = sorted(set(creators))
        for i in range(len(creators_sorted)):
            for j in range(i + 1, len(creators_sorted)):
                a, b = creators_sorted[i], creators_sorted[j]
                cur.execute("""
                    INSERT OR IGNORE INTO recipient_cross_references
                    (recipient_address, creator_a, creator_b, shared_context)
                    VALUES (?, ?, ?, ?)
                """, (recipient, a, b, "shared_recipient_hub"))

    # =========================================================================
    # METHOD 2: FUNDER CLUSTERS (REAL NETWORKS) — PATCHED PERF + cluster_id
    # =========================================================================

    def build_funder_clusters(self) -> List[FunderCluster]:
        conn = _connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        clusters: List[FunderCluster] = []

        if not _table_exists(conn, "creator_funders"):
            conn.close()
            print("[ANALYZER] creator_funders table not found; skipping funder clusters")
            return clusters

        cols = _get_columns(conn, "creator_funders")
        amount_col = _pick_first_existing(cols, ["amount_sol", "amount", "sol_amount"]) or "amount_sol"

        funder_to_creators: Dict[str, Set[str]] = defaultdict(set)
        funder_volume: Dict[str, float] = defaultdict(float)

        try:
            cur.execute(f"""
                SELECT funder_address, creator_address, COALESCE({amount_col}, 0) as amount_sol
                FROM creator_funders
                WHERE funder_address IS NOT NULL AND creator_address IS NOT NULL
            """)
            for row in cur.fetchall():
                f = row["funder_address"]
                c = row["creator_address"]
                if f in IGNORE_ADDRESSES or c in IGNORE_ADDRESSES:
                    continue
                funder_to_creators[f].add(c)
                try:
                    funder_volume[f] += float(row["amount_sol"] or 0.0)
                except Exception:
                    pass
        except Exception as e:
            conn.close()
            print(f"[ERROR] Reading creator_funders: {e}")
            return clusters

        # PATCH: only cluster funders that have >=2 creators (otherwise they can never connect)
        funders = [f for f, cs in funder_to_creators.items() if len(cs) >= 2]
        if len(funders) < 2:
            conn.close()
            return clusters

        uf = UnionFind()
        edges: List[Tuple[str, str, float, int]] = []
        for f in funders:
            uf.find(f)

        # O(n^2) is now safe because funders list is small (multi-target funders)
        for i in range(len(funders)):
            a = funders[i]
            A = funder_to_creators[a]
            for j in range(i + 1, len(funders)):
                b = funders[j]
                B = funder_to_creators[b]
                overlap = len(A & B)
                if overlap == 0:
                    continue
                union_sz = len(A | B)
                jaccard = overlap / union_sz if union_sz else 0.0

                if overlap >= MIN_OVERLAP_CREATORS or jaccard >= MIN_JACCARD:
                    uf.union(a, b)
                    edges.append((a, b, jaccard, overlap))

        cluster_groups = [g for g in uf.groups().values() if len(g) > 1]

        for idx, g in enumerate(cluster_groups, start=1):
            creators_served: Set[str] = set()
            total_vol = 0.0
            for f in g:
                creators_served |= funder_to_creators[f]
                total_vol += funder_volume.get(f, 0.0)

            cluster_id = f"FUNDERS_{idx}"
            cluster_edges = [e for e in edges if (e[0] in g and e[1] in g)]
            clusters.append(FunderCluster(
                cluster_id=cluster_id,
                funders=set(g),
                creators_served=creators_served,
                total_volume_sol=total_vol,
                edges=cluster_edges,
            ))

        self._persist_funder_cluster_summaries(conn, clusters)
        conn.commit()
        conn.close()
        return clusters

    def _persist_funder_cluster_summaries(self, conn: sqlite3.Connection, clusters: List[FunderCluster]) -> None:
        cur = conn.cursor()
        for cl in clusters:
            for primary in cl.funders:
                connected = sorted([f for f in cl.funders if f != primary])
                cur.execute("""
                    INSERT OR REPLACE INTO funder_networks
                    (primary_funder, cluster_id, connected_funders, transfer_chain, creators_served, network_size, total_volume_sol)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    primary,
                    cl.cluster_id,
                    _safe_json_dumps(connected),
                    _safe_json_dumps([]),
                    _safe_json_dumps(sorted(list(cl.creators_served))),
                    len(cl.funders),
                    cl.total_volume_sol,
                ))

    # =========================================================================
    # METHOD 3: CREATOR CLUSTERS
    # =========================================================================

    def build_creator_clusters(self) -> List[CreatorCluster]:
        conn = _connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        clusters: List[CreatorCluster] = []

        if not _table_exists(conn, "creator_sol_transfers"):
            conn.close()
            print("[ANALYZER] creator_sol_transfers table not found; skipping creator clusters")
            return clusters

        cols = _get_columns(conn, "creator_sol_transfers")
        creator_col = _pick_first_existing(cols, ["creator_address", "creator", "creator_pubkey"])
        dest_col = _pick_first_existing(cols, ["destination_address", "destination", "to_address", "recipient_address"])
        if creator_col is None or dest_col is None:
            conn.close()
            print("[ANALYZER] creator_sol_transfers missing creator/destination columns; skipping")
            return clusters

        creator_to_dests: Dict[str, Set[str]] = defaultdict(set)
        dest_to_creators: Dict[str, Set[str]] = defaultdict(set)

        try:
            cur.execute(f"""
                SELECT {creator_col} as creator, {dest_col} as dest
                FROM creator_sol_transfers
                WHERE {creator_col} IS NOT NULL AND {dest_col} IS NOT NULL
            """)
            for row in cur.fetchall():
                c = row["creator"]
                d = row["dest"]
                if d in IGNORE_ADDRESSES or c in IGNORE_ADDRESSES:
                    continue
                creator_to_dests[c].add(d)
                dest_to_creators[d].add(c)
        except Exception as e:
            conn.close()
            print(f"[ERROR] Reading creator_sol_transfers: {e}")
            return clusters

        creators = list(creator_to_dests.keys())
        if len(creators) < 2:
            conn.close()
            return clusters

        pair_shared_count: Dict[Tuple[str, str], int] = defaultdict(int)
        for dest, cset in dest_to_creators.items():
            c_list = sorted(cset)
            if len(c_list) < 2:
                continue
            for i in range(len(c_list)):
                for j in range(i + 1, len(c_list)):
                    pair_shared_count[(c_list[i], c_list[j])] += 1

        uf = UnionFind()
        for c in creators:
            uf.find(c)

        edges: List[Tuple[str, str, int]] = []
        for (a, b), shared_cnt in pair_shared_count.items():
            if shared_cnt >= MIN_SHARED_DESTS_FOR_CREATOR_EDGE:
                uf.union(a, b)
                edges.append((a, b, shared_cnt))

        cluster_groups = [g for g in uf.groups().values() if len(g) > 1]

        for idx, g in enumerate(cluster_groups, start=1):
            cluster_id = f"CREATORS_{idx}"
            gset = set(g)

            dest_counter: Dict[str, int] = defaultdict(int)
            for c in gset:
                for d in creator_to_dests.get(c, set()):
                    dest_counter[d] += 1
            shared_dests = {d for d, k in dest_counter.items() if k >= 2}

            cluster_edges = [e for e in edges if (e[0] in gset and e[1] in gset)]
            clusters.append(CreatorCluster(
                cluster_id=cluster_id,
                creators=gset,
                shared_destinations=shared_dests,
                edges=cluster_edges,
            ))

        self._persist_creator_cluster_summaries(conn, clusters)
        conn.commit()
        conn.close()
        return clusters

    def _persist_creator_cluster_summaries(self, conn: sqlite3.Connection, clusters: List[CreatorCluster]) -> None:
        cur = conn.cursor()
        for cl in clusters:
            for primary in cl.creators:
                connected = sorted([c for c in cl.creators if c != primary])
                risk = "HIGH" if len(cl.creators) >= 3 else "MEDIUM"
                cur.execute("""
                    INSERT OR REPLACE INTO creator_networks
                    (creator_address, connected_creators, shared_destinations, network_size, network_risk_level, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    primary,
                    _safe_json_dumps(connected),
                    _safe_json_dumps(sorted(list(cl.shared_destinations))),
                    len(cl.creators),
                    risk
                ))

    # =========================================================================
    # UNIFIED CLUSTER AROUND A TARGET CREATOR
    # =========================================================================

    def analyze_creator_unified_cluster(self, target_creator: str) -> UnifiedClusterReport:
        conn = _connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row

        # load mappings
        recipient_map = self._load_creator_to_recipients(conn)
        funder_map, funder_amounts, funder_time_map = self._load_creator_to_funders(conn)
        dest_map, dest_time_map = self._load_creator_to_destinations(conn)

        creators_seen: Set[str] = {target_creator}
        funders_seen: Set[str] = set(funder_map.get(target_creator, set()))
        recipients_seen: Set[str] = set(recipient_map.get(target_creator, set()))
        dests_seen: Set[str] = set(dest_map.get(target_creator, set()))

        recipient_to_creators = defaultdict(set)
        for c, rs in recipient_map.items():
            for r in rs:
                recipient_to_creators[r].add(c)

        funder_to_creators = defaultdict(set)
        for c, fs in funder_map.items():
            for f in fs:
                funder_to_creators[f].add(c)

        dest_to_creators = defaultdict(set)
        for c, ds in dest_map.items():
            for d in ds:
                dest_to_creators[d].add(c)

        queue = deque([target_creator])
        while queue:
            c = queue.popleft()
            rs = recipient_map.get(c, set())
            fs = funder_map.get(c, set())
            ds = dest_map.get(c, set())

            for r in rs:
                recipients_seen.add(r)
                for other in recipient_to_creators.get(r, set()):
                    if other not in creators_seen:
                        creators_seen.add(other)
                        queue.append(other)

            for f in fs:
                funders_seen.add(f)
                for other in funder_to_creators.get(f, set()):
                    if other not in creators_seen:
                        creators_seen.add(other)
                        queue.append(other)

            for d in ds:
                dests_seen.add(d)
                for other in dest_to_creators.get(d, set()):
                    if other not in creators_seen:
                        creators_seen.add(other)
                        queue.append(other)

        burst_metrics = self._compute_burst_metrics(
            target_creator=target_creator,
            creators=creators_seen,
            recipient_time_map=self._load_recipient_times(conn),
            funder_time_map=funder_time_map,
            dest_time_map=dest_time_map
        )

        score, reasons, risk_level = self._score_unified_cluster(
            target_creator=target_creator,
            creators=creators_seen,
            funders=funders_seen,
            recipients=recipients_seen,
            destinations=dests_seen,
            recipient_map=recipient_map,
            funder_map=funder_map,
            dest_map=dest_map,
            funder_amounts=funder_amounts,
            burst_metrics=burst_metrics,
            conn=conn
        )

        report = UnifiedClusterReport(
            target_creator=target_creator,
            creators=creators_seen,
            funders=funders_seen,
            recipients=recipients_seen,
            destinations=dests_seen,
            score=score,
            risk_level=risk_level,
            reasons=reasons,
            burst_metrics=burst_metrics
        )

        self._persist_unified_cluster(conn, report)
        conn.commit()
        conn.close()
        return report

    def _persist_unified_cluster(self, conn: sqlite3.Connection, report: UnifiedClusterReport) -> None:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO unified_creator_clusters
            (target_creator, cluster_creators, cluster_funders, cluster_recipients, cluster_destinations,
             score, risk_level, reasons, burst_metrics, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            report.target_creator,
            _safe_json_dumps(sorted(list(report.creators))),
            _safe_json_dumps(sorted(list(report.funders))),
            _safe_json_dumps(sorted(list(report.recipients))),
            _safe_json_dumps(sorted(list(report.destinations))),
            report.score,
            report.risk_level,
            _safe_json_dumps(report.reasons),
            _safe_json_dumps(report.burst_metrics),
        ))

    # =========================================================================
    # LOADERS
    # =========================================================================

    def _load_creator_to_recipients(self, conn: sqlite3.Connection) -> Dict[str, Set[str]]:
        out: Dict[str, Set[str]] = defaultdict(set)
        if not _table_exists(conn, "creator_recipients_unified"):
            return out
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT creator_address, recipient_address
                FROM creator_recipients_unified
                WHERE creator_address IS NOT NULL AND recipient_address IS NOT NULL
            """)
            for c, r in cur.fetchall():
                if r in IGNORE_ADDRESSES or c in IGNORE_ADDRESSES:
                    continue
                out[c].add(r)
        except Exception:
            pass
        return out

    def _load_recipient_times(self, conn: sqlite3.Connection) -> Dict[Tuple[str, str], Optional[datetime]]:
        out: Dict[Tuple[str, str], Optional[datetime]] = {}
        if not _table_exists(conn, "creator_recipients_unified"):
            return out
        cols = _get_columns(conn, "creator_recipients_unified")
        tcol = _pick_first_existing(cols, ["last_transfer_time", "updated_at", "created_at"])
        if tcol is None:
            return out
        cur = conn.cursor()
        try:
            cur.execute(f"""
                SELECT creator_address, recipient_address, {tcol}
                FROM creator_recipients_unified
                WHERE creator_address IS NOT NULL AND recipient_address IS NOT NULL
            """)
            for c, r, t in cur.fetchall():
                # PATCH: filter SYSTEM here too
                if r in IGNORE_ADDRESSES or c in IGNORE_ADDRESSES:
                    continue
                out[(c, r)] = _as_dt(t)
        except Exception:
            pass
        return out

    def _load_creator_to_funders(self, conn: sqlite3.Connection) -> Tuple[
        Dict[str, Set[str]],
        Dict[Tuple[str, str], float],
        Dict[Tuple[str, str], Optional[datetime]]
    ]:
        creator_to_funders: Dict[str, Set[str]] = defaultdict(set)
        amount_map: Dict[Tuple[str, str], float] = {}
        time_map: Dict[Tuple[str, str], Optional[datetime]] = {}

        if not _table_exists(conn, "creator_funders"):
            return creator_to_funders, amount_map, time_map

        cols = _get_columns(conn, "creator_funders")
        amount_col = _pick_first_existing(cols, ["amount_sol", "amount", "sol_amount"]) or "amount_sol"
        time_col = _pick_first_existing(cols, ["funding_time", "block_time", "timestamp", "created_at", "updated_at"])
        is_cex_col = _pick_first_existing(cols, ["is_cex", "is_exchange"])

        cur = conn.cursor()
        try:
            if time_col and is_cex_col:
                cur.execute(f"""
                    SELECT creator_address, funder_address, COALESCE({amount_col}, 0) as amount_sol, {time_col}, {is_cex_col}
                    FROM creator_funders
                    WHERE creator_address IS NOT NULL AND funder_address IS NOT NULL
                """)
                for c, f, amt, t, is_cex in cur.fetchall():
                    if f in IGNORE_ADDRESSES or c in IGNORE_ADDRESSES:
                        continue
                    creator_to_funders[c].add(f)
                    try:
                        amount = float(amt or 0.0)
                        if bool(is_cex):
                            amount *= CEX_FUNDER_MULTIPLIER
                        # PATCH: accumulate
                        amount_map[(c, f)] = amount_map.get((c, f), 0.0) + amount
                    except Exception:
                        amount_map[(c, f)] = amount_map.get((c, f), 0.0)
                    time_map[(c, f)] = _as_dt(t)

            elif time_col:
                cur.execute(f"""
                    SELECT creator_address, funder_address, COALESCE({amount_col}, 0) as amount_sol, {time_col}
                    FROM creator_funders
                    WHERE creator_address IS NOT NULL AND funder_address IS NOT NULL
                """)
                for c, f, amt, t in cur.fetchall():
                    if f in IGNORE_ADDRESSES or c in IGNORE_ADDRESSES:
                        continue
                    creator_to_funders[c].add(f)
                    try:
                        amount = float(amt or 0.0)
                        amount_map[(c, f)] = amount_map.get((c, f), 0.0) + amount
                    except Exception:
                        amount_map[(c, f)] = amount_map.get((c, f), 0.0)
                    time_map[(c, f)] = _as_dt(t)

            elif is_cex_col:
                cur.execute(f"""
                    SELECT creator_address, funder_address, COALESCE({amount_col}, 0) as amount_sol, {is_cex_col}
                    FROM creator_funders
                    WHERE creator_address IS NOT NULL AND funder_address IS NOT NULL
                """)
                for c, f, amt, is_cex in cur.fetchall():
                    if f in IGNORE_ADDRESSES or c in IGNORE_ADDRESSES:
                        continue
                    creator_to_funders[c].add(f)
                    try:
                        amount = float(amt or 0.0)
                        if bool(is_cex):
                            amount *= CEX_FUNDER_MULTIPLIER
                        amount_map[(c, f)] = amount_map.get((c, f), 0.0) + amount
                    except Exception:
                        amount_map[(c, f)] = amount_map.get((c, f), 0.0)

            else:
                cur.execute(f"""
                    SELECT creator_address, funder_address, COALESCE({amount_col}, 0) as amount_sol
                    FROM creator_funders
                    WHERE creator_address IS NOT NULL AND funder_address IS NOT NULL
                """)
                for c, f, amt in cur.fetchall():
                    if f in IGNORE_ADDRESSES or c in IGNORE_ADDRESSES:
                        continue
                    creator_to_funders[c].add(f)
                    try:
                        amount = float(amt or 0.0)
                        amount_map[(c, f)] = amount_map.get((c, f), 0.0) + amount
                    except Exception:
                        amount_map[(c, f)] = amount_map.get((c, f), 0.0)

        except Exception:
            pass

        return creator_to_funders, amount_map, time_map

    def _load_creator_to_destinations(self, conn: sqlite3.Connection) -> Tuple[
        Dict[str, Set[str]],
        Dict[Tuple[str, str], Optional[datetime]]
    ]:
        creator_to_dests: Dict[str, Set[str]] = defaultdict(set)
        time_map: Dict[Tuple[str, str], Optional[datetime]] = {}

        if not _table_exists(conn, "creator_sol_transfers"):
            return creator_to_dests, time_map

        cols = _get_columns(conn, "creator_sol_transfers")
        creator_col = _pick_first_existing(cols, ["creator_address", "creator", "creator_pubkey"])
        dest_col = _pick_first_existing(cols, ["destination_address", "destination", "to_address", "recipient_address"])
        time_col = _pick_first_existing(cols, ["last_transfer_time", "block_time", "timestamp", "created_at", "updated_at"])

        if creator_col is None or dest_col is None:
            return creator_to_dests, time_map

        cur = conn.cursor()
        try:
            if time_col:
                cur.execute(f"""
                    SELECT {creator_col} as creator, {dest_col} as dest, {time_col}
                    FROM creator_sol_transfers
                    WHERE {creator_col} IS NOT NULL AND {dest_col} IS NOT NULL
                """)
                for c, d, t in cur.fetchall():
                    if d in IGNORE_ADDRESSES or c in IGNORE_ADDRESSES:
                        continue
                    creator_to_dests[c].add(d)
                    time_map[(c, d)] = _as_dt(t)
            else:
                cur.execute(f"""
                    SELECT {creator_col} as creator, {dest_col} as dest
                    FROM creator_sol_transfers
                    WHERE {creator_col} IS NOT NULL AND {dest_col} IS NOT NULL
                """)
                for c, d in cur.fetchall():
                    if d in IGNORE_ADDRESSES or c in IGNORE_ADDRESSES:
                        continue
                    creator_to_dests[c].add(d)
        except Exception:
            pass

        return creator_to_dests, time_map

    # =========================================================================
    # BURST METRICS
    # =========================================================================

    def _compute_burst_metrics(
        self,
        target_creator: str,
        creators: Set[str],
        recipient_time_map: Dict[Tuple[str, str], Optional[datetime]],
        funder_time_map: Dict[Tuple[str, str], Optional[datetime]],
        dest_time_map: Dict[Tuple[str, str], Optional[datetime]],
    ) -> Dict[str, Dict[str, int]]:
        now = _utcnow()

        def window_key(seconds: int) -> str:
            if seconds == 3600:
                return "1h"
            if seconds == 6 * 3600:
                return "6h"
            if seconds == 24 * 3600:
                return "24h"
            return f"{seconds}s"

        metrics = {"recipient": {}, "funder": {}, "destination": {}}

        if recipient_time_map:
            for w in BURST_WINDOWS:
                cutoff = now.timestamp() - w
                seen: Set[str] = set()
                for (c, r), t in recipient_time_map.items():
                    if r in IGNORE_ADDRESSES:
                        continue
                    if c == target_creator or c not in creators:
                        continue
                    if t and t.timestamp() >= cutoff:
                        seen.add(c)
                metrics["recipient"][window_key(w)] = len(seen)

        if funder_time_map:
            for w in BURST_WINDOWS:
                cutoff = now.timestamp() - w
                seen: Set[str] = set()
                for (c, f), t in funder_time_map.items():
                    if f in IGNORE_ADDRESSES:
                        continue
                    if c == target_creator or c not in creators:
                        continue
                    if t and t.timestamp() >= cutoff:
                        seen.add(c)
                metrics["funder"][window_key(w)] = len(seen)

        if dest_time_map:
            for w in BURST_WINDOWS:
                cutoff = now.timestamp() - w
                seen: Set[str] = set()
                for (c, d), t in dest_time_map.items():
                    if d in IGNORE_ADDRESSES:
                        continue
                    if c == target_creator or c not in creators:
                        continue
                    if t and t.timestamp() >= cutoff:
                        seen.add(c)
                metrics["destination"][window_key(w)] = len(seen)

        return metrics

    def _load_is_cex_funders(self, conn: sqlite3.Connection) -> Dict[str, bool]:
        is_cex_map: Dict[str, bool] = {}
        if not _table_exists(conn, "creator_funders"):
            return is_cex_map

        cols = _get_columns(conn, "creator_funders")
        is_cex_col = _pick_first_existing(cols, ["is_cex", "is_exchange"])
        if is_cex_col is None:
            return is_cex_map

        cur = conn.cursor()
        try:
            # PATCH: use MAX to avoid inconsistent rows
            cur.execute(f"""
                SELECT funder_address, MAX(CASE WHEN {is_cex_col} THEN 1 ELSE 0 END) as is_cex
                FROM creator_funders
                WHERE funder_address IS NOT NULL
                GROUP BY funder_address
            """)
            for f, is_cex in cur.fetchall():
                if f in IGNORE_ADDRESSES:
                    continue
                is_cex_map[f] = bool(is_cex)
        except Exception:
            pass

        return is_cex_map

    # =========================================================================
    # SCORING — PATCHED CEX weighting counted once per shared funder
    # =========================================================================

    def _score_unified_cluster(
        self,
        target_creator: str,
        creators: Set[str],
        funders: Set[str],
        recipients: Set[str],
        destinations: Set[str],
        recipient_map: Dict[str, Set[str]],
        funder_map: Dict[str, Set[str]],
        dest_map: Dict[str, Set[str]],
        funder_amounts: Dict[Tuple[str, str], float],
        burst_metrics: Dict[str, Dict[str, int]],
        conn: sqlite3.Connection,
    ) -> Tuple[float, List[str], str]:
        reasons: List[str] = []
        score = 0.0

        creator_count = len(creators)
        if creator_count > 1:
            score += WEIGHT_CREATOR_COUNT * (creator_count - 1)
            reasons.append(f"connected_creators({creator_count})")

        # shared recipients
        recipient_counts = defaultdict(int)
        for c in creators:
            for r in recipient_map.get(c, set()):
                if r in IGNORE_ADDRESSES:
                    continue
                recipient_counts[r] += 1
        shared_recipient_hubs = [r for r, k in recipient_counts.items() if k >= 2]
        if shared_recipient_hubs:
            score += WEIGHT_SHARED_RECIPIENT_HUB * min(len(shared_recipient_hubs), 10)
            reasons.append(f"shared_recipient_hubs({len(shared_recipient_hubs)})")

        # shared funders, weighted (once per shared funder)
        is_cex_map = self._load_is_cex_funders(conn)

        funder_counts = defaultdict(int)
        for c in creators:
            for f in funder_map.get(c, set()):
                if f in IGNORE_ADDRESSES:
                    continue
                funder_counts[f] += 1

        shared_funders = [f for f, k in funder_counts.items() if k >= 2]
        if shared_funders:
            weighted_count = 0.0
            for f in shared_funders:
                weighted_count += (CEX_FUNDER_MULTIPLIER if is_cex_map.get(f, False) else 1.0)
            weighted_count = min(weighted_count, 10.0)
            score += WEIGHT_SHARED_FUNDER * weighted_count
            reasons.append(f"shared_funders({len(shared_funders)}, weighted={weighted_count:.1f})")

        # shared destinations
        dest_counts = defaultdict(int)
        for c in creators:
            for d in dest_map.get(c, set()):
                if d in IGNORE_ADDRESSES:
                    continue
                dest_counts[d] += 1
        shared_dests = [d for d, k in dest_counts.items() if k >= 2]
        if shared_dests:
            score += WEIGHT_SHARED_DESTINATION * min(len(shared_dests), 10)
            reasons.append(f"shared_destinations({len(shared_dests)})")

        # total funding (already CEX-downweighted in loader)
        total_funding = 0.0
        for c in creators:
            for f in funder_map.get(c, set()):
                if f in IGNORE_ADDRESSES:
                    continue
                total_funding += funder_amounts.get((c, f), 0.0)
        if total_funding >= BOOST_VOLUME_SOL:
            score += WEIGHT_HIGH_VOLUME
            reasons.append(f"high_total_funding({total_funding:.2f}SOL)")

        # burst boost
        for entity_type in ["recipient", "funder", "destination"]:
            m = burst_metrics.get(entity_type, {})
            if m.get("1h", 0) >= 3 or m.get("6h", 0) >= 5:
                score += WEIGHT_BURST
                reasons.append(f"burst_{entity_type}")
                break

        if score >= 12:
            risk = "CRITICAL"
        elif score >= 7:
            risk = "HIGH"
        elif score >= 3:
            risk = "MEDIUM"
        else:
            risk = "CLEAN"

        if not reasons:
            reasons.append("no_strong_links_detected")

        return score, reasons, risk

    # =========================================================================
    # PIPELINE
    # =========================================================================

    def analyze_new_token(self, creator_address: str) -> Dict:
        print(f"\n[ANALYZER] ===== Starting Cluster Analysis for {creator_address[:8]}... =====")
        results = {
            "timestamp": _utcnow().isoformat(),
            "creator": creator_address,
            "recipient_coordinators": 0,
            "funder_clusters": 0,
            "creator_clusters": 0,
            "unified_cluster": {},
            "total_flags": 0,
        }

        print("[ANALYZER] Phase 1: Detecting recipient hubs...")
        coordinators = self.detect_network_coordinators()
        results["recipient_coordinators"] = len(coordinators)
        print(f"  ✓ Found {len(coordinators)} recipient hubs")

        print("[ANALYZER] Phase 2: Building funder clusters...")
        funder_clusters = self.build_funder_clusters()
        results["funder_clusters"] = len(funder_clusters)
        print(f"  ✓ Found {len(funder_clusters)} funder clusters")

        print("[ANALYZER] Phase 3: Building creator clusters...")
        creator_clusters = self.build_creator_clusters()
        results["creator_clusters"] = len(creator_clusters)
        print(f"  ✓ Found {len(creator_clusters)} creator clusters")

        print("[ANALYZER] Phase 4: Unified cluster scoring...")
        report = self.analyze_creator_unified_cluster(creator_address)
        results["unified_cluster"] = {
            "score": report.score,
            "risk_level": report.risk_level,
            "reasons": report.reasons,
            "cluster_sizes": {
                "creators": len(report.creators),
                "funders": len(report.funders),
                "recipients": len(report.recipients),
                "destinations": len(report.destinations),
            },
            "burst_metrics": report.burst_metrics,
        }
        print(f"  ✓ Risk={report.risk_level} score={report.score:.2f}")

        results["total_flags"] = (
            results["recipient_coordinators"]
            + results["funder_clusters"]
            + results["creator_clusters"]
            + (1 if report.risk_level in ("MEDIUM", "HIGH", "CRITICAL") else 0)
        )

        print(f"\n[ANALYZER] ===== Analysis Complete =====")
        print(f"  Total flagged outputs: {results['total_flags']}")
        return results

    def run_full_analysis(self) -> Dict:
        print("[ANALYZER] Starting full cluster analysis...")

        results = {
            "timestamp": _utcnow().isoformat(),
            "recipient_coordinators": 0,
            "funder_clusters": 0,
            "creator_clusters": 0,
        }

        results["recipient_coordinators"] = len(self.detect_network_coordinators())
        results["funder_clusters"] = len(self.build_funder_clusters())
        results["creator_clusters"] = len(self.build_creator_clusters())

        print(f"\n[ANALYZER] Full Analysis Complete")
        print(f"  Recipient Hubs: {results['recipient_coordinators']}")
        print(f"  Funder Clusters: {results['funder_clusters']}")
        print(f"  Creator Clusters: {results['creator_clusters']}")
        return results


# =========================================================================
# SINGLETON + CONVENIENCE
# =========================================================================

_analyzer: Optional[CrossFundingClusterAnalyzer] = None


def get_analyzer() -> CrossFundingClusterAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = CrossFundingClusterAnalyzer(DB_PATH)
    return _analyzer


def analyze_funding_clusters_for_token(creator_address: str) -> Dict:
    return get_analyzer().analyze_new_token(creator_address)


def run_full_cluster_analysis() -> Dict:
    return get_analyzer().run_full_analysis()


# =========================================================================
# MAIN
# =========================================================================

def main():
    analyzer = CrossFundingClusterAnalyzer(DB_PATH)
    results = analyzer.run_full_analysis()
    print("\n" + "=" * 80)
    print("FINAL RESULTS:")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()