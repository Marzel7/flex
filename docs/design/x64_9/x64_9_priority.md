# X64.9 — Phase 7: Priority Matrix

Ranked using operational benefit, engineering effort, production risk,
storage reclaimed, and confidence — not storage size alone. This
directly informed the ordering below; several items with larger
storage numbers rank lower than smaller items because their risk or
effort is disproportionately higher.

## Immediate

| Item | Operational benefit | Engineering effort | Production risk | Storage reclaimed | Confidence |
|---|---|---|---|---|---|
| Remove `funder_networks` hot-DB copy (`DROP TABLE`, deferring `VACUUM`) | High — frees the single largest disk consumer, directly relieves the disk-full pressure this entire X64.7-X64.9 arc has been managing around | Low — a single `DROP TABLE`, no code change | Low — zero dependencies confirmed twice | ~2.86GB | High |
| Investigate the `creator_funding_ready` queue backlog blocking the graph-analyzer's `QUEUE_GUARD` | High — this is a live, ongoing operational degradation (23+ days and climbing), not a storage issue; likely has knock-on effects beyond just `coordinated_creator_edges` staleness | Unknown until investigated — likely low effort to diagnose, unknown effort to fix depending on root cause | Currently already degraded (the guard is doing its job correctly by deferring, but the underlying backlog is a symptom of something wrong) | None (not a storage item) | Medium — root cause of the guard-trigger confirmed, root cause of the backlog itself not yet investigated |

## High

| Item | Operational benefit | Engineering effort | Production risk | Storage reclaimed | Confidence |
|---|---|---|---|---|---|
| Purge `wt_subprov_sig_retry` DONE/FAILED rows (scoped, batched) | Medium — reduces ops-DB size and query-plan overhead on a table that's 16% of the ops DB | Low-Medium — batching loop + status-scoped WHERE, code-verified safe | Low — confirmed zero readers of these rows | ~393MB | High |
| Build the daily retention jobs for the above (Phase 5 design) | High — converts a one-time cleanup into a permanent fix, preventing re-accumulation | Medium — needs a scheduled script, logging, and the batching/pause pattern | Low — same scoping as the one-time purge | Ongoing (prevents re-growth) | High |

## Medium

| Item | Operational benefit | Engineering effort | Production risk | Storage reclaimed | Confidence |
|---|---|---|---|---|---|
| Refactor the `wt_candidate_websocket_watches` existence check (Phase 4 Design 3, option a) OR implement the retain-latest-per-subprov purge mitigation (option b) | Medium — unblocks a real 319MB reclaim opportunity currently stuck behind a hidden dependency | Medium — either a small code refactor or a slightly more complex scoped-delete query, plus a mandatory regression test before trusting it | Medium — this is exactly the kind of change that silently breaks a downstream classifier if done wrong, per Phase 2's finding; must not be rushed | ~190-290MB (partial, per Phase 3's revised estimate) | Medium — the mitigation approach is sound but unvalidated against real data yet |
| Design and implement archive tooling for `prediction_decision_context` + `token_prediction_events` | Medium — meaningful long-term storage relief and matches this project's existing archive precedent | High — requires actual code changes to make readers archive-aware (not just a data move), per Phase 4 Design 5 | Medium — any half-finished archive-awareness change risks readers silently missing archived rows | ~454MB | Medium — the data-move mechanics are proven, the code-repointing work is new |

## Low

| Item | Operational benefit | Engineering effort | Production risk | Storage reclaimed | Confidence |
|---|---|---|---|---|---|
| Investigate the ~13 remaining unverified "9+6" zero-reference small tables individually | Low individually, but closes an open verification gap this audit flagged (2 of 2 similar candidates checked so far turned out wrong in the original pass) | Low per table, using the precise-grep + row-recency method established in this audit | Low — read-only investigation, no action yet | Small individually, unquantified in aggregate | Low until each is individually checked |
| Time-boxed partial archival of `transfer_index` + funder-transfer family | Potentially the largest long-term reclaim opportunity in the whole audit | High — the most structurally complex candidate (cross-table joinability, confirmed-attribution-only scoping) | Medium-High — risks breaking re-evaluation of reopened investigation clusters if scoped incorrectly | 544MB+ base, growing | Medium — well-reasoned but the largest remaining unknown in this whole plan |
| Quarterly automated storage audit (Phase 5 design) | Low immediate benefit, meaningful long-term process improvement (turns this manual X64.8/X64.9 exercise into a repeatable, diffable job) | Medium — needs to generalize the ad hoc queries used in this audit into a maintained script | Low — read-only | None directly | High — the design is straightforward, mainly an engineering-time question |

## Explicitly deprioritized / not recommended at this time

| Item | Reason |
|---|---|
| Any action on `wt_active_subprov_sessions` EXPIRED rows | BLOCKED per Phase 1/2 — requires new engineering work (summary-table migration) before a safe design even exists; do not schedule until that prerequisite work is separately planned and completed |
| Any cleanup action on `rpc_response_cache` | Already healthy — no work needed, would be pure wasted effort |
| Any cleanup or archive action on `sol_transfers` | Live production dependency of a currently-dormant-not-dead subsystem — any action here is really a decision about the webhook pipeline's future, not a storage decision, and is out of scope for this audit |
| `VACUUM` on the hot DB | Real benefit (physically reclaiming the ~2.86GB+ freed by the `funder_networks` drop) but the highest-risk single action in this entire plan (full exclusive lock for potentially hours) — explicitly deferred to its own separately-authorized maintenance window, never bundled with routine work |
