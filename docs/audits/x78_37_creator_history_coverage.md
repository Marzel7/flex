# X78.37 — Creator History Coverage & Completeness Contract

## Result

The legacy `address_scan_state` cursor is **not** evidence coverage. All 6,448
live rows contain `v1_migration_start`, rather than a transaction boundary.
It must not be used to skip or classify creator history.

X78.37 adds an opt-in, separate coverage ledger. It writes a page observation
only after the existing page facts have committed. Therefore a crash can leave
facts without a coverage checkpoint, but cannot create a checkpoint for facts
that were not durable. This is the required safe failure direction.

The ledger remains disabled by default (`CREATOR_HISTORY_COVERAGE_ENABLED=0`)
and does not affect acquisition, cursors, funder attribution, RPC, or the
outgoing completion barrier.

## Completeness semantics

`COMPLETE_EXHAUSTED` requires both a provider exhaustion response and a proven
contiguous boundary. The present extractor can prove this only for a shallow
one-page sequence followed by exhaustion. Deep pages currently use exclusive
`before` cursors without overlap, so even an empty final page is recorded as
`EXHAUSTED_UNVERIFIED_CONTIGUITY`, never complete.

Every legacy cap, 30-day cutoff, funder threshold, timeout, HTTP/provider
failure and missing continuation remains `PARTIAL`, `FAILED`, or `UNKNOWN`.
None is evidence of no additional funders.

## Validation

Focused safety regression: **17 passed**.

## Gate

**PASS — contract only.** The durable state machine is safe and expressive.
It does not make existing deep history reusable. X78.38 may audit incremental
reuse, but must stop if it cannot prove overlap/equivalence from a frozen
corpus.
