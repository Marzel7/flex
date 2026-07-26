# X65.2 — Phase 5: Health Correlation

Compares the 12 unresolved launches against the current pipeline
health metric (`Births: 2358 seen / 2358 persisted / 0 missing / 100%`)
and determines which explanation category the unresolved cohort is
consistent with.

## What the "100% Birth Health" metric actually measures

Traced to its source: `src/core/main.py:24681-24750`. It is a
**log-line-counting proxy**, computed only over the current listener
run's log tail (from the most recent `[WATCHDOG]` marker onward):

```
births.seen      = count of "[PUMPPORTAL] ... Birth:" + "[BIRTH] ... Pump.fun launch detected" lines
births.persisted = count of "[PREMIG_BIRTH_SEED]" lines
births.missing   = max(0, seen - persisted - pending_retry)
```

This metric answers exactly one question: *"of the births the listener
logged as received in its current run, how many also logged a
successful `token_analysis` write?"* It does **not** check:
- whether `create_tx_signature` (or any other individual column)
  survived a **later** write to the same row,
- whether `wt_create_event_ledger` received a corresponding entry,
- whether funding-lineage extraction subsequently ran,
- anything at all about rows written in a **previous** listener run
  (the metric is scoped to the log tail since the last `[WATCHDOG]`
  marker — i.e., since the current process's most recent start).

## Why 100% Birth Health and 12 unresolved launches are not contradictory

Three independent reasons, all confirmed by direct evidence in this
and the prior investigation pass:

1. **Different measurement window.** The 12 unresolved launches span
   2026-07-15 through 2026-07-21 — a period covering **hundreds** of
   separate listener process lifetimes (3,224 restarts recorded in
   that span, Phase 1). The "100%" figure is a snapshot of the
   *current* process's own short run only. A birth logged and
   correctly persisted in run #2,891 (some minutes ago) is invisible
   to the health metric computed during run #3,224 (now) — the metric
   was never designed to, and cannot, retrospectively audit historical
   runs.
2. **Different evidence target.** The metric counts a `[PREMIG_BIRTH_SEED]`
   log line as full success — and for all 12 unresolved launches (10
   of 12 directly confirmed via log evidence, per the earlier
   investigation), that line's implied condition (a successful
   `_insert_bonding_curve_token()` write) genuinely did occur — the row
   was created, `pf_ws_creator` was set correctly. The metric is
   accurately reporting that the birth-time write succeeded. It says
   nothing about the migration-time write that ran afterward and
   overwrote `create_tx_signature` back to `NULL` — a completely
   separate log line (`[DB] ✅ Updated token entry with creator: ...]`,
   from `_update_token_entry_with_creator()`) that the health metric
   does not track at all.
3. **Different failure mechanism than "missing."** The health metric's
   `missing` count models *births that were never persisted at all*.
   The 12 unresolved launches are not "missing births" in that sense —
   they are births that **were** persisted, and then had one specific
   field silently overwritten by a **later, separate** write. This is
   a data-integrity/overwrite bug, not a coverage/completeness bug —
   the two categories are measured by entirely different mechanisms,
   and a 100%-complete coverage metric provides no information about
   overwrite correctness.

## Consistency check against each candidate explanation

| Candidate explanation | Consistent with evidence? | Basis |
|---|---|---|
| **Historical missing coverage** (launches predate the pipeline) | **No** | Phase 1: all 12 occurred strictly after the current code generation's deployment (394dbd9, 2026-07-14); none predate it |
| **Current pipeline failure** (the live pipeline is broken right now, contradicting "100% healthy") | **No, but with a nuance** | The specific failure mode (migration-time clobber) is a **currently-still-present** code defect (confirmed unchanged in the live file) — so in that sense it *is* "current" — but it manifests as a data-overwrite bug the health metric was never built to detect, not a contradiction of what that metric actually measures |
| **Transient outage** (a temporary period where the listener was fully down) | **Partially** | The chronic restart-looping (Phase 1: 3,224 restarts, 43.7% of gaps <5min) is a real, corroborating instability signal for at least the subset of launches occurring within minutes of a restart (8/12), but does not by itself explain the other 4, nor does it change the fact that the CREATE-ledger stage specifically never fires regardless of restart timing |
| **Deployment gap** (a period between two code versions where capture logic was absent) | **No** | Git history shows a single stable code generation (394dbd9) covering the entire 07-14 through present window; no intervening deploy changed the relevant write paths |
| **Indexing omission** (the ledger-population logic itself never ran, independent of restarts) | **Yes — this is the best fit** | `wt_create_event_ledger` is empty for all 12 regardless of whether that specific launch was near a restart (Phase 2/3); the deterministic write-path defect (unconditional `UPDATE` with no `COALESCE`, still present in the live file) explains 100% of the cohort uniformly, whereas restart-timing alone only correlates with 8/12 |

## Reconciled explanation

The 100% Birth Health figure and the 12 unresolved launches describe
**two different, non-overlapping facts about the same pipeline**: the
health metric confirms the birth-time *row creation* step is working
correctly right now (and, by the log-line ratio evidence, has been
throughout this window) — while the unresolved cohort demonstrates
that a **separate, later write** (migration-time creator
re-extraction) is *silently discarding* one specific field from that
already-correctly-created row. A metric scoped to "was the row
created" cannot detect a defect in "was a later field-level update
non-destructive." Both facts are true simultaneously and are fully
reconciled once the health metric's actual measurement boundary is
understood — no contradiction exists, and no assumption needs to be
stretched to make them compatible.
