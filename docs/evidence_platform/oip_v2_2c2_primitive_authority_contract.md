# OIP v2.2C.2 - Primitive Authority Contract v1

## Verdict

Primitive persistence is an immutable historical ledger. Persistence does not imply current authority.
Current intelligence must consume a separate, deterministic authority projection. The isolated v1
projection classifies all 401,050 persisted observations and equals the 346,730-observation clean
current-state replay exactly: count equality, authority-minus-clean 0, and clean-minus-authority 0.

No RPC, acquisition, production interaction, canonical mutation, Primitive mutation, provenance
deletion, compact-storage change, identity action, or governance action occurred.

## Existing contract audit

| Rule | Explicit source | Meaning | Conflict or gap |
|---|---|---|---|
| Primitive rows and Evidence links are immutable and append-only | EP2.0; schema triggers; EP2 tests | Previously generated observations remain stored | No conflict with a separate authority projection |
| Identity includes type, version, parameters, ordered Evidence IDs, subjects, window, output and quality | EP2.0; `PrimitiveObservation.create` | A changed cohort or output has a changed ID | Aggregate history accumulates by design |
| Identical replay inserts zero duplicates | EP2.0 and replay tests | Same inputs and semantics reproduce IDs | It does not require clean replay to recreate every historical snapshot |
| Multiple Primitive versions coexist | EP2.0 tests | Old versions remain queryable | Prior contract did not select a current version |
| Semantic logic changes require a version increment | EP2.0 documentation | Version is part of the semantic namespace | EP2.1 changed freshness semantics but retained version `1` |
| Aggregates use the complete available cohort | timing and recurrence generators | New Evidence creates a new immutable snapshot | Prior contract did not say which snapshot current consumers use |
| Discovery consumes supplied Primitive windows | Discovery engine | Consumer behavior follows hydration policy | Existing hydration had no authority policy |
| `generated_at` is stored | Primitive schema | It records generator time | It is not a proven insertion timestamp or run identity |

The old requirements were incomplete rather than evidence corruption. EP2.1 is the one direct
contract violation: a semantic change was made without a version increment.

## Family taxonomy

| Family | Semantic type | Cohort sensitivity | Current authority rule |
|---|---|---|---|
| SYSTEM_TRANSFER | EVENT_FACT | COHORT_INVARIANT | all observations in an approved semantic version |
| DIRECT_COUNTERPARTY | EVENT_FACT | COHORT_INVARIANT | all observations in an approved semantic version |
| LAUNCH_SIGNER | IMMUTABLE_DERIVATION | COHORT_INVARIANT | all observations in an approved semantic version |
| WSOL_CLOSE | IMMUTABLE_DERIVATION | COHORT_INVARIANT | all observations in an approved semantic version |
| PROGRAM_INTERACTION | IMMUTABLE_DERIVATION | COHORT_INVARIANT | all observations in an approved semantic version |
| SHARED_TRANSACTION | IMMUTABLE_DERIVATION | COHORT_INVARIANT | all observations in an approved semantic version |
| LAUNCH_ACTIVATION | IMMUTABLE_DERIVATION | COHORT_INVARIANT | all observations in an approved semantic version |
| ECONOMIC_FUNDING | IMMUTABLE_DERIVATION | COHORT_INVARIANT | all observations in an approved semantic version |
| WALLET_FRESH_AT_EVENT | HISTORICAL_SNAPSHOT | STATE_DEPENDENT | corrected EP2.1 semantics; old v1 semantics are legacy |
| REPEATED_COUNTERPARTY | CURRENT_STATE_AGGREGATE | COHORT_GROWING | latest generated snapshot per source/destination/version |
| BEHAVIOURAL_TIMING | CURRENT_STATE_AGGREGATE | COHORT_GROWING | latest generated snapshot per subject/ordering/scope/version |

`PROGRAM_INTERACTION` is registered but has zero rows in this frozen corpus. Unknown future families
fail closed until a complete contract entry exists.

## Authority model

The minimal states required by the current corpus are:

- `AUTHORITATIVE`: active input to current-state consumers.
- `HISTORICAL_SNAPSHOT`: an immutable earlier aggregate, with `superseded_by` identifying current state.
- `LEGACY_VERSION`: retained output of superseded generator semantics where historical metadata is defective.

`PERSISTED` is orthogonal to these states. All three states remain persisted and queryable. Future
conflict handling must be specified only when real conflict semantics exist; v1 does not add an
unused `CONFLICTED` or `UNKNOWN` state.

Authority group identity is `(family, primitive_version, semantic grouping key)`. Authority metadata
contains observation ID, group, state, successor, reason, and authority-contract version. It never
changes Primitive identity. Future authority transitions should be append-only events with a
deterministic current view; the shadow table is a validation projection, not that production design.

