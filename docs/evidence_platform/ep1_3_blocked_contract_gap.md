# EP1.3 blocker — frozen normalization contract unavailable

Status: **SUPERSEDED**

The full X78.26 response was subsequently recovered from the retained local
Codex session. The missing-source blocker is resolved, but formalization exposed
semantic contradictions documented in
`ep1_3a_blocked_semantic_conflicts.md`.

EP1.3 requires deterministic normalization for twelve Evidence families
"exactly as defined by X78.26". The repository and retained architecture
documents do not contain those definitions.

## Verified inputs

Repository-wide and retained-attachment searches located:

- X78.25's conceptual examples of immutable transaction facts;
- X78.27's operational isolation and single-writer requirements;
- X78.28's phase-level list (`TransactionFact`, `InstructionFact`,
  `BalanceFact`, `LaunchFact`, movement/participation/observation facts);
- EP1.0's generic immutable envelope/provenance/artifact schema;
- EP1.2's raw acquisition envelope and artifact contract.

They do **not** define the required field-level contracts for:

1. `TransactionFact`
2. `AccountParticipationFact`
3. `InstructionFact`
4. `BalanceFact`
5. `NativeMovementFact`
6. `TokenMovementFact`
7. `AccountCloseFact`
8. `ProgramEventFact`
9. `LaunchFact`
10. `AddressHistoryObservation`
11. `TransactionVerificationObservation`
12. `ExternalRegistryObservation`

## Missing frozen decisions

For each family, implementation requires but cannot verify:

- required and optional fields and their types;
- canonical ordering and null/absence rules;
- primary/deterministic Evidence ID formula;
- source observation cardinality;
- provider-disagreement identity boundary;
- schema/replay/parser version values;
- raw artifact mapping rules;
- malformed/partial observation rules;
- provenance payload contract.

## Storage mismatch

The existing EP1.0 schema contains only generic immutable envelopes,
provenance, artifact references, and writer receipts. It contains no frozen
normalized-fact representation and no normalization-status contract.

EP1.3 simultaneously requires facts and normalization status to be appended
while prohibiting Evidence schema modification. Without the missing X78.26
contract, it is not possible to determine whether normalized facts/statuses
must be represented as existing envelopes or whether an already-approved but
uncommitted schema was intended.

## Why implementation stopped

Inventing any of these fields, identities, tables, or status representations
would redesign the frozen Evidence Contract and could make replay identities
irreversible. That is expressly prohibited by EP1.3.

No EP1.3 implementation, schema change, database write, commit, or push was
performed.

## Required unblock

Provide the final field-level X78.26 contract (including deterministic ID and
normalization-status storage rules), or explicitly approve formalizing that
missing contract as a prerequisite amendment before EP1.3 implementation.
