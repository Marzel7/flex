#!/usr/bin/env python3
"""
Cross-Funding Cluster Analyzer (v2.2) — Clusters + ALL Networks (Solana, DB-driven)

What's new vs v2.1:
✅ Outputs **ALL funder networks regardless of size** ("atomic networks"):
   - Every funder that funded >=2 creators becomes a network record (even if it doesn't join any cluster)
✅ Excludes **CEX/infra funders from clustering topology** (so Coinbase won't "belong" to FUNDERS_1),
   but still records them for investigation.
✅ Keeps your existing outputs:
   - clustered funder networks (FUNDERS_1..N) in funder_networks
   - recipient hubs in network_coordinators (+ cross refs)
   - creator networks (if creator_sol_transfers exists)
   - unified per-creator cluster scoring

Tables:
- atomic_funder_networks  (NEW): one row per multi-target funder (creator_count>=2), regardless of clustering
- funder_networks         (existing): one row per funder that belongs to a non-CEX cluster, with cluster_id
- infra_funders_observed   (NEW): CEX/infra multi-target funders excluded from clustering but recorded

This module is DB-only (no RPC calls).
"""

import sqlite3
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict, deque

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")

# funder_networks lives in the investigation archive DB (moved out of the hot
# DB). The analyzer writes cluster membership there, not to the hot DB, so the
# offline build never refills the hot table after the archive move.
_ANALYZER_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INVESTIGATION_ARCHIVE_DB = os.path.abspath(os.getenv(
    "INVESTIGATION_ARCHIVE_DB",
    os.path.join(_ANALYZER_REPO_ROOT, "database", "flex_investigation_archive.db")))

# -----------------------------
# Tuning knobs
# -----------------------------
MIN_CREATORS_FOR_RECIPIENT_HUB = 2

# Atomic networks: include any funder funding >= this many creators
MIN_CREATORS_FOR_ATOMIC_FUNDER_NETWORK = 2

# Cluster edges (between funders) based on similarity of funded creator sets
MIN_OVERLAP_CREATORS = 2
MIN_JACCARD = 0.25

# Creator cluster edge threshold
MIN_SHARED_DESTS_FOR_CREATOR_EDGE = 1

# Address filtering
IGNORE_ADDRESSES = {"SYSTEM"}

# CEX handling
CEX_FUNDER_MULTIPLIER = 0.3           # used in scoring/amounts (optional)
EXCLUDE_CEX_FROM_CLUSTERING = True    # ✅ per your request


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
    edges: List[Tuple[str, str, float, int]]  # (a,b,jaccard,overlap)


@dataclass(frozen=True)
class CreatorCluster:
    cluster_id: str
    creators: Set[str]
    shared_destinations: Set[str]
    edges: List[Tuple[str, str, int]]  # (a,b,shared_dest_count)


@dataclass(frozen=True)
class UnifiedClusterReport:
    target_creator: str
    creators: Set[str]
    funders: Set[str]
    recipients: Set[str]
    destinations: Set[str]
    score: float
    risk_level: str
    reasons: List[str]
    burst_metrics: Dict[str, Dict[str, int]]


# =========================================================================
# HELPERS
# =========================================================================

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _connect(db_path: str = DB_PATH, timeout: int = 60) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row
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


