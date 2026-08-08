# OIP v2.1B — Acquisition Efficiency & Coverage Optimization

## Verdict

**C — Retry/failover refinement required**

This milestone used only the completed v2.1A corpus. It issued zero RPC calls,
created no new coverage, and changed no Evidence, Primitive, Runtime, Discovery,
Motif, relationship, production, or UI semantics.

## Provider failure analysis

The v2.1A label `PROVIDER_UNAVAILABLE` is too coarse to support causal analysis.
The 394 unsuccessful responses were retained only in the interrupted process's
memory. Their status codes, RPC error bodies, exception types, and latencies were
not written to durable telemetry. Therefore all 394 are individually classified:

`UNKNOWN_TELEMETRY_NOT_RETAINED`

The complete signature/launch/dependency census is preserved in
`oip_v2_1b_provider_failure_census.json`. This is not evidence that the
transactions are historically unavailable. Their recoverability cannot be
determined from retained pilot data.

The bounded runner now durably records every future attempt before downstream
processing. The fixed taxonomy distinguishes recovered, null transaction,
timeout, rate limiting, provider HTTP failure, malformed request, retryable RPC
error, terminal RPC error, malformed response, transport error, and unknown.
It does not change retry or provider selection.

## Retry and provider comparison

The pilot used `request_once`: zero retries and zero failovers. Consequently:

- Additional-retry recovery: not measured.
- Delayed-retry recovery: not measured.
- Public RPC recovery: not measured.
- Existing failover recovery: not measured.
- Helius first-attempt recovery: 606/1,000 (60.6%).

The shared acquisition layer already contains the production-compatible
sequential retry/failover mechanism, but v2.1A intentionally bypassed it so one
logical target could not exceed the 1,000-attempt ceiling. A future validation
must budget physical attempts, not logical signatures, and persist every
attempt. No additional provider is justified by the present data.

## Dependency yield

| Dependency | Calls | Transactions recovered | Transaction yield |
|---|---:|---:|---:|
| Creation | 459 | 277 | 60.35% |
| Migration | 541 | 329 | 60.81% |

| Launch class | Launches | Calls | Completed | Completions/call |
|---|---:|---:|---:|---:|
| Missing creation and migration | 459 | 918 | 274 | 0.2985 |
| Missing migration only | 82 | 82 | 53 | 0.6463 |

Migration-only launches returned 2.17 times more completed launches per call.
They are therefore the highest-value dependency class for the next bounded
validation. This prioritization changes acquisition order only; it does not
change population membership or coverage semantics.

## Storage analysis

The Evidence database grew from 777,375,744 to 1,370,628,096 allocated bytes:
593,252,352 bytes total, or 1,814,227 bytes per completed launch.

Measured logical growth:

- Compressed artifacts: 3,015,064 bytes.
- Raw artifact size represented: 18,791,081 bytes.
- Evidence payloads: 36,881,481 bytes.
- Primitive payloads: 39,658,186 bytes.
- Primitive-to-Evidence links: +1,363,402 rows.

Artifact bytes are not the cause of the 593 MB increase. Physical SQLite
inspection attributes 412,975,104 bytes of growth to the
`primitive_evidence_inputs` table and its primary-key index. Primitive rows and
their index add another 68,961,872 bytes. Together those structures account for
about 81.2% of total allocated growth. This is provenance-link amplification,
not artifact duplication.

Potential optimization must remain physical only: page/index layout,
`WITHOUT ROWID` evaluation, compact immutable reference encoding, or separate
content-addressed reference blocks. None is approved here because Evidence and
Primitive persistence contracts are frozen.

## Stage telemetry

| Stage | v2.1A measurement |
|---|---|
| Acquisition | Unavailable after process interruption |
| Mirror | Unavailable as an independent duration |
| Normalization | Unavailable as an independent duration |
| Primitive replay | 232.408 seconds |
| Discovery | Deterministic |
| Motifs | Deterministic |
| Relationships | Deterministic |

Future bounded execution now writes per-attempt classification and latency to a
durable shadow telemetry log before mirroring. The next runner must also persist
stage start/end observations independently so downstream replay cannot erase or
mask acquisition timing.

## Yield model

| KPI | Measured result |
|---|---:|
| Completed launches / RPC | 0.327 |
| Completed launches / credit | 0.0327 |
| Evidence facts / RPC | 82.99 |
| Primitive observations / RPC | 83.017 |
| Discovery occurrence gain / RPC | 3.945 |
| New motif IDs / RPC | 0.956 |
| New relationship IDs / RPC | 0.139 |
| Net relationships / RPC | -0.007 |
| Allocated storage / RPC | 593,252 bytes |

New IDs are replay differences, not claims of new identity. Recanonicalization
removed 134 former motif IDs and relationship replay removed 146 former IDs.

## Updated acquisition strategy

Do not authorize 5,000 calls yet.

The next approved experiment should remain at or below 1,000 physical attempts:

1. Select migration-only dependencies first, with representative control strata.
2. Persist every attempt's status, RPC code, exception class, latency, provider,
   retry ordinal, and correlation ID.
3. Use a fixed physical-attempt budget shared by retry and failover.
4. Compare no-retry, delayed-retry, and existing-failover cohorts using matched
   dependency/date/provider-source strata.
5. Stop automatically at the budget and produce separate acquisition, mirror,
   normalization, primitive, discovery, motif, and relationship timings.
6. Reassess physical storage before scaling beyond 1,000 attempts.

Only if that experiment classifies every outcome and demonstrates higher
completion yield without disproportionate storage growth should staged 1,000-call
batches or a 5,000-call batch be considered.
