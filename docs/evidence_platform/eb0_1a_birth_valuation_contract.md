# EB0.1A canonical birth and valuation evidence contract

## Boundary

`src/evidence/contracts/birth_valuation.py` is a pure, deterministic projection over already-bounded local evidence. It imports no provider, database, service, queue, or runtime code. EB0.1A does not activate collection or establish source authority.

## Event semantics

- `CHAIN_BIRTH`: an event-time chain birth fact.
- `PLATFORM_FIRST_SEEN`: the platform's first observation, which may be later than chain birth.
- `MIGRATION`: a migration fact, independent of birth and market observation.
- `MARKET_FIRST_OBSERVED`: the earliest market observation available to the stated source/version, not automatically the token's birth valuation.

These event kinds remain separate facts. The projection never collapses one into another.

## Valuation rules

- Values are positive decimal strings. `0` is not a missing-value encoding.
- Missing valuation must be `price_or_market_cap_value=null`, `valuation_semantics=UNKNOWN`, and `completeness_state=NOT_OBSERVED`.
- `BIRTH_MARKET_CAP` requires a positive value, matching event and observation timestamps, `VERIFIED` quality, `COMPLETE` evidence, a birth-compatible event kind, and explicit `birth_equivalence_proven=true`.
- A later `MARKET_FIRST_OBSERVED` value therefore cannot be promoted to birth market cap merely because it is the first value the platform retained.

## Provenance and conflicts

`provenance_digest` hashes mint, event kind/time, source/version, observation time, and the caller-supplied source-record digest. `observation_id` additionally covers valuation, quality, completeness, and equivalence proof. Exact replays are idempotently deduplicated; different sources, versions, or values remain separate immutable facts. Projection ordering and its aggregate digest are independent of input order.

## Qualification scope

Frozen fixtures cover exact birth, platform first-seen, delayed market observation, missing valuation, conflicting sources, and replay ordering. This milestone performs no provider calls, production access, ranking, GMGN work, operator attribution, Evidence Mirror/Cohort activation, or production activation.
