# Additional 10× Optimization: Funding Fingerprints & Cluster Caching

This document describes an additional optimization that can reduce wallet scanning by **another 5–10×** on top of the global wallet cache + incremental scans.

It works especially well for pump.fun / meme launch ecosystems where many launches reuse the same infra, CEX paths, and funding patterns.

---

## Why This Helps
Even with wallet caching, you still scan:
- new funders you haven't seen before
- moderate-activity wallets that refresh after TTL

Funding fingerprints reduce scanning by allowing you to:
- classify a wallet's role quickly (bot / infra / cex / retail)
- detect repeated funding patterns across launches
- skip deep scans when a wallet matches a known pattern

---

## Concept: Funding Fingerprint
A fingerprint is a compact representation of a wallet's funding behavior over a short horizon (e.g., 30–300 tx).

Example fingerprint inputs:
- Top counterparties (top 5 senders/recipients)
- % of volume involving CEX/aggregators
- Median transfer size
- Count of distinct counterparties
- Presence of known infra accounts (Jito tip, Meteora, deBridge, Axiom, etc.)
- Time burstiness (many txs in short period)

Fingerprint output:
- stable hash key (string)
- cluster id
- wallet_type classification
- confidence score

---

## New Tables

### 1) wallet_fingerprints
Stores computed fingerprints for wallets.

```sql
CREATE TABLE IF NOT EXISTS wallet_fingerprints (
  address TEXT PRIMARY KEY,
  fingerprint_hash TEXT NOT NULL,
  cluster_id INTEGER,
  wallet_type TEXT DEFAULT 'unknown',
  confidence REAL DEFAULT 0.0,
  computed_at INTEGER,
  sample_txs INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_wallet_fingerprints_hash
ON wallet_fingerprints(fingerprint_hash);

CREATE INDEX IF NOT EXISTS idx_wallet_fingerprints_cluster
ON wallet_fingerprints(cluster_id);
```

### 2) fingerprint_clusters
Maps a fingerprint hash to a stable cluster meaning and skip policy.

```sql
CREATE TABLE IF NOT EXISTS fingerprint_clusters (
  cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint_hash TEXT NOT NULL UNIQUE,
  wallet_type TEXT DEFAULT 'unknown',
  skip_policy TEXT DEFAULT 'normal',     -- normal | shallow | skip
  created_at INTEGER,
  updated_at INTEGER
);
```

---

## Fingerprint Computation (Minimal Viable)
Compute from the first 1–2 pages fetched during any scan.

Inputs (cheap to compute):
- total_in_sol, total_out_sol
- distinct_counterparties
- top 5 counterparties by volume (in + out)
- cex_share = volume involving known CEX wallets / total volume
- infra_share = volume involving known infra wallets / total volume

Fingerprint hash example:
- sorted list of (counterparty, bucketed_volume)
- plus bucketed stats: cex_share bucket, median_size bucket, distinct_counterparties bucket

Goal:
- similar wallets map to same hash even with minor noise

---

## Decision Rules (Where the 10× comes from)

### Rule A: Cluster-based scan depth
If a wallet matches an existing fingerprint cluster:

- skip_policy = 'skip'  -> do not scan wallet (just tag and move on)
- skip_policy = 'shallow' -> scan max 1 page
- skip_policy = 'normal' -> scan as usual

This prevents deep scans on wallets that are already known as:
- CEX-related consolidation wallets
- aggregator routers
- recurring bot infra wallets
- airdrop distribution plumbing

### Rule B: Confidence-based caching
If fingerprint confidence is high (>= 0.9):
- extend TTL aggressively (e.g. 12 hours) for 'infra'/'cex'/'aggregator' types

### Rule C: Fast classification shortcut
If first page already indicates:
- cex_share > 0.8
OR
- infra_share > 0.8
OR
- distinct_counterparties > 200

Then:
- classify as aggregator/cex/bot
- store fingerprint
- set skip_policy='skip' or 'shallow'

---

## Integration Points

### 1) During wallet scan
After first page fetched:
- compute fingerprint candidate
- lookup fingerprint_hash in fingerprint_clusters
- if match and skip_policy != normal:
  - stop scan early
  - update wallet_analysis_state as scanned (shallow)
  - store fingerprint + cluster_id

### 2) During creator run
When funders are discovered:
- if funder already has fingerprint cluster with skip_policy='skip':
  - do not enqueue scan at all
  - treat as “known infra” and move on

---

## Expected Savings
This adds savings on top of wallet cache by reducing scans for:
- new-but-patterned wallets
- repeated infra/bot wallets with different addresses but same behavior

Typical improvement:
- wallet scans reduced by 30–70% further
- deep scans reduced by 80–95% further

Combined impact (cache + fingerprints):
- can reach 5–15 credits per token consistently in production

---

## Rollout Plan
1) Add tables wallet_fingerprints and fingerprint_clusters
2) Compute fingerprints opportunistically from existing scans (no extra calls)
3) Build clusters automatically when fingerprint_hash repeats (>= N wallets)
4) Start with conservative skip_policy rules (mostly 'shallow')
5) Promote to 'skip' only when confidence is very high and manual review confirms

---

## Validation Metrics
Add telemetry fields:
- fingerprint_match_rate
- scans_skipped_by_fingerprint
- scans_shallowed_by_fingerprint
- avg_pages_per_scan_before_vs_after

Target:
- fingerprint_match_rate >= 30% after 1–2 weeks
- avg_pages_per_scan drops by another 20–50%
