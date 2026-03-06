# Funding Extraction Wallet Cache Improvements (Production Changes)

This document captures the concrete changes to implement in the project to reduce RPC calls and Helius credit usage by improving the wallet analysis cache and scan strategy.

## Objectives
- Avoid repeated historical scans of the same funder wallets across multiple creators.
- Make wallet scans incremental using signature cursors.
- Reduce deep pagination with early-stop heuristics and wallet-type filtering.
- Add instrumentation to measure savings inside the codebase.

---

## Change Set

### 1) Upgrade wallet_analysis_state schema
Store **both cursors** and basic metadata required for safe incremental scanning and skip rules.

```sql
CREATE TABLE IF NOT EXISTS wallet_analysis_state (
    address TEXT PRIMARY KEY,
    newest_signature TEXT,
    oldest_signature TEXT,
    last_analyzed_at INTEGER,
    tx_scanned INTEGER DEFAULT 0,
    meaningful_transfers_found INTEGER DEFAULT 0,
    wallet_type TEXT DEFAULT 'unknown',
    total_tx_count INTEGER,
    error_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wallet_analysis_state_last_analyzed
ON wallet_analysis_state(last_analyzed_at);

CREATE INDEX IF NOT EXISTS idx_wallet_analysis_state_wallet_type
ON wallet_analysis_state(wallet_type);
```

**Why:**
- `newest_signature`: boundary for incremental scans (“stop when we hit this”).
- `oldest_signature`: optional for history boundary / future analysis.
- `wallet_type`, `total_tx_count`: enables skip rules for huge/unhelpful wallets.

---

### 2) Enforce consistent cursor semantics
Pick one approach and keep it consistent:

- **Incremental scan stop marker:** `newest_signature`
- **Pagination cursor:** `before=<last_seen_signature>` (Helius paging)
- After scan finishes:
  - set `newest_signature` to the most recent tx signature seen during this run
  - set `oldest_signature` to the oldest tx signature seen during this run

---

### 3) Adaptive TTL (skip rescans)
Replace fixed 30-minute TTL with activity-based TTL:

- Active wallets: 30 minutes
- Moderate wallets: 2 hours
- Inactive wallets: 6 hours

Implementation hint:
- classify “activity” by whether the scan observed new txs since last cursor, or by tx count in recent window.

---

### 4) Wallet-type filtering (skip heavy wallets)
Persist `wallet_type` and skip scans for:

- `cex`
- `aggregator`

These wallets are high-volume and low-signal for “who funded whom”.

---

### 5) Total transaction count guard
If `total_tx_count > 5000`, cap scan depth aggressively:

- `max_pages = 1`

This prevents runaway pagination on extremely active wallets.

---

### 6) Early stop heuristics
Stop scanning a wallet when:

- ≥ 10 meaningful transfers found, AND
- ≥ 3 consecutive pages contain zero meaningful transfers

Meaningful transfer threshold for funder analysis:

- ≥ 0.2 SOL (tune as needed)

---

### 7) Funder filtering (cut long tail)
Before scanning funder wallets, filter out funders below a floor:

- ignore funders sending < 0.2 SOL

This typically removes the majority of wallets and yields major call reductions.

---

### 8) Ban RPC “getTransaction per signature” loops by default
Preferred hierarchy:

1) Helius address feed `/v0/addresses/{address}/transactions`
2) If signatures are required: `getSignaturesForAddress` once, then Helius batch `/v0/transactions`
3) Only allow RPC `getTransaction` per signature behind an explicit emergency flag

This removes the biggest RPC explosion mode.

---

## Instrumentation (measure savings in-project)

### 9) Add wallet_scan_metrics telemetry table
Record each wallet scan attempt:

```sql
CREATE TABLE IF NOT EXISTS wallet_scan_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT NOT NULL,
  creator_address TEXT,
  scan_type TEXT NOT NULL,            -- cached_skip | incremental_scan | full_scan | error
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

### 10) Add runtime KPIs
At end of a creator run, print:

- cache_hit_rate
- helius_pages_per_scan
- rpc_calls_per_scan

And compute estimated saved pages:

- `saved_pages ≈ cache_hits × baseline_pages_per_wallet`

Baseline pages can be measured from pre-optimization runs (or from first day after rollout).

---

## Expected Savings
Once the cache is warm (high funder overlap), typical improvements are:

- 70–95% fewer Helius pages fetched for funder scans
- RPC calls near zero (assuming Helius available)
- Token-level credits: ~150–300 → ~5–20 (with filtering + early stop)

---

## Rollout Plan
1) Add schema + metrics tables (safe no-op).
2) Implement cache read/write and cursor semantics.
3) Implement TTL + wallet-type skip.
4) Implement early-stop + funder filter.
5) Disable rpc-only getTransaction loops by default.
6) Monitor: cache hit rate, pages per wallet, 429 rates, and total credits per token.
