# X63 — Walkback Queue Audit (Combined)

Read-only, code-grounded audit of the walkback pipeline as it exists today.
No code was modified. This document merges the seven original deliverables
into one file, in this order: Master Audit, Execution Flow, Entry Points,
Schema, Worker Lifecycle, Performance, Integration Recommendations.

---

# 1. Master Audit

## 1. Full execution flow
See §2 (Execution Flow) for the literal call-chain diagram. Summary: a migration
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
Four call sites found (see §3 Entry Points for full detail): (1)
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
See §4 (Schema). The live schema has 34 columns, materially more than
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
Full detail in §5 (Worker Lifecycle). Polling: 45s fixed interval,
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
Confirmed writes (full list and citations in §2 Execution Flow §3):
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
raw evidence in `wt_candidate_websocket_watches`. Root cause (see §6
Performance): the function gates its own INSERT on
`classify_quick_birth_migration()` requiring a non-NULL `migrated_at`, but
it is invoked at/near CREATE time — before any token has migrated or
could be known to — so the gate fails almost every real call and the
function returns before writing anything. The mechanism is wired
end-to-end but cannot fire from its current call site as written; this is
a structural timing-order bug, not an unfired-but-healthy detector. There
is no multi-tier priority scheme, no separate named queues, and no other
WHERE/ORDER BY variation beyond this single priority/enqueue-time sort.

---

# 2. Execution Flow

All citations are `file:line` against the current repo state.

## 1. Literal call chain (migration → final storage)

```
pump.fun migration event detected
        │
        ▼
src/core/watchtower_attribution.py:store_migration()  (watchtower_attribution.py:122-147)
  writes migrated_tokens (neutral fact)
        │
        ▼
src/core/watchtower_attribution.py:146-147
  enqueue_migration(conn, mint=mint, creator=creator)
        │
        ▼
src/core/walkback_queue.py:enqueue_migration()  (walkback_queue.py:307-392)
        │
        ├─► classify_creator()  (walkback_queue.py:187-302)  — zero-RPC DB-only lookup
        │      cascades through: wt_ops_v2_wallets → wt_watchtower_launches →
        │      wt_wrap_close_candidates → wt_creator_birth_launch →
        │      wt_candidate_websocket_watches → watchtower_token_attribution →
        │      established-launcher check → CEX/relay check → FULL_WALKBACK fallback
        │
        ├─► INSERT OR IGNORE INTO wt_walkback_queue (walkback_queue.py:368-380)
        │      status/initial_outcome computed from walkback_class:
        │        LINK_ONLY* / OP_GRAPH_ROLE_MISMATCH / SELF_ROOTED_OPERATION → status='complete'
        │        SKIP                                                        → status='skipped'
        │        PARTIAL_* / FULL_WALKBACK (create-anchor valid)             → status='pending'
        │        FULL_WALKBACK (no valid create anchor)                     → status='waiting'
        │
        ├─► evaluate_and_enqueue_candidate()  (watchtower_candidates.py:118-189)
        │      X63 EPHEMERAL_WSOL_CREATOR_HANDOFF candidate detector — if it fires,
        │      sets wt_walkback_queue.priority=100 for this mint while status
        │      is pending/waiting (watchtower_candidates.py:170-174)
        │
        └─► if status is complete/skipped at enqueue time:
               materialize_outcome() (attribution_outcome.py) + sync_walkback_result()
               — row is DONE, worker never sees it
        │
        ▼  (only PARTIAL_* / FULL_WALKBACK rows reach here — the RPC-consuming path)
src/core/walkback_worker.py:run_loop()  (walkback_worker.py:1241-1328)
  infinite loop, INTERVAL_SEC=45s between iterations (walkback_worker.py:64,1328)
        │
        ├─► heartbeat write (walkback_worker.py:1298-1303)
        ├─► COUNT(*) pending rows with attempts < MAX_ATTEMPTS (walkback_worker.py:1306-1309)
        └─► if pending > 0: drain_batch(ops)  (walkback_worker.py:1153-1218)
                │
                ├─► SELECT up to BATCH_SIZE=8 candidate rows
                │     ORDER BY COALESCE(priority,0) DESC, enqueued_at ASC
                │     (walkback_worker.py:1160-1167)
                │
                ├─► for each row: _mark_running(ops, mint)  (walkback_worker.py:498-507)
                │     → deep_walkback.claim_with_lease()  (deep_walkback.py:231-240)
                │       atomic UPDATE ... WHERE status IN (pending, expired-running)
                │       — rowcount==1 required, else another worker already claimed it
                │
                ├─► _process_row(ops, row)  (walkback_worker.py:859-1054)
                │     dispatches on walkback_class:
                │       PARTIAL_TREASURY → 1-hop RPC from subprov (walkback_worker.py:874-890)
                │       PARTIAL_SUBPROV  → 1-hop RPC from creator (walkback_worker.py:892-913)
                │       FULL_WALKBACK    → hop1 (creator's funder) then hop2
                │                          (funder's funder), optionally deep-expanding
                │                          via _expand_unknown_upstream up to
                │                          DEEP_MAX_HOPS=8 (walkback_worker.py:915-1037)
                │     each hop calls _find_with_evidence → _find_funder_via_rpc, which
                │     performs the actual getSignaturesForAddress/getTransaction RPC work
                │     (walkback_worker.py:325-448)
                │
                └─► result written via one of:
                      _mark_complete()  (walkback_worker.py:573-630)
                      _mark_failed()    (walkback_worker.py:633-644)
                      _mark_exhausted() (walkback_worker.py:647-660)
                      each of these also calls materialize_outcome() and
                      sync_walkback_result()
        │
        ▼
time.sleep(INTERVAL_SEC) → loop repeats (walkback_worker.py:1328)
```

## 2. Zero-RPC "LINK_ONLY" fast path

