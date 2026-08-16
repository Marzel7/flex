# PSI0E-A published-projection integration envelope

PSI0E-A qualifies a pure, default-off and non-authoritative envelope boundary
over caller-injected synthetic representations of the PSI0D three-file
publication bundle. Qualification never opens or applies the contract to the
real PSI0D bundle.

The adapter replay-verifies canonical `contract.json`, `hashes.json`, and
`projection.json` bytes; publication and consumer contract identities; file,
projection, hash-manifest and bundle digests; lineage; production-derived
summary provenance; exact five-surface aggregate schema; accounting;
`ABSENT_NOT_NEGATIVE`; preserved duplicates, conflicts and unmatched counts;
reason codes; interpretation; and false authority.

The pure core emits only a canonical descriptive integration envelope with:

- source bundle, projection, hashes, publication-contract and consumer-contract
  identities;
- cohort and per-surface coverage numerators and denominators;
- row, unique, duplicate and unmatched counts;
- missingness semantics, aggregate conflict/unmatched counts and reason codes;
- default-off/consumer-disabled state and false policy, ranking, integration,
  deployment and activation authority.

Exact schemas reject extra fields, so mints, addresses, signatures, payloads,
scores, thresholds, selection fields and other source values cannot enter the
envelope. The core performs no file, database, network, service or configuration
I/O and has no retry or fallback behavior.

This qualification does not authorize reading the real PSI0D bundle, creating
real integration output, connecting or deploying a consumer, Evidence Mirror or
Cohort Mode activation, production activation, or EB2.
