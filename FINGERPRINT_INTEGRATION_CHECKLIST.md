# Fingerprint Integration - Step-by-Step Checklist

**Objective**: Complete wallet fingerprint clustering integration
**Time**: 30-45 minutes
**Risk**: Very Low (backward compatible, graceful fallbacks)

---

## Pre-Integration Verification

- [ ] Verify `wallet_fingerprint_clustering.py` is in `http_instrumentation/` directory
- [ ] Verify `wallet_fingerprint_clustering_schema.sql` has been applied to database
- [ ] Verify `get_transactions_helius()` already has `max_pages` parameter (line ~380)
- [ ] Verify `FINGERPRINT_CLUSTER` is initialized at module load (line ~100)
- [ ] Verify imports are in place (lines ~35-45)

**Command to verify**:
```bash
grep -n "from wallet_fingerprint_clustering import" funder_incoming_extractor.py
grep -n "FINGERPRINT_CLUSTER = " funder_incoming_extractor.py
grep -n "def get_transactions_helius" funder_incoming_extractor.py | head -1
```

---

## Step 1: Add Logging Import (5 min)

**File**: `funder_incoming_extractor.py`
**Location**: Line ~26-30 (after other imports)

- [ ] Find imports section
- [ ] Add:
```python
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
```

**Verify**:
```bash
grep -n "logger = logging.getLogger" funder_incoming_extractor.py
```

---

## Step 2: Replace Confidence Scoring Function (10 min)

**File**: `funder_incoming_extractor.py`
**Location**: `_fingerprint_wallet_type_and_confidence()` function (line ~200-225)

- [ ] Find the function
- [ ] Replace entire function with improved version from FINGERPRINT_INTEGRATION_COMPLETE.md
- [ ] Key changes:
  - ✅ Add `txs` parameter
  - ✅ Add transaction pattern analysis
  - ✅ Add logging instead of silent failures
  - ✅ Better confidence scoring (0.5-0.95)

**Command to verify**:
```bash
grep -A 5 "def _fingerprint_wallet_type_and_confidence" funder_incoming_extractor.py
```

---

## Step 3: Improve Fingerprint Lookup (15 min)

**File**: `funder_incoming_extractor.py`
**Location**: Fingerprint clustering block (line ~520-560)

- [ ] Find the `if FINGERPRINT_CLUSTER is not None:` block
- [ ] Initialize variables at function start:
```python
action = None
cached_type = None
cached_conf = None
helius_pages = 1
fingerprint_cache_hit = 0
fingerprint_refresh = 0
```

- [ ] Update SKIP action to check DB cache first
- [ ] Add proper logging for all actions (SKIP/REFRESH/FULL_SCAN)
- [ ] Replace `except Exception:` with proper error logging

**Key improvements**:
- ✅ SKIP checks DB cache before returning
- ✅ REFRESH sets helius_pages=1
- ✅ Error logging instead of silent failures
- ✅ Track fingerprint_cache_hit and fingerprint_refresh flags

**Command to verify**:
```bash
grep -n "fingerprint_cache_hit" funder_incoming_extractor.py | head -5
```

---

## Step 4: Add Transaction Fetching with Error Handling (5 min)

**File**: `funder_incoming_extractor.py`
**Location**: Around line ~580-590

- [ ] Find Helius transaction fetching
- [ ] Wrap `get_transactions_helius()` call in try/except
- [ ] Add logging for failures
- [ ] Pass `max_pages=helius_pages` parameter

```python
txs = None
if USE_HELIUS:
    try:
        txs = get_transactions_helius(
            funder_address,
            limit=helius_limit,
            max_pages=helius_pages,
        )
    except Exception as e:
        logger.warning(f"[HELIUS] Address feed failed: {e}")
        txs = None
```

---

## Step 5: Add Fingerprint Update with Transaction Analysis (10 min)

**File**: `funder_incoming_extractor.py`
**Location**: Before final return statement (line ~758)

- [ ] Find where to insert (before `return {`)
- [ ] Add fingerprint update code:
```python
if FINGERPRINT_CLUSTER is not None and txs:
    try:
        wallet_type, conf = _fingerprint_wallet_type_and_confidence(funder_address, txs)
        if cached_type and cached_conf is not None and cached_conf >= conf:
            wallet_type, conf = cached_type, float(cached_conf)
        FINGERPRINT_CLUSTER.save_fingerprint(
            funder_address,
            wallet_type=wallet_type,
            confidence=float(conf),
            pages_scanned=int(helius_pages),
            skip_reason=str(source),
        )
    except Exception as e:
        logger.warning(f"[FINGERPRINT] Save failed: {e}")
```

**Key points**:
- ✅ Passes `txs` to confidence function
- ✅ Respects cached confidence if higher
- ✅ Records pages_scanned from helius_pages
- ✅ Error logging

---

## Step 6: Record Fingerprint Metrics (5 min)

**File**: `funder_incoming_extractor.py`
**Location**: Just before return statement (line ~760)

