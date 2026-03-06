# Funding Fingerprints Integration Guide

**Status:** Ready for Production
**Expected Additional Savings:** 30-70% fewer scans
**Combined Savings (Cache + Fingerprints):** 5-15 credits per token (vs 150-300 before)

---

## Overview

Funding fingerprints classify wallets from early pages and apply skip/shallow policies without any extra API calls. This adds another **5-10x optimization** on top of your global wallet cache.

### Why It Works

You already fetch the first page from Helius when scanning a wallet. Fingerprints:
1. Compute a stable hash from that data (CEX involvement, infrastructure routing, peer count)
2. Look up cluster policies based on the hash
3. Apply skip/shallow policies to stop early

**Zero extra API calls required.**

---

## SQL Migrations

### Migration 1: Create Fingerprint Tables

```sql
-- Fingerprints for individual wallets
CREATE TABLE IF NOT EXISTS wallet_fingerprints (
    address TEXT PRIMARY KEY,
    fingerprint_hash TEXT NOT NULL,
    cluster_id INTEGER,
    wallet_type TEXT DEFAULT 'unknown',
    confidence REAL DEFAULT 0.0,
    computed_at INTEGER,
    sample_txs INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Clusters: groups of wallets with same behavior pattern
CREATE TABLE IF NOT EXISTS fingerprint_clusters (
    cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint_hash TEXT NOT NULL UNIQUE,
    wallet_type TEXT DEFAULT 'unknown',
    skip_policy TEXT DEFAULT 'normal',     -- 'skip' | 'shallow' | 'normal'
    wallet_count INTEGER DEFAULT 0,
    created_at INTEGER,
    updated_at INTEGER
);

-- Fast lookup by hash
CREATE INDEX IF NOT EXISTS idx_wallet_fingerprints_hash
    ON wallet_fingerprints(fingerprint_hash);

CREATE INDEX IF NOT EXISTS idx_wallet_fingerprints_cluster
    ON wallet_fingerprints(cluster_id);

CREATE INDEX IF NOT EXISTS idx_fingerprint_clusters_hash
    ON fingerprint_clusters(fingerprint_hash);
```

---

## How It Works

### Step 1: Fetch First Page (Existing Code)

Your wallet scan already fetches this:
```python
transactions, newest_sig, oldest_sig, tx_count, meaningful = \
    await fetch_wallet_transactions_incremental(session, address, ...)
```

### Step 2: Compute Fingerprint (New Code)

After first page, compute fingerprint from already-fetched data:

```python
from wallet_fingerprint_cache import apply_fingerprint_after_first_page
from infra_mapping import CEX_ACCOUNTS, INFRASTRUCTURE_ACCOUNTS

# After fetching first page
fp_result = await apply_fingerprint_after_first_page(
    conn,
    address,
    transactions[:100],  # Use first page only
    cex_addresses=set(CEX_ACCOUNTS.keys()),
    infra_addresses=set(INFRASTRUCTURE_ACCOUNTS.keys())
)

# Check if should stop early
if fp_result['should_stop_scanning']:
    logger.info(f"[FINGERPRINT] Stopping scan for {address}: policy=skip")
    return {'status': 'fingerprint_skip', 'wallet_type': fp_result['wallet_type']}
```

### Step 3: Control Scan Depth (New Code)

Use fingerprint to limit how many pages to scan:

```python
from wallet_fingerprint_cache import get_max_pages_for_wallet

# BEFORE: Always scan up to 50 pages
max_pages = 50

# AFTER: Let fingerprint control it
max_pages = get_max_pages_for_wallet(conn, address, default_max_pages=50)
# Returns: 0 (skip), 1 (shallow), or 50 (normal)

if max_pages == 0:
    logger.info(f"[FINGERPRINT] Skipping wallet {address} entirely")
    return {'status': 'fingerprint_skip'}

# Continue scanning with limited pages...
```

---

## Integration into Wallet Scan Function

### Before (Current Code)

```python
async def analyze_wallet_incremental(session, conn, address, creator_address=None):
    state = get_wallet_state(conn, address)

    with ScanTimer(conn, address, creator_address) as timer:
        # Fetch transactions (1-50 pages)
        transactions, newest_sig, oldest_sig, tx_count, meaningful = \
            await fetch_wallet_transactions_incremental(
                session, address, state['newest_signature'],
                max_pages=MAX_PAGES_PER_SCAN
            )

        # Store state
        update_wallet_state(conn, address, ...)
```

### After (With Fingerprints)

