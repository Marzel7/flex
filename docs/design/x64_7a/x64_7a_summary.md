# X64.7A — Master Summary

Companion documents: [x64_7a_commit_hardening.md](x64_7a_commit_hardening.md)
(Phases 1-4, 8 — implementation detail),
[x64_7a_deployment_deferred.md](x64_7a_deployment_deferred.md)
(Phases 5-7 — deferred, procedure documented),
[x64_7a_regression_results.md](x64_7a_regression_results.md) (test
results, including a real bug caught and fixed during test-writing).

## Scope actually completed in this session

Phases 1, 2, 3, 4, and 8 — all code, schema, and tests. Phases 5, 6, and
7 — deliberately deferred to a separate, planned production deployment,
per explicit direction, for the same reason X64.7's own Phase 12 was
deferred: restarting a live production process and observing real
traffic is an operational deployment decision, not implementation work,
and bundling it into this session would risk conflating "did the code
change cause an issue" with "did the restart itself cause an issue."

## Required summary

Per the task's own instruction, most of these metrics require the
deferred live observation window (Phase 6) to populate honestly — they
are reported as **not yet measured** rather than fabricated or estimated:

- **Validated CREATE events**: not measured (Phase 6 deferred)
- **Initial ledger commits**: not measured (Phase 6 deferred)
- **Initial ledger failures**: not measured (Phase 6 deferred)
- **Durable pending writes**: not measured (Phase 6 deferred) — the
  mechanism is implemented and tested (`wt_create_ledger_pending` does
  not yet exist in the live ops DB, confirmed by direct query, since no
  write has ever been attempted against it in production)
- **Pending writes recovered**: not measured (Phase 6 deferred)
- **Creator-null events persisted before inference**: not measured live,
  but the *mechanism* is proven by unit test
  (`test_two_stage_write_commits_pending_before_creator_known`,
  `test_creator_inference_exception_after_pending_write_leaves_ledger_row_intact`)
- **Migrations observed**: ongoing in production, unaffected by this
  task (migration recording itself was never touched)
- **Migrations with ledger anchor**: not measured (Phase 6 deferred) —
  `wt_migration_ledger_coverage` does not yet exist in the live ops DB
- **Migrations with pending ledger write**: not measured (Phase 6
  deferred)
- **Migrations genuinely missing ledger evidence**: not measured (Phase
  6 deferred)
- **Ledger conflicts**: 0 in testing (no conflicting real-world data
  observed yet, since the ledger has no production rows)
- **New `WAITING_FOR_CREATE_ANCHOR` rows**: confirmed **39** as of this
  task's completion (2026-07-21), up from X64.7's 33 — the queue
  continued growing at its established rate throughout this
  implementation-only session, since the fix has not yet been deployed
  to reduce that growth rate

### Answers to the task's explicit questions

**Is the ledger committed before creator inference begins?**
**Yes, now confirmed in code and by test** — the PENDING write in
`handle_birth` happens strictly before `_infer_creator_from_tx` is
called. Not yet confirmed in live production traffic (Phase 6 deferred).

**Can a ledger-write failure survive restart and retry automatically?**
**Yes, confirmed by test** — `wt_create_ledger_pending` persists the
full retry payload to disk (SQLite `conn.commit()` via the same
serialized write lane every other durable write in this listener uses),
survives a simulated restart (fresh connection to the same file), and
`retry_pending_writes()` — now wired into `walkback_worker.py`'s
ordinary `run_loop()` cycle — recovers it automatically, zero-RPC,
bounded backoff. Not yet observed recovering a real production failure
(Phase 6 deferred; no real failure has occurred yet since the code isn't
deployed).

**Does production migration processing explicitly detect a missing
ledger row?**
**Yes, in code** — `store_migration()` now calls
`_record_migration_coverage()`, which queries the ledger and persists
`MIGRATION_CREATE_LEDGER_MISSING` (plus a structured log line) for
exactly this condition. Non-blocking, tested. Not yet exercised against
real migration traffic in production (Phase 6 deferred).

**Does the ordinary walkback worker consume ledger anchors?**
**Yes, confirmed — and this was a genuine, independently-verified gap
before this task.** `reconcile_waiting_create_anchors()`, the function
`walkback_worker.py`'s `run_loop()` actually calls every cycle, now
routes through `resolve_anchor_with_priority()` (ledger-first) before
falling back to the legacy widened-source search. Verified by test that
a signature existing **only** in the ledger is recovered by this exact
production entry point, not merely by the standalone helper function in
isolation.

**What percentage of validated live CREATE events reached durable
storage?**
**Not measurable yet** — requires the Phase 6 live observation window.

**Did any creator-null CREATE fail to persist?**
**Not measurable yet in production.** In testing: no — every
creator-null `record_create_event()` call in the test suite succeeded
unconditionally (mint is the only required field, unchanged from X64.7,
now additionally protected by the two-stage-write ordering fix so a
downstream exception can no longer retroactively prevent it either).

**Did the stuck-anchor population continue to grow after deployment?**
**Not applicable yet — no deployment has occurred.** The population grew
from 33 (X64.7's snapshot) to 39 (this task's completion) purely because
the fix has not been deployed; this is the expected, unremarkable
continuation of the pre-existing growth rate, not a new finding.

## Success criteria — assessed

- **"Validated CREATE + resolved mint → durable ledger row or durable
  pending retry occurs before creator inference or enrichment can affect
  the event"**: **true in code, verified by test**; not yet verified in
  live production traffic (Phase 6 deferred, by explicit, documented
  decision).
- **"Live production validation demonstrates that migration anchoring no
  longer silently depends on funding extraction"**: **the mechanism
  exists and is provably wired into the production worker cycle**
  (Phase 4's core finding and fix); the live *demonstration* itself
  awaits the deferred deployment window.

X64.7A's implementation is complete and independently tested to a
standard consistent with the rest of this session's X64.x work. Its
success criteria's *live-validation* half remains explicitly open,
honestly reported as such, pending a follow-up deployment task.