For a row classified `LINK_ONLY` / `LINK_ONLY_GRAPH` / `OP_GRAPH_ROLE_MISMATCH` /
`SELF_ROOTED_OPERATION` / `SKIP`, the entire lineage is already known from
existing tables (`wt_ops_v2_wallets`, `wt_watchtower_launches`,
`wt_wrap_close_candidates`, etc.). `enqueue_migration` marks the row
`complete`/`skipped` at insert time (`walkback_queue.py:356-359`) and the
worker never touches it — `drain_batch`'s SELECT only matches
`status='pending'` or an expired `status='running'` lease
(`walkback_worker.py:1163`), so these rows are excluded by construction.

## 3. What "successful completion" writes

Traced writes from a completed `PARTIAL_*`/`FULL_WALKBACK` row
(`_mark_complete`, `walkback_worker.py:573-630`):
1. `wt_walkback_queue` — `status='complete'`, `intelligence_outcome`, `subprov`,
   `treasury` (COALESCE, never overwrites), `rpc_used`, `completed_at`.
2. `watchtower_token_attribution` — only if `confirmed_subprov` or `treasury`
   is set (`walkback_worker.py:590-604`), upsert on mint.
3. `wt_discovered_subprovs` — via `_ensure_subprov_lead` only for
   `LINEAGE_GAP` outcome with an unconfirmed subprov (`walkback_worker.py:608-619`).
4. `wt_treasury_review` — via `_surface_treasury_review_lead` →
   `treasury_bank.add_walkback_hop2_lead` for `LINEAGE_GAP` funder leads
   (`walkback_worker.py:621-625`) and for unknown hop-2 candidates during
   `FULL_WALKBACK` deep expansion (`walkback_worker.py:1016-1017`).
5. `wt_attribution_outcomes` (+ `wt_unknown_infrastructure_registry`) — via
   `materialize_outcome()` in `src/ops/attribution_outcome.py:603` (called at
   the end of every `_mark_*` function).
6. `wt_watchtower_candidates` — via `sync_walkback_result()` in
   `watchtower_candidates.py:238-266` (called at end of every `_mark_*`),
   mirrors final status/outcome into the candidate row only if one exists.
7. `wt_provisioning_sessions` / `wt_provisioning_edges` — via
   `_capture_provisioning_facts()` → `capture_provisioning_relationship()`
   in `src/ops/provisioning_edges.py`, called during the `FULL_WALKBACK`
   branch at hop1 confirmation (`walkback_worker.py:964-968`) and at hop2
   (`walkback_worker.py:990-997`) — append-only evidence, never attribution.
8. `wt_walkback_atomic_flows` / `wt_walkback_edge_candidates` — via
   `deep_walkback.persist_edge_candidate` / `persist_atomic_flows`, called
   inside `_find_funder_via_rpc` for every candidate considered, not just the
   winner (`walkback_worker.py:428-444`).

`wt_confirmed_treasuries` is **only read** by the worker (`_is_known_treasury`,
`walkback_worker.py:467-470`) — never written by any code path traced in
`walkback_worker.py` or `walkback_queue.py`. Promotion into
`wt_confirmed_treasuries` happens in `src/core/treasury_bank.py`, a separate
human/webhook-driven promotion path, not part of the walkback completion flow.

---

# 3. Entry Points

Search performed: `grep -rn "enqueue_migration\|INSERT INTO wt_walkback_queue\|INSERT OR IGNORE INTO wt_walkback_queue" src/ scripts/`

Four distinct call sites found, plus the single row-insert implementation.

## 1. `src/core/walkback_queue.py:307-392` — `enqueue_migration()` (the only INSERT)
This is the sole function that ever inserts a row into `wt_walkback_queue`
(`INSERT OR IGNORE`, `walkback_queue.py:368-380`). All other entry points call
into this function; none insert directly except the test/shadow script noted
in §4.

## 2. `src/core/watchtower_attribution.py:146-147` — inside `store_migration()`
- **Trigger:** every call to `store_migration()`, i.e. every observed
  pump.fun migration event stored via Layer 1 of the attribution pipeline
  (`watchtower_attribution.py:122-147`).
- **Condition:** none beyond `store_migration` itself being called; wrapped
  in a bare `try/except: pass` (`watchtower_attribution.py:145-148`), so a
  failure here is silent.
- **Payload:** `mint=mint, creator=creator` — no `force`, no `live_conn`, no
  create-signature params passed explicitly (defaults apply).

## 3. `src/core/pumpfun_curve_listener.py:816` — creator-unknown fallback
- **Trigger:** inside the migration-processing path, when `creator_wallet`
  could not be resolved from `wt_staged_wallets`/`token_analysis` at
  migration time (`pumpfun_curve_listener.py:~808-826`).
- **Condition:** gated on `if not creator_wallet:` — i.e. only fires when the
  creator lookup chain (staged wallets, `wt_creator_launches` backfill) came
  up empty.
- **Payload:** `mint=mint, creator=_creator_for_wb` where `_creator_for_wb`
  is `token_analysis.earliest_tx_creator` (possibly `None`). Routed through
  `database_write_service.submit()` rather than calling `enqueue_migration`
  directly on a raw connection — uses a serialized write-service wrapper
  (`pumpfun_curve_listener.py:817-826`) instead of a bare connection, unlike
  entry point #2.
- Note: this is a **retrospective/backfill-style enqueue** triggered by a
  live migration event where creator attribution was incomplete, not a pure
  "new migration" trigger like #2.

## 4. `scripts/x54_shadow_validation.py:66` — direct INSERT, bypasses `enqueue_migration`
- **Trigger:** manual script run, not a pipeline trigger.
- **Condition:** whatever the script's own logic gates on (not audited in
  depth here — flagged as out of scope of the production pipeline; this is a
  validation/shadow script, not a triggered entry point).
- **Payload:** `INSERT OR IGNORE INTO wt_walkback_queue(mint,creator,walkback_class,status,attempts,enqueued_at,updated_at) VALUES (?,?,?,'running',1,?,?)`
  — inserts directly as `status='running', attempts=1`, **skipping**
  `classify_creator()`'s zero-RPC classification and **skipping**
  `evaluate_and_enqueue_candidate()`. This is the only enqueue path in the
  codebase that does not go through `enqueue_migration()`.

## What does NOT enqueue
- `src/ops/funding_boundary_backfill.py` — reads `wt_walkback_queue` and
  `wt_attribution_outcomes` to populate `wt_funding_boundary`; does not
  enqueue anything (`funding_boundary_backfill.py:44-51`, LEFT JOIN read-only).
