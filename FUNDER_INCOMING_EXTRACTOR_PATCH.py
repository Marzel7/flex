# Funder Incoming Extractor Patch - Real Credits Savings Tracking
# Date: 2026-03-05
# Purpose: Integrate cache_action and credits_saved into metrics recording
#
# APPLY THIS PATCH TO: funder_incoming_extractor.py
# LOCATION: Lines 680-935 (extract_transfers_for_funder function)

# ============================================================================
# CONTEXT: fingerprint_cache_hit and fingerprint_refresh are already tracked
# at lines 683-684 and 692, 714. This patch adds credits_saved calculation
# and passes both cache_action and credits_saved to record_request().
# ============================================================================

# CHANGE 1: Add credits_saved calculation after fingerprint cache decision
# LOCATION: After line 722 (after fingerprint cache lookup block)
# REPLACE the existing:
#     helius_pages = 1
# WITH THIS:

"""
        helius_pages = 1

    # Calculate credits saved based on cache action
    cache_action = "none"
    credits_saved = 0

    if FINGERPRINT_CLUSTER is not None and action is not None:
        if action == FingerprintAction.SKIP:
            # Wallet fingerprint cache hit (avoided full scan)
            # Would have used 200 credits for full scan
            cache_action = "skip"
            credits_saved = 200
        elif action == FingerprintAction.REFRESH:
            # Wallet fingerprint cache refresh (light 1-page scan instead of full)
            # Would have used 200 credits, using 50 for light scan
            # Net savings = 200 - 50 = 150
            cache_action = "refresh"
            credits_saved = 150
        else:  # FULL_SCAN
            # No cache hit, normal full scan
            cache_action = "full_scan"
            credits_saved = 0
"""

# ============================================================================
# CHANGE 2: Update record_request call to pass cache_action and credits_saved
# LOCATION: Lines 925-932 (current record_request call)
#
# REPLACE THIS:
#     record_request(
#         funder_address=funder_address,
#         section="funder_incoming",
#         source=source,
#         fingerprint_cache_hit=fingerprint_cache_hit,
#         fingerprint_refresh=fingerprint_refresh,
#     )
#
# WITH THIS:

"""
    # Record metrics with real credits savings tracking
    try:
        # Build the provider and method from context (already available)
        provider = "helius_rpc" if USE_HELIUS else "solana_rpc"
        method = "helius_address_feed" if USE_HELIUS else "rpc_signatures"

        record_request(
            section="funder_incoming",
            provider=provider,
            method=method,
            status_code=200,  # We're recording successful cache operations
            latency_ms=0.0,   # Cache operations are free in terms of latency
            mode="realtime",
            retries=0,
            source_file="funder_incoming_extractor",
            cache_action=cache_action,      # NEW: 'skip', 'refresh', 'full_scan', 'none'
            credits_saved=credits_saved,    # NEW: actual credits avoided
        )
    except Exception as e:
        logger.debug(f"[METRICS] Recording failed: {e}")
"""

# ============================================================================
# COMPLETE UPDATED FUNCTION SIGNATURE
# ============================================================================

