"""
Funding Fingerprints & Cluster Caching - Additional 5-10x Optimization

Reduces wallet scanning by classifying wallets from early pages and applying
skip/shallow policies based on funding patterns. No extra API calls required.

Expected additional savings: 30-70% fewer scans on new wallets
Combined with global cache: 5-15 credits per token (vs 150-300 before)
"""

import sqlite3
import hashlib
import time
from typing import Dict, Optional, Tuple, List
from collections import Counter
import logging

logger = logging.getLogger(__name__)

DB_PATH = "flex_complete_database.db"

# Cluster creation threshold: create cluster after N wallets with same fingerprint
CLUSTER_CREATION_THRESHOLD = 5

# Confidence thresholds for automatic classification
CONFIDENCE_THRESHOLD_HIGH = 0.9
CONFIDENCE_THRESHOLD_MEDIUM = 0.7

# Skip policy rules
class SkipPolicy:
    SKIP = "skip"        # Do not scan at all
    SHALLOW = "shallow"  # Scan max 1 page
    NORMAL = "normal"    # Scan as usual


# ============================================================================
# SCHEMA INITIALIZATION
# ============================================================================

def migrate_fingerprint_schema(conn: sqlite3.Connection) -> None:
    """
    Create fingerprint and cluster tables.

    Idempotent - safe to call multiple times.
    """
    cursor = conn.cursor()

    # Wallet fingerprints table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wallet_fingerprints (
            address TEXT PRIMARY KEY,
            fingerprint_hash TEXT NOT NULL,
            fingerprint_version INTEGER DEFAULT 1,
            cluster_id INTEGER,
            wallet_type TEXT DEFAULT 'unknown',
            confidence REAL DEFAULT 0.0,
            computed_at INTEGER,
            sample_txs INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Fingerprint clusters table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fingerprint_clusters (
            cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint_hash TEXT NOT NULL UNIQUE,
            wallet_type TEXT DEFAULT 'unknown',
            skip_policy TEXT DEFAULT 'normal',
            cluster_confidence REAL DEFAULT 0.0,
            wallet_count INTEGER DEFAULT 0,
            created_at INTEGER,
            updated_at INTEGER
        )
    """)

    # Indexes for fast lookups
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_wallet_fingerprints_hash
        ON wallet_fingerprints(fingerprint_hash)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_wallet_fingerprints_cluster
        ON wallet_fingerprints(cluster_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_fingerprint_clusters_hash
        ON fingerprint_clusters(fingerprint_hash)
    """)

    conn.commit()


# ============================================================================
# FINGERPRINT COMPUTATION (ZERO EXTRA API CALLS)
# ============================================================================