- `src/ops/detection_reconciliation.py` — fully read-only against
  `wt_walkback_queue.intelligence_outcome`; confirmed no writes anywhere in
  the file (`detection_reconciliation.py:9-13` docstring, and no
  `conn.execute("INSERT`/`UPDATE` calls present).
- `src/ops/watchtower_candidates.py:evaluate_and_enqueue_candidate()` does
  **not** insert into `wt_walkback_queue` — it only UPDATEs `priority` on an
  existing row (`watchtower_candidates.py:170-174`) and inserts into
  `wt_watchtower_candidates`, a separate table. It is always called from
  inside `enqueue_migration()` itself (both the "already exists" branch,
  `walkback_queue.py:326-333`, and the post-insert branch,
  `walkback_queue.py:382-385`), so it can never run before a
  `wt_walkback_queue` row exists for that mint.
- No script under `scripts/` with "backfill" in the name was found calling
  `enqueue_migration` (search confirmed only `x54_shadow_validation.py`
  touches the table directly, via raw INSERT, not via backfill logic).

## Duplicate-prevention mechanism
`enqueue_migration()` is idempotent on `mint` via `INSERT OR IGNORE`
(`walkback_queue.py:368`, PK is `mint`). Before that, if `force=False`
(the default) and a row already exists, the function short-circuits: it
still calls `evaluate_and_enqueue_candidate()` (so a later-arriving X63
signal can still raise priority on an existing row) but returns the
existing `walkback_class` without touching `status`/`attempts`
(`walkback_queue.py:321-333`). `force=True` deletes the existing row first
(`walkback_queue.py:365-366`) — no caller in the codebase passes
`force=True` (not found in the grep results above).

---

# 4. Schema

Source: `sqlite3 database/wt_ops_v2.db ".schema wt_walkback_queue"` run 2026-07-21,
cross-checked against `ensure_schema()` in `src/core/walkback_queue.py:78-149` and
the ALTER TABLE block in `src/ops/watchtower_candidates.py:52-59`.

The live table has more columns than `ensure_schema()`'s `CREATE TABLE` literal
because several columns were added later via `ALTER TABLE ... ADD COLUMN` from
three different modules. `CREATE TABLE IF NOT EXISTS` is a no-op on an existing
table, so the authoritative column list is the live schema, not the CREATE TABLE
literal in `walkback_queue.py`.

## Column-by-column (live schema)

