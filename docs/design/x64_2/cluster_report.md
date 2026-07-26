# X64.2 — Phase 5: Timing Analysis (Provisioning Bursts)

All timestamps use `funder_block_time` (the actual on-chain funding
transaction time), not `completed_at` (walkback processing time, which
lags the real event and would distort clustering). One row
(`HTog7L8RFmgvza1hGg6hWnQncxeViedNyy6zPUwNpump`, funded
2026-07-08T16:21:01) is excluded from cluster analysis — see
`disposable_wallet_analysis.md` and `falsification.md` for why it is not
representative of this cohort's timing. The remaining 17 span
2026-07-19T01:58:30 → 2026-07-20T18:23:17 (40.4 hours).

## Sorted funding times (17, outlier excluded)

```
2026-07-19T01:58:30  AGumPoj6j…   Dbvr7ktCbxq…
2026-07-19T21:02:44  61BtvdXL…    FZFM6roR4…
2026-07-20T05:23:30  A9TJYUgp…    F6hrtsQbY…
2026-07-20T06:47:51  51bLwxUw…    CQYN8HpSj…
2026-07-20T09:21:00  F74webej…    9gTRxKUiG…
2026-07-20T11:55:41  3uJNC2pJ…    GxyGhyQKv…
2026-07-20T12:26:00  F8dWKhaK…    G9dYo6spsE…
2026-07-20T13:33:20  HHcXBLbn…    HXMUxU94Z…
2026-07-20T14:31:55  Q3WvTW8d…    AU3CFDUay…
2026-07-20T14:45:26  CvP9vVUC…    DCyQJVfAL…
2026-07-20T14:45:43  9NqjcpGC…    Q6rUf193C…
2026-07-20T14:47:50  DxRJpsVN…    7gkGAKgr1…
2026-07-20T14:54:23  EXn2aNzt…    HBoQ8iQX6…
2026-07-20T16:43:27  9rvQ2wcq…    DuXAsBkoY…
2026-07-20T16:57:54  6UXXyzvn…    Dq54F75j5…
2026-07-20T17:16:08  8D9ncyi7…    2rxo9N5g4…
2026-07-20T18:23:17  8wpoG9gb…    GCzbZ4sam…
```

## Cluster analysis at each granularity

- **30 seconds**: `CvP9vVUC…` (14:45:26) and `9NqjcpGC…` (14:45:43) are
  17 seconds apart — the only pair within 30s. Different creators,
  different disposable wallets. No shared evidence beyond proximity.
- **1 minute**: same pair only.
- **5 minutes**: a 4-launch window from 14:45:26 to 14:54:23 (8m57s span,
  entries `CvP9vVUC…`, `9NqjcpGC…`, `DxRJpsVN…`, `EXn2aNzt…`) — this is
  the single densest window in the dataset. All 4 have distinct creators
  and distinct disposable wallets; no pairwise shared evidence found.
- **30 minutes**: the same 4-launch cluster remains the only grouping
  denser than the dataset's average spacing; no additional launches fall
  within 30 minutes of it on either side (next launch before it is
  13:33:20, ~72 min earlier; next after is 16:43:27, ~109 min later).
- **1 hour**: no change from the 30-minute grouping.

## Baseline comparison (falsification-relevant)

17 launches over 40.4 hours is an average of one qualifying launch
(passing the X64 pattern specifically — not all pump.fun launches) every
~2.4 hours. A single 4-launch, 9-minute window is a real local density
spike relative to that average, but:
- It contains **zero shared wallets, zero shared creators, zero shared
  funding amounts** — the only thing the 4 rows share is proximity in
  time.
- Pump.fun's overall launch rate (all tokens, not just X64-qualifying
  ones) is high enough that a 9-minute window containing 4 *unrelated*
  WSOL_WRAP_CLOSE-funded creators, purely from population density, cannot
  be ruled out from this data alone — no RPC-free way exists to compute
  the platform's baseline WSOL_WRAP_CLOSE-funded-launch rate for a true
  statistical comparison (flagged as a gap, not estimated).

## Conclusion for this phase

**No coordinated provisioning wave, sequential disposable-wallet creation
pattern, or synchronized multi-creator launch is evident in the stored
data.** The one local density spike (4 launches in 9 minutes) is
timing-only — it does not correlate with any shared wallet, amount, or
creator evidence, and is not distinguishable from ordinary
platform-level launch-rate variance given the data available. See
`falsification.md` for the formal rejection of "coordinated burst" as an
explanation for this cluster.
