# OIP v2.1F — Staged 1,000-Attempt Coverage Expansion

## Decision

**B — INCREASE TO 2,000-ATTEMPT BOUNDED BATCH**

The 5,000-call acquisition remains premature. Compact provenance migration priority is **HIGH**, but it remains a separate milestone.

## Acquisition

- Physical attempts: **1,000** exactly
- Recovered transactions: **1,000/1,000**
- Retries / failovers: **0 / 0**
- Helius latency: p50 **44.171 ms**, p95 **57.815 ms**, max **284.311 ms**
- Resume proof: attempts 1–100 were not repeated; numbering resumed at 101

## Coverage

- Complete launches: **998 → 1498** (**+500**)
- Completion: **3.114% → 4.675%**
- Remaining pending launches: **11,827**
- Remaining actionable dependencies: **23,653**

| Attempt range | Recovered | Completed | Completed/attempt |
|---|---:|---:|---:|
| 1-100 | 100 | 50 | 0.500 |
| 101-250 | 150 | 75 | 0.500 |
| 251-500 | 250 | 125 | 0.500 |
| 501-750 | 250 | 125 | 0.500 |
| 751-1000 | 250 | 125 | 0.500 |

Marginal completion yield is **STABLE**; paired missing-both launches hold at the expected 0.5 completions/attempt in every checkpoint segment.

## Downstream

- Evidence facts: **+134,995**
- Primitive observations: **+54,523**
- Provenance links: **+2,575,319**
- Discovery occurrences: **+6,174**
- Canonical motifs net: **+460**
- Relationships net: **+40**

`BEHAVIOURAL_TIMING` produced **2,458,217** new links (95.45%). The dominant Evidence→Primitive pair was `AccountParticipationFact → BEHAVIOURAL_TIMING` with **2,251,811** links.

## Storage

Total physical growth was **1,153,833,238 bytes** (**1,153,833 bytes/attempt**), below the accepted 1.67–2.78 GB planning range. This lowers the observed marginal cost but does not remove the canonical TEXT-key scaling concern.

## Validation

Primitive replay generated **240,253** observations on both passes with identical digest `ed8662ad214a717a1a81638b04ef1bd8d836a56ee491b9873f3f89d78a09d48a`; pass two inserted zero. Discovery, motif, and relationship expanded-corpus validators passed deterministically with zero RPC and zero production writes.
