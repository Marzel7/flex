
# Measuring RPC & Helius Savings from Wallet Cache Optimization

## Purpose
This document explains how to **measure the real RPC and Helius API savings inside the project codebase** after implementing the wallet analysis cache optimization.

Instead of estimating theoretical savings, the system records **actual scan telemetry** during runtime.

This allows you to track:

- Cache hit rate
- Helius pages fetched
- RPC calls used
- Wallet scans avoided
- Average API cost per wallet
- Total calls saved

---

# 1. Add Wallet Scan Telemetry

Create a telemetry table to record wallet scan activity.

```sql
CREATE TABLE IF NOT EXISTS wallet_scan_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT NOT NULL,
  creator_address TEXT,
  scan_type TEXT NOT NULL,
  helius_pages INTEGER DEFAULT 0,
  rpc_calls INTEGER DEFAULT 0,
  tx_fetched INTEGER DEFAULT 0,
  started_at INTEGER,
  finished_at INTEGER,
  duration_ms INTEGER,
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_started_at
ON wallet_scan_metrics(started_at);

CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_creator
ON wallet_scan_metrics(creator_address);
```

scan_type values:

- cached_skip
- incremental_scan
- full_scan
- error

---

# 2. Where to Record Metrics

## Cache Hit

If wallet scan is skipped due to cache TTL:

```
scan_type = cached_skip
helius_pages = 0
rpc_calls = 0
```

This represents **calls saved**.

---

## Incremental Scan

Each time a page is fetched from:

```
GET /v0/addresses/{wallet}/transactions
```

increment:

```
helius_pages += 1
```

---

## RPC Fallback

If the system calls:

- getSignaturesForAddress
- getTransaction

increment:

```
rpc_calls += 1
```

---

# 3. Compute Actual Savings

Query metrics:

```sql
SELECT
  COUNT(*) AS total_wallet_requests,
  SUM(CASE WHEN scan_type='cached_skip' THEN 1 ELSE 0 END) AS cache_hits,
  SUM(helius_pages) AS helius_pages_used,
  SUM(rpc_calls) AS rpc_calls_used
FROM wallet_scan_metrics;
```

---

# 4. Calculate Saved Calls

Define baseline average pages per wallet before optimization.

Example:

```
baseline_pages_per_wallet = 1.5
```

Estimated saved Helius pages:

```
saved_pages = cache_hits * baseline_pages_per_wallet
```

Actual usage:

```
actual_pages = SUM(helius_pages)
```

---

# 5. Runtime KPI Metrics

Track these counters during execution:

```
wallets_requested
wallets_skipped_cache
wallets_scanned
helius_pages_total
rpc_calls_total
```

Then compute:

```
CACHE_HIT_RATE = wallets_skipped_cache / wallets_requested
AVG_PAGES_PER_WALLET = helius_pages_total / wallets_scanned
RPC_CALLS_PER_WALLET = rpc_calls_total / wallets_scanned
```

---

# 6. Expected Healthy Metrics

Typical optimized system:

| Metric | Expected |
|------|------|
Cache Hit Rate | 70–95% |
Avg Pages Per Wallet | 1.0–1.5 |
RPC Calls | Near zero |

---

# 7. Debug Indicators

If metrics look like:

Cache Hit < 30%
→ cache TTL too low or wallet state not written.

Pages per wallet > 3
→ early stop logic not triggering.

RPC calls high
→ fallback logic using getTransaction loops.

---

# 8. Minimal Runtime Counter Example

Example lightweight counters:

```python
stats = {
  "wallets_requested": 0,
  "cache_hits": 0,
  "wallet_scans": 0,
  "helius_pages": 0,
  "rpc_calls": 0,
}
```

Print summary:

```
CACHE HIT RATE
PAGES PER SCAN
RPC CALLS PER SCAN
```

This provides immediate visibility into optimization performance.

---

# 9. Expected Impact

Before optimization:

150–300 Helius credits per token launch

After optimization:

10–30 credits per token launch

Savings:

80–90% reduction in RPC and Helius API usage