| Column | Type | Origin | Written by | Read by |
|---|---|---|---|---|
| `mint` | TEXT NOT NULL, PK | `walkback_queue.py:107` | `enqueue_migration()` INSERT (`walkback_queue.py:368-380`) | every query in the pipeline |
| `creator` | TEXT | `walkback_queue.py:108` | insert; `_process_row` FULL_WALKBACK branch UPDATE when recovered from DB (`walkback_worker.py:921-923`) | `classify_creator`, `_process_row`, `attribution_outcome.py` |
| `subprov` | TEXT | `walkback_queue.py:109` | insert; `_mark_complete` `COALESCE(subprov,?)` (`walkback_worker.py:585`) | classification lookups, `_process_row` |
| `treasury` | TEXT | `walkback_queue.py:110` | insert; `_mark_complete` `COALESCE(treasury,?)` (`walkback_worker.py:585`) | dashboard, `attribution_outcome.py` |
| `walkback_class` | TEXT NOT NULL DEFAULT 'FULL_WALKBACK' | `walkback_queue.py:111` | insert only, never updated after enqueue | `drain_batch` row selection payload, `_process_row` dispatch (`walkback_worker.py:874,915,892`) |
| `attribution_source` | TEXT | `walkback_queue.py:112` | insert only (`src` from `classify_creator`) | not read elsewhere in-repo besides dashboards (not traced further) |
| `intelligence_outcome` | TEXT | `walkback_queue.py:113` (dup ALTER at `:81`) | insert (zero-RPC classes); `set_intelligence_outcome`; `_mark_complete`; `_mark_failed`/`_mark_exhausted` do NOT set this except `_mark_exhausted` sets `NO_ATTRIBUTION_FOUND` (`walkback_worker.py:652`) | `queue_stats`, `detection_reconciliation.py:206`, `funding_boundary_backfill.py` |
| `status` | TEXT NOT NULL DEFAULT 'pending' | `walkback_queue.py:114` | insert (`pending`/`complete`/`skipped`/`waiting`); `_mark_running`→`running`; `_mark_complete`→`complete`; `_mark_failed`/`_mark_exhausted`→`failed`; `recover_stalled_running_jobs`→`pending`/`failed` | `drain_batch` WHERE clause, `build_walkback_health`, `queue_stats` |
| `rpc_used` | INTEGER NOT NULL DEFAULT 0 | `walkback_queue.py:115` | insert (0); every `_mark_*` does `rpc_used=rpc_used+?` | `queue_stats.rpc_total`, `funding_boundary_backfill.py` |
| `attempts` | INTEGER NOT NULL DEFAULT 0 | `walkback_queue.py:116` | incremented inside `deep_walkback.claim_with_lease` (called from `_mark_running`, not shown in walkback_queue.py/walkback_worker.py directly — see `src/core/deep_walkback.py`) | `drain_batch` WHERE `attempts < MAX_ATTEMPTS`, `finalize_exhausted_pending`, exception handler in `_process_row` (`walkback_worker.py:1044`) |
| `last_error` | TEXT | `walkback_queue.py:117` | `_mark_failed`, exception handler, `recover_stalled_running_jobs` sentinel strings | `build_walkback_health` write-failure detection (LIKE match) |
| `enqueued_at` | INTEGER NOT NULL DEFAULT strftime | `walkback_queue.py:118` | insert only, immutable | `drain_batch` ORDER BY (FIFO tiebreak), `queue_stats` trend window, health oldest-pending |
| `started_at` | INTEGER | `walkback_queue.py:119` | set by `deep_walkback.claim_with_lease` on claim; cleared to NULL by `recover_stalled_running_jobs` on requeue | `build_walkback_health` latency + stalled detection |
| `completed_at` | INTEGER | `walkback_queue.py:120` | every `_mark_complete`/`_mark_failed`/`_mark_exhausted`/`set_intelligence_outcome`/`link_only` | `build_walkback_health` latency, `queue_stats` |
| `updated_at` | INTEGER NOT NULL DEFAULT strftime | `walkback_queue.py:121` | every write path | `build_walkback_health` stalled fallback, `write_failures` window |
| `funder_wallet` | TEXT | `walkback_queue.py:122` (dup ALTER `:83`) | `_store_funder` (`walkback_worker.py:528-539`) | `promote_recurring_funders`, `funding_boundary_backfill.py` |
| `funding_mechanism` | TEXT | `walkback_queue.py:123` (dup ALTER `:84`) | `_store_funder` | `promote_recurring_funders` (mechanism aggregation) |
| `funder_amount_sol` | REAL | `walkback_queue.py:124` (dup ALTER `:85`) | `_store_funder` | `funding_boundary_backfill.py` |
| `funder_sig` | TEXT | `walkback_queue.py:125` (dup ALTER `:86`) | `_store_funder` | `funding_boundary_backfill.py`, `_mark_complete` LINEAGE_GAP path |
| `funder_slot` | INTEGER | `walkback_queue.py:126` (dup ALTER `:87`) | `_store_funder` | not read further in-repo besides display |
| `funder_block_time` | INTEGER | `walkback_queue.py:127` (dup ALTER `:88`) | `_store_funder` | `promote_recurring_funders` (min/max), `funding_boundary_backfill.py` |
| `claimed_by` | TEXT | **not in `walkback_queue.py` at all** — added elsewhere (likely `deep_walkback.py`, not audited here) | `deep_walkback.claim_with_lease` (worker_id) | lease reclaim logic in `deep_walkback.py` |
| `claimed_at` | INTEGER | same as above | `claim_with_lease` | lease reclaim logic |
| `lease_expires_at` | INTEGER | same as above | `claim_with_lease` | `drain_batch` WHERE clause: `status='running' AND COALESCE(lease_expires_at,0) < now` (`walkback_worker.py:1163`) |
| `next_retry_at` | INTEGER | not in `walkback_queue.py` | not observed being SET in the files read (likely `deep_walkback.py`) | `drain_batch` WHERE `COALESCE(next_retry_at,0) <= now` (`walkback_worker.py:1164`) — gates backoff |
| `path_state` | TEXT | `walkback_queue.py:360-363` sets on insert (`CREATE_ANCHORED`/`WAITING_FOR_CREATE_ANCHOR`/`QUEUED`) | insert; `deep_walkback.set_path_state` during `_expand_unknown_upstream` (`walkback_worker.py:824,1028`) | not read by worker selection logic directly |
| `termination_reason_json` | TEXT | not in `walkback_queue.py` | not observed set in files read (likely `deep_walkback.py`) | not observed read |
| `create_anchor_signature` | TEXT | `walkback_queue.py:372-374` insert | insert only from `create_signature` param | `_recover_create_signature_from_db` (`walkback_worker.py:786-790`) |
| `create_anchor_slot` | INTEGER | insert | insert only | not read further here |
| `create_anchor_block_time` | INTEGER | insert | insert only | not read further here |
| `create_anchor_source` | TEXT | insert | insert only | not read further here |
| `create_anchor_audit_state` | TEXT | insert (`VALID`/`MISSING_OR_MALFORMED`) | insert only | not read further here |
| `priority` | INTEGER NOT NULL DEFAULT 0 | **added by `src/ops/watchtower_candidates.py:52-59`, not `walkback_queue.py`** | `evaluate_and_enqueue_candidate` sets to `HIGH_PRIORITY=100` when a candidate fires (`watchtower_candidates.py:170-174`) | `drain_batch` `ORDER BY COALESCE(priority,0) DESC, enqueued_at ASC` (`walkback_worker.py:1165`) |
| `priority_reason` | TEXT | `watchtower_candidates.py:52-59` | same call, set to `"QUICK_BIRTH_MIGRATION+EPHEMERAL_WSOL_CREATOR_HANDOFF"` | not read by worker logic, display only |

## Indexes (all confirmed live)
- `ix_wbq_status (status, enqueued_at)` — `walkback_queue.py:131-132`
- `ix_wbq_class (walkback_class, status)` — `walkback_queue.py:134-135`
- `ix_wbq_outcome (intelligence_outcome)` — `walkback_queue.py:137-138`
- `ix_wbq_funder (funder_wallet)` — `walkback_queue.py:140-141`
- `ix_wbq_priority (status, priority DESC, enqueued_at ASC)` — `watchtower_candidates.py:60-63`, matches exactly the ORDER BY used by `drain_batch`.

## Important correction vs. the task's assumed schema
The task's "known" column list omits `claimed_by`, `claimed_at`, `lease_expires_at`,
`next_retry_at`, `path_state`, `termination_reason_json`, `create_anchor_*` (5
columns), `priority`, and `priority_reason` — 13 columns not in the assumed list.
Several of these (`claimed_by`, `claimed_at`, `next_retry_at`,
`termination_reason_json`) are not created anywhere in `walkback_queue.py` or
`walkback_worker.py`; they must originate in `src/core/deep_walkback.py`, which
was referenced (`deep_walkback.claim_with_lease`, `deep_walkback.set_path_state`)
but not opened in this audit — flagged here as **not fully traced**, since its
`ensure_schema()` is called at `walkback_queue.py:145-146` and is presumably
where these columns are added.

---

# 5. Worker Lifecycle

