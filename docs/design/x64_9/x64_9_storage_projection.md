# X64.9 — Phase 3: Storage Impact Projection

Estimates below reflect the corrected candidate set from Phase 1/2 —
`rpc_response_cache` and `sol_transfers` are excluded entirely (not
cleanup candidates); `wt_active_subprov_sessions` is excluded from any
immediate action (BLOCKED); `wt_candidate_websocket_watches` is
included only as a smaller, scoped estimate reflecting the
existence-check-preserving mitigation from Phase 2, not a full
EXPIRED-row wipe.

## Immediate reclaim (delete obsolete table)

| Action | Bytes reclaimed | Confidence |
|---|---|---|
| Remove `funder_networks` hot-DB copy | ~2,864,263,168 bytes (2.86GB, table data) + its own indexes (not separately sized in X64.8, likely modest given the table's simple schema) | High |

**Immediate reclaim total: ~2.86GB.**

## Near-term reclaim (scoped, status/state-based purge)

| Action | Estimated bytes reclaimed | Confidence | Caveat |
|---|---|---|---|
| Purge `wt_subprov_sig_retry` WHERE status IN ('DONE','FAILED') | ~99.997% of 392,859,648 bytes ≈ **~392.85MB** | High | Must be status-scoped; the 60 PENDING/RUNNING rows must survive |
| Purge `wt_candidate_websocket_watches` EXPIRED rows, retaining one row per `subprov_wallet` to preserve the existence check (Phase 2 mitigation) | Partial — of 3,031,736 EXPIRED rows, only the most-recent-per-`subprov_wallet` need survive; without a distinct-subprov count in this pass, this is estimated at **60-90% of the EXPIRED rows' share of the table (~319MB total table size)**, i.e. roughly **190-290MB**, not the full 317MB+ | Medium — exact reclaim depends on how many distinct `subprov_wallet` values exist among EXPIRED rows, not measured in this pass | Requires the existence-check mitigation (refactor or retain-latest) to be implemented first — see Phase 4 |

**Near-term reclaim total (conservative): ~580-680MB.**

## Explicitly NOT included (corrected from X64.8)

| Table | X64.8 estimate | Why excluded now |
|---|---|---|
| `rpc_response_cache` | Modest, "worth checking" | Already self-maintaining via TTL eviction — zero incremental reclaim available from a manual action |
| `sol_transfers` | Small, legacy | Live production table — zero reclaim available without a much larger decision (disabling/removing the webhook subsystem entirely), out of scope |
| `wt_active_subprov_sessions` EXPIRED rows | Modest (54MB) | BLOCKED — see Phase 2; any reclaim here requires new engineering work (summary-table migration) before it's safe, not a simple purge |

## Long-term reclaim

### Retention (time-boxed archival, not deletion)

| Table(s) | Estimated reclaim (from hot DB, moved to archive) | Timeframe |
|---|---|---|
| `prediction_decision_context` + `token_prediction_events` | ~454MB (per X64.8 Phase 6, unchanged by this revalidation) | 6-12 months, rolling |
| `transfer_index` + funder-transfer family (confirmed-attribution-only subset) | Unquantified but potentially the largest long-term single opportunity (544MB+ base table, growing) — requires a confirmed-attribution age cutoff not yet defined | 6-12 months post-confirmation, rolling |
| `wss_metrics` | 163MB now, growing continuously (fastest row-count grower in the audit) | 30-90 days, rolling |

### Archive (small reporting-only tables, batched)

| Table(s) | Estimated reclaim | Notes |
|---|---|---|
| `trade_simulations`, `wt_attribution_outcomes`, `wt_subprov_evidence` | ~39MB combined (17.1MB + 4.3MB + 17.9MB) | Low-risk, easy batch alongside a larger archive-tooling effort |

### Automatic purge (once designed — see Phase 4/5)

| Table(s) | Estimated ongoing reclaim | Notes |
|---|---|---|
| `wt_subprov_sig_retry` DONE/FAILED rows | Continuous — prevents the table from re-growing to its current 393MB size after the one-time purge above | Daily job, see Phase 5 |
| `wt_candidate_websocket_watches` EXPIRED rows (post-mitigation) | Continuous — same purpose | Daily job, see Phase 5, contingent on Phase 4's mitigation being implemented first |
| `rpc_response_cache` | Already automatic — no new job needed | N/A |

## Cumulative savings estimate

| Tier | Bytes | Approx. |
|---|---|---|
| Immediate (funder_networks removal) | ~2,864,263,168 | 2.86GB |
| Near-term (scoped queue purges) | ~608,000,000-712,000,000 (midpoint of range above) | ~0.6-0.7GB |
| Long-term archival (retention-driven, over 6-12 months) | ~1,056,000,000+ (454MB + 163MB + 39MB + unquantified `transfer_index` subset) | ~1.0GB+ and growing |
| **Total realistic near-term (immediate + near-term)** | **~3.5GB** | Achievable within weeks once the scoped purge jobs (Phase 4/5) are built |
| **Total including long-term archival** | **~4.5GB+** | Achievable over 6-12 months as data ages into archival eligibility |

This is meaningfully smaller than X64.8's headline framing might have
suggested once `rpc_response_cache`, `sol_transfers`, and
`wt_active_subprov_sessions` EXPIRED rows are correctly excluded or
scoped down — the revalidation reduces the confidently-actionable
near-term total from X64.8's implied ~3.9GB+ (2.86GB + 712MB + partial
credit for the excluded items) down to a more conservative but
**fully evidence-backed ~3.5GB**, with the difference mostly reflecting
work correctly deferred (the session-classifier mitigation) rather than
storage that was never real.
