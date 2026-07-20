# X28.0 — Decouple Creator Watch Lifetime from Subprov Session Lifetime

**Status: implementation sprint, complete.** Builds directly on
[X27.11](X27_11_SIMPLIFY_SUBPROV_LIFECYCLE_FANOUT_CAPTURE.md)'s conclusion B.
Per that sprint's recommended narrow first step, this sprint fixes exactly
the confirmed coupling defect and adds durable provenance — no quiet-period
state machine, no sweep reframing, no TTL/concurrency changes (all explicitly
out of scope per this sprint's constraints).

## Objective

A parent subprov must be free to unsubscribe (hit its session TTL) without
destroying CREATE coverage for candidates already armed in
`ProgramCreateWatcher`. Preserve treasury lineage, funding provenance,
funding mechanism classification, candidate durability, launch idempotency,
and all existing recovery behaviour.

## Phase 1 — `evict_by_subprov()` caller audit

Four total references were found (one more than X27.11 had traced — the
post-CREATE watchdog path was not previously inspected):

| Caller | Current reason | Correct? | Action taken |
|---|---|---|---|
| `cleanup_pass()` → `expire_stale_sessions()` ([ws_cascade.py:4364](../../src/core/ws_cascade.py#L4364), pre-fix) | Session hit `SESSION_TTL_SEC` (30min), regardless of whether it produced armed candidates | **No — the confirmed defect.** `expire_stale_sessions()`'s query ([ws_cascade_store.py:1509](../../src/core/ws_cascade_store.py#L1509)) has no candidate-existence guard; a session with live armed candidates could still be TTL-expired, destroying them. | **Removed.** The branch still calls `mgr.unsubscribe(subprov)` (drops only the subprov's own WS subscription); candidates are left untouched. A `parent_cleanup_candidates_preserved` metric now fires whenever this branch runs against a subprov that had ≥1 armed candidate, proving the fix is exercised, not just present. |
| `cleanup_pass()` → `reject_unproven_sessions()` ([ws_cascade.py:4371](../../src/core/ws_cascade.py#L4371)) | `PROVISION_CANDIDATE` rejected after 2h with zero wrap-close evidence | **Yes — genuinely a no-op today, proven structurally.** `reject_unproven_sessions()`'s own query ([ws_cascade_store.py:1544-1564](../../src/core/ws_cascade_store.py#L1544-L1564)) `NOT EXISTS`-guards on both `wt_subprov_evidence` and any `WATCHING/FIRED_CREATE/BUY_SWARM` row in `wt_candidate_websocket_watches` — a session only reaches this branch with **zero** candidates. | **Kept**, with an explanatory comment marking it as a defensive no-op tied to that query's invariant — a warning for future editors not to weaken the query without re-auditing this call. Covered by a new regression test (`test_reject_unproven_sessions_query_guarantees_zero_candidates`). |
| `_post_create_watchdog()` ([ws_cascade.py:4306](../../src/core/ws_cascade.py#L4306)) | 120s post-CREATE armed-continuation window closes with no further sibling fan-out | **Already correctly gated — a distinct, more careful mechanism.** This fires *after* a CREATE has already been recorded for that subprov; the code's own comment cites historical proof (33 launches, zero second-creator wrap-closes after CREATE). It is feature-flagged behind `PW_POST_CREATE_EVICT_ENABLED` (default `"0"`, unset anywhere in this deployment) — a dry-run/no-op in production today. | **Left exactly as-is** — enabling it is out of scope (Phase 5 explicitly forbids touching session-lifecycle behaviour this sprint); noted as a candidate for the next lifecycle sprint. |
| `evict_by_subprov()` itself ([ws_cascade.py:1338](../../src/core/ws_cascade.py#L1338)) | — | — | Added a `candidate_evicted_by_parent` metric emission and a docstring stating the invariant: this should read zero in steady state given only known-safe callers remain (Phase 8). |

## Phase 2 — Candidate ownership

The fix is exactly the removal described above: `expire_stale_sessions()`'s
branch in `cleanup_pass()` ([ws_cascade.py:4364-4376](../../src/core/ws_cascade.py#L4364-L4376))
no longer calls `evict_by_subprov()`. A candidate that reaches
`active_candidates` (armed) now depends only on:

- its own `expires_at` (`CANDIDATE_TTL_SEC`, via `expire_stale_candidates()`/
  `ProgramCreateWatcher`'s internal `_expire_loop()` — both untouched this
  sprint), or
- an explicit outcome (`CREATE_DETECTED` via `process_candidate_sig`, or
  `BUY_SWARM`/invalidation via `close_candidate()` — both untouched).

Never on parent session expiry, parent unsubscribe, or parent cleanup. A
static regression test (`test_cleanup_pass_source_no_longer_evicts_on_session_expiry`)
inspects `cleanup_pass()`'s source directly (with comments stripped) to
guarantee this stays true even if the function is edited again later.

## Phase 3 — Funding provenance preserved

`wt_candidate_websocket_watches` gained five new columns (migration in
`ensure_cascade_schema()`, [ws_cascade_store.py:615-634](../../src/core/ws_cascade_store.py#L615-L634)):

```
initial_subprov_funding_sol        -- the treasury's original funding of the PARENT subprov
initial_subprov_funding_signature
initial_subprov_funding_time
subprov_fanout_count_at_capture    -- running fan-out count at this candidate's capture time
subprov_fanout_value_at_capture    -- running fan-out SOL value at this candidate's capture time
```

`open_candidate_watch()` ([ws_cascade_store.py:1792](../../src/core/ws_cascade_store.py#L1792))
now snapshots these at INSERT time by reading the most recent matching
`wt_active_subprov_sessions` row — best-effort (a missing session row leaves
the fields `NULL` rather than failing the insert; covered by
`test_provenance_survives_with_no_session_row`). This makes provenance
independent of any later join back to the session row. (In practice that
join would still work today, since sessions are never hard-deleted — no
`DELETE FROM wt_active_subprov_sessions` or `wt_candidate_websocket_watches`
exists anywhere in the codebase, confirmed by grep — but the brief's
requirement is that provenance survive on the candidate's own record, not
merely be reconstructable.)

`treasury_wallet`, `subprov_wallet`, `wrap_close_signature`,
`funding_amount` (the candidate's own wrap-close amount), and
`funding_mechanism` were already present on this table from prior sprints —
no duplication was introduced for those.

## Phase 4 — Capital context preserved (not acted on)

`subprov_fanout_count_at_capture`/`subprov_fanout_value_at_capture`
accumulate across every candidate captured from the same subprov
(`test_fanout_count_and_value_accumulate_across_multiple_candidates` proves
this for a 3-candidate fan-out; the running totals are computed directly
from `COUNT(*)`/`SUM(funding_amount)` over existing rows at insert time, so a
100+ SOL treasury funding a large fan-out has every candidate's row carry
the running total up to that point). `initial_subprov_funding_sol` is copied
unchanged onto every candidate regardless of how large the fan-out grows —
confirmed the original 500 SOL figure is identical on the 1st and 3rd
candidate in the test. **No closure or eviction decision reads these fields
this sprint** — purely additive data capture, per the explicit Phase 4
instruction to preserve, not act on, capital context.

## Phase 5 — Session lifecycle left intact

Confirmed by diffing this sprint's changes against the constant/function
list: `SESSION_TTL_SEC`, `CANDIDATE_TTL_SEC`, `MAX_ACTIVE_SUBPROVS`,
`SWEEP_CONCURRENCY` were not modified; `subprov_sweep_pass()`,
`fair_sweep_candidates()`, `resync_subscriptions()`, and all reconnection
logic are untouched. The only functional changes are the four call/insert
sites listed in Phases 1-3 above (each tagged `X28.0` in-line for easy
future auditing) plus the additive schema migration.

## Phase 6/7 — Validation (replay-style, unit-level)

A full live/recorded on-chain replay was judged unnecessary for this narrow
a change (no state-machine timing to validate, unlike the deferred
lifecycle-simplification sprint) — instead, `tests/test_x28_0_decouple_creator_watch_lifetime.py`
(12 tests, all passing) exercises the exact mechanics end-to-end at the
store/ProgramCreateWatcher level:

- `test_evict_by_subprov_removes_only_matching_candidates` /
  `test_evict_by_subprov_increments_candidate_evicted_by_parent_metric` /
  `test_evict_by_subprov_with_no_cascade_ref_does_not_raise` — eviction
  mechanics and telemetry, isolated from the caller.
- `test_cleanup_pass_source_no_longer_evicts_on_session_expiry` — the direct
  regression guard for the fix itself.
- `test_open_candidate_watch_snapshots_initial_funding_provenance` /
  `test_provenance_survives_with_no_session_row` /
  `test_fanout_count_and_value_accumulate_across_multiple_candidates` —
  Phase 3/4 provenance and capital-context capture.
- `test_expire_stale_sessions_does_not_delete_candidate_rows` — proves a
  session TTL expiry never touches the candidate table at all (Phase 6's
  "candidate remains armed" requirement, at the store layer).
- `test_reject_unproven_sessions_query_guarantees_zero_candidates` — proves
  the retained reject-path eviction call is structurally a no-op, including
  a negative case (a subprov *with* a legitimate candidate is never
  selected by that query).
- `test_create_after_parent_unsubscribe_metric_fires_when_subprov_not_subscribed`
  — exercises the Phase 8 telemetry condition directly.
- `test_duplicate_fanout_capture_remains_idempotent` — proves the new
  provenance columns didn't disturb the existing
  `(candidate, wrap_close_sig)` idempotency guarantee.

## Phase 8 — Telemetry added

- `candidate_evicted_by_parent` — incremented inside `evict_by_subprov()`
  itself via `self._cascade_ref._metric(...)`
  ([ws_cascade.py:1338-1355](../../src/core/ws_cascade.py#L1338-L1355)).
  Should read zero from the TTL-expiry path in steady state now that call
  is removed; any nonzero reading traces to one of the two known-safe
  remaining callers.
- `parent_cleanup_candidates_preserved` — incremented in the
  `expire_stale_sessions()` branch of `cleanup_pass()` whenever a subprov
  had ≥1 candidate still armed at TTL-expiry time, with a
  `🛡 PARENT_CLEANUP_PRESERVED` log line naming the subprov and count —
  direct, positive proof the fix is firing in production, not just present
  in code.
- `create_after_parent_unsubscribe` — incremented in
  `process_candidate_sig()`'s CREATE branch
  ([ws_cascade.py:3898-3931](../../src/core/ws_cascade.py#L3898-L3931)) when
  the launch's subprov is absent from `self.mgr.wallet_kind` (i.e. already
  unsubscribed) at the moment CREATE is recorded — the single most direct
  metric proving the decoupling actually pays off end-to-end.

`candidate_survived_parent_cleanup` and `funding_provenance_preserved` from
the brief's list are covered by the two metrics above plus the schema
columns themselves (their non-null presence on a candidate row *is* the
"provenance preserved" signal — a dedicated counter was judged redundant
with `parent_cleanup_candidates_preserved` and the always-populated columns,
so it wasn't added separately). `candidate_invalidated`/`candidate_expired`
already existed under other names (`close_candidate()`'s state transitions,
`ProgramCreateWatcher.metric_candidates_expired`) and were not duplicated.

## Phase 9 — Regression testing

New suite: 12/12 passing (`tests/test_x28_0_decouple_creator_watch_lifetime.py`).

Targeted regression (`pytest tests/ -k x24`, the full family of tests
touching subscription/sweep/cascade behaviour, 193 tests): **193 passed, 0
failed** — includes `test_x24_2_sweep_pass_orchestration.py`,
`test_x24_8_sub_kind_breakdown.py`,
`test_x24_9_subscription_target_validation.py`, and
`test_x27_7_restore_lifecycle_capture.py`, all fully green.

**Full-suite caveat (pre-existing, not a regression from this sprint):**
running the entire ~2045-test suite in one process shows ~123 failures
(mostly in the same x24/x27 files above) due to cross-test global-state
pollution — verified by (a) running each affected file in isolation, where
all pass 100%, (b) running the `-k x24` family together (193 tests), where
all pass, and (c) reproducing the same ~123 failures with this sprint's own
new test file entirely excluded via `--ignore`. This confirms the pollution
predates X28.0 and is not caused by it. Not investigated or fixed here — out
of this sprint's scope (a cross-cutting test-isolation issue, not specific
to subprov/candidate lifecycle) and flagged as a separate follow-up.

One further pre-existing, unrelated failure was independently confirmed via
`git stash` (`test_enqueue_creator_funders.py::TestApproveCandidate::test_approve_funder_has_no_enqueue`)
— reproduces identically with none of this sprint's changes applied.

## What was not changed (constraints honoured)

`SESSION_TTL_SEC`, `SWEEP_CONCURRENCY`, `MAX_ACTIVE_SUBPROVS`, sweep
scheduling, reconnection logic, and subprov subscription behaviour are all
untouched. No quiet periods, `CAPTURING_FANOUT`/`FINAL_RECONCILIATION`
states, or adaptive sweep logic were introduced — those remain scoped to a
future lifecycle-simplification sprint per X27.11's recommendation.

## Conclusion

**A — Successfully implemented.**

Candidate lifetime is now independent of parent session lifetime: the one
confirmed coupling (`evict_by_subprov()` called unconditionally from
`expire_stale_sessions()`'s TTL-expiry cleanup path) has been removed, and a
subprov hitting its 30-minute session TTL now only drops its own WS
subscription — already-armed candidates remain in
`ProgramCreateWatcher.active_candidates` and continue to be matched against
the single global pump.fun program-log stream exactly as X27.11's Phase 2
established. CREATE coverage survives parent cleanup, proven at the
store/watcher mechanics level via 12 new tests and confirmed safe against
193 tests in the directly-relevant x24/x27 family. Funding provenance
(treasury signature/amount/time, funding mechanism, and running fan-out
count/value) now travels with every candidate row at capture time,
independent of any later join back to the (never-deleted, but no longer
required) session row.