def compute_wallet_fingerprint(
    transactions: List[Dict],
    cex_addresses: set,
    infra_addresses: set
) -> Tuple[str, str, float, Dict]:
    """
    Compute fingerprint hash from already-fetched transactions.

    This runs on data you've ALREADY FETCHED, so zero extra API calls.

    Args:
        transactions: List of transaction objects from Helius
        cex_addresses: Set of known CEX wallet addresses
        infra_addresses: Set of known infrastructure addresses

    Returns:
        (fingerprint_hash, wallet_type, confidence, stats_dict)
        Where:
        - fingerprint_hash: Stable hash of wallet behavior pattern
        - wallet_type: 'cex' | 'bot' | 'aggregator' | 'unknown'
        - confidence: 0.0-1.0 confidence in classification
        - stats_dict: Raw stats (for debugging/analysis)
    """
    if not transactions:
        return None, 'unknown', 0.0, {}

    # Extract transfer information
    total_in_sol = 0.0
    total_out_sol = 0.0
    counterparties = Counter()  # address -> total SOL volume
    cex_volume = 0.0
    infra_volume = 0.0
    total_volume = 0.0

    for tx in transactions:
        native_transfers = tx.get('nativeTransfers', [])
        for transfer in native_transfers:
            amount_sol = transfer.get('amount', 0) / 1_000_000_000
            frm = transfer.get('from', '')
            to = transfer.get('to', '')

            total_volume += amount_sol

            # Track counterparties
            if frm and to:
                counterparties[frm] += amount_sol
                counterparties[to] += amount_sol

                # Track CEX/infra involvement
                if frm in cex_addresses or to in cex_addresses:
                    cex_volume += amount_sol
                if frm in infra_addresses or to in infra_addresses:
                    infra_volume += amount_sol

    # Compute statistics
    distinct_counterparties = len(counterparties)
    top_5_counterparties = counterparties.most_common(5)

    # Shares (0.0-1.0)
    cex_share = cex_volume / max(total_volume, 1)
    infra_share = infra_volume / max(total_volume, 1)

    # Bucketing for stable hashing (reduce noise)
    cex_share_bucket = _bucket_value(cex_share, buckets=[0, 0.2, 0.5, 0.8, 1.0])
    infra_share_bucket = _bucket_value(infra_share, buckets=[0, 0.2, 0.5, 0.8, 1.0])
    counterparties_bucket = _bucket_value(
        distinct_counterparties,
        buckets=[0, 5, 20, 50, 100, 200, 500, 1000]
    )

    # Top 5 counterparties (normalized to buckets for stability)
    top_5_normalized = [
        (_normalize_address(addr), _bucket_value(vol / max(total_volume, 1), buckets=[0, 0.01, 0.05, 0.1, 0.2, 0.5, 1.0]))
        for addr, vol in top_5_counterparties
    ]

    # Build fingerprint hash from bucketed stats
    fingerprint_components = [
        f"cex:{cex_share_bucket}",
        f"infra:{infra_share_bucket}",
        f"peers:{counterparties_bucket}",
        f"top5:{'|'.join([f'{a[:4]}:{b}' for a, b in top_5_normalized])}",
    ]

    fingerprint_hash = hashlib.sha256(
        '|'.join(fingerprint_components).encode()
    ).hexdigest()[:16]  # 16-char hash

    # Classify wallet type and confidence
    wallet_type, confidence = _classify_from_stats(
        cex_share=cex_share,
        infra_share=infra_share,
        distinct_peers=distinct_counterparties,
        total_volume=total_volume
    )

    stats = {
        'total_volume_sol': total_volume,
        'total_in_sol': total_in_sol,
        'total_out_sol': total_out_sol,
        'distinct_counterparties': distinct_counterparties,
        'cex_share': cex_share,
        'infra_share': infra_share,
        'top_5_counterparties': top_5_counterparties,
        'sample_size': len(transactions)
    }

    logger.debug(
        f"[FINGERPRINT] Computed: hash={fingerprint_hash} | type={wallet_type} | conf={confidence:.2f} | "
        f"cex={cex_share:.2f} | infra={infra_share:.2f} | peers={distinct_counterparties}"
    )

    return fingerprint_hash, wallet_type, confidence, stats


def _bucket_value(value: float, buckets: List[float]) -> str:
    """
    Map value to nearest bucket for stable hashing.

    Example: buckets=[0, 0.5, 1.0], value=0.75 -> "0.5"
    """
    for i in range(len(buckets) - 1):
        if buckets[i] <= value < buckets[i + 1]:
            return str(buckets[i])
    return str(buckets[-1])


def _normalize_address(address: str) -> str:
    """Normalize address for hashing (first 8 chars)"""
    return address[:8] if address else "unknown"


def _classify_from_stats(
    cex_share: float,
    infra_share: float,
    distinct_peers: int,
    total_volume: float
) -> Tuple[str, float]:
    """
    Classify wallet type from funding statistics.

    Returns:
        (wallet_type, confidence)
    """
    # High CEX involvement
    if cex_share > 0.8:
        return 'cex', 0.95

    # High infrastructure involvement
    if infra_share > 0.8:
        return 'aggregator', 0.95

    # Many distinct peers = likely router/aggregator
    if distinct_peers > 200:
        return 'bot', 0.85

    # Low volume but many peers = bot activity
    if total_volume < 10 and distinct_peers > 50:
        return 'bot', 0.75

    # Default to unknown with low confidence
    return 'unknown', 0.0


