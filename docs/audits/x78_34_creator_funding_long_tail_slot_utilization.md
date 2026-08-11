# X78.34 — Creator Funding Long-Tail Extraction & Slot Utilization Reduction

Date: 10 August 2026  
Branch: `classification-attribution-axis`  
Baseline HEAD: `cb1fc110e105436c4baa9fe15f956628f80db3ce`  
Production worker: PID 99601, exactly 2 extraction slots  
RPC ceiling: 8 (unchanged)

Machine-readable qualification: [x78_34_qualification.json](./x78_34_qualification.json)

## Outcome

The fixed five-row completion barrier was removed. The worker now uses bounded
rolling admission with at most two extracting rows and two claimed reserve /
enriching rows. Authoritative funding extraction and terminal queue accounting
remain inside the scarce extraction slot. Best-effort post-extraction work runs
after slot release while the creator-keyed single-flight remains held.

This safely removes scheduler tail and post-extraction slot occupancy, but it
does **not** make two-slot capacity sufficient. The remaining deficit is the
full-extraction long tail, principally outgoing-transfer history and creator
funding history.

## Frozen 30-minute qualification

| Measurement | Result |
|---|---:|
| Observation duration | 1,800s |
| HOT arrivals | 138 (276/hour) |
| Live completions | 111 (222/hour) |
| Capacity ratio | 0.8043 |
| HOT ready | 887 → 820 |
| Oldest HOT | 21,595s → 21,550s |
| 3–6h cohort | 448 → 518 |
| Expiries | 500 |
| Full / fast completions | 108 / 3 |
| Full extraction p50 / p95 / max | 21.526s / 65.153s / 90.011s |
| Jobs >30s / >60s | 35 / 10 |
| Timeout retries | 6 |
| WAL final | 0.185 MB |

HOT depth appears to fall only because 500 rows expired. The 3–6h cohort grew
by 70. This is expiry-masked deficit, not catch-up.

## Exact production path

`_recover_stale_and_claim` → `_run_rolling_claim_window` →
`_run_creator_scoped_row` → `_process_job` →
`extract_funding_for_new_token` → authoritative `creator_funders` check →
`RealTimeCreatorFundingExtractor.process_new_token` (history, transaction
acquisition, parsing and funding persistence) → concurrent Jito / deBridge /
Axiom / outgoing checks → terminal queue transition → extraction-slot release
→ second-hop enqueue → risk scoring → network assignment → intelligence
refresh → creator-flight release.

## Phase evidence

115 full-job ledgers were captured. Aggregate timed work:

| Phase | Total |
|---|---:|
| Outgoing-transfer scan | 1,960.261s |
| Creator history and funding | 1,058.739s |
| Jito | 11.450s |
| Authoritative-state read | 1.886s |
| deBridge | 1.041s |
| Axiom | 1.012s |

Representative observed paths included:

- 56.6s: 46.1s creator history + 9.8s outgoing scan;
- 43.4s: 8.6s creator history + 34.7s outgoing scan.

The dominant signature is `OUTGOING_TRANSFER_SCAN`, with
`DEEP_HISTORY_PAGINATION` a material secondary cause. The timeout cohort is
`MIXED`: both phases can dominate, rather than one common provider failure.

## RPC semaphore and provider evidence

Across 9,630 semaphore-gated calls, cumulative semaphore wait was 59.356ms and
the maximum single wait was 2.339ms. Semaphore contention is operationally
zero relative to job duration.

| Provider / method | Calls | p50 | p95 | max | retry / 5xx |
|---|---:|---:|---:|---:|---:|
| Helius `getTransaction` | 7,658 | 50ms | 125ms | 12.36s | 0 / 0 |
| Helius `getSignaturesForAddress` | 2,404 | 62ms | 164ms | 12.59s | 0 / 0 |
| Bonfida SNS | 1,168 | 76ms | 209ms | 2.19s | 0 / 0 |
| Helius enhanced address history | 356 | 592ms | 1.67s | 13.94s | 0 / 0 |
| Helius oldest transaction | 113 | 266ms | 689ms | 2.94s | 0 / 0 |

There was one enhanced-history 4xx and no 5xx storm. Provider policy, retry
semantics, the 90s timeout and the RPC ceiling were not changed.

## Slot contract and safety

- Extraction slot: held only through `_process_job`, including authoritative
  persistence and terminal queue state.
- Creator single-flight: remains held through all post-extraction enrichment.
- Rolling ownership: two extracting + at most two reserve/enriching rows.
- Claiming remains transactional, HOT-only and one deep row per creator.
- No SQLite transaction is shared across slots.
- Shutdown cancels and joins tasks owned by the bounded window; unstarted
  claims do not exceed the two-row reserve and remain recoverable under the
  existing stale-running contract.

No provider work was dropped or semantically altered. Outgoing-transfer work
is proven to be the main long-tail cost, but it is consumed by discovery,
Watchtower and creator relationship projections. Deferring it durably requires
a separately approved queue/ownership contract; it was therefore not moved or
silently skipped in X78.34.

## Validation

49 focused tests passed across X78.34 instrumentation/refill, X78.32 dedupe,
X78.31 fast paths, X78.30 freshness, X78.29 accounting, X78.17 read boundaries,
X78.14 cancellation cleanup and X76.3 extractor concurrency. A separate
X78.22 fail-open assertion was already inconsistent with the current inherited
log wording and was not caused by X78.34; its other SQL-boundary tests passed.

Live end state:

- Creator Funding: running, heartbeat fresh, capacity warning;
- Creator Resolution: running and progressing;
- Operational Intelligence: fresh, warning inherited from Creator Funding;
- listener/API/ingestion: healthy and connected;
- database: healthy, write p99 633ms, zero recent lock errors;
- Token Prediction: decommissioned;
- Broad Price Tracking: decommissioned.

## Final verdicts

Long-Tail Extraction: **C — LONG_TAIL_REMAINS_WITH_IDENTIFIED_CAUSE**

Slot Utilization: **A — CONTINUOUS_REFILL_VALIDATED**

Two-Slot Capacity: **C — STILL_INSUFFICIENT_LONG_TAIL**

HOT Queue: **D — EXPIRY_MASKING_DEFICIT**

Production Health: **C — HEALTHY_SERVICES_CAPACITY_NOT_READY**

Evidence Activation: **HOLD**

Acquisition: **HOLD_ACQUISITION**

Residual cause: `LONG_TAIL_HISTORY_DEPTH` + `OUTGOING_TRANSFER_SCAN`.
A third slot is not authorized by this milestone.
