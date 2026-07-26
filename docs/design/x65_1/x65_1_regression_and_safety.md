# X65.1 — Phase 10: Regression and Safety Validation

## Behaviour Cohort remains exclusive

Live-checked post-deployment: `canonical_behaviour_conserved: True`
(via `/api/ops-v2/operational-intelligence?window=7d`) — X65.0's
exclusivity invariant is fully intact and unaffected by this task's
changes. Nothing in X65.1 reads, writes, or references
`canonical_behaviour`/`behaviours` at all.

## Cohort count conserved / no launch disappears / no launch duplicated

`resolve_treasury_for_cohort()` returns a Python dict keyed by mint —
structurally, a dict cannot silently drop or duplicate a key: every
mint passed in appears exactly once in the output (verified directly:
`test_resolve_cohort_returns_one_object_per_mint`). Live-verified
against the real cohort: 19 mints in, 19 `treasury_resolution` objects
out, summing to 7 `KNOWN_TREASURY` + 12 `UNRESOLVED` = 19.

## Fresh Creator classification unchanged

X65.1 does not read, write, or reference `creator_identity` anywhere —
confirmed by direct code inspection of `src/ops/treasury_resolution.py`
(zero occurrences of `creator_identity` in the file) and of the API
route addition (the new route calls only
`resolve_treasury_for_cohort`, nothing from `src/ops/creator_identity.py`).

## Existing known-operation assignments are unchanged

`match_known_treasury()` is a pure `SELECT` against
`wt_confirmed_treasuries`/`wt_ops_v2_wallets` — verified by direct test
(`test_match_known_treasury_never_writes_to_confirmed_table`,
`test_resolve_cohort_is_read_only`) and by live row-count comparison:
`wt_confirmed_treasuries` remains at 61 rows,
`wt_active_subprov_sessions` continues its normal live growth (both
before and after this task's deployment), consistent with zero writes
originating from this task's code.

## No unknown treasury is automatically confirmed

Zero `INSERT`/`UPDATE`/`DELETE`/`CREATE TABLE`/`ALTER`/`DROP`
statements exist anywhere in `src/ops/treasury_resolution.py` —
confirmed by direct grep (empty result). The
`UNKNOWN_TREASURY_CANDIDATE` classification path (Phase 6) never
touches `wt_confirmed_treasuries`; it only reads from it to check
whether a wallet is *already* confirmed, returning `None` (not writing
anything) when it isn't.

## No production treasury root is rewritten

Same evidence as above — this module contains no write path of any
kind. "Rerooting" a treasury (reassigning which operation/treasury a
wallet is considered part of) would require a write to
`wt_confirmed_treasuries` or `wt_ops_v2_wallets`; neither ever occurs
here.

## All new operation assignments are backed by an existing confirmed treasury relationship

Every `operation_id` this module surfaces comes directly from
`wt_ops_v2_wallets.operation_uuid`, joined only for a `treasury_wallet`
that already passed the `wt_confirmed_treasuries` check in the same
function call (`match_known_treasury()`) — there is no code path that
returns a non-null `operation_id` without first confirming the
treasury. Live-verified: all 7 `KNOWN_TREASURY` results in the real
cohort carry an `operation_id` that was independently confirmed present
in `wt_ops_v2_wallets` during Phase 2/5's manual audit, before this
module was even written.

## Unresolved launches remain Unassigned

`UNRESOLVED` results always carry `operation_id: null` (verified:
`test_full_resolution_unresolved_when_no_evidence`,
`test_full_resolution_unresolved_when_no_attribution_row_at_all`) — the
Discovery UI's existing `TOPO_SELECTION.operation` state is entirely
untouched by this task, so these 12 launches remain exactly as
`__UNASSIGNED__` in the existing Operation Attribution stage as they
were before X65.1.

## Traversal is bounded

`MAX_WALKBACK_DEPTH = 2`, and depth is only ever extended after an
explicit `is_bridged_further_upstream()` check (a real, tested query,
never assumed true) — verified against the live cohort: all 3 treasury
candidates checked returned `bridged_further_upstream: False`, so depth
remained at 2 for every launch in this cohort; no unbounded recursion
is possible even in principle, since the bridging check itself is only
ever consulted once per resolution (no loop, no recursive re-entry into
`resolve_treasury_for_launch`).

## API latency remains acceptable

Live-measured: `GET /api/ops-v2/treasury-resolution?mints=<1 mint>` →
**19ms**. This is a synchronous, bounded (≤200 mints per request),
purely-database-read endpoint — no RPC, no network I/O, consistent with
this project's other fast, database-only endpoints measured earlier in
this project's operating history (e.g. the `/api/discovery/recent`
endpoint after its own index fix, ~0.01-0.5s for a similar read-only
query shape).

## No uncontrolled per-row RPC fan-out is introduced

Confirmed by direct grep: zero RPC-related imports or calls anywhere in
`src/ops/treasury_resolution.py` (`requests.`, `urlopen`, `httpx`,
`aiohttp`, `_rpc(` — all absent). Every fact this module surfaces is
read from already-indexed SQLite tables (`wt_attribution_outcomes`,
`wt_active_subprov_sessions`, `wt_confirmed_treasuries`,
`wt_ops_v2_wallets`) — per the task's own explicit instruction, "Use
cached or database evidence first," this module goes further and uses
*only* database evidence; no RPC enrichment of any kind was implemented
for the 12 `UNRESOLVED` launches in this pass (they remain genuinely
unresolved rather than being closed via a bounded/queued RPC
investigation, which the task frames as an acceptable but not required
extension — deferred as explicit future work, not silently skipped).

## Test suite results

67 tests pass across the full relevant regression surface: 23 new
(`test_x65_1_treasury_resolution.py`), plus 44 pre-existing
(`test_x65_0_exclusive_behaviour.py`, `test_x64_8_creator_identity.py`,
`test_x27_4_behaviour_queue.py`) — zero regressions, zero new failures.

## Process health

All four managed processes (`watchtower_listener`, `walkback_worker`,
`ws_cascade`, `watchtower_api`) remain `RUNNING` post-deployment; only
`watchtower_api` was deliberately restarted (to load the new route and
template changes) — the other three were untouched by this task, per
their unbroken uptimes at verification time.
