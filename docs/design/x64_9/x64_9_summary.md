# X64.9 — Executive Summary: Lifecycle Validation & Maintenance Framework

Read-only validation, 2026-07-21, performed immediately following X64.8
(Database Lifecycle & Retention Audit). No production data was
modified. This validation pass found **two significant corrections**
to X64.8's conclusions and **materially narrowed** two others — the
value of this phase was less "confirming X64.8" and more "catching
what X64.8 got wrong before anything was deleted."

## Which cleanup candidates are fully validated?

| Candidate | Status |
|---|---|
| `funder_networks` (hot-DB copy) | **Fully validated, SAFE** — unchanged from X64.8, confirmed a second time |
| `wt_subprov_sig_retry` DONE/FAILED rows | **Fully validated, SAFE (scoped)** — X64.8's suspicion confirmed with direct code-level proof that the only reader excludes these rows |
| `cross_network_senders`, `oneoff_hub5e1_outbound` | **Fully validated, SAFE** — individually confirmed zero code references |

## Which still require investigation?

| Candidate | Status |
|---|---|
| `wt_candidate_websocket_watches` EXPIRED rows | **INVESTIGATE** — a hidden existence-check dependency was found (`ws_cascade_store.py:771-774`); safe purge requires a scoped mitigation (retain-latest-per-subprov), not a blanket delete |
| `coordinated_creator_edges` staleness | **INVESTIGATE, reclassified** — not primarily a storage issue; it's a symptom of a separate, more urgent stuck `creator_funding_ready` queue (23+ days backlogged and climbing) |
| Remainder of X64.8's "9 zero-row + 6 stale" table set | **INVESTIGATE** — only 2 of the ~10 total candidates in this batch were individually re-verified; given that 2 of those 10 (`rpc_response_cache`, `sol_transfers`) turned out to be wrong, the unverified remainder should not be assumed safe |

## What was corrected (X64.8 was wrong)

| Candidate | Correction |
|---|---|
| `rpc_response_cache` | X64.8 flagged this as "worth checking" for a possible eviction gap. **It already has full, working TTL-based eviction** (`rpc_cache.py`, three delete paths, one already inline in `pumpfun_curve_listener.py`). Remove from the cleanup list entirely. |
| `sol_transfers` | X64.8 claimed "zero code references, superseded by `transfer_index`." **This was a false positive from an imprecise grep** that conflated the table name with the similarly-named `creator_sol_transfers` table and an `extract_sol_transfers()` method. The real table has 50+ live references across 7 production modules. Its staleness reflects this project's already-known dormant (not dead) webhook pipeline, not dead code. Remove from the cleanup list entirely. |
| `wt_active_subprov_sessions` EXPIRED rows | X64.8 flagged this as a Low-Medium confidence pruning candidate. **It is actually BLOCKED** — these rows are actively read AND mutated by a live "operational-spend-proxy" classifier (Hello-program-linkage detection) running in `ws_cascade_store.py`. A purge here would silently disable a real detection feature. |

## Estimated immediate storage recovery

**~2.86GB** — `funder_networks` hot-DB copy removal (`DROP TABLE`),
unchanged from X64.8. Note: physical disk-file shrinkage requires a
subsequent `VACUUM`, deferred to its own maintenance-window-gated phase
(X64.9J) given it's the single highest-risk, non-interruptible
operation in this entire framework.

## Estimated long-term storage recovery

- **Near-term (scoped, code-verified-safe purges)**: ~580-680MB
  (`wt_subprov_sig_retry` DONE/FAILED rows fully; `wt_candidate_websocket_watches`
  EXPIRED rows partially, pending the existence-check mitigation)
- **Longer-term (6-12 month rolling archival, contingent on new code-repointing work)**:
  ~1.0GB+ and growing (`prediction_decision_context`, `token_prediction_events`,
  `wss_metrics`, small reporting tables)
- **Total realistic near-term**: ~3.5GB
- **Total including long-term archival**: ~4.5GB+

This is a meaningfully smaller and more conservative figure than a
naive reading of X64.8 alone would suggest, specifically because two of
its candidates are now excluded entirely and one is scoped down — this
is the expected, correct outcome of a validation pass, not a failure of
it.

## Recommended maintenance jobs

Daily: purge terminal `wt_subprov_sig_retry` rows; purge (mitigated)
`wt_candidate_websocket_watches` EXPIRED rows. Weekly: `rpc_response_cache`
health-check (observability only, no new eviction logic needed). Monthly:
archive aged `prediction_decision_context`/`token_prediction_events`.
Quarterly: an automated successor to this manual X64.8/X64.9 audit
process itself. Full designs in
[x64_9_retention_jobs.md](x64_9_retention_jobs.md).

## Recommended archive strategy

Unchanged from X64.8: archive `prediction_decision_context` +
`token_prediction_events` (time-boxed), the small reporting trio
(`trade_simulations`/`wt_attribution_outcomes`/`wt_subprov_evidence`),
and eventually a confirmed-attribution-only subset of `transfer_index` +
funder-transfer tables (the largest remaining opportunity, but the
highest structural complexity — recommend scoping as its own dedicated
follow-on task, not bundled into this roadmap).

## Recommended backup strategy

Unchanged from X64.8: Strategy B (operational + historical split),
sequenced after the cleanup phases below shrink the operational
footprint (X64.9I in the execution plan).

## Highest-priority implementation

**X64.9A — remove the `funder_networks` hot-DB copy.** Single largest,
lowest-risk, highest-confidence action in the entire framework; ready
to execute today with no further validation needed.

## Lowest-risk implementation

Also **X64.9A** — zero dependencies confirmed twice across two
independent audits, using an already-proven archive-copy-as-safety-net
pattern from this project's own prior work (X64.7C's backup deletion
precedent).

## Proposed X64.9A scope

Exactly and only: `DROP TABLE funder_networks` against
`database/flex_complete_database.db`, preceded by the Phase 6 safety
contract's pre-verification (archive row count ≥ 41,734, archive
`quick_check` = ok, no code references remain), followed by immediate
post-drop verification (table gone, archive unaffected, both production
processes still healthy). **`VACUUM` is explicitly excluded from
X64.9A's scope** — it is its own separately-gated phase (X64.9J),
requiring a dedicated maintenance window given it's the only
non-interruptible, fully-exclusive-lock action in this entire
framework.

## Provenance note

This summary and the seven other X64.9 documents it references were
produced entirely inline (no background-agent delegation), following
the same disciplined read-only verification approach established across
X64.7B/X64.7C/X64.8: verify before concluding, prefer direct code
inspection over pattern-matching assumptions, and treat every
"probably safe" claim as requiring positive evidence before acting on
it. Nothing in this document authorizes execution — every phase in
[x64_9_execution_plan.md](x64_9_execution_plan.md) still requires its
own separate, explicit approval before any production data is touched.
