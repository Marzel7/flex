# EP4.0 — Generic Unknown Operation Discovery

## Outcome

EP4.0 adds a completely isolated, non-authoritative Discovery consumer. It
generates deterministic investigation candidates from frozen runtime input
windows without loading an Operation Contract or reading production storage.

```text
Immutable EvidenceInputWindow
             +
Immutable PrimitiveInputWindow
             +
optional Behaviour Observations / Topology Revisions / Runtime digests
             ↓
      Discovery Engine v1
             ↓
append-only Candidate Store
```

The existing Evidence, Primitive, and Operation Runtime implementations were
not modified.

## Candidate contract

Every candidate contains only:

- deterministic candidate ID and Discovery version;
- supporting Evidence and Primitive IDs;
- optional supporting Behaviour Observation and Topology Revision IDs;
- observed recurring primitive motif;
- population and observation window;
- quality, missing Evidence, and contradictory Evidence;
- discovery lifecycle and immutable input digest.

Candidate payloads contain no Operator, Treasury, Controller, confidence,
canonical identity, governance recommendation, or promotion output.

## Generic clustering

The engine constructs label-blind subject/observation incidence clusters. A
candidate arises when a subject participates in a recurring multi-subject
primitive motif. The output records observed dimensions rather than assigning
roles:

- recurring primitive combinations;
- directional observation counts;
- counterparty/population recurrence;
- Behaviour observation recurrence where supplied;
- Topology revision recurrence where supplied;
- infrastructure-related primitive recurrence where observed.

There is no seeded wallet, known Operation, prescribed topology, similarity
score, ranking, or identity inference. Broad candidate counts are expected at
this phase: EP4.0 proposes evidence-backed structures; later investigation may
triage them.

## Lifecycle

The isolated lifecycle is:

```text
OBSERVED → RECURRING_PATTERN → INVESTIGATE → DISMISSED
```

`OBSERVED` and `RECURRING_PATTERN` may also transition directly to `DISMISSED`.
No transition can produce `CONFIRMED_OPERATION` or `CANONICAL`.

Candidate rows, references, and lifecycle events are append-only. Replaying an
identical snapshot produces duplicate identities rather than new candidates.

## Validation

Validation report:
`docs/evidence_platform/ep4_0_unknown_discovery_validation.json`

| Dataset | Primitive observations | Candidates | Replay |
|---|---:|---:|---|
| Known corpus A | 85,989 | 14,203 | Deterministic |
| Known corpus B | 858 | 94 | Deterministic |
| Generic unlabelled population | 4 | 2 | Deterministic |

Validation dataset names are comparison labels applied after execution. They
are never available to the engine and never appear in candidate payloads.

All three datasets passed:

- deterministic candidate identity;
- idempotent append-only replay;
- label-blind output;
- explicit missing/conflicting provenance;
- zero RPC;
- zero production database reads or writes;
- zero Operation Contracts loaded;
- zero identity promotions;
- zero governance actions.

## Health and metrics

The engine reports evaluation, candidate, supporting-primitive, and population
bound metrics. The Candidate Store reports persisted candidates and lifecycle
events. Both health surfaces explicitly report `authoritative: false`; the
engine also reports identity and governance disabled.

## Safety boundary

Discovery is a pattern detector, not a classifier. It cannot create an
Operator, mutate an Operation, execute governance, or become canonical. Its
only output is a replayable proposal for investigation.
