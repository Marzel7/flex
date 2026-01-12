#!/usr/bin/env python3
"""
Analyze coverage bottlenecks and propose improvements
"""

print("""
COVERAGE ANALYSIS: Why is it only 6-12%?
==========================================

Current Bottleneck: fetch_transactions_async()
----------------------------------------------

Problem: Individual getTransaction RPC calls are slow and rate-limited
  - Current: 50 parallel requests per batch
  - Each request: ~200-500ms on public RPC
  - Rate limit: ~40 requests/second on public RPC
  - Time per batch: 50 txs × 500ms = 500ms+ (if sequential)
  - But we do 50 in parallel, so ~10-15 batches per second max
  - For 1000 signatures: ~67-100 seconds minimum

Current Results:
  - Fetching 879 signatures
  - Only getting 57 transactions (6.5% coverage)
  - This suggests RPC is rejecting/timing out on most calls

Root Causes:
1. Public RPC rate limiting (strict on individual calls)
2. Individual getTransaction calls are expensive
3. Timeout failures silently drop transactions
4. No retry logic for failed requests

Improvement Strategy
====================

OPTION 1: Increase Batch Size (Quick Win)
------------------------------------------
Current: batch_size = 50
Proposed: batch_size = 100-200

Impact:
  ✓ Better parallelization
  ✓ More efficient RPC usage
  ✗ May hit rate limits harder
  
Risk: Could make things worse if RPC rejects large batches

---

OPTION 2: Add Retry Logic with Exponential Backoff (Medium)
-----------------------------------------------------------
For each failed transaction fetch:
  - Retry 2-3 times with increasing delays
  - Wait 1s, 2s, 4s between retries
  - Skip after max retries

Impact:
  ✓ Recovers from transient failures
  ✓ Doesn't require infrastructure changes
  ✓ Reduces false timeouts
  
Expected improvement: +20-40% coverage

---

OPTION 3: Switch to Helius API (Best, Requires Auth)
-----------------------------------------------------
Use Helius getTransaction API instead of RPC:
  - Helius supports batch requests (up to 100 txs per call)
  - Better rate limits (1000+ req/second with API key)
  - Faster response times (~100ms vs 500ms)
  - Dedicated infrastructure

Implementation:
  1. Detect if HELIUS_API_KEY available
  2. Use Helius batch API for transaction fetching
  3. Fall back to RPC individual calls if no key

Impact:
  ✓ Could achieve 80-100% coverage
  ✓ 5-10x faster
  ✓ More reliable (fewer timeouts)
  ✓ Better rate limits
  
Downside: Requires Helius API key

---

OPTION 4: Archival RPC Provider (Premium)
------------------------------------------
Use archival RPC (Syndica, QuickNode, etc) instead of public:
  - Better rate limits
  - Faster response times
  - More reliable infrastructure

Impact:
  ✓ 40-60% coverage improvement
  ✗ Costs money (~$100-500/month)

---

OPTION 5: Increase MAX_SIGNATURES Intelligently (Combined)
----------------------------------------------------------
Current: MAX_SIGNATURES = 40000

Strategy:
  - Fetch fewer signatures initially (1000-2000)
  - Get better coverage of recent activity
  - Faster analysis for bonding curve tokens
  - Complete before token migrates (usually 15-30 min)

Implementation:
  1. Keep MAX_SIGNATURES = 40000 for full analysis
  2. Add MAX_SECONDS timeout (e.g., 120 seconds)
  3. Stop early if we hit timeout

Impact:
  ✓ Better for active tokens
  ✓ Faster results for real-time monitoring
  ✗ May miss some activity on slow tokens

---

RECOMMENDED: Multi-Step Approach
=================================

Phase 1 (Now): Retry Logic + Increase Batch Size
-------------------------------------------------
  - Add retry with exponential backoff (2-3 retries)
  - Increase batch_size from 50 to 100
  - Expected: 15-25% coverage

Phase 2 (With Helius): Use Helius Batch API
--------------------------------------------
  - Detect if HELIUS_API_KEY available
  - Use Helius for transaction fetching (not just signatures)
  - Fall back to RPC + retry for non-Helius users
  - Expected: 60-80% coverage with Helius, 15-25% without

Phase 3 (Optional): Premium RPC
--------------------------------
  - Add archival RPC option for dedicated infrastructure
  - Expected: 80-100% coverage

Implementation Priority:
1. Retry logic (20 lines of code, +10-15% coverage)
2. Increase batch size (1 line, +5-10% coverage)
3. Helius batch API (50 lines, +40-60% coverage)
""")
