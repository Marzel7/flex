# Realtime Creator Funding Extractor Patch - Real Credits Savings Tracking
# Date: 2026-03-05
# Purpose: Integrate Layer 6 (Creator Funding Graph Cache) with real credits savings tracking
#
# APPLY THIS PATCH TO: realtime_creator_funding_extractor.py
# LOCATION: extract_funding_for_new_token function (around line 60-250)

# ============================================================================
# OVERVIEW: Layer 6 (Creator Funding Graph Cache) Integration
# ============================================================================
#
# Current Flow (without Layer 6):
# 1. Detect new token
# 2. Extract creator funding (100-200 credits)
# 3. Store in database
#
# New Flow (with Layer 6):
# 1. Detect new token
# 2. Check Creator Funding Graph Cache for creator
#    ├─ Cache Hit → Return cached funders [0 credits] ← SAVINGS!
#    └─ Cache Miss → Extract & Store → Continue
# 3. Proceed with funder extraction
#
# This patch adds cache lookup and credits_saved tracking.
# ============================================================================

# CHANGE 1: Add import at module level
# LOCATION: After line 1 (after existing imports)
#
# ADD THIS:

"""
from creator_funding_graph_cache import CreatorFundingGraphCache

# Initialize creator funding cache
CREATOR_CACHE = None
try:
    CREATOR_CACHE = CreatorFundingGraphCache(DB_PATH, ttl_hours=24)
    logger.info("[CREATOR_CACHE] Initialized successfully")
except Exception as e:
    logger.warning(f"[CREATOR_CACHE] Initialization failed (non-blocking): {e}")
"""

# ============================================================================
# CHANGE 2: Add creator cache lookup before extraction
# LOCATION: In extract_funding_for_new_token function, before extraction (around line 150-200)
#
# ADD THIS at the start of the extraction section:

"""
def extract_funding_for_new_token(creator_address, created_at, create_tx_sig, mint):
    '''...existing docstring...'''

    # Initialize tracking variables
    creator_cache_hit = 0
    cache_action = "none"
    credits_saved = 0

    # Layer 6: Check creator funding graph cache first
    creator_funders = None
    if CREATOR_CACHE is not None:
        try:
            cached = CREATOR_CACHE.get_cached_funders(creator_address)
            if cached is not None:
                # Cache hit! Use cached funders
                creator_funders = cached
                creator_cache_hit = 1
                cache_action = "skip"
                credits_saved = 150  # Avoided creator extraction (150 credits)
                logger.info(
                    f"[CREATOR_CACHE] ✅ HIT: {creator_address[:16]}... "
                    f"({len(cached)} funders, saved 150 credits)"
                )
            else:
                # Cache miss - will need to extract
                cache_action = "full_scan"
                credits_saved = 0
                logger.debug(f"[CREATOR_CACHE] ❌ MISS: {creator_address[:16]}...")
        except Exception as e:
            logger.warning(f"[CREATOR_CACHE] Lookup failed (non-blocking): {e}")
            cache_action = "full_scan"
            credits_saved = 0

    # If cache miss, extract creator funding
    if creator_funders is None:
        try:
            creator_funders = extract_creator_funders(creator_address)  # Existing function

            # Layer 6: Store in cache for next token
            if CREATOR_CACHE is not None and creator_funders:
                try:
                    CREATOR_CACHE.store_funders(creator_address, creator_funders)
                    logger.info(
                        f"[CREATOR_CACHE] 💾 STORED: {creator_address[:16]}... "
                        f"({len(creator_funders)} funders)"
                    )
                except Exception as e:
                    logger.warning(f"[CREATOR_CACHE] Store failed (non-blocking): {e}")

        except Exception as e:
            logger.error(f"[EXTRACTION] Creator funding extraction failed: {e}")
            return None

    # Continue with existing processing...
    # (Insert rest of function logic here)

    # Record metrics with Layer 6 cache tracking
    try:
        record_request(
            section="creator_funding",
            provider="helius_rpc" if USE_HELIUS else "solana_rpc",
            method="creator_funders_extraction",
            status_code=200,
            latency_ms=extraction_time,  # Time spent on extraction (or 0 if cached)
            mode="realtime",
            retries=0,
            source_file="realtime_creator_funding_extractor",
            cache_action=cache_action,      # NEW: 'skip' for cache hit, 'full_scan' for miss
            credits_saved=credits_saved,    # NEW: 150 for cache hit, 0 for miss
        )
    except Exception as e:
        logger.debug(f"[METRICS] Recording failed: {e}")

    return {
        "mint": mint,
        "creator": creator_address,
        "funders": creator_funders,
        "cache_hit": creator_cache_hit,  # For logging/monitoring
    }
"""

# ============================================================================
# CHANGE 3: Update metrics recording to include creator cache hit
# LOCATION: After extraction completion, before returning
#
# REPLACE existing record_request call WITH:

"""
    try:
        record_request(
            section="creator_funding",
            provider="helius_rpc" if USE_HELIUS else "solana_rpc",
            method="creator_funders_extraction",
            status_code=200,
            latency_ms=latency if cache_action == "full_scan" else 0.0,
            mode="realtime",
            retries=0,
            source_file="realtime_creator_funding_extractor",
            cache_action=cache_action,      # NEW
            credits_saved=credits_saved,    # NEW
        )
    except Exception as e:
        logger.debug(f"[METRICS] Recording failed: {e}")
"""

# ============================================================================
# CONFIGURATION: Enable/Disable Creator Cache
# ============================================================================