# ============================================================================
# FINGERPRINT PERSISTENCE
# ============================================================================

def upsert_wallet_fingerprint(
    conn: sqlite3.Connection,
    address: str,
    fingerprint_hash: str,
    wallet_type: str,
    confidence: float,
    sample_txs: int = 0,
    cluster_id: Optional[int] = None,
    fingerprint_version: int = 1
) -> int:
    """
    Store or update a wallet fingerprint.

    Returns:
        cluster_id if assigned, else None
    """
    cursor = conn.cursor()
    current_time = int(time.time())

    cursor.execute("""
        INSERT OR REPLACE INTO wallet_fingerprints
        (address, fingerprint_hash, fingerprint_version, cluster_id, wallet_type, confidence,
         computed_at, sample_txs, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (address, fingerprint_hash, fingerprint_version, cluster_id, wallet_type, confidence, current_time, sample_txs))

    conn.commit()

    # Try to auto-create cluster if not exists
    if confidence >= CONFIDENCE_THRESHOLD_MEDIUM:
        assigned_cluster = maybe_create_cluster(
            conn,
            fingerprint_hash,
            wallet_type,
            confidence
        )
        if assigned_cluster:
            cluster_id = assigned_cluster

    return cluster_id


def get_fingerprint_by_address(conn: sqlite3.Connection, address: str) -> Optional[Dict]:
    """
    Get cached fingerprint for a wallet.

    Returns:
        {
            'fingerprint_hash': str,
            'cluster_id': int or None,
            'wallet_type': str,
            'confidence': float
        }
        or None if not cached
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT fingerprint_hash, cluster_id, wallet_type, confidence
        FROM wallet_fingerprints
        WHERE address = ?
    """, (address,))

    row = cursor.fetchone()
    if not row:
        return None

    return {
        'fingerprint_hash': row[0],
        'cluster_id': row[1],
        'wallet_type': row[2],
        'confidence': row[3]
    }


def get_cluster_by_hash(conn: sqlite3.Connection, fingerprint_hash: str) -> Optional[Dict]:
    """
    Get cluster policy for a fingerprint hash.

    Returns:
        {
            'cluster_id': int,
            'skip_policy': str ('skip' | 'shallow' | 'normal'),
            'wallet_type': str,
            'wallet_count': int
        }
        or None if no cluster exists
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cluster_id, skip_policy, wallet_type, wallet_count
        FROM fingerprint_clusters
        WHERE fingerprint_hash = ?
    """, (fingerprint_hash,))

    row = cursor.fetchone()
    if not row:
        return None

    return {
        'cluster_id': row[0],
        'skip_policy': row[1],
        'wallet_type': row[2],
        'wallet_count': row[3]
    }


def maybe_create_cluster(
    conn: sqlite3.Connection,
    fingerprint_hash: str,
    wallet_type: str,
    confidence: float
) -> Optional[int]:
    """
    Create a cluster if fingerprint_hash has N+ wallets.

    Safety rule: skip_policy='skip' only when cluster_wallet_count >= 20
    to prevent accidental misclassification from early data.

    Returns:
        cluster_id if created/exists, else None
    """
    cursor = conn.cursor()

    # Check if cluster already exists
    cursor.execute("""
        SELECT cluster_id FROM fingerprint_clusters
        WHERE fingerprint_hash = ?
    """, (fingerprint_hash,))

    existing = cursor.fetchone()
    if existing:
        return existing[0]

    # Count wallets with this fingerprint
    cursor.execute("""
        SELECT COUNT(*) FROM wallet_fingerprints
        WHERE fingerprint_hash = ?
    """, (fingerprint_hash,))

    wallet_count = cursor.fetchone()[0] or 0

    # Create cluster if threshold met
    if wallet_count >= CLUSTER_CREATION_THRESHOLD:
        # Determine skip policy based on confidence, type, and safety rule
        # SAFETY: Never allow skip_policy='skip' until cluster_wallet_count >= 20
        if (confidence >= CONFIDENCE_THRESHOLD_HIGH and 
            wallet_type in {'cex', 'aggregator'} and
            wallet_count >= 20):  # Safety rule: require 20+ wallets for skip
            skip_policy = SkipPolicy.SKIP
        elif confidence >= CONFIDENCE_THRESHOLD_MEDIUM:
            skip_policy = SkipPolicy.SHALLOW
        else:
            skip_policy = SkipPolicy.NORMAL

        current_time = int(time.time())

        cursor.execute("""
            INSERT INTO fingerprint_clusters
            (fingerprint_hash, wallet_type, skip_policy, cluster_confidence, wallet_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (fingerprint_hash, wallet_type, skip_policy, confidence, wallet_count, current_time, current_time))

        conn.commit()

        cluster_id = cursor.lastrowid

        logger.info(
            f"[FINGERPRINT_CLUSTER] Created cluster {cluster_id} | "
            f"hash={fingerprint_hash[:8]}... | type={wallet_type} | "
            f"policy={skip_policy} (conf={confidence:.2f}, count={wallet_count}) | "
            f"wallets={wallet_count}"
        )

        return cluster_id

    return None


# ============================================================================
# INTEGRATION: SCAN DEPTH CONTROL
# ============================================================================

def get_scan_policy(conn: sqlite3.Connection, address: str) -> Tuple[str, Optional[int]]:
    """
    Determine scan policy for a wallet based on fingerprint/cluster.

    Returns:
        (skip_policy, cluster_id)
        Where skip_policy is 'skip' | 'shallow' | 'normal'
    """
    # Check if wallet already has fingerprint
    fingerprint = get_fingerprint_by_address(conn, address)
    if fingerprint:
        # Look up cluster policy
        cluster = get_cluster_by_hash(conn, fingerprint['fingerprint_hash'])
        if cluster:
            return cluster['skip_policy'], cluster['cluster_id']

    return SkipPolicy.NORMAL, None


async def should_skip_wallet_scan(
    conn: sqlite3.Connection,
    address: str
) -> bool:
    """
    Return True if wallet should be completely skipped based on fingerprint.
    """
    skip_policy, _ = get_scan_policy(conn, address)
    return skip_policy == SkipPolicy.SKIP


def get_max_pages_for_wallet(
    conn: sqlite3.Connection,
    address: str,
    default_max_pages: int = 50
) -> int:
    """
    Get max pages to scan for a wallet based on fingerprint policy.

    Policy rules:
    - 'skip': return 0 (don't scan)
    - 'shallow': return 1 (scan only first page)
    - 'normal': return default_max_pages
    """
    skip_policy, _ = get_scan_policy(conn, address)

    if skip_policy == SkipPolicy.SKIP:
        return 0
    elif skip_policy == SkipPolicy.SHALLOW:
        return 1
    else:
        return default_max_pages


# ============================================================================
# INTEGRATION: FINGERPRINT AFTER FIRST PAGE
# ============================================================================

async def apply_fingerprint_after_first_page(
    conn: sqlite3.Connection,
    address: str,
    transactions: List[Dict],
    cex_addresses: set,
    infra_addresses: set,
    fingerprint_version: int = 1
) -> Dict:
    """
    Compute and store fingerprint after first page fetch.

    Call this after fetching the first page of transactions during a wallet scan.

    Returns:
        {
            'fingerprint_hash': str,
            'wallet_type': str,
            'confidence': float,
            'skip_policy': str,
            'should_stop_scanning': bool
        }
    """
    if not transactions:
        return {
            'fingerprint_hash': None,
            'skip_policy': SkipPolicy.NORMAL,
            'should_stop_scanning': False
        }

    # Compute fingerprint from already-fetched transactions
    fingerprint_hash, wallet_type, confidence, stats = compute_wallet_fingerprint(
        transactions,
        cex_addresses,
        infra_addresses
    )

    if not fingerprint_hash:
        return {
            'fingerprint_hash': None,
            'skip_policy': SkipPolicy.NORMAL,
            'should_stop_scanning': False
        }

    # Store fingerprint with version
    cluster_id = upsert_wallet_fingerprint(
        conn,
        address,
        fingerprint_hash,
        wallet_type,
        confidence,
        sample_txs=len(transactions),
        fingerprint_version=fingerprint_version
    )

    # Get policy
    cluster = get_cluster_by_hash(conn, fingerprint_hash)
    skip_policy = cluster['skip_policy'] if cluster else SkipPolicy.NORMAL

    should_stop = skip_policy == SkipPolicy.SKIP

    logger.debug(
        f"[FINGERPRINT_INTEGRATION] {address[:8]}... | "
        f"type={wallet_type} | policy={skip_policy} | stop={should_stop}"
    )

    return {
        'fingerprint_hash': fingerprint_hash,
        'wallet_type': wallet_type,
        'confidence': confidence,
        'skip_policy': skip_policy,
        'cluster_id': cluster_id,
        'should_stop_scanning': should_stop
    }


# ============================================================================
# TELEMETRY & ANALYSIS
# ============================================================================

def get_fingerprint_stats(conn: sqlite3.Connection, since_hours: int = 24) -> Dict:
    """
    Get fingerprint matching and skip statistics.

    Returns:
        {
            'total_fingerprints': int,
            'total_clusters': int,
            'skip_policy_skip_count': int,
            'skip_policy_shallow_count': int,
            'skip_policy_normal_count': int,
            'avg_cluster_size': float,
            'largest_cluster_size': int
        }
    """
    cursor = conn.cursor()

    # Total fingerprints
    cursor.execute("SELECT COUNT(*) FROM wallet_fingerprints")
    total_fp = cursor.fetchone()[0] or 0

    # Total clusters
    cursor.execute("SELECT COUNT(*) FROM fingerprint_clusters")
    total_clusters = cursor.fetchone()[0] or 0

    # Skip policies
    cursor.execute("""
        SELECT skip_policy, COUNT(*) as count
        FROM fingerprint_clusters
        GROUP BY skip_policy
    """)

    policies = {}
    total_in_clusters = 0
    for policy, count in cursor.fetchall():
        policies[policy] = count
        total_in_clusters += count

    # Cluster sizes
    cursor.execute("""
        SELECT AVG(wallet_count), MAX(wallet_count)
        FROM fingerprint_clusters
    """)

    row = cursor.fetchone()
    avg_size = row[0] or 0
    max_size = row[1] or 0

    return {
        'total_fingerprints': total_fp,
        'total_clusters': total_clusters,
        'skip_policy_skip': policies.get(SkipPolicy.SKIP, 0),
        'skip_policy_shallow': policies.get(SkipPolicy.SHALLOW, 0),
        'skip_policy_normal': policies.get(SkipPolicy.NORMAL, 0),
        'wallets_in_clusters': total_in_clusters,
        'avg_cluster_size': float(avg_size),
        'largest_cluster_size': max_size
    }


def estimate_scan_reduction(conn: sqlite3.Connection) -> Dict:
    """
    Estimate scan reduction from fingerprinting.

    Returns:
        {
            'wallets_skipped_by_fingerprint': int,
            'scans_avoided': int,
            'pages_avoided_estimate': int,
            'estimated_credits_saved': int
        }
    """
    stats = get_fingerprint_stats(conn)

    # Estimate pages avoided
    skip_count = stats['skip_policy_skip']
    shallow_count = stats['skip_policy_shallow']

    # Assume skipped wallets would have needed ~2 pages each
    pages_from_skip = skip_count * 2

    # Assume shallow wallets would have needed ~2 pages, now only need 1
    pages_from_shallow = shallow_count * 1

    total_pages_avoided = pages_from_skip + pages_from_shallow
    estimated_credits_saved = total_pages_avoided * 100

    return {
        'wallets_skipped_by_fingerprint': skip_count,
        'wallets_shallowed_by_fingerprint': shallow_count,
        'total_scan_reductions': skip_count + shallow_count,
        'pages_avoided_estimate': total_pages_avoided,
        'estimated_credits_saved': estimated_credits_saved
    }
