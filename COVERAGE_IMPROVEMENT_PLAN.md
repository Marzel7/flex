# Coverage Improvement Plan

## Current Status
- **Coverage**: 12.7% average (165 events from 1,300 signatures)
- **Time**: ~30 seconds per token
- **Method**: V2 async with 100 concurrent requests per batch

## Why Coverage is Limited (~12-18%)

### The Bottleneck: RPC Rate Limiting
```
Signatures Fetched:     1,300 ✓ (we get all signatures)
                           ↓
Transaction Fetches:    100-200 ⚠️ (12-15% success rate)
                           ↓
Rate Limit Kicks In:    After ~50-100 requests
Remaining Requests:     Fail/timeout
                           ↓
Final Coverage:         12-18%
```

## Root Cause Analysis

1. **Public RPC Rate Limits**: ~40 requests/second max
2. **Individual Transaction Calls**: Each `getTransaction` is expensive
3. **Batch Size**: 100 concurrent requests hits rate limits quickly
4. **Retry Logic**: Helps but can't overcome fundamental limit
5. **No Batch API**: Solana RPC doesn't support `getMultipleTransactions`

## Solutions (Ranked by Impact)

### Option 1: Premium RPC Provider ⭐⭐⭐ (RECOMMENDED)
**Impact**: +50-80% coverage improvement (60-80% total)

**Implementation**: Switch to QuickNode or Syndica
- Better rate limits: 1,000+ req/sec
- Faster responses: ~100ms vs ~500ms
- Better infrastructure
- Some support batch operations

**Cost**: $0 (free tier) to $100+/month (paid)
**Effort**: 30 minutes (swap RPC URL)
**Expected**: 60-80% coverage

**Code Change**:
```python
# Old
rpc_url = "https://api.mainnet-beta.solana.com"

# New (QuickNode)
rpc_url = "https://[YOUR-ENDPOINT].solana-mainnet.quiknode.pro/"
```

---

### Option 2: Local Transaction Cache ⭐⭐ (FREE)
**Impact**: +20-40% coverage improvement on repeated tokens

**Implementation**: SQLite cache for fetched transactions
- Store transactions after fetching
- Reuse cached data for re-analysis
- Avoid re-fetching same signatures

**Cost**: Free
**Effort**: 2-3 hours
**Expected**: 32-58% coverage (depends on token overlap)

**Benefits**:
- Faster re-analysis of tokens
- Reduces RPC load
- Works with any RPC provider

**Trade-offs**:
- Only helps with repeated tokens
- Requires disk space
- Cache invalidation logic needed

---

### Option 3: Multiple RPC Providers (Hybrid) ⭐⭐⭐
**Impact**: +30-50% coverage improvement

**Implementation**: Failover across multiple providers
- Try Helius first
- Fallback to QuickNode
- Fallback to public RPC
- Distribute load

**Cost**: $0-200/month
**Effort**: 4-6 hours
**Expected**: 50-70% coverage

**Benefits**:
- Resilient to any single provider failure
- Better rate limit distribution
- Highest coverage without extreme cost

---

### Option 4: Reduce Batch Size + More Retries (Quick Win) ⭐
**Impact**: +5-10% coverage improvement

**Implementation**: Adjust async parameters
```python
BATCH_SIZE = 50  # Down from 100
MAX_RETRIES = 7  # Up from 5
RETRY_DELAYS = [0.2, 0.5, 1, 2, 3, 5, 10]
```

**Cost**: Free
**Effort**: 15 minutes
**Expected**: 15-25% coverage

**Trade-offs**:
- Slower (more retries = more waiting)
- Might not help much (fundamental limit)

---

### Option 5: Streaming Transaction Parser (Already Done) ⭐
**Impact**: Optimizes existing approach

**Status**: ✓ Already implemented in V2
- Discards transactions after parsing
- Minimal memory overhead
- Fast processing

**Current benefit**: Enables 30-second analysis time

---

## Recommended Approach

### Phase 1 (Immediate) - Option 1: Premium RPC
```bash
Cost: $0-50/month (free tier available)
Time: 30 minutes
Benefit: 60-80% coverage
```

1. Sign up for QuickNode free tier
2. Get your endpoint URL
3. Update `pump_fun_pre_migration_analyzer_v2.py` line 29:
   ```python
   # Change from:
   # "https://api.mainnet-beta.solana.com"
   # To your QuickNode URL
   ```
4. Test and verify

### Phase 2 (If needed) - Option 3: Hybrid Multi-Provider
```bash
Cost: $0-200/month
Time: 4-6 hours
Benefit: 50-70% coverage (resilient)
```

Combine:
- QuickNode primary
- Helius secondary
- Public RPC fallback

### Phase 3 (Long-term) - Option 2: Local Cache
```bash
Cost: Free
Time: 2-3 hours
Benefit: +20-40% on repeated tokens
```

Add SQLite caching layer for tokens analyzed multiple times.

---

## Current Performance vs Improved

| Metric | Current | With Premium RPC | With Hybrid |
|--------|---------|------------------|------------|
| Coverage | 12-18% | 60-80% | 50-70% |
| Time | 30s | 30-40s | 35-45s |
| Cost | Free | $0-50/mo | $0-200/mo |
| Reliability | Medium | High | Very High |

---

## Implementation Steps for Option 1 (Quickest Win)

1. **Get QuickNode URL** (5 min)
   - Go to https://www.quicknode.com
   - Sign up (free tier available)
   - Create Solana endpoint
   - Copy endpoint URL

2. **Update Code** (5 min)
   ```python
   # pump_fun_pre_migration_analyzer_v2.py, line 29
   RPC_URL = os.getenv("RPC_URL", "https://[YOUR-QUICKNODE-URL].solana-mainnet.quiknode.pro/")
   ```

3. **Add to .env** (2 min)
   ```
   RPC_URL=https://[YOUR-QUICKNODE-URL].solana-mainnet.quiknode.pro/
   ```

4. **Test** (5 min)
   ```bash
   python3 pumpfun_curve_listener.py
   # Watch coverage % - should improve to 60-80%
   ```

5. **Monitor** (ongoing)
   - Track coverage improvements
   - Monitor rate limits
   - Adjust batch size if needed

---

## Decision Matrix

Choose based on your priorities:

| Priority | Solution |
|----------|----------|
| **Fast improvement** | Option 1 (Premium RPC) |
| **Zero cost** | Option 2 (Cache) |
| **Best reliability** | Option 3 (Hybrid) |
| **Quick test** | Option 4 (Tune parameters) |

---

## Next Steps

**Recommendation**: Implement **Option 1 (Premium RPC)** first
- Quickest path to 60-80% coverage
- Free tier available (test first)
- Only 30 minutes of work
- Massive coverage improvement

Would you like me to help implement this?