## Tuning knobs (all `os.environ.get`, walkback_worker.py:63-71)
| Knob | Default | Meaning |
|---|---|---|
| `WALKBACK_BATCH_SIZE` | 8 | rows claimed per `drain_batch` call |
| `WALKBACK_INTERVAL_SEC` | 45 | `time.sleep()` between `run_loop` iterations (walkback_worker.py:1328) |
| `WALKBACK_MAX_ATTEMPTS` | 3 | attempts ceiling before a row is treated as exhausted |
| `WALKBACK_RPC_BUDGET_BATCH` | 80 | RPC credits allowed per batch before the remaining claimed rows are deferred |
| `WALKBACK_SIG_LIMIT` | 20 | unused directly in the visible pagination path (see `SIG_PAGE_LIMIT`) |
| `WALKBACK_TX_FETCH_LIMIT` | 5 | max `getTransaction` calls per hop |
| `WALKBACK_SIG_PAGE_LIMIT` | 100 | signatures per `getSignaturesForAddress` page |
| `WALKBACK_SIG_PAGE_COUNT` | 3 | max pages fetched per wallet history window |
| `WALKBACK_RPC_TIMEOUT_S` | 8 | per-RPC-call timeout |
| `WALKBACK_LEASE_SECONDS` | 300 | claim lease duration (walkback_worker.py:501) |
| `WALKBACK_WORKER_ID` | `walkback-{pid}` | identity written to `claimed_by` |
| `WALKBACK_DEEP_MAX_HOPS` | 8 | max hops in `_expand_unknown_upstream` deep walk |
| `WALKBACK_DEEP_ROW_RPC_BUDGET` | 80 | RPC budget ceiling for one row's deep-expansion phase |

## Polling
`run_loop()` (walkback_worker.py:1241-1328) is a `while True:` loop. Each
iteration: writes a heartbeat, counts pending rows
(`WHERE status='pending' AND attempts < MAX_ATTEMPTS`,
walkback_worker.py:1306-1309), calls `drain_batch()` if any exist, then
unconditionally `time.sleep(INTERVAL_SEC)` (45s default) regardless of
whether work was found or the batch was full. No backoff/speedup logic
adjusts the sleep interval based on queue depth.

## Claim (concurrency-safety)
`_mark_running()` (walkback_worker.py:498-507) calls
`deep_walkback.claim_with_lease()` (deep_walkback.py:231-240), which performs
a single atomic UPDATE:
```sql
UPDATE wt_walkback_queue SET status='running', path_state='CLAIMED',
  claimed_by=?, claimed_at=?, lease_expires_at=?, started_at=COALESCE(started_at,?),
  updated_at=?, attempts=attempts+1
WHERE mint=? AND attempts < 100
  AND (status='pending' OR (status='running' AND COALESCE(lease_expires_at,0) < ?))
  AND COALESCE(next_retry_at,0) <= ?
```
Success is `cursor.rowcount == 1`. This is a compare-and-set claim pattern:
multiple worker processes can run `drain_batch` concurrently and race to
claim the same row, but only one UPDATE affects a row (SQLite's own
row-level write serialization under WAL), so exactly one worker wins;
`drain_batch` counts the losers as `skipped_claimed`
(walkback_worker.py:1184-1186). Note the hardcoded `attempts < 100` inside
`claim_with_lease` is a second, higher ceiling independent of
`MAX_ATTEMPTS=3` used by `drain_batch`'s own SELECT — `drain_batch` never
selects a row with `attempts >= MAX_ATTEMPTS` in the first place
(walkback_worker.py:1164), so this inner `<100` guard is effectively
unreachable dead code under normal operation.

## Selection query (exact SQL)
`drain_batch()` (walkback_worker.py:1160-1167):
```sql
SELECT mint, creator, subprov, treasury, walkback_class, attempts
FROM wt_walkback_queue
WHERE (status='pending' OR (status='running' AND COALESCE(lease_expires_at,0) < ?))
  AND attempts < ? AND COALESCE(next_retry_at,0) <= ?
ORDER BY COALESCE(priority,0) DESC, enqueued_at ASC
LIMIT ?
```
This is **not** pure FIFO: it orders first by `priority DESC` (defaulting to
0), then by `enqueued_at ASC` as the tiebreak. See §6 (Performance) — in
current production data every row has `priority=0`, so in practice the
ordering degenerates to FIFO-by-enqueue-time, but the mechanism to override
that is live code, not hypothetical (see §7 Integration Recommendations).

## Processing
`_process_row()` (walkback_worker.py:859-1054) dispatches on
`walkback_class`:
- `PARTIAL_TREASURY`: single hop from `subprov` (walkback_worker.py:874-890).
- `PARTIAL_SUBPROV`: single hop from `creator` (walkback_worker.py:892-913).
- `FULL_WALKBACK`: recovers creator from DB if missing
  (`_recover_creator_from_db`), hop1 from creator anchored before the CREATE
  signature, hop2 from hop1's funder (searching the oldest edge of the
  window with `prefer_oldest=True`), and if hop2 is unknown, optionally
  `_expand_unknown_upstream()` continues up to `DEEP_MAX_HOPS=8` more hops
  (walkback_worker.py:915-1037, `_expand_unknown_upstream` at 818-854).
- Any other `walkback_class` value reaching the worker is treated as a bug
  and immediately `_mark_failed` with `"unexpected class {wclass} in worker"`
  (walkback_worker.py:1038-1039) — this should be unreachable since
  `LINK_ONLY*`/`SKIP`/`OP_GRAPH_ROLE_MISMATCH`/`SELF_ROOTED_OPERATION` never
  reach `status='pending'`.

## Completion
- Success → `_mark_complete()` (walkback_worker.py:573-630): sets
  `status='complete'`, `intelligence_outcome`, COALESCEs `subprov`/`treasury`,
  increments `rpc_used`, sets `completed_at`; also fans out to
  `materialize_outcome`, `sync_walkback_result`, and conditionally
  `_ensure_subprov_lead`/`_surface_treasury_review_lead` for `LINEAGE_GAP`.

## Failure handling
- Any exception inside `_process_row`'s try block is caught
  (walkback_worker.py:1041-1052). If `attempts >= MAX_ATTEMPTS` (checked
  against the `attempts` value read at row-claim time, already incremented
  by `claim_with_lease`), the row is `_mark_exhausted` — terminal
  `status='failed'`, `intelligence_outcome='NO_ATTRIBUTION_FOUND'`
  (walkback_worker.py:647-660). Otherwise the row is reset to
  `status='pending'` with `last_error` set, to be retried in a later batch
  (walkback_worker.py:1047-1052) — this is a same-connection UPDATE, not a
  call through `_mark_failed`.