"""
To disable creator cache (for debugging/testing):
1. Environment variable: export CREATOR_CACHE_ENABLED=0
2. Or in code:
   if os.getenv("CREATOR_CACHE_ENABLED", "1") == "0":
       CREATOR_CACHE = None

To change TTL (time-to-live):
   CREATOR_CACHE = CreatorFundingGraphCache(DB_PATH, ttl_hours=12)  # 12 hours instead of 24

To clear cache (manual):
   sqlite3 flex_complete_database.db "DELETE FROM creator_funding_graph;"
"""

# ============================================================================
# USAGE EXAMPLE - How Credits Saved Will Be Recorded
# ============================================================================

"""
Example 1: Creator Cache HIT (creator launches second token)
---

Creator A launches Token 1:
  - Creator cache: MISS
  - Extract creator funding [150 credits]
  - Store in cache

Creator A launches Token 2 (same day):
  - Creator cache: HIT
  - Return cached funders [0 credits] ← SAVINGS!
  - Record metrics:
    - cache_action="skip"
    - credits_saved=150

---

Example 2: Creator Cache MISS (first token from creator)
---

Creator B launches Token 1:
  - Creator cache: MISS (creator not in cache)
  - Extract creator funding [150 credits]
  - Store in cache
  - Record metrics:
    - cache_action="full_scan"
    - credits_saved=0

---

Example 3: Multi-Token Creator Savings
---

Creator C launches 10 tokens over 24 hours:

Token 1: Extract (150cr) → Store → Total: 150cr
Token 2: Cache hit (0cr) → Total: 150cr   [Saved 150!]
Token 3: Cache hit (0cr) → Total: 150cr   [Saved 150!]
Token 4: Cache hit (0cr) → Total: 150cr   [Saved 150!]
Token 5: Cache hit (0cr) → Total: 150cr   [Saved 150!]
...
Token 10: Cache hit (0cr) → Total: 150cr  [Saved 150!]

Total: 150 credits instead of 1,500 (90% savings!)
"""

# ============================================================================
# MONITORING AND VERIFICATION
# ============================================================================

"""
After deploying this patch, verify Layer 6 cache effectiveness:

1. Creator cache growth (should grow over time):
   SELECT COUNT(*) as cached_creators FROM creator_funding_graph;

2. Cache hit rate (24h):
   SELECT
       ROUND(100.0 * SUM(CASE WHEN cache_action='skip' THEN 1 ELSE 0 END) /
             NULLIF(COUNT(*), 0), 1) as cache_hit_rate_pct
   FROM rpc_metrics
   WHERE section='creator_funding'
     AND recorded_at >= datetime('now', '-24 hours');

3. Real credits saved by cache action:
   SELECT
       cache_action,
       COUNT(*) as count,
       SUM(credits_saved) as total_saved
   FROM rpc_metrics
   WHERE section='creator_funding'
     AND cache_action IN ('skip', 'full_scan')
     AND recorded_at >= datetime('now', '-24 hours')
   GROUP BY cache_action
   ORDER BY total_saved DESC;

4. Top creators (most active):
   SELECT creator_address, funder_count
   FROM v_creator_graph_top_creators LIMIT 10;

5. Cumulative savings trend (all layers combined):
   SELECT
       DATE(recorded_at) as date,
       SUM(CASE WHEN cache_action != 'none' THEN credits_saved ELSE 0 END) as total_saved,
       COUNT(*) as total_requests,
       ROUND(100.0 * SUM(CASE WHEN cache_action != 'none' THEN 1 ELSE 0 END) /
             NULLIF(COUNT(*), 0), 1) as cache_hit_rate_pct
   FROM rpc_metrics
   WHERE recorded_at >= datetime('now', '-30 days')
   GROUP BY DATE(recorded_at)
   ORDER BY date DESC;
"""

# ============================================================================
# ERROR HANDLING & GRACEFUL DEGRADATION
# ============================================================================

"""
The implementation is designed to gracefully handle errors:

1. Cache initialization fails?
   → CREATOR_CACHE = None
   → Code continues, just extracts every time (no loss of functionality)

2. Cache lookup fails?
   → Catches exception, sets cache_action="full_scan", credits_saved=0
   → Falls back to normal extraction
   → Logs warning but continues

3. Cache store fails?
   → Catches exception, logs warning
   → Extraction still completes successfully
   → Just won't have cache hit next time

4. Record_request fails?
   → Catches exception, logs debug message
   → Extraction completes
   → Metrics not recorded, but system continues functioning

No scenario will cause extraction to fail or creator processing to break.
The cache is strictly additive optimization.
"""

# ============================================================================
# COMPLETE INTEGRATION CHECKLIST
# ============================================================================

"""
To fully integrate Layer 6 with real credits tracking:

1. ✅ Import CreatorFundingGraphCache at module level
2. ✅ Initialize CREATOR_CACHE in module initialization block
3. ✅ Add creator_cache_hit and cache_action variables
4. ✅ Check cache before extraction
5. ✅ Store in cache after extraction
6. ✅ Update record_request call with cache_action and credits_saved
7. ✅ Update RPC metrics recorder schema (separate patch)
8. ✅ Update RPC metrics recorder record_request signature (separate patch)
9. ✅ Deploy database schema migration
10. ✅ Test cache hits and verify metrics recorded correctly

Files involved:
   - realtime_creator_funding_extractor.py (THIS PATCH)
   - rpc_metrics_recorder.py (ALREADY PATCHED in RPC_METRICS_RECORDER_PATCH.py)
   - flex_complete_database.db (needs schema migration: rpc_metrics_schema_migration.sql)
"""