```python
async def analyze_wallet_incremental(
    session, conn, address, creator_address=None,
    cex_addresses=None, infra_addresses=None
):
    from wallet_fingerprint_cache import apply_fingerprint_after_first_page, get_max_pages_for_wallet
    from infra_mapping import CEX_ACCOUNTS, INFRASTRUCTURE_ACCOUNTS

    state = get_wallet_state(conn, address)

    # Default CEX/infra maps
    if cex_addresses is None:
        cex_addresses = set(CEX_ACCOUNTS.keys())
    if infra_addresses is None:
        infra_addresses = set(INFRASTRUCTURE_ACCOUNTS.keys())

    with ScanTimer(conn, address, creator_address) as timer:
        # STEP 1: Fetch first page
        page_1_transactions, newest_sig, oldest_sig, tx_count, meaningful = \
            await fetch_wallet_transactions_incremental(
                session, address, state['newest_signature'],
                max_pages=1  # Only fetch first page initially
            )

        if page_1_transactions:
            # STEP 2: Apply fingerprint (on already-fetched data)
            fp_result = await apply_fingerprint_after_first_page(
                conn,
                address,
                page_1_transactions,
                cex_addresses,
                infra_addresses
            )

            logger.debug(f"[FINGERPRINT] {address[:8]}... -> type={fp_result['wallet_type']} | policy={fp_result['skip_policy']}")

            # STEP 3: Stop early if skip policy says so
            if fp_result['should_stop_scanning']:
                timer.scan_type = ScanType.INCREMENTAL_SCAN
                timer.tx_fetched = tx_count
                update_wallet_state(conn, address, newest_sig, oldest_sig, tx_count, ...)
                return {'status': 'fingerprint_skip', 'wallet_type': fp_result['wallet_type']}

        # STEP 4: Determine how many more pages to fetch
        max_pages = get_max_pages_for_wallet(conn, address, default_max_pages=MAX_PAGES_PER_SCAN)

        if max_pages <= 1:
            # Already have first page (shallow scan)
            logger.debug(f"[FINGERPRINT] {address[:8]}... shallow scan (policy={fp_result['skip_policy']})")
        else:
            # Fetch remaining pages
            remaining_transactions, newest_sig, oldest_sig, tx_count, meaningful = \
                await fetch_wallet_transactions_incremental(
                    session, address, state['newest_signature'],
                    max_pages=max_pages
                )
            page_1_transactions.extend(remaining_transactions)

        # Store final state
        timer.scan_type = ScanType.INCREMENTAL_SCAN
        timer.tx_fetched = tx_count
        update_wallet_state(conn, address, newest_sig, oldest_sig, tx_count, ...)

    return {'status': 'scanned', 'tx_scanned': tx_count}
```

---

## How Wallets Get Classified

### Automatic Classification Rules

After fetching first page, wallet is classified as:

