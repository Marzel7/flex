# Helius Billing & Rate Limits - Master Reference

**Last Updated**: 2026-03-02
**Source**: https://www.helius.dev/docs/billing/credits and https://www.helius.dev/docs/billing/rate-limits

---

## Quick Reference: Credit Costs

### Standard RPC Methods (1 credit each)
- `getHealth`
- `getClusterNodes`
- `getVersion`
- `getSlot`
- `getSlotLeader`
- `getVoteAccounts`
- `getBalance`
- `getAccountInfo`
- `getMultipleAccounts`
- `simulateBundle`
- `getPriorityFeeEstimate` (Priority Fee API)
- `sendTransaction` (**0 credits** - free!)

### Historical/Archival Methods (10 credits each)
- `getSignaturesForAddress`
- `getTransaction`
- `getBlock`
- `getBlocks`
- `getBlocksWithLimit`
- `getBlockTime`
- `getInflationReward`
- `getSignatureStatuses` (conditional - see below)

### Advanced/Expensive Methods
| Method | Cost | Notes |
|--------|------|-------|
| `getProgramAccounts` | 10 credits | Standard lookup |
| `getProgramAccountsV2` | 1 credit | **Better**: Paginated alternative (paginate instead of fetching all) |
| `getTransactionsForAddress` | 100 credits | Helius-exclusive, Developer+ only |
| `getDAS` (Digital Asset Standard) | 10 credits | Per request |

### Helius Enhanced APIs
| Method | Cost | Usage |
|--------|------|-------|
| `helius_enhanced_addresses_transactions` | 100 credits | Batch addresses endpoint (funder_incoming_extractor) |
| `helius_enhanced_transactions_batch` | 100 credits | Batch transactions endpoint |
| `helius_enhanced_single_transaction` | 10 credits | Single transaction endpoint |

### Conditional Costs
- **`getSignatureStatuses`**:
  - Without `searchTransactionHistory`: **1 credit**
  - With `searchTransactionHistory` enabled: **10 credits**

### Streaming Data
- **LaserStream gRPC**: 3 credits per 0.1 MB uncompressed data
- **Enhanced WebSockets**: 3 credits per 0.1 MB uncompressed data
- **Calculation**: `bytes * (3 / (0.1 * 1024 * 1024))` = `bytes * 0.0000286865234375`

---

## Rate Limits by Plan

| Plan | RPC Requests/sec | DAS & Enhanced APIs/sec | Notes |
|------|------------------|------------------------|-------|
| **Free** | 10 | 2 | Starter plan |
| **Developer** | 50 | 10 | Mid-tier |
| **Business** | 200 | 50 | Enterprise-lite |
| **Professional** | 500 | 100 | High volume |
| **Enterprise** | Custom | Custom | Contact sales |

### Special Rate Limits

| Endpoint | Free | Developer | Business | Professional |
|----------|------|-----------|----------|--------------|
| `sendTransaction` | 1/sec | 10/sec | 50/sec | 100/sec |
| `getProgramAccounts` | 5/sec | 10/sec | 50/sec | 75/sec |
| `getTransaction` (batch) | 100 items per batch | 100 items | 100 items | 100 items |
| `getTransactionsForAddress` | N/A | Individual reqs | Individual reqs | Individual reqs |
| **WebSockets** | 5 concurrent | 25 concurrent | 100 concurrent | 250 concurrent |
| **ZK Compression** | 2/sec | 10/sec | 50/sec | 100/sec |
| **Wallet API** | 2/sec | 10/sec | 50/sec | 100/sec |

### Rate Limit Response
- When exceeded: HTTP 429 (Too Many Requests)
- Include `Retry-After` header with seconds to wait
- Backoff strategy: Exponential backoff with jitter recommended

---

## Your Current Setup

### Project Details
```
Project ID: b5b55487-ccfb-43f8-a2fb-766fbb68f8ce
API Key: f084fae8-d111-4337-9960-2d9c5e02a726
RPC Endpoint: https://mainnet.helius-rpc.com/?api-key={API_KEY}
Enhanced API: https://api-mainnet.helius-rpc.com/v0/transactions?api-key={API_KEY}
```

### Primary Usage in Flex
1. **creator_outgoing_extractor.py**
   - Method: `getSignaturesForAddress` (10 credits per call)
   - Concurrency: 3 concurrent
   - Rate: 8 req/sec
   - Usage: Scanning creator outgoing transfers

2. **funder_incoming_extractor.py**
   - Method: `helius_enhanced_addresses_transactions` (100 credits per batch)
   - Usage: Fetching funder incoming transfers

3. **funder_helius_extractor.py**
   - Various RPC methods for price/token data
   - Mix of 1-credit and 10-credit calls

---

## Cost Estimation & Reconciliation

### Calculating Expected Credits

