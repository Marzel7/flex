# OIP v2.1C — Bounded Retry & Failover Validation

## Result

**B — READY FOR STAGED 1,000-CALL BATCHES**

The experiment used **270 physical attempts**, all against Helius, and recovered **270/270 transactions**. The matched cohorts each recovered 90 transactions and completed 50 launches. No delayed retry or failover was triggered because every initial request succeeded.

| Policy | Attempts | Recovered | Completed launches | Recovery/attempt | Launches/attempt | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| NO_RETRY | 90 | 90 | 50 | 1.0000 | 0.5556 | 50.286 | 63.715 |
| DELAYED_RETRY | 90 | 90 | 50 | 1.0000 | 0.5556 | 50.343 | 63.725 |
| EXISTING_FAILOVER | 90 | 90 | 50 | 1.0000 | 0.5556 | 50.258 | 61.824 |

## Interpretation

The 394 v2.1A outcomes labelled provider-unavailable were not shown to be permanent historical gaps. In this later matched replay, every selected signature was available on the first Helius request. This establishes temporal first-attempt recovery, but does not identify which original failures were transient because v2.1A retained no attempt telemetry.

Retry and failover produced **zero incremental recovery per additional attempt** because they consumed zero additional attempts. The evidence-supported next policy is staged 1,000-attempt acquisition using no retry for successful first attempts, while retaining class-specific retry/failover instrumentation for future observed timeout, availability, rate-limit, transport, or RPC failures.

## Downstream Yield

- Evidence facts added: **36,435**
- Primitive observations added: **14,546**
- Primitive evidence inputs added: **968,304**
- Discovery occurrences net: **1,728**
- Canonical motifs net: **156**
- Relationships net: **82**
- Completed launches: **150**

## Performance and Storage

Provider latency was p50 **50.286 ms**, p95 **63.252 ms**, max **152.900 ms**. Mirror took **0.311s**, normalization **31.457s**, Primitive pass one **94.847s**, and deterministic pass two **34.843s**.

Incremental physical storage was **601,097,366 bytes**, or **2,226,287 bytes/attempt**. The `primitive_evidence_inputs` table added **968,304** rows, **66.57 inputs per new Primitive**, confirming provenance amplification remains dominant.

## Validation

Primitive replay generated the same **132,886** observations on both passes with digest `33346f90a48f6fb2086da557ec2d7889d119e5b69db524cd4cf6fca7b62b8219`; pass two inserted zero. Discovery, motif, operational-change, evolution, and relationship validators all passed without RPC or production writes. Resume repeated zero provider requests, and the tested budget guard rejects attempt 1,001.

No production interaction occurred. Evidence, Primitive, Runtime, Discovery, motif, relationship, identity, and governance semantics remained frozen.
