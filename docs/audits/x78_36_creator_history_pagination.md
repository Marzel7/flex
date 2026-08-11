# X78.36 — Creator History Pagination Depth & Acquisition Cost Reduction

## Outcome

The optimization gate is **BLOCKED**. Observation-only pagination telemetry is active; production extraction semantics are unchanged.

Creator Funding does not resolve one ranked funder. It accumulates every qualifying inbound sender and its total amount. Therefore finding a first candidate does not make later history irrelevant. The first instrumented deep example proved this directly: page 1 found funding, while page 2 contained three additional qualifying inbound transfers and changed authoritative aggregate amounts.

## Current history contract

- Accepted evidence is a Helius native transfer into the creator of at least 0.001 SOL, excluding known dust and creator-specific internal addresses.
- `creator_funders` is creator-global, set-valued, and amount-accumulating.
- It is not an earliest-funder or latest-funder decision.
- The main enhanced-history loop explicitly processes transfers on both sides of migration time.
- The Jito create-transaction check and outgoing scan are launch/migration-specific.
- A positive `creator_funders` row already activates the known-creator fast path.
- A missing row cannot mean “no funder”: timeout, provider failure, page caps and time cutoffs can all produce incomplete acquisition.

## Why safe reduction is not yet proven

The existing loop already stops at several non-completeness boundaries: 30 days, eight pages, three pages after an oldest-transaction bootstrap, five empty pages after five funders, or 50 funders. Provider errors and timeouts also terminate the loop. Except for an actually exhausted provider history response, none proves contiguous history coverage.

`address_scan_state` cannot currently support incremental reuse. All 6,435 live rows store the literal `v1_migration_start`, not an observed signature boundary. It contains no oldest slot, newest slot, continuity, provider/parser version, completeness state or unresolved gap.

The enhanced address-history path uses the shared request transport but not the persistent RPC response cache. Adding that cache is not automatically safe: the newest page is mutable as new transactions arrive, and no frozen equivalence corpus yet defines compatible freshness and continuation semantics.

## Repeat-creator pressure

The live database contains 325,363 distinct creators; 93,441 are repeat creators and collectively own 1,374,451 launches. The queue contains 12,185 rows belonging to 2,318 repeated creators. Positive creator funding is reused, but repeat/no-funder or incomplete cases cannot safely reuse absence.

## Instrumentation added

Each FULL extraction now emits a bounded `pagination` object inside the existing task-local `CFQ_PHASE_LEDGER`. It records page/cursor identity, wall time, returned count, duplicate signatures, relevant inbound transfers, new funders, provenance count, oldest timestamp, page effect, termination reason, and whether provider history was truly exhausted.

The instrumentation uses the existing `ContextVar`, so two extraction slots cannot cross-contaminate observations. It performs no RPC and no database write.

## Candidate decisions

| Candidate | Decision | Reason |
|---|---|---|
| Stop when funding is found | Rejected | Later pages can add funders or change amounts. |
| Stop at coverage checkpoint | Blocked | No valid contiguous checkpoint exists. |
| Reuse existing funding | Existing positive fast path retained | Negative/incomplete results are not reusable. |
| Incremental repeat-creator scan | Blocked | Persisted cursors are not chain boundaries. |
| Shared acquisition reuse | Partially present | Enhanced pages lack a proven persistent freshness contract. |
| Reuse parsed facts | Blocked | No compatible parsed-fact coverage contract exists. |
| Bound by migration slot | Rejected | Current creator-global result includes post-migration transfers. |

## Validation

The targeted regression run passed 76 tests across X78.35A, X78.34, X78.32, X78.31, X78.30, X78.29, X78.17, X78.14, X76.3, shared acquisition and writer arbitration. The corrected page-effect instrumentation then passed its five focused tests.

No extraction slots, RPC ceilings, retry/failover behavior, outgoing ownership, queue accounting, attribution or persistence semantics changed.

Machine-readable artifacts:

- `docs/audits/x78_36_acquisition_graph.json`
- `docs/audits/x78_36_pagination_audit.json`

## Final verdict

**X78.36 Optimization Gate: BLOCKED — no deterministic completeness invariant and equivalence corpus currently support reducing creator-history acquisition without reducing evidence.**

The deployed instrumentation is the minimum safe next step. It makes a representative equivalence corpus measurable instead of guessing from interleaved legacy logs.
