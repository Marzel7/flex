# X64.7A — Phases 5-7: Deployment and Live Validation (Deferred)

## Status: intentionally not performed in this task

Per explicit direction, Phases 5 (planned deployment/restart), 6
(bounded live shadow validation, 2+ hours or 50+ CREATE events), and 7
(production regression fixtures captured from live traffic) were **not**
executed in this session. This mirrors X64.7's own Phase 12 deferral for
the same underlying reason: the production `pumpfun_curve_listener`
process was already running when this task began, and restarting it
mid-investigation to load code changes is a deployment action requiring
its own maintenance window, monitoring plan, and rollback readiness — not
something to fold into implementation/audit work.

## What is ready for that deployment window

All of Phase 1-4 and 8's code changes are complete, tested (32 new tests
across `test_x64_7_create_event_ledger.py`'s pre-existing 23 and the new
`test_x64_7a_commit_hardening.py`'s 17 — see
`x64_7a_regression_results.md`), and verified not to regress any existing
behavior. Specifically ready:

- Two-stage ledger write in `handle_birth` (PENDING → RESOLVED/UNRESOLVED)
- Durable pending-write retry queue, wired into `walkback_worker.py`'s
  ordinary `run_loop()` cycle
- Migration coverage check, wired into `store_migration()`
- `reconcile_waiting_create_anchors()` now genuinely ledger-aware

## Pre-restart checklist (Phase 5, for the deployment window)

Per the task's explicit requirements, before restarting. Baseline values
already confirmed as of this task's completion (2026-07-21), to be
re-checked immediately before the actual restart since the queue is
live:

- [ ] Record current listener PID and start time
- [x] `wt_create_event_ledger` row count: **table does not yet exist in
      the live ops DB** — confirmed via direct query
      (`no such table: wt_create_ledger_pending`/analogous for the
      ledger table). Both new tables are created automatically via
      `ensure_schema()` on first real call after deployment — this is
      expected, not an error.
- [x] `wt_create_ledger_pending` row count: same — table does not yet
      exist, will be created on first use.
- [x] `WAITING_FOR_CREATE_ANCHOR` count: **39** (confirmed live,
      2026-07-21 — grown from X64.7's 33 baseline, consistent with the
      queue continuing to receive new migrations at the established
      rate while this task's code was not yet deployed).
- [ ] Record current `MINT_NOT_FOUND` count (per
      `anchor_reconciliation.dry_run_report()`) — re-check immediately
      before restart, since this drifts continuously.
- [ ] Verify the supervisord restart command/procedure
      (`run_listener.sh`'s own header: "supervisord owns the Python
      process")
- [ ] Verify a rollback procedure exists (revert the 3 modified files,
      restart again) — this task's changes are additive-only at the
      schema level (new tables, no altered/dropped columns), so rollback
      risk is limited to the listener code changes themselves, not data
      loss

## Post-restart verification (Phase 5)

- [ ] Process returns healthy (confirm via existing health-check
      mechanism)
- [ ] WS subscriptions reconnect (confirm via existing connection logs)
- [ ] CREATE and migration event streams resume (confirm
      `CREATE_TX_RECEIVED` log lines appear)
- [ ] No import or schema errors in the first few minutes of logs
- [ ] Old process (recorded PID) is confirmed no longer running
- [ ] New process confirmed running the expected code revision (e.g. via
      git SHA in a startup log line, if the deployment tooling supports
      it — not verified as existing in this codebase, flagged as a gap
      if not already present)

## Bounded live shadow validation (Phase 6) — procedure, not yet run

Observe for the required window (2+ hours or 50+ validated CREATE
events, whichever comes first) and tabulate:

```
CREATE_TX_RECEIVED count
CREATE_INSTRUCTION_FOUND count
CREATE_LEDGER_WRITE_ATTEMPT count
CREATE_LEDGER_WRITE_COMMITTED count
CREATE_LEDGER_WRITE_FAILED count
pending retry rows created (wt_create_ledger_pending inserts)
pending retry rows successfully replayed (retry_pending_writes 'recovered')
creator-null initial writes (creator_resolution_state='PENDING' on first write)
later creator enrichments (PENDING -> RESOLVED transitions)
migrations observed (store_migration calls)
migrations with ledger row (wt_migration_ledger_coverage = PRESENT or PENDING)
migrations with pending ledger write (would require cross-referencing
  wt_create_ledger_pending by mint at migration time — not directly
  tracked by wt_migration_ledger_coverage's current schema; a query
  joining the two tables by mint would answer this)
migrations with no ledger evidence (MISSING)
ledger conflicts (wt_create_ledger_conflicts inserts during the window)
```

**Ledger commit coverage** = committed unique CREATE signatures /
validated unique CREATE signatures, target 100%, any deficit must show
an explicit pending row or documented hard conflict — this calculation
requires the live counts above, not available until the deployment
window runs.

## Production regression fixtures (Phase 7) — not yet captured

Requires live traffic: 1 CREATE with creator known immediately, 1 with
creator initially NULL, 1 duplicate observation, 1 CREATE followed by
migration, 1 forced/simulated ledger-write-failure-recovered-by-retry.
None of these can be honestly captured from real production events
without the deployment happening — captured instead as synthetic unit
tests in `test_x64_7a_commit_hardening.py` (tests 1-8), which exercise
the identical code paths with controlled inputs. The live-fixture
requirement remains open until Phase 6's observation window runs.

## Recommendation

Treat Phases 5-7 as "X64.7B — Production Deployment and Live
Validation," executed as its own scheduled maintenance window, using
this document's checklists directly. Nothing about Phases 1-4/8's
implementation requires re-deriving any analysis before that window —
the code is complete and tested; only its live behavior remains
unobserved.
