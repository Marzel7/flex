# OIP v2.1G — Bounded 2,000-Attempt Coverage Expansion

## Decision

**PAUSE FOR COMPACT-PROVENANCE / PLATFORM OPTIMIZATION**

The 5,000-call acquisition status is **BLOCKED_PENDING_STORAGE_OPTIMIZATION**. Compact provenance migration priority is **BLOCKING_BEFORE_5K** and remains a separate milestone.

## Acquisition

- Physical attempts: **2,000** exactly
- Recovered transactions: **2,000/2,000**
- Retries / failovers: **0 / 0**
- Helius latency: p50 **48.120 ms**, p95 **66.636 ms**, max **193.945 ms**
- Resume proof: attempts 1–100 were not repeated; numbering resumed at 101

## Coverage

- Complete launches: **1498 → 2498** (**+1000**)
- Completion: **4.675% → 7.796%**
- Remaining pending launches: **10,844**
- Remaining actionable dependencies: **21,687**

| Attempt range | Recovered | Completed | Completed/attempt |
|---|---:|---:|---:|
| 1-100 | 100 | 50 | 0.500 |
| 101-250 | 150 | 75 | 0.500 |
| 251-500 | 250 | 125 | 0.500 |
| 501-750 | 250 | 125 | 0.500 |
| 751-1000 | 250 | 125 | 0.500 |
| 1001-1250 | 250 | 125 | 0.500 |
| 1251-1500 | 250 | 125 | 0.500 |
| 1501-1750 | 250 | 125 | 0.500 |
| 1751-2000 | 250 | 125 | 0.500 |

Marginal completion yield is **STABLE**; paired missing-both launches hold at the expected 0.5 completions/attempt in every checkpoint segment.

## Downstream

- Evidence facts: **+269,043**
- Primitive observations: **+108,113**
- Provenance links: **+4,331,458**
- Discovery occurrences: **+12,182**
- Canonical motifs net: **+812**
- Relationships net: **+98**

`BEHAVIOURAL_TIMING` produced **4,098,332** new links (94.62%). The dominant Evidence→Primitive pair was `AccountParticipationFact → BEHAVIOURAL_TIMING` with **3,758,589** links.

## Storage

Total physical growth was **2,040,669,559 bytes** (**1,020,335 bytes/attempt**), within the stage's bounded storage ceiling. This does not remove the canonical TEXT-key scaling concern.

## Validation

Primitive replay generated **346,730** observations on both passes with identical digest `3bce7576382773132ab0694135962895aadd1bd5d30b407aca3265d194274c7b`; pass two inserted zero. Discovery, motif, and relationship expanded-corpus validators passed deterministically with zero RPC and zero production writes.
