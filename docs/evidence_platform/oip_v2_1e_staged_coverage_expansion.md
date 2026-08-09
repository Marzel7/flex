# OIP v2.1E — Staged 1,000-Attempt Coverage Expansion

## Decision

**A — CONTINUE ANOTHER STAGED 1,000 ATTEMPTS**

The 5,000-call acquisition remains premature. Compact provenance migration priority is **HIGH**, but it remains a separate milestone.

## Acquisition

- Physical attempts: **1,000** exactly
- Recovered transactions: **1,000/1,000**
- Retries / failovers: **0 / 0**
- Helius latency: p50 **43.273 ms**, p95 **59.893 ms**, max **310.001 ms**
- Resume proof: attempts 1–100 were not repeated; numbering resumed at 101

## Coverage

- Complete launches: **477 → 998** (**+521**)
- Completion: **1.489% → 3.114%**
- Remaining pending launches: **12,327**
- Remaining actionable dependencies: **24,653**

| Attempt range | Recovered | Completed | Completed/attempt |
|---|---:|---:|---:|
| 1-100 | 100 | 71 | 0.710 |
| 101-250 | 150 | 75 | 0.500 |
| 251-500 | 250 | 125 | 0.500 |
| 501-750 | 250 | 125 | 0.500 |
| 751-1000 | 250 | 125 | 0.500 |

Marginal completion yield is **STABLE** after the completion-rich first 100 attempts; paired missing-both launches hold at the expected 0.5 completions/attempt.

## Downstream

- Evidence facts: **+135,859**
- Primitive observations: **+54,862**
- Provenance links: **+1,996,078**
- Discovery occurrences: **+6,243**
- Canonical motifs net: **+738**
- Relationships net: **+125**

`BEHAVIOURAL_TIMING` produced **1,879,042** new links (94.14%). The dominant Evidence→Primitive pair was `AccountParticipationFact → BEHAVIOURAL_TIMING` with **1,711,403** links.

## Storage

Total physical growth was **966,847,284 bytes** (**966,847 bytes/attempt**), below the accepted 1.67–2.78 GB planning range. This lowers the observed marginal cost but does not remove the canonical TEXT-key scaling concern.

## Validation

Primitive replay generated **186,685** observations on both passes with identical digest `e8b13018a916920001c1bd82cdcbf9c0c1b00c2a50bc3add62e31099f67b2ab7`; pass two inserted zero. Discovery, motif, and relationship expanded-corpus validators passed deterministically with zero RPC and zero production writes.
