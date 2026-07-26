# x63 — Walkback Queue Pipeline: Master Audit

Read-only, code-grounded audit of the walkback pipeline as it exists today.
No code was modified. Companion documents: `queue_flow.md`,
`queue_schema.md`, `worker_lifecycle.md`, `entry_points.md`,
`performance.md`, `integration_recommendations.md`.

## 1. Full execution flow
See `queue_flow.md` for the literal call-chain diagram. Summary: a migration
event reaches `src/core/watchtower_attribution.py:store_migration()`, which
calls `src/core/walkback_queue.py:enqueue_migration()`
(`watchtower_attribution.py:146-147`). That function runs a zero-RPC,
DB-only classification (`classify_creator`, `walkback_queue.py:187-302`) and
`INSERT OR IGNORE`s a row into `wt_walkback_queue`
(`walkback_queue.py:368-380`), immediately marking it `complete`/`skipped`
if lineage is already fully known, or `pending`/`waiting` if RPC-based
reconstruction is needed. It also invokes
`src/ops/watchtower_candidates.py:evaluate_and_enqueue_candidate()`, which
may raise the row's `priority` to 100 if the X63
`EPHEMERAL_WSOL_CREATOR_HANDOFF` primitive is detected
(`watchtower_candidates.py:170-174`). The standalone worker daemon
(`src/core/walkback_worker.py:run_loop()`) polls every 45s, claims up to 8
pending/expired-lease rows via an atomic CAS UPDATE
(`deep_walkback.py:231-240`), and processes each through
`_process_row()` (`walkback_worker.py:859-1054`), which performs 1-2 hop
(occasionally deeper) on-chain funder lookups via Helius RPC and writes the
final `intelligence_outcome`/`status` back to `wt_walkback_queue`, fanning
out to `wt_attribution_outcomes`, `wt_provisioning_edges`/`sessions`,
`watchtower_token_attribution`, `wt_discovered_subprovs`, and
`wt_treasury_review` depending on the outcome.

## 2. Every entry point
Four call sites found (see `entry_points.md` for full detail): (1)
`watchtower_attribution.py:146-147` inside `store_migration()` — the primary
live trigger, on every migration event; (2)
`pumpfun_curve_listener.py:816` — a creator-unknown fallback during
migration processing, routed through `database_write_service`; (3)
`scripts/x54_shadow_validation.py:66` — a manual script that inserts
directly, bypassing `enqueue_migration()` and its classification entirely;
(4) `enqueue_migration()` itself is the only function in the codebase that
performs the actual `INSERT OR IGNORE`. `funding_boundary_backfill.py` and
`detection_reconciliation.py` were both verified to be **read-only** against
`wt_walkback_queue` — neither enqueues anything.

## 3. Exact queue table schema
See `queue_schema.md`. The live schema has 34 columns, materially more than
the "known" list supplied in the task (13 additional columns not
mentioned: `claimed_by`, `claimed_at`, `lease_expires_at`, `next_retry_at`,
`path_state`, `termination_reason_json`, five `create_anchor_*` columns,
`priority`, `priority_reason`). These are added by three separate
`ensure_schema()`/`ALTER TABLE` blocks across `walkback_queue.py`,
`deep_walkback.py`, and `watchtower_candidates.py` — the schema is
assembled by three modules, not one.

## 4. Scheduling/selection logic
`drain_batch()`'s SELECT (`walkback_worker.py:1160-1167`):
```sql
WHERE (status='pending' OR (status='running' AND COALESCE(lease_expires_at,0) < now))
  AND attempts < MAX_ATTEMPTS AND COALESCE(next_retry_at,0) <= now
ORDER BY COALESCE(priority,0) DESC, enqueued_at ASC
LIMIT BATCH_SIZE
```
This **is** ordered — by `priority DESC` first, then `enqueued_at ASC`
(oldest-first) as tiebreak — not pure FIFO and not unordered.
Locking/claiming is a lease-based CAS UPDATE with `rowcount==1` semantics
(`deep_walkback.py:231-240`), safe under concurrent worker processes;
concurrent claim losers are simply skipped in the same batch
(`walkback_worker.py:1184-1186`).

