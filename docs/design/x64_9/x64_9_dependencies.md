# X64.9 — Phase 2: Dependency Validation

For every X64.8 cleanup candidate, this phase documents every dependency
found (code, scheduled task, dashboard, reconciliation process,
background worker) and classifies the candidate as **SAFE**,
**INVESTIGATE**, or **BLOCKED**.

## `funder_networks` (hot-DB copy)

- Production code: **none** (all reads/writes redirected to `arch.funder_networks`)
- Scheduled task: none found
- Dashboard: none (dashboard reads only the archive copy)
- Reconciliation process: none
- Background worker: none

**Classification: SAFE.**

## `wt_subprov_sig_retry` — DONE/FAILED rows only

- Production code: `due_subprov_sig_retries()` in `ws_cascade_store.py:1972-1990`
  is the sole reader, and it filters `WHERE r.status='PENDING' OR (r.status='RUNNING' AND ...)`.
  No other function in `ws_cascade.py`, `ws_cascade_store.py`, or
  `alert_evaluator.py` queries this table without a status filter that
  excludes DONE/FAILED.
- Scheduled task: `walkback_worker.py`'s retry-consumption loop only
  ever touches PENDING/RUNNING rows via the same function above.
- Dashboard: `alert_evaluator.py:152,282` reports a PENDING-count health
  metric only — does not read DONE rows.
- Reconciliation process: none found reading DONE/FAILED rows.
- Background worker: `ws_cascade.py` writes DONE status on completion
  but has no code path that reads it back afterward.

**Classification: SAFE** — but scoped strictly to `WHERE status IN ('DONE','FAILED')`.
PENDING/RUNNING rows (60 total) must never be touched by any purge job.

## `wt_candidate_websocket_watches` — EXPIRED rows

- Production code: **dependency found.** `ws_cascade_store.py:771-774`
  (inside a function determining "has this wallet been seen as a
  subprov candidate before" — used by at least one upstream caller
  deciding whether to treat a wallet as known-vs-novel) runs:
  ```sql
  SELECT 1 FROM wt_candidate_websocket_watches WHERE subprov_wallet=? LIMIT 1
  ```
  This has **no state filter** — it matches EXPIRED, EXPIRED_SIBLING,
  BUY_SWARM, AUDIT_ONLY, and FIRED_CREATE rows identically. Deleting
  EXPIRED rows changes this function's answer for any wallet whose
  *only* history is now-expired watches.
- Scheduled task: none found directly consuming EXPIRED rows on a schedule.
- Dashboard: `operation_dashboard_routes.py` mostly filters by
  `state='WATCHING'` or `state='RESOLVED_CREATE'` explicitly — these
  dashboard queries are unaffected by an EXPIRED-row purge.
- Reconciliation process: none found reading EXPIRED rows specifically.
- Background worker: `ws_cascade.py`/`ws_cascade_store.py` write
  EXPIRED as a terminal state but the only *read* of that terminal
  state (beyond the unfiltered existence check above) found in this
  pass is via `expire_all_candidates_for_subprov()`, which only reads
  `WHERE state='WATCHING'` before transitioning rows to EXPIRED — it
  does not read already-EXPIRED rows.

**Classification: INVESTIGATE.** Not BLOCKED (the dependency is narrow —
a single existence-check function, not a core detection path), but not
SAFE either. Two safe paths forward: (a) refactor the existence check at
`ws_cascade_store.py:771-774` to explicitly query a small "wallet ever
seen" summary/marker instead of scanning the full watches table, then
purge freely; or (b) scope any purge to preserve one row per
`subprov_wallet` (e.g. the most recent EXPIRED row) rather than deleting
all of them, preserving the existence check's behavior while still
reclaiming the bulk of the storage. Do not purge EXPIRED rows wholesale
without one of these mitigations.

## `wt_active_subprov_sessions` — EXPIRED rows

- Production code: **dependency found, and it is load-bearing.**
  `ws_cascade_store.py` contains three functions (approx. lines 1630-1660,
  1695-1730, 1730-1755) that explicitly query
  `wt_active_subprov_sessions WHERE s.state = 'EXPIRED'` as the *input*
  to an "operational-spend-proxy" classifier — this looks for a
  Hello-program funding-linkage pattern (matches this project's own
  established "Hello program operator linkage" detection concept) and
  **writes a new `session_tag` back onto EXPIRED rows** based on what
  it finds (`UPDATE wt_active_subprov_sessions SET session_tag = ...
  WHERE id = ?`). This means EXPIRED rows are not just read but
  actively mutated by ongoing classification passes.
- Additionally, `ws_cascade_store.py:775-778`'s existence check
  explicitly includes `state IN ('EXPIRED','COMPLETED')` as valid
  "this wallet has known history" evidence.
- Scheduled task: the classifier functions above appear to run as part
  of the same cascade/session-management cycle as live session
  handling (found alongside `ws_cascade.py`'s live-session code, not a
  separate cron job) — meaning they may run frequently, not just once.
- Dashboard: `operation_dashboard_routes.py` filters `state='ACTIVE'`
  in most read paths (lines 1753, 2248, 2407, 4212, 4487) — these are
  unaffected by an EXPIRED-row purge.
- Reconciliation process: the operational-spend-proxy classifier above
  effectively *is* a reconciliation process, and it depends directly on
  EXPIRED rows persisting.
- Background worker: none additional found beyond the above.

