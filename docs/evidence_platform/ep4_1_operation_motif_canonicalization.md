# EP4.1 — Operation Motif Canonicalization

## Outcome

EP4.1 adds a non-authoritative layer downstream of EP4.0. It converts individual
candidate subgraphs into deterministic structural motifs and consolidates every
candidate with the same motif while retaining occurrence-level provenance.

The Discovery Engine, Evidence, Primitives, Operation Runtime, and Operation
Contracts are unchanged.

## Boundary

The canonical graph removes concrete wallet addresses, mint addresses,
transaction signatures, absolute timestamps, and non-structural amounts. It
retains:

- directed relationships;
- primitive types and versions;
- structural role ordering;
- graph shape and multiplicity;
- relative temporal ranks and primitive sequence.

Concrete values remain only in immutable occurrence provenance. A motif is not
an identity, an Operation classification, or a governance recommendation.

## Canonical graph

`MotifCanonicalizer` constructs a directed labelled graph from the immutable
primitive observations referenced by each discovery candidate. Nodes begin with
operation-neutral structural roles such as `SOURCE`, `DESTINATION`, `SIGNER`,
`LAUNCH`, and `PARTICIPANT`. Iterative structural refinement produces stable
equivalence classes. The persisted graph is a quotient containing:

- node-class multiplicity and role counts;
- directed primitive/version edges;
- role order for each edge;
- relative temporal rank;
- operation-neutral unary observations;
- primitive sequence by temporal rank.

`motif_id` is derived only from this canonical graph, its primitive versions,
and canonicalization version. It does not include occurrence addresses,
evidence IDs, candidate IDs, timestamps, or signatures.

## Persistence and replay

`MotifStore` has three append-only record families:

- immutable motif definitions;
- immutable candidate-to-motif occurrences;
- immutable Candidate, Evidence, and Primitive references.

One immutable motif definition may accumulate many occurrence rows without
rewriting the definition. Re-appending the same corpus is idempotent. A new
candidate with an existing graph adds a new occurrence to the existing motif.

## Validation results

| Validation dataset | Raw candidates | Motifs | Compression | Largest motif | Singletons |
| --- | ---: | ---: | ---: | ---: | ---: |
| Known corpus A | 14,203 | 1,379 | 10.30× | 961 | 972 |
| Known corpus B | 94 | 15 | 6.27× | 26 | 8 |
| Generic unlabelled | 2 | 1 | 2.00× | 2 | 0 |

The large known corpus canonicalized in approximately 27.3 seconds during the
recorded validation. Replay with reversed candidate and primitive input order
produced identical motif IDs, canonical graphs, occurrence assignments, and
payload digests.

The validation used frozen shadow corpora only. It performed zero RPC calls,
zero production database reads or writes, loaded no Operation Contracts, and
performed no identity promotion or governance action.

The machine-readable report is
`docs/evidence_platform/ep4_1_motif_validation.json`.

## Health

Health output reports:

- motif and candidate counts;
- compression ratio;
- largest motif;
- singleton rate;
- occurrence distribution;
- generation latency;
- replay/persistence state.

All health and persistence output remains explicitly non-authoritative.