## 5. Worker lifecycle
Full detail in `worker_lifecycle.md`. Polling: 45s fixed interval,
regardless of queue depth. Claim: atomic lease UPDATE, 300s default lease.
Processing: 1-2 (occasionally up to 8, via deep expansion) RPC hops per row,
bounded by `SIG_PAGE_COUNT=3` pages × `TX_FETCH_LIMIT=5` transactions per
hop. Completion: `_mark_complete` writes `status='complete'` plus
`intelligence_outcome` and fans out to several tables. Failure: caught
exceptions either reset to `pending` for retry or, if `attempts >=
MAX_ATTEMPTS=3`, terminally `_mark_exhausted` (`status='failed'`,
`NO_ATTRIBUTION_FOUND`). No active backoff timer was found in the files
read (`next_retry_at` exists in schema but no write to a future value was
observed). Dead-letter state is `status='failed'`, permanently excluded from
`drain_batch`'s SELECT once `attempts>=MAX_ATTEMPTS`. Duplicate-prevention
is `INSERT OR IGNORE` on the `mint` primary key at enqueue time.

## 6. What triggers a launch to be queued
Purely migration events in the two live-pipeline entry points (#1 and #2
above), plus one manual/test script (#3) that bypasses the classification
logic entirely. No dedicated `scripts/*walkback*backfill*` enqueue path was
found; `funding_boundary_backfill.py` backfills a *different* table
(`wt_funding_boundary`) from *existing* `wt_walkback_queue` data — it does
not create new queue rows.

## 7. What a successful walkback produces
Confirmed writes (full list and citations in `queue_flow.md` §3):
`wt_walkback_queue` itself, `watchtower_token_attribution` (conditionally),
`wt_discovered_subprovs` (LINEAGE_GAP leads only),
`wt_treasury_review` (hop2 leads), `wt_attribution_outcomes` (via
`materialize_outcome`, always), `wt_watchtower_candidates` (via
`sync_walkback_result`, if a candidate row exists),
`wt_provisioning_sessions`/`wt_provisioning_edges` (append-only evidence,
`FULL_WALKBACK` only), and `wt_walkback_atomic_flows`/
`wt_walkback_edge_candidates` (per-candidate RPC evidence, inside
`_find_funder_via_rpc`). **`wt_confirmed_treasuries` is never written by the
walkback pipeline** — it is read-only input (`_is_known_treasury`);
promotion into it happens via a separate module, `src/core/treasury_bank.py`.

## 8. Existing prioritization
**Yes, prioritization exists**, contrary to what a naive read of
`walkback_queue.py` alone would suggest (its own `ensure_schema()` doesn't
mention `priority` — that column is added by
`src/ops/watchtower_candidates.py:52-63`, a separate module). The
`priority INTEGER` column feeds directly into `drain_batch`'s
`ORDER BY COALESCE(priority,0) DESC, enqueued_at ASC`, and it is actively
set — to a flat value of 100 — by the existing
`EPHEMERAL_WSOL_CREATOR_HANDOFF` candidate detector in
`watchtower_candidates.py:evaluate_and_enqueue_candidate()`. **However**, a
live query against `database/wt_ops_v2.db` on 2026-07-21 shows all 6753
current rows have `priority=0`, and `wt_watchtower_candidates` — the table
`evaluate_and_enqueue_candidate` inserts into before ever reaching the
priority UPDATE — has **zero rows**, despite 3,052,976 rows of qualifying
raw evidence in `wt_candidate_websocket_watches`. Root cause (see
`performance.md`): the function gates its own INSERT on
`classify_quick_birth_migration()` requiring a non-NULL `migrated_at`, but
it is invoked at/near CREATE time — before any token has migrated or
could be known to — so the gate fails almost every real call and the
function returns before writing anything. The mechanism is wired
end-to-end but cannot fire from its current call site as written; this is
a structural timing-order bug, not an unfired-but-healthy detector. There
is no multi-tier priority scheme, no separate named queues, and no other
WHERE/ORDER BY variation beyond this single priority/enqueue-time sort.
