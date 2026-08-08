# EP5.1 — Operational Evolution Graph

## Outcome

EP5.1 adds a deterministic, append-only Operational Evolution Graph over two
immutable EP5.0 landscape snapshots and their immutable `ChangeSnapshot`.
It reconstructs objective motif and neighbourhood lineage without inferring
identity, Operators, ownership, confidence, or governance.

## Lineage contract

An evolution edge is permitted only when one of these reproducible conditions
is present:

1. `CANONICAL_MOTIF_PERSISTENCE`: the deterministic canonical motif ID exists
   in both snapshots; or
2. `EXACT_OCCURRENCE_CONTINUITY`: the snapshots contain the same immutable
   candidate occurrence assignment.

Changed motif IDs therefore require an exact occurrence intersection. Merely
similar topology, behaviour, timing, addresses, or primitive composition never
creates lineage. Two unrelated motifs may look identical and still produce one
`RETIRED` plus one `NEW` event rather than a continuation.

Neighbourhood lineage consumes the immutable neighbourhood components already
produced by EP5.0. Each neighbourhood becomes a first-class evolution node and
each observed component transition becomes an objective evolution edge.

## Immutable graph

- `EvolutionNode` represents one motif or neighbourhood in one landscape
  snapshot.
- `EvolutionEdge` records the precise continuity basis and its supporting
  candidate, Evidence, Primitive, topology, relationship, and temporal IDs.
- `EvolutionEvent` records `NEW`, `CONTINUED`, `GREW`, `DECLINED`, `DORMANT`,
  `REACTIVATED`, `SPLIT`, `MERGED`, or `RETIRED`.
- `EvolutionSnapshot` binds the complete graph to the exact previous landscape,
  current landscape, EP5.0 change snapshot, and engine version.

The SQLite persistence layer is append-only. Re-appending the same graph is
idempotent. Updates and deletes are rejected by database triggers.

## Validation

Validation replayed the frozen known corpora and generic unlabelled population.
Inputs were also supplied in reverse order to prove ordering independence.

### Known corpus A

- Continuations: 791
- Births: 613
- Retirements: 5
- Growth events: 248
- Decline events: 2
- Dormancy events: 765
- Reactivations: 2
- Splits: 1
- Merges: 2
- Coverage: 100%

The difference from EP5.0's 767 motif continuations is the inclusion of 24
first-class neighbourhood continuations.

### Known corpus B

- Continuations: 9
- Births: 7
- Growth events: 6
- Dormancy events: 3
- Coverage: 100%

### Generic unlabelled population

- Continuations: 2
- Growth events: 1
- Reactivations: 1
- Coverage: 100%

Focused fixtures separately prove deterministic motif split, motif merge,
birth, retirement, continuity, similarity rejection, neighbourhood evolution,
and append-only persistence.

## Safety

- No Evidence, Primitive, Runtime, Discovery, canonicalization, intelligence,
  or EP5.0 semantics changed.
- No RPC calls occurred.
- No production database was read or written.
- No Operation Contract was loaded.
- Identity, Operator inference, confidence, and governance remain disabled.
- Replay and input ordering are deterministic.

The complete machine-readable replay graph is retained as deterministic gzip at
`docs/evidence_platform/ep5_1_operational_evolution_graph.json.gz`. The gzip
header uses a fixed timestamp and no filename, so the artifact digest is stable.
