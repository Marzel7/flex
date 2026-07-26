# X64.6 — Phase 7: Bounded RPC Recovery — Run Report

Zero-RPC recovery (Phase 6) was exhausted first and confirmed exhaustively
empty for all 42 rows before any RPC was spent (see
`x64_6_missing_create_audit.md` Phase 2). Per explicit user authorization,
a temporary, inline-only Helius RPC key was then used for this bounded
pass — never written to a file, never committed, matching this project's
established RPC-investigation discipline.

## Population searched

Of the 42 original `MINT_NOT_FOUND` rows (46 at the moment this phase
ran, due to continued live queue growth — 4 of those 46 had already
become `RECOVERABLE_VALID_ANCHOR` by X64.5's own zero-RPC path and were
excluded), **27 rows had a resolved `creator`** and were eligible for the
bounded RPC search. **19 rows have `creator=NULL`** in the queue itself
and were **not** searched — searching for a CREATE transaction without
knowing which wallet to query is not a bounded operation (it would
require scanning the mint's own history via `getSignaturesForAddress` on
the mint address itself, a materially different and more expensive
search not attempted in this pass; see "What was NOT attempted" below).

## Bounds enforced (Phase 7's explicit requirement)

| Bound | Value |
|---|---|
| Max signature pages fetched per row | 3 |
| Signatures per page | 20 |
| Max RPC credits per row | 12 |
| Time window (relative to `enqueued_at`) | 2 hours, CREATE-must-precede-migration enforced |
| Hard stop on first valid match | Yes — newest-first within the window, stops at first `pump.fun program + this mint present` transaction |

## Method

For each row: paginate the creator wallet's transaction history backward
from `enqueued_at` (bounded by page count and RPC credit cap), filter to
transactions within the time window whose block time is at or before
`enqueued_at`, then fetch and inspect each candidate (newest-first) for
the pump.fun program ID (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`)
and the target mint both appearing in the transaction's `accountKeys` —
the CREATE-instruction signature. First match wins; search stops
immediately (`Stop immediately once a validated CREATE anchor is
found`).

## Results

```
rows searched (creator known): 27
total_rpc_credits: 114
recovered: 13
unresolved: 14
```

Per-row RPC cost ranged from 2 to 12 credits (median 4). All 13 recovered
signatures independently verified against the production `valid_signature()`
function (87-88 chars, alphanumeric) — all pass. All 13 signatures are
mutually distinct (no cross-mint reuse). Full per-row detail:
[x64_6_stored_source_recovery.csv](x64_6_stored_source_recovery.csv).

### Unresolved rows (14) — reason breakdown

| Reason | Count | Meaning |
|---|---|---|
| `no_signatures_in_window` | 12 | The creator wallet had zero transactions within the bounded 2-hour/3-page window preceding `enqueued_at` — either genuinely quiet at that time, or the CREATE happened further back than this pass's bound allowed |
| `no_matching_create_in_window` | 2 | Transactions existed in-window, but none matched (pump.fun program + this mint both present) within the RPC-credit cap before the budget was exhausted |

No row failed due to a malformed or unsupported CREATE transaction shape
— every candidate transaction fetched was parseable; the failures are
purely "nothing matching found within the bound," which is the honest,
expected outcome of a *bounded* search, not a parser defect.

## What was NOT attempted (explicitly out of scope for this pass)

- **The 19 `creator=NULL` rows**: would require either (a) resolving the
  creator first via a separate, unbounded search, or (b) searching the
  mint address's own transaction history directly — neither was
  attempted, per the task's explicit "bounded" constraint and to avoid
  scope creep into the creator-resolution problem, which is Failure Mode
  C's job (Phase 4/9), not this recovery pass's.
- **Widening the time window or page count for the 14 unresolved rows**:
  a second pass with looser bounds was not run — per the task's
  instruction to keep this recovery strictly bounded and report
  unresolved rows honestly rather than iteratively loosen bounds until
  something is found.

## Persistence

All 13 recovered signatures were applied via the new
`anchor_reconciliation.apply_rpc_recovered_anchor()` function (Phase 8),
verified idempotent on replay, verified to never touch `subprov`/
`treasury`/`attempts`, and verified selectable by `drain_batch`'s own
WHERE clause immediately after. See `x64_6_implementation.md` for the
persistence-repair detail and `wt_anchor_reconciliation_log` audit trail.
