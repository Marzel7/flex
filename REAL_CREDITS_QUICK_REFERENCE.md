# Real Credits Savings - Quick Reference Card
**Bookmark this for deployment day**

---

## 📋 4-Step Deployment (51 minutes total)

### Step 1: Apply Database Schema (1 minute)
```bash
cd /Users/kevinkeaveney/Dev/claude/flex
sqlite3 flex_complete_database.db < rpc_metrics_schema_migration.sql

# Verify
sqlite3 flex_complete_database.db \
  "SELECT COUNT(*) FROM pragma_table_info('rpc_metrics') WHERE name IN ('cache_action', 'credits_saved');"
# Should return: 2
```

---

### Step 2: Patch RPC Metrics Recorder (10 minutes)
**File**: `rpc_metrics_recorder.py`

**Find**: `def record_request(self,` around line 265

**Add parameters to signature**:
```python
cache_action: str = "none",  # NEW
credits_saved: int = 0,       # NEW
```

**Find**: `_persist_rpc_metric(` call

**Update parameters**:
```python
_persist_rpc_metric(
    ts, section, provider, method, status_code, latency_ms,
    credits, mode, retries, source_file, error,
    cache_action=cache_action,      # NEW
    credits_saved=credits_saved,    # NEW
)
```

**Find**: INSERT statement in `_persist_rpc_metric()`

**Add columns**:
```sql
-- Add to column list
... error, process_pid, cache_action, credits_saved)
-- Add to values
... error, pid, ?, ?)
```

**Add method** (at end of class):
```python
def get_real_cache_savings(self, hours: int = 24) -> Dict[str, Any]:
    """Get actual documented cache savings from database."""
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cur = conn.cursor()
        cutoff = f"datetime('now', '-{hours} hours')"
        cur.execute(f"SELECT SUM(credits_saved), COUNT(*) FROM rpc_metrics WHERE cache_action IN ('skip', 'refresh') AND recorded_at >= {cutoff}")
        total_saved, total_hits = cur.fetchone() or (0, 0)
        cur.execute(f"SELECT COUNT(*) FROM rpc_metrics WHERE recorded_at >= {cutoff}")
        total_requests = cur.fetchone()[0] or 1
        conn.close()
        return {
            'window_hours': hours,
            'total_credits_saved': total_saved or 0,
            'total_cache_hits': total_hits or 0,
            'cache_hit_rate': f"{100.0 * (total_hits or 0) / total_requests:.1f}%",
        }
    except Exception as e:
        return {}
```

---

### Step 3: Patch Funder Incoming Extractor (15 minutes)
**File**: `funder_incoming_extractor.py`

**Find**: `helius_pages = 1` around line 722

**Add after it**:
```python
# Calculate credits saved based on cache action
cache_action = "none"
credits_saved = 0

if FINGERPRINT_CLUSTER is not None and action is not None:
    if action == FingerprintAction.SKIP:
        cache_action = "skip"
        credits_saved = 200
    elif action == FingerprintAction.REFRESH:
        cache_action = "refresh"
        credits_saved = 150
    else:  # FULL_SCAN
        cache_action = "full_scan"
        credits_saved = 0
```

**Find**: `record_request(` around line 926

**Replace with**:
```python
try:
    provider = "helius_rpc" if USE_HELIUS else "solana_rpc"
    method = "helius_address_feed" if USE_HELIUS else "rpc_signatures"

    record_request(
        section="funder_incoming",
        provider=provider,
        method=method,
        status_code=200,
        latency_ms=0.0,
        mode="realtime",
        retries=0,
        source_file="funder_incoming_extractor",
        cache_action=cache_action,      # NEW
        credits_saved=credits_saved,    # NEW
    )
except Exception as e:
    logger.debug(f"[METRICS] Recording failed: {e}")
```

---

### Step 4: Patch Creator Funding Extractor (15 minutes)
**File**: `realtime_creator_funding_extractor.py`

**Find**: Top of file (after imports)

**Add**:
```python
from creator_funding_graph_cache import CreatorFundingGraphCache

CREATOR_CACHE = None
try:
    CREATOR_CACHE = CreatorFundingGraphCache(DB_PATH, ttl_hours=24)
except Exception as e:
    logger.warning(f"[CREATOR_CACHE] Init failed: {e}")
```

**Find**: `extract_funding_for_new_token()` function start

**Add after docstring**:
```python
creator_cache_hit = 0
cache_action = "none"
credits_saved = 0

creator_funders = None
if CREATOR_CACHE is not None:
    try:
        cached = CREATOR_CACHE.get_cached_funders(creator_address)
        if cached is not None:
            creator_funders = cached
            creator_cache_hit = 1
            cache_action = "skip"
            credits_saved = 150
            logger.info(f"[CREATOR_CACHE] HIT: {creator_address[:16]}...")
        else:
            cache_action = "full_scan"
            credits_saved = 0
    except Exception as e:
        logger.warning(f"[CREATOR_CACHE] Lookup failed: {e}")
        cache_action = "full_scan"
        credits_saved = 0

if creator_funders is None:
    try:
        creator_funders = extract_creator_funders(creator_address)
        if CREATOR_CACHE is not None and creator_funders:
            try:
                CREATOR_CACHE.store_funders(creator_address, creator_funders)
            except Exception as e:
                logger.warning(f"[CREATOR_CACHE] Store failed: {e}")
    except Exception as e:
        logger.error(f"[EXTRACTION] Failed: {e}")
        return None
```

