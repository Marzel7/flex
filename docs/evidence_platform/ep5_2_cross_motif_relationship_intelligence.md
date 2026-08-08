# EP5.2 — Cross-Motif Relationship Intelligence

## Outcome

EP5.2 materializes immutable, typed, evidence-supported relationships between
motifs and tracks their objective evolution. It consumes only relationships and
support already recorded in frozen operational landscape snapshots.

It does not search for similar addresses, create new discovery links, infer
ownership, infer common control, classify Operations, or execute governance.

## Relationship boundary

A relationship observation exists only when an EP4.4 relationship has recorded
one or more exact support classes. EP5.2 translates that support into these
objective types:

- `SHARED_EVIDENCE_PROVENANCE`
- `SHARED_PRIMITIVE_OBSERVATION`
- `SHARED_FUNDING_OBSERVATION`
- `SHARED_COUNTERPARTY_OBSERVATION`
- `SHARED_INFRASTRUCTURE`
- `SHARED_TOPOLOGY`
- `SHARED_BEHAVIOUR`
- `SHARED_CADENCE`
- `SHARED_TEMPORAL_OBSERVATION`
- `SHARED_NEIGHBOURHOOD`

Absent recorded support, no relationship is produced. The relationship means
only that the two motifs share the named observation class.

```text
Shared behaviour       != shared control
Shared infrastructure  != common ownership
Relationship           != identity
```

## Immutable contract

Every `MotifRelationship` records:

- stable relationship ID and immutable observation ID;
- relationship and replay versions;
- the exact source landscape and motif pair;
- supporting Evidence, Primitive, behaviour, topology, temporal, and
  infrastructure references;
- observation window;
- supporting observation and motif counts;
- measured duration;
- Evidence and Primitive completeness;
- observed dormancy state.

No confidence or probability is calculated.

`RelationshipSnapshot` captures the complete typed graph for one immutable
landscape. SQLite persistence is append-only and idempotent; updates and deletes
are rejected.

## Relationship evolution

Relationship lineage follows only EP5.1 motif evolution edges. It never matches
relationships using similarity. Each immutable evolution observation is one of:

- `CREATED`
- `STRENGTHENED`
- `WEAKENED`
- `DORMANT`
- `REACTIVATED`
- `RETIRED`
- `SPLIT`
- `MERGED`
- `PERSISTED`

Strengthening and weakening are measured changes in supporting observation
count, not assessments of confidence.

## Frozen corpus validation

### Known corpus A

Current typed relationship observations: **348**

- Shared Evidence provenance: 78
- Shared Primitive observations: 78
- Shared counterparty observations: 36
- Shared temporal observations: 78
- Shared neighbourhood: 78

Evolution:

- Created: 118
- Strengthened: 88
- Dormant: 220
- Reactivated: 10
- Retired: 51

### Known corpus B

Current typed relationship observations: **325**

- Shared Evidence provenance: 85
- Shared Primitive observations: 75
- Shared funding observations: 5
- Shared counterparty observations: 13
- Shared temporal observations: 61
- Shared topology: 1
- Shared neighbourhood: 85

Evolution:

- Created: 223
- Strengthened: 24
- Persisted: 78

### Generic unlabelled population

No recorded cross-motif support exists, so the correct result is **zero
relationships**.

Focused fixtures additionally prove deterministic weakening, dormancy,
reactivation, retirement, split, merge, creation, persistence, unsupported-pair
rejection, replay, and append-only storage.

## Safety and replay

- Replay and reversed input order produce identical relationship IDs,
  observations, and evolution.
- Coverage and completeness are 100% for the consumed recorded relationship
  graph.
- No RPC call occurred.
- No production database was read or written.
- No Evidence, Primitive, Runtime, Discovery, motif, change, or evolution
  semantics changed.
- Identity, ownership inference, Operation inference, confidence, and governance
  remain disabled.

The complete deterministic validation output is retained at
`docs/evidence_platform/ep5_2_cross_motif_relationship_intelligence.json.gz`.
Its gzip header has a fixed timestamp and no filename, making its digest stable.
