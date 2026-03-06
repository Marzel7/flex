# Fingerprint Schema Improvements

**Status:** ✅ Complete & Deployed
**Date:** March 5, 2026
**Focus:** Safety, versioning, and progressive policy promotion

---

## Three Key Improvements Implemented

### 1. Fingerprint Versioning (`fingerprint_version`)

**What:** Each wallet fingerprint now tracks the algorithm version used to compute it.

**Why:** Enables safe algorithm updates without recomputation of old fingerprints.

**Schema:**
```sql
ALTER TABLE wallet_fingerprints ADD COLUMN fingerprint_version INTEGER DEFAULT 1;
```

**Usage:**
```python
# When computing fingerprints
upsert_wallet_fingerprint(
    conn, address, fp_hash, wallet_type, conf,
    fingerprint_version=2  # Use new algorithm
)

# When querying, can filter by version
SELECT * FROM wallet_fingerprints WHERE fingerprint_version = 1
```

**Benefits:**
- A/B test new fingerprinting algorithms
- Gradually deprecate old fingerprints
- Audit trail of which wallets use which algorithm version
- Zero breaking changes (defaults to version 1)

---

### 2. Cluster Confidence Tracking (`cluster_confidence`)

**What:** Fingerprint clusters now store the confidence level of their classification.

**Why:** Enables progressive policy promotion from shallow → skip as confidence grows.

**Schema:**
```sql
ALTER TABLE fingerprint_clusters ADD COLUMN cluster_confidence REAL DEFAULT 0.0;
```

**Usage:**
```python
# Cluster created with initial confidence
CREATE cluster_confidence = 0.85

# Over time, as more wallets join the cluster, confidence can be updated
UPDATE fingerprint_clusters
SET cluster_confidence = 0.95
WHERE cluster_id = 5

# Query can check confidence before applying skip policy
SELECT * FROM fingerprint_clusters
WHERE skip_policy = 'skip' AND cluster_confidence >= 0.9
```

**Benefits:**
- Progressively strengthen policies as evidence accumulates
- Implement feedback loops (e.g., if cluster causes false positives, reduce confidence)
- Audit trail of confidence changes over time
- More flexible policy management

---

### 3. Safety Rule: 20-Wallet Threshold for Skip Policy

**What:** Skip policies are only applied to clusters with >= 20 wallets.

**Why:** Prevent accidental misclassification from early, noisy data.

**Implementation:**
```python
def maybe_create_cluster(...):
    # ...
    if (confidence >= CONFIDENCE_THRESHOLD_HIGH and
        wallet_type in {'cex', 'aggregator'} and
        wallet_count >= 20):  # ← SAFETY RULE
        skip_policy = SkipPolicy.SKIP
    elif confidence >= CONFIDENCE_THRESHOLD_MEDIUM:
        skip_policy = SkipPolicy.SHALLOW
    else:
        skip_policy = SkipPolicy.NORMAL
```

**Timeline:**
```
Wallet 1-5:   policy=NORMAL (threshold not met)
Wallet 6-10:  policy=SHALLOW (medium confidence, < 20 count)
Wallet 11-19: policy=SHALLOW (still building evidence)
Wallet 20+:   policy=SKIP (threshold met, can trust pattern)
```

**Benefits:**
- Conservative approach prevents false positives
- Allows early clusters to gather evidence
- Graceful degradation of confidence
- Automatic transition when threshold is met

---

## Updated Schema

**wallet_fingerprints table:**
```sql
CREATE TABLE wallet_fingerprints (
    address TEXT PRIMARY KEY,
    fingerprint_hash TEXT NOT NULL,
    fingerprint_version INTEGER DEFAULT 1,      -- ← NEW
    cluster_id INTEGER,
    wallet_type TEXT DEFAULT 'unknown',
    confidence REAL DEFAULT 0.0,
    computed_at INTEGER,
    sample_txs INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

**fingerprint_clusters table:**
```sql
CREATE TABLE fingerprint_clusters (
    cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint_hash TEXT NOT NULL UNIQUE,
    wallet_type TEXT DEFAULT 'unknown',
    skip_policy TEXT DEFAULT 'normal',
    cluster_confidence REAL DEFAULT 0.0,         -- ← NEW
    wallet_count INTEGER DEFAULT 0,
    created_at INTEGER,
    updated_at INTEGER
);
```

---

## Integration Impact

### For New Deployments
✅ **No changes needed.** New tables created with `fingerprint_version=1` and `cluster_confidence=0.0` defaults.

### For Existing Deployments
If you already have fingerprint tables, migration is simple:

```python
# Call this once at startup
def migrate_fingerprint_schema_improvements(conn):
    cursor = conn.cursor()

    # Add new columns if they don't exist
    try:
        cursor.execute("""
            ALTER TABLE wallet_fingerprints
            ADD COLUMN fingerprint_version INTEGER DEFAULT 1
        """)
    except:
        pass  # Column already exists

    try:
        cursor.execute("""
            ALTER TABLE fingerprint_clusters
            ADD COLUMN cluster_confidence REAL DEFAULT 0.0
        """)
    except:
        pass  # Column already exists

    conn.commit()
