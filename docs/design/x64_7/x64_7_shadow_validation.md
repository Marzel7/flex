# X64.7 — Phase 12: Live Shadow Validation

## Status: intentionally deferred

The production `pumpfun_curve_listener` process (confirmed running, PID
37340 at the time of this audit) had **not** loaded this task's code
changes at the time this document was written — Python processes do not
hot-reload, and this task's edits to `pumpfun_curve_listener.py` (the
`handle_birth` instrumentation and ledger write) only take effect on the
next process restart.

**No live restart was performed during this investigation.** Restarting
a running production listener is an operational deployment action, not
an audit activity — it changes live production state, and the
consequences of doing so mid-investigation (harder to distinguish
"caused by the new code" from "caused by the restart itself," no planned
monitoring window, no rollback plan pre-arranged) outweigh the benefit of
observing Phase 12's metrics inside this same session.

## What Phase 12 requires, once deployed

For a bounded live observation period after a deliberate, planned
restart:

```
CREATE transactions detected:        (count of CREATE_TX_RECEIVED events)
Ledger writes attempted:             (count of CREATE_LEDGER_WRITE_ATTEMPT)
Ledger writes committed:             (count of CREATE_LEDGER_WRITE_COMMITTED)
Duplicate observations:              (ENRICHED-state results within the window)
Creator unresolved at write time:    (CREATE_CREATOR_RESOLVED with creator=UNRESOLVED)
Later creator enrichments:           (second record_create_event call for
                                       an already-ledgered signature that fills
                                       a previously-NULL creator)
Funding jobs enqueued:                (existing _enqueue_creator_funding_job
                                       call count, unchanged by this task)
Migrations observed:                  (existing migration event count)
Migrations with ledger anchor:        (mints reaching migration whose
                                       wt_create_event_ledger row already
                                       exists by migration time)
Migrations without ledger anchor:     (THE key metric — every such case
                                       must alert)
```

## The key metric and its alerting requirement

Per the task's explicit instruction, **"migration observed but no
canonical CREATE ledger row" must produce an explicit alert and
structured failure reason** — not this task's own scope to implement a
new alerting/paging mechanism (out of scope: this repo has no existing
alerting infrastructure this session identified to hook into), but the
**structured failure reason** half of that requirement is already
satisfied by this task's Phase 4 instrumentation: any mint reaching
migration without a ledger row will, by construction, have logged one of
`CREATE_PARSE_REJECTED` (with a `reason=` field), `CREATE_LEDGER_WRITE_FAILED`
(with `reason=`/`conflict=` fields), or simply no `CREATE_TX_RECEIVED`
line at all for that mint (meaning `handle_birth` was never invoked for
it) — all three cases are now distinguishable from logs in a way they
were not before this task (see `x64_7_call_graph.md`'s "silent failure
paths" section). Wiring these log lines into a dashboard/alert is a
reasonable, small follow-up task, not performed here.

## Recommended validation procedure for the deployment window

1. Schedule a planned restart of `pumpfun_curve_listener` (supervisord-
   managed, per `run_listener.sh`'s own header comment).
2. Immediately after restart, tail logs for the six new event names
   (`CREATE_TX_RECEIVED` through `CREATE_ENRICHMENT_SKIPPED`) to confirm
   they're firing as expected on real traffic.
3. Over a bounded window (recommend 1-2 hours, enough to observe
   several dozen real CREATE events given this system's observed
   ~1 launch/18min rate from X64.6's own timing analysis), tabulate the
   metrics above directly from logs or by querying
   `wt_create_event_ledger`'s row count and `wt_walkback_queue`'s
   `WAITING_FOR_CREATE_ANCHOR` population before/after.
4. Confirm the `WAITING_FOR_CREATE_ANCHOR` population's growth rate
   drops relative to the pre-deployment baseline (X64.6 measured ~1
   new stuck row per ~18 minutes on average) — a successful deployment
   should show new CREATE-observed mints landing in the ledger even when
   their creator resolves later or never, meaning fewer NEW
   `MINT_NOT_FOUND` rows going forward (existing already-stuck rows are
   unaffected by this deployment alone — they require the separate
   reconciliation pass already implemented in X64.5/X64.6, now also able
   to check the ledger via `resolve_anchor_with_priority`).

This procedure is documented here as the plan; executing it is a
deployment action for the user/operations team to schedule, not part of
this audit task's own deliverables.
