# OIP v2.2C.3 - Indexed Primitive Authority Projection and Downstream Equivalence

## Final verdicts

- **Authority Implementation:** A - INDEXED AUTHORITY PROJECTION VALIDATED
- **Downstream:** A - CURRENT-AUTHORITY DOWNSTREAM SEMANTICS VALIDATED
- **Compact Application:** READY_TO_RESUME_V2_2C
- **Acquisition:** HOLD_ACQUISITION

No acquisition was executed. There were zero RPC calls, production interactions, canonical
deletions, Primitive mutations, provenance deletions, Authority Contract changes, downstream
algorithm changes, identity actions, or governance actions.

## Frozen controls

| Population | Count |
|---|---:|
| Persisted Primitive ledger | 401,050 |
| Current authoritative | 346,730 |
| Historical snapshots | 14,626 |
| Legacy-version observations | 39,694 |
| Total non-authoritative | 54,320 |

Authority and clean replay are identical. Both ID digests are
`6e2bd05ce99979c4d397e173d741232a0074f2ac730c9e83b2138d8ecbb6d93e`;
authority-minus-clean and clean-minus-authority are both zero.

## Indexed authority design

The isolated prototype contains:

- `primitive_authority_events`: append-only authority decisions with Primitive, family, fixed-width
  authority-group ID, full group JSON, state, successor/current Primitive, reason, contract version,
  generator version, transition boundary, and recorded time.
- `indexed_current_primitive_authority`: deterministic view selecting `AUTHORITATIVE` events through
  the `(authority_state, primitive_id)` and `(authority_state, family, primitive_id)` indexes.
- `primitive_subject_index`: normalized `(subject, primitive_id, subject_order)` membership.
- `primitive_subject_cardinality`: indexed subject count per Primitive.
- `current_authority_subject`: indexed current `(subject, primitive_id)` projection.

The ledger contains 401,050 events. The current view returns 346,730 rows. All 54,320 historical or
legacy rows have a current-successor link. There are 671,092 persisted subject memberships and
616,444 current-authority memberships.

The first prototype indexed repeated authority-group JSON and attempted a duplicate materialized
current table. Both statements exceeded the ten-minute ceiling and were stopped. The accepted design
uses a SHA-256 group key for indexing, retains JSON only as audit payload, and avoids a duplicate bulk
current table. The failed prototype is not part of the application design.

## Query plans and performance

All required current paths are index-backed:

- all current: `authority_events_by_state`;
- family current: `authority_events_by_state_family`;
- one subject: primary key of `current_authority_subject`;
- canonical provenance: covering `(primitive_id, evidence_id)` primary index;
- compact provenance: external Primitive identity index, compact relation primary key, and Evidence
  integer primary key.

Subject lookup results:

| Selection | Primitives | Seconds |
|---|---:|---:|
| One subject | 33 | 5.354 |
| Known affected 206 subjects | 28,323 | 6.531 |
| Representative 1,000 subjects | 25,754 | 6.566 |

The normalized subject index was initially populated in 10,000-row committed checkpoints. Resume
does not rescan JSON or repeat the current-subject join.

Discovery evaluates only multi-subject Primitives. Applying this exact consumer predicate before
provenance hydration reduced the populations to 92,545 persisted and 92,217 authoritative
Primitives, with 372,209 and 370,299 provenance pairs respectively.

| Stage | All persisted | Current authority | Classification |
|---|---:|---:|---|
| Authority selection | 3.267s | 3.180s | ACCEPTABLE |
| Primitive/provenance hydration | 199.603s | 103.243s | SLOW_BUT_BOUNDED |
| Discovery | 10.768s | 18.089s | SLOW_BUT_BOUNDED |
| Motifs | 95.270s | 83.242s | SLOW_BUT_BOUNDED |
| Landscape and relationships | 161.019s | 127.541s | SLOW_BUT_BOUNDED |
| Total | 469.927s | 335.296s | SLOW_BUT_BOUNDED |

This completes where the old path failed before intelligence evaluation. No downstream algorithm
was changed; authority and multi-subject selection moved before provenance expansion.

## Compact provenance gate