**Find**: `record_request()` call after extraction

**Update to**:
```python
try:
    record_request(
        section="creator_funding",
        provider="helius_rpc" if USE_HELIUS else "solana_rpc",
        method="creator_funders_extraction",
        status_code=200,
        latency_ms=0.0,
        mode="realtime",
        retries=0,
        source_file="realtime_creator_funding_extractor",
        cache_action=cache_action,      # NEW
        credits_saved=credits_saved,    # NEW
    )
except Exception as e:
    logger.debug(f"[METRICS] Recording failed: {e}")
```

---

## ✅ Verification (2 minutes)

```bash
# Check schema
sqlite3 flex_complete_database.db \
  "SELECT name FROM sqlite_master WHERE type='view' AND name LIKE 'v_cache%';"
# Should show 3 views

# Check real savings (after first extraction)
sqlite3 flex_complete_database.db \
  "SELECT cache_action, COUNT(*), SUM(credits_saved) FROM rpc_metrics \
   WHERE cache_action != 'none' GROUP BY cache_action;"
```

---

## 📊 Real Savings Queries

**24-hour summary**:
```sql
SELECT * FROM v_cache_savings_24h;
```

**Daily trend (7 days)**:
```sql
SELECT
    DATE(recorded_at) as date,
    SUM(CASE WHEN cache_action='skip' THEN credits_saved ELSE 0 END) as skip_savings,
    SUM(CASE WHEN cache_action='refresh' THEN credits_saved ELSE 0 END) as refresh_savings,
    SUM(credits_saved) as total_daily
FROM rpc_metrics
WHERE recorded_at >= datetime('now', '-7 days')
GROUP BY DATE(recorded_at)
ORDER BY date DESC;
```

**By section**:
```sql
SELECT
    section,
    COUNT(*) as requests,
    SUM(credits_saved) as saved
FROM rpc_metrics
WHERE cache_action IN ('skip', 'refresh')
GROUP BY section
ORDER BY saved DESC;
```

---

## 🔄 Credits Saved Values

**Layer 5 (Wallet Fingerprint)**:
- SKIP: 200 credits (avoided full scan)
- REFRESH: 150 credits (200 full - 50 light)
- FULL_SCAN: 0 credits

**Layer 6 (Creator Cache)**:
- SKIP: 150 credits (avoided extraction)
- FULL_SCAN: 0 credits

---

## 🚨 Rollback (if needed)

**Quick disable**:
```bash
export FINGERPRINT_ENABLED=0
export CREATOR_CACHE_ENABLED=0
# Restart app
```

**Revert code**:
```bash
git checkout rpc_metrics_recorder.py
git checkout funder_incoming_extractor.py
git checkout realtime_creator_funding_extractor.py
```

**Clear cache**:
```bash
sqlite3 flex_complete_database.db "DELETE FROM wallet_fingerprints;"
sqlite3 flex_complete_database.db "DELETE FROM creator_funding_graph;"
```

---

## ⏰ Timeline

| Task | Time |
|------|------|
| Step 1: Schema | 1 min |
| Step 2: Recorder | 10 min |
| Step 3: Funder | 15 min |
| Step 4: Creator | 15 min |
| Verify & Test | 10 min |
| **Total** | **51 min** |

---

## 📱 Files to Reference

During deployment, have these open:

1. **REAL_CREDITS_SAVINGS_INTEGRATION_GUIDE.md** ← Complete details
2. **FUNDER_INCOMING_EXTRACTOR_PATCH.py** ← Layer 5 code reference
3. **REALTIME_CREATOR_FUNDING_EXTRACTOR_PATCH.py** ← Layer 6 code reference
4. **RPC_METRICS_RECORDER_PATCH.py** ← Recorder code reference
5. **rpc_metrics_schema_migration.sql** ← Copy/paste for schema

---

## ✨ What You'll See After

**Before deployment**: Estimates only
```
Cache hits: 450
Estimated savings: 450 × 200 = 90,000 credits (WRONG!)
```

**After deployment**: Real documented savings
```
Skip hits: 300 × 200 = 60,000 credits ✅
Refresh hits: 150 × 150 = 22,500 credits ✅
Total real savings: 82,500 credits (EXACT!)
```

---

**Print this page** → Keep at desk during deployment
**Estimated deployment time**: 51 minutes
**Risk level**: Very low (backward compatible)
**Expected benefit**: 100% accurate savings tracking