- Explicit non-exception failure paths also exist:
  `_mark_failed()` (walkback_worker.py:633-644, used e.g. when
  `PARTIAL_TREASURY`/`PARTIAL_SUBPROV` has a NULL required field) sets
  `status='failed'` with `last_error` but does **not** set
  `intelligence_outcome`.

## Retry / backoff
`MAX_ATTEMPTS=3` (default) is the retry ceiling. There is **no explicit
exponential/linear backoff timer set on retry** in the paths read here —
`attempts` is incremented at claim time by `claim_with_lease`, and a failed
row simply goes back to `status='pending'` and is re-selected on the next
`drain_batch` call (subject to `next_retry_at`, but no code path in
`walkback_worker.py` or `walkback_queue.py` was observed setting
`next_retry_at` to a future value — it defaults to NULL/0, so
`COALESCE(next_retry_at,0) <= now` is always true). **This is flagged as
unconfirmed**: `next_retry_at` may be set elsewhere (e.g. inside
`deep_walkback.py` beyond what was read, or by another module not covered
in this audit) — no evidence of an active backoff schedule was found in the
files read.

## Dead-letter / terminal state
`status='failed'` is terminal within the normal batch-processing path — once
`attempts >= MAX_ATTEMPTS`, `_mark_exhausted` (or the direct exception-path
`_mark_exhausted` call) sets `status='failed'` with
`intelligence_outcome='NO_ATTRIBUTION_FOUND'`, and `drain_batch`'s SELECT
excludes `attempts >= MAX_ATTEMPTS` rows entirely, so they are never
reclaimed by normal operation. `finalize_exhausted_pending()`
(walkback_worker.py:663-671), run at worker startup, sweeps any row stuck
`status='pending'` with `attempts>=max_attempts` (a recovery case, e.g. from
a crash) into the same terminal `_mark_exhausted` state.

## Stalled-job recovery (separate from retry)
`recover_stalled_running_jobs()` (`src/ops/walkback_health.py:114-164`),
called at worker startup (walkback_worker.py:1266-1269) with
`stalled_after_seconds=max(INTERVAL_SEC*3, 180)`: any row `status='running'`
whose `COALESCE(started_at,updated_at,enqueued_at)` is older than the
cutoff is either requeued to `pending` (if `attempts<max_attempts`) or
force-failed with `last_error='STALLED_RUNNING_JOB_ATTEMPTS_EXHAUSTED'` (if
exhausted). This is the crash-recovery mechanism for a worker that claimed a
row (setting `lease_expires_at`) and died before completing it — note
`drain_batch`'s own SELECT also independently reclaims expired leases
(`status='running' AND lease_expires_at < now`), so there are two
overlapping mechanisms for reclaiming abandoned `running` rows: the
per-batch lease-expiry check inside `drain_batch`, and the startup-only
`recover_stalled_running_jobs` sweep.

## Duplicate-prevention (enqueue-side, not worker-side)
Covered in §3 (Entry Points) — `INSERT OR IGNORE` on `mint` PK inside
`enqueue_migration()`.

---

# 6. Performance (read-only, database/wt_ops_v2.db)

All numbers below are live query results run 2026-07-21 via
`sqlite3 database/wt_ops_v2.db`. No estimates presented as fact — anything
not directly queryable is stated as such.

## Queue length / status distribution
```sql
SELECT status, COUNT(*) FROM wt_walkback_queue GROUP BY status;
```
| status | count |
|---|---|
| COMPLETE (capitalized outlier) | 1 |
| complete | 6363 |
| failed | 1 |
| skipped | 84 |
| waiting | 304 |

Total rows: **6753**. No rows currently `pending` or `running` at query
time. Note the single `COMPLETE` (uppercase) row is inconsistent with the
`STATUSES` tuple defined in `walkback_queue.py:44`
(`("pending","running","complete","skipped","failed")`) — a data anomaly,
likely from an external/manual write (possibly the shadow-validation script
path noted in §3 §4, though that script sets `status='running'`
not `'COMPLETE'` — origin not further traced).

## walkback_class distribution
```sql
SELECT walkback_class, COUNT(*) FROM wt_walkback_queue GROUP BY walkback_class;
```
| class | count |
|---|---|
| FULL_WALKBACK | 5888 |
| LINK_ONLY | 182 |
| PARTIAL_TREASURY | 599 |
| SKIP | 84 |

No `PARTIAL_SUBPROV`, `LINK_ONLY_GRAPH`, `OP_GRAPH_ROLE_MISMATCH`, or
`SELF_ROOTED_OPERATION` rows currently present.

## intelligence_outcome distribution
```sql
SELECT COALESCE(intelligence_outcome,'NULL'), COUNT(*) FROM wt_walkback_queue GROUP BY 1;
```
| outcome | count |
|---|---|
| LINEAGE_GAP | 1769 |
| NON_WATCHTOWER | 84 |
| NO_ATTRIBUTION_FOUND | 4376 |
| NULL (unresolved / waiting) | 304 |
| WATCHTOWER_CONFIRMED | 220 |

`NO_ATTRIBUTION_FOUND` is 64.8% of all rows — the large majority of
`FULL_WALKBACK` rows terminate without finding attribution.
`WATCHTOWER_CONFIRMED` is 3.3% of rows.

## attempts distribution
```sql
SELECT attempts, COUNT(*) FROM wt_walkback_queue GROUP BY attempts;
```
| attempts | count |
|---|---|
| 0 | 570 |
| 1 | 6065 |
| 2 | 117 |
| 3 | 1 |

Only 1 row has hit `attempts=3` (the `MAX_ATTEMPTS` default) — retries are
rare; the vast majority of rows complete or terminate on their first claim
(`attempts=1`, since `claim_with_lease` increments before processing).

## priority distribution
```sql
SELECT priority, COUNT(*) FROM wt_walkback_queue GROUP BY priority;
```
| priority | count |
|---|---|
| 0 | 6753 |