For the legacy corpus, `generated_at` is the only retained generation-era discriminator when two
timing outputs have the same group, cohort size, Evidence count, and window but different ordering.
The later generated observation reproduces clean replay. This is a bounded legacy rule, not a claim
that `generated_at` proves insertion order. Future aggregate generation must persist an explicit
snapshot/run boundary so authority never depends on an ambiguous era marker.

## Versioning contract

Any change that can alter selection, ordering, threshold, window semantics, missing-input behavior,
output schema/value, identity, or authority semantics MUST increment `primitive_version`. Bug fixes
are not exempt when outputs can change. A new version becomes current only through an explicit
contract-registry update naming its predecessor; numeric maximum alone is never authority.

Old and new versions may coexist as persisted and queryable observations. Multiple versions may be
simultaneously authoritative only when the registry explicitly declares semantically independent
views. The 39,694 old freshness rows remain honestly labelled stored version `1` and receive authority
reason `LEGACY_SEMANTICS_EP2_0`; they must not be rewritten to fake version `2`.

## Corpus projection

| Family | Authoritative | Historical snapshot | Legacy version |
|---|---:|---:|---:|
| BEHAVIOURAL_TIMING | 67,909 | 14,298 | 0 |
| DIRECT_COUNTERPARTY | 40,960 | 0 | 0 |
| ECONOMIC_FUNDING | 644 | 0 | 0 |
| LAUNCH_ACTIVATION | 644 | 0 | 0 |
| LAUNCH_SIGNER | 2,602 | 0 | 0 |
| REPEATED_COUNTERPARTY | 727 | 328 | 0 |
| SHARED_TRANSACTION | 9,614 | 0 | 0 |
| SYSTEM_TRANSFER | 27,333 | 0 | 0 |
| WALLET_FRESH_AT_EVENT | 186,601 | 0 | 39,694 |
| WSOL_CLOSE | 9,696 | 0 | 0 |
| **Total** | **346,730** | **14,626** | **39,694** |

All 54,320 non-authoritative rows remain queryable and carry explicit successor links. The complete
5,940,717-link historical surplus remains unchanged.

## Consumer and replay contract

- Discovery consumes `CURRENT_AUTHORITATIVE` only.
- Motifs consume Discovery candidates and their authoritative Primitive support.
- Relationships and Operational Landscape consume authoritative motif/landscape outputs.
- Historical analysis must explicitly request `ALL_PERSISTED` and must label the result historical.

`CURRENT_STATE_REPLAY`, given frozen Evidence, approved generator versions, and Authority Contract v1,
must exactly reproduce the current authority projection. That invariant passes for all 346,730 IDs.
`HISTORICAL_LEDGER_REPLAY` is distinct and is not currently exact: historical Evidence boundaries,
producer version/commit, run identity, and snapshot boundaries were not retained. Persistence replay
must remain idempotent; authority replay must reproduce the same projection; historical replay may
not claim completeness without those missing inputs.

## Downstream shadow impact

The 328 historical recurrence snapshots participate in current Discovery/motif support across 206
subjects. Their authoritative equivalents preserve the subjects but necessarily change supporting
Primitive sets and therefore can change content-addressed candidate, motif, and indirect relationship
identities.

A full frozen downstream run was attempted over the authority population. It was stopped after the
existing input-window implementation spent more than six minutes serializing the 346,730 Primitive /
6,457,475-provenance-link window before intelligence evaluation. Two unrelated pre-existing runs of
the same full motif validator had each accumulated more than 23 CPU-hours without completion. A
second indexed 206-subject slice also exceeded the bounded query window because subjects are stored
as JSON without a lookup index. No downstream equality or difference count is claimed.

This is an unresolved validation-performance limitation, not a semantic failure. The follow-up
authority implementation must apply the authority filter and indexed subject projection before full
`PrimitiveInputWindow` hydration, then rerun Discovery, motif, and relationship A/B validation.

## Implementation and migration gates

The smallest follow-up is an append-only authority-transition ledger plus indexed current projection.
It must record group, selected Primitive, predecessor, reason, contract version, generator semantic
version, and transition time/run boundary. A deterministic view may expose current authority.

Compact migration has two independent gates:

1. Historical gate: all 12,398,192 persisted provenance relations remain exactly equivalent.
2. Application gate: compact current-authority projection equals the 346,730-ID current-state replay,
   followed by completed frozen downstream A/B validation.

OIP v2.2C must not resume directly. Implement and validate the indexed authority projection in an
intermediate milestone first. Acquisition remains held; the 5,000-attempt expansion remains blocked.