- [ ] Add metrics recording:
```python
try:
    record_request(
        funder_address=funder_address,
        section="funder_incoming",
        source=source,
        fingerprint_cache_hit=fingerprint_cache_hit,
        fingerprint_refresh=fingerprint_refresh,
    )
except Exception as e:
    logger.debug(f"[METRICS] Recording failed: {e}")
```

**Verify metrics are passed**:
```bash
grep -n "fingerprint_cache_hit=" funder_incoming_extractor.py | head -1
grep -n "fingerprint_refresh=" funder_incoming_extractor.py | head -1
```

---

## Post-Integration Testing

### Test 1: Syntax Check (2 min)

```bash
python3 -m py_compile funder_incoming_extractor.py
```

Expected: No output (success)

### Test 2: Import Check (2 min)

```bash
python3 -c "from funder_incoming_extractor import extract_transfers_for_funder; print('✅ Import OK')"
```

Expected: `✅ Import OK`

### Test 3: Extract One Funder (5 min)

```bash
python3 << 'EOF'
from funder_incoming_extractor import extract_transfers_for_funder
# Use a test wallet address
result = extract_transfers_for_funder('wallet_address_here')
print(f"Result: {result}")
print(f"Source: {result.get('source')}")
EOF
```

Expected: No errors, valid result dict

### Test 4: Verify Fingerprints Saved (2 min)

```bash
sqlite3 flex_complete_database.db "
SELECT COUNT(*) as fingerprints,
       COUNT(DISTINCT wallet_type) as types
FROM wallet_fingerprints;
"
```

Expected: fingerprints > 0

### Test 5: Verify Metrics Recorded (2 min)

```bash
sqlite3 flex_complete_database.db "
SELECT COUNT(*) as total,
       SUM(fingerprint_cache_hit) as hits,
       SUM(fingerprint_refresh) as refreshes
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-1 hour')
LIMIT 1;
"
```

Expected: total > 0 (shows scans recorded)

### Test 6: Extract Multiple Funders (10 min)

Extract 5+ funders from the same creator:

```bash
python3 << 'EOF'
from funder_incoming_extractor import extract_for_creator
# Use a test creator with multiple funders
results = extract_for_creator('creator_address_here')
print(f"Extracted {len(results)} funder results")
EOF
```

Expected:
- First funder: source = "helius_address_feed" (or similar)
- Second+ funders: some may have `fingerprint_refresh` or `fingerprint_skip`

### Test 7: Monitor Cache Hit Rate (2 min)

```bash
sqlite3 flex_complete_database.db "
SELECT
    ROUND(100.0 * SUM(fingerprint_cache_hit) / COUNT(*), 1) as cache_hit_rate,
    COUNT(*) as total_scans,
    SUM(fingerprint_cache_hit) as cache_hits
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours');
"
```

Expected:
- Day 1: 0-5%
- Week 1: 20-30%
- Month 1: 40-60%

---

## Rollback Plan (If Issues)

If you need to revert:

1. **Disable fingerprinting** (quick fix):
```bash
export FINGERPRINT_ENABLED=0
# Restart service
```

2. **Restore from git** (full revert):
```bash
git checkout funder_incoming_extractor.py
```

3. **Remove metrics** (if needed):
```bash
sqlite3 flex_complete_database.db "
UPDATE wallet_scan_metrics
SET fingerprint_cache_hit = 0, fingerprint_refresh = 0
WHERE created_at >= datetime('now', '-1 day');
"
```

---

## Success Criteria

After completing all steps:

✅ No syntax errors in `funder_incoming_extractor.py`
✅ Fingerprints are saved in `wallet_fingerprints` table
✅ Metrics are recorded in `wallet_scan_metrics` table
✅ Cache hit rate grows over time (0% → 20-30% → 40-60%)
✅ No change in extraction results (backward compatible)
✅ Logs show proper fingerprint actions (SKIP/REFRESH/FULL_SCAN)

---

## Timeline

| Task | Time | Status |
|------|------|--------|
| Step 1: Logging import | 5 min | - [ ] |
| Step 2: Confidence function | 10 min | - [ ] |
| Step 3: Fingerprint lookup | 15 min | - [ ] |
| Step 4: Transaction fetching | 5 min | - [ ] |
| Step 5: Fingerprint update | 10 min | - [ ] |
| Step 6: Metrics recording | 5 min | - [ ] |
| Testing (all 7 tests) | 30 min | - [ ] |
| **Total** | **80 min** | - [ ] |

---

## Questions During Integration?

Refer to:
- **Code details**: FINGERPRINT_INTEGRATION_COMPLETE.md
- **Feedback**: FINGERPRINT_INTEGRATION_FEEDBACK.md
- **Full guide**: WALLET_FINGERPRINT_CLUSTERING_GUIDE.md

---

**Status**: Ready for integration
**Estimated completion**: 1.5-2 hours total
**Expected payoff**: 5-10% additional credit savings (month 1)
**Total optimization stack**: 80-90% Helius API reduction
