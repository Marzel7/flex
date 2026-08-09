# OIP v2.2A — Historical Intelligence Retention Audit

## Executive conclusion

The frozen v2.1G corpus supports cold tiering, but it does **not** support deletion. A measurable derived population does not participate in current Discovery/motifs, yet the corpus lacks authoritative Operation, investigation, anomaly, governance, and complete recurring-actor consumer state. Treating those absences as non-use would create permanent blindness.

**Retention verdict:** B — COLD TIERING JUSTIFIED, DELETION NOT JUSTIFIED
**Provenance priority:** COMPACT_PROVENANCE_FIRST
**Acquisition gate:** READY_FOR_5K_AFTER_STORAGE_MILESTONE

## Frozen population

- 32,044 launches; 2,498 completed launches.
- 807,545 Evidence records.
- 401,050 Primitive observations and 12,398,192 provenance links.
- 44,475 Discovery candidates, 4,367 motifs, and 686 relationships.
- Corpus: 5,725,122,560 bytes; Primitive/provenance core: 2,131,554,304 bytes.
- Frozen-relative launch ages: {'>90d': 4200, '8-30d': 11921, '0-7d': 4013, '31-90d': 11076, 'UNKNOWN': 834}. Creator recurrence: 2,615 recurring creators cover 15,650 launches; 14,508 creators occur once.
- Zero RPC, production reads/writes, canonical deletions, or semantic changes.

## Family inventory

| Primitive family | Rows | Provenance links | Discovery/motif supporting | No Discovery/motif participation |
|---|---:|---:|---:|---:|
| BEHAVIOURAL_TIMING | 82,207 | 11,573,246 | 0 | 82,207 |
| DIRECT_COUNTERPARTY | 40,960 | 40,960 | 40,958 | 2 |
| ECONOMIC_FUNDING | 644 | 1,288 | 644 | 0 |
| LAUNCH_ACTIVATION | 644 | 4,508 | 644 | 0 |
| LAUNCH_SIGNER | 2,602 | 5,204 | 2,602 | 0 |
| REPEATED_COUNTERPARTY | 1,055 | 5,012 | 1,055 | 0 |
| SHARED_TRANSACTION | 9,614 | 196,215 | 9,614 | 0 |
| SYSTEM_TRANSFER | 27,333 | 109,331 | 27,332 | 1 |
| WALLET_FRESH_AT_EVENT | 226,295 | 452,732 | 0 | 226,295 |
| WSOL_CLOSE | 9,696 | 9,696 | 9,696 | 0 |

`BEHAVIOURAL_TIMING` contains 82,207 rows and 11,573,246 links; 0 rows support Discovery/motifs and 82,207 do not. Non-participation is a query-path observation, not proof of no future value.

## Consumer and safety analysis

Discovery participation was reconstructed using the frozen `DiscoveryEngine` rule: subjects with at least two multi-subject observations produce candidates, and all observations for that subject become supporting primitives. Motif canonicalization creates one occurrence per candidate and carries those same supporting primitive IDs. Relationship participants are therefore already protected by motif protection.

The shadow database contains no canonical Discovery, motif, relationship, Operation, investigation, anomaly, or governance tables. Its validator reports establish deterministic aggregate outputs, but only Primitive/Evidence dependencies are queryable at row level. The mandatory safety overrides consequently cannot all be evaluated. The apparent isolated cohort (308,505 Primitive rows; approximately 12,025,983 links) is **not eligible** for dematerialization.

## Reconstruction

v2.1G recorded deterministic Discovery and motif replay. v2.1D proved exact compact-provenance relation identity across 3,495,337 links. That result supports a representation migration, but it is not silently promoted to proof for the enlarged v2.1G relation. A current-corpus compact migration must repeat the digest and lookup validation.

The shadow-dematerialization gate remained closed: no corpus copy was created and no row was removed. Exact protected motif/relationship equivalence after dematerialization therefore remains untested, as required when the safe cohort cannot be established.

## Retention model

- **PERMANENT:** immutable artifacts, Evidence, launch identity, signatures, timestamps, and explicit historical relationships.
- **HOT:** every Operation/motif/relationship participant; recurring actors and shared infrastructure; active, unresolved, investigated, anomalous, governed, predecessor, and successor structures.
- **COLD:** reconstructable historical derived state outside active queries, while actor and Evidence indexes remain searchable for reactivation.
- **RECOMPUTABLE:** version-pinned derived outputs whose full Evidence dependency manifest is retained.
- **DEMATERIALIZABLE:** only after every safety override is queryable and current-corpus rematerialization proves exact Primitive, Discovery, motif, and relationship equivalence.

## Storage direction

The prior compact prototype proved an exact relation-preserving representation and remains the lower-risk first storage milestone. The linear no-change projection for the remaining 21,687 attempts is 27,853,122,923 bytes. Projections are directional: changing corpus composition can change both provenance density and compression yield.

After current-corpus compact-provenance validation, the acquisition evidence supports the 5,000-call stage. Retention tiering should proceed as metadata and query-path design first; deletion remains blocked until the missing protection registries and rematerialization proof exist.