**Every single row currently has `priority=0`.** The prioritization
mechanism described in §5 (Worker Lifecycle) and §4 (Schema)
(`watchtower_candidates.py`'s `HIGH_PRIORITY=100` on X63 candidate
detection) exists in code and is wired into the worker's selection query,
but has never fired on this dataset. This was traced to a root cause, not
left as a hypothesis: `wt_watchtower_candidates` (the table
`evaluate_and_enqueue_candidate` inserts into before it ever reaches the
`priority` UPDATE) has **zero rows**, despite 3,052,976 rows of matching
`funding_mechanism` evidence in `wt_candidate_websocket_watches`
(3,017,300 `WSOL_WRAP_CLOSE` + 35,676 `SEEDED_ACCOUNT_CLOSE`) and 862 rows
in `wt_wrap_close_candidates`. The function never reaches its own INSERT.

The blocker is `classify_quick_birth_migration()`
(`src/ops/operational_intelligence.py:68-108`), called at
`watchtower_candidates.py:147-151`:
```python
quick = classify_quick_birth_migration(
    primitive.get("block_time"), timing.get("created_at"), timing.get("migrated_at")
)
if not quick["is_quick_birth_migration"]:
    return None
```
`is_quick_birth_migration` is only `True` when the classifier's internal
`reason` is exactly `"OK"`, which requires all three of birth/create/
migration timestamps to be non-NULL and within threshold — in particular
`timing.get("migrated_at")` (read from `token_analysis.migrated_at`,
`watchtower_candidates.py:112`) must already be set. But
`evaluate_and_enqueue_candidate` is invoked from `walkback_queue.py`'s
`enqueue_migration()` and the live curve listener — i.e. at/near CREATE
time, before a token has migrated to PumpSwap (if it ever does; migration
can be minutes away or may never happen). At that point
`token_analysis.migrated_at` is virtually always NULL, so `reason` resolves
to `"MISSING_MIGRATION"`, which is **not** in the classifier's own
`evaluable` set (`{"OK","BIRTH_TOO_OLD","MIGRATION_TOO_SLOW"}`) and
`is_quick_birth_migration` is `False` by construction. The function returns
`None` before the INSERT, on essentially every real call. This is not a
rare edge case or a detector-conditions miss — it is the default execution
order making the gate almost structurally unsatisfiable at its current call
site. (A secondary, non-blocking issue: `primitive.get("block_time")` — the
wrap-close event time — is passed as the classifier's `creator_birth_at`
argument, which is a naming/semantic mismatch but not what causes the
zero-row outcome.)

## RPC usage
```sql
SELECT SUM(rpc_used), AVG(rpc_used), MAX(rpc_used) FROM wt_walkback_queue;
```
| SUM | AVG | MAX |
|---|---|---|
| 48604 | 7.20 | 34 |

Average of ~7.2 RPC credits consumed per row across the full table
(including zero-RPC LINK_ONLY/SKIP rows, which drag the average down).
Restricting to `FULL_WALKBACK`/`PARTIAL_TREASURY` rows would give a more
representative per-processed-job figure but was not separately queried here
— flagged as a gap rather than estimated. Given the code path
(`SIG_PAGE_COUNT=3` pages + up to `TX_FETCH_LIMIT=5` `getTransaction` calls
per hop, up to 2 hops for `FULL_WALKBACK` plus optional deep expansion to
`DEEP_MAX_HOPS=8`), the theoretical worst case per row is much higher than
the observed average, consistent with most rows resolving quickly (hop1
absent → `NO_ATTRIBUTION_FOUND` in 1-2 RPC calls) per the outcome
distribution above.

## Failure / error rate
```sql
SELECT COUNT(*) FROM wt_walkback_queue WHERE status='failed';
```
Result: **1** row currently in terminal `failed` state (0.015% of all
rows). This is consistent with the attempts distribution above — retries
and hard failures are both rare in this dataset.

## Average processing latency
```sql
SELECT AVG(completed_at-started_at) FROM wt_walkback_queue
WHERE status='complete' AND started_at IS NOT NULL AND completed_at>=started_at;
```
Result: **1.44 seconds** average (across rows where `started_at` is
non-NULL, i.e. rows that actually went through the `running` claim state
rather than completing instantly at enqueue time via the zero-RPC path,
which never sets `started_at`).

## Oldest pending row
```sql
SELECT MIN(enqueued_at) FROM wt_walkback_queue WHERE status='pending';
```
Result: **no rows** (empty result set) — there is currently no pending
backlog at all; the queue is fully drained at time of measurement.

## What could NOT be derived from current schema/data
- **Per-walkback_class average processing time** — `started_at`/`completed_at`
  exist but were not cross-tabulated by class in this pass; derivable in
  principle, not computed here.
- **RPC calls per job broken out by class** — `rpc_used` exists per-row but
  a class-conditioned average was not separately queried.
- **Historical queue-depth trend over time** — no time-series snapshot table
  exists; only current-instant counts are available. `queue_stats()`'s
  `trend_24h` (walkback_queue.py:475-482) computes a 24h enqueue-time split
  live but was not invoked as part of this audit (would require importing
  and running Python, not a plain read-only `sqlite3` query, and was
  skipped to keep this audit to declared read-only SQL per the task's
  encouraged method).
- **Wall-clock throughput (rows/hour) under sustained load** — not derivable
  from a single point-in-time snapshot without a time-series log; the
  `queue_stats()`/`build_walkback_health()` `completed_per_minute`/
  `completed_last_hour` counters exist in code but reflect only the moment
  they're queried, not historical throughput.

---

# 7. Integration Recommendations for EPHEMERAL_WSOL_CREATOR_HANDOFF Signal

This is the only forward-looking section in this audit set. Everything
below is a recommendation, not a description of current behavior.

