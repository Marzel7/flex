# X64.9 — Phase 1: Revalidation of X64.8 Cleanup Candidates

Read-only revalidation, 2026-07-21 (same day as X64.8, run immediately
after). No data, schema, or files were modified.

This revalidation found **two significant corrections** to X64.8's
conclusions (`rpc_response_cache` and `sol_transfers` were both
misclassified) and **narrowed the confidence** on the two ops-DB queue
tables (hidden EXPIRED-row readers found — see
[x64_9_dependencies.md](x64_9_dependencies.md) for full detail). All
other X64.8 findings held up under re-verification.

## `funder_networks` (hot-DB copy)

| Check | X64.8 finding | Revalidated |
|---|---|---|
| Current size | 2.86GB | Unchanged (not re-measured via dbstat this pass, no reason to expect drift on a frozen table) |
| Current row count | 41,734 | **Unchanged: 41,734** |
| Last write | 2026-03-08 20:55:31 | **Unchanged — still frozen** |
| Archive copy row count | 42,314 (as of X64.8) | **Now 42,314, through 2026-06-22 11:07:32** (archive continues to grow independently) |
| Current writers | None | **Confirmed: none** |
| Current readers | None | **Confirmed: none** |
| Production dependency | None | **Confirmed: none** |
| Historical value | Superseded by archive | **Confirmed — archive copy is current superset** |
| Rebuildability | N/A (archive already exists) | N/A |

**Verdict: fully validated, no change from X64.8. Highest-confidence
cleanup candidate stands.**

## `wt_subprov_sig_retry`

| Check | X64.8 finding | Revalidated |
|---|---|---|
| Current row count | 2.31M (99.99% DONE) | **Re-confirmed exactly: 2,310,617 DONE / 5 FAILED / 57 PENDING / 3 RUNNING = 2,310,682 total** |
| Current writers | `ws_cascade_store.py`, `ws_cascade.py` | Confirmed |
| Current readers | Same modules | **Confirmed and narrowed**: the only production reader (`due_subprov_sig_retries()` in `ws_cascade_store.py:1972-1990`) explicitly filters `WHERE r.status='PENDING' OR (r.status='RUNNING' AND ...)` — DONE and FAILED rows are provably never selected by any query found |
| Production dependency | Active retry path | Confirmed active for PENDING/RUNNING rows only |
| Historical value | None claimed | None found |
| Rebuildability | N/A | N/A — these are retry-completion markers, not derivable data |

**Verdict: validated and strengthened. DONE rows (99.997% of the table)
are confirmed dead weight with code-level proof of non-access — a
status-scoped purge (`WHERE status='DONE'`) is safe.**

## `wt_candidate_websocket_watches`

| Check | X64.8 finding | Revalidated |
|---|---|---|
| Current state distribution | >99.3% EXPIRED | **Re-confirmed and refined**: `AUDIT_ONLY`=2, `BUY_SWARM`=19,981, `EXPIRED`=3,031,736, `EXPIRED_SIBLING`=1,218, `FIRED_CREATE`=39 (total ~3.05M) |
| Current writers | `ws_cascade.py`, `ws_cascade_store.py` | Confirmed |
| Current readers | None claimed for EXPIRED rows | **CORRECTION — hidden dependency found**: `ws_cascade_store.py:771-774` runs an *unfiltered* `SELECT 1 FROM wt_candidate_websocket_watches WHERE subprov_wallet=? LIMIT 1` inside a "has this wallet been seen as a subprov before" existence check — this incidentally matches EXPIRED rows too, treating any historical watch (not just live ones) as evidence |
| Production dependency | None (assumed) | **Now: partial — the existence-check semantics change if EXPIRED rows are deleted** |
| Historical value | Low | Low, **but the existence check gives EXPIRED rows a real (if narrow) operational role today** |
| Rebuildability | N/A | N/A |

**Verdict: NOT fully validated — see
[x64_9_dependencies.md](x64_9_dependencies.md) for the full
dependency trace. X64.8's "safe to purge" conclusion needs revision: a
blanket `WHERE state='EXPIRED'` purge would silently change the
behavior of the subprov-history existence check. A scoped purge
(retain rows referenced by the existence-check pattern, or refactor the
check first) is required, not a direct row purge.**