```

---

## Monitoring & Validation

### Check Schema Updates
```sql
PRAGMA table_info(wallet_fingerprints);     -- Should show fingerprint_version
PRAGMA table_info(fingerprint_clusters);    -- Should show cluster_confidence
```

### Monitor Version Distribution
```sql
SELECT fingerprint_version, COUNT(*) as wallet_count
FROM wallet_fingerprints
GROUP BY fingerprint_version
ORDER BY fingerprint_version DESC;
```

**Expected:** Should initially be all version 1, allowing gradual adoption of new versions.

### Monitor Confidence Growth
```sql
SELECT
    cluster_id,
    wallet_type,
    skip_policy,
    cluster_confidence,
    wallet_count
FROM fingerprint_clusters
WHERE skip_policy = 'skip'
ORDER BY wallet_count DESC;
```

**Expected:** Clusters with skip_policy='skip' should have wallet_count >= 20.

---

## Rollback Plan

If needed, improvements can be safely removed:

```python
# Remove fingerprint_version (reverts to single algorithm)
ALTER TABLE wallet_fingerprints DROP COLUMN fingerprint_version;

# Remove cluster_confidence (policies stay static)
ALTER TABLE fingerprint_clusters DROP COLUMN cluster_confidence;
```

Both are backward-compatible; existing data remains valid.

---

## Configuration Reference

All safety thresholds are configurable in `wallet_fingerprint_cache.py`:

```python
# Minimum wallet count before allowing skip policy
MIN_WALLET_COUNT_FOR_SKIP = 20  # ← The safety rule

# Confidence thresholds
CONFIDENCE_THRESHOLD_HIGH = 0.9    # For skip policies
CONFIDENCE_THRESHOLD_MEDIUM = 0.7  # For shallow policies

# Cluster creation threshold
CLUSTER_CREATION_THRESHOLD = 5     # Create cluster after 5 wallets
```

To adjust safety conservativeness:
```python
MIN_WALLET_COUNT_FOR_SKIP = 50  # More conservative (require 50 wallets)
MIN_WALLET_COUNT_FOR_SKIP = 10  # More aggressive (allow 10 wallets)
```

---

## Expected Behavior After Deployment

### Day 1-5: Building Evidence
- Fingerprints computed and stored with version=1
- Clusters created, policies set to SHALLOW or NORMAL
- No skip_policy='skip' until >= 20 wallets

### Day 5-14: Policy Promotion
- As clusters grow past 20 wallets, policies automatically upgrade to SKIP
- cluster_confidence values reflect the evidence base
- Existing shallow policies remain until confidence improves

### Week 2+: Stable State
- Most clusters have optimal policies
- New fingerprint versions can be deployed alongside version 1
- Gradual migration to newer algorithms as needed

---

## Files Updated

- **`wallet_fingerprint_cache.py`**
  - `migrate_fingerprint_schema()` - Updated schema with new columns
  - `upsert_wallet_fingerprint()` - Now accepts fingerprint_version parameter
  - `maybe_create_cluster()` - Implements 20-wallet safety rule
  - `apply_fingerprint_after_first_page()` - Passes fingerprint_version

- **`docs/FINGERPRINT_INTEGRATION.md`** - Updated with new parameters

---

## Version History

| Date | Change | Impact |
|------|--------|--------|
| 2026-03-05 | Added fingerprint_version, cluster_confidence, safety rule | Better versioning, progressive policies, safer defaults |
| 2026-03-04 | Initial fingerprint implementation | Zero-cost wallet classification |

---

**Status:** Production Ready
**Backward Compatible:** ✅ Yes
**Requires Migration:** ❌ No (auto on first run)
**Tested:** ✅ Yes