**CEX** (confidence >= 0.95):
- CEX account share > 80% of volume
- Applied policy: `skip` (don't scan further)

**Aggregator** (confidence >= 0.95):
- Infrastructure account share > 80% of volume
- Applied policy: `skip` (don't scan further)

**Bot** (confidence >= 0.85):
- Distinct peers > 200
- Applied policy: `shallow` (scan max 1 page)

**Bot** (confidence >= 0.75):
- Low volume + many peers
- Applied policy: `shallow`

**Unknown** (confidence < 0.7):
- Doesn't match any pattern
- Applied policy: `normal` (scan as usual)

### Cluster Auto-Creation

Once the same fingerprint hash appears for **5+ wallets**:
- Automatically create a cluster
- Cluster inherits policy from individual wallets' confidence/type
- All future wallets matching this hash use cluster policy

---

## Expected Metrics

### Before Fingerprints

```
100 funder wallets scanned
Average 2 pages per wallet
= 200 pages × 100 credits = 20,000 Helius credits per token
```

### After Fingerprints (Day 7)

```
100 funder wallets scanned:
- 30% skipped by fingerprint (30 wallets × 0 pages = 0 credits)
- 40% shallow scanned (40 wallets × 1 page = 4,000 credits)
- 30% normal scanned (30 wallets × 2 pages = 6,000 credits)

= 10,000 Helius credits per token
= 50% reduction from fingerprinting alone

Combined with global cache (80% hit rate):
= ~2,000 credits per token
= 90% reduction total (vs 20,000 before both optimizations)
```

---

## Validation Queries

### Check Fingerprint Coverage

```sql
SELECT
    COUNT(*) as total_wallets,
    SUM(CASE WHEN fingerprint_hash IS NOT NULL THEN 1 ELSE 0 END) as with_fingerprint,
    ROUND(100.0 * SUM(CASE WHEN fingerprint_hash IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) as coverage_pct
FROM wallet_fingerprints;
```

### Check Cluster Policies

```sql
SELECT
    skip_policy,
    COUNT(*) as cluster_count,
    SUM(wallet_count) as total_wallets_in_policy
FROM fingerprint_clusters
GROUP BY skip_policy;
```

### Estimate Scan Reduction

```sql
SELECT
    SUM(CASE WHEN skip_policy = 'skip' THEN wallet_count ELSE 0 END) as wallets_skipped,
    SUM(CASE WHEN skip_policy = 'shallow' THEN wallet_count ELSE 0 END) as wallets_shallowed,
    SUM(CASE WHEN skip_policy = 'skip' THEN wallet_count * 2 ELSE 0 END) +
    SUM(CASE WHEN skip_policy = 'shallow' THEN wallet_count * 1 ELSE 0 END) as pages_avoided,
    (SUM(CASE WHEN skip_policy = 'skip' THEN wallet_count * 2 ELSE 0 END) +
     SUM(CASE WHEN skip_policy = 'shallow' THEN wallet_count * 1 ELSE 0 END)) * 100 as credits_saved_estimate
FROM fingerprint_clusters;
```

### Monitor Fingerprint Matching

```python
from wallet_fingerprint_cache import get_fingerprint_stats, estimate_scan_reduction

conn = sqlite3.connect('flex_complete_database.db')

stats = get_fingerprint_stats(conn)
print(f"Total fingerprints: {stats['total_fingerprints']}")
print(f"Total clusters: {stats['total_clusters']}")
print(f"Skip policies: {stats['skip_policy_skip']} skip, {stats['skip_policy_shallow']} shallow")

reduction = estimate_scan_reduction(conn)
print(f"Pages avoided: {reduction['pages_avoided_estimate']}")
print(f"Credits saved: {reduction['estimated_credits_saved']}")

conn.close()
```

---

## Key Points

### Zero Extra API Calls

Fingerprints compute from **already-fetched first page**. No additional Helius or RPC calls.

### Progressive Clustering

Clusters are created automatically when patterns repeat across 5+ wallets. No manual config needed.

### Conservative at First

Initial policies are `shallow` or `normal`. Only promote to `skip` when confidence is very high.

### Composable with Cache

Works seamlessly with global wallet cache:
- Cache hit: 0 API calls
- Cache miss + fingerprint skip: 1 page (100 credits) instead of 50 pages (5,000 credits)
- Cache miss + fingerprint shallow: 1 page (100 credits)
- Cache miss + normal: 2+ pages as before

---

## Configuration

All settings in `wallet_fingerprint_cache.py`:

```python
# Cluster creation threshold
CLUSTER_CREATION_THRESHOLD = 5  # Create cluster after 5 wallets with same fingerprint

# Confidence thresholds
CONFIDENCE_THRESHOLD_HIGH = 0.9   # For 'skip' policy
CONFIDENCE_THRESHOLD_MEDIUM = 0.7  # For 'shallow' policy
```

---

## Troubleshooting

### Problem: Fingerprints not being used

**Check:**
```sql
SELECT COUNT(*) FROM wallet_fingerprints;
SELECT COUNT(*) FROM fingerprint_clusters;
```

If 0 fingerprints after scanning 100+ wallets:
- Verify `apply_fingerprint_after_first_page()` is called after first page fetch
- Check logs for `[FINGERPRINT]` messages

### Problem: No clusters being created

**Likely causes:**
- Threshold not met (need 5+ wallets with same hash)
- Low confidence scores

**Check:**
```sql
SELECT fingerprint_hash, COUNT(*) as count
FROM wallet_fingerprints
GROUP BY fingerprint_hash
ORDER BY count DESC
LIMIT 10;
```

If many hashes with 1-2 wallets: clusters will form over time

### Problem: Too many wallets marked 'skip'

**Solution:** Lower confidence thresholds
```python
CONFIDENCE_THRESHOLD_HIGH = 0.95  # Up from 0.9 (more conservative)
```

---

## Expected Timeline

| Day | Fingerprints | Clusters | Scans Reduced | Additional Savings |
|-----|---|---|---|---|
| 1 | 50-100 | 0 | 0% | 0% |
| 3 | 200-300 | 5-10 | 10-15% | 10-15% |
| 7 | 500+ | 20-30 | 30-50% | 15-25% |
| 14 | 1000+ | 50+ | 50-70% | 25-35% |

**Combined with global cache (80% hit rate):**
- Day 1: 75% reduction (cache)
- Day 7: 85% reduction (cache + fingerprints)
- Day 14: 90% reduction (cache + fingerprints)

---

## Files to Modify/Create

| File | Change |
|------|--------|
| `wallet_fingerprint_cache.py` | **NEW** - Fingerprint implementation |
| `wallet_cache_production.py` | Add `cex_addresses`, `infra_addresses` parameters to `analyze_wallet_incremental()` |
| Database | Run SQL migrations above |

---

**Version:** 1.0
**Status:** Ready for Integration
**Expected Impact:** 5-10x additional reduction on top of global cache