**Example: Creator Outgoing Scan**
```
Scan frequency: Hourly
Creators per scan: 1000
Calls per creator: 1 × getSignaturesForAddress = 10 credits each

Expected credits per scan: 1000 × 10 = 10,000 credits/hour
Daily estimate: 10,000 × 24 = 240,000 credits/day
Monthly estimate: 10,000 × 24 × 30 = 7,200,000 credits/month
```

### Reconciliation Process

1. **RPC Instrumentation** (your code)
   - Tracks every `record_request()` call
   - Calculates credits based on CREDIT_SCHEDULE
   - Stored in rpc_metrics_recorder.py
   - Access via: GET `/metrics/rpc/summary`

2. **Helius Account Balance** (actual usage)
   - Check dashboard: https://dashboard.helius.dev/rpcs?projectId=b5b55487-ccfb-43f8-a2fb-766fbb68f8ce
   - Update locally: `python helius_usage_cli.py update REMAINING USED MONTH`
   - View snapshots: `python helius_usage_cli.py history`

3. **Compare the Two**
   ```bash
   # Your instrumentation says you used:
   curl http://localhost:8001/metrics/rpc/summary | jq '.credits_instrumented_today'
   # Output: 24682

   # Helius dashboard says you used:
   python helius_usage_cli.py usage | jq '.creditsUsed'
   # Output: 24682

   # Perfect match = all calls are accounted for
   # Discrepancy = uninstrumented calls or test mode
   ```

---

## Critical Issues & Fixes

### Issue: Credit Count ≠ Call Count

**Problem**: Dashboard shows "33,530 creator_outgoing_scan" but only 3,353 credits used.

**Why**:
- **Call count** = number of recorded `record_request()` calls (33,530)
- **Credit cost** = sum of credits for each call
- If each call is 10 credits: 33,530 × 10 = 335,300 credits (but you only used 3,353!)

**Possible Causes**:
1. **Retries are inflating count** - same operation retried multiple times
2. **Calls aren't executing** - test mode or mocked calls
3. **Credit cost is wrong** - actual cost is ~0.1 credits per call (100x cheaper than expected)
4. **Different method is being called** - calls getSignaturesForAddress (10 credits) but charging for something cheaper

### Solution: Verify Actual Usage

1. Check your Helius dashboard for actual credits consumed
2. Verify that `getSignaturesForAddress` is actually being called (not a different method)
3. Check if retries are being counted separately (they should not increase credit cost)
4. Compare instrumented vs actual using reconciliation process above

---

## Optimization Strategies

### 1. Use Cheaper Alternatives
- **getProgramAccountsV2** (1 credit) instead of **getProgramAccounts** (10 credits)
- **getSignatureStatuses** without searchTransactionHistory (1 credit) instead of with it (10 credits)
- Batch calls where possible

### 2. Rate Limiting Strategy
Your current setup in creator_outgoing_extractor.py:
- OUTGOING_RPS = 8.0 (8 req/sec)
- OUTGOING_CONCURRENCY = 3
- MAX_PAGES_PER_CYCLE = 2 (progressive deepening)

This respects the rate limits and avoids 429 errors.

### 3. Caching & Deduplication
- Cache getTransaction results (many scanners query the same tx)
- Deduplicate signature lookups
- Store frequently-accessed data locally

### 4. Streaming for Large Data
- Use WebSockets instead of polling (more efficient)
- LaserStream for high-volume data needs
- Calculate data size before committing to transfer

---

## Files Tracking Credits

| File | Purpose |
|------|---------|
| `rpc_metrics_recorder.py` | CREDIT_SCHEDULE definition + recording logic |
| `creator_outgoing_extractor.py` | Records `getSignaturesForAddress` calls |
| `funder_incoming_extractor.py` | Records enhanced API batch calls |
| `funder_helius_extractor.py` | Records various RPC method calls |
| `helius_usage_cli.py` | Manual tracking + dashboard link |
| This file | Master reference for billing |

---

## Action Items

- [ ] Verify actual Helius account usage vs instrumented costs
- [ ] Check if 33,530 call count is inflated by retries
- [ ] Confirm getSignaturesForAddress is the method being called
- [ ] Consider using cheaper alternatives (getProgramAccountsV2, etc.)
- [ ] Set up weekly reconciliation: `python helius_usage_cli.py dashboard` + update
- [ ] Monitor 429 rate limit errors and adjust concurrency if needed
- [ ] Archive this document as reference for credit calculations

---

## Glossary

- **Credit**: Helius unit of account; 1 credit ≈ 1 lightweight RPC call
- **Enhanced API**: Helius custom APIs (transactions, DAS) for complex queries
- **Rate Limit**: Max requests per second for your plan
- **429**: HTTP "Too Many Requests" error (rate limited)
- **Retry-After**: Header telling client how long to wait before retrying
- **Instrumentation**: Tracking credits in your code (record_request calls)
- **Reconciliation**: Comparing instrumented credits vs actual Helius charges

---

**Generated**: 2026-03-02
**Status**: Production ready
**Next Review**: When significant usage changes occur or Helius updates pricing