def _safe_json(obj) -> str:
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
    def __init__(self, db_path: str = DB_PATH, archive_db_path: str = INVESTIGATION_ARCHIVE_DB):
        self.db_path = db_path
        # funder_networks writes target the archive DB, not the hot DB.
        self.archive_db_path = archive_db_path
        self.creators_set: Set[str] = set()
        self._ensure_db()
        self._ensure_archive_db()
        self._load_creators()

    # -----------------------------
    # DB init / schema
    # -----------------------------

    def _ensure_db(self) -> None:
        conn = _connect(self.db_path, timeout=5)
        cur = conn.cursor()

        # existing tables
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

        # NOTE: funder_networks is no longer created here — it has been moved to
        # the investigation archive DB (see _ensure_archive_db). This prevents
        # the analyzer from recreating/refilling the table in the hot DB.

        # unified per-creator
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

        # ✅ NEW: atomic funder networks (ALL multi-target funders, regardless of clustering)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS atomic_funder_networks (
                funder_address TEXT PRIMARY KEY,
                creators_funded INTEGER,
                creators_served TEXT,
                total_volume_sol REAL,
                is_cex BOOLEAN DEFAULT 0,
                excluded_from_clustering BOOLEAN DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ✅ NEW: infra funders observed (CEX/infra multi-target funders excluded from clustering)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS infra_funders_observed (
                funder_address TEXT PRIMARY KEY,
                creators_funded INTEGER,
                creators_served TEXT,
                total_volume_sol REAL,
                note TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_recipient_creator ON creator_recipients_unified(creator_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_recipient_address ON creator_recipients_unified(recipient_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_coordinator ON network_coordinators(coordinator_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_atomic_funder ON atomic_funder_networks(funder_address)")
        # idx_funder_cluster_id is created in the archive DB (see _ensure_archive_db)

        conn.commit()
        conn.close()

    def _ensure_archive_db(self) -> None:
        """Create funder_networks (+ index) in the investigation archive DB.

        funder_networks was moved out of the hot DB; the analyzer now writes
        cluster membership here so the hot DB is never refilled."""
        os.makedirs(os.path.dirname(self.archive_db_path), exist_ok=True)
        conn = _connect(self.archive_db_path, timeout=30)
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS funder_networks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    primary_funder TEXT NOT NULL UNIQUE,
                    connected_funders TEXT,
                    transfer_chain TEXT,
                    creators_served TEXT,
                    network_size INTEGER,
                    total_volume_sol REAL,
                    cluster_id TEXT,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_funder_cluster_id ON funder_networks(cluster_id)")
            conn.commit()
        finally:
            conn.close()

    def _load_creators(self) -> None:
        conn = _connect(self.db_path, timeout=60)
        try:
            if not _table_exists(conn, "token_analysis"):
                self.creators_set = set()
                print("[ANALYZER] token_analysis not found; creators_set empty")
                return
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT earliest_tx_creator
                FROM token_analysis
                WHERE earliest_tx_creator IS NOT NULL
            """)
            self.creators_set = {row[0] for row in cur.fetchall() if row and row[0]}
            print(f"[ANALYZER] Loaded {len(self.creators_set)} unique creators")
        finally:
            conn.close()

    # =========================================================================
    # METHOD 1: RECIPIENT HUBS
    # =========================================================================

    def detect_network_coordinators(self) -> List[NetworkCoordinator]:
        conn = _connect(self.db_path, timeout=60)
        coordinators: List[NetworkCoordinator] = []
        try:
            if not _table_exists(conn, "creator_recipients_unified"):
                print("[ANALYZER] creator_recipients_unified missing; skipping hubs")
                return coordinators

            cur = conn.cursor()
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
                recipient = row["recipient_address"]
                if recipient in IGNORE_ADDRESSES:
                    continue

                creator_count = int(row["unique_creators"] or 0)
                total_sol = float(row["total_sol"] or 0.0)
                is_cex = bool(row["is_cex"])
                cex_exchange = row["cex_exchange"]

                cur.execute("""
                    SELECT DISTINCT creator_address
                    FROM creator_recipients_unified
                    WHERE recipient_address = ?
                """, (recipient,))
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
                if total_sol >= 500:
                    flags.append(f"high_volume({total_sol:.2f}SOL)")
                if is_cex and creator_count >= 10:
                    flags.append("cex_hub")

                coordinator = NetworkCoordinator(
                    address=recipient,
                    creator_count=creator_count,
                    creators=creators,
                    total_sol_moved=total_sol,
                    network_confidence=confidence,
                    is_cex=is_cex,
                    suspicious_flags=flags,
                )
                coordinators.append(coordinator)

                # persist
                cur.execute("""
                    INSERT OR REPLACE INTO network_coordinators
                    (coordinator_address, creator_count, creators_linked, total_sol_moved,
                     network_confidence, is_cex, cex_exchange, suspicious_flags, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    coordinator.address,
                    coordinator.creator_count,
                    _safe_json(coordinator.creators),
                    coordinator.total_sol_moved,
                    coordinator.network_confidence,
                    1 if coordinator.is_cex else 0,
                    cex_exchange,
                    _safe_json(coordinator.suspicious_flags),
                ))

                # audit cross refs
                self._write_recipient_crossrefs(conn, recipient, creators)

            conn.commit()
            return coordinators
        finally:
            conn.close()

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
    # NEW METHOD: ATOMIC FUNDER NETWORKS (ALL multi-target funders)
    # =========================================================================

    def build_atomic_funder_networks(self) -> Dict[str, Dict]:
        """
        Builds and persists ALL funder networks regardless of size:
          - one row per funder where creator_count >= MIN_CREATORS_FOR_ATOMIC_FUNDER_NETWORK
          - stores creators_served JSON + total_volume_sol
          - marks is_cex and excluded_from_clustering
        Also populates infra_funders_observed for CEX funders (for investigation).
        """
        conn = _connect(self.db_path, timeout=60)
        try:
            if not _table_exists(conn, "creator_funders"):
                print("[ANALYZER] creator_funders missing; skipping atomic networks")
                return {}

            cols = _get_columns(conn, "creator_funders")
            amount_col = _pick_first_existing(cols, ["amount_sol", "amount", "sol_amount"]) or "amount_sol"
            is_cex_col = _pick_first_existing(cols, ["is_cex", "is_exchange"])
            funder_col = _pick_first_existing(cols, ["funder_address", "funder"]) or "funder_address"
            creator_col = _pick_first_existing(cols, ["creator_address", "creator"]) or "creator_address"

            # Load raw rows and aggregate
            cur = conn.cursor()
            if is_cex_col:
                cur.execute(f"""
                    SELECT {funder_col} AS funder, {creator_col} AS creator,
                           COALESCE({amount_col}, 0) AS amount_sol,
                           COALESCE({is_cex_col}, 0) AS is_cex
                    FROM creator_funders
                    WHERE {funder_col} IS NOT NULL AND {creator_col} IS NOT NULL
                """)
            else:
                cur.execute(f"""
                    SELECT {funder_col} AS funder, {creator_col} AS creator,
                           COALESCE({amount_col}, 0) AS amount_sol,
                           0 AS is_cex
                    FROM creator_funders
                    WHERE {funder_col} IS NOT NULL AND {creator_col} IS NOT NULL
                """)

            funder_to_creators: Dict[str, Set[str]] = defaultdict(set)
            funder_to_volume: Dict[str, float] = defaultdict(float)
            funder_is_cex: Dict[str, bool] = defaultdict(bool)

            for row in cur.fetchall():
                f = row["funder"]
                c = row["creator"]
                if not f or not c:
                    continue
                if f in IGNORE_ADDRESSES or c in IGNORE_ADDRESSES:
                    continue

                funder_to_creators[f].add(c)
                try:
                    funder_to_volume[f] += float(row["amount_sol"] or 0.0)
                except Exception:
                    pass
                if row["is_cex"]:
                    funder_is_cex[f] = True

            # Persist atomic networks for funders with >=2 creators
            atomic: Dict[str, Dict] = {}
            for f, creators in funder_to_creators.items():
                if len(creators) < MIN_CREATORS_FOR_ATOMIC_FUNDER_NETWORK:
                    continue
                creators_list = sorted(list(creators))
                total_vol = float(funder_to_volume.get(f, 0.0))
                is_cex = bool(funder_is_cex.get(f, False))
                excluded = 1 if (EXCLUDE_CEX_FROM_CLUSTERING and is_cex) else 0

                atomic[f] = {
                    "funder_address": f,
                    "creators_funded": len(creators_list),
                    "creators_served": creators_list,
                    "total_volume_sol": total_vol,
                    "is_cex": is_cex,
                    "excluded_from_clustering": bool(excluded),
                }

                cur.execute("""
                    INSERT OR REPLACE INTO atomic_funder_networks
                    (funder_address, creators_funded, creators_served, total_volume_sol,
                     is_cex, excluded_from_clustering, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    f,
                    len(creators_list),
                    _safe_json(creators_list),
                    total_vol,
                    1 if is_cex else 0,
                    excluded,
                ))

                # If excluded (CEX/infra), also note for investigation
                if excluded:
                    cur.execute("""
                        INSERT OR REPLACE INTO infra_funders_observed
                        (funder_address, creators_funded, creators_served, total_volume_sol,
                         note, updated_at)
                        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        f,
                        len(creators_list),
                        _safe_json(creators_list),
                        total_vol,
                        "CEX/infra funder excluded from clustering topology (kept for investigation)",
                    ))

            conn.commit()
            print(f"[ANALYZER] Atomic funder networks saved: {len(atomic)} (creator_count>=2)")
            if EXCLUDE_CEX_FROM_CLUSTERING:
                cex_cnt = sum(1 for v in atomic.values() if v["is_cex"])
                print(f"[ANALYZER]  └─ of which CEX/infra (excluded from clustering): {cex_cnt}")
            return atomic
        finally:
            conn.close()

    # =========================================================================
    # METHOD 2: FUNDER CLUSTERS (built on NON-CEX atomic networks)
    # =========================================================================

    def build_funder_clusters(self) -> List[FunderCluster]:
        """
        Clusters non-CEX funders using co-funding similarity.
        IMPORTANT:
          - CEX/infra funders are excluded from the clustering graph (if enabled)
          - But they are still stored in atomic_funder_networks / infra_funders_observed
        Persists cluster membership in funder_networks with cluster_id.
        """
        # Ensure atomic networks are up-to-date
        atomic = self.build_atomic_funder_networks()

        # Build graph funders = atomic funders NOT excluded and with >=2 creators
        graph_funders = {
            f: set(v["creators_served"])
            for f, v in atomic.items()
            if not v["excluded_from_clustering"]
        }
        if len(graph_funders) < 2:
            print("[ANALYZER] Not enough non-CEX atomic funders to cluster.")
            return []

        # Pre-filter: only funders with >=2 creators can ever overlap >=2
        funders = [f for f, creators in graph_funders.items() if len(creators) >= 2]
        if len(funders) < 2:
            print("[ANALYZER] Not enough non-CEX multi-target funders to cluster.")
            return []

        # volumes for cluster totals
        funder_volume = {f: float(atomic[f]["total_volume_sol"]) for f in funders}

        uf = UnionFind()
        edges: List[Tuple[str, str, float, int]] = []

        for f in funders:
            uf.find(f)

        # O(n^2) across reduced set (usually small)
        for i in range(len(funders)):
            a = funders[i]
            A = graph_funders[a]
            for j in range(i + 1, len(funders)):
                b = funders[j]
                B = graph_funders[b]
                inter = A.intersection(B)
                overlap = len(inter)
                if overlap == 0:
                    continue
                union = len(A.union(B))
                jaccard = overlap / union if union else 0.0

                if overlap >= MIN_OVERLAP_CREATORS or jaccard >= MIN_JACCARD:
                    uf.union(a, b)
                    edges.append((a, b, jaccard, overlap))

        groups = uf.groups()
        cluster_groups = [g for g in groups.values() if len(g) > 1]  # real clusters only

        clusters: List[FunderCluster] = []
        for idx, g in enumerate(cluster_groups, start=1):
            gset = set(g)
            creators_served: Set[str] = set()
            total_vol = 0.0
            for f in gset:
                creators_served |= graph_funders[f]
                total_vol += funder_volume.get(f, 0.0)

            cluster_id = f"FUNDERS_{idx}"
            cluster_edges = [e for e in edges if (e[0] in gset and e[1] in gset)]
            clusters.append(FunderCluster(
                cluster_id=cluster_id,
                funders=gset,
                creators_served=creators_served,
                total_volume_sol=total_vol,
                edges=cluster_edges,
            ))

        # Persist cluster membership (only clustered funders) to the ARCHIVE DB.
        # funder_networks no longer lives in the hot DB; we ATTACH the archive
        # and write to arch.funder_networks so the hot DB is never refilled.
        conn = _connect(self.archive_db_path, timeout=60)
        try:
            cur = conn.cursor()

            # Optional: clear previous cluster_id assignments for safety (keeps rows, but resets cluster_id)
            cur.execute("UPDATE funder_networks SET cluster_id = NULL WHERE cluster_id IS NOT NULL")

            for cl in clusters:
                for primary in cl.funders:
                    connected = sorted([f for f in cl.funders if f != primary])
                    cur.execute("""
                        INSERT OR REPLACE INTO funder_networks
                        (primary_funder, connected_funders, transfer_chain, creators_served,
                         network_size, total_volume_sol, cluster_id, detected_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        primary,
                        _safe_json(connected),
                        _safe_json([]),
                        _safe_json(sorted(list(cl.creators_served))),
                        len(cl.funders),
                        cl.total_volume_sol,
                        cl.cluster_id,
                    ))

            conn.commit()
            print(f"[ANALYZER] Funder clusters built: {len(clusters)} (non-CEX topology)")
            return clusters
        finally:
            conn.close()

    # =========================================================================
    # METHOD 3: CREATOR CLUSTERS (shared destinations)
    # =========================================================================

    def build_creator_clusters(self) -> List[CreatorCluster]:
        conn = _connect(self.db_path, timeout=60)
        try:
            if not _table_exists(conn, "creator_sol_transfers"):
                print("[ANALYZER] creator_sol_transfers missing; skipping creator clusters")
                return []

            cols = _get_columns(conn, "creator_sol_transfers")
            creator_col = _pick_first_existing(cols, ["creator_address", "creator", "creator_pubkey"])
            dest_col = _pick_first_existing(cols, ["destination_address", "destination", "to_address", "recipient_address"])
            if creator_col is None or dest_col is None:
                print("[ANALYZER] creator_sol_transfers missing creator/dest cols; skipping")
                return []

            cur = conn.cursor()
            creator_to_dests: Dict[str, Set[str]] = defaultdict(set)
            dest_to_creators: Dict[str, Set[str]] = defaultdict(set)

            cur.execute(f"""
                SELECT {creator_col} AS creator, {dest_col} AS dest
                FROM creator_sol_transfers
                WHERE {creator_col} IS NOT NULL AND {dest_col} IS NOT NULL
            """)
            for row in cur.fetchall():
                c = row["creator"]
                d = row["dest"]
                if not c or not d:
                    continue
                if d in IGNORE_ADDRESSES:
                    continue
                creator_to_dests[c].add(d)
                dest_to_creators[d].add(c)

            creators = list(creator_to_dests.keys())
            if len(creators) < 2:
                return []

            pair_shared: Dict[Tuple[str, str], int] = defaultdict(int)
            for dest, cset in dest_to_creators.items():
                c_list = sorted(cset)
                if len(c_list) < 2:
                    continue
                for i in range(len(c_list)):
                    for j in range(i + 1, len(c_list)):
                        pair_shared[(c_list[i], c_list[j])] += 1

            uf = UnionFind()
            for c in creators:
                uf.find(c)

            edges: List[Tuple[str, str, int]] = []
            for (a, b), shared_cnt in pair_shared.items():
                if shared_cnt >= MIN_SHARED_DESTS_FOR_CREATOR_EDGE:
                    uf.union(a, b)
                    edges.append((a, b, shared_cnt))

            groups = uf.groups()
            cluster_groups = [g for g in groups.values() if len(g) > 1]

            clusters: List[CreatorCluster] = []
            for idx, g in enumerate(cluster_groups, start=1):
                gset = set(g)
                dest_counter: Dict[str, int] = defaultdict(int)
                for c in gset:
                    for d in creator_to_dests.get(c, set()):
                        dest_counter[d] += 1
                shared_dests = {d for d, k in dest_counter.items() if k >= 2}
                cluster_edges = [e for e in edges if (e[0] in gset and e[1] in gset)]

                clusters.append(CreatorCluster(
                    cluster_id=f"CREATORS_{idx}",
                    creators=gset,
                    shared_destinations=shared_dests,
                    edges=cluster_edges,
                ))

            # persist summaries
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
                        _safe_json(connected),
                        _safe_json(sorted(list(cl.shared_destinations))),
                        len(cl.creators),
                        risk,
                    ))

            conn.commit()
            print(f"[ANALYZER] Creator clusters built: {len(clusters)}")
            return clusters
        finally:
            conn.close()

    # =========================================================================
    # UNIFIED PER-CREATOR NETWORK (kept; unchanged conceptually)
    # =========================================================================

    def analyze_creator_unified_cluster(self, target_creator: str) -> UnifiedClusterReport:
        conn = _connect(self.db_path, timeout=60)
        try:
            recipient_map = self._load_creator_to_recipients(conn)
            funder_map, funder_amounts, funder_time_map = self._load_creator_to_funders(conn)
            dest_map, dest_time_map = self._load_creator_to_destinations(conn)

            creators_seen: Set[str] = {target_creator}
            funders_seen: Set[str] = set()
            recipients_seen: Set[str] = set()
            dests_seen: Set[str] = set()

            # Reverse indexes
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

            # BFS expansion
            q = deque([target_creator])
            while q:
                c = q.popleft()

                for r in recipient_map.get(c, set()):
                    recipients_seen.add(r)
                    for other in recipient_to_creators.get(r, set()):
                        if other not in creators_seen:
                            creators_seen.add(other)
                            q.append(other)

                for f in funder_map.get(c, set()):
                    funders_seen.add(f)
                    for other in funder_to_creators.get(f, set()):
                        if other not in creators_seen:
                            creators_seen.add(other)
                            q.append(other)

                for d in dest_map.get(c, set()):
                    dests_seen.add(d)
                    for other in dest_to_creators.get(d, set()):
                        if other not in creators_seen:
                            creators_seen.add(other)
                            q.append(other)

            burst = self._compute_burst_metrics(
                target_creator=target_creator,
                creators=creators_seen,
                recipient_time_map=self._load_recipient_times(conn),
                funder_time_map=funder_time_map,
                dest_time_map=dest_time_map,
            )

            score, reasons, risk = self._score_unified_cluster(
                target_creator=target_creator,
                creators=creators_seen,
                funders=funders_seen,
                recipients=recipients_seen,
                destinations=dests_seen,
                recipient_map=recipient_map,
                funder_map=funder_map,
                dest_map=dest_map,
                funder_amounts=funder_amounts,
                burst_metrics=burst,
                conn=conn,
            )

            report = UnifiedClusterReport(
                target_creator=target_creator,
                creators=creators_seen,
                funders=funders_seen,
                recipients=recipients_seen,
                destinations=dests_seen,
                score=score,
                risk_level=risk,
                reasons=reasons,
                burst_metrics=burst,
            )

            self._persist_unified_cluster(conn, report)
            conn.commit()
            return report
        finally:
            conn.close()

    def _persist_unified_cluster(self, conn: sqlite3.Connection, report: UnifiedClusterReport) -> None:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO unified_creator_clusters
            (target_creator, cluster_creators, cluster_funders, cluster_recipients, cluster_destinations,
             score, risk_level, reasons, burst_metrics, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            report.target_creator,
            _safe_json(sorted(list(report.creators))),
            _safe_json(sorted(list(report.funders))),
            _safe_json(sorted(list(report.recipients))),
            _safe_json(sorted(list(report.destinations))),
            report.score,
            report.risk_level,
            _safe_json(report.reasons),
            _safe_json(report.burst_metrics),
        ))

    # =========================================================================
    # LOADERS
    # =========================================================================

    def _load_creator_to_recipients(self, conn: sqlite3.Connection) -> Dict[str, Set[str]]:
        out: Dict[str, Set[str]] = defaultdict(set)
        if not _table_exists(conn, "creator_recipients_unified"):
            return out
        cur = conn.cursor()
        cur.execute("""
            SELECT creator_address, recipient_address
            FROM creator_recipients_unified
            WHERE creator_address IS NOT NULL AND recipient_address IS NOT NULL
        """)
        for c, r in cur.fetchall():
            if r in IGNORE_ADDRESSES:
                continue
            out[c].add(r)
        return out

    def _load_recipient_times(self, conn: sqlite3.Connection) -> Dict[Tuple[str, str], Optional[datetime]]:
        out: Dict[Tuple[str, str], Optional[datetime]] = {}
        if not _table_exists(conn, "creator_recipients_unified"):
            return out
        cols = _get_columns(conn, "creator_recipients_unified")
        tcol = _pick_first_existing(cols, ["last_transfer_time", "updated_at", "created_at"])
        if not tcol:
            return out
        cur = conn.cursor()
        cur.execute(f"""
            SELECT creator_address, recipient_address, {tcol}
            FROM creator_recipients_unified
            WHERE creator_address IS NOT NULL AND recipient_address IS NOT NULL
        """)
        for c, r, t in cur.fetchall():
            out[(c, r)] = _as_dt(t)
        return out

    def _load_creator_to_funders(self, conn: sqlite3.Connection) -> Tuple[
        Dict[str, Set[str]],
        Dict[Tuple[str, str], float],
        Dict[Tuple[str, str], Optional[datetime]]
    ]:
        creator_to_funders: Dict[str, Set[str]] = defaultdict(set)
        amount_map: Dict[Tuple[str, str], float] = defaultdict(float)
        time_map: Dict[Tuple[str, str], Optional[datetime]] = {}

        if not _table_exists(conn, "creator_funders"):
            return creator_to_funders, dict(amount_map), time_map

        cols = _get_columns(conn, "creator_funders")
        amount_col = _pick_first_existing(cols, ["amount_sol", "amount", "sol_amount"]) or "amount_sol"
        time_col = _pick_first_existing(cols, ["funding_time", "block_time", "timestamp", "created_at", "updated_at"])
        is_cex_col = _pick_first_existing(cols, ["is_cex", "is_exchange"])

        cur = conn.cursor()
        if time_col and is_cex_col:
            cur.execute(f"""
                SELECT creator_address, funder_address, COALESCE({amount_col}, 0) AS amount_sol, {time_col}, {is_cex_col}
                FROM creator_funders
                WHERE creator_address IS NOT NULL AND funder_address IS NOT NULL
            """)
            for c, f, amt, t, is_cex in cur.fetchall():
                if f in IGNORE_ADDRESSES:
                    continue
                creator_to_funders[c].add(f)
                try:
                    amount = float(amt or 0.0)
                    if is_cex:
                        amount *= CEX_FUNDER_MULTIPLIER
                    amount_map[(c, f)] += amount  # ✅ accumulate
                except Exception:
                    pass
                time_map[(c, f)] = _as_dt(t)

        elif time_col:
            cur.execute(f"""
                SELECT creator_address, funder_address, COALESCE({amount_col}, 0) AS amount_sol, {time_col}
                FROM creator_funders
                WHERE creator_address IS NOT NULL AND funder_address IS NOT NULL
            """)
            for c, f, amt, t in cur.fetchall():
                if f in IGNORE_ADDRESSES:
                    continue
                creator_to_funders[c].add(f)
                try:
                    amount_map[(c, f)] += float(amt or 0.0)
                except Exception:
                    pass
                time_map[(c, f)] = _as_dt(t)

        elif is_cex_col:
            cur.execute(f"""
                SELECT creator_address, funder_address, COALESCE({amount_col}, 0) AS amount_sol, {is_cex_col}
                FROM creator_funders
                WHERE creator_address IS NOT NULL AND funder_address IS NOT NULL
            """)
            for c, f, amt, is_cex in cur.fetchall():
                if f in IGNORE_ADDRESSES:
                    continue
                creator_to_funders[c].add(f)
                try:
                    amount = float(amt or 0.0)
                    if is_cex:
                        amount *= CEX_FUNDER_MULTIPLIER
                    amount_map[(c, f)] += amount
                except Exception:
                    pass

        else:
            cur.execute(f"""
                SELECT creator_address, funder_address, COALESCE({amount_col}, 0) AS amount_sol
                FROM creator_funders
                WHERE creator_address IS NOT NULL AND funder_address IS NOT NULL
            """)
            for c, f, amt in cur.fetchall():
                if f in IGNORE_ADDRESSES:
                    continue
                creator_to_funders[c].add(f)
                try:
                    amount_map[(c, f)] += float(amt or 0.0)
                except Exception:
                    pass

        return creator_to_funders, dict(amount_map), time_map

    def _load_creator_to_destinations(self, conn: sqlite3.Connection) -> Tuple[Dict[str, Set[str]], Dict[Tuple[str, str], Optional[datetime]]]:
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
        if time_col:
            cur.execute(f"""
                SELECT {creator_col} AS creator, {dest_col} AS dest, {time_col}
                FROM creator_sol_transfers
                WHERE {creator_col} IS NOT NULL AND {dest_col} IS NOT NULL
            """)
            for c, d, t in cur.fetchall():
                if d in IGNORE_ADDRESSES:
                    continue
                creator_to_dests[c].add(d)
                time_map[(c, d)] = _as_dt(t)
        else:
            cur.execute(f"""
                SELECT {creator_col} AS creator, {dest_col} AS dest
                FROM creator_sol_transfers
                WHERE {creator_col} IS NOT NULL AND {dest_col} IS NOT NULL
            """)
            for c, d in cur.fetchall():
                if d in IGNORE_ADDRESSES:
                    continue
                creator_to_dests[c].add(d)

        return creator_to_dests, time_map

    # =========================================================================
    # BURST METRICS (optional)
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
        windows = [3600, 6 * 3600, 24 * 3600]

        def key(w: int) -> str:
            return "1h" if w == 3600 else "6h" if w == 6 * 3600 else "24h"

        metrics = {"recipient": {}, "funder": {}, "destination": {}}

        if recipient_time_map:
            for w in windows:
                cutoff = now.timestamp() - w
                seen = {c for (c, _r), t in recipient_time_map.items()
                        if c != target_creator and c in creators and t and t.timestamp() >= cutoff}
                metrics["recipient"][key(w)] = len(seen)

        if funder_time_map:
            for w in windows:
                cutoff = now.timestamp() - w
                seen = {c for (c, _f), t in funder_time_map.items()
                        if c != target_creator and c in creators and t and t.timestamp() >= cutoff}
                metrics["funder"][key(w)] = len(seen)

        if dest_time_map:
            for w in windows:
                cutoff = now.timestamp() - w
                seen = {c for (c, _d), t in dest_time_map.items()
                        if c != target_creator and c in creators and t and t.timestamp() >= cutoff}
                metrics["destination"][key(w)] = len(seen)

        return metrics

    # =========================================================================
    # SCORING (kept simple; you can tune)
    # =========================================================================

    def _load_is_cex_funders(self, conn: sqlite3.Connection) -> Dict[str, bool]:
        is_cex_map: Dict[str, bool] = {}
        if not _table_exists(conn, "creator_funders"):
            return is_cex_map
        cols = _get_columns(conn, "creator_funders")
        is_cex_col = _pick_first_existing(cols, ["is_cex", "is_exchange"])
        if not is_cex_col:
            return is_cex_map
        cur = conn.cursor()
        cur.execute(f"""
            SELECT DISTINCT funder_address, {is_cex_col}
            FROM creator_funders
            WHERE funder_address IS NOT NULL
        """)
        for f, is_cex in cur.fetchall():
            is_cex_map[f] = bool(is_cex)
        return is_cex_map

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
        score = 0.0
        reasons: List[str] = []

        creator_count = len(creators)
        if creator_count > 1:
            score += 2.0 * (creator_count - 1)
            reasons.append(f"connected_creators({creator_count})")

        # shared recipients within component
        rc = defaultdict(int)
        for c in creators:
            for r in recipient_map.get(c, set()):
                rc[r] += 1
        shared_r = [r for r, k in rc.items() if k >= 2]
        if shared_r:
            score += 2.5 * min(len(shared_r), 10)
            reasons.append(f"shared_recipient_hubs({len(shared_r)})")

        # shared funders within component (weighted by CEX)
        is_cex_map = self._load_is_cex_funders(conn)
        fc = defaultdict(int)
        fw = defaultdict(float)
        for c in creators:
            for f in funder_map.get(c, set()):
                fc[f] += 1
                fw[f] += (CEX_FUNDER_MULTIPLIER if is_cex_map.get(f, False) else 1.0)

        shared_f = [f for f, k in fc.items() if k >= 2]
        if shared_f:
            weighted = min(sum(fw[f] for f in shared_f), 10.0)
            score += 2.0 * weighted
            reasons.append(f"shared_funders({len(shared_f)}, weighted={weighted:.1f})")

        # shared destinations within component
        dc = defaultdict(int)
        for c in creators:
            for d in dest_map.get(c, set()):
                dc[d] += 1
        shared_d = [d for d, k in dc.items() if k >= 2]
        if shared_d:
            score += 2.0 * min(len(shared_d), 10)
            reasons.append(f"shared_destinations({len(shared_d)})")

        # total funding (best-effort)
        total_funding = 0.0
        for c in creators:
            for f in funder_map.get(c, set()):
                total_funding += funder_amounts.get((c, f), 0.0)
        if total_funding >= 500:
            score += 1.5
            reasons.append(f"high_total_funding({total_funding:.2f}SOL)")

        # burst
        if any(burst_metrics.get(t, {}).get("1h", 0) >= 3 for t in ("recipient", "funder", "destination")):
            score += 1.5
            reasons.append("burst_activity")

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
    # PIPELINES
    # =========================================================================

    def analyze_new_token(self, creator_address: str) -> Dict:
        print(f"\n[ANALYZER] ===== Starting Cluster Analysis for {creator_address[:8]}... =====")
        results = {
            "timestamp": _utcnow().isoformat(),
            "creator": creator_address,
            "recipient_coordinators": 0,
            "funder_clusters": 0,
            "creator_clusters": 0,
            "atomic_funder_networks": 0,
            "unified_cluster": {},
            "total_flags": 0,
        }

        print("[ANALYZER] Phase 1: Detecting recipient hubs...")
        coordinators = self.detect_network_coordinators()
        results["recipient_coordinators"] = len(coordinators)
        print(f"  ✓ Found {len(coordinators)} recipient hubs")

        print("[ANALYZER] Phase 2: Building atomic funder networks...")
        atomic = self.build_atomic_funder_networks()
        results["atomic_funder_networks"] = len(atomic)
        print(f"  ✓ Built {len(atomic)} atomic networks")

        print("[ANALYZER] Phase 3: Building funder clusters (non-CEX only)...")
        funder_clusters = self.build_funder_clusters()
        results["funder_clusters"] = len(funder_clusters)
        print(f"  ✓ Found {len(funder_clusters)} funder clusters")

        print("[ANALYZER] Phase 4: Building creator clusters...")
        creator_clusters = self.build_creator_clusters()
        results["creator_clusters"] = len(creator_clusters)
        print(f"  ✓ Found {len(creator_clusters)} creator clusters")

        print("[ANALYZER] Phase 5: Unified cluster scoring...")
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
            "atomic_funder_networks": 0,
            "funder_clusters": 0,
            "creator_clusters": 0,
        }

        results["recipient_coordinators"] = len(self.detect_network_coordinators())
        atomic = self.build_atomic_funder_networks()
        results["atomic_funder_networks"] = len(atomic)
        results["funder_clusters"] = len(self.build_funder_clusters())
        results["creator_clusters"] = len(self.build_creator_clusters())

        print(f"\n[ANALYZER] Full Analysis Complete")
        print(f"  Recipient Hubs: {results['recipient_coordinators']}")
        print(f"  Atomic Funder Networks: {results['atomic_funder_networks']}")
        print(f"  Funder Clusters (non-CEX): {results['funder_clusters']}")
        print(f"  Creator Clusters: {results['creator_clusters']}")
        return results


# =========================================================================
# MODULE-LEVEL CONVENIENCE
# =========================================================================

_analyzer: Optional[CrossFundingClusterAnalyzer] = None


def get_analyzer(db_path: str = DB_PATH) -> CrossFundingClusterAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = CrossFundingClusterAnalyzer(db_path)
    return _analyzer


def analyze_funding_clusters_for_token(creator_address: str) -> Dict:
    """Analyze clusters for a single token creator."""
    return get_analyzer().analyze_new_token(creator_address)


def run_full_cluster_analysis() -> Dict:
    """Run full analysis across all tokens."""
    return get_analyzer().run_full_analysis()


if __name__ == "__main__":
    analyzer = CrossFundingClusterAnalyzer()
    results = analyzer.run_full_analysis()
    print(json.dumps(results, indent=2))