# The updated extract_transfers_for_funder function should have:
#
# def extract_transfers_for_funder(
#     funder_address,
#     source="unknown",
# ):
#     """..."""
#     ...
#
#     # Initialize tracking variables
#     cached_type = None
#     cached_conf = None
#     helius_pages = 1
#     fingerprint_cache_hit = 0
#     fingerprint_refresh = 0
#
#     # Check fingerprint cache first
#     if FINGERPRINT_CLUSTER is not None:
#         try:
#             action, cached_type, cached_conf = FINGERPRINT_CLUSTER.lookup_wallet(funder_address)
#             if action == FingerprintAction.SKIP:
#                 fingerprint_cache_hit = 1
#                 # Return cached results...
#                 return {...}
#             elif action == FingerprintAction.REFRESH:
#                 fingerprint_refresh = 1
#                 helius_pages = 1
#             else:  # FULL_SCAN
#                 helius_pages = 1
#         except Exception as e:
#             logger.warning(f"[FINGERPRINT] Lookup failed: {e}")
#             helius_pages = 1
#
#     # NEW: Calculate credits saved based on cache action
#     cache_action = "none"
#     credits_saved = 0
#
#     if FINGERPRINT_CLUSTER is not None and action is not None:
#         if action == FingerprintAction.SKIP:
#             cache_action = "skip"
#             credits_saved = 200    # Avoided full scan
#         elif action == FingerprintAction.REFRESH:
#             cache_action = "refresh"
#             credits_saved = 150    # 200 (full scan) - 50 (light scan)
#         else:  # FULL_SCAN
#             cache_action = "full_scan"
#             credits_saved = 0
#
#     # Continue with existing extraction logic...
#
#     # Record metrics (UPDATED)
#     try:
#         provider = "helius_rpc" if USE_HELIUS else "solana_rpc"
#         method = "helius_address_feed" if USE_HELIUS else "rpc_signatures"
#
#         record_request(
#             section="funder_incoming",
#             provider=provider,
#             method=method,
#             status_code=200,
#             latency_ms=0.0,
#             mode="realtime",
#             retries=0,
#             source_file="funder_incoming_extractor",
#             cache_action=cache_action,      # NEW
#             credits_saved=credits_saved,    # NEW
#         )
#     except Exception as e:
#         logger.debug(f"[METRICS] Recording failed: {e}")
#
#     return {...}


# ============================================================================
# USAGE EXAMPLE - How Credits Saved Will Be Recorded
# ============================================================================

"""
Example 1: Fingerprint Cache SKIP (wallet seen before with high confidence)
---

funder_address: "98a72...bcf3a"
cached_type: "trader"
cached_conf: 0.95

Result:
- action = SKIP
- cache_action = "skip"
- credits_saved = 200
- record_request() called with:
  - section="funder_incoming"
  - cache_action="skip"
  - credits_saved=200  ← 200 credits avoided!

---

Example 2: Fingerprint Cache REFRESH (wallet seen before with medium confidence)
---

funder_address: "98a72...bcf3a"
cached_type: "whale"
cached_conf: 0.75

Result:
- action = REFRESH
- cache_action = "refresh"
- credits_saved = 150
- record_request() called with:
  - section="funder_incoming"
  - cache_action="refresh"
  - credits_saved=150  ← 150 credits avoided (200 full scan - 50 light scan)

---

Example 3: Fingerprint Cache FULL_SCAN (wallet not seen or low confidence)
---

funder_address: "98a72...bcf3a"
cached_type: None
cached_conf: None

Result:
- action = FULL_SCAN
- cache_action = "full_scan"
- credits_saved = 0
- record_request() called with:
  - section="funder_incoming"
  - cache_action="full_scan"
  - credits_saved=0  ← No savings, full scan needed
"""

# ============================================================================
# MONITORING AND VERIFICATION
# ============================================================================

"""
After deploying this patch, you can verify real savings tracking with these queries:

1. Cache action breakdown (24h):
   SELECT
       cache_action,
       COUNT(*) as count,
       SUM(credits_saved) as total_saved,
       ROUND(AVG(credits_saved), 1) as avg_saved
   FROM rpc_metrics
   WHERE cache_action IN ('skip', 'refresh', 'full_scan')
     AND recorded_at >= datetime('now', '-24 hours')
   GROUP BY cache_action
   ORDER BY total_saved DESC;

2. Real savings by section:
   SELECT
       section,
       COUNT(*) as requests,
       SUM(credits_saved) as actual_saved,
       ROUND(100.0 * SUM(CASE WHEN cache_action != 'none' THEN 1 ELSE 0 END) / COUNT(*), 1) as hit_rate_pct
   FROM rpc_metrics
   WHERE recorded_at >= datetime('now', '-24 hours')
   GROUP BY section
   ORDER BY actual_saved DESC;

3. Savings trend (daily):
   SELECT
       DATE(recorded_at) as date,
       SUM(CASE WHEN cache_action='skip' THEN credits_saved ELSE 0 END) as skip_savings,
       SUM(CASE WHEN cache_action='refresh' THEN credits_saved ELSE 0 END) as refresh_savings,
       SUM(credits_saved) as total_daily_savings
   FROM rpc_metrics
   WHERE cache_action IN ('skip', 'refresh')
     AND recorded_at >= datetime('now', '-7 days')
   GROUP BY DATE(recorded_at)
   ORDER BY date DESC;
"""