## `wt_active_subprov_sessions`

| Check | X64.8 finding | Revalidated |
|---|---|---|
| Current state distribution | Suggestive of stale-session accumulation, not confirmed | **Confirmed and refined**: `ACTIVE`=104, `BUY_SWARM_REJECTED`=18, `COMPLETED`=4, `EXPIRED`=159,650 |
| Current writers | `ws_cascade.py` | Confirmed |
| Current readers | Not confirmed in X64.8 | **CORRECTION — significant hidden dependency found**: `ws_cascade_store.py` (three separate functions, ~lines 1647, 1703, 1748) actively query `WHERE s.state = 'EXPIRED'` as part of an "operational-spend-proxy" classifier that re-examines EXPIRED sessions for Hello-proxy-linkage evidence (matches this project's own persistent "Hello program operator linkage" pattern) — plus `ws_cascade_store.py:775-778`'s existence check also explicitly includes `state IN ('EXPIRED','COMPLETED')` |
| Production dependency | Low (assumed) | **HIGH — EXPIRED rows are load-bearing for an active classification feature, not dead data** |
| Historical value | Low | **Actually high — these rows are the input to ongoing post-hoc classification, not just history** |
| Rebuildability | N/A | N/A — the classification logic depends on exactly this stored state, not something re-derivable from elsewhere without re-running the whole session-detection pipeline |

**Verdict: X64.8's classification was too permissive here. This table's
EXPIRED rows are NOT a pruning candidate at all in their current form —
see dependency doc. This is the most consequential correction in this
revalidation.**

## `rpc_response_cache`

| Check | X64.8 finding | Revalidated |
|---|---|---|
| Eviction/TTL logic | "No confirmed active eviction logic found" — flagged Low confidence | **CORRECTION — X64.8 was wrong.** `src/core/rpc_cache.py` has full TTL-based eviction: line 135 deletes an individual expired key on read-miss, line 219 on explicit invalidation, and **line 248 runs a bulk `DELETE FROM rpc_response_cache WHERE cached_at + ttl_seconds <= ?`**. `pumpfun_curve_listener.py:4069` also runs the same bulk-expiry delete. `main.py:25405-25409` even reports an "expired but not yet purged" count as a health metric. |
| Current writers/readers | Yes/Yes | Confirmed, and eviction is a third confirmed write path |
| Production dependency | Perf-only | Confirmed — but now correctly a *self-maintaining* cache, not an unbounded-growth risk |

**Verdict: this table should be REMOVED from the cleanup-candidate list
entirely. It already has working, multi-path TTL eviction. X64.8's
"worth checking" framing was itself the thing that needed checking —
now checked and resolved: no action needed here.**

## `coordinated_creator_edges`

| Check | X64.8 finding | Revalidated |
|---|---|---|
| Uniform `created_at` timestamp | All 328,702 rows share one timestamp (2026-06-06), suggesting a stalled rebuild job | Confirmed unchanged |
| Is the rebuild job still scheduled? | X64.8 did not check | **Checked: yes, still scheduled** — `crontab -l` shows `scripts/run_graph_analyzers.py` every 4 hours |
| Why hasn't it run since June 6? | Not investigated in X64.8 | **Root cause found**: `logs/graph_analyzers.log` shows the job *executes* on schedule but is immediately short-circuited every single run by a `[QUEUE_GUARD]` message: `"Skipping graph analyzers: hot-path work is ready"`. As of this revalidation, `creator_funding_ready=9390` and climbing, with `oldest_ready_age=2,006,384s` (~23 days) — **the creator-funding queue itself appears stuck/backlogged**, and the graph-analyzer guard is correctly deferring to it, but the underlying queue backlog is never clearing. |
| Production dependency | Read continuously (coordination views) | Confirmed |

**Verdict: this is NOT primarily a lifecycle/storage finding — it's a
symptom of a separate, likely more urgent operational issue (a stuck
`creator_funding_ready` queue blocking the graph-analyzer's queue guard
for at least 6+ weeks). Flagged here for visibility but recommend
treating as its own investigation, outside this audit's storage/
retention scope.**

## `sol_transfers`

| Check | X64.8 finding | Revalidated |
|---|---|---|
| Code references | "Zero code references, superseded by `transfer_index`" | **CORRECTION — X64.8 was wrong.** A precise `grep -rn "\bsol_transfers\b"` (excluding the similarly-named `creator_sol_transfers` table and the `extract_sol_transfers()` method, which the original loose grep conflated) finds **50+ live references** across `webhook_handler.py` (writer), `webhook_worker.py`, `main.py`, `rpc_metrics_api.py`, `webhook_creator_ranker.py`, `webhook_api_enriched.py`, `helius_webhook_sync.py` (all readers) |
| Row count / staleness | 40,581 rows, stale since 2026-03-09 | Confirmed row count and staleness date accurate |
| Why is it stale if the code is live? | Not investigated in X64.8 | Consistent with this project's own persistent memory ("Outbound worker disabled" — the webhook/COW pipeline has been dead since 2026-05-05); the table's staleness reflects an **upstream pipeline being idle**, not the table itself being dead code |

**Verdict: this table should be REMOVED from the cleanup-candidate list
entirely. It is extensively read by production code (webhook-derived
creator/activity views); its staleness is a symptom of the already-known
"Outbound worker disabled" issue, not evidence the table itself is
obsolete. Deleting or archiving it would silently break every one of
the ~7 modules that read it, the moment the webhook pipeline is
re-enabled.**

## Zero-reference tables (`cross_network_senders`, `oneoff_hub5e1_outbound`, and the broader "9+6" set from X64.8)

| Check | X64.8 finding | Revalidated |
|---|---|---|
| `cross_network_senders` | Zero code references, stale since 2026-02-16 | **Confirmed: zero code references** (precise grep); 85 rows present (non-zero, contradicting X64.8's "zero rows" framing for *this* table — it was likely grouped into the "6 stale non-zero" bucket, not the "9 zero-row" bucket; the two buckets were not disambiguated by name in X64.8) |
| `oneoff_hub5e1_outbound` | Zero code references, stale since 2026-05-28 | **Confirmed: zero code references**; 50 rows present |
| Remaining ~13 tables in the "9 zero-row + 6 stale" set | Not individually named in X64.8 | **Not individually re-verified in this pass** — recommend a follow-up pass naming and confirming each of the remaining tables individually before any batch action, given the false-positive rate already found in this same candidate set (`rpc_response_cache`, `sol_transfers`) |

**Verdict: the two individually-named tables validate as genuinely
zero-reference. However, given that 2 of the ~10 total candidates
re-examined in this revalidation turned out to be **wrong** in the
prior pass, the un-named remainder of the "9+6" set should not be acted
on without the same individual verification applied here — see
[x64_9_dependencies.md](x64_9_dependencies.md) Phase 2 for the
required follow-up scope.**

## Summary of corrections

| Candidate | X64.8 verdict | X64.9 revalidated verdict |
|---|---|---|
| `funder_networks` | High confidence, safe to remove | **Unchanged — confirmed** |
| `wt_subprov_sig_retry` DONE rows | High confidence, safe to purge | **Confirmed, strengthened with exact reader-code proof** |
| `wt_candidate_websocket_watches` EXPIRED rows | High confidence, safe to purge | **DOWNGRADED — hidden existence-check dependency found, not safe as a blanket purge** |
| `wt_active_subprov_sessions` EXPIRED rows | Low-Medium confidence, pruning candidate | **DOWNGRADED SHARPLY — actively read by a live classification feature, NOT a cleanup candidate in current form** |
| `rpc_response_cache` | Low confidence, "worth checking" | **REMOVED from candidate list — already has working TTL eviction, X64.8 was wrong** |
| `coordinated_creator_edges` | Medium confidence, possibly-stalled job | **Reclassified as a separate operational issue** (stuck creator-funding queue), not primarily a storage/lifecycle finding |
| `sol_transfers` | Medium confidence, legacy/superseded | **REMOVED from candidate list — extensively read by live code, X64.8 was wrong** |
| Zero-reference small tables | Medium confidence, batch candidate | **Partially confirmed** (2 of 2 individually-named tables hold up); remainder unverified, do not batch-act without individual checks |
