# OIP v2.2C — Compact Provenance Application-Path Equivalence

## Verdicts

- **Application:** C — APPLICATION DIFFERENCE FOUND
- **Migration:** NOT_READY
- **Acquisition:** HOLD_ACQUISITION
- **Canonical retirement:** RETIREMENT_NOT_SAFE

## Scope and controls

The audit used the frozen v2.1G source at `database/evidence_platform/oip_v2_1g_stage_2000_frozen/evidence.db`, containing 807,545 Evidence records, 401,050 canonical Primitive observations, and 12,398,192 provenance links. The existing v2.2B compact relation and controls were reused. No RPC, acquisition, production interaction, canonical write, provenance deletion, or semantic change occurred.

The v2.2B free-space note is corrected: unrelated local storage was manually removed outside that audit. Its filesystem before/after delta is not compact-provenance savings. The direct 3,816,488,960-byte canonical versus 521,879,552-byte compact object measurement remains authoritative.

## Compact repository

`CompactProvenanceRepository` exposes external Primitive/Evidence IDs while keeping integer keys private. Its query paths resolve the external identity first and then use the compact primary/reverse indexes. It supports Primitive-to-Evidence, Evidence-to-Primitive, pair existence, ordered full iteration, transactional new-identity insertion, and idempotent duplicate insertion.

Fixture validation passed for shared Evidence, multiple Primitives, duplicate replay, new identities, transactional interruption, and missing/corrupt mapping failure. The naïve compatibility view is not used as the primary adapter because v2.2B demonstrated pathological join planning.

## Full replay result

Two clean full replays ran from the same 807,545 frozen Evidence rows. Both generated exactly 346,730 observations and 6,457,475 provenance relationships with identical Primitive and provenance digests. Pass two inserted zero Primitive rows; the relation output was unchanged.

That deterministic replay does **not** equal the canonical accumulated population:

| Family | Canonical | Clean replay | Difference |
|---|---:|---:|---:|
| BEHAVIOURAL_TIMING | 82,207 | 67,909 | 14,298 |
| WALLET_FRESH_AT_EVENT | 226,295 | 186,601 | 39,694 |
| REPEATED_COUNTERPARTY | 1,055 | 727 | 328 |
| All other families | 91,493 | 91,493 | 0 |

Canonical total is 401,050 versus 346,730 from clean replay, a difference of 54,320 observations. Canonical provenance is 12,398,192 links versus 6,457,475 regenerated links.

Primitive generation consumes normalized Evidence, not stored provenance. The mismatch therefore occurs before the compact repository participates. The most likely interpretation is accumulated incremental derived state in the frozen canonical table, but this audit does not promote that explanation from inference to fact. The first failing gate is Phase 25 canonical Primitive comparison.

## Downstream gate

Discovery, motif, relationship, operational-change, and evolution equivalence were not rerun after the prerequisite failure. Using the frozen 401,050 Primitive population with the already identical compact relation should reproduce the persisted downstream controls, but that is an inference, not the clean-replay proof required by the brief. Expensive validators cannot repair a failed Primitive baseline.

Canonical controls remain 44,475 Discovery candidates, 4,367 motifs, and 686 relationships. They are unchanged; no compact-backed output is claimed equivalent in v2.2C.

## Performance and storage

Full clean generation took 97.35 seconds on pass one and 110.24 seconds on pass two. The isolated replay database reached 333,836,288 bytes. The v2.2B compact provenance database remains 521,879,552 bytes, with its direct 86.33% saving unchanged.

Measured v2.2B adapter-shaped queries showed exact-pair parity, a much faster indexed reverse lookup, and slower high-fan-out Primitive expansion due to identity resolution. This performance requires further application-path work but is secondary to the replay mismatch.

The existing 5,000-attempt persistent projection remains approximately 1.78–2.67 GB after compaction, before replay, journal, validator, and safety-reserve headroom. Acquisition remains held because migration is not ready.

## Migration and rollback

No sidecar cutover is authorized. The proposed future mechanism remains: canonical writes during sidecar build, cursor-checkpointed batches, a captured final delta, a bounded write pause, count/digest/anti-join verification, atomic reader/writer switch, and canonical fallback through a soak period. This design cannot advance until canonical clean-replay expectations are defined and proven.

Canonical provenance must remain available. Retirement is not safe.
