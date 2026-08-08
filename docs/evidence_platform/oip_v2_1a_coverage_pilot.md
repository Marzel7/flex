# OIP v2.1A — Coverage Expansion Pilot

## Decision

**C — Refine acquisition strategy first**

The pilot respected the frozen 32,042-launch population, issued exactly 1,000
`getTransaction` requests, consumed the 10,000-credit ceiling, and did not
continue automatically. Six launches born while the pilot was running were
excluded using the frozen source-row boundary (`rowid <= 1,615,500`).

Full recovery is not yet justified. Only 606 requests returned replayable
transactions, 394 were provider-unavailable, and 327 launches became complete.
The expanded corpus produced useful intelligence, but also increased motif
fragmentation and consumed 593 MB of SQLite storage.

## Sampling

The deterministic sampler selected complete launch-dependency groups using 22
strata across launch-date quartile, missing dependency, provider source,
WATCHTOWER membership, runtime-ready state, and discovery participation. It
covered 541 launches and 541 distinct creators. Discovery occurrence membership
was explicitly unavailable in the immutable summary snapshot, so it was retained
as an `UNAVAILABLE` stratum rather than inferred.

| Dimension | Calls | Recovered |
|---|---:|---:|
| Creation transaction | 459 | 277 |
| Migration transaction | 541 | 329 |
| Missing both dependencies | 918 | 553 |
| Missing migration only | 82 | 53 |
| WATCHTOWER | 1 | 1 |
| Non-WATCHTOWER | 999 | 605 |

Date bands returned 169/281, 151/255, 104/150, and 107/178; unknown-date
records returned 75/136. Provider-source yields were RECONCILER 161/251,
WEBSOCKET 203/349, pumpfun 164/262, and UNKNOWN 78/138.

## Measured yield

| Measurement | Result |
|---|---:|
| RPC calls / credits | 1,000 / 10,000 |
| Transactions recovered | 606 |
| Provider unavailable | 394 |
| Runtime-ready launches gained | 327 |
| Immutable Evidence added | 82,990 |
| Primitive observations added | 83,017 |
| Discovery occurrences gained | 3,945 |
| New motif IDs | 956 |
| Removed motif IDs after recanonicalization | 134 |
| New relationship IDs | 139 |
| Removed relationship IDs after replay | 146 |

Efficiency was 0.327 complete launches per RPC, 0.0327 per credit, 82.99 facts
per RPC, and 83.017 primitives per RPC. Primitive replay took 232.408 seconds
(0.232408 seconds per RPC-equivalent) and was deterministic: the second pass
inserted zero observations with an identical input digest.

The database grew by 593,252,352 bytes (593,252 bytes per RPC). Acquisition
latency and throughput are unavailable because the acquisition process was
interrupted after all responses were durably mirrored. That missing telemetry is
itself an acceptance gap and must be corrected before a larger batch.

## Downstream intelligence

Discovery increased from 14,203 to 18,148 occurrences. Motifs increased from
1,379 to 2,201; 1,518 are singletons, and 166 motifs are now needed to cover 80%
of activity. The fixed top-69 set explains 67.64%, down from roughly 80% in the
baseline. Relationship replay ended at 341 relationships versus 348 previously:
139 IDs were created and 146 retired. These are deterministic structural changes,
not identity or governance conclusions.

The Operational Landscape read adapter now uses the pilot EP4.3/EP4.4 snapshots
when present and labels them `coverage pilot · non-authoritative`. It falls back
to the frozen v1 reports when pilot snapshots are unavailable.

## Phase 2 recommendation

Before another acquisition batch:

1. Diagnose the 394 provider-unavailable responses by response class and age.
2. Preserve acquisition latency/throughput telemetry independently of downstream replay.
3. Prioritize dependency groups capable of completing a launch, while retaining
   representative control strata.
4. Measure and reduce the 593 KB-per-call storage amplification.
5. Re-run a bounded batch with explicit provider retry/failover policy inside the
   same hard credit ceiling.

No WATCHTOWER or 3SW2 source corpus was modified. No production consumer,
governance state, attribution, or canonical identity changed.
