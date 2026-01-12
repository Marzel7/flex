# Helius API Capabilities Analysis

## Endpoint Status ✓

### Working Endpoints
1. **Helius RPC** (`https://mainnet.helius-rpc.com/`)
   - ✓ getHealth: Working (1.04s response)
   - ✓ Standard RPC methods available
   - ⚠️ getMultipleTransactions: NOT SUPPORTED
   - ⚠️ getTransactions: NOT SUPPORTED

2. **Helius REST API** (`https://api.helius.xyz/v0/`)
   - ✓ `/addresses/{mint}/transactions` - Working for signature fetching
   - Provides paginated transaction history
   - Includes page-token for pagination

## Performance Comparison

| Provider | Response Time | Supports Batch | Rate Limit |
|----------|--------------|----------------|-----------|
| Public RPC | ~240ms | ❌ No | ~40/sec |
| Helius RPC | ~1000ms | ❌ No | ✓ Better |
| Helius REST | ~900ms | ? Unknown | ✓ Better |

## Current Limitations

### Why Coverage is Still Limited (~17-18%)
1. **No batch transaction API** - Helius doesn't support getMultipleTransactions
2. **Individual calls only** - Must fetch each transaction separately
3. **Public RPC rate limits** - Hit after ~50-100 requests
4. **RPC timeout** - 20s timeout too aggressive for high latency

## Phase 2 Options

### Option A: Use Helius RPC Instead of Public ✓ (Easy)
- Implementation: Change RPC_URL to Helius endpoint
- Expected coverage: 20-25% (slight improvement)
- Pros: Better rate limits, faster initial response
- Cons: Still individual calls, not much improvement

### Option B: Investigate Helius Advanced APIs ⚓ (Medium)
- Check if Helius has undocumented batch endpoints
- Look for Helius-specific transaction fetching methods
- Expected coverage: Unknown (30-60%?)

### Option C: Use Different Provider (Medium+)
- Quicknode: Supports better batch operations
- Syndica: Archival RPC with better throughput
- Expected coverage: 60-80%
- Downside: Likely requires paid plan

### Option D: Increase Timeout & Retries (Quick) ✓
- Increase RPC_TIMEOUT from 20s to 30-40s
- Increase MAX_RETRIES from 3 to 5-7
- Increase RETRY_DELAYS for longer backoff
- Expected coverage: 20-30% improvement with current setup

### Option E: Reduce Batch Size But Increase Retries (Hybrid)
- Smaller batches but more aggressive retry
- Better for rate-limited environments
- Expected coverage: 25-35%

## Recommendation

For now:

1. **Short term**: Option D (Increase Timeout & Retries)
   - 10-15 minutes of work
   - +10-15% coverage improvement
   - No infrastructure changes needed

2. **Medium term**: Option C (Try QuickNode or Syndica)
   - Research which provider has best coverage
   - Likely paid tier required
   - Could achieve 60-80% coverage

3. **Long term**: Option B (Custom API)
   - Implement proxy/cache layer
   - Store transaction data locally
   - Better for repeated queries

## Test Results

```
Token: 8XzSqqNevScuiqJwDuKM...
Signatures: 879
Current Coverage: 13.2% (116 txs)
With Option D (+50% timeout): ~19-20% expected
With Option C (QuickNode): ~60-70% expected
```

## Decision

**Recommend Option D now + Option C later**

Option D gives quick wins without external dependencies.
Option C (premium RPC) only needed if we need 60%+ coverage.