**Classification: BLOCKED.** This table's EXPIRED rows are actively
read AND mutated by a live classification pass, not just historically
referenced. A wholesale purge would silently and permanently disable
the operational-spend-proxy/Hello-linkage classifier for any
already-expired session not yet classified, and would delete the
`session_tag` evidence already written for sessions that were
classified. **No purge of this table should proceed without first
either (a) confirming the classifier has a bounded look-back window
(e.g. only considers sessions expired in the last N days) and scoping
any purge to rows older than that window, or (b) migrating the
`session_tag` conclusions to a separate, smaller summary table before
purging the source rows.** This is a genuine engineering task, not a
simple purge job.

## `rpc_response_cache`

- Production code: `rpc_cache.py` has three internal delete paths
  (lines 135, 219, 248) plus `pumpfun_curve_listener.py:4069` — all
  TTL-based, all already running as part of normal cache operation.
- Scheduled task: the bulk expiry delete (`rpc_cache.py:248`,
  `pumpfun_curve_listener.py:4069`) appears to run inline as part of
  normal request/cache-check flow, not as a separate cron job — but
  this means it fires continuously during normal operation, which is
  equivalent in effect to (arguably better than) a scheduled job.
- Dashboard: `main.py:25405-25409` reports an "expired but
  not-yet-purged" count as an observability metric, confirming the
  system already expects some transient lag between expiry and
  physical deletion, by design.

**Classification: SAFE — no action needed.** This table already has a
working, self-maintaining eviction mechanism. It should be removed from
any future cleanup-candidate list entirely.

## `coordinated_creator_edges`

- Production code: read continuously by `main.py` (10+ call sites) and
  the graph-analyzer API registration.
- Scheduled task: `scripts/run_graph_analyzers.py` runs every 4 hours
  via cron, **but has been skipped on every single run** since at
  least the last several log entries checked, due to a `[QUEUE_GUARD]`
  deferring to a backlogged `creator_funding_ready` queue
  (`oldest_ready_age≈2,006,384s`, ~23 days, and climbing).
- Dashboard: multiple `main.py` routes read this table directly for
  coordination views.
- Reconciliation process: none specific to this table found beyond the
  graph-analyzer job itself.
- Background worker: the graph-analyzer job is the only writer, and it
  is currently non-functional in practice (guard always skips it).

**Classification: INVESTIGATE — but reclassified as a separate,
higher-priority operational issue, not a lifecycle/storage item.** The
underlying problem (a `creator_funding_ready` backlog stuck for 3+
weeks, blocking the graph-analyzer's own queue guard) should be
triaged as its own incident, independent of this audit's storage scope.
Fixing it will also resolve the `coordinated_creator_edges` staleness
as a side effect.

## `sol_transfers`

- Production code: **extensively depended on** — `webhook_handler.py`
  (writer, table creation, insert-on-webhook), `webhook_worker.py`
  (multiple self-joins for network analysis), `main.py` (10+ read call
  sites for creator/activity views), `rpc_metrics_api.py` (cost-per-event
  metrics), `webhook_creator_ranker.py` (creator ranking/scoring),
  `webhook_api_enriched.py` (API responses), `helius_webhook_sync.py`
  (fallback data source).
- Scheduled task: none directly, but it's the backing store for the
  entire webhook-driven creator-funding pipeline.
- Dashboard: yes, multiple `main.py` dashboard/API routes.
- Reconciliation process: `webhook_worker.py`'s network-analysis
  self-joins depend on this table's contents directly.
- Background worker: the whole webhook ingestion pipeline (currently
  idle per this project's own "Outbound worker disabled" memory, not
  because this table is unused).

**Classification: BLOCKED — remove from cleanup-candidate list
entirely.** This table is core infrastructure for an entire (currently
dormant, not dead) subsystem. Any cleanup action here would need to
first resolve whether/when the webhook pipeline is re-enabled, which is
an entirely separate decision from this audit's scope.

## Zero-reference small tables (`cross_network_senders`, `oneoff_hub5e1_outbound`)

- Production code: zero references found for either table, via a
  precise (non-loose) grep for the exact table names.
- Scheduled task: none found.
- Dashboard: none found.
- Reconciliation process: none found.
- Background worker: none found.

**Classification: SAFE**, for these two specific, individually-verified
tables. **The remainder of X64.8's "9 zero-row + 6 stale" set is
classified INVESTIGATE** — not individually re-verified in this pass,
and given that 2 of the ~10 candidates checked in this same batch
(`rpc_response_cache`, `sol_transfers`) turned out to be **wrong** in
the original audit, the unverified remainder should not be assumed safe
by association. Each must be individually confirmed with the same
precise-grep + row-recency method used here before any batch action.

## Summary table

| Candidate | Classification | Key reason |
|---|---|---|
| `funder_networks` (hot copy) | **SAFE** | Zero dependencies of any kind, confirmed twice now |
| `wt_subprov_sig_retry` (DONE/FAILED rows) | **SAFE** (scoped) | Only reader explicitly excludes these statuses |
| `wt_candidate_websocket_watches` (EXPIRED rows) | **INVESTIGATE** | Narrow but real existence-check dependency found |
| `wt_active_subprov_sessions` (EXPIRED rows) | **BLOCKED** | Actively read AND mutated by a live classifier |
| `rpc_response_cache` | **SAFE — no action needed** | Already self-maintaining; remove from candidate list |
| `coordinated_creator_edges` | **INVESTIGATE** (reclassified) | Symptom of a separate stuck-queue incident |
| `sol_transfers` | **BLOCKED — remove from candidate list** | Extensively depended on by a dormant-not-dead subsystem |
| `cross_network_senders`, `oneoff_hub5e1_outbound` | **SAFE** | Individually verified, zero dependencies |
| Remainder of "9+6" zero-reference set | **INVESTIGATE** | Not individually re-verified; do not batch-assume safety |
