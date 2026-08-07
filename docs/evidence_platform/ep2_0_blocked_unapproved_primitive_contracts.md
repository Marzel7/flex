# EP2.0 blocker — unapproved primitive contracts

Status: **RESOLVED — OPTION 1 APPROVED**

EP2.0 is narrowed to the eleven X78.26 primitives. The seven additional names
are deferred candidates, not rejected and not part of Primitive Contract v1.

EP2.0 requires implementation of at least eighteen primitive types while also
requiring that only primitives approved by X78.26 be implemented. The recovered
X78.26 contract approves eleven primitives. Seven mandatory EP2.0 primitives do
not have an approved input/output, identity, quality or failure contract.

## Approved by X78.26

The frozen X78.26 primitive inventory defines:

1. `SYSTEM_TRANSFER`
2. `LAUNCH_SIGNER`
3. `WSOL_CLOSE`
4. `DIRECT_COUNTERPARTY`
5. `PROGRAM_INTERACTION`
6. `WALLET_FRESH_AT_EVENT`
7. `LAUNCH_ACTIVATION`
8. `ECONOMIC_FUNDING`
9. `SHARED_TRANSACTION`
10. `REPEATED_COUNTERPARTY`
11. `BEHAVIOURAL_TIMING`

For these primitives X78.26 supplies approved Evidence inputs, output fields and
failure semantics.

## Mandatory in EP2.0 but absent from X78.26

EP2.0 additionally mandates:

1. `TOKEN_TRANSFER`
2. `ACCOUNT_CREATION`
3. `TRANSACTION_SIGNER`
4. `FEE_PAYER`
5. `LAUNCH_CREATOR`
6. `ACCOUNT_CLOSE`
7. `PROGRAM_REUSE`

No X78.26 primitive contract defines these names.

Although related immutable Evidence families exist, deriving a primitive is not
a mechanical rename. Permanent decisions are still missing for each type:

- exact Evidence inputs and required joins;
- output payload and subjects;
- natural/logical primitive boundary;
- observation window;
- parameters;
- `PROVEN`, `DISPROVEN`, `INCOMPLETE`, `CONFLICTING` and `UNVERIFIABLE`
  transitions;
- missing-input and failure states;
- whether the primitive duplicates an approved primitive or represents a new
  semantic observation.

Examples of unresolved overlap include:

- `TOKEN_TRANSFER` versus approved `DIRECT_COUNTERPARTY` consuming
  `TokenMovementFact`;
- `ACCOUNT_CLOSE` versus immutable `AccountCloseFact` and approved
  `WSOL_CLOSE`;
- `TRANSACTION_SIGNER` / `FEE_PAYER` versus approved
  `PROGRAM_INTERACTION`, `LAUNCH_SIGNER` and `SHARED_TRANSACTION`;
- `LAUNCH_CREATOR` versus immutable `LaunchFact` and approved
  `LAUNCH_SIGNER`;
- `PROGRAM_REUSE`, which requires an aggregation window and grouping policy not
  defined by X78.26;
- `ACCOUNT_CREATION`, for which X78.26 deliberately stores account/balance/history
  facts but defines no generic creation primitive.

Choosing any of these boundaries during implementation would create new frozen
architecture, which EP2.0 expressly prohibits.

## Minimal unblock

Approve one of the following explicitly:

### Option A — implement only the frozen eleven

Remove the seven unapproved names from EP2.0 scope. This preserves X78.26
without amendment.

### Option B — approve an EP2.0A primitive-contract amendment

Define the seven missing primitives with the same contract fields used by
X78.26:

```text
primitive type and version
required Evidence families
subjects
parameters
observation window
output payload
quality-state rules
missing inputs
failure states
```

After approval, EP2.0 can implement all eighteen without inventing semantics.

## Work intentionally not performed

No primitive engine, registry, schema, storage, replay, health, metric, test,
production change, commit or push was created. EP1 Evidence remains unchanged.