## Important prior-art finding
`src/ops/watchtower_candidates.py` **already implements** an
`EPHEMERAL_WSOL_CREATOR_HANDOFF` candidate detector (`PRIMITIVE` constant,
`watchtower_candidates.py:17`), already wired into
`wt_walkback_queue.priority` via `HIGH_PRIORITY=100`
(`watchtower_candidates.py:19,170-174`), already called from inside
`enqueue_migration()` on both the fresh-insert and already-queued paths
(`walkback_queue.py:326-333, 382-385`). The queue's `ORDER BY
COALESCE(priority,0) DESC, enqueued_at ASC` (`walkback_worker.py:1165`)
already respects this column. **The integration point requested by this
task's context already exists in the codebase** — see §6 (Performance) for
the finding that it has not yet fired on any row in the current dataset
(all 6753 rows have `priority=0`), traced to a specific root cause:
`evaluate_and_enqueue_candidate` gates its own INSERT on
`classify_quick_birth_migration()` requiring a non-NULL `migrated_at`, but
it is called at/near CREATE time — before migration has happened or is
even knowable — so the gate fails on essentially every real invocation and
`wt_watchtower_candidates` stays empty (confirmed: 0 rows, against
3,052,976 rows of qualifying raw evidence in
`wt_candidate_websocket_watches`). This changes the framing of the
recommendations below: the existing call site (point #2) is not merely
"has not yet fired" — it is the reason nothing fires, and any fix must
address the timing gate itself, not just add volume through the same path.

If the actual need is "make this signal fire more often / earlier / on more
launches" rather than "build a new integration," the recommendations below
should be read as candidate places to strengthen or trigger the *existing*
mechanism rather than as a proposal for a parallel new one.

## Candidate insertion points, evaluated

1. **Before enqueue** (i.e. a pre-filter that changes `walkback_class` or
   skips enqueue entirely) — not recommended as a new addition. The
   classification step (`classify_creator`, `walkback_queue.py:187-302`) is
   already zero-RPC and purely DB-lookup driven; injecting a heavier
   detector here would slow every migration event, including the ~5.4% that
   are already `LINK_ONLY`/`SKIP` and need no walkback at all.

2. **During enqueue** (current implementation's actual location) — this is
   where `evaluate_and_enqueue_candidate` already runs. It is the natural
   point because it has access to the freshly-classified row and can gate
   the priority UPDATE on `status IN ('pending','waiting')`
   (`watchtower_candidates.py:171-172`), i.e. only affects rows that will
   actually reach the worker queue. This remains the best-fit insertion
   point for any refinement of the signal itself (e.g. adjusting what
   counts as a "quick birth → migration" window, or adding new handoff
   variants beyond `WSOL_WRAP_CLOSE`/`SEEDED_ACCOUNT_CLOSE`).

3. **At queue-selection time** (inside `drain_batch`'s SELECT) — not
   recommended for this signal specifically. The `ORDER BY priority DESC`
   clause already reads whatever value was set at enqueue time; recomputing
   the signal at selection time would mean re-deriving handoff evidence on
   every poll cycle (45s default) for every pending row, which is wasted
   work compared to computing it once at enqueue.

4. **Worker pre-processing** (inside `_process_row` before dispatch) — not
   a good fit; by this point the row has already been claimed and its
   position in the batch has already been decided, so any priority signal
   computed here can no longer influence ordering for *this* batch. Could
   only affect a *future* re-queue, which is a strictly worse trigger point
   than #2.

5. **Post-processing** (after `_mark_complete`) — not applicable for
   *prioritizing* this row (it's already done), but this is where
   `sync_walkback_result()` and `materialize_outcome()` already run, and
   where a completed walkback's evidence (e.g. a confirmed `WSOL_WRAP_CLOSE`
   discovered only during the RPC walk itself, not known at enqueue time)
   could seed **retroactive** priority-setting for *other still-pending*
   rows sharing the same wallet/creator pattern — this is not currently
   done anywhere observed in this audit.

## Recommended single best point
**#2 (during enqueue), reusing the existing `evaluate_and_enqueue_candidate`
mechanism, but only after fixing the `classify_quick_birth_migration`
timing gate at its call site.** As currently written, this integration
point cannot fire at CREATE time because it requires `migrated_at`, a
fact that by definition does not exist yet. Two non-mutually-exclusive
fixes, both scoped to `watchtower_candidates.py` and requiring no queue/
schema redesign:
- Treat `MISSING_MIGRATION` as evaluable at enqueue time (the primitive
  signal — the wrap-close handoff itself — is independent of whether the
  token later migrates; migration timing is presently used as a
  "quick pump" confirmation signal, not something the handoff detector
  should need). This would let the INSERT proceed on birth/create alone.
- Or, add a second, later call to `evaluate_and_enqueue_candidate` (or a
  dedicated re-evaluation) triggered from wherever `token_analysis.
  migrated_at` actually gets set (the migration listener/reconciler), so
  the full three-timestamp classification still applies but at a point
  where all three values can genuinely be non-NULL.

Absent one of these, point #2 remains the correct architectural location
once the gate is fixed —
- It is already the integration point in production code.
- It only touches rows before they reach `status='pending'`/`'waiting'`,
  so it can never race the worker's claim (`claim_with_lease`) or need to
  coordinate with an in-flight `running` row.
- It composes with the existing `ix_wbq_priority` index
  (`status, priority DESC, enqueued_at ASC`), so no schema or index change
  is needed to make a priority boost immediately effective in the next
  `drain_batch` poll.

## Can priority be extended without a full redesign?
**Yes.** The column, index, and ORDER BY clause are all already generic
(`priority INTEGER`, not a fixed enum of values) — a graduated priority
scheme (e.g. multiple tiers instead of the current binary
`0`/`HIGH_PRIORITY=100`) requires no schema change, only:
- Additional constants/logic inside `evaluate_and_enqueue_candidate` (or a
  sibling function called from the same enqueue-time hook) to compute a
  tier instead of a flat `100`.
- No change to `drain_batch`'s SELECT/ORDER BY at all — it already sorts by
  `priority DESC` generically.

The one architectural constraint worth flagging: `priority` is currently
only ever set (never read back and combined with other factors) by a single
writer (`watchtower_candidates.py:170-174`), and the UPDATE is
unconditional (`SET priority=?`, not `SET priority=MAX(priority,?)`)
(`watchtower_candidates.py:171`) — a second signal source writing to the
same column would silently overwrite this one's value rather than compose
with it, unless changed to a MAX/greatest-of pattern. Any second
prioritization source should be aware of this before writing to the same
column.