The unchanged v2.2B compact representation still preserves all 12,398,192 historical provenance
relations. For the current authority subset, canonical and compact paths independently returned
6,457,475 external `(primitive_id, evidence_id)` pairs. Both ordered digests are
`831e909da0de874db28ba7cc066e6c50febfabe7c17489d0c0071217eea6a051`; set difference is zero.

Canonical streaming took 38.191s. Compact streaming took 280.072s. Compact correctness passes, but
the compact compatibility path is slower and must continue using direct integer-key joins rather
than the compatibility view.

## Discovery A/B

The all-persisted rerun reproduced the frozen control count of 44,475 candidates. Current authority
also produces 44,475 candidates.

All candidate IDs change because the authority-mode population digest is part of snapshot and
candidate identity. After removing snapshot/support identity fields, 44,269 candidates are
semantically unchanged and exactly 206 are replaced. Those 206 are precisely the subjects supported
by the 328 historical `REPEATED_COUNTERPARTY` snapshots. All 206 subjects remain represented under
current authority. Classification: **EXPECTED_AUTHORITY_CORRECTION**.

## Motif A/B

| Result | All persisted | Current authority |
|---|---:|---:|
| Canonical motifs | 4,367 | 4,365 |
| Shared motif IDs | 4,162 | 4,162 |
| Historical-only/current-only IDs | 205 | 203 |
| Shared semantic occurrence structures | 4,160 | 4,160 |
| Historical-only/current-only semantic structures | 207 | 205 |

The replaced structures are the expected consequence of removing earlier recurrence edges from the
206 corrected candidates. No unrelated subject population was added or removed.

## Relationship and evolution A/B

Both populations produce exactly 686 relationships. All 686 relationship IDs are shared, and their
type counts are identical:

- `SHARED_COUNTERPARTY_OBSERVATION`: 26;
- `SHARED_EVIDENCE_PROVENANCE`: 165;
- `SHARED_NEIGHBOURHOOD`: 165;
- `SHARED_PRIMITIVE_OBSERVATION`: 165;
- `SHARED_TEMPORAL_OBSERVATION`: 165.

Relationship observation/support snapshots change to current-authority candidates and motifs, but
the complete structural relationship set is unchanged. Operational-change, operational-evolution,
and relationship-evolution snapshots were regenerated successfully; their content-addressed IDs
change as expected because the supporting snapshot identities change.

## Historical reconstruction

Historical access remains explicit and complete. For example subject
`2rgMe1RGQLnUQ8mKK497f8poMTfJkq2Gw2MkC4opeVSe` exposes:

- legacy freshness `2b307b61bbad...` -> current `8df91381fddc...`;
- historical timing `0000f267f795...` -> current `2387969bd5e4...`;
- the current event-like observations alongside both supersession chains.

`ALL_PERSISTED`, `CURRENT_AUTHORITATIVE`, `HISTORICAL_SNAPSHOT`, and `LEGACY_VERSION` are separate,
validated query modes. Unknown modes and unknown Primitive families fail closed.

## Writer and recovery contract

Future generation must use one transaction for Primitive persistence, provenance persistence,
authority-event append, and current-group projection update. Event-like observations become current
only under an approved version. A new growing aggregate appends a transition and atomically replaces
the group pointer; the earlier Primitive and authority event remain immutable. A semantic version
does not supersede its predecessor without an explicit registry relationship.

Recovery recomputes the current group pointer from immutable events ordered by transition boundary,
then event ID. Duplicate replay is idempotent by event identity. The fixture proves interrupted
current-projection reconstruction, duplicate import, immutable event enforcement, deterministic
competing-snapshot resolution, historical access, subject membership equivalence, and unknown-family
failure. Twenty-one focused and EP2 regression tests pass.

## Migration control

The two compact gates now pass independently:

1. Historical gate: 12,398,192 persisted provenance pairs remain preserved exactly.
2. Application gate: 346,730 current-authority IDs equal clean replay; current compact provenance is
   exact; and Discovery, motifs, relationships, change, and evolution complete under authority-first
   hydration with only the expected 206-subject correction.

OIP v2.2C may resume its compact application validation using this indexed authority and subject
projection design. This does not authorize production cutover or acquisition. The 5,000-attempt
expansion remains blocked until controlled migration readiness is separately approved.
